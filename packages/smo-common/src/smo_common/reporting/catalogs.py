"""Mission catalogue loading for the report generator.

One object holding everything a report needs from ``configs/<mission>``:
parameter definitions, HK wire structures + cadences, the event catalogue,
the MCS ground limit bands, S19 rules and the orbit configuration. All
loading is read-only and tolerant of missing files (a section whose
catalogue is absent states so in the report rather than crashing).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

__all__ = ["Catalogs"]


def _toi(v) -> int:
    return int(v) if isinstance(v, int) else int(str(v), 0)


def _load_yaml(path: Path) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}
    except yaml.YAMLError as e:
        logger.warning("Unparseable catalogue %s: %s", path, e)
        return {}


@dataclass
class Catalogs:
    """All ground catalogues, keyed for the report pipeline."""

    config_dir: Path
    params_by_id: dict[int, dict] = field(default_factory=dict)
    params_by_name: dict[str, int] = field(default_factory=dict)
    #: sid -> {"name", "interval_s", "parameters": [(pid, fmt, scale)]}
    hk_structs: dict[int, dict] = field(default_factory=dict)
    events_by_id: dict[int, dict] = field(default_factory=dict)
    limits_by_id: dict[int, dict] = field(default_factory=dict)
    s19_rules: list[dict] = field(default_factory=list)
    orbit: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, config_dir: str | Path) -> "Catalogs":
        cfg = Path(config_dir)
        cat = cls(config_dir=cfg)

        pdata = _load_yaml(cfg / "telemetry" / "parameters.yaml")
        if not pdata:
            cat.missing.append("telemetry/parameters.yaml")
        for p in pdata.get("parameters", []) or []:
            try:
                pid = _toi(p["id"])
            except (KeyError, ValueError):
                continue
            cat.params_by_id[pid] = p
            name = p.get("name")
            if name:
                cat.params_by_name[str(name)] = pid

        hkdata = _load_yaml(cfg / "telemetry" / "hk_structures.yaml")
        if not hkdata:
            cat.missing.append("telemetry/hk_structures.yaml")
        for s in hkdata.get("structures", []) or []:
            try:
                sid = int(s.get("sid", 0))
            except (TypeError, ValueError):
                continue
            plist = []
            for pp in s.get("parameters", []) or []:
                try:
                    pid = _toi(pp["param_id"])
                except (KeyError, ValueError):
                    continue
                plist.append((pid, str(pp.get("pack_format", "H")),
                              float(pp.get("scale", 1) or 1)))
            cat.hk_structs[sid] = {
                "name": s.get("name", f"SID{sid}"),
                "interval_s": float(s.get("interval_s", 4.0) or 4.0),
                "parameters": plist,
            }

        edata = _load_yaml(cfg / "events" / "event_catalog.yaml")
        if not edata:
            cat.missing.append("events/event_catalog.yaml")
        for e in edata.get("events", []) or []:
            try:
                eid = _toi(e["id"])
            except (KeyError, ValueError):
                continue
            cat.events_by_id[eid] = e

        ldata = _load_yaml(cfg / "mcs" / "limits.yaml")
        if not ldata:
            cat.missing.append("mcs/limits.yaml")
        for l in ldata.get("limits", []) or []:
            try:
                pid = _toi(l["param_id"])
            except (KeyError, ValueError):
                continue
            cat.limits_by_id[pid] = l

        s19 = _load_yaml(cfg / "monitoring" / "s19_rules.yaml")
        cat.s19_rules = list(s19.get("rules", []) or [])

        cat.orbit = _load_yaml(cfg / "orbit.yaml")
        if not cat.orbit:
            cat.missing.append("orbit.yaml")
        return cat

    # ── lookups ─────────────────────────────────────────────────────

    def param_id_for(self, name_or_id: str | int) -> Optional[int]:
        """Resolve a parameter reference (name, 0x-hex, or int) to its id."""
        if isinstance(name_or_id, int):
            return name_or_id if name_or_id in self.params_by_id else None
        s = str(name_or_id).strip()
        if s in self.params_by_name:
            return self.params_by_name[s]
        try:
            pid = int(s, 0)
        except ValueError:
            return None
        return pid if pid in self.params_by_id else None

    def param_name(self, pid: int) -> str:
        return self.params_by_id.get(pid, {}).get("name", f"0x{pid:04X}")

    def param_units(self, pid: int) -> str:
        return str(self.params_by_id.get(pid, {}).get("units", "") or "")

    def event_name(self, eid: int) -> str:
        return self.events_by_id.get(eid, {}).get("name", f"0x{eid:04X}")

    def event_subsystem(self, eid: int) -> str:
        return str(self.events_by_id.get(eid, {}).get("subsystem", "") or "")

    def cadence_for_pid(self, pid: int) -> Optional[float]:
        """Fastest HK cadence any structure samples this parameter at."""
        best: Optional[float] = None
        for s in self.hk_structs.values():
            for p, _fmt, _sc in s["parameters"]:
                if p == pid:
                    iv = s["interval_s"]
                    best = iv if best is None else min(best, iv)
        return best

    def t0_epoch(self) -> Optional[str]:
        v = self.orbit.get("t0_epoch")
        return str(v) if v else None

    def ground_stations(self) -> list[dict]:
        return list(self.orbit.get("ground_stations", []) or [])
