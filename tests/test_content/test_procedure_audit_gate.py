"""`tools/procedure_audit.py` is a CI gate, not a report (G3-e).

The auditor has existed for two sessions and was run by hand. Everything it
found stayed found: nine `PARAM_UNKNOWN` findings on the Phase D propulsion
events, a systemic S20 subtype inversion across ten procedure files, and a
`S8_COMMAND(func_id=81, …)` that names no catalogued command. A tool whose
output nobody is obliged to act on is a report; this file makes it a gate.

Every category is pinned at zero. The categories are:

* `TC_UNKNOWN` — a `**TC:**` line names a mnemonic that is not in
  `tc_catalog.yaml`. An operator following the card cannot send it.
* `FUNC_ID_MISMATCH` — a mnemonic and a `(func N)` annotation disagree with
  the catalogue. Pinned separately since Phase E; kept here for completeness.
* `PARAM_UNKNOWN` — a `(0xNNNN)` that resolves to no parameter *and* no event.
* `PARAM_NOT_REACHABLE` — a real parameter in no HK SID and not `on_demand`,
  so the verification step the card asks for cannot be performed.
* `EVENT_NAME_MISMATCH` — `PROP_BURN_START (0x0804)` when 0x0804 is
  `PROP_BURN_COMPLETE`. Referentially valid, operationally wrong.
* `SUBTYPE_INVERTED` — `GET_PARAM … Subtype 1` when this suite's S20 uses
  subtype 1 for SET and subtype 3 for GET.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from procedure_audit import Catalog, audit_procedures  # noqa: E402

CATEGORIES = ("TC_UNKNOWN", "FUNC_ID_MISMATCH", "PARAM_UNKNOWN",
              "PARAM_NOT_REACHABLE", "EVENT_NAME_MISMATCH",
              "SUBTYPE_INVERTED")

#: Findings that are deliberate. Every entry needs a reason; empty is the goal.
ALLOWLIST: dict[tuple[str, str], str] = {}


@pytest.fixture(scope="module")
def findings():
    cat = Catalog()
    cat.load()
    _, f = audit_procedures(cat)
    return [x for x in f if (x.procedure, x.detail) not in ALLOWLIST]


@pytest.mark.parametrize("category", CATEGORIES)
def test_the_procedure_audit_is_clean(findings, category):
    bad = [f"{f.procedure}: {f.detail}" for f in findings
           if f.category == category]
    assert not bad, (
        f"{len(bad)} {category} findings — run `python tools/procedure_audit.py` "
        f"for the full report:\n  " + "\n  ".join(bad))


def test_no_uncategorised_finding_escapes_the_gate(findings):
    """A new finding category added to the tool must be added here too, or it
    would be invisible — which is how the auditor became a report."""
    unknown = sorted({f.category for f in findings} - set(CATEGORIES))
    assert not unknown, (
        f"procedure_audit.py emits categories this gate does not check: "
        f"{unknown}")


def test_the_audit_still_scans_every_procedure():
    """A gate that silently stops looking at files is worse than no gate."""
    cat = Catalog()
    cat.load()
    reports, _ = audit_procedures(cat)
    on_disk = list((REPO / "configs" / "eosat1" / "procedures").rglob("*.md"))
    assert len(reports) == len(on_disk), (
        f"audited {len(reports)} procedures but {len(on_disk)} exist on disk")
    assert len(reports) >= 60, f"only {len(reports)} procedures scanned"


def test_the_allowlist_has_no_stale_entries():
    cat = Catalog()
    cat.load()
    _, all_findings = audit_procedures(cat)
    live = {(f.procedure, f.detail) for f in all_findings}
    stale = [k for k in ALLOWLIST if k not in live]
    assert not stale, f"ALLOWLIST names findings that no longer occur: {stale}"


# ── Guard the guards: each check must fire on a known-bad file ──────────

@pytest.mark.parametrize("body,category,needle", [
    ("**TC:** `NOT_A_REAL_COMMAND(x=1)` (Service 8, Subtype 1)\n",
     "TC_UNKNOWN", "NOT_A_REAL_COMMAND"),
    ("**Verify:** event PROP_BURN_START (0x0804) within 10s\n",
     "EVENT_NAME_MISMATCH", "PROP_BURN_START"),
    ("**TC:** `GET_PARAM(0x0101)` (Service 20, Subtype 1)\n",
     "SUBTYPE_INVERTED", "GET_PARAM"),
    ("**Verify:** `thing` (0xEEEE) reported\n",
     "PARAM_UNKNOWN", "0xEEEE"),
])
def test_each_check_fires(tmp_path, monkeypatch, body, category, needle):
    import procedure_audit as pa
    d = tmp_path / "procedures" / "contingency"
    d.mkdir(parents=True)
    (d / "bogus.md").write_text(body)
    monkeypatch.setattr(pa, "PROC_DIR", tmp_path / "procedures")
    cat = Catalog()
    cat.load()
    _, found = pa.audit_procedures(cat)
    hits = [f for f in found if f.category == category and needle in f.detail]
    assert hits, (
        f"the {category} check did not fire on a known-bad procedure: {body!r}")


def test_a_correct_event_citation_is_not_a_finding(tmp_path, monkeypatch):
    """The false positive this gate had to remove first: nine correct Phase D
    event citations reported as missing parameters, which is exactly the noise
    that stops anyone reading the report."""
    import procedure_audit as pa
    d = tmp_path / "procedures" / "contingency"
    d.mkdir(parents=True)
    (d / "ok.md").write_text(
        "**Verify:** event PROP_ARMED (0x0800); PROP_BURN_START (0x0803);\n"
        "PROP_LEAK_DETECTED (0x080B), PROP_VALVE_FAULT (0x080A)\n")
    monkeypatch.setattr(pa, "PROC_DIR", tmp_path / "procedures")
    cat = Catalog()
    cat.load()
    _, found = pa.audit_procedures(cat)
    assert not found, f"correct event citations reported as findings: {found}"
