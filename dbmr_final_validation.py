#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Validation and analysis utilities for the DBMR simulation model.

This module provides network generation, campaign job construction, mechanical
integrity checks, common-random-number audits, paired system effects, and
vehicle-level outcome summaries. Validation diagnostics are descriptive and do
not automatically tune model parameters."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import html
import json
import math
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

import dbmr_final_model as model


# =============================================================================
# FINAL SCIENTIFIC DESIGN
# =============================================================================

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1"
SUMMARY_DIR = OUT / "summaries"
VEHICLE_DIR = OUT / "vehicles"
TS_DIR = OUT / "timeseries"
ASSIGNMENT_DIR = OUT / "assignments"
SCRATCH_DIR = OUT / "scratch"
NETWORK_DIR = OUT / "networks"
REPORT_DIR = OUT / "reports"

for _p in (
    OUT, SUMMARY_DIR, VEHICLE_DIR, TS_DIR, ASSIGNMENT_DIR,
    SCRATCH_DIR, NETWORK_DIR, REPORT_DIR,
):
    _p.mkdir(parents=True, exist_ok=True)

SCENARIOS = ["S1", "S2", "S3"]
S2_TWO_LANE_EXITS = ("N", "S")

CORE_DEMANDS = [3000, 3200]
FALSIFICATION_DEMANDS = [1800, 2600]

P_VALUES = [0.25, 0.50, 0.75, 1.00]
T_UP_VALUES = [15.0, 30.0, 60.0]
SEEDS = [11, 43, 157]

PRIMARY_LATERAL_RESOLUTION_M = 0.05
ROBUSTNESS_LATERAL_RESOLUTION_M = 0.025

# Fixed free-flow references used by the pressure model.
LOCKED_FREEFLOW_S = {
    "right": 30.6,
    "straight": 40.8,
    "left": 51.0,
}

EXPECTED_SUMO_VERSION_TOKEN = "1.27.1"

RESEARCH_QUESTION = (
    "All vehicles enter at L0. Under experienced congestion, susceptible drivers "
    "may progressively adapt L0->L1->L2->L3->L4. The study asks whether these "
    "congestion-induced adaptive/assertive behaviors help the drivers who express "
    "them (waiting time, travel time, completion), to what degree, and what "
    "system-level reliability/stability/safety consequences accompany them."
)


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_obj(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def atomic_json(obj: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=True))
    os.replace(tmp, path)


