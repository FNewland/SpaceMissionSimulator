# LEOP-001: First Acquisition of Signal & OBC Boot

**Subsystem:** TT&C / OBDH / Ground Segment
**Phase:** LEOP
**Revision:** 3.0 — 2026-08c flow redesign (entry/exit states, no operator
pass override, time sync moved to LEOP-006, SAFE-tier naming)
**Approved:** Flight Operations Director

**Entry state:** cold boot — OBC in the bootloader, only `obc` and `ttc_rx`
lines energised, all heaters / AOCS / payload lines OFF, clock unset (J2000),
no contact established yet. This is the first card of LEOP.

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
- [ ] Iqaluit ground station antenna configured for EOSAT-1 S-band receive frequency
- [ ] Ground station demodulator configured for the **SAFE** rung of the TM rate
      ladder (the 1 000 bps rung, renamed from BEACON in 2026-08c — "beacon" now
      always means the separate UHF whip transmitter) — BPSK, conv(7,1/2)+RS,
      1 kBd, ~3.1 kHz occupied bandwidth. This is the ONLY rate the bootloader
      transmits (`subsystems/ttc.yaml` `beacon_tier: SAFE`); the demodulator
      must be re-configured for 16 kBd after application boot (Step 5)
- [ ] A real ground-station pass is SCHEDULED for this card (predicted AOS/LOS
      from the launch TLE). Pass override is an INSTRUCTOR control, not an
      operator action — no card in this flow instructs the operator to force
      contact
- [ ] Predicted AOS/LOS timeline loaded into ground station schedule
- [ ] Flight Dynamics have provided initial TLE from launch provider
- [ ] MCS database loaded with EOSAT-1 TM/TC definitions
- [ ] All console positions staffed and nominal

## Procedure Steps

### Step 1 — Configure Ground Station Receiver for the Scheduled Pass
**Action:** Command Iqaluit ground station to point at predicted AOS azimuth/elevation for the SCHEDULED pass. Enable auto-tracking on S-band downlink frequency. Set receiver bandwidth to wide acquisition mode (+/- 40 kHz Doppler window).
**Action:** Set the demodulator to the SAFE tier: 1 kBd BPSK with conv(7,1/2)+RS. At 1 000 bps this rung closes with ~56 dB of margin at 5 deg elevation even to the poorest station in the network, which is the whole point of the rung — first acquisition must not depend on a good pass.
**Action:** Wait for predicted AOS. Cold-boot has `ttc_tx` OFF, so there is no carrier to acquire passively; the first uplink in Step 2 is sent blind into the scheduled window and the spacecraft's auto-TX hold-down answers it.
**Verify:** Ground station status = TRACKING_READY at predicted AOS
**GO/NO-GO:** Ground station ready, scheduled pass window open

### Step 2 — Send First Uplink (Connection Test)
**TC:** `CONNECTION_TEST` (Service 17, Subtype 1)
**Action:** Service 17 ping is on the bootloader allow-list, so it is accepted by the OBC even before the application image is loaded. On acceptance, the platform's **auto-TX hold-down** kicks in: any accepted TC autonomously powers `ttc_tx` ON for 15 minutes, energising the downlink without further commanding.
**Verify:** S1.1 (Service 1, Subtype 1) acceptance received within 10s
**Verify:** S17.2 connection test report received within 10s
**Verify:** `eps.pl_ttc_tx == 1` (0x0112, timeout 10 s) — auto-armed
**GO/NO-GO:** Bidirectional link established, downlink visible to operator

### Step 3 — Verify Carrier Lock and Link Quality
**Action:** Confirm ground station receiver has achieved carrier lock. Record measured Doppler offset for Flight Dynamics.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 30s
**Verify:** `ttc.rssi` (0x0502) > -110 dBm within 30s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**GO/NO-GO:** Stable carrier lock with positive link margin

### Step 4 — Confirm OBC is in Bootloader
**TC:** `HK_REQUEST(sid=11)` (Service 3, Subtype 27) — Beacon SID
**Verify:** Beacon packet received (the bootloader only emits SID 11; the beacon structure carries `obdh.sw_image` 0x0311)
**Verify:** `obdh.sw_image` (0x0311) = 0 (BOOTLOADER) within 10s
**Note:** No subsystem SID is emitted periodically yet. The bootloader emits SID 11 only, and even once the application is running the power-up enable set is `simulation.yaml` `default_enabled_sids: [1, 11]` — every other SID must be commanded on with `HK_ENABLE` (Service 3, Subtype 5). This is expected.
**GO/NO-GO:** Bootloader confirmed; ready for application boot

