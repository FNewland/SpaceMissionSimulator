"""
EO Mission Simulator — Scenario Engine
Loads YAML scenario definitions, schedules failure injections, tracks
operator responses, and computes debrief metrics (MTTD/MTTI/MTTR).
"""
import time
import logging
import yaml
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from failure_manager import FailureManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScenarioEvent:
    """A single timed or condition-triggered event in the scenario."""
    t_offset_s:  float           # seconds after scenario start (or -1 for condition)
    action:      str             # "inject", "clear", "message", "check"
    params:      Dict[str, Any]  = field(default_factory=dict)
    condition:   Optional[str]   = None   # e.g. "bat_soc < 20"
    fired:       bool            = False


@dataclass
class ExpectedResponse:
    """One item in the debrief checklist."""
    description:   str
    category:      str            # "detect", "isolate", "recover"
    completed:     bool  = False
    t_completed_s: float = 0.0


@dataclass
class ScenarioDefinition:
    name:              str
    difficulty:        str
    duration_s:        float
    description:       str
    events:            List[ScenarioEvent]       = field(default_factory=list)
    expected_responses: List[ExpectedResponse]   = field(default_factory=list)
    briefing:          str = ""


@dataclass
class DebriefReport:
    scenario_name:  str
    total_duration_s: float
    mttd_s:         Optional[float]   # Mean Time To Detect
    mtti_s:         Optional[float]   # Mean Time To Isolate
    mttr_s:         Optional[float]   # Mean Time To Recover
    responses_completed: int
    responses_total:     int
    timeline:       List[dict]        = field(default_factory=list)
    score_pct:      float = 0.0


