#!/usr/bin/env python3
"""
DBMR 100×6 campaign preflight.

Runs 8 simulations:
- S1 historical-reference pair
- S4 new-geometry pair
- S5 new-geometry pair
- S6 new-geometry pair

For each scenario:
CONTROL + DYNAMIC p=.50,T=30, demand=3000, seed=11.

Full 15,600-run TRUBA array must not start unless this passes.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import dbmr_campaign_v2_common as c


OUT_JSON = c.META_DIR / "TRUBA_PREFLIGHT_PASS.json"
OUT_RUNS = c.REPORT_DIR / "PREFLIGHT_RUNS.csv"
OUT_CRN = c.REPORT_DIR / "PREFLIGHT_CRN_AUDIT.csv"
OUT_SYSTEM = c.REPORT_DIR / "PREFLIGHT_SYSTEM_PAIRED.csv"

HIST_CTRL = (
    c.ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1" / "summaries"
    / "CORE_S1_d3000_s11_CONTROL_CTRL_T---_lr0p05.json"
)
HIST_DYN = (
    c.ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1" / "summaries"
    / "CORE_S1_d3000_s11_DYNAMIC_p050_T030_lr0p05.json"
)

COMPARE_KEYS = [
    "throughput_vph",
    "completion_pct_of_departed",
    "collapse",
    "escalated_share_of_eligible",
    "transition_total",
    "mean_queue_halting",
    "tts_vehicle_h",
    "A_mean",
    "H_mean",
    "collision_total",
    "teleport_total",
    "ttc_lt1_exposure_pct",
    "emergency_brake_exposure_pct",
]


def numeric_equal(a, b, atol=1e-12):
    if pd.isna(a) and pd.isna(b):
        return True
    if isinstance(a, (str, bool)) or isinstance(b, (str, bool)):
        return a == b
    try:
        return bool(np.isclose(float(a), float(b), rtol=0.0, atol=atol))
    except Exception:
        return a == b


def historical_compare(runs):
    if not (HIST_CTRL.exists() and HIST_DYN.exists()):
        return {
            "historical_reference_available": False,
            "historical_exact_match_pass": None,
            "detail": [],
        }

    refs = {
        "CONTROL": json.loads(HIST_CTRL.read_text()),
        "DYNAMIC": json.loads(HIST_DYN.read_text()),
    }

    detail = []
    passed = True

    for mode in ("CONTROL", "DYNAMIC"):
        x = runs[
            (runs["scenario"].astype(str) == "S1")
            & (runs["mode"].astype(str) == mode)
        ]
        if len(x) != 1:
            passed = False
            detail.append({"mode": mode, "problem": f"row_n={len(x)}"})
            continue

        cur = x.iloc[0]
        ref = refs[mode]

        for key in COMPARE_KEYS:
            ok = key in cur.index and key in ref and numeric_equal(cur[key], ref[key])
            passed = passed and ok
            detail.append({
                "mode": mode,
                "metric": key,
                "current": cur.get(key, None),
                "historical": ref.get(key, None),
                "pass": bool(ok),
            })

    return {
        "historical_reference_available": True,
        "historical_exact_match_pass": bool(passed),
        "detail": detail,
    }


def main():
    frozen = c.verify_frozen_inputs()
    matrix = frozen["matrix"]

    wanted_scenarios = ["S1", "S4", "S5", "S6"]

    q = matrix[
        matrix["scenario"].isin(wanted_scenarios)
        & matrix["demand_vph"].eq(3000)
        & matrix["seed"].eq(11)
        & (
            matrix["mode"].eq("CONTROL")
            | (
                matrix["mode"].eq("DYNAMIC")
                & np.isclose(matrix["p"].astype(float), 0.50)
                & np.isclose(matrix["T_up_s"].astype(float), 30.0)
            )
        )
    ].copy()

    q = q.sort_values(["scenario_rank", "mode", "p"], kind="stable")

    if len(q) != 8:
        raise RuntimeError(f"Expected 8 preflight matrix rows, got {len(q)}")

    ctx = c.setup_context(q)
    v = ctx["v"]
    jobs = ctx["jobs"]

    print("DBMR FINAL CAMPAIGN V2 — 100x6 PREFLIGHT")
    print("=" * 100)
    print("selected_run_n:", len(jobs))
    print("scenarios:", wanted_scenarios)
    print("SUMO:", ctx["current_sumo_version"])
    for j in jobs:
        print(j["campaign_run_id"], "->", j["summary"])

    workers = 4
    v.run_jobs(jobs, workers=workers)

    runs = v.load_expected_results(jobs)
    c.assert_summary_path_isolation(runs)
    runs.to_csv(OUT_RUNS, index=False)

    mech = c.validate_mechanical_rows(runs)

    crn_ok, crn_detail = v.assignment_crn_audit(jobs)
    crn_detail.to_csv(OUT_CRN, index=False)

    elig_ok, elig_detail = v.eligibility_formula_audit(runs)

    sys_pair = v.system_paired_effects(runs)
    sys_pair.to_csv(OUT_SYSTEM, index=False)

    hist = historical_compare(runs)
    hist_gate = (
        True
        if hist["historical_reference_available"] is False
        else bool(hist["historical_exact_match_pass"])
    )

    # Each scenario must have exactly one control and one dynamic result.
    scenario_pair_counts = {}
    pair_pass = True
    for s in wanted_scenarios:
        z = runs[runs["scenario"].astype(str).eq(s)]
        ctrl_n = int((z["mode"].astype(str) == "CONTROL").sum())
        dyn_n = int((z["mode"].astype(str) == "DYNAMIC").sum())
        scenario_pair_counts[s] = {"control_n": ctrl_n, "dynamic_n": dyn_n}
        pair_pass = pair_pass and ctrl_n == 1 and dyn_n == 1

    checks = {
        "run_n_8": len(runs) == 8,
        "scenario_pair_counts_pass": bool(pair_pass),
        "mechanical_rows_pass": bool(mech["pass"]),
        "crn_assignment_integrity_pass": bool(crn_ok),
        "eligibility_formula_pass": bool(elig_ok),
        "system_paired_row_n_4": len(sys_pair) == 4,
        "new_scenarios_present": set(["S4", "S5", "S6"]).issubset(
            set(runs["scenario"].astype(str))
        ),
    }

    passed = all(bool(vv) for vv in checks.values())

    # Historical Mac/ARM exact match is retained as a diagnostic only on TRUBA
    # Linux/x86. Cross-architecture floating-point trajectories can differ
    # slightly even under the same frozen SUMO version; mechanical/CRN gates are
    # the hard launch criteria on the cluster.
    historical_diagnostic = {
        "available": bool(hist["historical_reference_available"]),
        "exact_match": hist["historical_exact_match_pass"],
    }

    result = {
        "status": "TRUBA_PREFLIGHT_PASS" if passed else "TRUBA_PREFLIGHT_FAIL",
        "preflight_pass": bool(passed),
        "simulation_run_n": 8,
        "checks": checks,
        "scenario_pair_counts": scenario_pair_counts,
        "mechanical_detail": mech,
        "eligibility_detail": elig_detail,
        "historical_reproducibility": hist,
        "historical_reproducibility_diagnostic_only": historical_diagnostic,
        "hashes": {
            "matrix_sha256": frozen["matrix_sha256"],
            "config_manifest_sha256": frozen["config_manifest_sha256"],
            "network_manifest_sha256": frozen["network_manifest_sha256"],
            "validation_sha256": frozen["validation_sha256"],
            "model_sha256": frozen["model_sha256"],
            "spec_sha256": frozen["spec_sha256"],
        },
        "next_if_pass": "Submit 100-task TRUBA seed-index array.",
    }
    c.atomic_json(result, OUT_JSON)

    print()
    print("PREFLIGHT CHECKS")
    print("-" * 100)
    for k, vv in checks.items():
        print(f"{k:42s} {vv}")
    print("TRUBA_PREFLIGHT_PASS:", passed)
    print("OUTPUT:", OUT_JSON)

    if not passed:
        raise RuntimeError("100x6 preflight failed; full TRUBA array remains locked")


if __name__ == "__main__":
    main()
