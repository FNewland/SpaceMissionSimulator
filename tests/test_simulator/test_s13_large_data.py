"""PUS S13 Large Data Transfer — edge paths.

S13 is the image-download service (service_dispatch.py ``_handle_s13``). Until
now only its happy path was exercised, and only indirectly from the RF
acceptance suite: create a transfer, pull a segment, end it. The failure paths —
segment request for a transfer that does not exist, ending a transfer twice,
status of an unknown transfer, malformed requests, two transfers in flight at
once — had zero coverage, and they are precisely the paths an operator hits
when a pass drops halfway through a dump.

These tests drive ``_handle_s13`` through the shared ``service_dispatcher``
fixture and inspect the packets it asks ``tm_builder._pack_tm`` to build, so
both the response *and* the session bookkeeping are checked.
"""
import struct

import pytest


BLOCK_BYTES = 1024


# ── Helpers ─────────────────────────────────────────────────────────

def _packs(mock_engine):
    """(service, subtype, data) for every TM packet built so far."""
    out = []
    for call in mock_engine.tm_builder._pack_tm.call_args_list:
        kw = call.kwargs
        out.append((kw.get("service"), kw.get("subtype"), kw.get("data", b"")))
    return out


def _last_pack(mock_engine):
    packs = _packs(mock_engine)
    assert packs, "no TM packet was built"
    return packs[-1]


def _create(dispatcher, scene_id=7, size_mb=4):
    """S13.1 create-transfer; returns the assigned transfer id."""
    dispatcher._handle_s13(1, struct.pack(">HH", scene_id, size_mb))
    return max(dispatcher._s13_transfers)


# ── Session creation ────────────────────────────────────────────────

class TestCreateTransfer:

    def test_create_returns_s13_2_with_transfer_id(self, service_dispatcher, mock_engine):
        service_dispatcher._handle_s13(1, struct.pack(">HH", 42, 8))
        service, subtype, data = _last_pack(mock_engine)
        assert (service, subtype) == (13, 2)
        transfer_id = struct.unpack(">H", data[:2])[0]
        assert transfer_id in service_dispatcher._s13_transfers

    def test_create_records_scene_size_and_block_count(self, service_dispatcher):
        tid = _create(service_dispatcher, scene_id=99, size_mb=2)
        xfer = service_dispatcher._s13_transfers[tid]
        assert xfer["scene_id"] == 99
        assert xfer["size_mb"] == 2
        assert xfer["status"] == "ACTIVE"
        assert xfer["blocks_sent"] == 0
        expected_blocks = (2 * 1024 * 1024 + BLOCK_BYTES - 1) // BLOCK_BYTES
        assert xfer["total_blocks"] == expected_blocks

    def test_zero_size_falls_back_to_default_image_size(self, service_dispatcher):
        """With no live payload model the fallback constant is used, and it is
        the configured 4 MB image, not the old hard-coded 800 (defect #82)."""
        from smo_simulator.service_dispatch import DEFAULT_IMAGE_SIZE_MB
        tid = _create(service_dispatcher, scene_id=1, size_mb=0)
        assert DEFAULT_IMAGE_SIZE_MB == 4
        assert service_dispatcher._s13_transfers[tid]["size_mb"] == 4

    def test_uncatalogued_scene_is_refused_when_a_payload_is_attached(
            self, service_dispatcher, mock_engine):
        """2026-08b PL-c: S13.1 binds to a REAL product.

        This test used to assert that a transfer opened successfully for
        scene 1 against a freshly-configured (therefore EMPTY) catalogue, and
        was sized from `image_size_mb`. That was the defect: a transfer could
        be created, run and completed for an image that had never been taken.
        With a real payload in the loop the session is now refused and NAKed.
        """
        from smo_simulator.models.payload_basic import PayloadBasicModel

        payload = PayloadBasicModel()
        payload.configure({"image_size_mb": 12.0, "total_storage_mb": 20000.0})
        mock_engine.subsystems["payload"] = payload
        service_dispatcher._handle_s13(1, struct.pack(">HH", 1, 0))
        assert service_dispatcher._s13_transfers == {}
        assert _last_pack(mock_engine)[:2] == (13, 4), "expected a TM(13,4) NAK"

    def test_catalogued_scene_is_sized_from_the_product(
            self, service_dispatcher, mock_engine):
        """The session takes its size from the product, not from the wire."""
        from smo_simulator.models.payload_basic import PayloadBasicModel

        payload = PayloadBasicModel()
        payload.configure({"image_size_mb": 12.0, "total_storage_mb": 20000.0})
        payload._state.mode = 2
        payload._state.fpa_ready = True
        cap = payload.handle_command({"command": "capture", "scene_id": 77,
                                      "lines": 2048})
        assert cap["success"], cap
        mock_engine.subsystems["payload"] = payload
        # `size_mb` on the wire is deliberately wrong — it must be ignored.
        service_dispatcher._handle_s13(1, struct.pack(">HH", 77, 999))
        tid = max(service_dispatcher._s13_transfers)
        xfer = service_dispatcher._s13_transfers[tid]
        assert xfer["size_bytes"] == payload.image_size_bytes(
            payload.find_image(77))
        assert xfer["total_blocks"] == payload.image_block_count(
            payload.find_image(77))
        assert xfer["size_mb"] != 999

    def test_configured_image_size_matches_the_mission_config(self):
        """The mission config is the single source of the image size."""
        import yaml
        from pathlib import Path
        from smo_simulator.service_dispatch import DEFAULT_IMAGE_SIZE_MB
        cfg = yaml.safe_load(
            (Path(__file__).resolve().parents[2] / "configs" / "eosat1"
             / "subsystems" / "payload.yaml").read_text())
        assert cfg["image_size_mb"] == 4.0
        assert DEFAULT_IMAGE_SIZE_MB == cfg["image_size_mb"]

    def test_transfer_ids_are_unique_across_creates(self, service_dispatcher):
        ids = {_create(service_dispatcher, scene_id=i, size_mb=1) for i in range(5)}
        assert len(ids) == 5, f"transfer ids collided: {sorted(ids)}"

    def test_short_create_request_is_rejected_without_session(
            self, service_dispatcher, mock_engine):
        service_dispatcher._handle_s13(1, b"\x00")  # < 4 bytes
        assert service_dispatcher._s13_transfers == {}
        assert _packs(mock_engine) == []


