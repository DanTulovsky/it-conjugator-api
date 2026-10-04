# `/complete` Prefix Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /complete` endpoint that returns dictionary words starting
with a partial word, restricted by part of speech, answered from an in-memory
prefix index built once at startup.

**Architecture:** A new `app/autocomplete_core.py` builds, from the read-only
`dictionary.db`, a dict of *sorted, deduped folded keys* per part of speech
(POS), plus a small per-POS map from folded key back to the real spelling.
Matching is `bisect` + a bounded walk — 10–20 µs on the hot path. The index is
rebuilt from `dictionary.db` on every boot, so it can never be stale: no
prebuilt artifact, no `Taskfile` task, no `Dockerfile` line. `app/api.py` exposes
it as `/complete` behind the existing `X-API-Key` gate.

**Tech Stack:** Python 3.12, FastAPI 0.115.0, pydantic, stdlib `sqlite3` /
`bisect` / `heapq`, stdlib `unittest`, and `fastapi.testclient` (needs `httpx`,
added to `requirements.txt` in Task 3 as the one test-only dependency).

**Spec:** `docs/superpowers/specs/2026-10-04-autocomplete-design.md`

## Global Constraints

- **Dependencies.** The implementation itself adds none: `bisect`, `heapq`,
  `sqlite3` and `unittest` are all stdlib. Task 3 adds **one test-only
  dependency**, `httpx`, to `requirements.txt` so `fastapi.testclient.TestClient`
  can drive the real ASGI stack. The user approved this. At roughly 10 MB on the
  image it is the simplest way to get reproducible HTTP-level tests, given the
  repo has no separate dev-requirements file. If image size matters more than
  that, move `httpx` to a new `requirements-dev.txt` — but then `task test` needs
  it installed, so `Taskfile.yml` changes too.
- **Python 3.12** in the image (`FROM python:3.12-slim`). Avoid 3.13-only syntax.
- **Interpreters.** System `python3` has no FastAPI installed. Focused runs use
  `.venv/bin/python`. The `Taskfile` uses bare `python3`, which resolves inside
  an activated virtualenv — `task test`, `task db:dictionary`, and
  `task swagger` all assume that activation.
- **Tests are stdlib `unittest`**, run as `python3 -m unittest discover -s tests -v`.
  No pytest, no fixtures.
- **Auth.** Every endpoint except `/health` requires header
  `X-API-Key: <SCRAPER_API_KEY>`, else HTTP 401. `/complete` is no exception.
- **`swagger.json` must be regenerated** (`task swagger`) in the *same task* as
  any change to routes or models. `_contract_problem()` in `api.lifespan`
  compares the checked-in file against `app.openapi()` and raises `RuntimeError`
  when they differ, so the API will not boot until it is regenerated. This is
  the repo's only OpenAPI artifact — JSON, not YAML.
- **Branch.** The user explicitly consented to committing directly to `main` in
  this checkout. Do **not** create a branch or a git worktree: `data/verbs.db`,
  `data/dictionary.db`, the Kaikki dump, `.venv` and `.env` are all gitignored
  and present here, so `task test` works as-is. A worktree would not contain
  them and every dictionary-backed test would silently skip.
- **Verify with the interpreter that is actually installed.** A green focused
  run is the evidence a step is done; do not claim a step passes without it.
- **Verbatim values from the spec:** default `limit` 20, clamped to 1–100;
  `pos` default is *all* parts of speech; a blank `q` is HTTP 400; an unmatched
  prefix is HTTP 200 with `success: true` and `matches: []`.
