"""ABR Lookup API enrichment — Plan A.

Reads  ./output/no_website.csv         (produced by check_dns.py)
Writes ./output/filtered_with_trading_name.csv

Per ABN we call the public ABR Lookup JSONP endpoint and extract:
    - main_entity_name   (EntityName — official registered name)
    - trading_name       (best business name from BusinessName[] array, if any)
    - business_names     (all alternative names, joined with " | ")

The JSONP wrapper is stripped before json.loads. Response shape:

    callback({
      "Abn": "51824753556",
      "AbnStatus": "Active",
      "BusinessName": ["Foo Trading", "Foo Pty Ltd"],
      "EntityName": "FOO PTY LTD",
      ...
    })

Plan A goal: feed trading_name (not raw business_name) to find_social.py
so Brave query has a far higher chance of matching the public-facing
FB/IG handle. Sesijos #5 V2 baseline hit-rate = 5% on business_name;
target after this enrichment = ≥15% on trading_name.

Auth:
    Requires ABR_GUID env var. Register at
    https://abr.business.gov.au/Tools/WebServicesAgreement

Concurrency:
    asyncio.Semaphore(ABR_CONCURRENCY) — conservative default 5 to
    respect ABR ToS ("politeness", no published rate limit but we don't
    want to be the noisy neighbour that gets the endpoint locked down).

Retries:
    tenacity exponential backoff 1s, 2s, 4s for transient HTTP failures.
    Per-ABN failure is logged + skipped, NEVER raised into the pipeline.

Resumable:
    If output CSV exists, ABNs already present are skipped. Safe to ^C
    and re-run.

Run:
    python enrich_abr.py
    python enrich_abr.py --limit 100
    python enrich_abr.py -i ./output/no_website.csv \\
                         -o ./output/filtered_with_trading_name.csv
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm.asyncio import tqdm as tqdm_async


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

ABR_GUID = os.getenv("ABR_GUID", "").strip()
ABR_ENDPOINT = os.getenv(
    "ABR_ENDPOINT", "https://abr.business.gov.au/json/AbnDetails.aspx"
).strip()
ABR_CONCURRENCY = int(os.getenv("ABR_CONCURRENCY", "5"))
ABR_TIMEOUT = float(os.getenv("ABR_TIMEOUT", "10.0"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_MIN = float(os.getenv("RETRY_BACKOFF_MIN", "1"))
RETRY_BACKOFF_MAX = float(os.getenv("RETRY_BACKOFF_MAX", "8"))

DEFAULT_INPUT = OUTPUT_DIR / "no_website.csv"
DEFAULT_OUTPUT = OUTPUT_DIR / os.getenv(
    "ABR_ENRICHED_CSV", "filtered_with_trading_name.csv"
)

# Errors-only log
logger = logging.getLogger("enrich_abr")
logger.setLevel(logging.INFO)
_err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(
    logging.Formatter("%(asctime)s [enrich_abr] %(levelname)s %(message)s")
)
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    logger.addHandler(_err_handler)


# ---------------------------------------------------------------------------
# JSONP UNWRAP
# ---------------------------------------------------------------------------

_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.DOTALL)


def _strip_jsonp(body: str) -> dict[str, Any]:
    """Unwrap `callback({...});` JSONP envelope to a plain dict.

    Raises ValueError if the body doesn't match the expected envelope or
    the inner payload isn't valid JSON.
    """
    match = _JSONP_RE.match(body.strip())
    if not match:
        raise ValueError(f"Unexpected JSONP envelope: {body[:120]!r}")
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# BUSINESS NAME RANKING
# ---------------------------------------------------------------------------

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(pty\s*ltd|proprietary\s*limited|pty|ltd|limited|inc|incorporated)\b",
    re.IGNORECASE,
)


def _looks_like_trading_name(name: str) -> bool:
    """Return True if a name reads as a public-facing trading brand.

    A trading name is preferred over the legal entity name because it's
    what shows on the shopfront, social profile, or website. Heuristic:
    not empty, no legal suffix (pty ltd / limited / inc), at least 3
    chars.
    """
    if not name or len(name) < 3:
        return False
    return not _LEGAL_SUFFIX_RE.search(name)


def _pick_trading_name(entity_name: str, business_names: list[str]) -> str:
    """Pick the best public-facing trading name from ABR response.

    Priority:
        1. First business_name that doesn't look like a legal entity
        2. First business_name as-is (even if it has legal suffix)
        3. Empty string (caller falls back to entity_name)
    """
    names = [str(n).strip() for n in business_names if str(n).strip()]
    for n in names:
        if _looks_like_trading_name(n):
            return n
    return names[0] if names else ""


# ---------------------------------------------------------------------------
# ABR LOOKUP — single ABN
# ---------------------------------------------------------------------------

class AbrLookupError(Exception):
    """Wrapped error from ABR endpoint (HTTP, parse, or business-level)."""


async def _lookup_abn(
    client: httpx.AsyncClient,
    abn: str,
    guid: str,
) -> dict[str, Any]:
    """Fetch + parse a single ABN. Returns dict with entity/trading/all names.

    Returns shape:
        {
            "abn": "51824753556",
            "main_entity_name": "FOO PTY LTD",
            "trading_name": "Foo Trading",
            "business_names": "Foo Trading | Foo Pty Ltd",
            "abr_status": "Active",
            "abr_error": "",  # populated on per-record failure
        }

    Network/parse failures are logged + returned with abr_error set.
    Never raises into caller.
    """
    out: dict[str, Any] = {
        "abn": abn,
        "main_entity_name": "",
        "trading_name": "",
        "business_names": "",
        "abr_status": "",
        "abr_error": "",
    }

    params = {"abn": abn, "guid": guid, "callback": "cb"}
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(RETRY_ATTEMPTS),
            wait=wait_exponential(
                multiplier=RETRY_BACKOFF_MIN, max=RETRY_BACKOFF_MAX
            ),
            retry=retry_if_exception_type(
                (httpx.HTTPError, httpx.TimeoutException)
            ),
            reraise=True,
        ):
            with attempt:
                resp = await client.get(
                    ABR_ENDPOINT, params=params, timeout=ABR_TIMEOUT
                )
                resp.raise_for_status()
                payload = _strip_jsonp(resp.text)
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
        out["abr_error"] = f"{type(e).__name__}: {e}"
        logger.warning("ABR lookup failed for %s: %s", abn, out["abr_error"])
        return out

    # Business-level error (bad GUID, ABN not found, etc.)
    if payload.get("Message"):
        out["abr_error"] = payload["Message"]
        logger.warning("ABR business error for %s: %s", abn, payload["Message"])
        return out

    out["main_entity_name"] = (payload.get("EntityName") or "").strip()
    out["abr_status"] = (payload.get("AbnStatus") or "").strip()

    raw_names = payload.get("BusinessName") or []
    if isinstance(raw_names, str):
        raw_names = [raw_names]
    out["trading_name"] = _pick_trading_name(out["main_entity_name"], raw_names)
    out["business_names"] = " | ".join(
        str(n).strip() for n in raw_names if str(n).strip()
    )
    return out


# ---------------------------------------------------------------------------
# BATCH ORCHESTRATION
# ---------------------------------------------------------------------------

async def _enrich_all(
    abns: list[str],
    guid: str,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Enrich every ABN with bounded concurrency. Returns one result per ABN."""
    sem = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=concurrency * 2)
    ) as client:

        async def _worker(abn: str) -> dict[str, Any]:
            async with sem:
                return await _lookup_abn(client, abn, guid)

        tasks = [_worker(abn) for abn in abns]
        for coro in tqdm_async.as_completed(
            tasks, total=len(tasks), desc="ABR lookup"
        ):
            results.append(await coro)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_existing(out_path: Path) -> set[str]:
    """Return ABNs already present in out_path (for resume)."""
    if not out_path.exists():
        return set()
    try:
        df = pd.read_csv(out_path, dtype={"abn": str}, usecols=["abn"])
        return set(df["abn"].dropna().astype(str))
    except (ValueError, KeyError, pd.errors.EmptyDataError):
        return set()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns exit code 0 on success, 1 on systemic failure."""
    parser = argparse.ArgumentParser(
        description="Enrich ABNs with trading_name via ABR Lookup API.",
    )
    parser.add_argument(
        "-i", "--input", type=Path, default=DEFAULT_INPUT,
        help=f"Input CSV with abn column (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Enriched output CSV (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Process only N ABNs (0 = all). Useful for smoke tests.",
    )
    parser.add_argument(
        "--concurrency", type=int, default=ABR_CONCURRENCY,
        help=f"Max concurrent lookups (default: {ABR_CONCURRENCY})",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Re-fetch ABNs already in output CSV (default: skip them).",
    )
    args = parser.parse_args(argv)

    if not ABR_GUID:
        print(
            "ERROR: ABR_GUID not set. Register at "
            "https://abr.business.gov.au/Tools/WebServicesAgreement, "
            "then put GUID into .env.",
            file=sys.stderr,
        )
        return 1

    if not args.input.exists():
        print(f"ERROR: input CSV not found: {args.input}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.input, dtype={"abn": str})
    if "abn" not in df.columns:
        print(f"ERROR: input CSV missing 'abn' column: {args.input}", file=sys.stderr)
        return 1

    df["abn"] = df["abn"].astype(str).str.strip()
    df = df[df["abn"].str.len() == 11].copy()

    already = set() if args.no_resume else _load_existing(args.output)
    pending = df[~df["abn"].isin(already)]

    if args.limit:
        pending = pending.head(args.limit)

    if pending.empty:
        print(f"Nothing to enrich (already done: {len(already)}).")
        return 0

    abns = pending["abn"].tolist()
    print(
        f"Enriching {len(abns)} ABNs "
        f"(skipped {len(already)} already done, concurrency={args.concurrency})"
    )

    started = time.time()
    results = asyncio.run(_enrich_all(abns, ABR_GUID, args.concurrency))
    elapsed = time.time() - started

    enriched_df = pd.DataFrame(results)
    out_df = pending.merge(enriched_df, on="abn", how="left")

    if args.output.exists() and not args.no_resume:
        prev = pd.read_csv(args.output, dtype={"abn": str})
        out_df = pd.concat([prev, out_df], ignore_index=True)
        out_df = out_df.drop_duplicates(subset=["abn"], keep="last")

    out_df.to_csv(args.output, index=False)

    n_ok = sum(1 for r in results if not r["abr_error"])
    n_trade = sum(1 for r in results if r["trading_name"])
    print(
        f"Done in {elapsed:.1f}s. "
        f"OK: {n_ok}/{len(results)}. "
        f"trading_name populated: {n_trade}/{len(results)} "
        f"({n_trade / max(len(results), 1):.0%}). "
        f"Output: {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
