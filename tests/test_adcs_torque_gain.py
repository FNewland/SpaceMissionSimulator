"""Tests for AOCS per-axis control-torque gains (MEM_LOAD / MEM_DUMP).

Feature: the operator can set the gain of the control torque in each body
axis (X, Y, Z) of the ADCS/AOCS via a PUS Service 6 MEM_LOAD telecommand, and
read the current gains back via a MEM_DUMP. A higher gain makes that axis
respond more strongly under the attitude control law; a lower gain weaker.

Memory map (AOCS gain register block):
    0x20100000  torque_gain_x   IEEE-754 big-endian float32
    0x20100004  torque_gain_y   IEEE-754 big-endian float32
    0x20100008  torque_gain_z   IEEE-754 big-endian float32

(a) directly sets gains on the AOCS model and asserts the per-axis commanded
    control torque scales with the gain (2.0 doubles, 0.5 halves, others
    unchanged).
(b) round-trips through the ServiceDispatcher: S6 MEM_LOAD changes the model
    gains; S6 MEM_DUMP returns the values just written (decoding the S6.6
    reply payload).
"""
from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from smo_simulator.models.aocs_basic import AOCSBasicModel
from smo_simulator.service_dispatch import ServiceDispatcher


GAIN_BASE = 0x20100000


@pytest.fixture
def aocs() -> AOCSBasicModel:
    model = AOCSBasicModel()
    model.configure({})
    return model


# ── (a) Control law scales with the per-axis gain ──────────────────────

class TestCommandedTorqueScaling:
    """The commanded control torque per axis scales with its gain."""

    def test_default_gains_are_unity(self, aocs):
        assert aocs.get_torque_gain("x") == 1.0
        assert aocs.get_torque_gain("y") == 1.0
        assert aocs.get_torque_gain("z") == 1.0

    def test_gain_two_doubles_axis_torque(self, aocs):
        axis = [1.0, 1.0, 1.0]
        magnitude = 0.1
        base = aocs._commanded_control_torque(axis, magnitude)

        aocs.set_torque_gain("x", 2.0)
        scaled = aocs._commanded_control_torque(axis, magnitude)

        # X axis doubled, Y and Z unchanged.
        assert scaled[0] == pytest.approx(2.0 * base[0])
        assert scaled[1] == pytest.approx(base[1])
        assert scaled[2] == pytest.approx(base[2])

    def test_gain_half_halves_axis_torque(self, aocs):
        axis = [1.0, 1.0, 1.0]
        magnitude = 0.1
        base = aocs._commanded_control_torque(axis, magnitude)

        aocs.set_torque_gain("y", 0.5)
        scaled = aocs._commanded_control_torque(axis, magnitude)

        # Y axis halved, X and Z unchanged.
        assert scaled[1] == pytest.approx(0.5 * base[1])
        assert scaled[0] == pytest.approx(base[0])
        assert scaled[2] == pytest.approx(base[2])

    def test_each_axis_independent(self, aocs):
        axis = [0.3, -0.6, 0.74]
        magnitude = 0.42
        aocs.set_torque_gain("x", 2.0)
        aocs.set_torque_gain("y", 0.5)
        aocs.set_torque_gain("z", 3.0)
        t = aocs._commanded_control_torque(axis, magnitude)
        assert t[0] == pytest.approx(axis[0] * magnitude * 2.0)
        assert t[1] == pytest.approx(axis[1] * magnitude * 0.5)
        assert t[2] == pytest.approx(axis[2] * magnitude * 3.0)

    def test_nominal_control_law_responds_more_strongly_with_higher_gain(self):
        """A higher X gain produces a larger X-axis attitude change per tick
        in the NOMINAL control law, with the same starting error."""
        from smo_simulator.models import aocs_basic as ab

        def x_step(gain: float) -> float:
            m = AOCSBasicModel()
            m.configure({})
            # Pure X-axis attitude error toward target.
            m._state.q = [0.0, 0.0, 0.0, 1.0]
            m._target_q = [0.2, 0.0, 0.0, 0.9797959]
            m._state.mode = ab.MODE_NOMINAL
            m.set_torque_gain("x", gain)
            q0x = m._state.q[0]
            # Many small ticks; suppress the tiny gaussian jitter by averaging.
            steps = 50
            for _ in range(steps):
                m._tick_nominal(m._state, 1.0)
            return abs(m._state.q[0] - q0x)

        strong = x_step(3.0)
        weak = x_step(1.0)
        assert strong > weak


# ── (b) MEM_LOAD / MEM_DUMP round-trip through the dispatcher ───────────

