import io
import json
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from clinical_monitor.analysis.evolution import evolution_evidence
from clinical_monitor.analysis.screening import voice_features
from clinical_monitor.analysis.text import critical_terms, text_evidence
from clinical_monitor.analysis.video import single_complete_pose
from clinical_monitor.application.errors import InvalidAnalysis, RecordConflict
from clinical_monitor.application.processing import MediaProcessor
from clinical_monitor.application.vitals import VitalSignsService
from clinical_monitor.domain.monitoring import ClinicalText, MediaRequest
from clinical_monitor.domain.vital_series import TemporalRule
from clinical_monitor.domain.vitals import ReferenceRange, VitalMeasurement, VitalSign
from clinical_monitor.tools.evaluate_real_data import fit_model
from tests import test_multimodal

services = test_multimodal.services


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("No chest pain, but shortness of breath.", ["shortness of breath"]),
        ("Denies any dizziness.", []),
        ("I cannot breathe.", ["cannot breathe"]),
    ],
)
def test_english_negation(text, expected):
    assert critical_terms(text) == expected


def record(**changes):
    return ClinicalText(
        source_id=uuid4(),
        patient_id=uuid4(),
        encounter_id=uuid4(),
        observed_at=datetime(2020, 1, 1, tzinfo=UTC),
        **changes,
    )


def test_cloud_terms_keep_original_negation():
    item = record(text="No chest pain. Shortness of breath.", language="en-GB")
    evidence = text_evidence(
        item, datetime.now(UTC), {"phrases": {"keyPhrases": ["chest pain", "shortness of breath"]}}
    )
    assert json.loads(evidence.description)["cloud_critical_terms"] == ["shortness of breath"]


def test_evolution_compares_prior_note_and_does_not_call_omission_recovery():
    first = record(text="Paciente com tontura.")
    later = first.model_copy(
        update={
            "source_id": uuid4(),
            "text": "Paciente com falta de ar.",
            "observed_at": first.observed_at + timedelta(minutes=1),
        }
    )
    history = [text_evidence(first, datetime.now(UTC)), text_evidence(later, datetime.now(UTC))]
    result = evolution_evidence(later, history)
    assert result.finding_type == "text.new_symptoms"
    data = json.loads(result.description)
    assert data["new_symptom_mentions"] == ["breathing"]
    assert data["no_longer_mentioned"] == ["dizziness"]
    assert evolution_evidence(first, history) is None


def test_profile_changes_cannot_reuse_job(services):
    _, _, jobs, patient, encounter = services
    request = MediaRequest(
        job_id=uuid4(),
        patient_id=patient,
        encounter_id=encounter,
        observed_at="2020-01-01T00:00:00Z",
        kind="audio",
    )
    jobs.submit(request, b"audio")
    with pytest.raises(RecordConflict):
        jobs.submit(request.model_copy(update={"analysis_profile": "consultation"}), b"audio")
    with pytest.raises(ValidationError):
        MediaRequest.model_validate({**request.model_dump(), "analysis_profile": "squat"})


def test_silent_voice_is_not_classified():
    with pytest.raises(InvalidAnalysis):
        voice_features(np.zeros(16000), 16000)


def test_incoming_points_trigger_temporal_detection_and_replay_is_idempotent(services):
    ingestion, fusion, _, patient, encounter = services
    analyzer = VitalSignsService(
        ingestion,
        {VitalSign.HEART_RATE: ReferenceRange(lower=10.0, upper=20.0)},
        {
            VitalSign.HEART_RATE: TemporalRule(
                min_duration_seconds=10.0, max_gap_seconds=5.0, min_samples=3
            )
        },
    )
    samples = [
        VitalMeasurement(
            source_id=uuid4(),
            patient_id=patient,
            encounter_id=encounter,
            signal="heart_rate",
            value=25.0,
            unit="bpm",
            quality_status="valid",
            observed_at=datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=second),
        )
        for second in (0, 5, 10)
    ]
    for sample in samples:
        analyzer.analyze(sample)
    evidence = ingestion.list_evidence(encounter)
    assert any(item.finding_type == "heart_rate.sustained_deviation" for item in evidence)
    analyzer.analyze(samples[-1])
    assert len(ingestion.list_evidence(encounter)) == len(evidence)
    assert fusion.list(encounter)
    later = samples[-1].model_copy(
        update={
            "source_id": uuid4(),
            "value": 15.0,
            "observed_at": samples[-1].observed_at + timedelta(minutes=1),
        }
    )
    analyzer.analyze(later)
    analyzer.analyze(
        later.model_copy(
            update={"source_id": uuid4(), "observed_at": later.observed_at + timedelta(seconds=5)}
        )
    )
    latest = ingestion.list_evidence(encounter)[-1]
    assert not latest.finding_type.endswith("sustained_deviation")


def test_pose_ignores_incomplete_border_detection_but_rejects_two_complete_people():
    points = np.zeros((17, 3))
    points[:, 2] = 1.0
    for indices, y in [([5, 6], 0), ([11, 12], 2), ([13, 14], 4), ([15, 16], 6)]:
        points[indices[0], :2] = [0, y]
        points[indices[1], :2] = [2, y]
    assert single_complete_pose([points, np.zeros((17, 3))]) is not None
    assert single_complete_pose([points, points]) is None
    points[5, 0] = np.nan
    assert single_complete_pose([points]) is None


def test_fit_does_not_learn_from_validation_or_test():
    rows = [
        {
            "split": "development",
            "participant": str(i),
            "target": i % 2,
            "features": {"value": float(i)},
        }
        for i in range(8)
    ]
    baseline = fit_model(rows, "test", "checksum")
    rows.append(
        {"split": "test", "participant": "reserved", "target": 1, "features": {"value": 1e12}}
    )
    assert fit_model(rows, "test", "checksum") == baseline


def test_consultation_silences_do_not_generate_vocal_anomaly(services):
    ingestion, fusion, jobs, patient, encounter = services
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        samples = np.r_[np.sin(np.arange(16000) * 0.08) * 5000, np.zeros(48000)]
        audio.writeframes(samples.astype("<i2").tobytes())
    request = MediaRequest(
        job_id=uuid4(),
        patient_id=patient,
        encounter_id=encounter,
        observed_at="2020-01-01T12:00:00Z",
        kind="audio",
        analysis_profile="consultation",
    )
    job, _ = jobs.submit(request, buffer.getvalue())

    class Cloud:
        def transcribe(self, samples, rate):
            return "Bom dia."

        def analyze_text(self, text):
            return {"sentiment": {"sentiment": "neutral"}, "phrases": {"keyPhrases": []}}

    result = MediaProcessor(jobs, jobs.settings, azure=Cloud()).run(job.job_id)
    assert result.status == "completed"
    assert result.result["acoustic"]["long_pauses"]
    assert not fusion.list(encounter)
    assert any(
        e.finding_type == "audio.conversation_pauses_context"
        for e in ingestion.list_evidence(encounter)
    )
