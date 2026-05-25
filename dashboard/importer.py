"""CSV → SQLite importer for the outreach dashboard.

Imports rows from the ABR pipeline CSVs into `outreach.db`:

    output/filtered_with_dns.csv   (preferred — has has_domain/found_domain)
    output/no_website.csv          (fallback — already filtered to no-domain rows)
    output/has_social.csv          (optional — fb/ig URLs when available)

Idempotent: re-running the import preserves existing `outreach` rows
(status, notes, sent_at). Lead snapshot fields are UPSERTed.

CLI:
    python -m dashboard.importer --csv ../output/filtered_with_dns.csv
    python -m dashboard.importer            # auto-discovers all known CSVs
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from dashboard.db import (
    connect,
    default_db_path,
    init_schema,
    log_activity,
    utcnow_iso,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent

CANDIDATE_CSVS = [
    PROJECT_ROOT / "output" / "filtered_with_dns.csv",
    PROJECT_ROOT / "output" / "no_website.csv",
    PROJECT_ROOT / "output" / "filtered_businesses.csv",
]

SOCIAL_CSV = PROJECT_ROOT / "output" / "has_social.csv"


# --- Industry keyword detection ------------------------------------------------
# Lightweight signal so dashboard can group leads even before the
# enrichment stage has tagged them. Add/extend freely — first match wins.
INDUSTRY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("electrical",       ("electrical", "electrician", "sparky")),
    ("plumbing",         ("plumbing", "plumber")),
    ("construction",     ("construction", "builders", "carpentry", "concrete",
                          "roofing", "tiling", "painting", "rendering")),
    ("hvac",             ("air conditioning", "hvac", "refrigeration",
                          "heating", "cooling")),
    ("landscaping",      ("landscaping", "garden", "lawn", "turf", "mowing")),
    ("cleaning",         ("cleaning", "carpet care", "pressure wash")),
    ("automotive",       ("auto ", "motors", "mechanic", "panel beat",
                          "smash repair", "tyres", "automotive")),
    ("transport",        ("transport", "logistics", "freight", "haulage",
                          "removal", "removals", "courier", "truck")),
    ("hospitality",      ("cafe", "restaurant", "bistro", "catering",
                          "bakery", "coffee", "kebab", "pizza", "sushi")),
    ("retail",           ("retail", "store", "shop", "boutique", "outlet")),
    ("real_estate",      ("real estate", "realty", "property", "realtor")),
    ("beauty",           ("beauty", "salon", "barber", "nails", "skin",
                          "spa", "lash", "brow", "massage")),
    ("fitness",          ("fitness", "gym", "yoga", "pilates", "personal train",
                          "crossfit")),
    ("healthcare",       ("dental", "dentist", "chiro", "physio", "podiatry",
                          "psychology", "medical", "clinic", "wellness")),
    ("legal",            ("lawyers", "legal", "solicitors", "barristers",
                          "conveyancing")),
    ("accounting",       ("accounting", "accountants", "bookkeeping",
                          "taxation")),
    ("marketing",        ("marketing", "advertising", "branding", "media",
                          "digital agency", "seo", "creative")),
    ("consulting",       ("consulting", "consultancy", "advisory")),
    ("trades",           ("trades", "handyman", "maintenance", "services")),
]


def detect_industry(name: str) -> str | None:
    lower = name.lower()
    for label, keywords in INDUSTRY_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return label
    return None


# --- CSV iteration -------------------------------------------------------------

def iter_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        yield from reader


def _as_bool(value: str | None) -> int:
    return 1 if str(value).strip().lower() in {"true", "1", "yes"} else 0


# --- Import logic --------------------------------------------------------------

def upsert_leads(
    conn: sqlite3.Connection,
    csv_path: Path,
) -> tuple[int, int, int]:
    """Return (rows_total, rows_inserted, rows_updated)."""
    now = utcnow_iso()

    cur = conn.cursor()
    cur.execute("BEGIN")

    import_id = cur.execute(
        "INSERT INTO imports(started_at, source_file, status) VALUES (?,?,?)",
        (now, csv_path.name, "running"),
    ).lastrowid

    inserted = updated = total = 0
    try:
        for row in iter_csv_rows(csv_path):
            abn = (row.get("abn") or "").strip()
            if not abn:
                continue
            total += 1

            has_domain = _as_bool(row.get("has_domain"))
            # no_website.csv was filtered to has_domain=False — treat absence
            # of column as 0 (no website found).
            found_domain = (row.get("found_domain") or "").strip() or None
            industry = detect_industry(row.get("business_name", ""))

            existing = cur.execute(
                "SELECT first_seen_at FROM leads WHERE abn = ?", (abn,),
            ).fetchone()

            if existing:
                cur.execute(
                    """
                    UPDATE leads SET
                        business_name    = ?,
                        name_normalized  = ?,
                        entity_type      = ?,
                        state            = ?,
                        postcode         = ?,
                        gst_status       = ?,
                        source_file      = ?,
                        has_domain       = ?,
                        found_domain     = COALESCE(?, found_domain),
                        industry_keyword = COALESCE(industry_keyword, ?),
                        last_seen_at     = ?
                    WHERE abn = ?
                    """,
                    (
                        row.get("business_name", ""),
                        row.get("name_normalized"),
                        row.get("entity_type"),
                        row.get("state"),
                        row.get("postcode"),
                        row.get("gst_status"),
                        row.get("source_file"),
                        has_domain,
                        found_domain,
                        industry,
                        now,
                        abn,
                    ),
                )
                updated += 1
            else:
                cur.execute(
                    """
                    INSERT INTO leads(
                        abn, business_name, name_normalized, entity_type,
                        state, postcode, gst_status, source_file,
                        has_domain, found_domain, industry_keyword,
                        first_seen_at, last_seen_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        abn,
                        row.get("business_name", ""),
                        row.get("name_normalized"),
                        row.get("entity_type"),
                        row.get("state"),
                        row.get("postcode"),
                        row.get("gst_status"),
                        row.get("source_file"),
                        has_domain,
                        found_domain,
                        industry,
                        now,
                        now,
                    ),
                )
                # initial outreach row with status='new'
                cur.execute(
                    "INSERT OR IGNORE INTO outreach(abn, updated_at) VALUES (?,?)",
                    (abn, now),
                )
                inserted += 1

        cur.execute(
            "UPDATE imports SET finished_at = ?, rows_total = ?, "
            "rows_inserted = ?, rows_updated = ?, status = ? WHERE id = ?",
            (utcnow_iso(), total, inserted, updated, "ok", import_id),
        )
        log_activity(conn, abn=None, action="import",
                     detail=f"{csv_path.name}: {inserted}+{updated}/{total}")
        cur.execute("COMMIT")
    except Exception as exc:
        cur.execute("ROLLBACK")
        conn.execute(
            "UPDATE imports SET finished_at = ?, status = ? WHERE id = ?",
            (utcnow_iso(), f"failed: {exc!s:.200}", import_id),
        )
        raise

    return total, inserted, updated


