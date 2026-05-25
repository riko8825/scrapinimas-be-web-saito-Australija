"""Cached SQL queries for the dashboard.

Heavy aggregations are wrapped in `st.cache_data(ttl=60)` so the UI stays
snappy with 97k+ leads. The cache key is the SQL params + db_path mtime
hash, so reimporting the CSV invalidates everything automatically.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard.db import connect, default_db_path


# --- DB freshness hash ---------------------------------------------------------

def db_fingerprint(db_path: Path | str | None = None) -> float:
    """Mtime of outreach.db — used to bust caches after import."""
    p = Path(db_path) if db_path else default_db_path()
    try:
        return p.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _read_sql(sql: str, params: tuple = ()) -> pd.DataFrame:
    with connect(default_db_path()) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# --- Lead listing (filtered) ---------------------------------------------------

LEAD_LIST_SQL = """
SELECT
    l.abn,
    l.business_name,
    l.state,
    l.postcode,
    l.entity_type,
    l.gst_status,
    l.industry_keyword,
    l.facebook_url,
    l.instagram_url,
    l.has_domain,
    l.found_domain,
    o.status,
    o.priority,
    o.sent_at,
    o.sent_channel,
    o.replied_at,
    o.booked_at,
    o.contact_name,
    o.contact_email,
    o.contact_phone,
    o.notes,
    o.tags,
    o.assigned_to,
    o.updated_at
