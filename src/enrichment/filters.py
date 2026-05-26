"""Quality gates — filtruoja, kurie lead'ai gauna kurios stage'ą.

Tikslas: nešvaistom $$ ir laiko ant lead'ų, kurių industry/geo/entity_type
neatitinka Empirra ICP, arba kurie jau turi reikalingus duomenis iš
ankstesnio stage'o.

Funkcijos grąžina ABNs (str list'us), kurie eligible kiekvienam stage'ui.
Kiekviena `eligible_for_stage_*` PRIVALO būti idempotent ir saugu pakart.
"""
from __future__ import annotations

import sqlite3


# ---------------------------------------------------------------------------
# INDUSTRY whitelist — Empirra ICP fit ($500 svetainė + AI automation)
# ---------------------------------------------------------------------------
# Service businesses + trades + professional services.
# NEįtraukti: religious, charity, govt, hobby clubs (NULL industry = skip).

INDUSTRY_WHITELIST: tuple[str, ...] = (
    "electrical", "plumbing", "hvac", "landscaping", "cleaning",
    "automotive", "transport", "hospitality", "retail", "real_estate",
    "beauty", "fitness", "healthcare", "construction",
    # Future-add jei importer.py pradės taggit:
    "legal", "accounting", "marketing", "consulting", "trades",
)

# ---------------------------------------------------------------------------
# ENTITY TYPE blacklist — trust/charity struktūros = ne operatoriai
# ---------------------------------------------------------------------------
ENTITY_NAME_BLACKLIST_PATTERNS: tuple[str, ...] = (
    "TRUSTEE FOR",
    "TRUSTEE OF",
    "AS TRUSTEE",
    "CHARITABLE TRUST",
    "FOUNDATION",
    "ASSOCIATION INC",
)

# ---------------------------------------------------------------------------
# WEBSITE blacklist — free-tier / not-real-website signals
# ---------------------------------------------------------------------------
WEBSITE_LOW_INTENT_PATTERNS: tuple[str, ...] = (
    "wixsite.com",
    "squarespace-website.com",
    "weebly.com",
    "business.site",     # Google free GBP-only website
    "godaddysites.com",
    "webnode.com",
    "yola.site",
    "jimdofree.com",
    "blogspot.com",
    "wordpress.com",     # vs self-hosted .org
    "facebook.com",      # FB page used as website
    "instagram.com",
    "linktr.ee",
    "carrd.co",
)


