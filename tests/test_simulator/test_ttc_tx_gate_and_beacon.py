"""The S-band downlink gate and the UHF beacon transmitter (2026-08b).

Two changes are pinned here, and they are one design rather than two:

* **finding T-a** — nothing anywhere in the TM chain read the transmitter
  state. `pa_on` was read in four places, none of them on the path a packet
  takes, and `SimulationEngine.downlink_active` gated on 0x0501 `link_status`,
  which is derived from frame_sync — the UPLINK receive chain. Reproduced on
  the shipped build: `pa_on=False`, `tx_fwd_power=0.0 W`, `link_status=LOCKED`,
  telemetry live.
* **ruling R2** — the fix for T-a alone would silence the beacon too, because
  BEACON was merely rung 0 of the same S-band ladder. Franz's ruling: implement
  the separate UHF beacon transmitter the textbook has always described, and
  let it gate HK SID 11 and nothing else.
"""
from __future__ import annotations

import queue
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def _ttc_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())


def _model(cfg: dict | None = None):
    from smo_simulator.models.ttc_basic import TTCBasicModel
    m = TTCBasicModel()
    m.configure(_ttc_config() if cfg is None else cfg)
    m._state.antenna_deployed = True
    return m


class _Orbit:
    in_eclipse = False
    gs_azimuth_deg = 180.0

    def __init__(self, in_contact=True, elevation=45.0, range_km=800.0):
        self.in_contact = in_contact
        self.gs_elevation_deg = elevation if in_contact else -20.0
        self.gs_range_km = range_km


def _run(model, params, seconds=20, orbit=None):
    orbit = orbit or _Orbit()
    for _ in range(seconds):
        model.tick(1.0, orbit, params)
    return params


# ══════════════════════════════════════════════════════════════════════════
# 1. The S-band gate
# ══════════════════════════════════════════════════════════════════════════

class TestTxActiveGate:

    def test_nominal_chain_is_transmitting(self):
        p = _run(_model(), {0x0112: 1})
        assert p[0x0525] == 1
        # 0x052B: PA, line, contact, radiating, beacon on, beacon radiating,
        # antenna deployed, beacon mode off.
        assert int(p[0x052B]) == 0xFF

    @pytest.mark.parametrize("setup, cleared_bit", [
        ("pa_off", 0x01),
        ("line_off", 0x02),
        ("no_contact", 0x04),
    ])
    def test_each_leg_stops_the_downlink(self, setup, cleared_bit):
        m = _model()
        params = {0x0112: 1}
        orbit = _Orbit()
        if setup == "pa_off":
            m._state.pa_on = False
        elif setup == "line_off":
            params[0x0112] = 0
        else:
            orbit = _Orbit(in_contact=False)
        _run(m, params, orbit=orbit)
        assert params[0x0525] == 0
        assert not int(params[0x052B]) & cleared_bit, (
            "the gate byte must say WHICH leg failed — a console cannot "
            "render 'INHIBITED (PA OFF)' from a bare boolean")

    def test_pa_thermal_shutdown_also_stops_it(self):
        m = _model()
        params = {0x0112: 1}
        _run(m, params, seconds=3)
        m._state.pa_overheat_shutdown = True
        m.tick(1.0, _Orbit(), params)
        assert params[0x0525] == 0

    def test_uplink_lock_is_unaffected_by_the_transmitter(self):
        """The receiver is on its own line. Killing the PA must not touch the
        lock chain — conflating the two is the original defect."""
        m = _model()
        m._state.pa_on = False
        params = _run(m, {0x0112: 0})
        assert params[0x0501] == 2, "LOCKED"
        assert params[0x0512] == 1, "frame sync"
        assert params[0x0525] == 0, "but nothing is being transmitted"


