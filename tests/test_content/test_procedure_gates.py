"""The 2026-08b Phase 5 validation gates — ruling R4, "add the validation gate".

Twenty-one shipped procedures were INERT: every telecommand in them was written
as ``**Action:**`` prose, which the canonical extractor drops, so they produced
zero executable steps while `procedure_index.yaml` declared a non-empty
`command_services` for each. One of them, EMG-006 *Thermal Runaway*, is an
EMERGENCY card.

None of the existing gates could see it. ``tools/procedure_audit.py``'s
``RE_TC_LINE`` matched only ``**TC:**``, so a document with no ``**TC:**`` line
had nothing to check and passed every category VACUOUSLY — the 21 worst
documents in the tree were precisely the ones the gate was blind to.

Each test below is one invariant, and each one FAILED on the pre-fix tree. The
counts in the docstrings are the measured "before" numbers, so a future reader
can tell a gate that bites from a gate that is decorative.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
PROC_DIR = CONFIG / "procedures"
FDIR_PROC_DIR = CONFIG / "fdir" / "procedures"

for _pkg in ("smo-common", "smo-simulator"):
    _src = REPO / "packages" / _pkg / "src"
    if _src.exists() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from smo_common.procedures import (  # noqa: E402
    markdown_steps, mnemonic_head, validate_steps,
)
from smo_simulator.fdir import ProcedureExecutor  # noqa: E402


# ── Shared fixtures ─────────────────────────────────────────────────

def _catalog() -> dict[str, dict]:
    data = yaml.safe_load((CONFIG / "commands" / "tc_catalog.yaml").read_text())
    out: dict[str, dict] = {}
    for cmd in data["commands"]:
        out[cmd["name"].upper()] = cmd
        for key in ("aliases", "short_names"):
            for alias in cmd.get(key, []) or []:
                out.setdefault(str(alias).upper(), cmd)
    return out


def _index() -> list[dict]:
    return yaml.safe_load(
        (PROC_DIR / "procedure_index.yaml").read_text())["procedures"]


CATALOG = _catalog()
INDEX = _index()
BY_ID = {e["id"]: e for e in INDEX}
DOCS = sorted(PROC_DIR.rglob("*.md"))


def _steps(entry: dict) -> list[dict]:
    text = (PROC_DIR / entry["file"]).read_text(encoding="utf-8")
    return markdown_steps(text, CATALOG)[0]


def _commands(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("type") == "command"]


def _is_advisory(entry: dict) -> bool:
    """A card that DECLARES it commands nothing.

    Encoded as ``runnable: {kind: advisory, reason: ...}`` rather than the
    ``advisory: true`` / ``advisory_reason:`` pair the ruling names, because
    ``ProcedureIndexEntry`` is a ``StrictModel`` in
    ``smo_common/config/schemas.py`` — not a Phase-5-owned file — and would
    reject the two new keys. The information is identical, the field is already
    in the schema, and ``smo_mcs.server._resolve_runnable`` already treats an
    unknown ``kind`` as not-runnable. See the phase report.
    """
    return (entry.get("runnable") or {}).get("kind") == "advisory"


@pytest.fixture(scope="module")
def registered_executor():
    """A ProcedureExecutor with the shipped FDIR cards and the engine's one
    registered callback, exactly as ``_wire_procedure_executor`` builds it."""
    ex = ProcedureExecutor()
    ex.register_command_callback("s8_command", lambda params: None)
    for path in sorted(FDIR_PROC_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        proc_id = cfg.get("procedure_id")
        assert proc_id, f"{path.name} has no procedure_id"
        ex.register_procedure(proc_id, cfg)
    return ex


# ── Gate 1 ──────────────────────────────────────────────────────────

def test_every_fdir_recovery_procedure_has_a_registered_command(
        registered_executor):
    """GATE 1 — every FDIR recovery card has >= 1 step with a registered command.

    This is the gate that would have caught the executor's false-success
    defect from the content side: a card with ``steps: []``, no ``steps`` key,
    a step with no ``command``, or a step naming a command the engine never
    registered, all used to report PROCEDURE_COMPLETED at 100 % progress.
    """
    bad: list[str] = []
    for proc_id in registered_executor.registered_procedures():
        problems = registered_executor.validate_procedure(proc_id)
        if problems:
            bad.append(f"{proc_id}: {'; '.join(problems)}")
    assert not bad, "FDIR recovery procedures that cannot command:\n  " + \
        "\n  ".join(bad)


def test_no_fdir_step_names_an_unregistered_command(registered_executor):
    """GATE 3 — no step names a command with no registered callback.

    ``s8_command`` is the ONLY callback ``EOSat1Engine._wire_procedure_executor``
    registers, so any other value is a step that silently sends nothing.
    """
    bad: list[str] = []
    for path in sorted(FDIR_PROC_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        for idx, step in enumerate(cfg.get("steps") or [], start=1):
            cmd = step.get("command")
            if cmd != "s8_command":
                bad.append(f"{path.name} step {idx}: command={cmd!r}")
    assert not bad, "steps naming an unregistered command:\n  " + \
        "\n  ".join(bad)


def test_every_fdir_step_func_id_is_catalogued():
    """A step's ``func_id`` must be a real S8 function.

    ``ttc_emergency_shutdown`` sent func 66 (TTC_PA_**ON**) where 67 (PA OFF)
    was meant — the level-3 emergency shutdown turned the transmitter ON.
    """
    known = {int(c["func_id"]) for c in CATALOG.values()
             if c.get("service") == 8 and c.get("func_id") is not None}
    bad: list[str] = []
    for path in sorted(FDIR_PROC_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text()) or {}
        for idx, step in enumerate(cfg.get("steps") or [], start=1):
            func = (step.get("params") or {}).get("func_id")
            if func is None:
                bad.append(f"{path.name} step {idx}: no func_id")
            elif int(func) not in known:
                bad.append(f"{path.name} step {idx}: func {func} is not an "
                           f"S8 function in tc_catalog.yaml")
    assert not bad, "\n  ".join(bad)


# ── Gate 2 ──────────────────────────────────────────────────────────

def test_every_recovery_action_resolves_to_a_commanding_procedure(
        registered_executor):
    """GATE 2 — every ``recovery_actions`` reference resolves and commands.

    ``EOSat1Engine._enter_recovery_level`` strips the ``.yaml`` and looks the
    stem up by ``procedure_id``. A reference that does not resolve produces a
    0x0F03 "unknown procedure" and the fault escalates with nothing sent.
    """
    fp = yaml.safe_load(
        (CONFIG / "fdir" / "fault_propagation.yaml").read_text()) or {}
    groups = ((fp.get("recovery_escalation") or {}).get("recovery_actions")
              or fp.get("recovery_actions") or {})
    assert groups, "fault_propagation.yaml declares no recovery_actions"

    bad: list[str] = []
    seen = 0
    for group_name, levels in groups.items():
        for level_name, cfg in (levels or {}).items():
            ref = str((cfg or {}).get("procedure", ""))
            proc_id = ref[:-5] if ref.endswith(".yaml") else ref
            seen += 1
            if not proc_id:
                bad.append(f"{group_name}.{level_name}: no procedure")
                continue
            problems = registered_executor.validate_procedure(proc_id)
            if problems:
                bad.append(f"{group_name}.{level_name} -> {proc_id}: "
                           f"{'; '.join(problems)}")
    assert seen >= 9, f"only {seen} recovery levels found"
    assert not bad, "recovery_actions that do not resolve to a commanding " \
        "procedure:\n  " + "\n  ".join(bad)


def test_every_rule_recovery_group_exists():
    """A propagation rule naming a recovery group that does not exist means
    ``_start_recovery`` returns immediately and NOTHING happens on the fault."""
    fp = yaml.safe_load(
        (CONFIG / "fdir" / "fault_propagation.yaml").read_text()) or {}
    groups = set((fp.get("recovery_escalation") or {}).get(
        "recovery_actions") or {})
    rules = fp.get("fault_cascades") or []
    assert rules, "fault_propagation.yaml declares no fault_cascades"
    bad = [f"{r.get('fault_id')} -> {r.get('recovery_action')}" for r in rules
           if r.get("recovery_action") and r["recovery_action"] not in groups]
    assert not bad, f"rules naming an unknown recovery group: {bad}"


# ── Gate 4 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "entry", [e for e in INDEX if e.get("runnable") and not _is_advisory(e)],
    ids=[e["id"] for e in INDEX if e.get("runnable") and not _is_advisory(e)])
def test_a_runnable_link_resolves_to_at_least_one_command(entry):
    """GATE 4 — a ``runnable:`` link must resolve to >= 1 command step.

    A link that resolves to an empty list is the ground-side twin of the
    executor defect: the MCS offers the card, the runner completes it
    instantly, and nothing was sent.
    """
    link = entry["runnable"]
    kind = link["kind"]
    if kind == "activity_type":
        acts = {a["name"]: a for a in yaml.safe_load(
            (CONFIG / "planning" / "activity_types.yaml").read_text())
            ["activity_types"]}
        act = acts.get(link["ref"])
        assert act is not None, f"{entry['id']} -> unknown activity {link['ref']}"
        from smo_common.procedures import from_activity_sequence
        steps = from_activity_sequence(act.get("command_sequence", []))
    elif kind == "steps_file":
        target = PROC_DIR / link["ref"]
        assert target.exists(), f"{entry['id']} -> missing {link['ref']}"
        steps = (yaml.safe_load(target.read_text()) or {}).get("steps", [])
        from smo_common.procedures import to_canonical
        steps = to_canonical(steps)
    else:
        steps = _steps(entry)

    commands = _commands(steps)
    assert commands, (
        f"{entry['id']} is marked runnable but resolves to ZERO command steps")
    assert validate_steps(steps, None) == [], entry["id"]


@pytest.mark.parametrize(
    "entry",
    [e for e in INDEX if (e.get("runnable") or {}).get("kind") == "markdown"],
    ids=[e["id"] for e in INDEX
         if (e.get("runnable") or {}).get("kind") == "markdown"])
def test_a_markdown_runnable_document_is_fully_understood(entry):
    """GATE 4b — ``kind: markdown`` requires a document with NO problems.

    In particular it requires that branching is declared with ``**BRANCH:**``
    or ``**GO/NO-GO:** ... -> GO `X` | NO-GO `Y``` rather than written as
    "proceed to Step 3" prose. That is the check which stops EMG-006 being
    flattened into "shed the heater power line unconditionally".
    """
    text = (PROC_DIR / entry["file"]).read_text(encoding="utf-8")
    _, problems = markdown_steps(text, CATALOG)
    blocking = [str(p) for p in problems if p.blocks_runnable]
    assert not blocking, (
        f"{entry['id']} is marked `runnable: {{kind: markdown}}` but the "
        f"extractor does not fully understand it:\n  " + "\n  ".join(blocking))


# ── Gate 5 — the root cause ─────────────────────────────────────────

@pytest.mark.parametrize("doc", DOCS, ids=[d.stem for d in DOCS])
def test_no_catalogued_command_hides_inside_an_action_line(doc):
    """GATE 5 — no catalogued mnemonic may live in an ``**Action:**`` block.

    THIS is the gate that closes the root cause. The extractor reads
    ``**TC:**`` and nothing else, so a command written behind ``**Action:**``
    is invisible to the runner, to the MCS and to every audit category.

    Before the fix: 21 documents were made ENTIRELY of such lines, and a
    further 14 healthy documents each hid one or more commands this way
    (including three whole housekeeping sweeps written as ``**Action:**``
    bullet lists, which not even the first version of this check could see).
    """
    text = doc.read_text(encoding="utf-8")
    _, problems = markdown_steps(text, CATALOG)
    hidden = [str(p) for p in problems if p.category == "ACTION_INERT"]
    assert not hidden, (
        f"{doc.relative_to(PROC_DIR)} commands from **Action:** prose:\n  "
        + "\n  ".join(hidden))


# ── Gate 6 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc", DOCS, ids=[d.stem for d in DOCS])
def test_every_tc_annotation_survives_extraction(doc):
    """GATE 6 — every ``**TC:**`` annotation must produce a step.

    Before the fix, 15 annotations in the shipped tree were silently dropped:
    13 whose Service/Subtype could not be parsed (``schema.py`` used to
    ``continue`` past them) and 2 written as prose with no backtick mnemonic.
    Four of the 13 were in COM-013, which meant that card was 80 % inert while
    looking, to every gate, entirely healthy.
    """
    text = doc.read_text(encoding="utf-8")
    _, problems = markdown_steps(text, CATALOG)
    dropped = [str(p) for p in problems if p.category == "TC_DROPPED"]
    assert not dropped, (
        f"{doc.relative_to(PROC_DIR)} has **TC:** annotations that do not "
        f"survive extraction:\n  " + "\n  ".join(dropped))


@pytest.mark.parametrize("doc", DOCS, ids=[d.stem for d in DOCS])
def test_branch_structure_is_well_formed(doc):
    """Every declared branch target resolves, and every backward jump is
    bounded. An unbounded backward jump is an infinite loop in a document an
    operator is asked to follow."""
    text = doc.read_text(encoding="utf-8")
    _, problems = markdown_steps(text, CATALOG)
    bad = [str(p) for p in problems if p.category == "BAD_BRANCH"]
    assert not bad, f"{doc.relative_to(PROC_DIR)}:\n  " + "\n  ".join(bad)


# ── Gate 7 — the defect stated as an invariant ──────────────────────

@pytest.mark.parametrize("entry", INDEX, ids=[e["id"] for e in INDEX])
def test_command_services_matches_the_extracted_steps(entry):
    """GATE 7 — ``command_services`` must equal the services actually extracted,
    and must be EMPTY for an advisory card.

    This single assertion is the whole defect stated as an invariant, and it
    failed on 61 of the 67 entries before the fix — including all 20
    non-advisory inert cards, each of which declared a non-empty
    ``command_services`` while yielding zero steps. CTG-001 declared
    ``[3, 5, 8, 20]`` and produced nothing at all.

    ``command_services`` is therefore DERIVED, not editorial. An advisory card
    declares that it commands nothing, and the emptiness is then a stated
    property rather than an accident.
    """
    steps = _steps(entry)
    extracted = sorted({s["service"] for s in _commands(steps)
                        if s.get("service") is not None})
    declared = sorted(entry.get("command_services") or [])
    advisory = _is_advisory(entry)

    if advisory:
        assert not declared, (
            f"{entry['id']} is advisory but declares "
            f"command_services: {declared}")
        assert not extracted, (
            f"{entry['id']} is advisory but extracts commands on services "
            f"{extracted} — it is not advisory")
        assert str((entry.get("runnable") or {}).get("reason", "")).strip(), (
            f"{entry['id']} is advisory with no reason. The whole point of the "
            f"declaration is that 'zero commands' becomes a DECLARED property "
            f"with a stated reason.")
        return

    assert extracted, (
        f"{entry['id']} ({entry['file']}) extracts ZERO command steps. Either "
        f"its commanding is still written as **Action:** prose, or it really "
        f"is a document — in which case mark it `advisory: true` with an "
        f"`advisory_reason`.")
    assert declared == extracted, (
        f"{entry['id']} declares command_services {declared} but its document "
        f"extracts {extracted}. This field is derived from the document, not "
        f"written by hand.")


def test_at_least_one_advisory_card_exists_and_is_justified():
    """A guard on the guard: if `advisory` ever stops being used, the
    zero-command assertion above silently becomes unreachable for the one
    legitimate case, and the next genuinely inert card gets marked advisory
    instead of fixed."""
    advisory = [e for e in INDEX if _is_advisory(e)]
    assert advisory, "no advisory cards — has the flag been dropped?"
    assert len(advisory) <= 3, (
        f"{len(advisory)} advisory cards. `advisory: true` is an exemption "
        f"from the zero-command gate; a growing list means cards are being "
        f"exempted rather than fixed: {[e['id'] for e in advisory]}")


# ── Gate 8 — every extracted command exists and agrees ──────────────

@pytest.mark.parametrize("doc", DOCS, ids=[d.stem for d in DOCS])
def test_every_extracted_command_is_catalogued_and_agrees(doc):
    """A step's mnemonic, service, subtype and func_id must all agree with
    ``tc_catalog.yaml``. The emergency cards carried an invented scheme in
    which the S8 SUBTYPE named the subsystem (3 OBC, 4 AOCS, 5 payload,
    7 heaters); nothing dispatches those, so every one of those commands would
    have been rejected on the wire."""
    steps, _ = markdown_steps(doc.read_text(encoding="utf-8"), CATALOG)
    bad: list[str] = []
    for step in _commands(steps):
        head = mnemonic_head(step.get("mnemonic", ""))
        entry = CATALOG.get(head)
        if entry is None:
            bad.append(f"{head or step.get('mnemonic')!r} is not catalogued")
            continue
        if step.get("service") != entry.get("service"):
            bad.append(f"{head}: annotated Service {step.get('service')}, "
                       f"catalogue says {entry.get('service')}")
        if step.get("subtype") != entry.get("subtype"):
            bad.append(f"{head}: annotated Subtype {step.get('subtype')}, "
                       f"catalogue says {entry.get('subtype')}")
        want_func = entry.get("func_id")
        if want_func is not None and step.get("func_id") is not None \
                and int(step["func_id"]) != int(want_func):
            bad.append(f"{head}: annotated func_id {step['func_id']}, "
                       f"catalogue says {want_func}")
    assert not bad, f"{doc.relative_to(PROC_DIR)}:\n  " + "\n  ".join(bad)


# ── Gate 9 — arguments must be INSIDE the backticks ─────────────────

_ARG_PLACEHOLDERS = {"n", "adr", "addr", "len", "block", "t", "tc", "dv",
                     "dir", "mode", "x", "k", "t_corrected", "seg", "s0"}


@pytest.mark.parametrize("doc", DOCS, ids=[d.stem for d in DOCS])
def test_every_extracted_command_carries_its_arguments(doc):
    """GATE 9 — a command with catalogue fields must be written with them.

    ``**TC:** `TTC_SET_DATA_RATE` rate=1 (Service 8, ...)`` puts the argument
    OUTSIDE the backticks, so the extractor produces a step with no argument
    and the wire packs the field as zero. Measured on a live engine:
    ``HK_REQUEST`` written that way returns ZERO telemetry packets, so every
    ``**Verify:**`` beneath it waits on a report that was never generated.

    Before the fix, 63 extracted commands were argument-less or carried
    argument names that do not exist on the command (``blue=`` for
    ``blue_ms=``, ``line=`` for ``line_index=``, ``select=`` for ``unit=``).
    """
    steps, _ = markdown_steps(doc.read_text(encoding="utf-8"), CATALOG)
    bad: list[str] = []
    for step in _commands(steps):
        mnemonic = step.get("mnemonic", "")
        head = mnemonic_head(mnemonic)
        entry = CATALOG.get(head)
        if entry is None:
            continue
        want = [f["name"] for f in (entry.get("fields") or [])]
        if not want:
            continue
        if "(" not in mnemonic:
            bad.append(f"{head} takes {want} but is written with no arguments "
                       f"— any argument after the closing backtick is INVISIBLE "
                       f"to the extractor and packs as zero")
            continue
        inner = mnemonic[mnemonic.find("(") + 1:mnemonic.rfind(")")]
        given = {part.split("=", 1)[0].strip()
                 for part in inner.split(",") if "=" in part}
        unknown = sorted(given - set(want))
        if unknown:
            bad.append(f"{head} is given {unknown}, but its catalogue fields "
                       f"are {want}")
        missing = sorted(set(want) - given)
        if missing:
            bad.append(f"{head} is missing argument(s) {missing}")
    assert not bad, f"{doc.relative_to(PROC_DIR)}:\n  " + "\n  ".join(bad)


# ── Guard the guards ────────────────────────────────────────────────
#
# Every gate above is only worth having if it FIRES on a known-bad input.
# These tests build the defect deliberately and assert the check catches it.

_TC = ("**TC:** `PAYLOAD_SET_MODE(mode=0)` "
       "(Service 8, Subtype 1, func_id 26)\n")


def test_gate_5_fires_on_a_command_behind_an_action_line():
    _, problems = markdown_steps(
        "**Action:** Command payload off: `PAYLOAD_SET_MODE(mode=0)`\n",
        CATALOG)
    assert any(p.category == "ACTION_INERT" for p in problems)


def test_gate_5_fires_on_a_command_in_an_action_bullet_block():
    """NOM-012's whole housekeeping sweep lived one line below the tag."""
    _, problems = markdown_steps(
        "**Action:** Request housekeeping:\n"
        "- `HK_REQUEST(sid=1)` — EPS\n"
        "- `HK_REQUEST(sid=2)` — AOCS\n",
        CATALOG)
    assert any(p.category == "ACTION_INERT" for p in problems)


