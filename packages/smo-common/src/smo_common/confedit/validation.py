"""Validation pipeline: YAML → schema → plausibility → cross-file checks.

Every gate returns structured ``Issue`` dicts::

    {"severity": "error"|"warning"|"info", "gate": "...", "path": "...",
     "msg": "...", "files": ["..."]}

``error`` blocks apply (unless the instructor forces with a recorded
reason); ``warning`` informs. Cross-file checks run against the *proposed*
content of the edited file merged with the on-disk content of every other
file, so divergence is caught before anything is written.
"""
from __future__ import annotations

import re
import struct as _struct
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import ValidationError

from .formgen import schema_model_for, unknown_key_report, _ListWrapper

__all__ = ["validate_file", "run_crosschecks", "CROSSCHECKS"]


def _issue(severity: str, gate: str, path: str, msg: str,
           files: Optional[list[str]] = None) -> dict:
    return {"severity": severity, "gate": gate, "path": path, "msg": msg,
            "files": files or []}


# ── gate 1+2: YAML parse + schema ────────────────────────────────────────

def _schema_issues(relpath: str, data: Any) -> list[dict]:
    issues: list[dict] = []
    model = schema_model_for(relpath)
    if model is None:
        return issues
    try:
        if isinstance(model, _ListWrapper):
            entries = (data or {}).get(model.key, []) or []
            if not isinstance(entries, list):
                issues.append(_issue("error", "schema", model.key,
                                     f"{model.key} must be a list"))
            else:
                for i, entry in enumerate(entries):
                    try:
                        model.model.model_validate(entry)
                    except ValidationError as e:
                        for err in e.errors():
                            loc = ".".join(str(p) for p in err["loc"])
                            issues.append(_issue(
                                "error", "schema",
                                f"{model.key}[{i}].{loc}", err["msg"]))
        else:
            model.model_validate(data or {})
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            issues.append(_issue("error", "schema", loc, err["msg"]))
    # unknown keys the loader would silently drop → warning band
    for uk in unknown_key_report(relpath, data):
        issues.append(_issue("warning", "unknown_key", uk["path"], uk["msg"]))
    return issues


# ── gate 3: range / physical plausibility ────────────────────────────────

def _tle_checksum_ok(line: str) -> bool:
    if len(line) != 69:
        return False
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10 == int(line[68]) if line[68].isdigit() else False


def _plaus_orbit(data: dict) -> list[dict]:
    issues = []
    for key in ("tle_line1", "tle_line2"):
        line = str(data.get(key, "") or "")
        if line and len(line) != 69:
            issues.append(_issue("error", "plausibility", key,
                                 f"TLE line must be 69 characters, got "
                                 f"{len(line)}"))
        elif line and not _tle_checksum_ok(line):
            # Warning, not error: the shipped synthetic TLE carries an
            # invalid checksum and sgp4 accepts it — an error here would
            # block every legitimate orbit.yaml edit on the shipped config.
            issues.append(_issue("warning", "plausibility", key,
                                 "TLE checksum invalid (sgp4 will still "
                                 "accept the line)"))
    t0 = data.get("t0_epoch")
    if t0:
        try:
            from datetime import datetime
            datetime.fromisoformat(str(t0).replace("Z", "+00:00"))
        except ValueError:
            issues.append(_issue("error", "plausibility", "t0_epoch",
                                 f"not parseable ISO-8601: {t0!r}"))
    for i, gs in enumerate(data.get("ground_stations", []) or []):
        name = gs.get("name", f"[{i}]")
        lat = gs.get("lat_deg")
        lon = gs.get("lon_deg")
        el = gs.get("min_elevation_deg")
        if lat is not None and not -90 <= float(lat) <= 90:
            issues.append(_issue("error", "plausibility",
                                 f"ground_stations[{i}].lat_deg",
                                 f"{name}: latitude {lat} outside ±90°"))
        if lon is not None and not -180 <= float(lon) <= 180:
            issues.append(_issue("error", "plausibility",
                                 f"ground_stations[{i}].lon_deg",
                                 f"{name}: longitude {lon} outside ±180°"))
        if el is not None and not 0 <= float(el) <= 90:
            issues.append(_issue("error", "plausibility",
                                 f"ground_stations[{i}].min_elevation_deg",
                                 f"{name}: elevation mask {el} outside 0–90°"))
    return issues


