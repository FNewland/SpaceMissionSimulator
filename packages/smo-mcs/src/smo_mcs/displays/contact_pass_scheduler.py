"""Ground Station Pass Schedule Panel.

Displays next contact windows with AOS/LOS times, elevation, duration, etc.
"""
import time as _time_mod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ContactPass:
    """Represents a single contact pass."""
    aos_time: float  # Unix timestamp
    los_time: float  # Unix timestamp
    max_elevation: float  # degrees
    ground_station: str
    data_downlink_capacity: float  # MB or percentage

    @property
    def duration_s(self) -> float:
        return self.los_time - self.aos_time

    @property
    def duration_min(self) -> float:
        return self.duration_s / 60.0

    def status_at_time(self, current_time: float) -> dict:
        """Return current status relative to this pass."""
        if current_time < self.aos_time:
            return {
                "status": "upcoming",
                "time_to_aos": self.aos_time - current_time,
            }
        elif current_time < self.los_time:
            return {
                "status": "in_contact",
                "time_to_los": self.los_time - current_time,
                "elevation": self._estimate_elevation(current_time),
            }
        else:
            return {"status": "completed"}

    def _estimate_elevation(self, current_time: float) -> float:
        """Estimate elevation at a given time during pass."""
        if self.los_time <= self.aos_time:
            return 0.0
        progress = (current_time - self.aos_time) / (self.los_time - self.aos_time)
        # Approximate bell curve for elevation
        import math
        return self.max_elevation * math.sin(progress * math.pi)


class ContactScheduler:
    """Manages contact pass scheduling and display."""

    def __init__(self):
        self._passes: list[ContactPass] = []
        self._current_contact_status: dict = {}

    def update_passes(self, passes: list[dict]) -> None:
        """Update the contact pass list from telemetry or planner API."""
        self._passes = []
        for p in passes:
            try:
                cp = ContactPass(
                    aos_time=p.get("aos_time", _time_mod.time()),
                    los_time=p.get("los_time", _time_mod.time() + 600),
                    max_elevation=p.get("max_elevation", 45.0),
                    ground_station=p.get("ground_station", "Unknown"),
                    data_downlink_capacity=p.get("data_downlink_capacity", 100.0),
                )
                self._passes.append(cp)
            except Exception:
                continue
        self._passes.sort(key=lambda p: p.aos_time)

    def get_next_passes(self, count: int = 10, current_time: Optional[float] = None) -> list[dict]:
        """Get the next N passes as display data."""
        if current_time is None:
            current_time = _time_mod.time()

        result = []
        for i, pass_obj in enumerate(self._passes[:count]):
            result.append({
                "index": i,
                "aos_time": pass_obj.aos_time,
                "los_time": pass_obj.los_time,
                "duration_min": round(pass_obj.duration_min, 1),
                "max_elevation": round(pass_obj.max_elevation, 1),
                "ground_station": pass_obj.ground_station,
                "data_downlink_capacity": round(pass_obj.data_downlink_capacity, 1),
                "status": pass_obj.status_at_time(current_time),
                "elevation_color": self._elevation_to_color(pass_obj.max_elevation),
            })
        return result

    def get_current_contact_status(self, current_time: Optional[float] = None) -> dict:
        """Get current contact status."""
        if current_time is None:
            current_time = _time_mod.time()

        # Find current or next contact
        for pass_obj in self._passes:
            status = pass_obj.status_at_time(current_time)
            if status["status"] == "in_contact":
                return {
                    "in_contact": True,
                    "ground_station": pass_obj.ground_station,
                    "time_to_los": status.get("time_to_los", 0),
                    "current_elevation": round(status.get("elevation", 0), 1),
                }
            elif status["status"] == "upcoming":
                return {
                    "in_contact": False,
                    "ground_station": pass_obj.ground_station,
                    "time_to_aos": status.get("time_to_aos", 0),
                }

        return {"in_contact": False, "ground_station": "None", "time_to_aos": float('inf')}

    @staticmethod
    def _elevation_to_color(elevation: float) -> str:
        """Map elevation to color code."""
        if elevation > 30:
            return "green"
        elif elevation > 10:
            return "yellow"
        elif elevation > 5:
            return "orange"
        else:
            return "red"
