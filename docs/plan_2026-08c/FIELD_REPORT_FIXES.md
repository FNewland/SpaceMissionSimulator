# Field-report fixes, 2026-08-17/18

Fourteen items reported from running the real system. Suite after:
**6672 passed, 10 skipped, 3 xfailed, 0 failed** (21 min).

Most of these were not the defect you described — they were a worse defect
wearing that description. Where that happened it is called out.

---

## 1. "Always in SAFE mode, never NOMINAL"

Root cause found by running the engine, not reading it. The bootloader zeroes the
reaction wheels (`start_in_bootloader: true`, so every boot), and **no procedure
anywhere re-enables them** — `AOCS_ENABLE_WHEEL` appeared only in a contingency
card; COM-004 used `RW_SET_SPEED_BIAS` and never enabled anything. So
`aocs.wheels_enabled` read 0 on the first tick AOCS ran, the FDIR rule
`wheels_enabled < 2` (level 2) latched at t≈11 s and never cleared, which forced
`sc_mode = SAFE` forever **and** repeatedly demoted `aocs.mode` out of NOMINAL
back to COARSE_SUN. The `att_error > 5 deg` rule then piled on because attitude
cannot converge without wheels. Both halves of your complaint, one cause.

Per your ruling, all three parts: wheels stay off until commanded (verified that
powering the EPS line does not enable them, and kept it), the four
`AOCS_ENABLE_WHEEL` commands were added to COM-004 as step 1 with a readback on
0x0260, and the legacy FDIR loop is now gated.

The gate is **per-rule `min_phase`, not a blanket phase gate** — a blanket
`phase >= 6` would disarm battery SoC, bus voltage, temperature, reboot-count and
CAN-bus rules for the entire early mission, and LEOP is exactly when a mishandled
spacecraft flattens its battery. Only two rules carry `min_phase: 6`, both
because their trigger condition *is* the expected state of an uncommissioned
spacecraft. A gated-out rule is disarmed, not suppressed: the latch clears and
the persistence accumulator resets, so a reboot or an instructor rewind cannot
strand a stale contingency.

Also found: **`sat.mode` (0x0001) and the console's SC MODE used different
definitions** — `sat.mode` read only the FDIR flags and ignored the phase gate,
so it could report NOMINAL while the header said SAFE. Two things called
"spacecraft mode" that disagreed. Now one derivation.

Proof: `test_nominal_reachable_e2e.py` drives a cold boot through
`OBC_BOOT_APP` -> power lines -> four wheel enables -> star trackers ->
COARSE_SUN -> NOMINAL -> phase 5 -> phase 6, asserts SAFE persists at phases 4
and 5, then holds NOMINAL for 60 min of sim time with the rules armed. A/B: with
`min_phase` cleared, the identical run latches SAFE again.

## 2. Mission phase — it gets a telecommand (this reverses D-5)

You said delete it if it has no purpose. It has one, so it gets commands: the
phase gates which subsystems tick, telecommand acceptance (phase <=3 restricts
uplink to a bootloader subset), the SC-mode derivation, advanced FDIR arming, the
separation timer, and the propulsion interlock — `prop.yaml min_phase: 6`, so
**with the phase parked at 4 no burn could ever be armed**. Nothing in the code
ever set phase 5 or 6; it parked at LEOP for the life of every run.

`SYS_SET_MISSION_PHASE`, S8 func_id 120, criticality critical, args `phase` +
`flags` (bit 0 = FORCE). Four distinct refusal codes rather than one:
0x0C01 out of range; 0x0C02 phases 0-2 refused **even with FORCE**, because those
phases stop the ticks that carry the uplink and accepting one would lock the
operator out permanently; 0x0C03 phase >=4 while the OBC is in the bootloader;
0x0C04 backwards without FORCE.

Backwards transitions are permitted but only deliberately. Refusing outright
would leave a premature declaration with no remedy; accepting silently loses the
protection. Forced transitions are logged `[FORCED]` in the event.

The instructor console was also missing the widget entirely — the backend handler
existed, was allowlisted and range-validated, and had no control. Added, along
with START SEPARATION, which had the same problem.

## 3. S12,12 "not an implemented subtype"

