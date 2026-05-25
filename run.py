"""Single entry point for the entire ABR outreach pipeline.

Usage:
    python run.py --step all
    python run.py --step parse | dns | social | messages
    python run.py --test                # only 500 records
    python run.py --state NSW           # override state filter
    python run.py --resume              # reuse existing stage outputs

Features:
    * Pre-flight summary  (XML files found, sizes, config, output paths)
    * Confirmation prompt before a full run
    * Per-stage timing (rows in -> rows out -> seconds)
    * Optional Telegram notification on completion
    * Final summary table in terminal
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import abr_parser
import check_dns
import find_social
import generate_outreach


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

load_dotenv()

ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "abr-data"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    "filtered": OUTPUT_DIR / "filtered_businesses.csv",
    "no_site":  OUTPUT_DIR / "no_website.csv",
    "social":   OUTPUT_DIR / "has_social.csv",
    "outreach": OUTPUT_DIR / "outreach_ready.csv",
}
ENRICHED_DNS = OUTPUT_DIR / "filtered_with_dns.csv"

STAGES = ("parse", "dns", "social", "messages")
STAGE_LABEL = {
    "parse":    "Parse ABR XML",
    "dns":      "DNS check",
    "social":   "Find social media",
    "messages": "Generate messages",
}

TEST_LIMIT = 500


# ---------------------------------------------------------------------------
# RESULT BOOKKEEPING
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    """One row in the final summary table."""
    name: str
    rows_in: int = 0
    rows_out: int = 0
    seconds: float = 0.0
    skipped: bool = False  # set True when --resume reused an existing file
    output_path: Path | None = None


@dataclass
class Run:
    """Holds the per-run state shared between stages."""
    args: argparse.Namespace
    results: list[StageResult] = field(default_factory=list)
    t_start: float = field(default_factory=time.perf_counter)


# ---------------------------------------------------------------------------
# UI HELPERS
# ---------------------------------------------------------------------------

def _banner(text: str, width: int = 72, char: str = "=") -> None:
    """Print a centered banner inside a box."""
    print()
    print(char * width)
    print(f"  {text}")
    print(char * width)


def _stage_header(idx: int, total: int, name: str) -> None:
    """Print a per-stage header."""
    print()
    print("-" * 72)
    print(f"  [{idx}/{total}] {STAGE_LABEL[name]}")
    print("-" * 72)


def _human_size(num_bytes: int) -> str:
    """Render byte count as KB/MB/GB."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if num_bytes >= factor:
            return f"{num_bytes / factor:.1f} {unit}"
    return f"{num_bytes} B"


