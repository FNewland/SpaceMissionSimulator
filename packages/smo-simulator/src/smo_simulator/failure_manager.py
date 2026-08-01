"""SMO Simulator — Failure Manager.

Manages active failure injections with gradual, step, and intermittent onset models.
Refactored from failure_manager.py — identical logic, just updated imports.
"""
import math
import random
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

ONSET_STEP = "step"
ONSET_GRADUAL = "gradual"
ONSET_INTERMITTENT = "intermittent"


@dataclass
class ActiveFailure:
    failure_id: str
    subsystem: str
    failure: str
    target_magnitude: float
    onset: str
    onset_duration_s: float
    duration_s: float
    extra_params: dict[str, Any] = field(default_factory=dict)
    current_magnitude: float = 0.0
    elapsed_s: float = 0.0
    active: bool = True
    _flicker_on: bool = True
    _flicker_next: float = 0.0


# Onset-timing fallbacks. The engine overrides these from simulation.yaml
# `failures:` (defect #51); they remain the defaults for direct construction.
DEFAULT_ONSET_DURATION_S = 90.0
DEFAULT_FIRST_FLICKER_S = (2.0, 6.0)
DEFAULT_FLICKER_ON_S = (2.0, 8.0)
DEFAULT_FLICKER_OFF_S = (5.0, 20.0)


