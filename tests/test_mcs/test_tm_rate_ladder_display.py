"""Defect #86 — the MCS displays the TM rate LADDER, not a bare number.

The TT&C page used to print ``ttc.data_rate_bps`` and nothing else. With a
three-rung ladder that is not enough information to operate on:

  * the tier carries the modulation and the coding, so "128000 bps" hides that
    the link just moved from BPSK conv+RS to QPSK RS(255,223);
  * the tier carries PERMISSION GATES, so the tier the ground COMMANDED and the
    tier the spacecraft is FLYING can differ. That difference is a diagnosis —
    "HIGH was commanded, the 10 deg gate refused it" — and a console that shows
    only the active rate turns it into a mystery ("why is the dump slow?").

These tests pin: the ladder is read from ttc.yaml (never hard-coded), the label
format, the active-vs-commanded resolution including the demotion reason, and
the new parameters / command reaching the console's config.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


@pytest.fixture(scope="module")
def server():
    return _make_server()


@pytest.fixture(scope="module")
def ttc_yaml():
    return yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())


# ── The ladder comes from config ───────────────────────────────────

class TestLadderIsConfigDriven:

    def test_ladder_matches_ttc_yaml_rung_for_rung(self, server, ttc_yaml):
        ladder = server._tm_rate_ladder
        cfg = ttc_yaml["tm_rate_tiers"]
        assert [t["name"] for t in ladder] == [t["name"] for t in cfg]
        assert [t["bps"] for t in ladder] == [t["bps"] for t in cfg]
        assert [t["command_code"] for t in ladder] == \
            [t["command_code"] for t in cfg]

    def test_labels_name_the_tier_modulation_and_coding(self, server):
        labels = {t["name"]: t["label"] for t in server._tm_rate_ladder}
        assert labels["LOW"] == "LOW (16 kbps, BPSK, conv+RS)"
        assert labels["HIGH"] == "HIGH (128 kbps, QPSK, RS(255,223))"
        assert labels["BEACON"] == "BEACON (1 kbps, BPSK, conv+RS)"
        # No NOMINAL-style bare number survives anywhere in the ladder.
        for label in labels.values():
            assert "kbps" in label or "bps" in label
            assert "(" in label

    def test_the_gates_travel_with_the_tier(self, server):
        high = next(t for t in server._tm_rate_ladder if t["name"] == "HIGH")
        assert high["requires_antenna"] is True
        assert high["requires_downlink_mode"] is True
        assert high["min_elevation_deg"] == 10.0
        low = next(t for t in server._tm_rate_ladder if t["name"] == "LOW")
        assert low["requires_downlink_mode"] is False
        assert low["min_elevation_deg"] == 0.0


# ── Active vs commanded ────────────────────────────────────────────

class TestActiveVersusCommanded:

    def test_agreeing_tiers_report_no_demotion(self, server):
        view = server._build_link_tier_view({
            "tm_tier": 1, "tm_tier_cmd": 1,
            "tm_symbol_rate": 16000.0, "downlink_mode": False})
        assert view["tm_tier_name"] == "LOW"
        assert view["tm_tier_label"] == "LOW (16 kbps, BPSK, conv+RS)"
        assert view["tm_tier_cmd_name"] == "LOW"
        assert view["tm_tier_demoted"] is False
        assert view["symbol_rate_bd"] == 16000.0
        assert view["downlink_mode"] is False

    def test_a_demoted_link_is_visible_not_hidden(self, server):
        """HIGH commanded, LOW active — the console must say so, and say why."""
        view = server._build_link_tier_view({
            "tm_tier": 1, "tm_tier_cmd": 2,
            "downlink_mode": True, "antenna_deployed": True,
            "elevation_deg": 6.0})
        assert view["tm_tier_name"] == "LOW"
        assert view["tm_tier_cmd_name"] == "HIGH"
        assert view["tm_tier_demoted"] is True
        assert "10 deg" in view["tm_tier_demotion_reason"]
        assert "6.0" in view["tm_tier_demotion_reason"]

    def test_demotion_reason_names_the_mode_gate(self, server):
        view = server._build_link_tier_view({
            "tm_tier": 1, "tm_tier_cmd": 2,
            "antenna_deployed": True, "downlink_mode": False,
            "elevation_deg": 40.0})
        assert view["tm_tier_demoted"] is True
        assert "DOWNLINK" in view["tm_tier_demotion_reason"]
        assert "79" in view["tm_tier_demotion_reason"]

    def test_demotion_reason_names_the_antenna_gate(self, server):
        view = server._build_link_tier_view({
            "tm_tier": 1, "tm_tier_cmd": 2,
            "antenna_deployed": False, "downlink_mode": True,
            "elevation_deg": 40.0})
        assert "antenna" in view["tm_tier_demotion_reason"].lower()

    def test_the_simulator_state_block_is_the_fallback(self, server):
        """A console without 0x0539-0x053C downlinked still labels the tier:
        the simulator's /api/state `ttc` block carries the same fields."""
        srv = _make_server()
        srv._ingest_sim_health({"ttc": {
            "tm_tier": 2, "tm_tier_name": "HIGH", "tm_coding": "RS",
            "symbol_rate_bd": 64000.0, "downlink_mode": True,
            "data_rate_bps": 128000, "elevation_deg": 35.0}})
        view = srv._build_link_tier_view({})
        assert view["tm_tier_name"] == "HIGH"
        assert view["tm_tier_label"] == "HIGH (128 kbps, QPSK, RS(255,223))"
        assert view["symbol_rate_bd"] == 64000.0
        assert view["downlink_mode"] is True

    def test_unknown_tier_index_degrades_quietly(self, server):
        assert server._tier_by_index(None) is None
        assert server._tier_by_index(99) is None
        assert server._build_link_tier_view({}) == {}


