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
      method: "affiliate-api",
      status: "planned",
      auth: "API marchand / affiliation",
      notes: "Marketplace : suivre vendeur + délai d'expédition.",
      respectRobots: true,
    },
    {
      retailer: "amazon-fr",
      method: "pa-api",
      status: "planned",
      auth: "Amazon Product Advertising API (associé)",
      notes:
        "Utiliser l'API officielle (PA-API) + liens affiliés ; ne pas scraper Amazon.",
      respectRobots: true,
    },
  ];

  // Ingestion : remplace les données démo par un flux réel.
  // En production, on chargera /api/inventory.json (généré côté serveur).
  async function loadInventory() {
    try {
      const res = await fetch("api/inventory.json", { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.records) && data.records.length) return data.records;
      }
    } catch (_) {
      /* pas de flux réel disponible : on retombe sur la démo */
    }
    return window.DISPOCLIM ? window.DISPOCLIM.INVENTORY : [];
  }

  window.DISPOCLIM_CONNECTORS = { CONNECTORS, loadInventory };
})();