FROM leads l
LEFT JOIN outreach o ON o.abn = l.abn
"""


@st.cache_data(ttl=60, show_spinner=False)
def fetch_leads(
    fp: float,                                                # noqa: ARG001
    states: tuple[str, ...] = (),
    industries: tuple[str, ...] = (),
    entity_types: tuple[str, ...] = (),
    gst_statuses: tuple[str, ...] = (),
    outreach_statuses: tuple[str, ...] = (),
    has_social: bool = False,
    has_phone: bool = False,
    has_email: bool = False,
    max_priority: int = 5,
    search: str = "",
    limit: int = 5000,
) -> pd.DataFrame:
    where: list[str] = []
    params: list = []

    def _in(col: str, values: tuple[str, ...]) -> None:
        if not values:
            return
        placeholders = ",".join("?" * len(values))
        where.append(f"{col} IN ({placeholders})")
        params.extend(values)

    _in("l.state",       states)
    _in("l.industry_keyword", industries)
    _in("l.entity_type", entity_types)
    _in("l.gst_status",  gst_statuses)
    _in("COALESCE(o.status, 'new')", outreach_statuses)

    if has_social:
        where.append("(l.facebook_url IS NOT NULL OR l.instagram_url IS NOT NULL)")
    if has_phone:
        where.append("o.contact_phone IS NOT NULL AND o.contact_phone != ''")
    if has_email:
        where.append("o.contact_email IS NOT NULL AND o.contact_email != ''")
    if max_priority < 5:
        where.append("COALESCE(o.priority, 3) <= ?")
        params.append(max_priority)

    if search:
        like = f"%{search.lower()}%"
        where.append(
            "(LOWER(l.business_name) LIKE ? "
            " OR l.abn LIKE ? "
            " OR l.postcode LIKE ?)"
        )
        params.extend([like, like, like])

    sql = LEAD_LIST_SQL
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(o.priority, 3) ASC, l.business_name ASC LIMIT ?"
    params.append(limit)

    return _read_sql(sql, tuple(params))


# --- KPIs ----------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def kpi_counts(fp: float) -> dict[str, int]:                  # noqa: ARG001
    sql = """
    SELECT
        (SELECT COUNT(*) FROM leads)                                AS total,
        (SELECT COUNT(*) FROM leads
            WHERE facebook_url IS NOT NULL OR instagram_url IS NOT NULL) AS contactable,
        COALESCE(SUM(CASE WHEN o.status = 'queued'  THEN 1 END), 0) AS queued,
        COALESCE(SUM(CASE WHEN o.status = 'sent'    THEN 1 END), 0) AS sent,
        COALESCE(SUM(CASE WHEN o.status = 'replied' THEN 1 END), 0) AS replied,
        COALESCE(SUM(CASE WHEN o.status = 'booked'  THEN 1 END), 0) AS booked,
        COALESCE(SUM(CASE WHEN o.status = 'won'     THEN 1 END), 0) AS won,
        COALESCE(SUM(CASE WHEN o.status = 'lost'    THEN 1 END), 0) AS lost,
        COALESCE(SUM(CASE WHEN o.status = 'skip'    THEN 1 END), 0) AS skip,
        COALESCE(SUM(CASE WHEN DATE(o.sent_at) = DATE('now') THEN 1 END), 0)
            AS sent_today,
        COALESCE(SUM(CASE WHEN DATE(o.sent_at) >= DATE('now','-6 days')
                          THEN 1 END), 0) AS sent_week
    FROM outreach o
    """
    with connect(default_db_path()) as conn:
        row = conn.execute(sql).fetchone()
    return dict(row) if row else {}


# --- Funnel --------------------------------------------------------------------

FUNNEL_ORDER = ["new", "queued", "sent", "replied", "booked", "won"]


@st.cache_data(ttl=60, show_spinner=False)
def funnel_df(fp: float) -> pd.DataFrame:                     # noqa: ARG001
    sql = """
    SELECT COALESCE(status, 'new') AS status, COUNT(*) AS n
    FROM outreach
    GROUP BY status
    """
    df = _read_sql(sql)
    # ensure all funnel stages present
    by_status = dict(zip(df["status"], df["n"], strict=False))
    return pd.DataFrame({
        "status": FUNNEL_ORDER,
        "n":      [int(by_status.get(s, 0)) for s in FUNNEL_ORDER],
    })


# --- Geo / industry / entity ---------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def by_state(fp: float) -> pd.DataFrame:                      # noqa: ARG001
    sql = """
    SELECT
        l.state,
        COUNT(*) AS leads,
        COALESCE(SUM(CASE WHEN o.status = 'sent' THEN 1 END), 0)    AS sent,
        COALESCE(SUM(CASE WHEN o.status = 'replied' THEN 1 END), 0) AS replied,
        COALESCE(SUM(CASE WHEN o.status = 'won' THEN 1 END), 0)     AS won
    FROM leads l
    LEFT JOIN outreach o ON o.abn = l.abn
    WHERE l.state IS NOT NULL AND l.state != ''
    GROUP BY l.state
    ORDER BY leads DESC
    """
    return _read_sql(sql)


@st.cache_data(ttl=60, show_spinner=False)
def by_industry(fp: float, top: int = 15) -> pd.DataFrame:    # noqa: ARG001
    sql = """
    SELECT
        COALESCE(l.industry_keyword, 'other') AS industry,
        COUNT(*) AS leads,
        COALESCE(SUM(CASE WHEN o.status IN ('sent','replied','booked','won')
                          THEN 1 END), 0) AS outreached
    FROM leads l
    LEFT JOIN outreach o ON o.abn = l.abn
    GROUP BY industry
    ORDER BY leads DESC
    LIMIT ?
    """
    return _read_sql(sql, (top,))


@st.cache_data(ttl=60, show_spinner=False)
def by_entity_type(fp: float) -> pd.DataFrame:                # noqa: ARG001
    sql = """
    SELECT COALESCE(entity_type,'?') AS entity_type, COUNT(*) AS n
    FROM leads
    GROUP BY entity_type
    ORDER BY n DESC
    """
    return _read_sql(sql)


# --- Timeline ------------------------------------------------------------------

@st.cache_data(ttl=60, show_spinner=False)
def activity_timeline(fp: float, days: int = 30) -> pd.DataFrame:  # noqa: ARG001
    sql = """
    SELECT
        DATE(happened_at) AS day,
        SUM(CASE WHEN action = 'status_change' AND detail LIKE 'sent%'
                 THEN 1 ELSE 0 END) AS sent,
        SUM(CASE WHEN action = 'status_change' AND detail LIKE 'replied%'
                 THEN 1 ELSE 0 END) AS replied,
        SUM(CASE WHEN action = 'status_change' AND detail LIKE 'booked%'
                 THEN 1 ELSE 0 END) AS booked,
        SUM(CASE WHEN action = 'status_change' AND detail LIKE 'won%'
                 THEN 1 ELSE 0 END) AS won
    FROM activity
    WHERE happened_at >= DATE('now', ? )
    GROUP BY day
    ORDER BY day
    """
    return _read_sql(sql, (f"-{days} days",))


@st.cache_data(ttl=60, show_spinner=False)
def activity_log(fp: float, abn: str | None = None,           # noqa: ARG001
                 limit: int = 200) -> pd.DataFrame:
    if abn:
        sql = """
        SELECT a.happened_at, a.abn, l.business_name,
               a.action, a.detail, a.actor
        FROM activity a
        LEFT JOIN leads l ON l.abn = a.abn
        WHERE a.abn = ?
        ORDER BY a.happened_at DESC
        LIMIT ?
        """
        return _read_sql(sql, (abn, limit))
    sql = """
    SELECT a.happened_at, a.abn, l.business_name,
           a.action, a.detail, a.actor
    FROM activity a
    LEFT JOIN leads l ON l.abn = a.abn
    WHERE a.action != 'import'
    ORDER BY a.happened_at DESC
    LIMIT ?
    """
    return _read_sql(sql, (limit,))


# --- Filter option lookups (lightweight, cached longer) -----------------------

@st.cache_data(ttl=300, show_spinner=False)
def distinct_values(fp: float, column: str) -> list[str]:     # noqa: ARG001
    allowed = {
        "state", "entity_type", "gst_status", "industry_keyword",
    }
    if column not in allowed:
        raise ValueError(f"column not allowed: {column}")
    df = _read_sql(
        f"SELECT DISTINCT {column} AS v FROM leads "
        f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
    )
    return df["v"].tolist()


@st.cache_data(ttl=300, show_spinner=False)
def last_import(fp: float) -> dict | None:                    # noqa: ARG001
    sql = "SELECT * FROM imports ORDER BY id DESC LIMIT 1"
    df = _read_sql(sql)
    if df.empty:
        return None
    return df.iloc[0].to_dict()


# --- Single lead detail --------------------------------------------------------

def fetch_lead(abn: str) -> dict | None:
    sql = LEAD_LIST_SQL + " WHERE l.abn = ?"
    with connect(default_db_path()) as conn:
        row = conn.execute(sql, (abn,)).fetchone()
    return dict(row) if row else None
