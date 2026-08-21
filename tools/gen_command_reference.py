#!/usr/bin/env python3
"""gen_command_reference.py — generate the command reference from the catalogue.

Phase E WP-12 (brief 04). ``configs/eosat1/commands/tc_catalog.yaml`` is the
single source of wire truth; this writes
``configs/eosat1/manual/09_command_reference.md`` from it, so the manual
cannot drift from the spacecraft (the hand-maintained document listed ONE of
the fourteen payload commands and two of the seventeen TT&C commands, and
its parameter tables were 70 entries against the mission's 418).

Every command gets its func_id, byte count, criticality, readback parameter,
an argument table with type / offset / unit / raw<->engineering conversion /
range / default, enum and bit tables where declared, and a worked example
hex string computed by the SAME encoder the ground segment uses
(``smo_common.commanding.encode``).

Usage::

    python tools/gen_command_reference.py
    python tools/gen_command_reference.py --check    # CI diff gate
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "configs" / "eosat1"

for _pkg in ("smo-common",):
    _src = REPO / "packages" / _pkg / "src"
    if _src.exists() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

import yaml  # noqa: E402

from source_revision import source_revision  # noqa: E402

from smo_common.commanding import (EncodeError, encode, field_default,  # noqa: E402
                                   field_width, load_catalog)

SERVICE_NAMES = {
    2: "Device Access", 3: "Housekeeping", 5: "Event Reporting",
    6: "Memory Management", 8: "Function Management", 9: "Time Management",
    11: "Time-tagged Scheduling", 12: "On-board Monitoring",
    13: "Large Data Transfer", 15: "On-board Storage", 17: "Connection Test",
    18: "On-board Control Procedures", 19: "Event-Action",
    20: "Parameter Management",
}

#: S8.1 func_id ranges -> section heading (mirrors the dispatcher's routing).
FUNC_GROUPS = [
    (range(0, 16), "AOCS"),
    (range(16, 26), "EPS"),
    (range(26, 40), "Payload"),
    (range(40, 50), "TCS"),
    (range(50, 63), "OBDH"),
    (range(63, 80), "TT&C"),
    (range(80, 83), "OBDH"),
    (range(83, 84), "EPS"),
    (range(84, 92), "AOCS"),
    (range(92, 96), "Payload"),
    (range(100, 108), "EPS (legacy quick actions)"),
    (range(108, 119), "Propulsion"),
]


def _group_of(func_id: int) -> str:
    for r, name in FUNC_GROUPS:
        if func_id in r:
            return name
    return "Other"


def _esc(text) -> str:
    return str(text or "").replace("\n", " ").replace("|", r"\|").strip()


def _expected_bytes(cmd: dict):
    widths = [field_width(f.get("type", "uint8")) for f in cmd.get("fields") or []]
    if any(w is None for w in widths):
        return None
    prefix = 1 if (cmd["service"], cmd["subtype"]) == (8, 1) else 0
    return sum(widths) + prefix


def _conversion(field: dict) -> str:
    cal = field.get("calibration") or {}
    ctype = str(cal.get("type", "none")).lower()
    if ctype == "linear":
        scale = cal.get("scale", 1.0)
        offset = cal.get("offset", 0.0)
        expr = f"eng = raw x {scale}"
        if offset:
            expr += f" {'+' if offset > 0 else '-'} {abs(offset)}"
        raw_u = field.get("raw_unit")
        return expr + (f" ({raw_u}/LSB)" if raw_u else "")
    if ctype == "polynomial":
        coeffs = cal.get("coefficients") or []
        return "eng = " + " + ".join(f"{c}*raw^{i}" for i, c in enumerate(coeffs))
    if ctype == "enum":
        return "enumerated"
    if ctype == "bitfield":
        return "bitfield"
    return "raw = eng"


def _range_text(field: dict) -> str:
    rng = field.get("range")
    if not rng:
        return "—"
    return f"{rng['min']} … {rng['max']}"


def _example_hex(catalog, cmd: dict) -> str:
    """Worked example, encoded by the ground segment's own encoder."""
    args = {f["name"]: field_default(f) for f in cmd.get("fields") or []}
    if any(field_width(f.get("type", "uint8")) is None
           for f in cmd.get("fields") or []):
        return "_variable length — see the field table_"
    try:
        wire = encode(cmd["name"], args, catalog=catalog)
    except EncodeError:
        return "_no default example_"
    hexed = " ".join(f"{b:02X}" for b in wire)
    if args:
        shown = ", ".join(f"{k}={v}" for k, v in args.items())
        return f"`{hexed}`  ({shown})"
    return f"`{hexed}`"


