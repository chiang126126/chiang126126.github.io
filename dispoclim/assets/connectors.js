/* =========================================================================
   DispoClim — registre des connecteurs de données (squelette d'intégration)
   -------------------------------------------------------------------------
   Ce fichier décrit COMMENT brancher de vraies sources de stock à la place
   des données de démonstration (assets/data.js). Il n'effectue aucune
   collecte côté navigateur : un connecteur = une source qui, côté serveur,
   normalise ses données vers le format `InventoryRecord` puis les pousse
   dans un index interrogé par le front.

   Priorité juridique (cf. CGV & politique de données du site) :
     1. API officielle marque / enseigne (contrat, clé)         → fiabilité « high »
     2. Flux / API d'un programme d'affiliation (Awin, Effiliation…) → « high/medium »
     3. Collecte web encadrée (robots.txt, CGU, cadence raisonnable,
        pas de contournement de protection, pas de réutilisation
        d'images sous marque) — uniquement à défaut d'option 1 ou 2 → « medium/low »

   Chaque connecteur DOIT exposer une fonction `fetchRecords()` côté serveur
   renvoyant un tableau d'InventoryRecord. Statut possible :
     'ready'    : source contractualisée / active
     'planned'  : intégration prévue, en attente d'accès officiel
     'research' : faisabilité à l'étude
   ========================================================================= */
(function () {
  "use strict";

  const CONNECTORS = [
    {
      retailer: "leroy-merlin",
      method: "affiliate+store-api",
      status: "planned",
      auth: "Clé partenaire / flux affilié",
      notes:
        "Disponibilité magasin via page produit (stock par magasin). Privilégier un accès flux officiel ; respecter robots.txt et cadence.",
      respectRobots: true,
    },
    {
      retailer: "castorama",
      method: "store-api",
      status: "planned",
      auth: "À contractualiser",
      notes: "Vérifier disponibilité d'un point de retrait par magasin.",
      respectRobots: true,
    },
    {
      retailer: "boulanger",
      method: "affiliate",
      status: "planned",
      auth: "Programme d'affiliation",
      notes: "Stock magasin + retrait 1h exposés sur fiche produit.",
      respectRobots: true,
    },
    {
      retailer: "darty",
      method: "affiliate",
      status: "planned",
      auth: "Programme d'affiliation (groupe Fnac-Darty)",
      notes: "Disponibilité retrait magasin et livraison.",
      respectRobots: true,
    },
    {
      retailer: "weldom",
      method: "store-api",
      status: "research",
      auth: "À étudier",
      notes: "Réseau de proximité ; couverture stock variable.",
      respectRobots: true,
    },
    {
      retailer: "bricoman",
      method: "store-api",
      status: "research",
      auth: "À étudier",
      notes: "Orienté pro ; vérifier conditions d'accès.",
      respectRobots: true,
    },
    {
      retailer: "manomano",
      method: "awin-feed",
      status: "ready",
      auth: "Flux produits Awin (merchant ID 17547)",
      notes: "Première source réelle : prix + dispo en ligne via flux affilié Awin (cf. backend/CONNECTORS.md).",
      respectRobots: true,
    },
    {
      retailer: "amazon-fr",
      method: "creators-api",
      status: "research",
      auth: "Amazon Creators API (associé éligible)",
      notes:
        "PA-API 5.0 retirée le 15/05/2026 → migrer vers la Creators API ; accès conditionné aux ventes. Ne pas scraper Amazon.",
      respectRobots: true,
    },
  ];

  // Ingestion : remplace les données démo par l'API backend réelle.
  // Définir window.DISPOCLIM_API_BASE = "https://api.dispoclim.fr" pour activer.
  // Voir backend/ (Express + Postgres) — endpoint GET /api/inventory.
  async function loadInventory(params) {
    const base = window.DISPOCLIM_API_BASE;
    if (base) {
      try {
        const qs = new URLSearchParams(params || {}).toString();
        const res = await fetch(`${base}/api/inventory${qs ? "?" + qs : ""}`, { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.records)) return data.records;
        }
      } catch (_) {
        /* API injoignable : on retombe sur la démo ci-dessous */
      }
    }
    return window.DISPOCLIM ? window.DISPOCLIM.INVENTORY : [];
  }

  window.DISPOCLIM_CONNECTORS = { CONNECTORS, loadInventory };
})();
