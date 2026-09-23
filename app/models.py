from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, constr

# ---- Literals: exactly what we scrape from WR ----
Mood = Literal[
    "indicativo",
    "tempi composti",
    "congiuntivo",
    "condizionale",
    "imperativo",
]

TenseIndicativo = Literal["presente", "imperfetto", "passato remoto", "futuro semplice"]
TenseTempiComposti = Literal[
    "passato prossimo", "trapassato prossimo", "trapassato remoto", "futuro anteriore"
]
TenseCongiuntivo = Literal["presente", "imperfetto", "passato", "trapassato"]
TenseCondizionale = Literal["presente", "passato"]
TenseImperativo = Literal["presente"]

# Union of all tenses
Tense = Literal[
    "presente",
    "imperfetto",
    "passato remoto",
    "futuro semplice",
    "passato prossimo",
    "trapassato prossimo",
    "trapassato remoto",
    "futuro anteriore",
    "passato",
    "trapassato",
]

PersonDefault = Literal[
    "io", "tu", "lui, lei, Lei, egli", "noi", "voi", "loro, Loro, essi"
]
PersonImperative = Literal["", "(tu)", "(Lei)", "(noi)", "(voi)", "(Loro)"]
Person = Literal[
    "",
    "(tu)",
    "(Lei)",
    "(noi)",
    "(voi)",
    "(Loro)",
    "io",
    "tu",
    "lui, lei, Lei, egli",
    "noi",
    "voi",
    "loro, Loro, essi",
    "lui",
    "lei",
    "Lei",
    "egli",
    "loro",
    "Loro",
    "essi",
]


# ---- Request / Response models ----
class ConjugateQuery(BaseModel):
    v: constr(min_length=1) = Field(..., description="Italian verb (infinitive)")
    full: bool = Field(True, description="If true, return full JSON and ignore filters")
    moods: list[Mood] | None = Field(
        None,
        description="Allowed: indicativo, tempi composti, congiuntivo, condizionale, imperativo",
    )
    tenses: list[Tense] | None = Field(
        None,
        description="Allowed: presente, imperfetto, passato remoto, futuro semplice, passato prossimo, trapassato prossimo, trapassato remoto, futuro anteriore, passato, trapassato",
    )
    persons: list[Person] | None = Field(
        None, description="Allowed: io/tu/... or '', (tu), (Lei), (noi), (voi), (Loro)"
    )


class ConjugationResponse(BaseModel):
    queried: str
    url: str
    model: str | None = None
    principal_forms: dict[str, Any]
    auxiliary: str | None = None
    conjugations: dict[str, dict[str, dict[str, str]]]


class APIResponse(BaseModel):
    success: bool
    requested: ConjugateQuery | None = None
    note: str | None = None
    error: str | None = None
    data: ConjugationResponse | None = None


# ---- Dictionary (/define) models ----
class SenseModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    glosses: list[str] | None = None
    raw_glosses: list[str] | None = None
    tags: list[str] | None = None
    form_of: list[Any] | None = None
    examples: list[Any] | None = None
    synonyms: list[Any] | None = None
    antonyms: list[Any] | None = None


class FormModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    form: str
    tags: list[str] | None = None


class DictionaryEntry(BaseModel):
    """One Wiktionary entry (word + part of speech + etymology section)."""

    model_config = ConfigDict(extra="allow")
    word: str
    pos: str | None = None
    etymology_number: str | None = None
    is_lemma: bool = False
    head: list[str] | None = None
    etymology: str | None = None
    ipa: list[str] | None = None
    rhymes: list[str] | None = None
    hyphenation: list[str] | None = None
    senses: list[SenseModel] | None = None
    forms: list[FormModel] | None = None


class DefinitionResponse(BaseModel):
    queried: str
    entries: list[DictionaryEntry]


class DefineResponse(BaseModel):
    success: bool
    requested: str | None = None
    note: str | None = None
    error: str | None = None
    data: DefinitionResponse | None = None


class HealthResponse(BaseModel):
    ok: bool
    databases: dict[str, bool] = Field(
        ..., description="Presence of each required SQLite database"
    )