- **No performance assertions in tests.** Timing tests flake. The timings in the
  spec are design justification, re-measured by hand when the index changes.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/autocomplete_core.py` (create) | Fold, index build, and the entire query path. No HTTP, no FastAPI imports. |
| `app/models.py` (modify) | `CompleteQuery`, `CompletionData`, `CompleteResponse`; `HealthResponse` gains index stats. |
| `app/api.py` (modify) | The `/complete` route, eager index build in `lifespan`, `/health` stats. |
| `tests/test_autocomplete.py` (create) | Fold, index, and endpoint tests. |
| `tests/test_dictionary.py` (modify) | Existing OpenAPI-contract assertions must learn about `/complete`. |
| `requirements.txt` (modify) | One added line: `httpx`, so `fastapi.testclient` can drive the real ASGI stack in tests. |
| `swagger.json` (modify) | Regenerated, twice: once for the route, once for `HealthResponse`. |
| `README.md` (modify) | Endpoints table row and a `/complete` section. |

---

### Task 1: The prefix index

Pure index code, no HTTP. Delivers a working case- and accent-insensitive prefix
lookup across one or more parts of speech.

**Files:**
- Create: `app/autocomplete_core.py`
- Test: `tests/test_autocomplete.py`

**Interfaces:**
- Consumes: `app.config.DICTIONARY_DB_PATH`; the `entries(word, pos)` table of
  `dictionary.db`.
- Produces:
  - `fold(word: str) -> str`
  - `class Index` with attributes `buckets: dict[str, list[str]]` (POS → sorted
    deduped folded keys) and `overrides: dict[str, dict[str, str]]` (POS →
    `{folded key: real spelling}`), methods `pos_values(self) -> list[str]`,
    `count(self) -> int`, and
    `complete(self, q: str, pos: list[str] | None = None, limit: int = 20) -> list[str]`
  - `build_index(db_path: str = DICTIONARY_DB_PATH) -> Index`
  - Module constant `FOLD: dict[int, str]` (a `str.maketrans` table)

- [ ] **Step 1: Write the failing fold and index tests**

Create `tests/test_autocomplete.py`:

```python
"""
Tests for prefix autocomplete (``/complete``).

Covers three layers:

1. ``app.autocomplete_core.fold`` — the accent/case normalisation.
2. ``app.autocomplete_core.Index`` — prefix matching, POS filtering, limits.
3. ``app.api`` — the ``/complete`` endpoint, its validation, and its auth.

The fold tests and the hand-built-index tests need nothing on disk. The
``dictionary.db``-backed tests and the endpoint tests need a built database
(``task db:dictionary``).

Run the suite from the repo root::

    python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import config, dictionary_core  # noqa: E402
from app.autocomplete_core import Index, build_index, fold  # noqa: E402

# The API key is read from the environment at import time; set it before we
# import app.api so the endpoints accept our test key.
TEST_API_KEY = "test-api-key"
os.environ.setdefault("SCRAPER_API_KEY", TEST_API_KEY)

import app.api as api  # noqa: E402

REPO_HAS_DICTIONARY = dictionary_core.db_is_present()


def _tiny_index():
    """A hand-built index so index tests never depend on the real dictionary.

    Keys are already folded, as ``build_index`` guarantees. ``citta`` has an
    override to ``città``; ``cane`` is deliberately in two POS buckets so the
    cross-bucket dedup path is exercised.
    """
    return Index(
        buckets={
            "verb": sorted(["mangiare", "mangi", "mangia", "mangiai", "mancare", "cane"]),
            "noun": sorted(["cane", "citta", "cittadino", "mancanza"]),
        },
        overrides={"noun": {"citta": "città"}},
    )


class TestFold(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(fold("CANE"), "cane")
        self.assertEqual(fold("Cane"), "cane")

    def test_strips_accents_anywhere_in_the_word(self):
        self.assertEqual(fold("città"), "citta")
        self.assertEqual(fold("però"), "pero")
        self.assertEqual(fold("più"), "piu")
        self.assertEqual(fold("così"), "cosi")

    def test_strips_a_final_accent_that_clean_accents_preserves(self):
        # db_core.clean_accents deliberately keeps word-final accents (it
        # normalises scraped conjugation tables). The index must not, or typing
        # `citta` would never find `città`.
        from app.db_core import clean_accents

        self.assertEqual(clean_accents("città"), "città")
        self.assertEqual(fold("città"), "citta")

    def test_is_idempotent(self):
        for word in ("cane", "città", "però", "CANE", "CITTÀ"):
            self.assertEqual(fold(fold(word)), fold(word))


class TestIndexPrefix(unittest.TestCase):

    def setUp(self):
        self.index = _tiny_index()

    def test_reports_its_parts_of_speech(self):
        self.assertEqual(self.index.pos_values(), ["noun", "verb"])
        self.assertEqual(self.index.count(), 10)

    def test_prefix_within_one_pos(self):
        self.assertEqual(
            self.index.complete("mang", pos=["verb"]),
            ["mangi", "mangia", "mangiai", "mangiare"],
        )

    def test_prefix_returns_the_real_spelling(self):
        self.assertEqual(
            self.index.complete("citt", pos=["noun"]),
            ["città", "cittadino"],
        )

    def test_prefix_with_no_pos_searches_every_bucket(self):
        # Only the noun bucket has a `citt` prefix, but the query still works.
        self.assertEqual(self.index.complete("citt"), ["città", "cittadino"])

    def test_prefix_with_no_pos_merges_and_sorts_across_buckets(self):
        # `mancanza` (noun) sorts before `mancare` (verb) by folded key.
        self.assertEqual(self.index.complete("manc"), ["mancanza", "mancare"])

    def test_merges_across_buckets_and_dedupes_a_word_with_two_pos(self):
        # `cane` is in both buckets; it must appear once.
        self.assertEqual(self.index.complete("cane"), ["cane"])

    def test_respects_limit(self):
        self.assertEqual(
            self.index.complete("mang", pos=["verb"], limit=2), ["mangi", "mangia"]
        )

    def test_no_match_returns_empty(self):
        self.assertEqual(self.index.complete("zzzz"), [])
        self.assertEqual(self.index.complete("mang", pos=["noun"]), [])

    def test_unknown_pos_matches_nothing(self):
        # Validation lives in the API layer; the index is deliberately not
        # authoritative about which POS names are valid.
        self.assertEqual(self.index.complete("mang", pos=["bogus"]), [])

    def test_blank_prefix_returns_empty(self):
        self.assertEqual(self.index.complete(""), [])
        self.assertEqual(self.index.complete("   "), [])

    def test_accented_query_matches_the_folded_key(self):
        # Both spellings of the query reach the same key.
        self.assertEqual(self.index.complete("città"), ["città", "cittadino"])
        self.assertEqual(self.index.complete("CITTÀ"), ["città", "cittadino"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_autocomplete -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.autocomplete_core'`.

If it instead reports `No module named 'fastapi'`, you used the system
interpreter; use `.venv/bin/python`.

- [ ] **Step 3: Write the implementation**

Create `app/autocomplete_core.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_autocomplete -v`

Expected: PASS — 15 tests (4 in `TestFold`, 11 in `TestIndexPrefix`).

- [ ] **Step 5: Run the whole suite to check for regressions**

Run: `task test`

Expected: `Ran 64 tests ... OK`. The 49 pre-existing tests must still pass.

- [ ] **Step 6: Commit**

```bash
git add app/autocomplete_core.py tests/test_autocomplete.py
git commit -m "feat(autocomplete): add the in-memory prefix index"
```

---

### Task 2: Substring matching, limit clamping, and index caching

Completes the index: the opt-in substring mode, the documented 1–100 limit
clamp, and the process-wide cached instance the API layer will use.

**Files:**
- Modify: `app/autocomplete_core.py`
- Test: `tests/test_autocomplete.py`

**Interfaces:**
- Consumes: everything from Task 1 (`fold`, `Index`, `build_index`).
- Produces:
  - `Index.complete(self, q: str, pos: list[str] | None = None, limit: int = 20, substring: bool = False) -> list[str]`
  - `ensure_index() -> Index` — builds once per process, then returns the cache
  - `index_stats() -> dict[str, int] | None` — `None` until built; else
    `{"parts_of_speech": int, "keys": int, "build_ms": int}`
  - Module globals `_index: Index | None`, `_build_ms: int | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_autocomplete.py`, before the `if __name__ == "__main__":` block (which does not exist yet — append at end of file):

```python
class TestIndexSubstring(unittest.TestCase):

    def setUp(self):
        self.index = _tiny_index()

    def test_substring_finds_an_interior_fragment(self):
        # `are` is a suffix, so prefix matching finds nothing.
        self.assertEqual(self.index.complete("are", pos=["verb"]), [])
        self.assertEqual(
            self.index.complete("are", pos=["verb"], substring=True),
            ["mancare", "mangiare"],
        )

    def test_substring_searches_every_bucket_by_default(self):
        self.assertEqual(
            self.index.complete("anc", substring=True), ["mancanza", "mancare"]
        )

    def test_substring_respects_pos(self):
        self.assertEqual(
            self.index.complete("anc", pos=["noun"], substring=True), ["mancanza"]
        )

    def test_substring_dedupes_across_buckets(self):
        # `ane`: `cane` is in both buckets.
        self.assertEqual(self.index.complete("ane", substring=True), ["cane"])

    def test_substring_returns_the_real_spelling(self):
        self.assertIn("città", self.index.complete("ttà", substring=True))

    def test_substring_no_match_returns_empty(self):
        self.assertEqual(self.index.complete("zzzz", substring=True), [])

    def test_substring_respects_limit(self):
        self.assertEqual(
            self.index.complete("ang", pos=["verb"], limit=2, substring=True),
            ["mangi", "mangia"],
        )


class TestLimitClamping(unittest.TestCase):
    """The spec says `limit` is clamped to 1-100, not rejected."""

    def setUp(self):
        self.index = _tiny_index()

    def test_zero_clamps_to_one(self):
        self.assertEqual(len(self.index.complete("mang", pos=["verb"], limit=0)), 1)

    def test_negative_clamps_to_one(self):
        self.assertEqual(len(self.index.complete("mang", pos=["verb"], limit=-5)), 1)

    def test_above_one_hundred_clamps_to_one_hundred(self):
        # The tiny index is smaller than 100, so this only has to not explode.
        self.assertEqual(
            self.index.complete("mang", pos=["verb"], limit=500),
            ["mangi", "mangia", "mangiai", "mangiare"],
        )

    def test_clamping_also_applies_to_substring(self):
        self.assertEqual(len(self.index.complete("ang", limit=0, substring=True)), 1)


class TestIndexCaching(unittest.TestCase):

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_ensure_index_builds_once_and_caches(self):
        first = autocomplete_core.ensure_index()
        self.assertIs(autocomplete_core.ensure_index(), first)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_index_stats_describe_the_loaded_index(self):
        autocomplete_core.ensure_index()
        stats = autocomplete_core.index_stats()
        self.assertIsNotNone(stats)
        self.assertEqual(stats["parts_of_speech"], 23)
        self.assertGreater(stats["keys"], 600_000)
```

Also add `autocomplete_core` to the import block at the top of the file:

```python
from app import autocomplete_core, config, dictionary_core  # noqa: E402
from app.autocomplete_core import Index, build_index, fold  # noqa: E402
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_autocomplete.TestIndexSubstring tests.test_autocomplete.TestLimitClamping -v`

Expected: FAIL — `TypeError: Index.complete() got an unexpected keyword argument 'substring'`, and `TestLimitClamping.test_zero_clamps_to_one` failing with 0 results.

Then: `.venv/bin/python -m unittest tests.test_autocomplete.TestIndexCaching -v`

Expected: FAIL — `AttributeError: module 'app.autocomplete_core' has no attribute 'ensure_index'`.

- [ ] **Step 3: Add the substring and caching code**

In `app/autocomplete_core.py`, add `import time` to the import block:

```python
import bisect
import heapq
import sqlite3
import time
from typing import Iterator
```

Replace the body of `Index.complete` and add `_substring_stream` after
`_prefix_stream`:

```python
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
        every bucket. Results are ordered by folded key, which is alphabetical
        ignoring case and accents.

        ``limit`` is clamped to 1-100 rather than rejected.
        """
        # Clamp in one place, so no caller can trip the emit loop with 0/-1.
        limit = max(1, min(int(limit), 100))

        key = fold(q).strip()
        if not key:
            return []

        streams = []
        for name in self._selected(pos):
            stream = (
                self._substring_stream(name, key)
                if substring
                else self._prefix_stream(name, key)
            )
            if stream is not None:
                streams.append(stream)
        return self._emit(streams, limit)
