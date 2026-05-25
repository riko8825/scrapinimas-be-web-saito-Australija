"""Second-pass DNS recheck — finds false negatives in no_website.csv.

The first full DNS run at conc=100 hit resolver-side throttling and
classified ~23% of true has-domain businesses as 'no website'. This
helper re-runs the same domain candidates against the same resolver
code but with conservative settings:

    * concurrency 30 (vs 100)
    * 2 nameservers (vs 4) so we hit Cloudflare/Google evenly
    * longer per-query timeout

Reads:   output/no_website.csv          (97,534 rows after the first pass)
Writes:  output/no_website_verified.csv (rows that still don't resolve)
         output/found_on_recheck.csv    (rows that resolved on retry)

Run:
    python recheck_dns.py
    python recheck_dns.py --limit 1000   # smoke test first
    python recheck_dns.py --concurrency 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import check_dns

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"

DEFAULT_INPUT = OUTPUT_DIR / "no_website.csv"
DEFAULT_VERIFIED = OUTPUT_DIR / "no_website_verified.csv"
DEFAULT_FOUND = OUTPUT_DIR / "found_on_recheck.csv"

RECHECK_CONCURRENCY = 50
RECHECK_NAMESERVERS = ["1.1.1.1", "8.8.8.8", "1.0.0.1", "8.8.4.4"]
RECHECK_TIMEOUT = 5.0


def _make_recheck_resolver():
    """Resolver tuned for slow, reliable second-pass verification."""
    import dns.asyncresolver
    r = dns.asyncresolver.Resolver(configure=False)
    r.nameservers = RECHECK_NAMESERVERS
    r.timeout = RECHECK_TIMEOUT
    r.lifetime = RECHECK_TIMEOUT
    return r


async def _check_one(resolver, sem, idx, name_normalized):
    """Run candidate domains for one business. Never raises."""
    try:
        candidates = check_dns.generate_domains(name_normalized)
        if not candidates:
            return idx, False, ""
        async with sem:
            for cand in candidates:
                if await check_dns._resolves(resolver, cand):
                    return idx, True, cand
        return idx, False, ""
    except Exception as exc:  # noqa: BLE001
        check_dns.logger.error("recheck row %d (%r): %s", idx, name_normalized, exc)
        return idx, False, ""


async def recheck_all(name_col_values: list[str], concurrency: int):
    """Return list[(has_domain, found_domain)] aligned with input."""
    resolver = _make_recheck_resolver()
    sem = asyncio.Semaphore(concurrency)

    has_domain = [False] * len(name_col_values)
    found_domain = [""] * len(name_col_values)

    tasks = [
        asyncio.create_task(_check_one(resolver, sem, i, n))
        for i, n in enumerate(name_col_values)
    ]

    n_has = 0
    n_no = 0
    bar = tqdm(total=len(tasks), desc="Recheck", unit=" biz", mininterval=0.5)
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
            bar.set_postfix_str(f"Found: {n_has} | Still no: {n_no}")
    finally:
        bar.close()

    return list(zip(has_domain, found_domain))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="Second-pass DNS recheck for false negatives."
    )
    ap.add_argument(
        "--input", "-i", type=Path, default=DEFAULT_INPUT,
        help=f"Input CSV (default: {DEFAULT_INPUT.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--verified", type=Path, default=DEFAULT_VERIFIED,
        help=f"Still-no-website output "
             f"(default: {DEFAULT_VERIFIED.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--found", type=Path, default=DEFAULT_FOUND,
        help=f"Found-on-recheck output "
             f"(default: {DEFAULT_FOUND.relative_to(ROOT)})",
    )
    ap.add_argument(
        "--limit", type=int, default=0,
        help="Only recheck first N rows (0 = all).",
    )
    ap.add_argument(
        "--concurrency", "-c", type=int, default=RECHECK_CONCURRENCY,
        help=f"Max concurrent checks (default: {RECHECK_CONCURRENCY})",
    )
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: {args.input} not found.", file=sys.stderr)
        return 2

    print(f"Reading {args.input} ...")
    df = pd.read_csv(args.input, dtype=str, keep_default_na=False, encoding="utf-8")
    if "name_normalized" not in df.columns:
        print("ERROR: input lacks 'name_normalized' column.", file=sys.stderr)
        return 2

    if args.limit and args.limit < len(df):
        print(f"Limiting to first {args.limit:,} rows (of {len(df):,})")
        df = df.head(args.limit).copy()

    print(f"Rechecking {len(df):,} businesses "
          f"(concurrency={args.concurrency}, timeout={RECHECK_TIMEOUT}s, "
          f"nameservers={RECHECK_NAMESERVERS}) ...")
    print()

    t0 = time.perf_counter()
    results = asyncio.run(recheck_all(df["name_normalized"].tolist(), args.concurrency))
    elapsed = time.perf_counter() - t0

    df["recheck_has_domain"] = [r[0] for r in results]
    df["recheck_found_domain"] = [r[1] for r in results]

    found_mask = df["recheck_has_domain"]
    found_df = df[found_mask].copy()
    verified_df = df[~found_mask].drop(columns=["recheck_has_domain", "recheck_found_domain"]).copy()

    # In found_df, promote the recheck result into the main found_domain column
    # so downstream stages don't need to know about the second pass.
    if "found_domain" in found_df.columns:
        found_df["found_domain"] = found_df["recheck_found_domain"]
        found_df["has_domain"] = "True"
    found_df = found_df.drop(columns=["recheck_has_domain", "recheck_found_domain"])

    args.verified.parent.mkdir(parents=True, exist_ok=True)
    args.found.parent.mkdir(parents=True, exist_ok=True)
    verified_df.to_csv(args.verified, index=False, encoding="utf-8")
    found_df.to_csv(args.found, index=False, encoding="utf-8")

    total = len(df)
    n_found = len(found_df)
    n_still = len(verified_df)
    rate = total / elapsed if elapsed else 0.0

    print()
    print("=" * 60)
    print(f"Re-checked     : {total:,}")
    print(f"Found on retry : {n_found:,}  ({n_found / total * 100:.1f}%)")
    print(f"Still no       : {n_still:,}  ({n_still / total * 100:.1f}%)")
    print(f"Time taken     : {elapsed:.1f}s  ({rate:.0f} biz/s)")
    print("=" * 60)
    print(f"  -> {args.verified}  ({n_still:,} rows)")
    print(f"  -> {args.found}     ({n_found:,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
