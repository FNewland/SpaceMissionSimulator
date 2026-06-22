# EOSAT-1 Time and Clocks

**Document ID:** EOSAT1-UM-TIME-012
**Issue:** 1.0
**Date:** 2026-06-22
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The EOSAT-1 ground segment and simulator distinguish **three independent time
domains**. Confusing them is a common source of operator error, so this
document states exactly which clock each component and function uses, and how
to switch the ground tools between following **simulation time** (when
connected to the simulator) and **real wall-clock UTC** (when connected to a
real spacecraft link).

The key principle: **spacecraft time always comes from telemetry**, regardless
of any ground configuration. The "time source" switch described here only
affects the *ground/operator* clock used by the MCS and the Planner for orbit
propagation, contact prediction and ground-track display.

## 2. The Three Time Domains

| # | Domain | What it is | Where it lives | How it advances |
|---|--------|-----------|----------------|-----------------|
| 1 | **Ground / Sim UTC** | The operator / ground clock. Drives orbit propagation, eclipse and contact geometry on the ground side, and the Planner's "now". | MCS `get_ground_utc()`; Planner `_get_now()` | In **sim mode**: tracks the simulator's `sim_time`/`speed`. In **real mode**: real wall-clock UTC. |
| 2 | **Spacecraft (OBC) time** | The onboard computer clock, carried as CUC seconds in TM parameter **0x0309** and surfaced by the MCS as `sc_obc_time_cuc` / `sc_time`. | Onboard OBC → telemetry → MCS param cache | Advances onboard; the ground only reads it from TM. **Never** reconstructed on the ground. |
| 3 | **Wall clock** | Real, physical time on the machine running the tools. | `datetime.now()` / `time.time()` | Real time, always. |

Domains 1 and 3 are equal in **real mode** but diverge in **sim mode** (where
domain 1 follows the accelerated/pausable simulator clock).

### 2.1 Which clock each component/function uses

| Component / function | Clock used | Notes |
|----------------------|-----------|-------|
| MCS **ground UTC** (`get_ground_utc()`) | Domain 1 (Ground/Sim UTC) | Sim mode: re-anchored from the simulator each poll. Real mode: wall clock. |
| MCS **spacecraft time** (`sc_obc_time_cuc`, `sc_time`) | Domain 2 (OBC CUC) | Always from TM param 0x0309. Independent of the time-source switch. |
| MCS **orbit propagation** (`_compute_orbital_state`) | Domain 1 | Propagates the TLE to `get_ground_utc()`. |
| MCS **ground-access / contact** (`in_contact`, elevation) | Domain 1 | Derived from the propagated orbital geometry at ground UTC. |
| Planner **contacts** / **ground-track** / **live spacecraft state** | Domain 1 (`_get_now()`) | Sim mode follows the simulator; `?epoch=` query overrides it. |
| **S11 time-tagged commands** | Domain 2 (spacecraft CUC) | Time tags are interpreted against the **onboard** clock, not ground UTC. |
| **Scenario scripting** (instructor) | Sim-elapsed time | Driven inside the simulator off `_sim_time`; not affected by ground settings. |
| Simulator internal timekeeping (`engine._sim_time`, orbit `_sim_utc`, OBC CUC) | Sim time | Authoritative source for domain 1 in sim mode. **Do not** change. |

## 3. The Sim-vs-Real Switch

A single setting selects whether the **MCS** and **Planner** follow simulation
time or real UTC.

### 3.1 Where it is set (precedence)

For each service the value is resolved with this precedence (first wins):

1. **CLI flag** — `--time-source sim|real`
2. **Environment variable** — `SMO_TIME_SOURCE` (`sim` | `real`)
3. **Mission-config field** — `time_source:` in `configs/eosat1/mission.yaml`
4. **Built-in default** — `"sim"`

The companion `sim_state_url` (the simulator endpoint polled for
`sim_time`/`speed`) resolves the same way:

1. `--sim-state-url <url>`
2. `SMO_SIM_STATE_URL`
3. `sim_state_url:` in `mission.yaml`
4. Default `http://<connect_host>:8080/api/state`

### 3.2 Mission-config field

`configs/eosat1/mission.yaml`:

```yaml
time_source: sim                 # "sim" | "real"
# sim_state_url: http://localhost:8080/api/state   # optional override
```

This is currently set to **`sim`** (connected to the simulator).

### 3.3 Environment / start.sh

`start.sh` exports the env vars near the top and passes `--time-source`
explicitly to both children:

```bash
SMO_TIME_SOURCE="${SMO_TIME_SOURCE:-sim}"          # --real flag sets this to real
export SMO_SIM_STATE_URL="${SMO_SIM_STATE_URL:-http://localhost:8080/api/state}"
```

- `./start.sh` → time source **sim** (default).
- `./start.sh --real` → time source **real** (wall clock).
- `SMO_TIME_SOURCE=real ./start.sh` → real, via env.

The active time source is echoed in the startup banner.

## 4. Closed-Loop Sim Sync (sim mode)

In **sim mode** the ground clock is **closed-loop synced** to the simulator,
not merely reconstructed locally:

- **MCS** runs a background loop (`_sim_state_poll_loop`) that polls
  `sim_state_url` every ~1.5 s, reads `sim_time` and `speed`, and
  **re-anchors** its open-loop clock (`_ground_epoch = sim_time`,
  `_ground_start_wall = now`, `_sim_speed = speed`). Between polls it
  extrapolates by `speed`, so worst-case drift is bounded by the poll
  interval. This tracks the simulator correctly through **pause**, **speed
  change** and **breakpoint load**.
- **Planner** keeps a cached anchor (`_sim_anchor_time` / `_sim_anchor_wall` /
  `_sim_anchor_speed`), refreshed by a background loop (and lazily, rate-limited
  to ~1/s, on request). `_get_now()` returns
  `anchor_sim_time + (now − anchor_wall) × speed`.

**Fallbacks:** on a failed poll, both services keep the last anchor (logged at
debug, no crash). If the simulator was never reachable, both fall back to
wall-clock UTC so the tools still function.

**Manual time control:** the MCS `POST /api/ground-time` endpoint still works
(offset / epoch / speed). Note that in sim mode the next sim poll re-anchors
epoch and speed from the simulator, so manual epoch/speed changes are
transient; the manual **offset** is preserved.

## 5. Pointing at a Real Mission vs the Sim

### 5.1 Simulator (current configuration)

- `time_source: sim` (or `./start.sh`).
- MCS/Planner follow the simulator's accelerated, pausable clock.
- TC/TM flow to the simulator ports (TC 8001 / TM 8002, or via the RF bridge).

### 5.2 Real mission

To operate against a real spacecraft link:

1. Set **`time_source: real`** in `mission.yaml`, or launch with
   `./start.sh --real` (or `--time-source real` on the service), or
   `SMO_TIME_SOURCE=real`.
2. Point the **MCS `--connect` / `--tc-port`** at the real ground-station TC/TM
   link instead of the simulator ports.
3. Configure the **real ground stations** in `orbit.yaml` /
   `planning/ground_stations.yaml` (real coordinates and mask).
4. In real mode the ground clock is real UTC and **no** `sim_state_url`
   polling occurs.

In **both** modes the spacecraft OBC time is read from telemetry (TM 0x0309)
and is never synthesised on the ground.

## 6. Caveats

- **MCS restart caveat:** the time-source and `sim_state_url` are resolved at
  MCS/Planner construction. Changing the mission-config field, env var or CLI
  flag requires **restarting** the affected service for the change to take
  effect.
- The simulator's own internal timekeeping is authoritative for sim mode and
  must not be altered by ground configuration.
