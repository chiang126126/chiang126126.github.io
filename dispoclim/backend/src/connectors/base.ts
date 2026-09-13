// Contrat commun à tous les connecteurs + helper de fetch poli (UA + timeout).
import type { Connector, CollectContext, InventoryRecord } from '../types.js';

export type { Connector, CollectContext, InventoryRecord };

export abstract class BaseConnector implements Connector {
  abstract retailer: string;
  abstract source: 'api' | 'affiliate' | 'scrape';
  abstract fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]>;

  /** Fetch identifié, avec timeout — à utiliser dans tous les connecteurs réels. */
  protected async politeFetch(
    url: string,
    ctx: CollectContext,
    init: RequestInit = {},
    timeoutMs = 12000
  ): Promise<Response> {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetch(url, {
        ...init,
        signal: ctx.signal ?? ctrl.signal,
        headers: {
          'User-Agent': ctx.userAgent,
          'Accept-Language': 'fr-FR,fr;q=0.9',
          ...(init.headers ?? {}),
        },
      });
    } finally {
      clearTimeout(t);
    }
  }
}
