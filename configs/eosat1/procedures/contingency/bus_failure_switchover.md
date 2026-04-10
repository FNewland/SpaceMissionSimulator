# PROC-OBC-OFF-002: CAN Bus Failure Switchover

**Category:** Contingency
**Position Lead:** FDIR / Systems
**Cross-Position:** Flight Director, all positions
**Difficulty:** Advanced

## Objective
Respond to a CAN bus failure that results in loss of housekeeping telemetry from one or
more subsystems. This procedure identifies which bus has failed, commands a switchover to
the redundant bus, and verifies that all subsystem housekeeping resumes on the new bus.

## Prerequisites
- [ ] Housekeeping data missing from one or more subsystems (HK packets not received)
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] OBC is responsive — `obdh.mode` (0x0300) can be queried
- [ ] Flight Director notified and authorizes bus switchover

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.bus_a_status | 0x030F | 0 (OK) or 1 (ERROR) |
| obdh.bus_b_status | 0x0310 | 0 (OK) or 1 (ERROR) |
| obdh.active_bus | 0x030E | 0 (Bus A) or 1 (Bus B) |
| obdh.mode | 0x0300 | Current OBC mode |
| obdh.cpu_load | 0x0302 | Monitor for anomalous increase |
| eps.bat_soc | 0x0101 | Record current value |
| eps.bus_voltage | 0x0105 | Record current value |
| aocs.mode | 0x020F | Verify after switchover |
| payload.mode | 0x0600 | Verify after switchover |
| ttc.link_status | 0x0501 | 1 (active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| OBC_SELECT_BUS | 8 | 1 | 44 | Select active CAN bus (0=A, 1=B) |

## Procedure Steps

### Step 1: Detect Missing Housekeeping
**Action:** Identify which subsystems are missing from housekeeping telemetry.
**Action:** Request individual HK reports for each subsystem:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=4)` — OBDH
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** Which SID responses are received and which are missing.
**Note:** If only OBDH responds (SID 4), the CAN bus connecting to external subsystems
has likely failed. If no responses are received, the issue may be OBC or TTC related
rather than bus-related.
**GO/NO-GO:** Missing HK confirmed from multiple subsystems — proceed to bus diagnosis.

### Step 2: Check Bus Status
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.bus_a_status` (0x030F) — record value:
- 0 = OK
- 1 = ERROR
**Verify:** `obdh.bus_b_status` (0x0310) — record value
**Verify:** `obdh.active_bus` (0x030E) — record which bus is currently active:
- 0 = Bus A
- 1 = Bus B
**Action:** Determine the fault:
- If active bus shows ERROR status, switchover is needed.
- If both buses show OK but HK is missing, the issue may be elsewhere (subsystem-level
  failure). Investigate individual subsystems before bus switchover.
**GO/NO-GO:** Active bus confirmed in ERROR state — proceed to switchover.

### Step 3: Command Bus Switchover
**Action:** Command switchover to the redundant bus:
- If currently on Bus A (0x030E = 0): `OBC_SELECT_BUS(bus=1)` (func_id 44) — switch to Bus B
- If currently on Bus B (0x030E = 1): `OBC_SELECT_BUS(bus=0)` (func_id 44) — switch to Bus A
**Note:** CAUTION — This command will briefly interrupt all bus communications during
the switchover. Expect a 5-10 s communication gap with subsystems.
**Verify:** Wait 15 s for switchover to complete.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.active_bus` (0x030E) reflects the new bus
**Verify:** `obdh.bus_a_status` (0x030F) and `obdh.bus_b_status` (0x0310) — record new states
**GO/NO-GO:** Bus switchover confirmed — proceed to verification.

### Step 4: Verify All Housekeeping Resumes
**Action:** Request HK from all subsystems:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=4)` — OBDH
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** All SID responses are received within 15 s.
**Verify:** Telemetry values are consistent and sensible (no stuck values, no zeros).
**Verify:** `eps.bat_soc` (0x0101) — record value (unchanged from pre-switchover)
**Verify:** `eps.bus_voltage` (0x0105) — record value
**Verify:** `aocs.mode` (0x020F) — AOCS mode unchanged
**Verify:** `payload.mode` (0x0600) — payload mode unchanged
**Verify:** `obdh.cpu_load` (0x0302) — not anomalously high
**Action:** Announce "ALL HK RESTORED" on the operations loop.
**GO/NO-GO:** All subsystem HK received and nominal — procedure complete.

### Step 5: Post-Switchover Assessment
**Action:** Log the bus failure event:
- Time of HK loss detection
- Which bus failed (A or B)
- Duration of HK gap
- Which subsystems were affected
- New active bus
**Action:** Verify that the failed bus status is recorded for engineering assessment.
**Action:** Continue operations on the new bus. The failed bus should NOT be switched
back to without engineering team approval and testing.
**Note:** The spacecraft is now operating on a single-string bus with no redundancy.
Any failure of the remaining bus is mission-critical.

## Verification Criteria
- [ ] `obdh.active_bus` (0x030E) reflects the new (redundant) bus
- [ ] All 6 subsystem HK reports received successfully
- [ ] No subsystem mode changes occurred during switchover
- [ ] All telemetry values are consistent and sensible
- [ ] `obdh.cpu_load` (0x0302) nominal (< 80%)
- [ ] Anomaly report filed with bus failure details

## Contingency
- If HK does not resume after switchover: The redundant bus may also be faulty. Check
  `obdh.bus_b_status` (or `obdh.bus_a_status` for the new bus). If both buses show
  ERROR, this is a critical failure. OBC can still communicate via TTC link. Escalate
  immediately to Flight Director and engineering team.
- If only some subsystems resume HK: The issue may be a partial bus failure or a
  subsystem-level fault. For subsystems still missing, investigate whether their bus
  interface has failed independently.
- If OBC does not respond to the bus select command: The OBC may be in a degraded state.
  Attempt `OBC_REBOOT` (func_id 42) as a last resort. If OBC is unresponsive, follow
  PROC-OBC-OFF-003 (OBC Redundancy Switchover).
- If a subsystem mode changed during switchover (e.g., AOCS went to safe mode): The
  brief bus interruption may have triggered a subsystem-level timeout. Restore the
  affected subsystem to its pre-switchover mode after verifying bus stability.
- If bus switchover causes continuous OBC reboots: OBC may be detecting bus errors on
  both buses. Command `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46) to stay in bootloader
  and prevent auto-reboot. Investigate from bootloader.
