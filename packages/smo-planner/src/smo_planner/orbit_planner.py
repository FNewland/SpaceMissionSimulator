"""SMO Planner — Orbit Planner. Long-horizon orbit prediction."""
from datetime import datetime, timedelta, timezone
from smo_common.orbit.propagator import OrbitPropagator, GroundStation


class OrbitPlanner:
    def __init__(self, propagator: OrbitPropagator):
        self._prop = propagator

    def predict_ground_track(self, start: datetime, duration_hours: float = 24,
                             step_s: float = 60.0) -> list[dict]:
        track = []
        steps = int(duration_hours * 3600 / step_s)
        self._prop.reset(start)
        for _ in range(steps):
            state = self._prop.advance(step_s)
            track.append({
                "utc": state.utc.isoformat(),
                "lat": round(state.lat_deg, 4),
                "lon": round(state.lon_deg, 4),
                "alt_km": round(state.alt_km, 2),
                "in_eclipse": bool(state.in_eclipse),
                "solar_beta_deg": round(state.solar_beta_deg, 2),
            })
        return track
