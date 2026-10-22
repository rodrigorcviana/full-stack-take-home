"""Internal records produced by the transform step and API output shapes."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

ValueTypeLiteral = Literal["quantity", "string", "codeable", "boolean", "integer", "components", "none"]


class PatientRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_id: str
    given_name: str = ""
    family_name: str = ""
    name_text: str = ""
    gender: str | None = None
    birth_date: date | None = None
    deceased: bool | None = None
    source_last_updated: datetime | None = None
    source_version_id: str = ""


class ComponentValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None = None
    display: str | None = None
    value: float | str | bool | None = None
    unit: str | None = None


class ObservationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_id: str
    original_patient_id: str | None = None
    status: str = "unknown"
    category: str = ""
    code_system: str = ""
    code: str = ""
    display: str = ""
    effective_at: datetime | None = None
    value_type: ValueTypeLiteral = "none"
    value_number: Decimal | None = None
    value_unit: str = ""
    value_text: str = ""
    components: list[ComponentValue] = []
    source_last_updated: datetime | None = None


# --- API output -------------------------------------------------------------


class ObservationOut(BaseModel):
    id: int
    status: str
    category: str
    code_system: str
    code: str
    display: str
    effective_at: datetime | None
    value_type: str
    value_number: float | None
    value_unit: str
    value_text: str
    components: list[ComponentValue]


class PatientSummaryOut(BaseModel):
    """What the UI shows, as an EHR would: name, age, gender. The source record id
    is deliberately not exposed (it resolves to the legacy record)."""

    id: int
    name: str
    age: int | None
    gender: str | None
    observation_count: int


class PatientDetailOut(PatientSummaryOut):
    observations: list[ObservationOut]

