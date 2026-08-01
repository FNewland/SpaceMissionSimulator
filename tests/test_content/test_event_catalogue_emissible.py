"""Every catalogued event must be emittable (brief 08 WP-8, gap 6 / O-11).

An event id in `events/event_catalog.yaml` that appears in no emission site is
a promise the tool cannot keep: the operations manual documents it, students
look for it, and it can never appear in the log.  Before Phase G, ten events
were in exactly that state — the seven FDIR events behind the fault-cascade
and recovery model that never ran, `0x0F06 LOAD_SHED_DEACTIVATED`,
`0x0111 EPS_SAFE_MODE_ENTRY`, and `0x0112 WING_PY_DEPLOYED` (whose emitter
raised `0x010E CHARGE_COMPLETE` instead).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
PACKAGES = REPO / "packages"

CATALOG = yaml.safe_load(
    (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())["events"]


def _pid(v) -> int:
    return int(v) if isinstance(v, int) else int(str(v), 0)


def _source() -> str:
    chunks = []
    for f in PACKAGES.glob("**/*.py"):
        if "__pycache__" in str(f):
            continue
        chunks.append(f.read_text(errors="ignore"))
    return "\n".join(chunks)


SRC = _source()

#: Events emitted from configuration rather than from a literal in code, or
#: reserved. Each needs a reason.
NOT_EMITTED_IN_CODE: dict[int, str] = {}


def _emitted(event_id: int) -> bool:
    """Does any source file name this id, in hex or decimal?"""
    forms = (f"0x{event_id:04X}", f"0x{event_id:04x}", f"0x{event_id:X}",
             str(event_id))
    for f in forms[:3]:
        if f in SRC:
            return True
    # Synthetic ranges: FDIR mirrors events as 0x8000|param and 0x9000|param.
    return False


def test_every_catalogued_event_is_emittable():
    orphans = []
    for e in CATALOG:
        eid = _pid(e["id"])
        if eid in NOT_EMITTED_IN_CODE:
            continue
        if not _emitted(eid):
            orphans.append(f"0x{eid:04X} {e.get('name')}")
    assert not orphans, (
        f"{len(orphans)} catalogued events appear at no emission site: "
        f"{orphans}")


def test_the_fdir_events_are_emitted():
    """The specific seven brief 08 O-11 said could never be emitted."""
    required = {
        0x0F00: "FDIR_FAULT_DETECTED",
        0x0F01: "FDIR_RECOVERY_STARTED",
        0x0F02: "FDIR_RECOVERY_COMPLETE",
        0x0F03: "FDIR_RECOVERY_FAILED",
        0x0F04: "FDIR_LEVEL_ESCALATION",
        0x0F06: "LOAD_SHED_DEACTIVATED",
        0x0F0A: "SAFE_MODE_ENTRY",
        0x0F0B: "SAFE_MODE_EXIT",
        0x0111: "EPS_SAFE_MODE_ENTRY",
        0x0112: "WING_PY_DEPLOYED",
    }
    missing = [f"0x{eid:04X} {name}" for eid, name in required.items()
               if not _emitted(eid)]
    assert not missing, f"still unemittable: {missing}"


def test_wing_deploy_events_use_their_own_ids():
    """The +Y/-Y wing deployment raised 0x010E/0x010F — CHARGE_COMPLETE and
    PDU_OVERCURRENT — so a wing deployment logged "battery reached 100% SoC"."""
    eps = (PACKAGES / "smo-simulator" / "src" / "smo_simulator" / "models"
           / "eps_basic.py").read_text()
    block = eps[eps.index("Wing deployment tick"):][:2500]
    emitted = re.findall(r"events_to_generate\.append\(\((0x[0-9A-Fa-f]+)",
                         block)
    assert "0x0112" in emitted, "+Y wing deployment does not raise 0x0112"
    assert "0x0113" in emitted, "-Y wing deployment does not raise 0x0113"
    assert "0x010E" not in emitted and "0x010F" not in emitted, (
        "wing deployment still raises CHARGE_COMPLETE / PDU_OVERCURRENT")


def test_event_ids_are_unique():
    seen: dict[int, str] = {}
    dupes = []
    for e in CATALOG:
        eid = _pid(e["id"])
        if eid in seen:
            dupes.append(f"0x{eid:04X}: {seen[eid]} vs {e.get('name')}")
        seen[eid] = e.get("name", "")
    assert not dupes, f"duplicate event ids: {dupes}"


def test_not_emitted_allowlist_entries_still_exist():
    ids = {_pid(e["id"]) for e in CATALOG}
    stale = [f"0x{i:04X}" for i in NOT_EMITTED_IN_CODE if i not in ids]
    assert not stale, f"stale NOT_EMITTED_IN_CODE entries: {stale}"
