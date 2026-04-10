# PROC-OBDH-OFF-002: OBC Bootloader Recovery

**Category:** Contingency
**Position Lead:** FDIR / Systems
**Cross-Position:** Flight Director
**Difficulty:** Advanced

## Objective
Recover the OBC when it is stuck in bootloader mode after a crash or unexpected
reboot. In bootloader mode, only the beacon HK structure (SID 11) is available,
and it is emitted from the dedicated bootloader APID (0x002) — distinct from the
application APID (0x001). No TM stores exist in bootloader mode; attitude,
thermal, and payload operations are all offline. Ground's first indication of
bootloader entry is that all application-APID traffic ceases and only a SID 11
beacon remains on the bootloader APID. The full application software is not running, meaning
no payload operations, no scheduled commands, and reduced FDIR capability. This
procedure verifies the bootloader state, assesses the reboot cause, attempts to
boot the application software, and if that fails, performs memory integrity checks
before considering a memory reload or OBC unit switchover.

## Prerequisites
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] OBC confirmed in bootloader — `obdh.sw_image` (0x0311) = 0
- [ ] Flight Director notified
- [ ] Bootloader is responding to HK requests (beacon SID 11 received)
- [ ] Previous `obdh.reboot_count` (0x030A) value known from last nominal pass

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.sw_image | 0x0311 | 0 (bootloader) — triggering condition |
| obdh.last_reboot_cause | 0x0316 | Record value (1=watchdog, 2=memory, 3=switchover, 4=commanded) |
| obdh.reboot_count | 0x030A | Record value — compare with last known |
| obdh.active_obc | 0x030C | Which OBC unit is active (0=A, 1=B) |
| obdh.mode | 0x0300 | Current mode (limited in bootloader) |
| obdh.temp | 0x0301 | OBC temperature within limits (< 50 C) |
| obdh.cpu_load | 0x0302 | Record value |
| obdh.boot_count_a | 0x0317 | OBC-A total boot count |
| obdh.boot_count_b | 0x0318 | OBC-B total boot count |
| obdh.bus_a_status | 0x030F | 0 (OK) or 1 (ERROR) |
| obdh.bus_b_status | 0x0310 | 0 (OK) or 1 (ERROR) |
| eps.bus_voltage | 0x0105 | > 27.0 V |
| ttc.link_status | 0x0501 | 1 (active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | -- | Request one-shot HK report |
| OBC_BOOT_APP | 8 | 1 | 45 | Boot application software from bootloader |
| OBC_BOOT_INHIBIT | 8 | 1 | 46 | Inhibit/allow auto-boot |
| MEM_CHECK | 6 | 9 | -- | Check memory CRC integrity |
| MEM_LOAD | 6 | 2 | -- | Load data to onboard memory |
| OBC_SWITCH_UNIT | 8 | 1 | 43 | Switch to redundant OBC (last resort) |
| OBC_REBOOT | 8 | 1 | 42 | Force OBC reboot |

## Procedure Steps

### Step 1: Confirm Bootloader State and Record Diagnostics
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Note:** In bootloader mode, only SID 11 (beacon) may respond. If SID 4 does not
return, request SID 11 instead.
**Verify:** `obdh.sw_image` (0x0311) = 0 (bootloader, application not running)
**Verify:** `obdh.last_reboot_cause` (0x0316) — record value:
- 0 = none (unexpected — should not be in bootloader without cause)
- 1 = watchdog (application crashed, watchdog triggered)
- 2 = memory (memory error detected, boot aborted)
- 3 = switchover (OBC unit switchover occurred)
- 4 = commanded (operator-initiated reboot)
**Verify:** `obdh.reboot_count` (0x030A) — record and compare with last known value
**Verify:** `obdh.active_obc` (0x030C) — record which OBC unit is active
**Verify:** `obdh.temp` (0x0301) — within limits (< 50 C)
**Verify:** `obdh.cpu_load` (0x0302) — record (should be low in bootloader)
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V — confirm power is stable
**Note:** If bus voltage is marginal (< 27.5 V), a brown-out may have caused the crash.
Address EPS before attempting application boot.
**GO/NO-GO:** Bootloader state confirmed, diagnostics recorded — proceed to assessment.

### Step 2: Assess Reboot Cause and Determine Recovery Path
**Action:** Based on `obdh.last_reboot_cause`, follow the appropriate path:

**If cause = 1 (watchdog) or cause = 4 (commanded):**
- Application image is likely intact but crashed during execution.
- Proceed to Step 3 (attempt application boot).

**If cause = 2 (memory):**
- Application image may be corrupt. Boot was aborted by bootloader CRC check.
- Proceed to Step 4 (memory integrity check before boot attempt).

**If cause = 3 (switchover):**
- Normal condition after OBC unit switchover. Image should be intact.
- Proceed to Step 3 (attempt application boot).

**If cause = 0 (none):**
- Unexpected state. Proceed cautiously to Step 4 (memory check first).

**Action:** Record assessment rationale in operations log.
**GO/NO-GO:** Assessment complete — follow appropriate path.

### Step 3: Attempt Application Boot
**Action:** Command application boot: `OBC_BOOT_APP` (func_id 55)
**Note:** The bootloader will attempt to load and start the application software.
This takes approximately 10-30 seconds.
**Verify:** Wait 30 s for application initialization.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running)
**Verify:** `obdh.sw_version` (0x030B) — correct application version
**Verify:** `obdh.mode` (0x0300) — OBC should enter SAFE or NOMINAL mode
**Verify:** `obdh.cpu_load` (0x0302) < 80% — normal post-boot load
**Action:** If application booted successfully:
- Proceed to Step 7 (full system verification).
**Action:** If `obdh.sw_image` still = 0 after 30 s:
- Application failed to boot. Proceed to Step 4 (memory integrity check).
**GO/NO-GO:** Boot succeeded — skip to Step 7. Boot failed — proceed to Step 4.