def _confirm(prompt: str) -> bool:
    """Return True if the user enters y/yes."""
    try:
        answer = input(f"{prompt} (y/n): ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


# ---------------------------------------------------------------------------
# PRE-FLIGHT
# ---------------------------------------------------------------------------

def _scan_xml(input_dir: Path) -> tuple[list[Path], int]:
    """Return (sorted xml file list, total size in bytes)."""
    files = sorted(input_dir.glob("*.xml"))
    total = sum(f.stat().st_size for f in files)
    return files, total


def print_preflight(args: argparse.Namespace) -> tuple[list[Path], int]:
    """Print config + file summary. Returns (xml_files, total_bytes)."""
    xml_files, total_bytes = _scan_xml(INPUT_DIR)

    _banner("ABR Outreach Pipeline — Pre-flight")
    print(f"  Step           : {args.step}")
    print(f"  Test mode      : {args.test}  ({TEST_LIMIT} record cap)" if args.test
          else f"  Test mode      : off")
    print(f"  Resume mode    : {args.resume}")
    print(f"  State filter   : {args.state or '(all states)'}")
    print(f"  GST filter     : {args.gst_status or '(all gst statuses)'}")
    print()
    print(f"  Input dir      : {INPUT_DIR}")
    print(f"  XML files      : {len(xml_files)}  (~{_human_size(total_bytes)})")
    if xml_files and len(xml_files) <= 8:
        for f in xml_files:
            print(f"     - {f.name}  ({_human_size(f.stat().st_size)})")
    print()
    print(f"  Output paths   :")
    for key, p in FILES.items():
        marker = " (exists)" if p.exists() else ""
        print(f"     - {key:10s}: {p.name}{marker}")
    print()
    print(f"  Brave API key  : {'set' if os.getenv('BRAVE_API_KEY') else 'MISSING'}")
    print(f"  Bing  API key  : {'set' if os.getenv('BING_API_KEY')  else 'missing'}")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    print(f"  Telegram       : "
          f"{'enabled' if tg_token and tg_chat else 'disabled (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)'}")
    return xml_files, total_bytes


# ---------------------------------------------------------------------------
# UTILITY: state filter
# ---------------------------------------------------------------------------

def _apply_state_filter(df: pd.DataFrame, state: str | None) -> pd.DataFrame:
    """If `state` is set, return only rows where df['state'] == state.

    Case-insensitive match. Leaves df untouched if state is empty or the
    column is missing.
    """
    if not state or "state" not in df.columns:
        return df
    mask = df["state"].astype(str).str.upper() == state.upper()
    return df[mask].copy()


def _apply_gst_filter(df: pd.DataFrame, gst_status: str | None) -> pd.DataFrame:
    """If `gst_status` is set, return only rows where df['gst_status'] matches.

    Case-insensitive match. ACT = active GST registration (the live outreach
    target), NON = never registered, CAN = cancelled.
    """
    if not gst_status or "gst_status" not in df.columns:
        return df
    mask = df["gst_status"].astype(str).str.upper() == gst_status.upper()
    return df[mask].copy()


def _apply_test_cap(df: pd.DataFrame, test: bool, label: str) -> pd.DataFrame:
    """If --test was passed and df is larger than TEST_LIMIT, truncate."""
    if test and len(df) > TEST_LIMIT:
        print(f"  [--test] truncating {label} {len(df):,} -> {TEST_LIMIT:,}")
        return df.head(TEST_LIMIT).copy()
    return df


# ---------------------------------------------------------------------------
# STAGES
# ---------------------------------------------------------------------------

def stage_parse(run: Run, idx: int, total: int) -> None:
    """XML -> filtered_businesses.csv (with optional state override)."""
    name = "parse"
    _stage_header(idx, total, name)

    out_path = FILES["filtered"]
    if run.args.resume and out_path.exists():
        df = pd.read_csv(out_path, dtype=str, keep_default_na=False, encoding="utf-8")
        print(f"  [--resume] reusing {out_path.name} ({len(df):,} rows)")
        run.results.append(StageResult(name, len(df), len(df), 0.0,
                                       skipped=True, output_path=out_path))
        return

    xml_files, _ = _scan_xml(INPUT_DIR)
    if not xml_files:
        print(f"  ERROR: no XML files in {INPUT_DIR}", file=sys.stderr)
        raise SystemExit(2)

    t0 = time.perf_counter()
    df = abr_parser.parse_directory(INPUT_DIR)
    rows_in = len(df)

    df = _apply_state_filter(df, run.args.state)
    if run.args.state:
        print(f"  state filter '{run.args.state.upper()}' kept "
              f"{len(df):,} / {rows_in:,} rows")

    before_gst = len(df)
    df = _apply_gst_filter(df, run.args.gst_status)
    if run.args.gst_status:
        print(f"  gst filter '{run.args.gst_status.upper()}' kept "
              f"{len(df):,} / {before_gst:,} rows")

    df = _apply_test_cap(df, run.args.test, "parsed rows")

    abr_parser.write_csv(df, out_path)
    abr_parser.print_statistics(df)

    run.results.append(StageResult(
        name=name, rows_in=rows_in, rows_out=len(df),
        seconds=time.perf_counter() - t0, output_path=out_path,
    ))


def stage_dns(run: Run, idx: int, total: int) -> None:
    """filtered_businesses.csv -> no_website.csv"""
    name = "dns"
    _stage_header(idx, total, name)

    out_path = FILES["no_site"]
    if run.args.resume and out_path.exists():
        df = pd.read_csv(out_path, dtype=str, keep_default_na=False, encoding="utf-8")
        print(f"  [--resume] reusing {out_path.name} ({len(df):,} rows)")
        run.results.append(StageResult(name, len(df), len(df), 0.0,
                                       skipped=True, output_path=out_path))
        return

    in_path = FILES["filtered"]
    if not in_path.exists():
        print(f"  ERROR: {in_path} not found — run --step parse first.",
              file=sys.stderr)
        raise SystemExit(2)

    df = pd.read_csv(in_path, dtype=str, keep_default_na=False, encoding="utf-8")
    rows_in = len(df)
    df = _apply_state_filter(df, run.args.state)
    before_gst = len(df)
    df = _apply_gst_filter(df, run.args.gst_status)
    if run.args.gst_status:
        print(f"  gst filter '{run.args.gst_status.upper()}' kept "
              f"{len(df):,} / {before_gst:,} rows")
    df = _apply_test_cap(df, run.args.test, "DNS input")

    t0 = time.perf_counter()
    name_col = "name_normalized" if "name_normalized" in df.columns else "business_name"
    print(f"  Checking DNS for {len(df):,} businesses (using {name_col}) ...")
    results = asyncio.run(check_dns.check_all(df[name_col].tolist(), 100))

    df["has_domain"] = [r[0] for r in results]
    df["found_domain"] = [r[1] for r in results]

    df.to_csv(ENRICHED_DNS, index=False, encoding="utf-8")
    no_site = df[~df["has_domain"]].copy()
    no_site.to_csv(out_path, index=False, encoding="utf-8")

    with_site = int(df["has_domain"].sum())
    print(f"  Has domain : {with_site:,}  ({(with_site/len(df)*100 if len(df) else 0):.1f}%)")
    print(f"  NO domain  : {len(no_site):,}")

    run.results.append(StageResult(
        name=name, rows_in=rows_in, rows_out=len(no_site),
        seconds=time.perf_counter() - t0, output_path=out_path,
    ))


def stage_social(run: Run, idx: int, total: int) -> None:
    """no_website.csv -> has_social.csv (Brave + Bing fallback)."""
    name = "social"
    _stage_header(idx, total, name)

    out_path = FILES["social"]
    if run.args.resume and out_path.exists():
        df = pd.read_csv(out_path, dtype=str, keep_default_na=False, encoding="utf-8")
        print(f"  [--resume] reusing {out_path.name} ({len(df):,} rows)")
        run.results.append(StageResult(name, len(df), len(df), 0.0,
                                       skipped=True, output_path=out_path))
        return

    if not find_social.BRAVE_API_KEY and not find_social.BING_API_KEY:
        print("  ERROR: BRAVE_API_KEY and BING_API_KEY both missing in .env",
              file=sys.stderr)
        raise SystemExit(2)

    in_path = FILES["no_site"]
    if not in_path.exists():
        print(f"  ERROR: {in_path} not found — run --step dns first.",
              file=sys.stderr)
        raise SystemExit(2)

    df = pd.read_csv(in_path, dtype=str, keep_default_na=False, encoding="utf-8")
    rows_in = len(df)
    df = _apply_state_filter(df, run.args.state)
    df = _apply_gst_filter(df, run.args.gst_status)
    df = _apply_test_cap(df, run.args.test, "social input")

    print(f"  Searching socials for {len(df):,} businesses "
          f"(rate: 1 req / {find_social.DELAY_MS}ms) ...")
    t0 = time.perf_counter()
    enriched = asyncio.run(find_social.lookup_all(df))

    out_df = pd.DataFrame({
        "abn":      enriched.get("abn", ""),
        "name":     enriched["business_name"],
        "state":    enriched.get("state", ""),
        "postcode": enriched.get("postcode", ""),
        "facebook":  enriched["facebook"],
        "instagram": enriched["instagram"],
    })
    keep = (out_df["facebook"] != "") | (out_df["instagram"] != "")
    out_df = out_df[keep].copy()
    out_df.to_csv(out_path, index=False, encoding="utf-8")

    n_fb = int((enriched["facebook"] != "").sum())
    n_ig = int((enriched["instagram"] != "").sum())
    print(f"  Found FB     : {n_fb:,}")
    print(f"  Found IG     : {n_ig:,}")
    print(f"  Either       : {len(out_df):,}")

    run.results.append(StageResult(
        name=name, rows_in=rows_in, rows_out=len(out_df),
        seconds=time.perf_counter() - t0, output_path=out_path,
    ))


def stage_messages(run: Run, idx: int, total: int) -> None:
    """has_social.csv -> outreach_ready.csv (one row per biz x platform)."""
    name = "messages"
    _stage_header(idx, total, name)

    out_path = FILES["outreach"]
    if run.args.resume and out_path.exists():
        df = pd.read_csv(out_path, dtype=str, keep_default_na=False, encoding="utf-8")
        print(f"  [--resume] reusing {out_path.name} ({len(df):,} rows)")
        run.results.append(StageResult(name, len(df), len(df), 0.0,
                                       skipped=True, output_path=out_path))
        return

    in_path = FILES["social"]
    if not in_path.exists():
        print(f"  ERROR: {in_path} not found — run --step social first.",
              file=sys.stderr)
        raise SystemExit(2)

    df = pd.read_csv(in_path, dtype=str, keep_default_na=False, encoding="utf-8")
    rows_in = len(df)
    df = _apply_state_filter(df, run.args.state)
    df = _apply_gst_filter(df, run.args.gst_status)
    df = _apply_test_cap(df, run.args.test, "messages input")

    t0 = time.perf_counter()
    out_df = generate_outreach.build_outreach(df)
    out_df.to_csv(
        out_path,
        index=False,
        encoding="utf-8",
        quoting=csv.QUOTE_ALL,
        lineterminator="\r\n",
    )
    generate_outreach.print_summary(out_df)

    run.results.append(StageResult(
        name=name, rows_in=rows_in, rows_out=len(out_df),
        seconds=time.perf_counter() - t0, output_path=out_path,
    ))


STAGE_FUNCS = {
    "parse":    stage_parse,
    "dns":      stage_dns,
    "social":   stage_social,
    "messages": stage_messages,
}


# ---------------------------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> None:
    """Best-effort Telegram message. Silently disables itself on errors."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("  Telegram notification sent.")
    except Exception as exc:  # noqa: BLE001 — never fail the pipeline on this
        print(f"  Telegram notification skipped: {exc}")


# ---------------------------------------------------------------------------
# FINAL SUMMARY
# ---------------------------------------------------------------------------

def print_summary(run: Run) -> str:
    """Print + return a formatted summary table (plain text, also for Telegram)."""
    elapsed_total = time.perf_counter() - run.t_start

    lines: list[str] = []
    lines.append("Pipeline summary")
    lines.append("")
    lines.append(f"{'Stage':<12} {'In':>10} {'Out':>10} {'Time':>10}  Status")
    lines.append("-" * 60)
    for r in run.results:
        status = "skipped" if r.skipped else "ok"
        time_str = f"{r.seconds:.1f}s" if r.seconds else "-"
        lines.append(
            f"{r.name:<12} {r.rows_in:>10,} {r.rows_out:>10,} {time_str:>10}  {status}"
        )
    lines.append("-" * 60)
    lines.append(f"{'TOTAL':<12} {'':>10} {'':>10} {elapsed_total:>9.1f}s")

    # Resolved file paths so the user sees exactly what to open
    lines.append("")
    lines.append("Outputs:")
    for r in run.results:
        if r.output_path:
            lines.append(f"  {r.name:<10s} -> {r.output_path}")

    text = "\n".join(lines)
    _banner("DONE")
    print(text)
    print()
    return text


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def _stages_to_run(step: str) -> list[str]:
    """Translate --step argument into an ordered list of stage names."""
    if step == "all":
        return list(STAGES)
    if step in STAGES:
        return [step]
    raise ValueError(f"Unknown step: {step!r}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="ABR outreach pipeline — single entry point.",
    )
    ap.add_argument(
        "--step", choices=("all",) + STAGES, default="all",
        help="Pipeline stage to run (default: all).",
    )
    ap.add_argument(
        "--test", action="store_true",
        help=f"Process only the first {TEST_LIMIT} records at each stage.",
    )
    ap.add_argument(
        "--state", type=str, default="",
        help="Override state filter (e.g. NSW). Applied after each CSV is read.",
    )
    ap.add_argument(
        "--gst-status", dest="gst_status", type=str, default="",
        help="Filter by GST status (ACT=active, NON=never registered, CAN=cancelled). "
             "ACT is the live outreach target.",
    )
    ap.add_argument(
        "--resume", action="store_true",
        help="Skip stages whose output CSV already exists.",
    )
    ap.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt (useful for cron / CI).",
    )
    args = ap.parse_args(argv)

    xml_files, total_bytes = print_preflight(args)

    # Confirmation prompt — required for `all`-style runs only.
    if args.step == "all" and not args.yes:
        size_str = _human_size(total_bytes) if xml_files else "no XML yet"
        prompt = (f"Found {len(xml_files)} XML file(s) (~{size_str}). Continue?")
        if not _confirm(prompt):
            print("Aborted.")
            return 1

    run = Run(args=args)
    stages = _stages_to_run(args.step)
    total = len(stages)

    try:
        for i, stage_name in enumerate(stages, start=1):
            STAGE_FUNCS[stage_name](run, i, total)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001 — surface but don't traceback-spam
        print(f"\nFATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

    summary_text = print_summary(run)

    if args.step == "all" or len(run.results) > 1:
        send_telegram(f"```\n{summary_text}\n```")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
