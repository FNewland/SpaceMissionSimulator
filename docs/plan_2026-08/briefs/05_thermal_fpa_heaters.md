# Workstream 5 — Thermal: FPA curve divergence + heater status

**Abstract.** Both defects reported against the EO-Sat-1 SMO suite are confirmed and traced to specific lines of model code, not to the telemetry, packetisation or display chain. (1) The focal-plane temperature is not one variable published twice — it is two genuinely independent state variables, `TCSState.temp_fpa` (`models/tcs_basic.py:24`, published 0x0408) and `PayloadState.fpa_temp` (`models/payload_basic.py:41`, published 0x0601), integrated with different time constants, different targets, and different, unlinked cooler-command sources; the proximate bug is `tcs_basic.py:492`, which applies `fpa_cooler_target_c` as an additive −35 K environment offset instead of as a setpoint, so the two curves diverge by +26.4 °C on the command path the shipped commissioning procedure actually uses. (2) Heater status is static for two different reasons: the battery heater's thermostat ON setpoint (+1.0 °C, `tcs.yaml:32`) lies 9.0 °C below the coldest temperature the battery node can ever reach (`env_int` = +10 °C in eclipse, `simulation.yaml:138`), so it can never autonomously trip, and manual commands are silently reverted at `tcs_basic.py:457` when the EPS heater line is off; the "FPA heater" indicator is in fact `tcs.cooler_fpa` (0x040C), written only by S8 func 43, a command no procedure sends. The OBC heater appears to work only because it bypasses the thermostat entirely and mirrors its EPS power line (`tcs_basic.py:477`). The audit additionally finds a permanently-violated S12 monitor with inverted limits that autonomously switches the payload off every session, a panel solar-heat gain ~80× too large (equilibrium panel temperatures up to +362 °C), no albedo/Earth-IR/radiator physics at all, and a power-accounting split in which the battery heater is over-billed by 6 W continuously (9.45 Wh/orbit against a stated +2 to +18 Wh/orbit margin) while up to 60 W of payload and cooler load can run entirely off the EPS books. Nine work packages are proposed with testable acceptance criteria; WP-5 must be coordinated with the parallel power-deficit workstream. Reproduction scripts are in `scripts/thermal/`.

---

## Defect 1: FPA curve divergence

**There are genuinely two independent state variables, integrated with different ODEs, from different actuator sources, published under two parameter IDs. This is not a unit/calibration alias — K vs °C is ruled out (both are °C, both packed `h, scale:100` in HK).**

| | TCS "FPA" | Payload "FPA" |
|---|---|---|
| state var | `TCSState.temp_fpa` — `models/tcs_basic.py:24` | `PayloadState.fpa_temp` — `models/payload_basic.py:41` |
| ODE | `tcs_basic.py:492-493` | `payload_basic.py:379-387` |
| time constant | `_TAU["fpa"]=120 s` (`tcs_basic.py:116`) | `100 s` cooling / `120 s` warming (`payload.yaml:4-5`) |
| cooler-ON target | `env_int + (target − 20)` = **−23…−25 °C** | `fpa_cooler_target_c` = **−15.0 °C** |
| cooler-OFF target | `env_int` = **+10…+12 °C** | `fpa_ambient_c` = **+5.0 °C** |
| actuator flag | `s.cooler_fpa`, set **only** by S8 func 43 (`service_dispatch.py:918-920` → `tcs_basic.py:629-632`) | `s.cooler_active`, set **only** by payload mode ≥1 (`payload_basic.py:408-413`) |
| published to | `0x0408 tcs.temp_fpa` (`tcs_basic.py:541`) | `0x0601 payload.fpa_temp` (`payload_basic.py:652`) |
| catalogued | `parameters.yaml:228` | `parameters.yaml:305` |
| HK | SID 3 @60 s (`hk_structures.yaml:162`) | SID 5 @8 s (`hk_structures.yaml:217`) |
| displayed | `displays.yaml:237,245,250` (EPS/TCS role) | `displays.yaml:377,485` (Payload/FD role) |

