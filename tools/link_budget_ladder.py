#!/usr/bin/env python3
"""EO-Sat-1 TM data-rate ladder — link-margin table per tier per station.

Reproduces the arithmetic of the data-rate research report (sections B.1-B.8)
directly from the shipped configuration, so the numbers Franz reads in the
report and the numbers the simulator flies are the same numbers.

For every rung of ``configs/eosat1/subsystems/ttc.yaml``'s ``tm_rate_tiers``
and every station in ``configs/eosat1/planning/ground_stations.yaml`` it
computes, at 5 deg elevation and at zenith:

    slant range   d(eps) = R_e * [ sqrt((r/R_e)^2 - cos^2 eps) - sin eps ]
    FSPL          20log10(d) + 20log10(f) - 147.55
    C/N0          EIRP + G_sc - FSPL - L_atm - L_pol - L_impl + G/T + 228.6
    Eb/N0         C/N0 - 10log10(R_b)
    margin        Eb/N0 - (required_eb_n0 - coding gain)

and the occupied bandwidth of each tier against the 5.00 MHz per-mission
allocation in 2200-2290 MHz.

The same model object the simulator uses (``TTCBasicModel.link_margin_for``)
does the arithmetic, so this tool cannot drift from the flight model.

Usage:
    python tools/link_budget_ladder.py [--config configs/eosat1]
                                       [--elevations 5,10,30,90]
                                       [--altitude-km 513]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "smo-simulator" / "src"))
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_simulator.models.ttc_basic import TTCBasicModel  # noqa: E402

#: NASA Explorers S-band table: a mission may occupy at most 5.00 MHz in
#: 2200-2290 MHz. This is the ceiling the ladder actually runs into — the
#: link closes with tens of dB to spare at every tier.
CHANNEL_BW_HZ = 5.0e6

#: Equatorial Earth radius used by the research report's geometry table.
EARTH_RADIUS_KM = 6378.137


def slant_range_km(elevation_deg: float, altitude_km: float) -> float:
    """Slant range to a ground station at the given elevation.

    d(eps) = R_e * [ sqrt((r/R_e)^2 - cos^2 eps) - sin eps ]

    At the 513 km mean altitude (2026-08 epoch/altitude ruling) this gives
    2603.4 km at 0 deg, 2110.9 km at 5 deg, 1723.5 km at 10 deg and 513.0 km
    at zenith.
    """
    r_ratio = (EARTH_RADIUS_KM + altitude_km) / EARTH_RADIUS_KM
    eps = math.radians(elevation_deg)
    return EARTH_RADIUS_KM * (
        math.sqrt(r_ratio ** 2 - math.cos(eps) ** 2) - math.sin(eps))


def load_model(config_dir: Path) -> TTCBasicModel:
    cfg = yaml.safe_load((config_dir / "subsystems" / "ttc.yaml").read_text())
    model = TTCBasicModel()
    model.configure(cfg)
    return model


def load_stations(config_dir: Path) -> list[tuple[str, float]]:
    gs_file = config_dir / "planning" / "ground_stations.yaml"
    data = yaml.safe_load(gs_file.read_text())
    stations = data.get("ground_stations") or data.get("stations") or []
    return [(s["name"], float(s.get("g_t_db", 20.0))) for s in stations]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(REPO / "configs" / "eosat1"))
    ap.add_argument("--elevations", default="5,10,30,90")
    # 2026-08: default 450 -> 513 (orbit.yaml TLE-true mean altitude).
    ap.add_argument("--altitude-km", type=float, default=513.0)
    ap.add_argument("--min-margin-db", type=float, default=3.0)
    args = ap.parse_args(argv)

    config_dir = Path(args.config)
    model = load_model(config_dir)
    stations = load_stations(config_dir)
    elevations = [float(x) for x in args.elevations.split(",") if x.strip()]

    print(f"EO-Sat-1 TM data-rate ladder — link margins")
    print(f"  config      : {config_dir}")
    print(f"  altitude    : {args.altitude_km:.0f} km")
    print(f"  downlink    : {model._dl_freq_hz / 1e6:.1f} MHz")
    print(f"  EIRP + G_sc : {model._eirp:.1f} dBW + {model._sc_gain:.1f} dBi")
    print(f"  losses      : atm(zenith) {model._atmospheric_loss:.1f} + pol "
          f"{model._polarization_loss:.1f} + impl {model._implementation_loss:.1f} dB")
    print(f"  required    : {model._required_eb_n0:.1f} dB uncoded, "
          f"less the tier's coding gain")
    print()

    print("Geometry")
    print(f"  {'elev':>5}  {'range (km)':>11}  {'FSPL (dB)':>10}")
    for el in elevations:
        d = slant_range_km(el, args.altitude_km)
        fspl = 20 * math.log10(d * 1000) + 20 * math.log10(model._dl_freq_hz) - 147.55
        print(f"  {el:5.0f}  {d:11.1f}  {fspl:10.2f}")
    print()

    print("Tiers")
    hdr = (f"  {'tier':<9}{'bps':>9}  {'mod':<6}{'coding':<9}"
           f"{'Rs (Bd)':>10}{'occupied BW':>14}{'% of 5 MHz':>12}")
    print(hdr)
    for i, t in enumerate(model.tiers):
        bw = model.tier_occupied_bw_hz(i)
        print(f"  {t['name']:<9}{t['bps']:>9}  "
              f"{str(t.get('modulation', 'BPSK')):<6}"
              f"{str(t.get('coding', 'RS')):<9}"
              f"{model.tier_symbol_rate(i):>10.0f}"
              f"{bw / 1000.0:>11.1f} kHz"
              f"{100.0 * bw / CHANNEL_BW_HZ:>11.1f}%")
    print()

    worst = None
    failures: list[str] = []
    for el in elevations:
        d = slant_range_km(el, args.altitude_km)
        print(f"Link margin (dB) at {el:.0f} deg elevation, range {d:.1f} km")
        print("  " + f"{'station':<12}{'G/T':>6}" +
              "".join(f"{t['name']:>11}" for t in model.tiers))
        for name, gt in stations:
            row = [f"  {name:<12}{gt:>6.1f}"]
            for i, t in enumerate(model.tiers):
                lb = model.link_margin_for(i, d, el, gs_gt_db=gt)
                m = lb["margin_db"]
                row.append(f"{m:>11.1f}")
                if worst is None or m < worst[0]:
                    worst = (m, name, t["name"], el)
                if m < args.min_margin_db:
                    failures.append(
                        f"{t['name']} at {name} / {el:.0f} deg: "
                        f"{m:.1f} dB < {args.min_margin_db:.1f} dB")
            print("".join(row))
        print()

    if worst:
        print(f"Worst case: {worst[2]} at {worst[1]}, {worst[3]:.0f} deg "
              f"= {worst[0]:.1f} dB")
    over_bw = [t["name"] for i, t in enumerate(model.tiers)
               if model.tier_occupied_bw_hz(i) > CHANNEL_BW_HZ]
    if over_bw:
        print(f"FAIL: tiers exceeding the {CHANNEL_BW_HZ / 1e6:.2f} MHz "
              f"channel allocation: {', '.join(over_bw)}")
    if failures:
        print(f"FAIL: {len(failures)} tier/station/elevation combinations "
              f"below {args.min_margin_db:.1f} dB:")
        for f in failures:
            print(f"  {f}")
    if failures or over_bw:
        return 1
    print(f"OK: every tier closes with >= {args.min_margin_db:.1f} dB at every "
          f"station and elevation, and every tier fits the "
          f"{CHANNEL_BW_HZ / 1e6:.2f} MHz channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
