"""Dashboard translations (LT / EN).

Keep the key tree flat-ish; nested namespaces only where it pays off
(`status.sent`, `metric.total`). Missing keys fall back to the key string
itself, so a half-translated UI is still navigable.
"""
from __future__ import annotations

import streamlit as st

LANGUAGES = {"lt": "Lietuvių", "en": "English"}
DEFAULT_LANG = "lt"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "lt": {
        # --- App shell ---
        "app.title": "ABR Outreach komandos centras",
        "app.caption": "Australijos verslai be svetainės · 97k+ lead · "
                       "outreach tracking",
        "lang.label": "Kalba",

        # --- Tabs ---
        "tab.overview":  "Apžvalga",
        "tab.leads":     "Leads",
        "tab.analytics": "Analitika",
        "tab.activity":  "Veiksmų istorija",
        "tab.settings":  "Nustatymai",

        # --- Sidebar / filters ---
        "sidebar.filters":  "Filtrai",
        "sidebar.import":   "Importas",
        "sidebar.actions":  "Veiksmai",
        "filter.state":     "Valstija",
        "filter.industry":  "Industrija",
        "filter.entity":    "Subjekto tipas",
        "filter.gst":       "GST statusas",
        "filter.status":    "Outreach statusas",
        "filter.search":    "Paieška (pavadinimas / ABN / postcode)",
        "filter.has_social":"Tik su social",
        "filter.has_phone": "Tik su telefonu",
        "filter.has_email": "Tik su email",
        "filter.priority":  "Prioritetas (≤)",
        "filter.reset":     "Išvalyti filtrus",

        # --- KPIs ---
        "kpi.total":         "Iš viso leadų",
        "kpi.filtered":      "Po filtrų",
        "kpi.contactable":   "Kontaktuotini (turi social)",
        "kpi.queued":        "Eilėje",
        "kpi.sent":          "Išsiųsta",
        "kpi.replied":       "Atsakė",
        "kpi.booked":        "Susitarta",
        "kpi.won":           "Laimėta",
        "kpi.lost":          "Pralaimėta",
        "kpi.skip":          "Praleista",
        "kpi.reply_rate":    "Atsakymo dažnis",
        "kpi.book_rate":     "Susitikimo dažnis",
        "kpi.win_rate":      "Laimėjimo dažnis",
        "kpi.this_week":     "Šią savaitę",
        "kpi.today":         "Šiandien",

        # --- Charts ---
        "chart.funnel":         "Outreach funnel",
        "chart.by_state":       "Lead skaičius pagal valstiją",
        "chart.by_industry":    "Top industrijos",
        "chart.by_entity":      "Pagal subjekto tipą",
        "chart.timeline":       "Veiksmų laiko juosta (30 d.)",
        "chart.conversion":     "Konversijos pagal valstiją",
        "chart.activity_heat":  "Aktyvumas savaitės dienomis",

        # --- Statuses ---
        "status.new":     "Naujas",
        "status.queued":  "Eilėje",
        "status.sent":    "Išsiųsta",
        "status.replied": "Atsakė",
        "status.booked":  "Susitarta",
        "status.won":     "Laimėta",
        "status.lost":    "Pralaimėta",
        "status.skip":    "Praleista",

        # --- Channels ---
        "channel.fb":    "Facebook",
        "channel.ig":    "Instagram",
        "channel.email": "El. paštas",
        "channel.dm":    "DM",
        "channel.phone": "Telefonas",

        # --- Leads table actions ---
        "leads.title":         "Leadai",
        "leads.count":         "{n} iš {total}",
        "leads.bulk.title":    "Masiniai veiksmai pažymėtoms eilutėms",
        "leads.bulk.select":   "Pažymėk leadus lentelėje (kairysis stulpelis), "
                               "tada veiksmas:",
        "leads.bulk.action":   "Veiksmas",
        "leads.bulk.status":   "Pažymėti statusą",
        "leads.bulk.channel":  "Kanalas",
        "leads.bulk.note":     "Pastaba (neprivaloma)",
        "leads.bulk.apply":    "Pritaikyti pažymėtoms",
        "leads.bulk.export":   "Eksportuoti pažymėtas (CSV)",
        "leads.bulk.none":     "Nepasirinkta nė viena eilutė.",
        "leads.bulk.done":     "Atnaujinta {n} įrašų.",

        # --- Detail panel ---
        "detail.title":        "Lead detalės",
        "detail.pick":         "Atidaryk įrašą paspaudęs ABN lentelėje arba "
                               "pasirink iš sąrašo žemiau.",
        "detail.contact":      "Kontaktai",
        "detail.social":       "Socialiniai tinklai",
        "detail.outreach":     "Outreach būsena",
        "detail.notes":        "Pastabos",
        "detail.tags":         "Žymos (kableliais)",
        "detail.assigned":     "Priskirta",
        "detail.history":      "Veiksmų istorija",
        "detail.save":         "Išsaugoti",
        "detail.save.ok":      "Išsaugota.",
        "detail.fb_url":       "Facebook URL",
        "detail.ig_url":       "Instagram URL",
        "detail.email":        "El. paštas",
        "detail.phone":        "Telefonas",
        "detail.contact_name": "Kontaktinis asmuo",
        "detail.priority":     "Prioritetas",
        "detail.lost_reason":  "Atmetimo priežastis",

        # --- Activity ---
        "activity.empty":   "Nėra veiksmų istorijos pasirinktam filtrui.",
        "activity.col.when":   "Kada",
        "activity.col.abn":    "ABN",
        "activity.col.name":   "Verslas",
        "activity.col.action": "Veiksmas",
        "activity.col.detail": "Detalė",

        # --- Settings / import ---
        "settings.title":      "Nustatymai",
        "settings.db":         "Duomenų bazės kelias",
        "settings.import.run": "Paleisti importą iš CSV",
        "settings.import.help":"Įkrauna visus output/*.csv į SQLite. "
                               "Idempotentu — esami outreach įrašai išlieka.",
        "settings.import.ok":  "Importas baigtas: {ins} naujų / {upd} atnaujintų.",
        "settings.refresh":    "Atnaujinti cache",
        "settings.danger":     "Pavojinga zona",
        "settings.reset.outreach": "Išvalyti outreach state (palieka leads)",
        "settings.confirm":    "Patvirtink — bus prarasta visa istorija",

        # --- Misc ---
        "empty.no_leads":   "Nė vieno leado. Paleisk importą Nustatymuose.",
        "empty.no_match":   "Nė vieno leado pagal pasirinktus filtrus.",
        "common.yes":       "Taip",
        "common.no":        "Ne",
        "common.unknown":   "—",
        "common.last_import": "Paskutinis importas",
    },

    "en": {
        "app.title": "ABR Outreach command center",
        "app.caption": "Australian businesses without a website · 97k+ leads · "
                       "outreach tracking",
        "lang.label": "Language",

        "tab.overview":  "Overview",
        "tab.leads":     "Leads",
        "tab.analytics": "Analytics",
        "tab.activity":  "Activity",
        "tab.settings":  "Settings",

        "sidebar.filters": "Filters",
        "sidebar.import":  "Import",
        "sidebar.actions": "Actions",
        "filter.state":    "State",
        "filter.industry": "Industry",
        "filter.entity":   "Entity type",
        "filter.gst":      "GST status",
        "filter.status":   "Outreach status",
        "filter.search":   "Search (name / ABN / postcode)",
        "filter.has_social":"Has social",
        "filter.has_phone": "Has phone",
        "filter.has_email": "Has email",
        "filter.priority":  "Priority (≤)",
        "filter.reset":     "Reset filters",

        "kpi.total":         "Total leads",
        "kpi.filtered":      "After filters",
        "kpi.contactable":   "Contactable (has social)",
        "kpi.queued":        "Queued",
        "kpi.sent":          "Sent",
        "kpi.replied":       "Replied",
        "kpi.booked":        "Booked",
        "kpi.won":           "Won",
        "kpi.lost":          "Lost",
        "kpi.skip":          "Skipped",
        "kpi.reply_rate":    "Reply rate",
        "kpi.book_rate":     "Booking rate",
        "kpi.win_rate":      "Win rate",
        "kpi.this_week":     "This week",
        "kpi.today":         "Today",

        "chart.funnel":         "Outreach funnel",
        "chart.by_state":       "Leads by state",
        "chart.by_industry":    "Top industries",
        "chart.by_entity":      "By entity type",
        "chart.timeline":       "Activity timeline (30d)",
        "chart.conversion":     "Conversion by state",
        "chart.activity_heat":  "Activity by weekday",

        "status.new":     "New",
        "status.queued":  "Queued",
        "status.sent":    "Sent",
        "status.replied": "Replied",
        "status.booked":  "Booked",
        "status.won":     "Won",
        "status.lost":    "Lost",
        "status.skip":    "Skipped",

        "channel.fb":    "Facebook",
        "channel.ig":    "Instagram",
        "channel.email": "Email",
        "channel.dm":    "DM",
        "channel.phone": "Phone",

        "leads.title":      "Leads",
        "leads.count":      "{n} of {total}",
        "leads.bulk.title": "Bulk actions for selected rows",
        "leads.bulk.select":"Select leads in the table (left column), then act:",
        "leads.bulk.action":"Action",
        "leads.bulk.status":"Set status",
        "leads.bulk.channel":"Channel",
        "leads.bulk.note":   "Note (optional)",
        "leads.bulk.apply":  "Apply to selected",
        "leads.bulk.export": "Export selected (CSV)",
        "leads.bulk.none":   "No rows selected.",
        "leads.bulk.done":   "Updated {n} records.",

        "detail.title":        "Lead details",
        "detail.pick":         "Open a record by clicking ABN in the table, "
                               "or pick from the list below.",
        "detail.contact":      "Contact",
        "detail.social":       "Social",
        "detail.outreach":     "Outreach state",
        "detail.notes":        "Notes",
        "detail.tags":         "Tags (comma-separated)",
        "detail.assigned":     "Assigned to",
        "detail.history":      "Activity history",
        "detail.save":         "Save",
        "detail.save.ok":      "Saved.",
        "detail.fb_url":       "Facebook URL",
        "detail.ig_url":       "Instagram URL",
        "detail.email":        "Email",
        "detail.phone":        "Phone",
        "detail.contact_name": "Contact name",
        "detail.priority":     "Priority",
        "detail.lost_reason":  "Lost reason",

        "activity.empty":   "No activity for the chosen filter.",
        "activity.col.when":   "When",
        "activity.col.abn":    "ABN",
        "activity.col.name":   "Business",
        "activity.col.action": "Action",
        "activity.col.detail": "Detail",

        "settings.title":      "Settings",
        "settings.db":         "Database path",
        "settings.import.run": "Run CSV import",
        "settings.import.help":"Loads all output/*.csv into SQLite. "
                               "Idempotent — existing outreach rows are kept.",
        "settings.import.ok":  "Import done: {ins} new / {upd} updated.",
        "settings.refresh":    "Refresh cache",
        "settings.danger":     "Danger zone",
        "settings.reset.outreach": "Reset outreach state (keep leads)",
        "settings.confirm":    "Confirm — full history will be lost",

        "empty.no_leads":   "No leads yet. Run import in Settings.",
        "empty.no_match":   "No leads match the current filters.",
        "common.yes":       "Yes",
        "common.no":        "No",
        "common.unknown":   "—",
        "common.last_import": "Last import",
    },
}


def get_lang() -> str:
    return st.session_state.get("lang", DEFAULT_LANG)


def set_lang(lang: str) -> None:
    if lang in LANGUAGES:
        st.session_state["lang"] = lang


def t(key: str, **kwargs: object) -> str:
    """Translate. Falls back to the key itself if missing."""
    lang = get_lang()
    txt = TRANSLATIONS.get(lang, {}).get(key) \
          or TRANSLATIONS[DEFAULT_LANG].get(key) \
          or key
    if kwargs:
        try:
            return txt.format(**kwargs)
        except (KeyError, IndexError):
            return txt
    return txt
