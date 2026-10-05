"""In-memory prefix index over ``dictionary.db`` for ``/complete``.

The dictionary is read-only, so the index is built once per process and lives
in memory. It is rebuilt from ``dictionary.db`` on every start, which means it
can never go stale — there is no cached artifact to generate or invalidate.

Structure::

    buckets[pos]   -> (canonical keys, other keys), each deduped; canonical
                      keys are shortest-first, other keys alphabetical
    overrides[pos] -> {folded key: real spelling}, only where they differ

A "folded key" is the word lower-cased with accents stripped, so typing
``citta`` finds ``città``. The real spelling is recovered from ``overrides``,
falling back to the key itself.

Each bucket is split in two so that a query offers a word's *canonical* form
before its inflected forms. For verbs the canonical form is the infinitive,
which is what puts ``mangiare`` in reach of a ``limit`` of 20 for the prefix
``mang`` instead of leaving it 37th behind ``mangiai``, ``mangiammo`` and the
rest. See :func:`_is_canonical`.

Within the canonical half, single words come before compound phrases and are
ordered shortest-first, so a query for ``parl`` leads with ``parlare`` rather
than ``parlamentare`` and ``parlamentizzare``: every match shares the typed
prefix, so the shortest is the completion that adds the fewest characters — the
word a client most likely means. Compounds (``città santa``, ``mangiare la
polvere``) follow every single word. The inflected half keeps plain alphabetical
order, also behind its compounds. See :func:`_canonical_order`.

``q`` is expected to be a non-blank prefix; rejecting a blank one is the API
layer's job.
"""

from __future__ import annotations

import bisect
import heapq
import sqlite3
import time
from typing import Callable, Iterator

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


def _single_word_first(key: str) -> int:
    """Outer sort rank: single words (0) before compound phrases (1).

    A compound entry contains a space (``città santa``, ``mangiare la polvere``,
    ``man mano``). A client typing a prefix is far more likely to want the single
    word, so compounds are demoted below every single-word match in both halves.
    """
    return 0 if " " not in key else 1


def _canonical_order(key: str) -> tuple[int, int, str]:
    """Sort key for the canonical half: single words, shortest first, then alpha.

    Every match for a prefix shares that prefix, so a shorter key is a
    completion that adds fewer characters — the closest match to what was typed.
    """
    return _single_word_first(key), len(key), key


def _inflected_order(key: str) -> tuple[int, str]:
    """Sort key for the inflected half: single words first, then alphabetical."""
    return _single_word_first(key), key


def _order_half(keys: set[str], canonical: bool) -> list[str]:
    """Sort one half into its emission order.

    Single words come before compound phrases (those containing a space), and
    within each band the keys are ordered by :func:`_canonical_order` or
    :func:`_inflected_order`.

    Built as a plain C sort followed by a *stable* ``key=len`` re-sort for the
    canonical half, rather than one keyed sort: keying every comparison costs
    ~2.5x here (measured 0.41 s vs 0.18 s over the real dictionary) for identical
    output. Stability is what preserves alphabetical order within a length.
    """
    ordered = sorted(keys)
    single = [k for k in ordered if " " not in k]
    compound = [k for k in ordered if " " in k]
    if canonical:
        single.sort(key=len)
        compound.sort(key=len)
    return single + compound


def _order_key(order: Callable[[str], object]) -> Callable[[tuple[str, str]], object]:
    """Lift a per-key ordering to ``heapq.merge``'s ``key`` over ``(key, pos)``."""

    def key(pair: tuple[str, str]) -> object:
        return order(pair[0])

    return key


#: Lifted merge keys for the two halves, in the same order as ``Index._ORDERS``.
_MERGE_KEYS = (_order_key(_canonical_order), _order_key(_inflected_order))


