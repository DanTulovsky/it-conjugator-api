import sqlite3
import json
import urllib.request
import codecs
import os
import sys
import re
import zlib

# Configure stdout to use utf-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

DB_PATH = "verbs.db"
KAIKKI_URL = "https://kaikki.org/dictionary/Italian/kaikki.org-dictionary-Italian.jsonl"

VOWELS_MAP = {
    'à': 'a', 'á': 'a', 'è': 'e', 'é': 'e', 'ì': 'i', 'í': 'i', 'ò': 'o', 'ó': 'o', 'ù': 'u', 'ú': 'u',
    'À': 'A', 'Á': 'A', 'È': 'E', 'É': 'E', 'Ì': 'I', 'Í': 'I', 'Ò': 'O', 'Ó': 'O', 'Ù': 'U', 'Ú': 'U'
}

def clean_accents(s: str) -> str:
    """Strip internal dictionary stress accents from Italian words, preserving final accents."""
    if not s:
        return s
    def clean_single_word(w: str) -> str:
        chars = list(w)
        for i in range(len(chars) - 1):
            if chars[i] in VOWELS_MAP:
                chars[i] = VOWELS_MAP[chars[i]]
        return "".join(chars)
    return " ".join(clean_single_word(w) for w in s.split())

def clean_parentheses(s: str) -> str:
    """Remove parenthesized qualifiers (e.g. '(rare) arrabbiare' -> 'arrabbiare')."""
    if not s:
        return s
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

# Moods and tenses person mappings
PERSON_MAPPING = [
    ({"first-person", "singular"}, "io"),
    ({"second-person", "singular"}, "tu"),
    ({"third-person", "singular"}, "lui, lei, Lei, egli"),
    ({"first-person", "plural"}, "noi"),
    ({"second-person", "plural"}, "voi"),
    ({"third-person", "plural"}, "loro, Loro, essi")
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
        "participio presente": "—"
    }
    
    # Initialize basic conjugation structure
    conjugations = {
        "indicativo": {
            "presente": {},
            "imperfetto": {},
            "passato remoto": {},
            "futuro semplice": {}
        },
        "congiuntivo": {
            "presente": {},
            "imperfetto": {}
        },
        "condizionale": {
            "presente": {}
        },
        "imperativo": {
            "presente": {}
        }
    }
    
    has_conjugations = False
    
    # Parse each form from the conjugation template
    for f in forms:
        if f.get("source") != "conjugation":
            continue
            
        tags = set(f.get("tags", []))
        form_val = clean_accents(f.get("form", ""))
        if not form_val:
            continue

        # Skip archaic, obsolete, literary, rare, and regional forms
        if tags & {"archaic", "obsolete", "literary", "rare", "regional"}:
            continue
            
        # Principal forms
        if "gerund" in tags:
            principal_forms["gerundio"] = form_val
        elif "participle" in tags and "past" in tags:
            principal_forms["participio passato"] = form_val
        elif "participle" in tags and "present" in tags:
            principal_forms["participio presente"] = form_val
            
        # Indicativo Presente
        if "indicative" in tags and "present" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["indicativo"]["presente"]:
                    conjugations["indicativo"]["presente"][person] = form_val
                    has_conjugations = True
                    
        # Indicativo Imperfetto
        elif "indicative" in tags and "imperfect" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["indicativo"]["imperfetto"]:
                    conjugations["indicativo"]["imperfetto"][person] = form_val
                    has_conjugations = True
                    
        # Indicativo Passato Remoto
        elif "indicative" in tags and "historic" in tags and "past" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["indicativo"]["passato remoto"]:
                    conjugations["indicativo"]["passato remoto"][person] = form_val
                    has_conjugations = True
                    
        # Indicativo Futuro Semplice
        elif "indicative" in tags and "future" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["indicativo"]["futuro semplice"]:
                    conjugations["indicativo"]["futuro semplice"][person] = form_val
                    has_conjugations = True
                    
        # Congiuntivo Presente
        elif "subjunctive" in tags and "present" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["congiuntivo"]["presente"]:
                    conjugations["congiuntivo"]["presente"][person] = form_val
                    has_conjugations = True
                    
        # Congiuntivo Imperfetto
        elif "subjunctive" in tags and "imperfect" in tags:
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["congiuntivo"]["imperfetto"]:
                    conjugations["congiuntivo"]["imperfetto"][person] = form_val
                    has_conjugations = True
                    
        # Condizionale Presente
        elif "conditional" in tags and "present" in tags or ("conditional" in tags and not any(t in tags for t in ["past", "perfect"])):
            for tag_set, person in PERSON_MAPPING:
                if tag_set.issubset(tags) and person not in conjugations["condizionale"]["presente"]:
                    conjugations["condizionale"]["presente"][person] = form_val
                    has_conjugations = True
                    
        # Imperativo Presente
        elif "imperative" in tags and "present" in tags or ("imperative" in tags and "negative" not in tags):
            is_formal = "formal" in tags or "second-person-semantically" in tags
            
            # Map second person singular
            if "singular" in tags:
                if "second-person" in tags and not is_formal and "(tu)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(tu)"] = form_val
                    has_conjugations = True
                elif (is_formal or "third-person" in tags) and "(Lei)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(Lei)"] = form_val
                    has_conjugations = True
            elif "plural" in tags:
                if "first-person" in tags and "(noi)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(noi)"] = form_val
                    has_conjugations = True
                elif "second-person" in tags and "(voi)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(voi)"] = form_val
                    has_conjugations = True
                elif (is_formal or "third-person" in tags) and "(Loro)" not in conjugations["imperativo"]["presente"]:
                    conjugations["imperativo"]["presente"]["(Loro)"] = form_val
                    has_conjugations = True
                    
    return auxiliary, model, principal_forms, conjugations, has_conjugations

