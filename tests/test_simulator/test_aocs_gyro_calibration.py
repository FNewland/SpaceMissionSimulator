"""AOCS gyro power path and the S8 func 13 gyro bias calibration.

Two complaints are pinned down here.

**"There is no IMU power on command."** There is no IMU *power line* and there
never will be — EO-Sat-1 flies three single-axis gyros with no redundant unit,
powered individually as S2 devices 0x0206-0x0208. What was missing was any
proof that those three device commands do something, and any way to see WHICH
unit is off from telemetry (0x0294 only counts them). The first group below
flies the devices against the real engine and asserts the rate channel and the
estimator genuinely degrade and genuinely recover.

**"The AOCS gyro calibration is only partially implemented."** It was worse
than partial. ``AOCS_GYRO_CALIBRATION`` ran

    self._gyro_bias = [0.0, 0.0, 0.0]
    return {"success": True, "message": "Gyro calibration complete"}

— it zeroed the *truth* state of a Gauss-Markov process the spacecraft cannot
observe (a ~1e-6 deg/s quantity that mean-reverts by itself in half an hour),
completed instantly, ignored an injected hardware bias entirely, had no
preconditions, published no telemetry and raised no event. An instructor could
inject a 0.05 deg/s ``gyro_bias``, fly COM-003 to the letter, be told
"complete", and the bias would be exactly where it started. The second group
below is the specification of what it does now.
"""
from __future__ import annotations

import copy
import struct
from pathlib import Path

import numpy as np
import pytest

from smo_common import attitude as att
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

GYRO_DEVICES = (0x0206, 0x0207, 0x0208)
FUNC_GYRO_CAL = 13

#: aocs.gyro_cal_state
CAL_IDLE, CAL_RUNNING, CAL_COMPLETE, CAL_ABORTED = 0, 1, 2, 3

#: Refusal / abort codes (smo_common.protocol.pus_services.FAILURE_CODES)
ERR_UNPOWERED = 0x0A01
ERR_UNIT_OFF = 0x0A02
ERR_NO_REFERENCE = 0x0A03
ERR_RATE_HIGH = 0x0A04
ERR_BUSY = 0x0A05
ERR_NOT_RUNNING = 0x0A07
ERR_OPERATOR = 0x0A08


class Harness:
    """A booted engine flying NOMINAL nadir with the trackers in the loop.

    The sequence is the operator's own: boot the application, power the AOCS
    line, enable the wheels, power the trackers, acquire the sun, go nadir,
    declare the mission phase.
    """

    def __init__(self) -> None:
        self.eng = SimulationEngine(CONFIG_DIR, speed=1.0)
        self.d = self.eng._dispatcher
        self.tc(55)                       # OBC_BOOT_APP
        self.run(20)
        self.tc(19, 7)                    # EPS_POWER_ON aocs_wheels
        self.tc(19, 2)                    # EPS_POWER_ON ttc_tx
        self.run(5)
        for wheel in range(4):
            self.tc(3, wheel)             # AOCS_ENABLE_WHEEL
        self.run(5)
        self.tc(4, 1)                     # ST1_POWER on
        self.tc(5, 1)                     # ST2_POWER on
        self.run(30)
        self.tc(0, 3)                     # COARSE_SUN
        self.run(300)
        self.tc(0, 4)                     # NOMINAL nadir
        self.run(600)
        self.tc(120, 5, 0)                # SYS_SET_MISSION_PHASE COMMISSIONING
        self.run(5)
        self.tc(120, 6, 0)                # ... NOMINAL
        self.run(5)

    # ── plumbing ─────────────────────────────────────────────────────
    @property
    def aocs(self):
        return self.eng.subsystems["aocs"]

    @property
    def p(self):
        return self.eng.params

    def tc(self, *data):
        return self.d.dispatch(8, 1, bytes(data), None)

    def device(self, device_id: int, on: bool) -> bool:
        """S2.1 device on/off. Returns True when it was accepted.

        S2.1 produces no TM of its own, so the DispatchResult (a list
        subclass) is empty on success — the outcome is on ``error_code``.
        """
        res = self.d.dispatch(2, 1, struct.pack(">HB", device_id, int(on)),
                              None)
        return res.error_code == 0

    def run(self, seconds: float) -> None:
        for _ in range(int(seconds)):
            self.eng._tick_once(1.0)

    def calibrate(self, action: int = 0, duration_s: int = 0):
        return self.d.dispatch(
            8, 1, struct.pack(">BBH", FUNC_GYRO_CAL, action, duration_s), None)

    def events(self, lo: int = 0x0000, hi: int = 0xFFFF) -> list[dict]:
        out = []
        while not self.eng.event_queue.empty():
            out.append(self.eng.event_queue.get_nowait())
        return [e for e in out if lo <= int(e.get("event_id", 0)) <= hi]


