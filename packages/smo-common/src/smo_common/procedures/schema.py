"""Canonical procedure schema + adapters for the four shipped dialects.

Roadmap X-12 / defect #83. Before this module "procedure" meant four unrelated
things in the tree:

  1. ``planning/activity_types.yaml`` ``command_sequence`` — UNTAGGED steps
     (``{service, subtype, func_id, params}`` | ``{wait_s}`` | ``{wait_for}``);
  2. the MCS Procedure Builder / ``procedures/custom/*.yaml`` — TAGGED steps
     (``{type: command|wait|tlm_check|go_nogo}``);
  3. ``procedures/**/*.md`` (64 files) — free-form markdown with
     ``**TC:** `MNEMONIC` (Service N, Subtype M, func_id F)`` annotations;
  4. ``obcp/*.yaml`` — on-board S18 step blobs (``{delay_s, service, subtype,
     func_id, data_hex}``).

The CANONICAL form adopted here is the tagged one (2), because it is the only
dialect that can express every step kind the ground runner supports. This
module defines it once and provides an adapter from each of the other
dialects, so nothing has to be rewritten to become runnable:

    from_activity_sequence(seq)  -> canonical   (dialect 1)
    from_builder_steps(steps)    -> canonical   (dialect 2, normalising)
    from_markdown(text)          -> canonical   (dialect 3, command steps only)
    to_canonical(steps)          -> canonical   (auto-detects 1 vs 2)

``smo_mcs.procedure_runner`` consumes the canonical form directly; the
untagged dialect keeps working because ``to_canonical`` converts it first.

Step types
----------
``command``   service, subtype, and EITHER ``data_hex`` OR the argument fields
              the runner packs (``func_id`` + ``params``, ``sid``, ``store_id``,
              ``param_id`` + ``value``, ``address`` + ``data``); optional
              ``verify`` condition.
``wait``      ``seconds``.
``tlm_check`` ``parameter`` (dot-path), ``condition`` operator, ``value``,
              ``timeout_s``.
``go_nogo``   ``label`` — operator decision gate.
``note``      ``text`` — non-executable operator instruction.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_SCHEMA_VERSION = 1

STEP_TYPES = ("command", "wait", "tlm_check", "go_nogo", "note")

#: Comparison operators accepted by ``tlm_check.condition`` / ``verify.operator``.
CONDITION_OPERATORS = ("==", "=", "!=", ">", "<", ">=", "<=",
                       "eq", "ne", "gt", "lt", "ge", "le")

# ``**TC:** `MNEMONIC` (Service 8, Subtype 1, func_id 55)`` in the shipped
# markdown procedures. Same shape the content test parses, kept in sync
# deliberately (tests/test_content/test_procedures_content.py).
_TC_LINE = re.compile(r"\*\*TC:\*\*\s*`([^`]+)`(?P<rest>[^\n]*)")
_ANNOTATION = re.compile(r"Service\s+(\d+),\s*Subtype\s+(\d+)")
_FUNC_ID = re.compile(r"func_id\s*[= ]\s*(\d+)")


class StepCondition(BaseModel):
    """A telemetry condition used by ``tlm_check`` and by ``command.verify``."""
    model_config = ConfigDict(extra="allow")

    parameter: str = ""
    value: Any = None
    operator: str = "=="
    timeout_s: float = 30.0


class ProcedureStep(BaseModel):
    """One canonical step. Extra keys are preserved, never silently dropped."""
    model_config = ConfigDict(extra="allow")

    type: str
    description: str = ""

    # command
    service: int | None = None
    subtype: int | None = None
    func_id: int | None = None
    params: list[int] = Field(default_factory=list)
    data_hex: str = ""
    verify: StepCondition | None = None

    # wait
    seconds: float | None = None

    # tlm_check
    parameter: str | None = None
    condition: str | None = None
    value: Any = None
    timeout_s: float | None = None

    # go_nogo / note
    label: str | None = None
    text: str | None = None


class CanonicalProcedure(BaseModel):
    """A runnable procedure in the canonical form."""
    model_config = ConfigDict(extra="allow")

    schema_version: int = CANONICAL_SCHEMA_VERSION
    id: str = ""
    name: str = ""
    description: str = ""
    source: str = ""          # where the definition came from (dialect/path)
    steps: list[ProcedureStep] = Field(default_factory=list)


# ── Helpers ─────────────────────────────────────────────────────────

def _as_int(value: Any) -> int | None:
    """Accept 26, "26" and "0x1A" — the three forms used across the configs."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _condition(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {
        "parameter": str(raw.get("parameter", "")),
        "value": raw.get("value"),
        "operator": str(raw.get("operator", raw.get("condition", "=="))),
        "timeout_s": float(raw.get("timeout_s", 30) or 30),
    }