# ── Segment download / NAK ──────────────────────────────────────────

class TestSegmentDownload:

    def test_segment_returns_block_and_advances_progress(
            self, service_dispatcher, mock_engine):
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 5))
        service, subtype, data = _last_pack(mock_engine)
        assert (service, subtype) == (13, 4)
        got_tid, got_seg = struct.unpack(">HH", data[:4])
        assert (got_tid, got_seg) == (tid, 5)
        assert len(data) == 4 + BLOCK_BYTES
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 6

    def test_segment_for_unknown_transfer_is_naked(
            self, service_dispatcher, mock_engine):
        """Unknown transfer id must produce a short S13.4 with no block payload.

        The operator's client distinguishes "here is your block" from "no such
        transfer" purely by the length of the S13.4 it gets back.
        """
        service_dispatcher._handle_s13(3, struct.pack(">HH", 4242, 0))
        service, subtype, data = _last_pack(mock_engine)
        assert (service, subtype) == (13, 4)
        assert data == struct.pack(">H", 4242), (
            "NAK for an unknown transfer must not carry a data block")
        assert service_dispatcher._s13_transfers == {}, (
            "a segment request must not create a session")

    def test_segment_for_paused_transfer_returns_nothing(
            self, service_dispatcher, mock_engine):
        tid = _create(service_dispatcher)
        service_dispatcher._s13_transfers[tid]["status"] = "PAUSED"
        mock_engine.tm_builder._pack_tm.reset_mock()
        responses = service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 1))
        assert responses == []
        assert _packs(mock_engine) == []
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 0

    def test_short_segment_request_is_ignored(self, service_dispatcher, mock_engine):
        tid = _create(service_dispatcher)
        mock_engine.tm_builder._pack_tm.reset_mock()
        assert service_dispatcher._handle_s13(3, struct.pack(">H", tid)) == []
        assert _packs(mock_engine) == []

    def test_repeated_segment_request_is_idempotent(self, service_dispatcher):
        """Re-requesting a block (ground-side NAK / retransmit) is safe."""
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 3))
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 3))
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 4

    def test_out_of_order_segment_rewinds_progress(self, service_dispatcher):
        """Progress tracks the last segment served, so a rewind is visible.

        Pins current behaviour: ``blocks_sent`` is set from the requested
        segment number rather than accumulated, so a retransmit of an earlier
        block moves the reported progress backwards.
        """
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 10))
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 11
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 2))
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 3


