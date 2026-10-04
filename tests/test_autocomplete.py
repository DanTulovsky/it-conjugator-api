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
