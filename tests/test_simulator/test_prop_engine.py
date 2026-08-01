"""Phase D — propulsion / orbit-control engine integration (brief 03 WP-1/3/5).

Full-engine tests: a real burn genuinely changes the orbit (epoch_version,
SMA, contact windows), the speed clamp holds during burns, shedding the EPS
prop line aborts the burn, S12/S19 autonomy fires, EPS bills the actual
actuator state, breakpoints round-trip a manoeuvred orbit AND a mid-burn
system, and same-seed runs are deterministic.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from smo_simulator.engine import SimulationEngine
from smo_simulator.breakpoints import BreakpointManager

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def make_engine(speed=1.0) -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip("eosat1 config dir missing")
    eng = SimulationEngine(CONFIG_DIR, speed=speed)
    eng.subsystems["obdh"]._state.sw_image = 1
    for _ in range(4):
        eng._tick_once(1.0)
    eng._spacecraft_phase = 6
    eps = eng.subsystems["eps"]
    eps._state.power_lines["prop"] = True
    eps._state.power_lines["aocs_wheels"] = True
    return eng


def hold_attitude(eng):
    """Pin AOCS to a converged NOMINAL hold so the burn gate passes."""
    aocs = eng.subsystems["aocs"]
    aocs._state.mode = 4
    aocs._state.q_true = list(aocs._state.q_ref)
    aocs._state.omega_true = [0.0, 0.0, 0.0]
    eng.subsystems["tcs"]._state.temp_thruster = max(
        eng.subsystems["tcs"]._state.temp_thruster, 200.0)


def settle(eng, n=2):
    for _ in range(n):
        hold_attitude(eng)
        eng._tick_once(1.0)


def start_burn(eng, dv=0.85, mode=1):
    settle(eng, 2)
    prop = eng.subsystems["prop"]
    r = prop.handle_command({"command": "set_burn", "dv_m_s": dv,
                             "direction": 0, "mode": mode})
    assert r["success"], r
    r = prop.handle_command({"command": "arm", "arm_code": 0xA5C3})
    assert r["success"], r
    r = prop.handle_command({"command": "execute"})
    assert r["success"], r
    return prop


def run_burn_out(eng, prop, max_ticks=300):
    n = 0
    while prop._state.state == 2 and n < max_ticks:
        hold_attitude(eng)
        eng._tick_once(1.0)
        n += 1
    return n


def drain_events(eng):
    out = []
    while True:
        try:
            out.append(eng.event_queue.get_nowait())
        except Exception:
            break
    return out


class TestOrbitChange:

    def test_burn_bumps_epoch_version_and_sma(self):
        eng = make_engine()
        assert eng.orbit.epoch_version == 0
        sma0 = eng.orbit.semi_major_axis_km()
        prop = start_burn(eng, dv=0.85)
        run_burn_out(eng, prop)
        assert prop._state.burn_status == 3
        assert eng.orbit.epoch_version == 1
        da = eng.orbit.semi_major_axis_km() - sma0
        # da = 2 a dv / v: 1.79 km per m/s -> ~1.52 km for 0.85 m/s
        assert da == pytest.approx(1.79 * 0.85, abs=0.15)
        # published telemetry follows on the next tick (impulse is applied
        # in the engine coupling AFTER the prop model publishes)
        settle(eng, 1)
        assert int(eng.params[0x0B19]) == 1
        assert eng.params[0x0B1A] == pytest.approx(sma0 + da, abs=0.1)

    def test_pass_times_shift_after_prograde_burn(self):
        """The feature is not cosmetic: contact windows genuinely move
        (brief 03 WP-1c). A prograde burn slows the ground track; AOS times
        drift later by tens of seconds per day."""
        eng = make_engine()
        before = eng.orbit.contact_windows(duration_s=86400.0, step_s=10.0)
        prop = start_burn(eng, dv=1.0)
        run_burn_out(eng, prop)
        assert eng.orbit.epoch_version == 1
        after = eng.orbit.contact_windows(duration_s=86400.0, step_s=10.0)
        assert before and after
        # Compare the LAST few windows (a ~24 h horizon accumulates ~34 s)
        shifts = []
        for wb, wa in zip(before[-4:], after[-4:]):
            if wb["gs_name"] != wa["gs_name"]:
                continue
            tb = datetime.fromisoformat(wb["aos"])
            ta = datetime.fromisoformat(wa["aos"])
            shifts.append((ta - tb).total_seconds())
        assert shifts, "no comparable windows"
        mean_shift = sum(shifts) / len(shifts)
        assert 15.0 <= mean_shift <= 60.0, (
            f"expected a ~20-45 s AOS delay after ~1 day, got {mean_shift}")

    def test_api_state_serves_elset_and_version(self):
        eng = make_engine()
        s = eng.get_state_summary()
        assert s["orbit_epoch_version"] == 0
        assert s["orbit_elset"] is None
        prop = start_burn(eng, dv=0.5)
        run_burn_out(eng, prop)
        s = eng.get_state_summary()
        assert s["orbit_epoch_version"] == 1
        el = s["orbit_elset"]
        assert el and el["tle_line1"].startswith("1 ") \
            and el["tle_line2"].startswith("2 ")


class TestSpeedClampAndShed:

    def test_speed_clamped_during_burn_with_event(self):
        eng = make_engine()
        prop = start_burn(eng, dv=3.0)
        drain_events(eng)
        eng.speed = 200.0
        hold_attitude(eng)
        eng._tick_once(1.0)
        assert eng.speed == pytest.approx(10.0), \
            "speed must clamp to max_speed_during_burn while FIRING"
        evs = drain_events(eng)
        assert any(e.get("event_id") == 0x0811 for e in evs), \
            "PROP_SPEED_CLAMPED event must fire on the clamp edge"
        # clamp releases after the burn
        run_burn_out(eng, prop)
        eng.speed = 200.0
        hold_attitude(eng)
        eng._tick_once(1.0)
        assert eng.speed == pytest.approx(200.0)

    def test_shedding_prop_line_aborts_burn(self):
        """Dropping the EPS prop line mid-burn = abort, reason 4 (WP-3c)."""
        eng = make_engine()
        prop = start_burn(eng, dv=3.0)
        settle(eng, 2)
        assert prop._state.state == 2
        eng.subsystems["eps"]._state.power_lines["prop"] = False
        settle(eng, 2)
        s = prop._state
        assert s.state in (0, 3)          # SAFED (or INHIBIT if 2nd abort)
        assert s.burn_status == 4
        assert s.abort_reason == 4
        # the partial dv still perturbed the orbit
        assert eng.orbit.epoch_version == 1


class TestAutonomy:

    def test_att_error_violation_aborts_within_two_ticks(self):
        """S12 monitor on 0x0217 -> S19 rule 8001 -> PROP_ABORT, plus the
        model's own 2-deg abort: the burn must die within 2 ticks of the
        attitude error crossing the limit (brief 03 WP-5d)."""
        eng = make_engine()
        prop = start_burn(eng, dv=3.0)
        settle(eng, 2)
        assert prop._state.state == 2
        # Blind the attitude: inject a big error via the truth quaternion
        aocs = eng.subsystems["aocs"]
        tcs = eng.subsystems["tcs"]
        for _ in range(2):
            aocs._state.mode = 4
            q = np.array(aocs._state.q_ref, float)
            # rotate 10 deg about body Z away from the reference
            dq = np.array([0.0, 0.0, math.sin(math.radians(5)),
                           math.cos(math.radians(5))])
            x, y, z, w = q; dx, dy, dz, dw = dq
            aocs._state.q_true = [
                w*dx + x*dw + y*dz - z*dy,
                w*dy - x*dz + y*dw + z*dx,
                w*dz + x*dy - y*dx + z*dw,
                w*dw - x*dx - y*dy - z*dz]
            tcs._state.temp_thruster = 200.0
            eng._tick_once(1.0)
            if prop._state.state != 2:
                break
        assert prop._state.state != 2, "burn must abort on attitude error"
        assert prop._state.burn_status == 4
        assert prop._state.abort_reason == 1

    def test_s19_rule_8005_disarms_on_safe_mode_event(self):
        eng = make_engine()
        settle(eng, 2)
        prop = eng.subsystems["prop"]
        prop.handle_command({"command": "set_burn", "dv_m_s": 0.5})
        assert prop.handle_command({"command": "arm",
                                    "arm_code": 0xA5C3})["success"]
        assert prop._state.state == 1
        eng._emit_event({"event_id": 0x0F0A, "severity": "MEDIUM",
                         "description": "SAFE_MODE_ENTRY (test)"})
        settle(eng, 1)
        assert prop._state.state == 0, \
            "rule 8005 must disarm propulsion on safe-mode entry"


class TestEPSBilling:

    def test_prop_line_bills_actual_state(self):
        """Quiescent 0 W; catbed heater 12 W; +2 W valves when armed
        (Phase B actuator-state billing convention, brief 03 WP-3)."""
        eng = make_engine()
        settle(eng, 3)
        # Quiescent: line on, nothing commanded
        assert eng.params[0x014A] == 1
        assert eng.params[0x014B] == pytest.approx(0.0, abs=0.01)
        # Catbed heater on (func 42 manual)
        tcs = eng.subsystems["tcs"]
        r = tcs.handle_command({"command": "heater", "circuit": "thruster",
                                "on": True})
        assert r["success"], r
        settle(eng, 3)
        assert eng.params[0x041B] == 1
        bus_v = eng.params[0x0105]
        assert eng.params[0x014B] * bus_v == pytest.approx(12.0, rel=0.1)
        # Armed adds the 2 W valve drivers
        prop = eng.subsystems["prop"]
        prop.handle_command({"command": "set_burn", "dv_m_s": 0.3})
        assert prop.handle_command({"command": "arm",
                                    "arm_code": 0xA5C3})["success"]
        settle(eng, 3)
        assert eng.params[0x014B] * bus_v == pytest.approx(14.0, rel=0.1)

    def test_heater_on_refused_when_line_dark(self):
        eng = make_engine()
        eng.subsystems["eps"]._state.power_lines["prop"] = False
        settle(eng, 2)
        r = eng.subsystems["tcs"].handle_command(
            {"command": "heater", "circuit": "thruster", "on": True})
        assert not r["success"], "func 42 must fail loudly on a dark line"
        assert r.get("error_code") == 0x0004

    def test_catbed_preheat_reaches_arm_interlock_in_20_min(self):
        """12 W into the C=50 J/K, tau=1200 s catbed node reaches 180 C in
        ~18 min — the design sheet's preheat (WP-6a)."""
        eng = make_engine()
        tcs = eng.subsystems["tcs"]
        # plain ticks — settle()/hold_attitude would pin the catbed hot
        for _ in range(2):
            eng._tick_once(1.0)
        assert tcs._state.temp_thruster < 100.0
        assert tcs.handle_command({"command": "heater", "circuit": "thruster",
                                   "on": True})["success"]
        t = 0.0
        while tcs._state.temp_thruster < 180.0 and t < 2400.0:
            eng._tick_once(60.0)
            t += 60.0
        assert tcs._state.temp_thruster >= 180.0, "catbed never reached 180 C"
        assert 600.0 <= t <= 1800.0, f"preheat took {t} s, expected ~1100 s"


