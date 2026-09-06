// Vérification minimale de robots.txt (cache par hôte). Conservateur : en cas de
// doute ou d'erreur réseau sur une source 'scrape', on N'AUTORISE PAS.
const cache = new Map<string, { rules: { allow: boolean; path: string }[]; at: number }>();
const TTL_MS = 6 * 3600 * 1000;

async function loadRobots(origin: string, ua: string) {
  const cached = cache.get(origin);
  if (cached && Date.now() - cached.at < TTL_MS) return cached.rules;
  const rules: { allow: boolean; path: string }[] = [];
  try {
    const res = await fetch(`${origin}/robots.txt`, { headers: { 'User-Agent': ua } });
    if (res.ok) {
      const txt = await res.text();
      let applies = false;
      for (const raw of txt.split('\n')) {
        const line = raw.split('#')[0].trim();
        if (!line) continue;
        const [k, ...rest] = line.split(':');
        const key = k.trim().toLowerCase();
        const val = rest.join(':').trim();
        if (key === 'user-agent') applies = val === '*' || ua.toLowerCase().includes(val.toLowerCase());
        else if (applies && key === 'disallow') rules.push({ allow: false, path: val });
        else if (applies && key === 'allow') rules.push({ allow: true, path: val });
      }
    }
  } catch {
    // réseau KO → règle vide ; la décision conservatrice est gérée par l'appelant.
  }
  cache.set(origin, { rules, at: Date.now() });
  return rules;
}

export async function isAllowedByRobots(url: string, ua: string): Promise<boolean> {
  let u: URL;
  try { u = new URL(url); } catch { return false; }
  const rules = await loadRobots(u.origin, ua);
  // Plus longue règle correspondante gagne (sémantique usuelle).
  let decision = true;
  let bestLen = -1;
  for (const r of rules) {
    if (r.path && u.pathname.startsWith(r.path) && r.path.length > bestLen) {
      decision = r.allow;
      bestLen = r.path.length;
    }
  }
  return decision;
}
