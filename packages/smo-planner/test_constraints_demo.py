#!/usr/bin/env python3
"""Demo and smoke test for constraint checking system."""

from datetime import datetime, timedelta, timezone
from smo_planner.activity_scheduler import ActivityScheduler
from smo_planner.constraint_checkers import (
    PowerConstraintChecker,
    AOCSConstraintChecker,
    ThermalConstraintChecker,
    DataVolumeConstraintChecker,
    ConflictResolutionChecker,
)


def test_power_constraints():
    """Test power budget constraint checking."""
    print("\n=== Testing Power Constraints ===")

    checker = PowerConstraintChecker(
        battery_capacity_wh=1120.0,
        min_soc_percent=20.0,
        bus_capacity_w=300.0,
    )

    # Create a test activity that exceeds bus capacity
    high_power_activity = {
        "id": 1,
        "name": "high_power_test",
        "power_w": 400.0,  # Exceeds 300W bus limit
        "duration_s": 300,
        "start_time": "2026-04-04T10:00:00Z",
    }

    violations = checker.check_activity_power(high_power_activity)
    print(f"Found {len(violations)} violations for high-power activity")
    for v in violations:
        print(f"  - {v.message}")

    # Test SoC timeline
    activities = [
        {
            "id": 1,
            "name": "imaging_pass",
            "power_w": 75.0,
            "duration_s": 120,
            "start_time": "2026-04-04T10:00:00Z",
            "state_name": "PLANNED",
        },
        {
            "id": 2,
            "name": "data_dump",
            "power_w": 50.0,
            "duration_s": 600,
            "start_time": "2026-04-04T11:00:00Z",
            "state_name": "PLANNED",
        },
    ]

    result = checker.check_plan_power(activities, initial_soc=80.0)
    print(f"\nPlan power result:")
    print(f"  - Final SoC: {result['final_soc']}%")
    print(f"  - Violations: {len(result['violations'])}")
    for v in result['violations']:
        print(f"    - {v.message}")


def test_aocs_constraints():
    """Test AOCS constraint checking."""
    print("\n=== Testing AOCS Constraints ===")

    checker = AOCSConstraintChecker(
        max_slew_rate_deg_per_s=1.0,
        min_settling_time_s=30.0,
    )

    # Create imaging activities with location info
    activities = [
        {
            "id": 1,
            "name": "imaging_pass",
            "capture_lat": 0.0,
            "capture_lon": 0.0,
            "duration_s": 120,
            "start_time": "2026-04-04T10:00:00Z",
        },
        {
            "id": 2,
            "name": "imaging_pass",
            "capture_lat": 10.0,  # 10 degrees away
            "capture_lon": 10.0,
            "duration_s": 120,
            "start_time": "2026-04-04T10:05:00Z",  # Only 5 min gap (too small)
        },
    ]

    violations = checker.check_slew_constraints(activities)
    print(f"Found {len(violations)} slew violations")
    for v in violations:
        print(f"  - {v.message}")
        if v.suggested_fix:
            print(f"    Fix: {v.suggested_fix}")

    # Test momentum
    momentum_activities = [
        {"id": i, "name": "imaging_pass"} for i in range(6)
    ]

    momentum_violations = checker.check_momentum_budget(momentum_activities)
    print(f"\nFound {len(momentum_violations)} momentum violations")
    for v in momentum_violations:
        print(f"  - {v.message}")


def test_thermal_constraints():
    """Test thermal constraint checking."""
    print("\n=== Testing Thermal Constraints ===")

    checker = ThermalConstraintChecker(
        max_imaging_minutes_per_orbit=30.0,
        orbit_period_minutes=91.0,
    )

    # Create a schedule with excessive imaging in one orbit
    base_time = datetime(2026, 4, 4, 10, 0, 0, tzinfo=timezone.utc)
    activities = [
        {
            "id": i,
            "name": "imaging_pass",
            "duration_s": 600,  # 10 min each
            "start_time": (base_time + timedelta(minutes=i*15)).isoformat(),
        }
        for i in range(4)  # 40 minutes total in first orbit
    ]

    duty_violations = checker.check_payload_duty_cycle(activities)
    print(f"Found {len(duty_violations)} duty cycle violations")
    for v in duty_violations:
        print(f"  - {v.message}")
        if v.suggested_fix:
            print(f"    Fix: {v.suggested_fix}")

    # Test cooldown
    close_activities = [
        {
            "id": 1,
            "name": "imaging_pass",
            "duration_s": 120,
            "start_time": "2026-04-04T10:00:00Z",
        },
        {
            "id": 2,
            "name": "imaging_pass",
            "duration_s": 120,
            "start_time": "2026-04-04T10:03:00Z",  # Only 60s after first
        },
    ]

    cooldown_violations = checker.check_cooldown_periods(close_activities)
    print(f"\nFound {len(cooldown_violations)} cooldown violations")
    for v in cooldown_violations:
        print(f"  - {v.message}")


