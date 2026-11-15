import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from clinical_monitor.analysis.audio import read_audio
from clinical_monitor.analysis.screening import MODEL_ROOT, predict, squat_features, voice_features
from clinical_monitor.analysis.video import analyze_video
from clinical_monitor.application.errors import InvalidAnalysis
from clinical_monitor.core.config import Settings


def fit_model(rows: list[dict], scope: str, digest: str, source: dict | None = None) -> dict:
    training = [r for r in rows if r["split"] == "development" and "features" in r]
    names = sorted(training[0]["features"])
    x = np.asarray([[r["features"][name] for name in names] for r in training])
    y = np.asarray([r["target"] for r in training])
    if len(set(y)) != 2:
        raise ValueError("Both classes are required for training")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    x = (x - mean) / scale

    def objective(parameters):
        weights, bias = parameters[:-1], parameters[-1]
        logits = x @ weights + bias
        loss = np.mean(np.logaddexp(0, logits) - y * logits) + 0.1 * weights @ weights / 2
        error = expit(logits) - y
        gradient = np.r_[x.T @ error / len(y) + 0.1 * weights, error.mean()]
        return loss, gradient

    fitted = minimize(objective, np.zeros(len(names) + 1), jac=True, method="BFGS")
    if not fitted.success:
        raise RuntimeError(f"Model fitting failed: {fitted.message}")
    model = {
        "features": names,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "coefficients": fitted.x[:-1].tolist(),
        "intercept": float(fitted.x[-1]),
        "threshold": 0.5,
        "regularization": 0.1,
        "scope": scope,
        "training_participants": sorted({r["participant"] for r in training}),
        "manifest_sha256": digest,
        "training_data_provenance": source or {},
        "method": "Logistic regression; development-only standardization; fixed threshold",
    }
    fingerprint = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()
    return {"version": "1.0.0+" + fingerprint[:12], **model}


def metrics(rows: list[dict]) -> dict:
    evaluated = [r for r in rows if "prediction" in r]
    tp = sum(r["target"] == 1 and r["prediction"] for r in evaluated)
    tn = sum(r["target"] == 0 and not r["prediction"] for r in evaluated)
    fp = sum(r["target"] == 0 and r["prediction"] for r in evaluated)
    fn = sum(r["target"] == 1 and not r["prediction"] for r in evaluated)
    return {
        "total": len(rows),
        "evaluated": len(evaluated),
        "abstained": len(rows) - len(evaluated),
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "accuracy": (tp + tn) / len(evaluated) if evaluated else None,
        "sensitivity": tp / (tp + fn) if tp + fn else None,
        "specificity": tn / (tn + fp) if tn + fp else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/real"))
    args = parser.parse_args()
    manifest = args.data / "manifest.json"
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    samples = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
    settings = Settings(_env_file=None)
    output = args.data / "evaluation"
    output.mkdir(exist_ok=True)
    MODEL_ROOT.mkdir(exist_ok=True)
    evaluation = {"manifest_sha256": digest, "clinical_validation": False, "profiles": {}}
    for dataset, profile, positive, scope in [
        (
            "voiced",
            "sustained_vowel",
            "pathological",
            "VOICED sustained vowel /a/ pilot; pathological vs healthy voice, not diagnosis",
        ),
        (
            "rehab24",
            "squat",
            "incorrect",
            "REHAB24 exercise 6 single repetition, half-profile, full body; educational pilot",
        ),
    ]:
        rows, participants = [], {}
        for sample in [s for s in samples if s["dataset"] == dataset]:
            participant = sample["participant"]
            if participant in participants and participants[participant] != sample["split"]:
                raise ValueError("Participant leaks across dataset splits")
            participants[participant] = sample["split"]
            path = args.data / Path(sample["file"].replace("\\", "/"))
            if hashlib.sha256(path.read_bytes()).hexdigest() != sample["sha256"]:
                raise ValueError("Dataset checksum mismatch")
            row = {k: sample[k] for k in ["file", "participant", "split", "label", "sha256"]}
            row["target"] = int(sample["label"] == positive)
            try:
                if dataset == "voiced":
                    audio, rate = read_audio(path, 300)
                    row["features"] = voice_features(audio, rate)
                else:
                    directory = output / path.stem
                    directory.mkdir(exist_ok=True)
                    cache = directory / "pose.json"
                    report = json.loads(cache.read_text()) if cache.exists() else {}
                    if report.get("pose_pipeline_version") != "2.0.0":
                        report = analyze_video(path, settings, directory)
                        cache.write_text(json.dumps(report), encoding="utf-8")
                    row["features"] = squat_features(report)
            except InvalidAnalysis as exc:
                row["abstention"] = str(exc)
            rows.append(row)
            print(f"{dataset}: {len(rows)}", flush=True)
        catalog = json.loads(Path("datasets/catalog.json").read_text(encoding="utf-8"))
        model = fit_model(rows, scope, digest, catalog[dataset])
        (MODEL_ROOT / f"{profile}.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
        for row in rows:
            if "features" in row:
                prediction = predict(profile, row["features"])
                row.update(prediction=prediction["flagged"], score=prediction["score"])
        evaluation["profiles"][profile] = {
            "scope": scope,
            "samples": rows,
            "metrics": {
                split: metrics([r for r in rows if r["split"] == split])
                for split in ["development", "validation", "test"]
            },
        }
    (output / "results.json").write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    print(json.dumps({k: v["metrics"] for k, v in evaluation["profiles"].items()}, indent=2))


if __name__ == "__main__":
    main()
