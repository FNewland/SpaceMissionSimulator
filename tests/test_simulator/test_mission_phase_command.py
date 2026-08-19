"""SYS_SET_MISSION_PHASE — the telecommand that owns the mission phase.

``Engine._tick_spacecraft_phase`` only implements the automatic power-up chain
1->2->3->4 and the reboot revert; nothing on board ever sets 5 or 6. The phase
is not decorative — it gates which subsystems tick, telecommand acceptance
(phase <= 3 restricts the uplink to the bootloader subset), the derived
sc_mode / sat.mode, arming of the advanced FDIR model and of the phase-gated
legacy rules, the separation timer, and the propulsion arm interlock
(``prop.yaml min_phase: 6``). Stuck at 4, no burn can ever be armed and the
console can never read anything but SAFE. Hence S8.1 func 120.
"""
from __future__ import annotations

from pathlib import Path

import re

import pytest
import yaml

from smo_simulator.engine import (
    SC_NOMINAL, SC_PHASE_NOMINAL, SC_SAFE, SimulationEngine,
)
from smo_simulator.service_dispatch import ServiceDispatcher

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

FUNC = 120
EV_PHASE_CHANGED = 0x0055

ERR_RANGE = 0x0C01
ERR_PRE_SEPARATION = 0x0C02
ERR_BOOTLOADER = 0x0C03
ERR_BACKWARDS = 0x0C04


def _engine(phase: int = 6) -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    if phase > 3:
        eng.apply_phase_change(phase)
    return eng


def _set_phase(eng, phase: int, flags: int = 0):
    return eng._dispatcher.dispatch(8, 1, bytes([FUNC, phase, flags]), None)


def _events(eng) -> list[dict]:
    import queue
    out = []
    while True:
        try:
            out.append(eng.event_queue.get_nowait())
        except queue.Empty:
            return out


# ── catalogue ───────────────────────────────────────────────────────

