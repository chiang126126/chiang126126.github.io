// POST /api/webhooks/stripe — active le Pass après paiement confirmé.
// ⚠️ Cette route doit recevoir le corps BRUT (raw) pour vérifier la signature.
import { Router, raw } from 'express';
import { query, one } from '../db.js';
import { constructWebhookEvent } from '../payments/stripe.js';

export const webhooksRouter = Router();

webhooksRouter.post('/webhooks/stripe', raw({ type: 'application/json' }), async (req, res) => {
  const sig = req.headers['stripe-signature'] as string;
  let event;
  try {
    event = constructWebhookEvent(req.body as Buffer, sig);
  } catch (err) {
    console.error('[webhook] signature invalide:', (err as Error).message);
    return res.status(400).send('invalid signature');
  }

  if (event.type === 'checkout.session.completed') {
    const s = event.data.object as any;
    const email = (s.customer_email ?? s.metadata?.email ?? '').toLowerCase();
    const alertId = s.metadata?.alertId as string | undefined;
    const days = Number(s.metadata?.days ?? 30);

    // Crée le Pass (paiement unique — pas de reconduction).
    const pass = await one<{ id: string }>(
      `insert into passes (email, stripe_session_id, stripe_payment_id, amount_cents, days, valid_until)
       values ($1,$2,$3,$4,$5, now() + ($5 || ' days')::interval)
       on conflict (stripe_session_id) do nothing
       returning id`,
      [email, s.id, s.payment_intent ?? null, s.amount_total ?? 199, days]
    );

    // Active l'alerte liée + trace la renonciation au droit de rétractation.
    if (pass && alertId) {
      await query(`update alerts set pass_id = $1, active = true where id = $2`, [pass.id, alertId]);
      await query(
        `insert into consents (email, kind, text_shown)
         values ($1,'withdrawal_waiver','Exécution immédiate du service numérique + renonciation au droit de rétractation (art. L221-28)')`,
        [email]
      );
    }
    console.log(`[webhook] Pass activé pour ${email} (${days} j)`);
  }

  res.json({ received: true });
});
