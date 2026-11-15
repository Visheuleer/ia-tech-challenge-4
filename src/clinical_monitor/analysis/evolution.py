import json
from datetime import UTC, datetime
from uuid import uuid5

from clinical_monitor.analysis.text import critical_terms, normalize_text, text_evidence

CONCEPTS = {
    "breathing": {
        "falta de ar",
        "dificuldade para respirar",
        "nao consigo respirar",
        "shortness of breath",
        "difficulty breathing",
        "breathless",
        "cannot breathe",
        "can't breathe",
    },
    "chest_pain": {"dor no peito", "chest pain"},
    "fatigue": {"cansaco", "tired", "fatigue"},
    "weakness": {"fraqueza", "weakness"},
    "dizziness": {"tontura", "dizziness"},
    "fainting": {"desmaio", "fainting"},
}


def concepts(text):
    terms = set(critical_terms(text))
    return {concept for concept, vocabulary in CONCEPTS.items() if vocabulary & terms}


def evolution_evidence(record, history):
    previous = [
        e
        for e in history
        if e.detector_name == "clinical-text" and e.observed_at < record.observed_at
    ]
    if not previous:
        return None
    prior = max(previous, key=lambda e: (e.observed_at, str(e.event_id)))
    before = concepts(json.loads(prior.description)["text"])
    after = concepts(record.text)
    added, resolved = sorted(after - before), sorted(before - after)
    description = {
        "previous_evidence_id": str(prior.event_id),
        "new_symptom_mentions": added,
        "no_longer_mentioned": resolved,
        "interpretation": "Change in documented mentions; omission does not establish recovery",
        "worsening_language": any(
            word in normalize_text(record.text).split()
            for word in ("piora", "piorando", "worse", "worsening")
        ),
    }
    finding = "new_symptoms" if added else "documentation_change"
    return text_evidence(record, datetime.now(UTC)).model_copy(
        update={
            "event_id": uuid5(record.source_id, f"evolution/1/{prior.event_id}"),
            "detector_name": "clinical-evolution",
            "detector_version": "1.0.0",
            "finding_type": f"text.{finding}",
            "description": json.dumps(description),
            "rule_criteria": "Compare explicit positive symptom mentions with preceding note",
        }
    )
