"""Outreach-generation stage: turn social-enriched rows into ready-to-send DMs.

Reads ./output/has_social.csv (output of find_social.py) and produces
./output/outreach_ready.csv, one row per (business x platform) — meaning a
business with both Facebook AND Instagram becomes TWO rows so each DM
can be tracked independently in Google Sheets.

Pipeline:
    1. Classify each business into an industry bucket from the name.
    2. Pick a template (3 per industry, rotated by hash → deterministic).
    3. Substitute placeholders (name, state, suburb).
    4. Validate ≤ 4 sentences and ends with '?'. If a template ever
       violated this, the build would fail on the self-test below — so
       generated messages are guaranteed to meet the spec.

Output schema (Google-Sheets friendly):
    abn, name, industry, state, postcode,
    platform, profile_url, message, status

`status` is always 'pending' — your sheet/CRM updates it later
('sent', 'replied', 'booked', ...).

Run:
    python generate_outreach.py
    python generate_outreach.py -i has_social.csv -o outreach_ready.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# INDUSTRY CLASSIFICATION
# ---------------------------------------------------------------------------

# Order matters: more specific buckets come first so "dental clinic"
# doesn't get caught by the generic "trades" bucket.
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "plumbing":    ("plumb", "plumber", "plumbing", "drain", "gas fit"),
    "electrical":  ("electric", "electrician", "sparky", "lighting"),
    "cafe":        ("cafe", "coffee", "espresso", "roaster", "barista", "restaurant", "bistro", "eatery"),
    "salon":       ("salon", "hair", "barber", "beauty", "nails", "lash", "brow", "waxing"),
    "dental":      ("dental", "dentist", "orthodont", "endodont"),
    "automotive":  ("mechanic", "automotive", "auto repair", "tyres", "tyre", "panel beat", "smash repair", "car service"),
    "cleaning":    ("clean", "cleaning", "janitor", "housekeep"),
    "trades":      ("roof", "roofing", "gutter", "paint", "painter", "concret",
                    "fenc", "landscap", "garden", "lawn", "turf",
                    "carpent", "build", "tiler", "tiling", "render",
                    "pest", "aircon", "air-con", "hvac"),
}

INDUSTRY_FALLBACK = "general"
ALL_INDUSTRIES = list(INDUSTRY_KEYWORDS.keys()) + [INDUSTRY_FALLBACK]


def detect_industry(name: str) -> str:
    """Classify a business name into a known industry bucket.

    Args:
        name: raw entity name.

    Returns:
        One of: plumbing, electrical, cafe, salon, dental, automotive,
        cleaning, trades, or 'general' (fallback).
    """
    if not name:
        return INDUSTRY_FALLBACK
    lname = name.lower()
    for industry, needles in INDUSTRY_KEYWORDS.items():
        for needle in needles:
            if needle in lname:
                return industry
    return INDUSTRY_FALLBACK


# ---------------------------------------------------------------------------
# TEMPLATES  (3 per industry, English, ≤ 4 sentences, ends with '?')
# ---------------------------------------------------------------------------
#
# Placeholders:
#   {first_word}  — first token of the business name (rough "first name")
#   {name}        — full business name as-is
#   {place}       — suburb if present, otherwise state, otherwise "Australia"
#   {state}       — state code only
#
# Tone rules:
#   - Conversational, lowercase opener
#   - No "I hope this finds you well"
#   - No exclamation marks, no emojis
#   - End with a real question (single '?')

TEMPLATES: dict[str, list[str]] = {
    "plumbing": [
        "Hey {first_word} — saw your page while looking up plumbers in {place}. "
        "Quick one: most plumbers I chat with are losing a few quotes a week "
        "because their online booking is messy or non-existent. "
        "Is that something you've noticed too?",

        "Hi there — came across {name} and noticed you're flat out judging by "
        "the reviews. Out of curiosity, how are people booking jobs with you "
        "at the moment — DMs, phone, or something else? "
        "Would a quick online booking link actually help, or just create noise?",

        "Hey, hope the week's not too crazy. I help {place} trades pick up "
        "1-2 extra jobs a week by tidying up the bit between 'someone finds "
        "you online' and 'they actually book'. Worth a 10-min chat to see if "
        "the same thing would work for {first_word}?",
    ],

    "electrical": [
        "Hey {first_word} — saw your page while looking for sparkies in {place}. "
        "Most electricians I speak to are missing jobs because leads slip "
        "between Facebook messages, calls, and texts. "
        "Is that something you'd want sorted, or are you across it already?",

        "Hi — came across {name} and your work looks tidy. Quick question: "
        "when someone DMs you for a quote, how long usually before you get "
        "back to them? "
        "Curious if a same-hour reply would actually win you more jobs, or if it's overkill?",

        "Hey, hope you're not on the tools right now. I help {state} "
        "electricians turn their socials into a steady booking source — no "
        "ads, just better follow-up. Open to a quick chat about whether "
        "it'd fit your setup?",
    ],

    "cafe": [
        "Hey {first_word} team — wandered onto your page and now I want a "
        "coffee in {place}. Quick one: are you tracking how many bookings or "
        "catering orders actually come through Instagram vs walk-ins? "
        "Would knowing that change how you spend your time on socials?",

        "Hi — {name} looks like the kind of spot I'd grab a flat white at. "
        "Out of curiosity, are catering and function inquiries coming "
        "through clean for you, or do they all live in DMs and get lost? "
        "Would a single inbox for that be useful, or already sorted?",

        "Hey, hope service hasn't been too brutal. I help cafes in {state} "
        "automate the boring bits — function inquiries, gift card orders, "
        "Google review nudges. Worth 10 minutes to see if any of that "
        "would actually save you time?",
    ],

    "salon": [
        "Hey {first_word} — your page came up while I was looking at salons "
        "in {place}. Most salon owners I chat with say no-shows and DM "
        "back-and-forth are the worst part of the week. "
        "Is that true for you, or have you cracked it?",

        "Hi — came across {name} and the work in your feed is gorgeous. "
        "Quick question: when clients DM to book, do they end up booking, "
        "or do half of them ghost? "
        "Curious if a one-tap booking link in your bio would actually convert better?",

        "Hey, hope today's chair was a kind one. I help {state} salons "
        "shave a few hours of admin a week — auto reminders, rebook nudges, "
        "DM-to-booking flow. Would that be worth a quick chat, or you're "
        "happy with how it runs now?",
    ],

    "dental": [
        "Hi {first_word} team — saw your practice page while researching "
        "clinics in {place}. Quick one: are new patient inquiries coming "
        "through your socials, or mostly via phone and Google? "
        "Would more of them switching to online booking actually help your front desk, or hurt?",

        "Hi — came across {name} and the patient reviews look great. "
        "Curious how you currently handle after-hours DMs asking about "
        "appointments — auto-reply, ignore, or someone catches them in the morning? "
        "Would a same-night reply change your booking rate?",

        "Hey, hope clinic's calm today. I help dental practices in {state} "
        "tidy up the 'inquiry to booking' bit — usually 1-2 extra new "
        "patients a week. Worth a quick chat about whether the same would "
        "work for your front desk?",
    ],

    "automotive": [
        "Hey {first_word} — saw your shop while looking for mechanics in "
        "{place}. Most workshops I chat with are still juggling quotes and "
        "service bookings across texts and calls. "
        "Is that draining your time too, or have you got it under control?",

        "Hi — came across {name} and your reviews look solid. Out of "
        "curiosity, when someone DMs asking 'how much for a service', do "
        "you reply each one manually, or have something set up? "
        "Would automating the obvious ones save you real time, or feel impersonal?",

        "Hey, hope the bays aren't too packed. I help {state} workshops "
        "stop losing leads between socials, phone, and email — basically "
        "one inbox for the lot. Worth 10 minutes to see if it'd fit how "
        "you run things?",
    ],

    "cleaning": [
        "Hey {first_word} — saw {name} while looking at cleaning businesses "
        "in {place}. Most operators I chat with are wasting time quoting "
        "leads that never reply or weren't serious. "
        "Is that something you'd like filtered out before it hits your inbox?",

        "Hi — came across your page and your jobs look tidy. Quick "
        "question: when someone DMs for a quote, are you doing the "
        "back-and-forth manually, or have a form/booking link? "
        "Would a single 'tell me your job' flow save you a few hours a week?",

        "Hey, hope today's run was smooth. I help {state} cleaning crews "
        "convert more DMs to actual bookings — usually by tightening "
        "follow-up and adding a quick quote form. Worth a chat about "
        "whether that'd suit {first_word}?",
    ],

    "trades": [
        "Hey {first_word} — saw {name} while looking up trades in {place}. "
        "Most trades I chat with are missing jobs because quotes sit in "
        "their DMs for a day or two. "
        "Is that happening for you, or are you across it already?",

        "Hi — came across your page and the work looks neat. Out of "
        "curiosity, are most of your jobs coming from word of mouth, "
        "Google, or socials? "
        "Would you actually want more inquiries from socials, or is the pipeline already full?",

        "Hey, hope you're not on a roof right now. I help {state} trades "
        "pick up 1-2 extra jobs a week by sorting the 'someone finds you "
        "online → they book' bit. Worth 10 minutes to see if it'd work "
        "for {first_word}?",
    ],

    "general": [
        "Hey {first_word} — came across {name} while looking at businesses "
        "in {place}. Quick question: are most of your customers finding you "
        "through socials, or word of mouth? "
        "Would more inquiries via DMs actually help, or just clog your day?",

        "Hi — your page caught my eye. Out of curiosity, how do leads "
        "usually reach you at the moment — DMs, phone, walk-in? "
        "Is there one of those that's working great, or one that drives you nuts?",

        "Hey, hope the week's been kind. I help {state} businesses turn "
        "their socials into a steadier booking source without ads — just "
        "better follow-up. Worth a quick chat about whether that'd fit "
        "{first_word}?",
    ],
}

# Build-time guarantee: every template list has exactly 3 items and each
# message ends with '?' and has ≤ 4 sentences. Asserted at import time so
# the script will literally not run if you break the rules above.
_SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)")


def _sentence_count(text: str) -> int:
    """Count sentence-ending punctuation tokens."""
    return len([m for m in _SENTENCE_RE.finditer(text) if m])


for _industry, _list in TEMPLATES.items():
    assert len(_list) == 3, f"{_industry}: needs 3 templates, has {len(_list)}"
    for _i, _tpl in enumerate(_list):
        assert _tpl.rstrip().endswith("?"), \
            f"{_industry}[{_i}]: must end with '?'"
        assert _sentence_count(_tpl) <= 4, \
            f"{_industry}[{_i}]: must be <= 4 sentences (got {_sentence_count(_tpl)})"

assert set(TEMPLATES.keys()) == set(ALL_INDUSTRIES), \
    "TEMPLATES keys must cover every industry bucket"


# ---------------------------------------------------------------------------
# PLACEHOLDER SUBSTITUTION
# ---------------------------------------------------------------------------

def _first_word(name: str) -> str:
    """Return a 'first name' for the opener (capitalized first token)."""
    if not name:
        return "there"
    tok = re.split(r"\s+", name.strip(), maxsplit=1)[0]
    tok = re.sub(r"[^A-Za-z0-9'-]", "", tok)
    return tok.title() if tok else "there"


def _place(suburb: str, state: str) -> str:
    """Best human-readable location: suburb > state > 'Australia'."""
    if suburb:
        return suburb.title()
    if state:
        return state
    return "Australia"


def _pick_template_index(key: str, count: int) -> int:
    """Deterministic 0..count-1 from a string key (stable across runs)."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest, 16) % count


