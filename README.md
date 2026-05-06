# PerspectivesOA

#### I startedby clicking around the simplepractice sandbox ui with devtools/cdp Network open, captured the HAR, then used it to identify the real /frontend/* json:api calls for demographics, overview timeline, appointments, intake notes, diagnoses, and measures. 
You kind of leaked some of the sauce during our coffee chat (find the underlying xhr/json calls the UI fires when elements are pressed). Then I built a fastapi extraction layer that turns those responses into formatted patient json, then added deterministic ASAM 4th-edition and tjc cts audit endpoints with cited evidence from the notes. I was thinking about using an llm to help with the diagnoses but decided on having a stricter framework as you said the language has to be precise for the payors to approve/not deny claims to increase reliability. Finally, hardened the implementation by validating fixtures, removing stale caches, fetching overview pages, adding regression tests, updating docs, and regenerating the sample json responses for the submission. 

#### the json files can be found at PerspectivesOA/samples, or attached in the email I am about to send to you.
---

A FastAPI service that reverse-engineers SimplePractice's internal `/frontend/*`
JSON:API to extract a complete patient chart, then runs **deterministic** ASAM
4th edition Level-of-Care and Joint Commission CTS audit engines over the
extracted text.

Built against Jamie D. Appleseed's chart (hashed id `0c39dadff6972e0f`) which
ships with the repo as a HAR-derived fixture, so every endpoint and every test
runs offline by default.

---

## What's in here

```
app/
  api/                # FastAPI routers (extraction + intelligence)
  domain/             # Pydantic domain models + build_patient_extract
  simplepractice/     # JSON:API client, IncludedIndex, fixture backend
  intelligence/
    asam/             # rules.yaml + 6-dimension engine + LoC matrix
    tjc/              # rules.yaml + curated CTS EP audit engine
  db/                 # SQLAlchemy models + async session (cache only)
fixtures/             # HAR-derived JSON pinned to Jamie's chart
scripts/parse_har.py  # walks a HAR, materializes fixtures/*.json
samples/              # regenerated sample Task 2 / Task 3 JSON outputs
docs/
  ENDPOINTS.md        # reverse-engineered SP endpoint catalog
  ASAM_RULES.md       # rule-by-rule rationale + 4th-edition citations
  TJC_RULES.md        # CTS EPs we audit + sources
```

---

## Quickstart

```bash
# 1. Install deps (Python 3.12, uv)
make install

# 2. Run the API in fixture/offline mode (no SP cookie required)
make dev
# → http://localhost:8000/api/v1/patients/0c39dadff6972e0f/extract

# 3. Run tests
make test
```

To talk to the live SimplePractice API instead of fixtures, copy `.env.example`
to `.env`, paste a `_simple_practice_session` cookie value, and unset
`SP_FORCE_FIXTURES`.

---

## Endpoints

### Task 2 — Data Extraction

| Method | Path                                                | Description                                                                             |
| ------ | --------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `GET`  | `/api/v1/patients/{hashed_id}/extract`              | The canonical extract: demographics, BPS admission assessment, full timeline, diagnoses |
| `GET`  | `/api/v1/patients/{hashed_id}/demographics`         | Just the patient profile (name, dob, contacts, measured scores)                         |
| `GET`  | `/api/v1/patients/{hashed_id}/admission-assessment` | The Biopsychosocial intake note as parsed sections                                      |
| `GET`  | `/api/v1/patients/{hashed_id}/timeline`             | Newest-first timeline of appointments + notes                                           |
| `GET`  | `/api/v1/healthz`                                   | Liveness probe                                                                          |

### Task 3 — Clinical Intelligence

| Method | Path                                     | Description                                                                 |
| ------ | ---------------------------------------- | --------------------------------------------------------------------------- |
| `POST` | `/api/v1/patients/{hashed_id}/asam`      | Six-dimension ASAM 4e scoring + recommended Level of Care, with cited spans |
| `POST` | `/api/v1/patients/{hashed_id}/tjc-audit` | Pass/fail/insufficient-data verdicts for a curated set of TJC CTS EPs       |

See [docs/ENDPOINTS.md](docs/ENDPOINTS.md) for full request/response shapes
(including the upstream SimplePractice endpoints we reverse-engineered).

`/extract` is cacheable by default and supports `?refresh=true`. The clinical
intelligence endpoints default to fresh extraction (`refresh=true`) so ASAM and
TJC verdicts are based on the latest available chart text; pass `refresh=false`
only when you intentionally want to reuse the extraction cache.

---

## Architecture

```
Caller → FastAPI router
          ├── ExtractionService  → SimplePracticeBackend (httpx) ─┐
          │                       └── FixtureBackend (HAR)        ├→ Document / Resource (JSON:API)
          │                                                       │   IncludedIndex (O(1) (type,id))
          │                                                       ▼
          │                                              build_patient_extract()
          │                                                       ▼
          │                                              PatientExtract (Pydantic)
          ├── AsamEngine        — rules.yaml → six dimension scores → LoC matrix
          └── TjcEngine         — rules.yaml → CTS EP verdicts with citation spans

PostgreSQL is used as a best-effort cache for /extract and audit history; Task
3 endpoints refresh extraction by default. The service stays correct when the
DB is offline.
```

