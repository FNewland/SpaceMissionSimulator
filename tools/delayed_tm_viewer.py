#!/usr/bin/env python3
"""delayed_tm_viewer.py — Live HTML viewer for archived delayed TM dumps.

A small aiohttp server you can start at the beginning of a simulation and
keep running. After every pass that performs an S15 dump, the simulator
writes a ``workspace/dumps/dump_sid<NN>_<UTC>.bin`` file. Open this tool
in your browser and click "Refresh" / "Load latest" / "Load all" / use
the time-window picker to decode any subset of those files into a
human-readable view (packet counts, S5 events, per-SID HK time series
with sparklines, anomalies).

It is fully offline — no CDN, no MCS / simulator dependency at runtime.
The decoder is shared with ``tools/tm_report.py``.

Usage::

    python tools/delayed_tm_viewer.py             # default port 8092
    python tools/delayed_tm_viewer.py --port 9100 --dumps workspace/dumps
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))
sys.path.insert(0, str(REPO / "tools"))

from aiohttp import web

from tm_report import (  # type: ignore
    PassAnalysis,
    load_catalogs,
    read_dump,
)

logger = logging.getLogger("delayed_tm_viewer")

DUMP_NAME_RE = re.compile(r"^dump_sid(\d+)_(\d{8}T\d{6}Z)\.bin$")


# ---------- core analysis ----------

def parse_dump_filename(name: str) -> tuple[int | None, datetime | None]:
    """Return (sid, utc_datetime) from a canonical dump filename."""
    m = DUMP_NAME_RE.match(name)
    if not m:
        return None, None
    sid = int(m.group(1))
    try:
        ts = datetime.strptime(m.group(2), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        ts = None
    return sid, ts


def list_dumps(dump_dir: Path) -> list[dict[str, Any]]:
    """Return metadata about every dump file in ``dump_dir``."""
    out: list[dict[str, Any]] = []
    if not dump_dir.exists():
        return out
    for p in sorted(dump_dir.glob("dump_*.bin")):
        try:
            stat = p.stat()
        except OSError:
            continue
        sid, ts = parse_dump_filename(p.name)
        out.append({
            "filename": p.name,
            "path": str(p),
            "sid": sid,
            "timestamp": ts.isoformat() if ts else None,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    out.sort(key=lambda d: d.get("timestamp") or d.get("mtime") or "")
    return out


def select_dumps(dump_dir: Path, *, files: list[str] | None = None,
                 since: str | None = None, until: str | None = None,
                 all_: bool = False, latest: bool = False) -> list[Path]:
    """Resolve a request into a concrete list of dump file paths."""
    available = list_dumps(dump_dir)
    if not available:
        return []

    if latest:
        last = available[-1]
        return [Path(last["path"])]

    if files:
        wanted = {f.strip() for f in files if f.strip()}
        return [Path(d["path"]) for d in available if d["filename"] in wanted]

    if all_ and not (since or until):
        return [Path(d["path"]) for d in available]

    def _parse(t: str | None) -> datetime | None:
        if not t:
            return None
        try:
            # Accept 'YYYY-MM-DDTHH:MM:SSZ' or '...+00:00'
            return datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            return None

    s, u = _parse(since), _parse(until)
    out: list[Path] = []
    for d in available:
        ts = _parse(d.get("timestamp")) or _parse(d.get("mtime"))
        if ts is None:
            continue
        if s and ts < s:
            continue
        if u and ts > u:
            continue
        out.append(Path(d["path"]))
    return out


def analyse_dumps(paths: Iterable[Path], params_by_id: dict, hk_plist: dict) -> dict[str, Any]:
    """Decode one or more dump files into a JSON-friendly summary."""
    an = PassAnalysis()
    files_used: list[dict[str, Any]] = []
    for path in paths:
        try:
            count = 0
            for raw in read_dump(path):
                an.ingest_packet(raw, params_by_id, hk_plist)
                count += 1
            files_used.append({"filename": path.name, "packets": count, "size_bytes": path.stat().st_size})
        except Exception as e:
            files_used.append({"filename": path.name, "error": str(e)})

    pkt_counts = [{"service": s, "subtype": st, "count": n}
                  for (s, st), n in sorted(an.pkt_counts.items())]
    events = [
        {"cuc": e["cuc"], "subtype": e["subtype"],
         "event_id": e["event_id"], "severity": e["severity"],
         "description": e["description"]}
        for e in an.events
    ]

    hk: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for sid, samples in sorted(an.hk_samples.items()):
        if not samples:
            continue
        per_pid: dict[int, list[tuple[int, float]]] = {}
        for cuc, vals in samples:
            for pid, v in vals.items():
                per_pid.setdefault(pid, []).append((cuc, v))
        params_out: list[dict[str, Any]] = []
        for pid in sorted(per_pid):
            pts = per_pid[pid]
            vals = [v for _, v in pts]
            mn, mx = min(vals), max(vals)
            avg = sum(vals) / len(vals)
            meta = params_by_id.get(pid, {})
            deltas = [abs(vals[i] - vals[i-1]) for i in range(1, len(vals))]
            flag = False
            if deltas:
                mean_d = sum(deltas) / len(deltas) or 1e-9
                peak = max(deltas)
                if peak > 5 * mean_d and peak > 0.5:
                    flag = True
                    anomalies.append({"sid": sid, "pid": pid,
                                      "name": meta.get("name", f"0x{pid:04X}"),
                                      "peak_delta": peak, "mean_delta": mean_d})
            params_out.append({
                "pid": pid,
                "name": meta.get("name", f"0x{pid:04X}"),
                "units": meta.get("units", ""),
                "min": mn, "max": mx, "mean": avg,
                "n": len(vals),
                "flag": flag,
                # Compact array of [cuc, value] for client-side sparkline
                "series": [[c, v] for c, v in pts],
            })
        hk.append({
            "sid": sid,
            "packet_count": an.hk_counts[sid],
            "sample_count": len(samples),
            "params": params_out,
        })

    return {
        "files": files_used,
        "first_cuc": an.first_time,
        "last_cuc": an.last_time,
        "total_packets": sum(an.pkt_counts.values()),
        "packet_counts": pkt_counts,
        "events": events,
        "hk": hk,
        "anomalies": anomalies,
    }


# ---------- HTTP handlers ----------

def make_app(dump_dir: Path) -> web.Application:
    params_by_id, _, hk_plist = load_catalogs()
    app = web.Application()
    app["dump_dir"] = dump_dir
    app["params_by_id"] = params_by_id
    app["hk_plist"] = hk_plist

    async def index(_req: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def api_dumps(_req: web.Request) -> web.Response:
        return web.json_response({"dump_dir": str(dump_dir),
                                  "dumps": list_dumps(dump_dir)})

    async def api_decode(req: web.Request) -> web.Response:
        q = req.query
        files = q.get("files", "")
        file_list = files.split(",") if files else None
        paths = select_dumps(
            dump_dir,
            files=file_list,
            since=q.get("since"),
            until=q.get("until"),
            all_=q.get("all") in ("1", "true", "yes"),
            latest=q.get("latest") in ("1", "true", "yes"),
        )
        if not paths:
            return web.json_response({"error": "no dump files matched the request",
                                      "files": [], "events": [], "hk": [],
                                      "packet_counts": [], "anomalies": [],
                                      "total_packets": 0,
                                      "first_cuc": None, "last_cuc": None})
        result = analyse_dumps(paths, app["params_by_id"], app["hk_plist"])
        return web.json_response(result)

    app.router.add_get("/", index)
    app.router.add_get("/api/dumps", api_dumps)
    app.router.add_get("/api/decode", api_decode)
    return app


# ---------- HTML page (served inline so the tool is single-file) ----------

INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Delayed TM Viewer</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.1.0/dist/chartjs-plugin-zoom.min.js"></script>
<style>
:root {
  --bg: #0a1226; --panel: #0f1d33; --border: #1f3050; --text: #cfe;
  --dim: #7a90ad; --accent: #5fb7ff; --good: #34d399; --warn: #f59e0b; --bad: #f87171;
}
* { box-sizing: border-box; }
html, body { background: var(--bg); color: var(--text); font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px; margin: 0; }
header { padding: 10px 16px; background: linear-gradient(180deg, #0f1d33, #0b1525); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 16px; }
header h1 { font-size: 14px; letter-spacing: 2px; color: var(--accent); margin: 0; font-weight: 700; }
header .status { color: var(--dim); font-size: 11px; }
main { padding: 14px 16px; }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 10px; background: var(--panel); border: 1px solid var(--border); border-radius: 4px; margin-bottom: 14px; }
button { background: #1a3a6a; color: var(--text); border: 1px solid #2a5a8a; border-radius: 3px; padding: 6px 12px; cursor: pointer; font-family: inherit; font-size: 11px; }
button:hover { background: #2a5a8a; }
button.primary { background: #2a5a8a; }
button.sm { padding: 3px 6px; font-size: 10px; }
input[type=text], input[type=datetime-local], select { background: #0a1226; color: var(--text); border: 1px solid var(--border); border-radius: 3px; padding: 5px 8px; font-family: inherit; font-size: 11px; }
label { color: var(--dim); font-size: 11px; margin-right: 4px; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; margin-bottom: 14px; }
.summary .card { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 10px 14px; }
.summary .card .label { font-size: 10px; color: var(--dim); text-transform: uppercase; letter-spacing: 1px; }
.summary .card .value { font-size: 18px; color: var(--accent); margin-top: 4px; font-variant-numeric: tabular-nums; }
.section { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; margin-bottom: 14px; }
.section h2 { font-size: 11px; letter-spacing: 1.5px; color: var(--dim); text-transform: uppercase; margin: 0; padding: 8px 12px; border-bottom: 1px solid var(--border); }
.section .body { padding: 10px 12px; max-height: 460px; overflow: auto; }
table { width: 100%; border-collapse: collapse; font-size: 11px; font-variant-numeric: tabular-nums; }
th, td { border-bottom: 1px solid #16223a; padding: 4px 6px; text-align: left; }
th { color: var(--dim); font-weight: 500; position: sticky; top: 0; background: var(--panel); }
td.num { text-align: right; }
.sev-1 { color: #9bb; } .sev-2 { color: #34d399; } .sev-3 { color: var(--warn); } .sev-4 { color: var(--bad); }
.flag { color: var(--warn); }
.dump-row { padding: 4px 6px; cursor: pointer; border-bottom: 1px solid #16223a; display: grid; grid-template-columns: 1fr 80px 110px 90px; gap: 6px; }
.dump-row:hover { background: #15294a; }
.dump-row.selected { background: #1a3a6a; }
.error { color: var(--bad); padding: 8px; }
.chart-container { position: relative; height: 300px; margin: 10px 0; background: #0a1226; border: 1px solid var(--border); border-radius: 4px; padding: 10px; }
.chart-wrapper { display: grid; grid-template-columns: 1fr; gap: 10px; }
.chart-item { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 10px; }
.chart-item-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.chart-item-header .title { color: var(--accent); font-size: 11px; font-weight: 500; }
.chart-item-header .close-btn { background: transparent; border: none; color: var(--dim); cursor: pointer; padding: 2px; }
.chart-item-header .close-btn:hover { color: var(--warn); }
.tab-buttons { display: flex; gap: 6px; margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.tab-button { background: transparent; border: none; color: var(--dim); padding: 6px 12px; cursor: pointer; font-size: 11px; border-bottom: 2px solid transparent; }
.tab-button:hover { color: var(--accent); }
.tab-button.active { color: var(--accent); border-bottom-color: var(--accent); }
</style>
</head><body>
<header>
  <h1>DELAYED TM VIEWER</h1>
  <span class="status" id="status">idle</span>
</header>
<main>
  <div class="controls">
    <button id="btnRefresh" class="primary">Refresh dumps</button>
    <button id="btnLatest">Load latest</button>
    <button id="btnSelected">Load selected</button>
    <button id="btnAll">Load all</button>
    <span style="border-left:1px solid var(--border);height:18px;"></span>
    <label>From</label><input type="datetime-local" id="from">
    <label>To</label><input type="datetime-local" id="to">
    <button id="btnRange">Load range</button>
  </div>

  <div class="section">
    <h2>Available dump files</h2>
    <div class="body" id="dumpsList">No dumps loaded.</div>
  </div>

  <div class="summary" id="summary"></div>

  <div class="section"><h2>Packet counts by service/subtype</h2>
    <div class="body" id="pktCounts">No data.</div></div>

  <div class="section"><h2>S5 Events</h2>
    <div class="body" id="events">No data.</div></div>

  <div class="section"><h2>HK time series by SID</h2>
    <div class="body" id="hk">No data.</div></div>

  <div class="section"><h2>Anomalies</h2>
    <div class="body" id="anomalies">No data.</div></div>

  <div id="chartsTabs" style="display:none;">
    <div class="tab-buttons">
      <button class="tab-button active" data-tab="hk-charts">HK Charts</button>
    </div>
    <div id="chartsSection" class="section">
      <h2>Charts</h2>
      <div class="body" id="chartsBody"></div>
    </div>
  </div>
</main>
<script>
const $ = id => document.getElementById(id);
let availableDumps = [];
let selectedDumps = new Set();
let chartInstances = {};
let chartsData = [];
let firstCuc = null;

function setStatus(s) { $("status").textContent = s; }

async function refreshDumps() {
  setStatus("listing dumps…");
  try {
    const r = await fetch("/api/dumps");
    const j = await r.json();
    availableDumps = j.dumps || [];
    renderDumpsList();
    setStatus(`${availableDumps.length} dump file(s) in ${j.dump_dir}`);
  } catch (e) {
    setStatus("ERROR: " + e);
  }
}

function renderDumpsList() {
  const root = $("dumpsList");
  if (!availableDumps.length) { root.textContent = "No dumps in workspace/dumps/. Run a pass with an S15 dump in the simulator."; return; }
  root.innerHTML = "";
  const header = document.createElement("div");
  header.className = "dump-row";
  header.style.color = "var(--dim)";
  header.style.cursor = "default";
  header.innerHTML = "<span>Filename</span><span>SID</span><span>UTC</span><span class='num'>Bytes</span>";
  root.appendChild(header);
  for (const d of availableDumps) {
    const row = document.createElement("div");
    row.className = "dump-row" + (selectedDumps.has(d.filename) ? " selected" : "");
    row.innerHTML = `<span>${d.filename}</span><span>${d.sid ?? "?"}</span><span>${d.timestamp || d.mtime || ""}</span><span class="num">${d.size_bytes.toLocaleString()}</span>`;
    row.addEventListener("click", () => {
      if (selectedDumps.has(d.filename)) selectedDumps.delete(d.filename);
      else selectedDumps.add(d.filename);
      renderDumpsList();
    });
    root.appendChild(row);
  }
}

async function loadDecoded(params) {
  setStatus("decoding…");
  try {
    const url = "/api/decode?" + new URLSearchParams(params).toString();
    const r = await fetch(url);
    const j = await r.json();
    if (j.error) {
      setStatus("ERROR: " + j.error);
      return;
    }
    render(j);
    setStatus(`decoded ${j.total_packets} packet(s) from ${j.files.length} file(s)`);
  } catch (e) {
    setStatus("ERROR: " + e);
  }
}

function render(j) {
  // Summary
  const sum = $("summary");
  sum.innerHTML = "";
  const cards = [
    ["Files", j.files.length],
    ["Total packets", j.total_packets],
    ["S5 events", j.events.length],
    ["HK SIDs", j.hk.length],
    ["Anomalies", j.anomalies.length],
    ["CUC range", `${j.first_cuc ?? "—"} → ${j.last_cuc ?? "—"}`],
  ];
  for (const [label, value] of cards) {
    const c = document.createElement("div"); c.className = "card";
    c.innerHTML = `<div class="label">${label}</div><div class="value">${value}</div>`;
    sum.appendChild(c);
  }

  // Packet counts
  $("pktCounts").innerHTML = j.packet_counts.length
    ? "<table><thead><tr><th>Service</th><th class='num'>Count</th></tr></thead><tbody>"
      + j.packet_counts.map(c => `<tr><td>S${c.service}.${c.subtype}</td><td class='num'>${c.count}</td></tr>`).join("")
      + "</tbody></table>"
    : "No packets decoded.";

  // Events
  $("events").innerHTML = j.events.length
    ? "<table><thead><tr><th>CUC</th><th>Subtype</th><th>Event ID</th><th>Description</th></tr></thead><tbody>"
      + j.events.map(e => `<tr class='sev-${e.severity}'><td>${e.cuc}</td><td>S5.${e.subtype}</td><td>0x${e.event_id.toString(16).padStart(4,"0").toUpperCase()}</td><td>${escapeHtml(e.description)}</td></tr>`).join("")
      + "</tbody></table>"
    : "No events.";

  // HK
  $("hk").innerHTML = j.hk.length
    ? j.hk.map(sid => `
        <h3 style="font-size:11px;color:var(--accent);margin:8px 0 4px;">SID ${sid.sid} — ${sid.packet_count} pkts, ${sid.sample_count} samples
        <button class="sm" onclick="plotAllForSid(${sid.sid}, ${JSON.stringify(sid.params).replace(/"/g, '&quot;')})">Plot All</button>
        </h3>
        <table><thead><tr><th>ID</th><th>Name</th><th>Units</th><th class='num'>Min</th><th class='num'>Max</th><th class='num'>Mean</th><th class='num'>N</th><th>Trend</th><th></th></tr></thead><tbody>
        ${sid.params.map(p => `<tr>
          <td>0x${p.pid.toString(16).padStart(4,"0").toUpperCase()}</td>
          <td>${escapeHtml(p.name)}</td>
          <td>${escapeHtml(p.units)}</td>
          <td class='num'>${fmtNum(p.min)}</td>
          <td class='num'>${fmtNum(p.max)}</td>
          <td class='num'>${fmtNum(p.mean)}</td>
          <td class='num'>${p.n}</td>
          <td>${spark(p.series)}</td>
          <td><button class="sm" onclick="addChart(${sid.sid}, ${p.pid}, ${JSON.stringify(p.name).replace(/"/g, '&quot;')}, ${JSON.stringify(p.series)}, ${JSON.stringify(p.units).replace(/"/g, '&quot;')})">Plot</button> ${p.flag ? "<span class='flag'>Δ</span>" : ""}</td>
        </tr>`).join("")}
        </tbody></table>`).join("")
    : "No HK packets decoded.";

  chartsData = j.hk;
  firstCuc = j.first_cuc;

  // Anomalies
  $("anomalies").innerHTML = j.anomalies.length
    ? "<ul>" + j.anomalies.map(a => `<li>SID ${a.sid} ${escapeHtml(a.name)}: peak Δ=${fmtNum(a.peak_delta)} (mean ${fmtNum(a.mean_delta)})</li>`).join("") + "</ul>"
    : "None.";
}

function fmtNum(x) {
  if (x == null || !isFinite(x)) return "—";
  if (Number.isInteger(x)) return String(x);
  const a = Math.abs(x);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return x.toExponential(3);
  return x.toFixed(3);
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function spark(pts) {
  if (!pts || pts.length < 2) return "";
  const w = 200, h = 28;
  const xs = pts.map(p => p[0]); const ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const dx = (x1 - x0) || 1, dy = (y1 - y0) || 1;
  const path = pts.map(([x,y]) => `${((x-x0)/dx*(w-2)+1).toFixed(1)},${((h-1)-((y-y0)/dy)*(h-2)).toFixed(1)}`).join(" ");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline fill="none" stroke="#5fb7ff" stroke-width="1.2" points="${path}"/></svg>`;
}

function isoOf(localInput) {
  if (!localInput.value) return null;
  // Treat the picker as UTC for simplicity (the backend is UTC).
  return new Date(localInput.value + "Z").toISOString();
}

function formatCucTime(cuc) {
  if (typeof cuc !== 'number') return String(cuc);
  const s = Math.floor(cuc / 256);
  const ms = ((cuc % 256) / 256 * 1000).toFixed(0);
  const date = new Date((s - 657720000) * 1000);
  return date.toISOString().split('T')[1].split('.')[0];
}

function getRelativeTime(cuc, baseCuc) {
  if (!baseCuc || typeof cuc !== 'number') return cuc;
  return (cuc - baseCuc).toFixed(2);
}

function addChart(sid, pid, name, series, units) {
  if (!series || series.length < 1) return;
  const chartId = `chart-${sid}-${pid}`;
  const chartsBody = $("chartsBody");
  $("chartsTabs").style.display = "block";

  let chartItem = document.getElementById(chartId);
  if (chartItem) {
    chartItem.scrollIntoView({ behavior: 'smooth' });
    return;
  }

  const minCuc = Math.min(...series.map(p => p[0]));
  const labels = series.map(([cuc]) => formatCucTime(cuc));
  const data = series.map(([_, v]) => v);

  chartItem = document.createElement("div");
  chartItem.id = chartId;
  chartItem.className = "chart-item";
  chartItem.innerHTML = `
    <div class="chart-item-header">
      <div class="title">SID ${sid} • 0x${pid.toString(16).padStart(4,"0").toUpperCase()} • ${escapeHtml(name)} (${escapeHtml(units)})</div>
      <button class="close-btn" onclick="removeChart('${chartId}')">✕</button>
    </div>
    <div class="chart-container">
      <canvas id="${chartId}-canvas"></canvas>
    </div>
  `;
  chartsBody.appendChild(chartItem);

  const ctx = document.getElementById(`${chartId}-canvas`).getContext('2d');
  const chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: `${name} ${units}`.trim(),
        data: data,
        borderColor: '#5fb7ff',
        backgroundColor: 'rgba(95, 183, 255, 0.1)',
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 5,
        pointBackgroundColor: '#5fb7ff',
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { color: '#cfe', font: { size: 11 } }
        },
        tooltip: {
          backgroundColor: 'rgba(10, 18, 38, 0.9)',
          titleColor: '#5fb7ff',
          bodyColor: '#cfe',
          borderColor: '#1f3050',
          borderWidth: 1,
          padding: 8,
          displayColors: true,
          callbacks: {
            title: (ctx) => {
              const idx = ctx[0].dataIndex;
              const cuc = series[idx][0];
              return `CUC: ${cuc} (${formatCucTime(cuc)})`;
            },
            label: (ctx) => `${ctx.dataset.label}: ${fmtNum(ctx.parsed.y)}`
          }
        },
        zoom: {
          pan: { enabled: true, mode: 'xy', modifierKey: 'ctrl' },
          zoom: { wheel: { enabled: true, speed: 0.1 }, pinch: { enabled: true }, mode: 'xy' }
        }
      },
      scales: {
        x: {
          ticks: { color: '#7a90ad', font: { size: 10 } },
          grid: { color: '#16223a' }
        },
        y: {
          ticks: { color: '#7a90ad', font: { size: 10 } },
          grid: { color: '#16223a' }
        }
      }
    },
    plugins: [ChartDataLabels || {}]
  });
  chartInstances[chartId] = chart;
}

function plotAllForSid(sid, params) {
  if (!params || params.length < 1) return;
  const chartId = `chart-all-${sid}`;
  const chartsBody = $("chartsBody");
  $("chartsTabs").style.display = "block";

  let chartItem = document.getElementById(chartId);
  if (chartItem) {
    chartItem.scrollIntoView({ behavior: 'smooth' });
    return;
  }

  const colors = ['#5fb7ff', '#34d399', '#f59e0b', '#f87171', '#a78bfa', '#ec4899', '#14b8a6'];
  const minCuc = Math.min(...params.flatMap(p => p.series.map(s => s[0])));
  const maxDataPoints = Math.max(...params.map(p => p.series.length));

  const labels = params[0].series.map(([cuc]) => formatCucTime(cuc));
  const datasets = params.map((p, idx) => {
    const data = p.series.map(([_, v]) => v);
    return {
      label: `${p.name} (${p.units})`.trim(),
      data: data,
      borderColor: colors[idx % colors.length],
      backgroundColor: colors[idx % colors.length] + '20',
      borderWidth: 2,
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      pointHoverRadius: 5,
      pointBackgroundColor: colors[idx % colors.length],
    };
  });

  chartItem = document.createElement("div");
  chartItem.id = chartId;
  chartItem.className = "chart-item";
  chartItem.innerHTML = `
    <div class="chart-item-header">
      <div class="title">SID ${sid} • All Parameters</div>
      <button class="close-btn" onclick="removeChart('${chartId}')">✕</button>
    </div>
    <div class="chart-container" style="height: 400px;">
      <canvas id="${chartId}-canvas"></canvas>
    </div>
  `;
  chartsBody.appendChild(chartItem);

  const ctx = document.getElementById(`${chartId}-canvas`).getContext('2d');
  const chart = new Chart(ctx, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: { color: '#cfe', font: { size: 10 }, usePointStyle: true, padding: 10 }
        },
        tooltip: {
          backgroundColor: 'rgba(10, 18, 38, 0.9)',
          titleColor: '#5fb7ff',
          bodyColor: '#cfe',
          borderColor: '#1f3050',
          borderWidth: 1,
          padding: 8,
          displayColors: true,
          callbacks: {
            title: (ctx) => {
              const idx = ctx[0].dataIndex;
              const cuc = params[0].series[idx][0];
              return `CUC: ${cuc} (${formatCucTime(cuc)})`;
            },
            label: (ctx) => `${ctx.dataset.label}: ${fmtNum(ctx.parsed.y)}`
          }
        },
        zoom: {
          pan: { enabled: true, mode: 'xy', modifierKey: 'ctrl' },
          zoom: { wheel: { enabled: true, speed: 0.1 }, pinch: { enabled: true }, mode: 'xy' }
        }
      },
      scales: {
        x: {
          ticks: { color: '#7a90ad', font: { size: 10 } },
          grid: { color: '#16223a' }
        },
        y: {
          ticks: { color: '#7a90ad', font: { size: 10 } },
          grid: { color: '#16223a' }
        }
      }
    },
    plugins: [ChartDataLabels || {}]
  });
  chartInstances[chartId] = chart;
}

function removeChart(chartId) {
  const chartItem = document.getElementById(chartId);
  if (chartItem) chartItem.remove();
  if (chartInstances[chartId]) {
    chartInstances[chartId].destroy();
    delete chartInstances[chartId];
  }
  if (Object.keys(chartInstances).length === 0) {
    $("chartsTabs").style.display = "none";
  }
}

$("btnRefresh").addEventListener("click", refreshDumps);
$("btnLatest").addEventListener("click", () => loadDecoded({ latest: 1 }));
$("btnAll").addEventListener("click", () => loadDecoded({ all: 1 }));
$("btnSelected").addEventListener("click", () => {
  if (!selectedDumps.size) { setStatus("no files selected — click rows in the list above"); return; }
  loadDecoded({ files: Array.from(selectedDumps).join(",") });
});
$("btnRange").addEventListener("click", () => {
  const s = isoOf($("from")), u = isoOf($("to"));
  const params = {};
  if (s) params.since = s;
  if (u) params.until = u;
  if (!s && !u) params.all = 1;
  loadDecoded(params);
});

refreshDumps();
setInterval(refreshDumps, 30000);  // poll dump dir for new files every 30 s
</script>
</body></html>
"""


# ---------- entrypoint ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Delayed TM viewer")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dumps", default="workspace/dumps",
                        help="Directory containing dump_*.bin files")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    dump_dir = Path(args.dumps).resolve()
    dump_dir.mkdir(parents=True, exist_ok=True)
    app = make_app(dump_dir)

    logger.info("Delayed TM viewer listening on http://%s:%d (dumps=%s)",
                args.host, args.port, dump_dir)
    web.run_app(app, host=args.host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
