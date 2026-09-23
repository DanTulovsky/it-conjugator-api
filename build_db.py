import codecs
import json
import os
import re
import sqlite3
import sys
import urllib.request
import zlib

# Configure stdout to use utf-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "verbs.db")
KAIKKI_URL = "https://kaikki.org/dictionary/Italian/kaikki.org-dictionary-Italian.jsonl"
# Local dump location (downloaded here on first build).
LOCAL_DUMP_PATH = os.path.join(DATA_DIR, os.path.basename(KAIKKI_URL))

VOWELS_MAP = {
    "à": "a",
    "á": "a",
    "è": "e",
    "é": "e",
    "ì": "i",
    "í": "i",
    "ò": "o",
    "ó": "o",
    "ù": "u",
    "ú": "u",
    "À": "A",
    "Á": "A",
    "È": "E",
    "É": "E",
    "Ì": "I",
    "Í": "I",
    "Ò": "O",
    "Ó": "O",
    "Ù": "U",
    "Ú": "U",
}

UNACCENTED_MONOSYLLABLES = {
    "fà": "fa",
    "và": "va",
    "stà": "sta",
    "stò": "sto",
    "hà": "ha",
    "hò": "ho",
    "fù": "fu",
    "dò": "do",
    "sà": "sa",
    "sò": "so",
    "vò": "vo",
    "pò": "po",
}


def clean_token(t: str) -> str:
    if not t:
        return t
    low = t.lower()
    if low in UNACCENTED_MONOSYLLABLES:
        rep = UNACCENTED_MONOSYLLABLES[low]
        return rep.capitalize() if t[0].isupper() else rep
    chars = list(t)
    for i in range(len(chars) - 1):
        if chars[i] in VOWELS_MAP:
            chars[i] = VOWELS_MAP[chars[i]]
    return "".join(chars)


def clean_single_word(w: str) -> str:
    if "'" in w:
        parts = w.split("'")
        return "'".join(clean_token(p) for p in parts)
    return clean_token(w)


def clean_accents(s: str) -> str:
    """Strip internal dictionary stress accents from Italian words, preserving final accents."""
    if not s:
        return s
    return " ".join(clean_single_word(w) for w in s.split())


def clean_parentheses(s: str) -> str:
    """Remove parenthesized qualifiers (e.g. '(rare) arrabbiare' -> 'arrabbiare')."""
    if not s:
        return s
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# Tags marking a form as non-standard/alternative (archaic, dialectal, poetic, ...).
# Such forms are excluded when extracting conjugations and principal forms.
NONSTANDARD_TAGS = {
    "archaic",
    "obsolete",
    "literary",
    "rare",
    "regional",
    "poetic",
    "dialectal",
    "uncommon",
    "traditional",
}

# Qualifier words appearing inside parentheticals in the head template expansion
# (e.g. "vìsto or (less popular) vedùto") that mark an alternative form as
# non-standard, so it should not be chosen over an unqualified one.
NONSTANDARD_QUALIFIERS = (
    "archaic",
    "obsolete",
    "literary",
    "rare",
    "uncommon",
    "less common",
    "less popular",
    "popular",
    "traditional",
    "poetic",
    "regional",
    "dialectal",
    "informal",
)

# A principal form must look like a word (possibly multi-word); anything else
# (clauses with commas, quotes, etc.) means the expansion text was not parseable.
PRINCIPAL_FORM_RE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\- ]*")


def extract_form_of_words(raw_word: str) -> list[str]:
    """Extract canonical target verbs from a form_of word string.

    Handles Kaikki template artifacts such as 'avere and',
    multiple targets like 'sparire and sparere', parenthetical qualifiers, etc.
    """
    if not raw_word:
        return []
    cleaned = clean_parentheses(clean_accents(raw_word)).strip()
    cleaned = re.sub(r"\s+(?:and|or)$", "", cleaned)
    parts = re.split(r"\s+(?:and|or)\s+", cleaned)
    results = []
    for p in parts:
        p = clean_parentheses(clean_accents(p)).strip()
        if p and PRINCIPAL_FORM_RE.fullmatch(p):
            results.append(p)
    return results


