"""The ground console must never display simulator truth as telemetry.

Field report 2026-08-20: "I am worried you are feeding raw sim data to the new
UI... (as opposed to decommutating simulated rf data)".

It is the right thing to worry about, and it is the property the whole training
value of this system rests on: if the MCS reads the engine's state instead of
decoding packets, then students learn a console that cannot exist, disabled HK
structures still show values, and the ground/space boundary — the thing being
taught — is fiction.

The audit says the boundary holds: ``_param_cache`` is written from exactly two
places, both downstream of the TM socket. That is a fact about today's source,
and facts about source rot. These tests make it a standing guarantee.

The rule: **every write into the parameter cache must be lexically inside the
TM-processing path.** Simulator-sourced blocks (``sc_mode``, ``sim_health``,
``hk_enabled``, orbit elsets) are legitimate and stay under their own keys with
a ``source`` tag; they must never land in ``params``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "server.py"

#: The only functions allowed to write the parameter cache. Both are fed by
#: `_tm_receive_loop` -> `decommutate_packet` -> `_process_tm`.
TM_PATH_FUNCTIONS = {"_process_tm"}

#: Methods that ingest the simulator's /api/state. None may touch the cache.
SIM_INGEST_FUNCTIONS = {
    "_ingest_sim_health", "_ingest_reported_hk_enabled",
    "_ingest_orbit_elset", "_ingest_sim_illumination", "_sim_state_poll_loop",
}


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(SERVER.read_text(encoding="utf-8"))


def _enclosing_function(tree: ast.Module, node: ast.AST) -> str | None:
    """Name of the innermost function containing `node`."""
    best: tuple[int, str] | None = None
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.lineno <= node.lineno and node.lineno <= (fn.end_lineno or fn.lineno):
            span = (fn.end_lineno or fn.lineno) - fn.lineno
            if best is None or span < best[0]:
                best = (span, fn.name)
    return best[1] if best else None


def _param_cache_writes(tree: ast.Module) -> list[tuple[int, str | None]]:
    """(line, enclosing function) for every `self._param_cache[...] = ...`."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == "_param_cache"):
                out.append((node.lineno, _enclosing_function(tree, node)))
    return out


def test_the_parameter_cache_is_written_only_from_the_tm_path(tree):
    writes = _param_cache_writes(tree)
    assert writes, (
        "no writes to _param_cache found at all — either the cache was renamed "
        "(update this test deliberately) or telemetry ingestion is broken")
    offenders = [(line, fn) for line, fn in writes if fn not in TM_PATH_FUNCTIONS]
    assert not offenders, (
        "the MCS parameter cache — everything the console displays as telemetry "
        "— is being written from outside the TM decommutation path: "
        f"{offenders}. If this is simulator state, the console is no longer a "
        "ground segment; it is a viewer onto the truth model, and it will not "
        "work against a real spacecraft.")


def test_simulator_ingestion_never_touches_the_parameter_cache(tree):
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in SIM_INGEST_FUNCTIONS:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "_param_cache":
                pytest.fail(
                    f"{fn.name}() references _param_cache at line {node.lineno}. "
                    "Simulator state must stay in its own keys (sc_mode, "
                    "sim_health, hk_enabled), tagged with its source.")


def test_sim_sourced_blocks_are_tagged_with_their_provenance():
    """A sim-only value may be shown, but never anonymously."""
    src = SERVER.read_text(encoding="utf-8")
    tagged = ('source="sim_state"' in src
              or "'source': 'sim_state'" in src
              or '"source": "sim_state"' in src)
    assert tagged, (
        "simulator-sourced state is no longer tagged, so the console cannot "
        "tell an operator which values would not exist on a real mission")


def test_the_sim_state_poll_is_gated_on_sim_mode():
    """Pointed at a real spacecraft, the sim poll must not run at all."""
    src = SERVER.read_text(encoding="utf-8")
    assert "_time_source" in src
    assert '_time_source == "sim"' in src, (
        "the simulator state poll is no longer gated on sim mode — a console "
        "connected to a real spacecraft would try to poll a truth API that "
        "does not exist")
