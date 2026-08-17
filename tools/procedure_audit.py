#!/usr/bin/env python3
"""procedure_audit.py — Static walkthrough auditor for EOSAT-1 procedures.

For every procedure markdown file under ``configs/eosat1/procedures/``, this
tool extracts:

* **TC references** — ``**TC:**`` lines and the command token immediately
  following (e.g. ``SET_AOCS_MODE``), plus any bare ``(Service N)`` annotation.
* **TM parameter references** — parenthesised hex IDs of the form ``(0x0123)``
  and dotted parameter names like ``eps.bat_voltage``.

Each reference is then cross-checked:

1. Command token must resolve to an entry in ``tc_catalog.yaml``
   (matched either by ``name`` or by a ``short_name``/``alias`` field).
2. Parameter hex ID or dotted name must resolve to an entry in
   ``telemetry/parameters.yaml``.
3. Parameter must be reachable by the ground segment:
   * included in at least one HK structure (``hk_structures.yaml``), OR
   * marked as ``on_demand: true`` in ``parameters.yaml`` (served via S2/S8).

The result is an HTML + JSON findings report written to the workspace folder
(``workspace/audit/procedure_audit.html`` and ``.json``) summarising:

* procedures scanned
* TC references OK / unresolved
* TM references OK / unresolved / not-in-HK-and-not-on-demand
* per-procedure drill-down tables

Run::

    python tools/procedure_audit.py

The tool is intentionally read-only — it never modifies procedures or catalogs.
Findings are meant to drive follow-up fixes in the appropriate place (catalog
entry, procedure wording, or HK structure definition).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

try:
    import yaml
except ImportError:  # pragma: no cover
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "configs" / "eosat1"
PROC_DIR = CFG / "procedures"
TC_CATALOG = CFG / "commands" / "tc_catalog.yaml"
PARAMS = CFG / "telemetry" / "parameters.yaml"
HK = CFG / "telemetry" / "hk_structures.yaml"
EVENTS = CFG / "events" / "event_catalog.yaml"
INDEX = PROC_DIR / "procedure_index.yaml"

OUT_DIR = REPO / "workspace" / "audit"

# The canonical extractor. The auditor used to have its own idea of what a
# procedure "contains", which is how a document with ZERO `**TC:**` lines
# passed every category vacuously: nothing to check meant nothing wrong. It now
# asks `smo_common.procedures` the same question the procedure runner asks, so
# "this document commands nothing" is a finding rather than a clean sheet.
_COMMON_SRC = REPO / "packages" / "smo-common" / "src"
if str(_COMMON_SRC) not in sys.path:
    sys.path.insert(0, str(_COMMON_SRC))
from smo_common.procedures import markdown_steps, mnemonic_head  # noqa: E402

# ---------- regex ----------
RE_TC_LINE = re.compile(r"\*\*TC:\*\*\s*`?([A-Z][A-Z0-9_]+)", re.IGNORECASE)

#: `**Action:** ... `EPS_POWER_OFF(line_index=5)` ...` — a catalogued command
#: written where the extractor cannot see it. THIS is the root cause of the 21
#: inert procedures: every one of them wrote its whole commanding this way, and
#: because `RE_TC_LINE` only matches `**TC:**`, the 21 worst documents in the
#: tree were the ones the gate could not see at all.
RE_ACTION_LINE = re.compile(r"(?m)^[ \t]*(?:[-*+]\s+|>\s*)?\*\*Action:?\*\*:?([^\n]*)")
RE_BARE_MNEMONIC = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")

#: `eps.bat_soc` (0x0101) — the NAME and the ID must agree. Both halves are
#: individually valid, so neither the TC check nor the parameter check could
#: ever notice `tcs.htr_thruster` (0x040D) when 0x040D is `ttc.pa_backup_temp`.
RE_NAME_WITH_ID = re.compile(
    r"`?\b([a-z]{2,8}\.[a-z][a-z0-9_]*)`?\s*\(\s*(0x[0-9A-Fa-f]{3,4})\s*\)")

#: `HK_REQUEST(sid=N)` and the `**Verify:**` lines in the same step.
RE_HK_REQUEST = re.compile(r"HK_REQUEST\(sid=(\d+)\)")
RE_VERIFY_PARAM = re.compile(r"`([a-z]{2,8}\.[a-z][a-z0-9_]*)`")
RE_STEP_BREAK = re.compile(r"(?m)^#{2,}\s")

# Generic service-family labels that aren't specific TCs and should not be
# flagged as unknown commands.
GENERIC_TC_LABELS = {
    "FUNC_PERFORM",   # S8.1 generic function-perform — specific func_id follows
    "FUNCTION_PERFORM",
    "ALTERNATE",      # procedural keywords, not TCs
    "CONTINUE",
    "GOTO",
    "LOOP",
}
RE_HEX_PARAM = re.compile(r"\(\s*(0x[0-9A-Fa-f]{3,4})\s*\)")
RE_DOTTED = re.compile(r"\b([a-z]{2,8}\.[a-z][a-z0-9_]+)\b")
RE_SERVICE = re.compile(r"\(\s*Service\s+(\d+)\s*\)")

# "PROP_BURN_START (0x0803)" / "event PROP_ARMED (0x0800)". Procedures cite
# event ids in the same parenthesised-hex shape as parameter ids (G3-e).
RE_EVENT_NAMED = re.compile(
    r"\b([A-Z][A-Z0-9_]{3,})\s*\(\s*(0x[0-9A-Fa-f]{3,4})\s*\)")

# "GET_PARAM ... Subtype 1". The S20 pair was inverted across the payload and
# AOCS procedures. THIS suite's S20 is `service_dispatch._handle_s20`:
#   subtype 1 = SET parameter (param_id + float32 value)
#   subtype 3 = GET parameter (param_id -> S20.2 value report)
# which is a documented deviation from ECSS-E-ST-70-41 (where 20,1 is the
# report *request* and 20,3 is the set) — see docs/PUS_SERVICE_REFERENCE.md
# Service 20. The procedures must match the wire, not the standard, because
# it is the wire the student's command reaches.
RE_S20_SUBTYPE = re.compile(
    r"`?(GET_PARAM|SET_PARAM)`?[^\n]{0,120}?[Ss]ubtype\s+(\d+)")
S20_SUBTYPE = {"GET_PARAM": 3, "SET_PARAM": 1}

# Phase E (brief 04 drift gate 4): a procedure that names a mnemonic and a
# func_id must agree with tc_catalog.yaml. This is the check that would have
# flagged corrupted_image.md's "PAYLOAD_CAPTURE ... func_id 22" and
# battery_cell_failure.md's "HEATER_SET_SETPOINT ... func 34" — 19 procedure
# files carried a whole stale numbering scheme in which every S8 annotation
# named the wrong function.
#
# 2026-08b: the closing `\)` was preceded by a literal `\(`, so this only ever
# matched a bare `(func 43)` and NOT the canonical annotation shape
# `(Service 8, Subtype 1, func_id 36)` — which is how LEOP-007 could annotate
# SET_FPA_COOLER (an alias of EPS_FPA_COOLER, func 17) with func_id 36
# (PAYLOAD_COOLER_SETPOINT, a uint16 centi-kelvin setpoint) and pass the gate.
# The `(` is now optional and `func_id` may be preceded by a comma.
RE_FUNC_ANNOT = re.compile(
    r"`?([A-Z][A-Z0-9_]{2,})(?:\([^`\n]*\))?`?[^\n]{0,80}?"
    r"[(,]\s*func(?:_id)?\s*[= ]\s*(\d+)\s*[),]")
RE_FUNC_TABLE_ROW = re.compile(
    r"^\|\s*([A-Z][A-Z0-9_]{2,})\s*\|\s*8\s*\|\s*1\s*\|\s*(\d+)\s*\|", re.M)


# ---------- catalog loading ----------
@dataclass
class Catalog:
    tc_names: set[str] = field(default_factory=set)
    tc_services: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: mnemonic (and alias) -> S8.1 func_id, for the annotation cross-check
    #: added in Phase E (brief 04 drift gate 4).
    tc_func_ids: dict[str, int] = field(default_factory=dict)
    param_by_id: dict[int, dict] = field(default_factory=dict)
    param_by_name: dict[str, dict] = field(default_factory=dict)
    hk_param_ids: set[int] = field(default_factory=set)
    hk_sids_for_param: dict[int, list[int]] = field(default_factory=dict)
    #: SID -> the parameter ids that structure actually carries. Needed for the
    #: HK_SID_MISMATCH check: a step that requests SID 5 and then verifies
    #: `ttc.link_status` is asking for a reading the packet does not contain.
    hk_params_by_sid: dict[int, set[int]] = field(default_factory=dict)
    on_demand_ids: set[int] = field(default_factory=set)
    #: mnemonic -> the full tc_catalog entry, for the extractor.
    tc_entries: dict[str, dict] = field(default_factory=dict)
    #: procedure id -> index entry, for the advisory / command_services checks.
    index_by_file: dict[str, dict] = field(default_factory=dict)
    #: Event id -> name. A procedure legitimately cites event ids in the same
    #: `(0x0803)` shape as parameter ids — the auditor used to report all nine
    #: Phase D propulsion event references as PARAM_UNKNOWN, which is a false
    #: positive that costs more attention than the defects it hides (G3-e).
    event_by_id: dict[int, str] = field(default_factory=dict)
    event_by_name: dict[str, int] = field(default_factory=dict)

    def load(self) -> None:
        with TC_CATALOG.open() as f:
            tc = yaml.safe_load(f) or {}
        for cmd in tc.get("commands", []) or []:
            name = str(cmd.get("name", "")).strip()
            if not name:
                continue
            self.tc_names.add(name.upper())
            self.tc_entries[name.upper()] = cmd
            try:
                self.tc_services[name.upper()] = (
                    int(cmd.get("service", 0)), int(cmd.get("subtype", 0)))
            except Exception:
                pass
            if cmd.get("func_id") is not None:
                try:
                    self.tc_func_ids[name.upper()] = int(cmd["func_id"])
                except Exception:
                    pass
            for alias_key in ("aliases", "short_names"):
                for a in cmd.get(alias_key, []) or []:
                    self.tc_names.add(str(a).upper())
                    self.tc_entries.setdefault(str(a).upper(), cmd)
                    if cmd.get("func_id") is not None:
                        self.tc_func_ids.setdefault(str(a).upper(),
                                                    int(cmd["func_id"]))

        with PARAMS.open() as f:
            pdata = yaml.safe_load(f) or {}
        for p in pdata.get("parameters", []) or []:
            try:
                pid = int(p["id"]) if isinstance(p["id"], int) else int(str(p["id"]), 0)
            except Exception:
                continue
            self.param_by_id[pid] = p
            nm = str(p.get("name", "")).strip().lower()
            if nm:
                self.param_by_name[nm] = p
            if bool(p.get("on_demand", False)):
                self.on_demand_ids.add(pid)

        if EVENTS.exists():
            with EVENTS.open() as f:
                edata = yaml.safe_load(f) or {}
            for e in edata.get("events", []) or []:
                try:
                    eid = int(e["id"]) if isinstance(e["id"], int) \
                        else int(str(e["id"]), 0)
                except Exception:
                    continue
                nm = str(e.get("name", "")).strip().upper()
                self.event_by_id[eid] = nm
                if nm:
                    self.event_by_name[nm] = eid

        with HK.open() as f:
            hk = yaml.safe_load(f) or {}
        for s in hk.get("structures", []) or []:
            sid = int(s.get("sid", 0))
            for pp in s.get("parameters", []) or []:
                pid_raw = pp.get("param_id")
                try:
                    pid = int(pid_raw) if isinstance(pid_raw, int) else int(str(pid_raw), 0)
                except Exception:
                    continue
                self.hk_param_ids.add(pid)
                self.hk_sids_for_param.setdefault(pid, []).append(sid)
                self.hk_params_by_sid.setdefault(sid, set()).add(pid)

        if INDEX.exists():
            with INDEX.open() as f:
                idx = yaml.safe_load(f) or {}
            for entry in idx.get("procedures", []) or []:
                if entry.get("file"):
                    self.index_by_file[str(entry["file"])] = entry


# ---------- audit ----------
@dataclass
class Finding:
    procedure: str
    #: TC_UNKNOWN, FUNC_ID_MISMATCH, PARAM_UNKNOWN, PARAM_NOT_REACHABLE,
    #: EVENT_NAME_MISMATCH, SUBTYPE_INVERTED, ACTION_COMMAND,
    #: TC_NOT_EXTRACTED, PARAM_NAME_ID_MISMATCH, HK_SID_MISMATCH,
    #: ZERO_COMMAND_PROCEDURE
    category: str
    detail: str
    context: str = ""


@dataclass
class ProcedureReport:
    rel_path: str
    category: str
    tc_refs: list[str] = field(default_factory=list)
    param_refs: list[str] = field(default_factory=list)
    tc_unknown: list[str] = field(default_factory=list)
    func_id_mismatch: list[str] = field(default_factory=list)
    param_unknown: list[str] = field(default_factory=list)
    param_not_reachable: list[str] = field(default_factory=list)
    event_refs: list[str] = field(default_factory=list)
    event_mismatch: list[str] = field(default_factory=list)
    subtype_inverted: list[str] = field(default_factory=list)
    #: 2026-08b Phase 5 additions.
    action_command: list[str] = field(default_factory=list)
    tc_not_extracted: list[str] = field(default_factory=list)
    param_name_id_mismatch: list[str] = field(default_factory=list)
    hk_sid_mismatch: list[str] = field(default_factory=list)
    zero_command: list[str] = field(default_factory=list)
    #: How many executable command steps the canonical extractor gets out of
    #: this document. The number the old auditor never asked for.
    command_steps: int = 0
    advisory: bool = False
    indexed: bool = True


def _action_bullet_block(text: str, start: int) -> str:
    """The bullet list that belongs to an ``**Action:**`` line (see the
    matching helper in ``smo_common.procedures.schema``)."""
    out: list[str] = []
    for line in text[start:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("**"):
            break
        out.append(stripped)
    return "\n" + "\n".join(out) if out else ""


def audit_procedures(cat: Catalog) -> tuple[list[ProcedureReport], list[Finding]]:
    reports: list[ProcedureReport] = []
    findings: list[Finding] = []
    for md in sorted(PROC_DIR.rglob("*.md")):
        rel = md.relative_to(PROC_DIR).as_posix()
        category = rel.split("/", 1)[0] if "/" in rel else "root"
        text = md.read_text(encoding="utf-8", errors="replace")

        rep = ProcedureReport(rel_path=rel, category=category)
        entry = cat.index_by_file.get(rel, {})
        # A card that DECLARES it commands nothing, via the `advisory: true` /
        # `advisory_reason:` fields on ProcedureIndexEntry. See the header of
        # procedures/procedure_index.yaml.
        rep.advisory = bool(entry.get("advisory"))
        # A document with no index entry is not audited for the zero-command
        # invariant: it has nothing declaring what it should command, and the
        # index/tree agreement is asserted separately in test_procedure_gates.
        rep.indexed = bool(entry)

        # ── 2026-08b Phase 5: what does the EXTRACTOR get out of this? ──
        #
        # Everything below this point used to be checked only against the text
        # the auditor could see. A document with no `**TC:**` line therefore
        # scored zero findings in every category while commanding nothing —
        # 21 documents, including an EMERGENCY card, passed that way.
        steps, problems = markdown_steps(text, cat.tc_entries)
        rep.command_steps = sum(1 for s in steps if s.get("type") == "command")

        for problem in problems:
            if problem.category == "TC_DROPPED":
                detail = f"line {problem.line}: {problem.message}"
                rep.tc_not_extracted.append(detail)
                findings.append(Finding(rel, "TC_NOT_EXTRACTED", detail))
            elif problem.category == "BAD_BRANCH":
                detail = f"line {problem.line}: {problem.message}"
                rep.tc_not_extracted.append(detail)
                findings.append(Finding(rel, "TC_NOT_EXTRACTED", detail))

        # A catalogued command hiding behind `**Action:**` — including the
        # bullet block underneath it, which is where NOM-012 kept an entire
        # six-structure housekeeping sweep.
        for m in RE_ACTION_LINE.finditer(text):
            body = m.group(1) + _action_bullet_block(text, m.end())
            hidden = sorted({tok for tok in RE_BARE_MNEMONIC.findall(body)
                             if tok in cat.tc_names
                             and tok not in GENERIC_TC_LABELS})
            for mnemonic in hidden:
                detail = (f"{mnemonic} is commanded from an **Action:** line; "
                          f"only **TC:** is extracted, so this is INERT")
                if detail not in rep.action_command:
                    rep.action_command.append(detail)
                    findings.append(Finding(
                        rel, "ACTION_COMMAND", detail,
                        context=m.group(0)[:120]))

        # A procedure that yields no commands at all. Advisory cards declare
        # that they are documents; everything else claiming `command_services`
        # while extracting nothing is the defect stated as an invariant.
        if rep.command_steps == 0 and not rep.advisory and rep.indexed:
            services = entry.get("command_services")
            detail = (f"extracts ZERO command steps"
                      + (f" while procedure_index.yaml declares "
                         f"command_services: {services}" if services else "")
                      + " — mark it `advisory: true` with an `advisory_reason`, "
                        "or give it real **TC:** annotations")
            rep.zero_command.append(detail)
            findings.append(Finding(rel, "ZERO_COMMAND_PROCEDURE", detail))
        elif rep.command_steps > 0 and rep.advisory:
            detail = (f"declared `advisory: true` but extracts "
                      f"{rep.command_steps} command step(s)")
            rep.zero_command.append(detail)
            findings.append(Finding(rel, "ZERO_COMMAND_PROCEDURE", detail))

        # `name (0xNNNN)` where the name and the id name different parameters.
        for m in RE_NAME_WITH_ID.finditer(text):
            name, hexid = m.group(1).lower(), m.group(2)
            entry_p = cat.param_by_name.get(name)
            if entry_p is None:
                continue
            try:
                pid = int(hexid, 16)
                want = (entry_p["id"] if isinstance(entry_p["id"], int)
                        else int(str(entry_p["id"]), 0))
            except (ValueError, KeyError):
                continue
            if pid == want:
                continue
            other = cat.param_by_id.get(pid, {}).get("name", "UNDEFINED")
            detail = (f"{name} cited as {hexid}, but {name} is 0x{want:04X} "
                      f"and {hexid} is {other}")
            if detail not in rep.param_name_id_mismatch:
                rep.param_name_id_mismatch.append(detail)
                findings.append(Finding(rel, "PARAM_NAME_ID_MISMATCH", detail))

        # A step that requests one HK structure and verifies a parameter that
        # lives in another. Both halves are individually valid, which is why
        # nothing caught it: the check the card asks for simply cannot pass.
        for block in RE_STEP_BREAK.split(text):
            requested = {int(x) for x in RE_HK_REQUEST.findall(block)}
            if not requested:
                continue
            available: set[int] = set()
            for sid in requested:
                available |= cat.hk_params_by_sid.get(sid, set())
            for line in block.splitlines():
                if not line.lstrip().startswith("**Verify:**"):
                    continue
                for name in RE_VERIFY_PARAM.findall(line):
                    entry_p = cat.param_by_name.get(name.lower())
                    if entry_p is None:
                        continue
                    pid = (entry_p["id"] if isinstance(entry_p["id"], int)
                           else int(str(entry_p["id"]), 0))
                    if pid in available or pid in cat.on_demand_ids:
                        continue
                    homes = sorted(cat.hk_sids_for_param.get(pid, []))
                    detail = (f"step requests HK SID {sorted(requested)} but "
                              f"verifies {name} (0x{pid:04X}), which is in "
                              f"SID {homes or 'no structure'}")
                    if detail not in rep.hk_sid_mismatch:
                        rep.hk_sid_mismatch.append(detail)
                        findings.append(Finding(rel, "HK_SID_MISMATCH", detail))

        # TCs
        for m in RE_TC_LINE.finditer(text):
            tok = m.group(1).upper()
            if tok in GENERIC_TC_LABELS:
                continue
            rep.tc_refs.append(tok)
            if tok not in cat.tc_names:
                rep.tc_unknown.append(tok)
                findings.append(Finding(rel, "TC_UNKNOWN", tok,
                                        context=text[max(0, m.start()-30):m.end()+30]))

        # func_id annotations must match the catalogue (drift gate 4)
        for rx in (RE_FUNC_ANNOT, RE_FUNC_TABLE_ROW):
            for m in rx.finditer(text):
                mnemonic = m.group(1).upper()
                if mnemonic in GENERIC_TC_LABELS:
                    continue
                want = cat.tc_func_ids.get(mnemonic)
                if want is None:
                    continue
                got = int(m.group(2))
                if got != want:
                    detail = (f"{mnemonic} annotated func_id {got}, "
                              f"tc_catalog.yaml says {want}")
                    if detail not in rep.func_id_mismatch:
                        rep.func_id_mismatch.append(detail)
                        findings.append(Finding(
                            rel, "FUNC_ID_MISMATCH", detail,
                            context=text[max(0, m.start() - 30):m.end() + 30]))

        # Named event references: "PROP_BURN_START (0x0803)". Checked BEFORE
        # the parameter sweep, because an event id in a procedure is not a
        # defect — citing the wrong NAME for it is (G3-e).
        event_hexes: set[str] = set()
        for m in RE_EVENT_NAMED.finditer(text):
            nm, hx = m.group(1).upper(), m.group(2)
            try:
                eid = int(hx, 16)
            except ValueError:
                continue
            # Event ids and parameter ids share the hex space, so the NAME
            # is what makes this an event reference. Without that test,
            # "GET_PARAM(0x0601)" reads as a citation of event 0x0601.
            if nm not in cat.event_by_name:
                continue
            event_hexes.add(hx.lower())
            rep.event_refs.append(f"{nm} ({hx})")
            want = cat.event_by_id.get(eid)
            if want is not None and want != nm:
                detail = f"{nm} cited as {hx}, but {hx} is {want}"
                if detail not in rep.event_mismatch:
                    rep.event_mismatch.append(detail)
                    findings.append(Finding(
                        rel, "EVENT_NAME_MISMATCH", detail,
                        context=text[max(0, m.start() - 40):m.end() + 20]))
            elif want is None:
                detail = (f"{nm} is event "
                          f"0x{cat.event_by_name[nm]:04X}, cited as {hx}")
                if detail not in rep.event_mismatch:
                    rep.event_mismatch.append(detail)
                    findings.append(Finding(rel, "EVENT_NAME_MISMATCH", detail))

        # S20 subtype annotations. GET_PARAM is S20.1 -> report S20.2;
        # SET_PARAM is S20.3. The payload procedures systematically inverted
        # the pair (Phase E audit, fixed in three commissioning procedures
        # only), so an operator following the card sent the wrong subtype.
        for m in RE_S20_SUBTYPE.finditer(text):
            mnemonic, sub = m.group(1).upper(), int(m.group(2))
            want = S20_SUBTYPE.get(mnemonic)
            if want is None or sub == want:
                continue
            detail = f"{mnemonic} annotated Subtype {sub}, S20 says {want}"
            if detail not in rep.subtype_inverted:
                rep.subtype_inverted.append(detail)
                findings.append(Finding(
                    rel, "SUBTYPE_INVERTED", detail,
                    context=text[max(0, m.start() - 40):m.end() + 20]))

        # Hex TM params
        for m in RE_HEX_PARAM.finditer(text):
            hx = m.group(1)
            try:
                pid = int(hx, 16)
            except ValueError:
                continue
            if hx.lower() in event_hexes:
                continue           # already resolved as a catalogued event
            rep.param_refs.append(hx)
            if pid not in cat.param_by_id:
                if pid in cat.event_by_id:
                    # An event id cited without its name. Not an error, but it
                    # is worth not calling it a missing parameter.
                    rep.event_refs.append(f"{cat.event_by_id[pid]} ({hx})")
                    continue
                rep.param_unknown.append(hx)
                findings.append(Finding(rel, "PARAM_UNKNOWN", hx))
                continue
            if pid not in cat.hk_param_ids and pid not in cat.on_demand_ids:
                rep.param_not_reachable.append(hx)
                findings.append(Finding(
                    rel, "PARAM_NOT_REACHABLE",
                    f"{hx} ({cat.param_by_id[pid].get('name','?')}) "
                    f"not in any HK SID and not marked on_demand"))

        # Dotted names — only check if the name is a known param but skip
        # plain English tokens. We treat them as soft-refs.
        for m in RE_DOTTED.finditer(text):
            nm = m.group(1).lower()
            if nm in cat.param_by_name:
                pid = int(cat.param_by_name[nm].get("id", 0)) if isinstance(
                    cat.param_by_name[nm].get("id"), int) else int(
                    str(cat.param_by_name[nm]["id"]), 0)
                if pid and pid not in cat.hk_param_ids and pid not in cat.on_demand_ids:
                    hx = f"0x{pid:04X}"
                    if hx not in rep.param_not_reachable:
                        rep.param_not_reachable.append(hx + f" ({nm})")
                        findings.append(Finding(
                            rel, "PARAM_NOT_REACHABLE",
                            f"{nm} ({hx}) not in any HK SID and not marked on_demand"))

        reports.append(rep)
    return reports, findings


# ---------- report ----------
HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>EOSAT-1 Procedure Audit</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; color: #222; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 4px; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; font-size: 13px; vertical-align: top; }}
th {{ background: #f0f0f0; }}
.ok {{ color: #080; }} .warn {{ color: #b60; }} .err {{ color: #c00; font-weight: bold; }}
code {{ background: #f6f6f6; padding: 1px 4px; border-radius: 3px; }}
summary {{ cursor: pointer; font-weight: bold; }}
.cat {{ background: #eef; padding: 2px 6px; border-radius: 3px; font-size: 11px; }}
</style></head><body>
<h1>EOSAT-1 Procedure Walkthrough Audit</h1>
<p>Generated by <code>tools/procedure_audit.py</code>.
Scans every procedure markdown file, extracts TC and TM references, and
cross-checks them against <code>tc_catalog.yaml</code>,
<code>telemetry/parameters.yaml</code>, and <code>hk_structures.yaml</code>.</p>
<h2>Summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Procedures scanned</td><td>{n_proc}</td></tr>
<tr><td>TC references (total)</td><td>{n_tc}</td></tr>
<tr><td>TC references unresolved</td><td class="{tc_cls}">{n_tc_bad}</td></tr>
<tr><td>TM param references (total)</td><td>{n_pm}</td></tr>
<tr><td>TM params unknown</td><td class="{pm_cls}">{n_pm_bad}</td></tr>
<tr><td>TM params not reachable (not in HK, not on_demand)</td><td class="{nr_cls}">{n_nr}</td></tr>
</table>

<h2>Findings by procedure</h2>
{per_proc}

<h2>All findings</h2>
<table>
<tr><th>Procedure</th><th>Category</th><th>Detail</th></tr>
{findings_rows}
</table>

</body></html>"""


