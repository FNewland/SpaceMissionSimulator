# EPS display requirements — for the single MCS owner (Phase 6)

**From:** EPS/power workstream (2026-08b Phases 0, 2a, 2b, 2c)
**To:** the single MCS owner, per COHERENCE_REVIEW §2.5 and §4
**Status:** requirements only. No MCS file has been touched by this workstream.

Every parameter named below **already exists and is already streamed** — none
of these requests needs new telemetry, a new SID, or a model change. Where an
item does depend on something this workstream changed, it says so.

---

## R-1 — `soc_trend` thresholds are calibrated for a spacecraft that does not exist (CORRECTNESS)

**File:** `packages/smo-mcs/src/smo_mcs/displays/power_budget.py`,
`PowerBudget.soc_trend`.

```python
if self.power_margin_w > 50:      return "charging"
elif self.power_margin_w < -10:   return "discharging"
else:                             return "stable"
```

EO-Sat-1's *entire* generation is ~85 W sunlit and ~38 W orbit-average, and
after the R1 re-sizing (POWER_SIZING.md) peak generation is 88.6 W. A +50 W
margin is therefore reachable only in a quiescent, wings-deployed, low-beta,
sunlit corner case. **In normal nominal operations — which is a real, healthy,
battery-charging state at +9 to +16 W — this display says "stable".** The
operator is told nothing is happening while the battery charges, and the one
word on the panel that is supposed to answer "am I gaining or losing energy?"
is wrong for most of the mission.

**Requested:** thresholds proportional to the spacecraft, and a dead-band that
reflects the measurement noise (`power_cons_w` carries a 1 W-sigma noise term),
e.g.

```python
if   self.power_margin_w >  +3.0: return "charging"
elif self.power_margin_w <  -3.0: return "discharging"
else:                             return "stable"
```

Better still, derive it from SoC rate rather than instantaneous margin, so a
single noisy sample cannot flip the label.

---

## R-2 — The breakdown panel reads SID-8-only parameters, so it says NO DATA by default (CORRECTNESS)

`PowerBudgetMonitor._SUBSYS_POWER_KEYS` reads `subsys_power_eps` …
`subsys_power_obdh` = **0x013C–0x0141**, which live **only in HK SID 8**. SID 8
is not in `simulation.yaml`'s `default_enabled_sids` `[1, 11]`, so unless an
operator has explicitly sent `TC(3,5) HK_ENABLE sid=8`, the entire
per-subsystem breakdown is empty on a fresh session. The panel does not say
why; it just shows nothing.

**Requested:** the panel must state its dependency — a "SID 8 not enabled —
send HK_ENABLE sid=8 to populate" affordance rather than a blank table — or
the MCS should enable SID 8 as part of its standard connect sequence.

**Note (changed this batch):** the breakdown now *reconciles*. Previously
0x013C (EPS) was hard-wired 0.0 and the propulsion line, an injected 80 W bus
short and the measurement noise belonged to no bucket, so the six values did
not sum to 0x0106. They now sum to it identically, so the panel can safely
display a TOTAL row and compare it against `power_cons` — please add that row,
because it is the check that would have caught the old defect.

---

## R-3 — Wing status must be on the power display (HIGH PRIORITY)

`wing_status` **0x0144** is now in **SID 1** (added this batch — it was
SID-8-only, i.e. invisible by default). This is the single biggest contributor
to Franz's reported "generation drops off way too fast": an operator can run a
whole session with the wings stowed at 18.6 W generation and nothing on any
display says so.

**Requested:** a permanent, prominent indicator on the power display:

| 0x0144 bits | Meaning | Suggested rendering |
|---|---|---|
| `0x00` | both wings STOWED | **red** "WINGS STOWED — generation limited to body panels" |
| `0x01` / `0x02` | one wing deployed | amber "+Y ONLY" / "−Y ONLY" |
| `0x03` | both deployed | green "WINGS DEPLOYED" |
| `0x10` / `0x20` | deploy in progress | pulsing, with `wing_deploy_timer` 0x0145 |

It should be visible without opening any sub-panel. A stowed wing is not a
failure, so it will not raise an alarm — which is exactly why it needs to be
on the face of the display.

---

## R-4 — Per-line power table (no new telemetry required)

**0x0110–0x0117** (line on/off) and **0x0118–0x011F** (per-line currents) are
**already in SID 1**, i.e. already arriving at 1 Hz on every session, and are
currently not surfaced as a table. Plus **0x014A / 0x014B** for the propulsion
line (on-demand).

