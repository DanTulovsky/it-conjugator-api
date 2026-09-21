import os
import json
import sqlite3
import zlib
from typing import Dict, Any, Optional

from .config import VERBS_DB_PATH

DB_PATH = VERBS_DB_PATH

VOWELS_MAP = {
    'à': 'a', 'á': 'a', 'è': 'e', 'é': 'e', 'ì': 'i', 'í': 'i', 'ò': 'o', 'ó': 'o', 'ù': 'u', 'ú': 'u',
    'À': 'A', 'Á': 'A', 'È': 'E', 'É': 'E', 'Ì': 'I', 'Í': 'I', 'Ò': 'O', 'Ó': 'O', 'Ù': 'U', 'Ú': 'U'
}

def clean_accents(s: str) -> str:
    """Strip internal dictionary stress accents, preserving final accents."""
    if not s:
        return s
    def clean_single_word(w: str) -> str:
        chars = list(w)
        for i in range(len(chars) - 1):
            if chars[i] in VOWELS_MAP:
                chars[i] = VOWELS_MAP[chars[i]]
        return "".join(chars)
    return " ".join(clean_single_word(w) for w in s.split())

# Auxiliary simple tenses mapping for generating compound tenses
AUX_TEMPLATES = {
    "essere": {
        "presente": {"io": "sono", "tu": "sei", "lui, lei, Lei, egli": "è", "noi": "siamo", "voi": "siete", "loro, Loro, essi": "sono"},
        "imperfetto": {"io": "ero", "tu": "eri", "lui, lei, Lei, egli": "era", "noi": "eravamo", "voi": "eravate", "loro, Loro, essi": "erano"},
        "passato remoto": {"io": "fui", "tu": "fosti", "lui, lei, Lei, egli": "fu", "noi": "fummo", "voi": "foste", "loro, Loro, essi": "furono"},
        "futuro semplice": {"io": "sarò", "tu": "sarai", "lui, lei, Lei, egli": "sarà", "noi": "saremo", "voi": "sarete", "loro, Loro, essi": "saranno"},
        "congiuntivo_presente": {"io": "sia", "tu": "sia", "lui, lei, Lei, egli": "sia", "noi": "siamo", "voi": "siate", "loro, Loro, essi": "siano"},
        "congiuntivo_imperfetto": {"io": "fossi", "tu": "fossi", "lui, lei, Lei, egli": "fosse", "noi": "fossimo", "voi": "foste", "loro, Loro, essi": "fossero"},
        "condizionale_presente": {"io": "sarei", "tu": "saresti", "lui, lei, Lei, egli": "sarebbe", "noi": "saremmo", "voi": "sareste", "loro, Loro, essi": "sarebbero"}
    },
    "avere": {
        "presente": {"io": "ho", "tu": "hai", "lui, lei, Lei, egli": "ha", "noi": "abbiamo", "voi": "avete", "loro, Loro, essi": "hanno"},
        "imperfetto": {"io": "avevo", "tu": "avevi", "lui, lei, Lei, egli": "aveva", "noi": "avevamo", "voi": "avevate", "loro, Loro, essi": "avevano"},
        "passato remoto": {"io": "ebbi", "tu": "avesti", "lui, lei, Lei, egli": "ebbe", "noi": "avemmo", "voi": "aveste", "loro, Loro, essi": "ebbero"},
        "futuro semplice": {"io": "avrò", "tu": "avrai", "lui, lei, Lei, egli": "avrà", "noi": "avremo", "voi": "avrete", "loro, Loro, essi": "avranno"},
        "congiuntivo_presente": {"io": "abbia", "tu": "abbia", "lui, lei, Lei, egli": "abbia", "noi": "abbiamo", "voi": "abbiate", "loro, Loro, essi": "abbiano"},
        "congiuntivo_imperfetto": {"io": "avessi", "tu": "avessi", "lui, lei, Lei, egli": "avesse", "noi": "avessimo", "voi": "aveste", "loro, Loro, essi": "avessero"},
        "condizionale_presente": {"io": "avrei", "tu": "avresti", "lui, lei, Lei, egli": "avrebbe", "noi": "avremmo", "voi": "avreste", "loro, Loro, essi": "avrebbero"}
    }
}

