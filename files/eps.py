"""
EO Mission Simulator — Electrical Power Subsystem (EPS)
Battery SoC / voltage model, solar array power coupled to eclipse state,
power budget management, heater control, and failure models.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Dict

from config import (
    P_BAT_VOLTAGE, P_BAT_SOC, P_BAT_TEMP, P_BAT_CURRENT, P_BAT_CAPACITY_WH,
    P_SA_A_CURRENT, P_SA_B_CURRENT,
    P_BUS_VOLTAGE, P_POWER_CONS, P_POWER_GEN, P_ECLIPSE_FLAG,
    BATTERY_CAPACITY_WH, BATTERY_NOMINAL_VOLTAGE,
    SA_PEAK_POWER_W, PLATFORM_IDLE_POWER_W, PAYLOAD_POWER_W,
    PAYLOAD_STANDBY_POWER_W, FPA_COOLER_POWER_W,
    TRANSPONDER_POWER_W, TRANSPONDER_RX_POWER_W,
    BAT_SOC_100_V, BAT_SOC_0_V, BAT_INTERNAL_R,
    OBC_MODE_NOMINAL,
)


@dataclass
class EPSState:
    """Mutable EPS state block — updated each tick."""
    # Battery
    bat_soc_pct:     float = 75.0    # 0–100 %
    bat_voltage:     float = 26.4    # V
    bat_current:     float = 0.0     # A  (positive = charging)
    bat_temp:        float = 15.0    # °C

    # Solar arrays (two independent wings)
    sa_a_current:    float = 4.5     # A
    sa_b_current:    float = 4.5     # A
    sa_a_enabled:    bool  = True
    sa_b_enabled:    bool  = True

    # Bus
    bus_voltage:     float = 28.2    # V
    power_gen_w:     float = 252.0   # W
    power_cons_w:    float = 120.0   # W

    # Eclipse
    in_eclipse:      bool  = False

    # Loads (commanded states)
    payload_mode:    int   = 0       # 0=off,1=standby,2=imaging
    fpa_cooler_on:   bool  = False
    transponder_tx:  bool  = True

    # Failure injection state
    sa_a_degradation: float = 1.0   # 1.0 = healthy, 0.0 = fully failed
    sa_b_degradation: float = 1.0
    bat_cell_failure:  bool  = False
    bus_short:         bool  = False


class EPSSubsystem:
    """
    Electrical Power Subsystem simulation.

    Battery SoC evolves using a power-balance approach:
        dSoC/dt = (P_gen - P_cons) / (3600 * capacity_Wh)

    SoC → OCV uses a simple linear mapping, then internal resistance
    provides the loaded voltage.
    """

    # Solar array power vs beta angle (cos approximation)
    _PANEL_AREA_M2     = 0.628        # m² total — gives 252 W at normal incidence
    _CELL_EFFICIENCY   = 0.295        # 29.5% GaAs
    _SOLAR_IRRAD_W_M2  = 1361.0       # W/m² at 1 AU (approx at LEO)
    _NOISE_SD          = 0.05         # fractional noise

    def __init__(self, dt_s: float = 1.0):
        self.dt   = dt_s
        self.state = EPSState()
        # Thermal coupling constant for battery temperature
        self._bat_temp_tau = 600.0    # s  (first-order thermal model)
        self._bat_temp_env = 15.0     # °C  ambient in battery bay

    # ------------------------------------------------------------------
    # Tick — called by SimulationEngine every dt seconds
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s = self.state

        # --- 1. Eclipse flag ---
        s.in_eclipse = orbit_state.in_eclipse

        # --- 2. Solar array generation ---
        beta_deg     = orbit_state.solar_beta_deg
        if s.in_eclipse:
            sa_fraction = 0.0
        else:
            # Effective illumination angle based on beta
            cos_beta = abs(math.cos(math.radians(beta_deg)))
            sa_fraction = max(0.0, cos_beta)

        base_pwr = (self._PANEL_AREA_M2 * self._CELL_EFFICIENCY * self._SOLAR_IRRAD_W_M2)
        noise = 1.0 + random.gauss(0, self._NOISE_SD * 0.05)

        if s.sa_a_enabled:
            sa_a_pwr = 0.5 * base_pwr * sa_fraction * s.sa_a_degradation * noise
        else:
            sa_a_pwr = 0.0

        if s.sa_b_enabled:
            sa_b_pwr = 0.5 * base_pwr * sa_fraction * s.sa_b_degradation * noise
        else:
            sa_b_pwr = 0.0

        gen_w = sa_a_pwr + sa_b_pwr

        # Current from each array (28V bus)
        s.sa_a_current = sa_a_pwr / 28.0
        s.sa_b_current = sa_b_pwr / 28.0
        s.power_gen_w  = gen_w

        # --- 3. Power consumption ---
        cons_w = PLATFORM_IDLE_POWER_W
        if s.payload_mode == 1:     # standby
            cons_w += PAYLOAD_STANDBY_POWER_W
        elif s.payload_mode == 2:   # imaging
            cons_w += PAYLOAD_POWER_W
        if s.fpa_cooler_on:
            cons_w += FPA_COOLER_POWER_W
        if s.transponder_tx:
            cons_w += TRANSPONDER_POWER_W
        else:
            cons_w += TRANSPONDER_RX_POWER_W

        # Bus-short failure: significantly increases consumption
        if s.bus_short:
            cons_w += 80.0

        s.power_cons_w = cons_w + random.gauss(0, 1.0)

        # --- 4. Battery SoC / voltage ---
        net_power_w     = gen_w - s.power_cons_w
        # dSoC/dt in %/s
        d_soc = (net_power_w / (BATTERY_CAPACITY_WH * 3600.0)) * 100.0 * self.dt
        s.bat_soc_pct   = max(0.0, min(100.0, s.bat_soc_pct + d_soc))

        # Open circuit voltage (linear interpolation SoC 0–100)
        ocv     = BAT_SOC_0_V + (BAT_SOC_100_V - BAT_SOC_0_V) * (s.bat_soc_pct / 100.0)
        # Battery current (positive = charging)
        bat_i   = net_power_w / (ocv + 1e-6)
        s.bat_current = bat_i + random.gauss(0, 0.1)
        # Loaded voltage
        v_loaded = ocv - bat_i * BAT_INTERNAL_R
        if s.bat_cell_failure:
            # One cell drops out: ~3.7V less OCV, higher internal resistance
            v_loaded -= 3.7
        s.bat_voltage = max(0.0, v_loaded) + random.gauss(0, 0.02)

        # Main bus voltage: regulator output (clamped by battery)
        s.bus_voltage = min(29.0, max(20.0, 28.2 + (s.bat_soc_pct - 75) * 0.02))
        s.bus_voltage += random.gauss(0, 0.01)

        # --- 5. Battery temperature (first-order lumped mass) ---
        # Self-heating from I²R; cooling toward ambient
        bat_heat_w  = abs(s.bat_current) ** 2 * BAT_INTERNAL_R
        delta_temp  = ((self._bat_temp_env - s.bat_temp) / self._bat_temp_tau
                       + bat_heat_w / 30.0) * self.dt  # 30 J/°C thermal mass
        s.bat_temp += delta_temp + random.gauss(0, 0.05)

        # --- 6. Write to shared parameter store ---
        shared_params[P_BAT_VOLTAGE]    = s.bat_voltage
        shared_params[P_BAT_SOC]        = s.bat_soc_pct
        shared_params[P_BAT_TEMP]       = s.bat_temp
        shared_params[P_BAT_CURRENT]    = s.bat_current
        shared_params[P_BAT_CAPACITY_WH]= BATTERY_CAPACITY_WH * (s.bat_soc_pct / 100.0)
        shared_params[P_SA_A_CURRENT]   = s.sa_a_current
        shared_params[P_SA_B_CURRENT]   = s.sa_b_current
        shared_params[P_BUS_VOLTAGE]    = s.bus_voltage
        shared_params[P_POWER_CONS]     = s.power_cons_w
        shared_params[P_POWER_GEN]      = s.power_gen_w
        shared_params[P_ECLIPSE_FLAG]   = 1 if s.in_eclipse else 0

    # ------------------------------------------------------------------
    # Command handlers (called by service_handlers.py)
    # ------------------------------------------------------------------

    def cmd_set_payload_mode(self, mode: int) -> bool:
        """0=off, 1=standby, 2=imaging"""
        if mode not in (0, 1, 2):
            return False
        self.state.payload_mode = mode
        return True

    def cmd_set_fpa_cooler(self, on: bool) -> bool:
        self.state.fpa_cooler_on = on
        return True

    def cmd_set_transponder_tx(self, on: bool) -> bool:
        self.state.transponder_tx = on
        return True

    def cmd_disable_array(self, array: str) -> bool:
        """array: 'A' or 'B'"""
        if array == 'A':
            self.state.sa_a_enabled = False
        elif array == 'B':
            self.state.sa_b_enabled = False
        else:
            return False
        return True

    def cmd_enable_array(self, array: str) -> bool:
        if array == 'A':
            self.state.sa_a_enabled = True
        elif array == 'B':
            self.state.sa_b_enabled = True
        else:
            return False
        return True

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_sa_degradation(self, array: str, magnitude: float) -> None:
        """Degrade solar array output. magnitude 0.0 = full failure, 1.0 = nominal."""
        if array == 'A':
            self.state.sa_a_degradation = max(0.0, min(1.0, 1.0 - magnitude))
        else:
            self.state.sa_b_degradation = max(0.0, min(1.0, 1.0 - magnitude))

    def inject_bat_cell_failure(self, failed: bool) -> None:
        self.state.bat_cell_failure = failed

    def inject_bus_short(self, active: bool) -> None:
        self.state.bus_short = active

    def set_bat_ambient_temp(self, temp_c: float) -> None:
        """Allow TCS to update battery bay ambient temperature."""
        self._bat_temp_env = temp_c
