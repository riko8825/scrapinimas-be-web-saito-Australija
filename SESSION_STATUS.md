# SESSION_STATUS

## Paskutinė sesija: 2026-05-26 (sesija #8 — Stage B LIVE 365 svetainių + export_outreach.py + V2-LITE strategy decision)

### Ką padarėme

**Stage B — Website scraper LIVE (src/enrichment/enrich_website.py):**
- async httpx + BeautifulSoup4 (jau įdiegta v4.14.3)
- 4 page'ų strategija: homepage + /contact + /contact-us + /about
- Email extraction: mailto: links (priority) + plain-text regex + false positive filtras
- Social URLs: <a href> selectors + plain-text fallback (FB, IG, LinkedIn)
- robots.txt cache per host, per-domain 2s rate limiter, User-Agent transparent
- Concurrency 10, timeout 10s, MAX_BYTES 500KB safety

**Stage B LIVE smoke ant 365 lead'ų (6.5 min, $0 cost):**
- 221 OK (61%), 141 no_data (38%), 0 errors
- 181 emails (49%), 155 FB URLs (42%), 106 IG (29%), 32 LinkedIn (8%)

**Cumulative state po sesijų #7+#8:**
- 1105 lead'ai Stage A processed (498 OK = 45%)
- 365 lead'ai Stage B processed (221 OK = 61%)
- 470 lead'ai su BENT VIENU kontaktu
  - 465 phone, 180 email, 157 FB, 108 IG, 390 website
- 116 lead'ai su VISAIS 3 channels (email + phone + FB)
- 28 lead'ai eligible Stage C (priority ≥ 50, be jokio kontakto)
- 84,912 lead'ai NEpaskanuoti (likę Stage A eligible)

**export_outreach.py — CSV generation tool:**
- Paima top-priority enriched leads (--limit N --min-score X --channel any|email|phone|fb)
- Generuoja paste-ready CSV su email_subject + email_body + dm_message templates
- Adaptyvus pagal website status (turi vs neturi):
  - TURI website: AI tools $200-500/mo offer
  - NETURI: $500 vienkartinis svetainės setup
- Mark exported į outreach.db (idempotent)

**Bug fixes (sesijos eigoje):**
1. `_mark_exported` updated_at NOT NULL violation — fix UPDATE first then INSERT
2. Email body "noticed you don't have a website" net jei turi — fix adaptyvus opening
3. Industry hook syntax "with your practice" — fix "like yours in {natural noun}"
4. [DRY] placeholder cleanup iš pirmo dry-run testo

