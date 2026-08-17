# EOSAT-1 Time and Clocks

**Document ID:** EOSAT1-UM-TIME-012
**Issue:** 2.0
**Date:** 2026-08-17
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

EOSAT-1 has **two clocks that can disagree**, plus one derived count:

* **SIM TIME** — the UTC of the simulated world. The ground segment works in it.
* **ONBOARD TIME** — the OBC's own clock. The spacecraft timestamps every packet
  it emits with it.
* **MET** — Mission Elapsed Time, derived from sim time.

The gap between SIM TIME and ONBOARD TIME is not a defect of the simulation; it
is what this chapter is about. The onboard oscillator drifts, the clock restarts
at the epoch after every reboot, and nothing corrects it unless GPS is
disciplining it. Closing that gap is an operator task — see **NOM-008 Clock
Synchronisation** and **LEOP-006 Time Synchronisation**.

A third clock, the **wall clock** of the machine you are sitting at, is not a
mission clock at all. It is legitimate for exactly three things: pacing the run
loop, network timeouts, and session log/file names. If a mission-time display
tracks your desk clock instead of sim time, that is a bug.

## 2. The clocks

| Clock | Owner | Definition | Behaviour |
|---|---|---|---|
| **SIM TIME** | simulator engine | absolute UTC of the simulated world. Starts at `orbit.yaml` `t0_epoch`. | Advances by `dt_real x speed`. **Stops when `speed = 0`.** The single source of truth for *when things happen*. |
| **ONBOARD TIME** | OBDH model | the OBC clock, in CUC seconds since the mission epoch. Starts at **CUC 0 = 2000-01-01T12:00:00 UTC (J2000)** on first start-up and after every bootloader reset. | Free-runs with oscillator drift; can be stepped by ground command (S9,1) or disciplined by GPS (S8 func 80). |
| **MET** | derived, in the simulator | `sim_time - t0_epoch`, in seconds. | Computed **once**, in the engine, and published as `met_s` on `/api/state`. The MCS and Planner read that value; they do not recompute it. |

Rules that follow:

1. The spacecraft timestamps **every** packet it emits with ONBOARD TIME — S1
   verification, S3 housekeeping, S5 events, S11 reports, S15 stored TM, S13
   transfers. No exceptions.
2. The ground segment (MCS and Planner) works in **SIM TIME** — displays,
   staleness, alarm times, the archive, command history, pass predictions and
   plan activity times.
3. **One epoch on the wire: J2000.** It is declared once, in
   `configs/eosat1/mission.yaml`:

   ```yaml
   time_epoch: "2000-01-01T12:00:00"   # live config, not decoration
   ```

   It is loaded at config load and pushed into the packet layer; the Python
   literal is only a fallback for running without a config tree.

## 3. The CUC time format

Onboard time travels as an ECSS CUC (CCSDS Unsegmented Time Code):

```
coarse : uint32 big-endian   whole seconds since the mission epoch (J2000)
fine   : uint16 big-endian   sub-second, in units of 1/65536 s  (~15.26 us)
```

It sits in the **PUS TM secondary header**, which is **9 octets**:

```
offset  size  field
 0       1    0x10 | pus_version
 1       1    service
 2       1    subtype
 3       4    CUC coarse (uint32 BE)
 7       2    CUC fine   (uint16 BE)
              ── 9 octets total ──
```

Consequences worth knowing at the console:

* the primary header occupies octets 0-5, the secondary header 6-14, and the
  **TM data field starts at octet 15** (it was 13 before the CUC change);
* TC packets are unaffected — the TC secondary header is still 3 octets
  `[0x20, service, subtype]` and carries no time;
* the S5 event body still carries a 4-octet whole-second CUC of its own. That
  is the *coarse* value of the header time, kept for compatibility; the header
  is the authoritative, full-precision timestamp;
* dump files written before the change cannot be decoded by the current tools —
  the data field is misaligned by two octets and the CRC fails. Delete
  pre-migration dumps rather than trying to read them.

## 4. `time_ref` — how the OBC believes it knows the time

`obdh.time_ref` (**0x0A13**, HK SID 4) is the OBC's own statement about its time
reference. Read it before you read the clock.

| Value | Name | Meaning | Drifts? |
|---|---|---|---|
| 0 | `NONE` | never set since the last bootloader reset. Free-running from CUC 0 — it thinks it is 1 January 2000. | yes |
| 1 | `GROUND` | last set by ground command (S9,1 `SET_TIME`). | yes |
| 2 | `GPS` | GPS-disciplined and the fix is good. Held to true time every tick. | **no** |
| 3 | `HOLDOVER` | was GPS-disciplined, the fix has been lost. Free-running from wherever it was. | yes |

