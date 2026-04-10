"""SMO Planner — Subsystem Constraint Checkers.

Enforces power, AOCS, thermal, and data volume constraints during
activity scheduling to maintain mission feasibility.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from dataclasses import dataclass

from smo_planner.utils import parse_iso as _parse_iso


@dataclass
class ConstraintViolation:
    """Represents a single constraint violation."""
    checker_name: str
    severity: str  # "error", "warning", "info"
    activity_id: int | None
    activity_name: str | None
    message: str
    suggested_fix: str | None = None

    def to_dict(self) -> dict:
        return {
            "checker": self.checker_name,
            "severity": self.severity,
            "activity_id": self.activity_id,
            "activity_name": self.activity_name,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
        }


class PowerConstraintChecker:
    """Validates power budget constraints throughout the mission plan."""

    # Power consumption per activity type (in Watts)
    DEFAULT_POWER_CONSUMPTION = {
        "imaging_pass": 75.0,  # 60W payload + 15W AOCS fine point
        "data_dump": 50.0,  # 40W TTC high power + 10W OBDH
        "calibration": 50.0,  # Payload calibration
        "momentum_desaturation": 30.0,  # Magnetorquers
        "housekeeping_collection": 5.0,  # Minimal
        "software_upload": 20.0,  # TTC reception
    }

    def __init__(
        self,
        battery_capacity_wh: float = 1120.0,
        min_soc_percent: float = 20.0,
        bus_capacity_w: float = 300.0,
        solar_array_max_w: float = 280.0,
    ):
        """Initialize power constraint checker.

        Args:
            battery_capacity_wh: Battery capacity in Watt-hours
            min_soc_percent: Minimum acceptable state of charge
            bus_capacity_w: Maximum bus current capacity in Watts
            solar_array_max_w: Maximum solar array output in Watts
        """
        self.battery_capacity_wh = battery_capacity_wh
        self.min_soc_percent = min_soc_percent
        self.bus_capacity_w = bus_capacity_w
        self.solar_array_max_w = solar_array_max_w

    def check_activity_power(
        self,
        activity: dict,
        ground_track: list[dict] | None = None,
    ) -> list[ConstraintViolation]:
        """Check power constraints for a single activity.

        Args:
            activity: Activity dict with name, start_time, duration_s, power_w
            ground_track: Optional ground track for eclipse detection

        Returns:
            List of constraint violations (empty if all OK)
        """
        violations: list[ConstraintViolation] = []
        power_w = activity.get("power_w", 0)
        name = activity.get("name", "unknown")
        activity_id = activity.get("id")

        # Check against bus capacity
        if power_w > self.bus_capacity_w:
            violations.append(ConstraintViolation(
                checker_name="PowerConstraintChecker",
                severity="error",
                activity_id=activity_id,
                activity_name=name,
                message=(
                    f"Activity power consumption ({power_w:.1f}W) "
                    f"exceeds bus capacity ({self.bus_capacity_w:.1f}W)"
                ),
                suggested_fix="Reduce concurrent high-power activities or increase bus capacity",
            ))

        return violations

    def check_plan_power(
        self,
        schedule: list[dict],
        initial_soc: float = 80.0,
        ground_track: list[dict] | None = None,
    ) -> dict:
        """Check power constraints across the entire mission plan.

        Args:
            schedule: List of activities
            initial_soc: Initial battery state of charge (%)
            ground_track: Ground track for eclipse detection

        Returns:
            Dict with violations, soc_timeline, and warnings
        """
        violations: list[ConstraintViolation] = []
        soc_wh = (self.battery_capacity_wh * initial_soc) / 100.0
        soc_timeline: list[dict] = []

        # Sort activities by start time
        sorted_schedule = sorted(
            schedule, key=lambda a: a.get("start_time", "")
        )

        for activity in sorted_schedule:
            state_name = activity.get("state_name", "")
            if state_name in ("CANCELLED", "FAILED"):
                continue

            start_str = activity.get("start_time", "")
            duration_s = activity.get("duration_s", 0)
            power_w = activity.get("power_w", 0)

            if not start_str or duration_s <= 0 or power_w <= 0:
                continue

            # Estimate eclipse fraction
            start = _parse_iso(start_str)
            if start is None:
                continue

            end = start + timedelta(seconds=duration_s)
            eclipse_fraction = self._estimate_eclipse_fraction(
                start, end, ground_track
            )

            # Energy consumption and generation
            hours = duration_s / 3600.0
            eclipse_hours = hours * eclipse_fraction
            sunlight_hours = hours * (1.0 - eclipse_fraction)

            # Base system drain (approximate)
            drain_wh = 95.0 * eclipse_hours + 95.0 * sunlight_hours

            # Activity power
            activity_drain_wh = power_w * hours

            # Solar charge (only in sunlight)
            charge_wh = (self.solar_array_max_w * 0.85) * sunlight_hours

            # Net energy
            net_wh = soc_wh + charge_wh - drain_wh - activity_drain_wh
            soc_wh = max(0.0, min(self.battery_capacity_wh, net_wh))

            soc_percent = (soc_wh / self.battery_capacity_wh) * 100.0
            soc_timeline.append({
                "activity_id": activity.get("id"),
                "activity_name": activity.get("name"),
                "start_time": start_str,
                "soc_before": round((soc_wh + drain_wh + activity_drain_wh - charge_wh) / self.battery_capacity_wh * 100.0, 1),
                "soc_after": round(soc_percent, 1),
            })

            # Check if SoC drops below minimum
            if soc_percent < self.min_soc_percent:
                violations.append(ConstraintViolation(
                    checker_name="PowerConstraintChecker",
                    severity="error",
                    activity_id=activity.get("id"),
                    activity_name=activity.get("name"),
                    message=(
                        f"Activity would result in SoC of {soc_percent:.1f}% "
                        f"(below minimum {self.min_soc_percent}%)"
                    ),
                    suggested_fix=(
                        f"Delay activity or reduce power consumption; "
                        f"need {soc_percent - self.min_soc_percent:.1f}% more charge"
                    ),
                ))

        return {
            "violations": violations,
            "soc_timeline": soc_timeline,
            "final_soc": round((soc_wh / self.battery_capacity_wh) * 100.0, 1),
        }

    @staticmethod
    def _estimate_eclipse_fraction(
        start: datetime,
        end: datetime,
        ground_track: list[dict] | None = None,
    ) -> float:
        """Estimate fraction of time in eclipse."""
        if not ground_track:
            return 0.35  # Default for 450km SSO

        eclipse_count = 0
        total_count = 0
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        for point in ground_track:
            utc = point.get("utc", "")
            if start_iso <= utc <= end_iso:
                total_count += 1
                if point.get("in_eclipse", False):
                    eclipse_count += 1

        if total_count == 0:
            return 0.35
        return eclipse_count / total_count


class AOCSConstraintChecker:
    """Validates attitude and control system constraints."""

    def __init__(
        self,
        max_slew_rate_deg_per_s: float = 1.0,
        min_settling_time_s: float = 30.0,
        momentum_capacity_h_km2_per_s: float = 100.0,
    ):
        """Initialize AOCS constraint checker.

        Args:
            max_slew_rate_deg_per_s: Maximum slew rate
            min_settling_time_s: Minimum settling time after slew
            momentum_capacity_h_km2_per_s: Reaction wheel momentum capacity
        """
        self.max_slew_rate_deg_per_s = max_slew_rate_deg_per_s
        self.min_settling_time_s = min_settling_time_s
        self.momentum_capacity = momentum_capacity_h_km2_per_s

    def check_slew_constraints(
        self,
        activities: list[dict],
    ) -> list[ConstraintViolation]:
        """Check slew time and settling constraints between activities.

        Args:
            activities: List of imaging or pointing activities

        Returns:
            List of constraint violations
        """
        violations: list[ConstraintViolation] = []
        sorted_acts = sorted(
            activities, key=lambda a: a.get("start_time", "")
        )

        for i in range(len(sorted_acts) - 1):
            curr = sorted_acts[i]
            next_act = sorted_acts[i + 1]

            # Check if both have target location info
            curr_lat = curr.get("capture_lat")
            curr_lon = curr.get("capture_lon")
            next_lat = next_act.get("capture_lat")
            next_lon = next_act.get("capture_lon")

            if not all([curr_lat, curr_lon, next_lat, next_lon]):
                continue

            # Calculate angular separation
            angular_sep = self._angular_distance(
                curr_lat, curr_lon, next_lat, next_lon
            )

            # Required slew time
            slew_time_s = angular_sep / self.max_slew_rate_deg_per_s

            # Required gap (slew + settling)
            required_gap_s = slew_time_s + self.min_settling_time_s

            # Check actual gap
            curr_end_str = curr.get("start_time", "")
            next_start_str = next_act.get("start_time", "")

            if curr_end_str and next_start_str:
                curr_end = _parse_iso(curr_end_str)
                next_start = _parse_iso(next_start_str)
                curr_duration = curr.get("duration_s", 0)

                if curr_end and next_start:
                    curr_end += timedelta(seconds=curr_duration)
                    actual_gap = (next_start - curr_end).total_seconds()

                    if actual_gap < required_gap_s:
                        violations.append(ConstraintViolation(
                            checker_name="AOCSConstraintChecker",
                            severity="error",
                            activity_id=next_act.get("id"),
                            activity_name=next_act.get("name"),
                            message=(
                                f"Insufficient slew time between activities: "
                                f"need {required_gap_s:.0f}s "
                                f"({slew_time_s:.0f}s slew + {self.min_settling_time_s}s settle), "
                                f"have {actual_gap:.0f}s"
                            ),
                            suggested_fix=(
                                f"Delay next activity by "
                                f"{required_gap_s - actual_gap:.0f}s"
                            ),
                        ))

        return violations

    def check_momentum_budget(
        self,
        activities: list[dict],
        momentum_per_imaging_pass: float = 0.15,
    ) -> list[ConstraintViolation]:
        """Check momentum accumulation and insertion of desaturation.

        Args:
            activities: List of activities
            momentum_per_imaging_pass: Momentum added per imaging pass (h*km^2/s)

        Returns:
            List of constraint violations and desaturation recommendations
        """
        violations: list[ConstraintViolation] = []
        accumulated_momentum = 0.0

        sorted_acts = sorted(
            activities, key=lambda a: a.get("start_time", "")
        )

        for activity in sorted_acts:
            name = activity.get("name", "")

            # Accumulate momentum from imaging
            if "imaging" in name.lower():
                accumulated_momentum += momentum_per_imaging_pass

                # Check if momentum exceeds 80% of capacity
                if accumulated_momentum > (0.8 * self.momentum_capacity):
                    violations.append(ConstraintViolation(
                        checker_name="AOCSConstraintChecker",
                        severity="warning",
                        activity_id=activity.get("id"),
                        activity_name=name,
                        message=(
                            f"Momentum accumulation ({accumulated_momentum:.2f} h*km^2/s) "
                            f"exceeds 80% of capacity ({0.8 * self.momentum_capacity:.2f})"
                        ),
                        suggested_fix=(
                            "Schedule momentum_desaturation activity "
                            "after this pass or before next imaging"
                        ),
                    ))

            # Reset momentum on desaturation
            elif "desaturate" in name.lower() or "desat" in name.lower():
                accumulated_momentum = 0.0

        return violations

    @staticmethod
    def _angular_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate angular distance between two points (degrees)."""
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        # Simple Euclidean approximation (valid for small distances)
        # In degrees: approximately sqrt(dlat^2 + (dlon * cos(lat))^2)
        lat_rad = math.radians((lat1 + lat2) / 2.0)
        dlon_scaled = dlon * math.cos(lat_rad)

        return math.sqrt(dlat**2 + dlon_scaled**2)