class ScenarioEngine:
    """
    Loads and runs training scenarios.

    Typical usage:
        se = ScenarioEngine(failure_manager, engine)
        se.load_directory("scenarios/")
        se.start("battery_heater_failure")
        # Each tick:
        se.tick(dt_sim, shared_params)
        # On operator action:
        se.record_response("detect", "Identified battery temperature drop")
    """

    def __init__(self, failure_manager: "FailureManager", engine):
        self._fm      = failure_manager
        self._engine  = engine
        self._library: Dict[str, ScenarioDefinition] = {}
        self._active:  Optional[ScenarioDefinition]  = None
        self._start_wall: float = 0.0
        self._elapsed_s:  float = 0.0
        self._timeline:   List[dict] = []
        self._detect_t:   Optional[float] = None
        self._isolate_t:  Optional[float] = None
        self._recover_t:  Optional[float] = None
        self._running:    bool = False

        # Load built-in scenarios
        self._register_builtin_scenarios()

    # ------------------------------------------------------------------
    # Library management
    # ------------------------------------------------------------------

    def load_directory(self, path: str) -> int:
        """Load all .yaml scenario files from a directory. Returns count loaded."""
        if not os.path.isdir(path):
            return 0
        count = 0
        for fname in os.listdir(path):
            if fname.endswith(('.yaml', '.yml')):
                try:
                    self.load_file(os.path.join(path, fname))
                    count += 1
                except Exception as e:
                    logger.warning("Failed to load scenario %s: %s", fname, e)
        return count

    def load_file(self, path: str) -> ScenarioDefinition:
        with open(path, 'r') as f:
            raw = yaml.safe_load(f)
        sd = self._parse_scenario(raw)
        self._library[sd.name] = sd
        logger.info("Loaded scenario: %s (%s)", sd.name, sd.difficulty)
        return sd

    def list_scenarios(self) -> List[dict]:
        return [
            {"name": sd.name, "difficulty": sd.difficulty,
             "duration_s": sd.duration_s, "description": sd.description}
            for sd in self._library.values()
        ]

    # ------------------------------------------------------------------
    # Scenario control
    # ------------------------------------------------------------------

    def start(self, name: str) -> bool:
        if name not in self._library:
            logger.error("Scenario not found: %s", name)
            return False
        if self._running:
            self.stop()

        self._active    = self._library[name]
        self._start_wall= time.monotonic()
        self._elapsed_s = 0.0
        self._timeline  = []
        self._detect_t  = None
        self._isolate_t = None
        self._recover_t = None
        self._running   = True

        # Reset fired flags
        for ev in self._active.events:
            ev.fired = False
        for resp in self._active.expected_responses:
            resp.completed = False

        self._log_event("scenario_start", {"name": name})
        logger.info("Scenario started: %s", name)
        return True

    def stop(self) -> Optional[DebriefReport]:
        if not self._running:
            return None
        self._running = False
        self._fm.clear_all()
        report = self._build_debrief()
        logger.info("Scenario stopped: %s  score=%.0f%%", self._active.name, report.score_pct)
        return report

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def elapsed_s(self) -> float:
        return self._elapsed_s

    def is_active(self) -> bool:
        return self._active

    def current_name(self) -> str:
        return self._active.name if self._active else ''

    # ------------------------------------------------------------------
    # Tick (called every engine step)
    # ------------------------------------------------------------------

    def tick(self, dt_sim: float, shared_params: Dict) -> None:
        if not self._running or self._active is None:
            return

        self._elapsed_s += dt_sim

        # Fire timed events
        for ev in self._active.events:
            if ev.fired:
                continue
            if ev.t_offset_s >= 0 and self._elapsed_s >= ev.t_offset_s:
                self._fire_event(ev)
            elif ev.condition:
                if self._eval_condition(ev.condition, shared_params):
                    self._fire_event(ev)

        # Auto-end when scenario duration expires
        if self._elapsed_s >= self._active.duration_s:
            self.stop()

    # ------------------------------------------------------------------
    # Operator response recording (called by instructor UI / TC handler)
    # ------------------------------------------------------------------

    def record_response(self, category: str, description: str = "") -> None:
        """Record an operator action for debrief tracking."""
        if not self._running:
            return
        t = self._elapsed_s
        self._log_event("operator_action", {"category": category, "description": description, "t": t})

        # Track first detect/isolate/recover times
        if category == "detect" and self._detect_t is None:
            self._detect_t = t
            logger.info("DETECT recorded at t=%.1fs", t)
        elif category == "isolate" and self._isolate_t is None:
            self._isolate_t = t
        elif category == "recover" and self._recover_t is None:
            self._recover_t = t

        # Try to match against expected responses
        for resp in self._active.expected_responses:
            if resp.category == category and not resp.completed:
                resp.completed   = True
                resp.t_completed_s = t
                break

    # ------------------------------------------------------------------
    # Firing events
    # ------------------------------------------------------------------

    def _fire_event(self, ev: ScenarioEvent) -> None:
        ev.fired = True
        action   = ev.action
        params   = ev.params

        if action == "inject":
            self._fm.inject(**params)
            self._log_event("failure_inject", {**params, "t": self._elapsed_s})
            logger.info("Scenario event INJECT at t=%.1fs: %s/%s",
                        self._elapsed_s, params.get("subsystem"), params.get("failure"))

        elif action == "clear":
            fid = params.get("failure_id")
            if fid:
                self._fm.clear(fid)
            else:
                self._fm.clear_all()
            self._log_event("failure_clear", {**params, "t": self._elapsed_s})

        elif action == "message":
            msg = params.get("text", "")
            logger.info("SCENARIO MESSAGE [t=%.1fs]: %s", self._elapsed_s, msg)
            self._log_event("message", {"text": msg, "t": self._elapsed_s})

        elif action == "check":
            # Instructor alert: check if expected condition has been met
            self._log_event("check", {**params, "t": self._elapsed_s})

    # ------------------------------------------------------------------
    # Condition evaluator
    # ------------------------------------------------------------------

    def _eval_condition(self, condition: str, params: Dict) -> bool:
        """
        Evaluate a simple condition string against the param store.
        Format: "<param_name_or_hex> <op> <value>"
        e.g. "bat_soc < 20"  or  "0x0101 < 20"
        """
        try:
            parts = condition.strip().split()
            if len(parts) != 3:
                return False
            lhs_str, op, rhs_str = parts
            rhs = float(rhs_str)

            # Resolve LHS: param hex ID or named alias
            if lhs_str.startswith('0x') or lhs_str.startswith('0X'):
                param_id = int(lhs_str, 16)
                lhs = float(params.get(param_id, 0))
            else:
                lhs = self._resolve_named_param(lhs_str, params)

            if   op == '<':  return lhs <  rhs
            elif op == '>':  return lhs >  rhs
            elif op == '<=': return lhs <= rhs
            elif op == '>=': return lhs >= rhs
            elif op == '==': return abs(lhs - rhs) < 0.01
        except Exception:
            pass
        return False

    def _resolve_named_param(self, name: str, params: Dict) -> float:
        """Map friendly param names to param IDs."""
        _ALIASES = {
            "bat_soc":    0x0101, "bat_voltage":  0x0100, "bat_temp":    0x0102,
            "bus_voltage":0x0105, "att_error":    0x0217, "obc_mode":    0x0300,
            "cpu_load":   0x0302, "fpa_temp":     0x0601, "rw1_speed":   0x0207,
            "rw2_speed":  0x0208, "rw3_speed":    0x0209, "rw4_speed":   0x020A,
            "temp_obc":   0x0406, "temp_battery": 0x0407, "aocs_mode":   0x020F,
            "link_status":0x0501, "store_pct":    0x0604, "tc_rej":      0x0306,
        }
        pid = _ALIASES.get(name.lower())
        if pid is not None:
            return float(params.get(pid, 0))
        return 0.0

    # ------------------------------------------------------------------
    # Debrief
    # ------------------------------------------------------------------

    def _build_debrief(self) -> DebriefReport:
        sd = self._active
        completed = sum(1 for r in sd.expected_responses if r.completed)
        total     = len(sd.expected_responses)
        score     = (completed / total * 100.0) if total > 0 else 100.0

        return DebriefReport(
            scenario_name    = sd.name,
            total_duration_s = self._elapsed_s,
            mttd_s           = self._detect_t,
            mtti_s           = self._isolate_t,
            mttr_s           = self._recover_t,
            responses_completed = completed,
            responses_total     = total,
            timeline         = list(self._timeline),
            score_pct        = score,
        )

    def _log_event(self, kind: str, data: dict) -> None:
        self._timeline.append({"kind": kind, "data": data, "t": self._elapsed_s})

    # ------------------------------------------------------------------
    # YAML parser
    # ------------------------------------------------------------------

    def _parse_scenario(self, raw: dict) -> ScenarioDefinition:
        events = []
        for ev_raw in raw.get("events", []):
            events.append(ScenarioEvent(
                t_offset_s = float(ev_raw.get("t", -1)),
                action     = ev_raw.get("action", "inject"),
                params     = ev_raw.get("params", {}),
                condition  = ev_raw.get("condition"),
            ))

        responses = []
        for resp_raw in raw.get("expected_responses", []):
            responses.append(ExpectedResponse(
                description = resp_raw.get("description", ""),
                category    = resp_raw.get("category", "detect"),
            ))

        return ScenarioDefinition(
            name        = raw["name"],
            difficulty  = raw.get("difficulty", "BASIC"),
            duration_s  = float(raw.get("duration_s", 1800)),
            description = raw.get("description", ""),
            events      = events,
            expected_responses = responses,
            briefing    = raw.get("briefing", ""),
        )

    # ------------------------------------------------------------------
    # Built-in scenario library
    # ------------------------------------------------------------------

    def _register_builtin_scenarios(self) -> None:
        """Register all 12 training scenarios from the architecture document."""

        # 1. Nominal Operations
        self._library["nominal_ops"] = ScenarioDefinition(
            name="nominal_ops", difficulty="BASIC", duration_s=1800,
            description="Nominal operations familiarisation — no failures injected.",
            briefing="Monitor all subsystems and learn nominal parameter ranges.",
            events=[
                ScenarioEvent(t_offset_s=0, action="message",
                    params={"text": "Simulation start. All systems nominal."}),
                ScenarioEvent(t_offset_s=600, action="message",
                    params={"text": "AOS window approaching in 5 minutes."}),
            ],
            expected_responses=[
                ExpectedResponse("Monitor EPS parameters", "detect"),
                ExpectedResponse("Verify contact window prediction", "detect"),
            ]
        )

        # 2. Battery Heater Failure
        self._library["battery_heater_failure"] = ScenarioDefinition(
            name="battery_heater_failure", difficulty="BASIC", duration_s=1200,
            description="Battery thermostat heater circuit fails open during eclipse.",
            briefing="An eclipse is imminent. Watch battery thermal parameters carefully.",
            events=[
                ScenarioEvent(t_offset_s=120, action="inject",
                    params={"subsystem":"tcs","failure":"heater_failure",
                            "circuit":"battery","magnitude":1.0,"onset":"step"}),
                ScenarioEvent(t_offset_s=120, action="message",
                    params={"text":"Eclipse entry. Monitor battery temperature."}),
            ],
            expected_responses=[
                ExpectedResponse("Detect battery temperature dropping below +5°C", "detect"),
                ExpectedResponse("Identify heater circuit failure", "isolate"),
                ExpectedResponse("Switch to backup heater or increase battery charge", "recover"),
            ]
        )

        # 3. Payload FPA Overtemperature
        self._library["fpa_overtemp"] = ScenarioDefinition(
            name="fpa_overtemp", difficulty="BASIC", duration_s=1500,
            description="FPA cooler fails during imaging session, FPA temperature rises.",
            briefing="Imaging session in progress. Monitor FPA temperature.",
            events=[
                ScenarioEvent(t_offset_s=60, action="inject",
                    params={"subsystem":"payload","failure":"cooler_failure",
                            "magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect FPA temperature rising above -5°C", "detect"),
                ExpectedResponse("Identify cooler failure", "isolate"),
                ExpectedResponse("Command payload to STANDBY mode", "recover"),
                ExpectedResponse("Schedule cooler restart attempt", "recover"),
            ]
        )

        # 4. Solar Array Degradation
        self._library["solar_array_degradation"] = ScenarioDefinition(
            name="solar_array_degradation", difficulty="INTERMEDIATE", duration_s=2400,
            description="SA-A degrades gradually to 30% output over 10 minutes.",
            briefing="All systems nominal. Watch power generation carefully.",
            events=[
                ScenarioEvent(t_offset_s=180, action="inject",
                    params={"subsystem":"eps","failure":"solar_array_partial",
                            "magnitude":0.7,"onset":"gradual","onset_duration_s":600,
                            "array":"A","duration_s":0}),
            ],
            expected_responses=[
                ExpectedResponse("Detect power generation declining", "detect"),
                ExpectedResponse("Identify SA-A current drop", "isolate"),
                ExpectedResponse("Shed non-essential loads", "recover"),
                ExpectedResponse("Adjust attitude for better sun angle", "recover"),
            ]
        )

        # 5. Reaction Wheel Bearing Degradation
        self._library["rw_bearing_failure"] = ScenarioDefinition(
            name="rw_bearing_failure", difficulty="INTERMEDIATE", duration_s=2700,
            description="RW-2 bearing degrades causing temperature rise and speed instability.",
            briefing="AOCS nominal. Monitor reaction wheel temperatures.",
            events=[
                ScenarioEvent(t_offset_s=300, action="inject",
                    params={"subsystem":"aocs","failure":"rw_bearing","wheel":1,
                            "magnitude":0.7,"onset":"gradual","onset_duration_s":900}),
            ],
            expected_responses=[
                ExpectedResponse("Detect RW-2 temperature anomaly", "detect"),
                ExpectedResponse("Detect RW-2 speed instability", "detect"),
                ExpectedResponse("Isolate RW-2 as fault source", "isolate"),
                ExpectedResponse("Command AOCS desaturation", "recover"),
                ExpectedResponse("Disable RW-2 and redistribute momentum", "recover"),
            ]
        )

        # 6. Star Tracker Blinding
        self._library["star_tracker_blind"] = ScenarioDefinition(
            name="star_tracker_blind", difficulty="INTERMEDIATE", duration_s=2100,
            description="Star tracker blinded by sun proximity; attitude error grows.",
            briefing="Spacecraft approaching high solar beta angle.",
            events=[
                ScenarioEvent(t_offset_s=120, action="inject",
                    params={"subsystem":"aocs","failure":"st_blind",
                            "magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect attitude error increasing", "detect"),
                ExpectedResponse("Identify star tracker invalid flag", "isolate"),
                ExpectedResponse("Switch to magnetometer/gyro-only attitude", "recover"),
                ExpectedResponse("Reduce attitude control bandwidth", "recover"),
            ]
        )

        # 7. OBC Watchdog Reset
        self._library["obc_watchdog_reset"] = ScenarioDefinition(
            name="obc_watchdog_reset", difficulty="INTERMEDIATE", duration_s=1800,
            description="OBC watchdog timer fires, triggering reboot and safe mode.",
            briefing="Spacecraft in nominal operations. OBC load is elevated.",
            events=[
                ScenarioEvent(t_offset_s=90, action="inject",
                    params={"subsystem":"obdh","failure":"cpu_spike",
                            "magnitude":0.95,"onset":"gradual","onset_duration_s":60}),
                ScenarioEvent(t_offset_s=210, action="inject",
                    params={"subsystem":"obdh","failure":"watchdog_reset",
                            "magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect CPU load rising above 85%", "detect"),
                ExpectedResponse("Detect OBC reboot / safe mode entry", "detect"),
                ExpectedResponse("Verify spacecraft in safe mode", "isolate"),
                ExpectedResponse("Command nominal mode recovery", "recover"),
                ExpectedResponse("Investigate watchdog trigger cause", "recover"),
            ]
        )

        # 8. Memory Bit-Error Spreading
        self._library["memory_bit_errors"] = ScenarioDefinition(
            name="memory_bit_errors", difficulty="ADVANCED", duration_s=3000,
            description="Single-event upset causes spreading memory errors in mass memory.",
            briefing="Spacecraft in nominal operations.",
            events=[
                ScenarioEvent(t_offset_s=120, action="inject",
                    params={"subsystem":"obdh","failure":"memory_errors","count":2,
                            "magnitude":1.0,"onset":"step"}),
                ScenarioEvent(t_offset_s=600, action="inject",
                    params={"subsystem":"obdh","failure":"memory_errors","count":5,
                            "magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect memory error count increase", "detect"),
                ExpectedResponse("Correlate errors with SEU event", "isolate"),
                ExpectedResponse("Command memory scrub", "recover"),
                ExpectedResponse("Verify error count stabilised", "recover"),
            ]
        )

        # 9. Combined Power + Thermal Anomaly
        self._library["power_thermal_combined"] = ScenarioDefinition(
            name="power_thermal_combined", difficulty="ADVANCED", duration_s=3600,
            description="SA-B partial failure coincides with battery heater fault.",
            briefing="Eclipse entry in 5 minutes. All systems should be nominal.",
            events=[
                ScenarioEvent(t_offset_s=240, action="inject",
                    params={"subsystem":"eps","failure":"solar_array_partial",
                            "magnitude":0.5,"onset":"gradual","onset_duration_s":300,"array":"B"}),
                ScenarioEvent(t_offset_s=360, action="inject",
                    params={"subsystem":"tcs","failure":"heater_failure",
                            "circuit":"battery","magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect SA-B power drop", "detect"),
                ExpectedResponse("Detect battery temperature falling", "detect"),
                ExpectedResponse("Identify two concurrent anomalies", "isolate"),
                ExpectedResponse("Shed payload and non-essential loads", "recover"),
                ExpectedResponse("Increase battery charge rate pre-eclipse", "recover"),
                ExpectedResponse("Monitor safe mode entry threshold", "recover"),
            ]
        )

        # 10. Transponder Primary Failure
        self._library["transponder_failure"] = ScenarioDefinition(
            name="transponder_failure", difficulty="ADVANCED", duration_s=2700,
            description="Primary transponder fails during contact window.",
            briefing="AOS in 3 minutes.",
            events=[
                ScenarioEvent(t_offset_s=150, action="inject",
                    params={"subsystem":"ttc","failure":"primary_failure",
                            "magnitude":1.0,"onset":"step"}),
                ScenarioEvent(t_offset_s=150, action="message",
                    params={"text":"AOS expected but no signal acquired."}),
            ],
            expected_responses=[
                ExpectedResponse("Detect loss of signal at AOS", "detect"),
                ExpectedResponse("Verify ephemeris / tracking data correct", "isolate"),
                ExpectedResponse("Identify primary transponder failure", "isolate"),
                ExpectedResponse("Command switch to redundant transponder", "recover"),
                ExpectedResponse("Re-acquire signal on redundant link", "recover"),
            ]
        )

        # 11. Momentum Buildup in Safe Mode
        self._library["momentum_buildup"] = ScenarioDefinition(
            name="momentum_buildup", difficulty="ADVANCED", duration_s=5400,
            description="Extended safe mode causes reaction wheel saturation.",
            briefing="Spacecraft entered safe mode. Desaturation not yet commanded.",
            events=[
                ScenarioEvent(t_offset_s=0, action="inject",
                    params={"subsystem":"aocs","failure":"rw_bearing","wheel":0,
                            "magnitude":0.0,"onset":"step"}),  # force safe mode via param
                ScenarioEvent(t_offset_s=1800, action="message",
                    params={"text":"Reaction wheel speeds approaching saturation limit."}),
            ],
            expected_responses=[
                ExpectedResponse("Monitor RW speeds during safe mode", "detect"),
                ExpectedResponse("Detect approach to saturation limit", "detect"),
                ExpectedResponse("Command magnetorquer desaturation", "recover"),
                ExpectedResponse("Verify RW speeds reduced", "recover"),
                ExpectedResponse("Plan safe-mode exit when conditions permit", "recover"),
            ]
        )

        # 12. Multi-Subsystem Contingency
        self._library["multi_subsystem"] = ScenarioDefinition(
            name="multi_subsystem", difficulty="EXPERT", duration_s=7200,
            description="Cascading failures: EPS → FDIR → safe mode → comms loss.",
            briefing="Full mission operations. Prioritise correctly under time pressure.",
            events=[
                ScenarioEvent(t_offset_s=300, action="inject",
                    params={"subsystem":"eps","failure":"solar_array_partial",
                            "magnitude":0.6,"onset":"gradual","onset_duration_s":600,"array":"A"}),
                ScenarioEvent(t_offset_s=900, action="inject",
                    params={"subsystem":"tcs","failure":"heater_failure",
                            "circuit":"battery","magnitude":1.0,"onset":"step"}),
                ScenarioEvent(t_offset_s=1800, action="inject",
                    params={"subsystem":"aocs","failure":"rw_bearing","wheel":2,
                            "magnitude":0.5,"onset":"gradual","onset_duration_s":600}),
                ScenarioEvent(t_offset_s=2700, action="inject",
                    params={"subsystem":"ttc","failure":"primary_failure",
                            "magnitude":1.0,"onset":"step"}),
            ],
            expected_responses=[
                ExpectedResponse("Detect initial power reduction", "detect"),
                ExpectedResponse("Detect battery thermal anomaly", "detect"),
                ExpectedResponse("Detect AOCS degradation", "detect"),
                ExpectedResponse("Detect comms loss", "detect"),
                ExpectedResponse("Correctly prioritise anomaly resolution order", "isolate"),
                ExpectedResponse("EPS recovery actions", "recover"),
                ExpectedResponse("Thermal recovery actions", "recover"),
                ExpectedResponse("AOCS recovery actions", "recover"),
                ExpectedResponse("Comms recovery via redundant transponder", "recover"),
            ]
        )
