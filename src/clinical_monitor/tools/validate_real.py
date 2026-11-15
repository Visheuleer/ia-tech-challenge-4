import argparse
import csv
import json
import math
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np

from clinical_monitor.core.config import Settings


def reference_words(path: Path, end: float) -> list[str]:
    content = path.read_text(encoding="utf-8")
    utterances = re.findall(
        r'intervals \[\d+\]:\s+xmin = ([\d.]+)\s+xmax = ([\d.]+)\s+text = "(.*?)"',
        content,
        re.DOTALL,
    )
    text = " ".join(text for start, stop, text in utterances if float(stop) <= end)
    return words(re.sub(r"<[^>]*>", "", text))


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())


def word_error_rate(reference: list[str], hypothesis: list[str]) -> float | None:
    if not reference:
        return None
    previous = list(range(len(hypothesis) + 1))
    for i, word in enumerate(reference, 1):
        current = [i]
        for j, candidate in enumerate(hypothesis, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != candidate))
            )
        previous = current
    return previous[-1] / len(reference)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--data", type=Path, default=Path("data/real"))
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--interval", type=float, default=0.0)
    args = parser.parse_args()
    token = Settings().api_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    output = args.data / "evaluation"
    output.mkdir(exist_ok=True)
    summary = {
        "mode": "recorded-data replay",
        "clinical_validation": False,
        "sources_are_separate_encounters": True,
        "jobs": [],
    }
    with httpx.Client(base_url=args.url + "/v1", headers=headers, timeout=60) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        base = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=20)

        def encounter():
            context = {"patient_id": str(uuid4()), "encounter_id": str(uuid4())}
            request("POST", "/patients", json={"patient_id": context["patient_id"]})
            request("POST", "/encounters", json={**context, "started_at": base.isoformat()})
            return context

        configuration = request("GET", "/configuration")
        context = encounter()
        summary["vitals_encounter_id"] = context["encounter_id"]
        summary["configured_ranges"] = configuration["vital_ranges"]
        times, count = [], 0
        with (args.data / "bidmc/bidmc_01_Numerics.csv").open() as file:
            for row in list(csv.DictReader(file, skipinitialspace=True))[:60]:
                for column, signal, unit in [
                    ("HR", "heart_rate", "bpm"),
                    ("SpO2", "oxygen_saturation", "%"),
                ]:
                    if signal not in configuration["vital_ranges"]:
                        continue
                    value = float(row[column])
                    if not math.isfinite(value) or value <= 0:
                        continue
                    sample = {
                        **context,
                        "source_id": str(uuid4()),
                        "signal": signal,
                        "value": value,
                        "unit": unit,
                        "quality_status": "valid",
                        "observed_at": (
                            base + timedelta(seconds=float(row["Time [s]"]))
                        ).isoformat(),
                    }
                    start = time.perf_counter()
                    request("POST", "/vital-signs/analyze", json=sample)
                    times.append(time.perf_counter() - start)
                    count += 1
                if args.interval > 0:
                    time.sleep(args.interval)
        if not count:
            raise RuntimeError("Configure heart_rate or oxygen_saturation before validation")
        evidence = request("GET", f"/encounters/{context['encounter_id']}/evidence")
        summary["vitals"] = {
            "samples": count,
            "p50_seconds": float(np.percentile(times, 50)),
            "p95_seconds": float(np.percentile(times, 95)),
            "temporal_evidence": sum(e["detector_name"] == "vital-series" for e in evidence),
            "alerts": len(
                request("GET", "/alerts", params={"encounter_id": context["encounter_id"]})
            ),
            "measurement_scope": (
                "HTTP ingestion through persisted alert; not UI or load-test latency"
            ),
        }
        manifest = json.loads((args.data / "manifest.json").read_text())
        selected = [
            s
            for s in manifest["samples"]
            if (s["dataset"] == "primock57" and s["role"] == "patient")
            or (s["dataset"] == "rehab24" and s["split"] == "test")
            or (s["dataset"] == "voiced" and s["split"] == "test")
        ][:18]
        queued = []
        control = encounter()
        summary["synthetic_control_encounter_id"] = control["encounter_id"]
        prescription = {
            **control,
            "medication": "Controle educacional",
            "unit": "mg",
            "route": "oral",
            "administrations_per_day": 1.0,
        }
        request(
            "POST",
            "/prescriptions/analyze",
            json={
                "previous": {
                    **prescription,
                    "source_id": str(uuid4()),
                    "dose": 10.0,
                    "observed_at": base.isoformat(),
                },
                "current": {
                    **prescription,
                    "source_id": str(uuid4()),
                    "dose": 30.0,
                    "observed_at": (base + timedelta(seconds=20)).isoformat(),
                },
            },
        )
        for index, text in enumerate(
            ["Paciente sem queixas.", "Paciente relata falta de ar e cansaço."]
        ):
            job = request(
                "POST",
                "/jobs/text",
                json={
                    **control,
                    "source_id": str(uuid4()),
                    "text": text,
                    "observed_at": (base + timedelta(seconds=index * 20)).isoformat(),
                },
            )
            queued.append(
                (
                    {"dataset": "synthetic_control", "file": f"clinical-evolution-{index + 1}"},
                    job["job_id"],
                )
            )
        for sample in selected:
            context = encounter()
            path = args.data / Path(sample["file"].replace("\\", "/"))
            profile = {
                "rehab24": "squat",
                "voiced": "sustained_vowel",
                "primock57": "consultation",
            }[sample["dataset"]]
            metadata = {
                **context,
                "job_id": str(uuid4()),
                "observed_at": base.isoformat(),
                "kind": "video" if profile == "squat" else "audio",
                "analysis_profile": profile,
                "language": sample.get("language", "pt-BR"),
            }
            job = request(
                "POST",
                "/jobs/media",
                data={"metadata": json.dumps(metadata)},
                files={"file": (path.name, path.read_bytes())},
            )
            queued.append((sample, job["job_id"]))
        deadline = time.monotonic() + args.wait_seconds
        while queued and time.monotonic() < deadline:
            for sample, job_id in queued.copy():
                job = request("GET", f"/jobs/{job_id}")
                if job["status"] in {"completed", "partial", "failed"}:
                    entry = {
                        "file": sample["file"],
                        "job_id": job_id,
                        "encounter_id": job["encounter_id"],
                        "status": job["status"],
                        "errors": job["errors"],
                        "evidence_count": len(job["evidence_ids"]),
                    }
                    if sample["dataset"] == "primock57":
                        reference = reference_words(args.data / sample["reference_transcript"], 120)
                        entry["word_error_rate"] = word_error_rate(
                            reference, words(job["result"].get("transcript", ""))
                        )
                        entry["wer_scope"] = (
                            "Approximate: complete reference utterances ending within 120s; "
                            "partial boundary utterances may affect score; punctuation normalized"
                        )
                        entry["azure_language"] = bool(job["result"].get("language"))
                    summary["jobs"].append(entry)
                    queued.remove((sample, job_id))
                    print(f"{sample['file']}: {job['status']}", flush=True)
            if queued:
                time.sleep(2)
        summary["pending"] = [job_id for _, job_id in queued]
        controls = request("GET", f"/encounters/{control['encounter_id']}/evidence")
        findings = {item["finding_type"] for item in controls}
        alerts = request("GET", "/alerts", params={"encounter_id": control["encounter_id"]})
        for alert in alerts:
            for status in ["acknowledged", "closed"]:
                request(
                    "POST",
                    f"/alerts/{alert['alert_id']}/actions",
                    json={
                        "status": status,
                        "actor": "Validação automatizada",
                        "note": "Controle sintético independente das bases reais",
                    },
                )
        report = request("GET", f"/encounters/{control['encounter_id']}/report")
        (output / "clinical-control.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        summary["clinical_control"] = {
            "evolution": "text.new_symptoms" in findings,
            "prescription": any(f.endswith("dose_change") for f in findings),
            "alerts_reviewed": len(alerts),
            "report_generated": bool(report["evidence"]),
        }
        summary["success"] = not queued and all(j["status"] == "completed" for j in summary["jobs"])
        summary["success"] = summary["success"] and all(summary["clinical_control"].values())
        (output / "integration.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary["vitals"], indent=2))
        if not summary["success"]:
            raise SystemExit("Some real-data jobs did not complete; inspect integration.json")


if __name__ == "__main__":
    main()
