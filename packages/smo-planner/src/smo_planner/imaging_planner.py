"""SMO Planner — Imaging Target Planner.

Computes imaging opportunities for ocean current monitoring targets,
determines when the spacecraft ground track intersects target bounding
boxes during sunlit conditions, and generates capture command sequences.

EOSAT-1 camera parameters:
  - Altitude: 450 km
  - FOV: 15 degrees
  - Swath width: ~120 km (2 * alt * tan(FOV/2))
  - Minimum solar elevation for imaging: 10 degrees
"""
from __future__ import annotations

import math
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from smo_planner.utils import parse_iso as _parse_iso

logger = logging.getLogger(__name__)

# Camera/orbit constants
ALTITUDE_KM = 450.0
FOV_DEG = 15.0
SWATH_KM = 2.0 * ALTITUDE_KM * math.tan(math.radians(FOV_DEG / 2.0))  # ~118 km
MIN_SOLAR_ELEVATION_DEG = 10.0
IMAGING_DURATION_S = 120  # seconds per imaging pass
IMAGING_POWER_W = 60
IMAGING_DATA_MB = 800

# Approximate Earth radius for distance calculations
EARTH_RADIUS_KM = 6371.0


class ImagingTarget:
    """An imaging target defined by a lat/lon bounding box."""

    def __init__(self, target_cfg: dict):
        self.id = target_cfg.get("id", "unknown")
        self.name = target_cfg.get("name", "unnamed")
        self.description = target_cfg.get("description", "")
        self.priority = target_cfg.get("priority", "medium")
        self.revisit_days = target_cfg.get("revisit_days", 7)
        self.min_solar_elevation_deg = target_cfg.get(
            "min_solar_elevation_deg", MIN_SOLAR_ELEVATION_DEG
        )

        region = target_cfg.get("region", {})
        self.min_lat = region.get("min_lat", -90.0)
        self.max_lat = region.get("max_lat", 90.0)
        self.min_lon = region.get("min_lon", -180.0)
        self.max_lon = region.get("max_lon", 180.0)

        # Center for reference
        self.center_lat = (self.min_lat + self.max_lat) / 2.0
        self.center_lon = (self.min_lon + self.max_lon) / 2.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "priority": self.priority,
            "revisit_days": self.revisit_days,
            "min_solar_elevation_deg": self.min_solar_elevation_deg,
            "region": {
                "min_lat": self.min_lat,
                "max_lat": self.max_lat,
                "min_lon": self.min_lon,
                "max_lon": self.max_lon,
            },
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
        }

    def contains_point(self, lat: float, lon: float) -> bool:
        """Check if a point falls within the target bounding box."""
        return (
            self.min_lat <= lat <= self.max_lat
            and self.min_lon <= lon <= self.max_lon
        )

    def within_swath(self, lat: float, lon: float, swath_km: float = SWATH_KM) -> bool:
        """Check if the target region is reachable from a given sub-satellite point.

        The target is considered reachable if the sub-satellite point is within
        half a swath width of the bounding box edges.
        """
        # Convert swath to approximate degrees
        lat_margin = swath_km / (2.0 * 111.0)  # ~111 km per degree latitude
        lon_margin = swath_km / (
            2.0 * 111.0 * max(math.cos(math.radians(lat)), 0.01)
        )

        return (
            (self.min_lat - lat_margin) <= lat <= (self.max_lat + lat_margin)
            and (self.min_lon - lon_margin) <= lon <= (self.max_lon + lon_margin)
        )


