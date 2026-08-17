"""Part B — the onboard clock model (docs/plan_2026-08c/TIME_ARCHITECTURE.md).

The OBC has its OWN clock. It is not an affine function of sim time: it starts
at J2000 (CUC 0) on power-up and after every bootloader reset, free-runs with a
per-unit oscillator drift drawn once per run from the seeded per-model RNG, and
is held exactly to true time only while GPS-disciplined.
"""
import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from smo_common.protocol.ecss_packet import (
    datetime_to_cuc_seconds, get_time_epoch,
)
from smo_common.state import derive_rng
from smo_simulator.models.obdh_basic import (
    OBDHBasicModel, SW_APPLICATION, SW_BOOTLOADER,
    TIME_REF_NONE, TIME_REF_GROUND, TIME_REF_GPS, TIME_REF_HOLDOVER,
    TIME_SINCE_SYNC_NEVER, GPS_FIX_PARAM, OBC_POWER_LINE_PARAM,
    EV_OBC_TIME_SYNC, EV_OBC_TIME_REF_LOST,
)

# The four config keys the spec declares, at their shipped values.
CLOCK_CFG = {
    "clock_drift_ppm_nominal": 12.0,
    "clock_drift_sigma_ppm": 4.0,
    "clock_gps_discipline": True,
    "clock_jump_event_threshold_s": 1.0,
}


class FakeEngine:
    """Just enough engine for the model: a sim clock and an event queue."""

    def __init__(self, sim_time: datetime | None = None):
        self._sim_time = sim_time or (get_time_epoch() + timedelta(days=9000))
        self.events: list[dict] = []
        self._model_event_queue = MagicMock()
        self._model_event_queue.put_nowait = self.events.append

    def advance(self, dt: float) -> None:
        self._sim_time = self._sim_time + timedelta(seconds=dt)


def make_model(cfg: dict | None = None, engine: FakeEngine | None = None,
               seed: int = 1234, booted: bool = True) -> OBDHBasicModel:
    m = OBDHBasicModel()
    m.rng = derive_rng(seed, "model:obdh")
    m.configure({**CLOCK_CFG, **(cfg or {})})
    m._engine = engine if engine is not None else FakeEngine()
    if booted:
        m._state.sw_image = SW_APPLICATION
    return m


def run(model: OBDHBasicModel, n: int, dt: float,
        params: dict | None = None) -> dict:
    # Mirrors the engine: sim time is advanced first, then the models tick
    # (engine._tick / _tick_substep).
    params = params if params is not None else {}
    for _ in range(n):
        eng = getattr(model, "_engine", None)
        if isinstance(eng, FakeEngine):
            eng.advance(dt)
        model.tick(dt, None, params)
    return params


# ── 1. Start-up state ────────────────────────────────────────────────

def test_clock_starts_at_j2000_with_no_reference():
    """First start-up: CUC 0, no time reference, never synced."""
    m = make_model()
    assert m.get_obc_time_s() == 0.0
    assert m._state.time_ref == TIME_REF_NONE
    assert m._state.last_sync_s == -1.0
    assert m.time_since_sync_s() is None


def test_obc_time_cuc_is_gone():
    """The old int field must not survive — three epochs were written to it."""
    assert not hasattr(m := OBDHBasicModel()._state, "obc_time_cuc"), m


# ── 2. Free-running drift ────────────────────────────────────────────

def test_drift_rate_is_what_the_config_says():
    """Over 1000 s the clock gains exactly drift_ppm * 1e-6 * 1000 s."""
    m = make_model({"clock_drift_ppm_nominal": 100.0,
                    "clock_drift_sigma_ppm": 0.0})
    run(m, 1000, 1.0)
    assert m._state.clock_drift_ppm == pytest.approx(100.0)
    # 100 ppm over 1000 s = 0.1 s gained.
    assert m.get_obc_time_s() == pytest.approx(1000.0 + 0.1, abs=1e-9)


def test_zero_drift_config_gives_a_perfect_clock():
    m = make_model({"clock_drift_ppm_nominal": 0.0,
                    "clock_drift_sigma_ppm": 0.0})
    run(m, 100, 0.5)
    assert m.get_obc_time_s() == pytest.approx(50.0, abs=1e-9)


