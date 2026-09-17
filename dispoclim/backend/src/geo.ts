// Utilitaires géo : distance haversine + code postal → coordonnées (centroïdes dép.).

export function haversineKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const R = 6371;
  const toR = (x: number) => (x * Math.PI) / 180;
  const dLat = toR(bLat - aLat);
  const dLon = toR(bLon - aLon);
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toR(aLat)) * Math.cos(toR(bLat)) * Math.sin(dLon / 2) ** 2;
  return Math.round(R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s)));
}

// Centroïdes par département (préfixe CP) — étendre selon besoin / remplacer par
// une vraie base de géocodage (BAN — Base Adresse Nationale, gratuite et officielle).
export const DEP_CENTROIDS: Record<string, [number, number]> = {
  '01': [46.2, 5.23], '06': [43.7, 7.2], '13': [43.4, 5.3], '21': [47.32, 5.04],
  '25': [47.24, 6.02], '31': [43.6, 1.43], '33': [44.84, -0.58], '34': [43.61, 3.88],
  '35': [48.11, -1.68], '38': [45.19, 5.72], '44': [47.22, -1.55], '45': [47.9, 1.9],
  '54': [48.69, 6.18], '59': [50.63, 3.06], '64': [43.3, -0.37], '67': [48.58, 7.75],
  '69': [45.76, 4.84], '73': [45.57, 5.92], '74': [46.06, 6.4], '75': [48.86, 2.35],
  '76': [49.44, 1.1], '77': [48.62, 2.95], '83': [43.4, 6.1], '92': [48.82, 2.25],
  '93': [48.91, 2.45], '94': [48.78, 2.45],
};

/** Coordonnées approximatives d'un code postal (préfixe département). */
export function cpToCoords(cp: string): [number, number] | null {
  const dep = (cp || '').trim().slice(0, 2);
  return DEP_CENTROIDS[dep] ?? null;
}
