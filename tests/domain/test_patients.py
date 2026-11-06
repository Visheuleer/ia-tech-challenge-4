from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from clinical_monitor.domain.patients import Encounter, Patient


def test_patient_rejects_identifying_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Patient.model_validate({"patient_id": str(uuid4()), "name": "Example"})


def test_encounter_normalizes_offsets_before_comparing_dates() -> None:
    encounter = Encounter.model_validate(
        {
            "encounter_id": str(uuid4()),
            "patient_id": str(uuid4()),
            "started_at": "2026-11-06T09:00:00-03:00",
            "ended_at": "2026-11-06T12:01:00Z",
        }
    )

    assert encounter.started_at == datetime(2026, 11, 6, 12, tzinfo=UTC)
    assert encounter.started_at.tzinfo is UTC


@pytest.mark.parametrize("ended_at", ["2026-11-06T11:59:00Z", "2026-11-06T13:00:00"])
def test_encounter_rejects_invalid_end_time(ended_at: str) -> None:
    with pytest.raises(ValidationError):
        Encounter.model_validate(
            {
                "encounter_id": str(uuid4()),
                "patient_id": str(uuid4()),
                "started_at": "2026-11-06T12:00:00Z",
                "ended_at": ended_at,
            }
        )


def test_encounter_can_remain_open() -> None:
    encounter = Encounter(
        encounter_id=uuid4(),
        patient_id=uuid4(),
        started_at=datetime(2026, 11, 6, 12, tzinfo=UTC),
    )

    assert encounter.ended_at is None
