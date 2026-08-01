"""Content tests for the shipped scenario library (configs/eosat1/scenarios/).

Historically only a handful of the 31 scenario YAMLs were ever loaded by a test,
so a typo'd ``failure:`` key, an unimplemented ``action:`` or a missing
``duration_s`` shipped silently and only showed up as "the scenario does
nothing" during a live training session (defect #34 / #52 class).

These tests drive every scenario file through the real ``ScenarioEngine``
loader and cross-check every reference against a code-derived catalog:

* every file parses and produces a definition with a name/duration/briefing;
* every ``action:`` is one the engine can actually execute;
* every ``inject`` event names a failure mode the target subsystem model
  really implements (scraped from ``inject_failure()`` source);
* the loader reports zero validation issues across the whole library;
* a scenario driven past ``duration_s`` auto-stops, clears the failures it
  injected, and produces a debrief.
"""
import ast
import re
from pathlib import Path

import pytest

from smo_simulator.scenario_engine import (
    SUPPORTED_ACTIONS,
    ScenarioEngine,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO_ROOT / "configs" / "eosat1" / "scenarios"
MODEL_DIR = (REPO_ROOT / "packages" / "smo-simulator" / "src"
             / "smo_simulator" / "models")

# Scenario/instructor alias -> model file. "adcs" is the UI alias for "aocs".
SUBSYSTEM_MODELS = {
    "eps": "eps_basic.py",
    "aocs": "aocs_basic.py",
    "adcs": "aocs_basic.py",
    "tcs": "tcs_basic.py",
    "ttc": "ttc_basic.py",
    "obdh": "obdh_basic.py",
    "payload": "payload_basic.py",
    "prop": "prop_basic.py",
}

VALID_DIFFICULTIES = {"BASIC", "BEGINNER", "INTERMEDIATE", "ADVANCED"}

SCENARIO_FILES = sorted(SCENARIO_DIR.glob("*.yaml"))
SCENARIO_IDS = [p.stem for p in SCENARIO_FILES]


def _model_failures(model_file: str) -> set[str]:
    """Failure names handled by a model's inject_failure(), from its source."""
    src = (MODEL_DIR / model_file).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "inject_failure":
            body = ast.get_source_segment(src, node)
            return set(re.findall(r'failure\s*==\s*["\']([\w]+)["\']', body))
    raise AssertionError(f"inject_failure() not found in {model_file}")


def _failure_catalog() -> dict[str, set[str]]:
    return {sub: _model_failures(f) for sub, f in SUBSYSTEM_MODELS.items()}


class _FakeFailureManager:
    """Records inject/clear calls without touching a real spacecraft model."""

    def __init__(self):
        self.injected: list[dict] = []
        self.cleared: list[str] = []
        self.clear_all_calls = 0

    def inject(self, subsystem="", failure="", magnitude=1.0, onset="step",
               duration_s=0.0, **extra):
        fid = f"{subsystem}.{failure}.{len(self.injected)}"
        self.injected.append({
            "id": fid, "subsystem": subsystem, "failure": failure,
            "magnitude": magnitude, "onset": onset, "duration_s": duration_s,
            "extra": extra,
        })
        return fid

    def clear(self, failure_id):
        self.cleared.append(failure_id)
        return True

    def clear_all(self):
        self.clear_all_calls += 1

    def active_ids(self) -> set[str]:
        return {f["id"] for f in self.injected} - set(self.cleared)


def _load_all() -> tuple[ScenarioEngine, _FakeFailureManager]:
    fm = _FakeFailureManager()
    engine = ScenarioEngine(failure_manager=fm)
    engine.load_scenarios_from_dir(SCENARIO_DIR)
    return engine, fm


# ── Library-level invariants ────────────────────────────────────────

def test_scenario_directory_is_not_empty():
    """Guards against the glob silently matching nothing."""
    assert len(SCENARIO_FILES) >= 30, (
        f"expected the shipped scenario library, found {len(SCENARIO_FILES)} "
        f"files in {SCENARIO_DIR}")


def test_every_scenario_file_loads_into_a_definition():
    """Every YAML on disk becomes a loadable scenario definition."""
    engine, _ = _load_all()
    listed = engine.list_scenarios()
    assert len(listed) == len(SCENARIO_FILES), (
        f"{len(SCENARIO_FILES)} scenario files on disk but "
        f"{len(listed)} definitions loaded — a file failed to parse or two "
        f"files share a scenario name")


def test_loader_reports_zero_validation_issues():
    """The whole shipped library must validate clean through the real loader.

    ``load_scenarios_from_dir`` skips events with an unknown action or an
    unimplemented failure mode and records them as load issues; any issue here
    means a scenario ships with dead events.
    """
    engine, _ = _load_all()
    issues = engine.load_issues()
    assert issues == [], (
        "scenario library has load-time validation issues:\n  "
        + "\n  ".join(issues))


# ── Per-file structural checks ──────────────────────────────────────

@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_declares_required_metadata(path):
    """Each scenario declares a name, a positive duration and a briefing."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    assert data.get("name"), f"{path.name}: missing top-level 'name'"
    duration = data.get("duration_s")
    assert duration is not None, f"{path.name}: missing 'duration_s'"
    assert float(duration) > 0, f"{path.name}: duration_s must be positive"
    assert data.get("briefing", "").strip(), f"{path.name}: empty 'briefing'"
    difficulty = data.get("difficulty", "BASIC")
    assert difficulty in VALID_DIFFICULTIES, (
        f"{path.name}: difficulty {difficulty!r} not in {sorted(VALID_DIFFICULTIES)}")


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_uses_only_implemented_actions(path):
    """Every event action must be one ScenarioEngine._fire_event implements."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    for idx, ev in enumerate(data.get("events", []) or []):
        action = ev.get("action", "")
        assert action in SUPPORTED_ACTIONS, (
            f"{path.name}: event[{idx}] action {action!r} is not implemented "
            f"(supported: {sorted(SUPPORTED_ACTIONS)})")


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_events_can_fire(path):
    """Every event has a trigger — a time offset or a condition."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    for idx, ev in enumerate(data.get("events", []) or []):
        assert ev.get("time_offset_s") is not None or ev.get("condition"), (
            f"{path.name}: event[{idx}] has neither time_offset_s nor "
            f"condition — it can never fire")


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_injects_only_known_failures(path):
    """inject events (and initial_conditions failures) name real failure modes."""
    import yaml
    catalog = _failure_catalog()
    data = yaml.safe_load(path.read_text()) or {}

    injects = [(f"event[{i}]", ev.get("params", {}) or {})
               for i, ev in enumerate(data.get("events", []) or [])
               if ev.get("action") == "inject"]
    injects += [
        (f"initial_conditions.failures[{i}]", f)
        for i, f in enumerate(
            (data.get("initial_conditions", {}) or {}).get("failures", []) or [])
    ]

    for where, params in injects:
        subsystem = params.get("subsystem", "")
        failure = params.get("failure", "")
        assert subsystem and failure, (
            f"{path.name}: {where} inject is missing subsystem/failure")
        assert subsystem in catalog, (
            f"{path.name}: {where} targets unknown subsystem {subsystem!r} "
            f"(known: {sorted(catalog)})")
        assert failure in catalog[subsystem], (
            f"{path.name}: {where} injects {failure!r} which "
            f"{subsystem} does not implement "
            f"(known: {sorted(catalog[subsystem])})")


@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_expected_responses_are_well_formed(path):
    """Debrief scoring counts expected_responses — they must carry a category."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    for idx, resp in enumerate(data.get("expected_responses", []) or []):
        assert isinstance(resp, dict), (
            f"{path.name}: expected_responses[{idx}] is not a mapping")
        assert resp.get("category"), (
            f"{path.name}: expected_responses[{idx}] has no 'category'")


# ── Runtime: auto-stop, cleanup and debrief ─────────────────────────

@pytest.mark.parametrize("path", SCENARIO_FILES, ids=SCENARIO_IDS)
def test_scenario_auto_stops_at_declared_duration(path):
    """Fast-run each scenario past duration_s; it must deactivate itself.

    Generalises test_scenario_autostop.py (which covered one synthetic
    definition) to the whole shipped library. Ticks in coarse steps so the
    whole library runs in well under a second.
    """
    engine, fm = _load_all()
    import yaml
    name = (yaml.safe_load(path.read_text()) or {}).get("name")
    assert engine.start(name), f"{path.name}: scenario {name!r} would not start"

    defn = engine._scenarios[name]
    step = max(1.0, defn.duration_s / 50.0)
    ticked = 0.0
    while engine.is_active() and ticked <= defn.duration_s + 2 * step:
        engine.tick(step, {})
        ticked += step

    assert not engine.is_active(), (
        f"{path.name}: still active after {ticked:.0f}s "
        f"(duration_s={defn.duration_s})")
    assert fm.active_ids() == set(), (
        f"{path.name}: failures {sorted(fm.active_ids())} leaked past "
        f"scenario end")

    debrief = engine.last_debrief()
    assert debrief is not None, f"{path.name}: no debrief after auto-stop"
    assert debrief.name == name
    assert debrief.duration_s >= defn.duration_s


def test_full_fast_run_fires_events_and_debriefs():
    """One full fast run end-to-end: events fire, responses score, debrief.

    Uses the scenario with the most inject events so the inject → auto-clear →
    debrief path is genuinely exercised rather than trivially satisfied.
    """
    import yaml
    best, best_injects = None, -1
    for path in SCENARIO_FILES:
        data = yaml.safe_load(path.read_text()) or {}
        n = sum(1 for ev in (data.get("events") or [])
                if ev.get("action") == "inject"
                and ev.get("time_offset_s") is not None)
        if n > best_injects:
            best, best_injects = data, n
    assert best_injects >= 1, "no scenario has a timed inject event"

    name = best["name"]
    engine, fm = _load_all()
    assert engine.start(name)

    defn = engine._scenarios[name]
    engine.record_response("detect", "operator saw the alarm")
    engine.record_response("isolate", "operator isolated the fault")

    step = max(1.0, defn.duration_s / 100.0)
    ticked = 0.0
    while engine.is_active() and ticked <= defn.duration_s + 2 * step:
        engine.tick(step, {})
        ticked += step

    assert fm.injected, f"{name}: no failure was ever injected during the run"
    assert not engine.is_active()
    assert fm.active_ids() == set(), "auto-stop did not clear injected failures"

    debrief = engine.last_debrief()
    assert debrief is not None
    assert debrief.name == name
    assert debrief.mttd_s is not None, "detect response not timed in debrief"
    assert debrief.mtti_s is not None, "isolate response not timed in debrief"
    assert 0.0 <= debrief.score_pct <= 100.0
    assert len(debrief.responses) == 2


def test_scenario_can_be_rerun_after_auto_stop():
    """fired flags reset on auto-stop so a scenario can be replayed."""
    import yaml
    engine, fm = _load_all()
    path = SCENARIO_FILES[0]
    name = (yaml.safe_load(path.read_text()) or {}).get("name")
    defn = engine._scenarios[name]

    for run in range(2):
        assert engine.start(name), f"run {run}: start failed"
        ticked = 0.0
        step = max(1.0, defn.duration_s / 20.0)
        while engine.is_active() and ticked <= defn.duration_s + 2 * step:
            engine.tick(step, {})
            ticked += step
        assert not engine.is_active(), f"run {run}: did not auto-stop"
        assert all(not ev.fired for ev in defn.events), (
            f"run {run}: fired flags not reset")
