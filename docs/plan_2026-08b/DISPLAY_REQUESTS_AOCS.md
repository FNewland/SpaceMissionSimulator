# AOCS display + interface requirements — Phase 1 (2026-08b)

Written by the AOCS (Phase 1) owner for the **single MCS owner** (coherence
review §2.5) and for the **payload owner** (§2.2 A-c coordination).
Nothing here has been implemented in `smo-mcs`; this is the requirements list.

---

## 1. What changed in the model that the ground can now see

Eleven new parameters, all in the AOCS-reserved `0x02xx` range, all declared in
`configs/eosat1/telemetry/parameters.yaml` and all downlinked in **HK SID 2**
(4 s cadence). Two new events in the AOCS-reserved event range, declared in
`configs/eosat1/events/event_catalog.yaml`.

| id | name | type | why it exists |
|---|---|---|---|
| `0x0294` | `aocs.gyro_units_on` | 0-3 | how many of the three gyro units are powered |
| `0x0295` | `aocs.gyro_saturated` | bool | a channel is at its 400 °/s measurement limit |
| `0x0296` | `aocs.gyro_bias_injected` | °/s | magnitude of an injected IMU-drift fault |
| `0x0297` | `aocs.commanded_mode` | 0-9 | the mode the **ground asked for** |
| `0x0298` | `aocs.mode_demoted` | bool | 1 = the spacecraft is **not** in its commanded mode |
| `0x0299` | `aocs.fine_degraded` | bool | FINE is running on a 3-wheel set |
| `0x029A` | `aocs.desat_inhibit` | bool | momentum dumps are fenced off |
| `0x029B` | `aocs.h_body_nms` | N·m·s | stored **body** momentum, `\|A·h_wheel\|` |
| `0x029C` | `aocs.desat_ineffective` | bool | last dump timed out without converging |
| `0x029D` | `aocs.wheels_commissioned` | bool | latched once ≥3 wheels were enabled together |

| event | name | when |
|---|---|---|
| `0x020E` | `AOCS_DESAT_INEFFECTIVE` | a dump hit `desaturation.timeout_s` without converging |
| `0x020F` | `AOCS_MODE_RESTORED` | the spacecraft got back to `commanded_mode` |

`0x0200 AOCS_MODE_CHANGE` now carries a **reason string** in its description
and is raised to severity MEDIUM when the transition is a demotion.

---

## 2. Display requirements, in priority order

### D-1 (critical) — commanded vs actual mode, on the AOCS panel and the summary bar

The single highest-value display change in this batch. The live instrumented
run spent six orbits in COARSE_SUN having been commanded NOMINAL, and **nothing
on any display said so** — the mode field read "COARSE_SUN" and looked
perfectly healthy.

Requirement: wherever `aocs.mode` (`0x020F`) is displayed, show
`aocs.commanded_mode` (`0x0297`) beside it, and highlight when
`aocs.mode_demoted` (`0x0298`) is 1.

```
MODE  ECLIPSE            (cmd NOMINAL)     ← amber while demoted
MODE  NOMINAL                              ← plain when mode == commanded
```

Suggested: amber for a *transient* demotion (mode ∈ {SLEW, DESAT, ECLIPSE}),
red for a *safing* demotion (mode ∈ {OFF, SAFE_BOOT, DETUMBLE, COARSE_SUN})
while `commanded_mode` is an operational mode.

### D-2 (critical) — surface the mode-change reason in the event log

`0x0200` descriptions now read e.g.

> `AOCS mode change from ECLIPSE to NOMINAL (eclipse exit; resuming commanded NOMINAL)`
> `AOCS mode change from NOMINAL to DETUMBLE (EMERGENCY body rate 2.31 deg/s > 2.00; return path DETUMBLE -> COARSE_SUN -> NOMINAL)`

The event log must not truncate these; the return path is the operationally
important half of the message.

### D-3 (high) — `smo-mcs/static/index.html` carries a stale AOCS mode table

`engine.py:2874` (`_check_subsystem_events`) emits a **second, duplicate**
`0x0200` event using a 5-entry mode table left over from before the 9-mode
state machine:

```python
modes = {0: 'OFF', 1: 'DETUMBLE', 2: 'SAFE', 3: 'NADIR', 4: 'TARGET'}
```

Live output today: `AOCS mode: TARGET -> ?` for a NOMINAL→ECLIPSE transition.
Every label in it is wrong for the current model, and modes 5-9 render as `?`.
**This is in `engine.py`, which the AOCS owner does not own.** Either delete the
duplicate emission (the model already emits a correct, reasoned `0x0200`) or
replace the table with `_MODE_NAMES` from
`smo_simulator.models.aocs_basic`. Recommend deletion — two events with the
same id and contradictory text is worse than one.

Anywhere the MCS has its own copy of the AOCS mode names, it must match
`configs/eosat1/aocs/aocs_mode_codes.yaml` (0 OFF, 1 SAFE_BOOT, 2 DETUMBLE,
3 COARSE_SUN, 4 NOMINAL, 5 FINE_POINT, 6 SLEW, 7 DESAT, 8 ECLIPSE,
9 INERTIAL).

### D-4 (high) — momentum display must show BODY momentum, not wheel speed

Operators were reading wheel RPM and the envelope percentage `0x02BA`, neither
of which is what a magnetic dump removes. A four-wheel pyramid can sit at
5000 rpm on every wheel with **zero** body momentum (the null-space direction),
and it can sit at 1200 rpm with a large one.

Requirement: add `aocs.h_body_nms` (`0x029B`) to the AOCS momentum panel next to
`0x02BA`, and show `aocs.desat_inhibit` (`0x029A`) and
`aocs.desat_ineffective` (`0x029C`) as status lamps in the same block.

