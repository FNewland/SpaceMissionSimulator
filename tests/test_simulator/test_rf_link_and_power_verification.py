"""Verification tests for two operational guarantees on the EOSAT-1 config:

1. RF link CLOSES during a real ground-station pass — the spacecraft acquires
   carrier -> bit -> frame lock and a POSITIVE link margin, so the ground would
   actually receive telemetry and uplink is possible.

2. Default power draw is NOT excessive — under nominal sunlit conditions with
   the deployable wings out, the spacecraft is net-positive over an orbit and
   the battery charges rather than discharging.

These pin the verified behaviour using the real eosat1 YAML configs and the
real orbit propagator (not mocked geometry), so a future regression in EIRP,
G/T, frequency units, range units, or the EPS load defaults will fail here.
"""
import math
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from smo_common.orbit.propagator import OrbitPropagator, GroundStation
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.eps_basic import EPSBasicModel

CFG = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def _load(name):
    return yaml.safe_load((CFG / name).read_text())


def _propagator(orbit_cfg):
    gss = [
        GroundStation(g["name"], g["lat_deg"], g["lon_deg"],
                      g.get("alt_km", 0.0), g.get("min_elevation_deg", 5.0))
        for g in orbit_cfg["ground_stations"]
    ]
    prop = OrbitPropagator(orbit_cfg["tle_line1"], orbit_cfg["tle_line2"],
                           gss, orbit_cfg.get("earth_radius_km", 6371.0))
    prop.reset(datetime.fromisoformat(orbit_cfg["t0_epoch"].replace("Z", "+00:00")))
    return prop


# ──────────────────────────────────────────────────────────────────────────
# TASK 1 — RF link closes during a real pass
# ──────────────────────────────────────────────────────────────────────────

def test_rf_link_closes_during_real_pass():
    """Advance to the next real ground contact, tick the TTC model through it,
    and assert it achieves frame lock (link_status LOCKED) with POSITIVE margin.
    """
    orbit_cfg = _load("orbit.yaml")
    ttc_cfg = _load("subsystems/ttc.yaml")
    prop = _propagator(orbit_cfg)

    # Step to the next real ground-station contact.
    t = 0.0
    while not prop.state.in_contact and t < 8 * 3600:
        prop.advance(10.0)
        t += 10.0
    assert prop.state.in_contact, "No ground contact found within 8 h — orbit/GS geometry broken"

    ttc = TTCBasicModel()
    ttc.configure(ttc_cfg)
    # Nominal operational state after AOS: PA on, antenna deployed.
    ttc._state.pa_on = True
    ttc._state.antenna_deployed = True

    shared: dict[int, float] = {}
    frame_locked = False
    margin_at_lock = None
    ebn0_at_lock = None
    for i in range(60):  # 60 s of contact, ample for the 10 s lock sequence
        ttc.tick(1.0, prop.state, shared)
        prop.advance(1.0)
        if shared.get(0x0512) == 1 and not frame_locked:
            frame_locked = True
            margin_at_lock = shared.get(0x0503)
            ebn0_at_lock = shared.get(0x0519)

    assert shared.get(0x0510) == 1, "carrier lock not achieved during pass"
    assert shared.get(0x0511) == 1, "bit sync not achieved during pass"
    assert frame_locked, "frame sync never achieved during a real pass"
    assert shared.get(0x0501) == 2, "link_status (0x0501) not LOCKED during pass"
    # The ground would actually receive TM only with positive link margin.
    assert margin_at_lock is not None and margin_at_lock > 0.0, \
        f"link margin not positive at frame lock ({margin_at_lock} dB)"
    assert ebn0_at_lock is not None and ebn0_at_lock > 12.0, \
        f"Eb/N0 below the 12 dB threshold at lock ({ebn0_at_lock} dB)"


def test_rf_link_budget_positive_at_overhead_pass():
    """Hand link budget at a nominal overhead pass range (~450 km, zenith)
    must be comfortably positive with the configured EIRP/G-T/frequency."""
    ttc_cfg = _load("subsystems/ttc.yaml")
    ttc = TTCBasicModel()
    ttc.configure(ttc_cfg)

    rng_km = 460.0  # overhead pass: ~altitude + GS altitude
    rate = ttc._tm_rate_hi
    fspl = (20 * math.log10(rng_km * 1000)
            + 20 * math.log10(ttc._dl_freq_hz) - 147.55)
    rssi = ttc._eirp + ttc._sc_gain - fspl + 30.0
    noise_bw = 10 * math.log10(rate)
    noise_floor = -228.6 + ttc._gs_gt + noise_bw
    ebn0 = (rssi - 30 - noise_floor) + ttc._coding_gain
    margin = ebn0 - 12.0
    assert margin > 5.0, f"overhead-pass link margin too low: {margin:.1f} dB"


