"""Phase E — reverse decode: TC data field bytes -> engineering values.

Used by the MCS builder's paste-to-decode path (mirrored in JS), by tests,
and by the command-reference generator's worked examples. ``decode`` is the
exact inverse of ``encode`` for every fixed-width command; round-trip
equality is asserted over the whole catalogue by
tests/test_common/test_arg_schema.py.
"""
from __future__ import annotations

import struct
from typing import Any

from .catalog import Catalog, STRUCT_FMT, VARIABLE_FIELD_TYPES, default_catalog, field_width
from .encode import raw_to_eng, resolve_command

__all__ = ["DecodeError", "decode", "decode_args"]


class DecodeError(ValueError):
    """Bytes do not parse against the catalogue schema."""


def decode_args(cmd: dict, data: bytes) -> dict[str, Any]:
    """Decode ARGUMENT octets (no func_id prefix) to engineering values."""
    out: dict[str, Any] = {}
    pos = 0
    fields = cmd.get("fields") or []
    for i, field in enumerate(fields):
        ftype = str(field.get("type", "uint8")).lower()
        name = field.get("name", f"field{i}")
        if ftype in VARIABLE_FIELD_TYPES:
            if i != len(fields) - 1:
                raise DecodeError(
                    f"{cmd.get('name')}: variable field {name} is not last")
            out[name] = data[pos:].hex()
            pos = len(data)
            continue
        width = field_width(ftype)
        if width is None or pos + width > len(data):
            raise DecodeError(
                f"{cmd.get('name')}: {len(data)} byte(s) is too short for "
                f"field {name} at offset {pos} (+{width})")
        endian = str(field.get("endian", "big")).lower()
        prefix = ">" if endian in ("big", "be", "") else "<"
        raw = struct.unpack(prefix + STRUCT_FMT[ftype], data[pos:pos + width])[0]
        out[name] = raw_to_eng(field, raw)
        pos += width
    if pos != len(data):
        raise DecodeError(
            f"{cmd.get('name')}: {len(data) - pos} trailing byte(s) after "
            f"the declared fields")
    return out


def decode(data: bytes | str, *, service: int = 8, subtype: int = 1,
           catalog: Catalog | None = None,
           name: str | None = None) -> tuple[str, dict[str, Any]]:
    """Decode a TC data field to ``(command_name, {arg: engineering_value})``.

    For S8.1 the command is identified from octet 0 (the func_id) exactly as
    the spacecraft identifies it; for other services pass ``name`` to select
    the catalogue entry (several share a service.subtype).
    """
    if isinstance(data, str):
        data = bytes.fromhex(data.replace(" ", ""))
    cat = catalog or default_catalog()
    if name is not None:
        cmd = resolve_command(name, cat)
        body = data
        if (int(cmd["service"]), int(cmd["subtype"])) == (8, 1) \
                and cmd.get("func_id") is not None:
            if not data or data[0] != int(cmd["func_id"]) & 0xFF:
                raise DecodeError(
                    f"octet 0 is not {cmd['name']}'s func_id "
                    f"0x{int(cmd['func_id']):02X}")
            body = data[1:]
        return cmd["name"], decode_args(cmd, body)
    if (service, subtype) == (8, 1):
        if not data:
            raise DecodeError("empty S8.1 data field (no func_id octet)")
        cmd = cat.by_func(8, 1, data[0])
        if cmd is None:
            raise DecodeError(f"func_id 0x{data[0]:02X} is not in the catalogue")
        return cmd["name"], decode_args(cmd, data[1:])
    cmd = cat.by_func(service, subtype, None)
    if cmd is None:
        raise DecodeError(f"S{service}.{subtype} is not in the catalogue")
    return cmd["name"], decode_args(cmd, data)
