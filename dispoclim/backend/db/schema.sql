-- =========================================================================
-- DispoClim — schéma PostgreSQL (MVP)
-- Région UE recommandée. Exécuter une fois à l'initialisation.
-- =========================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ---------- Référentiel ----------
create table if not exists products (
  id          text primary key,             -- ex. 'midea-portasplit-12000'
  brand       text not null,
  name        text not null,
  short       text,
  btu         integer,
  kw          numeric(4,1),
  energy      text,
  msrp        numeric(10,2),
  tags        text[] default '{}',
  active      boolean not null default true,
  created_at  timestamptz not null default now()
);

create table if not exists retailers (
  id          text primary key,             -- ex. 'leroy-merlin'
  name        text not null,
  color       text,
  kind        text not null default 'magasin', -- 'magasin' | 'en ligne'
  active      boolean not null default true
);

create table if not exists stores (
  id          text primary key,             -- ex. 'lm-annemasse'
  retailer_id text not null references retailers(id),
  name        text not null,
  city        text,
  cp          text,                          -- code postal
  lat         double precision,
  lon         double precision,
  online      boolean not null default false,
  poll_tier   text not null default 'warm', -- 'hot' | 'warm' | 'cold'
  active      boolean not null default true
);
create index if not exists idx_stores_retailer on stores(retailer_id);
create index if not exists idx_stores_cp on stores(cp);

-- ---------- État courant du stock (1 ligne par store × product) ----------
create table if not exists inventory (
  store_id    text not null references stores(id),
  product_id  text not null references products(id),
  status      text not null,                 -- 'in_stock' | 'low_stock' | 'out_of_stock'
  prev_status text,                           -- pour le diff réassort
  qty         integer,
  price       numeric(10,2),
  fulfillment text[] not null default '{}',  -- 'pickup','delivery'
  reliability text not null default 'medium',-- 'high' | 'medium' | 'low'
  source      text not null default 'scrape',-- 'api' | 'affiliate' | 'scrape'
  updated_at  timestamptz not null default now(),
  primary key (store_id, product_id)
);
create index if not exists idx_inventory_product_status on inventory(product_id, status);

-- ---------- Historique (option : confiance, graphiques prix) ----------
create table if not exists inventory_history (
  id          bigserial primary key,
  store_id    text not null,
  product_id  text not null,
  status      text not null,
  price       numeric(10,2),
  source      text,
  observed_at timestamptz not null default now()
);
create index if not exists idx_invhist_lookup on inventory_history(product_id, store_id, observed_at desc);

-- ---------- Pass payés (paiement unique, AUCUNE reconduction) ----------
create table if not exists passes (
  id                 uuid primary key default gen_random_uuid(),
  email              text not null,
  stripe_session_id  text unique,
  stripe_payment_id  text,
  amount_cents       integer not null default 199,
  currency           text not null default 'eur',
  days               integer not null default 30,         -- 30 / 60 / 90
  valid_from         timestamptz not null default now(),
  valid_until        timestamptz not null,
  status             text not null default 'active',       -- 'active' | 'expired' | 'refunded'
  created_at         timestamptz not null default now()
);
create index if not exists idx_passes_email on passes(email);

-- ---------- Abonnements d'alerte ----------
create table if not exists alerts (
  id                uuid primary key default gen_random_uuid(),
  email             text not null,
  phone             text,
  product_ids       text[] not null,
  cp                text not null,
  radius_km         integer not null default 50,
  max_price         numeric(10,2),
  fulfillment       text,                                   -- null | 'pickup' | 'delivery'
  channels          text[] not null default '{email}',      -- 'email','sms'
  pass_id           uuid references passes(id),
  active            boolean not null default false,          -- activé après paiement
  consent_at        timestamptz,
  consent_ip        text,
  unsubscribe_token text not null default encode(gen_random_bytes(16), 'hex'),
  last_notified_at  timestamptz,
  created_at        timestamptz not null default now()
);
create index if not exists idx_alerts_active on alerts(active) where active = true;
create index if not exists idx_alerts_email on alerts(email);
create unique index if not exists idx_alerts_unsub on alerts(unsubscribe_token);

-- ---------- Journal des notifications (anti-doublon + preuve) ----------
create table if not exists notifications (
  id          bigserial primary key,
  alert_id    uuid not null references alerts(id),
  store_id    text not null,
  product_id  text not null,
  channel     text not null,                  -- 'email' | 'sms'
  provider_id text,                            -- id message Brevo
  status      text not null default 'sent',   -- 'sent' | 'failed'
  sent_at     timestamptz not null default now()
);
create index if not exists idx_notif_dedupe on notifications(alert_id, store_id, product_id, sent_at desc);

-- ---------- Trace de consentement (RGPD / L34-5) ----------
create table if not exists consents (
  id          bigserial primary key,
  email       text not null,
  kind        text not null,                  -- 'marketing_email' | 'sms' | 'withdrawal_waiver'
  text_shown  text not null,                  -- texte exact accepté
  ip          text,
  created_at  timestamptz not null default now()
);
create index if not exists idx_consents_email on consents(email);
