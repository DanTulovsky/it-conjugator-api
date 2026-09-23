"""
Regression tests for the Italian conjugation database (verbs.db).

Verifies that ``app.db_core.get_conjugations()`` produces the correct
auxiliary, past participle and passato prossimo (lui/lei/egli) for a curated
list of verbs. Expected values follow standard Italian conjugation, with
WordReference (https://www.wordreference.com/conj/itverbs.aspx) as the
reference for ambiguous cases.

How to add a test case
----------------------
Add a 4-tuple to one of the lists below::

    (verb, expected_auxiliary, expected_past_participle, expected_passato_prossimo_lui)

Run the suite from the repo root::

    python3 -m unittest discover -s tests -v

Note on dual-auxiliary verbs: for verbs that take both ``essere``
(intransitive) and ``avere`` (transitive) — e.g. valere, correre, importare —
this project deliberately uses ``essere`` (Wiktionary's default). WordReference
tables may display ``avere`` for these; see the comments on those entries.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db_core import clean_accents, get_conjugations, resolve_pronominal_base
from app.filters import apply_filters
from build_db import extract_form_of_words

# ---------------------------------------------------------------------------
# Common verbs, grouped by conjugation pattern.
# (verb, auxiliary, past participle, passato prossimo lui/lei)
# ---------------------------------------------------------------------------
COMMON_VERBS = [
    # --- regular -are, auxiliary avere ---
    ("amare", "avere", "amato", "ha amato"),
    ("parlare", "avere", "parlato", "ha parlato"),
    ("mangiare", "avere", "mangiato", "ha mangiato"),
    ("lavorare", "avere", "lavorato", "ha lavorato"),
    ("cantare", "avere", "cantato", "ha cantato"),
    ("guardare", "avere", "guardato", "ha guardato"),
    ("comprare", "avere", "comprato", "ha comprato"),
    ("pagare", "avere", "pagato", "ha pagato"),
    ("cercare", "avere", "cercato", "ha cercato"),
    ("trovare", "avere", "trovato", "ha trovato"),
    ("portare", "avere", "portato", "ha portato"),
    ("pensare", "avere", "pensato", "ha pensato"),
    ("aspettare", "avere", "aspettato", "ha aspettato"),
    ("camminare", "avere", "camminato", "ha camminato"),
    ("studiare", "avere", "studiato", "ha studiato"),
    ("giocare", "avere", "giocato", "ha giocato"),
    ("telefonare", "avere", "telefonato", "ha telefonato"),
    ("viaggiare", "avere", "viaggiato", "ha viaggiato"),
    ("cominciare", "avere", "cominciato", "ha cominciato"),
    ("aiutare", "avere", "aiutato", "ha aiutato"),
    ("chiamare", "avere", "chiamato", "ha chiamato"),
    ("lasciare", "avere", "lasciato", "ha lasciato"),
    # --- regular -are, auxiliary essere ---
    ("entrare", "essere", "entrato", "è entrato"),
    # --- regular -ere, auxiliary avere ---
    ("credere", "avere", "creduto", "ha creduto"),
    ("temere", "avere", "temuto", "ha temuto"),
    ("ricevere", "avere", "ricevuto", "ha ricevuto"),
    ("vendere", "avere", "venduto", "ha venduto"),
    ("ripetere", "avere", "ripetuto", "ha ripetuto"),
    ("battere", "avere", "battuto", "ha battuto"),
    # --- regular -ire, auxiliary avere ---
    ("dormire", "avere", "dormito", "ha dormito"),
    ("sentire", "avere", "sentito", "ha sentito"),
    ("servire", "avere", "servito", "ha servito"),
    ("capire", "avere", "capito", "ha capito"),
    ("finire", "avere", "finito", "ha finito"),
    ("vestire", "avere", "vestito", "ha vestito"),
    ("costruire", "avere", "costruito", "ha costruito"),
    # --- -ire with irregular past participle, auxiliary avere ---
    ("aprire", "avere", "aperto", "ha aperto"),
    ("offrire", "avere", "offerto", "ha offerto"),
    ("coprire", "avere", "coperto", "ha coperto"),
    ("scoprire", "avere", "scoperto", "ha scoperto"),
    ("soffrire", "avere", "sofferto", "ha sofferto"),
    # --- irregular past participles, auxiliary avere ---
    ("fare", "avere", "fatto", "ha fatto"),
    ("dire", "avere", "detto", "ha detto"),
    ("dare", "avere", "dato", "ha dato"),
    ("bere", "avere", "bevuto", "ha bevuto"),
    ("vedere", "avere", "visto", "ha visto"),
    ("leggere", "avere", "letto", "ha letto"),
    ("scrivere", "avere", "scritto", "ha scritto"),
    ("prendere", "avere", "preso", "ha preso"),
    ("mettere", "avere", "messo", "ha messo"),
    ("chiedere", "avere", "chiesto", "ha chiesto"),
    ("rispondere", "avere", "risposto", "ha risposto"),
    ("rompere", "avere", "rotto", "ha rotto"),
    ("scegliere", "avere", "scelto", "ha scelto"),
    ("perdere", "avere", "perso", "ha perso"),
    ("vincere", "avere", "vinto", "ha vinto"),
    ("cuocere", "avere", "cotto", "ha cotto"),
    ("friggere", "avere", "fritto", "ha fritto"),
    ("conoscere", "avere", "conosciuto", "ha conosciuto"),
    ("tenere", "avere", "tenuto", "ha tenuto"),
    ("porre", "avere", "posto", "ha posto"),
    ("dovere", "avere", "dovuto", "ha dovuto"),
    ("potere", "avere", "potuto", "ha potuto"),
    ("volere", "avere", "voluto", "ha voluto"),
    ("sapere", "avere", "saputo", "ha saputo"),
    ("accendere", "avere", "acceso", "ha acceso"),
    ("spendere", "avere", "speso", "ha speso"),
    ("difendere", "avere", "difeso", "ha difeso"),
    ("decidere", "avere", "deciso", "ha deciso"),
    ("dividere", "avere", "diviso", "ha diviso"),
    ("ridere", "avere", "riso", "ha riso"),
    ("sorridere", "avere", "sorriso", "ha sorriso"),
    ("uccidere", "avere", "ucciso", "ha ucciso"),
    ("chiudere", "avere", "chiuso", "ha chiuso"),
    ("confondere", "avere", "confuso", "ha confuso"),
    ("nascondere", "avere", "nascosto", "ha nascosto"),
    ("spegnere", "avere", "spento", "ha spento"),
    ("togliere", "avere", "tolto", "ha tolto"),
    ("cogliere", "avere", "colto", "ha colto"),
    ("sciogliere", "avere", "sciolto", "ha sciolto"),
    ("muovere", "avere", "mosso", "ha mosso"),
    ("vivere", "avere", "vissuto", "ha vissuto"),
    ("assumere", "avere", "assunto", "ha assunto"),
    ("esprimere", "avere", "espresso", "ha espresso"),
    ("distruggere", "avere", "distrutto", "ha distrutto"),
    ("correggere", "avere", "corretto", "ha corretto"),
    ("proteggere", "avere", "protetto", "ha protetto"),
    ("dirigere", "avere", "diretto", "ha diretto"),
    # --- intransitive / motion verbs, auxiliary essere ---
    ("andare", "essere", "andato", "è andato"),
    ("venire", "essere", "venuto", "è venuto"),
    ("arrivare", "essere", "arrivato", "è arrivato"),
    ("partire", "essere", "partito", "è partito"),
    ("uscire", "essere", "uscito", "è uscito"),
    ("nascere", "essere", "nato", "è nato"),
    ("morire", "essere", "morto", "è morto"),
    ("diventare", "essere", "diventato", "è diventato"),
    ("restare", "essere", "restato", "è restato"),
    ("rimanere", "essere", "rimasto", "è rimasto"),
    ("crescere", "essere", "cresciuto", "è cresciuto"),
    ("salire", "essere", "salito", "è salito"),
    ("scendere", "essere", "sceso", "è sceso"),
    ("giungere", "essere", "giunto", "è giunto"),
    ("fuggire", "essere", "fuggito", "è fuggito"),
    ("piacere", "essere", "piaciuto", "è piaciuto"),
    ("sembrare", "essere", "sembrato", "è sembrato"),
    ("parere", "essere", "parso", "è parso"),
    ("succedere", "essere", "successo", "è successo"),
    ("bastare", "essere", "bastato", "è bastato"),
    ("costare", "essere", "costato", "è costato"),
    ("durare", "essere", "durato", "è durato"),
    ("appartenere", "essere", "appartenuto", "è appartenuto"),
    ("stare", "essere", "stato", "è stato"),
    ("scomparire", "essere", "scomparso", "è scomparso"),
    ("scoppiare", "essere", "scoppiato", "è scoppiato"),
    ("traboccare", "essere", "traboccato", "è traboccato"),
]

# ---------------------------------------------------------------------------
# Dual-auxiliary verbs. Policy: use essere (Wiktionary's intransitive default).
# WordReference tables display avere for these but explicitly note both
# auxiliaries are valid (essere intransitive / avere transitive), e.g. valere:
# "When this verb is used transitively, its compound tenses are formed with the
# auxiliary avere; when used intransitively, the auxiliary is essere."
# ---------------------------------------------------------------------------
DUAL_AUXILIARY_VERBS = [
    ("valere", "essere", "valso", "è valso"),  # WR table: ha valso
    ("equivalere", "essere", "equivalso", "è equivalso"),
    ("prevalere", "essere", "prevalso", "è prevalso"),
    ("correre", "essere", "corso", "è corso"),  # WR table: ha corso
    ("importare", "essere", "importato", "è importato"),  # WR table: ha importato
]

# ---------------------------------------------------------------------------
# Uncommon verbs / regression cases for past fixes (archaic/dialectal past
# participles no longer winning, homograph deduplication, curated overrides).
# ---------------------------------------------------------------------------
EDGE_CASES = [
    ("ripartire", "essere", "ripartito", "è ripartito"),  # homograph dedup (was avere)
    ("recarsi", "essere", "recatosi", "si è recato"),
    ("inferire", "avere", "inferito", "ha inferito"),  # 'to infer' sense (WR)
    ("contrarsi", "essere", "contrattosi", "si è contratto"),
    ("seppellire", "avere", "sepolto", "ha sepolto"),
    ("disperdere", "avere", "disperso", "ha disperso"),
    ("provvedere", "avere", "provveduto", "ha provveduto"),
    ("accorgersi", "essere", "accortosi", "si è accorto"),
    ("espandere", "avere", "espanso", "ha espanso"),  # curated override (WR: espanso)
    ("spandere", "avere", "spanso", "ha spanso"),
    ("assolvere", "avere", "assolto", "ha assolto"),
    ("calere", "avere", "caluto", "ha caluto"),  # defective, 3rd-person only
    ("valersi", "essere", "valsosi", "si è valso"),
    ("arrabbiarsi", "essere", "arrabbiatosi", "si è arrabbiato"),
    ("lavarsi", "essere", "lavatosi", "si è lavato"),
    ("svegliarsi", "essere", "svegliatosi", "si è svegliato"),
    ("divertirsi", "essere", "divertitosi", "si è divertito"),
    ("sedersi", "essere", "sedutosi", "si è seduto"),
    # "se ne è andato" (no elision of ne+è) — both this and the elided
    # "se n'è andato" are correct Italian; this project emits the non-elided form.
    ("andarsene", "essere", "andatosene", "se ne è andato"),
    ("mettercela", "avere", "messocela", "ce l'ha messa"),
]

# ---------------------------------------------------------------------------
# Defective verbs: no past participle, so no compound tenses at all.
# ---------------------------------------------------------------------------
DEFECTIVE_VERBS = [
    "solere",
    "competere",
    "concernere",
    "urgere",
    "vertere",
    "incombere",
    "tangere",
]


class TestConjugations(unittest.TestCase):
    def _assert_conjugation(self, verb, auxiliary, pp, lui_passato_prossimo):
        data = get_conjugations(verb)
        self.assertIsNotNone(data, f"{verb}: not found in database")
        self.assertEqual(data["auxiliary"], auxiliary, f"{verb}: auxiliary")
        self.assertEqual(
            data["principal_forms"].get("participio passato"),
            pp,
            f"{verb}: past participle",
        )
        self.assertEqual(
            data["conjugations"]["tempi composti"]["passato prossimo"][
                "lui, lei, Lei, egli"
            ],
            lui_passato_prossimo,
            f"{verb}: passato prossimo (lui/lei)",
        )

    def test_common_verbs(self):
        for verb, aux, pp, lui in COMMON_VERBS:
            with self.subTest(verb=verb):
                self._assert_conjugation(verb, aux, pp, lui)

    def test_dual_auxiliary_verbs(self):
        for verb, aux, pp, lui in DUAL_AUXILIARY_VERBS:
            with self.subTest(verb=verb):
                self._assert_conjugation(verb, aux, pp, lui)

    def test_edge_cases(self):
        for verb, aux, pp, lui in EDGE_CASES:
            with self.subTest(verb=verb):
                self._assert_conjugation(verb, aux, pp, lui)

    def test_defective_verbs_have_no_compound_tenses(self):
        for verb in DEFECTIVE_VERBS:
            with self.subTest(verb=verb):
                data = get_conjugations(verb)
                self.assertIsNotNone(data, f"{verb}: not found in database")
                self.assertEqual(
                    data["principal_forms"].get("participio passato"),
                    "—",
                    f"{verb}: expected no past participle",
                )
                self.assertNotIn(
                    "tempi composti",
                    data["conjugations"],
                    f"{verb}: expected no compound tenses",
                )

    def test_reverse_lookup(self):
        # Inflected/archaic forms must still resolve back to their infinitive.
        for form, expected_infinitive in [
            ("valsuto", "valere"),
            ("valso", "valere"),
            ("valsi", "valere"),
            ("andato", "andare"),
            ("partito", "partire"),
            ("mangiato", "mangiare"),
            ("ho", "avere"),
            ("ha", "avere"),
            ("hai", "avere"),
            ("hanno", "avere"),
            ("vado", "andare"),
            ("vai", "andare"),
            ("va", "andare"),
            ("vanno", "andare"),
            ("vada", "andare"),
            ("vadano", "andare"),
            ("veniamo", "venire"),
            ("spento", "spegnere"),
            ("sparvero", "sparire"),
            ("faccio", "fare"),
            ("fai", "fare"),
            ("fa", "fare"),
            ("sto", "stare"),
            ("sta", "stare"),
        ]:
            with self.subTest(form=form):
                data = get_conjugations(form)
                self.assertIsNotNone(data, f"{form}: not resolved")
                self.assertEqual(
                    data["queried"], expected_infinitive, f"{form}: reverse lookup"
                )

    def test_unaccented_monosyllables_in_simple_tenses(self):
        # In Italian orthography, fa, va, sta, sto, ha, ho, fu, do, sa, so must NOT carry accents.
        cases = [
            ("fare", "indicativo", "presente", "lui, lei, Lei, egli", "fa"),
            ("andare", "indicativo", "presente", "lui, lei, Lei, egli", "va"),
            ("stare", "indicativo", "presente", "io", "sto"),
            ("stare", "indicativo", "presente", "lui, lei, Lei, egli", "sta"),
            ("avere", "indicativo", "presente", "io", "ho"),
            ("avere", "indicativo", "presente", "lui, lei, Lei, egli", "ha"),
            ("dare", "indicativo", "presente", "io", "do"),
            ("dare", "indicativo", "presente", "lui, lei, Lei, egli", "dà"),
            ("sapere", "indicativo", "presente", "io", "so"),
            ("sapere", "indicativo", "presente", "lui, lei, Lei, egli", "sa"),
            ("essere", "indicativo", "passato remoto", "lui, lei, Lei, egli", "fu"),
        ]
        for verb, mood, tense, person, expected in cases:
            with self.subTest(verb=verb, mood=mood, tense=tense, person=person):
                data = get_conjugations(verb)
                self.assertIsNotNone(data, f"{verb}: not found")
                actual = data["conjugations"][mood][tense][person]
                self.assertEqual(actual, expected, f"{verb} {mood} {tense} {person}")

    def test_compound_tenses_agreement(self):
        # Verbs with direct object pronoun 'la' (sela, cela) agree with 'la' (singular feminine -a), not the subject
        data_mettercela = get_conjugations("mettercela")
        self.assertEqual(
            data_mettercela["conjugations"]["tempi composti"]["passato prossimo"][
                "noi"
            ],
            "ce l'abbiamo messa",
        )
        self.assertEqual(
            data_mettercela["conjugations"]["tempi composti"]["passato prossimo"][
                "loro, Loro, essi"
            ],
            "ce l'hanno messa",
        )

        # Full paradigm for mettercela: past participle agrees with singular feminine 'la' across all persons
        paradigm_mettercela = data_mettercela["conjugations"]["tempi composti"][
            "passato prossimo"
        ]
        self.assertEqual(paradigm_mettercela["io"], "ce l'ho messa")
        self.assertEqual(paradigm_mettercela["tu"], "ce l'hai messa")
        self.assertEqual(paradigm_mettercela["lui, lei, Lei, egli"], "ce l'ha messa")
        self.assertEqual(paradigm_mettercela["noi"], "ce l'abbiamo messa")
        self.assertEqual(paradigm_mettercela["voi"], "ce l'avete messa")
        self.assertEqual(paradigm_mettercela["loro, Loro, essi"], "ce l'hanno messa")

        # Full paradigm for cavarsela: past participle agrees with singular feminine 'la' across all persons
        data_cavarsela = get_conjugations("cavarsela")
        paradigm_cavarsela = data_cavarsela["conjugations"]["tempi composti"][
            "passato prossimo"
        ]
        self.assertEqual(paradigm_cavarsela["io"], "me la sono cavata")
        self.assertEqual(paradigm_cavarsela["tu"], "te la sei cavata")
        self.assertEqual(paradigm_cavarsela["lui, lei, Lei, egli"], "se l'è cavata")
        self.assertEqual(paradigm_cavarsela["noi"], "ce la siamo cavata")
        self.assertEqual(paradigm_cavarsela["voi"], "ve la siete cavata")
        self.assertEqual(paradigm_cavarsela["loro, Loro, essi"], "se la sono cavata")

    def test_phrasal_verbs_compound_tenses(self):
        # Multi-word verbs should not treat the base inflected verb as a clitic
        data_vedere_rosso = get_conjugations("vedere rosso")
        self.assertEqual(
            data_vedere_rosso["conjugations"]["tempi composti"]["passato prossimo"][
                "io"
            ],
            "ho visto rosso",
        )
        data_tenere_occhio = get_conjugations("tenere d'occhio")
        self.assertEqual(
            data_tenere_occhio["conjugations"]["tempi composti"]["passato prossimo"][
                "io"
            ],
            "ho tenuto d'occhio",
        )

        data_tenersi = get_conjugations("tenersi tutto dentro")
        self.assertEqual(
            data_tenersi["conjugations"]["tempi composti"]["passato prossimo"]["io"],
            "mi sono tenuto tutto dentro",
        )
        self.assertEqual(
            data_tenersi["conjugations"]["tempi composti"]["passato prossimo"]["noi"],
            "ci siamo tenuti tutto dentro",
        )

        data_andare_fiero = get_conjugations("andare fiero")
        self.assertEqual(
            data_andare_fiero["conjugations"]["tempi composti"]["passato prossimo"][
                "io"
            ],
            "sono andato fiero",
        )
        self.assertEqual(
            data_andare_fiero["conjugations"]["tempi composti"]["passato prossimo"][
                "lui, lei, Lei, egli"
            ],
            "è andato fiero",
        )
        self.assertEqual(
            data_andare_fiero["conjugations"]["tempi composti"]["passato prossimo"][
                "noi"
            ],
            "siamo andati fiero",
        )

    def test_dynamic_pronominal_verb(self):
        data = get_conjugations("parlarsela")
        self.assertIsNotNone(data)
        self.assertEqual(data["principal_forms"]["gerundio"], "parlandosela")
        self.assertEqual(data["principal_forms"]["participio passato"], "parlatosela")
        self.assertEqual(
            data["conjugations"]["tempi composti"]["passato prossimo"]["noi"],
            "ce la siamo parlata",
        )

        data_parlarsi = get_conjugations("parlarsi")
        self.assertIsNotNone(data_parlarsi)
        imperativo = data_parlarsi["conjugations"]["imperativo"]["presente"]
        self.assertEqual(imperativo["(tu)"], "parlati")
        self.assertEqual(imperativo["(Lei)"], "si parli")
        self.assertEqual(imperativo["(noi)"], "parliamoci")
        self.assertEqual(imperativo["(voi)"], "parlatevi")
        self.assertEqual(imperativo["(Loro)"], "si parlino")

        # Consonant doubling on monosyllabic stems in imperative
        data_farsi = get_conjugations("farsi")
        self.assertIsNotNone(data_farsi)
        self.assertEqual(
            data_farsi["conjugations"]["imperativo"]["presente"]["(tu)"], "fatti"
        )
        self.assertEqual(
            data_farsi["conjugations"]["imperativo"]["presente"]["(noi)"], "facciamoci"
        )

        data_andarsene = get_conjugations("andarsene")
        self.assertIsNotNone(data_andarsene)
        self.assertEqual(
            data_andarsene["conjugations"]["imperativo"]["presente"]["(tu)"], "vattene"
        )
        self.assertEqual(
            data_andarsene["conjugations"]["imperativo"]["presente"]["(Lei)"],
            "se ne vada",
        )
        self.assertEqual(
            data_andarsene["conjugations"]["imperativo"]["presente"]["(noi)"],
            "andiamocene",
        )

    def test_pronominal_rre_stems(self):
        # Stems ending in -rre (porre, trarre, condurre)
        self.assertEqual(resolve_pronominal_base("porsela"), ("porre", "sela"))
        self.assertEqual(resolve_pronominal_base("trarsela"), ("trarre", "sela"))
        self.assertEqual(resolve_pronominal_base("condursela"), ("condurre", "sela"))

    def test_extract_form_of_words(self):
        # Template parsing edge cases from Kaikki dump
        self.assertEqual(extract_form_of_words("avere and"), ["avere"])
        self.assertEqual(
            extract_form_of_words("avere and (obsolete) havere"), ["avere", "havere"]
        )
        self.assertEqual(
            extract_form_of_words("sparire and sparere"), ["sparire", "sparere"]
        )
        self.assertEqual(
            extract_form_of_words("spegnere and spengere"), ["spegnere", "spengere"]
        )
        self.assertEqual(
            extract_form_of_words("arruolare and arrolare"), ["arruolare", "arrolare"]
        )
        self.assertEqual(
            extract_form_of_words("lumachina di mare or lumachina marittima"),
            ["lumachina di mare", "lumachina marittima"],
        )
        self.assertEqual(extract_form_of_words(""), [])
        self.assertEqual(extract_form_of_words(None), [])

    def test_clean_accents_rules(self):
        # Monosyllables that must NOT have accents
        for bad, expected in [
            ("fà", "fa"),
            ("và", "va"),
            ("stà", "sta"),
            ("stò", "sto"),
            ("hà", "ha"),
            ("hò", "ho"),
            ("fù", "fu"),
            ("dò", "do"),
            ("sà", "sa"),
            ("sò", "so"),
            ("vò", "vo"),
            ("pò", "po"),
        ]:
            self.assertEqual(clean_accents(bad), expected, f"cleaning {bad}")

        # Accented monosyllables that MUST retain accents
        for valid in ["dà", "è", "può"]:
            self.assertEqual(clean_accents(valid), valid, f"preserving {valid}")

        # Polysyllabic oxytone words that MUST retain accents
        for valid in ["parlerò", "parlò", "poté", "finì", "rifà", "ridà"]:
            self.assertEqual(clean_accents(valid), valid, f"preserving {valid}")

        # Internal stress marks that MUST be stripped
        for stress, expected in [
            ("màngiano", "mangiano"),
            ("amàto", "amato"),
            ("crèdono", "credono"),
        ]:
            self.assertEqual(
                clean_accents(stress), expected, f"stripping stress {stress}"
            )

        # Apostrophes
        self.assertEqual(clean_accents("l'hà"), "l'ha")
        self.assertEqual(clean_accents("l'hò"), "l'ho")
        self.assertEqual(clean_accents("d'occhio"), "d'occhio")

    def test_person_filtering(self):
        data = get_conjugations("amare")
        # Filtering by 'lui' should match 3rd person singular
        f_lui = apply_filters(data, "indicativo", "presente", "lui", full=False)
        self.assertIn(
            "lui, lei, Lei, egli", f_lui["conjugations"]["indicativo"]["presente"]
        )
        self.assertEqual(len(f_lui["conjugations"]["indicativo"]["presente"]), 1)

        # Filtering by canonical string 'lui, lei, Lei, egli'
        f_canon = apply_filters(
            data, "indicativo", "presente", "lui, lei, Lei, egli", full=False
        )
        self.assertIn(
            "lui, lei, Lei, egli", f_canon["conjugations"]["indicativo"]["presente"]
        )

        # Filtering by aliases 'lei', 'egli'
        f_lei = apply_filters(data, "indicativo", "presente", "lei", full=False)
        self.assertIn(
            "lui, lei, Lei, egli", f_lei["conjugations"]["indicativo"]["presente"]
        )
        f_egli = apply_filters(data, "indicativo", "presente", "egli", full=False)
        self.assertIn(
            "lui, lei, Lei, egli", f_egli["conjugations"]["indicativo"]["presente"]
        )

        # Filtering by aliases 'loro', 'essi'
        f_loro = apply_filters(data, "indicativo", "presente", "loro", full=False)
        self.assertIn(
            "loro, Loro, essi", f_loro["conjugations"]["indicativo"]["presente"]
        )
        f_essi = apply_filters(data, "indicativo", "presente", "essi", full=False)
        self.assertIn(
            "loro, Loro, essi", f_essi["conjugations"]["indicativo"]["presente"]
        )

        # Filtering by 'tu' in imperativo
        f_tu_imp = apply_filters(data, "imperativo", "presente", "tu", full=False)
        self.assertIn("(tu)", f_tu_imp["conjugations"]["imperativo"]["presente"])


class TestConjugateEndpoint(unittest.TestCase):
    def setUp(self):
        from app import api

        os.environ["SCRAPER_API_KEY"] = "test-key"
        api.API_KEY = "test-key"
        self.api = api

    def test_conjugate_endpoint_person_filters(self):
        # Filtering by canonical group string with commas
        resp_canon = self.api.conjugate(
            v="amare", full=False, persons="lui, lei, Lei, egli", api_key="test-key"
        )
        self.assertTrue(resp_canon.success)
        self.assertEqual(
            list(resp_canon.data.conjugations["indicativo"]["presente"].keys()),
            ["lui, lei, Lei, egli"],
        )

        # Filtering by individual pronoun alias 'lui'
        resp_alias = self.api.conjugate(
            v="amare", full=False, persons="lui", api_key="test-key"
        )
        self.assertTrue(resp_alias.success)
        self.assertEqual(
            list(resp_alias.data.conjugations["indicativo"]["presente"].keys()),
            ["lui, lei, Lei, egli"],
        )

        # Filtering by 'loro'
        resp_loro = self.api.conjugate(
            v="amare", full=False, persons="loro", api_key="test-key"
        )
        self.assertTrue(resp_loro.success)
        self.assertEqual(
            list(resp_loro.data.conjugations["indicativo"]["presente"].keys()),
            ["loro, Loro, essi"],
        )

        # Filtering by 'tu' in imperative
        resp_imp = self.api.conjugate(
            v="amare", full=False, moods="imperativo", persons="tu", api_key="test-key"
        )
        self.assertTrue(resp_imp.success)
        self.assertEqual(
            list(resp_imp.data.conjugations["imperativo"]["presente"].keys()),
            ["(tu)"],
        )


if __name__ == "__main__":
    unittest.main()
