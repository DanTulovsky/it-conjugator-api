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

from app.db_core import get_conjugations  # noqa: E402


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
    ("valere", "essere", "valso", "è valso"),        # WR table: ha valso
    ("equivalere", "essere", "equivalso", "è equivalso"),
    ("prevalere", "essere", "prevalso", "è prevalso"),
    ("correre", "essere", "corso", "è corso"),       # WR table: ha corso
    ("importare", "essere", "importato", "è importato"),  # WR table: ha importato
]

# ---------------------------------------------------------------------------
# Uncommon verbs / regression cases for past fixes (archaic/dialectal past
# participles no longer winning, homograph deduplication, curated overrides).
# ---------------------------------------------------------------------------
EDGE_CASES = [
    ("ripartire", "essere", "ripartito", "è ripartito"),   # homograph dedup (was avere)
    ("recarsi", "essere", "recatosi", "si è recato"),
    ("inferire", "avere", "inferito", "ha inferito"),      # 'to infer' sense (WR)
    ("contrarsi", "essere", "contrattosi", "si è contratto"),
    ("seppellire", "avere", "sepolto", "ha sepolto"),
    ("disperdere", "avere", "disperso", "ha disperso"),
    ("provvedere", "avere", "provveduto", "ha provveduto"),
    ("accorgersi", "essere", "accortosi", "si è accorto"),
    ("espandere", "avere", "espanso", "ha espanso"),       # curated override (WR: espanso)
    ("spandere", "avere", "spanso", "ha spanso"),
    ("assolvere", "avere", "assolto", "ha assolto"),
    ("calere", "avere", "caluto", "ha caluto"),            # defective, 3rd-person only
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
        self.assertEqual(data["auxiliary"], auxiliary,
                         f"{verb}: auxiliary")
        self.assertEqual(data["principal_forms"].get("participio passato"), pp,
                         f"{verb}: past participle")
        self.assertEqual(
            data["conjugations"]["tempi composti"]["passato prossimo"]
            ["lui, lei, Lei, egli"],
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
                    data["principal_forms"].get("participio passato"), "—",
                    f"{verb}: expected no past participle",
                )
                self.assertNotIn("tempi composti", data["conjugations"],
                                 f"{verb}: expected no compound tenses")

    def test_reverse_lookup(self):
        # Inflected/archaic forms must still resolve back to their infinitive.
        for form, expected_infinitive in [
            ("valsuto", "valere"),
            ("valso", "valere"),
            ("valsi", "valere"),
            ("andato", "andare"),
            ("partito", "partire"),
            ("mangiato", "mangiare"),
        ]:
            with self.subTest(form=form):
                data = get_conjugations(form)
                self.assertIsNotNone(data, f"{form}: not resolved")
                self.assertEqual(data["queried"], expected_infinitive,
                                 f"{form}: reverse lookup")


if __name__ == "__main__":
    unittest.main()
