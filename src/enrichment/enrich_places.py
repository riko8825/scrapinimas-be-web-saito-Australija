"""Stage A: Google Places API (New) Text Search enrichment.

Reads eligible ABNs from outreach.db (via filters.eligible_for_stage_a),
calls POST https://places.googleapis.com/v1/places:searchText for each,
UPSERTs trading_name / phone / website / place_id / address into
the `enrichment` table.

Pricing (Enterprise SKU because we request websiteUri + phone):
  - $35 per 1k calls (0-100k tier)
  - First 1k/month FREE

Cost cap enforced via budget.can_spend() before run.

Idempotency: stage_a_status field tracks per-ABN state. Re-run skips
'ok' rows. --retry-errors flag retries only stage_a_status='error' rows.

Run:
    python -m src.enrichment.enrich_places --limit 100 --dry-run
    python -m src.enrichment.enrich_places --limit 100 --live
    python -m src.enrichment.enrich_places --limit 1000 --live --concurrency 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.asyncio import tqdm as tqdm_async

# Project imports
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dashboard.db import connect, default_db_path, init_schema, utcnow_iso  # noqa: E402
from src.enrichment import budget, filters, scoring, validators  # noqa: E402


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
PLACES_ENDPOINT = os.getenv(
    "PLACES_ENDPOINT", "https://places.googleapis.com/v1/places:searchText"
).strip()
PLACES_CONCURRENCY = int(os.getenv("PLACES_CONCURRENCY", "5"))
PLACES_TIMEOUT = float(os.getenv("PLACES_TIMEOUT", "15.0"))
PLACES_REGION = os.getenv("PLACES_REGION", "AU").strip()
PLACES_MONTHLY_CAP_USD = float(os.getenv("PLACES_MONTHLY_CAP_USD", "50.0"))

RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))

# Per-call cost (Enterprise SKU @ 0-100k tier). Free tier'as ignoruojamas
# saugumui — geriau pertaupyti nei ban'us gauti.
COST_PER_CALL_USD = 0.035

# Žinoma response shape (Field Mask atitinka tai, ką prašom).
# V2-LITE P0.1: pridėti rating / userRatingCount / businessStatus / priceLevel
# pain-signal scoring'ui (sesija #9). Visi šie laukai yra TAME PAČIAME Enterprise
# SKU tier'e — papildomos kainos NĖRA.
FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.internationalPhoneNumber,"
    "places.websiteUri,"
    "places.types,"
    "places.rating,"
    "places.userRatingCount,"
    "places.businessStatus,"
    "places.priceLevel"
)

# Google enum -> int mapping priceLevel'iui (DB int saugom):
PRICE_LEVEL_MAP: dict[str, int] = {
    "PRICE_LEVEL_FREE": 0,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


# ---------------------------------------------------------------------------
# LOGGING — atskira info + error
# ---------------------------------------------------------------------------

logger = logging.getLogger("enrich_places")
logger.setLevel(logging.INFO)

_info_handler = logging.FileHandler(
    LOG_DIR / f"enrich_places_{datetime.now():%Y%m%d}.log", encoding="utf-8"
)
_info_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
_err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(
    logging.Formatter("%(asctime)s [enrich_places] %(levelname)s %(message)s")
)
if not logger.handlers:
    logger.addHandler(_info_handler)
    logger.addHandler(_err_handler)


# ---------------------------------------------------------------------------
# PLACES API CALL
# ---------------------------------------------------------------------------

async def _places_search(
    client: httpx.AsyncClient,
    business_name: str,
    postcode: str,
    api_key: str,
) -> dict[str, Any]:
    """One Text Search request. Raises httpx.HTTPError on transport failure.

    Returns parsed response dict (may be {} jei nieko nerasta — caller turi
    patikrint 'places' key).
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": f"{business_name} {postcode}".strip(),
        "regionCode": PLACES_REGION,
        "pageSize": 1,   # mums reikia tik top match
    }
    resp = await client.post(
        PLACES_ENDPOINT, headers=headers, json=body, timeout=PLACES_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def _pick_top_place(response: dict[str, Any]) -> dict[str, Any] | None:
    """Iš response'o paimk pirmą Place arba None jei tuščia."""
    places = response.get("places") or []
    return places[0] if places else None


def _extract_display_name(place: dict[str, Any]) -> str:
    """displayName yra dict {'text': ..., 'languageCode': ...} arba string."""
    dn = place.get("displayName")
    if isinstance(dn, dict):
        return (dn.get("text") or "").strip()
    return (dn or "").strip()


# ---------------------------------------------------------------------------
# ENRICHMENT (single ABN)
# ---------------------------------------------------------------------------

async def _enrich_one(
    client: httpx.AsyncClient,
    abn: str,
    business_name: str,
    postcode: str,
    api_key: str,
) -> dict[str, Any]:
    """Enrich one ABN. NEVER raises — returns dict su 'stage_a_status' field.

    Returns shape:
        {
            "abn": str,
            "stage_a_status": "ok" | "not_found" | "error",
            "stage_a_cost_usd": float,
            "place_id": str | None,
            "trading_name": str | None,
            "formatted_address": str | None,
            "phone": str | None,
            "website_url": str | None,
            "place_types": str | None,  # JSON-encoded list
            # V2-LITE P0.1 fields:
            "rating": float | None,
            "review_count": int | None,
            "business_status": str | None,   # OPERATIONAL | CLOSED_*
            "price_level": int | None,       # 0..4 arba None
            "error_detail": str,
        }
    """
    out: dict[str, Any] = {
        "abn": abn,
        "stage_a_status": "error",
        "stage_a_cost_usd": COST_PER_CALL_USD,   # bill'inam net jei fail (req nuėjo)
        "place_id": None,
        "trading_name": None,
        "formatted_address": None,
        "phone": None,
        "website_url": None,
        "place_types": None,
        "rating": None,
        "review_count": None,
        "business_status": None,
        "price_level": None,
        "au_validation_status": None,
        "au_validation_reason": None,
        "error_detail": "",
    }

    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, max=8),
            retry=retry_if_exception_type(
                (httpx.HTTPError, httpx.TimeoutException)
            ),
            reraise=True,
        ):
            with attempt:
                response = await _places_search(
                    client, business_name, postcode, api_key
                )
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        out["error_detail"] = f"{type(e).__name__}: {str(e)[:200]}"
        logger.warning("Places HTTP error for %s: %s", abn, out["error_detail"])
        return out
    except Exception as e:  # noqa: BLE001 — saugumas
        out["error_detail"] = f"unexpected: {type(e).__name__}: {str(e)[:200]}"
        logger.error("Places unexpected error for %s: %s", abn, out["error_detail"])
        return out

    place = _pick_top_place(response)
    if place is None:
        out["stage_a_status"] = "not_found"
        return out

    out["stage_a_status"] = "ok"
    out["place_id"] = place.get("id")
    out["trading_name"] = _extract_display_name(place)
    out["formatted_address"] = place.get("formattedAddress")
    out["phone"] = place.get("internationalPhoneNumber")
    out["website_url"] = place.get("websiteUri")
    types = place.get("types") or []
    out["place_types"] = json.dumps(types) if types else None

    # V2-LITE P0.1 — pain signals
    rating = place.get("rating")
    out["rating"] = float(rating) if isinstance(rating, (int, float)) else None
    rc = place.get("userRatingCount")
    out["review_count"] = int(rc) if isinstance(rc, (int, float)) else None
    bs = place.get("businessStatus")
    out["business_status"] = bs if isinstance(bs, str) and bs else None
    pl_raw = place.get("priceLevel")
    out["price_level"] = PRICE_LEVEL_MAP.get(pl_raw) if isinstance(pl_raw, str) else None

    # V2-LITE P0.2 — anti-PROXYTECH AU validation
    v = validators.validate_au(
        phone=out["phone"],
        website=out["website_url"],
        formatted_address=out["formatted_address"],
    )
    out["au_validation_status"] = v.status
    out["au_validation_reason"] = v.reason[:500] if v.reason else None
    if v.status == "not_au":
        logger.info(
            "AU validation: not_au for %s — %s", abn, v.reason
        )

    return out


