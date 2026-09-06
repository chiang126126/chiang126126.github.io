// Point d'entrée de l'API DispoClim.
import express from 'express';
import { config, assertProd } from './config.js';
import { dbReady } from './db.js';
import { inventoryRouter } from './routes/inventory.js';
import { alertsRouter } from './routes/alerts.js';
import { checkoutRouter } from './routes/checkout.js';
import { webhooksRouter } from './routes/webhooks.js';

assertProd();
const app = express();

// CORS minimal pour le front (origine configurable).
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', config.frontOrigin);
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  res.header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// ⚠️ Le webhook Stripe doit être monté AVANT le parser JSON (corps brut requis).
app.use('/api', webhooksRouter);

// Parser JSON pour le reste.
app.use(express.json());

app.get('/healthz', async (_req, res) => {
  res.json({ ok: true, db: await dbReady(), connectors: config.collector.enabled });
});

app.use('/api', inventoryRouter);
app.use('/api', alertsRouter);
app.use('/api', checkoutRouter);

app.listen(config.port, () => {
  console.log(`DispoClim API sur :${config.port} — db=${config.databaseUrl ? 'configurée' : 'absente'} connecteurs=[${config.collector.enabled.join(',')}]`);
});
