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
``go_nogo``   ``label`` — operator decision gate; optional ``on_go`` /
              ``on_nogo`` branch targets (see below).
``note``      ``text`` — non-executable operator instruction.
``label``     ``name`` — a named jump target. Non-executable.
``branch``    ``parameter``/``condition``/``value`` predicate plus ``on_true``
              and ``on_false`` label targets, ``timeout_s`` and ``max_taken``.

Branching (2026-08b Phase 5, ruling R4)
---------------------------------------
The contingency and emergency cards are not straight lines. EMG-006 *Thermal
Runaway* says, in prose, "if the heater turned off skip to Step 5; if it is
still on go to Step 3", and Step 4 sheds a power line. Executed flat — which is
all the old ``from_markdown`` could produce — the runner would take BOTH arms
and shed the heater power line unconditionally on a spacecraft whose heater had
already obeyed the OFF command. Franz's ruling R4 asks for the contingency
cards to be runnable, so the branch structure has to be REPRESENTED, not
flattened.

Three additions make that possible:

* ``label`` steps name a target;
* ``branch`` steps evaluate a telemetry predicate and jump to one of two
  labels;
* ``go_nogo`` steps may carry ``on_go`` / ``on_nogo`` labels, so an operator
  decision is a real fork rather than a pause.

A **bounded loop guard** is mandatory: any jump to a label that appears EARLIER
in the step list is a loop, and such a step must carry ``max_taken`` (1..10).
The runner is required to stop taking that arm once the count is exhausted and
fall through to the next step. Without the bound, "if the heater is still on,
go back to Step 3" is an infinite loop that a real recovery card would never
be allowed to contain either.

Markdown annotations (dialect 3)
--------------------------------
::

    **LABEL:**  `STEP-5`
    **BRANCH:** `tcs.htr_battery == 0` -> GO `STEP-5` | NO-GO `STEP-3`
                (timeout 30 s, max 2)
    **GO/NO-GO:** operator wording -> GO `STEP-5` | NO-GO `STEP-3`
    **Verify:** `eps.pl_htr_bat == 0` (0x0115, timeout 30 s) — prose after the
                dash is ignored

``**Verify:**`` becomes a real ``tlm_check`` only when its FIRST backtick span
parses as ``parameter <op> value``; the ~600 existing prose ``**Verify:**``
lines are untouched, because ``` `tcs.htr_battery` (0x040A) should = 0 ``` does
not parse as a predicate. That is deliberate: promoting a verify condition is
an explicit editorial act, not something that happens to prose by accident.

REFUSAL, not silent flattening
------------------------------
``markdown_steps()`` returns ``(steps, problems)``. A document is only fit to
be marked ``runnable: {kind: markdown}`` when ``problems`` is empty, and the
problems include:

* a ``**TC:**`` annotation that cannot be resolved to a service/subtype (the
  old code ``continue``-d past these — 13 annotations in the shipped tree were
  silently dropped, 4 of them 80 % of COM-013);
* a branch or go/no-go target naming a label the document does not declare;
* a backward jump with no ``max_taken`` bound;
* **unstructured branching prose** — "proceed to Step 3", "skip to Step 5" —
  in a document that declares no ``**BRANCH:**`` structure. This is the check
  that stops EMG-006 being flattened into "shed the power line always".
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_SCHEMA_VERSION = 1

STEP_TYPES = ("command", "wait", "tlm_check", "go_nogo", "note",
              "label", "branch")

#: Step kinds that send nothing and check nothing. A procedure made only of
#: these is inert, and ``markdown_steps`` says so.
INERT_STEP_TYPES = ("note", "label")

#: Comparison operators accepted by ``tlm_check.condition`` / ``verify.operator``.
CONDITION_OPERATORS = ("==", "=", "!=", ">", "<", ">=", "<=",
                       "eq", "ne", "gt", "lt", "ge", "le")

