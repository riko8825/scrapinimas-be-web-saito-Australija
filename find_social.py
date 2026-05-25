"""Social-lookup stage: find Facebook + Instagram URLs for each business.

Reads ./output/no_website.csv (output of check_dns.py), runs three search
queries per business against Brave Search API:

    Q1:  "<clean_name>" <postcode> site:facebook.com    (precise)
    Q2:  "<clean_name>" <postcode> site:instagram.com   (precise)
    Q3:  "<clean_name>" australia facebook              (broad — yields both)

Candidate URLs from precise + broad queries are merged, fuzzy-matched
against the business name (bigram Jaccard >= 0.5), and gated by an
Australia signal check (.com.au / +61 / 'australia' / state name /
postcode in title or URL). The best-scoring AU-validated FB and IG
URLs are written to ./output/has_social.csv.

If Brave fails (HTTP error, rate limit, missing API key), the same query
falls back to the Bing Web Search API.

Rate limiting:
    A single global asyncio.Lock enforces a 400ms gap between outbound
    search requests, satisfying Brave's free-tier limit and being polite
    to Bing. This is sequential by design — running 100 searches in
    parallel without a key with sufficient quota would get us 429'd
    within seconds.

Resilience:
    Per CLAUDE.md: per-business errors log to logs/errors.log and the
    business is still emitted (with empty fb/ig columns). Pipeline never
    crashes on a single failure.

Run:
    python find_social.py
    python find_social.py -i no_website.csv -o has_social.csv --limit 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv
from tqdm.asyncio import tqdm as tqdm_async


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

load_dotenv()

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "").strip()
BING_API_KEY = os.getenv("BING_API_KEY", "").strip()

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BING_ENDPOINT = "https://api.bing.microsoft.com/v7.0/search"

DELAY_MS = int(os.getenv("SOCIAL_DELAY_MS", "400"))
SOCIAL_TIMEOUT = float(os.getenv("SOCIAL_TIMEOUT", "15"))
FUZZY_THRESHOLD = 0.5

OUTPUT_COLUMNS = ["abn", "name", "state", "postcode", "facebook", "instagram"]

LOG_DIR = Path("./logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("find_social")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
    fh.setLevel(logging.WARNING)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | find_social | %(message)s"
    ))
    logger.addHandler(fh)


# ---------------------------------------------------------------------------
# FUZZY MATCH  (pure python — bigram Jaccard)
# ---------------------------------------------------------------------------

_LEGAL_NOISE = (
    "pty ltd", "pty. ltd.", "pty ltd.",
    "proprietary limited", "proprietary",
    "pty", "ltd", "limited", "inc", "incorporated",
    "the trustee for", "trustee for", "atf", "the",
    "co.", "company", "&", "and",
)

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WS_RE = re.compile(r"\s+")

AU_STATE_NAMES = {
    "NSW": "new south wales",
    "VIC": "victoria",
    "QLD": "queensland",
    "WA":  "western australia",
    "SA":  "south australia",
    "TAS": "tasmania",
    "ACT": "australian capital territory",
    "NT":  "northern territory",
}


def _normalize(text: str) -> str:
    """Lowercase, strip legal suffixes + punctuation, collapse whitespace."""
    if not text:
        return ""
    s = text.lower()
    for noise in _LEGAL_NOISE:
        s = s.replace(noise, " ")
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


_PARENS_STATE_RE = re.compile(
    r"\((?:" + "|".join(AU_STATE_NAMES.keys()) + r")\)",
    re.IGNORECASE,
)


def _query_clean(name: str) -> str:
    """Strip legal-noise tokens from a business name for use in a search query.

    Unlike _normalize, this keeps original casing and only removes the
    legal-suffix tokens — brand-bearing words like SERVICES/GROUP/INDUSTRIES
    are preserved on purpose, since they often carry the actual brand
    identity (e.g. 'Hardy Group NT'). Parenthesised state codes like
    '(NT)' / '(QLD)' are stripped because Brave treats them as exact
    query terms and they crater recall on quoted searches.
    """
    if not name:
        return ""
    s = _PARENS_STATE_RE.sub(" ", name)
    # Apply case-insensitive token strips. Replace with space, then collapse.
    for noise in _LEGAL_NOISE:
        s = re.sub(rf"(?i)\b{re.escape(noise)}\b\.?", " ", s)
    s = re.sub(r"[(),.]", " ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def fuzzy_match(a: str, b: str) -> float:
    """Bigram-Jaccard similarity in [0.0, 1.0]. Empty inputs return 0.0."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    def _bigrams(s: str) -> set[str]:
        s = f" {s} "
        return {s[i:i + 2] for i in range(len(s) - 1)}

    ba, bb = _bigrams(na), _bigrams(nb)
    if not ba or not bb:
        return 0.0

    inter = len(ba & bb)
    union = len(ba | bb)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# URL EXTRACTION
