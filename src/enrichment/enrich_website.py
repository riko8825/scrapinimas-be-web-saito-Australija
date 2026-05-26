"""Stage B: Website scraper — extract email + FB + IG + LinkedIn iš svetainės.

Reads eligible ABNs via filters.eligible_for_stage_b (turi `website_url`
po Stage A, NOT free-tier hosting, NOT FB-as-website, NO contact_email yet),
fetches homepage + /contact + /about, parses HTML su BeautifulSoup4,
extract'ina contact channels per regex + DOM selectors.

Cost: $0 (jokio API key, jokio paid service).

Politeness:
  - Per-domain delay 2.0s (set via SCRAPER_PER_DOMAIN_DELAY)
  - Robots.txt compliance (cached per host)
  - User-Agent: politely identifies as Empirra lead enrichment
  - Concurrency 10 (set via SCRAPER_CONCURRENCY)
  - HTTP timeout 10s (set via SCRAPER_TIMEOUT)

Idempotency: stage_b_status field tracks per-ABN state. Re-run skips
'ok' rows; --retry-errors retries only 'error' rows.

Run:
    python -m src.enrichment.enrich_website --dry-run --limit 5
    python -m src.enrichment.enrich_website --live --limit 50
    python -m src.enrichment.enrich_website --live --limit 1000
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from tqdm.asyncio import tqdm as tqdm_async

# Project imports
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dashboard.db import connect, default_db_path, init_schema, utcnow_iso  # noqa: E402
from src.enrichment import filters  # noqa: E402


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

SCRAPER_TIMEOUT = float(os.getenv("SCRAPER_TIMEOUT", "10.0"))
SCRAPER_CONCURRENCY = int(os.getenv("SCRAPER_CONCURRENCY", "10"))
SCRAPER_PER_DOMAIN_DELAY = float(os.getenv("SCRAPER_PER_DOMAIN_DELAY", "2.0"))

USER_AGENT = (
    "Mozilla/5.0 (compatible; EmpirraBot/1.0; +https://empirra.com/bot) "
    "AU SMB outreach research"
)

# Pages, kuriuos lankome (besides homepage)
SECONDARY_PATHS = ("/contact", "/contact-us", "/about", "/about-us")

# Max bytes per HTTP response — prevent download'inant huge files
MAX_BYTES_PER_PAGE = 500_000  # 500 KB


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

logger = logging.getLogger("enrich_website")
logger.setLevel(logging.INFO)

_info_handler = logging.FileHandler(
    LOG_DIR / f"enrich_website_{datetime.now():%Y%m%d}.log", encoding="utf-8"
)
_info_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
)
_err_handler = logging.FileHandler(LOG_DIR / "errors.log", encoding="utf-8")
_err_handler.setLevel(logging.WARNING)
_err_handler.setFormatter(
    logging.Formatter("%(asctime)s [enrich_website] %(levelname)s %(message)s")
)
if not logger.handlers:
    logger.addHandler(_info_handler)
    logger.addHandler(_err_handler)


# ---------------------------------------------------------------------------
# EXTRACTION REGEX
# ---------------------------------------------------------------------------

# RFC 5322 lite — pakankamas 99% real-world email'ams.
# Atmetam image/file patterns (.jpg@2x kaip false positive).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Skip emails, kurie aiškiai NĖRA contact (placeholders, generic, file)
_EMAIL_SKIP_PATTERNS = (
    "example.com", "example.org", "yourdomain", "domain.com",
    "email@", "noreply", "no-reply", "donotreply",
    "sentry.io", "wixpress.com", ".png", ".jpg", ".gif",
    "@2x", "@3x",  # retina image suffixes
)

# Social URL patterns (loose — caller will normalize)
_FB_RE = re.compile(
    r"https?://(?:www\.|m\.)?facebook\.com/(?:pages/[^/]+/\d+|[A-Za-z0-9._-]+)/?",
    re.IGNORECASE,
)
_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?",
    re.IGNORECASE,
)
_LINKEDIN_RE = re.compile(
    r"https?://(?:www\.)?linkedin\.com/(?:in|company)/([A-Za-z0-9._-]+)/?",
    re.IGNORECASE,
)


def _is_real_email(email: str) -> bool:
    """Filter placeholder + image-suffix false positives."""
    em = email.lower().strip()
    if any(p in em for p in _EMAIL_SKIP_PATTERNS):
        return False
    if len(em) > 100 or len(em) < 5:
        return False
    return True


def _pick_best_email(emails: list[str]) -> str | None:
    """Iš kelių rastų email'ų pasirink geriausią — info@, contact@, hello@.

    Prefer paskirties (info@, contact@) prieš asmenis (john@). Tai NE
    privacy violation — tai public website footer'io email'as.
    """
    if not emails:
        return None
    cleaned = sorted(set(e.strip().lower() for e in emails if _is_real_email(e)))
    if not cleaned:
        return None

    # Priority: info > contact > hello > admin > sales > anything
    prefixes_priority = ("info@", "contact@", "hello@", "admin@", "sales@", "office@")
    for prefix in prefixes_priority:
        for email in cleaned:
            if email.startswith(prefix):
                return email
    return cleaned[0]


def _extract_emails_from_html(html: str) -> list[str]:
    """All email'ai iš HTML body — mailto: links + plain text regex."""
    soup = BeautifulSoup(html, "html.parser")

    # mailto: links (highest signal)
    mailto_emails = []
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.IGNORECASE)):
        href = a.get("href", "")
        em = href.replace("mailto:", "").split("?")[0].strip()
        if em:
            mailto_emails.append(em)

    # Plain text regex (lower priority, may have noise)
    text_emails = _EMAIL_RE.findall(soup.get_text())

    return mailto_emails + text_emails


