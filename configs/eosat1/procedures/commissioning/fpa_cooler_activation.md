# COM-010: FPA Cooler Activation
**Subsystem:** Payload / TCS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c: the cooler is now THIS card's to activate ---
LEOP-007 no longer starts it (owner finding: the FPA cooler went on far too
early, burning power through the whole of LEOP). Entry/exit states declared.
**Approved:** Flight Operations Director

**Entry state:** COM-009 exit state --- payload in STANDBY on its own line,
SID 5 emitting, FPA at the uncooled equilibrium. The cooler EPS line
(`eps.pl_fpa_cooler` 0x0114) may ALREADY read 1 --- COM-009's mode command is
itself a cooler-line path since Phase 4 --- but the cooler has not been
commanded and `tcs.cooler_fpa`/`payload.fpa_ready` still read 0.

## Purpose
Activate the Focal Plane Array (FPA) cooler on the multispectral imager. Monitor
the cooldown from the uncooled equilibrium to the commanded setpoint (default
-15C). Verify cooler power consumption, cooldown rate, and thermal stability. The
FPA must reach and hold the setpoint — `payload.fpa_ready` (0x0610) = 1 — before
imaging operations can begin.

> **Timings revised (build 2026-08).** Every temperature and interval in this
> procedure is measured off the corrected single-node FPA model. The previous
> revision described a 30-minute cooldown that passed through -20C on its way to
> -15C: it was written against the pre-Phase-B model, in which the setpoint was
> applied as an *additive environment offset* so the node ran away to ambient
> minus 35 K. The real loop settles in about 10 minutes and never overshoots the
> setpoint.

## Prerequisites
- [ ] COM-009 (Payload Power-On) completed — payload in STANDBY (mode 1)
- [ ] `payload.imager_status` (0x0611) shows no fault bits (COM-009 Step 5)
- [ ] `payload.fpa_temp` (0x0601) at the uncooled equilibrium (+8C to +14C)
- [ ] `eps.bat_soc` (0x0101) > 70% (cooler adds 15 W continuous load)
- [ ] `eps.power_gen` (0x0107) > 40W (sunlit phase, wings deployed)
- [ ] AOCS holding attitude in the NOMINAL family: `aocs.mode` (0x020F) = 4
      (or 8 in eclipse); codes per `configs/eosat1/aocs/aocs_mode_codes.yaml`.
- [ ] Bidirectional S-band link active
- [ ] Ground station pass covers at least 15 minutes (or multiple passes available)
- [ ] Payload and thermal engineers on console

## Procedure Steps

### Step 1 — Record Pre-Activation Thermal State
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — Payload housekeeping
**Verify:** `payload.fpa_temp` (0x0601) in range [+8C, +14C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) consistent with `payload.fpa_temp` within 10s
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Action:** Record baseline FPA temperature and timestamp. This marks cooldown T=0.
**GO/NO-GO:** FPA at ambient temperature, payload in STANDBY

### Step 2 — Enable FPA Cooler
**TC:** `FPA_COOLER(on=1)` (Service 8, Subtype 1, func_id 43) — FPA cooler enable
**Wire:** `2B 01`
**TC:** `GET_PARAM(param_id=0x040C)` (Service 20, Subtype 3) — `tcs.cooler_fpa`
**TC:** `GET_PARAM(param_id=0x0114)` (Service 20, Subtype 3) — `eps.pl_fpa_cooler`
**Verify:** `tcs.cooler_fpa == 1` (0x040C, timeout 10 s) — the cooler is commanded on
**Verify:** `eps.pl_fpa_cooler == 1` (0x0114, timeout 10 s) — and its EPS line is HOT
**Note:** The EPS-line check is new and it is the point of this card. Func 43 was
one of three cooler command paths that set the cooler flag WITHOUT energising EPS
line 4, so `tcs.cooler_fpa` read 1, the operator believed the cooler was running,
and the FPA never got cold — the "FPA does not get to YES" symptom. Since Phase 4
all four paths (funcs 16, 17, 26, 43 and S2 device 0x0601) energise the line, so
0x040C and 0x0114 must now agree. If they ever disagree again, that is the defect
returning.
**Note:** Expect the payload power draw to rise by 15 W
(`subsystems/payload.yaml: fpa_cooler_power_w`), reported at
`payload.cooler_pwr` (0x0602). The cooler is a **miniature Stirling-class
machine running at about 23 % of Carnot**, not a Peltier: holding -15 C against
the +12 C internal sink is a 27 K lift against UA = 0.833 W/K, i.e. 22.5 W of
parasitic heat pumped on 15 W of input — a COP of 1.50, where a single-stage
bismuth-telluride module tops out near 0.88 at this lift. There is no start-up
transient modelled and no vibration signature.
**GO/NO-GO:** FPA cooler enabled, its EPS line energised, and running