def render_message(industry: str, name: str, state: str, suburb: str = "") -> str:
    """Render a single outreach message.

    Args:
        industry: industry bucket (must exist in TEMPLATES).
        name: business name (used for {name} and {first_word}).
        state: 2-3 letter state code (used for {state}).
        suburb: optional suburb (used for {place} if present).

    Returns:
        Personalized message string. Falls back to 'general' bucket if
        `industry` is unknown.
    """
    bucket = TEMPLATES.get(industry) or TEMPLATES[INDUSTRY_FALLBACK]
    # Stable per business: same name → same template across runs.
    idx = _pick_template_index(name or industry, len(bucket))
    tpl = bucket[idx]
    return tpl.format(
        first_word=_first_word(name),
        name=name or "your business",
        state=state or "Australia",
        place=_place(suburb, state),
    )


# ---------------------------------------------------------------------------
# DRIVER
# ---------------------------------------------------------------------------

def _row_dict(record: dict, platform: str, profile_url: str, message: str) -> dict:
    """Build the final output row in the exact schema."""
    return {
        "abn": record.get("abn", ""),
        "name": record.get("name", ""),
        "industry": record.get("industry", ""),
        "state": record.get("state", ""),
        "postcode": record.get("postcode", ""),
        "platform": platform,
        "profile_url": profile_url,
        "message": message,
        "status": "pending",
    }


