"""Test instructor pass-override toggle path end to end (engine side).

Regression test for the unreliable "OVERRIDE PASSES" button: the engine
``_handle_instructor_cmd`` override branch must

  * accept an explicit desired state (set on / off), publishing 0x05FF; and
  * toggle when no explicit state is provided (robust to a bare command);

and the state the instructor UI polls (``get_state_summary`` for /api/state and
``get_instructor_snapshot``) must report the current override so the button can
reflect the true engine state on every poll.
"""
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine


@pytest.fixture
def engine():
    config_dir = Path(__file__).parent.parent.parent / "configs" / "eosat1"
    return SimulationEngine(config_dir, speed=1.0)


def _drain(engine):
    """Process any queued instructor commands the way the main loop does."""
    engine._drain_instr_queue()


class TestOverrideToggle:
    def test_explicit_enable_sets_flag_and_param(self, engine):
        engine.instr_queue.put_nowait({"type": "override_passes", "enabled": True})
        _drain(engine)
        assert engine._override_passes is True
        assert engine.params[0x05FF] == 1

    def test_explicit_disable_clears_flag_and_param(self, engine):
        engine._override_passes = True
        engine.params[0x05FF] = 1
        engine.instr_queue.put_nowait({"type": "override_passes", "enabled": False})
        _drain(engine)
        assert engine._override_passes is False
        assert engine.params[0x05FF] == 0

    def test_bare_command_toggles(self, engine):
        # No explicit state -> toggle from the current value each time.
        assert engine._override_passes is False
        engine.instr_queue.put_nowait({"type": "override_passes"})
        _drain(engine)
        assert engine._override_passes is True
        assert engine.params[0x05FF] == 1

        engine.instr_queue.put_nowait({"type": "override_passes"})
        _drain(engine)
        assert engine._override_passes is False
        assert engine.params[0x05FF] == 0

    def test_alternate_payload_keys_accepted(self, engine):
        # The handler must accept any of the recognised desired-state keys so a
        # mismatch between UI payload schema and handler can never silently
        # default to False.
        for key in ("on", "value", "state"):
            engine._override_passes = False
            engine.instr_queue.put_nowait({"type": "override_passes", key: True})
            _drain(engine)
            assert engine._override_passes is True, f"key={key} failed to enable"
            assert engine.params[0x05FF] == 1

    def test_repeated_explicit_same_state_is_idempotent(self, engine):
        # Two clicks that both resolve to the same explicit desired state must
        # not flip-flop (guards against the old double-send bug).
        for _ in range(3):
            engine.instr_queue.put_nowait({"type": "override_passes", "enabled": True})
        _drain(engine)
        assert engine._override_passes is True
        assert engine.params[0x05FF] == 1


class TestOverrideReflectedInPolledState:
    def test_state_summary_reports_override(self, engine):
        engine.instr_queue.put_nowait({"type": "override_passes", "enabled": True})
        _drain(engine)
        summary = engine.get_state_summary()
        # The UI's /api/state poll reads this to set the button's visual state.
        assert summary["override_passes"] is True
        assert summary["params"][0x05FF] == 1

        engine.instr_queue.put_nowait({"type": "override_passes", "enabled": False})
        _drain(engine)
        summary = engine.get_state_summary()
        assert summary["override_passes"] is False
        assert summary["params"][0x05FF] == 0

    def test_instructor_snapshot_reports_override(self, engine):
        engine.instr_queue.put_nowait({"type": "override_passes", "enabled": True})
        _drain(engine)
        snap = engine.get_instructor_snapshot()
        assert snap["spacecraft"]["override_passes"] is True
        assert snap["parameters"][0x05FF] == 1