### Step 3 — Verify Cooler Power Draw
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**TC:** `GET_PARAM(param_id=0x0106)` (Service 20, Subtype 3) — `eps.power_cons`, total load
**Verify:** Total power increase is 14-16 W relative to the pre-cooler baseline
within 30 s. (This read 0x0108, `eps.eclipse_flag` — a boolean, not a wattage.)
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (bus stable under additional load) within 10s
**GO/NO-GO:** Cooler power draw within specification, bus stable

### Step 4 — Monitor Cooldown (First 5 Minutes)
**Action:** Sample FPA temperature every minute during the initial ramp.
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+2 min
**Verify:** `payload.fpa_temp` < +4C within 10s (initial rate is about 4C/min)
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+5 min
**Verify:** `payload.fpa_temp` < -8C within 10s
**Action:** Record cooldown curve data points. The ramp is exponential, not
linear: the rate falls as the node approaches the setpoint, so a slower reading
at T+5 than at T+2 is expected and is not a fault.
**GO/NO-GO:** FPA temperature decreasing at the expected rate

### Step 5 — Monitor Settling (5-15 Minutes)
**Action:** Continue monitoring at 5-minute intervals as the loop closes.
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+10 min
**Verify:** `payload.fpa_temp` in range [-16.0C, -14.0C] within 10s
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+15 min
**Verify:** `payload.fpa_temp` in range [-16.0C, -15.0C] within 10s
**Note:** The detector never goes *below* the setpoint band. A reading colder
than -16.5C is a control fault, not an over-performing cooler; the cooler
regulates to the setpoint, it does not run open-loop.
**GO/NO-GO:** FPA at the commanded setpoint

### Step 6 — Verify Operational Temperature Reached
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3)
**Verify:** `payload.fpa_temp` (0x0601) in range [-16.5C, -14.5C] within 10s
**TC:** `GET_PARAM(param_id=0x0610)` (Service 20, Subtype 3) — `payload.fpa_ready`
**Verify:** `payload.fpa_ready` (0x0610) = 1 within 60s of reaching the band
**Action:** The readiness flag, not a remembered temperature, is the gate for
imaging: it means the loop has settled about the **active** setpoint (-1 K /
+5 K) and the detector is at or below -3.0C. It carries a 60 s hysteresis, so
expect it to set about a minute after the temperature arrives.
**TC:** `GET_PARAM(param_id=0x0631)` (Service 20, Subtype 3) — cooler setpoint readback (0x0621 is the GREEN-band SNR, not the setpoint)
**Verify:** `payload.fpa_setpoint == -15.0` (0x0631, timeout 10 s)
**TC:** `GET_PARAM(param_id=0x064A)` (Service 20, Subtype 3) — `payload.cooler_floor_c`
**TC:** `GET_PARAM(param_id=0x064B)` (Service 20, Subtype 3) — `payload.cooler_margin_k`
**Verify:** `payload.cooler_margin_k > 0` (0x064B, timeout 10 s) — the commanded
setpoint is reachable at the present sink temperature and load. Expect about
+5 K at the nominal -15 C setpoint with a +12 C sink.
**Note:** `cooler_margin_k` <= 0 means the cooler physically CANNOT reach the
commanded setpoint here — the answer is a warmer setpoint, not more waiting.
The achievable floor at 0x064A is solved every tick from the heat balance
`UA*dT^2 + (Q_load + eta*P)*dT - eta*P*T_h = 0`, so it moves with the sink and
with the 2 W imaging load: -20.35 C idle at a +12 C sink, -19.24 C while
imaging, and only -12.83 C at a +20 C sink, where even the nominal -15 C
setpoint is out of reach.
**GO/NO-GO:** FPA at the commanded setpoint, `fpa_ready` = 1, cooler margin positive

### Step 7 — Verify Temperature Stability
**Action:** Monitor FPA temperature for 10 minutes to confirm stable regulation at setpoint.
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+35 min
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+40 min
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — at T+45 min
**Verify:** `payload.fpa_temp` remains in range [-16.5C, -14.5C] across all samples
**Verify:** Temperature variation < 1.0C peak-to-peak over 10 minutes
**Action:** Record stability data. The regulated node holds 3 sigma = 0.5 K
about its setpoint, so peak-to-peak below 0.5C is normal control performance
and above 1.0C indicates a degraded loop.
**GO/NO-GO:** FPA temperature stable at operational setpoint

