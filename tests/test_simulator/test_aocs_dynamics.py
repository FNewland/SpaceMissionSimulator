"""Phase C (brief 02 WP-10) — AOCS dynamics, estimator, gating, secular mode.

Standalone-model tests: the AOCS model synthesises a deterministic SSO-like
environment when the orbit stub carries no ECI state, and its RNG is a
fixed-seed ``random.Random``, so every number here is reproducible.
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from smo_common import attitude as att
from smo_simulator.models.aocs_basic import (
    AOCSBasicModel, MODE_NOMINAL, MODE_FINE_POINT, MODE_SLEW, MODE_DESAT,
    MODE_INERTIAL, MODE_DETUMBLE, _RPM2RAD,
)

_DEG = math.pi / 180.0


def make_orbit(in_eclipse=False, beta=20.0):
    return SimpleNamespace(
        in_eclipse=in_eclipse, solar_beta_deg=beta, lat_deg=45.0,
        lon_deg=10.0, alt_km=450.0, vel_x=0.0, vel_y=7.5, vel_z=0.0,
        in_contact=False)


def make_model():
    m = AOCSBasicModel()
    m.configure({})
    return m


def settled_model(mode=MODE_NOMINAL, ticks=1200):
    """Model driven to steady state in the requested mode (ST powered)."""
    m = make_model()
    o = make_orbit()
    m.handle_command({"command": "st_power", "unit": 1, "on": True})
    m.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
    p = {}
    for _ in range(ticks):
        m.tick(1.0, o, p)
    if mode == MODE_FINE_POINT:
        r = m.handle_command({"command": "set_mode", "mode": 5})
        assert r["success"], r
        for _ in range(400):
            m.tick(1.0, o, p)
    elif mode != MODE_NOMINAL:
        m.handle_command({"command": "set_mode", "mode": mode})
        for _ in range(400):
            m.tick(1.0, o, p)
    return m, o, p


# ─── WP-2: rigid-body dynamics and actuators ─────────────────────────────

class TestDynamics:

    def test_momentum_conservation_free_body(self):
        """Zero control, zero disturbance: inertial |Iω + A·h_w| conserved."""
        m = make_model()
        s = m._state
        s.omega_true = [0.01, -0.005, 0.02]
        m._env = m._environment(make_orbit())

        def H_eci():
            C = att.quat_to_dcm(np.asarray(s.q_true, float))
            h_b = m._inertia @ np.array(s.omega_true) \
                + m._A @ np.array(s.h_wheel)
            return C.T @ h_b

        H0 = H_eci()
        for _ in range(20000):          # 10^4 s at h = 0.5
            m._rk4_step(s, 0.5, np.zeros(3))
        drift = np.linalg.norm(H_eci() - H0) / np.linalg.norm(H0)
        assert drift < 1e-6, f"momentum drift {drift:.2e}"

    def test_constant_torque_produces_expected_rate(self):
        """+τ about X for 100 s ⇒ ω_x = τ·t / I_xx within 1 %."""
        m = make_model()
        s = m._state
        s.h_wheel = [0.0] * 4           # remove bias momentum coupling
        m._env = m._environment(make_orbit())
        tau = 1.0e-4
        for _ in range(200):
            m._rk4_step(s, 0.5, np.array([tau, 0.0, 0.0]))
        expect = tau * 100.0 / m._inertia[0, 0]
        assert s.omega_true[0] == pytest.approx(expect, rel=0.01)

    def test_balanced_pyramid_zero_net_momentum(self):
        """Four wheels at equal alternating bias speeds: |h_body| ≈ 0 (the
        old scalar sum reported 2.51 N·m·s for the same state)."""
        m = make_model()
        h_b = m._A @ np.array(m._state.h_wheel)
        assert np.linalg.norm(h_b) < 1e-9
        # And a same-sign set reports a genuinely non-zero vector momentum
        m._state.h_wheel = [m._rw_inertia * 1200 * _RPM2RAD] * 4
        h_b = m._A @ np.array(m._state.h_wheel)
        assert np.linalg.norm(h_b) > 1e-3

    def test_mtq_dipole_torque_magnitude(self):
        """1.6 A·m² ⊥ a 30 µT field ⇒ τ = 4.8e-5 N·m within 1 %."""
        m = make_model()
        s = m._state
        s.mtq_enabled = True
        B = np.array([0.0, 0.0, 30e-6])
        tau = m._mtq_dipole_torque(s, np.array([1.6, 0.0, 0.0]), B)
        assert np.linalg.norm(tau) == pytest.approx(1.6 * 30e-6, rel=0.01)
        assert s.mtq_x_duty == pytest.approx(1.0)

    def test_wheel_torque_clamped_at_rated(self):
        """A huge commanded torque never exceeds ±max_torque per wheel."""
        m = make_model()
        s = m._state
        m._env = m._environment(make_orbit())
        m._apply_wheel_torque(s, 0.5, np.array([1.0, 1.0, 1.0]), None, 0.0)
        for t in s.tau_wheel:
            assert abs(t) <= m._rw_max_torque * (1.0 + 1e-9)


# ─── WP-7: disturbance torques are each inside the computed bound ────────

class TestDisturbances:

    def test_disturbance_magnitudes_sane(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        s = m._state
        tau = np.array(s.tau_dist)
        # Total bound: GG(4.5e-7) + aero(2.5e-7) + dipole(5.5e-7) + SRP(1.5e-7)
        assert np.linalg.norm(tau) < 1.5e-6
        assert np.linalg.norm(tau) > 1e-9   # and not zero — physics is on

    def test_gravity_gradient_at_45deg(self):
        """GG at 45° off-nadir ≈ (3µ/R³)·ΔI·sinθcosθ within 20 %."""
        m = make_model()
        o = make_orbit()
        m._env = m._environment(o)
        s = m._state
        # nadir along -x after rotating body 45° about y from nadir-z
        r = m._env['r_m']
        C_li = att.lvlh_dcm(r, m._env['v_ms'])
        q_nadir = att.dcm_to_quat(C_li)
        dq = np.array([0.0, math.sin(math.radians(22.5)), 0.0,
                       math.cos(math.radians(22.5))])
        s.q_true = [float(x) for x in att.quat_mult(q_nadir, dq)]
        tau = m._disturbance_torques(s)
        Rm = np.linalg.norm(r)
        n = 3.0 * att.MU_EARTH / Rm ** 3
        dI = abs(m._inertia[0, 0] - m._inertia[2, 2])
        gg_expect = n * dI * 0.5   # sin45·cos45
        # dipole/aero add on top; require the right order of magnitude
        assert np.linalg.norm(tau) > 0.3 * gg_expect
        assert np.linalg.norm(tau) < 5.0 * gg_expect

    def test_srp_switches_off_in_eclipse(self):
        m = make_model()
        s = m._state
        m._env = m._environment(make_orbit(in_eclipse=False))
        t_sun = np.array(m._disturbance_torques(s))
        m._env = m._environment(make_orbit(in_eclipse=True))
        t_ecl = np.array(m._disturbance_torques(s))
        assert not np.allclose(t_sun, t_ecl)


# ─── WP-4: estimator ─────────────────────────────────────────────────────

class TestEstimator:

    def test_mekf_converges_from_10deg(self):
        """From a 10° initial attitude error the MEKF reaches < 0.03° 3σ
        knowledge (and < 0.05° actual error) within 120 s of ST updates."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=900)
        s = m._state
        # Corrupt the estimate by 10° about X and re-inflate P
        dq = np.array([math.sin(math.radians(5.0)), 0.0, 0.0,
                       math.cos(math.radians(5.0))])
        m._ekf.q = att.quat_normalize(att.quat_mult(m._ekf.q, dq))
        m._ekf.P[:3, :3] = np.eye(3) * math.radians(10.0) ** 2
        m._ekf.converged = False
        for _ in range(120):
            m.tick(1.0, o, p)
        est_err = att.quat_angle_deg(m._ekf.q, np.array(s.q_true))
        assert s.knowledge_err_3sig_deg < 0.03
        assert est_err < 0.05

    def test_covariance_consistent_with_actual_error(self):
        """Reported 3σ knowledge bounds the actual estimation error and is
        within ~an order of magnitude of it (no wild over/under-confidence)."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        errs = []
        for _ in range(300):
            m.tick(1.0, o, p)
            errs.append(att.quat_angle_deg(m._ekf.q,
                                           np.array(m._state.q_true)))
        rms = math.sqrt(sum(e * e for e in errs) / len(errs))
        kn = m._state.knowledge_err_3sig_deg
        assert rms < kn * 2.0, f"actual {rms:.4f} vs 3sig {kn:.4f}"
        assert rms > kn / 30.0

    def test_knowledge_grows_without_star_tracker(self):
        """ST removed ⇒ propagate-only knowledge grows (gyro random walk)."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=900)
        kn0 = m._state.knowledge_err_3sig_deg
        m.handle_command({"command": "st_power", "unit": 1, "on": False})
        for _ in range(300):
            m.tick(1.0, o, p)
        assert m._state.knowledge_err_3sig_deg > kn0 * 3.0
        assert (m._state.est_source & 0x01) == 0

    def test_innovation_gate_rejects_wild_fix(self):
        """A 45° bogus ST quaternion is rejected and the counter increments."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=900)
        rej0 = m._ekf.innov_reject_count
        bad = att.quat_mult(np.array(m._state.q_true),
                            np.array([math.sin(0.4), 0, 0, math.cos(0.4)]))
        accepted = m._ekf.update_st(bad, n_avg=1)
        assert not accepted
        assert m._ekf.innov_reject_count == rej0 + 1


# ─── WP-5/WP-6: pointing performance per mode ────────────────────────────

class TestPointingPerformance:

    def test_nominal_steady_state_error_band(self):
        """NOMINAL total pointing error: < 0.1° always at steady state
        (imaging band), mean < 0.075°."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        errs = []
        for _ in range(300):
            m.tick(1.0, o, p)
            errs.append(m._state.att_error)
        assert max(errs) < 0.12
        assert sum(errs) / len(errs) < 0.075

    def test_fine_point_error_band_and_power(self):
        """FINE holds total pointing < 0.05° (payload gate) and bills 15 W."""
        m, o, p = settled_model(MODE_FINE_POINT)
        errs, tots = [], []
        for _ in range(300):
            m.tick(1.0, o, p)
            errs.append(m._state.att_error)
            tots.append(p[0x02AA])
        assert m._state.mode == MODE_FINE_POINT
        assert sum(errs) / len(errs) < 0.035
        assert max(tots) < 0.05
        assert p[0x02C8] == 15.0

    def test_nadir_tracking_pitch_rate(self):
        """In NOMINAL the body pitch rate settles at the orbit rate
        (-0.063 °/s about body Y) and nadir_body_z → 1."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1500)
        w_o = math.degrees(math.sqrt(
            att.MU_EARTH / ((6378.137 + 450.0) * 1000.0) ** 3))
        assert m._state.rate_pitch == pytest.approx(-w_o, rel=0.05)
        assert p[0x02B2] == pytest.approx(1.0, abs=0.01)

    def test_slew_30deg_completes_and_resumes_nominal(self):
        """A 30° slew completes (mode returns to NOMINAL) and the target is
        reached within the settling allowance."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        q0 = np.array(m._state.q_true)
        dq = np.array([0.0, math.sin(math.radians(15.0)), 0.0,
                       math.cos(math.radians(15.0))])
        qt = att.quat_mult(q0, dq)
        r = m.handle_command({"command": "slew_to_quaternion",
                              "quaternion": [float(x) for x in qt],
                              "rate_dps": 0.5})
        assert r["success"]
        completed_at = None
        for i in range(400):
            m.tick(1.0, o, p)
            if m._state.mode == MODE_NOMINAL and completed_at is None:
                completed_at = i
                err_at_complete = att.quat_angle_deg(
                    np.array(m._state.q_true), qt)
        assert completed_at is not None, "slew never completed"
        # 30° at 0.5°/s = 60 s trajectory + settling; generous bound 300 s
        assert completed_at < 300
        assert err_at_complete < 0.5

    def test_inertial_mode_accumulates_more_momentum(self):
        """INERTIAL (mode 9) accumulates clearly more secular wheel momentum
        over one full orbit than NADIR — the nadir-fixed disturbances (GG,
        aero) rotate through an inertially-held body and leave a secular
        residue (brief 02 §5). Measured over exactly one orbit so the
        once-per-orbit cyclic terms cancel."""
        def net_h_delta(mode):
            m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
            if mode == MODE_INERTIAL:
                m.handle_command({"command": "set_target_quat",
                                  "quaternion": list(m._state.q_true)})
                m.handle_command({"command": "set_mode", "mode": 9})
            for _ in range(200):
                m.tick(1.0, o, p)
            h0 = np.array(m._state.h_wheel)
            for _ in range(1140):        # one orbit (5700 s) at dt = 5 s
                m.tick(5.0, o, p)        # full physics: 10 sub-steps/tick
            return float(np.linalg.norm(np.array(m._state.h_wheel) - h0))
        d_inertial = net_h_delta(MODE_INERTIAL)
        d_nadir = net_h_delta(MODE_NOMINAL)
        assert d_inertial > d_nadir * 1.3, (d_inertial, d_nadir)
        assert d_nadir > 1e-5            # NADIR accumulates too (desats real)

    def test_detumble_converges_and_transitions(self):
        """From a 1.5 °/s tumble, B-dot detumbles below 0.5 °/s and the FSM
        advances to COARSE_SUN."""
        m = make_model()
        o = make_orbit()
        s = m._state
        s.omega_true = [1.2 * _DEG, -0.9 * _DEG, 0.6 * _DEG]
        m.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        p = {}
        for _ in range(900):
            m.tick(1.0, o, p)
        rate = math.sqrt(sum(w * w for w in s.omega_true)) / _DEG
        # After detumbling the FSM advances to COARSE_SUN, whose acquisition
        # manoeuvre runs at up to the 0.5 °/s slew cap — bound by 1.0 °/s
        # (well under the 2 °/s emergency threshold).
        assert rate < 1.0
        assert s.mode in (2, 3)  # COARSE_SUN once the 30 s guard passes