@pytest.fixture(scope="module")
def _booted_snapshot():
    """One boot to NOMINAL, captured as a real breakpoint.

    Booting takes ~1000 simulated seconds; doing it per test would make this
    the slowest file in the suite. Going through the shipped
    ``BreakpointManager`` rather than poking model state also means every test
    here starts from a state that has already survived a save/load, which is
    exactly the round-trip this subsystem keeps breaking.
    """
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    from smo_simulator.breakpoints import BreakpointManager
    proto = Harness()
    return BreakpointManager(proto.eng).save(name="gyro-cal-nominal")


@pytest.fixture
def sc(_booted_snapshot):
    from smo_simulator.breakpoints import BreakpointManager
    fresh = Harness.__new__(Harness)
    fresh.eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    fresh.d = fresh.eng._dispatcher
    assert BreakpointManager(fresh.eng).load(
        state=copy.deepcopy(_booted_snapshot))
    fresh.run(30)
    assert int(fresh.p[0x020F]) in (4, 8), (
        f"the restored spacecraft is in aocs.mode {int(fresh.p[0x020F])}, "
        f"not the NOMINAL family")
    return fresh



def _coast_gyro_only(sc, seconds: float = 600.0) -> float:
    """Tracker-less coast, flown INSIDE eclipse, returning the drift (deg).

    The coast must be genuinely gyro-only for the drift arithmetic to mean
    anything: in sunlight the coarse sun sensors keep aiding the filter even
    with both star trackers off. At the pre-2026-08 epoch the ~1030 s boot
    happened to land the coast inside the first eclipse; the 2026-08 epoch
    move (t0 -> 2026-08-20T12:00:00Z, eclipse now 1974-3996 s from t0) broke
    that accident, so the eclipse wait is now explicit.
    """
    for _ in range(2500):
        if sc.eng.orbit.state.in_eclipse:
            break
        sc.eng._tick_once(1.0)
    assert sc.eng.orbit.state.in_eclipse, "never reached eclipse"
    sc.device(0x0204, False)
    sc.device(0x0205, False)
    # Reinitialise the filter's bias state AFTER the trackers are gone (a
    # post-safe-mode filter restart): with the trackers still in the loop
    # during the eclipse wait, the EKF would simply re-learn the bias and
    # the gyro-only coast would prove nothing.
    sc.aocs._ekf.b = np.zeros(3)
    sc.run(seconds)
    return att.quat_angle_deg(np.asarray(sc.aocs._state.q_true, float),
                              sc.aocs._ekf.q)


# ══ 1. The S2 gyro device path ═══════════════════════════════════════

def test_all_three_gyro_units_start_powered_and_are_visible(sc):
    assert int(sc.p[0x0294]) == 3                     # gyro_units_on
    assert int(sc.p[0x02D1]) == 0b111                 # gyro_units_mask
    assert int(sc.p[0x025D]) == 1                     # gyro_enabled