def test_gate_6_fires_on_an_unparseable_tc_annotation():
    _, problems = markdown_steps(
        "**TC:** `NOT_IN_THE_CATALOGUE(x=1)` — no annotation\n", CATALOG)
    assert any(p.category == "TC_DROPPED" for p in problems)


def test_gate_6_fires_on_a_tc_line_with_no_mnemonic():
    _, problems = markdown_steps(
        "**TC:** Alternate between the two transponders on successive passes.\n",
        CATALOG)
    assert any(p.category == "TC_DROPPED" for p in problems)


def test_prose_branching_blocks_runnability():
    _, problems = markdown_steps(
        _TC + "**GO/NO-GO:** if the heater turned off, skip to Step 5\n",
        CATALOG)
    prose = [p for p in problems if p.category == "PROSE_BRANCH"]
    assert prose and prose[0].blocks_runnable
    # ...but it is NOT an extraction defect: the document is still a document.
    assert not prose[0].blocks_extraction


def test_a_declared_branch_structure_does_not_trip_the_prose_check():
    steps, problems = markdown_steps(
        "**LABEL:** `STEP-1`\n" + _TC +
        "**BRANCH:** `payload.mode == 0` -> GO `STEP-2` | NO-GO `STEP-1` "
        "(timeout 30 s, max 2)\n"
        "**LABEL:** `STEP-2`\n"
        "**Note:** proceed to Step 2\n",
        CATALOG)
    assert not problems, [str(p) for p in problems]
    assert any(s["type"] == "branch" for s in steps)


