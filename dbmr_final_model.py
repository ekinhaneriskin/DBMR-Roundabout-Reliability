#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DBMR FINAL MODEL V1 — FROZEN-CANDIDATE VALIDATION ENGINE
==========================================================

Research question
-----------------
All vehicles enter traffic at the calibrated conservative state L0.
A stable fraction p of drivers is *susceptible* to adapting their behavior
under experienced congestion. Susceptible drivers may progress one level at a
time:

    L0 -> L1 -> L2 -> L3 -> L4

The transition mechanism is driven by a continuous personal congestion-pressure
index. There are NO fixed 15 s creep or 30/60/90/120 s delay thresholds.

This pilot directly asks two questions:

1) Do susceptible drivers actually escalate under congestion?
2) Compared with the same-seed all-L0 counterfactual, do eligible/escalated
   vehicles save travel time or waiting time, and what happens to system
   throughput, reliability, queues and collisions?

Important scientific status
---------------------------
The L0/L2/L4 behavior *states* are independently empirically calibrated.
L1/L3 are the established interpolated transition states.

The congestion -> escalation HAZARD is NOT claimed to be empirically calibrated.
V22-H/K showed that openDD does not support a clean temporal aggression hazard.
Therefore V22-N2 treats the response time scale as an explicit sensitivity:

    severe-congestion mean one-level response time T_up = 15, 30, 60 s

Under pressure P=1, lambda_up = 1/T_up. Under weaker pressure,

    lambda_up(t) = P(t) / T_up

and the one-second transition probability is

    q_up = 1 - exp[-lambda_up * dt].

Thus there are no hard waiting thresholds. The sensitivity grid is retained so
that the paper never hides the remaining transition-rate uncertainty.

Population parameter p
----------------------
p is NOT the initial aggressive share.

    eligible_i = 1(elig_u_i < p)

Every vehicle starts L0. Noneligible drivers remain L0. Eligible drivers may
escalate only if experienced congestion produces transition events.

Congestion-pressure index
-------------------------
At each 1 s poll, each active vehicle receives four bounded diagnostics:

    speed_deficit = 1 - speed / allowed_speed
    wait_share    = accumulated_wait / elapsed_time
    delay_share   = excess_elapsed / (excess_elapsed + freeflow_time)
    local_halt    = halting_vehicles / vehicles on current edge

They are combined without fitted weights:

    memory_burden = 1 - (1-wait_share)(1-delay_share)
    local_context = 1 - (1-speed_deficit)(1-local_halt)
    P             = memory_burden * local_context

P in [0,1]. Crucially, local congestion alone cannot trigger adaptation before
personal waiting/delay burden begins to accumulate.

Counterfactual design
---------------------
For each seed there is one CONTROL run with all vehicles fixed at L0.
Dynamic runs reuse the same route/departure and eligibility random streams.
Individual vehicle IDs are therefore pairable across CONTROL and DYNAMIC runs.

Default pilot
-------------
Scenario  : S1, all exits one lane
Demand    : 3200 veh/h
p         : .25, .50, .75, 1.00 (p=0 is represented by CONTROL)
T_up      : 15, 30, 60 s
Seeds     : 11, 43, 167
Runs      : 3 controls + 4*3*3 dynamic = 39

No openDD context model is inserted here. V22-N2 first establishes whether the
intended congestion-response mechanism itself works and whether it benefits
vehicles. openDD-based traffic-context calibration can be added only after this
mechanism audit is understood.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm


# =============================================================================
# PROJECT / EXPERIMENT SETTINGS
# =============================================================================

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "DBMR_FINAL_VALIDATION_V1"
SUMMARY_DIR = OUT / "summaries"
VEHICLE_DIR = OUT / "vehicles"
TS_DIR = OUT / "timeseries"
ASSIGNMENT_DIR = OUT / "assignments"
SCRATCH_DIR = OUT / "scratch"
NETWORK_DIR = OUT / "network_S1"

for _p in (OUT, SUMMARY_DIR, VEHICLE_DIR, TS_DIR, ASSIGNMENT_DIR, SCRATCH_DIR, NETWORK_DIR):
    _p.mkdir(parents=True, exist_ok=True)

SCENARIO = "S1"
DEMAND_VPH = 3200
P_VALUES = [0.25, 0.50, 0.75, 1.00]
T_UP_VALUES = [15.0, 30.0, 60.0]
SEEDS = [11, 43, 167]

STEP = 0.2
POLL_DT = 1.0
LOG_DT = 10.0
DEMAND_HORIZON_S = 3600.0
SIM_END_S = 4200.0
LATERAL_RESOLUTION = 0.05
WAITING_TIME_MEMORY_S = 4200.0

APPROACH_EDGES = ["N_in", "E_in", "S_in", "W_in"]
RING_EDGES = [
    "c0c7", "c7c6", "c6c5", "c5c4",
    "c4c3", "c3c2", "c2c1", "c1c0",
]
OUT_EDGES = ["N_out", "E_out", "S_out", "W_out"]
EXTERNAL_EDGES = APPROACH_EDGES + RING_EDGES + OUT_EDGES

# Established S1 free-flow travel times from the prior DBMR calibration/audit.
FREEFLOW_S = {
    "right": 30.6,
    "straight": 40.8,
    "left": 51.0,
}

# Same non-terminating collapse diagnostic used in the recent architecture audits.
COLLAPSE_NO_MOVE_S = 30.0
COLLAPSE_NO_ARRIVAL_S = 300.0
COLLAPSE_MIN_QUEUE = 6

# Restored calibrated / previously used pushy amplitudes for the five levels.
# L0/L2/L4 are anchor-linked; L1/L3 are transition amplitudes used in V21-O.
LC_PUSHY_BY_LEVEL = {
    0: 0.0,
    1: 0.040000000000,
    2: 0.080000000000,
    3: 0.135111973435,
    4: 0.1902239468693733,
}

# Final contextual lcPushy gate.
# lcPushy may be non-zero only when:
#   (i) ego speed < 10 km/h,
#   (ii) at least one neighboring vehicle exists within 10 m Euclidean radius,
#   (iii) every vehicle within that radius is also below 10 km/h.
# Otherwise effective lcPushy is forced to zero.
#
# The gate is evaluated every simulation step (0.2 s), not merely at the
# 1 Hz behavioral-transition poll, so high-speed pushy exposure cannot persist
# between pressure updates.
PUSHY_SPEED_CUTOFF_KMH = 10.0
PUSHY_SPEED_CUTOFF_MPS = PUSHY_SPEED_CUTOFF_KMH / 3.6
PUSHY_NEIGHBOR_RADIUS_M = 10.0
PUSHY_NEIGHBOR_RADIUS2_M2 = PUSHY_NEIGHBOR_RADIUS_M ** 2
PUSHY_GRID_CELL_M = PUSHY_NEIGHBOR_RADIUS_M

# Exact approach-only three-abreast audit.
#
# We do not call a mere longitudinal overlap of 3 vehicles "three-abreast".
# A strict event requires:
#   1) all vehicles are on the same regular two-lane APPROACH edge;
#   2) their longitudinal physical footprints share a common cross-section;
#   3) at that cross-section, three vehicle body-width intervals can be packed
#      laterally without physical overlap.
#
# Robustness is reported at three minimum lateral-clearance requirements.
TRUE_ABREAST_CLEARANCES_M = (0.00, 0.05, 0.10)
TRUE_ABREAST_SAMPLE_DT_S = STEP  # every 0.2 s

# Surrogate-safety definitions retained from the established V20/T1 audit.
SAFETY_EDGES = APPROACH_EDGES + RING_EDGES
FOLLOW_MAX_NET_GAP_M = 80.0
CLOSE_GAP_FOR_SECURE_M = 10.0
EMERGENCY_BRAKE_THRESHOLD_MPS2 = -4.5


# =============================================================================
# FIVE-LEVEL FALLBACK PARAMETERIZATION
# =============================================================================

# Used only if the exact V21-L level audit CSV is unavailable.
# These values reproduce the V21-L level-parameter audit shown in the project.
FALLBACK_LEVELS = {
    0: {
        "tau": 2.100000,
        "minGap": 3.00,
        "accel": 2.6,
        "decel": 4.500000,
        "apparentDecel": 4.500000,
        "emergencyDecel": 9.000000,
        "sigma": 0.200,
        "speedFactor": 0.695280,
        "speedDev": 0.040000,
        "minGapLat": 0.850000,
        "maxSpeedLat": 0.65,
        "lcSpeedGain": 0.600000,
        "lcKeepRight": 1.000000,
        "lcSublane": 0.750000,
        "lcAssertive": 0.750,
        "lcImpatience": 0.000,
        "lcSigma": 0.060,
        "actionStepLength": 0.2,
        "latAlignment": "center",
        "lcPushy": 0.0,
    },
    1: {
        "tau": 1.549865,
        "minGap": 2.50,
        "accel": 2.6,
        "decel": 4.386023,
        "apparentDecel": 4.386023,
        "emergencyDecel": 8.772046,
        "sigma": 0.325,
        "speedFactor": 0.716195,
        "speedDev": 0.028825,
        "minGapLat": 0.806472,
        "maxSpeedLat": 0.80,
        "lcSpeedGain": 0.323096,
        "lcKeepRight": 0.549338,
        "lcSublane": 0.745500,
        "lcAssertive": 0.925,
        "lcImpatience": 0.070,
        "lcSigma": 0.085,
        "actionStepLength": 0.2,
        "latAlignment": "center",
        "lcPushy": LC_PUSHY_BY_LEVEL[1],
    },
    2: {
        "tau": 0.9997294069267808,
        "minGap": 2.00,
        "accel": 2.6,
        "decel": 4.272045581135899,
        "apparentDecel": 4.272045581135899,
        "emergencyDecel": 8.544091162271798,
        "sigma": 0.450,
        "speedFactor": 0.737110,
        "speedDev": 0.0176493418775498,
        "minGapLat": 0.7629443684443833,
        "maxSpeedLat": 0.95,
        "lcSpeedGain": 0.0461922760756686,
        "lcKeepRight": 0.0986751696951687,
        "lcSublane": 0.7409993414394558,
        "lcAssertive": 1.100,
        "lcImpatience": 0.140,
        "lcSigma": 0.110,
        "actionStepLength": 0.2,
        "latAlignment": "nice",
        "lcPushy": LC_PUSHY_BY_LEVEL[2],
    },
    3: {
        "tau": 0.670135,
        "minGap": 1.75,
        "accel": 2.6,
        "decel": 4.451954,
        "apparentDecel": 4.451954,
        "emergencyDecel": 8.903908,
        "sigma": 0.585,
        "speedFactor": 0.758025,
        "speedDev": 0.043484,
        "minGapLat": 0.631472,
        "maxSpeedLat": 1.20,
        "lcSpeedGain": 0.026430,
        "lcKeepRight": 0.053407,
        "lcSublane": 0.870500,
        "lcAssertive": 1.450,
        "lcImpatience": 0.295,
        "lcSigma": 0.145,
        "actionStepLength": 0.2,
        "latAlignment": "nice",
        "lcPushy": LC_PUSHY_BY_LEVEL[3],
    },
    4: {
        "tau": 0.3405407074606046,
        "minGap": 1.50,
        "accel": 2.6,
        "decel": 4.631862507108599,
        "apparentDecel": 4.631862507108599,
        "emergencyDecel": 9.263725014217197,
        "sigma": 0.720,
        "speedFactor": 0.778940,
        "speedDev": 0.0693180462904274,
        "minGapLat": 0.500000,
        "maxSpeedLat": 1.45,
        "lcSpeedGain": 0.0066676387526094,
        "lcKeepRight": 0.0081387882991693,
        "lcSublane": 1.000000,
        "lcAssertive": 1.800,
        "lcImpatience": 0.450,
        "lcSigma": 0.180,
        "actionStepLength": 0.2,
        "latAlignment": "arbitrary",
        "lcPushy": LC_PUSHY_BY_LEVEL[4],
    },
}

