-- ============================================================================
-- Migration: 001_v2lite — V2-LITE P0 stulpeliai
-- ============================================================================
-- Sesija #9 (2026-05-26+).
-- Pridedam pain-signal stulpelius enrichment lentelei:
--   P0.1 — Places rating + reviews + business_status + price_level
--   P0.2 — AU validation flag (post-Stage A)
--   P0.3 — Website classifier 0-3 + tech stack + footer year + mobile friendly
--
-- Idempotency: SQLite NE turi `ADD COLUMN IF NOT EXISTS`. Paleidžiam per
-- `apply_migration.py` helper'į, kuris tikrina `PRAGMA table_info(...)` prieš
-- kiekvieną ADD COLUMN.
--
-- Manual run:
--   python migrations/apply_migration.py 001_v2lite
--
-- Rollback (manual, jei reikia):
--   DROP/recreate enrichment lentelės (visi V2-LITE stulpeliai dingsta, esami
--   Stage A/B data lieka per UPSERT'us iš next run).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- P0.1 — Places rating + reviews
-- ---------------------------------------------------------------------------
ALTER TABLE enrichment ADD COLUMN review_count    INTEGER;
ALTER TABLE enrichment ADD COLUMN rating          REAL;
ALTER TABLE enrichment ADD COLUMN business_status TEXT;
    -- OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY (Google enum)
ALTER TABLE enrichment ADD COLUMN price_level     INTEGER;
    -- 0..4 (FREE..VERY_EXPENSIVE pagal Google enum mapping)

-- ---------------------------------------------------------------------------
-- P0.2 — AU validation
-- ---------------------------------------------------------------------------
-- au_validation_status: 'au_ok' | 'not_au' | 'unknown'
--   - au_ok      : praeina phone(+61) ARBA website(.au) ARBA address state
--   - not_au     : akivaizdžiai NE AU (anti-PROXYTECH bug)
--   - unknown    : nepakanka duomenų (no phone, no website, no formatted_address)
ALTER TABLE enrichment ADD COLUMN au_validation_status TEXT;
ALTER TABLE enrichment ADD COLUMN au_validation_reason TEXT;
    -- žmogiškas paaiškinimas: "phone +1 (US)", "website .ca", "address: Toronto"

-- ---------------------------------------------------------------------------
-- P0.3 — Website classifier
-- ---------------------------------------------------------------------------
-- website_class: 0 = no website
--                1 = dead / 5xx / cert error
--                2 = bad / outdated (no SSL, no viewport, framework <2018, footer<2020)
--                3 = modern (SSL + viewport + recent framework + footer ≥2020)
ALTER TABLE enrichment ADD COLUMN website_class    INTEGER;
ALTER TABLE enrichment ADD COLUMN mobile_friendly  INTEGER;   -- 0 | 1 | NULL
ALTER TABLE enrichment ADD COLUMN tech_stack       TEXT;      -- "wordpress" | "wix" | "squarespace" | "shopify" | "react" | "unknown"
ALTER TABLE enrichment ADD COLUMN footer_year      INTEGER;   -- parsed iš © YYYY
ALTER TABLE enrichment ADD COLUMN ssl_valid        INTEGER;   -- 0 | 1 | NULL
ALTER TABLE enrichment ADD COLUMN response_time_ms INTEGER;   -- TTFB
ALTER TABLE enrichment ADD COLUMN classifier_status TEXT;     -- 'ok' | 'unreachable' | 'error' | 'skipped' | NULL
ALTER TABLE enrichment ADD COLUMN classifier_attempted_at TEXT;

-- ---------------------------------------------------------------------------
-- Indices on new filterable fields
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_enrich_au_status      ON enrichment(au_validation_status);
CREATE INDEX IF NOT EXISTS idx_enrich_website_class  ON enrichment(website_class);
CREATE INDEX IF NOT EXISTS idx_enrich_rating         ON enrichment(rating);
CREATE INDEX IF NOT EXISTS idx_enrich_review_count   ON enrichment(review_count);
