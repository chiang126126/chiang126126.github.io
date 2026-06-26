/* =========================================================================
   DispoClim — données du radar de stock
   -------------------------------------------------------------------------
   ⚠️  DÉMO : les disponibilités, prix et horodatages ci-dessous sont générés
   de façon déterministe à des fins de démonstration de l'interface.
   En production, ces enregistrements proviendront des connecteurs définis
   dans assets/connectors.js (API officielles, flux affiliés ou collecte
   encadrée), au format `InventoryRecord` documenté plus bas.

   InventoryRecord = {
     storeId, productId,
     status: 'in_stock' | 'low_stock' | 'out_of_stock',
     qty: number|null,
     price: number|null,           // EUR TTC
     fulfillment: ['pickup','delivery'],
     updatedAt: epoch ms,
     reliability: 'high'|'medium'|'low',
     source: 'api'|'affiliate'|'scrape'
   }
   ========================================================================= */
(function () {
  "use strict";

  // ----- Produits suivis (phase 1 : climatiseurs mobiles forte demande) -----
  const PRODUCTS = [
    {
      id: "midea-portasplit-12000",
      brand: "Midea",
      name: "Midea PortaSplit Mobile Silent 4-en-1",
      short: "PortaSplit 12000 BTU",
      btu: 12000,
      kw: 3.5,
      energy: "A++",
      msrp: 799,
      tags: ["Climatiseur", "Chauffage", "Déshumidificateur", "Ventilateur"],
      img: "❄️",
    },
    {
      id: "comfee-9000",
      brand: "COMFEE'",
      name: "COMFEE' Mobile Air Conditioner 9000 BTU/h",
      short: "COMFEE' 9000 BTU",
      btu: 9000,
      kw: 2.6,
      energy: "A",
      msrp: 299,
      tags: ["Climatiseur", "Déshumidificateur", "Ventilateur"],
      img: "🌬️",
    },
    {
      id: "midea-portasplit-cool",
      brand: "Midea",
      name: "Midea PortaSplit Cool",
      short: "PortaSplit Cool",
      btu: 10000,
      kw: 2.9,
      energy: "A+",
      msrp: 649,
      tags: ["Climatiseur", "Déshumidificateur"],
      img: "❄️",
    },
    {
      id: "support-universel",
      brand: "Midea",
      name: "Support mural universel PortaSplit",
      short: "Support universel",
      btu: null,
      kw: null,
      energy: null,
      msrp: 49,
      tags: ["Accessoire", "Installation"],
      img: "🛠️",
    },
  ];

  // ----- Enseignes / canaux suivis -----
  const RETAILERS = [
    { id: "leroy-merlin", name: "Leroy Merlin", color: "#78be20", type: "magasin" },
    { id: "castorama", name: "Castorama", color: "#0a4ea2", type: "magasin" },
    { id: "boulanger", name: "Boulanger", color: "#e2001a", type: "magasin" },
    { id: "darty", name: "Darty", color: "#e2001a", type: "magasin" },
    { id: "weldom", name: "Weldom", color: "#e74011", type: "magasin" },
    { id: "bricoman", name: "Bricoman", color: "#005ca9", type: "magasin" },
    { id: "manomano", name: "ManoMano", color: "#5c2d91", type: "en ligne" },
    { id: "amazon-fr", name: "Amazon.fr", color: "#ff9900", type: "en ligne" },
  ];

  // ----- Magasins (coordonnées réelles approximatives des villes) -----
  // Couverture nationale de démonstration ; la prod en couvrira ~1 200.
  const STORES = [
    // Auvergne-Rhône-Alpes (zone de l'utilisateur : Viry 74)
    { id: "lm-annemasse", retailer: "leroy-merlin", name: "Leroy Merlin Annemasse", city: "Ville-la-Grand", cp: "74100", lat: 46.207, lon: 6.252 },
    { id: "casto-annecy", retailer: "castorama", name: "Castorama Annecy", city: "Seynod", cp: "74600", lat: 45.879, lon: 6.085 },
    { id: "bou-annemasse", retailer: "boulanger", name: "Boulanger Annemasse", city: "Étrembières", cp: "74100", lat: 46.176, lon: 6.225 },
    { id: "darty-annecy", retailer: "darty", name: "Darty Annecy", city: "Épagny", cp: "74330", lat: 45.943, lon: 6.092 },
    { id: "weldom-saint-julien", retailer: "weldom", name: "Weldom Saint-Julien-en-Genevois", city: "Saint-Julien-en-Genevois", cp: "74160", lat: 46.143, lon: 6.083 },
    { id: "bric-annecy", retailer: "bricoman", name: "Bricoman Annecy", city: "Metz-Tessy", cp: "74370", lat: 45.945, lon: 6.105 },
    { id: "lm-lyon-est", retailer: "leroy-merlin", name: "Leroy Merlin Lyon Est", city: "Saint-Priest", cp: "69800", lat: 45.700, lon: 4.944 },
    { id: "bou-lyon", retailer: "boulanger", name: "Boulanger Lyon La Part-Dieu", city: "Lyon", cp: "69003", lat: 45.760, lon: 4.857 },
    { id: "darty-grenoble", retailer: "darty", name: "Darty Grenoble", city: "Échirolles", cp: "38130", lat: 45.145, lon: 5.717 },
    { id: "casto-chambery", retailer: "castorama", name: "Castorama Chambéry", city: "La Ravoire", cp: "73490", lat: 45.560, lon: 5.946 },

    // Île-de-France
    { id: "lm-ivry", retailer: "leroy-merlin", name: "Leroy Merlin Ivry", city: "Ivry-sur-Seine", cp: "94200", lat: 48.813, lon: 2.391 },
    { id: "casto-pleyel", retailer: "castorama", name: "Castorama Saint-Denis Pleyel", city: "Saint-Denis", cp: "93200", lat: 48.920, lon: 2.345 },
    { id: "bou-madeleine", retailer: "boulanger", name: "Boulanger Paris Madeleine", city: "Paris", cp: "75008", lat: 48.870, lon: 2.324 },
    { id: "darty-republique", retailer: "darty", name: "Darty Paris République", city: "Paris", cp: "75011", lat: 48.867, lon: 2.363 },
    { id: "bric-gennevilliers", retailer: "bricoman", name: "Bricoman Gennevilliers", city: "Gennevilliers", cp: "92230", lat: 48.933, lon: 2.295 },
    { id: "weldom-vincennes", retailer: "weldom", name: "Weldom Vincennes", city: "Vincennes", cp: "94300", lat: 48.847, lon: 2.439 },
    { id: "lm-villiers", retailer: "leroy-merlin", name: "Leroy Merlin Villiers-en-Bière", city: "Villiers-en-Bière", cp: "77190", lat: 48.503, lon: 2.611 },

    // PACA
    { id: "lm-marseille", retailer: "leroy-merlin", name: "Leroy Merlin Marseille La Valentine", city: "Marseille", cp: "13011", lat: 43.293, lon: 5.475 },
    { id: "bou-nice", retailer: "boulanger", name: "Boulanger Nice Lingostière", city: "Nice", cp: "06200", lat: 43.722, lon: 7.197 },
    { id: "darty-aix", retailer: "darty", name: "Darty Aix-en-Provence", city: "Aix-en-Provence", cp: "13090", lat: 43.530, lon: 5.430 },
    { id: "casto-toulon", retailer: "castorama", name: "Castorama Toulon La Garde", city: "La Garde", cp: "83130", lat: 43.124, lon: 6.013 },

    // Occitanie
    { id: "lm-toulouse", retailer: "leroy-merlin", name: "Leroy Merlin Toulouse Gramont", city: "Toulouse", cp: "31200", lat: 43.633, lon: 1.487 },
    { id: "bou-montpellier", retailer: "boulanger", name: "Boulanger Montpellier Odysseum", city: "Montpellier", cp: "34000", lat: 43.604, lon: 3.918 },
    { id: "bric-toulouse", retailer: "bricoman", name: "Bricoman Toulouse Sud", city: "Roques", cp: "31120", lat: 43.515, lon: 1.385 },

    // Nouvelle-Aquitaine
    { id: "lm-bordeaux", retailer: "leroy-merlin", name: "Leroy Merlin Bordeaux Lac", city: "Bordeaux", cp: "33300", lat: 44.881, lon: -0.560 },
    { id: "darty-bordeaux", retailer: "darty", name: "Darty Bordeaux Mériadeck", city: "Bordeaux", cp: "33000", lat: 44.838, lon: -0.589 },
    { id: "weldom-biarritz", retailer: "weldom", name: "Weldom Biarritz", city: "Biarritz", cp: "64200", lat: 43.481, lon: -1.558 },

    // Hauts-de-France
    { id: "lm-lille", retailer: "leroy-merlin", name: "Leroy Merlin Lille Lomme", city: "Lomme", cp: "59160", lat: 50.640, lon: 2.985 },
    { id: "casto-lille", retailer: "castorama", name: "Castorama Englos", city: "Englos", cp: "59320", lat: 50.640, lon: 2.965 },
    { id: "bou-lille", retailer: "boulanger", name: "Boulanger Villeneuve-d'Ascq", city: "Villeneuve-d'Ascq", cp: "59650", lat: 50.640, lon: 3.130 },

    // Grand Est
    { id: "lm-strasbourg", retailer: "leroy-merlin", name: "Leroy Merlin Strasbourg", city: "Vendenheim", cp: "67550", lat: 48.665, lon: 7.715 },
    { id: "darty-nancy", retailer: "darty", name: "Darty Nancy", city: "Houdemont", cp: "54180", lat: 48.640, lon: 6.180 },
    { id: "bric-reims", retailer: "bricoman", name: "Bricoman Reims", city: "Reims", cp: "51100", lat: 49.244, lon: 4.060 },

    // Pays de la Loire / Bretagne
    { id: "lm-nantes", retailer: "leroy-merlin", name: "Leroy Merlin Nantes Saint-Herblain", city: "Saint-Herblain", cp: "44800", lat: 47.230, lon: -1.640 },
    { id: "bou-rennes", retailer: "boulanger", name: "Boulanger Rennes Cesson", city: "Cesson-Sévigné", cp: "35510", lat: 48.120, lon: -1.600 },
    { id: "darty-nantes", retailer: "darty", name: "Darty Nantes Atlantis", city: "Saint-Herblain", cp: "44800", lat: 47.225, lon: -1.630 },

    // Normandie / Centre
    { id: "casto-rouen", retailer: "castorama", name: "Castorama Rouen Tourville", city: "Tourville-la-Rivière", cp: "76410", lat: 49.330, lon: 1.120 },
    { id: "lm-orleans", retailer: "leroy-merlin", name: "Leroy Merlin Orléans Saran", city: "Saran", cp: "45770", lat: 47.950, lon: 1.880 },

    // En ligne (livraison nationale)
    { id: "manomano-fr", retailer: "manomano", name: "ManoMano (livraison France)", city: "En ligne", cp: "00000", lat: null, lon: null, online: true },
    { id: "amazon-fr-store", retailer: "amazon-fr", name: "Amazon.fr (livraison France)", city: "En ligne", cp: "00000", lat: null, lon: null, online: true },
  ];

  // ----- Centroïdes par département (préfixe CP) pour la recherche par CP -----
  const DEP_CENTROIDS = {
    "01": [46.20, 5.23], "06": [43.70, 7.20], "13": [43.40, 5.30], "14": [49.10, -0.30],
    "21": [47.32, 5.04], "25": [47.24, 6.02], "29": [48.20, -4.10], "31": [43.60, 1.43],
    "33": [44.84, -0.58], "34": [43.61, 3.88], "35": [48.11, -1.68], "37": [47.39, 0.69],
    "38": [45.19, 5.72], "42": [45.44, 4.39], "44": [47.22, -1.55], "45": [47.90, 1.90],
    "49": [47.47, -0.55], "51": [49.04, 4.02], "54": [48.69, 6.18], "57": [49.12, 6.18],
    "59": [50.63, 3.06], "62": [50.45, 2.83], "63": [45.78, 3.08], "64": [43.30, -0.37],
    "66": [42.70, 2.90], "67": [48.58, 7.75], "68": [47.75, 7.34], "69": [45.76, 4.84],
    "72": [48.00, 0.20], "73": [45.57, 5.92], "74": [46.06, 6.40], "75": [48.86, 2.35],
    "76": [49.44, 1.10], "77": [48.62, 2.95], "78": [48.80, 1.95], "80": [49.89, 2.30],
    "83": [43.40, 6.10], "84": [43.95, 4.81], "86": [46.58, 0.34], "87": [45.83, 1.26],
    "90": [47.64, 6.86], "91": [48.53, 2.25], "92": [48.82, 2.25], "93": [48.91, 2.45],
    "94": [48.78, 2.45], "95": [49.05, 2.10],
  };

  // ----- RNG déterministe (mulberry32) -----
  function rng(seed) {
    return function () {
      seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hash(str) {
    let h = 2166136261;
    for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  // ----- Génération des enregistrements de stock (démo) -----
  function buildInventory() {
    const now = Date.now();
    const records = [];
    for (const store of STORES) {
      for (const product of PRODUCTS) {
        const r = rng(hash(store.id + "|" + product.id));
        // Le support universel est très disponible ; les clims, beaucoup moins (pénurie).
        const scarcity = product.id === "support-universel" ? 0.20
          : product.id === "comfee-9000" ? 0.50
          : product.id === "midea-portasplit-cool" ? 0.62
          : 0.68; // PortaSplit 12000 : la plus rare
        const onlineBonus = store.online ? -0.18 : 0; // en ligne un peu mieux fourni
        const roll = r();
        let status, qty;
        if (roll < scarcity + onlineBonus) { status = "out_of_stock"; qty = 0; }
        else if (roll < scarcity + onlineBonus + 0.12) { status = "low_stock"; qty = 1 + Math.floor(r() * 3); }
        else { status = "in_stock"; qty = 3 + Math.floor(r() * 14); }

        // Prix : MSRP ± selon l'enseigne et la tension
        const swing = (r() - 0.4) * 0.16;
        let price = product.msrp ? Math.round(product.msrp * (1 + swing) * 100) / 100 : null;
        if (price) price = Math.round(price) - 0.01; // .99/.01 réaliste

        // Fraîcheur de la donnée : 1 à ~180 min, en ligne plus frais
        const ageMin = Math.floor(r() * (store.online ? 12 : 180)) + 1;
        const reliability = ageMin <= 15 ? "high" : ageMin <= 60 ? "medium" : "low";
        const source = store.online ? "affiliate" : (r() < 0.45 ? "api" : "scrape");

        const fulfillment = store.online ? ["delivery"]
          : (r() < 0.85 ? ["pickup", "delivery"] : ["pickup"]);

        records.push({
          storeId: store.id,
          productId: product.id,
          status, qty,
          price,
          fulfillment,
          updatedAt: now - ageMin * 60000,
          reliability, source,
        });
      }
    }
    return records;
  }

  const INVENTORY = buildInventory();

  // ----- Statistiques marché (bandeau dynamique) -----
  function marketStats() {
    const totalStores = STORES.length;
    const climRecords = INVENTORY.filter(
      (x) => x.productId !== "support-universel"
    );
    const inStockStores = new Set(
      climRecords.filter((x) => x.status !== "out_of_stock").map((x) => x.storeId)
    ).size;
    // "Vendus 24h" : estimation déterministe à partir de la tension
    const r = rng(hash("sold24h" + new Date().toISOString().slice(0, 13)));
    const sold24h = 40 + Math.floor(r() * 70);
    const lastUpdate = Math.max(...INVENTORY.map((x) => x.updatedAt));
    return { totalStores, inStockStores, sold24h, lastUpdate, monitored: 1176 };
  }

  window.DISPOCLIM = {
    PRODUCTS, RETAILERS, STORES, INVENTORY,
    DEP_CENTROIDS,
    marketStats,
  };
})();
