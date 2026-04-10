"""
EO Mission Simulator — Payload (Optical Imager)
FPA temperature management, imaging session state machine,
mass memory usage tracking, and failure injection.
"""
import random
from dataclasses import dataclass, field
from typing import Dict, List

from config import (
    P_PLI_MODE, P_FPA_TEMP, P_COOLER_PWR, P_IMAGER_TEMP,
    P_STORE_USED_PCT, P_IMAGE_COUNT, P_SCENE_ID,
    P_LINE_RATE, P_PLI_DATA_RATE, P_CHECKSUM_ERRORS,
    FPA_COOLER_POWER_W,
)

# Payload modes
PLI_MODE_OFF      = 0
PLI_MODE_STANDBY  = 1
PLI_MODE_IMAGING  = 2

# FPA cooling constants
_FPA_COOLER_TARGET_C  = -5.0
_FPA_AMBIENT_C        = 5.0
_FPA_TAU_COOLING      = 100.0   # s — time constant to reach cold
_FPA_TAU_WARMING      = 120.0   # s — time constant to warm back
_FPA_COOLDOWN_TIME_S  = 600.0   # required pre-imaging cooldown (s)


@dataclass
class PayloadState:
    mode:              int   = PLI_MODE_OFF
    fpa_temp:          float = 5.0    # °C  (matches ambient when off)
    cooler_active:     bool  = False
    imager_temp:       float = 5.0    # °C
    store_used_pct:    float = 20.0   # %
    image_count:       int   = 12
    current_scene_id:  int   = 0
    line_rate:         float = 0.0    # lines/s  (500 during imaging)
    data_rate_mbps:    float = 0.0    # Mbps
    checksum_errors:   int   = 0
    # Cooldown timer
    cooler_on_time_s:  float = 0.0
    # Image size (MB per image)
    image_size_mb:     float = 800.0
    total_storage_mb:  float = 20000.0  # 20 GB
    # Failures
    cooler_failed:     bool  = False
    fpa_degraded:      bool  = False


class PayloadSubsystem:
    """
    Optical imager payload.
    Must go through STANDBY → FPA cooldown → IMAGING sequence.
    """

    def __init__(self, dt_s: float = 1.0):
        self.dt    = dt_s
        self.state = PayloadState()

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, orbit_state, shared_params: Dict) -> None:
        s  = self.state
        dt = self.dt

        # --- FPA temperature ---
        if s.cooler_active and not s.cooler_failed:
            target_fpa = _FPA_COOLER_TARGET_C
            tau        = _FPA_TAU_COOLING
            s.cooler_on_time_s += dt
        else:
            target_fpa = _FPA_AMBIENT_C
            tau        = _FPA_TAU_WARMING
            s.cooler_on_time_s = 0.0

        s.fpa_temp += (target_fpa - s.fpa_temp) / tau * dt + random.gauss(0, 0.02)

        # --- Imager temperature (less tightly controlled) ---
        env_imager = 5.0 if not orbit_state.in_eclipse else -5.0
        s.imager_temp += (env_imager - s.imager_temp) / 400.0 * dt + random.gauss(0, 0.03)

        # --- Mode behaviour ---
        if s.mode == PLI_MODE_OFF:
            s.cooler_active = False
            s.line_rate     = 0.0
            s.data_rate_mbps = 0.0

        elif s.mode == PLI_MODE_STANDBY:
            s.cooler_active  = True   # start pre-cooling
            s.line_rate      = 0.0
            s.data_rate_mbps = 0.0

        elif s.mode == PLI_MODE_IMAGING:
            # Can only image if FPA is sufficiently cold
            fpa_ready = s.fpa_temp <= (_FPA_COOLER_TARGET_C + 5.0)
            if fpa_ready and not s.cooler_failed:
                s.line_rate     = 500.0 + random.gauss(0, 2.0)
                s.data_rate_mbps = 80.0 + random.gauss(0, 0.5)
                # Store image data
                data_per_tick_mb = s.data_rate_mbps * 1e6 / 8 / 1e6 * dt
                s.store_used_pct = min(100.0, s.store_used_pct
                                        + data_per_tick_mb / s.total_storage_mb * 100.0)
                # Occasional checksum error
                if random.random() < (0.0001 * (2 if s.fpa_degraded else 1)):
                    s.checksum_errors += 1
            else:
                # FPA not ready — imaging not proceeding
                s.line_rate     = 0.0
                s.data_rate_mbps = 0.0

        # --- Shared params ---
        shared_params[P_PLI_MODE]         = s.mode
        shared_params[P_FPA_TEMP]         = s.fpa_temp
        shared_params[P_COOLER_PWR]       = FPA_COOLER_POWER_W if s.cooler_active else 0.0
        shared_params[P_IMAGER_TEMP]      = s.imager_temp
        shared_params[P_STORE_USED_PCT]   = s.store_used_pct
        shared_params[P_IMAGE_COUNT]      = s.image_count
        shared_params[P_SCENE_ID]         = s.current_scene_id
        shared_params[P_LINE_RATE]        = s.line_rate
        shared_params[P_PLI_DATA_RATE]    = s.data_rate_mbps
        shared_params[P_CHECKSUM_ERRORS]  = s.checksum_errors

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def cmd_set_mode(self, mode: int) -> bool:
        if mode not in (PLI_MODE_OFF, PLI_MODE_STANDBY, PLI_MODE_IMAGING):
            return False
        self.state.mode = mode
        return True

    def cmd_set_scene(self, scene_id: int) -> bool:
        self.state.current_scene_id = scene_id
        return True

    def cmd_delete_image(self, count: int = 1) -> bool:
        """Simulate downlinking / deleting images."""
        freed_mb = self.state.image_size_mb * count
        freed_pct = freed_mb / self.state.total_storage_mb * 100.0
        self.state.store_used_pct = max(0.0, self.state.store_used_pct - freed_pct)
        self.state.image_count = max(0, self.state.image_count - count)
        return True

    # ------------------------------------------------------------------
    # Failure injection
    # ------------------------------------------------------------------

    def inject_cooler_failure(self, failed: bool) -> None:
        self.state.cooler_failed = failed
        if failed:
            self.state.cooler_active = False
            # If currently imaging, this will result in FPA warming

    def inject_fpa_degradation(self, degraded: bool) -> None:
        self.state.fpa_degraded = degraded