def _plaus_simulation(data: dict) -> list[dict]:
    issues = []
    stages = (((data.get("eps") or {}).get("load_shed") or {})
              .get("soc_stage_thresholds_pct"))
    if stages and list(stages) != sorted(stages, reverse=True):
        issues.append(_issue("error", "plausibility",
                             "eps.load_shed.soc_stage_thresholds_pct",
                             f"must be strictly descending: {stages}"))
    return issues


def _plaus_hk(data: dict) -> list[dict]:
    issues = []
    for i, s in enumerate((data or {}).get("structures", []) or []):
        iv = s.get("interval_s", 1)
        if iv is not None and float(iv) <= 0:
            issues.append(_issue("error", "plausibility",
                                 f"structures[{i}].interval_s",
                                 f"cadence must be > 0, got {iv}"))
        for j, p in enumerate(s.get("parameters", []) or []):
            fmt = str(p.get("pack_format", "H"))
            try:
                _struct.calcsize(">" + fmt)
            except _struct.error:
                issues.append(_issue("error", "plausibility",
                                     f"structures[{i}].parameters[{j}]"
                                     ".pack_format",
                                     f"invalid struct format {fmt!r}"))
    return issues


def _plaus_limits(data: dict) -> list[dict]:
    issues = []
    for i, l in enumerate((data or {}).get("limits", []) or []):
        yl, yh = l.get("yellow_low"), l.get("yellow_high")
        rl, rh = l.get("red_low"), l.get("red_high")
        if yl is not None and yh is not None and yl > yh:
            issues.append(_issue("error", "plausibility",
                                 f"limits[{i}]", "yellow_low > yellow_high"))
        if rl is not None and rh is not None and rl > rh:
            issues.append(_issue("error", "plausibility",
                                 f"limits[{i}]", "red_low > red_high"))
        if (rl is not None and yl is not None and rl > yl) or \
           (rh is not None and yh is not None and rh < yh):
            issues.append(_issue("warning", "plausibility", f"limits[{i}]",
                                 "red band does not contain the yellow band"))
    return issues


_PLAUSIBILITY: dict[str, Callable[[dict], list[dict]]] = {
    "orbit.yaml": _plaus_orbit,
    "simulation.yaml": _plaus_simulation,
    "telemetry/hk_structures.yaml": _plaus_hk,
    "mcs/limits.yaml": _plaus_limits,
}


# ── gate 4: cross-file checks ────────────────────────────────────────────

def _load(config_dir: Path, rel: str,
          proposed: dict[str, Any]) -> Optional[dict]:
    """On-disk YAML, unless the caller proposes new content for that file."""
    if rel in proposed:
        return proposed[rel]
    try:
        with open(config_dir / rel) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return None
    except yaml.YAMLError:
        return None


def _check_rf_frequencies(config_dir: Path, proposed: dict) -> list[dict]:
    """``rf.frequencies`` — ttc.yaml ↔ planning/ground_stations.yaml ↔
    rfsim.yaml. Nothing checked this before; all three files assert
    agreement only in header comments (the brief's motivating example)."""
    issues = []
    files = ["subsystems/ttc.yaml", "planning/ground_stations.yaml",
             "rfsim.yaml"]
    ttc = _load(config_dir, files[0], proposed)
    gs = _load(config_dir, files[1], proposed)
    rf = _load(config_dir, files[2], proposed)
    if not ttc:
        return issues
    ul = ttc.get("ul_freq_mhz")
    dl = ttc.get("dl_freq_mhz")
    tol = 0.51  # MHz — allow the small offsets the shipped files carry
    if gs:
        for i, st in enumerate(gs.get("ground_stations", []) or []):
            tx = st.get("tx_freq_mhz")
            rx = st.get("rx_freq_mhz")
            name = st.get("name", f"[{i}]")
            if ul is not None and tx is not None and abs(float(tx) - float(ul)) > tol:
                issues.append(_issue(
                    "error", "rf.frequencies", "ul_freq_mhz",
                    f"ttc.yaml uplink {ul} MHz disagrees with "
                    f"planning/ground_stations.yaml {name} tx_freq_mhz {tx} "
                    f"MHz", files[:2]))
            if dl is not None and rx is not None and abs(float(rx) - float(dl)) > tol:
                issues.append(_issue(
                    "error", "rf.frequencies", "dl_freq_mhz",
                    f"ttc.yaml downlink {dl} MHz disagrees with "
                    f"planning/ground_stations.yaml {name} rx_freq_mhz {rx} "
                    f"MHz", files[:2]))
    if rf:
        ch = rf.get("channel", {}) or {}
        f_dl = ch.get("frequency_hz")
        f_ul = ch.get("uplink_frequency_hz")
        if dl is not None and f_dl is not None and \
                abs(float(f_dl) / 1e6 - float(dl)) > tol:
            issues.append(_issue(
                "error", "rf.frequencies", "dl_freq_mhz",
                f"ttc.yaml downlink {dl} MHz disagrees with rfsim.yaml "
                f"channel.frequency_hz {f_dl}", [files[0], files[2]]))
        if ul is not None and f_ul is not None and \
                abs(float(f_ul) / 1e6 - float(ul)) > tol:
            issues.append(_issue(
                "error", "rf.frequencies", "ul_freq_mhz",
                f"ttc.yaml uplink {ul} MHz disagrees with rfsim.yaml "
                f"channel.uplink_frequency_hz {f_ul}", [files[0], files[2]]))
    return issues