def _command_block(catalog, cmd: dict) -> str:
    out: list[str] = []
    head = f"#### {cmd['name']}"
    out.append(head)
    out.append("")
    meta = [f"**S{cmd['service']}.{cmd['subtype']}**"]
    if cmd.get("func_id") is not None:
        meta.append(f"func_id **{cmd['func_id']}** (0x{int(cmd['func_id']):02X})")
    n = _expected_bytes(cmd)
    meta.append(f"{n} data byte(s)" if n is not None else "variable length")
    if cmd.get("criticality"):
        meta.append(str(cmd["criticality"]).upper())
    if cmd.get("position"):
        meta.append(f"position `{cmd['position']}`")
    rb = cmd.get("readback")
    if isinstance(rb, dict) and rb.get("param_id") is not None:
        rb_txt = rb.get("description") or ""
        meta.append(f"readback {rb_txt} (0x{int(rb['param_id']):04X})".replace("  ", " "))
    out.append(" · ".join(meta))
    out.append("")
    if cmd.get("description"):
        out.append(_esc(cmd["description"]))
        out.append("")
    if cmd.get("aliases"):
        out.append(f"*Aliases:* {', '.join(cmd['aliases'])}")
        out.append("")

    fields = cmd.get("fields") or []
    if fields:
        out.append("| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |")
        out.append("|---|---|---|---|---|---|---|")
        pos = 1 if (cmd["service"], cmd["subtype"]) == (8, 1) else 0
        for f in fields:
            offset = f.get("offset")
            if offset is None:
                offset = pos
            w = field_width(f.get("type", "uint8"))
            pos = (pos + w) if w is not None else pos
            unit = f.get("unit") or "—"
            if f.get("raw_unit"):
                unit = f"{unit} (raw {f['raw_unit']})"
            default = f.get("default")
            out.append(
                f"| `{_esc(f['name'])}` | {f.get('type', 'uint8')} | {offset} "
                f"| {_esc(unit)} | {_esc(_conversion(f))} | {_esc(_range_text(f))} "
                f"| {'—' if default is None else default} |")
        out.append("")
        for f in fields:
            if f.get("enum"):
                out.append(f"*`{f['name']}` values:*")
                out.append("")
                out.append("| Value | Label | Meaning |")
                out.append("|---|---|---|")
                for e in f["enum"]:
                    out.append(f"| {e['value']} | `{e['label']}` "
                               f"| {_esc(e.get('description', ''))} |")
                out.append("")
            if f.get("bits"):
                out.append(f"*`{f['name']}` bits:*")
                out.append("")
                out.append("| Bit | Name | Meaning |")
                out.append("|---|---|---|")
                for b in f["bits"]:
                    out.append(f"| {b['bit']} | `{b['name']}` "
                               f"| {'reserved — must be 0' if b.get('reserved') else _esc(b.get('description', ''))} |")
                out.append("")
        for f in fields:
            if f.get("description"):
                out.append(f"- `{f['name']}` — {_esc(f['description'])}")
        out.append("")
    else:
        out.append("*No arguments.*")
        out.append("")

    out.append(f"Example: {_example_hex(catalog, cmd)}")
    out.append("")
    return "\n".join(out)


