# PROC-OBDH-OFF-003: CAN Bus Failure Detection and Isolation

**Category:** Contingency
**Position Lead:** FDIR / Systems
**Cross-Position:** Flight Director, all positions
**Difficulty:** Advanced

## Objective
Detect, diagnose, and isolate a CAN bus failure that results in loss of housekeeping
telemetry from one or more subsystems. When the active CAN bus fails, the OBC can no
longer communicate with subsystem electronics connected via that bus. This procedure
identifies the failed bus, commands a switchover to the redundant bus, verifies that
all subsystem communications are restored, and assesses the operational implications
of operating on a single-string bus.

## Prerequisites
- [ ] Housekeeping data missing from one or more subsystems for > 30 s
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] OBC is responsive to commands — `obdh.mode` (0x0300) can be queried
- [ ] Flight Director notified and authorizes bus investigation
- [ ] OBC application software is running — `obdh.sw_image` (0x0311) = 1

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.bus_a_status | 0x030F | 0 (OK) or 1 (ERROR) |
| obdh.bus_b_status | 0x0310 | 0 (OK) or 1 (ERROR) |
| obdh.active_bus | 0x030E | 0 (Bus A) or 1 (Bus B) |
| obdh.mode | 0x0300 | Current OBC mode |
| obdh.cpu_load | 0x0302 | Monitor for anomalous increase |
| obdh.temp | 0x0301 | OBC temperature within limits |
| eps.bus_voltage | 0x0105 | > 27.0 V |
| eps.bat_soc | 0x0101 | Record current value |
| aocs.mode | 0x020F | Verify after switchover |
| payload.mode | 0x0600 | Verify after switchover |
| ttc.link_status | 0x0501 | 1 (active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | -- | Request one-shot HK report |
| OBC_SELECT_BUS | 8 | 1 | 44 | Select active CAN bus (0=A, 1=B) |
| OBC_REBOOT | 8 | 1 | 42 | Force OBC reboot (last resort) |

## Procedure Steps

### Step 1: Detect and Characterize Missing Housekeeping
**Action:** Request individual HK reports from each subsystem to determine which
are affected:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=4)` — OBDH
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** Record which SID responses are received and which are missing within 15 s.
**Action:** Categorize the failure pattern:
- **OBDH responds but other subsystems missing:** CAN bus connecting external
  subsystems has likely failed. Proceed to Step 2.
- **All subsystems missing including OBDH:** Issue may be OBC or TTC related,
  not bus related. Investigate OBC state first.
- **Only one subsystem missing:** May be a subsystem-level failure, not bus failure.
  Investigate that specific subsystem before bus switchover.
- **Multiple (but not all) subsystems missing:** Could be partial bus failure.
  Proceed to Step 2 for bus diagnosis.
**GO/NO-GO:** Missing HK confirmed from multiple subsystems — proceed to bus diagnosis.

### Step 2: Diagnose Bus Status
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.active_bus` (0x030E) — record which bus is currently active:
- 0 = Bus A
- 1 = Bus B
**Verify:** `obdh.bus_a_status` (0x030F) — record status:
- 0 = OK
- 1 = ERROR
**Verify:** `obdh.bus_b_status` (0x0310) — record status
**Verify:** `obdh.mode` (0x0300) — confirm OBC has not entered SAFE mode due to bus error
**Verify:** `obdh.cpu_load` (0x0302) — elevated load may indicate bus timeout retries
**Verify:** `obdh.temp` (0x0301) — within limits

**Action:** Assess the fault:
- **Active bus shows ERROR:** Bus failure confirmed. Proceed to Step 3 (switchover).
- **Active bus shows OK but HK is missing:** The bus status register may not have
  updated yet, or the issue is at the subsystem level. Wait 15 s and re-query.
  If status still OK but HK missing, proceed cautiously to Step 3.
- **Both buses show ERROR:** Critical situation. Both buses may be degraded.
  Proceed to Step 3 but expect partial recovery at best.

**Action:** Record pre-switchover state:
- Active bus and status
- Standby bus status
- OBC mode and CPU load
- EPS voltage (if available from last known frame)
**GO/NO-GO:** Active bus confirmed in ERROR state (or HK loss confirmed with bus
as likely cause) — proceed to switchover.

### Step 3: Command Bus Switchover
**Action:** Notify all console positions: "CAN bus switchover imminent. Expect
brief HK gap of 5-10 seconds."
**Action:** Command switchover to the redundant bus:
- If currently on Bus A (0x030E = 0):
  `OBC_SELECT_BUS(bus=1)` (func_id 44) — switch to Bus B
