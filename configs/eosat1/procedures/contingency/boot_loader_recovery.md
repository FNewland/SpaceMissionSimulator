# PROC-OBC-OFF-001: Boot Loader Recovery

**Category:** Contingency
**Position Lead:** FDIR / Systems
**Cross-Position:** (Flight Director for awareness)
**Difficulty:** Advanced

## Objective
Recover the OBC from a state where it is stuck in the bootloader (application software
has not loaded). This procedure diagnoses the cause, attempts to boot the application
image, and if the image is corrupt, inhibits auto-boot and initiates a memory check or
memory reload to restore the application.

## Prerequisites
- [ ] OBC detected in bootloader — `obdh.sw_image` (0x0311) = 0
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified
- [ ] Bootloader is responsive to commands (HK requests return data)

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.sw_image | 0x0311 | 0 (bootloader) — triggering condition |
| obdh.last_reboot_cause | 0x0316 | Record value (1=watchdog, 2=memory, 3=switchover, 4=commanded) |
| obdh.reboot_count | 0x030A | Record value |
| obdh.active_obc | 0x030C | Which OBC unit is active |
| obdh.mode | 0x0300 | Current mode (limited in bootloader) |
| obdh.temp | 0x0301 | OBC temperature within limits |
| obdh.cpu_load | 0x0302 | Record value |
| obdh.boot_count_a | 0x0317 | OBC-A total boot count |
| obdh.boot_count_b | 0x0318 | OBC-B total boot count |
| eps.bus_voltage | 0x0105 | > 27.0 V |
| ttc.link_status | 0x0501 | 1 (active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| OBC_BOOT_APP | 8 | 1 | 45 | Boot application software |
| OBC_BOOT_INHIBIT | 8 | 1 | 46 | Inhibit/allow auto-boot |
| MEM_CHECK | 6 | 9 | — | Check memory CRC |
| MEM_LOAD | 6 | 2 | — | Load data to on-board memory |
| OBC_SWITCH_UNIT | 8 | 1 | 43 | Switch to redundant OBC (last resort) |

## Procedure Steps

### Step 1: Detect Bootloader State
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 0 (bootloader, application not running)
**Verify:** `obdh.last_reboot_cause` (0x0316) — record the cause:
- 0 = none (unexpected — should not be in bootloader without cause)
- 1 = watchdog (application crashed, watchdog reset, bootloader did not auto-boot)
- 2 = memory (memory error detected, boot aborted)
- 3 = switchover (just switched OBC units)
- 4 = commanded (operator-initiated reboot)
**Verify:** `obdh.reboot_count` (0x030A) — record value
**Verify:** `obdh.active_obc` (0x030C) — record which unit is active
**Verify:** `obdh.temp` (0x0301) — within limits
**Note:** If `last_reboot_cause` = 2 (memory), the application image may be corrupt.
If cause = 1 (watchdog), the application may have crashed but the image could be intact.
**GO/NO-GO:** Bootloader state confirmed — proceed to diagnosis.

### Step 2: Check Last Reboot Cause and Assess
**Action:** Based on `obdh.last_reboot_cause`:

**If cause = 1 (watchdog) or cause = 4 (commanded):**
- Application image may be intact but crashed during execution.
- Proceed to Step 3 (attempt application boot).

**If cause = 2 (memory):**
- Application image may be corrupt.
- Proceed to Step 4 (memory check before boot attempt).

**If cause = 3 (switchover):**
- Normal condition after OBC switchover. Proceed to Step 3.

**If cause = 0 (none):**
- Unexpected bootloader state. Proceed cautiously to Step 4 (memory check).

**GO/NO-GO:** Assessment complete — follow appropriate path.

### Step 3: Attempt Application Boot
**Action:** Command application boot: `OBC_BOOT_APP` (func_id 55)
**Verify:** Wait 30 s for application to initialize.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running)
**Verify:** `obdh.sw_version` (0x030B) — correct application version
**Verify:** `obdh.mode` (0x0300) — OBC enters SAFE or NOMINAL
**Verify:** `obdh.cpu_load` (0x0302) < 80%
**Action:** If application booted successfully:
- Proceed to Step 7 (verification) to confirm all subsystems are nominal.
**Action:** If `obdh.sw_image` still = 0 after 30 s:
- Application failed to boot. Proceed to Step 4 (memory check).
**GO/NO-GO:** Application boot succeeded — skip to Step 7. Or boot failed — proceed to Step 4.