### D-5 (medium) — FINE degraded indication

`aocs.fine_degraded` (`0x0299`) = 1 means FINE is being flown on three wheels.
It is a legal, tested configuration (see §4) but the jitter/deadband spec is
relaxed, so a planner must be able to see it. Show as `FINE (3-wheel)`.

### D-6 (medium) — gyro block

`aocs.gyro_units_on` (`0x0294`) and `aocs.gyro_saturated` (`0x0295`) make the
three S2 gyro device commands observable for the first time. Put them next to
the existing `aocs.gyro_enabled` (`0x025D`), which now reflects the real device
states instead of being hard-wired to "AOCS mode ≠ 0".

### D-7 (low) — staleness

HK SID 2 is a 4 s structure. It is inside the current hard-coded 8 s rule, so it
does not need the cadence×1.5 staleness fix — noted only so the MCS owner knows
SID 2 is not one of the affected structures.

---

## 3. Coordination note for the PAYLOAD owner (coherence review §2.2, A-c)

**The two sides now agree. Here is exactly what the AOCS side does.**

1. **Automatic desat entry excludes FINE_POINT.** `aocs_basic.py`, the
   `mom_mgmt_enable` block in `tick()`, now covers only NOMINAL, INERTIAL and
   ECLIPSE. A momentum dump can no longer pre-empt a strip on its own.
2. **The `desaturate` command is refused in FINE_POINT** with
   `code="DESAT_REFUSED_FINE"`. This is the model-side guard on the S19
   `ea_id 2001` action path (S8 func 1) that the coherence review asked for —
   the S19 rule dispatches unconditionally, so the refusal has to live in the
   model, which is where every path (S8 func 1, S19, FDIR, procedures) lands.
3. **A desat inhibit is asserted automatically while the payload is imaging.**
   Every tick, AOCS reads shared parameter `0x0600` (`payload.mode`) and sets
   `desat_inhibit_auto = (payload.mode == 2)`. While set, `desaturate` is
   refused with `code="DESAT_INHIBITED"`.
   → **The contract: AOCS treats `payload.mode == 2` as IMAGING.** If the
   payload owner's CALIBRATION mode (PL-d) changes the enum such that imaging
   is no longer exactly 2, tell me and I will change the constant. Adding
   CALIBRATION as a *new* value (3, 4, …) is safe as-is.
4. **The operator can latch the inhibit** with the new
   `AOCS_SET_DESAT_INHIBIT` (S8 func 96), visible at `0x029A`.
5. **DESAT returns to the PRE-DESAT mode, not to NOMINAL.** If a dump does
   happen from NOMINAL during a campaign whose commanded mode is FINE, the
   spacecraft returns to NOMINAL and then **automatically re-promotes to FINE**
   once the `0x02C5` preconditions have held for 30 s. So the payload capture
   gate's "exactly AOCS mode 5" requirement is satisfiable again after a dump;
   before this change it never was.
6. **What the payload should watch instead of guessing:** `0x02C5` bit 4 is set
   when the momentum envelope is ≥70 %. That is the honest "a desat is coming"
   warning, and it is visible *before* a strip is planned rather than in the
   middle of one.

---

## 4. Note for the FDIR / procedures owner

- `safe_mode_entry.yaml:28` and `emergency_power_down.yaml:35` commanded
  `AOCS_SET_MODE data:[0x02]` = **DETUMBLE**, a rate-damping mode with no
  pointing. Both are now `[0x03]` = COARSE_SUN, matching
  `aocs_safe_mode_recovery.yaml` and the `safe_mode_aocs` FDIR callback.
- `fault_propagation.yaml aocs_wheel_failure_dual` no longer cuts the AOCS
  power line, and is gated on the new `aocs.wheels_commissioned` (`0x029D`)
  rather than on the bus being live.
- **COARSE_SUN is no longer a destination.** It is not in the model's
  `_OPERATIONAL_MODES` set, so an FDIR safing to mode 3 does **not** overwrite
  the ground's commanded mode, and the spacecraft hands itself back once sun
  lock and the star tracker recover. Any procedure that assumed the operator
  must manually re-command NOMINAL after a safing should be re-checked: the
  return is now automatic, and the manual step is a no-op rather than a
  requirement. (Procedure text is Phase 5, deliberately not edited here.)
- `configs/eosat1/monitoring/s19_rules.yaml` `ea_id 2001` description is now
  slightly optimistic — the dispatch is unchanged but the model may refuse it.
  Worth a note in the rule description; not edited (not AOCS-owned).

---

## 5. Note for the TEXTBOOK owner (Phase 7)

- `manual §6.1` and the old `aocs.yaml` comment claim a full 0.02 N·m·s wheel
  dump in "5-8 min". Measured on the fixed model: **25 min to 0.005 N·m·s,
  48 min to 0.001 N·m·s.** The 5-8 min figure is the instantaneous-torque
  arithmetic (0.020 / 4.8e-5) and ignores that τ = m × B can only ever remove
  the component of the momentum perpendicular to the local field. The corrected
  derivation is written out in `configs/eosat1/subsystems/aocs.yaml`
  under `magnetorquers:`.
- The IMU is **in** the simulator and always was (coherence review §2.6). The
  wiring is now real: three single-axis units on S2 devices `0x0206-0x0208`,
  one per body axis, with per-axis dead-channel handling, a 400 °/s measurement
  range and a temperature-dependent bias. The book's ~10 lines citing
  non-existent func IDs and power lines still need correcting.
- Mode 3 COARSE_SUN should be described as a **hold**, not a destination —
  `aocs_mode_codes.yaml` already says "prerequisite for nominal", and the model
  now behaves that way.
