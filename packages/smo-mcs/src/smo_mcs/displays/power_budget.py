"""Power Budget Monitor Panel.

Displays power generation/consumption, battery SoC, margin, eclipse status, etc.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class PowerBudget:
    """Current power budget state."""
    power_gen_w: float  # Current generation in watts
    power_cons_w: float  # Current consumption in watts
    battery_soc_percent: float  # Battery state of charge 0-100
    battery_temp_c: float  # Battery temperature
    load_shedding_stage: int  # 0=none, 1=low, 2=medium, 3=critical
    eclipse_active: bool
    time_to_eclipse_entry_s: Optional[float]
    time_to_eclipse_exit_s: Optional[float]
    per_subsystem_power: dict[str, float]  # subsystem -> watts

    @property
    def power_margin_w(self) -> float:
        """Margin between generation and consumption."""
        return self.power_gen_w - self.power_cons_w

    #: Dead-band on the instantaneous margin, in watts. `power_cons` carries a
    #: 1 W-sigma noise term, so anything tighter than ~3 W would make the label
    #: flicker on measurement noise alone.
    MARGIN_DEADBAND_W = 3.0

    @property
    def soc_trend(self) -> str:
        """Is the spacecraft gaining or losing energy right now?

        The thresholds used to be ``> +50 W`` for "charging" and ``< -10 W``
        for "discharging" — numbers calibrated for a spacecraft that does not
        exist. EO-Sat-1's ENTIRE generation is ~85 W sunlit (88.6 W peak after
        the R1 re-sizing) and ~38 W orbit-average, so a +50 W margin is
        reachable only in a quiescent, wings-deployed, low-beta, sunlit corner
        case. Normal nominal operations — a real, healthy, battery-charging
        state at +9 to +16 W — reported "stable": the one word on the panel
        that exists to answer "am I gaining or losing energy?" was wrong for
        most of the mission (EPS owner R-1).

        The band is now proportional to this spacecraft and sits just outside
        the measurement noise.
        """
        if self.power_margin_w > self.MARGIN_DEADBAND_W:
            return "charging"
        if self.power_margin_w < -self.MARGIN_DEADBAND_W:
            return "discharging"
        return "stable"


class PowerBudgetMonitor:
    """Monitors and displays power budget."""

    #: Telemetry key each subsystem's power draw arrives under. Phase B added
    #: 0x013C-0x0141 (HK SID 8); before that the breakdown below was a set of
    #: hard-coded constants that `update_from_telemetry` never touched, so the
    #: panel reported a fabricated budget that never moved (DI-03 class).
    _SUBSYS_POWER_KEYS = {
        "eps": "subsys_power_eps",
        "aocs": "subsys_power_aocs",
        "tcs": "subsys_power_tcs",
        "ttc": "subsys_power_ttc",
        "payload": "subsys_power_payload",
        "obdh": "subsys_power_obdh",
    }

    def __init__(self):
        self._current_state = PowerBudget(
            power_gen_w=0.0,
            power_cons_w=0.0,
            battery_soc_percent=0.0,
            battery_temp_c=0.0,
            load_shedding_stage=0,
            eclipse_active=False,
            time_to_eclipse_entry_s=None,
            time_to_eclipse_exit_s=None,
            per_subsystem_power={},
        )
        #: False until telemetry has actually been seen. The console must be
        #: able to tell "no data" from "0 W" — a power budget that reads as a
        #: plausible spacecraft when nothing is connected is the worst
        #: possible display.
        self._has_data = False

    def update_from_telemetry(self, state: dict) -> None:
        """Update power budget from telemetry state."""
        eps = state.get("eps", {}) or {}

        def _f(key, default=0.0):
            val = eps.get(key)
            return float(val) if isinstance(val, (int, float)) else default

        self._current_state.power_gen_w = _f("power_gen_W", _f("power_gen"))
        self._current_state.power_cons_w = _f("power_cons_W", _f("power_cons"))
        self._current_state.battery_soc_percent = _f("soc_pct", _f("bat_soc"))
        self._current_state.battery_temp_c = _f("bat_temp_C", _f("bat_temp"))
        self._current_state.load_shedding_stage = int(_f("load_shed_stage"))

        # Per-subsystem breakdown, straight from HK SID 8 where present.
        breakdown = {}
        for name, key in self._SUBSYS_POWER_KEYS.items():
            val = eps.get(key)
            if isinstance(val, (int, float)):
                breakdown[name] = float(val)
        self._current_state.per_subsystem_power = breakdown

        # Eclipse status (may be in state or computed)
        self._current_state.eclipse_active = bool(state.get("in_eclipse", state.get("eclipse_active", False)))
        self._current_state.time_to_eclipse_entry_s = state.get("time_to_eclipse_entry_s")
        self._current_state.time_to_eclipse_exit_s = state.get("time_to_eclipse_exit_s")
        if eps:
            self._has_data = True

    def get_display_data(self) -> dict:
        """Get display data for the power budget panel."""
        state = self._current_state
        return {
            "has_data": self._has_data,
            "power_gen_w": round(state.power_gen_w, 1),
            "power_cons_w": round(state.power_cons_w, 1),
            "power_margin_w": round(state.power_margin_w, 1),
            "battery_soc_percent": round(state.battery_soc_percent, 1),
            "battery_temp_c": round(state.battery_temp_c, 1),
            "soc_trend": state.soc_trend,
            "load_shedding_stage": state.load_shedding_stage,
            "load_shedding_label": self._stage_to_label(state.load_shedding_stage),
            "eclipse_active": state.eclipse_active,
            "time_to_eclipse_entry_s": state.time_to_eclipse_entry_s,
            "time_to_eclipse_exit_s": state.time_to_eclipse_exit_s,
            "per_subsystem_power": {
                k: round(v, 1) for k, v in state.per_subsystem_power.items()
            },
            "total_subsystem_power": round(sum(state.per_subsystem_power.values()), 1),
            # EPS owner R-2. 0x013C-0x0141 live ONLY in HK SID 8, which is not
            # in simulation.yaml's `default_enabled_sids: [1, 11]`. On a fresh
            # session the breakdown is therefore empty, and the panel used to
            # show nothing without saying why. State the dependency instead.
            "breakdown_available": bool(state.per_subsystem_power),
            "breakdown_requires": {"sid": 8, "command": "TC(3,5) HK_ENABLE sid=8"},
            "breakdown_note": (
                ""
                if state.per_subsystem_power else
                "Per-subsystem power (0x013C-0x0141) is carried only by HK "
                "SID 8, which is not enabled by default — send TC(3,5) "
                "HK_ENABLE sid=8 to populate this table."
            ),
            # The six values now sum to 0x0106 identically (the EPS owner
            # closed the gaps: 0x013C was hard-wired 0.0, and the propulsion
            # line, an injected bus short and the measurement noise belonged to
            # no bucket). Publish the reconciliation, because it is the check
            # that would have caught the old defect.
            "breakdown_residual_w": (
                round(state.power_cons_w - sum(state.per_subsystem_power.values()), 1)
                if state.per_subsystem_power else None
            ),
        }

    @staticmethod
    def _stage_to_label(stage: int) -> str:
        """Map load shedding stage to label."""
        labels = {
            0: "NOMINAL",
            1: "LOW",
            2: "MEDIUM",
            3: "CRITICAL",
        }
        return labels.get(stage, "UNKNOWN")
