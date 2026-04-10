# LEOP-001: First Acquisition of Signal & OBC Boot

**Subsystem:** TT&C / OBDH / Ground Segment
**Phase:** LEOP
**Revision:** 2.0 — rewritten for cold-boot initial state
**Approved:** Flight Operations Director

## Purpose
Establish first command path with EOSAT-1 following separation, exit the
OBDH bootloader into the application image, and synchronise on-board time.
At cold boot the spacecraft is dark on the downlink: only the receiver
(`ttc_rx`) and OBC (`obc`) lines are energised. The transmitter
(`ttc_tx`), all heaters, AOCS wheels, payload and FPA cooler lines are
OFF, and the OBC is running the bootloader image. This procedure is what
turns the spacecraft from a one-way listener into a fully bidirectional,
application-running platform.

## Prerequisites
- [ ] Launcher separation confirmed (launch provider telemetry)
- [ ] Svalbard ground station antenna configured for EOSAT-1 VHF/UHF receive frequency
- [ ] Predicted AOS/LOS timeline loaded into ground station schedule
- [ ] Flight Dynamics have provided initial TLE from launch provider
- [ ] MCS database loaded with EOSAT-1 TM/TC definitions
- [ ] All console positions staffed and nominal

## Procedure Steps

### Step 1 — Configure Ground Station Receiver and Force Pass Override
**Action:** Command Svalbard ground station to point at predicted AOS azimuth/elevation. Enable auto-tracking on VHF/UHF downlink frequency. Set receiver bandwidth to wide acquisition mode (+/- 40 kHz Doppler window).
**Action:** In the MCS, enable **Pass Override** so the simulator treats the link as in contact regardless of orbital geometry. Cold-boot has `ttc_tx` OFF, so there is no carrier to acquire passively — the override gives the operator the assured uplink path the spacecraft autonomy is designed around.
**Verify:** Ground station status = TRACKING_READY
**Verify:** MCS reports `pass_override = 1` (param 0x05FF)
**GO/NO-GO:** Ground station ready, override engaged

### Step 2 — Send First Uplink (Connection Test)
**TC:** `CONNECTION_TEST` (Service 17, Subtype 1)
**Action:** Service 17 ping is on the bootloader allow-list, so it is accepted by the OBC even before the application image is loaded. On acceptance, the platform's **auto-TX hold-down** kicks in: any accepted TC autonomously powers `ttc_tx` ON for 15 minutes, energising the downlink without further commanding.
**Verify:** S1.1 (Service 1, Subtype 1) acceptance received within 10s
**Verify:** S17.2 connection test report received within 10s
**Verify:** `eps.power_lines["ttc_tx"]` = 1 (auto-armed)
**GO/NO-GO:** Bidirectional link established, downlink visible to operator

### Step 3 — Verify Carrier Lock and Link Quality
**Action:** Confirm ground station receiver has achieved carrier lock. Record measured Doppler offset for Flight Dynamics.
**Verify:** `ttc.link_status` (0x0501) = 1 (LOCKED) within 30s
**Verify:** `ttc.rssi` (0x0502) > -110 dBm within 30s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**GO/NO-GO:** Stable carrier lock with positive link margin

### Step 4 — Confirm OBC is in Bootloader
**TC:** `HK_REQUEST(sid=11)` (Service 3, Subtype 27) — Beacon SID
**Verify:** Beacon packet received (the bootloader only emits SID 11)
**Verify:** `obdh.sw_image` (0x0303) = 0 (BOOTLOADER) within 10s
**Note:** SIDs 1–6 are not yet emitted: under cold boot the AOCS / payload / TTC lines are not all hot, so the periodic HK power gate suppresses them. This is expected.
**GO/NO-GO:** Bootloader confirmed; ready for application boot

### Step 5 — Boot OBC into Application Image
**TC:** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55)
**Action:** Issue the boot-app command. The OBDH model verifies the application CRC over a 10s window then transitions `sw_image` to APPLICATION (1). Until that transition completes, only the bootloader command set is accepted.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `obdh.sw_image` (0x0303) = 1 (APPLICATION) within 15s
**Verify:** Periodic HK SIDs 1, 3, 4, 6 begin appearing on the downlink (EPS, TCS, OBDH, TT&C — these have no line owner). SIDs 2 (AOCS) and 5 (Payload) remain absent until LEOP-007 powers their owning lines.
**GO/NO-GO:** Application image running, full PUS service set available

### Step 6 — Synchronise On-Board Time
**TC:** `SET_TIME(cuc_seconds)` (Service 9, Subtype 1)
**Action:** Upload current UTC epoch as CUC seconds. Now that the application image is running, S9.1 is no longer rejected by the bootloader filter. Use ground-station-stamped uplink time and add the one-way light time correction (~2–8 ms for LEO, negligible at this stage).
**Verify:** S1.1 acceptance and S1.7 execution complete received within 5s
**TC:** `TIME_REPORT_REQUEST` (Service 9, Subtype 2)
**Verify:** Time report TM received within 10s; |T_obc - T_ground| < 1.0 s
**GO/NO-GO:** Onboard clock synchronised to UTC within 1.0 s

### Step 7 — Request EPS Housekeeping (Power Sanity Check)
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [26.0V, 30.0V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 50% within 10s
**Verify:** `eps.power_gen` (0x0107) reported (value will be small while spacecraft is tumbling and not yet sun-pointed)
**GO/NO-GO:** Power state plausible for post-separation conditions

### Step 8 — Log and Report
**Action:** Record AOS time, override-arming time, time of first accepted TC, time of application boot, time-sync residual, and all received HK values. Distribute First Acquisition Report. Confirm pass duration sufficient for LEOP-007 (sequential power-on) on this contact, or schedule the next pass.
**GO/NO-GO:** Sufficient data collected and OBC in application image — clear to LEOP-007

## Off-Nominal Handling
- **No S1.1 returned to CONNECTION_TEST:** the platform's auto-TX hold-down only fires on accepted TCs, so if S1.1 never arrives, either the uplink is not getting through (check pass override / ground station) or the receiver is unhealthy. Retry once. If still nothing, repoint and try Troll/Inuvik.
- **`obdh.sw_image` does not transition after OBC_BOOT_APP:** the boot-app countdown is 10s. Wait 30s, then re-request HK SID 4. If still 0, the application CRC is failing — escalate to OBDH engineer and consider `OBC_BOOT_INHIBIT(0)` then `OBC_REBOOT`.
- **HK SIDs 1, 3, 4, 6 not emitting after boot_app:** verify `obdh.sw_image` actually went to 1. If yes, check that the HK scheduler is enabled for those SIDs (the bootloader-mode entry disables every SID except 11). Re-enable via `S3.5 ENABLE_HK_REPORT(sid=N)` for the missing SIDs.
- **SET_TIME rejected with 0x0006:** the OBC may not have completed the boot-app transition yet (race with the 10s CRC window). Wait 5s and retry.
- **Auto-TX hold-down expires before procedure complete:** any subsequent accepted TC re-arms the timer to 15 minutes, so this should not happen during a normal pass. If the operator goes 15 minutes without commanding, the link will drop — re-issue any TC to re-energise.

## Post-Conditions
- [ ] Bidirectional VHF/UHF link established (auto-TX hold-down active)
- [ ] OBC in application image (`obdh.sw_image` = 1)
- [ ] On-board time synchronised to UTC within 1.0 s
- [ ] EPS housekeeping confirms plausible power state
- [ ] First Acquisition Report distributed
- [ ] Ready to execute LEOP-007 (Sequential Power-On) on this or next pass