# Curated past-participle corrections where Wiktionary's canonical order in the
# head expansion ("X or Y") disagrees with standard usage. WordReference lists
# only "espanso" for espandere (and its model follows: spandere); Wiktionary
# lists "espànto" first. Keys are accent-cleaned infinitives.
PAST_PARTICIPLE_OVERRIDES = {
    "espandere": "espanso",
    "espandersi": "espansosi",
    "riespandere": "riespanso",
    "rispandere": "rispanso",
    "spandere": "spanso",
    "spandersi": "spansosi",
}


def parse_head_expansion_principal_form(entry, phrase: str) -> str:
    """
    Extract a canonical principal form from the head template expansion.

    The it-verb head template states the canonical forms, e.g.:
        "past participle vàlso"
        "past participle vìsto or (less popular) vedùto"  -> 'visto'
    Returns the first unqualified alternative, or None if not parseable.
    """
    for ht in entry.get("head_templates", []):
        exp = ht.get("expansion", "")
        m = re.search(
            r"(?<!no\s)"
            + phrase
            + r"\s+(.+?)(?=,\s*(?:first-person|second-person|third-person|auxiliary)|$)",
            exp,
        )
        if not m:
            continue
        best = None
        for part in re.split(r"\s+or\s+", m.group(1)):
            qualifiers = re.findall(r"\(([^)]*)\)", part)
            form = re.sub(r"\([^)]*\)", "", part).strip().rstrip(")")
            if not PRINCIPAL_FORM_RE.fullmatch(form):
                continue  # unparseable clause; fall back to table forms
            is_nonstandard = any(
                any(q in ql.lower() for q in NONSTANDARD_QUALIFIERS)
                for ql in qualifiers
            )
            if not is_nonstandard:
                return clean_accents(form)
            if best is None:
                best = clean_accents(form)
        if best is not None:
            return best
    return None


# Moods and tenses person mappings
PERSON_MAPPING = [
    ({"first-person", "singular"}, "io"),
    ({"second-person", "singular"}, "tu"),
    ({"third-person", "singular"}, "lui, lei, Lei, egli"),
    ({"first-person", "plural"}, "noi"),
    ({"second-person", "plural"}, "voi"),
    ({"third-person", "plural"}, "loro, Loro, essi"),
]