class FailureManager:
    def __init__(self, inject_fn: Callable, clear_fn: Callable, config: Any = None,
                 rng: random.Random | None = None):
        self._inject = inject_fn
        self._clear = clear_fn
        # Flicker-timing RNG (plan A-2). The engine passes a Random derived
        # from simulation.yaml `seed`; standalone construction stays random.
        self.rng = rng if rng is not None else random.Random(20260731)  # deterministic standalone fallback
        self._failures: dict[str, ActiveFailure] = {}
        self._sim_elapsed: float = 0.0
        # Onset timing (simulation.yaml `failures:` block, else defaults)
        self._default_onset_s = DEFAULT_ONSET_DURATION_S
        self._first_flicker_s = tuple(DEFAULT_FIRST_FLICKER_S)
        self._flicker_on_s = tuple(DEFAULT_FLICKER_ON_S)
        self._flicker_off_s = tuple(DEFAULT_FLICKER_OFF_S)
        if config is not None:
            self.configure(config)

    def configure(self, cfg: Any) -> None:
        """Apply a simulation.yaml ``failures:`` config block (pydantic or dict)."""
        def _get(obj, key, default):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        self._default_onset_s = float(
            _get(cfg, "default_onset_duration_s", self._default_onset_s))
        inter = _get(cfg, "intermittent", None)
        if inter is not None:
            for attr, key, cur in (
                ("_first_flicker_s", "first_flicker_s", self._first_flicker_s),
                ("_flicker_on_s", "on_s", self._flicker_on_s),
                ("_flicker_off_s", "off_s", self._flicker_off_s),
            ):
                rng = _get(inter, key, None)
                if rng and len(rng) == 2:
                    setattr(self, attr, (float(rng[0]), float(rng[1])))

    def inject(self, subsystem: str, failure: str, magnitude: float = 1.0,
               onset: str = ONSET_STEP, duration_s: float = 0.0,
               onset_duration_s: float | None = None, **extra) -> str:
        if onset_duration_s is None:
            onset_duration_s = self._default_onset_s
        fid = f"{subsystem}.{failure}.{extra.get('array', extra.get('wheel', extra.get('circuit', '0')))}"
        if onset == ONSET_STEP:
            onset_duration_s = 0.0
            current_mag = magnitude
        else:
            current_mag = 0.0

        af = ActiveFailure(
            failure_id=fid, subsystem=subsystem, failure=failure,
            target_magnitude=magnitude, onset=onset,
            onset_duration_s=onset_duration_s,
            duration_s=duration_s or 0.0,
            extra_params=dict(extra), current_magnitude=current_mag,
        )
        if onset == ONSET_STEP:
            self._apply(af, magnitude)
        elif onset == ONSET_INTERMITTENT:
            af._flicker_next = self._sim_elapsed + self.rng.uniform(*self._first_flicker_s)
        self._failures[fid] = af
        return fid

    def clear(self, failure_id: str) -> bool:
        af = self._failures.pop(failure_id, None)
        if af is None:
            return False
        self._clear_failure(af)
        return True

    def clear_all(self) -> None:
        for af in list(self._failures.values()):
            self._clear_failure(af)
        self._failures.clear()

    def active_failures(self) -> list[dict]:
        return [
            {"id": af.failure_id, "subsystem": af.subsystem, "failure": af.failure,
             "onset": af.onset, "magnitude": round(af.current_magnitude, 3),
             "elapsed_s": round(af.elapsed_s, 1), "duration_s": af.duration_s}
            for af in self._failures.values() if af.active
        ]

    def tick(self, dt_sim: float) -> None:
        self._sim_elapsed += dt_sim
        expired = []
        for fid, af in self._failures.items():
            if not af.active:
                expired.append(fid)
                continue
            af.elapsed_s += dt_sim
            if af.duration_s > 0 and af.elapsed_s >= af.duration_s:
                self._clear_failure(af)
                expired.append(fid)
                continue
            if af.onset == ONSET_GRADUAL:
                self._tick_gradual(af, dt_sim)
            elif af.onset == ONSET_INTERMITTENT:
                self._tick_intermittent(af, self._sim_elapsed)
        for fid in expired:
            self._failures.pop(fid, None)

    def _tick_gradual(self, af, dt_sim):
        if af.onset_duration_s <= 0:
            new_mag = af.target_magnitude
        else:
            ramp = af.target_magnitude / af.onset_duration_s
            new_mag = min(af.target_magnitude, af.current_magnitude + ramp * dt_sim)
        if abs(new_mag - af.current_magnitude) > 0.001:
            af.current_magnitude = new_mag
            self._apply(af, new_mag)

    def _tick_intermittent(self, af, sim_elapsed):
        if sim_elapsed >= af._flicker_next:
            af._flicker_on = not af._flicker_on
            if af._flicker_on:
                af._flicker_next = sim_elapsed + self.rng.uniform(*self._flicker_on_s)
                self._apply(af, af.target_magnitude)
            else:
                af._flicker_next = sim_elapsed + self.rng.uniform(*self._flicker_off_s)
                self._clear_failure(af, remove=False)

    # ─── State save/restore (plan A-4) ───────────────────────────────

    def get_state(self) -> dict[str, Any]:
        """Serialise active failures + flicker RNG for breakpoint save."""
        from smo_common.state import rng_state_to_json
        return {
            "sim_elapsed": self._sim_elapsed,
            "rng_state": rng_state_to_json(self.rng.getstate()),
            "failures": [
                {
                    "failure_id": af.failure_id, "subsystem": af.subsystem,
                    "failure": af.failure,
                    "target_magnitude": af.target_magnitude,
                    "onset": af.onset,
                    "onset_duration_s": af.onset_duration_s,
                    "duration_s": af.duration_s,
                    "extra_params": dict(af.extra_params),
                    "current_magnitude": af.current_magnitude,
                    "elapsed_s": af.elapsed_s, "active": af.active,
                    "flicker_on": af._flicker_on,
                    "flicker_next": af._flicker_next,
                }
                for af in self._failures.values()
            ],
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore active failures from a breakpoint.

        Does NOT re-apply the failures through inject_fn — the subsystem
        models' own restored state already reflects the injected effects.
        """
        from smo_common.state import restore_rng
        restore_rng(self.rng, state.get("rng_state"))
        self._sim_elapsed = float(state.get("sim_elapsed", 0.0))
        self._failures = {}
        for f in state.get("failures", []):
            af = ActiveFailure(
                failure_id=f["failure_id"], subsystem=f["subsystem"],
                failure=f["failure"],
                target_magnitude=float(f["target_magnitude"]),
                onset=f["onset"],
                onset_duration_s=float(f["onset_duration_s"]),
                duration_s=float(f["duration_s"]),
                extra_params=dict(f.get("extra_params", {})),
                current_magnitude=float(f.get("current_magnitude", 0.0)),
                elapsed_s=float(f.get("elapsed_s", 0.0)),
                active=bool(f.get("active", True)),
            )
            af._flicker_on = bool(f.get("flicker_on", True))
            af._flicker_next = float(f.get("flicker_next", 0.0))
            self._failures[af.failure_id] = af

    def _apply(self, af, magnitude):
        cmd = {"type": "inject", "subsystem": af.subsystem,
               "failure": af.failure, "magnitude": magnitude}
        cmd.update(af.extra_params)
        try:
            self._inject(cmd)
        except Exception as e:
            logger.warning("Failure apply error (%s): %s", af.failure_id, e)

    def _clear_failure(self, af, remove=True):
        cmd = {"type": "clear_failure", "subsystem": af.subsystem, "failure": af.failure}
        cmd.update(af.extra_params)
        try:
            self._clear(cmd)
        except Exception as e:
            logger.warning("Failure clear error (%s): %s", af.failure_id, e)
        if remove:
            af.active = False
