// Notifications via Brevo (e-mail transactionnel + SMS, fournisseur UE).
// E-mail : en-tête List-Unsubscribe + lien 1-clic (RFC 8058). SMS : mention STOP.
import { config } from '../config.js';

export interface RestockPayload {
  retailer: string;
  storeName: string;
  city: string;
  productId: string;
  price: number | null;
  unsubUrl: string;
}

function priceStr(p: number | null): string {
  return p != null ? `${p.toFixed(2).replace('.', ',')} €` : 'prix à vérifier';
}

export async function sendRestockEmail(to: string, p: RestockPayload): Promise<string | null> {
  if (!config.brevo.apiKey) {
    console.log(`[brevo:email DRY] ${to} ← ${p.storeName} (${priceStr(p.price)})`);
    return 'dry-run';
  }
  const html = `
    <div style="font-family:Inter,Arial,sans-serif;max-width:520px">
      <h2 style="color:#0ea5b7">❄️ De nouveau en stock !</h2>
      <p><b>${p.retailer} — ${p.storeName}</b>${p.city ? ` (${p.city})` : ''} vient d'avoir du stock.</p>
      <p>Prix indicatif : <b>${priceStr(p.price)}</b></p>
      <p style="color:#64748b;font-size:13px">Le stock peut repartir vite — vérifiez sur le site du marchand avant de vous déplacer.</p>
      <hr style="border:none;border-top:1px solid #e6ebf3">
      <p style="font-size:12px;color:#94a3b8">
        Vous recevez cet e-mail car vous avez activé une alerte DispoClim.
        <a href="${p.unsubUrl}">Se désinscrire en un clic</a>.
      </p>
    </div>`;

  const res = await fetch('https://api.brevo.com/v3/smtp/email', {
    method: 'POST',
    headers: { 'api-key': config.brevo.apiKey, 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify({
      sender: { email: config.brevo.senderEmail, name: config.brevo.senderName },
      to: [{ email: to }],
      subject: `❄️ En stock : ${p.retailer} ${p.city || ''}`.trim(),
      htmlContent: html,
      // Désinscription 1-clic (RFC 8058) — délivrabilité + conformité.
      headers: {
        'List-Unsubscribe': `<${p.unsubUrl}>`,
        'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
      },
    }),
  });
  if (!res.ok) { console.error('[brevo:email] échec', res.status, await res.text()); return null; }
  const data = (await res.json()) as { messageId?: string };
  return data.messageId ?? 'sent';
}

export async function sendRestockSms(to: string, p: RestockPayload): Promise<string | null> {
  if (!config.brevo.apiKey) {
    console.log(`[brevo:sms DRY] ${to} ← ${p.storeName}`);
    return 'dry-run';
  }
  const content =
    `DispoClim: en stock chez ${p.retailer} ${p.city} (${priceStr(p.price)}). ` +
    `Verifiez avant de vous deplacer. STOP au xxxxx pour ne plus recevoir.`;
  const res = await fetch('https://api.brevo.com/v3/transactionalSMS/sms', {
    method: 'POST',
    headers: { 'api-key': config.brevo.apiKey, 'content-type': 'application/json', accept: 'application/json' },
    body: JSON.stringify({ sender: config.brevo.smsSender, recipient: to, content, type: 'transactional' }),
  });
  if (!res.ok) { console.error('[brevo:sms] échec', res.status, await res.text()); return null; }
  const data = (await res.json()) as { reference?: string };
  return data.reference ?? 'sent';
}
