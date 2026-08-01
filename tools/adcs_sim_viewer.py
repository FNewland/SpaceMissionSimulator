#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADCS Failure-Scenario Viewer  —  tools/adcs_sim_viewer.py

A browser dashboard (default :8097) around the CERISE-style debris-strike ADCS
failure scenario. Set the orbit, the event timing and the fault definition in
the form, click "Run Simulation", and the diagnostic plots, a statistics row and
a colour-coded event timeline are rendered inline. The run's telemetry can be
downloaded as CSV.

The physics is NOT duplicated here: every run calls
``ADCS_failure_sim/cubesat_adcs_sim.py`` — the same module the CLI script uses —
so the viewer and the offline script can never drift apart. All 38 telemetry
channels that module logs are available to the plots and to the CSV.

Usage:  python tools/adcs_sim_viewer.py [--port 8097] [--host 127.0.0.1] [--public]
Or via: ./start.sh  (started automatically with the suite)

Dependencies: aiohttp, numpy (required); matplotlib (required for the plots —
if it is missing the server still starts and reports a clear error per run).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import os
import sys
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from aiohttp import web

# ── The shared physics module (ADCS_failure_sim/cubesat_adcs_sim.py) ──────────
REPO = Path(__file__).resolve().parents[1]
_SIM_DIR = REPO / "ADCS_failure_sim"
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))
import cubesat_adcs_sim as adcs  # noqa: E402

# ── matplotlib is optional at import time so a plotting-free install still
#    starts the server and gives a readable error instead of a stack trace. ────
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAVE_MATPLOTLIB = True
    MPL_ERROR = ""
except Exception as exc:                # pragma: no cover - environment dependent
    plt = None
    HAVE_MATPLOTLIB = False
    MPL_ERROR = str(exc)

MAX_CACHED_RUNS = 8          # CSVs kept in memory, keyed by run id


# ══════════════════════════════════════════════════════════════════════════════
#  PLOT GENERATION  (base64 PNG, main repo's channel names)
# ══════════════════════════════════════════════════════════════════════════════
def _arr(log, key):
    return np.array([r[key] for r in log], dtype=float)


def _shade(ax, ti, tr, tt):
    ax.axvspan(0,     ti / 60, color="#e8f5e9", alpha=0.45, lw=0)
    ax.axvspan(ti / 60, tr / 60, color="#fff3e0", alpha=0.45, lw=0)
    ax.axvspan(tr / 60, tt / 60, color="#ffebee", alpha=0.45, lw=0)
    ax.axvline(ti / 60, color="#666", ls="--", lw=1)
    ax.axvline(tr / 60, color="#666", ls="--", lw=1)


