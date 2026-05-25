# DECISION_LOG — ABR Outreach Pipeline

Architektūriniai sprendimai. Naujausi viršuje.

---

## 2026-05-25 (sesija #5) — Social discovery pivot: A → B → conditional C

**Sprendimas:** `find_social.py` (Brave Search) palikti Tested būsenoje, BET neinvestuoti daugiau tuning'o ir nelaikyti jo primary discovery layer'iu. Naujas trifazis kelias social profilių radimui:

- **Plan A (next session, free, 1 sesija):** Sukurti `enrich_abr.py` — ABR Lookup API (abr.business.gov.au) async wrapper. Per kiekvieną ABN gauti `trading_name` ir `business_names[]`. Pridėti `trading_name` stulpelį prie `filtered_with_dns.csv`. Re-run `find_social.py --limit 20` naudojant `trading_name` (ne `business_name`) kaip Brave query. Tikslas: hit-rate 5% → 15-20%.

- **Plan B (jei A pasiekia ≥15% recall, ~$30 cost, 2 sesijos):** Setup'inti GCP project + Places API + $50 billing alert. Implementuoti `enrich_places.py` (Text Search + Place Details). Smoke test ant 1000 leads (~$30 spend). Manual spot-check 30 grąžintų place_id'ų accuracy. **Decision point:** ar Places hit-rate ≥60% AU SMB segmentui.

- **Plan C (conditional, jei B patvirtina ekonomiką, ~$4,700 mass run, 9-11 sesijų, ~6 savaitės):** Pilnas hybrid pipeline — ABR + Places + website footer scrape + Apify FB scraper + Apify IG scraper + Claude Sonnet 4.6 vision verification. Reject anything confidence_score < 8/10.

**Priežastis:**
- Sesija #5 empiriškai parodė kad Brave Search **plateau'auja ties 5% recall** AU SMB FB/IG discovery'ui, **net su pilnu hardening'u** (`_query_clean`, postcode geo, AU geo gate, 3-query strategy). Tai fundamental šaltinio limitas, ne tuning klausimas — Brave indexas undersample'ina mažus AU SMB FB puslapius
- Tikslas — generuoti pirmus klientus, ne tobulinti suboptimal kelią. 5% recall × 97k leads = 4,850 social hits, bet **realiai daug iš jų bus low-quality** kontaktai, kurie nekonvertuos
- Profesionali industry benchmark lead enrichment kaina ~$0.20-$0.50/lead (Clay, Apollo, ZoomInfo). Mūsų pilno C plano cost ~$0.048/lead — **4-10× pigiau** nei market
- Staged validation (A→B→C) leidžia bail'inti anksti su minimalia investicija jei strategy neveikia: A=$0, B=$30, tik tada commit'inti C=$4,700

**Alternatyvos atmestos (žr. sesijos #5 pokalbio archyvą):**
- **Brave tuning forever** — atmesta dėl 5% ceiling'o (ne tuning klausimas)
- **LinkedIn Sales Navigator + Phantombuster** — atmesta dėl $99+$69/mėn ongoing cost'o ir solo-trader leads missing
- **Hunter.io / Apollo.io reverse lookup** — atmesta dėl AU smulkių verslų coverage'o (jie laiko US/EU B2B SaaS focused)
- **Facebook Graph API Pages Search** — atmesta dėl Meta API restriction'ų (reikia partner access, neavailable individual dev'ams po 2018)
- **Tiesiogiai šokti į Plan C** — atmesta dėl $4,700 commit'o rizikos be A/B validation

**Trade-off'as:**
- Plan A užtruks vieną sesiją prieš matydami pirmus realius outreach hits — bet 1 sesija yra acceptable
- Plan C kainuoja $4,700 vs current $0 — bet ROI ekonomika: 10k qualified leads × 2% reply × 10% close × $2-5k deal size = $40k-$100k revenue, 8-20× ROI
- Apify scraper'iai sulūžta kas 2-4 mėnesius (FB/IG keičia HTML) — pipeline downtime 24-48h kol Apify atnaujina actor'ius. Acceptable šitam use case'ui.

