"""FDIR/Alarm Panel.

Displays active alarms, FDIR status, S12 monitoring, S19 event-action rules.
"""
from dataclasses import dataclass
from typing import Optional
from enum import Enum


class Severity(Enum):
    """Alarm severity levels."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3


@dataclass
class Alarm:
    """Represents a single alarm."""
    alarm_id: int
    timestamp: float
    severity: Severity
    subsystem: str
    parameter: str
    description: str
    value: str
    limit: str
    acknowledged: bool
    source: str  # "S12", "S19", "S6", etc.


@dataclass
class FDIRRule:
    """Represents an active FDIR rule."""
    rule_id: int
    name: str
    service: int
    subtype: int
    enabled: bool
    violation_count: int


class FDIRAlarmPanel:
    """Manages FDIR and alarm displays."""

    def __init__(self):
        self._alarms: list[Alarm] = []
        self._s12_rules: list[FDIRRule] = []
        self._s19_rules: list[FDIRRule] = []
        self._fdir_level = "nominal"  # nominal, equipment, subsystem, system
        self._event_log: list[dict] = []

    def add_alarm(self, alarm_dict: dict) -> None:
        """Add an alarm from telemetry."""
        try:
            severity_val = alarm_dict.get("severity", 3)
            if isinstance(severity_val, int):
                severity = Severity(severity_val)
            else:
                severity = Severity.LOW

            alarm = Alarm(
                alarm_id=alarm_dict.get("id", 0),
                timestamp=alarm_dict.get("timestamp", 0),
                severity=severity,
                subsystem=alarm_dict.get("subsystem", "UNKNOWN"),
                parameter=alarm_dict.get("parameter", ""),
                description=alarm_dict.get("description", ""),
                value=alarm_dict.get("value", ""),
                limit=alarm_dict.get("limit", ""),
                acknowledged=alarm_dict.get("acknowledged", False),
                source=alarm_dict.get("source", ""),
            )
            self._alarms.append(alarm)
            # Keep only last 100 alarms
            self._alarms = self._alarms[-100:]
        except Exception:
            pass

    def acknowledge_alarm(self, alarm_id: int) -> bool:
        """Mark an alarm as acknowledged."""
        for alarm in self._alarms:
            if alarm.alarm_id == alarm_id:
                alarm.acknowledged = True
                return True
        return False

    def get_active_alarms(self) -> list[dict]:
        """Get active (unacknowledged) alarms sorted by severity."""
        unacked = [a for a in self._alarms if not a.acknowledged]
        unacked.sort(key=lambda a: (a.severity.value, -a.timestamp))
        return [
            {
                "id": a.alarm_id,
                "timestamp": a.timestamp,
                "severity": a.severity.name,
                "subsystem": a.subsystem,
                "parameter": a.parameter,
                "description": a.description,
                "value": a.value,
                "limit": a.limit,
                "source": a.source,
            }
            for a in unacked[:20]  # Top 20 active alarms
        ]

    def get_alarm_journal(self) -> list[dict]:
        """Get all alarm events (last 50)."""
        result = []
        for alarm in sorted(
            self._alarms, key=lambda a: (-a.timestamp, a.severity.value)
        )[:50]:
            result.append({
                "id": alarm.alarm_id,
                "timestamp": alarm.timestamp,
                "severity": alarm.severity.name,
                "subsystem": alarm.subsystem,
                "parameter": alarm.parameter,
                "value": alarm.value,
                "acknowledged": alarm.acknowledged,
                "source": alarm.source,
            })
        return result

    def update_s12_rules(self, rules: list[dict]) -> None:
        """Update S12 monitoring rules status."""
        self._s12_rules = []
        for r in rules:
            rule = FDIRRule(
                rule_id=r.get("id", 0),
                name=r.get("name", ""),
                service=12,
                subtype=r.get("subtype", 0),
                enabled=r.get("enabled", True),
                violation_count=r.get("violation_count", 0),
            )
            self._s12_rules.append(rule)

    def update_s19_rules(self, rules: list[dict]) -> None:
        """Update S19 event-action rules status."""
        self._s19_rules = []
        for r in rules:
            rule = FDIRRule(
                rule_id=r.get("id", 0),
                name=r.get("name", ""),
                service=19,
                subtype=r.get("subtype", 0),
                enabled=r.get("enabled", True),
                violation_count=r.get("violation_count", 0),
            )
            self._s19_rules.append(rule)

    def set_fdir_level(self, level: str) -> None:
        """Set current FDIR level: nominal, equipment, subsystem, system."""
        if level in ("nominal", "equipment", "subsystem", "system"):
            self._fdir_level = level

    def get_display_data(self) -> dict:
        """Get complete FDIR/alarm panel display data."""
        active_alarms = self.get_active_alarms()
        alarm_count_by_severity = {
            "CRITICAL": len([a for a in active_alarms if a["severity"] == "CRITICAL"]),
            "HIGH": len([a for a in active_alarms if a["severity"] == "HIGH"]),
            "MEDIUM": len([a for a in active_alarms if a["severity"] == "MEDIUM"]),
            "LOW": len([a for a in active_alarms if a["severity"] == "LOW"]),
        }

        return {
            "active_alarms": active_alarms,
            "alarm_count_by_severity": alarm_count_by_severity,
            "alarm_journal": self.get_alarm_journal(),
            "s12_monitoring": {
                "active_rules": len([r for r in self._s12_rules if r.enabled]),
                "violations": sum(r.violation_count for r in self._s12_rules),
                "rules": [
                    {
                        "id": r.rule_id,
                        "name": r.name,
                        "enabled": r.enabled,
                        "violations": r.violation_count,
                    }
                    for r in self._s12_rules[:10]
                ],
            },
            "s19_event_action": {
                "active_rules": len([r for r in self._s19_rules if r.enabled]),
                "triggered_count": sum(r.violation_count for r in self._s19_rules),
                "rules": [
                    {
                        "id": r.rule_id,
                        "name": r.name,
                        "enabled": r.enabled,
                        "triggered": r.violation_count,
                    }
                    for r in self._s19_rules[:10]
                ],
            },
            "fdir_level": self._fdir_level,
            "fdir_level_color": self._fdir_level_to_color(self._fdir_level),
        }

    @staticmethod
    def _fdir_level_to_color(level: str) -> str:
        """Map FDIR level to color."""
        colors = {
            "nominal": "green",
            "equipment": "yellow",
            "subsystem": "orange",
            "system": "red",
        }
        return colors.get(level, "gray")