def _b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def make_plots(log, cfg):
    """Render the four diagnostic figures as base64 PNGs."""
    if not HAVE_MATPLOTLIB:
        raise RuntimeError(
            "matplotlib is not installed, so the plots cannot be rendered "
            f"({MPL_ERROR}). Install it with: pip install matplotlib")

    TI = float(cfg["t_impact"])
    TR = float(cfg["t_recovery"])
    TT = float(cfg["t_total"])
    ts = _arr(log, "t")
    t = ts / 60.0

    BG, PBG = "#f9f9f9", "#fefefe"
    H_SAT_mNms = adcs.RW_MAX_MOMENTUM * 1e3
    TAU_RATED_mNm = adcs.RW_MAX_TORQUE * 1e3
    ax_name = "XYZ"[int(cfg["corrupted_axis"])]
    ax_key = ax_name.lower()

    # ── Plot 1: Sensors ───────────────────────────────────────────────────────
    fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig.patch.set_facecolor(BG)
    for a in ax:
        _shade(a, TI, TR, TT)
        a.set_facecolor(PBG)
    ax[0].plot(t, _arr(log, "mag_x_uT"), lw=.8, label="Bx")
    ax[0].plot(t, _arr(log, "mag_y_uT"), lw=.8, label="By")
    ax[0].plot(t, _arr(log, "mag_z_uT"), lw=.8, label="Bz")
    ax[0].plot(t, _arr(log, "mag_mag_uT"), "k", lw=1.2, label="|B|")
    ax[0].set_ylabel("Magnetometer [uT]")
    ax[0].legend(ncol=4, fontsize=8)
    ax[0].grid(alpha=.3)
    ax[0].set_title("Sensor Telemetry  (green=NORMAL  orange=SAFE/tumble  red=RECOVERY+)",
                    fontsize=10)
    ax[1].plot(t, _arr(log, "sun_x"), lw=.8, label="Sun_x")
    ax[1].plot(t, _arr(log, "sun_y"), lw=.8, label="Sun_y")
    ax[1].plot(t, _arr(log, "sun_z"), lw=.8, label="Sun_z")
    ax[1].set_ylabel("Sun sensor [unit, body]")
    ax[1].legend(ncol=3, fontsize=8)
    ax[1].grid(alpha=.3)
    # Flags panel: main's log adds triad_used (was not available in the fork).
    ax[2].plot(t, _arr(log, "eclipse"), lw=1, color="navy", label="Eclipse")
    ax[2].plot(t, _arr(log, "sun_valid"), lw=1, color="orange", ls=":", label="Sun valid")
    ax[2].plot(t, _arr(log, "triad_used"), lw=1, color="teal", ls="--",
               label="TRIAD fix accepted")
    ax[2].set_ylabel("Flags")
    ax[2].set_xlabel("Time [min]")
    ax[2].legend(ncol=3, fontsize=8)
    ax[2].grid(alpha=.3)
    ax[2].set_ylim(-.1, 1.1)
    fig.tight_layout()
    plots = {"sensors": _b64(fig)}

    # ── Plot 2: Estimator / control state ─────────────────────────────────────
    fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig.patch.set_facecolor(BG)
    for a in ax:
        _shade(a, TI, TR, TT)
        a.set_facecolor(PBG)
    # Pointing error (estimate vs nadir target) AND estimator error (estimate vs
    # truth) — the second channel exists only in the main repo's physics module.
    ax[0].plot(t, _arr(log, "att_err_deg"), color="crimson", lw=1,
               label="Pointing error (est vs nadir)")
    ax[0].plot(t, _arr(log, "est_err_deg"), color="#1f77b4", lw=.9, alpha=.85,
               label="Estimator error (est vs truth)")
    ax[0].set_ylabel("Angle [deg]")
    ax[0].legend(ncol=2, fontsize=8)
    ax[0].grid(alpha=.3)
    ax[0].set_title("ADCS Internal State: pointing/estimator error, body rates, control mode",
                    fontsize=10)
    ax[1].plot(t, _arr(log, "rate_x_dps"), lw=.8, label="wx")
    ax[1].plot(t, _arr(log, "rate_y_dps"), lw=.8, label="wy")
    ax[1].plot(t, _arr(log, "rate_z_dps"), lw=.8, label="wz")
    ax[1].plot(t, _arr(log, "tumble_rate_dps"), "k", lw=1.2, label="|rate vs nadir|")
    ax[1].set_ylabel("Body rate [deg/s]")
    ax[1].legend(ncol=4, fontsize=8)
    ax[1].grid(alpha=.3)
    modemap = {"NORMAL": 2, "RECOVERY": 1, "SAFE": 0}
    mn = np.array([modemap[r["mode"]] for r in log])
    ax[2].plot(t, mn, drawstyle="steps-post", color="purple", lw=1.4)
    ax[2].set_yticks([0, 1, 2])
    ax[2].set_yticklabels(["SAFE", "RECOVERY", "NORMAL"])
    ax[2].set_ylabel("Control mode")
    ax[2].set_xlabel("Time [min]")
    ax[2].grid(alpha=.3)
    fig.tight_layout()
    plots["estimator"] = _b64(fig)

    # ── Plot 3: Actuators (4 panels — wheel RPM added over the fork's 3) ──────
    fig, ax = plt.subplots(4, 1, figsize=(11, 11), sharex=True)
    fig.patch.set_facecolor(BG)
    for a in ax:
        _shade(a, TI, TR, TT)
        a.set_facecolor(PBG)
    ax[0].plot(t, _arr(log, "tau_wheel_x") * 1e3, lw=.8, label="tau_x")
    ax[0].plot(t, _arr(log, "tau_wheel_y") * 1e3, lw=.8, label="tau_y")
    ax[0].plot(t, _arr(log, "tau_wheel_z") * 1e3, lw=.8, label="tau_z")
    ax[0].axhline(TAU_RATED_mNm, color="grey", ls=":", lw=1, label="rated torque")
    ax[0].axhline(-TAU_RATED_mNm, color="grey", ls=":", lw=1)
    ax[0].set_ylabel("Wheel torque [mN m]")
    ax[0].legend(ncol=4, fontsize=8)
    ax[0].grid(alpha=.3)
    ax[0].set_title("Actuator Outputs: wheel torque, momentum, speed, magnetorquer dipole",
                    fontsize=10)
    ax[1].plot(t, _arr(log, "h_wheel_x") * 1e3, lw=.9, label="hx")
    ax[1].plot(t, _arr(log, "h_wheel_y") * 1e3, lw=.9, label="hy")
    ax[1].plot(t, _arr(log, "h_wheel_z") * 1e3, lw=.9, label="hz")
    ax[1].axhline(H_SAT_mNms, color="r", ls=":", lw=1, label="sat limit")
    ax[1].axhline(-H_SAT_mNms, color="r", ls=":", lw=1)
    ax[1].set_ylabel("Wheel momentum [mN m s]")
    ax[1].legend(ncol=4, fontsize=8)
    ax[1].grid(alpha=.3)
    # wheel_rpm_* is derived by the main repo's module; the fork had no such panel.
    ax[2].plot(t, _arr(log, "wheel_rpm_x"), lw=.9, label="RW-1 (X)")
    ax[2].plot(t, _arr(log, "wheel_rpm_y"), lw=.9, label="RW-2 (Y)")
    ax[2].plot(t, _arr(log, "wheel_rpm_z"), lw=.9, label="RW-3 (Z)")
    ax[2].set_ylabel("Wheel speed [RPM]")
    ax[2].legend(ncol=3, fontsize=8)
    ax[2].grid(alpha=.3)
    ax[3].plot(t, _arr(log, "mtq_x"), lw=.8, label="m_x")
    ax[3].plot(t, _arr(log, "mtq_y"), lw=.8, label="m_y")
    ax[3].plot(t, _arr(log, "mtq_z"), lw=.8, label="m_z")
    ax[3].set_ylabel("MTQ dipole [A m^2]")
    ax[3].set_xlabel("Time [min]")
    ax[3].legend(ncol=3, fontsize=8)
    ax[3].grid(alpha=.3)
    fig.tight_layout()
    plots["actuators"] = _b64(fig)

    # ── Plot 4: Event zoom ────────────────────────────────────────────────────
    def zoom_panel(axcol, t0, t1, title, ev_t):
        m = (ts >= t0) & (ts <= t1)
        tt = ts[m]
        mk = dict(marker=".", ms=3, lw=.8)
        axcol[0].set_title(title, fontsize=9)
        axcol[0].plot(tt, _arr(log, "rate_x_dps")[m], label="wx", **mk)
        axcol[0].plot(tt, _arr(log, "rate_y_dps")[m], label="wy", **mk)
        axcol[0].plot(tt, _arr(log, "rate_z_dps")[m], label="wz", **mk)
        axcol[0].plot(tt, _arr(log, "tumble_rate_dps")[m], "k", lw=1.2, label="|rate|")
        axcol[0].axvline(ev_t, color="purple", ls="--", lw=1)
        axcol[0].set_ylabel("Body rate [deg/s]")
        axcol[0].grid(alpha=.3)
        axcol[0].legend(fontsize=7, ncol=2)
        axcol[1].plot(tt, _arr(log, f"tau_cmd_{ax_key}")[m] * 1e3,
                      label=f"cmd_{ax_key} (corrupted)", **mk)
        axcol[1].plot(tt, _arr(log, f"tau_wheel_{ax_key}")[m] * 1e3,
                      label=f"delivered_{ax_key}", **mk)
        axcol[1].axhline(TAU_RATED_mNm, color="grey", ls=":", lw=1, label="rated torque")
        axcol[1].axhline(-TAU_RATED_mNm, color="grey", ls=":", lw=1)
        axcol[1].set_ylabel("Torque [mN m]")
        axcol[1].grid(alpha=.3)
        axcol[1].legend(fontsize=7)
        axcol[2].plot(tt, _arr(log, "h_wheel_x")[m] * 1e3, label="hx", **mk)
        axcol[2].plot(tt, _arr(log, "h_wheel_y")[m] * 1e3, label="hy", **mk)
        axcol[2].plot(tt, _arr(log, "h_wheel_z")[m] * 1e3, label="hz", **mk)
        axcol[2].axhline(H_SAT_mNms, color="r", ls=":", lw=1)
        axcol[2].axhline(-H_SAT_mNms, color="r", ls=":", lw=1)
        axcol[2].set_ylabel("Wheel mom. [mN m s]")
        axcol[2].set_xlabel("Time [s]")
        axcol[2].grid(alpha=.3)
        axcol[2].legend(fontsize=7, ncol=2)

    fig, ax = plt.subplots(3, 2, figsize=(13, 9), sharex="col")
    fig.patch.set_facecolor(BG)
    for a in ax.flat:
        a.set_facecolor(PBG)
    zoom_panel(ax[:, 0], max(TI - 30, 0), min(TI + 90, TT),
               "ZOOM: Debris Impact", TI)
    zoom_panel(ax[:, 1], max(TR - 20, 0), min(TR + 220, TT),
               f"ZOOM: Recovery Attempt (axis {ax_name} over-torque)", TR)
    fig.tight_layout()
    plots["zoom"] = _b64(fig)

    return plots


