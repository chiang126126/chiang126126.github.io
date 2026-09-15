// Configuration centralisée, lue depuis l'environnement.
function env(key: string, fallback = ''): string {
  return process.env[key] ?? fallback;
}

export const config = {
  port: Number(env('PORT', '8080')),
  publicBaseUrl: env('PUBLIC_BASE_URL', 'http://localhost:8080'),
  frontOrigin: env('FRONT_ORIGIN', '*'),
  databaseUrl: env('DATABASE_URL'),

  stripe: {
    secretKey: env('STRIPE_SECRET_KEY'),
    webhookSecret: env('STRIPE_WEBHOOK_SECRET'),
    passPriceCents: Number(env('PASS_PRICE_CENTS', '199')),
  },

  brevo: {
    apiKey: env('BREVO_API_KEY'),
    senderEmail: env('BREVO_SENDER_EMAIL', 'alertes@dispoclim.fr'),
    senderName: env('BREVO_SENDER_NAME', 'DispoClim'),
    smsSender: env('BREVO_SMS_SENDER', 'DispoClim'),
  },

  collector: {
    userAgent: env('COLLECTOR_USER_AGENT', 'DispoClimBot/0.1 (+https://dispoclim.fr/bot)'),
    enabled: env('ENABLED_CONNECTORS', 'demo').split(',').map((s) => s.trim()).filter(Boolean),
    notifyDedupeMinutes: Number(env('NOTIFY_DEDUPE_MINUTES', '120')),
  },
};

export function assertProd(): void {
  const missing: string[] = [];
  if (!config.databaseUrl) missing.push('DATABASE_URL');
  if (missing.length) {
    console.warn(`[config] variables manquantes (mode dégradé) : ${missing.join(', ')}`);
  }
}
