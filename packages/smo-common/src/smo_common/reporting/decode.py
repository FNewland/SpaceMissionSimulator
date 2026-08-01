"""The one correct decoder set for archived TM packets.

Reuses the wire-format authorities that already live in
``smo_common.protocol.pus_services`` (S1 verification, S3.25 HK envelope,
S5 events — note the S5 body is ``event_id(u16) severity(u8) cuc(u32)
aux_len(u8) aux``; the historical ``tools/tm_report.py`` parse treated
``data[2:]`` as text and prefixed every description with the severity/CUC
bytes) and adds the two report structures the MCS documents in
``smo_mcs.pus_reports`` (S12 transitions, S15.14 store status) so the report
library does not depend on the ground segment package.
"""
from __future__ import annotations

import struct
from typing import Any, Optional

from ..protocol.ecss_packet import DecommutatedPacket, decommutate_packet
from ..protocol.pus_services import EventData, VerificationData

__all__ = [
    "decode_packet", "decode_hk_body", "decode_event", "decode_verification",
    "decode_s12_transition", "decode_s15_store_status",
    "S12_EVENT_MASK", "S12_CLEAR_EVENT_MASK", "s12_event_to_param_id",
]

# Synthetic S5 events the simulator emits for every S12 transition
# (mirrors smo_mcs.pus_reports — see that module's docstring).
S12_EVENT_MASK = 0x9000
S12_CLEAR_EVENT_MASK = 0xA000


def s12_event_to_param_id(event_id: int) -> Optional[int]:
    top = event_id & 0xF000
    if top in (S12_EVENT_MASK, S12_CLEAR_EVENT_MASK):
        return event_id & 0x0FFF
    return None


def decode_packet(raw: bytes) -> Optional[DecommutatedPacket]:
    """Decommutate one raw archive packet; ``None`` when undecodable."""
    try:
        return decommutate_packet(raw)
    except (ValueError, struct.error):
        return None


def decode_hk_body(sid_body: bytes, hk_struct: dict) -> dict[int, float]:
    """Decode an S3.25 data field (after the 2-byte SID) into {pid: value}.

    ``hk_struct`` is a ``Catalogs.hk_structs`` entry:
    ``{"parameters": [(pid, pack_format, scale), ...]}``. Best-effort: a
    truncated packet yields the parameters that fit.
    """
    out: dict[int, float] = {}
    off = 0
    for pid, fmt, scale in hk_struct.get("parameters", []):
        try:
            size = struct.calcsize(">" + fmt)
        except struct.error:
            continue
        if off + size > len(sid_body):
            break
        try:
            (raw,) = struct.unpack(">" + fmt, sid_body[off:off + size])
        except struct.error:
            break
        off += size
        out[pid] = float(raw) / (scale or 1.0)
    return out


def decode_event(data_field: bytes) -> dict[str, Any]:
    """Decode an S5 event body correctly (aux text without header bytes)."""
    ev = EventData.parse(data_field)
    return {
        "event_id": ev.event_id,
        "severity": ev.severity,
        "cuc": ev.cuc_time,
        "aux_text": ev.aux_text,
        "s12_param_id": s12_event_to_param_id(ev.event_id),
    }


def decode_verification(data_field: bytes, subtype: int) -> dict[str, Any]:
    """Decode an S1.x verification report body."""
    v = VerificationData.parse(data_field, subtype)
    return {
        "subtype": subtype,
        "request_id": v.request_id,
        "tc_apid": v.tc_apid,
        "tc_seq": v.tc_seq,
        "error_code": v.error_code,
        "reason": v.reason,
    }


def decode_s12_transition(data_field: bytes, subtype: int) -> Optional[dict]:
    """Decode TM(12,9)/(12,10) — 18-byte form, 6-byte historical prefix ok."""
    if len(data_field) < 6:
        return None
    param_id, value = struct.unpack(">Hf", data_field[:6])
    out: dict[str, Any] = {
        "param_id": param_id,
        "value": value,
        "entered": subtype == 9,
        "transition": "out_of_limits" if subtype == 9 else "back_to_nominal",
    }
    if len(data_field) >= 18:
        mon_id, check_type, severity = struct.unpack(">HBB", data_field[6:10])
        low, high = struct.unpack(">ff", data_field[10:18])
        out.update({"mon_id": mon_id, "check_type": check_type,
                    "severity": severity, "low_limit": low, "high_limit": high})
    return out


def decode_s15_store_status(data_field: bytes) -> list[dict]:
    """Decode TM(15,14) — variable stride (12 + name_len) per record."""
    if len(data_field) < 1:
        return []
    n_stores = data_field[0]
    out = []
    off = 1
    for _ in range(n_stores):
        if off + 12 > len(data_field):
            break
        (sid, count, capacity, enabled, fill_x10,
         flags, wrap, name_len) = struct.unpack(">BHHBHBHB", data_field[off:off + 12])
        off += 12
        raw_name = data_field[off:off + name_len]
        if len(raw_name) < name_len:
            break
        off += name_len
        out.append({
            "id": sid, "count": count, "capacity": capacity,
            "enabled": bool(enabled), "fill_pct": round(fill_x10 / 10.0, 1),
            "overflow": bool(flags & 0x01), "circular": bool(flags & 0x02),
            "wrap_count": wrap, "name": raw_name.decode("ascii", errors="replace"),
        })
    return out
