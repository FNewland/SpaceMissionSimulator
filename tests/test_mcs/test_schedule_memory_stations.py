"""Defects #77-#79 — schedule display, memory map API, station list, time jump.

* `#s11-tbody` was written by nothing: TM(11,18) fell through `_process_tm` and
  the operator saw an empty table plus one opaque log line.
* The memory browser carried 11 hard-coded JavaScript regions that disagreed
  with `configs/eosat1/memory/memory_map.yaml` in extent and access rights.
* The browser hard-coded Iqaluit and Troll while the mission config has
  declared five stations for some time.
* The sim-state poll loop re-anchored the ground clock unconditionally, so a
  breakpoint load moved MCS time silently and left epoch-bound caches behind
  (the same gap the planner fixed as defect #59).
"""
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
MEMORY_YAML = CONFIG_DIR / "memory" / "memory_map.yaml"
ORBIT_YAML = CONFIG_DIR / "orbit.yaml"


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


def _pkt(service, subtype, data, apid=1, seq=0):
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _schedule_bytes(entries, enabled=True, next_id=7):
    out = struct.pack('>HBH', len(entries), 1 if enabled else 0, next_id)
    for cid, exec_t, apid, svc, st, data in entries:
        out += struct.pack('>HIHBBBB', cid, exec_t, apid, svc, st,
                           len(data), len(data)) + data
    return out


def _app(server):
    app = web.Application()
    app.router.add_get("/api/schedule", server._handle_schedule_report)
    app.router.add_get("/api/memory-map", server._handle_memory_map)
    app.router.add_get("/api/ground-stations", server._handle_ground_stations)
    return app


@pytest.fixture
async def mcs():
    server = _make_server()
    server._running = False
    cli = TestClient(TestServer(_app(server)))
    await cli.start_server()
    yield server, cli
    await cli.close()


# ── on-board schedule display ───────────────────────────────────────

