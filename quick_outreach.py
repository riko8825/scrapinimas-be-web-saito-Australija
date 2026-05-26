"""Quick FB DM outreach — paima N best-fit leads iš outreach.db ir
generuoja paste-ready CSV su FB search hint'u + DM template'u.

Workflow (rankinis, NULIS API spend'o):
    1. Paleisk: python quick_outreach.py --limit 30
    2. Atidaryk output/quick_outreach_YYYYMMDD.csv
    3. Kiekvienai eilutei:
       a) Spustelėk "fb_search_url" — atsidaro DuckDuckGo paieška
       b) Surask verslo FB puslapį (1-2 min)
       c) Atidaryk FB Messenger, paste "dm_message" tekstą
       d) Personalizuok (1-2 sakinius pagal puslapio turinį)
       e) Send
       f) Atžymėk eilutę CSV'e ("sent" stulpelyje)

Rate limit savisaugumui: 30 leads/run = 30 DM/diena. FB rate-limit'as
~30-50 naujų DM/d prieš tag'ina kaip spam. Likimai NULIS.

Filtras default'as: industry IN (high-value AU SMB), state IN (NSW/VIC/QLD —
didžiausi miestai, daugiau pinigų), has_domain=0 (be svetainės — mūsų ICP).

Tracking: outreach.db `outreach` lentelėj atžymės "exported" statusą,
kad pakartotinis paleidimas tų pačių leads negrąžintų.

Run:
    python quick_outreach.py
    python quick_outreach.py --limit 50
    python quick_outreach.py --industries plumbing,electrical --states NSW
    python quick_outreach.py --no-mark   # neatžymėti outreach.db (dry-run)
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# OneDrive folderis kur gyvena outreach.db (vienintelis source of truth)
DEFAULT_DB = Path(
    r"C:\Users\pinig\OneDrive\Stalinis kompiuteris\Automatiomm_empirra"
    r"\abr-data\abr-pipeline\dashboard\outreach.db"
)

# High-value AU SMB niche'os, kur Empirra svetainė + AI automation $500-2k
# tier'e turi natūralią paklausą. Tvarka = paklausos / mokumas dažnumu.
DEFAULT_INDUSTRIES = [
    "electrical",
    "plumbing",
    "construction",
    "landscaping",
    "cleaning",
    "automotive",
    "beauty",
    "healthcare",
]

# Didžiausi miestai = daugiausia mokumo = aukštesnis close rate
DEFAULT_STATES = ["NSW", "VIC", "QLD"]


# Legal suffixes, kurie niekada nebūna FB brand name'e
_LEGAL_NOISE = re.compile(
    r"\b(pty\s*\.?\s*ltd\.?|proprietary\s+limited|pty\s*\.?|ltd\.?|"
    r"limited|inc\.?|incorporated|the\s+trustee\s+for|"
    r"the\s+trustee\s+of|t/a|trading\s+as)\b",
    re.IGNORECASE,
)
_PARENS = re.compile(r"\s*\([^)]*\)\s*")
_PUNCT = re.compile(r"[.,;:!?\"\\/]")
_MULTI_SPACE = re.compile(r"\s+")


_ACRONYM_GAP = re.compile(r"(?<=\b[A-Z])\s+(?=[A-Z]\b)")


def _clean_business_name(name: str) -> str:
    """Pavalyk ABR legal name į FB-friendly brand name.

    Pavyzdžiai:
      'A.R.S. CLEANING GROUP PTY. LTD.' → 'ARS Cleaning Group'
      'NEWCASTLE & LAKE MACQUARIE ELECTRICAL & MAINTENANCE SERVICES PTY LTD'
        → 'Newcastle and Lake Macquarie Electrical and Maintenance Services'
      'THE TRUSTEE FOR SMITH FAMILY TRUST' → 'Smith Family Trust'
    """
    s = name or ""
    s = _PARENS.sub(" ", s)        # pašalink (NSW), (2290), etc.
    s = _LEGAL_NOISE.sub(" ", s)   # pašalink pty ltd / trustee / etc.
    s = s.replace("&", " and ")    # & netaikomas Google query
    s = _PUNCT.sub(" ", s)         # pašalink taškus + kabučius
    s = _MULTI_SPACE.sub(" ", s).strip()
    # Sujungiam vienraidžius akronimus: "A R S" → "ARS"
    while _ACRONYM_GAP.search(s):
        s = _ACRONYM_GAP.sub("", s)
    return s.title()


def _build_google_fb_url(clean_name: str, postcode: str) -> str:
    """Google paieška su site:facebook.com filtru — geriausias variantas.

    Google indeksuoja FB puslapius daug giliau nei DDG. Neuždeda quote'ų
    aplink pavadinimą (per griežta) — tiesiog pateikia žodžius + postcode.
    """
    query = f"{clean_name} {postcode} site:facebook.com"
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"


def _build_google_general_url(clean_name: str, postcode: str) -> str:
    """Google bendra paieška (website, FB, IG, Google Maps) viename.

    Naudinga kai FB neturi, BET verslo turi website / IG / Google Business.
    Operatorius vienu žvilgsniu mato visus channels.
    """
    query = f"{clean_name} {postcode} australia"
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"


def _build_fb_native_url(clean_name: str) -> str:
    """Facebook native page search — atidaro FB su login + paieška.

    Jei operatorius jau login'intas FB Empirra Page'oje, tai
    greičiausias kelias. Be login'o — peradresuoja į FB log in.
    """
    query = clean_name
    return f"https://www.facebook.com/search/pages/?q={urllib.parse.quote_plus(query)}"


def _build_dm_template(clean_name: str, industry: str) -> str:
    """Personalizuotas FB DM template'as su jau pavalytu pavadinimu.

    clean_name jau be Pty Ltd / & / taškų, Title Case (pvz. "Ars Cleaning Group").
    Tu PRIVALAI personalizuoti {{ADD_1_DETAIL_FROM_FB_PAGE}} prieš siunčiant.
    """
    industry_hook = {
        "electrical": "electrical work",
        "plumbing": "plumbing services",
        "construction": "construction projects",
        "landscaping": "landscaping work",
        "cleaning": "cleaning services",
        "automotive": "automotive work",
        "beauty": "beauty services",
        "healthcare": "healthcare services",
    }.get(industry, "your services")

    return (
        f"Hi {clean_name} team,\n\n"
        f"I noticed you do {industry_hook} but don't have a website yet — "
        f"{{{{ADD_1_DETAIL_FROM_FB_PAGE}}}} caught my eye.\n\n"
        f"I help small AU businesses like yours get a clean, "
        f"mobile-friendly website up in 2-3 days for AUD $500 "
        f"(fully owned by you, no monthly fees).\n\n"
        f"Would a quick 10-min chat this week make sense? "
        f"No pressure — just want to see if I can help.\n\n"
        f"Cheers,\nRokas (Empirra)"
    )


def _select_leads(
    db: sqlite3.Connection,
    industries: list[str],
    states: list[str],
    limit: int,
    exclude_exported: bool,
) -> list[sqlite3.Row]:
    """Pasiimk N best-fit leads su filtru.

    Filtras:
      - has_domain=0 (be svetainės — mūsų ICP)
      - industry_keyword IN (...) (high-value niche)
      - state IN (...) (didžiausi miestai)
      - jei exclude_exported: NOT EXISTS outreach.status='exported'
    """
    industries_ph = ",".join("?" * len(industries))
    states_ph = ",".join("?" * len(states))

    exclude_clause = ""
    if exclude_exported:
        exclude_clause = """
        AND NOT EXISTS (
            SELECT 1 FROM outreach o
            WHERE o.abn = leads.abn AND o.status = 'exported'
        )"""

    sql = f"""
        SELECT abn, business_name, state, postcode, industry_keyword
        FROM leads
        WHERE has_domain = 0
          AND industry_keyword IN ({industries_ph})
          AND state IN ({states_ph})
          {exclude_clause}
        ORDER BY RANDOM()
        LIMIT ?
    """
    return db.execute(sql, [*industries, *states, limit]).fetchall()


def _mark_exported(db: sqlite3.Connection, abns: list[str]) -> int:
    """Atžymėk outreach.status='exported' šiems ABNs, kad nepasikartotų.

    outreach lentelė jau turi eilutes visiems leads (default status='new'),
    todėl darome UPDATE, ne INSERT. updated_at + exported_at abu užpildomi.
    """
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for abn in abns:
        cur = db.execute(
            """UPDATE outreach
               SET status='exported',
                   exported_at=?,
                   updated_at=?
               WHERE abn=?""",
            (now, now, abn),
        )
        if cur.rowcount == 0:
            # Lead'as nepatekęs į outreach lentelę (rare) — pridėk
            db.execute(
                """INSERT INTO outreach (abn, status, exported_at, updated_at)
                   VALUES (?, 'exported', ?, ?)""",
                (abn, now, now),
            )
        n += 1
    db.commit()
    return n


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Paimk N best-fit AU SMB leads į CSV su FB DM template'ais.",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"SQLite path (default: OneDrive outreach.db)")
    parser.add_argument("--limit", type=int, default=30,
                        help="Kiek leads paimti (default: 30, atitinka FB safe DM/d)")
    parser.add_argument("--industries", type=str,
                        default=",".join(DEFAULT_INDUSTRIES),
                        help="Comma-separated industries filter")
    parser.add_argument("--states", type=str,
                        default=",".join(DEFAULT_STATES),
                        help="Comma-separated states filter (NSW,VIC,QLD,...)")
    parser.add_argument("--no-mark", action="store_true",
                        help="Dry-run: neatžymėti outreach.db (gali paleisti dar kartą)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="Output CSV path (default: output/quick_outreach_YYYYMMDD.csv)")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: outreach.db nerasta: {args.db}", file=sys.stderr)
        print("Pakeisk --db argumentą arba patikrink OneDrive sync state.",
              file=sys.stderr)
        return 1

    industries = [s.strip().lower() for s in args.industries.split(",") if s.strip()]
    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]

    output_path = args.output or OUTPUT_DIR / (
        f"quick_outreach_{datetime.now():%Y%m%d_%H%M}.csv"
    )

    db = sqlite3.connect(args.db)
    db.row_factory = sqlite3.Row

    # Patikrink ar outreach lentelė turi exported_at stulpelį, jei ne — pridėk
    cols = {r[1] for r in db.execute("PRAGMA table_info(outreach)").fetchall()}
    if "exported_at" not in cols:
        try:
            db.execute("ALTER TABLE outreach ADD COLUMN exported_at TEXT")
            db.commit()
            print("Info: pridėtas outreach.exported_at stulpelis")
        except sqlite3.OperationalError as e:
            print(f"WARN: negalėjau pridėti exported_at: {e}", file=sys.stderr)

    leads = _select_leads(
        db, industries, states, args.limit, exclude_exported=not args.no_mark
    )

    if not leads:
        print("Nieko nerasta su tavo filtru. Pabandyk plėsti --industries arba --states.")
        return 0

    rows = []
    for lead in leads:
        raw_name = lead["business_name"]
        clean_name = _clean_business_name(raw_name)
        rows.append({
            "abn": lead["abn"],
            "business_name": raw_name,
            "clean_name": clean_name,   # FB-friendly versija DM'ui
            "industry": lead["industry_keyword"],
            "state": lead["state"],
            "postcode": lead["postcode"],
            # 3 paieškos link'ai geriausi → atsarginiai
            "google_fb_url": _build_google_fb_url(clean_name, lead["postcode"]),
            "google_general_url": _build_google_general_url(
                clean_name, lead["postcode"]
            ),
            "fb_native_url": _build_fb_native_url(clean_name),
            "dm_message": _build_dm_template(
                clean_name, lead["industry_keyword"] or "your industry"
            ),
            "fb_url_found": "",  # rankiniu užpildysi po paieškos
            "sent": "",          # rankiniu žymėk "yes" po DM siuntimo
            "reply": "",         # rankiniu žymėk "yes" jei atsakė
            "notes": "",
        })

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    if not args.no_mark:
        marked = _mark_exported(db, [r["abn"] for r in rows])
        print(f"Atžymėta outreach.db: {marked} ABNs su status='exported'")

    db.close()

    print(f"\n✓ Sukurta: {output_path}")
    print(f"  Leads: {len(rows)}")
    print(f"  Industries: {', '.join(sorted({r['industry'] for r in rows}))}")
    print(f"  States: {', '.join(sorted({r['state'] for r in rows}))}")
    print(f"\nKitas žingsnis:")
    print(f"  1. Atidaryk CSV Excel'iuje / Google Sheets")
    print(f"  2. Spausk pirmą fb_search_url — surask FB puslapį")
    print(f"  3. Personalizuok dm_message {{{{PLACEHOLDER}}}}us")
    print(f"  4. Paste į FB Messenger, send, atžymėk 'sent' stulpelį")
    print(f"  5. Maks 30/dieną FB safe limit'ui")
    return 0


if __name__ == "__main__":
    sys.exit(main())
