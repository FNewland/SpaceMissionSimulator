"""Decoders for the PUS report telemetry the MCS displays.

Every function here turns one TM data field into plain dicts. They are pure and
side-effect free so they can be unit-tested against bytes built straight from
the wire spec, and so ``MCSServer._process_tm`` stays a dispatcher rather than a
bag of ``struct.unpack`` calls.

All wire formats are BIG-ENDIAN and mirror
``smo_simulator.service_dispatch`` exactly:

===============  ==========================================================
TM(12,9)/(12,10) param_id(u16) value(f32) mon_id(u16) check_type(u8)
                 severity(u8) low(f32) high(f32)                  [18 B]
                 The 6-byte prefix (param_id + value) is the historical form
                 and is still accepted — older builds emit only that.
TM(12,13)        count(u16) then per record:
                 mon_id(u16) param_id(u16) check_type(u8) enabled(u8)
                 low(f32) high(f32)                               [14 B]
TM(19,128)       count(u16) then per record:
                 ea_id(u16) trigger_kind(u8) trigger_id(u16)
                 action_func_id(u8) enabled(u8)                    [7 B]
TM(11,18)        count(u16) enabled(u8) next_id(u16) then per record:
                 id(u16) exec_time(u32 CUC) apid(u16) service(u8)
                 subtype(u8) data_len(u8) n(u8) data[n]    [VARIABLE 12+n]
TM(15,14)        n_stores(u8) then per record:
                 id(u8) count(u16) capacity(u16) enabled(u8)
                 fill_pct_x10(u16) flags(u8) wrap(u16) name_len(u8)
                 name[name_len]                           [VARIABLE 12+len]
TM(6,6)          address(u32) length(u16) data[length]
TM(6,10)         address(u32) length(u16) crc(u16)                 [8 B]
===============  ==========================================================

The two variable-stride reports (11,18 and 15,14) must be walked record by
record: deriving a stride by dividing the payload length by the record count —
which is what the MCS used to do for 15,14 — desynchronises the parse the
moment two records differ in length.
"""
from __future__ import annotations

import struct
from typing import Any, Optional

__all__ = [
    "TRIGGER_KIND_EVENT",
    "TRIGGER_KIND_PARAM",
    "S12_EVENT_MASK",
    "S12_CLEAR_EVENT_MASK",
    "s12_event_to_param_id",
    "parse_s12_transition",
    "parse_s12_definitions",
    "parse_s19_definitions",
    "parse_s11_schedule",
    "parse_s15_store_status",
    "parse_s6_dump",
    "parse_s6_check",
]

# S19 trigger namespaces (mirror smo_simulator.monitoring_loader).
TRIGGER_KIND_EVENT = 0
TRIGGER_KIND_PARAM = 1

# The simulator also synthesises an S5 event for every S12 transition so the
# S19 dispatcher and the on-board alarm store see it. Those ids are NOT in
# event_catalog.yaml — they are built as a mask over the id that moved:
#   0x9000 | (param_id & 0x0FFF)   limit entered
#   0xA000 | (mon_id   & 0x0FFF)   returned to nominal
# The FDIR journal uses this to file such an event against the parameter /
# monitor it came from instead of showing it as an unknown event id.
S12_EVENT_MASK = 0x9000
S12_CLEAR_EVENT_MASK = 0xA000


def s12_event_to_param_id(event_id: int) -> Optional[int]:
    """Return the param/monitor id encoded in a synthetic S12 event id.

    ``None`` for any event id that is not one of the two S12 masks, so the
    caller can tell "this is a catalogued S5 event" from "this is an S12
    transition wearing an event id".
    """
    top = event_id & 0xF000
    if top in (S12_EVENT_MASK, S12_CLEAR_EVENT_MASK):
        return event_id & 0x0FFF
    return None


def parse_s12_transition(data: bytes, subtype: int) -> Optional[dict]:
    """Decode TM(12,9) / TM(12,10).

    Returns ``None`` when the payload is too short to carry even the historical
    6-byte prefix. Fields beyond that prefix are omitted rather than faked when
    an older build sends the short form.
    """
    if len(data) < 6:
        return None
    param_id, value = struct.unpack(">Hf", data[:6])
    out: dict[str, Any] = {
        "param_id": param_id,
        "value": value,
        "transition": "out_of_limits" if subtype == 9 else "back_to_nominal",
        "entered": subtype == 9,
    }
    if len(data) >= 18:
        mon_id, check_type, severity = struct.unpack(">HBB", data[6:10])
        low, high = struct.unpack(">ff", data[10:18])
        out.update({
            "mon_id": mon_id,
            "check_type": check_type,
            "severity": severity,
            "low_limit": low,
            "high_limit": high,
        })
    return out