def test_an_unbounded_backward_jump_is_refused():
    _, problems = markdown_steps(
        "**LABEL:** `STEP-1`\n" + _TC +
        "**BRANCH:** `payload.mode == 0` -> GO `STEP-1` | NO-GO `STEP-1`\n",
        CATALOG)
    assert any(p.category == "BAD_BRANCH" and "BACKWARD" in p.message
               for p in problems)


def test_a_jump_to_an_undeclared_label_is_refused():
    _, problems = markdown_steps(
        _TC + "**BRANCH:** `payload.mode == 0` -> GO `NOWHERE` | NO-GO `ALSO-NOWHERE`\n",
        CATALOG)
    assert sum(1 for p in problems
               if p.category == "BAD_BRANCH" and "does not declare" in p.message) == 2


def test_a_loop_bound_above_the_ceiling_is_refused():
    from smo_common.procedures import MAX_LOOP_ITERATIONS
    _, problems = markdown_steps(
        "**LABEL:** `STEP-1`\n" + _TC +
        f"**BRANCH:** `payload.mode == 0` -> GO `STEP-1` | NO-GO `STEP-1` "
        f"(max {MAX_LOOP_ITERATIONS + 1})\n",
        CATALOG)
    assert any(p.category == "BAD_BRANCH" and "outside" in p.message
               for p in problems)


