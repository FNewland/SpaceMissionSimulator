"""System Overview Dashboard.

Top-level dashboard showing satellite mode, subsystem health, key parameters.
"""
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class HealthStatus(Enum):
    """Subsystem health status."""
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class SatelliteMode(Enum):
    """Satellite operational mode."""
    NOMINAL = "NOMINAL"
    SAFE = "SAFE"
    CONTINGENCY = "CONTINGENCY"
    COMMISSIONING = "COMMISSIONING"
    DECOMMISSIONED = "DECOMMISSIONED"


@dataclass
class SubsystemHealth:
    """Health status of a single subsystem."""
    name: str
    status: HealthStatus
    description: str


@dataclass
class KeyParameter:
    """A key system parameter for dashboard display."""
    name: str
    value: float
    units: str
    status: HealthStatus


class SystemOverviewDashboard:
    """Manages system-level overview dashboard."""

    def __init__(self):
        self._satellite_mode = SatelliteMode.NOMINAL
        self._subsystem_health: dict[str, SubsystemHealth] = {
            "eps": SubsystemHealth("EPS", HealthStatus.GREEN, "Power System Nominal"),
            "aocs": SubsystemHealth("AOCS", HealthStatus.GREEN, "Attitude Control Nominal"),
            "tcs": SubsystemHealth("TCS", HealthStatus.GREEN, "Thermal Control Nominal"),
            "ttc": SubsystemHealth("TT&C", HealthStatus.GREEN, "Telecom Nominal"),
            "obdh": SubsystemHealth("OBDH", HealthStatus.GREEN, "On-Board Computer Nominal"),
            "payload": SubsystemHealth("Payload", HealthStatus.GREEN, "Payload Nominal"),
        }
        self._key_parameters: dict[str, KeyParameter] = {}
        self._active_contacts = 0
        self._next_contact_countdown_s: Optional[float] = None
        self._active_alarms_count = 0
        self._critical_alarms = 0
        self._high_alarms = 0

    def update_from_telemetry(self, state: dict) -> None:
        """Update overview from telemetry state."""
        # Update satellite mode if present
        if "satellite_mode" in state:
            mode_str = state.get("satellite_mode", "NOMINAL").upper()
            try:
                self._satellite_mode = SatelliteMode(mode_str)
            except ValueError:
                pass

        # Update key parameters from each subsystem
        self._key_parameters = {
            "battery_soc": KeyParameter(
                "Battery SoC",
                float(state.get("eps", {}).get("bat_soc", 75.0)),
                "%",
                self._value_to_status(state.get("eps", {}).get("bat_soc", 75.0), 20, 10),
            ),
            "attitude_error": KeyParameter(
                "Attitude Error",
                float(state.get("aocs", {}).get("att_error", 0.5)),
                "deg",
                self._value_to_status(state.get("aocs", {}).get("att_error", 0.5), 1.0, 2.0),
            ),
            "fpa_temp": KeyParameter(
                "FPA Temperature",
                float(state.get("payload", {}).get("fpa_temp", -15.0)),
                "°C",
                self._value_to_status(state.get("payload", {}).get("fpa_temp", -15.0), -10, -20),
            ),
            "link_margin": KeyParameter(
                "Link Margin",
                float(state.get("ttc", {}).get("link_margin", 8.0)),
                "dB",
                self._value_to_status(state.get("ttc", {}).get("link_margin", 8.0), 3, 1),
            ),
            "storage_percent": KeyParameter(
                "Storage %",
                float(state.get("obdh", {}).get("storage_fill", 50.0)),
                "%",
                self._value_to_status(state.get("obdh", {}).get("storage_fill", 50.0), 80, 95),
            ),
        }

        # Update contact info
        self._active_contacts = state.get("in_contact", 0)
        self._next_contact_countdown_s = state.get("time_to_aos")

        # Update alarm counts (if provided)
        if "alarm_counts" in state:
            counts = state["alarm_counts"]
            self._active_alarms_count = counts.get("total", 0)
            self._critical_alarms = counts.get("critical", 0)
            self._high_alarms = counts.get("high", 0)

    def update_subsystem_health(self, subsystem: str, status: str, description: str = "") -> None:
        """Update health status of a subsystem."""
        try:
            health_status = HealthStatus[status.upper()]
            if subsystem in self._subsystem_health:
                self._subsystem_health[subsystem] = SubsystemHealth(
                    name=self._subsystem_health[subsystem].name,
                    status=health_status,
                    description=description or self._subsystem_health[subsystem].description,
                )
        except (KeyError, ValueError):
            pass

    def get_display_data(self) -> dict:
        """Get system overview dashboard display data."""
        total_alarms = (
            self._critical_alarms + self._high_alarms + self._active_alarms_count
        )

        return {
            "satellite_mode": self._satellite_mode.value,
            "satellite_mode_color": self._mode_to_color(self._satellite_mode),
            "subsystem_health": [
                {
                    "name": h.name,
                    "status": h.status.value,
                    "description": h.description,
                }
                for h in self._subsystem_health.values()
            ],
            "healthy_subsystems": sum(
                1 for h in self._subsystem_health.values()
                if h.status == HealthStatus.GREEN
            ),
            "warning_subsystems": sum(
                1 for h in self._subsystem_health.values()
                if h.status == HealthStatus.YELLOW
            ),
            "alarm_subsystems": sum(
                1 for h in self._subsystem_health.values()
                if h.status == HealthStatus.RED
            ),
            "key_parameters": [
                {
                    "name": p.name,
                    "value": round(p.value, 2),
                    "units": p.units,
                    "status": p.status.value,
                }
                for p in self._key_parameters.values()
            ],
            "active_contacts": self._active_contacts,
            "next_contact_countdown_s": self._next_contact_countdown_s,
            "next_contact_countdown_min": (
                round(self._next_contact_countdown_s / 60.0, 1)
                if self._next_contact_countdown_s
                else None
            ),
            "active_alarms": {
                "total": total_alarms,
                "critical": self._critical_alarms,
                "high": self._high_alarms,
                "medium_low": self._active_alarms_count,
            },
        }

    @staticmethod
    def _value_to_status(value: float, yellow_threshold: float, red_threshold: float) -> HealthStatus:
        """Map a value to health status based on thresholds."""
        if abs(value) >= abs(red_threshold):
            return HealthStatus.RED
        elif abs(value) >= abs(yellow_threshold):
            return HealthStatus.YELLOW
        else:
            return HealthStatus.GREEN

    @staticmethod
    def _mode_to_color(mode: SatelliteMode) -> str:
        """Map satellite mode to color."""
        colors = {
            SatelliteMode.NOMINAL: "green",
            SatelliteMode.SAFE: "yellow",
            SatelliteMode.CONTINGENCY: "orange",
            SatelliteMode.COMMISSIONING: "blue",
            SatelliteMode.DECOMMISSIONED: "gray",
        }
        return colors.get(mode, "gray")