**Requested:** one row per line — name, commanded state, current (A), derived
power (I × `bus_voltage` 0x0105), and the overcurrent trip flag — with a TOTAL
row that must equal `power_cons` 0x0106.

| Line | Params | Notes |
|---|---|---|
| obc | — (non-switchable) | 6 W constant |
| ttc_rx | — (non-switchable) | 1.5 W constant |
| ttc_tx | 0x0110 / 0x0118 | tier-dependent: 5 W LOW, 11 W HIGH |
| payload | 0x0111 / 0x0119 | 2.4 W standby, 5.5 W imaging |
| fpa_cooler | 0x0112 / 0x011A | 15 W |
| htr_bat | 0x0113 / 0x011B | 8 W, thermostat-duty |
| htr_obc | 0x0114 / 0x011C | 4 W |
| aocs_wheels | 0x0115 / 0x011D | mode-dependent 4 / 9 / 15 W |
| prop | 0x014A / 0x014B | catbed 12 W + valves 2 W, armed only |

This is the display an operator needs to answer "what is drawing my power?",
and it costs nothing to build because the data is already on the ground.

---

## R-5 — Generation-side detail

Currently the display shows only `power_gen` (0x0107). Everything needed for a
diagnosis is already streamed in SID 1:

| Parameter | ID | Use |
|---|---|---|
| per-panel currents | 0x012B–0x0130 | which face is producing — isolates a single-panel failure |
| SA A / B currents | 0x0103 / 0x0104 | string-level health |
| SA A / B voltages | (0x0124/0x0125 if present) | MPP voltage, 32.4 V when generating, 0 when dark |
| solar array drive angle | 0x0133 | **now load-bearing** (see below) |
| array age factor | 0x0123 | aging, 1.0 → 0.9175 at 3-year EOL |
| eclipse flag | 0x0108 | context for a 0 W reading |
| per-wing currents | 0x0146 / 0x0147 (SID 8) | wing-only, excluding the ±Y body faces |

**Requested:** a generation panel that renders the six faces plus the two
wings, so the three states that look identical on a single "power_gen" number
become visually distinct:

1. **eclipse** — everything zero, 0x0108 set;
2. **wings stowed** — body faces producing, wing currents zero, 0x0144 = 0;
3. **solar array drive OFF** (device 0x0101) — body faces producing, wing
   currents zero, 0x0144 = 3. *(Changed this batch: the device gate used to
   zero the body panels too, so this state was indistinguishable from a total
   array loss. It now leaves ~15–18 W of body generation, and that residual is
   the diagnosis.)*
4. **SADA stuck** (new `sada_stuck` failure) — 0x0133 flat while the wing
   currents develop a once-per-orbit |sin| ripple.

A small "SADA: TRACKING / STUCK / MANUAL, angle X°" readout next to 0x0133
would make case 4 immediately readable.

---

## R-6 — Wh/orbit rolling integral

A single instantaneous margin number cannot answer the question operators
actually have, which is "am I closing the energy budget over the orbit?" —
because the margin is positive in sunlight and negative in eclipse *by design*.

**Requested:** a rolling integral of (`power_gen` − `power_cons`) over a
trailing orbit period (5670 s), displayed in Wh/orbit alongside the
per-orbit minimum SoC. `tools/power_budget.py` reports the same quantity, so
the display and the sizing tool would speak the same units — the nominal
profile is +14.7 Wh/orbit at worst beta, +25.3 Wh/orbit at max-eclipse beta.

Suggested colouring: green ≥ 0, amber −10 … 0, red < −10 Wh/orbit.

---

## R-7 — Surface the instructor SoC override in the event log (NEW, Phase 2c)

The new `set_battery_soc` instructor command emits an event whose description
begins `INSTRUCTOR OVERRIDE: battery SoC set X % -> Y % (not physics)`.

**Requested:** render events whose description starts `INSTRUCTOR OVERRIDE`
distinctly (a badge, not just severity 2), so a student reviewing the log can
never mistake an instructor's intervention for spacecraft behaviour. That
distinguishability is the whole reason the override was implemented as a
first-class command rather than as a fake failure.

---

## R-8 — Staleness (already on the MCS owner's list)

Noted for completeness: the cadence × 1.5 staleness fix is a prerequisite for
R-2 and R-5. SID 8 runs at 8 s, so anything sourced from it is born wearing a
permanent STALE badge under the current hard-coded 8 s / 15 s rules.
