import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient


def test_jobs_reports_and_upload_boundaries(app_factory, monkeypatch, tmp_path):
    monkeypatch.setenv("CLINICAL_MONITOR_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("clinical_monitor.api.routes.monitoring.enqueue", lambda job_id, kind: None)
    app = app_factory()
    with TestClient(app) as client:
        patient_id, encounter_id, source_id = (str(uuid4()) for _ in range(3))
        client.post("/v1/patients", json={"patient_id": patient_id})
        context = {"patient_id": patient_id, "encounter_id": encounter_id}
        client.post("/v1/encounters", json={**context, "started_at": "2020-01-01T00:00:00Z"})
        record = {
            **context,
            "source_id": source_id,
            "observed_at": "2020-01-01T01:00:00Z",
            "text": "<script>alert(1)</script> falta de ar",
        }
        response = client.post("/v1/jobs/text", json=record)
        assert response.status_code == 202
        assert client.post("/v1/jobs/text", json=record).json() == response.json()
        assert client.post("/v1/jobs/text", json={**record, "text": "Outro"}).status_code == 409
        assert client.post(f"/v1/jobs/{source_id}/retry").status_code == 422
        job = app.state.jobs.claim(UUID(source_id))
        app.state.jobs.finish(job, {"text": record["text"]}, [], ["Unavailable"])
        artifact = client.get(f"/v1/jobs/{source_id}/artifacts/report.json")
        assert artifact.status_code == 200
        assert artifact.json()["status"] == "failed"
        assert client.get(f"/v1/jobs/{source_id}/artifacts/.env").status_code == 404
        report = client.get(f"/v1/encounters/{encounter_id}/report?format=html")
        assert report.status_code == 200
        assert "<script>" not in report.text
        assert "&lt;script&gt;" in report.text
        assert client.post(f"/v1/jobs/{source_id}/retry").json()["status"] == "queued"
        metadata = {
            **context,
            "job_id": str(uuid4()),
            "kind": "audio",
            "observed_at": record["observed_at"],
        }
        response = client.post(
            "/v1/jobs/media",
            data={"metadata": json.dumps(metadata)},
            files={"file": ("test.wav", b"invalid", "audio/wav")},
        )
        assert response.status_code == 422
        assert len(client.get("/v1/jobs").json()) == 1


def test_api_token_and_configuration_redaction(app_factory, monkeypatch):
    monkeypatch.setenv("CLINICAL_MONITOR_API_TOKEN", "test-private-token")
    monkeypatch.setenv("CLINICAL_MONITOR_AZURE_SPEECH_KEY", "test-private-key")
    with TestClient(app_factory()) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/configuration").status_code == 401
        response = client.get(
            "/v1/configuration", headers={"Authorization": "Bearer test-private-token"}
        )
        assert response.status_code == 200
        assert "test-private" not in response.text


def test_text_queue_does_not_share_media_backlog(monkeypatch):
    from clinical_monitor.worker import enqueue, process_media

    calls = []
    monkeypatch.setattr(process_media, "apply_async", lambda **kwargs: calls.append(kwargs))
    identifier = uuid4()
    assert enqueue(identifier, "text")
    assert enqueue(identifier, "audio")
    assert enqueue(identifier, "video")
    assert [call["queue"] for call in calls] == ["clinical", "media", "media"]
    assert all(call["args"] == [str(identifier)] for call in calls)
