# DispoClim — Choix de la première source réelle

Synthèse de la recherche d'intégration (sources 2025-2026) et décision pour le
premier connecteur réel. Code : `src/connectors/awinFeed.ts`.

## TL;DR

> **Première source = flux produits Awin, en commençant par ManoMano (FR).**
> Amazon PA-API 5.0 est écartée (API en fin de vie + accès conditionné aux ventes).

## Pourquoi pas Amazon (PA-API 5.0)

- **API retirée le 15 mai 2026** (dépréciée le 30 avril 2026) ; Amazon redirige
  vers la **Creators API**. À la date du projet, l'endpoint PA-API 5.0 est
  fermé → impasse pour une nouvelle intégration.
  Réf. : `webservices.amazon.com/paapi5/documentation/offersV2.html`,
  `affiliate-program.amazon.com/creatorsapi/docs`.
- **Accès conditionné aux ventes** : il faut un compte Associé approuvé et
  ~**10 ventes qualifiées / 30 jours** pour obtenir/garder les clés (sinon erreur
  `AssociateNotEligible`). Un nouveau projet n'y a pas accès.
  Réf. : `webservices.amazon.com/paapi5/documentation/troubleshooting/api-rates.html`.
- **Conditions restrictives** : cache prix/dispo **≤ 1 h**, **interdiction de
  stocker l'historique des prix**, **pas de stockage des images** (lien seulement),
  obligation d'afficher l'horodatage du prix.
  Réf. : `affiliate-program.amazon.com/help/operating/agreement`.

→ Le code Amazon reste possible plus tard via la **Creators API** une fois
l'éligibilité atteinte ; non prioritaire.

## Pourquoi Awin / ManoMano d'abord

- **ManoMano FR est actif sur Awin** (merchant ID **17547**, ~7 % de commission) —
  et ManoMano vend bien ces climatiseurs. Réf. : `ui.awin.com/merchant-profile/17547`.
- **Castorama (6991)** et **Darty (7735)** sont aussi sur Awin → extensions naturelles.
  Réf. : `ui.awin.com/merchant-profile/6991`, `ui.awin.com/merchant-profile-terms/7735`.
- **Flux produits documenté** avec exactement les champs utiles :
  `deep_link`, `product_name`, `search_price`, `currency`, `ean`, `mpn`,
  `in_stock`, `stock_quantity`, `merchant_product_id`.
  Réf. : `help.awin.com/docs/hosting-feeds`,
  `help.awin.com/docs/product-feed-list-download`.
- **Légal et structuré** : on est éditeur Awin approuvé par l'annonceur (relation
  d'affiliation), pas de scraping. Monétisation via les `deep_link`.

### Limite importante (intégrée au produit)

Un flux d'affiliation donne la **disponibilité et le prix EN LIGNE** de l'enseigne,
rafraîchis **~quotidiennement** (jusqu'à 4×/jour selon l'annonceur) — **pas** le
stock temps réel **par magasin physique**. Le connecteur rattache donc ces dispos
au magasin `online = true` de l'enseigne, avec `reliability = 'medium'`. Le stock
par magasin physique nécessitera des **API magasin officielles** (à négocier
enseigne par enseigne) — c'est la phase suivante.

### Réseaux par enseigne (France, 2025-2026)

| Enseigne | Réseau d'affiliation | Remarque |
|---|---|---|
| **ManoMano** | **Awin** (17547) | ✅ source de départ |
| Castorama | Awin (6991) | extension Awin directe |
| Darty | Awin (7735) **et** Effinity | |
| Boulanger | **Effinity** (ex-Effiliation) / programme propre | pas directement sur Awin |
| Leroy Merlin | **Kwanko** (et Affilae) ; Awin FR dormant | autre réseau requis |
| Weldom, Bricoman | non trouvés | — |

> ⚠️ Effiliation n'a pas fusionné avec Kwanko : la marque est devenue **Effinity**
> (2024). Boulanger/Darty sont sur Effinity ; Leroy Merlin sur Kwanko/Affilae.
> Couvrir toutes les enseignes imposera **plusieurs réseaux** (Awin + Kwanko + Effinity).

## Ce que le connecteur fait (`awinFeed.ts`)

1. Télécharge le flux produits Awin (URL Create-a-Feed ou `fid`), gère le **gzip**.
2. Parse le CSV (parseur tolérant : guillemets, virgules internes, CRLF).
3. Reconnaît les en-têtes via **alias** (Legacy `purl/instock/stockquant` ou
   Enhanced/Google) → robuste aux variations d'annonceur.
4. **Apparie** chaque ligne à un produit suivi (EAN prioritaire, sinon mots-clés).
5. Mappe vers `InventoryRecord` (statut depuis `in_stock`/`stock_quantity`, prix,
   `source: 'affiliate'`, magasin `online`).

Testé sans réseau ni clé : `npm run test:awin` (14 assertions, flux d'exemple dans
`scripts/fixtures/`). Compile : `npm run typecheck`.

## Identifiants à obtenir (action de ta part)

1. **Compte éditeur Awin** : inscription (`ui.awin.com/publisher-signup`), **dépôt
   remboursable ~5 €**, vérification du site. Puis **candidater au programme
   ManoMano (ID 17547)** et attendre l'approbation de l'annonceur.
2. **Clé API Product Feed** Awin (≠ clé Publisher API) → `AWIN_API_KEY`.
3. **Identifiant de flux** ManoMano (via *Create-a-Feed*, en sélectionnant les
   colonnes `deep_link, product_name, search_price, currency, ean, mpn, in_stock,
   stock_quantity, merchant_product_id`) → `AWIN_FEED_MANOMANO` (le `fid` ou l'URL).
4. **EAN** des produits suivis (Midea PortaSplit 12000, COMFEE' 9000) →
   `EAN_PORTASPLIT_12000`, `EAN_COMFEE_9000` (améliore l'appariement).
5. Mettre `ENABLED_CONNECTORS=awin` (ou `demo,awin`) et redéployer.

## Conformité affiliation

Reproduction fidèle des données annonceur, respect de leur PI, et **divulgation de
la relation d'affiliation** côté utilisateur (mention claire + liens d'affiliation
identifiés) — déjà prévue dans les mentions légales du front. Réf. :
`awin.com/gb/compliance-and-regulations/the-importance-of-affiliate-disclosure`.
