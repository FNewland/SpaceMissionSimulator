"""Defect #76 — the MCS FDIR window and the model behind it.

The textbook expects an FDIR display on the operator console (Ch 5.20 §2
pp. 314-316, Ch 7.5 pp. 338-339, Ch 12 pp. 406-411, Ch 15.2-15.4 pp. 497-499).
The MCS had none: `static/displays.js` held an FDIR renderer that index.html
never loaded, and `FDIRAlarmPanel.update_s12_rules` / `update_s19_rules` /
`set_fdir_level` were never called from anywhere in the repo, so
`/api/displays/fdir-alarms` reported 0 rules and "nominal" regardless of
spacecraft state.

These tests drive the real telemetry path — wire bytes in, panel state out.
"""
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.displays.fdir_panel import FDIRDisplayModel
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


def _pkt(service, subtype, data, apid=1, seq=0):
    """A minimal stand-in for a decommutated packet."""
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _s12_defs(records):
    out = struct.pack('>H', len(records))
    for mon_id, param_id, check, enabled, low, high in records:
        out += struct.pack('>HHBBff', mon_id, param_id, check, enabled, low, high)
    return out


def _s19_defs(records):
    out = struct.pack('>H', len(records))
    for ea_id, kind, trig, func, enabled in records:
        out += struct.pack('>HBHBB', ea_id, kind, trig, func, enabled)
    return out


def _s12_trans(param_id, value, mon_id, check, sev, low, high):
    return struct.pack('>HfHBBff', param_id, value, mon_id, check, sev, low, high)


# ── the model in isolation ──────────────────────────────────────────

class TestFDIRModelReference:
    def test_config_reference_is_the_starting_table(self):
        """Names are ground knowledge — TM(12,13) carries ids and limits only."""
        srv = _make_server()
        rows = srv._fdir_model.monitor_rows()
        assert len(rows) == 46, "all 46 s12_definitions.yaml rules must be present"
        assert rows[0]["name"] == "EPS Battery SoC Warning"
        assert all(r["onboard"] is False for r in rows), \
            "nothing reported by the spacecraft yet"

    def test_s19_reference_resolves_trigger_and_action_labels(self):
        srv = _make_server()
        rules = {r["ea_id"]: r for r in srv._fdir_model.rule_rows()}
        assert len(rules) == 32
        r = rules[1001]
        assert r["name"] == "eps_bus_undervoltage_payload_off"
        assert r["trigger_kind_name"] in ("event", "param")
        assert r["action_label"].startswith(str(r["action_func_id"]))

    def test_mon_ids_line_up_with_the_simulators_loader(self):
        """Both sides default mon_id to S12_CONFIG_MON_ID_BASE + file index."""
        from smo_simulator.monitoring_loader import (
            S12_CONFIG_MON_ID_BASE, load_s12_definitions,
        )
        srv = _make_server()
        sim_ids = set(load_s12_definitions(CONFIG_DIR))
        mcs_ids = {r["mon_id"] for r in srv._fdir_model.monitor_rows()}
        assert mcs_ids == sim_ids
        assert min(mcs_ids) == S12_CONFIG_MON_ID_BASE


