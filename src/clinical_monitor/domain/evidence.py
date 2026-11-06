from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.types import NonEmptyText, UtcDatetime


class Modality(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"
    VITAL_SIGNS = "vital_signs"
    PRESCRIPTION = "prescription"


class QualityStatus(StrEnum):
    VALID = "valid"
    DEGRADED = "degraded"
    UNUSABLE = "unusable"


class DetectionMethod(StrEnum):
    RULE = "rule"
    MODEL = "model"


class Evidence(DomainModel):
    event_id: UUID
    patient_id: UUID
    encounter_id: UUID
    source_id: UUID
    modality: Modality
    observed_at: UtcDatetime
    window_start: UtcDatetime
    window_end: UtcDatetime
    processed_at: UtcDatetime
    detector_name: NonEmptyText
    detector_version: NonEmptyText
    detection_method: DetectionMethod
    finding_type: NonEmptyText
    description: NonEmptyText
    quality_status: QualityStatus
    evidence_reference: NonEmptyText
    measured_value: Annotated[float, Field(strict=True)] | None = None
    unit: NonEmptyText | None = None
    confidence: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    rule_criteria: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("window_end must not precede window_start")
        if not self.window_start <= self.observed_at <= self.window_end:
            raise ValueError("observed_at must be within the analysis window")
        if self.processed_at < self.window_end:
            raise ValueError("processed_at must not precede window_end")
        return self

    @model_validator(mode="after")
    def validate_detection(self) -> Self:
        if self.detection_method == DetectionMethod.RULE:
            if self.rule_criteria is None:
                raise ValueError("rule detections require rule_criteria")
            if self.confidence is not None:
                raise ValueError("rule detections must not include confidence")
        if self.unit is not None and self.measured_value is None:
            raise ValueError("unit requires measured_value")
        return self
