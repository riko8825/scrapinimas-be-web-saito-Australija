"""DNS check stage — find businesses WITHOUT a website.

Reads  ./output/filtered_businesses.csv  (produced by abr_parser.py)
Writes ./output/has_website.csv          (businesses we SKIP)
       ./output/no_website.csv           (businesses we'll outreach to)

Per business we build up to 5 candidate domains from the
`name_normalized` column:

    base = name_normalized stripped of spaces
        -> {base}.com.au   {base}.com   {base}.au

    hyphen = name_normalized with spaces -> '-'
        -> {hyphen}.com.au   {hyphen}.com

For each candidate we ask dnspython for an A record first; if that
fails (NXDOMAIN, NoAnswer, timeout) we try CNAME. If ANY candidate
resolves, has_domain = True and we stop checking further candidates
for that business.

Concurrency:
    asyncio.Semaphore(100) — at most 100 in-flight business checks at
    a time. Within one business, candidates are tried sequentially so
    a hit on candidate #1 skips #2..#5.

Timeout:
    3 seconds per individual DNS query (lifetime). Failure of any
    single query is silently treated as "doesn't resolve" — we never
    raise into the pipeline.

Progress:
    tqdm bar shows live "Has domain: N | No domain: M" running counts.

Run:
    python check_dns.py
    python check_dns.py --limit 200
    python check_dns.py -i ./output/filtered_businesses.csv \\
                       --has ./output/has_website.csv \\
                       --no  ./output/no_website.csv
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import dns.asyncresolver
import dns.exception
import dns.resolver
import pandas as pd
from tqdm import tqdm


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_INPUT = OUTPUT_DIR / "filtered_businesses.csv"
DEFAULT_HAS = OUTPUT_DIR / "has_website.csv"
DEFAULT_NO = OUTPUT_DIR / "no_website.csv"

DNS_TIMEOUT = 3.0       # seconds per individual query
DNS_CONCURRENCY = 100   # max in-flight business checks
MAX_CANDIDATES = 5      # cap per business

# Public DNS servers. Windows auto-discovery is unreliable under async
# resolvers, so we pin Cloudflare + Google explicitly.
NAMESERVERS = ["1.1.1.1", "8.8.8.8", "1.0.0.1", "8.8.4.4"]

# Errors-only log (does not affect the user-facing console).
logger = logging.getLogger("check_dns")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    fh.setLevel(logging.WARNING)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | check_dns | %(message)s"
    ))
    logger.addHandler(fh)


# ---------------------------------------------------------------------------
# DOMAIN GENERATION
# ---------------------------------------------------------------------------

def generate_domains(name_normalized: str) -> list[str]:
    """Build up to MAX_CANDIDATES domain candidates from a normalized name.

    name_normalized is expected to be lowercase, whitespace-separated
    tokens with legal suffixes already stripped (e.g. 'acme cleaning').

    Strategy:
        joined  = tokens joined with no separator -> .com.au .com .au
        hyphen  = tokens joined with '-'          -> .com.au .com
                  (skipped if only one token)

    Args:
        name_normalized: pre-normalized business name.

    Returns:
        Ordered, de-duplicated list of candidate domain strings. Empty
        if `name_normalized` yields no usable slug.
    """
    if not name_normalized:
        return []

    # Defensive: drop anything that isn't a-z, 0-9, space, hyphen.
    cleaned = "".join(
        ch for ch in name_normalized.lower()
        if ch.isalnum() or ch in (" ", "-")
    )
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return []

    joined = "".join(tokens)
    hyphen = "-".join(tokens) if len(tokens) > 1 else ""

    # Sanity bounds — DNS labels have a 63-char limit.
    if len(joined) < 2 or len(joined) > 63:
        return []

    candidates: list[str] = [
        f"{joined}.com.au",
        f"{joined}.com",
        f"{joined}.au",
    ]
    if hyphen and 2 <= len(hyphen) <= 63:
        candidates += [f"{hyphen}.com.au", f"{hyphen}.com"]

    # Preserve order, drop dupes, cap at MAX_CANDIDATES.
    seen: set[str] = set()
    out: list[str] = []
    for d in candidates:
        if d not in seen:
            seen.add(d)
            out.append(d)
        if len(out) >= MAX_CANDIDATES:
            break
    return out


# ---------------------------------------------------------------------------
# DNS RESOLUTION
# ---------------------------------------------------------------------------

def _make_resolver() -> dns.asyncresolver.Resolver:
    """Create a pinned-nameserver async resolver with the right timeouts."""
    r = dns.asyncresolver.Resolver(configure=False)
    r.nameservers = NAMESERVERS
    r.timeout = DNS_TIMEOUT
    r.lifetime = DNS_TIMEOUT
    return r


# Errors that just mean "this name doesn't resolve" — we never raise.
_NEG_ERRORS = (
    dns.resolver.NXDOMAIN,
    dns.resolver.NoAnswer,
    dns.resolver.NoNameservers,
    dns.exception.Timeout,
)


async def _resolves(resolver: dns.asyncresolver.Resolver, domain: str) -> bool:
    """Return True if `domain` has an A record (CNAME if A missing).

    Per CLAUDE.md: any DNS failure is silently 'doesn't resolve' — a
    bad domain must never crash the pipeline.
    """
    # A record first — most domains have one and this is what we want
    # in practice ('does the site exist?').
    try:
        answers = await resolver.resolve(domain, "A")
        if len(answers) > 0:
            return True
    except _NEG_ERRORS:
        pass
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.debug("A %s: %s", domain, exc)

    # CNAME fallback — some Aussie SMB domains only have a CNAME pointing
    # at a hosting provider, no apex A. Still counts as "has a website".
    try:
        answers = await resolver.resolve(domain, "CNAME")
        if len(answers) > 0:
            return True
    except _NEG_ERRORS:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("CNAME %s: %s", domain, exc)

    return False


async def _check_business(
    resolver: dns.asyncresolver.Resolver,
    sem: asyncio.Semaphore,
    idx: int,
    name_normalized: str,
) -> tuple[int, bool, str]:
    """Resolve domain candidates for one business. Never raises.

    Returns:
        (idx, has_domain, found_domain). `found_domain` is "" if none
        of the candidates resolved.
    """
    try:
        candidates = generate_domains(name_normalized)
        if not candidates:
            return idx, False, ""

        async with sem:
            for cand in candidates:
                if await _resolves(resolver, cand):
                    return idx, True, cand
        return idx, False, ""
    except Exception as exc:  # noqa: BLE001 — log + skip, never crash
        logger.error("row %d (%r): %s", idx, name_normalized, exc)
        return idx, False, ""


async def check_all(
    name_normalized_col: list[str],
    concurrency: int = DNS_CONCURRENCY,
) -> list[tuple[bool, str]]:
    """Run DNS checks for every business, in concurrent batches.

    Args:
        name_normalized_col: list of pre-normalized business names.
        concurrency: max in-flight checks (asyncio.Semaphore size).

    Returns:
        List of (has_domain, found_domain) aligned 1:1 with input. The
        tqdm bar shows live counts in its postfix.
    """
    resolver = _make_resolver()
    sem = asyncio.Semaphore(concurrency)

    has_domain: list[bool] = [False] * len(name_normalized_col)
    found_domain: list[str] = [""] * len(name_normalized_col)

    tasks = [
        asyncio.create_task(_check_business(resolver, sem, i, n))
        for i, n in enumerate(name_normalized_col)
    ]

    n_has = 0
    n_no = 0
    # We drive a real tqdm bar by hand over asyncio.as_completed so we
    # can update the postfix on every completion. tqdm.asyncio.as_completed
    # returns a generator, not a tqdm instance, so set_postfix isn't
    # reachable through it.
    bar = tqdm(
        total=len(tasks),
        desc="Checked",
        unit=" biz",
        mininterval=0.3,
    )
    try:
        for fut in asyncio.as_completed(tasks):
            idx, ok, dom = await fut
            has_domain[idx] = ok
            found_domain[idx] = dom
            if ok:
                n_has += 1
            else:
                n_no += 1
            bar.update(1)
            bar.set_postfix_str(f"Has domain: {n_has} | No domain: {n_no}")
    finally:
        bar.close()

    return list(zip(has_domain, found_domain))


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def _resolve_name_column(df: pd.DataFrame) -> str:
    """Return the column to use for normalized names.

    Prefer `name_normalized` (produced by the updated abr_parser). Fall
    back to `business_name` if it's missing, so older CSVs still work.
    Errors loudly if neither column is present.
    """
    if "name_normalized" in df.columns:
        return "name_normalized"
    if "business_name" in df.columns:
        print(
            "  WARNING: input CSV has no 'name_normalized' column. Falling "
            "back to 'business_name' — domains will be less accurate. "
            "Re-run abr_parser.py with the updated version to fix.",
            file=sys.stderr,
        )
        return "business_name"
    raise SystemExit(
        "ERROR: input CSV must have either 'name_normalized' or 'business_name'."
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns an exit code."""
    ap = argparse.ArgumentParser(
        description="Find businesses without a website (DNS check stage)."
    )
    ap.add_argument(
        "--input", "-i",
        type=Path, default=DEFAULT_INPUT,
        help=f"Input CSV (default: {DEFAULT_INPUT.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--has",
        type=Path, default=DEFAULT_HAS,
        help=f"Output for businesses WITH a website "
             f"(default: {DEFAULT_HAS.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--no",
        dest="no_path",
        type=Path, default=DEFAULT_NO,
        help=f"Output for businesses WITHOUT a website "
             f"(default: {DEFAULT_NO.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--limit", type=int, default=0,
        help="Only check the first N rows (0 = all). Use 200 for a quick test.",
    )
    ap.add_argument(
        "--concurrency", "-c", type=int, default=DNS_CONCURRENCY,
        help=f"Max concurrent business checks (default: {DNS_CONCURRENCY})",
    )
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input CSV not found: {args.input}", file=sys.stderr)
        print(f"       Run abr_parser.py first to produce it.", file=sys.stderr)
        return 2

    print(f"Reading {args.input} ...")
    df = pd.read_csv(args.input, dtype=str, keep_default_na=False, encoding="utf-8")
    name_col = _resolve_name_column(df)

    if args.limit and args.limit < len(df):
        print(f"Limiting to first {args.limit:,} rows (of {len(df):,})")
        df = df.head(args.limit).copy()

    print(f"Checking DNS for {len(df):,} businesses "
          f"(concurrency={args.concurrency}, timeout={DNS_TIMEOUT}s) ...\n")

    t0 = time.perf_counter()
    results = asyncio.run(check_all(df[name_col].tolist(), args.concurrency))
    elapsed = time.perf_counter() - t0

    df["has_domain"] = [r[0] for r in results]
    df["found_domain"] = [r[1] for r in results]

    has_df = df[df["has_domain"]].copy()
    no_df = df[~df["has_domain"]].copy()

    args.has.parent.mkdir(parents=True, exist_ok=True)
    args.no_path.parent.mkdir(parents=True, exist_ok=True)
    has_df.to_csv(args.has, index=False, encoding="utf-8")
    no_df.to_csv(args.no_path, index=False, encoding="utf-8")

    total = len(df)
    n_has = len(has_df)
    n_no = len(no_df)
    rate = total / elapsed if elapsed else 0.0

    print()
    print("=" * 60)
    print(f"Total checked  : {total:,}")
    if total:
        print(f"Has domain     : {n_has:,}  ({n_has / total * 100:.1f}%)")
        print(f"No domain      : {n_no:,}  ({n_no / total * 100:.1f}%)")
    print(f"Time taken     : {elapsed:.1f}s  ({rate:.0f} biz/s)")
    print("=" * 60)
    print(f"  -> {args.has}    ({n_has:,} rows)")
    print(f"  -> {args.no_path}     ({n_no:,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