```

```python
    def _substring_stream(self, pos: str, key: str) -> Iterator[tuple[str, str]]:
        """``(key, pos)`` for every key in ``pos`` containing ``key``, in order.

        Always returns a generator (possibly empty), never ``None``: the whole
        bucket has to be scanned either way, so there is no cheap bail-out to
        detect. Measured at 0.06 ms for a common fragment and 10.5 ms for a
        fragment that matches nothing anywhere.
        """
        return ((candidate, pos) for candidate in self.buckets[pos] if key in candidate)
```

Then append the process-wide cache at the end of the module:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_autocomplete -v`

Expected: PASS — 28 tests.

- [ ] **Step 5: Sanity-check the real timings by hand**

The spec's timings justify the design, so confirm they still hold. Run:

```bash
.venv/bin/python -c "
import time
from app import autocomplete_core as ac
t = time.perf_counter(); idx = ac.ensure_index(); build = time.perf_counter() - t
def timeit(fn, n=200):
    t = time.perf_counter()
    for _ in range(n): fn()
    return (time.perf_counter() - t) / n * 1e6
print('build          %6.0f ms' % (build * 1000))
print('prefix 1 POS   %6.1f us' % timeit(lambda: idx.complete('mangi', ['verb'])))
print('prefix no POS  %6.1f us' % timeit(lambda: idx.complete('mangi')))
print('substring hit  %6.1f us' % timeit(lambda: idx.complete('ane', substring=True)))
print('stats', ac.index_stats())
"
```

Expected: build under ~2000 ms, `prefix 1 POS` in the tens of microseconds
(spec says 10–20 µs), and stats reporting 23 parts of speech and >600,000 keys.
If the single-POS number is in the hundreds of microseconds, the
`len(streams) == 1` fast path in `_emit` has been lost.

- [ ] **Step 6: Run the whole suite**

Run: `task test`

Expected: `Ran 77 tests ... OK`.

- [ ] **Step 7: Commit**

```bash
git add app/autocomplete_core.py tests/test_autocomplete.py
git commit -m "feat(autocomplete): add substring matching, limit clamping and index caching"
```

---

### Task 3: The `/complete` endpoint

Models plus the route. This task also regenerates `swagger.json` and updates the
existing contract assertions, because the app will not boot — and the suite will
not pass — until all three change together.

**Files:**
- Modify: `app/models.py`
- Modify: `app/api.py`
- Modify: `requirements.txt` (adds `httpx`, test-only)
- Modify: `tests/test_dictionary.py` (the `TestOpenAPIContract` class)
- Modify: `tests/test_autocomplete.py`
- Modify: `swagger.json` (regenerated)

**Interfaces:**
- Consumes: `autocomplete_core.ensure_index()`, `Index.complete()`,
  `Index.pos_values()`, `Index.buckets`; `api.API_KEY`, `api.api_key_header`,
  `api._csv_to_list`.
- Produces:
  - `models.CompleteQuery(q: str, pos: list[str] | None, limit: int, substring: bool)`
  - `models.CompletionData(queried: str, pos: list[str] | None, matches: list[str])`
  - `models.CompleteResponse(success, requested, note, error, data)`
  - `api.complete(q, pos, limit, substring, api_key) -> CompleteResponse | JSONResponse`

- [ ] **Step 1: Add the HTTP test dependency, then write the failing tests**

