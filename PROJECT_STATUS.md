# PROJECT_STATUS — ABR Outreach Pipeline

**Last updated:** 2026-05-26 (sesija #10 — V2-LITE P1 LIVE: suburb_tier + rule-based sales_angle + strict pre-flight + P0 fix'ai. $0 budget)
**Phase:** Phase 7 real-data validation — Stage A + B LIVE, **V2-LITE P0+P1 LIVE** (380 leads classified, 498/498 turi angle subject+body, top 50 strict gold leads CSV su 0 DEFAULT templates). Blokerius: vartotojo manual outreach NEpradėta (Gmail nesukurta).

## Tikslas

Surasti Australijos verslus BE svetainės iš ABR public XML dump'ų, papildyti
juos FB/IG social signal'ais ir sugeneruoti personalizuotus DM pranešimus
(Empirra outreach kampanijai). Operatoriaus workflow + analitika per Streamlit
dashboard ([dashboard/](dashboard/)).

## Modulių matrica

Status legend: Planned = 0%, In Build = 30%, Tested = 70%, Production = 100%, Blocked = 0%.

| # | Modulis | Failas | Status | Komentaras |
|---|---|---|---|---|
| 1 | XML parser | [abr_parser.py](abr_parser.py) | **Production** | Validated 2026-05-24 ant 20 real XML (12 GB, 20.2M įrašų, 737s, 0 errors) → 159,070 leads / 97,801 ACT. State/postcode bug fixed. |
| 2 | DNS check | [check_dns.py](check_dns.py) | **Production** | Validated 2026-05-25 ant 97,801 ACT leads (1h 45min, 15 biz/s, 0 errors) → 97,534 no-website. Known: ~10% false positives nuo UDP resolver throttling. |
| 3 | Social search (Brave) | [find_social.py](find_social.py) | **Tested** | Sesija #5: paleistas end-to-end ant 20 leads × 2 iteracijų. V1 (raw) 1/20 false positive; V2 (po hardening — `_query_clean` + 3-query strategy + `_is_australian` gate) 1/20 true positive (Hardy Landscaping NT). Precision 0% → 100%. **Recall plateau'auja ties 5% — Brave nėra tinkamas primary šaltinis**. Modulis veikia teisingai savo apimtyje; bus naudojamas po Plan A trading_name boost'o. |
| 3b | Social discovery — pivoted strategy | (planned) | **In Build (architecture only)** | Sesija #5: sutarta A→B→conditional C strategija. A: ABR Lookup API trading_name enrichment (free, 1 sesija). B: Google Places API smoke test 1000 leads (~$30, 2 sesijos). C (conditional): pilnas Places + Apify FB/IG + Claude vision pipeline ($4,700 mass run, 9-11 sesijų). Decision criteria DECISION_LOG sesija #5. |
| 3c | Plan A — ABR Lookup enrichment | (deleted) | **Skipped** | Sesija #6 pirma pusė: enrich_abr.py sukurtas + 3/3 sanity tests PASS. Sesija #6 antra pusė: SKIPPED + code ištrintas po vartotojo sprendimo pivot tiesiai į Plan B. Žr. DECISION_LOG sesija #6 antra pusė. |
| 3d | Plan B / Stage A — Places enrichment | [src/enrichment/enrich_places.py](src/enrichment/enrich_places.py) | **Tested** | Sesija #7: production ant 1100 leads, 45% hit. Sesija #9: FieldMask pridėtas rating/userRatingCount/businessStatus/priceLevel + integracija į validators (au_validation post-fetch). Re-process 498 leads SKIPPED (vartotojo sprendimas, nauji laukai bus pildomi tik nauj enrichment'uose). |
| 3e | Stage B — Website scraper | [src/enrichment/enrich_website.py](src/enrichment/enrich_website.py) | **Tested** | Sesija #8: 365 leads, 61% hit rate, 49% email + 42% FB + 29% IG + 8% LinkedIn. 0 errors. robots.txt compliant, per-domain 2s politeness. Cost: $0. |
| 3f | Stage C — SerpAPI socials lookup | (planned) | **Planned** | Atidėtas iki post-V2-LITE proof. SerpAPI ($5/1k, 100 free/mėn). Cap 5k leads = $25. Vykdoma TIK lead'ams be jokio kontakto + priority_score ≥ 50. |
| 3g | Enrichment orchestrator | (planned) | **Planned** | Sesija #11+ (po P1). `run_enrichment.py --stage all` — vienas CLI visi 3 stage'ai iš eilės. |
| 3h | V2-LITE validators (AU validation) | [src/enrichment/validators.py](src/enrichment/validators.py) | **Production** | Sesija #9: vote-based 3-signal (phone +61 / website .au / address AU state). 13/13 self-test PASS. Integruotas į enrich_places `_enrich_one`. Anti-PROXYTECH bug fix. |
| 3i | V2-LITE website classifier | [src/enrichment/website_classifier.py](src/enrichment/website_classifier.py) | **Production** | Sesija #9: async heuristic classifier (SSL + viewport + 15 tech stack patterns + footer year + TTFB). 380 leads classified 2.5min ($0): 68 dead / 99 bad / 213 modern. Sesija #10: `_is_cms_template_footer()` guard 10 markerių (Wix/Squarespace/GoDaddy). Re-classify 25 Wix: footer set 10→9 (minor false-positive panaikintas). |
| 3j | V2-LITE scoring | [src/enrichment/scoring_v2.py](src/enrichment/scoring_v2.py) | **Production** | Sesija #9: ~200pt formulė (6 komponentų). Sesija #10: + `suburb_tier` 7-tas komponentas (+5/+3pt), CLOSED_PERMANENTLY_PENALTY -10000 hard exclude. 9/9 self-test cases PASS (4→9, įskaitant ghost domain, CLOSED stress, tier-1, regional). |
| 3k | Migration framework | [migrations/apply_migration.py](migrations/apply_migration.py) | **Production** | Sesija #9: idempotent SQLite ADD COLUMN runner (PRAGMA table_info pre-check). 001_v2lite.sql: 14 stulpelių + 4 indeksai applied. Sesija #10: 002_sales_angle.sql (4 stulpeliai + 1 index) applied 5/5 0 errors. |
| 3l | Top N gold leads exporter | [export_gold_leads.py](export_gold_leads.py) | **Production** | Sesija #9: CSV exporter su pain-signal breakdown (30 stulpeliai). Sesija #10: `--strict` flag (pain signal + reviews≥10 + phone + ICP≥50) + 4 nauji stulpeliai (suburb_pts, angle_template_id, angle_subject, angle_body) → 34 stulpeliai total. 498→255 strict→top 50, 0 DEFAULT templates. |
| 3m | V2-LITE suburb tier | [src/enrichment/suburb_tier.py](src/enrichment/suburb_tier.py) | **Production** | Sesija #10: 87 tier-1 (Mosman/Toorak/Vaucluse/Cottesloe) + 142 tier-2 (CBD + middle-affluent) + 9 CBD postcode fallback. Parser regex `<Suburb> STATE POSTCODE$` atmeta non-AU. Doctest + manual self-test PASS. Heuristic source — CoreLogic 2023-2024. |
| 3n | V2-LITE sales angle (rule-based) | [src/enrichment/sales_angle.py](src/enrichment/sales_angle.py) | **Production** | Sesija #10: 11 priority-ordered template'ų (NE Claude — $0 only per user decision). 498/498 leads filled. Distribution: 218 DEFAULT / 108 NO_SITE_LOW_REVIEWS / 71 CLASSIFIER_DEAD / 36 LEGACY_STACK / 26 NO_MOBILE / 19 STALE_FOOTER / 8 NO_SSL / 9 WEBSITE_CLASS_2 / 2 STALE+MOBILE / 1 WIX_OLD. Unit test'ų trūksta. |
| 4 | Outreach generator | [generate_outreach.py](generate_outreach.py) | **Tested** | 27 templates su import-time assert'ais, end-to-end test'as ant synthetic dataset pereina |
| 5 | Orchestrator | [run.py](run.py) | **Tested** | Pre-flight, --step/--test/--state/--gst-status/--resume, Telegram, summary table; parse + DNS stage'ai Production-validated, social/messages priklauso nuo API |
| 6 | Unit test suite | [test_pipeline.py](test_pipeline.py) | **Production** | 46/46 PASS; tikrina visus 4 core helpers + mocked DNS |
| 7 | Dashboard (outreach tracking) | [dashboard/app.py](dashboard/app.py) | **Tested** | Streamlit, 5 tabai (Overview/Leads/Analytics/Activity/Settings), SQLite [dashboard/db.py](dashboard/db.py), CSV importer su industry auto-detect, LT/EN i18n. Paleistas — HTTP 200, 159,070 leads loaded. UI interaktyvūs flow (mark sent, edit detail, audit log) **nepatvirtinti naršyklėje**. |

**Pabaigtumas:** (10 × 1.0 + 6 × 0.7 + 1 × 0.3 + 2 × 0.0) / 18 × 100% = 14.5 / 18 ≈ **81%**

> Sesija #10 (2026-05-26): + 2 V2-LITE modulis (suburb_tier Production, sales_angle Production); 3 moduliai pakelti Tested → Production (website_classifier, scoring_v2, gold leads exporter). Denominator 15 → 18 (skip 3c Skipped). Numerator išaugo nuo praeito skaičiavimo (~9.5) iki 14.5. Pabaigtumas: kruopščiai perskaičiuotas pagal status legend → **81%** (sesija #9 deklaruotas 77% skaičiavime turėjo aritmetikos klaidą — realiai buvo ~70%).
>
> Sesija #9 (2026-05-26): pridėti 5 V2-LITE modulis (validators Production, website_classifier Tested, scoring_v2 Tested, migration framework Production, gold leads exporter Tested). Denominator 10 → 15. Pabaigtumas deklaruotas 77% (re-check sesijoje #10 atskleidė aritmetikos klaidą — tikrasis ~70%).
>
> Sesija #6 antra pusė (2026-05-25): modulis 3c "Plan A — ABR Lookup" SKIPPED (statusas 0%, code ištrintas), pridėtas naujas 3d "Plan B — Places enrichment" In Build (research only, 30%). Denominator padidėjo 9 → 10, pabaigtumas krito 74% → 67%, BET reali strategija švaresnė — eliminated middleman API (ABR), tiesiogiai prie source-of-truth (Places turi trading_name + phone + website vienu call'u).
>
> Sesija #6 pirma pusė (2026-05-25): pridėtas naujas modulis #3c "Plan A — ABR Lookup enrichment" In Build (kodas paruoštas, smoke laukia GUID). Denominator padidėjo 8 → 9, bendras pabaigtumas matematiškai krito 77% → 74%, BET reali progress'as pozityvi — paskutinis blocker'is Plan A startui (kodas neegzistuoja) išspręstas; liko tik external dependency (GUID email).
>
> Sesija #5 (2026-05-25): find_social.py In Build → Tested (+10pp recall pipeline savo kontekste), bet pridėjome naują modulį #3b "Social discovery pivoted strategy" In Build (architecture only) į denominator. Bendras pabaigtumas išliko 77%. Tikras "value delivered" — Brave kelio limitai apnuoginti su real-world skaičiais; nustatytas profesionalus alternatyvus kelias (A→B→C) su konkrečiomis sąnaudomis ir laiko prognozėmis.

## Iki 100% trūksta

1. **Manual outreach kickoff (KRITINIS, vartotojo darbas — sesija #11):** Gmail paskyros setup + 5-10 emails per dieną iš `gold_leads_20260526_2218.csv`. Be reply'ų neturim signal'o ar V2-LITE patvirtina.
2. **`sales_angle.py` unit test'ai** — template priority assert'ai (CLASSIFIER_DEAD > LEGACY_STACK), placeholder safety, fallback edge cases. Dabar tik dry-run preview validation.
3. **scoring_v2 ghost domain edge case** (`has_domain=1 + URL=NULL` → 0pt vietoj +10pt) — patikrinti su realiais reply duomenimis sesijoje #11.
4. **Stage A re-process (po V2-LITE proof'o):** 498 esamų OK leads gauna rating/reviewCount/businessStatus/priceLevel ($17.43 nominal, $0 real per free trial).
5. **Mass run 84,532 likę eligible** — TIK po ≥1 closed deal'o iš 50 outreach attempts.
6. **Stage C — SerpAPI socials lookup** (atidėta, $5/1k × 5k = $25, gali būti atmesta per $0 budget rule).
7. **Apify FB lookup verslams be svetainės** (~108 leads) — atidėta P2.
8. **Dashboard UI loop patikrinimas** — Tested → Production: realus operatoriaus scenarijus naršyklėje.
9. **importer.py bug fix** (carry-over sesija #5): `--csv has_social.csv` perrašo `business_name=""`. Fix: route social-schema CSV į `import_socials_if_present()` only.
10. **Wix CMS footer fix išplėtimas** — pridėti Wix Editor X, Squarespace 7.1 marker'ius (dabar fix konservatyvus — tik 1/10 footer'ių panaikinti).
11. **suburb_tier annual refresh strategy** — hardcoded list iš 2023-2024 CoreLogic, reikia decision'o ar drop'inti į CSV failą / cron'inti refresh.
12. **Orchestrator** (`src/enrichment/run_enrichment.py`) — vienas CLI visi Stage'ai + V2-LITE post-processors (classifier + sales_angle + gold export). Sesija #12+.

## Known issues / shortcuts

- **`no_website.csv` ~10% false positives** — UDP resolver throttling sustained >7 biz/s. 30-row diagnostic sample rodė 23% false negative; 1000-row recheck smoke parodė 9.9% recovery (tiksliau). Brave social search natūraliai filtruos didžiąją dalį (verslai su svetainėm turi labiau organic social presence).
- **`recheck_dns_doh.py`** liko repo, bet **ne active path'e**. v1 sequential (7 biz/s) ir v2 parallel-per-business (1 biz/s regression) abu netinkami pilnam 97k re-check'ui. Saugoma ateičiai.
- **Du `generate_domains` implementations** egzistuoja:
  - `src/utils.py:218` — `.com.au`, `.com`, first-word fallback, `.net.au`
  - `check_dns.py:90` — `.com.au`, `.com`, `.au`, hyphenated `.com.au`, hyphenated `.com`
  - Aktyviai naudojamas yra `check_dns` versija; `src/utils.py` versija — orphan
- **`src/parser.py`, `src/dns_check.py`, `src/social.py`, `src/messages.py`, `src/pipeline.py`** — visi orphan'ai. Root-level skriptai yra naujesnės versijos. Saugu ištrinti.
- **Kmart Australia tipo pavadinimai** — `name_normalized="kmart australia"` → join → `"kmartaustralia.com.au"`, kuris neegzistuoja (tikras yra `kmart.com.au`). False-negative ant multi-word brand'ų.
- **Brave free tier = 2000 req/mėn** — Sesija #5: pakeitėme į 3 query/biz (precise FB + precise IG + broad), tad 666 biz/mėn ant free tier. Mass run'ams reikės arba paid Brave tier'o arba kitokio šaltinio (Plan B/C).
- **Brave Search recall ceiling = ~5%** (sesija #5 finding) — net su pilnu hardening'u (query cleanup, postcode geo, AU validation, 3-query strategy) Brave neranda daugiau nei 5% AU SMB FB/IG profile'ių. Žr. memory `find_social_brave_ceiling.md`. Plan A→B→C — atsakymas į šitą limitą.
- **importer.py — social CSV mishandling** (sesija #5 discovery) — `--csv has_social.csv` per `upsert_leads()` perrašo `business_name=""`, nes social CSV turi `name` stulpelį. Workaround: niekada nepaleisti `--csv has_social.csv`, naudoti tik auto-discovery (`import_socials_if_present()`). Fix planuotas (carry-over).

## Phases

| Phase | Statusas | Aprašymas |
|---|---|---|
| 1. Project setup | ✅ Production | CLAUDE.md, requirements, env.example, gitignore |
| 2. Parser + utils | ✅ Production | abr_parser.py validated ant 20 real XML (159k leads). src/utils.py shared helpers. |
| 3. DNS check | ✅ Production | check_dns.py validated ant 97,801 ACT leads (1h45m, 0 errors, ~10% false-positive known) |
| 4. Social search | 🚧 In Build (pivoted) | find_social.py validated ant 20 leads (sesija #5): precision 100%, recall 5%. Brave ceiling identified. Naujas kelias: Plan A (ABR trading_name) → B (Places) → conditional C (hybrid). Decision log sesija #5. |
| 5. Outreach generator | ✅ Tested | generate_outreach.py + 27 templates |
| 6. Orchestrator + tests | ✅ Tested | run.py + test_pipeline.py 46/46. Parse + DNS stage'ai Production-validated. |
| 7. Real-data validation | 🚧 In Build | Parser ✅ 2026-05-24 (159k leads). DNS ✅ 2026-05-25 (97.5k no-website). Social pending. |
| 8. Production handoff | ⏳ Planned | First outreach campaign launch |
| 9. Dashboard | ✅ Tested | Streamlit + SQLite, 159k leads loaded, 5 tabai, LT/EN i18n. UI flow paliestas dirbant, bet nepasirinktas realus operatoriaus loop testas. |
