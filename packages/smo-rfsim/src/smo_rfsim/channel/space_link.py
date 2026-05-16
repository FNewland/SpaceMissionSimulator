"""Composite space link channel model.

Combines all channel effects into a single processing chain:
  Signal → Doppler shift → Fading → Phase noise → AWGN → Interferers

Each effect is independently configurable and can be enabled/disabled.
The channel model accepts complex baseband samples and returns
impaired samples with realistic RF artifacts.

Reference: CCSDS 401.0-B (RF and Modulation Systems)
"""

import math
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Optional

from .noise import AWGNSource, PhaseNoiseSource, CWInterferer, WidebandInterferer
from .fading import RicianFading, MultipathChannel
from .link_budget import LinkBudget

logger = logging.getLogger(__name__)


@dataclass
class SpaceLinkConfig:
    """Configuration for the space link channel model."""
    # Link budget
    eirp_dbw: float = 0.0
    frequency_hz: float = 437.0e6
    gs_gt_dbk: float = 12.0
    atmospheric_loss_db: float = 0.5
    implementation_loss_db: float = 2.0
    polarization_loss_db: float = 0.3
    coding_gain_db: float = 6.0

    # Noise
    awgn_enabled: bool = True
    eb_n0_db: float = 10.0  # override (used if link budget not driven by geometry)

    # Phase noise
    phase_noise_enabled: bool = False
    phase_noise_linewidth_hz: float = 10.0

    # Fading
    fading_enabled: bool = False
    fading_k_factor_db: float = 10.0  # Rician K-factor
    fading_max_doppler_hz: float = 50.0

    # Multipath
    multipath_enabled: bool = False
    multipath_taps: list = field(default_factory=list)

    # Interferers
    interferers: list = field(default_factory=list)

    # Doppler
    doppler_hz: float = 0.0

    # Signal processing
    sample_rate: float = 256000.0
    sps: int = 8
    bits_per_symbol: int = 1


class SpaceLinkChannel:
    """Full space link channel model with all configurable effects.

    Processing order:
    1. Doppler frequency shift (from orbital dynamics)
    2. Multipath delay spread (if enabled)
    3. Rician/Rayleigh fading (if enabled)
    4. Phase noise (oscillator jitter)
    5. AWGN (thermal noise)
    6. Interferers (CW, wideband)
    """

    def __init__(self, config: SpaceLinkConfig = None):
        cfg = config or SpaceLinkConfig()
        self._config = cfg
        self._doppler_hz = cfg.doppler_hz
        self._doppler_phase = 0.0
        self._sample_rate = cfg.sample_rate
        self._link_active = True
        self._sample_counter = 0

        # Link budget calculator
        self._link_budget = LinkBudget(
            eirp_dbw=cfg.eirp_dbw,
            frequency_hz=cfg.frequency_hz,
            gs_gt_dbk=cfg.gs_gt_dbk,
            atmospheric_loss_db=cfg.atmospheric_loss_db,
            implementation_loss_db=cfg.implementation_loss_db,
            polarization_loss_db=cfg.polarization_loss_db,
            coding_gain_db=cfg.coding_gain_db,
        )

        # Noise sources
        self._awgn = AWGNSource(
            eb_n0_db=cfg.eb_n0_db,
            sps=cfg.sps,
            bits_per_symbol=cfg.bits_per_symbol,
        ) if cfg.awgn_enabled else None

        self._phase_noise = PhaseNoiseSource(
            linewidth_hz=cfg.phase_noise_linewidth_hz,
            sample_rate=cfg.sample_rate,
        ) if cfg.phase_noise_enabled else None

        # Fading
        self._fading = RicianFading(
            k_factor_db=cfg.fading_k_factor_db,
            max_doppler_hz=cfg.fading_max_doppler_hz,
            sample_rate=cfg.sample_rate,
        ) if cfg.fading_enabled else None

        # Multipath
        self._multipath = MultipathChannel(
            taps=cfg.multipath_taps or None,
            sample_rate=cfg.sample_rate,
        ) if cfg.multipath_enabled else None

        # Interferers
        self._interferers = []
        for intf in cfg.interferers:
            itype = intf.get("type", "cw")
            if itype == "cw":
                self._interferers.append(CWInterferer(
                    freq_offset_hz=intf.get("freq_offset_hz", 5000),
                    power_dbm=intf.get("power_dbm", -90),
                    sample_rate=cfg.sample_rate,
                ))
            elif itype == "wideband":
                self._interferers.append(WidebandInterferer(
                    bandwidth_hz=intf.get("bandwidth_hz", 50000),
                    power_dbm=intf.get("power_dbm", -80),
                    center_offset_hz=intf.get("center_offset_hz", 0),
                    sample_rate=cfg.sample_rate,
                ))

    def set_eb_n0(self, db: float):
        """Override Eb/N0 directly (bypasses link budget geometry)."""
        self._config.eb_n0_db = db
        if self._awgn:
            self._awgn.set_eb_n0(db)

    def set_doppler(self, hz: float):
        """Set Doppler frequency offset."""
        self._doppler_hz = hz

    def set_link_active(self, active: bool):
        """Enable/disable signal path (no signal when not in view)."""
        self._link_active = active

    def set_bits_per_symbol(self, bps: int):
        """Update bits-per-symbol for noise scaling."""
        self._config.bits_per_symbol = bps
        if self._awgn:
            self._awgn.set_bits_per_symbol(bps)

    def set_freq_offset(self, hz: float):
        """Alias for set_doppler."""
        self.set_doppler(hz)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Apply all channel effects to complex baseband samples."""
        if not self._link_active:
            # No signal — return noise only
            sigma = 0.1
            return (np.random.normal(0, sigma, len(samples))
                    + 1j * np.random.normal(0, sigma, len(samples))
                    ).astype(samples.dtype)

        out = samples.copy()

        # 1. Doppler frequency shift
        if self._doppler_hz != 0.0:
            n = len(out)
            t = np.arange(n) / self._sample_rate
            phase = 2 * math.pi * self._doppler_hz * t + self._doppler_phase
            out = out * np.exp(1j * phase).astype(out.dtype)
            self._doppler_phase = phase[-1] if n > 0 else self._doppler_phase
            self._doppler_phase %= (2 * math.pi)

        # 2. Multipath
        if self._multipath:
            out = self._multipath.apply(out)

        # 3. Fading
        if self._fading:
            out = self._fading.apply(out)

        # 4. Phase noise
        if self._phase_noise:
            out = self._phase_noise.apply(out)

        # 5. AWGN
        if self._awgn:
            out = self._awgn.apply(out)

        # 6. Interferers
        for intf in self._interferers:
            out = intf.apply(out)

        self._sample_counter += len(samples)
        return out

    def get_status(self) -> dict:
        """Return current channel state for display."""
        return {
            "eb_n0_db": self._config.eb_n0_db,
            "doppler_hz": self._doppler_hz,
            "link_active": self._link_active,
            "awgn_enabled": self._config.awgn_enabled,
            "phase_noise_enabled": self._config.phase_noise_enabled,
            "phase_noise_linewidth_hz": self._config.phase_noise_linewidth_hz,
            "fading_enabled": self._config.fading_enabled,
            "fading_k_factor_db": self._config.fading_k_factor_db,
            "multipath_enabled": self._config.multipath_enabled,
            "n_interferers": len(self._interferers),
            "frequency_hz": self._config.frequency_hz,
            "sample_rate": self._sample_rate,
        }
