# PROC-EMG-003: Emergency OBC Reboot
**Subsystem:** OBDH
**Phase:** EMERGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Emergency commanded reboot of the On-Board Computer when the OBC is non-responsive,
exhibiting degraded behaviour (high CPU load, memory errors, command rejections), or stuck
in an anomalous state. This procedure resets the OBC to the boot loader, which then
restarts the flight software from the designated safe memory bank. The reboot clears all
volatile state, resets internal counters and buffers, and re-initialises all subsystem
communication interfaces. This is a last-resort recovery action that interrupts all
onboard operations, so it requires Flight Director authorisation and confirmation that
all non-critical operations have been suspended.

## Prerequisites
- [ ] OBC anomaly confirmed by at least two independent indicators:
  - `obdh.cpu_load` (0x0302) > 90 % sustained, OR
  - `obdh.tc_rej_count` (0x0306) incrementing on valid commands, OR
  - `obdh.mem_errors` (0x031E) > 0 and increasing, OR
  - `obdh.mode` (0x0300) not responding to mode change commands, OR
  - Housekeeping telemetry stale (no new packets for > 30 s)
- [ ] Flight Director authorisation obtained (signed on console log)
- [ ] All non-critical operations suspended:
  - Payload OFF: `payload.mode` (0x0600) = 0
  - No time-tagged command sequences pending
  - No memory upload in progress
- [ ] TTC link active: `ttc.link_status` (0x0501) = 1 (LOCKED)
- [ ] Battery SoC > 30 %: `eps.bat_soc` (0x0101) > 30 (margin for boot sequence)
- [ ] Flight Director and FDIR/Systems operators at console

## Procedure Steps

### Step 1 --- Pre-Reboot State Capture
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping (if responsive)
**Action:** Record all available OBDH parameters for post-reboot comparison:
  - `obdh.mode` (0x0300): current mode
  - `obdh.cpu_load` (0x0302): CPU utilisation
  - `obdh.mmm_used` (0x0303): memory usage
  - `obdh.reboot_count` (0x030A): current reboot counter
  - `obdh.sw_version` (0x030B): running software version
  - `obdh.active_obc` (0x030C): active OBC unit (A=0, B=1)
  - `obdh.sw_image` (0x0311): running image (0=bootloader, 1=application)
  - `obdh.last_reboot_cause` (0x0316): previous reboot cause
  - `obdh.seu_count` (0x0319): single-event upset counter
  - `obdh.mem_errors` (0x031E): memory error count
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- request EPS housekeeping
**Verify:** `eps.bat_soc` (0x0101) > 30 %
**Note:** If OBC is completely unresponsive (no HK received), record "NO RESPONSE" and
proceed directly to Step 2. The reboot command may still be accepted at the hardware
command decoder level even if the application software is hung.
**GO/NO-GO:** Pre-reboot state captured (or confirmed unresponsive). Flight Director approves reboot.

### Step 2 --- Suspend Payload and Shed Non-Critical Loads
**TC:** `PAYLOAD_SET_MODE` mode=0 (Service 8, Subtype 5) --- payload OFF
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10 s (if OBC responsive)
**TC:** `HEATER_CONTROL(circuit=obc, on=false)` (Service 8, Subtype 7) --- OBC heater OFF
**Note:** The OBC heater will be re-enabled after reboot once thermal state is assessed.
**Action:** If OBC is not accepting commands, these loads will remain in their current
state through the reboot. The boot loader will apply safe defaults on restart.
**GO/NO-GO:** Non-critical loads shed (or OBC confirmed non-responsive).

### Step 3 --- Send Commanded Reboot
**TC:** `FUNC_PERFORM` func_id=42 (Service 8, Subtype 1) --- command OBC reboot
**Action:** The OBC will immediately halt the running application, reset the processor,
and enter the boot loader. All volatile memory is cleared. The boot loader will perform
a power-on self-test (POST) and then load the application image from the designated
safe memory bank.
**Note:** Telemetry will cease immediately upon reboot command acceptance. This is expected.
**Verify:** Command acceptance telemetry (Service 1, Subtype 1) received --- this may be
the last packet before the OBC goes offline.
**Note:** If the OBC is hung and does not acknowledge the command, the hardware watchdog
timer (typically 120 s) may trigger an autonomous reboot. In this case, the reboot will
occur without explicit acknowledgement.
**GO/NO-GO:** Reboot command sent. Begin boot sequence monitoring.

### Step 4 --- Wait for Boot Sequence
**Action:** Wait 60 seconds for the OBC to complete the boot sequence:
  - 0--10 s: Processor reset, POST, memory self-test
  - 10--30 s: Boot loader loads application image from safe memory bank
  - 30--50 s: Application initialisation, subsystem interface bring-up
  - 50--60 s: HK telemetry service restarts, first packets generated