@pytest.mark.parametrize("axis,device", list(enumerate(GYRO_DEVICES)))
def test_powering_one_gyro_unit_off_is_visible_and_costs_that_axis(
        sc, axis, device):
    """The complaint behind "the gyro commands do nothing".

    A unit that is off must (a) show up in telemetry as that specific unit,
    and (b) cost the estimator real knowledge — a dead channel propagates at
    the unknown body rate, not at a healthy channel's ARW.
    """
    sc.run(60)
    knowledge_healthy = float(sc.p[0x02A7])           # arcsec 3sigma

    assert sc.device(device, False)
    sc.run(120)

    assert int(sc.p[0x0294]) == 2
    assert int(sc.p[0x02D1]) == 0b111 & ~(1 << axis), (
        "gyro_units_mask must name WHICH unit is off, not just how many")
    assert float(sc.p[0x02A7]) > 5.0 * knowledge_healthy, (
        f"switching gyro unit {axis} off left the filter claiming "
        f"{sc.p[0x02A7]:.1f} arcsec against {knowledge_healthy:.1f} healthy — "
        f"the dead channel is not being charged for")

    # ...and it recovers when the unit comes back.
    assert sc.device(device, True)
    sc.run(300)
    assert int(sc.p[0x0294]) == 3
    assert int(sc.p[0x02D1]) == 0b111
    assert float(sc.p[0x02A7]) < 2.0 * knowledge_healthy


def test_every_gyro_unit_off_drops_the_gyro_out_of_the_estimator(sc):
    for device in GYRO_DEVICES:
        assert sc.device(device, False)
    sc.run(60)
    assert int(sc.p[0x0294]) == 0
    assert int(sc.p[0x02D1]) == 0
    assert int(sc.p[0x025D]) == 0, "aocs.gyro_enabled must follow the hardware"
    assert not int(sc.p[0x02C1]) & 0x08, (
        "est_source bit3 still claims the gyro is contributing with every "
        "unit switched off")


def test_the_injected_bias_reaches_the_measurement(sc):
    """A gyro_bias fault must be visible at the gyro OUTPUT, not only in the
    instructor's own channel."""
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(10)
    assert float(sc.p[0x0296]) == pytest.approx(0.05 * 3 ** 0.5, rel=1e-3)
    assert float(sc.p[0x02D2]) == pytest.approx(0.05 * 3 ** 0.5, rel=0.05), (
        "aocs.gyro_bias_resid must show the bias an operator can actually see")


# ══ 2. Calibration preconditions ═════════════════════════════════════

def test_calibration_refuses_with_a_gyro_unit_off(sc):
    sc.device(0x0207, False)
    sc.run(2)
    res = sc.calibrate()
    assert res.error_code == ERR_UNIT_OFF
    assert "gyro units are powered" in res.reason
    sc.run(2)
    assert int(sc.p[0x02D0]) == ERR_UNIT_OFF
    assert int(sc.p[0x02C9]) == CAL_IDLE, "a refusal must not start a run"


def test_calibration_refuses_without_a_star_tracker_reference(sc):
    """2026-08c item 11: the gate is on the ESTIMATOR CONFIGURATION, not the
    mode — with both trackers off the MEKF bias state is an observation of
    nothing and the calibration refuses with 0x0A03. (In COARSE_SUN with a
    TRACKING star tracker the calibration is now PERMITTED — see
    test_calibration_permitted_in_coarse_sun_with_st below — which is what
    lets COM-003 calibrate before COM-004 enables the wheels.)"""
    sc.tc(4, 0)                                       # ST1 off
    sc.tc(5, 0)                                       # ST2 off
    sc.run(10)
    res = sc.calibrate()
    assert res.error_code == ERR_NO_REFERENCE
    sc.run(2)
    assert int(sc.p[0x02D0]) == ERR_NO_REFERENCE


# The positive half of the item-11 fix — calibration PERMITTED in COARSE_SUN
# with the ST aiding the filter and NO wheels enabled (the true COM-003
# state) — is proven end-to-end in
# tests/test_simulator/test_field_report_2026_08c.py::TestGyroCalPrecondition.
# This harness is unsuitable for it: its fixture enables the wheels and
# latches commanded_mode NOMINAL, so a commanded COARSE_SUN is actively
# slewing to sun (rates ~0.3 deg/s, above the 0x0A04 quiescence gate) and
# auto-promotes back to NOMINAL the moment sun lock holds.


def test_calibration_refuses_with_the_aocs_unpowered(sc):
    sc.tc(0, 0)                                       # MODE_OFF
    sc.run(5)
    res = sc.calibrate()
    assert res.error_code == ERR_UNPOWERED


