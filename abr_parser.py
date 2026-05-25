"""Parse Australian Business Register (ABR) bulk-extract XML files.

Reads every `*.xml` file in ./abr-data/, streams records with
`xml.etree.ElementTree.iterparse` (so 600 MB+ files never touch RAM at
once), filters by:

    * ABN status = ACT (active)
    * Business name contains one of the configured industry keywords

Writes the result to ./output/filtered_businesses.csv and prints
per-state and per-entity-type breakdowns.

Run:
    python abr_parser.py
    python abr_parser.py --input-dir ./abr-data --output ./output/filtered_businesses.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator
from xml.etree.ElementTree import iterparse

import pandas as pd
from tqdm import tqdm

# Bring in shared helpers from src/. Done with a sys.path insert so the
# script stays runnable as `python abr_parser.py` without needing to be
# installed as a package.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from utils import normalize_name  # noqa: E402

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

KEYWORDS: tuple[str, ...] = (
    "plumbing", "electrical", "electrician", "landscaping", "garden",
    "roofing", "cleaning", "cafe", "restaurant", "salon", "barber",
    "dental", "mechanic", "painting", "concreting", "fencing",
    "pest control", "aircon",
)

OUTPUT_COLUMNS: list[str] = [
    "abn",
    "business_name",
    "name_normalized",
    "entity_type",
    "state",
    "postcode",
    "gst_status",
    "source_file",
]

# Pre-compile keyword regex for cheap case-insensitive name matching.
# Word boundaries on each side so "cafeteria" does NOT match "cafe".
_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b",
    flags=re.IGNORECASE,
)

# Tag suffixes we care about (after namespace stripping)
_TAG_ABR = "ABR"


# ---------------------------------------------------------------------------
# XML HELPERS
# ---------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    """Return the local tag name without an XML namespace prefix.

    ElementTree emits namespaced tags as ``{http://...}LocalName``. ABR
    dumps occasionally use a namespace, so we always strip it before
    comparing.
    """
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _find_first(element, *names: str):
    """Depth-first search for the first descendant whose local name is in `names`.

    Args:
        element: ElementTree element to search under.
        *names: one or more local tag names to look for.

    Returns:
        The matching child element, or None.
    """
    wanted = set(names)
    for child in element.iter():
        if _strip_ns(child.tag) in wanted:
            return child
    return None


def _text(element, *names: str) -> str:
    """Return the stripped text of the first matching descendant, or ''."""
    node = _find_first(element, *names)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _extract_business_name(record) -> str:
    """Pick the best human-readable business name from an <ABR> record.

    Preference order:
        1. MainEntity / NonIndividualName / NonIndividualNameText
        2. LegalEntity / IndividualName  (GivenName + FamilyName)
        3. Any *NameText* element       (fallback for trading-name variants)
    """
    # 1. Non-individual (company) name
    name = _text(record, "NonIndividualNameText")
    if name:
        return name

    # 2. Individual name — concatenate given + family
    given = _text(record, "GivenName")
    family = _text(record, "FamilyName")
    full = f"{given} {family}".strip()
    if full:
        return full

    # 3. Any *NameText* fallback
    for child in record.iter():
        local = _strip_ns(child.tag)
        if local.endswith("NameText") and child.text:
            return child.text.strip()

    return ""


def _extract_record(record) -> dict[str, str]:
    """Pull ABN, name, entity type, state, postcode, GST status from one <ABR>.

    Missing fields become empty strings — never None — so the row maps
    cleanly into a CSV.
    """
    # ABN element + status attribute
    abn_node = _find_first(record, "ABN")
    abn = (abn_node.text or "").strip() if abn_node is not None else ""
    abn_status = abn_node.get("status", "") if abn_node is not None else ""

    # State + postcode live under BusinessAddress/AddressDetails
    addr = _find_first(record, "AddressDetails")
    state = _text(addr, "State") if addr is not None else ""
    postcode = _text(addr, "Postcode") if addr is not None else ""

    # GST status attribute on the <GST> element (e.g. status="ACT")
    gst_node = _find_first(record, "GST")
    gst_status = gst_node.get("status", "") if gst_node is not None else ""

    return {
        "abn": abn,
        "abn_status": abn_status,
        "business_name": _extract_business_name(record),
        "entity_type": _text(record, "EntityTypeInd"),
        "state": state,
        "postcode": postcode,
        "gst_status": gst_status,
    }


# ---------------------------------------------------------------------------
# STREAMING PARSER
# ---------------------------------------------------------------------------

def _iter_xml_files(input_dir: Path) -> list[Path]:
    """Return sorted list of *.xml files under `input_dir`."""
    files = sorted(input_dir.glob("*.xml"))
    return files


def stream_records(xml_path: Path) -> Iterator[dict[str, str]]:
    """Yield one dict per <ABR> record using iterparse.

    Clears each element after processing to keep peak memory roughly
    constant regardless of file size.
    """
    # `end` event fires after the closing tag of each element is read.
    context = iterparse(str(xml_path), events=("end",))
    src = xml_path.name

    for _event, elem in context:
        if _strip_ns(elem.tag) != _TAG_ABR:
            continue
        try:
            row = _extract_record(elem)
        except Exception as exc:  # noqa: BLE001 — log + skip per CLAUDE.md rule
            print(f"  [warn] {src}: failed to parse a record: {exc}", file=sys.stderr)
            row = None
        finally:
            # Always release memory, even on extraction failure.
            elem.clear()

        if row is not None:
            row["source_file"] = src
            yield row


def matches_filters(row: dict[str, str]) -> bool:
    """Return True if this record passes the ACT + keyword filters."""
    if row.get("abn_status") != "ACT":
        return False
    name = row.get("business_name", "")
    if not name:
        return False
    return bool(_KEYWORD_RE.search(name))


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def parse_directory(input_dir: Path) -> pd.DataFrame:
    """Stream every XML file under `input_dir`, filter, return a DataFrame.

    Shows a per-file tqdm progress bar (units = scanned records) and a
    second outer counter of files processed.
    """
    files = _iter_xml_files(input_dir)
    if not files:
        print(f"No .xml files found in {input_dir}", file=sys.stderr)
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    print(f"Found {len(files)} XML file(s) in {input_dir}")

    matched: list[dict[str, str]] = []
    total_scanned = 0

    for file_idx, path in enumerate(files, start=1):
        file_size_mb = path.stat().st_size / (1024 * 1024)
        desc = f"[{file_idx}/{len(files)}] {path.name} ({file_size_mb:.0f} MB)"

        scanned_in_file = 0
        kept_in_file = 0

        # We don't know the record count in advance, so tqdm runs in
        # "unknown total" mode; it still shows rate + elapsed.
        bar = tqdm(stream_records(path), desc=desc, unit=" rec", mininterval=0.5)
        for row in bar:
            scanned_in_file += 1
            if matches_filters(row):
                matched.append({
                    "abn": row["abn"],
                    "business_name": row["business_name"],
                    "name_normalized": normalize_name(row["business_name"]),
                    "entity_type": row["entity_type"],
                    "state": row["state"],
                    "postcode": row["postcode"],
                    "gst_status": row["gst_status"],
                    "source_file": row["source_file"],
                })
                kept_in_file += 1
            if scanned_in_file % 50_000 == 0:
                bar.set_postfix(kept=kept_in_file)
        bar.close()

        total_scanned += scanned_in_file
        print(f"  {path.name}: kept {kept_in_file:,} / scanned {scanned_in_file:,}")

    print(f"\nScanned {total_scanned:,} records across {len(files)} file(s).")
    print(f"Matched {len(matched):,} active businesses on keyword filter.")

    return pd.DataFrame(matched, columns=OUTPUT_COLUMNS)


def print_statistics(df: pd.DataFrame) -> None:
    """Print total, per-state, and per-entity-type counts."""
    if df.empty:
        print("\nNo matching records — nothing to summarize.")
        return

    print("\n" + "=" * 50)
    print(f"TOTAL MATCHED: {len(df):,}")
    print("=" * 50)

    print("\nBy state:")
    state_counts = df["state"].replace("", "(unknown)").value_counts()
    for state, n in state_counts.items():
        print(f"  {state:<10} {n:>8,}")

    print("\nBy entity type:")
    type_counts = df["entity_type"].replace("", "(unknown)").value_counts()
    for etype, n in type_counts.items():
        print(f"  {etype:<10} {n:>8,}")

    print()


def write_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Persist the matched rows to CSV (UTF-8, no index)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8", quoting=csv.QUOTE_MINIMAL)
    print(f"Wrote {len(df):,} rows -> {output_path.resolve()}")


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Parse ABR XML dumps into a filtered CSV.",
    )
    parser.add_argument(
        "--input-dir", "-i",
        type=Path,
        default=Path("./abr-data"),
        help="Directory holding ABR *.xml files (default: ./abr-data)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("./output/filtered_businesses.csv"),
        help="Output CSV path (default: ./output/filtered_businesses.csv)",
    )
    args = parser.parse_args(argv)

    if not args.input_dir.exists():
        print(f"ERROR: input dir does not exist: {args.input_dir}", file=sys.stderr)
        return 2

    df = parse_directory(args.input_dir)
    write_csv(df, args.output)
    print_statistics(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