Two actions, both prerequisites of this task's deliverable. First, append
`httpx` to `requirements.txt` (**on its own line**, after `uvicorn[standard]`),
then install it into the local virtualenv:

```bash
printf 'httpx\n' >> requirements.txt
.venv/bin/pip install httpx
```

`httpx` is what `fastapi.testclient.TestClient` drives; it is a test-only
dependency, never imported by `app/`.

Now append to `tests/test_autocomplete.py`:

```python
class TestCompleteEndpoint(unittest.TestCase):
    """The endpoint gates on the API key exactly like /conjugate and /define."""

    def test_requires_api_key(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            api.complete(q="cane", api_key=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_a_blank_query(self):
        import json

        resp = api.complete(q="   ", api_key=api.API_KEY)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("'q'", json.loads(resp.body)["error"])

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_rejects_an_unknown_part_of_speech(self):
        import json

        resp = api.complete(q="mang", pos="bogus", api_key=api.API_KEY)
        self.assertEqual(resp.status_code, 400)
        error = json.loads(resp.body)["error"]
        self.assertIn("bogus", error)
        # The message tells the caller what is actually allowed.
        self.assertIn("verb", error)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_completes_a_verb_prefix(self):
        # `mangiare` is the 37th key for the prefix `mangi`, so a shorter prefix
        # would not reach it inside the default limit. Verified against the
        # built dictionary: 138 verb keys start with `mangi`.
        resp = api.complete(q="mangiare", pos="verb", limit=5, api_key=api.API_KEY)
        self.assertTrue(resp.success)
        self.assertEqual(resp.requested.q, "mangiare")
        self.assertEqual(resp.requested.pos, ["verb"])
        self.assertEqual(resp.data.queried, "mangiare")
        self.assertEqual(resp.data.pos, ["verb"])
        self.assertIn("mangiare", resp.data.matches)
        self.assertTrue(all(fold(w).startswith("mangiare") for w in resp.data.matches))

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_results_are_ordered_by_folded_key(self):
        # The documented ordering: alphabetical ignoring case and accents. This
        # pins it, so switching to lemma-first ranking later is a deliberate
        # test change rather than a silent one.
        matches = api.complete(q="mangi", pos="verb", limit=50, api_key=api.API_KEY)
        folded = [fold(w) for w in matches.data.matches]
        self.assertEqual(folded, sorted(folded))
        self.assertEqual(len(folded), 50)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_pos_filter_changes_the_result(self):
        verbs = api.complete(q="mang", pos="verb", api_key=api.API_KEY).data.matches
        nouns = api.complete(q="mang", pos="noun", api_key=api.API_KEY).data.matches
        self.assertTrue(verbs)
        self.assertNotEqual(verbs, nouns)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_omitting_pos_reports_no_filter(self):
        resp = api.complete(q="mang", api_key=api.API_KEY)
        self.assertIsNone(resp.requested.pos)
        self.assertIsNone(resp.data.pos)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_unmatched_prefix_is_an_empty_success_not_an_error(self):
        resp = api.complete(q="zzzznotawordzzzz", api_key=api.API_KEY)
        self.assertTrue(resp.success)
        self.assertEqual(resp.data.matches, [])
        self.assertIsNotNone(resp.note)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_limit_is_clamped_not_rejected(self):
        self.assertEqual(len(api.complete(q="a", limit=0, api_key=api.API_KEY).data.matches), 1)
        self.assertEqual(len(api.complete(q="a", limit=-5, api_key=api.API_KEY).data.matches), 1)
        self.assertLessEqual(
            len(api.complete(q="a", limit=500, api_key=api.API_KEY).data.matches), 100
        )

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_accent_insensitive_query(self):
        resp = api.complete(q="citta", pos="noun", api_key=api.API_KEY)
        self.assertIn("città", resp.data.matches)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_substring_is_opt_in(self):
        # `ttà` folds to `tta`. Verified against the built dictionary: no verb
        # starts with `tta` (so the prefix path finds nothing), but many contain
        # it (`abballotta`, `abballottai`, ...), so the substring path does not.
        prefix_only = api.complete(q="ttà", pos="verb", api_key=api.API_KEY).data.matches
        with_substring = api.complete(
            q="ttà", pos="verb", substring=True, limit=10, api_key=api.API_KEY
        ).data.matches
        self.assertEqual(prefix_only, [])
        self.assertTrue(with_substring)
        self.assertTrue(any(not fold(w).startswith("tta") for w in with_substring))


class TestCompleteHTTP(unittest.TestCase):
    """The real HTTP surface, through Starlette's ASGI stack.

    The tests above call the endpoint function directly. These go over HTTP, so
    they prove the status codes, headers and JSON bodies a client actually
    receives — which is the part a direct call cannot vouch for.
    """

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        # Deliberately NOT used as a context manager: that would run the
        # lifespan (database checks, contract check, index build), none of which
        # these tests are about. Without startup, `complete` still works because
        # it calls ensure_index() lazily.
        cls.client = TestClient(api.app)

    def _get(self, **params):
        return self.client.get(
            "/complete", params=params, headers={"X-API-Key": api.API_KEY}
        )

    def test_missing_key_is_401(self):
        resp = self.client.get("/complete", params={"q": "cane"})
        self.assertEqual(resp.status_code, 401)

    def test_blank_query_is_400(self):
        resp = self._get(q="   ")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("'q'", resp.json()["error"])

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_unknown_pos_is_400(self):
        resp = self._get(q="mang", pos="bogus")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("bogus", resp.json()["error"])

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_success_is_200_with_the_documented_shape(self):
        resp = self._get(q="citt", pos="noun", limit=5)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["requested"]["q"], "citt")
        self.assertEqual(body["requested"]["pos"], ["noun"])
        self.assertEqual(body["requested"]["limit"], 5)
        self.assertIs(body["requested"]["substring"], False)
        self.assertEqual(body["data"]["queried"], "citt")
        self.assertEqual(body["data"]["pos"], ["noun"])
        self.assertEqual(
            body["data"]["matches"],
            ["città", "città santa", "città sante", "città stato", "città vecchia"],
        )
```

