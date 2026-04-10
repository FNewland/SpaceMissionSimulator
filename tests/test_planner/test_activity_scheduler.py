"""Tests for Phase 5 — ActivityScheduler (smo-planner).

Covers:
  - add_activity (type lookup, custom kwargs, ID auto-increment)
  - get_schedule (sorted by start_time)
  - get_activity by ID
  - update_state transitions
  - delete_activity
  - check_conflicts (conflicting and non-conflicting)
  - validate_schedule
  - get_command_sequence
  - clear
  - Activity warnings from conflict detection
"""
import pytest
from smo_planner.activity_scheduler import ActivityScheduler, ActivityState


# ── Fixtures ────────────────────────────────────────────────────────

SAMPLE_ACTIVITY_TYPES = [
    {
        "name": "imaging",
        "duration_s": 120,
        "power_w": 45,
        "data_volume_mb": 512,
        "priority": "high",
        "procedure_ref": "PROC-IMG-001",
        "command_sequence": [
            {"service": 8, "subtype": 1, "func_id": "0x10"},
            {"service": 8, "subtype": 1, "func_id": "0x11"},
        ],
        "conflicts_with": ["downlink"],
    },
    {
        "name": "downlink",
        "duration_s": 600,
        "power_w": 30,
        "data_volume_mb": 0,
        "priority": "high",
        "procedure_ref": "PROC-DL-001",
        "command_sequence": [
            {"service": 13, "subtype": 1},
        ],
        "conflicts_with": ["imaging"],
    },
    {
        "name": "orbit_maintenance",
        "duration_s": 60,
        "power_w": 80,
        "data_volume_mb": 0,
        "priority": "critical",
        "procedure_ref": "PROC-OM-001",
        "command_sequence": [],
        "conflicts_with": [],
    },
]


def _make_scheduler(types=None):
    """Create a scheduler with sample activity types."""
    return ActivityScheduler(types if types is not None else SAMPLE_ACTIVITY_TYPES)


# ── add_activity ────────────────────────────────────────────────────

class TestAddActivity:
    """Test adding activities with type lookup and custom kwargs."""

    def test_add_known_type_uses_defaults(self):
        sched = _make_scheduler()
        act = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        assert act["name"] == "imaging"
        assert act["duration_s"] == 120
        assert act["power_w"] == 45
        assert act["data_volume_mb"] == 512
        assert act["priority"] == "high"
        assert act["procedure_ref"] == "PROC-IMG-001"
        assert len(act["command_sequence"]) == 2
        assert act["conflicts_with"] == ["downlink"]
        assert act["state"] == int(ActivityState.PLANNED)
        assert act["state_name"] == "PLANNED"

    def test_add_unknown_type_uses_defaults(self):
        sched = _make_scheduler()
        act = sched.add_activity("custom_activity", "2026-03-11T12:00:00Z")
        assert act["name"] == "custom_activity"
        assert act["duration_s"] == 300  # default
        assert act["power_w"] == 0
        assert act["data_volume_mb"] == 0
        assert act["priority"] == "medium"
        assert act["procedure_ref"] is None
        assert act["command_sequence"] == []
        assert act["conflicts_with"] == []

    def test_add_custom_kwargs_override_type(self):
        sched = _make_scheduler()
        act = sched.add_activity(
            "imaging",
            "2026-03-11T10:00:00Z",
            duration_s=999,
            priority="critical",
        )
        assert act["duration_s"] == 999
        assert act["priority"] == "critical"

    def test_add_without_types(self):
        sched = ActivityScheduler()
        act = sched.add_activity("some_activity", "2026-03-11T10:00:00Z")
        assert act["id"] == 1
        assert act["duration_s"] == 300


# ── ID auto-increment ──────────────────────────────────────────────

