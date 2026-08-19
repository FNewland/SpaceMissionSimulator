"""Fault-propagation graph for the alarm drawer (POSITION_NEEDS G6).

``configs/<mission>/fdir/fault_propagation.yaml`` encodes the cascade
ancestry the simulator flies, and until now was unused for display (FS5:
no first-out grouping in a cascade). This module parses it into a
node/edge graph the MCS serves at ``GET /api/fault-graph`` so the alarm
drawer can group "1 root cause, N consequent" client-side.

Two edge sets are served, and they are deliberately distinct:

* ``cascades`` — the RAW config: fault → (target subsystem, action). This
  is exactly what the file says and what the simulator executes.
* ``edges`` — DERIVED fault→fault ancestry: fault A is a parent of fault
  B when one of A's cascade actions targets the subsystem of B's primary
  parameter (the config cascades to subsystems, not to named faults, so
  fault-level ancestry is a ground-side inference; the response says so).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


def _subsystem_of(param_name: str) -> str:
    return param_name.split(".", 1)[0] if "." in param_name else ""


def load_fault_graph(config_dir: str | Path,
                     param_ids_by_name: Optional[dict[str, int]] = None) -> dict:
    """Parse fault_propagation.yaml into a display graph.

    ``param_ids_by_name`` (name → int id) lets each node carry the hex id
    of the parameter its ground/on-board alarm rides on; absent names get
    ``param_id: null`` rather than a guess.
    """
    path = Path(config_dir) / "fdir" / "fault_propagation.yaml"
    if not path.exists():
        return {"available": False,
                "reason": "fdir/fault_propagation.yaml not found",
                "nodes": [], "edges": [], "cascades": []}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:  # noqa: BLE001
        return {"available": False,
                "reason": f"fdir/fault_propagation.yaml unreadable: {e}",
                "nodes": [], "edges": [], "cascades": []}

    ids = param_ids_by_name or {}
    nodes: list[dict] = []
    cascades: list[dict] = []
    for fc in data.get("fault_cascades") or []:
        if not isinstance(fc, dict):
            continue
        fid = str(fc.get("fault_id", ""))
        pname = str(fc.get("primary_param", ""))
        pid = ids.get(pname)
        node: dict[str, Any] = {
            "id": fid,
            "description": str(fc.get("description", "")),
            "param": pname,
            "param_id": (f"0x{pid:04X}" if isinstance(pid, int) else None),
            "subsystem": _subsystem_of(pname),
            "condition": fc.get("condition"),
            "threshold": fc.get("threshold"),
            "flags": fc.get("flags"),
            "persist_s": fc.get("persist_s"),
            "recovery": fc.get("recovery"),
        }
        nodes.append(node)
        for c in fc.get("cascades") or []:
            if not isinstance(c, dict):
                continue
            cascades.append({
                "from": fid,
                "to_subsystem": str(c.get("target_subsystem", "")),
                "action": str(c.get("action", "")),
                "delay_s": c.get("delay_s"),
                "description": str(c.get("description", "")),
            })

    # Derived fault→fault ancestry (see module docstring).
    by_subsystem: dict[str, list[str]] = {}
    for n in nodes:
        by_subsystem.setdefault(n["subsystem"], []).append(n["id"])
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for c in cascades:
        for child in by_subsystem.get(c["to_subsystem"], []):
            if child == c["from"]:
                continue
            key = (c["from"], child)
            if key in seen:
                continue
            seen.add(key)
            edges.append({"parent": c["from"], "child": child,
                          "via_action": c["action"],
                          "via_subsystem": c["to_subsystem"]})

    return {
        "available": True,
        "source": "fdir/fault_propagation.yaml",
        "edge_semantics": ("edges are DERIVED fault-to-fault ancestry: "
                           "parent cascades into the subsystem of the "
                           "child's primary parameter. cascades are the "
                           "raw config."),
        "nodes": nodes,
        "edges": edges,
        "cascades": cascades,
    }
