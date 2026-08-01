"""Defect #46 — the MCS S11.4 builder must embed a COMPLETE CCSDS/PUS TC.

The simulator stores ``data[4:]`` of an S11.4 payload verbatim and runs
``decommutate_packet`` on it at fire time; a bare svc+st+data blob dies as
"malformed packet". The MCS now re-embeds such payloads server-side
(`MCSServer._embed_s11_packet`) and offers /api/schedule/upload-s11 which
converts a planner activity into correctly-framed time-tagged TCs
(defect #44).
"""
import struct
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.protocol.ecss_packet import decommutate_packet, TIME_EPOCH
from smo_common.protocol.framing import FRAME_HEADER_SIZE
from smo_mcs.server import MCSServer
from smo_common.config.schemas import MCSDisplayConfig


def _make_server():
    with patch("smo_mcs.server.load_mcs_displays",
               return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        return MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
        )


class _FakeWriter:
    """Captures framed TC bytes written by the server."""

    def __init__(self):
        self.buffer = b""

    def is_closing(self):
        return False

    def write(self, data: bytes):
        self.buffer += data

    async def drain(self):
        pass

    def frames(self) -> list[bytes]:
        out, buf = [], self.buffer
        while len(buf) >= FRAME_HEADER_SIZE:
            n = struct.unpack(">H", buf[:FRAME_HEADER_SIZE])[0]
            out.append(buf[FRAME_HEADER_SIZE:FRAME_HEADER_SIZE + n])
            buf = buf[FRAME_HEADER_SIZE + n:]
        return out


class TestEmbedS11Packet:
    def test_bare_svc_st_payload_is_wrapped_into_full_packet(self):
        server = _make_server()
        raw = struct.pack(">I", 12345) + bytes([17, 1])
        out = server._embed_s11_packet(11, 4, raw)
        assert out[:4] == raw[:4]  # exec time preserved
        parsed = decommutate_packet(out[4:])
        assert parsed is not None and parsed.secondary is not None
        assert parsed.secondary.service == 17
        assert parsed.secondary.subtype == 1
        assert parsed.crc_valid

    def test_wrapped_payload_carries_inner_data(self):
        server = _make_server()
        inner_data = bytes([28]) + struct.pack(">ff", 45.5, -75.25)
        raw = struct.pack(">I", 999) + bytes([8, 1]) + inner_data
        out = server._embed_s11_packet(11, 4, raw)
        parsed = decommutate_packet(out[4:])
        assert parsed.secondary.service == 8
        assert parsed.secondary.subtype == 1
        assert parsed.data_field == inner_data

    def test_already_embedded_full_packet_untouched(self):
        server = _make_server()
        inner = server._tc_manager.build_command(17, 1, b"")
        raw = struct.pack(">I", 777) + inner
        assert server._embed_s11_packet(11, 4, raw) == raw

    def test_non_s11_data_passes_through(self):
        server = _make_server()
        data = bytes([8, 1, 28])
        assert server._embed_s11_packet(8, 1, data) == data
        assert server._embed_s11_packet(11, 7, b"\x00\x01") == b"\x00\x01"

    def test_embedded_tc_tracked_for_verification(self):
        server = _make_server()
        raw = struct.pack(">I", 5) + bytes([17, 1])
        server._embed_s11_packet(11, 4, raw)
        entries = [e for e in server._verification_log
                   if e["state"] == "SCHEDULED"]
        assert entries and entries[0]["service"] == 17


