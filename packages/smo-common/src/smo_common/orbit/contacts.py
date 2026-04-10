"""SMO Common — Ground Station Contact Window Computation."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from .propagator import OrbitPropagator, GroundStation


def compute_contact_windows(
    propagator: OrbitPropagator,
    ground_station: GroundStation,
    start_utc: datetime,
    duration_s: float = 86400.0,
    step_s: float = 30.0,
) -> list[dict]:
    """Compute contact windows for a ground station over a time period."""
    return propagator.contact_windows(
        duration_s=duration_s,
        step_s=step_s,
        gs=ground_station,
    )


def compute_all_contacts(
    propagator: OrbitPropagator,
    ground_stations: list[GroundStation],
    start_utc: datetime,
    duration_s: float = 86400.0,
    step_s: float = 30.0,
) -> list[dict]:
    """Compute contact windows for all ground stations."""
    all_windows = []
    for gs in ground_stations:
        windows = propagator.contact_windows(
            duration_s=duration_s,
            step_s=step_s,
            gs=gs,
        )
        all_windows.extend(windows)
    # Sort by AOS time
    all_windows.sort(key=lambda w: w["aos"])
    return all_windows
