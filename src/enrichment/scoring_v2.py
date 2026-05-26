"""V2-LITE pain-signal scoring (sesija #9).

Skirtumas nuo `scoring.py`: tas vertina LEADO ICP fit'ą (industry/state/name).
Šis vertina **SALES OPPORTUNITY** kombinuojant ICP fit + pain signals iš
Stage A (rating, reviews, business_status) ir P0.3 classifier (website_class,
mobile_friendly, footer_year, tech_stack).

Max ~200 pts. Naudojam SQL'e ORDER BY ar Python sort'inimui Top-N gold leads'ams.

NULL-safe: kiekvienas lead'as gali turėti dalį laukų NULL (esama 380 leads
neturi rating/reviews, nes Stage A FieldMask buvo siauresnis). NULL'us
laikom kaip 0 (no contribution), NE -∞.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.enrichment.scoring import priority_score as base_priority_score


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

@dataclass
class ScoreBreakdown:
    """Auditable score breakdown — pasakoma kodėl lead turi tiek pt."""
    base_icp: int = 0          # 0-100 (industry/state/name/gst — iš scoring.py)
    channel_avail: int = 0     # 0-40 (no_website / weak website)
    review_signal: int = 0     # 0-30 (review_count + rating combo)
    business_status: int = 0   # 0/-100 (CLOSED_PERMANENTLY = exclude)
    revenue_proxy: int = 0     # 0-10 (entity_type PTY LTD)
    stale_website: int = 0     # 0-40 (footer year / legacy stack pain)
    reasons: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.base_icp
            + self.channel_avail
            + self.review_signal
            + self.business_status
            + self.revenue_proxy
            + self.stale_website
        )


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------

def _channel_score(
    has_domain: int | None,
    website_class: int | None,
    has_website_url: bool,
) -> tuple[int, str]:
    """0-40 pts pagal kanalo prieinamumą / kokybę.

    Logikos prioritetas:
      website_class — TRUTH (po classifier run'o)
      has_website_url (iš Stage A Places) — TRUTH be class'o
      has_domain — LEGACY ABR signal (gali būti out-of-date)

    Skalė:
      truly no website (no class + no URL + has_domain=0) → 40
      website_class=1 (dead)         → 35
      website_class=2 (bad/outdated) → 30
      website_class=3 (modern)       → 0 (hard sale)
      website_url yra, NEclassified  → 10
    """
    if website_class == 1:
        return 35, "dead website"
    if website_class == 2:
        return 30, "bad/outdated website"
    if website_class == 3:
        return 0, "modern website (hard sale)"
    # Class NULL → fallback į url existence
    if has_website_url:
        return 10, "website not classified"
    if has_domain == 0:
        return 40, "no website"
    return 10, "website state unknown"


def _review_score(
    rating: float | None,
    review_count: int | None,
) -> tuple[int, str]:
    """0-30 pts — aktyvus verslas su geru reputation.

    review_count proves verslas TIKRAI veikia (ne zombie).
    rating proves verslas NĖRA dead-on-arrival prastoms paslaugoms.

    Žemos rating'os (<3.5) = SKIP (NE pretenderis Empirra svetainei).
    """
    if rating is None and review_count is None:
        return 0, "no review data"

    rc = review_count or 0
    r = rating or 0.0

    if rc < 5:
        return 0, f"only {rc} reviews"

    pts = 0
    if rc >= 50:
        pts += 15
    elif rc >= 20:
        pts += 10
    elif rc >= 10:
        pts += 5

    if r >= 4.5:
        pts += 15
    elif r >= 4.0:
        pts += 10
    elif r >= 3.5:
        pts += 5
    elif r > 0 and r < 3.5:
        # Aktyvus, bet blogos rating'os — NE Empirra target ($ wasted).
        return 0, f"low rating {r}"

    return min(pts, 30), f"reviews={rc} rating={r}"


def _business_status_score(business_status: str | None) -> tuple[int, str]:
    """Hard exclude jei CLOSED_PERMANENTLY (Google žinia, kad biznis miręs)."""
    if business_status == "CLOSED_PERMANENTLY":
        return -100, "CLOSED_PERMANENTLY"
    if business_status == "CLOSED_TEMPORARILY":
        return -20, "CLOSED_TEMPORARILY"
    return 0, ""


def _revenue_proxy_score(entity_type: str | None) -> tuple[int, str]:
    """+10 pts jei incorporated (PTY LTD = paying business)."""
    if not entity_type:
        return 0, ""
    et = entity_type.upper()
    if "PTY" in et or "PROPRIETARY" in et or "LIMITED" in et:
        return 10, "incorporated (PTY LTD)"
    return 0, et[:30]


def _stale_website_score(
    footer_year: int | None,
    tech_stack: str | None,
    mobile_friendly: int | None,
    ssl_valid: int | None,
) -> tuple[int, str]:
    """0-40 pts — PAIN signals iš classifier.

    Sumažėjantis grįžimas:
      footer_year extreme stale (<2018) → 20 pts
      legacy/free stack (wix/godaddysites/weebly) → 15 pts
      no mobile_friendly → 10 pts (jau yra penalty channel_score'e bet add'inam ekstra)
      no SSL → 5 pts
    """
    if footer_year is None and tech_stack is None:
        return 0, "no classifier data"

    pts = 0
    reasons: list[str] = []

    if footer_year is not None:
        if footer_year < 2018:
            pts += 20
            reasons.append(f"footer={footer_year}")
        elif footer_year < 2022:
            pts += 10
            reasons.append(f"footer={footer_year}")

    legacy_stacks = {"wix", "godaddysites", "weebly", "yola", "jimdo", "webnode", "google sites"}
    if tech_stack and tech_stack.lower() in legacy_stacks:
        pts += 15
        reasons.append(f"stack={tech_stack}")

    if mobile_friendly == 0:
        pts += 10
        reasons.append("not mobile")

    if ssl_valid == 0:
        pts += 5
        reasons.append("no SSL")

    return min(pts, 40), " ".join(reasons) if reasons else "fresh website"


# ---------------------------------------------------------------------------
# Public scorer
# ---------------------------------------------------------------------------

def score_v2(
    *,
    # Lead fields:
    industry_keyword: str | None,
    state: str | None,
    business_name: str | None,
    gst_status: str | None,
    entity_type: str | None,
    has_domain: int | None,
    # Enrichment fields:
    website_url: str | None,
    website_class: int | None,
    rating: float | None,
    review_count: int | None,
    business_status: str | None,
    footer_year: int | None,
    tech_stack: str | None,
    mobile_friendly: int | None,
    ssl_valid: int | None,
) -> ScoreBreakdown:
    """V2-LITE pain-signal score.

    Visi argumentai keyword-only — kad call site'as nepainiotų pozicijas
    pridėdamas naują lauką.
    """
    b = ScoreBreakdown()

    b.base_icp = base_priority_score(industry_keyword, state, business_name, gst_status)
    if b.base_icp:
        b.reasons.append(f"icp={b.base_icp}")

    has_website_url = bool(website_url and website_url.strip())
    b.channel_avail, r = _channel_score(has_domain, website_class, has_website_url)
    if r:
        b.reasons.append(f"channel: {r} (+{b.channel_avail})")

    b.review_signal, r = _review_score(rating, review_count)
    if r:
        b.reasons.append(f"reviews: {r} (+{b.review_signal})")

    b.business_status, r = _business_status_score(business_status)
    if r:
        b.reasons.append(f"status: {r} ({b.business_status:+d})")

    b.revenue_proxy, r = _revenue_proxy_score(entity_type)
    if r:
        b.reasons.append(f"revenue: {r} (+{b.revenue_proxy})")

    b.stale_website, r = _stale_website_score(footer_year, tech_stack, mobile_friendly, ssl_valid)
    if r:
        b.reasons.append(f"stale: {r} (+{b.stale_website})")

    return b


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Case 1: PERFECT gold lead (no website, AU, electrician, high reviews)
    b = score_v2(
        industry_keyword="electrical",
        state="NSW",
        business_name="ACME Electrical Services Pty Ltd",
        gst_status="ACT",
        entity_type="Australian Private Company",
        has_domain=0,
        website_url=None,
        website_class=None,
        rating=4.8,
        review_count=80,
        business_status="OPERATIONAL",
        footer_year=None,
        tech_stack=None,
        mobile_friendly=None,
        ssl_valid=None,
    )
    print(f"GOLD lead (no website): total={b.total}  base={b.base_icp}  chan={b.channel_avail}  rev={b.review_signal}")
    for r in b.reasons:
        print(f"  - {r}")

    # Case 2: Modern website (hard sale)
    b = score_v2(
        industry_keyword="electrical",
        state="NSW",
        business_name="ACME Electrical Services Pty Ltd",
        gst_status="ACT",
        entity_type="Australian Private Company",
        has_domain=1,
        website_url="https://acme.com.au",
        website_class=3,
        rating=4.8,
        review_count=80,
        business_status="OPERATIONAL",
        footer_year=2025,
        tech_stack="webflow",
        mobile_friendly=1,
        ssl_valid=1,
    )
    print(f"\nMODERN site (hard sale): total={b.total}  chan={b.channel_avail}  stale={b.stale_website}")

    # Case 3: Closed permanently — exclude
    b = score_v2(
        industry_keyword="electrical", state="NSW", business_name="ACME",
        gst_status="ACT", entity_type="PTY", has_domain=0,
        website_url=None,
        website_class=None, rating=4.5, review_count=20,
        business_status="CLOSED_PERMANENTLY",
        footer_year=None, tech_stack=None, mobile_friendly=None, ssl_valid=None,
    )
    print(f"\nCLOSED: total={b.total} (should be negative)")

    # Case 4: Dead website (class 1) + good reviews
    b = score_v2(
        industry_keyword="plumbing", state="VIC",
        business_name="Modern Group Plumbing Pty Ltd",
        gst_status="ACT", entity_type="PTY LTD", has_domain=1,
        website_url="https://moderngroup.com.au",
        website_class=1, rating=4.6, review_count=45,
        business_status="OPERATIONAL",
        footer_year=None, tech_stack=None, mobile_friendly=None, ssl_valid=None,
    )
    print(f"\nDEAD website + reviews: total={b.total}")