class TestEngineDownlinkGate:

    def _engine(self, params):
        from smo_simulator.engine import SimulationEngine
        e = MagicMock(spec=SimulationEngine)
        e.params = dict(params)
        e._in_contact = True
        e._override_passes = False
        e.tm_queue = queue.Queue(maxsize=100)
        e.tm_queue_drops = 0
        e.tm_packets_enqueued = 0
        type(e).downlink_active = SimulationEngine.downlink_active
        type(e).uplink_active = SimulationEngine.uplink_active
        return e

    def test_fails_closed_before_the_first_tick(self):
        """The old default was `True` — a spacecraft with no transmitter at
        all counted as transmitting."""
        assert self._engine({}).downlink_active is False
        assert self._engine({}).uplink_active is False

    def test_locked_link_does_not_open_the_downlink_on_its_own(self):
        """The exact reproduction of Franz's report."""
        e = self._engine({0x0501: 2, 0x0525: 0, 0x052B: 0x00})
        assert e.downlink_active is False

    def test_tx_active_opens_it(self):
        assert self._engine({0x0525: 1}).downlink_active is True

    def test_acquiring_is_not_a_lock_for_the_uplink(self):
        """`bool(link_status)` accepted ACQUIRING(1) — carrier lock with no
        frame sync — as permission to dispatch telecommands."""
        assert self._engine({0x0501: 1}).uplink_active is False
        assert self._engine({0x0501: 2}).uplink_active is True

    def test_override_substitutes_for_contact_but_never_for_the_pa(self):
        e = self._engine({0x0525: 0, 0x052B: 0x03})   # PA on, line on
        e._override_passes = True
        assert e.downlink_active is True
        e.params[0x052B] = 0x02                        # PA off, line on
        assert e.downlink_active is False


# ══════════════════════════════════════════════════════════════════════════
# 2. EIRP derived from the commanded PA power (finding T-b)
# ══════════════════════════════════════════════════════════════════════════

class TestEirpFollowsTheCommandedPower:

    def test_levels_move_the_eirp_by_the_power_ratio(self):
        m = _model()
        ref = m._effective_eirp_dbw()
        m.handle_command({"command": "set_tx_power", "level": 0})   # 1.0 W
        assert m._effective_eirp_dbw() == pytest.approx(ref - 3.01, abs=0.02)
        m.handle_command({"command": "set_tx_power", "level": 1})   # 2.0 W
        assert m._effective_eirp_dbw() == pytest.approx(ref, abs=1e-9)
        m.handle_command({"command": "set_tx_power", "level": 2})   # 5.0 W
        assert m._effective_eirp_dbw() == pytest.approx(ref + 3.98, abs=0.02)

    def test_the_margin_moves_with_it(self):
        """The defect: TTC_SET_TX_POWER changed telemetry 0x050D and nothing
        else, so FDIR's 'reduce TX power' recovery was a no-op on the link."""
        m = _model()
        nominal = m.link_margin_for(1, 800.0, 45.0)["margin_db"]
        m.handle_command({"command": "set_tx_power", "level": 0})
        low = m.link_margin_for(1, 800.0, 45.0)["margin_db"]
        assert nominal - low == pytest.approx(3.01, abs=0.02)

    def test_the_reference_is_the_researched_operating_point(self):
        """2 W RF = 33 dBm, the top of the EnduroSat S-band transceiver's
        published range and the point POWER_SIZING.md sized the 11 W DC
        ttc_tx line against."""
        assert _ttc_config()["pa_ref_power_w"] == 2.0

    def test_it_is_published(self):
        m = _model()
        params = {0x0112: 1}
        _run(m, params, seconds=3)
        assert params[0x052C] == pytest.approx(m._effective_eirp_dbw(), abs=0.01)


# ══════════════════════════════════════════════════════════════════════════
# 3. The UHF beacon transmitter (ruling R2)
# ══════════════════════════════════════════════════════════════════════════