def test_calibration_refuses_while_one_is_already_running(sc):
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(10)
    res = sc.calibrate()
    assert res.error_code == ERR_BUSY
    assert int(sc.p[0x02C9]) == CAL_RUNNING


def test_a_refusal_does_not_silently_no_op(sc):
    """The specific complaint: refuse loudly with a code, do not accept and
    quietly do nothing. Every gate above produces a distinct code, and the
    dispatcher turns it into an S1.8 the console can name."""
    from smo_common.protocol.pus_services import FAILURE_CODES
    for code in (ERR_UNPOWERED, ERR_UNIT_OFF, ERR_NO_REFERENCE, ERR_RATE_HIGH,
                 ERR_BUSY, ERR_NOT_RUNNING, ERR_OPERATOR):
        assert code in FAILURE_CODES, f"0x{code:04X} has no shared meaning"


# ══ 3. The calibration itself ════════════════════════════════════════

def test_calibration_takes_real_time_with_observable_progress(sc):
    res = sc.calibrate(duration_s=120)
    assert res.error_code == 0
    sc.run(2)
    assert int(sc.p[0x02C9]) == CAL_RUNNING
    assert float(sc.p[0x02CA]) < 10.0

    sc.run(58)
    mid = float(sc.p[0x02CA])
    assert 40.0 < mid < 60.0, f"progress at t+60 of a 120 s arc was {mid}"
    assert int(sc.p[0x02C9]) == CAL_RUNNING, "it must NOT complete instantly"

    sc.run(70)
    assert int(sc.p[0x02C9]) == CAL_COMPLETE
    assert float(sc.p[0x02CA]) == pytest.approx(100.0)
    assert int(sc.p[0x02CF]) == 1                     # SUCCESS


def test_calibration_estimates_the_injected_bias_and_stops_the_drift(sc):
    """The headline test.

    Inject a known hardware bias, run the calibration, and assert that

      * the estimate it publishes matches the injected truth per axis;
      * the residual bias at the gyro output collapses while the injected
        HARDWARE fault is untouched — calibration compensates a fault, it does
        not repair it;
      * the estimator's own bias state is reset, because the measurement it
        now sees is bias-free;
      * and the attitude estimate consequently stops drifting when the star
        tracker is taken away and the filter has to coast on the gyro alone.
    """
    bias_dps = 0.05
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=bias_dps)
    sc.run(30)

    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)

    assert int(sc.p[0x02C9]) == CAL_COMPLETE
    assert int(sc.p[0x02CF]) == 1
    assert int(sc.p[0x02D0]) == 0

    # 1. The estimate matches the truth, per axis.
    for pid, axis in ((0x02CB, "X"), (0x02CC, "Y"), (0x02CD, "Z")):
        assert float(sc.p[pid]) == pytest.approx(bias_dps, rel=0.05), (
            f"calibration estimated {sc.p[pid]:.5f} deg/s on {axis} against "
            f"an injected {bias_dps} deg/s")

    # 2. The residual is gone; the hardware fault is not.
    assert float(sc.p[0x02D2]) < 0.005, "the bias is still at the gyro output"
    assert float(sc.p[0x02CE]) == pytest.approx(bias_dps * 3 ** 0.5, rel=0.05)
    assert float(sc.p[0x0296]) == pytest.approx(bias_dps * 3 ** 0.5, rel=1e-3), (
        "the INJECTED hardware bias must survive a calibration — compensating "
        "a fault is not repairing it, and 0x0296 is the instructor's view")
    assert sc.aocs._gyro_bias_inject == [bias_dps] * 3

    # 3. The filter's bias state was reset with the compensation applied.
    assert float(np.linalg.norm(sc.aocs._ekf.b)) * 180.0 / np.pi < 0.002

    # 4. The estimate stops drifting on a gyro-only coast. Take the trackers
    #    away and restart the filter's bias state, exactly as a post-safe-mode
    #    filter reinitialisation would.
    drift = _coast_gyro_only(sc)
    assert drift < 1.0, (
        f"the attitude estimate drifted {drift:.2f} deg over a 600 s "
        f"gyro-only coast after a successful calibration")


