#!/usr/bin/env python3
"""SMO Scenario Generator — interactive web UI.

Serves a form at http://localhost:8096 where the instructor describes failures
and timing; clicking Generate writes the YAML directly to
configs/eosat1/scenarios/ and returns the file path.

Usage:
    python tools/scenario_generator.py
    python tools/scenario_generator.py --port 8097
"""
import argparse
import json
import os
import re
import sys
import textwrap
from pathlib import Path
from datetime import datetime

try:
    from aiohttp import web
except ImportError:
    raise SystemExit("aiohttp not installed — run: pip install aiohttp")

# ── Failure catalogue ────────────────────────────────────────────────────────
#
# Derived from the simulator's declarative failure catalogue, which is built
# from each subsystem model's own FAILURE_SPECS table (declared next to its
# inject_failure() body). This file used to carry a hand-copied dict of
# subsystems -> failure names -> {label, extras}; it drifted every time a model
# changed, and its "extras" list understood only `wheel`, `face` and `axis`, so
# every other typed parameter (star tracker unit, EPS power line, memory
# segment, gyro axis, drift ppm, leak rate ...) was unreachable from this tool
# and the scenarios it generated silently used the model defaults.
#
# The form is now generated from the same ParamSpec objects the instructor
# console uses, so a new failure appears here automatically and with the right
# controls, ranges and engineering units.

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _pkg in ("smo-common", "smo-simulator"):
    _src = _REPO_ROOT / "packages" / _pkg / "src"
    if _src.exists() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from smo_simulator import failure_catalog as _failure_catalog  # noqa: E402

# {subsystem: {"label": str, "failures": {name: FailureSpec-as-dict}}}
SUBSYSTEMS = {
    entry["name"]: {
        "label": entry["label"],
        "failures": {f["name"]: f for f in entry["failures"]},
    }
    for entry in _failure_catalog.to_dict()["subsystems"]
}

# Meta keys of a generated inject event that belong to the failure MANAGER
# (severity/timing) rather than to the failure's own parameter spec.
_INJECT_META_KEYS = ("type", "subsystem", "failure", "magnitude", "onset",
                     "onset_duration_s", "timing_type", "time_offset_s",
                     "condition", "label", "params")

# Key telemetry parameters available for set_parameter events
PARAMS = [
    {"id": "0x0101", "name": "eps.bat_soc",       "desc": "Battery SoC (%)",           "unit": "%"},
    {"id": "0x0105", "name": "eps.bus_voltage",    "desc": "Bus voltage (V)",            "unit": "V"},
    {"id": "0x0107", "name": "eps.power_gen",      "desc": "Power generation (W)",       "unit": "W"},
    {"id": "0x0106", "name": "eps.power_cons",     "desc": "Power consumption (W)",      "unit": "W"},
    {"id": "0x0102", "name": "eps.bat_temp",       "desc": "Battery temperature (°C)",   "unit": "C"},
    {"id": "0x0208", "name": "aocs.rw2_speed",     "desc": "RW-2 speed (RPM)",           "unit": "RPM"},
    {"id": "0x0207", "name": "aocs.rw1_speed",     "desc": "RW-1 speed (RPM)",           "unit": "RPM"},
    {"id": "0x0209", "name": "aocs.rw3_speed",     "desc": "RW-3 speed (RPM)",           "unit": "RPM"},
    {"id": "0x020A", "name": "aocs.rw4_speed",     "desc": "RW-4 speed (RPM)",           "unit": "RPM"},
    {"id": "0x0407", "name": "tcs.temp_battery",   "desc": "Battery temperature TCS (°C)", "unit": "C"},
    {"id": "0x0406", "name": "tcs.temp_obc",       "desc": "OBC temperature (°C)",       "unit": "C"},
    {"id": "0x0408", "name": "tcs.temp_fpa",       "desc": "FPA temperature (°C)",       "unit": "C"},
    {"id": "0x0302", "name": "obdh.cpu_load",      "desc": "CPU load (%)",               "unit": "%"},
    {"id": "0x0502", "name": "ttc.rssi",            "desc": "RSSI (dBm)",                "unit": "dBm"},
    {"id": "0x0503", "name": "ttc.link_margin",    "desc": "Link margin (dB)",           "unit": "dB"},
]

DIFFICULTIES = ["BASIC", "INTERMEDIATE", "ADVANCED", "EXPERT"]
ONSETS = ["step", "gradual", "intermittent"]
FACES = ["px", "mx", "py", "my", "pz", "mz"]

