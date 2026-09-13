// Moteur d'alertes : à partir des réassorts détectés, prévient les abonnés éligibles.
import { query } from '../db.js';
import { config } from '../config.js';
import { haversineKm, cpToCoords } from '../geo.js';
import { sendRestockEmail, sendRestockSms } from '../notifications/brevo.js';
import type { RestockEvent } from '../types.js';

interface AlertRow {
  id: string; email: string; phone: string | null;
  product_ids: string[]; cp: string; radius_km: number;
  channels: string[]; last_notified_at: string | null;
  unsubscribe_token: string;
}

export async function processRestocks(events: RestockEvent[]): Promise<void> {
  for (const ev of events) {
    const store = (await query<any>(
      `select s.id, s.name, s.city, s.cp, s.lat, s.lon, s.online, r.name as retailer
         from stores s join retailers r on r.id = s.retailer_id where s.id = $1`,
      [ev.storeId]
    ))[0];
    if (!store) continue;

    // Alertes actives, avec Pass valide, ciblant ce produit.
    const alerts = await query<AlertRow>(
      `select a.id, a.email, a.phone, a.product_ids, a.cp, a.radius_km,
              a.channels, a.last_notified_at, a.unsubscribe_token
         from alerts a
         join passes p on p.id = a.pass_id
        where a.active = true
          and p.status = 'active' and p.valid_until > now()
          and $1 = any(a.product_ids)`,
      [ev.productId]
    );

    for (const a of alerts) {
      // Filtre distance (les magasins en ligne passent toujours).
      if (!store.online) {
        const origin = cpToCoords(a.cp);
        if (origin && store.lat != null && store.lon != null) {
          const d = haversineKm(origin[0], origin[1], store.lat, store.lon);
          if (d > a.radius_km) continue;
        }
      }

      // Anti-doublon (même alerte/magasin/produit récemment notifié).
      const dupe = (await query<{ n: number }>(
        `select count(*)::int as n from notifications
          where alert_id = $1 and store_id = $2 and product_id = $3
            and sent_at > now() - ($4 || ' minutes')::interval`,
        [a.id, ev.storeId, ev.productId, config.collector.notifyDedupeMinutes]
      ))[0];
      if (dupe && dupe.n > 0) continue;

      const unsubUrl = `${config.publicBaseUrl}/api/unsubscribe?token=${a.unsubscribe_token}`;
      const payload = {
        retailer: store.retailer as string,
        storeName: store.name as string,
        city: (store.city as string) ?? '',
        productId: ev.productId,
        price: ev.price,
        unsubUrl,
      };

      if (a.channels.includes('email')) {
        const id = await sendRestockEmail(a.email, payload);
        await logNotif(a.id, ev, 'email', id);
      }
      if (a.channels.includes('sms') && a.phone) {
        const id = await sendRestockSms(a.phone, payload);
        await logNotif(a.id, ev, 'sms', id);
      }
      await query(`update alerts set last_notified_at = now() where id = $1`, [a.id]);
    }
  }
}

async function logNotif(alertId: string, ev: RestockEvent, channel: string, providerId: string | null) {
  await query(
    `insert into notifications (alert_id, store_id, product_id, channel, provider_id, status)
     values ($1,$2,$3,$4,$5,$6)`,
    [alertId, ev.storeId, ev.productId, channel, providerId, providerId ? 'sent' : 'failed']
  );
}
