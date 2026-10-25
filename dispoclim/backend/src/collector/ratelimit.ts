// Limiteur de cadence simple (token sur fenêtre) + jitter, par connecteur/hôte.
export class RateLimiter {
  private minIntervalMs: number;
  private last = 0;

  constructor(opts: { perSecond: number }) {
    this.minIntervalMs = 1000 / Math.max(0.001, opts.perSecond);
  }

  async wait(): Promise<void> {
    const now = Date.now();
    const earliest = this.last + this.minIntervalMs;
    const jitter = Math.floor(Math.random() * 120); // évite les rafales synchronisées
    const delay = Math.max(0, earliest - now) + jitter;
    this.last = now + delay;
    if (delay > 0) await new Promise((r) => setTimeout(r, delay));
  }
}
