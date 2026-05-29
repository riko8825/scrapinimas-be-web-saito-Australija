"""V2-LITE P1.2: Australijos suburb-tier lookup pagal wealth/spend proxy.

Tikslas: pridėti scoring_v2 mažą bonus'ą (+5pt) lead'ams iš tier-1 ar tier-2
suburb'ų — žmonės tose vietose dažniau perka prekes/paslaugas premium, taigi
service business operatorius ten labiau pirks Empirra svetainę.

Šaltinis: kombinacija iš:
  - Sydney/Melbourne/Brisbane/Perth/Adelaide premium suburb sąrašų (CoreLogic, RealEstate.com.au top 50 by median price 2023-2024)
  - CBD postcode'ai (visada tier-1)
  - Žinomi affluent enclaves (Mosman, Toorak, Cottesloe, Vaucluse, ...)

Tier reikšmės:
    1  premium / blue-chip  → +5pt scoring_v2
    2  upper-middle / CBD   → +3pt
    3  middle               → 0pt (default)
    4  budget / regional    → 0pt (nedaro penalty — tik regional service businesses
                              dažnai nori website, irgi target)

NE-ABS source'as — heuristic, ne moksliškas. Sąrašas turi būti revise'inamas
kasmet jeigu naudosim production scale'e.

Lookup logikos prioritetas:
    1. exact suburb_norm + state match → tier
    2. postcode CBD set (visi major city CBD postcode'ai) → tier 2
    3. default → tier 3
"""
from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# TIER 1 — premium (top ~50 wealthiest AU suburbs, mixed sources 2023-2024)
# Source: CoreLogic Top 50 Most Expensive Suburbs reports + cross-check
# RealEstate.com.au median sale prices.
# ---------------------------------------------------------------------------

TIER_1_SUBURBS: Final[frozenset[tuple[str, str]]] = frozenset({
    # NSW — Sydney eastern + lower north + harbour
    ("bellevue hill", "NSW"),
    ("vaucluse", "NSW"),
    ("point piper", "NSW"),
    ("rose bay", "NSW"),
    ("double bay", "NSW"),
    ("darling point", "NSW"),
    ("dover heights", "NSW"),
    ("woollahra", "NSW"),
    ("paddington", "NSW"),
    ("centennial park", "NSW"),
    ("mosman", "NSW"),
    ("cremorne", "NSW"),
    ("northbridge", "NSW"),
    ("clontarf", "NSW"),
    ("seaforth", "NSW"),
    ("balmoral", "NSW"),
    ("balgowlah heights", "NSW"),
    ("longueville", "NSW"),
    ("hunters hill", "NSW"),
    ("kirribilli", "NSW"),
    ("milsons point", "NSW"),
    ("manly", "NSW"),
    ("palm beach", "NSW"),
    ("avalon beach", "NSW"),
    ("bilgola plateau", "NSW"),
    ("whale beach", "NSW"),
    ("bondi beach", "NSW"),
    ("tamarama", "NSW"),
    ("bronte", "NSW"),

    # VIC — Melbourne inner east + bayside
    ("toorak", "VIC"),
    ("south yarra", "VIC"),
    ("malvern", "VIC"),
    ("armadale", "VIC"),
    ("hawthorn", "VIC"),
    ("kew", "VIC"),
    ("camberwell", "VIC"),
    ("canterbury", "VIC"),
    ("balwyn", "VIC"),
    ("balwyn north", "VIC"),
    ("brighton", "VIC"),
    ("brighton east", "VIC"),
    ("hampton", "VIC"),
    ("middle park", "VIC"),
    ("albert park", "VIC"),
    ("east melbourne", "VIC"),
    ("south melbourne", "VIC"),
    ("port melbourne", "VIC"),
    ("st kilda west", "VIC"),

    # QLD — Brisbane inner west + Gold Coast premium
    ("hamilton", "QLD"),
    ("ascot", "QLD"),
    ("hawthorne", "QLD"),
    ("new farm", "QLD"),
    ("teneriffe", "QLD"),
    ("bulimba", "QLD"),
    ("clayfield", "QLD"),
    ("st lucia", "QLD"),
    ("indooroopilly", "QLD"),
    ("chelmer", "QLD"),
    ("graceville", "QLD"),
    ("noosa heads", "QLD"),
    ("sunshine beach", "QLD"),
    ("mermaid beach", "QLD"),
    ("broadbeach waters", "QLD"),
    ("main beach", "QLD"),
    ("surfers paradise", "QLD"),

    # WA — Perth western suburbs
    ("peppermint grove", "WA"),
    ("cottesloe", "WA"),
    ("dalkeith", "WA"),
    ("nedlands", "WA"),
    ("claremont", "WA"),
    ("mosman park", "WA"),
    ("city beach", "WA"),
    ("floreat", "WA"),
    ("subiaco", "WA"),
    ("swanbourne", "WA"),

    # SA — Adelaide eastern
    ("walkerville", "SA"),
    ("medindie", "SA"),
    ("st peters", "SA"),
    ("unley park", "SA"),
    ("burnside", "SA"),
    ("toorak gardens", "SA"),
    ("springfield", "SA"),

    # ACT — Canberra premium
    ("forrest", "ACT"),
    ("yarralumla", "ACT"),
    ("red hill", "ACT"),
    ("griffith", "ACT"),
    ("deakin", "ACT"),
})


