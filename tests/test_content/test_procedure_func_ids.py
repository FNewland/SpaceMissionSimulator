"""Phase E (brief 04 drift gate 4) — procedure func_id annotations.

A procedure markdown that writes ``PAYLOAD_CAPTURE ... (func_id 22)`` is
telling an operator to send a command that does not exist. Nineteen files
carried a whole stale numbering scheme (PAYLOAD_SET_MODE 20, CAPTURE 22,
DELETE_IMAGE 24, MARK_BAD_SEGMENT 25, FPA_COOLER 33, HEATER_BATTERY 30,
HEATER_SET_SETPOINT 34, TTC_PA_OFF 54, OBC_* 40-47, EPS_POWER_* 13/14 …).
``tools/procedure_audit.py`` now cross-checks every mnemonic/func_id pair
against ``tc_catalog.yaml``; this pins it at zero.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from procedure_audit import Catalog, audit_procedures  # noqa: E402


@pytest.fixture(scope="module")
def audit():
    cat = Catalog()
    cat.load()
    return audit_procedures(cat)


def test_no_procedure_contradicts_the_catalogue_func_ids(audit):
    _, findings = audit
    bad = [f"{f.procedure}: {f.detail}"
           for f in findings if f.category == "FUNC_ID_MISMATCH"]
    assert not bad, ("procedure func_id annotations disagree with "
                     "tc_catalog.yaml:\n  " + "\n  ".join(bad))


def test_the_cross_check_actually_fires(tmp_path, monkeypatch):
    """Guard the guard: a deliberately wrong annotation must be caught."""
    import procedure_audit as pa
    proc_dir = tmp_path / "procedures" / "contingency"
    proc_dir.mkdir(parents=True)
    (proc_dir / "bogus.md").write_text(
        "**Action:** `PAYLOAD_CAPTURE(scene_id=1, lines=2048)` (func_id 22)\n")
    monkeypatch.setattr(pa, "PROC_DIR", tmp_path / "procedures")
    cat = Catalog()
    cat.load()
    _, findings = pa.audit_procedures(cat)
    mismatches = [f for f in findings if f.category == "FUNC_ID_MISMATCH"]
    assert mismatches, "the func_id cross-check did not fire on a known-bad file"
    assert "PAYLOAD_CAPTURE annotated func_id 22" in mismatches[0].detail
    assert "says 28" in mismatches[0].detail


def test_payload_procedures_use_real_commands_not_phantom_parameters():
    """WP-10: no S20 write against a payload parameter id that is not a
    settable parameter, and no 'Subtype 3' labelled as a SET."""
    proc = REPO / "configs" / "eosat1" / "procedures" / "commissioning"
    offenders = []
    for name in ("payload_calibration.md", "first_light.md",
                 "fpa_cooler_activation.md"):
        text = (proc / name).read_text()
        for phantom in ("SET_PARAM(0x0630", "SET_PARAM(0x0634",
                        "SET_PARAM(0x0637", "SET_PARAM(0x0640",
                        "SET_PARAM(0x0641", "SET_PARAM(0x0620",
                        "SET_PARAM(0x0622", "GET_PARAM(0x0621"):
            if phantom in text:
                offenders.append(f"{name}: {phantom}")
        # S20 subtype 1 = SET, subtype 3 = GET. A GET_PARAM labelled
        # "Subtype 1" (or a SET labelled "Subtype 3") is the inversion that
        # shipped in every payload procedure.
        for line in text.splitlines():
            if "GET_PARAM" in line and "Subtype 1)" in line:
                offenders.append(f"{name}: GET_PARAM labelled Subtype 1")
            if "SET_PARAM" in line and "Subtype 3)" in line:
                offenders.append(f"{name}: SET_PARAM labelled Subtype 3")
    assert not offenders, "\n  ".join(["payload procedures still phantom:"] + offenders)
