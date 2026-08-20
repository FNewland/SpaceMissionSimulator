# Field batch 2026-08-19 — delivery notes

Everything in this batch came from you flying the system for real. Grouped by
what was actually wrong, because in most cases it was not what the symptom
suggested.

---

## The two you were blocked on

**"Can't get beacon mode off / only beacon telemetry."** Two separate traps,
both now closed.

*The first* was timing: `OBC_BOOT_APP` runs a **10-second CRC verification**
before `sw_image` flips to APPLICATION. Your `TTC_SET_BEACON_MODE(off)` arrived
inside that window, while the OBC was still in the bootloader — where func 70
is not on the allow-list — so it was refused S1.2 and the ladder stayed pinned.
The card documents the wait; nothing on the console showed you the boot was
still running.

*The second is worse and is the real answer.* `TTC_SET_BEACON_MODE` sent with
**no argument byte** silently defaulted to `on = True` — accepted, clean
S1.1/S1.3/S1.7, and beacon mode latched **ON**. The same
`bool(data[0]) if data else True` pattern appeared at **12 sites**, and the
default was the dangerous direction at most of them: boot inhibit ON, desat
inhibit ON, prop inhibit ON, heaters ON, FPA cooler ON, downlink mode ON.
The sweep then found the same class in **45 fixed-argument commands** and 17
`if len(data) >= N` branches that did nothing and still reported S1.7 — plus
`AOCS_RW_RAMP_DOWN` defaulting `wheel_idx` to **255 = all wheels** and
`EPS_DEPLOY_WING` defaulting to **both** (one-shot, irreversible). All now
refuse with a notice naming the missing field and its legal values. Four
silent clamps became refusals too (SADA angle, heater duty, watchdog period,
wing code).

Where your empty command came from: **not** the typed command builder — that
encodes one value per catalogue field. It was the raw **"Data (hex)" box**,
which had no length gate, only an advisory chip. Both ends are now closed, and
TC(19,1) can no longer define an S19 rule that fires an argument-taking
function with no argument.

**S1.7 now means what it says.** Per your ruling, any command whose effect takes
more than a second holds its completion: boot-app, wing deploy, gyro
calibration, payload calibration, memory scrub. S1.3 goes out on acceptance,
S1.7 arrives when the effect actually completes against the original sequence
count, and S1.8 with a real reason on failure or timeout. A failed boot CRC now
produces an S1.8 instead of silence. Pending completions survive a breakpoint.
New telemetry `obdh.boot_state` (0x0A15) and `boot_time_remaining_s` (0x0A16)
in HK SID 4, a `BOOT_COMPLETE` event, and the bootloader refusal now reads
*"a boot IS in progress, ~N s of CRC left — wait for its S1.7, then re-send"*.

---

## The two that were quietly corrupting your session

**Chart.js and the map "not installed".** They were installed. **The modern
pages never loaded them** — no `<script>` tag for `chart.umd.min.js` or
`svg-world-map.js` in the modern MCS shell or the modern planner pages; only
the graceful-degradation notices were there, so they showed permanently. Both
tags added. That is also why the ground track was empty.

**The corrupt archive.** Your `tm_archive.db` was 49 KB with a **29.9 MB WAL** —
a write-ahead log that never checkpointed, which is exactly the failure mode
the archive hardening predicted for a synced/mounted folder. Moved to
`_to_delete/archive_corrupt_20260819/`; the MCS builds a clean one on restart.
If it recurs, set `SMO_TM_ARCHIVE` to a path outside the synced folder.

---

## Eclipse — one physical fact, fourteen evaluators

You reported two symptoms: generating power in eclipse, and AOCS in eclipse
when the planner said otherwise. Same root cause, and the same shape as the
two-sim-clocks and three-METs defects: **one geometry, many evaluators, on
three different clocks** (spacecraft, MCS, planner), agreeing only when nothing
moved. There were even two *different* shadow models in one file, plus a
**hard-coded 35-minute eclipse** in the planner's timeline JS.

