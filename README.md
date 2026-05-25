# ABR Pipeline

Find Australian businesses **without a website**, locate their social media,
and generate personalized cold outreach messages.

Input: public ABR (Australian Business Register) XML dumps.
Output: a scored CSV of leads with ready-to-send messages.

---

## What it does (4 stages)

```
abr-data/*.xml
   │
   │  1.  src/parser.py        lxml streaming, applies STATES + KEYWORDS filters
   ▼
output/filtered_businesses.csv
   │
   │  2.  src/dns_check.py     async DNS-over-HTTPS, keeps rows whose domain candidates DO NOT resolve
   ▼
output/no_website.csv
   │
   │  3.  src/social.py        async Brave/Bing search, extracts Facebook + Instagram URLs
   ▼
output/has_social.csv
   │
   │  4.  src/messages.py      Claude API, 2 personalized outreach variants per lead
   ▼
output/outreach_ready.csv      ← final
```

---

## Step-by-step setup

### 1. Clone & enter the project

```powershell
cd abr-pipeline
```

### 2. Create a Python 3.11+ virtual environment

```powershell
# Windows PowerShell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```powershell
copy .env.example .env
# (macOS / Linux)
cp .env.example .env
```

Open `.env` and fill in real values:

| Variable             | Required? | Notes |
|----------------------|-----------|-------|
| `BRAVE_API_KEY`      | Recommended | Brave Search API — best price/quality for social lookup |
| `BING_API_KEY`       | Optional    | Bing Web Search v7 — fallback / secondary |
| `ANTHROPIC_API_KEY`  | Recommended | Without it, `messages.py` falls back to deterministic templates |
| `STATES`             | Yes (default `NSW,VIC,QLD`) | Comma-separated state codes |
| `KEYWORDS`           | Yes | Industry filter (matched against entity name) |
| `OUTPUT_DIR`         | Yes (default `./output`) | All CSVs land here |
| `LOG_DIR`            | Yes (default `./logs`)   | Logs + `errors.log` land here |

### 5. Drop ABR XML files into `abr-data/`

Download the public XML dump from the [ABR Bulk Extract](https://data.gov.au/dataset/abn-bulk-extract)
page and place one or more `.xml` files into `abr-data/`. The parser streams
each file with `lxml.iterparse`, so 600 MB+ files are fine — they will never be
loaded fully into memory.

### 6. Run the full pipeline

```bash
python src/pipeline.py
```

Want a small dry-run first? Set `PIPELINE_LIMIT=500` in `.env` (or pass `--limit 500`).

### 7. Inspect output

| File                                  | What's in it |
|---------------------------------------|--------------|
| `output/filtered_businesses.csv`      | All ACT-status entities matching `STATES` + `KEYWORDS` |
| `output/no_website.csv`               | Subset whose 5 candidate domains all failed DNS lookup |
| `output/has_social.csv`               | Subset of `no_website.csv` with at least one FB or IG URL |
| `output/outreach_ready.csv`           | Final: ABN, name, state, industry, socials, `message_v1`, `message_v2`, `score` |
| `logs/pipeline_YYYYMMDD.log`          | Per-run info log |
| `logs/errors.log`                     | All per-record errors (persistent across runs) |

---

## Dashboard (outreach tracking)

A Streamlit dashboard lives in `dashboard/`. It imports the pipeline CSVs into a
local SQLite (`dashboard/outreach.db`) and layers mutable outreach state on top:
status (`new → queued → sent → replied → booked → won / lost / skip`), contact
info, social URLs, notes, tags, assignee, full activity log.

### Setup (one-off)

```bash
pip install -r requirements-dashboard.txt
python -m dashboard.importer       # CSV → outreach.db (idempotent)
```

### Launch

```bash
streamlit run dashboard/app.py
# Windows: double-click start-dashboard.bat
```

Five tabs:
- **Overview** — KPI tiles (total / contactable / sent / replied / booked / won),
  funnel chart, 30-day activity timeline.
- **Leads** — filterable table with inline status/priority/contact edits,
  bulk actions (mark sent/replied/skip with optional channel + note), CSV export,
  per-lead detail panel (social URLs, notes, tags, assignee, history).
- **Analytics** — leads by state, top industries, entity-type donut, conversion
  table with reply / win rate progress bars per state.
- **Activity** — full audit trail (every status change, note, social update).
- **Settings** — re-run CSV import, refresh cache, reset outreach state.

UI is bilingual (LT / EN) — pick in the sidebar. The SQLite file is gitignored;
it's per-operator state, not a shared artifact.

---

## Running a single stage

Each module is independently CLI-runnable, useful for iterating without
re-parsing the whole XML.

```bash
python src/parser.py        # XML  -> filtered_businesses.csv
python src/dns_check.py     # ...  -> no_website.csv
python src/social.py        # ...  -> has_social.csv
python src/messages.py      # ...  -> outreach_ready.csv
```

---

## Troubleshooting

**`Missing required .env keys: ...`**
→ Copy `.env.example` to `.env` and fill in `STATES`, `KEYWORDS`, paths, etc.

**`No XML files found in abr-data/`**
→ Place at least one `.xml` from the ABR bulk extract into `abr-data/`.

**Pipeline runs but `no_website.csv` is empty**
→ Your `STATES` / `KEYWORDS` filters are too narrow, or every candidate has
already a domain. Widen the filters in `.env` or check `logs/pipeline_*.log`
for parse counts per stage.

**`errors.log` growing fast during DNS / social stage**
→ Lower `DNS_CONCURRENCY` (try 50) or raise `SOCIAL_DELAY_MS` (try 800).
A few per-record errors are expected; the pipeline never crashes on them.

**Outreach messages look templated**
→ `ANTHROPIC_API_KEY` is not set, so `messages.py` is using the deterministic
fallback. Add the key to `.env` to switch to Claude-generated copy.

---

## Project layout

```
abr-pipeline/
├── CLAUDE.md            project rules
├── .env.example         config template
├── .env                 your real config  (gitignored)
├── .gitignore
├── requirements.txt
├── README.md
├── abr-data/            input XML  (gitignored)
├── output/              CSVs       (gitignored)
├── logs/                log files  (gitignored)
└── src/
    ├── __init__.py
    ├── parser.py        XML  -> filtered_businesses.csv
    ├── dns_check.py     async DNS-over-HTTPS
    ├── social.py        async FB/IG search
    ├── messages.py      Claude outreach generator
    ├── utils.py         config, logging, normalize, fuzzy, checkpoints
    └── pipeline.py      orchestrator
```

---

## Ethics & compliance

- ABR data is **public**. Outreach must still comply with the
  Australian **Spam Act 2003**: include an unsubscribe mechanism and identify
  the sender in every message you send.
- DNS lookups are passive — no port scanning, no probing.
- Social search uses **public search APIs only** — no login-protected scraping.
- Never resell or redistribute raw ABR data; this pipeline is for first-party
  outreach only.
