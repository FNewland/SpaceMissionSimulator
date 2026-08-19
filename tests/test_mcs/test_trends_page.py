"""The TRENDS tab and the situational-awareness strip, executed for real.

`index.html` is a single page with ~10 kLOC of inline JS and no module
boundary, so the only way to test its behaviour without a browser is the node
harness (`tests/test_mcs/js/mcs_harness.js`), which evaluates the inline
script inside a `vm` context backed by a small DOM stub.

What is covered here is the logic that would otherwise only be checked by
eye: the trend chart's dataset construction (series, limit-band overlay,
normalisation, time-formatted axis), the eclipse/contact shading input, the
CSV builder, and every branch of the SA strip.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")


def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run(
        [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# A seeded archive answer, shaped like /api/archive/parameters + param-names.
SEED_JS = """
trendNames = ['eps.soc_pct', 'eps.bus_voltage_V', 'orbit.eclipse', 'orbit.contact'];
trendBands = { 'eps.soc_pct': {yellow_low:25, yellow_high:95, red_low:15,
                               red_high:100, units:'%', param_id:'0x0101'} };
const _sel = document.getElementById('trend-params');
_sel.selectedOptions = [{value:'eps.soc_pct'}];
trendRenderParamList();
const T0 = 1700000000;
trendSeries = {
  'eps.soc_pct':  [{utc_epoch:T0,value:80},{utc_epoch:T0+10,value:60},{utc_epoch:T0+20,value:12}],
  'eps.bus_voltage_V': [{utc_epoch:T0,value:28.1},{utc_epoch:T0+10,value:27.4}],
  'orbit.eclipse':[{utc_epoch:T0,value:0},{utc_epoch:T0+10,value:1},{utc_epoch:T0+20,value:1}],
  'orbit.contact':[{utc_epoch:T0,value:1},{utc_epoch:T0+10,value:0},{utc_epoch:T0+20,value:0}],
};
trendMeta = { start: T0, end: T0 + 30 };
"""


@requires_node
def test_trend_chart_plots_the_selected_series(tmp_path):
    out = run_js(SEED_JS + """
document.getElementById('trend-bands').checked = false;
document.getElementById('trend-normalise').checked = false;
trendDraw();
return {
  labels: trendChart.data.datasets.map(d => d.label),
  data: trendChart.data.datasets[0].data,
  xTick: trendChart.cfg.options.scales.x.ticks.callback(1700000000),
};
""", tmp_path)
    assert out["labels"] == ["eps.soc_pct (%)"]
    assert out["data"] == [{"x": 1700000000, "y": 80},
                           {"x": 1700000010, "y": 60},
                           {"x": 1700000020, "y": 12}]
    # The x axis is epoch seconds rendered as UTC — no date adapter is
    # vendored, so a plain time formatter is the only correct option.
    assert out["xTick"].endswith("Z") and ":" in out["xTick"]


@requires_node
def test_limit_bands_overlay_the_trend(tmp_path):
    """Ground limits on the trend are the point of WP-4 + O-9 together."""
    out = run_js(SEED_JS + """
document.getElementById('trend-bands').checked = true;
document.getElementById('trend-normalise').checked = false;
trendDraw();
const bands = trendChart.data.datasets.slice(1);
return { labels: bands.map(d => d.label), ys: bands.map(d => d.data[0].y),
         dashed: bands.every(d => Array.isArray(d.borderDash)) };
""", tmp_path)
    assert out["ys"] == [15, 100, 25, 95]
    assert out["dashed"] is True
    assert out["labels"] == ["red low 15", "red high 100",
                             "yellow low 25", "yellow high 95"]


@requires_node
def test_limit_bands_are_suppressed_when_they_would_lie(tmp_path):
    """Two parameters on one axis, or a normalised axis, must not draw bands."""
    out = run_js(SEED_JS + """
document.getElementById('trend-bands').checked = true;
document.getElementById('trend-normalise').checked = true;
trendDraw();
const normalised = trendChart.data.datasets.length;
document.getElementById('trend-normalise').checked = false;
document.getElementById('trend-params').selectedOptions =
  [{value:'eps.soc_pct'}, {value:'eps.bus_voltage_V'}];
trendDraw();
return { normalised, twoParams: trendChart.data.datasets.length };
""", tmp_path)
    assert out["normalised"] == 1, "bands drawn on a normalised axis"
    assert out["twoParams"] == 2, "bands drawn with two parameters on one axis"


@requires_node
def test_normalisation_maps_each_series_to_0_100(tmp_path):
    out = run_js(SEED_JS + """