def extract_conjugations_and_metadata(entry):
    """
    Extracts the auxiliary, model, principal forms, and simple conjugation table from a verb entry.
    """
    forms = entry.get("forms", [])
    word = clean_parentheses(clean_accents(entry.get("word", "")))

    # Find auxiliary
    auxiliary = "avere"
    for f in forms:
        if "auxiliary" in f.get("tags", []):
            auxiliary = clean_parentheses(clean_accents(f.get("form", "avere")))
            break

    # Determine model from head template if possible
    model = word
    if "head_templates" in entry:
        for ht in entry["head_templates"]:
            if ht.get("name") == "it-verb":
                break

    principal_forms = {
        "infinito": word,
        "gerundio": "—",
        "participio passato": "—",
        "participio presente": "—",
    }

    # Initialize basic conjugation structure
    conjugations = {
        "indicativo": {
            "presente": {},
            "imperfetto": {},
            "passato remoto": {},
            "futuro semplice": {},
        },
        "congiuntivo": {"presente": {}, "imperfetto": {}},
        "condizionale": {"presente": {}},
        "imperativo": {"presente": {}},
    }

    has_conjugations = False

    # Collect candidates for principal forms; Wiktionary entries can contain
    # several conjugation tables (main, "lesser-used forms", dialectal), and a
    # later table may list non-standard variants with no qualifying tags, so the
    # FIRST candidate wins rather than the last one seen.
    gerund_candidates = []
    pp_candidates = []
    prespart_candidates = []

    # Parse each form from the conjugation template
    for f in forms:
        if f.get("source") != "conjugation":
            continue

        tags = set(f.get("tags", []))
        form_val = clean_accents(f.get("form", ""))
        if not form_val or form_val in ("-", "—"):
            continue

        # Principal forms
        if "gerund" in tags:
            gerund_candidates.append(form_val)
        elif "participle" in tags and "past" in tags:
            pp_candidates.append(form_val)
        elif "participle" in tags and "present" in tags:
            prespart_candidates.append(form_val)

        # Skip archaic, obsolete, literary, rare, regional, poetic, dialectal,
        # uncommon, and traditional forms
        if tags & NONSTANDARD_TAGS:
            continue

        # Indicativo Presente
        if "indicative" in tags and "present" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["indicativo"]["presente"]
                ):
                    conjugations["indicativo"]["presente"][person] = form_val
                    has_conjugations = True

        # Indicativo Imperfetto
        elif "indicative" in tags and "imperfect" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["indicativo"]["imperfetto"]
                ):
                    conjugations["indicativo"]["imperfetto"][person] = form_val
                    has_conjugations = True

        # Indicativo Passato Remoto
        elif "indicative" in tags and "historic" in tags and "past" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["indicativo"]["passato remoto"]
                ):
                    conjugations["indicativo"]["passato remoto"][person] = form_val
                    has_conjugations = True

        # Indicativo Futuro Semplice
        elif "indicative" in tags and "future" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["indicativo"]["futuro semplice"]
                ):
                    conjugations["indicativo"]["futuro semplice"][person] = form_val
                    has_conjugations = True

        # Congiuntivo Presente
        elif "subjunctive" in tags and "present" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["congiuntivo"]["presente"]
                ):
                    conjugations["congiuntivo"]["presente"][person] = form_val
                    has_conjugations = True

        # Congiuntivo Imperfetto
        elif "subjunctive" in tags and "imperfect" in tags:
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["congiuntivo"]["imperfetto"]
                ):
                    conjugations["congiuntivo"]["imperfetto"][person] = form_val
                    has_conjugations = True

        # Condizionale Presente
        elif (
            "conditional" in tags
            and "present" in tags
            or (
                "conditional" in tags
                and not any(t in tags for t in ["past", "perfect"])
            )
        ):
            for tag_set, person in PERSON_MAPPING:
                if (
                    tag_set.issubset(tags)
                    and person not in conjugations["condizionale"]["presente"]
                ):
                    conjugations["condizionale"]["presente"][person] = form_val
                    has_conjugations = True

        # Imperativo Presente
        elif (
            "imperative" in tags
            and "present" in tags
            or ("imperative" in tags and "negative" not in tags)
        ):
            is_formal = "formal" in tags or "second-person-semantically" in tags

            # Map second person singular
            if "singular" in tags:
                if (
                    "second-person" in tags
                    and not is_formal
                    and "(tu)" not in conjugations["imperativo"]["presente"]
                ):
                    conjugations["imperativo"]["presente"]["(tu)"] = form_val
                    has_conjugations = True
                elif (
                    is_formal or "third-person" in tags
                ) and "(Lei)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(Lei)"] = form_val
                    has_conjugations = True
            elif "plural" in tags:
                if (
                    "first-person" in tags
                    and "(noi)" not in conjugations["imperativo"]["presente"]
                ):
                    conjugations["imperativo"]["presente"]["(noi)"] = form_val
                    has_conjugations = True
                elif (
                    "second-person" in tags
                    and "(voi)" not in conjugations["imperativo"]["presente"]
                ):
                    conjugations["imperativo"]["presente"]["(voi)"] = form_val
                    has_conjugations = True
                elif (
                    is_formal or "third-person" in tags
                ) and "(Loro)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(Loro)"] = form_val
                    has_conjugations = True

    # Resolve principal forms: prefer the canonical form stated in the head
    # template expansion (e.g. "past participle vàlso"), then the first table
    # candidate. This prevents a dialectal/archaic variant from a later
    # conjugation table (e.g. "valsùto") from overriding the standard form
    # ("valso").
    pp = parse_head_expansion_principal_form(entry, "past participle")
    if pp:
        principal_forms["participio passato"] = pp
    elif pp_candidates:
        principal_forms["participio passato"] = pp_candidates[0]
    override = PAST_PARTICIPLE_OVERRIDES.get(word)
    if override:
        principal_forms["participio passato"] = override

    if gerund_candidates:
        principal_forms["gerundio"] = gerund_candidates[0]
    if prespart_candidates:
        principal_forms["participio presente"] = prespart_candidates[0]

    return auxiliary, model, principal_forms, conjugations, has_conjugations