**CSV exports (output/):**
- outreach_ready_20260526_1403.csv (10 leads, su placeholder bug'u)
- outreach_ready_20260526_1405.csv (10 leads, gera data)
- outreach_ready_20260526_1821.csv (10 leads, fix'inta email logika)
- outreach_ready_20260526_1829.csv (470 leads, pilnas export'as)

**V2-LITE strategijos sprendimas (sesijos pabaigoje):**

Vartotojas pateikė detalų pasisakymą apie pipeline limit'us su 7 kritikomis (vertinimas 9/10):
1. has_domain=0 yra MELAS — reality 4 website lygiai (no/social-only/dead/modern)
2. Dead website detection = aukso market'as
3. PAIN SIGNALS > CONTACT INFO (reviews + rating + tech stack)
4. AU validation būtina (PROXYTECH bug = symptom)
5. Tech stack detection (Wix/Weebly/old WP)
6. Social presence finder verslams be svetainės
7. Sales angle generator (Claude Haiku personalized emails)

**Sprendimas:** V2-LITE (NE pilnas V2):
- Palikti esamus failus (NE delete'inti)
- Tikslas: PIRMI PINIGAI > perfect pipeline
- P0 (kita sesija): rating fields + AU validation + website classifier
- P1 (sesija po): sales angle generator + suburb tier
- P2-P3 (vėliau): mass run, Apify FB, competitor analysis

### Kas liko / nepatvirtinta

- **P0 darbai NEpadaryti** (atidėta sesijai #9):
  1. Places `userRatingCount + rating` field'ų pridėjimas (30 min)
  2. AU validation post-Stage A (anti-PROXYTECH, 30 min)
  3. Website classifier 0-3 lygiai (2-3h)
- **Vartotojas dar nesiunti email'ų rankomis** — turi 470 leads outreach_ready_20260526_1829.csv, bet rankinis warmup nepradėtas
- **Gmail paskyra naujam outreach'ui nesukurta** (vartotojo darbas, 5 min)
- **Mass run NEpaleistas** ant likę 84,912 eligible
- **PROXYTECH-like false positives** outreach.db'e — nepatikrinta kiek (1 aptiktas iš 470)

### Kitas žingsnis (sesija #9 — V2-LITE P0)

**3 darbai (3-4h):**

1. **P0.1 (30 min):** Atnaujint `src/enrichment/enrich_places.py` Field Mask:
   - Pridėti: `places.userRatingCount`, `places.rating`, `places.businessStatus`, `places.priceLevel`
   - DB schema: ALTER TABLE enrichment ADD COLUMN review_count INT, rating REAL, business_status TEXT, price_level INT
   - Re-process esamus 498 OK leads ($0 cost — tas pats Enterprise SKU tier'as)

2. **P0.2 (30 min):** AU validation post-Stage A:
   - Naujas modulis `src/enrichment/validators.py`
   - Foreach lead post-Stage A: tikrint phone +61 + website TLD .au + address AU state code
   - Jei FAIL — mark `stage_a_status='not_au'`, skip Stage B

3. **P0.3 (2-3h):** Website classifier 0-3:
   - Naujas modulis `src/enrichment/website_classifier.py`
   - HTTP HEAD + HTML parse: SSL, viewport tag, framework (meta generator), footer year, response time
   - DB schema: ALTER TABLE enrichment ADD COLUMN website_class INT, mobile_friendly INT, tech_stack TEXT
   - Classify 390 leads su website

**Sesijos #9 DoD:**
- 470 leads turi naujus stulpelius (rating, review_count, website_class)
- Nauja priority scoring formulė pagal V2-LITE logiką
- Top 50 "gold leads" CSV export'as su pain-signal-based ranking'u

### Carry-overs

- Vartotojo manual outreach (10 email'ai per pirmas 2 sav.) — nepradėta
- Gmail outreach paskyra — nesukurta
- Apify FB lookup leads be svetainės (~108 leads) — atidėta P2
- Mass run 84k — atidėta P3 (po V2-LITE patvirtina)

## Istorija

| Data | Trukmė | Self-score | Pabaigtumas | Santrauka |
|---|---|---|---|---|
| 2026-05-26 #8 | ~5h | 8/10 | 85% | Stage B LIVE 365 svetainių (61% hit, 0 errors). export_outreach.py + 4 CSV exports. V2-LITE strategy sprendimas. P0 atidėtas sesijai #9. |
| 2026-05-26 #7 | ~5h | 9/10 | 80% | Waterfall architecture + Stage A LIVE (1100 leads, 45% hit, $0 real cost). 365 ready Stage B. Solution architect + agent reality checks šaltinių kombinacija. Production-ready Stage A. |
| 2026-05-25 #6 | ~2h | 7/10 | 74% | Plan A→B pivot mid-session. enrich_abr.py sukurtas tada ištrintas. Places API research baigtas ($35/1k Enterprise, 1k free/mėn). Memory init pilnas. Code laukia dashboard/ push'o. |

### Ką padarėme

**Waterfall enrichment architecture** (Stages A=Places, B=Website scrape, C=SerpAPI):
- Solution architect agentas (`solution-architect` subagent) suprojektavo pilną pipeline'ą su quality gate'ais ir budget guards
- Nauja DB schema: `enrichment` table (21 stulpeliai) + `enrichment_runs` table (audit + cost tracking) — saugu pridėta į `dashboard/db.py` per `CREATE TABLE IF NOT EXISTS`
- `src/enrichment/` modulis (5 failai): `filters.py`, `scoring.py`, `budget.py`, `enrich_places.py`, `__init__.py`
- Quality gates:
  - Pre-Stage A: industry whitelist (19), gst_status='ACT' (NE 'Active' — ABR abbreviation), valid 4-digit AU postcode, NOT trustee, NOT enriched
  - Pre-Stage B: website_url + NOT free-tier hosting (wix/squarespace/wordpress.com/etc, 13 patterns) + NOT facebook/instagram as website
  - Pre-Stage C: A+B contact channels TUŠTI + priority_score ≥ 50 (cost gate)
- Priority scoring: 0-100 pts pagal industry (0-40, legal/accounting 40, healthcare 38), state (0-30, NSW 30, VIC 28), name quality (0-20), GST Active (0-10)
- Budget guards: PLACES_MONTHLY_CAP_USD=50 default + can_spend() pre-flight check
- 86,017 lead'ų eligible Stage A iš 159,070 (54% pass quality gates)

**GCP setup (vartotojo darbas):**
- Vartotojas užregistravo Google Cloud project → enable'ino Places API (New) → priėmė EEA Terms of Service → sukūrė API key (AIza...) → įdėjo į `.env`
- Google free trial: €256.52 credit + 90 dienų (3× standartinį $300/90d) — pakankamai pilnam mass run'ui
- API valid + Places API live test su "Bunnings Sydney" → HTTP 200, Bunnings Alexandria adresas + phone + website

**Live smoke #1 — 100 leads (7.7s, $0 real cost):**
- Hit rate: **56%** (56/100 OK)
- Su phone: 53%, su website: 43%, su trading_name: 61%
- 0 errors
- Real lead'ai (manual spot-check): BIOART DENTAL VIC, AMM DENTAL CLINIC VIC, JUST ELECTRICAL SYDNEY, SG MCLEAN PLUMBING

**Live smoke #2 — 1000 leads (90s, $0 real cost):**
- Hit rate: **44.2%** (442/1000 OK) — žemiau už 100-batch dėl diversity'o didesnio sample'e
- 526 not_found (51%) — sole trader'iai be Google My Business profile'io
- 32 errors (3.2%) — trumpas rate-limit pause vidury batch'o, auto-resume
- Cost: $35 nominal'iai, **$0 real** (telpa į 1k Enterprise SKU free monthly tier'į)

**Sumarinis state po 1100 leads:**
- 498/1100 OK (45%) → 465 su phone, 390 su website, 503 su trading_name
- **365 leads eligible Stage B** (turi website, ne FB-as-website, ne free-tier)
- 133 leads su website BUT facebook.com URL ar free-tier hosting → tiesiai į Stage C
- 32 leads su error status → retry sesijoje #8 (TBD)

**Memory updates:**
- `places_api.md` — endpoint specs (jau buvo iš sesijos #6)
- `waterfall_architecture.md` — naujas memory, pilna 3-stage architektūra + quality gates + scoring + budget
- `MEMORY.md` indeksas atnaujintas

### Kas liko / nepatvirtinta

- **Stage B kodas neegzistuoja** — `enrich_website.py` paruoštas tik specifikacijoj DECISION_LOG'e. Sesija #8 darbas.
- **Stage C kodas neegzistuoja** — `enrich_socials.py` (SerpAPI) paruoštas tik specifikacijoj. Sesija #9 darbas.
- **Orchestrator (`run_enrichment.py`)** — paruoštas specifikacijoj. Sesija #10.
- **Pilnas 86k mass run NEpaleistas** — laukia, kol Stage B/C bus baigtos, kad single-pass pipeline ekonomiškai veiktų
- **32 errors retry** — `--retry-errors` flag'as enrich_places.py — pending sesija #8
- **65/86k eligible Stage A nepaliesti** — 1100 ėmime tik 1.3% scope'o. Tikras coverage'o test'as ant 5k+ būtų autoritetingesnis baseline.
- **Email outreach setup** — net jei gauname email'us iš Stage B, vis dar reikia: Gmail SMTP setup, deliverability warmup, ZeroBounce verification ($65 už 10k jei bounce rate >2%)
- **Manual outreach BE Stage B** — vartotojas gali jau dabar paimti 498 enriched lead'us su phone iš outreach.db ir skambinti. Skambučio script'as nepasiruoštas.

### Kitas žingsnis

**SESIJA #8 — Stage B (website scraper):**
1. Sukurti `src/enrichment/enrich_website.py` — async httpx + BeautifulSoup4
2. Foreach lead su `website_url`: GET homepage + /contact + /about + footer
3. Extract: email (mailto:, regex), FB URL (a[href*="facebook.com"]), IG URL, LinkedIn
4. UPDATE enrichment table su contact_email, scraped_fb_url, scraped_ig_url, linkedin_url
5. Politeness: 2s per-domain delay, robots.txt compliance, concurrency=10
6. Smoke ant 365 eligible (~7 min ETA), tikėtinas hit rate 50-70%

**SESIJA #9 — Stage C (SerpAPI):**
1. Vartotojas užregistruos SerpAPI account (FREE 100 searches/mėn, žiūr https://serpapi.com)
2. Sukurti `src/enrichment/enrich_socials.py` — Google search via SerpAPI
3. Foreach top-priority lead be jokio kontakto: query "<trading_name> <state> facebook OR instagram"
4. UPDATE enrichment.scraped_fb_url + scraped_ig_url
5. Cap 5k calls = $25 cost

**SESIJA #10 — Orchestrator:**
1. Sukurti `src/enrichment/run_enrichment.py` — vienas CLI, `--stage all` paleidžia A→B→C iš eilės
2. Dashboard view: `dashboard/queries.py` papildomas enrichment funnel
3. End-to-end test ant 1000 leads
4. Decision dėl pilno 86k mass run

## Istorija

| Data | Trukmė | Self-score | Pabaigtumas | Santrauka |
|---|---|---|---|---|
| 2026-05-26 #7 | ~5h | 9/10 | 80% | Waterfall architecture + Stage A LIVE (1100 leads, 45% hit, $0 real cost). 365 ready Stage B. Solution architect + agent reality checks šaltinių kombinacija. Production-ready Stage A. |
| 2026-05-25 #6 | ~2h | 7/10 | 74% | Plan A→B pivot mid-session. enrich_abr.py sukurtas tada ištrintas. Places API research baigtas ($35/1k Enterprise, 1k free/mėn). Memory init pilnas. Code laukia dashboard/ push'o. |

### Ką padarėme (sesijos #6 antra pusė)

**Plan A → Plan B pivot:**
- Vartotojas pasakė: "be jokio brave. jo prenumerata jau atsaukta" + "Pereinam tiesiai į Plan B (Google Places)" — strateginis sprendimas eliminate ABR Lookup viduriniąjį žingsnį
- enrich_abr.py ištrintas (Plan A code'as nebenaudojamas)
- `.env.example` ABR_* config (`ABR_GUID`, `ABR_ENDPOINT`, `ABR_CONCURRENCY`, `ABR_TIMEOUT`, `ABR_ENRICHED_CSV`) pakeistas į PLACES_* config (`GOOGLE_PLACES_API_KEY`, `PLACES_ENDPOINT`, `PLACES_CONCURRENCY`, `PLACES_TIMEOUT`, `PLACES_REGION`, `PLACES_LIMIT`, `PLACES_DRY_RUN`)

**Google Places API research:**
- Endpoint patvirtinta: `POST https://places.googleapis.com/v1/places:searchText` (Places API New v1)
- Headers: `X-Goog-Api-Key`, `X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,places.websiteUri,places.internationalPhoneNumber,places.types`
- Body: `{"textQuery": "<name> <postcode>", "regionCode": "AU"}`
- **Pricing — Text Search Enterprise SKU (websiteUri + phone Enterprise field'ai):**
  - $35/1000 calls (0-100k tier)
  - **1k free monthly quota** — smoke test 1000 leads = $0 jei tilps į vieno mėnesio quota
  - 100k mass run ≈ $3,465 (vs sesijos #5 estimate $4,700 — 25% pigiau, nes vienas Text Search call'as atstoja 2-step Text Search + Place Details)
- Rate limits: 600 QPM default → mūsų `PLACES_CONCURRENCY=10` ≈ 600 RPM steady state ✅

**Blockers atskleisti:**
- `dashboard/` direktorija (su `db.py`, `importer.py`, `app.py`, `i18n.py`, `queries.py`, `outreach.db`) **neegzistuoja šitam git repo'e** — sesijos #4 dashboard kodas niekada nebuvo committed į origin/main. enrich_places.py architektūra reikalauja `leads` lentelės schemos žinojimo prieš SELECT/UPDATE pattern'ą — code'as laukia, kol vartotojas push'ins `dashboard/`.

**Memory updates:**
- `places_api.md` — naujas reference memory (endpoint, headers, field mask, pricing, rate limits)
- `abr_lookup_api.md` — pažymėtas SUPERSEDED (paliekamas archyvui)
- `MEMORY.md` indeksas atnaujintas

**Architektūros sprendimai (DECISION_LOG):**
- Naujas įrašas "Plan A → Plan B pivot": single-source-of-truth value (vienas call = trading_name + phone + website + place_id), GUID friction eliminate, cost model 25% pigiau, AU SMB coverage hipotezė ≥60%
- Senas įrašas "Plan A start: enrich_abr.py" pažymėtas [SUPERSEDED] header'iu

### Ką padarėme (sesijos #6 pirma pusė, archyvas)

**Memory init:**
- Sukurta visa memory infrastruktūra `~/.claude/projects/c--Users-pinig-scrapinimas-be-web-saito-Australija/memory/`:
  - `MEMORY.md` indeksas (dabar 7 pointer'iai)
  - `user_role.md` — Empirra founder profile + komunikacijos preferencijos
  - `project_state.md` — pipeline state per sesijos #6 start, blockers, Plan A→B→C strategija
  - `find_social_brave_ceiling.md` — 5% recall ceiling rationale
  - `feedback_clickable_links.md` — markdown links taisyklė
  - `run_py_dns_contract.md` — stage_dns tuple-unpack pattern
  - `abr_lookup_api.md` — JSONP endpoint specs (dabar SUPERSEDED)

**Brave key leak fix:**
- Aptikta sesijos #6 start: `.env.example` faile committed realus `BRAVE_API_KEY=BSAh5c6r_...` value. Vartotojas paskelbė: prenumerata atšaukta, key dead. `.env.example` pakeistas į `BRAVE_API_KEY=your_key_here`. Senas key vis dar git history, bet useless.

**Plan A — enrich_abr.py (SUPERSEDED tos pačios sesijos antroje pusėje):**
- enrich_abr.py sukurtas + push'inta (commit f2a8145), tada ištrintas Plan B pivot metu
- Sanity tests buvo 3/3 PASS (JSONP unwrap + name picker + error envelope)
- Code'as deleted, bet DECISION_LOG įrašas + memory `abr_lookup_api.md` palikti referensui

### Ką padarėme

**Memory init:**
- Sukurta visa memory infrastruktūra `~/.claude/projects/c--Users-pinig-scrapinimas-be-web-saito-Australija/memory/`:
  - `MEMORY.md` indeksas (6 pointer'iai)
  - `user_role.md` — Empirra founder profile + komunikacijos preferencijos
  - `project_state.md` — pipeline state per sesijos #6 start, blockers, Plan A→B→C strategija
  - `find_social_brave_ceiling.md` — 5% recall ceiling rationale
  - `feedback_clickable_links.md` — markdown links taisyklė
  - `run_py_dns_contract.md` — stage_dns tuple-unpack pattern
  - `abr_lookup_api.md` — JSONP endpoint specs + GUID auth + response shape

**Brave key leak fix:**
- Aptikta sesijos #6 start: `.env.example` faile committed realus `BRAVE_API_KEY=BSAh5c6r_...` value (public GitHub repo). Pakeista į `BRAVE_API_KEY=your_key_here`. Vartotojas TURI revoke'inti compromised key per Brave dashboard ir generuoti naują (žr. carry-over).

**Plan A — enrich_abr.py:**
- Research: ABR Lookup endpoint `https://abr.business.gov.au/json/AbnDetails.aspx` (JSONP, params: `abn`, `guid`, `callback`). Registration: https://abr.business.gov.au/Tools/WebServicesAgreement. WebFetch patvirtino live endpoint (`Message: "The GUID entered is not recognised as a Registered Party"` su test GUID).
- Sukurtas [enrich_abr.py](enrich_abr.py) — async httpx + tenacity exponential backoff + tqdm progress + JSONP unwrap + resumable design (skip already-processed ABNs). Concurrency=5 default (politeness, ne hammering).
- `_pick_trading_name()` heuristika: pirmas `BusinessName` be legal suffix (`pty ltd`, `proprietary limited`, `inc`); fallback — pirmas as-is; jei tuščia — empty string (find_social.py fallback į `business_name`).
- 5 nauji env'ai pridėti į `.env.example`: `ABR_GUID`, `ABR_ENDPOINT`, `ABR_CONCURRENCY`, `ABR_TIMEOUT`, `ABR_ENRICHED_CSV`.
- Sanity tests: `py_compile` PASS, `--help` PASS, inline JSONP unwrap + name picker + error envelope — 3/3 PASS.

**Memory updates:**
- `project_state.md` updated session #6 snapshot
- `abr_lookup_api.md` — naujas reference memory
- `MEMORY.md` indeksas atnaujintas

### Kas liko / nepatvirtinta

- **`dashboard/` neegzistuoja git repo'e** — sesijos #4 kodas niekada nebuvo committed. enrich_places.py architektūra reikalauja `dashboard/db.py` schemos žinojimo (leads table columns) prieš SELECT/UPDATE pattern'ą. Vartotojas TURI push'inti `dashboard/` folderį prieš sesiją #7.
- **enrich_places.py kodas neegzistuoja** — laukia dashboard/ push'o. Research baigtas, .env config paruoštas, DECISION_LOG + memory atnaujinti. Tik code generation likę.
- **GCP project + Places API key + billing alert nesetup'inti** — vartotojas atliks paraleliai sesijoje #7 (instant setup, jokio email lūkesčio).
- **find_social.py nepritaikytas Places duomenims** — po Places enrichment'o reikės modifikuoti find_social.py, kad naudotų `displayName.text` arba fallback `business_name` query string'e (jei vis tiek norėsim social discovery layer'į papildomam coverage'ui).
- **DNS no_website.csv neegzistuoja šitam clone'ui** — `output/` direktorija tuščia. Bet Places enrichment'as veiks ant outreach.db `leads` table, ne CSV — tai blocker'is dingsta.
- **Importer.py bug** (carry-over #5) — `--csv has_social.csv` perrašo business_name. Workaround dokumentuotas, fix odojamas.
- **Dashboard UI loop nepatvirtintas** (carry-over iš sesijos #4).

### Kitas žingsnis

**SESIJA #7 (kai dashboard/ push'inta):**
1. Vartotojas push'ina `dashboard/` folderį (db.py, importer.py, app.py, i18n.py, queries.py) į origin/main. Optional: outreach.db schema dump'as arba sample DB.
2. Vartotojas paraleliai: GCP Console → New project → Enable "Places API (New)" → Credentials → API key → Restrict to Places API + IP allowlist + $50 monthly billing alert
3. Vartotojas įdeda `GOOGLE_PLACES_API_KEY=<key>` į `.env`
4. Claude inspect'ina `dashboard/db.py` `leads` table schemą — pasitvirtina, kurie stulpeliai egzistuoja (trading_name, phone, website_url, place_id) arba ar reikia ALTER TABLE migration
5. Claude sukuria `enrich_places.py` — async httpx + SQLite-native (SELECT FROM leads, UPDATE leads), idempotent (skip if place_id NOT NULL), dry-run default, `--live` flag
6. Dry-run sanity check: `py -3 enrich_places.py --limit 10` — pamatom queries, NEhit'inam live API
7. Live smoke: `py -3 enrich_places.py --live --limit 1000` (~$0 if first 1k of month, ~$35 if quota exhausted)
8. Manual spot-check 30 grąžintų leads — accuracy + AU geo + name match quality
9. GO/NO-GO decision dėl full 100k mass run (~$3,465)

**Carry-overs:**
- dashboard/ folderis push'inamas į repo
- GCP setup + Places API key
- DNS stage rerun (jei vis tiek norėsim DNS-based filtravimo papildomam coverage'ui)
- Importer.py bug fix (route social CSV į dedicated importer)
- find_social.py adaptacija prie Places duomenų (optional, jei Places coverage < 100%)

## Istorija

| Data | Trukmė | Self-score | Pabaigtumas | Santrauka |
|---|---|---|---|---|
| 2026-05-25 #6 | ~2h | 7/10 | 74% | Plan A→B pivot mid-session. enrich_abr.py sukurtas tada ištrintas. Places API research baigtas ($35/1k Enterprise, 1k free/mėn). Memory init pilnas. Code laukia dashboard/ push'o. |
| 2026-05-25 #5 | ~3h | 7/10 | 77% | find_social.py validated end-to-end (V1 false positive → V2 100% precision via AU geo gate + 3-query strategy). Brave ceiling exposed at 5% recall. Plan A→B→C strategy agreed. Memory + docs synced. |

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
