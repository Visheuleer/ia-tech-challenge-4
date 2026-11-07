from collections.abc import Mapping
from datetime import UTC, datetime

from clinical_monitor.analysis.vitals import VitalRangeAnalyzer
from clinical_monitor.application.errors import InvalidAnalysis, RecordConflict
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement, VitalSign


class VitalSignsService:
    def __init__(
        self,
        ingestion: IngestionService,
        ranges: Mapping[VitalSign, ReferenceRange],
    ) -> None:
        self._ingestion = ingestion
        self._ranges = dict(ranges)
        self._analyzer = VitalRangeAnalyzer()

    def analyze(self, measurement: VitalMeasurement) -> tuple[Evidence, bool]:
        processed_at = datetime.now(UTC)
        if measurement.observed_at > processed_at:
            raise InvalidAnalysis("Measurement time must not be in the future")
        reference = self._ranges.get(measurement.signal)
        if reference is None:
            raise InvalidAnalysis("No reference range configured for this vital sign")

        evidence = self._analyzer.analyze(measurement, reference, processed_at)
        try:
            created = self._ingestion.ingest_evidence(evidence)
        except RecordConflict:
            existing = self._ingestion.get_evidence(evidence.event_id)
            if existing.model_dump(exclude={"processed_at"}) != evidence.model_dump(
                exclude={"processed_at"}
            ):
                raise
            return existing, False
        return evidence, created