# ── Abort / end of transfer ─────────────────────────────────────────

class TestEndTransfer:

    def test_end_removes_the_session(self, service_dispatcher):
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        assert tid not in service_dispatcher._s13_transfers

    def test_abort_mid_transfer_leaves_no_state_behind(self, service_dispatcher):
        """Abort after partial delivery: no session, no leaked bookkeeping."""
        tid = _create(service_dispatcher, size_mb=16)
        for seg in range(3):
            service_dispatcher._handle_s13(3, struct.pack(">HH", tid, seg))
        assert service_dispatcher._s13_transfers[tid]["blocks_sent"] == 3

        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        assert service_dispatcher._s13_transfers == {}

    def test_end_of_unknown_transfer_is_a_no_op(self, service_dispatcher, mock_engine):
        assert service_dispatcher._handle_s13(5, struct.pack(">H", 999)) == []
        assert _packs(mock_engine) == []

    def test_double_end_does_not_raise(self, service_dispatcher):
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        assert service_dispatcher._s13_transfers == {}

    def test_segment_after_abort_is_naked(self, service_dispatcher, mock_engine):
        """A ground station that keeps pulling after an abort gets a NAK."""
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        mock_engine.tm_builder._pack_tm.reset_mock()
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 0))
        service, subtype, data = _last_pack(mock_engine)
        assert (service, subtype) == (13, 4)
        assert data == struct.pack(">H", tid)

    def test_short_end_request_is_ignored(self, service_dispatcher):
        tid = _create(service_dispatcher)
        assert service_dispatcher._handle_s13(5, b"\x00") == []
        assert tid in service_dispatcher._s13_transfers


# ── Status reporting ────────────────────────────────────────────────

class TestTransferStatus:

    def test_status_reports_progress(self, service_dispatcher, mock_engine):
        tid = _create(service_dispatcher, size_mb=1)
        total = service_dispatcher._s13_transfers[tid]["total_blocks"]
        service_dispatcher._handle_s13(3, struct.pack(">HH", tid, 99))

        service_dispatcher._handle_s13(9, struct.pack(">H", tid))
        service, subtype, data = _last_pack(mock_engine)
        assert (service, subtype) == (13, 10)
        got_tid, sent, total_blocks, status = struct.unpack(">HIIB", data[:11])
        assert got_tid == tid
        assert sent == 100
        assert total_blocks == total
        assert status == 1  # ACTIVE

    def test_status_of_unknown_transfer_returns_nothing(
            self, service_dispatcher, mock_engine):
        assert service_dispatcher._handle_s13(9, struct.pack(">H", 1234)) == []
        assert _packs(mock_engine) == []

    def test_status_after_abort_returns_nothing(self, service_dispatcher):
        tid = _create(service_dispatcher)
        service_dispatcher._handle_s13(5, struct.pack(">H", tid))
        assert service_dispatcher._handle_s13(9, struct.pack(">H", tid)) == []

    def test_status_code_reflects_paused_state(self, service_dispatcher, mock_engine):
        tid = _create(service_dispatcher)
        service_dispatcher._s13_transfers[tid]["status"] = "PAUSED"
        service_dispatcher._handle_s13(9, struct.pack(">H", tid))
        _, _, data = _last_pack(mock_engine)
        assert struct.unpack(">HIIB", data[:11])[3] == 2  # PAUSED


# ── Concurrency and unsupported subtypes ────────────────────────────

