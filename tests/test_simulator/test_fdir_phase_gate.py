"""Per-rule phase gating of the legacy FDIR rule set, and the one spacecraft mode.

Background. ``mission.yaml`` boots into the bootloader on every run, and
``Engine._enter_bootloader_mode`` leaves all four reaction wheels disabled, so
``aocs.wheels_enabled`` (0x0260) reads 0 on the first tick AOCS runs. The
shipped level-2 rule ``aocs.wheels_enabled < 2`` therefore latched immediately,
pinned the derived ``sc_mode`` to SAFE and drove ``safe_mode_aocs``, which
demotes ``aocs.mode`` out of NOMINAL — the "always SAFE, never NOMINAL"
complaint. The ``aocs.att_error > 5`` rule piled on, because a large attitude
error is the EXPECTED state while an uncommissioned spacecraft detumbles and is
slewed around during AOCS checkout.

The fix is a per-rule ``min_phase`` in ``subsystems/fdir.yaml`` rather than a
blanket ``phase >= 6`` on the loop: the rest of the rule set is hardware
protection (battery, thermal, reboot count, CAN bus) which must stay armed
through LEOP.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_simulator.engine import (
    SC_SAFE, SC_NOMINAL, SC_PHASE_NOMINAL, SimulationEngine,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

#: Rules that are deliberately phase-gated, and nothing else.
GATED_RULES = {("aocs.wheels_enabled", "safe_mode_aocs"),
               ("aocs.att_error", "safe_mode_aocs")}


@pytest.fixture(scope="module")
def engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


# ── configuration ───────────────────────────────────────────────────

def test_fdir_yaml_gates_exactly_the_two_aocs_rules():
    """Only the rules whose trigger IS the expected uncommissioned state."""
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "fdir.yaml").read_text())
    gated = {(r["parameter"], r["action"]) for r in cfg["rules"]
             if r.get("min_phase") is not None}
    assert gated == GATED_RULES, (
        "the phase gate has spread beyond the two AOCS rules it is for; "
        f"gated = {sorted(gated)}")


def test_hardware_protection_rules_are_not_gated():
    """Battery, thermal, reboot and bus rules must stay armed through LEOP."""
    cfg = yaml.safe_load((CONFIG_DIR / "subsystems" / "fdir.yaml").read_text())
    still_armed = {"eps.bat_soc", "eps.bus_voltage", "tcs.temp_battery",
                   "obdh.temp", "obdh.reboot_count", "obdh.can_bus_status",
                   "ttc.pa_temp", "aocs.rw1_temp", "aocs.rw2_temp",
                   "aocs.rw3_temp", "aocs.rw4_temp"}
    offenders = [r["parameter"] for r in cfg["rules"]
                 if r["parameter"] in still_armed
                 and r.get("min_phase") is not None]
    assert not offenders, (
        f"hardware-protection rules must not be phase-gated: {offenders}")


def test_min_phase_defaults_to_no_gate():
    from smo_common.config.schemas import FDIRRuleConfig
    rule = FDIRRuleConfig(parameter="eps.bat_soc", condition="< 20",
                          threshold=20.0, action="payload_poweroff")
    assert rule.min_phase is None


# ── arming ──────────────────────────────────────────────────────────

def _rule(engine, parameter, action):
    for r in engine._fdir_rules:
        if r.parameter == parameter and r.action == action:
            return r
    raise AssertionError(f"rule {parameter}/{action} not in the shipped config")


@pytest.mark.parametrize("parameter,action", sorted(GATED_RULES))
@pytest.mark.parametrize("phase", [0, 1, 2, 3, 4, 5])
def test_gated_rules_are_disarmed_below_nominal(engine, parameter, action,
                                                phase):
    engine._spacecraft_phase = phase
    assert not engine._fdir_rule_armed(_rule(engine, parameter, action))


@pytest.mark.parametrize("parameter,action", sorted(GATED_RULES))
def test_gated_rules_arm_at_nominal(engine, parameter, action):
    engine._spacecraft_phase = SC_PHASE_NOMINAL
    assert engine._fdir_rule_armed(_rule(engine, parameter, action))


@pytest.mark.parametrize("phase", [0, 3, 4, 6])
def test_ungated_rules_are_armed_in_every_phase(engine, phase):
    engine._spacecraft_phase = phase
    for rule in engine._fdir_rules:
        if (rule.parameter, rule.action) in GATED_RULES:
            continue
        assert engine._fdir_rule_armed(rule), (
            f"{rule.parameter}/{rule.action} disarmed at phase {phase}")


# ── behaviour ───────────────────────────────────────────────────────

def test_wheels_off_in_leop_does_not_latch_safe():
    """The whole complaint, reduced to one tick."""
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = 4                 # LEOP
    eng.params[0x0260] = 0.0                  # all wheels disabled
    eng._tick_fdir()
    assert not eng._fdir_safe_active
    key = "aocs.wheels_enabled_safe_mode_aocs"
    assert not eng._fdir_triggered.get(key, False)


def test_wheels_off_when_commissioned_still_latches_safe():
    """The gate must not throw the protection away, only defer it."""
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = SC_PHASE_NOMINAL
    eng.params[0x0260] = 0.0
    eng._tick_fdir()
    assert eng._fdir_safe_active
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE


def test_dropping_below_min_phase_disarms_and_clears_the_latch():
    """A stale latch would survive an instructor rewind or an OBC reboot."""
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = SC_PHASE_NOMINAL
    eng.params[0x0260] = 0.0
    eng._tick_fdir()
    assert eng._fdir_safe_active

    eng._spacecraft_phase = 4                 # reboot back to LEOP
    eng._tick_fdir()
    assert not eng._fdir_safe_active
    assert not eng._fdir_triggered["aocs.wheels_enabled_safe_mode_aocs"]


def test_att_error_persistence_accumulator_is_reset_when_disarmed():
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    key = "aocs.att_error_safe_mode_aocs"
    eng._spacecraft_phase = SC_PHASE_NOMINAL
    eng.params[0x0217] = 90.0
    for _ in range(10):
        eng._tick_fdir()
    assert eng._fdir_persist[key] == 10.0

    eng._spacecraft_phase = 5
    eng._tick_fdir()
    assert eng._fdir_persist[key] == 0.0


# ── sat.mode (0x0001) is sc_mode, not a second opinion ──────────────

def test_sat_mode_parameter_equals_sc_mode_in_every_derivation():
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    for phase in (3, 4, 5, 6):
        for safe in (False, True):
            for emergency in (False, True):
                eng._spacecraft_phase = phase
                eng._fdir_safe_active = safe
                eng._fdir_emergency_active = emergency
                eng._update_sc_mode()
                assert int(eng.params[0x0001]) == eng.sc_mode, (
                    f"sat.mode disagrees with sc_mode at phase={phase} "
                    f"safe={safe} emergency={emergency}")


def test_sat_mode_is_safe_during_leop_even_with_no_contingency():
    """The old derivation read NOMINAL here while the console read SAFE."""
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = 4
    eng._fdir_safe_active = False
    eng._fdir_emergency_active = False
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE
    assert int(eng.params[0x0001]) == SC_SAFE


def test_sat_mode_reaches_nominal_when_commissioned():
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng._spacecraft_phase = SC_PHASE_NOMINAL
    eng._fdir_safe_active = False
    eng._fdir_emergency_active = False
    eng.params[0x0600] = 0
    eng._update_sc_mode()
    assert int(eng.params[0x0001]) == SC_NOMINAL


def test_parameter_catalogue_documents_the_four_value_scale():
    params = yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    entry = next(p for p in params["parameters"] if int(p["id"]) == 0x0001)
    desc = entry["description"]
    for token in ("SAFE", "NOMINAL", "SCIENCE", "EMERGENCY"):
        assert token in desc, f"sat.mode description omits {token}: {desc}"


# ── the other half of the fix: wheels need an explicit command ──────

def test_powering_the_eps_line_does_not_enable_the_wheels():
    """Powering aocs_wheels energises the drive electronics, nothing more."""
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    d = eng._dispatcher
    d.dispatch(8, 1, bytes([55]), None)          # OBC_BOOT_APP
    for _ in range(20):
        eng._tick_once(1.0)
    d.dispatch(8, 1, bytes([19, 7]), None)       # POWER_LINE_ON(aocs_wheels)
    for _ in range(5):
        eng._tick_once(1.0)

    eps = eng.subsystems["eps"]
    assert eps._state.power_lines["aocs_wheels"] is True
    assert int(eng.params[0x0260]) == 0, (
        "the EPS power line must not auto-enable the reaction wheels")

    for wheel in range(4):
        d.dispatch(8, 1, bytes([3, wheel]), None)   # AOCS_ENABLE_WHEEL
    for _ in range(3):
        eng._tick_once(1.0)
    assert int(eng.params[0x0260]) == 4


def test_com_004_commands_all_four_wheels_on_with_a_verify():
    """COM-004 is where the wheels get enabled; the card must say so."""
    card = (CONFIG_DIR / "procedures" / "commissioning"
            / "aocs_actuator_checkout.md").read_text()
    for wheel in range(4):
        assert f"AOCS_ENABLE_WHEEL(wheel_idx={wheel})" in card
    assert "func_id 3" in card
    assert "`aocs.wheels_enabled` (0x0260) == 4" in card
    # ...and early: before the first spin-up test.
    assert card.index("AOCS_ENABLE_WHEEL") < card.index("RW_SET_SPEED_BIAS")
