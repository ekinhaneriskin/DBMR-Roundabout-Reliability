#!/usr/bin/env python3
"""
Aggregate and audit the completed DBMR 100×6 TRUBA campaign.

Runs NO new SUMO simulation.

Produces:
- 15,600 run-level results
- 14,400 system paired effects
- streaming per-driver private-benefit summaries for
  ALL / ELIGIBLE / NONELIGIBLE / ESCALATED
- exact p90 ΔTT, winner share, loser share
- final campaign audit

Advantage sign convention:
    delta_TT_adv_s = TT_control - TT_dynamic
Positive => dynamic/adaptive run is individually advantageous in travel-time terms.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

import dbmr_campaign_v2_common as c


RUNS_CSV = c.REPORT_DIR / "CORE_RUN_LEVEL_RESULTS_100x6.csv"
SYSTEM_CSV = c.REPORT_DIR / "SYSTEM_PAIRED_EFFECTS_100x6.csv"
PRIVATE_CSV = c.REPORT_DIR / "PRIVATE_BENEFIT_PAIRED_100x6.csv"
CRN_CSV = c.REPORT_DIR / "CRN_ASSIGNMENT_AUDIT_100x6.csv"
FINAL_AUDIT = c.REPORT_DIR / "FINAL_CAMPAIGN_AUDIT_100x6.json"


def system_paired_effects(runs: pd.DataFrame) -> pd.DataFrame:
    good = runs[runs["error"].fillna("").astype(str).str.strip().eq("")].copy()
    controls = good[good["mode"].astype(str).eq("CONTROL")].copy()
    dynamic = good[good["mode"].astype(str).eq("DYNAMIC")].copy()

    ctrl = controls.set_index(["scenario", "demand_vph", "seed"], drop=False)

    rows = []
    for r in dynamic.itertuples(index=False):
        key = (str(r.scenario), int(r.demand_vph), int(r.seed))
        if key not in ctrl.index:
            continue
        cc = ctrl.loc[key]
        if isinstance(cc, pd.DataFrame):
            if len(cc) != 1:
                continue
            cc = cc.iloc[0]

        def g(obj, name, default=np.nan):
            try:
                return getattr(obj, name)
            except Exception:
                try:
                    return obj[name]
                except Exception:
                    return default

        dyn_collapse_t = g(r, "collapse_time_s")
        ctrl_collapse_t = g(cc, "collapse_time_s")
        collapse_time_delta = (
            float(dyn_collapse_t) - float(ctrl_collapse_t)
            if pd.notna(dyn_collapse_t) and pd.notna(ctrl_collapse_t)
            else np.nan
        )

        rows.append({
            "scenario": str(r.scenario),
            "demand_vph": int(r.demand_vph),
            "seed": int(r.seed),
            "p": float(r.p),
            "T_up_s": float(r.T_up_s),
            "throughput_delta_vph": float(r.throughput_vph) - float(cc["throughput_vph"]),
            "completion_delta_pp": (
                float(r.completion_pct_of_departed)
                - float(cc["completion_pct_of_departed"])
            ),
            "queue_delta_n": float(r.mean_queue_halting) - float(cc["mean_queue_halting"]),
            "tts_delta_vehicle_h": float(r.tts_vehicle_h) - float(cc["tts_vehicle_h"]),
            "collapse_dynamic": int(r.collapse),
            "collapse_control": int(cc["collapse"]),
            "collapse_indicator_delta": int(r.collapse) - int(cc["collapse"]),
            "collapse_time_dynamic_s": dyn_collapse_t,
            "collapse_time_control_s": ctrl_collapse_t,
            "collapse_time_delta_s": collapse_time_delta,
            "collision_delta": float(r.collision_total) - float(cc["collision_total"]),
            "teleport_delta": float(r.teleport_total) - float(cc["teleport_total"]),
            "ttc_lt1_delta_pp": (
                float(r.ttc_lt1_exposure_pct) - float(cc["ttc_lt1_exposure_pct"])
            ),
            "emergency_brake_delta_pp": (
                float(r.emergency_brake_exposure_pct)
                - float(cc["emergency_brake_exposure_pct"])
            ),
        })

    return pd.DataFrame(rows)


def group_private_metrics(dyn: pd.DataFrame, ctrl: pd.DataFrame, group: str) -> dict:
    d = dyn

    if group == "ELIGIBLE":
        d = d[d["eligible"].astype(int).eq(1)]
    elif group == "NONELIGIBLE":
        d = d[d["eligible"].astype(int).eq(0)]
    elif group == "ESCALATED":
        d = d[d["ever_escalated"].astype(int).eq(1)]
    elif group == "ALL":
        pass
    else:
        raise ValueError(group)

    m = d.merge(
        ctrl[[
            "id",
            "arrived",
            "duration_s",
            "delay_s",
            "accumulated_wait_s",
        ]],
        on="id",
        how="inner",
        suffixes=("_dyn", "_ctrl"),
    )

    if m.empty:
        return {
            "group": group,
            "common_departed_n": 0,
            "both_arrived_n": 0,
        }

    both = m[
        m["arrived_dyn"].astype(int).eq(1)
        & m["arrived_ctrl"].astype(int).eq(1)
    ].copy()

    out = {
        "group": group,
        "common_departed_n": int(len(m)),
        "both_arrived_n": int(len(both)),
        "completion_dyn_pct": 100.0 * float(m["arrived_dyn"].mean()),
        "completion_ctrl_pct": 100.0 * float(m["arrived_ctrl"].mean()),
        "completion_gain_pp": 100.0 * float(
            m["arrived_dyn"].mean() - m["arrived_ctrl"].mean()
        ),
        "dyn_only_arrived_n": int(
            (
                m["arrived_dyn"].astype(int).eq(1)
                & m["arrived_ctrl"].astype(int).eq(0)
            ).sum()
        ),
        "ctrl_only_arrived_n": int(
            (
                m["arrived_dyn"].astype(int).eq(0)
                & m["arrived_ctrl"].astype(int).eq(1)
            ).sum()
        ),
    }

    if both.empty:
        out.update({
            "mean_delta_TT_adv_s": np.nan,
            "median_delta_TT_adv_s": np.nan,
            "p90_delta_TT_adv_s": np.nan,
            "winner_share": np.nan,
            "loser_share": np.nan,
            "tie_share": np.nan,
            "mean_delta_wait_adv_s": np.nan,
            "median_delta_wait_adv_s": np.nan,
        })
        return out

    # Frozen sign convention for main paper:
    # positive = control TT - dynamic TT = dynamic individually advantageous.
    delta_tt = (
        pd.to_numeric(both["duration_s_ctrl"], errors="coerce")
        - pd.to_numeric(both["duration_s_dyn"], errors="coerce")
    )
    delta_wait = (
        pd.to_numeric(both["accumulated_wait_s_ctrl"], errors="coerce")
        - pd.to_numeric(both["accumulated_wait_s_dyn"], errors="coerce")
    )

    delta_tt = delta_tt[np.isfinite(delta_tt)]
    delta_wait = delta_wait[np.isfinite(delta_wait)]

    out.update({
        "mean_delta_TT_adv_s": float(delta_tt.mean()) if len(delta_tt) else np.nan,
        "median_delta_TT_adv_s": float(delta_tt.median()) if len(delta_tt) else np.nan,
        "p90_delta_TT_adv_s": float(delta_tt.quantile(0.90)) if len(delta_tt) else np.nan,
        "winner_share": float((delta_tt > 0).mean()) if len(delta_tt) else np.nan,
        "loser_share": float((delta_tt < 0).mean()) if len(delta_tt) else np.nan,
        "tie_share": float((delta_tt == 0).mean()) if len(delta_tt) else np.nan,
        "mean_delta_wait_adv_s": float(delta_wait.mean()) if len(delta_wait) else np.nan,
        "median_delta_wait_adv_s": float(delta_wait.median()) if len(delta_wait) else np.nan,
    })
    return out


def private_benefit_streaming(runs: pd.DataFrame, model) -> pd.DataFrame:
    good = runs[runs["error"].fillna("").astype(str).str.strip().eq("")].copy()

    rows = []
    grouped = good.groupby(["scenario", "demand_vph", "seed"], sort=False)
    expected_groups = 1200

    for (scenario, demand, seed), g in tqdm(
        grouped,
        total=expected_groups,
        desc="Private-benefit pairing",
        unit="pair-group",
    ):
        controls = g[g["mode"].astype(str).eq("CONTROL")]
        dynamics = g[g["mode"].astype(str).eq("DYNAMIC")]

        if len(controls) != 1 or len(dynamics) != 12:
            raise RuntimeError(
                f"Bad pair group {scenario},{demand},{seed}: "
                f"control={len(controls)} dynamic={len(dynamics)}"
            )

        cr = controls.iloc[0]
        ctrl_v = model.load_vehicle_file(str(cr["vehicle_file"]))
        if ctrl_v.empty:
            raise RuntimeError(f"Empty control vehicle file: {cr['vehicle_file']}")

        for _, dr in dynamics.iterrows():
            dyn_v = model.load_vehicle_file(str(dr["vehicle_file"]))
            if dyn_v.empty:
                raise RuntimeError(f"Empty dynamic vehicle file: {dr['vehicle_file']}")

            for group in ("ALL", "ELIGIBLE", "NONELIGIBLE", "ESCALATED"):
                m = group_private_metrics(dyn_v, ctrl_v, group)
                m.update({
                    "scenario": str(scenario),
                    "demand_vph": int(demand),
                    "seed": int(seed),
                    "p": float(dr["p"]),
                    "T_up_s": float(dr["T_up_s"]),
                    "group": group,
                    "dynamic_run_id": str(dr["run_id"]),
                    "control_run_id": str(cr["run_id"]),
                    "interpretation_note": (
                        "Positive delta_TT_adv_s = TT_control - TT_dynamic; "
                        "ELIGIBLE/NONELIGIBLE are susceptibility-defined; "
                        "ESCALATED is post-treatment descriptive only."
                    ),
                })
                rows.append(m)

    return pd.DataFrame(rows)


def main():
    frozen = c.verify_frozen_inputs()

    # Require every seed task to have passed.
    task_reports = []
    for i in range(100):
        p = c.task_report_path(i)
        if not p.exists():
            raise RuntimeError(f"Missing seed task report: {p}")
        d = c.read_json(p)
        task_reports.append(d)
        if not bool(d.get("task_pass")):
            raise RuntimeError(f"Seed task {i} is not PASS")

    ctx = c.setup_context()
    v = ctx["v"]
    model = ctx["model"]
    jobs = ctx["jobs"]

    print("DBMR TRUBA AGGREGATION V2")
    print("=" * 100)
    print("expected jobs:", len(jobs))
    print("task reports:", len(task_reports))

    runs = v.load_expected_results(jobs)
    c.assert_summary_path_isolation(runs)
    runs.to_csv(RUNS_CSV, index=False)

    mech = c.validate_mechanical_rows(runs)
    crn_ok, crn_detail = v.assignment_crn_audit(jobs)
    crn_detail.to_csv(CRN_CSV, index=False)

    system = system_paired_effects(runs)
    system.to_csv(SYSTEM_CSV, index=False)

    private = private_benefit_streaming(runs, model)
    private.to_csv(PRIVATE_CSV, index=False)

    checks = {
        "task_report_n_100": len(task_reports) == 100,
        "all_task_reports_pass": all(bool(x.get("task_pass")) for x in task_reports),
        "run_n_15600": len(runs) == 15600,
        "all_error_empty": bool(
            runs["error"].fillna("").astype(str).str.strip().eq("").all()
        ),
        "mechanical_rows_pass": bool(mech["pass"]),
        "crn_assignment_integrity_pass": bool(crn_ok),
        "system_paired_row_n_14400": len(system) == 14400,
        "private_benefit_row_n_57600": len(private) == 57600,
        "private_groups_exact": set(private["group"].astype(str)) == {
            "ALL", "ELIGIBLE", "NONELIGIBLE", "ESCALATED"
        },
        "scenario_n_6": runs["scenario"].nunique() == 6,
        "seed_n_100": runs["seed"].nunique() == 100,
    }
    passed = all(bool(v) for v in checks.values())

    report = {
        "status": (
            "CORE_15600_CAMPAIGN_COMPLETE"
            if passed else
            "CORE_15600_CAMPAIGN_AUDIT_FAIL"
        ),
        "campaign_pass": bool(passed),
        "checks": checks,
        "mechanical_detail": mech,
        "hashes": {
            "matrix_sha256": frozen["matrix_sha256"],
            "config_manifest_sha256": frozen["config_manifest_sha256"],
            "network_manifest_sha256": frozen["network_manifest_sha256"],
            "validation_sha256": frozen["validation_sha256"],
            "model_sha256": frozen["model_sha256"],
            "spec_sha256": frozen["spec_sha256"],
        },
        "files": {
            "run_level_results": str(RUNS_CSV.relative_to(c.ROOT)),
            "system_paired_effects": str(SYSTEM_CSV.relative_to(c.ROOT)),
            "private_benefit_paired": str(PRIVATE_CSV.relative_to(c.ROOT)),
            "crn_audit": str(CRN_CSV.relative_to(c.ROOT)),
        },
        "private_benefit_sign": (
            "delta_TT_adv_s = TT_control - TT_dynamic; positive = dynamic/adaptive "
            "run individually advantageous in travel-time terms"
        ),
        "interpretation_guardrail": (
            "Absolute L2/L4 braking and jerk remain model-form sensitive. "
            "Collision/teleport counts are simulation robustness indicators, "
            "not real-world crash probabilities."
        ),
    }
    c.atomic_json(report, FINAL_AUDIT)

    print()
    print("FINAL AUDIT")
    print("-" * 100)
    for k, v in checks.items():
        print(f"{k:42s} {v}")
    print("FINAL_CAMPAIGN_PASS:", passed)
    print("OUTPUT:", FINAL_AUDIT)

    if not passed:
        raise RuntimeError("15,600-run campaign aggregation/audit failed")


if __name__ == "__main__":
    main()
