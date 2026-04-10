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

OUT_DIR = REPO / "workspace" / "audit"

# ---------- regex ----------
RE_TC_LINE = re.compile(r"\*\*TC:\*\*\s*`?([A-Z][A-Z0-9_]+)", re.IGNORECASE)

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


# ---------- catalog loading ----------
@dataclass
class Catalog:
    tc_names: set[str] = field(default_factory=set)
    tc_services: dict[str, tuple[int, int]] = field(default_factory=dict)
    param_by_id: dict[int, dict] = field(default_factory=dict)
    param_by_name: dict[str, dict] = field(default_factory=dict)
    hk_param_ids: set[int] = field(default_factory=set)
    hk_sids_for_param: dict[int, list[int]] = field(default_factory=dict)
    on_demand_ids: set[int] = field(default_factory=set)

    def load(self) -> None:
        with TC_CATALOG.open() as f:
            tc = yaml.safe_load(f) or {}
        for cmd in tc.get("commands", []) or []:
            name = str(cmd.get("name", "")).strip()
            if not name:
                continue
            self.tc_names.add(name.upper())
            try:
                self.tc_services[name.upper()] = (
                    int(cmd.get("service", 0)), int(cmd.get("subtype", 0)))
            except Exception:
                pass
            for alias_key in ("aliases", "short_names"):
                for a in cmd.get(alias_key, []) or []:
                    self.tc_names.add(str(a).upper())

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


# ---------- audit ----------
@dataclass
class Finding:
    procedure: str
    category: str  # TC_UNKNOWN, PARAM_UNKNOWN, PARAM_NOT_REACHABLE
    detail: str
    context: str = ""


@dataclass
class ProcedureReport:
    rel_path: str
    category: str
    tc_refs: list[str] = field(default_factory=list)
    param_refs: list[str] = field(default_factory=list)
    tc_unknown: list[str] = field(default_factory=list)
    param_unknown: list[str] = field(default_factory=list)
    param_not_reachable: list[str] = field(default_factory=list)


def audit_procedures(cat: Catalog) -> tuple[list[ProcedureReport], list[Finding]]:
    reports: list[ProcedureReport] = []
    findings: list[Finding] = []
    for md in sorted(PROC_DIR.rglob("*.md")):
        rel = md.relative_to(PROC_DIR).as_posix()
        category = rel.split("/", 1)[0] if "/" in rel else "root"
        text = md.read_text(encoding="utf-8", errors="replace")

        rep = ProcedureReport(rel_path=rel, category=category)

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

        # Hex TM params
        for m in RE_HEX_PARAM.finditer(text):
            hx = m.group(1)
            try:
                pid = int(hx, 16)
            except ValueError:
                continue
            rep.param_refs.append(hx)
            if pid not in cat.param_by_id:
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
        bad = len(r.tc_unknown) + len(r.param_unknown) + len(r.param_not_reachable)
        cls = "err" if bad else "ok"
        per_proc_html.append(
            f"<details><summary class='{cls}'>"
            f"<span class='cat'>{r.category}</span> {r.rel_path} — "
            f"{len(r.tc_refs)} TCs, {len(r.param_refs)} TMs, "
            f"<b>{bad}</b> issues</summary>"
            f"<ul>"
            + (f"<li class='err'>Unknown TCs: {', '.join(r.tc_unknown)}</li>"
               if r.tc_unknown else "")
            + (f"<li class='err'>Unknown TM params: {', '.join(r.param_unknown)}</li>"
               if r.param_unknown else "")
            + (f"<li class='warn'>TM params not reachable: "
               f"{', '.join(r.param_not_reachable)}</li>"
               if r.param_not_reachable else "")
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
    for cat_name in ("TC_UNKNOWN", "PARAM_UNKNOWN", "PARAM_NOT_REACHABLE"):
        n = sum(1 for f in findings if f.category == cat_name)
        print(f"  {cat_name}: {n}")
    print(f"Report: {OUT_DIR/'procedure_audit.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