**Root cause (definitive):** `tcs_basic.py:492`

```python
cool = (self._fpa_cooler_target - 20.0) if (s.cooler_fpa and not s.cooler_failed) else 0.0
s.temp_fpa += (env_int+cool-s.temp_fpa)/self._TAU["fpa"]*dt+random.gauss(0,0.02)
```

`fpa_cooler_target_c` is used as an **additive environment offset** (`−15 − 20 = −35 K`), not as a setpoint the node is driven to. So TCS asymptotes to `env_int − 35`, never to `−15`. Compounding this, the two models take their cooler command from different, unlinked sources, so the *actuator states* also disagree.

**Numeric reproduction** (`/tmp/th/fpa.py`; 450 km SSO, P=5670 s, 35 % eclipse, `simulation.yaml:135-143` env; final-orbit means):

| operator action | `tcs.temp_fpa` (0x0408) | `payload.fpa_temp` (0x0601) | divergence |
|---|---|---|---|
| nothing (default) | **+11.3 °C** | **+5.0 °C** | **+6.3 °C** |
| `PL_SET_MODE(1)` only | **+11.4 °C** | **−15.0 °C** | **+26.4 °C** |
| `PL_SET_MODE(1)` + `FPA_COOLER(1)` | **−23.6 °C** | **−15.0 °C** | **−8.6 °C** |
| `FPA_COOLER(1)` only | **−23.7 °C** | **+5.0 °C** | **−28.7 °C** |

The middle row is what a trainee following the shipped procedures actually sees: `COM-010 fpa_cooler_activation.md` prerequisite requires payload STANDBY, and `COM-010` Step 1 line 30 explicitly asserts *"`tcs.temp_fpa` (0x0408) consistent with `payload.fpa_temp`"* — the procedure already encodes the intended invariant, and it fails by 26 °C.

**Is the divergence a real unmodelled effect?** No — not as coded. A real cooled FPA *does* differ from its mounting-bracket thermistor, but that difference is a **small, cooler-side gradient (1–3 K, faster τ on the detector side)**, not a 26 °C sign-flipping offset driven by two disconnected command paths. The evidence that this is a bug, not physics:
- Both configs use the identical key name and value `fpa_cooler_target_c: -15.0` (`tcs.yaml:39`, `payload.yaml:2`) — they are meant to be the same node.
- The payload model *already* has a legitimate bracket/detector split: `detector_temp_c = fpa_temp + 0.5` (`payload_basic.py:517`, published 0x0617). That is the correct pattern.
- TCS's own FPA-ready event window is `(−50, −5)` (`tcs.yaml:54`) while payload's is `[−16, −10]` (`payload_basic.py:396`) — two different definitions of "ready" for one detector.

**Recommended resolution.** TCS owns the thermal node; payload reads it.
1. Make TCS the single integrator. Fix `tcs_basic.py:492` to drive toward the setpoint, not offset by it: `target = self._fpa_cooler_target if cooler else env_int`, with a *bounded heat lift* (see AF-6) rather than an unbounded env offset.
2. Drive `s.cooler_fpa` from the union of the three existing command paths (TCS func 43, EPS func 17, payload mode ≥1) via one shared param, so all three views agree.
3. Delete `PayloadState.fpa_temp` as an independent integrator; make `payload_basic.py:652` publish `0x0601 = shared_params[0x0408] + detector_offset`, keeping `payload.yaml:3-5` (`fpa_ambient_c`, `fpa_tau_*`) only if you deliberately keep a fast detector node — in which case **rename** it `payload.detector_temp` and label 0x0601 "detector (cold-finger) temperature", 0x0408 "FPA bracket temperature", and add a documented ≤3 K offset. Either fix is defensible; publishing two numbers both labelled "FPA temperature" is not.

---

## Defect 2: heater status static

### Chain trace (all three actuators)