### Step 5 — Boot OBC into Application Image
**TC:** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55)
**Action:** Issue the boot-app command. The OBDH model verifies the application CRC over a 10s window then transitions `sw_image` to APPLICATION (1). Until that transition completes, only the bootloader command set is accepted.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `obdh.sw_image` (0x0311) = 1 (APPLICATION) within 15s
**Verify:** Periodic HK **SID 1 (EPS) and SID 11 (Beacon)** — and only those two — begin appearing on the downlink. Application boot re-applies `simulation.yaml` `default_enabled_sids: [1, 11]` (`engine.py::_apply_default_hk_enable`, called from the boot handler), so every other SID (2 AOCS, 3 TCS, 4 OBDH/Platform, 5 Payload, 6 TT&C, 8 EPS-extended, 9 PROP) is DISABLED at boot and must be commanded on with `HK_ENABLE(sid=N)` (Service 3, Subtype 5). Powering a subsystem's line does not enable its SID.
**TC:** `HK_ENABLE(sid=3)` (Service 3, Subtype 5) — TCS periodic housekeeping
**TC:** `HK_ENABLE(sid=4)` (Service 3, Subtype 5) — OBDH / Platform periodic housekeeping
**TC:** `HK_ENABLE(sid=6)` (Service 3, Subtype 5) — TT&C periodic housekeeping
**Verify:** Periodic SIDs 3, 4 and 6 begin appearing within one structure interval (SID 3 = 60 s, SID 4 = 8 s, SID 6 = 8 s). SIDs 2 (AOCS) and 5 (Payload) are enabled in LEOP-007 once their owning lines are powered.
**Note:** Booting the application does **NOT** clear beacon mode. `ttc.beacon_mode` (0x0521) stays 1 and the spacecraft stays on the BEACON rung until it is cleared explicitly in Step 5a. Until then every `TTC_SET_DATA_RATE` is REFUSED with S1.8 error `0x0007` and the failure notice "TM rate is forced to the beacon tier in beacon mode (LOW refused)".
**GO/NO-GO:** Application image running, full PUS service set available — proceed to Step 5a before changing rate

### Step 5a — Clear Beacon Mode and Move to the LOW Rung
**TC:** `TTC_SET_BEACON_MODE(on=0)` (Service 8, Subtype 1, func_id 70)
**Action:** Clear beacon mode. This is the gate on the whole rate ladder: while beacon mode is latched, BEACON is the ONLY permitted tier and both LOW and HIGH are refused. It is a separate, deliberate step because the beacon rung is the lifeline — the spacecraft never leaves it by accident.
**Verify:** S1.1 acceptance + S1.7 execution complete received within 5s
**Verify:** `ttc.beacon_mode` (0x0521) = 0 within 10s
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) (LOW)
**Action:** Select the ladder's `default_tier` — **LOW, 16 000 bps, BPSK conv(7,1/2)+RS, 16 kBd**. Re-configure the ground demodulator to 16 kBd BEFORE expecting the subsystem SIDs: the beacon demodulator will not lock 16 kbps.
**Verify:** S1.1 acceptance + S1.7 execution complete (NOT an S1.8) within 5s
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps and `ttc.tm_tier` (0x0539) = 1 (LOW)
**Verify:** `ttc.tm_symbol_rate` (0x053B) = 16000 Bd
**Note:** The full order of ON-BOARD gates on the ladder is: **beacon mode off** → LOW selectable; **antenna deployed** (func 69, COM-006) **+ DOWNLINK mode on** (func 79) → HIGH selectable. There is NO on-board elevation gate (2026-08c): the 10-deg rule on HIGH is GROUND arm-through policy — the MCS warns from its own contact geometry and the planner schedules HIGH dumps above it. Each unmet on-board gate is reported as its own S1.8 failure notice naming that gate; read the notice, not the code (they all report `0x0007`). See `docs/LINK_BUDGET_AND_RATES.md` §6.3.
**Note:** LOW carries the full periodic HK set (1 557 bps of channel after RS and TM framing) at under 10 % duty, so the spacecraft never falls behind on housekeeping at the routine rate. The HIGH tier (128 kbps) is NOT entered here — it is only used for dumps (see NOM-011).
**GO/NO-GO:** Beacon mode cleared, downlink at LOW, ground demodulator reconfigured to 16 kBd

