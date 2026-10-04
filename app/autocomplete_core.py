"""In-memory prefix index over ``dictionary.db`` for ``/complete``.

The dictionary is read-only, so the index is built once per process and lives
in memory. It is rebuilt from ``dictionary.db`` on every start, which means it
can never go stale — there is no cached artifact to generate or invalidate.

Structure::

    buckets[pos]   -> (canonical keys, other keys), each sorted and deduped
    overrides[pos] -> {folded key: real spelling}, only where they differ

A "folded key" is the word lower-cased with accents stripped, so typing
``citta`` finds ``città``. The real spelling is recovered from ``overrides``,
falling back to the key itself.

Each bucket is split in two so that a query offers a word's *canonical* form
before its inflected forms. For verbs the canonical form is the infinitive,
which is what puts ``mangiare`` in reach of a ``limit`` of 20 for the prefix
``mang`` instead of leaving it 37th behind ``mangiai``, ``mangiammo`` and the
rest. See :func:`_is_canonical`.

``q`` is expected to be a non-blank prefix; rejecting a blank one is the API
layer's job.
"""

from __future__ import annotations

import bisect
import heapq
import sqlite3
import time
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


#: Italian infinitive endings. Used to decide a verb's canonical form.
INFINITIVE_ENDINGS = ("are", "ere", "ire", "arre", "erre", "orre", "urre")


def _is_canonical(pos: str, folded_key: str, is_lemma: bool) -> bool:
    """Whether ``folded_key`` is the canonical form a client should be offered first.

    For verbs that means the infinitive. ``is_lemma`` alone is NOT enough:
    Wiktionary gives pronominal and clitic verbs their own entries, so it flags
    ~48,000 verb keys as lemmas — only about a fifth of which are infinitives
    (``abbacchiarsi``, ``mangiarla`` and ``abbacchiandoci`` are all "lemmas").
    Requiring the infinitive ending is what actually lifts ``mangiare`` to the
    top of a ``mang`` completion.

    Every other part of speech is left unsplit — they keep plain alphabetical
    order — because only verbs have a canonical form a client must be able to
    reach: the one ``/conjugate`` accepts.
    """
    if pos != "verb":
        return True
    return is_lemma and folded_key.endswith(INFINITIVE_ENDINGS)


