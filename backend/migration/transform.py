"""Pure functions mapping FHIR resources to internal records.

Everything here is deliberately tolerant: the sandbox has patients without
names or birth dates, free-text-only names, observations with text-only
codes, string values, and component-only values. A TransformError is
raised only when a record is unusable (no id, no code at all).
"""

import re
from datetime import date, datetime, time, timezone
from decimal import Decimal

from django.utils.dateparse import parse_date, parse_datetime
from pydantic import ValidationError

from .schemas.fhir import CodeableConcept, FhirObservation, FhirPatient, ObservationValues
from .schemas.internal import ComponentValue, ObservationRecord, PatientRecord

LOINC = "http://loinc.org"
_PATIENT_REF = re.compile(r"(?:^|/)Patient/([A-Za-z0-9\-.]{1,64})(?:/_history/[^/]+)?$")


class TransformError(ValueError):
    """The resource cannot be represented in the internal model."""


# --- helpers ------------------------------------------------------------------


def parse_fhir_datetime(value: str | None) -> datetime | None:
    """FHIR dateTime/instant/date -> aware datetime (UTC).

    Date-only values become midnight UTC; partial dates (YYYY, YYYY-MM) and
    garbage become None rather than failing the record.
    """
    if not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        parsed = None
    if parsed is None:
        try:
            day = parse_date(value)
        except ValueError:
            day = None
        if day is None:
            return None
        return datetime.combine(day, time.min, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_fhir_date(value: str | None) -> date | None:
    """Full YYYY-MM-DD only; partial dates are dropped (we don't store precision)."""
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def parse_patient_reference(reference: str | None) -> str | None:
    """'Patient/123', 'https://host/fhir/Patient/123', 'Patient/123/_history/2' -> '123'."""
    if not reference:
        return None
    match = _PATIENT_REF.search(reference)
    return match.group(1) if match else None


def _pick_coding(concept: CodeableConcept | None):
    if concept is None or not concept.coding:
        return None
    return next((c for c in concept.coding if c.system == LOINC), concept.coding[0])


def _concept_label(concept: CodeableConcept | None) -> str:
    if concept is None:
        return ""
    coding = _pick_coding(concept)
    if coding and coding.display:
        return coding.display
    if concept.text:
        return concept.text
    return (coding.code or "") if coding else ""


def _flatten_value(values: ObservationValues) -> tuple[str, Decimal | None, str, str]:
    """value[x] -> (value_type, number, unit, text)."""
    if values.valueQuantity is not None and values.valueQuantity.value is not None:
        q = values.valueQuantity
        return "quantity", q.value, q.unit or q.code or "", ""
    if values.valueInteger is not None:
        return "integer", Decimal(values.valueInteger), "", ""
    if values.valueString is not None:
        return "string", None, "", values.valueString
    if values.valueCodeableConcept is not None:
        return "codeable", None, "", _concept_label(values.valueCodeableConcept)
    if values.valueBoolean is not None:
        return "boolean", None, "", "true" if values.valueBoolean else "false"
    return "none", None, "", ""


# --- Patient ------------------------------------------------------------------


def transform_patient(data: dict) -> PatientRecord:
    try:
        src = FhirPatient.model_validate(data)
    except ValidationError as exc:
        raise TransformError(f"invalid Patient: {exc.errors()[0]['msg']}") from exc

    name = next((n for n in src.name if n.use == "official"), src.name[0] if src.name else None)
    given = " ".join(part.strip() for part in name.given if part and part.strip()) if name else ""
    family = (name.family or "").strip() if name else ""
    name_text = (name.text or "").strip() if name and not (given or family) else ""

    deceased: bool | None = None
    if src.deceasedBoolean is not None:
        deceased = src.deceasedBoolean
    elif src.deceasedDateTime:
        deceased = True

    return PatientRecord(
        original_id=src.id,
        given_name=given,
        family_name=family,
        name_text=name_text,
        gender=src.gender,
        birth_date=parse_fhir_date(src.birthDate),
        deceased=deceased,
        source_last_updated=parse_fhir_datetime(src.meta.lastUpdated if src.meta else None),
        source_version_id=(src.meta.versionId if src.meta else None) or "",
    )


# --- Observation --------------------------------------------------------------


def transform_observation(data: dict) -> ObservationRecord:
    try:
        src = FhirObservation.model_validate(data)
    except ValidationError as exc:
        raise TransformError(f"invalid Observation: {exc.errors()[0]['msg']}") from exc

    coding = _pick_coding(src.code)
    if coding is None and not (src.code and src.code.text):
        raise TransformError("Observation has no code")

    effective = src.effectiveDateTime or (src.effectivePeriod.start if src.effectivePeriod else None) or src.effectiveInstant

    components = [
        ComponentValue(
            code=(_pick_coding(c.code).code if _pick_coding(c.code) else None),
            display=_concept_label(c.code) or None,
            value=_component_value(c),
            unit=(c.valueQuantity.unit or c.valueQuantity.code) if c.valueQuantity else None,
        )
        for c in src.component
    ]

    value_type, number, unit, text = _flatten_value(src)
    if value_type == "none" and components:
        value_type = "components"

    category = ""
    if src.category:
        cat_coding = _pick_coding(src.category[0])
        category = (cat_coding.code if cat_coding else None) or src.category[0].text or ""

    return ObservationRecord(
        original_id=src.id,
        original_patient_id=parse_patient_reference(src.subject.reference if src.subject else None),
        status=src.status or "unknown",
        category=category,
        code_system=(coding.system if coding else None) or "",
        code=(coding.code if coding else None) or "",
        display=_concept_label(src.code),
        effective_at=parse_fhir_datetime(effective),
        value_type=value_type,
        value_number=number,
        value_unit=unit,
        value_text=text,
        components=components,
        source_last_updated=parse_fhir_datetime(src.meta.lastUpdated if src.meta else None),
    )


def _component_value(values: ObservationValues) -> float | str | bool | None:
    value_type, number, _unit, text = _flatten_value(values)
    if value_type in ("quantity", "integer"):
        return float(number)
    if value_type == "boolean":
        return text == "true"
    if value_type == "none":
        return None
    return text
