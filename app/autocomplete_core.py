"""In-memory prefix index over ``dictionary.db`` for ``/complete``.

The dictionary is read-only, so the index is built once per process and lives
in memory. It is rebuilt from ``dictionary.db`` on every start, which means it
can never go stale — there is no cached artifact to generate or invalidate.

Structure::

    buckets[pos]   -> sorted, deduped list of folded keys
    overrides[pos] -> {folded key: real spelling}, only where they differ

A "folded key" is the word lower-cased with accents stripped, so typing
``citta`` finds ``città``. The real spelling is recovered from ``overrides``,
falling back to the key itself.

``q`` is expected to be a non-blank prefix; rejecting a blank one is the API
layer's job.
"""

from __future__ import annotations

import bisect
import heapq
import sqlite3
from typing import Iterator

from .config import DICTIONARY_DB_PATH

# Accented Latin vowels mapped to their base letter. Applied to the whole word,
# including a final accent: ``città`` -> ``citta``.
#
# ``db_core.clean_accents`` is NOT reusable here. It deliberately *preserves*
# word-final accents (it normalises scraped conjugation tables), and that final
# accent is exactly what has to go for ``citta`` to find ``città``.
FOLD = str.maketrans(
    {
        "à": "a", "á": "a", "â": "a", "ä": "a", "ã": "a", "å": "a",
        "è": "e", "é": "e", "ê": "e", "ë": "e",
        "ì": "i", "í": "i", "î": "i", "ï": "i",
        "ò": "o", "ó": "o", "ô": "o", "ö": "o", "õ": "o",
        "ù": "u", "ú": "u", "û": "u", "ü": "u",
    }
)


def fold(word: str) -> str:
    """Normalise a word for matching: lower-cased, accents stripped."""
    return word.lower().translate(FOLD)


class Index:
    """A prefix lookup over the dictionary, split into parts-of-speech buckets.

    Immutable once built; build one with :func:`build_index`.
    """

    __slots__ = ("buckets", "overrides")

    def __init__(
        self,
        buckets: dict[str, list[str]],
        overrides: dict[str, dict[str, str]],
    ) -> None:
        self.buckets = buckets
        self.overrides = overrides

    def pos_values(self) -> list[str]:
        """Every part of speech present in the index, sorted."""
        return sorted(self.buckets)

    def count(self) -> int:
        """Total indexed keys. A word counts once per part of speech it has."""
        return sum(len(keys) for keys in self.buckets.values())

    def complete(self, q: str, pos: list[str] | None = None, limit: int = 20) -> list[str]:
        """Return words beginning with ``q``, at most ``limit`` of them.

        ``pos`` restricts the search to those parts of speech; ``None`` searches
        every bucket. Results are ordered by folded key, which is alphabetical
        ignoring case and accents.
        """
        key = fold(q).strip()
        if not key:
            return []

        streams = []
        for name in self._selected(pos):
            stream = self._prefix_stream(name, key)
            if stream is not None:
                streams.append(stream)
        return self._emit(streams, limit)

    def _selected(self, pos: list[str] | None) -> list[str]:
        """The buckets to search. Unknown POS names select nothing."""
        if not pos:
            return list(self.buckets)
        return [name for name in pos if name in self.buckets]

    def _prefix_stream(self, pos: str, key: str) -> Iterator[tuple[str, str]] | None:
        """``(folded key, pos)`` pairs for every key in ``pos`` starting with ``key``.

        Returns ``None`` when the bucket holds no match, so that a query hitting
        a single bucket can skip the merge entirely.
        """
        keys = self.buckets[pos]
        start = bisect.bisect_left(keys, key)
        if start >= len(keys) or not keys[start].startswith(key):
            return None
        return self._run(pos, keys, start, key)

    @staticmethod
    def _run(pos: str, keys: list[str], start: int, key: str) -> Iterator[tuple[str, str]]:
        """Yield matching ``(key, pos)`` pairs lazily from ``start`` onwards.

        Lazy is the point: a two-letter prefix can match hundreds of thousands
        of keys, and the walk stops as soon as ``limit`` results are emitted.
        """
        i, total = start, len(keys)
        while i < total and keys[i].startswith(key):
            yield keys[i], pos
            i += 1

    def _emit(self, streams: list[Iterator[tuple[str, str]]], limit: int) -> list[str]:
        """Resolve ``(key, pos)`` pairs to real spellings, deduped, up to ``limit``.

        A single stream skips :func:`heapq.merge` — that is the hot path
        (``q=mangi&pos=verb``) and the merge is ~40x slower than the plain walk.
        """
        if not streams:
            return []

        source: Iterator[tuple[str, str]]
        source = streams[0] if len(streams) == 1 else heapq.merge(*streams)

        overrides = self.overrides
        seen: set[str] = set()
        out: list[str] = []
        for key, pos in source:
            # The same word can be several parts of speech, so it appears in
            # several buckets; emit it once.
            word = overrides.get(pos, {}).get(key, key)
            if word in seen:
                continue
            seen.add(word)
            out.append(word)
            if len(out) >= limit:
                break
        return out


def build_index(db_path: str = DICTIONARY_DB_PATH) -> Index:
    """Build the index from ``dictionary.db``.

    Rows are streamed rather than fetched in bulk: the table also holds ~150 MB
    of compressed entry blobs that this never needs, and holding 623k rows in a
    list at once costs tens of megabytes for nothing.
    """
    buckets: dict[str, list[str]] = {}
    overrides: dict[str, dict[str, str]] = {}

    conn = sqlite3.connect(db_path)
    try:
        for word, pos in conn.execute("SELECT word, pos FROM entries"):
            key = fold(word)
            if key != word:
                # First spelling in dump order wins when several fold to one key
                # (e.g. `manco` and `mancò`). The real spelling is cosmetic here:
                # whichever is returned, the client can act on it.
                overrides.setdefault(pos, {}).setdefault(key, word)
            buckets.setdefault(pos, []).append(key)
    finally:
        conn.close()

    for pos, keys in buckets.items():
        buckets[pos] = sorted(set(keys))
    return Index(buckets, overrides)