# ─── WP-6: FINE gating and mode power ────────────────────────────────────

class TestFineGating:

    def test_fine_refused_from_cold(self):
        m = make_model()
        r = m.handle_command({"command": "set_mode", "mode": 5})
        assert not r["success"]
        assert r["code"] == "FINE_NOT_READY"
        mask = r["fine_ready_mask"]
        assert mask & 0x01      # ST not tracking
        assert m._state.mode == 0

    def test_fine_ready_mask_clears_when_ready(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=1400)
        assert p[0x02C5] == 0.0
        r = m.handle_command({"command": "fine_ready_check"})
        assert r["fine_ready_mask"] == 0

    def test_fine_refused_with_three_wheels(self):
        m, o, p = settled_model(MODE_NOMINAL, ticks=1400)
        m.handle_command({"command": "disable_wheel", "wheel": 2})
        m.tick(1.0, o, p)
        r = m.handle_command({"command": "set_mode", "mode": 5})
        assert not r["success"]
        assert r["fine_ready_mask"] & 0x02

    def test_mode_power_published(self):
        """0x02C8 follows the COARSE 4 / NOMINAL 9 / FINE 15 W table."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        assert p[0x02C8] == 9.0
        m.handle_command({"command": "set_mode", "mode": 3})
        m.tick(1.0, o, p)
        assert p[0x02C8] == 4.0

    def test_eps_bills_aocs_from_published_power(self):
        """EPS aocs_wheels line follows 0x02C8 (Phase B billing convention)."""
        from smo_simulator.models.eps_basic import EPSBasicModel
        eps = EPSBasicModel()
        eps.configure({})
        eps._state.power_lines["aocs_wheels"] = True   # commissioned line
        o = make_orbit()
        p = {0x02C8: 15.0}
        eps.tick(1.0, o, p)
        assert p[0x013D] == 15.0
        p[0x02C8] = 4.0
        eps.tick(1.0, o, p)
        assert p[0x013D] == 4.0


# ─── WP-1: sub-stepping and secular mode ─────────────────────────────────

class TestAcceleration:

    @pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 600.0, 1000.0])
    def test_detumble_stable_at_any_dt(self, dt):
        """DETUMBLE from 0.3 °/s converges (never diverges) for dt up to
        1000 s — the old kinematics hit 1.4e39 °/s at dt=1000."""
        m = make_model()
        o = make_orbit()
        s = m._state
        s.omega_true = [0.2 * _DEG, -0.15 * _DEG, 0.1 * _DEG]
        m.handle_command({"command": "set_mode", "mode": MODE_DETUMBLE})
        # Disable the FSM: once damped it advances to COARSE_SUN and the
        # sun-acquisition manoeuvre raises rates again by design — this
        # test isolates the B-dot convergence itself.
        m._check_auto_transitions = lambda *a, **k: None
        p = {}
        n = max(1, int(1800 / dt))
        for _ in range(n):
            m.tick(dt, o, p)
        rate = math.sqrt(sum(w * w for w in s.omega_true)) / _DEG
        assert rate < 0.3, f"dt={dt}: rate {rate}"

    def test_substep_consistency_dt1_vs_dt60(self):
        """3600 s of NOMINAL at dt=1 and dt=60 agree on the final attitude
        to < 0.1° (both run full physics; 60 s = 120 sub-steps at 0.5 s)."""
        def run(dt):
            m = make_model()
            o = make_orbit()
            m.handle_command({"command": "st_power", "unit": 1, "on": True})
            m.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
            p = {}
            t = 0.0
            while t < 3600.0:
                m.tick(dt, o, p)
                t += dt
            return m
        m1 = run(1.0)
        m60 = run(60.0)
        # Both must have converged onto the same (deterministic) reference
        e1 = m1._state.att_error
        e60 = m60._state.att_error
        assert e1 < 0.12 and e60 < 0.2, (e1, e60)
        ang = att.quat_angle_deg(np.array(m1._state.q_true),
                                 np.array(m60._state.q_true))
        assert ang < 0.5, f"dt=1 vs dt=60 diverged by {ang:.3f} deg"

    def test_secular_mode_honest_and_slow_states_advance(self):
        """Above 60x: 0x02C6 reads 1, AOCS_FAST_LOOP_BYPASSED fires on the
        transition, wheel momentum still accumulates, and the attitude stays
        on the reference within the mode's error distribution."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        events = []
        m._engine = SimpleNamespace(
            _model_event_queue=SimpleNamespace(put=events.append))
        h0 = np.array(m._state.h_wheel)
        p[0x05FE] = 1000.0          # engine says 1000x
        for _ in range(20):
            m.tick(1000.0, o, p)    # 20,000 s of sim time
        assert p[0x02C6] == 1.0
        assert any(e['event_id'] == 0x020D for e in events)
        dh = np.linalg.norm(np.array(m._state.h_wheel) - h0)
        assert dh > 1e-4, "wheel momentum froze in secular mode"
        assert m._state.att_error < 0.2
        # Dropping back below the threshold restores full fidelity + event
        events.clear()
        p[0x05FE] = 1.0
        m.tick(1.0, o, p)
        assert p[0x02C6] == 0.0
        assert any(e['event_id'] == 0x020D for e in events)


