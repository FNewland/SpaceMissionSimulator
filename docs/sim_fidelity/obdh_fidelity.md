# OBDH Subsystem Simulator Fidelity Analysis

**Document**: EOSAT-1 OBDH Fidelity Gap Analysis and Implementation Requirements
**Date**: 2026-03-12
**Target**: "Undetectably different from real spacecraft" simulation fidelity
**Baseline**: `packages/smo-simulator/src/smo_simulator/models/obdh_basic.py` (505 lines)

---

## Table of Contents

1. [Current Model Capabilities](#1-current-model-capabilities)
2. [Gap Analysis Summary](#2-gap-analysis-summary)
3. [Gap 1 -- Bootloader State Machine](#3-gap-1----bootloader-state-machine)
4. [Gap 2 -- Circular HK Buffer](#4-gap-2----circular-hk-buffer)
5. [Gap 3 -- S12 Monitoring Enforcement Per Tick](#5-gap-3----s12-monitoring-enforcement-per-tick)
6. [Gap 4 -- S19 Event-Action Trigger Wiring](#6-gap-4----s19-event-action-trigger-wiring)
7. [Gap 5 -- Memory Operations Beyond Stubs](#7-gap-5----memory-operations-beyond-stubs)
8. [New Parameters and Config](#8-new-parameters-and-config)
9. [Test Cases Required](#9-test-cases-required)
10. [Implementation Priority](#10-implementation-priority)

---

## 1. Current Model Capabilities

The `OBDHBasicModel` in `obdh_basic.py` currently implements the following:

### 1.1 Dual OBC Cold Redundancy

- Two OBC units (A/B) with `active_obc` field (0=A, 1=B).
- Backup unit tracked via `obc_b_status` (OFF/STANDBY/ACTIVE).
- `_switchover()` method performs cold-redundant failover: swaps active unit, calls `_reboot()` with `REBOOT_SWITCHOVER` cause. No state transfer -- fresh start on new unit.
- Per-unit boot counters (`boot_count_a`, `boot_count_b`), clearable via `obc_clear_reboot_cnt`.

### 1.2 Software Image Model (Bootloader/Application)

- `sw_image` field: `SW_BOOTLOADER` (0) or `SW_APPLICATION` (1).
- `_reboot()` always drops to bootloader, sets mode to safe (1), resets TC/TM counters.
- 10-second simulated CRC verification timer (`boot_app_timer`) before transitioning to application.
- `boot_inhibit` flag prevents auto-boot after reboot.
- `boot_image_corrupt` flag simulates CRC failure -- OBC stays in bootloader.
- Manual `obc_boot_app` command triggers boot sequence from bootloader.

### 1.3 Dual CAN Bus

- Two buses (A/B) with independent status (`bus_a_status`, `bus_b_status`): OK, DEGRADED, or FAILED.
- Configurable subsystem-to-bus mapping (`bus_a_subsystems`, `bus_b_subsystems`).
- `is_subsystem_reachable()` checks both bus health and subsystem mapping.
- `obc_select_bus` command with failed-bus rejection.

### 1.4 Buffer Management

- Three buffers: HKTM (`hktm_buf_fill` / 1000 capacity), Event (`event_buf_fill` / 500 capacity), Alarm (`alarm_buf_fill` / 200 capacity).
- Stop-when-full behaviour (returns `False` from `record_event()` / `record_alarm()`).
- HKTM buffer drains during downlink (when TTC carrier lock is active via `shared_params[0x0510]`).

### 1.5 Flight Hardware Realism (Phase 4)

- SEU simulation with South Atlantic Anomaly probability model (~1 per orbit).
- Memory scrub progress (20-minute cycle, corrects 3 SEU errors on completion).
- Task count, stack usage, heap usage telemetry with mode-dependent behaviour.
- CPU load with Gaussian noise, mode-dependent offsets, bootloader/application differentiation.

### 1.6 Watchdog

- Watchdog armed in application mode only; bootloader disables watchdog.
- Configurable period (`watchdog_period_ticks`, default 30).
- Watchdog timeout triggers `_reboot(REBOOT_WATCHDOG)`.

### 1.7 TC/TM Accounting

- `record_tc_received()`, `record_tc_accepted()`, `record_tc_rejected()`, `record_tm_packet()` methods.
- CUC time maintained via `obc_time_cuc` (initialised from system clock at configure time).

### 1.8 Failure Injection

- `watchdog_reset`, `memory_errors`, `cpu_spike`, `obc_crash`, `bus_failure`, `boot_image_corrupt`, `memory_corruption`.
- All failures clearable via `clear_failure()`.

### 1.9 Commands (handle_command)

| Command | Description |
|---|---|
| `set_mode` | Set OBC mode (0/1/2) |
| `set_time` | Set CUC time |
| `memory_scrub` | Start memory scrub |
| `obc_reboot` | Reboot OBC |
| `obc_switch_unit` | Switch to other OBC |
| `obc_select_bus` | Select CAN bus (0/1) |
| `obc_boot_app` | Boot application from bootloader |
| `obc_boot_inhibit` | Enable/disable auto-boot |
| `obc_clear_reboot_cnt` | Reset all boot counters |

### 1.10 Shared Parameters Published

The model writes 28 parameters to `shared_params` per tick (0x0300--0x031E).

---

## 2. Gap Analysis Summary

| Gap | Current State | Target State | Impact |
|---|---|---|---|
| **Bootloader state machine** | Bootloader reduces CPU, disables watchdog, marks `sw_image=0`. No HK or TC restriction. | Bootloader restricts HK to beacon SID 10 only; rejects all TC except S8 func_ids 42--47 and S17.1. | **Critical** -- operators would immediately notice unrestricted commanding in bootloader. |
| **Circular HK buffer** | HK Store (S15 store 1) uses `stop-when-full` list in `tm_storage.py`. OBDH `hktm_buf_fill` counter separately tracks in-model buffer fill. | Store 1 uses circular buffer: oldest packets overwritten when full, no overflow flag for HK. | **High** -- real OBCs never stop recording HK; they overwrite oldest. |
| **S12 monitoring per tick** | `ServiceDispatcher` has `check_monitoring()` method and S12 TC handlers but `check_monitoring()` is never called from `engine._run_loop()`. | Engine calls `check_monitoring()` every tick; violations generate S5 events and optionally trigger S19 event-actions. | **High** -- S12 monitoring definitions exist but are dead code. |
| **S19 event-action wiring** | `ServiceDispatcher` has `trigger_event_action()` method but it is never called from `engine._emit_event()`. | Engine calls `trigger_event_action()` on every event emission; matched actions execute S8 functions. | **High** -- event-action definitions can be created but never fire. |
| **Memory operations** | S6 handler is stub-only: `MEM_LOAD` logs but discards data; `MEM_DUMP` returns zero bytes; `MEM_CHECK` returns hardcoded `0xABCD`. | Simulated address space per `memory_map.yaml` (11 regions); loads persist in simulated memory; dumps return actual content; checksums computed from stored content. | **Medium** -- operators notice zero dumps and constant checksums during SW upload procedures. |

---

## 3. Gap 1 -- Bootloader State Machine

### 3.1 Problem Statement

A real OBC bootloader is a minimal firmware environment. It:
- Generates only a "beacon" HK packet (reduced parameter set, longer interval) to let ground know it is alive.
- Accepts only a narrow set of commands needed for recovery: OBC lifecycle commands (reboot, switchover, bus select, boot app, boot inhibit, clear boot count) and connection test.
- Rejects all other commands with an S1.2 acceptance failure.

The current simulator makes no distinction: when `sw_image == SW_BOOTLOADER`, all 6 SIDs continue to be emitted at their normal rates, and all PUS services/commands are accepted. An operator trained on real flight software would immediately detect the simulation during any reboot or boot-inhibit scenario.

### 3.2 Existing Infrastructure

The HK structures config already defines a BootLoader SID:

```yaml
# configs/eosat1/telemetry/hk_structures.yaml
- sid: 10
  name: BootLoader
  interval_s: 16.0
  parameters:
    - { param_id: 0x030C, pack_format: B, scale: 1 }   # active_obc
    - { param_id: 0x030E, pack_format: B, scale: 1 }   # active_bus
    - { param_id: 0x0105, pack_format: H, scale: 100 }  # bus_voltage
    - { param_id: 0x0301, pack_format: h, scale: 100 }  # obc_temp
    - { param_id: 0x0308, pack_format: I, scale: 1 }    # uptime
    - { param_id: 0x030A, pack_format: H, scale: 1 }    # reboot_count
    - { param_id: 0x0311, pack_format: B, scale: 1 }    # sw_image
    - { param_id: 0x0316, pack_format: B, scale: 1 }    # last_reboot_cause
```

This is an 8-parameter beacon-style packet at 16-second interval -- exactly what a bootloader would emit. But the engine currently does not gate HK emission based on `sw_image`.

### 3.3 Implementation Requirements

#### 3.3.1 Engine-Level HK Gating

In `engine.py`, method `_emit_hk_packets()` must check the OBDH software image state:

- When `sw_image == SW_BOOTLOADER`:
  - Emit **only** SID 10 (BootLoader beacon).
  - All other SIDs (1--6) are suppressed.
- When `sw_image == SW_APPLICATION`:
  - Emit all enabled SIDs normally (current behaviour).
  - SID 10 is suppressed (application firmware replaces the beacon).

The OBDH model already publishes `sw_image` to `shared_params[0x0311]`. The engine can read this value at the top of `_emit_hk_packets()`.

**Pseudocode change in `_emit_hk_packets()`:**
```python
sw_image = int(self.params.get(0x0311, 1))  # default APPLICATION
bootloader_mode = (sw_image == 0)

for sid, interval in self._hk_intervals.items():
    if bootloader_mode:
        if sid != 10:
            continue  # suppress non-beacon SIDs
    else:
        if sid == 10:
            continue  # suppress beacon in application mode
    # ... existing emission logic ...
```

#### 3.3.2 Engine-Level TC Rejection in Bootloader

In `engine.py`, method `_dispatch_tc()`, after decommutation and before acceptance check, add a bootloader command filter:

- Read `sw_image` from `shared_params[0x0311]`.
- When `sw_image == SW_BOOTLOADER`, allow only:
  - **S17.1** (Connection Test) -- lets ground verify RF link.
  - **S8.1 with func_id in {42, 43, 44, 45, 46, 47}** -- the OBDH-specific commands:
    - 42 = `obc_reboot`
    - 43 = `obc_switch_unit`
    - 44 = `obc_select_bus`
    - 45 = `obc_boot_app`
    - 46 = `obc_boot_inhibit`
    - 47 = `obc_clear_reboot_cnt`
  - **S9.1** (Time set) -- may also be needed for time sync during LEOP.
- All other service/subtype combinations: reject with S1.2 acceptance failure, error code `0x0006` ("Bootloader mode -- command not available").

**Allowed commands constant:**
```python
BOOTLOADER_ALLOWED_S8_FUNC_IDS = {42, 43, 44, 45, 46, 47}
BOOTLOADER_ERROR_CODE = 0x0006
```

**Pseudocode addition in `_dispatch_tc()` after decommutation:**
```python
sw_image = int(self.params.get(0x0311, 1))
if sw_image == 0:  # SW_BOOTLOADER
    allowed = False
    if svc == 17 and sub == 1:
        allowed = True
    elif svc == 9 and sub == 1:
        allowed = True
    elif svc == 8 and sub == 1 and len(pkt.data_field) >= 1:
        func_id = pkt.data_field[0]
        if func_id in BOOTLOADER_ALLOWED_S8_FUNC_IDS:
            allowed = True
    if not allowed:
        rej = self.tm_builder.build_verification_failure(
            pkt.primary.apid, pkt.primary.sequence_count, 0x0006)
        self._enqueue_tm(rej)
        if obdh:
            obdh.record_tc_rejected()
        return
```

#### 3.3.3 OBDH Model Changes

The OBDH model itself (`obdh_basic.py`) needs minimal changes for this gap:

- Add a `BOOTLOADER_BEACON_SID = 10` constant.
- Add a property `is_bootloader` -> `bool` to simplify external queries.
- Optionally, `handle_command()` should also reject commands it doesn't recognise when in bootloader mode (defence in depth), though the engine-level gate is the primary enforcement.

### 3.4 Configuration Changes

- `configs/eosat1/subsystems/obdh.yaml`: Add `bootloader_beacon_sid: 10` and `bootloader_allowed_func_ids: [42, 43, 44, 45, 46, 47]` so these are configurable rather than hardcoded.
- `configs/eosat1/telemetry/hk_structures.yaml`: SID 10 already defined, no change needed.

---

## 4. Gap 2 -- Circular HK Buffer

### 4.1 Problem Statement

The onboard TM storage (`tm_storage.py`) implements **stop-when-full** behaviour for all stores including Store 1 (HK_Store). This is correct for event logs and science data on many spacecraft, but real OBC HK stores invariably use a **circular buffer** (ring buffer): when the store reaches capacity, the oldest packet is overwritten by the newest one.

This matters because:
1. A stopped HK store creates a telemetry gap that would never occur on a real spacecraft.
2. Operators performing store dumps after long non-contact periods expect the most recent N packets, not the oldest N packets.
3. The overflow flag behaviour is incorrect for HK: a real system never signals overflow because it never stops writing.

### 4.2 Current Implementation in `tm_storage.py`

```python
class OnboardTMStorage:
    def store_packet_direct(self, store_id, pkt, timestamp=0.0):
        if len(store) >= cap:
            self._overflow[store_id] = True
            return False  # STOP: reject packet
        store.append(pkt)
        return True
```

All four stores (HK, Event, Science, Alarm) use the same `store_packet_direct()` path. The `_stores` dict holds plain `list` objects.

### 4.3 Implementation Requirements

#### 4.3.1 Per-Store Buffer Mode

Add a `buffer_mode` property per store: `'stop_when_full'` (default, current behaviour) or `'circular'`.

**Updated `DEFAULT_STORES`:**
```python
DEFAULT_STORES = {
    1: {'name': 'HK_Store', 'capacity': 5000, 'buffer_mode': 'circular'},
    2: {'name': 'Event_Store', 'capacity': 1000, 'buffer_mode': 'stop_when_full'},
    3: {'name': 'Science_Store', 'capacity': 10000, 'buffer_mode': 'stop_when_full'},
    4: {'name': 'Alarm_Store', 'capacity': 500, 'buffer_mode': 'stop_when_full'},
}
```

#### 4.3.2 Circular Buffer Implementation

Replace the plain `list` for circular-mode stores with `collections.deque(maxlen=capacity)`:

```python
from collections import deque

# In __init__:
for store_id, info in defs.items():
    cap = info.get('capacity', 5000)
    mode = info.get('buffer_mode', 'stop_when_full')
    self._buffer_modes[store_id] = mode
    if mode == 'circular':
        self._stores[store_id] = deque(maxlen=cap)
    else:
        self._stores[store_id] = []
```

**Updated `store_packet_direct()`:**
```python
def store_packet_direct(self, store_id, pkt, timestamp=0.0):
    if store_id not in self._stores:
        return False
    if not self._enabled.get(store_id, False):
        return False

    store = self._stores[store_id]
    mode = self._buffer_modes.get(store_id, 'stop_when_full')

    if mode == 'circular':
        # deque(maxlen=N) automatically discards oldest on append
        store.append(pkt)
        if timestamp > 0:
            self._newest_ts[store_id] = timestamp
            # oldest_ts is the timestamp of store[0], update on wrap
        return True  # circular never fails
    else:
        # stop-when-full (existing behaviour)
        cap = self._capacities[store_id]
        if len(store) >= cap:
            self._overflow[store_id] = True
            return False
        store.append(pkt)
        # timestamp tracking...
        return True
```

#### 4.3.3 Dump Ordering

`start_dump()` must return packets in chronological order. `deque` iteration is already in insertion order, so `list(store)` remains correct. The oldest packet in a wrapped circular buffer is `store[0]`.

#### 4.3.4 Overflow Flag Semantics

For circular-mode stores, `is_overflow()` should return `False` (the store never overflows -- it wraps). Alternatively, introduce a `wrapped` flag that indicates the buffer has been full at least once, distinguishing "all data since boot" from "last N packets":

```python
def is_wrapped(self, store_id: int) -> bool:
    """True if the circular store has overwritten at least one packet."""
    return self._wrapped.get(store_id, False)
```

#### 4.3.5 OBDH Model Buffer Counter Alignment

The OBDH model's `hktm_buf_fill` counter (0x0312) should reflect the actual store fill level rather than incrementing independently. Two approaches:

- **Option A**: Have the engine pass the HK store fill level back to the OBDH model each tick.
- **Option B**: Have the OBDH model query `_tm_storage.get_status()` for store 1 fill percentage.

For circular buffers, `hktm_buf_fill` should report `len(store) / capacity * 100` as a percentage. Once the buffer wraps, this stays at 100%.

### 4.4 Configuration Changes

- `configs/eosat1/subsystems/obdh.yaml`: Add `hk_store_mode: circular` (documentation only; the actual config is in `tm_storage.py` defaults).
- No HK structure changes needed.

---

## 5. Gap 3 -- S12 Monitoring Enforcement Per Tick

### 5.1 Problem Statement

The `ServiceDispatcher` class has a complete S12 implementation:

- **S12.1** (Enable monitoring): Sets `_s12_enabled = True`.
- **S12.2** (Disable monitoring): Sets `_s12_enabled = False`.
- **S12.6** (Add monitoring definition): Stores `param_id`, `check_type`, `low_limit`, `high_limit` in `_s12_definitions`.
- **S12.7** (Delete monitoring definition): Removes from `_s12_definitions`.
- **S12.12** (Report monitoring definitions): Returns TM report of all definitions.
- **`check_monitoring()`**: Iterates all enabled definitions, checks parameter values against limits, returns violation list.

However, `check_monitoring()` is **never called** from the engine's `_run_loop()`. The docstring says "Called by the engine each tick" but this wiring does not exist. The S12 monitoring definitions are dead data.

### 5.2 Current Engine Tick Loop (relevant section)

```python
# engine.py _run_loop():
while self.running:
    # ... orbit, subsystems, cross-coupling ...
    if self._fdir_enabled:
        self._tick_fdir()       # FDIR uses its own rule set
    self._check_subsystem_events()
    self._check_transitions(orbit_state)
    self._emit_hk_packets(dt_sim)
    self._failure_manager.tick(dt_sim)
```

There is no `self._tick_s12_monitoring()` call.

### 5.3 Implementation Requirements

#### 5.3.1 ServiceDispatcher Persistence

Currently, `_dispatch_tc()` creates a **new** `ServiceDispatcher(self)` instance on every TC. This means S12 definitions added via S12.6 are lost after the TC is processed. The dispatcher must be a persistent engine attribute.

**In `engine.__init__()`:**
```python
from smo_simulator.service_dispatch import ServiceDispatcher
self._dispatcher = ServiceDispatcher(self)
```

**In `engine._dispatch_tc()`:**
```python
# Replace:
#   dispatcher = ServiceDispatcher(self)
# With:
dispatcher = self._dispatcher
```

This single change also fixes S19 definitions, S5 event enable/disable state, and S3 custom SID persistence.

#### 5.3.2 Engine Tick Integration

Add to the engine's `_run_loop()`, after FDIR checks:

```python
# S12 On-Board Monitoring
self._tick_s12_monitoring()
```

**New method `_tick_s12_monitoring()`:**
```python
def _tick_s12_monitoring(self) -> None:
    """Check S12 monitoring definitions against current parameters."""
    violations = self._dispatcher.check_monitoring()
    for v in violations:
        param_id = v['param_id']
        value = v['value']
        low = v['low_limit']
        high = v['high_limit']
        # Generate S5 event (severity 3 = MEDIUM for limit violations)
        self._emit_event({
            'event_id': 0x9000 | (param_id & 0x0FFF),
            'severity': 3,
            'description': (
                f"S12 OOL: param 0x{param_id:04X} = {value:.2f} "
                f"[{low:.2f}, {high:.2f}]"
            ),
        })
```

#### 5.3.3 S12 Transition Detection

The current `check_monitoring()` reports violations every tick when a parameter is out-of-limits. Real S12 implementations report **transitions** only (in-limits to out-of-limits, and optionally the return to in-limits). Add edge detection:

```python
# In ServiceDispatcher.check_monitoring():
for mon_id, defn in self._s12_definitions.items():
    # ...
    ool = (value < low or value > high)
    was_ool = defn.get('_in_violation', False)

    if ool and not was_ool:
        defn['_in_violation'] = True
        violations.append({...})  # transition to OOL
    elif not ool and was_ool:
        defn['_in_violation'] = False
        # Optionally emit a "back in limits" event
```

#### 5.3.4 S12 Check Rate Limiting

Real monitoring runs at a configurable check rate, not every simulation tick. Add a configurable interval:

- Default: check every 4 seconds (configurable via `s12_check_interval_s` in engine config).
- Track with a timer similar to HK timers.

#### 5.3.5 Bootloader Interaction

When in bootloader mode, S12 monitoring should be suspended (the bootloader firmware does not run the monitoring service). Add a check at the top of `_tick_s12_monitoring()`:

```python
if int(self.params.get(0x0311, 1)) == 0:  # bootloader
    return
```

---

## 6. Gap 4 -- S19 Event-Action Trigger Wiring

### 6.1 Problem Statement

The `ServiceDispatcher` has a complete S19 implementation:

- **S19.1** (Add event-action): Stores `event_type` -> `action_func_id` mapping.
- **S19.2** (Delete event-action): Removes mapping.
- **S19.4** (Enable event-action): Adds to `_s19_enabled_ids`.
- **S19.5** (Disable event-action): Removes from `_s19_enabled_ids`.
- **S19.8** (Report event-actions): Returns TM report of all definitions.
- **`trigger_event_action(event_type)`**: Iterates enabled definitions, matches `event_type`, executes `_handle_s8(1, bytes([func_id]))`.

However, `trigger_event_action()` is **never called** from `engine._emit_event()`. Event-action definitions can be uploaded by operators but will never fire.

### 6.2 Implementation Requirements

#### 6.2.1 Wiring in `_emit_event()`

Modify `engine._emit_event()` to call the dispatcher's trigger method:

```python
def _emit_event(self, ev: dict) -> None:
    pkt = self.tm_builder.build_event_packet(...)
    if pkt:
        self._enqueue_tm(pkt)
        try:
            self.event_queue.put_nowait(ev)
        except queue.Full:
            pass

        # S19 Event-Action trigger
        severity = ev.get('severity', 1)
        self._dispatcher.trigger_event_action(severity)
```

Note: The S19 `event_type` field maps to the S5 event severity (1=INFO, 2=LOW, 3=MEDIUM, 4=HIGH) per ECSS PUS-C convention. The event_id could also be used as the matching key -- see section 6.2.3.

#### 6.2.2 ServiceDispatcher Persistence (Same as Gap 3)

This requires the same dispatcher persistence fix described in section 5.3.1. Without it, S19 definitions uploaded via TC are lost immediately.

#### 6.2.3 Event Matching Granularity

The current S19 implementation matches on `event_type` (a single byte). Real implementations typically support:

- **Severity-based**: Match any event of severity >= N (current approach).
- **Event-ID-based**: Match specific event IDs (e.g., 0x0100 for SOC_CRITICAL).

Enhance `_s19_definitions` to support both:

```python
self._s19_definitions[ea_id] = {
    'event_type': event_type,       # severity match (0 = match all)
    'event_id': event_id,           # specific ID match (0 = match all)
    'action_func_id': action_func_id,
}
```

Update `trigger_event_action()` to accept both parameters:

```python
def trigger_event_action(self, event_type: int, event_id: int = 0) -> None:
    for ea_id, defn in self._s19_definitions.items():
        if ea_id not in self._s19_enabled_ids:
            continue
        type_match = (defn['event_type'] == 0 or defn['event_type'] == event_type)
        id_match = (defn.get('event_id', 0) == 0 or defn.get('event_id', 0) == event_id)
        if type_match and id_match:
            func_id = defn['action_func_id']
            self._handle_s8(1, bytes([func_id]))
```

Update `_emit_event()` call:

```python
event_id = ev.get('event_id', 0)
severity = ev.get('severity', 1)
self._dispatcher.trigger_event_action(severity, event_id)
```

#### 6.2.4 Re-entrancy Guard

An event-action that triggers a command which itself emits an event could cause infinite recursion. Add a re-entrancy guard:

```python
def trigger_event_action(self, event_type, event_id=0):
    if self._s19_in_progress:
        return  # prevent recursive triggers
    self._s19_in_progress = True
    try:
        # ... matching and execution ...
    finally:
        self._s19_in_progress = False
```

#### 6.2.5 Bootloader Interaction

S19 event-action execution should be suspended in bootloader mode (the bootloader does not run the event-action service):

```python
def trigger_event_action(self, event_type, event_id=0):
    sw_image = int(self._engine.params.get(0x0311, 1))
    if sw_image == 0:  # bootloader
        return
    # ...
```

---

## 7. Gap 5 -- Memory Operations Beyond Stubs

### 7.1 Problem Statement

The S6 Memory Management service in `service_dispatch.py` handles three subtypes:

| Subtype | Name | Current Behaviour | Problem |
|---|---|---|---|
| 2 | MEM_LOAD | Logs address and data, discards payload | Operators cannot upload patches or config |
| 5 | MEM_DUMP | Returns address + zero bytes | Operators see all-zeros for any address |
| 9 | MEM_CHECK | Returns hardcoded checksum `0xABCD` | Operators see same checksum for every region |

The project already has a detailed memory map in `configs/eosat1/subsystems/memory_map.yaml` defining 11 memory regions with addresses, sizes, and types (readonly, flash, ram).

### 7.2 Memory Map Reference

From `memory_map.yaml`:

| Region | Start | Size | Type |
|---|---|---|---|
| Boot ROM | 0x00000000 | 0x10000 (64 KB) | readonly |
| Boot Backup | 0x00010000 | 0x10000 (64 KB) | readonly |
| Application A | 0x00100000 | 0x80000 (512 KB) | flash |
| Application B | 0x00180000 | 0x80000 (512 KB) | flash |
| Configuration | 0x00200000 | 0x20000 (128 KB) | flash |
| HK Store | 0x00300000 | 0x100000 (1 MB) | flash |
| Event Store | 0x00400000 | 0x80000 (512 KB) | flash |
| Science Store | 0x00500000 | 0x800000 (8 MB) | flash |
| Alarm Store | 0x00D00000 | 0x40000 (256 KB) | flash |
| Scratchpad RAM | 0x20000000 | 0x40000 (256 KB) | ram |
| Stack/Heap | 0x20040000 | 0x20000 (128 KB) | ram |

### 7.3 Implementation Requirements

#### 7.3.1 Simulated Memory Store

Create a new class `SimulatedMemory` (in a new file `packages/smo-simulator/src/smo_simulator/sim_memory.py` or embedded in the OBDH model):

```python
class SimulatedMemory:
    """Simulated OBC address space with region-aware read/write/checksum."""

    def __init__(self, memory_map: list[dict]):
        self._regions: list[dict] = []
        self._storage: dict[str, bytearray] = {}
        for region in memory_map:
            name = region['name']
            start = region['start']
            size = region['size']
            rtype = region['type']
            self._regions.append({
                'name': name, 'start': start, 'size': size, 'type': rtype,
                'end': start + size,
            })
            # Pre-fill with pattern data
            if rtype == 'readonly':
                # Boot ROM: fill with 0xFF (erased flash pattern)
                self._storage[name] = bytearray([0xFF] * min(size, 65536))
            elif rtype == 'flash':
                self._storage[name] = bytearray([0xFF] * min(size, 65536))
            elif rtype == 'ram':
                self._storage[name] = bytearray([0x00] * min(size, 65536))
```

Note: We limit actual backing storage to 64 KB per region (or the region size, whichever is smaller) to avoid allocating 8 MB for the Science Store. Addresses beyond the backed range return 0xFF for flash or 0x00 for RAM.

#### 7.3.2 Memory Operations

**`load(address, data) -> (bool, str)`:**
```python
def load(self, address: int, data: bytes) -> tuple[bool, str]:
    region = self._find_region(address)
    if region is None:
        return False, "Address outside mapped regions"
    if region['type'] == 'readonly':
        return False, "Write to read-only region rejected"
    offset = address - region['start']
    storage = self._storage[region['name']]
    max_backed = len(storage)
    for i, byte in enumerate(data):
        if offset + i < max_backed:
            storage[offset + i] = byte
    return True, "OK"
```

**`dump(address, length) -> bytes`:**
```python
def dump(self, address: int, length: int) -> bytes:
    region = self._find_region(address)
    if region is None:
        return bytes(length)  # unmapped -> zeros
    offset = address - region['start']
    storage = self._storage[region['name']]
    max_backed = len(storage)
    result = bytearray()
    for i in range(length):
        if offset + i < max_backed:
            result.append(storage[offset + i])
        else:
            result.append(0xFF if region['type'] == 'flash' else 0x00)
    return bytes(result)
```

**`checksum(address, length) -> int`:**
```python
def checksum(self, address: int, length: int) -> int:
    data = self.dump(address, length)
    # CRC-16-CCITT (as used by many flight computers)
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return crc
```

#### 7.3.3 Updated S6 Handler

```python
def _handle_s6(self, subtype: int, data: bytes) -> list[bytes]:
    memory = getattr(self._engine, '_sim_memory', None)

    if subtype == 2 and len(data) >= 5:
        # MEM_LOAD
        addr = struct.unpack('>I', data[:4])[0]
        payload_data = data[4:]
        if memory:
            ok, msg = memory.load(addr, payload_data)
            if not ok:
                self._last_error = msg
                self._last_error_code = 0x0010
        # S6.3 load success report
        resp = struct.pack('>IH', addr, len(payload_data))
        return [self._engine.tm_builder._pack_tm(service=6, subtype=3, data=resp)]

    elif subtype == 5 and len(data) >= 6:
        # MEM_DUMP
        addr = struct.unpack('>I', data[:4])[0]
        length = struct.unpack('>H', data[4:6])[0]
        length = min(length, 256)  # cap dump size per packet
        if memory:
            content = memory.dump(addr, length)
        else:
            content = bytes(length)
        resp = struct.pack('>I', addr) + content
        return [self._engine.tm_builder._pack_tm(service=6, subtype=6, data=resp)]

    elif subtype == 9 and len(data) >= 6:
        # MEM_CHECK
        addr = struct.unpack('>I', data[:4])[0]
        length = struct.unpack('>H', data[4:6])[0]
        if memory:
            crc = memory.checksum(addr, length)
        else:
            crc = 0xABCD
        resp = struct.pack('>IHH', addr, length, crc)
        return [self._engine.tm_builder._pack_tm(service=6, subtype=10, data=resp)]

    return []
```

#### 7.3.4 Engine Integration

In `engine.__init__()`:

```python
from smo_simulator.sim_memory import SimulatedMemory
memory_map_cfg = load_memory_map(self.config_dir)  # new loader function
self._sim_memory = SimulatedMemory(memory_map_cfg)
```

#### 7.3.5 Read-Only Region Enforcement

Loads to Boot ROM (0x00000000--0x0000FFFF) and Boot Backup (0x00010000--0x0001FFFF) must be rejected. The `load()` method checks `region['type'] == 'readonly'` and returns an error.

#### 7.3.6 Application Image Validation

When `obc_boot_app` is commanded, the boot sequence could validate the Application A region checksum against a stored expected value, providing a more realistic CRC verification than the current timer-only approach.

### 7.4 Configuration Changes

- `configs/eosat1/subsystems/memory_map.yaml`: Already complete, no changes needed.
- `configs/eosat1/subsystems/obdh.yaml`: Add `memory_map_file: memory_map.yaml` reference.
- New loader function in `smo_common/config/loader.py`: `load_memory_map()`.

---

## 8. New Parameters and Config

### 8.1 New Telemetry Parameters

| Param ID | Name | Type | Description |
|---|---|---|---|
| 0x031F | `obdh.bootloader_active` | B (uint8) | 1 when in bootloader mode (derived, same as sw_image==0) |
| 0x0320 | `obdh.s12_mon_count` | H (uint16) | Number of active S12 monitoring definitions |
| 0x0321 | `obdh.s19_ea_count` | H (uint16) | Number of active S19 event-action definitions |
| 0x0322 | `obdh.hk_store_wrapped` | B (uint8) | 1 when HK circular buffer has wrapped |
| 0x0323 | `obdh.mem_load_count` | H (uint16) | Number of S6 memory loads performed |
| 0x0324 | `obdh.mem_dump_count` | H (uint16) | Number of S6 memory dumps performed |

### 8.2 Updated `parameters.yaml` Additions

```yaml
# OBDH Fidelity Enhancement params
- { id: 0x031F, name: obdh.bootloader_active, subsystem: obdh, description: "Bootloader active flag (1=bootloader, 0=application)" }
- { id: 0x0320, name: obdh.s12_mon_count, subsystem: obdh, description: "Active S12 monitoring definition count" }
- { id: 0x0321, name: obdh.s19_ea_count, subsystem: obdh, description: "Active S19 event-action definition count" }
- { id: 0x0322, name: obdh.hk_store_wrapped, subsystem: obdh, description: "HK store circular buffer wrapped flag" }
- { id: 0x0323, name: obdh.mem_load_count, subsystem: obdh, description: "S6 memory load operation count" }
- { id: 0x0324, name: obdh.mem_dump_count, subsystem: obdh, description: "S6 memory dump operation count" }
```

### 8.3 HK Structure Updates

Add new params to SID 4 (Platform):

```yaml
- { param_id: 0x031F, pack_format: B, scale: 1 }
- { param_id: 0x0320, pack_format: H, scale: 1 }
- { param_id: 0x0321, pack_format: H, scale: 1 }
- { param_id: 0x0322, pack_format: B, scale: 1 }
```

### 8.4 Configuration File Updates

**`configs/eosat1/subsystems/obdh.yaml`** additions:

```yaml
# Bootloader configuration
bootloader:
  beacon_sid: 10
  allowed_services:
    - { service: 17, subtype: 1 }    # Connection test
    - { service: 9, subtype: 1 }     # Time set
  allowed_s8_func_ids: [42, 43, 44, 45, 46, 47]
  error_code: 0x0006

# S12 monitoring
s12:
  check_interval_s: 4.0
  max_definitions: 64

# S19 event-action
s19:
  max_definitions: 32

# Memory
memory_map_file: memory_map.yaml
```

### 8.5 New Error Code

| Code | Name | Description |
|---|---|---|
| 0x0006 | `BOOTLOADER_CMD_REJECT` | Command not available in bootloader mode |
| 0x0010 | `MEM_LOAD_REJECTED` | Memory load to read-only region or unmapped address |

---

## 9. Test Cases Required

### 9.1 Bootloader State Machine Tests

| # | Test | Description | File |
|---|---|---|---|
| B-1 | `test_bootloader_suppresses_non_beacon_hk` | After reboot (with boot_inhibit), tick engine; verify only SID 10 packets emitted, SIDs 1-6 suppressed. | `test_obdh_bootloader.py` |
| B-2 | `test_bootloader_emits_beacon_at_16s` | After reboot, tick 17 seconds; verify exactly 1 SID 10 packet emitted (16s interval). | `test_obdh_bootloader.py` |
| B-3 | `test_bootloader_rejects_s3_command` | Send S3.27 (one-shot HK) while in bootloader; verify S1.2 rejection with error 0x0006. | `test_obdh_bootloader.py` |
| B-4 | `test_bootloader_rejects_s8_aocs_command` | Send S8.1 with func_id=0 (AOCS set mode) while in bootloader; verify rejection. | `test_obdh_bootloader.py` |
| B-5 | `test_bootloader_accepts_s8_obc_reboot` | Send S8.1 with func_id=42 (obc_reboot) while in bootloader; verify acceptance and S1.1 returned. | `test_obdh_bootloader.py` |
| B-6 | `test_bootloader_accepts_s8_boot_app` | Send S8.1 with func_id=45 while in bootloader; verify acceptance. | `test_obdh_bootloader.py` |
| B-7 | `test_bootloader_accepts_s17_connection_test` | Send S17.1 while in bootloader; verify S17.2 report returned. | `test_obdh_bootloader.py` |
| B-8 | `test_bootloader_accepts_s9_time_set` | Send S9.1 while in bootloader; verify acceptance. | `test_obdh_bootloader.py` |
| B-9 | `test_application_mode_all_sids_emitted` | In application mode, tick long enough to verify SIDs 1-6 are emitted. | `test_obdh_bootloader.py` |
| B-10 | `test_application_mode_sid10_suppressed` | In application mode, verify SID 10 is NOT emitted. | `test_obdh_bootloader.py` |
| B-11 | `test_boot_app_transitions_hk_to_all_sids` | Reboot, then boot_app; after transition, verify all SIDs resume. | `test_obdh_bootloader.py` |
| B-12 | `test_bootloader_rejects_s12_add_monitor` | Send S12.6 while in bootloader; verify rejection. | `test_obdh_bootloader.py` |
| B-13 | `test_bootloader_tc_rejection_counter` | Send 3 rejected TCs in bootloader; verify `tc_rej_count` increments by 3. | `test_obdh_bootloader.py` |

### 9.2 Circular HK Buffer Tests

| # | Test | Description | File |
|---|---|---|---|
| C-1 | `test_hk_store_circular_mode` | Verify store 1 is configured with `buffer_mode='circular'`. | `test_tm_storage.py` |
| C-2 | `test_circular_store_never_rejects` | Fill store 1 to capacity, add one more packet; verify return True (not rejected). | `test_tm_storage.py` |
| C-3 | `test_circular_store_overwrites_oldest` | Fill store 1 to capacity with packets A1..AN, add packet B; verify A1 is gone and B is present. | `test_tm_storage.py` |
| C-4 | `test_circular_store_dump_order` | Fill and wrap; verify `start_dump()` returns packets in chronological order (oldest first). | `test_tm_storage.py` |
| C-5 | `test_circular_store_no_overflow_flag` | Fill and wrap; verify `is_overflow()` returns False. | `test_tm_storage.py` |
| C-6 | `test_circular_store_wrapped_flag` | Fill and wrap; verify `is_wrapped()` returns True. Not wrapped before full: returns False. | `test_tm_storage.py` |
| C-7 | `test_stop_when_full_stores_unchanged` | Verify stores 2, 3, 4 still reject when full (stop-when-full behaviour preserved). | `test_tm_storage.py` |
| C-8 | `test_hktm_buf_fill_reflects_circular` | After circular wrap, verify `hktm_buf_fill` param (0x0312) stays at capacity (100%). | `test_tm_storage.py` |
| C-9 | `test_circular_store_delete_resets` | Call `delete_store(1)` on a wrapped circular store; verify empty and wrapped=False. | `test_tm_storage.py` |

### 9.3 S12 Monitoring Enforcement Tests

| # | Test | Description | File |
|---|---|---|---|
| M-1 | `test_s12_definition_persists_across_tcs` | Add S12.6 definition, then send an unrelated TC; verify definition still exists. | `test_s12_monitoring.py` |
| M-2 | `test_s12_violation_generates_event` | Add S12.6 for param 0x0302 (CPU load) with high_limit=50; set CPU to 60; tick engine; verify S5 event emitted with event_id 0x9302. | `test_s12_monitoring.py` |
| M-3 | `test_s12_no_event_within_limits` | Add S12.6 for param 0x0302 with limits [0, 100]; tick; verify no S12 event emitted. | `test_s12_monitoring.py` |
| M-4 | `test_s12_edge_detection_only_one_event` | Param goes out-of-limits and stays; verify only one event on the transition, not repeated every tick. | `test_s12_monitoring.py` |
| M-5 | `test_s12_return_to_limits_clears_flag` | Param goes OOL, then returns to limits; next OOL transition generates new event. | `test_s12_monitoring.py` |
| M-6 | `test_s12_disabled_skips_checks` | Disable monitoring (S12.2); verify no violations reported even with OOL values. | `test_s12_monitoring.py` |
| M-7 | `test_s12_bootloader_suspends_monitoring` | In bootloader mode, verify `_tick_s12_monitoring()` returns without checking. | `test_s12_monitoring.py` |
| M-8 | `test_s12_delete_removes_definition` | Add then delete (S12.7) a definition; verify it no longer produces violations. | `test_s12_monitoring.py` |
| M-9 | `test_s12_report_returns_all_definitions` | Add 3 definitions; send S12.12; verify TM report contains all 3. | `test_s12_monitoring.py` |

### 9.4 S19 Event-Action Tests

| # | Test | Description | File |
|---|---|---|---|
| E-1 | `test_s19_definition_persists` | Add S19.1 event-action; send unrelated TC; verify definition still exists. | `test_s19_event_action.py` |
| E-2 | `test_s19_triggers_on_matching_event` | Add S19.1 linking severity 4 to func_id 10 (EPS payload mode); emit event severity 4; verify EPS command executed. | `test_s19_event_action.py` |
| E-3 | `test_s19_does_not_trigger_disabled` | Add S19.1, then disable with S19.5; emit matching event; verify no action. | `test_s19_event_action.py` |
| E-4 | `test_s19_does_not_trigger_non_matching` | Add S19.1 for severity 4; emit severity 1 event; verify no action. | `test_s19_event_action.py` |
| E-5 | `test_s19_re_enable_triggers` | Disable then re-enable (S19.4); emit matching event; verify action fires. | `test_s19_event_action.py` |
| E-6 | `test_s19_recursion_guard` | Add S19 linking an event that generates another event; verify no infinite loop (max 1 level). | `test_s19_event_action.py` |
| E-7 | `test_s19_bootloader_suspended` | In bootloader, event-action should not fire. | `test_s19_event_action.py` |
| E-8 | `test_s19_delete_removes` | Add then delete (S19.2); emit matching event; verify no action. | `test_s19_event_action.py` |
| E-9 | `test_s19_report_returns_all` | Add 2 definitions; send S19.8; verify TM report contains both with enable flags. | `test_s19_event_action.py` |
| E-10 | `test_s19_event_id_matching` | Add S19.1 matching event_id 0x0100 (SOC_CRITICAL); emit 0x0100; verify action fires. Emit 0x0200; verify no action. | `test_s19_event_action.py` |

### 9.5 Memory Operation Tests

| # | Test | Description | File |
|---|---|---|---|
| S-1 | `test_mem_load_to_flash_persists` | S6.2 load 16 bytes to Application A region; S6.5 dump same address; verify data matches. | `test_s6_memory.py` |
| S-2 | `test_mem_load_to_readonly_rejected` | S6.2 load to Boot ROM (0x00000000); verify S1.8 execution failure. | `test_s6_memory.py` |
| S-3 | `test_mem_load_to_ram_persists` | S6.2 load to Scratchpad RAM (0x20000000); S6.5 dump; verify data matches. | `test_s6_memory.py` |
| S-4 | `test_mem_dump_unmapped_returns_zeros` | S6.5 dump address 0xFFFF0000 (unmapped); verify response contains zeros. | `test_s6_memory.py` |
| S-5 | `test_mem_dump_flash_default_ff` | S6.5 dump untouched flash region; verify response contains 0xFF pattern. | `test_s6_memory.py` |
| S-6 | `test_mem_checksum_varies_with_content` | Load different data to two regions; checksum each; verify different checksums. | `test_s6_memory.py` |
| S-7 | `test_mem_checksum_consistent` | Checksum same region twice without modification; verify same result. | `test_s6_memory.py` |
| S-8 | `test_mem_checksum_changes_after_load` | Checksum region, load data, checksum again; verify different result. | `test_s6_memory.py` |
| S-9 | `test_mem_dump_capped_at_256_bytes` | Request 1024-byte dump; verify response capped at 256 bytes. | `test_s6_memory.py` |
| S-10 | `test_mem_load_count_increments` | Perform 3 loads; verify `mem_load_count` param (0x0323) equals 3. | `test_s6_memory.py` |
| S-11 | `test_mem_region_boundary_load` | Load data spanning two regions; verify only data within the first region is stored. | `test_s6_memory.py` |

### 9.6 Integration Tests

| # | Test | Description | File |
|---|---|---|---|
| I-1 | `test_reboot_full_lifecycle` | Reboot -> verify bootloader beacon -> boot_app -> verify all SIDs resume -> verify S12 resumes. | `test_obdh_integration.py` |
| I-2 | `test_s12_triggers_s19_action` | Define S12 monitor on battery SoC; define S19 linking severity 3 to payload off; drive SoC below limit; verify payload powers off. | `test_obdh_integration.py` |
| I-3 | `test_sw_upload_via_s6` | Load data to Application A region; checksum; boot_app; verify application starts. | `test_obdh_integration.py` |
| I-4 | `test_bootloader_mem_dump_available` | In bootloader, S6.5 dump should be accepted (memory dump is needed for debugging in bootloader -- this is a design decision; document if rejected). | `test_obdh_integration.py` |
| I-5 | `test_circular_hk_survives_contact_gap` | Simulate 2-orbit non-contact period; dump HK store; verify most recent packets present, oldest overwritten. | `test_obdh_integration.py` |

---

## 10. Implementation Priority

| Priority | Gap | Effort Estimate | Rationale |
|---|---|---|---|
| **P0** | Dispatcher persistence (prerequisite for S12, S19) | 0.5 day | Without this, no PUS service state survives between TCs. Blocks P1 and P2. |
| **P1** | Bootloader state machine (Gap 1) | 1.5 days | Most visible fidelity gap. Operators detect immediately during LEOP/reboot training. |
| **P1** | S12 monitoring per tick (Gap 3) | 1 day | Dead code that operators expect to function. S12 is used in every monitoring scenario. |
| **P2** | S19 event-action wiring (Gap 4) | 1 day | Depends on dispatcher persistence. Enables autonomous response training. |
| **P2** | Circular HK buffer (Gap 2) | 1 day | Affects store dump realism during long non-contact periods. |
| **P3** | Memory operations (Gap 5) | 2 days | Important for SW upload procedure training but less frequently exercised. |

**Total estimated effort**: 7 days

---

*This document was generated with AI assistance.*

![AIG - Artificial Intelligence Generated](../../Screenshot%202026-03-10%20at%202.33.26%20PM.png)

Source: [https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/](https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/)