class ImagingPlanner:
    """Plans imaging activities for ocean current monitoring targets."""

    def __init__(self, targets: list[dict] | None = None):
        self._targets: list[ImagingTarget] = []
        if targets:
            for t in targets:
                self._targets.append(ImagingTarget(t))

    def load_targets_from_config(self, config_path: Path) -> None:
        """Load imaging targets from a YAML config file."""
        try:
            import yaml
            with open(config_path) as f:
                data = yaml.safe_load(f)
            target_list = data.get("imaging_targets", [])
            self._targets = [ImagingTarget(t) for t in target_list]
            logger.info("Loaded %d imaging targets from %s",
                        len(self._targets), config_path)
        except Exception as e:
            logger.error("Failed to load imaging targets: %s", e)

    def get_targets(self) -> list[dict]:
        """Return all configured imaging targets as dicts."""
        return [t.to_dict() for t in self._targets]

    def compute_opportunities(
        self,
        ground_track: list[dict],
        duration_hours: float = 24.0,
    ) -> list[dict]:
        """Compute imaging opportunities from a ground track.

        For each ground track point, check if any target falls within the
        camera swath and if the conditions are met (sunlit, not in eclipse).

        Args:
            ground_track: list of dicts with utc, lat, lon, alt_km, in_eclipse
            duration_hours: planning horizon (for reference)

        Returns:
            list of opportunity dicts with target info, timing, and geometry
        """
        if not self._targets or not ground_track:
            return []

        opportunities: list[dict] = []
        active_targets: dict[str, dict | None] = {t.id: None for t in self._targets}

        for point in ground_track:
            lat = point.get("lat", 0.0)
            lon = point.get("lon", 0.0)
            utc_str = point.get("utc", "")
            in_eclipse = point.get("in_eclipse", False)
            alt_km = point.get("alt_km", ALTITUDE_KM)

            # Skip eclipse points (need sunlight for imaging)
            if in_eclipse:
                # End any active opportunities
                for tid in list(active_targets):
                    if active_targets[tid] is not None:
                        opp = active_targets[tid]
                        opp["end_utc"] = utc_str
                        opportunities.append(opp)
                        active_targets[tid] = None
                continue

            # Check solar elevation (approximate: if not in eclipse and dayside, >10 deg)
            solar_ok = not in_eclipse  # simplified check

            for target in self._targets:
                if target.within_swath(lat, lon):
                    if solar_ok and active_targets[target.id] is None:
                        # Start a new opportunity
                        active_targets[target.id] = {
                            "target_id": target.id,
                            "target_name": target.name,
                            "priority": target.priority,
                            "start_utc": utc_str,
                            "end_utc": utc_str,
                            "sub_sat_lat": lat,
                            "sub_sat_lon": lon,
                            "alt_km": alt_km,
                            "center_lat": target.center_lat,
                            "center_lon": target.center_lon,
                            "swath_km": round(SWATH_KM, 1),
                        }
                    elif active_targets[target.id] is not None:
                        # Update end time
                        active_targets[target.id]["end_utc"] = utc_str
                else:
                    # Target not in swath — close any active opportunity
                    if active_targets[target.id] is not None:
                        opp = active_targets[target.id]
                        opp["end_utc"] = utc_str
                        opportunities.append(opp)
                        active_targets[target.id] = None

        # Close any still-open opportunities
        for tid in active_targets:
            if active_targets[tid] is not None:
                opportunities.append(active_targets[tid])

        # Sort by start time
        opportunities.sort(key=lambda o: o.get("start_utc", ""))

        # Add computed duration to each
        for opp in opportunities:
            start = _parse_iso(opp.get("start_utc", ""))
            end = _parse_iso(opp.get("end_utc", ""))
            if start and end:
                opp["duration_s"] = round((end - start).total_seconds(), 1)
            else:
                opp["duration_s"] = 0.0

        return opportunities

    def generate_capture_sequence(
        self,
        target_id: str,
        start_time: str,
        lat: float | None = None,
        lon: float | None = None,
    ) -> dict:
        """Generate a capture command sequence for a target.

        Returns an activity dict suitable for the ActivityScheduler.
        """
        target = None
        for t in self._targets:
            if t.id == target_id:
                target = t
                break

        if target is None:
            raise ValueError(f"Unknown target ID: {target_id}")

        capture_lat = lat if lat is not None else target.center_lat
        capture_lon = lon if lon is not None else target.center_lon

        # Generate S8 function command with lat/lon parameters
        command_sequence = [
            {
                "service": 8,
                "subtype": 1,
                "func_id": 0x14,
                "description": "PAYLOAD_IMAGER_ON",
            },
            {"wait_s": 5},
            {
                "service": 8,
                "subtype": 1,
                "func_id": 0x16,
                "description": f"PAYLOAD_CAPTURE lat={capture_lat:.2f} lon={capture_lon:.2f}",
                "parameters": {
                    "target_lat": capture_lat,
                    "target_lon": capture_lon,
                    "target_id": target_id,
                },
            },
            {
                "wait_for": {
                    "parameter": "payload.imaging_active",
                    "value": True,
                    "timeout_s": 30,
                },
            },
        ]

        return {
            "name": f"imaging_{target_id}",
            "start_time": start_time,
            "duration_s": IMAGING_DURATION_S,
            "power_w": IMAGING_POWER_W,
            "data_volume_mb": IMAGING_DATA_MB,
            "priority": target.priority,
            "procedure_ref": "NOM-001",
            "command_sequence": command_sequence,
            "target_id": target_id,
            "target_name": target.name,
            "capture_lat": capture_lat,
            "capture_lon": capture_lon,
        }



# _parse_iso imported from smo_planner.utils
