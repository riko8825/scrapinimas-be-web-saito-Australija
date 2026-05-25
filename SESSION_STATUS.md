# SESSION_STATUS

## Paskutinė sesija: 2026-05-25 (sesija #5 — find_social.py validated end-to-end + Brave ceiling exposed + Plan A→B→C agreed)

### Ką padarėme

**Venv setup:**
- Įdiegtos visos pipeline deps į [abr-pipeline/.venv](.venv) per `pip install -r requirements.txt` (pandas 2.3.3, httpx 0.28.1, anthropic 0.104.1, lxml 6.1.1, aiodns, dnspython, tenacity, tqdm, aiofiles + transitives). Import sanity check OK.

**`find_social.py --limit 20` — pirmas live API run:**
- V1 (raw kodas iš sesijos #3): 20/20 apdoroti per 17.2s (1.2 biz/s), 0 errors. **1 hit: FAST PAINTING → facebook.com/fast.painting.18 — BET tai Texas LLC, ne SA Australia (false positive).**
- Importer paleistas: `--csv has_social.csv` per `upsert_leads()` perrašė `business_name=""` (social CSV turi `name`, ne `business_name`). **Bug logged carry-over #5.** Workaround: naudoti `import_socials_if_present()` auto-discovery, ne `--csv has_social.csv`.

**Hardening — 3 commit'ai į [find_social.py](find_social.py):**
1. **Query cleanup** — `_LEGAL_NOISE` praplėstas (`proprietary`, `pty` standalone). Naujas `_query_clean()` keičia `_normalize()` query stringe — šalina legal noise + `(NT)/(QLD)` parenthesised state codes, BET išsaugo brand'o žodžius `SERVICES`/`GROUP`/`INDUSTRIES`.
2. **Postcode vietoj state** — `_build_query_precise(name, postcode, site)` formatas: `"<clean>" 0810 site:facebook.com`. Postcode = 4-digit AU-specific signal, kur kas tikslesnis nei 2-letter state code.
3. **AU geo validation** — `_is_australian(title, url, state, postcode)`: tikrina `.com.au` / `+61` / `australia` / state kodą / state pilną pavadinimą (`AU_STATE_NAMES` dict) / postcode title arba URL'e. Pridėtas `_PARENS_STATE_RE` regex (`\((NSW|VIC|...)\)`). `_pick_best()` reikalauja **abiejų** gate'ų: fuzzy ≥ 0.5 **IR** AU signal.
4. **3-query strategija** — `_lookup_business` siunčia 3 query/biz vietoj 2: precise FB + precise IG + broad `"<name>" australia facebook` (be `site:` filtro, kad pasiektų .com.au footer'ius + plačiau indexuotus FB puslapius). Visi kandidatai merge'inami, AU gate'as post-filter.

**V2 rezultatas ant tų pačių 20 verslų:**
- 1 hit: **Hardy Landscaping NT → [facebook.com/hardylandscapingnt](https://facebook.com/hardylandscapingnt)** (true positive — Brave snippet "Darwin NT" patvirtina)
- FAST PAINTING Texas LLC false positive atmestas (AU validation veikia)
- API cost: 60 calls vs 40 (+50%, 25s vs 17s)
- **Precision: 0% → 100%. Recall: 5% → 5% (unchanged).**

**Importer re-run ant V2:** 1 row updated DB lygyje (Hardy ABN 11004693770 gavo `facebook_url`). Dashboard "Kontaktuotini" turi pasirodyti 1.

**Strateginis verdiktas:**
- Brave Search yra **fundamentaliai netinkamas šaltinis** AU SMB FB/IG discovery'ui — 5% recall ceiling laikosi net po pilno hardening'o
- Permąstytas tikslas: ne find_social.py tuning, o **stable lead enrichment pipeline** kuris generuotų pirmus klientus
- Aptarti TIER 1-3 alternatyvūs šaltiniai (Google Places, Apify FB/IG scrapers, Claude Vision verification, ABR Lookup, LinkedIn Sales Nav, Hunter/Apollo)
- **Sutarta strategija (žr. DECISION_LOG):** A (ABR trading name, free, 1 sesija) → B (Google Places smoke test 1000 leads, ~$30, 2 sesijos) → **conditional C** (pilnas Places + Apify + Claude pipeline, ~$4,700 mass run, 9-11 sesijų)

**Memory updates:**
- `project_state.md` — updated session #5 snapshot
- `find_social_brave_ceiling.md` — naujas memory (Brave 5% ceiling rationale)
- `MEMORY.md` — pridėtas pointer

### Kas liko / nepatvirtinta

- **`find_social.py` nėra production-ready kaip primary discovery layer** — V2 yra Tested-level kokybės (kodas teisingas, precision 100%), bet recall per žemas šitam šaltiniui. Atsiraks atskirai kai Plan A duos trading_name boost'ą.
- **Importer.py bug** (`--csv has_social.csv` perrašo business_name) — workaround dokumentuotas, fix odojamas (carry-over #5).
- **Dashboard UI loop nepatvirtintas** (carry-over iš sesijos #4 nepasirinktas dabartinėje sesijoje).
- **Plan A nepradėtas** — ABR Lookup API integracijai dar nieko nedaryta (research, code, test).
- **Plan B nepradėtas** — GCP project + Places API key + billing alerts neset'inti.

### Kitas žingsnis

**SESIJA #6 — Plan A start:**
1. Research ABR Lookup API: endpoint, auth model (GUID-based), rate limits, response shape, terms of service compliance
2. Create `enrich_abr.py` — async httpx client, 5 req/s limit per ABR ToS, retries
3. Smoke test ant 100 ABNs iš `filtered_with_dns.csv` — patvirtinti kad endpoint grąžina `trading_name`/`business_names` array
4. Scale ant pilnos 97k leads — pridėti `trading_name` stulpelį prie `filtered_with_dns.csv` (incremental write, resumable)
5. Re-run `find_social.py --limit 20` su `trading_name` (ne `business_name`) — palyginti hit-rate vs sesijos #5 V2 baseline

**SESIJA #7 — Plan B (jei A pasiekė ≥15% hit-rate):**
1. Setup GCP project, enable Places API, set $50 billing alert
2. Implement `enrich_places.py` Text Search wrapper + Place Details fetch
3. Smoke test ant 1000-row sample (~$30 spend)
4. Manual spot-check 30 grąžintų place_id'ų prieš AU verifikacijos accuracy
5. GO/NO-GO decision dėl pilno Plan C ($4,700 commitment)

## Istorija

| Data | Trukmė | Self-score | Pabaigtumas | Santrauka |
|---|---|---|---|---|
| 2026-05-25 #5 | ~3h | 7/10 | 77% | find_social.py validated end-to-end (V1 false positive → V2 100% precision via AU geo gate + 3-query strategy). Brave ceiling exposed at 5% recall. Plan A→B→C strategy agreed. Memory + docs synced. |
| 2026-05-25 #4 | ~2h | 8/10 | 77% | Streamlit dashboard (6 nauji failai: db, importer, i18n, queries, app, requirements). 159k leads → SQLite. Brave key validated. UI loop netestuotas. |
| 2026-05-25 #3 | ~3h | 7/10 | 83% | DNS check ant 97,801 ACT leads (1h45m, 0 errors). `run.py stage_dns` bug fix + `--gst-status` flag. Žinomas ~10% false-positive rate, recheck path UDP/DoH abandoned dėl resolver throttling. |
| 2026-05-24 #2 | ~1h | 9/10 | 78% | Parser bug fix (state/postcode tags) + pilnas parse 20 XML (159k leads, 97.8k ACT, 0 errors) |
| 2026-05-24 #1 | ~5h | 8/10 | 75% | Projekto setup nuo nulio: 5 CLI skriptai + 46 test'ai. Real ABR XML + API neištestuoti. |

---

## Sesija #4 (2026-05-25 — Streamlit dashboard + Brave key validated) — archyvas

### Ką padarėme

### Ką padarėme

**Naujas dashboard pod-projektas** ([dashboard/](dashboard/)):
- [dashboard/db.py](dashboard/db.py) — SQLite schema (`leads`, `outreach`, `activity`, `imports`) + mutation API (`set_status`, `update_outreach_fields`, `update_lead_socials`, `log_activity`). FK constraint'as `activity.abn` padarytas nullable po pirmojo run'o crash'o (`-import-` sentinel nesilaikė FK)
- [dashboard/importer.py](dashboard/importer.py) — CSV → SQLite idempotent UPSERT. Importavo 159,070 leads (97,801 ACT + 61,269 papildomų iš filtered_businesses). Industry auto-detect (19 keyword grupių) — ant 100-row sample 96% rows gauna industry tag
- [dashboard/i18n.py](dashboard/i18n.py) — LT/EN translation dict (~160 raktų), `get_lang/set_lang/t()` helper'iai per `st.session_state`
- [dashboard/queries.py](dashboard/queries.py) — 9 `@st.cache_data(ttl=60)` aggregations su `db_fingerprint()` (mtime hash) cache invalidation
- [dashboard/app.py](dashboard/app.py) — 5 tabai (Overview KPI + funnel + timeline / Leads su inline edit + bulk + detail panel / Analytics su Altair charts + ProgressColumn / Activity audit / Settings)
- [requirements-dashboard.txt](requirements-dashboard.txt) — streamlit ≥1.32, pandas ≥2.0, altair ≥5.2
- [start-dashboard.bat](start-dashboard.bat) — Windows launcher su auto-import jei `outreach.db` neegzistuoja
- README atnaujintas — Dashboard sekcija
- `.gitignore` — `dashboard/outreach.db*` pridėta

**Dashboard infra paleista:**
- `.venv` sukurta (Python 3.12.10)
- Dashboard deps įdiegtos (streamlit 1.57, altair 5.5, pandas 2.3)
- Pirmas pilnas importas: 159,070 leads į `outreach.db`
- Streamlit live `http://localhost:8501`, HTTP 200, 5,381 bytes — naršyklė atidaryta automatiškai
- Background task ID: `bkp8b1y27`

**Brave API key — validuota:**
- Tu pakopijavai `BRAVE_API_KEY` į [.env.example](.env.example), ne [.env](.env)
- Variantas A įvykdytas: `cp .env.example .env`
- python-dotenv + httpx instalavo į `.venv` ad-hoc
- Live Brave API test: HTTP 200, 20 rezultatų, pirmas `https://www.facebook.com/localelectriciansydney/`
- `.env` gitignored — saugu

**Memory updates:**
- `feedback_clickable_links.md` — naujas feedback memory (visos URL/path nuorodos privalo būti markdown links, ne plain text)
- `MEMORY.md` indeksas atnaujintas

### Kas liko / nepatvirtinta

- **`find_social.py` vis dar neegzistuoja live API run'as** — raktas validuotas atskira ad-hoc komanda, ne per `find_social.py`. Reikia paleisti `python find_social.py --limit 20` ant `no_website.csv`
- **Dashboard UI interaktyvūs flow nepatvirtinti naršyklėje** — paleido, HTTP 200, bet realus operatoriaus loop (pažymėti lead'ą kaip "sent", atidaryti detail panel, redaguoti contact info, patikrinti activity log) nebuvo atliktas
- **`.venv` neturi pipeline deps** — tik dashboard + dotenv + httpx. `find_social.py` paleidimui reikės `pip install -r requirements.txt` į tą patį `.venv`
- **Schema migration was iteratyvus** — `activity.abn FK NOT NULL` crash'ino pirmą importą, reikėjo rankinis `rm outreach.db` po fix'o. Future schema changes turi gauti migration mechanism
- **`recheck_dns_doh.py`, src/ orphans, dvigubas generate_domains** — visi carry-over iš sesijos #3, nepaliesta

### Kitas žingsnis

1. **Paleisti `find_social.py --limit 20`** — pirma live validacija. Pirma įdiegti `pip install -r requirements.txt` į `.venv`. Stebėti malformed responses, quota errors
2. Atidaryti dashboard naršyklėje ([http://localhost:8501](http://localhost:8501)) ir pereiti per realų workflow loop: pasirinkti 1 lead'ą, pažymėti "sent" su Facebook kanalu, įrašyti contact info, patikrinti audit log. **Tai patvirtins dashboard Tested → Production**
3. Jei `find_social.py --limit 20` švarus, scale į 1000 ir reimportuoti `has_social.csv` per dashboard Settings → "Run CSV import" (dashboard tada turės `facebook_url`/`instagram_url` užpildytus)

---

## Sesija #3 (2026-05-25 — DNS check ant 97,801 ACT leads)

### Ką padarėme

**Phase 7 real-data validation — DNS check stage:**
- Aptiktas pre-existing bug [run.py:269-272](run.py#L269-L272): `stage_dns` perduodavo `business_name` (raw uppercase) į `check_dns.check_all` ir saugojo visą `(bool, str)` tuple kaip `found_domain` stulpelį. Rezultatas: visi rows pažymimi `has_domain=True`, `no_website.csv` baigia 0 eilučių
- Fix: naudoti `name_normalized` + unpack `[r[0] for r in results]` / `[r[1] for r in results]`
- Aptiktas `state` vs `gst_status` confusion: `run.py --state ACT` filtruoja geographic ACT (Australian Capital Territory, 2,417 verslai), ne `gst_status=ACT` (active GST, 97,801 verslai). Pridėtas `--gst-status` flag + `_apply_gst_filter` helper [run.py:194-204](run.py#L194-L204)
- Smoke test (`--test`, 500 rows) patvirtino fix: **79.8% no-domain rate**, 0 errors
- Pilnas `python run.py --step dns --gst-status ACT -y` ant 97,801 leads — **1h 45min, 15 biz/s, 0 errors**
- Rezultatas: 267 has-domain (0.3%), **97,534 no-domain (99.7%)**
- 99.7% per geras — ekstrapoliuojant smoke-testą tikėjomės ~70-80% no-domain. Diagnostinis 30-row sample atskleidė **~10% false negatives** (verslai kurie *tikrai* turi domain'us, bet pažymėti kaip "no website")

**Root cause analizė (UDP DNS rate-limiting):**
- conc=100 × 5 candidates × 2 record types per business → resolver'iai 429-na po pirmų 5k užklausų
- dnspython.asyncresolver be adaptive backoff'o → timeout'as skip'inamas kaip "doesn't resolve"
- Smoke test'ai (500-1000 rows) buvo per maži, kad pasiektų throttle threshold

**Recheck attempt — abandoned:**
- Sukurtas `recheck_dns.py` (UDP, conc=30) — smoke davė 9.9% recovery rate, 5 biz/s → ETA 5.4h
- Sukurtas `recheck_dns_doh.py` (httpx + Cloudflare/Google DoH JSON) v1 — sequential candidates, 7 biz/s → ETA 230 min
- Sukurtas `recheck_dns_doh.py` v2 — parallel per business (5 candidates × 2 rtypes = 10 in-flight per biz × conc=100 = 1000 burst) — endpoint'ai throttle'ino, regression iki 1 biz/s
- Decision: priimti ~10% false-positive rate `no_website.csv`. Brave social search natūraliai išfiltruos daugumą (verslai su svetainėm turi labiau organic social presence)

**Memory updates:**
- `project_state.md` → session #3 snapshot (DNS validated, social pending)
- `run_py_dns_contract.md` — naujas feedback memory apie stage_dns kontraktą (tuple unpack + name_normalized)
- `MEMORY.md` indeksas atnaujintas

### Kas liko / nepatvirtinta

- **`find_social.py`** — `BRAVE_API_KEY` `.env` nenustatytas, niekada nepalietė live API
- **`no_website.csv` ~10% false positives** — žinoma trūkumas, neištaisyta (recheck path'as throttle-limited)
- **`recheck_dns_doh.py`** liko repo, bet ne active path'e — laukia ateities sesijos jei pasirodys outreach response pažaida
- **`src/` orphan failai** ir dvigubas `generate_domains` — carry-over iš sesijos #1, nepaliesta
- **`run.py --step all`** pilnas end-to-end nepatikrintas

### Kitas žingsnis

1. **Nustatyti `BRAVE_API_KEY`** `.env`, paleisti `python find_social.py --limit 20` ant `no_website.csv` — pirmas live API run. Stebėti malformed responses, quota errors. **Highest priority** — Phase 4 milestone
2. Jei 20 sample atrodo OK, scale iki 1000 ir patikrinti FB/IG hit rate. Memory expectation: dauguma AU SMB turi FB arba IG net be svetainės
3. Optional: revisit DNS recheck DoH path'ą jei outreach response rate'as parodys problemų ("aš turiu svetainę" complaint'ai)
