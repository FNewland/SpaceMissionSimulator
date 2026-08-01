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
    # Applied to the simulator when the scenario starts (phase, speed, SoC,
    # pass override, power lines, params, pre-injected failures).
    initial_conditions: dict[str, Any] = field(default_factory=dict)


# Actions the scenario engine can actually execute. Anything else is a
# scenario-authoring mistake and is rejected at load time (defect #52).
SUPPORTED_ACTIONS = frozenset({
    "inject", "clear", "set_parameter", "message", "set_speed", "set_phase",
})

# Event id used to surface load-time scenario config errors as real telemetry.
EVENT_SCENARIO_CONFIG_INVALID = 0x0F20


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

    def __init__(self, failure_manager=None, engine=None, defaults=None):
        self._fm = failure_manager
        self._engine = engine
        # simulation.yaml scenario_defaults: (pydantic model or dict); the
        # fallbacks reproduce the previous hard-coded values.
        self._defaults = {
            "default_duration_s": 1800.0,
            "default_difficulty": "BASIC",
            "eq_tolerance": 0.01,
            "valid_difficulties": ["BASIC", "BEGINNER", "INTERMEDIATE", "ADVANCED"],
        }
        if defaults is not None:
            for key in list(self._defaults):
                val = (defaults.get(key) if isinstance(defaults, dict)
                       else getattr(defaults, key, None))
                if val is not None:
                    self._defaults[key] = val
        # Load-time schema problems (skipped events, bad difficulty, duplicates)
        self._load_issues: list[str] = []
        self._issues_emitted = False
        # subsystem -> implemented failure-mode names (lazily introspected)
        self._failure_registry: dict[str, set[str]] = {}
        self._scenarios: dict[str, ScenarioDefinition] = {}
        self._active: Optional[ScenarioDefinition] = None
        self._elapsed: float = 0.0
        self._start_time: float = 0.0
        self._responses: list[dict] = []
        self._response_times: dict[str, float] = {}
        self._last_debrief: Optional[ScenarioDebrief] = None
        # Failure ids injected by the currently-active scenario, so they can be
        # cleared when the scenario stops (auto-end or manual) and don't leak
        # into the next scenario / free run.
        self._injected_fids: list[str] = []

    # ------------------------------------------------------------------
    # Loading + schema validation
    # ------------------------------------------------------------------

    def _known_failures(self, subsystem: str) -> set[str]:
        """Failure-mode names a subsystem model actually implements.

        Derived from the model's own ``inject_failure`` source so the registry
        can never drift from the implementation. Returns an empty set when the
        model (or its source) is unavailable — callers then skip the check
        rather than rejecting a valid event.
        """
        if subsystem in self._failure_registry:
            return self._failure_registry[subsystem]
        names: set[str] = set()
        engine = self._engine
        model = None
        if engine is not None and hasattr(engine, "subsystems"):
            # Instructor UI and scenarios use "adcs" as an alias for "aocs".
            model = (engine.subsystems.get(subsystem)
                     or engine.subsystems.get({"adcs": "aocs"}.get(subsystem, "")))
        if model is not None and hasattr(model, "inject_failure"):
            try:
                import inspect
                import re
                src = inspect.getsource(model.inject_failure)
                names = set(re.findall(r'failure\s*==\s*["\']([\w]+)["\']', src))
            except Exception as e:  # noqa: BLE001 — introspection is best-effort
                logger.debug("Failure registry introspection failed for %s: %s",
                             subsystem, e)
        self._failure_registry[subsystem] = names
        return names

    def _validate_event(self, ev_data: dict, scenario_name: str) -> str | None:
        """Return an error string if this event cannot execute, else None."""
        action = ev_data.get("action", "")
        if action not in SUPPORTED_ACTIONS:
            return (f"unknown action {action!r} "
                    f"(supported: {', '.join(sorted(SUPPORTED_ACTIONS))})")
        if action == "inject":
            params = ev_data.get("params", {}) or {}
            subsystem = params.get("subsystem", "")
            failure = params.get("failure", "")
            if not subsystem or not failure:
                return "inject event missing params.subsystem/params.failure"
            known = self._known_failures(subsystem)
            if known and failure not in known:
                return (f"unknown failure {failure!r} for subsystem {subsystem!r} "
                        f"(known: {', '.join(sorted(known))})")
        if ev_data.get("time_offset_s") is None and not ev_data.get("condition"):
            return "event has neither time_offset_s nor condition — can never fire"
        return None

    def load_scenarios_from_dir(self, scenario_dir: Path) -> None:
        """Load and VALIDATE all YAML scenario files from a directory.

        Events referencing an unimplemented action or an unknown failure mode
        are skipped (not loaded) and recorded as load issues, which are logged
        and emitted as SCENARIO_CONFIG_INVALID events. Previously such events
        loaded fine and then did nothing at run time (defect #52).
        """
        if not scenario_dir.exists():
            return
        defaults = self._defaults
        for path in sorted(scenario_dir.glob("*.yaml")):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                name = data.get("name", path.stem)
                events = []
                for idx, ev_data in enumerate(data.get("events", []) or []):
                    err = self._validate_event(ev_data, name)
                    if err:
                        self._record_issue(path.name, name, idx, err)
                        continue
                    events.append(ScenarioEvent(
                        time_offset_s=ev_data.get("time_offset_s"),
                        condition=ev_data.get("condition"),
                        action=ev_data.get("action", ""),
                        params=ev_data.get("params", {}),
                    ))
                difficulty = data.get("difficulty", defaults["default_difficulty"])
                if difficulty not in defaults["valid_difficulties"]:
                    self._record_issue(
                        path.name, name, -1,
                        f"difficulty {difficulty!r} is not one of "
                        f"{defaults['valid_difficulties']}")
                defn = ScenarioDefinition(
                    name=name,
                    difficulty=difficulty,
                    duration_s=data.get("duration_s", defaults["default_duration_s"]),
                    briefing=data.get("briefing", ""),
                    events=events,
                    expected_responses=data.get("expected_responses", []),
                    initial_conditions=data.get("initial_conditions", {}) or {},
                )
                if defn.name in self._scenarios:
                    self._record_issue(path.name, defn.name, -1,
                                       "duplicate scenario name — previous definition replaced")
                self._scenarios[defn.name] = defn
                logger.info("Loaded scenario: %s (%d events)", defn.name, len(defn.events))
            except Exception as e:
                logger.warning("Failed to load scenario %s: %s", path.name, e)
                self._record_issue(path.name, path.stem, -1, f"parse error: {e}")

    def _record_issue(self, file_name: str, scenario: str, index: int,
                      message: str) -> None:
        where = f"event[{index}]" if index >= 0 else "definition"
        text = f"{file_name}: {scenario}: {where}: {message}"
        logger.warning("Scenario config invalid — %s", text)
        self._load_issues.append(text)

    def load_issues(self) -> list[str]:
        """Load-time scenario schema problems (skipped events, bad difficulty)."""
        return list(self._load_issues)

    def emit_load_issues(self) -> None:
        """Emit one SCENARIO_CONFIG_INVALID event per load issue (once).

        Called by the engine after its TM/dispatcher plumbing exists, because
        scenarios are loaded earlier in engine construction.
        """
        if self._issues_emitted or self._engine is None:
            return
        self._issues_emitted = True
        for text in self._load_issues:
            try:
                self._engine._emit_event({
                    'event_id': EVENT_SCENARIO_CONFIG_INVALID,
                    'severity': 2,
                    'description': f"Scenario config invalid: {text}"[:200],
                })
            except Exception as e:  # noqa: BLE001 — never block startup
                logger.debug("Could not emit scenario load issue: %s", e)

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
        self._injected_fids = []
        self._apply_initial_conditions(defn)
        logger.info("Scenario started: %s", name)
        return True

    def _apply_initial_conditions(self, defn: ScenarioDefinition) -> None:
        """Put the simulator into the scenario's declared starting state.

        Supported keys (all optional):
            spacecraft_phase: 0-6      speed: float
            override_passes: bool      soc_pct: float
            power_lines: {name: bool}  params: {param_id: value}
            failures: [{subsystem, failure, magnitude, onset, duration_s}]

        Previously ``initial_conditions:`` was parsed by nothing at all — three
        scenarios carried dead blocks and started in the wrong state (defect #52).
        """
        ic = defn.initial_conditions or {}
        if not ic:
            return
        engine = self._engine
        if engine is None:
            logger.warning("Scenario %s: initial_conditions ignored (no engine)",
                           defn.name)
            return

        phase = ic.get("spacecraft_phase")
        if phase is not None:
            try:
                engine._handle_instructor_cmd({'type': 'set_phase', 'phase': int(phase)})
            except Exception as e:  # noqa: BLE001
                logger.warning("Scenario %s: set_phase failed: %s", defn.name, e)

        if ic.get("speed") is not None:
            self._set_speed(float(ic["speed"]))

        if ic.get("override_passes") is not None:
            try:
                engine._handle_instructor_cmd({
                    'type': 'override_passes', 'value': bool(ic["override_passes"])})
            except Exception as e:  # noqa: BLE001
                logger.warning("Scenario %s: override_passes failed: %s", defn.name, e)

        eps = getattr(engine, "subsystems", {}).get("eps")
        if ic.get("soc_pct") is not None and eps is not None and hasattr(eps, "_state"):
            soc = float(ic["soc_pct"])
            eps._state.bat_soc_pct = soc
            eps._state._prev_soc = soc
            eps._state.bat_dod_pct = 100.0 - soc

        for line, on in (ic.get("power_lines") or {}).items():
            if eps is not None and hasattr(eps, "_state") and line in eps._state.power_lines:
                eps._state.power_lines[line] = bool(on)
            else:
                logger.warning("Scenario %s: unknown power line %r in "
                               "initial_conditions", defn.name, line)

        for pid_raw, value in (ic.get("params") or {}).items():
            try:
                pid = int(pid_raw, 0) if isinstance(pid_raw, str) else int(pid_raw)
                engine.params[pid] = value
            except Exception as e:  # noqa: BLE001
                logger.warning("Scenario %s: bad initial param %r: %s",
                               defn.name, pid_raw, e)

        for f in (ic.get("failures") or []):
            if not self._fm:
                break
            fid = self._fm.inject(
                subsystem=f.get("subsystem", ""), failure=f.get("failure", ""),
                magnitude=float(f.get("magnitude", 1.0)),
                onset=f.get("onset", "step"),
                duration_s=float(f.get("duration_s", 0)),
            )
            if fid:
                self._injected_fids.append(fid)
        logger.info("Scenario %s: applied initial conditions %s",
                    defn.name, sorted(ic.keys()))

    def _set_speed(self, value: float) -> None:
        """Set the engine's time acceleration, clamped to simulation.yaml max."""
        engine = self._engine
        if engine is None:
            logger.warning("Scenario set_speed: no engine reference")
            return
        max_speed = float(getattr(engine, "_speed_max", 1000.0))
        speed = max(0.0, min(float(value), max_speed))
        if speed != float(value):
            logger.warning("Scenario set_speed %.1f clamped to %.1f", value, speed)
        engine.speed = speed
        logger.info("Scenario set_speed: %.1fx", speed)

    def stop(self) -> Optional[ScenarioDebrief]:
        """Stop the active scenario, returning (and caching) its debrief.

        Idempotent: returns ``None`` and does nothing if no scenario is active.
        """
        return self._finish(reason="stopped")

    def _finish(self, reason: str) -> Optional[ScenarioDebrief]:
        """Tear down the active scenario exactly once.

        Builds the debrief, caches it on ``self._last_debrief`` for later
        retrieval, clears any failures this scenario injected, resets the
        per-event ``fired`` flags, and deactivates the scenario. Safe to call
        when nothing is active (returns ``None``).
        """
        if self._active is None:
            return None
        debrief = self._build_debrief()
        self._last_debrief = debrief
        # Clear failures injected by this scenario so they don't leak past the
        # scenario's lifetime. Timed injects (finite duration_s) auto-clear via
        # the failure_manager; this also catches indefinite (duration_s<=0)
        # injects that would otherwise stay active forever.
        if self._fm is not None:
            for fid in self._injected_fids:
                try:
                    self._fm.clear(fid)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Scenario stop: failed to clear failure %s: %s",
                                   fid, e)
        self._injected_fids = []
        # Reset fired-event state so the definition can be re-run cleanly.
        for ev in self._active.events:
            ev.fired = False
        logger.info("Scenario '%s' %s after %.1fs (score %.0f%%)",
                    self._active.name, reason, debrief.duration_s,
                    debrief.score_pct)
        self._active = None
        return debrief

    def last_debrief(self) -> Optional[ScenarioDebrief]:
        """Return the debrief from the most recently finished scenario."""
        return self._last_debrief

    # ─── State save/restore (plan A-4) ───────────────────────────────

    def get_state(self) -> dict:
        """Serialise the running scenario's progress for breakpoint save."""
        state: dict[str, Any] = {"active": None}
        if self._active is not None:
            state.update({
                "active": self._active.name,
                "elapsed": self._elapsed,
                "responses": [dict(r) for r in self._responses],
                "response_times": dict(self._response_times),
                "injected_fids": list(self._injected_fids),
                "fired": [i for i, ev in enumerate(self._active.events)
                          if ev.fired],
            })
        return state

    def set_state(self, state: dict) -> None:
        """Restore scenario progress from a breakpoint.

        The scenario definition itself is config (reloaded at boot); only the
        run-time progress is state. A snapshot naming a scenario that is no
        longer configured deactivates cleanly.
        """
        name = state.get("active")
        if not name:
            # No scenario was running at save time; clear any current one
            # WITHOUT firing debrief/cleanup side effects — the restored
            # failure-manager state already reflects the snapshot.
            self._active = None
            self._injected_fids = []
            return
        defn = self._scenarios.get(name)
        if defn is None:
            logger.warning("Breakpoint restore: scenario %r not configured — "
                           "deactivating", name)
            self._active = None
            self._injected_fids = []
            return
        self._active = defn
        self._elapsed = float(state.get("elapsed", 0.0))
        self._start_time = time.monotonic() - self._elapsed
        self._responses = [dict(r) for r in state.get("responses", [])]
        self._response_times = dict(state.get("response_times", {}))
        self._injected_fids = list(state.get("injected_fids", []))
        fired = set(int(i) for i in state.get("fired", []))
        for i, ev in enumerate(defn.events):
            ev.fired = i in fired

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

        # Auto-end on duration: deactivate the scenario so the engine's main
        # loop stops ticking it. Previously this only logged "duration expired"
        # and left ``self._active`` set, so the scenario ran forever.
        if self._elapsed >= self._active.duration_s:
            self._finish(reason="duration expired")

    def _fire_event(self, ev: ScenarioEvent) -> None:
        ev.fired = True
        action = ev.action
        params = ev.params

        if action == "inject" and self._fm:
            fid = self._fm.inject(
                subsystem=params.get("subsystem", ""),
                failure=params.get("failure", ""),
                magnitude=float(params.get("magnitude", 1.0)),
                onset=params.get("onset", "step"),
                duration_s=float(params.get("duration_s", 0)),
                **{k: v for k, v in params.items()
                   if k not in ("subsystem", "failure", "magnitude", "onset", "duration_s")},
            )
            # Track so it can be cleared if the scenario ends while still active.
            if fid:
                self._injected_fids.append(fid)
        elif action == "clear" and self._fm:
            fid = params.get("failure_id", "")
            if fid:
                self._fm.clear(fid)
            else:
                self._fm.clear_all()
        elif action == "set_parameter":
            # Drive a telemetry parameter to a specific value (e.g. force a bus
            # voltage drop in a scenario). Numeric ``param_id`` is supported
            # directly; symbolic ``parameter`` names without a numeric id are
            # logged as unresolved because the scenario engine has no name→id
            # registry. Without this branch, set_parameter events fired silently
            # (no effect, no warning) — broken scenario authoring would look fine.
            if self._engine is None:
                logger.warning("Scenario set_parameter: no engine reference")
                return
            pid_raw = params.get("param_id")
            value = params.get("value")
            if pid_raw is None:
                logger.warning(
                    "Scenario set_parameter: symbolic 'parameter=%s' has no numeric "
                    "param_id — skipping (use hex/decimal param_id instead)",
                    params.get("parameter", "?"))
                return
            try:
                pid = int(pid_raw, 0) if isinstance(pid_raw, str) else int(pid_raw)
                self._engine.params[pid] = value
                logger.info("Scenario set_parameter: 0x%04X = %r", pid, value)
            except Exception as e:
                logger.warning("Scenario set_parameter failed: %s", e)
        elif action == "message":
            logger.info("Scenario message: %s", params.get("text", ""))
        elif action == "set_speed":
            # Time acceleration, e.g. to run through the separation timer and
            # then drop back to real time for the first pass. Accepts
            # params.value or a top-level `value:` mapped in by the loader.
            self._set_speed(float(params.get("value", params.get("speed", 1.0))))
        elif action == "set_phase":
            if self._engine is None:
                logger.warning("Scenario set_phase: no engine reference")
                return
            try:
                self._engine._handle_instructor_cmd({
                    'type': 'set_phase', 'phase': int(params.get("phase", 6))})
                logger.info("Scenario set_phase: %s", params.get("phase"))
            except Exception as e:  # noqa: BLE001
                logger.warning("Scenario set_phase failed: %s", e)
        else:
            # Unrecognised action — surface it so scenario-authoring mistakes
            # are visible. Previously these fired silently.
            logger.warning("Scenario: unknown action %r (params=%r)", action, params)

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
            if op == "==": return abs(value - threshold) < self._defaults["eq_tolerance"]
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