def test_without_the_calibration_the_same_coast_walks_away(sc):
    """The control for the test above: same bias, same coast, no calibration.

    Without it the estimate walks off at |b|.t — tens of degrees in ten
    minutes, which is what trips the aocs.att_error FDIR rule. This is the
    teaching point the calibration exists to make, so it is asserted rather
    than assumed.
    """
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    drift = _coast_gyro_only(sc)
    assert drift > 20.0, (
        f"an uncompensated 0.05 deg/s three-axis bias should walk the estimate "
        f"tens of degrees in 600 s; it moved {drift:.2f} deg")


def test_a_second_calibration_converges_rather_than_double_counting(sc):
    """Running it twice must not subtract the bias twice.

    The filter observes the bias of the ALREADY-COMPENSATED measurement, so
    the new table is the old table plus the leftover. A naive
    ``comp = mean(b)`` would zero the compensation on the second run.
    """
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)
    first = list(sc.aocs._state.gyro_cal_comp)

    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)
    second = list(sc.aocs._state.gyro_cal_comp)

    for i in range(3):
        assert second[i] == pytest.approx(first[i], abs=0.002)
    assert float(sc.p[0x02D2]) < 0.005


# ══ 4. Aborts ════════════════════════════════════════════════════════

def test_losing_a_precondition_mid_run_aborts_instead_of_averaging_noise(sc):
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(60)
    assert int(sc.p[0x02C9]) == CAL_RUNNING

    sc.device(0x0208, False)              # pull the Z gyro out mid-arc
    sc.run(5)

    assert int(sc.p[0x02C9]) == CAL_ABORTED
    assert int(sc.p[0x02CF]) == 2                     # FAILED
    assert int(sc.p[0x02D0]) == ERR_UNIT_OFF
    assert sc.aocs._state.gyro_cal_comp == [0.0, 0.0, 0.0], (
        "an aborted calibration must apply NOTHING")


def test_operator_abort_and_abort_with_nothing_running(sc):
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(30)
    assert sc.calibrate(action=1).error_code == 0
    sc.run(2)
    assert int(sc.p[0x02C9]) == CAL_ABORTED
    assert int(sc.p[0x02D0]) == ERR_OPERATOR

    res = sc.calibrate(action=1)
    assert res.error_code == ERR_NOT_RUNNING


def test_an_abort_leaves_an_earlier_good_compensation_in_force(sc):
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)
    good = list(sc.aocs._state.gyro_cal_comp)
    assert int(sc.p[0x02C9]) == CAL_COMPLETE

    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(60)
    # 2026-08c item 11: COARSE_SUN no longer costs the ST reference, so the
    # abort trigger is a REAL reference loss — both trackers powered off.
    sc.tc(4, 0)
    sc.tc(5, 0)
    sc.run(5)
    assert int(sc.p[0x02C9]) == CAL_ABORTED
    assert sc.aocs._state.gyro_cal_comp == good, (
        "aborting a re-calibration must not discard the working table")


# ══ 5. Events ════════════════════════════════════════════════════════

def test_the_calibration_raises_start_and_complete_events(sc):
    sc.events()                                       # drain the boot log
    assert sc.calibrate(duration_s=60).error_code == 0
    sc.run(70)
    ids = [int(e["event_id"]) for e in sc.events(0x0210, 0x0212)]
    assert ids == [0x0210, 0x0211], f"expected START then COMPLETE, got {ids}"


def test_an_abort_raises_the_abort_event_with_its_reason(sc):
    sc.events()
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(40)
    sc.device(0x0206, False)
    sc.run(5)
    evs = sc.events(0x0210, 0x0212)
    aborts = [e for e in evs if int(e["event_id"]) == 0x0212]
    assert aborts, f"no AOCS_GYRO_CAL_ABORT raised; saw {evs}"
    assert "0x0A02" in aborts[0]["description"]


def test_a_successful_calibration_clears_the_gyro_bias_high_alarm(sc):
    """GYRO_BIAS_HIGH (0x020B) is measured on the RESIDUAL, so compensating a
    real bias genuinely clears the alarm even though the hardware fault is
    still injected."""
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    assert sc.aocs._prev_gyro_bias_high is True
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)
    assert sc.aocs._prev_gyro_bias_high is False
    assert float(sc.p[0x0296]) > 0.05, "the fault itself is still injected"


