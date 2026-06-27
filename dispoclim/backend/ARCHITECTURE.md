# DispoClim — Architecture backend MVP

> Objectif : transformer le prototype statique en service réel capable de
> **collecter** la disponibilité en magasin, l'**exposer via une API**,
> **encaisser** le Pass (1,99 €) et **alerter** par e-mail/SMS au réassort —
> avec un coût d'exploitation minimal et une conformité France/UE dès le départ.

Le vrai produit n'est pas l'UI : c'est **l'exactitude de la donnée + la vitesse
d'alerte + la couverture des enseignes**. Toute l'architecture est pensée autour
de ces trois métriques.

---

## 1. Vue d'ensemble

```
                          ┌─────────────────────────────────────────────┐
                          │              SOURCES DE DONNÉES               │
                          │  API officielle ▸ flux affilié ▸ collecte     │
                          │  Leroy Merlin · Castorama · Boulanger · Darty │
                          │  Weldom · Bricoman · ManoMano · Amazon (PA-API)│
                          └───────────────┬─────────────────────────────-─┘
                                          │  (priorité : officiel > affilié > scrape encadré)
                  ┌───────────────────────▼───────────────────────┐
                  │     COLLECTOR (worker planifié, multi-tier)    │
                  │  robots.txt + rate-limit + backoff + cache     │
                  │  normalise → InventoryRecord → upsert + DIFF   │
                  └───────────────┬───────────────────┬───────────-┘
                                  │ upsert            │ transitions (rupture → dispo)
                  ┌───────────────▼────────┐   ┌──────▼───────────────────────┐
                  │   POSTGRES (UE)        │   │   ALERT ENGINE (file/queue)  │
                  │  stores, inventory,    │   │  match alertes actives +     │
                  │  alerts, passes,       │◀──│  Pass valide + consentement  │
                  │  notifications, consent│   │  → dispatch e-mail / SMS     │
                  └───────┬────────────────┘   └──────┬───────────────────────┘
                          │                           │
        ┌─────────────────▼──────────┐      ┌─────────▼─────────────┐
        │   API (Node/TS, Express)   │      │  Brevo (e-mail + SMS, │
        │  GET /api/inventory        │      │  EU) · 1-click unsub   │
        │  POST /api/alerts          │      └───────────────────────┘
        │  POST /api/checkout        │
        │  POST /api/webhooks/stripe │◀────────  Stripe Checkout (paiement unique 1,99 €)
        │  GET  /api/unsubscribe     │
        └─────────────┬──────────────┘
                      │  JSON
        ┌─────────────▼──────────────┐
        │   FRONT (statique actuel)  │  ← connectors.js appelle l'API, sinon démo
        │   GitHub Pages / Vercel    │
        └────────────────────────────┘
```

---

## 2. Stack recommandée (MVP, faible coût, EU)

| Brique | Choix recommandé | Pourquoi / alternatives |
|---|---|---|
| Langage | **Node 20 + TypeScript** | Même langage que le front, un seul écosystème. Alt : Python/FastAPI. |
| API | **Express** (ou Fastify) | Minimal, connu. |
| Base de données | **PostgreSQL (Supabase EU, région Francfort)** | Géo (PostGIS dispo), `pg_cron`, RLS, sauvegardes managées. Alt : Neon, RDS. |
| Worker / planif. | **Cron** (Supabase `pg_cron` / Render Cron / Railway / GitHub Actions) | Déclenche le collector par paliers de fréquence. |
| Paiement | **Stripe Checkout** — *paiement unique*, pas d'abonnement | Conforme « sans reconduction ». Webhook crée le Pass. |
| E-mail + SMS | **Brevo** (ex-Sendinblue, **français/UE**) | Email transactionnel + SMS chez le même fournisseur UE. Alt : Resend (email) + Twilio/OVH (SMS). |
| Hébergement API/worker | **Render** ou **Railway** ou **Fly.io** | Un petit service Node + un worker. Alt 100 % serverless : Supabase Edge Functions. |
| Front | **GitHub Pages** (actuel) ou **Vercel** | Reste statique, consomme l'API. |

**Résidence des données (RGPD)** : Postgres en région UE, Brevo (UE), Stripe
entité UE. Aucune donnée personnelle ne quitte l'UE sans garanties.

---

## 3. Modèle de données (PostgreSQL)

Voir `db/schema.sql`. Tables clés :

- **`products`**, **`retailers`**, **`stores`** — référentiel (stores géolocalisés `lat/lon`).
- **`inventory`** — *état courant* par (store, product) : `status`, `qty`, `price`,
  `fulfillment[]`, `reliability`, `source`, `updated_at`, **`prev_status`** (pour le diff).
