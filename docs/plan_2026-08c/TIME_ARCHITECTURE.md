# Time architecture — SIM TIME vs ONBOARD TIME

Owner ruling, 2026-08-17. This document is the spec. Where it disagrees with any
other document, this wins; where it disagrees with the code, the code is wrong.

## The problem

There is currently no independent onboard clock. `obdh.obc_time_cuc` is advanced
by exactly the same `dt` that advances `engine._sim_time`, so onboard time is a
pure affine function of sim time. Worse, three different epochs are written into
that one field (J2000 at init, J2000 at t0 re-pin, **Unix** on GPS sync and on
S9.1 from the browser), so a GPS sync jumps the clock ~30 years and trips the
5-second-jump guard into SAFE_BOOT every time. Meanwhile the MCS timestamps
sim-time state with **wall clock** everywhere — archive rows, alarms, staleness,
pass timers — so pausing the simulator makes all telemetry go stale and
accelerating it makes nothing ever go stale.

## The three clocks

| Clock | Owner | Definition |
|---|---|---|
| **SIM TIME** | the simulator engine | absolute UTC of the simulated world. Starts at `orbit.yaml t0_epoch`, advances by `dt_sim = dt_real x speed`, stops when `speed == 0`. The single source of truth for *when things happen*. |
| **ONBOARD TIME** | the OBDH model | the OBC's own clock. Starts at **2000-01-01T12:00:00 UTC (CUC 0)** on first start-up and after every bootloader reset. Free-runs with drift; can be set by ground command or disciplined by GPS. |
| **MET** | derived | `sim_time - t0_epoch`. One definition, computed once in the simulator. |

Wall clock (`time.time()`, `Date.now()`) is legitimate for exactly three things:
real-time pacing of the run loop, real-time timeouts on network I/O, and
filenames/log lines that describe the *session* rather than the mission. Nothing
else may use it.

## Rules

1. **The spacecraft timestamps every packet it emits with ONBOARD TIME.** No
   exceptions — S1, S3, S5, S11 reports, S15 stored TM, S13 transfers.
2. **The ground segment (MCS and planner) works in SIM TIME.** Displays,
   staleness, alarm timestamps, the archive, command history, reports, pass
   predictions, plan activity times.
3. **The gap between them is the teaching point.** The console shows both, plus
   the correlation, and the operator's job in NOM-008 is to close it.
4. **One epoch on the wire: J2000** (2000-01-01T12:00:00 UTC). It is defined once
   and read from `mission.yaml time_epoch` — which is currently dead config.
5. Nothing outside the OBDH model may write onboard time. Nothing outside the
   engine may own a sim clock.

---

## Part A — CUC wire format (breaking change)

Replace the bare `uint32` of whole seconds with a proper ECSS CUC:

```
coarse : uint32 big-endian   seconds since J2000
fine   : uint16 big-endian   sub-second in units of 1/65536 s (~15.26 us)
```

The PUS secondary header grows **7 -> 9 bytes**:

```
byte 0   : 0x10 | pus_version
byte 1   : service
byte 2   : subtype
bytes 3-6: cuc coarse (uint32 BE)
bytes 7-8: cuc fine   (uint16 BE)
```

`smo_common.protocol.ecss_packet` gains:

- `CUC_FINE_TICKS = 65536`
- `@dataclass CucTime` with `coarse: int`, `fine: int`, and
  `to_seconds() -> float`, `from_seconds(float) -> CucTime`,
  `to_datetime() -> datetime`, `from_datetime(dt) -> CucTime`, `pack()/unpack()`.
- `SecondaryHeader` carries `cuc_coarse: int` and `cuc_fine: int`.
  **Keep a `cuc_time` property** that reads/writes `cuc_coarse`, so the many
  existing call sites that only care about whole seconds keep working; add
  `cuc_seconds -> float` for the full-precision value.
- `parse_cuc_time(coarse, fine=0) -> datetime` and
  `datetime_to_cuc(dt) -> tuple[int, int]`, with the single-value legacy forms
  kept as thin wrappers so nothing silently changes meaning.
- `TIME_EPOCH` stays the constant but is **loaded from `mission.yaml
  time_epoch`** at config load, with the literal as the fallback. Add a gate test
  that the YAML and the constant agree.