def test_substep_truncation_bug_is_gone():
    """speed=2.5 at 1 Hz gives three 0.8333 s sub-steps; `int(dt)` froze the
    clock (and uptime) completely because int(0.8333) == 0."""
    m = make_model({"clock_drift_ppm_nominal": 0.0,
                    "clock_drift_sigma_ppm": 0.0})
    dt = 2.5 / 3.0
    run(m, 3, dt)
    assert m.get_obc_time_s() == pytest.approx(2.5, abs=1e-9)
    assert m._state.uptime_s == 2
    run(m, 3, dt)
    assert m.get_obc_time_s() == pytest.approx(5.0, abs=1e-9)
    assert m._state.uptime_s == 5


def test_drift_drawn_once_per_run_not_per_tick():
    m = make_model()
    run(m, 1, 1.0)
    drawn = m._state.clock_drift_ppm
    run(m, 50, 1.0)
    assert m._state.clock_drift_ppm == drawn


def test_drift_is_deterministic_for_a_given_seed():
    """Same seed ⇒ same drawn drift. Different seed ⇒ (almost surely) not."""
    a = make_model(seed=7)
    b = make_model(seed=7)
    c = make_model(seed=8)
    for m in (a, b, c):
        run(m, 1, 1.0)
    assert a._state.clock_drift_ppm == b._state.clock_drift_ppm
    assert a._state.clock_drift_ppm != c._state.clock_drift_ppm
    # And it is drawn from the configured distribution, not hardcoded.
    assert abs(a._state.clock_drift_ppm - 12.0) < 5 * 4.0


def test_drift_survives_reboot_but_the_clock_does_not():
    """clock_drift_ppm is the crystal, not state: a reboot must not redraw it."""
    m = make_model()
    run(m, 10, 1.0)
    drift = m._state.clock_drift_ppm
    m.handle_command({"command": "obc_reboot"})
    assert m._state.clock_drift_ppm == drift


# ── 3. Ground set (S9,1) ─────────────────────────────────────────────

def test_set_time_sets_ref_ground_and_reports_signed_jump():
    m = make_model()
    run(m, 100, 1.0)
    before = m.get_obc_time_s()
    res = m.handle_command({"command": "set_time", "cuc_seconds": 500.0})
    assert res["success"]
    assert m.get_obc_time_s() == pytest.approx(500.0)
    assert m._state.time_ref == TIME_REF_GROUND
    assert m._state.last_sync_s == pytest.approx(500.0)
    assert m.time_since_sync_s() == pytest.approx(0.0)
    assert res["time_jump_s"] == pytest.approx(500.0 - before)


def test_set_time_backwards_gives_a_negative_jump():
    m = make_model()
    m.handle_command({"command": "set_time", "cuc_seconds": 1000.0})
    res = m.handle_command({"command": "set_time", "cuc_seconds": 400.0})
    assert res["time_jump_s"] == pytest.approx(-600.0)


def test_set_time_is_refused_in_the_bootloader():
    m = make_model(booted=False)
    assert m._state.sw_image == SW_BOOTLOADER
    res = m.handle_command({"command": "set_time", "cuc_seconds": 500.0})
    assert not res["success"]
    assert m.get_obc_time_s() == 0.0


def test_ground_set_clock_keeps_drifting():
    m = make_model({"clock_drift_ppm_nominal": 100.0,
                    "clock_drift_sigma_ppm": 0.0})
    m.handle_command({"command": "set_time", "cuc_seconds": 1000.0})
    run(m, 1000, 1.0)
    assert m._state.time_ref == TIME_REF_GROUND
    assert m.get_obc_time_s() == pytest.approx(2000.1, abs=1e-6)


# ── 4. GPS discipline ────────────────────────────────────────────────

