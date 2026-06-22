"""Defect A (Bug 3): OBC must NOT auto-boot to APPLICATION at startup.

Root cause: S19 rules ea_id 4001/4003 had action_func_id 55, which routes to
OBDH obc_boot_app (boots the OBC application) rather than the intended TTC
set-TX-power (func 68). At cold start the TTC BER edge-emits event 0x050C,
firing rule 4003 and booting the OBC app within ~10 simulated seconds, so the
spacecraft left the bootloader on its own.

This test runs the engine loop for ~15 simulated seconds (driving _tick_once
directly — fast, no real sleeps) and asserts the OBC stays in bootloader.
"""
import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = "configs/eosat1"
SW_BOOTLOADER = 0
PARAM_SW_IMAGE = 0x0311
PHASE_BOOT = 3


def _run_engine_seconds(eng, seconds, dt=1.0):
    """Drive the engine loop body deterministically for `seconds` sim-time."""
    n = int(round(seconds / dt))
    for _ in range(n):
        eng._tick_once(dt)


def test_obc_stays_in_bootloader_at_startup():
    eng = SimulationEngine(CONFIG_DIR)
    obdh = eng.subsystems["obdh"]

    # Preconditions: cold start in bootloader.
    assert obdh._state.sw_image == SW_BOOTLOADER
    assert eng.params.get(PARAM_SW_IMAGE) == 0
    assert eng._spacecraft_phase == PHASE_BOOT

    # Run ~15 simulated seconds. Pre-fix, rule 4003 (BER high, event 0x050C)
    # fired func 55 = obc_boot_app within ~10s and the OBC booted the app.
    _run_engine_seconds(eng, 15.0)

    # The OBC must still be in bootloader and the phase must not auto-advance.
    assert obdh._state.sw_image == SW_BOOTLOADER, (
        "OBC auto-booted to APPLICATION — S19 rule mis-mapped to obc_boot_app"
    )
    assert eng.params.get(PARAM_SW_IMAGE) == 0
    assert eng._spacecraft_phase == PHASE_BOOT, (
        "spacecraft phase auto-advanced out of BOOT without operator command"
    )


def test_s19_blocks_obc_critical_func_in_bootloader():
    """Defect A-secondary: the autonomous S19 path refuses OBC-critical funcs
    (52/53/55/56) while the OBC is in bootloader (defense-in-depth)."""
    eng = SimulationEngine(CONFIG_DIR)
    obdh = eng.subsystems["obdh"]
    assert obdh._state.sw_image == SW_BOOTLOADER

    # obc_boot_app (55) must be refused while in bootloader.
    eng._dispatcher._s19_dispatch(9999, 55)
    assert obdh._state.sw_image == SW_BOOTLOADER

    # A non-critical func (e.g. 68 set_tx_power) is still allowed.
    eng._dispatcher._s19_dispatch(9999, 68)
    assert obdh._state.sw_image == SW_BOOTLOADER