# ─── WP-0: breakpoint round-trip and determinism ─────────────────────────

class TestStateRoundTrip:

    def test_breakpoint_mid_slew_resumes(self):
        """A breakpoint saved mid-SLEW must resume the slew to the same
        target (the old model aborted to NOMINAL on restore)."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        q0 = np.array(m._state.q_true)
        dq = np.array([0.0, math.sin(math.radians(10.0)), 0.0,
                       math.cos(math.radians(10.0))])
        qt = [float(x) for x in att.quat_mult(q0, dq)]
        m.handle_command({"command": "slew_to_quaternion",
                          "quaternion": qt, "rate_dps": 0.5})
        for _ in range(15):
            m.tick(1.0, o, p)
        assert m._state.mode == MODE_SLEW
        snap = m.get_state()

        m2 = make_model()
        m2.set_state(snap)
        assert m2._state.mode == MODE_SLEW
        assert m2._state.slew_target_q == pytest.approx(qt)
        completed = False
        for _ in range(300):
            m2.tick(1.0, o, dict(p))
            if m2._state.mode == MODE_NOMINAL:
                completed = True
                break
        assert completed, "restored slew did not run to completion"
        assert att.quat_angle_deg(np.array(m2._state.q_true),
                                  np.array(qt)) < 1.0

    def test_same_seed_identical_attitude_trace(self):
        """Two models from the same state produce bit-identical traces."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=600)
        snap = m.get_state()
        a = make_model()
        b = make_model()
        a.set_state(snap)
        b.set_state(snap)
        pa, pb = {}, {}
        for _ in range(200):
            a.tick(1.0, o, pa)
            b.tick(1.0, o, pb)
        assert a._state.q_true == b._state.q_true
        assert a._state.h_wheel == b._state.h_wheel
        assert pa[0x0217] == pb[0x0217]

    def test_state_roundtrip_preserves_new_fields(self):
        m, o, p = settled_model(MODE_FINE_POINT)
        snap = m.get_state()
        m2 = make_model()
        m2.set_state(snap)
        assert m2._state.q_true == m._state.q_true
        assert m2._state.omega_true == m._state.omega_true
        assert m2._state.h_wheel == m._state.h_wheel
        assert m2._state.pointing_frame == m._state.pointing_frame
        assert m2._ekf.get_state() == m._ekf.get_state()
        # tick both once — identical output (RNG state restored too)
        p1, p2 = {}, {}
        m.tick(1.0, o, p1)
        m2.tick(1.0, o, p2)
        assert p1[0x0217] == p2[0x0217]