- [ ] **Step 2: Run the endpoint tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_autocomplete.TestCompleteEndpoint tests.test_autocomplete.TestCompleteHTTP -v`

Expected: FAIL — `AttributeError: module 'app.api' has no attribute 'complete'`,
and for the HTTP tests `404 != 200` / `404 != 401`.

- [ ] **Step 3: Add the models**

In `app/models.py`, append after the `DefineResponse` class:

```python
# ---- Autocomplete (/complete) models ----
class CompleteQuery(BaseModel):
    q: constr(min_length=1) = Field(..., description="Partial word to complete")
    pos: list[str] | None = Field(
        None,
        description=(
            "Restrict to these parts of speech, e.g. verb or noun. Values are "
            "case-sensitive and match the dictionary's POS names. Omitted means "
            "every part of speech."
        ),
    )
    limit: int = Field(20, description="Maximum completions; clamped to 1-100")
    substring: bool = Field(
        False, description="If true, match anywhere in the word instead of as a prefix"
    )


class CompletionData(BaseModel):
    queried: str = Field(..., description="The q that was searched for, verbatim")
    pos: list[str] | None = Field(
        None, description="The POS filter actually applied; null when every POS was searched"
    )
    matches: list[str] = Field(..., description="Completing words, alphabetically")


class CompleteResponse(BaseModel):
    success: bool
    requested: CompleteQuery | None = None
    note: str | None = None
    error: str | None = None
    data: CompletionData | None = None
```

- [ ] **Step 4: Add the route**

In `app/api.py`, add `autocomplete_core` to the imports — the existing
`from .db_core import get_conjugations` sits between `from .config import ...`
and `from .dictionary_core import get_definitions`, so insert alongside it:

```python
from . import autocomplete_core
from .config import DICTIONARY_DB_PATH, SWAGGER_PATH, VERBS_DB_PATH
```

and extend the models import:

```python
from .models import (
    APIResponse,
    CompleteQuery,
    CompleteResponse,
    CompletionData,
    ConjugateQuery,
    ConjugationResponse,
    DefineResponse,
    DefinitionResponse,
    HealthResponse,
)
```

Then append the route at the end of `app/api.py`:

```python
@app.get(
    "/complete",
    response_model=CompleteResponse,
    tags=["dictionary"],
    summary="Prefix-complete an Italian word",
    responses={401: {"description": "Invalid or missing X-API-Key"}},
)
def complete(
    q: str = Query(..., min_length=1, description="Partial word to complete"),
    pos: str | None = Query(
        None,
        description="CSV of parts of speech to restrict to (e.g. verb,noun); omitted = all",
    ),
    limit: int = Query(20, description="Maximum completions, clamped to 1-100"),
    substring: bool = Query(
        False, description="If true, match anywhere in the word instead of as a prefix"
    ),
    api_key: str | None = Security(api_key_header),
):
    """Return dictionary words beginning with ``q``, for autocomplete.

    Matching ignores case and accents, so ``citta`` finds ``città``. Pass ``pos``
    to restrict to parts of speech — a conjugation client wants ``pos=verb``.
    With ``substring=true`` ``q`` may appear anywhere in the word.

    An unmatched prefix is not an error: the response is HTTP 200 with
    ``success: true`` and an empty ``matches`` list.
    """
    if not API_KEY or api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    index = autocomplete_core.ensure_index()

    query = q.strip()
    if not query:
        return JSONResponse(
            status_code=400,
            content=CompleteResponse(
                success=False, error="Parameter 'q' must not be empty."
            ).model_dump(),
        )

    pos_list = _csv_to_list(pos)
    unknown = [p for p in (pos_list or []) if p not in index.buckets]
    if unknown:
        return JSONResponse(
            status_code=400,
            content=CompleteResponse(
                success=False,
                error=(
                    f"Unknown part(s) of speech: {', '.join(unknown)}. "
                    f"Allowed: {', '.join(index.pos_values())}"
                ),
            ).model_dump(),
        )

    matches = index.complete(query, pos=pos_list, limit=limit, substring=substring)
    return CompleteResponse(
        success=True,
        requested=CompleteQuery(
            q=query, pos=pos_list, limit=limit, substring=substring
        ),
        note=None if matches else "No completions for this prefix.",
        data=CompletionData(queried=query, pos=pos_list, matches=matches),
    )
```

- [ ] **Step 5: Teach the existing contract test about `/complete`**

In `tests/test_dictionary.py`, inside `TestOpenAPIContract`, update two methods —
both assert against the live app, so they fail until `/complete` is listed:

```python
    def test_lists_all_endpoints(self):
        self.assertEqual(
            sorted(self.spec["paths"]),
            ["/complete", "/conjugate", "/define", "/health"],
        )
```

```python
    def test_secured_endpoints_require_the_key(self):
        for path in ("/complete", "/conjugate", "/define"):
            self.assertIn("security", self.spec["paths"][path]["get"],
                          f"{path} should declare security")
```

Add one more method to that class:

```python
    def test_complete_request_and_response_are_typed(self):
        get = self.spec["paths"]["/complete"]["get"]
        names = [p["name"] for p in get["parameters"]]
        self.assertEqual(names, ["q", "pos", "limit", "substring"])
        ref = get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        self.assertTrue(ref.endswith("/CompleteResponse"))
        schemas = self.spec["components"]["schemas"]
        for name in ("CompleteQuery", "CompletionData", "CompleteResponse"):
            self.assertIn(name, schemas)
```

- [ ] **Step 6: Run the tests to verify the contract failure**

Run: `.venv/bin/python -m unittest tests.test_dictionary.TestOpenAPIContract -v`

Expected: PASS — these assert against `app.openapi()`, generated live.

Now run: `.venv/bin/python -m unittest tests.test_dictionary.TestStartupDatabaseCheck.test_contract_in_sync_when_file_present -v`

Expected: FAIL — the checked-in `swagger.json` no longer matches the app's live
OpenAPI document. This is the intended guard, and it is why the next step is not
optional.

- [ ] **Step 7: Regenerate the contract**

```bash
task swagger
```

Expected output names four paths: `/complete, /conjugate, /define, /health`.

- [ ] **Step 8: Run the tests to verify the contract failure is resolved**

Run: `.venv/bin/python -m unittest tests.test_dictionary.TestStartupDatabaseCheck -v`

Expected: PASS, including `test_contract_in_sync_when_file_present` and
`test_starts_when_databases_present`.

- [ ] **Step 9: Run the whole suite**

Run: `task test`

Expected: `Ran 93 tests ... OK`.

- [ ] **Step 10: Commit**

```bash
git add app/models.py app/api.py requirements.txt swagger.json tests/test_dictionary.py tests/test_autocomplete.py
git commit -m "feat(api): add the /complete prefix autocomplete endpoint"
```

---

### Task 4: Build the index eagerly at startup, and report it on `/health`

Right now the index is built by whichever request arrives first. That moves a
~0.8 s, ~120 MB cost onto a user-facing request, which is the wrong place for
it. Build it in `lifespan` instead, alongside the existing database checks, and
surface its shape on `/health` so a broken index is visible from outside.

**Files:**
- Modify: `app/api.py`
- Modify: `app/models.py`
- Modify: `tests/test_autocomplete.py`
- Modify: `tests/test_dictionary.py`
- Modify: `swagger.json` (regenerated)

**Interfaces:**
- Consumes: `autocomplete_core.ensure_index()`, `autocomplete_core.index_stats()`;
  the existing `api.lifespan`, `api.health`, `api._missing_databases`,
  `api._contract_problem`.
- Produces: `models.HealthResponse.ok/databases/autocomplete`; the index is built
  before `lifespan` yields, so no request pays for it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_autocomplete.py`:

