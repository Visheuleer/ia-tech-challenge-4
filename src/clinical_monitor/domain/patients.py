from typing import Self
from uuid import UUID

from pydantic import model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.types import UtcDatetime


class Patient(DomainModel):
    patient_id: UUID


class Encounter(DomainModel):
    encounter_id: UUID
    patient_id: UUID
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self
