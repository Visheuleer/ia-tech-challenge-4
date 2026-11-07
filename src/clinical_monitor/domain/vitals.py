from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.evidence import QualityStatus
from clinical_monitor.domain.types import UtcDatetime


class VitalSign(StrEnum):
    HEART_RATE = "heart_rate"
    OXYGEN_SATURATION = "oxygen_saturation"
    SYSTOLIC_PRESSURE = "systolic_pressure"
    DIASTOLIC_PRESSURE = "diastolic_pressure"


UNITS = {
    VitalSign.HEART_RATE: "bpm",
    VitalSign.OXYGEN_SATURATION: "%",
    VitalSign.SYSTOLIC_PRESSURE: "mmHg",
    VitalSign.DIASTOLIC_PRESSURE: "mmHg",
}


class ReferenceRange(DomainModel):
    lower: Annotated[float, Field(ge=0, strict=True)]
    upper: Annotated[float, Field(gt=0, strict=True)]

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.lower >= self.upper:
            raise ValueError("lower must be less than upper")
        return self


class VitalMeasurement(DomainModel):
    source_id: UUID
    patient_id: UUID
    encounter_id: UUID
    signal: VitalSign
    value: Annotated[float, Field(ge=0, strict=True)]
    unit: Literal["bpm", "%", "mmHg"]
    observed_at: UtcDatetime
    quality_status: QualityStatus

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        if self.unit != UNITS[self.signal]:
            raise ValueError("Unit does not match the vital sign")
        if self.signal == VitalSign.OXYGEN_SATURATION and self.value > 100:
            raise ValueError("Oxygen saturation must not exceed 100 percent")
        return self
