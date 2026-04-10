"""SMO Simulator — Basic TCS Model.

Thermal zones, heater thermostat control, FPA cooler.
Enhanced with heater setpoint commands, auto mode, sensor drift, heater stuck-on.

Battery-heater-only active thermal control (EOSAT-1):
  - Battery heater: thermostat-controlled (only active thermal element)
  - OBC heater: manual control only (via EPS power line state)
  - Thruster heater: always OFF (no thrusters on EOSAT-1)
  - Panel temperatures coupled to solar illumination via EPS per-panel currents
"""
import math
import random
from dataclasses import dataclass, field
from typing import Any
from smo_common.models.subsystem import SubsystemModel

@dataclass
class TCSState:
    temp_panel_px: float = 15.0; temp_panel_mx: float = 12.0
    temp_panel_py: float = 20.0; temp_panel_my: float = 18.0
    temp_panel_pz: float = 10.0; temp_panel_mz: float = 8.0
    temp_obc: float = 25.0; temp_battery: float = 15.0
    temp_fpa: float = -5.0; temp_thruster: float = 5.0
    htr_battery: bool = False; htr_obc: bool = False
    htr_thruster: bool = False; cooler_fpa: bool = False
    htr_battery_failed: bool = False; htr_obc_failed: bool = False
    htr_thruster_failed: bool = False; cooler_failed: bool = False
    obc_internal_heat_w: float = 0.0
    # Manual override flags (False = auto thermostat, True = manual control)
    htr_battery_manual: bool = False
    htr_obc_manual: bool = False
    htr_thruster_manual: bool = False
    # Sensor drift offsets (failure injection)
    sensor_drift: dict = field(default_factory=dict)
    # Heater stuck-on flags
    htr_battery_stuck_on: bool = False
    htr_obc_stuck_on: bool = False
    htr_thruster_stuck_on: bool = False
    # Heater open-circuit flags (heater appears ON but provides no heat)
    htr_battery_open_circuit: bool = False
    htr_obc_open_circuit: bool = False
    htr_thruster_open_circuit: bool = False
    # ── Phase 4: Flight hardware realism ──
    htr_duty_battery: float = 0.0    # Battery heater duty cycle (0-100%)
    htr_duty_obc: float = 0.0       # OBC heater duty cycle (0-100%)
    htr_duty_thruster: float = 0.0  # Thruster heater duty cycle (0-100%)
    htr_total_power_w: float = 0.0  # Total heater power consumption
    # Duty cycle tracking (cumulative on-time in sliding window)
    _htr_on_accum: dict = field(default_factory=lambda: {
        "battery": 0.0, "obc": 0.0, "thruster": 0.0
    })
    _duty_window_s: float = 600.0  # 10-minute sliding window
    # ── Heater setpoints and limits ──
    htr_battery_setpoint_on_c: float = 1.0
    htr_battery_setpoint_off_c: float = 5.0
    htr_obc_setpoint_on_c: float = 5.0
    htr_obc_setpoint_off_c: float = 10.0
    htr_thruster_setpoint_on_c: float = 2.0
    htr_thruster_setpoint_off_c: float = 8.0
    htr_battery_duty_limit_pct: float = 100.0
    htr_obc_duty_limit_pct: float = 100.0
    htr_thruster_duty_limit_pct: float = 100.0
    # ── Decontamination heating ──
    decontamination_active: bool = False
    decontam_start_time: float = 0.0  # Relative to tick
    decontam_fpa_target_c: float = 50.0
    # ── Event tracking (to avoid repeated events) ──
    _prev_htr_battery_on: bool = False
    _prev_htr_obc_on: bool = False
    _prev_htr_thruster_on: bool = False
    _prev_temp_battery: float = 15.0
    _prev_temp_obc: float = 25.0
    _prev_temp_fpa: float = -5.0
    _prev_htr_battery_failed: bool = False
    _prev_htr_obc_failed: bool = False
    _prev_htr_thruster_failed: bool = False
    _prev_htr_battery_stuck_on: bool = False
    _prev_htr_obc_stuck_on: bool = False
    _prev_htr_thruster_stuck_on: bool = False
    _prev_fpa_ready: bool = False
    # ── Thermal zone coupling ──
    _temp_history: list = field(default_factory=lambda: [])  # For rate calculation

    # S2 Device Access — device on/off states (device_id -> on/off)
    # NOTE (DEFECT 5): S2 device commands are NOT used for EOSAT-1 thermal control.
    # All heater control is via S8 functional commands (HEATER_*, func_id 40-49).
    # This dictionary is kept for future mission variants with multi-zone control.
    device_states: dict = field(default_factory=lambda: {
        0x0300: True,   # Heater zone 1 (unused for EOSAT-1)
        0x0301: True,   # Heater zone 2 (unused for EOSAT-1)
        0x0302: True,   # Heater zone 3 (unused for EOSAT-1)
        0x0303: True,   # Heater zone 4 (unused for EOSAT-1)
        0x0304: True,   # Heater zone 5 (unused for EOSAT-1)
        0x0305: True,   # Heater zone 6 (unused for EOSAT-1)
        0x0306: True,   # Heater zone 7 (unused for EOSAT-1)
        0x0307: True,   # Heater zone 8 (unused for EOSAT-1)
        0x0308: True,   # Heater zone 9 (unused for EOSAT-1)
        0x0309: True,   # Heater zone 10 (unused for EOSAT-1)
        0x030A: True,   # Thermistor array (unused for EOSAT-1)
        0x030B: True,   # Decontamination heater (unused for EOSAT-1)
    })


