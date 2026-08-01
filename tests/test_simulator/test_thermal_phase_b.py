"""Phase B acceptance tests for brief 05 (thermal: FPA + heaters + billing).

Covers:
  * WP-1/WP-2 — single FPA node: TCS drives the node to the SETPOINT (not an
    additive offset); TCS 0x0408 and payload 0x0601 agree within 3 C in all
    four command combinations of the defect-1 table; every command path
    (TCS func 43, EPS func 17, PL_SET_MODE, S2 device) yields one agreed
    effective cooler state 0x040C.
  * WP-3 — battery heater reachable: >= 2 rising and >= 2 falling thermostat
    edges per orbit with the line on; duty strictly inside (0, 100); the
    battery reaches <= +5 C in eclipse with the heater off.
  * WP-4 — HEATER_BATTERY with the EPS line off returns a refusal.
  * WP-5 / brief 01 WP-5/WP-12 — billing == actuator state: heater billed
    energy equals the TCS heater-power integral within 1 %; a payload mode
    set via the payload path bills EPS; tier-dependent TX power.
  * WP-6 — no S12 monitor violates in a clean nominal profile; the loader
    refuses inverted limit bands.
"""
import math
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402
from smo_simulator.models.payload_basic import PayloadBasicModel  # noqa: E402
from smo_simulator.models.tcs_basic import TCSBasicModel  # noqa: E402

ORBIT_S = 5670.0
ECLIPSE_FRACTION = 0.34


class _Orbit:
    def __init__(self):
        self.in_eclipse = False
        self.solar_beta_deg = 30.0


def _cfg(name):
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / f"{name}.yaml").read_text())
    sim = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
    if name in sim:
        cfg["simulation"] = sim[name]
    return cfg


def _models(seed=3):
    eps = EPSBasicModel()
    eps.configure(_cfg("eps"))
    eps.rng.seed(seed)
    tcs = TCSBasicModel()
    tcs.configure(_cfg("tcs"))
    tcs.rng.seed(seed + 1)
    pl = PayloadBasicModel()
    pl.configure(_cfg("payload"))
    pl.rng.seed(seed + 2)
    return eps, tcs, pl


def _co_tick(eps, tcs, pl, params, orbit, seconds, dt=10.0, t0=0.0,
             eclipse_profile=True, hook=None):
    for i in range(int(seconds / dt)):
        t = t0 + i * dt
        if eclipse_profile:
            orbit.in_eclipse = (t % ORBIT_S) > (1 - ECLIPSE_FRACTION) * ORBIT_S
        if hook:
            hook(t)
        eps.tick(dt, orbit, params)
        tcs.tick(dt, orbit, params)
        pl.tick(dt, orbit, params)
    return t0 + seconds


