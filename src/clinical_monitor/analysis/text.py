import re
import unicodedata

from clinical_monitor.domain.evidence import DetectionMethod, Evidence, Modality, QualityStatus
from clinical_monitor.domain.monitoring import ClinicalText


def normalize_text(text: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )


def critical_terms(text: str) -> list[str]:
    normalized = normalize_text(text)
    terms = [
        "falta de ar",
        "dor no peito",
        "dificuldade para respirar",
        "nao consigo respirar",
        "desmaio",
        "tontura",
        "cansaco",
        "fraqueza",
        "shortness of breath",
        "chest pain",
        "difficulty breathing",
        "breathless",
        "cannot breathe",
        "can't breathe",
        "fainting",
        "dizziness",
        "tired",
        "fatigue",
        "weakness",
    ]
    found = []
    for term in terms:
        for match in re.finditer(r"\b" + re.escape(term) + r"\b", normalized):
            prefix = normalized[max(0, match.start() - 80) : match.start()]
            clause = re.split(r"[.!?;,]|\b(mas|porem|but|however)\b", prefix)[-1]
            if not re.search(
                r"\b(nao|nega|negou|sem|ausencia de|no|not|denies|without|denied)\b"
                r"(?:\s+\w+){0,3}\s*$",
                clause,
            ):
                found.append(term)
                break
    return found


def text_evidence(record: ClinicalText, processed_at, cloud: dict | None = None) -> Evidence:
    from uuid import uuid5

    terms = critical_terms(record.text)
    phrases = (cloud or {}).get("phrases", {}).get("keyPhrases", [])
    grounded = [term for term in terms if any(term in normalize_text(p) for p in phrases)]
    description = {
        "text": record.text,
        "critical_terms": terms,
        "azure": cloud,
        "cloud_critical_terms": grounded,
        "language": record.language,
    }
    import json

    return Evidence(
        event_id=uuid5(record.source_id, "clinical-text/2.0.0"),
        patient_id=record.patient_id,
        encounter_id=record.encounter_id,
        source_id=record.source_id,
        modality=Modality.TEXT,
        observed_at=record.observed_at,
        window_start=record.observed_at,
        window_end=record.observed_at,
        processed_at=processed_at,
        detector_name="clinical-text",
        detector_version="2.0.0",
        detection_method=DetectionMethod.RULE,
        finding_type="text.critical_terms" if terms else "text.no_critical_terms",
        description=json.dumps(description, ensure_ascii=False),
        quality_status=QualityStatus.VALID,
        evidence_reference=f"text:{record.source_id}",
        rule_criteria=(
            "Critical vocabulary with local negation; sentiment is contextual, not a diagnosis"
        ),
    )