Not a missing handler — `_handle_s12` already implemented 12,3 / 12,4 / 12,12 and
the catalogue listed all three. The **acceptance gate** whitelisted only
`{1,2,6,7}`, so all three were refused before reaching the handler that
implements them. Now `{1,2,3,4,6,7,12}`. TC(12,12) returns TM(12,13) with the 46
monitoring definitions, which is exactly what the console's "Refresh from
Spacecraft" already decoded.

## 4. Failure injection — 62 injections audited, 5 broken, all fixed

Every injection was exercised against a real engine, each against an identical
un-injected **control** engine ticked in lockstep so free-running physics cancels
and any divergence is the injection's own doing. Then breakpoint-saved, restored
into a fresh engine, then cleared.

| Injection | What was wrong |
|---|---|
| `prop.tank_overpressure` | wrote the pressure; the blowdown relation recomputed it from propellant load on the next tick. **Zero divergence at any magnitude** |
| `aocs.st_blind` | same shape — `_tick_star_trackers` recomputed blinding from sun geometry next tick. Also ignored `unit` on inject while `clear` honoured it, so the two could target different boxes |
| `eps.sada_stuck` | worked live, but its state was model attributes not dataclass fields, so it **healed on breakpoint restore** |
| `eps.overvoltage` | same healing bug; clear also restored a hard-coded threshold instead of the configured one |
| `obdh.cpu_spike` | same healing bug; same hard-coded restore |

Two more found by reading: **failure ids collided** — the id was built without the
unit selector, so `st_failure unit=1` and `unit=2` shared an id and the second
overwrote the first, leaving one applied and untracked after a clear. And
`ScenarioEngine._apply_initial_conditions` **dropped typed kwargs**, so a
pre-injected scenario failure lost its unit selector and hit the model default.

## 5. Typed failure parameters

The single 0..1 slider is gone. Every injection now declares a `ParamSpec` —
type, label, unit, range, allowed values, default — next to its `inject_failure`,
single-sourced. 62 specs, 44 typed parameters, served over
`GET /api/failure-catalog`, and the instructor UI **generates** the form: a
dropdown for wheel/panel/tracker/power line/gyro axis, a number field with the
real engineering unit and range, a checkbox for booleans, multi-select where the
model takes several. Validated on both sides and **rejected with a reason, never
clamped silently** — and the UI no longer discards 4xx bodies, so the reason
reaches you.

## 6. Scenarios brought up to date

38 files, all now load and all verified to produce peak divergence against a
control run. The interesting ones:
- `gs_antenna_failure` injected a **spacecraft** fault while briefing students to
  diagnose a **ground** fault.
- `eps_overcurrent` used 2.0x on line 3 (payload), which draws 0 W unless the
  imager is on — it could not trip at any magnitude.
- `fpa_overtemp` injected `solar_array_partial magnitude: 0.0`, which means
  "wing healthy". A dead event.
- `debris_impact_hermes3` poked five model-owned parameters that are overwritten
  on the next tick.
- Two scenarios ("INSTRUCTOR CUE: inject from the Radio UI") injected nothing at
  all.
New gates: every scenario's failure names exist and its typed parameters validate.

## 7. Planner — "Start time (UTC)" was local time

Both halves of the round trip were wrong in a way that cancelled out and hid it:
the prefill wrote local wall digits into a field labelled UTC, and the read-back
parsed a zone-less string, which JS defines as local. Prefilled values survived;
anything *typed* shifted by the offset. Fixed both ends, with a hint under the
field showing the local equivalent. Verified under four timezones including
half-hour offsets and date rollover. Swept the rest of the planner UI — it is the
only date input, and every display formatter was already on `getUTC*`.

## 8. Planner — template overlap conflicts

Three independent causes, none of them the local-time bug:
- **The conflict check was time-blind.** It reported a conflict for every pair
  with non-empty start strings, ignoring when they ran — 8 dumps x 2 images gave
  36 "conflicts" between activities hours apart.
- **The overlap check was global, not per-resource.** A data dump (ttc+obdh) and
  a calibration (payload+aocs) contend for nothing. Now gated on
  `requires_subsystems` intersecting, conservative when a type declares none.
- **Template expansion.** `_upcoming_passes` counted contact *windows*; five
  stations give ~52/day and consecutive windows overlap in time — one physical
  opportunity seen from two dishes, but there is one transmit chain. And flexible
  anchors were resolved with no knowledge of the pass-pinned dumps, so a daylight
  imaging pass landed on a booked dump in about a third of load epochs.