class TestUhfBeaconTransmitter:

    def test_it_is_a_separate_radio_on_a_separate_band(self):
        b = _ttc_config()["beacon"]
        lo, hi = b["band_mhz"]
        assert lo <= b["dl_freq_mhz"] <= hi
        assert 435.0 <= b["dl_freq_mhz"] <= 438.0, "amateur-satellite segment"
        # ...and nowhere near the S-band plan.
        assert b["dl_freq_mhz"] < 1000.0 < _ttc_config()["dl_freq_mhz"]

    def test_its_budget_is_its_own(self):
        """The teaching point: UHF wins path loss and gives back G/T."""
        m = _model()
        s_band = m.link_margin_for(1, 1944.5, 5.0)      # LOW rung, 5 deg
        beacon = m.beacon_link_budget(1944.5, 5.0)
        # 20*log10(2200/437.5) = 14.03 dB of free-space path loss saved.
        assert s_band["fspl_db"] - beacon["fspl_db"] == pytest.approx(14.03,
                                                                     abs=0.05)
        # ...and 28 dB of ground-station G/T given back.
        assert s_band["gt_eff_db"] - beacon["gt_eff_db"] == pytest.approx(28.0,
                                                                          abs=0.1)
        # Both still close, by very different routes.
        assert beacon["margin_db"] > 10.0
        assert s_band["margin_db"] > beacon["margin_db"]

    def test_it_survives_the_s_band_pa(self):
        """THE ruling. PA off must not silence the lifeline."""
        m = _model()
        m._state.pa_on = False
        params = _run(m, {0x0112: 0})
        assert params[0x0525] == 0, "S-band dead"
        assert params[0x0527] == 1, "UHF beacon alive"
        assert params[0x0528] > 0.0, "and closing its own budget"
        assert params[0x052A] == 1.0, "1 W RF"

    def test_it_can_be_commanded_off(self):
        m = _model()
        m._state.pa_on = False
        assert m.handle_command({"command": "beacon_tx_off"})["success"]
        params = _run(m, {0x0112: 0})
        assert params[0x0526] == 0 and params[0x0527] == 0
        assert m.handle_command({"command": "beacon_tx_on"})["success"]
        params = _run(m, {0x0112: 0})
        assert params[0x0527] == 1

    def test_it_needs_a_station_in_view(self):
        m = _model()
        params = _run(m, {0x0112: 1}, orbit=_Orbit(in_contact=False))
        assert params[0x0526] == 1, "still commanded on"
        assert params[0x0527] == 0, "but radiating at nobody"

    def test_beacon_mode_is_not_the_beacon_transmitter(self):
        """The distinction ruling R2 turns on: `beacon_mode` (0x0521) selects
        a rung of the S-BAND rate ladder; `beacon_tx_on` (0x0526) switches a
        different radio. Neither implies the other."""
        m = _model()
        m.handle_command({"command": "set_beacon_mode", "on": True})
        params = _run(m, {0x0112: 1})
        assert params[0x0521] == 1, "S-band ladder pinned to the BEACON rung"
        assert params[0x0506] == 1000, "...at 1 kbps"
        assert params[0x0526] == 1, "UHF beacon transmitter untouched"

        m2 = _model()
        m2.handle_command({"command": "beacon_tx_off"})
        params2 = _run(m2, {0x0112: 1})
        assert params2[0x0521] == 0, "beacon MODE not entered"
        assert params2[0x0506] == 16000, "S-band still at the LOW rung"


class TestBeaconCarriesSid11Only:
    """The beacon emits HK SID 11 when the S-band chain is down — and only
    then, so nothing is duplicated while the prime chain is up."""

    def _model_with_engine(self):
        m = _model()
        engine = MagicMock()
        engine._hk_intervals = {11: 30.0, 6: 8.0}
        engine._hk_enabled = {11: True, 6: True}
        engine._hk_structures = {11: [], 6: []}
        engine.tm_queue = queue.Queue(maxsize=100)
        engine.tm_queue_drops = 0
        engine.tm_packets_enqueued = 0
        engine.tm_builder.build_hk_packet = MagicMock(return_value=b"BEACON" * 4)
        m._engine = engine
        return m, engine

    def test_beacon_emits_when_the_s_band_chain_is_dead(self):
        m, engine = self._model_with_engine()
        m._state.pa_on = False
        _run(m, {0x0112: 0}, seconds=91)          # three 30 s intervals
        assert engine.tm_queue.qsize() == 3
        sids = [c.args[0] for c in engine.tm_builder.build_hk_packet.call_args_list]
        assert set(sids) == {11}, "SID 11 and nothing else"

    def test_beacon_stays_quiet_while_the_s_band_chain_is_up(self):
        """No double emission: the OBC's own HK scheduler owns SID 11 then."""
        m, engine = self._model_with_engine()
        _run(m, {0x0112: 1}, seconds=91)
        assert engine.tm_queue.qsize() == 0

    def test_disabling_sid_11_silences_the_content_not_the_radio(self):
        m, engine = self._model_with_engine()
        engine._hk_enabled[11] = False
        m._state.pa_on = False
        params = _run(m, {0x0112: 0}, seconds=91)
        assert engine.tm_queue.qsize() == 0
        assert params[0x0527] == 1, "the transmitter is still keyed"


# ══════════════════════════════════════════════════════════════════════════
# 4. Beacon-mode exit and safe-mode entry (findings 3 and 4)
# ══════════════════════════════════════════════════════════════════════════

