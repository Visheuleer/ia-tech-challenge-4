import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from clinical_monitor.analysis.audio import acoustic_features, read_audio
from clinical_monitor.analysis.evolution import evolution_evidence
from clinical_monitor.analysis.screening import predict, squat_features, voice_features
from clinical_monitor.analysis.text import text_evidence
from clinical_monitor.analysis.video import analyze_video
from clinical_monitor.application.errors import InvalidAnalysis, RecordNotFound
from clinical_monitor.application.jobs import JobService
from clinical_monitor.domain.evidence import DetectionMethod, Evidence, Modality, QualityStatus
from clinical_monitor.domain.monitoring import ClinicalText, Job
from clinical_monitor.infrastructure.azure import AzureClient, IntegrationUnavailable

logger = logging.getLogger(__name__)


class MediaProcessor:
    def __init__(self, jobs: JobService, settings, azure: AzureClient | None = None):
        self.jobs = jobs
        self.settings = settings
        self.azure = azure or AzureClient(settings)
        self._custom_azure = azure is not None

    def _client(self, language):
        if self._custom_azure:
            return self.azure
        return AzureClient(
            self.settings.model_copy(
                update={"speech_language": language, "text_language": language.split("-")[0]}
            )
        )

    def _screen(self, job, features, duration, result, evidence_ids):
        screening = predict(job.analysis_profile, features)
        result["screening"] = screening
        finding = (
            ("voice_change" if job.kind == "audio" else "exercise_deviation")
            if screening["flagged"]
            else "no_detected_deviation"
        )
        evidence = self._base(
            job,
            duration,
            "experimental-screening",
            finding,
            json.dumps(screening),
            "valid",
            "experimental",
            screening["model_version"],
        )
        evidence = Evidence.model_validate(
            {
                **evidence.model_dump(),
                "detection_method": DetectionMethod.MODEL,
                "rule_criteria": None,
            }
        )
        evidence_ids.append(self._save(evidence).event_id)

    def _save(self, evidence: Evidence) -> Evidence:
        try:
            existing = self.jobs.ingestion.get_evidence(evidence.event_id)
        except RecordNotFound:
            return self.jobs.ingestion.ingest_analysis_evidence(evidence)[0]
        return existing

    def run(self, job_id: UUID) -> Job | None:
        job = self.jobs.claim(job_id)
        if job is None:
            return None
        result, evidence_ids, errors = dict(job.result), list(job.evidence_ids), []
        try:
            path = self.jobs.directory(job_id) / job.input_name
            if job.kind == "audio":
                self._audio(job, path, result, evidence_ids, errors)
            elif job.kind == "video":
                self._video(job, path, result, evidence_ids)
            else:
                record = ClinicalText.model_validate_json(path.read_text(encoding="utf-8"))
                self._text(record, result, evidence_ids, errors)
        except (InvalidAnalysis, IntegrationUnavailable, ImportError) as exc:
            errors.append(str(exc))
        except Exception as exc:
            logger.error("Processing failed for job %s (%s)", job_id, type(exc).__name__)
            errors.append("Unexpected processing error; inspect server logs")
        return self.jobs.finish(job, result, list(dict.fromkeys(evidence_ids)), errors)

    def _base(
        self,
        job: Job,
        duration: float,
        name: str,
        finding: str,
        description: str,
        quality: str,
        criteria: str,
        version: str = "1.0.0",
    ) -> Evidence:
        end = job.observed_at + timedelta(seconds=duration)
        if end > datetime.now(UTC):
            raise InvalidAnalysis("Recording ends in the future")
        return Evidence(
            event_id=uuid5(job.job_id, f"{name}/{version}/{finding}"),
            patient_id=job.patient_id,
            encounter_id=job.encounter_id,
            source_id=job.job_id,
            modality=Modality(job.kind),
            observed_at=end,
            window_start=job.observed_at,
            window_end=end,
            processed_at=datetime.now(UTC),
            detector_name=name,
            detector_version=version,
            detection_method=DetectionMethod.RULE,
            finding_type=f"{job.kind}.{finding}",
            description=description,
            quality_status=QualityStatus(quality),
            evidence_reference=f"job:{job.job_id}/report.json",
            rule_criteria=criteria,
        )

    def _audio(self, job, path, result, evidence_ids, errors):
        samples, rate = read_audio(path, self.settings.max_media_seconds)
        features = acoustic_features(samples, rate, self.settings.audio_pause_seconds)
        result["acoustic"] = features
        if job.analysis_profile == "sustained_vowel":
            self._screen(
                job,
                voice_features(samples, rate),
                features["duration_seconds"],
                result,
                evidence_ids,
            )
            return
        if job.analysis_profile == "consultation":
            features["finding"] = "conversation_pauses_context"
        evidence = self._base(
            job,
            features["duration_seconds"],
            "acoustic-pauses",
            features["finding"],
            json.dumps(features),
            features["quality_status"],
            f"Energy-based pauses >= {self.settings.audio_pause_seconds}s; "
            "acoustic proxy, not diagnosis",
            f"1.0.0+{self.settings.audio_pause_seconds}",
        )
        evidence_ids.append(self._save(evidence).event_id)
        if "transcript" not in result:
            try:
                result["transcript"] = self._client(job.language).transcribe(samples, rate)
            except IntegrationUnavailable as exc:
                errors.append(str(exc))
        if result.get("transcript"):
            transcript = result["transcript"]
            if len(transcript) > 5000:
                errors.append("Text analysis limited to first 5000 characters")
            record = ClinicalText(
                source_id=job.job_id,
                patient_id=job.patient_id,
                encounter_id=job.encounter_id,
                observed_at=job.observed_at,
                text=transcript[:5000],
                language=job.language,
            )
            self._text(record, result, evidence_ids, errors)

    def _text(self, record, result, evidence_ids, errors):
        if "language" not in result:
            try:
                result["language"] = self._client(record.language).analyze_text(record.text)
            except IntegrationUnavailable as exc:
                errors.append(str(exc))
        result["text"] = record.text
        local = text_evidence(record, datetime.now(UTC))
        evidence_ids.append(self._save(local).event_id)
        evolution = evolution_evidence(
            record, self.jobs.ingestion.list_evidence(record.encounter_id)
        )
        if evolution is not None:
            evidence_ids.append(self._save(evolution).event_id)
            result["evolution"] = json.loads(evolution.description)
        if result.get("language"):
            cloud = text_evidence(record, datetime.now(UTC), result["language"])
            cloud = cloud.model_copy(
                update={
                    "event_id": uuid5(record.source_id, "azure-language/2.0.0"),
                    "detector_name": "azure-language",
                    "finding_type": "text.cloud_critical_terms"
                    if json.loads(cloud.description)["cloud_critical_terms"]
                    else "text.language_context",
                }
            )
            evidence_ids.append(self._save(cloud).event_id)

    def _video(self, job, path, result, evidence_ids):
        report = analyze_video(path, self.settings, self.jobs.directory(job.job_id))
        result["video"] = report
        if job.analysis_profile == "squat":
            self._screen(
                job, squat_features(report), report["duration_seconds"], result, evidence_ids
            )
            return
        valid = [frame for frame in report["frames"] if frame["quality"] == "valid"]
        kinds = []
        if any(frame["posture_deviation"] for frame in valid):
            kinds.append("posture_deviation")
        if any(frame["prolonged_stillness"] for frame in valid):
            kinds.append("prolonged_stillness")
        if not kinds:
            kinds = ["not_evaluated" if not valid else "no_detected_deviation"]
        criteria = (
            f"Single-person pose: tilt > {self.settings.video_tilt_degrees}; "
            f"knee asymmetry > {self.settings.video_asymmetry_degrees}; "
            f"stillness <= {self.settings.video_stillness_speed} for "
            f"{self.settings.video_stillness_seconds}s; no surgical assessment"
        )
        for finding in kinds:
            evidence = self._base(
                job,
                report["duration_seconds"],
                "yolov8-pose-rules",
                finding,
                json.dumps(report),
                report["quality_status"],
                criteria,
            )
            evidence_ids.append(self._save(evidence).event_id)
