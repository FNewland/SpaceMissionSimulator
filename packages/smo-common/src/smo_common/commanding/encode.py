"""Phase E — the ONE encoder every ground-side consumer uses.

``encode("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0})`` returns the exact
TC data field, ``bytes.fromhex("2464d7")`` — func_id octet included for S8.1.
The MCS builder's JavaScript encoder mirrors this function and the
cross-encoder agreement test (tests/test_content/test_command_encoders_agree.py)
asserts they stay byte-identical.

Engineering-unit conversion, range checking and enum/bitfield validation all
happen here, from the catalogue's per-field schema (brief 04 §Argument schema
design). Out-of-range values raise ``EncodeError`` unless ``validate=False``
(the deliberate "send anyway — fault-injection training" path).
"""
from __future__ import annotations

import math
import struct
from typing import Any

from .catalog import Catalog, STRUCT_FMT, VARIABLE_FIELD_TYPES, default_catalog, field_width

__all__ = ["EncodeError", "encode", "encode_args", "eng_to_raw", "raw_to_eng",
           "field_default", "resolve_command"]


class EncodeError(ValueError):
    """A value cannot be encoded against the catalogue schema."""


def _cal(field: dict) -> dict:
    cal = field.get("calibration") or {}
    if not isinstance(cal, dict):
        cal = dict(cal)
    return cal


def eng_to_raw(field: dict, eng: Any) -> int | float:
    """Engineering value -> raw wire value for one field."""
    ftype = str(field.get("type", "uint8")).lower()
    cal = _cal(field)
    ctype = str(cal.get("type", "none")).lower()
    if ftype.startswith("float"):
        return float(eng)
    if ctype == "linear":
        scale = float(cal.get("scale", 1.0))
        offset = float(cal.get("offset", 0.0))
        if scale == 0:
            raise EncodeError(f"{field.get('name')}: linear calibration scale 0")
        return int(round((float(eng) - offset) / scale))
    if ctype == "polynomial":
        coeffs = [float(c) for c in cal.get("coefficients", [])]
        if len(coeffs) > 2:
            raise EncodeError(
                f"{field.get('name')}: polynomial degree > 1 not reversible")
        c0 = coeffs[0] if coeffs else 0.0
        c1 = coeffs[1] if len(coeffs) > 1 else 1.0
        if c1 == 0:
            raise EncodeError(f"{field.get('name')}: polynomial c1 == 0")
        return int(round((float(eng) - c0) / c1))
    # none / enum / bitfield: raw integer is the engineering value
    return int(round(float(eng)))


def raw_to_eng(field: dict, raw: int | float) -> float | int:
    """Raw wire value -> engineering value for one field."""
    ftype = str(field.get("type", "uint8")).lower()
    cal = _cal(field)
    ctype = str(cal.get("type", "none")).lower()
    if ftype.startswith("float"):
        return float(raw)
    if ctype == "linear":
        return float(raw) * float(cal.get("scale", 1.0)) + float(cal.get("offset", 0.0))
    if ctype == "polynomial":
        coeffs = [float(c) for c in cal.get("coefficients", [])]
        return float(sum(c * float(raw) ** i for i, c in enumerate(coeffs)))
    return int(raw)


def _int_bounds(ftype: str) -> tuple[int, int]:
    width = field_width(ftype) or 1
    bits = width * 8
    if ftype.startswith("int"):
        return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    return 0, (1 << bits) - 1