def _passthrough(raw: dict, out: dict) -> None:
    """Carry the runner's other argument fields across unchanged.

    ``ProcedureRunner._build_data_hex`` also understands ``sid``, ``store_id``,
    ``param_id``/``value``, ``address``/``data`` and ``parameters``; the
    canonical form keeps them rather than inventing a second encoding.
    """
    for key in ("sid", "store_id", "param_id", "address", "data",
                "parameters", "mnemonic", "criticality", "position"):
        if key in raw and raw[key] is not None:
            out[key] = raw[key]


# ── Adapters ────────────────────────────────────────────────────────

def canonical_step(raw: dict) -> dict:
    """Normalise ONE step of any ground dialect into the canonical form."""
    if not isinstance(raw, dict):
        return {"type": "note", "text": str(raw),
                "description": f"Unrecognised step: {raw!r}"}

    tagged = str(raw.get("type", "") or "").strip().lower()

    # Untagged dialect (activity_types.yaml) -> infer the tag.
    if tagged not in STEP_TYPES:
        if "wait_s" in raw:
            tagged = "wait"
        elif "wait_for" in raw:
            tagged = "tlm_check"
        elif "service" in raw:
            tagged = "command"
        elif "text" in raw or "note" in raw:
            tagged = "note"
        else:
            tagged = ""

    if tagged == "wait":
        seconds = raw.get("seconds", raw.get("wait_s", 0))
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            seconds = 0.0
        return {"type": "wait", "seconds": seconds,
                "description": raw.get("description") or f"Wait {seconds:g}s"}

    if tagged == "tlm_check":
        source = raw.get("wait_for") if isinstance(raw.get("wait_for"), dict) else raw
        cond = _condition(source) or {}
        out = {
            "type": "tlm_check",
            "parameter": cond.get("parameter", ""),
            "condition": cond.get("operator", "=="),
            "value": cond.get("value"),
            "timeout_s": cond.get("timeout_s", 30.0),
        }
        out["description"] = raw.get("description") or describe_step(out)
        return out

    if tagged == "go_nogo":
        label = raw.get("label") or raw.get("description") or "operator decision"
        return {"type": "go_nogo", "label": label,
                "description": raw.get("description") or f"GO/NO-GO: {label}"}

    if tagged == "note":
        text = raw.get("text") or raw.get("note") or raw.get("description") or ""
        return {"type": "note", "text": text,
                "description": raw.get("description") or f"Note: {text}"}

    if tagged == "command":
        out: dict[str, Any] = {"type": "command"}
        service = _as_int(raw.get("service"))
        subtype = _as_int(raw.get("subtype"))
        if service is not None:
            out["service"] = service
        if subtype is not None:
            out["subtype"] = subtype
        func_id = _as_int(raw.get("func_id"))
        if func_id is not None:
            out["func_id"] = func_id
        params = raw.get("params")
        if isinstance(params, (list, tuple)):
            coerced = [_as_int(p) for p in params]
            out["params"] = [p for p in coerced if p is not None]
        data_hex = str(raw.get("data_hex", "") or "").replace(" ", "")
        if data_hex:
            out["data_hex"] = data_hex
        verify = _condition(raw.get("verify"))
        if verify:
            out["verify"] = verify
        _passthrough(raw, out)
        out["description"] = raw.get("description") or describe_step(out)
        return out

    # Genuinely unrecognised: keep it visible as a note rather than pretending
    # it is executable (the runner fails closed on unknown steps).
    return {"type": "note", "text": str(raw),
            "description": f"Unrecognised step: {raw!r}"}


def to_canonical(steps: list[dict] | None) -> list[dict]:
    """Normalise a whole step list of any ground dialect."""
    return [canonical_step(s) for s in (steps or [])]


def from_activity_sequence(sequence: list[dict] | None) -> list[dict]:
    """Adapter for ``activity_types.yaml`` ``command_sequence`` (dialect 1)."""
    return to_canonical(sequence)


def from_builder_steps(steps: list[dict] | None) -> list[dict]:
    """Adapter for MCS-builder / ``procedures/custom/*.yaml`` (dialect 2)."""
    return to_canonical(steps)


def from_markdown(text: str, catalog: dict[str, dict] | None = None) -> list[dict]:
    """Adapter for a shipped ops procedure (dialect 3).

    Extracts the ``**TC:**`` annotations in document order and turns each into
    a canonical ``command`` step. Prose, GO/NO-GO wording and verification text
    are NOT interpreted — this produces the commanding skeleton of the
    procedure, which is why only procedures explicitly marked ``runnable:`` in
    ``procedure_index.yaml`` are offered for execution.

    ``catalog`` (optional) maps mnemonic -> ``tc_catalog.yaml`` entry and is
    used to fill in service/subtype/func_id the annotation omits.
    """
    catalog = catalog or {}
    steps: list[dict] = []
    for match in _TC_LINE.finditer(text or ""):
        mnemonic = match.group(1).strip()
        rest = match.group("rest") or ""
        entry = catalog.get(mnemonic, {})

        annotation = _ANNOTATION.search(rest)
        service = int(annotation.group(1)) if annotation else _as_int(entry.get("service"))
        subtype = int(annotation.group(2)) if annotation else _as_int(entry.get("subtype"))
        if service is None or subtype is None:
            continue

        func_match = _FUNC_ID.search(rest)
        func_id = (int(func_match.group(1)) if func_match
                   else _as_int(entry.get("func_id")))

        raw: dict[str, Any] = {
            "type": "command",
            "service": service,
            "subtype": subtype,
            "mnemonic": mnemonic,
            "description": mnemonic,
        }
        if func_id is not None:
            raw["func_id"] = func_id
        steps.append(canonical_step(raw))
    return steps


