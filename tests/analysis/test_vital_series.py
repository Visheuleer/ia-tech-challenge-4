from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinical_monitor.analysis.vital_series import VitalSeriesAnalyzer
from clinical_monitor.domain.vital_series import TemporalRule, VitalSeries
from clinical_monitor.domain.vitals import ReferenceRange


def build_series(points: list[tuple[int, float, str]]) -> VitalSeries:
    patient_id, encounter_id = uuid4(), uuid4()
    return VitalSeries.model_validate(
        {
            "series_id": uuid4(),
            "measurements": [
                {
                    "source_id": uuid4(),
                    "patient_id": patient_id,
                    "encounter_id": encounter_id,
                    "signal": "heart_rate",
                    "value": value,
                    "unit": "bpm",
                    "observed_at": datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
                    "quality_status": quality,
                }
                for seconds, value, quality in points
            ],
        }
    )


@pytest.mark.parametrize(
    ("points", "finding", "quality"),
    [
        (
            [(0, 25.0, "valid"), (5, 25.0, "valid"), (10, 25.0, "valid")],
            "sustained_deviation",
            "valid",
        ),
        (
            [(0, 5.0, "valid"), (5, 5.0, "valid"), (10, 5.0, "valid")],
            "sustained_deviation",
            "valid",
        ),
        (
            [(0, 15.0, "valid"), (5, 25.0, "valid"), (10, 15.0, "valid")],
            "no_sustained_deviation",
            "valid",
        ),
        (
            [(0, 25.0, "valid"), (5, 5.0, "valid"), (10, 25.0, "valid")],
            "no_sustained_deviation",
            "valid",
        ),
        (
            [(0, 10.0, "valid"), (5, 20.0, "valid"), (10, 10.0, "valid")],
            "no_sustained_deviation",
            "valid",
        ),
        (
            [(0, 25.0, "valid"), (5, 25.0, "valid"), (11, 25.0, "valid")],
            "insufficient_data",
            "degraded",
        ),
        (
            [(0, 25.0, "valid"), (5, 25.0, "unusable"), (10, 25.0, "valid")],
            "insufficient_data",
            "degraded",
        ),
        (
            [(0, 25.0, "valid"), (5, 25.0, "degraded"), (10, 25.0, "valid")],
            "insufficient_data",
            "degraded",
        ),
        (
            [(0, 25.0, "unusable"), (5, 25.0, "unusable"), (10, 25.0, "unusable")],
            "insufficient_data",
            "unusable",
        ),
        (
            [(0, 25.0, "valid"), (4, 25.0, "valid"), (9, 25.0, "valid")],
            "insufficient_data",
            "valid",
        ),
    ],
)
def test_temporal_classification_respects_continuity(
    points: list[tuple[int, float, str]], finding: str, quality: str
) -> None:
    series = build_series(points)
    result = VitalSeriesAnalyzer().analyze(
        series,
        ReferenceRange(lower=10.0, upper=20.0),
        TemporalRule(min_duration_seconds=10.0, max_gap_seconds=5.0, min_samples=3),
        datetime(2020, 1, 2, tzinfo=UTC),
    )

    assert result.finding_type == f"heart_rate.{finding}"
    assert result.quality_status == quality
    assert len(result.supporting_measurements) == len(points)
    assert result.confidence is None
    assert result.window_start == series.ordered_measurements[0].observed_at
    assert result.window_end == series.ordered_measurements[-1].observed_at


def test_multiple_episodes_are_reported_separately() -> None:
    series = build_series(
        [
            (0, 25.0, "valid"),
            (5, 25.0, "valid"),
            (10, 25.0, "valid"),
            (15, 15.0, "valid"),
            (20, 5.0, "valid"),
            (25, 5.0, "valid"),
            (30, 5.0, "valid"),
        ]
    )
    result = VitalSeriesAnalyzer().analyze(
        series,
        ReferenceRange(lower=10.0, upper=20.0),
        TemporalRule(min_duration_seconds=10.0, max_gap_seconds=5.0, min_samples=3),
        datetime(2020, 1, 2, tzinfo=UTC),
    )

    assert "above_range:" in result.description
    assert "below_range:" in result.description
    assert result.description.count("(3 samples)") == 2


def test_minimum_sample_count_cannot_be_replaced_by_duration() -> None:
    series = build_series([(0, 25.0, "valid"), (10, 25.0, "valid")])
    result = VitalSeriesAnalyzer().analyze(
        series,
        ReferenceRange(lower=10.0, upper=20.0),
        TemporalRule(min_duration_seconds=10.0, max_gap_seconds=10.0, min_samples=3),
        datetime(2020, 1, 2, tzinfo=UTC),
    )

    assert result.finding_type.endswith("insufficient_data")


@pytest.mark.parametrize("field", ["patient_id", "encounter_id", "source_id", "observed_at"])
def test_inconsistent_or_duplicate_samples_are_rejected(field: str) -> None:
    series = build_series([(0, 25.0, "valid"), (5, 25.0, "valid")]).model_dump(mode="json")
    first, second = series["measurements"]
    second[field] = first[field] if field in {"source_id", "observed_at"} else str(uuid4())

    with pytest.raises(ValidationError):
        VitalSeries.model_validate(series)


def test_mixed_signals_are_rejected() -> None:
    payload = build_series([(0, 25.0, "valid"), (5, 25.0, "valid")]).model_dump(mode="json")
    payload["measurements"][1].update(signal="systolic_pressure", unit="mmHg")

    with pytest.raises(ValidationError):
        VitalSeries.model_validate(payload)


@pytest.mark.parametrize("size", [0, 1, 1001])
def test_window_size_is_bounded(size: int) -> None:
    payload = build_series([(0, 25.0, "valid"), (5, 25.0, "valid")]).model_dump(mode="json")
    payload["measurements"] = [payload["measurements"][0]] * size

    with pytest.raises(ValidationError):
        VitalSeries.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"min_duration_seconds": 0},
        {"max_gap_seconds": -1},
        {"min_samples": 1},
        {"min_duration_seconds": float("inf")},
        {"min_samples": True},
    ],
)
def test_invalid_temporal_rules_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TemporalRule.model_validate(
            {"min_duration_seconds": 10.0, "max_gap_seconds": 5.0, "min_samples": 3, **changes}
        )
