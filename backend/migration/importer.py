"""Import phases: patients -> observations -> (optional) orphan repair -> verify.

The database is the only state. Each phase resumes from the newest
meta.lastUpdated it has already stored, and every page is upserted on
original_id inside one transaction, so interrupting and re-running is safe.
"""

import logging
from datetime import datetime

from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .fhir_client import FhirClient
from .models import ImportRun, LinkStatus, Observation, Patient
from .schemas.internal import ObservationRecord, PatientRecord
from .transform import TransformError, transform_observation, transform_patient

logger = logging.getLogger(__name__)

PATIENT_UPDATE_FIELDS = [
    "given_name", "family_name", "name_text", "gender", "birth_date", "deceased",
    "source_last_updated", "source_version_id", "imported_at",
]
OBSERVATION_UPDATE_FIELDS = [
    "patient", "original_patient_id", "link_status", "status", "category", "code_system", "code",
    "display", "effective_at", "value_type", "value_number", "value_unit", "value_text", "components",
    "source_last_updated", "imported_at",
]
REJECT_SAMPLE_SIZE = 50


class ImporterError(Exception):
    pass


def cursor_string(value: datetime | None) -> str | None:
    """DB datetime -> FHIR instant usable in `_lastUpdated=ge...` (ms precision)."""
    return value.isoformat(timespec="milliseconds") if value else None


class Importer:
    def __init__(self, client: FhirClient, run: ImportRun):
        self.client = client
        self.run = run

    # --- bookkeeping ------------------------------------------------------

    def _set_phase(self, phase: str, **stats):
        self.run.phase = phase
        self.run.summary.setdefault(phase, {}).update(stats)
        self.run.save(update_fields=["phase", "summary"])

    def _transform_page(self, resources: list[dict], transform, phase: str):
        records, rejects = {}, []
        for resource in resources:
            try:
                record = transform(resource)
            except TransformError as exc:
                rejects.append({"original_id": resource.get("id"), "error": str(exc)})
                logger.warning("%s: rejected %s/%s: %s", phase, resource.get("resourceType"), resource.get("id"), exc)
                continue
            records[record.original_id] = record  # de-dupe within a page; last wins
        stats = self.run.summary.setdefault(phase, {})
        stats["rejected"] = stats.get("rejected", 0) + len(rejects)
        samples = stats.setdefault("reject_samples", [])
        samples.extend(rejects[: max(0, REJECT_SAMPLE_SIZE - len(samples))])
        return list(records.values()), rejects

    # --- phase 1: patients ------------------------------------------------

    def import_patients(self, max_pages: int | None = None) -> dict:
        phase = "patients"
        since = cursor_string(Patient.objects.aggregate(m=Max("source_last_updated"))["m"])
        logger.info("patients: starting (resume from %s)", since or "beginning")
        self._set_phase(phase, resumed_from=since, pages=0, fetched=0, upserted=0)

        for page_no, resources in enumerate(self.client.iter_pages("Patient", since=since, max_pages=max_pages), 1):
            records, _ = self._transform_page(resources, transform_patient, phase)
            with transaction.atomic():
                upsert_patients(records)
            stats = self.run.summary[phase]
            self._set_phase(phase, pages=page_no, fetched=stats["fetched"] + len(resources), upserted=stats["upserted"] + len(records))
            logger.info("patients page %d: fetched %d, upserted %d, rejected %d", page_no, len(resources), len(records), len(resources) - len(records))
        return self.run.summary[phase]

    # --- phase 2: observations -------------------------------------------

    def import_observations(self, max_pages: int | None = None) -> dict:
        phase = "observations"
        if not Patient.objects.exists():
            raise ImporterError("no patients imported yet; run the patients phase first")
        patient_pks = dict(Patient.objects.values_list("original_id", "pk"))
        since = cursor_string(Observation.objects.aggregate(m=Max("source_last_updated"))["m"])
        logger.info("observations: starting (resume from %s, %d local patients)", since or "beginning", len(patient_pks))
        self._set_phase(phase, resumed_from=since, pages=0, fetched=0, upserted=0, linked=0, orphans=0, no_patient_subject=0)

        for page_no, resources in enumerate(self.client.iter_pages("Observation", since=since, max_pages=max_pages), 1):
            records, _ = self._transform_page(resources, transform_observation, phase)
            with transaction.atomic():
                link_counts = upsert_observations(records, patient_pks)
            stats = self.run.summary[phase]
            self._set_phase(
                phase,
                pages=page_no,
                fetched=stats["fetched"] + len(resources),
                upserted=stats["upserted"] + len(records),
                linked=stats["linked"] + link_counts[LinkStatus.LINKED],
                orphans=stats["orphans"] + link_counts[LinkStatus.ORPHAN],
                no_patient_subject=stats["no_patient_subject"] + link_counts[LinkStatus.NO_PATIENT_SUBJECT],
            )
            logger.info(
                "observations page %d: fetched %d, upserted %d, linked %d, orphans %d, no-patient %d",
                page_no, len(resources), len(records), link_counts[LinkStatus.LINKED],
                link_counts[LinkStatus.ORPHAN], link_counts[LinkStatus.NO_PATIENT_SUBJECT],
            )
        return self.run.summary[phase]

    # --- phase 3 (opt-in): orphan repair ---------------------------------

    def repair_orphans(self, limit: int | None = None) -> dict:
        phase = "repair_orphans"
        orphan_ids = list(
            Observation.objects.filter(link_status=LinkStatus.ORPHAN)
            .values_list("original_patient_id", flat=True)
            .distinct()
            .order_by("original_patient_id")
        )
        if limit is not None:
            orphan_ids = orphan_ids[:limit]
        logger.info("repair: %d distinct missing patients to fetch", len(orphan_ids))
        self._set_phase(phase, attempted=len(orphan_ids), repaired_patients=0, linked=0, patient_missing=0, rejected=0)

        for original_id in orphan_ids:
            data = self.client.get_resource("Patient", original_id)
            stats = self.run.summary[phase]
            if data is None:
                n = Observation.objects.filter(link_status=LinkStatus.ORPHAN, original_patient_id=original_id).update(
                    link_status=LinkStatus.PATIENT_MISSING
                )
                self._set_phase(phase, patient_missing=stats["patient_missing"] + n)
                continue
            try:
                record = transform_patient(data)
            except TransformError as exc:
                logger.warning("repair: rejected Patient/%s: %s", original_id, exc)
                self._set_phase(phase, rejected=stats["rejected"] + 1)
                continue
            with transaction.atomic():
                upsert_patients([record])
                patient = Patient.objects.get(original_id=original_id)
                n = Observation.objects.filter(link_status=LinkStatus.ORPHAN, original_patient_id=original_id).update(
                    patient=patient, link_status=LinkStatus.LINKED
                )
            self._set_phase(phase, repaired_patients=stats["repaired_patients"] + 1, linked=stats["linked"] + n)
        return self.run.summary[phase]

    # --- phase 4: verification -------------------------------------------

    def verify(self, sample_size: int = 5) -> dict:
        """Compare local counts to the server. Reported, not enforced: the source is live."""
        phase = "verify"
        report = {
            "server_patients": self.client.count("Patient"),
            "local_patients": Patient.objects.count(),
            "server_observations": self.client.count("Observation"),
            "local_observations": Observation.objects.count(),
            "local_link_status": dict(
                Observation.objects.values_list("link_status").annotate(n=Count("id")).order_by("link_status")
            ),
            "samples": [],
        }
        sample = (
            Patient.objects.annotate(n=Count("observations", filter=Q(observations__link_status=LinkStatus.LINKED)))
            .filter(n__gt=0)
            .order_by("?")[:sample_size]
        )
        for patient in sample:
            server_n = self.client.count("Observation", {"patient": patient.original_id})
            report["samples"].append(
                {"original_id": patient.original_id, "local": patient.n, "server": server_n, "match": server_n == patient.n}
            )
        self._set_phase(phase, **report)
        logger.info("verify: %s", report)
        return report


