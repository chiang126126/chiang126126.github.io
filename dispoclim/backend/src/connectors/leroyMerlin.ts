// Exemple de connecteur enseigne — TEMPLATE.
// ⚠️ Squelette volontairement non opérationnel : aucune URL réelle n'est appelée.
// Avant activation : privilégier un ACCÈS OFFICIEL (API enseigne ou flux affilié),
// respecter robots.txt + CGU, limiter la cadence, et ne PAS réutiliser logos/images.
import { BaseConnector } from './base.js';
import type { CollectContext, InventoryRecord, Source } from '../types.js';
import { isAllowedByRobots } from '../collector/robots.js';
import { RateLimiter } from '../collector/ratelimit.js';

export class LeroyMerlinConnector extends BaseConnector {
  retailer = 'leroy-merlin';
  // Mettre 'api' ou 'affiliate' dès qu'un accès officiel est en place.
  source: Source = 'scrape';

  // ≤ 1 requête/seconde par défaut — ajuster selon l'accord/robots.
  private limiter = new RateLimiter({ perSecond: 1 });

  async fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]> {
    const records: InventoryRecord[] = [];
    const stores = ctx.stores.filter((s) => s.retailerId === this.retailer);

    for (const store of stores) {
      for (const product of ctx.products) {
        // 1) Construire l'URL de la source officielle (placeholder).
        const url = this.buildSourceUrl(store.id, product.id);

        // 2) Respect de robots.txt (sauf source API officielle dédiée).
        if (this.source === 'scrape' && !(await isAllowedByRobots(url, ctx.userAgent))) {
          continue; // interdit → on saute, on ne force jamais.
        }

        // 3) Cadence polie.
        await this.limiter.wait();

        try {
          // 4) Récupération + normalisation.
          //    En prod : const res = await this.politeFetch(url, ctx);
          //              const data = await res.json();
          //              records.push(this.normalize(store.id, product.id, data));
          //
          //    Tant qu'aucun accès officiel n'est branché, ce template ne renvoie rien.
          void url;
        } catch (err) {
          console.warn(`[${this.retailer}] échec ${store.id}/${product.id}:`, (err as Error).message);
        }
      }
    }
    return records;
  }

  private buildSourceUrl(storeId: string, productId: string): string {
    // TODO : remplacer par l'endpoint officiel (API stock magasin / flux affilié).
    return `https://example.invalid/${this.retailer}/${storeId}/${productId}`;
  }

  /** Adapter au format réel de la source. */
  // private normalize(storeId: string, productId: string, data: any): InventoryRecord { ... }
}