```
model state ──► shared_params ──► HK SID 3 (60 s) ──► TM socket ──► MCS _param_cache
             ──► parameters.yaml name ──► displays.yaml status_indicator
```

| actuator | model write | param | in HK? | display | changes? |
|---|---|---|---|---|---|
| battery heater | `tcs_basic.py:543` | 0x040A `tcs.htr_battery` (`parameters.yaml:229`) | yes, `hk_structures.yaml:163` | `displays.yaml:239-240`, `:149` | **NO** |
| OBC heater | `tcs_basic.py:544` | 0x040B `tcs.htr_obc` (`parameters.yaml:230`) | yes, `hk_structures.yaml:164` | `displays.yaml:242-243` | **YES** |
| FPA cooler | `tcs_basic.py:545` | 0x040C `tcs.cooler_fpa` (`parameters.yaml:231`) | yes, `hk_structures.yaml:165` | `displays.yaml:245-246` | **NO** |

**The transport chain is sound for all three.** Ruled out by evidence: parameter never written (all three are written every tick); bitfield packing (each is its own `B` field); absent from HK SID (all three present in SID 3); display reads a different parameter (names match `parameters.yaml` exactly). The break is in the **model control law**, and it is a *different* cause for each of the two broken ones.

### Battery heater — thermostat setpoint is unreachable

`tcs_basic.py:453-457`
```python
bat_eps_on = bool(shared_params.get(0x0115, 0))   # eps.pl_htr_bat, written eps_basic.py:731
if bat_eps_on:  self._thermostat_control("battery", s.temp_battery)
else:           s.htr_battery = False
```
`_thermostat_control` (`tcs_basic.py:236-262`) turns ON only at `temp <= lo`, where `lo = htr_battery_setpoint_on_c = 1.0 °C` (`tcs_basic.py:54`, loaded from `tcs.yaml:32 on_temp_c: 1.0`).

The battery node can never get there. `tcs_basic.py:463`: `temp_battery` relaxes to `env_int` only, which is **10.0 °C in eclipse / 12.0 °C sunlit** (`simulation.yaml:138,141`) — there is no cold sink and no radiator term on the battery node.

Reproduction (`/tmp/th/bat.py`, 12 h, including the OBC conduction path):

| htr_bat line | htr_obc line | T_bat final | T_bat min over 12 h | thermostat ON-trips |
|---|---|---|---|---|
| OFF | OFF | 11.50 °C | 9.36 °C | **0** |
| ON | OFF | 10.54 °C | 9.21 °C | **0** |
| ON | ON | 12.69 °C | 10.47 °C | **0** |

**Margin to trip: 9.0 °C. The battery heater cannot autonomously turn on in any modelled orbit, eclipse, or attitude.**

Second-order contributor: manual command also silently reverts. `HEATER_BATTERY` (S8 func 40, `service_dispatch.py:616-621`) sets `htr_battery=True` + `htr_battery_manual=True` and returns `{"success": True}` (`tcs_basic.py:~620`), but the next tick hits `tcs_basic.py:457` and forces it back to `False` if `eps.pl_htr_bat` (0x0115) is OFF — and `htr_bat` **defaults OFF** (`eps.yaml:57`, `eps_basic.py:36`). So the trainee sees TC accepted + executed, and 0x040A stays 0 forever.

### OBC heater — works, and only because it bypasses the thermostat

`tcs_basic.py:473-477`
```python
obc_eps_on = bool(shared_params.get(0x0116, 0))
if getattr(s, "htr_obc_manual", False): s.htr_obc = bool(s.htr_obc) and obc_eps_on
else:                                    s.htr_obc = obc_eps_on
```
`htr_obc` is a **direct mirror of the EPS power-line state** — no thermostat at all. Every `EPS_POWER_ON(line=6)` / `HEATER_OBC` toggles 0x040B immediately. That is why the OBC heater is the only one that appears to work. (It is also the one the operations manual says does not exist — `manual/03_tcs.md:64-67`.)

