"""Phase G lane 1 — ground limit checking, the alarm system, command history,
the trend/history API and the advanced-display endpoints.

Regression gates for the four things brief 08 found built-and-unreachable:

* **O-9 / WP-3** `configs/eosat1/mcs/limits.yaml` declares 51 limit bands and
  had zero production consumers. The gate here is data-driven off the shipped
  YAML: *every* band must produce a journal entry when a synthetic HK packet
  violates it, so a band added later cannot be silently inert.
* **O-12 / finding 4-6** `TMArchive.store_command`, `.update_command_state`,
  `.acknowledge_alarm` and `.prune` had zero callers — command history was a
  permanently empty schema and no acknowledgement survived a restart.
* **Operator gap 1 / WP-4** the four `/api/archive/*` endpoints had no client.
* **O-13** the `/api/displays/*` set was headless.
"""
from __future__ import annotations

import asyncio
import struct
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.loader import load_hk_structures, load_limits
from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer
from smo_mcs.tm_archive import TMArchive

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"


# ── fixtures / helpers ──────────────────────────────────────────────────

def _limits():
    return load_limits(CONFIG_DIR)


def _hk_by_param() -> dict[int, object]:
    out = {}
    for hk in load_hk_structures(CONFIG_DIR):
        for p in hk.parameters:
            out.setdefault(p.param_id, hk)
    return out


HK_BY_PARAM = _hk_by_param()
LIMITS = _limits()


def _make_server(tmp_path: Path | None = None) -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        srv = MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                        connect_port=9999, http_port=0)
    # Never let a test write into the shipped config tree.
    srv._archive.close()
    if tmp_path is not None:
        srv._archive = TMArchive(tmp_path / "tm_archive.db")
        srv._archive.open()
    else:
        srv._archive = MagicMock()
        srv._archive.store_alarm.return_value = 1
    return srv


def _pkt(service, subtype, data, apid=1, seq=0):
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _hk_payload(hk, overrides: dict[int, float]) -> bytes:
    """S3.25 payload for *hk* with engineering values applied."""
    out = struct.pack(">H", hk.sid)
    for p in hk.parameters:
        eng = overrides.get(p.param_id, 0.0)
        if p.pack_format in ("f", "d"):
            out += struct.pack(">" + p.pack_format, float(eng))
            continue
        raw = int(round(eng * p.scale))
        size = struct.calcsize(">" + p.pack_format)
        signed = p.pack_format.islower()
        bits = size * 8
        lo = -(1 << (bits - 1)) if signed else 0
        hi = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
        out += struct.pack(">" + p.pack_format, max(lo, min(hi, raw)))
    return out


def _ws(srv):
    ws = MagicMock()
    ws.send_str = AsyncMock()
    srv._ws_clients = [(ws, None)]
    return ws


def _violating_value(lim) -> float:
    """A value unambiguously outside *lim* on a representable side."""
    if lim.red_high is not None:
        return lim.red_high + max(1.0, abs(lim.red_high) * 0.5)
    return (lim.red_low if lim.red_low is not None else 0.0) - 1.0


# ── 1. G1-a: EVERY band in limits.yaml reaches the alarm journal ────────

@pytest.mark.parametrize("lim", LIMITS, ids=lambda l: f"0x{l.param_id:04X}")
def test_every_shipped_band_reaches_the_alarm_journal(lim):
    """The regression gate for O-9.

    Before Phase G, `TMProcessor` was constructed without `limits=`, so
    `_check_limit` returned immediately for every parameter and `pop_alarms()`
    had no callers. This parametrises over the shipped YAML rather than a
    hand-written list so that adding a band without wiring it fails here.
    """
    hk = HK_BY_PARAM.get(lim.param_id)
    assert hk is not None, (
        f"0x{lim.param_id:04X} carries a limit band but is in no HK SID — "
        "the ground check can never see it")
    srv = _make_server()
    _ws(srv)
    payload = _hk_payload(hk, {lim.param_id: _violating_value(lim)})
    asyncio.run(srv._process_tm(_pkt(3, 25, payload)))

    tag = f"0x{lim.param_id:04X}"
    raised = [a for a in srv._alarm_journal
              if a.get("source") == "LIMIT" and a["parameter"] == tag]
    assert raised, (
        f"{tag} violated its band and produced no ground alarm; journal held "
        f"{[a['parameter'] for a in srv._alarm_journal]}")
    assert raised[0]["severity"] >= 3
    assert raised[0]["band"], "the alarm must carry the band it broke"