def _entry_quality(entry, has_conjugations: bool) -> dict:
    """
    Rank a verb entry for deduplication. The dump can contain several entries
    with the same infinitive (separate etymologies/sections, or a literary or
    archaic homograph listed after the standard verb). Standard entries beat
    non-standard (literary, archaic, ...) ones; among those, richer entries
    (with a conjugation table, more senses) are preferred; full ties keep the
    first entry encountered.
    """
    nonstandard = set()
    for ht in entry.get("head_templates", []):
        if ht.get("name") == "tlb":
            for key, value in ht.get("args", {}).items():
                if key != "1" and isinstance(value, str):
                    nonstandard.add(value)
    for sense in entry.get("senses", []):
        nonstandard.update(sense.get("tags", []))
    return {
        "nonstandard": bool(nonstandard & NONSTANDARD_TAGS),
        "has_conj": has_conjugations,
        "nsenses": len(entry.get("senses", [])),
    }


def _is_better_verb_entry(new_meta: dict, old_meta: dict) -> bool:
    if new_meta["nonstandard"] != old_meta["nonstandard"]:
        return not new_meta["nonstandard"]
    if new_meta["has_conj"] != old_meta["has_conj"]:
        return new_meta["has_conj"]
    if new_meta["nsenses"] != old_meta["nsenses"]:
        return new_meta["nsenses"] > old_meta["nsenses"]
    return False  # keep the first entry