def test_gps_sync_uses_the_j2000_epoch_not_unix():
    """The old code wrote int(sim_time.timestamp()) — a Unix timestamp — into
    a J2000 field, a ~9.5e8 s jump on every sync."""
    eng = FakeEngine()
    m = make_model(engine=eng)
    run(m, 60, 1.0)
    expected = datetime_to_cuc_seconds(eng._sim_time)
    res = m.handle_command({"command": "gps_time_sync",
                            "shared_params": {GPS_FIX_PARAM: 3.0},
                            "sim_time": eng._sim_time})
    assert res["success"]
    assert m.get_obc_time_s() == pytest.approx(expected)
    # Sanity: a Unix timestamp would be ~9.46e8 s larger.
    assert abs(m.get_obc_time_s() - eng._sim_time.timestamp()) > 9e8
    assert m._state.time_ref == TIME_REF_GPS


def test_gps_sync_jump_is_small_when_the_clock_is_nearly_right():
    """With the epoch bug fixed, a routine sync no longer trips the 5 s guard."""
    eng = FakeEngine()
    m = make_model(engine=eng)
    m.handle_command({"command": "set_time",
                      "cuc_seconds": datetime_to_cuc_seconds(eng._sim_time)})
    run(m, 100, 1.0)
    res = m.handle_command({"command": "gps_time_sync",
                            "shared_params": {GPS_FIX_PARAM: 3.0},
                            "sim_time": eng._sim_time})
    assert abs(res["time_jump_s"]) < 5.0


def test_gps_sync_requires_a_3d_fix():
    eng = FakeEngine()
    m = make_model(engine=eng)
    res = m.handle_command({"command": "gps_time_sync",
                            "shared_params": {GPS_FIX_PARAM: 1.0},
                            "sim_time": eng._sim_time})
    assert not res["success"]
    assert m._state.time_ref == TIME_REF_NONE


def test_gps_discipline_holds_the_clock_exactly():
    """No drift accumulates while disciplined, whatever the ppm."""
    eng = FakeEngine()
    m = make_model({"clock_drift_ppm_nominal": 500.0,
                    "clock_drift_sigma_ppm": 0.0}, engine=eng)
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": {GPS_FIX_PARAM: 3.0},
                      "sim_time": eng._sim_time})
    run(m, 2000, 1.0, {GPS_FIX_PARAM: 3.0})
    assert m.get_obc_time_s() == pytest.approx(
        datetime_to_cuc_seconds(eng._sim_time), abs=1e-6)
    assert m._state.time_ref == TIME_REF_GPS
    # Continuously disciplined ⇒ the sync is never stale.
    assert m.time_since_sync_s() == pytest.approx(0.0)


def test_losing_the_fix_goes_to_holdover_and_resumes_drift_from_there():
    eng = FakeEngine()
    m = make_model({"clock_drift_ppm_nominal": 1000.0,
                    "clock_drift_sigma_ppm": 0.0}, engine=eng)
    params = {GPS_FIX_PARAM: 3.0}
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": params, "sim_time": eng._sim_time})
    run(m, 100, 1.0, params)
    at_loss = m.get_obc_time_s()

    params[GPS_FIX_PARAM] = 0.0
    run(m, 1, 1.0, params)          # the tick that notices the loss
    assert m._state.time_ref == TIME_REF_HOLDOVER
    assert [e["event_id"] for e in eng.events].count(EV_OBC_TIME_REF_LOST) == 1

    run(m, 999, 1.0, params)
    # Drift resumed FROM where it was: 1000 s elapsed at 1000 ppm ⇒ +1 s.
    # (abs=1e-3: obc_time_s is ~7.8e8 here, so float64 resolution is ~1e-7
    # per operation and 1000 accumulations cannot be compared to 1e-6.)
    assert m.get_obc_time_s() == pytest.approx(at_loss + 1000.0 + 1.0, abs=1e-3)
    # ... and the sync is now visibly ageing.
    assert m.time_since_sync_s() == pytest.approx(1001.0, abs=1e-3)