- The templates were also genuinely bad: `payload_checkout` spaced calibration
  and imaging 20 min apart when the eclipse is ~35 min, so whenever both fell in
  one eclipse they snapped to the same instant.

144 template loads across 48 sim epochs with real propagation: **0 conflicts**.

## 9. Planner data budget — dumps were counted, at 69 % of their real rate

Not omission. Each contact was collapsed to one averaged number: the ladder blend
was smeared uniformly across the window although the HIGH tier is a contiguous
arc centred on closest approach, and station setup/teardown was applied as a flat
derating although it is spent at the two lowest-elevation ends. The templates
deliberately book dumps in the HIGH core, so they were charged 0.686x what they
actually achieve. 11 dumps worth 44.3 MB were credited 17.3 MB.

Now each contact has a real usable window and a HIGH arc, and a dump is credited
the instantaneous rung for the seconds it occupies. Overlapping windows at two
dishes are treated as one chain at the best available rung. Result: the shipped
24 h plan closes (24.6 MB generated, 24.5 MB down); an over-planned 12-image day
genuinely does not. No margin was fudged.

## 10. Planner power budget — 0 on the PC, 100 on the Mac

Reproduced deterministically, and it is not a platform difference:
`_estimate_eclipse_fraction` returned a hard-coded 0.35 and `_get_beta_angle` a
hard-coded 30 degrees whenever the ground track was missing or did not intersect
the window, then the budget integrated on them. Those constants sit on the
opposite side of zero from the real balance, and the per-pass clamp drove the
result to whichever rail matched. `ground_track_hours` is 3.0 against a 24 h
contact horizon, so **21 of every 24 hours were fabricated** — the two machines
differed only in whether the background track recompute had landed. The same
clamp also laundered NaN: `max(0, min(100, nan))` is 100 and
`max(0, min(nan, 100))` is 0, which is your two numbers exactly.

Illumination is now a required argument; missing or non-covering input returns
`computable: false` with a reason instead of a plausible-looking number; the UI
says so rather than rendering 0 %. Physical battery clamping is kept but
recorded. Also fixed: eclipse samples were selected by lexical ISO-string
comparison, which breaks across `Z` / `+00:00` spellings.

**Disagreement reported rather than papered over.** Over the shipped 24 h plan
the planner says -4.7 W margin and calls the day infeasible; `tools/power_budget.py`
says +5.5 W and closes it. ~15 W apart on a 38 W spacecraft, and it is all in the
planner's constants: a 32 W base built from "OBC 15 + RX 5 + wheels 12", none of
which exists in `eps.yaml` (6.0 / 1.5 / 9) — they are the pre-2026-08b manual
figures `POWER_SIZING.md` retired as fictional; a 64 W array that is the two
wings alone at beta=0, omitting 0.198 m^2 of body panels; and activity power
added on top of the base only while overlapping a contact. **This needs an EPS
ruling** — tuning it in the planner would hide which side is right. One
unambiguous mismatch was fixed: a 120 Wh battery fallback against `eps.yaml`'s
100 Wh, a fourth number claiming to be the battery.

## 11. MCS trends "does not work at all"

The hinted causes were already correct and I proved it rather than assuming — the
archive call sites all pass the schema-v2 epoch, and the window is already on sim
time. The real defect is that **every failure mode rendered identically**: archive
corrupt, HTTP 500, and a genuinely empty window all printed
`NO ARCHIVED DATA IN THIS WINDOW`, which from an operator's chair is exactly
"does not work at all". Two bugs behind it: the archive-unavailable message was
unconditionally overwritten by the sample count a moment later, and `fetch` does
not reject on HTTP 500, so a server error became "no data".

Now the pane reports the worst outcome most-specific-first, names the window and
its clock when a window is genuinely empty, and calls out a legacy wall-clock
archive explicitly — those rows can never intersect a sim-time window however
wide you make it, and the old pane implied a quiet spacecraft instead.

## 12. "matplotlib is installed but the MCS says it is not"

Both a real bug and the likely interpreter mismatch.