### Step 8 — Monitor Through Eclipse (if applicable)
**Action:** If pass spans an eclipse, verify cooler maintains FPA temperature.
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — during eclipse
**Verify:** `payload.fpa_temp` remains in range [-16.5C, -14.5C] within 10s
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V (bus holds with cooler load) within 10s
**GO/NO-GO:** Cooler maintains setpoint through eclipse with adequate power

### Step 9 — Generate Cooler Activation Report
**Action:** Compile complete cooldown curve (temperature vs time), cooler power consumption, thermal stability data, and eclipse performance. Distribute FPA Cooler Activation Report. FPA is now ready for imaging.
**GO/NO-GO:** FPA cooler activated and stable — ready for COM-012

## Off-Nominal Handling
- If FPA temperature not decreasing after 2 minutes: Verify cooler status via `GET_PARAM(param_id=0x040C)`. If `tcs.cooler_fpa` (0x040C) is 1 but `eps.pl_fpa_cooler` (0x0114) is 0, the
  cooler line is not energised and nothing is being pumped — that is the Phase 4
  defect and it should no longer be reachable. If both are 1 and
  `payload.cooler_pwr` (0x0602) is zero, the machine has failed. If both are 1,
  the draw is 15 W and the temperature still will not fall, read
  `payload.cooler_margin_k` (0x064B): a value <= 0 means the setpoint is out of
  reach at the present sink, which is a configuration problem, not a failure. Power off cooler, power off payload. Escalate to payload thermal engineer.
- If the cooldown rate is slower than 2C/min over the first two minutes: this indicates reduced cooler efficiency or a higher thermal load than predicted. Allow additional time. If the FPA cannot reach -14.5C within 20 minutes, log an anomaly. Imaging is still possible at a warmer setpoint down to -3.0 C with reduced SNR —
  re-command the setpoint with `PAYLOAD_COOLER_SETPOINT` rather than waiting
  indefinitely. The commandable envelope is -20 C to 0 C
  (`subsystems/tcs.yaml` `setpoint_min_c` / `setpoint_max_c`).
- If bus voltage drops below 27.0V with cooler running: Disable cooler via `FPA_COOLER(on=0)` (func_id 43, wire `2B 00`). Check battery SOC. Wait for higher charge state or better illumination. Retry during next sunlit pass with full battery.
- If FPA temperature oscillating > 1C peak-to-peak at setpoint: Cooler control loop may need tuning. Check the setpoint via `GET_PARAM(param_id=0x0631)`. If the oscillation persists, re-command the setpoint with `PAYLOAD_COOLER_SETPOINT(setpoint=-15.00 degC)` (func_id 36, wire `24 64 D7`) — there is no commandable cooler-gain parameter.
- If cooler introduces excessive vibration (detected via accelerometer jitter): Compare AOCS attitude jitter with cooler ON vs OFF. If jitter exceeds pointing requirement, schedule cooler operation for non-imaging periods. Consult AOCS engineer for filter tuning.

## Post-Conditions
- [ ] FPA cooler activated and running
- [ ] FPA temperature stable at the commanded setpoint (-15C, band -16.5C to -14.5C)
- [ ] `payload.fpa_ready` (0x0610) = 1
- [ ] Temperature stability < 1.0C peak-to-peak
- [ ] Cooler power draw 14-16 W within specification
- [ ] `eps.pl_fpa_cooler` (0x0114) = 1 — the cooler's EPS line is energised
- [ ] `payload.cooler_margin_k` (0x064B) > 0 — the setpoint is within the
      cooler's authority at the present sink
- [ ] Bus voltage maintained under cooler load
- [ ] Cooldown curve documented
- [ ] Eclipse performance verified (if tested)
- [ ] FPA Cooler Activation Report distributed

## Exit state
- Cooler RUNNING (`tcs.cooler_fpa` 0x040C = 1, line 0x0114 = 1) and left
  running --- COM-011/COM-012 and every imaging card need the FPA regulating;
  restoring "cooler off" here would defeat the card's purpose
- `payload.fpa_ready` (0x0610) = 1 --- FPA at setpoint
- No other state touched

**NEXT:** COM-011 (Payload Calibration)