def json_ok(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
        return not bool(str(d.get("error", "")).strip())
    except Exception:
        return False


def find_binary(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise FileNotFoundError(
            f"'{name}' not found on PATH. Verify the SUMO installation."
        )
    return p


def sumo_version_line() -> str:
    return subprocess.check_output(
        [find_binary("sumo"), "--version"],
        text=True,
        stderr=subprocess.STDOUT,
    ).splitlines()[0].strip()


def mp_context():
    import multiprocessing as mp
    return mp.get_context("spawn")


# =============================================================================
# EXACT S1 / S2 / S3 NETWORKS
# =============================================================================

def scenario_exit_lanes(scenario: str) -> dict[str, int]:
    if scenario == "S1":
        return {"N": 1, "E": 1, "S": 1, "W": 1}
    if scenario == "S2":
        return {
            d: (2 if d in S2_TWO_LANE_EXITS else 1)
            for d in ["N", "E", "S", "W"]
        }
    if scenario == "S3":
        return {"N": 2, "E": 2, "S": 2, "W": 2}
    raise ValueError(scenario)


def build_network(scenario: str) -> Path:
    """
    Builds the DBMR S1/S2/S3 geometry:
    - 2-lane approaches;
    - 2-lane 8-arc circulating roadway;
    - scenario-specific exit lane counts only.
    """
    K = 77.5 / 70.0
    work = NETWORK_DIR / scenario
    work.mkdir(parents=True, exist_ok=True)

    lanes = scenario_exit_lanes(scenario)
    nodes = work / "nodes.nod.xml"
    edges = work / "edges.edg.xml"
    conns = work / "connections.con.xml"
    net = work / f"{scenario}.net.xml"

    nodes.write_text(f"""<nodes>
  <node id="N" x="0" y="{200*K:.1f}"/>
  <node id="E" x="{200*K:.1f}" y="0"/>
  <node id="S" x="0" y="-{200*K:.1f}"/>
  <node id="W" x="-{200*K:.1f}" y="0"/>

  <node id="c0" x="0" y="{70*K:.1f}"/>
  <node id="c1" x="{49*K:.1f}" y="{49*K:.1f}"/>
  <node id="c2" x="{70*K:.1f}" y="0"/>
  <node id="c3" x="{49*K:.1f}" y="-{49*K:.1f}"/>
  <node id="c4" x="0" y="-{70*K:.1f}"/>
  <node id="c5" x="-{49*K:.1f}" y="-{49*K:.1f}"/>
  <node id="c6" x="-{70*K:.1f}" y="0"/>
  <node id="c7" x="-{49*K:.1f}" y="{49*K:.1f}"/>
</nodes>
""")

    edges.write_text(f"""<edges>
  <edge id="c0c7" from="c0" to="c7" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c7c6" from="c7" to="c6" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c6c5" from="c6" to="c5" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c5c4" from="c5" to="c4" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c4c3" from="c4" to="c3" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c3c2" from="c3" to="c2" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c2c1" from="c2" to="c1" numLanes="2" speed="16.7" priority="2"/>
  <edge id="c1c0" from="c1" to="c0" numLanes="2" speed="16.7" priority="2"/>

  <edge id="N_in" from="N" to="c0" numLanes="2" speed="16.7" priority="1"/>
  <edge id="E_in" from="E" to="c2" numLanes="2" speed="16.7" priority="1"/>
  <edge id="S_in" from="S" to="c4" numLanes="2" speed="16.7" priority="1"/>
  <edge id="W_in" from="W" to="c6" numLanes="2" speed="16.7" priority="1"/>

  <edge id="N_out" from="c0" to="N" numLanes="{lanes['N']}" speed="16.7" priority="2"/>
  <edge id="E_out" from="c2" to="E" numLanes="{lanes['E']}" speed="16.7" priority="2"/>
  <edge id="S_out" from="c4" to="S" numLanes="{lanes['S']}" speed="16.7" priority="2"/>
  <edge id="W_out" from="c6" to="W" numLanes="{lanes['W']}" speed="16.7" priority="2"/>
</edges>
""")

    lines = [
        "<connections>",
        '<connection from="N_in" to="c0c7" fromLane="0" toLane="0"/>',
        '<connection from="N_in" to="c0c7" fromLane="1" toLane="1"/>',
        '<connection from="E_in" to="c2c1" fromLane="0" toLane="0"/>',
        '<connection from="E_in" to="c2c1" fromLane="1" toLane="1"/>',
        '<connection from="S_in" to="c4c3" fromLane="0" toLane="0"/>',
        '<connection from="S_in" to="c4c3" fromLane="1" toLane="1"/>',
        '<connection from="W_in" to="c6c5" fromLane="0" toLane="0"/>',
        '<connection from="W_in" to="c6c5" fromLane="1" toLane="1"/>',
    ]

    ring_pairs = [
        ("c0c7", "c7c6"), ("c7c6", "c6c5"),
        ("c6c5", "c5c4"), ("c5c4", "c4c3"),
        ("c4c3", "c3c2"), ("c3c2", "c2c1"),
        ("c2c1", "c1c0"), ("c1c0", "c0c7"),
    ]
    for a, b in ring_pairs:
        lines.append(f'<connection from="{a}" to="{b}" fromLane="0" toLane="0"/>')
        lines.append(f'<connection from="{a}" to="{b}" fromLane="1" toLane="1"/>')

    exit_from = {
        "N": "c1c0",
        "E": "c3c2",
        "S": "c5c4",
        "W": "c7c6",
    }
    for d in ["N", "E", "S", "W"]:
        src = exit_from[d]
        if lanes[d] == 1:
            lines.append(
                f'<connection from="{src}" to="{d}_out" fromLane="0" toLane="0"/>'
            )
            lines.append(
                f'<connection from="{src}" to="{d}_out" fromLane="1" toLane="0"/>'
            )
        else:
            lines.append(
                f'<connection from="{src}" to="{d}_out" fromLane="0" toLane="0"/>'
            )
            lines.append(
                f'<connection from="{src}" to="{d}_out" fromLane="1" toLane="1"/>'
            )

    lines.append("</connections>")
    conns.write_text("\n".join(lines) + "\n")

    subprocess.run(
        [
            find_binary("netconvert"),
            "-n", str(nodes),
            "-e", str(edges),
            "-x", str(conns),
            "--geometry.max-angle", "15",
            "--junctions.join", "false",
            "--roundabouts.guess", "false",
            "--no-turnarounds", "true",
            "--output-file", str(net),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    return net


def read_exit_lane_counts(net: Path) -> dict[str, int]:
    root = ET.parse(net).getroot()
    out = {}
    for d in ["N", "E", "S", "W"]:
        edge = root.find(f"./edge[@id='{d}_out']")
        if edge is None:
            raise RuntimeError(f"Missing {d}_out in {net}")
        out[d] = len(edge.findall("lane"))
    return out


# =============================================================================
# DIAGNOSTIC FREE-FLOW CHECK (DOES NOT CHANGE LOCKED FREEFLOW VALUES)
# =============================================================================

def measure_freeflow(net: Path, scenario: str) -> dict[str, float]:
    work = NETWORK_DIR / scenario / "freeflow_check"
    work.mkdir(parents=True, exist_ok=True)
    rou = work / "freeflow.rou.xml"
    trip = work / "tripinfo.xml"

    rou.write_text("""<routes>
<vType id="c" vClass="passenger" maxSpeed="20" sigma="0"/>
<vehicle id="right" type="c" depart="0">
  <route edges="N_in c0c7 c7c6 W_out"/>
</vehicle>
<vehicle id="straight" type="c" depart="30">
  <route edges="N_in c0c7 c7c6 c6c5 c5c4 S_out"/>
</vehicle>
<vehicle id="left" type="c" depart="60">
  <route edges="N_in c0c7 c7c6 c6c5 c5c4 c4c3 c3c2 E_out"/>
</vehicle>
</routes>
""")

    subprocess.run(
        [
            find_binary("sumo"),
            "-n", str(net),
            "-r", str(rou),
            "--step-length", str(model.STEP),
            "--lateral-resolution", str(PRIMARY_LATERAL_RESOLUTION_M),
            "--tripinfo-output", str(trip),
            "--no-step-log", "true",
            "--no-warnings", "true",
            "--xml-validation", "never",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    res = {}
    root = ET.parse(trip).getroot()
    for ti in root.findall("tripinfo"):
        res[str(ti.attrib["id"])] = float(ti.attrib["duration"])
    return res


# =============================================================================
# MODEL / LEVEL PREFLIGHT
# =============================================================================

def level_anchor_audit(levels: dict[int, dict]) -> tuple[bool, dict]:
    """
    Prevent accidental drift from the frozen parameter anchors.
    """
    expected = {
        0: {
            "tau": 2.10,
            "minGap": 3.00,
            "lcSpeedGain": 0.6000,
            "lcKeepRight": 1.0000,
        },
        2: {
            "tau": 0.9997294069267808,
            "minGap": 2.00,
            "lcSpeedGain": 0.0461922760756686,
            "minGapLat": 0.7629443684443833,
        },
        4: {
            "tau": 0.3405407074606046,
            "minGap": 1.50,
            "lcSpeedGain": 0.0066676387526094,
            "lcKeepRight": 0.0081387882991693,
            "lcPushy": 0.1902239468693733,
        },
    }

    checks = {}
    ok = True
    for lvl, fields in expected.items():
        for key, target in fields.items():
            obs = float(levels[lvl][key])
            passed = bool(np.isclose(obs, target, rtol=0.0, atol=1e-12))
            checks[f"L{lvl}.{key}"] = {
                "observed": obs,
                "expected": target,
                "pass": passed,
            }
            ok = ok and passed
    return ok, checks


def preflight() -> dict:
    version = sumo_version_line()
    levels, source = model.load_levels()
    anchor_ok, anchor_checks = level_anchor_audit(levels)

    networks = {}
    network_hashes = {}
    lane_counts = {}
    freeflow_measured = {}

    for scenario in SCENARIOS:
        net = build_network(scenario)
        networks[scenario] = net
        network_hashes[scenario] = sha256_path(net)
        lane_counts[scenario] = read_exit_lane_counts(net)
        freeflow_measured[scenario] = measure_freeflow(net, scenario)

    lane_count_ok = all(
        lane_counts[s] == scenario_exit_lanes(s)
        for s in SCENARIOS
    )

    exact_level_source = (
        str(source) != "embedded_fallback"
        and Path(str(source)).exists()
    )

    version_ok = EXPECTED_SUMO_VERSION_TOKEN in version

    pre = {
        "sumo_version": version,
        "sumo_version_expected_token": EXPECTED_SUMO_VERSION_TOKEN,
        "sumo_version_pass": version_ok,
        "level_source": str(source),
        "exact_level_source_pass": exact_level_source,
        "level_anchor_pass": anchor_ok,
        "level_anchor_checks": anchor_checks,
        "lane_counts": lane_counts,
        "lane_count_pass": lane_count_ok,
        "network_paths": {k: str(v) for k, v in networks.items()},
        "network_hashes": network_hashes,
        "freeflow_locked_s": LOCKED_FREEFLOW_S,
        "freeflow_diagnostic_s": freeflow_measured,
        "preflight_pass": bool(
            version_ok
            and exact_level_source
            and anchor_ok
            and lane_count_ok
        ),
    }
    atomic_json(pre, OUT / "PREFLIGHT.json")

    if not pre["preflight_pass"]:
        raise RuntimeError(
            "PREFLIGHT FAILED. See outputs/DBMR_FINAL_VALIDATION_V1/PREFLIGHT.json. "
            "The full suite was not started."
        )

    return {
        "preflight": pre,
        "levels": levels,
        "level_source": str(source),
        "networks": networks,
    }


# =============================================================================
# MANIFEST
# =============================================================================

def make_job(
    *,
    layer: str,
    scenario: str,
    demand: int,
    seed: int,
    mode: str,
    p: float,
    T_up: float,
    resolution: float,
    net: Path,
    levels: dict,
    instrument_third_row: bool,
    suffix: str = "",
) -> dict:
    ptag = f"p{int(round(p*100)):03d}" if mode == "DYNAMIC" else "CTRL"
    ttag = f"T{int(round(T_up)):03d}" if mode == "DYNAMIC" else "T---"
    rtag = str(resolution).replace(".", "p")
    run_id = (
        f"{layer}_{scenario}_d{demand}_s{seed}_{mode}_{ptag}_{ttag}_lr{rtag}{suffix}"
    )

    return {
        "run_id": run_id,
        "validation_layer": layer,
        "scenario": scenario,
        "demand_vph": int(demand),
        "seed": int(seed),
        "mode": mode,
        "p": float(p),
        "T_up": float(T_up),
        "lateral_resolution": float(resolution),
        "instrument_third_row": bool(instrument_third_row),
        "instrument_lanechange": bool(instrument_third_row),
        "freeflow_s": dict(LOCKED_FREEFLOW_S),
        "net": str(net),
        "levels": levels,
        "scratch": str(SCRATCH_DIR),
        "assignment_file": str(ASSIGNMENT_DIR / f"{run_id}_assignments.csv"),
        "summary": str(SUMMARY_DIR / f"{run_id}.json"),
        "out": str(OUT),
        "vehicle_dir": str(VEHICLE_DIR),
        "ts_dir": str(TS_DIR),
        "assignment_dir": str(ASSIGNMENT_DIR),
    }


def build_manifest(networks: dict[str, Path], levels: dict) -> list[dict]:
    jobs = []

    # ---------------- CORE: 234 runs ----------------
    for scenario in SCENARIOS:
        for demand in CORE_DEMANDS:
            for seed in SEEDS:
                # one all-L0 control
                jobs.append(make_job(
                    layer="CORE",
                    scenario=scenario,
                    demand=demand,
                    seed=seed,
                    mode="CONTROL",
                    p=0.0,
                    T_up=30.0,
                    resolution=PRIMARY_LATERAL_RESOLUTION_M,
                    net=networks[scenario],
                    levels=levels,
                    instrument_third_row=True,
                ))

                for p in P_VALUES:
                    for T_up in T_UP_VALUES:
                        jobs.append(make_job(
                            layer="CORE",
                            scenario=scenario,
                            demand=demand,
                            seed=seed,
                            mode="DYNAMIC",
                            p=p,
                            T_up=T_up,
                            resolution=PRIMARY_LATERAL_RESOLUTION_M,
                            net=networks[scenario],
                            levels=levels,
                            instrument_third_row=(
                                np.isclose(p, 1.0)
                                and np.isclose(T_up, 30.0)
                            ),
                        ))

    # ---------------- FALSIFICATION: 36 runs ----------------
    for scenario in SCENARIOS:
        for demand in FALSIFICATION_DEMANDS:
            for seed in SEEDS:
                jobs.append(make_job(
                    layer="FALSIFICATION",
                    scenario=scenario,
                    demand=demand,
                    seed=seed,
                    mode="CONTROL",
                    p=0.0,
                    T_up=30.0,
                    resolution=PRIMARY_LATERAL_RESOLUTION_M,
                    net=networks[scenario],
                    levels=levels,
                    instrument_third_row=False,
                ))
                jobs.append(make_job(
                    layer="FALSIFICATION",
                    scenario=scenario,
                    demand=demand,
                    seed=seed,
                    mode="DYNAMIC",
                    p=1.0,
                    T_up=30.0,
                    resolution=PRIMARY_LATERAL_RESOLUTION_M,
                    net=networks[scenario],
                    levels=levels,
                    instrument_third_row=False,
                ))

    # ---------------- RESOLUTION: 6 runs ----------------
    for seed in SEEDS:
        jobs.append(make_job(
            layer="RESOLUTION",
            scenario="S1",
            demand=3200,
            seed=seed,
            mode="CONTROL",
            p=0.0,
            T_up=30.0,
            resolution=ROBUSTNESS_LATERAL_RESOLUTION_M,
            net=networks["S1"],
            levels=levels,
            instrument_third_row=False,
        ))
        jobs.append(make_job(
            layer="RESOLUTION",
            scenario="S1",
            demand=3200,
            seed=seed,
            mode="DYNAMIC",
            p=1.0,
            T_up=30.0,
            resolution=ROBUSTNESS_LATERAL_RESOLUTION_M,
            net=networks["S1"],
            levels=levels,
            instrument_third_row=False,
        ))

    # ---------------- REPRODUCIBILITY: 2 runs ----------------
    for mode, p in [("CONTROL", 0.0), ("DYNAMIC", 1.0)]:
        jobs.append(make_job(
            layer="REPRO",
            scenario="S1",
            demand=3200,
            seed=43,
            mode=mode,
            p=p,
            T_up=30.0,
            resolution=PRIMARY_LATERAL_RESOLUTION_M,
            net=networks["S1"],
            levels=levels,
            instrument_third_row=False,
            suffix="_DUP",
        ))

    # Deterministic manifest order.
    jobs.sort(key=lambda j: j["run_id"])

    manifest_rows = []
    for j in jobs:
        manifest_rows.append({
            k: j[k]
            for k in (
                "run_id", "validation_layer", "scenario", "demand_vph",
                "seed", "mode", "p", "T_up", "lateral_resolution",
                "instrument_third_row", "instrument_lanechange",
                "net", "summary", "assignment_file",
            )
        })
    pd.DataFrame(manifest_rows).to_csv(OUT / "VALIDATION_MANIFEST.csv", index=False)

    return jobs


# =============================================================================
# WORKER / EXECUTION
# =============================================================================

def worker_run(job: dict) -> dict:
    from pathlib import Path
    import dbmr_final_model as m

    # Redirect all model outputs into this suite's fixed experiment folder.
    m.OUT = Path(job["out"])
    m.VEHICLE_DIR = Path(job["vehicle_dir"])
    m.TS_DIR = Path(job["ts_dir"])
    m.ASSIGNMENT_DIR = Path(job["assignment_dir"])
    m.SCRATCH_DIR = Path(job["scratch"])

    return m.run_one(job)


def run_jobs(jobs: list[dict], workers: int) -> None:
    pending = [j for j in jobs if not json_ok(Path(j["summary"]))]

    print()
    print("RUN MANIFEST")
    print("-" * 100)
    print(f"selected={len(jobs)} | complete={len(jobs)-len(pending)} | pending={len(pending)}")
    print(f"workers={workers}")
    print()

    if not pending:
        return

    with ProcessPoolExecutor(
        max_workers=max(1, workers),
        mp_context=mp_context(),
    ) as ex:
        futs = {ex.submit(worker_run, j): j for j in pending}

        for fut in tqdm(
            as_completed(futs),
            total=len(futs),
            desc="DBMR FINAL VALIDATION",
            unit="run",
        ):
            j = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = {
                    "run_id": j["run_id"],
                    "validation_layer": j["validation_layer"],
                    "scenario": j["scenario"],
                    "demand_vph": j["demand_vph"],
                    "seed": j["seed"],
                    "mode": j["mode"],
                    "p": j["p"],
                    "T_up_s": j["T_up"] if j["mode"] == "DYNAMIC" else np.nan,
                    "lateral_resolution_m": j["lateral_resolution"],
                    "architecture_pass": 0,
                    "error": f"worker_exception:{type(e).__name__}:{e}",
                }

            atomic_json(res, Path(j["summary"]))

            print(
                f"DONE {j['validation_layer']} {j['scenario']} d={j['demand_vph']} "
                f"s={j['seed']} {j['mode']} p={j['p']:.2f} T={j['T_up']:.0f} | "
                f"q={res.get('throughput_vph', np.nan):.0f} | "
                f"esc={res.get('escalated_share_of_eligible', np.nan):.3f} | "
                f"collapse={res.get('collapse','NA')} | "
                f"arch={res.get('architecture_pass','NA')} | "
                f"err={res.get('error','')}"
            )


# =============================================================================
# LANE-CHANGE-AWARE THIRD-ROW ATTRIBUTION
# =============================================================================

def parse_active_lc_intervals(path: Path) -> dict[str, list[tuple[float, float]]]:
    out: dict[str, list[tuple[float, float]]] = {}
    current: dict[str, float] = {}
    if not path.exists():
        return out

    root = ET.parse(path).getroot()
    for el in list(root):
        tag = str(el.tag)
        vid = str(el.attrib.get("id", ""))
        if not vid:
            continue
        try:
            t = float(el.attrib.get("time", "nan"))
        except Exception:
            continue
        if not np.isfinite(t):
            continue

        if tag == "changeStarted":
            if vid in current:
                out.setdefault(vid, []).append((current[vid], t))
            current[vid] = t
        elif tag == "changeEnded":
            if vid in current:
                out.setdefault(vid, []).append((current.pop(vid), t))

    for vid, t0 in current.items():
        out.setdefault(vid, []).append((t0, np.inf))

    for vid in out:
        out[vid].sort()
    return out


def lc_active(intervals: dict[str, list[tuple[float, float]]], vid: str, t: float) -> bool:
    vals = intervals.get(str(vid), [])
    if not vals:
        return False
    starts = [x[0] for x in vals]
    i = bisect.bisect_right(starts, float(t)) - 1
    if i < 0:
        return False
    a, b = vals[i]
    return a <= float(t) < b


def pipe_float(value: object) -> list[float]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    out = []
    for x in str(value).split("|"):
        try:
            out.append(float(x))
        except Exception:
            out.append(np.nan)
    return out


def pipe_int(value: object) -> list[int]:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    out = []
    for x in str(value).split("|"):
        try:
            out.append(int(float(x)))
        except Exception:
            out.append(-1)
    return out


def classify_third_row(summary: dict) -> dict:
    if int(summary.get("instrument_third_row", 0)) != 1:
        return {}

    event_path = Path(str(summary.get("true_abreast_event_file", "")))
    lc_path = Path(str(summary.get("lanechange_file", "")))
    sample_n = int(summary.get("true_abreast_network_sample_n", 0) or 0)

    if not event_path.exists() or sample_n <= 0:
        return {
            "strict_network_time_pct": 0.0,
            "lc_free_network_time_pct": 0.0,
            "lc_free_all_L0_network_time_pct": 0.0,
            "lc_free_any_L1plus_network_time_pct": 0.0,
            "lc_free_any_pushy_network_time_pct": 0.0,
            "lc_free_latstable_005_network_time_pct": 0.0,
            "lc_free_latstable_010_network_time_pct": 0.0,
        }

    try:
        ev = pd.read_csv(event_path, compression="gzip")
    except pd.errors.EmptyDataError:
        ev = pd.DataFrame()

    if ev.empty:
        return {
            "strict_network_time_pct": 0.0,
            "lc_free_network_time_pct": 0.0,
            "lc_free_all_L0_network_time_pct": 0.0,
            "lc_free_any_L1plus_network_time_pct": 0.0,
            "lc_free_any_pushy_network_time_pct": 0.0,
            "lc_free_latstable_005_network_time_pct": 0.0,
            "lc_free_latstable_010_network_time_pct": 0.0,
        }

    intervals = parse_active_lc_intervals(lc_path)

    rows = []
    for _, e in ev.iterrows():
        t = float(e["t_s"])
        vids = [x for x in str(e["vehicle_ids"]).split("|") if x]
        levels = pipe_int(e.get("levels"))
        pushies = pipe_float(e.get("pushy_values"))
        latv = pipe_float(e.get("lateral_speeds_mps"))

        active = [lc_active(intervals, v, t) for v in vids]
        lc_free = not any(active)
        max_abs_lat = (
            max(abs(x) for x in latv if np.isfinite(x))
            if any(np.isfinite(x) for x in latv)
            else np.inf
        )

        rows.append({
            "t_s": t,
            "lc_free": lc_free,
            "all_L0": bool(levels) and all(x == 0 for x in levels),
            "any_L1plus": any(x >= 1 for x in levels),
            "any_pushy": any(np.isfinite(x) and x > 0 for x in pushies),
            "max_abs_lat_speed": max_abs_lat,
        })

    c = pd.DataFrame(rows)

    strict_t = set(c["t_s"].astype(float))
    lcf = c[c["lc_free"]].copy()
    lc_t = set(lcf["t_s"].astype(float))
    l0_t = set(lcf.loc[lcf["all_L0"], "t_s"].astype(float))
    l1_t = set(lcf.loc[lcf["any_L1plus"], "t_s"].astype(float))
    p_t = set(lcf.loc[lcf["any_pushy"], "t_s"].astype(float))
    s005_t = set(lcf.loc[lcf["max_abs_lat_speed"] <= 0.05, "t_s"].astype(float))
    s010_t = set(lcf.loc[lcf["max_abs_lat_speed"] <= 0.10, "t_s"].astype(float))

    def pct(n: int) -> float:
        return 100.0 * n / sample_n if sample_n else np.nan

    return {
        "strict_network_time_pct": pct(len(strict_t)),
        "lc_free_network_time_pct": pct(len(lc_t)),
        "lc_free_share_of_strict_pct": (
            100.0 * len(lc_t) / len(strict_t) if strict_t else np.nan
        ),
        "lc_free_all_L0_network_time_pct": pct(len(l0_t)),
        "lc_free_any_L1plus_network_time_pct": pct(len(l1_t)),
        "lc_free_any_pushy_network_time_pct": pct(len(p_t)),
        "lc_free_latstable_005_network_time_pct": pct(len(s005_t)),
        "lc_free_latstable_010_network_time_pct": pct(len(s010_t)),
    }


# =============================================================================
# MECHANICAL AUDITS
# =============================================================================

def load_expected_results(jobs: list[dict]) -> pd.DataFrame:
    rows = []
    for j in jobs:
        p = Path(j["summary"])
        if not p.exists():
            rows.append({
                "run_id": j["run_id"],
                "error": "missing_summary",
            })
            continue
        try:
            rows.append(json.loads(p.read_text()))
        except Exception as e:
            rows.append({
                "run_id": j["run_id"],
                "error": f"summary_parse:{type(e).__name__}:{e}",
            })
    return pd.DataFrame(rows)


def assignment_crn_audit(jobs: list[dict]) -> tuple[bool, pd.DataFrame]:
    """
    Route/departure/elig_u assignments must be identical for all jobs sharing
    the same (demand, seed), independent of p, T_up or scenario.
    """
    rows = []
    for j in jobs:
        p = Path(j["assignment_file"])
        rows.append({
            "run_id": j["run_id"],
            "demand_vph": j["demand_vph"],
            "seed": j["seed"],
            "hash": sha256_path(p) if p.exists() else "",
        })

    d = pd.DataFrame(rows)
    grouped = (
        d.groupby(["demand_vph", "seed"], as_index=False)
        .agg(
            files=("run_id", "count"),
            unique_hashes=("hash", "nunique"),
            missing_hashes=("hash", lambda s: int((s == "").sum())),
        )
    )
    grouped["pass"] = (
        (grouped["unique_hashes"] == 1)
        & (grouped["missing_hashes"] == 0)
    )
    return bool(grouped["pass"].all()), grouped


def eligibility_formula_audit(runs: pd.DataFrame) -> tuple[bool, dict]:
    checked = 0
    mismatch = 0
    controls_nonzero = 0

    good = runs[runs["error"].fillna("") == ""].copy()

    for _, r in good.iterrows():
        vf = Path(str(r.get("vehicle_file", "")))
        if not vf.exists():
            mismatch += 1
            continue
        try:
            v = pd.read_csv(vf, compression="gzip", usecols=["elig_u", "eligible"])
        except Exception:
            mismatch += 1
            continue

        checked += len(v)
        if str(r["mode"]) == "CONTROL":
            controls_nonzero += int((v["eligible"].astype(int) != 0).sum())
        else:
            expected = (v["elig_u"].astype(float) < float(r["p"])).astype(int)
            mismatch += int((expected.to_numpy() != v["eligible"].astype(int).to_numpy()).sum())

    ok = mismatch == 0 and controls_nonzero == 0
    return ok, {
        "vehicle_rows_checked": int(checked),
        "dynamic_formula_mismatches": int(mismatch),
        "control_nonzero_eligible_rows": int(controls_nonzero),
        "pass": bool(ok),
    }


def reproducibility_audit(runs: pd.DataFrame) -> tuple[bool, pd.DataFrame]:
    rows = []
    keys = [
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

    for mode, p in [("CONTROL", 0.0), ("DYNAMIC", 1.0)]:
        core = runs[
            (runs["validation_layer"] == "CORE")
            & (runs["scenario"] == "S1")
            & (runs["demand_vph"] == 3200)
            & (runs["seed"] == 43)
            & (runs["mode"] == mode)
        ].copy()
        if mode == "DYNAMIC":
            core = core[
                np.isclose(core["p"].astype(float), 1.0)
                & np.isclose(core["T_up_s"].astype(float), 30.0)
            ]
        repro = runs[
            (runs["validation_layer"] == "REPRO")
            & (runs["mode"] == mode)
        ].copy()

        passed = len(core) == 1 and len(repro) == 1
        detail = {}
        if passed:
            a = core.iloc[0]
            b = repro.iloc[0]
            for k in keys:
                av = a.get(k, np.nan)
                bv = b.get(k, np.nan)
                if pd.isna(av) and pd.isna(bv):
                    kp = True
                elif isinstance(av, (str, bool)) or isinstance(bv, (str, bool)):
                    kp = av == bv
                else:
                    kp = bool(np.isclose(float(av), float(bv), rtol=0.0, atol=1e-12))
                detail[k] = kp
                passed = passed and kp

        rows.append({
            "mode": mode,
            "pass": bool(passed),
            "checks": json.dumps(detail, sort_keys=True),
        })

    d = pd.DataFrame(rows)
    return bool(d["pass"].all()), d


# =============================================================================
# SCIENTIFIC DIAGNOSTICS — NO POST-HOC PASS/FAIL THRESHOLDS
# =============================================================================

def core_condition_summary(runs: pd.DataFrame) -> pd.DataFrame:
    x = runs[
        (runs["validation_layer"] == "CORE")
        & (runs["error"].fillna("") == "")
    ].copy()

    x["condition"] = np.where(
        x["mode"] == "CONTROL",
        "CONTROL",
        x.apply(lambda r: f"p={r['p']:.2f},T={r['T_up_s']:.0f}", axis=1),
    )

    return (
        x.groupby(["scenario", "demand_vph", "condition"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            eligible_share=("eligible_share_departed", "mean"),
            escalated_share=("escalated_share_of_eligible", "mean"),
            reached_L4_mean=("reached_L4_n", "mean"),
            throughput_mean_vph=("throughput_vph", "mean"),
            completion_mean_pct=("completion_pct_of_departed", "mean"),
            collapse_count=("collapse", "sum"),
            collapse_time_mean_s=("collapse_time_s", "mean"),
            queue_mean_n=("mean_queue_halting", "mean"),
            tts_mean_vehicle_h=("tts_vehicle_h", "mean"),
            A_mean=("A_mean", "mean"),
            H_mean=("H_mean", "mean"),
            collisions=("collision_total", "sum"),
            teleports=("teleport_total", "sum"),
            ttc_lt1_mean_pct=("ttc_lt1_exposure_pct", "mean"),
            emergency_brake_mean_pct=("emergency_brake_exposure_pct", "mean"),
            max_drac_seed_max=("max_drac_mps2", "max"),
            secure_gap_deficit_seed_max=("max_secure_gap_deficit_m", "max"),
            pushy_exposure_mean_pct=("pushy_nonzero_vehicle_step_pct", "mean"),
        )
    )


def system_paired_effects(runs: pd.DataFrame) -> pd.DataFrame:
    core = runs[
        (runs["validation_layer"] == "CORE")
        & (runs["error"].fillna("") == "")
    ].copy()
    controls = core[core["mode"] == "CONTROL"].copy()
    dyn = core[core["mode"] == "DYNAMIC"].copy()

    rows = []
    for _, d in dyn.iterrows():
        c = controls[
            (controls["scenario"] == d["scenario"])
            & (controls["demand_vph"] == d["demand_vph"])
            & (controls["seed"] == d["seed"])
        ]
        if len(c) != 1:
            continue
        c = c.iloc[0]
        rows.append({
            "scenario": d["scenario"],
            "demand_vph": d["demand_vph"],
            "seed": d["seed"],
            "p": d["p"],
            "T_up_s": d["T_up_s"],
            "throughput_delta_vph": d["throughput_vph"] - c["throughput_vph"],
            "completion_delta_pp": (
                d["completion_pct_of_departed"] - c["completion_pct_of_departed"]
            ),
            "queue_delta_n": d["mean_queue_halting"] - c["mean_queue_halting"],
            "tts_delta_vehicle_h": d["tts_vehicle_h"] - c["tts_vehicle_h"],
            "collapse_dynamic": d["collapse"],
            "collapse_control": c["collapse"],
            "ttc_lt1_delta_pp": (
                d["ttc_lt1_exposure_pct"] - c["ttc_lt1_exposure_pct"]
            ),
            "emergency_brake_delta_pp": (
                d["emergency_brake_exposure_pct"] - c["emergency_brake_exposure_pct"]
            ),
            "collision_delta": d["collision_total"] - c["collision_total"],
            "teleport_delta": d["teleport_total"] - c["teleport_total"],
        })

    return pd.DataFrame(rows)


def vehicle_paired_effects(runs: pd.DataFrame) -> pd.DataFrame:
    """
    Paired same-ID dynamic-vs-all-L0 outcomes.

    ELIGIBLE is the susceptibility-assigned group (ITT-like within the dynamic
    assignment). NONELIGIBLE quantifies spillover/system effects. ESCALATED is
    post-treatment descriptive only and must not be treated as randomized.
    """
    core = runs[
        (runs["validation_layer"] == "CORE")
        & (runs["error"].fillna("") == "")
    ].copy()
    controls = core[core["mode"] == "CONTROL"].copy()
    dyn = core[core["mode"] == "DYNAMIC"].copy()

    rows = []
    cache: dict[str, pd.DataFrame] = {}

    def vf(path: str) -> pd.DataFrame:
        if path not in cache:
            cache[path] = model.load_vehicle_file(path)
        return cache[path]

    for _, d in dyn.iterrows():
        c = controls[
            (controls["scenario"] == d["scenario"])
            & (controls["demand_vph"] == d["demand_vph"])
            & (controls["seed"] == d["seed"])
        ]
        if len(c) != 1:
            continue
        c = c.iloc[0]

        dyn_v = vf(str(d["vehicle_file"]))
        ctrl_v = vf(str(c["vehicle_file"]))

        for group in ("ALL", "ELIGIBLE", "NONELIGIBLE", "ESCALATED"):
            m = model.paired_group_metrics(dyn_v, ctrl_v, group)
            rows.append({
                "scenario": d["scenario"],
                "demand_vph": d["demand_vph"],
                "seed": d["seed"],
                "p": d["p"],
                "T_up_s": d["T_up_s"],
                "group": group,
                **{k: v for k, v in m.items() if k != "group"},
                "interpretation_note": (
                    "ESCALATED is post-treatment descriptive; "
                    "ELIGIBLE/NONELIGIBLE comparisons occur under network interference."
                ),
            })

    return pd.DataFrame(rows)


def falsification_summary(runs: pd.DataFrame) -> pd.DataFrame:
    f = runs[
        (runs["validation_layer"].isin(["FALSIFICATION", "CORE"]))
        & (runs["error"].fillna("") == "")
    ].copy()

    # Keep p=1/T=30 dynamic and controls at 1800/2600/3000/3200.
    f = f[
        (f["mode"] == "CONTROL")
        | (
            (f["mode"] == "DYNAMIC")
            & np.isclose(f["p"].astype(float), 1.0)
            & np.isclose(f["T_up_s"].astype(float), 30.0)
        )
    ].copy()

    return (
        f.groupby(["scenario", "demand_vph", "mode"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            escalated_share=("escalated_share_of_eligible", "mean"),
            L4_share=(
                "reached_L4_n",
                lambda s: float(s.sum()),
            ),
            throughput_mean_vph=("throughput_vph", "mean"),
            completion_mean_pct=("completion_pct_of_departed", "mean"),
            collapse_count=("collapse", "sum"),
            queue_mean_n=("mean_queue_halting", "mean"),
            pushy_exposure_mean_pct=("pushy_nonzero_vehicle_step_pct", "mean"),
        )
    )


def third_row_summary(runs: pd.DataFrame) -> pd.DataFrame:
    q = runs[
        (runs["validation_layer"] == "CORE")
        & (runs["instrument_third_row"] == 1)
        & (runs["error"].fillna("") == "")
    ].copy()

    rows = []
    for _, r in q.iterrows():
        m = classify_third_row(r.to_dict())
        rows.append({
            "scenario": r["scenario"],
            "demand_vph": r["demand_vph"],
            "seed": r["seed"],
            "mode": r["mode"],
            "p": r["p"],
            "T_up_s": r["T_up_s"],
            **m,
        })

    d = pd.DataFrame(rows)
    if d.empty:
        return d

    return (
        d.groupby(["scenario", "demand_vph", "mode"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            strict_network_time_pct=("strict_network_time_pct", "mean"),
            lc_free_network_time_pct=("lc_free_network_time_pct", "mean"),
            lc_free_share_of_strict_pct=("lc_free_share_of_strict_pct", "mean"),
            lc_free_all_L0_network_time_pct=(
                "lc_free_all_L0_network_time_pct", "mean"
            ),
            lc_free_any_L1plus_network_time_pct=(
                "lc_free_any_L1plus_network_time_pct", "mean"
            ),
            lc_free_any_pushy_network_time_pct=(
                "lc_free_any_pushy_network_time_pct", "mean"
            ),
            lc_free_latstable_005_network_time_pct=(
                "lc_free_latstable_005_network_time_pct", "mean"
            ),
            lc_free_latstable_010_network_time_pct=(
                "lc_free_latstable_010_network_time_pct", "mean"
            ),
        )
    )


def resolution_summary(runs: pd.DataFrame) -> pd.DataFrame:
    core = runs[
        (runs["validation_layer"] == "CORE")
        & (runs["scenario"] == "S1")
        & (runs["demand_vph"] == 3200)
        & (runs["error"].fillna("") == "")
    ].copy()
    core = core[
        (core["mode"] == "CONTROL")
        | (
            (core["mode"] == "DYNAMIC")
            & np.isclose(core["p"].astype(float), 1.0)
            & np.isclose(core["T_up_s"].astype(float), 30.0)
        )
    ].copy()

    rob = runs[
        (runs["validation_layer"] == "RESOLUTION")
        & (runs["error"].fillna("") == "")
    ].copy()

    rows = []
    for _, rr in rob.iterrows():
        cc = core[
            (core["seed"] == rr["seed"])
            & (core["mode"] == rr["mode"])
        ]
        if len(cc) != 1:
            continue
        c = cc.iloc[0]
        rows.append({
            "seed": rr["seed"],
            "mode": rr["mode"],
            "primary_resolution_m": c["lateral_resolution_m"],
            "robust_resolution_m": rr["lateral_resolution_m"],
            "throughput_delta_vph": rr["throughput_vph"] - c["throughput_vph"],
            "completion_delta_pp": (
                rr["completion_pct_of_departed"] - c["completion_pct_of_departed"]
            ),
            "queue_delta_n": rr["mean_queue_halting"] - c["mean_queue_halting"],
            "tts_delta_vehicle_h": rr["tts_vehicle_h"] - c["tts_vehicle_h"],
            "escalated_share_delta": (
                rr["escalated_share_of_eligible"] - c["escalated_share_of_eligible"]
            ),
            "collision_primary": c["collision_total"],
            "collision_robust": rr["collision_total"],
            "teleport_primary": c["teleport_total"],
            "teleport_robust": rr["teleport_total"],
            "ttc_lt1_delta_pp": (
                rr["ttc_lt1_exposure_pct"] - c["ttc_lt1_exposure_pct"]
            ),
        })

    return pd.DataFrame(rows)


# =============================================================================
# REPORT / VALIDATION OUTPUT
# =============================================================================

def write_html_report(
    gate: dict,
    core_summary: pd.DataFrame,
    falsification: pd.DataFrame,
    third: pd.DataFrame,
    resolution: pd.DataFrame,
) -> None:
    def table(df: pd.DataFrame, n: int = 200) -> str:
        if df is None or df.empty:
            return "<p>No rows.</p>"
        return df.head(n).to_html(index=False, border=0, classes="dataframe")

    gate_rows = pd.DataFrame([
        {"gate": k, "value": v}
        for k, v in gate.items()
        if not isinstance(v, (dict, list))
    ])

    html_text = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>DBMR Final Validation V1</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif; margin: 32px; }}
h1,h2 {{ margin-top: 28px; }}
table {{ border-collapse: collapse; font-size: 12px; }}
th,td {{ border: 1px solid #ddd; padding: 5px 7px; }}
th {{ background: #f3f3f3; position: sticky; top: 0; }}
code {{ background: #f5f5f5; padding: 2px 4px; }}
.pass {{ font-weight: 700; }}
</style>
</head>
<body>
<h1>DBMR Final Validation V1</h1>
<p><b>Research question:</b> {html.escape(RESEARCH_QUESTION)}</p>
<p>Mechanical gates are implementation checks only. Scientific tables are descriptive and are not subjected to post-hoc pass/fail thresholds.</p>

<h2>Mechanical gate</h2>
{table(gate_rows)}

<h2>Core condition summary</h2>
{table(core_summary)}

<h2>Congestion falsification summary</h2>
{table(falsification)}

<h2>Lane-change-aware third-row attribution</h2>
{table(third)}

<h2>Lateral-resolution robustness</h2>
{table(resolution)}

</body></html>
"""
    (REPORT_DIR / "VALIDATION_REPORT.html").write_text(html_text, encoding="utf-8")


def analyze_and_gate(
    jobs: list[dict],
    pre: dict,
    levels: dict,
    level_source: str,
) -> None:
    runs = load_expected_results(jobs)
    runs.to_csv(OUT / "RUN_LEVEL_RESULTS.csv", index=False)

    good = runs[runs["error"].fillna("") == ""].copy()

    crn_ok, crn_detail = assignment_crn_audit(jobs)
    crn_detail.to_csv(REPORT_DIR / "CRN_ASSIGNMENT_AUDIT.csv", index=False)

    elig_ok, elig_detail = eligibility_formula_audit(runs)
    repro_ok, repro_detail = reproducibility_audit(runs)
    repro_detail.to_csv(REPORT_DIR / "REPRODUCIBILITY_AUDIT.csv", index=False)

    objective_checks = {
        "expected_runs": len(jobs),
        "successful_runs": int(len(good)),
        "all_runs_present": bool(len(good) == len(jobs)),
        "preflight_pass": bool(pre["preflight_pass"]),
        "exact_level_source_pass": bool(pre["exact_level_source_pass"]),
        "level_anchor_pass": bool(pre["level_anchor_pass"]),
        "network_lane_count_pass": bool(pre["lane_count_pass"]),
        "sumo_version_pass": bool(pre["sumo_version_pass"]),
        "all_architecture_pass": bool(
            len(good) == len(jobs)
            and good["architecture_pass"].fillna(0).astype(int).eq(1).all()
        ),
        "all_depart_L0": bool(
            len(good)
            and good["departure_not_l0_n"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_level_jumps": bool(
            len(good)
            and good["level_jump_violation_n"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_noneligible_transitions": bool(
            len(good)
            and good["noneligible_transition_n"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_type_mismatches": bool(
            len(good)
            and good["type_mismatch_n"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_pushy_update_errors": bool(
            len(good)
            and good["pushy_runtime_update_errors"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_highspeed_pushy": bool(
            len(good)
            and good["pushy_highspeed_nonzero_samples"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_fast_neighbor_pushy": bool(
            len(good)
            and good["pushy_fast_neighbor_nonzero_samples"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_no_neighbor_pushy": bool(
            len(good)
            and good["pushy_no_neighbor_nonzero_samples"].fillna(0).astype(int).eq(0).all()
        ),
        "zero_safety_query_errors": bool(
            len(good)
            and good["safety_query_errors"].fillna(0).astype(int).eq(0).all()
        ),
        "crn_assignment_integrity_pass": bool(crn_ok),
        "eligibility_formula_pass": bool(elig_ok),
        "reproducibility_pass": bool(repro_ok),
    }

    gate_pass = all(
        v for k, v in objective_checks.items()
        if isinstance(v, bool)
    )
    objective_checks["MECHANICAL_GATE_PASS"] = bool(gate_pass)
    objective_checks["scientific_status"] = "VALIDATION_COMPLETE"

    gate = {
        **objective_checks,
        "eligibility_audit": elig_detail,
        "level_source": level_source,
        "model_code_sha256": sha256_path(ROOT / "dbmr_final_model.py"),
        "validation_code_sha256": sha256_path(ROOT / "dbmr_final_validation.py"),
        "manifest_sha256": sha256_path(OUT / "VALIDATION_MANIFEST.csv"),
        "research_question": RESEARCH_QUESTION,
    }
    atomic_json(gate, REPORT_DIR / "MECHANICAL_GATE.json")

    # Scientific outputs
    core = core_condition_summary(runs)
    sys_pair = system_paired_effects(runs)
    veh_pair = vehicle_paired_effects(runs)
    fals = falsification_summary(runs)
    third = third_row_summary(runs)
    resol = resolution_summary(runs)

    core.to_csv(REPORT_DIR / "CORE_CONDITION_SUMMARY.csv", index=False)
    sys_pair.to_csv(REPORT_DIR / "SYSTEM_PAIRED_EFFECTS.csv", index=False)
    veh_pair.to_csv(REPORT_DIR / "VEHICLE_PAIRED_EFFECTS.csv", index=False)
    fals.to_csv(REPORT_DIR / "CONGESTION_FALSIFICATION.csv", index=False)
    third.to_csv(REPORT_DIR / "THIRD_ROW_ATTRIBUTION.csv", index=False)
    resol.to_csv(REPORT_DIR / "RESOLUTION_ROBUSTNESS.csv", index=False)

    # Validation specification export.
    spec = {
        "spec_name": "DBMR_VALIDATION_SPEC_EXPORT",
        "research_question": RESEARCH_QUESTION,
        "scientific_status": "VALIDATION_COMPLETE",
        "mechanical_gate_pass": bool(gate_pass),
        "sumo_version": pre["sumo_version"],
        "step_s": model.STEP,
        "poll_s": model.POLL_DT,
        "primary_lateral_resolution_m": PRIMARY_LATERAL_RESOLUTION_M,
        "pressure_model": {
            "speed_deficit": "1-speed/allowed_speed",
            "wait_share": "accumulated_wait/elapsed",
            "delay_share": "excess/(excess+freeflow)",
            "local_halt": "halting_vehicles/vehicles_on_edge",
            "memory_burden": "1-(1-wait_share)*(1-delay_share)",
            "local_context": "1-(1-speed_deficit)*(1-local_halt)",
            "P": "memory_burden*local_context",
            "q_up": "1-exp(-(P/T_up)*dt)",
        },
        "susceptibility": {
            "meaning_of_p": "probability/fraction susceptible to escalation, not assertive at entry",
            "p_values": P_VALUES,
            "all_vehicles_start_L0": True,
        },
        "T_up_sensitivity_s": T_UP_VALUES,
        "contextual_lcPushy": {
            "speed_cutoff_kmh": model.PUSHY_SPEED_CUTOFF_KMH,
            "neighbor_radius_m": model.PUSHY_NEIGHBOR_RADIUS_M,
            "rule": (
                "level amplitude active only if ego<10 km/h, >=1 neighbor within "
                "10 m, and every neighbor in radius <10 km/h; else 0"
            ),
            "amplitude_by_level": model.LC_PUSHY_BY_LEVEL,
        },
        "locked_freeflow_s": LOCKED_FREEFLOW_S,
        "scenarios": {
            s: scenario_exit_lanes(s) for s in SCENARIOS
        },
        "levels": {
            str(k): {kk: model._clean_value(vv) for kk, vv in v.items()}
            for k, v in levels.items()
        },
        "level_source": level_source,
        "network_hashes": pre["network_hashes"],
        "model_code_sha256": gate["model_code_sha256"],
        "validation_code_sha256": gate["validation_code_sha256"],
        "manifest_sha256": gate["manifest_sha256"],
    }
    atomic_json(spec, OUT / "DBMR_VALIDATION_SPEC_EXPORT.json")

    if gate_pass:
        validation_complete = {
            "status": "VALIDATION_COMPLETE",
            "spec_sha256": sha256_obj(spec),
            "spec_file": str(OUT / "DBMR_VALIDATION_SPEC_EXPORT.json"),
        }
        atomic_json(validation_complete, OUT / "VALIDATION_COMPLETE.json")

    write_html_report(gate, core, fals, third, resol)

    print()
    print("=" * 110)
    print("DBMR FINAL VALIDATION — MECHANICAL GATE")
    print("=" * 110)
    for k, v in objective_checks.items():
        print(f"{k:40s} {v}")

    print()
    print("Scientific diagnostics were exported without post-hoc pass/fail thresholds.")
    print("Primary files:")
    print(" ", REPORT_DIR / "VALIDATION_REPORT.html")
    print(" ", REPORT_DIR / "CORE_CONDITION_SUMMARY.csv")
    print(" ", REPORT_DIR / "SYSTEM_PAIRED_EFFECTS.csv")
    print(" ", REPORT_DIR / "VEHICLE_PAIRED_EFFECTS.csv")
    print(" ", REPORT_DIR / "CONGESTION_FALSIFICATION.csv")
    print(" ", REPORT_DIR / "THIRD_ROW_ATTRIBUTION.csv")
    print(" ", REPORT_DIR / "RESOLUTION_ROBUSTNESS.csv")
    print(" ", OUT / "DBMR_VALIDATION_SPEC_EXPORT.json")
    if gate_pass:
        print(" ", OUT / "FREEZE_READY.json")


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers",
        type=int,
        default=min(6, max(1, os.cpu_count() or 1)),
        help="Parallel SUMO worker processes. 6 is appropriate for the M4 MacBook.",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Do not start SUMO; rebuild reports from existing completed summaries.",
    )
    args = parser.parse_args()

    print("DBMR FINAL VALIDATION V1")
    print("=" * 110)
    print("Research question:")
    print(RESEARCH_QUESTION)
    print()
    print("This suite never redefines p as an assertive-at-entry share.")
    print("All vehicles enter L0; susceptibility only controls eligibility to escalate.")
    print()

    pf = preflight()
    pre = pf["preflight"]
    levels = pf["levels"]
    networks = pf["networks"]
    level_source = pf["level_source"]

    jobs = build_manifest(networks, levels)

    layer_counts = pd.Series([j["validation_layer"] for j in jobs]).value_counts()
    print("Manifest:")
    for layer, n in layer_counts.items():
        print(f"  {layer}: {n}")
    print(f"  TOTAL: {len(jobs)}")
    print()

    if not args.analyze_only:
        run_jobs(jobs, args.workers)

    analyze_and_gate(
        jobs=jobs,
        pre=pre,
        levels=levels,
        level_source=level_source,
    )


if __name__ == "__main__":
    main()
