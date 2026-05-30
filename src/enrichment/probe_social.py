"""PROBE: ar Google Places websiteUri grąžina FB/IG verslams be tikros svetainės?

Vienkartinis diagnostinis skriptas (NE production modulis). Paima N leads iš
`output/outreach_ready_*.csv`, kurie pažymėti BE svetainės, ir per Google Places
Text Search patikrina ką realiai grąžina websiteUri laukas:
  - tikra svetainė (.com.au, .com ir t.t.)
  - FB/IG puslapis (facebook.com / instagram.com)
  - nieko (not_found)

Tikslas: nuspręsti ar verta statyti pilną social-enrichment modulį 87 leads'ams
PRIEŠ leidžiant Places kreditus visiems.

Run:
    python -m src.enrichment.probe_social --csv output/outreach_ready_20260526_1829.csv --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
PLACES_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
PLACES_TIMEOUT = 15.0

FIELD_MASK = (
    "places.id,"
    "places.displayName,"
    "places.formattedAddress,"
    "places.internationalPhoneNumber,"
    "places.websiteUri,"
    "places.types"
)

SOCIAL_HOSTS = ("facebook.com", "fb.com", "instagram.com", "instagr.am")


def _no_site(row: dict) -> bool:
    """True jei lead pažymėtas be svetainės."""
    w = (row.get("website") or "").strip().lower()
    return w in ("", "no", "none", "nan", "false", "0")


def _classify_url(url: str | None) -> str:
    """Grąžina 'social' / 'website' / 'none' pagal websiteUri host'ą."""
    if not url:
        return "none"
    host = (urlparse(url).netloc or "").lower().lstrip("www.")
    if any(s in host for s in SOCIAL_HOSTS):
        return "social"
    return "website"


async def _probe_one(client: httpx.AsyncClient, name: str, postcode: str) -> dict:
    """Vienas Text Search. Grąžina dict su websiteUri + klasifikacija. NEVER raises."""
    out = {"website_uri": None, "kind": "error", "detail": ""}
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    body = {
        "textQuery": f"{name} {postcode}".strip(),
        "regionCode": "AU",
        "pageSize": 1,
    }
    try:
        resp = await client.post(PLACES_ENDPOINT, headers=headers, json=body, timeout=PLACES_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 — diagnostika, tik log
        out["detail"] = f"{type(e).__name__}: {str(e)[:150]}"
        return out

    places = data.get("places") or []
    if not places:
        out["kind"] = "not_found"
        return out
    uri = places[0].get("websiteUri")
    out["website_uri"] = uri
    out["kind"] = _classify_url(uri)
    return out


_TAGS = {
    "social": "✅ SOCIAL",
    "website": "🌐 website",
    "none": "— no_uri",
    "not_found": "∅ not_found",
    "error": "✗ error",
}


async def _run(rows: list[dict]) -> None:
    counts: dict[str, int] = {}
    async with httpx.AsyncClient() as client:
        for i, r in enumerate(rows, 1):
            name = (r.get("business_name") or r.get("trading_name") or "").strip()
            postcode = (r.get("postcode") or "").strip()
            res = await _probe_one(client, name, postcode)
            counts[res["kind"]] = counts.get(res["kind"], 0) + 1
            tag = _TAGS.get(res["kind"], res["kind"])
            print(f"{i}. {name[:40]:40} | {tag:14} | {res['website_uri'] or res['detail'] or '-'}")

    print("\nSantrauka:", ", ".join(f"{_TAGS.get(k, k)}={v}" for k, v in sorted(counts.items())))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Probe Places websiteUri for no-website leads")
    p.add_argument("--csv", required=True, help="Path to outreach_ready CSV")
    p.add_argument("--limit", type=int, default=5, help="How many no-website leads to probe")
    args = p.parse_args(argv)

    if not PLACES_API_KEY:
        print("ERROR: GOOGLE_PLACES_API_KEY not set in .env", file=sys.stderr)
        return 1

    path = ROOT / args.csv if not Path(args.csv).is_absolute() else Path(args.csv)
    if not path.exists():
        print(f"ERROR: CSV not found: {path}", file=sys.stderr)
        return 1

    all_rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    no_site = [r for r in all_rows if _no_site(r)][: args.limit]
    if not no_site:
        print("No no-website leads found in CSV.")
        return 0

    print(f"Probing {len(no_site)} no-website leads via Places Text Search...\n")
    asyncio.run(_run(no_site))
    print(
        "\nLegenda: ✅ SOCIAL = Google turi jų FB/IG kaip websiteUri (tinka kriterijui), "
        "🌐 website = tikra svetainė (tada NE be svetainės), "
        "— no_uri = verslą rado, bet websiteUri laukas tuščias (nei svetainės, nei social), "
        "∅ not_found = Google verslo nerado."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
