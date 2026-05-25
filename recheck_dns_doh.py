"""DoH (DNS-over-HTTPS) second-pass recheck — bypasses UDP rate-limits.

The UDP-based dnspython resolver at conc=50 plateaus at ~6 biz/s due to
public resolver rate-limiting per source IP (NoNameservers / SERVFAIL
when bursts exceed ~100 qps).

DoH solves this — Cloudflare/Google's HTTPS endpoints accept hundreds of
qps per source IP, are TCP-based, and trivially scale with httpx's
connection pool.

We rotate between two endpoints to halve per-host load:
    * https://1.1.1.1/dns-query                  (Cloudflare)
    * https://dns.google/resolve                 (Google)

Both speak the JSON DoH variant (RFC 8484 JSON), which is simpler than
the wire-format POST and avoids needing a DNS message builder.

Reads:   output/no_website.csv
Writes:  output/no_website_verified.csv  (still don't resolve)
         output/found_on_recheck.csv     (resolved on retry)
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import sys
import time
from pathlib import Path

import httpx
import pandas as pd
from tqdm import tqdm

import check_dns

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"

DEFAULT_INPUT = OUTPUT_DIR / "no_website.csv"
DEFAULT_VERIFIED = OUTPUT_DIR / "no_website_verified.csv"
DEFAULT_FOUND = OUTPUT_DIR / "found_on_recheck.csv"

DOH_ENDPOINTS = (
    "https://1.1.1.1/dns-query",
    "https://dns.google/resolve",
)

DOH_CONCURRENCY = 100
DOH_TIMEOUT = 5.0


_DOH_HEADERS = {"accept": "application/dns-json"}


async def _doh_query(
    client: httpx.AsyncClient,
    endpoint: str,
    domain: str,
    rtype: str,
) -> bool:
    """One DoH JSON query. True iff Status=0 and Answer is non-empty."""
    try:
        resp = await client.get(
            endpoint,
            params={"name": domain, "type": rtype},
            headers=_DOH_HEADERS,
            timeout=DOH_TIMEOUT,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        return data.get("Status") == 0 and bool(data.get("Answer"))
    except Exception as exc:  # noqa: BLE001
        check_dns.logger.debug("DoH %s %s: %s", rtype, domain, exc)
        return False


async def _check_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    endpoint_cycle: itertools.cycle,
    idx: int,
    name_normalized: str,
) -> tuple[int, bool, str]:
    """Resolve candidate domains for one business via DoH. Never raises.

    Fires all (candidate × record-type) queries in parallel; the first
    hit wins and remaining queries are cancelled. This is the key win
    over the UDP resolver, which was sequential per-candidate.
    """
    try:
        candidates = check_dns.generate_domains(name_normalized)
        if not candidates:
            return idx, False, ""

        async with sem:
            # Build all (candidate, rtype) tasks tagged so we know which
            # candidate scored a hit. We fire A and CNAME concurrently
            # — most real domains have an A record, but we don't want
            # to wait on a CNAME timeout for the others.
            tagged_tasks: list[asyncio.Task] = []
            for cand in candidates:
                ep = next(endpoint_cycle)
                for rtype in ("A", "CNAME"):
                    async def _run(c=cand, r=rtype, e=ep):
                        return c, await _doh_query(client, e, c, r)
                    tagged_tasks.append(asyncio.create_task(_run()))

            winner = ""
            try:
                for fut in asyncio.as_completed(tagged_tasks):
                    cand, ok = await fut
                    if ok:
                        winner = cand
                        break
            finally:
                for t in tagged_tasks:
                    if not t.done():
                        t.cancel()

            return idx, bool(winner), winner
    except Exception as exc:  # noqa: BLE001
        check_dns.logger.error("DoH row %d (%r): %s", idx, name_normalized, exc)
        return idx, False, ""


async def recheck_all(
    name_col_values: list[str],
    concurrency: int,
) -> list[tuple[bool, str]]:
    """Run DoH-backed DNS rechecks. Returns list[(has_domain, found_domain)]."""
    sem = asyncio.Semaphore(concurrency)
    endpoint_cycle = itertools.cycle(DOH_ENDPOINTS)

    has_domain = [False] * len(name_col_values)
    found_domain = [""] * len(name_col_values)

    limits = httpx.Limits(
        max_connections=concurrency * 2,
        max_keepalive_connections=concurrency,
    )
    async with httpx.AsyncClient(
        http2=False,
        limits=limits,
        timeout=DOH_TIMEOUT,
    ) as client:
        tasks = [
            asyncio.create_task(
                _check_one(client, sem, endpoint_cycle, i, n)
            )
            for i, n in enumerate(name_col_values)
        ]

        n_has = 0
        n_no = 0
        bar = tqdm(
            total=len(tasks),
            desc="DoH recheck",
            unit=" biz",
            mininterval=0.5,
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
                bar.set_postfix_str(f"Found: {n_has} | Still no: {n_no}")
        finally:
            bar.close()

    return list(zip(has_domain, found_domain))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="DoH-backed second-pass DNS recheck."
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
        "--concurrency", "-c", type=int, default=DOH_CONCURRENCY,
        help=f"Max concurrent checks (default: {DOH_CONCURRENCY})",
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

    print(f"DoH rechecking {len(df):,} businesses "
          f"(concurrency={args.concurrency}, timeout={DOH_TIMEOUT}s) ...")
    print(f"Endpoints: {', '.join(DOH_ENDPOINTS)}")
    print()

    t0 = time.perf_counter()
    results = asyncio.run(recheck_all(df["name_normalized"].tolist(), args.concurrency))
    elapsed = time.perf_counter() - t0

    df["recheck_has_domain"] = [r[0] for r in results]
    df["recheck_found_domain"] = [r[1] for r in results]

    found_mask = df["recheck_has_domain"]
    found_df = df[found_mask].copy()
    verified_df = df[~found_mask].drop(
        columns=["recheck_has_domain", "recheck_found_domain"]
    ).copy()

    # Promote recheck result into the canonical found_domain/has_domain
    # columns so downstream stages don't need to know about pass 2.
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
