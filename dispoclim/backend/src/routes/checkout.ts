// POST /api/checkout — crée la session Stripe (paiement unique) pour activer un Pass.
import { Router } from 'express';
import { one } from '../db.js';
import { config } from '../config.js';
import { createCheckoutSession } from '../payments/stripe.js';

export const checkoutRouter = Router();

checkoutRouter.post('/checkout', async (req, res) => {
  const b = req.body ?? {};
  const alertId = String(b.alertId ?? '');
  const days = ([30, 60, 90].includes(Number(b.days)) ? Number(b.days) : 30) as 30 | 60 | 90;

  const alert = await one<{ email: string }>(`select email from alerts where id = $1`, [alertId]);
  if (!alert) return res.status(404).json({ error: 'alert_not_found' });

  try {
    const { url } = await createCheckoutSession({
      email: alert.email,
      days,
      alertId,
      successUrl: `${config.frontOrigin}/dispoclim/?paid=1`,
      cancelUrl: `${config.frontOrigin}/dispoclim/alerte.html?canceled=1`,
    });
    res.json({ url });
  } catch (err) {
    console.error('[checkout]', (err as Error).message);
    res.status(500).json({ error: 'checkout_failed' });
  }
});
