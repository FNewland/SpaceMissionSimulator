"""Cross-check the shipped flight procedures against the MCS command catalog.

``configs/eosat1/procedures/**/*.md`` are the documents an operator follows
during a pass. Each commanding step is written as::

    **TC:** `OBC_BOOT_APP` (Service 8, Subtype 1, func_id 55)

Nothing verified that those mnemonics exist in ``tc_catalog.yaml``, that the
service/subtype annotation matches the catalog entry, or that a raw S8 func_id
quoted in a procedure routes to a real handler. A procedure step naming a
command the MCS cannot send is a dead step an operator only discovers mid-pass
(defect #40 was exactly a catalog/procedure mismatch surfacing at load time).

``test_config_integrity.py`` already checks that every indexed procedure file
exists; this module covers the *contents* of those files.

**Domain ownership:** where a procedure's annotation disagrees with the shipped
implementation, the drift is recorded in an allowlist below rather than
silently rewritten — deciding whether the doc or the code is wrong is a Flight
Ops call. The allowlists pin today's state so new drift still fails.
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from smo_simulator.service_dispatch import ServiceDispatcher

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
PROC_DIR = CONFIG_DIR / "procedures"
INDEX_FILE = PROC_DIR / "procedure_index.yaml"
CATALOG_FILE = CONFIG_DIR / "commands" / "tc_catalog.yaml"

TC_LINE = re.compile(r"\*\*TC:\*\*\s*`([^`]+)`(?P<rest>[^\n]*)")
ANNOTATION = re.compile(r"Service\s+(\d+),\s*Subtype\s+(\d+)")
FUNC_ID = re.compile(r"func_id\s*[= ]\s*(\d+)")

PROC_FILES = sorted(PROC_DIR.rglob("*.md"))
PROC_IDS = [str(p.relative_to(PROC_DIR)) for p in PROC_FILES]

# Placeholder mnemonics for "send a raw S8.1 with this func_id". They are not
# catalog entries by design, but they must always carry a func_id.
GENERIC_S8_WRAPPERS = {"FUNC_PERFORM", "S8_COMMAND"}

# (mnemonic, service, subtype) annotations in the procedures that disagree with
# tc_catalog.yaml. Reported to Flight Ops; pinned here so *new* drift fails.
#: Known mnemonic/annotation disagreements with `tc_catalog.yaml`.
#:
#: **EMPTY as of 2026-08b Phase 5 — every entry below was FIXED, not excused.**
#: The list is kept (with its history) because an empty allowlist is the goal
#: state and a future addition should have to argue for itself.
#:
#: What was here, and what happened to it:
#:
#:   ("HK_REQUEST", 3, 25) / (3, 1)      -> 96 annotations corrected to S3.27
#:   ("HK_REPORT_CONFIG", 3, 25)         -> the mnemonic is an ALIAS of
#:                                          HK_ENABLE and the command is S3.5;
#:                                          hk_configuration.md also wrote its
#:                                          `SID=N` outside the backticks, so
#:                                          the wire packed sid=0
#:   ("HEATER_CONTROL", 8, 7)            -> HEATER_CONTROL is an alias of
#:   ("OBC_SET_MODE", 8, 3)                 HEATER_BATTERY and has no `circuit`
#:   ("AOCS_SET_MODE", 8, 4)                argument at all. The whole "S8
#:   ("PAYLOAD_SET_MODE", 8, 5)             subtype names the subsystem"
#:   ("TTC_SWITCH_REDUNDANT", 8, 2)         shorthand was invented; every S8
#:                                          function-perform is Subtype 1, and
#:                                          the dispatcher rejects the rest.
#:   ("TTC_SWITCH_PRIMARY", 11, 1)       -> S11 is time-tagged scheduling; the
#:   ("TTC_SWITCH_REDUNDANT", 11, 2)        transponder selects are S8 funcs
#:                                          63 / 64
#:   ("MEM_LOAD_DATA", 6, 9)             -> the command is MEM_LOAD at S6.2;
#:                                          S6.9 is MEM_CHECK, so the UPLOAD
#:                                          step was addressed to the checksum
#:                                          service
#:   ("MEM_CHECK", 6, 5)                 -> S6.5 is MEM_DUMP: the card asked
#:                                          for a dump and then verified a
#:                                          checksum nobody computed
#:   ("MEM_LOAD_ABORT", 6, 3)            -> the mnemonic does not exist in the
#:                                          catalogue at all; the reference is
#:                                          gone from the tree
KNOWN_ANNOTATION_DRIFT: dict[tuple[str, int, int], str] = {}

# Raw S8 func_ids quoted in procedures that route to a different subsystem than
# the surrounding text describes. Value = subsystem the func actually drives.
# Same class as the S19 rule drift; reported, not rewritten.
KNOWN_FUNC_INTENT_DRIFT = {
    10: "aocs",     # software_upload.md: "suspend onboard scheduling"
    11: "aocs",     # software_upload.md: "resume onboard scheduling"
    20: "eps",      # software_upload.md: "switch boot source"
    21: "eps",      # obc_reboot.md / software_upload.md: "select memory bank"
    30: "payload",  # software_upload.md: "command controlled OBC restart"
    42: "tcs",      # obc_reboot.md: "command OBC reboot"
    43: "tcs",      # obc_reboot.md: "switch to backup OBC"
}

SUBSYSTEMS = ("aocs", "eps", "payload", "tcs", "obdh", "ttc", "prop")


# ── Helpers ─────────────────────────────────────────────────────────

def _catalog() -> dict[str, dict]:
    """Mnemonic (name or alias) -> catalog entry."""
    entries = yaml.safe_load(CATALOG_FILE.read_text())["commands"]
    index: dict[str, dict] = {}
    for entry in entries:
        index[entry["name"]] = entry
        for alias in entry.get("aliases", []) or []:
            index.setdefault(alias, entry)
    return index


CATALOG = _catalog()


def _tc_references(path: Path) -> list[dict]:
    """Every **TC:** step in a procedure, with its annotations."""
    refs = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        match = TC_LINE.search(line)
        if not match:
            continue
        rest = match.group("rest")
        annotation = ANNOTATION.search(rest)
        func = FUNC_ID.search(match.group(1) + rest)
        refs.append({
            "line": lineno,
            "raw": match.group(1),
            "mnemonic": match.group(1).split("(")[0].strip(),
            "service": int(annotation.group(1)) if annotation else None,
            "subtype": int(annotation.group(2)) if annotation else None,
            "func_id": int(func.group(1)) if func else None,
        })
    return refs


def _routed_subsystems(func_id: int) -> set[str]:
    """Which subsystem handlers S8.1 func_id actually drives."""
    engine = MagicMock()
    engine.params = {}
    engine.tm_builder = MagicMock()
    engine.tm_builder._pack_tm = MagicMock(return_value=b"\x00" * 20)
    subs = {}
    for name in SUBSYSTEMS:
        model = MagicMock()
        model.handle_command = MagicMock(return_value={"success": True})
        model._state = MagicMock()
        model._state.power_lines = {}
        subs[name] = model
    engine.subsystems = subs
    ServiceDispatcher(engine)._handle_s8(1, bytes([func_id]) + bytes(32))
    return {n for n, m in subs.items() if m.handle_command.called}


# ── Sanity ──────────────────────────────────────────────────────────

def test_procedure_tree_is_discovered():
    assert len(PROC_FILES) >= 40, (
        f"expected the shipped procedure tree, found {len(PROC_FILES)} files")
    assert CATALOG, "tc_catalog.yaml produced no commands"


def test_index_is_parseable_and_covers_the_tree():
    index = yaml.safe_load(INDEX_FILE.read_text())["procedures"]
    assert index, "procedure_index.yaml lists no procedures"
    for entry in index:
        assert entry.get("id"), f"index entry without id: {entry}"
        assert entry.get("file"), f"{entry.get('id')}: index entry without file"


def test_catalog_aliases_do_not_shadow_a_real_command():
    """An alias that collides with another command's name is ambiguous."""
    entries = yaml.safe_load(CATALOG_FILE.read_text())["commands"]
    names = {e["name"] for e in entries}
    collisions = []
    for entry in entries:
        for alias in entry.get("aliases", []) or []:
            if alias in names:
                collisions.append((entry["name"], alias))
    assert not collisions, (
        f"tc_catalog aliases shadow real command names: {collisions}")