def build_database():
    # Ensure the data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # Remove existing DB if any to start fresh
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create tables (storing JSON as compressed BLOBs)
    cursor.execute("""
    CREATE TABLE verbs (
        infinitive TEXT PRIMARY KEY,
        conjugation_json BLOB,
        auxiliary TEXT,
        model TEXT,
        principal_forms_json BLOB
    )
    """)

    cursor.execute("""
    CREATE TABLE forms (
        form TEXT,
        infinitive TEXT,
        PRIMARY KEY (form, infinitive)
    )
    """)

    cursor.execute("CREATE INDEX idx_forms_form ON forms(form)")
    cursor.execute("CREATE INDEX idx_forms_infinitive ON forms(infinitive)")
    conn.commit()

    print("Database tables and indexes created successfully.")

    if os.path.exists(LOCAL_DUMP_PATH):
        print(f"Using local file: {LOCAL_DUMP_PATH}")
        reader = codecs.getreader("utf-8")(open(LOCAL_DUMP_PATH, "rb"))
    else:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(KAIKKI_URL, headers=headers)
        print(f"Streaming Wiktionary data from {KAIKKI_URL}...")
        reader = codecs.getreader("utf-8")(urllib.request.urlopen(req))

    verbs_meta = {}  # infinitive -> quality_meta across the whole build; prevents worse homographs from overwriting
    verbs_best = {}  # infinitive -> (quality_meta, row_tuple); keeps the best entry per batch
    forms_to_insert = set()

    line_count = 0
    verb_count = 0
    form_ref_count = 0

    try:
        for line in reader:
            line_count += 1
            if line_count % 50000 == 0:
                print(f"Processed {line_count} lines. Saved {verb_count} verbs...")

            try:
                entry = json.loads(line)
                if entry.get("pos") != "verb":
                    continue

                word = clean_parentheses(clean_accents(entry.get("word", "")))
                if not word:
                    continue

                # Extract conjugations first
                auxiliary, model, principal_forms, conjugations, has_conjugations = (
                    extract_conjugations_and_metadata(entry)
                )

                # Check if it's a form-of entry
                is_form_of = False
                form_of_verbs = []

                senses = entry.get("senses", [])
                for sense in senses:
                    if "form_of" in sense:
                        is_form_of = True
                        for fo in sense["form_of"]:
                            raw_w = fo.get("word")
                            if raw_w:
                                form_of_verbs.extend(extract_form_of_words(raw_w))

                if "form_of" in entry:
                    is_form_of = True
                    for fo in entry["form_of"]:
                        raw_w = fo.get("word")
                        if raw_w:
                            form_of_verbs.extend(extract_form_of_words(raw_w))

                if is_form_of and not has_conjugations:
                    for fv in form_of_verbs:
                        forms_to_insert.add((word, fv))
                        form_ref_count += 1
                    continue

                # Store verb as a main verb entry (compress JSONs with zlib).
                # Multiple entries can share an infinitive (e.g. a literary or
                # archaic homograph after the standard verb); keep the best one
                # instead of letting the last entry overwrite earlier ones.
                if has_conjugations or "head_templates" in entry:
                    comp_conj = zlib.compress(
                        json.dumps(conjugations, ensure_ascii=False).encode("utf-8")
                    )
                    comp_pf = zlib.compress(
                        json.dumps(principal_forms, ensure_ascii=False).encode("utf-8")
                    )

                    row = (
                        word,
                        sqlite3.Binary(comp_conj),
                        auxiliary,
                        model,
                        sqlite3.Binary(comp_pf),
                    )
                    meta = _entry_quality(entry, has_conjugations)
                    prev_meta = verbs_meta.get(word)
                    if prev_meta is not None and not _is_better_verb_entry(
                        meta, prev_meta
                    ):
                        continue
                    verbs_meta[word] = meta
                    verbs_best[word] = (meta, row)
                    verb_count += 1

                    forms_to_insert.add((word, word))
                    for fv in form_of_verbs:
                        forms_to_insert.add((word, fv))

                    for f in entry.get("forms", []):
                        if f.get("source") == "conjugation":
                            tags = set(f.get("tags", []))
                            if (
                                "table-tags" in tags
                                or "auxiliary" in tags
                                or (tags & NONSTANDARD_TAGS)
                            ):
                                continue
                            fval = clean_accents(f.get("form"))
                            if fval and fval not in (
                                "-",
                                "—",
                                "no-table-tags",
                                "it-conj",
                                "it-conj-rfc",
                                "irregular",
                            ):
                                forms_to_insert.add((fval, word))
                                parts = fval.split()
                                if len(parts) > 1:
                                    forms_to_insert.add((parts[-1], word))

                # Write in batches of 1000
                if len(verbs_best) >= 1000:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO verbs VALUES (?, ?, ?, ?, ?)",
                        [r for _, r in verbs_best.values()],
                    )
                    verbs_best.clear()

                    cursor.executemany(
                        "INSERT OR IGNORE INTO forms VALUES (?, ?)",
                        list(forms_to_insert),
                    )
                    forms_to_insert.clear()
                    conn.commit()

            except Exception:
                pass

    except Exception as e:
        print("Error during streaming:", e)
        conn.close()
        sys.exit(1)

    # Flush remaining batch
    if verbs_best:
        cursor.executemany(
            "INSERT OR REPLACE INTO verbs VALUES (?, ?, ?, ?, ?)",
            [r for _, r in verbs_best.values()],
        )
    if forms_to_insert:
        cursor.executemany(
            "INSERT OR IGNORE INTO forms VALUES (?, ?)", list(forms_to_insert)
        )

    conn.commit()
    conn.close()

    print("Database build completed successfully!")
    print(f"Total lines processed: {line_count}")
    print(f"Total main verbs loaded: {verb_count}")
    print(f"Total reverse lookup forms loaded: {form_ref_count}")


if __name__ == "__main__":
    build_database()