class TestFDIRModelTransitions:
    def _model(self):
        m = FDIRDisplayModel()
        m.load_reference(
            s12={0x8000: {"mon_id": 0x8000, "param_id": 0x0101, "check_type": 0,
                          "low_limit": 20.0, "high_limit": 100.0, "enabled": True,
                          "name": "EPS Battery SoC Warning", "severity": "WARNING"}},
            s19={1001: {"ea_id": 1001, "name": "eps_soc_low_payload_off",
                        "trigger_kind": 1, "trigger_id": 0x0101,
                        "action_func_id": 20, "enabled": True}},
            events={0x0102: {"name": "BATTERY_SOC_CRITICAL", "subsystem": "eps"}},
            funcs={20: "EPS_POWER_LINE_OFF"},
            params={0x0101: "eps.bat_soc"},
        )
        return m

    def test_out_of_limits_transition_marks_the_monitor(self):
        m = self._model()
        m.ingest_s12_transition(
            {"param_id": 0x0101, "value": 17.5, "mon_id": 0x8000,
             "check_type": 0, "severity": 3, "low_limit": 20.0,
             "high_limit": 100.0, "entered": True}, ts=1000.0)
        row = m.monitor_rows()[0]
        assert row["status"] == "OUT_OF_LIMITS"
        assert row["violation_count"] == 1
        assert row["current_value"] == pytest.approx(17.5)

    def test_return_to_nominal_clears_the_monitor(self):
        m = self._model()
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True}, ts=1000.0)
        m.ingest_s12_transition({"param_id": 0x0101, "value": 45.0,
                                 "mon_id": 0x8000, "entered": False}, ts=1010.0)
        row = m.monitor_rows()[0]
        assert row["status"] == "IN_LIMITS"
        assert row["violation_count"] == 1, "the violation still happened"

    def test_a_parameter_violation_attributes_the_matching_rule(self):
        """S19 does not report "rule N fired"; the trigger telemetry does."""
        m = self._model()
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True}, ts=1000.0)
        rule = m.rule_rows()[0]
        assert rule["fire_count"] == 1
        assert rule["last_fire_ts"] == 1000.0
        assert rule["fire_inferred"] is True

    def test_an_event_rule_does_not_fire_on_a_parameter_of_the_same_id(self):
        """Event ids and parameter ids share 0x0000-0x0FFF — namespace matters."""
        m = self._model()
        m._s19_ref[1001]["trigger_kind"] = 0        # same id, event namespace
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True}, ts=1000.0)
        assert m.rule_rows()[0]["fire_count"] == 0

    def test_a_disabled_rule_is_never_attributed_a_fire(self):
        m = self._model()
        m.ingest_s19_definitions([{"ea_id": 1001, "trigger_kind": 1,
                                   "trigger_id": 0x0101, "action_func_id": 20,
                                   "enabled": False}])
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True}, ts=1000.0)
        assert m.rule_rows()[0]["fire_count"] == 0

    def test_journal_correlates_events_and_s12_transitions(self):
        m = self._model()
        m.ingest_event(0x0102, 4, "Battery SoC below 10%", ts=1000.0)
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True,
                                 "low_limit": 20.0, "high_limit": 100.0}, ts=1001.0)
        kinds = [e["kind"] for e in m.journal()]
        assert "event" in kinds and "s12_in" in kinds and "rule" in kinds
        evt = next(e for e in m.journal() if e["kind"] == "event")
        assert evt["label"] == "BATTERY_SOC_CRITICAL"

    def test_synthetic_s12_mirror_events_are_not_duplicated_in_the_journal(self):
        """The sim also emits 0x9xxx/0xAxxx S5 events for every S12 transition."""
        m = self._model()
        m.ingest_s12_transition({"param_id": 0x0101, "value": 17.5,
                                 "mon_id": 0x8000, "entered": True}, ts=1000.0)
        before = len([e for e in m.journal() if e["kind"] in ("event", "s12_in")])
        assert m.ingest_event(0x9000 | 0x0101, 3, "…", ts=1000.0) is None
        after = len([e for e in m.journal() if e["kind"] in ("event", "s12_in")])
        assert after == before

    def test_a_reported_table_replaces_the_ground_reference(self):
        m = self._model()
        m.ingest_s12_definitions([
            {"mon_id": 0x8000, "param_id": 0x0101, "check_type": 0,
             "enabled": False, "low_limit": 15.0, "high_limit": 90.0},
        ])
        row = m.monitor_rows()[0]
        assert row["onboard"] is True
        assert row["enabled"] is False
        assert row["low_limit"] == pytest.approx(15.0)
        assert row["name"] == "EPS Battery SoC Warning", "ground name is kept"

    def test_a_monitor_deleted_on_board_disappears(self):
        m = self._model()
        m.ingest_s12_definitions([])
        assert m.monitor_rows() == []


