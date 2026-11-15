import argparse
import csv
import hashlib
import json
import re
import wave
import zipfile
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import httpx
import numpy as np

from clinical_monitor.tools.dataset_downloads import RangeReader, download

REHAB = "https://zenodo.org/records/13305826/files/"
VOICED = "https://physionet.org/files/voiced/1.0.0/"
REVISION = "cd2ac707ad03cb4d2531f4ec6b90c659bf4357c5"
PRIMOCK = f"https://raw.githubusercontent.com/babylonhealth/primock57/{REVISION}/"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_pcm(path, samples, rate):
    if np.max(np.abs(samples)) > 1:
        raise ValueError("Audio exceeds PCM range; no silent normalization permitted")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes((samples * 32767).astype("<i2").tobytes())


def video_clip(source, destination, first, last):
    if destination.exists():
        previous = cv2.VideoCapture(str(destination))
        complete = (
            previous.isOpened()
            and int(previous.get(cv2.CAP_PROP_FRAME_COUNT)) == last - first + 1
            and abs(previous.get(cv2.CAP_PROP_FPS) - 30) < 0.01
        )
        previous.release()
        if complete:
            return
    capture = cv2.VideoCapture(str(source))
    writer = None
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or abs(fps - 30) > 0.01:
            raise ValueError("Expected readable source video at 30 FPS")
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = cv2.VideoWriter(
            str(destination), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
        )
        if not writer.isOpened():
            raise ValueError("Video encoder unavailable")
        capture.set(cv2.CAP_PROP_POS_FRAMES, first)
        for _ in range(last - first + 1):
            ok, frame = capture.read()
            if not ok:
                raise ValueError("Incomplete video segment")
            writer.write(frame)
    finally:
        capture.release()
        if writer is not None:
            writer.release()


def prepare_rehab(client, root):
    directory = root / "rehab24"
    directory.mkdir(parents=True, exist_ok=True)
    annotation = download(client, REHAB + "Segmentation.csv", directory / "Segmentation.csv")
    download(client, REHAB + "Segmentation.txt", directory / "Segmentation.txt")
    with annotation.open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source, delimiter=";"))
    selected = []
    for person, split in [
        ("3", "development"),
        ("4", "development"),
        ("5", "validation"),
        ("9", "test"),
    ]:
        for label in ["1", "0"]:
            candidates = [
                r
                for r in rows
                if r["person_id"] == person
                and r["exercise_id"] == "6"
                and r["correctness"] == label
                and r["cam17_orientation"] == "half-profile"
                and r["extra_person_in_cam17"] == "0"
                and r["lights_on"] == "1"
            ]
            if len(candidates) < 2:
                raise ValueError("Insufficient annotated repetitions")
            selected.extend([{**row, "split": split} for row in candidates[:2]])
    metadata = client.get("https://zenodo.org/api/records/13305826")
    metadata.raise_for_status()
    archive = next(f for f in metadata.json()["files"] if f["key"] == "videos.zip")
    remote = RangeReader(client, archive["links"]["self"], archive["size"])
    entries = []
    with zipfile.ZipFile(remote) as bundle:
        for row in selected:
            member = f"Ex6/{row['video_id']}-Camera17-30fps.mp4"
            source = directory / Path(member).name
            if not source.exists():
                source.write_bytes(bundle.read(member))
            if zlib.crc32(source.read_bytes()) != bundle.getinfo(member).CRC:
                raise ValueError("Cached source video checksum mismatch")
            name = f"{row['video_id']}-rep{int(row['repetition_number']):02d}.mp4"
            path = directory / name
            first, last = int(row["first_frame"]), int(row["last_frame"])
            video_clip(source, path, first, last)
            entries.append(
                {
                    "dataset": "rehab24",
                    "file": str(path.relative_to(root)),
                    "source_member": member,
                    "source_sha256": digest(source),
                    "sha256": digest(path),
                    "participant": row["person_id"],
                    "split": row["split"],
                    "label": "correct" if row["correctness"] == "1" else "incorrect",
                    "first_frame": first,
                    "last_frame": last,
                    "frame_index_policy": "source indices passed to OpenCV; inclusive end",
                    "duration_seconds": (last - first + 1) / 30,
                    "annotation": row,
                    "transformation": "Temporal crop; mp4v re-encoding",
                }
            )
            print(f"Video ready: {name}", flush=True)
    return entries


