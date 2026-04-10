"""SMO Planner — Enhanced Activity Scheduler.

Supports activity states, conflict detection, procedure references,
command sequence generation, and pass-based scheduling validation.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any

from smo_planner.utils import parse_iso as _parse_iso
from smo_planner.constraint_checkers import (
    validate_plan, ValidationResult,
    PowerConstraintChecker, AOCSConstraintChecker,
    ThermalConstraintChecker, DataVolumeConstraintChecker,
    ConflictResolutionChecker,
)


class ActivityState(IntEnum):
    PLANNED = 0
    VALIDATED = 1
    UPLOADED = 2
    EXECUTING = 3
    COMPLETED = 4
    FAILED = 5
    CANCELLED = 6


class ActivityScheduler:
    """Enhanced scheduler with conflict detection and procedure linking."""

    def __init__(self, activity_types: list[dict] | None = None):
        self._types = {a["name"]: a for a in (activity_types or [])}
        self._schedule: list[dict] = []
        self._next_id = 1

    def add_activity(self, name: str, start_time: str, **kwargs) -> dict:
        """Add an activity to the schedule."""
        atype = self._types.get(name, {})
        activity = {
            "id": self._next_id,
            "name": name,
            "start_time": start_time,
            "duration_s": atype.get("duration_s", 300),
            "power_w": atype.get("power_w", 0),
            "data_volume_mb": atype.get("data_volume_mb", 0),
            "priority": atype.get("priority", "medium"),
            "procedure_ref": atype.get("procedure_ref"),
            "command_sequence": atype.get("command_sequence", []),
            "conflicts_with": atype.get("conflicts_with", []),
            "pre_conditions": atype.get("pre_conditions", []),
            "state": int(ActivityState.PLANNED),
            "state_name": "PLANNED",
            **kwargs,
        }
        self._next_id += 1

        # Check name-based conflicts and time overlaps before adding
        warnings_list: list[str] = []
        conflicts = self.check_conflicts(activity)
        warnings_list.extend(conflicts)

        # Also check time overlaps at insertion time
        overlaps = self.check_time_overlap(activity)
        warnings_list.extend(overlaps)

        if warnings_list:
            activity["warnings"] = warnings_list

        self._schedule.append(activity)
        self._schedule.sort(key=lambda a: a["start_time"])
        return activity

    def get_schedule(self) -> list[dict]:
        return list(self._schedule)

    def get_activity(self, activity_id: int) -> dict | None:
        for a in self._schedule:
            if a["id"] == activity_id:
                return a
        return None

    def update_state(self, activity_id: int, state: ActivityState) -> bool:
        for a in self._schedule:
            if a["id"] == activity_id:
                a["state"] = int(state)
                a["state_name"] = state.name
                return True
        return False

    def delete_activity(self, activity_id: int) -> bool:
        for i, a in enumerate(self._schedule):
            if a["id"] == activity_id:
                self._schedule.pop(i)
                return True
        return False

    def check_conflicts(self, activity: dict) -> list[str]:
        """Check for scheduling conflicts with existing activities."""
        conflicts = []
        name = activity.get("name", "")
        start = activity.get("start_time", "")
        duration = activity.get("duration_s", 300)
        conflicts_with = activity.get("conflicts_with", [])

        for existing in self._schedule:
            if existing.get("state") in (
                int(ActivityState.CANCELLED),
                int(ActivityState.FAILED),
            ):
                continue

            # Check time overlap
            ex_start = existing.get("start_time", "")
            ex_duration = existing.get("duration_s", 300)

            # Simple string comparison (ISO format is sortable)
            if start and ex_start:
                # Check name conflict
                ex_name = existing.get("name", "")
                if ex_name in conflicts_with or name in existing.get(
                    "conflicts_with", []
                ):
                    conflicts.append(
                        f"Conflicts with {ex_name} (id={existing['id']})"
                    )

        return conflicts

    def check_time_overlap(self, activity: dict) -> list[str]:
        """Check for time-based overlap conflicts with existing activities."""
        overlaps = []
        start_str = activity.get("start_time", "")
        duration = activity.get("duration_s", 300)

        if not start_str:
            return overlaps

        start = _parse_iso(start_str)
        if start is None:
            return overlaps
        end = start + timedelta(seconds=duration)

        for existing in self._schedule:
            if existing.get("state") in (
                int(ActivityState.CANCELLED),
                int(ActivityState.FAILED),
            ):
                continue
            if existing.get("id") == activity.get("id"):
                continue

            ex_start_str = existing.get("start_time", "")
            ex_duration = existing.get("duration_s", 300)
            if not ex_start_str:
                continue

            ex_start = _parse_iso(ex_start_str)
            if ex_start is None:
                continue
            ex_end = ex_start + timedelta(seconds=ex_duration)

            # Overlap if one starts before the other ends
            if start < ex_end and end > ex_start:
                overlaps.append(
                    f"Time overlap with '{existing['name']}' "
                    f"(id={existing['id']}, "
                    f"{ex_start_str} +{ex_duration}s)"
                )

        return overlaps

    # ── Pass-based scheduling ───────────────────────────────────────

    def schedule_pass_activity(
        self,
        pass_id: int,
        offset_min: float,
        activity_name: str,
        contacts: list[dict],
        **kwargs,
    ) -> dict:
        """Schedule an activity relative to a contact pass.

        Args:
            pass_id: 1-based pass index within the contacts list
            offset_min: minutes after AOS to schedule the activity
            activity_name: name of the activity type
            contacts: list of contact window dicts (with aos, los, duration_s)
            **kwargs: additional activity fields

        Returns:
            The created activity dict

        Raises:
            ValueError: if pass_id is invalid, offset is negative,
                        or activity extends past LOS
        """
        if not contacts:
            raise ValueError("No contact windows provided")
        if pass_id < 1 or pass_id > len(contacts):
            raise ValueError(
                f"Invalid pass_id {pass_id}: must be 1-{len(contacts)}"
            )
        if offset_min < 0:
            raise ValueError(
                f"offset_min must be >= 0, got {offset_min}"
            )

        contact = contacts[pass_id - 1]
        aos_str = contact.get("aos", "")
        los_str = contact.get("los", "")
        gs_name = contact.get("gs_name", "unknown")
        pass_duration_s = contact.get("duration_s", 0)

        aos = _parse_iso(aos_str)
        los = _parse_iso(los_str)
        if aos is None or los is None:
            raise ValueError(
                f"Pass {pass_id} has invalid AOS/LOS timestamps"
            )

        # Compute activity start from AOS + offset
        activity_start = aos + timedelta(minutes=offset_min)

        # Get activity duration from type or kwargs
        atype = self._types.get(activity_name, {})
        duration_s = kwargs.get("duration_s", atype.get("duration_s", 300))

        # Validate activity fits within the pass
        activity_end = activity_start + timedelta(seconds=duration_s)
        if activity_end > los:
            raise ValueError(
                f"Activity '{activity_name}' ends at "
                f"{activity_end.isoformat()} which is past LOS at "
                f"{los.isoformat()} for pass {pass_id} at {gs_name}"
            )

        if activity_start < aos:
            raise ValueError(
                f"Activity start {activity_start.isoformat()} is before "
                f"AOS at {aos.isoformat()} for pass {pass_id}"
            )

        # Add the activity with pass metadata
        activity = self.add_activity(
            activity_name,
            activity_start.isoformat(),
            pass_id=pass_id,
            pass_gs=gs_name,
            pass_aos=aos_str,
            pass_los=los_str,
            offset_min=offset_min,
            **kwargs,
        )
        return activity

    def validate_pass_plan(
        self,
        contacts: list[dict],
        power_budget: dict | None = None,
        data_budget: dict | None = None,
        telemetry: dict[str, float] | None = None,
    ) -> list[dict]:
        """Validate the full schedule against pass constraints.

        Checks:
          - Time overlap between activities
          - Activities assigned to passes fit within pass boundaries
          - Name-based conflict detection
          - Power constraints (if power_budget provided)
          - Data volume constraints (if data_budget provided)

        Args:
            contacts: contact windows list
            power_budget: optional power budget result from BudgetTracker
            data_budget: optional data budget result from BudgetTracker

        Returns:
            list of issue dicts with activity_id, type, and message
        """
        issues = []

        for activity in self._schedule:
            if activity.get("state") in (
                int(ActivityState.CANCELLED),
                int(ActivityState.FAILED),
            ):
                continue

            # Name-based conflicts
            name_conflicts = self.check_conflicts(activity)
            for c in name_conflicts:
                issues.append({
                    "activity_id": activity["id"],
                    "type": "conflict",
                    "message": c,
                })

            # Time overlaps
            overlaps = self.check_time_overlap(activity)
            for o in overlaps:
                issues.append({
                    "activity_id": activity["id"],
                    "type": "time_overlap",
                    "message": o,
                })

            # Pre-condition evaluation (if telemetry available)
            precond_issues = self.check_pre_conditions(activity, telemetry)
            for p in precond_issues:
                issues.append({
                    "activity_id": activity["id"],
                    "type": "pre_condition",
                    "message": p,
                })

            # Pass boundary validation
            pass_id = activity.get("pass_id")
            if pass_id is not None and contacts:
                if 1 <= pass_id <= len(contacts):
                    contact = contacts[pass_id - 1]
                    los_str = contact.get("los", "")
                    los = _parse_iso(los_str)
                    act_start = _parse_iso(activity.get("start_time", ""))
                    if act_start and los:
                        act_end = act_start + timedelta(
                            seconds=activity.get("duration_s", 300)
                        )
                        if act_end > los:
                            issues.append({
                                "activity_id": activity["id"],
                                "type": "pass_boundary",
                                "message": (
                                    f"Activity extends past LOS "
                                    f"({act_end.isoformat()} > "
                                    f"{los.isoformat()}) for pass {pass_id}"
                                ),
                            })

        # Power constraint checks
        if power_budget:
            for warning in power_budget.get("warnings", []):
                issues.append({
                    "activity_id": None,
                    "type": "power_constraint",
                    "message": warning,
                })

        # Data volume constraint checks
        if data_budget:
            for warning in data_budget.get("warnings", []):
                issues.append({
                    "activity_id": None,
                    "type": "data_constraint",
                    "message": warning,
                })

        return issues

    def validate_schedule(self, contacts: list[dict] | None = None) -> list[dict]:
        """Validate all activities for conflicts and constraint violations."""
        issues = []
        for activity in self._schedule:
            if activity.get("state") in (
                int(ActivityState.CANCELLED),
                int(ActivityState.FAILED),
            ):
                continue
            conflicts = self.check_conflicts(activity)
            for c in conflicts:
                issues.append({
                    "activity_id": activity["id"],
                    "type": "conflict",
                    "message": c,
                })
        return issues

    def check_pre_conditions(
        self, activity: dict, telemetry: dict[str, float] | None = None
    ) -> list[str]:
        """Evaluate pre-conditions for an activity against current telemetry.

        Pre-conditions are strings like ``"eps.bus_voltage > 24.0"`` that
        reference dotted telemetry paths and a comparison operator.

        Args:
            activity: activity dict with optional ``pre_conditions`` list
            telemetry: flat dict mapping dotted param names to float values
                       (e.g. ``{"eps.bus_voltage": 28.1, ...}``)

        Returns:
            List of unmet pre-condition descriptions (empty = all met)
        """
        pre_conds = activity.get("pre_conditions", [])
        if not pre_conds or telemetry is None:
            return []

        unmet: list[str] = []
        import re
        _cond_re = re.compile(
            r"^\s*([\w.]+)\s*([><=!]+)\s*([\d.eE+-]+)\s*$"
        )

        for cond in pre_conds:
            m = _cond_re.match(str(cond))
            if m is None:
                unmet.append(f"Unparseable pre-condition: {cond}")
                continue
            param_name, op, threshold_s = m.group(1), m.group(2), m.group(3)
            try:
                threshold = float(threshold_s)
            except ValueError:
                unmet.append(f"Invalid threshold in: {cond}")
                continue

            actual = telemetry.get(param_name)
            if actual is None:
                unmet.append(f"Telemetry '{param_name}' not available for: {cond}")
                continue

            satisfied = False
            if op == ">":
                satisfied = actual > threshold
            elif op == ">=":
                satisfied = actual >= threshold
            elif op == "<":
                satisfied = actual < threshold
            elif op == "<=":
                satisfied = actual <= threshold
            elif op in ("==", "="):
                satisfied = abs(actual - threshold) < 1e-6
            elif op in ("!=", "<>"):
                satisfied = abs(actual - threshold) >= 1e-6
            else:
                unmet.append(f"Unknown operator '{op}' in: {cond}")
                continue

            if not satisfied:
                unmet.append(
                    f"Pre-condition NOT met: {cond} "
                    f"(actual {param_name}={actual:.2f})"
                )

        return unmet

    def get_command_sequence(self, activity_id: int) -> list[dict]:
        """Get the command sequence for an activity."""
        activity = self.get_activity(activity_id)
        if not activity:
            return []
        return activity.get("command_sequence", [])

    def clear(self) -> None:
        self._schedule.clear()
        self._next_id = 1

    # ── Constraint Validation ────────────────────────────────────────

    def validate_constraints(
        self,
        contacts: list[dict] | None = None,
        ground_track: list[dict] | None = None,
        battery_soc_percent: float = 80.0,
    ) -> ValidationResult:
        """Validate schedule against all subsystem constraints.

        Args:
            contacts: Contact windows (optional)
            ground_track: Ground track for eclipse detection (optional)
            battery_soc_percent: Current battery state of charge

        Returns:
            ValidationResult with comprehensive constraint check status
        """
        return validate_plan(
            self._schedule,
            contacts=contacts,
            ground_track=ground_track,
            battery_soc_percent=battery_soc_percent,
        )

    def check_power_constraints(
        self,
        ground_track: list[dict] | None = None,
        initial_soc: float = 80.0,
    ) -> dict:
        """Check power budget constraints.

        Returns dict with violations and SoC timeline
        """
        checker = PowerConstraintChecker()
        return checker.check_plan_power(
            self._schedule, initial_soc, ground_track
        )

    def check_aocs_constraints(self) -> dict:
        """Check AOCS slew and momentum constraints.

        Returns dict with slew violations and momentum violations
        """
        slew_checker = AOCSConstraintChecker()
        imaging_activities = [
            a for a in self._schedule
            if "imaging" in a.get("name", "").lower()
        ]

        slew_violations = slew_checker.check_slew_constraints(
            imaging_activities
        )
        momentum_violations = slew_checker.check_momentum_budget(
            self._schedule
        )

        return {
            "slew_violations": [v.to_dict() for v in slew_violations],
            "momentum_violations": [v.to_dict() for v in momentum_violations],
        }

    def check_thermal_constraints(self) -> dict:
        """Check thermal duty cycle and cooldown constraints.

        Returns dict with duty cycle and cooldown violations
        """
        thermal_checker = ThermalConstraintChecker()

        duty_violations = thermal_checker.check_payload_duty_cycle(
            self._schedule
        )
        cooldown_violations = thermal_checker.check_cooldown_periods(
            self._schedule
        )

        return {
            "duty_cycle_violations": [v.to_dict() for v in duty_violations],
            "cooldown_violations": [v.to_dict() for v in cooldown_violations],
        }

    def check_data_volume_constraints(
        self,
        current_onboard_mb: float = 0.0,
    ) -> dict:
        """Check data volume and storage constraints.

        Args:
            current_onboard_mb: Current onboard data volume

        Returns dict with storage violations
        """
        data_checker = DataVolumeConstraintChecker()
        violations = data_checker.check_storage_capacity(
            self._schedule, current_onboard_mb
        )

        return {
            "storage_violations": [v.to_dict() for v in violations],
        }

    def check_resource_conflicts(self) -> dict:
        """Check for exclusive resource conflicts.

        Returns dict with conflict violations
        """
        conflict_checker = ConflictResolutionChecker()
        violations = conflict_checker.check_resource_conflicts(self._schedule)

        return {
            "conflict_violations": [v.to_dict() for v in violations],
        }



# _parse_iso imported from smo_planner.utils