Everything downstream of this must be updated: `tm_builder`, the TC/TM decoders,
the MCS parser, XTCE and SMDL generation, the archive schema, the reporting
period selection, and `manual/12_time_and_clocks.md`.

---

## Part B — the onboard clock model (OBDH)

State on the OBDH model, replacing `obc_time_cuc: int`:

```python
obc_time_s: float      # seconds since J2000, sub-second resolution
time_ref: int          # see below
last_sync_s: float     # obc_time_s at the last successful sync, -1 if never
clock_drift_ppm: float # this unit's actual drift, drawn once per run
```

### `obdh.time_ref` values

| Value | Name | Meaning |
|---|---|---|
| 0 | `NONE` | never set since the last bootloader reset. Free-running from CUC 0. |
| 1 | `GROUND` | last set by ground command (S9.1). Free-running with drift. |
| 2 | `GPS` | GPS-disciplined. **No drift** — held to sim time every tick. |
| 3 | `HOLDOVER` | was GPS-disciplined, GPS fix lost. Free-running with drift from where it was. |

### Propagation, per tick

- `time_ref == GPS` **and** `aocs.gps_fix >= 2`:
  `obc_time_s = <sim time as CUC seconds>` exactly. No drift accumulates. This is
  the owner's ruling: *no drift when synced to GPS time*.
- Otherwise (`NONE`, `GROUND`, `HOLDOVER`):
  `obc_time_s += dt_sim * (1.0 + clock_drift_ppm * 1e-6)`

  Note this is a **float accumulation**, which also fixes an existing bug: the
  current `int(dt)` truncation freezes the OBC clock entirely at any non-integer
  sub-step (e.g. `speed = 2.5` at 1 Hz gives three 0.8333 s sub-steps and
  `int(0.8333) == 0`).

### Drift

`clock_drift_ppm` is drawn **once per run** from the seeded per-model RNG
(`smo_common.state.derive_rng`) so it is deterministic and reproducible:

```
clock_drift_ppm = clock_drift_ppm_nominal + N(0, clock_drift_sigma_ppm)
```

New `OBDHConfig` keys (declare them properly — `OBDHConfig` is a StrictModel, so
undeclared keys in YAML are REJECTED and the defaults stay live):

```yaml
clock_drift_ppm_nominal: 12.0    # a plausible TCXO
clock_drift_sigma_ppm: 4.0       # per-unit spread
clock_gps_discipline: true       # whether a GPS sync latches into disciplined mode
clock_jump_event_threshold_s: 1.0
```

At 12 ppm the clock gains ~1 s/day, so a sync is meaningful over a shift without
being absurd. Add a `clock_drift` failure injection that forces a large ppm
(order 500) so an instructor can make it bite inside a session.

### Transitions

- **S9.1 `SET_TIME`**: sets `obc_time_s` from the commanded CUC (coarse+fine),
  `time_ref = GROUND`, `last_sync_s = obc_time_s`. Emit an S5 event carrying the
  **signed jump in seconds**, not just "time set". Reject a set while in
  bootloader (already the case).
- **S8 func 80 `OBC_GPS_TIME_SYNC`**: requires `aocs.gps_fix >= 2`, else refuse
  with the existing failure code. On success sets `obc_time_s` from sim time,
  `time_ref = GPS`, `last_sync_s = obc_time_s`. Emit the jump event.
  **Fix the epoch bug**: it currently writes `int(sim_time.timestamp())`, a Unix
  timestamp, into a J2000 field.
- **GPS fix lost while `time_ref == GPS`**: `time_ref = HOLDOVER`, emit an event.
  **Fix regained**: back to `GPS`, re-discipline, emit an event.
- The existing "jump > 5 s forces AOCS to SAFE_BOOT" guard stays, but must now
  compare in the correct epoch — with the Unix/J2000 bug fixed it will stop
  firing spuriously on every GPS sync.

### Reboot and power (owner ruling)

- `OBC_REBOOT` -> bootloader mode. **The bootloader resets the clock**:
  `obc_time_s = 0.0`, `time_ref = NONE`, `last_sync_s = -1`, `uptime_s = 0`.
