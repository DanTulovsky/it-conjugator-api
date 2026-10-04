# Prefix autocomplete for the dictionary (`/complete`) — design

Status: approved, ready for an implementation plan.
Date: 2026-10-04.

## Problem

Clients of this API currently have no way to discover words. A conjugation UI
wants to complete a verb the user is part-way through typing (`mangi` →
`mangiare`) and then call `/conjugate`; a dictionary UI wants the same for
`/define`. Both need it fast enough to run on every keystroke.

The data to answer this is already on disk: `data/dictionary.db` holds 622,957
entries over 588,731 distinct words and 23 part-of-speech (POS) values. It is
read-only, so an in-memory index built once at startup is the natural fit.

Requirements:

- partial word in, list of completions out, fast enough for per-keystroke calls;
- restrictible to specific parts of speech (a conjugation client sends verbs);
- accent- and case-insensitive, so `citta` finds `città`;
- prefix matching by default, with opt-in substring matching.

Explicitly out of scope: ranking by frequency, lemma-first ordering for
non-verbs, typo tolerance, and an "infinitives only" filter. Verb **canonical-first**
ordering was added later, on request, after measuring that the pure-alphabetical
order put `mangiare` 37th for the prefix `mang`.

## Endpoint

```
GET /complete?q=man&pos=verb,noun&limit=20&substring=false
X-API-Key: <key>
```

| Param | Type | Default | Notes |
|---|---|---|---|
| `q` | string | — | required, non-blank; the partial word |
| `pos` | CSV | all POS values | restrict to one or more parts of speech |
| `limit` | int | `20` | clamped to 1–100 |
| `substring` | bool | `false` | opt in to matching anywhere in the word |

Auth matches `/conjugate` and `/define`: the `X-API-Key` header must equal
`SCRAPER_API_KEY`, otherwise 401.

Response, using the existing envelope so every endpoint has one shape:

```json
{
  "success": true,
  "requested": {"q": "man", "pos": ["verb"], "limit": 20, "substring": false},
  "data": {
    "queried": "man",
    "pos": ["verb"],
    "matches": ["manca", "mancai", "mancammo", "mancan", "mancando"]
  }
}
```

`data.pos` echoes the filter that was actually applied: the requested values,
or `null` when the request omitted `pos` (meaning "all"). `data.queried` echoes
the raw `q` as received, not the folded key, matching how `/define` echoes
`queried`.

An unmatched prefix is a normal result, not an error: HTTP 200 with
`success: true`, `matches: []`, and a `note`. That follows `/conjugate`'s
"filters returned no items" behaviour rather than `/define`'s
`success: false`, because an empty completion list is a legitimate answer to a
legitimate question.

400 only for a genuinely malformed request: blank `q`, or a `pos` value that is
not in the index. Neither error trusts the database's presence.

### Validation of `pos`

`pos` is validated at runtime against the POS keys actually present in the
loaded index, and the 400 message lists them. It is deliberately **not** a
Pydantic `Literal` like the `Mood`/`Tense` sets in `app/models.py`: those
describe a fixed vocabulary this service defines, whereas the POS set is
derived from the Wiktionary dump and can gain values when
`task db:dictionary` is re-run. A hardcoded literal list would silently start
rejecting valid words. The OpenAPI description documents the values as a plain
string for the same reason.

## Index

New module `app/autocomplete_core.py`. Two dicts, built once at startup:

- `buckets: dict[str, tuple[list[str], list[str]]]` — POS → a pair of sorted,
  deduped *folded key* lists: the bucket's **canonical** keys first, then the
  rest. The search surface is 603,520 keys across 23 buckets (`verb` alone is
  384,652, of which 10,653 are canonical).
- `overrides: dict[str, dict[str, str]]` — POS → `{folded key: real spelling}`
  for the 45,261 entries whose spelling differs from their key, so results show
  `città` and `Manco` rather than `citta` and `manco`. Keyed per POS, so the key
  `manco` resolves to `mancò` in the `verb` bucket and `Manco` in the `name`
  bucket.

A key is `word.lower().translate(FOLD)`, where `FOLD` maps each accented Latin
vowel to its base letter. `app/db_core.py` already has `VOWELS_MAP` and
`clean_accents`, but `clean_accents` deliberately *preserves* word-final accents
(it exists to normalise scraped conjugation tables), so it cannot be reused
here — a word-final accent is exactly what `città` → `citta` must strip.

### Canonical-first ordering

Each bucket is split so a query can offer a word's canonical form before its
inflected forms. For verbs the canonical form is **the infinitive**: a key that
is `is_lemma` *and* ends in `-are`/`-ere`/`-ire`/`-rre`. That is what puts
`mangiare` 3rd for the prefix `mang`, instead of 37th behind `mangiai`,
`mangiammo` and the rest.