class Index:
    """A prefix lookup over the dictionary, split into parts-of-speech buckets.

    Each bucket holds two sorted key lists: canonical forms first (see
    :func:`_is_canonical`), then everything else. Queries emit a bucket's
    canonical matches before its inflected ones.

    Immutable once built; build one with :func:`build_index`.
    """

    __slots__ = ("buckets", "overrides")

    def __init__(
        self,
        buckets: dict[str, tuple[list[str], list[str]]],
        overrides: dict[str, dict[str, str]],
    ) -> None:
        self.buckets = buckets
        self.overrides = overrides

    def pos_values(self) -> list[str]:
        """Every part of speech present in the index, sorted."""
        return sorted(self.buckets)

    def count(self) -> int:
        """Total indexed keys. A word counts once per part of speech it has."""
        return sum(
            len(canonical) + len(other) for canonical, other in self.buckets.values()
        )

    def complete(
        self,
        q: str,
        pos: list[str] | None = None,
        limit: int = 20,
        substring: bool = False,
    ) -> list[str]:
        """Return words matching ``q``, at most ``limit`` of them.

        By default ``q`` is a prefix. With ``substring=True`` it may appear
        anywhere in the word — that path scans whole buckets, so it is opt-in.
        ``pos`` restricts the search to those parts of speech; ``None`` searches
        every bucket.

        Canonical forms (verb infinitives) come first, then inflected forms;
        within each group, alphabetical by folded key (ignoring case and
        accents). ``limit`` is clamped to 1-100 rather than rejected.
        """
        # Clamp in one place, so no caller can trip the emit loop with 0/-1.
        limit = max(1, min(int(limit), 100))

        key = fold(q).strip()
        if not key:
            return []

        positions = self._selected(pos)
        seen: set[str] = set()
        out: list[str] = []
        # Two passes over the same buckets: canonical forms first, then the
        # rest. This is what keeps `mangiare` visible for the prefix `mang`
        # instead of burying it behind 36 inflected forms.
        for canonical in (True, False):
            streams = []
            for name in positions:
                stream = (
                    self._substring_stream(name, key, canonical)
                    if substring
                    else self._prefix_stream(name, key, canonical)
                )
                if stream is not None:
                    streams.append(stream)
            out = self._emit(streams, limit, seen, out)
            if len(out) >= limit:
                break
        return out

    def _selected(self, pos: list[str] | None) -> list[str]:
        """The buckets to search. Unknown POS names select nothing."""
        if not pos:
            return list(self.buckets)
        return [name for name in pos if name in self.buckets]

    def _keys(self, pos: str, canonical: bool) -> list[str]:
        """The sorted key list for one bucket: canonical forms, or the rest."""
        return self.buckets[pos][0 if canonical else 1]

    def _prefix_stream(
        self, pos: str, key: str, canonical: bool
    ) -> Iterator[tuple[str, str]] | None:
        """``(folded key, pos)`` pairs for every key in ``pos`` starting with ``key``.

        Returns ``None`` when the bucket holds no match, so that a query hitting
        a single bucket can skip the merge entirely.
        """
        keys = self._keys(pos, canonical)
        start = bisect.bisect_left(keys, key)
        if start >= len(keys) or not keys[start].startswith(key):
            return None
        return self._run(pos, keys, start, key)

    def _substring_stream(
        self, pos: str, key: str, canonical: bool
    ) -> Iterator[tuple[str, str]]:
        """``(key, pos)`` for every key in ``pos`` containing ``key``, in order.

        Always returns a generator (possibly empty), never ``None``: the whole
        key list has to be scanned either way, so there is no cheap bail-out to
        detect. Measured at 0.06 ms for a common fragment and 10.5 ms for a
        fragment that matches nothing anywhere.
        """
        return ((candidate, pos) for candidate in self._keys(pos, canonical) if key in candidate)

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

    def _emit(
        self,
        streams: list[Iterator[tuple[str, str]]],
        limit: int,
        seen: set[str] | None = None,
        out: list[str] | None = None,
    ) -> list[str]:
        """Resolve ``(key, pos)`` pairs to real spellings, deduped, up to ``limit``.

        ``seen`` and ``out`` can be threaded in so the canonical and inflected
        passes share one dedup set and one result list — a word can be canonical
        for one part of speech and inflected for another.

        A single stream skips :func:`heapq.merge` — that is the hot path
        (``q=mangi&pos=verb``) and the merge is ~40x slower than the plain walk.
        """
        seen = set() if seen is None else seen
        out = [] if out is None else out
        if not streams:
            return out

        source: Iterator[tuple[str, str]]
        source = streams[0] if len(streams) == 1 else heapq.merge(*streams)

        overrides = self.overrides
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
    buckets: dict[str, tuple[list[str], list[str]]] = {}
    overrides: dict[str, dict[str, str]] = {}

    conn = sqlite3.connect(db_path)
    try:
        for word, pos, is_lemma in conn.execute(
            "SELECT word, pos, is_lemma FROM entries"
        ):
            key = fold(word)
            if key != word:
                # First spelling in dump order wins when several fold to one key
                # (e.g. `manco` and `mancò`). The real spelling is cosmetic here:
                # whichever is returned, the client can act on it.
                overrides.setdefault(pos, {}).setdefault(key, word)
            canonical, other = buckets.setdefault(pos, ([], []))
            target = canonical if _is_canonical(pos, key, bool(is_lemma)) else other
            target.append(key)
    finally:
        conn.close()

    for pos, (canonical, other) in buckets.items():
        # A word can appear in this POS both as a lemma and as a form; canonical
        # wins, and subtracting it keeps the two halves disjoint so `count()`
        # still reports distinct (word, pos) keys.
        canonical_set = set(canonical)
        buckets[pos] = (sorted(canonical_set), sorted(set(other) - canonical_set))
    return Index(buckets, overrides)


_index: Index | None = None
_build_ms: int | None = None


def ensure_index() -> Index:
    """Return the process-wide index, building it on first use.

    Called eagerly from the API lifespan so the cost (~0.65 s, ~115 MB on the
    current dictionary) is paid at startup instead of by the first request. It
    is idempotent, which also keeps the test suite from rebuilding on every
    lifespan invocation.
    """
    global _index, _build_ms
    if _index is None:
        started = time.perf_counter()
        _index = build_index()
        _build_ms = int((time.perf_counter() - started) * 1000)
    return _index


def index_stats() -> dict[str, int] | None:
    """Index shape for ``/health``; ``None`` when it has not been built yet."""
    if _index is None:
        return None
    return {
        "parts_of_speech": len(_index.buckets),
        "keys": _index.count(),
        "build_ms": _build_ms or 0,
    }
