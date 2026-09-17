// Collector : exécute les connecteurs actifs, met à jour l'inventaire, calcule le
// diff (réassorts) et déclenche le moteur d'alertes. Lancer via cron (palier hot/warm/cold).
import { config } from '../config.js';
import { query, dbReady } from '../db.js';
import { getConnectors } from '../connectors/index.js';
import { processRestocks } from '../alerts/engine.js';
import type { Store, Product, InventoryRecord, RestockEvent, Status } from '../types.js';

async function loadStores(tier?: string): Promise<Store[]> {
  const rows = await query<any>(
    `select id, retailer_id, name, city, cp, lat, lon, online, poll_tier
       from stores where active = true ${tier ? 'and poll_tier = $1' : ''}`,
    tier ? [tier] : []
  );
  return rows.map((r) => ({
    id: r.id, retailerId: r.retailer_id, name: r.name, city: r.city, cp: r.cp,
    lat: r.lat, lon: r.lon, online: r.online, pollTier: r.poll_tier,
  }));
}

async function loadProducts(): Promise<Product[]> {
  const rows = await query<any>(`select id, brand, name from products where active = true`);
  return rows.map((r) => ({ id: r.id, brand: r.brand, name: r.name }));
}

/** Upsert d'un enregistrement ; renvoie un RestockEvent si transition vers le stock. */
async function upsert(rec: InventoryRecord): Promise<RestockEvent | null> {
  const prev = await query<{ status: Status }>(
    `select status from inventory where store_id = $1 and product_id = $2`,
    [rec.storeId, rec.productId]
  );
  const prevStatus = prev[0]?.status ?? null;

  await query(
    `insert into inventory
       (store_id, product_id, status, prev_status, qty, price, fulfillment, reliability, source, updated_at)
     values ($1,$2,$3,$4,$5,$6,$7,$8,$9, to_timestamp($10/1000.0))
     on conflict (store_id, product_id) do update set
       prev_status = inventory.status,
       status = excluded.status, qty = excluded.qty, price = excluded.price,
       fulfillment = excluded.fulfillment, reliability = excluded.reliability,
       source = excluded.source, updated_at = excluded.updated_at`,
    [rec.storeId, rec.productId, rec.status, prevStatus, rec.qty, rec.price,
     rec.fulfillment, rec.reliability, rec.source, rec.updatedAt]
  );

  await query(
    `insert into inventory_history (store_id, product_id, status, price, source)
     values ($1,$2,$3,$4,$5)`,
    [rec.storeId, rec.productId, rec.status, rec.price, rec.source]
  );

  const becameAvailable =
    prevStatus === 'out_of_stock' && (rec.status === 'in_stock' || rec.status === 'low_stock');
  if (becameAvailable) {
    return { storeId: rec.storeId, productId: rec.productId, from: 'out_of_stock', to: rec.status, price: rec.price, at: rec.updatedAt };
  }
  return null;
}

export async function runCollector(tier?: string): Promise<{ records: number; restocks: number }> {
  if (!(await dbReady())) {
    console.warn('[collector] base indisponible — abandon (configurer DATABASE_URL).');
    return { records: 0, restocks: 0 };
  }
  const [stores, products] = await Promise.all([loadStores(tier), loadProducts()]);
  const connectors = getConnectors(config.collector.enabled);
  const ctx = { products, stores, userAgent: config.collector.userAgent };

  let count = 0;
  const restocks: RestockEvent[] = [];
  for (const c of connectors) {
    const scoped = { ...ctx, stores: c.retailer === '*' ? stores : stores.filter((s) => s.retailerId === c.retailer) };
    let recs: InventoryRecord[] = [];
    try {
      recs = await c.fetchRecords(scoped);
    } catch (err) {
      console.error(`[collector] connecteur ${c.retailer} en échec:`, (err as Error).message);
      continue;
    }
    for (const rec of recs) {
      const ev = await upsert(rec);
      count++;
      if (ev) restocks.push(ev);
    }
  }

  if (restocks.length) await processRestocks(restocks);
  console.log(`[collector] tier=${tier ?? 'all'} records=${count} restocks=${restocks.length}`);
  return { records: count, restocks: restocks.length };
}

// Exécution directe : `npm run collector -- [tier]`
const invokedDirectly = process.argv[1]?.endsWith('run.ts') || process.argv[1]?.endsWith('run.js');
if (invokedDirectly) {
  runCollector(process.argv[2]).then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); });
}