The real bug: `matplotlib.use("Agg", force=False)` means, per matplotlib's own
source, *silently ignore the failure* — and `use()` also sets
`backend_fallback = False`, **disabling matplotlib's own headless fallback**. So
on a machine with no display, or with `MPLBACKEND`/`matplotlibrc` naming an
interactive backend, the import raises and gets caught as "not installed" on a
machine where it plainly is. The `except ImportError` also swallowed an
ImportError raised *inside* a working install (NumPy ABI mismatch, broken C
extension). And everything after the guard was unprotected, so a backend that
imports but cannot draw took the whole report out with a 500.

Now: `MPLBACKEND` is set before matplotlib is imported at all, `use("Agg",
force=True)`, and a throwaway figure is created and closed so a draw-time failure
is caught at boot. The diagnostic names `sys.executable`, the environment, the
matplotlib status and the actual exception, and distinguishes
`not-installed` / `import-failed` / `backend-failed` — so if it is the wrong
Python, the message says which Python and gives you the exact install command for
*that* interpreter. Status is logged once at MCS boot and served on
`GET /api/reports`.

## 13. OBDH buffer fill vs S15 buffer fill

Same quantity; the OBDH one was fabricated. `obdh.hktm_buf_fill` was an admitted
placeholder that accumulated `rng.uniform(0,2) * dt` percent against a fictitious
1000-slot capacity and drained on **TTC carrier lock** — it modelled ground
contact, not storage, and never touched the real store. Measured: it saturated at
100 % about 25 min into every gap between passes while the real 18 000-slot HK
store was at 15-35 %, and read 0 % during a pass while the store kept growing.
That is your "occasionally very high".

`event_buf_fill` and `alarm_buf_fill` were worse — raw packet counts declared as
percent, fed only by methods nothing in production calls, so they read 0 forever
and would have shown "137 %" if they had ever been fed.

All three now read `count/capacity` off the real store, the identical arithmetic
TM(15,14) publishes, and both pages render to the same resolution so rounding
cannot make them look different. Found while dumping: **TC(15,9) on any store
with more than 255 packets died** — the progress report packed the count into a
`>HB`, so the operator saw a dispatch error although the dump had queued.

## 14. IMU power and gyro calibration

The S2 gyro device path was already correct — powering a unit off does degrade
the channel and inflate that axis's process noise, verified end to end. What was
missing is that the console's AOCS equipment group offered star-tracker and GPS
power buttons and **nothing at all for the gyros**, so from the command page it
genuinely looked like there is no IMU power command. Added, plus
`aocs.gyro_units_mask` so telemetry says *which* unit is off rather than only how
many. (There is still no IMU power *line*, by design — three gyros powered as S2
devices.)

`AOCS_GYRO_CALIBRATION` was a two-line sham: it zeroed the **truth** state of a
Gauss-Markov process the spacecraft cannot observe, completed instantly, and
ignored the injected hardware bias entirely. An instructor could inject
0.05 deg/s, fly COM-003, be told "complete", and nothing changed.

Now: preconditions with a specific failure code each (unpowered, a unit off, no
star tracker *in the determination loop* — COARSE_SUN is refused — rates too
high, busy, preconditions lost mid-run); a real 300 s arc with a 30 s settle
discard and observable progress; it estimates the bias and writes a compensation
table applied on the measurement path. Measured: estimate 0.0500/0.0497/0.0500
against 0.05 injected, residual 0.0866 -> 0.00025 deg/s while the injected
hardware bias stays 0.0866 — the fault persists, the symptom is compensated,
which is the correct semantics. Gyro-only coast drift over 600 s: **50.3 deg
uncalibrated vs 0.27 deg calibrated.** COM-003 was almost entirely fabricated
(it commanded the calibration with `SET_PARAM` on parameters that are not the
calibration, and told the operator to calibrate in COARSE_SUN where the tracker
is not in the loop) and has been rewritten.

## 15. Console field truncation

`.panel { overflow: hidden }` — and every panel is a grid item, so a non-visible
`overflow` loses its automatic minimum size and the track is squeezed below its
content. The result clips with **no ellipsis, no wrap, no scrollbar and no
tooltip**, which is the worst possible failure on an ops console: you cannot tell
a clipped value from a real one. Fixed in CSS and in the render path — displayed
values stay rounded, the full unrounded value and unit go into the tooltip and
`data-raw`. Verified by measuring the ink of every value cell against its nearest
clipping ancestor in headless Chromium at four widths, with a negative control
that re-applies the old CSS and proves it did clip.

