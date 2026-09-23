from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import ValidationError

from .config import DICTIONARY_DB_PATH, SWAGGER_PATH, VERBS_DB_PATH
from .db_core import get_conjugations
from .dictionary_core import get_definitions
from .filters import _split_csv_preserving_phrases, apply_filters
from .models import (
    APIResponse,
    ConjugateQuery,
    ConjugationResponse,
    DefineResponse,
    DefinitionResponse,
    HealthResponse,
)

API_KEY = os.getenv("SCRAPER_API_KEY")  # set via Docker env

# Documented as an OpenAPI apiKey security scheme. auto_error=False so the
# endpoints keep returning their own 401 with a consistent body.
api_key_header = APIKeyHeader(
    name="X-API-Key", scheme_name="X-API-Key", auto_error=False
)

REQUIRED_DATABASES = {
    "conjugations (verbs.db)": VERBS_DB_PATH,
    "dictionary (dictionary.db)": DICTIONARY_DB_PATH,
}


def _missing_databases() -> list[tuple[str, str]]:
    return [
        (label, path)
        for label, path in REQUIRED_DATABASES.items()
        if not os.path.exists(path)
    ]


def _contract_problem() -> str | None:
    """Return a message if swagger.json is missing, unreadable, or out of date.

    The served contract is still generated live from the app; this only guards
    against the checked-in ``swagger.json`` drifting from it.
    """
    if not os.path.exists(SWAGGER_PATH):
        return f"API contract file not found: {SWAGGER_PATH}"
    try:
        with open(SWAGGER_PATH, encoding="utf-8") as fh:
            on_disk = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return f"API contract file {SWAGGER_PATH} could not be read ({exc})"
    if on_disk != app.openapi():
        return f"API contract file {SWAGGER_PATH} is out of date with the running app"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems: list[str] = []

    missing = _missing_databases()
    if missing:
        problems.append(
            "Missing database file(s):\n"
            + "\n".join(f"  - {label}: {path}" for label, path in missing)
        )

    contract_problem = _contract_problem()
    if contract_problem:
        problems.append(contract_problem)

    if problems:
        raise RuntimeError(
            "The API cannot start:\n\n"
            + "\n\n".join(problems)
            + "\n\nFix, then restart:\n"
            "    task db                        # build verbs.db + dictionary.db\n"
            "    task swagger                   # regenerate swagger.json from the app\n"
            "    # or individually:\n"
            "    python3 build_db.py            # conjugation database (verbs.db)\n"
            "    python3 build_dictionary_db.py # full Italian dictionary (dictionary.db)\n"
            "    python3 dump_openapi.py        # API contract (swagger.json)\n"
            "\nThe first dictionary build downloads the Kaikki Italian dump (~761 MB)\n"
            "if it is not already present at data/kaikki.org-dictionary-Italian.jsonl."
        )
    yield


app = FastAPI(title="WR Italian Conjugation API", version="0.4.0", lifespan=lifespan)


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["meta"],
    summary="Liveness and database status",
)
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        databases={
            label: os.path.exists(path) for label, path in REQUIRED_DATABASES.items()
        },
    )


def _csv_to_list(s: str | None) -> list[str] | None:
    if not s:
        return None
    return _split_csv_preserving_phrases(s)


@app.get(
    "/conjugate",
    response_model=APIResponse,
    tags=["conjugations"],
    summary="Conjugate an Italian verb",
    responses={401: {"description": "Invalid or missing X-API-Key"}},
)
def conjugate(
    v: str = Query(..., min_length=1, description="Italian verb (infinitive)"),
    full: bool = Query(
        True, description="If true, return full JSON and ignore filters"
    ),
    moods: str | None = Query(
        None,
        description="CSV moods (Literal): indicativo,tempi composti,congiuntivo,condizionale,imperativo",
    ),
    tenses: str | None = Query(None, description="CSV tenses (Literal)"),
    persons: str | None = Query(None, description="CSV persons (Literal)"),
    api_key: str | None = Security(api_key_header),
):
    """Return the conjugation table for a verb (infinitives and inflected forms).

    Accepts infinitives (``mangiare``) and inflected/pronominal forms
    (``mi arrabbio``), which are resolved back to their infinitive.
    """
    if not API_KEY or api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    """
    Query params are CSV, but validated against Literal sets via a Pydantic model.
    If an invalid value is provided, we return HTTP 400 with a clear message.
    """
    try:
        req = ConjugateQuery(
            v=v,
            full=full,
            moods=_csv_to_list(moods),
            tenses=_csv_to_list(tenses),
            persons=_csv_to_list(persons),
        )
    except ValidationError as ve:
        return JSONResponse(
            status_code=400,
            content=APIResponse(
                success=False, error=ve.errors()[0]["msg"]
            ).model_dump(),
        )

    try:
        data = get_conjugations(req.v)
        if not data or not data.get("conjugations"):
            return APIResponse(
                success=False, error="Verb not found in offline database", requested=req
            )

        filtered = apply_filters(data, req.moods, req.tenses, req.persons, req.full)

        if not filtered.get("conjugations"):
            return APIResponse(
                success=True,
                note="Lookup OK, but filters returned no items.",
                requested=req,
                data=ConjugationResponse(**filtered),
            )

        return APIResponse(
            success=True, requested=req, data=ConjugationResponse(**filtered)
        )

    except Exception as e:
        return APIResponse(success=False, error=str(e), requested=req)


@app.get(
    "/define",
    response_model=DefineResponse,
    tags=["dictionary"],
    summary="Look up an Italian word in the dictionary",
    responses={401: {"description": "Invalid or missing X-API-Key"}},
)
def define(
    v: str = Query(..., min_length=1, description="Italian word to look up"),
    api_key: str | None = Security(api_key_header),
):
    """Full offline dictionary lookup: senses/glosses, tags, IPA, etymology and inflections.

    Returns every entry for the word across all parts of speech. An unknown word
    yields HTTP 200 with ``success: false``.
    """
    if not API_KEY or api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")

    query = v.strip()
    if not query:
        return JSONResponse(
            status_code=400,
            content=DefineResponse(
                success=False, error="Parameter 'v' must not be empty."
            ).model_dump(),
        )

    try:
        data = get_definitions(query)
        if not data or not data.get("entries"):
            return DefineResponse(
                success=False,
                error="Word not found in offline dictionary",
                requested=query,
            )

        return DefineResponse(
            success=True, requested=query, data=DefinitionResponse(**data)
        )

    except Exception as e:
        return DefineResponse(success=False, error=str(e), requested=query)