def build_document(catalog) -> str:
    commands = catalog.commands
    s8 = [c for c in commands if (c["service"], c["subtype"]) == (8, 1)]
    other = [c for c in commands if (c["service"], c["subtype"]) != (8, 1)]

    lines: list[str] = []
    lines.append("# EOSAT-1 Command Reference")
    lines.append("")
    lines.append("**Document ID:** EOSAT1-UM-REF-010  ")
    lines.append("**Issue:** 2.0  ")
    # Source-derived, not wall-clock: the generated file must be a pure
    # function of its input, or every calendar day is a spurious diff.
    rev = source_revision([CONFIG / "commands" / "tc_catalog.yaml"])
    lines.append(f"**Source revision:** `tc_catalog.yaml@{rev}`  ")
    lines.append("**Classification:** UNCLASSIFIED — For Simulation Use Only")
    lines.append("")
    lines.append("> **GENERATED FILE — DO NOT EDIT BY HAND.**  ")
    lines.append("> Generated from the telecommand database "
                 "(`configs/eosat1/commands/tc_catalog.yaml`), the single "
                 "source of wire truth for every telecommand argument. Every "
                 "example hex string below is produced by the ground segment's "
                 "own encoder, so this reference and the uplinked bytes cannot "
                 "disagree. Regenerate after any change to the database.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── conventions ──
    lines.append("## 1. Reading this document")
    lines.append("")
    lines.append("**Offsets** are octets within the TC **data field**. For S8.1 "
                 "(Function Management) octet 0 is the `func_id` byte, so the "
                 "first argument sits at offset 1 — exactly how the on-board "
                 "dispatcher reads it (`func_id = data[0]`, arguments from "
                 "`data[1:]`).")
    lines.append("")
    lines.append("**Byte order** is big-endian everywhere, declared per field "
                 "rather than assumed.")
    lines.append("")
    lines.append("**Engineering vs raw.** Uplink arguments are fixed-point "
                 "integers with a declared scale and offset; the operator types "
                 "the engineering value and the ground segment computes the raw "
                 "field. `eng = raw x 0.01 - 273.15 (cK/LSB)` means the wire "
                 "carries centi-kelvin: a setpoint of -15.00 degC is raw 25815 = "
                 "`0x64D7`. **No negative engineering value ever produces a "
                 "two's-complement raw** on this spacecraft's temperature "
                 "arguments.")
    lines.append("")
    lines.append("**Rejection, not clamping.** A value outside the declared "
                 "range is refused with TM(1,8) execution failure "
                 "(commonly error `0x0003`), never silently clamped. The MCS "
                 "command builder shows the range inline and flags a bad value "
                 "before SEND.")
    lines.append("")

    # ── summary table ──
    lines.append("## 2. Command summary")
    lines.append("")
    lines.append("| Command | Service | func_id | Bytes | Arguments | Criticality |")
    lines.append("|---|---|---|---|---|---|")
    for c in sorted(commands, key=lambda x: (x["service"], x["subtype"],
                                             x.get("func_id") if x.get("func_id") is not None else -1,
                                             x["name"])):
        n = _expected_bytes(c)
        args = ", ".join(f["name"] for f in c.get("fields") or []) or "—"
        fid = "—" if c.get("func_id") is None else f"{c['func_id']} (0x{int(c['func_id']):02X})"
        lines.append(
            f"| `{c['name']}` | S{c['service']}.{c['subtype']} | {fid} "
            f"| {n if n is not None else 'var'} | {_esc(args)} "
            f"| {c.get('criticality') or 'nominal'} |")
    lines.append("")

    # ── S8.1 by subsystem ──
    lines.append("## 3. S8.1 Function Management commands")
    lines.append("")
    seen_groups: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for c in sorted(s8, key=lambda x: x["func_id"]):
        g = _group_of(int(c["func_id"]))
        if g not in by_group:
            by_group[g] = []
            seen_groups.append(g)
        by_group[g].append(c)
    for i, g in enumerate(seen_groups, start=1):
        lines.append(f"### 3.{i} {g}")
        lines.append("")
        for c in by_group[g]:
            lines.append(_command_block(catalog, c))

    # ── other services ──
    lines.append("## 4. Other PUS services")
    lines.append("")
    by_service: dict[int, list[dict]] = {}
    for c in other:
        by_service.setdefault(int(c["service"]), []).append(c)
    for i, svc in enumerate(sorted(by_service), start=1):
        lines.append(f"### 4.{i} Service {svc} — {SERVICE_NAMES.get(svc, '')}")
        lines.append("")
        for c in sorted(by_service[svc], key=lambda x: (x["subtype"], x["name"])):
            lines.append(_command_block(catalog, c))

    # ── quick commands ──
    if catalog.quick_commands:
        lines.append("## 5. MCS quick commands")
        lines.append("")
        lines.append("The console's QUICK COMMANDS panel is generated from the "
                     "same catalogue: each button names a command and its "
                     "**engineering** argument values, and the hex below is what "
                     "the shared encoder produces at click time.")
        lines.append("")
        lines.append("| Group | Button | Command | Arguments | Wire bytes |")
        lines.append("|---|---|---|---|---|")
        for q in catalog.quick_commands:
            try:
                wire = " ".join(f"{b:02X}" for b in
                                encode(q["command"], q.get("args") or {},
                                       catalog=catalog))
            except EncodeError as exc:      # pragma: no cover - config error
                wire = f"ERROR: {exc}"
            args = ", ".join(f"{k}={v}" for k, v in (q.get("args") or {}).items()) or "—"
            lines.append(f"| {_esc(q.get('group', ''))} | {_esc(q.get('label', ''))} "
                         f"| `{q['command']}` | {_esc(args)} | `{wire}` |")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Generated from `commands/tc_catalog.yaml` — "
                 f"{len(commands)} commands, "
                 f"{sum(len(c.get('fields') or []) for c in commands)} arguments.*")
    lines.append("")
    return "\n".join(lines)