- **`inventory_history`** *(option)* — historique prix/dispo pour graphiques & confiance.
- **`passes`** — Pass payés : `email`, `stripe_session_id`, `valid_from`, `valid_until`,
  `days`, `status`. **Aucune reconduction** (pas de `subscription_id`).
- **`alerts`** — abonnements : `email`, `phone?`, `product_ids[]`, `cp`, `radius_km`,
  `channels[]` (email/sms), `pass_id?`, `active`, `consent_at`, `consent_ip`,
  `unsubscribe_token`, `last_notified_at`.
- **`notifications`** — journal d'envoi (anti-doublon, preuve, métriques).
- **`consents`** — trace RGPD : type, texte exact accepté, horodatage, IP.

---

## 4. Connecteurs (le cœur juridique et technique)

Interface unique (`src/connectors/base.ts`) :

```ts
interface Connector {
  retailer: string;
  source: 'api' | 'affiliate' | 'scrape';
  // renvoie l'état pour les (produits × magasins) suivis
  fetchRecords(ctx: CollectContext): Promise<InventoryRecord[]>;
}
```

**Ordre de priorité imposé** (le collector choisit la meilleure source dispo) :

1. **API officielle marque/enseigne** (contrat + clé) → `reliability: 'high'`.
2. **Flux / API d'affiliation** (Awin, Effiliation, Amazon PA-API…) → `high/medium`.
3. **Collecte web encadrée**, uniquement à défaut → `medium/low`, avec garde-fous :
   - lecture et respect de **`robots.txt`** (`src/collector/robots.ts`) ;
   - **rate-limit** + backoff exponentiel + jitter (`src/collector/ratelimit.ts`) ;
   - **User-Agent identifiant** le bot + page de contact ;
   - **pas de contournement** de protection anti-bot, pas de connexion à un compte ;
   - extraction **factuelle minimale** (dispo, prix, magasin) — **pas de réutilisation
     des images/logos** sous marque ; lien sortant vers la fiche du marchand ;
   - prise en compte du **droit *sui generis* des bases de données** (Dir. 96/9/CE) :
     pas d'extraction substantielle/systématique ; privilégier l'accord officiel.

Chaque connecteur normalise vers `InventoryRecord` (même format que le front démo).
Ajouter une enseigne = ajouter un fichier dans `src/connectors/` et l'enregistrer
dans `index.ts`. Aucune autre partie du système ne change.

---

## 5. Collector multi-paliers (vitesse d'alerte)

Le collector (`src/collector/run.ts`) tourne en continu via cron, mais **pas à la
même fréquence pour tout** — c'est ce qui crée l'avance sur la page publique :

| Palier | Cible | Fréquence indicative |
|---|---|---|
| **Hot** | Produits/magasins très demandés et surveillés par ≥1 Pass actif | ~30–60 s |
| **Warm** | Produits suivis, zones actives | ~3–5 min |
| **Cold** | Reste du catalogue/parc | ~15–30 min |

À chaque cycle : `fetchRecords()` → **upsert** dans `inventory` → calcul du **diff**
(`prev_status` → `status`). Toute transition **`out_of_stock` → `in_stock`/`low_stock`**
émet un événement « réassort » consommé immédiatement par l'alert engine (file en
mémoire pour le MVP, ou Postgres `LISTEN/NOTIFY` / Redis plus tard). La notification
part **avant** le rafraîchissement du cache public → l'« ~1 min d'avance » promis.

---

## 6. Moteur d'alertes

`src/alerts/engine.ts` : pour chaque réassort (store, product) :

1. Sélectionne les `alerts` **actives** dont `product_ids` contient le produit et
   dont le magasin est **dans le rayon** (`cp` → coords → haversine).
2. Filtre celles ayant un **Pass valide** (`valid_until > now`) et un **consentement**
   pour le canal visé.
3. **Anti-doublon** : ignore si une notif identique (alert, store, product) a été
   envoyée depuis < N minutes (table `notifications`).
4. Enfile l'envoi e-mail/SMS via Brevo, journalise, met à jour `last_notified_at`.

---

## 7. Paiement (Pass, sans reconduction)

`src/payments/stripe.ts` + `routes/checkout.ts` + `routes/webhooks.ts` :

- `POST /api/checkout` crée une **Stripe Checkout Session** en `mode: 'payment'`
  (paiement unique — **jamais** `mode: 'subscription'`), montant 1,99 € TTC,
  métadonnées : `email`, `days` (30/60/90), critères d'alerte.
