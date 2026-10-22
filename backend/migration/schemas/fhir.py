"""Loose pydantic models for the FHIR R4 fields we read.

Unknown fields are ignored on purpose: the source is messy and we only
need a small subset. Dates are kept as strings here and parsed in the
transform step, because FHIR dates may be partial (YYYY or YYYY-MM).
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FhirModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Coding(FhirModel):
    system: str | None = None
    code: str | None = None
    display: str | None = None


class CodeableConcept(FhirModel):
    coding: list[Coding] = []
    text: str | None = None


class Reference(FhirModel):
    reference: str | None = None
    display: str | None = None


class Quantity(FhirModel):
    value: Decimal | None = None
    unit: str | None = None
    code: str | None = None
    system: str | None = None


class Period(FhirModel):
    start: str | None = None
    end: str | None = None


class HumanName(FhirModel):
    use: str | None = None
    family: str | None = None
    given: list[str] = []
    text: str | None = None


class Meta(FhirModel):
    versionId: str | None = None
    lastUpdated: str | None = None


class FhirPatient(FhirModel):
    resourceType: Literal["Patient"]
    id: str
    meta: Meta | None = None
    name: list[HumanName] = []
    gender: str | None = None
    birthDate: str | None = None
    deceasedBoolean: bool | None = None
    deceasedDateTime: str | None = None


class ObservationValues(FhirModel):
    """The value[x] choice shared by Observation and Observation.component."""

    valueQuantity: Quantity | None = None
    valueString: str | None = None
    valueCodeableConcept: CodeableConcept | None = None
    valueBoolean: bool | None = None
    valueInteger: int | None = None


class ObservationComponent(ObservationValues):
    code: CodeableConcept | None = None


class FhirObservation(ObservationValues):
    resourceType: Literal["Observation"]
    id: str
    meta: Meta | None = None
    status: str | None = None
    category: list[CodeableConcept] = []
    code: CodeableConcept | None = None
    subject: Reference | None = None
    effectiveDateTime: str | None = None
    effectivePeriod: Period | None = None
    effectiveInstant: str | None = None
    component: list[ObservationComponent] = []
