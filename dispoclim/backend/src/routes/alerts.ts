// POST /api/alerts        — crée une alerte 'pending' + trace le consentement
// GET  /api/unsubscribe    — désinscription 1 clic (RFC 8058 : GET et POST)
import { Router } from 'express';
import { query, one } from '../db.js';

export const alertsRouter = Router();

alertsRouter.post('/alerts', async (req, res) => {
  const b = req.body ?? {};
  const email = String(b.email ?? '').trim().toLowerCase();
  if (!email || !email.includes('@')) return res.status(400).json({ error: 'email_invalid' });
  if (!b.consent) return res.status(400).json({ error: 'consent_required' });

  const productIds: string[] = Array.isArray(b.productIds) ? b.productIds : [b.productId].filter(Boolean);
  if (!productIds.length) return res.status(400).json({ error: 'product_required' });

  const channels: string[] = Array.isArray(b.channels) && b.channels.length ? b.channels : ['email'];
  const ip = (req.headers['x-forwarded-for'] as string)?.split(',')[0]?.trim() ?? req.socket.remoteAddress ?? null;

  const alert = await one<{ id: string }>(
    `insert into alerts (email, phone, product_ids, cp, radius_km, max_price, fulfillment,
                         channels, active, consent_at, consent_ip)
     values ($1,$2,$3,$4,$5,$6,$7,$8,false, now(), $9)
     returning id`,
    [email, b.phone ?? null, productIds, String(b.cp ?? ''), Number(b.radius ?? 50),
     b.maxPrice ?? null, b.fulfillment ?? null, channels, ip]
  );

  // Trace RGPD / L34-5 du consentement marketing.
  await query(
    `insert into consents (email, kind, text_shown, ip) values ($1,'marketing_email',$2,$3)`,
    [email, String(b.consentText ?? 'Consentement alertes DispoClim'), ip]
  );

  // L'alerte reste inactive jusqu'au paiement du Pass (cf. /api/checkout).
  res.json({ alertId: alert!.id, status: 'pending_payment' });
});

async function doUnsubscribe(token: string): Promise<boolean> {
  const rows = await query(`update alerts set active = false where unsubscribe_token = $1 returning id`, [token]);
  return rows.length > 0;
}

alertsRouter.get('/unsubscribe', async (req, res) => {
  const ok = await doUnsubscribe(String(req.query.token ?? ''));
  res.status(ok ? 200 : 404).send(
    ok ? 'Vous êtes désinscrit. Vous ne recevrez plus d’alertes.' : 'Lien invalide.'
  );
});
// RFC 8058 : les clients mail font un POST pour le 1-clic.
alertsRouter.post('/unsubscribe', async (req, res) => {
  const token = String(req.query.token ?? req.body?.token ?? '');
  const ok = await doUnsubscribe(token);
  res.sendStatus(ok ? 200 : 404);
});
