"""Integration tests for the full RF processing chain.

Tests the complete path: ECSS packet → CCSDS framing → convolutional
encoding → channel → Viterbi decode → frame parse → ECSS packet.
"""

import struct
import pytest
from smo_common.protocol.ecss_packet import build_tm_packet, build_tc_packet
from smo_rfsim.ccsds.asm import attach_asm
from smo_rfsim.ccsds.tm_frame import TMFrameBuilder, TMFrameParser
from smo_rfsim.ccsds.frame_sync import FrameSynchronizer
from smo_rfsim.ccsds.convolutional import encode as conv_encode
from smo_rfsim.ccsds.viterbi import decode as viterbi_decode
from smo_rfsim.ccsds.reed_solomon import encode as rs_encode, decode as rs_decode
from smo_rfsim.ccsds.tc_cltu import encode_cltu, decode_cltu
from smo_rfsim.channel.model import ChannelModel
from smo_rfsim.config import RFSimConfig
from smo_rfsim.mode import RFSimMode


FRAME_LEN = 200


def _frame_to_wire(frame) -> bytes:
    raw = frame.header.pack() + frame.data
    if frame.fecf is not None:
        raw += struct.pack('>H', frame.fecf)
    return raw


class TestConvolutionalRoundtrip:
    def test_encode_decode_roundtrip(self):
        """Data survives convolutional encode → Viterbi decode."""
        data = b'\x42\xAA\xBB\xCC' * 10
        encoded = conv_encode(data)
        decoded = viterbi_decode(encoded, original_length=len(data))
        assert decoded == data

    def test_with_bit_errors(self):
        """Viterbi corrects scattered bit errors."""
        data = b'\x55\x66\x77\x88' * 10
        encoded = bytearray(conv_encode(data))
        # Introduce a few bit errors
        encoded[5] ^= 0x01
        encoded[15] ^= 0x08
        encoded[25] ^= 0x40
        decoded = viterbi_decode(bytes(encoded), original_length=len(data))
        assert decoded == data


class TestReedSolomonRoundtrip:
    def test_encode_corrupt_decode(self):
        """RS decoder corrects symbol errors."""
        data = b'\xDE\xAD\xBE\xEF' * 25  # 100 bytes
        codeword = bytearray(rs_encode(data))
        # Corrupt 3 symbols
        codeword[10] ^= 0xFF
        codeword[50] ^= 0xAA
        codeword[90] ^= 0x55
        decoded = rs_decode(bytes(codeword))
        assert decoded is not None
        assert decoded == data


class TestFullTMChainWithCoding:
    def test_packet_through_coded_framing(self):
        """ECSS packet → frame → RS encode → conv encode → decode chain."""
        original = build_tm_packet(1, 3, 25, b'\x42' * 30)

        # Build frame
        builder = TMFrameBuilder(scid=1, frame_length=FRAME_LEN, fecf_present=True)
        builder.add_packet(original, vcid=0)
        frames = builder.flush(vcid=0)
        assert len(frames) >= 1

        for frame in frames:
            # Serialize frame
            frame_bytes = _frame_to_wire(frame)

            # RS encode
            rs_coded = rs_encode(frame_bytes)

            # Convolutional encode
            conv_coded = conv_encode(rs_coded)

            # Viterbi decode
            viterbi_out = viterbi_decode(conv_coded, original_length=len(rs_coded))
            assert viterbi_out == rs_coded

            # RS decode
            rs_out = rs_decode(viterbi_out)
            assert rs_out is not None
            assert rs_out == frame_bytes

            # Parse frame
            parser = TMFrameParser(frame_length=FRAME_LEN, fecf_present=True)
            parsed = parser.parse_frame(rs_out)
            assert parsed is not None
            packets = parser.extract_packets(parsed)
            assert len(packets) == 1
            assert packets[0] == original


class TestFullTCChain:
    def test_tc_through_cltu_with_errors(self):
        """TC packet → CLTU → single bit error → decode → verify."""
        tc = build_tc_packet(1, 8, 1, b'\x01\x02\x03\x04\x05')
        cltu = bytearray(encode_cltu(tc))

        # Single bit error in a code block
        cltu[6] ^= 0x02

        decoded = decode_cltu(bytes(cltu), correct_errors=True)
        assert decoded is not None
        assert decoded[:len(tc)] == tc


class TestConfigModes:
    def test_packet_mode_defaults(self):
        cfg = RFSimConfig()
        assert cfg.mode == RFSimMode.PACKET

    def test_frame_mode_config(self):
        cfg = RFSimConfig()
        cfg.mode = RFSimMode.FRAME
        assert cfg.mode == RFSimMode.FRAME
        assert cfg.ccsds.tm_frame_length == 1115
        assert cfg.ccsds.rs_enabled
        assert cfg.ccsds.convolutional_enabled

    def test_rf_mode_config(self):
        cfg = RFSimConfig()
        cfg.mode = RFSimMode.RF
        assert cfg.mode == RFSimMode.RF
