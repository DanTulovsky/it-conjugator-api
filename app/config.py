from __future__ import annotations

import os

# Absolute paths to the on-disk data (SQLite databases + source dump) that must
# be built/downloaded before the API runs.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
VERBS_DB_PATH = os.path.join(DATA_DIR, "verbs.db")
DICTIONARY_DB_PATH = os.path.join(DATA_DIR, "dictionary.db")
# Local copy of the Kaikki Italian Wiktionary dump (downloaded on first build).
KAIKKI_JSONL_PATH = os.path.join(DATA_DIR, "kaikki.org-dictionary-Italian.jsonl")
# Checked-in OpenAPI contract (regenerate with `task swagger`).
SWAGGER_PATH = os.path.join(ROOT_DIR, "swagger.json")


HOMEPAGE_URL = "https://www.wordreference.com/"
BASE = "https://www.wordreference.com/conj/itverbs.aspx"
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    "Referer": "https://www.wordreference.com/",
}


# Toggle to raise on missing sections (otherwise only warn to stderr)
STRICT_CHECKS = False


# Expected structure on WordReference Italian conjugations
EXPECTED = {
"indicativo": {"presente", "imperfetto", "passato remoto", "futuro semplice"},
"tempi composti": {"passato prossimo", "trapassato prossimo", "trapassato remoto", "futuro anteriore"},
"congiuntivo": {"presente", "imperfetto", "passato", "trapassato"},
"condizionale": {"presente", "passato"},
"imperativo": {"presente"},
}


# Stable orders for person labels
PERSON_ORDER_DEFAULT = [
"io", "tu", "lui, lei, Lei, egli", "noi", "voi", "loro, Loro, essi"
]
PERSON_ORDER_IMPERATIVE = [
"", "(tu)", "(Lei)", "(noi)", "(voi)", "(Loro)"
]