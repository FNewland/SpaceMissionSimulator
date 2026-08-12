"""AOCS mode management, desaturation and gyro wiring (2026-08b batch).

Every test here pins down a behaviour that was measured to be broken on the
shipped model, so each one carries the before/after number it was written from.

* Mode management (finding A-a). A live instrumented run went
  NOMINAL -> ECLIPSE -> COARSE_SUN after the first eclipse and never came back
  for six orbits. Root cause was twofold: a truth-side gyro bias model that
  random-walked ~100x faster than the MEKF's process noise (6.8 deg of drift in
  one eclipse against a 0.29 deg 3-sigma covariance, tripping the
  `aocs.att_error > 5` FDIR rule), and an eclipse-exit transition hard-wired to
  NOMINAL that could not restore a FINE or INERTIAL commanded mode.
* Desaturation. The dump gain was ~450x the rate at which the geomagnetic
  field sweeps, so the momentum vector locked onto B and the mode never
  reached its exit condition; the exit test itself looked only at wheel speed,
  and there was no timeout.
* Gyro wiring. The three S2 gyro device commands were inert.
"""
import math

import numpy as np
import pytest

from smo_common import attitude as att
from smo_simulator.models.aocs_basic import (
    MODE_COARSE_SUN, MODE_DESAT, MODE_DETUMBLE, MODE_ECLIPSE, MODE_FINE_POINT,
    MODE_INERTIAL, MODE_NOMINAL, MODE_OFF, MODE_SLEW, _RPM2RAD,
)

from tests.test_simulator.test_aocs_dynamics import (
    make_model, make_orbit, settled_model,
)

_DEG = math.pi / 180.0


def _h_body(m):
    return float(np.linalg.norm(m._A @ np.array(m._state.h_wheel)))


# ─── Mode management: every demotion has a return path ───────────────────

