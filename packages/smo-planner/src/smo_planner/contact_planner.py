"""SMO Planner — Contact Window Planner."""
from datetime import datetime
from smo_common.orbit.propagator import OrbitPropagator, GroundStation


class ContactPlanner:
    def __init__(self, propagator: OrbitPropagator, ground_stations: list[GroundStation]):
        self._prop = propagator
        self._stations = ground_stations

    def compute_windows(self, start: datetime, duration_hours: float = 24) -> list[dict]:
        windows = []
        self._prop.reset(start)
        for gs in self._stations:
            ws = self._prop.contact_windows(
                duration_s=duration_hours * 3600, step_s=30.0, gs=gs)
            windows.extend(ws)
        windows.sort(key=lambda w: w["aos"])
        return windows