#: Italian infinitive endings. Used to decide a verb's canonical form.
#:
#: The `-si` reflexives are included because for inherently-pronominal verbs
#: (`pentirsi`, `accorgersi`, `suicidarsi`, `mettersi`) the reflexive *is* the
#: dictionary form — without them those verbs sat outside a default `limit` for
#: their own prefix. The attachments that mark a non-infinitive (`mangiarla`,
#: `abbacchiandoci`) are deliberately absent, so they stay in the inflected half.
INFINITIVE_ENDINGS = (
    "are", "ere", "ire", "arre", "erre", "orre", "urre",
    "arsi", "ersi", "irsi",
)


def _is_canonical(pos: str, folded_key: str, is_lemma: bool) -> bool:
    """Whether ``folded_key`` is the canonical form a client should be offered first.

    For verbs that means the infinitive, plain or reflexive. ``is_lemma`` alone
    is NOT enough: Wiktionary gives gerunds and clitic-object verbs their own
    entries, so it flags ~48,000 verb keys as lemmas — only about a quarter of
    which are infinitives (``abbacchiandoci``, ``mangiarla`` and ``mangiamole``
    are all "lemmas" but none is an infinitive). Requiring an infinitive ending
    is what actually lifts ``mangiare`` to the top of a ``mang`` completion.

    ``is_lemma`` is still required as a gate, and it earns its place: it excludes
    non-infinitive keys that happen to share an ending (``apersi``, passato remoto
    of ``aprire``). The cost is the 132 reflexive keys Wiktionary marks as pure
    forms rather than lemmas — ``ricordarsi`` among them — which stay unboosted.

    Every other part of speech is left unsplit — they keep plain alphabetical
    order — because only verbs have a canonical form a client must be able to
    reach: the one ``/conjugate`` accepts.
    """
    if pos != "verb":
        return True
    return is_lemma and folded_key.endswith(INFINITIVE_ENDINGS)