# ---------------------------------------------------------------------------
# TIER 2 — upper-middle (CBD + major regional cities + affluent middle ring)
# ---------------------------------------------------------------------------

TIER_2_SUBURBS: Final[frozenset[tuple[str, str]]] = frozenset({
    # NSW
    ("sydney", "NSW"),
    ("the rocks", "NSW"),
    ("pyrmont", "NSW"),
    ("ultimo", "NSW"),
    ("surry hills", "NSW"),
    ("darlinghurst", "NSW"),
    ("potts point", "NSW"),
    ("elizabeth bay", "NSW"),
    ("rushcutters bay", "NSW"),
    ("redfern", "NSW"),
    ("waterloo", "NSW"),
    ("alexandria", "NSW"),
    ("erskineville", "NSW"),
    ("newtown", "NSW"),
    ("glebe", "NSW"),
    ("annandale", "NSW"),
    ("leichhardt", "NSW"),
    ("balmain", "NSW"),
    ("rozelle", "NSW"),
    ("birchgrove", "NSW"),
    ("lavender bay", "NSW"),
    ("mcmahons point", "NSW"),
    ("waverton", "NSW"),
    ("crows nest", "NSW"),
    ("st leonards", "NSW"),
    ("chatswood", "NSW"),
    ("lane cove", "NSW"),
    ("ryde", "NSW"),
    ("randwick", "NSW"),
    ("coogee", "NSW"),
    ("clovelly", "NSW"),
    ("maroubra", "NSW"),
    ("kensington", "NSW"),
    ("kingsford", "NSW"),
    ("dee why", "NSW"),
    ("freshwater", "NSW"),
    ("collaroy", "NSW"),
    ("newport", "NSW"),
    ("byron bay", "NSW"),
    ("terrigal", "NSW"),

    # VIC
    ("melbourne", "VIC"),
    ("docklands", "VIC"),
    ("southbank", "VIC"),
    ("carlton", "VIC"),
    ("fitzroy", "VIC"),
    ("collingwood", "VIC"),
    ("richmond", "VIC"),
    ("abbotsford", "VIC"),
    ("northcote", "VIC"),
    ("fairfield", "VIC"),
    ("ivanhoe", "VIC"),
    ("eaglemont", "VIC"),
    ("st kilda", "VIC"),
    ("elwood", "VIC"),
    ("caulfield north", "VIC"),
    ("elsternwick", "VIC"),
    ("prahran", "VIC"),
    ("windsor", "VIC"),
    ("docklands", "VIC"),
    ("essendon", "VIC"),
    ("moonee ponds", "VIC"),
    ("ascot vale", "VIC"),
    ("flemington", "VIC"),
    ("kensington", "VIC"),
    ("parkville", "VIC"),
    ("north melbourne", "VIC"),
    ("west melbourne", "VIC"),
    ("williamstown", "VIC"),
    ("yarraville", "VIC"),
    ("seddon", "VIC"),
    ("footscray", "VIC"),
    ("sandringham", "VIC"),
    ("black rock", "VIC"),
    ("beaumaris", "VIC"),
    ("mentone", "VIC"),
    ("mornington", "VIC"),
    ("sorrento", "VIC"),
    ("portsea", "VIC"),
    ("torquay", "VIC"),

    # QLD
    ("brisbane city", "QLD"),
    ("south brisbane", "QLD"),
    ("west end", "QLD"),
    ("highgate hill", "QLD"),
    ("paddington", "QLD"),
    ("milton", "QLD"),
    ("auchenflower", "QLD"),
    ("toowong", "QLD"),
    ("kelvin grove", "QLD"),
    ("red hill", "QLD"),
    ("fortitude valley", "QLD"),
    ("spring hill", "QLD"),
    ("kangaroo point", "QLD"),
    ("east brisbane", "QLD"),
    ("woolloongabba", "QLD"),
    ("greenslopes", "QLD"),
    ("camp hill", "QLD"),
    ("coorparoo", "QLD"),
    ("noosaville", "QLD"),
    ("buderim", "QLD"),
    ("burleigh heads", "QLD"),
    ("palm beach", "QLD"),
    ("currumbin", "QLD"),

    # WA
    ("perth", "WA"),
    ("east perth", "WA"),
    ("west perth", "WA"),
    ("northbridge", "WA"),
    ("leederville", "WA"),
    ("mount lawley", "WA"),
    ("highgate", "WA"),
    ("north perth", "WA"),
    ("scarborough", "WA"),
    ("trigg", "WA"),
    ("north fremantle", "WA"),
    ("fremantle", "WA"),
    ("south fremantle", "WA"),
    ("east fremantle", "WA"),

    # SA
    ("adelaide", "SA"),
    ("north adelaide", "SA"),
    ("kent town", "SA"),
    ("parkside", "SA"),
    ("unley", "SA"),
    ("hyde park", "SA"),
    ("malvern", "SA"),
    ("glenelg", "SA"),
    ("brighton", "SA"),
    ("henley beach", "SA"),
    ("semaphore", "SA"),
    ("norwood", "SA"),

    # TAS
    ("hobart", "TAS"),
    ("battery point", "TAS"),
    ("sandy bay", "TAS"),
    ("west hobart", "TAS"),
    ("launceston", "TAS"),

    # ACT
    ("canberra", "ACT"),
    ("braddon", "ACT"),
    ("kingston", "ACT"),
    ("barton", "ACT"),
    ("manuka", "ACT"),
    ("turner", "ACT"),

    # NT
    ("darwin", "NT"),
    ("larrakeyah", "NT"),
    ("cullen bay", "NT"),
    ("fannie bay", "NT"),
})


