# Migration plan: FHIR R4 Patients and Observations → internal service

Scope: ~50,000 patients and their observations, read from a legacy clinical system's FHIR R4 API, written into a new internal service. This document is the plan; `README.md` describes the small implementation built against the public HAPI sandbox, which follows the same design where the sandbox allows it.

## 1. Overall approach

```
 legacy FHIR API                      migration worker                         internal DB
 ┌──────────────┐   pages of 500    ┌─────────────────────────┐   per-page txn   ┌───────────────┐
 │ Patient      │ ───────────────▶  │ fetch → validate → map  │ ───────────────▶ │ patient       │
 │ Observation  │ ───────────────▶  │ (pydantic)  → link      │ ───────────────▶ │ observation   │
 └──────────────┘   retry/backoff   │ cursor = max(lastUpdated)│                 │ import_run    │
                                    └─────────────────────────┘                  └───────────────┘
            phase 1 patients ──▶ phase 2 observations ──▶ phase 3 orphan repair (opt-in) ──▶ verify ──▶ cutover
```

**Extraction.** Prefer FHIR Bulk Data (`$export` with `_type=Patient,Observation`) when the legacy server supports it: one asynchronous job, NDJSON files, a consistent snapshot, and `_since` for incremental re-runs. When it is not available or not reliable, fall back to paged search: `GET /Patient?_sort=_lastUpdated&_count=500` and the same for `Observation`, following the bundle's `next` link within a run. The implementation uses the fallback because the public sandbox's export queue is shared and unpredictable.

**Reliability.** The target database is the only state. Each phase resumes from the newest `meta.lastUpdated` it has already stored (`_lastUpdated=ge<cursor>`), so an interrupted run is re-run, never restarted. Every page is validated, mapped and upserted on `original_id` inside one transaction, so partial pages never land and re-fetching the same records is harmless. Observations are matched to patients in memory against the `original_id → pk` map; unmatched ones are stored unlinked with a `link_status`, never dropped, and repaired explicitly in a later pass. Records that cannot be mapped are counted and logged with their ids, and the run continues.

```
def import_phase(resource_type, transform, upsert):
    cursor = max(source_last_updated) from target table        # None on first run
    for page in client.iter_pages(resource_type, since=cursor): # 500 per page, follows `next`
        records, rejects = validate_and_map(page, transform)    # pydantic, tolerant of missing fields
        with transaction():
            upsert(records)                                     # ON CONFLICT(original_id) DO UPDATE
        record_progress(run, page, len(records), rejects)
```

**API limits and politeness.** Requests are sequential per phase with exponential backoff on 429/5xx/timeouts. A run of 50k patients and ~300k observations is roughly 700 pages; at a few seconds each this is a one-to-two hour batch, which is fine for a one-off migration and can be scheduled off-peak. If the legacy API enforces a rate limit, the page loop takes a fixed delay from configuration rather than retrying into the limit.

**Observability.** Each run is a row in `import_run` with phase, page counters, reject samples, link statistics, the verification report and any error. Logs carry counts and resource ids only, never names or dates of birth. In production the same counters feed metrics (pages/s, rejects, orphans, retries) and an alert on a failed run.

## 2. Data mapping

The internal model keeps what the new service needs to list patients and show their results, and drops the rest of the FHIR surface (addresses, telecom, narrative, extensions, encounters, performers). Every row keeps the source id so any record can be traced back or re-fetched.

| Internal `patient` | FHIR `Patient` | Rule |
|---|---|---|
| `original_id` | `id` | Unique; upsert key |
| `given_name`, `family_name` | `name[use=official]` else `name[0]` | Given parts joined by space |
| `name_text` | `name[].text` | Only when there is no structured name |
| `gender` | `gender` | As-is; may be null |
| `birth_date` | `birthDate` | Full dates only; `YYYY` / `YYYY-MM` become null |
| `deceased` | `deceasedBoolean` / `deceasedDateTime` | Boolean |
| `source_last_updated`, `source_version_id` | `meta.lastUpdated`, `meta.versionId` | Cursor and provenance |

| Internal `observation` | FHIR `Observation` | Rule |
|---|---|---|
| `original_id` | `id` | Unique; upsert key |
| `original_patient_id`, `patient_id`, `link_status` | `subject.reference` | `Patient/<id>` (relative, absolute or versioned) → id; matched locally. Non-Patient subjects are kept with `no_patient_subject` |
| `status` | `status` | As-is |
| `category` | `category[0].coding[0].code` | e.g. `laboratory`, `vital-signs` |
| `code_system`, `code`, `display` | `code.coding[]` (LOINC preferred) / `code.text` | Reject when neither exists |
| `effective_at` | `effectiveDateTime` / `effectivePeriod.start` / `effectiveInstant` | UTC; date-only values become midnight UTC |
| `value_type`, `value_number`, `value_unit`, `value_text` | `value[x]` | Quantity/integer → number + unit; string/codeable/boolean → text |
| `components` | `component[]` | JSON list of `{code, display, value, unit}` (blood pressure style panels) |

## 3. Validation

- **Counts.** Local row counts versus the server's `_summary=count` totals for both resource types, recorded on the run. Small drift is expected while the legacy system is still live; a large gap fails the run.
- **Per-patient sampling.** For a random sample of linked patients compare the local observation count with `Observation?patient=<id>&_summary=count`. In production the sample is sized for a confidence level, not a fixed five.
- **Referential integrity.** No observation may remain in `orphan` state at cutover; each must be `linked` or deliberately `patient_missing` after repair, and the list of `patient_missing` ids is reviewed.
- **Rejects.** Zero rejects is the target. Every reject carries the source id and the reason so the mapping can be fixed and the phase re-run.
- **Domain checks.** Units present for quantities, dates within plausible ranges, codes resolving to known systems. A spot check of a handful of patients in the UI against the legacy UI by a clinician closes the loop.

## 4. Safety: handling PHI

- **Minimum necessary.** Only the fields the new service needs are stored; identifiers, contact details and narrative are not pulled.
- **Access control, not obscurity.** Clinicians need to see names, so the UI shows them as an EHR does. In production the API sits behind authentication and role-based authorization, every read of patient data is access-logged, and the logs are retained for audit. Fields with no clinical use in the new service (source record id, identifiers, contact details) are not exposed at all.
- **No PHI in logs or run reports.** Only counts and resource ids.
- **In transit.** TLS to the legacy API and to the internal service.
- **At rest.** A platform concern: encrypted volume for the database, encrypted managed database in production, encrypted object storage if staging files are ever used. Application-level file encryption is not used; the key would sit next to the data and add little.
- **Retention.** Any staging artefacts are purged after verification. Local developer copies are gitignored and deleted.
- **Governance.** Access on a need-to-know basis, a BAA or equivalent with any vendor involved, and the migration run itself logged as a data processing activity.
- **Deliberately omitted in the exercise**, by agreement: authentication and authorization, access logging, and encryption at rest.

## 5. Rollback

- **Stop and resume, not restart.** A failed page leaves the database consistent; the next run resumes from the cursor.
- **Snapshot before cutover.** Take a database backup immediately before the final run; rollback is a restore.
- **Run-scoped deletion.** Rows record the run that last wrote them, so a bad run can be deleted by run id without touching earlier data.
- **Legacy stays the source of truth** until verification passes. Nothing in the legacy system is modified; reads are idempotent, so a full re-run is always possible.
- **Incremental catch-up.** After cutover, re-running with the same cursor logic (or `$export?_since=`) pulls only what changed, which also covers records that arrived during the migration.
