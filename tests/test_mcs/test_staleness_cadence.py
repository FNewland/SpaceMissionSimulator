"""Staleness by cadence — Franz's TCS-graph report (2026-08b).

    "The timeouts for the TCS graphs is set to 60s but TCS telemetry is only
     coming in every 60s so we often get stale data displays rather than
     thermal graphs. Can the stale data flag be set on the cadence of the
     telemetry times 1.5 or something?"

The console used to carry three hard-coded numbers — `TM_FRESH_S = 5`,
`TM_STALE_S = 15` and `_STALE_DEFAULT_S = 8.0` — and apply them to every
parameter regardless of how often it is actually downlinked. HK SID 3 (TCS)
has `interval_s: 60.0`, so the thermal charts were BLANKED (buffers emptied)
for ~45 s of every 60 and could never accumulate a trace at all.

These tests pin the three halves of the fix:

* the server-side cadence join (`param_id -> fastest interval`), published on
  `/api/client-config` and `/api/param-catalog` — the browser previously had
  no way to know which parameter rides in which structure;
* the four-state client verdict, including the NOT-ENABLED state that replaced
  a silent lie (a disabled SID's parameters are absent from the MCS cache, so
  the chart fell through to the global link age and reported FRESH while
  plotting nothing);
* the duplicate-sample gate: one append per real sample, not one per UI tick.
"""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"
STALENESS_YAML = CONFIG_DIR / "mcs" / "staleness.yaml"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")


def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(INDEX), str(snippet_file)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


@pytest.fixture
async def client() -> TestClient:
    server = _make_server()
    server._running = False
    app = web.Application()
    app.router.add_get("/api/client-config", server._handle_client_config)
    app.router.add_get("/api/param-catalog", server._handle_param_catalog)
    cli = TestClient(TestServer(app))
    await cli.start_server()
    yield cli
    await cli.close()


def client_config_prelude() -> str:
    """JS that installs the real server payload, exactly as the browser does.

    Parameter-level tests must run against the real cadence join, not against
    the page's pre-fetch fallbacks — otherwise a parameter would be judged
    aperiodic and every assertion would pass for the wrong reason.
    """
    server = _make_server()
    payload = {
        "staleness": dict(server._staleness_policy),
        "param_cadence": {f"0x{pid:04X}": [ent["interval_s"], *ent["sids"]]
                          for pid, ent in server._param_cadence.items()},
        "subsystem_cadence": server._subsystem_cadence,
        "sid_intervals": {str(sid): meta.get("interval_s")
                          for sid, meta in server._hk_sid_meta.items()},
    }
    return f"applyStalenessConfig({json.dumps(payload)});\n"