```python
class TestIndexStartup(unittest.TestCase):
    """The index is derived from dictionary.db, so it can never go stale."""

    def _run_lifespan(self):
        import asyncio

        async def go():
            async with api.lifespan(api.app):
                return True

        return asyncio.run(go())

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_lifespan_builds_the_index(self):
        autocomplete_core._index = None
        try:
            self.assertTrue(self._run_lifespan())
            self.assertIsNotNone(autocomplete_core._index)
        finally:
            autocomplete_core._index = None

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_lifespan_is_happy_when_the_index_is_already_built(self):
        autocomplete_core.ensure_index()
        self.assertTrue(self._run_lifespan())

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_health_reports_index_stats(self):
        autocomplete_core.ensure_index()
        health = api.health()
        self.assertTrue(health.ok)
        self.assertEqual(health.autocomplete["parts_of_speech"], 23)
        self.assertGreater(health.autocomplete["keys"], 600_000)
        self.assertIn("build_ms", health.autocomplete)
```

And add to `TestCompleteEndpoint` in the same file:

```python
    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_the_endpoint_does_not_pay_for_the_build(self):
        # Startup builds the index, so the endpoint must find it already there.
        autocomplete_core.ensure_index()
        before = autocomplete_core.index_stats()["build_ms"]
        api.complete(q="mang", pos="verb", api_key=api.API_KEY)
        self.assertEqual(autocomplete_core.index_stats()["build_ms"], before)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m unittest tests.test_autocomplete.TestIndexStartup -v`

Expected: FAIL — `AttributeError: 'HealthResponse' object has no attribute 'autocomplete'`.

- [ ] **Step 3: Add the stats field to the health model**

In `app/models.py`, replace the `HealthResponse` class:

```python
class HealthResponse(BaseModel):
    ok: bool
    databases: dict[str, bool] = Field(
        ..., description="Presence of each required SQLite database"
    )
    autocomplete: dict[str, int] | None = Field(
        None,
        description=(
            "Prefix index stats: parts_of_speech, keys, build_ms. "
            "null when the index has not been built yet."
        ),
    )
```

- [ ] **Step 4: Build the index in the lifespan and report it on /health**

In `app/api.py`, in `lifespan`, insert before the `yield`:

```python
    # The prefix index is rebuilt from dictionary.db on every start, so it can
    # never be stale — there is no artifact to invalidate. ~0.8 s and ~120 MB
    # on the current dictionary, paid here rather than by the first request.
    autocomplete_core.ensure_index()
    yield
```

Then replace `health`:

```python
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        databases={
            label: os.path.exists(path) for label, path in REQUIRED_DATABASES.items()
        },
        # Deliberately does not call ensure_index(): a liveness probe must not
        # trigger a 0.8 s build.
        autocomplete=autocomplete_core.index_stats(),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_autocomplete -v`

Expected: PASS — 48 tests.

- [ ] **Step 6: Regenerate the contract again**

`HealthResponse` changed shape, so `swagger.json` is stale again.

Run: `.venv/bin/python -m unittest tests.test_dictionary.TestStartupDatabaseCheck.test_contract_in_sync_when_file_present -v`

Expected: FAIL — "out of date with the running app".

Then run: `task swagger`

Then re-run the same test.

Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `task test`

Expected: `Ran 98 tests ... OK`.

- [ ] **Step 8: Confirm the app boots and the endpoint answers over real HTTP**

Startup is part of what is being verified here, so this one *does* run the
lifespan (`TestClient` as a context manager). No server, no background process,
no new dependency — just `httpx` driving the real ASGI stack:

```bash
SCRAPER_API_KEY=dev-key .venv/bin/python -c "
import json
from fastapi.testclient import TestClient
from app.api import app
h = {'X-API-Key': 'dev-key'}
with TestClient(app) as c:          # runs the lifespan: DB checks, contract check, index build
    print('health  ', json.dumps(c.get('/health').json()['autocomplete']))
    r = c.get('/complete', params={'q': 'mangiare', 'pos': 'verb', 'limit': 3}, headers=h)
    print('complete', r.status_code, json.dumps(r.json()['data']['matches'], ensure_ascii=False))
    r = c.get('/complete', params={'q': 'citta', 'pos': 'noun', 'limit': 3}, headers=h)
    print('accents ', r.status_code, json.dumps(r.json()['data']['matches'], ensure_ascii=False))
"
```

Expected: `health` reports
`{"parts_of_speech": 23, "keys": 603520, "build_ms": <a few hundred>}`; the first
`complete` returns `["mangiare", "mangiare a quattro ganasce", "mangiare la foglia"]`;
the second returns `["città", "città santa", "città sante"]` — the accent-folded
query finding the accented spelling.

- [ ] **Step 9: Commit**

```bash
git add app/api.py app/models.py swagger.json tests/test_autocomplete.py
git commit -m "feat(autocomplete): build the index at startup and report it on /health"
```

---

### Task 5: Document `/complete`, then verify the whole thing

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the finished endpoint from Tasks 1–4.
- Produces: nothing code-facing; this task's deliverable is the documented,
  verified feature.

- [ ] **Step 1: Add the endpoint to the README table**

In `README.md`, the "Endpoints at a glance" table currently ends with the
`/define` row. Add a row after it:

```markdown
| GET    | `/complete?q=`    | yes  | Words starting with a prefix, POs filterable |
```

Also update the sentence directly under that table, which currently reads
"Both data endpoints return **HTTP 200** for a well-formed request even when the
result is empty" — change "Both data endpoints" to "All three data endpoints".

- [ ] **Step 2: Add the `/complete` section**

In `README.md`, add this section immediately after the `/define` section (before
the section that begins "### Error handling" — search for it; if the heading
differs, place it after the `/define` examples and before the next `##` heading):

````markdown
### `GET /complete`

Autocomplete: given a partial word, returns dictionary words that start with it.
Use it to drive a dropdown and then call `/define` or `/conjugate` with the
chosen word.

