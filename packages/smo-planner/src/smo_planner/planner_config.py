"""SMO Planner — planner-specific configuration and station alignment.

The planner has its own config file, ``configs/<mission>/planning/planner.yaml``,
holding settings that belong to the *planning tool* rather than to the
spacecraft or the simulator: time-source defaults, planning horizons, refresh
intervals, budget parameters, default-plan behaviour and per-station planning
attributes (priority, bands, scheduling constraints).

Ground-station GEOMETRY is NOT owned by this file. ``orbit.yaml`` remains the
canonical source (defect #42); the planner's station block adds planning
attributes on top of it and may repeat the geometry for readability. The two
are reconciled by :func:`check_station_alignment`, which is run:

* at planner start-up (a load-time validation warning — see
  ``PlannerServer._check_station_alignment``), and
* on demand, either via ``GET /api/config/alignment`` or from the CLI::

      python -m smo_planner.planner_config --config configs/eosat1

  which prints a side-by-side alignment report and exits non-zero on
  divergence (usable as a CI check).

Schema validation follows the smo-common config pattern (pydantic v2 models +
a ``load_*_config`` helper built on ``smo_common.config.loader.load_yaml``), but
the models live here rather than in smo-common because they describe the
planner application, not the mission/spacecraft.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PLANNER_CONFIG_RELPATH = Path("planning") / "planner.yaml"


# ── Schema ──────────────────────────────────────────────────────────

class PlannerTimeConfig(BaseModel):
    """Planner clock behaviour (see server.py time-source resolution)."""
    default_time_source: str = Field(
        default="sim",
        description='Fallback clock source when neither CLI, env nor '
                    'mission.yaml specify one: "sim" or "real".')
    sim_state_url: str | None = Field(
        default=None,
        description="Simulator state endpoint override; None derives "
                    "http://<connect_host>:8080/api/state.")
    anchor_refresh_s: float = Field(
        default=1.5, description="Background sim-clock anchor refresh period (wall s).")
    jump_tolerance_s: float = Field(
        default=5.0,
        description="Sim-time discontinuity threshold. If a refreshed anchor "
                    "differs from what the previous anchor predicted by more "
                    "than this (scaled by speed), a breakpoint/scenario jump is "
                    "declared and cached plan products are invalidated.")
    stale_anchor_s: float = Field(
        default=60.0,
        description="Anchor age beyond which the clock is reported degraded.")


class PlannerHorizonConfig(BaseModel):
    """Planning horizons / look-ahead."""
    contacts_hours: float = 24.0
    contacts_step_s: float = 10.0
    ground_track_hours: float = 3.0
    ground_track_step_s: float = 30.0
    imaging_hours: float = 24.0
    imaging_step_s: float = 60.0
    timeline_hours: float = Field(
        default=24.0, description="Width of the UI timeline window.")


class PlannerRefreshConfig(BaseModel):
    """Recompute / poll intervals."""
    recompute_interval_s: float = Field(
        default=600.0,
        description="Periodic contact + ground-track recompute period. Also "
                    "recomputed immediately on a sim-time discontinuity.")
    sim_recompute_interval_s: float = Field(
        default=1800.0,
        description="Recompute when the cached products are older than this "
                    "many SIM seconds (covers time-accelerated runs).")
    poll_interval_s: float = 30.0
    spacecraft_refresh_s: float = 5.0


class PlannerBudgetConfig(BaseModel):
    """Power/data budget parameters.

    ``None`` means "take the value from the spacecraft config" (payload.yaml /
    ttc.yaml / eps.yaml) — the planner must not carry its own copy of a number
    the spacecraft already declares.
    """
    initial_soc_percent: float = 80.0
    min_soc_percent: float = 25.0
    battery_capacity_wh: float | None = Field(
        default=None, description="None -> subsystems/eps.yaml battery.capacity_wh")
    storage_capacity_mb: float | None = Field(
        default=None, description="None -> subsystems/payload.yaml total_storage_mb")
    image_size_mb: float | None = Field(
        default=None, description="None -> subsystems/payload.yaml image_size_mb")
    downlink_rate_bps: float | None = Field(
        default=None, description="None -> subsystems/ttc.yaml tm_rate_hi_bps")
    protocol_overhead: float = Field(
        default=0.20, description="Framing/coding overhead fraction on the downlink.")
    storage_margin_percent: float = 10.0
    hk_background_mb_per_hour: float = Field(
        default=0.0,
        description="Housekeeping data accruing outside planned activities. "
                    "Default 0: the S15 HK store is CIRCULAR, so it never "
                    "overflows and never blocks a plan — the science store is "
                    "what the data budget models. Set > 0 to model HK against "
                    "the same store for a training exercise.")
    warn_utilization_percent: float = 80.0


class PlannerImagingConfig(BaseModel):
    """Camera + imaging-opportunity feasibility parameters."""
    fov_deg: float = 15.0
    swath_km: float | None = Field(
        default=None, description="None -> 2*alt*tan(fov/2) from orbit.yaml altitude.")
    max_off_nadir_deg: float = Field(
        default=7.5,
        description="Largest roll the payload may be pointed off nadir for a "
                    "capture. Opportunities needing more are infeasible.")
    capture_duration_s: float = 120.0
    capture_power_w: float = 60.0
    default_min_solar_elevation_deg: float = 10.0
    enforce_solar_elevation: bool = True
    enforce_revisit: bool = True


class PlannerDefaultPlanConfig(BaseModel):
    """What the planner's schedule contains at start-up."""
    start_empty: bool = Field(
        default=True,
        description="The planner starts with an EMPTY schedule. Standard "
                    "activities are loaded explicitly by the operator.")
    templates_file: str = "planning/standard_plans.yaml"
    default_template: str = "nominal_ops_day"


