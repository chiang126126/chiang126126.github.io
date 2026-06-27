// GET /api/inventory — lecture publique consommée par le front (format InventoryRecord).
import { Router } from 'express';
import { query, dbReady } from '../db.js';
import { haversineKm, cpToCoords } from '../geo.js';
import type { InventoryRecord } from '../types.js';

export const inventoryRouter = Router();

inventoryRouter.get('/inventory', async (req, res) => {
  const product = String(req.query.product ?? '');
  const cp = String(req.query.cp ?? '');
  const radius = Number(req.query.radius ?? 9999);
  const maxPrice = req.query.maxPrice ? Number(req.query.maxPrice) : null;
  const fulfillment = req.query.fulfillment ? String(req.query.fulfillment) : null; // pickup|delivery
  const retailers = req.query.retailers ? String(req.query.retailers).split(',') : null;
  const inStock = String(req.query.inStock ?? '') === 'true';

  if (!(await dbReady())) {
    return res.status(503).json({ error: 'inventory_unavailable', records: [] });
  }

  const rows = await query<any>(
    `select i.store_id, i.product_id, i.status, i.qty, i.price, i.fulfillment,
            i.reliability, i.source, extract(epoch from i.updated_at)*1000 as updated_at,
            s.retailer_id, s.lat, s.lon, s.online
       from inventory i join stores s on s.id = i.store_id
      where i.product_id = $1 and s.active = true`,
    [product]
  );

  const origin = cpToCoords(cp);
  const records: (InventoryRecord & { dist: number | null; retailerId: string })[] = [];
  for (const r of rows) {
    if (retailers && !retailers.includes(r.retailer_id)) continue;
    if (inStock && r.status === 'out_of_stock') continue;
    if (maxPrice && r.price && Number(r.price) > maxPrice) continue;
    if (fulfillment && !(r.fulfillment ?? []).includes(fulfillment)) continue;
    const dist = origin && r.lat != null && !r.online ? haversineKm(origin[0], origin[1], r.lat, r.lon) : null;
    if (origin && !r.online && dist != null && dist > radius) continue;
    records.push({
      storeId: r.store_id, productId: r.product_id, status: r.status,
      qty: r.qty, price: r.price != null ? Number(r.price) : null,
      fulfillment: r.fulfillment ?? [], reliability: r.reliability, source: r.source,
      updatedAt: Number(r.updated_at), dist, retailerId: r.retailer_id,
    });
  }

  res.set('Cache-Control', 'public, max-age=30'); // CDN court : fraîcheur vs charge
  res.json({ records, count: records.length });
});
