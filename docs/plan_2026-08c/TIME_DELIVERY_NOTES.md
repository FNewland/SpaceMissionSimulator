# Time architecture — delivery notes, 2026-08-17

Spec: `TIME_ARCHITECTURE.md`. Wire-format detail: `CUC_MIGRATION.md`.
Suite after the change: **5998 passed, 10 skipped, 3 xfailed, 0 failed** (17 min).

## What you now have

**SIM TIME** is owned by the engine and is the absolute UTC of the simulated
world. **ONBOARD TIME** is owned by the OBDH model, starts at
2000-01-01T12:00:00 (CUC 0), free-runs with drift, and is the only thing the
spacecraft stamps packets with. **MET** is `sim_time - t0_epoch`, defined once.
The MCS and planner work in sim time throughout.

The spacecraft's clock now genuinely drifts: 12 ppm nominal with a 4 ppm
per-unit spread, drawn once per run from the seeded RNG so determinism holds.
That is about a second a day — visible over a shift, not absurd. A GPS sync
latches into disciplined mode and holds the clock exactly; losing the fix drops
to HOLDOVER and drift resumes from where it was; regaining it re-disciplines.
A reboot drops to the bootloader, which resets the clock to J2000, and coming
back up in application mode does **not** restore it — so LEOP's boot-then-sync
order is now load-bearing rather than ceremonial. There is a `clock_drift`
failure injection at 500 ppm for making it bite inside a session.

`obdh.time_ref` (0x0A13) shows NONE / GROUND / GPS / HOLDOVER,
`obdh.obc_time_fine` (0x0A12) carries the sub-second, and
`obdh.time_since_sync_s` (0x0A14) counts up from the last sync with a 12 h
yellow limit. All three are in HK SID 4.

**0x0A14 deliberately has no S12 definition.** The OBC cannot detect that its
own clock is wrong — that is the point of the exercise — so it is a ground-only
limit, and the ground-only canary count moved 30 -> 31 for exactly that reason.

The true onboard-vs-sim offset is on the **instructor console only**, next to
the drift ppm actually drawn for this run. A leak test asserts it appears in no
telemetry parameter and not in the state summary the MCS polls.

## Bugs this uncovered and closed

- **A GPS sync jumped the clock ~30 years.** The sync path wrote
  `int(sim_time.timestamp())` — a Unix timestamp — into a J2000 field. The
  5-second-jump guard then dutifully forced AOCS into SAFE_BOOT on every single
  sync.
- **An S11 time tag set from the browser never fired.** The browser encoded CUC
  as Unix seconds while the server encoded J2000, so every tag landed ~9.5e8 s
  in the future. The comment in the browser asserting the two matched was false.
- **The onboard clock froze entirely at non-integer sub-steps.** It advanced by
  `int(dt)`, so at `speed = 2.5` on a 1 Hz tick — three 0.8333 s sub-steps —
  `int(0.8333) == 0` and the clock and uptime simply stopped while sim time ran
  on. This was the only place the two clocks decoupled today, by accident.
- **Pausing the simulator made all telemetry go stale**, and accelerating it made
  nothing ever go stale: staleness thresholds are in sim seconds but were being
  compared against wall-clock ages, in ~20 server sites and ~30 browser sites.
- **`sc_time` was permanently `None` on the console.** A `> 1e9` heuristic on the
  0x0309 decode existed only to paper over the Unix/J2000 confusion; a healthy
  J2000 value is ~8.4e8, so it never rendered — and would have started
  "working", thirty years wrong, only after 2031. The same heuristic was still
  live in the browser's OBDH tab after the server side was fixed.
- **S1 verification packets carried `cuc = 0`**, and the reporting layer had a
  whole `zero_cuc_policy` to cope. Both are gone — and removing the policy was
  necessary rather than tidy, because CUC 0 is now a *legitimate* time (the clock
  starts there out of a bootloader reset), so a policy reinterpreting 0 as
  "unknown" would have actively misplaced real post-reboot telemetry.
- **Two independent sim clocks.** `engine._sim_time` and `propagator._sim_utc`
  were separate objects agreeing only by convention, and `apply_delta_v` reset
  one without the other. The engine now owns the clock and drives the
  propagator; `engine.sim_time == engine.orbit.utc` is an invariant.
- **MET was the FDIR module's accumulator**, exported as if it were mission
  elapsed time, with the MCS deriving a third definition of its own.
- **S15 stores had no timestamps at all** for the HK/event/science stores, and
  the alarm store carried a Unix float while the packets inside it carried J2000
  CUC. Also, a circular store's `oldest_ts` froze at the first packet ever
  stored instead of following eviction.
- **The archive mixed wall-clock rows with sim-time state**, so an archived
  parameter could not be correlated to a packet's CUC.
- `mission.yaml time_epoch` was dead config duplicating a Python literal — a
  trap for exactly this kind of change. It is now the live source, with a gate
  test that the two agree.

## One decision that needs your ruling

My spec said the planner "must not silently correct for" a wrong OBC clock, so
that a 40 s error makes an S11 schedule fire 40 s early. On implementation that
turned out to be half wrong, and the agent flagged it rather than faking the
algebra.

What actually happens: the ground converts a sim-time instant to an onboard CUC
using the **measured time correlation** from TM 0x0309 — which is what time
correlation is *for*. With a current correlation, a known 40 s offset is
compensated and the schedule fires on time. The error that bites is the drift
accumulated **since the correlation sample**, which is the realistic
uplink-this-pass, execute-next-pass case.

Both branches are implemented and tested. The planner uploads a sim-time ISO
instant and does no conversion; the single conversion lives in the MCS. If you
want the harsher teaching behaviour — no compensation at all, so a clock error
always shows up as a timing error — say so and it is a small change.

## Follow-up not done here

- **The courseware describes time correlation in several places** and has not
  been swept against this change. `SpaceCDF/docs/course/` — the Ch 13 operator
  calculations, the LEOP and nominal walkthroughs, and both workbooks. The
  simulator-side docs *are* done: `manual/12_time_and_clocks.md` is rewritten,
  and NOM-008 and LEOP-006 are rewritten against the real behaviour.
- Pre-migration dump files are unreadable: the old 7-byte-header packets carry no
  version flag to distinguish them. Existing archive databases are detected and
  flagged as `v1 / time_base='wall'` rather than silently misread, but there is
  no conversion — start a fresh file.
- `smo-rfsim`'s `frontend.py` stamps `vc_active` and `status.timestamp` with wall
  clock. Both ends of every comparison are wall clock so nothing mixes epochs,
  and it reads as real-time DSP I/O, but it is the one link-related age not on
  sim time. Left deliberately; worth a look if you disagree.