def build_compound_tenses(conjugations: Dict[str, Any], auxiliary: str, pp: str, is_reflexive: bool, verb_type: str) -> None:
    """
    Fills in all Italian compound tenses dynamically using the auxiliary verb's simple tenses
    and past participle agreement.
    """
    if not pp or pp == "—":
        return
        
    aux_simple = AUX_TEMPLATES.get(auxiliary, AUX_TEMPLATES["avere"])
    
    # Initialize compound tenses sections
    if "tempi composti" not in conjugations:
        conjugations["tempi composti"] = {}
        
    for tense in ["passato prossimo", "trapassato prossimo", "trapassato remoto", "futuro anteriore"]:
        if tense not in conjugations["tempi composti"]:
            conjugations["tempi composti"][tense] = {}
            
    if "passato" not in conjugations["congiuntivo"]:
        conjugations["congiuntivo"]["passato"] = {}
    if "trapassato" not in conjugations["congiuntivo"]:
        conjugations["congiuntivo"]["trapassato"] = {}
        
    if "passato" not in conjugations["condizionale"]:
        conjugations["condizionale"]["passato"] = {}
        
    persons = ["io", "tu", "lui, lei, Lei, egli", "noi", "voi", "loro, Loro, essi"]
    
    def assemble_compound_form(clitic_str, aux_form, pp_form):
        if not clitic_str:
            return f"{aux_form} {pp_form}"
        starts_with_vowel = aux_form[0].lower() in ['a', 'e', 'i', 'o', 'u', 'h', 'è', 'é', 'ò']
        if starts_with_vowel:
            if clitic_str.endswith(" la "):
                clitic_str = clitic_str[:-4] + " l'"
            elif clitic_str.endswith(" lo "):
                clitic_str = clitic_str[:-4] + " l'"
        return f"{clitic_str}{aux_form} {pp_form}".strip()
        
    for person in persons:
        # Extract clitic dynamically from present simple
        clitic = ""
        pres_simple = conjugations.get("indicativo", {}).get("presente", {}).get(person, "")
        parts = pres_simple.split()
        if len(parts) > 1:
            clitic = " ".join(parts[:-1]) + " "
            
        # Participle agreement
        pp_agree = pp
        clean_pp = pp
        if clean_pp.endswith("sela"):
            clean_pp = clean_pp[:-4]
        elif clean_pp.endswith("cela"):
            clean_pp = clean_pp[:-4]
        elif clean_pp.endswith("sene"):
            clean_pp = clean_pp[:-4]
        elif clean_pp.endswith("cene"):
            clean_pp = clean_pp[:-4]
        elif clean_pp.endswith("si"):
            clean_pp = clean_pp[:-2]
            
        if clean_pp.endswith("o") or clean_pp.endswith("a"):
            is_plural = person in ["noi", "voi", "loro, Loro, essi"]
            if verb_type in ["sela", "cela"]:
                # Feminine agreement due to preceding 'la' direct object
                pp_agree = clean_pp[:-1] + ("e" if is_plural else "a")
            elif auxiliary == "essere":
                # Standard subject agreement for essere (o/i)
                pp_agree = clean_pp[:-1] + ("i" if is_plural else "o")
            else:
                pp_agree = clean_pp
                
        conjugations["tempi composti"]["passato prossimo"][person] = assemble_compound_form(clitic, aux_simple["presente"][person], pp_agree)
        conjugations["tempi composti"]["trapassato prossimo"][person] = assemble_compound_form(clitic, aux_simple["imperfetto"][person], pp_agree)
        conjugations["tempi composti"]["trapassato remoto"][person] = assemble_compound_form(clitic, aux_simple["passato remoto"][person], pp_agree)
        conjugations["tempi composti"]["futuro anteriore"][person] = assemble_compound_form(clitic, aux_simple["futuro semplice"][person], pp_agree)
        
        conjugations["congiuntivo"]["passato"][person] = assemble_compound_form(clitic, aux_simple["congiuntivo_presente"][person], pp_agree)
        conjugations["congiuntivo"]["trapassato"][person] = assemble_compound_form(clitic, aux_simple["congiuntivo_imperfetto"][person], pp_agree)
        
        conjugations["condizionale"]["passato"][person] = assemble_compound_form(clitic, aux_simple["condizionale_presente"][person], pp_agree)