class Index:
    """A prefix lookup over the dictionary, split into parts-of-speech buckets.

    Each bucket holds two key lists: canonical forms first (see
    :func:`_is_canonical`), then everything else. Within each half, single words
    come before compound phrases (see :func:`_single_word_first`); the canonical
    half is additionally shortest-first (:func:`_canonical_order`), the inflected
    half alphabetical (:func:`_inflected_order`). Queries emit a bucket's
    canonical matches before its inflected ones.

    Immutable once built; build one with :func:`build_index`.
    """

    #: Per-half ordering. The canonical half adds length; the inflected half is
    #: alphabetical. Both demote compounds, so both are "banded" and both need
    #: spans (see :meth:`_spans`).
    _ORDERS = (_canonical_order, _inflected_order)

    __slots__ = ("buckets", "overrides", "spans")

    def __init__(
        self,
        buckets: dict[str, tuple[list[str], list[str]]],
        overrides: dict[str, dict[str, str]],
    ) -> None:
        # The Index owns the ordering invariant, so a hand-built index (tests)
        # and a built one (dictionary.db) behave identically: each half is
        # deduped and made disjoint, then sorted by its half's ordering.
        self.buckets = {}
        self.spans = {}
        for pos, halves in buckets.items():
            canonical_set = set(halves[0])
            ordered = (
                _order_half(canonical_set, canonical=True),
                _order_half(set(halves[1]) - canonical_set, canonical=False),
            )
            self.buckets[pos] = ordered
            self.spans[pos] = tuple(
                self._spans(keys, order) for keys, order in zip(ordered, self._ORDERS)
            )
        self.overrides = overrides

    @staticmethod
    def _spans(keys: list[str], order: Callable[[str], tuple]) -> list[tuple[int, int]]:
        """``(start, end)`` for each run of keys sharing an order-key prefix.

        ``order`` ends in the key itself, so within a run the keys are in plain
        ascending order and bisectable; the run boundary is where the preceding
        terms (single-word rank, and length for the canonical half) change.
        Prefix matching walks the runs in order, bisecting within each, to emit
        results in the half's order without scanning the whole list.
        """
        spans: list[tuple[int, int]] = []
        i, total = 0, len(keys)
        while i < total:
            prefix = order(keys[i])[:-1]
            j = i + 1
            while j < total and order(keys[j])[:-1] == prefix:
                j += 1
            spans.append((i, j))
            i = j
        return spans

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

        Canonical forms (verb infinitives) come first, then inflected forms.
        Within each group single words precede compound phrases; the canonical
        group is additionally shortest-first, the inflected group alphabetical.
        ``limit`` is clamped to 1-100 rather than rejected.
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
        # instead of burying it behind 36 inflected forms. Each pass merges on
        # its half's ordering.
        for canonical in (True, False):
            half = 0 if canonical else 1
            streams = []
            for name in positions:
                stream = (
                    self._substring_stream(name, key, canonical)
                    if substring
                    else self._prefix_stream(name, key, canonical)
                )
                if stream is not None:
                    streams.append(stream)
            out = self._emit(streams, limit, seen, out, _MERGE_KEYS[half])
            if len(out) >= limit:
                break
        return out

    def _selected(self, pos: list[str] | None) -> list[str]:
        """The buckets to search. Unknown POS names select nothing."""
        if not pos:
            return list(self.buckets)
        return [name for name in pos if name in self.buckets]

    def _keys(self, pos: str, canonical: bool) -> list[str]:
        """The key list for one bucket: canonical forms, or the rest."""
        return self.buckets[pos][0 if canonical else 1]

    def _prefix_stream(
        self, pos: str, key: str, canonical: bool
    ) -> Iterator[tuple[str, str]] | None:
        """``(folded key, pos)`` pairs for every key in ``pos`` starting with ``key``.

        Returns ``None`` when the bucket holds no match, so that a query hitting
        a single bucket can skip the merge entirely.
        """
        keys = self._keys(pos, canonical)
        # Each half is banded by its ordering (single-word rank, plus length for
        # the canonical half), so matches are not one contiguous run: bisect
        # within each band. Bands are already in emission order.
        runs = []
        for start, end in self.spans[pos][0 if canonical else 1]:
            i = bisect.bisect_left(keys, key, start, end)
            if i < end and keys[i].startswith(key):
                runs.append((i, end))
        if not runs:
            return None
        return self._run_spans(pos, keys, runs, key)

    @staticmethod
    def _run_spans(
        pos: str, keys: list[str], runs: list[tuple[int, int]], key: str
    ) -> Iterator[tuple[str, str]]:
        """Yield matching ``(key, pos)`` pairs lazily across several spans.

        Lazy is the point: a short prefix can match hundreds of thousands of
        keys, and the walk stops as soon as ``limit`` results are emitted.
        """
        for start, end in runs:
            i = start
            while i < end and keys[i].startswith(key):
                yield keys[i], pos
                i += 1

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

    def _emit(
        self,
        streams: list[Iterator[tuple[str, str]]],
        limit: int,
        seen: set[str] | None = None,
        out: list[str] | None = None,
        merge_key: Callable[[tuple[str, str]], object] | None = None,
    ) -> list[str]:
        """Resolve ``(key, pos)`` pairs to real spellings, deduped, up to ``limit``.

        ``seen`` and ``out`` can be threaded in so the canonical and inflected
        passes share one dedup set and one result list — a word can be canonical
        for one part of speech and inflected for another.

        ``merge_key`` is the ordering applied when several buckets are merged
        (each pass uses its half's ordering; ``None`` is plain key order).

        A single stream skips :func:`heapq.merge` — that is the hot path
        (``q=mangi&pos=verb``) and the merge is ~40x slower than the plain walk.
        """
        seen = set() if seen is None else seen
        out = [] if out is None else out
        if not streams:
            return out

        source: Iterator[tuple[str, str]]
        if len(streams) == 1:
            source = streams[0]
        else:
            source = heapq.merge(*streams, key=merge_key)

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

    # `Index` owns ordering and the canonical/other disjointness, so the halves
    # are handed over as accumulated.
    return Index(buckets, overrides)


_index: Index | None = None
_build_ms: int | None = None


def ensure_index() -> Index:
    """Return the process-wide index, building it on first use.

    Called eagerly from the API lifespan so the cost (~0.8 s, ~120 MB on the
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
