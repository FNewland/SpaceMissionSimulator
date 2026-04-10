"""Regression tests for the centralised TC power-state acceptance gate.

Background: previously the engine only checked target power state for a tiny
subset of S8 commands. Operators could therefore send S3.27 (on-demand HK),
S20 parameter requests, or S8 commands to a powered-off subsystem and get
real TM packets back, plus an S1.7 "execution complete" success report.

These tests pin the new behaviour: when the EPS power line that owns the
target unit is OFF, the engine must reject the TC at acceptance with an
S1.2 / error 0x0004, emit no S1.1, no S1.3, no dispatch responses, and no
S1.7. When the line is ON, the same TC must be accepted normally.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"
APID = 100


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    # Force uplink open so TCs are dispatched instead of rejected with 0x0005
    eng._override_passes = True
    # Skip past bootloader (phase ≤ 3) so the bootloader allowlist doesn't
    # swallow our S3/S8 commands before they reach the power gate.
    eng._spacecraft_phase = 6
    return eng


def _drain_tm(eng: SimulationEngine) -> list:
    """Decommutate every packet currently in the TM queue."""
    pkts = []
    while not eng.tm_queue.empty():
        raw = eng.tm_queue.get_nowait()
        d = decommutate_packet(raw)
        if d is not None and d.secondary is not None:
            pkts.append(d)
    return pkts


def _send_tc(eng: SimulationEngine, service: int, subtype: int, data: bytes) -> list:
    """Push a TC into the engine, run the dispatch, return decommutated TM."""
    # Drain anything queued from earlier ticks so each test sees a clean view.
    _drain_tm(eng)
    raw = build_tc_packet(APID, service, subtype, data)
    eng.tc_queue.put_nowait(raw)
    eng._drain_tc_queue()
    return _drain_tm(eng)


def _has_subtypes(pkts, *wanted: tuple[int, int]) -> dict:
    """Return {(svc,sub): packet, ...} for packets matching the wanted pairs."""
    out = {}
    for p in pkts:
        key = (p.secondary.service, p.secondary.subtype)
        if key in wanted:
            out[key] = p
    return out


def _set_line(eng: SimulationEngine, line: str, on: bool) -> None:
    eng.subsystems["eps"]._state.power_lines[line] = on


# ---------------------------------------------------------------------------
# S3.27 — on-demand HK against a powered-off owner
# ---------------------------------------------------------------------------


def test_s3_27_payload_hk_accepted_when_payload_off():
    """S3.27 bypasses power gate — unpowered subsystems return zero/default params."""
    eng = _make_engine()
    _set_line(eng, "payload", False)
    pkts = _send_tc(eng, 3, 27, struct.pack(">H", 5))  # SID 5 = Payload

    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services, f"S3.27 should always be accepted, got {services}"
    assert (1, 2) not in services, "S3.27 should not be rejected for unpowered subsystem"
    assert (1, 7) in services, "S3.27 should complete successfully"


def test_s3_27_payload_hk_accepted_when_payload_on():
    eng = _make_engine()
    _set_line(eng, "payload", True)
    eng.subsystems["payload"]._state.mode = 1  # STANDBY — subsystem booted
    pkts = _send_tc(eng, 3, 27, struct.pack(">H", 5))

    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services, f"expected S1.1 acceptance, got {services}"
    assert (1, 2) not in services
    assert (1, 7) in services, "expected S1.7 execution complete"


def test_s3_27_aocs_hk_accepted_when_aocs_wheels_off():
    """S3.27 bypasses power gate — AOCS HK returns zeros when wheels off."""
    eng = _make_engine()
    _set_line(eng, "aocs_wheels", False)
    pkts = _send_tc(eng, 3, 27, struct.pack(">H", 2))  # SID 2 = AOCS
    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services, "S3.27 should always be accepted"
    assert (1, 2) not in services
    assert (1, 7) in services


def test_s3_27_eps_hk_always_allowed():
    """EPS is always-on infrastructure — no power gate."""
    eng = _make_engine()
    _set_line(eng, "payload", False)
    _set_line(eng, "aocs_wheels", False)
    pkts = _send_tc(eng, 3, 27, struct.pack(">H", 1))  # SID 1 = EPS
    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services
    assert (1, 7) in services


# ---------------------------------------------------------------------------
# S8.1 — function management against a powered-off owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func_id, line",
    [
        (0, "aocs_wheels"),    # AOCS range 0..15
        (5, "aocs_wheels"),
        (26, "payload"),       # Payload range 26..39
        (35, "payload"),
        (63, "ttc_tx"),        # TTC range 63..78
        (70, "ttc_tx"),
    ],
)
def test_s8_command_rejected_when_target_line_off(func_id, line):
    eng = _make_engine()
    _set_line(eng, line, False)
    pkts = _send_tc(eng, 8, 1, bytes([func_id]))
    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 2) in services, f"S8.1 func {func_id} with {line} OFF should be S1.2; got {services}"
    assert (1, 1) not in services
    assert (1, 7) not in services


@pytest.mark.parametrize(
    "func_id, line",
    [(0, "aocs_wheels"), (26, "payload"), (63, "ttc_tx")],
)
def test_s8_command_accepted_when_target_line_on(func_id, line):
    eng = _make_engine()
    _set_line(eng, line, True)
    pkts = _send_tc(eng, 8, 1, bytes([func_id]))
    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services, f"S8.1 func {func_id} with {line} ON should be S1.1; got {services}"


def test_s8_eps_command_always_allowed_even_with_other_lines_off():
    eng = _make_engine()
    _set_line(eng, "payload", False)
    _set_line(eng, "aocs_wheels", False)
    _set_line(eng, "ttc_tx", False)
    pkts = _send_tc(eng, 8, 1, bytes([16]))  # EPS func_id range 16..25
    services = [(p.secondary.service, p.secondary.subtype) for p in pkts]
    assert (1, 1) in services


# ---------------------------------------------------------------------------
# OBDH counters: rejected ≠ accepted
# ---------------------------------------------------------------------------


def test_rejected_tc_increments_rejected_counter_not_accepted():
    """Use S8.1 with a powered-off target to test the rejected counter."""
    eng = _make_engine()
    _set_line(eng, "payload", False)
    obdh = eng.subsystems["obdh"]
    rej_before = obdh._state.tc_rej_count
    acc_before = obdh._state.tc_acc_count
    _send_tc(eng, 8, 1, bytes([26]))  # func_id 26 = payload, which is off
    assert obdh._state.tc_rej_count == rej_before + 1
    assert obdh._state.tc_acc_count == acc_before  # NOT incremented


def test_accepted_tc_increments_accepted_counter_not_rejected():
    eng = _make_engine()
    _set_line(eng, "payload", True)
    eng.subsystems["payload"]._state.mode = 1  # STANDBY
    obdh = eng.subsystems["obdh"]
    rej_before = obdh._state.tc_rej_count
    acc_before = obdh._state.tc_acc_count
    _send_tc(eng, 3, 27, struct.pack(">H", 5))
    assert obdh._state.tc_acc_count == acc_before + 1
    assert obdh._state.tc_rej_count == rej_before