def test_regaining_the_fix_re_disciplines_automatically():
    eng = FakeEngine()
    m = make_model({"clock_drift_ppm_nominal": 100000.0,
                    "clock_drift_sigma_ppm": 0.0}, engine=eng)
    params = {GPS_FIX_PARAM: 3.0}
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": params, "sim_time": eng._sim_time})
    params[GPS_FIX_PARAM] = 0.0
    run(m, 200, 1.0, params)
    assert m._state.time_ref == TIME_REF_HOLDOVER
    drifted = m.get_obc_time_s()

    params[GPS_FIX_PARAM] = 3.0
    run(m, 1, 1.0, params)
    assert m._state.time_ref == TIME_REF_GPS
    assert m.get_obc_time_s() == pytest.approx(
        datetime_to_cuc_seconds(eng._sim_time), abs=1e-6)
    sync_events = [e for e in eng.events if e["event_id"] == EV_OBC_TIME_SYNC]
    assert len(sync_events) == 1
    # The event carries the SIGNED jump, not just "time set".
    assert "jump -" in sync_events[0]["description"]
    assert drifted > m.get_obc_time_s()


def test_gps_discipline_can_be_disabled_by_config():
    eng = FakeEngine()
    m = make_model({"clock_gps_discipline": False,
                    "clock_drift_ppm_nominal": 100.0,
                    "clock_drift_sigma_ppm": 0.0}, engine=eng)
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": {GPS_FIX_PARAM: 3.0},
                      "sim_time": eng._sim_time})
    assert m._state.time_ref == TIME_REF_GROUND
    run(m, 1000, 1.0, {GPS_FIX_PARAM: 3.0})
    # Free-ran the whole way: 100 ppm over 1000 s.
    assert m.get_obc_time_s() - datetime_to_cuc_seconds(eng._sim_time) \
        == pytest.approx(0.1, abs=1e-3)


# ── 5. Reboot and power ──────────────────────────────────────────────

def test_reboot_returns_the_clock_to_j2000():
    eng = FakeEngine()
    m = make_model(engine=eng)
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": {GPS_FIX_PARAM: 3.0},
                      "sim_time": eng._sim_time})
    assert m.get_obc_time_s() > 0
    m.handle_command({"command": "obc_reboot"})
    assert m.get_obc_time_s() == 0.0
    assert m._state.time_ref == TIME_REF_NONE
    assert m._state.last_sync_s == -1.0
    assert m._state.uptime_s == 0


def test_boot_app_does_not_restore_the_clock():
    """LEOP flow: boot the app, THEN run the time sync."""
    eng = FakeEngine()
    m = make_model(engine=eng)
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": {GPS_FIX_PARAM: 3.0},
                      "sim_time": eng._sim_time})
    m.handle_command({"command": "obc_reboot"})
    m.handle_command({"command": "obc_boot_app"})
    run(m, 11, 1.0)
    assert m._state.sw_image == SW_APPLICATION
    assert m._state.time_ref == TIME_REF_NONE
    assert m.get_obc_time_s() < 12.0      # ~11 s of free-run since J2000
    assert m.time_since_sync_s() is None


def test_watchdog_reboot_also_resets_the_clock():
    eng = FakeEngine()
    m = make_model(engine=eng)
    m.handle_command({"command": "set_time", "cuc_seconds": 5000.0})
    m.inject_failure("obc_crash")
    assert m.get_obc_time_s() == 0.0
    assert m._state.time_ref == TIME_REF_NONE


def test_power_cycling_the_obc_line_resets_the_clock():
    eng = FakeEngine()
    m = make_model(engine=eng)
    params = {OBC_POWER_LINE_PARAM: 1.0}
    m.handle_command({"command": "set_time", "cuc_seconds": 5000.0})
    run(m, 5, 1.0, params)
    assert m.get_obc_time_s() > 5000.0

    params[OBC_POWER_LINE_PARAM] = 0.0
    run(m, 10, 1.0, params)
    frozen = m.get_obc_time_s()          # unpowered: the clock is dead
    assert frozen == pytest.approx(m.get_obc_time_s())

    params[OBC_POWER_LINE_PARAM] = 1.0
    run(m, 1, 1.0, params)
    assert m._state.time_ref == TIME_REF_NONE
    assert m.get_obc_time_s() < 2.0
    assert m._state.sw_image == SW_BOOTLOADER


# ── 6. Telemetry ─────────────────────────────────────────────────────