document.getElementById('trend-normalise').checked = true;
trendDraw();
return trendChart.data.datasets[0].data.map(p => Math.round(p.y));
""", tmp_path)
    assert out == [100, 71, 0]


@requires_node
def test_csv_export_carries_every_selected_series(tmp_path):
    out = run_js(SEED_JS + """
document.getElementById('trend-params').selectedOptions =
  [{value:'eps.soc_pct'}, {value:'eps.bus_voltage_V'}];
let captured = null;
Blob = function(parts) { captured = parts[0]; };
URL = { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} };
trendExportCsv();
return captured.split('\\n');
""", tmp_path)
    assert out[0] == "utc_iso,utc_epoch,eps.soc_pct,eps.bus_voltage_V"
    assert len(out) == 4                       # header + 3 timestamps
    assert out[1].endswith(",80,28.1")
    # The third sample exists for SoC only; the missing cell must be empty,
    # never interpolated.
    assert out[3].endswith(",12,")


@requires_node
def test_shading_runs_are_derived_from_the_archived_context_series(tmp_path):
    """The eclipse/contact shading plugin reads orbit.eclipse / orbit.contact."""
    out = run_js(SEED_JS + """
document.getElementById('trend-shading').checked = true;
const rects = [];
const chart = {
  ctx: { save(){}, restore(){}, fillRect(x,y,w,h){ rects.push([x,w]); }, fillStyle:'' },
  chartArea: { left: 0, right: 100, top: 0, bottom: 50 },
  scales: { x: { getPixelForValue: v => (v - 1700000000) } },
};
trendShadePlugin.beforeDatasetsDraw(chart);
return rects;
""", tmp_path)
    # eclipse runs 10..20 and contact runs 0..10 — one shaded box each.
    assert [10, 10] in out          # eclipse from x=10, width 10
    assert [0, 10] in out           # contact from x=0, width 10


@requires_node
def test_sa_strip_renders_every_field(tmp_path):
    out = run_js("""
state.sa = { link:'live', last_frame_age_s: 1.2, unacked_alarms: 0,
             tm_tier_name:'HIGH', tm_tier_cmd_name:'HIGH', tm_tier_demoted:false,
             store_fill_pct: 40.0, in_eclipse:false };
state.speed = 1;
updateSaBar();
const g = id => { const e = document.getElementById(id); return [e.textContent, e.className]; };
return { live:g('sa-live'), tier:g('sa-tier'), store:g('sa-store'),
         un:g('sa-unacked'), speed:g('sa-speed'), ecl:g('sa-eclipse') };
""", tmp_path)
    assert out["live"] == ["LIVE", "sa-val ok"]
    assert out["tier"] == ["HIGH", "sa-val ok"]
    assert out["store"] == ["40.0%", "sa-val ok"]
    assert out["un"] == ["0", "sa-val ok"]
    assert out["speed"] == ["x1.0", "sa-val"]
    assert out["ecl"] == ["SUNLIT", "sa-val ok"]


@requires_node
def test_sa_strip_shows_a_demoted_tier_and_a_stale_link(tmp_path):
    """Active vs commanded tier is the whole point of showing both."""
    out = run_js("""
state.sa = { link:'stale', last_frame_age_s: 240, unacked_alarms: 4,
             tm_tier_name:'BEACON', tm_tier_cmd_name:'HIGH', tm_tier_demoted:true,
             tm_tier_demotion_reason:'antenna stowed',
             store_fill_pct: 96.0, in_eclipse:true };
updateSaBar();
const g = id => { const e = document.getElementById(id); return [e.textContent, e.className]; };
return { live:g('sa-live'), tier:g('sa-tier'), store:g('sa-store'),
         un:g('sa-unacked'), ecl:g('sa-eclipse'),
         tip: document.getElementById('sa-tier-wrap').title };
""", tmp_path)
    assert out["live"] == ["STALE 240s", "sa-val bad"]
    assert out["tier"][0] == "BEACON ← cmd HIGH"
    assert out["tier"][1] == "sa-val warn"
    assert "antenna stowed" in out["tip"]
    assert out["store"][1] == "sa-val bad"
    assert out["un"] == ["4", "sa-val bad"]
    assert out["ecl"][0] == "ECLIPSE"


@requires_node
def test_sa_strip_says_no_data_rather_than_zero(tmp_path):
    """A console with no telemetry must not read as a healthy spacecraft."""
    out = run_js("""
state.sa = { link:'none', last_frame_age_s: null, unacked_alarms: 0 };
updateSaBar();
const g = id => document.getElementById(id).textContent;
return { live:g('sa-live'), tier:g('sa-tier'), store:g('sa-store'), ecl:g('sa-eclipse') };
""", tmp_path)
    assert out["live"] == "NO DATA"
    assert out["tier"] == "NO DATA"
    assert out["store"] == "NO DATA"
    assert out["ecl"] == "--"


@requires_node
def test_pass_countdown_uses_the_contact_schedule_endpoint(tmp_path):
    out = run_js("""