Companion parameters, all in **HK SID 4 (Platform/OBDH, 8 s interval)**:

| Parameter | ID | Type | Notes |
|---|---|---|---|
| `obdh.obc_time` | `0x0309` | uint32 | coarse CUC seconds |
| `obdh.obc_time_fine` | `0x0A12` | uint16 | sub-second, 1/65536 s |
| `obdh.time_ref` | `0x0A13` | uint8 | table above |
| `obdh.time_since_sync_s` | `0x0A14` | uint32 | onboard seconds since the last successful sync. Saturates rather than wrapping; `4294967295` (0xFFFFFFFF) means **never synced**. Ground limit: yellow at 12 h, red at 24 h |

`obdh.uptime` (`0x0308`) is seconds since **boot**, not since the epoch. It is
useful for spotting a reboot and for a rate check; it is never a timestamp.

## 5. The drift model

The oscillator is a plausible TCXO. Its actual drift is drawn **once per run**
from the seeded per-model RNG, so a given seed always produces the same clock:

```
clock_drift_ppm = clock_drift_ppm_nominal + N(0, clock_drift_sigma_ppm)
```

`configs/eosat1/subsystems/obdh.yaml`:

```yaml
clock_drift_ppm_nominal: 12.0     # ~1 s/day
clock_drift_sigma_ppm: 4.0        # per-unit spread
clock_gps_discipline: true        # a GPS sync latches into disciplined mode
clock_jump_event_threshold_s: 1.0 # |jump| that raises an S5 clock event
clock_drift_failure_ppm: 500.0    # ppm forced by the `clock_drift` injection
```

At the nominal 12 ppm the clock gains roughly **1 second per day** — worth a
sync once a shift, visible but not dramatic after a week. Per tick, when not
GPS-disciplined:

```
obc_time_s += dt_sim * (1 + clock_drift_ppm * 1e-6)
```

An instructor can make the drift bite inside a session with the `clock_drift`
failure injection, which forces order **500 ppm (~43 s/day)**.

Because drift is a *rate*, the error you measure grows with
`time_since_sync_s`. A 3-second offset with `time_since_sync_s` = 3 days is a
healthy ~12 ppm clock. The same 3 seconds ten minutes after a sync is not.

## 6. GPS discipline and holdover

* **`OBC_GPS_TIME_SYNC`** (S8, func 80) requires a 3D fix or better —
  `aocs.gps_fix` (`0x0274`) >= 2. Without it the command is refused.
* On success the clock is set to true time, `time_ref` becomes **GPS** and
  `time_since_sync_s` restarts. While `time_ref = GPS` and the fix holds, the
  clock is held to true time every tick and **accumulates no drift at all**.
* **Losing the fix** moves `time_ref` to **HOLDOVER**, raises
  `OBC_TIME_REF_LOST` (`0x030E`), and drift resumes from where the clock was.
* **Regaining the fix** re-disciplines automatically, raises `OBC_TIME_SYNC`
  (`0x030D`) with the jump it had to make, and returns `time_ref` to GPS.
* With `clock_gps_discipline: false` a GPS sync would be a one-shot set
  (`time_ref = GROUND`) and drift would continue. It ships as `true`.

A ground set and a GPS sync are **not** the same operation:

| | ground `SET_TIME` (S9,1) | `OBC_GPS_TIME_SYNC` (S8 func 80) |
|---|---|---|
| source of truth | your ground clock, corrected for light time and processing delay | the spacecraft's own GPS receiver |
| resulting `time_ref` | `GROUND` | `GPS` (then `HOLDOVER` if the fix drops) |
| drift afterwards | continues at the drawn ppm | none while the fix is good |
| accuracy | as good as your correction arithmetic | tracks true time exactly |
| needs a pass | yes — it is an uplinked command | yes to command it, but re-acquisition afterwards is autonomous |
| available in the bootloader | **no** — rejected pre-boot | no |

## 7. Clock events

Every clock step raises an S5 event carrying the **signed jump in seconds** —
"time set" alone would not tell you how far out the clock was, which is the
point of the exercise. Jumps smaller than `clock_jump_event_threshold_s`
(1.0 s) are suppressed, so a routine re-sync of a healthy clock does not fill
the event log.

