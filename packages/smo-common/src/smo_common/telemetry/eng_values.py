"""Engineering values for discrete (enumerated) telemetry parameters.

Field report 2026-08-20: "check in the new version that parameters are
displayed in engineering values, e.g. for AOCS mode - but this may be a
generic issue - at the minute only the raw values seem to display".

It was generic. The modern console formats every catalogue-driven value through
a numeric formatter and appends the ``units`` string, so ``aocs.mode`` renders
as ``4`` rather than ``NOMINAL``. The classic console looked better only
because someone had hand-written a bespoke ``aocsModeLabel()``-style function
for each widget that mattered — real, but duplicated per widget, and a third
copy of codes that already live in configuration.

The root cause was upstream of both consoles: ``telemetry/parameters.yaml`` has
no structured field for state codes. Where the codes are documented at all,
they are documented **in prose**, inside ``description``:

    - { id: 0x02C9, name: aocs.gyro_cal_state, subsystem: aocs,
        description: "Gyro bias calibration state: 0=IDLE, 1=RUNNING,
                      2=COMPLETE, 3=ABORTED" }

This module turns that existing, already-authored truth into a structured map,
so engineering values can be rendered generically instead of one widget at a
time. Three sources, in priority order:

1. an explicit ``states:`` mapping on the parameter (authoritative when
   present — for anything whose codes are not, or cannot be, written in prose);
2. a *canonical* codes file, where one already exists and is declared the
   single source of truth by its own header — today that is
   ``aocs/aocs_mode_codes.yaml`` for ``aocs.mode`` (0x020F), which carries no
   description at all;
3. the ``description`` prose, parsed strictly.

Nothing is invented. The parser requires at least two ``N=NAME`` pairs before
it will claim a parameter is enumerated, so a description that merely mentions
a number in passing ("0 = fully calibrated") does not silently become a state
table, and a parameter with no documented codes keeps rendering as a raw
number — which is honest, and is what it did before.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

#: ``12=NAME`` / ``12 = NAME``. The name must start with a letter, so a bare
#: range like "0-3" or a unit like "0=1.5V" cannot masquerade as a state.
#: The negative lookbehind keeps a decimal ("v1.0=x") or a dotted parameter
#: name from being read as a code.
_PAIR = re.compile(r"(?<![\w.])(\d+)\s*=\s*([A-Za-z][A-Za-z0-9_\-]*(?: [A-Za-z0-9_\-]+)*)")

#: A description must document at least this many codes before we treat the
#: parameter as enumerated. One pair is far more likely to be an aside.
_MIN_PAIRS = 2


def parse_states_from_description(description: str) -> dict[int, str]:
    """Extract ``{code: name}`` from prose, or ``{}`` if it is not a state list.

    Deliberately conservative: see ``_MIN_PAIRS``. Trailing prose after the
    last pair is not captured as part of a name because names stop at the
    first comma, semicolon, or sentence break.
    """
    text = str(description or "")
    if not text:
        return {}
    out: dict[int, str] = {}
    for chunk in re.split(r"[;,)(]|\.\s", text):
        for code, name in _PAIR.findall(chunk):
            name = name.strip()
            if not name:
                continue
            out.setdefault(int(code), name)
    return out if len(out) >= _MIN_PAIRS else {}


def load_canonical_state_files(config_dir: Path) -> dict[int, dict[int, str]]:
    """Codes files that declare themselves the single source of truth.

    Kept as an explicit, short registry rather than a directory scan: a file is
    only canonical for a parameter because a human said so in its header, and
    that claim should be visible here rather than inferred from a filename.
    """
    out: dict[int, dict[int, str]] = {}
    aocs_modes = Path(config_dir) / "aocs" / "aocs_mode_codes.yaml"
    if aocs_modes.is_file():
        try:
            doc = yaml.safe_load(aocs_modes.read_text()) or {}
            modes = doc.get("modes") or {}
            table = {int(k): str(v.get("name", "")).strip()
                     for k, v in modes.items() if str(v.get("name", "")).strip()}
            if table:
                out[0x020F] = table          # aocs.mode
        except (yaml.YAMLError, AttributeError, ValueError):
            pass                              # a malformed codes file is not fatal
    return out


def states_for(param: dict[str, Any], canonical: dict[int, dict[int, str]] | None = None,
               ) -> dict[int, str]:
    """The state table for one parameter dict, by the priority above."""
    explicit = param.get("states")
    if isinstance(explicit, dict) and explicit:
        return {int(k): str(v) for k, v in explicit.items()}
    pid = param.get("id")
    if isinstance(pid, str):
        pid = int(pid, 0)
    if canonical and pid in canonical:
        return dict(canonical[pid])
    return parse_states_from_description(param.get("description", ""))


def build_state_tables(parameters: list[dict[str, Any]],
                       config_dir: Path) -> dict[int, dict[int, str]]:
    """``{param_id: {code: name}}`` for every parameter that HAS state codes."""
    canonical = load_canonical_state_files(config_dir)
    out: dict[int, dict[int, str]] = {}
    for p in parameters:
        pid = p.get("id")
        if isinstance(pid, str):
            pid = int(pid, 0)
        if pid is None:
            continue
        table = states_for(p, canonical)
        if table:
            out[int(pid)] = table
    return out


def format_value(value: Any, states: dict[int, str] | None, units: str = "") -> str:
    """Render one value the way an operator should read it.

    ``4`` becomes ``NOMINAL (4)`` when the parameter is enumerated — the raw
    code is kept in parentheses deliberately, because operators cross-check
    against procedure text and packet dumps that quote the number.
    """
    if value is None:
        return "—"
    if states:
        try:
            code = int(round(float(value)))
        except (TypeError, ValueError):
            return str(value)
        name = states.get(code)
        return f"{name} ({code})" if name else f"UNKNOWN ({code})"
    if units:
        return f"{value} {units}"
    return str(value)