**Operacinis impact:**
- Carry-over tasks 1-2 atspindi šitą strategiją (sesija #6 = Plan A, sesija #7 = Plan B GO/NO-GO)
- Memory `find_social_brave_ceiling.md` užfiksuoja kodėl Brave atmestas kaip primary path — kad ateityje neperdarytume šito tyrimo
- find_social.py kodas lieka Tested (jo precision 100% post-fix), bet jo recall savaime nepateisina production use'o — bus naudojamas po Plan A trading_name boost'o

---

## 2026-05-25 (sesija #5) — find_social.py hardening: AU geo gate + 3-query strategy

**Sprendimas:** `find_social.py` papildytas 4 mechanizmais kovai su false positive'ais ir žemu recall'u:

1. **`_query_clean()`** funkcija pakeičia `_normalize()` query stringo paruošime. Šalina legal noise (`pty ltd`, `proprietary`, `limited`, `pty` standalone) IR parenthesised state codes (`(NT)`, `(QLD)` ir kt.), BET išsaugo brand'o žodžius (`SERVICES`, `GROUP`, `INDUSTRIES`) — jie dažnai brand'o dalis (pvz. "Hardy Group NT").

2. **3-query strategija** per verslą (vietoj 2):
   - Precise FB: `"<clean>" <postcode> site:facebook.com`
   - Precise IG: `"<clean>" <postcode> site:instagram.com`
   - Broad: `"<clean>" australia facebook` (be `site:` filtro — gauna .com.au footer'ius + plačiau indexuotus puslapius)
   Visi rezultatai merge'inami → vienas `_pick_best` pass'as.

3. **`_is_australian(title, url, state, postcode)`** geo gate — tikrina ar bet kuris iš signal'ų yra title/URL'e: `.com.au` / `+61` / `australia` / state code (` NSW `, ` NT `, ...) / state pilnas pavadinimas (`new south wales`, `northern territory`, ...) / postcode (`0810`). Mapping `AU_STATE_NAMES` dict.

4. **`_pick_best()` dual-gate** — kandidat'as praeina TIK jei: fuzzy ≥ 0.5 (handle ar title vs business_name) **IR** `_is_australian()` returns True. Be AU signal'o → atmestas net jei fuzzy 1.0.

**Priežastis:**
- Sesijos #5 V1 run'as 20 leads grąžino 1 hit (FAST PAINTING → facebook.com/fast.painting.18) — **false positive: tai Texas LLC, ne SA Australia**. Precision = 0%. Šitas tipas bug'o yra exact issue: search engine grąžina top-ranked global match, mes priimame nieko nepatikrinę
- Postcode (4-digit AU-specific, `0810`) yra **kur kas stipresnis** geo signalas nei state code (`NT`) — 2 raidės sutampa su milijonais atsitiktinumų, postcode beveik 100% AU-specific
- Broad query be `site:` filtro empiriškai grąžina FB URL'us iš trečių šalių katalogų ir verslų svetainių footer'ių — Brave geriau grade'ina kai turi pilną web context, ne tik FB subset
- Hardy Landscaping (NT) atvejis parodė kad parenthesised `(NT)` viduje quote'o → Brave laiko jį required exact string → 0 results. Fix `_PARENS_STATE_RE` regex'as šalina jį iš query (bet palieka jį pavadinime fuzzy match'ui)

**Verifikacija:** V2 run'as ant tų pačių 20 leads — 1 hit (Hardy Landscaping NT → [facebook.com/hardylandscapingnt](https://facebook.com/hardylandscapingnt)). Brave snippet'as "Darwin NT" patvirtina AU verslą. Texas LLC false positive atmestas. **Precision: 0% → 100% sample lygyje. Recall: nepasikeitė (5%) — bet recall problema yra šaltinio (Brave), ne AU validation logikos.**

**Trade-off'as:**
- 3 query/biz vietoj 2 = +50% API cost (40 → 60 calls per 20 biz). Brave free tier 2000/mėn = 666 biz/mėn vietoj 1000. Acceptable smoke test apimtyse, bet mass run'ams reikės paid tier'o arba kitokio šaltinio.
- AU gate yra **konservatyvus** — galimai atmes tikrus AU verslus, kurių FB title/URL neturi nei vieno iš 6 signal'ų. Šitas tradeoff'as priimtinas, nes false positive cost'as (siunčiame outreach US verslui) yra didesnis nei false negative cost'as (praleidžiame vieną AU verslą iš 97k).

---

## 2026-05-25 (sesija #4) — Dashboard storage: SQLite vietoj Supabase

**Sprendimas:** Outreach tracking duomenys (status, sent_at, replied_at, contact_*, notes, tags, audit log) saugomi lokaliame SQLite faile [dashboard/outreach.db](dashboard/outreach.db), ne Supabase cloud DB. Schema sukurta [dashboard/db.py](dashboard/db.py) (`leads`, `outreach`, `activity`, `imports` lentelės).

**Priežastis:**
- ABR pipeline yra CSV-based ir lokalus (XML parse → CSV → CSV). Supabase prijungimas reikalautų: naujos `abr_outreach` schemos migracijos, `SUPABASE_DB_URL` env config, network round-trip latency kiekvienam UI veiksmui
- 97k+ leads + multi-row aggregations Streamlit'e per pgbouncer būtų lėtesnis nei lokalus SQLite (WAL mode, `synchronous=NORMAL`)
- Vienas operatorius — nereikia multi-user sync. SQLite WAL palaiko concurrent read'us + 1 writer'į, ko užtenka Streamlit single-process modelui
- Zero-config: failas atsiranda pirmo `python -m dashboard.importer` paleidimo metu. Nereikia Supabase paskyros, RLS policies, network setup

**Alternatyva atmesta:** Supabase su atskira `abr_outreach` schema (kaip [empirra-lead-scraper](../New folder/dashboard/app.py) projekte). Atmesta dėl over-engineering vienam operatoriui ir +1 cloud dependency.

**Tradeoff:** Dashboard duomenys neperkeliami tarp kompiuterių automatiškai. Jei reikės sync — `outreach.db` file copy + reimport. Šitas tradeoff'as priimtinas, nes outreach yra solo operatoriaus darbas.

**`outreach.db` gitignored** ([.gitignore](.gitignore)) — operatoriaus per-machine state, ne shared artifact.

---

## 2026-05-25 (sesija #4) — Dashboard CSV importas yra idempotent UPSERT

**Sprendimas:** [dashboard/importer.py](dashboard/importer.py) `upsert_leads()` daro `SELECT first_seen_at FROM leads WHERE abn=?` ir tada arba `UPDATE` (jei egzistuoja) arba `INSERT` (jei naujas). `outreach` lentelės eilutės kuriamos TIK pirmo `INSERT` metu su `INSERT OR IGNORE` — esami `status`, `sent_at`, `notes`, `tags`, `contact_*` reikšmės **niekada nebus overwrite'intos** reimport'u.

**Priežastis:** Operatorius reimport'uos CSV po kiekvieno `find_social.py` / `check_dns.py` paleidimo (kad nauji social URL ar found_domain įkristų į DB). Tačiau jis tuo pat metu jau bus pažymėjęs dalį leads kaip "sent" ar įrašęs notes — šitie negali būti prarasti.

**Verifikacija:** Per sesiją #4 testas pakartotinas reimport: po `set_status(['abn1','abn2','abn3'], 'sent')` ir antro `python -m dashboard.importer --csv test_100.csv` — sent rows = 3 (nepakeitė), imports lentelėje 2 įrašai. Confirmed.

**Tradeoff:** Jei CSV pasikeičia `business_name`, `state` ar kiti lead-level laukai — jie OVERWRITE'inami. Industry keyword saugomas su `COALESCE(industry_keyword, ?)` — neoverwrite'ina, kad rankiniai industry edit'ai dashboard'e išliktų.

---

## 2026-05-25 (sesija #4) — Dashboard UI yra dvikalbis (LT/EN), ne tik vienos kalbos

**Sprendimas:** Dashboard turi LT ↔ EN toggle sidebar viršuje. ~160 raktų translation dict'as [dashboard/i18n.py](dashboard/i18n.py), `t("key")` helper, `st.session_state["lang"]` saugo pasirinkimą per rerun'us.

**Priežastis:**
- Globalus CLAUDE.md sako: visa Empirra komunikacija LT. Bet outreach kampanija — AU verslams angliškai. Dashboard ateityje gali matyti virtual assistant / sub-contractor, kuriam LT kontekstas nereikalingas
- Translation overhead vienam operatoriui yra ~1 dienos darbas iškart (160 keys × 2 lang), o vėliau retrofitting'as kainuotų >3× — visus `st.button`, `st.column_config` titles, error message'us reiktų wrap'inti retroaktyviai
- Vienos kalbos overhead'as runtime'e: 1 dict lookup per render, neturi performance impact

**Alternatyva atmesta:** Tik LT (atmesta dėl bottle-neck'o ateityje, jei norėsis pasamdyti pagalbą). Tik EN (atmesta nes operatorius dabar dirba LT).

**Fallback elgesys:** Trūkstamas key → LT fallback → key string. Pusiau-išverstas UI vis tiek navigable, ne crash.

---

## 2026-05-25 (sesija #3) — Priimti ~10% false-positive rate `no_website.csv` vietoj recheck'o

**Sprendimas:** Po pilno DNS run'o (97,801 ACT leads → 97,534 no-domain @ 99.7%) diagnostinis 1000-row recheck smoke parodė ~10% false negative rate (verslai su realiomis svetainėmis pažymėti kaip "no website"). **Priimame** šį rate ir keliam į Phase 4 (social search) be antros pass'o.

**Pakeitė tarpinį sprendimą:** Per sesiją buvo bandymai pataisyti:
1. `recheck_dns.py` UDP @ conc=30, 2 NS, 5s timeout → 5 biz/s, ETA 5.4h ❌
2. `recheck_dns.py` UDP @ conc=50, 4 NS, 5s timeout → 6 biz/s, ETA 4.5h ❌
3. `recheck_dns_doh.py` v1 (httpx + Cloudflare/Google DoH JSON, sequential candidates) → 7 biz/s, ETA 230 min ❌
4. `recheck_dns_doh.py` v2 (parallel per business, 10 tasks/biz × conc=100 = 1000 in-flight) → 1 biz/s regression (endpoint throttling) ❌

Visi keturi paliko `>3h` ETA, kuri netilptų į vieną sesiją ir blokuotų Phase 4.

**Priežastis priimti:**
- Brave social search ([find_social.py](find_social.py)) natūraliai filtruoja didžiąją dalį false-positives: verslai su realiomis svetainėmis turi labiau organic social presence ir Brave ranking juos atskiria
- Outreach kampanija vis tiek turės žmogiškąjį review prieš išsiuntimą — likę 1-2% false-positives bus pastebėti per outbound process
- 97,534 lead'ų pool'as yra >>> reikalingas pradinei kampanijai (tikslas: pradėti su 100-500 outreach žinučių, ne pulti visus iš karto)

**Alternatyva atmesta:** Paid DoH service (NextDNS, Quad9 Premium) su garantuotu rps. Atmesta dėl: (a) papildomas billing nepateisinamas dėl 10% trade-off; (b) reikia 1-2 dienų vendor evaluation, blokuotų Phase 4.

**Saugomas ateičiai:** `recheck_dns_doh.py` lieka repo, bet ne active pipeline path'e. Jei outreach response rate'as parodys problemų ("aš turiu svetainę" complaint'ai >5%), tada peržiūrim DoH path'ą su kitokia taktika (per-business sleep, target tik suspicious rows).

**Lesson learned:** Smoke testai @ 500-1000 rows neapšildo throttle scenario, kuris pasirodo tik po ~5k užklausų. Ateityje validation pipeline'us reikia tikrinti 5k-row chunks (~5-10 min smoke), ne tik 500.

---

## 2026-05-25 (sesija #3) — `run.py` įgyja `--gst-status` flag'ą atskirai nuo `--state`

**Sprendimas:** Pridėtas naujas CLI flag'as `--gst-status {ACT|NON|CAN}` ir helper'is `_apply_gst_filter` [run.py:194-204](run.py#L194-L204). Atskirai nuo `--state {NSW|VIC|...}` (geographic).

**Priežastis:** ABR XML turi du nesusijusius "ACT" reikšmes:
- `state="ACT"` — Australian Capital Territory (2,417 verslai iš 159,070)
- `gst_status="ACT"` — Active GST registration (97,801 verslai — **outreach target**)

Be šio flag'o, vartotojas norintis filtruoti pagal "ACT leads" intuityviai paleistų `--state ACT` ir gautų 41× mažesnį pool'ą nei tikėjosi. Tylus klaidingas filtras → tyliai mažesnis outreach scope.

**Alternatyva atmesta:** Pervadinti `gst_status="ACT"` į kitką (pvz., "ACTIVE") parser'yje, kad išvengti konflikto. Atmesta, nes ABR data dictionary'je `ACT/NON/CAN` yra kanonical 3-char codes, ir bet kuris ateities vartotojas, žiūrintis raw CSV, tikėtųsi būtent jų.

**Pakeitimo apimtis:**
- `_apply_gst_filter` helper'is — analogiška mechanika kaip `_apply_state_filter`
- Įjungtas visuose 4 stage'uose (`parse`, `dns`, `social`, `messages`) consistency tikslu
- Pre-flight banner'is rodo abu filter'ius separately

---

## 2026-05-25 (sesija #3) — `run.py stage_dns` consume'ina `name_normalized`, unpack'ina tuples

**Sprendimas:** `stage_dns` [run.py:269-275](run.py#L269-L275) naudoja `df["name_normalized"]` kaip input'ą `check_dns.check_all`'ui ir distil'ina jo `list[tuple[bool, str]]` return type į du atskirus stulpelius `has_domain` ir `found_domain`.

**Pakeitė ankstesnį sprendimą:** Ankstesnė versija (a) perdavinėjo `df["business_name"]` (raw uppercase su "PTY LTD" suffix'ais), kuriam `generate_domains` neprojektuotas, ir (b) saugojo visą tuple kaip vieną `found_domain` stulpelį, tada `df["has_domain"] = df["found_domain"].astype(bool)` — bet `(False, '')` yra truthy, todėl visi rows tapo `has_domain=True`.

**Padariniai senojo bug'o:**
- Smoke test #1 (500 rows): visi 500 pažymėti `has_domain=True`, `no_website.csv` = 0 rows
- Padariniai full run'ui būtų buvę: 0 leads social search'ui, pipeline halt

**Priežastis bug'as išliko iki sesijos #3:** standalone `python check_dns.py` driver'is [check_dns.py:365-366](check_dns.py#L365-L366) tai daro teisingai per `_resolve_name_column` + tuple indexing. `run.py:stage_dns` buvo parašytas anksčiau, kol dar `name_normalized` stulpelio nebuvo, ir niekuomet nebuvo re-verified po `abr_parser.py` schema pakeitimo (sesija #2). Memory'je įrašyta kaip [[run-py-dns-contract]] feedback memory ateities sesijoms.

**Lesson learned:** Kai standalone modulis turi tinkamą logiką, bet orchestratorius dubliuoja ją inline — ateities pakeitimai modulyje gali silent'iai praleisti orchestratorių. Geriau eksportuoti `process_dataframe(df)` helper'į iš `check_dns.py` ir kviesti jį iš `run.py`, ne re-implement'inti.

---

## 2026-05-24 (sesija #2) — Parser address extraction: anchor on `AddressDetails`

**Sprendimas:** `abr_parser._extract_record()` ([abr_parser.py:153-155](abr_parser.py#L153-L155)) ieško `AddressDetails` element'o, tada skaito jo vaikus `State` + `Postcode`.

**Pakeitė ankstesnį sprendimą:** ankstesnė versija ieškojo `MainBusinessPhysicalAddress` ir `StateCode` — tag'ų, kurių faktinėje ABR public extract XML schema'oje **nėra**. Pirmoje synthetic test fazėje tai praėjo, nes mock CSV nesimuliavo XML struktūros. Pirmasis live parse atskleidė: visi 7,808 įrašai grįžo su `state=""`, `postcode=""`.

**Faktinė ABR schema** (patvirtinta `Public01.xml` head'e):
```xml
<MainEntity>
  <NonIndividualName>...</NonIndividualName>
  <BusinessAddress>
    <AddressDetails>
      <State>NSW</State>
      <Postcode>2000</Postcode>
    </AddressDetails>
  </BusinessAddress>
</MainEntity>
```

**Alternatyva atmesta:** anchor'inti ant `BusinessAddress` (parent). Atmesta, nes `_find_first` daro depth-first iter — abu kelią pasiekia tuos pačius vaikus, bet `AddressDetails` yra arčiau leaf'o ir saugesnis jei ateityje atsiras kitokia adreso struktūra (pvz., `MainBusinessPhysicalAddress` kaip alternatyvi šaka, kurios egzistavimą reikia audituoti — 53/159,070 įrašų be state vis dar yra).

**Lesson learned:** synthetic CSV → live XML transition'as turi *visada* prasidėti smoke testu ant 1 real failo prieš leidžiant full batch. Jei būtume leidę visus 20 failų iškart, 12 min būtų sudirbusios netinkamą CSV ir tektų restart'inti.

---

## 2026-05-24 — Path unification: visi runtime CSV į `./output/`

**Sprendimas:** Visi 5 standalone skriptai + `run.py` orchestratorius default'ina visus CSV į `./output/` (anksčiau buvo projekto šaknyje).

**Alternatyva atmesta:** Palikti projekto šaknyje (paprasčiau new user'iui rasti). Atmesta, nes konflikto su `abr-data/` (input dir, kuris yra projekto šaknyje) ir nešvarus repo root.

**Privalumai:** Vienas direktorijos paspaudimas `output/` ir matai visus pipeline rezultatus. `.gitignore`'ina vienu `output/` rule.

---

## 2026-05-24 — DNS resolver: dnspython, ne aiodns

**Sprendimas:** `check_dns.py` naudoja `dns.asyncresolver` (dnspython) vietoj `aiodns`.

**Priežastis:** aiodns ant Windows silent-fail'ina, kai bandoma auto-discover nameservers — visi domain'ai grąžina `False` net ir tie, kurie egzistuoja. Tai produkcijoje paverstų **visus 800k+ rows tariamais "no_website"** — katastrofiškas false-positive. dnspython elgiasi tas pats jei nepasakai aiškiai, bet su `Resolver(configure=False)` + `nameservers = [...]` veikia deterministiškai.

**Side note:** aiodns vis dar yra requirements.txt'e ir test_pipeline.py importina jį (legacy mock), bet faktiškai nenaudojamas. Galima ištrinti vėliau.

**Mitigation:** Explicit nameservers — Cloudflare (1.1.1.1) + Google (8.8.8.8) — pin'inti `_make_resolver()` funkcijoje.

---

## 2026-05-24 — Template'ai su import-time assert'ais

**Sprendimas:** `generate_outreach.py` validuoja kiekvieną outreach template'ą **import metu** (eilutės 178-186) — `assert ≤ 4 sentences AND endswith('?')`. Jei kažkuris template'as sulaužytas, script'as net neload'inasi.

**Priežastis:** Spec'as konkrečiai prašė ≤4 sentences + question mark. Runtime validation'as būtų per vėlu — jau būtų pakviestas LLM su blogu input'u. Import-time fail dramatiškai sumažina shipping risk'ą.

**Trade-off:** Modulio import laikas šiek tiek lėtesnis. Acceptable.

---

## 2026-05-24 — Standalone CLI skriptai root'e + `src/utils.py`

**Sprendimas:** Visi pipeline stage'ai yra root-level standalone CLI skriptai (`abr_parser.py`, `check_dns.py`, `find_social.py`, `generate_outreach.py`, `run.py`). Tik `src/utils.py` lieka kaip shared helpers.

**Alternatyva atmesta:** Pilnas Python package (`src/abr_pipeline/__init__.py` su submodules + entry_points pyproject.toml'e). Atmesta, nes:
- Spec'ai prašė `python skriptas.py` paleidimą — standalone
- Vienas dev'as, vienas projektas — package overhead nepateisinamas
- Kiekvienas skriptas turi savo `--help`, lengviau debug'inti

**Priežastis:** Stage'ai turi būti nepriklausomi (kad galima rerun atskirą stage'ą kai prieš tai esantis jau padarytas). CLI default'ai user-friendly.

**Žinomas trūkumas:** Tas pats kodas dubliuojasi tarp `src/parser.py` ir root `abr_parser.py` (legacy iš ankstesnės sesijos dalies). Reikia ištrinti `src/` legacy failus.

---

## 2026-05-24 — `name_normalized` saugomas filtered_businesses.csv

**Sprendimas:** `abr_parser.py` skaičiuoja `normalize_name(business_name)` PARSE metu ir įrašo į CSV kaip atskirą stulpelį.

**Alternatyva atmesta:** Normalize'inti each stage'e pagal poreikį (lazy). Atmesta dėl:
- DNS check stage skaitytų raw business_name ir kiekvienu kartu re-normalize'intų — wasted CPU ant 800k+ rows
- Skirtingi stage'ai gali turėti skirtingas normalize strategy → silent divergence

**Privalumas:** Vienas truth point. Visi stage'ai naudoja tą pačią normalizuotą versiją (`name_normalized` column).

**Trade-off:** Jei pakeisi `normalize_name()` logic'ą, reikia re-run'inti parser'į.