# ══ 6. Breakpoint round-trip ═════════════════════════════════════════

def test_a_running_calibration_survives_a_breakpoint_round_trip(sc):
    """Persistent state that silently heals on restore is a repeat bug here,
    so the accumulator and the elapsed arc are pinned, not just the flag."""
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(100)

    snapshot = copy.deepcopy(sc.aocs.get_state())
    before = sc.aocs._state
    saved = (before.gyro_cal_state, before.gyro_cal_elapsed,
             before.gyro_cal_n, list(before.gyro_cal_acc),
             before.gyro_cal_duration_s)
    assert saved[0] == CAL_RUNNING and saved[2] > 0

    sc.aocs.set_state(snapshot)
    after = sc.aocs._state
    assert (after.gyro_cal_state, after.gyro_cal_elapsed, after.gyro_cal_n,
            list(after.gyro_cal_acc), after.gyro_cal_duration_s) == saved

    # ...and it finishes normally from the restored state.
    sc.run(220)
    assert int(sc.p[0x02C9]) == CAL_COMPLETE
    assert float(sc.p[0x02CB]) == pytest.approx(0.05, rel=0.05)


def test_the_compensation_survives_a_restore_into_a_fresh_engine(sc):
    """The failure mode this codebase keeps re-growing: state that heals on
    restore. A calibration that vanished across a breakpoint would silently
    un-calibrate the gyros, and the injected fault would come back with it."""
    sc.aocs.inject_failure("gyro_bias", axis="all", bias=0.05)
    sc.run(30)
    assert sc.calibrate(duration_s=300).error_code == 0
    sc.run(320)
    comp = list(sc.aocs._state.gyro_cal_comp)
    snapshot = copy.deepcopy(sc.aocs.get_state())

    other = SimulationEngine(CONFIG_DIR, speed=1.0)
    other.subsystems["aocs"].set_state(snapshot)
    for _ in range(5):
        other._tick_once(1.0)

    restored = other.subsystems["aocs"]._state
    assert restored.gyro_cal_comp == comp
    assert restored.gyro_cal_bias == comp
    assert restored.gyro_cal_result == 1
    assert other.subsystems["aocs"]._gyro_bias_inject == [0.05] * 3, (
        "the injected HARDWARE fault must survive too")
    assert float(other.params[0x02D2]) < 0.005, (
        "the restored spacecraft must still be compensated")


# ══ 7. Catalogue / dispatch agreement ════════════════════════════════

def test_the_catalogued_command_encodes_to_what_the_dispatcher_reads():
    from smo_common.commanding import encode, load_catalog
    catalog = load_catalog(CONFIG_DIR)
    data = encode("AOCS_GYRO_CALIBRATION", {"action": 1, "duration_s": 600},
                  catalog=catalog)
    assert data == struct.pack(">BBH", FUNC_GYRO_CAL, 1, 600)
    # The legacy no-argument form is still a valid START.
    assert encode("AOCS_GYRO_CALIBRATION", {}, catalog=catalog) == \
        struct.pack(">BBH", FUNC_GYRO_CAL, 0, 0)


def test_the_console_can_reach_the_gyro_devices_and_the_calibration():
    """The operator-facing half of "there is no IMU power on command".

    The three S2 gyro device commands were catalogued and worked, but the
    console's AOCS EQUIPMENT quick-command group offered star-tracker and GPS
    power and nothing at all for the gyros — so from the command page there
    was no gyro power control to find.
    """
    import yaml
    cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml")
                         .read_text())
    quick = cat["quick_commands"]
    by_command = {q["command"] for q in quick}
    for n in range(3):
        assert f"S2_AOCS_GYROSCOPE_{n}" in by_command, (
            f"gyro unit {n} has no quick command — the console command page "
            f"cannot power it")
    assert "AOCS_GYRO_CALIBRATION" in by_command
    gyro = [q for q in quick if q["command"].startswith("S2_AOCS_GYROSCOPE")]
    assert {q["args"]["state"] for q in gyro} == {0, 1}, "need ON and OFF"
    assert {q["args"]["device_id"] for q in gyro} == set(GYRO_DEVICES)