def _extract_socials(html: str, base_url: str) -> dict[str, str | None]:
    """FB + IG + LinkedIn URLs iš HTML.

    Ieškom <a href="..."> ir taip pat plain text regex'u (jei JS rendered'iai
    paliko URL kaip tekstas).
    """
    soup = BeautifulSoup(html, "html.parser")
    fb_urls, ig_urls, li_urls = [], [], []

    # <a href> links — tikriausia signal
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "facebook.com" in href.lower():
            full = urljoin(base_url, href)
            if _FB_RE.search(full):
                fb_urls.append(full)
        elif "instagram.com" in href.lower():
            full = urljoin(base_url, href)
            if _IG_RE.search(full):
                ig_urls.append(full)
        elif "linkedin.com" in href.lower():
            full = urljoin(base_url, href)
            if _LINKEDIN_RE.search(full):
                li_urls.append(full)

    # Text regex fallback (kai href'as ne pilnas URL)
    text = soup.get_text()
    fb_urls.extend(_FB_RE.findall(text))
    ig_urls.extend(_IG_RE.findall(text))
    li_urls.extend(_LINKEDIN_RE.findall(text))

    return {
        "fb_url": fb_urls[0].rstrip("/") if fb_urls else None,
        "ig_url": ig_urls[0].rstrip("/") if ig_urls else None,
        "linkedin_url": li_urls[0].rstrip("/") if li_urls else None,
    }


# ---------------------------------------------------------------------------
# ROBOTS.TXT CACHE
# ---------------------------------------------------------------------------

_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


async def _robots_allows(url: str, client: httpx.AsyncClient) -> bool:
    """Check robots.txt cached per host. Returns True if allowed or unknown."""
    parsed = urlparse(url)
    host = parsed.netloc
    if not host:
        return True

    if host in _ROBOTS_CACHE:
        rp = _ROBOTS_CACHE[host]
        return rp.can_fetch(USER_AGENT, url) if rp else True

    robots_url = f"{parsed.scheme}://{host}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=5.0)
        if resp.status_code == 200:
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())
            _ROBOTS_CACHE[host] = rp
            return rp.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001 — robots fail = allow (defensive)
        pass
    _ROBOTS_CACHE[host] = None  # unknown — allow
    return True


# ---------------------------------------------------------------------------
# PER-DOMAIN RATE LIMITER
# ---------------------------------------------------------------------------

class DomainRateLimiter:
    """Politeness — minimum gap between same-host requests."""

    def __init__(self, delay_seconds: float) -> None:
        self.delay = delay_seconds
        self._next_at: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            next_allowed = self._next_at.get(host, 0)
            wait_seconds = max(0, next_allowed - now)
            self._next_at[host] = max(next_allowed, now) + self.delay
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)


# ---------------------------------------------------------------------------
# PAGE FETCH (single)
# ---------------------------------------------------------------------------