# ── Procedure → catalog resolution ──────────────────────────────────

@pytest.mark.parametrize("path", PROC_FILES, ids=PROC_IDS)
def test_every_referenced_command_exists_in_the_catalog(path):
    """Every **TC:** mnemonic must be sendable from the MCS."""
    unknown = []
    for ref in _tc_references(path):
        mnemonic = ref["mnemonic"]
        if mnemonic in GENERIC_S8_WRAPPERS or mnemonic in CATALOG:
            continue
        unknown.append(f"line {ref['line']}: {ref['raw']!r}")
    assert not unknown, (
        f"{path.relative_to(PROC_DIR)} references commands that are not in "
        f"tc_catalog.yaml (by name or alias):\n  " + "\n  ".join(unknown))


@pytest.mark.parametrize("path", PROC_FILES, ids=PROC_IDS)
def test_generic_s8_wrappers_declare_a_func_id(path):
    """`FUNC_PERFORM` / `S8_COMMAND` are meaningless without a func_id."""
    bad = []
    for ref in _tc_references(path):
        if ref["mnemonic"] in GENERIC_S8_WRAPPERS and ref["func_id"] is None:
            bad.append(f"line {ref['line']}: {ref['raw']!r}")
    assert not bad, (
        f"{path.relative_to(PROC_DIR)} uses a generic S8 wrapper with no "
        f"func_id — the operator has nothing to send:\n  " + "\n  ".join(bad))


