"""SMO Common — shared state-serialisation protocol and RNG helpers.

Phase A foundation (plan A-2 / A-4):

* ``Stateful`` — the protocol every component holding mutable simulation
  state must implement so breakpoints can round-trip it. The engine keeps a
  registry of Stateful components; ``BreakpointManager`` iterates that
  registry and a component whose save fails FAILS THE WHOLE SAVE (no more
  silently-incomplete breakpoints, brief 08 C-3).

* RNG-state JSON helpers — ``random.Random.getstate()`` returns nested
  tuples, which JSON round-trips as lists; ``setstate()`` requires the tuple
  form back. These two helpers are the single conversion point so every
  component serialises RNG state the same way.

* ``derive_rng`` — one seeded ``random.Random`` per engine *component*,
  derived deterministically from the engine seed and the component name.
  Seeding from a string uses SHA-512 internally (CPython documented
  behaviour), so the derivation is stable across processes and Python
  versions regardless of ``PYTHONHASHSEED``.
"""
from __future__ import annotations

import random
from typing import Any, Protocol, runtime_checkable

#: Default engine seed when simulation.yaml has no ``seed:`` key.
DEFAULT_SEED = 20260731


@runtime_checkable
class Stateful(Protocol):
    """A component whose complete mutable state round-trips as a JSON-safe dict."""

    def get_state(self) -> dict[str, Any]:
        """Serialise complete mutable state for breakpoint save."""
        ...

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore state from a breakpoint."""
        ...


def derive_rng(seed: int | None, component: str) -> random.Random:
    """Return a ``random.Random`` seeded deterministically for one component.

    Same (seed, component) pair ⇒ identical stream, in any process.
    """
    if seed is None:
        seed = DEFAULT_SEED
    return random.Random(f"{int(seed)}:{component}")


def rng_state_to_json(state: tuple) -> list:
    """Convert ``Random.getstate()`` output to a JSON-serialisable list."""
    version, internal, gauss_next = state
    return [int(version), [int(x) for x in internal], gauss_next]


def rng_state_from_json(data: Any) -> tuple:
    """Convert the JSON form back to what ``Random.setstate()`` expects."""
    version, internal, gauss_next = data
    return (int(version), tuple(int(x) for x in internal),
            None if gauss_next is None else float(gauss_next))


def restore_rng(rng: random.Random, data: Any) -> None:
    """Restore ``rng`` from a JSON-encoded state; ignore None (old snapshots)."""
    if data is not None:
        rng.setstate(rng_state_from_json(data))


def restore_dataclass_state(target: Any, state: dict[str, Any],
                            skip: tuple[str, ...] = ()) -> None:
    """Copy ``state`` onto the live dataclass ``target``, without aliasing.

    Every model used to do this by hand as ``setattr(target, k, v)``. For a
    scalar that is harmless; for the container fields — ``power_lines``,
    ``device_states``, ``line_currents``, ``image_catalog``, ``rw_speed``,
    ``_temp_history`` — it assigned the *snapshot's own* object into live
    state, so from that moment the model mutated the stored breakpoint on
    every tick. The snapshot silently drifted away from the state it claimed
    to capture, a second load restored the drifted values, and two models
    restored from one snapshot shared containers. Phase C found and fixed
    this for AOCS only; this is the shared, swept version.

    ``state`` is never mutated (a snapshot may be loaded more than once).
    Keys the target does not declare are ignored, so old snapshots load.
    """
    import copy as _copy
    for k, v in state.items():
        if k in skip:
            continue
        if hasattr(target, k):
            setattr(target, k, _copy.deepcopy(v)
                    if isinstance(v, (dict, list, set, tuple, bytearray))
                    else v)
