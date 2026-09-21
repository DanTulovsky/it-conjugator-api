# it-conjugator-api

A lightweight, high-performance, 100% offline API and SQLite database service for
Italian: full verb conjugations **and** a complete dictionary lookup.

This project replaces live web scrapers (which are susceptible to Cloudflare/WAF IP blocks on VPS environments) and offline machine-learning packages (which often fail to conjugate reflexive and pronominal verbs accurately).

## Features

- **100% Offline**: Serves everything from local, pre-compiled SQLite databases in `data/` (`verbs.db`, `dictionary.db`).
- **Complete Verb Coverage**: Contains **48,689 main verbs** and **408,031 reverse lookup forms**.
- **Full Dictionary Lookup**: Every part of speech from the Italian Wiktionary dump — **622,957 entries** with senses/glosses, usage tags, IPA, hyphenation, etymology and inflections (`/define`).
- **Reverse Lookup Support**: Automatically resolves inflected/conjugated forms (e.g. `dico`) back to their root infinitives (`dire`).
- **Pronominal & Reflexive Verbs**: Accurately handles reflexive verbs (e.g. `arrabbiarsi`), double clitics (e.g. `tirarsela`), and compound pronominal verbs (e.g. `mettercela`) with proper clitic contraction (e.g. *ce l'ho messa*) and past participle agreement.
- **Accent Cleaning**: Helper accents used for pronunciation guides in dictionaries are automatically stripped for clean standard output.
- **Compact Storage**: Both databases store their payloads in `zlib`-compressed `BLOB` columns.

## Data layout

Everything binary lives under `data/` and is **not stored in git** — it is built
locally (see below):

```
data/
  verbs.db                          # conjugation database   (built by build_db.py)
  dictionary.db                     # full dictionary         (built by build_dictionary_db.py)
  kaikki.org-dictionary-Italian.jsonl   # source dump (~761 MB, downloaded on first build)
```

## API Setup

### Requirements
- Python 3.10+
- FastAPI
- Uvicorn
- [Task](https://taskfile.dev) (optional, for the `task` shortcuts)

Install dependencies:
```bash
pip install -r requirements.txt
```

### Build the databases

Both databases are built from the [Kaikki](https://kaikki.org/dictionary/rawdata.html)
Italian Wiktionary dump. The first build downloads the dump (~761 MB) into
`data/` if it is not already there; subsequent builds reuse it.

```bash
task db            # build both verbs.db and dictionary.db
# or without Task:
python3 build_db.py             # -> data/verbs.db
python3 build_dictionary_db.py  # -> data/dictionary.db
```

The `task db*` targets skip rebuilding when their output is already newer than
the build script; pass `--force` (e.g. `task db --force`) to rebuild anyway, for
instance after the source dump is refreshed.

The API **hard-fails on startup** if either database is missing, printing the
exact command needed to build it.

Set the required API key environment variable:
* **Windows (PowerShell)**:
  ```powershell
  $env:SCRAPER_API_KEY="secret_auth"
  ```
* **Windows (CMD)**:
  ```cmd
  set SCRAPER_API_KEY=secret_auth
  ```
* **Linux / macOS**:
  ```bash
  export SCRAPER_API_KEY="secret_auth"
  ```

Run the service:
```bash
python -m uvicorn app.api:app --host 0.0.0.0 --port 8000
# or: task run   (builds + starts via docker compose)
```

### Docker
Build the databases first, then run with Docker:
```bash
task db
docker build -t it-conjugator-api .
docker run -p 8000:8000 -e SCRAPER_API_KEY=secret_auth it-conjugator-api
```

### Tests
The data is covered by a regression suite (stdlib `unittest`, no extra deps) that
checks the conjugation tables (`tests/test_conjugations.py`) and the dictionary
lookup, curation and startup checks (`tests/test_dictionary.py`). It needs the
databases built first:

```bash
task test
# or:
python3 -m unittest discover -s tests -v
```

---

## Using the API (clients)

Everything below is what a **client** needs. Server setup is in *API Setup* above.

### Base URL and authentication

The server has no path prefix — endpoints live at the root (e.g. `http://localhost:8000`).
Every endpoint **except `/health`** requires the API key in the `X-API-Key` header:

```http
GET /define?v=casa HTTP/1.1
Host: localhost:8000
X-API-Key: secret_auth
```

A missing or wrong key returns `401`:

```json
{ "detail": "Invalid or missing X-API-Key" }
```

The key is the server's `SCRAPER_API_KEY`. Keep it server-side; there are no
per-client keys, CORS headers, or rate limits — put your own gateway in front if
you expose this publicly.

### Endpoints at a glance

| Method | Path              | Auth | Purpose                                    |
|--------|-------------------|------|--------------------------------------------|
| GET    | `/health`         | no   | Liveness + which databases are loaded      |
| GET    | `/conjugate?v=`   | yes  | Conjugation table for a verb or inflected form |
| GET    | `/define?v=`      | yes  | Full dictionary entry for a word           |

Both data endpoints return **HTTP 200** for a well-formed request even when the
result is empty — a not-found word is **not** a 404. Branch on the `success`
field (see [Error handling](#error-handling)).

### `GET /conjugate`

Conjugates an Italian verb. `v` accepts an infinitive (`mangiare`) **or** an
inflected / pronominal form (`dico`, `mi arrabbio`), which is resolved back to
its infinitive.

| Query param | Type   | Default | Notes |
|-------------|--------|---------|-------|
| `v`         | string | —       | **required**; Italian verb or form |
| `full`      | bool   | `true`  | `true` returns the whole table and ignores the filters |
| `moods`     | CSV    | all     | `indicativo`, `tempi composti`, `congiuntivo`, `condizionale`, `imperativo` |
| `tenses`    | CSV    | all     | `presente`, `imperfetto`, `passato remoto`, `futuro semplice`, `passato prossimo`, `trapassato prossimo`, `trapassato remoto`, `futuro anteriore`, `passato`, `trapassato` |
| `persons`   | CSV    | all     | `io`, `tu`, `lui, lei, Lei, egli`, `noi`, `voi`, `loro, Loro, essi` (imperative uses `(tu)`, `(Lei)`, `(noi)`, `(voi)`, `(Loro)`) |

Use `full=false` with `moods`/`tenses`/`persons` to keep the payload small — the
full table for one verb is a few KB. An invalid filter value returns **`400`**.

```bash
API=http://localhost:8000
KEY=secret_auth

# Full table
curl -H "X-API-Key: $KEY" "$API/conjugate?v=mangiare"

# Only the present indicative
curl -H "X-API-Key: $KEY" "$API/conjugate?v=mangiare&full=false&moods=indicativo&tenses=presente"
```

```json
{
  "success": true,
  "requested": { "v": "mangiare", "full": false, "moods": ["indicativo"], "tenses": ["presente"], "persons": null },
  "note": null,
  "error": null,
  "data": {
    "queried": "mangiare",
    "url": "https://www.wordreference.com/conj/itverbs.aspx?v=mangiare",
    "model": "mangiare",
    "principal_forms": {
      "infinito": "mangiare",
      "gerundio": "mangiando",
      "participio passato": "mangiato",
      "participio presente": "mangiante"
    },
    "auxiliary": "avere",
    "conjugations": {
      "indicativo": {
        "presente": {
          "io": "mangio",
          "tu": "mangi",
          "lui, lei, Lei, egli": "mangia",
          "noi": "mangiamo",
          "voi": "mangiate",
          "loro, Loro, essi": "mangiano"
        }
      }
    }
  }
}
```

`data.conjugations` is nested `mood → tense → person → form`. `data.queried` tells
you which infinitive an inflected form resolved to, so a client can send raw user
input:

```jsonc
// GET /conjugate?v=dico  ->  { "queried": "dire", "auxiliary": "avere", ... }
```

### `GET /define`

| Query param | Type   | Notes |
|-------------|--------|-------|
| `v`         | string | **required**; the word to look up |

Returns every entry for the word across **all parts of speech**. Matching is exact
first, then case-insensitive. Entries are sorted lemma-first (`is_lemma: true`).

| Field | Meaning |
|-------|---------|
| `word`, `pos` | headword and part of speech (`noun`, `verb`, `adj`, `adv`, …) |
| `is_lemma` | `true` when the entry has its own definition, `false` for pure inflections |
| `head` | human-readable head line (gender, plural, diminutives…) |
| `etymology` | prose etymology, when available |
| `ipa`, `rhymes`, `hyphenation` | pronunciation data (arrays) |
| `senses[]` | `glosses`, `raw_glosses`, `tags`, `form_of`, `examples`, `synonyms`, `antonyms` |
| `forms[]` | inflections (`form`, `tags`) — e.g. a verb's whole conjugation |
| `related`, `derived`, `translations`, … | extra Wiktionary links, passed through as-is |

```bash
curl -H "X-API-Key: $KEY" "$API/define?v=casa"
```

```json
{
  "success": true,
  "requested": "casa",
  "data": {
    "queried": "casa",
    "entries": [
      {
        "word": "casa",
        "pos": "noun",
        "etymology_number": null,
        "is_lemma": true,
        "head": ["casa f (plural case, diminutive casìna or casétta …, augmentative casóna …)"],
        "etymology": "Inherited from Latin casa (“house”).",
        "ipa": ["/ˈka.sa/", "/ˈka.za/"],
        "rhymes": ["-asa", "-aza"],
        "hyphenation": ["cà‧sa", "cà‧sa"],
        "senses": [
          {
            "glosses": ["house"],
            "tags": ["feminine"],
            "synonyms": [{ "word": "abitazione" }, { "word": "dimora" }]
          }
        ]
      }
    ]
  }
}
```

An inflected word resolves to its own entry, which links back to the lemma via
`senses[].form_of` — so `case` gives `{"glosses": ["plural of casa"], "form_of": [{"word": "casa"}]}`.

> **Payload size.** `/define` has no filters; entries vary from a few hundred bytes
> to a few KB (a verb's `forms[]` lists every conjugation). If you only need
> conjugations, prefer `/conjugate`.

### Error handling

| Situation | HTTP | Body |
|-----------|------|------|
| Missing / wrong `X-API-Key` | `401` | `{"detail": "Invalid or missing X-API-Key"}` |
| Invalid `/conjugate` filter value | `400` | `{"success": false, "error": "…"}` |
| Verb not found | `200` | `{"success": false, "error": "Verb not found in offline database"}` |
| Word not found | `200` | `{"success": false, "error": "Word not found in offline dictionary"}` |
| Missing required `v` | `422` | FastAPI validation error |

**Always check `success` rather than relying on the status code** for a lookup that
found nothing. `error` is human-readable; `success: false` is the machine signal.

### Client examples

Python (`requests`):

```python
import requests

BASE, KEY = "http://localhost:8000", "secret_auth"
headers = {"X-API-Key": KEY}

conj = requests.get(f"{BASE}/conjugate", params={"v": "mangiare"},
                    headers=headers, timeout=10).json()
if conj["success"]:
    print(conj["data"]["conjugations"]["indicativo"]["presente"]["io"])  # mangio

entry = requests.get(f"{BASE}/define", params={"v": "casa"},
                     headers=headers, timeout=10).json()
if entry["success"]:
    for e in entry["data"]["entries"]:
        print(e["pos"], e["senses"][0]["glosses"])
```

JavaScript (`fetch`):

```javascript
const BASE = "http://localhost:8000";
const headers = { "X-API-Key": "secret_auth" };

const res = await fetch(`${BASE}/define?v=casa`, { headers });
const body = await res.json();
if (res.status === 401) throw new Error("bad API key");
if (body.success) console.log(body.data.entries[0].senses[0].glosses);
```

### OpenAPI contract

`swagger.json` (OpenAPI 3.1) is importable by clients and code generators, and the
running server serves the same document plus interactive docs:

- `GET /openapi.json` — machine-readable contract
- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc

The served document is always **generated live from the running app** (it cannot
drift from the code). `swagger.json` is a checked-in snapshot; regenerate it with
`task swagger` after changing routes or models, or startup will fail fast.

---

## Data Attribution & Credits

The underlying conjugation tables, forms, and dictionary mappings in this service are parsed and built from the machine-readable Italian dictionary dumps provided by **[Kaikki.org](https://kaikki.org/)**, which are extracted from **Wiktionary** using the `wiktextract` tool.

### Academic Citation
If you use this dataset or API in academic research, please cite the creator of the extraction tool:

> Tatu Ylonen: *Wiktextract: Wiktionary as Machine-Readable Structured Data*, Proceedings of the 13th Conference on Language Resources and Evaluation (LREC), pp. 1317-1325, Marseille, 20-25 June 2022.
