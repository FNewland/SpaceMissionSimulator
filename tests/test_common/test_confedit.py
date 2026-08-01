"""Safe config editor library (Phase F, brief 06 §B).

Both history backends (shadow git and snapshot fallback) run the same
suite via parametrisation — the interface contract is identical. All
mutating tests operate on a COPY of ``configs/eosat1`` under tmp_path with
an isolated history dir; the real tree is only ever read.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.confedit import ConfigEditor  # noqa: E402
from smo_common.confedit.classification import (  # noqa: E402
    GROUP_ORDER,
    classify,
)
from smo_common.confedit.editor import ConfigEditError  # noqa: E402
from smo_common.confedit.formgen import (  # noqa: E402
    form_spec_for,
    schema_model_for,
    unknown_key_report,
)

CONFIG_DIR = REPO / "configs" / "eosat1"
KNOWN_GROUPS = {k for k, _ in GROUP_ORDER}


# ── classification ───────────────────────────────────────────────────────

def test_classification_covers_every_config_file():
    """Every yaml/md/xml actually present under configs/eosat1 classifies
    into a known group — including files other phases added (prop, obcp,
    reports)."""
    walked = 0
    for p in CONFIG_DIR.rglob("*"):
        if not p.is_file() or p.suffix not in (".yaml", ".yml", ".md", ".xml"):
            continue
        rp = p.relative_to(CONFIG_DIR).as_posix()
        cls = classify(rp)
        walked += 1
        assert cls.group in KNOWN_GROUPS, rp
        # nothing real may land in the junk catch-all
        assert cls.group != "junk", f"{rp} fell through the classification"
    assert walked > 100  # the tree is large; a tiny count means a bad walk


@pytest.mark.parametrize("rp,editable,group", [
    ("subsystems/memory_map.yaml", False, "dead"),     # dead: no reader
    ("aocs/aocs_mode_codes.yaml", False, "dead"),      # dead: no reader
    ("xtce/eosat1.xml", False, "generated"),
    ("smdl/eps.xml", False, "generated"),
    ("subsystems/prop.yaml", True, "subsystem"),       # Phase D addition
    ("subsystems/eps.yaml", True, "subsystem"),
    ("telemetry/hk_structures.yaml", True, "interface"),
    ("scenarios/nominal_ops.yaml", True, "scenario"),
    ("reports/pass_report.yaml", True, "reports"),
    ("obcp/04_orbit_manoeuvre.yaml", True, "simulation"),
    ("mcs/positions.yaml", True, "mcs"),
    ("manual/01_eps.md", True, "docs"),
])
def test_classification_spot_checks(rp, editable, group):
    cls = classify(rp)
    assert cls.editable is editable
    assert cls.group == group


def test_interface_files_marked_and_restart_scopes_populated():
    assert classify("telemetry/hk_structures.yaml").interface_defining
    assert classify("telemetry/parameters.yaml").interface_defining
    assert "simulator" in classify("simulation.yaml").restart_scope
    assert set(classify("mission.yaml").restart_scope) == {
        "simulator", "mcs", "planner"}


# ── schema-driven form generation ────────────────────────────────────────

def test_form_specs_generated_for_schema_mapped_files():
    for rp in ("mission.yaml", "orbit.yaml", "simulation.yaml",
               "subsystems/eps.yaml", "subsystems/ttc.yaml",
               "subsystems/prop.yaml", "scenarios/nominal_ops.yaml",
               "telemetry/hk_structures.yaml", "mcs/limits.yaml"):
        spec = form_spec_for(rp)
        assert spec is not None, rp
        fields = spec.get("fields") or spec.get("item_fields")
        assert fields, rp
        names = {f["name"] for f in fields}
        assert names  # every field carries name+type
        for f in fields:
            assert "type" in f


def test_form_spec_carries_descriptions_from_schema():
    spec = form_spec_for("mission.yaml")
    by_name = {f["name"]: f for f in spec["fields"]}
    assert "description" in by_name["spacecraft_apid"]


def test_unknown_key_report_finds_silently_dropped_keys():
    data = yaml.safe_load((CONFIG_DIR / "mission.yaml").read_text())
    data["definitely_bogus_key"] = 1
    report = unknown_key_report("mission.yaml", data)
    paths = {u["path"] for u in report}
    assert "definitely_bogus_key" in paths
    # the historically dead block the brief names
    assert "ground_segment" in paths or "ground_segment" not in data


def test_no_schema_for_markdown():
    assert schema_model_for("manual/01_eps.md") is None


# ── the apply pipeline, both backends ────────────────────────────────────

@pytest.fixture(params=["git", "snapshot"])
def editor(request, tmp_path) -> ConfigEditor:
    cfg = tmp_path / "configs" / "eosat1"
    shutil.copytree(CONFIG_DIR, cfg)
    return ConfigEditor(cfg, history_dir=tmp_path / "history",
                        backend_kind=request.param)


def _edit_scenario(editor: ConfigEditor) -> tuple[str, str]:
    orig = editor.get_file("scenarios/nominal_ops.yaml")["text"]
    new = re.sub(r"duration_s:\s*[\d.]+", "duration_s: 4242.0", orig,
                 count=1)
    assert new != orig
    return orig, new


def test_apply_records_history_with_trailers(editor):
    _, new = _edit_scenario(editor)
    res = editor.apply("scenarios/nominal_ops.yaml", new,
                       actor="franz", role="instructor",
                       message="stretch the scenario", reason="lab prep")
    assert res["revision"]
    assert res["restart_required"] == ["simulator"]
    assert "duration_s: 4242.0" in editor.get_file(
        "scenarios/nominal_ops.yaml")["text"]

    hist = editor.history("scenarios/nominal_ops.yaml")
    assert hist, "apply must create a revision"
    top = hist[0]
    assert top["trailers"]["Actor"] == "franz"
    assert top["trailers"]["Reason"] == "lab prep"
    assert top["trailers"]["RestartScope"] == "simulator"
    assert "stretch the scenario" in top["message"]

    # audit journal is append-only JSONL with the same facts
    audit = (editor.history_dir / "audit.jsonl").read_text().strip()
    entries = [json.loads(l) for l in audit.splitlines()]
    assert entries[-1]["action"] == "apply"
    assert entries[-1]["actor"] == "franz"


def test_revert_restores_byte_identical_as_new_revision(editor):
    orig, new = _edit_scenario(editor)
    editor.apply("scenarios/nominal_ops.yaml", new, message="edit",
                 reason="x")
    hist = editor.history("scenarios/nominal_ops.yaml")
    base_rev = hist[-1]["rev"]  # oldest (initial baseline)
    n_before = len(editor.history())
    res = editor.revert(base_rev, "scenarios/nominal_ops.yaml",
                        reason="undo")
    assert editor.get_file("scenarios/nominal_ops.yaml")["text"] == orig
    # a NEW revision, not a rewrite
    assert len(editor.history()) == n_before + 1
    assert res["revision"] != base_rev


def test_schema_violation_rejected_and_file_untouched(editor):
    before = editor.get_file("simulation.yaml")["text"]
    bad = before.replace("seed:", "seed: not_an_integer  # was:", 1)
    assert bad != before
    with pytest.raises(ConfigEditError) as ei:
        editor.apply("simulation.yaml", bad, message="bad", reason="")
    assert ei.value.status == 422
    assert any(i["severity"] == "error" and i["gate"] == "schema"
               for i in ei.value.issues)
    assert editor.get_file("simulation.yaml")["text"] == before


def test_out_of_range_rejected_and_file_untouched(editor):
    before = editor.get_file("orbit.yaml")["text"]
    bad = before.replace("lat_deg: 63.747", "lat_deg: 163.747")
    with pytest.raises(ConfigEditError) as ei:
        editor.apply("orbit.yaml", bad, message="bad", reason="")
    assert any(i["gate"] == "plausibility" and "lat_deg" in i["path"]
               for i in ei.value.issues if i["severity"] == "error")
    assert editor.get_file("orbit.yaml")["text"] == before


def test_crossfile_divergence_rejected_naming_both_sides(editor):
    """The brief's motivating example: mutate ttc.yaml dl_freq_mhz and the
    rf.frequencies check must name ground_stations.yaml / rfsim.yaml."""
    before = editor.get_file("subsystems/ttc.yaml")["text"]
    bad = before.replace("dl_freq_mhz: 2200.0", "dl_freq_mhz: 2300.0")
    assert bad != before
    with pytest.raises(ConfigEditError) as ei:
        editor.apply("subsystems/ttc.yaml", bad, message="detune", reason="")
    rf_issues = [i for i in ei.value.issues
                 if i.get("check") == "rf.frequencies"]
    assert rf_issues, "rf.frequencies must fire"
    named = {f for i in rf_issues for f in i["files"]}
    assert "planning/ground_stations.yaml" in named
    assert "rfsim.yaml" in named
    assert editor.get_file("subsystems/ttc.yaml")["text"] == before


def test_shed_ladder_crosscheck_fires(editor):
    before = editor.get_file("simulation.yaml")["text"]
    bad = before.replace("[30.0, 20.0, 10.0]", "[35.0, 20.0, 10.0]")
    if bad == before:
        pytest.skip("shed ladder literal not found in simulation.yaml")
    with pytest.raises(ConfigEditError) as ei:
        editor.apply("simulation.yaml", bad, message="x", reason="")
    assert any(i.get("check") == "eps.shed_ladder" for i in ei.value.issues)


def test_error_override_requires_force_and_reason(editor):
    before = editor.get_file("subsystems/ttc.yaml")["text"]
    bad = before.replace("dl_freq_mhz: 2200.0", "dl_freq_mhz: 2300.0")
    # force without reason still blocks
    with pytest.raises(ConfigEditError):
        editor.apply("subsystems/ttc.yaml", bad, message="m", force=True)
    res = editor.apply("subsystems/ttc.yaml", bad, message="m",
                       reason="deliberate detune exercise", force=True)
    assert res["revision"]
    hist = editor.history("subsystems/ttc.yaml")
    assert hist[0]["trailers"].get("Override")
    # put it back
    editor.apply("subsystems/ttc.yaml", before, message="restore",
                 reason="test cleanup")


@pytest.mark.parametrize("rp", [
    "subsystems/memory_map.yaml",   # dead
    "aocs/aocs_mode_codes.yaml",    # dead
    "xtce/eosat1.xml",              # generated
])
def test_must_not_edit_rejected_server_side(editor, rp):
    with pytest.raises(ConfigEditError) as ei:
        editor.apply(rp, "x: 1", message="nope", reason="nope")
    assert ei.value.status == 403
    with pytest.raises(ConfigEditError):
        editor.revert("HEAD", rp)


@pytest.mark.parametrize("rp", ["../../etc/passwd", "/etc/passwd",
                                "a/../../b.yaml"])
def test_path_escape_rejected(editor, rp):
    with pytest.raises(ConfigEditError) as ei:
        editor.get_file(rp)
    assert ei.value.status == 400


def test_baseline_tag_and_apply_round_trip(editor):
    orig, new = _edit_scenario(editor)
    editor.create_baseline("pre-exercise", "before the CERISE exercise")
    baselines = editor.list_baselines()
    assert any(b["name"] == "pre-exercise" for b in baselines)
    # diverge, then apply the baseline back
    editor.apply("scenarios/nominal_ops.yaml", new, message="diverge",
                 reason="x")
    assert "duration_s: 4242.0" in editor.get_file(
        "scenarios/nominal_ops.yaml")["text"]
    res = editor.apply_baseline("pre-exercise", reason="reset for class")
    assert res["revision"]
    assert editor.get_file("scenarios/nominal_ops.yaml")["text"] == orig
    assert set(res["restart_required"]) == {"simulator", "mcs", "planner"}


def test_diff_preview_text_and_semantic(editor):
    orig, new = _edit_scenario(editor)
    d = editor.diff("scenarios/nominal_ops.yaml", new)
    assert "-" in d["text_diff"] and "+duration_s" in d["text_diff"].replace(
        "+  duration_s", "+duration_s")
    changed = [s for s in d["semantic_diff"] if "duration_s" in s["path"]]
    assert changed and changed[0]["after"] == 4242.0
    # comment-only edit → empty semantic diff
    d2 = editor.diff("scenarios/nominal_ops.yaml",
                     orig + "\n# a trailing comment\n")
    assert d2["text_diff"]
    assert d2["semantic_diff"] == []


def test_shipped_config_is_crosscheck_clean(editor):
    errors = [i for i in editor.crosschecks() if i["severity"] == "error"]
    assert errors == []


def test_source_repo_untouched_by_shadow_history(editor, tmp_path):
    """The shadow history must never write into the source repo's .git."""
    # the editor's history lives under tmp_path, never under REPO
    assert str(editor.history_dir).startswith(str(tmp_path))
    assert not (editor.config_dir / ".git").exists()
