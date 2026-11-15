import json
from pathlib import Path

import numpy as np
from scipy.signal import correlate
from scipy.special import expit

from clinical_monitor.application.errors import InvalidAnalysis

MODEL_ROOT = Path(__file__).parent / "models"


def voice_features(samples: np.ndarray, rate: int) -> dict[str, float]:
    size = round(rate * 0.04)
    frames = np.asarray([samples[i : i + size] for i in range(0, len(samples) - size, size // 2)])
    if len(frames) < 10:
        raise InvalidAnalysis("Insufficient sustained voice")
    energy = np.sqrt(np.mean(frames**2, axis=1))
    frames = frames[energy > max(0.008, float(energy.max()) * 0.1)]
    if len(frames) < 10:
        raise InvalidAnalysis("Insufficient audible sustained voice")
    pitches, periodicity, flatness, centroids, rms = [], [], [], [], []
    for frame in frames:
        centered = frame - frame.mean()
        correlation = correlate(centered, centered, mode="full", method="fft")[size - 1 :]
        low, high = int(rate / 400), int(rate / 65)
        lag = low + int(np.argmax(correlation[low:high]))
        pitches.append(rate / lag)
        periodicity.append(float(correlation[lag] / max(correlation[0], 1e-12)))
        spectrum = abs(np.fft.rfft(centered * np.hanning(size))) ** 2 + 1e-12
        flatness.append(float(np.exp(np.mean(np.log(spectrum))) / np.mean(spectrum)))
        frequencies = np.fft.rfftfreq(size, 1 / rate)
        centroids.append(float(np.sum(frequencies * spectrum) / spectrum.sum() / rate))
        rms.append(float(np.sqrt(np.mean(frame**2))))
    return {
        "pitch_cv": float(np.std(pitches) / np.mean(pitches)),
        "periodicity_mean": float(np.mean(periodicity)),
        "periodicity_std": float(np.std(periodicity)),
        "spectral_flatness": float(np.mean(flatness)),
        "spectral_centroid": float(np.mean(centroids)),
        "energy_cv": float(np.std(rms) / np.mean(rms)),
    }


def squat_features(report: dict) -> dict[str, float]:
    frames = [frame for frame in report["frames"] if frame["quality"] == "valid"]
    if len(frames) < 4 or report["valid_fraction"] < 0.8:
        raise InvalidAnalysis("Squat requires at least four reliable poses and 80% coverage")
    knees = [(f["left_knee_degrees"] + f["right_knee_degrees"]) / 2 for f in frames]
    tilt = [f["trunk_tilt_degrees"] for f in frames]
    asymmetry = [f["knee_asymmetry_degrees"] for f in frames]
    if np.ptp(knees) < 15:
        raise InvalidAnalysis("No complete squat movement identified")
    return {
        "knee_min": float(np.min(knees)),
        "knee_range": float(np.ptp(knees)),
        "tilt_max": float(np.max(tilt)),
        "tilt_range": float(np.ptp(tilt)),
        "asymmetry_mean": float(np.mean(asymmetry)),
        "asymmetry_max": float(np.max(asymmetry)),
    }


def predict(profile: str, features: dict[str, float]) -> dict:
    path = MODEL_ROOT / f"{profile}.json"
    if not path.is_file():
        raise InvalidAnalysis("Screening model missing; run the real-data evaluation tool")
    model = json.loads(path.read_text(encoding="utf-8"))
    vector = np.asarray([features[name] for name in model["features"]])
    standardized = (vector - model["mean"]) / model["scale"]
    score = float(expit(standardized @ model["coefficients"] + model["intercept"]))
    return {
        "profile": profile,
        "model_version": model["version"],
        "score": score,
        "threshold": model["threshold"],
        "flagged": score >= model["threshold"],
        "features": features,
        "scope": model["scope"],
        "experimental": True,
        "score_is_calibrated_probability": False,
    }
