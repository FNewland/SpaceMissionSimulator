"""TM data-rate ladder — defect #85 (and the defect #84 G/T sign fix).

Franz's complaint was concrete: "my low rate seems to be 1 kbps which is very
low. I might expect a beacon at that rate in bootloader, but in routine
operation, I need enough to get housekeeping data down. For high rate, this
should be for delayed telemetry and image dumps."

The binary `tm_rate_lo_bps: 1000` / `tm_rate_hi_bps: 64000` switch is replaced
by a config-declared three-rung ladder in
``configs/eosat1/subsystems/ttc.yaml``:

    BEACON    1 000 bps  BPSK  conv(7,1/2)+RS   bootloader / beacon only
    LOW      16 000 bps  BPSK  conv(7,1/2)+RS   ROUTINE OPERATIONS (default),
                                                and the safe/FDIR fallback
    HIGH    128 000 bps  QPSK  RS(255,223)      delayed TM + image dumps;
                                                DOWNLINK mode + >=10 deg

These tests pin: the ladder comes from config; tier selection and the
rejection rules; the commanded tier stays authoritative across ticks (defect
#74 must not regress); telemetry reports the active tier and its rate; both TM
pacers honour the tier; the HK backlog drains at NOMINAL and grows at BEACON;
the catalogue's declared byte count matches the handler (the defect #80
class); modulation follows the tier and moves the symbol rate, not the bit
rate; and the per-tier/per-station link margins reproduce the researched
arithmetic with the ground-station G/T ADDED (defect #84).
"""
import math
import os
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from smo_common.config.schemas import TTCConfig
from smo_simulator.engine import SimulationEngine
from smo_simulator.models.ttc_basic import TTCBasicModel

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"
TTC_YAML = yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())
STATIONS = yaml.safe_load(
    (CONFIG_DIR / "planning" / "ground_stations.yaml").read_text()
)["ground_stations"]

#: NASA Explorers S-band table — a mission may occupy at most 5.00 MHz of
#: 2200-2290 MHz. This, not the link, is what actually bounds the top tier.
CHANNEL_BW_HZ = 5.0e6
EARTH_RADIUS_KM = 6378.137


def _model(**overrides) -> TTCBasicModel:
    cfg = dict(TTC_YAML)
    cfg.update(overrides)
    m = TTCBasicModel()
    m.configure(cfg)
    return m


def _orbit(elevation=30.0, range_km=800.0, in_contact=True):
    return SimpleNamespace(
        in_contact=in_contact, in_eclipse=False, solar_beta_deg=20.0,
        lat_deg=45.0, lon_deg=10.0, alt_km=450.0,
        vel_x=0.0, vel_y=7.5, vel_z=0.0,
        gs_elevation_deg=elevation, gs_azimuth_deg=180.0,
        gs_range_km=range_km)


def _slant_range_km(elevation_deg: float, altitude_km: float = 450.0) -> float:
    r = (EARTH_RADIUS_KM + altitude_km) / EARTH_RADIUS_KM
    eps = math.radians(elevation_deg)
    return EARTH_RADIUS_KM * (
        math.sqrt(r ** 2 - math.cos(eps) ** 2) - math.sin(eps))


# ══════════════════════════════════════════════════════════════════════════
# The ladder is configuration, not a code constant
# ══════════════════════════════════════════════════════════════════════════