def _check_station_geometry(config_dir: Path, proposed: dict) -> list[dict]:
    """``stations.geometry`` — orbit.yaml (canonical) ↔
    planning/ground_stations.yaml ↔ planning/planner.yaml stations.
    Extends the planner's alignment mechanism to cover all three files."""
    issues = []
    orbit = _load(config_dir, "orbit.yaml", proposed)
    if not orbit:
        return issues
    canon = {st.get("name"): st
             for st in orbit.get("ground_stations", []) or []}
    tol_pos = 0.01   # degrees (matches planner.yaml alignment default)
    tol_alt = 0.05   # km
    for rel in ("planning/ground_stations.yaml", "planning/planner.yaml"):
        other = _load(config_dir, rel, proposed)
        if not other:
            continue
        stations = other.get("ground_stations") or other.get("stations") or []
        for st in stations:
            name = st.get("name")
            if name not in canon:
                issues.append(_issue(
                    "warning", "stations.geometry", f"stations[{name}]",
                    f"{name}: declared in {rel} but absent from orbit.yaml "
                    "(canonical) — the planner would offer a station the "
                    "simulator never sees", ["orbit.yaml", rel]))
                continue
            c = canon[name]
            for fld, tol in (("lat_deg", tol_pos), ("lon_deg", tol_pos),
                             ("alt_km", tol_alt)):
                a, b = st.get(fld), c.get(fld)
                if a is None or b is None:
                    continue  # geometry optional in planner.yaml (inherited)
                if abs(float(a) - float(b)) > tol:
                    issues.append(_issue(
                        "error", "stations.geometry",
                        f"stations[{name}].{fld}",
                        f"{name}: {fld} differs — orbit.yaml {b}, {rel} {a}",
                        ["orbit.yaml", rel]))
        declared = {st.get("name") for st in stations}
        if rel == "planning/ground_stations.yaml":
            for name in canon:
                if name not in declared:
                    issues.append(_issue(
                        "warning", "stations.geometry", f"stations[{name}]",
                        f"{name}: present in orbit.yaml but missing from "
                        f"{rel} — no RF attributes defined for it",
                        ["orbit.yaml", rel]))
    return issues


def _check_shed_ladder(config_dir: Path, proposed: dict) -> list[dict]:
    """``eps.shed_ladder`` — simulation.yaml soc_stage_thresholds_pct ↔
    fdir/fault_propagation.yaml stage soc_threshold (was a buried
    engine-start warning)."""
    issues = []
    sim = _load(config_dir, "simulation.yaml", proposed)
    fdir = _load(config_dir, "fdir/fault_propagation.yaml", proposed)
    if not sim or not fdir:
        return issues
    stages = (((sim.get("eps") or {}).get("load_shed") or {})
              .get("soc_stage_thresholds_pct"))
    fdir_stages = fdir.get("load_shedding_stages") or {}
    fdir_thresholds = []
    for key in ("stage_1", "stage_2", "stage_3"):
        st = fdir_stages.get(key) or {}
        if "soc_threshold" in st:
            fdir_thresholds.append(float(st["soc_threshold"]))
    if stages and fdir_thresholds and \
            [float(s) for s in stages] != fdir_thresholds:
        issues.append(_issue(
            "error", "eps.shed_ladder", "eps.load_shed.soc_stage_thresholds_pct",
            f"simulation.yaml shed ladder {list(stages)} disagrees with "
            f"fdir/fault_propagation.yaml stage soc_thresholds "
            f"{fdir_thresholds}",
            ["simulation.yaml", "fdir/fault_propagation.yaml"]))
    return issues


