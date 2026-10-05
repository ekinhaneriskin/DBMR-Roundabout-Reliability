#!/usr/bin/env python3
"""Lightweight integrity check for the public DBMR release."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXPECTED_ROWS = {
    "CORE_RUN_LEVEL_RESULTS_100x6.csv": 15600,
    "SYSTEM_PAIRED_EFFECTS_100x6.csv": 14400,
    "PRIVATE_BENEFIT_PAIRED_100x6.csv": 57600,
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def main() -> None:
    for name, expected in EXPECTED_ROWS.items():
        actual = csv_rows(ROOT / "results" / name)
        if actual != expected:
            raise RuntimeError(f"{name}: {actual} rows; expected {expected}")

    audit = json.loads((ROOT / "results" / "FINAL_CAMPAIGN_AUDIT_100x6.json").read_text())
    if not audit.get("campaign_pass"):
        raise RuntimeError("Final campaign audit is not PASS")
    if not all(audit.get("checks", {}).values()):
        raise RuntimeError("At least one final campaign audit check failed")

    seeds = json.loads((ROOT / "config" / "FINAL_100_SEEDS_USED_V2_1.json").read_text())["seeds"]
    if len(seeds) != 100 or len(set(seeds)) != 100:
        raise RuntimeError("Final seed list is not exactly 100 unique values")
    if not all(1 <= int(x) <= 2_147_483_647 for x in seeds):
        raise RuntimeError("Final seed list contains a value outside the SUMO seed domain")

    frozen = ROOT / "provenance" / "frozen_campaign_sources.tar.gz"
    with tarfile.open(frozen, "r:gz") as tf:
        members = {
            "dbmr_final_model.py": audit["hashes"]["model_sha256"],
            "dbmr_final_validation.py": audit["hashes"]["validation_sha256"],
        }
        for basename, expected in members.items():
            member = next((m for m in tf.getmembers() if Path(m.name).name == basename), None)
            if member is None:
                raise RuntimeError(f"Missing frozen source: {basename}")
            data = tf.extractfile(member).read()
            if sha256_bytes(data) != expected:
                raise RuntimeError(f"Frozen source hash mismatch: {basename}")

    print("REPO_INTEGRITY_CHECK_PASS: True")


if __name__ == "__main__":
    main()
