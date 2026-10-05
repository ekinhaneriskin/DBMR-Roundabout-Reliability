#!/usr/bin/env python3
"""Build and audit the frozen 100-seed x 6-geometry DBMR campaign configuration.

The audit creates the six networks, the 15,600-row campaign matrix, and the
configuration manifests without running the scientific simulation campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

import dbmr_six_scenario_networks_v2 as geom


ROOT = Path(__file__).resolve().parent

SPEC = ROOT / "config" / "DBMR_FINAL_SPEC.json"
SEED_SOURCE = ROOT / "config" / "FINAL_100_SEEDS_USED_V2_1.json"
MODEL_SOURCE = ROOT / "dbmr_final_model.py"
VALIDATION_SOURCE = ROOT / "dbmr_final_validation.py"

OUT = ROOT / "outputs" / "DBMR_FINAL_CAMPAIGN_CONFIG_V2_100x6"
OUT.mkdir(parents=True, exist_ok=True)

NETWORK_DIR = OUT / "networks"
REFERENCE_NETWORK_DIR = OUT / "_validated_builder_reference"
NETWORK_DIR.mkdir(parents=True, exist_ok=True)
REFERENCE_NETWORK_DIR.mkdir(parents=True, exist_ok=True)

SEED_MANIFEST = OUT / "FINAL_CAMPAIGN_SEEDS_100.json"
CAMPAIGN_MATRIX = OUT / "CORE_CAMPAIGN_MATRIX_100x6.csv"
CAMPAIGN_MANIFEST = OUT / "FINAL_CAMPAIGN_MANIFEST_100x6.json"
OUTPUT_SCHEMA = OUT / "FINAL_OUTPUT_SCHEMA_100x6.json"
AUDIT_JSON = OUT / "FINAL_CAMPAIGN_CONFIGURATION_AUDIT_100x6.json"
NETWORK_MANIFEST = OUT / "SIX_SCENARIO_NETWORK_MANIFEST.json"

SCENARIO_ORDER = list(geom.SCENARIO_ORDER)
CORE_DEMANDS = [3000, 3200]
P_VALUES = [0.25, 0.50, 0.75, 1.00]
T_UP_VALUES = [15.0, 30.0, 60.0]

TARGET_SEED_N = 100
SUMO_SEED_MAX = 2_147_483_647

EXPECTED_CORE_RUNS = 6 * 2 * 100 * 13
EXPECTED_CONTROL_N = 6 * 2 * 100
EXPECTED_DYNAMIC_N = 6 * 2 * 100 * 12
EXPECTED_PAIR_KEY_N = 6 * 2 * 100

EXPECTED_MODEL_SHA256 = "f5689b15fbd4a4e571f27598868f05d3f02b8f855ed0682da5cbdc697bf47b19"
EXPECTED_VALIDATION_SHA256 = "5da1f7e87e3a20de508ce5cc5d04f7b5467012d46ddfc57a6a6063741fe9147c"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label}: {path}")
    return path


def read_json(path: Path):
    return json.loads(path.read_text())


def verify_sources():
    require(MODEL_SOURCE, "dbmr_final_model.py")
    require(VALIDATION_SOURCE, "dbmr_final_validation.py")
    model_hash = sha256(MODEL_SOURCE)
    validation_hash = sha256(VALIDATION_SOURCE)
    if model_hash != EXPECTED_MODEL_SHA256:
        raise RuntimeError("dbmr_final_model.py hash changed")
    if validation_hash != EXPECTED_VALIDATION_SHA256:
        raise RuntimeError("dbmr_final_validation.py hash changed")
    return {
        "model_sha256": model_hash,
        "validation_sha256": validation_hash,
    }

def verify_spec():
    require(SPEC, "authoritative final spec")
    spec = read_json(SPEC)
    if not isinstance(spec, dict) or not spec:
        raise RuntimeError("Final spec is empty or malformed")
    return spec, sha256(SPEC)


def freeze_100_seeds():
    """Load and validate the exact 100 seeds used by the final campaign."""
    source = read_json(require(SEED_SOURCE, "final 100-seed manifest"))
    seeds = [int(x) for x in source.get("seeds", [])]
    if len(seeds) != TARGET_SEED_N or len(set(seeds)) != TARGET_SEED_N:
        raise RuntimeError("Seed manifest must contain exactly 100 unique seeds")
    if any(v <= 0 or v > SUMO_SEED_MAX for v in seeds):
        raise RuntimeError("Seed manifest contains a value outside the SUMO seed domain")

    proposed = {
        "manifest": "DBMR_FINAL_CAMPAIGN_SEEDS_100_V2_1",
        "seed_n": TARGET_SEED_N,
        "seed_domain": "1..2147483647",
        "source": str(SEED_SOURCE.relative_to(ROOT)),
        "source_sha256": sha256(SEED_SOURCE),
        "seeds": seeds,
    }
    SEED_MANIFEST.write_text(json.dumps(proposed, indent=2))
    return proposed

def build_and_audit_networks():
    import dbmr_final_validation as v

    vfile = Path(v.__file__).resolve()
    expected_vfile = (ROOT / "dbmr_final_validation.py").resolve()
    if vfile != expected_vfile:
        raise RuntimeError(f"Wrong validation module imported: {vfile}")
    if sha256(vfile) != EXPECTED_VALIDATION_SHA256:
        raise RuntimeError("dbmr_final_validation.py hash changed")

    # Build exact reference S1/S2/S3 using the frozen validated builder, but
    # redirect NETWORK_DIR so validation outputs remain isolated.
    old_network_dir = v.NETWORK_DIR
    v.NETWORK_DIR = REFERENCE_NETWORK_DIR
    try:
        validated_refs = {s: Path(v.build_network(s)) for s in ["S1", "S2", "S3"]}
    finally:
        v.NETWORK_DIR = old_network_dir

    # Build all six using generalized geometry-only extension.
    built = {
        s: geom.build_network(
            s,
            NETWORK_DIR,
            netconvert_binary=v.find_binary("netconvert"),
        )
        for s in SCENARIO_ORDER
    }

    rows = {}
    for s in SCENARIO_ORDER:
        lanes = geom.scenario_exit_lanes(s)
        row = {
            "scenario": s,
            "description": geom.SCENARIO_META[s],
            "lane_counts": lanes,
            "network_path": str(Path(built[s]).relative_to(ROOT)),
            "network_raw_sha256": sha256(Path(built[s])),
            "network_canonical_xml_sha256": geom.canonical_xml_sha256(Path(built[s])),
        }
        if s in validated_refs:
            ref = validated_refs[s]
            row.update({
                "validated_builder_reference": str(ref.relative_to(ROOT)),
                "validated_builder_canonical_xml_sha256": geom.canonical_xml_sha256(ref),
                "canonical_reproduction_pass": (
                    geom.canonical_xml_sha256(ref)
                    == geom.canonical_xml_sha256(Path(built[s]))
                ),
            })
        else:
            row["canonical_reproduction_pass"] = None
        rows[s] = row

    existing_pass = all(
        bool(rows[s]["canonical_reproduction_pass"])
        for s in ["S1", "S2", "S3"]
    )
    if not existing_pass:
        raise RuntimeError(
            "Generalized network builder does not exactly reproduce validated S1/S2/S3"
        )

    manifest = {
        "network_family": "DBMR_SIX_SCENARIO_EXIT_GEOMETRY_V2",
        "behavioral_ladder_changed": False,
        "only_geometry_change": "exit lane counts",
        "scenario_order": SCENARIO_ORDER,
        "scenarios": rows,
        "validated_anchor_reproduction_pass": True,
    }
    NETWORK_MANIFEST.write_text(json.dumps(manifest, indent=2))
    return manifest


def build_core_matrix(seeds):
    rows = []
    for scenario_rank, scenario in enumerate(SCENARIO_ORDER):
        meta = geom.SCENARIO_META[scenario]
        for demand in CORE_DEMANDS:
            for seed_index, seed in enumerate(seeds):
                pair_key = f"{scenario}_d{demand}_s{seed}"

                rows.append({
                    "phase": "CORE",
                    "scenario": scenario,
                    "scenario_rank": scenario_rank,
                    "double_exit_n": int(meta["double_exit_n"]),
                    "geometry_pattern": meta["pattern"],
                    "demand_vph": demand,
                    "seed_index": seed_index,
                    "seed": int(seed),
                    "mode": "CONTROL",
                    "p": 0.0,
                    "T_up_s": np.nan,
                    "pair_key": pair_key,
                    "condition_key": f"{scenario}_d{demand}_CONTROL",
                    "run_id": f"CORE_{scenario}_d{demand}_s{seed}_CONTROL",
                })

                for p in P_VALUES:
                    for T in T_UP_VALUES:
                        rows.append({
                            "phase": "CORE",
                            "scenario": scenario,
                            "scenario_rank": scenario_rank,
                            "double_exit_n": int(meta["double_exit_n"]),
                            "geometry_pattern": meta["pattern"],
                            "demand_vph": demand,
                            "seed_index": seed_index,
                            "seed": int(seed),
                            "mode": "DYNAMIC",
                            "p": float(p),
                            "T_up_s": float(T),
                            "pair_key": pair_key,
                            "condition_key": (
                                f"{scenario}_d{demand}_p{p:.2f}_T{int(T)}"
                            ),
                            "run_id": (
                                f"CORE_{scenario}_d{demand}_s{seed}"
                                f"_p{p:.2f}_T{int(T)}"
                            ),
                        })

    return pd.DataFrame(rows)


def validate_matrix(df, seeds):
    controls = df[df["mode"].eq("CONTROL")]
    dynamic = df[df["mode"].eq("DYNAMIC")]

    checks = {
        "run_n": int(len(df)),
        "expected_run_n": EXPECTED_CORE_RUNS,
        "run_count_pass": len(df) == EXPECTED_CORE_RUNS,
        "scenario_n": int(df["scenario"].nunique()),
        "scenario_order_exact": (
            list(dict.fromkeys(df["scenario"].astype(str).tolist()))
            == SCENARIO_ORDER
        ),
        "demand_n": int(df["demand_vph"].nunique()),
        "seed_n": int(df["seed"].nunique()),
        "sumo_seed_domain_pass": bool(df["seed"].between(1, SUMO_SEED_MAX).all()),
        "control_n": int(len(controls)),
        "dynamic_n": int(len(dynamic)),
        "expected_control_n": EXPECTED_CONTROL_N,
        "expected_dynamic_n": EXPECTED_DYNAMIC_N,
        "pair_key_n": int(df["pair_key"].nunique()),
        "expected_pair_key_n": EXPECTED_PAIR_KEY_N,
        "run_id_unique": bool(df["run_id"].is_unique),
        "one_control_per_pair_key": bool(
            (controls.groupby("pair_key").size() == 1).all()
        ),
        "twelve_dynamic_per_pair_key": bool(
            (dynamic.groupby("pair_key").size() == 12).all()
        ),
        "all_100_seeds_each_scenario_demand": bool(
            (
                df.groupby(["scenario", "demand_vph"])["seed"].nunique()
                == len(seeds)
            ).all()
        ),
        "p_values_exact": sorted(dynamic["p"].unique().tolist()) == P_VALUES,
        "T_values_exact": sorted(dynamic["T_up_s"].unique().tolist()) == T_UP_VALUES,
        "control_p_zero": bool((controls["p"] == 0.0).all()),
        "control_T_is_NA": bool(controls["T_up_s"].isna().all()),
        "conditions_per_pair_exact_13": bool(
            (df.groupby("pair_key").size() == 13).all()
        ),
    }

    required = [
        "run_count_pass",
        "scenario_order_exact",
        "run_id_unique",
        "one_control_per_pair_key",
        "twelve_dynamic_per_pair_key",
        "all_100_seeds_each_scenario_demand",
        "p_values_exact",
        "T_values_exact",
        "control_p_zero",
        "control_T_is_NA",
        "sumo_seed_domain_pass",
        "conditions_per_pair_exact_13",
    ]
    checks["matrix_pass"] = all(bool(checks[k]) for k in required)
    return checks


def build_output_schema():
    schema = {
        "worker_write_policy": {
            "libsumo_workers_write_parquet": False,
            "intermediate": ["csv.gz", "json", "pickle"],
            "final_parquet_only_clean_analysis_process": True,
        },
        "restartability": {
            "complete_marker": "per-run summary JSON with empty error",
            "rerun_rule": "skip clean run_id; rerun missing/error run_id",
            "array_unit": "seed_index; 156 runs per seed task",
        },
        "private_benefit": {
            "delta_TT_definition": "TT_control - TT_dynamic",
            "positive_interpretation": "individually advantageous in travel-time terms",
            "required_statistics": [
                "n",
                "median_delta_TT_s",
                "p90_delta_TT_s",
                "winner_share",
                "loser_share",
            ],
            "required_groups": [
                "ALL",
                "ELIGIBLE",
                "NONELIGIBLE",
                "ESCALATED",
            ],
        },
        "primary_outcomes": [
            "private benefit",
            "system performance",
            "eligible/noneligible externality",
            "collapse probability/timing",
            "regime dependence",
            "reliability",
        ],
        "model_sensitive": [
            "absolute L2/L4 braking p95",
            "absolute L2/L4 jerk p95",
            "collision/teleport absolute interpretation",
        ],
    }
    OUTPUT_SCHEMA.write_text(json.dumps(schema, indent=2))
    return schema


def main():
    source_hashes = verify_sources()
    _, spec_hash = verify_spec()
    seed_manifest = freeze_100_seeds()
    seeds = seed_manifest["seeds"]

    network_manifest = build_and_audit_networks()

    matrix = build_core_matrix(seeds)
    checks = validate_matrix(matrix, seeds)
    matrix.to_csv(CAMPAIGN_MATRIX, index=False)
    build_output_schema()

    manifest = {
        "manifest": "DBMR_FINAL_CAMPAIGN_CONFIG_V2_100x6",
        "simulation_run": False,
        "status": (
            "CORE CAMPAIGN CONFIGURATION FROZEN"
            if checks["matrix_pass"]
            else "CONFIGURATION AUDIT FAILED"
        ),
        "operational_model": "Krauss",
        "behavioral_ladder": "L0-L4 (five levels; unchanged)",
        "behavioral_ladder_changed": False,
        "final_spec_path": str(SPEC.relative_to(ROOT)),
        "final_spec_sha256": spec_hash,
        "model_source_sha256": source_hashes["model_sha256"],
        "validation_source_sha256": source_hashes["validation_sha256"],
        "scenarios": {
            s: {
                **geom.SCENARIO_META[s],
                "exit_lanes": geom.SCENARIO_LANES[s],
            }
            for s in SCENARIO_ORDER
        },
        "scenario_order": SCENARIO_ORDER,
        "network_manifest": str(NETWORK_MANIFEST.relative_to(ROOT)),
        "network_manifest_sha256": sha256(NETWORK_MANIFEST),
        "core_demands_vph": CORE_DEMANDS,
        "p_values_dynamic": P_VALUES,
        "T_up_s_dynamic": T_UP_VALUES,
        "control_definition": "p=0; fixed L0; one control per scenario-demand-seed",
        "control_T_up_implementation_s": 30.0,
        "seed_manifest": str(SEED_MANIFEST.relative_to(ROOT)),
        "seed_n": len(seeds),
        "core_run_count": int(len(matrix)),
        "expected_core_run_count": EXPECTED_CORE_RUNS,
        "CRN_policy": (
            "Within each scenario-demand-seed pair_key, CONTROL and all 12 DYNAMIC "
            "conditions reuse the same route/departure/vehicle-ID realization; "
            "assignment generation remains deterministic and pairable."
        ),
        "truba_array_design": {
            "array_index": "seed_index 0..99",
            "runs_per_array_task": 6 * 2 * 13,
            "runs_per_array_task_expected": 156,
            "workers_per_task": 4,
            "array_concurrency": 48,
            "maximum_concurrent_cores_at_48x4": 192,
        },
        "matrix_path": str(CAMPAIGN_MATRIX.relative_to(ROOT)),
        "output_schema_path": str(OUTPUT_SCHEMA.relative_to(ROOT)),
        "matrix_checks": checks,
        "launch_authorization": bool(
            checks["matrix_pass"]
            and network_manifest["validated_anchor_reproduction_pass"]
        ),
    }
    CAMPAIGN_MANIFEST.write_text(json.dumps(manifest, indent=2))

    audit = {
        "SOURCE_HASHES_OK": True,
        "FINAL_SPEC_PRESENT": True,
        "SEED_100_FREEZE_OK": len(seeds) == 100 and len(set(seeds)) == 100 and all(1 <= int(x) <= SUMO_SEED_MAX for x in seeds),
        "SIX_SCENARIO_NETWORK_AUDIT_PASS": bool(
            network_manifest["validated_anchor_reproduction_pass"]
        ),
        "CORE_MATRIX_RUN_N": int(len(matrix)),
        "CORE_MATRIX_EXPECTED_N": EXPECTED_CORE_RUNS,
        "CORE_MATRIX_PASS": bool(checks["matrix_pass"]),
        "LAUNCH_AUTHORIZATION": bool(manifest["launch_authorization"]),
    }
    audit["FINAL_CAMPAIGN_CONFIG_AUDIT_PASS"] = all(
        bool(v)
        for k, v in audit.items()
        if k not in {"CORE_MATRIX_RUN_N", "CORE_MATRIX_EXPECTED_N"}
    )
    AUDIT_JSON.write_text(json.dumps(audit, indent=2))

    print("DBMR FINAL CAMPAIGN CONFIGURATION AUDIT V2 — 100x6")
    print("=" * 100)
    print("simulation_run: False")
    print("behavioral_ladder: L0-L4")
    print("seed_n:", len(seeds))
    print("scenario_order:", SCENARIO_ORDER)
    print("core_matrix_run_n:", len(matrix))
    print("expected_core_run_n:", EXPECTED_CORE_RUNS)
    print("control_n:", checks["control_n"])
    print("dynamic_n:", checks["dynamic_n"])
    print("pair_key_n:", checks["pair_key_n"])
    print("network_anchor_reproduction_pass:",
          network_manifest["validated_anchor_reproduction_pass"])
    print("matrix_pass:", checks["matrix_pass"])
    print("launch_authorization:", manifest["launch_authorization"])
    print("OUTPUT:", OUT)
    print("FINAL_CAMPAIGN_CONFIG_AUDIT_PASS:",
          audit["FINAL_CAMPAIGN_CONFIG_AUDIT_PASS"])


if __name__ == "__main__":
    main()