# CBD postcode'ai (visi major city centers) — fallback jei suburb match'as nepataikė.
CBD_POSTCODES: Final[frozenset[str]] = frozenset({
    "2000",  # Sydney CBD
    "3000",  # Melbourne CBD
    "4000",  # Brisbane CBD
    "5000",  # Adelaide CBD
    "6000",  # Perth CBD
    "7000",  # Hobart CBD
    "0800",  # Darwin CBD
    "2600",  # Canberra (Parkes / Barton / Parliament)
    "2601",  # Canberra (Acton / City)
})


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_AU_STATES: Final[frozenset[str]] = frozenset({
    "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"
})

# Matches: "...some text... <Suburb words> <STATE> <POSTCODE>" at end of string.
# Suburb gali būti multi-word ("North Tamworth", "Surfers Paradise"), state ALL CAPS,
# postcode 4 skaitmenys.
_SUBURB_RE = re.compile(
    r",?\s*([A-Za-z][A-Za-z\s'\-]+?)\s+(NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+(\d{4})\s*(?:,\s*Australia)?\s*$",
    re.IGNORECASE,
)


def parse_suburb(formatted_address: str | None) -> tuple[str | None, str | None, str | None]:
    """Iš `formatted_address` ištraukia (suburb_lowercased, state_upper, postcode).

    Grąžina (None, None, None) jei nepavyko parser'inti (pvz. ne-AU adresas,
    truncated string ar pan.).

    >>> parse_suburb("11 Tully Ave, Liverpool NSW 2170")
    ('liverpool', 'NSW', '2170')
    >>> parse_suburb("Unit 5/240 Harbord Rd, Brookvale NSW 2100")
    ('brookvale', 'NSW', '2100')
    >>> parse_suburb("116 North St, North Tamworth NSW 2340")
    ('north tamworth', 'NSW', '2340')
    >>> parse_suburb("27 Labrook Dr Suite 100, Richmond, VA 23225, USA")
    (None, None, None)
    >>> parse_suburb(None)
    (None, None, None)
    """
    if not formatted_address:
        return None, None, None
    m = _SUBURB_RE.search(formatted_address)
    if not m:
        return None, None, None
    suburb_raw, state_raw, postcode = m.group(1), m.group(2), m.group(3)
    state = state_raw.upper()
    if state not in _AU_STATES:
        return None, None, None
    suburb = suburb_raw.strip().lower()
    return suburb, state, postcode