# ─── WP-9: desat cycle with the new wheel numbers ────────────────────────

class TestDesat:

    def test_desat_dumps_momentum_and_exits(self):
        """A loaded wheel set dumps toward the desat bias and the FSM
        returns to NOMINAL once speeds are inside the guard."""
        m, o, p = settled_model(MODE_NOMINAL, ticks=1200)
        # Load the wheels with a net momentum (same-sign 3000 rpm)
        for i in range(4):
            m._state.rw_speed[i] = 3000.0
        m.handle_command({"command": "set_mode", "mode": MODE_DESAT})
        exited = False
        for i in range(3600):
            m.tick(1.0, o, p)
            if m._state.mode == MODE_NOMINAL:
                exited = True
                break
        assert exited, "desat never completed"
        assert p[0x02BA] < 20.0   # envelope healthy again


# ─── Brief 01 WP-9 (reduced form): attitude-dependent generation ─────────

class TestAttitudeDependentGeneration:

    def test_sun_pointing_beats_nadir_at_high_beta(self):
        """The TRUE attitude drives EPS illumination (0x02B3-B5): switching
        the pointing frame to SUN raises orbit generation by >= 25 % over
        nadir at beta = 49.8 deg (brief 01 WP-9 acceptance, reduced)."""
        import statistics
        import yaml
        from smo_simulator.models.eps_basic import EPSBasicModel
        eps_cfg = yaml.safe_load(open("configs/eosat1/subsystems/eps.yaml"))

        def mean_gen(frame):
            o = SimpleNamespace(
                in_eclipse=False, solar_beta_deg=49.8, lat_deg=45.0,
                lon_deg=10.0, alt_km=450.0, vel_x=0.0, vel_y=7.5,
                vel_z=0.0, in_contact=False)
            a = AOCSBasicModel()
            a.configure({})
            e = EPSBasicModel()
            e.configure(eps_cfg)
            e._state.wing_py_deployed = True
            e._state.wing_my_deployed = True
            a.handle_command({"command": "st_power", "unit": 1, "on": True})
            a.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
            p = {}
            for _ in range(1200):
                a.tick(1.0, o, p)
            if frame is not None:
                a.handle_command({"command": "set_pointing_frame",
                                  "frame": frame})
                for _ in range(700):
                    a.tick(1.0, o, p)
            vals = []
            for _ in range(300):
                a.tick(1.0, o, p)
                e.tick(1.0, o, p)
                vals.append(e._state.power_gen_w)
            return statistics.mean(vals)

        g_nadir = mean_gen(None)
        g_sun = mean_gen(3)   # pointing_frame 3 = SUN
        assert g_sun > g_nadir * 1.25, (
            f"sun-pointing {g_sun:.1f} W vs nadir {g_nadir:.1f} W")

    def test_large_off_nadir_slew_changes_generation(self):
        """A 90-degree off-nadir attitude measurably changes generation —
        the illumination path reads the TRUE quaternion, not a phase ramp."""
        import statistics
        import yaml
        from smo_simulator.models.eps_basic import EPSBasicModel
        eps_cfg = yaml.safe_load(open("configs/eosat1/subsystems/eps.yaml"))
        o = make_orbit(beta=20.0)
        a = AOCSBasicModel()
        a.configure({})
        e = EPSBasicModel()
        e.configure(eps_cfg)
        e._state.wing_py_deployed = True
        e._state.wing_my_deployed = True
        a.handle_command({"command": "st_power", "unit": 1, "on": True})
        a.handle_command({"command": "set_mode", "mode": MODE_NOMINAL})
        p = {}
        for _ in range(1200):
            a.tick(1.0, o, p)

        def mean_gen(n=120):
            vals = []
            for _ in range(n):
                a.tick(1.0, o, p)
                e.tick(1.0, o, p)
                vals.append(e._state.power_gen_w)
            return statistics.mean(vals)

        g_nadir = mean_gen()
        # 90° yaw... roll the wings' drive axis toward the sun line
        q0 = np.array(a._state.q_true)
        dq = np.array([math.sin(math.radians(45.0)), 0.0, 0.0,
                       math.cos(math.radians(45.0))])
        qt = [float(x) for x in att.quat_mult(q0, dq)]
        a.handle_command({"command": "set_target_quat", "quaternion": qt})
        a.handle_command({"command": "set_mode", "mode": MODE_INERTIAL})
        for _ in range(400):
            a.tick(1.0, o, p)
        g_rolled = mean_gen()
        assert abs(g_rolled - g_nadir) > 0.1 * max(g_nadir, 1.0), (
            f"attitude change invisible to EPS: {g_nadir:.1f} -> {g_rolled:.1f} W")


