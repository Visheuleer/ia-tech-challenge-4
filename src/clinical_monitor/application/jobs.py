from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from clinical_monitor.application.errors import InvalidAnalysis, RecordConflict, RecordNotFound
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.runtime import RuntimeStore
from clinical_monitor.domain.monitoring import ClinicalText, Job, MediaRequest


class JobService:
    def __init__(self, store: RuntimeStore, ingestion: IngestionService, settings):
        self.store = store
        self.ingestion = ingestion
        self.settings = settings

    def directory(self, job_id: UUID) -> Path:
        root = self.settings.data_dir.resolve() / "jobs"
        directory = root / str(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get(self, job_id: UUID) -> Job:
        row = self.store.get(f"job:{job_id}")
        if row is None:
            raise RecordNotFound("Job not found")
        return Job.model_validate(row[1])

    def list(self, encounter_id: UUID | None = None) -> list[Job]:
        return sorted(
            [Job.model_validate(row) for row in self.store.list("job", encounter_id)],
            key=lambda job: job.created_at,
            reverse=True,
        )

    def submit(self, request: MediaRequest | ClinicalText, content: bytes) -> tuple[Job, bool]:
        if len(content) > self.settings.max_upload_mb * 1024 * 1024 or not content:
            raise InvalidAnalysis("File is empty or exceeds upload size limit")
        encounter = self.ingestion.get_encounter(request.encounter_id)
        if encounter.patient_id != request.patient_id:
            raise InvalidAnalysis("Patient does not belong to this encounter")
        if (
            request.observed_at < encounter.started_at
            or (encounter.ended_at is not None and request.observed_at > encounter.ended_at)
            or request.observed_at > datetime.now(UTC)
        ):
            raise InvalidAnalysis("Recording time is outside the encounter or in the future")
        job_id = request.source_id if isinstance(request, ClinicalText) else request.job_id
        kind = "text" if isinstance(request, ClinicalText) else request.kind
        digest = hashlib.sha256(content).hexdigest()
        extension = {"audio": ".wav", "video": ".mp4", "text": ".json"}[kind]
        name = digest + extension
        now = datetime.now(UTC)
        job = Job(
            job_id=job_id,
            patient_id=request.patient_id,
            encounter_id=request.encounter_id,
            observed_at=request.observed_at,
            kind=kind,
            language=request.language,
            analysis_profile=getattr(request, "analysis_profile", "general"),
            created_at=now,
            updated_at=now,
            input_name=name,
            input_hash=digest,
        )
        existing = self.store.get(f"job:{job_id}")
        if existing:
            return self._replay(job, Job.model_validate(existing[1]))
        directory = self.directory(job_id)
        temporary = directory / f"{uuid4()}.tmp"
        temporary.write_bytes(content)
        os.replace(temporary, directory / name)
        if not self.store.create(
            f"job:{job_id}", "job", job.encounter_id, job.model_dump(mode="json")
        ):
            return self._replay(job, self.get(job_id))
        return job, True

    def _replay(self, candidate: Job, existing: Job) -> tuple[Job, bool]:
        fields = [
            "patient_id",
            "encounter_id",
            "observed_at",
            "kind",
            "input_hash",
            "language",
            "analysis_profile",
        ]
        if any(getattr(candidate, field) != getattr(existing, field) for field in fields):
            raise RecordConflict("Job identifier already exists with different input")
        return existing, False

    def claim(self, job_id: UUID) -> Job | None:
        key = f"job:{job_id}"
        for _ in range(5):
            row = self.store.get(key)
            if row is None:
                raise RecordNotFound("Job not found")
            version, payload = row
            job = Job.model_validate(payload)
            now = datetime.now(UTC)
            expired = (now - job.updated_at).total_seconds() > self.settings.task_lease_seconds
            if job.status != "queued" and not (job.status == "running" and expired):
                return None
            if job.attempts >= self.settings.job_max_attempts:
                failed = job.model_copy(
                    update={
                        "status": "failed",
                        "updated_at": now,
                        "errors": ("Maximum attempts reached",),
                    }
                )
                self.store.replace(key, version, failed.model_dump(mode="json"))
                return None
            claimed = job.model_copy(
                update={"status": "running", "updated_at": now, "attempts": job.attempts + 1}
            )
            if self.store.replace(key, version, claimed.model_dump(mode="json")):
                return claimed
        return None

    def finish(self, job: Job, result: dict, evidence_ids: list[UUID], errors: list[str]) -> Job:
        key = f"job:{job.job_id}"
        row = self.store.get(key)
        if row is None:
            raise RecordNotFound("Job not found")
        version, payload = row
        current = Job.model_validate(payload)
        if current.attempts != job.attempts or current.status != "running":
            return current
        status = "partial" if errors and evidence_ids else "failed" if errors else "completed"
        finished = current.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(UTC),
                "result": result,
                "evidence_ids": tuple(evidence_ids),
                "errors": tuple(errors),
            }
        )
        report = self.directory(job.job_id) / "report.json"
        temporary = report.with_suffix(f".{uuid4()}.tmp")
        temporary.write_text(finished.model_dump_json(indent=2), encoding="utf-8")
        if self.store.replace(key, version, finished.model_dump(mode="json")):
            os.replace(temporary, report)
            return finished
        temporary.unlink(missing_ok=True)
        return self.get(job.job_id)

    def retry(self, job_id: UUID) -> Job:
        key = f"job:{job_id}"
        row = self.store.get(key)
        if row is None:
            raise RecordNotFound("Job not found")
        version, payload = row
        job = Job.model_validate(payload)
        if job.status not in {"failed", "partial"}:
            raise InvalidAnalysis("Only partial or failed jobs can be retried")
        if job.attempts >= self.settings.job_max_attempts:
            raise InvalidAnalysis("Maximum attempts reached")
        queued = job.model_copy(update={"status": "queued", "updated_at": datetime.now(UTC)})
        if not self.store.replace(key, version, queued.model_dump(mode="json")):
            raise RecordConflict("Job was modified concurrently")
        return queued

    def submit_text(self, record: ClinicalText) -> tuple[Job, bool]:
        return self.submit(
            record, json.dumps(record.model_dump(mode="json"), ensure_ascii=False).encode()
        )