class TestIDAutoIncrement:
    """Test that IDs are automatically incremented."""

    def test_first_activity_gets_id_1(self):
        sched = _make_scheduler()
        act = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        assert act["id"] == 1

    def test_sequential_ids(self):
        sched = _make_scheduler()
        a1 = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        a2 = sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        a3 = sched.add_activity("orbit_maintenance", "2026-03-11T12:00:00Z")
        assert a1["id"] == 1
        assert a2["id"] == 2
        assert a3["id"] == 3

    def test_ids_not_reused_after_delete(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.delete_activity(1)
        a2 = sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        assert a2["id"] == 2  # ID 1 not reused

    def test_clear_resets_ids(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        sched.clear()
        act = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        assert act["id"] == 1


# ── get_schedule ────────────────────────────────────────────────────

class TestGetSchedule:
    """Test that get_schedule returns a sorted copy."""

    def test_empty_schedule(self):
        sched = _make_scheduler()
        assert sched.get_schedule() == []

    def test_sorted_by_start_time(self):
        sched = _make_scheduler()
        sched.add_activity("orbit_maintenance", "2026-03-11T14:00:00Z")
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T12:00:00Z")
        schedule = sched.get_schedule()
        times = [a["start_time"] for a in schedule]
        assert times == sorted(times)

    def test_returns_copy(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        schedule = sched.get_schedule()
        schedule.clear()
        assert len(sched.get_schedule()) == 1  # original unchanged


# ── get_activity ────────────────────────────────────────────────────

class TestGetActivity:
    """Test retrieving activities by ID."""

    def test_get_existing_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        act = sched.get_activity(1)
        assert act is not None
        assert act["name"] == "imaging"

    def test_get_nonexistent_activity(self):
        sched = _make_scheduler()
        assert sched.get_activity(999) is None

    def test_get_after_multiple_adds(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        sched.add_activity("orbit_maintenance", "2026-03-11T12:00:00Z")
        act = sched.get_activity(2)
        assert act["name"] == "downlink"


# ── update_state ────────────────────────────────────────────────────

class TestUpdateState:
    """Test state transitions."""

    def test_update_to_validated(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        result = sched.update_state(1, ActivityState.VALIDATED)
        assert result is True
        act = sched.get_activity(1)
        assert act["state"] == int(ActivityState.VALIDATED)
        assert act["state_name"] == "VALIDATED"

    def test_update_through_lifecycle(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        for state in [
            ActivityState.VALIDATED,
            ActivityState.UPLOADED,
            ActivityState.EXECUTING,
            ActivityState.COMPLETED,
        ]:
            assert sched.update_state(1, state) is True
        act = sched.get_activity(1)
        assert act["state"] == int(ActivityState.COMPLETED)
        assert act["state_name"] == "COMPLETED"

    def test_update_to_failed(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.update_state(1, ActivityState.EXECUTING)
        sched.update_state(1, ActivityState.FAILED)
        act = sched.get_activity(1)
        assert act["state"] == int(ActivityState.FAILED)
        assert act["state_name"] == "FAILED"

    def test_update_to_cancelled(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.update_state(1, ActivityState.CANCELLED)
        act = sched.get_activity(1)
        assert act["state"] == int(ActivityState.CANCELLED)

    def test_update_nonexistent_returns_false(self):
        sched = _make_scheduler()
        result = sched.update_state(999, ActivityState.VALIDATED)
        assert result is False


# ── delete_activity ─────────────────────────────────────────────────

class TestDeleteActivity:
    """Test deleting activities."""

    def test_delete_existing_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        result = sched.delete_activity(1)
        assert result is True
        assert sched.get_activity(1) is None
        assert len(sched.get_schedule()) == 0

    def test_delete_nonexistent_returns_false(self):
        sched = _make_scheduler()
        result = sched.delete_activity(999)
        assert result is False

    def test_delete_middle_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        sched.add_activity("orbit_maintenance", "2026-03-11T12:00:00Z")
        sched.delete_activity(2)
        schedule = sched.get_schedule()
        assert len(schedule) == 2
        assert all(a["id"] != 2 for a in schedule)


# ── check_conflicts ─────────────────────────────────────────────────

class TestCheckConflicts:
    """Test conflict detection between activities."""

    def test_no_conflicts_for_compatible_activities(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        # orbit_maintenance does not conflict with imaging
        activity = {
            "name": "orbit_maintenance",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 60,
            "conflicts_with": [],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 0

    def test_conflict_with_named_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        # downlink conflicts with imaging
        activity = {
            "name": "downlink",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 600,
            "conflicts_with": ["imaging"],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 1
        assert "imaging" in conflicts[0]

    def test_bidirectional_conflict(self):
        """If A conflicts_with B, the check should also detect B's conflicts_with A."""
        sched = _make_scheduler()
        # Add imaging (which has conflicts_with=["downlink"])
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        # Now check downlink with empty conflicts_with — should still detect
        # because imaging.conflicts_with includes "downlink"
        activity = {
            "name": "downlink",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 600,
            "conflicts_with": [],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 1
        assert "imaging" in conflicts[0]

    def test_no_conflict_with_cancelled_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.update_state(1, ActivityState.CANCELLED)
        activity = {
            "name": "downlink",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 600,
            "conflicts_with": ["imaging"],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 0

    def test_no_conflict_with_failed_activity(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.update_state(1, ActivityState.FAILED)
        activity = {
            "name": "downlink",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 600,
            "conflicts_with": ["imaging"],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 0

    def test_conflict_with_multiple_activities(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("imaging", "2026-03-11T10:05:00Z")
        activity = {
            "name": "downlink",
            "start_time": "2026-03-11T10:00:00Z",
            "duration_s": 600,
            "conflicts_with": ["imaging"],
        }
        conflicts = sched.check_conflicts(activity)
        assert len(conflicts) == 2


# ── Activity warnings from conflicts ────────────────────────────────

class TestActivityWarnings:
    """Test that add_activity attaches warnings when conflicts are detected."""

    def test_no_warnings_when_no_conflicts(self):
        sched = _make_scheduler()
        act = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        assert "warnings" not in act

    def test_warnings_attached_on_conflict(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        act = sched.add_activity("downlink", "2026-03-11T10:00:00Z")
        assert "warnings" in act
        assert len(act["warnings"]) >= 1
        assert "imaging" in act["warnings"][0]

    def test_activity_still_added_despite_warnings(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T10:00:00Z")
        assert len(sched.get_schedule()) == 2


# ── validate_schedule ───────────────────────────────────────────────

class TestValidateSchedule:
    """Test full schedule validation."""

    def test_validate_clean_schedule(self):
        sched = _make_scheduler()
        sched.add_activity("orbit_maintenance", "2026-03-11T10:00:00Z")
        sched.add_activity("orbit_maintenance", "2026-03-11T12:00:00Z")
        issues = sched.validate_schedule()
        assert len(issues) == 0

    def test_validate_detects_conflicts(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T10:00:00Z")
        issues = sched.validate_schedule()
        assert len(issues) > 0
        assert all(i["type"] == "conflict" for i in issues)

    def test_validate_ignores_cancelled_activities(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T10:00:00Z")
        sched.update_state(1, ActivityState.CANCELLED)
        issues = sched.validate_schedule()
        assert len(issues) == 0

    def test_validate_empty_schedule(self):
        sched = _make_scheduler()
        issues = sched.validate_schedule()
        assert issues == []

    def test_validate_issues_contain_activity_id(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T10:00:00Z")
        issues = sched.validate_schedule()
        for issue in issues:
            assert "activity_id" in issue
            assert "type" in issue
            assert "message" in issue


# ── get_command_sequence ────────────────────────────────────────────

class TestGetCommandSequence:
    """Test retrieving command sequences for activities."""

    def test_get_command_sequence_with_commands(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        cmds = sched.get_command_sequence(1)
        assert len(cmds) == 2
        assert cmds[0]["service"] == 8
        assert cmds[0]["func_id"] == "0x10"

    def test_get_command_sequence_empty(self):
        sched = _make_scheduler()
        sched.add_activity("orbit_maintenance", "2026-03-11T10:00:00Z")
        cmds = sched.get_command_sequence(1)
        assert cmds == []

    def test_get_command_sequence_nonexistent(self):
        sched = _make_scheduler()
        cmds = sched.get_command_sequence(999)
        assert cmds == []


# ── clear ───────────────────────────────────────────────────────────

class TestClear:
    """Test clearing the scheduler."""

    def test_clear_removes_all_activities(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        sched.clear()
        assert sched.get_schedule() == []

    def test_clear_resets_next_id(self):
        sched = _make_scheduler()
        sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        sched.add_activity("downlink", "2026-03-11T11:00:00Z")
        sched.clear()
        act = sched.add_activity("imaging", "2026-03-11T10:00:00Z")
        assert act["id"] == 1
