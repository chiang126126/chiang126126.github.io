// Paiement du Pass via Stripe Checkout — mode 'payment' (paiement UNIQUE).
// JAMAIS 'subscription' : le service est explicitement « sans reconduction ».
import Stripe from 'stripe';
import { config } from '../config.js';

export const stripe = config.stripe.secretKey
  ? new Stripe(config.stripe.secretKey, { apiVersion: '2024-06-20' as any })
  : null;

export interface CheckoutInput {
  email: string;
  days: 30 | 60 | 90;
  alertId: string; // alerte 'pending' à activer après paiement
  successUrl: string;
  cancelUrl: string;
}

export async function createCheckoutSession(input: CheckoutInput): Promise<{ url: string }> {
  if (!stripe) throw new Error('Stripe non configuré (STRIPE_SECRET_KEY manquant)');
  const session = await stripe.checkout.sessions.create({
    mode: 'payment', // ← paiement unique, pas d'abonnement
    customer_email: input.email,
    line_items: [{
      quantity: 1,
      price_data: {
        currency: 'eur',
        unit_amount: config.stripe.passPriceCents,
        product_data: {
          name: `Pass de surveillance DispoClim — ${input.days} jours`,
          description: 'Surveillance de stock + alertes e-mail/SMS. Paiement unique, sans reconduction.',
        },
      },
    }],
    metadata: { alertId: input.alertId, days: String(input.days), email: input.email },
    success_url: input.successUrl,
    cancel_url: input.cancelUrl,
  });
  if (!session.url) throw new Error('Stripe : URL de session absente');
  return { url: session.url };
}

/** Vérifie la signature du webhook et renvoie l'événement typé. */
export function constructWebhookEvent(rawBody: Buffer, signature: string): Stripe.Event {
  if (!stripe) throw new Error('Stripe non configuré');
  return stripe.webhooks.constructEvent(rawBody, signature, config.stripe.webhookSecret);
}