class PlannerAlignmentConfig(BaseModel):
    """How planner<->orbit.yaml station divergence is treated."""
    canonical_source: str = Field(
        default="orbit.yaml",
        description="Informational: the file that owns station geometry.")
    on_divergence: str = Field(
        default="warn", description="warn | error | ignore")
    position_tolerance_deg: float = 0.001    # ~110 m
    altitude_tolerance_km: float = 0.01      # 10 m
    elevation_tolerance_deg: float = 0.1


class PlannerStationConfig(BaseModel):
    """Per-station PLANNING attributes (geometry is canonical in orbit.yaml)."""
    name: str
    enabled: bool = True
    priority: int = Field(
        default=50, description="Higher wins when two stations offer the same slot.")
    bands: list[str] = Field(default_factory=lambda: ["S"])
    supports_payload_downlink: bool = False
    downlink_rate_bps: float | None = Field(
        default=None, description="Station-specific rate; None -> budget default.")
    setup_time_s: float = Field(
        default=60.0, description="Ground+onboard configuration before AOS-usable time.")
    teardown_time_s: float = 30.0
    min_dump_elevation_deg: float = Field(
        default=10.0, description="Elevation below which a dump is not planned.")
    max_passes_per_day: int | None = None
    notes: str = ""
    # Optional geometry mirror — validated against orbit.yaml, never used as
    # the source of truth.
    lat_deg: float | None = None
    lon_deg: float | None = None
    alt_km: float | None = None
    min_elevation_deg: float | None = None


class PlannerConfig(BaseModel):
    """configs/<mission>/planning/planner.yaml."""
    time: PlannerTimeConfig = Field(default_factory=PlannerTimeConfig)
    horizons: PlannerHorizonConfig = Field(default_factory=PlannerHorizonConfig)
    refresh: PlannerRefreshConfig = Field(default_factory=PlannerRefreshConfig)
    budget: PlannerBudgetConfig = Field(default_factory=PlannerBudgetConfig)
    imaging: PlannerImagingConfig = Field(default_factory=PlannerImagingConfig)
    default_plan: PlannerDefaultPlanConfig = Field(
        default_factory=PlannerDefaultPlanConfig)
    alignment: PlannerAlignmentConfig = Field(default_factory=PlannerAlignmentConfig)
    stations: list[PlannerStationConfig] = Field(default_factory=list)

    def station(self, name: str) -> PlannerStationConfig | None:
        for s in self.stations:
            if s.name == name:
                return s
        return None


