import io
import json
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

from clinical_monitor.analysis.audio import acoustic_features, read_audio
from clinical_monitor.analysis.prescriptions import PrescriptionAnalyzer
from clinical_monitor.analysis.text import critical_terms
from clinical_monitor.analysis.video import pose_features
from clinical_monitor.application.fusion import FusionService
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.jobs import JobService
from clinical_monitor.application.processing import MediaProcessor
from clinical_monitor.core.config import Settings
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.monitoring import AlertAction, ClinicalText, MediaRequest
from clinical_monitor.domain.patients import Encounter, Patient
from clinical_monitor.domain.prescriptions import PrescriptionChange
from clinical_monitor.infrastructure.azure import AzureClient, IntegrationUnavailable
from clinical_monitor.infrastructure.memory import InMemoryRepository
from clinical_monitor.infrastructure.runtime import MemoryRuntimeStore


@pytest.fixture
def services(tmp_path):
    ingestion = IngestionService(InMemoryRepository(), InMemoryRepository(), InMemoryRepository())
    patient_id, encounter_id = uuid4(), uuid4()
    ingestion.register_patient(Patient(patient_id=patient_id))
    ingestion.register_encounter(
        Encounter(
            encounter_id=encounter_id,
            patient_id=patient_id,
            started_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    store = MemoryRuntimeStore()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    fusion = FusionService(ingestion, store)
    ingestion.observe_evidence(fusion.refresh)
    return ingestion, fusion, JobService(store, ingestion, settings), patient_id, encounter_id


def make_evidence(
    patient_id, encounter_id, modality="audio", finding="prolonged_pauses", quality="valid"
):
    return Evidence(
        event_id=uuid4(),
        patient_id=patient_id,
        encounter_id=encounter_id,
        source_id=uuid4(),
        modality=modality,
        observed_at="2020-01-01T12:00:10Z",
        window_start="2020-01-01T12:00:00Z",
        window_end="2020-01-01T12:00:10Z",
        processed_at="2020-01-01T12:01:00Z",
        detector_name="test",
        detector_version="1",
        detection_method="rule",
        finding_type=f"{modality}.{finding}",
        description="Synthetic fixture",
        quality_status=quality,
        evidence_reference="fixture",
        rule_criteria="Synthetic rule",
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Paciente com falta de ar.", ["falta de ar"]),
        ("Paciente nega falta de ar.", []),
        ("Sem dor no peito.", []),
        ("Não apresenta tontura.", []),
        ("Nega dor no peito, mas apresenta falta de ar.", ["falta de ar"]),
        ("Não consigo respirar.", ["nao consigo respirar"]),
        ("Paciente relata cansaço e fraqueza.", ["cansaco", "fraqueza"]),
    ],
)
def test_contextual_term_detection(text, expected):
    assert sorted(critical_terms(text)) == sorted(expected)


def test_acoustic_pauses_and_silent_audio_are_distinguished():
    rate = 16000
    voiced = 0.2 * np.sin(2 * np.pi * 180 * np.arange(rate) / rate)
    samples = np.concatenate([voiced, np.zeros(rate * 3), voiced])
    result = acoustic_features(samples, rate, 2.0)
    assert result["finding"] == "prolonged_pauses"
    assert result["quality_status"] == "valid"
    assert acoustic_features(np.zeros(rate), rate)["quality_status"] == "unusable"


def test_wav_loading_resamples_stereo_and_rejects_invalid_file(tmp_path):
    path = tmp_path / "audio.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(np.zeros(16000, dtype="<i2").tobytes())
    samples, rate = read_audio(path)
    assert rate == 16000 and len(samples) == 16000
    path.write_bytes(b"not a wave file")
    with pytest.raises(Exception, match="Invalid PCM WAV"):
        read_audio(path)


def test_pose_geometry_detects_tilt_and_rejects_missing_joints():
    points = np.zeros((17, 3))
    points[:, 2] = 1.0
    for indices, y in [([5, 6], 0), ([11, 12], 2), ([13, 14], 4), ([15, 16], 6)]:
        points[indices[0], :2] = [0, y]
        points[indices[1], :2] = [2, y]
    assert pose_features(points)["trunk_tilt_degrees"] == 0
    points[[5, 6], 0] += 3
    assert pose_features(points)["trunk_tilt_degrees"] > 25
    points[15, 2] = 0.1
    assert pose_features(points) is None


def test_prescriptions_convert_units_without_false_dose_change():
    patient, encounter = uuid4(), uuid4()
    base = dict(
        patient_id=patient,
        encounter_id=encounter,
        medication="Demo",
        route="oral",
        administrations_per_day=1.0,
    )
    change = PrescriptionChange.model_validate(
        {
            "previous": {
                **base,
                "source_id": uuid4(),
                "observed_at": "2020-01-01T00:00:00Z",
                "dose": 1.0,
                "unit": "g",
            },
            "current": {
                **base,
                "source_id": uuid4(),
                "observed_at": "2020-01-01T00:01:00Z",
                "dose": 1000.0,
                "unit": "mg",
            },
        }
    )
    assert (
        PrescriptionAnalyzer().analyze(change, 0.5).finding_type.endswith("within_expected_change")
    )
    altered = change.model_dump()
    altered["current"]["dose"] = 2000.0
    assert (
        PrescriptionAnalyzer()
        .analyze(PrescriptionChange.model_validate(altered), 0.5)
        .finding_type.endswith("dose_change")
    )


def test_fusion_excludes_unusable_and_supports_review_and_reopening(services):
    ingestion, fusion, _, patient, encounter = services
    ingestion.ingest_evidence(make_evidence(patient, encounter, quality="unusable"))
    assert fusion.list() == []
    first = make_evidence(patient, encounter)
    ingestion.ingest_evidence(first)
    alert = fusion.list()[0]
    assert alert.severity == "medium"
    fusion.transition(
        alert.alert_id, AlertAction(status="acknowledged", actor="Reviewer", note="Seen")
    )
    fusion.transition(
        alert.alert_id, AlertAction(status="closed", actor="Reviewer", note="Reviewed")
    )
    fusion.refresh(encounter)
    assert fusion.list()[0].status == "closed"
    ingestion.ingest_evidence(make_evidence(patient, encounter, "video", "posture_deviation"))
    reopened = fusion.list()[0]
    assert reopened.status == "open"
    assert reopened.severity == "high"
    assert len(reopened.history) == 3
    assert len(reopened.evidence_ids) == 2


def test_fusion_does_not_mix_patients(services):
    ingestion, fusion, _, patient, encounter = services
    other = uuid4()
    ingestion.register_patient(Patient(patient_id=other))
    second = uuid4()
    ingestion.register_encounter(
        Encounter(encounter_id=second, patient_id=other, started_at="2020-01-01T00:00:00Z")
    )
    ingestion.ingest_evidence(make_evidence(patient, encounter))
    ingestion.ingest_evidence(make_evidence(other, second, "video", "posture_deviation"))
    assert len(fusion.list()) == 2
    assert all(len(alert.evidence_ids) == 1 for alert in fusion.list())


def test_blank_reviewer_is_rejected():
    with pytest.raises(ValidationError):
        AlertAction(status="acknowledged", actor=" ", note="review")


def test_stale_fusion_snapshot_cannot_remove_new_evidence(services):
    ingestion, fusion, _, patient, encounter = services
    ingestion.ingest_evidence(make_evidence(patient, encounter))
    stale = fusion.list()[0]
    ingestion.ingest_evidence(make_evidence(patient, encounter, "video", "posture_deviation"))
    merged = fusion._merge(stale)
    assert len(merged.evidence_ids) == 2
    assert merged.severity == "high"


def test_only_one_worker_claims_a_job(services):
    _, _, jobs, patient, encounter = services
    job, _ = jobs.submit_text(
        ClinicalText(
            source_id=uuid4(),
            patient_id=patient,
            encounter_id=encounter,
            observed_at="2020-01-01T12:00:00Z",
            text="Paciente com falta de ar.",
        )
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: jobs.claim(job.job_id), range(8)))
    assert sum(claim is not None for claim in claims) == 1


