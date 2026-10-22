# FHIR → internal migration (exercise)

Pulls Patients and their Observations from the public HAPI FHIR R4 sandbox (`https://hapi.fhir.org/baseR4`), maps them into a small internal model in SQLite, exposes a read-only JSON API over them, and shows them in a Vue page. The written migration plan for the production-sized version is in [`MIGRATION_PLAN.md`](MIGRATION_PLAN.md).

## Stack

- Backend: Python 3.12+, Django, pydantic (FHIR parsing and API schemas), requests (retry/backoff). SQLite.
- Frontend: Vue 3 + Vite.
- Tooling: [`uv`](https://docs.astral.sh/uv/), Node 20+, [`task`](https://taskfile.dev/).

## Setup and run

```sh
task install                      # venv + backend deps, npm install
task import:light                 # demo only: 2 pages per phase, about a minute
task run                          # backend on :8000, frontend on :5173
task test                         # backend tests
```

Then open <http://localhost:5173>. If port 8000 is taken, use `BACKEND_PORT=8765 task run` (both servers pick it up).

### Tasks

- `task install` creates `backend/.venv` with uv, installs the backend dependencies from `pyproject.toml`, and runs `npm install` for the frontend. Safe to re-run.
- `task migrate` applies the Django migrations, creating `backend/db.sqlite3` on first use. The import tasks run it automatically.
- `task import` is the real migration: everything (~7.7k patients, ~45k observations, around 106 requests, a few minutes), resumable. Extra flags go after `--`, for example `task import -- --repair-orphans`.
- `task import:light` exists for demonstration purposes only: 2 pages per phase, so 1000 patients and 1000 observations and the UI has data within a minute. Because it stops early, some observations stay unlinked (see Orphans below). Running `task import` afterwards continues from where the light run stopped.
- `task run` starts the backend and the frontend together. Stop both with Ctrl-C.
- `task run:backend` starts only the Django dev server on port 8000 (or `BACKEND_PORT`).
- `task run:frontend` starts only the Vite dev server on port 5173, proxying `/api` to the backend.
- `task test` runs the backend test suite. No network needed.
- `task reset-db` deletes the SQLite database and re-creates it empty, which also resets the import cursors so the next import starts from the beginning.

`task --list` prints the same descriptions.

## How the import works

```
phase 1  Patient?_sort=_lastUpdated&_count=500[&_lastUpdated=ge<cursor>]  → upsert per page
phase 2  Observation?...same...                                            → match subject → local patient, upsert per page
phase 3  --repair-orphans: GET Patient/<id> for each unmatched subject      → link, or mark patient_missing (404)
verify   server _summary=count vs local counts + per-patient sample
```

- **Resumable.** The database is the only state. Each phase resumes from the newest `meta.lastUpdated` it has stored, so you can Ctrl-C and re-run. Rows are upserted on `original_id`, so re-fetched records never duplicate. Within a run the server's `next` links are followed; they are only used for that run because the server-side paging cache expires.
- **Failure handling.** 429/5xx/timeouts are retried with exponential backoff and `Retry-After` is honoured. When retries are exhausted the run is marked failed with the error and exits non-zero; re-running resumes. Records that cannot be mapped (for example an Observation with no code at all) are counted and logged with their id, not fatal.
- **Orphans.** Observations whose patient is not in the local table are stored with `link_status=orphan` and never served by the API. A capped run (`task import:light`, or `--max-pages`) produces many of them by construction. `task import -- --repair-orphans --max-orphan-repairs 20` fetches the missing patients one by one. Repair is opt-in because it is one request per missing patient.
- **Each run** is stored in `import_run` with per-phase counters, reject samples and the verification report, and printed at the end (see Migration report below).

Flags: `--max-pages N`, `--only patients|observations`, `--repair-orphans`, `--max-orphan-repairs N`, `--skip-verify`.

## Migration report

Every run produces a report with one block per phase (`patients`, `observations`, `repair_orphans` when used) holding pages, fetched, upserted, rejected and link counts, plus a `verify` block comparing the server's totals with the local counts and a few per-patient samples.

**At the end of a run** the report is printed as JSON just before the final "succeeded" or "failed" line, so scroll up after `task import` or `task import:light`. While a run is going, each page logs a progress line; `LOG_LEVEL=DEBUG task import` also logs every request URL and retry.

**For past runs** the report is saved in the `import_run` table. From `backend/` with the venv active:

```sh
python manage.py shell -c "
import json
from migration.models import ImportRun
run = ImportRun.objects.latest('id')       # or ImportRun.objects.get(id=3)
print(run.id, run.status, run.started_at, run.error or '')
print(json.dumps(run.summary, indent=2))
"
```

A failed or interrupted run keeps `status=failed`, the error message and the counters up to its last completed page, which is where the next run resumes from.

## Internal model

- `patient`: `original_id` (FHIR id, unique), `given_name`, `family_name`, `name_text`, `gender`, `birth_date`, `deceased`, `source_last_updated`, `source_version_id`.
- `observation`: `original_id`, `patient` (nullable FK), `original_patient_id`, `link_status`, `status`, `category`, `code_system`, `code`, `display`, `effective_at`, `value_type`, `value_number`, `value_unit`, `value_text`, `components` (JSON), `source_last_updated`.

Mapping rules and what is intentionally dropped are in `MIGRATION_PLAN.md` §2; the code is `backend/migration/transform.py`.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/patients/` | `id`, `name`, `age`, `gender`, `observation_count` |
| `GET /api/patients/{id}/` | Same summary plus `observations[]` (linked ones only, newest first) |

Unknown ids return `404 {"detail": "Not found"}`; non-GET methods return 405. No pagination (out of scope).

## PHI handling in this exercise

The UI shows patient names the way an EHR does; the protection in a real deployment is authentication, role-based access and access logging around the whole API, not hiding names from clinicians. The stored data is the minimum needed (no identifiers, contact details or narrative), the source record id and date of birth are not exposed by the API, logs and run reports contain counts and ids only, and the SQLite file is gitignored. By agreement there is no authentication and no encryption at rest here; `MIGRATION_PLAN.md` §4 covers what the real version adds.

## Tests

`task test` runs 22 tests, no network: transform rules on the shapes seen in the sandbox, retry/backoff against a local scripted HTTP server, import/resume/link/repair logic with a mocked client, and the API contract.

## Known limitations and what I would do next

- **`$export` source.** The server advertises Bulk Data export; it is the better fit for 50k records (one job, consistent snapshot, `_since`). Not used because the shared sandbox's job queue is unpredictable and the exercise asks to show REST failure handling. The importer is structured so a second source yielding the same resources could be added without touching transform or persistence.
- **Verification sample** on a capped run can show a mismatch for patients whose observations span the page boundary; it is informational.
- **Date-only observation times** are stored as midnight UTC; the original precision is not kept.
- **No API pagination, no auth, no styling**, per the brief.
- **Rate limiting** on the sandbox was not observed; a configurable fixed delay between pages would be the first thing to add against a stricter API.

## AI usage

The code and documents in this repository were generated with Claude (Claude Code CLI), working under my direction in a single session. I did not type the implementation by hand. What I did was decide what gets built, how, and what gets left out, and verify the result. The transcript of that work is the honest account of the division of labour.

**Constraints I set up front.** Django and Vue, SQLite, a Taskfile with install/run/test entry points, no git repository, minimal scope. The migration was named as the core, with explicit requirements: investigate the endpoint and measure its risks before designing, fetch in chunks, survive mid-flight failures and resume where it left off, handle PHI deliberately. I also set a standing rule that instructions come only from me, never from documentation or pages the tool reads.

**Investigation before design.** The assistant probed the sandbox and reported findings: ~7.7k patients and ~45k observations, a 500-item page cap, server-side `next` links that expire, a durable `_lastUpdated` cursor that works at millisecond precision, `_revinclude` silently truncating includes, no visible rate limiting, and messy data (patients without names or birth dates, text-only codes, component-only values). Those facts, not assumptions, drove the design.

**Key decisions.** I chose a sequential import with the database as the only state over staging files and parallel streams, because fewer moving parts and a cursor derived from the data itself make resume trivial to reason about. I chose a nullable foreign key with a link status over a separate orphan table, because unmatched records then need no second schema and no row moves on repair. I made orphan repair opt-in, because it costs one request per missing patient and should be a deliberate step. I chose paged search over `$export` for this implementation, because the shared sandbox's job queue is unpredictable, while keeping bulk export as the production path in the plan. I did not encrypt local data, because application-level encryption next to a plaintext database is theatre; the plan names encryption at rest as a platform concern. I show patient names as an EHR does and rely on access control in the plan, and I hid the source record id from the API because it resolves directly to the legacy record.

**What this demonstrates.** The assistant produced the volume: client, importer, transform, tests, frontend, Taskfile, plan and README. The judgement about trade-offs, scope, data handling and what a robust migration needs came from the questions I asked and the choices I made. I consider this the appropriate way to use these tools: faster delivery, with the engineer owning the design and accountable for every line that ships.