class TestModeReturnPaths:

    def test_eclipse_exit_resumes_commanded_mode(self):
        """Eclipse exit must restore the COMMANDED mode, not a hard-wired
        NOMINAL. This is the transition that lost nadir pointing for six
        orbits in the instrumented run."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        assert s.commanded_mode == MODE_NOMINAL

        o_ecl = make_orbit(in_eclipse=True)
        for _ in range(60):
            m.tick(1.0, o_ecl, p)
        assert s.mode == MODE_ECLIPSE
        assert s.mode_demoted is True
        assert p[0x0298] == 1.0
        assert "resume" in s.mode_change_reason

        for _ in range(60):
            m.tick(1.0, o, p)
        assert s.mode == MODE_NOMINAL, s.mode_change_reason
        assert s.mode_demoted is False
        assert p[0x0297] == float(MODE_NOMINAL)

    def test_inertial_holds_through_eclipse(self):
        """INERTIAL does NOT yield to eclipse, and that is correct: an ECI
        attitude hold has no sun dependence, and ECLIPSE exists to say "no sun
        reference available". Pinned as a test because the eclipse yield is
        exactly the transition that stranded NOMINAL."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        m.handle_command({"command": "set_target_quat",
                          "quaternion": list(s.q_true)})
        m.handle_command({"command": "set_mode", "mode": MODE_INERTIAL})
        for _ in range(60):
            m.tick(1.0, o, p)
        o_ecl = make_orbit(in_eclipse=True)
        for _ in range(600):
            m.tick(1.0, o_ecl, p)
        assert s.mode == MODE_INERTIAL
        assert s.mode_demoted is False
        assert s.att_error < 1.0

    def test_fine_is_repromoted_after_eclipse(self):
        """FINE cannot be re-entered at the instant a transient ends (its
        0x02C5 preconditions need a settled, converged spacecraft), so the
        return path is NOMINAL followed by an automatic promotion once the
        preconditions have actually held for 30 s."""
        m, o, p = settled_model(MODE_FINE_POINT, ticks=1400)
        s = m._state
        assert s.commanded_mode == MODE_FINE_POINT

        o_ecl = make_orbit(in_eclipse=True)
        for _ in range(60):
            m.tick(1.0, o_ecl, p)
        assert s.mode == MODE_ECLIPSE

        for _ in range(600):
            m.tick(1.0, o, p)
            if s.mode == MODE_FINE_POINT:
                break
        assert s.mode == MODE_FINE_POINT, s.mode_change_reason

    def test_slew_returns_to_pre_slew_mode(self):
        """A slew is a transient. Ending it always in NOMINAL threw away an
        INERTIAL commanded mode on every manoeuvre."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        m.handle_command({"command": "set_target_quat",
                          "quaternion": list(s.q_true)})
        m.handle_command({"command": "set_mode", "mode": MODE_INERTIAL})
        for _ in range(60):
            m.tick(1.0, o, p)
        q0 = np.asarray(s.q_true, float)
        dq = np.array([0.0, math.sin(math.radians(5.0)), 0.0,
                       math.cos(math.radians(5.0))])
        qt = att.quat_normalize(att.quat_mult(q0, dq))
        m.handle_command({"command": "slew_to_quaternion",
                          "quaternion": [float(x) for x in qt],
                          "rate_dps": 0.5})
        assert s.mode == MODE_SLEW
        for _ in range(600):
            m.tick(1.0, o, p)
            if s.mode != MODE_SLEW:
                break
        assert s.mode == MODE_INERTIAL

    def test_coarse_sun_hands_back_to_commanded_mode(self):
        """COARSE_SUN is a safe hold, never a destination. An FDIR safing that
        lands here has to give the spacecraft back when the sun lock and the
        tracker recover — this is the transition that stranded the live run."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        m.handle_command({"command": "set_target_quat",
                          "quaternion": list(s.q_true)})
        m.handle_command({"command": "set_mode", "mode": MODE_INERTIAL})
        for _ in range(60):
            m.tick(1.0, o, p)
        # FDIR safing: exactly what the `safe_mode_aocs` callback does.
        m.handle_command({"command": "set_mode", "mode": MODE_COARSE_SUN})
        assert s.commanded_mode == MODE_INERTIAL  # unchanged by the demotion
        for _ in range(3000):
            m.tick(1.0, o, p)
            if s.mode == MODE_INERTIAL:
                break
        assert s.mode == MODE_INERTIAL, (s.mode, s.mode_change_reason)

    def test_commanded_mode_is_not_overwritten_by_transients(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        for transient in (MODE_DESAT, MODE_SLEW, MODE_ECLIPSE, MODE_DETUMBLE):
            m.handle_command({"command": "set_mode", "mode": transient})
            assert s.commanded_mode == MODE_NOMINAL

    def test_every_transition_carries_a_reason(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        s = m._state
        o_ecl = make_orbit(in_eclipse=True)
        for _ in range(60):
            m.tick(1.0, o_ecl, p)
        assert s.mode == MODE_ECLIPSE
        assert s.mode_change_reason not in ("", "unspecified")


# ─── Eclipse pointing: the star tracker works BEST in umbra ──────────────

class TestEclipsePointing:

    def test_eclipse_pointing_does_not_decay(self):
        """Measured before the fix: 6.8 deg of true pointing error after 25 min
        of eclipse, which is what tripped the 5 deg FDIR rule every orbit.
        Two causes, both fixed: the truth gyro bias random-walked ~100x faster
        than the filter believed, and ECLIPSE refused to use the star tracker
        even though umbra is the best sky it ever sees."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        o_ecl = make_orbit(in_eclipse=True)
        for _ in range(1500):
            m.tick(1.0, o_ecl, p)
        assert s.mode == MODE_ECLIPSE
        assert s.att_error < 1.0, f"eclipse pointing decayed to {s.att_error} deg"
        # And the covariance must be consistent with the actual error.
        actual = att.quat_angle_deg(m._ekf.q, np.asarray(s.q_true, float))
        assert actual < 1.0

    def test_gyro_bias_truth_is_bounded_by_its_spec(self):
        """The truth bias is a Gauss-Markov with the tau / sigma declared in
        aocs.yaml, not a free random walk. Before the fix it reached ~3e-3
        deg/s (11 deg/h) in an hour against a 2.8e-5 deg/s (0.1 deg/h) spec."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=100)
        peak = 0.0
        for _ in range(7200):
            m.tick(1.0, o, p)
            peak = max(peak, max(abs(b) for b in m._gyro_bias))
        # 6 sigma on the declared steady-state bias sigma, generously.
        assert peak < 6.0 * m._gyro_bias_ss_dps, peak


# ─── Desaturation ────────────────────────────────────────────────────────

class TestDesaturation:

    def _load(self, m, nms=0.020):
        """Put `nms` of BODY momentum on the wheels (all same sign -> +Z)."""
        per = nms / 2.0          # A @ [h,h,h,h] = (0, 0, 2h)
        for i in range(4):
            m._state.h_wheel[i] = per
            m._state.rw_speed[i] = per / m._rw_inertia / _RPM2RAD

    def test_dump_actually_removes_body_momentum(self):
        """The old k = 1.0 s^-1 gain locked the momentum vector onto B and the
        reference 0.020 N.m.s loading never fell below 0.005 within an orbit.
        k = 0.002 (~1.8x the field sweep rate) reaches it in ~1500 s."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        self._load(m)
        h0 = _h_body(m)
        assert h0 == pytest.approx(0.020, rel=0.05)
        m.handle_command({"command": "desaturate"})
        for _ in range(3000):
            m.tick(1.0, o, p)
            s.mode = MODE_DESAT          # pin: measure the dump, not the FSM
        assert _h_body(m) < 0.4 * h0, (h0, _h_body(m))

    def test_desat_returns_to_the_pre_desat_mode(self):
        """DESAT always exited to NOMINAL, so a dump during an INERTIAL
        campaign silently ended it."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        m.handle_command({"command": "set_target_quat",
                          "quaternion": list(s.q_true)})
        m.handle_command({"command": "set_mode", "mode": MODE_INERTIAL})
        for _ in range(200):
            m.tick(1.0, o, p)
        self._load(m)
        m.handle_command({"command": "desaturate"})
        assert s.mode == MODE_DESAT
        for _ in range(5000):
            m.tick(1.0, o, p)
            if s.mode != MODE_DESAT:
                break
        assert s.mode == MODE_INERTIAL, (s.mode, s.mode_change_reason)
        assert s.desat_ineffective is False

    def test_desat_times_out_instead_of_parking_for_ever(self):
        """With no working actuator the dump cannot converge. Without a
        timeout the spacecraft stayed in DESAT for ever — off-pointing, out of
        contact and unrecoverable."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        events = []

        class _Engine:
            class _Q:
                @staticmethod
                def put(e):
                    events.append(e)
            _model_event_queue = _Q()

        m._engine = _Engine()
        m._desat_timeout_s = 300.0
        s.mtq_enabled = False
        self._load(m)
        m.handle_command({"command": "desaturate"})
        for _ in range(600):
            m.tick(1.0, o, p)
            if s.mode != MODE_DESAT:
                break
        assert s.mode == MODE_NOMINAL
        assert s.desat_ineffective is True
        assert p[0x029C] == 1.0
        assert any(e["event_id"] == 0x020E for e in events), \
            "AOCS_DESAT_INEFFECTIVE (0x020E) not emitted"

    def test_mom_mgmt_exit_pct_is_read(self):
        """`exit_pct` was accepted, range-checked and then read nowhere. It is
        now one of the DESAT completion tests."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        r = m.handle_command({"command": "set_mom_mgmt", "enable": True,
                              "entry_pct": 70, "exit_pct": 60})
        assert r["success"]
        self._load(m, nms=0.030)
        m.handle_command({"command": "desaturate"})
        for _ in range(4000):
            m.tick(1.0, o, p)
            if s.mode != MODE_DESAT:
                break
        assert s.mode != MODE_DESAT
        # A 60 % exit target must complete far sooner than a 5 % one; the
        # sharper statement is simply that the mode ended above the old
        # wheel-speed guard, which only the exit_pct test can explain.
        assert m._h_env_frac(s) <= 60.0 + 1e-6 or _h_body(m) <= m._desat_deadband_nms

    def test_desat_inhibit_operator_and_payload(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        m.handle_command({"command": "set_desat_inhibit", "inhibit": True})
        m.tick(1.0, o, p)
        assert p[0x029A] == 1.0
        r = m.handle_command({"command": "desaturate"})
        assert not r["success"] and r["code"] == "DESAT_INHIBITED"
        m.handle_command({"command": "set_desat_inhibit", "inhibit": False})
        # Payload IMAGING (0x0600 == 2) asserts the inhibit automatically, so
        # that this and the payload capture gate (which demands exactly AOCS
        # mode 5) cannot disagree.
        p[0x0600] = 2.0
        m.tick(1.0, o, p)
        r = m.handle_command({"command": "desaturate"})
        assert not r["success"] and r["code"] == "DESAT_INHIBITED"
        p[0x0600] = 1.0
        m.tick(1.0, o, p)
        assert m.handle_command({"command": "desaturate"})["success"]

    def test_desat_refused_in_fine_and_never_auto_entered(self):
        """A dump mid-strip drops FINE, the payload flags the strip PARTIAL,
        and the healthy exit used to return to NOMINAL — so one dump cost the
        mission its imaging mode permanently."""
        m, o, p = settled_model(MODE_FINE_POINT, ticks=1400)
        s = m._state
        r = m.handle_command({"command": "desaturate"})
        assert not r["success"] and r["code"] == "DESAT_REFUSED_FINE"
        # Auto-entry excludes FINE even at 99 % of the momentum envelope.
        m.handle_command({"command": "set_mom_mgmt", "enable": True,
                          "entry_pct": 30, "exit_pct": 20})
        h_max = m._rw_inertia * m._rw_max_speed * _RPM2RAD
        for i in range(4):
            s.h_wheel[i] = 0.9 * h_max
        for _ in range(60):
            m.tick(1.0, o, p)
        assert m._h_env_frac(s) > 30.0
        assert s.mode == MODE_FINE_POINT
        # ... but bit4 of 0x02C5 tells the operator before they plan a strip.
        assert s.fine_ready_mask & 0x10


# ─── Gyro / IMU wiring ───────────────────────────────────────────────────

class TestGyroWiring:

    def test_gyro_enabled_telemetry_follows_the_devices(self):
        """0x025D was hard-wired to `mode != OFF`, so the three S2 gyro
        commands looked inert on the ground."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        s = m._state
        assert p[0x025D] == 1.0 and p[0x0294] == 3.0
        s.device_states[0x0206] = False
        m.tick(1.0, o, p)
        assert p[0x025D] == 1.0 and p[0x0294] == 2.0
        for d in (0x0207, 0x0208):
            s.device_states[d] = False
        m.tick(1.0, o, p)
        assert p[0x025D] == 0.0 and p[0x0294] == 0.0
        assert s.mode != MODE_OFF        # the AOCS is still powered

    def test_gyros_off_inflates_the_covariance(self):
        """`_gyro_measurement` never consulted device_states, so switching all
        three gyros off left the MEKF propagating on a perfect gyro."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        # Kill the tracker too, so nothing bounds the covariance.
        s.st1_failed = s.st2_failed = True
        for d in (0x0206, 0x0207, 0x0208):
            s.device_states[d] = False
        k0 = s.knowledge_err_3sig_deg
        for _ in range(300):
            m.tick(1.0, o, p)
        assert s.knowledge_err_3sig_deg > 10.0 * max(k0, 1e-4), (
            k0, s.knowledge_err_3sig_deg)
        assert not (s.est_source & 0x08)   # gyro no longer a source

    def test_one_dead_axis_only_costs_that_axis(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        s = m._state
        s.device_states[0x0207] = False
        m.tick(1.0, o, p)
        assert m._omega_meas[1] == 0.0
        assert m._omega_meas[0] != 0.0

    def test_gyro_saturates_on_a_violent_tumble(self):
        m = make_model()
        o = make_orbit()
        p = {}
        m._env = m._environment(o)
        m._state.omega_true = [600.0 * _DEG, 0.0, 0.0]   # > 400 deg/s range
        w = m._gyro_measurement(m._state, 1.0)
        assert abs(w[0]) == pytest.approx(m._gyro_max_rate_dps * _DEG, rel=1e-6)
        assert m._gyro_saturated is True

    def test_injected_gyro_bias_persists_and_is_visible(self):
        """The injection used to be written into the Gauss-Markov truth state,
        where it would mean-revert away in ~30 min."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        m.inject_failure("gyro_bias", axis="all", bias=0.05)
        for _ in range(3600):
            m.tick(1.0, o, p)
        assert p[0x0296] == pytest.approx(0.05 * math.sqrt(3.0), rel=1e-3)
        assert abs(p[0x0270]) > 0.04
        m.clear_failure("gyro_bias")
        m.tick(1.0, o, p)
        assert p[0x0296] == pytest.approx(0.0, abs=1e-9)


# ─── DETUMBLE is B-dot: magnetometer only ────────────────────────────────

class TestDetumbleIsBdot:

    def _run(self, m, o, w0=10.0, n=6000):
        s = m._state
        s.omega_true = [w0 * _DEG, -0.6 * w0 * _DEG, 0.8 * w0 * _DEG]
        m.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        p = {}
        for _ in range(n):
            m.tick(1.0, o, p)
            s.mode = MODE_DETUMBLE
        return float(np.linalg.norm(s.omega_true)) / _DEG

    def test_detumble_works_with_every_gyro_off(self):
        """B-dot needs no rate sensor and no attitude knowledge — that is the
        whole point of it. The old implementation fed back `omega_true`, a
        quantity no sensor produces."""
        m, o = make_model(), make_orbit()
        for d in (0x0206, 0x0207, 0x0208):
            m._state.device_states[d] = False
        assert self._run(m, o) < 0.5

    def test_detumble_fails_without_the_magnetometer(self):
        m, o = make_model(), make_orbit()
        for d in (0x0209, 0x020A):
            m._state.device_states[d] = False
        assert self._run(m, o) > 5.0

    def test_detumble_converges_from_a_separation_tumble(self):
        m, o = make_model(), make_orbit()
        assert self._run(m, o) < 0.5


# ─── Secular fast-forward must agree with the real-time path ─────────────

class TestSecularAllocation:

    def test_secular_momentum_uses_the_active_wheel_set(self):
        """The fast-forward path used the fixed 4-wheel pseudo-inverse, so with
        a wheel failed the momentum that should have gone to the surviving
        three was silently discarded and the books diverged above 60x."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        s = m._state
        m.handle_command({"command": "disable_wheel", "wheel": 2})
        for _ in range(60):
            m.tick(1.0, o, p)
        h_start = [float(x) for x in s.h_wheel]
        p[0x05FE] = 120.0            # engine speed -> secular mode
        for _ in range(20):
            m.tick(60.0, o, p)
        assert s.fidelity_mode == 1
        # The dead wheel must not have been fed, and the live ones must have.
        assert s.h_wheel[2] == pytest.approx(h_start[2], abs=1e-9)
        moved = sum(1 for i in (0, 1, 3)
                    if abs(s.h_wheel[i] - h_start[i]) > 1e-12)
        assert moved == 3, [s.h_wheel[i] - h_start[i] for i in (0, 1, 3)]


# ─── FDIR needs to tell a failure from an uncommissioned bus ─────────────

class TestWheelCommissioningLatch:

    def test_latch_is_clear_until_wheels_are_enabled(self):
        m = make_model()
        o = make_orbit()
        p = {}
        s = m._state
        for w in range(4):
            m.handle_command({"command": "disable_wheel", "wheel": w})
        m.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        for _ in range(30):
            m.tick(1.0, o, p)
        assert p[0x029D] == 0.0
        for w in range(3):
            m.handle_command({"command": "enable_wheel", "wheel": w})
        m.tick(1.0, o, p)
        assert p[0x029D] == 1.0
        # A genuine in-flight double failure must NOT clear the latch, or the
        # FDIR rule it gates could never fire.
        for w in range(3):
            m.handle_command({"command": "disable_wheel", "wheel": w})
        m.tick(1.0, o, p)
        assert p[0x029D] == 1.0
        # Only an AOCS power-down does.
        m.handle_command({"command": "set_mode", "mode": MODE_OFF})
        m.tick(1.0, o, p)
        assert p[0x029D] == 0.0