# --- persistence helpers ----------------------------------------------------------


def upsert_patients(records: list[PatientRecord]) -> None:
    if not records:
        return
    now = timezone.now()
    Patient.objects.bulk_create(
        [Patient(**r.model_dump(), imported_at=now) for r in records],
        update_conflicts=True,
        unique_fields=["original_id"],
        update_fields=PATIENT_UPDATE_FIELDS,
        batch_size=500,
    )


def upsert_observations(records: list[ObservationRecord], patient_pks: dict[str, int]) -> dict[str, int]:
    counts = {LinkStatus.LINKED: 0, LinkStatus.ORPHAN: 0, LinkStatus.NO_PATIENT_SUBJECT: 0}
    rows = []
    now = timezone.now()
    for r in records:
        if r.original_patient_id is None:
            status, patient_pk = LinkStatus.NO_PATIENT_SUBJECT, None
        elif (patient_pk := patient_pks.get(r.original_patient_id)) is not None:
            status = LinkStatus.LINKED
        else:
            status = LinkStatus.ORPHAN
        counts[status] += 1
        data = r.model_dump()
        data["components"] = [c.model_dump() for c in r.components]
        rows.append(Observation(**data, patient_id=patient_pk, link_status=status, imported_at=now))
    if rows:
        Observation.objects.bulk_create(
            rows,
            update_conflicts=True,
            unique_fields=["original_id"],
            update_fields=OBSERVATION_UPDATE_FIELDS,
            batch_size=500,
        )
    return counts
