"""V2-LITE P0.3: Website classifier — pain signals iš svetainės HTML/HTTP.

Klasifikuoja kiekvieną lead'o website į 4 lygius (0-3):

    0  no website        ABN turi has_domain=0 ARBA website_url=NULL
                         (NEklasifikuojam — jau žinom)
    1  dead              5xx / cert error / timeout / DNS fail
    2  bad / outdated    GYVA, bet:
                            - no HTTPS (only HTTP)
                            - NE mobile-friendly (no viewport meta)
                            - tech_stack legacy (wix / weebly / godaddysites / old WP)
                            - footer year < (CURRENT_YEAR - 4)
                            (bent 1 trigger'is)
    3  modern            GYVA + HTTPS + viewport + (recent footer OR modern stack)
                            (žaliasis šviesoforas — sunkiausia parduoti redizainą)

Naudoja TIK heuristics (regex + meta tags), JOKIO Lighthouse / PageSpeed /
external API. Pricing: $0 (mūsų bandwidth + Empirra user-agent only).

Politeness: 2.0s per-domain delay (default), bounded concurrency, robots.txt
ignored čia (mes net JS nepriimam, tik HEAD + GET su 500KB cap).

Idempotency: classifier_status field. Re-run skips 'ok' rows; --retry-errors
retries 'error'/'unreachable'.

Run:
    python -m src.enrichment.website_classifier --dry-run --limit 5
    python -m src.enrichment.website_classifier --live --limit 50
    python -m src.enrichment.website_classifier --live --limit 1000
    python -m src.enrichment.website_classifier --live --retry-errors
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm.asyncio import tqdm as tqdm_async

# Project imports
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dashboard.db import connect, default_db_path, init_schema, utcnow_iso  # noqa: E402


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

CLASSIFIER_TIMEOUT = float(os.getenv("CLASSIFIER_TIMEOUT", "10.0"))
CLASSIFIER_CONCURRENCY = int(os.getenv("CLASSIFIER_CONCURRENCY", "10"))
CLASSIFIER_PER_DOMAIN_DELAY = float(os.getenv("CLASSIFIER_PER_DOMAIN_DELAY", "2.0"))

USER_AGENT = (
    "Mozilla/5.0 (compatible; EmpirraBot/1.0; +https://empirra.com/bot) "
    "AU SMB website classifier"
)

# Max bytes per HTTP response — saugumas nuo huge files.
MAX_BYTES_PER_PAGE = 500_000   # 500 KB

# „Modern" stack — jei aptinkam šitą, NE laikom outdated, NET jei footer senas.
MODERN_STACKS: frozenset[str] = frozenset({
    "shopify", "webflow", "next.js", "nextjs", "react", "vue", "framer",
    "ghost", "hubspot",
})

# „Legacy / free" stack — automatinis ≤2 lygis.
LEGACY_FREE_STACKS: frozenset[str] = frozenset({
    "wix", "weebly", "godaddysites", "yola", "jimdo", "webnode",
    "business.site", "google sites",
})

# Per-stack patterns (raktas → tuple of strings to grep case-insensitive HTML).
TECH_PATTERNS: dict[str, tuple[str, ...]] = {
    "wordpress": ("wp-content", "wp-includes", "wordpress"),
    "wix":       ("wix.com", "wixsite.com", "wix-code"),
    "weebly":    ("weebly.com", "weeblycloud"),
    "squarespace": ("squarespace.com", "squarespace-cdn", "static1.squarespace.com"),
    "shopify":   ("cdn.shopify.com", "shopify.com/s/", "shopify-section"),
    "webflow":   ("webflow.com", "wf-loaded", "data-wf-"),
    "next.js":   ("__next", "_next/static"),
    "react":     ("react-dom", "data-reactroot"),
    "vue":       ("data-v-", "vue.js"),
    "framer":    ("framer.com", "framerusercontent.com"),
    "godaddysites": ("godaddysites.com", "img1.wsimg.com"),
    "google sites": ("sites.google.com", "/_/sitescontent/"),
    "ghost":     ("ghost.io", "/assets/built/"),
    "hubspot":   ("hs-scripts.com", "hubspot"),
}

# Footer year regex — © YYYY / Copyright YYYY / 20XX Company
_FOOTER_YEAR_RE = re.compile(r"(?:©|copyright|\(c\))\s*(\d{4})", re.IGNORECASE)
_GENERIC_YEAR_RE = re.compile(r"\b(20\d{2})\b")

CURRENT_YEAR = datetime.now().year
LEGACY_YEAR_THRESHOLD = CURRENT_YEAR - 4  # 2026 -> footer < 2022 = stale


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logger = logging.getLogger("website_classifier")
logger.setLevel(logging.INFO)

_info_handler = logging.FileHandler(
    LOG_DIR / f"website_classifier_{datetime.now():%Y%m%d}.log", encoding="utf-8"
)
_info_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
_err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(
    logging.Formatter("%(asctime)s [website_classifier] %(levelname)s %(message)s")
)
if not logger.handlers:
    logger.addHandler(_info_handler)
    logger.addHandler(_err_handler)


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    u = url.strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


async def _fetch_homepage(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, Any]:
    """GET su 500KB cap + cert/SSL validation. Returns dict — NEVER raises.

    Returns:
        {
            "ok": bool,
            "status_code": int | None,
            "ssl_valid": bool | None,
            "response_time_ms": int | None,
            "final_url": str | None,    # po redirect'ų
            "html": str | None,         # max 500KB
            "error": str,
        }
    """
    out: dict[str, Any] = {
        "ok": False, "status_code": None, "ssl_valid": None,
        "response_time_ms": None, "final_url": None, "html": None, "error": "",
    }

    started = time.monotonic()
    try:
        resp = await client.get(
            url,
            timeout=CLASSIFIER_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
        )
        out["response_time_ms"] = int((time.monotonic() - started) * 1000)
        out["status_code"] = resp.status_code
        out["final_url"] = str(resp.url)
        out["ssl_valid"] = str(resp.url).startswith("https://")

        # Cap response size
        content = resp.content[:MAX_BYTES_PER_PAGE]
        try:
            out["html"] = content.decode(resp.encoding or "utf-8", errors="ignore")
        except (LookupError, UnicodeDecodeError):
            out["html"] = content.decode("utf-8", errors="ignore")

        out["ok"] = 200 <= resp.status_code < 400
        if not out["ok"]:
            out["error"] = f"HTTP {resp.status_code}"
        return out

    except httpx.ConnectError as e:
        # ConnectError apima DNS fail + SSL handshake fail + TCP refused.
        out["error"] = f"connect: {str(e)[:200]}"
    except httpx.TimeoutException:
        out["error"] = "timeout"
    except httpx.HTTPError as e:
        out["error"] = f"http: {type(e).__name__}: {str(e)[:200]}"
    except Exception as e:  # noqa: BLE001
        out["error"] = f"unexpected: {type(e).__name__}: {str(e)[:200]}"

    out["response_time_ms"] = int((time.monotonic() - started) * 1000)
    # If error mentions SSL/cert, mark ssl_valid=0 explicitly.
    err_lower = out["error"].lower()
    if "ssl" in err_lower or "cert" in err_lower:
        out["ssl_valid"] = False
    return out


# ---------------------------------------------------------------------------
# HTML analysis
# ---------------------------------------------------------------------------

def _detect_tech_stack(html: str) -> str:
    """Returns first matched stack name from TECH_PATTERNS, arba 'unknown'."""
    if not html:
        return "unknown"
    lower = html.lower()
    for stack, patterns in TECH_PATTERNS.items():
        for p in patterns:
            if p in lower:
                return stack
    return "unknown"


def _has_viewport_meta(soup: BeautifulSoup) -> bool:
    """`<meta name="viewport" content="width=device-width, ...">` egzistuoja."""
    meta = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    if not meta:
        return False
    content = (meta.get("content") or "").lower()
    return "width" in content or "device-width" in content


def _extract_footer_year(soup: BeautifulSoup, full_html: str) -> int | None:
    """Try footer-specific first, fallback į bet kokius 20XX HTML'e."""
    # Footer tag first.
    footer = soup.find("footer")
    if footer:
        text = footer.get_text(" ", strip=True)
        m = _FOOTER_YEAR_RE.search(text)
        if m:
            year = int(m.group(1))
            if 2000 <= year <= CURRENT_YEAR + 1:
                return year
        m = _GENERIC_YEAR_RE.search(text)
        if m:
            year = int(m.group(1))
            if 2000 <= year <= CURRENT_YEAR + 1:
                return year

    # Global © YYYY (anywhere in HTML).
    m = _FOOTER_YEAR_RE.search(full_html)
    if m:
        year = int(m.group(1))
        if 2000 <= year <= CURRENT_YEAR + 1:
            return year
    return None


