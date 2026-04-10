# Post-Separation Scenario Configuration

## Overview

The EOSAT-1 Space Mission Simulator now supports realistic post-separation initialization. Previously, the simulator defaulted to Phase 6 (NOMINAL operations) where all subsystems were already commissioned. The new separation scenario starts the spacecraft in Phase 1 (SEPARATION_TIMER) with all systems powered down, simulating the actual state immediately after separation from the launch vehicle.

## Changes Made

### 1. New Configuration File: `configs/eosat1/scenarios/separation.yaml`

This file defines the post-separation scenario for instructors and students. It includes:
- Scenario name: "Post-Separation"
- Duration: 1 hour (3600s) to cover separation timer (30 min) + initial commissioning
- Briefing explaining the scenario objectives
- Expected responses for evaluating student performance

### 2. Enhanced Engine: `packages/smo-simulator/src/smo_simulator/engine.py`

#### New Method: `configure_separation_state()`

This method comprehensively configures the entire spacecraft for post-separation:

```python
def configure_separation_state(self) -> None:
    """Configure spacecraft for post-separation state."""
```

**Spacecraft Configuration:**
- **Phase**: 1 (SEPARATION_TIMER) - 30-minute countdown before initial power-on
- **Timer Duration**: 1800 seconds (30 minutes)

**Subsystem States:**

| Subsystem | Configuration | Details |
|-----------|---------------|---------|
| **EPS** | Battery: 95% SoC | Freshly charged pre-launch battery |
| | All switchable power lines: OFF | Payload, TX, wheels, cooler, heaters all disabled |
| **AOCS** | Mode: 0 (OFF) | No attitude control active |
| | Tumble Rates: ~1-2 deg/s random | Simulates separation impulse tip-off tumbling |
| | Attitude: Identity quaternion | [0, 0, 0, 1] - nominal body orientation |
| **TCS** | All heaters: OFF | No active thermal control |
| | All zones: ~20°C ambient | Panel, OBC, battery, FPA, thruster |
| **TTC** | Antenna: NOT deployed | Mechanical deployment needed later |
| | Beacon Mode: ON | Low-rate bootloader telemetry only |
| | PA (Power Amp): OFF | No transmission capability |
| **OBDH** | Software Image: 0 (BOOTLOADER) | Running bootloader, not application |
| | Boot Timer: Not running | OBC not yet initiated boot sequence |
| **Payload** | Mode: 0 (OFF) | Imager completely powered down |
| | Cooler: OFF | FPA temperature drifts with panels |

#### Updated Method: `_handle_instructor_cmd()`

The `start_separation` instructor command now calls `configure_separation_state()`:

```python
elif t == 'start_separation':
    self.configure_separation_state()
    self._emit_event({
        'event_id': 0x0053,
        'severity': 2,
        'description': "Separation initiated — 30 min timer started",
    })
```

## Usage

### Via Instructor Command

Send the instructor command to initiate separation:

```python
{
    'type': 'start_separation'
}
```

This transitions the spacecraft from any operational phase to post-separation immediately.

### Phase Progression

The separation scenario follows this phase progression:

```
Phase 0: PRE_SEPARATION (not used in this scenario)
    ↓
Phase 1: SEPARATION_TIMER (30 min countdown)
    • Everything OFF
    • Separation impulse tumble continues
    • Monitor timer on parameter 0x0128
    • Timer expires → transition to Phase 2
    ↓
Phase 2: INITIAL_POWER_ON (auto-transition after timer)
    • OBC and RX power lines enabled
    • Bootloader starts automatically
    • Transition to Phase 3
    ↓
Phase 3: BOOTLOADER_OPS
    • Running bootloader (beacon HK only)
    • Students can send commands to:
      * Enable TTC_TX power line
      * Deploy antenna (TTC command)
      * Boot application (TTC command)
    ↓
Phase 4+: LEOP/COMMISSIONING/NOMINAL
    • After application boot, transition to higher phases
    • Full subsystem commissioning available
```

