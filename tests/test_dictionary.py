"""
Tests for the full dictionary lookup (dictionary.db, ``/define``).

Covers three layers:

1. ``build_dictionary_db`` — the curation logic that turns a raw Kaikki
   Wiktionary entry into the compact blob we store.
2. ``app.dictionary_core.get_definitions`` — the lookup.
3. ``app.api`` — the ``/define`` endpoint and the startup database check.

These tests need a built ``dictionary.db`` (``task db:dictionary``). The
curation-logic tests do not.

Run the suite from the repo root::

    python3 -m unittest discover -s tests -v
"""

import asyncio
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import build_dictionary_db as builder  # noqa: E402
import build_db as builder_verbs  # noqa: E402
from app import config, dictionary_core  # noqa: E402

# The API key is read from the environment at import time; set it before we
# import app.api so the endpoints accept our test key.
TEST_API_KEY = "test-api-key"
os.environ.setdefault("SCRAPER_API_KEY", TEST_API_KEY)

import app.api as api  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Curation logic (no database required)
# ---------------------------------------------------------------------------
RAW_ENTRY = {
    "pos": "noun",
    "word": "cane",
    "lang": "Italian",
    "lang_code": "it",
    "etymology_number": 1,
    "etymology_text": "From the Latin canis, canem (“dog”).",
    "etymology_templates": [{"name": "inh", "expansion": "<broken html>"}],
    "head_templates": [
        {"name": "it-noun", "expansion": "cane m (plural cani, feminine cagna)"}
    ],
    "sounds": [{"ipa": "/ˈka.ne/"}, {"rhymes": "-ane"}],
    "hyphenation": ["cà‧ne"],
    "hyphenations": [{"parts": ["cà‧ne"]}],
    "forms": [{"form": "cani", "tags": ["plural"]}, {"form": "cagna", "tags": ["feminine"]}],
    "senses": [
        {"glosses": ["dog, male dog"], "tags": ["masculine"]},
        {"glosses": ["hammer"], "raw_glosses": ["(firearms) hammer"], "tags": ["masculine"]},
    ],
    "derived": [{"word": "cagnara"}],
}

FORM_OF_ENTRY = {
    "pos": "noun",
    "word": "cani",
    "senses": [{"glosses": ["plural of cane"], "form_of": [{"word": "cane"}]}],
}


class TestCuration(unittest.TestCase):

    def test_head_expansion_extracted(self):
        self.assertEqual(
            builder._head_expansions(RAW_ENTRY),
            ["cane m (plural cani, feminine cagna)"],
        )

    def test_is_form_of_detection(self):
        self.assertFalse(builder._is_form_of(RAW_ENTRY))
        self.assertTrue(builder._is_form_of(FORM_OF_ENTRY))

    def test_is_form_of_requires_all_senses_to_be_form_of(self):
        # An entry that also carries its own (non-form-of) sense is a lemma.
        mixed = {
            "pos": "noun",
            "word": "casa",
            "senses": [
                {"glosses": ["house"], "tags": ["feminine"]},
                {"glosses": ["form of casare"], "form_of": [{"word": "casare"}]},
            ],
        }
        self.assertFalse(builder._is_form_of(mixed))

    def test_is_form_of_true_when_all_senses_are_form_of(self):
        self.assertTrue(builder._is_form_of(FORM_OF_ENTRY))

    def test_curate_drops_raw_template_keys(self):
        curated = builder.curate_entry(RAW_ENTRY)
        # word/pos are stored as columns, not duplicated in the blob.
        self.assertNotIn("word", curated)
        self.assertNotIn("pos", curated)
        # Raw wikitext template noise is dropped ...
        for key in ("lang", "lang_code", "etymology_templates", "hyphenations",
                    "head_templates", "sounds"):
            self.assertNotIn(key, curated)
        # ... and replaced by the readable equivalents.
        self.assertEqual(curated["head"], ["cane m (plural cani, feminine cagna)"])
        self.assertEqual(curated["ipa"], ["/ˈka.ne/"])
        self.assertEqual(curated["rhymes"], ["-ane"])
        self.assertEqual(curated["hyphenation"], ["cà‧ne"])
        self.assertEqual(curated["etymology"], "From the Latin canis, canem (“dog”).")
        # Unrelated data is preserved verbatim.
        self.assertEqual(curated["etymology_number"], 1)
        self.assertEqual(curated["derived"], [{"word": "cagnara"}])

    def test_curate_senses_keep_glosses_and_drop_noise(self):
        curated = builder.curate_entry(RAW_ENTRY)
        self.assertEqual(
            curated["senses"][0], {"glosses": ["dog, male dog"], "tags": ["masculine"]}
        )
        self.assertEqual(curated["senses"][1]["raw_glosses"], ["(firearms) hammer"])

    def test_curate_forms(self):
        curated = builder.curate_entry(RAW_ENTRY)
        self.assertEqual(
            curated["forms"],
            [{"form": "cani", "tags": ["plural"]}, {"form": "cagna", "tags": ["feminine"]}],
        )

    def test_curate_form_of_entry_preserves_form_of(self):
        curated = builder.curate_entry(FORM_OF_ENTRY)
        self.assertEqual(curated["senses"][0]["form_of"], [{"word": "cane"}])

    def test_curate_omits_empty_sections(self):
        curated = builder.curate_entry({"pos": "intj", "word": "ehi", "senses": [{"glosses": ["hey"]}]})
        self.assertNotIn("head", curated)
        self.assertNotIn("ipa", curated)
        self.assertNotIn("forms", curated)


