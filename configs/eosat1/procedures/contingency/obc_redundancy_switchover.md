# PROC-OBC-OFF-003: OBC Redundancy Switchover

**Category:** Contingency
**Position Lead:** FDIR / Systems
**Cross-Position:** Flight Director, all positions
**Difficulty:** Advanced

## Objective
Switch from the anomalous primary On-Board Computer (OBC-A) to the redundant unit
(OBC-B) when OBC-A exhibits persistent faults such as repeated watchdog reboots,
memory corruption, or unrecoverable software errors. This procedure commands the unit
switchover, verifies boot to the bootloader, confirms the application software image
loads correctly, and verifies all subsystems return to nominal operation.

## Prerequisites
- [ ] OBC-A anomaly confirmed — repeated reboots, memory errors, or unresponsive behavior
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director authorizes OBC switchover (critical command)
- [ ] Backup OBC (OBC-B) in STANDBY — `obdh.obc_b_status` (0x030D) = 1
- [ ] All position operators briefed on impending OBC switchover

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.mode | 0x0300 | Current OBC mode |
| obdh.active_obc | 0x030C | 0 (OBC-A) before switchover |
| obdh.obc_b_status | 0x030D | 1 (STANDBY) — redundant unit ready |
| obdh.sw_image | 0x0311 | 0 (bootloader) after switchover, then 1 (application) |
| obdh.reboot_count | 0x030A | Record before and after |
| obdh.last_reboot_cause | 0x0316 | 3 (switchover) expected after transition |
| obdh.sw_version | 0x030B | Correct application version after boot |
| obdh.cpu_load | 0x0302 | < 80% after stabilization |
| obdh.temp | 0x0301 | Within limits |
| eps.bus_voltage | 0x0105 | > 27.0 V |
| aocs.mode | 0x020F | Verify post-switchover |
| payload.mode | 0x0600 | Verify post-switchover |
| ttc.link_status | 0x0501 | 1 (active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| OBC_SWITCH_UNIT | 8 | 1 | 43 | Switch to redundant OBC (critical) |
| OBC_BOOT_APP | 8 | 1 | 45 | Boot application software from bootloader |
| OBC_SET_MODE | 8 | 1 | 40 | Set OBC mode |

## Procedure Steps

### Step 1: Verify OBC-A Anomaly
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) — record current mode
**Verify:** `obdh.reboot_count` (0x030A) — record value (elevated if watchdog reboots)
**Verify:** `obdh.last_reboot_cause` (0x0316) — record cause:
- 0 = none, 1 = watchdog, 2 = memory, 3 = switchover, 4 = commanded
**Verify:** `obdh.cpu_load` (0x0302) — record value (may be anomalously high)
**Verify:** `obdh.active_obc` (0x030C) = 0 (OBC-A is active)
**Verify:** `obdh.obc_b_status` (0x030D) = 1 (OBC-B is in STANDBY and available)
**Action:** Confirm that the anomaly is persistent and not recoverable on OBC-A.
If `obdh.reboot_count` > 4, FDIR may have already triggered safe mode.
**GO/NO-GO:** OBC-A anomaly confirmed, OBC-B in STANDBY — proceed with switchover.

### Step 2: Command OBC Switchover
**Action:** Announce "COMMANDING OBC SWITCHOVER" on the operations loop.
**Action:** All position operators prepare for temporary telemetry loss.
**Action:** Command OBC switch: `OBC_SWITCH_UNIT` (func_id 43)
**Note:** CRITICAL COMMAND — This will:
1. Halt OBC-A operations
2. Power on OBC-B processor
3. OBC-B will boot to its bootloader
4. All subsystem states will be preserved by the subsystem units themselves
5. Expect 30-60 s of no telemetry during transition
**Verify:** Wait 30 s for initial boot.
**GO/NO-GO:** Command sent — wait for bootloader confirmation.

### Step 3: Verify Boot to Bootloader
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Note:** If no response within 15 s, retry the request. OBC-B may still be in early boot.
**Verify:** `obdh.active_obc` (0x030C) = 1 (OBC-B is now active)
**Verify:** `obdh.sw_image` (0x0311) = 0 (bootloader — not yet running application)
**Verify:** `obdh.last_reboot_cause` (0x0316) = 3 (switchover)
**Verify:** `obdh.temp` (0x0301) — OBC-B temperature within limits
**Verify:** `ttc.link_status` (0x0501) = 1 — TTC link maintained
**GO/NO-GO:** OBC-B in bootloader — proceed to boot application.