def render(reports: list[ProcedureReport], findings: list[Finding]) -> str:
    n_tc = sum(len(r.tc_refs) for r in reports)
    n_tc_bad = sum(len(r.tc_unknown) for r in reports)
    n_pm = sum(len(r.param_refs) for r in reports)
    n_pm_bad = sum(len(r.param_unknown) for r in reports)
    n_nr = sum(len(r.param_not_reachable) for r in reports)

    per_proc_html = []
    for r in reports:
        bad = (len(r.tc_unknown) + len(r.param_unknown)
               + len(r.param_not_reachable) + len(r.func_id_mismatch)
               + len(r.event_mismatch) + len(r.subtype_inverted)
               + len(r.action_command) + len(r.tc_not_extracted)
               + len(r.param_name_id_mismatch) + len(r.hk_sid_mismatch)
               + len(r.zero_command))
        cls = "err" if bad else "ok"
        per_proc_html.append(
            f"<details><summary class='{cls}'>"
            f"<span class='cat'>{r.category}</span> {r.rel_path} — "
            f"{len(r.tc_refs)} TCs, <b>{r.command_steps}</b> extracted command "
            f"steps{' (ADVISORY)' if r.advisory else ''}, "
            f"{len(r.param_refs)} TMs, "
            f"{len(r.event_refs)} events, "
            f"<b>{bad}</b> issues</summary>"
            f"<ul>"
            + (f"<li class='err'>Commanding hidden behind **Action:**: "
               f"{'; '.join(r.action_command)}</li>" if r.action_command else "")
            + (f"<li class='err'>**TC:** annotations that do not survive "
               f"extraction: {'; '.join(r.tc_not_extracted)}</li>"
               if r.tc_not_extracted else "")
            + (f"<li class='err'>Zero-command procedure: "
               f"{'; '.join(r.zero_command)}</li>" if r.zero_command else "")
            + (f"<li class='err'>Parameter name/id disagreement: "
               f"{'; '.join(r.param_name_id_mismatch)}</li>"
               if r.param_name_id_mismatch else "")
            + (f"<li class='err'>HK structure does not carry the verified "
               f"parameter: {'; '.join(r.hk_sid_mismatch)}</li>"
               if r.hk_sid_mismatch else "")
            + (f"<li class='err'>Unknown TCs: {', '.join(r.tc_unknown)}</li>"
               if r.tc_unknown else "")
            + (f"<li class='err'>func_id annotations contradicting the catalogue: "
               f"{'; '.join(r.func_id_mismatch)}</li>"
               if r.func_id_mismatch else "")
            + (f"<li class='err'>Unknown TM params: {', '.join(r.param_unknown)}</li>"
               if r.param_unknown else "")
            + (f"<li class='warn'>TM params not reachable: "
               f"{', '.join(r.param_not_reachable)}</li>"
               if r.param_not_reachable else "")
            + (f"<li class='err'>Event name/id mismatches: "
               f"{'; '.join(r.event_mismatch)}</li>"
               if r.event_mismatch else "")
            + (f"<li class='err'>S20 subtype inverted: "
               f"{'; '.join(r.subtype_inverted)}</li>"
               if r.subtype_inverted else "")
            + "</ul></details>"
        )

    findings_rows = "".join(
        f"<tr><td>{f.procedure}</td><td>{f.category}</td><td>{f.detail}</td></tr>"
        for f in findings
    ) or "<tr><td colspan='3' class='ok'>No findings — all references clean.</td></tr>"

    return HTML_TEMPLATE.format(
        n_proc=len(reports),
        n_tc=n_tc, n_tc_bad=n_tc_bad, tc_cls="err" if n_tc_bad else "ok",
        n_pm=n_pm, n_pm_bad=n_pm_bad, pm_cls="err" if n_pm_bad else "ok",
        n_nr=n_nr, nr_cls="warn" if n_nr else "ok",
        per_proc="\n".join(per_proc_html),
        findings_rows=findings_rows,
    )