## 16. Commands removed from subsystem pages

Per your rule that all commands belong on the command page: six `Request S15 TM
Dump` buttons (EPS, TCS, AOCS, PAYLOAD, PROP, OBDH), the PROP pane's
`ENABLE SID 9` / `ONE-SHOT REQUEST`, the PROPULSION and PAYLOAD command consoles,
and the alarm dialog's `DUMP S12 STORE`. All now live in a new COMMANDING panel,
and each page keeps a signpost saying where its command went. Kept as display
actions: the dump scrubber, the on-demand filter, the archive link.

## 17. "database disk image is malformed"

Three causes, each sufficient alone: `check_same_thread=False` disabled sqlite3's
own guard with **nothing in its place** while the request path, the 1 Hz
snapshot, a background prune and thread-executor routes all shared one handle; no
busy timeout, so overlapping writers got instant `database is locked` swallowed
by a bare `except` mid-write; and `PRAGMA journal_mode=WAL` **returns the mode
actually in force** and was never checked — on a synced or mounted folder it
silently stays on the rollback journal. `configs/data` lives inside the checkout,
so if that folder is synced this is exactly what happens.

Fixed all three, and the failure is now survivable: `quick_check` at open,
`open()` never raises, and every archive endpoint returns HTTP 200 with a
labelled state. Four distinct operator-facing states — `new`, `legacy` (v1
wall-clock, readable, not correlatable), `corrupt` (playback unavailable, **live
telemetry unaffected**), `unavailable`. The string `malformed` never reaches a
response body. `SMO_TM_ARCHIVE` relocates the file off synced storage without
editing the tree.

## 18. Textbook Chapter 14 — 64 cards re-corrected against the current simulator

Six cards would have damaged the spacecraft or lost the mission as written:
- **COM-004 was missing entirely** from the book (dismissed as "folded into
  COM-003"). It is the only card that enables the reaction wheels. Flown as
  printed, commissioning cannot complete.
- **LEOP-007 step 15 and COM-013** left the phase at 4, so `sat.mode` stays
  pinned SAFE and **no burn can ever be armed** — the mission-loss case for a
  conjunction.
- **Every reboot path** (NOM-006, CTG-010, CTG-021, EMG-004) re-armed the onboard
  S11 schedule without re-syncing a clock the bootloader had reset to J2000. Time
  tags then sit decades in the OBC's future and silently never fire — including a
  pre-loaded burn or safing sequence.
- **CTG-012** told the operator to compute line 8's parameters as `0x0110 + N`
  and `0x0118 + N`, which resolve to `eps.line_current_0` and `eps.bat_dod`. The
  block does not extrapolate past index 7.
- **LEOP-001** budgeted a 15-minute auto-TX hold-down; the real hold is 300 s.
- **EMG-006** described a 24.5 V bottom rung that does not exist.

Also corrected throughout: the 4S battery band (it is 8S-1P, 100 Wh), a
six-face solar array (only five faces carry cells; -Z is the imager aperture),
`EA_ENABLE(definition_id=)` (the field is `ea_id`), and the whole clock story
across LEOP-006, NOM-006, NOM-009, CTG-010, CTG-018, CTG-021, CTG-027 and
EMG-004.

---

## Needs a ruling from you

1. **The planner's power constants disagree with the simulator's by ~15 W** on a
   38 W spacecraft (item 10). This is an EPS-workstream decision, not a planner
   bug to tune away.
2. `first_acquisition.md` still says a 15-minute auto-TX hold-down while
   `simulation.yaml` says 300 s. I followed the config; the shipped card should
   be fixed.
3. `PowerConstraintChecker._estimate_eclipse_fraction` has the identical
   hard-coded-0.35 pattern that caused item 10, on a different endpoint with its
   own semantics and tests. Left alone deliberately.
4. `scenarios/aocs_sensor_cascade.yaml` still recommends `AOCS_EST_RESET` where
   running the (now real) gyro calibration is the better answer.
5. `inject_failure("st_blind")` re-randomises the gyro bias to sigma 0.005 deg/s,
   far larger than the model's own 2.8e-5 steady state. Plausibly a defect.
