"""Engine-side S11 store→fire path (defects #44/#46).

Proves that a TC(11,4) whose payload is exec_time(4) + a COMPLETE embedded
TC packet (as the fixed MCS builder now produces) is stored by the on-board
scheduler and, once the OBC clock passes the tag, executes the embedded
command and emits S1 verification for it. Also documents why the old
MCS payload format (bare svc+st+data) could never execute.
"""
import struct

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = "configs/eosat1"


@pytest.fixture
def engine():
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6  # nominal ops (out of bootloader)
    # Keep the RF link open so S1 verification TM reaches tm_queue.
    eng._override_passes = True
    # Energise the payload power line so payload TCs pass the power gate.
    eps = eng.subsystems["eps"]
    if hasattr(eps, "_state") and hasattr(eps._state, "power_lines"):
        eps._state.power_lines["payload"] = True
    return eng


def _drain_tm(engine):
    pkts = []
    while not engine.tm_queue.empty():
        pkts.append(engine.tm_queue.get_nowait())
    return pkts


def _fire_due(engine, at_cuc):
    """Mirror of the engine tick's S11 release (engine.py `_tick_once`)."""
    due = engine._tc_scheduler.tick(at_cuc)
    for tc_pkt in due:
        engine._dispatch_tc(tc_pkt)
    return due


class TestS11StoreAndFire:
    def test_full_packet_payload_is_stored_and_acked(self, engine):
        # Onboard CUC is full-precision seconds; the S11 wire field is u32
        # whole seconds.
        cuc = int(engine._get_cuc_time())
        inner = build_tc_packet(apid=1, service=17, subtype=1, data=b"",
                                seq_count=70)
        outer = build_tc_packet(
            apid=1, service=11, subtype=4,
            data=struct.pack(">I", cuc + 30) + inner, seq_count=71)
        engine._dispatch_tc(outer)
        assert engine._tc_scheduler.get_status()["count"] == 1
        # TM(11,5) insert ack present
        acks = [decommutate_packet(p) for p in _drain_tm(engine)]
        assert any(p and p.secondary and p.secondary.service == 11
                   and p.secondary.subtype == 5 for p in acks)

    @staticmethod
    def _fine_pointing_ok(payload):
        """Phase C WP-6: capture is gated on AOCS FINE_POINT with pointing
        error < 0.05°. These tests exercise the S11 machinery, not the
        pointing chain — stamp a compliant last-seen AOCS state."""
        payload._state.aocs_seen = True
        payload._state.aocs_mode_seen = 5
        payload._state.aocs_total_err_seen = 0.01
        payload._state.aocs_rate_stab_seen = 0.001
        payload._state.aocs_settled_seen = True

    def test_scheduled_capture_fires_and_executes(self, engine):
        payload = engine.subsystems["payload"]
        payload._state.mode = 2  # IMAGING
        payload._state.fpa_ready = True
        self._fine_pointing_ok(payload)
        images_before = payload._state.image_count

        # Onboard CUC is full-precision seconds; the S11 wire field is u32
        # whole seconds.
        cuc = int(engine._get_cuc_time())
        inner_data = bytes([28]) + struct.pack(">ff", 45.5, -75.25)
        inner = build_tc_packet(apid=1, service=8, subtype=1,
                                data=inner_data, seq_count=77)
        outer = build_tc_packet(
            apid=1, service=11, subtype=4,
            data=struct.pack(">I", cuc + 5) + inner, seq_count=78)

        engine._dispatch_tc(outer)
        assert engine._tc_scheduler.get_status()["count"] == 1
        _drain_tm(engine)

        # Not due yet
        assert _fire_due(engine, cuc + 4) == []
        assert payload._state.image_count == images_before

        # Past the time tag: embedded TC executes
        due = _fire_due(engine, cuc + 6)
        assert len(due) == 1
        assert payload._state.image_count == images_before + 1
        assert engine._tc_scheduler.get_status()["count"] == 0

        # S1.1 (acceptance) and S1.7 (completion) for the EMBEDDED seq 77
        s1 = [decommutate_packet(p) for p in _drain_tm(engine)]
        s1 = [p for p in s1 if p and p.secondary and p.secondary.service == 1
              and len(p.data_field) >= 4]
        seqs_by_subtype = {}
        for p in s1:
            req_id = struct.unpack(">I", p.data_field[:4])[0]
            seqs_by_subtype.setdefault(p.secondary.subtype,
                                       set()).add(req_id & 0x3FFF)
        assert 77 in seqs_by_subtype.get(1, set()), "no S1.1 for embedded TC"
        assert 77 in seqs_by_subtype.get(7, set()), "no S1.7 for embedded TC"

    def test_fire_order_preserved_for_multi_step_activity(self, engine):
        """set_mode then capture, as the S11 upload path schedules them."""
        payload = engine.subsystems["payload"]
        payload._state.mode = 0
        payload._state.fpa_ready = True
        self._fine_pointing_ok(payload)
        images_before = payload._state.image_count

        # Onboard CUC is full-precision seconds; the S11 wire field is u32
        # whole seconds.
        cuc = int(engine._get_cuc_time())
        set_mode = build_tc_packet(apid=1, service=8, subtype=1,
                                   data=bytes([26, 2]), seq_count=80)
        capture = build_tc_packet(
            apid=1, service=8, subtype=1,
            data=bytes([28]) + struct.pack(">ff", 1.0, 2.0), seq_count=81)
        for t, pkt, seq in ((cuc + 10, set_mode, 90), (cuc + 16, capture, 91)):
            outer = build_tc_packet(apid=1, service=11, subtype=4,
                                    data=struct.pack(">I", t) + pkt,
                                    seq_count=seq)
            engine._dispatch_tc(outer)
        assert engine._tc_scheduler.get_status()["count"] == 2

        _fire_due(engine, cuc + 12)
        assert payload._state.mode == 2
        assert payload._state.image_count == images_before

        _fire_due(engine, cuc + 20)
        assert payload._state.image_count == images_before + 1

    def test_legacy_bare_payload_never_executes(self, engine):
        """Regression guard for defect #46: the OLD MCS builder embedded
        svc+st+data with no CCSDS framing — stored fine, dead at fire."""
        payload = engine.subsystems["payload"]
        payload._state.mode = 2
        payload._state.fpa_ready = True
        images_before = payload._state.image_count

        # Onboard CUC is full-precision seconds; the S11 wire field is u32
        # whole seconds.
        cuc = int(engine._get_cuc_time())
        outer = build_tc_packet(
            apid=1, service=11, subtype=4,
            data=struct.pack(">I", cuc + 5) + bytes([8, 1, 28]),
            seq_count=99)
        engine._dispatch_tc(outer)
        assert engine._tc_scheduler.get_status()["count"] == 1

        due = _fire_due(engine, cuc + 10)
        assert len(due) == 1  # released...
        # ...but dropped as malformed: no execution, no state change
        assert payload._state.image_count == images_before