def build_database():
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
    
    local_path = os.path.basename(KAIKKI_URL)
    if os.path.exists(local_path):
        print(f"Using local file: {local_path}")
        reader = codecs.getreader("utf-8")(open(local_path, "rb"))
    else:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(KAIKKI_URL, headers=headers)
        print(f"Streaming Wiktionary data from {KAIKKI_URL}...")
        reader = codecs.getreader("utf-8")(urllib.request.urlopen(req))
    
    verbs_to_insert = []
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
                auxiliary, model, principal_forms, conjugations, has_conjugations = extract_conjugations_and_metadata(entry)
                
                # Check if it's a form-of entry
                is_form_of = False
                form_of_verbs = []
                
                senses = entry.get("senses", [])
                for sense in senses:
                    if "form_of" in sense:
                        is_form_of = True
                        for fo in sense["form_of"]:
                            fword = clean_parentheses(clean_accents(fo.get("word")))
                            if fword:
                                form_of_verbs.append(fword)
                                
                if "form_of" in entry:
                    is_form_of = True
                    for fo in entry["form_of"]:
                        fword = clean_parentheses(clean_accents(fo.get("word")))
                        if fword:
                            form_of_verbs.append(fword)
                            
                if is_form_of and not has_conjugations:
                    for fv in form_of_verbs:
                        forms_to_insert.add((word, fv))
                        form_ref_count += 1
                    continue
                    
                # Store verb as a main verb entry (compress JSONs with zlib)
                if has_conjugations or "head_templates" in entry:
                    comp_conj = zlib.compress(json.dumps(conjugations, ensure_ascii=False).encode('utf-8'))
                    comp_pf = zlib.compress(json.dumps(principal_forms, ensure_ascii=False).encode('utf-8'))
                    
                    verbs_to_insert.append((
                        word,
                        sqlite3.Binary(comp_conj),
                        auxiliary,
                        model,
                        sqlite3.Binary(comp_pf)
                    ))
                    verb_count += 1
                    
                    forms_to_insert.add((word, word))
                    for fv in form_of_verbs:
                        forms_to_insert.add((word, fv))
                    
                    for f in entry.get("forms", []):
                        if f.get("source") == "conjugation":
                            fval = clean_accents(f.get("form"))
                            if fval:
                                forms_to_insert.add((fval, word))
                                parts = fval.split()
                                if len(parts) > 1:
                                    forms_to_insert.add((parts[-1], word))
                                    
                # Write in batches of 1000
                if len(verbs_to_insert) >= 1000:
                    cursor.executemany(
                        "INSERT OR REPLACE INTO verbs VALUES (?, ?, ?, ?, ?)",
                        verbs_to_insert
                    )
                    verbs_to_insert.clear()
                    
                    cursor.executemany(
                        "INSERT OR IGNORE INTO forms VALUES (?, ?)",
                        list(forms_to_insert)
                    )
                    forms_to_insert.clear()
                    conn.commit()
                    
            except Exception as e:
                pass
                    
    except Exception as e:
        print("Error during streaming:", e)
        conn.close()
        sys.exit(1)
        
    # Flush remaining batch
    if verbs_to_insert:
        cursor.executemany(
            "INSERT OR REPLACE INTO verbs VALUES (?, ?, ?, ?, ?)",
            verbs_to_insert
        )
    if forms_to_insert:
        cursor.executemany(
            "INSERT OR IGNORE INTO forms VALUES (?, ?)",
            list(forms_to_insert)
        )
        
    conn.commit()
    conn.close()
    
    print("Database build completed successfully!")
    print(f"Total lines processed: {line_count}")
    print(f"Total main verbs loaded: {verb_count}")
    print(f"Total reverse lookup forms loaded: {form_ref_count}")

if __name__ == "__main__":
    build_database()
