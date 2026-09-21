"""Offline dictionary lookups backed by ``dictionary.db``.

Each row of ``dictionary.db`` holds one Wiktionary entry (word + part of
speech + etymology section) as a zlib-compressed JSON blob built by
``build_dictionary_db.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import zlib
from typing import Any, Dict, List, Optional

from .config import DICTIONARY_DB_PATH


def db_is_present() -> bool:
    return os.path.exists(DICTIONARY_DB_PATH)


def _row_to_entry(word: str, row: sqlite3.Row) -> Dict[str, Any]:
    entry = json.loads(zlib.decompress(row["entry_json"]).decode("utf-8"))
    entry["word"] = word
    entry["pos"] = row["pos"]
    entry["etymology_number"] = row["etymology_number"]
    entry["is_lemma"] = bool(row["is_lemma"])
    return entry


def get_definitions(word_query: str) -> Optional[Dict[str, Any]]:
    """Return all dictionary entries for ``word_query`` (any part of speech).

    Matching is exact first, then case-insensitive. Returns ``None`` when the
    word is not present in the dictionary.
    """
    # Validate the query before touching the database: a blank query is a
    # client-side error and must not depend on the database's presence.
    word = (word_query or "").strip().strip("\"'")
    if not word:
        return None

    if not db_is_present():
        raise FileNotFoundError(
            f"Dictionary database not found at {DICTIONARY_DB_PATH}. "
            "Build it with `task db:dictionary` (or `python3 build_dictionary_db.py`)."
        )

    conn = sqlite3.connect(DICTIONARY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        rows = cursor.execute(
            "SELECT word, pos, etymology_number, is_lemma, entry_json "
            "FROM entries WHERE word = ?",
            (word,),
        ).fetchall()

        if not rows:
            # Case-insensitive fallback (dictionary headwords are lower-cased).
            rows = cursor.execute(
                "SELECT word, pos, etymology_number, is_lemma, entry_json "
                "FROM entries WHERE word = ? COLLATE NOCASE",
                (word,),
            ).fetchall()

        if not rows:
            return None

        entries: List[Dict[str, Any]] = [
            _row_to_entry(row["word"], row) for row in rows
        ]
        # Lemma entries first, then by part of speech for stable output.
        entries.sort(key=lambda e: (not e["is_lemma"], e["pos"] or "", e["etymology_number"] or ""))

        return {"queried": word, "entries": entries}
    finally:
        conn.close()