- `OBC_BOOT_APP` -> application mode. The clock is **not** restored; it is still
  at J2000 and must be re-synced. This is exactly the LEOP flow: boot the app,
  then run the time sync.
- Powering the `obc` line off and on has the same effect.
- All of this must survive a breakpoint save/restore correctly — add the new
  fields to the OBDH `_EXTRA_STATE_KEYS` / `get_state` / `set_state`. (The
  gyro_bias failure had exactly this bug last batch: a persistent state that
  silently healed on snapshot restore.)

### New telemetry

Verify the next free ids in the OBDH block before allocating; the intent is:

| id | name | type | notes |
|---|---|---|---|
| `0x0309` | `obdh.obc_time` | uint32 | **unchanged** — coarse CUC seconds |
| new | `obdh.obc_time_fine` | uint16 | sub-second, 1/65536 s |
| new | `obdh.time_ref` | uint8 | 0 NONE / 1 GROUND / 2 GPS / 3 HOLDOVER |
| new | `obdh.time_since_sync_s` | uint32 | onboard seconds since the last sync; saturates rather than wrapping. `0xFFFFFFFF` if never synced |

Declare them in `parameters.yaml`, add them to HK **SID 4 (Platform/OBDH)**, and
add ground limits where sensible (e.g. `time_since_sync_s` yellow at 12 h).

Do **not** downlink the true onboard-vs-sim offset — the spacecraft cannot know
it. Expose it on the **instructor console only**, as the ground-truth answer to
the exercise.

---

## Part C — the simulator's sim clock

1. **One sim clock.** `engine._sim_time` and `propagator._sim_utc` are currently
   two independent `datetime` objects that agree only by convention, and
   `apply_delta_v` resets one without the other. Make the engine own the clock
   and have the propagator read it (or be driven from it explicitly), so they
   cannot diverge. Keep the SGP4 call signature working on absolute UTC.
2. **MET.** `engine` exports `met_s = (sim_time - t0_epoch).total_seconds()`.
   Delete the export of `_sim_elapsed_fdir` as MET (it is the FDIR module's own
   accumulator and only coincidentally equal). Keep the FDIR accumulator for
   FDIR's internal use, under a name that says so.
3. **Packet timestamps.** `engine._get_cuc_time()` is the single seam. It must
   return the OBDH clock (coarse, fine) — never sim time, never wall clock.
   Remove the wall-clock fallback in `TMBuilder._get_cuc`: a missing time source
   is a bug and should raise, not silently stamp the packet with the host clock.
4. **S1 verification packets carry `cuc = 0`** today because `_pack_tm` is called
   without a `cuc=`. Fix it — they should carry onboard time like everything
   else. Check `reporting/dataset.py`'s `zero_cuc_policy` afterwards; it may
   become dead code, and if so, remove it rather than leave a policy for a case
   that can no longer arise.
5. **S15 stores.** `store_packet` is called without a timestamp, so
   `_oldest_ts`/`_newest_ts` are never populated for the HK/event/science stores.
   Pass the onboard CUC. The alarm store currently gets `sim_time.timestamp()` —
   a Unix float — change it to onboard CUC so it matches the packets inside it.
6. **S11 schedule** already compares against `_get_cuc_time()`, which is correct
   — it is an *onboard* schedule and must fire on *onboard* time. Keep that, and
   make sure the ground-side conversion (below) is on the same epoch. This is
   now a genuine teaching point: if the OBC clock is wrong, the schedule fires at
   the wrong sim time.
7. **Model helpers on the wrong epoch.** `eps_basic._get_time()` and
   `payload_basic._now()` return `sim_time.timestamp()` (Unix). Anything they
   feed into telemetry — notably `payload.last_calibration_time` (`0x062C`) —
   must be onboard CUC instead, since it is downlinked next to a CUC header.
   Anything purely internal to the model should use MET or sim seconds and say
   which.
8. **Dump filenames** use `datetime.utcnow()`. Name them from sim time; a session
   artefact may keep a wall-clock suffix if you want uniqueness, but the mission
   timestamp in the name must be sim time.

---

## Part D — MCS

Everything the MCS says about the mission must be on **sim time**. It already has
an open-loop `get_ground_utc()` anchored on the simulator; the work is to route
everything through it and kill the wall-clock call sites.