class TestFDIRLevelDerivation:
    def _m(self):
        m = FDIRDisplayModel()
        m.load_reference(s12={0x8000: {"mon_id": 0x8000, "param_id": 0x0101},
                              0x8001: {"mon_id": 0x8001, "param_id": 0x0207}})
        return m

    def test_nominal_when_nothing_is_wrong(self):
        assert self._m().derive_level([], 0) == "nominal"

    def test_one_monitor_out_of_limits_is_equipment_level(self):
        m = self._m()
        m.ingest_s12_transition({"param_id": 0x0101, "mon_id": 0x8000,
                                 "value": 1.0, "entered": True})
        assert m.derive_level([], 0) == "equipment"

    def test_two_monitors_out_of_limits_is_subsystem_level(self):
        m = self._m()
        for mon, pid in ((0x8000, 0x0101), (0x8001, 0x0207)):
            m.ingest_s12_transition({"param_id": pid, "mon_id": mon,
                                     "value": 1.0, "entered": True})
        assert m.derive_level([], 0) == "subsystem"

    def test_a_critical_alarm_is_system_level(self):
        assert self._m().derive_level([{"severity": "CRITICAL"}], 0) == "system"

    def test_safe_mode_is_system_level(self):
        m = self._m()
        m.set_sc_mode(0)
        assert m.derive_level([], 0) == "system"

    def test_emergency_mode_is_system_level(self):
        m = self._m()
        m.set_sc_mode(3)
        assert m.derive_level([], 0) == "system"

    def test_load_shed_escalates(self):
        m = self._m()
        assert m.derive_level([], 1) == "subsystem"
        assert m.derive_level([], 3) == "system"

    def test_state_strip_reports_latches_and_their_provenance(self):
        m = self._m()
        m.set_sc_mode(3, source="sim_state")
        strip = m.state_strip("system", load_shed_stage=2)
        assert strip["sc_mode_name"] == "EMERGENCY"
        assert strip["emergency_latched"] is True
        assert strip["safe_latched"] is False
        assert strip["latch_source"] == "derived_from_sc_mode"
        assert strip["sc_mode_source"] == "sim_state"
        assert strip["load_shed_stage"] == 2

    def test_mode_change_is_journalled(self):
        m = self._m()
        m.set_sc_mode(1)
        m.set_sc_mode(0)
        modes = [e for e in m.journal() if e["kind"] == "mode"]
        assert len(modes) == 2
        assert modes[0]["label"].endswith("SAFE")


# ── through the server's TM path ────────────────────────────────────

@pytest.mark.asyncio
class TestServerTelemetryPath:
    async def _drain(self, srv, pkt):
        srv._archive = MagicMock()
        await srv._process_tm(pkt)

    async def test_s12_13_populates_the_monitor_table(self):
        srv = _make_server()
        await self._drain(srv, _pkt(12, 13, _s12_defs([
            (0x8000, 0x0101, 0, 1, 20.0, 100.0),
            (0x8001, 0x0101, 0, 1, 10.0, 100.0),
        ])))
        rows = srv._fdir_model.monitor_rows()
        assert len(rows) == 2
        assert all(r["onboard"] for r in rows)

    async def test_s19_128_populates_the_rule_table(self):
        srv = _make_server()
        await self._drain(srv, _pkt(19, 128, _s19_defs([
            (1001, 0, 0x0102, 10, 1),
            (1002, 1, 0x0101, 20, 0),
        ])))
        rules = {r["ea_id"]: r for r in srv._fdir_model.rule_rows()}
        assert set(rules) == {1001, 1002}
        assert rules[1002]["enabled"] is False

    async def test_s12_9_marks_the_monitor_and_raises_an_alarm(self):
        srv = _make_server()
        await self._drain(srv, _pkt(12, 13, _s12_defs([
            (0x8000, 0x0101, 0, 1, 20.0, 100.0)])))
        await self._drain(srv, _pkt(12, 9,
                                    _s12_trans(0x0101, 17.5, 0x8000, 0, 3, 20.0, 100.0)))
        row = srv._fdir_model.monitor_rows()[0]
        assert row["status"] == "OUT_OF_LIMITS"
        assert len(srv._alarm_journal) == 1
        assert srv._alarm_journal[0]["source"] == "S12"

    async def test_the_legacy_endpoint_stops_reporting_zero_rules(self):
        """update_s12_rules/update_s19_rules/set_fdir_level are now called."""
        srv = _make_server()
        await self._drain(srv, _pkt(19, 128, _s19_defs([(1001, 0, 0x0102, 10, 1)])))
        data = srv._fdir_alarm_panel.get_display_data()
        assert data["s19_event_action"]["active_rules"] == 1
        assert data["s12_monitoring"]["active_rules"] > 0

    async def test_fdir_level_escalates_from_real_telemetry(self):
        srv = _make_server()
        await self._drain(srv, _pkt(12, 13, _s12_defs([
            (0x8000, 0x0101, 0, 1, 20.0, 100.0),
            (0x8001, 0x0207, 0, 1, -1.0, 1.0),
        ])))
        assert srv._fdir_alarm_panel.get_display_data()["fdir_level"] == "nominal"
        for mon, pid in ((0x8000, 0x0101), (0x8001, 0x0207)):
            await self._drain(srv, _pkt(12, 9,
                                        _s12_trans(pid, 99.0, mon, 0, 3, -1.0, 1.0)))
        assert srv._fdir_alarm_panel.get_display_data()["fdir_level"] != "nominal"