def prepare_voiced(client, root, per_class):
    directory = root / "voiced"
    directory.mkdir(parents=True, exist_ok=True)
    records = download(client, VOICED + "RECORDS", directory / "RECORDS")
    checksums = download(client, VOICED + "SHA256SUMS.txt", directory / "SHA256SUMS.txt")
    expected = {
        line.split()[1].lstrip("*"): line.split()[0]
        for line in checksums.read_text().splitlines()
        if line.strip()
    }

    def header(record):
        path = download(client, VOICED + record + ".hea", directory / (record + ".hea"))
        if digest(path) != expected[path.name]:
            raise ValueError("Source header checksum mismatch")
        return record, path.read_text()

    with ThreadPoolExecutor(max_workers=4) as executor:
        headers = list(executor.map(header, records.read_text().split()))
    counts, entries = Counter(), []
    for record, text in headers:
        diagnosis = re.search(r"<diagnoses>:\s*(.*?)\s*<medications>", text).group(1)
        label = "healthy" if diagnosis.lower() == "healthy" else "pathological"
        if counts[label] >= per_class:
            continue
        fields, signal = text.splitlines()[0].split(), text.splitlines()[1].split()
        if fields[1] != "1" or signal[1] != "32":
            raise ValueError("Unexpected WFDB encoding")
        rate, frames = int(fields[2]), int(fields[3])
        gain_match = re.fullmatch(r"([\d.]+)\((-?\d+)\)/NU", signal[2])
        if gain_match is None:
            raise ValueError("Unexpected WFDB gain/baseline")
        gain, baseline = float(gain_match[1]), int(gain_match[2])
        raw = download(client, VOICED + record + ".dat", directory / (record + ".dat"))
        if digest(raw) != expected[raw.name]:
            raise ValueError("Source signal checksum mismatch")
        samples = np.frombuffer(raw.read_bytes(), dtype="<i4").astype(np.float64)
        if len(samples) != frames:
            raise ValueError("Signal length mismatch")
        path = directory / (record + ".wav")
        save_pcm(path, (samples - baseline) / gain, rate)
        index = counts[label]
        split = (
            "development"
            if index < per_class * 0.6
            else ("validation" if index < per_class * 0.8 else "test")
        )
        counts[label] += 1
        entries.append(
            {
                "dataset": "voiced",
                "file": str(path.relative_to(root)),
                "record": record,
                "participant": record,
                "label": label,
                "source_diagnosis": diagnosis,
                "split": split,
                "source_sha256": digest(raw),
                "sha256": digest(path),
                "duration_seconds": frames / rate,
                "sample_rate": rate,
                "transformation": (
                    "WFDB int32 calibrated by gain/baseline to PCM16; no normalization"
                ),
                "task": "sustained_vowel_not_consultation",
            }
        )
    if counts != {"healthy": per_class, "pathological": per_class}:
        raise ValueError("Requested class balance unavailable")
    print(f"Clinical voices ready: {dict(counts)}", flush=True)
    return entries


def prepare_consultations(client, root):
    directory = root / "primock57"
    directory.mkdir(parents=True, exist_ok=True)
    download(client, PRIMOCK + "LICENSE.md", directory / "LICENSE.md")
    entries = []
    for number in [4, 6]:
        identifier = f"day1_consultation{number:02d}"
        download(client, PRIMOCK + f"notes/{identifier}.json", directory / f"{identifier}.json")
        for role in ["patient", "doctor"]:
            name = f"{identifier}_{role}"
            download(
                client, PRIMOCK + f"transcripts/{name}.TextGrid", directory / f"{name}.TextGrid"
            )
            url = f"https://media.githubusercontent.com/media/babylonhealth/primock57/{REVISION}/audio/{name}.wav"
            source = download(client, url, directory / f"{name}.wav")
            pointer = client.get(PRIMOCK + f"audio/{name}.wav")
            pointer.raise_for_status()
            expected = re.search(r"oid sha256:([a-f0-9]+)", pointer.text).group(1)
            if digest(source) != expected:
                raise ValueError("Git LFS checksum mismatch")
            path = directory / f"{name}-first120s.wav"
            with wave.open(str(source), "rb") as audio:
                if audio.getsampwidth() != 2 or audio.getnchannels() != 1:
                    raise ValueError("Unexpected consultation audio format")
                rate = audio.getframerate()
                content = audio.readframes(min(audio.getnframes(), rate * 120))
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(rate)
                audio.writeframes(content)
            entries.append(
                {
                    "dataset": "primock57",
                    "file": str(path.relative_to(root)),
                    "consultation": identifier,
                    "role": role,
                    "language": "en-GB",
                    "split": "integration_only",
                    "source_sha256": expected,
                    "sha256": digest(path),
                    "start_seconds": 0,
                    "duration_seconds": len(content) / (2 * rate),
                    "reference_transcript": f"primock57/{name}.TextGrid",
                    "reference_scope": "Full source; evaluate only utterances within clip",
                    "task": "human_role_play_consultation",
                    "transformation": "First 120s; no resynthesis",
                }
            )
            print(f"Consultation ready: {name}", flush=True)
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/real"))
    parser.add_argument("--voices-per-class", type=int, default=32, choices=range(1, 59))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        entries = prepare_rehab(client, args.output)
        entries += prepare_voiced(client, args.output, args.voices_per_class)
        entries += prepare_consultations(client, args.output)
    manifest = {
        "catalog": "datasets/catalog.json",
        "status": "prepared_not_evaluated",
        "samples": entries,
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Prepared {len(entries)} samples in {args.output}")


if __name__ == "__main__":
    main()
