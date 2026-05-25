"""ABR Outreach command center — Streamlit dashboard.

Five tabs:
  1. Overview   — KPI tiles + funnel + activity sparkline
  2. Leads      — filtered table + per-lead detail editor + bulk actions
  3. Analytics  — geo / industry / entity / conversion / weekday heatmap
  4. Activity   — append-only audit trail
  5. Settings   — CSV import, cache refresh, danger zone

Run:
    streamlit run dashboard/app.py
or double-click start-dashboard.bat in the project root.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# Allow `python -m streamlit run dashboard/app.py` from any CWD
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.db import (                                    # noqa: E402
    VALID_STATUSES,
    connect,
    default_db_path,
    init_schema,
    set_status,
    update_lead_socials,
    update_outreach_fields,
)
from dashboard.i18n import (                                  # noqa: E402
    LANGUAGES,
    get_lang,
    set_lang,
    t,
)
from dashboard.importer import (                              # noqa: E402
    CANDIDATE_CSVS,
    import_socials_if_present,
    upsert_leads,
)
from dashboard.queries import (                               # noqa: E402
    activity_log,
    activity_timeline,
    by_entity_type,
    by_industry,
    by_state,
    db_fingerprint,
    distinct_values,
    fetch_lead,
    fetch_leads,
    funnel_df,
    kpi_counts,
    last_import,
)


CHANNELS = ("fb", "ig", "email", "dm", "phone")
DETAIL_KEY = "selected_abn"


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def ensure_db() -> None:
    """Schema must exist before any query — first launch creates an empty DB."""
    db = default_db_path()
    with connect(db) as conn:
        init_schema(conn)


def bust_cache() -> None:
    st.cache_data.clear()


# ---------------------------------------------------------------------------
# Sidebar — language + filters + import
# ---------------------------------------------------------------------------

def render_sidebar(fp: float) -> dict:
    with st.sidebar:
        lang_label = t("lang.label")
        current = get_lang()
        picked = st.radio(
            lang_label,
            options=list(LANGUAGES.keys()),
            format_func=lambda c: LANGUAGES[c],
            index=list(LANGUAGES.keys()).index(current),
            horizontal=True,
            key="lang_picker",
        )
        if picked != current:
            set_lang(picked)
            st.rerun()

        st.markdown("---")
        st.header(t("sidebar.filters"))

        states     = distinct_values(fp, "state")
        industries = distinct_values(fp, "industry_keyword")
        entities   = distinct_values(fp, "entity_type")
        gsts       = distinct_values(fp, "gst_status")

        sel_states = st.multiselect(t("filter.state"),    states)
        sel_inds   = st.multiselect(t("filter.industry"), industries)
        sel_ents   = st.multiselect(t("filter.entity"),   entities)
        sel_gst    = st.multiselect(t("filter.gst"),      gsts)
        sel_status = st.multiselect(
            t("filter.status"),
            options=list(VALID_STATUSES),
            format_func=lambda s: t(f"status.{s}"),
        )

        search = st.text_input(t("filter.search"), value="").strip()

        col_a, col_b = st.columns(2)
        with col_a:
            has_social = st.checkbox(t("filter.has_social"), value=False)
            has_email  = st.checkbox(t("filter.has_email"),  value=False)
        with col_b:
            has_phone   = st.checkbox(t("filter.has_phone"), value=False)
            max_prio    = st.slider(t("filter.priority"), 1, 5, 5)

        if st.button(t("filter.reset"), use_container_width=True):
            for k in (
                "lang_picker",  # keep language
            ):
                pass
            for key in list(st.session_state.keys()):
                if key not in {"lang", "lang_picker"}:
                    del st.session_state[key]
            st.rerun()

        st.markdown("---")
        st.header(t("sidebar.import"))
        li = last_import(fp)
        if li:
            st.caption(f"{t('common.last_import')}: "
                       f"`{li['source_file']}` · "
                       f"{li['finished_at'] or '—'}")
        if st.button(t("settings.import.run"), use_container_width=True,
                     key="sidebar_import"):
            _run_import()

    return dict(
        states=tuple(sel_states),
        industries=tuple(sel_inds),
        entity_types=tuple(sel_ents),
        gst_statuses=tuple(sel_gst),
        outreach_statuses=tuple(sel_status),
        search=search,
        has_social=has_social,
        has_phone=has_phone,
        has_email=has_email,
        max_priority=max_prio,
    )


# ---------------------------------------------------------------------------
# Import action
# ---------------------------------------------------------------------------

def _run_import() -> None:
    csvs = [p for p in CANDIDATE_CSVS if p.exists()]
    if not csvs:
        st.error(
            "No CSV found in ./output. Run the ABR pipeline first "
            "(`python abr_parser.py` → `python check_dns.py`)."
        )
        return
    ins_total = upd_total = 0
    with st.spinner("Importing CSV → SQLite ..."), connect(default_db_path()) as conn:
        init_schema(conn)
        for csv_path in csvs:
            _, ins, upd = upsert_leads(conn, csv_path)
            ins_total += ins
            upd_total += upd
        import_socials_if_present(conn)
    bust_cache()
    st.success(t("settings.import.ok", ins=ins_total, upd=upd_total))


# ---------------------------------------------------------------------------
# Tab: Overview
# ---------------------------------------------------------------------------

def tab_overview(fp: float) -> None:
    k = kpi_counts(fp)
    if not k or not k.get("total"):
        st.info(t("empty.no_leads"))
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(t("kpi.total"),       f"{k['total']:,}")
    c2.metric(t("kpi.contactable"), f"{k['contactable']:,}",
              delta=f"{k['contactable'] / max(k['total'],1) * 100:.1f}%")
    c3.metric(t("kpi.sent"),     f"{k['sent']:,}",
              delta=f"+{k['sent_today']} {t('kpi.today').lower()}")
    c4.metric(t("kpi.replied"),  f"{k['replied']:,}",
              delta=_rate(k['replied'], k['sent']))
    c5.metric(t("kpi.booked"),   f"{k['booked']:,}",
              delta=_rate(k['booked'], k['replied']))

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric(t("kpi.queued"),  f"{k['queued']:,}")
    c7.metric(t("kpi.won"),     f"{k['won']:,}",
              delta=_rate(k['won'], k['booked']))
    c8.metric(t("kpi.lost"),    f"{k['lost']:,}")
    c9.metric(t("kpi.skip"),    f"{k['skip']:,}")
    c10.metric(t("kpi.this_week") + " — " + t("kpi.sent").lower(),
               f"{k['sent_week']:,}")

    st.markdown("---")

    left, right = st.columns([1, 1])
    with left:
        st.subheader(t("chart.funnel"))
        fdf = funnel_df(fp).copy()
        fdf["label"] = fdf["status"].map(lambda s: t(f"status.{s}"))
        chart = (
            alt.Chart(fdf)
            .mark_bar()
            .encode(
                x=alt.X("n:Q", title=None),
                y=alt.Y("label:N", sort=fdf["label"].tolist(), title=None),
                tooltip=["label", "n"],
                color=alt.Color("status:N", legend=None,
                                scale=alt.Scale(scheme="blues")),
            )
            .properties(height=260)
        )
        st.altair_chart(chart, use_container_width=True)

    with right:
        st.subheader(t("chart.timeline"))
        tdf = activity_timeline(fp, days=30)
        if tdf.empty:
            st.caption(t("activity.empty"))
        else:
            long = tdf.melt("day", var_name="action", value_name="n")
            chart = (
                alt.Chart(long)
                .mark_line(point=True)
                .encode(
                    x=alt.X("day:T", title=None),
                    y=alt.Y("n:Q", title=None),
                    color=alt.Color("action:N", title=None),
                    tooltip=["day", "action", "n"],
                )
                .properties(height=260)
            )
            st.altair_chart(chart, use_container_width=True)


def _rate(num: int, den: int) -> str:
    if not den:
        return "—"
    return f"{num / den * 100:.1f}%"


# ---------------------------------------------------------------------------
# Tab: Leads
# ---------------------------------------------------------------------------

LEAD_COLUMNS_VISIBLE = [
    "abn", "business_name", "state", "postcode",
    "industry_keyword", "entity_type",
    "status", "priority",
    "facebook_url", "instagram_url",
    "contact_phone", "contact_email",
    "sent_at", "replied_at", "tags", "assigned_to",
]


def tab_leads(fp: float, filters: dict) -> None:
    df = fetch_leads(fp, **filters)
    if df.empty:
        st.info(t("empty.no_match"))
        return

    total = kpi_counts(fp).get("total", 0)
    st.markdown(f"### {t('leads.title')} · "
                f"{t('leads.count', n=len(df), total=total)}")

    # Coerce missing outreach state for display only
    disp = df.copy()
    disp["status"]   = disp["status"].fillna("new")
    disp["priority"] = disp["priority"].fillna(3).astype(int)
    disp["select"]   = False
    cols = ["select", *LEAD_COLUMNS_VISIBLE]

    edited = st.data_editor(
        disp[cols],
        hide_index=True,
        use_container_width=True,
        height=480,
        column_config={
            "select": st.column_config.CheckboxColumn(" ", width="small"),
            "abn": st.column_config.TextColumn("ABN", width="small",
                                               disabled=True),
            "business_name": st.column_config.TextColumn(
                "Business", disabled=True),
            "state":     st.column_config.TextColumn(width="small",
                                                     disabled=True),
            "postcode":  st.column_config.TextColumn(width="small",
                                                     disabled=True),
            "industry_keyword": st.column_config.TextColumn(
                "Industry", disabled=True),
            "entity_type": st.column_config.TextColumn("Type", width="small",
                                                       disabled=True),
            "status": st.column_config.SelectboxColumn(
                t("filter.status"),
                options=list(VALID_STATUSES),
                required=True,
            ),
            "priority": st.column_config.NumberColumn(
                t("detail.priority"), min_value=1, max_value=5, step=1,
            ),
            "facebook_url": st.column_config.LinkColumn(t("detail.fb_url")),
            "instagram_url": st.column_config.LinkColumn(t("detail.ig_url")),
            "contact_phone": st.column_config.TextColumn(t("detail.phone"),
                                                         width="small"),
            "contact_email": st.column_config.TextColumn(t("detail.email")),
            "sent_at":    st.column_config.DatetimeColumn(t("kpi.sent"),
                                                          disabled=True),
            "replied_at": st.column_config.DatetimeColumn(t("kpi.replied"),
                                                          disabled=True),
            "tags":       st.column_config.TextColumn(t("detail.tags")),
            "assigned_to":st.column_config.TextColumn(t("detail.assigned"),
                                                      width="small"),
        },
        key="lead_editor",
    )

    # --- Persist inline edits (status / priority / contacts / tags) ----------
    _persist_inline_edits(disp, edited)

    # --- Bulk actions row ----------------------------------------------------
    st.markdown("---")
    st.markdown(f"**{t('leads.bulk.title')}**")
    st.caption(t("leads.bulk.select"))

    selected_abns = edited.loc[edited["select"], "abn"].tolist()

    b1, b2, b3, b4 = st.columns([1, 1, 2, 1])
    with b1:
        bulk_status = st.selectbox(
            t("leads.bulk.status"),
            options=list(VALID_STATUSES),
            index=2,
            format_func=lambda s: t(f"status.{s}"),
        )
    with b2:
        bulk_channel = st.selectbox(
            t("leads.bulk.channel"),
            options=("",) + CHANNELS,
            format_func=lambda c: "—" if not c else t(f"channel.{c}"),
        )
    with b3:
        bulk_note = st.text_input(t("leads.bulk.note"), value="")
    with b4:
        st.write("")
        st.write("")
        do_apply = st.button(t("leads.bulk.apply"), type="primary",
                             use_container_width=True)

    c_export, _ = st.columns([1, 4])
    with c_export:
        if selected_abns:
            csv_bytes = edited.loc[edited["select"]].drop(columns=["select"]) \
                              .to_csv(index=False).encode("utf-8-sig")
        else:
            csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            t("leads.bulk.export") if selected_abns
                else "📥 " + t("leads.title") + " → CSV",
            data=csv_bytes,
            file_name="abr_outreach_export.csv",
            mime="text/csv",
            use_container_width=True,
        )

    if do_apply:
        if not selected_abns:
            st.warning(t("leads.bulk.none"))
        else:
            with connect(default_db_path()) as conn:
                n = set_status(
                    conn, selected_abns, bulk_status,
                    channel=bulk_channel or None,
                    note=bulk_note or None,
                    actor="dashboard",
                )
            bust_cache()
            st.success(t("leads.bulk.done", n=n))
            st.rerun()

    # --- Detail panel --------------------------------------------------------
    st.markdown("---")
    _render_detail_panel(fp, df)


def _persist_inline_edits(orig: pd.DataFrame, edited: pd.DataFrame) -> None:
    """Diff editor vs original; write per-row updates + activity entries."""
    editable_cols = [
        "status", "priority", "contact_phone", "contact_email",
        "tags", "assigned_to",
    ]
    changes: list[tuple[str, dict, str | None]] = []
    for _, e in edited.iterrows():
        o = orig[orig["abn"] == e["abn"]]
        if o.empty:
            continue
        o = o.iloc[0]
        delta: dict = {}
        new_status: str | None = None
        for col in editable_cols:
            new = e[col]
            old = o[col]
            # Treat NaN / None equivalently
            same = (pd.isna(new) and pd.isna(old)) or (new == old)
            if same:
                continue
            if col == "status":
                new_status = str(new)
            else:
                delta[col] = None if (pd.isna(new) or new == "") else new
        if delta or new_status:
            changes.append((e["abn"], delta, new_status))

    if not changes:
        return

    with connect(default_db_path()) as conn:
        for abn, delta, new_status in changes:
            if delta:
                update_outreach_fields(conn, abn, delta, actor="dashboard")
            if new_status:
                set_status(conn, [abn], new_status, actor="dashboard")
    bust_cache()


def _render_detail_panel(fp: float, df: pd.DataFrame) -> None:
    abns = df["abn"].tolist()
    if not abns:
        return

    st.markdown(f"### {t('detail.title')}")
    sel = st.selectbox(
        "ABN",
        options=[""] + abns,
        format_func=lambda a: a if not a else
            f"{a} · {df.loc[df['abn']==a, 'business_name'].iloc[0]}",
        key=DETAIL_KEY,
    )
    if not sel:
        st.caption(t("detail.pick"))
        return

    lead = fetch_lead(sel)
    if not lead:
        return

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"#### {lead['business_name']}")
        st.write(f"**ABN:** `{lead['abn']}`")
        st.write(f"**{t('filter.state')}:** {lead.get('state') or '—'} · "
                 f"{lead.get('postcode') or ''}")
        st.write(f"**{t('filter.industry')}:** "
                 f"{lead.get('industry_keyword') or '—'}")
        st.write(f"**{t('filter.entity')} / {t('filter.gst')}:** "
                 f"{lead.get('entity_type') or '—'} · "
                 f"{lead.get('gst_status') or '—'}")
        if lead.get("found_domain"):
            st.write(f"**Domain:** {lead['found_domain']}")

    with col2:
        with st.form(f"detail_form_{sel}", clear_on_submit=False):
            st.markdown(f"**{t('detail.social')}**")
            fb = st.text_input(t("detail.fb_url"),
                               value=lead.get("facebook_url") or "")
            ig = st.text_input(t("detail.ig_url"),
                               value=lead.get("instagram_url") or "")

            st.markdown(f"**{t('detail.contact')}**")
            name  = st.text_input(t("detail.contact_name"),
                                  value=lead.get("contact_name") or "")
            email = st.text_input(t("detail.email"),
                                  value=lead.get("contact_email") or "")
            phone = st.text_input(t("detail.phone"),
                                  value=lead.get("contact_phone") or "")

            st.markdown(f"**{t('detail.outreach')}**")
            status_idx = list(VALID_STATUSES).index(lead.get("status") or "new")
            new_status = st.selectbox(
                t("filter.status"),
                options=list(VALID_STATUSES),
                index=status_idx,
                format_func=lambda s: t(f"status.{s}"),
            )
            priority = st.slider(t("detail.priority"), 1, 5,
                                  int(lead.get("priority") or 3))
            tags = st.text_input(t("detail.tags"), value=lead.get("tags") or "")
            assigned = st.text_input(t("detail.assigned"),
                                      value=lead.get("assigned_to") or "")
            notes = st.text_area(t("detail.notes"),
                                  value=lead.get("notes") or "", height=120)
            lost_reason = ""
            if new_status == "lost":
                lost_reason = st.text_input(t("detail.lost_reason"),
                                             value=lead.get("lost_reason") or "")

            saved = st.form_submit_button(t("detail.save"), type="primary",
                                          use_container_width=True)
            if saved:
                with connect(default_db_path()) as conn:
                    update_lead_socials(conn, sel, fb.strip() or None,
                                        ig.strip() or None, actor="dashboard")
                    update_outreach_fields(conn, sel, {
                        "contact_name":  name.strip() or None,
                        "contact_email": email.strip() or None,
                        "contact_phone": phone.strip() or None,
                        "priority":      priority,
                        "tags":          tags.strip() or None,
                        "assigned_to":   assigned.strip() or None,
                        "notes":         notes.strip() or None,
                    }, actor="dashboard")
                    if (lead.get("status") or "new") != new_status:
                        set_status(conn, [sel], new_status,
                                   note=lost_reason or None, actor="dashboard")
                bust_cache()
                st.success(t("detail.save.ok"))
                st.rerun()

    st.markdown(f"**{t('detail.history')}**")
    log = activity_log(fp, abn=sel, limit=50)
    if log.empty:
        st.caption(t("activity.empty"))
    else:
        st.dataframe(
            log.rename(columns={
                "happened_at": t("activity.col.when"),
                "action":      t("activity.col.action"),
                "detail":      t("activity.col.detail"),
                "actor":       t("detail.assigned"),
            })[[t("activity.col.when"), t("activity.col.action"),
                t("activity.col.detail"), t("detail.assigned")]],
            hide_index=True,
            use_container_width=True,
            height=240,
        )


# ---------------------------------------------------------------------------
# Tab: Analytics
# ---------------------------------------------------------------------------

def tab_analytics(fp: float) -> None:
    if not kpi_counts(fp).get("total"):
        st.info(t("empty.no_leads"))
        return

    # State row
    s = by_state(fp)
    e = by_entity_type(fp)

    c1, c2 = st.columns([2, 1])
    with c1:
        st.subheader(t("chart.by_state"))
        st.altair_chart(
            alt.Chart(s).mark_bar().encode(
                x=alt.X("state:N", sort="-y", title=None),
                y=alt.Y("leads:Q", title=None),
                tooltip=["state", "leads", "sent", "replied", "won"],
                color=alt.Color("leads:Q", legend=None,
                                scale=alt.Scale(scheme="blues")),
            ).properties(height=300),
            use_container_width=True,
        )
    with c2:
        st.subheader(t("chart.by_entity"))
        st.altair_chart(
            alt.Chart(e).mark_arc(innerRadius=50).encode(
                theta="n:Q",
                color=alt.Color("entity_type:N", title=None),
                tooltip=["entity_type", "n"],
            ).properties(height=300),
            use_container_width=True,
        )

    # Industry row
    st.subheader(t("chart.by_industry"))
    i = by_industry(fp, top=15)
    i_long = i.melt("industry", var_name="bucket", value_name="n")
    st.altair_chart(
        alt.Chart(i_long).mark_bar().encode(
            x=alt.X("n:Q", title=None, stack=None),
            y=alt.Y("industry:N", sort="-x", title=None),
            color=alt.Color("bucket:N", title=None,
                            scale=alt.Scale(scheme="tableau10")),
            tooltip=["industry", "bucket", "n"],
        ).properties(height=420),
        use_container_width=True,
    )

    # Conversion by state
    st.subheader(t("chart.conversion"))
    s2 = s.copy()
    s2["reply_rate"] = (s2["replied"] / s2["sent"].replace(0, pd.NA)).fillna(0)
    s2["win_rate"]   = (s2["won"]     / s2["sent"].replace(0, pd.NA)).fillna(0)
    st.dataframe(
        s2.rename(columns={
            "state":      t("filter.state"),
            "leads":      t("kpi.total"),
            "sent":       t("kpi.sent"),
            "replied":    t("kpi.replied"),
            "won":        t("kpi.won"),
            "reply_rate": t("kpi.reply_rate"),
            "win_rate":   t("kpi.win_rate"),
        }),
        hide_index=True,
        use_container_width=True,
        column_config={
            t("kpi.reply_rate"): st.column_config.ProgressColumn(
                t("kpi.reply_rate"), min_value=0.0, max_value=1.0,
                format="%.1f%%",
            ),
            t("kpi.win_rate"): st.column_config.ProgressColumn(
                t("kpi.win_rate"), min_value=0.0, max_value=1.0,
                format="%.1f%%",
            ),
        },
    )


# ---------------------------------------------------------------------------
# Tab: Activity
# ---------------------------------------------------------------------------

def tab_activity(fp: float) -> None:
    log = activity_log(fp, limit=500)
    if log.empty:
        st.info(t("activity.empty"))
        return
    log = log.rename(columns={
        "happened_at":   t("activity.col.when"),
        "abn":           t("activity.col.abn"),
        "business_name": t("activity.col.name"),
        "action":        t("activity.col.action"),
        "detail":        t("activity.col.detail"),
    })
    st.dataframe(
        log[[t("activity.col.when"), t("activity.col.abn"),
             t("activity.col.name"), t("activity.col.action"),
             t("activity.col.detail")]],
        hide_index=True,
        use_container_width=True,
        height=620,
    )

    buf = io.BytesIO(log.to_csv(index=False).encode("utf-8-sig"))
    st.download_button("📥 activity.csv", buf, "activity.csv", "text/csv")


# ---------------------------------------------------------------------------
# Tab: Settings
# ---------------------------------------------------------------------------

def tab_settings(fp: float) -> None:
    st.subheader(t("settings.title"))
    st.write(f"**{t('settings.db')}:** `{default_db_path()}`")
    st.caption(t("settings.import.help"))

    cols = st.columns(3)
    with cols[0]:
        if st.button(t("settings.import.run"), type="primary",
                     use_container_width=True):
            _run_import()
    with cols[1]:
        if st.button(t("settings.refresh"), use_container_width=True):
            bust_cache()
            st.rerun()

    li = last_import(fp)
    if li:
        st.markdown("---")
        st.write(f"**{t('common.last_import')}:** "
                 f"`{li['source_file']}` — "
                 f"{li['rows_total']} rows "
                 f"({li['rows_inserted']} new / {li['rows_updated']} updated) — "
                 f"{li['finished_at']}")

    st.markdown("---")
    st.markdown(f"### ⚠️ {t('settings.danger')}")
    confirm = st.checkbox(t("settings.confirm"))
    if st.button(t("settings.reset.outreach"), disabled=not confirm):
        with connect(default_db_path()) as conn:
            conn.execute("DELETE FROM outreach")
            conn.execute("DELETE FROM activity WHERE action != 'import'")
        bust_cache()
        st.success("OK.")
        st.rerun()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="ABR Outreach",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    ensure_db()
    fp = db_fingerprint()

    st.title("🎯 " + t("app.title"))
    st.caption(t("app.caption"))

    filters = render_sidebar(fp)

    tabs = st.tabs([
        t("tab.overview"),
        t("tab.leads"),
        t("tab.analytics"),
        t("tab.activity"),
        t("tab.settings"),
    ])
    with tabs[0]:
        tab_overview(fp)
    with tabs[1]:
        tab_leads(fp, filters)
    with tabs[2]:
        tab_analytics(fp)
    with tabs[3]:
        tab_activity(fp)
    with tabs[4]:
        tab_settings(fp)


if __name__ == "__main__":
    main()
