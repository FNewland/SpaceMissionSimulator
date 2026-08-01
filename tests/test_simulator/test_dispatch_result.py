"""`dispatch()` returns its outcome; the side channels are gone (WP-7 / C-1).

The dispatcher used to signal failure by setting `_last_error`,
`_last_error_code` and `_last_exec_fail` on itself; the engine reset them
before the call and read them after.  That is not reentrant, and three
sources of nested dispatch are live — S11 time-tag release, S19 event-actions
and S18 OBCP steps — so an inner command overwrote the outer command's flag
and the outer command reported the inner one's outcome.

Also covers O-4: the seventeen S1.4 execution-start failure sites passed a
literal `request_id = 0`, and the MCS correlates verification rows by
`tc_seq`, so those refusals could never turn a row red.
"""
from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from smo_common.protocol.ecss_packet import decommutate_packet
from smo_simulator.engine import SimulationEngine
from smo_simulator.service_dispatch import DispatchResult, ServiceDispatcher

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


@pytest.fixture
def engine():
    return SimulationEngine(str(CONFIG_DIR))


# ── The result object ───────────────────────────────────────────────────

def test_dispatch_returns_a_dispatch_result(engine):
    res = engine._dispatcher.dispatch(17, 1, b"", None)
    assert isinstance(res, DispatchResult)
    assert isinstance(res, list), \
        "existing call sites iterate the return value; it must stay a list"
    assert res.failure_kind == ""
    assert res.error is None


def test_failure_travels_back_with_the_packets(engine):
    ttc = engine.subsystems["ttc"]
    ttc._state.downlink_mode = False
    res = engine._dispatcher.dispatch(8, 1, bytes([65, 0x01]), None)  # HIGH
    assert res.failed
    assert res.failure_kind == "execution"
    assert res.error_code == 0x0007
    assert res.reason


def test_exec_start_failure_is_a_distinct_kind(engine):
    # S12 enable of an undefined monitor -> the dispatcher downlinks its own
    # S1.4 rather than letting the engine send an S1.8.
    res = engine._dispatcher.dispatch(12, 3, struct.pack('>H', 0xBEEF), None)
    assert res.failure_kind == "exec_start"
    assert res.error_code == 0x0007


def test_side_channels_are_gone():
    disp = ServiceDispatcher(MagicMock(subsystems={}))
    for gone in ("_last_error", "_last_error_code", "_last_exec_fail"):
        assert not hasattr(disp, gone), \
            f"{gone} still exists — the non-reentrant mechanism is back"


# ── Reentrancy ──────────────────────────────────────────────────────────

def test_nested_dispatch_cannot_clobber_the_outer_result(engine):
    """An inner failing dispatch must not make a good outer command look bad."""
    disp = engine._dispatcher
    real_handle_s17 = disp._handle_s17

    def nested(subtype, data):
        # Fire a guaranteed-to-fail dispatch from inside this one.
        inner = disp.dispatch(8, 1, bytes([65, 0x01]), None)
        assert inner.failed, "the inner command was supposed to fail"
        return real_handle_s17(subtype, data)

    engine.subsystems["ttc"]._state.downlink_mode = False
    disp._handle_s17 = nested
    try:
        outer = disp.dispatch(17, 1, b"", None)
    finally:
        disp._handle_s17 = real_handle_s17
    assert not outer.failed, \
        "the inner failure leaked into the outer command's verdict"


def test_outer_failure_survives_a_successful_nested_dispatch(engine):
    disp = engine._dispatcher
    real = disp._handle_s8

    def nested(subtype, data):
        out = real(subtype, data)
        disp.dispatch(17, 1, b"", None)   # succeeds; must not clear the failure
        return out

    engine.subsystems["ttc"]._state.downlink_mode = False
    disp._handle_s8 = nested
    try:
        res = disp.dispatch(8, 1, bytes([65, 0x01]), None)
    finally:
        disp._handle_s8 = real
    assert res.failed, "a nested success cleared the outer command's failure"


def test_s19_action_failure_does_not_become_the_operators_verdict(engine):
    """An S19 rule firing mid-dispatch has its own, isolated outcome."""
    disp = engine._dispatcher
    engine.subsystems["ttc"]._state.downlink_mode = False
    # Rule: on event 0x1234, run S8.1 func 65 tier 1 (HIGH) — which is gated.
    disp._s19_definitions[9001] = {
        'event_type': 0x1234, 'trigger_kind': 0, 'action_func_id': 65,
        'action_data': bytes([0x01]),
    }
    disp._s19_enabled_ids.add(9001)
    real = disp._handle_s17

    def nested(subtype, data):
        disp.trigger_event_action(0x1234)
        return real(subtype, data)

    disp._handle_s17 = nested
    try:
        res = disp.dispatch(17, 1, b"", None)
    finally:
        disp._handle_s17 = real
    assert not res.failed, \
        "a refused S19 autonomy action became the operator command's result"


# ── O-4: the S1.4 carries the real request id ───────────────────────────

def _s14(packets):
    for raw in packets:
        pkt = decommutate_packet(raw)
        if pkt and pkt.secondary and pkt.secondary.service == 1 \
                and pkt.secondary.subtype == 4:
            return struct.unpack('>HH', pkt.data_field[:4])
    return None


class _Hdr:
    def __init__(self, seq):
        self.sequence_count = seq
        self.apid = 1


def test_exec_start_failure_carries_the_sequence_count(engine):
    res = engine._dispatcher.dispatch(12, 3, struct.pack('>H', 0xBEEF),
                                      _Hdr(4242))
    parsed = _s14(res)
    assert parsed is not None, "no S1.4 emitted"
    request_id, error_code = parsed
    assert request_id == 4242, (
        "S1.4 still carries request_id 0 — the MCS correlates by tc_seq, so "
        "this refusal can never mark its verification row")
    assert error_code == 0x0007


def test_s6_memory_refusal_carries_the_sequence_count(engine):
    # MEM_LOAD with a malformed request.
    res = engine._dispatcher.dispatch(6, 2, b"\x00\x00\x00\x00\x00", _Hdr(77))
    parsed = _s14(res)
    assert parsed is not None
    assert parsed[0] == 77


def test_s19_bad_trigger_kind_carries_the_sequence_count(engine):
    data = struct.pack('>HBH', 5000, 9, 0x0100) + bytes([10])
    res = engine._dispatcher.dispatch(19, 1, data, _Hdr(1234))
    parsed = _s14(res)
    assert parsed is not None
    assert parsed[0] == 1234


def test_no_exec_fail_site_still_passes_a_literal_zero():
    src = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
           / "service_dispatch.py").read_text()
    import re
    bad = re.findall(r"generate_s1_exec_fail\(\s*0\s*,", src)
    assert not bad, f"{len(bad)} S1.4 sites still pass request_id = 0"