### FPA cooler — commanded from a channel nobody uses

`s.cooler_fpa` is written by exactly one code path: `tcs_basic.py:629-632`, reached only from S8 **func 43 `FPA_COOLER`** (`service_dispatch.py:918-920`). It is **not** set by:
- `EPS_FPA_COOLER` func 17 → `eps_basic.py:812` sets EPS-internal `s.fpa_cooler_on` only;
- `PL_SET_MODE(1/2)` func 26 → `payload_basic.py:408-413` sets payload-internal `s.cooler_active` only;
- `S2_PAYLOAD_COOLER` (device 0x0601) → `payload_basic.py:271-272` only;
- EPS power line 4 `fpa_cooler` (0x0114) — TCS never reads it.

`manual/03_tcs.md:112` states the cooler control is *"Autonomous via payload mode"*, and `COM-010` never sends func 43. So in every shipped procedure the "FPA Cooler" indicator at `displays.yaml:245` stays **OFF while the cooler is running**. Three independent cooler-state booleans exist for one cryocooler.

---

## Additional thermal findings

1. **[CRITICAL] Panel solar-heat gain is ~80× too large; panel temperatures reach hundreds of °C.** `tcs_basic.py:432-436` + `simulation.yaml:142-143`: `env = base_env + (I·28·0.3)·10`. The `10 K/W` gain should be `1/UA = C/τ⁻¹`, i.e. 0.120 K/W (+X), 0.125 (+Y), 0.267 (+Z) → gain is **83×/80×/38×** too large. Equilibrium +Y panel temperature: 54 °C at 0.25 A, 116 °C at 1 A, **362 °C at 4 A** (achievable — the deployed +Y wing is 0.330 m² → 4.7 A at normal incidence, `eps.yaml:35`, `eps_basic.py:405-413`). `manual/03_tcs.md:167` promises "+80 °C steady state". No limit or S12 monitor exists on 0x0400–0x0405, so this is silently wrong on the "Panel Temperature Distribution" chart.

2. **[MAJOR] S12 monitor "TCS FPA Temperature Low" has inverted limits and is permanently violated.** `s12_definitions.yaml:190-196`: `low_limit: -15.0, high_limit: -20.0`. The checker is `if checked < low or checked > high` (`service_dispatch.py:1412`) — the admissible set is empty. `monitoring_loader.py:118-129` loads it verbatim with no ordering validation. Because TCS params are never power-gated (`service_dispatch.py:1351-1352`), it fires at t=0, emits 0x9408, which fires **S19 rule 3001 `tcs_fpa_overtemp_payload_off` → S8 func 20 payload OFF** (`s19_rules.yaml:97-104`, `engine.py:1873-1876`). The spacecraft autonomously switches the payload off in every session for a monitor that cannot be satisfied.

3. **[MAJOR] Both achievable TCS FPA states are outside the red limits.** `limits.yaml:36` gives 0x0408 yellow −18/+8, red −20/+12. Model: cooler OFF → +9.6…+12.6 °C (permanent yellow, intermittent red); cooler ON → −22.6…−25.6 °C (permanent red). Only the payload's −15 °C sits inside. No configuration of the model produces a green FPA on the TCS side.

4. **[MAJOR] Payload FPA sits exactly on its S12 low limit → alarm chatter.** `s12_definitions.yaml:310-316` low = −15.0; model asymptote is −15.0 ± σ, σ = 0.02·√(τ/2Δt) ≈ 0.14 °C (`payload_basic.py:385`). The parameter crosses the limit ~50 % of samples, and each crossing fires S19 rule 6002 (`s19_rules.yaml:199-205`).

5. **[MAJOR] Five mutually inconsistent FPA limit sets, and three battery-heater setpoint sets.**

