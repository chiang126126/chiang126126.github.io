// Registre des connecteurs. Activer via ENABLED_CONNECTORS (csv).
import type { Connector } from '../types.js';
import { DemoConnector } from './demo.js';
import { LeroyMerlinConnector } from './leroyMerlin.js';

const ALL: Record<string, () => Connector> = {
  demo: () => new DemoConnector(),
  'leroy-merlin': () => new LeroyMerlinConnector(),
  // 'castorama': () => new CastoramaConnector(),
  // 'boulanger': () => new BoulangerConnector(),
  // 'darty': () => new DartyConnector(),
  // 'amazon-fr': () => new AmazonPaApiConnector(),
};

export function getConnectors(enabled: string[]): Connector[] {
  return enabled.map((id) => ALL[id]).filter(Boolean).map((f) => f());
}