class TestConcurrentTransfers:

    def test_two_transfers_progress_independently(self, service_dispatcher):
        a = _create(service_dispatcher, scene_id=1, size_mb=4)
        b = _create(service_dispatcher, scene_id=2, size_mb=4)
        assert a != b

        service_dispatcher._handle_s13(3, struct.pack(">HH", a, 7))
        assert service_dispatcher._s13_transfers[a]["blocks_sent"] == 8
        assert service_dispatcher._s13_transfers[b]["blocks_sent"] == 0

    def test_aborting_one_transfer_leaves_the_other_running(self, service_dispatcher):
        a = _create(service_dispatcher, scene_id=1)
        b = _create(service_dispatcher, scene_id=2)
        service_dispatcher._handle_s13(5, struct.pack(">H", a))
        assert a not in service_dispatcher._s13_transfers
        assert service_dispatcher._s13_transfers[b]["status"] == "ACTIVE"
        assert service_dispatcher._s13_transfers[b]["scene_id"] == 2

    def test_blocks_are_segment_derived_without_a_payload_model(
            self, service_dispatcher, mock_engine):
        """With no real payload attached, the legacy ramp is still served.

        Two transfers asking for the same segment get identical blocks because
        there is no product to derive them from. This is the UNIT-harness
        fallback only; `test_blocks_come_from_the_product` pins the real
        behaviour, where the bytes differ per scene.
        """
        a = _create(service_dispatcher, scene_id=1)
        b = _create(service_dispatcher, scene_id=2)
        service_dispatcher._handle_s13(3, struct.pack(">HH", a, 4))
        block_a = _last_pack(mock_engine)[2][4:]
        service_dispatcher._handle_s13(3, struct.pack(">HH", b, 4))
        block_b = _last_pack(mock_engine)[2][4:]
        assert block_a == block_b

    def test_blocks_come_from_the_product(self, service_dispatcher, mock_engine):
        """2026-08b PL-c: with a real payload the bytes ARE the product.

        Two different scenes served the same segment index must differ, and the
        bytes must equal what the payload model derives for that product — the
        old handler answered `(segment_num + i) % 256` for every scene, so no
        downlink could be checksummed and no corruption was visible.
        """
        from smo_simulator.models.payload_basic import PayloadBasicModel

        payload = PayloadBasicModel()
        payload.configure({"image_size_mb": 4.0, "total_storage_mb": 20000.0})
        payload._state.mode = 2
        payload._state.fpa_ready = True
        payload.handle_command({"command": "capture", "scene_id": 11,
                                "lines": 2048})
        payload.handle_command({"command": "capture", "scene_id": 12,
                                "lines": 2048})
        mock_engine.subsystems["payload"] = payload

        service_dispatcher._handle_s13(1, struct.pack(">HH", 11, 0))
        a = max(service_dispatcher._s13_transfers)
        service_dispatcher._handle_s13(1, struct.pack(">HH", 12, 0))
        b = max(service_dispatcher._s13_transfers)

        service_dispatcher._handle_s13(3, struct.pack(">HH", a, 4))
        block_a = _last_pack(mock_engine)[2][4:]
        service_dispatcher._handle_s13(3, struct.pack(">HH", b, 4))
        block_b = _last_pack(mock_engine)[2][4:]

        assert len(block_a) == BLOCK_BYTES
        assert block_a != block_b, "two scenes served identical bytes"
        assert block_a == payload.image_block(11, 4)
        assert block_b == payload.image_block(12, 4)


class TestUnsupportedRequests:

    @pytest.mark.parametrize("subtype", [0, 2, 4, 6, 7, 8, 10, 11, 255])
    def test_unsupported_subtype_is_ignored(self, service_dispatcher, mock_engine,
                                            subtype):
        assert service_dispatcher._handle_s13(subtype, b"\x00\x01\x00\x02") == []
        assert _packs(mock_engine) == []

    def test_dispatch_routes_service_13(self, service_dispatcher, mock_engine):
        """The public dispatch() entry point reaches the S13 handler."""
        service_dispatcher.dispatch(13, 1, struct.pack(">HH", 3, 1))
        assert service_dispatcher._s13_transfers, "dispatch() did not reach S13"
        assert _last_pack(mock_engine)[:2] == (13, 2)

    def test_abandoned_transfer_is_not_auto_reaped(self, service_dispatcher):
        """There is no S13 session timeout — sessions live until ended.

        Documents a known gap rather than asserting a behaviour that exists: a
        dump abandoned by a lost pass stays in ``_s13_transfers`` forever, so
        the only cleanup is an explicit S13.5. If a timeout is ever added, this
        test should be replaced with one that asserts the expiry.
        """
        tid = _create(service_dispatcher)
        for _ in range(100):
            service_dispatcher.dispatch(17, 1, b"")  # unrelated traffic
        assert tid in service_dispatcher._s13_transfers
        assert service_dispatcher._s13_transfers[tid]["status"] == "ACTIVE"