| Event | ID | Raised by |
|---|---|---|
| `OBC_TIME_SET` | `0x030C` | S9,1 ground set. Text of the form `OBC clock set by ground (S9,1): jump +12.345s, time_ref=GROUND` |
| `OBC_TIME_SYNC` | `0x030D` | GPS sync — commanded, or automatic on re-acquisition after holdover |
| `OBC_TIME_REF_LOST` | `0x030E` | GPS fix lost while disciplined; `time_ref` -> HOLDOVER, drift resumes |

One further consequence: a GPS sync whose jump exceeds **5 s** forces AOCS to
SAFE_BOOT (event `0x020F`), because the attitude solution was computed against
a clock that has just moved. Expect it after a long holdover or a reboot; it is
a real effect, not an artefact.

## 8. Reboot behaviour

**The bootloader resets the clock.** On `OBC_REBOOT`, or on powering the `obc`
EPS line off and on again:

* `obc_time_s = 0` — the OBC is back at J2000;
* `time_ref = NONE`;
* `time_since_sync_s` reads never-synced (`0xFFFFFFFF`);
* `uptime` = 0 and `reboot_count` increments.

Booting the application image (`OBC_BOOT_APP`) does **not** restore the time.
The clock is still at J2000 and must be re-synchronised — exactly the LEOP flow:
boot the app, then run the time sync. `SET_TIME` is not in the bootloader
command allowlist, so it can only be sent once the application image is running.

A clock reading a date in the year 2000 is therefore not an epoch bug; it is an
un-synced OBC. Check `obdh.uptime` and `obdh.reboot_count` before concluding
anything about drift.

## 9. Reading the correlation on the console

The MCS console shows, side by side:

* **SIM UTC** — ground/sim time, the clock everything on the ground runs on;
* **ONBOARD** — `obdh.obc_time` + `obdh.obc_time_fine` decoded against the
  mission epoch;
* **`time_ref`** — NONE / GROUND / GPS / HOLDOVER;
* **time since sync** — `obdh.time_since_sync_s`, or "never".

The number that matters is the **offset**, `ONBOARD - SIM UTC`; the console
flags it once it exceeds the display threshold. Both readings age: the onboard
value is only as fresh as the last SID 4 packet, so at an 8 s cadence expect up
to ~8 s of staleness plus light time. That is why NOM-008 has you request a
fresh HK SID 4 and an explicit TM(9,2) time report rather than reading whatever
happens to be on screen.

Two independent routes carry the same clock — the TM(9,2) time report and
`0x0309` in HK SID 4. Cross-check them: a disagreement larger than the SID 4
collection interval means the time service and the housekeeping sampler
disagree about the clock, which is a different fault from the clock being wrong.

The spacecraft cannot know its own offset from true time, so it is not
downlinked. The simulator's instructor view holds the ground-truth value; it is
the answer to the exercise, not an operational display.

## 10. Time-tagged commands (S11) and the planner

The onboard schedule fires on **ONBOARD TIME**. It is the spacecraft's own
schedule and has nothing else to run on.

The Planner works in sim time: activity start times are absolute ISO-8601 UTC
sim-time instants. On upload the Planner sends that sim-time instant to the MCS,
and the **MCS** performs the single conversion to a CUC time tag through its
time correlation (anchored on `0x0309`), so the tag lands on the OBC's own
clock.

Nothing in the chain hides a clock error, and that is deliberate:

* with a current correlation, the tag is expressed on the OBC's clock and the
  activity executes at the planned sim time even while the clock is out;
* if the clock has moved since the correlation was taken — the usual case for a
  plan uplinked on one pass and executed on the next, with drift in between —
  the activity fires **early by the amount the clock is fast** and late by the
  amount it is slow. A 40 s fast clock runs your plan 40 s early.

The Planner will not pre-compensate. Bending the plan to fit a wrong clock
leaves the clock wrong and hides the reason to fix it. The remedy is a clock
sync before the plan matters; after the sync the same plan, unchanged, fires
when it says it does.

If the clock has never been set (`time_ref = NONE`, still near J2000), be
especially careful with time tags computed straight from the epoch: such a tag
may be decades in the OBC's future, and the command simply never fires.

## 11. MET and where it comes from

`MET = sim_time - t0_epoch`. It is computed in the simulator and published as
`met_s` on `/api/state`. The Planner reads it from there and carries it forward
with its sim-clock anchor; the MCS does the same. No component recomputes MET
from its own idea of a start epoch — one definition, one number, everywhere.

MET is not an onboard quantity and is not downlinked. Do not confuse it with
`obdh.uptime`, which counts from the last OBC boot, or with the FDIR module's
internal elapsed-time accumulator.

## 12. The sim-vs-real switch