# ── Loader ──────────────────────────────────────────────────────────

def load_planner_config(config_dir: str | Path) -> PlannerConfig:
    """Load planning/planner.yaml, or all-defaults if it is absent.

    A config directory without planner.yaml behaves exactly as the planner did
    before the file existed (no stations declared -> no alignment constraint).
    """
    path = Path(config_dir) / PLANNER_CONFIG_RELPATH
    if not path.exists():
        logger.info("No planner.yaml in %s — using built-in planner defaults", config_dir)
        return PlannerConfig()
    from smo_common.config.loader import load_yaml
    data = load_yaml(path)
    cfg = PlannerConfig.model_validate(data or {})
    logger.info("Loaded planner config: %s (%d stations)", path, len(cfg.stations))
    return cfg


# ── Station alignment ───────────────────────────────────────────────

_STATUS_OK = "ok"
_STATUS_MISSING_IN_PLANNER = "missing_in_planner"
_STATUS_MISSING_IN_ORBIT = "missing_in_orbit"
_STATUS_GEOMETRY_MISMATCH = "geometry_mismatch"
_STATUS_DISABLED = "disabled_in_planner"


def check_station_alignment(
    planner_cfg: PlannerConfig,
    orbit_stations: list,
) -> dict:
    """Compare the planner's station list with orbit.yaml's canonical list.

    ``orbit_stations`` is any sequence of objects/dicts exposing
    ``name``/``lat_deg``/``lon_deg``/``alt_km``/``min_elevation_deg``
    (``OrbitConfig.ground_stations`` or ``GroundStation`` objects both work).

    Returns a JSON-serialisable report::

        {"aligned": bool, "canonical_source": "orbit.yaml",
         "on_divergence": "warn", "issues": [str, ...],
         "stations": [{"name", "status", "in_orbit", "in_planner",
                       "orbit": {...}|None, "planner": {...}|None,
                       "deltas": {...}}, ...]}

    A planner station that declares no geometry is *not* a mismatch: geometry
    is optional there and inherited from orbit.yaml. A planner config with no
    stations at all is aligned by definition (nothing is claimed).
    """
    tol = planner_cfg.alignment

    def _geom(o) -> dict:
        get = (lambda k: o.get(k)) if isinstance(o, dict) else (lambda k: getattr(o, k, None))
        return {
            "name": get("name"),
            "lat_deg": get("lat_deg"),
            "lon_deg": get("lon_deg"),
            "alt_km": get("alt_km"),
            "min_elevation_deg": get("min_elevation_deg"),
        }

    orbit_map = {}
    for o in orbit_stations or []:
        g = _geom(o)
        orbit_map[g["name"]] = g

    planner_map = {s.name: s for s in planner_cfg.stations}

    issues: list[str] = []
    rows: list[dict] = []

    for name in sorted(set(orbit_map) | set(planner_map)):
        o = orbit_map.get(name)
        p = planner_map.get(name)
        row: dict = {
            "name": name,
            "in_orbit": o is not None,
            "in_planner": p is not None,
            "orbit": o,
            "planner": None,
            "deltas": {},
            "status": _STATUS_OK,
        }
        if p is not None:
            row["planner"] = {
                "name": p.name,
                "enabled": p.enabled,
                "priority": p.priority,
                "bands": list(p.bands),
                "lat_deg": p.lat_deg,
                "lon_deg": p.lon_deg,
                "alt_km": p.alt_km,
                "min_elevation_deg": p.min_elevation_deg,
            }

        if o is None:
            row["status"] = _STATUS_MISSING_IN_ORBIT
            issues.append(
                f"{name}: declared in planner.yaml but absent from orbit.yaml "
                f"(canonical). The planner would offer a station the simulator "
                f"never sees.")
        elif p is None:
            row["status"] = _STATUS_MISSING_IN_PLANNER
            issues.append(
                f"{name}: present in orbit.yaml but missing from planner.yaml "
                f"stations — no planning attributes (priority/bands/limits) "
                f"are defined for it.")
        else:
            deltas = {}
            for field, tolerance in (
                ("lat_deg", tol.position_tolerance_deg),
                ("lon_deg", tol.position_tolerance_deg),
                ("alt_km", tol.altitude_tolerance_km),
                ("min_elevation_deg", tol.elevation_tolerance_deg),
            ):
                pv = getattr(p, field)
                if pv is None:
                    continue  # inherited from orbit.yaml — not a claim
                ov = o.get(field)
                if ov is None:
                    continue
                d = float(pv) - float(ov)
                deltas[field] = round(d, 6)
                if abs(d) > tolerance:
                    row["status"] = _STATUS_GEOMETRY_MISMATCH
                    issues.append(
                        f"{name}: {field} differs — orbit.yaml {ov}, "
                        f"planner.yaml {pv} (delta {d:+.4f}, tolerance "
                        f"{tolerance})")
            row["deltas"] = deltas
            if row["status"] == _STATUS_OK and not p.enabled:
                row["status"] = _STATUS_DISABLED
        rows.append(row)

    aligned = not issues
    return {
        "aligned": aligned,
        "canonical_source": tol.canonical_source,
        "on_divergence": tol.on_divergence,
        "orbit_station_count": len(orbit_map),
        "planner_station_count": len(planner_map),
        "issues": issues,
        "stations": rows,
    }