| source | FPA limits | battery heater on/off, power |
|---|---|---|
| `tcs.yaml:52-58` | warn −50/−5, alarm −60/0 | 1 / 5 °C, 6 W |
| `limits.yaml:36` | yellow −18/+8, red −20/+12 | — |
| `s12_definitions.yaml:182-196` | −15/−3 and −15/−20 | — |
| `thermal_exceedance.md:24` | cold −45, hot −15 | — |
| `manual/03_tcs.md:118-120` | yellow −18/+8, red −20/+12 | **5 / 10 °C, 8 W** |
| `tcs_verification.md:64-65` | — | **5 / 10 °C** |
| `routine_health_check.md:59` | — | **"ON if temp < +10 C"** |

`tcs.yaml` is the outlier that the code actually uses, and it is the one that makes the heater unreachable.

6. **[MAJOR] Model constants are over-determined and imply an implausible cooler.** The model specifies **both** τ and C independently, so `UA = C/τ` is an unstated derived quantity: FPA `100 J/K ÷ 120 s = 0.833 W/K`. The `−35 K` cooler offset therefore implies **29.2 W of heat lift from a 15 W Stirling cooler → COP 1.94 at −25 °C**. Real miniature Stirling coolers at 250 K deliver COP ≈ 0.1–0.3. Heater authority is correspondingly tiny: battery `6 W / 1.667 W/K = +3.6 K`, OBC `4 W / 0.889 W/K = +4.5 K` — a heater that can raise the battery by 3.6 K cannot save it from a real cold case.

7. **[MAJOR] No external flux model.** Grep across `packages/` finds **no albedo, no Earth IR, no view factors, and no Stefan-Boltzmann term**; `_solar_irrad = 1361.0` (`eps_basic.py:175`) is the only flux constant and it is used for *power*, not heat. Consequences: (a) the nadir face (`panel_pz`) is given base_env **−5 K**, colder than the ram face, when a 450 km nadir-pointing EO satellite sees ~237 W/m² Earth IR + ~30 % albedo on that face; (b) internal nodes (obc, battery, fpa) have **zero attitude and zero beta dependence** — `env_int` is a constant 10/12 °C (`tcs_basic.py:445-451`), so `manual/03_tcs.md:73-84` "orientation-based thermal control" (sun-point to warm the battery) is untrainable — it does nothing.

8. **[MAJOR] Beta-angle dependence is inverted.** `tcs_basic.py:449`: `sun_f = abs(cos(beta))`, `env_ext = −10 + 50·sun_f`. Maximum external temperature at β = 0 — which is the *maximum-eclipse* geometry — and minimum at β = 90°, the full-sun geometry. Orbit-average external sink should rise with |β|.

9. **[MAJOR] Radiation-to-space term is linear in T, applied only to panels, and double-counts.** `tcs_basic.py:274-279`: `rad_loss = 0.2·(T + 270.15)`, subtracted from panels only, on top of an `env` relaxation that already represents radiative equilibrium. Effect is a fixed −6.5 K (±X), −6.8 K (±Y), −13.3 K (±Z) offset. No radiator node exists: `tcs.temp_radiator_n/s` (0x0420/0x0421, `parameters.yaml:408-409`) are declared `stub: true` and **never written** — yet `tcs_verification.md:71` tells operators to `GET_PARAM(0x0420)` for "FPA cooler power status" (which is actually 0x040C).

10. **[MAJOR] Param-ID collision: 0x040D.** `tcs_basic.py:546` writes `htr_thruster` (boolean) to 0x040D; `parameters.yaml:407` declares 0x040D as `ttc.pa_backup_temp` in °C.

11. **[MAJOR] `payload.fpa_ready` is permanently 0.** Model writes `0x060F` (`payload_basic.py:229,670`); `parameters.yaml:315` and HK SID 5 (`hk_structures.yaml:228`) both use **0x0610**. `tm_builder.py:67` does `params.get(param_id, 0)` → the "FPA Ready" indicator (`displays.yaml:389-390`) is hard-zero forever. `0x060F` is not in `parameters.yaml`, so it is invisible too.