def eligible_for_stage_a(
    conn: sqlite3.Connection,
    limit: int = 1000,
    industries: tuple[str, ...] | None = None,
    states: tuple[str, ...] | None = None,
) -> list[str]:
    """Lead'ai, kuriems verta paleisti Google Places API call'ą.

    Filtras:
      - industry IN whitelist (default: visi 19)
      - has_domain = 0 (be svetainės — ICP)
      - postcode 4-digit AU (1000-9999)
      - gst_status = 'Active' (gyvas verslas)
      - business_name NOT LIKE '%TRUSTEE FOR%' (trust struktūros)
      - state IN states (default: visi 8 AU)
      - enrichment.stage_a_status IS NULL (nerunintas)
    """
    industries = industries or INDUSTRY_WHITELIST
    states = states or ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")

    industries_ph = ",".join("?" * len(industries))
    states_ph = ",".join("?" * len(states))

    blacklist_clauses = " AND ".join(
        f"UPPER(l.business_name) NOT LIKE '%' || ? || '%'"
        for _ in ENTITY_NAME_BLACKLIST_PATTERNS
    )

    # gst_status = 'ACT' (Active, ABR abbreviation), NOT 'Active'
    sql = f"""
        SELECT l.abn
        FROM leads l
        LEFT JOIN enrichment e ON e.abn = l.abn
        WHERE l.has_domain = 0
          AND l.industry_keyword IN ({industries_ph})
          AND l.state IN ({states_ph})
          AND l.gst_status = 'ACT'
          AND l.postcode GLOB '[0-9][0-9][0-9][0-9]'
          AND CAST(l.postcode AS INTEGER) BETWEEN 1000 AND 9999
          AND ({blacklist_clauses})
          AND e.stage_a_status IS NULL
        ORDER BY l.first_seen_at DESC
        LIMIT ?
    """
    params = [
        *industries,
        *states,
        *ENTITY_NAME_BLACKLIST_PATTERNS,
        limit,
    ]
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def eligible_for_stage_b(
    conn: sqlite3.Connection,
    limit: int = 1000,
) -> list[str]:
    """Lead'ai, kurie turi website_url iš Stage A ir NEturi email dar.

    Filtras:
      - stage_a_status = 'ok' (Places call'as sėkmingas)
      - website_url NOT NULL ir NOT low-intent free-tier
      - website_url NOT facebook.com / instagram.com (ne svetainė, o social)
      - contact_email IS NULL (dar neturim email)
      - stage_b_status IS NULL ARBA 'error' (retry tik error'us)
      - au_validation_status != 'not_au' (V2-LITE P0.2 — anti-PROXYTECH).
        NULL ar 'unknown' praeina (legacy data + ambiguous = leidžiam).
    """
    blacklist_clauses = " AND ".join(
        "LOWER(e.website_url) NOT LIKE '%' || ? || '%'"
        for _ in WEBSITE_LOW_INTENT_PATTERNS
    )

    sql = f"""
        SELECT e.abn
        FROM enrichment e
        WHERE e.stage_a_status = 'ok'
          AND e.website_url IS NOT NULL
          AND LENGTH(TRIM(e.website_url)) > 0
          AND ({blacklist_clauses})
          AND e.contact_email IS NULL
          AND (e.stage_b_status IS NULL OR e.stage_b_status = 'error')
          AND (e.au_validation_status IS NULL
               OR e.au_validation_status != 'not_au')
        ORDER BY e.priority_score DESC NULLS LAST, e.updated_at DESC
        LIMIT ?
    """
    params = [*WEBSITE_LOW_INTENT_PATTERNS, limit]
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def eligible_for_stage_c(
    conn: sqlite3.Connection,
    limit: int = 500,
    min_score: int = 50,
) -> list[str]:
    """Lead'ai, kurie po A+B vis dar BE jokio kontakto.

    SerpAPI brangus ($5/1k) → tik top-N highest-priority leads.

    Filtras:
      - stage_a_status = 'ok'
      - contact_email IS NULL
      - phone IS NULL
      - scraped_fb_url IS NULL
      - scraped_ig_url IS NULL
      - priority_score >= min_score (cost gate)
      - stage_c_status IS NULL ARBA 'error'
    """
    sql = """
        SELECT e.abn
        FROM enrichment e
        WHERE e.stage_a_status = 'ok'
          AND e.contact_email IS NULL
          AND e.phone IS NULL
          AND e.scraped_fb_url IS NULL
          AND e.scraped_ig_url IS NULL
          AND COALESCE(e.priority_score, 0) >= ?
          AND (e.stage_c_status IS NULL OR e.stage_c_status = 'error')
        ORDER BY e.priority_score DESC
        LIMIT ?
    """
    return [r[0] for r in conn.execute(sql, (min_score, limit)).fetchall()]


def count_eligible(conn: sqlite3.Connection) -> dict[str, int]:
    """Greitas snapshot — kiek lead'ų laukia kiekvienam stage'ui.

    Naudinga prieš paleidžiant batch'ą, kad operator'us žinotų scope'ą.
    """
    return {
        "stage_a": len(eligible_for_stage_a(conn, limit=1_000_000)),
        "stage_b": len(eligible_for_stage_b(conn, limit=1_000_000)),
        "stage_c": len(eligible_for_stage_c(conn, limit=1_000_000)),
    }