def main() -> int:
    cat = Catalog()
    cat.load()
    reports, findings = audit_procedures(cat)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "procedure_audit.json").write_text(json.dumps(
        {
            "summary": {
                "n_procedures": len(reports),
                "n_findings": len(findings),
                "tc_catalog_entries": len(cat.tc_names),
                "param_catalog_entries": len(cat.param_by_id),
                "hk_params": len(cat.hk_param_ids),
                "on_demand_params": len(cat.on_demand_ids),
            },
            "reports": [asdict(r) for r in reports],
            "findings": [asdict(f) for f in findings],
        }, indent=2))
    (OUT_DIR / "procedure_audit.html").write_text(render(reports, findings))

    # Console summary
    print(f"Scanned {len(reports)} procedures")
    print(f"Findings: {len(findings)}")
    for cat_name in ("TC_UNKNOWN", "FUNC_ID_MISMATCH", "PARAM_UNKNOWN",
                     "PARAM_NOT_REACHABLE", "EVENT_NAME_MISMATCH",
                     "SUBTYPE_INVERTED", "ACTION_COMMAND", "TC_NOT_EXTRACTED",
                     "PARAM_NAME_ID_MISMATCH", "HK_SID_MISMATCH",
                     "ZERO_COMMAND_PROCEDURE"):
        n = sum(1 for f in findings if f.category == cat_name)
        print(f"  {cat_name}: {n}")
    inert = [r.rel_path for r in reports
             if r.command_steps == 0 and not r.advisory]
    print(f"Procedures extracting zero commands (non-advisory): {len(inert)}")
    print(f"Report: {OUT_DIR/'procedure_audit.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