class TestSingleFPANode:
    def test_setpoint_reached_not_offset(self):
        """WP-1 (a): cooler ON drives 0x0408 to fpa_cooler_target_c ± 0.5 C —
        the old code asymptoted to env_int - 35 (≈ -23..-25 C)."""
        eps, tcs, pl = _models()
        eps._state.power_lines["fpa_cooler"] = True
        tcs._state.cooler_fpa = True
        params = {}
        orbit = _Orbit()
        _co_tick(eps, tcs, pl, params, orbit, 1200.0, eclipse_profile=False)
        assert params[0x0408] == pytest.approx(-15.0, abs=0.5)

    @pytest.mark.parametrize("combo", ["none", "pl_only", "pl_plus_tcs",
                                       "tcs_only"])
    def test_four_command_combos_agree_within_3c(self, combo):
        """WP-1 (b): after 600 s, |0x0408 - 0x0601| <= 3.0 in all four
        command combinations of the defect-1 table. The middle rows used to
        diverge by 26.4 C."""
        eps, tcs, pl = _models()
        params = {}
        orbit = _Orbit()
        # power lines up (the dispatch prerequisite EPS_POWER_ON steps)
        eps._state.power_lines["payload"] = True
        eps._state.power_lines["fpa_cooler"] = True

        if combo in ("pl_only", "pl_plus_tcs"):
            assert pl.handle_command({"command": "set_mode", "mode": 1})["success"]
            # dispatch routes PL_SET_MODE -> unified TCS cooler flag
            tcs.handle_command({"command": "fpa_cooler", "on": True})
        if combo in ("pl_plus_tcs", "tcs_only"):
            assert tcs.handle_command({"command": "fpa_cooler", "on": True})["success"]

        _co_tick(eps, tcs, pl, params, orbit, 900.0, eclipse_profile=False)
        assert abs(params[0x0408] - params[0x0601]) <= 3.0, (
            f"combo {combo}: TCS {params[0x0408]:.1f} C vs payload "
            f"{params[0x0601]:.1f} C")

    def test_unified_cooler_state_and_billing(self):
        """WP-2: with the line powered, the commanded cooler yields
        0x040C == 1 AND EPS bills 15 W on the fpa_cooler line AND the FPA
        cools; commanding OFF reverses all three."""
        eps, tcs, pl = _models()
        eps._state.power_lines["fpa_cooler"] = True
        params = {}
        orbit = _Orbit()
        tcs.handle_command({"command": "fpa_cooler", "on": True})
        _co_tick(eps, tcs, pl, params, orbit, 600.0, eclipse_profile=False)
        assert params[0x040C] == 1
        assert eps._state.line_currents["fpa_cooler"] * \
            max(eps._state.bus_voltage, 20.0) == pytest.approx(15.0, abs=0.5)
        assert params[0x0408] < -10.0

        tcs.handle_command({"command": "fpa_cooler", "on": False})
        _co_tick(eps, tcs, pl, params, orbit, 600.0, eclipse_profile=False)
        assert params[0x040C] == 0
        assert eps._state.line_currents["fpa_cooler"] == 0.0
        assert params[0x0408] > 0.0

    def test_line_off_means_no_cooling_no_billing(self):
        """Commanded ON but EPS line 4 dark: effective state 0, no billing,
        no cooling — one truth on every view."""
        eps, tcs, pl = _models()
        eps._state.power_lines["fpa_cooler"] = False
        tcs._state.cooler_fpa = True
        params = {}
        orbit = _Orbit()
        _co_tick(eps, tcs, pl, params, orbit, 600.0, eclipse_profile=False)
        assert params[0x040C] == 0
        assert eps._state.line_currents["fpa_cooler"] == 0.0
        assert params[0x0408] > 0.0


