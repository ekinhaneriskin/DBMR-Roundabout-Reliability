#!/usr/bin/env python3
"""Execute one frozen seed-index block of the DBMR campaign on TRUBA."""

from __future__ import annotations

import argparse
import os

import pandas as pd

import dbmr_campaign_v2_common as c


PREFLIGHT = c.META_DIR / "TRUBA_PREFLIGHT_PASS.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--seed-index",
        type=int,
        default=None,
        help="0..99; defaults to SLURM_ARRAY_TASK_ID",
    )
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    seed_index = args.seed_index
    if seed_index is None:
        raw = os.environ.get("SLURM_ARRAY_TASK_ID", "")
        if not raw:
            raise RuntimeError("Need --seed-index or SLURM_ARRAY_TASK_ID")
        seed_index = int(raw)

    if not 0 <= seed_index <= 99:
        raise ValueError(f"seed_index out of range: {seed_index}")

    if not PREFLIGHT.exists():
        raise FileNotFoundError(
            f"TRUBA preflight artifact missing: {PREFLIGHT}"
        )

    pre = c.read_json(PREFLIGHT)
    if not bool(pre.get("preflight_pass")):
        raise RuntimeError("TRUBA preflight is not PASS")

    frozen = c.verify_frozen_inputs()

    # Refuse a stale preflight after configuration changes.
    pre_hash = pre.get("hashes", {})
    current_hash = {
        "matrix_sha256": frozen["matrix_sha256"],
        "config_manifest_sha256": frozen["config_manifest_sha256"],
        "network_manifest_sha256": frozen["network_manifest_sha256"],
        "validation_sha256": frozen["validation_sha256"],
        "model_sha256": frozen["model_sha256"],
        "spec_sha256": frozen["spec_sha256"],
    }
    mismatch = {
        k: {"preflight": pre_hash.get(k), "current": v}
        for k, v in current_hash.items()
        if pre_hash.get(k) != v
    }
    if mismatch:
        raise RuntimeError(f"Configuration changed after preflight: {mismatch}")

    matrix = frozen["matrix"]
    q = matrix[matrix["seed_index"].astype(int).eq(seed_index)].copy()

    if len(q) != 156:
        raise RuntimeError(f"seed_index {seed_index}: expected 156 rows, got {len(q)}")
    if q["seed"].nunique() != 1:
        raise RuntimeError("A seed-index task contains multiple actual seeds")
    if q["scenario"].nunique() != 6 or q["demand_vph"].nunique() != 2:
        raise RuntimeError("A seed-index task does not cover 6 scenarios × 2 demands")
    if q["pair_key"].nunique() != 12:
        raise RuntimeError("A seed-index task must contain exactly 12 CRN pair groups")

    actual_seed = int(q["seed"].iloc[0])

    ctx = c.setup_context(q)
    v = ctx["v"]
    jobs = ctx["jobs"]

    print("DBMR TRUBA SEED ARRAY WORKER V2")
    print("=" * 100)
    print("seed_index:", seed_index)
    print("seed:", actual_seed)
    print("run_n:", len(jobs))
    print("workers:", args.workers)
    print("SLURM_JOB_ID:", os.environ.get("SLURM_JOB_ID", ""))
    print("SLURM_ARRAY_JOB_ID:", os.environ.get("SLURM_ARRAY_JOB_ID", ""))
    print("SLURM_ARRAY_TASK_ID:", os.environ.get("SLURM_ARRAY_TASK_ID", ""))
    print("SUMO:", ctx["current_sumo_version"])

    v.run_jobs(jobs, workers=max(1, int(args.workers)))

    runs = v.load_expected_results(jobs)
    c.assert_summary_path_isolation(runs)

    mech = c.validate_mechanical_rows(runs)
    crn_ok, crn_detail = v.assignment_crn_audit(jobs)
    elig_ok, elig_detail = v.eligibility_formula_audit(runs)
    sys_pair = v.system_paired_effects(runs)

    checks = {
        "run_n_156": len(runs) == 156,
        "all_error_empty": bool(
            runs["error"].fillna("").astype(str).str.strip().eq("").all()
        ),
        "mechanical_rows_pass": bool(mech["pass"]),
        "crn_assignment_integrity_pass": bool(crn_ok),
        "eligibility_formula_pass": bool(elig_ok),
        "system_paired_row_n_144": len(sys_pair) == 144,
        "scenario_n_6": runs["scenario"].nunique() == 6,
        "demand_n_2": runs["demand_vph"].nunique() == 2,
    }
    passed = all(bool(vv) for vv in checks.values())

    task_report = {
        "status": "SEED_TASK_PASS" if passed else "SEED_TASK_FAIL",
        "task_pass": bool(passed),
        "seed_index": int(seed_index),
        "seed": actual_seed,
        "run_n": int(len(runs)),
        "checks": checks,
        "mechanical_detail": mech,
        "eligibility_detail": elig_detail,
        "crn_group_n": int(len(crn_detail)),
        "system_paired_row_n": int(len(sys_pair)),
        "slurm": {
            "job_id": os.environ.get("SLURM_JOB_ID", ""),
            "array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID", ""),
            "array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID", ""),
            "node": os.environ.get("SLURMD_NODENAME", ""),
        },
        "hashes": current_hash,
    }
    out = c.task_report_path(seed_index)
    c.atomic_json(task_report, out)

    print()
    print("TASK CHECKS")
    print("-" * 100)
    for k, vv in checks.items():
        print(f"{k:42s} {vv}")
    print("TASK_REPORT:", out)
    print("SEED_TASK_PASS:", passed)

    if not passed:
        raise RuntimeError(f"seed_index={seed_index} failed campaign task audit")


if __name__ == "__main__":
    main()