def _config_join() -> tuple[dict[int, float], dict[str, float]]:
    """The cadence join computed straight from the YAML, for cross-checking."""
    hk = yaml.safe_load((CONFIG_DIR / "telemetry" / "hk_structures.yaml").read_text())
    params = yaml.safe_load((CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())

    def _toi(v):
        return int(v) if isinstance(v, int) else int(str(v), 0)

    subsystem_of = {_toi(p["id"]): str(p.get("subsystem", ""))
                    for p in params["parameters"]}
    per_param: dict[int, float] = {}
    per_subsystem: dict[str, float] = {}
    for s in hk["structures"]:
        interval = float(s["interval_s"])
        for pp in s.get("parameters", []) or []:
            pid = _toi(pp["param_id"])
            per_param[pid] = min(per_param.get(pid, interval), interval)
            sub = subsystem_of.get(pid, "")
            if sub:
                per_subsystem[sub] = min(per_subsystem.get(sub, interval), interval)
    return per_param, per_subsystem


# ── the cadence join is published, server-side ──────────────────────

class TestCadenceJoinIsPublished:
    """Nothing used to say which parameters ride in which HK structure.

    `/api/hk-structures` published `interval_s` per SID and
    `/api/param-catalog` published `in_hk` / `on_demand`; the join between
    them — the one fact needed to derive a per-parameter threshold — existed
    nowhere the browser could reach.
    """

    async def test_client_config_carries_the_policy_from_configuration(self, client):
        body = await (await client.get("/api/client-config")).json()
        pol = body["staleness"]
        cfg = yaml.safe_load(STALENESS_YAML.read_text())["staleness"]
        for key, value in cfg.items():
            assert pol[key] == pytest.approx(float(value)), (
                f"{key} must be served from mcs/staleness.yaml, not a literal")
        # Franz asked for "the cadence x 1.5".
        assert pol["stale_factor"] == pytest.approx(1.5)

    async def test_param_cadence_matches_the_hk_configuration(self, client):
        body = await (await client.get("/api/client-config")).json()
        served = {int(k, 16): v[0] for k, v in body["param_cadence"].items()}
        expected, _ = _config_join()
        assert served == expected

    async def test_tcs_parameters_are_published_as_a_60_second_cadence(self, client):
        body = await (await client.get("/api/client-config")).json()
        # 0x0406 tcs.temp_obc — the parameter behind the TCS temperature chart.
        assert body["param_cadence"]["0x0406"][0] == pytest.approx(60.0)
        assert body["param_cadence"]["0x0406"][1:] == [3]
        assert body["subsystem_cadence"]["tcs"]["interval_s"] == pytest.approx(60.0)

    async def test_param_catalog_carries_the_join_too(self, client):
        body = await (await client.get("/api/param-catalog")).json()
        by_hex = {p["hex"]: p for p in body["parameters"]}
        assert by_hex["0x0406"]["hk_interval_s"] == pytest.approx(60.0)
        assert by_hex["0x0406"]["hk_sids"] == [3]
        # An on-demand-only parameter is aperiodic: no HK structure carries it,
        # so it must NOT be judged against any interval.
        assert by_hex["0x0B1B"]["hk_interval_s"] is None
        assert by_hex["0x0B1B"]["hk_sids"] == []

    async def test_prop_sid_9_is_in_the_join(self, client):
        """PROP is SID 9 at 8 s; its display is new in this batch."""
        body = await (await client.get("/api/client-config")).json()
        assert body["subsystem_cadence"]["prop"] == {"interval_s": 8.0, "sids": [9]}
        assert body["sid_intervals"]["9"] == pytest.approx(8.0)


# ── the client fallbacks must not drift from the configuration ──────

@requires_node
class TestClientFallbacksMatchConfig:
    """index.html ships fallbacks for use before /api/client-config returns.

    A fallback that has drifted from the configuration is worse than none, so
    it is pinned here rather than trusted.
    """

    def test_fallback_subsystem_cadence_matches_the_yaml(self, tmp_path):
        out = run_js("return {sub: SUBSYS_CADENCE, sid: SID_INTERVALS, "
                     "pol: STALENESS};", tmp_path)
        _, expected_sub = _config_join()
        served = {k: v["interval_s"] for k, v in out["sub"].items()}
        assert served == expected_sub
        hk = yaml.safe_load((CONFIG_DIR / "telemetry" / "hk_structures.yaml").read_text())
        expected_sids = {str(int(s["sid"])): float(s["interval_s"])
                         for s in hk["structures"]}
        assert out["sid"] == expected_sids
        cfg = yaml.safe_load(STALENESS_YAML.read_text())["staleness"]
        for key, value in cfg.items():
            assert out["pol"][key] == pytest.approx(float(value))


# ── the four-state verdict ──────────────────────────────────────────

@requires_node
class TestCadenceDerivedFreshness:

    def test_tcs_is_not_stale_at_55_seconds(self, tmp_path):
        """SID 3 sends every 60 s: at 55 s the sample is merely nearly due.

        Under the old rule this was `stale`, the chart was blanked and its
        buffers were emptied — which is why a thermal trend could never form.
        """
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      subsystem_staleness: { tcs: 55.0 } };
            return { v: tmFreshness(state, 'tcs'),
                     d: tmFreshnessDetail(state, 'tcs') };
        """, tmp_path)
        assert out["v"] == "degraded"
        assert out["d"]["interval_s"] == 60.0

    def test_tcs_is_stale_at_95_seconds(self, tmp_path):
        """1.5 cycles is 90 s — beyond that a sample has certainly been missed."""
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      subsystem_staleness: { tcs: 95.0 } };
            return tmFreshness(state, 'tcs');
        """, tmp_path)
        assert out == "stale"

    def test_tcs_is_fresh_through_most_of_its_cycle(self, tmp_path):
        out = run_js("""
            const r = {};
            [0, 10, 30, 45, 53].forEach(a => {
              state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                        subsystem_staleness: { tcs: a } };
              r[a] = tmFreshness(state, 'tcs');
            });
            return r;
        """, tmp_path)
        assert set(out.values()) == {"fresh"}, out

    def test_eight_second_structures_do_not_wear_a_permanent_lag_chip(self, tmp_path):
        """TTC / PAYLOAD / OBDH / PROP are 8 s SIDs.

        Every age they can legitimately reach between packets must read fresh,
        or the amber LAG chip is permanent and therefore meaningless.
        """
        out = run_js("""
            const r = {};
            ['ttc','payload','obdh','prop'].forEach(sub => {
              r[sub] = [0, 4, 8, 9].map(a => {
                const ss = {}; ss[sub] = a;
                state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                          subsystem_staleness: ss };
                return tmFreshness(state, sub);
              });
            });
            return r;
        """, tmp_path)
        for sub, verdicts in out.items():
            assert set(verdicts) == {"fresh"}, f"{sub}: {verdicts}"

    def test_beacon_cadence_is_honoured_for_a_beacon_only_phase(self, tmp_path):
        """SID 11 is 30 s and is the ONLY telemetry in bootloader phases.

        Under the old 15 s rule those parameters read STALE for 22 s in every
        30 during the one phase where they are all the operator has.
        """
        out = run_js("""
            const r = {};
            [5, 20, 26, 40, 60].forEach(a => {
              r[a] = ageVerdict(a, SID_INTERVALS[11]);
            });
            return r;
        """, tmp_path)
        assert out["5"] == "fresh" and out["20"] == "fresh" and out["26"] == "fresh"
        assert out["40"] == "degraded"     # 27–45 s
        assert out["60"] == "stale"        # > 30 x 1.5

    def test_on_demand_parameters_do_not_go_stale_eight_seconds_after_a_fetch(self, tmp_path):
        """0x0B1B belongs to no HK structure: it is fetched with TC(20,3).

        The old flat 8 s default made every on-demand value STALE moments
        after it arrived, forever.
        """
        out = run_js("""
            """ + client_config_prelude() + """
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      params_age: { '2843': 60.0 } };
            return { v: paramFreshness(0x0B1B), stale: isParamStale(0x0B1B) };
        """, tmp_path)
        assert out["v"] == "fresh"
        assert out["stale"] is False

    def test_tcs_cells_are_not_permanently_badged_stale(self, tmp_path):
        out = run_js("""
            """ + client_config_prelude() + """
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      params_age: { '1030': 40.0 } };      // 0x0406, 40 s old
            return { v: paramFreshness(0x0406), stale: isParamStale(0x0406),
                     cell: paramCell(0x0406, '12.3 C') };
        """, tmp_path)
        assert out["v"] == "fresh"
        assert out["stale"] is False
        assert "STALE" not in out["cell"]