#: Hard ceiling on a bounded loop. A recovery card that needs more than ten
#: passes round the same arm is not a procedure, it is a control loop, and it
#: belongs in the on-board software rather than in an operator document.
MAX_LOOP_ITERATIONS = 10

#: Problem categories reported by ``markdown_steps``.
#:
#: The split matters. ``TC_DROPPED``, ``ACTION_INERT`` and ``BAD_BRANCH`` are
#: defects in ANY document — an annotation that does not survive extraction, a
#: command written where the extractor cannot see it, a malformed branch — and
#: the content gate pins all three at zero across the whole tree.
#:
#: ``PROSE_BRANCH`` is different. A card that says "if the heater is still on,
#: proceed to Step 3" in prose is a perfectly good *document*; it is only
#: unfit to be EXECUTED, because a flat runner takes both arms. So it blocks
#: `runnable: {kind: markdown}` and nothing else. Making it a tree-wide error
#: would demand that all 65 operations documents be rewritten as programs,
#: which is the opposite of what an operations manual is for.
PROBLEM_TC_DROPPED = "TC_DROPPED"
PROBLEM_ACTION_INERT = "ACTION_INERT"
PROBLEM_BAD_BRANCH = "BAD_BRANCH"
PROBLEM_PROSE_BRANCH = "PROSE_BRANCH"

#: Categories that are a defect wherever they appear.
EXTRACTION_PROBLEM_CATEGORIES = (
    PROBLEM_TC_DROPPED, PROBLEM_ACTION_INERT, PROBLEM_BAD_BRANCH)

#: Categories that additionally block `runnable: {kind: markdown}`.
RUNNABLE_PROBLEM_CATEGORIES = (
    EXTRACTION_PROBLEM_CATEGORIES + (PROBLEM_PROSE_BRANCH,))

