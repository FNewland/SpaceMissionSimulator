# Determinism and reproducibility

**Audience:** students who need a run they can check their working against,
instructors who need an exercise that behaves the same way twice, and anyone
marking a numerical answer.

**The short version.** With the shipped configuration, the same seed and the
same `t0_epoch` and the same command script give a byte-identical parameter
trace. Time acceleration does **not** change the physics up to ×60; above ×60
the attitude loop is deliberately and visibly replaced by a statistical model.
Wall-clock time is never an input to the simulation.

Read this before you write down a number and expect someone else to get it
back.

---

## 1. What makes a run

Three inputs, and nothing else:

| Input | Where | Default |
|---|---|---|
| **Seed** | `configs/eosat1/simulation.yaml: seed` | `20260731` |
| **Epoch** | `configs/eosat1/orbit.yaml: t0_epoch`, selected by `simulation.yaml: epoch: t0` | fixed reference epoch |
| **Command script** | what the operator sends, and when in *sim* time | — |

Change the seed and a cohort gets a different but equally reproducible
universe. Set `epoch: wall` and the run starts at the wall clock — which is
the one supported way to make a run *not* reproducible, and the log says so.

### 1.1 One RNG per component

`seed(` had **zero** hits in `packages/` before the 2026-08 build. Separation
tumble rates, star-tracker star counts, coarse sun-sensor illumination,
magnetometer and gyro noise, gyro bias random walk and failure-flicker timing
all drew from the global `random` module. Every run of every exercise
differed, and the RNG state was not in the breakpoint, so N-5's `t0_epoch` fix
did *not* deliver reproducibility on its own.

Now: `smo_common.state.derive_rng(seed, component)` gives every component its
own `random.Random`, seeded from `f"{seed}:{component}"`. Seeding a `Random`
from a string uses SHA-512 internally, so the derivation is stable across
processes, machines and Python versions regardless of `PYTHONHASHSEED`.

Consequences worth knowing:

* Adding a draw to one model does not shift another model's stream. This is
  why per-component derivation was chosen over one shared generator.
* Every component's RNG state is in the breakpoint, so save → load → continue
  reproduces the run that was saved, not merely a run with the same seed.

**Pinned by** `tests/test_simulator/test_determinism.py`.

---

## 2. What `speed` does to the physics

### 2.1 The rule

`speed` multiplies simulated time per wall second: `dt_sim = dt_real × speed`.
It used to be handed straight to the models, so at ×60 the battery, thermal
RC, heater hysteresis, wheel dynamics and S12 delta baselines all integrated
60-second Euler steps and gave different answers than at ×1. Detumble diverged
to 1.4e39 at dt = 1000. "Speed it up to reach the eclipse" is the *normal*
student workflow, so this was not a corner case.

The tick is now sub-stepped:

```yaml
tick:
  dt_max_s: 1.0      # the step the model equations always see
  max_substeps: 600  # cap on the per-tick sub-step count
```

`N = ceil(dt_sim / dt_max_s)`, capped at `max_substeps`. Per-tick *outputs* —
HK sampling, TM release, dump pacing — stay at the outer tick cadence; only
the physics is sub-stepped. If the cap binds (`dt_sim > 600 s`), the per-step
`dt` grows above `dt_max_s` rather than losing simulated time: the simulation
never silently skips time.

### 2.2 Process noise scales as √dt

Random-walk noise was scaled **linearly** in `dt` (`random.gauss(0, σ) * dt`),
so the accumulated variance was off by a factor of `speed`. Every stochastic
integrator now scales as `σ·√dt`, which is the only scaling under which the
process has the same statistics at any step size.

### 2.3 What "unchanged physics" is worth, numerically

`tests/test_simulator/test_acceleration_consistency.py` runs the same
3600 s of simulated time as 3600 × 1 s and as 60 × 60 s ticks and asserts:

| Quantity | Agreement |
|---|---|
| Battery SoC at end of run | within **0.5 %** |
| Battery node temperature | within **0.5 K** |
| Eclipse entry time | within **60 s** |

and, at `dt = 600 s`, that every attitude and thermal state stays finite and
inside its physical range — the raw detumble and Ornstein–Uhlenbeck models
diverge for a single Euler step that large, and sub-stepping is what keeps
them in their stable region.