def test_data_constraints():
    """Test data volume constraint checking."""
    print("\n=== Testing Data Volume Constraints ===")

    checker = DataVolumeConstraintChecker(
        storage_capacity_mb=20000.0,
        storage_margin_percent=10.0,
    )

    # Create activities that would exceed storage
    activities = [
        {
            "id": i,
            "name": "imaging_pass",
            "data_volume_mb": 800,
            "state_name": "PLANNED",
            "start_time": f"2026-04-04T{10+i:02d}:00:00Z",
        }
        for i in range(30)  # 30 * 800 MB = 24 GB (exceeds 20 GB + 10% margin)
    ]

    violations = checker.check_storage_capacity(activities)
    print(f"Found {len(violations)} storage violations")
    for i, v in enumerate(violations[:3]):  # Show first 3
        print(f"  - {v.message}")
    if len(violations) > 3:
        print(f"  ... and {len(violations) - 3} more")


def test_conflict_resolution():
    """Test resource conflict resolution."""
    print("\n=== Testing Conflict Resolution ===")

    checker = ConflictResolutionChecker()

    # Create overlapping imaging and data dump
    activities = [
        {
            "id": 1,
            "name": "imaging_pass",
            "duration_s": 300,
            "start_time": "2026-04-04T10:00:00Z",
            "state_name": "PLANNED",
        },
        {
            "id": 2,
            "name": "data_dump",
            "duration_s": 600,
            "start_time": "2026-04-04T10:03:00Z",  # Overlaps with imaging
            "state_name": "PLANNED",
        },
    ]

    violations = checker.check_resource_conflicts(activities)
    print(f"Found {len(violations)} resource conflicts")
    for v in violations:
        print(f"  - {v.message}")
        if v.suggested_fix:
            print(f"    Fix: {v.suggested_fix}")


def test_scheduler_integration():
    """Test integration with ActivityScheduler."""
    print("\n=== Testing Scheduler Integration ===")

    activity_types = [
        {
            "name": "imaging_pass",
            "duration_s": 120,
            "power_w": 75,
            "data_volume_mb": 800,
            "priority": "high",
        },
        {
            "name": "data_dump",
            "duration_s": 600,
            "power_w": 50,
            "data_volume_mb": 0,
            "priority": "high",
        },
    ]

    scheduler = ActivityScheduler(activity_types)

    # Add some activities
    scheduler.add_activity("imaging_pass", "2026-04-04T10:00:00Z")
    scheduler.add_activity("data_dump", "2026-04-04T11:00:00Z")
    scheduler.add_activity("imaging_pass", "2026-04-04T12:00:00Z")

    # Check constraints
    power_result = scheduler.check_power_constraints()
    print(f"\nPower check:")
    print(f"  - Violations: {len(power_result['violations'])}")

    thermal_result = scheduler.check_thermal_constraints()
    print(f"\nThermal check:")
    print(f"  - Duty cycle violations: {len(thermal_result['duty_cycle_violations'])}")
    print(f"  - Cooldown violations: {len(thermal_result['cooldown_violations'])}")

    data_result = scheduler.check_data_volume_constraints()
    print(f"\nData volume check:")
    print(f"  - Storage violations: {len(data_result['storage_violations'])}")

    conflict_result = scheduler.check_resource_conflicts()
    print(f"\nConflict check:")
    print(f"  - Conflict violations: {len(conflict_result['conflict_violations'])}")


if __name__ == "__main__":
    print("=" * 60)
    print("CONSTRAINT CHECKER DEMO AND SMOKE TEST")
    print("=" * 60)

    try:
        test_power_constraints()
        test_aocs_constraints()
        test_thermal_constraints()
        test_data_constraints()
        test_conflict_resolution()
        test_scheduler_integration()

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETED SUCCESSFULLY")
        print("=" * 60)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