def conjugate_pronominal_dynamically(base_data: Dict[str, Any], verb_query: str, verb_type: str) -> Dict[str, Any]:
    """
    Constructs a complete conjugation table for a pronominal verb dynamically
    from its base verb conjugations.
    """
    infinitive = verb_query
    base_infinitive = base_data["queried"]
    base_conj = base_data["conjugations"]
    base_pf = base_data["principal_forms"]
    
    reflexive_clitics = {"io": "mi", "tu": "ti", "lui, lei, Lei, egli": "si", "noi": "ci", "voi": "vi", "loro, Loro, essi": "si"}
    la_clitics = {"io": "me la", "tu": "te la", "lui, lei, Lei, egli": "se la", "noi": "ce la", "voi": "ve la", "loro, Loro, essi": "se la"}
    ne_clitics = {"io": "me ne", "tu": "te ne", "lui, lei, Lei, egli": "se ne", "noi": "ce ne", "voi": "ve ne", "loro, Loro, essi": "se ne"}
    
    is_reflexive = False
    auxiliary = base_data["auxiliary"] or "avere"
    
    if verb_type == "si":
        is_reflexive = True
        auxiliary = "essere"
        clitic_map = reflexive_clitics
    elif verb_type == "sela":
        is_reflexive = True
        auxiliary = "essere"
        clitic_map = la_clitics
    elif verb_type == "sene":
        is_reflexive = True
        auxiliary = "essere"
        clitic_map = ne_clitics
    elif verb_type == "ci":
        is_reflexive = False
        clitic_map = {p: "ci" for p in reflexive_clitics}
    elif verb_type == "cela":
        is_reflexive = False
        clitic_map = {p: "ce la" for p in reflexive_clitics}
    elif verb_type == "cene":
        is_reflexive = False
        clitic_map = {p: "ce ne" for p in reflexive_clitics}
    else:
        is_reflexive = False
        clitic_map = {p: "" for p in reflexive_clitics}
        
    base_gerund = base_pf.get("gerundio", "")
    base_pp = base_pf.get("participio passato", "")
    
    gerundio = "—"
    if base_gerund and base_gerund != "—":
        if verb_type == "si": gerundio = base_gerund + "si"
        elif verb_type == "sela": gerundio = base_gerund[:-1] + "andocela" if base_gerund.endswith("ando") else base_gerund + "sela"
        elif verb_type == "ci": gerundio = base_gerund + "ci"
        elif verb_type == "cela": gerundio = base_gerund + "cela"
        else: gerundio = base_gerund + verb_type
        
    participio_passato = "—"
    if base_pp and base_pp != "—":
        if verb_type == "si": participio_passato = base_pp + "si"
        elif verb_type == "sela": participio_passato = base_pp + "sela"
        elif verb_type == "ci": participio_passato = base_pp + "ci"
        elif verb_type == "cela": participio_passato = base_pp + "cela"
        else: participio_passato = base_pp + verb_type

    principal_forms = {
        "infinito": infinitive,
        "gerundio": clean_accents(gerundio),
        "participio passato": clean_accents(participio_passato)
    }
    
    conjugations = {}
    for mood, tenses in base_conj.items():
        if mood == "tempi composti":
            continue
            
        conjugations[mood] = {}
        for tense, people in tenses.items():
            if tense in ["passato", "trapassato"]:
                continue
                
            conjugations[mood][tense] = {}
            for person, form in people.items():
                if not form or form == "—":
                    continue
                    
                c_person = person
                if mood == "imperativo":
                    c_person = "tu" if person == "(tu)" else "Lei" if person == "(Lei)" else "noi" if person == "(noi)" else "voi" if person == "(voi)" else "Loro" if person == "(Loro)" else "tu"
                    
                clitic = clitic_map.get(c_person, "")
                form_parts = form.split()
                first_word = form_parts[0] if form_parts else form
                
                final_clitic = clitic
                if clitic.endswith(" la") and first_word in ["ho", "hai", "ha", "hanno", "era", "ero", "eri", "erano", "ebbi", "ebbe", "ebbero"]:
                    final_clitic = clitic[:-2] + "l'"
                elif clitic.endswith(" lo") and first_word in ["ho", "hai", "ha", "hanno", "era", "ero", "eri", "erano", "ebbi", "ebbe", "ebbero"]:
                    final_clitic = clitic[:-2] + "l'"
                    
                prefix = f"{final_clitic} " if final_clitic else ""
                is_suffix_imperative = mood == "imperativo" and person in ["(tu)", "(noi)", "(voi)"]
                
                if is_suffix_imperative:
                    suffix = verb_type
                    if form in ["di", "fa", "da", "sta", "va"]:
                        if suffix.startswith(("s", "l", "c", "v")):
                            suffix = suffix[0] + suffix
                    conjugations[mood][tense][person] = clean_accents(f"{form}{suffix}")
                else:
                    conjugations[mood][tense][person] = clean_accents(f"{prefix}{form}")
                    
    clean_pp_base = base_pp
    if clean_pp_base and clean_pp_base.endswith("si"):
        clean_pp_base = clean_pp_base[:-2]
        
    build_compound_tenses(conjugations, auxiliary, clean_pp_base, is_reflexive, verb_type)
    
    return {
        "queried": infinitive,
        "url": f"https://www.wordreference.com/conj/itverbs.aspx?v={infinitive}",
        "model": base_data.get("model", base_infinitive),
        "principal_forms": principal_forms,
        "auxiliary": auxiliary,
        "conjugations": conjugations
    }