| Query param | Type   | Default | Notes |
|-------------|--------|---------|-------|
| `q`         | string | —       | **required**; the partial word |
| `pos`       | CSV    | all     | restrict to parts of speech, e.g. `verb` or `verb,noun` |
| `limit`     | int    | `20`    | maximum completions; clamped to 1–100 |
| `substring` | bool   | `false` | match anywhere in the word, not just at the start |

Matching ignores case and accents: `citta` finds `città`. `pos` values are
case-sensitive and are the dictionary's POS names — `verb`, `noun`, `adj`,
`name`, `adv`, `suffix`, `prep_phrase`, `prefix`, `intj`, `pron`, `phrase`,
`conj`, `num`, `det`, `prep`, `proverb`, `contraction`, `character`, `article`,
`symbol`, `punct`, `particle`, `interfix`. An unknown value is a `400` that lists
them.

Results are ordered by their case- and accent-folded form. A conjugation client
wants `pos=verb`:

```bash
curl -H "X-API-Key: $KEY" "$API/complete?q=citt&pos=noun&limit=5"
```

```json
{
  "success": true,
  "requested": {"q": "citt", "pos": ["noun"], "limit": 5, "substring": false},
  "data": {
    "queried": "citt",
    "pos": ["noun"],
    "matches": ["città", "città santa", "città sante", "città stato", "città vecchia"]
  }
}
```

**Prefix, not substring.** `q=ttà` folds to `tta` and matches nothing by
default, because no word starts with `tta`. Pass `substring=true` and it finds
`abballotta`, `abballottai`, and the rest. That path scans the whole index
(about 10 ms in the worst case, versus tens of microseconds for a prefix), so
use it on explicit request only.

**Ordering is alphabetical, not by usefulness.** For `q=mangi&pos=verb` the
infinitive `mangiare` is the 37th match, behind inflected forms like `mangiai`
and `mangiammo`. That is the documented trade-off of ordering by folded key; if
a conjugation dropdown needs infinitives first, that is a ranking change to make
deliberately rather than something this endpoint guesses.

**An unmatched prefix is not an error.** The response is `200` with
`success: true`, `matches: []`, and a `note` — an unknown prefix is a normal
answer to a normal question. Only a blank `q` or an unknown `pos` is a `400`.
````

- [ ] **Step 3: Verify the documented examples by hand**

Confirm every claim the README now makes, through the real HTTP surface:

```bash
SCRAPER_API_KEY=dev-key .venv/bin/python -c "
import json
from fastapi.testclient import TestClient
from app.api import app
h = {'X-API-Key': 'dev-key'}
with TestClient(app) as c:
    r = c.get('/complete', params={'q': 'citt', 'pos': 'noun', 'limit': 5}, headers=h)
    print('README example :', json.dumps(r.json()['data']['matches'], ensure_ascii=False))
    r = c.get('/complete', params={'q': 'ttà', 'pos': 'verb'}, headers=h)
    print('prefix only    :', r.status_code, r.json()['data']['matches'], '|', r.json()['note'])
    r = c.get('/complete', params={'q': 'ttà', 'pos': 'verb', 'substring': 'true', 'limit': 3}, headers=h)
    print('with substring :', r.status_code, json.dumps(r.json()['data']['matches'], ensure_ascii=False))
    r = c.get('/complete', params={'q': 'mangi', 'pos': 'Bogus'}, headers=h)
    print('bad pos        :', r.status_code, r.json()['error'][:80])
"
```

Expected, in order:

1. `["città", "città santa", "città sante", "città stato", "città vecchia"]` —
   must match the README's JSON example exactly.
2. `200 [] | No completions for this prefix.`
3. `200 ["abballotta", "abballottai", "abballottammo"]`
4. `400 Unknown part(s) of speech: Bogus. Allowed: adj, adv, article, …`

Correct the README if any line does not match reality — the README is the
deliverable here, and an example that cannot be reproduced is worse than none.

- [ ] **Step 4: Rebuild the databases and run the full suite from scratch**

The index reads `dictionary.db` directly, so the strongest end-to-end check is
the documented build path followed by the documented test command.

Run: `task db`

Expected: `db:verbs` and `db:dictionary` — both report up to date (their
`Taskfile` checksums are unchanged, so no 761 MB download).

Run: `task test`

Expected: `Ran 98 tests ... OK`. If any test fails only when the databases were
rebuilt, the index build has a dependency on table row order; investigate rather
than reordering the test.

- [ ] **Step 5: Review the final diff**

Run: `git diff --stat HEAD~4` (four commits back — Task 1 through Task 5's docs)

Expected exactly these files, nothing else:

```
 README.md                      |  ...
 app/api.py                     |  ...
 app/autocomplete_core.py       |  ...
 app/models.py                  |  ...
 docs/...                       |  ...   (the spec and plan, already committed)
 requirements.txt               |  ...   (one added line: httpx)
 swagger.json                   |  ...
 tests/test_autocomplete.py     |  ...
 tests/test_dictionary.py       |  ...
```

Confirm no scratch file, no stray probe script, and **no change to
`Taskfile.yml` or `Dockerfile`** — the design deliberately adds no new artifact
to build, ship, or invalidate. The only `requirements.txt` change is the single
`httpx` line from Task 3; if anything else moved in it, investigate.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document the /complete endpoint"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Endpoint, four params, auth, envelope | 3 |
| `data.pos` echoes the filter, `null` when omitted | 3 (test `test_omitting_pos_reports_no_filter`) |
| Empty match is `success: true` with a note | 3 |
| 400 on blank `q`; 400 on unknown `pos` listing allowed values | 3 |
| Runtime `pos` validation, not a Pydantic `Literal` | 3 (`if p not in index.buckets`) |
| `buckets` / `overrides` structure, folded keys | 1 |
| `fold` strips a final accent, unlike `clean_accents` | 1 (`test_strips_a_final_accent_that_clean_accents_preserves`) |
| No "all words" bucket; `heapq.merge` on demand | 1 (`_emit`) |
| Build from `dictionary.db`, streamed not `fetchall` | 1 (`build_index`) |
| No prebuilt file, no covering index, no Taskfile/Dockerfile change | 1, 5 Step 6 |
| Build eagerly in `lifespan` | 4 |
| Hot path skips the merge (10–20 µs) | 1 (`_emit`), 2 Step 5 (re-measured) |
| Substring is opt-in and slow-path documented | 2, 5 |
| `limit` clamped to 1–100, not rejected | 2 |
| Multi-word and proper-name entries present; `pos` narrows | 3 (`test_pos_filter_changes_the_result`) |
| `/health` reports index stats | 4 |
| HTTP-level status codes and bodies (401, 400, 200 shape) | 3 (`TestCompleteHTTP`) |
| `swagger.json` regenerated; app boots | 3 Step 7, 4 Step 6 |
| Ordering is folded-alphabetical, and pinned | 3 (`test_results_are_ordered_by_folded_key`) |
| Tests: fold, prefix, POS, accents, collisions, substring, limits, errors, consistency | 1, 2, 3 |
| README row and section | 5 |

One spec item is **deliberately not implemented as a test**: the "spelling
collisions" case (key `manco` resolving to `mancò` under `pos=verb` and `Manco`
under `pos=name`). `build_index` picks the first spelling in dump order, which
is stable for a given database but not something to freeze in a test — a
Wiktionary refresh could reorder it. The behaviour is documented in
`build_index`'s docstring instead. `TestIndexPrefix.test_prefix_returns_the_real_spelling`
covers the override mechanism itself.