- Webhook `checkout.session.completed` → crée/prolonge un **`pass`** et active
  l'`alert` liée. Aucune carte conservée pour prélèvement futur.
- **Droit de rétractation** : la case « exécution immédiate + renonciation »
  (déjà dans `alerte.html`) est enregistrée dans `consents` au moment du paiement.

> ⚠️ Économie : Stripe EU ≈ 1,4 % + 0,25 € par transaction → ~0,28 € sur 1,99 €.
> Marge à surveiller ; envisager des paliers (ex. Pass 90 j) ou un panier groupé.

---

## 8. Notifications & conformité e-mail/SMS

`src/notifications/brevo.ts` :

- **E-mail transactionnel** avec en-tête **`List-Unsubscribe`** + `List-Unsubscribe-Post`
  (RFC 8058) → **désinscription en 1 clic** ; lien `GET /api/unsubscribe?token=...`.
- **SMS** : opt-in explicite, mention STOP.
- **Consentement** (case non pré-cochée) tracé dans `consents` (texte + horodatage + IP).
- **Double opt-in** recommandé pour l'e-mail (réduit les plaintes, améliore la délivrabilité).
- Conforme **art. L34-5 CPCE** (prospection) et **RGPD** ; rétention limitée à la
  validité du Pass puis purge/anonymisation.

---

## 9. API publique (consommée par le front)

| Route | Méthode | Rôle |
|---|---|---|
| `/api/inventory` | GET | `?product=&cp=&radius=&maxPrice=&fulfillment=&retailers=&inStock=` → `{records:[…]}` (format front actuel). Cache CDN court. |
| `/api/alerts` | POST | Crée une alerte (avant paiement : statut `pending`). |
| `/api/checkout` | POST | Crée la session Stripe, renvoie l'URL de paiement. |
| `/api/webhooks/stripe` | POST | Active le Pass après paiement. |
| `/api/unsubscribe` | GET/POST | Désinscription 1 clic (token). |
| `/api/account` | GET | Gérer/supprimer ses alertes (lien tokenisé, RGPD). |
| `/healthz` | GET | Supervision. |

Le front (`assets/connectors.js`) appelle déjà `loadInventory()` : il suffit de
pointer `API_BASE` vers le service et il bascule des données démo vers l'API réelle.

---

## 10. Sécurité & exploitation

- Secrets via variables d'environnement (`.env`, jamais commités) — voir `.env.example`.
- Vérification de **signature** des webhooks Stripe.
- **Rate-limit** des routes publiques (anti-abus).
- Logs structurés + métriques : *fraîcheur médiane*, *taux d'échec par connecteur*,
  *délai réassort→notif*, *taux de délivrabilité*. Ces 3 KPI = la qualité produit.
- Sauvegardes Postgres automatiques (managé).

---

## 11. Coût indicatif (MVP, ordre de grandeur mensuel)

| Poste | Estimation |
|---|---|
| Postgres managé (Supabase Pro / Neon) | 0–25 € |
| Hébergement API + worker (Render/Railway) | 7–20 € |
| Brevo (e-mail + SMS) | gratuit jusqu'à ~300 e-mails/j, puis à l'usage ; SMS ~0,05–0,08 €/envoi |
| Stripe | à la transaction (~0,28 € / Pass) |
| **Total fixe de départ** | **~15–45 €/mois** + variable usage |

---

## 12. Feuille de route d'implémentation

1. **S1** — DB + API `/api/inventory` branchée sur le **connecteur démo** (déjà fourni),
   front pointant sur l'API. *Le site marche « pour de vrai » avec une seule source.*
2. **S2** — Stripe Checkout + webhook + table `passes` ; page `alerte.html` reliée.
3. **S3** — Alert engine + Brevo (e-mail d'abord, 1-click unsub) ; collector 2 paliers.
4. **S4** — 1er **connecteur réel** (priorité à une enseigne offrant API/affiliation),
   robots/rate-limit ; SMS ; double opt-in.
5. **Ensuite** — montée en enseignes, historique prix, multi-régions, extension
   ventilateurs/déshumidificateurs/chauffages → *radar des produits saisonniers*.

---

## 13. Démarrage

Voir [`README.md`](./README.md). Le scaffold compile et tourne avec le **connecteur
démo** (mêmes données que le front), sans aucune source réelle ni secret : on peut
lancer l'API, créer une alerte de test et voir le moteur de diff fonctionner, puis
brancher Stripe/Brevo/connecteurs réels au fur et à mesure.
