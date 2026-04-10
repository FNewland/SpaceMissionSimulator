"""SMO Simulator — YAML Scenario Engine.

Loads scenario definitions from YAML, manages execution, tracks operator responses.
Refactored from scenario_engine.py — scenarios are now purely YAML-defined.
"""
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class ScenarioEvent:
    time_offset_s: float | None = None
    condition: str | None = None
    action: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    fired: bool = False


@dataclass
class ScenarioDefinition:
    name: str = ""
    difficulty: str = "BASIC"
    duration_s: float = 1800.0
    briefing: str = ""
    events: list[ScenarioEvent] = field(default_factory=list)
    expected_responses: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ScenarioDebrief:
    name: str = ""
    duration_s: float = 0.0
    score_pct: float = 0.0
    mttd_s: float | None = None
    mtti_s: float | None = None
    mttr_s: float | None = None
    responses: list[dict] = field(default_factory=list)


class ScenarioEngine:
    """Manages scenario loading, execution, and scoring."""

    def __init__(self, failure_manager=None, engine=None):
        self._fm = failure_manager
        self._engine = engine
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._active: Optional[ScenarioDefinition] = None
        self._elapsed: float = 0.0
        self._start_time: float = 0.0
        self._responses: list[dict] = []
        self._response_times: dict[str, float] = {}

    def load_scenarios_from_dir(self, scenario_dir: Path) -> None:
        """Load all YAML scenario files from a directory."""
        if not scenario_dir.exists():
            return
        for path in sorted(scenario_dir.glob("*.yaml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                events = []
                for ev_data in data.get("events", []):
                    events.append(ScenarioEvent(
                        time_offset_s=ev_data.get("time_offset_s"),
                        condition=ev_data.get("condition"),
                        action=ev_data.get("action", ""),
                        params=ev_data.get("params", {}),
                    ))
                defn = ScenarioDefinition(
                    name=data.get("name", path.stem),
                    difficulty=data.get("difficulty", "BASIC"),
                    duration_s=data.get("duration_s", 1800.0),
                    briefing=data.get("briefing", ""),
                    events=events,
                    expected_responses=data.get("expected_responses", []),
                )
                self._scenarios[defn.name] = defn
                logger.info("Loaded scenario: %s", defn.name)
            except Exception as e:
                logger.warning("Failed to load scenario %s: %s", path.name, e)

    def list_scenarios(self) -> list[dict]:
        return [{"name": s.name, "difficulty": s.difficulty, "duration_s": s.duration_s,
                 "briefing": s.briefing}
                for s in self._scenarios.values()]

    def start(self, name: str) -> bool:
        defn = self._scenarios.get(name)
        if defn is None:
            logger.warning("Unknown scenario: %s", name)
            return False
        # Reset fired flags
        for ev in defn.events:
            ev.fired = False
        self._active = defn
        self._elapsed = 0.0
        self._start_time = time.monotonic()
        self._responses = []
        self._response_times = {}
        logger.info("Scenario started: %s", name)
        return True

    def stop(self) -> Optional[ScenarioDebrief]:
        if self._active is None:
            return None
        debrief = self._build_debrief()
        self._active = None
        return debrief

    def is_active(self) -> bool:
        return self._active is not None

    def current_name(self) -> str:
        return self._active.name if self._active else ""

    @property
    def elapsed_s(self) -> float:
        return self._elapsed

    def tick(self, dt_sim: float, shared_params: dict) -> None:
        if self._active is None:
            return
        self._elapsed += dt_sim

        # Check timed events
        for ev in self._active.events:
            if ev.fired:
                continue
            if ev.time_offset_s is not None and self._elapsed >= ev.time_offset_s:
                self._fire_event(ev)
            elif ev.condition is not None:
                if self._eval_condition(ev.condition, shared_params):
                    self._fire_event(ev)

        # Auto-end on duration
        if self._elapsed >= self._active.duration_s:
            logger.info("Scenario '%s' duration expired", self._active.name)

    def _fire_event(self, ev: ScenarioEvent) -> None:
        ev.fired = True
        action = ev.action
        params = ev.params

        if action == "inject" and self._fm:
            self._fm.inject(
                subsystem=params.get("subsystem", ""),
                failure=params.get("failure", ""),
                magnitude=float(params.get("magnitude", 1.0)),
                onset=params.get("onset", "step"),
                duration_s=float(params.get("duration_s", 0)),
                **{k: v for k, v in params.items()
                   if k not in ("subsystem", "failure", "magnitude", "onset", "duration_s")},
            )
        elif action == "clear" and self._fm:
            fid = params.get("failure_id", "")
            if fid:
                self._fm.clear(fid)
            else:
                self._fm.clear_all()
        elif action == "message":
            logger.info("Scenario message: %s", params.get("text", ""))

    def _eval_condition(self, condition: str, params: dict) -> bool:
        """Simple condition evaluator: 'param_name op value'."""
        try:
            parts = condition.split()
            if len(parts) != 3:
                return False
            param_name, op, threshold = parts[0], parts[1], float(parts[2])
            # Try to resolve param name
            value = None
            for pid, val in params.items():
                if str(pid) == param_name:
                    value = float(val)
                    break
            if value is None:
                return False
            if op == "<": return value < threshold
            if op == ">": return value > threshold
            if op == "==": return abs(value - threshold) < 0.01
            if op == "<=": return value <= threshold
            if op == ">=": return value >= threshold
        except Exception:
            pass
        return False

    def record_response(self, category: str, description: str = "") -> None:
        if self._active is None:
            return
        self._responses.append({
            "category": category, "description": description,
            "time_s": self._elapsed,
        })
        if category not in self._response_times:
            self._response_times[category] = self._elapsed

    def _build_debrief(self) -> ScenarioDebrief:
        expected = len(self._active.expected_responses)
        completed = len(self._responses)
        score = (completed / max(expected, 1)) * 100.0
        return ScenarioDebrief(
            name=self._active.name,
            duration_s=self._elapsed,
            score_pct=min(100.0, score),
            mttd_s=self._response_times.get("detect"),
            mtti_s=self._response_times.get("isolate"),
            mttr_s=self._response_times.get("recover"),
            responses=list(self._responses),
        )
