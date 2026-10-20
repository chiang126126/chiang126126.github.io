// Connecteur de démonstration : génère un état de stock déterministe et plausible
// pour TOUS les magasins/produits suivis, sans aucune source externe.
// Permet de lancer toute la chaîne (API → diff → alertes) avant de brancher le réel.
import { BaseConnector } from './base.js';
import type { CollectContext, InventoryRecord, Status, Reliability, Source, Fulfillment } from '../types.js';

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(seed: number) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SCARCITY: Record<string, number> = {
  'midea-portasplit-12000': 0.68,
  'comfee-9000': 0.5,
  'midea-portasplit-cool': 0.62,
  'support-universel': 0.2,
};

export class DemoConnector extends BaseConnector {
  retailer = '*';
  source: Source = 'scrape';

  async fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]> {
    const now = Date.now();
    const out: InventoryRecord[] = [];
    // Le bucket horaire fait "bouger" le stock à chaque heure → simule des réassorts.
    const bucket = Math.floor(now / 3600000);
    for (const store of ctx.stores) {
      for (const product of ctx.products) {
        const r = rng(hash(`${store.id}|${product.id}|${bucket}`));
        const scarcity = (SCARCITY[product.id] ?? 0.6) + (store.online ? -0.18 : 0);
        const roll = r();
        let status: Status, qty: number | null;
        if (roll < scarcity) { status = 'out_of_stock'; qty = 0; }
        else if (roll < scarcity + 0.12) { status = 'low_stock'; qty = 1 + Math.floor(r() * 3); }
        else { status = 'in_stock'; qty = 3 + Math.floor(r() * 14); }

        const price = 599 + Math.round(r() * 280) - 0.01;
        const ageMin = Math.floor(r() * (store.online ? 12 : 180)) + 1;
        const reliability: Reliability = ageMin <= 15 ? 'high' : ageMin <= 60 ? 'medium' : 'low';
        const fulfillment: Fulfillment[] = store.online ? ['delivery']
          : r() < 0.85 ? ['pickup', 'delivery'] : ['pickup'];

        out.push({
          storeId: store.id,
          productId: product.id,
          status,
          qty,
          price,
          fulfillment,
          updatedAt: now - ageMin * 60000,
          reliability,
          source: 'scrape',
        });
      }
    }
    return out;
  }
}