- If currently on Bus B (0x030E = 1):
  `OBC_SELECT_BUS(bus=0)` (func_id 44) — switch to Bus A
**CAUTION:** This command will briefly interrupt all CAN bus communications during
the switchover. Subsystem HK will be unavailable for 5-10 seconds. This is normal.
**Verify:** Wait 15 s for switchover to complete.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.active_bus` (0x030E) reflects the new bus
**Verify:** `obdh.bus_a_status` (0x030F) — record updated status
**Verify:** `obdh.bus_b_status` (0x0310) — record updated status
**GO/NO-GO:** Bus switchover confirmed — proceed to verification.

### Step 4: Verify All Housekeeping Resumes on New Bus
**Action:** Request HK from all subsystems:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=4)` — OBDH
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** All six SID responses received within 15 s.
**Verify:** Telemetry values are consistent and sensible:
- No stuck values or unexpected zeros
- Values are consistent with pre-failure readings
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V — power stable
**Verify:** `eps.bat_soc` (0x0101) — unchanged from pre-switchover
**Verify:** `aocs.mode` (0x020F) — AOCS mode unchanged from pre-failure state
**Verify:** `payload.mode` (0x0600) — payload mode unchanged
**Verify:** `obdh.cpu_load` (0x0302) — not anomalously high (< 80%)
**Action:** Announce on operations loop: "ALL HK RESTORED on [Bus A/B]. Bus switchover
successful."
**GO/NO-GO:** All subsystem HK received and nominal — proceed to post-switchover assessment.

### Step 5: Post-Switchover Assessment and Documentation
**Action:** Log the bus failure event with the following details:
- Time of first HK loss detection
- Which bus failed (A or B)
- Duration of HK gap (from detection to restoration)
- Which subsystems were affected
- New active bus identifier
- Any subsystem mode changes that occurred during the gap
**Action:** Verify the failed bus error status is recorded for engineering assessment.
**Action:** Assess the operational impact:
- **Single-string bus:** The spacecraft is now operating on one bus with no CAN
  redundancy. Any failure of the remaining bus would be mission-critical.
- **FDIR implications:** Review whether onboard FDIR rules reference bus status.
  Confirm FDIR will not attempt to switch back to the failed bus.
**Action:** Notify Flight Director of reduced redundancy status.
**Action:** Continue operations on the new bus. The failed bus MUST NOT be switched
back to without engineering team approval and analysis.
**GO/NO-GO:** Assessment complete, incident documented — procedure complete.

## Verification Criteria
- [ ] `obdh.active_bus` (0x030E) reflects the new (redundant) bus
- [ ] Failed bus status is ERROR: `obdh.bus_a_status` (0x030F) or `obdh.bus_b_status` (0x0310) = 1
- [ ] All 6 subsystem HK reports received successfully on new bus
- [ ] No subsystem mode changes occurred during switchover
- [ ] All telemetry values are consistent with pre-failure values
- [ ] `obdh.cpu_load` (0x0302) < 80%
- [ ] `eps.bus_voltage` (0x0105) > 27.0 V
- [ ] Anomaly report filed with bus failure details
- [ ] Flight Director informed of reduced bus redundancy

## Contingency
- If HK does not resume after switchover: The redundant bus may also be faulty.
  Check the newly active bus status register. If both buses show ERROR, this is
  a critical dual-bus failure. OBC can still communicate via TTC link but cannot
  reach subsystems. Escalate immediately to Flight Director and engineering team.
- If only some subsystems resume HK after switchover: A partial bus failure or
  individual subsystem bus interface failure may exist. For subsystems still
  missing, the issue is subsystem-level, not bus-level. Investigate each
  individually.
- If OBC does not respond to the bus select command: OBC may be in a degraded
  state. Attempt `OBC_REBOOT` (func_id 42) as a last resort. If OBC is
  unresponsive, follow PROC-OBC-OFF-003 (OBC Redundancy Switchover).
- If a subsystem mode changed during switchover (e.g., AOCS went to SAFE or
  DETUMBLE): The brief bus interruption may have triggered a subsystem timeout.
  After confirming bus stability, restore the affected subsystem to its
  pre-switchover mode using the appropriate mode command.
- If bus switchover triggers continuous OBC reboots: The OBC may be detecting
  errors on both buses. Command `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46) to
  stay in bootloader and stop the reboot cycle. Investigate from bootloader.
- If the failed bus recovers on its own (intermittent failure): Do NOT switch
  back without engineering analysis. Intermittent bus failures indicate a
  marginal hardware condition that may fail permanently at any time.