class TestBreakpoints:

    def test_burn_survives_save_ticks_load(self):
        """Burn -> save -> 3 orbits -> load: the restored orbit is the
        POST-burn orbit at the saved instant, |r| < 1 m, and the eclipse
        param agrees with fresh propagation (brief 03 WP-3a)."""
        eng = make_engine()
        prop = start_burn(eng, dv=1.0)
        run_burn_out(eng, prop)
        assert eng.orbit.epoch_version == 1
        settle(eng, 2)
        r_saved = np.array(eng.orbit.state.pos_eci)
        snap = BreakpointManager(eng).save(name="post-burn")
        assert snap.get("orbit_elset"), "manoeuvred elset must be serialised"
        # 3 orbits of drift
        for _ in range(17):
            eng._tick_once(1000.0)
        assert BreakpointManager(eng).load(state=snap)
        r_back = np.array(eng.orbit.state.pos_eci)
        assert np.linalg.norm(r_back - r_saved) * 1e3 < 1.0
        assert eng.orbit.epoch_version == 1, "burn must survive the load"
        # eclipse flag agrees with fresh propagation
        eps = eng.subsystems["eps"]
        eps.tick(1.0, eng.orbit.state, eng.params)
        assert bool(eng.params[0x0108]) == bool(eng.orbit.state.in_eclipse)

    def test_pre_propulsion_snapshot_still_loads(self):
        """A snapshot without orbit_elset (old format) restores as
        un-manoeuvred (WP-3b)."""
        eng = make_engine()
        prop = start_burn(eng, dv=1.0)
        run_burn_out(eng, prop)
        assert eng.orbit.epoch_version == 1
        snap = BreakpointManager(eng).save(name="legacy")
        snap.pop("orbit_elset", None)   # simulate a pre-Phase-D snapshot
        assert BreakpointManager(eng).load(state=snap)
        assert eng.orbit.epoch_version == 0, \
            "absent orbit_elset must restore the config TLE"

    def test_breakpoint_mid_burn_resumes_and_completes(self):
        eng = make_engine()
        prop = start_burn(eng, dv=2.0)
        settle(eng, 5)
        assert prop._state.state == 2
        dv_at_save = prop._state.burn_dv_mag_m_s
        snap = BreakpointManager(eng).save(name="mid-burn")
        # perturb: abort and drain
        prop.handle_command({"command": "abort"})
        settle(eng, 3)
        assert BreakpointManager(eng).load(state=snap)
        assert prop._state.state == 2, "restored system must still be FIRING"
        assert prop._state.burn_dv_mag_m_s == pytest.approx(dv_at_save)
        run_burn_out(eng, prop)
        assert prop._state.burn_status == 3
        assert prop._state.burn_dv_achieved_m_s == pytest.approx(2.0,
                                                                 abs=0.01)


class TestDeterminism:

    def test_same_seed_same_burn_same_trace(self):
        def run():
            eng = make_engine()
            prop = start_burn(eng, dv=0.85)
            run_burn_out(eng, prop)
            settle(eng, 5)
            return {pid: eng.params.get(pid)
                    for pid in list(range(0x0B00, 0x0B1C)) + [0x014A, 0x014B]}
        a, b = run(), run()
        assert a == b, "same seed + same script must give identical traces"