**Monitor:** TTC receiver for any telemetry downlink resumption.
**Timeout:** If no telemetry received within 90 s, extend wait to 120 s.
**Critical:** If no telemetry received within 120 s, the OBC may be stuck in boot loader
or may have failed to boot entirely. Proceed to Recovery Action 1.

### Step 4b --- Boot OBC into Application Image
**TC:** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55)
**Action:** After reboot the OBC comes up in the bootloader (`obdh.sw_image` = 0) and only accepts the bootloader-allowed command set (boot/maintenance funcs + EPS power-line on/off). Issue OBC_BOOT_APP to start the 10 s CRC verification of the application image. Until this completes, every application TC below (HK_ENABLE, OBC_SET_MODE, etc.) will be rejected with error 0x0004.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 15 s
**Verify:** `obdh.sw_image` (0x0311) = 1 (APPLICATION) within 15 s
**GO/NO-GO:** Application image running, full PUS service set available.

### Step 5 --- Verify OBC Heartbeat and Mode
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) = 1 (SAFE) within 10 s --- OBC boots into SAFE mode after reboot
**Verify:** `obdh.sw_image` (0x0311) = 1 (application) --- running from application image, not bootloader
**Verify:** `obdh.reboot_count` (0x030A) = previous_count + 1 --- exactly one reboot occurred
**Verify:** `obdh.last_reboot_cause` (0x0316) = 4 (commanded) --- reboot was from our command
**Verify:** `obdh.sw_version` (0x030B) = expected version --- correct software loaded
**Verify:** `obdh.cpu_load` (0x0302) < 50 % --- CPU no longer overloaded
**Verify:** `obdh.mem_errors` (0x031E) = 0 --- memory errors cleared by reboot
**GO/NO-GO:** OBC alive, in SAFE mode, running correct application. Heartbeat confirmed.

### Step 6 --- Check Boot Source and Active OBC
**Verify:** `obdh.active_obc` (0x030C) --- confirm same OBC unit as before reboot (A=0, B=1)
**Verify:** `obdh.active_bus` (0x030E) --- confirm CAN bus assignment (primary=0)
**Verify:** `obdh.bus_a_status` (0x030F) = 0 (OK) --- CAN bus A operational
**Verify:** `obdh.bus_b_status` (0x0310) = 0 (OK) --- CAN bus B operational
**Verify:** `obdh.obc_b_status` (0x030D) --- record backup OBC status (0=OFF, 1=STANDBY)
**Note:** If the OBC has switched to the backup unit (active_obc changed), an autonomous
failover may have occurred. This indicates a more serious hardware fault on the primary
unit. Escalate to OBDH engineer for investigation.
**GO/NO-GO:** Boot source and OBC identity confirmed. No unexpected switchover.

### Step 7 --- Re-Enable HK Reporting and Verify Subsystem Communication
**TC:** `HK_ENABLE` SID=1 (Service 3, Subtype 5) --- enable EPS periodic reporting
**TC:** `HK_ENABLE` SID=2 (Service 3, Subtype 5) --- enable AOCS periodic reporting
**TC:** `HK_ENABLE` SID=3 (Service 3, Subtype 5) --- enable TCS periodic reporting
**TC:** `HK_ENABLE` SID=5 (Service 3, Subtype 5) --- enable TTC periodic reporting
**Verify:** Periodic HK packets from all enabled SIDs arriving within 30 s
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- request EPS housekeeping
**Verify:** `eps.bus_voltage` (0x0105) > 28.0 V --- power bus nominal
**Verify:** `eps.bat_soc` (0x0101) > 25 % --- sufficient charge
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- request AOCS housekeeping
**Verify:** `aocs.mode` (0x020F) --- record current AOCS mode (may have defaulted to SAFE_POINT or DETUMBLE)
**Verify:** `aocs.att_error` (0x0217) --- record current attitude error
**TC:** `HK_REQUEST` SID=3 (Service 3, Subtype 25) --- request TCS housekeeping
**Verify:** `tcs.temp_obc` (0x0406) in range [-10 C, +50 C] --- OBC temperature within limits
**Verify:** `tcs.temp_battery` (0x0407) in range [-10 C, +45 C]
**GO/NO-GO:** All subsystems responding via CAN bus. Telemetry nominal.