def parse_s12_definitions(data: bytes) -> list[dict]:
    """Decode TM(12,13) — the full on-board monitoring definition table."""
    if len(data) < 2:
        return []
    count = struct.unpack(">H", data[:2])[0]
    out = []
    off = 2
    for _ in range(count):
        if off + 14 > len(data):
            break
        mon_id, param_id, check_type, enabled = struct.unpack(
            ">HHBB", data[off:off + 6])
        low, high = struct.unpack(">ff", data[off + 6:off + 14])
        out.append({
            "mon_id": mon_id,
            "param_id": param_id,
            "check_type": check_type,
            "enabled": bool(enabled),
            "low_limit": low,
            "high_limit": high,
        })
        off += 14
    return out


def parse_s19_definitions(data: bytes) -> list[dict]:
    """Decode TM(19,128) — the full event-action rule table."""
    if len(data) < 2:
        return []
    count = struct.unpack(">H", data[:2])[0]
    out = []
    off = 2
    for _ in range(count):
        if off + 7 > len(data):
            break
        ea_id, kind, trigger_id, func, enabled = struct.unpack(
            ">HBHBB", data[off:off + 7])
        out.append({
            "ea_id": ea_id,
            "trigger_kind": kind,
            "trigger_kind_name": "param" if kind == TRIGGER_KIND_PARAM else "event",
            "trigger_id": trigger_id,
            "action_func_id": func,
            "enabled": bool(enabled),
        })
        off += 7
    return out


def parse_s11_schedule(data: bytes) -> Optional[dict]:
    """Decode TM(11,18) — the detailed on-board schedule report.

    Records are variable length (12 + n data bytes) so the walk is driven by
    each record's own ``n`` field. Entries come back sorted by execution time,
    which is the order an operator reads a schedule in.
    """
    if len(data) < 5:
        return None
    count, enabled, next_id = struct.unpack(">HBH", data[:5])
    entries = []
    off = 5
    for _ in range(count):
        if off + 12 > len(data):
            break
        (cmd_id, exec_time, apid, service, subtype,
         data_len, n) = struct.unpack(">HIHBBBB", data[off:off + 12])
        off += 12
        payload = data[off:off + n]
        if len(payload) < n:
            break
        off += n
        entries.append({
            "id": cmd_id,
            "exec_time_cuc": exec_time,
            "apid": apid,
            "service": service,
            "subtype": subtype,
            "data_len": data_len,
            "data_hex": payload.hex().upper(),
            "truncated": data_len > n,
        })
    entries.sort(key=lambda e: (e["exec_time_cuc"], e["id"]))
    return {
        "count": count,
        "enabled": bool(enabled),
        "next_id": next_id,
        "entries": entries,
    }


def parse_s15_store_status(data: bytes) -> list[dict]:
    """Decode TM(15,14) — on-board TM store status.

    Variable stride (12 + name_len); the previous fixed-stride reader threw
    away name, fill %, overflow, circular and wrap count (defect #73).
    """
    if len(data) < 1:
        return []
    n_stores = data[0]
    out = []
    off = 1
    for _ in range(n_stores):
        if off + 12 > len(data):
            break
        (sid, count, capacity, enabled, fill_x10,
         flags, wrap, name_len) = struct.unpack(">BHHBHBHB", data[off:off + 12])
        off += 12
        raw_name = data[off:off + name_len]
        if len(raw_name) < name_len:
            break
        off += name_len
        entry = {
            "id": sid,
            "count": count,
            "capacity": capacity,
            "enabled": bool(enabled),
            "fill_pct": round(fill_x10 / 10.0, 1),
            "overflow": bool(flags & 0x01),
            "circular": bool(flags & 0x02),
            "wrap_count": wrap,
            "name": raw_name.decode("ascii", errors="replace"),
        }
        out.append(entry)
    return out


def parse_s6_dump(data: bytes) -> Optional[dict]:
    """Decode TM(6,6) — memory dump report."""
    if len(data) < 6:
        return None
    address, length = struct.unpack(">IH", data[:6])
    payload = data[6:6 + length]
    return {
        "address": address,
        "length": length,
        "data_hex": payload.hex().upper(),
        "received": len(payload),
        "truncated": len(payload) < length,
    }


def parse_s6_check(data: bytes) -> Optional[dict]:
    """Decode TM(6,10) — memory check (CRC-16-CCITT) report."""
    if len(data) < 8:
        return None
    address, length, crc = struct.unpack(">IHH", data[:8])
    return {"address": address, "length": length, "crc": crc}
