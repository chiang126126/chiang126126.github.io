// Registre des connecteurs. Activer via ENABLED_CONNECTORS (csv).
import type { Connector } from '../types.js';
import { DemoConnector } from './demo.js';
import { LeroyMerlinConnector } from './leroyMerlin.js';
import { AwinFeedConnector } from './awinFeed.js';

const ALL: Record<string, () => Connector> = {
  demo: () => new DemoConnector(),
  // Connecteur réel : flux produits Awin (ManoMano, Castorama, Darty… selon AWIN_FEED_*)
  awin: () => new AwinFeedConnector(),
  'leroy-merlin': () => new LeroyMerlinConnector(),
  // 'amazon-fr': PA-API 5.0 retirée le 15/05/2026 → migrer vers Creators API (cf. CONNECTORS.md)
};

export function getConnectors(enabled: string[]): Connector[] {
  return enabled.map((id) => ALL[id]).filter(Boolean).map((f) => f());
}
