#!/usr/bin/env python3
"""Build the six frozen roundabout exit-capacity geometries used in the campaign."""

from __future__ import annotations

import hashlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


SCENARIO_ORDER = ["S1", "S4", "S5", "S2", "S6", "S3"]

SCENARIO_LANES = {
    "S1": {"N": 1, "E": 1, "S": 1, "W": 1},
    "S2": {"N": 2, "E": 1, "S": 2, "W": 1},  # opposite double exits
    "S3": {"N": 2, "E": 2, "S": 2, "W": 2},

    "S4": {"N": 2, "E": 1, "S": 1, "W": 1},  # one double exit
    "S5": {"N": 2, "E": 2, "S": 1, "W": 1},  # two adjacent double exits
    "S6": {"N": 2, "E": 2, "S": 2, "W": 1},  # three double exits
}

SCENARIO_META = {
    "S1": {"double_exit_n": 0, "pattern": "all_single", "existing_anchor": True},
    "S4": {"double_exit_n": 1, "pattern": "one_double_N", "existing_anchor": False},
    "S5": {"double_exit_n": 2, "pattern": "two_adjacent_NE", "existing_anchor": False},
    "S2": {"double_exit_n": 2, "pattern": "two_opposite_NS", "existing_anchor": True},
    "S6": {"double_exit_n": 3, "pattern": "three_double_NES", "existing_anchor": False},
    "S3": {"double_exit_n": 4, "pattern": "all_double", "existing_anchor": True},
}


def scenario_exit_lanes(scenario: str) -> dict[str, int]:
    try:
        return dict(SCENARIO_LANES[str(scenario)])
    except KeyError:
        raise ValueError(f"Unknown six-scenario geometry: {scenario}")


def canonical_xml_sha256(path: Path) -> str:
    root = ET.parse(path).getroot()

    def normalize(elem):
        attrs = sorted(elem.attrib.items())
        elem.attrib.clear()
        for k, v in attrs:
            elem.set(k, v)
        if elem.text is not None and not elem.text.strip():
            elem.text = None
        if elem.tail is not None and not elem.tail.strip():
            elem.tail = None
        for child in list(elem):
            normalize(child)

    normalize(root)
    raw = ET.tostring(root, encoding="utf-8", method="xml")
    return hashlib.sha256(raw).hexdigest()


def build_network(
    scenario: str,
    network_root: Path,
    *,
    netconvert_binary: str = "netconvert",
) -> Path:
    """Build one scenario; only exit lane counts vary across the family."""
    K = 77.5 / 70.0
    work = Path(network_root) / str(scenario)
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
            str(netconvert_binary),
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
