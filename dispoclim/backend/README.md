# DispoClim — Backend MVP

API + collecteur de stock + paiement (Stripe) + alertes e-mail/SMS (Brevo).
Conception détaillée : **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

> Le front reste statique (GitHub Pages). Ce backend tourne à part (Render /
> Railway / Fly.io / Supabase). Le front bascule des données démo vers l'API
> réelle dès que `window.DISPOCLIM_API_BASE` pointe sur ce service.

## Arborescence

```
backend/
  ARCHITECTURE.md         # conception complète (stack, modèle, conformité, coûts)
  db/schema.sql           # schéma PostgreSQL
  db/seed.sql             # produits + enseignes + magasins de démarrage
  scripts/init-db.ts      # applique schema + seed
  src/
    server.ts             # API Express (inventory, alerts, checkout, webhooks, unsubscribe)
    config.ts  db.ts  geo.ts  types.ts
    connectors/           # base + demo + leroyMerlin (template) + registre
    collector/            # run (orchestration) + robots.txt + rate-limit
    alerts/engine.ts      # diff réassort → notifications éligibles
    notifications/brevo.ts# e-mail + SMS (1-clic unsubscribe)
    payments/stripe.ts    # Checkout paiement unique (sans reconduction)
```

## Démarrage local

```bash
cd dispoclim/backend
cp .env.example .env          # renseigner DATABASE_URL au minimum
npm install

# 1) Base (PostgreSQL UE — ex. Supabase). Applique schéma + seed.
npm run db:init

# 2) Première collecte avec le connecteur 'demo' (aucune source réelle requise)
npm run collector             # ENABLED_CONNECTORS=demo par défaut

# 3) API
npm run dev                   # http://localhost:8080/healthz
#    GET /api/inventory?product=midea-portasplit-12000&cp=74580&radius=100
```

Sans `DATABASE_URL`, l'API démarre en **mode dégradé** (`/healthz` répond,
`/api/inventory` renvoie 503) — utile pour vérifier le câblage.

## Brancher le front sur l'API

Dans le HTML du front, avant `assets/connectors.js` :

```html
<script>window.DISPOCLIM_API_BASE = "https://api.dispoclim.fr";</script>
```

`connectors.js` appellera `${API_BASE}/api/inventory?…` et retombera sur les
données démo si l'API est injoignable.

## Planification (collector)

Lancer `npm run collector -- hot` toutes les ~60 s, `-- warm` toutes les ~5 min,
`-- cold` toutes les ~20 min (cron Render/Railway, `pg_cron`, ou GitHub Actions).
Voir le tableau des paliers dans ARCHITECTURE.md §5.

## Paiement & alertes (activation)

1. Renseigner `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET`, configurer le webhook
   Stripe vers `POST /api/webhooks/stripe` (event `checkout.session.completed`).
2. Renseigner `BREVO_API_KEY` (+ expéditeurs). Sans clé, les envois sont en
   **dry-run** (journalisés, non envoyés) — pratique pour tester le moteur.
3. Flux : `POST /api/alerts` (alerte *pending*) → `POST /api/checkout` (Stripe) →
   webhook active le Pass et l'alerte → le collector détecte un réassort →
   `alerts/engine.ts` envoie l'e-mail/SMS avec lien de désinscription 1-clic.

## Sécurité / conformité (rappel)

- Secrets en variables d'environnement (jamais commités).
- Webhook Stripe à signature vérifiée, monté sur corps **brut**.
- Connecteurs : API officielle/affiliation en priorité ; sinon robots.txt +
  rate-limit + UA identifiant ; pas de réutilisation des images/logos de marque.
- Consentement tracé (`consents`), désinscription 1-clic, rétention limitée.
