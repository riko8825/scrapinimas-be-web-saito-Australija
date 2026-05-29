"""V2-LITE P1.1: Rule-based sales angle generator ($0).

NE-LLM šabloninis pick'eris. Įvedam lead'o pain signal'us → iš template
katalogo parenkam GERIAUSIAI tinkančią žinutę pagal prioritetų sąrašą.

Logikos prioritetas (pirmas match'as → tas template'as):

    1. CLASSIFIER_DEAD       website_class == 1  → "your website is down"
    2. NO_SITE_HIGH_REVIEWS  no_website + reviews >= 20 → "47 reviews, no website"
    3. WIX_OLD_FOOTER        tech_stack=wix + footer <=2019 → "Wix from 2018 era"
    4. LEGACY_STACK          stack in (wix/weebly/godaddy) → "free site, you can do better"
    5. STALE_FOOTER_NO_MOBILE footer <2020 + mobile_friendly=0 → "site looks 2015, no mobile"
    6. STALE_FOOTER          footer <2020 → "footer says 2014"
    7. NO_MOBILE             mobile_friendly=0 → "your site doesn't work on phones"
    8. NO_SSL                ssl_valid=0 → "no padlock, customers nervous"
    9. WEBSITE_CLASS_2       fallback class=2 → "looks outdated"
   10. NO_SITE_LOW_REVIEWS   no_website (else) → "no website at all"
   11. DEFAULT               anything else → generic

Visi template'ai turi tarp `{trading_name}` / `{review_count}` / `{footer_year}` /
`{tech_stack}` / `{first_name}` placeholders. Filling — pure str.format() su
defaults (jei laukas NULL — fallback).

Output per lead:
    {
        "template_id": "STALE_FOOTER_NO_MOBILE",
        "subject":     "Quick question about {trading_name}'s website",
        "body":        "Hi,\\n\\nNoticed {trading_name}'s site footer says...",
    }

Stored in `enrichment` table: angle_template_id, angle_subject, angle_body.

Run:
    python -m src.enrichment.sales_angle --dry-run --limit 5
    python -m src.enrichment.sales_angle --live --limit 100
    python -m src.enrichment.sales_angle --live  # all unfilled rows
    python -m src.enrichment.sales_angle --live --overwrite  # re-fill
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dashboard.db import connect, default_db_path, utcnow_iso  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("sales_angle")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.FileHandler(LOG_DIR / f"sales_angle_{datetime.now():%Y%m%d}.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

CURRENT_YEAR = datetime.now().year

# Bendras tail'as — Empirra value prop + low-friction CTA (mes paslaugą NEparduodam tekste).
COMMON_CTA = (
    "We build fast modern sites for AU service businesses — usually 7-10 day turnaround, "
    "no monthly lock-in.\n\n"
    "Would a quick 10-minute call this week make sense? Happy to share examples first.\n\n"
    "Cheers,\n"
    "{sender_name}"
)


TEMPLATES: list[dict[str, Any]] = [
    # ----------------------------------------------------------------------
    # 1. CLASSIFIER_DEAD — site offline / 5xx / cert error
    # ----------------------------------------------------------------------
    {
        "id": "CLASSIFIER_DEAD",
        "match": lambda c: c["website_class"] == 1,
        "subject": "Quick heads up — {trading_name} website is down",
        "body": (
            "Hi,\n\n"
            "Wanted to flag that when I tried opening your site at {website_url}, "
            "it didn't load — either offline or returning an error.\n\n"
            "If that's a known issue, ignore this. If not — losing inbound calls from people "
            "who Google {trading_name} and can't find the site.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 2. NO_SITE_HIGH_REVIEWS — verslas su 20+ reviews bet be svetainės
    # ----------------------------------------------------------------------
    {
        "id": "NO_SITE_HIGH_REVIEWS",
        "match": lambda c: (
            (c["website_class"] is None and not c["has_website_url"])
            and (c["review_count"] or 0) >= 20
        ),
        "subject": "{trading_name} — {review_count} Google reviews, but no website?",
        "body": (
            "Hi,\n\n"
            "Noticed {trading_name} has {review_count} Google reviews — clearly people love working with you. "
            "But couldn't find a website anywhere.\n\n"
            "When someone searches you and lands on the Google listing, there's no place to send them to see "
            "your work, get a quote form, or even read more. You're leaving easy bookings on the table.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 3. WIX_OLD_FOOTER — Wix + footer < 2020
    # ----------------------------------------------------------------------
    {
        "id": "WIX_OLD_FOOTER",
        "match": lambda c: (
            (c["tech_stack"] or "").lower() == "wix"
            and c["footer_year"] is not None and c["footer_year"] <= 2019
        ),
        "subject": "{trading_name} — your Wix site looks {age} years old",
        "body": (
            "Hi,\n\n"
            "Took a quick look at {website_url} — footer says {footer_year}, and it's still on Wix.\n\n"
            "Wix sites from that era are usually slow on mobile, poor SEO, and don't convert visitors into "
            "calls/quotes. We migrate businesses off Wix into a fast custom site that actually ranks.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 4. LEGACY_STACK — bet kuris free/legacy CMS
    # ----------------------------------------------------------------------
    {
        "id": "LEGACY_STACK",
        "match": lambda c: (c["tech_stack"] or "").lower() in {
            "wix", "weebly", "godaddysites", "yola", "jimdo", "webnode", "google sites",
        },
        "subject": "Quick note on {trading_name}'s website",
        "body": (
            "Hi,\n\n"
            "Saw {website_url} runs on {tech_stack}. That probably worked when you set it up, but those "
            "platforms are now hurting your Google ranking and load slow on phones.\n\n"
            "AU service businesses lose ~40% of mobile visitors after 3s of load time. A clean custom site "
            "fixes that — better SEO, faster, and you own it.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 5. STALE_FOOTER_NO_MOBILE — footer <2020 + not mobile
    # ----------------------------------------------------------------------
    {
        "id": "STALE_FOOTER_NO_MOBILE",
        "match": lambda c: (
            c["footer_year"] is not None and c["footer_year"] < 2020
            and c["mobile_friendly"] == 0
        ),
        "subject": "{trading_name} — site looks stuck in {footer_year}",
        "body": (
            "Hi,\n\n"
            "Quick observation about {website_url}: footer year shows {footer_year}, and the site doesn't "
            "resize properly on mobile.\n\n"
            "Anyone Googling you from a phone (which is ~70% of local searches now) sees a desktop site "
            "shrunk down, gives up, calls a competitor. Easy fix.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 6. STALE_FOOTER — footer <2020 (mobile OK)
    # ----------------------------------------------------------------------
    {
        "id": "STALE_FOOTER",
        "match": lambda c: c["footer_year"] is not None and c["footer_year"] < 2020,
        "subject": "{trading_name} — your site footer still says {footer_year}",
        "body": (
            "Hi,\n\n"
            "Small thing — {website_url} footer still shows ©{footer_year}. That tells visitors (and "
            "Google) the site hasn't been touched in {age} years.\n\n"
            "If the business is still going strong (looks like it is — saw the recent reviews), the website "
            "shouldn't be the thing telling people otherwise.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 7. NO_MOBILE — site OK bet mobile broken
    # ----------------------------------------------------------------------
    {
        "id": "NO_MOBILE",
        "match": lambda c: c["mobile_friendly"] == 0,
        "subject": "{trading_name} site doesn't work on phones",
        "body": (
            "Hi,\n\n"
            "Loaded {website_url} on my phone — text is tiny, buttons don't fit. No viewport meta tag set.\n\n"
            "Roughly 7 out of 10 people searching for {industry} in {state} are on mobile. That's a lot of "
            "would-be customers giving up before they reach you.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 8. NO_SSL — http only
    # ----------------------------------------------------------------------
    {
        "id": "NO_SSL",
        "match": lambda c: c["ssl_valid"] == 0,
        "subject": "{trading_name} — no padlock on your website",
        "body": (
            "Hi,\n\n"
            "Noticed {website_url} doesn't have HTTPS (no padlock icon in Chrome). Browsers now show a "
            "scary \"Not Secure\" warning to every visitor — and Google ranks HTTPS sites higher.\n\n"
            "Fixable in an afternoon. Worth doing.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 9. WEBSITE_CLASS_2 — class=2 fallback (kažkokie pain signals, bet ne specifiniai aukščiau)
    # ----------------------------------------------------------------------
    {
        "id": "WEBSITE_CLASS_2",
        "match": lambda c: c["website_class"] == 2,
        "subject": "{trading_name} — your website is leaking bookings",
        "body": (
            "Hi,\n\n"
            "Took a look at {website_url}. A few things are quietly costing you bookings: outdated design, "
            "slow load on mobile, weak SEO signals.\n\n"
            "Local service businesses doing {industry} in {state} usually 2-3x their inbound quote requests "
            "after a proper rebuild. No fluff — happy to show a couple before/after examples.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 10. NO_SITE_LOW_REVIEWS — be reviews threshold'o
    # ----------------------------------------------------------------------
    {
        "id": "NO_SITE_LOW_REVIEWS",
        "match": lambda c: (
            c["website_class"] is None and not c["has_website_url"]
        ),
        "subject": "{trading_name} — quick question about your website",
        "body": (
            "Hi,\n\n"
            "Couldn't find a website for {trading_name} online. Total guess on my part — either you're "
            "running on referrals only, or it's just been on the to-do list for a while.\n\n"
            "Either way: AU {industry} businesses without a site usually lose ~30% of inbound to "
            "competitors who do have one. That's the cost of staying invisible.\n\n"
            + COMMON_CTA
        ),
    },

    # ----------------------------------------------------------------------
    # 11. DEFAULT — fallback (modern site, no specific pain)
    # ----------------------------------------------------------------------
    {
        "id": "DEFAULT",
        "match": lambda c: True,  # always last
        "subject": "Quick question for {trading_name}",
        "body": (
            "Hi,\n\n"
            "Reaching out to AU {industry} businesses in {state} we think we can help with website "
            "performance / lead capture.\n\n"
            "Not pushy — if it's not relevant, ignore. If it is, would 10 minutes this week work?\n\n"
            + COMMON_CTA
        ),
    },
]


# ---------------------------------------------------------------------------
# Picker + filler
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _safe_get(row: sqlite3.Row | dict, key: str, default: Any = None) -> Any:
    """sqlite3.Row neturi .get() metodo. Suvienodinam."""
    try:
        v = row[key]
        return v if v is not None else default
    except (KeyError, IndexError):
        return default


def _build_context(row: sqlite3.Row | dict) -> dict[str, Any]:
    """Surenkam match'eriam reikalingus laukus į vieną dict'ą.

    Naudojam abu — orig row + derived `has_website_url`.
    """
    website_url = _safe_get(row, "website_url")
    return {
        "trading_name": _safe_get(row, "trading_name") or _safe_get(row, "business_name") or "your business",
        "business_name": _safe_get(row, "business_name", ""),
        "industry": _safe_get(row, "industry_keyword", "service"),
        "state": _safe_get(row, "state", "AU"),
        "website_url": website_url or "",
        "website_class": _safe_get(row, "website_class"),
        "tech_stack": _safe_get(row, "tech_stack"),
        "footer_year": _safe_get(row, "footer_year"),
        "mobile_friendly": _safe_get(row, "mobile_friendly"),
        "ssl_valid": _safe_get(row, "ssl_valid"),
        "rating": _safe_get(row, "rating"),
        "review_count": _safe_get(row, "review_count"),
        "has_website_url": bool(website_url and str(website_url).strip()),
    }


def pick_template(row: sqlite3.Row | dict) -> dict[str, Any]:
    """Pirmas match'as iš TEMPLATES sąrašo (priority order). DEFAULT visada match'ina."""
    ctx = _build_context(row)
    for t in TEMPLATES:
        try:
            if t["match"](ctx):
                return t
        except Exception as e:  # noqa: BLE001
            logger.warning("Template %s match failed: %s", t["id"], e)
            continue
    # NEsiekiama (DEFAULT visada True), bet defensive.
    return TEMPLATES[-1]


def _format_safe(template: str, context: dict[str, Any]) -> str:
    """str.format() su default placeholder reikšmėmis — NE crash'inam dėl missing key.

    Pridedam derived placeholders: `age` (years since footer_year), `sender_name`.
    """
    fill = dict(context)

    # Derived: footer age
    fy = context.get("footer_year")
    fill["age"] = (CURRENT_YEAR - fy) if isinstance(fy, int) else "several"

    # Defaults — jei null/empty, fallback į žmogiškai skambantį tekstą.
    if not fill.get("website_url"):
        fill["website_url"] = "your website"
    if not fill.get("tech_stack") or fill["tech_stack"] == "unknown":
        fill["tech_stack"] = "an older platform"
    if not fill.get("footer_year"):
        fill["footer_year"] = "a while back"
    if not fill.get("review_count"):
        fill["review_count"] = "several"
    if not fill.get("industry"):
        fill["industry"] = "service"
    if not fill.get("state"):
        fill["state"] = "AU"

    # Sender placeholder — paliekam kaip {sender_name} jei vartotojas dar nesusicasc'ino.
    fill.setdefault("sender_name", "Tadas")  # default Empirra founder

    # str.format_map su SafeDict (KeyError → palieka {key} originale)
    class _SafeDict(dict):
        def __missing__(self, k: str) -> str:  # type: ignore[override]
            return "{" + k + "}"

    try:
        return template.format_map(_SafeDict(fill))
    except Exception as e:  # noqa: BLE001
        logger.warning("format failed: %s — falling back to raw template", e)
        return template


def generate_angle(row: sqlite3.Row | dict) -> dict[str, str]:
    """Public entry — grąžina {'template_id', 'subject', 'body'}."""
    tmpl = pick_template(row)
    ctx = _build_context(row)
    return {
        "template_id": tmpl["id"],
        "subject": _format_safe(tmpl["subject"], ctx),
        "body": _format_safe(tmpl["body"], ctx),
    }


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def _eligible_for_angle(
    conn: sqlite3.Connection,
    limit: int | None,
    overwrite: bool,
) -> list[sqlite3.Row]:
    """Rows eligible angle generation'ui.

    Default: tik lead'ai be filled angle (`angle_subject IS NULL`).
    Su `overwrite`: visi lead'ai (re-generation).

    Pre-filter:
      - stage_a_status='ok' (kažką žinom apie verslą)
      - business_status != 'CLOSED_PERMANENTLY'
      - au_validation_status != 'not_au'
    """
    where = [
        "e.stage_a_status = 'ok'",
        "(e.business_status IS NULL OR e.business_status != 'CLOSED_PERMANENTLY')",
        "(e.au_validation_status IS NULL OR e.au_validation_status != 'not_au')",
    ]
    if not overwrite:
        where.append("e.angle_subject IS NULL")

    limit_clause = f"LIMIT {int(limit)}" if limit else ""

    sql = f"""
        SELECT l.abn, l.business_name, l.industry_keyword, l.state,
               e.trading_name, e.website_url,
               e.website_class, e.tech_stack, e.footer_year,
               e.mobile_friendly, e.ssl_valid,
               e.rating, e.review_count
        FROM leads l
        JOIN enrichment e ON e.abn = l.abn
        WHERE {' AND '.join(where)}
        ORDER BY e.priority_score DESC NULLS LAST
        {limit_clause}
    """
    return conn.execute(sql).fetchall()


def _store_angle(
    conn: sqlite3.Connection,
    abn: str,
    angle: dict[str, str],
) -> None:
    now = utcnow_iso()
    conn.execute(
        """UPDATE enrichment SET
                angle_template_id  = ?,
                angle_subject      = ?,
                angle_body         = ?,
                angle_generated_at = ?,
                updated_at         = ?
           WHERE abn = ?""",
        (
            angle["template_id"],
            angle["subject"],
            angle["body"],
            now, now,
            abn,
        ),
    )


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
        description="V2-LITE P1.1: Rule-based sales angle generator ($0)."
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Max rows to process (default: all eligible)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Re-generate angles for rows that already have one",
    )
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.dry_run and not args.live:
        print(
            "ERROR: pass either --dry-run or --live.",
            file=sys.stderr,
        )
        return 1

    db_path = args.db or _onedrive_db_fallback()
    if not db_path.exists():
        print(f"ERROR: outreach.db not found at {db_path}", file=sys.stderr)
        return 1

    conn = connect(db_path)
    rows = _eligible_for_angle(conn, args.limit, args.overwrite)

    if not rows:
        print("Nothing to generate. (No eligible rows or all already have angles.)")
        return 0

    print(f"{'[DRY RUN] ' if args.dry_run else ''}Sales angle: {len(rows)} rows")

    counters: dict[str, int] = {}
    for r in rows:
        angle = generate_angle(r)
        counters[angle["template_id"]] = counters.get(angle["template_id"], 0) + 1
        if args.dry_run:
            if counters[angle["template_id"]] <= 1:
                # First example per template
                print(f"\n--- {angle['template_id']}  ({r['trading_name'] or r['business_name']}) ---")
                print(f"SUBJ: {angle['subject']}")
                print(f"BODY:\n{angle['body']}")
        else:
            _store_angle(conn, r["abn"], angle)

    if not args.dry_run:
        conn.commit()

    print(f"\nTemplate distribution:")
    for tid, n in sorted(counters.items(), key=lambda x: -x[1]):
        print(f"  {tid:30s} {n}")
    print(f"\nTotal: {sum(counters.values())} rows {'previewed' if args.dry_run else 'stored'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