### 2.4 Above ×60: SECULAR mode, declared

Full-fidelity attitude physics (RK4 at fixed h = 0.5 s, wheels integrated
analytically) costs about 7 ms of wall time per tick at ×60 — under 1 % of the
budget. Above ×60 that stops being affordable, so the AOCS fast loop is
**bypassed**:

* `q_true` is set to the reference attitude plus a deterministic error drawn
  from *that mode's own* closed-loop error distribution (σ from the same gains
  and noise parameters — consistent, not invented).
* The slow states integrate exactly over the full step: wheel momentum from
  ∫τ dt, gyro bias random walk with √dt scaling, MEKF covariance to steady
  state.
* An S5 event fires and `aocs.fidelity_mode` (a downlinked parameter) reads 1.

So a ×1000 run is a **declared fast-forward**, not a silent lie. The threshold
is `simulation.yaml: aocs.integration.secular_above_speed` (default 60).

**Do not grade a pointing-error number taken from a run in secular mode.** It
is a sample from a distribution, not an integration.

### 2.5 Propulsion clamps acceleration

While the thruster is FIRING the engine clamps `speed` to ≤ ×10 and raises
`PROP_SPEED_CLAMPED`. At ×1000 a single tick is longer than an entire 32 s
burn, and the "abort the burn on attitude error" lesson would be
unobservable. The clamp is visible in telemetry and in the event log.

---

## 3. What is *not* reproducible

Say these out loud before an exercise; each is a legitimate question a student
will ask.

| Not reproducible | Why | Does it affect a graded number? |
|---|---|---|
| Wall-clock arrival times of packets | Real-time pacing depends on host load | No — every packet carries a CUC sim-time stamp |
| `./start.sh` rate control | `_run_loop` has no drift compensation, so ×1 is not exactly real-time under load | No — **sim time is authoritative**, and every model integrates `dt_sim` |
| Dump *filenames* | The timestamp in `dump_sid<NN>_<...>.bin` is wall clock | No — the packets inside are CUC-stamped |
| MCS live view contents | Packets released while `downlink_active` is false are discarded by the link, not the archive | No — and *archive minus live* is itself the interesting number (see the report generator) |
| A run with `epoch: wall` | Deliberate opt-out | Yes. Don't grade one |
| A run in secular mode (>×60) | Attitude is sampled, not integrated | Yes, for attitude. Slow states (momentum, SoC, thermal) are still integrated |

---

## 4. How to reproduce a run exactly

1. Note the **seed** (`simulation.yaml`) and confirm `epoch: t0`.
2. Note the **command script** in *sim* time, not wall time — the CUC stamps
   in the archive give you this for free.
3. Keep `speed ≤ 60` for anything attitude-related.
4. Re-run. The parameter trace is byte-identical.

To reproduce from the *middle* of a run, save a breakpoint: RNG state is part
of it, so a load continues the same stream rather than starting a new one
(`docs/THREADING_AND_STATE.md` §4).

---

## 5. Two time axes, and why it matters

There are two clocks and they are unrelated under acceleration:

* **CUC (sim time)** — stamped on every packet by the OBC. This is the
  spacecraft's clock and the only axis on which telemetry may be compared.
* **Wall clock** — the host's. Appears in dump filenames, log lines and
  nothing else that matters.

At ×60, one wall minute is one simulated hour. Any period selection — report
generation, trend windows, pass reports — is therefore **CUC-based**. A tool
that filtered by file mtime would return the wrong hour of the mission.

---

## 6. Where this is enforced

| Property | Test |
|---|---|
| Same seed ⇒ identical 500-tick trace; different seed ⇒ different trace | `tests/test_simulator/test_determinism.py` |
| RNG state round-trips through a breakpoint | `tests/test_simulator/test_determinism.py` |
| ×1 and ×60 agree on SoC, thermal and eclipse entry | `tests/test_simulator/test_acceleration_consistency.py` |
| dt = 600 stays finite and physical | `tests/test_simulator/test_acceleration_consistency.py` |
| Sub-step config actually reaches the engine | `tests/test_simulator/test_acceleration_consistency.py` |
| Complete state round-trip, property-based | `tests/test_simulator/test_breakpoint_round_trip.py` |
