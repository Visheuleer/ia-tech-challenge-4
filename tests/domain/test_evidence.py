from datetime import UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinical_monitor.domain.evidence import Evidence


@pytest.fixture
def evidence_payload() -> dict[str, object]:
    return {
        "event_id": str(uuid4()),
        "patient_id": str(uuid4()),
        "encounter_id": str(uuid4()),
        "source_id": str(uuid4()),
        "modality": "video",
        "observed_at": "2026-11-06T09:00:05-03:00",
        "window_start": "2026-11-06T12:00:00Z",
        "window_end": "2026-11-06T12:00:10Z",
        "processed_at": "2026-11-06T12:01:00Z",
        "detector_name": "movement-detector",
        "detector_version": "0.1.0",
        "detection_method": "model",
        "finding_type": "movement_deviation",
        "description": "Synthetic movement deviation",
        "quality_status": "valid",
        "evidence_reference": "recordings/session.mp4#t=0,10",
        "confidence": 0.8,
    }


def test_evidence_round_trip_preserves_identity_and_normalizes_utc(
    evidence_payload: dict[str, object],
) -> None:
    evidence = Evidence.model_validate(evidence_payload)

    assert evidence.observed_at.tzinfo is UTC
    assert evidence.observed_at.hour == 12
    assert Evidence.model_validate_json(evidence.model_dump_json()) == evidence


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "invalid"),
        ("observed_at", "2026-11-06T12:00:05"),
        ("observed_at", "2026-11-06T12:00:11Z"),
        ("window_end", "2026-11-06T11:59:59Z"),
        ("processed_at", "2026-11-06T12:00:09Z"),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
        ("confidence", True),
        ("measured_value", float("inf")),
        ("detector_version", " "),
        ("evidence_reference", ""),
        ("quality_status", "unknown"),
        ("unexpected_field", "value"),
        ("unit", "degrees"),
    ],
)
def test_evidence_rejects_invalid_payloads(
    evidence_payload: dict[str, object], field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate({**evidence_payload, field: value})


def test_rule_requires_criteria_and_rejects_confidence(
    evidence_payload: dict[str, object],
) -> None:
    rule = {**evidence_payload, "detection_method": "rule", "confidence": None}
    with pytest.raises(ValidationError, match="require rule_criteria"):
        Evidence.model_validate(rule)

    rule["rule_criteria"] = "Synthetic threshold exceeded for 10 seconds"
    evidence = Evidence.model_validate(rule)
    assert evidence.confidence is None

    with pytest.raises(ValidationError, match="must not include confidence"):
        Evidence.model_validate({**rule, "confidence": 1.0})


@pytest.mark.parametrize("quality", ["degraded", "unusable"])
def test_partial_quality_is_preserved(evidence_payload: dict[str, object], quality: str) -> None:
    evidence = Evidence.model_validate(
        {
            **evidence_payload,
            "quality_status": quality,
            "confidence": None,
        }
    )

    assert evidence.quality_status == quality
    assert evidence.confidence is None


def test_evidence_cannot_be_mutated_after_validation(
    evidence_payload: dict[str, object],
) -> None:
    evidence = Evidence.model_validate(evidence_payload)

    with pytest.raises(ValidationError, match="frozen_instance"):
        evidence.confidence = 2.0


def test_instantaneous_event_and_delayed_processing_are_accepted(
    evidence_payload: dict[str, object],
) -> None:
    timestamp = "2026-11-06T12:00:00Z"
    evidence = Evidence.model_validate(
        {
            **evidence_payload,
            "window_start": timestamp,
            "window_end": timestamp,
            "observed_at": timestamp,
            "processed_at": "2026-11-07T12:00:00Z",
            "measured_value": 42.0,
            "unit": "degrees",
        }
    )

    assert evidence.window_start == evidence.window_end
    assert evidence.measured_value == 42.0