def resolve_pronominal_base(verb_query: str) -> Optional[tuple[str, str]]:
    """
    Detects if a verb query is pronominal, and returns its base verb and pronominal type.
    E.g. 'mettercela' -> ('mettere', 'cela')
    """
    suffixes = [
        ("sela", 4), ("sene", 4), ("cela", 4), ("cene", 4),
        ("si", 2), ("ci", 2), ("ne", 2)
    ]
    
    for suffix, length in suffixes:
        if verb_query.endswith(suffix):
            stem = verb_query[:-length]
            candidates = [stem + "e", stem]
            
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            for cand in candidates:
                cursor.execute("SELECT 1 FROM verbs WHERE infinitive = ?", (cand,))
                if cursor.fetchone():
                    conn.close()
                    return cand, suffix
            conn.close()
            
    return None

def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
        
    return previous_row[-1]

def find_fuzzy_infinitive(cursor: sqlite3.Cursor, query: str) -> Optional[str]:
    words = query.strip().split()
    if not words:
        return None
        
    candidate = words[-1]
    if len(candidate) < 4:
        return None
        
    prefix = candidate[:4]
    cursor.execute("""
        SELECT DISTINCT form, infinitive 
        FROM forms 
        WHERE form LIKE ?
    """, (prefix + "%",))
    
    rows = cursor.fetchall()
    if not rows:
        return None
        
    best_dist = 999
    best_infinitives = []
    
    for form, infinitive in rows:
        dist = levenshtein_distance(form, candidate)
        if dist < best_dist:
            best_dist = dist
            best_infinitives = [infinitive]
        elif dist == best_dist:
            best_infinitives.append(infinitive)
            
    if best_dist > 2:
        return None
        
    if len(best_infinitives) > 1:
        has_reflexive_clitic = any(w in ["mi", "ti", "si", "ci", "vi", "me", "te", "se", "ce", "ve"] for w in words[:-1])
        if has_reflexive_clitic:
            for inf in best_infinitives:
                if inf.endswith(("si", "sela", "sene", "ci", "cela", "cene")):
                    return inf
                    
    return best_infinitives[0]