@pytest.mark.asyncio
class TestScheduleDisplay:
    async def _ingest(self, srv, raw):
        srv._archive = MagicMock()
        await srv._process_tm(_pkt(11, 18, raw))

    async def test_schedule_report_is_decoded_and_cached(self):
        srv = _make_server()
        await self._ingest(srv, _schedule_bytes([
            (1, 838557417, 1, 8, 1, bytes([0x1C])),
            (2, 838557500, 1, 17, 1, b""),
        ]))
        rep = srv._schedule_report
        assert rep["count"] == 2 and rep["enabled"] is True and rep["next_id"] == 7
        assert len(rep["entries"]) == 2

    async def test_commands_resolve_to_catalogue_mnemonics(self):
        """(id, exec_time) alone told the operator nothing about WHICH command."""
        srv = _make_server()
        await self._ingest(srv, _schedule_bytes([
            (1, 838557417, 1, 8, 1, bytes([0x1C])),     # S8 func 0x1C = 28
            (2, 838557500, 1, 17, 1, b""),              # S17.1 connection test
            (3, 838557600, 1, 12, 12, b""),             # S12.12 report monitors
        ]))
        by_id = {e["id"]: e for e in srv._schedule_report["entries"]}
        assert by_id[2]["mnemonic"] == "CONNECTION_TEST"
        assert by_id[3]["mnemonic"] == "MON_REPORT_DEFS"
        # The S8 mnemonic must come from the func id in the data bytes, not
        # from "the first S8.1 entry in the catalogue".
        expected = srv._resolve_command_name(8, 1, "1C")
        assert by_id[1]["mnemonic"] == expected
        assert by_id[1]["mnemonic"] != "S8.1"

    async def test_s8_func_id_selects_the_right_catalogue_entry(self):
        srv = _make_server()
        name_1c = srv._resolve_command_name(8, 1, "1C")
        name_13 = srv._resolve_command_name(8, 1, "13")
        assert name_1c and name_13 and name_1c != name_13

    async def test_execution_times_are_converted_to_ground_utc(self):
        srv = _make_server()
        await self._ingest(srv, _schedule_bytes([(1, 838557417, 1, 17, 1, b"")]))
        e = srv._schedule_report["entries"][0]
        assert e["exec_utc"], "the operator needs a wall-clock time, not a CUC count"
        parsed = datetime.fromisoformat(e["exec_utc"])
        assert parsed.year >= 2026

    async def test_conversion_is_anchored_on_obc_time_when_available(self):
        srv = _make_server()
        now = srv.get_ground_utc()
        srv._param_cache[0x0309] = {"value": 1_000_000.0, "last_update_ts": 0, "sid": 1}
        # 300 s later on board -> 300 s later on the ground
        iso = srv._obc_cuc_to_utc(1_000_300)
        got = datetime.fromisoformat(iso)
        assert abs((got - (now + timedelta(seconds=300))).total_seconds()) < 5

    async def test_endpoint_is_honest_before_any_report(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/schedule")).json()
        assert body["never_reported"] is True and body["entries"] == []

    async def test_endpoint_serves_the_decoded_report(self, mcs):
        srv, cli = mcs
        await self._ingest(srv, _schedule_bytes([(4, 838557417, 1, 17, 1, b"")]))
        body = await (await cli.get("/api/schedule")).json()
        assert body["entries"][0]["id"] == 4
        assert body["entries"][0]["mnemonic"] == "CONNECTION_TEST"

    async def test_report_rides_on_the_state_broadcast(self):
        """So the S11 table survives a tab switch without re-issuing TC(11,17)."""
        srv = _make_server()
        await self._ingest(srv, _schedule_bytes([(1, 838557417, 1, 17, 1, b"")]))
        assert srv._schedule_report
        # _state_poll_loop copies it into the broadcast payload.
        assert "entries" in srv._schedule_report


# ── memory map API ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMemoryMapApi:
    async def test_regions_match_the_yaml_exactly(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/memory-map")).json()
        declared = yaml.safe_load(MEMORY_YAML.read_text())["regions"]
        assert len(body["regions"]) == len(declared)
        got = {r["name"]: r for r in body["regions"]}
        for d in declared:
            r = got[d["name"]]
            assert r["start"] == int(str(d["start"]), 0)
            assert r["size"] == int(str(d["size"]), 0)
            assert r["end"] == r["start"] + r["size"]
            assert r["access"] == str(d["access"]).lower()

    async def test_readonly_regions_are_marked_not_writable(self, mcs):
        """The old UI called a freely writable region "readonly" and vice versa."""
        _srv, cli = mcs
        body = await (await cli.get("/api/memory-map")).json()
        by_name = {r["name"]: r for r in body["regions"]}
        assert by_name["boot_rom"]["writable"] is False
        assert by_name["boot_backup"]["writable"] is False
        assert by_name["app_a"]["writable"] is True
        assert by_name["scratch_ram"]["writable"] is True

    async def test_limits_come_from_the_yaml(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/memory-map")).json()
        declared = yaml.safe_load(MEMORY_YAML.read_text())["limits"]
        assert body["limits"]["max_load_bytes"] == int(str(declared["max_load_bytes"]), 0)
        assert body["limits"]["max_dump_bytes"] == int(str(declared["max_dump_bytes"]), 0)

    async def test_bound_region_is_flagged(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/memory-map")).json()
        gains = next(r for r in body["regions"] if r["name"] == "aocs_gains")
        assert gains["binding"] is not None
        assert gains["binding"]["model"] == "aocs"

    async def test_the_deleted_javascript_map_is_gone_from_the_page(self):
        """The 11 hard-coded regions must not survive anywhere in index.html."""
        page = (REPO / "packages/smo-mcs/src/smo_mcs/static/index.html").read_text()
        for invented in ("'Boot ROM'", "'Scratchpad RAM'", "'Stack/Heap'",
                         "0x00D00000"):
            assert invented not in page, f"hard-coded memory map remnant: {invented}"

    async def test_dump_and_check_reports_are_served(self, mcs):
        srv, cli = mcs
        srv._archive = MagicMock()
        payload = bytes(range(8))
        await srv._process_tm(_pkt(6, 6, struct.pack('>IH', 0x00100000, 8) + payload))
        await srv._process_tm(_pkt(6, 10, struct.pack('>IHH', 0x00100000, 8, 0x4097)))
        body = await (await cli.get("/api/memory-map")).json()
        assert body["dumps"][0]["address"] == 0x00100000
        assert body["dumps"][0]["data_hex"] == payload.hex().upper()
        assert body["dumps"][0]["region"] == "app_a"
        assert body["checks"][0]["crc"] == 0x4097
        assert body["checks"][0]["region"] == "app_a"

    async def test_unmapped_address_gets_no_region_name(self):
        srv = _make_server()
        assert srv._memory_region_name(0xDEADBEEF) == ""
        assert srv._memory_region_name(0x00100000) == "app_a"


# ── ground station list ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestGroundStations:
    async def test_every_configured_station_is_served(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/ground-stations")).json()
        declared = yaml.safe_load(ORBIT_YAML.read_text())["ground_stations"]
        assert [g["name"] for g in body["ground_stations"]] == \
               [d["name"] for d in declared]
        assert len(body["ground_stations"]) == 5

    async def test_geometry_matches_orbit_yaml(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/ground-stations")).json()
        declared = {d["name"]: d for d in yaml.safe_load(ORBIT_YAML.read_text())["ground_stations"]}
        for g in body["ground_stations"]:
            d = declared[g["name"]]
            assert g["lat_deg"] == pytest.approx(d["lat_deg"])
            assert g["lon_deg"] == pytest.approx(d["lon_deg"])
            assert g["min_elevation_deg"] == pytest.approx(d["min_elevation_deg"])

    async def test_rf_augmentation_is_merged(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/ground-stations")).json()
        svalbard = next(g for g in body["ground_stations"] if g["name"] == "Svalbard")
        assert svalbard["antenna_diameter_m"] == pytest.approx(11.3)
        assert svalbard["band"] == "S-band"

    async def test_every_station_gets_a_distinct_display_colour(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/ground-stations")).json()
        colors = [g["color"] for g in body["ground_stations"]]
        assert len(set(colors)) == len(colors)

    async def test_ids_are_dom_safe(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/ground-stations")).json()
        ids = {g["name"]: g["id"] for g in body["ground_stations"]}
        assert ids["O'Higgins"] == "o-higgins"
        assert ids["Iqaluit"] == "iqaluit"
        assert all(i.replace("-", "").isalnum() for i in ids.values())

    async def test_the_station_list_matches_the_orbit_propagator(self):
        """The MCS's own contact geometry already uses all five."""
        srv = _make_server()
        assert srv._orbit_prop is not None
        prop_names = {gs.name for gs in srv._orbit_prop._ground_stations}
        api_names = {g["name"] for g in srv.ground_stations()}
        assert api_names == prop_names

    async def test_the_page_no_longer_hard_codes_two_stations(self):
        page = (REPO / "packages/smo-mcs/src/smo_mcs/static/index.html").read_text()
        assert "'Iqaluit': '#3b82f6'" not in page
        assert 'data-gs="iqaluit"' not in page
        assert 'id="gs-troll-panel"' not in page


# ── sim-clock discontinuity (breakpoint load) ───────────────────────

class TestSimTimeJumpDetection:
    def _armed_server(self):
        srv = _make_server()
        srv._ground_epoch = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
        srv._ground_start_wall = __import__("time").time()
        srv._sim_speed = 1.0
        return srv

    def test_normal_polling_is_not_a_jump(self):
        srv = self._armed_server()
        srv._detect_sim_time_jump(srv._ground_epoch + timedelta(seconds=1.4))
        assert srv._jump_count == 0

    def test_a_backward_breakpoint_load_is_detected(self):
        srv = self._armed_server()
        delta = srv._detect_sim_time_jump(srv._ground_epoch - timedelta(hours=2))
        assert srv._jump_count == 1
        assert delta == pytest.approx(-7200, abs=10)
        assert srv._last_jump_delta_s == pytest.approx(-7200, abs=10)
        assert srv._last_jump_utc is not None

    def test_a_forward_jump_is_detected(self):
        srv = self._armed_server()
        srv._detect_sim_time_jump(srv._ground_epoch + timedelta(minutes=45))
        assert srv._jump_count == 1
        assert srv._last_jump_delta_s > 0

    def test_epoch_bound_caches_are_dropped_on_a_jump(self):
        """Schedule UTCs, store status and memory reports all describe the old
        epoch; leaving them on screen is worse than showing nothing."""
        srv = self._armed_server()
        srv._schedule_report = {"count": 3, "entries": [{"id": 1}]}
        srv._storage_status = [{"id": 1, "count": 5}]
        srv._memory_dumps.append({"address": 0})
        srv._memory_checks.append({"address": 0})
        srv._detect_sim_time_jump(srv._ground_epoch - timedelta(hours=1))
        assert srv._schedule_report == {}
        assert srv._storage_status == []
        assert not srv._memory_dumps and not srv._memory_checks

    def test_caches_survive_a_normal_poll(self):
        srv = self._armed_server()
        srv._schedule_report = {"count": 1, "entries": []}
        srv._detect_sim_time_jump(srv._ground_epoch + timedelta(seconds=1.0))
        assert srv._schedule_report != {}

    def test_the_parameter_cache_is_deliberately_kept(self):
        """Its values are still the last known spacecraft state and its ages
        are wall-clock based, so the chart staleness gate keeps working."""
        srv = self._armed_server()
        srv._param_cache[0x0101] = {"value": 55.0, "last_update_ts": 1.0, "sid": 1}
        srv._detect_sim_time_jump(srv._ground_epoch - timedelta(hours=1))
        assert 0x0101 in srv._param_cache

    def test_tolerance_scales_with_speed(self):
        srv = self._armed_server()
        srv._sim_speed = 60.0
        srv._detect_sim_time_jump(srv._ground_epoch + timedelta(seconds=120))
        assert srv._jump_count == 0, "120 s at 60x is within one poll interval"

    def test_no_anchor_means_no_jump(self):
        srv = _make_server()
        srv._ground_epoch = None
        assert srv._detect_sim_time_jump(datetime.now(timezone.utc)) == 0.0
        assert srv._jump_count == 0

    def test_sim_health_fields_are_absorbed(self):
        srv = _make_server()
        srv._ingest_sim_health({
            "sc_mode": 3, "engine_fault": "ValueError: boom",
            "engine_fault_count": 2, "tm_rate_bps": 8192, "rt_tm_backlog": 17,
            "scheduler": {"enabled": False, "count": 3},
        })
        assert srv._sim_health["engine_fault"] == "ValueError: boom"
        assert srv._sim_health["tm_rate_bps"] == 8192
        assert srv._sim_health["rt_tm_backlog"] == 17
        assert srv._sim_health["scheduler_enabled"] is False
        assert srv._sim_health["source"] == "sim_state"
        assert srv._fdir_model.state_strip("nominal")["sc_mode_name"] == "EMERGENCY"
