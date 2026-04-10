"""
EO Mission Simulator — Telemetry, Tracking & Command (TT&C)
Transponder state, link simulation tied to orbit contact windows,
ranging, data rate control, and failure injection.
"""
import math
import random
from dataclasses import dataclass
from typing import Dict

from config import (
    P_TTC_MODE, P_LINK_STATUS, P_RSSI, P_LINK_MARGIN,
    P_UL_FREQ, P_DL_FREQ, P_TM_DATA_RATE,
    P_XPDR_TEMP, P_RANGING_STATUS, P_RANGE_KM,
    P_CONTACT_ELEVATION, P_CONTACT_AZ,
    TRANSPONDER_UL_FREQ_MHZ, TRANSPONDER_DL_FREQ_MHZ,
    TM_RATE_HI_BPS, TM_RATE_LO_BPS,
    GS_MIN_ELEVATION,
)

# TT&C modes
TTC_MODE_PRIMARY    = 0
TTC_MODE_REDUNDANT  = 1
TTC_MODE_SAFE       = 2   # emergency low-rate beacon

# Nominal link budget constants (simplified Friis model)
_EIRP_DBW         = 10.0    # dBW  (ground station EIRP)
_GS_G_T           = 20.0    # dB/K  (ground station G/T)
_SC_GAIN_DBI      = 3.0     # dBi  antenna gain
_BOLTZMANN_DB     = -228.6  # dBW/K/Hz
_DL_FREQ_HZ       = TRANSPONDER_DL_FREQ_MHZ * 1e6
_NOMINAL_RSSI_DBM = -85.0   # dBm at 1000 km, 64 kbps


@dataclass
class TTCState:
    mode:           int   = TTC_MODE_PRIMARY
    link_active:    bool  = False      # True when in contact window
    rssi_dbm:       float = -120.0
    link_margin_db: float = 0.0
    tm_data_rate:   int   = TM_RATE_HI_BPS
    xpdr_temp:      float = 28.0
    ranging_active: bool  = False
    range_km:       float = 0.0
    elevation_deg:  float = -90.0
    azimuth_deg:    float = 0.0
    # Failures
    primary_failed: bool  = False
    redundant_failed: bool = False


class TTCSubsystem:
    """
    TT&C transponder and link simulation.
    Link is active only when orbit propagator reports contact.
    RSSI is computed from range using simplified Friis equation.
    """

    # Required Eb/N0 for BPSK at BER 1e-6 ≈ 10.5 dB, add implementation losses
    _REQUIRED_SNR_DB = 12.0

    def __init__(self, dt_s: float = 1.0):
        self.dt    = dt_s
        self.state = TTCState()

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s = self.state

        # --- Link state ---
        in_contact = orbit_state.in_contact
        if s.primary_failed and s.mode == TTC_MODE_PRIMARY:
            in_contact = False   # no contact until switched to redundant
        if s.redundant_failed and s.mode == TTC_MODE_REDUNDANT:
            in_contact = False

        s.link_active     = in_contact
        s.elevation_deg   = orbit_state.gs_elevation_deg
        s.azimuth_deg     = orbit_state.gs_azimuth_deg
        s.range_km        = orbit_state.gs_range_km

        # --- Link budget (Friis path loss approximation) ---
        if in_contact and s.range_km > 0:
            # Free space path loss (dB)
            fspl = 20 * math.log10(s.range_km * 1000) + 20 * math.log10(_DL_FREQ_HZ) - 147.55
            # RSSI: simplified Friis receive power (dBm)
            s.rssi_dbm = (_EIRP_DBW + _SC_GAIN_DBI - fspl
                          + 30.0   # dBW → dBm
                          + random.gauss(0, 0.5))
            # Link margin
            # Noise floor for 64 kbps BPSK
            noise_bw_db = 10 * math.log10(s.tm_data_rate)
            noise_floor  = _BOLTZMANN_DB + _GS_G_T + noise_bw_db
            snr = s.rssi_dbm - 30 - noise_floor
            s.link_margin_db = snr - self._REQUIRED_SNR_DB + random.gauss(0, 0.2)
        else:
            s.rssi_dbm       = -120.0 + random.gauss(0, 0.5)
            s.link_margin_db = 0.0
            s.ranging_active = False

        # --- Ranging ---
        s.ranging_active = in_contact and (s.range_km > 0)

        # --- Transponder temperature ---
        tx_load = 1.0 if in_contact else 0.2
        target_temp = 28.0 + 8.0 * tx_load
        s.xpdr_temp += (target_temp - s.xpdr_temp) / 300.0 * self.dt + random.gauss(0, 0.02)

        # --- Shared params ---
        shared_params[P_TTC_MODE]        = s.mode
        shared_params[P_LINK_STATUS]     = 1 if s.link_active else 0
        shared_params[P_RSSI]            = s.rssi_dbm
        shared_params[P_LINK_MARGIN]     = s.link_margin_db
        shared_params[P_UL_FREQ]         = TRANSPONDER_UL_FREQ_MHZ
        shared_params[P_DL_FREQ]         = TRANSPONDER_DL_FREQ_MHZ
        shared_params[P_TM_DATA_RATE]    = s.tm_data_rate
        shared_params[P_XPDR_TEMP]       = s.xpdr_temp
        shared_params[P_RANGING_STATUS]  = 1 if s.ranging_active else 0
        shared_params[P_RANGE_KM]        = s.range_km
        shared_params[P_CONTACT_ELEVATION] = s.elevation_deg
        shared_params[P_CONTACT_AZ]        = s.azimuth_deg

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_switch_to_redundant(self) -> bool:
        if self.state.redundant_failed:
            return False
        self.state.mode = TTC_MODE_REDUNDANT
        return True

    def cmd_switch_to_primary(self) -> bool:
        if self.state.primary_failed:
            return False
        self.state.mode = TTC_MODE_PRIMARY
        return True

    def cmd_set_tm_rate(self, rate_bps: int) -> bool:
        if rate_bps in (TM_RATE_HI_BPS, TM_RATE_LO_BPS):
            self.state.tm_data_rate = rate_bps
            return True
        return False

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_primary_failure(self, failed: bool) -> None:
        self.state.primary_failed = failed
        if failed and self.state.mode == TTC_MODE_PRIMARY:
            self.state.link_active = False

    def inject_redundant_failure(self, failed: bool) -> None:
        self.state.redundant_failed = failed

    @property
    def is_link_active(self) -> bool:
        return self.state.link_active