class ThermalConstraintChecker:
    """Validates thermal constraints and duty cycles."""

    def __init__(
        self,
        max_imaging_minutes_per_orbit: float = 30.0,
        orbit_period_minutes: float = 91.0,
        fpa_cooldown_period_s: float = 300.0,
    ):
        """Initialize thermal constraint checker.

        Args:
            max_imaging_minutes_per_orbit: Max payload imaging time per orbit
            orbit_period_minutes: Orbital period
            fpa_cooldown_period_s: Required cooldown after imaging
        """
        self.max_imaging_minutes_per_orbit = max_imaging_minutes_per_orbit
        self.orbit_period_minutes = orbit_period_minutes
        self.fpa_cooldown_period_s = fpa_cooldown_period_s

    def check_payload_duty_cycle(
        self,
        activities: list[dict],
    ) -> list[ConstraintViolation]:
        """Check that imaging doesn't exceed duty cycle limits.

        Args:
            activities: List of activities

        Returns:
            List of constraint violations
        """
        violations: list[ConstraintViolation] = []

        # Group imaging activities by orbit
        orbits: dict[int, list[dict]] = {}
        orbit_period_s = self.orbit_period_minutes * 60.0

        for activity in activities:
            name = activity.get("name", "")
            if "imaging" not in name.lower():
                continue

            start_str = activity.get("start_time", "")
            if not start_str:
                continue

            start = _parse_iso(start_str)
            if start is None:
                continue

            # Assign to orbit number (approximate)
            orbit_num = int(start.timestamp() / orbit_period_s)

            if orbit_num not in orbits:
                orbits[orbit_num] = []
            orbits[orbit_num].append(activity)

        # Check duty cycle per orbit
        for orbit_num, orbit_activities in orbits.items():
            total_imaging_s = 0.0
            for activity in orbit_activities:
                total_imaging_s += activity.get("duration_s", 0)

            total_imaging_min = total_imaging_s / 60.0

            if total_imaging_min > self.max_imaging_minutes_per_orbit:
                violations.append(ConstraintViolation(
                    checker_name="ThermalConstraintChecker",
                    severity="warning",
                    activity_id=orbit_activities[0].get("id"),
                    activity_name="imaging_pass (multiple)",
                    message=(
                        f"Imaging duty cycle in orbit {orbit_num} "
                        f"({total_imaging_min:.1f} min) exceeds limit "
                        f"({self.max_imaging_minutes_per_orbit:.1f} min)"
                    ),
                    suggested_fix="Spread imaging activities across multiple orbits",
                ))

        return violations

    def check_cooldown_periods(
        self,
        activities: list[dict],
    ) -> list[ConstraintViolation]:
        """Check that imaging activities have proper cooldown periods.

        Args:
            activities: List of activities

        Returns:
            List of constraint violations
        """
        violations: list[ConstraintViolation] = []
        sorted_acts = sorted(
            activities, key=lambda a: a.get("start_time", "")
        )

        for i, activity in enumerate(sorted_acts):
            name = activity.get("name", "")
            if "imaging" not in name.lower():
                continue

            # Find next activity
            if i + 1 < len(sorted_acts):
                next_activity = sorted_acts[i + 1]
                next_name = next_activity.get("name", "")

                # Check if next activity is imaging (needs cooldown)
                if "imaging" in next_name.lower():
                    curr_end_str = activity.get("start_time", "")
                    next_start_str = next_activity.get("start_time", "")

                    if curr_end_str and next_start_str:
                        curr_end = _parse_iso(curr_end_str)
                        next_start = _parse_iso(next_start_str)
                        curr_duration = activity.get("duration_s", 0)

                        if curr_end and next_start:
                            curr_end += timedelta(seconds=curr_duration)
                            gap_s = (next_start - curr_end).total_seconds()

                            if gap_s < self.fpa_cooldown_period_s:
                                violations.append(ConstraintViolation(
                                    checker_name="ThermalConstraintChecker",
                                    severity="warning",
                                    activity_id=next_activity.get("id"),
                                    activity_name=next_name,
                                    message=(
                                        f"Insufficient cooldown between imaging activities: "
                                        f"need {self.fpa_cooldown_period_s:.0f}s, "
                                        f"have {gap_s:.0f}s"
                                    ),
                                    suggested_fix=(
                                        f"Delay next imaging by "
                                        f"{self.fpa_cooldown_period_s - gap_s:.0f}s"
                                    ),
                                ))

        return violations