`is_lemma` alone is **not** sufficient, which measurement revealed: Wiktionary
gives pronominal and clitic verbs their own entries and marks them as lemmas, so
the flag covers 48,432 verb keys — only about a fifth of which are infinitives
(`abbacchiarsi`, `mangiarla`, `abbacchiandoci` are all "lemmas"). Requiring the
infinitive ending is what actually surfaces the verb a client can pass to
`/conjugate`.

Only the **verb** bucket is split. Every other POS keeps plain alphabetical
order, because only verbs have a canonical form a client must be able to reach.
The two halves are kept disjoint (a key that is both a lemma and a form lands in
the canonical half) so `count()` still reports distinct `(word, pos)` keys.

There is deliberately no separate "all words" bucket. A request without `pos`
k-way merges the 23 buckets with `heapq.merge` and dedups while filling the
result. This avoids a ~40 MB duplicate list and one extra build pass, and only
the first `limit` results are ever materialised.

## Build

Built eagerly in the FastAPI `lifespan`, after the existing startup checks pass:

1. Stream `SELECT word, pos, is_lemma FROM entries` — iterating the cursor,
   never `fetchall()`, so 623k rows are never all resident at once.
2. Fold, bucket into the canonical/other halves, and collect overrides in one
   pass.
3. `sorted(set(...))` each half, subtracting the canonical half from the other.

Measured against the real `data/dictionary.db`:

| Step | Time |
|---|---|
| scan 622,957 rows | 0.17 s |
| fold + bucket | 0.43 s |
| sort + dedup | 0.03 s |
| **startup cost** | **~0.76 s**, ~100 MB RSS |

Because the index is derived from `dictionary.db` on every boot it cannot go
stale. Consequently there is **no** prebuilt index artifact: no build script, no
`Taskfile.yml` task, no `Dockerfile` copy, and no fingerprint/staleness check
like the one `_contract_problem()` needs for `swagger.json`. A missing
`dictionary.db` is already fatal to startup.

A covering `(word, pos)` index on `entries` would make the scan index-only. It
was measured at 0.04 s saved for +15 MB of database, so it is **not** added.

If the index fails to build, startup fails with the same
problemlist-and-RuntimeError pattern the existing `lifespan` uses.

## Query

Single entry point:

```python
def complete(q: str, pos: list[str] | None, limit: int, substring: bool) -> list[str]
```

- **Prefix (default).** For each selected bucket, `bisect_left` for the folded
  query, then walk forward while `key.startswith(query)`. With one `pos` this is
  the whole job — measured **10–20 µs**. With no `pos`, take each bucket's
  matching slice and `heapq.merge` them in sorted order, skipping keys already
  seen, until `limit` words are emitted — measured **13 µs**.
- **Substring (`substring=true`).** Linear scan of the selected buckets with
  early exit at `limit`. Measured **0.06 ms** for a common fragment, **10.5 ms**
  worst case when nothing matches anywhere. This is the documented slow path;
  it is opt-in for that reason.

Both paths run **twice**: once over the canonical halves of the selected
buckets, then over the rest, stopping as soon as `limit` is reached. The two
passes share one dedup set and one result list, since a word can be canonical
for one POS and inflected for another. A query that fills `limit` from the
canonical halves never runs the second pass, so the hot path is unchanged; a
narrow prefix such as `mangiare` runs both and still measures **~2 µs**.

Both paths resolve each emitted key through that bucket's `overrides` entry,
falling back to the key itself, and each resolved word consumes one unit of
`limit`.

Measured end-to-end, the hot path — `q=mangi&pos=verb`, what a conjugation
client sends on every keystroke — is ~5 µs.

## Edge cases

| Case | Behaviour |
|---|---|
| `q` blank or whitespace | 400, before the index is consulted |
| `pos` contains an unknown value | 400 listing the allowed values |
| `limit` = 0, negative, or > 100 | clamped to 1–100, not an error |
| `Cane` / `CANE` / `cane` | all fold to `cane`; one result set |
| `citta` | matches `città` and `città santa` |
| `pero` | matches `però` (the dump has no unaccented `pero`); overrides carry the accent |
| key `manco` with `pos=verb` vs `pos=name` | `mancò` vs `Manco` |
| `mang` with `pos=verb` | the infinitives lead (`manganare`, `manganellare`, `mangiare`, …), then the inflected forms; `mangiare` is 3rd of 20 rather than 37th |
| multiword entries (`man mano`, `città santa`) and proper names (`Manacorda`) | in the index by default; a space sorts before letters, so phrases cluster first. Narrowed out with `pos`. |
| missing `dictionary.db` | startup already fails; `/complete` is never reachable |

The default `pos` covers all 23 values, proper names and multiword phrases
included, mirroring how `/define` returns every entry for a word. Clients that
want a clean dropdown pass `pos=noun,verb,adj`.

## Wiring

