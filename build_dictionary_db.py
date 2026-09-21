#!/usr/bin/env python3
"""Build ``dictionary.db`` — a full offline Italian dictionary.

The source is the machine-readable Italian Wiktionary dump published by
Kaikki.org (``kaikki.org-dictionary-Italian.jsonl``). If the file is not
present locally it is streamed from Kaikki.org.

Unlike ``build_db.py`` (which keeps only verb conjugations), this builder keeps
*every* part of speech and stores each dictionary entry as a zlib-compressed
JSON blob so the original structure is preserved.

Entry shape produced here (keys absent when empty)::

    word, pos, etymology_number, etymology,
    head, ipa, rhymes, hyphenation,
    senses: [{glosses, raw_glosses, tags, form_of, examples, synonyms, antonyms}],
    forms:  [{form, tags}],
    derived, related, synonyms, antonyms, translations, descendants, categories, ...

``word`` and ``pos`` become columns; the remaining keys are stored as the
compressed ``entry_json`` blob. The ``is_lemma`` column marks entries that have
at least one independent sense (see :func:`_is_form_of`) so lookups can list
real lemmas before pure inflected forms.

Usage::

    python3 build_dictionary_db.py            # use local dump, else download
"""

import codecs
import json
import os
import sqlite3
import sys
import urllib.request
import zlib

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "dictionary.db")
KAIKKI_URL = "https://kaikki.org/dictionary/Italian/kaikki.org-dictionary-Italian.jsonl"
# Local dump location (downloaded here on first build).
LOCAL_DUMP_PATH = os.path.join(DATA_DIR, os.path.basename(KAIKKI_URL))

# Top-level keys dropped from the stored entry blob.
#
# - word / pos            -> stored as dedicated columns instead.
# - lang / lang_code      -> always Italian in this dump.
# - head_templates        -> replaced by the readable ``head`` list.
# - sounds                -> replaced by ``ipa`` / ``rhymes``.
# - hyphenations          -> duplicate of ``hyphenation``.
# - etymology_templates / inflection_templates / info_templates
#                         -> raw wikitext expansions (some contain broken
#                            embedded HTML); ``etymology`` keeps the readable text.
# - forms / senses        -> replaced by curated versions.
DROP_KEYS = {
    "word", "pos", "lang", "lang_code",
    "head_templates", "sounds", "hyphenations",
    "etymology_templates", "etymology_text", "inflection_templates", "info_templates",
    "forms", "senses",
    "wikipedia", "original_title", "source",
}

# Sense-level keys kept as-is.
SENSE_KEYS = ("glosses", "raw_glosses", "tags", "form_of", "examples", "synonyms", "antonyms")


def _head_expansions(entry):
    """Readable head lines, e.g. 'cane m (plural cani, feminine cagna, ...)'."""
    out = []
    for ht in entry.get("head_templates", []):
        exp = ht.get("expansion")
        if exp:
            out.append(exp)
    return out


def _is_form_of(entry):
    """True when this entry only describes inflected forms of another word.

    An entry counts as a form-of (non-lemma) entry when it has a top-level
    ``form_of``, or when it has senses and *every* sense is a form-of sense.
    An entry that also carries at least one independent sense is treated as a
    lemma, so a word with its own definition is never demoted behind the
    form-of entries in a lookup.
    """
    if "form_of" in entry:
        return True
    senses = entry.get("senses", [])
    if not senses:
        return False
    return all("form_of" in sense for sense in senses)


def curate_entry(entry):
    """Return the trimmed, self-contained dict stored for one dump line."""
    out = {k: v for k, v in entry.items() if k not in DROP_KEYS}

    if entry.get("etymology_text"):
        out["etymology"] = entry["etymology_text"]

    head = _head_expansions(entry)
    if head:
        out["head"] = head

    ipa, rhymes = [], []
    for s in entry.get("sounds", []):
        if s.get("ipa"):
            ipa.append(s["ipa"])
        if s.get("rhymes"):
            rhymes.append(s["rhymes"])
    if ipa:
        out["ipa"] = ipa
    if rhymes:
        out["rhymes"] = rhymes

    forms = []
    for f in entry.get("forms", []):
        form = f.get("form")
        if not form:
            continue
        item = {"form": form}
        if f.get("tags"):
            item["tags"] = f["tags"]
        forms.append(item)
    if forms:
        out["forms"] = forms

    senses = []
    for s in entry.get("senses", []):
        trimmed = {k: s[k] for k in SENSE_KEYS if s.get(k)}
        senses.append(trimmed)
    if senses:
        out["senses"] = senses

    return out


def build_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY,
            word TEXT NOT NULL,
            pos TEXT,
            etymology_number TEXT,
            is_lemma INTEGER NOT NULL DEFAULT 0,
            entry_json BLOB NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX idx_entries_word ON entries(word)")
    cursor.execute("CREATE INDEX idx_entries_word_nocase ON entries(word COLLATE NOCASE)")
    conn.commit()
    print("Created dictionary.db schema.")

    if os.path.exists(LOCAL_DUMP_PATH):
        print(f"Using local file: {LOCAL_DUMP_PATH}")
        source = open(LOCAL_DUMP_PATH, "rb")
    else:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(KAIKKI_URL, headers=headers)
        print(f"Streaming Wiktionary data from {KAIKKI_URL} ...")
        source = urllib.request.urlopen(req)
    reader = codecs.getreader("utf-8")(source)

    batch = []
    line_count = 0
    entry_count = 0
    skipped = 0

    try:
        for line in reader:
            line_count += 1
            if line_count % 100000 == 0:
                print(f"Processed {line_count} lines. Stored {entry_count} entries...")

            try:
                entry = json.loads(line)
            except Exception:
                skipped += 1
                continue

            word = entry.get("word")
            if not word:
                skipped += 1
                continue

            curated = curate_entry(entry)
            blob = sqlite3.Binary(
                zlib.compress(json.dumps(curated, ensure_ascii=False).encode("utf-8"), 9)
            )
            ety_num = entry.get("etymology_number")
            batch.append(
                (
                    word,
                    entry.get("pos"),
                    str(ety_num) if ety_num is not None else None,
                    0 if _is_form_of(entry) else 1,
                    blob,
                )
            )
            entry_count += 1

            if len(batch) >= 2000:
                cursor.executemany(
                    "INSERT INTO entries (word, pos, etymology_number, is_lemma, entry_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                batch.clear()
                conn.commit()
    except Exception as e:
        conn.close()
        source.close()
        print(f"Error during streaming: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        if not source.closed:
            source.close()

    if batch:
        cursor.executemany(
            "INSERT INTO entries (word, pos, etymology_number, is_lemma, entry_json) "
            "VALUES (?, ?, ?, ?, ?)",
            batch,
        )

    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    size_mb = os.path.getsize(DB_PATH) / 1e6
    print("Database build completed successfully!")
    print(f"Total lines processed:   {line_count}")
    print(f"Total entries stored:    {entry_count}")
    print(f"Skipped (empty/bad) lines: {skipped}")
    print(f"Database size:           {size_mb:.1f} MB ({DB_PATH})")


if __name__ == "__main__":
    build_database()
