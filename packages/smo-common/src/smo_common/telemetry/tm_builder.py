"""SMO Common — TM Packet Builder.

Assembles ECSS PUS TM packets from shared parameter stores.
Supports S1 (Verification), S3 (Housekeeping), S5 (Events),
S20 (Parameter Value Reports).

Config-driven: HK structure definitions are loaded from YAML config
rather than being hardcoded.
"""
import struct
import logging
from datetime import datetime, timezone
from typing import Optional

from ..protocol.ecss_packet import crc16_ccitt, TIME_EPOCH

logger = logging.getLogger(__name__)


class TMBuilder:
    """Assembles ECSS PUS TM packets. All packets are big-endian per CCSDS/ECSS."""

    def __init__(self, apid: int, time_source=None):
        self._apid = apid
        self._seq_count = 0
        self._time_source = time_source  # callable returning CUC seconds

    @property
    def apid(self) -> int:
        """Current APID used for all emitted TM packets."""
        return self._apid

    def set_apid(self, apid: int) -> None:
        """Switch the source APID used for subsequent TM packets.

        Used by the engine to distinguish bootloader-sourced TM (beacon/SID 11)
        from application-software-sourced TM. Sequence counter is reset on switch
        so each APID maintains its own counter per ECSS/CCSDS convention.
        """
        new_apid = int(apid) & 0x7FF
        if new_apid != self._apid:
            self._apid = new_apid
            self._seq_count = 0

    def build_hk_packet(self, sid: int, params: dict,
                        hk_structure: list[tuple] | None = None) -> Optional[bytes]:
        """Build a Service 3 Housekeeping TM packet.

        Args:
            sid: Structure ID.
            params: Shared parameter store {param_id: value}.
            hk_structure: List of (param_id, pack_format, scale) tuples.
        """
        if hk_structure is None:
            return None
        cuc = self._get_cuc(params)
        data = struct.pack('>H', sid)
        for param_id, fmt, scale in hk_structure:
            value = params.get(param_id, 0)
            try:
                packed_val = int(round(float(value) * scale))
                data += struct.pack('>' + fmt, packed_val)
            except (struct.error, OverflowError, TypeError):
                data += struct.pack('>' + fmt, 0)
        return self._pack_tm(service=3, subtype=25, data=data, cuc=cuc)

    def build_event_packet(self, event_id: int, severity: int,
                           aux_text: str, params: dict) -> Optional[bytes]:
        """Build a Service 5 Event Report TM packet."""
        cuc = self._get_cuc(params)
        aux = aux_text.encode('ascii', errors='ignore')[:32]
        data = struct.pack('>HB', event_id, severity)
        data += struct.pack('>I', cuc)
        data += struct.pack('>B', len(aux)) + aux
        return self._pack_tm(service=5, subtype=severity, data=data, cuc=cuc)

    def build_verification_acceptance(self, tc_apid: int, tc_seq: int) -> bytes:
        """Build Service 1 subtype 1 — Acceptance Success."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>I', request_id)
        return self._pack_tm(service=1, subtype=1, data=data)

    def build_verification_completion(self, tc_apid: int, tc_seq: int) -> bytes:
        """Build Service 1 subtype 7 — Execution Complete."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>I', request_id)
        return self._pack_tm(service=1, subtype=7, data=data)

    def build_verification_failure(self, tc_apid: int, tc_seq: int,
                                   error_code: int) -> bytes:
        """Build Service 1 subtype 2 — Acceptance Failure."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>IH', request_id, error_code)
        return self._pack_tm(service=1, subtype=2, data=data)

    def build_execution_failure(self, tc_apid: int, tc_seq: int,
                                error_code: int) -> bytes:
        """Build Service 1 subtype 8 — Execution Failure."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>IH', request_id, error_code)
        return self._pack_tm(service=1, subtype=8, data=data)

    def build_time_report(self, cuc: int) -> bytes:
        """Build Service 9 subtype 2 — Time Report."""
        data = struct.pack('>I', cuc)
        return self._pack_tm(service=9, subtype=2, data=data, cuc=cuc)

    def build_connection_test_report(self) -> bytes:
        """Build Service 17 subtype 2 — Connection Test Report."""
        cuc = self._get_cuc({})
        return self._pack_tm(service=17, subtype=2, data=b'', cuc=cuc)

    def build_param_value_report(self, param_id: int, value: float) -> bytes:
        """Build Service 20 Parameter Value Report (subtype 2)."""
        cuc = self._get_cuc({})
        data = struct.pack('>HBf', param_id, 1, value)
        return self._pack_tm(service=20, subtype=2, data=data, cuc=cuc)

    def _pack_tm(self, service: int, subtype: int, data: bytes,
                 cuc: int = 0) -> bytes:
        sec_hdr = bytes([0x10, service, subtype]) + struct.pack('>I', cuc)
        payload = sec_hdr + data
        data_length_field = len(payload) + 1  # CCSDS: (octets after primary including CRC) - 1
        self._seq_count = (self._seq_count + 1) & 0x3FFF
        packet_id = (0 << 13) | (0 << 12) | (1 << 11) | (self._apid & 0x7FF)
        seq_ctrl = (0b11 << 14) | self._seq_count
        primary = struct.pack('>HHH', packet_id, seq_ctrl, data_length_field)
        packet = primary + payload
        crc = crc16_ccitt(packet)
        return packet + struct.pack('>H', crc)

    def _get_cuc(self, params: dict) -> int:
        if self._time_source is not None:
            return self._time_source()
        # Fallback: current wall-clock
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return int((now - TIME_EPOCH).total_seconds())