class DataVolumeConstraintChecker:
    """Validates data volume and storage constraints."""

    def __init__(
        self,
        storage_capacity_mb: float = 20000.0,
        storage_margin_percent: float = 10.0,
        downlink_rate_mbps: float = 0.5,
    ):
        """Initialize data volume constraint checker.

        Args:
            storage_capacity_mb: Total onboard storage capacity
            storage_margin_percent: Minimum margin to maintain
            downlink_rate_mbps: Effective downlink rate (accounting for overhead)
        """
        self.storage_capacity_mb = storage_capacity_mb
        self.storage_margin_percent = storage_margin_percent
        self.downlink_rate_mbps = downlink_rate_mbps
        self.min_margin_mb = (
            storage_capacity_mb * storage_margin_percent / 100.0
        )

    def check_storage_capacity(
        self,
        activities: list[dict],
        current_onboard_mb: float = 0.0,
    ) -> list[ConstraintViolation]:
        """Check that scheduled activities don't exceed storage capacity.

        Args:
            activities: List of activities
            current_onboard_mb: Current onboard data volume

        Returns:
            List of constraint violations
        """
        violations: list[ConstraintViolation] = []
        onboard_mb = current_onboard_mb

        sorted_acts = sorted(
            activities, key=lambda a: a.get("start_time", "")
        )

        for activity in sorted_acts:
            state_name = activity.get("state_name", "")
            if state_name in ("CANCELLED", "FAILED"):
                continue

            name = activity.get("name", "")
            data_gen = activity.get("data_volume_mb", 0)

            # Add generated data
            onboard_mb += data_gen

            # Check capacity
            if onboard_mb > self.storage_capacity_mb:
                violations.append(ConstraintViolation(
                    checker_name="DataVolumeConstraintChecker",
                    severity="error",
                    activity_id=activity.get("id"),
                    activity_name=name,
                    message=(
                        f"Data generation ({data_gen:.1f} MB) would result in "
                        f"total onboard storage of {onboard_mb:.1f} MB, "
                        f"exceeding capacity ({self.storage_capacity_mb:.1f} MB)"
                    ),
                    suggested_fix=(
                        f"Schedule data_dump before this activity to free "
                        f"{onboard_mb - self.storage_capacity_mb:.1f} MB"
                    ),
                ))

            # Check margin
            available_mb = self.storage_capacity_mb - onboard_mb
            if available_mb < self.min_margin_mb and data_gen > 0:
                violations.append(ConstraintViolation(
                    checker_name="DataVolumeConstraintChecker",
                    severity="warning",
                    activity_id=activity.get("id"),
                    activity_name=name,
                    message=(
                        f"Storage utilization is {(onboard_mb / self.storage_capacity_mb * 100):.1f}%, "
                        f"below desired margin of {self.storage_margin_percent}%"
                    ),
                    suggested_fix="Schedule a data dump to reduce onboard data volume",
                ))

            # Subtract downlink during dump activities
            if "dump" in name.lower() or "downlink" in name.lower():
                duration_s = activity.get("duration_s", 0)
                downlink_mb = self.downlink_rate_mbps * (duration_s / 60.0)
                onboard_mb = max(0.0, onboard_mb - downlink_mb)

        return violations