# ── the fourth state: NOT ENABLED ───────────────────────────────────

@requires_node
class TestDisabledSidState:
    """A disabled SID is not a stale link, and the remedy is different.

    Today a disabled SID's parameters never enter the MCS cache, so
    `subsystem_staleness` has no key for the subsystem, the gate fell back to
    the global link age and the chart reported FRESH while plotting nothing —
    and `isParamStale` returned false, so the values rendered as current.
    """

    def test_prop_sid_9_not_enabled_is_reported_as_such(self, tmp_path):
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      hk_sid_age_s: { '1': 0.4, '11': 3.0 },
                      subsystem_staleness: { eps: 0.4 } };
            const d = tmFreshnessDetail(state, 'prop');
            return { v: d.verdict, reason: d.reason, dead: d.dead_sids,
                     label: staleChartLabel(d.verdict, state, d) };
        """, tmp_path)
        assert out["v"] == "disabled"
        assert out["reason"] == "sid_not_enabled"
        assert out["dead"] == [9]
        assert "SID 9" in out["label"]
        assert "TC(3,5)" in out["label"]
        assert "STALE" not in out["label"]

    def test_an_explicitly_disabled_sid_counts_as_disabled(self, tmp_path):
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      hk_sid_age_s: { '3': 200.0 }, hk_enabled: { '3': false },
                      subsystem_staleness: { tcs: 200.0 } };
            return tmFreshness(state, 'tcs');
        """, tmp_path)
        assert out == "disabled"

    def test_a_disabled_sid_blanks_the_chart_with_the_command_to_send(self, tmp_path):
        out = run_js("""
            ensureChart('prop-chart-tank', [{label:'P', borderColor:'#0f0'}], 'bar');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      hk_sid_age_s: { '1': 0.4 } };
            for (let i = 0; i < 5; i++) pushChartData('prop-chart-tank', i, [20]);
            return { points: chartBuffers['prop-chart-tank'][0].length };
        """, tmp_path)
        assert out["points"] == 0

    def test_a_disabled_sid_does_not_masquerade_as_fresh_on_a_cell(self, tmp_path):
        """0x0B01 (prop tank pressure) with SID 9 off and no value ever seen."""
        out = run_js("""
            """ + client_config_prelude() + """
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      hk_sid_age_s: { '1': 0.4 }, params_age: {} };
            const d = paramFreshnessDetail(0x0B01);
            return { v: d.verdict, dead: d.dead_sids,
                     cell: paramCell(0x0B01, '--') };
        """, tmp_path)
        assert out["v"] == "disabled"
        assert out["dead"] == [9]
        assert "SID 9 OFF" in out["cell"]

    def test_unknown_sid_observation_is_not_reported_as_disabled(self, tmp_path):
        """An unknown is not a diagnosis: no hk_sid_age_s means no verdict."""
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true };
            return tmFreshness(state, 'prop');
        """, tmp_path)
        assert out == "fresh"


# ── the duplicate-sample gate ───────────────────────────────────────

@requires_node
class TestOneAppendPerRealSample:
    """`pushChartData` appended one point per 1 Hz UI tick regardless of
    whether new telemetry had arrived, so a 60 s structure contributed sixty
    identical samples per real sample and the 120-point window showed two
    minutes of duplicates instead of two hours of history."""

    def test_a_sixty_second_structure_appends_once_per_sample(self, tmp_path):
        out = run_js("""
            ensureChart('tcs-chart-temps', [{label:'OBC', borderColor:'#0f0'}], 'C');
            let appended = 0;
            // Two real TCS samples, 60 UI ticks apart.
            for (let cycle = 0; cycle < 2; cycle++) {
              for (let a = 0; a < 50; a++) {
                state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                          subsystem_staleness: { tcs: a } };
                pushChartData('tcs-chart-temps', cycle * 100 + a, [20 + cycle]);
              }
            }
            return { points: chartBuffers['tcs-chart-temps'][0].length,
                     ys: chartBuffers['tcs-chart-temps'][0].map(p => p.y) };
        """, tmp_path)
        assert out["points"] == 2, out
        assert out["ys"] == [20, 21]

    def test_a_one_hertz_structure_still_plots_every_second(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            for (let i = 0; i < 10; i++) {
              state = { last_frame_age_s: 0.5, stale: false, in_contact: true,
                        subsystem_staleness: { eps: 0.2 + (i % 2) * 0.1 } };
              pushChartData('eps-chart-soc', i, [50 + i]);
            }
            return chartBuffers['eps-chart-soc'][0].length;
        """, tmp_path)
        # Ages alternate 0.2 / 0.3: five genuine resets out of ten ticks.
        assert out == 5

    def test_charts_without_a_per_subsystem_age_are_ungated(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true };
            for (let i = 0; i < 4; i++) pushChartData('eps-chart-soc', i, [50]);
            return chartBuffers['eps-chart-soc'][0].length;
        """, tmp_path)
        assert out == 4
