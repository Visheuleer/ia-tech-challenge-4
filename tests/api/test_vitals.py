from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinical_monitor.core.config import Settings


@pytest.fixture
def client(
    app_factory: Callable[[], FastAPI], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("CLINICAL_MONITOR_VITAL_RANGES", '{"heart_rate":{"lower":10,"upper":20}}')
    with TestClient(app_factory()) as client:
        yield client


@pytest.fixture
def measurement(client: TestClient) -> dict[str, object]:
    patient_id, encounter_id = str(uuid4()), str(uuid4())
    assert client.post("/v1/patients", json={"patient_id": patient_id}).status_code == 201
    assert (
        client.post(
            "/v1/encounters",
            json={
                "encounter_id": encounter_id,
                "patient_id": patient_id,
                "started_at": "2020-01-01T00:00:00Z",
                "ended_at": "2020-01-02T00:00:00Z",
            },
        ).status_code
        == 201
    )
    return {
        "source_id": str(uuid4()),
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "signal": "heart_rate",
        "value": 25.0,
        "unit": "bpm",
        "observed_at": "2020-01-01T12:00:00Z",
        "quality_status": "valid",
    }


def test_analysis_is_persisted_and_replays_keep_original_processing_time(
    client: TestClient, measurement: dict[str, object]
) -> None:
    first = client.post("/v1/vital-signs/analyze", json=measurement)
    replay = client.post("/v1/vital-signs/analyze", json=measurement)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["finding_type"] == "heart_rate.above_range"
    assert client.get(f"/v1/evidence/{first.json()['event_id']}").json() == first.json()


@pytest.mark.parametrize(
    "changes",
    [
        {"value": 26.0},
        {"quality_status": "degraded"},
        {"observed_at": "2020-01-01T13:00:00Z"},
    ],
)
def test_changed_measurement_cannot_overwrite_previous_analysis(
    client: TestClient, measurement: dict[str, object], changes: dict[str, object]
) -> None:
    first = client.post("/v1/vital-signs/analyze", json=measurement)
    response = client.post("/v1/vital-signs/analyze", json={**measurement, **changes})

    assert response.status_code == 409
    assert client.get(f"/v1/evidence/{first.json()['event_id']}").json() == first.json()


@pytest.mark.parametrize(
    ("changes", "status_code"),
    [
        ({"patient_id": str(uuid4())}, 404),
        ({"observed_at": "2019-12-31T12:00:00Z"}, 422),
        ({"observed_at": "2020-01-03T12:00:00Z"}, 422),
        ({"observed_at": "2099-01-01T12:00:00Z"}, 422),
        ({"unit": "mmHg"}, 422),
        ({"signal": "systolic_pressure", "unit": "mmHg"}, 422),
    ],
)
def test_invalid_requests_are_rejected(
    client: TestClient,
    measurement: dict[str, object],
    changes: dict[str, object],
    status_code: int,
) -> None:
    response = client.post("/v1/vital-signs/analyze", json={**measurement, **changes})

    assert response.status_code == status_code


def test_concurrent_measurements_do_not_duplicate_analysis(
    client: TestClient, measurement: dict[str, object]
) -> None:
    def submit(_: int) -> tuple[int, dict]:
        response = client.post("/v1/vital-signs/analyze", json=measurement)
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(8)))

    assert [status for status, _ in results].count(201) == 1
    assert [status for status, _ in results].count(200) == 7
    assert all(payload == results[0][1] for _, payload in results)


def test_reference_ranges_are_loaded_from_environment(client: TestClient) -> None:
    assert Settings().vital_ranges["heart_rate"].upper == 20.0