**Placeholder scan:** every code step contains complete, runnable code; every
command step states its expected output. No "TBD", no "handle edge cases", no
"similar to Task N".

**Type consistency:** `Index.complete` is defined in Task 1 with
`(q, pos=None, limit=20)` and extended in Task 2 with `substring=False` —
`complete(q="ttà", pos="verb", substring=True, ...)` in Task 3's test uses the
keyword form, so the added parameter is compatible. `autocomplete_core._index`
is written in Task 2 and reset by a test in Task 4, which is the same name.
`index_stats()` returns `dict[str, int] | None` in Task 2 and is consumed as
such by `HealthResponse.autocomplete` in Task 4. `fold` is imported into the
test module in Task 1 and used by Task 3's tests.
Test counts, assuming every method above is added verbatim:
49 (baseline, measured) → 64 (Task 1) → 77 (Task 2) → 93 (Task 3) → 98 (Task 4, after the
review fixes below added one test).

**Corrections already applied to this plan.** Each was a real defect found by
running the plan's own code and commands, not a style preference:

- `test_substring_is_opt_in` used `q=are` and asserted the prefix path returns
  `[]`. It does not: 20 verb keys start with `are` (`areare`, `arenai`, …).
  Replaced with `q=ttà`, which folds to `tta` and genuinely has zero prefix
  matches but many substring matches.
- `test_completes_a_verb_prefix` used `q=mangi` and asserted `mangiare` is in the
  default `limit=20` results. It is the 37th of 138 matches. Changed to
  `q=mangiare`.
- Task 4's and Task 5's smoke tests used `uvicorn … &` with `curl` and `kill %1`,
  which can strand a server or fail on process-group reaping, and
  `fastapi.testclient` is unusable without `httpx`. Replaced with `TestClient`
  (and `httpx` added to `requirements.txt`, which the user approved).
- Task 4's review fixes (applied after review, so the counts above include them): the startup
  index build is wrapped so a present-but-unreadable `dictionary.db` fails startup with the same
  curated "The API cannot start … Fix, then restart" message as the sibling checks, instead of a
  raw traceback; `/health` gained a test that pins "reports without building"; and the two
  `TestIndexStartup` methods that mutate the `autocomplete_core._index` / `_build_ms` globals now
  save and restore them instead of forcing `None`.
- Task 5 previously ended with a step that moved `dictionary.db` aside to check
  the startup guard. It contained a leftover no-op line and would have
  invalidated Task's `db:dictionary` checksum, risking a surprise multi-minute
  rebuild on the next `task test`. Deleted: the guard is already covered by the
  existing `test_hard_fails_when_database_missing` and
  `test_hard_fails_when_contract_missing`.

**Assumptions checked against the real database.** Every expected value that
depends on the built dictionary was measured, not guessed:

- `verb` bucket holds 384,651 keys; the whole index holds 603,520 keys across
  23 POS values, with 45,261 spelling overrides (`Index.count()`).
- 138 verb keys start with `mangi`, and `mangiare` is the **37th** — so
  `test_completes_a_verb_prefix` uses `q=mangiare`, not `q=mangi`, which would
  not reach it inside the default limit of 20.
- `q=tta` (the fold of `ttà`) matches **zero** verbs as a prefix but many as a
  substring, so it is the example used for the opt-in test and the README.
  The earlier draft of this plan used `q=are`, which is wrong: 20 verb keys
  start with `are` (`areare`, `arenai`, …).
- `q=citt&pos=noun` returns
  `["città", "città santa", "città sante", "città stato", "città vecchia"]`,
  which is the README's example output.

**Known limitation, deliberately not fixed — RESOLVED by a follow-up.** Ordering
was by folded key only, so for `q=mangi&pos=verb` the infinitive `mangiare` was
the 37th match, behind inflected forms such as `mangiai` and `mangiammo`. That
followed from the approved spec ("alphabetical; no ranking") and was documented in
the README rather than worked around.

Follow-up implemented (verbs only): each bucket is now split into a *canonical*
half and an *inflected* half, and a query emits the canonical half first, so
`mangiare` is 3rd of 20 for the prefix `mang`. Measurement killed two candidate
fixes before either shipped: `is_lemma`-first ordering does **not** work
(Wiktionary marks pronominal verbs — `abbacchiarsi`, `mangiarla`,
`abbacchiandoci` — as lemmas, so the infinitive stayed outside the top 20), and
`verbs.db`'s `infinitive` column is not a plain-infinitive list either. The rule
that works is `is_lemma AND an infinitive ending`, where the endings include the
`-si` reflexives: 13,141 of 384,651 verb keys. See the spec's "Canonical-first
ordering" section. The test that pinned the old ordering was renamed to
`test_results_are_canonical_first_then_alphabetical` — the deliberate change that
named behaviour was always going to require.

A code review of that follow-up caught a real gap the first cut had: the plain
endings alone dropped reflexive infinitives, and for inherently-pronominal verbs
(`pentirsi`, `accorgersi`, `mettersi`) the reflexive is the *only* infinitive —
`mettersi` sat outside a default `limit` for its own prefix. Adding
`-arsi`/`-ersi`/`-irsi` fixed that (`pentirsi` 1st, `sedersi` 2nd) while leaving
`mangiare` at 3rd, and `is_lemma` kept excluding lookalikes such as `apersi`
(passato remoto of `aprire`). 132 reflexive keys Wiktionary records as pure forms
(`ricordarsi`) remain unboosted — an upstream data limitation, documented in the
spec, not worked around with a looser heuristic.