# ---------------------------------------------------------------------------
# DRY-RUN MODE — print queries that WOULD be sent
# ---------------------------------------------------------------------------

def _dry_run_one(abn: str, business_name: str, postcode: str) -> dict[str, Any]:
    """Dry-run: nieko nesiunčia, returns simulated 'planned' status."""
    return {
        "abn": abn,
        "stage_a_status": "skipped",
        "stage_a_cost_usd": 0.0,
        "place_id": None,
        "trading_name": f"[DRY] would query: '{business_name} {postcode}'",
        "formatted_address": None,
        "phone": None,
        "website_url": None,
        "place_types": None,
        "rating": None,
        "review_count": None,
        "business_status": None,
        "price_level": None,
        "au_validation_status": None,
        "au_validation_reason": None,
        "error_detail": "",
    }


# ---------------------------------------------------------------------------
# DB PERSISTENCE
# ---------------------------------------------------------------------------

def _upsert_enrichment(conn, result: dict[str, Any], priority: int) -> None:
    """UPSERT enrichment row su Stage A duomenimis + priority score.

    V2-LITE P0.1: papildomi stulpeliai rating / review_count / business_status /
    price_level. Result dict turi turėti šiuos raktus (gali būti None).
    """
    now = utcnow_iso()
    conn.execute(
        """INSERT INTO enrichment (
                abn, stage_a_status, stage_a_attempted_at, stage_a_cost_usd,
                place_id, trading_name, formatted_address, phone, website_url,
                place_types, rating, review_count, business_status, price_level,
                au_validation_status, au_validation_reason,
                priority_score, updated_at
           )
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(abn) DO UPDATE SET
                stage_a_status      = excluded.stage_a_status,
                stage_a_attempted_at= excluded.stage_a_attempted_at,
                stage_a_cost_usd    = excluded.stage_a_cost_usd,
                place_id            = excluded.place_id,
                trading_name        = excluded.trading_name,
                formatted_address   = excluded.formatted_address,
                phone               = excluded.phone,
                website_url         = excluded.website_url,
                place_types         = excluded.place_types,
                rating              = excluded.rating,
                review_count        = excluded.review_count,
                business_status     = excluded.business_status,
                price_level         = excluded.price_level,
                au_validation_status= excluded.au_validation_status,
                au_validation_reason= excluded.au_validation_reason,
                priority_score      = excluded.priority_score,
                updated_at          = excluded.updated_at""",
        (
            result["abn"],
            result["stage_a_status"],
            now,
            result["stage_a_cost_usd"],
            result["place_id"],
            result["trading_name"],
            result["formatted_address"],
            result["phone"],
            result["website_url"],
            result["place_types"],
            result.get("rating"),
            result.get("review_count"),
            result.get("business_status"),
            result.get("price_level"),
            result.get("au_validation_status"),
            result.get("au_validation_reason"),
            priority,
            now,
        ),
    )