def build_outreach(df: pd.DataFrame) -> pd.DataFrame:
    """Expand has_social rows into one outreach row per (business, platform).

    A business with both FB and IG produces two rows; one with only FB
    produces one row; rows with neither are dropped.
    """
    rows: list[dict] = []

    name_col = "name" if "name" in df.columns else "business_name"
    suburb_col = "suburb" if "suburb" in df.columns else None

    for _, r in df.iterrows():
        name = str(r.get(name_col, "") or "").strip()
        state = str(r.get("state", "") or "").strip()
        suburb = str(r.get(suburb_col, "") or "").strip() if suburb_col else ""
        fb = str(r.get("facebook", "") or "").strip()
        ig = str(r.get("instagram", "") or "").strip()

        if not name or (not fb and not ig):
            continue

        industry = detect_industry(name)
        message = render_message(industry, name, state, suburb)

        base = {
            "abn": str(r.get("abn", "") or "").strip(),
            "name": name,
            "industry": industry,
            "state": state,
            "postcode": str(r.get("postcode", "") or "").strip(),
        }

        if fb:
            rows.append(_row_dict(base, "Facebook", fb, message))
        if ig:
            rows.append(_row_dict(base, "Instagram", ig, message))

    return pd.DataFrame(rows, columns=[
        "abn", "name", "industry", "state", "postcode",
        "platform", "profile_url", "message", "status",
    ])


