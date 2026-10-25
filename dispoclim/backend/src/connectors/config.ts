// Configuration des connecteurs réels (identifiants via variables d'env).
function env(k: string, d = ''): string { return process.env[k] ?? d; }

/**
 * Awin — flux produits (Create-a-Feed / Product Data).
 * apiKey : clé API éditeur Awin. feeds : un flux par annonceur (advertiser).
 * `retailerId` doit correspondre à une enseigne de la table `retailers`,
 * disposant d'un magasin `online = true` (où sont rattachées les dispos en ligne).
 */
export const AWIN = {
  apiKey: env('AWIN_API_KEY'),
  language: env('AWIN_LANGUAGE', 'fr'),
  feeds: [
    { retailerId: 'manomano', feedId: env('AWIN_FEED_MANOMANO') },
    // { retailerId: 'boulanger', feedId: env('AWIN_FEED_BOULANGER') },
    // { retailerId: 'leroy-merlin', feedId: env('AWIN_FEED_LEROY_MERLIN') },
  ].filter((f) => f.feedId),
};

/**
 * Identification des produits dans un flux marchand :
 *  - `eans`  : clé primaire fiable (renseigner via env quand connus) ;
 *  - `keywords` : repli si le flux n'a pas d'EAN exploitable (tous mots requis).
 */
export interface ProductMatcher { eans: string[]; keywords: string[]; }

export const PRODUCT_MATCHERS: Record<string, ProductMatcher> = {
  'midea-portasplit-12000': {
    eans: env('EAN_PORTASPLIT_12000').split(',').map((s) => s.trim()).filter(Boolean),
    keywords: ['midea', 'portasplit'],
  },
  'comfee-9000': {
    eans: env('EAN_COMFEE_9000').split(',').map((s) => s.trim()).filter(Boolean),
    keywords: ['comfee', '9000'],
  },
};