class TestS11ScheduleUploadEndpoint:
    async def _client(self, server) -> TestClient:
        app = web.Application()
        app.router.add_post("/api/schedule/upload-s11",
                            server._handle_s11_schedule_upload)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_activity_becomes_time_tagged_tcs(self):
        server = _make_server()
        writer = _FakeWriter()
        server._tc_writer = writer
        client = await self._client(server)
        start = datetime.now(timezone.utc) + timedelta(minutes=10)
        try:
            resp = await client.post("/api/schedule/upload-s11", json={
                "name": "imaging_pass",
                "start_time": start.isoformat(),
                "steps": [
                    {"service": 8, "subtype": 1, "func_id": 0x1A,
                     "params": [2], "description": "PAYLOAD_SET_MODE_IMAGING"},
                    {"wait_s": 5},
                    {"wait_for": {"parameter": "payload.mode", "value": 2}},
                    # Phase E WP-1: capture carries scene_id + lines in
                    # ENGINEERING units via the shared catalogue encoder
                    # (geolocation is stamped on board, not commanded).
                    {"service": 8, "subtype": 1, "func_id": 0x1C,
                     "description": "PAYLOAD_CAPTURE",
                     "args": {"scene_id": 1042, "lines": 2048}},
                ],
            })
            body = await resp.json()
            assert resp.status == 200
            assert body["status"] == "scheduled"
            assert body["count"] == 2
            assert body["start_time"] == start.isoformat()
            assert len(body["skipped"]) == 1  # the wait_for

            frames = writer.frames()
            assert len(frames) == 2
            outers = [decommutate_packet(f) for f in frames]
            for outer in outers:
                assert outer.secondary.service == 11
                assert outer.secondary.subtype == 4
                embedded = decommutate_packet(outer.data_field[4:])
                assert embedded is not None and embedded.secondary is not None
                assert embedded.crc_valid

            # First embedded TC: payload set-mode(2); second: capture with
            # scene_id + lines (Phase E WP-1 wire form: 1C 04 12 08 00)
            e0 = decommutate_packet(outers[0].data_field[4:])
            assert e0.data_field == bytes([0x1A, 2])
            e1 = decommutate_packet(outers[1].data_field[4:])
            assert e1.data_field == bytes.fromhex("1c04120800")
            scene_id, lines = struct.unpack(">HH", e1.data_field[1:5])
            assert scene_id == 1042
            assert lines == 2048

            # Exec times: T and T+6 (5 s wait + 1 s ordering gap), in CUC
            t0 = struct.unpack(">I", outers[0].data_field[:4])[0]
            t1 = struct.unpack(">I", outers[1].data_field[:4])[0]
            assert t1 - t0 == 6
            start_naive = start.astimezone(timezone.utc).replace(tzinfo=None)
            expected_cuc = int((start_naive - TIME_EPOCH).total_seconds())
            assert abs(t0 - expected_cuc) <= 1
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_start_time_required(self):
        server = _make_server()
        server._tc_writer = _FakeWriter()
        client = await self._client(server)
        try:
            resp = await client.post("/api/schedule/upload-s11", json={
                "name": "x",
                "steps": [{"service": 17, "subtype": 1}],
            })
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_no_tc_connection_returns_503(self):
        server = _make_server()
        server._tc_writer = None
        client = await self._client(server)
        try:
            resp = await client.post("/api/schedule/upload-s11", json={
                "name": "x",
                "start_time": "2026-03-11T10:00:00Z",
                "steps": [{"service": 17, "subtype": 1}],
            })
            assert resp.status == 503
        finally:
            await client.close()


class TestPusCommandS11Wrap:
    @pytest.mark.asyncio
    async def test_ui_style_s11_payload_wrapped_on_send(self):
        """The MCS UI sends exec_time+svc+st+data; the server must uplink a
        payload whose embedded bytes decommutate as a full TC."""
        server = _make_server()
        writer = _FakeWriter()
        server._tc_writer = writer

        app = web.Application()
        app.router.add_post("/api/pus-command", server._handle_pus_command)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            data_hex = struct.pack(">I", 4242).hex() + "1101"  # S17.1 at 4242
            resp = await client.post("/api/pus-command", json={
                "service": 11, "subtype": 4, "data_hex": data_hex,
                "name": "SCHEDULE_TC",
            })
            assert (await resp.json())["status"] == "sent"
            outer = decommutate_packet(writer.frames()[0])
            assert outer.secondary.service == 11
            assert struct.unpack(">I", outer.data_field[:4])[0] == 4242
            embedded = decommutate_packet(outer.data_field[4:])
            assert embedded is not None and embedded.secondary is not None
            assert embedded.secondary.service == 17
            assert embedded.secondary.subtype == 1
        finally:
            await client.close()
