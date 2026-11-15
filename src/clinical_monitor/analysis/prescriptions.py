from datetime import UTC, datetime
from uuid import uuid5

from clinical_monitor.application.errors import InvalidAnalysis
from clinical_monitor.domain.evidence import DetectionMethod, Evidence, Modality, QualityStatus
from clinical_monitor.domain.prescriptions import PrescriptionChange


class PrescriptionAnalyzer:
    def analyze(self, change: PrescriptionChange, max_relative_change: float) -> Evidence:
        before, after = change.previous, change.current
        if after.observed_at > datetime.now(UTC):
            raise InvalidAnalysis("Prescription must not be in the future")
        findings = []
        ratios = []
        if before.medication.casefold() != after.medication.casefold():
            findings.append("medication_change")
        if before.route.casefold() != after.route.casefold():
            findings.append("route_change")
        factors = {"g": 1000.0, "mg": 1.0, "mcg": 0.001}
        if before.unit == after.unit or before.unit in factors and after.unit in factors:
            old_dose = before.dose * factors.get(before.unit, 1.0)
            new_dose = after.dose * factors.get(after.unit, 1.0)
            dose_change = abs(new_dose - old_dose) / old_dose
            ratios.append(dose_change)
            if dose_change > max_relative_change:
                findings.append("dose_change")
        else:
            findings.append("unit_change")
        frequency_change = (
            abs(after.administrations_per_day - before.administrations_per_day)
            / before.administrations_per_day
        )
        ratios.append(frequency_change)
        if frequency_change > max_relative_change:
            findings.append("frequency_change")
        finding = findings[0] if findings else "within_expected_change"
        rule = (
            f"Relative dose/frequency change > {max_relative_change}; "
            "medication, route or incompatible unit changes"
        )
        version = f"1.0.0+{max_relative_change}"
        return Evidence(
            event_id=uuid5(after.source_id, f"prescription/{version}"),
            patient_id=after.patient_id,
            encounter_id=after.encounter_id,
            source_id=after.source_id,
            modality=Modality.PRESCRIPTION,
            observed_at=after.observed_at,
            window_start=before.observed_at,
            window_end=after.observed_at,
            processed_at=datetime.now(UTC),
            detector_name="prescription-change",
            detector_version=version,
            detection_method=DetectionMethod.RULE,
            finding_type=f"prescription.{finding}",
            description=change.model_dump_json(),
            quality_status=QualityStatus.VALID,
            evidence_reference=f"prescription:{after.source_id}",
            measured_value=max(ratios, default=0.0),
            unit="relative_change",
            rule_criteria=rule,
        )
