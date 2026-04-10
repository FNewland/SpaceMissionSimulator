"""SMO MCS — TC Manager.

Command building, sending, and verification tracking.
Supports S3, S5, S6, S8, S9, S11, S12, S15, S17, S19, S20.
"""
import itertools
import struct
import logging
from typing import Any, Optional

from smo_common.protocol.ecss_packet import build_tc_packet

logger = logging.getLogger(__name__)


class TCManager:
    """Builds and tracks telecommands."""

    def __init__(self, apid: int = 1):
        self._apid = apid
        self._seq_counter = itertools.count(1)
        self._seq_count = 0  # last used sequence number (for external reads)
        self._pending_verifications: dict[int, dict] = {}

    def build_command(self, service: int, subtype: int,
                      data: bytes = b'') -> bytes:
        seq = next(self._seq_counter) & 0x3FFF
        self._seq_count = seq  # store for external reads
        return build_tc_packet(
            apid=self._apid, service=service,
            subtype=subtype, data=data,
            seq_count=seq,
        )

    # ─── S3 Housekeeping ─────────────────────────────────────────────

    def build_s3_hk_request(self, sid: int) -> bytes:
        return self.build_command(3, 27, struct.pack('>H', sid))

    def build_s3_hk_enable(self, sid: int) -> bytes:
        return self.build_command(3, 5, struct.pack('>H', sid))

    def build_s3_hk_disable(self, sid: int) -> bytes:
        return self.build_command(3, 6, struct.pack('>H', sid))

    def build_s3_hk_set_interval(self, sid: int, interval_s: float) -> bytes:
        return self.build_command(
            3, 31, struct.pack('>Hf', sid, interval_s)
        )

    # ─── S5 Event Reporting ──────────────────────────────────────────

    def build_s5_event_enable(self, event_type: int) -> bytes:
        return self.build_command(5, 5, bytes([event_type]))

    def build_s5_event_disable(self, event_type: int) -> bytes:
        return self.build_command(5, 6, bytes([event_type]))

    # ─── S6 Memory Management ────────────────────────────────────────

    def build_s6_mem_load(self, address: int, data: bytes) -> bytes:
        return self.build_command(
            6, 2, struct.pack('>I', address) + data
        )

    def build_s6_mem_dump(self, address: int, length: int) -> bytes:
        return self.build_command(
            6, 5, struct.pack('>IH', address, length)
        )

    def build_s6_mem_check(self, address: int, length: int) -> bytes:
        return self.build_command(
            6, 9, struct.pack('>IH', address, length)
        )

    # ─── S8 Function Management ──────────────────────────────────────

    def build_s8_command(self, func_id: int, params: bytes = b'') -> bytes:
        data = bytes([func_id]) + params
        return self.build_command(8, 1, data)

    # ─── S12 On-Board Monitoring ─────────────────────────────────────

    def build_s12_mon_enable(self) -> bytes:
        return self.build_command(12, 1, b'')

    def build_s12_mon_disable(self) -> bytes:
        return self.build_command(12, 2, b'')

    def build_s12_mon_add(self, param_id: int, check_type: int,
                          low_limit: float, high_limit: float) -> bytes:
        data = struct.pack('>HBff', param_id, check_type,
                          low_limit, high_limit)
        return self.build_command(12, 6, data)

    def build_s12_mon_delete(self, param_id: int) -> bytes:
        return self.build_command(12, 7, struct.pack('>H', param_id))

    # ─── S19 Event-Action ────────────────────────────────────────────

    def build_s19_ea_add(self, ea_id: int, event_type: int,
                         action_func_id: int) -> bytes:
        data = struct.pack('>HBB', ea_id, event_type, action_func_id)
        return self.build_command(19, 1, data)

    def build_s19_ea_delete(self, ea_id: int) -> bytes:
        return self.build_command(19, 2, struct.pack('>H', ea_id))

    def build_s19_ea_enable(self, ea_id: int) -> bytes:
        return self.build_command(19, 4, struct.pack('>H', ea_id))

    def build_s19_ea_disable(self, ea_id: int) -> bytes:
        return self.build_command(19, 5, struct.pack('>H', ea_id))

    # ─── S20 Parameter Management ────────────────────────────────────

    def build_s20_param_request(self, param_id: int) -> bytes:
        return self.build_command(20, 3, struct.pack('>H', param_id))

    def build_s20_param_set(self, param_id: int, value: float) -> bytes:
        return self.build_command(20, 1, struct.pack('>Hf', param_id, value))

    # ─── Verification tracking ───────────────────────────────────────

    def track_verification(self, seq: int, command_name: str) -> None:
        self._pending_verifications[seq] = {
            "name": command_name, "accepted": False, "completed": False,
        }

    def process_verification(self, request_id: int,
                             subtype: int) -> Optional[str]:
        seq = request_id & 0x3FFF
        entry = self._pending_verifications.get(seq)
        if not entry:
            return None
        if subtype == 1:
            entry["accepted"] = True
            return f"{entry['name']}: accepted"
        elif subtype == 7:
            entry["completed"] = True
            return f"{entry['name']}: completed"
        elif subtype == 2:
            return f"{entry['name']}: REJECTED"
        return None