# ── /api/fdir shape ─────────────────────────────────────────────────

def _app(server):
    app = web.Application()
    app.router.add_get("/api/fdir", server._handle_fdir_display)
    app.router.add_get("/api/schedule", server._handle_schedule_report)
    app.router.add_get("/api/memory-map", server._handle_memory_map)
    app.router.add_get("/api/ground-stations", server._handle_ground_stations)
    app.router.add_get("/api/displays/fdir-alarms", server._handle_fdir_alarms)
    return app


@pytest.fixture
async def mcs():
    server = _make_server()
    server._running = False
    cli = TestClient(TestServer(_app(server)))
    await cli.start_server()
    yield server, cli
    await cli.close()


@pytest.mark.asyncio
class TestFDIRApi:
    async def test_payload_carries_all_five_panels(self, mcs):
        _srv, cli = mcs
        body = await (await cli.get("/api/fdir")).json()
        for key in ("state", "monitors", "monitor_summary", "rules",
                    "rule_summary", "journal", "active_alarms"):
            assert key in body, key

    async def test_state_strip_fields(self, mcs):
        _srv, cli = mcs
        st = (await (await cli.get("/api/fdir")).json())["state"]
        for key in ("fdir_level", "sc_mode", "sc_mode_name", "safe_latched",
                    "emergency_latched", "load_shed_stage",
                    "monitors_out_of_limits", "s12_reported", "s19_reported"):
            assert key in st, key

    async def test_monitor_rows_are_display_ready(self, mcs):
        _srv, cli = mcs
        rows = (await (await cli.get("/api/fdir")).json())["monitors"]
        assert rows
        for key in ("mon_id", "name", "param_id", "param_name", "check_type_name",
                    "low_limit", "high_limit", "enabled", "status",
                    "current_value", "violation_count"):
            assert key in rows[0], key

    async def test_rule_rows_are_display_ready(self, mcs):
        _srv, cli = mcs
        rows = (await (await cli.get("/api/fdir")).json())["rules"]
        assert rows
        for key in ("ea_id", "name", "trigger_kind_name", "trigger_id",
                    "trigger_label", "action_func_id", "action_label",
                    "enabled", "fire_count"):
            assert key in rows[0], key

    async def test_legacy_endpoint_is_consistent_with_the_window(self, mcs):
        srv, cli = mcs
        legacy = await (await cli.get("/api/displays/fdir-alarms")).json()
        window = await (await cli.get("/api/fdir")).json()
        assert legacy["s12_monitoring"]["active_rules"] == \
            window["monitor_summary"]["enabled"]
        assert legacy["s19_event_action"]["active_rules"] == \
            window["rule_summary"]["enabled"]
        assert legacy["fdir_level"] == window["state"]["fdir_level"]
