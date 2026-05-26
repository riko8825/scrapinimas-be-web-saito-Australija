"""AU validation — anti-PROXYTECH safeguard po Stage A (Google Places).

Kontekstas (sesija #8): Google Places kartais grąžina ne-AU verslą (PROXYTECH
bug — Kanados įmonė ant AU postcode query'o). Tokie lead'ai turi būti
pažymėti `not_au` PRIEŠ Stage B (kad nešvaistytume HTTP request'ų ant
non-target market'o) ir EXCLUDED iš outreach export'ų.

Validacijos logika — votes-based:
  1. phone — `+61` ar `0X` AU format → AU vote
            `+1` / `+44` / kt → NOT-AU vote
  2. website — `.au` / `.com.au` ar `gov.au` → AU vote
              `.ca` / `.us` / `.co.uk` / kt → NOT-AU vote
  3. address — su 2-3 raidžių AU state kodu (NSW/VIC/QLD/...) ir 4-digit
              postcode → AU vote
              su žinomu ne-AU country marker'iu (Canada/USA/UK/...) → NOT-AU

Verdiktas:
  - bent 1 AU vote IR 0 NOT-AU votes → 'au_ok'
  - bent 1 NOT-AU vote (nepriklausomai nuo AU votes) → 'not_au'
  - viskas tuščia / ambiguous → 'unknown'

Konservatyvu by design — false negative ('au_ok' žymime ką ne reikia) yra
brangiau nei false positive ('not_au' praleidžiame gerą lead). False negative
= $0.035 Stage B + galimas wasted outreach. False positive = vienkartinis
prarastas lead iš 100k+ kandidatų pool'o.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AU_STATES: frozenset[str] = frozenset(
    {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}
)

AU_TLD_SUFFIXES: tuple[str, ...] = (
    ".au",
    ".com.au",
    ".net.au",
    ".org.au",
    ".gov.au",
    ".edu.au",
    ".id.au",
)

# Pavyzdinis "ne-AU" TLD juodasis sąrašas — top sources, kuriuos esam matę
# false positive'uose. Jei TLD ne čia ir ne .au — NEsprendžiam (neutral).
NON_AU_TLD_SUFFIXES: tuple[str, ...] = (
    ".ca",       # Canada (PROXYTECH bug source)
    ".us",
    ".co.uk",
    ".uk",
    ".ie",
    ".de",
    ".fr",
    ".nz",
    ".sg",
    ".in",
    ".my",
    ".ph",
)

# Country marker tokens addres'e (UPPERCASED comparison)
NON_AU_COUNTRY_MARKERS: tuple[str, ...] = (
    "CANADA",
    "UNITED STATES",
    "USA",
    " UK",
    "UNITED KINGDOM",
    "IRELAND",
    "NEW ZEALAND",
    "SINGAPORE",
    "PHILIPPINES",
    "INDIA",
    "MALAYSIA",
    "GERMANY",
    "FRANCE",
)

# Skambučio prefix patternas — +61 arba 0X 4-digits-min.
# (61) leidžiam tarp skliaustų, brūkšneliais ar tarpais.
_PHONE_AU_RE = re.compile(r"(?:\+\s*61|\(\s*61\s*\)|^0)\s*[\d\-\s\(\)]{6,}")
_PHONE_NON_AU_RE = re.compile(r"^\s*\+\s*(?!61\b)(\d{1,4})")  # +1, +44, +91...

_POSTCODE_RE = re.compile(r"\b(\d{4})\b")
_STATE_RE = re.compile(r"\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\b")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Single ABN validation verdict."""
    status: str                    # 'au_ok' | 'not_au' | 'unknown'
    reason: str                    # human-readable trail
    au_signals: tuple[str, ...]    # which checks said "AU"
    non_au_signals: tuple[str, ...]  # which checks said "NOT AU"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_phone(phone: str | None) -> tuple[bool, bool, str]:
    """Returns (is_au, is_non_au, detail). Both False jei tuščia/ambiguous."""
    if not phone:
        return False, False, ""
    p = phone.strip()
    if not p:
        return False, False, ""

    # +61 ar 0X — AU
    if _PHONE_AU_RE.search(p):
        return True, False, f"phone AU format ({p[:18]})"

    # +<kitas-šalis-code> — NOT AU
    m = _PHONE_NON_AU_RE.match(p)
    if m:
        cc = m.group(1)
        return False, True, f"phone +{cc} (non-AU country code)"

    return False, False, f"phone format unclear ({p[:18]})"


