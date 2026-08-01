"""System Overview Dashboard.

Top-level dashboard showing satellite mode, subsystem health, key parameters.
"""
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class HealthStatus(Enum):
    """Subsystem health status.

    ``UNKNOWN`` is distinct from ``GREEN``: "we have not heard from this" is
    not "this is fine", and a console that renders the two identically is
    exactly how a dead telemetry link looks like a healthy spacecraft.
    """
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


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
    """A key system parameter for dashboard display.

    ``value`` is ``None`` when the parameter has not been received. The
    previous version substituted a plausible constant (75 % SoC, -15 C FPA,
    8 dB link margin), so with no telemetry connected at all the dashboard
    rendered a healthy spacecraft — the DI-03 fabricated-data class.
    """
    name: str
    value: Optional[float]
    units: str
    status: HealthStatus
    param_id: str = ""


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

        # Update key parameters from each subsystem. `_pick` returns None when
        # the parameter has not arrived; a missing value is UNKNOWN, never a
        # nominal-looking default.
        def _pick(subsystem: str, *keys):
            sub = state.get(subsystem) or {}
            for key in keys:
                val = sub.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return float(val)
            return None

        specs = (
            ("battery_soc", "Battery SoC", "%", "0x0101", 20, 10,
             _pick("eps", "soc_pct", "bat_soc")),
            ("attitude_error", "Attitude Error", "deg", "0x0217", 1.0, 2.0,
             _pick("aocs", "att_error_deg", "att_error")),
            ("fpa_temp", "FPA Temperature", "°C", "0x0601", -10, -20,
             _pick("payload", "fpa_temp_C", "fpa_temp")),
            ("link_margin", "Link Margin", "dB", "0x0507", 3, 1,
             _pick("ttc", "link_margin_dB", "link_margin")),
            ("storage_percent", "Storage %", "%", "0x0312", 80, 95,
             _pick("obdh", "hktm_buf_fill", "storage_fill")),
        )
        self._key_parameters = {
            key: KeyParameter(
                label, value, units,
                (self._value_to_status(value, yellow, red)
                 if value is not None else HealthStatus.UNKNOWN),
                pid,
            )
            for key, label, units, pid, yellow, red, value in specs
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

    def update_health_from_limits(self, limit_status_by_subsystem: dict) -> None:
        """Colour the subsystem health strip from the GROUND limit check.

        Until Phase G every one of the six subsystems reported GREEN forever:
        nothing ever called `update_subsystem_health`, so the "health" row was
        decoration. It now reflects `mcs/limits.yaml`, which is the only limit
        check 30 of the 51 limited parameters have.
        """
        for subsystem, statuses in (limit_status_by_subsystem or {}).items():
            key = str(subsystem).lower()
            if key not in self._subsystem_health:
                continue
            worst = "nominal"
            offenders: list[str] = []
            for tag, status in (statuses or {}).items():
                if str(status).startswith("red"):
                    worst = "red"
                    offenders.append(tag)
                elif str(status).startswith("yellow") and worst != "red":
                    worst = "yellow"
                    offenders.append(tag)
            status_name = {"red": "RED", "yellow": "YELLOW"}.get(worst, "GREEN")
            desc = (f"{len(offenders)} parameter(s) out of ground limits: "
                    + ", ".join(sorted(offenders)[:4])) if offenders else \
                   self._subsystem_health[key].description
            self.update_subsystem_health(key, status_name, desc)

    def set_alarm_counts(self, total: int, high: int, critical: int = 0) -> None:
        """Feed the real journal counts (nothing ever set these before)."""
        self._active_alarms_count = max(0, int(total) - int(high) - int(critical))
        self._high_alarms = int(high)
        self._critical_alarms = int(critical)

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
                    "value": (round(p.value, 2) if p.value is not None
                              else None),
                    "units": p.units,
                    "status": p.status.value,
                    "param_id": p.param_id,
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
