"""SMO Simulator — State Breakpoint Manager.

Serialize/restore complete simulation state for breakpoint save/load.

Plan A-4: the manager serialises the engine's Stateful-component registry
(``engine.stateful_components()``) — engine-level state, every subsystem
model, the S11 scheduler, failure manager, scenario engine, service
dispatcher, load-shedding manager, S15 TM stores, S18 OBCP state and the S6
memory image. A failure saving ANY component fails the WHOLE save (brief 08
C-3: the old code logged a warning and persisted a silently-incomplete
breakpoint that loaded without complaint).

Clocks (TIME_ARCHITECTURE Part C.8): a snapshot restores ONE coherent sim
clock — `sim_time` — from which the orbit propagator is re-driven; `saved_at`
is a wall-clock session artefact and is never read back into state. The
spacecraft's ONBOARD clock is not stored separately either: it belongs to the
OBDH model and round-trips inside `subsystems["obdh"]`.

Snapshot format (backwards compatible — old snapshots still load):
  top-level:   name, saved_at (was: timestamp), tick_count, sim_time, speed,
               sc_mode, params, fdir_triggered, hk_timers, hk_enabled,
               orbit_utc (legacy mirror of sim_time, written but not read),
               orbit_elset (Phase D: post-burn TLE re-fit; None/absent =
               never manoeuvred, config TLE authoritative)
  subsystems:  {model_name: model.get_state()}          (pre-A-4 key, kept)
  tm_storage / obcp / memory                            (pre-A-4 keys, kept)
  engine:      engine.get_state()                       (A-4)
  components:  {name: component.get_state()}            (A-4: tc_scheduler,
               failure_manager, scenario_engine, dispatcher, load_shedding)
  rng:         {seed, engine, failure_manager}          (A-2, redundant with
               engine/components blocks; kept for older snapshots)
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Registry names serialised under their own legacy top-level key rather than
# the generic "components" block, so snapshots stay readable by older code.
_LEGACY_KEYS = {"tm_storage", "obcp", "memory"}


class BreakpointSaveError(RuntimeError):
    """A component failed to serialise — the whole save is aborted."""


class BreakpointManager:
    """Saves and loads complete simulator state snapshots."""

    def __init__(self, engine):
        self._engine = engine

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self, name: str = "", path: Path | None = None) -> dict:
        """Capture a full state snapshot.

        Raises BreakpointSaveError if ANY registered component fails to
        serialise — an incomplete breakpoint must never be written.
        """
        eng = self._engine
        state = {
            # `name`/`saved_at` describe the SESSION (which file, saved when by
            # whose console) and are the one legitimate wall-clock use here.
            # Everything about the simulated world is on sim time.
            "name": name or f"breakpoint_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "tick_count": eng._tick_count,
            # THE clock (spec Part C.1/C.8). The orbit propagator has no clock
            # of its own, so this one field restores the whole sim-time state;
            # the onboard clock is inside the OBDH model's own state block.
            "sim_time": eng._sim_time.isoformat(),
            "speed": eng.speed,
            "sc_mode": eng.sc_mode,
            "params": {str(k): v for k, v in eng.params.items()},
            "subsystems": {},
            "components": {},
            "fdir_triggered": dict(eng._fdir_triggered),
            "hk_timers": {str(k): v for k, v in eng._hk_timers.items()},
        }

        # Stateful-component registry (plan A-4). NO exception swallowing:
        # a component that cannot serialise fails the whole save.
        components = self._registry()
        for comp_name, comp in components.items():
            try:
                comp_state = comp.get_state()
            except Exception as e:
                raise BreakpointSaveError(
                    f"breakpoint save failed: component {comp_name!r} "
                    f"({type(comp).__name__}.get_state) raised {e!r}") from e
            if comp_name == "engine":
                state["engine"] = comp_state
            elif comp_name.startswith("model:"):
                state["subsystems"][comp_name.split(":", 1)[1]] = comp_state
            elif comp_name in _LEGACY_KEYS:
                state[comp_name] = comp_state
            else:
                state["components"][comp_name] = comp_state

        # Legacy RNG block (A-2) — redundant with the engine/failure_manager
        # component states above, kept so A-2-era snapshots and tools keep
        # working.
        from smo_common.state import rng_state_to_json
        rng_block: dict[str, Any] = {"seed": getattr(eng, "seed", None)}
        if hasattr(eng, "rng"):
            rng_block["engine"] = rng_state_to_json(eng.rng.getstate())
        fm = getattr(eng, "_failure_manager", None)
        if fm is not None and hasattr(fm, "rng"):
            rng_block["failure_manager"] = rng_state_to_json(fm.rng.getstate())
        state["rng"] = rng_block

        # HK enable state — which SIDs the operator has commanded on is part
        # of the spacecraft configuration (defect #75).
        hk_enabled = getattr(eng, "_hk_enabled", None)
        if isinstance(hk_enabled, dict):
            state["hk_enabled"] = {str(k): bool(v) for k, v in hk_enabled.items()}

        # Orbit ELEMENT SET (Phase D). SGP4 was stateless given a UTC time
        # only until propulsion existed: a burn re-fits the element set, so
        # the clock alone no longer identifies the orbit. orbit_elset is None
        # while the spacecraft has never manoeuvred (the config TLE stays
        # authoritative), and old snapshots without the key restore correctly
        # as un-manoeuvred.
        #
        # The propagator's clock is NOT saved separately any more: it has no
        # clock of its own, it is driven from `sim_time` above. `orbit_utc` is
        # still written for older readers, and is always equal to sim_time.
        orbit = getattr(eng, "orbit", None)
        if orbit is not None:
            try:
                state["orbit_utc"] = orbit.utc.isoformat()
            except Exception as e:
                raise BreakpointSaveError(
                    f"breakpoint save failed: orbit clock unavailable "
                    f"({e!r})") from e
            if hasattr(orbit, "get_elset"):
                try:
                    state["orbit_elset"] = orbit.get_elset()
                except Exception as e:
                    raise BreakpointSaveError(
                        f"breakpoint save failed: orbit element set "
                        f"unavailable ({e!r})") from e

        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            logger.info("Breakpoint saved: %s -> %s", state["name"], path)
        return state

    def _registry(self) -> dict[str, Any]:
        """The engine's Stateful registry, with a fallback for mock/legacy
        engines constructed without one (bare unit-test doubles)."""
        eng = self._engine
        reg = getattr(eng, "stateful_components", None)
        if callable(reg):
            components = reg()
            if components:
                return components
        # Fallback: reconstruct the pre-A-4 set from well-known attributes.
        components = {}
        for sub_name, model in getattr(eng, "subsystems", {}).items():
            components[f"model:{sub_name}"] = model
        for attr, comp_name in (("_tm_storage", "tm_storage"),
                                ("_obcp_manager", "obcp"),
                                ("_memory", "memory")):
            comp = getattr(eng, attr, None)
            if comp is not None and hasattr(comp, "get_state"):
                components[comp_name] = comp
        return components

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load(self, state: dict | None = None, path: Path | None = None) -> bool:
        """Restore from a state snapshot."""
        if path and path.exists():
            with open(path, 'r') as f:
                state = json.load(f)
        if state is None:
            return False

        eng = self._engine
        try:
            eng._tick_count = state.get("tick_count", 0)
            eng._sim_time = datetime.fromisoformat(state["sim_time"]) if "sim_time" in state else eng._sim_time
            eng.speed = state.get("speed", 1.0)
            eng.sc_mode = state.get("sc_mode", 0)
            eng.params = {int(k): v for k, v in state.get("params", {}).items()}
            # copy, not the snapshot's own dict: the engine latches FDIR rules
            # into this every tick, so assigning it by reference made the
            # breakpoint drift away from the state it captured (G2-f).
            eng._fdir_triggered = dict(state.get("fdir_triggered", {}) or {})
            eng._hk_timers = {int(k): v for k, v in state.get("hk_timers", {}).items()}

            components = self._registry()

            for sub_name, sub_state in state.get("subsystems", {}).items():
                model = components.get(f"model:{sub_name}") or \
                    getattr(eng, "subsystems", {}).get(sub_name)
                if model:
                    model.set_state(sub_state)

            # Engine-level state (A-4). Missing in pre-A-4 snapshots.
            if "engine" in state and "engine" in components:
                components["engine"].set_state(state["engine"])

            # Generic component states (A-4).
            for comp_name, comp_state in state.get("components", {}).items():
                comp = components.get(comp_name)
                if comp is not None:
                    comp.set_state(comp_state)
                else:
                    logger.warning(
                        "Breakpoint has state for unregistered component %r "
                        "— skipped", comp_name)

            # Legacy per-key components (also in pre-A-4 snapshots).
            for comp_name in _LEGACY_KEYS:
                comp = components.get(comp_name) or getattr(
                    eng, {"tm_storage": "_tm_storage", "obcp": "_obcp_manager",
                          "memory": "_memory"}[comp_name], None)
                if comp is not None and hasattr(comp, "set_state") \
                        and comp_name in state:
                    comp.set_state(state[comp_name])

            # Legacy RNG block (A-2 snapshots without the engine/components
            # states). The A-4 component states above already restored these
            # streams; restoring twice from the same snapshot is idempotent.
            from smo_common.state import restore_rng
            rng_block = state.get("rng") or {}
            if hasattr(eng, "rng"):
                restore_rng(eng.rng, rng_block.get("engine"))
            fm = getattr(eng, "_failure_manager", None)
            if fm is not None and hasattr(fm, "rng"):
                restore_rng(fm.rng, rng_block.get("failure_manager"))

            # Restore which HK SIDs were enabled (see save()).
            if "hk_enabled" in state and isinstance(
                    getattr(eng, "_hk_enabled", None), dict):
                for sid, on in state["hk_enabled"].items():
                    eng._hk_enabled[int(sid)] = bool(on)

            # Restore the orbit propagator's ELEMENT SET (Phase D: a burn's
            # TLE re-fit must be back in place before we re-propagate), then
            # drive the propagator from the ONE restored clock — eng._sim_time
            # — so the satellite is back at the snapshot's orbital position.
            # There is no separate orbit clock to restore and no fallback to
            # choose between: a snapshot that carries an `orbit_utc`
            # disagreeing with `sim_time` was written by the pre-Part-C code
            # and the sim clock is the authority. A snapshot without
            # orbit_elset (pre-propulsion, or never manoeuvred) restores the
            # config TLE.
            orbit = getattr(eng, "orbit", None)
            if orbit is not None and hasattr(orbit, "set_elset"):
                try:
                    orbit.set_elset(state.get("orbit_elset"))
                except Exception as e:
                    logger.warning("Orbit element-set restore failed: %s", e)
            if orbit is not None and hasattr(orbit, "reset"):
                try:
                    orbit.reset(eng._sim_time)
                except Exception as e:
                    logger.warning("Orbit propagator reset failed: %s", e)

            logger.info("Breakpoint loaded: %s", state.get("name", "unknown"))
            return True
        except Exception as e:
            logger.error("Failed to load breakpoint: %s", e)
            return False
