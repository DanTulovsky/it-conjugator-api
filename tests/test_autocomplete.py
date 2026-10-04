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

from app import autocomplete_core, config, dictionary_core  # noqa: E402
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
