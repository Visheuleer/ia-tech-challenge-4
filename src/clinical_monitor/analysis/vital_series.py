from datetime import datetime
from hashlib import sha256
from uuid import uuid5

from clinical_monitor.domain.evidence import (
    DetectionMethod,
    Evidence,
    EvidenceMeasurement,
    Modality,
    QualityStatus,
)
from clinical_monitor.domain.vital_series import TemporalRule, VitalSeries
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement


class VitalSeriesAnalyzer:
    def analyze(
        self,
        series: VitalSeries,
        reference: ReferenceRange,
        rule: TemporalRule,
        processed_at: datetime,
    ) -> Evidence:
        samples = series.ordered_measurements
        first, last = samples[0], samples[-1]
        criteria = (
            f"{first.signal.value}: outside [{reference.lower}, {reference.upper}] "
            f"{first.unit} on the same side; valid quality only; "
            f"duration >= {rule.min_duration_seconds}s; "
            f"gap <= {rule.max_gap_seconds}s; samples >= {rule.min_samples}"
        )
        version = f"1.0.0+{sha256(criteria.encode()).hexdigest()}"
        episodes = self._episodes(samples, reference, rule)
        quality = self._quality(samples, rule)
        sufficient = any(
            self._qualifies(segment, rule) for segment in self._segments(samples, rule)
        )
        finding = "insufficient_data"
        if episodes:
            finding = "sustained_deviation"
        elif sufficient:
            finding = "no_sustained_deviation"
        description = "; ".join(episodes) if episodes else finding
        return Evidence(
            event_id=uuid5(series.series_id, f"vital-series/{version}"),
            patient_id=first.patient_id,
            encounter_id=first.encounter_id,
            source_id=series.series_id,
            modality=Modality.VITAL_SIGNS,
            observed_at=last.observed_at,
            window_start=first.observed_at,
            window_end=last.observed_at,
            processed_at=processed_at,
            detector_name="vital-series",
            detector_version=version,
            detection_method=DetectionMethod.RULE,
            finding_type=f"{first.signal.value}.{finding}",
            description=description,
            quality_status=quality,
            evidence_reference=f"vital-series:{series.series_id}",
            rule_criteria=criteria,
            supporting_measurements=tuple(
                EvidenceMeasurement(
                    source_id=sample.source_id,
                    observed_at=sample.observed_at,
                    measured_value=sample.value,
                    unit=sample.unit,
                    quality_status=sample.quality_status,
                )
                for sample in samples
            ),
        )

    def _episodes(
        self,
        samples: tuple[VitalMeasurement, ...],
        reference: ReferenceRange,
        rule: TemporalRule,
    ) -> list[str]:
        episodes: list[str] = []
        for segment in self._segments(samples, rule):
            run: list[VitalMeasurement] = []
            direction = ""
            for sample in segment:
                current = (
                    "below_range"
                    if sample.value < reference.lower
                    else "above_range"
                    if sample.value > reference.upper
                    else ""
                )
                if current != direction:
                    self._record_episode(episodes, run, direction, rule)
                    run = []
                direction = current
                if direction:
                    run.append(sample)
            self._record_episode(episodes, run, direction, rule)
        return episodes

    def _segments(
        self, samples: tuple[VitalMeasurement, ...], rule: TemporalRule
    ) -> list[list[VitalMeasurement]]:
        segments: list[list[VitalMeasurement]] = []
        current: list[VitalMeasurement] = []
        for sample in samples:
            gap = (sample.observed_at - current[-1].observed_at).total_seconds() if current else 0
            if sample.quality_status != QualityStatus.VALID or gap > rule.max_gap_seconds:
                if current:
                    segments.append(current)
                current = []
            if sample.quality_status == QualityStatus.VALID:
                current.append(sample)
        if current:
            segments.append(current)
        return segments

    def _qualifies(self, samples: list[VitalMeasurement], rule: TemporalRule) -> bool:
        return (
            len(samples) >= rule.min_samples
            and (samples[-1].observed_at - samples[0].observed_at).total_seconds()
            >= rule.min_duration_seconds
        )

    def _record_episode(
        self,
        episodes: list[str],
        run: list[VitalMeasurement],
        direction: str,
        rule: TemporalRule,
    ) -> None:
        if self._qualifies(run, rule):
            episodes.append(
                f"{direction}: {run[0].observed_at.isoformat()} to "
                f"{run[-1].observed_at.isoformat()} ({len(run)} samples)"
            )

    def _quality(self, samples: tuple[VitalMeasurement, ...], rule: TemporalRule) -> QualityStatus:
        if all(sample.quality_status == QualityStatus.UNUSABLE for sample in samples):
            return QualityStatus.UNUSABLE
        has_gap = any(
            (right.observed_at - left.observed_at).total_seconds() > rule.max_gap_seconds
            for left, right in zip(samples, samples[1:], strict=False)
        )
        if has_gap or any(sample.quality_status != QualityStatus.VALID for sample in samples):
            return QualityStatus.DEGRADED
        return QualityStatus.VALID