def get_conjugations(verb_query: str) -> Optional[Dict[str, Any]]:
    """
    Queries the SQLite database for the verb conjugation table.
    Supports reverse lookup (conjugation forms) and dynamic pronominal verb construction.
    """
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database file not found at {DB_PATH}. Please run build_db.py first.")
        
    cleaned_query = clean_accents(verb_query).strip().strip("\"'")
    if not cleaned_query:
        return None
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    resolved_infinitive = cleaned_query
    has_direct_match = False
    
    # 1. Direct match: check if the queried verb exists directly in verbs table
    cursor.execute("SELECT conjugation_json FROM verbs WHERE infinitive = ?", (cleaned_query,))
    row = cursor.fetchone()
    if row:
        conj_blob = row[0]
        if conj_blob:
            conj_dict = json.loads(zlib.decompress(conj_blob).decode('utf-8'))
            if conj_dict.get("indicativo", {}).get("presente"):
                has_direct_match = True
                
    # 2. Reverse lookup: if not found directly, check the forms index
    if not has_direct_match:
        cursor.execute("SELECT DISTINCT infinitive FROM forms WHERE form = ? ORDER BY length(infinitive) ASC", (cleaned_query,))
        rows = cursor.fetchall()
        for r in rows:
            cand = r[0]
            cursor.execute("SELECT conjugation_json FROM verbs WHERE infinitive = ?", (cand,))
            v_row = cursor.fetchone()
            if v_row:
                try:
                    conj_dict = json.loads(zlib.decompress(v_row[0]).decode('utf-8'))
                    if conj_dict.get("indicativo", {}).get("presente"):
                        resolved_infinitive = cand
                        break
                except Exception:
                    pass
            
    # 3. Fetch main verb data
    cursor.execute(
        "SELECT conjugation_json, auxiliary, model, principal_forms_json FROM verbs WHERE infinitive = ?",
        (resolved_infinitive,)
    )
    row = cursor.fetchone()
    
    # Fuzzy match fallback if not found
    if not row:
        fuzzy_infinitive = find_fuzzy_infinitive(cursor, cleaned_query)
        if fuzzy_infinitive:
            resolved_infinitive = fuzzy_infinitive
            cursor.execute(
                "SELECT conjugation_json, auxiliary, model, principal_forms_json FROM verbs WHERE infinitive = ?",
                (resolved_infinitive,)
            )
            row = cursor.fetchone()
            
    conn.close()
    
    if row:
        conjugation_blob, auxiliary, model, principal_forms_blob = row
        conjugations = json.loads(zlib.decompress(conjugation_blob).decode('utf-8'))
        principal_forms = json.loads(zlib.decompress(principal_forms_blob).decode('utf-8'))
        
        # Verify conjugation table is not empty
        if conjugations.get("indicativo", {}).get("presente"):
            # Load compound tenses dynamically
            pp = principal_forms.get("participio passato", "")
            is_reflexive = resolved_infinitive.endswith(("si", "sela", "cela", "sene", "cene"))
            
            verb_type = ""
            for suffix in ["sela", "sene", "cela", "cene", "si", "ci", "ne"]:
                if resolved_infinitive.endswith(suffix):
                    verb_type = suffix
                    break
                    
            build_compound_tenses(conjugations, auxiliary, pp, is_reflexive, verb_type)
            
            return {
                "queried": resolved_infinitive,
                "url": f"https://www.wordreference.com/conj/itverbs.aspx?v={resolved_infinitive}",
                "model": model,
                "principal_forms": principal_forms,
                "auxiliary": auxiliary,
                "conjugations": conjugations
            }
            
    # 4. Dynamic pronominal lookup fallback
    pronominal_resolution = resolve_pronominal_base(resolved_infinitive)
    if pronominal_resolution:
        base_infinitive, verb_type = pronominal_resolution
        base_data = get_conjugations(base_infinitive)
        if base_data:
            return conjugate_pronominal_dynamically(base_data, resolved_infinitive, verb_type)
            
    return None
