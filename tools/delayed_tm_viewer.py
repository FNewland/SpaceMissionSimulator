#!/usr/bin/env python3
"""delayed_tm_viewer.py — Live HTML viewer for archived delayed TM dumps.

A small aiohttp server you can start at the beginning of a simulation and
keep running. After every pass that performs an S15 dump, the simulator
writes a ``workspace/dumps/dump_sid<NN>_<UTC>.bin`` file. Open this tool
in your browser and click "Refresh" / "Load latest" / "Load all" / use
the time-window picker to decode any subset of those files into a
human-readable view (packet counts, S5 events, per-SID HK time series
with sparklines, anomalies).

It is fully offline — no CDN. The decoder is shared with
``tools/tm_report.py``; the store layout and location rules are shared with
``smo_common.dump_store``.

The tool runs in one of two modes:

**Local mode (default)** reads the dump directory off the local filesystem.
This is the single-machine workflow and is unchanged.

**API mode** (``--api http://<server>:9090``) reads the store over HTTP from
whichever service owns it — normally the MCS, optionally the simulator. Use
this when the archive lives on a server and this tool runs somewhere else,
so no shared/NFS mount is needed (defect #90). Note that students do not
need this tool at all in a networked classroom: the MCS serves its own
listing/download/summary page at ``http://<server>:9090/dumps``.

Usage::

    python tools/delayed_tm_viewer.py             # default port 8092
    python tools/delayed_tm_viewer.py --port 9100 --dumps workspace/dumps
    python tools/delayed_tm_viewer.py --api http://server:9090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))
sys.path.insert(0, str(REPO / "tools"))

from aiohttp import web

from smo_common.dump_store import (  # type: ignore
    DUMP_NAME_RE,
    list_dumps as _store_list_dumps,
    parse_dump_filename,
    resolve_dump_dir,
)

from tm_report import (  # type: ignore
    PassAnalysis,
    load_catalogs,
    read_dump,
)

logger = logging.getLogger("delayed_tm_viewer")


# ---------- ground limits on played-back series (POSITION_NEEDS G11) ----------
#
# The ground limit table lives in configs/<mission>/mcs/limits.yaml and its
# parser lives in smo_common (`smo_common.config.loader.load_limits`,
# `LimitDef` in config.schemas) — the MCS uses the same one, but this tool
# may not import smo-mcs, and does not need to.

def load_limits_by_pid(config_dir: Path) -> dict[int, Any]:
    """{param_id: LimitDef} from mcs/limits.yaml; {} when absent/broken."""
    try:
        from smo_common.config.loader import load_limits
        return {l.param_id: l for l in load_limits(config_dir)}
    except Exception as e:  # noqa: BLE001 — limits are an overlay, not a dep
        logger.warning("Could not load mcs/limits.yaml from %s: %s",
                       config_dir, e)
        return {}


def _classify(v: float, lim) -> str:
    """'red' | 'yellow' | 'ok' for one engineering value against one band."""
    if lim.red_low is not None and v < lim.red_low:
        return "red"
    if lim.red_high is not None and v > lim.red_high:
        return "red"
    if lim.yellow_low is not None and v < lim.yellow_low:
        return "yellow"
    if lim.yellow_high is not None and v > lim.yellow_high:
        return "yellow"
    return "ok"


def evaluate_limits(pts: list[tuple[float, float]], lim) -> dict[str, Any]:
    """Time-in-band accounting for one decoded series.

    Each sample owns the interval up to the next sample; the last sample
    (and a single-sample series) gets the mean observed spacing, falling
    back to the limit's ``check_interval_s``. 'yellow' means outside the
    yellow band but inside red; 'red' means outside the red band — the two
    times never double-count.
    """
    times = [t for t, _ in pts]
    dts = [max(0.0, times[i + 1] - times[i]) for i in range(len(pts) - 1)]
    positive = [d for d in dts if d > 0]
    default_dt = (sum(positive) / len(positive) if positive
                  else float(getattr(lim, "check_interval_s", 4.0) or 4.0))
    t_yellow = t_red = 0.0
    n_yellow = n_red = 0
    worst = "ok"
    for i, (_, v) in enumerate(pts):
        dt = dts[i] if i < len(dts) and dts[i] > 0 else default_dt
        c = _classify(v, lim)
        if c == "red":
            t_red += dt
            n_red += 1
            worst = "red"
        elif c == "yellow":
            t_yellow += dt
            n_yellow += 1
            if worst != "red":
                worst = "yellow"
    return {
        "time_in_yellow_s": round(t_yellow, 3),
        "time_in_red_s": round(t_red, 3),
        "samples_yellow": n_yellow,
        "samples_red": n_red,
        "state": worst,
        "band": {
            "yellow_low": lim.yellow_low, "yellow_high": lim.yellow_high,
            "red_low": lim.red_low, "red_high": lim.red_high,
        },
    }


# ---------- core analysis ----------

def list_dumps(dump_dir: Path) -> list[dict[str, Any]]:
    """Return metadata about every dump file in ``dump_dir``.

    Thin wrapper over ``smo_common.dump_store.list_dumps`` so this tool and
    the MCS/simulator dump APIs describe the store identically.
    """
    return _store_list_dumps(dump_dir)


def _parse_ts(t: str | None) -> datetime | None:
    """Parse 'YYYY-MM-DDTHH:MM:SSZ' or '...+00:00'; None if unparseable."""
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


def select_meta(available: list[dict[str, Any]], *, files: list[str] | None = None,
                since: str | None = None, until: str | None = None,
                all_: bool = False, latest: bool = False) -> list[dict[str, Any]]:
    """Filter a listing (local or fetched from the API) down to a selection.

    Works purely on metadata dicts keyed by ``filename``/``timestamp``, so the
    same selection logic serves the local filesystem and a remote store.
    """
    if not available:
        return []

    if latest:
        return [available[-1]]

    if files:
        wanted = {f.strip() for f in files if f.strip()}
        return [d for d in available if d["filename"] in wanted]

    if all_ and not (since or until):
        return list(available)

    s, u = _parse_ts(since), _parse_ts(until)
    out: list[dict[str, Any]] = []
    for d in available:
        ts = _parse_ts(d.get("timestamp")) or _parse_ts(d.get("mtime"))
        if ts is None:
            continue
        if s and ts < s:
            continue
        if u and ts > u:
            continue
        out.append(d)
    return out


def select_dumps(dump_dir: Path, *, files: list[str] | None = None,
                 since: str | None = None, until: str | None = None,
                 all_: bool = False, latest: bool = False) -> list[Path]:
    """Resolve a request into a concrete list of local dump file paths."""
    chosen = select_meta(list_dumps(dump_dir), files=files, since=since,
                         until=until, all_=all_, latest=latest)
    return [Path(d["path"]) for d in chosen]


# ---------- remote (API) store client ----------

class RemoteDumpStore:
    """Read a dump store over the HTTP API instead of the local filesystem.

    Downloaded archives are cached in a temporary directory so the existing
    local decode path (:func:`analyse_dumps`) is reused unchanged — the only
    difference between local and API mode is where the bytes come from.
    """

    def __init__(self, api_base: str, cache_dir: Path | None = None):
        self.api_base = api_base.rstrip("/")
        if not self.api_base.endswith("/api/dumps"):
            self.api_base += "/api/dumps"
        self._cache = Path(cache_dir) if cache_dir else \
            Path(tempfile.mkdtemp(prefix="smo-dump-cache-"))
        self._cache.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> Path:
        return self._cache

    async def list(self) -> list[dict[str, Any]]:
        import aiohttp
        async with aiohttp.ClientSession() as s:
            async with s.get(self.api_base) as r:
                r.raise_for_status()
                body = await r.json()
        return body.get("dumps", [])

    async def fetch(self, filename: str) -> Path:
        """Download one dump into the cache (skipping an unchanged copy)."""
        import aiohttp
        # The server validates the name too; refuse locally as well so a bad
        # name never becomes a filesystem write outside the cache.
        if Path(filename).name != filename or not DUMP_NAME_RE.match(filename):
            raise ValueError(f"not a dump archive filename: {filename!r}")
        target = self._cache / filename
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.api_base}/file", params={"name": filename}) as r:
                r.raise_for_status()
                data = await r.read()
        target.write_bytes(data)
        return target

    async def select(self, **kwargs) -> list[Path]:
        chosen = select_meta(await self.list(), **kwargs)
        out: list[Path] = []
        for d in chosen:
            try:
                out.append(await self.fetch(d["filename"]))
            except Exception as e:  # noqa: BLE001 — one bad file must not kill the view
                logger.warning("Could not fetch %s from %s: %s",
                               d.get("filename"), self.api_base, e)
        return out


def analyse_dumps(paths: Iterable[Path], params_by_id: dict, hk_plist: dict,
                  limits_by_pid: dict[int, Any] | None = None) -> dict[str, Any]:
    """Decode one or more dump files into a JSON-friendly summary.

    ``limits_by_pid`` (G11): when given, every decoded series is run through
    the ground limits (mcs/limits.yaml) and each parameter carries
    time-in-yellow / time-in-red plus its band for plot shading; the result
    carries a per-dump ``limits`` summary line.
    """
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
    limits_by_pid = limits_by_pid or {}
    lim_yellow: list[str] = []      # parameters whose worst state is yellow
    lim_red: list[str] = []         # parameters whose worst state is red
    lim_checked = 0
    for sid, samples in sorted(an.hk_samples.items()):
        if not samples:
            continue
        # cuc is full-precision on-board seconds (float).
        per_pid: dict[int, list[tuple[float, float]]] = {}
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
            entry: dict[str, Any] = {
                "pid": pid,
                "name": meta.get("name", f"0x{pid:04X}"),
                "units": meta.get("units", ""),
                "min": mn, "max": mx, "mean": avg,
                "n": len(vals),
                "flag": flag,
                # Compact array of [cuc, value] for client-side sparkline
                "series": [[c, v] for c, v in pts],
                # G11: ground-limit evaluation (None = no band configured)
                "limit": None,
            }
            lim = limits_by_pid.get(pid)
            if lim is not None:
                verdict = evaluate_limits(pts, lim)
                entry["limit"] = verdict
                lim_checked += 1
                if verdict["state"] == "red":
                    lim_red.append(entry["name"])
                elif verdict["state"] == "yellow":
                    lim_yellow.append(entry["name"])
            params_out.append(entry)
        hk.append({
            "sid": sid,
            "packet_count": an.hk_counts[sid],
            "sample_count": len(samples),
            "params": params_out,
        })

    # G11: one honest per-dump summary line for the ground-limit sweep.
    if not limits_by_pid:
        lim_summary = "no ground limits loaded"
    elif lim_checked == 0:
        lim_summary = "no decoded parameter carries a ground limit"
    elif not lim_yellow and not lim_red:
        lim_summary = (f"all {lim_checked} limit-checked parameters "
                       f"within limits")
    else:
        lim_summary = (f"{len(lim_yellow)} parameter(s) exceeded yellow, "
                       f"{len(lim_red)} red")

    return {
        "files": files_used,
        "limits": {
            "checked": lim_checked,
            "yellow": lim_yellow,
            "red": lim_red,
            "summary": lim_summary,
        },
        "first_cuc": an.first_time,
        "last_cuc": an.last_time,
        "total_packets": sum(an.pkt_counts.values()),
        "packet_counts": pkt_counts,
        "events": events,
        "hk": hk,
        "anomalies": anomalies,
        # S12 on-board monitoring — the contents Franz could not see. Decoded
        # by `PassAnalysis` (tm_report), which now has the `svc == 12` branch
        # it never had. S12 packets are routed to the Alarm store (store 4),
        # so an alarm dump is mostly this.
        "s12_transitions": an.s12_transitions,
        "s12_definitions": an.s12_definitions,
    }


# ---------- HTTP handlers ----------

def make_app(dump_dir: Path | None, api_base: str | None = None) -> web.Application:
    """Build the viewer app.

    Exactly one source is used: the local ``dump_dir`` (default), or the
    remote store at ``api_base`` when given.
    """
    params_by_id, _, hk_plist = load_catalogs()
    app = web.Application()
    remote = RemoteDumpStore(api_base) if api_base else None
    app["dump_dir"] = dump_dir
    app["remote"] = remote
    app["params_by_id"] = params_by_id
    app["hk_plist"] = hk_plist
    # G11: ground limits applied to every decoded series.
    app["limits_by_pid"] = load_limits_by_pid(REPO / "configs" / "eosat1")

    async def index(_req: web.Request) -> web.Response:
        return web.Response(text=INDEX_HTML, content_type="text/html")

    async def api_dumps(_req: web.Request) -> web.Response:
        if remote is not None:
            try:
                dumps = await remote.list()
            except Exception as e:  # noqa: BLE001 — surface, don't 500
                return web.json_response(
                    {"dump_dir": remote.api_base, "dumps": [],
                     "error": f"cannot reach dump API: {e}"})
            return web.json_response({"dump_dir": remote.api_base, "dumps": dumps})
        return web.json_response({"dump_dir": str(dump_dir),
                                  "dumps": list_dumps(dump_dir)})

    async def api_decode(req: web.Request) -> web.Response:
        q = req.query
        files = q.get("files", "")
        file_list = files.split(",") if files else None
        selection = dict(
            files=file_list,
            since=q.get("since"),
            until=q.get("until"),
            all_=q.get("all") in ("1", "true", "yes"),
            latest=q.get("latest") in ("1", "true", "yes"),
        )
        if remote is not None:
            try:
                paths = await remote.select(**selection)
            except Exception as e:  # noqa: BLE001
                return web.json_response(
                    {"error": f"cannot reach dump API: {e}",
                     "files": [], "events": [], "hk": [],
                     "packet_counts": [], "anomalies": [],
                     "total_packets": 0, "first_cuc": None, "last_cuc": None})
        else:
            paths = select_dumps(dump_dir, **selection)
        if not paths:
            return web.json_response({"error": "no dump files matched the request",
                                      "files": [], "events": [], "hk": [],
                                      "packet_counts": [], "anomalies": [],
                                      "total_packets": 0,
                                      "first_cuc": None, "last_cuc": None})
        result = analyse_dumps(paths, app["params_by_id"], app["hk_plist"],
                               limits_by_pid=app["limits_by_pid"])
        return web.json_response(result)

    app.router.add_get("/", index)
    app.router.add_get("/api/dumps", api_dumps)
    app.router.add_get("/api/decode", api_decode)

    # Report generator (Phase F): same library and routes the MCS mounts, so
    # the standalone viewer offers /reports and /api/reports/* in local mode.
    if dump_dir is not None:
        try:
            from smo_common.reporting import register_report_routes
            register_report_routes(app, dump_dir,
                                   REPO / "configs" / "eosat1",
                                   owner="delayed_tm_viewer")
        except Exception as e:  # noqa: BLE001 — reports are optional here
            logger.warning("Report API not mounted: %s", e)

    # Serve Chart.js from the repo's vendor tree rather than a CDN, so the
    # "fully offline" promise in the module docstring is actually true. The
    # MCS static tree is preferred; the top-level vendor/ is the fallback.
    for candidate in (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "vendor",
                      REPO / "vendor"):
        if (candidate / "js" / "chart.umd.min.js").is_file():
            app.router.add_static("/vendor", str(candidate))
            break
    else:  # pragma: no cover - only on a broken checkout
        logger.warning("Chart.js not found in the vendor tree; plots will not render")
    return app


# ---------- HTML page (served inline so the tool is single-file) ----------

INDEX_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Delayed TM Viewer</title>
<!-- Vendored locally (see the /vendor static route) so the tool really is
     offline, as the module docstring promises. -->
<script src="/vendor/js/chart.umd.min.js"></script>
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
/* G11: per-parameter ground-limit badges */
.lim-badge { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-right: 3px; white-space: nowrap; }
.lim-badge.yellow { color: var(--warn); border: 1px solid var(--warn); }
.lim-badge.red { color: var(--bad); border: 1px solid var(--bad); }
.lim-ok { color: var(--good); font-size: 10px; }
.lim-none { color: #556b85; font-size: 10px; }
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

  <div class="section"><h2>On-board monitoring (S12)</h2>
    <div class="body" id="s12">No data.</div></div>

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
let lastStore = "";
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
    lastStore = j.dump_dir || "";
    renderDumpsList();
    setStatus(j.error ? ("ERROR: " + j.error)
                      : `${availableDumps.length} dump file(s) in ${j.dump_dir}`);
  } catch (e) {
    setStatus("ERROR: " + e);
  }
}

function renderDumpsList() {
  const root = $("dumpsList");
  if (!availableDumps.length) {
    root.textContent = "No dumps in " + (lastStore || "the store") +
      ". Run a pass with an S15 dump in the simulator.";
    return;
  }
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
    // G11: the per-dump ground-limit summary line
    ["Ground limits", j.limits ? j.limits.summary : "—"],
    ["On-board time (CUC) range",
     `${j.first_cuc == null ? "—" : j.first_cuc.toFixed(3) + " (" + formatCucTime(j.first_cuc) + ")"}`
     + " → "
     + `${j.last_cuc == null ? "—" : j.last_cuc.toFixed(3) + " (" + formatCucTime(j.last_cuc) + ")"}`],
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
      + j.events.map(e => `<tr class='sev-${e.severity}'><td>${formatCucTime(e.cuc)}</td><td>S5.${e.subtype}</td><td>0x${e.event_id.toString(16).padStart(4,"0").toUpperCase()}</td><td>${escapeHtml(e.description)}</td></tr>`).join("")
      + "</tbody></table>"
    : "No events.";

  // S12 on-board monitoring — the decoded contents of an alarm dump.
  // Before this the viewer had no `svc == 12` branch at all, so four limit
  // violations showed up as one row in the packet-count table and nothing
  // anywhere said which parameter, which way it went, or against what band.
  const tr = j.s12_transitions || [];
  const defs = j.s12_definitions || [];
  const sevColour = {INFO:"#888", WARNING:"#f59e0b", ALARM:"#ff6b6b", HIGH:"#ff6b6b"};
  let s12html = "";
  if (tr.length) {
    const entered = tr.filter(t => t.entered).length;
    s12html += `<div style="color:var(--dim);margin-bottom:6px;">${tr.length} check transition(s): `
      + `${entered} out-of-limits, ${tr.length - entered} return(s) to nominal.</div>`
      + "<table><thead><tr><th>CUC</th><th>Mon</th><th>Parameter</th><th>Transition</th>"
      + "<th class='num'>Value</th><th>Check</th><th>Severity</th><th class='num'>Limit band</th>"
      + "</tr></thead><tbody>"
      + tr.map(t => `<tr>
          <td>${t.cuc == null ? "—" : formatCucTime(t.cuc)}</td>
          <td class='num'>${t.mon_id == null ? "—" : t.mon_id}</td>
          <td>${escapeHtml(t.param_hex || "")} ${escapeHtml(t.param_name || "")}</td>
          <td>${t.entered ? "<b style='color:#ff6b6b'>OUT OF LIMITS</b>"
                          : "<span style='color:#3fca8a'>back to nominal</span>"}</td>
          <td class='num'>${fmtNum(t.value)} ${escapeHtml(t.units || "")}</td>
          <td>${escapeHtml(t.check_type_name || "—")}</td>
          <td style="color:${sevColour[t.severity_name] || "inherit"}">${escapeHtml(t.severity_name || "—")}</td>
          <td class='num'>${escapeHtml(t.band || "not reported")}</td></tr>`).join("")
      + "</tbody></table>";
  }
  if (defs.length) {
    s12html += "<h3 style='font-size:11px;color:var(--accent);margin:10px 0 4px;'>"
      + "Monitoring definitions (TM 12,13)</h3>"
      + "<table><thead><tr><th>Mon</th><th>Parameter</th><th>Check</th><th>State</th>"
      + "<th class='num'>Low</th><th class='num'>High</th></tr></thead><tbody>"
      + defs.map(d => `<tr><td class='num'>${d.mon_id}</td>
          <td>${escapeHtml(d.param_hex || "")} ${escapeHtml(d.param_name || "")}</td>
          <td>${escapeHtml(d.check_type_name || "—")}</td>
          <td>${d.enabled ? "enabled" : "disabled"}</td>
          <td class='num'>${fmtNum(d.low_limit)}</td>
          <td class='num'>${fmtNum(d.high_limit)}</td></tr>`).join("")
      + "</tbody></table>";
  }
  $("s12").innerHTML = s12html || "No S12 monitoring packets in this dump.";

  // HK
  $("hk").innerHTML = j.hk.length
    ? j.hk.map(sid => `
        <h3 style="font-size:11px;color:var(--accent);margin:8px 0 4px;">SID ${sid.sid} — ${sid.packet_count} pkts, ${sid.sample_count} samples
        <button class="sm" onclick="plotAllForSid(${sid.sid}, ${JSON.stringify(sid.params).replace(/"/g, '&quot;')})">Plot All</button>
        </h3>
        <table><thead><tr><th>ID</th><th>Name</th><th>Units</th><th class='num'>Min</th><th class='num'>Max</th><th class='num'>Mean</th><th class='num'>N</th><th>Limits</th><th>Trend</th><th></th></tr></thead><tbody>
        ${sid.params.map(p => `<tr>
          <td>0x${p.pid.toString(16).padStart(4,"0").toUpperCase()}</td>
          <td>${escapeHtml(p.name)}</td>
          <td>${escapeHtml(p.units)}</td>
          <td class='num'>${fmtNum(p.min)}</td>
          <td class='num'>${fmtNum(p.max)}</td>
          <td class='num'>${fmtNum(p.mean)}</td>
          <td class='num'>${p.n}</td>
          <td>${limitCell(p)}</td>
          <td>${spark(p.series)}</td>
          <td><button class="sm" onclick="addChart(${sid.sid}, ${p.pid}, ${JSON.stringify(p.name).replace(/"/g, '&quot;')}, ${JSON.stringify(p.series)}, ${JSON.stringify(p.units).replace(/"/g, '&quot;')}, ${JSON.stringify(p.limit).replace(/"/g, '&quot;')})">Plot</button> ${p.flag ? "<span class='flag'>Δ</span>" : ""}</td>
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

/* G11: per-parameter time-in-yellow / time-in-red badges */
function fmtDurS(s) {
  if (s >= 60) return (s / 60).toFixed(1) + " min";
  return s.toFixed(0) + " s";
}
function limitCell(p) {
  const l = p.limit;
  if (!l) return "<span class='lim-none'>no limit</span>";
  if (l.state === "ok") return "<span class='lim-ok'>in band</span>";
  let out = "";
  if (l.time_in_red_s > 0) out += `<span class='lim-badge red'>RED ${fmtDurS(l.time_in_red_s)}</span>`;
  if (l.time_in_yellow_s > 0) out += `<span class='lim-badge yellow'>YEL ${fmtDurS(l.time_in_yellow_s)}</span>`;
  return out || "<span class='lim-ok'>in band</span>";
}

/* G11: limit-band shading on plotted series (yellow wash outside the yellow
   band, red wash outside the red band, dashed threshold lines). */
const limitBandPlugin = {
  id: "limitBands",
  beforeDatasetsDraw(chart, _args, opts) {
    const b = opts && opts.band;
    if (!b) return;
    const area = chart.chartArea, y = chart.scales.y;
    if (!area || !y) return;
    const px = v => Math.max(area.top, Math.min(area.bottom, y.getPixelForValue(v)));
    const ctx = chart.ctx;
    ctx.save();
    const zone = (fromPx, toPx, color) => {
      const top = Math.min(fromPx, toPx), h = Math.abs(toPx - fromPx);
      if (h > 0.5) { ctx.fillStyle = color; ctx.fillRect(area.left, top, area.right - area.left, h); }
    };
    const YEL = "rgba(245,158,11,0.08)", RED = "rgba(248,113,113,0.10)";
    if (b.yellow_high != null) zone(area.top, px(b.yellow_high), YEL);
    if (b.yellow_low != null) zone(px(b.yellow_low), area.bottom, YEL);
    if (b.red_high != null) zone(area.top, px(b.red_high), RED);
    if (b.red_low != null) zone(px(b.red_low), area.bottom, RED);
    ctx.setLineDash([4, 3]); ctx.lineWidth = 1;
    for (const [k, c] of [["yellow_low", "#f59e0b"], ["yellow_high", "#f59e0b"],
                          ["red_low", "#f87171"], ["red_high", "#f87171"]]) {
      const v = b[k];
      if (v == null) continue;
      const yv = y.getPixelForValue(v);
      if (yv < area.top || yv > area.bottom) continue;
      ctx.strokeStyle = c;
      ctx.beginPath(); ctx.moveTo(area.left, yv); ctx.lineTo(area.right, yv); ctx.stroke();
    }
    ctx.restore();
  }
};

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

// On-board CUC, in SECONDS since the mission epoch J2000 =
// 2000-01-01T12:00:00Z (smo_common.protocol.ecss_packet, epoch now live from
// mission.yaml `time_epoch`). Since the CUC migration the 9-octet PUS
// secondary header carries coarse (uint32 s) + fine (uint16, 1/65536 s), and
// the backend hands us the COMBINED value as a float — coarse + fine/65536 —
// so these numbers legitimately have a fractional part. (The old comment
// "It is NOT coarse/fine packed" described the pre-migration wire and is no
// longer true; the `cuc / 256` and magic 657720000 offsets were always wrong.)
const CUC_EPOCH_UNIX_MS = Date.UTC(2000, 0, 1, 12, 0, 0);   // 946728000000

function formatCucTime(cuc) {
  if (typeof cuc !== 'number' || !isFinite(cuc)) return String(cuc);
  // hh:mm:ss.mmm — the fine field resolves ~15.26 us, so show sub-seconds
  // rather than truncating them away.
  return new Date(CUC_EPOCH_UNIX_MS + cuc * 1000).toISOString().substr(11, 12);
}

function addChart(sid, pid, name, series, units, limit) {
  if (!series || series.length < 1) return;
  const chartId = `chart-${sid}-${pid}`;
  const chartsBody = $("chartsBody");
  $("chartsTabs").style.display = "block";

  let chartItem = document.getElementById(chartId);
  if (chartItem) {
    chartItem.scrollIntoView({ behavior: 'smooth' });
    return;
  }

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
        },
        // G11: limit-band shading from mcs/limits.yaml (null = no band)
        limitBands: { band: limit && limit.band ? limit.band : null }
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
    // NOTE: chartjs-plugin-datalabels is not loaded by this page and no
    // datalabels options are configured. Referencing the undeclared global
    // `ChartDataLabels` threw a ReferenceError while building the options
    // object, killing every chart before it was constructed.
    plugins: [limitBandPlugin].concat(
      (typeof ChartDataLabels !== 'undefined') ? [ChartDataLabels] : [])
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
    // NOTE: chartjs-plugin-datalabels is not loaded by this page and no
    // datalabels options are configured. Referencing the undeclared global
    // `ChartDataLabels` threw a ReferenceError while building the options
    // object, killing every chart before it was constructed.
    plugins: (typeof ChartDataLabels !== 'undefined') ? [ChartDataLabels] : []
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
    import os
    parser = argparse.ArgumentParser(description="Delayed TM viewer")
    parser.add_argument("--port", type=int, default=8092)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dumps", default=None,
                        help="Directory containing dump_*.bin files "
                             "(default: $SMO_DUMP_DIR, else mission dump_dir, "
                             "else workspace/dumps)")
    parser.add_argument("--api", default=os.environ.get("SMO_DUMP_API"),
                        help="Read the store over HTTP from a service that "
                             "serves it (e.g. http://server:9090) instead of "
                             "the local filesystem. Also honoured via "
                             "SMO_DUMP_API.")
    parser.add_argument("--public", action="store_true",
                        help="Bind 0.0.0.0 (also honoured via SMO_PUBLIC=1)")
    # ── one-shot report mode (Phase F) ──
    parser.add_argument("--report", metavar="TEMPLATE", default=None,
                        help="Generate a report from a template id (e.g. "
                             "pass_report, daily_ops_review, "
                             "anomaly_investigation) or a YAML path, then "
                             "exit — no server is started. Uses the same "
                             "library as the MCS /reports page.")
    parser.add_argument("--period-start", default=None,
                        help="With --report: absolute period start "
                             "(ISO-8601 UTC, CUC-based selection)")
    parser.add_argument("--period-end", default=None,
                        help="With --report: absolute period end")
    parser.add_argument("--var", action="append", default=[],
                        metavar="KEY=VALUE",
                        help="With --report: template vars "
                             "(e.g. --var t0=2026-03-10T04:18:22Z)")
    parser.add_argument("-o", "--out", default=None,
                        help="With --report: output HTML path "
                             "(default workspace/reports/<auto>)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    host = "0.0.0.0" if (args.public or os.environ.get("SMO_PUBLIC") == "1") else args.host

    if args.report:
        # One-shot report generation over the local store, no server.
        from smo_common.reporting import generate_report
        from smo_common.reporting.spec import SpecError
        mission_cfg = None
        try:
            from smo_common.config.loader import load_mission_config
            mission_cfg = load_mission_config(REPO / "configs" / "eosat1")
        except Exception:  # noqa: BLE001
            pass
        dump_dir = resolve_dump_dir(mission_cfg, explicit=args.dumps).resolve()
        period = None
        if args.period_start and args.period_end:
            period = {"kind": "absolute", "start": args.period_start,
                      "end": args.period_end}
        vars_ = {}
        for kv in args.var:
            if "=" in kv:
                k, v = kv.split("=", 1)
                vars_[k.strip()] = v.strip()
        try:
            meta = generate_report(
                args.report, config_dir=REPO / "configs" / "eosat1",
                dump_dir=dump_dir, vars=vars_, period_override=period,
                out_path=args.out)
        except SpecError as e:
            for err in e.errors:
                print(f"spec error — {err['path']}: {err['msg']}",
                      file=sys.stderr)
            return 2
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print(f"{meta['title']}: {meta['packets']} packets over "
              f"{meta['period']['start_utc']} → {meta['period']['end_utc']}"
              f" -> {meta['path']}")
        return 0

    if args.api:
        app = make_app(None, api_base=args.api)
        logger.info("Delayed TM viewer listening on http://%s:%d (API store=%s)",
                    host, args.port, args.api)
    else:
        # Shared resolution order: --dumps > SMO_DUMP_DIR > mission dump_dir >
        # workspace/dumps. Resolved to an absolute path here because the viewer
        # may be started from a different working directory than the simulator.
        mission_cfg = None
        try:
            from smo_common.config.loader import load_mission_config
            mission_cfg = load_mission_config(REPO / "configs" / "eosat1")
        except Exception as e:  # noqa: BLE001 — config is optional for this tool
            logger.debug("No mission config for dump_dir resolution: %s", e)
        dump_dir = resolve_dump_dir(mission_cfg, explicit=args.dumps).resolve()
        dump_dir.mkdir(parents=True, exist_ok=True)
        app = make_app(dump_dir)
        logger.info("Delayed TM viewer listening on http://%s:%d (dumps=%s)",
                    host, args.port, dump_dir)

    web.run_app(app, host=host, port=args.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
