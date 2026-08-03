# it-conjugator-api

A lightweight, high-performance, 100% offline API and SQLite database service for Italian verb conjugations.

This project replaces live web scrapers (which are susceptible to Cloudflare/WAF IP blocks on VPS environments) and offline machine-learning packages (which often fail to conjugate reflexive and pronominal verbs accurately).

## Features

- **100% Offline**: Serves conjugations directly from a local, pre-compiled SQLite database (`verbs.db`).
- **Complete Verb Coverage**: Contains **48,689 main verbs** and **408,031 reverse lookup forms**.
- **Reverse Lookup Support**: Automatically resolves inflected/conjugated forms (e.g. `dico`) back to their root infinitives (`dire`).
- **Pronominal & Reflexive Verbs**: Accurately handles reflexive verbs (e.g. `arrabbiarsi`), double clitics (e.g. `tirarsela`), and compound pronominal verbs (e.g. `mettercela`) with proper clitic contraction (e.g. *ce l'ho messa*) and past participle agreement.
- **Accent Cleaning**: Helper accents used for pronunciation guides in dictionaries are automatically stripped for clean standard output.
- **Embedded Database**: The SQLite file is compressed with `zlib` columns, bringing it down to a compact ~87 MB file which is safely under GitHub's 100 MB limit.

## API Setup

### Requirements
- Python 3.10+
- FastAPI
- Uvicorn

Install dependencies:
```bash
pip install -r requirements.txt
```

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
```

Once running, query the API using standard HTTP tools (such as curl):
```bash
curl -H "X-API-Key: secret_auth" "http://localhost:8000/conjugate?v=mi+arrabbio"
```

### Docker
Alternatively, run with Docker:
```bash
docker build -t it-conjugator-api .
docker run -p 8000:8000 it-conjugator-api
```

### Tests
The conjugation data is covered by a regression suite (`tests/test_conjugations.py`)
that checks the auxiliary, past participle, and passato prossimo for ~120 verbs
against hard-coded expected values. Run it from the repo root (no dependencies,
uses the stdlib `unittest`):
```bash
python3 -m unittest discover -s tests -v
```

---

## Data Attribution & Credits

The underlying conjugation tables, forms, and dictionary mappings in this service are parsed and built from the machine-readable Italian dictionary dumps provided by **[Kaikki.org](https://kaikki.org/)**, which are extracted from **Wiktionary** using the `wiktextract` tool.

### Academic Citation
If you use this dataset or API in academic research, please cite the creator of the extraction tool:

> Tatu Ylonen: *Wiktextract: Wiktionary as Machine-Readable Structured Data*, Proceedings of the 13th Conference on Language Resources and Evaluation (LREC), pp. 1317-1325, Marseille, 20-25 June 2022.