# ══════════════════════════════════════════════════════════════════════════════
#  STATISTICS + CSV
# ══════════════════════════════════════════════════════════════════════════════
def compute_stats(log, cfg):
    """Summary row. Uses main's channel names and its extra channels
    (est_err_deg, triad_used), and looks at all three wheels, not just X."""
    modes = [r["mode"] for r in log]
    dt = float(cfg["dt"])
    h_all = np.abs(np.column_stack([_arr(log, "h_wheel_x"),
                                    _arr(log, "h_wheel_y"),
                                    _arr(log, "h_wheel_z")]))
    peak_axis = int(np.argmax(h_all.max(axis=0)))
    return {
        "max_att_err":   round(float(np.nanmax(_arr(log, "att_err_deg"))), 2),
        "max_est_err":   round(float(np.nanmax(_arr(log, "est_err_deg"))), 2),
        "max_tumble":    round(float(np.nanmax(_arr(log, "tumble_rate_dps"))), 2),
        "max_h_pct":     round(float(h_all.max() / adcs.RW_MAX_MOMENTUM * 100), 1),
        "peak_wheel":    "XYZ"[peak_axis],
        "max_rpm":       round(float(np.nanmax(np.abs(np.column_stack([
            _arr(log, "wheel_rpm_x"), _arr(log, "wheel_rpm_y"),
            _arr(log, "wheel_rpm_z")])))), 0),
        "triad_pct":     round(float(np.mean(_arr(log, "triad_used")) * 100), 1),
        "time_normal":   round(sum(1 for m in modes if m == "NORMAL") * dt / 60, 1),
        "time_safe":     round(sum(1 for m in modes if m == "SAFE") * dt / 60, 1),
        "time_recovery": round(sum(1 for m in modes if m == "RECOVERY") * dt / 60, 1),
        "samples":       len(log),
        "channels":      len(log[0]) if log else 0,
    }