def _validate(field: dict, eng: Any, raw: Any) -> None:
    name = field.get("name", "?")
    ftype = str(field.get("type", "uint8")).lower()
    cal = _cal(field)
    ctype = str(cal.get("type", "none")).lower()
    rng = field.get("range")
    if rng is not None:
        lo, hi = float(rng["min"]), float(rng["max"])
        if not (lo <= float(eng) <= hi):
            unit = field.get("unit", "")
            raise EncodeError(
                f"{name} = {eng}{(' ' + unit) if unit else ''} is outside "
                f"[{lo}, {hi}]{(' ' + unit) if unit else ''}")
    if ctype == "enum":
        valid = {int(e["value"]) for e in field.get("enum") or []}
        if valid and int(raw) not in valid:
            raise EncodeError(
                f"{name} = {raw} is not one of the enumerated values "
                f"{sorted(valid)}")
    if ctype == "bitfield":
        bits = field.get("bits") or []
        allowed_mask = 0
        for b in bits:
            if not b.get("reserved", False):
                allowed_mask |= 1 << int(b["bit"])
        if bits and int(raw) & ~allowed_mask:
            raise EncodeError(
                f"{name} = 0x{int(raw):02X} sets reserved bits "
                f"(allowed mask 0x{allowed_mask:02X})")
    if not ftype.startswith("float"):
        lo, hi = _int_bounds(ftype)
        if not (lo <= int(raw) <= hi):
            raise EncodeError(
                f"{name}: raw value {raw} does not fit {ftype}")
        rr = field.get("raw_range")
        if rr is not None and not (float(rr["min"]) <= int(raw) <= float(rr["max"])):
            raise EncodeError(
                f"{name}: raw {raw} outside declared raw_range "
                f"[{rr['min']}, {rr['max']}]")


def field_default(field: dict) -> Any:
    if field.get("default") is not None:
        return field["default"]
    rng = field.get("range")
    if rng is not None:
        return rng["min"]
    enum = field.get("enum") or []
    if enum:
        return int(enum[0]["value"])
    return 0


def _pack_field(field: dict, raw: Any) -> bytes:
    ftype = str(field.get("type", "uint8")).lower()
    if ftype in VARIABLE_FIELD_TYPES:
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return bytes.fromhex(str(raw).replace(" ", ""))
    fmt = STRUCT_FMT.get(ftype)
    if fmt is None:
        raise EncodeError(f"{field.get('name')}: unknown field type {ftype!r}")
    endian = str(field.get("endian", "big")).lower()
    prefix = ">" if endian in ("big", "be", "") else "<"
    if fmt in ("f", "d"):
        return struct.pack(prefix + fmt, float(raw))
    return struct.pack(prefix + fmt, int(raw))


def encode_args(cmd: dict, args: dict[str, Any] | None = None, *,
                validate: bool = True) -> bytes:
    """Encode the ARGUMENT octets of a command (no func_id prefix)."""
    args = dict(args or {})
    out = bytearray()
    for field in cmd.get("fields") or []:
        name = field.get("name")
        if name in args:
            eng = args.pop(name)
        else:
            eng = field_default(field)
        raw = eng_to_raw(field, eng)
        if validate:
            _validate(field, eng, raw)
        out += _pack_field(field, raw)
    if args:
        raise EncodeError(
            f"{cmd.get('name')}: unknown argument(s) {sorted(args)} "
            f"(fields: {[f.get('name') for f in cmd.get('fields') or []]})")
    return bytes(out)


def resolve_command(name_or_key, catalog: Catalog | None = None) -> dict:
    cat = catalog or default_catalog()
    if isinstance(name_or_key, str):
        return cat.get(name_or_key)
    service, subtype, func_id = name_or_key
    cmd = cat.by_func(service, subtype, func_id)
    if cmd is None:
        raise KeyError(f"no catalogued command for S{service}.{subtype} "
                       f"func {func_id}")
    return cmd


def encode(name_or_key, args: dict[str, Any] | None = None, *,
           catalog: Catalog | None = None, validate: bool = True) -> bytes:
    """Encode a command's complete TC DATA FIELD from engineering values.

    ``name_or_key`` is the catalogue name (or alias), or a
    ``(service, subtype, func_id)`` tuple. For S8.1 commands the returned
    bytes start with the func_id octet, exactly as the dispatcher reads them.
    """
    cmd = resolve_command(name_or_key, catalog)
    body = encode_args(cmd, args, validate=validate)
    if (int(cmd["service"]), int(cmd["subtype"])) == (8, 1) and cmd.get("func_id") is not None:
        return bytes([int(cmd["func_id"]) & 0xFF]) + body
    return body
