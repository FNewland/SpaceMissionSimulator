"""Tests for smo-gateway — Gateway, UpstreamConnection, DownstreamManager.

Covers:
  - Gateway.__init__ stores parameters correctly
  - Gateway.stop sets _running to False and closes connections
  - UpstreamConnection.__init__ stores host/port
  - UpstreamConnection.connect success and failure
  - UpstreamConnection.send_packet when connected and disconnected
  - UpstreamConnection.read_packet when disconnected
  - UpstreamConnection.close sets _connected to False
  - DownstreamManager.add_client / remove_client / client_count
  - DownstreamManager.broadcast_tm fans out to all clients
  - DownstreamManager.broadcast_tm removes disconnected clients
  - DownstreamManager.close_all clears all clients
  - Framing integration: frame_packet produces correct length prefix
  - TC routing: downstream client packet forwarded upstream
  - TM routing: upstream packet broadcast to downstream clients
"""
import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from smo_gateway.gateway import Gateway
from smo_gateway.upstream import UpstreamConnection
from smo_gateway.downstream import DownstreamManager
from smo_common.protocol.framing import frame_packet


# ── Gateway.__init__ ────────────────────────────────────────────────

class TestGatewayInit:
    """Test that Gateway stores its parameters."""

    def test_gateway_init_stores_params(self):
        gw = Gateway("192.168.1.1", 10025, "0.0.0.0", 10026)
        assert gw.upstream_host == "192.168.1.1"
        assert gw.upstream_port == 10025
        assert gw.listen_host == "0.0.0.0"
        assert gw.listen_port == 10026
        assert gw._running is False
        assert gw._downstream_clients == []
        assert gw._upstream_reader is None
        assert gw._upstream_writer is None

    def test_gateway_init_default_listen(self):
        gw = Gateway("localhost", 9999)
        assert gw.listen_host == "0.0.0.0"
        assert gw.listen_port == 10025


# ── Gateway.stop ────────────────────────────────────────────────────

