import argparse
import hashlib
import json
from pathlib import Path

import httpx

from clinical_monitor.tools.dataset_downloads import download

BASE = "https://physionet.org/files/bidmc/1.0.0/"


def prepare(root: Path):
    directory = root / "bidmc"
    directory.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        sums = download(client, BASE + "SHA256SUMS.txt", directory / "SHA256SUMS.txt")
        download(client, BASE + "LICENSE", directory / "LICENSE")
        name = "bidmc_csv/bidmc_01_Numerics.csv"
        path = download(client, BASE + name, directory / "bidmc_01_Numerics.csv")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = next(
        line.split()[0]
        for line in sums.read_text().splitlines()
        if line.split()[-1].lstrip("./") == name
    )
    if digest != expected:
        raise ValueError("BIDMC checksum mismatch")
    provenance = {
        "dataset": "BIDMC PPG and Respiration 1.0.0",
        "source": BASE + name,
        "sha256": digest,
        "subject": "01",
        "sample_interval_seconds": 1,
        "license": "Open Data Commons Attribution License v1.0",
        "citation": (
            "Pimentel et al., IEEE TBME 64(8), 1914-1923, 2016; DOI 10.1109/TBME.2016.2613124"
        ),
        "scope": "Recorded hospital HR and SpO2; no adjudicated anomaly labels or blood pressure",
    }
    (directory / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/real"))
    args = parser.parse_args()
    print(prepare(args.output))


if __name__ == "__main__":
    main()
