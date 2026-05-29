-- ============================================================================
-- Migration: 002_sales_angle — V2-LITE P1 sales angle generator
-- ============================================================================
-- Sesija #10 (2026-05-26+).
-- Rule-based (NE Claude/LLM, $0) angle template picker per lead.
--   angle_template_id   : kuris template'as suveikė (audit trail)
--   angle_subject       : email subject su filled placeholders
--   angle_body          : email body su filled placeholders
--   angle_generated_at  : kada generuota (re-run skip jei filled)
-- ============================================================================

ALTER TABLE enrichment ADD COLUMN angle_template_id  TEXT;
ALTER TABLE enrichment ADD COLUMN angle_subject      TEXT;
ALTER TABLE enrichment ADD COLUMN angle_body         TEXT;
ALTER TABLE enrichment ADD COLUMN angle_generated_at TEXT;

CREATE INDEX IF NOT EXISTS idx_enrich_angle_template ON enrichment(angle_template_id);
