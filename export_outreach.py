"""Export enriched leads from outreach.db į CSV outreach'ui.

Paima leads, kuriuos jau enrichino Stage A+B (turi bent vieną iš:
phone, email, FB URL, IG URL), pagal priority_score DESC, į paste-ready CSV.

CSV stulpeliai (operator'iui dirbti):
    - abn, business_name, trading_name, industry, state, postcode
    - phone, email (PILDOMI iš Stage A + B)
    - website, fb_url, ig_url, linkedin_url
    - formatted_address (iš Google Places)
    - priority_score
    - dm_message (Empirra cold template, jau personalizuotas trading_name + industry)
    - email_subject (suggested subject line)
    - email_body (paste-ready body)
    - sent (rankiniu žymėk "yes" po siuntimo)
    - reply (rankiniu žymėk "yes" jei atsakė)
    - notes

Run:
    python export_outreach.py --limit 10
    python export_outreach.py --limit 50 --channel email
    python export_outreach.py --limit 100 --min-score 60
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


# ---------------------------------------------------------------------------
# OUTREACH TEMPLATES
# ---------------------------------------------------------------------------

def _industry_hook(industry: str | None) -> str:
    """Industry-specific opening line."""
    hooks = {
        "electrical": "your electrical work",
        "plumbing": "your plumbing services",
        "construction": "your construction projects",
        "landscaping": "your landscaping work",
        "cleaning": "your cleaning services",
        "automotive": "your automotive work",
        "transport": "your transport services",
        "hospitality": "your business",
        "retail": "your business",
        "real_estate": "your real estate work",
        "beauty": "your beauty services",
        "fitness": "your fitness business",
        "healthcare": "your practice",
        "hvac": "your HVAC services",
    }
    return hooks.get(industry or "", "your business")


def _build_dm_message(trading_name: str, industry: str | None) -> str:
    """FB Messenger DM — kazualus, paste-ready su 1 placeholder'iu."""
    name = trading_name or "team"
    hook = _industry_hook(industry)
    return (
        f"Hi {name} team,\n\n"
        f"Just came across your page — {{{{ADD_DETAIL_FROM_THEIR_PAGE}}}} "
        f"really stood out.\n\n"
        f"I noticed you do {hook} but don't have a full website yet. "
        f"I help small AU businesses like yours get a clean, mobile-friendly "
        f"site up in 2-3 days for AUD $500 (fully yours, no monthly fees).\n\n"
        f"Would a quick 10-min chat make sense? Happy to send a few examples.\n\n"
        f"Cheers,\nRokas (Empirra)"
    )


def _build_email_subject(trading_name: str, industry: str | None) -> str:
    """Email subject — short, no spam triggers, no caps, no $$."""
    if industry in ("electrical", "plumbing", "hvac", "construction", "automotive"):
        return f"Quick question about {trading_name}'s online presence"
    if industry in ("healthcare", "beauty", "legal", "accounting"):
        return f"A website for {trading_name}?"
    return f"Question about {trading_name}"


def _build_email_body(trading_name: str, industry: str | None) -> str:
    """Email body — professional + casual, AUD $500 transparent."""
    name = trading_name or "there"
    hook = _industry_hook(industry)
    return (
        f"Hi {name} team,\n\n"
        f"I came across your business and noticed you don't have a website "
        f"yet — just wanted to reach out.\n\n"
        f"I help small Australian businesses with {hook} get a clean, "
        f"mobile-friendly site online in 2-3 days. One-off cost of AUD $500, "
        f"no monthly fees, you own everything.\n\n"
        f"If you'd like to chat about it (no pressure either way), reply to "
        f"this email or check out a few examples at empirra.com.\n\n"
        f"Cheers,\n"
        f"Rokas\n"
        f"Empirra | empirra.com\n"
    )


# ---------------------------------------------------------------------------
# QUERY
# ---------------------------------------------------------------------------