# ── Validation ──────────────────────────────────────────────────────

def validate_steps(
    steps: list[dict] | None,
    known_parameters: set[str] | None = None,
) -> list[str]:
    """Return a list of problems with a canonical step list (empty = valid).

    ``known_parameters`` is the telemetry vocabulary the runner can actually
    resolve. When supplied, a ``tlm_check`` (or a ``command.verify``) naming a
    parameter outside it is REJECTED here — at save time — instead of failing
    as a 30 s timeout mid-pass, which is what used to happen (audit Q7.1).
    """
    problems: list[str] = []
    for idx, step in enumerate(to_canonical(steps)):
        kind = step.get("type")
        where = f"step {idx + 1}"
        if kind not in STEP_TYPES:
            problems.append(f"{where}: unknown step type {kind!r}")
            continue

        if kind == "command":
            if step.get("service") is None or step.get("subtype") is None:
                problems.append(f"{where}: command needs service and subtype")
            data_hex = step.get("data_hex", "")
            if data_hex:
                try:
                    bytes.fromhex(data_hex)
                except ValueError:
                    problems.append(f"{where}: data_hex {data_hex!r} is not hex")

        if kind == "wait":
            seconds = step.get("seconds")
            if seconds is None or seconds < 0:
                problems.append(f"{where}: wait needs a non-negative 'seconds'")

        if kind == "tlm_check":
            operator = step.get("condition") or "=="
            if operator not in CONDITION_OPERATORS:
                problems.append(f"{where}: unknown comparison {operator!r}")

        for parameter, label in _referenced_parameters(step):
            if not parameter:
                problems.append(f"{where}: {label} needs a parameter")
            elif known_parameters is not None and parameter not in known_parameters:
                suggestion = _closest(parameter, known_parameters)
                problems.append(
                    f"{where}: {label} parameter '{parameter}' is not a "
                    f"telemetry parameter the procedure runner can read"
                    + (f" — did you mean '{suggestion}'?" if suggestion else "")
                )
    return problems


def _referenced_parameters(step: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if step.get("type") == "tlm_check":
        out.append((str(step.get("parameter") or ""), "tlm_check"))
    verify = step.get("verify")
    if isinstance(verify, dict):
        out.append((str(verify.get("parameter") or ""), "verify"))
    return out


def _closest(name: str, vocabulary: set[str]) -> str | None:
    import difflib
    matches = difflib.get_close_matches(name, sorted(vocabulary), n=1, cutoff=0.6)
    return matches[0] if matches else None


# ── Rendering ───────────────────────────────────────────────────────

def describe_step(step: dict) -> str:
    """One line of readable text for a step, in ANY dialect.

    ``ProcedureRunner._step_desc`` used to fall through to ``str(step)`` for
    every builder-authored step, so the MCS step list showed operators raw
    Python dicts (audit Q7.1). This is the single renderer for all dialects.
    """
    if not isinstance(step, dict):
        return str(step)

    kind = str(step.get("type", "") or "").strip().lower()
    if kind not in STEP_TYPES:
        if "wait_s" in step:
            return f"Wait {step['wait_s']}s"
        if "wait_for" in step:
            condition = step["wait_for"] or {}
            return (f"Wait for {condition.get('parameter', '?')} "
                    f"{condition.get('operator', '==')} {condition.get('value', '?')}")
        if "service" in step:
            return step.get("description") or (
                f"S{step.get('service')}.{step.get('subtype')}")
        return str(step)

    if kind == "wait":
        seconds = step.get("seconds", step.get("wait_s", 0))
        try:
            return f"Wait {float(seconds):g}s"
        except (TypeError, ValueError):
            return f"Wait {seconds}s"

    if kind == "tlm_check":
        return (f"Check {step.get('parameter') or '?'} "
                f"{step.get('condition') or '=='} {step.get('value')}"
                f" (timeout {step.get('timeout_s', 30):g}s)")

    if kind == "go_nogo":
        return f"GO/NO-GO: {step.get('label') or 'operator decision'}"

    if kind == "note":
        return f"Note: {step.get('text') or ''}".rstrip()

    # command
    label = step.get("mnemonic") or step.get("description")
    detail = f"S{step.get('service')}.{step.get('subtype')}"
    func_id = step.get("func_id")
    if func_id is not None:
        detail += f" func {func_id}"
        params = step.get("params") or []
        if params:
            detail += "(" + ",".join(str(p) for p in params) + ")"
    elif step.get("data_hex"):
        detail += f" data={step['data_hex']}"
    if label and label != detail:
        return f"{label} [{detail}]"
    return detail
