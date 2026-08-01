"""Config-side content checks for the procedure convergence (defect #83).

`tests/test_mcs/test_procedure_convergence.py` proves the MCS resolves and runs
the linked procedures. This module checks the SHIPPED CONTENT on its own, with
no server: that every `runnable:` link in `procedure_index.yaml` is
well-formed and points at something that really exists, that the documents the
`markdown` adapter is pointed at really carry `**TC:**` annotations, and that
the image-size reconciliation (defect #82) is consistent across the config
files that duplicate it.
"""
from pathlib import Path

import pytest
import yaml

from smo_common.procedures import (
    from_activity_sequence, from_markdown, validate_steps,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
PROC_DIR = CONFIG_DIR / "procedures"

VALID_KINDS = {"activity_type", "steps_file", "markdown"}


def _index() -> list[dict]:
    return yaml.safe_load((PROC_DIR / "procedure_index.yaml").read_text())["procedures"]


def _activity_types() -> dict[str, dict]:
    data = yaml.safe_load(
        (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
    return {a["name"]: a for a in data["activity_types"]}


LINKED = [e for e in _index() if e.get("runnable")]


# ── runnable: links ─────────────────────────────────────────────────

def test_exemplar_links_are_present():
    """Framework plus exemplars — the four the convergence shipped with."""
    linked_ids = {e["id"] for e in LINKED}
    assert {"NOM-002", "NOM-003", "NOM-008", "NOM-009"} <= linked_ids


@pytest.mark.parametrize("entry", LINKED, ids=[e["id"] for e in LINKED])
def test_link_is_well_formed(entry):
    link = entry["runnable"]
    assert isinstance(link, dict), f"{entry['id']}: runnable must be a mapping"
    assert link.get("kind") in VALID_KINDS, f"{entry['id']}: {link}"
    if link["kind"] in ("activity_type", "steps_file"):
        assert link.get("ref"), f"{entry['id']}: {link['kind']} needs a ref"


@pytest.mark.parametrize("entry", LINKED, ids=[e["id"] for e in LINKED])
def test_link_target_exists_and_produces_valid_steps(entry):
    link = entry["runnable"]
    kind = link["kind"]

    if kind == "activity_type":
        activity = _activity_types().get(link["ref"])
        assert activity is not None, f"{entry['id']} -> unknown activity {link['ref']}"
        steps = from_activity_sequence(activity.get("command_sequence", []))
    elif kind == "steps_file":
        target = PROC_DIR / link["ref"]
        assert target.exists(), f"{entry['id']} -> missing {link['ref']}"
        steps = (yaml.safe_load(target.read_text()) or {}).get("steps", [])
    else:
        document = PROC_DIR / entry["file"]
        assert document.exists(), f"{entry['id']} -> missing {entry['file']}"
        steps = from_markdown(document.read_text(encoding="utf-8"))

    assert steps, f"{entry['id']} resolves to an empty step list"
    assert validate_steps(steps, None) == [], entry["id"]


@pytest.mark.parametrize("entry", LINKED, ids=[e["id"] for e in LINKED])
def test_linked_document_still_exists(entry):
    """A runnable link is only useful next to the prose it automates."""
    assert (PROC_DIR / entry["file"]).exists()


def test_markdown_linked_documents_carry_tc_annotations():
    """`kind: markdown` derives steps from `**TC:**` lines — a document
    without them would silently resolve to nothing."""
    for entry in LINKED:
        if entry["runnable"]["kind"] != "markdown":
            continue
        text = (PROC_DIR / entry["file"]).read_text(encoding="utf-8")
        assert "**TC:**" in text, entry["id"]


def test_unlinked_procedures_stay_documents():
    """The 64 markdown procedures are the operations manual; only a small,
    explicitly chosen subset is automated (docs/PROCEDURE_SCHEMA.md §5)."""
    assert len(LINKED) < len(_index()) / 2


# ── Image size reconciliation (defect #82) ──────────────────────────

def test_image_size_is_consistent_across_the_configs():
    """payload.yaml is canonical; the planner's activity catalogue must agree.

    The size used to be triplicated (payload.yaml, activity_types.yaml,
    imaging_planner.IMAGING_DATA_MB) at 800 MB.
    """
    from smo_planner.imaging_planner import IMAGING_DATA_MB

    payload = yaml.safe_load(
        (CONFIG_DIR / "subsystems" / "payload.yaml").read_text())
    imaging = _activity_types()["imaging_pass"]

    assert payload["image_size_mb"] == 4.0
    assert imaging["data_volume_mb"] == payload["image_size_mb"]
    assert IMAGING_DATA_MB == payload["image_size_mb"]


def test_activity_data_volumes_are_physically_defensible():
    """Each activity's data volume must be reachable at its own duration.

    The 80 Mbps figure is the payload's focal-plane readout rate — the
    absolute ceiling on what any activity can generate. The pre-#82 numbers
    (calibration 50 MB from a gyro calibration, housekeeping 10 MB from one
    S3.27 request) were scaled to the 800 MB-image world and implied
    megabit-per-second housekeeping.
    """
    payload = yaml.safe_load(
        (CONFIG_DIR / "subsystems" / "payload.yaml").read_text())
    max_rate_mb_s = payload["data_rate_mbps"] / 8.0

    for name, activity in _activity_types().items():
        volume = float(activity.get("data_volume_mb", 0) or 0.0)
        if volume <= 0:
            continue
        duration = float(activity["duration_s"])
        assert volume <= max_rate_mb_s * duration, (
            f"{name} generates {volume} MB in {duration} s — above the "
            f"payload's own {payload['data_rate_mbps']} Mbps readout rate")

    # ...and the non-imaging activities must not out-generate an image.
    image_mb = payload["image_size_mb"]
    for name in ("calibration", "housekeeping_collection"):
        assert _activity_types()[name]["data_volume_mb"] < image_mb, (
            f"{name} generates more than a whole image")