const now = Date.now() / 1000;
saContact = { in_contact:false, ground_station:'Svalbard',
              aos_time: now + 125, los_time: now + 725 };
renderSaPass();
const before = [document.getElementById('sa-pass-lbl').textContent,
                document.getElementById('sa-pass').textContent];
saContact = { in_contact:true, ground_station:'Troll',
              aos_time: now - 60, los_time: now + 65 };
renderSaPass();
const during = [document.getElementById('sa-pass-lbl').textContent,
                document.getElementById('sa-pass').textContent];
saContact = { in_contact:false, ground_station:'', aos_time:null, los_time:null };
renderSaPass();
return { before, during, none: document.getElementById('sa-pass').textContent };
""", tmp_path)
    assert out["before"][0] == "NEXT AOS"
    assert out["before"][1].startswith("02:0") and "Svalbard" in out["before"][1]
    assert out["during"][0] == "LOS IN"
    assert "Troll" in out["during"][1]
    assert out["none"] == "NO PASS SCHEDULED"


@requires_node
def test_alarm_row_links_to_its_contingency_procedure(tmp_path):
    out = run_js("""
alarmJournal = [{
  id: 1, timestamp: Date.now()/1000, severity: 4, subsystem: 'eps',
  parameter: '0x0101', name: 'eps.bat_soc', value: '9.0', units: '%',
  limit: 'RED_LOW < 15.0', source: 'LIMIT', acknowledged: false,
  procedure: { id: 'CTG-001', name: 'Under-Voltage Load Shed' },
}];
renderAlarmJournal();
return document.getElementById('alarm-journal-body').innerHTML;
""", tmp_path)
    assert "openProcedureFromAlarm('CTG-001')" in out
    assert "Under-Voltage Load Shed" in out
    assert "alarm-proc-btn" in out
    assert "fresh" in out, "a new unacknowledged alarm carries no visual cue"


# ── The empty trend page must name its own cause ──────────────────────────
#
# Owner report: "the trends tab does not work at all". Reproduced against a
# running MCS: every one of the tab's failure modes — a corrupt archive, a
# server-side query error, an unreachable `/api/archive/param-names`, and a
# window that genuinely holds no rows — rendered the SAME thing, an empty
# chart under the string "NO ARCHIVED DATA IN THIS WINDOW" (or, for the
# parameter list, a silently empty selector reading "0 of 0 archived").
#
# The four are operationally different: one is a broken ground segment, one
# is a broken request, one is a quiet spacecraft and one is a file whose
# rows are on the wrong clock. These gates keep them distinguishable.

_STUB = """
adoptServerClock({ ground_utc: new Date(1773100000000).toISOString(), speed: 1 });
function stubArchive(fn) { fetch = fn; }
"""


def _run_async(body: str, tmp_path: Path):
    return run_js(_STUB + "return (async () => {\n" + body + "\n})();", tmp_path)


@requires_node
def test_an_unavailable_archive_is_not_reported_as_an_empty_window(tmp_path):
    """A corrupt archive said 'NO ARCHIVED DATA IN THIS WINDOW'."""
    out = _run_async("""
stubArchive(async () => ({ ok: true, json: async () => ({
  names: [], bands: {}, data: [], archive_unavailable: true,
  archive: { available: false, status: 'corrupt',
             message: 'TELEMETRY ARCHIVE CORRUPT — the archive file is damaged' } }) }));
await trendLoadNames();
document.getElementById('trend-params').selectedOptions = [{value:'eps.soc_pct'}];
await trendLoad();
return { status: document.getElementById('trend-status').textContent,
         colour: document.getElementById('trend-status').style.color,
         banner: document.getElementById('archive-banner').textContent };
""", tmp_path)
    assert out["status"].startswith("ARCHIVE UNAVAILABLE")
    assert "damaged" in out["status"]
    assert "NO ARCHIVED DATA" not in out["status"]
    assert "alarm" in out["colour"]
    assert "TELEMETRY ARCHIVE CORRUPT" in out["banner"]


@requires_node
def test_a_failed_query_is_not_reported_as_an_empty_window(tmp_path):
    """`fetch` does not reject on HTTP 500 — the old code read it as [] rows."""
    out = _run_async("""
stubArchive(async (url) => url.indexOf('param-names') >= 0
  ? { ok: true, json: async () => ({ names: ['eps.soc_pct'], bands: {},
      archive: { available: true, status: 'ok', time_base: 'sim' } }) }
  : { ok: false, status: 500, json: async () => ({ error: 'query_parameters blew up' }) });