# ---------------------------------------------------------------------------
# Tier lookup
# ---------------------------------------------------------------------------

def suburb_tier_lookup(
    suburb: str | None,
    state: str | None,
    postcode: str | None,
) -> int:
    """Grąžina tier (1-4) pagal (suburb, state) → CBD postcode fallback.

    >>> suburb_tier_lookup("mosman", "NSW", "2088")
    1
    >>> suburb_tier_lookup("toorak", "VIC", "3142")
    1
    >>> suburb_tier_lookup("sydney", "NSW", "2000")
    2
    >>> suburb_tier_lookup("liverpool", "NSW", "2170")
    3
    >>> suburb_tier_lookup(None, None, "2000")
    2
    >>> suburb_tier_lookup(None, None, None)
    3
    """
    if suburb and state:
        key = (suburb, state)
        if key in TIER_1_SUBURBS:
            return 1
        if key in TIER_2_SUBURBS:
            return 2
    if postcode and postcode in CBD_POSTCODES:
        return 2
    return 3


def tier_from_address(formatted_address: str | None) -> tuple[int, str | None, str | None]:
    """Convenience: address string → (tier, suburb, state).

    >>> tier_from_address("11 Tully Ave, Mosman NSW 2088")
    (1, 'mosman', 'NSW')
    >>> tier_from_address("123 Random St, Liverpool NSW 2170")
    (3, 'liverpool', 'NSW')
    >>> tier_from_address("Bangkok 10400, Thailand")
    (3, None, None)
    """
    suburb, state, postcode = parse_suburb(formatted_address)
    tier = suburb_tier_lookup(suburb, state, postcode)
    return tier, suburb, state


def tier_score(tier: int) -> tuple[int, str]:
    """Maps tier → (points, reason) scoring_v2 component'ui.

    Tier 1 → +5, tier 2 → +3, else 0.
    """
    if tier == 1:
        return 5, "premium suburb (tier 1)"
    if tier == 2:
        return 3, "upper-middle / CBD (tier 2)"
    return 0, ""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import doctest
    failures, _tests = doctest.testmod(verbose=False)
    if failures:
        raise SystemExit(f"doctest FAILED: {failures}")

    # Sanity stats
    print(f"TIER 1 suburbs: {len(TIER_1_SUBURBS)}")
    print(f"TIER 2 suburbs: {len(TIER_2_SUBURBS)}")
    print(f"CBD postcodes:  {len(CBD_POSTCODES)}")

    # Smoke test on real-style addresses
    samples = [
        "11 Tully Ave, Liverpool NSW 2170",
        "1/828 High St, Kew East VIC 3102",
        "Mosman NSW 2088",
        "5 Ian Oliver Dr, Waikerie SA 5330",
        "Suite 200, 100 Collins St, Melbourne VIC 3000",
        "27 Labrook Dr Suite 100, Richmond, VA 23225, USA",
        "Bangkok 10400, Thailand",
        None,
        "",
        "999 Some Rd, Toorak VIC 3142",
        "12 Random St, Bellevue Hill NSW 2023",
    ]
    print("\nSample address parse:")
    for s in samples:
        tier, sub, st = tier_from_address(s)
        pts, reason = tier_score(tier)
        print(f"  {(s or '<None>')[:60]:60s} → tier={tier} ({sub}, {st})  +{pts}pt  {reason}")

    print("\nself-test PASSED.")
