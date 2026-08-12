"""Phase B (brief 01, test 3): array generation physics.

Locks the loss stack (WP-1), the solar array drive (WP-2), the sun-vector
determinism (WP-8) and the 6U geometry sanity limits (WP-3).
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


def _eps_cfg():
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())
    sim = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text())
    cfg["simulation"] = sim["eps"]
    return cfg


class _Orbit:
    def __init__(self, beta=0.0, eclipse=False):
        self.solar_beta_deg = beta
        self.in_eclipse = eclipse


def _model(eol=False):
    m = EPSBasicModel()
    m.configure(_eps_cfg())
    m.rng.seed(1)
    if eol:
        # 2026-08b: the aging rate is now DERIVED from eps.yaml
        # `array_losses.annual_degradation` (2.75 %/yr over sunlit hours), so
        # wind the odometer to the design life and let the model compute the
        # factor rather than restating a hard-coded 3.14e-6/h here.
        m._state.sa_lifetime_hours = (
            3.0 * 8760.0 * EPSBasicModel.SUNLIT_FRACTION)
        m._state.sa_age_factor = max(
            0.5, 1.0 - m._state.sa_lifetime_hours * m._sa_degradation_per_hour)
    return m


def _mean_gen(m, sun, n=50, dt=1.0, orbit=None):
    orbit = orbit or _Orbit()
    params = {0x0245: sun[0], 0x0246: sun[1], 0x0247: sun[2]}
    total = 0.0
    for _ in range(n):
        m.tick(dt, orbit, params)
        total += m._state.power_gen_w
    return total / n


class TestLossStack:
    def test_one_square_metre_normal_incidence_eol(self):
        """WP-1 acceptance: 1 m2 at normal incidence with the full loss stack
        and 3-year aging => 267.2 W (K = 1361 x 0.295 x 0.85 x 0.926 x 0.97
        x 0.95 x 0.9175). 2026-08b: the aging factor is 0.9175, not 0.92 —
        3 x 2.75 %/yr linear, now derived from eps.yaml rather than from a
        hard-coded per-hour constant that disagreed with it."""
        m = _model(eol=True)
        m._panel_areas_body = {k: 0.0 for k in m._panel_areas_body}
        m._wing_py_area, m._wing_my_area = 1.0, 0.0
        m._state.wing_py_deployed = True
        # Phase C: drive axis is body Y — full illumination needs sun ⊥ Y
        gen = _mean_gen(m, (1.0, 0.0, 0.0))
        assert gen == pytest.approx(267.2, abs=2.5)

    def test_legacy_behaviour_without_loss_stack(self):
        """Back-compat: with `array_losses` absent the constant reverts to
        the legacy BOL 389.5 W/m2 (cell_eff x irradiance x MPPT only)."""
        cfg = _eps_cfg()
        cfg.pop("array_losses")
        m = EPSBasicModel()
        m.configure(cfg)
        m.rng.seed(1)
        m._panel_areas_body = {k: 0.0 for k in m._panel_areas_body}
        m._wing_py_area, m._wing_my_area = 1.0, 0.0
        m._state.wing_py_deployed = True
        gen = _mean_gen(m, (1.0, 0.0, 0.0))  # sun ⊥ the Y drive axis
        assert gen == pytest.approx(389.5, abs=3.0)


class TestSolarArrayDrive:
    def _deployed(self, eol=False):
        m = _model(eol)
        m._state.wing_py_deployed = True
        m._state.wing_my_deployed = True
        return m

    def test_tracking_wing_power_flat_over_orbit_phase(self):
        """WP-2 (a): with sada_tracking the wing contribution is independent
        of orbit phase to within +/-2 % at fixed beta."""
        m = self._deployed()
        beta = math.radians(30.0)
        vals = []
        for ang_deg in range(0, 360, 20):
            ang = math.radians(ang_deg)
            # Phase C frame: orbit normal = body Y (SADA drive axis)
            sun = (math.cos(beta) * math.cos(ang),
                   math.sin(beta), math.cos(beta) * math.sin(ang))
            vals.append(_mean_gen(m, sun, n=30, orbit=_Orbit(beta=30.0)))
        # Body faces DO vary with phase; isolate the wings by zeroing body.
        m2 = self._deployed()
        m2._panel_areas_body = {k: 0.0 for k in m2._panel_areas_body}
        vals = []
        for ang_deg in range(0, 360, 20):
            ang = math.radians(ang_deg)
            # Phase C frame: orbit normal = body Y (SADA drive axis)
            sun = (math.cos(beta) * math.cos(ang),
                   math.sin(beta), math.cos(beta) * math.sin(ang))
            vals.append(_mean_gen(m2, sun, n=30, orbit=_Orbit(beta=30.0)))
        mean = sum(vals) / len(vals)
        assert (max(vals) - min(vals)) / mean < 0.04, (
            f"tracking wing power not flat: {min(vals):.1f}..{max(vals):.1f}")

    def test_drive_angle_load_bearing_when_not_tracking(self):
        """WP-2 (b): with tracking off, the frozen drive angle places the
        wing normal — rotating the wings 90 deg away from a fixed sun kills
        their output (>= 60 % reduction).

        2026-08b: the shaft now SLEWS at `sada_max_rate_deg_s` (1.0 deg/s)
        instead of teleporting, so the 90 deg reposition needs >= 90 s of
        ticks before the steady state is measured.
        """
        m = self._deployed()
        m._sada_tracking = False
        m._panel_areas_body = {k: 0.0 for k in m._panel_areas_body}
        sun = (0.0, 1.0, 0.0)  # along the wings' home (+Y) normal
        m.handle_command({"command": "set_solar_array_drive", "angle_deg": 0.0})
        gen_home = _mean_gen(m, sun, n=30)
        m.handle_command({"command": "set_solar_array_drive", "angle_deg": 90.0})
        _mean_gen(m, sun, n=95)          # let the shaft actually get there
        gen_rot = _mean_gen(m, sun, n=30)
        assert gen_home > 10.0
        assert gen_rot < 0.4 * gen_home, (
            f"drive angle not load-bearing: {gen_home:.1f} -> {gen_rot:.1f} W")

    def test_drive_slews_at_the_configured_rate(self):
        """2026-08b: `sada_max_rate_deg_s` was dead config — the angle was
        assigned instantly. A 90 deg reposition must now take ~90 s at
        1.0 deg/s, and must be PARTIALLY complete in between."""
        m = self._deployed()
        m._sada_tracking = False
        m.handle_command({"command": "set_solar_array_drive", "angle_deg": 90.0})
        _mean_gen(m, (0.0, 1.0, 0.0), n=30, dt=1.0)
        assert 25.0 < m._state.solar_array_drive_angle < 35.0, (
            "drive teleported or did not move: "
            f"{m._state.solar_array_drive_angle:.1f} deg after 30 s")
        _mean_gen(m, (0.0, 1.0, 0.0), n=70, dt=1.0)
        assert m._state.solar_array_drive_angle == pytest.approx(90.0, abs=0.5)

    def test_stuck_sada_failure_is_injectable_and_freezes_the_shaft(self):
        """2026-08b: nothing at runtime could set `_sada_tracking = False`, so
        the "SADA stuck" failure the code comments described could not be
        exercised. It is now a first-class injection, observable as a frozen
        0x0133 plus a fall in orbit-average wing yield."""
        m = self._deployed()
        assert m._sada_tracking is True
        m.inject_failure("sada_stuck", angle_deg=0.0)
        assert m._sada_tracking is False
        # Sun swings right across the frozen normal; the shaft must not move.
        for k in range(60):
            ang = math.radians(6.0 * k)
            m.tick(1.0, _Orbit(), {0x0245: math.cos(ang), 0x0246: 0.0,
                                   0x0247: math.sin(ang)})
            assert m._state.solar_array_drive_angle == pytest.approx(0.0)
        m.clear_failure("sada_stuck")
        assert m._sada_tracking is True

    def test_stuck_sada_costs_orbit_average_energy(self):
        """A stuck drive (tracking off) reverts the wings to the |sin phi|
        sweep duty — ~36 % less orbit-average wing energy than tracking."""
        beta = math.radians(20.0)

        def orbit_avg(tracking):
            m = self._deployed()
            m._sada_tracking = tracking
            m._panel_areas_body = {k: 0.0 for k in m._panel_areas_body}
            total = 0.0
            n = 72
            for i in range(n):
                ang = 2 * math.pi * i / n
                sun = (math.cos(beta) * math.cos(ang),
                       math.cos(beta) * math.sin(ang), math.sin(beta))
                total += _mean_gen(m, sun, n=5, orbit=_Orbit(beta=20.0))
            return total / n

        avg_track = orbit_avg(True)
        avg_stuck = orbit_avg(False)
        assert avg_stuck < 0.75 * avg_track, (
            f"stuck SADA not punished: {avg_track:.1f} vs {avg_stuck:.1f} W")

    def test_device_gate_zeroes_only_the_wings(self):
        """WP-2 (c), REVISED 2026-08b: S2 device 0x0101 (solar array drive)
        OFF removes the WING contribution only.

        The gate used to zero gen_w and every sa_panel_current, including the
        five body-mounted faces — which need no drive, being bonded to the
        structure and wired straight to the MPPT. An operator who switched the
        SADA off therefore saw 0 W in full sunlight, indistinguishable from a
        total array loss. The residual body generation is the diagnosis.
        """
        sun = (0.6, 0.0, -0.8)   # off-axis so body faces are genuinely lit
        healthy = self._deployed()
        gen_on = _mean_gen(healthy, sun, n=10)

        m = self._deployed()
        m.set_device_state(0x0101, False)
        gen_off = _mean_gen(m, sun, n=10)

        assert m._state.sa_wing_currents["py"] == 0.0
        assert m._state.sa_wing_currents["my"] == 0.0
        assert gen_off > 0.0, "body-mounted cells need no solar array drive"
        assert gen_off < 0.5 * gen_on, (
            f"wing contribution not removed: {gen_on:.1f} -> {gen_off:.1f} W")
        # Body faces keep producing; the ±Y faces keep their BODY share only.
        assert m._state.sa_panel_currents["px"] > 0.0


class TestSunVectorDeterminism:
    def test_generation_pure_function_of_geometry(self):
        """WP-8: the sun-sweep phase is a deterministic function of the orbit
        state — two identical geometries give identical generation, and the
        orbit-average at fixed beta does not drift with elapsed time (the old
        5700 s accumulator drifted +/-8 W over ~12 days)."""
        from tools.power_budget import load_configs, build_propagator
        from smo_simulator.models.aocs_basic import AOCSBasicModel
        cfg = load_configs(CONFIG_DIR)
        prop, epoch = build_propagator(cfg["orbit"])

        def orbit_avg_illum(start_offset_s):
            from datetime import timedelta
            prop.reset(epoch + timedelta(seconds=start_offset_s))
            total = n = 0
            for _ in range(567):  # one orbit at 10 s
                st = prop.advance(10.0)
                ph = AOCSBasicModel._orbit_phase_from_geometry(st)
                assert ph is not None
                if not st.in_eclipse:
                    b = math.radians(st.solar_beta_deg)
                    total += math.cos(b) * abs(math.sin(math.radians(ph)))
                n += 1
            return total / n

        a = orbit_avg_illum(0.0)
        b = orbit_avg_illum(100 * 5669.0)  # ~100 orbits later
        assert a == pytest.approx(b, rel=0.02), (
            f"orbit-average sun geometry drifted: {a:.4f} vs {b:.4f}")


class TestGeometrySanity:
    def test_credible_6u_areas(self):
        cfg = _eps_cfg()
        sp = cfg["solar_panels"]
        body = sum(sp[k] for k in ("px_area_m2", "mx_area_m2", "py_area_m2",
                                   "my_area_m2", "pz_area_m2", "mz_area_m2"))
        assert body <= 0.20, f"body cell area {body} m2 not credible for a 6U"
        assert sp["wing_py_area_m2"] <= 0.18 + 1e-9
        assert sp["wing_my_area_m2"] <= 0.18 + 1e-9
        deployed = body + sp["wing_py_area_m2"] + sp["wing_my_area_m2"]
        assert deployed <= 0.56, f"deployed total {deployed} m2 not credible"
        assert sp["mz_area_m2"] == 0.0, "-Z face carries the imager aperture"

    def test_battery_capacity_is_6u_class(self):
        cfg = _eps_cfg()
        assert 60.0 <= cfg["battery"]["capacity_wh"] <= 110.0