class TestLadderIsConfiguration:

    def test_shipped_ladder(self):
        m = _model()
        assert [t["name"] for t in m.tiers] == ["BEACON", "LOW", "HIGH"]
        assert [t["bps"] for t in m.tiers] == [1000, 16000, 128000]
        assert [t["modulation"] for t in m.tiers] == ["BPSK", "BPSK", "QPSK"]
        assert [t["coding"] for t in m.tiers] == ["CONV_RS", "CONV_RS", "RS"]

    def test_the_bps_are_tunable_without_touching_code(self):
        """Franz may retune HIGH; nothing in code hard-codes any rate."""
        cfg = yaml.safe_load(
            (CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())
        cfg["tm_rate_tiers"][-1]["bps"] = 256000
        m = TTCBasicModel()
        m.configure(cfg)
        assert m.tier_bps(m.tier_index("HIGH")) == 256000
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        r = m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert r["success"] and r["tm_data_rate"] == 256000

    def test_ladder_must_ascend(self):
        cfg = dict(TTC_YAML)
        cfg["tm_rate_tiers"] = [
            {"name": "A", "bps": 128000}, {"name": "B", "bps": 16000}]
        with pytest.raises(ValueError):
            TTCBasicModel().configure(cfg)

    def test_schema_validates_and_derives_the_deprecated_aliases(self):
        cfg = TTCConfig(**TTC_YAML)
        assert [t.name for t in cfg.tm_rate_tiers] == ["BEACON", "LOW", "HIGH"]
        assert cfg.tier("HIGH").bps == 128000
        assert cfg.tier("HIGH").bits_per_symbol == 2
        # Aliases resolve for out-of-lane readers even when absent from YAML.
        bare = {k: v for k, v in TTC_YAML.items()
                if k not in ("tm_rate_hi_bps", "tm_rate_lo_bps")}
        derived = TTCConfig(**bare)
        assert derived.tm_rate_lo_bps == 16000   # safe tier
        assert derived.tm_rate_hi_bps == 128000  # top tier

    def test_schema_rejects_a_descending_ladder(self):
        bad = dict(TTC_YAML)
        bad["tm_rate_tiers"] = [
            {"name": "A", "bps": 128000}, {"name": "B", "bps": 16000}]
        with pytest.raises(Exception):
            TTCConfig(**bad)

    def test_schema_rejects_an_unknown_default_tier(self):
        bad = dict(TTC_YAML)
        bad["default_tier"] = "TURBO"
        with pytest.raises(Exception):
            TTCConfig(**bad)

    def test_uplink_no_longer_inherits_the_top_tm_tier(self):
        """`tc_uplink_rate_bps` used to default to tm_rate_hi_bps, which under
        the ladder would silently make the TELECOMMAND uplink 1.024 Mbps."""
        m = _model()
        assert m._tc_uplink_rate_bps == 4000
        assert m._tc_uplink_rate_bps != m.tier_bps(len(m.tiers) - 1)


# ══════════════════════════════════════════════════════════════════════════
# Tier selection and the rejection rules
# ══════════════════════════════════════════════════════════════════════════

class TestTierSelection:

    def test_default_operational_tier_is_low_not_the_top_of_the_ladder(self):
        """Routine operations sit at LOW; HIGH has to be earned."""
        m = _model()
        assert m.tier_name(m._default_tier) == "LOW"
        assert m._state.tm_data_rate == 16000
        assert m._default_tier != len(m.tiers) - 1

    def test_select_each_permitted_tier_by_name(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        for name, bps in (("LOW", 16000), ("HIGH", 128000)):
            r = m.handle_command({"command": "set_tm_rate", "rate": name})
            assert r["success"], r
            assert m._state.tm_data_rate == bps

    def test_legacy_binary_codes_still_decode(self):
        """0 and 1 keep meaning "the low rate" and "the high rate" verbatim —
        now 16 kbps and 128 kbps instead of 1 kbps and 64 kbps."""
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        assert m.handle_command(
            {"command": "set_tm_rate", "rate": 0})["tier"] == "LOW"
        assert m.handle_command(
            {"command": "set_tm_rate", "rate": 1})["tier"] == "HIGH"

    def test_fdir_reduce_power_byte_selects_low(self):
        """fdir/procedures/ttc_reduce_power.yaml sends data: [0x00]."""
        fdir = yaml.safe_load(
            (CONFIG_DIR / "fdir" / "procedures"
             / "ttc_reduce_power.yaml").read_text())
        step = next(s for s in fdir["steps"]
                    if s["params"].get("func_id") == 65)
        m = _model()
        r = m.handle_command(
            {"command": "set_tm_rate", "rate": step["params"]["data"][0]})
        assert r["success"] and r["tier"] == "LOW"

    def test_raw_bps_still_decodes(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        assert m.handle_command(
            {"command": "set_tm_rate", "rate": 128000})["tier"] == "HIGH"

    def test_beacon_is_rejected_in_nominal_operations(self):
        m = _model()
        m._state.antenna_deployed = True
        r = m.handle_command({"command": "set_tm_rate", "rate": "BEACON"})
        assert r["success"] is False
        assert r["error_code"] == 0x0007
        assert "bootloader" in r["message"] or "beacon" in r["message"].lower()

    def test_beacon_mode_refuses_every_other_tier(self):
        m = _model()
        m._state.antenna_deployed = True
        m.handle_command({"command": "set_beacon_mode", "on": True})
        r = m.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        assert r["success"] is False and r["error_code"] == 0x0007

    def test_beacon_tier_is_permitted_in_beacon_mode(self):
        m = _model()
        m.handle_command({"command": "set_beacon_mode", "on": True})
        r = m.handle_command({"command": "set_tm_rate", "rate": "BEACON"})
        assert r["success"] and r["tm_data_rate"] == 1000

    def test_high_needs_a_deployed_antenna(self):
        m = _model()
        m._state.downlink_mode = True
        assert m._state.antenna_deployed is False
        r = m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert r["success"] is False and r["error_code"] == 0x0007
        assert "antenna" in r["message"]

    def test_low_does_not_need_a_deployed_antenna(self):
        """LOW is the safe-mode lifeline: it must work with the antenna stowed."""
        m = _model()
        assert m._state.antenna_deployed is False
        assert m.handle_command(
            {"command": "set_tm_rate", "rate": "LOW"})["success"]

    def test_high_needs_downlink_mode(self):
        m = _model()
        m._state.antenna_deployed = True
        r = m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert r["success"] is False and r["error_code"] == 0x0007
        assert "DOWNLINK" in r["message"]

    def test_high_needs_the_elevation_gate_while_in_contact(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=6.0, range_km=1900.0), {})
        r = m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert r["success"] is False and r["error_code"] == 0x0007
        assert "elevation" in r["message"]

    def test_high_is_granted_above_the_elevation_gate(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        r = m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert r["success"] and m._state.tm_data_rate == 128000

    def test_unknown_tier_is_rejected_as_a_bad_argument(self):
        m = _model()
        r = m.handle_command({"command": "set_tm_rate", "rate": 12345})
        assert r["success"] is False and r["error_code"] == 0x0003

    def test_rejection_never_silently_clamps(self):
        """A refused tier must leave the commanded tier exactly as it was."""
        m = _model()
        m._state.antenna_deployed = True
        m.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        before = m._state.data_rate_mode
        m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})  # refused
        assert m._state.data_rate_mode == before
        assert m._state.tm_data_rate == 16000

    def test_downlink_mode_command(self):
        m = _model()
        assert m.handle_command(
            {"command": "set_downlink_mode", "on": True})["success"]
        assert m._state.downlink_mode is True
        m.handle_command({"command": "set_downlink_mode", "on": False})
        assert m._state.downlink_mode is False


# ══════════════════════════════════════════════════════════════════════════
# Commanded tier stays authoritative (defect #74 must not regress)
# ══════════════════════════════════════════════════════════════════════════

class TestCommandedTierIsAuthoritative:

    def test_commanded_tier_survives_many_ticks(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        for _ in range(20):
            m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        assert m._state.tm_data_rate == 128000
        assert m.tier_name(m._state.tm_tier) == "HIGH"

    def test_high_auto_reverts_below_the_gate_and_comes_back(self):
        """The gate demotes the ACTIVE tier only; the COMMANDED tier survives,
        so HIGH resumes on the next good geometry without re-commanding."""
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        assert m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})["success"]

        m.tick(1.0, _orbit(elevation=6.0, range_km=1900.0), {})
        assert m.tier_name(m._state.tm_tier) == "LOW"          # demoted
        assert m.tier_name(m._state.data_rate_mode) == "HIGH"  # still commanded

        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        assert m.tier_name(m._state.tm_tier) == "HIGH"
        assert m._state.tm_data_rate == 128000

    def test_stowed_antenna_demotes_to_the_safe_tier(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        m._state.antenna_deployed = False
        m.tick(1.0, _orbit(), {})
        assert m.tier_name(m._state.tm_tier) == "LOW"
        assert m._state.tm_data_rate == 16000

    def test_beacon_mode_pins_the_beacon_tier(self):
        m = _model()
        m._state.antenna_deployed = True
        m.handle_command({"command": "set_beacon_mode", "on": True})
        for _ in range(5):
            m.tick(1.0, _orbit(), {})
        assert m._state.tm_data_rate == 1000
        assert m.tier_name(m._state.tm_tier) == "BEACON"

    def test_leaving_beacon_mode_lands_on_low_not_beacon(self):
        m = _model()
        m.handle_command({"command": "set_beacon_mode", "on": True})
        m.tick(1.0, _orbit(), {})
        m.handle_command({"command": "set_beacon_mode", "on": False})
        m.tick(1.0, _orbit(), {})
        assert m.tier_name(m._state.tm_tier) == "LOW"


# ══════════════════════════════════════════════════════════════════════════
# Telemetry reports the active tier and its rate
# ══════════════════════════════════════════════════════════════════════════

class TestTierTelemetry:

    def test_params_carry_tier_rate_and_symbol_rate(self):
        m = _model()
        m._state.antenna_deployed = True
        m.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        p: dict[int, float] = {}
        m.tick(1.0, _orbit(), p)
        assert p[0x0506] == 16000             # tm_data_rate (already existed)
        assert p[0x0539] == m.tier_index("LOW")   # active tier
        assert p[0x053A] == m.tier_index("LOW")   # commanded tier
        assert p[0x053B] == 16000             # BPSK: symbol rate == bit rate
        assert p[0x053C] == 0.0               # downlink mode off

    def test_active_and_commanded_tiers_differ_when_gated(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        p: dict[int, float] = {}
        m.tick(1.0, _orbit(elevation=6.0, range_km=1900.0), p)
        assert p[0x053A] == m.tier_index("HIGH")  # commanded
        assert p[0x0539] == m.tier_index("LOW")   # active
        assert p[0x0506] == 16000

    def test_engine_snapshot_names_the_tier(self, tmp_path):
        os.environ.setdefault("SMO_DUMP_DIR", str(tmp_path / "dumps"))
        eng = SimulationEngine(CONFIG_DIR)
        ttc = eng.subsystems["ttc"]
        ttc._state.beacon_mode = False
        ttc._state.antenna_deployed = True
        ttc.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        assert eng._current_tm_tier() == (ttc.tier_index("LOW"), "LOW")
        assert eng._current_tm_rate_bps() == 16000
        assert eng._current_tm_coding() == "CONV_RS"


# ══════════════════════════════════════════════════════════════════════════
# Pacing honours the tier, and the HK backlog drains at NOMINAL
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def engine(tmp_path):
    os.environ.setdefault("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng._in_contact = True
    eng.params[0x0501] = 2
    obdh = eng.subsystems.get("obdh")
    if obdh is not None:
        obdh._state.sw_image = 1
    ttc = eng.subsystems["ttc"]
    ttc._state.beacon_mode = False
    ttc._state.antenna_deployed = True
    eng._force_rt_pacing = True
    eng._rt_pending.clear()
    eng._rt_byte_budget = 0.0
    while not eng.tm_queue.empty():
        eng.tm_queue.get_nowait()
    return eng


class TestPacingHonoursTheTier:

    #: Channel bytes/s the shipped hk_structures.yaml generates: 1351.5 bps of
    #: PUS packets, x1.1435 for RS(255,223), x1115/1107 for the TM transfer
    #: frame = 1557 bps = 194.6 B/s.
    HK_CHANNEL_BPS = 1557.0

    @staticmethod
    def _drain(engine):
        n = 0
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
            n += 1
        return n

    def _run(self, engine, tier_name, seconds=60, pkt=104):
        """Offer HK at the repo's own generation rate for `seconds` seconds
        and return the backlog depth afterwards."""
        ttc = engine.subsystems["ttc"]
        orbit = _orbit(elevation=45.0, range_km=600.0)
        if tier_name == "BEACON":
            ttc._state.beacon_mode = True
        else:
            ttc._state.beacon_mode = False
            assert ttc.handle_command(
                {"command": "set_tm_rate", "rate": tier_name})["success"]
        ttc.tick(1.0, orbit, engine.params)
        offered = 0.0
        for _ in range(seconds):
            offered += self.HK_CHANNEL_BPS / 8.0
            while offered >= pkt:
                engine._enqueue_tm(b'\x08\x01' + b'\x00' * (pkt - 2))
                offered -= pkt
            engine._tick_rt_emission(1.0)
        return len(engine._rt_pending)

    def test_hk_backlog_drains_at_low(self, engine):
        """LOW is the ROUTINE operations rate: 16 kbps is 10.3x the 1557 bps
        HK channel rate, so nothing backs up in normal operations."""
        assert self._run(engine, "LOW") == 0

    def test_hk_backlog_drains_at_high(self, engine):
        """128 kbps is 82x the HK channel rate — dumps never starve HK."""
        engine.subsystems["ttc"]._state.downlink_mode = True
        engine.subsystems["ttc"].tick(
            1.0, _orbit(elevation=45.0, range_km=600.0), engine.params)
        assert self._run(engine, "HIGH") == 0

    def test_hk_backlog_grows_at_beacon(self, engine):
        """1000 bps is BELOW the 1557 bps HK channel rate, so the backlog can
        never clear — which is precisely why 1 kbps is a beacon rate and not
        an operations rate (Franz's complaint, quantified)."""
        short = self._run(engine, "BEACON", seconds=30)
        engine._rt_pending.clear()
        engine._rt_byte_budget = 0.0
        long = self._run(engine, "BEACON", seconds=60)
        assert short > 0, "the backlog must build at the beacon rate"
        assert long > short, "the backlog must GROW, not settle"

    def test_rate_change_changes_what_the_radio_sees(self, engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.downlink_mode = True
        orbit = _orbit(elevation=45.0, range_km=600.0)
        ttc.tick(1.0, orbit, engine.params)
        ttc.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        ttc.tick(1.0, orbit, engine.params)
        for _ in range(60):
            engine._enqueue_tm(b'\x08\x01' + b'\x00' * 102)
        engine._tick_rt_emission(1.0)
        slow = self._drain(engine)

        engine._rt_pending.clear()
        engine._rt_byte_budget = 0.0
        ttc.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        ttc.tick(1.0, orbit, engine.params)
        for _ in range(60):
            engine._enqueue_tm(b'\x08\x01' + b'\x00' * 102)
        engine._tick_rt_emission(1.0)
        fast = self._drain(engine)
        assert fast > slow

    def test_dump_pacer_reads_the_same_tier(self, engine):
        """The S15 dump pacer and the real-time pacer share one rate source,
        so a tier change moves both."""
        ttc = engine.subsystems["ttc"]
        ttc._state.downlink_mode = True
        orbit = _orbit(elevation=45.0, range_km=600.0)
        ttc.tick(1.0, orbit, engine.params)
        ttc.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        ttc.tick(1.0, orbit, engine.params)
        engine._dump_pending = [(b'\x08\x01' + b'\x00' * 254, 1)
                                for _ in range(40)]
        engine._dump_byte_budget = 0.0
        engine._tick_dump_emission(1.0)
        slow_left = len(engine._dump_pending)

        ttc.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        ttc.tick(1.0, orbit, engine.params)
        engine._dump_pending = [(b'\x08\x01' + b'\x00' * 254, 1)
                                for _ in range(40)]
        engine._dump_byte_budget = 0.0
        engine._tick_dump_emission(1.0)
        assert len(engine._dump_pending) < slow_left


# ══════════════════════════════════════════════════════════════════════════
# Catalogue / handler byte agreement (the defect #80 class)
# ══════════════════════════════════════════════════════════════════════════

class TestCatalogueAgreesWithTheHandler:

    @staticmethod
    def _cmd(name):
        cat = yaml.safe_load(
            (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        for c in cat["commands"]:
            if c["name"] == name:
                return c
        raise AssertionError(f"{name} missing from tc_catalog.yaml")

    def test_set_data_rate_is_one_uint8_and_two_data_bytes(self):
        from smo_mcs.tc_lengths import expected_data_bytes
        c = self._cmd("TTC_SET_DATA_RATE")
        assert [f["type"] for f in c["fields"]] == ["uint8"]
        # 1 func_id byte + 1 uint8 field
        assert expected_data_bytes(c)["expected_data_bytes"] == 2

    def test_set_downlink_mode_is_catalogued(self):
        from smo_mcs.tc_lengths import expected_data_bytes
        c = self._cmd("TTC_SET_DOWNLINK_MODE")
        assert c["func_id"] == 79
        assert [f["type"] for f in c["fields"]] == ["uint8"]
        assert expected_data_bytes(c)["expected_data_bytes"] == 2

    def test_catalogue_documents_every_tier_code(self):
        c = self._cmd("TTC_SET_DATA_RATE")
        desc = c["description"] + c["fields"][0]["description"]
        for name in ("LOW", "HIGH", "BEACON"):
            assert name in desc
        assert "16 kbps" in desc and "128 kbps" in desc and "1 kbps" in desc

    def test_catalogue_codes_match_the_ttc_config(self):
        m = _model()
        codes = {t["name"]: t.get("command_code") for t in m.tiers}
        assert codes == {"BEACON": 0xFF, "LOW": 0x00, "HIGH": 0x01}

    def test_dispatcher_decodes_the_catalogued_single_byte(self, engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.downlink_mode = True
        ttc.tick(1.0, _orbit(elevation=45.0, range_km=600.0), engine.params)
        engine._dispatcher._handle_s8(1, bytes([65, 0x01]))
        assert ttc._state.tm_data_rate == 128000
        engine._dispatcher._handle_s8(1, bytes([65, 0x00]))
        assert ttc._state.tm_data_rate == 16000

    def test_dispatcher_rejects_an_ungated_tier_with_an_s1_failure(self, engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.downlink_mode = False
        # WP-7: outcome comes back on the DispatchResult.
        res = engine._dispatcher.dispatch(8, 1, bytes([65, 0x01]), None)  # HIGH
        assert res.failure_kind == "execution", "HIGH should have been refused"
        assert res.error_code == 0x0007
        assert ttc._state.tm_data_rate != 128000

    def test_dispatcher_routes_downlink_mode(self, engine):
        ttc = engine.subsystems["ttc"]
        engine._dispatcher._handle_s8(1, bytes([79, 1]))
        assert ttc._state.downlink_mode is True
        engine._dispatcher._handle_s8(1, bytes([79, 0]))
        assert ttc._state.downlink_mode is False

    def test_bare_tc_defaults_to_low_not_the_top_tier(self, engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.downlink_mode = True
        engine._dispatcher._handle_s8(1, bytes([65]))
        assert ttc._state.tm_data_rate == 16000


# ══════════════════════════════════════════════════════════════════════════
# Modulation and coding follow the tier
# ══════════════════════════════════════════════════════════════════════════

class TestModulationFollowsTheTier:

    def test_modulation_and_coding_track_the_tier(self):
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        for name, mod, coding in (("LOW", 0, "CONV_RS"),
                                  ("HIGH", 1, "RS")):
            assert m.handle_command(
                {"command": "set_tm_rate", "rate": name})["success"]
            assert m._state.modulation_mode == mod
            assert m.active_coding == coding

    def test_bit_rate_is_the_information_rate_not_bits_times_symbols(self):
        """QPSK must not double the reported bit rate."""
        m = _model()
        m._state.antenna_deployed = True
        m._state.downlink_mode = True
        m.tick(1.0, _orbit(elevation=45.0, range_km=600.0), {})
        m.handle_command({"command": "set_tm_rate", "rate": "HIGH"})
        assert m._state.modulation_mode == 1        # QPSK
        assert m._state.tm_data_rate == 128000      # NOT 256000
        assert m.active_symbol_rate_bd == 64000

    def test_symbol_rate_per_tier(self):
        m = _model()
        assert m.tier_symbol_rate(m.tier_index("BEACON")) == 1000
        assert m.tier_symbol_rate(m.tier_index("LOW")) == 16000
        assert m.tier_symbol_rate(m.tier_index("HIGH")) == 64000

    def test_every_tier_fits_the_5_mhz_channel(self):
        """The regulation, not the link, is what bounds the top tier."""
        m = _model()
        bws = [m.tier_occupied_bw_hz(i) for i in range(len(m.tiers))]
        # Research section B.8: 3.1 kHz / 50 kHz / 100 kHz.
        assert bws[0] == pytest.approx(3110, rel=0.02)
        assert bws[1] == pytest.approx(49756, rel=0.02)
        assert bws[2] == pytest.approx(99512, rel=0.02)
        assert max(bws) < CHANNEL_BW_HZ
        # The top tier uses ~2 % of the 5 MHz mission channel.
        assert max(bws) < 0.05 * CHANNEL_BW_HZ


# ══════════════════════════════════════════════════════════════════════════
# Link budget — defect #84 (G/T sign) and the researched margins
# ══════════════════════════════════════════════════════════════════════════

class TestLinkBudget:

    def test_gt_is_added_not_subtracted(self):
        """Defect #84: a better station must produce a better link.

        The old model made Inuvik (22.0 dB/K) 7 dB WORSE than Troll (18.5).
        """
        m = _model()
        tier = m.tier_index("HIGH")
        d5 = _slant_range_km(5.0)
        troll = m.link_margin_for(tier, d5, 5.0, gs_gt_db=18.5)["margin_db"]
        inuvik = m.link_margin_for(tier, d5, 5.0, gs_gt_db=22.0)["margin_db"]
        assert inuvik - troll == pytest.approx(3.5, abs=1e-9)

    def test_stations_rank_by_gt_at_every_elevation(self):
        m = _model()
        tier = m.tier_index("HIGH")
        for el in (5.0, 90.0):
            d = _slant_range_km(el)
            ranked = sorted(
                ((m.link_margin_for(tier, d, el, gs_gt_db=s["g_t_db"])["margin_db"],
                  s["name"], s["g_t_db"]) for s in STATIONS))
            assert [r[2] for r in ranked] == sorted(s["g_t_db"] for s in STATIONS)

    def test_ticked_model_agrees_with_the_closed_form(self):
        """The tick's link budget and `link_margin_for` are the same budget."""
        m = _model()
        m._state.antenna_deployed = True
        m._state.pa_on = True
        p: dict[int, float] = {}
        orbit = _orbit(elevation=45.0, range_km=600.0)
        for _ in range(15):     # let the lock sequence complete
            m.tick(1.0, orbit, p)
        closed = m.link_margin_for(m._state.tm_tier, 600.0, 45.0)
        assert p[0x0503] == pytest.approx(closed["margin_db"], abs=1.5)

    def test_researched_margins_at_5deg_and_zenith(self):
        """Reproduce the research report's arithmetic (sections B.2-B.7).

        Spot values, Iqaluit (G/T 20.0, de-rated 1.5 dB below 10 deg):
          C/N0 at 5 deg  = 13.0 - 165.07 - 2.87 - 0.3 - 2.0 + 18.5 + 228.6
                         = 89.86 dB-Hz
          HIGH margin    = 89.86 - 10log10(128000) - (9.6 - 6.0) = 35.2 dB
          C/N0 at zenith = 13.0 - 152.36 - 0.5 - 0.3 - 2.0 + 20.0 + 228.6
                         = 106.44 dB-Hz
          HIGH margin    = 106.44 - 51.07 - 3.6 = 51.8 dB
        """
        m = _model()
        tier = m.tier_index("HIGH")
        five = m.link_margin_for(tier, _slant_range_km(5.0), 5.0, gs_gt_db=20.0)
        assert five["c_n0_dbhz"] == pytest.approx(89.86, abs=0.05)
        assert five["margin_db"] == pytest.approx(35.2, abs=0.1)
        zen = m.link_margin_for(tier, _slant_range_km(90.0), 90.0, gs_gt_db=20.0)
        assert zen["c_n0_dbhz"] == pytest.approx(106.44, abs=0.05)
        assert zen["margin_db"] == pytest.approx(51.8, abs=0.1)
        # Troll (G/T 18.5) at zenith: report table B.3 gives 104.94 dB-Hz.
        troll = m.link_margin_for(tier, _slant_range_km(90.0), 90.0,
                                  gs_gt_db=18.5)
        assert troll["c_n0_dbhz"] == pytest.approx(104.94, abs=0.05)

    def test_each_tier_step_costs_9_to_12_dB(self):
        """1 / 16 / 128 kbps = +12.04 dB then +9.03 dB of rate.

        BEACON->LOW keeps conv+RS, so the margin step is the full 12.04 dB.
        LOW->HIGH additionally swaps conv(7,1/2)+RS (7.5 dB) for RS only
        (6.0 dB), costing 1.5 dB on top of the 9.03 dB rate step.
        """
        m = _model()
        d = _slant_range_km(5.0)
        margins = [m.link_margin_for(i, d, 5.0, gs_gt_db=20.0)["margin_db"]
                   for i in range(len(m.tiers))]
        assert margins[0] - margins[1] == pytest.approx(12.04, abs=0.05)
        assert margins[1] - margins[2] == pytest.approx(9.03 + 1.5, abs=0.05)

    def test_every_permitted_tier_closes_with_3dB_at_every_station(self):
        """The headline check: permitted tier x station x geometry >= 3 dB."""
        m = _model()
        for el in (5.0, 90.0):
            d = _slant_range_km(el)
            for s in STATIONS:
                for i, t in enumerate(m.tiers):
                    # HIGH is gated to >=10 deg, so it is not "permitted" at 5.
                    if float(t.get("min_elevation_deg", 0.0)) > el:
                        continue
                    lb = m.link_margin_for(i, d, el, gs_gt_db=s["g_t_db"])
                    assert lb["margin_db"] >= 3.0, (
                        f"{t['name']} at {s['name']} / {el} deg only closes "
                        f"with {lb['margin_db']:.1f} dB")

    def test_high_still_closes_at_the_worst_gated_geometry(self):
        """HIGH is only ever used above 10 deg — check it there, at Troll."""
        m = _model()
        lb = m.link_margin_for(m.tier_index("HIGH"), _slant_range_km(10.0),
                               10.0, gs_gt_db=18.5)
        assert lb["margin_db"] == pytest.approx(38.0, abs=0.1)

    def test_one_image_costs_a_meaningful_fraction_of_a_pass(self):
        """The teaching constraint the ladder has to preserve.

        4 MiB / 128 000 bps = 262.1 s. The five-station network gives
        385 contact-min/day over 103 passes per 48 h = 51.5 passes/day, so an
        average pass is 23100 / 51.5 = 448.5 s. One image is therefore 58 % of
        an average pass, and a pass carries about 1.7 images.
        """
        m = _model()
        image_bits = 4 * 1024 * 1024 * 8
        seconds = image_bits / m.tier_bps(m.tier_index("HIGH"))
        assert seconds == pytest.approx(262.14, abs=0.1)
        avg_pass_s = (385 * 60) / (103 / 2)
        assert avg_pass_s == pytest.approx(448.5, abs=0.5)
        assert 0.5 < seconds / avg_pass_s < 0.7

    def test_the_tool_and_the_model_agree(self):
        """tools/link_budget_ladder.py must not drift from the flight model."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "link_budget_ladder",
            Path(__file__).resolve().parents[2] / "tools" / "link_budget_ladder.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.slant_range_km(5.0, 450.0) == pytest.approx(1944.5, abs=0.2)
        assert mod.slant_range_km(90.0, 450.0) == pytest.approx(450.0, abs=0.01)
        assert mod.main([]) == 0