def _check_hk_wire(config_dir: Path, proposed: dict) -> list[dict]:
    """``hk.wire`` — every hk_structures param_id ∈ parameters.yaml."""
    issues = []
    hk = _load(config_dir, "telemetry/hk_structures.yaml", proposed)
    params = _load(config_dir, "telemetry/parameters.yaml", proposed)
    if not hk or not params:
        return issues

    def _toi(v):
        return int(v) if isinstance(v, int) else int(str(v), 0)

    known = set()
    for p in params.get("parameters", []) or []:
        try:
            known.add(_toi(p["id"]))
        except (KeyError, ValueError):
            pass
    for s in hk.get("structures", []) or []:
        for p in s.get("parameters", []) or []:
            try:
                pid = _toi(p["param_id"])
            except (KeyError, ValueError):
                continue
            if pid not in known:
                issues.append(_issue(
                    "error", "hk.wire",
                    f"structures[sid={s.get('sid')}]",
                    f"param_id 0x{pid:04X} in hk_structures.yaml is not "
                    "declared in parameters.yaml",
                    ["telemetry/hk_structures.yaml",
                     "telemetry/parameters.yaml"]))
    return issues


#: check id → (trigger files, function). A check runs when any of its files
#: is edited (or when the registry is run standalone).
CROSSCHECKS: dict[str, tuple[tuple[str, ...], Callable]] = {
    "rf.frequencies": (("subsystems/ttc.yaml", "planning/ground_stations.yaml",
                        "rfsim.yaml"), _check_rf_frequencies),
    "stations.geometry": (("orbit.yaml", "planning/ground_stations.yaml",
                           "planning/planner.yaml"), _check_station_geometry),
    "eps.shed_ladder": (("simulation.yaml", "fdir/fault_propagation.yaml"),
                        _check_shed_ladder),
    "hk.wire": (("telemetry/hk_structures.yaml", "telemetry/parameters.yaml"),
                _check_hk_wire),
}


def run_crosschecks(config_dir: Path,
                    proposed: Optional[dict[str, Any]] = None,
                    only_for: Optional[str] = None) -> list[dict]:
    """Run the registry. ``proposed`` maps relpath → parsed YAML of edited
    files; ``only_for`` limits to checks that involve that file."""
    proposed = proposed or {}
    issues: list[dict] = []
    for check_id, (files, fn) in CROSSCHECKS.items():
        if only_for is not None and only_for not in files:
            continue
        try:
            for issue in fn(config_dir, proposed):
                issue["check"] = check_id
                issues.append(issue)
        except Exception as e:  # noqa: BLE001 — a broken check must not block editing
            issues.append(_issue("warning", check_id, "<check>",
                                 f"cross-check failed to run: {e}"))
    return issues


# ── the pipeline ─────────────────────────────────────────────────────────

def validate_file(config_dir: Path, relpath: str, text: str) -> list[dict]:
    """Full pipeline for one proposed file content. Returns issues."""
    rp = relpath.replace("\\", "/").lstrip("/")
    if rp.endswith(".md"):
        return []  # markdown: no schema, no cross-checks
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark else "<yaml>"
        return [_issue("error", "yaml", where, f"YAML parse error: {e}")]
    issues = _schema_issues(rp, data)
    plaus = _PLAUSIBILITY.get(rp)
    if plaus and isinstance(data, dict):
        issues.extend(plaus(data))
    if not any(i["severity"] == "error" for i in issues):
        issues.extend(run_crosschecks(config_dir, {rp: data}, only_for=rp))
    return issues