## Learning Objectives

Students using the separation scenario learn to:

1. **Monitor Separation Timer** - Understand the 30-minute passive phase
2. **Boot OBC** - Sequence the bootloader → application transition
3. **Enable Transmitter** - Turn on switchable power line via EPS commands
4. **Deploy Antenna** - Execute mechanical deployment via TTC command
5. **Verify Communications** - Establish command link with spacecraft
6. **Detumble Spacecraft** - Execute AOCS detumble maneuver
7. **Commission Thermal Control** - Turn on heaters, monitor temperatures
8. **Verify Nominal Power** - Enable nominal power loads once stable

## Parameter IDs

Key parameters for monitoring separation:

| Param ID | Description | Notes |
|----------|-------------|-------|
| 0x0129 | Phase state | 1=SEPARATION_TIMER, 2=INITIAL_POWER_ON, 3=BOOTLOADER_OPS |
| 0x0127 | Timer active flag | 1 = separation timer running |
| 0x0128 | Timer countdown (s) | 0-1800 seconds remaining |
| 0x0101 | Battery SoC (%) | Should be 95% at separation |
| 0x050x | AOCS rates | Rate_roll, rate_pitch, rate_yaw |
| 0x0500 | TTC link active | 0 = no contact (until antenna deployed) |

## Example Scenario Flow

**T+0s**: Separation command
- Phase 1, Timer 1800s
- All power lines OFF except OBC/RX (unswitchable)
- AOCS tumbling at 1-2 deg/s

**T+1800s**: Timer expires → Phase 2 → Phase 3
- OBC bootloader started
- Beacon mode active
- Students receive bootloader HK packets

**T+1850s**: Student deploys antenna
- TTC antenna_deployed = True
- Link becomes possible when in contact

**T+2100s**: Student during contact window
- Sends: Enable TTC_TX power line
- Sends: Command to boot application
- Receives: Application boot confirmation

**T+2300s**: Application running → Phase 4+
- Full command set available
- Thermal/AOCS commissioning begins

## Testing

The implementation has been validated with:
- Syntax checks (Python AST compilation)
- State configuration tests (all subsystems properly initialized)
- Command handler tests (start_separation trigger)
- Phase progression tests (timer countdown and transitions)

Run tests with:
```bash
cd /sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation
pytest tests/test_simulator/test_leop_engine.py -v
```

## Files Modified

1. **Created**: `/configs/eosat1/scenarios/separation.yaml` - Scenario definition
2. **Modified**: `/packages/smo-simulator/src/smo_simulator/engine.py` - Added `configure_separation_state()` method and updated `start_separation` handler

## Backward Compatibility

The default spacecraft phase remains 6 (NOMINAL) for existing tests and code that doesn't explicitly call `start_separation`. This ensures all existing tests continue to pass without modification.

To use the separation scenario:
- Explicitly call the `start_separation` instructor command
- Or load the `separation.yaml` scenario from the MCS UI

## Implementation Details

### AOCS Tumble Simulation

Separation tumble rates are randomly generated per axis:
```python
rate_X = random.uniform(0.8, 2.0) * (±1)  # deg/s
```

This provides realistic variation in each test run while keeping rates in the expected 1-2 deg/s range for typical 6U cubesat separations.

### Battery State of Charge

Battery is set to 95% SoC to reflect:
- Full pre-launch charge
- Small losses during encapsulation and launch sequence
- Battery health margin after 30-minute passive phase

### Power Line Management

Only **switchable** power lines are disabled at separation. **Unswitchable** lines (OBC, RX) remain controllable but may be disabled during Phase 1:
- Switchable: payload, ttc_tx, fpa_cooler, htr_bat, htr_obc, aocs_wheels
- Unswitchable: obc, ttc_rx

### Command Decoder Timer

The TTC command decoder enable timer (0x0522) is set to 900s (15 minutes), allowing students to command the spacecraft during the 30-minute separation window even before antenna deployment, which enables link establishment.
