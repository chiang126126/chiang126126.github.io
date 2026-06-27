# DispoClim — Radar de stock des climatiseurs mobiles en France 🇫🇷❄️

> *« Le climatiseur mobile en rupture partout ? On vous dit où il reste du stock. »*

DispoClim est un **service de surveillance de disponibilité en magasin** pour les
produits saisonniers en pénurie en France. Phase 1 : les climatiseurs mobiles à
forte demande — **Midea PortaSplit** et **COMFEE' 9000 BTU**.

C'est l'équivalent français, localisé et conforme, de [braucheklima.de](https://braucheklima.de) (Allemagne).

## Le problème résolu

Pendant la canicule, les climatiseurs mobiles populaires sont en rupture chez la
plupart des enseignes, se réapprovisionnent par à-coups et repartent en quelques
minutes. L'utilisateur ne veut pas ouvrir 7 sites de magasins chaque matin.
DispoClim regarde à sa place, **au niveau du magasin**, en continu, et l'alerte
au réassort.

## Ce qui est livré (prototype front statique)

| Fichier | Rôle |
|---|---|
| `index.html` | Landing + **radar interactif** (filtres CP, rayon, prix, retrait/livraison, enseignes, tri) |
| `alerte.html` | Page de création d'alerte / activation du Pass |
| `cgv.html` | Conditions Générales de Vente (rétractation, résiliation, médiation) |
| `confidentialite.html` | Politique de confidentialité **RGPD** + consentement marketing |
| `mentions-legales.html` | Éditeur, hébergeur, propriété intellectuelle, indépendance |
| `cookies.html` | Politique de cookies (CNIL) |
| `assets/data.js` | Produits, enseignes, ~40 magasins géolocalisés + **données de démo** déterministes |
| `assets/connectors.js` | **Squelette d'intégration** des vraies sources (API/affiliation/collecte) |
| `assets/app.js` | Logique : distance haversine, filtres, tri, rendu, gating freemium |
| `assets/style.css` | Design system (responsive, accessible) |
| `sitemap.xml` | SEO |

## Fonctionnalités du radar

- **Recherche par code postal + rayon** (10/25/50/100/200 km / France) — calcul de distance haversine.
- **Stock au niveau du magasin** : enseigne, adresse, statut (en stock / faible / rupture), quantité, **prix**, **horodatage**, **indice de fiabilité** et **source** (API / flux partenaire / web).
- **Filtres** : produit, prix max, retrait ou livraison, enseignes, « uniquement en stock ».
- **Tri** : distance, disponibilité, prix, fraîcheur de la donnée.
- **Bandeau marché dynamique** : magasins suivis, magasins avec stock, unités écoulées 24 h, dernière maj.
- **Freemium** : gratuit = 6 magasins les plus proches ; **Pass 1,99 €** (30–90 j, sans reconduction) = tous les magasins + alertes e-mail/SMS. *(Bascule « Aperçu Pass » pour démo.)*

## Modèle économique

- Recherche de base **gratuite**.
- **Pass de surveillance : 1,99 € en paiement unique**, 30 à 90 jours, **sans reconduction automatique** (≠ abonnement forcé).
- Revenus complémentaires possibles via liens d'affiliation sortants (sans surcoût pour l'utilisateur).

## Conformité (France/UE)

CGV, droit de rétractation (art. L221-18 & L221-28), résiliation/désinscription en
un clic, médiation de la consommation, RGPD, consentement marketing (case non
pré-cochée, opt-out), politique de cookies CNIL, respect des marques et des
règles d'accès aux données (priorité aux **API officielles / flux d'affiliation**).

## Brancher de vraies données

Le front charge `api/inventory.json` s'il existe (voir `connectors.loadInventory()`),
sinon il retombe sur les données de démo. Un job serveur normalisera chaque source
vers le format `InventoryRecord` documenté dans `assets/data.js`, en respectant
robots.txt, les CGU et une cadence raisonnable.

## Feuille de route

1. **Valider** sur Midea PortaSplit + COMFEE' (clim mobile, été 2026).
2. Contractualiser les **accès officiels** (API enseignes, affiliation).
3. Brancher **paiement** (Pass) + **passerelle e-mail/SMS** (alertes).
4. Étendre aux **ventilateurs, déshumidificateurs, chauffages d'appoint** → *radar des produits saisonniers en pénurie en France*.
5. **SEO / acquisition** sur les requêtes à forte intention (« Midea PortaSplit stock », « COMFEE 9000 BTU disponibilité », « climatiseur mobile en stock près de chez moi »).

## Développement local

Site 100 % statique — aucun build :

```bash
python3 -m http.server 8000
# puis http://localhost:8000/dispoclim/
```

> ⚠️ Les disponibilités, prix et horodatages affichés sont des **données de
> démonstration** générées de façon déterministe. Ils ne reflètent pas le stock
> réel des enseignes.