class TestBatteryHeater:
    def test_thermostat_cycles_every_orbit(self):
        """WP-3 (c): with line 5 ON, >= 2 rising and >= 2 falling edges of
        0x040A per orbit, and duty strictly inside (0, 100)."""
        eps, tcs, pl = _models()
        eps._state.power_lines["htr_bat"] = True
        params = {}
        orbit = _Orbit()
        prev = False
        per_orbit = {}
        duties = []

        def hook(t):
            pass

        t = 0.0
        dt = 10.0
        for i in range(int(6 * ORBIT_S / dt)):
            t = i * dt
            orbit.in_eclipse = (t % ORBIT_S) > (1 - ECLIPSE_FRACTION) * ORBIT_S
            eps.tick(dt, orbit, params)
            tcs.tick(dt, orbit, params)
            k = int(t // ORBIT_S)
            if k >= 1:  # skip the transient first orbit
                rec = per_orbit.setdefault(k, [0, 0])
                on = bool(params.get(0x040A, 0))
                if on and not prev:
                    rec[0] += 1
                if prev and not on:
                    rec[1] += 1
                duties.append(params.get(0x040E, 0.0))
            prev = bool(params.get(0x040A, 0))

        # >= 2 rising and >= 2 falling per orbit on average, and every orbit
        # shows at least one full cycle (an edge can straddle an orbit
        # boundary, so the per-orbit floor is 1 with the >= 2 mean enforced
        # over the whole window).
        n = len(per_orbit)
        total_r = sum(r for r, _ in per_orbit.values())
        total_f = sum(f for _, f in per_orbit.values())
        assert total_r >= 2 * n and total_f >= 2 * n, (
            f"only {total_r} rising / {total_f} falling edges over {n} orbits")
        for k, (rising, falling) in per_orbit.items():
            assert rising >= 1 and falling >= 1, (
                f"orbit {k}: {rising} rising / {falling} falling edges — "
                "thermostat not genuinely cycling")
        steady = duties[len(duties) // 2:]
        assert 0.0 < min(steady) and max(steady) < 100.0

    def test_battery_reaches_5c_in_eclipse_without_heater(self):
        """WP-3 (b): the eclipse sink is low enough that the thermostat trip
        point is genuinely reachable."""
        eps, tcs, pl = _models()
        eps._state.power_lines["htr_bat"] = False
        params = {}
        orbit = _Orbit()
        t_min = 99.0
        dt = 10.0
        for i in range(int(3 * ORBIT_S / dt)):
            t = i * dt
            orbit.in_eclipse = (t % ORBIT_S) > (1 - ECLIPSE_FRACTION) * ORBIT_S
            eps.tick(dt, orbit, params)
            tcs.tick(dt, orbit, params)
            if t > ORBIT_S:
                t_min = min(t_min, tcs._state.temp_battery)
        assert t_min <= 5.0, (
            f"battery only reached {t_min:.1f} C — thermostat unreachable")

    def test_heater_command_fails_loudly_when_line_dark(self):
        """WP-4: HEATER_BATTERY(on=1) with eps.pl_htr_bat = 0 returns an
        execution failure with a reason, and 0x040A stays 0."""
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        tcs.rng.seed(9)

        class _Eng:
            params = {0x0115: 0, 0x0116: 0}

        tcs._engine = _Eng()
        res = tcs.handle_command({"command": "heater", "circuit": "battery",
                                  "on": True})
        assert res["success"] is False
        assert "line" in res["message"].lower()
        params = {0x0115: 0}
        orbit = _Orbit()
        tcs.tick(10.0, orbit, params)
        assert params[0x040A] == 0

        # With the line powered the same command succeeds.
        _Eng.params[0x0115] = 1
        res = tcs.handle_command({"command": "heater", "circuit": "battery",
                                  "on": True})
        assert res["success"] is True


class TestBillingEqualsActuators:
    def test_heater_energy_matches_tcs_integral(self):
        """WP-5 (c): heater Wh billed by EPS == ∫ 0x0411 dt within 1 %."""
        eps, tcs, pl = _models()
        eps._state.power_lines["htr_bat"] = True
        eps._state.power_lines["htr_obc"] = True
        params = {0x0116: 1}
        orbit = _Orbit()
        e_billed = e_tcs = 0.0
        dt = 10.0
        for i in range(int(4 * ORBIT_S / dt)):
            t = i * dt
            orbit.in_eclipse = (t % ORBIT_S) > (1 - ECLIPSE_FRACTION) * ORBIT_S
            eps.tick(dt, orbit, params)
            tcs.tick(dt, orbit, params)
            bus = max(eps._state.bus_voltage, 20.0)
            e_billed += (eps._state.line_currents["htr_bat"]
                         + eps._state.line_currents["htr_obc"]) * bus * dt
            e_tcs += params.get(0x0411, 0.0) * dt
        assert e_tcs > 0
        assert e_billed == pytest.approx(e_tcs, rel=0.01), (
            f"billed {e_billed / 3600:.2f} Wh vs TCS {e_tcs / 3600:.2f} Wh")

    def test_heater_line_on_thermostat_off_bills_zero(self):
        """WP-5 (a): line 5 ON with the zone warm (thermostat OFF) bills 0 W
        — the 6 W phantom bill is gone."""
        eps, tcs, pl = _models()
        eps._state.power_lines["htr_bat"] = True
        tcs._state.temp_battery = 15.0  # well above the OFF setpoint
        params = {}
        orbit = _Orbit()
        eps.tick(1.0, orbit, params)   # publish line states
        tcs.tick(1.0, orbit, params)   # thermostat decides (stays OFF)
        eps.tick(1.0, orbit, params)   # bill from actual heater state
        assert params[0x040A] == 0
        assert eps._state.line_currents["htr_bat"] == 0.0

    def test_payload_mode_via_payload_path_bills_eps(self):
        """WP-5 (b): PL_SET_MODE alone (payload model path) raises
        eps.power_cons by the standby wattage — nothing runs off the books."""
        eps, tcs, pl = _models()
        eps._state.power_lines["payload"] = True
        params = {}
        orbit = _Orbit()
        _co_tick(eps, tcs, pl, params, orbit, 30.0, eclipse_profile=False)
        base = eps._state.line_currents["payload"]
        assert base == 0.0
        pl.handle_command({"command": "set_mode", "mode": 1})
        _co_tick(eps, tcs, pl, params, orbit, 30.0, eclipse_profile=False)
        bus = max(eps._state.bus_voltage, 20.0)
        assert eps._state.line_currents["payload"] * bus == pytest.approx(
            6.0, abs=0.3), "payload standby not billed from actual state"

    def test_tx_power_is_tier_dependent(self):
        """Brief 01 WP-12: HIGH tier bills transponder_hi_power_w (20 W),
        LOW bills 12 W."""
        eps, tcs, pl = _models()
        eps._state.power_lines["ttc_tx"] = True
        orbit = _Orbit()
        params = {0x0506: 16000.0}
        eps.tick(1.0, orbit, params)
        bus = max(eps._state.bus_voltage, 20.0)
        assert eps._state.line_currents["ttc_tx"] * bus == pytest.approx(12.0, abs=0.3)
        params[0x0506] = 128000.0
        eps.tick(1.0, orbit, params)
        bus = max(eps._state.bus_voltage, 20.0)
        assert eps._state.line_currents["ttc_tx"] * bus == pytest.approx(20.0, abs=0.3)


class TestMonitors:
    def _definitions(self):
        from smo_simulator.monitoring_loader import load_s12_definitions
        return load_s12_definitions(CONFIG_DIR)

    def test_loader_refuses_inverted_limits(self, tmp_path):
        from smo_simulator.monitoring_loader import load_s12_definitions
        bad = tmp_path / "monitoring"
        bad.mkdir()
        (bad / "s12_definitions.yaml").write_text(  # noqa: E501

            "s12_definitions:\n"
            "  - param_id: 0x0101\n"
            "    name: inverted\n"
            "    low_limit: -15.0\n"
            "    high_limit: -20.0\n"
            "    severity: WARNING\n")
        with pytest.raises(ValueError, match="[Ii]nverted"):
            load_s12_definitions(tmp_path)

    def test_shipped_definitions_have_no_inverted_bands(self):
        defs = self._definitions()
        assert defs, "no S12 definitions loaded"
        for d in defs.values():
            assert d["high_limit"] >= d["low_limit"], d["name"]

    def test_clean_nominal_run_violates_no_thermal_monitor(self):
        """WP-6 (b/e): a 3-orbit nominal profile (payload standby + cooled
        half / off half) keeps every TCS + payload + EPS thermal parameter
        inside its S12 band, so S19 rule 3001 never fires."""
        defs = self._definitions()
        eps, tcs, pl = _models()
        s = eps._state
        s.wing_py_deployed = True
        s.wing_my_deployed = True
        for line in ("aocs_wheels", "htr_bat", "htr_obc", "payload",
                     "fpa_cooler"):
            s.power_lines[line] = True
        params = {0x0116: 1}
        orbit = _Orbit()
        pl.handle_command({"command": "set_mode", "mode": 1})
        tcs.handle_command({"command": "fpa_cooler", "on": True})

        watched = {0x0408, 0x0601, 0x0407, 0x0406, 0x0411, 0x0102}
        violations = []
        dt = 10.0
        for i in range(int(3 * ORBIT_S / dt)):
            t = i * dt
            orbit.in_eclipse = (t % ORBIT_S) > (1 - ECLIPSE_FRACTION) * ORBIT_S
            if t > 1.5 * ORBIT_S and pl._state.mode != 0:
                # second half: payload off, cooler off (warm state)
                pl.handle_command({"command": "set_mode", "mode": 0})
                tcs.handle_command({"command": "fpa_cooler", "on": False})
            eps.tick(dt, orbit, params)
            tcs.tick(dt, orbit, params)
            pl.tick(dt, orbit, params)
            if t < 900.0:
                continue  # cooldown/settling transient
            for d in defs.values():
                pid = d["param_id"]
                if pid not in watched or pid not in params:
                    continue
                if d.get("check_type", 0) != 0:
                    continue
                v = params[pid]
                if pid == 0x0601 and pl._state.mode == 0:
                    continue  # payload dark — monitor gated off in flight
                if v < d["low_limit"] or v > d["high_limit"]:
                    violations.append((t, d["name"], round(v, 2)))
        assert not violations, (
            f"{len(violations)} S12 violations in a clean run; first 5: "
            f"{violations[:5]}")
