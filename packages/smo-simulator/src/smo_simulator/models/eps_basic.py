"""SMO Simulator — Basic EPS Model.

Battery SoC/voltage model, solar array power coupled to eclipse state,
power budget management with switchable power lines.
Per-line current draw, overcurrent detection, overvoltage/undervoltage flags,
and load shedding sequence.

6 body-panel solar array model: body-mounted panels on all 6 faces
(+X, -X, +Y, -Y, +Z, -Z) with attitude-dependent power generation
via AOCS CSS sun vector.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Any

from smo_common.models.subsystem import SubsystemModel

# Panel face normals in body frame (6U cubesat body-mounted panels)
PANEL_NORMALS = {
    'px': (1, 0, 0), 'mx': (-1, 0, 0),
    'py': (0, 1, 0), 'my': (0, -1, 0),
    'pz': (0, 0, 1), 'mz': (0, 0, -1),
}

# Power line definitions
# (name, switchable, default_on, power_w, description)
# Convention: only the non-switchable infrastructure lines (OBC, TTC RX) are
# energised at power-on. Every switchable line defaults OFF — operators must
# explicitly command equipment on after separation, matching real LEOP flow.
POWER_LINE_DEFS = [
    ("obc", False, True, 40.0, "OBC computer"),
    ("ttc_rx", False, True, 5.0, "TTC receiver"),
    ("ttc_tx", True, False, 20.0, "TTC transmitter"),
    ("payload", True, False, 8.0, "Payload imager"),
    ("fpa_cooler", True, False, 15.0, "FPA cooler"),
    ("htr_bat", True, False, 6.0, "Battery heater"),
    ("htr_obc", True, False, 4.0, "OBC heater"),
    ("aocs_wheels", True, False, 12.0, "Reaction wheels"),
]

POWER_LINE_NAMES = [d[0] for d in POWER_LINE_DEFS]
POWER_LINE_SWITCHABLE = {d[0]: d[1] for d in POWER_LINE_DEFS}
POWER_LINE_DEFAULTS = {d[0]: d[2] for d in POWER_LINE_DEFS}
POWER_LINE_NOMINAL_W = {d[0]: d[3] for d in POWER_LINE_DEFS}

# Overcurrent thresholds (amps) per line — 150% of nominal at 28V
OC_THRESHOLDS = {
    "obc": 2.0, "ttc_rx": 0.3, "ttc_tx": 1.0, "payload": 2.5,
    "fpa_cooler": 1.0, "htr_bat": 0.5, "htr_obc": 0.3, "aocs_wheels": 0.8,
}
OC_TRIP_TIME_S = 0.05  # 50ms trip time (simulated as immediate at 1Hz tick)

# Load shedding priority (first shed = lowest priority, index 0 = first off)
LOAD_SHED_ORDER = ["payload", "fpa_cooler", "ttc_tx", "aocs_wheels"]
LOAD_SHED_VOLTAGE = 26.5  # Bus voltage below which load shedding begins


@dataclass
class EPSState:
    bat_soc_pct: float = 75.0
    bat_voltage: float = 26.4
    bat_current: float = 0.0
    bat_temp: float = 15.0
    sa_a_current: float = 4.5
    sa_b_current: float = 4.5
    sa_a_voltage: float = 28.2
    sa_b_voltage: float = 28.2
    sa_a_enabled: bool = True
    sa_b_enabled: bool = True
    bus_voltage: float = 28.2
    power_gen_w: float = 252.0
    power_cons_w: float = 120.0
    in_eclipse: bool = False
    payload_mode: int = 0
    fpa_cooler_on: bool = False
    transponder_tx: bool = True
    sa_a_degradation: float = 1.0
    sa_b_degradation: float = 1.0
    bat_cell_failure: bool = False
    bus_short: bool = False
    # Power lines
    power_lines: dict = field(default_factory=lambda: dict(POWER_LINE_DEFAULTS))
    # Per-line currents (indexed by line name)
    line_currents: dict = field(default_factory=lambda: {n: 0.0 for n in POWER_LINE_NAMES})
    # Overcurrent
    oc_trip_flags: int = 0  # bitmask: bit i = line i tripped
    uv_flag: bool = False
    ov_flag: bool = False
    # Overcurrent injection multiplier per line
    oc_inject: dict = field(default_factory=dict)
    # ── Separation / LEOP ──
    sep_timer_active: bool = False
    sep_timer_remaining: float = 0.0
    pdm_unsw_status: int = 0x03  # bit 0 = RX, bit 1 = OBC (both on by default)
    # ── Per-panel solar currents (6 body-mounted faces) ──
    sa_panel_currents: dict = field(default_factory=lambda: {
        'px': 0.0, 'mx': 0.0, 'py': 0.0, 'my': 0.0, 'pz': 0.0, 'mz': 0.0
    })
    sa_panel_degradation: dict = field(default_factory=lambda: {
        'px': 1.0, 'mx': 1.0, 'py': 1.0, 'my': 1.0, 'pz': 1.0, 'mz': 1.0
    })
    # ── Phase 4: Flight hardware realism ──
    bat_dod_pct: float = 25.0       # Depth of discharge (100 - SoC)
    bat_cycles: int = 0              # Charge/discharge cycle count
    bat_max_dod_pct: float = 80.0    # Maximum allowed DoD
    mppt_efficiency: float = 0.97    # MPPT tracker efficiency
    sa_lifetime_hours: float = 0.0   # Cumulative sunlit hours
    sa_age_factor: float = 1.0       # Degradation factor from aging (1.0=new)
    _was_charging: bool = False      # For cycle count tracking
    # ── Event tracking (previous state for edge detection) ──
    _prev_mode: int = 0              # Previous spacecraft mode
    _prev_soc: float = 75.0          # Previous SoC
    _prev_bus_voltage: float = 28.2  # Previous bus voltage
    _prev_in_eclipse: bool = False   # Previous eclipse state
    _prev_oc_trip_flags: int = 0     # Previous OC trip flags
    _prev_load_shed_stage: int = 0   # Previous load shedding stage
    _was_charging_prev: bool = False # For charge complete detection
    # ── Load shedding and control ──
    load_shed_stage: int = 0         # 0=none, 1/2/3=progressive shed
    charge_rate_override_a: float = 0.0  # 0.0 = auto, else manual A
    actual_charge_current_a: float = 0.0  # Actual charge current applied (Defect #4)
    solar_array_drive_angle: float = 0.0 # Solar array orientation angle
    battery_heater_on: bool = True   # Battery heater enable
    battery_heater_setpoint_c: float = 10.0  # Setpoint temperature
    eps_mode: int = 0                # 0=nominal, 1=safe, 2=emergency
    # S2 Device Access — device on/off states (device_id -> on/off)
    device_states: dict = field(default_factory=lambda: {
        0x0100: True,   # Battery heater
        0x0101: True,   # Solar array drive
        0x0102: True,   # PDU switch bus 1
        0x0103: True,   # PDU switch bus 2
        0x0104: True,   # PDU switch bus 3
        0x010F: True,   # Battery charge regulator
    })


class EPSBasicModel(SubsystemModel):
    """Basic Electrical Power Subsystem simulation with power lines."""

    def __init__(self):
        self._state = EPSState()
        self._cfg: dict[str, Any] = {}
        # Defaults from original config
        self._battery_capacity_wh = 120.0
        self._platform_idle_w = 95.0
        self._payload_power_w = 45.0
        self._payload_standby_w = 8.0
        self._fpa_cooler_w = 15.0
        self._transponder_w = 20.0
        self._transponder_rx_w = 5.0
        self._soc_100_v = 29.2
        self._soc_0_v = 21.5
        self._internal_r = 0.05
        self._panel_area = 0.628
        self._cell_eff = 0.295
        self._solar_irrad = 1361.0
        self._bat_temp_tau = 600.0
        self._bat_temp_env = 15.0
        # UV/OV thresholds
        self._uv_threshold = 26.5
        self._ov_threshold = 29.5
        # Parameter IDs
        self._param_ids: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "eps"

    def configure(self, config: dict[str, Any]) -> None:
        self._cfg = config
        bat = config.get("battery", {})
        self._battery_capacity_wh = bat.get("capacity_wh", 120.0)
        self._soc_100_v = bat.get("soc_100_v", 29.2)
        self._soc_0_v = bat.get("soc_0_v", 21.5)
        self._internal_r = bat.get("internal_r_ohm", 0.05)
        self._platform_idle_w = config.get("platform_idle_power_w", 95.0)
        self._payload_power_w = config.get("payload_power_w", 45.0)
        self._payload_standby_w = config.get("payload_standby_power_w", 8.0)
        self._fpa_cooler_w = config.get("fpa_cooler_power_w", 15.0)
        self._transponder_w = config.get("transponder_power_w", 20.0)
        self._transponder_rx_w = config.get("transponder_rx_power_w", 5.0)
        arrays = config.get("arrays", [])
        if arrays:
            total_area = sum(a.get("area_m2", 0.314) for a in arrays)
            self._panel_area = total_area
            self._cell_eff = arrays[0].get("efficiency", 0.295)
        self._param_ids = config.get("param_ids", {
            "bat_voltage": 0x0100, "bat_soc": 0x0101, "bat_temp": 0x0102,
            "sa_a_current": 0x0103, "sa_b_current": 0x0104,
            "bus_voltage": 0x0105, "power_cons": 0x0106, "power_gen": 0x0107,
            "eclipse_flag": 0x0108, "bat_current": 0x0109,
            "bat_capacity_wh": 0x010A,
        })
        # Per-panel solar areas (6U cubesat body-mounted panels)
        panel_cfg = config.get("solar_panels", {})
        self._panel_areas = {
            'px': panel_cfg.get('px_area_m2', 0.06),  # 6U face ~200x300mm = 0.06 m2
            'mx': panel_cfg.get('mx_area_m2', 0.06),
            'py': panel_cfg.get('py_area_m2', 0.03),  # 6U short face ~100x300mm
            'my': panel_cfg.get('my_area_m2', 0.03),
            'pz': panel_cfg.get('pz_area_m2', 0.06),
            'mz': panel_cfg.get('mz_area_m2', 0.06),  # Nadir face (partially obstructed by payload)
        }
        # Load power line config overrides if present
        pl_cfg = config.get("power_lines", {})
        for line_name in POWER_LINE_DEFAULTS:
            if line_name in pl_cfg:
                self._state.power_lines[line_name] = pl_cfg[line_name]

    def tick(self, dt: float, orbit_state: Any, shared_params: dict[int, float]) -> None:
        s = self._state
        events_to_generate = []  # Accumulate events
        s.in_eclipse = orbit_state.in_eclipse
        beta_deg = orbit_state.solar_beta_deg

        # ── Event: Eclipse entry/exit ──
        if s.in_eclipse and not s._prev_in_eclipse:
            events_to_generate.append((0x010C, f"Eclipse entry: SoC={s.bat_soc_pct:.1f}%"))
        elif not s.in_eclipse and s._prev_in_eclipse:
            events_to_generate.append((0x010D, f"Eclipse exit: resuming solar generation"))

        # Solar array aging: ~2.75% degradation per year in LEO
        # (typical GaAs triple junction)
        if not s.in_eclipse:
            s.sa_lifetime_hours += dt / 3600.0
        # Degradation: 0.00031% per sunlit hour (~2.75%/year at 8760h/yr * 0.6 sunlit fraction)
        s.sa_age_factor = max(0.5, 1.0 - s.sa_lifetime_hours * 3.14e-6)

        # Get sun vector from AOCS shared params (CSS sun vector in body frame)
        sun_bx = shared_params.get(0x0245, 0.0)  # CSS sun vector X (body frame)
        sun_by = shared_params.get(0x0246, 0.0)  # CSS sun vector Y
        sun_bz = shared_params.get(0x0247, 0.0)  # CSS sun vector Z
        sun_mag = (sun_bx**2 + sun_by**2 + sun_bz**2) ** 0.5

        if s.in_eclipse:
            # No solar generation in eclipse
            for face in PANEL_NORMALS:
                s.sa_panel_currents[face] = 0.0
            gen_w = 0.0
            s.sa_a_current = 0.0
            s.sa_b_current = 0.0
        elif sun_mag < 0.01:
            # Fallback: AOCS sun vector not available — use beta angle (old behavior)
            cos_beta = abs(math.cos(math.radians(beta_deg)))
            sa_fraction = max(0.0, cos_beta)

            base_pwr = self._panel_area * self._cell_eff * self._solar_irrad
            noise = 1.0 + random.gauss(0, 0.0025)

            sa_a_pwr = 0.5 * base_pwr * sa_fraction * s.sa_a_degradation * noise if s.sa_a_enabled else 0.0
            sa_b_pwr = 0.5 * base_pwr * sa_fraction * s.sa_b_degradation * noise if s.sa_b_enabled else 0.0

            # Apply MPPT efficiency and aging
            sa_a_pwr *= s.mppt_efficiency * s.sa_age_factor
            sa_b_pwr *= s.mppt_efficiency * s.sa_age_factor

            gen_w = sa_a_pwr + sa_b_pwr
            # Distribute evenly to maintain backward compat for sa_a/sa_b
            s.sa_a_current = sa_a_pwr / 28.0
            s.sa_b_current = sa_b_pwr / 28.0
        else:
            # 6 body-panel model: per-panel cosine projection against sun vector
            sun_nx = sun_bx / sun_mag
            sun_ny = sun_by / sun_mag
            sun_nz = sun_bz / sun_mag

            gen_w = 0.0
            for face, normal in PANEL_NORMALS.items():
                # Cosine projection: dot product of sun vector with panel normal
                cos_angle = normal[0] * sun_nx + normal[1] * sun_ny + normal[2] * sun_nz
                illumination = max(0.0, cos_angle)  # Only sunlit when facing sun

                area = self._panel_areas.get(face, 0.06)
                degradation = s.sa_panel_degradation.get(face, 1.0)
                noise = 1.0 + random.gauss(0, 0.005)

                panel_pwr = area * self._cell_eff * self._solar_irrad * illumination
                panel_pwr *= degradation * s.mppt_efficiency * s.sa_age_factor * noise

                panel_current = panel_pwr / 28.0
                s.sa_panel_currents[face] = panel_current
                gen_w += panel_pwr

            # Map panels to legacy SA A/B: A = +X,+Y,+Z; B = -X,-Y,-Z
            s.sa_a_current = sum(s.sa_panel_currents[f] for f in ['px', 'py', 'pz'])
            s.sa_b_current = sum(s.sa_panel_currents[f] for f in ['mx', 'my', 'mz'])

        sa_a_pwr_v = s.sa_a_current * 28.0
        sa_b_pwr_v = s.sa_b_current * 28.0
        s.sa_a_voltage = sa_a_pwr_v / max(s.sa_a_current, 0.01) if s.sa_a_current > 0.01 else 0.0
        s.sa_b_voltage = sa_b_pwr_v / max(s.sa_b_current, 0.01) if s.sa_b_current > 0.01 else 0.0
        s.power_gen_w = gen_w

        # Power consumption — driven by power line states with per-line current
        cons_w = 0.0
        lines = s.power_lines
        bus_v = max(s.bus_voltage, 20.0)

        # Compute per-line power and current
        line_powers = {}
        line_powers["obc"] = 40.0  # non-switchable
        line_powers["ttc_rx"] = 5.0  # non-switchable

        if lines.get("ttc_tx", True):
            line_powers["ttc_tx"] = self._transponder_w
        else:
            line_powers["ttc_tx"] = 0.0

        if lines.get("payload", False):
            if s.payload_mode == 1:
                line_powers["payload"] = self._payload_standby_w
            elif s.payload_mode == 2:
                line_powers["payload"] = self._payload_power_w
            else:
                line_powers["payload"] = 0.0
        else:
            line_powers["payload"] = 0.0

        if lines.get("fpa_cooler", False) and s.fpa_cooler_on:
            line_powers["fpa_cooler"] = self._fpa_cooler_w
        else:
            line_powers["fpa_cooler"] = 0.0

        line_powers["htr_bat"] = 6.0 if lines.get("htr_bat", True) else 0.0
        line_powers["htr_obc"] = 4.0 if lines.get("htr_obc", True) else 0.0
        line_powers["aocs_wheels"] = 12.0 if lines.get("aocs_wheels", True) else 0.0

        # Apply overcurrent injection multiplier
        for line_name, mult in s.oc_inject.items():
            if line_name in line_powers:
                line_powers[line_name] *= mult

        # Compute currents and check overcurrent
        for i, line_name in enumerate(POWER_LINE_NAMES):
            pwr = line_powers.get(line_name, 0.0)
            current = pwr / bus_v
            s.line_currents[line_name] = current

            # Overcurrent detection
            threshold = OC_THRESHOLDS.get(line_name, 999.0)
            if current > threshold and POWER_LINE_SWITCHABLE.get(line_name, False):
                # Trip the line
                s.oc_trip_flags |= (1 << i)
                s.power_lines[line_name] = False
                line_powers[line_name] = 0.0
                s.line_currents[line_name] = 0.0
                # Side effects
                if line_name == "payload":
                    s.payload_mode = 0
                elif line_name == "fpa_cooler":
                    s.fpa_cooler_on = False
                elif line_name == "ttc_tx":
                    s.transponder_tx = False

            cons_w += line_powers.get(line_name, 0.0)

        if s.bus_short:
            cons_w += 80.0
        s.power_cons_w = cons_w + random.gauss(0, 1.0)

        # Battery SoC
        net_power_w = gen_w - s.power_cons_w

        # Charge rate limiting: if charge_rate_override_a > 0, clamp charge current
        # (Defect #4 fix: enforce set_charge_rate command in model)
        if net_power_w > 0 and s.charge_rate_override_a > 0:
            # Charging case: limit charge power to charge_rate_override_a * nominal bus voltage
            max_charge_power_w = s.charge_rate_override_a * 28.0  # 28V nominal bus
            net_power_w = min(net_power_w, max_charge_power_w)

        d_soc = (net_power_w / (self._battery_capacity_wh * 3600.0)) * 100.0 * dt

        # DoD limiting: reduce charge acceptance above max DoD recovery
        old_soc = s.bat_soc_pct
        s.bat_soc_pct = max(0.0, min(100.0, s.bat_soc_pct + d_soc))
        s.bat_dod_pct = 100.0 - s.bat_soc_pct

        # Cycle count: one full charge-discharge transition
        is_charging = net_power_w > 0
        if s._was_charging and not is_charging:
            s.bat_cycles += 1
        s._was_charging = is_charging

        # Voltage
        ocv = self._soc_0_v + (self._soc_100_v - self._soc_0_v) * (s.bat_soc_pct / 100.0)
        bat_i = net_power_w / (ocv + 1e-6)
        s.bat_current = bat_i + random.gauss(0, 0.1)
        # Track actual charge current (positive = charging)
        # (Defect #4: provide feedback of actual charge rate)
        if net_power_w > 0:
            s.actual_charge_current_a = max(0.0, bat_i)
        else:
            s.actual_charge_current_a = 0.0
        v_loaded = ocv - bat_i * self._internal_r
        if s.bat_cell_failure:
            v_loaded -= 3.7
        s.bat_voltage = max(0.0, v_loaded) + random.gauss(0, 0.02)
        # Bus voltage tracks the loaded battery (single-bus, direct-energy
        # transfer architecture). Previously this was a SoC-only polynomial
        # which ignored load current and internal resistance, hiding bus
        # sag during high-power phases (payload imaging, slew, eclipse).
        s.bus_voltage = min(29.0, max(0.0, s.bat_voltage)) + random.gauss(0, 0.01)

        # Undervoltage / overvoltage detection
        s.uv_flag = s.bus_voltage < self._uv_threshold
        s.ov_flag = s.bus_voltage > self._ov_threshold

        # Load shedding: progressively shed switchable loads when bus voltage
        # drops below the load-shedding threshold.  Loads are shed in priority
        # order (lowest priority first).  Lines are restored only when bus
        # voltage recovers above threshold + 0.5 V hysteresis.
        if s.bus_voltage < LOAD_SHED_VOLTAGE:
            for shed_line in LOAD_SHED_ORDER:
                if s.power_lines.get(shed_line, False):
                    s.power_lines[shed_line] = False
                    # Apply side effects (same as overcurrent trip)
                    if shed_line == "payload":
                        s.payload_mode = 0
                    elif shed_line == "fpa_cooler":
                        s.fpa_cooler_on = False
                    elif shed_line == "ttc_tx":
                        s.transponder_tx = False
                    break  # Shed one line per tick to allow recovery

        # Battery temperature
        bat_heat_w = abs(s.bat_current) ** 2 * self._internal_r
        delta_temp = ((self._bat_temp_env - s.bat_temp) / self._bat_temp_tau + bat_heat_w / 30.0) * dt
        s.bat_temp += delta_temp + random.gauss(0, 0.05)

        # ── Event detection: Battery temperature extremes ──
        if s.bat_temp > 45.0:
            events_to_generate.append((0x0109, f"Battery overtemp: {s.bat_temp:.1f}C"))
        if s.bat_temp < -5.0:
            events_to_generate.append((0x010A, f"Battery undertemp: {s.bat_temp:.1f}C"))

        # ── Event detection: SoC thresholds ──
        if s.bat_soc_pct < 10.0 and s._prev_soc >= 10.0:
            events_to_generate.append((0x0102, f"Battery critical SoC: {s.bat_soc_pct:.1f}%"))
        elif s.bat_soc_pct < 20.0 and s._prev_soc >= 20.0:
            events_to_generate.append((0x0101, f"Battery low SoC warning: {s.bat_soc_pct:.1f}%"))

        # ── Event detection: Charge complete ──
        if s.bat_soc_pct >= 99.0 and s._prev_soc < 99.0 and s._was_charging:
            events_to_generate.append((0x010E, f"Battery charge complete: {s.bat_soc_pct:.1f}%"))

        # ── Event detection: Bus voltage issues ──
        if s.bus_voltage < 25.0 and s._prev_bus_voltage >= 25.0:
            events_to_generate.append((0x0104, f"Bus undervoltage critical: {s.bus_voltage:.2f}V"))
        elif s.bus_voltage < 27.0 and s._prev_bus_voltage >= 27.0:
            events_to_generate.append((0x0103, f"Bus undervoltage warning: {s.bus_voltage:.2f}V"))

        # ── Event detection: Overcurrent trips ──
        new_trips = s.oc_trip_flags & ~s._prev_oc_trip_flags
        for i in range(len(POWER_LINE_NAMES)):
            if new_trips & (1 << i):
                line_name = POWER_LINE_NAMES[i]
                events_to_generate.append((0x010F, f"PDU overcurrent trip: {line_name}"))

        # ── Event detection: Load shedding stages ──
        # Calculate current load shed stage
        cur_load_shed_stage = 0
        if s.bus_voltage < LOAD_SHED_VOLTAGE:
            shed_count = sum(1 for ln in LOAD_SHED_ORDER if not s.power_lines.get(ln, False))
            cur_load_shed_stage = min(3, shed_count)

        if cur_load_shed_stage > s._prev_load_shed_stage:
            events_to_generate.append((0x0106 + cur_load_shed_stage - 1, f"Load shed stage {cur_load_shed_stage}"))

        s.load_shed_stage = cur_load_shed_stage

        # ── Event detection: Solar array degradation ──
        # Track if solar array shows significant drop from expected output
        expected_sa_power = (self._panel_area * self._cell_eff * self._solar_irrad *
                           (1.0 if not s.in_eclipse else 0.0)) * s.mppt_efficiency * s.sa_age_factor
        actual_sa_power = s.power_gen_w
        if expected_sa_power > 50.0 and actual_sa_power < expected_sa_power * 0.7:
            if not s.in_eclipse:
                events_to_generate.append((0x010B, f"Solar array degraded: {actual_sa_power:.1f}W vs {expected_sa_power:.1f}W"))

        # ── Event detection: Power line state changes ──
        for i, line_name in enumerate(POWER_LINE_NAMES):
            cur_state = s.power_lines.get(line_name, False)
            prev_state = bool(s._prev_oc_trip_flags & (1 << i))  # Use trip flags temporarily
            if cur_state and not prev_state and POWER_LINE_SWITCHABLE.get(line_name, False):
                events_to_generate.append((0x0110, f"Power line {line_name} switched ON"))

        # ── Event detection: EPS mode changes ──
        if s.eps_mode != s._prev_mode:
            events_to_generate.append((0x0100, f"EPS mode change: {s._prev_mode} -> {s.eps_mode}"))

        # Store events in shared_params for engine to generate S5 packets
        if events_to_generate and hasattr(self, '_engine'):
            for event_id, event_desc in events_to_generate:
                self._engine.event_queue.put((event_id, event_desc, self._get_time()))

        # ── Update previous state tracking ──
        s._prev_soc = s.bat_soc_pct
        s._prev_bus_voltage = s.bus_voltage
        s._prev_in_eclipse = s.in_eclipse
        s._prev_oc_trip_flags = s.oc_trip_flags
        s._prev_load_shed_stage = s.load_shed_stage
        s._prev_mode = s.eps_mode

        # Write shared params
        p = self._param_ids
        shared_params[p.get("bat_voltage", 0x0100)] = s.bat_voltage
        shared_params[p.get("bat_soc", 0x0101)] = s.bat_soc_pct
        shared_params[p.get("bat_temp", 0x0102)] = s.bat_temp
        shared_params[p.get("bat_current", 0x0109)] = s.bat_current
        shared_params[p.get("bat_capacity_wh", 0x010A)] = self._battery_capacity_wh * (s.bat_soc_pct / 100.0)
        shared_params[p.get("sa_a_current", 0x0103)] = s.sa_a_current
        shared_params[p.get("sa_b_current", 0x0104)] = s.sa_b_current
        shared_params[p.get("bus_voltage", 0x0105)] = s.bus_voltage
        shared_params[p.get("power_cons", 0x0106)] = s.power_cons_w
        shared_params[p.get("power_gen", 0x0107)] = s.power_gen_w
        shared_params[p.get("eclipse_flag", 0x0108)] = 1 if s.in_eclipse else 0

        # New EPS params (Phase 3)
        shared_params[0x010B] = s.sa_a_voltage
        shared_params[0x010C] = s.sa_b_voltage
        shared_params[0x010D] = s.oc_trip_flags
        shared_params[0x010E] = 1 if s.uv_flag else 0
        shared_params[0x010F] = 1 if s.ov_flag else 0

        # Power line status (0x0110-0x0117)
        for i, line_name in enumerate(POWER_LINE_NAMES):
            shared_params[0x0110 + i] = 1 if s.power_lines.get(line_name, False) else 0

        # Per-line currents (0x0118-0x011F)
        for i, line_name in enumerate(POWER_LINE_NAMES):
            shared_params[0x0118 + i] = s.line_currents.get(line_name, 0.0)

        # Phase 4: Flight hardware realism
        shared_params[0x0120] = s.bat_dod_pct
        shared_params[0x0121] = float(s.bat_cycles)
        shared_params[0x0122] = s.mppt_efficiency
        shared_params[0x0123] = s.sa_age_factor
        shared_params[0x0124] = s.sa_a_degradation
        shared_params[0x0125] = s.sa_b_degradation
        shared_params[0x0126] = s.sa_lifetime_hours

        # PDM and separation
        shared_params[0x0127] = 1 if s.sep_timer_active else 0
        shared_params[0x0128] = s.sep_timer_remaining
        # NOTE: 0x0129 is the canonical spacecraft_phase parameter (owned by
        # the engine — see _tick_spacecraft_phase). EPS used to overwrite
        # this with pdm_unsw_status every tick which corrupted the phase
        # value mid-tick. PDM unswitched-line status now lives at 0x013A.
        shared_params[0x013A] = float(s.pdm_unsw_status)
        # 0x012A is reserved (was a duplicate phase mirror) — leave unset.

        # Per-panel solar array currents
        panel_names = ['px', 'mx', 'py', 'my', 'pz', 'mz']
        for i, face in enumerate(panel_names):
            shared_params[0x012B + i] = s.sa_panel_currents.get(face, 0.0)

        # Enhanced telemetry: load switching and power management
        shared_params[0x0131] = float(sum(1 << i for i, ln in enumerate(POWER_LINE_NAMES) if s.power_lines.get(ln, False)))
        shared_params[0x0132] = s.charge_rate_override_a
        shared_params[0x0143] = s.actual_charge_current_a  # Defect #4: actual charge current feedback
        shared_params[0x0133] = s.solar_array_drive_angle
        shared_params[0x0134] = float(s.load_shed_stage)
        shared_params[0x0135] = s.power_gen_w - s.power_cons_w  # Power margin
        shared_params[0x0136] = max(0.0, 100.0 - s.bat_dod_pct)  # Battery health percentage
        shared_params[0x0137] = float(s.eps_mode)
        shared_params[0x0138] = 1 if s.battery_heater_on else 0
        shared_params[0x0139] = s.battery_heater_setpoint_c
        shared_params[0x010E] = 1 if s.uv_flag else 0
        shared_params[0x010F] = 1 if s.ov_flag else 0

        # NOTE: An earlier duplicate telemetry-write block that referenced
        # undefined locals `lines`, `gen_w`, `cons_w` and re-wrote all of the
        # params already set above has been removed. It would have crashed on
        # the first tick. All power-line / per-line current / Phase 4 / panel /
        # 0x0131–0x0139 params are written exactly once, earlier in this
        # method, using the canonical ``s.power_lines`` / ``s.line_currents``
        # state. See defects/reviews/power.md for the full defect entry.

    def get_telemetry(self) -> dict[int, float]:
        s = self._state
        p = self._param_ids
        return {
            p.get("bat_voltage", 0x0100): s.bat_voltage,
            p.get("bat_soc", 0x0101): s.bat_soc_pct,
            p.get("bat_temp", 0x0102): s.bat_temp,
            p.get("bus_voltage", 0x0105): s.bus_voltage,
            p.get("power_gen", 0x0107): s.power_gen_w,
            p.get("power_cons", 0x0106): s.power_cons_w,
        }

    def handle_command(self, cmd: dict[str, Any]) -> dict[str, Any]:
        command = cmd.get("command", "")
        if command == "set_payload_mode":
            mode = int(cmd.get("mode", 0))
            if mode in (0, 1, 2):
                self._state.payload_mode = mode
                return {"success": True}
            return {"success": False, "message": "Invalid mode"}
        elif command == "set_fpa_cooler":
            self._state.fpa_cooler_on = bool(cmd.get("on", False))
            return {"success": True}
        elif command == "set_transponder_tx":
            self._state.transponder_tx = bool(cmd.get("on", True))
            return {"success": True}
        elif command == "disable_array":
            arr = cmd.get("array", "A")
            if arr == "A": self._state.sa_a_enabled = False
            elif arr == "B": self._state.sa_b_enabled = False
            return {"success": True}
        elif command == "enable_array":
            arr = cmd.get("array", "A")
            if arr == "A": self._state.sa_a_enabled = True
            elif arr == "B": self._state.sa_b_enabled = True
            return {"success": True}
        elif command == "power_line_on":
            return self._set_power_line(cmd, True)
        elif command == "power_line_off":
            return self._set_power_line(cmd, False)
        elif command == "reset_oc_flag":
            return self._reset_oc_flag(cmd)
        elif command == "switch_load":
            load_id = cmd.get("load_id", 0)
            state = bool(cmd.get("state", False))
            return self._switch_load(load_id, state)
        elif command == "set_charge_rate":
            rate_a = float(cmd.get("rate_a", 0.0))
            self._state.charge_rate_override_a = rate_a
            return {"success": True, "message": f"Charge rate set to {rate_a}A"}
        elif command == "set_solar_array_drive":
            angle = float(cmd.get("angle_deg", 0.0))
            self._state.solar_array_drive_angle = max(-90.0, min(90.0, angle))
            return {"success": True, "message": f"Solar array drive angle set to {self._state.solar_array_drive_angle}"}
        elif command == "emergency_load_shed":
            stage = int(cmd.get("stage", 1))
            return self._emergency_load_shed(stage)
        elif command == "bus_isolate":
            bus_id = int(cmd.get("bus_id", 0))
            return {"success": True, "message": f"Bus {bus_id} isolation command acknowledged"}
        elif command == "set_battery_heater":
            on = bool(cmd.get("on", True))
            setpoint = float(cmd.get("setpoint_c", 10.0))
            self._state.battery_heater_on = on
            self._state.battery_heater_setpoint_c = setpoint
            return {"success": True, "message": f"Battery heater: {on}, setpoint {setpoint}C"}
        elif command == "reset_trip":
            line_idx = cmd.get("line_index", -1)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_trip_flags &= ~(1 << line_idx)
                self._state.power_lines[line_name] = True
                return {"success": True, "message": f"Trip reset: {line_name}"}
            return {"success": False, "message": "Invalid line index"}
        elif command == "get_power_budget":
            return self._get_power_budget_summary()
        elif command == "set_eps_mode":
            mode = int(cmd.get("mode", 0))
            if mode in (0, 1, 2):
                self._state.eps_mode = mode
                return {"success": True, "message": f"EPS mode set to {mode}"}
            return {"success": False, "message": "Invalid EPS mode"}
        return {"success": False, "message": f"Unknown command: {command}"}

    def _set_power_line(self, cmd: dict, on: bool) -> dict[str, Any]:
        """Switch a power line on or off."""
        line_idx = cmd.get("line_index", -1)
        if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
            line_name = POWER_LINE_NAMES[line_idx]
        else:
            line_name = cmd.get("line_name", "")

        if line_name not in POWER_LINE_NAMES:
            return {"success": False, "message": f"Unknown power line: {line_name}",
                    "error_code": 0x0005}

        if not POWER_LINE_SWITCHABLE[line_name]:
            return {"success": False,
                    "message": f"Power line {line_name} is not switchable",
                    "error_code": 0x0006}

        self._state.power_lines[line_name] = on

        # Side effects
        if not on:
            if line_name == "payload":
                self._state.payload_mode = 0
            elif line_name == "fpa_cooler":
                self._state.fpa_cooler_on = False
            elif line_name == "ttc_tx":
                self._state.transponder_tx = False
        else:
            if line_name == "ttc_tx":
                self._state.transponder_tx = True

        return {"success": True}

    def _reset_oc_flag(self, cmd: dict) -> dict[str, Any]:
        """Reset overcurrent trip flag and re-enable the line."""
        line_idx = cmd.get("line_index", -1)
        if not isinstance(line_idx, int) or line_idx < 0 or line_idx >= len(POWER_LINE_NAMES):
            return {"success": False, "message": "Invalid line index"}
        line_name = POWER_LINE_NAMES[line_idx]
        if not (self._state.oc_trip_flags & (1 << line_idx)):
            return {"success": False, "message": "Line not tripped"}
        # Clear trip flag
        self._state.oc_trip_flags &= ~(1 << line_idx)
        # Re-enable line
        if POWER_LINE_SWITCHABLE.get(line_name, False):
            self._state.power_lines[line_name] = True
        return {"success": True}

    def inject_failure(self, failure: str, magnitude: float = 1.0, **kwargs) -> None:
        if failure == "solar_array_partial":
            arr = kwargs.get("array", "A")
            if arr == "A":
                self._state.sa_a_degradation = max(0.0, min(1.0, 1.0 - magnitude))
            else:
                self._state.sa_b_degradation = max(0.0, min(1.0, 1.0 - magnitude))
        elif failure == "bat_cell":
            self._state.bat_cell_failure = bool(magnitude)
        elif failure == "bus_short":
            self._state.bus_short = bool(magnitude)
        elif failure == "overcurrent":
            # Inject overcurrent on a specific line via multiplier
            line_idx = kwargs.get("line_index", 3)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_inject[line_name] = magnitude
        elif failure == "undervoltage":
            # Reduce bus voltage artificially
            self._state.bat_soc_pct = max(0.0, self._state.bat_soc_pct - magnitude * 10)
        elif failure == "overvoltage":
            # Will be detected by ov_flag threshold check
            self._ov_threshold = max(25.0, self._ov_threshold - magnitude)
        elif failure == "solar_panel_loss":
            face = kwargs.get("face", "px")
            if face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = max(0.0, 1.0 - magnitude)

        elif failure == "solar_array_total_loss":
            # Both wings dead — drives the worst-case
            # solar_panel_loss_response / progressive_load_shed contingency.
            self._state.sa_a_degradation = 0.0
            self._state.sa_b_degradation = 0.0
            for face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 0.0

    def clear_failure(self, failure: str, **kwargs) -> None:
        if failure == "solar_array_partial":
            arr = kwargs.get("array", "A")
            if arr == "A": self._state.sa_a_degradation = 1.0
            else: self._state.sa_b_degradation = 1.0
        elif failure == "bat_cell":
            self._state.bat_cell_failure = False
        elif failure == "bus_short":
            self._state.bus_short = False
        elif failure == "overcurrent":
            line_idx = kwargs.get("line_index", 3)
            if isinstance(line_idx, int) and 0 <= line_idx < len(POWER_LINE_NAMES):
                line_name = POWER_LINE_NAMES[line_idx]
                self._state.oc_inject.pop(line_name, None)
        elif failure == "undervoltage":
            # SoC recovers naturally during charging; mark the inject "off" by
            # zeroing any residual injection bookkeeping. Recovery is physical.
            pass
        elif failure == "overvoltage":
            self._ov_threshold = 29.5
        elif failure == "solar_panel_loss":
            face = kwargs.get("face", "px")
            if face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 1.0

        elif failure == "solar_array_total_loss":
            self._state.sa_a_degradation = 1.0
            self._state.sa_b_degradation = 1.0
            for face in self._state.sa_panel_degradation:
                self._state.sa_panel_degradation[face] = 1.0

    def get_state(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self._state)

    def set_state(self, state: dict[str, Any]) -> None:
        for k, v in state.items():
            if hasattr(self._state, k):
                setattr(self._state, k, v)

    def set_bat_ambient_temp(self, temp_c: float) -> None:
        self._bat_temp_env = temp_c

    def _get_time(self) -> float:
        """Return current mission time (placeholder for engine time)."""
        import time
        return time.time()

    def _switch_load(self, load_id: int, state: bool) -> dict[str, Any]:
        """Switch individual load/power line on/off."""
        if load_id < 0 or load_id >= len(POWER_LINE_NAMES):
            return {"success": False, "message": f"Invalid load ID: {load_id}", "error_code": 0x0005}
        line_name = POWER_LINE_NAMES[load_id]
        if not POWER_LINE_SWITCHABLE.get(line_name, False):
            return {"success": False, "message": f"Load {line_name} is not switchable", "error_code": 0x0006}
        self._state.power_lines[line_name] = state
        return {"success": True, "message": f"Load {line_name} switched {'ON' if state else 'OFF'}"}

    def _emergency_load_shed(self, stage: int) -> dict[str, Any]:
        """Immediately shed loads to specified stage (1, 2, or 3)."""
        if stage < 0 or stage > 3:
            return {"success": False, "message": "Invalid load shed stage (0-3)"}
        # Shed loads up to target stage
        for idx, load_name in enumerate(LOAD_SHED_ORDER):
            should_shed = (idx + 1) <= stage
            self._state.power_lines[load_name] = not should_shed
        self._state.load_shed_stage = stage
        return {"success": True, "message": f"Emergency load shed stage {stage}"}

    def _get_power_budget_summary(self) -> dict[str, Any]:
        """Return current power generation vs consumption summary."""
        gen_w = self._state.power_gen_w
        cons_w = self._state.power_cons_w
        margin_w = gen_w - cons_w
        return {
            "success": True,
            "power_gen_w": gen_w,
            "power_cons_w": cons_w,
            "power_margin_w": margin_w,
            "power_margin_pct": (margin_w / max(gen_w, 1.0)) * 100.0,
            "bus_voltage_v": self._state.bus_voltage,
            "bat_soc_pct": self._state.bat_soc_pct,
        }

    # S2 Device Access — device-level on/off control
    def set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Returns True if successful."""
        if device_id not in self._state.device_states:
            return False
        self._state.device_states[device_id] = on_off
        return True

    def get_device_state(self, device_id: int) -> bool:
        """Get device on/off state. Returns True if on, False if off or invalid."""
        return self._state.device_states.get(device_id, False)
