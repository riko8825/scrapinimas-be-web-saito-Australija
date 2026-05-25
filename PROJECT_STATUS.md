# PROJECT_STATUS — ABR Outreach Pipeline

**Last updated:** 2026-05-25 (sesija #6)
**Phase:** Phase 7 real-data validation — parser + DNS DONE, social discovery strategy pivoted to A→B→C. **Plan A code ready (enrich_abr.py)**, awaiting live GUID smoke. + Phase 9 Dashboard.

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
| 3c | Plan A — ABR Lookup enrichment | [enrich_abr.py](enrich_abr.py) | **In Build** | Sesija #6: kodas paruoštas (async httpx + JSONP unwrap + tenacity retry + resumable + trading_name picker heuristika). Sanity tests 3/3 PASS (compile + --help + inline JSONP/picker/error envelope). Live smoke laukia vartotojo GUID registracijos per https://abr.business.gov.au/Tools/WebServicesAgreement. |
| 4 | Outreach generator | [generate_outreach.py](generate_outreach.py) | **Tested** | 27 templates su import-time assert'ais, end-to-end test'as ant synthetic dataset pereina |
| 5 | Orchestrator | [run.py](run.py) | **Tested** | Pre-flight, --step/--test/--state/--gst-status/--resume, Telegram, summary table; parse + DNS stage'ai Production-validated, social/messages priklauso nuo API |
| 6 | Unit test suite | [test_pipeline.py](test_pipeline.py) | **Production** | 46/46 PASS; tikrina visus 4 core helpers + mocked DNS |
| 7 | Dashboard (outreach tracking) | [dashboard/app.py](dashboard/app.py) | **Tested** | Streamlit, 5 tabai (Overview/Leads/Analytics/Activity/Settings), SQLite [dashboard/db.py](dashboard/db.py), CSV importer su industry auto-detect, LT/EN i18n. Paleistas — HTTP 200, 159,070 leads loaded. UI interaktyvūs flow (mark sent, edit detail, audit log) **nepatvirtinti naršyklėje**. |

**Pabaigtumas:** (1.0 + 1.0 + 0.7 + 0.3 + 0.3 + 0.7 + 0.7 + 1.0 + 0.7) / 9 × 100% = **74%**

> Sesija #6 (2026-05-25): pridėtas naujas modulis #3c "Plan A — ABR Lookup enrichment" In Build (kodas paruoštas, smoke laukia GUID). Denominator padidėjo 8 → 9, bendras pabaigtumas matematiškai krito 77% → 74%, BET reali progress'as pozityvi — paskutinis blocker'is Plan A startui (kodas neegzistuoja) išspręstas; liko tik external dependency (GUID email).
>
> Sesija #5 (2026-05-25): find_social.py In Build → Tested (+10pp recall pipeline savo kontekste), bet pridėjome naują modulį #3b "Social discovery pivoted strategy" In Build (architecture only) į denominator. Bendras pabaigtumas išliko 77%. Tikras "value delivered" — Brave kelio limitai apnuoginti su real-world skaičiais; nustatytas profesionalus alternatyvus kelias (A→B→C) su konkrečiomis sąnaudomis ir laiko prognozėmis.

## Iki 100% trūksta

1. **Plan A — ABR Lookup API trading_name enrichment** (sesija #6): naujas `enrich_abr.py`, async 5 req/s, +`trading_name` stulpelis prie `filtered_with_dns.csv`. Re-run find_social.py su trading_name. Target: Brave recall 5% → 15-20%.
2. **Plan B — Google Places smoke test 1000 leads** (sesija #7, jei A pasiekia ≥15%): GCP setup + `enrich_places.py` + 1000-row sample (~$30). GO/NO-GO decision dėl Plan C.
3. **Plan C — Pilnas hybrid pipeline** (sesijos #8-16, jei B patvirtina ekonomika): Places + Apify FB/IG + Claude vision verification. ~$4,700 mass run cost, ~6 savaitės kalendoriaus.
4. **Dashboard UI loop patikrinimas** — Tested → Production: paleisti dashboard, naršyklėje pereiti per realų scenarijų (pasirinkti lead'ą, pažymėti "sent" su channel + note, patikrinti detail panel save, audit log entry).
5. **Outreach generator + orchestrator end-to-end** — Tested → Production: visi 4 stage'ai turi pereiti pilną pipeline ant tikrų duomenų (`--step all`).
6. **importer.py bug fix** (carry-over sesija #5): `--csv has_social.csv` perrašo `business_name=""` per `upsert_leads()`. Fix: route social-schema CSV į `import_socials_if_present()` only.
7. **(Optional) DNS recheck path** — ~10% false-positive cleanup `no_website.csv`. DoH path'as throttle-limited, reikia kitos taktikos.

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