class ConflictResolutionChecker:
    """Validates and resolves resource conflicts between activities."""

    # Priority levels (higher = more important)
    ACTIVITY_PRIORITIES = {
        "imaging_pass": 100,
        "data_dump": 80,
        "software_upload": 85,
        "calibration": 40,
        "momentum_desaturation": 50,
        "housekeeping_collection": 10,
    }

    def __init__(self):
        """Initialize conflict resolution checker."""
        pass

    def check_resource_conflicts(
        self,
        activities: list[dict],
    ) -> list[ConstraintViolation]:
        """Check for exclusive resource conflicts (imaging vs dump, etc).

        Args:
            activities: List of activities

        Returns:
            List of constraint violations
        """
        violations: list[ConstraintViolation] = []

        # Find overlapping activities with conflicting resource requirements
        for i, act1 in enumerate(activities):
            state1 = act1.get("state_name", "")
            if state1 in ("CANCELLED", "FAILED"):
                continue

            name1 = act1.get("name", "")
            start1_str = act1.get("start_time", "")
            duration1 = act1.get("duration_s", 0)

            if not start1_str:
                continue

            start1 = _parse_iso(start1_str)
            if start1 is None:
                continue

            end1 = start1 + timedelta(seconds=duration1)

            for act2 in activities[i + 1:]:
                state2 = act2.get("state_name", "")
                if state2 in ("CANCELLED", "FAILED"):
                    continue

                name2 = act2.get("name", "")
                start2_str = act2.get("start_time", "")
                duration2 = act2.get("duration_s", 0)

                if not start2_str:
                    continue

                start2 = _parse_iso(start2_str)
                if start2 is None:
                    continue

                end2 = start2 + timedelta(seconds=duration2)

                # Check for time overlap
                if start1 < end2 and end1 > start2:
                    # Check for resource conflicts
                    if self._have_exclusive_resources(name1, name2):
                        # Priority-based resolution suggestion
                        priority1 = self.ACTIVITY_PRIORITIES.get(
                            name1, 50
                        )
                        priority2 = self.ACTIVITY_PRIORITIES.get(
                            name2, 50
                        )

                        if priority1 >= priority2:
                            loser = act2
                            winner = act1
                        else:
                            loser = act1
                            winner = act2

                        violations.append(ConstraintViolation(
                            checker_name="ConflictResolutionChecker",
                            severity="error",
                            activity_id=loser.get("id"),
                            activity_name=loser.get("name"),
                            message=(
                                f"Time overlap with '{winner.get('name')}' "
                                f"({start1.isoformat()} to {end1.isoformat()}); "
                                f"both require exclusive antenna/transmitter"
                            ),
                            suggested_fix=(
                                f"Delay '{loser.get('name')}' by at least "
                                f"{(end2 - start2).total_seconds():.0f}s"
                            ),
                        ))

        return violations

    @staticmethod
    def _have_exclusive_resources(name1: str, name2: str) -> bool:
        """Check if two activities require exclusive resources."""
        # Define exclusive resource groups
        antenna_users = {
            "data_dump", "downlink", "software_upload",
            "housekeeping_collection"
        }
        high_power_users = {"imaging_pass", "data_dump"}

        uses_antenna1 = any(x in name1.lower() for x in antenna_users)
        uses_antenna2 = any(x in name2.lower() for x in antenna_users)

        # Two antenna users conflict
        if uses_antenna1 and uses_antenna2:
            return True

        # Imaging during ground contact (antenna needed)
        if "imaging" in name1.lower() and uses_antenna2:
            return True
        if "imaging" in name2.lower() and uses_antenna1:
            return True

        # Multiple high-power consumers
        high_power1 = any(x in name1.lower() for x in high_power_users)
        high_power2 = any(x in name2.lower() for x in high_power_users)
        if high_power1 and high_power2:
            # Imaging + anything else is usually OK (they have power budgets)
            # But data dump + imaging might exceed power
            if ("imaging" in name1.lower() and "dump" in name2.lower()) or \
               ("dump" in name1.lower() and "imaging" in name2.lower()):
                return False  # They can overlap with power management

        return False


