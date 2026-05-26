"""V2-LITE Top N "gold leads" CSV export'as su pain-signal scoring.

Skirtumas nuo `export_outreach.py`:
  - Naudoja scoring_v2 (pain-signal model, ne tik ICP fit)
  - Kiekvienas lead'as turi `score_breakdown` stulpelį (auditable: kodėl 167 pt)
  - Filtruoja CLOSED_PERMANENTLY (Google žinia, biznis miręs)
  - Filtruoja au_validation_status='not_au' (anti-PROXYTECH)
  - Default: top 50

Run:
    python export_gold_leads.py
    python export_gold_leads.py --limit 100
    python export_gold_leads.py --limit 50 --min-score 100
    python export_gold_leads.py --limit 50 --require-contact   (must turėti phone/email/FB/IG)
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT))
from dashboard.db import connect, default_db_path  # noqa: E402
from src.enrichment.scoring_v2 import score_v2  # noqa: E402


CSV_COLUMNS = (
    "rank",
    "abn",
    "business_name",
    "trading_name",
    "industry",
    "state",
    "postcode",
    "v2_score",
    "icp_base",
    "channel_pts",
    "review_pts",
    "stale_pts",
    "revenue_pts",
    "status_pts",
    "has_website",
    "website_class",
    "tech_stack",
    "footer_year",
    "mobile_friendly",
    "ssl_valid",
    "rating",
    "review_count",
    "business_status",
    "phone",
    "email",
    "website_url",
    "fb_url",
    "ig_url",
    "formatted_address",
    "au_validation_status",
    "score_reasons",
)


def _onedrive_db_fallback() -> Path:
    local = default_db_path()
    if local.exists():
        return local
    onedrive = Path(
        r"C:\Users\pinig\OneDrive\Stalinis kompiuteris\Automatiomm_empirra"
        r"\abr-data\abr-pipeline\dashboard\outreach.db"
    )
    return onedrive if onedrive.exists() else local


def fetch_candidates(
    conn: sqlite3.Connection,
    require_contact: bool,
    exclude_closed: bool,
    exclude_not_au: bool,
) -> list[sqlite3.Row]:
    """Užkrauna visus lead'us, kurie potencialiai gali tapti gold lead.

    Pre-filter (SQL):
      - leads JOIN enrichment ON abn
      - stage_a_status='ok' (kažką žinom)
      - CLOSED_PERMANENTLY (jei exclude_closed) → skip
      - not_au (jei exclude_not_au) → skip
      - jei require_contact → bent vienas iš phone/email/fb/ig must be NOT NULL

    Score'inam Python pusėje (NULL-safe + auditable trail).
    """
    where: list[str] = ["e.stage_a_status = 'ok'"]
    if exclude_closed:
        where.append(
            "(e.business_status IS NULL OR e.business_status != 'CLOSED_PERMANENTLY')"
        )
    if exclude_not_au:
        where.append(
            "(e.au_validation_status IS NULL OR e.au_validation_status != 'not_au')"
        )
    if require_contact:
        where.append(
            "(e.phone IS NOT NULL OR e.contact_email IS NOT NULL "
            " OR e.scraped_fb_url IS NOT NULL OR e.scraped_ig_url IS NOT NULL)"
        )

    sql = f"""
        SELECT l.abn, l.business_name, l.industry_keyword, l.state, l.postcode,
               l.entity_type, l.gst_status, l.has_domain,
               e.trading_name, e.phone, e.contact_email, e.website_url,
               e.scraped_fb_url, e.scraped_ig_url, e.formatted_address,
               e.rating, e.review_count, e.business_status,
               e.au_validation_status,
               e.website_class, e.tech_stack, e.footer_year,
               e.mobile_friendly, e.ssl_valid
        FROM leads l
        JOIN enrichment e ON e.abn = l.abn
        WHERE {' AND '.join(where)}
    """
    return conn.execute(sql).fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="V2-LITE Top N gold leads export (pain-signal scoring)."
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--min-score", type=int, default=0)
    parser.add_argument(
        "--require-contact", action="store_true",
        help="Only lead'ai su bent vienu kontaktu (phone/email/fb/ig)",
    )
    parser.add_argument(
        "--include-closed", action="store_true",
        help="Include CLOSED_PERMANENTLY (NE rekomenduoju)",
    )
    parser.add_argument(
        "--include-not-au", action="store_true",
        help="Include not_au validation (NE rekomenduoju)",
    )
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output CSV path (default: output/gold_leads_YYYYMMDD_HHMM.csv)",
    )
    args = parser.parse_args(argv)

    db_path = args.db or _onedrive_db_fallback()
    if not db_path.exists():
        print(f"ERROR: outreach.db not found at {db_path}", file=sys.stderr)
        return 1
    conn = connect(db_path)

    rows = fetch_candidates(
        conn,
        require_contact=args.require_contact,
        exclude_closed=not args.include_closed,
        exclude_not_au=not args.include_not_au,
    )
    print(f"Candidates after pre-filter: {len(rows)}")

    if not rows:
        print("Nothing matched. Try removing --require-contact or lowering --min-score.")
        return 0

    # Score all
    scored: list[tuple[int, dict, sqlite3.Row]] = []
    for r in rows:
        b = score_v2(
            industry_keyword=r["industry_keyword"],
            state=r["state"],
            business_name=r["business_name"],
            gst_status=r["gst_status"],
            entity_type=r["entity_type"],
            has_domain=r["has_domain"],
            website_url=r["website_url"],
            website_class=r["website_class"],
            rating=r["rating"],
            review_count=r["review_count"],
            business_status=r["business_status"],
            footer_year=r["footer_year"],
            tech_stack=r["tech_stack"],
            mobile_friendly=r["mobile_friendly"],
            ssl_valid=r["ssl_valid"],
        )
        if b.total < args.min_score:
            continue
        scored.append((b.total, {
            "v2_score": b.total,
            "icp_base": b.base_icp,
            "channel_pts": b.channel_avail,
            "review_pts": b.review_signal,
            "stale_pts": b.stale_website,
            "revenue_pts": b.revenue_proxy,
            "status_pts": b.business_status,
            "score_reasons": " | ".join(b.reasons),
        }, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:args.limit]
    print(f"Scored: {len(scored)} above min_score={args.min_score}, taking top {len(top)}")

    if not top:
        print("No leads passed min_score gate.")
        return 0

    # Quick distribution print
    dist = {}
    for s, _, _ in top:
        bucket = (s // 25) * 25
        dist[bucket] = dist.get(bucket, 0) + 1
    print("Score distribution (top N):")
    for b in sorted(dist.keys(), reverse=True):
        print(f"  {b:3d}-{b+24:3d}: {'#' * dist[b]}  ({dist[b]})")

    # Write CSV
    if args.output:
        out_path = args.output
    else:
        out_path = OUTPUT_DIR / f"gold_leads_{datetime.now():%Y%m%d_%H%M}.csv"

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for rank, (score, score_dict, row) in enumerate(top, start=1):
            writer.writerow({
                "rank": rank,
                "abn": row["abn"],
                "business_name": row["business_name"],
                "trading_name": row["trading_name"] or "",
                "industry": row["industry_keyword"] or "",
                "state": row["state"] or "",
                "postcode": row["postcode"] or "",
                "v2_score": score,
                "icp_base": score_dict["icp_base"],
                "channel_pts": score_dict["channel_pts"],
                "review_pts": score_dict["review_pts"],
                "stale_pts": score_dict["stale_pts"],
                "revenue_pts": score_dict["revenue_pts"],
                "status_pts": score_dict["status_pts"],
                "has_website": "yes" if (row["website_url"] and row["website_url"].strip()) else "no",
                "website_class": row["website_class"] if row["website_class"] is not None else "",
                "tech_stack": row["tech_stack"] or "",
                "footer_year": row["footer_year"] if row["footer_year"] is not None else "",
                "mobile_friendly": row["mobile_friendly"] if row["mobile_friendly"] is not None else "",
                "ssl_valid": row["ssl_valid"] if row["ssl_valid"] is not None else "",
                "rating": row["rating"] if row["rating"] is not None else "",
                "review_count": row["review_count"] if row["review_count"] is not None else "",
                "business_status": row["business_status"] or "",
                "phone": row["phone"] or "",
                "email": row["contact_email"] or "",
                "website_url": row["website_url"] or "",
                "fb_url": row["scraped_fb_url"] or "",
                "ig_url": row["scraped_ig_url"] or "",
                "formatted_address": row["formatted_address"] or "",
                "au_validation_status": row["au_validation_status"] or "",
                "score_reasons": score_dict["score_reasons"],
            })

    print(f"\nCSV: {out_path}")
    print(f"Rows: {len(top)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