def make_csv(log):
    if not log:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(log[0].keys()))
    w.writeheader()
    w.writerows(log)
    return buf.getvalue()


def run_scenario(params):
    """Run the shared physics module. Returns (log, events, cfg)."""
    cfg = adcs.resolve_params(dict(params or {}, verbose=False))
    events = []
    log, _orbit = adcs.run(cfg, events)
    return log, events, cfg


# ══════════════════════════════════════════════════════════════════════════════
#  SERVER
# ══════════════════════════════════════════════════════════════════════════════
_runs = OrderedDict()                        # run_id -> csv text (bounded)
_executor = ThreadPoolExecutor(max_workers=1)


def _defaults_payload():
    d = adcs.default_params()
    d["epoch"] = d["epoch"].strftime("%Y-%m-%dT%H:%M")
    d.pop("verbose", None)
    return {"defaults": d, "limits": adcs.PARAM_LIMITS,
            "have_matplotlib": HAVE_MATPLOTLIB}


async def handle_index(req):
    html = HTML.replace("__DEFAULTS_JSON__", json.dumps(_defaults_payload()))
    return web.Response(text=html, content_type="text/html")


async def handle_defaults(req):
    return web.json_response(_defaults_payload())


def _do_run(params):
    log, events, cfg = run_scenario(params)
    plots = make_plots(log, cfg)
    return log, events, cfg, plots


