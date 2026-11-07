from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinical_monitor.analysis.vitals import VitalRangeAnalyzer
from clinical_monitor.core.config import Settings
from clinical_monitor.domain.evidence import QualityStatus
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement, VitalSign


@pytest.fixture
def measurement_payload() -> dict[str, object]:
    return {
        "source_id": str(uuid4()),
        "patient_id": str(uuid4()),
        "encounter_id": str(uuid4()),
        "signal": "heart_rate",
        "value": 15.0,
        "unit": "bpm",
        "observed_at": "2020-01-01T09:00:00-03:00",
        "quality_status": "valid",
    }


@pytest.mark.parametrize(
    ("value", "finding"),
    [
        (9.0, "below_range"),
        (10.0, "within_range"),
        (15.0, "within_range"),
        (20.0, "within_range"),
        (21.0, "above_range"),
    ],
)
def test_reference_range_includes_both_boundaries(
    measurement_payload: dict[str, object], value: float, finding: str
) -> None:
    measurement = VitalMeasurement.model_validate({**measurement_payload, "value": value})
    evidence = VitalRangeAnalyzer().analyze(
        measurement, ReferenceRange(lower=10.0, upper=20.0), datetime(2020, 1, 2, tzinfo=UTC)
    )

    assert evidence.finding_type == f"heart_rate.{finding}"
    assert evidence.measured_value == value
    assert evidence.confidence is None
    assert evidence.rule_criteria is not None
    assert evidence.observed_at.hour == 12
    assert evidence.window_start == evidence.window_end


@pytest.mark.parametrize("signal", list(VitalSign))
def test_all_supported_signals_produce_traceable_evidence(
    measurement_payload: dict[str, object], signal: VitalSign
) -> None:
    unit = (
        "bpm"
        if signal == VitalSign.HEART_RATE
        else ("%" if signal == VitalSign.OXYGEN_SATURATION else "mmHg")
    )
    measurement = VitalMeasurement.model_validate(
        {
            **measurement_payload,
            "signal": signal,
            "unit": unit,
        }
    )
    evidence = VitalRangeAnalyzer().analyze(
        measurement, ReferenceRange(lower=10.0, upper=20.0), datetime(2020, 1, 2, tzinfo=UTC)
    )

    assert evidence.finding_type == f"{signal.value}.within_range"
    assert evidence.source_id == measurement.source_id
    assert evidence.unit == unit


@pytest.mark.parametrize(
    ("quality", "finding"),
    [("unusable", "not_evaluated"), ("degraded", "above_range")],
)
def test_quality_is_preserved_without_treating_unusable_data_as_normal(
    measurement_payload: dict[str, object], quality: str, finding: str
) -> None:
    measurement = VitalMeasurement.model_validate(
        {
            **measurement_payload,
            "value": 30.0,
            "quality_status": quality,
        }
    )
    evidence = VitalRangeAnalyzer().analyze(
        measurement, ReferenceRange(lower=10.0, upper=20.0), datetime(2020, 1, 2, tzinfo=UTC)
    )

    assert evidence.finding_type == f"heart_rate.{finding}"
    assert evidence.quality_status == QualityStatus(quality)


def test_rule_identity_changes_only_when_configuration_changes(
    measurement_payload: dict[str, object],
) -> None:
    measurement = VitalMeasurement.model_validate(measurement_payload)
    analyzer = VitalRangeAnalyzer()
    reference = ReferenceRange(lower=10.0, upper=20.0)
    first = analyzer.analyze(measurement, reference, datetime(2020, 1, 2, tzinfo=UTC))
    replay = analyzer.analyze(measurement, reference, datetime(2020, 1, 3, tzinfo=UTC))
    changed = analyzer.analyze(
        measurement, ReferenceRange(lower=10.0, upper=30.0), datetime(2020, 1, 3, tzinfo=UTC)
    )

    assert first.event_id == replay.event_id
    assert first.detector_version == replay.detector_version
    assert changed.event_id != first.event_id
    assert changed.detector_version != first.detector_version


@pytest.mark.parametrize(
    "changes",
    [
        {"value": -1.0},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": True},
        {"value": "15"},
        {"unit": "%"},
        {"observed_at": "2020-01-01T12:00:00"},
        {"signal": "oxygen_saturation", "unit": "%", "value": 101.0},
        {"quality_status": "unknown"},
    ],
)
def test_invalid_measurements_are_rejected(
    measurement_payload: dict[str, object], changes: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        VitalMeasurement.model_validate({**measurement_payload, **changes})


@pytest.mark.parametrize(
    "ranges",
    [
        {"heart_rate": {"lower": 20, "upper": 10}},
        {"heart_rate": {"lower": 10, "upper": 10}},
        {"heart_rate": {"lower": -1, "upper": 10}},
        {"heart_rate": {"lower": 0, "upper": float("inf")}},
        {"oxygen_saturation": {"lower": 10, "upper": 101}},
        {"unknown": {"lower": 10, "upper": 20}},
    ],
)
def test_invalid_configuration_is_rejected(ranges: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(vital_ranges=ranges)
