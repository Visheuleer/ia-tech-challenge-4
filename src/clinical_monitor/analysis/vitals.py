from datetime import datetime
from hashlib import sha256
from uuid import uuid5

from clinical_monitor.domain.evidence import (
    DetectionMethod,
    Evidence,
    Modality,
    QualityStatus,
)
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement


class VitalRangeAnalyzer:
    def analyze(
        self,
        measurement: VitalMeasurement,
        reference: ReferenceRange,
        processed_at: datetime,
    ) -> Evidence:
        rule = (
            f"{measurement.signal.value}: "
            f"{reference.lower} <= value <= {reference.upper} {measurement.unit}"
        )
        version = f"1.0.0+{sha256(rule.encode()).hexdigest()}"
        finding = self._classify(measurement, reference)
        return Evidence(
            event_id=uuid5(measurement.source_id, f"vital-range/{version}"),
            patient_id=measurement.patient_id,
            encounter_id=measurement.encounter_id,
            source_id=measurement.source_id,
            modality=Modality.VITAL_SIGNS,
            observed_at=measurement.observed_at,
            window_start=measurement.observed_at,
            window_end=measurement.observed_at,
            processed_at=processed_at,
            detector_name="vital-range",
            detector_version=version,
            detection_method=DetectionMethod.RULE,
            finding_type=f"{measurement.signal.value}.{finding}",
            description=f"{measurement.signal.value}: {finding}",
            quality_status=measurement.quality_status,
            evidence_reference=f"vital-signs:{measurement.source_id}",
            measured_value=measurement.value,
            unit=measurement.unit,
            rule_criteria=f"quality=unusable -> not evaluated; otherwise {rule}",
        )

    def _classify(self, measurement: VitalMeasurement, reference: ReferenceRange) -> str:
        if measurement.quality_status == QualityStatus.UNUSABLE:
            return "not_evaluated"
        if measurement.value < reference.lower:
            return "below_range"
        if measurement.value > reference.upper:
            return "above_range"
        return "within_range"