# ── Cascade knowledge base (derived from fault_propagation.yaml + subsystem models) ──
# Maps (subsystem, failure) → list of cascade effects with timing and severity
CASCADE_MAP = {
    # EPS failures
    ("eps", "solar_panel_loss"): [
        {"to": "eps",     "effect": "Bus voltage drops → undervoltage alarm",         "delay_s": 5,   "severity": "warning"},
        {"to": "eps",     "effect": "Battery begins discharging (SoC declining)",      "delay_s": 10,  "severity": "warning"},
        {"to": "tcs",     "effect": "Heaters shed to reduce load (temp drops)",        "delay_s": 60,  "severity": "warning"},
        {"to": "aocs",    "effect": "AOCS enters safe mode if bus < 22 V",             "delay_s": 120, "severity": "critical"},
        {"to": "payload", "effect": "Payload powered off at load-shed stage 2",        "delay_s": 180, "severity": "critical"},
        {"to": "obdh",    "effect": "Watchdog reset risk if SoC < 10%",               "delay_s": 600, "severity": "critical"},
    ],
    ("eps", "solar_array_total_loss"): [
        {"to": "eps",     "effect": "Bus voltage collapses immediately",               "delay_s": 1,   "severity": "critical"},
        {"to": "eps",     "effect": "Battery only — survival time ~90 min",            "delay_s": 2,   "severity": "critical"},
        {"to": "tcs",     "effect": "All non-essential heaters shed",                  "delay_s": 5,   "severity": "warning"},
        {"to": "aocs",    "effect": "AOCS safe mode within seconds",                   "delay_s": 5,   "severity": "critical"},
        {"to": "payload", "effect": "Payload powered off immediately",                 "delay_s": 2,   "severity": "critical"},
        {"to": "ttc",     "effect": "TX power reduced to preserve battery",            "delay_s": 10,  "severity": "warning"},
        {"to": "obdh",    "effect": "OBC enters contingency boot sequence",            "delay_s": 30,  "severity": "critical"},
    ],
    ("eps", "bat_cell"): [
        {"to": "eps",     "effect": "Reduced capacity → SoC drops faster",            "delay_s": 5,   "severity": "warning"},
        {"to": "eps",     "effect": "Battery overtemp risk during charge",             "delay_s": 30,  "severity": "warning"},
        {"to": "tcs",     "effect": "Battery heater may activate abnormally",          "delay_s": 60,  "severity": "info"},
        {"to": "aocs",    "effect": "Safe mode if SoC drops below threshold",          "delay_s": 300, "severity": "critical"},
    ],
    ("eps", "undervoltage"): [
        {"to": "aocs",    "effect": "AOCS enters safe mode (< 22 V threshold)",        "delay_s": 5,   "severity": "critical"},
        {"to": "payload", "effect": "Payload powered off",                             "delay_s": 2,   "severity": "critical"},
        {"to": "tcs",     "effect": "Non-essential heaters disabled",                  "delay_s": 1,   "severity": "warning"},
        {"to": "ttc",     "effect": "TX power reduced",                                "delay_s": 3,   "severity": "warning"},
    ],
    ("eps", "bus_short"): [
        {"to": "eps",     "effect": "Overcurrent trip — affected bus powered off",     "delay_s": 0,   "severity": "critical"},
        {"to": "aocs",    "effect": "AOCS power loss if on shorted bus",               "delay_s": 1,   "severity": "critical"},
        {"to": "obdh",    "effect": "Possible OBC reset if OBC bus affected",          "delay_s": 1,   "severity": "critical"},
    ],
    ("eps", "overcurrent"): [
        {"to": "eps",     "effect": "Overcurrent protection trips affected line",       "delay_s": 0,   "severity": "critical"},
        {"to": "aocs",    "effect": "Attitude control loss if AOCS line tripped",      "delay_s": 1,   "severity": "critical"},
        {"to": "payload", "effect": "Payload offline if payload line tripped",         "delay_s": 1,   "severity": "warning"},
    ],
    # AOCS failures
    ("aocs", "rw_bearing"): [
        {"to": "aocs",    "effect": "RW friction increases → speed runaway risk",      "delay_s": 30,  "severity": "warning"},
        {"to": "aocs",    "effect": "Attitude pointing error grows (desaturation fails)","delay_s": 120,"severity": "warning"},
        {"to": "aocs",    "effect": "Safe mode entry if RW exceeds 6000 RPM",          "delay_s": 300, "severity": "critical"},
        {"to": "payload", "effect": "Imaging quality degrades, then halts",            "delay_s": 120, "severity": "warning"},
        {"to": "eps",     "effect": "RW current draw spikes (motor fighting bearing)", "delay_s": 30,  "severity": "info"},
        {"to": "tcs",     "effect": "RW thermal output rises",                         "delay_s": 60,  "severity": "info"},
    ],
    ("aocs", "rw_seizure"): [
        {"to": "aocs",    "effect": "Immediate attitude disturbance torque",           "delay_s": 0,   "severity": "critical"},
        {"to": "aocs",    "effect": "Safe mode entry if 3-axis control lost",          "delay_s": 5,   "severity": "critical"},
        {"to": "payload", "effect": "Payload halted — attitude unstable",              "delay_s": 5,   "severity": "critical"},
        {"to": "eps",     "effect": "AOCS power spike then drop (wheel off)",          "delay_s": 2,   "severity": "warning"},
    ],
    ("aocs", "gyro_bias"): [
        {"to": "aocs",    "effect": "Attitude estimate drifts — pointing error grows", "delay_s": 60,  "severity": "warning"},
        {"to": "aocs",    "effect": "Star tracker needed for recovery; if blinded → safe mode","delay_s": 300,"severity": "critical"},
        {"to": "payload", "effect": "Imaging off-target progressively",               "delay_s": 120, "severity": "warning"},
    ],
    ("aocs", "st_failure"): [
        {"to": "aocs",    "effect": "Attitude estimation degrades to gyro-only",       "delay_s": 1,   "severity": "warning"},
        {"to": "aocs",    "effect": "Safe mode if gyro drift exceeds limit",           "delay_s": 300, "severity": "critical"},
        {"to": "payload", "effect": "Fine pointing lost",                              "delay_s": 60,  "severity": "warning"},
    ],
    ("aocs", "st_blind"): [
        {"to": "aocs",    "effect": "Fine attitude reference lost",                    "delay_s": 1,   "severity": "warning"},
        {"to": "aocs",    "effect": "Coarse sun-safe mode after gyro timeout",         "delay_s": 120, "severity": "critical"},
        {"to": "payload", "effect": "Imaging halted",                                  "delay_s": 5,   "severity": "warning"},
    ],
    ("aocs", "multi_wheel_failure"): [
        {"to": "aocs",    "effect": "3-axis attitude control lost immediately",         "delay_s": 1,   "severity": "critical"},
        {"to": "aocs",    "effect": "Sun-safe tumble mode — magnetorquers only",       "delay_s": 5,   "severity": "critical"},
        {"to": "payload", "effect": "Payload powered off",                             "delay_s": 2,   "severity": "critical"},
        {"to": "eps",     "effect": "Power draw drops (wheels off), but SA may lose sun pointing","delay_s": 10,"severity": "warning"},
        {"to": "ttc",     "effect": "Link margin degrades if antenna off-pointing",    "delay_s": 30,  "severity": "warning"},
    ],
    # TCS failures
    ("tcs", "cooler_failure"): [
        {"to": "tcs",     "effect": "FPA temperature rises (imaging sensor heats up)", "delay_s": 60,  "severity": "warning"},
        {"to": "payload", "effect": "Payload imaging degraded → halted above threshold","delay_s": 180,"severity": "critical"},
        {"to": "eps",     "effect": "FPA cooler power line freed (minor power saving)", "delay_s": 5,  "severity": "info"},
    ],
    ("tcs", "heater_failure"): [
        {"to": "tcs",     "effect": "Zone temperature drops below setpoint",           "delay_s": 120, "severity": "warning"},
        {"to": "eps",     "effect": "Battery temp may fall below -5°C safe limit",     "delay_s": 300, "severity": "critical"},
        {"to": "obdh",    "effect": "OBC performance degrades below -20°C",            "delay_s": 600, "severity": "warning"},
    ],
    ("tcs", "heater_stuck_on"): [
        {"to": "tcs",     "effect": "Zone temperature climbs above setpoint",          "delay_s": 60,  "severity": "warning"},
        {"to": "eps",     "effect": "Heater line drawing excess current",              "delay_s": 10,  "severity": "warning"},
        {"to": "tcs",     "effect": "Battery overtemp risk → charge rate cut",         "delay_s": 300, "severity": "critical"},
    ],
    ("tcs", "obc_thermal"): [
        {"to": "obdh",    "effect": "CPU throttling → increased latency",              "delay_s": 30,  "severity": "warning"},
        {"to": "obdh",    "effect": "Possible OBC reset above 85°C",                  "delay_s": 120, "severity": "critical"},
        {"to": "aocs",    "effect": "AOCS commanding delayed during OBC slowdown",     "delay_s": 30,  "severity": "info"},
    ],
    # TTC failures
    ("ttc", "uplink_loss"): [
        {"to": "ttc",     "effect": "No TC reception — ground cannot command",         "delay_s": 0,   "severity": "critical"},
        {"to": "obdh",    "effect": "FDIR may escalate if no uplink within timeout",   "delay_s": 300, "severity": "warning"},
        {"to": "aocs",    "effect": "No manual override possible during anomaly",      "delay_s": 0,   "severity": "warning"},
    ],
    ("ttc", "primary_failure"): [
        {"to": "ttc",     "effect": "Switchover to redundant transponder attempted",   "delay_s": 2,   "severity": "warning"},
        {"to": "ttc",     "effect": "Link outage during switchover (~10 s)",           "delay_s": 2,   "severity": "warning"},
        {"to": "obdh",    "effect": "TC loss triggers FDIR uplink-loss watchdog",      "delay_s": 5,   "severity": "warning"},
    ],
    ("ttc", "pa_overheat"): [
        {"to": "ttc",     "effect": "TX power auto-reduced to protect PA",             "delay_s": 1,   "severity": "warning"},
        {"to": "ttc",     "effect": "Downlink data rate reduced",                      "delay_s": 5,   "severity": "warning"},
        {"to": "ttc",     "effect": "PA shutdown if temp > 90°C → full link loss",     "delay_s": 60,  "severity": "critical"},
        {"to": "tcs",     "effect": "Transponder compartment heat rises",              "delay_s": 30,  "severity": "info"},
    ],
    ("ttc", "high_ber"): [
        {"to": "ttc",     "effect": "TC frame error rate rises — commands may be lost", "delay_s": 0,  "severity": "warning"},
        {"to": "obdh",    "effect": "TC rejection count increases",                    "delay_s": 5,   "severity": "info"},
    ],
    # OBDH failures
    ("obdh", "watchdog_reset"): [
        {"to": "obdh",    "effect": "OBC reboots → ~30 s blackout",                   "delay_s": 0,   "severity": "critical"},
        {"to": "aocs",    "effect": "AOCS enters safe mode during OBC reboot",         "delay_s": 2,   "severity": "critical"},
        {"to": "payload", "effect": "Payload safed during OBC reboot",                 "delay_s": 1,   "severity": "warning"},
        {"to": "ttc",     "effect": "TM stream interrupted during reboot",             "delay_s": 0,   "severity": "warning"},
        {"to": "obdh",    "effect": "Boots into BOOTLOADER (phase 3) — manual recovery needed","delay_s": 30,"severity": "critical"},
    ],
    ("obdh", "bus_failure"): [
        {"to": "aocs",    "effect": "AOCS commanding via CAN lost → safe mode",        "delay_s": 2,   "severity": "critical"},
        {"to": "payload", "effect": "Payload cannot be commanded → powered off",       "delay_s": 1,   "severity": "critical"},
        {"to": "eps",     "effect": "EPS enters contingency power profile",            "delay_s": 3,   "severity": "warning"},
        {"to": "ttc",     "effect": "TTC in standby awaiting comms restoration",       "delay_s": 1,   "severity": "warning"},
        {"to": "tcs",     "effect": "Heater commanding lost",                          "delay_s": 2,   "severity": "warning"},
    ],
    ("obdh", "obc_crash"): [
        {"to": "obdh",    "effect": "Full OBC crash → watchdog trips within 5 s",     "delay_s": 5,   "severity": "critical"},
        {"to": "aocs",    "effect": "AOCS autonomous safe mode",                       "delay_s": 5,   "severity": "critical"},
        {"to": "payload", "effect": "Payload loses power",                             "delay_s": 5,   "severity": "critical"},
        {"to": "ttc",     "effect": "TM drops — ground sees loss of signal",           "delay_s": 5,   "severity": "critical"},
    ],
    ("obdh", "memory_errors"): [
        {"to": "obdh",    "effect": "Correctable SEU events → scrubber load increases","delay_s": 10,  "severity": "info"},
        {"to": "obdh",    "effect": "Uncorrectable errors may trigger safe mode reboot","delay_s": 300,"severity": "warning"},
        {"to": "aocs",    "effect": "AOCS commanding reliability reduced",             "delay_s": 30,  "severity": "info"},
    ],
    ("obdh", "cpu_spike"): [
        {"to": "obdh",    "effect": "TC processing delayed — commands queued",         "delay_s": 0,   "severity": "warning"},
        {"to": "aocs",    "effect": "AOCS update rate drops if OBC overloaded",        "delay_s": 5,   "severity": "info"},
        {"to": "ttc",     "effect": "TM packetisation may lag",                        "delay_s": 5,   "severity": "info"},
    ],
}

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
SCENARIO_DIR = REPO_ROOT / "configs" / "eosat1" / "scenarios"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def generate_yaml(data: dict) -> tuple[str, str]:
    """Build YAML text from the form data dict. Returns (yaml_text, filename)."""
    name = data.get("name", "Custom Scenario").strip() or "Custom Scenario"
    difficulty = data.get("difficulty", "BASIC")
    duration = int(data.get("duration_s", 1800))
    briefing = data.get("briefing", "").strip()
    events_raw = data.get("events", [])

    lines = []
    lines.append(f'name: "{name}"')
    lines.append(f"difficulty: {difficulty}")
    lines.append(f"duration_s: {duration}")
    if briefing:
        lines.append("briefing: |")
        for bl in textwrap.wrap(briefing, 72):
            lines.append(f"  {bl}")
    lines.append("")
    lines.append("events:")
    lines.append("")

    for ev in events_raw:
        ev_type = ev.get("type", "inject")

        if ev_type == "inject":
            subsys = ev.get("subsystem", "")
            failure = ev.get("failure", "")
            magnitude = float(ev.get("magnitude", 1.0))
            onset = ev.get("onset", "step")
            onset_dur = ev.get("onset_duration_s", "")
            # Typed parameters, whatever the failure's spec declares. This
            # used to be a hard-coded (wheel, face, axis) triple, so every
            # other unit selector silently fell back to the model default.
            spec = (SUBSYSTEMS.get(subsys, {}).get("failures", {})
                    .get(failure, {}))
            typed = {p["name"]: ev.get(p["name"])
                     for p in (spec.get("params") or [])
                     if ev.get(p["name"]) not in (None, "", [])}
            timing_type = ev.get("timing_type", "time")
            time_offset = ev.get("time_offset_s", 0)
            condition = ev.get("condition", "").strip()
            label = ev.get("label", "").strip()

            if label:
                lines.append(f"  # {label}")
            if timing_type == "condition" and condition:
                lines.append(f'  - condition: "{condition}"')
            else:
                lines.append(f"  - time_offset_s: {int(time_offset)}")
            lines.append(f"    action: inject")
            lines.append(f"    params:")
            lines.append(f"      subsystem: {subsys}")
            lines.append(f"      failure: {failure}")
            lines.append(f"      magnitude: {magnitude}")
            lines.append(f"      onset: {onset}")
            if onset == "gradual" and onset_dur:
                lines.append(f"      onset_duration_s: {int(onset_dur)}")
            for pname, pvalue in typed.items():
                if isinstance(pvalue, (list, tuple)):
                    rendered = "[" + ", ".join(json.dumps(v) for v in pvalue) + "]"
                else:
                    rendered = json.dumps(pvalue)
                lines.append(f"      {pname}: {rendered}")
            lines.append("")

        elif ev_type == "set_parameter":
            param_id = ev.get("param_id", "")
            value = ev.get("value", 0)
            param_name = ev.get("param_name", param_id)
            time_offset = ev.get("time_offset_s", 0)
            label = ev.get("label", "").strip()

            if label:
                lines.append(f"  # {label}")
            else:
                lines.append(f"  # Force {param_name} = {value}")
            lines.append(f"  - time_offset_s: {int(time_offset)}")
            lines.append(f"    action: set_parameter")
            lines.append(f"    params:")
            lines.append(f"      param_id: {param_id}  # {param_name}")
            lines.append(f"      value: {value}")
            lines.append("")

    # expected_responses
    responses = data.get("expected_responses", [])
    if responses:
        lines.append("expected_responses:")
        for r in responses:
            cat = r.get("category", "recover")
            desc = r.get("description", "").strip()
            if desc:
                lines.append(f"  - category: {cat}")
                lines.append(f"    description: >")
                for dl in textwrap.wrap(desc, 70):
                    lines.append(f"      {dl}")
        lines.append("")

    yaml_text = "\n".join(lines)
    filename = _slug(name) + ".yaml"
    return yaml_text, filename


# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>SMO Scenario Generator</title>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
    --green: #3fb950; --yellow: #d29922; --red: #f85149;
    --input-bg: #21262d;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 14px/1.6 'Segoe UI', system-ui, sans-serif; padding: 24px; }
  h1 { font-size: 20px; font-weight: 600; color: var(--accent); margin-bottom: 4px; }
  .subtitle { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 15px; font-weight: 600; margin-bottom: 14px; color: var(--text); }
  .row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .field { display: flex; flex-direction: column; gap: 4px; }
  .field label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  input, select, textarea {
    background: var(--input-bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; padding: 7px 10px; font-size: 13px; font-family: inherit;
    transition: border-color .15s;
  }
  input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); }
  textarea { resize: vertical; min-height: 72px; }
  .f-grow { flex: 1 1 200px; }
  .f-sm { flex: 0 0 120px; }
  .f-xs { flex: 0 0 90px; }
  .btn {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--accent); color: #0d1117; font-weight: 600; font-size: 13px;
    border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer;
    transition: opacity .15s;
  }
  .btn:hover { opacity: .88; }
  .btn-ghost {
    background: transparent; border: 1px solid var(--border); color: var(--text);
    border-radius: 6px; padding: 6px 12px; font-size: 13px; cursor: pointer;
    transition: border-color .15s;
  }
  .btn-ghost:hover { border-color: var(--accent); color: var(--accent); }
  .btn-danger { background: transparent; border: 1px solid var(--red); color: var(--red); }
  .btn-danger:hover { background: var(--red); color: #fff; }
  #events-list { display: flex; flex-direction: column; gap: 10px; }
  .event-card {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 14px; position: relative;
  }
  .event-card .ev-title {
    font-size: 12px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .05em; color: var(--accent); margin-bottom: 10px;
  }
  .event-card .remove-btn {
    position: absolute; top: 10px; right: 10px;
    background: none; border: none; color: var(--muted); cursor: pointer; font-size: 16px;
  }
  .event-card .remove-btn:hover { color: var(--red); }
  .add-btns { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  #resp-list { display: flex; flex-direction: column; gap: 8px; }
  .resp-row { display: flex; gap: 8px; align-items: flex-start; }
  .resp-row select { flex: 0 0 130px; }
  .resp-row textarea { flex: 1; min-height: 52px; }
  .resp-row button { flex: none; margin-top: 2px; }
  #output-area { display: none; }
  #yaml-out {
    background: #010409; border: 1px solid var(--border); border-radius: 6px;
    padding: 14px; font: 13px/1.7 'JetBrains Mono', 'Fira Code', monospace;
    white-space: pre; overflow-x: auto; color: #aff5b4;
  }
  .success-banner {
    background: #0d2a18; border: 1px solid var(--green); border-radius: 6px;
    padding: 10px 14px; color: var(--green); font-size: 13px; margin-bottom: 12px;
    display: none;
  }
  .error-banner {
    background: #2d1118; border: 1px solid var(--red); border-radius: 6px;
    padding: 10px 14px; color: var(--red); font-size: 13px; margin-bottom: 12px;
    display: none;
  }
  /* Success modal */
  #success-modal {
    display: none; position: fixed; inset: 0; z-index: 999;
    background: rgba(0,0,0,.6); backdrop-filter: blur(3px);
    align-items: center; justify-content: center;
  }
  #success-modal.show { display: flex; }
  .modal-box {
    background: #0d2a18; border: 1px solid var(--green); border-radius: 12px;
    padding: 32px 40px; text-align: center; max-width: 420px; width: 90%;
    animation: popIn .25s cubic-bezier(.34,1.56,.64,1);
  }
  @keyframes popIn {
    from { transform: scale(.8); opacity: 0; }
    to   { transform: scale(1);  opacity: 1; }
  }
  .modal-icon { font-size: 48px; margin-bottom: 12px; }
  .modal-title { font-size: 20px; font-weight: 700; color: var(--green); margin-bottom: 8px; }
  .modal-sub { font-size: 13px; color: var(--muted); word-break: break-all; margin-bottom: 24px; }
  .modal-path { color: var(--text); font-family: monospace; }
  .modal-close {
    background: var(--green); color: #0d1117; font-weight: 700;
    border: none; border-radius: 6px; padding: 10px 28px;
    font-size: 14px; cursor: pointer; transition: opacity .15s;
  }
  .modal-close:hover { opacity: .85; }
  .hint { font-size: 11px; color: var(--muted); margin-top: 2px; }

  /* Cascade timeline */
  .cascade-row { display: flex; gap: 0; margin-bottom: 6px; align-items: stretch; }
  .cascade-time {
    flex: 0 0 72px; font-size: 11px; color: var(--muted); font-family: monospace;
    padding-top: 10px; text-align: right; padding-right: 10px;
  }
  .cascade-spine { flex: 0 0 24px; display: flex; flex-direction: column; align-items: center; }
  .cascade-dot {
    width: 10px; height: 10px; border-radius: 50%; margin-top: 10px; flex-shrink: 0;
    border: 2px solid;
  }
  .cascade-dot.info     { background:#1f3a52; border-color:#58a6ff; }
  .cascade-dot.warning  { background:#3a2a00; border-color:#d29922; }
  .cascade-dot.critical { background:#3a0d10; border-color:#f85149; }
  .cascade-line { flex: 1; width: 2px; background: var(--border); margin-top: 2px; }
  .cascade-body {
    flex: 1; background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 8px 12px; margin-bottom: 4px;
  }
  .cascade-body.info     { border-left: 3px solid #58a6ff; }
  .cascade-body.warning  { border-left: 3px solid #d29922; }
  .cascade-body.critical { border-left: 3px solid #f85149; }
  .cascade-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 2px; }
  .cascade-subsys {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    padding: 1px 6px; border-radius: 3px;
  }
  .subsys-eps     { background:#1a2a1a; color:#3fb950; }
  .subsys-aocs    { background:#1a1a2a; color:#79c0ff; }
  .subsys-tcs     { background:#2a1a00; color:#e3b341; }
  .subsys-ttc     { background:#2a002a; color:#d2a8ff; }
  .subsys-obdh    { background:#2a1500; color:#ffa657; }
  .subsys-payload { background:#1a2a2a; color:#56d364; }
  .cascade-sev { font-size: 10px; color: var(--muted); }
  .cascade-sev.critical { color: #f85149; }
  .cascade-sev.warning  { color: #d29922; }
  .cascade-effect { font-size: 12px; color: var(--text); }
  .cascade-source { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .cascade-group-label {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em;
    color: var(--accent); margin: 14px 0 6px 96px; padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  .cascade-empty { color: var(--muted); font-size: 13px; padding: 12px 0; }
  .extras { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
  .tag { background: #1f2937; border: 1px solid var(--border); border-radius: 4px;
         padding: 2px 7px; font-size: 11px; color: var(--muted); }
</style>
</head>
<body>

<h1>SMO Scenario Generator</h1>
<p class="subtitle">Describe your failures — the tool writes the YAML to configs/eosat1/scenarios/</p>

<!-- METADATA -->
<div class="card">
  <h2>Scenario Metadata</h2>
  <div class="row">
    <div class="field f-grow">
      <label>Scenario Name</label>
      <input id="sc-name" type="text" placeholder="e.g. Battery Thermal Runaway" value="">
    </div>
    <div class="field f-sm">
      <label>Difficulty</label>
      <select id="sc-diff">
        <option>BASIC</option><option>INTERMEDIATE</option>
        <option selected>ADVANCED</option><option>EXPERT</option>
      </select>
    </div>
    <div class="field f-xs">
      <label>Duration (s)</label>
      <input id="sc-dur" type="number" value="1800" min="60" step="60">
    </div>
  </div>
  <div class="field">
    <label>Briefing (shown to operators before start)</label>
    <textarea id="sc-brief" placeholder="Describe the scenario narrative and what operators must achieve..."></textarea>
  </div>
</div>

<!-- EVENTS -->
<div class="card">
  <h2>Failure Events &amp; Parameter Overrides</h2>
  <div id="events-list"></div>
  <div class="add-btns">
    <button class="btn-ghost" onclick="addInject()">+ Inject Failure</button>
    <button class="btn-ghost" onclick="addSetParam()">+ Set Parameter</button>
  </div>
</div>

<!-- CASCADE PREVIEW -->
<div class="card" id="cascade-card" style="display:none">
  <h2>Cascade Effect Preview <span style="color:var(--muted);font-weight:400;font-size:12px">— auto-generated from your failure events</span></h2>
  <div id="cascade-timeline"></div>
</div>

<!-- EXPECTED RESPONSES -->
<div class="card">
  <h2>Expected Operator Responses <span style="color:var(--muted);font-weight:400;font-size:12px">(optional — used for scoring)</span></h2>
  <div id="resp-list"></div>
  <button class="btn-ghost" style="margin-top:10px" onclick="addResponse()">+ Add Response</button>
</div>

<!-- GENERATE -->
<div style="display:flex;gap:12px;align-items:center;margin-bottom:20px">
  <button class="btn" onclick="generate()">&#9654; Generate &amp; Save YAML</button>
  <button class="btn-ghost" onclick="preview()">Preview YAML</button>
</div>

<!-- Success modal -->
<div id="success-modal">
  <div class="modal-box">
    <div class="modal-icon">&#x2705;</div>
    <div class="modal-title">Scenario Created!</div>
    <div class="modal-sub">Saved to<br><span class="modal-path" id="modal-path"></span></div>
    <button class="modal-close" onclick="closeModal()">OK</button>
  </div>
</div>

<div class="success-banner" id="success-banner"></div>
<div class="error-banner" id="error-banner"></div>

<div id="output-area">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <h2 style="margin:0">Generated YAML</h2>
      <button class="btn-ghost" onclick="copyYaml()">Copy</button>
    </div>
    <div id="yaml-out"></div>
  </div>
</div>

<script>
const SUBSYSTEMS = """ + json.dumps(SUBSYSTEMS) + r""";
const PARAMS = """ + json.dumps(PARAMS) + r""";
const FACES = """ + json.dumps(FACES) + r""";

let evId = 0;

function addInject() {
  const id = ++evId;
  const subsysOpts = Object.entries(SUBSYSTEMS).map(([k,v]) =>
    `<option value="${k}">${v.label}</option>`).join('');

  const div = document.createElement('div');
  div.className = 'event-card';
  div.id = `ev-${id}`;
  div.dataset.type = 'inject';
  div.innerHTML = `
    <div class="ev-title">&#9889; Failure Injection</div>
    <button class="remove-btn" onclick="removeEv(${id})">&#x2715;</button>
    <div class="row">
      <div class="field f-xs">
        <label>Timing</label>
        <select onchange="toggleTiming(${id}, this.value)">
          <option value="time">At time (s)</option>
          <option value="condition">Condition</option>
        </select>
      </div>
      <div class="field f-xs" id="ev-${id}-time-field">
        <label>Time offset (s)</label>
        <input type="number" id="ev-${id}-time" value="0" min="0">
      </div>
      <div class="field f-grow" id="ev-${id}-cond-field" style="display:none">
        <label>Condition (e.g. 257 &lt; 60)</label>
        <input type="text" id="ev-${id}-cond" placeholder="param_id_decimal op value">
        <span class="hint">Use decimal param ID. e.g. 257 &lt; 60 means bat_soc(0x0101=257) &lt; 60</span>
      </div>
    </div>
    <div class="row">
      <div class="field f-sm">
        <label>Subsystem</label>
        <select id="ev-${id}-sub" onchange="updateFailures(${id})">${subsysOpts}</select>
      </div>
      <div class="field f-grow">
        <label>Failure</label>
        <select id="ev-${id}-fail" onchange="updateExtras(${id})"></select>
      </div>
      <div class="field f-xs">
        <label>Magnitude (0–1)</label>
        <input type="number" id="ev-${id}-mag" value="1.0" min="0" max="1" step="0.05">
      </div>
      <div class="field f-sm">
        <label>Onset</label>
        <select id="ev-${id}-onset" onchange="updateExtras(${id})">
          <option>step</option><option>gradual</option><option>intermittent</option>
        </select>
      </div>
    </div>
    <div id="ev-${id}-extras" class="extras"></div>
    <div class="field" style="margin-top:8px">
      <label>Label / comment (optional)</label>
      <input type="text" id="ev-${id}-label" placeholder="e.g. SA-2B impact at T+0">
    </div>
  `;
  document.getElementById('events-list').appendChild(div);
  updateFailures(id);
  scheduleCascadeUpdate();
}

function addSetParam() {
  const id = ++evId;
  const paramOpts = PARAMS.map(p =>
    `<option value="${p.id}" data-name="${p.name}">${p.desc} (${p.id})</option>`).join('');

  const div = document.createElement('div');
  div.className = 'event-card';
  div.id = `ev-${id}`;
  div.dataset.type = 'set_parameter';
  div.innerHTML = `
    <div class="ev-title">&#128200; Set Parameter</div>
    <button class="remove-btn" onclick="removeEv(${id})">&#x2715;</button>
    <div class="row">
      <div class="field f-xs">
        <label>Time offset (s)</label>
        <input type="number" id="ev-${id}-time" value="0" min="0">
      </div>
      <div class="field f-grow">
        <label>Parameter</label>
        <select id="ev-${id}-param">${paramOpts}</select>
      </div>
      <div class="field f-xs">
        <label>Value</label>
        <input type="number" id="ev-${id}-value" value="0" step="any">
      </div>
    </div>
    <div class="field" style="margin-top:4px">
      <label>Label / comment (optional)</label>
      <input type="text" id="ev-${id}-label" placeholder="e.g. Force bus voltage low">
    </div>
  `;
  document.getElementById('events-list').appendChild(div);
}

function removeEv(id) {
  document.getElementById(`ev-${id}`)?.remove();
}

function toggleTiming(id, val) {
  document.getElementById(`ev-${id}-time-field`).style.display = val === 'time' ? '' : 'none';
  document.getElementById(`ev-${id}-cond-field`).style.display = val === 'condition' ? '' : 'none';
}

function updateFailures(id) {
  const sub = document.getElementById(`ev-${id}-sub`).value;
  const failures = SUBSYSTEMS[sub]?.failures || {};
  const sel = document.getElementById(`ev-${id}-fail`);
  sel.innerHTML = Object.entries(failures)
    .map(([k,v]) => `<option value="${k}">${v.label}</option>`).join('');
  updateExtras(id);
}

// Render the typed parameter controls for the selected failure, straight from
// its ParamSpec list. Nothing about a failure's parameters is written here:
// the enum values, the ranges, the engineering units and the labels all come
// from the model's own FAILURE_SPECS via /api catalogue -> SUBSYSTEMS.
function updateExtras(id) {
  const sub = document.getElementById(`ev-${id}-sub`).value;
  const fail = document.getElementById(`ev-${id}-fail`).value;
  const onset = document.getElementById(`ev-${id}-onset`).value;
  const spec = SUBSYSTEMS[sub]?.failures?.[fail];
  const container = document.getElementById(`ev-${id}-extras`);
  let html = '';

  for (const p of (spec?.params || [])) {
    const cid = `ev-${id}-p-${p.name}`;
    const unit = p.unit ? ` [${p.unit}]` : '';
    const label = `<label>${p.label || p.name}${unit}</label>`;
    if (p.type === 'bool') {
      html += `<div class="field f-xs">${label}
        <select id="${cid}" data-param="${p.name}">
          <option value="true"${p.default ? ' selected' : ''}>Yes</option>
          <option value="false"${p.default ? '' : ' selected'}>No</option>
        </select></div>`;
    } else if (p.type === 'enum') {
      const values = p.values || [];
      const labels = p.value_labels || values.map(String);
      const defs = Array.isArray(p.default) ? p.default.map(String)
                 : (p.default === null || p.default === undefined ? [] : [String(p.default)]);
      const opts = values.map((v, i) =>
        `<option value="${v}"${defs.includes(String(v)) ? ' selected' : ''}>${labels[i]}</option>`).join('');
      const multi = p.multi ? ' multiple size="4"' : '';
      html += `<div class="field f-xs">${label}
        <select id="${cid}" data-param="${p.name}" data-multi="${p.multi ? '1' : ''}"${multi}>${opts}</select></div>`;
    } else {
      const step = p.type === 'int' ? 1 : (p.step || 'any');
      const min = (p.min === null || p.min === undefined) ? '' : ` min="${p.min}"`;
      const max = (p.max === null || p.max === undefined) ? '' : ` max="${p.max}"`;
      const val = (p.default === null || p.default === undefined) ? '' : ` value="${p.default}"`;
      html += `<div class="field f-xs">${label}
        <input type="number" id="${cid}" data-param="${p.name}" data-ptype="${p.type}" step="${step}"${min}${max}${val}></div>`;
    }
  }
  if (onset === 'gradual') {
    html += `<div class="field f-xs"><label>Onset duration (s)</label>
      <input type="number" id="ev-${id}-onset-dur" value="60" min="1"></div>`;
  }
  container.innerHTML = html;
}

// Read every generated parameter control back, typed.
function collectExtras(id) {
  const out = {};
  document.querySelectorAll(`#ev-${id}-extras [data-param]`).forEach(el => {
    const name = el.dataset.param;
    if (el.dataset.multi) {
      const vals = Array.from(el.selectedOptions).map(o => {
        const n = Number(o.value);
        return Number.isFinite(n) && o.value.trim() !== '' ? n : o.value;
      });
      if (vals.length) out[name] = vals;
      return;
    }
    const raw = (el.value ?? '').toString().trim();
    if (raw === '') return;
    if (raw === 'true' || raw === 'false') { out[name] = (raw === 'true'); return; }
    const num = Number(raw);
    out[name] = (Number.isFinite(num) && el.tagName !== 'SELECT') ? num
              : (Number.isFinite(num) ? num : raw);
  });
  return out;
}

function addResponse() {
  const id = ++evId;
  const cats = ['detect','isolate','recover','verify'].map(c =>
    `<option value="${c}">${c}</option>`).join('');
  const div = document.createElement('div');
  div.className = 'resp-row';
  div.id = `resp-${id}`;
  div.innerHTML = `
    <select>${cats}</select>
    <textarea placeholder="Describe what the operator should do..."></textarea>
    <button class="btn-ghost btn-danger" onclick="document.getElementById('resp-${id}').remove()">&#x2715;</button>
  `;
  document.getElementById('resp-list').appendChild(div);
}

function collectData() {
  const name = document.getElementById('sc-name').value.trim() || 'Custom Scenario';
  const difficulty = document.getElementById('sc-diff').value;
  const duration_s = parseInt(document.getElementById('sc-dur').value) || 1800;
  const briefing = document.getElementById('sc-brief').value.trim();

  const events = [];
  document.querySelectorAll('#events-list .event-card').forEach(card => {
    const id = card.id.replace('ev-', '');
    const type = card.dataset.type;

    if (type === 'inject') {
      const timingType = card.querySelector('select').value;
      const ev = {
        type: 'inject',
        timing_type: timingType,
        time_offset_s: parseInt(document.getElementById(`ev-${id}-time`)?.value) || 0,
        condition: document.getElementById(`ev-${id}-cond`)?.value?.trim() || '',
        subsystem: document.getElementById(`ev-${id}-sub`).value,
        failure: document.getElementById(`ev-${id}-fail`).value,
        magnitude: parseFloat(document.getElementById(`ev-${id}-mag`).value) || 1.0,
        onset: document.getElementById(`ev-${id}-onset`).value,
        onset_duration_s: document.getElementById(`ev-${id}-onset-dur`)?.value || '',
        ...collectExtras(id),
        label: document.getElementById(`ev-${id}-label`)?.value?.trim() || '',
      };
      events.push(ev);
    } else {
      const sel = document.getElementById(`ev-${id}-param`);
      const opt = sel.options[sel.selectedIndex];
      events.push({
        type: 'set_parameter',
        time_offset_s: parseInt(document.getElementById(`ev-${id}-time`).value) || 0,
        param_id: sel.value,
        param_name: opt.dataset.name || sel.value,
        value: parseFloat(document.getElementById(`ev-${id}-value`).value) || 0,
        label: document.getElementById(`ev-${id}-label`)?.value?.trim() || '',
      });
    }
  });

  const expected_responses = [];
  document.querySelectorAll('#resp-list .resp-row').forEach(row => {
    const cat = row.querySelector('select').value;
    const desc = row.querySelector('textarea').value.trim();
    if (desc) expected_responses.push({ category: cat, description: desc });
  });

  return { name, difficulty, duration_s, briefing, events, expected_responses };
}

async function generate() {
  const data = collectData();
  hideBanners();
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...data, save: true }),
    });
    const j = await res.json();
    if (j.ok) {
      showModal(j.path);
      showYaml(j.yaml);
    } else {
      showError(j.error || 'Unknown error');
    }
  } catch(e) { showError(String(e)); }
}

async function preview() {
  const data = collectData();
  hideBanners();
  try {
    const res = await fetch('/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...data, save: false }),
    });
    const j = await res.json();
    if (j.ok) showYaml(j.yaml);
    else showError(j.error || 'Unknown error');
  } catch(e) { showError(String(e)); }
}

function showYaml(text) {
  document.getElementById('yaml-out').textContent = text;
  document.getElementById('output-area').style.display = '';
  document.getElementById('output-area').scrollIntoView({ behavior: 'smooth' });
}
function copyYaml() {
  navigator.clipboard.writeText(document.getElementById('yaml-out').textContent);
}
// ── Cascade preview ──────────────────────────────────────────────────────────
const CASCADE_MAP_DATA = __CASCADE_JSON__;

function buildCascadeMap() {
  return CASCADE_MAP_DATA;
}

const SUBSYS_COLOR = {
  eps:'subsys-eps', aocs:'subsys-aocs', tcs:'subsys-tcs',
  ttc:'subsys-ttc', obdh:'subsys-obdh', payload:'subsys-payload'
};

function renderCascades() {
  const cmap = buildCascadeMap();
  const cards = document.querySelectorAll('#events-list .event-card[data-type="inject"]');
  const container = document.getElementById('cascade-timeline');
  const card = document.getElementById('cascade-card');

  // Collect all events with their base time
  const allEffects = [];
  cards.forEach(evCard => {
    const id = evCard.id.replace('ev-', '');
    const sub = document.getElementById(`ev-${id}-sub`)?.value;
    const fail = document.getElementById(`ev-${id}-fail`)?.value;
    const timeOffset = parseInt(document.getElementById(`ev-${id}-time`)?.value) || 0;
    const timingType = evCard.querySelector('select')?.value;
    const key = `${sub}::${fail}`;
    const effects = cmap[key];
    if (!effects) return;
    effects.forEach(e => {
      const absTime = timingType === 'condition' ? null : timeOffset + (e.delay_s || 0);
      allEffects.push({ ...e, source_sub: sub, source_fail: fail, base_t: timeOffset, abs_t: absTime });
    });
  });

  if (allEffects.length === 0) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  // Sort by absolute time (null = conditional, goes at end)
  allEffects.sort((a, b) => {
    if (a.abs_t === null && b.abs_t === null) return 0;
    if (a.abs_t === null) return 1;
    if (b.abs_t === null) return -1;
    return a.abs_t - b.abs_t;
  });

  let html = '';
  const seen = new Set();
  allEffects.forEach((e, i) => {
    const key = `${e.abs_t}|${e.to}|${e.effect}`;
    if (seen.has(key)) return;
    seen.add(key);
    const isLast = i === allEffects.length - 1;
    const timeLabel = e.abs_t === null ? 'condition' : `T+${fmtTime(e.abs_t)}`;
    const subsysClass = SUBSYS_COLOR[e.to] || 'subsys-payload';
    const sevLabel = e.severity === 'critical' ? 'CRITICAL' : e.severity === 'warning' ? 'WARNING' : 'INFO';
    html += `
      <div class="cascade-row">
        <div class="cascade-time">${timeLabel}</div>
        <div class="cascade-spine">
          <div class="cascade-dot ${e.severity}"></div>
          ${isLast ? '' : '<div class="cascade-line"></div>'}
        </div>
        <div class="cascade-body ${e.severity}">
          <div class="cascade-header">
            <span class="cascade-subsys ${subsysClass}">${e.to.toUpperCase()}</span>
            <span class="cascade-sev ${e.severity}">${sevLabel}</span>
          </div>
          <div class="cascade-effect">${e.effect}</div>
          <div class="cascade-source">← from ${e.source_sub}::${e.source_fail} at T+${fmtTime(e.base_t)}</div>
        </div>
      </div>`;
  });
  container.innerHTML = html;
}

function fmtTime(s) {
  if (s === null) return '?';
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return m > 0 ? `${m}m${sec.toString().padStart(2,'0')}s` : `${sec}s`;
}

function scheduleCascadeUpdate() {
  clearTimeout(window._cascTimer);
  window._cascTimer = setTimeout(renderCascades, 200);
}
// Observe events list for any change
new MutationObserver(scheduleCascadeUpdate)
  .observe(document.getElementById('events-list'), { childList: true, subtree: true, attributes: true, characterData: true });

function showModal(path) {
  document.getElementById('modal-path').textContent = path;
  document.getElementById('success-modal').classList.add('show');
}
function closeModal() {
  document.getElementById('success-modal').classList.remove('show');
}
// Close modal on backdrop click
document.getElementById('success-modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});
function showSuccess(msg) {
  const b = document.getElementById('success-banner');
  b.textContent = '✓ ' + msg; b.style.display = '';
}
function showError(msg) {
  const b = document.getElementById('error-banner');
  b.textContent = '✗ ' + msg; b.style.display = '';
}
function hideBanners() {
  document.getElementById('success-banner').style.display = 'none';
  document.getElementById('error-banner').style.display = 'none';
}
</script>
</body>
</html>
"""


# ── aiohttp handlers ──────────────────────────────────────────────────────────

async def handle_index(request):
    cascade_json = json.dumps({f"{k[0]}::{k[1]}": v for k, v in CASCADE_MAP.items()})
    page = HTML.replace("__CASCADE_JSON__", cascade_json)
    return web.Response(text=page, content_type="text/html")


async def handle_generate(request):
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({"ok": False, "error": f"Bad JSON: {e}"})

    try:
        yaml_text, filename = generate_yaml(data)
    except Exception as e:
        return web.json_response({"ok": False, "error": f"YAML generation failed: {e}"})

    save = data.get("save", False)
    saved_path = ""
    if save:
        try:
            SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
            out_path = SCENARIO_DIR / filename
            out_path.write_text(yaml_text)
            saved_path = str(out_path.relative_to(REPO_ROOT))
        except Exception as e:
            return web.json_response({"ok": False, "error": f"Could not write file: {e}"})

    return web.json_response({"ok": True, "yaml": yaml_text, "path": saved_path, "filename": filename})


def main():
    parser = argparse.ArgumentParser(description="SMO Scenario Generator")
    parser.add_argument("--port", type=int, default=8096)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/generate", handle_generate)

    print(f"SMO Scenario Generator → http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=lambda *_: None)


if __name__ == "__main__":
    main()
