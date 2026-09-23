from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

PERSON_ALIASES = {
    "lui, lei, lei, egli": {"lui, lei, lei, egli", "lui", "lei", "egli"},
    "loro, loro, essi": {"loro, loro, essi", "loro", "essi"},
    "(tu)": {"(tu)", "tu"},
    "(lei)": {"(lei)", "lei"},
    "(noi)": {"(noi)", "noi"},
    "(voi)": {"(voi)", "voi"},
    "(loro)": {"(loro)", "loro"},
}


def _split_csv_preserving_phrases(s: Any) -> list[str] | None:
    if not isinstance(s, str) or not s.strip():
        return None
    normalized = s
    placeholders = {}
    for target in ["lui, lei, Lei, egli", "loro, Loro, essi"]:
        pattern = re.compile(
            r"\b" + r"\s*,\s*".join(target.split(",")) + r"\b", re.IGNORECASE
        )
        for m in pattern.finditer(normalized):
            key = f"__PH_{len(placeholders)}__"
            placeholders[key] = m.group(0)
            normalized = normalized[: m.start()] + key + normalized[m.end() :]
            break
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    return [placeholders.get(p, p) for p in parts]


def _to_set(val: Any) -> set[str] | None:
    if not val:
        return None
    if isinstance(val, (list, set)):
        items = val
    elif isinstance(val, str):
        items = _split_csv_preserving_phrases(val) or []
    else:
        return None
    return {p.strip().lower() for p in items if p and isinstance(p, str) and p.strip()}


def _person_matches(person_key: str, pset: set[str]) -> bool:
    p_lower = person_key.strip().lower()
    if p_lower in pset:
        return True
    aliases = PERSON_ALIASES.get(p_lower)
    if aliases and bool(aliases & pset):
        return True
    return False


def apply_filters(
    data: dict[str, Any],
    moods: str | list[str] | set[str] | None = None,
    tenses: str | list[str] | set[str] | None = None,
    persons: str | list[str] | set[str] | None = None,
    full: bool = True,
) -> dict[str, Any]:
    """Return filtered copy; preserves original order of keys."""
    if full:
        return data

    mset = _to_set(moods)
    tset = _to_set(tenses)
    pset = _to_set(persons)

    conj = data.get("conjugations", {})
    new_conj: dict[str, Any] = {}

    for mood, tenses_map in conj.items():
        if mset and mood.lower() not in mset:
            continue
        new_tenses_map: dict[str, Any] = {}
        for tense, person_map in tenses_map.items():
            if tset and tense.lower() not in tset:
                continue
            if pset:
                filtered_person_map = OrderedDict(
                    (p, f) for p, f in person_map.items() if _person_matches(p, pset)
                )
            else:
                filtered_person_map = person_map
            if filtered_person_map:
                new_tenses_map[tense] = filtered_person_map
        if new_tenses_map:
            new_conj[mood] = new_tenses_map

    new_data = dict(data)
    new_data["conjugations"] = new_conj
    return new_data