await trendLoadNames();
document.getElementById('trend-params').selectedOptions = [{value:'eps.soc_pct'}];
await trendLoad();
return { status: document.getElementById('trend-status').textContent,
         colour: document.getElementById('trend-status').style.color };
""", tmp_path)
    assert out["status"].startswith("ARCHIVE QUERY FAILED")
    assert "500" in out["status"] and "query_parameters blew up" in out["status"]
    assert "NO ARCHIVED DATA" not in out["status"]
    assert "alarm" in out["colour"]


@requires_node
def test_an_unreachable_param_name_endpoint_says_so(tmp_path):
    """The selector was silently empty: the literal 'does not work at all'."""
    out = _run_async("""
stubArchive(async () => { throw new Error('Failed to fetch'); });
await trendLoadNames();
return { count: document.getElementById('trend-param-count').textContent,
         status: document.getElementById('trend-status').textContent };
""", tmp_path)
    assert "No parameter list" in out["count"]
    assert "Failed to fetch" in out["count"]
    assert "0 of 0 archived" not in out["count"]
    assert out["status"].startswith("PARAMETER LIST UNAVAILABLE")


@requires_node
def test_a_genuinely_empty_window_names_the_window_it_queried(tmp_path):
    """'No data' is only actionable if the operator can see which window."""
    out = _run_async("""
stubArchive(async () => ({ ok: true, json: async () => ({
  names: ['eps.soc_pct'], bands: {}, data: [], archive_unavailable: false,
  archive: { available: true, status: 'ok', time_base: 'sim',
             message: 'Telemetry archive online' } }) }));
await trendLoadNames();
document.getElementById('trend-params').selectedOptions = [{value:'eps.soc_pct'}];
await trendLoad();
return document.getElementById('trend-status').textContent;
""", tmp_path)
    assert out.startswith("Archive queried OK")
    assert "no samples in" in out
    assert "sim time" in out          # names the clock the window is on
    assert "2026-03-09" in out        # and the window itself


@requires_node
def test_a_legacy_wall_clock_archive_explains_why_it_can_never_match(tmp_path):
    """A pre-migration file cannot intersect a sim-time window, ever.

    Widening the window does not help and the operator must not be left
    trying. The archive's own `time_base` is the evidence.
    """
    out = _run_async("""
stubArchive(async () => ({ ok: true, json: async () => ({
  names: ['eps.soc_pct'], bands: {}, data: [], archive_unavailable: false,
  archive: { available: true, status: 'legacy', time_base: 'wall',
             message: 'LEGACY ARCHIVE (schema v1)' } }) }));
await trendLoadNames();
document.getElementById('trend-params').selectedOptions = [{value:'eps.soc_pct'}];
await trendLoad();
return { status: document.getElementById('trend-status').textContent,
         colour: document.getElementById('trend-status').style.color };
""", tmp_path)
    assert "LEGACY ARCHIVE" in out["status"]
    assert "WALL CLOCK" in out["status"]
    assert "warning" in out["colour"]


@requires_node
def test_the_trend_window_is_sim_time_not_browser_wall_clock(tmp_path):
    """Archive rows carry sim-time epochs (schema v2).

    The MCS sim epoch is months away from the host clock, so a window built
    from `Date.now()` intersects nothing. The window must come from the
    server-anchored clock (`simNowSec`), and the request must carry it.
    """
    out = _run_async("""
const urls = [];
// Sim time is 2026-03-10; the browser's own clock is irrelevant.
adoptServerClock({ ground_utc: '2026-03-10T00:00:00Z', speed: 1 });
stubArchive(async (url) => { urls.push(url); return { ok: true, json: async () => ({
  names: ['eps.soc_pct'], bands: {}, data: [],
  archive: { available: true, status: 'ok', time_base: 'sim' } }) }; });
await trendLoadNames();
document.getElementById('trend-window').value = '3600';
document.getElementById('trend-params').selectedOptions = [{value:'eps.soc_pct'}];
await trendLoad();
// trendMeta is the window the request was actually built from.
return { urls, win: trendMeta, wall: Date.now() / 1000 };
""", tmp_path)
    sim_epoch = 1773100800.0                       # 2026-03-10T00:00:00Z
    assert abs(out["win"]["end"] - sim_epoch) < 30, out["win"]
    assert abs(out["win"]["end"] - out["win"]["start"] - 3600) < 1
    # And it is nowhere near the browser's wall clock, which is the bug class.
    assert abs(out["win"]["end"] - out["wall"]) > 3600
    q = [u for u in out["urls"] if "eps.soc_pct" in u]
    assert q, out["urls"]
    assert f"start={out['win']['start']}" in q[0]
    assert f"end={out['win']['end']}" in q[0]