def test_clock_telemetry_ids_and_values():
    m = make_model({"clock_drift_ppm_nominal": 0.0,
                    "clock_drift_sigma_ppm": 0.0})
    params = run(m, 1, 1.5)
    assert params[0x0309] == 1                       # coarse seconds
    assert params[0x0A12] == pytest.approx(0.5 * 65536, abs=1)  # fine
    assert params[0x0A13] == TIME_REF_NONE
    assert params[0x0A14] == TIME_SINCE_SYNC_NEVER   # never synced


def test_time_since_sync_saturates_rather_than_wrapping():
    m = make_model({"clock_drift_ppm_nominal": 0.0,
                    "clock_drift_sigma_ppm": 0.0})
    m.handle_command({"command": "set_time", "cuc_seconds": 0.0})
    params = {}
    m.tick(1.0, None, params)
    assert params[0x0A14] == 1
    # A clock that has been running for longer than the field can hold must
    # peg at 0xFFFFFFFE, leaving 0xFFFFFFFF to mean "never".
    m._state.obc_time_s = 1e12
    m.tick(0.0, None, params)
    assert params[0x0A14] == TIME_SINCE_SYNC_NEVER - 1


def test_telemetry_reflects_the_time_reference():
    eng = FakeEngine()
    m = make_model(engine=eng)
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": {GPS_FIX_PARAM: 3.0},
                      "sim_time": eng._sim_time})
    params = run(m, 1, 1.0, {GPS_FIX_PARAM: 3.0})
    assert params[0x0A13] == TIME_REF_GPS
    assert params[0x0A14] == 0


def test_param_ids_are_configurable():
    m = OBDHBasicModel()
    m.configure({"param_ids": {"obc_time": 0x0309, "obc_time_fine": 0x0A12,
                               "time_ref": 0x0A13, "time_since_sync": 0x0A14}})
    m._state.sw_image = SW_APPLICATION
    params = {}
    m.tick(1.0, None, params)
    for pid in (0x0309, 0x0A12, 0x0A13, 0x0A14):
        assert pid in params


# ── 7. clock_drift failure injection ─────────────────────────────────

def test_clock_drift_injection_forces_a_large_ppm():
    m = make_model()
    run(m, 1, 1.0)
    nominal = m._state.clock_drift_ppm
    m.inject_failure("clock_drift")
    assert m._state.clock_drift_ppm == pytest.approx(500.0)
    assert m._state.clock_drift_ppm > 10 * nominal

    run(m, 1000, 1.0)
    # 500 ppm over 1000 s = 0.5 s of error, visible inside a session.
    assert m.get_obc_time_s() == pytest.approx(1001.0 + 0.5, abs=1e-3)


def test_clock_drift_injection_honours_magnitude_and_explicit_ppm():
    m = make_model()
    m.inject_failure("clock_drift", magnitude=0.5)
    assert m._state.clock_drift_ppm == pytest.approx(250.0)
    m.clear_failure("clock_drift")
    m.inject_failure("clock_drift", ppm=1234.0)
    assert m._state.clock_drift_ppm == pytest.approx(1234.0)


def test_clock_drift_failure_ppm_is_configurable():
    m = make_model({"clock_drift_failure_ppm": 900.0})
    m.inject_failure("clock_drift")
    assert m._state.clock_drift_ppm == pytest.approx(900.0)


def test_clearing_clock_drift_restores_the_drawn_value():
    m = make_model()
    run(m, 1, 1.0)
    nominal = m._state.clock_drift_ppm
    m.inject_failure("clock_drift")
    m.clear_failure("clock_drift")
    assert m._state.clock_drift_ppm == pytest.approx(nominal)


# ── 8. Breakpoint round trip ─────────────────────────────────────────