12. **[MAJOR] Three dead display/planner parameters.** `tcs.temp_regulation_margin` (gauge `displays.yaml:144`, planner pre-condition `activity_types.yaml:268`) exists in **no** file — not even `parameters.yaml`. `eps.subsys_power_tcs` and siblings (0x013C–0x0141, `parameters.yaml:353-358`) are displayed twice (`displays.yaml:30,528`) and written by no model.

13. **[MINOR] Battery heater FDIR override described in the manual does not exist.** `manual/03_tcs.md:60-62` claims FDIR will force the heater on near red limits "overriding ground commands". No such rule exists in `s19_rules.yaml` or `fdir.py`.

14. **[MINOR] Decontamination bake-out is unbounded and unlimited.** `tcs_basic.py:485-490` drives `temp_fpa → +50 °C` with τ=120 s and **no duty limit, no power draw, and no EPS billing at all** — a 65 K rise on a 0.833 W/K node implies ~54 W of heater that costs nothing. `decontamination_active` (0x0418) has no S12 monitor.

15. **[MINOR] Heater duty-cycle window is only approximately correct.** `tcs_basic.py:521-529`: exponential decay approximation to a 600 s sliding window asymptotes to 99.8 %, never 100 %. Cosmetic, but `htr_duty_battery` (0x040E) reads 0.0 forever anyway (finding above).

16. **[MINOR] S12 heater-power threshold is unreachable and disagrees with `limits.yaml`.** `s12_definitions.yaml:217-223` trips 0x0411 above **50 W**; `limits.yaml:39` uses yellow 15 / red 18 W. Maximum possible `htr_total_power_w` is **10 W** (6 + 4; thruster forced off at `tcs_basic.py:497`). Neither can ever fire.

---

## Heater power vs EPS

**Heater power is billed on the wrong variable: EPS bills the power *line*, not the heater. TCS's own `htr_total_power_w` (0x0411) is written at `tcs_basic.py:551` and read by nothing** (grep: only YAML/config references, no consumer).

`eps_basic.py:477-481`:
```python
line_powers["htr_bat"] = self._line_power_w.get("htr_bat", 6.0) if lines.get("htr_bat", True) else 0.0
line_powers["htr_obc"] = self._line_power_w.get("htr_obc", 4.0) if lines.get("htr_obc", True) else 0.0
```

| item | model truth | EPS bills | error |
|---|---|---|---|
| Battery heater | duty **0 %** always (Defect 2) → 0 W | **6.0 W continuous** whenever line 5 is ON | **+6.0 W over-billed**, = **9.45 Wh/orbit** (5670 s) |
| OBC heater | on ⇔ line on (`tcs_basic.py:477`) | 4.0 W when line 6 ON | consistent — correct by accident |
| FPA cooler | payload `cooler_active` = mode ≥ 1, publishes 15 W at 0x0602 (`payload_basic.py:653-655`) | only if `lines["fpa_cooler"]` **AND** EPS-internal `s.fpa_cooler_on` (`eps_basic.py:467`), set solely by S8 func 17 | **15 W unbilled** on the `PL_SET_MODE` path; or 15 W billed with no cooling on the EPS-only path |
| Payload bus | `payload_basic` mode from S8 func 26 | `s.payload_mode`, set solely by S8 func 16 (`eps_basic.py:456-465`) | **up to 45 W unbilled** (`eps.yaml:38`) if the operator uses `PL_SET_MODE` alone |
| Decontamination | ~54 W equivalent heat (finding 14) | nothing | **fully unbilled** |

**Feed to the power workstream.** Against the stated margin of **+2 to +18 Wh/orbit** (`eps.yaml:31-36`):
- the phantom **6 W battery-heater bill = 9.45 Wh/orbit** is between 0.5× and 4.7× the whole margin, and is pure fiction — it is charged for a heater that the thermostat can never energise;
- conversely, the **15 W cooler + 45 W payload** can run in the payload model completely off the EPS books (60 W ≈ 94 Wh/orbit) if the trainee uses only the payload commands, which is exactly what `COM-010` and `imaging_session.md` instruct.