def build_payload_settings_table(catalog) -> str:
    """The §Commandable settings block for manual/06_payload.md."""
    payload = [c for c in catalog.commands
               if (c["service"], c["subtype"]) == (8, 1)
               and int(c["func_id"]) in list(range(26, 40)) + list(range(92, 96))]
    lines = ["| Setting | Command | func_id | Wire form | Engineering range | Default | Readback |",
             "|---|---|---|---|---|---|---|"]
    for c in sorted(payload, key=lambda x: x["func_id"]):
        fields = c.get("fields") or []
        if fields:
            wire = ", ".join(
                f"{f['name']}:{f.get('type', 'uint8')}"
                + (f" x{(f.get('calibration') or {}).get('scale')}"
                   if str((f.get('calibration') or {}).get('type', '')).lower() == "linear"
                   else "")
                for f in fields)
            rng = "; ".join(_range_text(f) for f in fields)
            default = "; ".join(str(f.get("default")) for f in fields)
        else:
            wire, rng, default = "no arguments", "—", "—"
        rb = c.get("readback") or {}
        rb_txt = (f"{rb.get('description', '')} (0x{int(rb['param_id']):04X})"
                  if rb.get("param_id") is not None else "—")
        lines.append(
            f"| {_esc(c.get('description', c['name'])[:60])} | `{c['name']}` "
            f"| {c['func_id']} (0x{int(c['func_id']):02X}) | {_esc(wire)} "
            f"| {_esc(rng)} | {_esc(default)} | {_esc(rb_txt)} |")
    return "\n".join(lines)


MARK_START = "<!-- BEGIN GENERATED: payload commandable settings -->"
MARK_END = "<!-- END GENERATED: payload commandable settings -->"


def update_payload_manual(catalog, path: Path) -> bool:
    """Refresh the generated settings table inside manual/06_payload.md."""
    text = path.read_text()
    block = (f"{MARK_START}\n"
             "<!-- Generated by tools/gen_command_reference.py — do not edit by hand. -->\n\n"
             f"{build_payload_settings_table(catalog)}\n\n"
             f"{MARK_END}")
    if MARK_START in text and MARK_END in text:
        head = text.split(MARK_START)[0]
        tail = text.split(MARK_END)[1]
        new = head + block + tail
    else:
        new = text.rstrip() + (
            "\n\n---\n\n## Commandable settings\n\n"
            "Every payload setting reachable over the wire, with its encoding.\n\n"
            + block + "\n")
    if new != text:
        path.write_text(new)
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=str,
                    default=str(CONFIG / "manual" / "09_command_reference.md"))
    ap.add_argument("--check", action="store_true",
                    help="fail (exit 1) if the generated output differs from "
                         "what is on disk — the CI drift gate")
    args = ap.parse_args()

    catalog = load_catalog(CONFIG)
    doc = build_document(catalog)
    out = Path(args.output)
    payload_manual = CONFIG / "manual" / "06_payload.md"

    if args.check:
        current = out.read_text() if out.exists() else ""
        stale = []
        if current != doc:
            stale.append(str(out))
        if payload_manual.exists():
            before = payload_manual.read_text()
            update_payload_manual(catalog, payload_manual)
            if payload_manual.read_text() != before:
                payload_manual.write_text(before)   # restore; --check is read-only
                stale.append(str(payload_manual))
        if stale:
            print("STALE (re-run tools/gen_command_reference.py):")
            for s in stale:
                print(f"  {s}")
            return 1
        print("command reference is up to date")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    print(f"Command reference generated: {out}")
    print(f"  Commands: {len(catalog.commands)}")
    print(f"  Quick commands: {len(catalog.quick_commands)}")
    if payload_manual.exists() and update_payload_manual(catalog, payload_manual):
        print(f"  Updated: {payload_manual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