# ── New parameters and the new command ─────────────────────────────

class TestNewParametersAndCommand:

    LADDER_PARAMS = {
        0x0539: "ttc.tm_tier",
        0x053A: "ttc.tm_tier_cmd",
        0x053B: "ttc.tm_symbol_rate",
        0x053C: "ttc.downlink_mode",
    }

    def test_parameters_are_declared_and_reach_the_console_meta(self, server):
        for pid, name in self.LADDER_PARAMS.items():
            meta = server._param_meta.get(pid)
            assert meta is not None, f"0x{pid:04X} missing from parameters.yaml"
            assert meta["name"] == name
            assert meta["subsystem"] == "ttc"

    def test_every_ladder_parameter_is_limit_checked(self):
        limits = yaml.safe_load(
            (CONFIG_DIR / "mcs" / "limits.yaml").read_text())["limits"]
        by_id = {int(l["param_id"]): l for l in limits}
        for pid in list(self.LADDER_PARAMS) + [0x0506]:
            assert pid in by_id, f"0x{pid:04X} has no limit definition"
            lim = by_id[pid]
            assert lim["red_low"] <= lim["yellow_low"]
            assert lim["yellow_high"] <= lim["red_high"]

    def test_the_tier_limits_treat_low_as_nominal_and_beacon_as_off_nominal(self):
        """The ladder's whole point: routine ops sit at LOW, and dropping to
        the beacon lifeline is the alarm case — not the other way round."""
        limits = {int(l["param_id"]): l for l in yaml.safe_load(
            (CONFIG_DIR / "mcs" / "limits.yaml").read_text())["limits"]}
        rate = limits[0x0506]
        assert rate["yellow_low"] == 16000.0     # LOW is the floor of nominal
        assert rate["red_low"] < 1000.0          # the beacon warns, not alarms
        assert rate["yellow_high"] >= 128000.0   # HIGH is legitimate
        tier = limits[0x0539]
        assert tier["yellow_low"] == 1.0         # index 1 = LOW
        assert tier["yellow_high"] == 2.0        # index 2 = HIGH

    def test_downlink_mode_command_is_catalogued_as_one_uint8(self):
        catalog = yaml.safe_load(
            (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        cmd = next(c for c in catalog["commands"]
                   if c["name"] == "TTC_SET_DOWNLINK_MODE")
        assert cmd["service"] == 8 and cmd["subtype"] == 1
        assert cmd["func_id"] == 79
        assert len(cmd["fields"]) == 1 and cmd["fields"][0]["type"] == "uint8"

    def test_the_ttc_position_may_send_the_downlink_mode_command(self):
        positions = yaml.safe_load(
            (CONFIG_DIR / "mcs" / "positions.yaml").read_text())["positions"]
        allowed = positions["ttc"]["allowed_func_ids"]
        assert 79 in allowed, "TT&C cannot gate the HIGH tier without func 79"
        assert 65 in allowed


# ── The console page itself ────────────────────────────────────────

class TestConsolePage:

    @pytest.fixture(scope="class")
    @classmethod
    def page(cls):
        return (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" /
                "static" / "index.html").read_text()

    def test_the_rate_command_buttons_send_the_catalogued_one_byte_form(self):
        # Phase E WP-5: quick commands are generated from tc_catalog.yaml's
        # quick_commands list — the buttons carry ENGINEERING values (tier
        # codes) and the hex is computed by the shared encoder, so the wire
        # is "41 <code>" by construction. Assert the list carries both rungs.
        import yaml
        raw = yaml.safe_load((REPO / "configs" / "eosat1" / "commands" /
                              "tc_catalog.yaml").read_text())
        quick = raw.get("quick_commands") or []
        rates = {q["name"]: q for q in quick
                 if q.get("command") == "TTC_SET_DATA_RATE"}
        assert rates["TTC_RATE_HIGH"]["args"] == {"rate": 1}
        assert rates["TTC_RATE_LOW"]["args"] == {"rate": 0}
        from smo_common.commanding import encode, load_catalog
        cat = load_catalog(REPO / "configs" / "eosat1")
        assert encode("TTC_SET_DATA_RATE", {"rate": 1}, catalog=cat).hex() == "4101"
        assert encode("TTC_SET_DATA_RATE", {"rate": 0}, catalog=cat).hex() == "4100"

    def test_the_page_carries_no_stale_raw_bps_literal(self, page):
        assert "41 00 00 FA 00" not in page, "stale 64000 raw-bps rate command"
        assert "41 00 00 19 00" not in page

    def test_there_is_a_downlink_mode_control_next_to_the_rate_control(self):
        import yaml
        raw = yaml.safe_load((REPO / "configs" / "eosat1" / "commands" /
                              "tc_catalog.yaml").read_text())
        quick = raw.get("quick_commands") or []
        dl = [q for q in quick if q.get("command") == "TTC_SET_DOWNLINK_MODE"]
        assert {q["args"]["on"] for q in dl} == {0, 1}
        # ...and it sits in the same TTC group as the rate controls.
        assert all(q.get("group") == "TTC" for q in dl)

    def test_the_rf_panel_shows_tier_active_commanded_and_downlink_mode(self, page):
        for element_id in ("ttc-rate", "ttc-rate-cmd", "ttc-rate-demoted",
                           "ttc-symrate", "ttc-dlmode-lbl"):
            assert f'id="{element_id}"' in page
        assert "updateTTCRateLadder" in page
        # the rate table is not duplicated in JavaScript
        assert "state.tm_rate_ladder" in page