# ``**TC:** `MNEMONIC` (Service 8, Subtype 1, func_id 55)`` in the shipped
# markdown procedures. Same shape the content test parses, kept in sync
# deliberately (tests/test_content/test_procedures_content.py).
#: Every annotation is anchored to the START of a line (allowing list markers
#: and blockquote prefixes). Without the anchor the extractor matches its own
#: documentation: a sentence containing the words "a `**TC:**` line" becomes a
#: command step, and because the mnemonic group could span newlines it then
#: swallowed the next several paragraphs. Annotations are written at line start
#: in all 65 shipped documents, so the anchor costs nothing and removes a whole
#: class of false positive.
_ANNOT_PREFIX = r"(?m)^[ \t]*(?:[-*+]\s+|>\s*)?"
_TC_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*TC:\*\*\s*`([^`\n]+)`(?P<rest>[^\n]*)")
#: A `**TC:**` line with NO backtick span at all — prose masquerading as
#: commanding, e.g. EMG-004's "**TC:** Alternate between `A` and `B`". The old
#: extractor could not even see these; they now surface as problems.
_TC_LINE_ANY = re.compile(_ANNOT_PREFIX + r"\*\*TC:\*\*(?P<rest>[^\n]*)")
_ANNOTATION = re.compile(r"Service\s+(\d+),\s*Subtype\s+(\d+)")
_SERVICE_ONLY = re.compile(r"Service\s+(\d+)")
_FUNC_ID = re.compile(r"func(?:_id)?\s*[= ]\s*(\d+)")

#: `MNEMONIC(arg=1, arg2=2)` -> `MNEMONIC`. The catalogue is keyed on the bare
#: mnemonic; looking it up with the arguments attached (which is what the old
#: code did) missed EVERY annotated call, so the catalogue fallback that was
#: supposed to fill in an omitted Service/Subtype never once fired.
_MNEMONIC_HEAD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)")

# ── Branching annotations (2026-08b Phase 5) ────────────────────────
_LABEL_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*LABEL:\*\*\s*`([A-Za-z0-9_.\-]+)`")
_BRANCH_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*BRANCH:\*\*\s*`(?P<pred>[^`\n]+)`\s*->\s*"
    r"GO\s*`(?P<go>[A-Za-z0-9_.\-]+)`\s*\|\s*"
    r"NO-?GO\s*`(?P<nogo>[A-Za-z0-9_.\-]+)`(?P<rest>[^\n]*)")
_GONOGO_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*GO/NO-?GO:\*\*(?P<text>[^\n]*)")
_GONOGO_TARGETS = re.compile(
    r"->\s*GO\s*`(?P<go>[A-Za-z0-9_.\-]+)`\s*\|\s*"
    r"NO-?GO\s*`(?P<nogo>[A-Za-z0-9_.\-]+)`")
_VERIFY_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*Verify:\*\*\s*`([^`\n]+)`(?P<rest>[^\n]*)")
#: `parameter <op> value` — the whole of a backtick span, nothing else.
_PREDICATE = re.compile(
    r"^\s*(?P<param>[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\s*"
    r"(?P<op>==|!=|>=|<=|>|<)\s*(?P<value>[^\s]+)\s*$")
_TIMEOUT = re.compile(r"timeout\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.I)
_MAX_TAKEN = re.compile(r"max\s+([0-9]+)", re.I)

#: Branching written as PROSE. A document that says this and declares no
#: `**BRANCH:**` structure cannot be executed safely, because a flat runner
#: takes every arm — for EMG-006 that means shedding a power line
#: unconditionally. Matching it is how the extractor knows to refuse.
_PROSE_BRANCH = re.compile(
    r"(?:proceed|skip|go|jump|return|continue)\s+(?:to|back\s+to)\s+"
    r"[Ss]tep\s+\d+|->\s*[Ss]tep\s+\d+", re.I)

#: `**Action:**` prose — the tag the extractor has never understood. Every
#: command written behind it is invisible to the runner, which is the whole
#: root cause of the 21 inert procedures.
_ACTION_LINE = re.compile(
    _ANNOT_PREFIX + r"\*\*Action:?\*\*:?(?P<rest>[^\n]*)")


class MarkdownProblem(BaseModel):
    """One reason a markdown procedure was not fully understood.

    A plain string was tempting, but the gate needs to treat "this annotation
    is dropped" (a defect anywhere) differently from "this card branches in
    prose" (only a bar to execution), and encoding that distinction in the
    message text is how gates start matching on substrings.
    """
    model_config = ConfigDict(extra="forbid")

    category: str
    message: str
    line: int = 0

    def __str__(self) -> str:                       # pragma: no cover - trivial
        where = f"line {self.line}: " if self.line else ""
        return f"[{self.category}] {where}{self.message}"

    @property
    def blocks_extraction(self) -> bool:
        return self.category in EXTRACTION_PROBLEM_CATEGORIES

    @property
    def blocks_runnable(self) -> bool:
        return self.category in RUNNABLE_PROBLEM_CATEGORIES


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

    # label
    name: str | None = None

    # branch / go_nogo targets. `on_true`/`on_false` are label NAMES, not
    # indices: an index would silently re-point itself the moment a step is
    # inserted, which is precisely the class of edit an operations document
    # receives most often.
    on_true: str | None = None
    on_false: str | None = None
    on_go: str | None = None
    on_nogo: str | None = None
    #: Bounded loop guard. Required on any jump to an EARLIER label.
    max_taken: int | None = None


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
        out = {"type": "go_nogo", "label": label,
               "description": raw.get("description") or f"GO/NO-GO: {label}"}
        for key in ("on_go", "on_nogo"):
            if raw.get(key):
                out[key] = str(raw[key])
        max_taken = _as_int(raw.get("max_taken"))
        if max_taken is not None:
            out["max_taken"] = max_taken
        return out

    if tagged == "label":
        name = str(raw.get("name") or raw.get("label") or "").strip()
        return {"type": "label", "name": name,
                "description": raw.get("description") or f"Label: {name}"}

    if tagged == "branch":
        source = raw.get("wait_for") if isinstance(raw.get("wait_for"), dict) else raw
        cond = _condition(source) or {}
        out = {
            "type": "branch",
            "parameter": cond.get("parameter", ""),
            "condition": cond.get("operator", "=="),
            "value": cond.get("value"),
            "timeout_s": cond.get("timeout_s", 30.0),
            "on_true": str(raw.get("on_true") or ""),
            "on_false": str(raw.get("on_false") or ""),
        }
        max_taken = _as_int(raw.get("max_taken"))
        if max_taken is not None:
            out["max_taken"] = max_taken
        out["description"] = raw.get("description") or describe_step(out)
        return out

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


def mnemonic_head(text: str) -> str:
    """``PAYLOAD_SET_MODE(mode=3)`` -> ``PAYLOAD_SET_MODE``.

    The catalogue is keyed on the bare mnemonic. ``from_markdown`` used to look
    it up with the argument list still attached, so ``catalog.get(mnemonic)``
    missed every annotated call in the tree and the catalogue fallback for an
    omitted Service/Subtype could never fire.
    """
    match = _MNEMONIC_HEAD.match(text or "")
    return match.group(1).upper() if match else ""


def _coerce_predicate_value(raw: str) -> Any:
    """`0`, `-15.0`, `0x03`, `NADIR` — keep numbers numeric, text as text."""
    token = (raw or "").strip().strip("`'\"")
    as_int = _as_int(token)
    if as_int is not None:
        return as_int
    try:
        return float(token)
    except ValueError:
        return token