def test_limit_state_is_published_for_the_displays():
    srv = _make_server()
    _ws(srv)
    lim = next(l for l in LIMITS if l.param_id == 0x0101)      # eps.bat_soc
    hk = HK_BY_PARAM[lim.param_id]
    asyncio.run(srv._process_tm(
        _pkt(3, 25, _hk_payload(hk, {lim.param_id: 2.0}))))
    statuses = srv._tm_processor.limit_statuses()
    assert statuses[0x0101].startswith("red")


def test_ground_only_parameters_are_the_ones_with_no_s12_definition():
    """30 of the 51 bands are the ONLY limit check those parameters get.

    Pinned so that deleting an S12 definition (or a band) makes the coverage
    change visible instead of silent.
    """
    s12 = yaml.safe_load(
        (CONFIG_DIR / "monitoring" / "s12_definitions.yaml").read_text())
    s12_ids = {int(d["param_id"]) if isinstance(d["param_id"], int)
               else int(str(d["param_id"]), 0)
               for d in s12["s12_definitions"]}
    ground_only = sorted(l.param_id for l in LIMITS if l.param_id not in s12_ids)
    assert len(ground_only) == 30, (
        "ground-only monitoring set changed: "
        f"{[hex(p) for p in ground_only]}")
    # Spot-check the classes brief 08 named explicitly.
    for pid in (0x0118, 0x011F, 0x0273, 0x0275, 0x0539, 0x053A):
        assert pid in ground_only


# ── 2. G1-b: acknowledgement persists across a restart ──────────────────

def test_acknowledgement_persists_across_a_restart(tmp_path):
    srv = _make_server(tmp_path)
    _ws(srv)
    alarm = asyncio.run(srv._raise_alarm({
        "severity": 4, "subsystem": "eps", "parameter": "0x0101",
        "value": "2.0", "source": "LIMIT",
    }))
    db_id = alarm["db_id"]
    assert db_id > 0
    assert srv.unacknowledged_alarm_count() == 1
    srv._persist_alarm_ack(alarm)
    srv._archive.close()

    # "Restart": a brand-new archive object on the same file.
    reopened = TMArchive(tmp_path / "tm_archive.db")
    reopened.open()
    rows = reopened.query_alarms()
    reopened.close()
    assert rows, "the alarm was not archived at all"
    assert all(r["acknowledged"] for r in rows if r["id"] == db_id), \
        "acknowledgement did not survive the restart"


def test_ack_endpoint_writes_through_to_the_archive(tmp_path):
    srv = _make_server(tmp_path)
    _ws(srv)
    alarm = asyncio.run(srv._raise_alarm({
        "severity": 3, "subsystem": "ttc", "parameter": "0x050C",
        "value": "-3.0", "source": "LIMIT",
    }))
    req = MagicMock()
    req.match_info = {"alarm_id": str(alarm["id"])}
    resp = asyncio.run(srv._handle_alarm_ack(req))
    assert resp.status == 200
    rows = srv._archive.query_alarms()
    assert rows and rows[-1]["acknowledged"]
    assert srv.unacknowledged_alarm_count() == 0


def test_alarms_link_to_a_contingency_procedure():
    """Alarm → procedure linkage, driven by mcs/alarm_procedures.yaml."""
    srv = _make_server()
    _ws(srv)
    lim = next(l for l in LIMITS if l.param_id == 0x0101)
    hk = HK_BY_PARAM[lim.param_id]
    asyncio.run(srv._process_tm(
        _pkt(3, 25, _hk_payload(hk, {lim.param_id: 2.0}))))
    alarm = next(a for a in srv._alarm_journal if a.get("source") == "LIMIT")
    assert alarm["procedure"]["id"] == "CTG-001"
    assert alarm["procedure"]["name"], "procedure id did not resolve to a name"