# ──────────────────────────────────────────────────────────────────────────
# TASK 2 — default power draw is not excessive (net-positive in sunlight)
# ──────────────────────────────────────────────────────────────────────────

def _sun_body(beta_deg, phase_deg):
    """Replicate the AOCS CSS composite sun-body vector (sweeps with phase)."""
    b = math.radians(beta_deg)
    p = math.radians(phase_deg)
    return (math.cos(b) * math.cos(p),
            math.cos(b) * math.sin(p),
            math.sin(b))


def _orbit_net_power(lines_on, wings, dur_s=5700.0, step_s=10.0):
    orbit_cfg = _load("orbit.yaml")
    eps_cfg = _load("subsystems/eps.yaml")
    eps = EPSBasicModel()
    eps.configure(eps_cfg)
    for ln in lines_on:
        eps._state.power_lines[ln] = True
    if "payload" in lines_on:
        eps._state.payload_mode = 2
    if "fpa_cooler" in lines_on:
        eps._state.fpa_cooler_on = True
    if wings:
        eps._state.wing_py_deployed = True
        eps._state.wing_my_deployed = True
    eps._state.bat_soc_pct = 75.0

    prop = _propagator(orbit_cfg)
    shared: dict[int, float] = {}
    period = 86400.0 / 15.24
    soc0 = eps._state.bat_soc_pct
    gens, conss = [], []
    t = 0.0
    while t < dur_s:
        if prop.state.in_eclipse:
            shared[0x0245] = shared[0x0246] = shared[0x0247] = 0.0
        else:
            sx, sy, sz = _sun_body(prop.state.solar_beta_deg, (t / period) * 360.0)
            shared[0x0245], shared[0x0246], shared[0x0247] = sx, sy, sz
        eps.tick(step_s, prop.state, shared)
        gens.append(eps._state.power_gen_w)
        conss.append(eps._state.power_cons_w)
        prop.advance(step_s)
        t += step_s
    n = len(gens)
    return (sum(gens) / n, sum(conss) / n, soc0, eps._state.bat_soc_pct)


def test_default_power_on_state_net_positive():
    """At power-on the EPS default config has every switchable line OFF.
    Over an orbit it must be net-positive and the battery must not discharge.
    """
    avg_gen, avg_cons, soc0, soc1 = _orbit_net_power(lines_on=[], wings=False)
    assert avg_gen - avg_cons > 0.0, \
        f"default power-on draw exceeds generation: gen={avg_gen:.1f}W cons={avg_cons:.1f}W"
    assert soc1 >= soc0, f"battery discharged in sunlight at default state ({soc0}->{soc1})"


def test_nominal_ops_with_wings_net_positive_battery_charges():
    """Nominal ops (TX + reaction wheels) with the solar wings deployed must be
    net-positive over an orbit and the battery SoC must climb, not fall — i.e.
    the default loads are right-sized against array generation."""
    avg_gen, avg_cons, soc0, soc1 = _orbit_net_power(
        lines_on=["ttc_tx", "aocs_wheels"], wings=True)
    assert avg_gen - avg_cons > 0.0, \
        f"nominal ops net-negative: gen={avg_gen:.1f}W cons={avg_cons:.1f}W"
    assert soc1 > soc0 + 1.0, \
        f"battery did not charge in sunlight under nominal ops ({soc0}->{soc1})"


def test_full_ops_with_wings_still_net_positive():
    """Even with every load on (payload imaging, FPA cooler, heaters, wheels,
    TX), once the wings are deployed the orbit-average power must stay positive
    so the battery still charges in sunlight."""
    avg_gen, avg_cons, soc0, soc1 = _orbit_net_power(
        lines_on=["ttc_tx", "payload", "fpa_cooler", "aocs_wheels",
                  "htr_bat", "htr_obc"],
        wings=True)
    assert avg_gen - avg_cons > 0.0, \
        f"full ops net-negative: gen={avg_gen:.1f}W cons={avg_cons:.1f}W"
    assert soc1 >= soc0, \
        f"battery discharged under full sunlit ops ({soc0}->{soc1})"