def import_socials_if_present(conn: sqlite3.Connection) -> int:
    if not SOCIAL_CSV.exists():
        return 0
    n = 0
    cur = conn.cursor()
    cur.execute("BEGIN")
    try:
        for row in iter_csv_rows(SOCIAL_CSV):
            abn = (row.get("abn") or "").strip()
            if not abn:
                continue
            fb = (row.get("facebook") or "").strip() or None
            ig = (row.get("instagram") or "").strip() or None
            if not (fb or ig):
                continue
            cur.execute(
                "UPDATE leads SET facebook_url = COALESCE(?, facebook_url), "
                "instagram_url = COALESCE(?, instagram_url) WHERE abn = ?",
                (fb, ig, abn),
            )
            n += cur.rowcount
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    return n


# --- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Import ABR CSVs into outreach.db")
    p.add_argument("--csv", type=Path, help="Specific CSV to import "
                                            "(default: auto-discover).")
    p.add_argument("--db",  type=Path, default=default_db_path(),
                   help="SQLite database path.")
    p.add_argument("--no-socials", action="store_true",
                   help="Skip has_social.csv import even if present.")
    args = p.parse_args(argv)

    conn = connect(args.db)
    init_schema(conn)

    csvs = [args.csv] if args.csv else [
        p for p in CANDIDATE_CSVS if p.exists()
    ]
    if not csvs:
        print(f"No CSV found. Looked in: {[str(p) for p in CANDIDATE_CSVS]}",
              file=sys.stderr)
        return 1

    # prefer most-specific CSV (filtered_with_dns) — if multiple exist,
    # importing them in order is still idempotent because of UPSERT.
    print(f"DB: {args.db}")
    for csv_path in csvs:
        print(f"→ importing {csv_path.name} ...")
        total, ins, upd = upsert_leads(conn, csv_path)
        print(f"  total={total}  inserted={ins}  updated={upd}")

    if not args.no_socials:
        n = import_socials_if_present(conn)
        if n:
            print(f"→ socials updated: {n} rows")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
