# Mission Planner Constraint Enforcement

## Overview

The EOSAT-1 Mission Planner now includes comprehensive subsystem constraint checking to enforce mission feasibility during activity scheduling. The constraint system validates power budgets, AOCS limitations, thermal constraints, and data storage across the mission timeline.

## Architecture

### Core Components

1. **PowerConstraintChecker** (`constraint_checkers.py`)
   - Validates battery state of charge (SoC) throughout mission
   - Checks activity power consumption against bus capacity
   - Models eclipse periods and solar generation
   - Predicts SoC timeline and identifies violations

2. **AOCSConstraintChecker**
   - Validates slew time requirements between imaging passes
   - Enforces minimum settling time (30s default) after slews
   - Tracks momentum accumulation and desaturation needs
   - Suggests desaturation windows during ground contacts

3. **ThermalConstraintChecker**
   - Enforces payload duty cycle limits (30 min/orbit default)
   - Checks required cooldown periods between imaging (300s default)
   - Tracks FPA temperature constraints
   - Prevents thermal runaway scenarios

4. **DataVolumeConstraintChecker**
   - Tracks onboard data volume generation and downlink
   - Validates storage capacity headroom (10% margin default)
   - Schedules data dump activities to maintain margin
   - Projects data timeline across mission

5. **ConflictResolutionChecker**
   - Detects exclusive resource conflicts
   - Implements priority-based resolution (imaging > calibration > maintenance)
   - Prevents simultaneous antenna usage
   - Prevents overlapping high-power activities

### Integration Points

#### ActivityScheduler Enhancements

New methods in `ActivityScheduler`:

```python
def validate_constraints(
    contacts: list[dict] | None = None,
    ground_track: list[dict] | None = None,
    battery_soc_percent: float = 80.0,
) -> ValidationResult:
    """Comprehensive validation across all constraints."""

def check_power_constraints(...) -> dict
def check_aocs_constraints(...) -> dict
def check_thermal_constraints(...) -> dict
def check_data_volume_constraints(...) -> dict
def check_resource_conflicts(...) -> dict
```

#### REST API Endpoints

New constraint validation endpoints in server:

```
GET /api/constraints/validate
    - Query: battery_soc (float, default 80)
    - Returns: full validation result with all violations

GET /api/constraints/power
    - Returns: power budget violations and SoC timeline

GET /api/constraints/aocs
    - Returns: slew and momentum violations

GET /api/constraints/thermal
    - Returns: duty cycle and cooldown violations

GET /api/constraints/data-volume
    - Query: current_onboard_mb (float)
    - Returns: storage violations

GET /api/constraints/conflicts
    - Returns: resource conflict violations
```

## Power Budget Model

### Power Consumption Per Activity

Default values (configurable in code):

| Activity | Power (W) | Notes |
|----------|-----------|-------|
| imaging_pass | 75 | 60W payload + 15W AOCS fine point |
| data_dump | 50 | 40W TTC high power + 10W OBDH |
| calibration | 50 | Payload calibration |
| momentum_desaturation | 30 | Magnetorquers |
| housekeeping | 5 | Minimal |
| software_upload | 20 | TTC reception |

### Battery Model

- **Capacity**: 1120 Wh (40 Ah @ 28V)
- **Initial SoC**: 80% (configurable)
- **Min SoC Threshold**: 20% (configurable)
- **Voltage Range**: 21.5V - 29.2V

### Solar Generation

- **Peak Output**: 280W @ beta=0°
- **Variation**: cos(beta) scaling with solar angle
- **Duty Cycle**: ~65% sunlit (35% eclipse for 450km SSO)

### Energy Balance

For each activity interval:

```
Energy In:  solar_output * sunlight_fraction * time
Energy Out: base_drain + activity_power * time
Net:        current_soc + energy_in - energy_out
```

## AOCS Constraints

### Slew Requirements

- **Max Slew Rate**: 1.0 deg/s (configurable)
- **Required Gap**: slew_time + settling_time
- **Settling Time**: 30s (configurable)

Slew time calculated as:
```
slew_time_s = angular_separation_deg / max_slew_rate_deg_per_s
```

Angular separation computed from lat/lon using:
```
distance = sqrt(dlat^2 + (dlon * cos(mean_lat))^2)
```

### Momentum Management

- **Capacity**: 100 h·km²/s (configurable)
- **Warning Threshold**: 80% of capacity
- **Momentum Per Imaging**: 0.15 h·km²/s (configurable)
- **Desaturation**: Resets momentum to zero

Auto-insertion of desaturation activities when momentum threshold exceeded.

## Thermal Constraints

### Payload Duty Cycle

- **Max Per Orbit**: 30 minutes (configurable)
- **Orbit Period**: 91 minutes (450km SSO)
- **Calculation**: Sum imaging duration per orbital period

### FPA Cooling

- **Target Temperature**: -15°C
- **Operating Range**: -50°C to -5°C
- **Cooler Power**: 15W
- **Tau (cooling)**: 100s
- **Tau (warming)**: 120s

### Cooldown Requirements

- **Between Imaging**: 300s (configurable)
- **Purpose**: Allow FPA temperature stabilization
- **Enforcement**: Required gap between consecutive imaging passes

## Data Storage Model

### Storage Management

- **Total Capacity**: 20 GB
- **Safety Margin**: 10% (2 GB)
- **Minimum Headroom**: 2 GB maintained at all times

### Data Generation

- **Imaging Pass**: 800 MB per pass
- **Calibration**: 50 MB
- **Housekeeping**: 10 MB per collection

### Downlink Rates

- **Nominal Rate**: 64 kbps
- **Protocol Overhead**: 20%
- **Effective Rate**: 51.2 kbps
- **Elevation Dependence**: Efficiency factor based on pass max elevation

