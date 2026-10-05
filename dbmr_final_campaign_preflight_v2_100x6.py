#!/usr/bin/env python3
"""Run the eight-simulation preflight for the 100-seed x 6-geometry campaign."""

from __future__ import annotations

import numpy as np

import dbmr_campaign_v2_common as c

OUT_JSON = c.META_DIR / "TRUBA_PREFLIGHT_PASS.json"
OUT_RUNS = c.REPORT_DIR / "PREFLIGHT_RUNS.csv"
OUT_CRN = c.REPORT_DIR / "PREFLIGHT_CRN_AUDIT.csv"
OUT_SYSTEM = c.REPORT_DIR / "PREFLIGHT_SYSTEM_PAIRED.csv"


def main() -> None:
    frozen = c.verify_frozen_inputs()
    matrix = frozen["matrix"]
    scenarios = ["S1", "S4", "S5", "S6"]

    selected = matrix[
        matrix["scenario"].isin(scenarios)
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
    selected = selected.sort_values(["scenario_rank", "mode", "p"], kind="stable")

    if len(selected) != 8:
        raise RuntimeError(f"Expected 8 preflight rows, got {len(selected)}")

    ctx = c.setup_context(selected)
    validation = ctx["v"]
    jobs = ctx["jobs"]

    validation.run_jobs(jobs, workers=4)
    runs = validation.load_expected_results(jobs)
    c.assert_summary_path_isolation(runs)
    runs.to_csv(OUT_RUNS, index=False)

    mechanical = c.validate_mechanical_rows(runs)
    crn_ok, crn_detail = validation.assignment_crn_audit(jobs)
    crn_detail.to_csv(OUT_CRN, index=False)
    eligibility_ok, eligibility_detail = validation.eligibility_formula_audit(runs)

    system_pairs = validation.system_paired_effects(runs)
    system_pairs.to_csv(OUT_SYSTEM, index=False)

    pair_counts = {}
    pair_count_ok = True
    for scenario in scenarios:
        rows = runs[runs["scenario"].astype(str).eq(scenario)]
        control_n = int((rows["mode"].astype(str) == "CONTROL").sum())
        dynamic_n = int((rows["mode"].astype(str) == "DYNAMIC").sum())
        pair_counts[scenario] = {"control_n": control_n, "dynamic_n": dynamic_n}
        pair_count_ok = pair_count_ok and control_n == 1 and dynamic_n == 1

    checks = {
        "run_n_8": len(runs) == 8,
        "scenario_pair_counts_pass": bool(pair_count_ok),
        "mechanical_rows_pass": bool(mechanical["pass"]),
        "crn_assignment_integrity_pass": bool(crn_ok),
        "eligibility_formula_pass": bool(eligibility_ok),
        "system_paired_row_n_4": len(system_pairs) == 4,
        "geometry_coverage_pass": set(["S4", "S5", "S6"]).issubset(
            set(runs["scenario"].astype(str))
        ),
    }
    passed = all(bool(value) for value in checks.values())

    result = {
        "status": "TRUBA_PREFLIGHT_PASS" if passed else "TRUBA_PREFLIGHT_FAIL",
        "preflight_pass": bool(passed),
        "simulation_run_n": 8,
        "checks": checks,
        "scenario_pair_counts": pair_counts,
        "mechanical_detail": mechanical,
        "eligibility_detail": eligibility_detail,
        "hashes": {
            "matrix_sha256": frozen["matrix_sha256"],
            "config_manifest_sha256": frozen["config_manifest_sha256"],
            "network_manifest_sha256": frozen["network_manifest_sha256"],
            "validation_sha256": frozen["validation_sha256"],
            "model_sha256": frozen["model_sha256"],
            "spec_sha256": frozen["spec_sha256"],
        },
    }
    c.atomic_json(result, OUT_JSON)

    print("DBMR 100x6 PREFLIGHT")
    for key, value in checks.items():
        print(f"{key:42s} {value}")
    print("TRUBA_PREFLIGHT_PASS:", passed)

    if not passed:
        raise RuntimeError("100x6 preflight failed")


if __name__ == "__main__":
    main()