def _check_website(website: str | None) -> tuple[bool, bool, str]:
    """Returns (is_au, is_non_au, detail)."""
    if not website:
        return False, False, ""
    w = website.strip().lower()
    if not w:
        return False, False, ""

    # Strip protocol + path, keep just hostname.
    host = w.replace("https://", "").replace("http://", "").split("/", 1)[0]
    host = host.strip().rstrip(".")
    if not host:
        return False, False, ""

    # Patikrint AU TLD'us pirmą — '.com.au' tikrinti PRIEŠ '.com'.
    for suf in AU_TLD_SUFFIXES:
        if host.endswith(suf):
            return True, False, f"website TLD {suf} ({host})"

    for suf in NON_AU_TLD_SUFFIXES:
        if host.endswith(suf):
            return False, True, f"website TLD {suf} ({host})"

    # Generic .com / .net / .org — nieko nepasako (gali būti AU verslas su .com).
    return False, False, f"website TLD generic ({host})"


def _check_address(address: str | None) -> tuple[bool, bool, str]:
    """Returns (is_au, is_non_au, detail)."""
    if not address:
        return False, False, ""
    a = address.strip()
    if not a:
        return False, False, ""

    a_upper = a.upper()

    # Pirma — patikrint ne-AU country marker'ius.
    for marker in NON_AU_COUNTRY_MARKERS:
        if marker in a_upper:
            return False, True, f"address contains '{marker.strip()}'"

    # AU state + 4-digit postcode → AU vote.
    state_m = _STATE_RE.search(a_upper)
    pc_m = _POSTCODE_RE.search(a)
    if state_m and pc_m:
        return True, False, f"address state={state_m.group(1)} postcode={pc_m.group(1)}"

    if state_m:
        return True, False, f"address state={state_m.group(1)}"

    # Tik postcode be state'o — silpnas signalas (Kanados postcode'ai 5-skait.,
    # bet šis regex'as catches tik 4-digit) — leidžiam kaip ambiguous.
    return False, False, "address ambiguous (no state code)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_au(
    phone: str | None,
    website: str | None,
    formatted_address: str | None,
) -> ValidationResult:
    """Vote-based AU validation. NEVER raises.

    Naudojim post-Stage A, prieš įrašant į DB:
        result = validators.validate_au(phone, website_url, formatted_address)
        out["au_validation_status"] = result.status
        out["au_validation_reason"] = result.reason
    """
    au_votes: list[str] = []
    non_au_votes: list[str] = []
    trail: list[str] = []

    for check in (
        _check_phone(phone),
        _check_website(website),
        _check_address(formatted_address),
    ):
        is_au, is_non, detail = check
        if detail:
            trail.append(detail)
        if is_au:
            au_votes.append(detail)
        if is_non:
            non_au_votes.append(detail)

    # Bet koks NOT-AU vote → not_au (konservatyvu, anti-PROXYTECH).
    if non_au_votes:
        return ValidationResult(
            status="not_au",
            reason=" | ".join(non_au_votes),
            au_signals=tuple(au_votes),
            non_au_signals=tuple(non_au_votes),
        )

    if au_votes:
        return ValidationResult(
            status="au_ok",
            reason=" | ".join(au_votes),
            au_signals=tuple(au_votes),
            non_au_signals=(),
        )

    return ValidationResult(
        status="unknown",
        reason=" | ".join(trail) if trail else "no signals (empty phone/website/address)",
        au_signals=(),
        non_au_signals=(),
    )


# ---------------------------------------------------------------------------
# Self-test (run module directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    cases: list[tuple[str, str | None, str | None, str | None, str]] = [
        # (name, phone, website, address, expected_status)
        ("Pure AU", "+61 2 9876 5432", "https://example.com.au", "123 Pitt St, Sydney NSW 2000", "au_ok"),
        ("AU phone only", "0412 345 678", None, None, "au_ok"),
        ("AU website only", None, "https://acme.net.au", None, "au_ok"),
        ("AU address only", None, None, "12 Smith Rd, Melbourne VIC 3000, Australia", "au_ok"),
        ("PROXYTECH-like (CA)", "+1 416 555 1234", "https://proxytech.ca", "Toronto, ON, Canada", "not_au"),
        ("US phone", "+1 415 555 0100", None, None, "not_au"),
        ("UK website", None, "https://acme.co.uk", None, "not_au"),
        ("Address USA", None, None, "100 Main St, NY 10001, USA", "not_au"),
        ("Mixed (AU phone + UK site = NOT AU)", "+61 2 1111 2222", "https://acme.co.uk", None, "not_au"),
        ("Empty everything", None, None, None, "unknown"),
        ("Generic .com only", None, "https://example.com", None, "unknown"),
        ("Postcode no state", None, None, "Box 12, Some Town 1234", "unknown"),
        ("AU phone + generic .com", "+61 3 8888 1234", "https://shop.com", None, "au_ok"),
    ]

    fails = 0
    for name, phone, web, addr, expected in cases:
        r = validate_au(phone, web, addr)
        ok = r.status == expected
        mark = "OK  " if ok else "FAIL"
        if not ok:
            fails += 1
        print(f"  {mark}  {name:38s}  got={r.status:8s}  expected={expected:8s}  reason={r.reason}")

    if fails:
        print(f"\n{fails} case(s) failed.")
        raise SystemExit(1)
    print(f"\nAll {len(cases)} cases passed.")
