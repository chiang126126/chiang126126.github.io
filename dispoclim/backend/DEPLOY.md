# DispoClim — Déploiement

Trois briques à déployer : **base PostgreSQL (UE)**, **API + collecteur (Node)**,
et le branchement des **secrets** (Stripe, Brevo). Deux chemins au choix.

---

## Chemin A — Render Blueprint (le plus « un clic »)

Le fichier [`render.yaml`](./render.yaml) décrit tout : 1 base Postgres
(Francfort) + l'API web + 3 cron (hot/warm/cold).

1. **Préparer** : Render lit `render.yaml` à la **racine du dépôt**. Comme le
   backend est dans `dispoclim/backend`, soit tu copies `render.yaml` à la racine
   (chaque service a déjà `rootDir: dispoclim/backend`), soit tu crées les
   services à la main avec Root Directory = `dispoclim/backend`.
2. Sur Render : **New + → Blueprint**, sélectionne ce dépôt → Render crée la base,
   l'API et les cron.
3. **Initialiser la base** une fois (schéma + seed). Depuis l'onglet *Shell* du
   service API (ou en local avec l'`DATABASE_URL` externe) :
   ```bash
   psql "$DATABASE_URL" -f db/schema.sql -f db/seed.sql
   # ou : npm run db:init
   ```
4. **Renseigner les secrets** (variables `sync:false`) dans le dashboard de l'API
   et des cron : `PUBLIC_BASE_URL` (= URL publique de l'API Render),
   `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`.
5. **Webhook Stripe** → pointer sur `https://<api>/api/webhooks/stripe`
   (événement `checkout.session.completed`), copier le *signing secret* dans
   `STRIPE_WEBHOOK_SECRET`.

> Plans **free** par défaut (utile pour tester) : la base gratuite expire après
> 90 j et l'API gratuite se met en veille. Pour la prod, passer en plan payant.

---

## Chemin B — Supabase (Postgres managé UE) + API en conteneur

### 1. Base Supabase (UE)
- Crée un projet Supabase **région EU (Frankfurt)**.
- SQL Editor → colle puis exécute `db/schema.sql`, puis `db/seed.sql`.
- Récupère la **connection string** (Project Settings → Database) → `DATABASE_URL`.
- *(Option)* planifier le collecteur avec l'extension **`pg_cron`**, ou garder les
  cron côté hébergeur de l'API.

### 2. API + collecteur (Docker)
Le [`Dockerfile`](./Dockerfile) build l'image (API par défaut). Héberge-la où tu
veux (Render, Railway, Fly.io, Scaleway…) :
```bash
docker build -t dispoclim-api dispoclim/backend
docker run -p 8080:8080 --env-file dispoclim/backend/.env dispoclim-api
# collecteur (même image) :
docker run --env-file .env dispoclim-api node dist/collector/run.js hot
```
Sur Railway/Fly : 1 service web (commande par défaut) + 1 job planifié par palier
(`node dist/collector/run.js hot|warm|cold`).

---

## Brancher le front (GitHub Pages) sur l'API

Dans les pages du front, avant `assets/connectors.js` :
```html
<script>window.DISPOCLIM_API_BASE = "https://<ton-api>";</script>
```
Le front appelle alors `${API_BASE}/api/inventory?…` et retombe sur les données
démo si l'API est injoignable. Vérifie que `FRONT_ORIGIN` (CORS) côté API vaut
bien `https://chiang126126.github.io`.

---

## Vérification post-déploiement

```bash
curl https://<api>/healthz
# → {"ok":true,"db":true,"connectors":["demo"]}
curl "https://<api>/api/inventory?product=midea-portasplit-12000&cp=74580&radius=100"
# → {"records":[…],"count":N}
```
Puis : créer une alerte de test (`POST /api/alerts`), payer en mode test Stripe,
vérifier l'activation du Pass via le webhook, et laisser le cron `hot` détecter un
réassort (le connecteur démo fait « bouger » le stock chaque heure) → e-mail/SMS
(en dry-run si `BREVO_API_KEY` absent).

---

## Passer du connecteur démo au connecteur réel

Une fois les identifiants d'une source obtenus (cf. recommandation d'intégration) :
1. Implémenter le connecteur dans `src/connectors/<enseigne>.ts` et l'enregistrer
   dans `src/connectors/index.ts`.
2. Ajouter les clés d'API en variables d'environnement.
3. Mettre `ENABLED_CONNECTORS=<enseigne>` (ou `demo,<enseigne>`).
4. Redéployer. Le reste de la chaîne (API, diff, alertes, paiement) ne change pas.
