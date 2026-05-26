"""Priority scoring — kuris lead'as worth Stage C ($5/1k brangiausias).

Score 0-100 (didesnis = aukštesnis prioritetas):
  - industry weight  (0-40 pts) — kurios industrijos labiau moka už svetainę
  - state weight     (0-30 pts) — kuriose state'se daugiau mokumo
  - name quality     (0-20 pts) — ar brand name skamba kaip real verslas
  - active signal    (0-10 pts) — GST Active + recent first_seen_at

Naudojama eligible_for_stage_c() min_score filter'ui ir Stage B ordering'ui.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# INDUSTRY weights — empirinis empiri Empirra ICP fit
# ---------------------------------------------------------------------------
# High: pinigų pilni service business + lengva parodyti ROI
# Mid: stabilūs verslai, mid-budget
# Low: low-margin arba seasonal
INDUSTRY_WEIGHTS: dict[str, int] = {
    "legal":        40,   # high billable hour, $$ klientai
    "accounting":   40,
    "healthcare":   38,   # dental + chiro = mokumo
    "real_estate":  35,
    "automotive":   32,   # repair shops, mid budget
    "electrical":   30,
    "plumbing":     30,
    "hvac":         28,
    "construction": 28,
    "marketing":    28,
    "consulting":   28,
    "beauty":       25,
    "fitness":      25,
    "hospitality":  22,   # cafes — žemesnis margin
    "landscaping":  22,
    "trades":       20,
    "transport":    18,   # logistics jau dažnai turi B2B kontaktus
    "cleaning":     18,
    "retail":       15,   # E-commerce competes hard, žemesnis website value
}

# ---------------------------------------------------------------------------
# STATE weights — economic activity proxy
# ---------------------------------------------------------------------------
STATE_WEIGHTS: dict[str, int] = {
    "NSW": 30,   # Sydney + Newcastle + Wollongong
    "VIC": 28,   # Melbourne
    "QLD": 25,   # Brisbane + Gold Coast
    "WA":  22,   # Perth
    "SA":  18,   # Adelaide
    "ACT": 18,   # Canberra (govt-heavy = stable)
    "TAS": 12,
    "NT":  10,
}


# ---------------------------------------------------------------------------
# NAME QUALITY — ar verslas atrodo serious
# ---------------------------------------------------------------------------
_NAME_QUALITY_SIGNALS = {
    "has_brand_word": (
        # Brand-like žodžiai (ne tik vardas + pavardė)
        r"\b(group|services|solutions|industries|works|co\.?|company|"
        r"enterprises|holdings|partners)\b"
    ),
    "is_just_personal_name": (
        # Tik vardas + pavardė (sole trader, mažas budget)
        r"^[A-Z]+\s+[A-Z]+$"
    ),
}


def name_quality_score(business_name: str) -> int:
    """0-20 pts pagal name quality signals.

    +10 jei turi brand žodį (Group/Services/Solutions/Co)
    +5  jei multi-word (≥3 words, ne tik vardas+pavardė)
    +5  jei NEturi visų-kapitalų sole trader name pattern
    """
    if not business_name:
        return 0

    name = business_name.upper().strip()
    score = 0

    if re.search(_NAME_QUALITY_SIGNALS["has_brand_word"], name, re.IGNORECASE):
        score += 10

    words = [w for w in re.split(r"\s+", name) if w]
    if len(words) >= 3:
        score += 5

    if not re.match(_NAME_QUALITY_SIGNALS["is_just_personal_name"], name):
        score += 5

    return min(score, 20)


def priority_score(
    industry_keyword: str | None,
    state: str | None,
    business_name: str | None,
    gst_status: str | None = None,
) -> int:
    """Pilnas score 0-100 lead'ui pagal Empirra ICP fit.

    Naudojama:
      - Stage C eligibility (min_score=50 default)
      - Stage B ordering (highest priority first)
      - Dashboard sorting
    """
    industry_pts = INDUSTRY_WEIGHTS.get(industry_keyword or "", 0)
    state_pts = STATE_WEIGHTS.get(state or "", 0)
    name_pts = name_quality_score(business_name or "")
    # ABR uses 'ACT' (active), 'CAN' (cancelled), 'NON' (non-registered)
    active_pts = 10 if gst_status == "ACT" else 0

    return min(industry_pts + state_pts + name_pts + active_pts, 100)