def test_catalogued_with_a_typed_schema_and_critical():
    cat = yaml.safe_load(
        (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())["commands"]
    entry = next(c for c in cat if c.get("name") == "SYS_SET_MISSION_PHASE")
    assert (entry["service"], entry["subtype"], entry["func_id"]) == (8, 1, 120)
    assert entry["criticality"] == "critical"
    fields = {f["name"]: f for f in entry["fields"]}
    assert fields["phase"]["type"] == "uint8"
    assert fields["flags"]["type"] == "uint8"
    # The description is the operations documentation for what the phase does.
    for gate in ("bootloader", "sc_mode", "prop.yaml", "FORCE"):
        assert gate in entry["description"]


def test_func_id_120_does_not_collide():
    cat = yaml.safe_load(
        (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())["commands"]
    owners = [c["name"] for c in cat
              if c.get("service") == 8 and c.get("func_id") == 120]
    assert owners == ["SYS_SET_MISSION_PHASE"]


def test_event_is_catalogued():
    cat = yaml.safe_load(
        (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())["events"]
    entry = next(e for e in cat if int(e["id"]) == EV_PHASE_CHANGED)
    assert entry["name"] == "MISSION_PHASE_CHANGED"


def test_failure_codes_have_meanings():
    from smo_common.protocol.pus_services import FAILURE_CODES
    for code in (ERR_RANGE, ERR_PRE_SEPARATION, ERR_BOOTLOADER, ERR_BACKWARDS):
        assert code in FAILURE_CODES, f"0x{code:04X} has no vocabulary entry"


# ── the happy path ──────────────────────────────────────────────────

def test_leop_to_commissioning_to_nominal():
    eng = _engine(phase=4)
    assert _set_phase(eng, 5).error is None
    assert eng._spacecraft_phase == 5
    assert _set_phase(eng, 6).error is None
    assert eng._spacecraft_phase == 6


def test_declaring_nominal_unlocks_sc_mode():
    eng = _engine(phase=4)
    eng._fdir_safe_active = False
    eng._fdir_emergency_active = False
    eng.params[0x0600] = 0
    eng._update_sc_mode()
    assert eng.sc_mode == SC_SAFE          # phase gate, not a contingency

    _set_phase(eng, 5)
    _set_phase(eng, 6)
    eng._update_sc_mode()
    assert eng.sc_mode == SC_NOMINAL
    assert int(eng.params[0x0001]) == SC_NOMINAL


def test_phase_parameter_is_published_immediately():
    eng = _engine(phase=4)
    _set_phase(eng, 6)
    assert int(eng.params[0x0129]) == 6


def test_the_phase_sticks_across_ticks():
    eng = _engine(phase=4)
    _set_phase(eng, 6)
    for _ in range(30):
        eng._tick_once(1.0)
    assert eng._spacecraft_phase == SC_PHASE_NOMINAL


def test_accepted_change_emits_the_event_with_both_phases():
    eng = _engine(phase=4)
    _events(eng)
    _set_phase(eng, 5)
    evs = [e for e in _events(eng) if e.get("event_id") == EV_PHASE_CHANGED]
    assert len(evs) == 1
    assert evs[0]["from_phase"] == 4 and evs[0]["to_phase"] == 5
    assert "LEOP" in evs[0]["description"]
    assert "COMMISSIONING" in evs[0]["description"]


def test_redeclaring_the_same_phase_is_an_accepted_no_op():
    eng = _engine(phase=6)
    res = _set_phase(eng, 6)
    assert res.error is None
    assert eng._spacecraft_phase == 6
    evs = [e for e in _events(eng) if e.get("event_id") == EV_PHASE_CHANGED]
    assert evs and evs[-1]["from_phase"] == evs[-1]["to_phase"]


# ── guards ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("phase", [7, 9, 200, 255])
def test_out_of_range_is_refused(phase):
    eng = _engine(phase=6)
    res = _set_phase(eng, phase)
    assert res.error and res.error["error_code"] == ERR_RANGE
    assert eng._spacecraft_phase == 6


@pytest.mark.parametrize("phase", [0, 1, 2])
@pytest.mark.parametrize("flags", [0, 1])
def test_pre_separation_phases_are_never_commandable(phase, flags):
    """Even FORCE must not reach them: phases 0-1 stop the ticks that carry
    the uplink, so accepting one would lock the operator out for good."""
    eng = _engine(phase=6)
    res = _set_phase(eng, phase, flags)
    assert res.error and res.error["error_code"] == ERR_PRE_SEPARATION
    assert eng._spacecraft_phase == 6


@pytest.mark.parametrize("phase", [4, 5, 6])
def test_application_phase_refused_while_in_the_bootloader(phase):
    eng = _engine(phase=3)
    assert eng.subsystems["obdh"]._state.sw_image == 0
    res = _set_phase(eng, phase)
    assert res.error and res.error["error_code"] == ERR_BOOTLOADER
    assert "OBC_BOOT_APP" in res.error["reason"]
    assert eng._spacecraft_phase == 3


def test_the_command_is_reachable_from_the_bootloader():
    """The phase gate must not lock the operator out of the phase command."""
    from smo_common.protocol.ecss_packet import build_tc_packet
    eng = _engine(phase=3)
    raw = build_tc_packet(apid=0x01, service=8, subtype=1,
                          data=bytes([FUNC, 6, 0]), seq_count=1)
    eng._dispatch_tc(raw)
    # Refused by the command's own bootloader guard (0x0C03), NOT by the
    # blanket bootloader-subset gate (0x0006).
    codes = [e.get("error_code") for e in _events(eng)
             if e.get("event_id") == SimulationEngine.EV_COMMAND_REJECTED]
    assert 0x0006 not in codes, "func 120 is not in the bootloader subset"
    assert ERR_BOOTLOADER in codes


def test_backwards_transition_refused_without_force():
    eng = _engine(phase=6)
    res = _set_phase(eng, 5, flags=0)
    assert res.error and res.error["error_code"] == ERR_BACKWARDS
    assert "FORCE" in res.error["reason"]
    assert eng._spacecraft_phase == 6


def test_backwards_transition_allowed_with_force():
    eng = _engine(phase=6)
    res = _set_phase(eng, 4, flags=ServiceDispatcher.PHASE_FLAG_FORCE)
    assert res.error is None
    assert eng._spacecraft_phase == 4
    evs = [e for e in _events(eng) if e.get("event_id") == EV_PHASE_CHANGED]
    assert evs[-1]["forced"] is True
    assert "FORCED" in evs[-1]["description"]


def test_forward_transition_does_not_need_force():
    eng = _engine(phase=4)
    assert _set_phase(eng, 6, flags=0).error is None
    assert eng._spacecraft_phase == 6


def test_missing_argument_is_refused_not_defaulted():
    eng = _engine(phase=4)
    res = eng._dispatcher.dispatch(8, 1, bytes([FUNC]), None)
    assert res.error is not None
    assert eng._spacecraft_phase == 4


# ── what the phase actually gates ───────────────────────────────────

def test_propulsion_interlock_needs_the_command():
    """prop.yaml min_phase: 6 — unreachable before this command existed."""
    prop = yaml.safe_load(
        (CONFIG_DIR / "subsystems" / "prop.yaml").read_text())
    assert int(prop["interlocks"]["min_phase"]) == 6


def test_advanced_fdir_arms_only_once_declared():
    eng = _engine(phase=4)
    assert eng._spacecraft_phase < SC_PHASE_NOMINAL
    _set_phase(eng, 5)
    _set_phase(eng, 6)
    assert eng._spacecraft_phase >= SC_PHASE_NOMINAL


# ── the procedure cards that declare it ─────────────────────────────

@pytest.mark.parametrize("card,phase", [
    # 2026-08-19 flow redesign: the declarations moved to the true terminal
    # cards -- phase 5 is declared by LEOP-003 (initial orbit determination,
    # the LAST card in LEOP execution order), phase 6 by COM-013 (the
    # commissioning closure card). LEOP-007 no longer declares anything.
    ("leop/initial_orbit_determination.md", 5),
    ("commissioning/adcs_commissioning.md", 6),
])
def test_the_terminal_cards_command_the_declaration(card, phase):
    text = (CONFIG_DIR / "procedures" / card).read_text()
    assert f"SYS_SET_MISSION_PHASE(phase={phase}, flags=0)" in text
    assert "func_id 120" in text
    # The cards write the verify as "(0x0129) = N" (procedure style).
    assert re.search(rf"`eps\.spacecraft_phase` \(0x0129\) ==? {phase}", text)


def test_leop_card_no_longer_claims_there_is_no_telecommand():
    text = (CONFIG_DIR / "procedures" / "leop"
            / "sequential_power_on.md").read_text()
    assert "There is NO flight telecommand" not in text
