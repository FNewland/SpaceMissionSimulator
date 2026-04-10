"""
EO Mission Simulator — Failure Manager
Manages active failure injections with gradual, step, and intermittent onset models.
Ticked by the SimulationEngine each simulation step.
"""
import math
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Onset mode constants
# ---------------------------------------------------------------------------
ONSET_STEP         = "step"        # Instantaneous full-magnitude injection
ONSET_GRADUAL      = "gradual"     # Linear ramp over onset_duration_s
ONSET_INTERMITTENT = "intermittent"  # Random flickering at given frequency


@dataclass
class ActiveFailure:
    """One running failure injection."""
    failure_id:       str            # unique key: "eps.solar_array_partial.A"
    subsystem:        str
    failure:          str
    target_magnitude: float          # 0–1, final severity
    onset:            str            # ONSET_* constant
    onset_duration_s: float          # how long to ramp to full magnitude
    duration_s:       float          # total lifetime (0 = permanent)
    extra_params:     Dict[str, Any] = field(default_factory=dict)

    # Runtime state
    current_magnitude: float = 0.0
    elapsed_s:         float = 0.0
    active:            bool  = True
    # Intermittent state
    _flicker_on:  bool  = True
    _flicker_next: float = 0.0      # wall-clock time of next flicker toggle


class FailureManager:
    """
    Manages all active failure injections.

    Usage (called from SimulationEngine):
        fm = FailureManager(inject_fn=engine._handle_failure_inject,
                            clear_fn=engine._handle_failure_clear)
        fm.inject("eps", "solar_array_partial", magnitude=0.8,
                  onset="gradual", duration_s=300, array="A")
        # Each tick:
        fm.tick(dt_sim)
    """

    def __init__(self,
                 inject_fn: Callable[[dict], None],
                 clear_fn:  Callable[[dict], None]):
        self._inject   = inject_fn
        self._clear    = clear_fn
        self._failures: Dict[str, ActiveFailure] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inject(
        self,
        subsystem:        str,
        failure:          str,
        magnitude:        float  = 1.0,
        onset:            str    = ONSET_STEP,
        duration_s:       float  = 0.0,
        onset_duration_s: float  = 90.0,
        **extra
    ) -> str:
        """
        Inject a failure.  Returns a failure_id that can be used to clear it.
        extra kwargs are passed through to the engine inject command (e.g. array='A').
        """
        fid = f"{subsystem}.{failure}.{extra.get('array', extra.get('wheel', extra.get('circuit', '0')))}"
        # Normalise duration: None means permanent (0.0)
        if duration_s is None:
            duration_s = 0.0
        # Resolve onset duration based on onset type
        if onset == ONSET_STEP:
            onset_duration_s = 0.0
            current_mag = magnitude
        else:
            current_mag = 0.0

        af = ActiveFailure(
            failure_id       = fid,
            subsystem        = subsystem,
            failure          = failure,
            target_magnitude = magnitude,
            onset            = onset,
            onset_duration_s = onset_duration_s,
            duration_s       = duration_s,
            extra_params     = dict(extra),
            current_magnitude= current_mag,
        )

        if onset == ONSET_STEP:
            # Apply immediately at full magnitude
            self._apply(af, magnitude)
        elif onset == ONSET_INTERMITTENT:
            af._flicker_next = time.monotonic() + random.uniform(2, 6)

        self._failures[fid] = af
        logger.info("Failure injected: %s (%s onset, mag=%.2f, dur=%s)",
                    fid, onset, magnitude, f"{duration_s:.0f}s" if duration_s else "permanent")
        return fid

    def clear(self, failure_id: str) -> bool:
        """Immediately clear a specific failure by ID."""
        af = self._failures.pop(failure_id, None)
        if af is None:
            return False
        self._clear_failure(af)
        logger.info("Failure cleared: %s", failure_id)
        return True

    def clear_all(self) -> None:
        """Clear every active failure."""
        for af in list(self._failures.values()):
            self._clear_failure(af)
        self._failures.clear()

    def active_failures(self) -> List[dict]:
        """Return list of dicts describing active failures (for instructor UI)."""
        return [
            {
                "id":        af.failure_id,
                "subsystem": af.subsystem,
                "failure":   af.failure,
                "onset":     af.onset,
                "magnitude": round(af.current_magnitude, 3),
                "elapsed_s": round(af.elapsed_s, 1),
                "duration_s": af.duration_s,
            }
            for af in self._failures.values()
            if af.active
        ]

    # ------------------------------------------------------------------
    # Tick (called by engine each sim step)
    # ------------------------------------------------------------------

    def tick(self, dt_sim: float) -> None:
        expired = []
        now = time.monotonic()

        for fid, af in self._failures.items():
            if not af.active:
                expired.append(fid)
                continue

            af.elapsed_s += dt_sim

            # Check lifetime expiry
            if af.duration_s and af.duration_s > 0 and af.elapsed_s >= af.duration_s:
                self._clear_failure(af)
                expired.append(fid)
                logger.info("Failure expired: %s", fid)
                continue

            if af.onset == ONSET_GRADUAL:
                self._tick_gradual(af, dt_sim)
            elif af.onset == ONSET_INTERMITTENT:
                self._tick_intermittent(af, now)
            # STEP: magnitude already applied at inject time, nothing to do

        for fid in expired:
            self._failures.pop(fid, None)

    # ------------------------------------------------------------------
    # Onset physics
    # ------------------------------------------------------------------

    def _tick_gradual(self, af: ActiveFailure, dt_sim: float) -> None:
        if af.onset_duration_s <= 0:
            new_mag = af.target_magnitude
        else:
            ramp_rate = af.target_magnitude / af.onset_duration_s
            new_mag   = min(af.target_magnitude,
                            af.current_magnitude + ramp_rate * dt_sim)

        if abs(new_mag - af.current_magnitude) > 0.001:
            af.current_magnitude = new_mag
            self._apply(af, new_mag)

    def _tick_intermittent(self, af: ActiveFailure, now: float) -> None:
        if now >= af._flicker_next:
            af._flicker_on = not af._flicker_on
            # On period: 2–8s;  Off period: 5–20s
            if af._flicker_on:
                af._flicker_next = now + random.uniform(2.0, 8.0)
                self._apply(af, af.target_magnitude)
            else:
                af._flicker_next = now + random.uniform(5.0, 20.0)
                self._clear_failure(af, remove=False)

    # ------------------------------------------------------------------
    # Apply / clear to engine
    # ------------------------------------------------------------------

    def _apply(self, af: ActiveFailure, magnitude: float) -> None:
        cmd = {
            "type":      "inject",
            "subsystem": af.subsystem,
            "failure":   af.failure,
            "magnitude": magnitude,
        }
        cmd.update(af.extra_params)
        try:
            self._inject(cmd)
        except Exception as e:
            logger.warning("Failure apply error (%s): %s", af.failure_id, e)

    def _clear_failure(self, af: ActiveFailure, remove: bool = True) -> None:
        cmd = {
            "type":      "clear_failure",
            "subsystem": af.subsystem,
            "failure":   af.failure,
        }
        cmd.update(af.extra_params)
        try:
            self._clear(cmd)
        except Exception as e:
            logger.warning("Failure clear error (%s): %s", af.failure_id, e)
        if remove:
            af.active = False