# ---------------------------------------------------------------------------
# 2. Lookup (requires dictionary.db)
# ---------------------------------------------------------------------------
@unittest.skipUnless(dictionary_core.db_is_present(),
                     "dictionary.db not built — run `task db:dictionary`")
class TestGetDefinitions(unittest.TestCase):

    @staticmethod
    def _glosses(entry):
        out = []
        for sense in entry.get("senses", []) or []:
            out.extend(sense.get("glosses", []) or [])
        return out

    def test_noun_lookup(self):
        data = dictionary_core.get_definitions("cane")
        self.assertIsNotNone(data)
        self.assertEqual(data["queried"], "cane")
        self.assertTrue(data["entries"])

        noun_glosses = [
            g for e in data["entries"] if e["pos"] == "noun" for g in self._glosses(e)
        ]
        self.assertTrue(any("dog" in g for g in noun_glosses), noun_glosses)

    def test_verb_lookup_has_senses_and_principal_parts(self):
        data = dictionary_core.get_definitions("mangiare")
        verb_entries = [e for e in data["entries"] if e["pos"] == "verb"]
        self.assertTrue(verb_entries)
        verb_glosses = [g for e in verb_entries for g in self._glosses(e)]
        self.assertTrue(any("to eat" in g for g in verb_glosses), verb_glosses)
        # Inflections are carried through from the dump.
        self.assertTrue(any(e.get("forms") for e in verb_entries))

    def test_entries_sorted_lemma_first(self):
        data = dictionary_core.get_definitions("cane")
        flags = [e["is_lemma"] for e in data["entries"]]
        self.assertEqual(flags, sorted(flags, reverse=True))

    def test_case_insensitive_fallback(self):
        data = dictionary_core.get_definitions("Cane")
        self.assertIsNotNone(data)
        self.assertTrue(data["entries"])

    def test_missing_word_returns_none(self):
        self.assertIsNone(
            dictionary_core.get_definitions("zzzznotawordzzzz")
        )


class TestGetDefinitionsWithoutDatabase(unittest.TestCase):
    """Lookup paths that must work (or fail cleanly) with no database present."""

    def test_empty_query_returns_none(self):
        # An empty query short-circuits before the database is ever opened.
        original = dictionary_core.DICTIONARY_DB_PATH
        dictionary_core.DICTIONARY_DB_PATH = os.path.join(ROOT, "does-not-exist.db")
        try:
            self.assertIsNone(dictionary_core.get_definitions("   "))
        finally:
            dictionary_core.DICTIONARY_DB_PATH = original

    def test_missing_database_raises_with_instructions(self):
        original = dictionary_core.DICTIONARY_DB_PATH
        dictionary_core.DICTIONARY_DB_PATH = os.path.join(ROOT, "does-not-exist.db")
        try:
            with self.assertRaises(FileNotFoundError) as ctx:
                dictionary_core.get_definitions("cane")
            self.assertIn("build_dictionary_db.py", str(ctx.exception))
        finally:
            dictionary_core.DICTIONARY_DB_PATH = original


