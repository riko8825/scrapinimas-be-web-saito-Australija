# CLAUDE.md — ABR Pipeline

Projekto kalba: **lietuvių**. Sistema: Windows 11 · Python 3.11+.

---

## PROJECT PURPOSE

Surasti **Australijos verslus, kurie NETURI svetainės** (iš ABR — Australian Business
Register — viešų duomenų), aptikti jų socialinių tinklų paskyras (Facebook, Instagram)
ir sugeneruoti **personalizuotus outreach pranešimus**.

End goal: CSV su lead'ais, paruoštais cold outreach kampanijai (Empirra paslaugos —
custom svetainė + AI automatizacijos Australijos service business operatoriams).

---

## STACK

- **Python 3.11+**
- **pandas** — duomenų manipuliacija, CSV I/O, grupavimas
- **asyncio** — concurrency visiems I/O bound žingsniams
- **httpx** — async HTTP klientas (DNS-over-HTTPS, social search, LLM API)
- **lxml** — streaming XML parsing su `iterparse` (ABR XML failai 600MB+)
- **tqdm** — progress bars
- **python-dotenv** — `.env` config loading
- **aiofiles** — async file I/O log'ams ir CSV chunk'ams
- **tenacity** — retry logic API call'ams (exponential backoff)

---

## DATA FLOW

```
abr-data/*.xml
        │
        │  parser.py  (lxml iterparse, stream)
        ▼
output/filtered_businesses.csv      ← visi ACT statuso verslai, kurie atitinka filtrus
        │
        │  dns_check.py  (async httpx + DoH)
        ▼
output/no_website.csv                ← TIK tie, kurių domain NEresolve'inasi
        │
        │  social.py  (async search, FB/IG extract)
        ▼
output/has_social.csv                ← TIK tie, kurie turi bent FB ARBA IG
        │
        │  messages.py  (Claude API, async, retry)
        ▼
output/outreach_ready.csv            ← FINAL: lead + asmeninis pranešimas
```

Kiekvienas žingsnis — atskiras CLI'ininkamas modulis. `pipeline.py` paleidžia visus iš eilės.

---

## TAISYKLĖS (privaloma laikytis)

### Failai ir keliai
- **Visi output failai** → `./output/` (niekur kitur)
- **Visi log failai** → `./logs/` (niekur kitur)
- **Niekada** necommit'inti `abr-data/`, `output/`, `logs/` (visi gitignored)

### Config
- **Visi parametrai** kraunami iš `.env` per `python-dotenv`
- **Niekada** nehardcode'inti API raktų, slenksčių, paths kode
- `.env.example` visada turi būti sinchroniškas su faktiškai naudojamais kintamaisiais

### HTTP
- **Visi HTTP request'ai async** — `await httpx.AsyncClient().get(...)`
- **Niekada** sync `requests` ar blocking I/O hot path'e
- Concurrency limit per `asyncio.Semaphore` (default 10)
- Retry su `tenacity` — 3 bandymai, exponential backoff (1s, 2s, 4s)

### XML
- **Visada** `lxml.etree.iterparse` su `event="end", tag="ABR"`
- **Niekada** `etree.parse(file)` ar `file.read()` — failas neprivalo tilpti į RAM
- Po kiekvieno `<ABR>` element'o — `element.clear()` + ištrinti previous sibling'us

### Progress
- **Kiekviena ilga operacija** (>5s arba >100 elementų) — `tqdm` progress bar
- Stulpelis `desc` privalo aiškiai pasakyti, kas vyksta (`"Parsing XML"`, `"DNS check"`)

### Logging
- **Kiekviena funkcija** turi docstring (vienos eilutės minimum, dažniausiai 3-5 eilutės)
- **Error log'ai** → `./logs/errors.log` (atskirai nuo info log'ų)
- Vienas record'as fail'ina → log'inam į `errors.log`, **NE crash'inam** pipeline
- Info log'ai → `./logs/pipeline_YYYYMMDD.log`
- Stdout — tik tqdm progress + final summary

### Klaidų valdymas
- Pipeline turi atlaikyti milijonų record'ų stream'ą
- Single record failure (blogas XML, DNS timeout, API 500) — log + skip, **NE raise**
- Sistemnė klaida (failas nerastas, blogi credential'ai) — fail-fast su aiškia žinute

---

## REŽIMAS IR DoD

**Automation code** — lokali Python CLI, jokios production deploy.

**Definition of Done:**
1. `python src/pipeline.py` pereina be uncaught exception
2. `output/outreach_ready.csv` turi >0 eilučių su pilnais stulpeliais
3. `logs/errors.log` peržiūrėtas — jokių sisteminių klaidų, tik per-record skip'ai
4. Random sample 10 outreach pranešimų — žmogiškai skambantys, ne template'as

---

## STRUKTŪRA

```
abr-pipeline/
├── CLAUDE.md
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── abr-data/                  ← input XML (gitignored)
├── output/                    ← visi CSV (gitignored)
├── logs/                      ← pipeline_YYYYMMDD.log + errors.log
└── src/
    ├── __init__.py
    ├── parser.py              XML → filtered_businesses.csv
    ├── dns_check.py           filtered → no_website.csv (async)
    ├── social.py              no_website → has_social.csv (async)
    ├── messages.py            has_social → outreach_ready.csv (Claude API)
    ├── utils.py               logging, config, semaphore helpers
    └── pipeline.py            orchestrator (visus 4 iš eilės)
```

---

## KONTROLĖ PRIEŠ COMMIT

- `python -m py_compile src/*.py` — sintaksė pereina
- `python src/pipeline.py --limit 100` — small batch run pereina švariai
- `logs/errors.log` per pastarąjį run'ą — peržiūrėtas
- `.env` **NĖRA** staged (`git status` patikrint)