def test_clock_state_round_trips_through_a_breakpoint():
    eng = FakeEngine()
    m = make_model(engine=eng)
    params = {GPS_FIX_PARAM: 3.0}
    m.handle_command({"command": "gps_time_sync",
                      "shared_params": params, "sim_time": eng._sim_time})
    params[GPS_FIX_PARAM] = 0.0
    run(m, 300, 1.0, params)
    assert m._state.time_ref == TIME_REF_HOLDOVER

    snapshot = copy.deepcopy(m.get_state())
    for key in ("obc_time_s", "time_ref", "last_sync_s", "clock_drift_ppm"):
        assert key in snapshot, f"{key} missing from the breakpoint"

    restored = make_model(engine=FakeEngine())
    restored.set_state(copy.deepcopy(snapshot))
    assert restored.get_obc_time_s() == m.get_obc_time_s()
    assert restored._state.time_ref == m._state.time_ref
    assert restored._state.last_sync_s == m._state.last_sync_s
    assert restored._state.clock_drift_ppm == m._state.clock_drift_ppm
    assert restored._state.uptime_s == m._state.uptime_s
    assert restored._uptime_acc == pytest.approx(m._uptime_acc)


def test_injected_clock_drift_does_not_heal_on_restore():
    """The gyro_bias bug of the last batch: persistent failure state that
    silently healed on snapshot restore."""
    m = make_model()
    run(m, 1, 1.0)
    m.inject_failure("clock_drift")
    snapshot = copy.deepcopy(m.get_state())

    restored = make_model(seed=99)      # a different unit, different draw
    restored.set_state(copy.deepcopy(snapshot))
    assert restored._state.clock_drift_ppm == pytest.approx(500.0)
    run(restored, 1000, 1.0)
    assert restored.get_obc_time_s() == pytest.approx(
        m.get_obc_time_s() + 1000.5, abs=1e-3)
    # And clearing it afterwards still restores the ORIGINAL drawn drift,
    # not whatever this unit would have drawn for itself.
    restored.clear_failure("clock_drift")
    assert restored._state.clock_drift_ppm == pytest.approx(
        m._clock_drift_saved)


def test_restore_does_not_redraw_the_drift():
    m = make_model(seed=1)
    run(m, 1, 1.0)
    snapshot = copy.deepcopy(m.get_state())
    restored = make_model(seed=2)
    restored.set_state(copy.deepcopy(snapshot))
    assert restored._clock_drift_drawn is True
    run(restored, 5, 1.0)
    assert restored._state.clock_drift_ppm == m._state.clock_drift_ppm


def test_set_state_does_not_mutate_the_snapshot():
    m = make_model()
    run(m, 10, 1.0)
    snapshot = m.get_state()
    before = copy.deepcopy(snapshot)
    make_model().set_state(snapshot)
    assert snapshot == before


# ── 9. Determinism end to end ────────────────────────────────────────

def test_same_seed_gives_an_identical_clock_history():
    def history(seed):
        m = make_model(seed=seed)
        out = []
        for _ in range(200):
            m.tick(0.7, None, {})
            out.append(m.get_obc_time_s())
        return out

    assert history(4242) == history(4242)
    assert history(4242) != history(4243)


# ── 10. Dispatcher paths, against a real engine ──────────────────────
#
# These exercise the S9,1 / S9,2 / S8 func 80 handlers and the time-jump
# guard in service_dispatch.py, which is where the Unix/J2000 epoch bug lived.

import struct

from smo_common.protocol.ecss_packet import build_tc_packet
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = "configs/eosat1"


@pytest.fixture
def engine():
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6            # nominal ops (out of the bootloader)
    eng._override_passes = True
    eng.subsystems["obdh"]._state.sw_image = SW_APPLICATION
    return eng


def _events(eng) -> list[dict]:
    out = []
    while not eng.event_queue.empty():
        out.append(eng.event_queue.get_nowait())
    return out


def test_s9_1_set_time_over_the_wire(engine):
    obdh = engine.subsystems["obdh"]
    tc = build_tc_packet(apid=1, service=9, subtype=1,
                         data=struct.pack(">I", 900_000_000), seq_count=1)
    engine._dispatch_tc(tc)
    assert obdh.get_obc_time_s() == pytest.approx(900_000_000.0)
    assert obdh._state.time_ref == TIME_REF_GROUND
    ev = [e for e in _events(engine) if e["event_id"] == 0x030C]
    assert len(ev) == 1, "S9,1 must raise the clock-set event"
    assert "jump +900000000" in ev[0]["description"]