def test_gate_1_fires_on_each_of_the_four_false_success_shapes():
    """The four ways ``ProcedureExecutor`` used to report success having sent
    nothing. Empirically verified on the pre-fix tree: all four produced
    0x0F07 then 0x0F08 at progress_pct 100.0."""
    ex = ProcedureExecutor()
    ex.register_command_callback("s8_command", lambda p: None)
    cases = {
        "empty steps": {"procedure_id": "p", "steps": []},
        "no steps key": {"procedure_id": "p", "name": "P"},
        "unregistered command": {"steps": [{"command": "nope", "params": {}}]},
        "no command key": {"steps": [{"name": "x", "params": {}}]},
    }
    for label, cfg in cases.items():
        ex.register_procedure("p", cfg)
        assert ex.validate_procedure("p"), f"{label}: gate did not fire"
    ex.register_procedure(
        "p", {"steps": [{"command": "s8_command",
                         "params": {"func_id": 0, "data": [3]}}]})
    assert ex.validate_procedure("p") == [], "the gate rejects a healthy card"


def test_the_gate_sees_every_document_on_disk():
    """A gate that silently stops looking at files is worse than no gate."""
    assert len(DOCS) >= 60, f"only {len(DOCS)} documents found"
    indexed = {e["file"] for e in INDEX}
    on_disk = {d.relative_to(PROC_DIR).as_posix() for d in DOCS}
    assert indexed == on_disk, (
        f"procedure_index.yaml and the tree disagree: "
        f"indexed-not-on-disk={sorted(indexed - on_disk)}, "
        f"on-disk-not-indexed={sorted(on_disk - indexed)}")