A single setting selects whether the **MCS** and **Planner** follow simulation
time or real UTC. It does **not** affect spacecraft time: onboard time always
comes from telemetry and is never synthesised on the ground.

### 12.1 Precedence

For each service (first wins):

1. **CLI flag** — `--time-source sim|real`
2. **Environment variable** — `SMO_TIME_SOURCE` (`sim` | `real`)
3. **Mission-config field** — `time_source:` in `configs/eosat1/mission.yaml`
4. **Built-in default** — `"sim"`

The companion `sim_state_url` (the simulator endpoint polled for `sim_time`,
`speed` and `met_s`) resolves the same way:

1. `--sim-state-url <url>`
2. `SMO_SIM_STATE_URL`
3. `sim_state_url:` in `mission.yaml`
4. Default `http://<connect_host>:8080/api/state`

### 12.2 Mission config

```yaml
time_epoch: "2000-01-01T12:00:00"   # the CUC epoch — live config
time_source: sim                    # "sim" | "real"
# sim_state_url: http://localhost:8080/api/state   # optional override
```

### 12.3 start.sh

```bash
SMO_TIME_SOURCE="${SMO_TIME_SOURCE:-sim}"          # --real sets this to real
export SMO_SIM_STATE_URL="${SMO_SIM_STATE_URL:-http://localhost:8080/api/state}"
```

- `./start.sh` -> time source **sim** (default).
- `./start.sh --real` -> **real** (wall clock).
- `SMO_TIME_SOURCE=real ./start.sh` -> real, via env.

The active time source is echoed in the startup banner.

### 12.4 Closed-loop sim sync

In sim mode the ground clock is closed-loop synced to the simulator rather than
reconstructed locally:

* the **MCS** polls `sim_state_url` every ~1.5 s, reads `sim_time` and `speed`
  and re-anchors its clock; between polls it extrapolates by `speed`, so
  worst-case drift is bounded by the poll interval. This tracks pause, speed
  change and breakpoint load correctly;
* the **Planner** keeps the same kind of anchor (`_sim_anchor_time` /
  `_sim_anchor_wall` / `_sim_anchor_speed`), refreshed at most ~1/s, and returns
  `anchor_sim_time + (now - anchor_wall) x speed`. A discontinuity larger than
  the configured tolerance is treated as a jump: contact and ground-track caches
  are dropped immediately.

**Fallbacks:** on a failed poll both services keep the last anchor. If the
simulator was never reachable they fall back to wall-clock UTC and say so — the
Planner reports `wall_clock_fallback` on `/api/time-source` and logs a warning,
because a SIM badge over wall-clock data is how operators end up planning
against the wrong day.

Ground ages are measured in **sim** seconds: pausing the simulator freezes
staleness rather than making everything go stale, and running at 60x ages
telemetry 60x faster. That is intended.

## 13. Caveats

* **Restart caveat.** The time source and `sim_state_url` are resolved when the
  MCS/Planner is constructed. Changing the config field, env var or CLI flag
  requires restarting the affected service.
* The simulator's own timekeeping is authoritative in sim mode and must not be
  altered by ground configuration.
* Nothing outside the OBDH model may write onboard time; nothing outside the
  engine may own a sim clock.

## 14. Quick diagnosis

| Symptom | Look at | Likely cause |
|---|---|---|
| Onboard time reads year 2000 | `time_ref` = NONE, small `uptime`, `reboot_count` up | OBC rebooted; the bootloader reset the clock. Re-sync (LEOP-006, or NOM-008 Steps 3-5) |
| Offset grows steadily at ~1 s/day | `time_ref` = GROUND or HOLDOVER, `time_since_sync_s` | Normal oscillator drift at ~12 ppm |
| Offset grows ~40x faster than that | same, in an instructor session | `clock_drift` failure injection (~500 ppm) |
| Offset frozen at zero | `time_ref` = GPS | GPS-disciplined; correct behaviour |
| `time_ref` flipped to HOLDOVER | event `0x030E`, `aocs.gps_fix` < 2 | GPS fix lost; drift has resumed |
| GPS sync refused | `aocs.gps_fix` (`0x0274`) | Needs a 3D fix (>= 2) |
| `SET_TIME` rejected | `obdh.sw_image` (`0x0311`) | Still in the bootloader; boot the application image first |
| AOCS dropped to SAFE_BOOT right after a sync | event `0x020F`, jump in `0x030D` | The sync jump exceeded 5 s |
| Scheduled activity fired early or late | the offset and `time_since_sync_s` at upload | A clock error, not a planning error. Sync, then re-check |