def test_missing_azure_is_partial_and_successful_retry_preserves_evidence(services):
    ingestion, fusion, jobs, patient, encounter = services
    job, _ = jobs.submit_text(
        ClinicalText(
            source_id=uuid4(),
            patient_id=patient,
            encounter_id=encounter,
            observed_at="2020-01-01T12:00:00Z",
            text="Paciente com falta de ar.",
        )
    )
    processor = MediaProcessor(jobs, jobs.settings)
    result = processor.run(job.job_id)
    assert result.status == "partial"
    assert len(result.evidence_ids) == 1
    assert fusion.list()[0].reason == "text.critical_terms"

    class Cloud:
        def analyze_text(self, text):
            return {
                "sentiment": {"sentiment": "negative"},
                "phrases": {"keyPhrases": ["falta de ar"]},
            }

    jobs.retry(job.job_id)
    result = MediaProcessor(jobs, jobs.settings, azure=Cloud()).run(job.job_id)
    assert result.status == "completed"
    assert len(ingestion.list_evidence(encounter)) == 2
    assert (jobs.directory(job.job_id) / "report.json").is_file()


def test_audio_pipeline_persists_acoustics_transcript_and_cloud_context(services):
    ingestion, _, jobs, patient, encounter = services
    buffer = io.BytesIO()
    rate = 16000
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((np.sin(np.arange(rate) * 0.08) * 5000).astype("<i2").tobytes())
    job, _ = jobs.submit(
        MediaRequest(
            job_id=uuid4(),
            patient_id=patient,
            encounter_id=encounter,
            observed_at="2020-01-01T12:00:00Z",
            kind="audio",
        ),
        buffer.getvalue(),
    )

    class Cloud:
        def transcribe(self, samples, rate):
            return "Estou com falta de ar."

        def analyze_text(self, text):
            return {
                "sentiment": {"sentiment": "negative"},
                "phrases": {"keyPhrases": ["falta de ar"]},
            }

    result = MediaProcessor(jobs, jobs.settings, azure=Cloud()).run(job.job_id)
    assert result.status == "completed"
    assert len(result.evidence_ids) == 3
    assert {item.modality.value for item in ingestion.list_evidence(encounter)} == {"audio", "text"}