So the power budget is simultaneously **over-charged for heaters** and **under-charged for the payload chain**, depending on which command path the operator takes. Whoever is closing the power deficit must fix the *coupling*, not the wattages: bill EPS from the actual actuator states (0x040A/0x040B/0x0411 and the payload's own mode/cooler flags), not from line-switch position.

---

## Work packages

| # | Title | Files | Acceptance criteria (testable) | Effort | Depends on |
|---|---|---|---|---|---|
| **WP-1** | Single FPA thermal node | `models/tcs_basic.py:492-493`, `models/payload_basic.py:379-387,652`, `configs/eosat1/subsystems/payload.yaml:2-5` | (a) TCS FPA asymptote with cooler ON = `fpa_cooler_target_c` ± 0.5 °C, not `target−20`. (b) After 600 s at any orbit phase, `abs(params[0x0408] − params[0x0601]) ≤ 3.0` in *all four* command combinations of the Defect-1 table. (c) Payload no longer integrates its own `fpa_temp`; `test_payload_enhanced` still passes. | **M** | — |
| **WP-2** | Unify FPA cooler actuator state | `models/tcs_basic.py:629-632`, `models/eps_basic.py:467,812`, `models/payload_basic.py:408-413`, `service_dispatch.py:735,920` | After **any** of `FPA_COOLER(1)`, `EPS_FPA_COOLER(1)`, `PL_SET_MODE(1)`, `S2_PAYLOAD_COOLER(on)`: `params[0x040C] == 1` **and** EPS bills 15 W on line 4 **and** the FPA cools. Symmetric for OFF. | **M** | WP-1 |
| **WP-3** | Make the battery heater reachable | `configs/eosat1/subsystems/tcs.yaml:29-37`, `configs/eosat1/simulation.yaml:135-143`, `models/tcs_basic.py:453-470` | (a) Setpoints reconciled to the manual (ON +5 / OFF +10 °C, 8 W) across `tcs.yaml`, `manual/03_tcs.md:57`, `tcs_verification.md:64-65`, `routine_health_check.md:59`. (b) Eclipse internal sink lowered so `temp_battery` reaches ≤ +5 °C in eclipse with the heater off. (c) Over one orbit with line 5 ON, `params[0x040A]` records **≥ 2 rising and ≥ 2 falling edges**, and `params[0x040E]` (duty) is strictly in (0, 100). | **M** | — |
| **WP-4** | Fail loudly when a heater command cannot take effect | `models/tcs_basic.py:~615-625`, `service_dispatch.py:616-621` | `HEATER_BATTERY(on=1)` with `eps.pl_htr_bat`=0 returns **TC(1,8) execution failure** with a reason string, not `success: True`. Regression test asserts 0x040A stays 0 *and* a failure ack is emitted. | **S** | WP-3 |
| **WP-5** | Bill EPS from actuator state, not line state | `models/eps_basic.py:456-481`, `models/tcs_basic.py:551` | (a) With line 5 ON and thermostat OFF, `eps.power_cons` contribution from `htr_bat` is **0 W**, and equals `params[0x0411]` when ON. (b) `PL_SET_MODE(2)` alone raises `eps.power_cons` by `payload_power_w`. (c) Orbit-energy assertion: heater Wh/orbit == `∫0x0411 dt` within 1 %. | **M** | WP-2, WP-3; **coordinate with the power-deficit workstream** |
| **WP-6** | Fix broken/inverted monitors and reconcile limit sets | `configs/eosat1/monitoring/s12_definitions.yaml:182-223,309-316`, `configs/eosat1/mcs/limits.yaml:36,39`, `monitoring_loader.py:118-129` | (a) Loader raises on `high_limit < low_limit`; unit test covers it. (b) No S12 monitor is in violation in a clean 3-orbit nominal run. (c) 0x0601 low limit moved off the setpoint (≥ 3σ ≈ 0.5 °C clearance). (d) 0x0411 thresholds ≤ 10 W in both files. (e) S19 rule 3001 does not fire in a nominal run. | **S** | WP-1 |
| **WP-7** | Repair thermal telemetry gaps | `models/payload_basic.py:229,670`, `models/tcs_basic.py:546`, `parameters.yaml:315,405-409`, `displays.yaml:144`, `activity_types.yaml:268` | (a) `payload.fpa_ready` (0x0610) toggles 0→1 within 60 s of `PL_SET_MODE(1)` in HK SID 5. (b) 0x040D collision resolved (thruster status moved or removed). (c) Every parameter referenced by `displays.yaml` and `activity_types.yaml` exists in `parameters.yaml` **and** is written by a model — asserted by a new test that walks both files. Covers `tcs.temp_regulation_margin`, `eps.subsys_power_*`, 0x0420/0x0421. | **M** | — |
| **WP-8** | External-flux / radiator physics | `models/tcs_basic.py:265-279,440-451`, `configs/eosat1/simulation.yaml:135-143`, `configs/eosat1/subsystems/tcs.yaml:3-27` | (a) Panel solar gain re-derived as `1/UA` per node; sunlit panel equilibrium ∈ [−80, +80] °C at all beta and all currents up to 5 A. (b) Earth-IR and albedo terms added to the nadir face with a documented view factor; nadir face is warmer than zenith in eclipse. (c) Beta dependence monotonic in \|β\| for orbit-average external sink. (d) `env_int` gains attitude/beta dependence so sun-pointing measurably warms the battery (≥ 3 °C), making `manual/03_tcs.md:73-84` trainable. (e) Node τ and C reconciled — one is derived, not both configured. (f) Real radiator node written to 0x0420/0x0421. | **L** | WP-1, WP-3 |
| **WP-9** | Rewrite thermal procedures against the fixed model | `procedures/commissioning/fpa_cooler_activation.md`, `.../tcs_verification.md`, `procedures/contingency/thermal_exceedance.md`, `procedures/nominal/eclipse_transition.md`, `.../routine_health_check.md`, `procedures/emergency/thermal_runaway.md`, `manual/03_tcs.md` | (a) Every `GET_PARAM`/`SET_PARAM` id in these files exists and has the claimed meaning — currently `COM-010` Step 2/6 uses **0x0620/0x0621/0x0622** (band SNRs) for cooler enable/setpoint/gain, Step 3 uses **0x0108** (eclipse flag) for load power, and `COM-002` Steps 6/7 use **0x0410/0x0411/0x0420**; correct ids are 0x040C, 0x0330/0x0331. (b) `COM-010` contradiction removed: prerequisite requires STANDBY (cooler running, FPA already at −15 °C) while Step 1 demands ambient +5…+25 °C. (c) Cooldown profile matches the model — real time to 0 °C is **29 s** and to −10 °C is **2.3 min**, not the 10/15 min in Steps 4-5; Step 5's "< −20 °C at T+20 min then < −13 °C at T+30" requires an overshoot a first-order lag cannot produce. (d) `manual/03_tcs.md:64-67` ("no OBC heater circuit") reconciled with the OBC heater that exists in config, model, TC catalog, EPS line and display. (e) All quoted limits match `limits.yaml` and `s12_definitions.yaml` exactly. | **M** | WP-1, WP-3, WP-6 |

**Suggested order:** WP-1 → WP-2 → WP-3 → WP-4/WP-6/WP-7 (parallel) → WP-5 (sync with the power workstream) → WP-8 → WP-9.

Reproduction scripts written during this audit: `/tmp/th/fpa.py` (two FPA curves), `/tmp/th/bat.py` (battery thermostat unreachability), `/tmp/th/panel.py` (UA/heater-authority/cooler-COP/panel-temperature tables). Copies are kept alongside this brief in `scripts/thermal/`.