async def handle_run(req):
    try:
        params = await req.json()
    except Exception:
        raise web.HTTPBadRequest(text="request body must be JSON")
    if not HAVE_MATPLOTLIB:
        return web.json_response(
            {"error": "matplotlib is not installed, so the plots cannot be "
                      f"rendered ({MPL_ERROR}). Install it with: "
                      "pip install matplotlib"}, status=503)
    loop = asyncio.get_event_loop()
    try:
        log, events, cfg, plots = await loop.run_in_executor(
            _executor, _do_run, params)
    except ValueError as exc:                 # bad/unknown parameter
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:                  # pragma: no cover - defensive
        return web.json_response({"error": f"simulation failed: {exc}"}, status=500)

    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = make_csv(log)
    while len(_runs) > MAX_CACHED_RUNS:
        _runs.popitem(last=False)

    cfg_out = dict(cfg)
    cfg_out["epoch"] = cfg["epoch"].strftime("%Y-%m-%dT%H:%M:%SZ")
    return web.json_response({"run_id": run_id, "plots": plots,
                              "stats": compute_stats(log, cfg),
                              "events": events, "params": cfg_out})


async def handle_download(req):
    """CSV for a specific run id (falls back to the most recent run)."""
    run_id = req.query.get("id")
    if run_id:
        text = _runs.get(run_id)
        if text is None:
            raise web.HTTPNotFound(text="no such run id (it may have expired)")
    elif _runs:
        run_id, text = next(reversed(_runs.items()))
    else:
        raise web.HTTPNotFound(text="no simulation has been run yet")
    return web.Response(
        text=text, content_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=adcs_telemetry_{run_id}.csv"})


def create_app():
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/defaults", handle_defaults)
    app.router.add_post("/run", handle_run)
    app.router.add_get("/download", handle_download)
    return app


# ══════════════════════════════════════════════════════════════════════════════
#  HTML DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ADCS Failure-Scenario Viewer</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d1117;--panel:#161b22;--bdr:#21262d;
  --cyan:#00c8f0;--amber:#ff8533;--green:#23d18b;--red:#ff3b5c;
  --text:#e6edf3;--mut:#8b949e;--mono:'Courier New',monospace
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;font-size:14px;height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{background:var(--panel);border-bottom:1px solid var(--bdr);padding:11px 22px;display:flex;align-items:center;gap:14px;flex-shrink:0}
.hlogo{font-family:var(--mono);font-size:11px;letter-spacing:.25em;color:var(--cyan);text-transform:uppercase}
.hsep{width:1px;height:13px;background:var(--bdr)}
.hsub{font-size:12px;color:var(--mut)}
.layout{display:grid;grid-template-columns:290px 1fr;flex:1;overflow:hidden}
.sidebar{background:var(--panel);border-right:1px solid var(--bdr);padding:18px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.main{overflow-y:auto;padding:18px;display:flex;flex-direction:column;gap:14px}
h3{font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:var(--cyan);margin-bottom:2px}
.field{display:flex;flex-direction:column;gap:4px}
label{font-size:11px;color:var(--mut)}
input,select{background:#0d1117;border:1px solid var(--bdr);border-radius:5px;color:var(--text);padding:6px 9px;font-size:12px;font-family:var(--mono);width:100%;transition:border-color .15s}
input:focus,select:focus{outline:none;border-color:var(--cyan)}
.hint{font-size:10px;color:#555;font-family:var(--mono)}
.btn{background:var(--cyan);color:#000;font-weight:700;border:none;border-radius:6px;padding:9px;cursor:pointer;font-size:12px;letter-spacing:.05em;transition:opacity .15s;width:100%}
.btn:hover{opacity:.85}.btn:disabled{opacity:.35;cursor:not-allowed}
.btn-dl{background:transparent;border:1px solid var(--bdr);color:var(--mut);border-radius:6px;padding:7px;cursor:pointer;font-size:11px;width:100%;transition:all .15s}
.btn-dl:hover:not(:disabled){border-color:var(--cyan);color:var(--cyan)}.btn-dl:disabled{opacity:.35;cursor:not-allowed}
hr{border:none;border-top:1px solid var(--bdr)}
.stats-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}
.stat{background:var(--panel);border:1px solid var(--bdr);border-radius:8px;padding:14px;text-align:center}
.stat-n{font-size:20px;font-weight:700;font-family:var(--mono);line-height:1;margin-bottom:4px}
.stat-l{font-size:10px;color:var(--mut)}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--bdr)}
.tab{padding:7px 16px;font-size:12px;font-weight:600;cursor:pointer;border-bottom:2px solid transparent;color:var(--mut);transition:all .15s}
.tab.on{color:var(--cyan);border-color:var(--cyan)}.tab:hover:not(.on){color:var(--text)}
.plot-wrap{background:var(--panel);border:1px solid var(--bdr);border-radius:8px;overflow:hidden;display:none}
.plot-wrap.on{display:block}.plot-wrap img{width:100%;display:block}
.events{display:flex;flex-direction:column;gap:6px}
.ev{display:flex;gap:10px;align-items:center;background:var(--panel);border:1px solid var(--bdr);border-left:3px solid;border-radius:6px;padding:9px 13px}
.ev.impact{border-left-color:var(--amber)}.ev.recovery{border-left-color:var(--green)}.ev.autosafe{border-left-color:var(--red)}
.ev-t{font-family:var(--mono);font-size:11px;white-space:nowrap}
.ev.impact .ev-t{color:var(--amber)}.ev.recovery .ev-t{color:var(--green)}.ev.autosafe .ev-t{color:var(--red)}
.ev-msg{font-size:12px;color:var(--text)}
.placeholder{text-align:center;padding:80px 20px;color:var(--mut);font-size:13px;line-height:1.7}
.loader{display:none;text-align:center;padding:60px 20px;color:var(--cyan);font-family:var(--mono);font-size:12px}
.loader.on{display:block}
.spinner{display:inline-block;width:22px;height:22px;border:2px solid rgba(0,200,240,.18);border-top-color:var(--cyan);border-radius:50%;animation:spin .7s linear infinite;margin-bottom:12px}
@keyframes spin{to{transform:rotate(360deg)}}
.tab-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.legend{display:flex;gap:12px;font-size:10px;color:var(--mut);align-items:center}
.ldot{width:10px;height:10px;border-radius:2px}
</style>
</head>
<body>
<header>
  <span class="hlogo">EO-Sat-1</span>
  <div class="hsep"></div>
  <span class="hsub">ADCS Failure-Scenario Viewer &mdash; :8097 &mdash; physics: ADCS_failure_sim/cubesat_adcs_sim.py</span>
</header>
<div class="layout">

  <!-- Sidebar -->
  <div class="sidebar">
    <h3>Orbit</h3>
    <div class="field">
      <label>Altitude (km)</label>
      <input id="alt_km" type="number" step="10">
      <span class="hint">ISS ~ 408 km, Sun-sync ~ 500-600 km</span>
    </div>
    <div class="field">
      <label>Inclination (deg)</label>
      <input id="inc_deg" type="number" step="0.1">
      <span class="hint">97.4 deg -> sun-synchronous</span>
    </div>
    <div class="field"><label>Eccentricity</label><input id="ecc" type="number" step="0.0001"></div>
    <div class="field"><label>RAAN (deg)</label><input id="raan_deg" type="number" step="1"></div>
    <div class="field"><label>Arg. of Perigee (deg)</label><input id="argp_deg" type="number" step="1"></div>
    <div class="field"><label>True Anomaly (deg)</label><input id="nu_deg" type="number" step="1"></div>
    <div class="field">
      <label>Epoch (UTC)</label>
      <input id="epoch" type="datetime-local" step="60">
      <span class="hint">Sets Sun position &amp; magnetic field</span>
    </div>
    <hr>
    <h3>Timing</h3>
    <div class="field">
      <label>Total Duration (s)</label>
      <input id="t_total" type="number" step="300">
      <span class="hint">16000 s ~ 4.4 h ~ 2.8 orbits</span>
    </div>
    <div class="field"><label>Time Step (s)</label><input id="dt" type="number" step="0.5">
      <span class="hint">1 s = telemetry rate of the CSV</span></div>
    <div class="field"><label>Debris Impact Time (s)</label><input id="t_impact" type="number" step="60"></div>
    <div class="field"><label>Recovery Command Time (s)</label><input id="t_recovery" type="number" step="60"></div>
    <hr>
    <h3>Debris Strike</h3>
    <div class="field">
      <label>Corrupted Axis</label>
      <select id="corrupted_axis">
        <option value="0">X  (RW-1)</option>
        <option value="1">Y  (RW-2)</option>
        <option value="2">Z  (RW-3)</option>
      </select>
    </div>
    <div class="field">
      <label>Gain Corruption Factor (x)</label>
      <input id="corruption_factor" type="number" step="1">
      <span class="hint">10 = corrupted axis commands 10x normal torque</span>
    </div>
    <div class="field"><label>d-omega X (rad/s)</label><input id="dw_x" type="number" step="0.001"></div>
    <div class="field"><label>d-omega Y (rad/s)</label><input id="dw_y" type="number" step="0.001"></div>
    <div class="field"><label>d-omega Z (rad/s)</label><input id="dw_z" type="number" step="0.001"></div>
    <div class="field"><label>Noise Seed</label><input id="seed" type="number" step="1">
      <span class="hint">same seed -> identical telemetry</span></div>
    <hr>
    <button class="btn" id="run-btn" onclick="runSim()">Run Simulation</button>
    <button class="btn-dl" id="reset-btn" onclick="applyDefaults()">Reset to defaults</button>
    <button class="btn-dl" id="dl-btn" onclick="downloadCSV()" disabled>Download Telemetry CSV</button>
  </div>

  <!-- Main -->
  <div class="main" id="main-area">
    <div class="placeholder" id="placeholder">
      Configure the scenario on the left and click <strong style="color:var(--cyan)">Run Simulation</strong>.<br>
      The four diagnostic plots, the statistics row and the event timeline appear here.<br><br>
      <span style="font-size:11px;color:#555">Typical run time: 4-8 seconds for a 3-hour scenario at 1 s steps.</span>
    </div>

    <div class="loader" id="loader">
      <div class="spinner"></div><br>
      Running simulation...<br>
      <span style="color:var(--mut);font-size:11px" id="loader-sub">Integrating the 6-DOF attitude dynamics</span>
    </div>

    <div id="results" style="display:none">
      <div class="stats-grid" id="stats-grid"></div>
      <div id="events-wrap"></div>
      <div>
        <div class="tab-row">
          <div class="tabs">
            <div class="tab on"  onclick="showTab('sensors',   this)">Sensors</div>
            <div class="tab"     onclick="showTab('estimator', this)">Estimator</div>
            <div class="tab"     onclick="showTab('actuators', this)">Actuators</div>
            <div class="tab"     onclick="showTab('zoom',      this)">Event Zoom</div>
          </div>
          <div class="legend">
            <span class="ldot" style="background:rgba(100,200,100,.5)"></span>NORMAL
            <span class="ldot" style="background:rgba(255,180,60,.5)"></span>SAFE / tumble
            <span class="ldot" style="background:rgba(255,80,80,.5)"></span>RECOVERY
          </div>
        </div>
        <div class="plot-wrap on" id="tab-sensors">  <img id="img-sensors"></div>
        <div class="plot-wrap"    id="tab-estimator"><img id="img-estimator"></div>
        <div class="plot-wrap"    id="tab-actuators"><img id="img-actuators"></div>
        <div class="plot-wrap"    id="tab-zoom">     <img id="img-zoom"></div>
      </div>
    </div><!-- /results -->
  </div><!-- /main -->
</div><!-- /layout -->

<script>
const BOOT = __DEFAULTS_JSON__;
const FIELDS = ['alt_km','inc_deg','ecc','raan_deg','argp_deg','nu_deg','epoch',
                't_total','dt','t_impact','t_recovery','corrupted_axis',
                'corruption_factor','dw_x','dw_y','dw_z','seed'];
let LAST_RUN_ID = null;

function g(id){ return document.getElementById(id) }

function applyDefaults(){
  FIELDS.forEach(k => { if (g(k) && BOOT.defaults[k] !== undefined) g(k).value = BOOT.defaults[k]; });
}

function showTab(name, el){
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  document.querySelectorAll('.plot-wrap').forEach(p => p.classList.remove('on'));
  el.classList.add('on');
  g('tab-' + name).classList.add('on');
}

function fmt(t){
  const m = Math.floor(t/60), s = Math.floor(t%60);
  return 'T+' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
}

function esc(s){
  return String(s).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

async function runSim(){
  const btn = g('run-btn');
  btn.disabled = true; btn.textContent = 'Running...';
  g('placeholder').style.display = 'none';
  g('loader').classList.add('on');
  g('results').style.display = 'none';

  const params = { epoch: g('epoch').value, corrupted_axis: +g('corrupted_axis').value };
  FIELDS.forEach(k => { if (k !== 'epoch' && k !== 'corrupted_axis') params[k] = +g(k).value; });
  g('loader-sub').textContent =
    'Integrating ' + Math.round(params.t_total / params.dt) + ' time steps';

  try {
    const res = await fetch('/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(params),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d.error || ('Server error ' + res.status));

    LAST_RUN_ID = d.run_id;
    ['sensors','estimator','actuators','zoom'].forEach(k => {
      g('img-' + k).src = 'data:image/png;base64,' + d.plots[k];
    });

    const s = d.stats;
    g('stats-grid').innerHTML = `
      <div class="stat"><div class="stat-n" style="color:var(--amber)">${s.max_att_err}&deg;</div><div class="stat-l">Max Pointing Error</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--cyan)">${s.max_est_err}&deg;</div><div class="stat-l">Max Estimator Error</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--red)">${s.max_tumble}&deg;/s</div><div class="stat-l">Peak Tumble Rate</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--amber)">${s.max_h_pct}%</div><div class="stat-l">Peak Wheel Momentum (${s.peak_wheel})</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--mut)">${s.max_rpm}</div><div class="stat-l">Peak Wheel Speed [RPM]</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--green)">${s.time_normal} min</div><div class="stat-l">Time in NORMAL</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--mut)">${s.time_safe} min</div><div class="stat-l">Time in SAFE</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--cyan)">${s.time_recovery} min</div><div class="stat-l">Time in RECOVERY</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--green)">${s.triad_pct}%</div><div class="stat-l">Samples with TRIAD Fix</div></div>
      <div class="stat"><div class="stat-n" style="color:var(--mut)">${s.samples}</div><div class="stat-l">Samples &times; ${s.channels} Channels</div></div>`;

    g('events-wrap').innerHTML =
      '<h3 style="margin-bottom:8px">Key Events</h3>' +
      '<div class="events">' +
      (d.events.length ? d.events.map(e =>
        `<div class="ev ${esc(e.type)}">
           <span class="ev-t">${fmt(e.t)}</span>
           <span class="ev-msg">${esc(e.msg)}</span>
         </div>`).join('')
       : '<div class="ev"><span class="ev-msg">No events in this run.</span></div>') +
      '</div>';

    g('loader').classList.remove('on');
    g('results').style.display = 'flex';
    g('results').style.flexDirection = 'column';
    g('results').style.gap = '14px';
    g('dl-btn').disabled = false;

  } catch(err) {
    g('loader').classList.remove('on');
    g('placeholder').style.display = 'block';
    g('placeholder').innerHTML = '<span style="color:var(--red)">Error: ' + esc(err.message) + '</span>';
  }

  btn.disabled = false; btn.textContent = 'Run Simulation';
}

function downloadCSV(){
  const url = '/download' + (LAST_RUN_ID ? ('?id=' + LAST_RUN_ID) : '');
  fetch(url)
    .then(r => r.blob())
    .then(b => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(b);
      a.download = 'adcs_telemetry.csv';
      a.click();
    });
}

applyDefaults();
if (!BOOT.have_matplotlib){
  g('placeholder').innerHTML =
    '<span style="color:var(--red)">matplotlib is not installed, so plots cannot be rendered.<br>' +
    'Install it with: pip install matplotlib</span>';
  g('run-btn').disabled = true;
}
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="ADCS Failure-Scenario Viewer")
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--public", action="store_true",
                    help="bind 0.0.0.0 so the viewer is reachable from the LAN")
    args = ap.parse_args()

    host = "0.0.0.0" if (args.public or os.environ.get("SMO_PUBLIC") == "1") else args.host
    if not HAVE_MATPLOTLIB:
        print(f"==> WARNING: matplotlib unavailable ({MPL_ERROR}) - plots disabled")
    print(f"==> ADCS Failure-Scenario Viewer on http://localhost:{args.port}")
    web.run_app(create_app(), host=host, port=args.port, print=None)


if __name__ == "__main__":
    main()
