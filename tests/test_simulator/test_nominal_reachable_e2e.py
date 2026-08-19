"""End-to-end: NOMINAL is reachable from a cold boot, using only telecommands.

The complaint this pins down was "we are always in SAFE mode, never NOMINAL".
Three separate things caused it and all three are exercised here, headlessly,
against the real engine and the shipped configuration:

  * COM-004 now commands ``AOCS_ENABLE_WHEEL`` — nothing used to, so
    ``aocs.wheels_enabled`` stayed 0 for the whole session;
  * the ``aocs.wheels_enabled`` and ``aocs.att_error`` FDIR rules are phase
    gated (``min_phase: 6``), so they cannot latch SAFE during LEOP and
    repeatedly demote ``aocs.mode`` out from under the operator;
  * ``SYS_SET_MISSION_PHASE`` (S8.1 func 120) declares phases 5 and 6 —
    nothing on board advances past 4, and ``sc_mode`` is pinned to SAFE below
    phase 6.

The sequence is deliberately the one an operator would fly: boot the
application, power the AOCS line, enable the wheels, power the star trackers,
acquire the sun, go nadir, declare COMMISSIONING then NOMINAL.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from smo_simulator.engine import SC_NOMINAL, SC_SAFE, SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

#: aocs.mode values that mean "flying the commissioned pointing mode".
#: 4 NOMINAL_NADIR and 8 ECLIPSE_PROPAGATE are the same operational state
#: either side of the terminator — the autonomous eclipse handover is expected.
#: 3 COARSE_SUN / 2 DETUMBLE / 1 SAFE_BOOT / 0 OFF are the demotions that must
#: not happen.
OPERATIONAL_AOCS_MODES = {4, 5, 8}

POWER_LINE_AOCS_WHEELS = 7
POWER_LINE_TTC_TX = 2


@pytest.mark.slow
def test_nominal_is_reachable_and_holds():
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    d = eng._dispatcher

    def tc(*data):
        res = d.dispatch(8, 1, bytes(data), None)
        assert res.error is None, res.error
        return res

    def run(seconds: float):
        for _ in range(int(seconds)):
            eng._tick_once(1.0)

    assert eng._spacecraft_phase == 3, "expected a bootloader cold boot"

    # LEOP-001: boot the application. The phase advances 3 -> 4 on its own.
    tc(55)
    run(20)
    assert eng._spacecraft_phase == 4
    assert eng.sc_mode == SC_SAFE, "phase 4 must still derive SAFE"

    # LEOP-007: power the AOCS suite and the transmitter.
    tc(19, POWER_LINE_AOCS_WHEELS)
    tc(19, POWER_LINE_TTC_TX)
    run(5)
    assert int(eng.params[0x0260]) == 0, (
        "powering the aocs_wheels line must not enable the wheels")

    # COM-004 Step 1: enable all four reaction wheels.
    for wheel in range(4):
        tc(3, wheel)
    run(5)
    assert int(eng.params[0x0260]) == 4

    # COM-003: star trackers on (without them the AOCS legitimately drops to a
    # sun-safe hold on the first eclipse exit).
    tc(4, 1)
    tc(5, 1)
    run(30)

    # COM-013: sun acquisition, then nadir pointing.
    tc(0, 3)
    run(300)
    tc(0, 4)
    run(600)
    assert int(eng.params[0x020F]) in OPERATIONAL_AOCS_MODES

    # Still SAFE — only the phase declaration unlocks NOMINAL.
    assert eng.sc_mode == SC_SAFE

    # LEOP-007 Step 8 and COM-013 Step 14.
    tc(120, 5, 0)
    run(5)
    assert eng._spacecraft_phase == 5 and eng.sc_mode == SC_SAFE
    tc(120, 6, 0)
    run(5)

    assert eng.sc_mode == SC_NOMINAL
    assert int(eng.params[0x0001]) == SC_NOMINAL, "sat.mode must agree"

    # ...and it holds, across a full eclipse cycle, with the now-armed FDIR
    # rules evaluating every tick.
    for minute in range(60):
        run(60)
        mode = int(eng.params[0x020F])
        assert eng.sc_mode == SC_NOMINAL, (
            f"fell out of NOMINAL at t+{minute + 1} min "
            f"(safe_latched={eng._fdir_safe_active}, "
            f"emergency={eng._fdir_emergency_active})")
        assert mode in OPERATIONAL_AOCS_MODES, (
            f"aocs.mode demoted to {mode} at t+{minute + 1} min")
        assert int(eng.params[0x0001]) == eng.sc_mode
