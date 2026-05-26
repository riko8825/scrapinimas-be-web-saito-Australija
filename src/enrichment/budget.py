"""Cost cap guards — hard stop'ina enrichment, jei mėnesinis budget'as viršyt.

Tikslas: niekada netyčia neišleisti $3,500 už nakties run'ą.

Naudojama PRIEŠ kiekvieną API call'ą (Stage A Places, Stage C SerpAPI).
Stage B (website scrape) = $0, jokio guard'o nereikia.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# DEFAULT monthly caps — overridable per .env
# ---------------------------------------------------------------------------
DEFAULT_CAPS_USD: dict[str, float] = {
    "a": 50.0,     # Stage A: Places ($35 = ~1k calls)
    "c": 30.0,     # Stage C: SerpAPI ($25 = 5k calls)
}


def month_to_date_spend(conn: sqlite3.Connection, stage: str) -> float:
    """USD spent this calendar month for a stage (from enrichment_runs)."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    row = conn.execute(
        """SELECT COALESCE(SUM(cost_usd), 0)
           FROM enrichment_runs
           WHERE stage = ? AND started_at >= ?""",
        (stage, month_start),
    ).fetchone()
    return float(row[0] if row else 0.0)


def can_spend(
    conn: sqlite3.Connection,
    stage: str,
    additional_usd: float,
    monthly_cap_usd: float | None = None,
) -> tuple[bool, float, float]:
    """Ar saugu išleisti N papildomų USD šitam stage'ui?

    Returns: (allowed, current_spend, cap)
    """
    cap = monthly_cap_usd if monthly_cap_usd is not None else DEFAULT_CAPS_USD.get(stage, 0)
    current = month_to_date_spend(conn, stage)
    return (current + additional_usd <= cap, current, cap)


def estimate_stage_cost(stage: str, n_calls: int) -> float:
    """USD estimate prieš batch'o paleidimą.

    Stage A (Places Enterprise SKU): $35/1k, BET pirmi 1k/mėn FREE
    Stage C (SerpAPI): $5/1k

    Argument: assume worst case = visi calls billable (ignoruoja free tier
    — saugiau, kad nepamesim track).
    """
    rates = {
        "a": 0.035,   # $35/1000
        "c": 0.005,   # $5/1000
    }
    rate = rates.get(stage, 0)
    return n_calls * rate
