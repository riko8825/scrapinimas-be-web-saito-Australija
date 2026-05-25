"""Self-contained test suite for the ABR outreach pipeline.

No pytest required — just `python test_pipeline.py`. Each check prints
[PASS] or [FAIL]; the script exits with code 0 only if every test passes.

Covers:
    1. Synthetic 50-row dataset (in-memory, no XML on disk)
    2. normalize_name()  — apostrophes, '&', PTY LTD variations, digits
    3. generate_domains() — verifies .com.au/.com variants are produced
    4. detect_industry() — across all 9 buckets + fallback
    5. fuzzy_match()    — threshold logic
    6. DNS check with mocked aiodns resolver — no network access
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

# Make ./src importable so we can pull in shared helpers.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import pandas as pd

import check_dns
import find_social
import generate_outreach
from src import utils as src_utils  # explicit alias so swaps stay obvious


# ---------------------------------------------------------------------------
# TEST RUNNER  (tiny — avoid pytest dep)
# ---------------------------------------------------------------------------

class TestRunner:
    """Collect pass/fail counts and pretty-print per-test results."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        """Record one assertion. `detail` is shown only on failure."""
        if condition:
            self.passed += 1
            print(f"  [PASS] {label}")
        else:
            self.failed += 1
            self.failures.append(f"{label}: {detail}" if detail else label)
            line = f"  [FAIL] {label}"
            if detail:
                line += f"\n         {detail}"
            print(line)

    def section(self, title: str) -> None:
        """Visual divider between test groups."""
        print()
        print("=" * 70)
        print(f"  {title}")
        print("=" * 70)

    def summary(self) -> int:
        """Print final tally; return exit code (0 = all passed)."""
        total = self.passed + self.failed
        print()
        print("=" * 70)
        if self.failed == 0:
            print(f"  ALL TESTS PASSED  ({self.passed}/{total})")
        else:
            print(f"  {self.failed} TEST(S) FAILED  ({self.passed}/{total} passed)")
            print("  Failing tests:")
            for f in self.failures:
                print(f"    - {f}")
        print("=" * 70)
        return 0 if self.failed == 0 else 1


# ---------------------------------------------------------------------------
# 1. FAKE DATASET (50 rows, no XML needed)
# ---------------------------------------------------------------------------

def build_fake_dataset(n: int = 50) -> pd.DataFrame:
    """Return a 50-row DataFrame mimicking parser.py output.

    Mix of industries, states, and statuses so downstream filters get
    something realistic to chew on. Includes `name_normalized` so it
    matches the post-update abr_parser schema.
    """
    industries = [
        ("Smith Plumbing Pty Ltd",     "plumbing"),
        ("ACME Electrical Services",   "electrical"),
        ("Cafe Verona",                "cafe"),
        ("Pretty Hair Salon",          "salon"),
        ("Sydney Dental Clinic",       "dental"),
        ("Joes Mechanic Workshop",     "automotive"),
        ("Sparkle Cleaning Co",        "cleaning"),
        ("Apex Roofing Pty Ltd",       "trades"),
        ("Random Holdings Pty Ltd",    "general"),
        ("Bob's Painting Services",    "trades"),
    ]
    states = ["NSW", "VIC", "QLD", "WA", "SA"]

    rows: list[dict[str, str]] = []
    for i in range(n):
        name, _ = industries[i % len(industries)]
        business_name = f"{name} #{i:02d}"
        rows.append({
            "abn": str(11111111111 + i),
            "business_name": business_name,
            "name_normalized": src_utils.normalize_name(business_name),
            "entity_type": "PRV",
            "state": states[i % len(states)],
            "postcode": f"{2000 + (i * 7) % 8000:04d}",
            "gst_status": "ACT",
            "source_file": "fake.xml",
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. normalize_name
# ---------------------------------------------------------------------------

def test_normalize_name(t: TestRunner) -> None:
    t.section("2. normalize_name() — legal-suffix stripping + edge cases")

    fn = src_utils.normalize_name

    # 2a. Apostrophe handling — must NOT crash, must drop "PTY LTD" + lowercase
    out = fn("Smith's Plumbing PTY LTD")
    t.check(
        "drops PTY LTD on apostrophe input",
        "pty" not in out and "ltd" not in out,
        f"got {out!r}",
    )
    t.check(
        "lowercases apostrophe input",
        out == out.lower(),
        f"got {out!r}",
    )
    t.check(
        "retains 'smith' + 'plumbing' tokens",
        "smith" in out and "plumbing" in out,
        f"got {out!r}",
    )

    # 2b. Ampersand + multiple suffix forms
    out = fn("J & B ELECTRICAL PTY. LTD.")
    t.check(
        "strips 'PTY. LTD.' (with dots)",
        "pty" not in out and "ltd" not in out,
        f"got {out!r}",
    )
    t.check(
        "drops '&' connector",
        "&" not in out,
        f"got {out!r}",
    )
    t.check(
        "keeps 'electrical' token",
        "electrical" in out,
        f"got {out!r}",
    )

    # 2c. Digits in name must survive
    out = fn("CAFE 23")
    t.check(
        "preserves digits in 'CAFE 23'",
        "23" in out and "cafe" in out,
        f"got {out!r}",
    )

    # 2d. Empty / None safety
    t.check("empty string -> empty",      fn("") == "")
    t.check("None-like input is safe",    fn(None) == "")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. generate_domains
# ---------------------------------------------------------------------------

def test_generate_domains(t: TestRunner) -> None:
    t.section("3. generate_domains() — domain candidate generation")

    # Pipeline-of-record version (used by check_dns.py).
    # NOTE: new check_dns.generate_domains expects a pre-normalized name
    # (lowercase, legal suffixes already stripped). We feed it accordingly
    # — that's how the real pipeline calls it via the name_normalized column.
    gen = check_dns.generate_domains

    domains = gen("acme cleaning")
    t.check(
        "yields at most 5 candidates",
        len(domains) <= 5,
        f"got {len(domains)}: {domains}",
    )
    t.check(
        "includes a .com.au variant",
        any(d.endswith(".com.au") for d in domains),
        f"got {domains}",
    )
    t.check(
        "includes a .com variant",
        any(d.endswith(".com") for d in domains),
        f"got {domains}",
    )
    t.check(
        "normalized input -> no 'pty'/'ltd' in slug",
        all("pty" not in d and "ltd" not in d for d in domains),
        f"got {domains}",
    )
    t.check(
        "lowercases all candidates",
        all(d == d.lower() for d in domains),
        f"got {domains}",
    )

    # Multi-word businesses get a hyphenated variant
    domains = gen("smith plumbing")
    t.check(
        "produces hyphenated variant for multi-word names",
        any("-" in d for d in domains),
        f"got {domains}",
    )

    # The spec asked for ".com.au variations" — explicit check:
    t.check(
        "'smith plumbing' -> 'smithplumbing.com.au' present",
        "smithplumbing.com.au" in domains,
        f"got {domains}",
    )

    # Junk inputs degrade safely
    t.check("empty name -> []", gen("") == [])
    t.check("single-char name -> []", gen("a") == [])

    # Also smoke-test the src/utils.py version (parallel implementation)
    util_domains = src_utils.generate_domains("ACME Cleaning Pty Ltd")
    t.check(
        "src.utils.generate_domains also produces .com.au",
        any(d.endswith(".com.au") for d in util_domains),
        f"got {util_domains}",
    )


# ---------------------------------------------------------------------------
# 4. detect_industry
# ---------------------------------------------------------------------------

def test_detect_industry(t: TestRunner) -> None:
    t.section("4. detect_industry() — keyword-based classification")

    det = generate_outreach.detect_industry

    cases: list[tuple[str, str]] = [
        ("Smith Plumbing Pty Ltd",       "plumbing"),
        ("ACME Electrical Services",     "electrical"),
        ("Cafe Verona",                  "cafe"),
        ("Pretty Hair Salon",            "salon"),
        ("Sydney Dental Clinic",         "dental"),
        ("Joes Mechanic Workshop",       "automotive"),
        ("Sparkle Cleaning Co",          "cleaning"),
        ("Apex Roofing Pty Ltd",         "trades"),
        ("Random Holdings Pty Ltd",      "general"),
    ]
    for name, expected in cases:
        got = det(name)
        t.check(
            f"{name!r} -> {expected}",
            got == expected,
            f"got {got!r}",
        )

    # Should always return a string from the known set, even on junk
    t.check(
        "empty name -> 'general'",
        det("") == "general",
    )


# ---------------------------------------------------------------------------
# 5. fuzzy_match
# ---------------------------------------------------------------------------

def test_fuzzy_match(t: TestRunner) -> None:
    t.section("5. fuzzy_match() — bigram similarity + threshold logic")

    fm = find_social.fuzzy_match
    THRESHOLD = 0.5

    # Identical strings -> 1.0
    t.check("identical strings -> 1.0", fm("ACME Cleaning", "ACME Cleaning") == 1.0)

    # Near-identical (same root, suffix differs) -> above threshold
    score = fm("ACME Cleaning Pty Ltd", "ACME Cleaning Services")
    t.check(
        f"'ACME Cleaning Pty Ltd' ~ 'ACME Cleaning Services' >= {THRESHOLD}",
        score >= THRESHOLD,
        f"got {score:.3f}",
    )

    # Completely different -> well below threshold
    score = fm("Sydney Salon", "Melbourne Cafe")
    t.check(
        f"'Sydney Salon' ~ 'Melbourne Cafe' < {THRESHOLD}",
        score < THRESHOLD,
        f"got {score:.3f}",
    )

    # Empty inputs -> 0.0 (never raise)
    t.check("fuzzy(\"\", \"x\") -> 0.0", fm("", "x") == 0.0)
    t.check("fuzzy(\"x\", \"\") -> 0.0", fm("x", "") == 0.0)

    # Score is bounded [0, 1]
    for a, b in [("foo", "bar"), ("abcd", "abcde"), ("hello world", "world hello")]:
        s = fm(a, b)
        t.check(f"fuzzy({a!r},{b!r}) bounded [0,1]", 0.0 <= s <= 1.0, f"got {s}")


# ---------------------------------------------------------------------------
# 6. Mocked DNS check
# ---------------------------------------------------------------------------

class FakeResolver:
    """Stand-in for dns.asyncresolver.Resolver — no network, deterministic.

    `resolvable` is the set of domain strings that should be reported as
    resolving. Everything else raises NXDOMAIN, which check_dns._resolves
    catches.
    """

    def __init__(self, resolvable: set[str]) -> None:
        self.resolvable = resolvable
        self.calls: list[str] = []

    async def resolve(self, domain: str, rdtype: str = "A") -> Any:
        self.calls.append(domain)
        if domain in self.resolvable:
            # check_dns._resolves only checks `len(answers) > 0`, so any
            # iterable with at least one element will do.
            return ["FAKE_RDATA"]
        import dns.resolver
        raise dns.resolver.NXDOMAIN()


async def _run_dns_with_mock(
    name_normalized_col: list[str],
    resolvable: set[str],
) -> list[str]:
    """Reproduce check_dns.check_all() but with FakeResolver. Same logic, no net."""
    resolver = FakeResolver(resolvable)
    sem = asyncio.Semaphore(10)

    async def _one(idx: int, name_norm: str) -> tuple[int, str]:
        cands = check_dns.generate_domains(name_norm)
        async with sem:
            for c in cands:
                if await check_dns._resolves(resolver, c):  # noqa: SLF001
                    return idx, c
        return idx, ""

    results: list[str] = [""] * len(name_normalized_col)
    for fut in asyncio.as_completed(
        [asyncio.create_task(_one(i, n)) for i, n in enumerate(name_normalized_col)]
    ):
        idx, found = await fut
        results[idx] = found
    return results


def test_dns_check_mocked(t: TestRunner) -> None:
    t.section("6. DNS check (mocked) — has_domain truth-table")

    df = build_fake_dataset(10)
    # New check_dns expects the already-normalized name (matches the
    # post-update parser output).
    names = df["name_normalized"].tolist()

    # Make domain candidates for one row resolve, rest fail.
    target_row = 0
    target_name = names[target_row]
    cands = check_dns.generate_domains(target_name)
    # We make the very first candidate the one that "resolves".
    resolvable = {cands[0]} if cands else set()

    found = asyncio.run(_run_dns_with_mock(names, resolvable))

    # Target row found the expected domain
    t.check(
        f"target row {target_row} found {cands[0]!r}",
        found[target_row] == cands[0],
        f"got {found[target_row]!r}",
    )

    # Every other row reports no domain
    others_blank = all(f == "" for i, f in enumerate(found) if i != target_row)
    t.check("non-target rows return ''", others_blank)

    # has_domain truth table
    df["found_domain"] = found
    df["has_domain"] = df["found_domain"].astype(bool) & (df["found_domain"] != "")

    n_has = int(df["has_domain"].sum())
    n_missing = int((~df["has_domain"]).sum())
    t.check(
        "exactly 1 row has has_domain=True",
        n_has == 1,
        f"got n_has={n_has}",
    )
    t.check(
        "remaining 9 rows have has_domain=False",
        n_missing == len(df) - 1,
        f"got n_missing={n_missing}",
    )

    # no_website subset = those WITHOUT a domain
    no_site = df[~df["has_domain"]]
    t.check(
        "no_website subset size matches missing count",
        len(no_site) == n_missing,
        f"len(no_site)={len(no_site)}, n_missing={n_missing}",
    )


# ---------------------------------------------------------------------------
# 1. Smoke test for the fake dataset itself
# ---------------------------------------------------------------------------

def test_dataset(t: TestRunner) -> None:
    t.section("1. Fake dataset — shape + columns")
    df = build_fake_dataset(50)
    t.check("dataset has 50 rows", len(df) == 50, f"got {len(df)}")
    required = {"abn", "business_name", "entity_type",
                "state", "postcode", "gst_status", "source_file"}
    t.check(
        "dataset has all required columns",
        required.issubset(df.columns),
        f"missing: {required - set(df.columns)}",
    )
    t.check("all ABNs are unique", df["abn"].is_unique)
    t.check(
        "states drawn from 5-state pool",
        set(df["state"]) <= {"NSW", "VIC", "QLD", "WA", "SA"},
        f"got {sorted(set(df['state']))}",
    )


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def main() -> int:
    t = TestRunner()

    test_dataset(t)
    test_normalize_name(t)
    test_generate_domains(t)
    test_detect_industry(t)
    test_fuzzy_match(t)
    test_dns_check_mocked(t)

    return t.summary()


if __name__ == "__main__":
    raise SystemExit(main())