def test_azure_contracts_use_expected_paths_headers_and_payloads():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        if "/stt/" in request.url.path:
            assert request.content[:4] == b"RIFF"
            return httpx.Response(200, json={"RecognitionStatus": "Success", "DisplayText": "Olá."})
        kind = json.loads(request.content)["kind"]
        document = (
            {"id": "1", "sentiment": "neutral"}
            if kind == "SentimentAnalysis"
            else {"id": "1", "keyPhrases": ["consulta"]}
        )
        return httpx.Response(200, json={"results": {"documents": [document], "errors": []}})

    settings = Settings(
        _env_file=None,
        azure_speech_endpoint="https://speech.example.com",
        azure_speech_key="test-key",
        azure_language_endpoint="https://language.example.com",
        azure_language_key="test-key",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        azure = AzureClient(settings, client)
        assert azure.transcribe(np.ones(16000), 16000) == "Olá."
        assert azure.analyze_text("Consulta.")["sentiment"]["sentiment"] == "neutral"
    assert len(requests) == 3


def test_azure_errors_do_not_leak_keys():
    settings = Settings(
        _env_file=None,
        azure_language_endpoint="https://language.example.com",
        azure_language_key="hidden-key",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429))) as client:
        with pytest.raises(IntegrationUnavailable, match="429") as exc:
            AzureClient(settings, client).analyze_text("Teste")
        assert "hidden-key" not in str(exc.value)
