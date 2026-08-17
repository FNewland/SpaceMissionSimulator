"""Exactly ONE 0x0200 AOCS_MODE_CHANGE event, and its labels are right.

`engine._check_subsystem_events` used to emit a SECOND 0x0200 event on every
mode change, built from a five-entry table
(`{0: OFF, 1: DETUMBLE, 2: SAFE, 3: NADIR, 4: TARGET}`) left over from before
the nine-mode machine.  Against the live enum
(`aocs_basic._MODE_NAMES`: OFF / SAFE_BOOT / DETUMBLE / COARSE_SUN / NOMINAL /
FINE_POINT / SLEW / DESAT / ECLIPSE / INERTIAL) every label was wrong and modes
5-9 rendered as "?", so a NOMINAL -> ECLIPSE transition downlinked
"AOCS mode: TARGET -> ?" next to the model's correct event.

The AOCS model owns the event; the duplicate is deleted.
"""
from __future__ import annotations

import queue
from pathlib import Path

import pytest

from smo_simulator.models.aocs_basic import (
    MODE_ECLIPSE, MODE_NOMINAL, _MODE_NAMES,
)

from tests.test_simulator.test_aocs_dynamics import make_orbit, settled_model

REPO = Path(__file__).resolve().parents[2]
ENGINE_SRC = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
              / "engine.py").read_text()


class _FakeEngine:
    def __init__(self):
        self._model_event_queue = queue.Queue()


# ── the model emits one correct event ───────────────────────────────────

def test_nominal_to_eclipse_emits_exactly_one_correct_event():
    m, orbit, params = settled_model(MODE_NOMINAL, ticks=1200)
    eng = _FakeEngine()
    m._engine = eng
    while not eng._model_event_queue.empty():
        eng._model_event_queue.get_nowait()

    eclipsed = make_orbit(in_eclipse=True)
    for _ in range(60):
        m.tick(1.0, eclipsed, params)
    assert m._state.mode == MODE_ECLIPSE

    mode_events = []
    while not eng._model_event_queue.empty():
        ev = eng._model_event_queue.get_nowait()
        if ev.get("event_id") == 0x0200:
            mode_events.append(ev)

    assert len(mode_events) == 1, (
        f"expected one 0x0200 event for NOMINAL -> ECLIPSE, got "
        f"{[e['description'] for e in mode_events]}")
    desc = mode_events[0]["description"]
    assert "NOMINAL" in desc and "ECLIPSE" in desc, desc
    assert "?" not in desc, f"a mode rendered as '?': {desc}"
    # The stale table's vocabulary must not appear anywhere.
    for stale in ("TARGET", "NADIR"):
        assert stale not in desc, f"stale mode label in the event: {desc}"


def test_every_live_mode_has_a_label():
    """The table the event uses must cover the whole enum, 0-9."""
    assert set(_MODE_NAMES) == set(range(10))
    assert all(_MODE_NAMES[i] for i in range(10))


# ── the engine no longer emits its own ──────────────────────────────────

def test_the_engine_does_not_emit_a_second_mode_event():
    """`_check_subsystem_events` must not be a second 0x0200 emitter."""
    start = ENGINE_SRC.index("def _check_subsystem_events")
    end = ENGINE_SRC.index("def ", ENGINE_SRC.index("Payload mode change",
                                                    start))
    body = ENGINE_SRC[start:end]
    assert "'event_id': 0x0200" not in body and '"event_id": 0x0200' not in body


def test_the_stale_five_entry_mode_table_is_gone():
    assert "3: 'NADIR', 4: 'TARGET'" not in ENGINE_SRC
    assert "_prev_aocs_mode" not in ENGINE_SRC.replace(
        "There is no `_prev_aocs_mode`", ""), \
        "the edge-detection state for the deleted duplicate is still here"
