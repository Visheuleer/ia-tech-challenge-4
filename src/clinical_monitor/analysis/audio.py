import math
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from clinical_monitor.application.errors import InvalidAnalysis


def read_audio(path: Path, max_seconds: float = 300.0) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            channels, width = audio.getnchannels(), audio.getsampwidth()
            if channels not in (1, 2) or width != 2 or rate < 8000 or rate > 96000:
                raise InvalidAnalysis("Use mono or stereo PCM WAV, 16 bits, 8-96 kHz")
            if frames / rate > max_seconds or frames / rate < 0.5:
                raise InvalidAnalysis(f"Audio duration must be 0.5 to {max_seconds:g} seconds")
            samples = (
                np.frombuffer(audio.readframes(frames), dtype="<i2").astype(np.float64) / 32768.0
            )
            samples = samples.reshape(-1, channels).mean(axis=1)
    except (wave.Error, EOFError, ValueError) as exc:
        raise InvalidAnalysis("Invalid PCM WAV file") from exc
    factor = math.gcd(rate, 16000)
    return resample_poly(samples, 16000 // factor, rate // factor), 16000


def acoustic_features(samples: np.ndarray, rate: int, pause_seconds: float = 2.0) -> dict:
    frame_size = int(rate * 0.02)
    frames = samples[: len(samples) // frame_size * frame_size].reshape(-1, frame_size)
    energy = np.sqrt(np.mean(frames**2, axis=1))
    threshold = max(0.008, float(np.max(energy)) * 0.1)
    voiced = energy > threshold
    runs = []
    start = None
    for index, active in enumerate(voiced):
        if not active and start is None:
            start = index
        if active and start is not None:
            runs.append((start * 0.02, index * 0.02))
            start = None
    if start is not None:
        runs.append((start * 0.02, len(voiced) * 0.02))
    pauses = [(start, end) for start, end in runs if end - start >= pause_seconds]
    voiced_fraction = float(np.mean(voiced))
    clipping = float(np.mean(np.abs(samples) > 0.99))
    quality = "unusable" if voiced_fraction < 0.05 else "degraded" if clipping > 0.05 else "valid"
    return {
        "duration_seconds": len(samples) / rate,
        "voiced_fraction": voiced_fraction,
        "silence_fraction": 1 - voiced_fraction,
        "rms": float(np.sqrt(np.mean(samples**2))),
        "clipping_fraction": clipping,
        "long_pauses": pauses,
        "quality_status": quality,
        "finding": "not_evaluated"
        if quality == "unusable"
        else ("prolonged_pauses" if pauses else "no_prolonged_pauses"),
    }
