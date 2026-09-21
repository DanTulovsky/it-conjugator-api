from __future__ import annotations
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, ConfigDict, Field, ValidationError, constr

# ---- Literals: exactly what we scrape from WR ----
Mood = Literal[
    "indicativo",
    "tempi composti",
    "congiuntivo",
    "condizionale",
    "imperativo",
]

TenseIndicativo = Literal["presente", "imperfetto", "passato remoto", "futuro semplice"]
TenseTempiComposti = Literal["passato prossimo", "trapassato prossimo", "trapassato remoto", "futuro anteriore"]
TenseCongiuntivo = Literal["presente", "imperfetto", "passato", "trapassato"]
TenseCondizionale = Literal["presente", "passato"]
TenseImperativo = Literal["presente"]

# Union of all tenses
Tense = Literal[
    "presente", "imperfetto", "passato remoto", "futuro semplice",
    "passato prossimo", "trapassato prossimo", "trapassato remoto", "futuro anteriore",
    "passato", "trapassato",
]

PersonDefault = Literal["io", "tu", "lui, lei, Lei, egli", "noi", "voi", "loro, Loro, essi"]
PersonImperative = Literal["", "(tu)", "(Lei)", "(noi)", "(voi)", "(Loro)"]
Person = Literal["", "(tu)", "(Lei)", "(noi)", "(voi)", "(Loro)",
                 "io", "tu", "lui, lei, Lei, egli", "noi", "voi", "loro, Loro, essi"]

# ---- Request / Response models ----
class ConjugateQuery(BaseModel):
    v: constr(min_length=1) = Field(..., description="Italian verb (infinitive)")
    full: bool = Field(True, description="If true, return full JSON and ignore filters")
    moods: Optional[List[Mood]] = Field(None, description="Allowed: indicativo, tempi composti, congiuntivo, condizionale, imperativo")
    tenses: Optional[List[Tense]] = Field(None, description="Allowed: presente, imperfetto, passato remoto, futuro semplice, passato prossimo, trapassato prossimo, trapassato remoto, futuro anteriore, passato, trapassato")
    persons: Optional[List[Person]] = Field(None, description="Allowed: io/tu/... or '', (tu), (Lei), (noi), (voi), (Loro)")

class ConjugationResponse(BaseModel):
    queried: str
    url: str
    model: Optional[str] = None
    principal_forms: Dict[str, Any]
    auxiliary: Optional[str] = None
    conjugations: Dict[str, Dict[str, Dict[str, str]]]

class APIResponse(BaseModel):
    success: bool
    requested: Optional[ConjugateQuery] = None
    note: Optional[str] = None
    error: Optional[str] = None
    data: Optional[ConjugationResponse] = None

# ---- Dictionary (/define) models ----
class SenseModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    glosses: Optional[List[str]] = None
    raw_glosses: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    form_of: Optional[List[Any]] = None
    examples: Optional[List[Any]] = None
    synonyms: Optional[List[Any]] = None
    antonyms: Optional[List[Any]] = None

class FormModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    form: str
    tags: Optional[List[str]] = None

class DictionaryEntry(BaseModel):
    """One Wiktionary entry (word + part of speech + etymology section)."""
    model_config = ConfigDict(extra="allow")
    word: str
    pos: Optional[str] = None
    etymology_number: Optional[str] = None
    is_lemma: bool = False
    head: Optional[List[str]] = None
    etymology: Optional[str] = None
    ipa: Optional[List[str]] = None
    rhymes: Optional[List[str]] = None
    hyphenation: Optional[List[str]] = None
    senses: Optional[List[SenseModel]] = None
    forms: Optional[List[FormModel]] = None

class DefinitionResponse(BaseModel):
    queried: str
    entries: List[DictionaryEntry]

class DefineResponse(BaseModel):
    success: bool
    requested: Optional[str] = None
    note: Optional[str] = None
    error: Optional[str] = None
    data: Optional[DefinitionResponse] = None

class HealthResponse(BaseModel):
    ok: bool
    databases: Dict[str, bool] = Field(..., description="Presence of each required SQLite database")