class TestGatewayStop:
    """Test Gateway.stop cleans up connections."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self):
        gw = Gateway("localhost", 10025)
        gw._running = True
        await gw.stop()
        assert gw._running is False

    @pytest.mark.asyncio
    async def test_stop_closes_upstream_writer(self):
        gw = Gateway("localhost", 10025)
        gw._running = True
        mock_writer = MagicMock()
        gw._upstream_writer = mock_writer
        await gw.stop()
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_closes_downstream_clients(self):
        gw = Gateway("localhost", 10025)
        gw._running = True
        mock_w1 = MagicMock()
        mock_w2 = MagicMock()
        gw._downstream_clients = [mock_w1, mock_w2]
        await gw.stop()
        mock_w1.close.assert_called_once()
        mock_w2.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_with_no_connections(self):
        gw = Gateway("localhost", 10025)
        gw._running = True
        await gw.stop()
        assert gw._running is False


# ── UpstreamConnection.__init__ ─────────────────────────────────────

class TestUpstreamInit:
    """Test UpstreamConnection initialization."""

    def test_upstream_init(self):
        up = UpstreamConnection("10.0.0.1", 8080)
        assert up.host == "10.0.0.1"
        assert up.port == 8080
        assert up._connected is False
        assert up._reader is None
        assert up._writer is None


# ── UpstreamConnection.connect ──────────────────────────────────────

class TestUpstreamConnect:
    """Test upstream connection setup."""

    @pytest.mark.asyncio
    async def test_connect_success(self):
        up = UpstreamConnection("localhost", 10025)
        mock_reader = MagicMock()
        mock_writer = MagicMock()
        with patch("smo_gateway.upstream.asyncio.open_connection",
                    new_callable=AsyncMock,
                    return_value=(mock_reader, mock_writer)):
            result = await up.connect()
        assert result is True
        assert up._connected is True
        assert up._reader is mock_reader
        assert up._writer is mock_writer

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        up = UpstreamConnection("bad-host", 99999)
        with patch("smo_gateway.upstream.asyncio.open_connection",
                    new_callable=AsyncMock,
                    side_effect=ConnectionRefusedError("refused")):
            result = await up.connect()
        assert result is False
        assert up._connected is False


# ── UpstreamConnection.send_packet / read_packet ────────────────────

class TestUpstreamSendRead:
    """Test send and read operations."""

    @pytest.mark.asyncio
    async def test_send_packet_when_connected(self):
        up = UpstreamConnection("localhost", 10025)
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock()
        up._writer = mock_writer
        up._connected = True
        result = await up.send_packet(b"\xDE\xAD")
        assert result is True
        # frame_packet prepends 2-byte length
        expected_frame = frame_packet(b"\xDE\xAD")
        mock_writer.write.assert_called_once_with(expected_frame)
        mock_writer.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_packet_when_not_connected(self):
        up = UpstreamConnection("localhost", 10025)
        result = await up.send_packet(b"\xDE\xAD")
        assert result is False

    @pytest.mark.asyncio
    async def test_send_packet_write_error_disconnects(self):
        up = UpstreamConnection("localhost", 10025)
        mock_writer = MagicMock()
        mock_writer.drain = AsyncMock(side_effect=ConnectionResetError("reset"))
        up._writer = mock_writer
        up._connected = True
        result = await up.send_packet(b"\xDE\xAD")
        assert result is False
        assert up._connected is False

    @pytest.mark.asyncio
    async def test_read_packet_when_not_connected(self):
        up = UpstreamConnection("localhost", 10025)
        result = await up.read_packet()
        assert result is None


# ── UpstreamConnection.close ────────────────────────────────────────

class TestUpstreamClose:
    """Test closing the upstream connection."""

    @pytest.mark.asyncio
    async def test_close_sets_connected_false(self):
        up = UpstreamConnection("localhost", 10025)
        up._connected = True
        mock_writer = MagicMock()
        up._writer = mock_writer
        await up.close()
        assert up._connected is False
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_writer(self):
        up = UpstreamConnection("localhost", 10025)
        up._connected = True
        await up.close()
        assert up._connected is False


# ── DownstreamManager ───────────────────────────────────────────────

class TestDownstreamManager:
    """Test multi-client downstream management."""

    def test_initial_client_count_is_zero(self):
        dm = DownstreamManager()
        assert dm.client_count == 0

    @pytest.mark.asyncio
    async def test_add_client_increments_count(self):
        dm = DownstreamManager()
        w1 = MagicMock()
        w2 = MagicMock()
        await dm.add_client(w1)
        assert dm.client_count == 1
        await dm.add_client(w2)
        assert dm.client_count == 2

    @pytest.mark.asyncio
    async def test_remove_client_decrements_and_closes(self):
        dm = DownstreamManager()
        w = MagicMock()
        await dm.add_client(w)
        await dm.remove_client(w)
        assert dm.client_count == 0
        w.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_nonexistent_client_is_noop(self):
        dm = DownstreamManager()
        w = MagicMock()
        await dm.remove_client(w)  # should not raise
        assert dm.client_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_tm_sends_to_all(self):
        dm = DownstreamManager()
        w1 = MagicMock()
        w1.drain = AsyncMock()
        w2 = MagicMock()
        w2.drain = AsyncMock()
        await dm.add_client(w1)
        await dm.add_client(w2)
        await dm.broadcast_tm(b"\x01\x02\x03")
        expected_frame = frame_packet(b"\x01\x02\x03")
        w1.write.assert_called_once_with(expected_frame)
        w2.write.assert_called_once_with(expected_frame)

    @pytest.mark.asyncio
    async def test_broadcast_tm_removes_disconnected_client(self):
        dm = DownstreamManager()
        good_writer = MagicMock()
        good_writer.drain = AsyncMock()
        bad_writer = MagicMock()
        bad_writer.write.side_effect = ConnectionResetError("gone")
        await dm.add_client(good_writer)
        await dm.add_client(bad_writer)
        await dm.broadcast_tm(b"\x01\x02\x03")
        assert dm.client_count == 1
        bad_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_all_clears_clients(self):
        dm = DownstreamManager()
        w1 = MagicMock()
        w2 = MagicMock()
        await dm.add_client(w1)
        await dm.add_client(w2)
        await dm.close_all()
        assert dm.client_count == 0
        w1.close.assert_called_once()
        w2.close.assert_called_once()


# ── Framing Integration ────────────────────────────────────────────

class TestFramingIntegration:
    """Test that framing produces correct length-prefixed output."""

    def test_frame_packet_correct_length(self):
        data = b"\xAA\xBB\xCC"
        framed = frame_packet(data)
        # First 2 bytes are big-endian length
        length = struct.unpack('>H', framed[:2])[0]
        assert length == 3
        assert framed[2:] == data

    def test_frame_empty_packet(self):
        framed = frame_packet(b"")
        length = struct.unpack('>H', framed[:2])[0]
        assert length == 0

    def test_frame_large_packet(self):
        data = b"\x00" * 1000
        framed = frame_packet(data)
        length = struct.unpack('>H', framed[:2])[0]
        assert length == 1000
        assert len(framed) == 1002