async def _fetch_html(
    client: httpx.AsyncClient,
    url: str,
    rate_limiter: DomainRateLimiter,
) -> str | None:
    """GET URL su politeness + size cap. Returns HTML string or None."""
    parsed = urlparse(url)
    host = parsed.netloc
    if not host:
        return None

    await rate_limiter.wait(host)
    if not await _robots_allows(url, client):
        logger.info("robots.txt disallows %s", url)
        return None

    try:
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"}
        resp = await client.get(
            url, headers=headers, timeout=SCRAPER_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "").lower()
        if "html" not in ctype and "xml" not in ctype:
            return None
        # Truncate to max bytes — saugumas
        return resp.text[:MAX_BYTES_PER_PAGE]
    except (httpx.HTTPError, httpx.TimeoutException, UnicodeDecodeError) as e:
        logger.warning("fetch failed %s: %s", url, type(e).__name__)
        return None


# ---------------------------------------------------------------------------
# ENRICHMENT (single ABN)
# ---------------------------------------------------------------------------

async def _enrich_one(
    client: httpx.AsyncClient,
    abn: str,
    website_url: str,
    rate_limiter: DomainRateLimiter,
) -> dict[str, Any]:
    """Enrich one lead by scraping its website. NEVER raises.

    Strategy: fetch homepage + try /contact /contact-us /about. Merge findings.
    Pirma rasta info wins (assume homepage = canonical signal).
    """
    out: dict[str, Any] = {
        "abn": abn,
        "stage_b_status": "no_data",
        "contact_email": None,
        "scraped_fb_url": None,
        "scraped_ig_url": None,
        "linkedin_url": None,
    }

    # Normalize URL — Places kartais grąžina su trailing slash, kartais ne
    url = website_url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    parsed = urlparse(url)
    if not parsed.netloc:
        out["stage_b_status"] = "error"
        logger.warning("invalid URL %s: %s", abn, website_url)
        return out

    base = f"{parsed.scheme}://{parsed.netloc}"

    # Try homepage first
    pages_to_try = [url]
    if url.rstrip("/") != base:
        pages_to_try.append(base)
    for path in SECONDARY_PATHS:
        pages_to_try.append(base + path)

    emails_all = []
    socials_merged = {"fb_url": None, "ig_url": None, "linkedin_url": None}

    for page_url in pages_to_try:
        html = await _fetch_html(client, page_url, rate_limiter)
        if html is None:
            continue
        emails_all.extend(_extract_emails_from_html(html))
        socials = _extract_socials(html, page_url)
        for key in socials_merged:
            if not socials_merged[key] and socials[key]:
                socials_merged[key] = socials[key]

        # Stop early jei jau radom email + visi socials
        if (
            _pick_best_email(emails_all)
            and all(socials_merged.values())
        ):
            break

    best_email = _pick_best_email(emails_all)
    out["contact_email"] = best_email
    out["scraped_fb_url"] = socials_merged["fb_url"]
    out["scraped_ig_url"] = socials_merged["ig_url"]
    out["linkedin_url"] = socials_merged["linkedin_url"]

    # Status logic
    if best_email or any(socials_merged.values()):
        out["stage_b_status"] = "ok"
    else:
        out["stage_b_status"] = "no_data"

    return out


# ---------------------------------------------------------------------------
# DRY-RUN
# ---------------------------------------------------------------------------

def _dry_run_one(abn: str, website_url: str) -> dict[str, Any]:
    return {
        "abn": abn,
        "stage_b_status": "skipped",
        "contact_email": f"[DRY] would scrape {website_url}",
        "scraped_fb_url": None,
        "scraped_ig_url": None,
        "linkedin_url": None,
    }


# ---------------------------------------------------------------------------
# DB PERSIST
# ---------------------------------------------------------------------------

def _upsert(conn, result: dict[str, Any]) -> None:
    """UPDATE existing enrichment row (Stage A jau įdėjo abn)."""
    now = utcnow_iso()
    conn.execute(
        """UPDATE enrichment
           SET stage_b_status = ?,
               stage_b_attempted_at = ?,
               contact_email = ?,
               scraped_fb_url = ?,
               scraped_ig_url = ?,
               linkedin_url = ?,
               updated_at = ?
           WHERE abn = ?""",
        (
            result["stage_b_status"],
            now,
            result["contact_email"],
            result["scraped_fb_url"],
            result["scraped_ig_url"],
            result["linkedin_url"],
            now,
            result["abn"],
        ),
    )


def _create_run(conn) -> int:
    cur = conn.execute(
        "INSERT INTO enrichment_runs (stage, started_at, status) VALUES ('b', ?, 'running')",
        (utcnow_iso(),),
    )
    return cur.lastrowid


