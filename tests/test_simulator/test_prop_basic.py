"""Phase D — propulsion model unit tests (brief 03 WP-2).

Standalone PropBasicModel driven with controlled shared parameters: state
machine + every arm interlock (distinct rejection codes), blowdown physics
against the rocket equation, attitude-error cross-coupling, sub-stepping at
extreme dt, and bit-identical state round-trip mid-burn.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from smo_simulator.models.prop_basic import (
    PropBasicModel, ST_SAFED, ST_ARMED, ST_FIRING, ST_INHIBIT,
    AB_ATT_ERR, AB_GROUND, VLV_CLOSED, VLV_OPEN, G0,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


class FakeOrbitState:
    def __init__(self):
        import numpy as np
        self.in_eclipse = False
        self.pos_eci = np.array([6828.0, 0.0, 0.0])
        self.vel_eci = np.array([0.0, 7.64, 0.0])
        from datetime import datetime, timezone
        self.utc = datetime(2026, 3, 10, tzinfo=timezone.utc)


GOOD_PARAMS = {
    0x0409: 200.0,   # catbed temp
    0x020F: 4,       # AOCS NOMINAL
    0x0217: 0.10,    # att error
    0x0101: 90.0,    # SoC
    0x0129: 6,       # phase NOMINAL
    0x014A: 1,       # prop line ON
}


def make_model() -> PropBasicModel:
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "prop.yaml").read_text())
    m = PropBasicModel()
    m.configure(cfg)
    return m


def tick(m, sp=None, dt=1.0, n=1, eclipse=False):
    os_ = FakeOrbitState()
    os_.in_eclipse = eclipse
    params = dict(GOOD_PARAMS)
    if sp:
        params.update(sp)
    for _ in range(n):
        m.tick(dt, os_, params)
    return params


def load_and_arm(m, dv=0.85, direction=0, mode=1):
    tick(m)
    r = m.handle_command({"command": "set_burn", "dv_m_s": dv,
                          "direction": direction, "mode": mode})
    assert r["success"], r
    r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
    assert r["success"], r
    return m


class TestStateMachine:

    def test_initial_state_safed(self):
        m = make_model()
        assert m._state.state == ST_SAFED
        assert m._state.m_prop_kg == pytest.approx(0.400)
        assert m._state.tank_p_bar == pytest.approx(22.0, abs=0.05)

    def test_full_nominal_cycle(self):
        m = make_model()
        load_and_arm(m)
        assert m._state.state == ST_ARMED
        assert m.handle_command({"command": "execute"})["success"]
        assert m._state.state == ST_FIRING
        # run the burn out
        for _ in range(60):
            tick(m)
            if m._state.state != ST_FIRING:
                break
        assert m._state.state == ST_ARMED          # nominal completion -> ARMED
        assert m._state.burn_status == 3
        assert m.handle_command({"command": "disarm"})["success"]
        assert m._state.state == ST_SAFED

    def test_execute_without_arm_refused_with_code(self):
        m = make_model()
        tick(m)
        r = m.handle_command({"command": "execute"})
        assert not r["success"]
        assert r["error_code"] == 0x0B11

    def test_ground_abort_mid_burn(self):
        m = make_model()
        load_and_arm(m)
        m.handle_command({"command": "execute"})
        tick(m, n=3)
        m.handle_command({"command": "abort"})
        s = m._state
        assert s.state == ST_SAFED
        assert s.burn_status == 4
        assert s.abort_reason == AB_GROUND
        # partial dv banked as a pending impulse
        assert s.pending_impulse is not None
        assert s.pending_impulse["dv_mag_m_s"] > 0.0

    def test_double_abort_latches_inhibit(self):
        m = make_model()
        for _ in range(2):
            load_and_arm(m)
            m.handle_command({"command": "execute"})
            tick(m)
            m.handle_command({"command": "abort"})
            m.consume_pending_impulse()
        assert m._state.state == ST_INHIBIT
        assert m._state.inhibit_latched
        # arming refused while latched
        m.handle_command({"command": "set_burn", "dv_m_s": 0.2})
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"] and r["error_code"] == 0x0B0A
        # only the keyed ground clear releases it
        assert not m.handle_command({"command": "inhibit_clr", "key": 1})["success"]
        assert m.handle_command({"command": "inhibit_clr",
                                 "key": 0xA5C3})["success"]
        assert m._state.state == ST_SAFED

    def test_iso_close_both_blocks_arm(self):
        m = make_model()
        tick(m)
        m.handle_command({"command": "iso_close", "branch": 2})
        assert m._state.latch_a == VLV_CLOSED
        assert m._state.latch_b == VLV_CLOSED
        m.handle_command({"command": "set_burn", "dv_m_s": 0.2})
        tick(m)
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"] and r["error_code"] == 0x0B09
        m.handle_command({"command": "iso_open", "branch": 0})
        assert m._state.latch_a == VLV_OPEN


class TestArmInterlocks:
    """Each interlock refuses PROP_ARM with its own distinct code and its
    bit is visible in the 0x0B15 mask (brief 03 WP-2a)."""

    CASES = [
        ("cold_catbed", {0x0409: 100.0}, 0x0B01),
        ("aocs_mode", {0x020F: 2}, 0x0B04),
        ("att_error", {0x0217: 3.5}, 0x0B05),
        ("low_soc", {0x0101: 30.0}, 0x0B06),
        ("phase", {0x0129: 4}, 0x0B07),
    ]

    @pytest.mark.parametrize("name,perturb,code",
                             CASES, ids=[c[0] for c in CASES])
    def test_single_interlock_refuses(self, name, perturb, code):
        m = make_model()
        tick(m, sp=perturb)
        m.handle_command({"command": "set_burn", "dv_m_s": 0.5})
        tick(m, sp=perturb)
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"], f"{name}: arm must be refused"
        assert r["error_code"] == code, f"{name}: wrong code {r}"
        assert m._state.state == ST_SAFED

    def test_eclipse_interlock(self):
        m = make_model()
        os_ = FakeOrbitState(); os_.in_eclipse = True
        m.tick(1.0, os_, dict(GOOD_PARAMS))
        m.handle_command({"command": "set_burn", "dv_m_s": 0.5})
        m.tick(1.0, os_, dict(GOOD_PARAMS))
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"] and r["error_code"] == 0x0B08

    def test_no_burn_loaded(self):
        m = make_model()
        tick(m)
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"] and r["error_code"] == 0x0B0B

    def test_stale_burn_outside_arm_window(self):
        m = make_model()
        tick(m)
        m.handle_command({"command": "set_burn", "dv_m_s": 0.5})
        tick(m, dt=60.0, n=6)   # 360 s > 300 s window
        r = m.handle_command({"command": "arm", "arm_code": 0xA5C3})
        assert not r["success"] and r["error_code"] == 0x0B0B

    def test_bad_arm_code(self):
        m = make_model()
        tick(m)
        m.handle_command({"command": "set_burn", "dv_m_s": 0.5})
        r = m.handle_command({"command": "arm", "arm_code": 0x1234})
        assert not r["success"] and r["error_code"] == 0x0B0F

    def test_cold_catbed_named_in_mask(self):
        m = make_model()
        tick(m, sp={0x0409: 20.0})
        assert int(m._state.arm_inhibit_mask) & 0x001, \
            "catbed bit must be set in 0x0B15 while the bed is cold"


class TestBurnPhysics:

    def test_dv_against_rocket_equation(self):
        """0.85 m/s BOL burn: ~32 s, ~4.9 g, and the achieved dv must match
        Tsiolkovsky on the actually-consumed propellant (WP-2b)."""
        m = make_model()
        load_and_arm(m, dv=0.85, mode=1)
        m.handle_command({"command": "execute"})
        for _ in range(80):
            tick(m)
            if m._state.state != ST_FIRING:
                break
        s = m._state
        assert s.burn_status == 3
        assert s.burn_dv_achieved_m_s == pytest.approx(0.85, abs=0.005)
        assert s.burn_elapsed_s == pytest.approx(31.9, abs=1.0)
        used_kg = 0.400 - s.m_prop_kg
        assert used_kg * 1e3 == pytest.approx(4.9, abs=0.3)
        v_e = 220.0 * G0     # Isp is ~BOL over one small burn
        dv_pred = v_e * math.log(12.40 / (12.40 - used_kg))
        assert s.burn_dv_achieved_m_s == pytest.approx(dv_pred, rel=0.01)

    def test_blowdown_curve_monotonic_to_eol(self):
        """P follows the isothermal blowdown from 22.0 to 9.33 bar as the
        full 400 g is expelled, monotonically (WP-2c)."""
        m = make_model()
        p_prev = m._tank_pressure(0.400)
        assert p_prev == pytest.approx(22.0, abs=0.01)
        for g in range(400, -1, -20):
            p = m._tank_pressure(g / 1000.0)
            assert p <= p_prev + 1e-9
            p_prev = p
        assert m._tank_pressure(0.0) == pytest.approx(9.33, abs=0.02)
        # thrust and Isp ride the pressure
        assert m._thrust_n(22.0) == pytest.approx(0.33, abs=0.005)
        assert m._thrust_n(9.33) == pytest.approx(0.14, abs=0.005)
        assert m._isp_s(22.0) == pytest.approx(220.0, abs=0.5)
        assert m._isp_s(9.33) == pytest.approx(196.0, abs=0.5)

    def test_dv_remaining_is_total_capability_at_bol(self):
        m = make_model()
        assert m.dv_remaining_m_s() == pytest.approx(66.9, abs=1.5)

    def test_attitude_error_costs_cos_theta(self):
        """att_error = 30 deg: along-track dv = 0.866x, lateral non-zero
        (WP-2d)."""
        m = make_model()
        # widen the on-board abort limit so the 30-deg case integrates
        m.handle_command({"command": "set_limits", "att_err_max_deg": 45.0})
        tick(m, sp={0x0217: 30.0})
        m.handle_command({"command": "set_burn", "dv_m_s": 1.0, "mode": 1})
        tick(m, sp={0x0217: 30.0})
        # arm gate would refuse 30 deg; bypass the gate to test the physics
        m._state.state = ST_FIRING
        m._state.burn_status = 2
        m._state.fcv_open = True
        for _ in range(80):
            tick(m, sp={0x0217: 30.0})
            if m._state.state != ST_FIRING:
                break
        imp = m._state.pending_impulse
        assert imp is not None
        s = m._state
        assert s.burn_dv_achieved_m_s == pytest.approx(1.0, abs=0.01)
        # the components were accumulated with cos/sin(30 deg)
        assert imp["dv_mag_m_s"] == pytest.approx(1.0, abs=0.01)
        import numpy as np
        dv = np.array(imp["dv_eci_km_s"]) * 1e3
        v_hat = np.array([0.0, 1.0, 0.0])
        along = float(abs(np.dot(dv, v_hat)))
        lateral = float(np.linalg.norm(dv - np.dot(dv, v_hat) * v_hat))
        assert along == pytest.approx(0.866, abs=0.01)
        assert lateral == pytest.approx(0.5, abs=0.01)

    def test_abort_on_attitude_error_mid_burn(self):
        m = make_model()
        load_and_arm(m, dv=3.0, mode=1)
        m.handle_command({"command": "execute"})
        tick(m, n=3)
        assert m._state.state == ST_FIRING
        tick(m, sp={0x0217: 5.0})   # above the 2.0 deg on-board limit
        s = m._state
        assert s.state == ST_SAFED
        assert s.burn_status == 4
        assert s.abort_reason == AB_ATT_ERR
        assert s.pending_impulse is not None, "partial dv must still apply"

    def test_giant_dt_substeps_keep_dv_accurate(self):
        """At speed 1000x a tick can be 1000 s; the internal <= ~1 s
        sub-stepping must keep the total dv error < 0.5 % (WP-2e)."""
        m = make_model()
        load_and_arm(m, dv=3.0, mode=1)
        m.handle_command({"command": "execute"})
        tick(m, dt=1000.0, n=1)
        s = m._state
        assert s.state == ST_ARMED
        assert s.burn_dv_achieved_m_s == pytest.approx(3.0, rel=0.005)

    def test_retrograde_flips_the_impulse(self):
        m = make_model()
        load_and_arm(m, dv=0.5, direction=1, mode=1)
        m.handle_command({"command": "execute"})
        for _ in range(80):
            tick(m)
            if m._state.state != ST_FIRING:
                break
        import numpy as np
        dv = np.array(m._state.pending_impulse["dv_eci_km_s"]) * 1e3
        assert dv[1] < 0, "retrograde burn must oppose +Y velocity"


class TestFailuresAndTelemetry:

    def test_leak_depletes_and_publishes(self):
        m = make_model()
        m.inject_failure("leak", rate_mg_s=50.0)
        sp = tick(m, dt=60.0, n=10)
        assert sp[0x0B16] == pytest.approx(50.0)
        assert m._state.m_prop_kg < 0.400 - 0.02
        m.clear_failure("leak")
        assert m._state.leak_rate_mg_s == 0.0

    def test_thrust_degraded_scales_estimate(self):
        m = make_model()
        m.inject_failure("thrust_degraded", magnitude=0.55)
        sp = tick(m)
        assert sp[0x0B09] == pytest.approx(0.55 * 330.0, rel=0.02)

    def test_stuck_valve_reports_fault_and_resists_close(self):
        m = make_model()
        m.inject_failure("latch_stuck_open", branch=0)
        tick(m)
        r = m.handle_command({"command": "iso_close", "branch": 0})
        assert not r["success"] and r["error_code"] == 0x0B12
        sp = tick(m)
        assert sp[0x0B06] == 2.0   # fault
        assert sp[0x0B16] > 0.0    # seep

    def test_all_block_params_published(self):
        m = make_model()
        sp = tick(m)
        for pid in range(0x0B00, 0x0B1C):
            assert pid in sp, f"0x{pid:04X} not published"

    def test_state_round_trip_mid_burn_bit_identical(self):
        """get_state/set_state round-trip is exact for an armed system
        mid-burn (WP-2f)."""
        m = make_model()
        load_and_arm(m, dv=2.0, mode=1)
        m.handle_command({"command": "execute"})
        tick(m, n=7)
        assert m._state.state == ST_FIRING
        snap = m.get_state()
        m2 = make_model()
        m2.set_state(snap)
        assert m2.get_state() == snap
        # both continue identically
        for a, b in zip(range(60), range(60)):
            tick(m); tick(m2)
            assert m2._state.burn_dv_mag_m_s == m._state.burn_dv_mag_m_s
            if m._state.state != ST_FIRING:
                break
        assert m2._state.state == m._state.state
        assert m2._state.m_prop_kg == m._state.m_prop_kg
