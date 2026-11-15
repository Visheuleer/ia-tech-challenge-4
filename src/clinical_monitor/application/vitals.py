from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid5

from clinical_monitor.analysis.vitals import VitalRangeAnalyzer
from clinical_monitor.application.errors import InvalidAnalysis
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.vital_series import VitalSeriesService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.vital_series import TemporalRule, VitalSeries
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement, VitalSign


class VitalSignsService:
    def __init__(
        self,
        ingestion: IngestionService,
        ranges: Mapping[VitalSign, ReferenceRange],
        temporal_rules: Mapping[VitalSign, TemporalRule] | None = None,
    ) -> None:
        self._ingestion = ingestion
        self._ranges = dict(ranges)
        self._analyzer = VitalRangeAnalyzer()
        self._temporal_rules = dict(temporal_rules or {})

    def analyze(self, measurement: VitalMeasurement) -> tuple[Evidence, bool]:
        processed_at = datetime.now(UTC)
        if measurement.observed_at > processed_at:
            raise InvalidAnalysis("Measurement time must not be in the future")
        reference = self._ranges.get(measurement.signal)
        if reference is None:
            raise InvalidAnalysis("No reference range configured for this vital sign")

        evidence = self._analyzer.analyze(measurement, reference, processed_at)
        saved = self._ingestion.ingest_analysis_evidence(evidence)
        if measurement.signal in self._temporal_rules:
            self._analyze_history(measurement)
        return saved

    def _analyze_history(self, measurement: VitalMeasurement) -> None:
        points = {}
        rule = self._temporal_rules[measurement.signal]
        horizon = (
            max(rule.min_duration_seconds, rule.max_gap_seconds * (rule.min_samples - 1))
            + rule.max_gap_seconds
        )
        start = measurement.observed_at - timedelta(seconds=horizon)
        for item in self._ingestion.list_evidence(measurement.encounter_id):
            if (
                item.detector_name == "vital-range"
                and item.finding_type.startswith(f"{measurement.signal.value}.")
                and item.observed_at <= measurement.observed_at
                and item.observed_at >= start
            ):
                points[item.observed_at] = VitalMeasurement(
                    source_id=item.source_id,
                    patient_id=item.patient_id,
                    encounter_id=item.encounter_id,
                    signal=measurement.signal,
                    value=item.measured_value,
                    unit=item.unit,
                    observed_at=item.observed_at,
                    quality_status=item.quality_status,
                )
        samples = tuple(points[key] for key in sorted(points)[-1000:])
        if len(samples) < 2:
            return
        identity = "/".join(str(sample.source_id) for sample in samples)
        series = VitalSeries(
            series_id=uuid5(measurement.encounter_id, identity), measurements=samples
        )
        VitalSeriesService(self._ingestion, self._ranges, self._temporal_rules).analyze(series)