def classify(fetch_result: dict[str, Any]) -> dict[str, Any]:
    """Build classification dict iš fetch result'o.

    Returns:
        {
            "website_class": int (1-3),       # 0 = handled outside (no URL)
            "mobile_friendly": int | None,    # 0 / 1
            "tech_stack": str,
            "footer_year": int | None,
            "ssl_valid": int | None,          # 0 / 1
            "response_time_ms": int | None,
            "classifier_status": str,         # 'ok' | 'unreachable' | 'error'
            "reasons": list[str],             # debugging trail
        }
    """
    reasons: list[str] = []

    if not fetch_result["ok"]:
        # Dead = class 1.
        return {
            "website_class": 1,
            "mobile_friendly": None,
            "tech_stack": "unknown",
            "footer_year": None,
            "ssl_valid": 1 if fetch_result.get("ssl_valid") is True else
                        (0 if fetch_result.get("ssl_valid") is False else None),
            "response_time_ms": fetch_result.get("response_time_ms"),
            "classifier_status": "unreachable" if "connect" in fetch_result["error"]
                                or "timeout" in fetch_result["error"]
                                else "error",
            "reasons": [f"dead: {fetch_result['error']}"],
        }

    html = fetch_result["html"] or ""
    soup = BeautifulSoup(html, "html.parser")

    has_viewport = _has_viewport_meta(soup)
    footer_year = _extract_footer_year(soup, html)
    stack = _detect_tech_stack(html)
    ssl_ok = bool(fetch_result.get("ssl_valid"))

    if not ssl_ok:
        reasons.append("no HTTPS")
    if not has_viewport:
        reasons.append("no viewport meta (NOT mobile-friendly)")
    if stack in LEGACY_FREE_STACKS:
        reasons.append(f"legacy/free stack: {stack}")
    if footer_year is not None and footer_year < LEGACY_YEAR_THRESHOLD:
        reasons.append(f"stale footer year: {footer_year} (< {LEGACY_YEAR_THRESHOLD})")

    # Class decision: bent 1 problema → 2; jokios + modern stack OR fresh footer → 3.
    if reasons:
        website_class = 2
    else:
        if stack in MODERN_STACKS or (footer_year is not None and footer_year >= CURRENT_YEAR - 1):
            website_class = 3
            reasons.append(f"modern: ssl+viewport+stack={stack}+footer={footer_year}")
        else:
            # Visa OK bet nei modern stack nei fresh footer → leidžiam 3 (no pain signals).
            website_class = 3
            reasons.append("no pain signals detected")

    return {
        "website_class": website_class,
        "mobile_friendly": 1 if has_viewport else 0,
        "tech_stack": stack,
        "footer_year": footer_year,
        "ssl_valid": 1 if ssl_ok else 0,
        "response_time_ms": fetch_result.get("response_time_ms"),
        "classifier_status": "ok",
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _eligible_for_classifier(
    conn,
    limit: int,
    retry_errors: bool = False,
) -> list[tuple[str, str]]:
    """Lead'ai su website_url, kurių dar neklasifikavom (arba retry).

    Grąžina [(abn, website_url), ...].

    Filtras:
      - website_url NOT NULL (kažką klasifikuoti turim)
      - stage_a_status = 'ok'
      - au_validation_status != 'not_au' (anti-PROXYTECH)
      - classifier_status IS NULL ARBA (retry_errors AND IN ('error','unreachable'))
    """
    if retry_errors:
        status_clause = (
            "(e.classifier_status IS NULL OR "
            " e.classifier_status IN ('error', 'unreachable'))"
        )
    else:
        status_clause = "e.classifier_status IS NULL"

    sql = f"""
        SELECT e.abn, e.website_url
        FROM enrichment e
        WHERE e.stage_a_status = 'ok'
          AND e.website_url IS NOT NULL
          AND LENGTH(TRIM(e.website_url)) > 0
          AND (e.au_validation_status IS NULL
               OR e.au_validation_status != 'not_au')
          AND {status_clause}
        ORDER BY e.priority_score DESC NULLS LAST, e.updated_at DESC
        LIMIT ?
    """
    return [(r[0], r[1]) for r in conn.execute(sql, (limit,)).fetchall()]


def _upsert_classification(conn, abn: str, result: dict[str, Any]) -> None:
    """UPDATE enrichment row su classifier fields. Row turi jau egzistuoti
    (po Stage A). NE INSERT — jei row dingo, log + skip.
    """
    now = utcnow_iso()
    n = conn.execute(
        """UPDATE enrichment SET
                website_class           = ?,
                mobile_friendly         = ?,
                tech_stack              = ?,
                footer_year             = ?,
                ssl_valid               = ?,
                response_time_ms        = ?,
                classifier_status       = ?,
                classifier_attempted_at = ?,
                updated_at              = ?
           WHERE abn = ?""",
        (
            result["website_class"],
            result["mobile_friendly"],
            result["tech_stack"],
            result["footer_year"],
            result["ssl_valid"],
            result["response_time_ms"],
            result["classifier_status"],
            now, now,
            abn,
        ),
    ).rowcount
    if n == 0:
        logger.warning("UPDATE 0 rows for %s — enrichment row missing", abn)


# ---------------------------------------------------------------------------
# BATCH
# ---------------------------------------------------------------------------

async def _classify_batch(
    conn,
    targets: list[tuple[str, str]],
    concurrency: int,
    dry_run: bool,
) -> dict[str, int]:
    """Process all (abn, url) tuples. Returns counters."""
    sem = asyncio.Semaphore(concurrency)
    last_request_per_host: dict[str, float] = defaultdict(float)
    counters = {"ok": 0, "unreachable": 0, "error": 0, "skipped": 0,
                "class_1": 0, "class_2": 0, "class_3": 0}

    if dry_run:
        for abn, url in targets:
            counters["skipped"] += 1
        return counters

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=concurrency * 2),
        verify=True,    # strict SSL — fail'inam jei cert blogas
        http2=False,
    ) as client:

        async def _worker(abn: str, url: str) -> tuple[str, dict[str, Any]]:
            async with sem:
                norm = _normalize_url(url)
                if not norm:
                    return abn, {
                        "website_class": 1, "mobile_friendly": None,
                        "tech_stack": "unknown", "footer_year": None,
                        "ssl_valid": None, "response_time_ms": None,
                        "classifier_status": "error",
                        "reasons": ["empty url"],
                    }
                host = urlparse(norm).hostname or norm
                # Per-domain politeness
                elapsed = time.monotonic() - last_request_per_host[host]
                wait_for = CLASSIFIER_PER_DOMAIN_DELAY - elapsed
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                last_request_per_host[host] = time.monotonic()

                fetch = await _fetch_homepage(client, norm)
                cls = classify(fetch)
                return abn, cls

        tasks = [_worker(abn, url) for abn, url in targets]
        for coro in tqdm_async.as_completed(tasks, total=len(tasks), desc="Classify"):
            abn, cls = await coro
            _upsert_classification(conn, abn, cls)
            counters[cls["classifier_status"]] = counters.get(cls["classifier_status"], 0) + 1
            wc = cls["website_class"]
            counters[f"class_{wc}"] = counters.get(f"class_{wc}", 0) + 1

    return counters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _onedrive_db_fallback() -> Path:
    local = default_db_path()
    if local.exists():
        return local
    onedrive = Path(
        r"C:\Users\pinig\OneDrive\Stalinis kompiuteris\Automatiomm_empirra"
        r"\abr-data\abr-pipeline\dashboard\outreach.db"
    )
    return onedrive if onedrive.exists() else local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="V2-LITE P0.3: Website classifier — 4-level pain signal scorer."
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=CLASSIFIER_CONCURRENCY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--retry-errors", action="store_true",
        help="Include 'error' / 'unreachable' rows for retry",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.dry_run and not args.live:
        print(
            "ERROR: pass either --dry-run or --live.\n"
            "  --dry-run = preview targets only\n"
            "  --live    = actually fetch + classify (TIK Empirra bandwidth, $0 paid)",
            file=sys.stderr,
        )
        return 1

    db_path = args.db or _onedrive_db_fallback()
    if not db_path.exists():
        print(f"ERROR: outreach.db not found at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    init_schema(conn)

    targets = _eligible_for_classifier(
        conn, limit=args.limit, retry_errors=args.retry_errors
    )

    if not targets:
        print(
            "Nothing to classify. Possible reasons:\n"
            "  - All website-bearing leads already classified\n"
            "  - All eligible leads marked au_validation_status='not_au'\n"
            "  - No Stage A enrichment runs yet"
        )
        return 0

    print(
        f"{'[DRY RUN] ' if args.dry_run else ''}Classifier: {len(targets)} targets, "
        f"concurrency={args.concurrency}, delay={CLASSIFIER_PER_DOMAIN_DELAY}s"
    )
    if args.dry_run:
        for abn, url in targets[:10]:
            print(f"  {abn}  ->  {url}")
        if len(targets) > 10:
            print(f"  ... (+{len(targets) - 10} more)")
        return 0

    started = time.time()
    counters = asyncio.run(_classify_batch(
        conn, targets, args.concurrency, args.dry_run
    ))
    elapsed = time.time() - started

    print(
        f"\nDone in {elapsed:.1f}s.\n"
        f"  OK:           {counters.get('ok', 0)}\n"
        f"  Unreachable:  {counters.get('unreachable', 0)}\n"
        f"  Error:        {counters.get('error', 0)}\n"
        f"  Class 1 (dead):           {counters.get('class_1', 0)}\n"
        f"  Class 2 (bad/outdated):   {counters.get('class_2', 0)}\n"
        f"  Class 3 (modern):         {counters.get('class_3', 0)}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