def test_every_configured_procedure_id_exists_in_the_index():
    srv = _make_server()
    index_ids = set(srv._procedure_meta)
    assert index_ids, "procedure_index.yaml did not load"
    configured = (set(srv._alarm_proc_by_param.values())
                  | set(srv._alarm_proc_by_event.values())
                  | set(srv._alarm_proc_by_subsystem.values()))
    assert configured, "alarm_procedures.yaml did not load"
    assert not (configured - index_ids), (
        f"alarm_procedures.yaml names unknown procedures: "
        f"{sorted(configured - index_ids)}")


def test_every_limited_parameter_has_a_procedure_link():
    """A ground alarm with no procedure is an alarm list entry, not an alarm."""
    srv = _make_server()
    missing = [f"0x{l.param_id:04X}" for l in LIMITS
               if l.param_id not in srv._alarm_proc_by_param]
    assert not missing, f"limited parameters with no linked procedure: {missing}"


# ── 3. G1-c: command history is archived and transitions on S1 ──────────

def _s1_payload(subtype: int, tc_seq: int, error_code: int = 0) -> bytes:
    if subtype == 4:
        return struct.pack(">HH", tc_seq, error_code)
    return struct.pack(">IH", tc_seq, error_code)


def test_sent_command_is_archived_and_transitions_on_verification(tmp_path):
    srv = _make_server(tmp_path)
    _ws(srv)
    srv._tc_writer = MagicMock()
    srv._tc_writer.is_closing.return_value = False
    srv._tc_writer.drain = AsyncMock()

    req = MagicMock()
    req.json = AsyncMock(return_value={
        "service": 8, "subtype": 1, "data_hex": "10 01",
        "name": "EPS_LINE_ON", "position": "flight_director",
    })
    with patch.object(srv, "_check_position_access", return_value=(True, "")):
        resp = asyncio.run(srv._handle_pus_command(req))
    assert resp.status == 200
    seq = srv._tc_manager._seq_count

    rows = srv._archive.query_commands()
    assert rows, "store_command was not called from the TC build path"
    row = next(r for r in rows if r["seq"] == seq)
    assert row["state"] == "SENT"
    assert row["name"] == "EPS_LINE_ON"
    assert row["service"] == 8 and row["subtype"] == 1
    assert row["data_hex"] == "1001"
    assert row["position"] == "flight_director"

    # S1.1 acceptance, then S1.7 completion.
    asyncio.run(srv._process_tm(_pkt(1, 1, _s1_payload(1, seq))))
    assert next(r for r in srv._archive.query_commands()
                if r["seq"] == seq)["state"] == "ACCEPTED"
    asyncio.run(srv._process_tm(_pkt(1, 7, _s1_payload(7, seq))))
    assert next(r for r in srv._archive.query_commands()
                if r["seq"] == seq)["state"] == "COMPLETED"


def test_rejected_command_is_archived_as_rejected(tmp_path):
    srv = _make_server(tmp_path)
    _ws(srv)
    srv._archive.store_command(seq=42, name="TEST", service=8, subtype=1)
    asyncio.run(srv._process_tm(_pkt(1, 2, _s1_payload(2, 42, 0x0007))))
    row = next(r for r in srv._archive.query_commands() if r["seq"] == 42)
    assert row["state"] == "REJECTED"


def test_archive_is_pruned_on_start_and_on_a_timer():
    srv = _make_server()          # MagicMock archive
    srv._archive.prune.reset_mock()
    srv._archive_last_prune = time.time() - srv._archive_prune_interval_s - 1
    srv._maybe_prune_archive()
    srv._archive.prune.assert_called_once()
    # Immediately again — must NOT prune, that is the point of the timer.
    srv._archive.prune.reset_mock()
    srv._maybe_prune_archive()
    srv._archive.prune.assert_not_called()


def test_prune_runs_at_construction():
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.TMArchive") as archive_cls:
        archive_cls.return_value = MagicMock()
        srv = MCSServer(config_dir=str(CONFIG_DIR), http_port=0)
    srv._archive.open.assert_called_once()
    srv._archive.prune.assert_called_once()


def test_shutdown_closes_the_archive():
    srv = _make_server()
    srv.shutdown()
    srv._archive.close.assert_called_once()


# ── 4. G1-d: the trend endpoints answer for a seeded archive ────────────

