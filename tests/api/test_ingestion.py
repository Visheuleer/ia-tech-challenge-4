from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from clinical_monitor.api.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def records() -> dict[str, dict[str, object]]:
    patient_id = str(uuid4())
    encounter_id = str(uuid4())
    return {
        "patients": {"patient_id": patient_id},
        "encounters": {
            "encounter_id": encounter_id,
            "patient_id": patient_id,
            "started_at": "2026-11-06T12:00:00Z",
            "ended_at": "2026-11-06T13:00:00Z",
        },
        "evidence": {
            "event_id": str(uuid4()),
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "source_id": str(uuid4()),
            "modality": "video",
            "observed_at": "2026-11-06T09:00:05-03:00",
            "window_start": "2026-11-06T12:00:00Z",
            "window_end": "2026-11-06T12:00:10Z",
            "processed_at": "2026-11-07T12:01:00Z",
            "detector_name": "movement-detector",
            "detector_version": "0.1.0",
            "detection_method": "model",
            "finding_type": "movement_deviation",
            "description": "Synthetic deviation",
            "quality_status": "valid",
            "evidence_reference": "recordings/example.mp4#t=0,10",
            "confidence": 0.8,
        },
    }


def register_encounter(client: TestClient, records: dict[str, dict[str, object]]) -> None:
    assert client.post("/v1/patients", json=records["patients"]).status_code == 201
    assert client.post("/v1/encounters", json=records["encounters"]).status_code == 201


def test_registration_ingestion_and_identical_replays(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    for resource, id_field in [
        ("patients", "patient_id"),
        ("encounters", "encounter_id"),
        ("evidence", "event_id"),
    ]:
        payload = records[resource]
        created = client.post(f"/v1/{resource}", json=payload)
        replay = client.post(f"/v1/{resource}", json=payload)
        fetched = client.get(f"/v1/{resource}/{payload[id_field]}")

        assert created.status_code == 201
        assert replay.status_code == 200
        assert fetched.status_code == 200
        assert created.json() == replay.json() == fetched.json()

    assert created.json()["observed_at"] == "2026-11-06T12:00:05Z"


@pytest.mark.parametrize("resource", ["patients", "encounters", "evidence"])
def test_missing_record_returns_not_found(client: TestClient, resource: str) -> None:
    response = client.get(f"/v1/{resource}/{uuid4()}")

    assert response.status_code == 404
    assert "detail" in response.json()


def test_encounter_requires_registered_patient(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    response = client.post("/v1/encounters", json=records["encounters"])

    assert response.status_code == 404
    assert client.get(f"/v1/encounters/{records['encounters']['encounter_id']}").status_code == 404


def test_evidence_requires_registered_encounter(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    client.post("/v1/patients", json=records["patients"])

    assert client.post("/v1/evidence", json=records["evidence"]).status_code == 404


def test_evidence_rejects_patient_from_another_encounter(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    register_encounter(client, records)
    other_patient = {"patient_id": str(uuid4())}
    client.post("/v1/patients", json=other_patient)
    evidence = {**records["evidence"], **other_patient}

    response = client.post("/v1/evidence", json=evidence)

    assert response.status_code == 422
    assert client.get(f"/v1/evidence/{evidence['event_id']}").status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("window_start", "2026-11-06T11:59:59Z"),
        ("window_end", "2026-11-06T13:00:01Z"),
        ("observed_at", "2026-11-06T12:00:05"),
        ("confidence", 1.5),
        ("detector_version", ""),
    ],
)
def test_invalid_evidence_is_not_stored(
    client: TestClient, records: dict[str, dict[str, object]], field: str, value: object
) -> None:
    register_encounter(client, records)
    evidence = {**records["evidence"], field: value}

    assert client.post("/v1/evidence", json=evidence).status_code == 422
    assert client.get(f"/v1/evidence/{evidence['event_id']}").status_code == 404


@pytest.mark.parametrize(
    ("resource", "id_field", "changes"),
    [
        ("encounters", "encounter_id", {"ended_at": "2026-11-06T14:00:00Z"}),
        ("evidence", "event_id", {"description": "Conflicting content"}),
        ("evidence", "event_id", {"detector_version": "0.2.0"}),
    ],
)
def test_conflicts_preserve_original_record(
    client: TestClient,
    records: dict[str, dict[str, object]],
    resource: str,
    id_field: str,
    changes: dict[str, object],
) -> None:
    register_encounter(client, records)
    client.post("/v1/evidence", json=records["evidence"])
    url = f"/v1/{resource}/{records[resource][id_field]}"
    original = client.get(url).json()

    response = client.post(f"/v1/{resource}", json={**records[resource], **changes})

    assert response.status_code == 409
    assert client.get(url).json() == original


def test_concurrent_replays_create_only_one_record(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    register_encounter(client, records)

    def submit(_: int) -> int:
        return client.post("/v1/evidence", json=records["evidence"]).status_code

    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(submit, range(8)))

    assert statuses.count(201) == 1
    assert statuses.count(200) == 7


def test_app_instances_do_not_share_records(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    client.post("/v1/patients", json=records["patients"])

    with TestClient(create_app()) as other_client:
        response = other_client.get(f"/v1/patients/{records['patients']['patient_id']}")

    assert response.status_code == 404


def test_open_encounter_accepts_evidence(
    client: TestClient, records: dict[str, dict[str, object]]
) -> None:
    records["encounters"]["ended_at"] = None
    register_encounter(client, records)

    assert client.post("/v1/evidence", json=records["evidence"]).status_code == 201


def test_routes_are_exposed_in_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for resource in ["patients", "encounters", "evidence"]:
        assert "post" in paths[f"/v1/{resource}"]
        assert {"200", "201", "422"} <= paths[f"/v1/{resource}"]["post"]["responses"].keys()