def format_alignment_report(report: dict) -> str:
    """Render :func:`check_station_alignment` output as a side-by-side table."""
    lines: list[str] = []
    lines.append("Ground-station alignment — planner.yaml vs orbit.yaml "
                 f"(canonical: {report.get('canonical_source')})")
    lines.append("")
    hdr = (f"{'STATION':<12} {'ORBIT.YAML (lat/lon/alt/mask)':<38} "
           f"{'PLANNER.YAML':<30} {'STATUS':<18}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    def _fmt_geom(g: dict | None) -> str:
        if not g:
            return "-"
        if g.get("lat_deg") is None:
            return "(inherits orbit.yaml)"
        return (f"{g['lat_deg']:>9.4f} {g['lon_deg']:>10.4f} "
                f"{(g.get('alt_km') or 0.0):>6.3f}km {(g.get('min_elevation_deg') or 0.0):>4.1f}deg")

    for row in report.get("stations", []):
        planner = row.get("planner")
        extra = ""
        if planner:
            extra = f"prio {planner['priority']:>3} {'/'.join(planner['bands'])}"
            if planner.get("lat_deg") is None:
                extra = "(inherits) " + extra
        lines.append(
            f"{row['name']:<12} {_fmt_geom(row.get('orbit')):<38} "
            f"{extra or '-':<30} {row['status']:<18}")

    lines.append("")
    if report.get("aligned"):
        lines.append(
            f"ALIGNED — {report.get('orbit_station_count')} station(s) in "
            f"orbit.yaml, {report.get('planner_station_count')} in planner.yaml.")
    else:
        lines.append(f"DIVERGENT — {len(report['issues'])} issue(s):")
        for i in report["issues"]:
            lines.append(f"  * {i}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: print the planner/orbit station alignment report.

    Exit code 0 = aligned, 2 = divergent (so it can gate CI).
    """
    ap = argparse.ArgumentParser(
        description="Print the planner<->orbit.yaml ground-station alignment report.")
    ap.add_argument("--config", default="configs/eosat1",
                    help="Mission config directory (default: configs/eosat1)")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    args = ap.parse_args(argv)

    from smo_common.config.loader import load_orbit_config

    config_dir = Path(args.config)
    planner_cfg = load_planner_config(config_dir)
    orbit_cfg = load_orbit_config(config_dir)
    report = check_station_alignment(planner_cfg, orbit_cfg.ground_stations)

    if args.json:
        import json
        print(json.dumps(report, indent=2))
    else:
        print(format_alignment_report(report))
    return 0 if report["aligned"] else 2


if __name__ == "__main__":
    sys.exit(main())
