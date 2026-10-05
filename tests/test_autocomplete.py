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

from app import autocomplete_core, dictionary_core  # noqa: E402
from app.autocomplete_core import (  # noqa: E402
    Index,
    _canonical_order,
    _inflected_order,
    _is_canonical,
    fold,
)

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

    Every key sits in its bucket's *canonical* half, so these tests pin the
    canonical half's shortest-first order. :func:`_ranked_index` covers the
    canonical / inflected split.
    """
    return Index(
        buckets={
            "verb": (sorted(["mangiare", "mangi", "mangia", "mangiai", "mancare", "cane"]), []),
            "noun": (sorted(["cane", "citta", "cittadino", "mancanza"]), []),
        },
        overrides={"noun": {"citta": "città"}},
    )


def _ranked_index():
    """A tiny index whose verb bucket really is split canonical / inflected.

    ``mangiare`` and ``mancare`` are the canonical (infinitive) verbs; ``mangi``,
    ``mangia``, ``mangiai``, ``mangiammo`` and ``mancai`` are inflected forms.
    ``mangi``/``mangia``/``mangiai``/``mangiammo`` sort *before* ``mangiare``
    alphabetically, so canonical-first ordering is observable rather than
    accidental — and ``mancai`` shares the ``manc`` prefix of a canonical verb in
    a *different* POS, which exercises the multi-POS merge.
    """
    return Index(
        buckets={
            "verb": (
                sorted(["mangiare", "mancare"]),
                sorted(["mangi", "mangia", "mangiai", "mangiammo", "mancai"]),
            ),
            "noun": (sorted(["mancanza"]), []),
        },
        overrides={},
    )


def _compound_index():
    """An index mixing single words and compound phrases (entries with a space).

    ``città`` is a single noun; ``città santa`` and ``città vecchia`` are
    compounds that sort *before* it alphabetically. Single words must lead
    regardless, in both halves — ``mangiare`` before ``mangiare la polvere``, and
    the inflected ``mangia`` before ``mangia la mela``.
    """
    return Index(
        buckets={
            "verb": (
                sorted(["mangiare", "mangiare la polvere"]),
                sorted(["mangia", "mangia la mela"]),
            ),
            "noun": (sorted(["città", "città santa", "città vecchia"]), []),
        },
        overrides={},
    )


class TestCanonicalForm(unittest.TestCase):
    """Canonical forms (verb infinitives) must be offered before inflected ones."""

    def setUp(self):
        self.index = _ranked_index()

    def test_canonical_precedes_inflected_within_one_pos(self):
        # Alphabetically `mangi` < `mangiare`; canonical-first flips that.
        self.assertEqual(
            self.index.complete("mang", pos=["verb"]),
            ["mangiare", "mangi", "mangia", "mangiai", "mangiammo"],
        )

    def test_limit_can_be_filled_entirely_by_canonical_forms(self):
        self.assertEqual(self.index.complete("mang", pos=["verb"], limit=1), ["mangiare"])
        self.assertEqual(
            self.index.complete("mang", pos=["verb"], limit=2), ["mangiare", "mangi"]
        )

    def test_inflected_forms_are_still_reachable(self):
        # Nothing is dropped — the inflected forms simply follow.
        full = self.index.complete("mang", pos=["verb"], limit=100)
        for form in ("mangi", "mangia", "mangiai", "mangiammo"):
            self.assertIn(form, full)

    def test_canonical_precedes_inflected_across_a_multi_pos_merge(self):
        # Both `mancare` (verb) and `mancanza` (noun) are canonical, so they are
        # merged ahead of the inflected `mancai` — even though `mancai` sorts
        # between them alphabetically. `mancare` (7) precedes `mancanza` (8)
        # under the canonical half's shortest-first order.
        self.assertEqual(self.index.complete("manc"), ["mancare", "mancanza", "mancai"])
        self.assertEqual(self.index.complete("manc", pos=["verb"]), ["mancare", "mancai"])

    def test_substring_respects_canonical_first(self):
        self.assertEqual(
            self.index.complete("ang", pos=["verb"], substring=True),
            ["mangiare", "mangi", "mangia", "mangiai", "mangiammo"],
        )

    def test_no_duplicate_when_a_word_is_canonical_in_one_pos_and_not_another(self):
        index = Index(buckets={"verb": (["cane"], []), "noun": ([], ["cane"])}, overrides={})
        self.assertEqual(index.complete("cane"), ["cane"])

    def test_count_and_pos_values_span_both_halves(self):
        self.assertEqual(self.index.count(), 8)
        self.assertEqual(self.index.pos_values(), ["noun", "verb"])

    def test_non_verb_buckets_are_not_split(self):
        self.assertEqual(_tiny_index().complete("citt", pos=["noun"]), ["città", "cittadino"])


class TestCompoundOrdering(unittest.TestCase):
    """Compound phrases (entries containing a space) sort below single words."""

    def setUp(self):
        self.index = _compound_index()

    def test_single_word_precedes_compounds_in_the_canonical_half(self):
        # `città santa` and `città vecchia` sort before `città` alphabetically,
        # but the single word must lead.
        self.assertEqual(
            self.index.complete("citt", pos=["noun"]),
            ["città", "città santa", "città vecchia"],
        )

    def test_single_word_precedes_compound_across_pos(self):
        self.assertEqual(
            self.index.complete("citt"),
            ["città", "città santa", "città vecchia"],
        )

    def test_canonical_single_word_precedes_canonical_compound(self):
        self.assertEqual(
            self.index.complete("mang", pos=["verb"], limit=2),
            ["mangiare", "mangiare la polvere"],
        )

    def test_inflected_single_word_precedes_inflected_compound(self):
        # `mangia la mela` sorts before `mangia` alphabetically; it must not.
        self.assertEqual(
            self.index.complete("mang", pos=["verb"]),
            ["mangiare", "mangiare la polvere", "mangia", "mangia la mela"],
        )

    def test_compounds_are_still_reachable(self):
        words = self.index.complete("citt", pos=["noun"], limit=100)
        for compound in ("città santa", "città vecchia"):
            self.assertIn(compound, words)


class TestIsCanonical(unittest.TestCase):
    """The verb/infinitive rule the split depends on."""

    def test_verb_infinitives_are_canonical(self):
        for key in ("mangiare", "credere", "finire", "porre", "tradurre", "narrare"):
            self.assertTrue(_is_canonical("verb", key, True), key)

    def test_reflexive_infinitives_are_canonical(self):
        # For inherently-pronominal verbs the reflexive IS the dictionary form,
        # so it must be boosted; otherwise `pentirsi` sat outside a default
        # limit for the prefix `pentir`.
        for key in ("pentirsi", "accorgersi", "suicidarsi", "mettersi", "lavarsi"):
            self.assertTrue(_is_canonical("verb", key, True), key)

    def test_verb_inflected_forms_are_not_canonical(self):
        for key in ("mangiai", "mangiammo", "mangiato", "mangiando", "cane"):
            self.assertFalse(_is_canonical("verb", key, True), key)

    def test_clitic_and_gerund_lemmas_are_not_canonical(self):
        # The finding that made `is_lemma`-first ordering useless: Wiktionary
        # marks these as lemmas, yet none is the infinitive a picker keys on.
        for key in ("mangiarla", "mangiamole", "abbacchiandoci", "mangiandosi"):
            self.assertFalse(_is_canonical("verb", key, True), key)
        # ...while real infinitives, plain and reflexive, are canonical.
        self.assertTrue(_is_canonical("verb", "mangiare", True))
        self.assertTrue(_is_canonical("verb", "mangiarsi", True))

    def test_a_non_lemma_verb_key_is_never_canonical(self):
        self.assertFalse(_is_canonical("verb", "mangiare", False))
        # `apersi` is passato remoto of `aprire`; it shares the `-ersi` ending
        # with real reflexives, and only the lemma flag keeps it out.
        self.assertFalse(_is_canonical("verb", "apersi", False))

    def test_other_parts_of_speech_are_all_canonical(self):
        for pos in ("noun", "adj", "adv", "name"):
            self.assertTrue(_is_canonical(pos, "qualunque", False), pos)


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
        # `mancare` (7) precedes `mancanza` (8): the canonical half is
        # shortest-first, and both are canonical, so length decides.
        self.assertEqual(self.index.complete("manc"), ["mancare", "mancanza"])

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
            self.index.complete("anc", substring=True), ["mancare", "mancanza"]
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

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_every_bucket_is_split_into_disjoint_ordered_halves(self):
        # The invariant that keeps `count()` equal to the number of distinct
        # (word, pos) keys: a key that is both a lemma and a form would otherwise
        # land in both halves. Each half is deduped and sorted by its own order.
        index = autocomplete_core.ensure_index()
        for pos, (canonical, other) in index.buckets.items():
            self.assertEqual(canonical, sorted(set(canonical), key=_canonical_order), pos)
            self.assertEqual(other, sorted(set(other), key=_inflected_order), pos)
            self.assertFalse(set(canonical) & set(other), f"{pos} halves overlap")
        self.assertLessEqual(index.count(), 611_169)

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_only_the_verb_bucket_is_split(self):
        index = autocomplete_core.ensure_index()
        self.assertTrue(index.buckets["verb"][1], "verb bucket should have inflected keys")
        for pos, (canonical, other) in index.buckets.items():
            if pos != "verb":
                self.assertFalse(other, f"{pos} should not be split")


class TestCompleteEndpoint(unittest.TestCase):
    """The endpoint gates on the API key exactly like /conjugate and /define."""

    def test_requires_api_key(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            api.complete(q="cane", api_key=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_a_blank_query(self):
        import json

        # The blank check must run BEFORE the index is consulted: a blank `q`
        # must be a 400 even if the index cannot be built at all.
        def _must_not_be_called():
            raise AssertionError("ensure_index must not run for a blank q")

        original = api.autocomplete_core.ensure_index
        api.autocomplete_core.ensure_index = _must_not_be_called
        try:
            resp = api.complete(q="   ", api_key=api.API_KEY)
        finally:
            api.autocomplete_core.ensure_index = original

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
        # A long prefix keeps this independent of the canonical-first ordering.
        resp = api.complete(q="mangiare", pos="verb", limit=5, api_key=api.API_KEY)
        self.assertTrue(resp.success)
        self.assertEqual(resp.requested.q, "mangiare")
        self.assertEqual(resp.requested.pos, ["verb"])
        self.assertEqual(resp.data.queried, "mangiare")
        self.assertEqual(resp.data.pos, ["verb"])
        self.assertIn("mangiare", resp.data.matches)
        self.assertTrue(all(fold(w).startswith("mangiare") for w in resp.data.matches))

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_results_are_canonical_first_then_ranked(self):
        # The documented ordering: verb infinitives first, then inflected forms.
        # Within each group single words precede compounds; the canonical group
        # is shortest-first, the inflected group alphabetical. This pins it, so
        # any future ranking change is a deliberate test change.
        matches = api.complete(q="mang", pos="verb", limit=100, api_key=api.API_KEY)
        words = matches.data.matches
        self.assertEqual(len(words), 100)
        index = autocomplete_core.ensure_index()
        canonical = set(index._keys("verb", True))
        flags = [fold(w) in canonical for w in words]
        # Once an inflected form appears, no canonical form may follow it.
        self.assertEqual(flags, sorted(flags, reverse=True))
        # Each group is ordered by its half's key (single-word-first, and for
        # canonical forms shortest-first), and single words precede compounds.
        for wanted, order in ((True, _canonical_order), (False, _inflected_order)):
            keys = [fold(w) for w, is_canonical in zip(words, flags) if is_canonical is wanted]
            self.assertEqual(keys, sorted(keys, key=order))
            ranks = [0 if " " not in k else 1 for k in keys]
            self.assertEqual(ranks, sorted(ranks))

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_the_infinitive_surfaces_for_the_prefix_a_client_types(self):
        # The regression this ordering exists for: `mang` is what a conjugation
        # client sends, and `mangiare` must be inside a default-sized window
        # rather than 37th behind `mangiai`, `mangiammo` and the rest.
        matches = api.complete(q="mang", pos="verb", limit=5, api_key=api.API_KEY)
        self.assertIn("mangiare", matches.data.matches)

        wide = api.complete(q="mang", pos="verb", limit=100, api_key=api.API_KEY)
        words = wide.data.matches
        # `manganai` and `mangana` sort before `mangiare` alphabetically, yet
        # follow it here — that is the whole point of the split.
        self.assertLess(words.index("mangiare"), words.index("manganai"))
        self.assertLess(words.index("mangiare"), words.index("mangana"))

        # The narrower prefix puts it first outright.
        tight = api.complete(q="mangi", pos="verb", limit=5, api_key=api.API_KEY)
        self.assertEqual(tight.data.matches[0], "mangiare")

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_reflexive_infinitives_surface_too(self):
        # For inherently-pronominal verbs the reflexive is the dictionary form.
        # Without the `-si` endings `pentirsi` and `accorgersi` sat 16th-18th of
        # ~20, and `mettersi` was outside the window entirely.
        for verb, prefix in (
            ("pentirsi", "pentir"),
            ("accorgersi", "accorger"),
            ("mettersi", "metter"),
            ("lavarsi", "lavar"),
        ):
            matches = api.complete(q=prefix, pos="verb", limit=20, api_key=api.API_KEY)
            self.assertIn(verb, matches.data.matches, f"{verb} missing for {prefix}")

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_inflected_verb_forms_are_still_reachable(self):
        # Canonical-first reorders results; it must not remove any of them.
        words = api.complete(
            q="mang", pos="verb", limit=100, api_key=api.API_KEY
        ).data.matches
        self.assertEqual(len(words), 100)
        # Both halves are represented: the infinitive group ...
        self.assertIn("manganare", words)
        # ... and inflected forms, which simply follow it.
        for form in ("mangana", "manganai", "manganato", "mangerà"):
            self.assertIn(form, words)

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

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_the_endpoint_does_not_pay_for_the_build(self):
        # Startup builds the index, so the endpoint must find it already there.
        autocomplete_core.ensure_index()
        before = autocomplete_core.index_stats()["build_ms"]
        api.complete(q="mang", pos="verb", api_key=api.API_KEY)
        self.assertEqual(autocomplete_core.index_stats()["build_ms"], before)


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
            ["città", "cittì", "citto", "cittade", "cittadi"],
        )


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
        saved_index = autocomplete_core._index
        saved_ms = autocomplete_core._build_ms
        try:
            autocomplete_core._index = None
            autocomplete_core._build_ms = None
            self.assertTrue(self._run_lifespan())
            self.assertIsNotNone(autocomplete_core._index)
        finally:
            autocomplete_core._index = saved_index
            autocomplete_core._build_ms = saved_ms

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_lifespan_is_happy_when_the_index_is_already_built(self):
        autocomplete_core.ensure_index()
        self.assertTrue(self._run_lifespan())

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_health_reports_index_stats(self):
        # /health must report the index WITHOUT building it: a liveness probe
        # must not pay the build. Before the index exists the field is null and
        # must stay that way.
        saved_index = autocomplete_core._index
        saved_ms = autocomplete_core._build_ms
        try:
            autocomplete_core._index = None
            autocomplete_core._build_ms = None
            api.health()
            self.assertIsNone(
                autocomplete_core.index_stats(),
                "/health must not build the index",
            )

            autocomplete_core.ensure_index()
            stats = api.health().autocomplete
            self.assertIsNotNone(stats)
            self.assertEqual(stats["parts_of_speech"], 23)
            self.assertGreater(stats["keys"], 600_000)
            self.assertIn("build_ms", stats)
        finally:
            autocomplete_core._index = saved_index
            autocomplete_core._build_ms = saved_ms

    @unittest.skipUnless(REPO_HAS_DICTIONARY, "dictionary.db not built — run `task db:dictionary`")
    def test_hard_fails_when_the_index_cannot_be_built(self):
        # A present but unreadable dictionary.db must fail startup with the
        # curated message, not a raw traceback.
        def _boom():
            raise ValueError("database disk image is malformed")

        original = autocomplete_core.ensure_index
        autocomplete_core.ensure_index = _boom
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._run_lifespan()
        finally:
            autocomplete_core.ensure_index = original

        message = str(ctx.exception)
        self.assertIn("cannot start", message)
        self.assertIn("task db", message)
        self.assertIn("database disk image is malformed", message)
