"""The MCS's eclipse reads come from the single authority.

Owner report: "we have managed to get into a state where we were generating
power in eclipse". Nothing on board ever did: the console put a GROUND-derived
eclipse label (MCS propagator, MCS clock) next to a TELEMETERED wattage, and
near a terminator crossing the two described different instants.

Fixes pinned here:

* the power-budget panel takes its eclipse label from the same EPS telemetry
  packet as ``power_gen`` (``eps.sun_fraction`` 0x0142, or the boolean
  ``eps.eclipse_flag`` / ``eps.eclipse_active``), so label and wattage cannot
  contradict each other;
* ``_compute_orbital_state`` prefers the spacecraft-reported present-time
  illumination over its own re-derivation, and says which it used;
* the ground propagation that remains (future transitions, real mode) runs the
  same geometry as the spacecraft.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from smo_mcs.displays.power_budget import PowerBudgetMonitor
from smo_mcs.server import MCSServer


def test_panel_takes_the_eclipse_flag_from_the_same_packet_as_the_watts():
    mon = PowerBudgetMonitor()
    # Ground propagation says ECLIPSE; the telemetry that carries the 85 W
    # says full sun. The panel must believe the telemetry.
    mon.update_from_telemetry({
        "in_eclipse": True,                    # ground-derived, stale
        "eps": {"power_gen": 85.0, "power_cons": 60.0,
                "eclipse_flag": 0, "sun_fraction": 1.0},
    })
    data = mon.get_display_data()
    assert data["eclipse_active"] is False
    assert data["sun_fraction"] == 1.0
    assert data["eclipse_source"] == "telemetry"
    assert not (data["eclipse_active"] and data["power_gen_w"] > 0.0)


def test_panel_never_shows_generation_in_eclipse():
    mon = PowerBudgetMonitor()
    mon.update_from_telemetry({
        "in_eclipse": False,                   # ground says sunlit
        "eps": {"power_gen": 0.0, "power_cons": 60.0,
                "eclipse_flag": 1, "sun_fraction": 0.0},
    })
    data = mon.get_display_data()
    assert data["eclipse_active"] is True
    assert data["power_gen_w"] == 0.0
    assert not (data["eclipse_active"] and data["power_gen_w"] > 0.0)


def test_panel_falls_back_to_the_ground_value_without_telemetry():
    mon = PowerBudgetMonitor()
    mon.update_from_telemetry({"in_eclipse": True, "eps": {}})
    data = mon.get_display_data()
    assert data["eclipse_active"] is True
    assert data["eclipse_source"] == "ground_propagator"


def _fake_mcs() -> MCSServer:
    srv = MCSServer.__new__(MCSServer)
    srv._time_source = "sim"
    srv._sim_illum = None
    return srv


def test_ingest_caches_the_spacecraft_illumination():
    srv = _fake_mcs()
    MCSServer._ingest_sim_illumination(srv, {
        "in_eclipse": True, "sun_fraction": 0.0, "in_penumbra": False,
        "shadow_model": "conical", "time_to_eclipse_exit_s": 900.0,
    })
    assert srv._sim_illum["in_eclipse"] is True
    assert srv._sim_illum["sun_fraction"] == 0.0
    assert srv._sim_illum["time_to_eclipse_exit_s"] == 900.0

    # Real mode has no such endpoint — the cache must stay empty.
    srv2 = _fake_mcs()
    srv2._time_source = "real"
    MCSServer._ingest_sim_illumination(srv2, {"in_eclipse": True})
    assert srv2._sim_illum is None


def test_present_time_state_prefers_the_spacecraft():
    srv = _fake_mcs()
    o = SimpleNamespace(lat_deg=0.0, lon_deg=0.0, alt_km=513.0,
                        in_eclipse=False, sun_fraction=1.0, in_penumbra=False,
                        shadow_model="conical", in_contact=False,
                        active_gs=None, gs_elevation_deg=-5.0)
    srv._orbit_prop = SimpleNamespace(
        reset=lambda t: None, state=o,
        next_eclipse_transition=lambda: {
            "in_eclipse": False, "time_to_eclipse_entry_s": 300.0,
            "time_to_eclipse_exit_s": None})
    srv.sim_now = lambda: None

    out = MCSServer._compute_orbital_state(srv)
    assert out["in_eclipse"] is False
    assert out["eclipse_source"] == "ground_propagator"

    srv._sim_illum = {"in_eclipse": True, "sun_fraction": 0.0,
                      "in_penumbra": False, "shadow_model": "conical",
                      "time_to_eclipse_entry_s": None,
                      "time_to_eclipse_exit_s": 1200.0,
                      "wall": time.time()}
    out = MCSServer._compute_orbital_state(srv)
    assert out["in_eclipse"] is True
    assert out["sun_fraction"] == 0.0
    assert out["eclipse_source"] == "spacecraft"
    assert out["time_to_eclipse_exit_s"] == 1200.0

    # Stale cache -> honest fallback.
    srv._sim_illum["wall"] = time.time() - 3600.0
    out = MCSServer._compute_orbital_state(srv)
    assert out["in_eclipse"] is False
    assert out["eclipse_source"] == "ground_propagator"
