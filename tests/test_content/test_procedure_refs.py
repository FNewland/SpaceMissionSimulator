"""`activity_types.yaml` ↔ `procedure_index.yaml` must agree (brief 08 gap 5 /
O-5).

The planner's `procedure_ref` is what an operator follows when the schedule
says "run this activity". Five of the nine refs were once off by one — a
`data_dump` activity pointed at NOM-002 *Imaging Session*, a
`momentum_desaturation` at NOM-006 *Software Upload* — while the reverse
`runnable:` links in `procedure_index.yaml` pointed the *other* way and were
correct. No test checked `procedure_ref` at all: `test_procedure_links_content`
validates only `runnable:`.

An off-by-one in this pairing is invisible in review (every id is well-formed
and every target exists) and catastrophic in a classroom, so the check has to
be semantic as well as referential.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"

ACTIVITIES = yaml.safe_load(
    (CONFIG / "planning" / "activity_types.yaml").read_text())["activity_types"]
INDEX = yaml.safe_load(
    (CONFIG / "procedures" / "procedure_index.yaml").read_text())["procedures"]

BY_ID = {p["id"]: p for p in INDEX}
ACT_BY_NAME = {a["name"]: a for a in ACTIVITIES}

#: Words that mean the same thing on the two sides of the link. Without these
#: the semantic check would reject two correct pairings, and a bare
#: "these two strings share a word" rule is too weak to catch an off-by-one.
SYNONYMS: dict[str, set[str]] = {
    "housekeeping": {"health", "housekeeping", "hk"},
    "collection": {"check", "collection", "routine"},
    "thermal": {"thermal", "tcs"},
    "rebalance": {"verification", "rebalance", "balance"},
    "desaturation": {"management", "desaturation", "desat", "unload"},
    "fdir": {"fdir", "anomaly", "fault"},
    "dump": {"dump", "downlink", "download"},
    "pass": {"pass", "session"},
    "shedding": {"shed", "shedding"},
    "manoeuvre": {"manoeuvre", "maneuver", "maintenance"},
}

_WORD = re.compile(r"[a-z]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _expand(words: set[str]) -> set[str]:
    out = set(words)
    for w in words:
        out |= SYNONYMS.get(w, set())
    return out


# ── 1. Referential integrity ───────────────────────────────────────────

def test_every_procedure_ref_resolves():
    bad = []
    for act in ACTIVITIES:
        ref = act.get("procedure_ref")
        if ref is None:
            continue
        if ref not in BY_ID:
            bad.append(f"{act['name']} -> {ref}")
    assert not bad, f"activity_types.yaml procedure_ref does not resolve: {bad}"


def test_every_activity_has_a_procedure_ref():
    """An activity a student can schedule with no procedure to follow is a
    hole in the courseware, not a convenience."""
    missing = [a["name"] for a in ACTIVITIES if not a.get("procedure_ref")]
    assert not missing, f"activity types with no procedure_ref: {missing}"


def test_procedure_refs_are_not_shared():
    """Two activities pointing at one procedure is the signature of the
    off-by-one: the shifted refs collide before they run out."""
    seen: dict[str, str] = {}
    dupes = []
    for act in ACTIVITIES:
        ref = act.get("procedure_ref")
        if ref in seen:
            dupes.append(f"{ref}: {seen[ref]} and {act['name']}")
        seen[ref] = act["name"]
    assert not dupes, f"procedure_ref shared between activities: {dupes}"


# ── 2. The reverse link must agree ──────────────────────────────────────

def test_runnable_links_round_trip():
    """Where `procedure_index.yaml` carries `runnable: {kind: activity_type}`,
    that activity's `procedure_ref` must name this procedure."""
    bad = []
    for proc in INDEX:
        run = proc.get("runnable") or {}
        if run.get("kind") != "activity_type":
            continue
        act_name = run.get("ref")
        act = ACT_BY_NAME.get(act_name)
        if act is None:
            bad.append(f"{proc['id']} runnable -> unknown activity {act_name!r}")
            continue
        if act.get("procedure_ref") != proc["id"]:
            bad.append(
                f"{proc['id']} claims activity {act_name}, but that activity's "
                f"procedure_ref is {act.get('procedure_ref')}")
    assert not bad, f"runnable/procedure_ref disagreement: {bad}"


def test_runnable_activity_refs_exist():
    bad = [f"{p['id']} -> {p['runnable']['ref']}" for p in INDEX
           if (p.get("runnable") or {}).get("kind") == "activity_type"
           and p["runnable"]["ref"] not in ACT_BY_NAME]
    assert not bad, f"runnable activity_type refs that do not resolve: {bad}"


# ── 3. Semantic plausibility ───────────────────────────────────────────

@pytest.mark.parametrize("activity", ACTIVITIES, ids=lambda a: a["name"])
def test_procedure_name_is_plausible_for_the_activity(activity):
    """The off-by-one produced pairings that are *referentially* perfect and
    obviously wrong to a human: `data_dump` -> "Imaging Session". Compare the
    vocabularies."""
    ref = activity.get("procedure_ref")
    proc = BY_ID.get(ref)
    assert proc is not None, f"{activity['name']} -> {ref} does not resolve"

    act_words = _expand(_words(activity["name"]) |
                        _words(str(activity.get("description", ""))))
    proc_words = _expand(_words(proc["name"]))
    overlap = act_words & proc_words
    assert overlap, (
        f"activity {activity['name']!r} points at {ref} "
        f"{proc['name']!r}, which shares no vocabulary with it — this is what "
        f"the off-by-one drift looked like. Words: {sorted(_words(activity['name']))} "
        f"vs {sorted(_words(proc['name']))}")


def test_synonym_table_has_no_dead_entries():
    """A synonym kept alive after the config changed hides the next drift."""
    corpus = set()
    for act in ACTIVITIES:
        corpus |= _words(act["name"]) | _words(str(act.get("description", "")))
    for proc in INDEX:
        corpus |= _words(proc["name"])
    dead = [k for k in SYNONYMS if k not in corpus]
    assert not dead, f"SYNONYMS keys no longer appear in either file: {dead}"


# ── 4. The procedure file behind the ref actually exists ───────────────

def test_referenced_procedures_have_a_document():
    missing = []
    for act in ACTIVITIES:
        proc = BY_ID.get(act.get("procedure_ref"))
        if proc is None:
            continue
        rel = proc.get("file")
        if not rel or not (CONFIG / "procedures" / rel).exists():
            missing.append(f"{proc['id']} -> {rel}")
    assert not missing, (
        f"procedure_ref resolves to an index entry with no document: {missing}")
