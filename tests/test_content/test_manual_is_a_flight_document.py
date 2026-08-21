"""The operations manual must read as a real-mission document.

Raised 2026-08-21:

    "the textbook and MCS manual are meant to be for real missions, not for a
    simulated environment"

The manual an operator works from has to be true of the spacecraft, not of the
training rig behind it. A chapter that says telemetry is "evaluated every
simulation tick", or that a recovery is "an instructor action", or that a
hardware definition lives in a Python module, is describing the simulator.

Documentation ABOUT the training system is legitimate and stays on disk —
installation guides, the software architecture, the RF simulation layer. Those
opt out of the operations manual by carrying ``**Manual-Scope:** system`` in
their header, and the MCS manual listing filters them out.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MANUAL = REPO / "configs" / "eosat1" / "manual"
SERVER = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "server.py"

SCOPE_MARK = "**Manual-Scope:** system"

#: Words that betray the training rig. Matched case-insensitively on prose.
SIMULATOR_TELLS = [
    r"\bsimulat(?:or|ion|ed)\b",
    r"\binstructor\b",
    r"\bfailure injection\b",
    r"packages/smo-",
    r"\bsmo_simulator\b",
    r"\bsmo_common\.",
]


def _operational_chapters() -> list[Path]:
    """Manual pages an operator is served — i.e. not system-scope."""
    out = []
    for md in sorted(MANUAL.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        if SCOPE_MARK in text.split("\n## ", 1)[0]:
            continue                       # opted out in its header block
        out.append(md)
    return out


def _strip_scoped_appendices(text: str) -> str:
    """Drop any appendix that declares itself system-scope.

    A flight chapter may carry a clearly-marked appendix about the training
    rig — 12_time_and_clocks does, for the ground time source — as long as the
    operational body stands on its own.
    """
    parts = re.split(r"\n## (?=Appendix)", text)
    kept = [parts[0]]
    for part in parts[1:]:
        if SCOPE_MARK not in part.split("\n\n", 3)[0] + part[:400]:
            kept.append(part)
    return "\n## ".join(kept)


@pytest.mark.parametrize("chapter", _operational_chapters(),
                         ids=lambda p: p.name)
def test_no_simulator_language_in_an_operational_chapter(chapter: Path):
    body = _strip_scoped_appendices(chapter.read_text(encoding="utf-8"))
    offenders = []
    for i, line in enumerate(body.splitlines(), 1):
        for pat in SIMULATOR_TELLS:
            if re.search(pat, line, re.I):
                offenders.append(f"  {i}: {line.strip()[:100]}")
                break
    assert not offenders, (
        f"{chapter.name} is served to operators but describes the training "
        "system:\n" + "\n".join(offenders) +
        f"\n\nEither rewrite it for a real mission, or mark the document "
        f"'{SCOPE_MARK}' in its header if it is training-system documentation.")


def test_classification_is_not_simulation_only():
    """Every chapter carried this banner. It is a flight document now."""
    offenders = [md.name for md in MANUAL.glob("*.md")
                 if "For Simulation Use Only" in md.read_text(encoding="utf-8")]
    assert not offenders, (
        f"chapters still classified 'For Simulation Use Only': {offenders}")


def test_the_system_scope_documents_are_still_present():
    """The opt-out must hide them from operators, not delete them."""
    expected = {"installation_guide.md", "installation_guide_rfsim.md",
                "software_architecture.md", "11_rf_simulation.md"}
    present = {md.name for md in MANUAL.glob("*.md")
               if SCOPE_MARK in md.read_text(encoding="utf-8")}
    missing = expected - present
    assert not missing, (
        f"training-system documents lost their scope marker: {missing}")


def test_the_manual_listing_filters_system_scope():
    """The marker has to mean something at the endpoint."""
    src = SERVER.read_text(encoding="utf-8")
    assert "Manual-Scope" in src, (
        "the MCS manual listing no longer filters system-scope documents, so "
        "installation guides and the software architecture will be served to "
        "operators as flight documentation")


def test_operational_chapters_were_actually_found():
    """Guard against the whole suite passing vacuously."""
    chapters = _operational_chapters()
    assert len(chapters) >= 12, (
        f"only {len(chapters)} operational chapters found — the scope marker "
        "may have been applied too widely")
