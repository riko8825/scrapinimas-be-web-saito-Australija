"""SQLite storage for the ABR outreach dashboard.

Single file `outreach.db` next to the dashboard. Source of truth = CSV pipeline;
this DB layers mutable outreach state (sent/replied/notes/social URLs) on top.

Tables:
    leads            -- canonical lead snapshot (abn = PK)
    outreach         -- per-lead mutable tracking (1:1 with leads, abn FK)
    activity         -- append-only audit trail of status changes / notes
    imports          -- run history of CSV imports (for diffing)

Reimport is idempotent: existing `outreach` rows are preserved on UPSERT.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

# Įkraunam .env, kad OUTREACH_DB_PATH būtų prieinamas (dashboard paleidžiamas
# atskirai nuo pipeline, todėl reikia load'inti čia).
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_FILENAME = "outreach.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    abn               TEXT PRIMARY KEY,
    business_name     TEXT NOT NULL,
    name_normalized   TEXT,
    entity_type       TEXT,
    state             TEXT,
    postcode          TEXT,
    gst_status        TEXT,
    source_file       TEXT,
    has_domain        INTEGER NOT NULL DEFAULT 0,
    found_domain      TEXT,
    facebook_url      TEXT,
    instagram_url     TEXT,
    industry_keyword  TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_state      ON leads(state);
CREATE INDEX IF NOT EXISTS idx_leads_entity     ON leads(entity_type);
CREATE INDEX IF NOT EXISTS idx_leads_gst        ON leads(gst_status);
CREATE INDEX IF NOT EXISTS idx_leads_postcode   ON leads(postcode);
CREATE INDEX IF NOT EXISTS idx_leads_industry   ON leads(industry_keyword);
CREATE INDEX IF NOT EXISTS idx_leads_has_domain ON leads(has_domain);

CREATE TABLE IF NOT EXISTS outreach (
    abn               TEXT PRIMARY KEY REFERENCES leads(abn) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'new',
        -- new | queued | sent | replied | booked | won | lost | skip
    priority          INTEGER NOT NULL DEFAULT 3,   -- 1=high .. 5=low
    sent_at           TEXT,
    sent_channel      TEXT,                          -- fb | ig | email | dm | phone
    replied_at        TEXT,
    reply_sentiment   TEXT,                          -- positive | neutral | negative
    booked_at         TEXT,
    won_at            TEXT,
    lost_at           TEXT,
    lost_reason       TEXT,
    contact_name      TEXT,
    contact_email     TEXT,
    contact_phone     TEXT,
    notes             TEXT,
    tags              TEXT,                          -- comma-separated
    assigned_to       TEXT,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outreach_status   ON outreach(status);
CREATE INDEX IF NOT EXISTS idx_outreach_sent_at  ON outreach(sent_at);
CREATE INDEX IF NOT EXISTS idx_outreach_priority ON outreach(priority);
CREATE INDEX IF NOT EXISTS idx_outreach_assigned ON outreach(assigned_to);

CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    abn         TEXT,                            -- NULL for system-wide events
    happened_at TEXT NOT NULL,
    actor       TEXT,
    action      TEXT NOT NULL,
        -- status_change | note | channel_sent | reply_logged | tag_added | import
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_activity_abn  ON activity(abn);
CREATE INDEX IF NOT EXISTS idx_activity_when ON activity(happened_at DESC);

CREATE TABLE IF NOT EXISTS imports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    source_file   TEXT NOT NULL,
    rows_total    INTEGER,
    rows_inserted INTEGER,
    rows_updated  INTEGER,
    status        TEXT NOT NULL DEFAULT 'running'   -- running | ok | failed
);

-- Waterfall enrichment state (Plan A=Places, B=Website, C=Socials).
-- Separate table to keep `leads` schema stable + allow easy DROP if smoke fails.
CREATE TABLE IF NOT EXISTS enrichment (
    abn                     TEXT PRIMARY KEY REFERENCES leads(abn) ON DELETE CASCADE,
    -- Stage A: Google Places API
    stage_a_status          TEXT,        -- pending | ok | not_found | error | skipped
    stage_a_attempted_at    TEXT,
    stage_a_cost_usd        REAL NOT NULL DEFAULT 0,
    place_id                TEXT,
    trading_name            TEXT,
    formatted_address       TEXT,
    phone                   TEXT,
    website_url             TEXT,
    place_types             TEXT,        -- JSON array
    -- Stage B: Website scraper
    stage_b_status          TEXT,        -- pending | ok | blocked | no_data | error | skipped
    stage_b_attempted_at    TEXT,
    contact_email           TEXT,
    scraped_fb_url          TEXT,
    scraped_ig_url          TEXT,
    linkedin_url            TEXT,
    -- Stage C: Socials lookup (SerpAPI)
    stage_c_status          TEXT,        -- pending | ok | not_found | error | skipped
    stage_c_attempted_at    TEXT,
    stage_c_cost_usd        REAL NOT NULL DEFAULT 0,
    -- Scoring + audit
    priority_score          INTEGER,
    updated_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_enrich_stage_a    ON enrichment(stage_a_status);
CREATE INDEX IF NOT EXISTS idx_enrich_stage_b    ON enrichment(stage_b_status);
CREATE INDEX IF NOT EXISTS idx_enrich_stage_c    ON enrichment(stage_c_status);
CREATE INDEX IF NOT EXISTS idx_enrich_priority   ON enrichment(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_enrich_place_id   ON enrichment(place_id);

-- Per-run audit log (cost tracking, budget caps).
CREATE TABLE IF NOT EXISTS enrichment_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage           TEXT NOT NULL,           -- a | b | c
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    count_attempted INTEGER NOT NULL DEFAULT 0,
    count_ok        INTEGER NOT NULL DEFAULT 0,
    count_error     INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running'   -- running | ok | failed
);

CREATE INDEX IF NOT EXISTS idx_runs_stage ON enrichment_runs(stage, started_at DESC);
"""

