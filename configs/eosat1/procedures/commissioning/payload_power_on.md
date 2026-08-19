# COM-009: Payload Power On
**Subsystem:** Payload / EPS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c: the payload EPS line is now THIS card's to
energise --- LEOP-007 no longer touches it (owner finding: LEOP powered
everything including the payload). Entry/exit states declared.
**Approved:** Flight Operations Director

**Entry state:** COM-008 exit state --- platform fully commissioned, OBC
NOMINAL, AOCS commanded NOMINAL, payload line OFF (`eps.pl_payload` 0x0113 =
0, untouched since separation), FPA-cooler line OFF, downlink LOW.

## Purpose
Power on the multispectral imager payload for the first time in orbit. Transition
the payload from OFF to STANDBY mode. Verify payload electronics initialization,
telemetry generation, and confirm the power draw is within the EPS budget. Establish
the baseline payload state required for subsequent cooler activation and imaging.

## Prerequisites
- [ ] COM-001 through COM-008 completed — platform fully commissioned
- [ ] AOCS holding attitude in the NOMINAL family: `aocs.mode` (0x020F) = 4
      (or 8 in eclipse). Codes per `configs/eosat1/aocs/aocs_mode_codes.yaml`.
- [ ] `eps.bat_soc` (0x0101) > 75% (payload power-on adds ~15W load)
- [ ] `eps.power_gen` (0x0107) > 40W (sunlit phase, wings deployed)
- [ ] OBC in NOMINAL mode
- [ ] `tcs.temp_fpa` (0x0408) in range [+5C, +25C] (ambient, pre-cooler)
- [ ] Bidirectional S-band link active with link margin > 6 dB
- [ ] Payload team on console

## Procedure Steps

### Step 1 — Pre-Power-On EPS Baseline
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.5V, 28.5V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 75% within 10s
**Verify:** `eps.power_gen` (0x0107) > 40W within 10s
**Action:** Record pre-power-on bus voltage, battery SOC, and power generation as baseline.
**GO/NO-GO:** EPS can support additional payload load

### Step 2 — Verify Payload Currently OFF
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**GO/NO-GO:** Payload confirmed OFF — safe to power on

### Step 3 — Energise the Payload Line, then Command STANDBY
**Note:** The payload EPS line must be energised BEFORE the mode command. This
card sent `PAYLOAD_SET_MODE(1)` with no `EPS_POWER_ON`, and the payload model
resets the mode to OFF on the next tick when its line is dark — so the mode
appeared to take, then silently reverted, and every later step in this card was
run against a payload that was off.
**TC:** `EPS_POWER_ON(line_index=3)` (Service 8, Subtype 1, func_id 19) — the `payload` line
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27) — EPS housekeeping
**Verify:** `eps.pl_payload == 1` (0x0113, timeout 10 s) — payload line energised
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**Note:** Payload power supply enabled; the electronics boot sequence initiates.
Boot duration is approximately 45 seconds.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**Verify:** `payload.mode == 1` (0x0600, timeout 60 s) — STANDBY
**Verify:** Re-request SID 5 after a further 60 s and confirm `payload.mode` is
STILL 1. A mode that reverts to 0 on the next tick is the signature of an
un-energised payload line.
**GO/NO-GO:** Payload transitioned to STANDBY and HELD there

### Step 4 — Verify Payload Power Draw
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Action:** Compare bus voltage and power consumption with pre-power-on baseline.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V within 10s (bus maintained under new load)
**TC:** `GET_PARAM(param_id=0x0106)` (Service 20, Subtype 3) — `eps.power_cons`, total load
**Verify:** Total load increase relative to baseline is **2-3 W** within 10 s.
(This read 0x0108, which is `eps.eclipse_flag` — a 0/1 boolean, not a wattage.
The expected increase is the payload STANDBY draw from `eps.yaml`, ~2.4 W; the
old "10-20 W" figure was the STANDBY draw plus the 15 W FPA cooler, which is
not switched on until COM-010.)
**GO/NO-GO:** Payload power draw within expected range

### Step 5 — Verify Payload Electronics Health
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**TC:** `GET_PARAM(param_id=0x0611)` (Service 20, Subtype 3) — `payload.imager_status`
**TC:** `GET_PARAM(param_id=0x0603)` (Service 20, Subtype 3) — `payload.imager_temp`
**Verify:** `payload.imager_status` (0x0611) — imager status word, no fault bits set
**Verify:** `payload.imager_temp` (0x0603) within its operating band
**Note:** This step used to read 0x0602 as "firmware version" and 0x0603 as
"self-test result". 0x0602 is `payload.cooler_pwr` (watts) and 0x0603 is
`payload.imager_temp` (degrees C). There is no firmware-version or self-test
parameter in `telemetry/parameters.yaml`; the imager status word is the health
indicator the payload actually publishes.
**GO/NO-GO:** Payload electronics healthy — imager status and temperature nominal