def _seed(archive: TMArchive, t0: float) -> None:
    for i in range(30):
        archive.store_parameters(
            {"eps.soc_pct": 80.0 - i, "eps.bus_voltage_V": 28.0,
             "orbit.eclipse": 1.0 if i > 15 else 0.0,
             "orbit.contact": 1.0 if i < 5 else 0.0},
            utc_epoch=t0 + i)
    archive.store_event(0x0102, 4, "eps", "Battery SoC below 10%", utc_epoch=t0 + 20)
    archive.store_alarm({"timestamp": t0 + 21, "severity": 4, "subsystem": "eps",
                         "parameter": "0x0101", "value": "9.0", "source": "LIMIT"})
    archive.store_command(seq=7, name="EPS_LINE_ON", service=8, subtype=1,
                          data_hex="1001", position="eps_tcs",
                          utc_epoch=t0 + 22)


async def _client(srv, routes) -> TestClient:
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def test_trend_endpoints_return_the_seeded_series(tmp_path):
    srv = _make_server(tmp_path)
    t0 = time.time() - 300
    _seed(srv._archive, t0)

    async def run():
        client = await _client(srv, [
            ("GET", "/api/archive/parameters", srv._handle_archive_params),
            ("GET", "/api/archive/param-names", srv._handle_archive_param_names),
            ("GET", "/api/archive/events", srv._handle_archive_events),
            ("GET", "/api/archive/alarms", srv._handle_archive_alarms),
            ("GET", "/api/archive/playback", srv._handle_archive_playback),
            ("GET", "/api/archive/commands", srv._handle_archive_commands),
            ("POST", "/api/displays/alarm-trends", srv._handle_alarm_trends),
        ])
        try:
            r = await client.get("/api/archive/parameters",
                                 params={"name": "eps.soc_pct",
                                         "start": t0 - 1, "end": t0 + 100})
            series = (await r.json())["data"]
            assert len(series) == 30
            assert series[0]["value"] == pytest.approx(80.0)
            assert series[-1]["value"] == pytest.approx(51.0)
            assert series[0]["utc_epoch"] < series[-1]["utc_epoch"]

            r = await client.get("/api/archive/param-names")
            names = await r.json()
            assert "eps.soc_pct" in names["names"]
            assert "orbit.eclipse" in names["names"], \
                "eclipse shading series is not archived"
            assert "orbit.contact" in names["names"]
            # The limit-band overlay needs the band on the archive's own key.
            assert names["bands"]["eps.soc_pct"]["yellow_low"] == pytest.approx(25.0)
            assert names["bands"]["eps.soc_pct"]["param_id"] == "0x0101"

            r = await client.get("/api/archive/events",
                                 params={"start": t0 - 1, "end": t0 + 100})
            assert (await r.json())["events"][0]["event_id"] == 0x0102

            r = await client.get("/api/archive/alarms",
                                 params={"start": t0 - 1, "end": t0 + 100})
            assert (await r.json())["alarms"][0]["parameter"] == "0x0101"

            r = await client.get("/api/archive/playback",
                                 params={"subsystem": "eps",
                                         "start": t0 - 1, "end": t0 + 100})
            pb = (await r.json())["data"]
            assert pb and "soc_pct" in pb[0]

            r = await client.get("/api/archive/commands")
            cmds = (await r.json())["commands"]
            assert cmds[0]["name"] == "EPS_LINE_ON"

            r = await client.post("/api/displays/alarm-trends",
                                  json={"start": t0 - 1, "end": t0 + 100,
                                        "bucket_s": 60})
            trend = await r.json()
            assert trend["count"] == 1
            assert trend["by_subsystem"]["eps"] == 1
            assert trend["by_source"]["LIMIT"] == 1
            assert sum(b["total"] for b in trend["buckets"]) == 1
        finally:
            await client.close()

    asyncio.run(run())


def test_archive_snapshot_records_the_shading_series(tmp_path):
    srv = _make_server(tmp_path)
    srv._archive_state_snapshot({
        "eps": {"soc_pct": 70.0, "flag": True},
        "in_eclipse": True, "in_contact": False, "alt_km": 512.3,
    })
    names = set(srv._archive.parameter_names())
    assert {"eps.soc_pct", "orbit.eclipse", "orbit.contact",
            "orbit.alt_km"} <= names
    assert "eps.flag" not in names, "booleans must not be archived as parameters"
    ecl = srv._archive.query_parameters("orbit.eclipse")
    assert ecl[-1]["value"] == 1.0