# ---------------------------------------------------------------------------
# 3. API layer
# ---------------------------------------------------------------------------
class TestDefineEndpoint(unittest.TestCase):

    def test_requires_api_key(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            api.define(v="cane", api_key=None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_health_reports_database_presence(self):
        health = api.health()
        self.assertTrue(health.ok)
        self.assertEqual(
            set(health.databases),
            {"conjugations (verbs.db)", "dictionary (dictionary.db)"},
        )
        self.assertTrue(all(isinstance(v, bool) for v in health.databases.values()))

    def test_surfaces_build_instructions_when_database_absent(self):
        # If the DB disappears at runtime, /define must tell the client how to fix it
        # rather than crash or return a bare "not found".
        original = dictionary_core.DICTIONARY_DB_PATH
        dictionary_core.DICTIONARY_DB_PATH = os.path.join(ROOT, "does-not-exist.db")
        try:
            resp = api.define(v="cane", api_key=api.API_KEY)
            self.assertFalse(resp.success)
            self.assertIn("build_dictionary_db.py", resp.error)
        finally:
            dictionary_core.DICTIONARY_DB_PATH = original

    @unittest.skipUnless(dictionary_core.db_is_present(),
                         "dictionary.db not built — run `task db:dictionary`")
    def test_returns_entries(self):
        resp = api.define(v="cane", api_key=api.API_KEY)
        self.assertTrue(resp.success)
        self.assertEqual(resp.requested, "cane")
        self.assertTrue(resp.data.entries)
        self.assertEqual(resp.data.entries[0].word, "cane")

    @unittest.skipUnless(dictionary_core.db_is_present(),
                         "dictionary.db not built — run `task db:dictionary`")
    def test_unknown_word(self):
        resp = api.define(v="zzzznotawordzzzz", api_key=api.API_KEY)
        self.assertFalse(resp.success)
        self.assertIn("not found", resp.error.lower())

    @unittest.skipUnless(dictionary_core.db_is_present(),
                         "dictionary.db not built — run `task db:dictionary`")
    def test_preserves_undeclared_entry_fields(self):
        # Fields like 'derived'/'translations' are not declared on DictionaryEntry;
        # they must survive serialisation thanks to extra="allow".
        resp = api.define(v="cane", api_key=api.API_KEY)
        dumps = [e.model_dump() for e in resp.data.entries]
        self.assertTrue(
            any("derived" in d or "translations" in d for d in dumps),
            [sorted(d) for d in dumps],
        )


class TestStartupDatabaseCheck(unittest.TestCase):
    def _run_lifespan(self):
        async def go():
            async with api.lifespan(api.app):
                return True
        return asyncio.run(go())

    def test_starts_when_databases_present(self):
        missing = api._missing_databases()
        if missing:
            self.skipTest(f"databases not built: {missing}")
        self.assertTrue(self._run_lifespan())

    def test_hard_fails_when_database_missing(self):
        original = api.REQUIRED_DATABASES
        api.REQUIRED_DATABASES = {"dictionary (dictionary.db)": os.path.join(ROOT, "nope.db")}
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._run_lifespan()
            message = str(ctx.exception)
            self.assertIn("missing", message.lower())
            self.assertIn("task db", message)
        finally:
            api.REQUIRED_DATABASES = original

    def test_contract_in_sync_when_file_present(self):
        self.assertIsNone(api._contract_problem())

    def test_hard_fails_when_contract_missing(self):
        original = api.SWAGGER_PATH
        api.SWAGGER_PATH = os.path.join(ROOT, "no-such-swagger.json")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._run_lifespan()
            message = str(ctx.exception)
            self.assertIn("contract file not found", message)
            self.assertIn("task swagger", message)
        finally:
            api.SWAGGER_PATH = original

    def test_hard_fails_when_contract_is_stale(self):
        stale_path = os.path.join(ROOT, ".swagger-stale-test.json")
        with open(stale_path, "w", encoding="utf-8") as fh:
            json.dump({"openapi": "3.1.0", "info": {}, "paths": {}}, fh)
        original = api.SWAGGER_PATH
        api.SWAGGER_PATH = stale_path
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._run_lifespan()
            message = str(ctx.exception)
            self.assertIn("out of date", message)
            self.assertIn("task swagger", message)
        finally:
            api.SWAGGER_PATH = original
            os.remove(stale_path)

    def test_hard_fails_when_contract_is_not_json(self):
        bad_path = os.path.join(ROOT, ".swagger-bad-test.json")
        with open(bad_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        original = api.SWAGGER_PATH
        api.SWAGGER_PATH = bad_path
        try:
            with self.assertRaises(RuntimeError) as ctx:
                self._run_lifespan()
            self.assertIn("could not be read", str(ctx.exception))
        finally:
            api.SWAGGER_PATH = original
            os.remove(bad_path)


class TestDataLayout(unittest.TestCase):
    """All binaries live under data/ (nothing is stored in git)."""

    def test_databases_live_in_data_dir(self):
        self.assertEqual(config.DATA_DIR, os.path.join(ROOT, "data"))
        for path in (config.VERBS_DB_PATH, config.DICTIONARY_DB_PATH):
            self.assertEqual(os.path.dirname(path), config.DATA_DIR)
        self.assertIn("kaikki.org-dictionary-Italian.jsonl", config.KAIKKI_JSONL_PATH)
        self.assertEqual(os.path.dirname(config.KAIKKI_JSONL_PATH), config.DATA_DIR)

    def test_builders_write_into_data_dir(self):
        # Builders resolve paths from their own location (not the cwd) and must
        # agree with the runtime config.
        self.assertEqual(builder.DB_PATH, config.DICTIONARY_DB_PATH)
        self.assertEqual(builder.LOCAL_DUMP_PATH, config.KAIKKI_JSONL_PATH)
        self.assertEqual(builder_verbs.DB_PATH, config.VERBS_DB_PATH)
        self.assertEqual(builder_verbs.LOCAL_DUMP_PATH, config.KAIKKI_JSONL_PATH)
        for path in (builder.DB_PATH, builder.LOCAL_DUMP_PATH, builder_verbs.DB_PATH):
            self.assertTrue(os.path.isabs(path), path)


class TestOpenAPIContract(unittest.TestCase):
    """The generated contract (swagger.json) must describe the real API."""

    @classmethod
    def setUpClass(cls):
        cls.spec = api.app.openapi()

    def test_lists_all_endpoints(self):
        self.assertEqual(
            sorted(self.spec["paths"]),
            ["/conjugate", "/define", "/health"],
        )

    def test_declares_api_key_security_scheme(self):
        scheme = self.spec["components"]["securitySchemes"]["X-API-Key"]
        self.assertEqual(scheme["type"], "apiKey")
        self.assertEqual(scheme["in"], "header")
        self.assertEqual(scheme["name"], "X-API-Key")

    def test_secured_endpoints_require_the_key(self):
        for path in ("/conjugate", "/define"):
            self.assertIn("security", self.spec["paths"][path]["get"],
                          f"{path} should declare security")

    def test_define_request_and_response_are_typed(self):
        get = self.spec["paths"]["/define"]["get"]
        self.assertTrue(any(p["name"] == "v" for p in get["parameters"]))
        ref = get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        self.assertTrue(ref.endswith("/DefineResponse"))
        schemas = self.spec["components"]["schemas"]
        for name in ("DefineResponse", "DefinitionResponse", "DictionaryEntry", "HealthResponse"):
            self.assertIn(name, schemas)

    def test_health_response_is_typed(self):
        ref = self.spec["paths"]["/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        self.assertTrue(ref.endswith("/HealthResponse"))


if __name__ == "__main__":
    unittest.main()