VALID_STATUSES = (
    "new", "queued", "sent", "replied", "booked", "won", "lost", "skip",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open SQLite with sensible defaults for a single-user analytical workload."""
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        isolation_level=None,        # autocommit; we use explicit BEGIN where needed
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def default_db_path() -> Path:
    """Grąžina outreach.db kelią.

    Pirmenybė env var OUTREACH_DB_PATH (pvz. OneDrive lokacija, kur laikomi
    realūs duomenys). Jei nenustatyta — fallback į dashboard/outreach.db
    šalia šio modulio.
    """
    env = os.getenv("OUTREACH_DB_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / DB_FILENAME


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def log_activity(
    conn: sqlite3.Connection,
    abn: str | None,
    action: str,
    detail: str | None = None,
    actor: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO activity(abn, happened_at, actor, action, detail) "
        "VALUES (?,?,?,?,?)",
        (abn, utcnow_iso(), actor, action, detail),
    )


def ensure_outreach_row(conn: sqlite3.Connection, abn: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO outreach(abn, updated_at) VALUES (?,?)",
        (abn, utcnow_iso()),
    )


def set_status(
    conn: sqlite3.Connection,
    abns: Iterable[str],
    status: str,
    actor: str | None = None,
    channel: str | None = None,
    note: str | None = None,
) -> int:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    now = utcnow_iso()
    n = 0
    for abn in abns:
        ensure_outreach_row(conn, abn)
        sets: dict[str, Any] = {"status": status, "updated_at": now}
        if status == "sent":
            sets["sent_at"] = now
            if channel:
                sets["sent_channel"] = channel
        elif status == "replied":
            sets["replied_at"] = now
        elif status == "booked":
            sets["booked_at"] = now
        elif status == "won":
            sets["won_at"] = now
        elif status == "lost":
            sets["lost_at"] = now
            if note:
                sets["lost_reason"] = note

        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(
            f"UPDATE outreach SET {cols} WHERE abn = ?",
            (*sets.values(), abn),
        )
        log_activity(
            conn, abn, "status_change",
            detail=f"{status}" + (f" via {channel}" if channel else "")
                   + (f" — {note}" if note else ""),
            actor=actor,
        )
        n += 1
    return n


def update_outreach_fields(
    conn: sqlite3.Connection,
    abn: str,
    fields: dict[str, Any],
    actor: str | None = None,
) -> None:
    if not fields:
        return
    ensure_outreach_row(conn, abn)
    fields = {**fields, "updated_at": utcnow_iso()}
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE outreach SET {cols} WHERE abn = ?",
        (*fields.values(), abn),
    )
    log_activity(
        conn, abn, "note",
        detail="updated: " + ", ".join(fields.keys()),
        actor=actor,
    )


def update_lead_socials(
    conn: sqlite3.Connection,
    abn: str,
    facebook: str | None,
    instagram: str | None,
    actor: str | None = None,
) -> None:
    conn.execute(
        "UPDATE leads SET facebook_url = ?, instagram_url = ?, last_seen_at = ? "
        "WHERE abn = ?",
        (facebook or None, instagram or None, utcnow_iso(), abn),
    )
    log_activity(
        conn, abn, "note",
        detail=f"socials updated (fb={'y' if facebook else 'n'}, "
               f"ig={'y' if instagram else 'n'})",
        actor=actor,
    )