# ── 5. G1-e/G1-f: the display endpoints answer and are reachable ────────

def test_system_overview_reports_unknown_without_telemetry():
    srv = _make_server()
    srv._latest_state = {}

    async def run():
        client = await _client(srv, [
            ("GET", "/api/displays/system-overview", srv._handle_system_overview)])
        try:
            data = await (await client.get("/api/displays/system-overview")).json()
            assert all(p["value"] is None for p in data["key_parameters"]), \
                "the board fabricated plausible values with no telemetry"
            assert all(p["status"] == "unknown" for p in data["key_parameters"])
            assert data["link"]["stale"] is True
        finally:
            await client.close()

    asyncio.run(run())


def test_system_overview_health_follows_the_ground_limits():
    srv = _make_server()
    _ws(srv)
    lim = next(l for l in LIMITS if l.param_id == 0x0101)
    hk = HK_BY_PARAM[lim.param_id]
    asyncio.run(srv._process_tm(
        _pkt(3, 25, _hk_payload(hk, {lim.param_id: 2.0}))))
    srv._latest_state = {"eps": {"soc_pct": 2.0}}

    async def run():
        client = await _client(srv, [
            ("GET", "/api/displays/system-overview", srv._handle_system_overview)])
        try:
            data = await (await client.get("/api/displays/system-overview")).json()
            eps = next(h for h in data["subsystem_health"] if h["name"] == "EPS")
            assert eps["status"] == "red", \
                "subsystem health ignored the ground limit check"
            assert data["unacknowledged_alarms"] >= 1
        finally:
            await client.close()

    asyncio.run(run())


def test_power_budget_says_no_data_instead_of_inventing_one():
    srv = _make_server()
    srv._latest_state = {}

    async def run():
        client = await _client(srv, [
            ("GET", "/api/displays/power-budget", srv._handle_power_budget)])
        try:
            d = await (await client.get("/api/displays/power-budget")).json()
            assert d["has_data"] is False
            assert d["per_subsystem_power"] == {}
        finally:
            await client.close()

    asyncio.run(run())


def test_power_budget_uses_the_real_per_subsystem_breakdown():
    srv = _make_server()
    srv._latest_state = {"eps": {
        "power_gen_W": 60.0, "power_cons_W": 45.0, "soc_pct": 88.0,
        "subsys_power_eps": 4.0, "subsys_power_aocs": 9.0,
        "subsys_power_tcs": 12.0, "subsys_power_ttc": 8.0,
        "subsys_power_payload": 7.0, "subsys_power_obdh": 5.0,
    }}

    async def run():
        client = await _client(srv, [
            ("GET", "/api/displays/power-budget", srv._handle_power_budget)])
        try:
            d = await (await client.get("/api/displays/power-budget")).json()
            assert d["has_data"] is True
            assert d["power_margin_w"] == pytest.approx(15.0)
            assert d["per_subsystem_power"]["tcs"] == pytest.approx(12.0)
            assert d["total_subsystem_power"] == pytest.approx(45.0)
        finally:
            await client.close()

    asyncio.run(run())


def test_contact_schedule_drops_completed_passes_and_is_json_safe():
    srv = _make_server()
    now = time.time()
    srv._contact_scheduler.update_passes([
        {"aos_time": now - 3600, "los_time": now - 3000,
         "max_elevation": 20.0, "ground_station": "Past"},
        {"aos_time": now + 600, "los_time": now + 1200,
         "max_elevation": 40.0, "ground_station": "Svalbard"},
    ])
    passes = srv._contact_scheduler.get_next_passes(10, current_time=now)
    assert [p["ground_station"] for p in passes] == ["Svalbard"]
    empty = srv._contact_scheduler.get_current_contact_status(current_time=now)
    srv._contact_scheduler.update_passes([])
    none_status = srv._contact_scheduler.get_current_contact_status(current_time=now)
    assert none_status["time_to_aos"] is None, \
        "float('inf') is not valid JSON and broke the whole response"
    assert empty["ground_station"] == "Svalbard"


# ── 6. G1-f: the situational-awareness block ────────────────────────────