def _parse_predicate(span: str) -> dict | None:
    match = _PREDICATE.match(span or "")
    if not match:
        return None
    return {
        "parameter": match.group("param"),
        "operator": match.group("op"),
        "value": _coerce_predicate_value(match.group("value")),
    }


def markdown_steps(
    text: str,
    catalog: dict[str, dict] | None = None,
) -> tuple[list[dict], list[MarkdownProblem]]:
    """Extract a canonical step list from a shipped ops procedure (dialect 3).

    Returns ``(steps, problems)``. Each problem carries a category (see
    ``EXTRACTION_PROBLEM_CATEGORIES`` / ``RUNNABLE_PROBLEM_CATEGORIES``); a
    document is fit to carry ``runnable: {kind: markdown}`` only when no
    problem has ``blocks_runnable``.

    What is extracted, in document order:

    ``**TC:**``     -> ``command``
    ``**Verify:**`` -> ``tlm_check``, but only when the first backtick span is
                       a bare ``parameter <op> value`` predicate
    ``**LABEL:**``  -> ``label``
    ``**BRANCH:**`` -> ``branch``
    ``**GO/NO-GO:**`` -> ``go_nogo`` (with targets when the line carries them)

    What is REFUSED (each becomes a problem, never a silent drop):

    * a ``**TC:**`` line whose Service/Subtype resolves neither from the
      annotation nor from the catalogue;
    * a ``**TC:**`` line with no backtick mnemonic at all;
    * a branch/go-nogo target naming an undeclared label;
    * a backward jump with no ``max_taken`` bound, or one outside 1..10;
    * unstructured "proceed to Step N" branching prose in a document that
      declares no ``**BRANCH:**`` structure;
    * a catalogued command mnemonic hiding inside an ``**Action:**`` line —
      the root cause of the 21 inert procedures, and the one defect the old
      extractor was structurally incapable of noticing.
    """
    catalog = catalog or {}
    text = text or ""
    steps: list[dict] = []
    problems: list[MarkdownProblem] = []

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    def problem(category: str, message: str, line: int = 0) -> None:
        problems.append(MarkdownProblem(
            category=category, message=message, line=line))

    # ── Pass 1: collect every annotation with its position ──────────
    events: list[tuple[int, str, Any]] = []

    tc_with_backticks = {m.start() for m in _TC_LINE.finditer(text)}
    for match in _TC_LINE_ANY.finditer(text):
        if match.start() not in tc_with_backticks:
            problem(PROBLEM_TC_DROPPED,
                    f"**TC:** line carries no `MNEMONIC` span, so it commands "
                    f"nothing: {match.group('rest').strip()[:70]!r}",
                    line_of(match.start()))

    for match in _TC_LINE.finditer(text):
        events.append((match.start(), "tc", match))
    for match in _VERIFY_LINE.finditer(text):
        events.append((match.start(), "verify", match))
    for match in _LABEL_LINE.finditer(text):
        events.append((match.start(), "label", match))
    branch_spans = []
    for match in _BRANCH_LINE.finditer(text):
        events.append((match.start(), "branch", match))
        branch_spans.append((match.start(), match.end()))
    for match in _GONOGO_LINE.finditer(text):
        events.append((match.start(), "gonogo", match))
        # A GO/NO-GO carrying explicit targets IS declared branch structure —
        # it is the right representation when the decision variable is computed
        # on the ground (NOM-008's clock delta) and no telemetry predicate can
        # honestly stand in for it.
        if _GONOGO_TARGETS.search(match.group("text") or ""):
            branch_spans.append((match.start(), match.end()))

    events.sort(key=lambda e: e[0])

    declared_labels: set[str] = {
        m.group(1) for m in _LABEL_LINE.finditer(text)}
    referenced: list[tuple[int, str]] = []   # (step index, label)

    for pos, kind, match in events:
        line = line_of(pos)

        if kind == "tc":
            mnemonic = match.group(1).strip()
            rest = match.group("rest") or ""
            entry = catalog.get(mnemonic_head(mnemonic), {})

            annotation = _ANNOTATION.search(rest)
            if annotation:
                service = int(annotation.group(1))
                subtype = int(annotation.group(2))
            else:
                # Fall back to the catalogue — which is what the old code
                # believed it was doing. A `(Service 8)`-only annotation still
                # pins the service, so disagreeing with the catalogue is
                # itself worth reporting.
                service = _as_int(entry.get("service"))
                subtype = _as_int(entry.get("subtype"))
                service_only = _SERVICE_ONLY.search(rest)
                if service_only and service is not None \
                        and int(service_only.group(1)) != service:
                    problem(PROBLEM_TC_DROPPED,
                            f"`{mnemonic}` is annotated Service "
                            f"{service_only.group(1)} but the catalogue says "
                            f"Service {service}", line)
            if service is None or subtype is None:
                problem(PROBLEM_TC_DROPPED,
                        f"`{mnemonic}` has no parseable (Service N, Subtype M) "
                        + ("annotation and is not in tc_catalog.yaml"
                           if not entry else
                           "annotation and the catalogue entry gives no "
                           "service/subtype")
                        + " — this annotation is DROPPED by the extractor",
                        line)
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

        elif kind == "verify":
            predicate = _parse_predicate(match.group(1))
            if predicate is None:
                continue          # prose verify — narrative, not a step
            rest = match.group("rest") or ""
            timeout = _TIMEOUT.search(rest)
            steps.append(canonical_step({
                "type": "tlm_check",
                "parameter": predicate["parameter"],
                "condition": predicate["operator"],
                "value": predicate["value"],
                "timeout_s": float(timeout.group(1)) if timeout else 30.0,
            }))

        elif kind == "label":
            steps.append(canonical_step(
                {"type": "label", "name": match.group(1)}))

        elif kind == "branch":
            predicate = _parse_predicate(match.group("pred"))
            if predicate is None:
                problem(PROBLEM_BAD_BRANCH,
                        f"**BRANCH:** predicate `{match.group('pred')}` is not "
                        f"`parameter <op> value`", line)
                continue
            rest = match.group("rest") or ""
            timeout = _TIMEOUT.search(rest)
            max_taken = _MAX_TAKEN.search(rest)
            raw = {
                "type": "branch",
                "parameter": predicate["parameter"],
                "condition": predicate["operator"],
                "value": predicate["value"],
                "timeout_s": float(timeout.group(1)) if timeout else 30.0,
                "on_true": match.group("go"),
                "on_false": match.group("nogo"),
            }
            if max_taken:
                raw["max_taken"] = int(max_taken.group(1))
            referenced.append((len(steps), match.group("go")))
            referenced.append((len(steps), match.group("nogo")))
            steps.append(canonical_step(raw))

        elif kind == "gonogo":
            body = match.group("text") or ""
            targets = _GONOGO_TARGETS.search(body)
            raw = {"type": "go_nogo",
                   "label": _GONOGO_TARGETS.sub("", body).strip(" —-").strip()
                   or "operator decision"}
            if targets:
                raw["on_go"] = targets.group("go")
                raw["on_nogo"] = targets.group("nogo")
                max_taken = _MAX_TAKEN.search(body)
                if max_taken:
                    raw["max_taken"] = int(max_taken.group(1))
                referenced.append((len(steps), targets.group("go")))
                referenced.append((len(steps), targets.group("nogo")))
            steps.append(canonical_step(raw))

    # ── Pass 2: structural checks ───────────────────────────────────
    for message in _branch_problems(steps):
        problem(PROBLEM_BAD_BRANCH, message)

    # Unstructured branching prose. Only a problem when the document does NOT
    # declare a branch structure: with `**BRANCH:**` present, the prose is the
    # human-readable narration of the machine-readable fork, which is what an
    # operations document is supposed to look like.
    if not branch_spans:
        prose = sorted({line_of(m.start()) for m in _PROSE_BRANCH.finditer(text)})
        if prose:
            problem(PROBLEM_PROSE_BRANCH,
                    f"branching written as prose on line(s) "
                    f"{', '.join(str(p) for p in prose[:6])}"
                    + (" …" if len(prose) > 6 else "")
                    + " but no **BRANCH:** structure is declared — a flat "
                      "runner would execute EVERY arm",
                    prose[0])

    # Catalogued mnemonics hiding behind `**Action:**`. This is the gate that
    # closes the root cause: the 21 inert procedures each wrote their whole
    # commanding this way, and every audit category passed vacuously.
    if catalog:
        known = {k.upper() for k in catalog}
        for match in _ACTION_LINE.finditer(text):
            rest = (match.group("rest") or "") + _action_bullets(text, match.end())
            hidden = sorted({
                token for token in re.findall(r"\b([A-Z][A-Z0-9_]{3,})\b", rest)
                if token in known})
            if hidden:
                problem(PROBLEM_ACTION_INERT,
                        f"**Action:** line names catalogued command(s) "
                        f"{', '.join(hidden)} — the extractor only reads "
                        f"**TC:**, so this commanding is INERT",
                        line_of(match.start()))

    return steps, problems