def _create_run(conn) -> int:
    """Start audit row, return run_id."""
    cur = conn.execute(
        "INSERT INTO enrichment_runs (stage, started_at, status) VALUES ('a', ?, 'running')",
        (utcnow_iso(),),
    )
    return cur.lastrowid


def _finish_run(conn, run_id: int, attempted: int, ok: int, error: int, cost: float, status: str) -> None:
    conn.execute(
        """UPDATE enrichment_runs
           SET finished_at = ?, count_attempted = ?, count_ok = ?,
               count_error = ?, cost_usd = ?, status = ?
           WHERE id = ?""",
        (utcnow_iso(), attempted, ok, error, cost, status, run_id),
    )


# ---------------------------------------------------------------------------
# BATCH ORCHESTRATION
# ---------------------------------------------------------------------------

async def _run_batch(
    conn,
    abns: list[str],
    api_key: str,
    concurrency: int,
    dry_run: bool,
) -> dict[str, int]:
    """Process all ABNs su bounded concurrency. Returns counters."""

    # Pre-fetch lead data for query building
    placeholders = ",".join("?" * len(abns))
    leads_rows = conn.execute(
        f"""SELECT abn, business_name, postcode, state, industry_keyword, gst_status
            FROM leads WHERE abn IN ({placeholders})""",
        abns,
    ).fetchall()
    leads_map = {r["abn"]: r for r in leads_rows}

    sem = asyncio.Semaphore(concurrency)
    counters = {"ok": 0, "not_found": 0, "error": 0, "skipped": 0, "cost": 0.0}

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=concurrency * 2)
    ) as client:

        async def _worker(abn: str) -> dict[str, Any]:
            async with sem:
                lead = leads_map.get(abn)
                if lead is None:
                    return {
                        "abn": abn, "stage_a_status": "error",
                        "stage_a_cost_usd": 0.0, "place_id": None,
                        "trading_name": None, "formatted_address": None,
                        "phone": None, "website_url": None, "place_types": None,
                        "error_detail": "lead not found in leads table",
                    }
                if dry_run:
                    return _dry_run_one(abn, lead["business_name"], lead["postcode"])
                return await _enrich_one(
                    client, abn, lead["business_name"], lead["postcode"], api_key
                )

        tasks = [_worker(abn) for abn in abns]
        for coro in tqdm_async.as_completed(tasks, total=len(tasks), desc="Places"):
            result = await coro
            lead = leads_map.get(result["abn"])
            priority = scoring.priority_score(
                lead["industry_keyword"] if lead else None,
                lead["state"] if lead else None,
                lead["business_name"] if lead else None,
                lead["gst_status"] if lead else None,
            )
            _upsert_enrichment(conn, result, priority)
            counters[result["stage_a_status"]] = counters.get(result["stage_a_status"], 0) + 1
            counters["cost"] += float(result["stage_a_cost_usd"])

    return counters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _onedrive_db_fallback() -> Path:
    """If dashboard/outreach.db missing, fallback to OneDrive path."""
    local = default_db_path()
    if local.exists():
        return local
    onedrive = Path(
        r"C:\Users\pinig\OneDrive\Stalinis kompiuteris\Automatiomm_empirra"
        r"\abr-data\abr-pipeline\dashboard\outreach.db"
    )
    if onedrive.exists():
        return onedrive
    return local  # let connect() raise meaningful error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage A: Google Places API enrichment for AU SMB leads."
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max ABNs per run (default: 100). Use 1000 for smoke after key validated.",
    )
    parser.add_argument(
        "--concurrency", type=int, default=PLACES_CONCURRENCY,
        help=f"Max concurrent API calls (default: {PLACES_CONCURRENCY})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't hit API — print queries that WOULD be sent.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Confirm live API calls + billing. Required to override default dry-run safety.",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to outreach.db (default: dashboard/outreach.db or OneDrive)",
    )
    parser.add_argument(
        "--industries", type=str, default=None,
        help="Comma-separated industries filter (default: full whitelist)",
    )
    parser.add_argument(
        "--states", type=str, default=None,
        help="Comma-separated states filter (default: all 8 AU)",
    )
    args = parser.parse_args(argv)

    # Safety: dry_run is default. --live must be explicit.
    if not args.dry_run and not args.live:
        print(
            "ERROR: must pass either --dry-run (safe) or --live (billable).\n"
            "Tip: start with --dry-run to validate filters, then --live --limit 100 "
            "for first real smoke (still FREE if first 1k of month).",
            file=sys.stderr,
        )
        return 1

    if args.live and not PLACES_API_KEY:
        print(
            "ERROR: GOOGLE_PLACES_API_KEY not set in .env.\n"
            "Setup: GCP Console -> APIs Library -> 'Places API (New)' -> Enable\n"
            "       -> Credentials -> Create API key -> paste into .env",
            file=sys.stderr,
        )
        return 1

    db_path = args.db or _onedrive_db_fallback()
    if not db_path.exists():
        print(f"ERROR: outreach.db not found at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    init_schema(conn)   # ensures enrichment table exists

    industries = tuple(s.strip() for s in args.industries.split(",")) if args.industries else None
    states = tuple(s.strip().upper() for s in args.states.split(",")) if args.states else None

    abns = filters.eligible_for_stage_a(
        conn, limit=args.limit, industries=industries, states=states
    )

    if not abns:
        print(
            "Nothing to enrich — no eligible leads. Possible reasons:\n"
            "  - All matching leads already have stage_a_status set\n"
            "  - Filters too narrow (--industries / --states)\n"
            "  - leads table empty"
        )
        return 0

    # Budget guard (only for --live)
    if args.live:
        estimated_cost = budget.estimate_stage_cost("a", len(abns))
        allowed, current, cap = budget.can_spend(
            conn, "a", estimated_cost, monthly_cap_usd=PLACES_MONTHLY_CAP_USD
        )
        if not allowed:
            print(
                f"ERROR: budget cap exceeded.\n"
                f"  Current month-to-date Stage A spend: ${current:.2f}\n"
                f"  Estimated this run: ${estimated_cost:.2f}\n"
                f"  Cap: ${cap:.2f}\n"
                f"  Override: set PLACES_MONTHLY_CAP_USD in .env or use --limit smaller",
                file=sys.stderr,
            )
            return 1
        print(
            f"Budget check OK: ${current:.2f} spent + ${estimated_cost:.2f} estimated "
            f"<= ${cap:.2f} cap"
        )

    print(
        f"{'[DRY RUN] ' if args.dry_run else ''}Stage A: {len(abns)} eligible ABNs, "
        f"concurrency={args.concurrency}"
    )

    run_id = _create_run(conn)
    started = time.time()
    try:
        counters = asyncio.run(_run_batch(
            conn, abns, PLACES_API_KEY, args.concurrency, args.dry_run
        ))
        status = "ok"
    except Exception as e:  # noqa: BLE001
        logger.error("Batch crashed: %s", e)
        counters = {"ok": 0, "not_found": 0, "error": 0, "skipped": 0, "cost": 0.0}
        status = "failed"
        raise
    finally:
        _finish_run(
            conn, run_id,
            attempted=sum(counters.get(k, 0) for k in ("ok", "not_found", "error", "skipped")),
            ok=counters.get("ok", 0),
            error=counters.get("error", 0),
            cost=counters.get("cost", 0.0),
            status=status,
        )

    elapsed = time.time() - started
    n_ok = counters.get("ok", 0)
    n_total = sum(counters.get(k, 0) for k in ("ok", "not_found", "error"))
    hit_rate = (n_ok / n_total * 100) if n_total else 0

    print(
        f"\nDone in {elapsed:.1f}s.\n"
        f"  OK:        {counters.get('ok', 0)}\n"
        f"  Not found: {counters.get('not_found', 0)}\n"
        f"  Error:     {counters.get('error', 0)}\n"
        f"  Skipped:   {counters.get('skipped', 0)}\n"
        f"  Hit rate:  {hit_rate:.1f}%\n"
        f"  Cost:      ${counters.get('cost', 0):.4f}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