def test_s9_1_accepts_the_six_octet_coarse_plus_fine_body(engine):
    obdh = engine.subsystems["obdh"]
    tc = build_tc_packet(apid=1, service=9, subtype=1,
                         data=struct.pack(">IH", 1000, 32768), seq_count=2)
    engine._dispatch_tc(tc)
    assert obdh.get_obc_time_s() == pytest.approx(1000.5)


def test_s9_2_time_report_carries_the_onboard_clock(engine):
    obdh = engine.subsystems["obdh"]
    obdh.handle_command({"command": "set_time", "cuc_seconds": 1234.5})
    pkts = engine._dispatcher._handle_s9(2, b"")
    assert len(pkts) == 1
    from smo_common.protocol.ecss_packet import decommutate_packet
    dec = decommutate_packet(pkts[0])
    coarse, fine = struct.unpack(">IH", dec.data_field[:6])
    assert coarse == 1234
    assert fine == pytest.approx(0.5 * 65536, abs=1)


def test_gps_sync_no_longer_trips_aocs_into_safe_boot(engine):
    """The epoch bug made every sync look like a ~9.5e8 s jump, so the 5 s
    guard forced AOCS to SAFE_BOOT on every single sync."""
    obdh = engine.subsystems["obdh"]
    aocs = engine.subsystems["aocs"]
    engine.params[GPS_FIX_PARAM] = 3.0
    # A clock that is already right: sync, drift a little, sync again.
    obdh.handle_command({
        "command": "set_time",
        "cuc_seconds": datetime_to_cuc_seconds(engine._sim_time)})
    aocs._state.mode = 4
    tc = build_tc_packet(apid=1, service=8, subtype=1,
                         data=bytes([80]), seq_count=3)
    engine._dispatch_tc(tc)
    assert obdh._state.time_ref == TIME_REF_GPS
    assert aocs._state.mode == 4, "AOCS must not be forced to SAFE_BOOT"
    assert not [e for e in _events(engine) if e["event_id"] == 0x020F]


def test_gps_sync_after_a_reboot_does_trip_the_guard(engine):
    """A genuinely large jump still forces SAFE_BOOT — the guard stays."""
    obdh = engine.subsystems["obdh"]
    aocs = engine.subsystems["aocs"]
    engine.params[GPS_FIX_PARAM] = 3.0
    obdh._reset_clock()                      # as after a bootloader reset
    aocs._state.mode = 4
    tc = build_tc_packet(apid=1, service=8, subtype=1,
                         data=bytes([80]), seq_count=4)
    engine._dispatch_tc(tc)
    assert aocs._state.mode == 1             # MODE_SAFE_BOOT
    evs = _events(engine)
    sync = [e for e in evs if e["event_id"] == 0x030D]
    assert len(sync) == 1
    assert "jump +" in sync[0]["description"]
    assert [e for e in evs if e["event_id"] == 0x020F]


def test_gps_sync_without_a_fix_is_refused(engine):
    obdh = engine.subsystems["obdh"]
    engine.params[GPS_FIX_PARAM] = 0.0
    tc = build_tc_packet(apid=1, service=8, subtype=1,
                         data=bytes([80]), seq_count=5)
    engine._dispatch_tc(tc)
    assert obdh._state.time_ref == TIME_REF_NONE


def test_small_resync_does_not_spam_the_event_log(engine):
    """Below clock_jump_event_threshold_s, no event."""
    obdh = engine.subsystems["obdh"]
    engine.params[GPS_FIX_PARAM] = 3.0
    obdh.handle_command({
        "command": "set_time",
        "cuc_seconds": datetime_to_cuc_seconds(engine._sim_time)})
    _events(engine)
    tc = build_tc_packet(apid=1, service=8, subtype=1,
                         data=bytes([80]), seq_count=6)
    engine._dispatch_tc(tc)
    assert not [e for e in _events(engine) if e["event_id"] == 0x030D]


def test_engine_time_source_is_the_obdh_clock(engine):
    """engine._get_cuc_time() -> obdh.get_obc_time_s(), full precision."""
    obdh = engine.subsystems["obdh"]
    obdh.handle_command({"command": "set_time", "cuc_seconds": 4242.25})
    assert engine._get_cuc_time() == pytest.approx(4242.25)