Pass elevation categories:
- ≤ 5°: 30% efficiency (marginal)
- 5-10°: 50% efficiency
- 10-30°: 60-90% (interpolated)
- > 30°: 90-100% (interpolated)

## Configuration Updates

### activity_types.yaml Enhancements

Each activity type now includes:

```yaml
activity_types:
  - name: imaging_pass
    duration_s: 120
    min_duration_s: 60        # Minimum allowed duration
    max_duration_s: 300       # Maximum allowed duration
    power_w: 75               # Power consumption
    data_volume_mb: 800       # Data generation
    requires_subsystems:      # Required subsystems
      - payload
      - aocs
    conflicts_with:           # Conflicting activities
      - momentum_desaturation
    thermal_constraints:
      max_continuous_minutes: 30
      max_per_orbit_minutes: 30
      required_cooldown_s: 300
      fpa_temp_min_c: -50.0
      fpa_temp_max_c: -5.0
    aocs_constraints:
      # For desaturation activities:
      resets_momentum: true
      target_momentum: 0.0
      required_gap_s_after: 30
```

## Validation Results

### ConstraintViolation Object

```python
@dataclass
class ConstraintViolation:
    checker_name: str           # Which checker found it
    severity: str               # "error", "warning", "info"
    activity_id: int | None
    activity_name: str | None
    message: str                # Description of violation
    suggested_fix: str | None   # Recommended action
```

### ValidationResult Object

```python
{
    "valid": bool,              # Overall pass/fail
    "violation_count": int,
    "error_count": int,
    "warning_count": int,
    "violations": [
        {
            "checker": "PowerConstraintChecker",
            "severity": "error",
            "activity_id": 3,
            "activity_name": "imaging_pass",
            "message": "Activity would result in SoC of 18.5% (below minimum 20%)",
            "suggested_fix": "Delay activity or reduce power consumption; need 1.5% more charge"
        },
        ...
    ],
    "checker_results": {
        "power": {
            "final_soc": 65.3,
            "soc_timeline": [...]
        }
    }
}
```

## Usage Examples

### Basic Constraint Check

```python
from smo_planner.activity_scheduler import ActivityScheduler
from smo_planner.constraint_checkers import validate_plan

# Create scheduler and add activities
scheduler = ActivityScheduler(activity_types)
scheduler.add_activity("imaging_pass", "2026-04-04T10:00:00Z", ...)

# Validate entire plan
result = scheduler.validate_constraints(
    contacts=contact_windows,
    ground_track=ground_track_data,
    battery_soc_percent=80.0
)

if not result.is_valid:
    for violation in result.violations:
        if violation.severity == "error":
            print(f"ERROR: {violation.message}")
            print(f"  Fix: {violation.suggested_fix}")
```

### API Usage

```bash
# Full constraint validation
curl "http://localhost:9091/api/constraints/validate?battery_soc=78.5"

# Check power budget only
curl "http://localhost:9091/api/constraints/power"

# Check AOCS constraints
curl "http://localhost:9091/api/constraints/aocs"

# Check thermal constraints
curl "http://localhost:9091/api/constraints/thermal"

# Check data volume
curl "http://localhost:9091/api/constraints/data-volume?current_onboard_mb=2500"

# Check resource conflicts
curl "http://localhost:9091/api/constraints/conflicts"
```

## Priority-Based Conflict Resolution

When two activities conflict for exclusive resources, the scheduler uses priority ranking:

| Activity | Priority | Reasoning |
|----------|----------|-----------|
| imaging_pass | 100 | Primary mission objective |
| software_upload | 85 | Critical for flight safety |
| data_dump | 80 | Essential for mission continuity |
| momentum_desaturation | 50 | AOCS health management |
| calibration | 40 | Quality improvement |
| housekeeping | 10 | Lowest priority |

Higher priority activities are retained; lower priority activities are delayed or repositioned.

## Extensibility

### Adding New Constraints

1. Create new checker class inheriting key methods:
   ```python
   class NewConstraintChecker:
       def check_constraint(self, activities: list[dict]) -> list[ConstraintViolation]:
           violations = []
           # ... constraint logic ...
           return violations
   ```

2. Integrate into `validate_plan()` function
3. Add REST API endpoint to `PlannerServer`
4. Update activity_types.yaml with new constraint fields

### Customizing Thresholds

All constraint thresholds are parameterized:

```python
# Power
checker = PowerConstraintChecker(
    battery_capacity_wh=1120.0,
    min_soc_percent=20.0,        # Can change to 25%
    bus_capacity_w=300.0,
)

# AOCS
checker = AOCSConstraintChecker(
    max_slew_rate_deg_per_s=1.0,
    min_settling_time_s=30.0,
)

# Thermal
checker = ThermalConstraintChecker(
    max_imaging_minutes_per_orbit=30.0,  # Can change to 45 min
    fpa_cooldown_period_s=300.0,
)

# Data
checker = DataVolumeConstraintChecker(
    storage_capacity_mb=20000.0,
    storage_margin_percent=10.0,         # Can change to 15%
)
```

## Performance Considerations

- **Validation Time**: O(n²) for n activities (conflict checking)
- **Memory**: O(n) for SoC timeline tracking
- **Typical Schedule**: <100ms for 20-30 activities

Optimization strategies for large plans:
1. Batch constraint checks (don't validate on every add_activity)
2. Cache ground track eclipse fractions
3. Lazy evaluation of non-critical checkers

## Testing

Run constraint validation tests:

```bash
cd packages/smo-planner
python -m pytest tests/test_constraints.py -v
```

Example test coverage:
- Power SoC depletion scenarios
- AOCS slew time calculations
- Thermal duty cycle limits
- Storage overflow detection
- Exclusive resource conflicts
- Priority-based resolution
