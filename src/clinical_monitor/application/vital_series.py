from collections.abc import Mapping
from datetime import UTC, datetime

from clinical_monitor.analysis.vital_series import VitalSeriesAnalyzer
from clinical_monitor.application.errors import InvalidAnalysis
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.vital_series import TemporalRule, VitalSeries
from clinical_monitor.domain.vitals import ReferenceRange, VitalSign


class VitalSeriesService:
    def __init__(
        self,
        ingestion: IngestionService,
        ranges: Mapping[VitalSign, ReferenceRange],
        rules: Mapping[VitalSign, TemporalRule],
    ) -> None:
        self._ingestion = ingestion
        self._ranges = dict(ranges)
        self._rules = dict(rules)
        self._analyzer = VitalSeriesAnalyzer()

    def analyze(self, series: VitalSeries) -> tuple[Evidence, bool]:
        processed_at = datetime.now(UTC)
        samples = series.ordered_measurements
        if samples[-1].observed_at > processed_at:
            raise InvalidAnalysis("Measurement time must not be in the future")
        signal = samples[0].signal
        reference, rule = self._ranges.get(signal), self._rules.get(signal)
        if reference is None or rule is None:
            raise InvalidAnalysis("Reference range and temporal rule must be configured")
        evidence = self._analyzer.analyze(series, reference, rule, processed_at)
        return self._ingestion.ingest_analysis_evidence(evidence)