def _select_enriched_leads(
    conn: sqlite3.Connection,
    limit: int,
    min_score: int,
    channel_filter: str | None,
    exclude_exported: bool,
) -> list[sqlite3.Row]:
    """Get top-priority enriched leads ready for outreach.

    Filters:
      - stage_a_status='ok' (Places confirmed exists)
      - bent vienas iš: phone, contact_email, scraped_fb_url, scraped_ig_url
      - priority_score >= min_score
      - jei channel_filter='email': contact_email NOT NULL
      - jei channel_filter='phone': phone NOT NULL
      - jei channel_filter='fb': scraped_fb_url NOT NULL
      - jei exclude_exported: skip jau exported (outreach.status='exported')
    """
    where_clauses = ["e.stage_a_status = 'ok'"]
    where_clauses.append("e.priority_score >= ?")
    params: list = [min_score]

    if channel_filter == "email":
        where_clauses.append("e.contact_email IS NOT NULL")
    elif channel_filter == "phone":
        where_clauses.append("e.phone IS NOT NULL")
    elif channel_filter == "fb":
        where_clauses.append("e.scraped_fb_url IS NOT NULL")
    else:
        # ANY channel
        where_clauses.append(
            "(e.contact_email IS NOT NULL OR e.phone IS NOT NULL "
            "OR e.scraped_fb_url IS NOT NULL OR e.scraped_ig_url IS NOT NULL)"
        )

    if exclude_exported:
        where_clauses.append(
            "NOT EXISTS (SELECT 1 FROM outreach o "
            "WHERE o.abn = e.abn AND o.status = 'exported')"
        )

    where_sql = " AND ".join(where_clauses)

    sql = f"""
        SELECT
            l.abn, l.business_name, l.industry_keyword,
            l.state, l.postcode,
            e.trading_name, e.formatted_address,
            e.phone, e.contact_email,
            e.website_url, e.scraped_fb_url, e.scraped_ig_url, e.linkedin_url,
            e.priority_score
        FROM enrichment e
        JOIN leads l ON l.abn = e.abn
        WHERE {where_sql}
        ORDER BY e.priority_score DESC, RANDOM()
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _mark_exported(conn: sqlite3.Connection, abns: list[str]) -> int:
    """Mark outreach.status='exported' to prevent duplicates across runs."""
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for abn in abns:
        cur = conn.execute(
            """UPDATE outreach
               SET status='exported', exported_at=?, updated_at=?
               WHERE abn=?""",
            (now, now, abn),
        )
        if cur.rowcount == 0:
            conn.execute(
                """INSERT INTO outreach (abn, status, exported_at, updated_at)
                   VALUES (?, 'exported', ?, ?)""",
                (abn, now, now),
            )
        n += 1
    conn.commit()
    return n


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
        description="Export top-priority enriched leads į CSV outreach'ui."
    )
    parser.add_argument("--limit", type=int, default=10,
                        help="Kiek leads paimti (default: 10)")
    parser.add_argument("--min-score", type=int, default=50,
                        help="Min priority_score (default: 50)")
    parser.add_argument(
        "--channel", choices=["any", "email", "phone", "fb"], default="any",
        help="Filter by channel availability (default: any)",
    )
    parser.add_argument("--no-mark", action="store_true",
                        help="Dry-run — neatžymėti outreach.status='exported'")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output CSV path (default: output/outreach_ready_*.csv)")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args(argv)

    db_path = args.db or _onedrive_db_fallback()
    conn = connect(db_path)

    channel = None if args.channel == "any" else args.channel
    leads = _select_enriched_leads(
        conn, args.limit, args.min_score, channel,
        exclude_exported=not args.no_mark,
    )

    if not leads:
        print(
            f"Nieko nerasta. Pabandyk --min-score sumažinti (dabar {args.min_score}) "
            f"arba --channel any."
        )
        return 0

    output_path = args.output or OUTPUT_DIR / (
        f"outreach_ready_{datetime.now():%Y%m%d_%H%M}.csv"
    )

    rows = []
    for lead in leads:
        trading = lead["trading_name"] or lead["business_name"]
        industry = lead["industry_keyword"]
        rows.append({
            "abn": lead["abn"],
            "business_name": lead["business_name"],
            "trading_name": trading,
            "industry": industry,
            "state": lead["state"],
            "postcode": lead["postcode"],
            "address": lead["formatted_address"] or "",
            "priority_score": lead["priority_score"],
            "phone": lead["phone"] or "",
            "email": lead["contact_email"] or "",
            "website": lead["website_url"] or "",
            "fb_url": lead["scraped_fb_url"] or "",
            "ig_url": lead["scraped_ig_url"] or "",
            "linkedin_url": lead["linkedin_url"] or "",
            "email_subject": (
                _build_email_subject(trading, industry)
                if lead["contact_email"] else ""
            ),
            "email_body": (
                _build_email_body(trading, industry)
                if lead["contact_email"] else ""
            ),
            "dm_message": (
                _build_dm_message(trading, industry)
                if lead["scraped_fb_url"] else ""
            ),
            "sent": "",
            "reply": "",
            "notes": "",
        })

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    if not args.no_mark:
        marked = _mark_exported(conn, [r["abn"] for r in rows])
        print(f"Atžymėta outreach.db: {marked} ABNs status='exported'")
    conn.close()

    # Stats
    n_email = sum(1 for r in rows if r["email"])
    n_phone = sum(1 for r in rows if r["phone"])
    n_fb = sum(1 for r in rows if r["fb_url"])
    n_full = sum(1 for r in rows if r["email"] and r["phone"] and r["fb_url"])

    print(
        f"\n✓ Sukurta: {output_path}\n"
        f"  Leads: {len(rows)}\n"
        f"  Su email:    {n_email}/{len(rows)}\n"
        f"  Su phone:    {n_phone}/{len(rows)}\n"
        f"  Su FB:       {n_fb}/{len(rows)}\n"
        f"  Su VISAIS 3: {n_full}/{len(rows)}\n"
    )
    print("Kitas žingsnis:")
    print("  1. Atidaryk CSV (Excel ar Google Sheets)")
    print("  2. 5-10 lead'ams: paimk email_subject + email_body, siųsk per Gmail")
    print("  3. Likusiems su FB URL: paimk dm_message, paste į FB Messenger")
    print("  4. Atžymėk 'sent' stulpelį po siuntimo")
    print("  5. Po 3-7 dienų — pasakyk man kiek reply gavau")
    return 0


if __name__ == "__main__":
    sys.exit(main())