At 1× speed there were zero disagreements — which is why no test caught it. At
60×, the ground said ECLIPSE while telemetered `eps.power_gen` read **20.0 W**
for a 120-second window, and ground-vs-spacecraft eclipse disagreed on 1.35 %
of samples. The skew bound is poll-gap × speed: up to 90 sim-seconds, twice per
orbit.

Now there is **one** shadow computation — conical umbra plus penumbra with a
finite solar disc, chosen precisely because a cylinder has no penumbra, so
illumination steps 1→0 between samples and consumers can only agree by
sampling at the same instant. It publishes `eps.sun_fraction` (0x0142), and
generation is `area × k_eff × geometry` with the illumination folded in — the
`if in_eclipse: gen_w = 0` branch is **deleted**, so zero output in umbra is
arithmetic, not a check that can be bypassed. Ground tools read the
spacecraft's present-time value and tag it `eclipse_source`, falling back to
their own propagator (same function) only when the anchor is stale. The power
panel takes its eclipse label from the same packet as the wattage.

True geometry at the new epoch: period **94.5 min**, β **−30.6°**, eclipse
fraction **0.355**, duration **33.5 min**. The documented "β 49.5°, 0.315" was
stale.

---

## HK SIDs disabling themselves

Real defect, and the culprit was your own procedure. `apply_phase_change()`
re-applied `default_enabled_sids: [1, 11]` on **application→application**
transitions — and `SYS_SET_MISSION_PHASE` is the last step of the last LEOP
card *and* the last commissioning card. Declaring the phase wiped the
commissioning you had just done. No reboot, no event, no display.

Fixed: a phase declaration is not a reboot. A *boot* legitimately does reset the
set, and now says so — event `0x0056 HK_ENABLE_SET_RESET` naming the silenced
SIDs and the reason, plus an Enabled column and a banner in the HK Manager.

You were right that it reaches delayed TM, and **two things were lying about
the gap**: the delayed-TM viewer drew a 400-second outage as one smoothed
filled segment between adjacent points on a category axis (fabricated
continuity), and the report generator computed `expected` from
`times[-1] - times[0]`, so a SID that stopped half-way through reported ~0 %
missing and **zero gaps** — precisely the shape a disable produces. Both now
detect and draw real gaps.

---

## Planner activities were not flyable

Every activity type audited against what the simulator actually requires, and
most were wrong:

- **imaging_pass** emitted `func 0x01` labelled "SET_MODE_FINE_POINT" — func 1
  is `AOCS_DESATURATE`. It desaturated (20–50 min, and it *inhibits imaging*)
  where it meant to point, then the capture was refused 0x0021. Zero products,
  every time. Now: NOMINAL → payload STANDBY → FINE → 90 s settle → IMAGING →
  shutter OPEN → capture → readout → restore. 120 s → 300 s.
- **data_dump** left DOWNLINK mode ~1 s after starting, demoting to LOW —
  measured 366 s of a 330 s dump running at the wrong rate. Now holds HIGH for
  the window, steps down *then* leaves DOWNLINK.
- **momentum_desaturation** booked 600 s for a measured 29-minute operation,
  and was refused outright from FINE_POINT. Now leaves FINE first, books
  1740 s, polls progress.
- **orbit_manoeuvre** never opened the latch valve, and waited on
  `catbed == 180` — float equality, so it always timed out. Now opens the
  valve, uses `>=`, and safes afterwards.
- **software_upload** targeted `0x40000000`, which is unmapped — refused as
  read-only boot ROM.

Minimum durations and prerequisites are now **data**, so the conflict checker
catches an under-booked activity or an imaging pass hung on a desat's tail.
The proof is `test_fly_a_plan.py`: the planner's own expansion of a realistic
day, executed as real packets against a real engine, asserting no refusals and
that the capture, dump, desat and burn all actually achieve their intent.

---