1. `get_ground_utc()` -> rename to something that says what it is (`get_sim_utc`
   / `sim_now`), keep the anchor-and-extrapolate mechanism, keep the jump
   detection.
2. **Staleness** (`server.py` TM receive timestamp, HK SID last-seen, observed
   cadence) must age in **sim seconds**. Thresholds in `mcs/staleness.yaml` are
   already sim seconds, so today they are compared against wall ages: pause makes
   everything stale, acceleration makes nothing stale. Fix both.
3. **Alarms** — raise and clear timestamps on sim time.
4. **The archive** (`tm_archive.py`) — every `utc_epoch` default is
   `time.time()`. Require an explicit epoch at every call site and pass sim time,
   so an archived parameter can be correlated with a packet's CUC. Note the
   archive will then contain sim-time rows; add a schema version or a column note
   so an old database is not silently misread.
5. **Command history** — stamp with sim time, and record the onboard CUC the
   command was *scheduled* for where one exists.
6. **Remove the `> 1e9` heuristic** in the `0x0309` decode. It exists only to
   paper over the Unix/J2000 confusion, and it means `sc_time` is permanently
   `None` today and starts "working" only after a GPS sync writes a Unix value.
   Use the proper decode.
7. **Fix the browser's epoch bugs.** `s9SyncTime` and `s11ResolveTimeTag` in
   `static/index.html` encode CUC as **Unix** seconds while the server encodes
   J2000; the comment there asserting they match is false. Consequence today: an
   S11 time tag set from the browser is ~9.5e8 s in the future and **never
   fires**. Route both through one helper.
8. **The UI's own clocks.** ~30 `Date.now()` sites. The pass AOS/LOS timers run
   on wall clock against a sim-time pass window; freshness checks compare
   `Date.now()` against server wall timestamps. Give the front end a single
   `simNow()` derived from the server's sim time and speed, and use it for
   everything mission-related. `updateTimeCorrelation()` is broken twice over —
   it treats `state.sim_time` (an ISO string) as `new Date(simTime * 1000)`.
9. **Show the correlation.** The console should display SIM/ground UTC, onboard
   time, `time_ref`, and time-since-sync side by side, and flag when the offset
   exceeds a threshold. This is the operator-facing half of the whole change.

---

## Part E — planner

The planner already anchors on the simulator's sim time; confirm and finish it.

1. All activity times stay absolute ISO-8601 UTC on **sim time**.
2. Take MET from the simulator's single definition rather than deriving a third.
3. `activity_scheduler` sorts ISO strings lexically (`"Simple string comparison
   (ISO format is sortable)"`) — that is wrong for mixed offsets and `Z` forms.
   Parse and sort as datetimes.
4. Plan upload -> S11: the conversion from sim-time activity start to **onboard
   CUC** must go through the MCS's time correlation, not through a naive
   `datetime_to_cuc`. If the OBC clock is off by 40 s, the schedule must fire 40 s
   early in sim time — that is the correct, teachable behaviour, and the planner
   must not silently correct for it.

---

## Part F — documentation and courseware

- `configs/eosat1/manual/12_time_and_clocks.md` currently describes three clean
  domains that the code does not implement. Rewrite it to match this spec.
- `mission.yaml time_epoch` becomes live config; say so.
- NOM-008 (Clock Synchronisation) and LEOP-006 (Time Sync) both need rewriting
  against the real behaviour: the drift rate, what `time_ref` shows, what the
  jump event says, and why a reboot means a re-sync.
- The courseware in `SpaceCDF/docs/course/` describes time correlation in several
  places; those need a pass once the code lands. **Out of scope for this batch —
  log it in the outstanding list.**

## Acceptance

- A run with `speed = 1` and no sync shows the onboard clock drifting away from
  sim time at the configured ppm, visible in telemetry.
- A GPS sync closes the gap to zero and holds it there while the fix is good.
- Losing the GPS fix moves `time_ref` to HOLDOVER and drift resumes.
- `OBC_REBOOT` returns the clock to J2000 and `time_ref` to NONE.
- Pausing the simulator freezes all MCS ages; accelerating it ages them at speed.
- An S11 time tag set from the browser fires at the intended time.
- Determinism holds: same seed and same `t0_epoch` gives an identical run,
  including the drawn drift.
