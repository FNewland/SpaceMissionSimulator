"""GO/NO-GO matrix config ↔ manual mirror (POSITION_NEEDS G7).

The machine-readable matrix is configs/eosat1/mcs/gonogo_matrix.yaml
(served at /api/gonogo-matrix); the table in
manual/10_desk_procedures.md §"GO/NO-GO Call Matrix" is its mirror for
the printed manual. This test holds the two in step: same situations, in
the same order, and the manual notes that it is a mirror.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"


def _yaml_rows():
    data = yaml.safe_load(
        (CONFIG / "mcs" / "gonogo_matrix.yaml").read_text())
    return data["gonogo_matrix"]


def _manual_rows():
    text = (CONFIG / "manual" / "10_desk_procedures.md").read_text()
    section = text.split("## GO/NO-GO Call Matrix", 1)[1]
    section = section.split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("Situation", "") \
                or set(cells[0]) <= {"-", " "}:
            continue
        rows.append(cells)
    return rows


def test_same_situations_same_order():
    yaml_situations = [r["situation"] for r in _yaml_rows()]
    manual_situations = [r[0] for r in _manual_rows()]
    assert yaml_situations == manual_situations, (
        "gonogo_matrix.yaml and the manual's GO/NO-GO Call Matrix table "
        "have diverged — edit the YAML (canonical) and mirror the manual")


def test_fd_decisions_match():
    yaml_fd = [r["fd_decision"] for r in _yaml_rows()]
    manual_fd = [r[3] for r in _manual_rows()]
    assert yaml_fd == manual_fd


def test_manual_declares_the_mirror():
    text = (CONFIG / "manual" / "10_desk_procedures.md").read_text()
    section = text.split("## GO/NO-GO Call Matrix", 1)[1][:600]
    assert "gonogo_matrix.yaml" in section, (
        "the manual section must say it mirrors mcs/gonogo_matrix.yaml")


def test_who_responds_are_position_ids_or_declared_literals():
    positions = set(yaml.safe_load(
        (CONFIG / "mcs" / "positions.yaml").read_text())["positions"])
    literals = {"ALL", "affected"}
    for row in _yaml_rows():
        for who in row["who_responds"]:
            assert who in positions or who in literals, (
                f"{row['situation']}: who_responds {who!r} is neither a "
                "position id nor a declared literal (ALL/affected)")