### Step 8 --- Restore Operational Mode and Log Results
**TC:** `OBC_SET_MODE` mode=0 (Service 8, Subtype 3) --- transition OBC to NOMINAL mode
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 5 s
**TC:** `HEATER_CONTROL(circuit=obc, on=true)` (Service 8, Subtype 7) --- restore OBC heater
**Verify:** `tcs.htr_obc` (0x040B) = 1 (active)
**Action:** Log reboot results:
  - Reboot cause (anomaly description)
  - Pre-reboot state: CPU load, memory errors, mode, SEU count
  - Boot duration (time from reboot command to first HK packet)
  - Post-reboot state: mode, CPU load, memory errors, sw_version
  - Subsystem communication status
  - Any unexpected behaviour during boot
**Action:** If the original anomaly was caused by a software defect, schedule a software
investigation and potential patch upload (PROC-NOM-005).
**Action:** If the anomaly was caused by a single-event upset (SEU), log the SEU count
and monitor for recurrence.
**GO/NO-GO:** OBC in NOMINAL mode. Reboot recovery complete.

## Recovery Actions

### Recovery Action 1 --- OBC Fails to Boot (No Telemetry After 120 s)
1. Verify TTC link is still active: `ttc.link_status` (0x0501) should still be LOCKED
   (TTC operates independently of OBC boot state).
2. Wait one additional orbit (95 min) --- the hardware watchdog may trigger a second
   reboot attempt.
3. If still no response: the OBC may be stuck in bootloader or hardware fault.
4. Attempt OBC redundancy switch:
   **TC:** `FUNC_PERFORM` func_id=43 (Service 8, Subtype 1) --- switch to backup OBC
   (This command is routed via the EPS power distribution unit, which is independent of
   the OBC application software.)
5. Wait 90 s for backup OBC to boot.
6. If backup OBC responds: continue from Step 5 with the backup unit active.
7. If neither OBC responds: escalate to emergency team. Spacecraft is in autonomous mode
   relying on FDIR hardware.

### Recovery Action 2 --- OBC Boots Into Bootloader Only
1. If `obdh.sw_image` (0x0311) = 0 (bootloader): the application image may be corrupted.
2. **TC:** `FUNC_PERFORM` func_id=21 (Service 8, Subtype 1) --- select alternate memory bank.
3. **TC:** `FUNC_PERFORM` func_id=42 (Service 8, Subtype 1) --- reboot from alternate bank.
4. Wait 90 s and verify `obdh.sw_image` (0x0311) = 1 (application).
5. If successful: the primary image bank is likely corrupted. Schedule a software re-upload
   to the failed bank via PROC-NOM-005 when conditions permit.
6. If both banks fail: the OBC will remain in bootloader. Basic commanding is available
   but housekeeping and autonomy are limited. Escalate for anomaly resolution.

## Off-Nominal Handling
- If `obdh.reboot_count` (0x030A) increments by > 1: The OBC may have experienced
  multiple reboot cycles (boot loop). Check `obdh.last_reboot_cause` (0x0316). If cause
  is 1 (watchdog), the application may be crashing on startup. Attempt bootloader-only
  mode and select alternate image bank.
- If `obdh.active_obc` (0x030C) changed unexpectedly: An autonomous OBC switchover
  occurred. The primary OBC may have a hardware fault. Log the event and proceed with
  the backup unit. Schedule investigation of the primary unit.
- If AOCS is in DETUMBLE after reboot: The AOCS has defaulted to safe mode. This is
  expected behaviour. Monitor rates and transition to SAFE_POINT then NADIR_POINT once
  rates are < 0.1 deg/s.
- If `eps.bat_soc` drops below 20 % after reboot: The boot sequence power draw may have
  been excessive. Shed all non-essential loads immediately. Maintain OBC in SAFE mode
  until SoC recovers above 30 %.
- If periodic anomaly recurs after reboot: The root cause may be a hardware fault (memory
  bit-flip, processor degradation) rather than a software issue. Consider switching to the
  backup OBC unit as a longer-term solution.

## Post-Conditions
- [ ] OBC rebooted and running in NOMINAL mode: `obdh.mode` (0x0300) = 0
- [ ] Correct software version confirmed: `obdh.sw_version` (0x030B) matches expected
- [ ] Running from application image: `obdh.sw_image` (0x0311) = 1
- [ ] `obdh.cpu_load` (0x0302) < 50 % (anomaly resolved)
- [ ] `obdh.mem_errors` (0x031E) = 0
- [ ] All subsystem communication restored (EPS, AOCS, TCS, TTC, Payload responding)
- [ ] HK periodic reporting re-enabled for all required SIDs
- [ ] OBC heater restored: `tcs.htr_obc` (0x040B) = 1
- [ ] `obdh.reboot_count` (0x030A) = previous_count + 1 (single reboot, no boot loop)
- [ ] Anomaly report filed with pre/post reboot data
- [ ] Root cause investigation initiated
- [ ] Flight Director has confirmed recovery and authorised return to nominal operations

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*
