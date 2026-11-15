from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.types import NonEmptyText, UtcDatetime


class Prescription(DomainModel):
    source_id: UUID
    patient_id: UUID
    encounter_id: UUID
    observed_at: UtcDatetime
    medication: NonEmptyText
    dose: Annotated[float, Field(gt=0, strict=True)]
    unit: Literal["mg", "g", "mcg", "mL", "IU"]
    administrations_per_day: Annotated[float, Field(gt=0, le=24, strict=True)]
    route: NonEmptyText


class PrescriptionChange(DomainModel):
    previous: Prescription
    current: Prescription

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        before, after = self.previous, self.current
        if (before.patient_id, before.encounter_id) != (after.patient_id, after.encounter_id):
            raise ValueError("Prescriptions must belong to the same patient and encounter")
        if after.observed_at <= before.observed_at:
            raise ValueError("Current prescription must follow previous prescription")
        if before.source_id == after.source_id:
            raise ValueError("Prescription identifiers must differ")
        return self