def _action_bullets(text: str, start: int) -> str:
    """The bullet block that belongs to an ``**Action:**`` line.

    An `**Action:**` paragraph continues over wrapped lines and bullets alike.
    NOM-012 wrote its whole housekeeping sweep as

        **Action:** Request full housekeeping sweep across all subsystems:
        - `HK_REQUEST(sid=1)` — EPS housekeeping
        - `HK_REQUEST(sid=2)` — AOCS housekeeping

    Only the first line carries the tag, so a check that looked at that line
    alone saw prose and no commands — the commanding is one line further down
    and just as invisible to the extractor. The block ends at the first blank
    line, heading, or line that is not a list item.
    """
    out: list[str] = []
    for line in text[start:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("**"):
            break
        out.append(stripped)
    return "\n" + "\n".join(out) if out else ""


def _branch_problems(steps: list[dict]) -> list[str]:
    """Label resolution + the bounded loop guard."""
    problems: list[str] = []
    label_index: dict[str, int] = {}
    for idx, step in enumerate(steps):
        if step.get("type") == "label":
            name = str(step.get("name") or "")
            if not name:
                problems.append(f"step {idx + 1}: label has no name")
            elif name in label_index:
                problems.append(
                    f"step {idx + 1}: label {name!r} is declared twice — a "
                    f"jump to it would be ambiguous")
            else:
                label_index[name] = idx

    for idx, step in enumerate(steps):
        kind = step.get("type")
        if kind not in ("branch", "go_nogo"):
            continue
        keys = (("on_true", "on_false") if kind == "branch"
                else ("on_go", "on_nogo"))
        targets = [step.get(k) for k in keys if step.get(k)]
        if not targets:
            continue          # a go_nogo with no targets is a plain pause
        backward = False
        for target in targets:
            if target not in label_index:
                problems.append(
                    f"step {idx + 1}: jumps to label {target!r}, which the "
                    f"document does not declare with **LABEL:**")
            elif label_index[target] <= idx:
                backward = True
        if not backward:
            continue
        # A jump to an earlier label is a loop. Unbounded, it is an infinite
        # one; a recovery card is not allowed to contain that.
        bound = step.get("max_taken")
        if bound is None:
            problems.append(
                f"step {idx + 1}: jumps BACKWARD to an earlier label with no "
                f"`max N` loop bound")
        elif not (1 <= int(bound) <= MAX_LOOP_ITERATIONS):
            problems.append(
                f"step {idx + 1}: loop bound max {bound} is outside "
                f"1..{MAX_LOOP_ITERATIONS}")
    return problems


def from_markdown(text: str, catalog: dict[str, dict] | None = None) -> list[dict]:
    """Backwards-compatible wrapper: the steps only.

    Callers that need to know whether the document was fully understood must
    use ``markdown_steps``. This function is kept because the MCS procedure
    runner and several tests call it positionally, but a document is only
    offered for execution when ``procedure_index.yaml`` marks it ``runnable:``,
    and the content gate refuses to allow that mark unless ``markdown_steps``
    reports no problems.
    """
    return markdown_steps(text, catalog)[0]


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

        if kind in ("tlm_check", "branch"):
            operator = step.get("condition") or "=="
            if operator not in CONDITION_OPERATORS:
                problems.append(f"{where}: unknown comparison {operator!r}")

        if kind == "label" and not (step.get("name") or "").strip():
            problems.append(f"{where}: label needs a name")

        if kind == "branch" and not (step.get("on_true") or step.get("on_false")):
            problems.append(f"{where}: branch needs at least one target "
                            f"('on_true' / 'on_false')")

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
    # Label resolution and the bounded loop guard are structural, so they are
    # checked once over the whole list rather than per step.
    problems.extend(_branch_problems(to_canonical(steps)))
    return problems


def _referenced_parameters(step: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if step.get("type") in ("tlm_check", "branch"):
        out.append((str(step.get("parameter") or ""), step["type"]))
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
        text = f"GO/NO-GO: {step.get('label') or 'operator decision'}"
        if step.get("on_go") or step.get("on_nogo"):
            text += (f" [GO -> {step.get('on_go') or 'next'}, "
                     f"NO-GO -> {step.get('on_nogo') or 'next'}]")
        return text

    if kind == "note":
        return f"Note: {step.get('text') or ''}".rstrip()

    if kind == "label":
        return f"Label {step.get('name') or '?'}"

    if kind == "branch":
        text = (f"If {step.get('parameter') or '?'} "
                f"{step.get('condition') or '=='} {step.get('value')} "
                f"-> {step.get('on_true') or 'next'}, else -> "
                f"{step.get('on_false') or 'next'}")
        if step.get("max_taken") is not None:
            text += f" (max {step['max_taken']}x)"
        return text

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