@pytest.mark.parametrize("path", PROC_FILES, ids=PROC_IDS)
def test_referenced_func_ids_route_to_a_real_handler(path):
    """A func_id quoted in a procedure must not fall through the S8 map."""
    dead = []
    for ref in _tc_references(path):
        if ref["func_id"] is None:
            continue
        if not _routed_subsystems(ref["func_id"]):
            dead.append(f"line {ref['line']}: func_id {ref['func_id']} "
                        f"({ref['raw']!r})")
    assert not dead, (
        f"{path.relative_to(PROC_DIR)} quotes S8 func_ids that route nowhere:"
        f"\n  " + "\n  ".join(dead))


@pytest.mark.parametrize("path", PROC_FILES, ids=PROC_IDS)
def test_annotations_match_the_catalog(path):
    """(Service N, Subtype M) in the doc must match the catalog entry.

    Known disagreements are listed in KNOWN_ANNOTATION_DRIFT with the reason;
    anything new fails here.
    """
    drift = []
    for ref in _tc_references(path):
        entry = CATALOG.get(ref["mnemonic"])
        if entry is None or ref["service"] is None:
            continue
        actual = (entry["service"], entry["subtype"])
        declared = (ref["service"], ref["subtype"])
        if actual == declared:
            continue
        key = (ref["mnemonic"], ref["service"], ref["subtype"])
        if key in KNOWN_ANNOTATION_DRIFT:
            continue
        drift.append(
            f"line {ref['line']}: {ref['mnemonic']} annotated S{declared[0]}."
            f"{declared[1]} but tc_catalog has S{actual[0]}.{actual[1]}")
    assert not drift, (
        f"{path.relative_to(PROC_DIR)} has service/subtype annotations that "
        f"disagree with tc_catalog.yaml:\n  " + "\n  ".join(drift))


# ── Allowlist hygiene ───────────────────────────────────────────────

def test_annotation_drift_allowlist_has_no_stale_entries():
    """Every allowlisted disagreement must still exist in the tree.

    Without this, a fixed procedure leaves a permanent excuse behind and the
    next real regression slips through under the same key.
    """
    seen = set()
    for path in PROC_FILES:
        for ref in _tc_references(path):
            entry = CATALOG.get(ref["mnemonic"])
            if entry is None or ref["service"] is None:
                continue
            if (entry["service"], entry["subtype"]) != (ref["service"],
                                                        ref["subtype"]):
                seen.add((ref["mnemonic"], ref["service"], ref["subtype"]))
    stale = set(KNOWN_ANNOTATION_DRIFT) - seen
    assert not stale, (
        f"KNOWN_ANNOTATION_DRIFT lists disagreements that no longer exist — "
        f"remove them: {sorted(stale)}")


def test_func_intent_drift_allowlist_is_accurate():
    """The pinned func_id → subsystem mapping must still hold.

    These func_ids are quoted in obc_reboot.md / software_upload.md as OBDH
    operations (reboot, boot-source switch, memory bank select) but route to
    other subsystems. Pinned so a future S8 remap — intentional or not — is
    visible here.
    """
    wrong = []
    for func_id, expected in sorted(KNOWN_FUNC_INTENT_DRIFT.items()):
        routed = _routed_subsystems(func_id)
        if expected not in routed:
            wrong.append(f"func {func_id}: expected {expected}, routes to "
                         f"{sorted(routed) or 'nothing'}")
    assert not wrong, (
        "KNOWN_FUNC_INTENT_DRIFT is out of date:\n  " + "\n  ".join(wrong))


@pytest.mark.xfail(
    strict=True,
    reason="obc_reboot.md and software_upload.md quote S8 func_ids for OBDH "
           "operations (reboot / boot-source / memory bank) that route to "
           "AOCS, EPS, payload and TCS handlers instead. Reported to Flight "
           "Ops; this xfail flips to a pass — and must then be deleted — once "
           "the procedures (or the S8 map) are corrected.",
)
def test_obdh_procedures_command_the_obdh():
    """Summary gate on the outstanding procedure/S8 review item."""
    offenders = []
    for path in PROC_FILES:
        if path.name not in ("obc_reboot.md", "software_upload.md"):
            continue
        for ref in _tc_references(path):
            if ref["func_id"] is None:
                continue
            routed = _routed_subsystems(ref["func_id"])
            if "obdh" not in routed:
                offenders.append(
                    f"{path.name}:{ref['line']} func {ref['func_id']} -> "
                    f"{sorted(routed)}")
    assert not offenders, (
        "OBDH procedure steps that do not reach the OBDH model:\n  "
        + "\n  ".join(offenders))