class ValidationResult:
    """Result from comprehensive plan validation."""

    def __init__(self):
        self.violations: list[ConstraintViolation] = []
        self.checker_results: dict[str, Any] = {}
        self.is_valid: bool = True

    def add_violations(self, violations: list[ConstraintViolation]) -> None:
        """Add violations from a checker."""
        self.violations.extend(violations)
        if any(v.severity == "error" for v in violations):
            self.is_valid = False

    def to_dict(self) -> dict:
        """Serialize to dict for JSON response."""
        return {
            "valid": self.is_valid,
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
            "error_count": len([v for v in self.violations if v.severity == "error"]),
            "warning_count": len([v for v in self.violations if v.severity == "warning"]),
            "checker_results": self.checker_results,
        }


def validate_plan(
    activities: list[dict],
    contacts: list[dict] | None = None,
    ground_track: list[dict] | None = None,
    battery_soc_percent: float = 80.0,
) -> ValidationResult:
    """Comprehensive plan validation across all constraints.

    Args:
        activities: List of scheduled activities
        contacts: Optional contact windows
        ground_track: Optional ground track for eclipse detection
        battery_soc_percent: Current battery state of charge

    Returns:
        ValidationResult with all violations and status
    """
    result = ValidationResult()

    # Power constraints
    power_checker = PowerConstraintChecker()
    power_result = power_checker.check_plan_power(
        activities, battery_soc_percent, ground_track
    )
    result.add_violations(power_result["violations"])
    result.checker_results["power"] = {
        "final_soc": power_result["final_soc"],
        "soc_timeline": power_result["soc_timeline"],
    }

    # AOCS constraints
    aocs_checker = AOCSConstraintChecker()
    aocs_slew_violations = aocs_checker.check_slew_constraints(activities)
    aocs_momentum_violations = aocs_checker.check_momentum_budget(activities)
    result.add_violations(aocs_slew_violations)
    result.add_violations(aocs_momentum_violations)

    # Thermal constraints
    thermal_checker = ThermalConstraintChecker()
    thermal_duty_violations = thermal_checker.check_payload_duty_cycle(
        activities
    )
    thermal_cooldown_violations = thermal_checker.check_cooldown_periods(
        activities
    )
    result.add_violations(thermal_duty_violations)
    result.add_violations(thermal_cooldown_violations)

    # Data volume constraints
    data_checker = DataVolumeConstraintChecker()
    data_violations = data_checker.check_storage_capacity(activities)
    result.add_violations(data_violations)

    # Conflict resolution
    conflict_checker = ConflictResolutionChecker()
    conflict_violations = conflict_checker.check_resource_conflicts(activities)
    result.add_violations(conflict_violations)

    return result
