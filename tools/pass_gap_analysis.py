#!/usr/bin/env python3
"""EO-Sat-1 multi-ground-station pass-gap analysis.

Propagates the mission TLE for 48 h from the mission T-0 epoch, computes
contact windows against ALL ground stations in configs/eosat1/orbit.yaml
(the canonical station list, shared with the planner), and prints gap
statistics between successive contacts (any station).

Rationale: with Iqaluit (63.7N) + Troll (72.0S) on a ~94.5-min SSO, passes
alternate north/south, so the typical gap between successive contacts should
be roughly half an orbit (~47 min) rather than a full orbit or more.

Usage:
    python tools/pass_gap_analysis.py [--hours 48] [--config configs/eosat1]
"""
import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from smo_common.config.loader import load_orbit_config  # noqa: E402
from smo_common.orbit.propagator import OrbitPropagator, GroundStation  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--step", type=float, default=10.0, help="scan step (s)")
    ap.add_argument("--config", default=str(REPO_ROOT / "configs" / "eosat1"))
    args = ap.parse_args()

    cfg = load_orbit_config(Path(args.config))
    stations = [
        GroundStation(name=g.name, lat_deg=g.lat_deg, lon_deg=g.lon_deg,
                      alt_km=g.alt_km, min_elevation_deg=g.min_elevation_deg)
        for g in cfg.ground_stations
    ]
    prop = OrbitPropagator(cfg.tle_line1, cfg.tle_line2,
                           ground_stations=stations,
                           earth_radius_km=cfg.earth_radius_km)
    # t0_epoch is not part of the pydantic OrbitConfig schema — read raw yaml.
    import yaml
    raw = yaml.safe_load((Path(args.config) / "orbit.yaml").read_text()) or {}
    t0_str = raw.get("t0_epoch")
    t0 = datetime.fromisoformat(t0_str.replace("Z", "+00:00")) \
        if t0_str else datetime.now(timezone.utc)
    prop.reset(t0)

    # Merged all-station windows (gs=None), sorted by AOS, tagged gs_name.
    windows = prop.contact_windows(duration_s=args.hours * 3600.0,
                                   step_s=args.step)
    if not windows:
        print("No contact windows found — check TLE / station config.")
        return 1

    days = args.hours / 24.0
    per_station: dict[str, list[dict]] = {}
    for w in windows:
        per_station.setdefault(w["gs_name"], []).append(w)

    # Gaps between successive contacts (LOS[i] -> AOS[i+1]), any station.
    gaps = []
    for a, b in zip(windows, windows[1:]):
        los = datetime.fromisoformat(a["los"])
        aos = datetime.fromisoformat(b["aos"])
        gap_min = (aos - los).total_seconds() / 60.0
        if gap_min > 0:
            gaps.append(gap_min)

    # Orbit period from TLE mean motion (rev/day).
    mm = float(cfg.tle_line2[52:63])
    period_min = 1440.0 / mm

    print(f"Epoch: {t0.isoformat()}   span: {args.hours:.0f} h   "
          f"orbit period: {period_min:.1f} min (half-orbit ~{period_min/2:.1f} min)")
    print(f"Total passes: {len(windows)} ({len(windows)/days:.1f}/day)")
    for name, ws in sorted(per_station.items()):
        durs = [w["duration_s"] for w in ws]
        els = [w["max_elevation_deg"] for w in ws]
        print(f"  {name:10s}: {len(ws):3d} passes ({len(ws)/days:.1f}/day), "
              f"mean duration {statistics.mean(durs)/60.0:.1f} min, "
              f"max elevation median {statistics.median(els):.1f} deg")

    print(f"Gaps between successive contacts (any station), n={len(gaps)}:")
    print(f"  min {min(gaps):.1f} min | median {statistics.median(gaps):.1f} min | "
          f"mean {statistics.mean(gaps):.1f} min | max {max(gaps):.1f} min")

    # Simple distribution
    print("  Distribution:")
    bins = [(0, 15), (15, 30), (30, 60), (60, 90), (90, 120), (120, 1e9)]
    for lo, hi in bins:
        n = sum(1 for g in gaps if lo <= g < hi)
        label = f"{lo:>3.0f}-{hi:<4.0f}" if hi < 1e9 else f"{lo:>3.0f}+   "
        print(f"    {label} min: {n:3d}  {'#' * n}")

    half = period_min / 2.0
    med = statistics.median(gaps)
    ratio = med / half
    if ratio <= 1.2:
        note = "~half an orbit"
    elif ratio <= 2.2:
        note = ("between half and one orbit — some orbits miss one station's "
                "longitude window (expected orbital mechanics, not a defect)")
    else:
        note = "> one orbit — check station config"
    print(f"Median gap / half-orbit ratio: {ratio:.2f} ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