class TestBeaconModeExitResetsTheCommandedTier:

    def test_exit_lands_on_the_safe_tier(self):
        """Exit used to set one flag and leave `data_rate_mode` pinned at
        BEACON, so the console reported commanded=BEACON / active=LOW forever
        and the demotion display could never clear."""
        m = _model()
        m.handle_command({"command": "set_beacon_mode", "on": True})
        assert m._state.data_rate_mode == m._beacon_tier
        m.handle_command({"command": "set_beacon_mode", "on": False})
        assert m._state.data_rate_mode == m._safe_tier
        params = _run(m, {0x0112: 1}, seconds=2)
        assert params[0x0539] == params[0x053A], (
            "active and commanded tier must agree once the gate has cleared")

    def test_exit_when_never_in_beacon_mode_changes_nothing(self):
        m = _model()
        m.handle_command({"command": "set_tm_rate", "rate": "LOW"})
        m._state.downlink_mode = True
        m._state.data_rate_mode = 2                   # HIGH
        m.handle_command({"command": "set_beacon_mode", "on": False})
        assert m._state.data_rate_mode == 2


class TestApplicationBootLiftsTheSbandInhibit:
    """`_enter_bootloader_mode` sets `pa_on = False` and nothing performed the
    reverse (2026-08b, finding 3). Invisible while nothing read `pa_on`;
    "every commissioning sequence boots into silence" once it is the gate."""

    def test_the_boot_edge_re_enables_the_pa(self):
        m = _model()
        m._state.pa_on = False
        params = {0x0112: 1, 0x0311: 0}          # bootloader
        _run(m, params, seconds=3)
        assert m._state.pa_on is False, "the bootloader keeps the S-band dark"
        assert params[0x0525] == 0
        assert params[0x0527] == 1, "the beacon is the bootloader's downlink"

        params[0x0311] = 1                        # OBC_BOOT_APP
        m.tick(1.0, _Orbit(), params)
        assert m._state.pa_on is True
        assert params[0x0525] == 1

    def test_it_is_an_edge_not_a_re_arm(self):
        """An operator who sends TTC_PA_DISABLE after boot keeps it off."""
        m = _model()
        params = {0x0112: 1, 0x0311: 1}
        _run(m, params, seconds=3)
        m.handle_command({"command": "pa_off"})
        _run(m, params, seconds=3)
        assert m._state.pa_on is False
        assert params[0x0525] == 0

    def test_a_thermal_shutdown_is_never_overridden(self):
        m = _model()
        params = {0x0112: 1, 0x0311: 0}
        _run(m, params, seconds=2)
        m._state.pa_on = False
        m._state.pa_overheat_shutdown = True
        params[0x0311] = 1
        m.tick(1.0, _Orbit(), params)
        assert m._state.pa_on is False


class TestSafeModeCommandsTheSafeTier:

    def _model_in_mode(self, mode):
        m = _model()
        engine = MagicMock()
        engine.sc_mode = mode
        engine._hk_intervals = {11: 30.0}
        engine._hk_enabled = {11: True}
        engine._hk_structures = {11: []}
        m._engine = engine
        return m, engine

    def test_entry_commands_the_safe_tier(self):
        m, engine = self._model_in_mode(1)             # NOMINAL
        m._state.downlink_mode = True
        m._state.data_rate_mode = 2                    # HIGH, mid-dump
        m.tick(1.0, _Orbit(), {0x0112: 1})
        assert m._state.data_rate_mode == 2

        engine.sc_mode = 0                             # SC_SAFE
        m.tick(1.0, _Orbit(), {0x0112: 1})
        assert m._state.data_rate_mode == m._safe_tier

    def test_it_is_edge_triggered_not_latched(self):
        """An operator may still choose a tier while safe mode persists."""
        m, engine = self._model_in_mode(1)
        engine.sc_mode = 0
        m.tick(1.0, _Orbit(), {0x0112: 1})
        m._state.downlink_mode = True
        m._state.data_rate_mode = 2
        m.tick(1.0, _Orbit(), {0x0112: 1})
        assert m._state.data_rate_mode == 2

    def test_a_run_that_starts_in_safe_mode_is_not_disturbed(self):
        """Every run starts SAFE (uncommissioned), and the bootloader owns the
        tier then — the first tick must adopt, not command."""
        m, _ = self._model_in_mode(0)
        m._state.beacon_mode = True
        m._state.data_rate_mode = m._beacon_tier
        m.tick(1.0, _Orbit(), {0x0112: 1})
        assert m._state.data_rate_mode == m._beacon_tier