### Step 6 — Check FPA Temperature (Pre-Cooler)
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — `payload.fpa_temp`
**Verify:** `payload.fpa_temp` (0x0601) in range [+5C, +30C] within 10s
**Action:** Record FPA ambient temperature before cooler activation (COM-010).
**GO/NO-GO:** FPA temperature at expected ambient level

### Step 7 — Verify Payload Data Interface
**TC:** `GET_PARAM(param_id=0x0604)` (Service 20, Subtype 3) — `payload.store_used` (%)
**TC:** `GET_PARAM(param_id=0x0648)` (Service 20, Subtype 3) — `payload.product_count`
**TC:** `GET_PARAM(param_id=0x0647)` (Service 20, Subtype 3) — `payload.product_mb`
**Verify:** `payload.store_used` (0x0604) around 20 % — the store ships with
pre-existing non-product fill, so 20 % is the empty baseline, not a fault
**Verify:** `payload.product_count` (0x0648) = 0 and `payload.product_mb` (0x0647)
= 0 — no catalogued products before first light
**Note:** 0x0604 is `payload.store_used` as a PERCENTAGE and 0x0605 is
`payload.image_count`. This step read them as "data interface status" and
"onboard storage free (MB)"; there is no data-interface-status parameter.
`product_count` / `product_mb` are the Phase 4 additions that distinguish real
imagery from the store's baseline fill.
**Action:** Confirm payload can write image data to mass memory.
**GO/NO-GO:** Data interface operational, storage available

### Step 8 — Maintain STANDBY for Thermal Stabilization
**Action:** Hold payload in STANDBY for 10 minutes to allow electronics thermal stabilization. Monitor payload and platform temperatures.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27) — at T+5 min
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — at T+10 min
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — Payload housekeeping
**Verify:** `tcs.temp_obc` (0x0406) stable (not affected by payload heat) within 10s
**Verify:** `payload.fpa_temp` (0x0601) stable within 10s
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) maintained within 10s
**GO/NO-GO:** Payload thermally stable in STANDBY

### Step 9 — Log Payload Power-On Report
**Action:** Record all power-on telemetry including timing, power draw, firmware version, self-test results, FPA temperature, and data interface status. Distribute Payload Power-On Report.
**GO/NO-GO:** Payload successfully powered on in STANDBY — ready for COM-010

## Off-Nominal Handling
- If `payload.mode` does not transition to STANDBY within 90s: Retry `PAYLOAD_SET_MODE(mode=1)` once. If still no transition, check the payload power line via `GET_PARAM(param_id=0x0113)` — `eps.pl_payload`.
  (0x0610 is `payload.fpa_ready`, not a power-bus flag.) If `eps.pl_payload` (0x0113) is 0, the payload line is not energised — that is
  Step 3's `EPS_POWER_ON(line_index=3)`, and a mode command without it reverts on
  the next tick. If power ON but payload unresponsive, power off via `PAYLOAD_SET_MODE(mode=0)` and escalate.
- If bus voltage drops below 27.0V on payload power-on: Immediately power off payload via `PAYLOAD_SET_MODE(mode=0)`. Check battery SOC. If SOC < 60%, wait for higher charge state. If bus regulation issue, investigate EPS before retry.
- If the imager status word shows a fault: record it via `GET_PARAM(param_id=0x0611)`. Power off payload via `PAYLOAD_SET_MODE(mode=0)`. Do not proceed to cooler activation. Escalate to payload engineer with self-test diagnostic data.
- If payload power draw > 5 W in STANDBY (against a ~2.4 W budget): Unexpected current draw may indicate electronics fault. Monitor for 5 minutes. If trending upward, power off payload. If stable at higher-than-expected level, consult payload manufacturer specifications.
- If the FPA temperature reading is anomalous: cross-check against the TCS node
  `tcs.temp_fpa` (0x0408), which is the same physical node published by the TCS
  model. A disagreement between 0x0601 and 0x0408 is a telemetry-path problem;
  agreement on an odd value is a real thermal state. If thermistor fault, note limitation for COM-010 (cooler activation will rely on alternative monitoring).

## Post-Conditions
- [ ] Payload in STANDBY mode (mode 1)
- [ ] Payload line energised — `eps.pl_payload` (0x0113) = 1
- [ ] Payload power draw within expected range (2-3 W in STANDBY)
- [ ] `payload.imager_status` (0x0611) shows no fault bits
- [ ] FPA temperature at ambient baseline recorded
- [ ] Data interface operational, storage available
- [ ] Bus voltage stable under payload load
- [ ] Payload Power-On Report distributed

## Exit state
- Payload line ON, payload in STANDBY (`payload.mode` 0x0600 = 1), SID 5
  enabled and emitting
- NOTE: since Phase 4 EVERY cooler command path --- including the
  `PAYLOAD_SET_MODE` used here (funcs 16/17/26/43 and S2 device 0x0601) ---
  energises EPS line 4, so `eps.pl_fpa_cooler` (0x0114) may already read 1 at
  exit. The COOLER itself is commanded (and its performance verified) in
  COM-010; this card claims only the line side effect.
- No AOCS, OBC, TTC or heater state touched

**NEXT:** COM-010 (FPA Cooler Activation)