## The S11 time tags

Your diagnosis was right, and it was the **classic** MCS console's S11
scheduler: `new Date(txt)` on a zone-less string is browser-**local** by
specification. Measured for the same typed time: UTC → CUC 826418096 (correct),
New York → +5 h, Kolkata → −5 h 30. Exactly "no offset if it is in UTC" — the
UTC host was the only one that was right, so the defect was invisible on the
machine that mattered. Prefilled values always worked (they carry a `Z`); only
typed ones were wrong. The modern page was already correct.

---

## Wide display

Relaid out as you asked: narrow left rail (TIME / NEXT PASSES / LINK stacked)
with the ground track dominating — **~2.3× the map area** on ultrawide, 2.1× at
16:9 — plan wider, budgets as a slim strip under it. Plus your follow-ups: the
next orbit's contacts and activities are labelled (station, type, time, max
elevation), the track is solid for the current and next orbit and **dotted
beyond**, and the five ground stations are drawn with names — turning green
while in contact — with **AOS and LOS marked on the track**, driven by the same
contact data as the NEXT PASSES list so the two cannot disagree.

The orbital period is **derived from equator crossings** of the served ground
track (94.5 min measured), with a Kepler fallback, and printed on the wall with
its provenance — no hard-coded period, given the epoch just moved.

Two real findings: the map projection scaled longitude and latitude
independently, so the world was squashed to whatever shape the block happened
to be; and `fetchMission()` marked the page fresh in the no-services case,
making the 120-second STALE banner **unreachable** in the default deployment.

---

## Needs a decision from you

1. **A 0.85 m/s burn cannot complete.** Thrust misalignment
   (`thrust_offset_mm: 5.0` → 1.65 mN·m) against a 2 mN·m wheel drives
   `att_error` past 1.0° in ~4 s, and S19 rule 8001 aborts at ~0.08 m/s — every
   time, from a settled entry. The offset, the wheel authority, the control
   bandwidth or the per-burn ΔV needs revisiting. The plan now handles the
   abort correctly rather than hanging.
2. **The auto-TX hold-down drops an operator-commanded line.** `auto_tx_hold_s:
   300` de-energises `ttc_tx` after 300 s with no accepted TC even in NOMINAL
   with the line explicitly commanded on — it killed a transmitter mid-dump.
   Arguably it should not expire outside the bootloader phases.
3. **COM-003 gyro calibration is unreachable at the new geometry** (currently an
   xfail). Wheels-off MTQ-only control hunts at 0.1–0.4 °/s continuously at the
   2026-08-20 sun/field geometry, so the 0.10 °/s gate never opens; at the old
   epoch there were quiet spells. Relax the gate, permit an MTQ-quiet arc, or
   move the calibration after COM-004.
4. **The 240 km swath ruling was made at the retired 450 km altitude.** The FOV
   is unchanged, so at 513 km it gives **273.5 km**. Re-derive the FOV to
   restore 240 km, or accept 273.5?
5. **`aocs.fine_ready` (0x02C5) and `payload.fpa_ready` (0x0610) are absent from
   the engine state summary**, so a ground procedure cannot read the very gates
   that refuse FINE entry or a capture. Worked around in the MCS; adding them to
   the summary is the right single source.
6. Routine-ops power balance is slightly negative (−0.5 to −1.5 W with cooler
   and TX up), so a long payload day discharges into a load shed.

## Follow-ups not done

- Procedure cards need HK re-enable steps after every boot path
  (`obc_reboot.md` lists SIDs 1/2/3/5 but not 4, 6, 9, 12; the four
  bootloader/watchdog/switchover recovery cards have no re-enable step at all).
- The planner's interactive page still synthesises eclipse from a hard-coded
  35 min / 40 min offset — the last independent eclipse model in the tree.
- Consequence sweep from the orbit move: docs stating 450 km, the
  `POWER_SIZING.md` table at the new β, the FCC decay argument, courseware.