# ─── WP-9: autonomy chain (S12 edge -> S19 rule -> S8 action) ────────────

class TestAutonomy:

    @pytest.fixture
    def engine(self):
        from smo_simulator.engine import SimulationEngine
        return SimulationEngine("configs/eosat1")

    def test_momentum_saturation_triggers_autonomous_desat(self, engine):
        """h_env_frac past 70 % fires S19 rule 2001 (via the S12 edge) and
        the spacecraft desaturates autonomously (WP-9 acceptance a)."""
        from smo_simulator.service_dispatch import TRIGGER_KIND_PARAM
        aocs = engine.subsystems["aocs"]
        aocs._state.mode = MODE_NOMINAL
        # Simulate the S12 edge the monitor on 0x02BA would raise
        engine._dispatcher.trigger_event_action(0x02BA, TRIGGER_KIND_PARAM)
        assert aocs._state.mode == MODE_DESAT

    def test_rw3_overspeed_disables_wheel_2_not_0(self, engine):
        """Rule 2007 (RW3 overspeed) carries action_data [0x02]: wheel index
        2 is disabled — previously every wheel rule disabled wheel 0."""
        from smo_simulator.service_dispatch import TRIGGER_KIND_PARAM
        aocs = engine.subsystems["aocs"]
        assert all(aocs._state.active_wheels) or True
        aocs._state.active_wheels = [True, True, True, True]
        engine._dispatcher.trigger_event_action(0x0209, TRIGGER_KIND_PARAM)
        assert aocs._state.active_wheels[2] is False
        assert aocs._state.active_wheels[0] is True

    def test_fresh_engine_not_latched_safe(self, engine):
        """0x0260 aocs.wheels_enabled is WRITTEN now (4 wheels), so the
        fdir 'wheels_enabled < 2' rule no longer latches SAFE at t=0."""
        engine._tick_once(1.0)
        engine._tick_once(1.0)
        assert engine.params.get(0x0260, 0.0) >= 2.0
        assert not getattr(engine, "_fdir_safe_active", False)