def test_sa_block_carries_the_header_fields():
    srv = _make_server()
    srv._storage_status = [{"id": 1, "name": "HK", "fill_pct": 91.5,
                            "overflow": False}]
    data = {"ttc": {"tm_tier": 1, "tm_tier_name": "LOW", "tm_tier_cmd": 2,
                    "tm_tier_cmd_name": "HIGH", "tm_tier_demoted": True,
                    "tm_tier_demotion_reason": "antenna stowed"},
            "obdh": {}, "in_contact": True, "ground_station_name": "Svalbard",
            "in_eclipse": False}
    sa = srv._build_sa_block(data, age_s=2.0, stale=False)
    assert sa["link"] == "live"
    assert sa["tm_tier_name"] == "LOW" and sa["tm_tier_cmd_name"] == "HIGH"
    assert sa["tm_tier_demoted"] is True
    assert sa["store_fill_pct"] == pytest.approx(91.5)
    assert sa["store_fill_source"] == "S15.14"
    assert sa["ground_station_name"] == "Svalbard"

    assert srv._build_sa_block(data, age_s=9.0, stale=False)["link"] == "degraded"
    assert srv._build_sa_block(data, age_s=90.0, stale=True)["link"] == "stale"
    assert srv._build_sa_block(data, age_s=None, stale=True)["link"] == "none"


def test_sa_store_fill_falls_back_to_the_obdh_buffers():
    srv = _make_server()
    srv._storage_status = []
    sa = srv._build_sa_block(
        {"obdh": {"hktm_buf_fill": 12.0, "event_buf_fill": 44.0,
                  "alarm_buf_fill": 3.0}}, age_s=1.0, stale=False)
    assert sa["store_fill_pct"] == pytest.approx(44.0)
    assert "SID 4" in sa["store_fill_source"]


# ── 7. G1-g + reachability: what the console actually calls ─────────────

MCS_ROUTES_THE_UI_MUST_CALL = [
    "/api/limits",
    "/api/archive/parameters",
    "/api/archive/param-names",
    "/api/archive/events",
    "/api/archive/alarms",
    "/api/archive/playback",
    "/api/archive/commands",
    "/api/displays/system-overview",
    "/api/displays/contact-schedule",
    "/api/displays/power-budget",
    "/api/displays/alarm-trends",
]


@pytest.mark.parametrize("route", MCS_ROUTES_THE_UI_MUST_CALL)
def test_console_calls_every_endpoint_phase_g_wired(route):
    assert route in INDEX.read_text(), (
        f"{route} is registered but the console never calls it — "
        "that is exactly the O-12/O-13 debt Phase G was closing")


def test_registered_routes_match_expectation():
    """The legacy shims are gone and the Phase G routes are registered.

    Read from the source the same way the repo-wide reachability test does,
    so no event loop has to be started to enumerate routes.
    """
    src = (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs"
           / "server.py").read_text()
    assert 'add_post("/api/command"' not in src, \
        "the disabled /api/command 410 stub is back"
    assert 'add_get("/catalog"' not in src, "the /catalog legacy shim is back"
    assert '"/api/displays/procedure-status"' not in src
    for route in MCS_ROUTES_THE_UI_MUST_CALL:
        assert f'"{route}"' in src, f"{route} is not registered"


def test_legacy_command_path_is_gone_from_the_console():
    """The function and the fetch are gone; only the tombstone comment remains."""
    html = INDEX.read_text()
    assert "function sendLegacyCommand" not in html
    assert "async function sendLegacyCommand" not in html
    assert "fetch('/api/command'" not in html


def test_trends_tab_is_present_and_reachable():
    html = INDEX.read_text()
    assert 'data-tab="trends"' in html
    assert 'id="pane-trends"' in html
    assert "initTrendsTab" in html
    assert "trendExportCsv" in html          # CSV export
    assert "trendShadePlugin" in html        # eclipse / contact shading
    positions = yaml.safe_load(
        (CONFIG_DIR / "mcs" / "positions.yaml").read_text())["positions"]
    for name, cfg in positions.items():
        assert "trends" in cfg["visible_tabs"], \
            f"{name} cannot reach the TRENDS tab"


def test_alarm_filters_persist_and_new_alarm_cue_exists():
    html = INDEX.read_text()
    assert "ALARM_FILTER_KEY" in html and "localStorage" in html
    assert "flashNewAlarmCue" in html
    assert "openProcedureFromAlarm" in html
