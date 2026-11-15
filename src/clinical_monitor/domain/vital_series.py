from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, model_validator

from clinical_monitor.domain.base import DomainModel
from clinical_monitor.domain.vitals import VitalMeasurement


class TemporalRule(DomainModel):
    min_duration_seconds: Annotated[float, Field(gt=0, strict=True)]
    max_gap_seconds: Annotated[float, Field(gt=0, strict=True)]
    min_samples: Annotated[int, Field(ge=2, le=1000, strict=True)] = 2


class VitalSeries(DomainModel):
    series_id: UUID
    measurements: Annotated[tuple[VitalMeasurement, ...], Field(min_length=2, max_length=1000)]

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        first = self.measurements[0]
        identity = (first.patient_id, first.encounter_id, first.signal, first.unit)
        if any(
            (sample.patient_id, sample.encounter_id, sample.signal, sample.unit) != identity
            for sample in self.measurements
        ):
            raise ValueError("Measurements must share patient, encounter, signal and unit")
        if len({sample.source_id for sample in self.measurements}) != len(self.measurements):
            raise ValueError("Measurement identifiers must be unique")
        if len({sample.observed_at for sample in self.measurements}) != len(self.measurements):
            raise ValueError("Measurement timestamps must be unique")
        return self

    @property
    def ordered_measurements(self) -> tuple[VitalMeasurement, ...]:
        return tuple(sorted(self.measurements, key=lambda sample: sample.observed_at))
