// Connecteur RÉEL — flux produits Awin (programme d'affiliation).
// Source légale et structurée : on est éditeur Awin approuvé par l'annonceur.
// ⚠️ Un flux d'affiliation donne la disponibilité/prix EN LIGNE de l'enseigne,
//    rafraîchie ~quotidiennement — PAS le stock temps réel par magasin physique
//    (qui nécessite une API magasin officielle). On rattache donc les dispos au
//    magasin `online = true` de l'enseigne. Fiabilité = 'medium'.
//
// Schéma Awin (Legacy) — alias d'en-têtes gérés ci-dessous :
//   deep_link(purl) · product_name(name) · search_price/price · currency ·
//   in_stock(instock) · stock_quantity(stockquant) · ean · mpn · merchant_product_id(pid)
// Doc : https://help.awin.com/docs/hosting-feeds ·
//       https://help.awin.com/docs/product-feed-list-download
import gunzip from 'node:zlib';
import { BaseConnector } from './base.js';
import type { CollectContext, InventoryRecord, Source, Status, Fulfillment } from '../types.js';
import { AWIN, PRODUCT_MATCHERS } from './config.js';

// ----- parsing CSV tolérant (champs entre guillemets, "" échappé, CRLF) -----
export function parseCsv(text: string, delimiter = ','): string[][] {
  const rows: string[][] = [];
  let field = '', row: string[] = [], inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === delimiter) { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); field = ''; row = []; }
    else if (c === '\r') { /* ignore */ }
    else field += c;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.length > 1 || (r.length === 1 && r[0] !== ''));
}

const ALIASES: Record<string, string[]> = {
  deepLink: ['aw_deep_link', 'deep_link', 'purl', 'product_url'],
  name: ['product_name', 'name', 'title'],
  price: ['search_price', 'price', 'store_price', 'display_price'],
  currency: ['currency', 'currency_code'],
  ean: ['ean', 'gtin', 'gtin13'],
  mpn: ['mpn', 'model_number', 'modelno'],
  inStock: ['in_stock', 'instock', 'availability', 'stock_status'],
  stockQty: ['stock_quantity', 'stockquant', 'quantity'],
  merchantId: ['merchant_product_id', 'product_id', 'pid', 'aw_product_id', 'sku'],
};

export function buildHeaderIndex(header: string[]): Record<string, number> {
  const lower = header.map((h) => h.trim().toLowerCase());
  const idx: Record<string, number> = {};
  for (const [key, names] of Object.entries(ALIASES)) {
    for (const n of names) {
      const at = lower.indexOf(n);
      if (at >= 0) { idx[key] = at; break; }
    }
  }
  return idx;
}

function get(row: string[], idx: Record<string, number>, key: string): string {
  const at = idx[key];
  return at != null ? (row[at] ?? '').trim() : '';
}

function parsePrice(raw: string): number | null {
  if (!raw) return null;
  const n = parseFloat(raw.replace(/[^0-9.,]/g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

const IN_STOCK_TRUTHY = new Set(['1', 'yes', 'y', 'true', 'in stock', 'in-stock', 'instock', 'available']);

export function rowToStatus(inStockRaw: string, qtyRaw: string): { status: Status; qty: number | null } {
  const v = inStockRaw.trim().toLowerCase();
  const qty = qtyRaw && /^\d+$/.test(qtyRaw.trim()) ? parseInt(qtyRaw, 10) : null;
  const inStock = IN_STOCK_TRUTHY.has(v) || (qty != null && qty > 0 && v === '');
  if (!inStock) return { status: 'out_of_stock', qty: 0 };
  if (qty != null && qty <= 3) return { status: 'low_stock', qty };
  return { status: 'in_stock', qty };
}

/** Quel produit suivi correspond à cette ligne ? (EAN prioritaire, sinon mots-clés.) */
export function matchProduct(name: string, ean: string): string | null {
  const lname = name.toLowerCase();
  for (const [productId, m] of Object.entries(PRODUCT_MATCHERS)) {
    if (ean && m.eans.includes(ean.trim())) return productId;
  }
  for (const [productId, m] of Object.entries(PRODUCT_MATCHERS)) {
    if (m.keywords.length && m.keywords.every((k) => lname.includes(k.toLowerCase()))) return productId;
  }
  return null;
}

/** Transforme le texte d'un flux Awin en InventoryRecord (pure, testable). */
export function mapFeed(csvText: string, storeId: string, now = Date.now()): InventoryRecord[] {
  const rows = parseCsv(csvText);
  if (!rows.length) return [];
  const idx = buildHeaderIndex(rows[0]);
  const out: InventoryRecord[] = [];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    const name = get(r, idx, 'name');
    const ean = get(r, idx, 'ean');
    const productId = matchProduct(name, ean);
    if (!productId) continue;
    const { status, qty } = rowToStatus(get(r, idx, 'inStock'), get(r, idx, 'stockQty'));
    const price = parsePrice(get(r, idx, 'price'));
    const fulfillment: Fulfillment[] = ['delivery'];
    out.push({
      storeId, productId, status, qty, price, fulfillment,
      updatedAt: now, reliability: 'medium', source: 'affiliate',
    });
  }
  return out;
}

export class AwinFeedConnector extends BaseConnector {
  retailer = '*'; // gère plusieurs annonceurs configurés
  source: Source = 'affiliate';

  private feedUrl(feedId: string): string {
    // Si feedId est déjà une URL complète (générée via Create-a-Feed), on l'utilise telle quelle.
    if (feedId.startsWith('http')) return feedId;
    const cols = 'aw_deep_link,product_name,merchant_product_id,search_price,currency,ean,mpn,in_stock,stock_quantity';
    return `https://productdata.awin.com/datafeed/download/apikey/${AWIN.apiKey}` +
      `/language/${AWIN.language}/fid/${feedId}/columns/${cols}/format/csv/delimiter/%2C/compression/gzip/`;
  }

  private async download(url: string, ctx: CollectContext): Promise<string> {
    const res = await this.politeFetch(url, ctx, {}, 30000);
    if (!res.ok) throw new Error(`flux Awin HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    // Détection gzip (magic 0x1f 0x8b) → décompression.
    if (buf.length > 2 && buf[0] === 0x1f && buf[1] === 0x8b) {
      return gunzip.gunzipSync(buf).toString('utf8');
    }
    return buf.toString('utf8');
  }

  async fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]> {
    if (!AWIN.apiKey || !AWIN.feeds.length) {
      console.warn('[awin] non configuré (AWIN_API_KEY / AWIN_FEED_* manquants) — aucun enregistrement.');
      return [];
    }
    const all: InventoryRecord[] = [];
    for (const feed of AWIN.feeds) {
      const online = ctx.stores.find((s) => s.retailerId === feed.retailerId && s.online);
      if (!online) {
        console.warn(`[awin] pas de magasin online pour ${feed.retailerId} — flux ignoré.`);
        continue;
      }
      try {
        const csv = await this.download(this.feedUrl(feed.feedId!), ctx);
        const recs = mapFeed(csv, online.id);
        console.log(`[awin] ${feed.retailerId}: ${recs.length} produit(s) suivi(s) trouvé(s)`);
        all.push(...recs);
      } catch (err) {
        console.error(`[awin] échec flux ${feed.retailerId}:`, (err as Error).message);
      }
    }
    return all;
  }
}
