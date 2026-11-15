from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.types import NonEmptyText, UtcDatetime


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class AlertTransition(DomainModel):
    status: AlertStatus
    actor: NonEmptyText
    note: NonEmptyText
    occurred_at: UtcDatetime


class Alert(DomainModel):
    alert_id: UUID
    patient_id: UUID
    encounter_id: UUID
    window_start: UtcDatetime
    window_end: UtcDatetime
    severity: Literal["medium", "high"]
    status: AlertStatus = AlertStatus.OPEN
    evidence_ids: tuple[UUID, ...]
    modalities: tuple[str, ...]
    missing_modalities: tuple[str, ...]
    reason: NonEmptyText
    updated_at: UtcDatetime
    history: tuple[AlertTransition, ...] = ()


class AlertAction(DomainModel):
    status: AlertStatus
    actor: Annotated[NonEmptyText, Field(max_length=100)]
    note: Annotated[NonEmptyText, Field(max_length=1000)]


class ClinicalText(DomainModel):
    source_id: UUID
    patient_id: UUID
    encounter_id: UUID
    observed_at: UtcDatetime
    text: Annotated[str, Field(min_length=1, max_length=5000)]
    language: Literal["pt-BR", "en-GB", "en-US"] = "pt-BR"


class MediaRequest(DomainModel):
    job_id: UUID
    patient_id: UUID
    encounter_id: UUID
    observed_at: UtcDatetime
    kind: Literal["audio", "video"]
    language: Literal["pt-BR", "en-GB", "en-US"] = "pt-BR"
    analysis_profile: Literal["general", "squat", "sustained_vowel", "consultation"] = "general"

    @model_validator(mode="after")
    def validate_profile(self):
        profiles = {
            "video": {"general", "squat"},
            "audio": {"general", "sustained_vowel", "consultation"},
        }
        if self.analysis_profile not in profiles[self.kind]:
            raise ValueError("Analysis profile does not match media kind")
        return self


class Job(DomainModel):
    job_id: UUID
    patient_id: UUID
    encounter_id: UUID
    observed_at: UtcDatetime
    kind: Literal["audio", "video", "text"]
    language: Literal["pt-BR", "en-GB", "en-US"] = "pt-BR"
    analysis_profile: Literal["general", "squat", "sustained_vowel", "consultation"] = "general"
    status: Literal["queued", "running", "completed", "partial", "failed"] = "queued"
    created_at: UtcDatetime
    updated_at: UtcDatetime
    attempts: int = 0
    input_name: str
    input_hash: str
    evidence_ids: tuple[UUID, ...] = ()
    errors: tuple[str, ...] = ()
    result: dict = Field(default_factory=dict)