# ---------------------------------------------------------------------------

_FB_RE = re.compile(
    r"https?://(?:www\.|m\.|web\.)?facebook\.com/([^/?#\s\"']+)",
    re.IGNORECASE,
)
_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/([^/?#\s\"']+)",
    re.IGNORECASE,
)

# Handle prefixes / sub-paths that are NOT a real business page.
_FB_BLOCKED = {
    "sharer", "tr", "login", "dialog", "plugins", "ads",
    "watch", "events", "marketplace", "groups", "pages",
    "help", "policies", "business", "search", "home.php",
}
_IG_BLOCKED = {
    "p", "explore", "reels", "reel", "tv", "accounts",
    "developer", "directory", "about", "tags", "stories",
}


def _normalize_handle(raw: str) -> str:
    """Lowercase + strip query/fragment debris from a captured handle."""
    return raw.split("?")[0].split("#")[0].rstrip("/").lower()


def extract_facebook(url: str) -> str:
    """Return canonical https://facebook.com/<handle> URL, or '' if junk."""
    m = _FB_RE.search(url)
    if not m:
        return ""
    handle = _normalize_handle(m.group(1))
    if not handle or handle in _FB_BLOCKED or len(handle) < 2:
        return ""
    if "." in handle:  # things like 'pages/...' or 'profile.php'
        if handle.startswith("profile.php"):
            return ""
    return f"https://facebook.com/{handle}"


def extract_instagram(url: str) -> str:
    """Return canonical https://instagram.com/<handle> URL, or '' if junk."""
    m = _IG_RE.search(url)
    if not m:
        return ""
    handle = _normalize_handle(m.group(1))
    if not handle or handle in _IG_BLOCKED or len(handle) < 2:
        return ""
    return f"https://instagram.com/{handle}"


def _is_australian(title: str, url: str, state: str, postcode: str) -> bool:
    """Return True if the (title, url) blob carries any Australian signal.

    Signals considered AU-positive:
      - '.com.au' anywhere in the URL
      - the literal '+61' phone prefix
      - 'australia' (case-insensitive)
      - the business's own state code (' nsw ', etc.) or full state name
      - the business's own postcode

    This is a *gate* applied after fuzzy match in _pick_best — without an
    AU signal, the candidate is rejected as a likely foreign false-positive
    (e.g. matching a US business with the same brand name).
    """
    blob = f" {title.lower()} {url.lower()} "
    if ".com.au" in blob:
        return True
    if "+61" in blob:
        return True
    if "australia" in blob:
        return True
    if state:
        s = state.strip().lower()
        if f" {s} " in blob:
            return True
        full = AU_STATE_NAMES.get(state.upper(), "")
        if full and full in blob:
            return True
    if postcode and postcode.strip() in blob:
        return True
    return False


def _pick_best(
    candidates: list[tuple[str, str]],
    business_name: str,
    extractor,
    state: str = "",
    postcode: str = "",
) -> str:
    """Run extractor over (url, title) pairs, keep best AU-validated candidate.

    Two gates must both pass: (1) bigram-Jaccard fuzzy match ≥ FUZZY_THRESHOLD
    against either the handle or the title; (2) at least one Australian geo
    signal present in the title or URL (see _is_australian). Returns the
    canonical URL of the best-scoring survivor, or '' if none qualify.
    """
    best_url = ""
    best_score = 0.0
    for url, title in candidates:
        canonical = extractor(url)
        if not canonical:
            continue
        handle = canonical.rsplit("/", 1)[-1].replace(".", " ").replace("-", " ").replace("_", " ")
        score = max(fuzzy_match(business_name, handle), fuzzy_match(business_name, title))
        if score < FUZZY_THRESHOLD or score <= best_score:
            continue
        if not _is_australian(title, url, state, postcode):
            continue
        best_score = score
        best_url = canonical
    return best_url


# ---------------------------------------------------------------------------
# SEARCH PROVIDERS
# ---------------------------------------------------------------------------

def _build_query_precise(name: str, postcode: str, site: str) -> str:
    """Narrow query: exact name + postcode + site filter.

    Best for businesses whose FB/IG handle is well-indexed and whose
    page text contains the postcode. Empirically returns few results
    (Brave treats too many terms as required), but when it hits it's
    a strong match.
    """
    clean = _query_clean(name)
    geo = postcode.strip() if postcode else ""
    return f'"{clean}" {geo} site:{site}'.strip()


def _build_query_broad(name: str, social: str) -> str:
    """Broad query: exact name + 'australia' + social keyword, no site: filter.

    Lets Brave grade pages globally, picking up FB/IG URLs that surface
    from third-party catalogues, the business's own website, or
    well-indexed FB pages whose body text mentions Australia. Returns
    many more candidates than the precise query.
    """
    clean = _query_clean(name)
    return f'"{clean}" australia {social}'.strip()


async def _brave_search(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Call Brave Web Search API. Raises httpx.HTTPError on failure."""
    if not BRAVE_API_KEY:
        raise RuntimeError("BRAVE_API_KEY not configured")

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": BRAVE_API_KEY,
    }
    params = {"q": query, "count": 10, "safesearch": "off"}
    resp = await client.get(BRAVE_ENDPOINT, headers=headers, params=params,
                            timeout=SOCIAL_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("web", {}).get("results", []) or []


async def _bing_search(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    """Call Bing Web Search v7 API. Raises httpx.HTTPError on failure."""
    if not BING_API_KEY:
        raise RuntimeError("BING_API_KEY not configured")

    headers = {"Ocp-Apim-Subscription-Key": BING_API_KEY}
    params = {"q": query, "count": 10, "responseFilter": "Webpages"}
    resp = await client.get(BING_ENDPOINT, headers=headers, params=params,
                            timeout=SOCIAL_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("webPages", {}).get("value", []) or []


def _brave_to_pairs(results: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Normalize Brave results to (url, title) tuples."""
    return [(r.get("url", ""), r.get("title", "") or r.get("description", ""))
            for r in results if r.get("url")]


def _bing_to_pairs(results: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Normalize Bing results to (url, title) tuples."""
    return [(r.get("url", ""), r.get("name", "") or r.get("snippet", ""))
            for r in results if r.get("url")]


# ---------------------------------------------------------------------------
# RATE LIMITER  (global 400ms gap between outbound requests)
# ---------------------------------------------------------------------------

class GapLimiter:
    """Enforce a minimum gap between successive acquires across all tasks."""

    def __init__(self, gap_ms: int) -> None:
        self.gap = gap_ms / 1000.0
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        """Block until the next slot is free; reserve it on return."""
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_at = now + self.gap


# ---------------------------------------------------------------------------
# LOOKUP DRIVER
# ---------------------------------------------------------------------------

async def _search_one(
    client: httpx.AsyncClient,
    limiter: GapLimiter,
    query: str,
) -> list[tuple[str, str]]:
    """Try Brave, fall back to Bing on any error. Returns (url, title) list.

    Returns an empty list (never raises) if both providers fail or no API
    keys are configured — the business simply gets blank fb/ig columns.
    """
    # ---- Brave ----
    if BRAVE_API_KEY:
        await limiter.wait()
        try:
            return _brave_to_pairs(await _brave_search(client, query))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brave failed for %r: %s", query, exc)

    # ---- Bing fallback ----
    if BING_API_KEY:
        await limiter.wait()
        try:
            return _bing_to_pairs(await _bing_search(client, query))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bing failed for %r: %s", query, exc)

    return []


async def _lookup_business(
    client: httpx.AsyncClient,
    limiter: GapLimiter,
    name: str,
    state: str,
    postcode: str,
) -> tuple[str, str]:
    """Run 3 queries per business → merge candidates → pick best AU-validated.

    Queries:
      1. precise FB:  "<clean>" <postcode> site:facebook.com
      2. precise IG:  "<clean>" <postcode> site:instagram.com
      3. broad:       "<clean>" australia facebook       (extracts BOTH FB+IG)

    The broad query has no site: filter, so its results include the
    business's own .com.au site, third-party catalogues, and FB pages
    indexed without the postcode in body text. Both FB and IG URLs are
    extracted from broad results — IG handles often appear in FB page
    snippets or vice versa.

    Cost: 3 Brave API calls per business (vs 2 before).
    """
    if not name:
        return "", ""

    fb_pairs = await _search_one(client, limiter,
                                 _build_query_precise(name, postcode, "facebook.com"))
    ig_pairs = await _search_one(client, limiter,
                                 _build_query_precise(name, postcode, "instagram.com"))
    broad_pairs = await _search_one(client, limiter,
                                    _build_query_broad(name, "facebook"))

    fb_url = _pick_best(fb_pairs + broad_pairs, name, extract_facebook, state, postcode)
    ig_url = _pick_best(ig_pairs + broad_pairs, name, extract_instagram, state, postcode)
    return fb_url, ig_url


async def lookup_all(df: pd.DataFrame) -> pd.DataFrame:
    """Process every row sequentially under the 400ms limiter.

    Sequential by design (see module docstring): Brave free tier is ~1 rps
    and parallel calls trigger 429s within a few seconds.

    Args:
        df: must contain `business_name` and `state` columns.

    Returns:
        Original df augmented with `facebook` and `instagram` columns.
    """
    limiter = GapLimiter(DELAY_MS)

    fb_urls: list[str] = [""] * len(df)
    ig_urls: list[str] = [""] * len(df)

    names = df["business_name"].fillna("").astype(str).tolist()
    states = df["state"].fillna("").astype(str).tolist() if "state" in df.columns else [""] * len(df)
    postcodes = df["postcode"].fillna("").astype(str).tolist() if "postcode" in df.columns else [""] * len(df)

    timeout = httpx.Timeout(SOCIAL_TIMEOUT, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def _task(idx: int) -> tuple[int, str, str]:
            try:
                fb, ig = await _lookup_business(client, limiter, names[idx], states[idx], postcodes[idx])
                return idx, fb, ig
            except Exception as exc:  # noqa: BLE001 — never crash the run
                logger.error("Row %d (%r): %s", idx, names[idx], exc)
                return idx, "", ""

        tasks = [asyncio.create_task(_task(i)) for i in range(len(df))]
        for fut in tqdm_async.as_completed(
            tasks, total=len(tasks), desc="Social search", unit=" biz", mininterval=0.5
        ):
            idx, fb, ig = await fut
            fb_urls[idx] = fb
            ig_urls[idx] = ig

    df = df.copy()
    df["facebook"] = fb_urls
    df["instagram"] = ig_urls
    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns an exit code."""
    ap = argparse.ArgumentParser(
        description="Find FB/IG URLs for businesses in no_website.csv."
    )
    ap.add_argument("--input", "-i", type=Path, default=Path("./output/no_website.csv"))
    ap.add_argument("--output", "-o", type=Path, default=Path("./output/has_social.csv"))
    ap.add_argument(
        "--limit", type=int, default=0,
        help="Only process the first N rows (0 = all). Useful for API-quota dev runs.",
    )
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input CSV not found: {args.input}", file=sys.stderr)
        return 2

    if not BRAVE_API_KEY and not BING_API_KEY:
        print("ERROR: neither BRAVE_API_KEY nor BING_API_KEY is set in .env",
              file=sys.stderr)
        return 2

    providers = []
    if BRAVE_API_KEY:
        providers.append("Brave (primary)")
    if BING_API_KEY:
        providers.append("Bing (fallback)" if BRAVE_API_KEY else "Bing")
    print(f"Providers: {', '.join(providers)}")
    print(f"Rate: 1 request every {DELAY_MS}ms\n")

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False, encoding="utf-8")

    for required in ("business_name",):
        if required not in df.columns:
            print(f"ERROR: input CSV missing required column {required!r}",
                  file=sys.stderr)
            return 2

    if args.limit and args.limit < len(df):
        print(f"Limiting to first {args.limit:,} rows (of {len(df):,})")
        df = df.head(args.limit).copy()

    print(f"Searching socials for {len(df):,} businesses ...\n")
    t0 = time.perf_counter()
    enriched = asyncio.run(lookup_all(df))
    elapsed = time.perf_counter() - t0

    # Conform to the requested output schema:
    # abn, name, state, postcode, facebook, instagram
    out = pd.DataFrame({
        "abn": enriched.get("abn", ""),
        "name": enriched["business_name"],
        "state": enriched.get("state", ""),
        "postcode": enriched.get("postcode", ""),
        "facebook": enriched["facebook"],
        "instagram": enriched["instagram"],
    })

    # Keep only rows that found at least one social profile.
    keep = (out["facebook"] != "") | (out["instagram"] != "")
    out_with_social = out[keep].copy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_with_social.to_csv(args.output, index=False, encoding="utf-8")

    total = len(out)
    n_fb = int((out["facebook"] != "").sum())
    n_ig = int((out["instagram"] != "").sum())
    n_either = int(keep.sum())
    rate = total / elapsed if elapsed else 0.0

    print()
    print("=" * 50)
    print(f"Searched      : {total:,} businesses in {elapsed:.1f}s ({rate:.1f}/s)")
    print(f"Found FB      : {n_fb:,}")
    print(f"Found IG      : {n_ig:,}")
    print(f"Found either  : {n_either:,}  ({(n_either / total * 100):.1f}%)" if total else "")
    print("=" * 50)
    print(f"Wrote {len(out_with_social):,} rows -> {args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
