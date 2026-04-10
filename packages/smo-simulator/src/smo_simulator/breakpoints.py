"""SMO Simulator — State Breakpoint Manager.

Serialize/restore complete simulation state for breakpoint save/load.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BreakpointManager:
    """Saves and loads complete simulator state snapshots."""

    def __init__(self, engine):
        self._engine = engine

    def save(self, name: str = "", path: Path | None = None) -> dict:
        """Capture a full state snapshot."""
        eng = self._engine
        state = {
            "name": name or f"breakpoint_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tick_count": eng._tick_count,
            "sim_time": eng._sim_time.isoformat(),
            "speed": eng.speed,
            "sc_mode": eng.sc_mode,
            "params": {str(k): v for k, v in eng.params.items()},
            "subsystems": {},
            "fdir_triggered": dict(eng._fdir_triggered),
            "hk_timers": {str(k): v for k, v in eng._hk_timers.items()},
        }
        for sub_name, model in eng.subsystems.items():
            try:
                state["subsystems"][sub_name] = model.get_state()
            except Exception as e:
                logger.warning("Failed to save state for %s: %s", sub_name, e)

        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'w') as f:
                json.dump(state, f, indent=2, default=str)
            logger.info("Breakpoint saved: %s -> %s", state["name"], path)
        return state

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
            eng._fdir_triggered = state.get("fdir_triggered", {})
            eng._hk_timers = {int(k): v for k, v in state.get("hk_timers", {}).items()}

            for sub_name, sub_state in state.get("subsystems", {}).items():
                model = eng.subsystems.get(sub_name)
                if model:
                    model.set_state(sub_state)
            logger.info("Breakpoint loaded: %s", state.get("name", "unknown"))
            return True
        except Exception as e:
            logger.error("Failed to load breakpoint: %s", e)
            return False