Key design choices (per the assessment's "rule-based reasoning, no LLM"
constraint):

- The ASAM engine and TJC engine are **deterministic regex+predicate
  pipelines**. All thresholds, phrase lists, and the LoC decision matrix
  live in YAML so policy changes are diff-reviewable.
- Repeated mentions of the same finding across multiple notes count as
  evidence (more citations) but not as additional severity, so we don't
  over-score from clinically-redundant text.
- Negation is detected with a backward-looking regex: `no evidence of acute
  psychosis` does not raise the Dimension 3 rating.

---

## Authentication model

SimplePractice's `/frontend/*` endpoints expect a logged-in browser session:

```http
Cookie: _simple_practice_session=<from a logged-in browser>
X-CSRF-Token: <parsed from /clients/{hashed_id}/overview HTML>
Accept: application/vnd.api+json
api-version: 2025-03-21
User-Agent: <modern Chrome/Safari UA>
Referer: https://secure.simplepractice.com/clients/{hashed_id}/overview
```

`SimplePracticeClient` lazy-fetches the CSRF token from the overview HTML on
the first request and refreshes it on a 401/419. Timeline extraction follows
all `/frontend/overview-items` pages and merges JSON:API resources by
`(type, id)` so older notes are not silently dropped. Cookies in HAR files are
stripped by Chrome by default, so for live mode the user pastes the cookie
value into `.env`.

---

## Offline / fixture mode

Every test in `tests/` (and `make dev` by default) talks to `FixtureBackend`,
which reads the JSON files in `fixtures/` exactly as they came from
SimplePractice. Fixture mode validates both the requested hashed id and numeric
client id so a missing fixture cannot be mislabeled as Jamie's chart. The
fixtures were materialized by running:

```bash
make parse-har HAR=/path/to/secure.simplepractice.com.har
```

`scripts/parse_har.py` walks the HAR's `log.entries`, persists each
`/frontend/*` JSON response verbatim, and synthesizes any individual
appointment fixtures that the timeline references but the HAR omits.

---

## Database

`docker compose up -d postgres` brings up `postgres:16-alpine` on port 5432
with the credentials in `.env.example`. Running `make migrate` then `make seed`
creates the schema and pre-warms the extraction cache against the fixture.

Tables (single Alembic migration, see `alembic/versions/`):

| Table         | Purpose                                               |
| ------------- | ----------------------------------------------------- |
| `extractions` | Latest `PatientExtract` payload per hashed id (jsonb) |
| `asam_audits` | History of every ASAM run for trend analysis          |
| `tjc_audits`  | History of every TJC audit run                        |

Cache reads and all writes are best-effort — a missing/dead Postgres never
breaks the API.

---

## Sample outputs

Fresh sample responses for the simulated patient live in `samples/`:

- `patient_extract.json` - Task 2 structured extract.
- `asam_assessment.json` - Task 3 ASAM 4th-edition Level of Care output.
- `tjc_audit.json` - Task 3 Joint Commission CTS audit output.

Regenerate them after extraction or rule changes with:

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path
from app.domain.extraction import build_patient_extract
from app.intelligence.asam import AsamEngine
from app.intelligence.tjc import TjcEngine
from app.simplepractice import FixtureBackend

async def main():
    out = Path("samples")
    out.mkdir(exist_ok=True)
    extract = await build_patient_extract(FixtureBackend("fixtures"), "0c39dadff6972e0f")
    (out / "patient_extract.json").write_text(extract.model_dump_json(indent=2))
    (out / "asam_assessment.json").write_text(AsamEngine().assess(extract).model_dump_json(indent=2))
    (out / "tjc_audit.json").write_text(TjcEngine().audit(extract).model_dump_json(indent=2))

asyncio.run(main())
PY
```

---

## Testing

```bash
make test       # all tests run offline against fixtures
make lint       # ruff check + format check
make typecheck  # mypy strict
```

Test files:

- `tests/test_jsonapi_parser.py` — IncludedIndex resolution + ref traversal
- `tests/test_extraction.py` — `build_patient_extract` returns the expected
  Jamie chart (demographics, contacts, diagnoses, BPS structure, three
  classified progress notes, timeline ordering, cache behavior, and the API
  endpoints).
- `tests/test_asam_jamie.py` — ASAM produces (2, 0, 3, 2, 2, 1) and
  recommends 2.1 IOP — matching the SOAP note's own clinical disposition.
- `tests/test_tjc_jamie.py` — CTS.02.02.01 EP2 fails on missing spiritual
  assessment, CTS.03.01.03 EP2 fails on structured `goal: null`, the rest pass.

---

## Out of scope

- No write/PATCH/POST against SimplePractice — read-only client.
- No LLM calls — purely deterministic per the assessment's clinical-safety
  preference.
- No UI (the assessment said "nice but not required").

---

## License

MIT.