NUMERIC_VTYPE_FIELDS = [
    "tau",
    "minGap",
    "accel",
    "decel",
    "apparentDecel",
    "emergencyDecel",
    "sigma",
    "speedFactor",
    "speedDev",
    "minGapLat",
    "maxSpeedLat",
    "lcSpeedGain",
    "lcKeepRight",
    "lcSublane",
    "lcAssertive",
    "lcImpatience",
    "lcSigma",
    "actionStepLength",
    "lcPushy",
]


# =============================================================================
# ROUTES
# =============================================================================

ROUTE_TEMPLATES = {
    "N": {
        "right": ["N_in", "c0c7", "c7c6", "W_out"],
        "straight": ["N_in", "c0c7", "c7c6", "c6c5", "c5c4", "S_out"],
        "left": ["N_in", "c0c7", "c7c6", "c6c5", "c5c4", "c4c3", "c3c2", "E_out"],
    },
    "E": {
        "right": ["E_in", "c2c1", "c1c0", "N_out"],
        "straight": ["E_in", "c2c1", "c1c0", "c0c7", "c7c6", "W_out"],
        "left": ["E_in", "c2c1", "c1c0", "c0c7", "c7c6", "c6c5", "c5c4", "S_out"],
    },
    "S": {
        "right": ["S_in", "c4c3", "c3c2", "E_out"],
        "straight": ["S_in", "c4c3", "c3c2", "c2c1", "c1c0", "N_out"],
        "left": ["S_in", "c4c3", "c3c2", "c2c1", "c1c0", "c0c7", "c7c6", "W_out"],
    },
    "W": {
        "right": ["W_in", "c6c5", "c5c4", "S_out"],
        "straight": ["W_in", "c6c5", "c5c4", "c4c3", "c3c2", "E_out"],
        "left": ["W_in", "c6c5", "c5c4", "c4c3", "c3c2", "c2c1", "c1c0", "N_out"],
    },
}


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def atomic_json(data: dict, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=True))
    os.replace(tmp, path)


