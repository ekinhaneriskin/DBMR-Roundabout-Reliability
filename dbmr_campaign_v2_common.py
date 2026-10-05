#!/usr/bin/env python3
"""Common frozen-contract utilities for DBMR 100-seed × 6-geometry campaign."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import dbmr_six_scenario_networks_v2 as geom


ROOT = Path(__file__).resolve().parent

CONFIG_DIR = ROOT / "outputs" / "DBMR_FINAL_CAMPAIGN_CONFIG_V2_100x6"
MATRIX_PATH = CONFIG_DIR / "CORE_CAMPAIGN_MATRIX_100x6.csv"
CONFIG_MANIFEST = CONFIG_DIR / "FINAL_CAMPAIGN_MANIFEST_100x6.json"
CONFIG_AUDIT = CONFIG_DIR / "FINAL_CAMPAIGN_CONFIGURATION_AUDIT_100x6.json"
SEED_MANIFEST = CONFIG_DIR / "FINAL_CAMPAIGN_SEEDS_100.json"
NETWORK_MANIFEST = CONFIG_DIR / "SIX_SCENARIO_NETWORK_MANIFEST.json"

SPEC_PATH = ROOT / "config" / "DBMR_FINAL_SPEC.json"

VALIDATION_PATH = ROOT / "dbmr_final_validation.py"
MODEL_PATH = ROOT / "dbmr_final_model.py"

CAMPAIGN_OUT = ROOT / "outputs" / "DBMR_FINAL_CAMPAIGN_V2_100x6"
TASK_REPORT_DIR = CAMPAIGN_OUT / "truba_tasks"
REPORT_DIR = CAMPAIGN_OUT / "reports"
META_DIR = CAMPAIGN_OUT / "meta"

for d in (CAMPAIGN_OUT, TASK_REPORT_DIR, REPORT_DIR, META_DIR):
    d.mkdir(parents=True, exist_ok=True)

EXPECTED_VALIDATION_SHA256 = "5da1f7e87e3a20de508ce5cc5d04f7b5467012d46ddfc57a6a6063741fe9147c"
EXPECTED_MODEL_SHA256 = "f5689b15fbd4a4e571f27598868f05d3f02b8f855ed0682da5cbdc697bf47b19"

EXPECTED_MATRIX_N = 15600
EXPECTED_CONTROL_N = 1200
EXPECTED_DYNAMIC_N = 14400
EXPECTED_PAIR_KEY_N = 1200
CONTROL_T_IMPLEMENTATION_S = 30.0

MECHANICAL_ZERO_FIELDS = [
    "departure_not_l0_n",
    "level_jump_violation_n",
    "noneligible_transition_n",
    "type_mismatch_n",
    "pushy_runtime_update_errors",
    "pushy_highspeed_nonzero_samples",
    "pushy_fast_neighbor_nonzero_samples",
    "pushy_no_neighbor_nonzero_samples",
    "safety_query_errors",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def atomic_json(obj: Any, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=True, default=str))
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text())


def require(path: Path, label: str):
    if not path.exists():
        raise FileNotFoundError(f"{label}: {path}")
    return path


def verify_frozen_inputs():
    for p, label in [
        (MATRIX_PATH, "100x6 matrix"),
        (CONFIG_MANIFEST, "100x6 manifest"),
        (CONFIG_AUDIT, "100x6 config audit"),
        (SEED_MANIFEST, "100-seed manifest"),
        (NETWORK_MANIFEST, "six-scenario network manifest"),
        (SPEC_PATH, "authoritative final spec"),
        (VALIDATION_PATH, "dbmr_final_validation.py"),
        (MODEL_PATH, "dbmr_final_model.py"),
    ]:
        require(p, label)

    config = read_json(CONFIG_MANIFEST)
    audit = read_json(CONFIG_AUDIT)
    net_manifest = read_json(NETWORK_MANIFEST)

    if not bool(audit.get("FINAL_CAMPAIGN_CONFIG_AUDIT_PASS")):
        raise RuntimeError("100x6 configuration audit is not PASS")
    if not bool(config.get("launch_authorization")):
        raise RuntimeError("100x6 manifest launch_authorization is false")
    if config.get("operational_model") != "Krauss":
        raise RuntimeError("Operational model is not Krauss")
    if config.get("behavioral_ladder") != "L0-L4 (five levels; unchanged)":
        raise RuntimeError("Behavioral ladder freeze changed unexpectedly")
    if not bool(net_manifest.get("validated_anchor_reproduction_pass")):
        raise RuntimeError("S1/S2/S3 generalized-network reproduction did not pass")


    if sha256(VALIDATION_PATH) != EXPECTED_VALIDATION_SHA256:
        raise RuntimeError("dbmr_final_validation.py hash changed")
    if sha256(MODEL_PATH) != EXPECTED_MODEL_SHA256:
        raise RuntimeError("dbmr_final_model.py hash changed")

    spec_sha = sha256(SPEC_PATH)
    if spec_sha != str(config["final_spec_sha256"]):
        raise RuntimeError("Authoritative final spec hash changed")

    matrix = pd.read_csv(MATRIX_PATH)
    if len(matrix) != EXPECTED_MATRIX_N:
        raise RuntimeError(f"matrix rows {len(matrix)} != {EXPECTED_MATRIX_N}")
    if int((matrix["mode"] == "CONTROL").sum()) != EXPECTED_CONTROL_N:
        raise RuntimeError("CONTROL count changed")
    if int((matrix["mode"] == "DYNAMIC").sum()) != EXPECTED_DYNAMIC_N:
        raise RuntimeError("DYNAMIC count changed")
    if int(matrix["pair_key"].nunique()) != EXPECTED_PAIR_KEY_N:
        raise RuntimeError("pair_key count changed")
    if not matrix["run_id"].is_unique:
        raise RuntimeError("campaign run_id is not unique")
    if sorted(matrix["seed_index"].unique().tolist()) != list(range(100)):
        raise RuntimeError("seed_index is not exactly 0..99")
    if list(dict.fromkeys(matrix["scenario"].astype(str).tolist())) != geom.SCENARIO_ORDER:
        raise RuntimeError("scenario order changed")

    return {
        "matrix": matrix,
        "config": config,
        "audit": audit,
        "network_manifest": net_manifest,
        "validation_sha256": sha256(VALIDATION_PATH),
        "model_sha256": sha256(MODEL_PATH),
        "spec_sha256": spec_sha,
        "matrix_sha256": sha256(MATRIX_PATH),
        "config_manifest_sha256": sha256(CONFIG_MANIFEST),
        "seed_manifest_sha256": sha256(SEED_MANIFEST),
        "network_manifest_sha256": sha256(NETWORK_MANIFEST),
    }


def import_exact_modules():
    import dbmr_final_validation as v
    import dbmr_final_model as model

    if Path(v.__file__).resolve() != VALIDATION_PATH.resolve():
        raise RuntimeError(f"Wrong validation module imported: {v.__file__}")
    if Path(model.__file__).resolve() != MODEL_PATH.resolve():
        raise RuntimeError(f"Wrong model module imported: {model.__file__}")

    required_v = [
        "make_job",
        "run_jobs",
        "load_expected_results",
        "assignment_crn_audit",
        "eligibility_formula_audit",
        "level_anchor_audit",
        "system_paired_effects",
        "read_exit_lane_counts",
        "sumo_version_line",
    ]
    missing = [x for x in required_v if not hasattr(v, x)]
    if missing:
        raise RuntimeError(f"Validation interface changed: missing {missing}")

    required_m = [
        "load_levels",
        "run_one",
        "write_routes",
        "pressure_index",
        "transition_probability",
        "load_vehicle_file",
    ]
    missing = [x for x in required_m if not hasattr(model, x)]
    if missing:
        raise RuntimeError(f"Model interface changed: missing {missing}")

    # Extend only the geometry lookup used by validation helper code.
    # Existing S1/S2/S3 values are identical.
    v.scenario_exit_lanes = geom.scenario_exit_lanes

    return v, model


def redirect_validation_outputs(v, new_out: Path = CAMPAIGN_OUT):
    """Redirect every validation module Path global under its original OUT."""
    old_out = getattr(v, "OUT", None)
    if not isinstance(old_out, Path):
        raise RuntimeError("dbmr_final_validation.OUT unavailable")

    old_out_resolved = old_out.resolve()
    mapping = {}

    snapshot = {
        name: value
        for name, value in vars(v).items()
        if isinstance(value, Path)
    }

    for name, old_path in snapshot.items():
        try:
            rel = old_path.resolve().relative_to(old_out_resolved)
        except Exception:
            continue

        new_path = new_out / rel
        if old_path.suffix:
            new_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            new_path.mkdir(parents=True, exist_ok=True)

        setattr(v, name, new_path)
        mapping[name] = {"old": str(old_path), "new": str(new_path)}

    v.OUT = new_out
    new_out.mkdir(parents=True, exist_ok=True)
    return mapping


def verify_sumo_version(v):
    spec = read_json(SPEC_PATH)
    frozen = str(spec.get("sumo_version", ""))
    current = str(v.sumo_version_line())
    if frozen and current != frozen:
        raise RuntimeError(
            f"SUMO version mismatch: current={current!r}, frozen={frozen!r}"
        )
    return current, frozen


def load_levels(v, model):
    levels, level_source = model.load_levels()
    ok, detail = v.level_anchor_audit(levels)
    if not ok:
        raise RuntimeError(f"Frozen level anchor audit failed: {detail}")
    return levels, level_source, detail


def network_paths(frozen):
    out = {}
    rows = frozen["network_manifest"]["scenarios"]

    for s in geom.SCENARIO_ORDER:
        p = ROOT / rows[s]["network_path"]
        require(p, f"{s} network")
        if sha256(p) != rows[s]["network_raw_sha256"]:
            raise RuntimeError(f"{s} network raw hash changed")

        canonical = geom.canonical_xml_sha256(p)
        if canonical != rows[s]["network_canonical_xml_sha256"]:
            raise RuntimeError(f"{s} network canonical XML hash changed")

        out[s] = p

    return out


def build_jobs(v, levels, matrix_subset: pd.DataFrame, networks: dict[str, Path]):
    jobs = []

    resolution = float(v.PRIMARY_LATERAL_RESOLUTION_M)

    for idx, r in matrix_subset.iterrows():
        mode = str(r["mode"])
        impl_T = (
            CONTROL_T_IMPLEMENTATION_S
            if mode == "CONTROL"
            else float(r["T_up_s"])
        )

        j = v.make_job(
            layer="CORE",
            scenario=str(r["scenario"]),
            demand=int(r["demand_vph"]),
            seed=int(r["seed"]),
            mode=mode,
            p=float(r["p"]),
            T_up=float(impl_T),
            resolution=resolution,
            net=Path(networks[str(r["scenario"])]),
            levels=levels,
            instrument_third_row=False,
            suffix="",
        )

        j["campaign_run_id"] = str(r["run_id"])
        j["pair_key"] = str(r["pair_key"])
        j["campaign_matrix_row"] = int(idx)
        j["seed_index"] = int(r["seed_index"])
        j["scientific_T_up_s"] = (
            None if mode == "CONTROL" else float(r["T_up_s"])
        )
        jobs.append(j)

    engine_ids = [str(j["run_id"]) for j in jobs]
    summaries = [str(j["summary"]) for j in jobs]

    if len(set(engine_ids)) != len(engine_ids):
        raise RuntimeError("Engine run IDs are not unique in selected jobs")
    if len(set(summaries)) != len(summaries):
        raise RuntimeError("Summary paths are not unique in selected jobs")

    assert_job_path_isolation(jobs)
    return jobs


def assert_job_path_isolation(jobs):
    validation_root = (ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1").resolve()
    new_root = CAMPAIGN_OUT.resolve()

    path_keys = [
        "summary",
        "assignment_file",
        "out",
        "vehicle_dir",
        "ts_dir",
        "assignment_dir",
        "scratch",
    ]
    bad = []

    for j in jobs:
        for key in path_keys:
            if key not in j:
                bad.append((j.get("run_id"), key, "missing"))
                continue
            p = Path(str(j[key])).resolve()
            try:
                p.relative_to(validation_root)
                bad.append((j.get("run_id"), key, "validation_output_tree"))
                continue
            except Exception:
                pass
            try:
                p.relative_to(new_root)
            except Exception:
                bad.append((j.get("run_id"), key, "outside_campaign_tree"))

    if bad:
        raise RuntimeError(f"Job path isolation failed: {bad[:20]}")


def assert_summary_path_isolation(runs: pd.DataFrame):
    validation_root = (ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1").resolve()
    new_root = CAMPAIGN_OUT.resolve()

    bad = []
    for _, r in runs.iterrows():
        vf = str(r.get("vehicle_file", "") or "")
        if not vf:
            bad.append((r.get("run_id"), "vehicle_file_missing"))
            continue

        p = Path(vf).resolve()
        try:
            p.relative_to(validation_root)
            bad.append((r.get("run_id"), "vehicle_file_validation_tree"))
            continue
        except Exception:
            pass
        try:
            p.relative_to(new_root)
        except Exception:
            bad.append((r.get("run_id"), "vehicle_file_outside_campaign_tree"))

    if bad:
        raise RuntimeError(f"Summary output isolation failed: {bad[:20]}")


def validate_mechanical_rows(runs: pd.DataFrame):
    checks = {
        "run_n": int(len(runs)),
        "all_error_empty": bool(
            runs["error"].fillna("").astype(str).str.strip().eq("").all()
        ),
        "all_architecture_pass": bool(
            runs["architecture_pass"].fillna(0).astype(int).eq(1).all()
        ) if "architecture_pass" in runs else False,
    }

    for field in MECHANICAL_ZERO_FIELDS:
        checks[f"{field}_present"] = field in runs.columns
        checks[f"{field}_zero"] = (
            bool(pd.to_numeric(runs[field], errors="coerce").fillna(0).eq(0).all())
            if field in runs.columns
            else False
        )

    checks["pass"] = all(
        bool(v)
        for k, v in checks.items()
        if isinstance(v, bool)
    )
    return checks


def task_report_path(seed_index: int) -> Path:
    return TASK_REPORT_DIR / f"seed_index_{int(seed_index):03d}.json"


def setup_context(matrix_subset: pd.DataFrame | None = None):
    frozen = verify_frozen_inputs()
    v, model = import_exact_modules()
    current_sumo, frozen_sumo = verify_sumo_version(v)
    levels, level_source, level_detail = load_levels(v, model)
    redirect_map = redirect_validation_outputs(v, CAMPAIGN_OUT)
    networks = network_paths(frozen)

    matrix = frozen["matrix"] if matrix_subset is None else matrix_subset
    jobs = build_jobs(v, levels, matrix, networks)

    return {
        "frozen": frozen,
        "v": v,
        "model": model,
        "levels": levels,
        "level_source": level_source,
        "level_detail": level_detail,
        "networks": networks,
        "matrix": matrix,
        "jobs": jobs,
        "current_sumo_version": current_sumo,
        "frozen_sumo_version": frozen_sumo,
        "redirect_map": redirect_map,
    }
