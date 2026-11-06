from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from clinical_monitor.api.app import create_app


def test_app_starts_with_environment_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("CLINICAL_MONITOR_APP_NAME", "Clinical Monitor Test")

    with TestClient(create_app()) as client:
        response = client.get("/health")
        schema = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Clinical Monitor Test"
    assert "/health" in schema.json()["paths"]