### Step 4: Inhibit Auto-Boot and Check Memory Integrity
**Action:** Inhibit auto-boot to prevent a boot loop:
`OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46)
**Verify:** OBC remains in bootloader (no automatic boot attempts).
**Action:** Perform memory CRC check on the application image region:
`MEM_CHECK(memory_id=1, address=0x00000000, length=0x00100000)` (Service 6, Subtype 9)
**Note:** Address and length refer to the application image in EEPROM. Adjust based
on the actual memory map.
**Verify:** Wait for memory check result report.
**Action:** Assess CRC result:
- **CRC matches expected:** Image is intact. Boot failure may be transient.
  Proceed to Step 5 (retry boot).
- **CRC does NOT match:** Image is corrupt. Proceed to Step 6 (memory reload).
**GO/NO-GO:** Memory check complete — follow appropriate path.

### Step 5: Retry Application Boot After Memory Verification
**Action:** Disable boot inhibit: `OBC_BOOT_INHIBIT(inhibit=0)` (func_id 46)
**Action:** Command application boot: `OBC_BOOT_APP` (func_id 55)
**Verify:** Wait 30 s for application initialization.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running)
**Verify:** `obdh.sw_version` (0x030B) — correct application version
**Action:** If boot succeeds: proceed to Step 7 (full system verification).
**Action:** If boot fails again: proceed to Step 6 (memory reload or OBC switch).
**GO/NO-GO:** Boot succeeded — skip to Step 7. Boot failed again — proceed to Step 6.

### Step 6: Escalate — Memory Reload or OBC Switchover
**Action:** Ensure boot inhibit is active: `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46)

**Option A — Memory Reload (if pass time permits):**
**Action:** Upload application software image via memory load commands:
`MEM_LOAD(memory_id=1, address=ADDR, data=BLOCK)` (Service 6, Subtype 2)
**Note:** Full image upload is a lengthy process and may require multiple contacts
depending on image size and uplink data rate.
**Action:** After upload, verify image: `MEM_CHECK` (Service 6, Subtype 9)
**Verify:** CRC matches expected value.
**Action:** Disable boot inhibit and boot:
- `OBC_BOOT_INHIBIT(inhibit=0)` (func_id 46)
- `OBC_BOOT_APP` (func_id 55)
**Verify:** `obdh.sw_image` (0x0311) = 1 within 30 s.
**Action:** If boot succeeds, proceed to Step 7.

**Option B — OBC Unit Switchover (if memory reload not feasible):**
**Action:** Command switchover to redundant OBC:
`OBC_SWITCH_UNIT` (func_id 43)
**CAUTION:** This is a critical command. The active OBC will power down and the
redundant unit will boot from its own EEPROM image.
**Verify:** Wait 60 s for redundant OBC to boot.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.active_obc` (0x030C) reflects the new unit
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running on backup)
**Action:** If backup OBC boots successfully, proceed to Step 7.

**GO/NO-GO:** Application restored via reload or switchover — proceed to Step 7.
If neither option succeeds, escalate to EMG-003.

### Step 7: Verify All Subsystems Nominal
**Action:** Request HK from all subsystems:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** All five HK responses received within 30 s.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V
**Verify:** `aocs.mode` (0x020F) — record and restore if needed
**Verify:** `payload.mode` (0x0600) — record and restore if needed
**Verify:** `obdh.mode` (0x0300) — if SAFE, consider restoring to NOMINAL:
`OBC_SET_MODE(mode=0)` (func_id 40)
**Action:** Verify onboard time against ground reference:
- If drift > 5 s, perform time synchronisation via `SET_TIME` (Service 9, Subtype 1)
**Action:** Log recovery event:
- Reboot cause and count
- Number of boot attempts required
- Memory check results (if performed)
- Time spent in bootloader
- Application version confirmed
- Post-recovery subsystem states
**GO/NO-GO:** All subsystems nominal, application running — recovery complete.

## Verification Criteria
- [ ] `obdh.sw_image` (0x0311) = 1 (application running)
- [ ] `obdh.sw_version` (0x030B) = correct application version
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE) as appropriate
- [ ] `obdh.cpu_load` (0x0302) < 80%
- [ ] All subsystem HK reports received and nominal
- [ ] `obdh.reboot_count` (0x030A) documented
- [ ] No subsequent unexpected reboots for at least 30 minutes
- [ ] Recovery event documented in anomaly log

## Contingency
- If OBC reboots again during application boot attempt: Immediately command
  `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46) to prevent boot loop. If
  `obdh.reboot_count` increments by > 3 within 1 hour, suspect boot loop.
  Escalate to EMG-003 (Emergency OBC Reboot).
- If bootloader itself becomes unresponsive (no HK at all): This is a critical
  failure. No software recovery is possible on this OBC unit. Switch to redundant
  OBC immediately: `OBC_SWITCH_UNIT` (func_id 43).
- If memory reload fails due to uplink errors: Verify TTC link quality. Retry
  failed blocks. Use low data rate for more reliable uplink:
  `TTC_SET_DATA_RATE(rate=0)` (func_id 52).
- If backup OBC also fails to boot application: Both application images may be
  corrupt. This requires a double memory reload — one per OBC unit. Coordinate with
  ground software team and plan multi-contact upload campaign.
- If AOCS reverted to DETUMBLE after OBC reboot: Execute CON-002 (AOCS Anomaly
  Recovery) to restore pointing after OBC is confirmed stable.
- If memory check reveals corruption in the bootloader region: Do NOT attempt to
  write to the bootloader region. Switch to redundant OBC. Bootloader corruption
  is not safely recoverable from ground.