class TCSBasicModel(SubsystemModel):
    _HEATER_POWER = {"battery": 6.0, "obc": 4.0, "thruster": 8.0}
    _THERMOSTAT = {"battery": (5.0, 1.0), "obc": (10.0, 5.0), "thruster": (8.0, 2.0)}
    _TAU = {"panel_px":600,"panel_mx":600,"panel_py":500,"panel_my":500,
            "panel_pz":800,"panel_mz":800,"obc":900,"battery":1200,"fpa":120,"thruster":400}
    _CAP = {"panel_px":5000,"panel_mx":5000,"panel_py":4000,"panel_my":4000,
            "panel_pz":3000,"panel_mz":3000,"obc":800,"battery":2000,"fpa":100,"thruster":500}

    def __init__(self):
        self._state = TCSState()
        self._param_ids: dict[str, int] = {}
        self._fpa_cooler_target: float = -5.0
        self._engine = None
        # Instance copies so configure() doesn't mutate class-level dicts
        self._TAU = dict(TCSBasicModel._TAU)
        self._CAP = dict(TCSBasicModel._CAP)
        self._HEATER_POWER = dict(TCSBasicModel._HEATER_POWER)
        self._THERMOSTAT = dict(TCSBasicModel._THERMOSTAT)
        # Thermal zone conductance matrix (conduction between adjacent zones) [W/K]
        self._zone_conductance = {
            ("obc", "battery"): 0.5,
            ("battery", "obc"): 0.5,
            ("structure", "electronics"): 0.8,
            ("electronics", "structure"): 0.8,
            ("payload", "structure"): 0.4,
            ("structure", "payload"): 0.4,
        }
        # Radiation to space (effective conductance)
        self._space_radiation_g = 0.2  # W/K (radiative coupling to 3K sink)
        # Event thresholds (can be overridden by config)
        self._temp_warning_limits = {
            "battery": (0.0, 40.0),  # (low_warn, high_warn)
            "obc": (5.0, 50.0),
            "fpa": (-50.0, -5.0),
        }
        self._temp_alarm_limits = {
            "battery": (-10.0, 50.0),
            "obc": (-5.0, 60.0),
            "fpa": (-60.0, 0.0),
        }
        self._thermal_runaway_rate = 2.0  # deg/min

    @property
    def name(self) -> str: return "tcs"

    def configure(self, config: dict[str, Any]) -> None:
        self._param_ids = config.get("param_ids", {
            "temp_panel_px":0x0400,"temp_panel_mx":0x0401,"temp_panel_py":0x0402,
            "temp_panel_my":0x0403,"temp_panel_pz":0x0404,"temp_panel_mz":0x0405,
            "temp_obc":0x0406,"temp_battery":0x0407,"temp_fpa":0x0408,
            "temp_thruster":0x0409,"htr_battery":0x040A,"htr_obc":0x040B,
            "cooler_fpa":0x040C,"htr_thruster":0x040D,
        })
        # Override thermal zone constants from config
        for zone in config.get("zones", []):
            name = zone.get("name") if isinstance(zone, dict) else getattr(zone, "name", None)
            if not name:
                continue
            tau = zone.get("time_constant_s") if isinstance(zone, dict) else getattr(zone, "time_constant_s", None)
            cap = zone.get("capacitance_j_per_c") if isinstance(zone, dict) else getattr(zone, "capacitance_j_per_c", None)
            init_temp = zone.get("initial_temp_c") if isinstance(zone, dict) else getattr(zone, "initial_temp_c", None)
            if tau is not None:
                self._TAU[name] = tau
            if cap is not None:
                self._CAP[name] = cap
            if init_temp is not None:
                attr = f"temp_{name}"
                if hasattr(self._state, attr):
                    setattr(self._state, attr, init_temp)
        # Override heater constants from config
        for heater in config.get("heaters", []):
            name = heater.get("name") if isinstance(heater, dict) else getattr(heater, "name", None)
            if not name:
                continue
            power = heater.get("power_w") if isinstance(heater, dict) else getattr(heater, "power_w", None)
            on_t = heater.get("on_temp_c") if isinstance(heater, dict) else getattr(heater, "on_temp_c", None)
            off_t = heater.get("off_temp_c") if isinstance(heater, dict) else getattr(heater, "off_temp_c", None)
            if power is not None:
                self._HEATER_POWER[name] = power
            if on_t is not None and off_t is not None:
                self._THERMOSTAT[name] = (off_t, on_t)
        # FPA cooler target
        self._fpa_cooler_target = config.get("fpa_cooler_target_c", -5.0)

    def _thermostat_control(self, circuit: str, temp: float) -> None:
        """Apply thermostat control for a heater circuit (auto mode only)."""
        s = self._state
        manual_flag = getattr(s, f"htr_{circuit}_manual", False)
        stuck_on = getattr(s, f"htr_{circuit}_stuck_on", False)
        failed = getattr(s, f"htr_{circuit}_failed", False)

        if stuck_on:
            # Heater stays on regardless of commands
            setattr(s, f"htr_{circuit}", True)
            return
        if manual_flag:
            # Manual mode — don't apply thermostat
            return
        if failed:
            setattr(s, f"htr_{circuit}", False)
            return

        # Get dynamic setpoints (allow override via commands)
        on_attr = f"htr_{circuit}_setpoint_on_c"
        off_attr = f"htr_{circuit}_setpoint_off_c"
        lo = getattr(s, on_attr, self._THERMOSTAT[circuit][1])
        hi = getattr(s, off_attr, self._THERMOSTAT[circuit][0])

        if temp <= lo:
            setattr(s, f"htr_{circuit}", True)
        elif temp >= hi:
            setattr(s, f"htr_{circuit}", False)

    def _apply_thermal_coupling(self, dt: float) -> None:
        """Apply conductive and radiative heat transfer between zones."""
        s = self._state
        # Coupling between battery and OBC (shared electronics box)
        g_bat_obc = self._zone_conductance.get(("battery", "obc"), 0.5)
        heat_flow = g_bat_obc * (s.temp_obc - s.temp_battery)
        s.temp_battery += (heat_flow / self._CAP["battery"]) * dt
        s.temp_obc -= (heat_flow / self._CAP["obc"]) * dt

        # Radiation cooling to space (all external zones)
        for zone in ["panel_px", "panel_mx", "panel_py", "panel_my", "panel_pz", "panel_mz"]:
            T = getattr(s, f"temp_{zone}", 0.0)
            # Stefan-Boltzmann: rad cooling proportional to T difference from 3K space
            rad_loss = self._space_radiation_g * (T - (-270.15))  # 3K in Celsius
            setattr(s, f"temp_{zone}", T - (rad_loss / self._CAP.get(zone, 1000)) * dt)

    def _generate_events(self, dt: float) -> None:
        """Generate TCS events with edge detection."""
        if not hasattr(self, '_engine') or not self._engine:
            return

        s = self._state
        events = []

        # Helper: emit event with edge detection
        def emit_if_changed(event_dict: dict, condition_now: bool, prev_attr: str) -> None:
            prev = getattr(s, prev_attr, False)
            if condition_now and not prev:
                events.append(event_dict)
                setattr(s, prev_attr, True)
            elif not condition_now and prev:
                setattr(s, prev_attr, False)

        # Temperature warning/alarm events
        bat_temp = s.temp_battery
        obc_temp = s.temp_obc
        fpa_temp = s.temp_fpa

        # Battery overtemp warning
        bat_warn_low, bat_warn_high = self._temp_warning_limits["battery"]
        emit_if_changed(
            {"event_id": 0x0400, "severity": "MEDIUM", "subsystem": "tcs",
             "description": f"TCS_OVERTEMP_WARNING: Battery {bat_temp:.1f}C"},
            (bat_temp > bat_warn_high),
            "_prev_bat_overtemp_warn"
        )

        # Battery overtemp alarm
        bat_alarm_low, bat_alarm_high = self._temp_alarm_limits["battery"]
        emit_if_changed(
            {"event_id": 0x0401, "severity": "HIGH", "subsystem": "tcs",
             "description": f"TCS_OVERTEMP_ALARM: Battery {bat_temp:.1f}C"},
            (bat_temp > bat_alarm_high),
            "_prev_bat_overtemp_alarm"
        )

        # Battery undertemp warning
        emit_if_changed(
            {"event_id": 0x0402, "severity": "MEDIUM", "subsystem": "tcs",
             "description": f"TCS_UNDERTEMP_WARNING: Battery {bat_temp:.1f}C"},
            (bat_temp < bat_warn_low),
            "_prev_bat_undertemp_warn"
        )

        # Battery undertemp alarm
        emit_if_changed(
            {"event_id": 0x0403, "severity": "HIGH", "subsystem": "tcs",
             "description": f"TCS_UNDERTEMP_ALARM: Battery {bat_temp:.1f}C"},
            (bat_temp < bat_alarm_low),
            "_prev_bat_undertemp_alarm"
        )

        # Heater on/off events
        if s.htr_battery != s._prev_htr_battery_on:
            if s.htr_battery:
                events.append({"event_id": 0x0404, "severity": "INFO", "subsystem": "tcs",
                              "description": "HEATER_ON: Battery heater activated"})
            else:
                events.append({"event_id": 0x0405, "severity": "INFO", "subsystem": "tcs",
                              "description": "HEATER_OFF: Battery heater deactivated"})
            s._prev_htr_battery_on = s.htr_battery

        # Heater failure events
        if s.htr_battery_stuck_on != s._prev_htr_battery_stuck_on:
            if s.htr_battery_stuck_on:
                events.append({"event_id": 0x0406, "severity": "HIGH", "subsystem": "tcs",
                              "description": "HEATER_STUCK_ON: Battery heater cannot turn off"})
            s._prev_htr_battery_stuck_on = s.htr_battery_stuck_on

        if s.htr_battery_failed != s._prev_htr_battery_failed:
            if s.htr_battery_failed:
                events.append({"event_id": 0x0407, "severity": "HIGH", "subsystem": "tcs",
                              "description": "HEATER_STUCK_OFF: Battery heater circuit failure"})
            s._prev_htr_battery_failed = s.htr_battery_failed

        # Mode change event
        decontam_now = s.decontamination_active
        prev_decontam = getattr(s, "_prev_decontam_active", False)
        if decontam_now != prev_decontam:
            events.append({"event_id": 0x0408, "severity": "INFO", "subsystem": "tcs",
                          "description": f"TCS_MODE_CHANGE: Decontamination {'START' if decontam_now else 'STOP'}"})
            s._prev_decontam_active = decontam_now

        # Thermal runaway detection (temperature rate > 2 deg/min)
        if len(s._temp_history) > 1:
            prev_time, prev_temp = s._temp_history[-1]
            curr_time = 0  # Current time in this tick
            rate_deg_per_min = (bat_temp - prev_temp) / max(dt / 60.0, 0.01)
            if abs(rate_deg_per_min) > self._thermal_runaway_rate:
                events.append({"event_id": 0x0409, "severity": "HIGH", "subsystem": "tcs",
                              "description": f"THERMAL_RUNAWAY: Battery rate {rate_deg_per_min:.1f} deg/min"})

        # Track temperature for rate calculation
        s._temp_history.append((0, bat_temp))
        if len(s._temp_history) > 10:
            s._temp_history.pop(0)

        # FPA thermal readiness
        fpa_warn_low, fpa_warn_high = self._temp_warning_limits["fpa"]
        fpa_ready = fpa_warn_low <= fpa_temp <= fpa_warn_high
        if fpa_ready and not s._prev_fpa_ready:
            events.append({"event_id": 0x040A, "severity": "INFO", "subsystem": "tcs",
                          "description": f"FPA_THERMAL_READY: FPA {fpa_temp:.1f}C in operational range"})
            s._prev_fpa_ready = True
        elif not fpa_ready and s._prev_fpa_ready:
            events.append({"event_id": 0x040B, "severity": "MEDIUM", "subsystem": "tcs",
                          "description": f"FPA_THERMAL_NOT_READY: FPA {fpa_temp:.1f}C out of range"})
            s._prev_fpa_ready = False

        # Dispatch all events to the engine
        for event in events:
            try:
                self._engine.event_queue.put_nowait(event)
            except:
                pass  # Queue full, skip event

    def tick(self, dt: float, orbit_state: Any, shared_params: dict[int, float]) -> None:
        s = self._state
        if orbit_state.in_eclipse:
            env_ext, env_int = -30.0, 10.0
        else:
            beta = orbit_state.solar_beta_deg
            sun_f = abs(math.cos(math.radians(beta)))
            env_ext, env_int = -10.0 + 50.0*sun_f, 12.0

        # Apply thermal coupling between zones
        self._apply_thermal_coupling(dt)

        # --- Panel temperatures with solar illumination coupling ---
        # Read per-panel solar currents from EPS (proxy for illumination)
        panel_faces = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        panel_solar_param_ids = [0x012B, 0x012C, 0x012D, 0x012E, 0x012F, 0x0130]
        panel_base_envs = [env_ext+5, env_ext, env_ext+8, env_ext, env_ext-5, env_ext-5]

        for face, pid, base_env in zip(panel_faces, panel_solar_param_ids, panel_base_envs):
            attr = f"temp_panel_{face}"
            key = f"panel_{face}"
            T = getattr(s, attr)
            solar_current = shared_params.get(pid, 0.0)
            # Solar heating proportional to current (~30% of solar power becomes heat)
            solar_heat_w = solar_current * 28.0 * 0.3
            heat_input = solar_heat_w * 10.0  # Scale factor for temperature effect
            env = base_env + heat_input
            T += (env - T) / self._TAU[key] * dt + random.gauss(0, 0.05)
            setattr(s, attr, T)

        # --- Battery temp: thermostat-controlled, gated on EPS power line ---
        # EPS power line 5 (htr_bat) must be ON for battery heater to operate
        bat_eps_on = bool(shared_params.get(0x0115, 0))
        if bat_eps_on:
            self._thermostat_control("battery", s.temp_battery)
        else:
            s.htr_battery = False  # No power → heater off
        # Apply heater stuck-on / open-circuit failure overrides
        if s.htr_battery_stuck_on:
            s.htr_battery = True  # Cannot turn off
        bat_pwr = self._HEATER_POWER["battery"] if s.htr_battery else 0.0
        if s.htr_battery_open_circuit:
            bat_pwr = 0.0  # Heater appears ON but provides no heat
        s.temp_battery += ((env_int-s.temp_battery)/self._TAU["battery"]+bat_pwr/self._CAP["battery"])*dt+random.gauss(0,0.02)

        # --- OBC temp: manual control only (via EPS power line state) ---
        # No thermostat — heater state is whatever the operator set via
        # power_line_on/off commands. Read from EPS power line 6 (htr_obc).
        s.htr_obc = bool(shared_params.get(0x0116, 0))
        pwr = self._HEATER_POWER["obc"] if s.htr_obc else 0.0
        if s.htr_obc_open_circuit:
            pwr = 0.0
        s.temp_obc += ((env_int-s.temp_obc)/self._TAU["obc"]+(pwr+s.obc_internal_heat_w)/self._CAP["obc"])*dt+random.gauss(0,0.03)

        # --- FPA temp ---
        cool = (self._fpa_cooler_target - 20.0) if (s.cooler_fpa and not s.cooler_failed) else 0.0
        s.temp_fpa += (env_int+cool-s.temp_fpa)/self._TAU["fpa"]*dt+random.gauss(0,0.02)

        # --- Thruster temp: passive zone, no active heater (EOSAT-1 has no thrusters) ---
        s.htr_thruster = False  # Always off — no thruster heater on EOSAT-1
        s.temp_thruster += ((env_int-5-s.temp_thruster)/self._TAU["thruster"])*dt+random.gauss(0,0.03)

        # Phase 4: Heater duty cycle tracking
        decay = dt / s._duty_window_s
        for circuit in ("battery", "obc", "thruster"):
            on = getattr(s, f"htr_{circuit}")
            if on:
                s._htr_on_accum[circuit] += dt
            # Exponential decay to approximate sliding window
            s._htr_on_accum[circuit] *= (1.0 - decay)
            duty = min(100.0, s._htr_on_accum[circuit] / s._duty_window_s * 100.0)
            setattr(s, f"htr_duty_{circuit}", duty)

        # Total heater power
        s.htr_total_power_w = 0.0
        if s.htr_battery:
            s.htr_total_power_w += self._HEATER_POWER["battery"]
        if s.htr_obc:
            s.htr_total_power_w += self._HEATER_POWER["obc"]
        if s.htr_thruster:
            s.htr_total_power_w += self._HEATER_POWER["thruster"]

        # Write shared params — apply sensor drift offsets for failure simulation
        p = self._param_ids
        drift = s.sensor_drift

        shared_params[p.get("temp_panel_px",0x0400)] = s.temp_panel_px + drift.get("panel_px", 0.0)
        shared_params[p.get("temp_panel_mx",0x0401)] = s.temp_panel_mx + drift.get("panel_mx", 0.0)
        shared_params[p.get("temp_panel_py",0x0402)] = s.temp_panel_py + drift.get("panel_py", 0.0)
        shared_params[p.get("temp_panel_my",0x0403)] = s.temp_panel_my + drift.get("panel_my", 0.0)
        shared_params[p.get("temp_panel_pz",0x0404)] = s.temp_panel_pz + drift.get("panel_pz", 0.0)
        shared_params[p.get("temp_panel_mz",0x0405)] = s.temp_panel_mz + drift.get("panel_mz", 0.0)
        shared_params[p.get("temp_obc",0x0406)] = s.temp_obc + drift.get("obc", 0.0)
        shared_params[p.get("temp_battery",0x0407)] = s.temp_battery + drift.get("battery", 0.0)
        shared_params[p.get("temp_fpa",0x0408)] = s.temp_fpa + drift.get("fpa", 0.0)
        shared_params[p.get("temp_thruster",0x0409)] = s.temp_thruster + drift.get("thruster", 0.0)
        shared_params[p.get("htr_battery",0x040A)] = 1 if s.htr_battery else 0
        shared_params[p.get("htr_obc",0x040B)] = 1 if s.htr_obc else 0
        shared_params[p.get("cooler_fpa",0x040C)] = 1 if s.cooler_fpa else 0
        shared_params[p.get("htr_thruster",0x040D)] = 1 if s.htr_thruster else 0
        # Phase 4: Flight hardware params
        shared_params[0x040E] = s.htr_duty_battery
        shared_params[0x040F] = s.htr_duty_obc
        shared_params[0x0410] = s.htr_duty_thruster
        shared_params[0x0411] = s.htr_total_power_w
        # Panel temperatures (illumination-coupled, secondary param IDs)
        shared_params[0x0412] = s.temp_panel_px + drift.get("panel_px", 0.0)
        shared_params[0x0413] = s.temp_panel_mx + drift.get("panel_mx", 0.0)
        shared_params[0x0414] = s.temp_panel_py + drift.get("panel_py", 0.0)
        shared_params[0x0415] = s.temp_panel_my + drift.get("panel_my", 0.0)
        shared_params[0x0416] = s.temp_panel_pz + drift.get("panel_pz", 0.0)
        shared_params[0x0417] = s.temp_panel_mz + drift.get("panel_mz", 0.0)

        # New telemetry parameters (Phase 5: Event generation support)
        shared_params[0x0418] = 1.0 if s.decontamination_active else 0.0  # DECONTAMINATION_ACTIVE
        # Thermal gradient (max temp difference between zones)
        all_temps = [s.temp_panel_px, s.temp_panel_mx, s.temp_panel_py, s.temp_panel_my,
                     s.temp_panel_pz, s.temp_panel_mz, s.temp_obc, s.temp_battery, s.temp_fpa]
        thermal_gradient = max(all_temps) - min(all_temps)
        shared_params[0x0419] = thermal_gradient

        # Setpoint readback (0x0330, 0x0331) — DEFECT 3 fix
        # Allow operators to verify heater setpoints after command via S20 parameter read
        shared_params[0x0330] = s.htr_battery_setpoint_on_c
        shared_params[0x0331] = s.htr_obc_setpoint_on_c

        # Generate events
        self._generate_events(dt)

    def get_telemetry(self) -> dict[int, float]:
        return {0x0408: self._state.temp_fpa, 0x0407: self._state.temp_battery}

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        if command == "heater":
            circuit = cmd.get("circuit", "battery")
            on = bool(cmd.get("on", True))
            failed = getattr(self._state, f"htr_{circuit}_failed", False)
            stuck = getattr(self._state, f"htr_{circuit}_stuck_on", False)
            if stuck:
                return {"success": False, "message": "Heater stuck on, cannot control"}
            if failed and on:
                return {"success": False, "message": "Heater failed"}
            setattr(self._state, f"htr_{circuit}", on)
            # Manual override — disable thermostat auto control
            setattr(self._state, f"htr_{circuit}_manual", True)
            return {"success": True}
        elif command == "set_setpoint":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            else:
                circuit = str(circuit_idx)
            on_temp = cmd.get("on_temp")
            off_temp = cmd.get("off_temp")
            if on_temp is not None and off_temp is not None:
                # Update both instance dict and state object
                self._THERMOSTAT[circuit] = (float(off_temp), float(on_temp))
                setattr(self._state, f"htr_{circuit}_setpoint_on_c", float(on_temp))
                setattr(self._state, f"htr_{circuit}_setpoint_off_c", float(off_temp))
                return {"success": True}
            return {"success": False, "message": "Missing on_temp or off_temp"}
        elif command == "auto_mode":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            else:
                circuit = str(circuit_idx)
            setattr(self._state, f"htr_{circuit}_manual", False)
            return {"success": True}
        elif command == "fpa_cooler":
            on = bool(cmd.get("on", True))
            if self._state.cooler_failed and on: return {"success": False}
            self._state.cooler_fpa = on
            return {"success": True}
        elif command == "set_heater_duty_limit":
            circuit_idx = cmd.get("circuit", 0)
            circuits = ["battery", "obc", "thruster"]
            if isinstance(circuit_idx, int) and 0 <= circuit_idx < len(circuits):
                circuit = circuits[circuit_idx]
            else:
                circuit = str(circuit_idx)
            duty_pct = cmd.get("duty_limit_pct", 100.0)
            duty_pct = max(0.0, min(100.0, float(duty_pct)))
            setattr(self._state, f"htr_{circuit}_duty_limit_pct", duty_pct)
            return {"success": True, "message": f"Heater {circuit} duty limit set to {duty_pct}%"}
        elif command == "decontamination_start":
            self._state.decontamination_active = True
            self._state.decontam_start_time = 0.0
            self._state.decontam_fpa_target_c = cmd.get("target_temp_c", 50.0)
            return {"success": True, "message": "Decontamination heating started"}
        elif command == "decontamination_stop":
            self._state.decontamination_active = False
            return {"success": True, "message": "Decontamination heating stopped"}
        elif command == "get_thermal_map":
            thermal_map = {
                "temp_panel_px": self._state.temp_panel_px,
                "temp_panel_mx": self._state.temp_panel_mx,
                "temp_panel_py": self._state.temp_panel_py,
                "temp_panel_my": self._state.temp_panel_my,
                "temp_panel_pz": self._state.temp_panel_pz,
                "temp_panel_mz": self._state.temp_panel_mz,
                "temp_obc": self._state.temp_obc,
                "temp_battery": self._state.temp_battery,
                "temp_fpa": self._state.temp_fpa,
                "temp_thruster": self._state.temp_thruster,
            }
            return {"success": True, "thermal_map": thermal_map}
        return {"success": False, "message": f"Unknown: {command}"}

    def inject_failure(self, failure: str, magnitude: float = 1.0, **kw) -> None:
        if failure == "heater_failure":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_failed", bool(magnitude))
            if magnitude: setattr(self._state, f"htr_{c}", False)
        elif failure == "cooler_failure":
            self._state.cooler_failed = bool(magnitude)
            if magnitude: self._state.cooler_fpa = False
        elif failure == "obc_thermal":
            self._state.obc_internal_heat_w = float(kw.get("heat_w", 30.0))
        elif failure == "sensor_drift":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift[zone] = magnitude
        elif failure == "heater_stuck_on":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_stuck_on", True)
            setattr(self._state, f"htr_{c}", True)
        elif failure == "heater_open_circuit":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_open_circuit", True)

        elif failure == "temp_anomaly":
            # Force a sensor over its red line — drives thermal_exceedance.md.
            zone = kw.get("zone", "battery")
            offset_c = float(kw.get("offset_c", magnitude * 30.0))
            attr = f"temp_{zone}"
            if hasattr(self._state, attr):
                setattr(self._state, attr, getattr(self._state, attr) + offset_c)
            # Persist via sensor_drift so subsequent ticks keep it elevated.
            self._state.sensor_drift[zone] = self._state.sensor_drift.get(zone, 0.0) + offset_c

    def clear_failure(self, failure: str, **kw) -> None:
        if failure == "heater_failure":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_failed", False)
        elif failure == "cooler_failure":
            self._state.cooler_failed = False
        elif failure == "obc_thermal":
            self._state.obc_internal_heat_w = 0.0
        elif failure == "sensor_drift":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift.pop(zone, None)
        elif failure == "heater_stuck_on":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_stuck_on", False)
        elif failure == "heater_open_circuit":
            c = kw.get("circuit", "battery")
            setattr(self._state, f"htr_{c}_open_circuit", False)

        elif failure == "temp_anomaly":
            zone = kw.get("zone", "battery")
            self._state.sensor_drift.pop(zone, None)

    def get_state(self) -> dict[str, Any]:
        import dataclasses; return dataclasses.asdict(self._state)

    def set_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            if hasattr(self._state, k): setattr(self._state, k, v)

    def get_battery_temp(self) -> float: return self._state.temp_battery

    # S2 Device Access — device-level on/off control (reserved for future multi-zone variants)
    # NOTE (DEFECT 5): Not used for EOSAT-1. All control via S8 functional commands.
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful.
        Reserved for future missions with zone-level heater control."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid.
        Reserved for future missions with zone-level heater control."""
        return self._state.device_states.get(device_id, False)
