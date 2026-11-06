from uuid import UUID

from clinical_monitor.application.errors import InvalidRelationship, RecordNotFound
from clinical_monitor.application.repositories import Repository
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.patients import Encounter, Patient


class IngestionService:
    def __init__(
        self,
        patients: Repository[Patient],
        encounters: Repository[Encounter],
        evidence: Repository[Evidence],
    ) -> None:
        self._patients = patients
        self._encounters = encounters
        self._evidence = evidence

    def register_patient(self, patient: Patient) -> bool:
        return self._patients.add(patient.patient_id, patient)

    def get_patient(self, patient_id: UUID) -> Patient:
        patient = self._patients.get(patient_id)
        if patient is None:
            raise RecordNotFound("Patient not found")
        return patient

    def register_encounter(self, encounter: Encounter) -> bool:
        self.get_patient(encounter.patient_id)
        return self._encounters.add(encounter.encounter_id, encounter)

    def get_encounter(self, encounter_id: UUID) -> Encounter:
        encounter = self._encounters.get(encounter_id)
        if encounter is None:
            raise RecordNotFound("Encounter not found")
        return encounter

    def ingest_evidence(self, evidence: Evidence) -> bool:
        self.get_patient(evidence.patient_id)
        encounter = self.get_encounter(evidence.encounter_id)
        if evidence.patient_id != encounter.patient_id:
            raise InvalidRelationship("Evidence patient does not match encounter patient")
        if evidence.window_start < encounter.started_at:
            raise InvalidRelationship("Evidence window starts before the encounter")
        if encounter.ended_at is not None and evidence.window_end > encounter.ended_at:
            raise InvalidRelationship("Evidence window ends after the encounter")
        return self._evidence.add(evidence.event_id, evidence)

    def get_evidence(self, event_id: UUID) -> Evidence:
        evidence = self._evidence.get(event_id)
        if evidence is None:
            raise RecordNotFound("Evidence not found")
        return evidence