### Step 4: Verify Software Image and Boot Application
**Action:** Verify the application software image is valid before booting:
`HK_REQUEST(sid=4)` — check `obdh.sw_version` (0x030B) is available in bootloader
context (may report bootloader version).
**Action:** Command application boot: `OBC_BOOT_APP` (func_id 55)
**Verify:** Wait 30 s for application to initialize.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running)
**Verify:** `obdh.sw_version` (0x030B) — correct application software version
**Verify:** `obdh.mode` (0x0300) — OBC should enter SAFE or NOMINAL mode
**Verify:** `obdh.cpu_load` (0x0302) < 80% (no anomalous load on new unit)
**GO/NO-GO:** Application booted successfully on OBC-B — proceed to subsystem verification.

### Step 5: Verify All Subsystems Nominal
**Action:** Request HK from all subsystems:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** All HK responses received within 15 s.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V
**Verify:** `eps.bat_soc` (0x0101) — record value
**Verify:** `aocs.mode` (0x020F) — record mode. AOCS may have reverted to SAFE_POINT
during the switchover. If so, restore to nominal mode after confirming stability.
**Verify:** `payload.mode` (0x0600) — record mode. Payload may have gone to OFF during
switchover. Restore to STANDBY after confirming OBC stability.
**Verify:** `ttc.link_status` (0x0501) = 1
**Action:** If `obdh.mode` (0x0300) = 1 (SAFE), restore to NOMINAL when all subsystems
are confirmed: `OBC_SET_MODE(mode=0)` (func_id 40)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10 s
**GO/NO-GO:** All subsystems nominal on OBC-B — switchover complete.

### Step 6: Post-Switchover Documentation
**Action:** Log the OBC switchover event:
- Time of switchover command
- OBC-A anomaly symptoms and reboot count
- OBC-B boot time and application version
- All subsystem states post-switchover
- Any subsystems that required mode restoration
**Action:** Clear reboot counter if desired: `OBC_CLEAR_REBOOT_CNT` (func_id 47)
**Action:** Notify Flight Director: "OBC switchover complete. Operating on OBC-B.
OBC-A is offline and requires engineering assessment before reuse."
**Note:** The spacecraft is now operating on the redundant OBC with no further
OBC redundancy. Any failure of OBC-B is mission-critical.

## Verification Criteria
- [ ] `obdh.active_obc` (0x030C) = 1 (OBC-B active)
- [ ] `obdh.sw_image` (0x0311) = 1 (application running)
- [ ] `obdh.sw_version` (0x030B) = correct version
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL)
- [ ] All subsystem HK reports received and nominal
- [ ] No anomalous reboot events on OBC-B
- [ ] Anomaly report filed for OBC-A failure

## Contingency
- If OBC-B does not respond after switchover (no HK within 60 s): The switchover may
  have failed or OBC-B has a fault. Wait up to 120 s. If still no response, OBC-B may
  be in a boot loop. Try `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46) to stop
  auto-boot cycling, then `OBC_BOOT_APP` manually.
- If `obdh.sw_image` remains 0 (stuck in bootloader): Application image may be corrupt.
  Follow PROC-OBC-OFF-001 (Boot Loader Recovery) for memory check and recovery.
- If OBC-B boots but immediately enters repeated reboots: Command `OBC_BOOT_INHIBIT
  (inhibit=1)` to stay in bootloader. Investigate from bootloader. May need to upload
  fresh application image via `MEM_LOAD`.
- If subsystems do not resume HK after OBC-B boots: CAN bus may also be affected.
  Follow PROC-OBC-OFF-002 (CAN Bus Failure Switchover) to check and switch bus.
- If it is not possible to switch to OBC-B (command rejected or `obc_b_status` = 0):
  OBC-B may not be in STANDBY or may have a hardware fault. Attempt to recover OBC-A
  using `OBC_REBOOT` (func_id 42). If OBC-A also cannot recover, this is a
  mission-critical emergency — escalate immediately.
