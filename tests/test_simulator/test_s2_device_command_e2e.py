"""S2 device-access commands must be ACCEPTED by the engine and actually
toggle device state end-to-end (regression).

Two bugs made every S2 device command (GPS receiver, star trackers, ...) fail:
1. service 2 was missing from the engine's `known_services` acceptance set, so
   the command was rejected with S1.2 (unknown service) and never executed.
2. `_handle_s2` indexed the PrimaryHeader object as if it were bytes
   (`len(primary_header)`), raising mid-dispatch.
"""
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine
from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet

CONFIG = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def _engine():
    if not CONFIG.exists():
        pytest.skip("eosat1 config not present")
    eng = SimulationEngine(CONFIG, speed=1.0)
    eng._override_passes = True
    eng._in_contact = True
    eng._spacecraft_phase = 6  # nominal (out of bootloader)
    return eng


def _send_s2(eng, hexstr):
    eng._dispatch_tc(build_tc_packet(100, 2, 1, bytes.fromhex(hexstr)))
    subs = []
    while not eng.tm_queue.empty():
        d = decommutate_packet(eng.tm_queue.get_nowait())
        if d and d.secondary:
            subs.append((d.secondary.service, d.secondary.subtype))
    return subs


def test_s2_gps_device_command_accepted_and_executes():
    eng = _engine()
    aocs = eng.subsystems["aocs"]
    assert aocs._state.device_states.get(0x020F) is True

    subs = _send_s2(eng, "020F00")  # GPS receiver OFF
    # Accepted (S1.1) + executed to completion (S1.7), NOT rejected (S1.2)
    assert (1, 1) in subs, f"S2 command not accepted (S1.1 missing): {subs}"
    assert (1, 2) not in subs, f"S2 command was rejected with S1.2: {subs}"
    assert (1, 7) in subs, f"S2 command did not complete (S1.7 missing): {subs}"
    assert aocs._state.device_states.get(0x020F) is False

    subs = _send_s2(eng, "020F01")  # back ON
    assert (1, 2) not in subs
    assert aocs._state.device_states.get(0x020F) is True


def test_s2_status_report_returns_s2_6():
    eng = _engine()
    subs = _send_s2(eng, "020F")  # S2.6 report status (device_id only)
    # Should not be rejected; a S2.6 status report is returned
    assert (1, 2) not in subs
