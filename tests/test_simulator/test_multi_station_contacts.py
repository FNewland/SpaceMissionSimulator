"""Multi-ground-station contact support (defect #41).

Verifies that the orbit propagator gates ``in_contact`` on ALL configured
ground stations (not just the first / primary one), exposes the active
station name, and that merged contact-window prediction is sorted and tagged
per station — while remaining backward compatible with single-station use.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smo_common.config.loader import load_orbit_config
from smo_common.orbit.propagator import OrbitPropagator, GroundStation

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"
EPOCH = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)


def _mission_propagator():
    cfg = load_orbit_config(CONFIG_DIR)
    stations = [
        GroundStation(name=g.name, lat_deg=g.lat_deg, lon_deg=g.lon_deg,
                      alt_km=g.alt_km, min_elevation_deg=g.min_elevation_deg)
        for g in cfg.ground_stations
    ]
    assert len(stations) >= 2, "mission config must define multiple stations"
    prop = OrbitPropagator(cfg.tle_line1, cfg.tle_line2,
                           ground_stations=stations,
                           earth_radius_km=cfg.earth_radius_km)
    prop.reset(EPOCH)
    return prop, stations


def _mid_pass_epoch(prop, station_name, duration_s=6 * 3600):
    """Find the middle of the first pass for the named station."""
    for w in prop.contact_windows(duration_s=duration_s, step_s=10.0):
        if w["gs_name"] == station_name:
            aos = datetime.fromisoformat(w["aos"])
            los = datetime.fromisoformat(w["los"])
            return aos + (los - aos) / 2
    pytest.fail(f"No {station_name} pass found within {duration_s} s")


class TestMultiStationContact:
    def test_troll_pass_sets_in_contact_and_active_gs(self):
        """A planner-scheduled Troll pass must not be dead air: mid-pass the
        propagator must report contact with active_gs == 'Troll'."""
        prop, _ = _mission_propagator()
        mid = _mid_pass_epoch(prop, "Troll")
        prop.reset(mid)
        s = prop.state
        assert s.in_contact is True
        assert s.active_gs == "Troll"
        assert s.gs_elevation_deg >= 5.0
        # Iqaluit (antipodal latitude) must be below the mask at that time
        assert s.gs_elevations_deg["Iqaluit"] < 5.0

    def test_iqaluit_pass_sets_active_gs(self):
        prop, _ = _mission_propagator()
        mid = _mid_pass_epoch(prop, "Iqaluit")
        prop.reset(mid)
        s = prop.state
        assert s.in_contact is True
        assert s.active_gs == "Iqaluit"

    def test_out_of_contact_has_no_active_gs(self):
        prop, stations = _mission_propagator()
        # Scan for an epoch where no station is above its mask
        t = EPOCH
        for _ in range(200):
            prop.reset(t)
            if not prop.state.in_contact:
                break
            t += timedelta(seconds=60)
        s = prop.state
        assert s.in_contact is False
        assert s.active_gs is None
        # Per-station elevations still reported for every station
        assert set(s.gs_elevations_deg) == {gs.name for gs in stations}


class TestMergedContactWindows:
    def test_merged_windows_sorted_and_tagged(self):
        prop, _ = _mission_propagator()
        windows = prop.contact_windows(duration_s=24 * 3600, step_s=30.0)
        assert windows, "expected contact windows within 24 h"
        names = {w["gs_name"] for w in windows}
        # The mission network is the five stations in orbit.yaml (Iqaluit,
        # Troll, Svalbard, Inuvik, O'Higgins); every one of them sees the
        # spacecraft within 24 h on this SSO.
        expected = {gs.name for gs in _mission_propagator()[1]}
        assert names == expected, f"missing station in {names}"
        aos_list = [w["aos"] for w in windows]
        assert aos_list == sorted(aos_list), "merged windows must be AOS-sorted"

    def test_merged_equals_union_of_per_station(self):
        prop, stations = _mission_propagator()
        merged = prop.contact_windows(duration_s=12 * 3600, step_s=30.0)
        per = []
        for gs in stations:
            prop.reset(EPOCH)
            per.extend(prop.contact_windows(duration_s=12 * 3600, step_s=30.0,
                                            gs=gs))
        per.sort(key=lambda w: w["aos"])
        prop.reset(EPOCH)
        assert merged == per

    def test_gap_between_passes_roughly_half_orbit(self):
        """User goal: with both stations active the typical gap between
        successive contacts is around half an orbit (~47 min), never the
        multi-hour dead zones of the single-station behaviour."""
        prop, _ = _mission_propagator()
        windows = prop.contact_windows(duration_s=48 * 3600, step_s=30.0)
        assert len(windows) >= 30
        gaps = []
        for a, b in zip(windows, windows[1:]):
            los = datetime.fromisoformat(a["los"])
            aos = datetime.fromisoformat(b["aos"])
            gaps.append((aos - los).total_seconds() / 60.0)
        gaps.sort()
        median = gaps[len(gaps) // 2]
        # Half-orbit ~47 min; allow half-to-one-orbit range (some orbits miss
        # one station's longitude window — expected orbital mechanics).
        assert 20.0 <= median <= 95.0
        # Single-station config had >11 h dead zones; multi-station must not.
        assert max(gaps) < 3.5 * 60.0


class TestBackwardCompatibility:
    def test_single_station_config_still_works(self):
        cfg = load_orbit_config(CONFIG_DIR)
        gs = GroundStation(name="Iqaluit", lat_deg=63.747, lon_deg=-68.518,
                           alt_km=0.034, min_elevation_deg=5.0)
        prop = OrbitPropagator(cfg.tle_line1, cfg.tle_line2,
                               ground_stations=[gs])
        prop.reset(EPOCH)
        mid = _mid_pass_epoch(prop, "Iqaluit", duration_s=24 * 3600)
        prop.reset(mid)
        s = prop.state
        assert s.in_contact is True
        assert s.active_gs == "Iqaluit"
        assert s.gs_elevation_deg >= 5.0
        # gs=None on a single-station propagator == explicit-gs behaviour
        w_default = prop.contact_windows(duration_s=6 * 3600, step_s=30.0)
        prop.reset(mid)
        w_explicit = prop.contact_windows(duration_s=6 * 3600, step_s=30.0,
                                          gs=gs)
        assert w_default == w_explicit

    def test_no_stations_configured(self):
        cfg = load_orbit_config(CONFIG_DIR)
        prop = OrbitPropagator(cfg.tle_line1, cfg.tle_line2,
                               ground_stations=[])
        prop.reset(EPOCH)
        s = prop.state
        assert s.in_contact is False
        assert s.active_gs is None
        assert s.gs_elevations_deg == {}
        assert prop.contact_windows(duration_s=3600) == []

    def test_orbitstate_defaults(self):
        from smo_common.orbit.propagator import OrbitState
        s = OrbitState()
        assert s.active_gs is None
        assert s.gs_elevations_deg == {}
        assert s.in_contact is False


class TestEngineSurfacesActiveStation:
    def test_state_summary_and_aos_event_include_station(self):
        from smo_simulator.engine import SimulationEngine
        engine = SimulationEngine(CONFIG_DIR, speed=1.0)
        # Drive THE sim clock to a Troll mid-pass epoch, then tick once. The
        # engine owns the clock and re-propagates the orbit to it on every
        # sub-step, so moving the propagator alone no longer moves the sim.
        mid = _mid_pass_epoch(engine.orbit, "Troll", duration_s=12 * 3600)
        engine._sim_time = mid
        engine.orbit.propagate_to(mid)
        engine._tick_once(1.0)
        assert engine._in_contact is True
        assert engine._active_gs == "Troll"
        summary = engine.get_state_summary()
        assert summary["in_contact"] is True
        assert summary["ground_station_name"] == "Troll"
        assert "gs_elevation_deg" in summary
        snap = engine.get_instructor_snapshot()
        assert snap["orbit"]["active_gs"] == "Troll"
        # AOS / LOS transition events carry the station name
        captured = []
        engine._emit_event = captured.append
        engine._prev_in_contact = False
        engine._check_transitions(engine.orbit.state)
        aos = [e for e in captured if e["event_id"] == 0x0001]
        assert aos and "Troll" in aos[-1]["description"]
        # Simulate LOS: previous contact was Troll, now out of contact
        from smo_common.orbit.propagator import OrbitState
        engine._prev_in_contact = True
        engine._prev_active_gs = "Troll"
        engine._check_transitions(OrbitState())
        los = [e for e in captured if e["event_id"] == 0x0002]
        assert los and "Troll" in los[-1]["description"]