- `app/models.py` — `CompleteQuery`, `CompletionData`, `CompleteResponse`.
- `app/api.py` — the `/complete` route, and `autocomplete_core.build_index()`
  in `lifespan`.
- `app/autocomplete_core.py` — the index and query.
- `/health` — extend so the loaded index is observable: POS count, key count,
  and build seconds. Cheap, and the only way to confirm from outside that the
  index actually built.
- `swagger.json` — **must** be regenerated with `task swagger`, and this is a
  hard requirement, not a nicety: `_contract_problem()` in `lifespan` compares
  the checked-in file against `app.openapi()` and raises `RuntimeError` when
  they differ, so the API will not start at all until the contract is
  regenerated. This is the repo's only OpenAPI artifact — it is JSON, produced
  by `dump_openapi.py`, and read from `config.SWAGGER_PATH`. There is no YAML
  copy and none is added: a second serialisation of the same document would be
  a second thing to keep in sync, and the startup check only understands the
  JSON one.

  Acceptance: `python3 dump_openapi.py` leaves `swagger.json` unmodified
  (regenerating a correct contract is a no-op) and the app boots.
- `README.md` — a row in the endpoints table and a `/complete` section in the
  style of the `/define` and `/conjugate` sections.

## Testing

`tests/test_autocomplete.py`, stdlib `unittest`, in the style of
`tests/test_conjugations.py` and `tests/test_dictionary.py`. Needs a built
`dictionary.db` (`task db`).

Endpoint behaviour is covered twice on purpose: once by calling the endpoint
function directly (the existing repo style, which gives precise access to the
response model) and once through `fastapi.testclient.TestClient`, which is the
only way to pin the status codes and JSON bodies a client actually receives.
`TestClient` requires `httpx`, which is therefore added to `requirements.txt` as
the project's one test-only dependency; `app/` never imports it.

1. **Fold** — `città` → `citta`, `però` → `pero`, `CANE` → `cane`; idempotent.
2. **Prefix, one POS** — `man` + `pos=verb` returns only verbs, respecting
   `limit`; canonical forms precede inflected ones, each group alphabetical.
3. **Prefix, no POS** — results are sorted, deduped across buckets, and
   truncated to `limit`.
4. **POS filter** — the same query with `pos=noun` returns only nouns; a word
   that is both a noun and a verb is reachable under either.
5. **Accents** — `citta` returns `città`; the returned spelling carries the accent.
6. **Spelling collisions** — the key `manco` resolves to `mancò` under
   `pos=verb` and `Manco` under `pos=name`.
7. **Substring** — `substring=false` gives prefix-only; `substring=true` finds
   an interior fragment; `limit` still holds.
8. **Limits** — `limit=0` / `limit=-5` clamp; `limit=500` clamps to 100.
9. **Errors** — blank `q` → 400; unknown `pos` → 400; missing key → 401.
10. **Consistency** — for a sample of queries, every returned word exists in
    `dictionary.db` for the requested POS. This is the check that catches an
    index built from a stale or wrong source.
11. **Canonical-first ordering** — on a hand-built index, canonical forms lead
    inflected ones within a POS, across a multi-POS merge, and on the substring
    path; `limit` can be filled entirely by canonical forms; and nothing is
    dropped. Backed by the `_is_canonical` rule, including the pronominal verbs
    (`abbacchiarsi`, `mangiarla`) that made `is_lemma` unusable. On the real
    database, `mang` + `pos=verb` puts `mangiare` ahead of `manganai`/`mangana`,
    which sort before it alphabetically.
11. **OpenAPI contract** — `swagger.json` parses, contains the `/complete`
    path with its four parameters, and equals `app.openapi()` exactly. This
    duplicates the startup guard as a test so a forgotten `task swagger`
    fails the suite instead of only failing at boot.

No performance assertions: timing tests flake on CI. The timings above are
recorded here as the design's justification, and re-measured by hand if the
index or query changes.

## Deliberate simplifications

Each with its ceiling and the upgrade path if it is ever hit:

- **Ranking is canonical-first only.** Verb infinitives lead their bucket, then
  everything alphabetical. There is no frequency data, no per-word popularity,
  and no lemma-first ordering for other parts of speech. This was revised after
  measurement showed the original "no ranking" plan left `mangiare` 37th for the
  prefix `mang`, and that `is_lemma`-based ordering would not have fixed it.
- **No substring index.** A linear scan (10.5 ms worst case) stands in for a
  trigram or suffix-array index. Build one if substring becomes the common path.
- **No prebuilt index file.** ~0.76 s of startup is cheaper than an artifact to
  generate, ship, and invalidate.
- **No infinitive-only filter.** Canonical-first ordering surfaces infinitives
  without hiding anything. A `lemma=true` filter would shrink the verb bucket
  from 384,652 keys to 10,653 (and the whole index from 603,520 to 229,522,
  roughly halving startup memory) — worth adding if a client wants to browse
  only infinitives rather than search for one.