def _finish_run(conn, run_id: int, attempted: int, ok: int, error: int, status: str) -> None:
    conn.execute(
        """UPDATE enrichment_runs
           SET finished_at = ?, count_attempted = ?, count_ok = ?,
               count_error = ?, status = ?
           WHERE id = ?""",
        (utcnow_iso(), attempted, ok, error, status, run_id),
    )


# ---------------------------------------------------------------------------
# BATCH ORCHESTRATION
# ---------------------------------------------------------------------------

async def _run_batch(
    conn,
    abns: list[str],
    concurrency: int,
    dry_run: bool,
) -> dict[str, int]:
    """Process all ABNs su bounded concurrency."""
    placeholders = ",".join("?" * len(abns))
    rows = conn.execute(
        f"SELECT abn, website_url FROM enrichment WHERE abn IN ({placeholders})",
        abns,
    ).fetchall()
    url_map = {r["abn"]: r["website_url"] for r in rows}

    sem = asyncio.Semaphore(concurrency)
    rate_limiter = DomainRateLimiter(SCRAPER_PER_DOMAIN_DELAY)
    counters = {"ok": 0, "no_data": 0, "error": 0, "skipped": 0}

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=concurrency * 2),
        follow_redirects=True,
    ) as client:

        async def _worker(abn: str) -> dict[str, Any]:
            async with sem:
                website = url_map.get(abn)
                if not website:
                    return {
                        "abn": abn, "stage_b_status": "error",
                        "contact_email": None, "scraped_fb_url": None,
                        "scraped_ig_url": None, "linkedin_url": None,
                    }
                if dry_run:
                    return _dry_run_one(abn, website)
                return await _enrich_one(client, abn, website, rate_limiter)

        tasks = [_worker(abn) for abn in abns]
        for coro in tqdm_async.as_completed(tasks, total=len(tasks), desc="Scrape"):
            result = await coro
            _upsert(conn, result)
            counters[result["stage_b_status"]] = counters.get(result["stage_b_status"], 0) + 1

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
    if onedrive.exists():
        return onedrive
    return local


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage B: Website scraper — extract email + FB + IG + LinkedIn."
    )
    parser.add_argument("--limit", type=int, default=100,
                        help="Max leads per run (default: 100)")
    parser.add_argument("--concurrency", type=int, default=SCRAPER_CONCURRENCY,
                        help=f"Max concurrent scrapes (default: {SCRAPER_CONCURRENCY})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't actually fetch — just print URLs.")
    parser.add_argument("--live", action="store_true",
                        help="Confirm live scrape. Required to override dry-run safety.")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.dry_run and not args.live:
        print(
            "ERROR: must pass either --dry-run (safe) or --live (real scrape).",
            file=sys.stderr,
        )
        return 1

    db_path = args.db or _onedrive_db_fallback()
    conn = connect(db_path)
    init_schema(conn)

    abns = filters.eligible_for_stage_b(conn, limit=args.limit)

    if not abns:
        print("Nothing eligible for Stage B. Run Stage A first.")
        return 0

    print(
        f"{'[DRY RUN] ' if args.dry_run else ''}Stage B: {len(abns)} eligible, "
        f"concurrency={args.concurrency}, per-domain delay={SCRAPER_PER_DOMAIN_DELAY}s"
    )

    run_id = _create_run(conn)
    started = time.time()
    try:
        counters = asyncio.run(_run_batch(conn, abns, args.concurrency, args.dry_run))
        status = "ok"
    except Exception as e:  # noqa: BLE001
        logger.error("Batch crashed: %s", e)
        counters = {"ok": 0, "no_data": 0, "error": 0, "skipped": 0}
        status = "failed"
        raise
    finally:
        _finish_run(
            conn, run_id,
            attempted=sum(counters.get(k, 0) for k in ("ok", "no_data", "error", "skipped")),
            ok=counters.get("ok", 0),
            error=counters.get("error", 0),
            status=status,
        )

    elapsed = time.time() - started
    n_ok = counters.get("ok", 0)
    n_total = sum(counters.get(k, 0) for k in ("ok", "no_data", "error"))
    hit_rate = (n_ok / n_total * 100) if n_total else 0

    print(
        f"\nDone in {elapsed:.1f}s.\n"
        f"  OK (got data):  {counters.get('ok', 0)}\n"
        f"  No data found:  {counters.get('no_data', 0)}\n"
        f"  Error:          {counters.get('error', 0)}\n"
        f"  Skipped:        {counters.get('skipped', 0)}\n"
        f"  Hit rate:       {hit_rate:.1f}%\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
