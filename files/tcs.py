"""
EO Mission Simulator — Thermal Control Subsystem (TCS)
Lumped-mass thermal model per zone, heater command handling,
eclipse-driven thermal cycling, and failure injection.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Dict

from config import (
    P_TEMP_PANEL_PX, P_TEMP_PANEL_MX, P_TEMP_PANEL_PY,
    P_TEMP_PANEL_MY, P_TEMP_PANEL_PZ, P_TEMP_PANEL_MZ,
    P_TEMP_OBC, P_TEMP_BATTERY, P_TEMP_FPA, P_TEMP_THRUSTER,
    P_HTR_BATTERY, P_HTR_OBC, P_COOLER_FPA, P_HTR_THRUSTER,
)

# Heater power (W) for each circuit
_HEATER_POWER = {
    'battery':   6.0,
    'obc':       4.0,
    'thruster':  8.0,
}

# Thermostat set-points (°C): [off_high, on_low]
_THERMOSTAT = {
    'battery':   (5.0,  1.0),
    'obc':       (10.0, 5.0),
    'thruster':  (8.0,  2.0),
}


@dataclass
class TCSState:
    # Panel temperatures (°C)
    temp_panel_px: float = 15.0
    temp_panel_mx: float = 12.0
    temp_panel_py: float = 20.0
    temp_panel_my: float = 18.0
    temp_panel_pz: float = 10.0
    temp_panel_mz: float = 8.0
    # Internal equipment temperatures
    temp_obc:     float = 25.0
    temp_battery: float = 15.0
    temp_fpa:     float = -5.0
    temp_thruster: float = 5.0
    # Heater states (True = ON)
    htr_battery:  bool = False
    htr_obc:      bool = False
    htr_thruster: bool = False
    # FPA cooler
    cooler_fpa:   bool = False
    # Heater failures (circuit open → heater cannot activate)
    htr_battery_failed: bool = False
    htr_obc_failed:     bool = False
    htr_thruster_failed: bool = False
    # FPA cooler failure
    cooler_failed:       bool = False
    # OBC thermal anomaly injection
    obc_internal_heat_w: float = 0.0


class TCSSubsystem:
    """
    Simple first-order (lumped capacitance) thermal model.

    Each zone has:
        C   — thermal capacitance (J/°C)
        tau — cooling time constant toward environment (s)
        env — environment reference temperature (varies with eclipse)
    """

    # Thermal capacitance J/°C for each zone
    _CAP = {
        'panel_px': 5000, 'panel_mx': 5000,
        'panel_py': 4000, 'panel_my': 4000,
        'panel_pz': 3000, 'panel_mz': 3000,
        'obc':      800,  'battery': 2000,
        'fpa':      100,  'thruster': 500,
    }

    # Time constants (s) toward environment
    _TAU = {
        'panel_px': 600, 'panel_mx': 600,
        'panel_py': 500, 'panel_my': 500,
        'panel_pz': 800, 'panel_mz': 800,
        'obc':      900, 'battery': 1200,
        'fpa':      120, 'thruster': 400,
    }

    def __init__(self, dt_s: float = 1.0):
        self.dt    = dt_s
        self.state = TCSState()
        self._in_eclipse = False

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s = self.state
        self._in_eclipse = orbit_state.in_eclipse
        dt = self.dt

        # Environment temperature (simplified):
        # Sunlight: external panels heated; Eclipse: cold soak
        if self._in_eclipse:
            env_external = -30.0
            env_internal = 10.0
        else:
            # Sun-facing panel is hotter; nadir/zenith moderate
            beta = orbit_state.solar_beta_deg
            sun_factor = abs(math.cos(math.radians(beta)))
            env_external = -10.0 + 50.0 * sun_factor
            env_internal = 12.0

        # --- Panel temperatures ---
        panels = [
            ('panel_px', 'temp_panel_px', env_external + 5),
            ('panel_mx', 'temp_panel_mx', env_external),
            ('panel_py', 'temp_panel_py', env_external + 8),
            ('panel_my', 'temp_panel_my', env_external),
            ('panel_pz', 'temp_panel_pz', env_external - 5),
            ('panel_mz', 'temp_panel_mz', env_external - 5),
        ]
        for key, attr, env in panels:
            T = getattr(s, attr)
            tau = self._TAU[key]
            T += (env - T) / tau * dt + random.gauss(0, 0.05)
            setattr(s, attr, T)

        # --- Battery temperature ---
        htr_bat_ok = not s.htr_battery_failed
        # Thermostat logic
        lo, hi = _THERMOSTAT['battery'][1], _THERMOSTAT['battery'][0]
        if s.temp_battery <= lo and htr_bat_ok:
            s.htr_battery = True
        elif s.temp_battery >= hi:
            s.htr_battery = False
        htr_bat_pwr = _HEATER_POWER['battery'] if s.htr_battery else 0.0
        T = s.temp_battery
        T += ((env_internal - T) / self._TAU['battery']
              + htr_bat_pwr / self._CAP['battery']) * dt
        T += random.gauss(0, 0.02)
        s.temp_battery = T

        # --- OBC temperature ---
        htr_obc_ok = not s.htr_obc_failed
        lo_o, hi_o = _THERMOSTAT['obc'][1], _THERMOSTAT['obc'][0]
        if s.temp_obc <= lo_o and htr_obc_ok:
            s.htr_obc = True
        elif s.temp_obc >= hi_o:
            s.htr_obc = False
        htr_obc_pwr = _HEATER_POWER['obc'] if s.htr_obc else 0.0
        T = s.temp_obc
        T += ((env_internal - T) / self._TAU['obc']
              + (htr_obc_pwr + s.obc_internal_heat_w) / self._CAP['obc']) * dt
        T += random.gauss(0, 0.03)
        s.temp_obc = T

        # --- FPA temperature ---
        cooler_effect = -25.0 if (s.cooler_fpa and not s.cooler_failed) else 0.0
        fpa_env = env_internal + cooler_effect
        T = s.temp_fpa
        T += (fpa_env - T) / self._TAU['fpa'] * dt + random.gauss(0, 0.02)
        s.temp_fpa = T

        # --- Thruster temperature ---
        htr_thr_ok = not s.htr_thruster_failed
        lo_t, hi_t = _THERMOSTAT['thruster'][1], _THERMOSTAT['thruster'][0]
        if s.temp_thruster <= lo_t and htr_thr_ok:
            s.htr_thruster = True
        elif s.temp_thruster >= hi_t:
            s.htr_thruster = False
        htr_thr_pwr = _HEATER_POWER['thruster'] if s.htr_thruster else 0.0
        T = s.temp_thruster
        T += ((env_internal - 5 - T) / self._TAU['thruster']
              + htr_thr_pwr / self._CAP['thruster']) * dt
        T += random.gauss(0, 0.03)
        s.temp_thruster = T

        # --- Shared parameters ---
        shared_params[P_TEMP_PANEL_PX] = s.temp_panel_px
        shared_params[P_TEMP_PANEL_MX] = s.temp_panel_mx
        shared_params[P_TEMP_PANEL_PY] = s.temp_panel_py
        shared_params[P_TEMP_PANEL_MY] = s.temp_panel_my
        shared_params[P_TEMP_PANEL_PZ] = s.temp_panel_pz
        shared_params[P_TEMP_PANEL_MZ] = s.temp_panel_mz
        shared_params[P_TEMP_OBC]       = s.temp_obc
        shared_params[P_TEMP_BATTERY]   = s.temp_battery
        shared_params[P_TEMP_FPA]       = s.temp_fpa
        shared_params[P_TEMP_THRUSTER]  = s.temp_thruster
        shared_params[P_HTR_BATTERY]    = 1 if s.htr_battery else 0
        shared_params[P_HTR_OBC]        = 1 if s.htr_obc else 0
        shared_params[P_COOLER_FPA]     = 1 if s.cooler_fpa else 0
        shared_params[P_HTR_THRUSTER]   = 1 if s.htr_thruster else 0

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_heater(self, circuit: str, on: bool) -> bool:
        """circuit: 'battery', 'obc', 'thruster'"""
        failed = getattr(self.state, f'htr_{circuit}_failed', False)
        if failed and on:
            return False   # cannot turn on a failed heater
        setattr(self.state, f'htr_{circuit}', on)
        return True

    def cmd_fpa_cooler(self, on: bool) -> bool:
        if self.state.cooler_failed and on:
            return False
        self.state.cooler_fpa = on
        return True

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_heater_failure(self, circuit: str, failed: bool) -> None:
        setattr(self.state, f'htr_{circuit}_failed', failed)
        if failed:
            setattr(self.state, f'htr_{circuit}', False)

    def inject_cooler_failure(self, failed: bool) -> None:
        self.state.cooler_failed = failed
        if failed:
            self.state.cooler_fpa = False

    def inject_obc_thermal(self, extra_heat_w: float) -> None:
        """Inject additional OBC self-heating (thermal runaway scenario)."""
        self.state.obc_internal_heat_w = extra_heat_w

    def get_battery_temp(self) -> float:
        return self.state.temp_battery
