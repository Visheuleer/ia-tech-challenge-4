from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(
    app_factory: Callable[[], FastAPI],
    monkeypatch: pytest.MonkeyPatch,
    temporal_payload: dict,
) -> Iterator[TestClient]:
    monkeypatch.setenv("CLINICAL_MONITOR_VITAL_RANGES", '{"heart_rate":{"lower":10,"upper":20}}')
    monkeypatch.setenv(
        "CLINICAL_MONITOR_VITAL_TEMPORAL_RULES",
        '{"heart_rate":{"min_duration_seconds":10,"max_gap_seconds":5,"min_samples":3}}',
    )
    first = temporal_payload["measurements"][0]
    with TestClient(app_factory()) as client:
        assert (
            client.post("/v1/patients", json={"patient_id": first["patient_id"]}).status_code == 201
        )
        assert (
            client.post(
                "/v1/encounters",
                json={
                    "patient_id": first["patient_id"],
                    "encounter_id": first["encounter_id"],
                    "started_at": "2020-01-01T00:00:00Z",
                    "ended_at": "2020-01-02T00:00:00Z",
                },
            ).status_code
            == 201
        )
        yield client


def test_reordered_replays_preserve_evidence_and_samples(
    client: TestClient,
    temporal_payload: dict,
) -> None:
    first = client.post("/v1/vital-signs/analyze-series", json=temporal_payload)
    temporal_payload["measurements"].reverse()
    replay = client.post("/v1/vital-signs/analyze-series", json=temporal_payload)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["finding_type"] == "heart_rate.sustained_deviation"
    assert len(first.json()["supporting_measurements"]) == 3
    assert client.get(f"/v1/evidence/{first.json()['event_id']}").json() == first.json()


def test_changed_sample_conflicts_even_if_finding_remains_the_same(
    client: TestClient,
    temporal_payload: dict,
) -> None:
    original = client.post("/v1/vital-signs/analyze-series", json=temporal_payload)
    temporal_payload["measurements"][1]["value"] = 26.0

    assert client.post("/v1/vital-signs/analyze-series", json=temporal_payload).status_code == 409
    assert client.get(f"/v1/evidence/{original.json()['event_id']}").json() == original.json()


def test_concurrent_series_replays_create_one_evidence(
    client: TestClient,
    temporal_payload: dict,
) -> None:
    def submit(_: int) -> tuple[int, dict]:
        response = client.post("/v1/vital-signs/analyze-series", json=temporal_payload)
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(8)))

    assert [status for status, _ in results].count(201) == 1
    assert [status for status, _ in results].count(200) == 7
    assert all(result == results[0][1] for _, result in results)


@pytest.mark.parametrize("timestamp", ["2019-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_outside_encounter_or_future_windows_are_rejected(
    client: TestClient, temporal_payload: dict, timestamp: str
) -> None:
    temporal_payload["measurements"][0]["observed_at"] = timestamp

    assert client.post("/v1/vital-signs/analyze-series", json=temporal_payload).status_code == 422


def test_temporal_rule_must_be_configured(
    app_factory: Callable[[], FastAPI], monkeypatch: pytest.MonkeyPatch, temporal_payload: dict
) -> None:
    monkeypatch.setenv("CLINICAL_MONITOR_VITAL_TEMPORAL_RULES", "{}")
    with TestClient(app_factory()) as client:
        response = client.post("/v1/vital-signs/analyze-series", json=temporal_payload)
    assert response.status_code == 422