### Step 4: Enable Boot Inhibit and Check Memory
**Action:** Inhibit auto-boot to prevent boot loop: `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46)
**Verify:** OBC remains in bootloader (no automatic boot attempts)
**Action:** Perform memory CRC check on the application image region:
`MEM_CHECK(memory_id=1, address=0x00000000, length=0x00100000)` (Service 6, Subtype 9)
**Note:** Address and length values are for the application image stored in EEPROM.
Adjust based on the actual memory map if different.
**Verify:** Wait for memory check result report.
**Action:** Assess CRC result:
- If CRC matches expected value: Image is intact. The boot failure may be due to a
  transient error. Proceed to Step 5 (retry boot).
- If CRC does NOT match: Image is corrupt. Proceed to Step 6 (memory reload).
**GO/NO-GO:** Memory check complete — follow appropriate path.

### Step 5: Retry Application Boot After Memory Check
**Action:** Disable boot inhibit: `OBC_BOOT_INHIBIT(inhibit=0)` (func_id 46)
**Action:** Command application boot: `OBC_BOOT_APP` (func_id 55)
**Verify:** Wait 30 s for application to initialize.
**Action:** Request OBDH housekeeping: `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.sw_image` (0x0311) = 1 (application running)
**Verify:** `obdh.sw_version` (0x030B) — correct version
**Action:** If boot succeeds, proceed to Step 7. If boot fails again, proceed to Step 6.
**GO/NO-GO:** Boot succeeded — skip to Step 7. Or boot failed again — proceed to Step 6.

### Step 6: Attempt Memory Reload
**Action:** Ensure boot inhibit is active: `OBC_BOOT_INHIBIT(inhibit=1)` (func_id 46)
**Action:** Upload application software image to EEPROM via memory load commands:
`MEM_LOAD(memory_id=1, address=ADDR, data=BLOCK)` (Service 6, Subtype 2)
**Note:** This requires uploading the full application image in blocks via telecommand.
This is a lengthy process (may take multiple ground station contacts depending on
image size and uplink data rate).
**Action:** After upload complete, verify the image:
`MEM_CHECK(memory_id=1, address=0x00000000, length=0x00100000)` (Service 6, Subtype 9)
**Verify:** CRC matches the expected value for the uploaded image.
**Action:** If CRC matches, disable boot inhibit and boot:
`OBC_BOOT_INHIBIT(inhibit=0)` (func_id 46)
`OBC_BOOT_APP` (func_id 55)
**Verify:** `obdh.sw_image` (0x0311) = 1 within 30 s
**Action:** If boot succeeds, proceed to Step 7. If boot fails, proceed to Contingency.
**GO/NO-GO:** Memory reload and boot attempted — proceed to verification if successful.

### Step 7: Verify All Subsystems Nominal
**Action:** Request HK from all subsystems:
- `HK_REQUEST(sid=1)` — EPS
- `HK_REQUEST(sid=2)` — AOCS
- `HK_REQUEST(sid=3)` — TCS
- `HK_REQUEST(sid=5)` — TTC
- `HK_REQUEST(sid=6)` — Payload
**Verify:** All HK responses received.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V
**Verify:** `aocs.mode` (0x020F) — record and restore if needed
**Verify:** `payload.mode` (0x0600) — record and restore if needed
**Verify:** `obdh.mode` (0x0300) — if SAFE, restore to NOMINAL: `OBC_SET_MODE(mode=0)`
**Action:** Log recovery event with:
- Boot cause, number of boot attempts, memory check results
- Time spent in bootloader
- Application version confirmed
- Post-recovery subsystem states
**GO/NO-GO:** All subsystems nominal — recovery complete.

## Verification Criteria
- [ ] `obdh.sw_image` (0x0311) = 1 (application running)
- [ ] `obdh.sw_version` (0x030B) = correct application version
- [ ] `obdh.mode` (0x0300) = 0 (NOMINAL)
- [ ] All subsystem HK reports received and nominal
- [ ] `obdh.cpu_load` (0x0302) < 80%
- [ ] No subsequent unexpected reboots for at least 30 minutes
- [ ] Recovery event documented in anomaly log

## Contingency
- If application fails to boot after memory reload: The OBC hardware may be damaged.
  Consider switching to redundant OBC: `OBC_SWITCH_UNIT` (func_id 43) per
  PROC-OBC-OFF-003. The current OBC is non-recoverable from ground.
- If memory load fails (uplink errors): Verify TTC link quality. Retry the failed
  blocks. Consider using low data rate for more reliable uplink if errors persist.
- If OBC enters a boot loop (repeated reboots): Immediately command
  `OBC_BOOT_INHIBIT(inhibit=1)` to halt the cycle. Each reboot increments the reboot
  counter, and if it exceeds 4, FDIR may trigger spacecraft EMERGENCY mode.
- If bootloader itself becomes unresponsive: This is a critical failure — no software
  recovery is possible on this OBC unit. Switch to redundant OBC immediately.
- If memory check reveals corruption in the bootloader region: Do NOT attempt memory
  load to the bootloader region. Switch to redundant OBC. Bootloader corruption is
  not safely recoverable.
