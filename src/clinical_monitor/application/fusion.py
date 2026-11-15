from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from clinical_monitor.application.errors import InvalidAnalysis, RecordConflict, RecordNotFound
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.runtime import RuntimeStore
from clinical_monitor.domain.evidence import Evidence, QualityStatus
from clinical_monitor.domain.monitoring import Alert, AlertAction, AlertStatus, AlertTransition


def is_anomaly(evidence: Evidence) -> bool:
    if evidence.quality_status == QualityStatus.UNUSABLE:
        return False
    return evidence.finding_type.rsplit(".", 1)[-1] in {
        "above_range",
        "below_range",
        "sustained_deviation",
        "dose_change",
        "frequency_change",
        "medication_change",
        "route_change",
        "unit_change",
        "posture_deviation",
        "prolonged_stillness",
        "prolonged_pauses",
        "critical_terms",
        "cloud_critical_terms",
        "new_symptoms",
        "voice_change",
        "exercise_deviation",
    }


class FusionService:
    def __init__(self, ingestion: IngestionService, store: RuntimeStore, window_seconds: int = 60):
        self._ingestion = ingestion
        self._store = store
        self._window_seconds = window_seconds

    def refresh(self, encounter_id: UUID) -> list[Alert]:
        encounter = self._ingestion.get_encounter(encounter_id)
        windows: dict[int, list[Evidence]] = defaultdict(list)
        for evidence in self._ingestion.list_evidence(encounter_id):
            windows[int(evidence.observed_at.timestamp()) // self._window_seconds].append(evidence)
        alerts = []
        for bucket, items in sorted(windows.items()):
            anomalies = sorted(
                (item for item in items if is_anomaly(item)), key=lambda item: str(item.event_id)
            )
            if not anomalies:
                continue
            start = datetime.fromtimestamp(bucket * self._window_seconds, UTC)
            modalities = sorted({item.modality.value for item in anomalies})
            available = {
                item.modality.value
                for item in items
                if item.quality_status != QualityStatus.UNUSABLE
            }
            severity = (
                "high"
                if len(modalities) >= 2 or {"vital_signs", "prescription"} & set(modalities)
                else "medium"
            )
            alert = Alert(
                alert_id=uuid5(encounter_id, f"fusion-v1/{self._window_seconds}/{bucket}"),
                patient_id=encounter.patient_id,
                encounter_id=encounter_id,
                window_start=start,
                window_end=start + timedelta(seconds=self._window_seconds),
                severity=severity,
                evidence_ids=tuple(item.event_id for item in anomalies),
                modalities=tuple(modalities),
                missing_modalities=tuple(
                    sorted({"audio", "video", "text", "vital_signs", "prescription"} - available)
                ),
                reason="; ".join(sorted({item.finding_type for item in anomalies})),
                updated_at=datetime.now(UTC),
            )
            alerts.append(self._merge(alert))
        return alerts

    def _merge(self, candidate: Alert) -> Alert:
        key = f"alert:{candidate.alert_id}"
        for _ in range(10):
            current = self._store.get(key)
            if current is None:
                if self._store.create(
                    key, "alert", candidate.encounter_id, candidate.model_dump(mode="json")
                ):
                    return candidate
                continue
            version, payload = current
            existing = Alert.model_validate(payload)
            candidate = candidate.model_copy(
                update={
                    "evidence_ids": tuple(
                        sorted(set(candidate.evidence_ids) | set(existing.evidence_ids), key=str)
                    ),
                    "modalities": tuple(
                        sorted(set(candidate.modalities) | set(existing.modalities))
                    ),
                    "missing_modalities": tuple(
                        sorted(set(candidate.missing_modalities) & set(existing.missing_modalities))
                    ),
                    "severity": "high"
                    if "high" in {candidate.severity, existing.severity}
                    else "medium",
                    "reason": "; ".join(
                        sorted(set(candidate.reason.split("; ")) | set(existing.reason.split("; ")))
                    ),
                }
            )
            fields = {"updated_at", "status", "history"}
            if candidate.model_dump(exclude=fields) == existing.model_dump(exclude=fields):
                return existing
            new_evidence = set(candidate.evidence_ids) - set(existing.evidence_ids)
            status = (
                AlertStatus.OPEN
                if new_evidence and existing.status == AlertStatus.CLOSED
                else existing.status
            )
            history = existing.history
            if status != existing.status:
                history += (
                    AlertTransition(
                        status=status,
                        actor="system",
                        note="New evidence received",
                        occurred_at=candidate.updated_at,
                    ),
                )
            merged = candidate.model_copy(update={"status": status, "history": history})
            if self._store.replace(key, version, merged.model_dump(mode="json")):
                return merged
        raise RecordConflict("Alert was modified concurrently; retry")

    def list(self, encounter_id: UUID | None = None) -> list[Alert]:
        return sorted(
            [Alert.model_validate(item) for item in self._store.list("alert", encounter_id)],
            key=lambda alert: alert.updated_at,
            reverse=True,
        )

    def transition(self, alert_id: UUID, action: AlertAction) -> Alert:
        key = f"alert:{alert_id}"
        for _ in range(10):
            row = self._store.get(key)
            if row is None:
                raise RecordNotFound("Alert not found")
            version, payload = row
            alert = Alert.model_validate(payload)
            if alert.status == action.status:
                return alert
            allowed = {
                AlertStatus.OPEN: {AlertStatus.ACKNOWLEDGED},
                AlertStatus.ACKNOWLEDGED: {AlertStatus.CLOSED},
                AlertStatus.CLOSED: set(),
            }
            if action.status not in allowed[alert.status]:
                raise InvalidAnalysis("Recognize the alert before closing it")
            now = datetime.now(UTC)
            transition = AlertTransition(
                status=action.status,
                actor=action.actor.strip(),
                note=action.note.strip(),
                occurred_at=now,
            )
            changed = alert.model_copy(
                update={
                    "status": action.status,
                    "updated_at": now,
                    "history": alert.history + (transition,),
                }
            )
            if self._store.replace(key, version, changed.model_dump(mode="json")):
                return changed
        raise RecordConflict("Alert was modified concurrently; retry")