@pytest.fixture
def dispatcher_with_aocs():
    """ServiceDispatcher whose engine carries a real AOCS model so MEM_LOAD
    writes and MEM_DUMP reads exercise the real gain state. _pack_tm echoes
    its data so the S6.6 reply payload can be decoded."""
    engine = MagicMock()
    aocs = AOCSBasicModel()
    aocs.configure({})
    engine.subsystems = {"aocs": aocs}

    def _pack_tm(service, subtype, data):
        # Return a recognisable structure: (service, subtype, data)
        return (service, subtype, data)

    engine.tm_builder._pack_tm = MagicMock(side_effect=_pack_tm)
    disp = ServiceDispatcher(engine)
    return disp, aocs


def _mem_load(disp, addr, payload):
    return disp.dispatch(6, 2, struct.pack(">I", addr) + payload)


def _mem_dump(disp, addr, length):
    return disp.dispatch(6, 5, struct.pack(">IH", addr, length))


class TestMemLoadDumpRoundTrip:

    def test_mem_load_writes_single_gain(self, dispatcher_with_aocs):
        disp, aocs = dispatcher_with_aocs
        # Write Y gain (offset +4) = 2.5
        _mem_load(disp, GAIN_BASE + 4, struct.pack(">f", 2.5))
        assert aocs.get_torque_gain("y") == pytest.approx(2.5)
        # X and Z untouched.
        assert aocs.get_torque_gain("x") == pytest.approx(1.0)
        assert aocs.get_torque_gain("z") == pytest.approx(1.0)

    def test_mem_load_writes_all_three_gains(self, dispatcher_with_aocs):
        disp, aocs = dispatcher_with_aocs
        _mem_load(disp, GAIN_BASE, struct.pack(">fff", 1.5, 0.25, 4.0))
        assert aocs.get_torque_gain("x") == pytest.approx(1.5)
        assert aocs.get_torque_gain("y") == pytest.approx(0.25)
        assert aocs.get_torque_gain("z") == pytest.approx(4.0)

    def test_mem_dump_returns_current_gains(self, dispatcher_with_aocs):
        disp, aocs = dispatcher_with_aocs
        aocs.set_torque_gain("x", 1.25)
        aocs.set_torque_gain("y", 2.75)
        aocs.set_torque_gain("z", 0.5)

        replies = _mem_dump(disp, GAIN_BASE, 12)
        assert len(replies) == 1
        service, subtype, data = replies[0]
        assert (service, subtype) == (6, 6)
        addr, length = struct.unpack(">IH", data[:6])
        assert addr == GAIN_BASE
        assert length == 12
        gx, gy, gz = struct.unpack(">fff", data[6:18])
        assert gx == pytest.approx(1.25)
        assert gy == pytest.approx(2.75)
        assert gz == pytest.approx(0.5)

    def test_load_then_dump_round_trip(self, dispatcher_with_aocs):
        disp, aocs = dispatcher_with_aocs
        _mem_load(disp, GAIN_BASE, struct.pack(">fff", 3.0, 0.1, 1.9))

        replies = _mem_dump(disp, GAIN_BASE, 12)
        service, subtype, data = replies[0]
        gx, gy, gz = struct.unpack(">fff", data[6:18])
        assert (gx, gy, gz) == pytest.approx((3.0, 0.1, 1.9))

    def test_dump_outside_gain_region_unaffected(self, dispatcher_with_aocs):
        """A dump of an ordinary region still returns synthetic content."""
        disp, _ = dispatcher_with_aocs
        replies = _mem_dump(disp, 0x00200000, 4)  # Config region (0xAA)
        service, subtype, data = replies[0]
        assert (service, subtype) == (6, 6)
        content = data[6:]
        assert content == b"\xAA\xAA\xAA\xAA"


# ── Breakpoint persistence of the new fields ───────────────────────────

def test_gains_persist_through_get_set_state():
    m = AOCSBasicModel()
    m.configure({})
    m.set_torque_gain("x", 2.0)
    m.set_torque_gain("y", 0.5)
    m.set_torque_gain("z", 3.5)
    snap = m.get_state()
    assert snap["torque_gain_x"] == 2.0
    assert snap["torque_gain_y"] == 0.5
    assert snap["torque_gain_z"] == 3.5

    m2 = AOCSBasicModel()
    m2.configure({})
    m2.set_state(snap)
    assert m2.get_torque_gain("x") == 2.0
    assert m2.get_torque_gain("y") == 0.5
    assert m2.get_torque_gain("z") == 3.5