### Step 6 — Note the Unset Clock (do NOT sync here)
**Action:** Record that `obdh.time_ref` (0x0A13) reads 0 (NONE): the clock has
never been set and every packet timestamp reads year-2000 CUC. That is correct
behaviour, not a fault.
**Note:** Time synchronisation is **LEOP-006's job** — both the ground path
(S9.1 set-time) and the GPS path (S8.1 func 80) live in that one card so the
boot-then-sync logic is written exactly once. This card sends no time command.
**GO/NO-GO:** Clock state understood and logged

### Step 7 — Request EPS Housekeeping (Power Sanity Check)
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [26.0V, 30.0V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 50% within 10s
**Verify:** `eps.power_gen` (0x0107) reported (value will be small while spacecraft is tumbling and not yet sun-pointed)
**GO/NO-GO:** Power state plausible for post-separation conditions

### Step 8 — Log and Report
**Action:** Record AOS time, time of first accepted TC, time of application boot, and all received HK values. Distribute First Acquisition Report. Confirm pass duration sufficient for LEOP-002 (initial health check) on this contact, or schedule the next pass.
**GO/NO-GO:** Sufficient data collected and OBC in application image — clear to LEOP-002

## Off-Nominal Handling
- **No S1.1 returned to CONNECTION_TEST:** the platform's auto-TX hold-down only fires on accepted TCs, so if S1.1 never arrives, either the uplink is not getting through (check the pass geometry and ground-station pointing) or the receiver is unhealthy. Retry once. If still nothing, repoint and try Troll on its next scheduled pass.
- **`obdh.sw_image` does not transition after OBC_BOOT_APP:** the boot-app countdown is 10s. Wait 30s, then re-request HK SID 11 (which carries `obdh.sw_image`). If still 0, the application CRC is failing — escalate to OBDH engineer and consider `OBC_BOOT_INHIBIT(0)` then `OBC_REBOOT`.
- **SID 1 not emitting after boot_app, or SIDs 3/4/6 still absent after the `HK_ENABLE`s in Step 5:** verify `obdh.sw_image` actually went to 1. If yes, remember that application boot re-applies `default_enabled_sids: [1, 11]` and disables everything else, so any SID commanded on before the boot is switched back off. Re-issue `HK_ENABLE(sid=N)` (Service 3, Subtype 5) for each missing SID after the boot.
- **`TTC_SET_DATA_RATE` answered with S1.8 error 0x0007 after the application booted:** beacon mode is still latched — Step 5a was skipped or its `TTC_SET_BEACON_MODE(on=0)` was not accepted. Read the S1.8 failure notice: it names the gate verbatim ("TM rate is forced to the beacon tier in beacon mode (LOW refused)"), and the same text appears as S5 event `0x030B COMMAND_REJECTED` in the event log and the FDIR journal. Re-issue Step 5a. The application boot deliberately does not clear beacon mode.
- **Auto-TX hold-down expires before procedure complete:** any subsequent accepted TC re-arms the timer to 15 minutes, so this should not happen during a normal pass. If the operator goes 15 minutes without commanding, the link will drop — re-issue any TC to re-energise.

## Exit state
- Bidirectional S-band link established (auto-TX hold-down active; `ttc_tx`
  will drop again if the operator goes 15 min without commanding — LEOP cards
  that need the downlink re-establish it, they do not assume it)
- OBC in application image (`obdh.sw_image` 0x0311 = 1); mission phase = 4
  (LEOP, advanced automatically by the boot)
- Beacon mode cleared (`ttc.beacon_mode` 0x0521 = 0), downlink on the LOW rung
  (`ttc.tm_tier` 0x0539 = 1); HK SIDs 1, 3, 4, 6, 11 enabled — SIDs 2/5/7/12
  stay disabled until the card that powers their subsystem enables them
- Clock still UNSET (`obdh.time_ref` 0x0A13 = 0) — synced in LEOP-006
- All heaters, AOCS, payload, cooler and prop lines still OFF; nothing beyond
  the link and the OBC image was changed by this card

## Post-Conditions
- [ ] First Acquisition Report distributed
- [ ] Exit state above verified

**NEXT:** LEOP-002 (Initial Health Check)