def print_summary(df: pd.DataFrame) -> None:
    """Print totals + per-platform + per-industry breakdown."""
    if df.empty:
        print("\nNo outreach rows generated — nothing to summarize.")
        return

    print("\n" + "=" * 50)
    print(f"TOTAL READY      : {len(df):,}")
    print(f"Unique businesses: {df['abn'].nunique():,}")
    print("=" * 50)

    print("\nBy platform:")
    for p, n in df["platform"].value_counts().items():
        print(f"  {p:<12} {n:>8,}")

    print("\nBy industry:")
    for i, n in df["industry"].value_counts().items():
        print(f"  {i:<12} {n:>8,}")

    print("\nBy state:")
    for s, n in df["state"].replace("", "(unknown)").value_counts().items():
        print(f"  {s:<12} {n:>8,}")
    print()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns an exit code."""
    ap = argparse.ArgumentParser(
        description="Generate personalized outreach messages from has_social.csv."
    )
    ap.add_argument("--input", "-i", type=Path, default=Path("./output/has_social.csv"))
    ap.add_argument("--output", "-o", type=Path, default=Path("./output/outreach_ready.csv"))
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input CSV not found: {args.input}", file=sys.stderr)
        return 2

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False, encoding="utf-8")

    if df.empty:
        print(f"WARNING: {args.input} is empty — nothing to do.")
        return 0

    name_col = "name" if "name" in df.columns else "business_name"
    if name_col not in df.columns:
        print("ERROR: input CSV missing required column 'name' or 'business_name'",
              file=sys.stderr)
        return 2

    print(f"Reading {len(df):,} rows from {args.input} ...")
    out = build_outreach(df)

    # Google Sheets is happy with UTF-8, quoted strings, CRLF line endings.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(
        args.output,
        index=False,
        encoding="utf-8",
        quoting=csv.QUOTE_ALL,
        lineterminator="\r\n",
    )

    print_summary(out)
    print(f"Wrote {len(out):,} outreach rows -> {args.output.resolve()}")
    print("Import this file directly into Google Sheets (File > Import > Upload).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