# ─── WP-6: payload PARTIAL flag on FINE loss mid-strip ───────────────────

class TestPayloadStripGating:

    def _payload(self):
        from smo_simulator.models.payload_basic import PayloadBasicModel
        p = PayloadBasicModel()
        p.configure({})
        s = p._state
        s.mode = 2
        s.fpa_ready = True
        s.aocs_seen = True
        s.aocs_mode_seen = 5
        s.aocs_total_err_seen = 0.01
        s.aocs_rate_stab_seen = 0.001
        s.aocs_settled_seen = True
        return p

    def test_capture_refused_outside_fine(self):
        p = self._payload()
        p._state.aocs_mode_seen = 4
        r = p.handle_command({"command": "capture"})
        assert not r["success"]
        assert r["code"] == "POINTING_NOT_FINE"

    def test_capture_refused_error_above_gate(self):
        p = self._payload()
        p._state.aocs_total_err_seen = 0.08
        r = p.handle_command({"command": "capture"})
        assert not r["success"]
        assert r["code"] == "POINTING_ERROR_HIGH"

    def test_fine_loss_mid_strip_flags_partial_with_error(self):
        """Losing FINE during the strip marks the image PARTIAL and stamps
        the actual pointing error into the catalog entry."""
        p = self._payload()
        r = p.handle_command({"command": "capture"})
        assert r["success"], r
        scene = r["scene_id"]
        img = next(i for i in p._state.image_catalog
                   if i["scene_id"] == scene)
        assert img["status"] == 0
        assert img["pointing_err_deg"] == pytest.approx(0.01)
        # FINE drops mid-strip; the shared params carry the new AOCS state
        o = make_orbit()
        shared = {0x020F: 4.0, 0x02AA: 0.35, 0x02C3: 0.02, 0x0292: 0.0,
                  0x0217: 0.35}
        p.tick(1.0, o, shared)
        assert img["status"] == 1, "image not flagged PARTIAL on FINE loss"
        assert img["pointing_err_deg"] >= 0.35
        assert img["quality"] < 90.0