def json_ok(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
        return not bool(d.get("error"))
    except Exception:
        return False


def mp_context():
    import multiprocessing as mp
    return mp.get_context("spawn")


def find_binary(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    raise FileNotFoundError(
        f"Could not find '{name}' on PATH. Activate the project venv and verify SUMO installation."
    )


def _clean_value(v):
    if isinstance(v, np.generic):
        return v.item()
    return v


def _type_matches_level(type_id: str, level: int) -> bool:
    """
    Per-vehicle vType mutations create a SUMO singular type such as
    'L1@veh123'. Both the base type and its singular derivative represent
    the same DBMR behavioral level.
    """
    base = f"L{int(level)}"
    type_id = str(type_id)
    return type_id == base or type_id.startswith(base + "@")


def _readback_matches_2dp(observed: float, target: float, tol: float = 1e-9) -> bool:
    """
    SUMO 1.27.1 on this project build returns these runtime lateral values
    at two-decimal display/read-back precision. The setter itself receives
    the full double. Audit against the observable round-trip value rather
    than against the unrounded target.
    """
    return abs(float(observed) - round(float(target), 2)) <= tol


def stable_uniform(*parts) -> float:
    """Deterministic U(0,1), independent of Python hash randomization."""
    token = "|".join(str(x) for x in parts)
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return (n + 0.5) / float(2**64)


def pressure_index(
    speed_mps: float,
    allowed_speed_mps: float,
    accumulated_wait_s: float,
    elapsed_s: float,
    freeflow_s: float,
    local_halt_ratio: float,
) -> tuple[float, float, float, float, float, float, float]:
    """
    Continuous congestion-pressure index; no hard transition threshold.

    Returns:
      P, speed_deficit, wait_share, delay_share,
      local_halt_ratio, memory_burden, local_context
    """
    allowed = max(float(allowed_speed_mps), 0.1)
    speed_deficit = float(np.clip(1.0 - float(speed_mps) / allowed, 0.0, 1.0))

    elapsed = max(float(elapsed_s), POLL_DT)
    wait_share = float(np.clip(float(accumulated_wait_s) / elapsed, 0.0, 1.0))

    ff = max(float(freeflow_s), 1e-6)
    excess = max(0.0, float(elapsed_s) - ff)
    delay_share = float(excess / (excess + ff))

    local_halt_ratio = float(np.clip(local_halt_ratio, 0.0, 1.0))

    # No fitted weights and no step threshold.
    memory_burden = float(1.0 - (1.0 - wait_share) * (1.0 - delay_share))
    local_context = float(1.0 - (1.0 - speed_deficit) * (1.0 - local_halt_ratio))
    P = float(np.clip(memory_burden * local_context, 0.0, 1.0))

    return (
        P,
        speed_deficit,
        wait_share,
        delay_share,
        local_halt_ratio,
        memory_burden,
        local_context,
    )


def transition_probability(P: float, T_up_s: float, dt_s: float = POLL_DT) -> float:
    if not np.isfinite(P) or P <= 0.0:
        return 0.0
    if T_up_s <= 0.0:
        raise ValueError("T_up_s must be positive")
    lam = float(np.clip(P, 0.0, 1.0)) / float(T_up_s)
    return float(1.0 - math.exp(-lam * float(dt_s)))


# =============================================================================
# LOAD FIVE LEVELS
# =============================================================================

def load_levels() -> tuple[dict[int, dict], str]:
    csv_path = ROOT / "outputs" / "V21_L_DYNAMIC_DBMR_PILOT" / "V21L_LEVEL_PARAMETERS.csv"
    levels = {k: dict(v) for k, v in FALLBACK_LEVELS.items()}
    source = "embedded_fallback"

    if csv_path.exists():
        d = pd.read_csv(csv_path)
        if "level" not in d.columns:
            raise RuntimeError(f"Missing 'level' in {csv_path}")

        for level in range(5):
            g = d[pd.to_numeric(d["level"], errors="coerce") == level]
            if len(g) != 1:
                raise RuntimeError(
                    f"Expected exactly one L{level} row in {csv_path}; found {len(g)}"
                )
            row = g.iloc[0]
            for field in NUMERIC_VTYPE_FIELDS:
                if field in row.index and pd.notna(row[field]):
                    levels[level][field] = float(row[field])
            if "latAlignment" in row.index and pd.notna(row["latAlignment"]):
                levels[level]["latAlignment"] = str(row["latAlignment"])

        source = str(csv_path)

    # V21-L P0_ZERO stored lcPushy=0. Restore the calibrated/transition amplitudes.
    for level in range(5):
        levels[level]["lcPushy"] = float(LC_PUSHY_BY_LEVEL[level])
        levels[level]["actionStepLength"] = STEP
        if float(levels[level]["tau"]) <= STEP:
            raise RuntimeError(
                f"Invalid L{level}: tau={levels[level]['tau']} <= actionStepLength={STEP}"
            )

    return levels, source


def vtype_line(type_id: str, params: dict) -> str:
    attrs = {
        "id": type_id,
        "vClass": "passenger",
        "length": 5.0,
        "maxSpeed": 20.0,
    }
    for field in NUMERIC_VTYPE_FIELDS:
        if field in params and params[field] is not None:
            try:
                v = float(params[field])
            except Exception:
                continue
            if np.isfinite(v):
                attrs[field] = v
    attrs["latAlignment"] = params.get("latAlignment", "center")

    parts = []
    for k, v in attrs.items():
        if isinstance(v, float):
            sval = f"{v:.15g}"
        else:
            sval = str(v)
        parts.append(f'{k}="{sval}"')
    return "<vType " + " ".join(parts) + "/>"


# =============================================================================
# S1 NETWORK
# =============================================================================

def build_s1_network() -> Path:
    prior = ROOT / "outputs" / "V21_L_DYNAMIC_DBMR_PILOT" / "network_S1" / "S1.net.xml"
    if prior.exists():
        return prior

    net = NETWORK_DIR / "S1.net.xml"
    if net.exists():
        return net

    K = 77.5 / 70.0
    nodes = NETWORK_DIR / "nodes.nod.xml"
    edges = NETWORK_DIR / "edges.edg.xml"
    conns = NETWORK_DIR / "connections.con.xml"

    nodes.write_text(f'''<nodes>
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
''')

    edges.write_text('''<edges>
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
  <edge id="N_out" from="c0" to="N" numLanes="1" speed="16.7" priority="2"/>
  <edge id="E_out" from="c2" to="E" numLanes="1" speed="16.7" priority="2"/>
  <edge id="S_out" from="c4" to="S" numLanes="1" speed="16.7" priority="2"/>
  <edge id="W_out" from="c6" to="W" numLanes="1" speed="16.7" priority="2"/>
</edges>
''')

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

    exit_from = {"N": "c1c0", "E": "c3c2", "S": "c5c4", "W": "c7c6"}
    for d, src in exit_from.items():
        lines.append(f'<connection from="{src}" to="{d}_out" fromLane="0" toLane="0"/>')
        lines.append(f'<connection from="{src}" to="{d}_out" fromLane="1" toLane="0"/>')
    lines.append("</connections>")
    conns.write_text("\n".join(lines) + "\n")

    cmd = [
        find_binary("netconvert"),
        "-n", str(nodes),
        "-e", str(edges),
        "-x", str(conns),
        "--geometry.max-angle", "15",
        "--junctions.join", "false",
        "--roundabouts.guess", "false",
        "--no-turnarounds", "true",
        "--output-file", str(net),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"netconvert failed:\n{proc.stderr}")
    return net


# =============================================================================
# ROUTE GENERATION — EVERY VEHICLE STARTS L0
# =============================================================================

def write_routes(
    path: Path,
    assignment_path: Path,
    demand: int,
    horizon_s: float,
    seed: int,
    levels: dict[int, dict],
) -> dict:
    rng_route = random.Random(seed)
    rng_elig = random.Random((seed << 16) ^ int(demand * 1000))

    interval = 1.0 / max(demand / 3600.0, 1e-12)
    t = 0.0
    vid = 0
    assignment_rows = []

    with open(path, "w") as f:
        f.write("<routes>\n")
        for level in range(5):
            f.write("  " + vtype_line(f"L{level}", levels[level]) + "\n")

        while t <= horizon_s:
            app = rng_route.choice(["N", "E", "S", "W"])
            turn = rng_route.choice(["right", "straight", "left"])
            route = ROUTE_TEMPLATES[app][turn]
            elig_u = rng_elig.random()
            vehicle_id = f"veh{vid}"

            # IMPORTANT: every vehicle is physically inserted as L0.
            f.write(
                f'  <vehicle id="{vehicle_id}" type="L0" depart="{t:.1f}">'
                f'<route edges="{" ".join(route)}"/>'
                f'<param key="turn" value="{turn}"/>'
                f'<param key="approach" value="{app}"/>'
                f'<param key="elig_u" value="{elig_u:.12f}"/>'
                f'</vehicle>\n'
            )

            assignment_rows.append({
                "id": vehicle_id,
                "seed": seed,
                "demand_vph": demand,
                "elig_u": elig_u,
                "initial_level": 0,
                "depart_scheduled": round(t, 1),
                "approach": app,
                "turn": turn,
            })

            vid += 1
            t += interval * rng_route.uniform(0.9, 1.1)

        f.write("</routes>\n")

    assignment_df = pd.DataFrame(assignment_rows)
    assignment_df.to_csv(assignment_path, index=False)

    return {
        "scheduled_n": int(len(assignment_df)),
    }


# =============================================================================
# COMPOSITION METRICS
# =============================================================================

def behavior_metrics(level_counts: dict[int, int], active_n: int) -> tuple[float, float]:
    if active_n <= 0:
        return np.nan, np.nan

    pi = np.array([level_counts.get(k, 0) for k in range(5)], dtype=float) / float(active_n)
    A = float(sum((k / 4.0) * pi[k] for k in range(5)))
    H = float((1.0 - np.sum(pi ** 2)) / (1.0 - 1.0 / 5.0))
    H = float(np.clip(H, 0.0, 1.0))
    return A, H


def _lane_center_from_right_edge(
    lane_index: int,
    lane_widths: list[float],
    lateral_lane_pos_m: float,
) -> float:
    """
    Continuous vehicle-center coordinate measured leftward from the road's
    right edge.

    SUMO lane index 0 is the rightmost lane. getLateralLanePosition is the
    offset from the current lane center; positive is left in the sublane model.
    """
    i = int(lane_index)
    if i < 0 or i >= len(lane_widths):
        raise ValueError(f"invalid lane_index={i} for widths={lane_widths}")
    return (
        float(sum(lane_widths[:i]))
        + 0.5 * float(lane_widths[i])
        + float(lateral_lane_pos_m)
    )


def _max_lateral_nonoverlap_set(
    records: dict[str, dict],
    active_ids: set[str],
    clearance_m: float,
) -> list[str]:
    """
    Maximum-cardinality set of pairwise non-overlapping lateral body intervals.

    This is the classic interval-scheduling problem; greedy by interval upper
    endpoint is optimal for maximum cardinality.
    """
    intervals = []
    for vid in active_ids:
        r = records[vid]
        intervals.append((float(r["lat_hi"]), float(r["lat_lo"]), vid))
    intervals.sort()

    chosen = []
    last_hi = -np.inf
    for hi, lo, vid in intervals:
        if lo >= last_hi + float(clearance_m) - 1e-12:
            chosen.append(vid)
            last_hi = hi
    return chosen


def _true_abreast_on_edge(
    records: dict[str, dict],
    clearance_m: float,
) -> list[str]:
    """
    Return one maximum set of genuinely side-by-side vehicles on one approach.

    Vehicles must share a positive-length common longitudinal cross-section.
    Zero-length bumper contact is excluded by processing interval END events
    before START events at the same longitudinal coordinate.
    """
    if len(records) < 3:
        return []

    events = []
    for vid, r in records.items():
        events.append((float(r["rear_s"]), 0, vid))   # START handled second
        events.append((float(r["front_s"]), -1, vid)) # END handled first

    # encode START as +1 after END
    events2 = []
    for pos, flag, vid in events:
        if flag == 0:
            events2.append((pos, 1, vid))
        else:
            events2.append((pos, 0, vid))
    events2.sort(key=lambda x: (x[0], x[1]))

    active: set[str] = set()
    best: list[str] = []
    i = 0

    while i < len(events2):
        x = events2[i][0]

        # all ENDs at x
        while i < len(events2) and events2[i][0] == x and events2[i][1] == 0:
            active.discard(events2[i][2])
            i += 1

        # all STARTs at x
        while i < len(events2) and events2[i][0] == x and events2[i][1] == 1:
            active.add(events2[i][2])
            i += 1

        if len(active) >= 3:
            chosen = _max_lateral_nonoverlap_set(records, active, clearance_m)
            if len(chosen) > len(best):
                best = chosen

    return best


# =============================================================================
# ONE RUN
# =============================================================================

def run_one(job: dict) -> dict:
    import libsumo as traci

    mode = str(job["mode"])
    seed = int(job["seed"])
    p = float(job.get("p", 0.0))
    T_up = float(job.get("T_up", 30.0))
    run_id = str(job["run_id"])
    levels = {int(k): v for k, v in job["levels"].items()}

    scenario = str(job.get("scenario", SCENARIO))
    demand_vph = int(job.get("demand_vph", DEMAND_VPH))
    lateral_resolution = float(job.get("lateral_resolution", LATERAL_RESOLUTION))
    freeflow_s = {
        str(k): float(v)
        for k, v in job.get("freeflow_s", FREEFLOW_S).items()
    }
    instrument_third_row = bool(job.get("instrument_third_row", False))
    instrument_lanechange = bool(job.get("instrument_lanechange", instrument_third_row))
    validation_layer = str(job.get("validation_layer", "core"))

    work = Path(job["scratch"])
    work.mkdir(parents=True, exist_ok=True)

    rou = work / f"{run_id}.rou.xml"
    cfg = work / f"{run_id}.sumocfg"
    lanechange_file = work / f"{run_id}.lanechanges.xml"
    assignment_file = Path(job["assignment_file"])

    route_info = write_routes(
        rou,
        assignment_file,
        demand_vph,
        DEMAND_HORIZON_S,
        seed,
        levels,
    )

    cfg.write_text(f'''<configuration>
<input>
  <net-file value="{job['net']}"/>
  <route-files value="{rou}"/>
</input>
<time>
  <begin value="0"/>
  <end value="{SIM_END_S}"/>
  <step-length value="{STEP}"/>
</time>
<report>
  <no-step-log value="true"/>
</report>
</configuration>
''')

    vdata: dict[str, dict] = {}
    transition_rows = []
    timeseries = []
    collision_event_rows = []

    completed = 0
    collision_total = 0
    teleport_total = 0
    emergency_total = 0

    last_move_t = 0.0
    last_arrival_t = 0.0
    collapse_t = None
    collapse_reason = ""

    tts_vehicle_s = 0.0
    queue_sum = 0.0
    queue_n = 0
    A_sum = 0.0
    H_sum = 0.0
    comp_n = 0

    transition_total = 0
    level_jump_violation_n = 0
    noneligible_transition_n = 0
    departure_not_l0_n = 0
    type_mismatch_n = 0

    # Context-gated lcPushy audit.
    pushy_sample_n = 0
    pushy_nonzero_sample_n = 0
    pushy_effective_sum = 0.0
    pushy_activation_events = 0
    pushy_active_vehicle_ids = set()
    pushy_highspeed_nonzero_samples = 0
    pushy_fast_neighbor_nonzero_samples = 0
    pushy_no_neighbor_nonzero_samples = 0
    pushy_runtime_update_errors = 0

    # Surrogate safety, sampled at the existing 1 Hz behavioral poll.
    safety_vehicle_seconds = 0
    ttc_lt1_vehicle_seconds = 0
    min_ttc_s = np.inf
    max_drac_mps2 = 0.0
    min_physical_gap_m = np.inf
    max_secure_gap_deficit_m = 0.0
    emergency_brake_vehicle_seconds = 0
    safety_query_errors = 0

    # True three-abreast audit at every 0.2 s on the four straight approaches.
    abreast_network_sample_n = 0
    abreast_any_step_n = {c: 0 for c in TRUE_ABREAST_CLEARANCES_M}
    abreast_edge_step_n = {c: 0 for c in TRUE_ABREAST_CLEARANCES_M}
    abreast_max_count = {c: 0 for c in TRUE_ABREAST_CLEARANCES_M}
    abreast_current_streak = {
        c: {e: 0 for e in APPROACH_EDGES}
        for c in TRUE_ABREAST_CLEARANCES_M
    }
    abreast_max_streak = {
        c: {e: 0 for e in APPROACH_EDGES}
        for c in TRUE_ABREAST_CLEARANCES_M
    }
    abreast_unique_vehicles = {c: set() for c in TRUE_ABREAST_CLEARANCES_M}
    abreast_event_rows = []
    lane_width_cache = {}

    # Approach-only lateral-position exposure.
    approach_vehicle_time_s = 0.0
    approach_lateral_sample_n = 0
    approach_abs_poslat_sum_m = 0.0
    approach_poslat_gt_025_n = 0
    approach_poslat_gt_050_n = 0

    last_log_t = 0.0
    error = ""

    try:
        _sumo_args = [
            find_binary("sumo"),
            "-c", str(cfg),
            "--seed", str(seed),
            "--no-step-log", "true",
            "--no-warnings", "true",
            "--xml-validation", "never",
            "--lateral-resolution", str(lateral_resolution),
            "--ignore-route-errors", "true",
            "--collision.mingap-factor", "0",
            "--collision.action", "teleport",
            "--time-to-teleport", "-1",
            "--waiting-time-memory", str(WAITING_TIME_MEMORY_S),
        ]
        if instrument_lanechange:
            _sumo_args += [
                "--lanechange-output", str(lanechange_file),
                "--lanechange-output.started", "true",
                "--lanechange-output.ended", "true",
            ]
        traci.start(_sumo_args)

        step_counter = 0
        poll_counter = 0

        while traci.simulation.getMinExpectedNumber() > 0:
            traci.simulationStep()
            step_counter += 1
            t = float(traci.simulation.getTime())

            # ---------------- departures ----------------
            for vid in traci.simulation.getDepartedIDList():
                try:
                    turn = str(traci.vehicle.getParameter(vid, "turn") or "straight")
                    approach = str(traci.vehicle.getParameter(vid, "approach") or "")
                    elig_u = float(traci.vehicle.getParameter(vid, "elig_u"))
                    type_id = str(traci.vehicle.getTypeID(vid))
                except Exception as e:
                    error = f"departure_audit:{vid}:{e}"
                    raise

                if type_id != "L0":
                    departure_not_l0_n += 1

                eligible = int(mode == "DYNAMIC" and elig_u < p)

                vdata[vid] = {
                    "id": vid,
                    "turn": turn,
                    "approach": approach,
                    "elig_u": elig_u,
                    "eligible": eligible,
                    "depart_time": t,
                    "level": 0,
                    "max_level": 0,
                    "ever_escalated": 0,
                    "transition_count": 0,
                    "first_escalation_time": np.nan,
                    "first_escalation_elapsed": np.nan,
                    "pressure_sum": 0.0,
                    "pressure_n": 0,
                    "pressure_max": 0.0,
                    "pressure_auc": 0.0,
                    "last_pressure": 0.0,
                    "last_wait_s": 0.0,
                    "last_speed_mps": np.nan,
                    "last_allowed_speed_mps": np.nan,
                    "arrival_time": np.nan,
                    "arrived": 0,
                    "teleport_seen": 0,
                    "effective_pushy": 0.0,
                    "pushy_active_prev": 0,
                    "level_time_0": 0.0,
                    "level_time_1": 0.0,
                    "level_time_2": 0.0,
                    "level_time_3": 0.0,
                    "level_time_4": 0.0,
                }

            # ---------------- arrival/teleport/collision ----------------
            arrived_ids = tuple(traci.simulation.getArrivedIDList())
            completed += len(arrived_ids)
            if arrived_ids:
                last_arrival_t = t
                for vid in arrived_ids:
                    if vid in vdata:
                        vdata[vid]["arrival_time"] = t
                        vdata[vid]["arrived"] = 1

            tp_ids = tuple(traci.simulation.getStartingTeleportIDList())
            teleport_total += len(tp_ids)
            for vid in tp_ids:
                if vid in vdata:
                    vdata[vid]["teleport_seen"] = 1

            try:
                cols = tuple(traci.simulation.getCollisions())
            except Exception:
                cols = ()
            collision_total += len(cols)
            for _c in cols:
                _collider = str(getattr(_c, "collider", ""))
                _victim = str(getattr(_c, "victim", ""))
                collision_event_rows.append({
                    "run_id": run_id,
                    "validation_layer": validation_layer,
                    "scenario": scenario,
                    "demand_vph": demand_vph,
                    "mode": mode,
                    "seed": seed,
                    "p": p,
                    "T_up_s": T_up if mode == "DYNAMIC" else np.nan,
                    "time_s": t,
                    "collider": _collider,
                    "victim": _victim,
                    "collision_type": str(getattr(_c, "type", "")),
                    "lane": str(getattr(_c, "lane", "")),
                    "pos_m": float(getattr(_c, "pos", np.nan)),
                    "collider_speed_mps": float(getattr(_c, "colliderSpeed", np.nan)),
                    "victim_speed_mps": float(getattr(_c, "victimSpeed", np.nan)),
                    "collider_level": int(vdata.get(_collider, {}).get("level", -1)),
                    "victim_level": int(vdata.get(_victim, {}).get("level", -1)),
                    "collider_pushy": float(vdata.get(_collider, {}).get("effective_pushy", 0.0)),
                    "victim_pushy": float(vdata.get(_victim, {}).get("effective_pushy", 0.0)),
                })

            try:
                emergency_total += int(traci.simulation.getEmergencyStoppingVehiclesNumber())
            except Exception:
                pass

            # -------------------------------------------------------------
            # CONTEXT-GATED lcPushy — evaluated EVERY 0.2 s simulation step
            # -------------------------------------------------------------
            #
            # Build one spatial hash for all tracked active vehicles. This
            # avoids an O(N^2) all-pairs scan while preserving the intended
            # 10 m Euclidean neighborhood definition.
            gate_ids = (
                tuple(
                    vid for vid in traci.vehicle.getIDList()
                    if vid in vdata
                )
                if mode == "DYNAMIC"
                else ()
            )
            gate_speed = {}
            gate_pos = {}
            spatial = {}

            for _vid in gate_ids:
                try:
                    _s = float(traci.vehicle.getSpeed(_vid))
                    _x, _y = traci.vehicle.getPosition(_vid)
                    _x = float(_x)
                    _y = float(_y)
                except Exception:
                    continue

                gate_speed[_vid] = _s
                gate_pos[_vid] = (_x, _y)
                _cell = (
                    int(math.floor(_x / PUSHY_GRID_CELL_M)),
                    int(math.floor(_y / PUSHY_GRID_CELL_M)),
                )
                spatial.setdefault(_cell, []).append(_vid)

            for _vid in gate_ids:
                if _vid not in gate_speed or _vid not in gate_pos:
                    continue

                _v = vdata[_vid]
                _level = int(_v["level"])
                _ego_speed = float(gate_speed[_vid])
                _x, _y = gate_pos[_vid]

                _cx = int(math.floor(_x / PUSHY_GRID_CELL_M))
                _cy = int(math.floor(_y / PUSHY_GRID_CELL_M))

                _neighbors = []
                for _dx in (-1, 0, 1):
                    for _dy in (-1, 0, 1):
                        for _nid in spatial.get((_cx + _dx, _cy + _dy), ()):
                            if _nid == _vid or _nid not in gate_pos:
                                continue
                            _nx, _ny = gate_pos[_nid]
                            _d2 = (_nx - _x) ** 2 + (_ny - _y) ** 2
                            if _d2 <= PUSHY_NEIGHBOR_RADIUS2_M2:
                                _neighbors.append(_nid)

                _has_neighbor = len(_neighbors) > 0
                _all_neighbors_slow = bool(
                    _has_neighbor
                    and all(
                        float(gate_speed[_nid]) < PUSHY_SPEED_CUTOFF_MPS
                        for _nid in _neighbors
                        if _nid in gate_speed
                    )
                )
                _ego_slow = _ego_speed < PUSHY_SPEED_CUTOFF_MPS
                _context_on = bool(
                    _level > 0
                    and _ego_slow
                    and _has_neighbor
                    and _all_neighbors_slow
                )

                _target_pushy = (
                    float(LC_PUSHY_BY_LEVEL[_level])
                    if _context_on
                    else 0.0
                )
                _old_pushy = float(_v.get("effective_pushy", 0.0))

                if abs(_target_pushy - _old_pushy) > 1e-12:
                    try:
                        traci.vehicle.setParameter(
                            _vid,
                            "laneChangeModel.lcPushy",
                            str(_target_pushy),
                        )
                    except Exception as e:
                        pushy_runtime_update_errors += 1
                        error = f"context_pushy_update:{_vid}:L{_level}:{e}"
                        raise

                    if _old_pushy <= 0.0 and _target_pushy > 0.0:
                        pushy_activation_events += 1
                        pushy_active_vehicle_ids.add(_vid)

                    _v["effective_pushy"] = _target_pushy

                _effective = float(_v.get("effective_pushy", 0.0))
                pushy_sample_n += 1
                pushy_effective_sum += _effective

                if _effective > 0.0:
                    pushy_nonzero_sample_n += 1
                    if not _ego_slow:
                        pushy_highspeed_nonzero_samples += 1
                    if not _has_neighbor:
                        pushy_no_neighbor_nonzero_samples += 1
                    if _has_neighbor and not _all_neighbors_slow:
                        pushy_fast_neighbor_nonzero_samples += 1

                _v["pushy_active_prev"] = int(_effective > 0.0)

            if instrument_third_row:
                # -------------------------------------------------------------
                # TRUE THREE-ABREAST AUDIT — EVERY 0.2 s, APPROACH EDGES ONLY
                # -------------------------------------------------------------
                abreast_network_sample_n += 1
                _network_hit = {c: False for c in TRUE_ABREAST_CLEARANCES_M}

                for _edge in APPROACH_EDGES:
                    try:
                        _edge_ids = tuple(traci.edge.getLastStepVehicleIDs(_edge))
                    except Exception:
                        _edge_ids = ()

                    if _edge not in lane_width_cache:
                        try:
                            _nl = int(traci.edge.getLaneNumber(_edge))
                            lane_width_cache[_edge] = [
                                float(traci.lane.getWidth(f"{_edge}_{i}"))
                                for i in range(_nl)
                            ]
                        except Exception:
                            lane_width_cache[_edge] = [3.2, 3.2]

                    _widths = lane_width_cache[_edge]
                    _records = {}
                    approach_vehicle_time_s += len(_edge_ids) * STEP

                    for _vid in _edge_ids:
                        try:
                            _lane_i = int(traci.vehicle.getLaneIndex(_vid))
                            _pos_lat = float(
                                traci.vehicle.getLateralLanePosition(_vid)
                            )
                            approach_lateral_sample_n += 1
                            approach_abs_poslat_sum_m += abs(_pos_lat)
                            if abs(_pos_lat) > 0.25:
                                approach_poslat_gt_025_n += 1
                            if abs(_pos_lat) > 0.50:
                                approach_poslat_gt_050_n += 1
                            _veh_w = float(traci.vehicle.getWidth(_vid))
                            _front = float(traci.vehicle.getLanePosition(_vid))
                            _veh_len = float(traci.vehicle.getLength(_vid))
                            _lat_c = _lane_center_from_right_edge(
                                _lane_i, _widths, _pos_lat
                            )
                        except Exception:
                            continue

                        _records[_vid] = {
                            "front_s": _front,
                            "rear_s": _front - _veh_len,
                            "lat_center": _lat_c,
                            "lat_lo": _lat_c - 0.5 * _veh_w,
                            "lat_hi": _lat_c + 0.5 * _veh_w,
                            "lane_index": _lane_i,
                            "posLat": _pos_lat,
                            "width": _veh_w,
                        }

                    for _clear in TRUE_ABREAST_CLEARANCES_M:
                        _chosen = _true_abreast_on_edge(_records, _clear)
                        _count = len(_chosen)
                        abreast_max_count[_clear] = max(
                            abreast_max_count[_clear], _count
                        )

                        if _count >= 3:
                            _network_hit[_clear] = True
                            abreast_edge_step_n[_clear] += 1
                            abreast_current_streak[_clear][_edge] += 1
                            abreast_max_streak[_clear][_edge] = max(
                                abreast_max_streak[_clear][_edge],
                                abreast_current_streak[_clear][_edge],
                            )
                            abreast_unique_vehicles[_clear].update(_chosen)

                            # Keep event rows only for strict physical non-overlap;
                            # the 5/10 cm variants remain aggregate robustness metrics.
                            if _clear == 0.0:
                                _levels = [
                                    int(vdata.get(v, {}).get("level", -1))
                                    for v in _chosen
                                ]
                                _pushies = [
                                    float(vdata.get(v, {}).get("effective_pushy", 0.0))
                                    for v in _chosen
                                ]
                                _speeds = []
                                _lat_speeds = []
                                for v in _chosen:
                                    try:
                                        _speeds.append(float(traci.vehicle.getSpeed(v)))
                                    except Exception:
                                        _speeds.append(np.nan)
                                    try:
                                        _lat_speeds.append(
                                            float(traci.vehicle.getLateralSpeed(v))
                                        )
                                    except Exception:
                                        _lat_speeds.append(np.nan)

                                abreast_event_rows.append({
                                    "run_id": run_id,
                                    "mode": mode,
                                    "seed": seed,
                                    "p": p,
                                    "T_up_s": T_up if mode == "DYNAMIC" else np.nan,
                                    "t_s": t,
                                    "edge": _edge,
                                    "n_abreast": _count,
                                    "vehicle_ids": "|".join(_chosen),
                                    "levels": "|".join(str(x) for x in _levels),
                                    "pushy_values": "|".join(
                                        f"{x:.6g}" for x in _pushies
                                    ),
                                    "speeds_mps": "|".join(
                                        f"{x:.6g}" if np.isfinite(x) else "nan"
                                        for x in _speeds
                                    ),
                                    "lateral_speeds_mps": "|".join(
                                        f"{x:.6g}" if np.isfinite(x) else "nan"
                                        for x in _lat_speeds
                                    ),
                                    "lane_indices": "|".join(
                                        str(_records[v]["lane_index"])
                                        for v in _chosen
                                    ),
                                    "lateral_centers_m_from_right_edge": "|".join(
                                        f"{_records[v]['lat_center']:.6f}"
                                        for v in _chosen
                                    ),
                                    "lateral_body_intervals_m": "|".join(
                                        f"{_records[v]['lat_lo']:.6f}:"
                                        f"{_records[v]['lat_hi']:.6f}"
                                        for v in _chosen
                                    ),
                                })
                        else:
                            abreast_current_streak[_clear][_edge] = 0

                for _clear in TRUE_ABREAST_CLEARANCES_M:
                    if _network_hit[_clear]:
                        abreast_any_step_n[_clear] += 1

            poll_steps = max(1, int(round(POLL_DT / STEP)))
            if step_counter % poll_steps != 0:
                if t >= SIM_END_S:
                    break
                continue

            poll_counter += 1
            current_ids = tuple(traci.vehicle.getIDList())

            # ---------------- network state cache ----------------
            edge_stats = {}
            total_edge_veh = 0
            total_edge_halt = 0
            for edge in EXTERNAL_EDGES:
                try:
                    nveh = int(traci.edge.getLastStepVehicleNumber(edge))
                    nhalt = int(traci.edge.getLastStepHaltingNumber(edge))
                except Exception:
                    nveh, nhalt = 0, 0
                edge_stats[edge] = (nveh, nhalt)
                total_edge_veh += nveh
                total_edge_halt += nhalt

            network_halt_ratio = (
                float(total_edge_halt / total_edge_veh)
                if total_edge_veh > 0
                else 0.0
            )

            level_counts = {k: 0 for k in range(5)}
            speeds = []
            pressure_vals = []
            eligible_active_n = 0
            any_move = False

            # ---------------- individual pressure + transition ----------------
            for vid in current_ids:
                if vid not in vdata:
                    continue

                v = vdata[vid]
                old_level = int(v["level"])

                try:
                    current_type = str(traci.vehicle.getTypeID(vid))
                    if not _type_matches_level(current_type, old_level):
                        type_mismatch_n += 1

                    speed = float(traci.vehicle.getSpeed(vid))
                    allowed = float(traci.vehicle.getAllowedSpeed(vid))
                    acc_wait = float(traci.vehicle.getAccumulatedWaitingTime(vid))
                    road = str(traci.vehicle.getRoadID(vid))
                except Exception:
                    continue

                elapsed = max(0.0, t - float(v["depart_time"]))
                ff = float(freeflow_s.get(v["turn"], freeflow_s["straight"]))

                if road in edge_stats and edge_stats[road][0] > 0:
                    local_halt = float(edge_stats[road][1] / edge_stats[road][0])
                else:
                    local_halt = network_halt_ratio

                (
                    P,
                    speed_deficit,
                    wait_share,
                    delay_share,
                    local_halt_ratio,
                    memory_burden,
                    local_context,
                ) = pressure_index(
                    speed,
                    allowed,
                    acc_wait,
                    elapsed,
                    ff,
                    local_halt,
                )

                v["pressure_sum"] += P
                v["pressure_n"] += 1
                v["pressure_auc"] += P * POLL_DT
                v["pressure_max"] = max(float(v["pressure_max"]), P)
                v["last_pressure"] = P
                v["last_wait_s"] = acc_wait
                v["last_speed_mps"] = speed
                v["last_allowed_speed_mps"] = allowed
                v[f"level_time_{old_level}"] += POLL_DT

                pressure_vals.append(P)
                speeds.append(speed)
                if speed > 0.3:
                    any_move = True

                if int(v["eligible"]) == 1:
                    eligible_active_n += 1

                # CONTROL: never transitions.
                # DYNAMIC: eligible drivers may move ONE level upward per poll.
                if (
                    mode == "DYNAMIC"
                    and int(v["eligible"]) == 1
                    and old_level < 4
                ):
                    q_up = transition_probability(P, T_up, POLL_DT)
                    u = stable_uniform("V22N2", seed, vid, poll_counter, old_level)

                    if u < q_up:
                        new_level = old_level + 1

                        if new_level - old_level != 1:
                            level_jump_violation_n += 1

                        try:
                            # IMPORTANT SUMO runtime semantics:
                            # setType updates the vehicle type id / ordinary vType attributes,
                            # but lane-change-model attributes are stored on the individual
                            # vehicle and are NOT refreshed by setType. Therefore every
                            # behavior-state transition explicitly updates the individual
                            # lane-change parameters as well.
                            traci.vehicle.setType(vid, f"L{new_level}")

                            lp = levels[new_level]

                            # Dedicated per-vehicle lateral setters.
                            traci.vehicle.setMinGapLat(vid, float(lp["minGapLat"]))
                            traci.vehicle.setMaxSpeedLat(vid, float(lp["maxSpeedLat"]))
                            traci.vehicle.setLateralAlignment(vid, str(lp.get("latAlignment", "center")))

                            # Individual lane-change-model parameters.
                            for _lc_key in (
                                "lcSpeedGain",
                                "lcKeepRight",
                                "lcSublane",
                                "lcAssertive",
                                "lcImpatience",
                                "lcSigma",
                            ):
                                traci.vehicle.setParameter(
                                    vid,
                                    f"laneChangeModel.{_lc_key}",
                                    str(float(lp[_lc_key])),
                                )

                            # lcPushy is NOT a persistent state attribute here.
                            # Reset it on every level transition. The 0.2-s
                            # contextual gate above is the only mechanism that
                            # may subsequently activate it.
                            traci.vehicle.setParameter(
                                vid,
                                "laneChangeModel.lcPushy",
                                "0.0",
                            )
                            v["effective_pushy"] = 0.0
                            v["pushy_active_prev"] = 0

                            type_after = str(traci.vehicle.getTypeID(vid))

                            # Runtime read-back audit.
                            #
                            # V22-R2 showed that this SUMO 1.27.1 build exposes
                            # minGapLat / lane-change numeric read-back at 2-decimal
                            # precision (e.g. 0.806472... -> 0.81). Therefore compare
                            # with the observable two-decimal round-trip value rather
                            # than treating that formatting/serialization as a failed
                            # update.
                            if not _readback_matches_2dp(
                                traci.vehicle.getMinGapLat(vid), lp["minGapLat"]
                            ):
                                raise RuntimeError(
                                    "minGapLat runtime update mismatch:"
                                    f"target={lp['minGapLat']}:"
                                    f"read={traci.vehicle.getMinGapLat(vid)}"
                                )
                            if not _readback_matches_2dp(
                                traci.vehicle.getMaxSpeedLat(vid), lp["maxSpeedLat"]
                            ):
                                raise RuntimeError(
                                    "maxSpeedLat runtime update mismatch:"
                                    f"target={lp['maxSpeedLat']}:"
                                    f"read={traci.vehicle.getMaxSpeedLat(vid)}"
                                )
                            if str(traci.vehicle.getLateralAlignment(vid)) != str(
                                lp.get("latAlignment", "center")
                            ):
                                raise RuntimeError(
                                    "latAlignment runtime update mismatch:"
                                    f"target={lp.get('latAlignment', 'center')}:"
                                    f"read={traci.vehicle.getLateralAlignment(vid)}"
                                )

                        except Exception as e:
                            error = f"runtime_level_update:{vid}:L{new_level}:{e}"
                            raise

                        # Per-vehicle setters may create a singular type id such
                        # as L1@veh123. This is expected and still represents L1.
                        if not _type_matches_level(type_after, new_level):
                            type_mismatch_n += 1

                        v["level"] = new_level
                        v["max_level"] = max(int(v["max_level"]), new_level)
                        v["ever_escalated"] = 1
                        v["transition_count"] += 1
                        transition_total += 1

                        if not np.isfinite(float(v["first_escalation_time"])):
                            v["first_escalation_time"] = t
                            v["first_escalation_elapsed"] = elapsed

                        transition_rows.append({
                            "run_id": run_id,
                            "seed": seed,
                            "p": p,
                            "T_up_s": T_up,
                            "id": vid,
                            "t_s": t,
                            "elapsed_s": elapsed,
                            "from_level": old_level,
                            "to_level": new_level,
                            "pressure": P,
                            "q_up": q_up,
                            "u_transition": u,
                            "speed_deficit": speed_deficit,
                            "wait_share": wait_share,
                            "delay_share": delay_share,
                            "local_halt_ratio": local_halt_ratio,
                            "memory_burden": memory_burden,
                            "local_context": local_context,
                            "accumulated_wait_s": acc_wait,
                            "speed_mps": speed,
                            "allowed_speed_mps": allowed,
                        })

                # Final current level after any transition this poll.
                final_level = int(v["level"])
                if int(v["eligible"]) == 0 and final_level != 0:
                    noneligible_transition_n += 1
                level_counts[final_level] += 1

            # -------------------------------------------------------------
            # 1 Hz SURROGATE-SAFETY INSTRUMENTATION
            # -------------------------------------------------------------
            # Same definitions used in the prior V20/T1 audits. This is
            # diagnostic safety evidence, not an empirical crash model.
            for _vid in current_ids:
                if _vid not in vdata:
                    continue
                try:
                    _road = str(traci.vehicle.getRoadID(_vid))
                    if _road not in SAFETY_EDGES:
                        continue

                    _speed = float(traci.vehicle.getSpeed(_vid))
                    _accel = float(traci.vehicle.getAcceleration(_vid))
                    safety_vehicle_seconds += 1

                    if _accel <= EMERGENCY_BRAKE_THRESHOLD_MPS2:
                        emergency_brake_vehicle_seconds += 1

                    _lead = traci.vehicle.getLeader(_vid, FOLLOW_MAX_NET_GAP_M)
                    if not (_lead and _lead[0]):
                        continue

                    _leader_id = str(_lead[0])
                    _net_gap = float(_lead[1])
                    _min_gap = float(traci.vehicle.getMinGap(_vid))
                    _physical_gap = _net_gap + _min_gap

                    if _physical_gap > 0.0:
                        min_physical_gap_m = min(min_physical_gap_m, _physical_gap)

                    try:
                        _leader_speed = float(traci.vehicle.getSpeed(_leader_id))
                    except Exception:
                        _leader_speed = np.nan

                    if np.isfinite(_leader_speed):
                        _closing = _speed - _leader_speed
                        if _closing > 0.0 and _physical_gap > 0.0:
                            _ttc = _physical_gap / _closing
                            _drac = (_closing * _closing) / (2.0 * _physical_gap)
                            min_ttc_s = min(min_ttc_s, _ttc)
                            max_drac_mps2 = max(max_drac_mps2, _drac)
                            if _ttc < 1.0:
                                ttc_lt1_vehicle_seconds += 1

                        if 0.0 < _physical_gap <= CLOSE_GAP_FOR_SECURE_M:
                            try:
                                _leader_decel = float(traci.vehicle.getDecel(_leader_id))
                                _secure = float(
                                    traci.vehicle.getSecureGap(
                                        _vid,
                                        _speed,
                                        _leader_speed,
                                        _leader_decel,
                                        _leader_id,
                                    )
                                )
                                max_secure_gap_deficit_m = max(
                                    max_secure_gap_deficit_m,
                                    _secure - _net_gap,
                                )
                            except Exception:
                                safety_query_errors += 1

                except Exception:
                    safety_query_errors += 1

            active_n = sum(level_counts.values())
            tts_vehicle_s += active_n * POLL_DT

            if any_move:
                last_move_t = t

            total_q = sum(edge_stats[e][1] for e in APPROACH_EDGES)
            ring_n = sum(edge_stats[e][0] for e in RING_EDGES)

            no_move = (t - last_move_t) > COLLAPSE_NO_MOVE_S
            no_arrivals = (t - last_arrival_t) > COLLAPSE_NO_ARRIVAL_S
            collapse_now = (
                t > 60.0
                and active_n > 0
                and (no_move or no_arrivals)
                and total_q > COLLAPSE_MIN_QUEUE
            )
            if collapse_now and collapse_t is None:
                collapse_t = t
                collapse_reason = "no_move" if no_move else "no_arrivals"

            A, H = behavior_metrics(level_counts, active_n)
            if np.isfinite(A):
                A_sum += A
                H_sum += H
                comp_n += 1

            queue_sum += total_q
            queue_n += 1

            if (t - last_log_t) >= (LOG_DT - 1e-9):
                timeseries.append({
                    "run_id": run_id,
                    "mode": mode,
                    "seed": seed,
                    "p": p,
                    "T_up_s": T_up if mode == "DYNAMIC" else np.nan,
                    "t_s": t,
                    "active_n": active_n,
                    "eligible_active_n": eligible_active_n,
                    "lvl0": level_counts[0],
                    "lvl1": level_counts[1],
                    "lvl2": level_counts[2],
                    "lvl3": level_counts[3],
                    "lvl4": level_counts[4],
                    "A_state": A,
                    "H_state": H,
                    "mean_pressure": float(np.mean(pressure_vals)) if pressure_vals else np.nan,
                    "p90_pressure": float(np.quantile(pressure_vals, 0.90)) if pressure_vals else np.nan,
                    "queue_halting": total_q,
                    "ring_n": ring_n,
                    "mean_speed_mps": float(np.mean(speeds)) if speeds else np.nan,
                    "network_halt_ratio": network_halt_ratio,
                    "transitions_so_far": transition_total,
                })
                last_log_t = t

            if t >= SIM_END_S:
                break

    except Exception as e:
        if not error:
            error = f"run_exception:{type(e).__name__}:{e}"

    finally:
        try:
            traci.close()
        except Exception:
            pass

    # ---------------- native lane-change output ----------------
    native_lc_total = 0
    native_lc_approach = 0
    native_lc_approach_vehicles = set()
    native_lc_reason = Counter()
    lanechange_parse_error = ""

    if instrument_lanechange and lanechange_file.exists():
        try:
            _root = ET.parse(lanechange_file).getroot()
            for _ch in _root.findall("change"):
                native_lc_total += 1
                _from = str(_ch.attrib.get("from", ""))
                _edge = _from.rsplit("_", 1)[0] if "_" in _from else _from
                if _edge in APPROACH_EDGES:
                    native_lc_approach += 1
                    native_lc_approach_vehicles.add(
                        str(_ch.attrib.get("id", ""))
                    )
                    _reason = str(_ch.attrib.get("reason", "")).split("|")[0]
                    native_lc_reason[_reason] += 1
        except Exception as e:
            lanechange_parse_error = f"{type(e).__name__}:{e}"

    abreast_event_file = OUT / f"{run_id}_true_abreast_events.csv.gz"
    if instrument_third_row:
        pd.DataFrame(abreast_event_rows).to_csv(
            abreast_event_file, index=False, compression="gzip"
        )
    else:
        abreast_event_file = Path("")

    collision_event_file = OUT / f"{run_id}_collision_events.csv"
    pd.DataFrame(collision_event_rows).to_csv(collision_event_file, index=False)

    # ---------------- vehicle file ----------------
    end_t = min(SIM_END_S, max([0.0] + [float(x.get("arrival_time", 0.0)) for x in vdata.values() if np.isfinite(x.get("arrival_time", np.nan))]))
    if end_t <= 0:
        end_t = SIM_END_S

    vehicle_rows = []
    for vid, v in vdata.items():
        arrived = int(v["arrived"])
        depart_t = float(v["depart_time"])
        arrival_t = float(v["arrival_time"]) if arrived else np.nan
        observed_end = arrival_t if arrived else SIM_END_S
        duration = arrival_t - depart_t if arrived else np.nan
        ff = float(freeflow_s.get(v["turn"], freeflow_s["straight"]))
        delay = max(0.0, duration - ff) if arrived else np.nan

        vehicle_rows.append({
            "run_id": run_id,
            "mode": mode,
            "seed": seed,
            "p": p,
            "T_up_s": T_up if mode == "DYNAMIC" else np.nan,
            "id": vid,
            "turn": v["turn"],
            "approach": v["approach"],
            "elig_u": v["elig_u"],
            "eligible": int(v["eligible"]),
            "depart_time": depart_t,
            "arrived": arrived,
            "arrival_time": arrival_t,
            "duration_s": duration,
            "freeflow_s": ff,
            "delay_s": delay,
            "accumulated_wait_s": float(v["last_wait_s"]),
            "teleport_seen": int(v["teleport_seen"]),
            "effective_pushy_end": float(v.get("effective_pushy", 0.0)),
            "ever_escalated": int(v["ever_escalated"]),
            "max_level": int(v["max_level"]),
            "level_end": int(v["level"]),
            "transition_count": int(v["transition_count"]),
            "first_escalation_time": v["first_escalation_time"],
            "first_escalation_elapsed": v["first_escalation_elapsed"],
            "pressure_mean": (
                float(v["pressure_sum"] / v["pressure_n"])
                if int(v["pressure_n"]) > 0
                else np.nan
            ),
            "pressure_max": float(v["pressure_max"]),
            "pressure_auc": float(v["pressure_auc"]),
            "follow_time_s": max(0.0, observed_end - depart_t),
            "level_time_0": float(v["level_time_0"]),
            "level_time_1": float(v["level_time_1"]),
            "level_time_2": float(v["level_time_2"]),
            "level_time_3": float(v["level_time_3"]),
            "level_time_4": float(v["level_time_4"]),
        })

    veh_df = pd.DataFrame(vehicle_rows)
    veh_file = VEHICLE_DIR / f"{run_id}_vehicles.csv.gz"
    veh_df.to_csv(veh_file, index=False, compression="gzip")

    trans_file = OUT / f"{run_id}_transitions.csv.gz"
    pd.DataFrame(transition_rows).to_csv(trans_file, index=False, compression="gzip")

    ts_file = TS_DIR / f"{run_id}_timeseries.csv.gz"
    pd.DataFrame(timeseries).to_csv(ts_file, index=False, compression="gzip")

    departed_n = int(len(veh_df))
    eligible_n = int(veh_df["eligible"].sum()) if departed_n else 0
    escalated_n = int(veh_df["ever_escalated"].sum()) if departed_n else 0

    max_level_counts = {
        k: int((veh_df["max_level"] == k).sum()) if departed_n else 0
        for k in range(5)
    }

    architecture_pass = int(
        departure_not_l0_n == 0
        and level_jump_violation_n == 0
        and noneligible_transition_n == 0
        and type_mismatch_n == 0
        and pushy_runtime_update_errors == 0
        and pushy_highspeed_nonzero_samples == 0
        and pushy_fast_neighbor_nonzero_samples == 0
        and pushy_no_neighbor_nonzero_samples == 0
        and safety_query_errors == 0
        and (not instrument_lanechange or lanechange_parse_error == "")
        and (mode != "CONTROL" or escalated_n == 0)
    )

    result = {
        "run_id": run_id,
        "mode": mode,
        "validation_layer": validation_layer,
        "scenario": scenario,
        "demand_vph": demand_vph,
        "lateral_resolution_m": lateral_resolution,
        "instrument_third_row": int(instrument_third_row),
        "instrument_lanechange": int(instrument_lanechange),
        "seed": seed,
        "p": p,
        "T_up_s": T_up if mode == "DYNAMIC" else np.nan,
        "scheduled_n": route_info["scheduled_n"],
        "departed_n": departed_n,
        "eligible_n": eligible_n,
        "eligible_share_departed": eligible_n / departed_n if departed_n else np.nan,
        "escalated_n": escalated_n,
        "escalated_share_of_eligible": escalated_n / eligible_n if eligible_n else 0.0,
        "reached_L1_n": int((veh_df["max_level"] >= 1).sum()) if departed_n else 0,
        "reached_L2_n": int((veh_df["max_level"] >= 2).sum()) if departed_n else 0,
        "reached_L3_n": int((veh_df["max_level"] >= 3).sum()) if departed_n else 0,
        "reached_L4_n": int((veh_df["max_level"] >= 4).sum()) if departed_n else 0,
        "maxL0_n": max_level_counts[0],
        "maxL1_n": max_level_counts[1],
        "maxL2_n": max_level_counts[2],
        "maxL3_n": max_level_counts[3],
        "maxL4_n": max_level_counts[4],
        "transition_total": transition_total,
        "completed": completed,
        "completion_pct_of_departed": 100.0 * completed / departed_n if departed_n else np.nan,
        "throughput_vph": completed / DEMAND_HORIZON_S * 3600.0,
        "collapse": int(collapse_t is not None),
        "collapse_time_s": collapse_t,
        "collapse_reason": collapse_reason,
        "collision_total": collision_total,
        "teleport_total": teleport_total,
        "emergency_stop_vehicle_events": emergency_total,
        "tts_vehicle_h": tts_vehicle_s / 3600.0,
        "mean_queue_halting": queue_sum / queue_n if queue_n else np.nan,
        "A_mean": A_sum / comp_n if comp_n else np.nan,
        "H_mean": H_sum / comp_n if comp_n else np.nan,
        "departure_not_l0_n": departure_not_l0_n,
        "level_jump_violation_n": level_jump_violation_n,
        "noneligible_transition_n": noneligible_transition_n,
        "type_mismatch_n": type_mismatch_n,
        "pushy_speed_cutoff_kmh": PUSHY_SPEED_CUTOFF_KMH,
        "pushy_neighbor_radius_m": PUSHY_NEIGHBOR_RADIUS_M,
        "pushy_sample_n": pushy_sample_n,
        "pushy_nonzero_sample_n": pushy_nonzero_sample_n,
        "pushy_nonzero_vehicle_step_pct": (
            100.0 * pushy_nonzero_sample_n / pushy_sample_n
            if pushy_sample_n else 0.0
        ),
        "effective_pushy_mean": (
            pushy_effective_sum / pushy_sample_n
            if pushy_sample_n else 0.0
        ),
        "pushy_activation_events": pushy_activation_events,
        "pushy_active_vehicle_n": len(pushy_active_vehicle_ids),
        "pushy_highspeed_nonzero_samples": pushy_highspeed_nonzero_samples,
        "pushy_fast_neighbor_nonzero_samples": pushy_fast_neighbor_nonzero_samples,
        "pushy_no_neighbor_nonzero_samples": pushy_no_neighbor_nonzero_samples,
        "pushy_runtime_update_errors": pushy_runtime_update_errors,

        "safety_vehicle_seconds": safety_vehicle_seconds,
        "ttc_lt1_vehicle_seconds": ttc_lt1_vehicle_seconds,
        "ttc_lt1_exposure_pct": (
            100.0 * ttc_lt1_vehicle_seconds / safety_vehicle_seconds
            if safety_vehicle_seconds else np.nan
        ),
        "min_ttc_s": min_ttc_s if np.isfinite(min_ttc_s) else np.nan,
        "max_drac_mps2": max_drac_mps2,
        "min_physical_gap_m": (
            min_physical_gap_m if np.isfinite(min_physical_gap_m) else np.nan
        ),
        "max_secure_gap_deficit_m": max_secure_gap_deficit_m,
        "emergency_brake_exposure_pct": (
            100.0 * emergency_brake_vehicle_seconds / safety_vehicle_seconds
            if safety_vehicle_seconds else np.nan
        ),
        "safety_query_errors": safety_query_errors,
        "collision_event_file": str(collision_event_file),

        "true_abreast_network_sample_n": abreast_network_sample_n,
        "true_abreast_any_step_pct_c0": (
            100.0 * abreast_any_step_n[0.0] / abreast_network_sample_n
            if abreast_network_sample_n else 0.0
        ),
        "true_abreast_any_step_pct_c005": (
            100.0 * abreast_any_step_n[0.05] / abreast_network_sample_n
            if abreast_network_sample_n else 0.0
        ),
        "true_abreast_any_step_pct_c010": (
            100.0 * abreast_any_step_n[0.10] / abreast_network_sample_n
            if abreast_network_sample_n else 0.0
        ),
        "true_abreast_edge_step_n_c0": abreast_edge_step_n[0.0],
        "true_abreast_edge_step_n_c005": abreast_edge_step_n[0.05],
        "true_abreast_edge_step_n_c010": abreast_edge_step_n[0.10],
        "true_abreast_max_count_c0": abreast_max_count[0.0],
        "true_abreast_max_count_c005": abreast_max_count[0.05],
        "true_abreast_max_count_c010": abreast_max_count[0.10],
        "true_abreast_max_duration_s_c0": (
            max(abreast_max_streak[0.0].values()) * STEP
        ),
        "true_abreast_max_duration_s_c005": (
            max(abreast_max_streak[0.05].values()) * STEP
        ),
        "true_abreast_max_duration_s_c010": (
            max(abreast_max_streak[0.10].values()) * STEP
        ),
        "true_abreast_unique_vehicle_n_c0": len(abreast_unique_vehicles[0.0]),
        "true_abreast_unique_vehicle_n_c005": len(abreast_unique_vehicles[0.05]),
        "true_abreast_unique_vehicle_n_c010": len(abreast_unique_vehicles[0.10]),
        "approach_vehicle_time_s": approach_vehicle_time_s,
        "approach_mean_abs_poslat_m": (
            approach_abs_poslat_sum_m / approach_lateral_sample_n
            if approach_lateral_sample_n else np.nan
        ),
        "approach_poslat_gt_025_pct": (
            100.0 * approach_poslat_gt_025_n / approach_lateral_sample_n
            if approach_lateral_sample_n else np.nan
        ),
        "approach_poslat_gt_050_pct": (
            100.0 * approach_poslat_gt_050_n / approach_lateral_sample_n
            if approach_lateral_sample_n else np.nan
        ),
        "native_lc_total": native_lc_total,
        "native_lc_approach": native_lc_approach,
        "native_lc_approach_vehicle_n": len(native_lc_approach_vehicles),
        "native_lc_approach_per_vehicle_min": (
            native_lc_approach / (approach_vehicle_time_s / 60.0)
            if approach_vehicle_time_s > 0 else np.nan
        ),
        "native_lc_reason_strategic": native_lc_reason.get("strategic", 0),
        "native_lc_reason_speedGain": native_lc_reason.get("speedGain", 0),
        "native_lc_reason_keepRight": native_lc_reason.get("keepRight", 0),
        "native_lc_reason_cooperative": native_lc_reason.get("cooperative", 0),
        "native_lc_reason_sublane": native_lc_reason.get("sublane", 0),
        "lanechange_parse_error": lanechange_parse_error,
        "lanechange_file": str(lanechange_file) if instrument_lanechange else "",
        "true_abreast_event_file": (
            str(abreast_event_file) if instrument_third_row else ""
        ),

        "architecture_pass": architecture_pass,
        "vehicle_file": str(veh_file),
        "transition_file": str(trans_file),
        "timeseries_file": str(ts_file),
        "assignment_file": str(assignment_file),
        "error": error,
    }

    for f in (rou, cfg):
        try:
            f.unlink()
        except Exception:
            pass

    return result


# =============================================================================
# PAIRED COUNTERFACTUAL ANALYSIS
# =============================================================================

def load_vehicle_file(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, compression="gzip")


def paired_group_metrics(dyn: pd.DataFrame, ctrl: pd.DataFrame, selector: str) -> dict:
    d = dyn.copy()
    c = ctrl.copy()

    if selector == "ELIGIBLE":
        d = d[d["eligible"] == 1].copy()
    elif selector == "ESCALATED":
        d = d[d["ever_escalated"] == 1].copy()
    elif selector == "NONELIGIBLE":
        d = d[d["eligible"] == 0].copy()
    elif selector == "ALL":
        pass
    else:
        raise ValueError(selector)

    # Common departed vehicle IDs. This avoids comparing vehicles that never
    # entered one of the two diverging simulations.
    m = d.merge(
        c[[
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

    if len(m) == 0:
        return {
            "group": selector,
            "common_departed_n": 0,
        }

    comp_dyn = float(m["arrived_dyn"].mean())
    comp_ctrl = float(m["arrived_ctrl"].mean())

    both = m[(m["arrived_dyn"] == 1) & (m["arrived_ctrl"] == 1)].copy()

    if len(both):
        both["delta_tt_s"] = both["duration_s_dyn"] - both["duration_s_ctrl"]
        both["delta_delay_s"] = both["delay_s_dyn"] - both["delay_s_ctrl"]
        both["delta_wait_s"] = both["accumulated_wait_s_dyn"] - both["accumulated_wait_s_ctrl"]

        mean_delta_tt = float(both["delta_tt_s"].mean())
        median_delta_tt = float(both["delta_tt_s"].median())
        mean_delta_wait = float(both["delta_wait_s"].mean())
        median_delta_wait = float(both["delta_wait_s"].median())
        saved_tt_pct = float(100.0 * (both["delta_tt_s"] < 0).mean())
        saved_wait_pct = float(100.0 * (both["delta_wait_s"] < 0).mean())
    else:
        mean_delta_tt = np.nan
        median_delta_tt = np.nan
        mean_delta_wait = np.nan
        median_delta_wait = np.nan
        saved_tt_pct = np.nan
        saved_wait_pct = np.nan

    return {
        "group": selector,
        "common_departed_n": int(len(m)),
        "both_arrived_n": int(len(both)),
        "completion_dyn_pct": 100.0 * comp_dyn,
        "completion_ctrl_pct": 100.0 * comp_ctrl,
        "completion_gain_pp": 100.0 * (comp_dyn - comp_ctrl),
        "dyn_only_arrived_n": int(((m["arrived_dyn"] == 1) & (m["arrived_ctrl"] == 0)).sum()),
        "ctrl_only_arrived_n": int(((m["arrived_dyn"] == 0) & (m["arrived_ctrl"] == 1)).sum()),
        "mean_delta_tt_s": mean_delta_tt,
        "median_delta_tt_s": median_delta_tt,
        "mean_delta_wait_s": mean_delta_wait,
        "median_delta_wait_s": median_delta_wait,
        "saved_tt_pct_of_both_arrived": saved_tt_pct,
        "saved_wait_pct_of_both_arrived": saved_wait_pct,
    }


# =============================================================================
# ANALYSIS / REPORT
# =============================================================================

def analyze(levels: dict[int, dict], level_source: str) -> None:
    rows = []
    for pth in sorted(SUMMARY_DIR.glob("*.json")):
        try:
            rows.append(json.loads(pth.read_text()))
        except Exception:
            pass

    if not rows:
        raise RuntimeError("No V22-N2 summaries found.")

    runs = pd.DataFrame(rows)
    runs = runs.sort_values(["mode", "T_up_s", "p", "seed"], na_position="first").reset_index(drop=True)
    runs.to_csv(OUT / "V22N2_RUNS.csv", index=False)

    good = runs[runs["error"].fillna("") == ""].copy()
    control = good[good["mode"] == "CONTROL"].copy()
    dynamic = good[good["mode"] == "DYNAMIC"].copy()

    # ---------------- architecture audit ----------------
    expected_controls = len(SEEDS)
    expected_dynamic = len(P_VALUES) * len(T_UP_VALUES) * len(SEEDS)
    expected_total = expected_controls + expected_dynamic

    audit = pd.DataFrame([{
        "expected_runs": expected_total,
        "successful_runs": len(good),
        "controls_expected": expected_controls,
        "controls_found": len(control),
        "dynamic_expected": expected_dynamic,
        "dynamic_found": len(dynamic),
        "all_runs_present": len(good) == expected_total,
        "all_architecture_pass": bool(len(good) and good["architecture_pass"].eq(1).all()),
        "all_depart_L0": int(good["departure_not_l0_n"].sum()) == 0 if len(good) else False,
        "zero_level_jumps": int(good["level_jump_violation_n"].sum()) == 0 if len(good) else False,
        "zero_noneligible_transitions": int(good["noneligible_transition_n"].sum()) == 0 if len(good) else False,
        "zero_type_mismatches": int(good["type_mismatch_n"].sum()) == 0 if len(good) else False,
        "controls_zero_escalation": int(control["escalated_n"].sum()) == 0 if len(control) else False,
        "V22N2_OVERALL_PASS": bool(
            len(good) == expected_total
            and len(good)
            and good["architecture_pass"].eq(1).all()
        ),
        "level_source": level_source,
    }])
    audit.to_csv(OUT / "V22N2_ARCHITECTURE_AUDIT.csv", index=False)

    # ---------------- dynamic system summary ----------------
    dyn_summary = (
        dynamic.groupby(["T_up_s", "p"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            eligible_share_departed=("eligible_share_departed", "mean"),
            escalated_share_of_eligible=("escalated_share_of_eligible", "mean"),
            reached_L2_mean=("reached_L2_n", "mean"),
            reached_L3_mean=("reached_L3_n", "mean"),
            reached_L4_mean=("reached_L4_n", "mean"),
            transition_total_mean=("transition_total", "mean"),
            throughput_mean_vph=("throughput_vph", "mean"),
            completion_mean_pct=("completion_pct_of_departed", "mean"),
            collapse_seed_count=("collapse", "sum"),
            collision_total=("collision_total", "sum"),
            teleport_total=("teleport_total", "sum"),
            tts_mean_vehicle_h=("tts_vehicle_h", "mean"),
            queue_mean_n=("mean_queue_halting", "mean"),
            A_mean=("A_mean", "mean"),
            H_mean=("H_mean", "mean"),
        )
    )
    dyn_summary["collapse_rate_pct"] = 100.0 * dyn_summary["collapse_seed_count"] / dyn_summary["seeds"]
    dyn_summary.to_csv(OUT / "V22N2_DYNAMIC_SYSTEM_SUMMARY.csv", index=False)

    # ---------------- paired system contrasts ----------------
    sys_rows = []
    ctrl_by_seed = {int(r.seed): r for _, r in control.iterrows()}
    for _, r in dynamic.iterrows():
        seed = int(r.seed)
        if seed not in ctrl_by_seed:
            continue
        c = ctrl_by_seed[seed]
        sys_rows.append({
            "seed": seed,
            "T_up_s": float(r.T_up_s),
            "p": float(r.p),
            "throughput_gain_vph": float(r.throughput_vph - c.throughput_vph),
            "completion_gain_pp": float(r.completion_pct_of_departed - c.completion_pct_of_departed),
            "tts_change_vehicle_h": float(r.tts_vehicle_h - c.tts_vehicle_h),
            "queue_change_n": float(r.mean_queue_halting - c.mean_queue_halting),
            "collision_change": int(r.collision_total - c.collision_total),
            "teleport_change": int(r.teleport_total - c.teleport_total),
            "dynamic_collapse": int(r.collapse),
            "control_collapse": int(c.collapse),
            "collapse_change": int(r.collapse - c.collapse),
        })

    sys_pair = pd.DataFrame(sys_rows)
    sys_pair.to_csv(OUT / "V22N2_SYSTEM_PAIRED_CONTRASTS.csv", index=False)

    sys_pair_summary = (
        sys_pair.groupby(["T_up_s", "p"], as_index=False)
        .agg(
            seeds=("seed", "nunique"),
            throughput_gain_mean_vph=("throughput_gain_vph", "mean"),
            throughput_gain_median_vph=("throughput_gain_vph", "median"),
            completion_gain_mean_pp=("completion_gain_pp", "mean"),
            tts_change_mean_vehicle_h=("tts_change_vehicle_h", "mean"),
            queue_change_mean_n=("queue_change_n", "mean"),
            collision_change_total=("collision_change", "sum"),
            collapse_change_sum=("collapse_change", "sum"),
        )
    )
    sys_pair_summary.to_csv(OUT / "V22N2_SYSTEM_PAIRED_SUMMARY.csv", index=False)

    # ---------------- paired individual outcomes ----------------
    indiv_rows = []
    control_vehicle_cache = {}

    for _, r in tqdm(dynamic.iterrows(), total=len(dynamic), desc="Pairing individual counterfactuals"):
        seed = int(r.seed)
        if seed not in ctrl_by_seed:
            continue

        if seed not in control_vehicle_cache:
            control_vehicle_cache[seed] = load_vehicle_file(ctrl_by_seed[seed].vehicle_file)

        ctrl_v = control_vehicle_cache[seed]
        dyn_v = load_vehicle_file(r.vehicle_file)
        if dyn_v.empty or ctrl_v.empty:
            continue

        for selector in ("ELIGIBLE", "ESCALATED", "NONELIGIBLE", "ALL"):
            m = paired_group_metrics(dyn_v, ctrl_v, selector)
            m.update({
                "run_id": r.run_id,
                "seed": seed,
                "T_up_s": float(r.T_up_s),
                "p": float(r.p),
            })
            indiv_rows.append(m)

    indiv = pd.DataFrame(indiv_rows)
    indiv.to_csv(OUT / "V22N2_INDIVIDUAL_PAIRED_RESULTS.csv", index=False)

    def summarize_group(group_name: str) -> pd.DataFrame:
        d = indiv[indiv["group"] == group_name].copy()
        if d.empty:
            return pd.DataFrame()
        out = (
            d.groupby(["T_up_s", "p"], as_index=False)
            .agg(
                seeds=("seed", "nunique"),
                common_departed_mean=("common_departed_n", "mean"),
                both_arrived_mean=("both_arrived_n", "mean"),
                completion_gain_mean_pp=("completion_gain_pp", "mean"),
                median_delta_tt_mean_s=("median_delta_tt_s", "mean"),
                mean_delta_tt_mean_s=("mean_delta_tt_s", "mean"),
                median_delta_wait_mean_s=("median_delta_wait_s", "mean"),
                mean_delta_wait_mean_s=("mean_delta_wait_s", "mean"),
                saved_tt_pct_mean=("saved_tt_pct_of_both_arrived", "mean"),
                saved_wait_pct_mean=("saved_wait_pct_of_both_arrived", "mean"),
            )
        )
        return out

    elig_summary = summarize_group("ELIGIBLE")
    esc_summary = summarize_group("ESCALATED")
    nonelig_summary = summarize_group("NONELIGIBLE")

    elig_summary.to_csv(OUT / "V22N2_BENEFIT_ELIGIBLE.csv", index=False)
    esc_summary.to_csv(OUT / "V22N2_BENEFIT_ESCALATED.csv", index=False)
    nonelig_summary.to_csv(OUT / "V22N2_EXTERNALITY_NONELIGIBLE.csv", index=False)

    # ---------------- transition profile ----------------
    trans_rows = []
    for _, r in dynamic.iterrows():
        pth = Path(r.transition_file)
        if not pth.exists():
            continue
        try:
            d = pd.read_csv(pth, compression="gzip")
        except pd.errors.EmptyDataError:
            continue
        if d.empty:
            continue
        trans_rows.append(d)

    trans = pd.concat(trans_rows, ignore_index=True) if trans_rows else pd.DataFrame()
    if not trans.empty:
        trans.to_csv(OUT / "V22N2_ALL_TRANSITIONS.csv.gz", index=False, compression="gzip")
        trans_profile = (
            trans.groupby(["T_up_s", "p", "from_level", "to_level"], as_index=False)
            .agg(
                transitions=("id", "size"),
                vehicles=("id", "nunique"),
                elapsed_median_s=("elapsed_s", "median"),
                pressure_mean=("pressure", "mean"),
                pressure_median=("pressure", "median"),
                wait_share_mean=("wait_share", "mean"),
                delay_share_mean=("delay_share", "mean"),
                local_halt_mean=("local_halt_ratio", "mean"),
            )
        )
    else:
        trans_profile = pd.DataFrame()

    trans_profile.to_csv(OUT / "V22N2_TRANSITION_PROFILE.csv", index=False)

    # ---------------- level audit ----------------
    level_df = pd.DataFrame([
        {"level": level, **{k: _clean_value(v) for k, v in levels[level].items()}}
        for level in range(5)
    ])
    level_df.to_csv(OUT / "V22N2_LEVELS_USED.csv", index=False)

    # ---------------- report ----------------
    report = (
        "DBMR V22-R — DYNAMIC CONGESTION-RESPONSE MECHANISM AUDIT\n"
        + "=" * 145
        + "\n\nMODEL STATUS\n"
        + "------------\n"
        + "All vehicles start L0. p is susceptibility, NOT initial aggression share.\n"
        + "Eligible vehicles may progress L0->L1->L2->L3->L4 via a continuous pressure-driven hazard.\n"
        + "No hard 15 s or 30/60/90/120 s escalation thresholds are used.\n"
        + "T_up=[15,30,60] s is an explicit hazard-timescale sensitivity, not an empirical calibration.\n"
        + f"Level source: {level_source}\n\n"
        + "ARCHITECTURE AUDIT\n"
        + "------------------\n"
        + audit.to_string(index=False)
        + "\n\nDYNAMIC SYSTEM SUMMARY\n"
        + "----------------------\n"
        + dyn_summary.to_string(index=False)
        + "\n\nSYSTEM PAIRED VS ALL-L0 CONTROL\n"
        + "-------------------------------\n"
        + sys_pair_summary.to_string(index=False)
        + "\n\nINDIVIDUAL BENEFIT — ELIGIBLE (DYNAMIC - CONTROL; negative time = saving)\n"
        + "----------------------------------------------------------------------------\n"
        + elig_summary.to_string(index=False)
        + "\n\nINDIVIDUAL BENEFIT — ESCALATED (descriptive exposed subset)\n"
        + "----------------------------------------------------------\n"
        + esc_summary.to_string(index=False)
        + "\n\nEXTERNALITY — NONELIGIBLE\n"
        + "-------------------------\n"
        + nonelig_summary.to_string(index=False)
        + "\n\nTRANSITION PROFILE\n"
        + "------------------\n"
        + trans_profile.to_string(index=False)
        + "\n"
    )

    (OUT / "REPORT.txt").write_text(report)

    print()
    print("DBMR V22-R — DYNAMIC CONGESTION-RESPONSE MECHANISM AUDIT")
    print("=" * 145)
    print()
    print("ARCHITECTURE AUDIT")
    print("-" * 145)
    print(audit.to_string(index=False))
    print()
    print("DYNAMIC SYSTEM SUMMARY")
    print("-" * 145)
    print(dyn_summary.to_string(index=False))
    print()
    print("SYSTEM PAIRED VS ALL-L0 CONTROL")
    print("-" * 145)
    print(sys_pair_summary.to_string(index=False))
    print()
    print("INDIVIDUAL BENEFIT — ELIGIBLE")
    print("negative delta time = dynamic response saved time")
    print("-" * 145)
    print(elig_summary.to_string(index=False))
    print()
    print("INDIVIDUAL BENEFIT — ESCALATED")
    print("descriptive exposed subset; negative delta time = saving")
    print("-" * 145)
    print(esc_summary.to_string(index=False))
    print()
    print("EXTERNALITY — NONELIGIBLE")
    print("-" * 145)
    print(nonelig_summary.to_string(index=False))
    print()
    print("TRANSITION PROFILE")
    print("-" * 145)
    print(trans_profile.to_string(index=False))
    print()
    print("Saved:", OUT)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers",
        type=int,
        default=min(6, max(1, os.cpu_count() or 1)),
        help="Parallel SUMO/libsumo workers (default: up to 6).",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Delete V22-N2 run summaries and rerun the 39-run pilot.",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Do not run SUMO; rebuild analysis from completed summaries/files.",
    )
    args = parser.parse_args()

    levels, level_source = load_levels()
    net = build_s1_network()

    print()
    print("DBMR V22-N2 — DYNAMIC CONGESTION-RESPONSE MECHANISM AUDIT")
    print("=" * 110)
    print("Network:", net)
    print("Level source:", level_source)
    print("p susceptibility values:", P_VALUES)
    print("T_up sensitivity (s):", T_UP_VALUES)
    print("Seeds:", SEEDS)
    print("Workers:", args.workers)
    print("All vehicles start L0: YES")
    print("Fixed initial aggressive share: NO")
    print("Temporal escalation: ENABLED, pressure-driven stochastic hazard")
    print("Hard creep/delay thresholds: NONE")
    print("openDD temporal aggression hazard: NOT CLAIMED / NOT USED")
    print("Counterfactual all-L0 control per seed: YES")

    pd.DataFrame([
        {"level": level, **levels[level]}
        for level in range(5)
    ]).to_csv(OUT / "V22N2_LEVELS_USED.csv", index=False)

    if args.rerun:
        for pth in SUMMARY_DIR.glob("*.json"):
            pth.unlink()

    if not args.analyze_only:
        jobs = []

        # One all-L0 counterfactual per seed. These are reused for every p/T_up.
        for seed in SEEDS:
            run_id = f"V22N2_CTRL_S1_d{DEMAND_VPH}_s{seed}"
            jobs.append({
                "run_id": run_id,
                "mode": "CONTROL",
                "seed": seed,
                "p": 0.0,
                "T_up": 30.0,
                "net": str(net),
                "scratch": str(SCRATCH_DIR),
                "assignment_file": str(ASSIGNMENT_DIR / f"{run_id}_assignments.csv"),
                "summary": str(SUMMARY_DIR / f"{run_id}.json"),
                "levels": levels,
            })

        for T_up in T_UP_VALUES:
            for p in P_VALUES:
                for seed in SEEDS:
                    tcode = int(round(T_up))
                    pcode = int(round(100 * p))
                    run_id = f"V22N2_DYN_T{tcode:03d}_p{pcode:03d}_s{seed}"
                    jobs.append({
                        "run_id": run_id,
                        "mode": "DYNAMIC",
                        "seed": seed,
                        "p": p,
                        "T_up": T_up,
                        "net": str(net),
                        "scratch": str(SCRATCH_DIR),
                        "assignment_file": str(ASSIGNMENT_DIR / f"{run_id}_assignments.csv"),
                        "summary": str(SUMMARY_DIR / f"{run_id}.json"),
                        "levels": levels,
                    })

        pending = [j for j in jobs if not json_ok(Path(j["summary"]))]

        print()
        print(
            f"Selected={len(jobs)} | complete={len(jobs)-len(pending)} | "
            f"pending={len(pending)}"
        )

        if pending:
            with ProcessPoolExecutor(
                max_workers=max(1, args.workers),
                mp_context=mp_context(),
            ) as ex:
                futs = {ex.submit(run_one, j): j for j in pending}

                for fut in tqdm(
                    as_completed(futs),
                    total=len(futs),
                    desc="V22-N2 dynamic congestion-response",
                    unit="run",
                ):
                    j = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = {
                            "run_id": j["run_id"],
                            "mode": j["mode"],
                            "scenario": SCENARIO,
                            "demand_vph": DEMAND_VPH,
                            "seed": j["seed"],
                            "p": j["p"],
                            "T_up_s": j["T_up"] if j["mode"] == "DYNAMIC" else np.nan,
                            "error": f"worker_exception:{type(e).__name__}:{e}",
                        }

                    atomic_json(res, Path(j["summary"]))

                    print(
                        f"DONE {j['mode']} "
                        f"T={j['T_up'] if j['mode']=='DYNAMIC' else '-'} "
                        f"p={j['p']:.2f} seed={j['seed']} | "
                        f"elig={res.get('eligible_share_departed', np.nan):.3f} | "
                        f"esc={res.get('escalated_share_of_eligible', np.nan):.3f} | "
                        f"L4={res.get('reached_L4_n', 'NA')} | "
                        f"q={res.get('throughput_vph', np.nan):.0f} | "
                        f"collapse={res.get('collapse', 'NA')} | "
                        f"coll={res.get('collision_total', 'NA')} | "
                        f"err={res.get('error', '')}"
                    )

    analyze(levels, level_source)


if __name__ == "__main__":
    main()
