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
from typing import Any, Callable, Optional

from .noise import AWGNSource, PhaseNoiseSource, CWInterferer, WidebandInterferer
from .fading import RicianFading, MultipathChannel
from .link_budget import LinkBudget

logger = logging.getLogger(__name__)


# ── Impairment plug-in registry ────────────────────────────────────────
#
# EXTENSION POINT. An "impairment" is any object exposing
# ``apply(samples: np.ndarray) -> np.ndarray``. Register a factory here and
# the type becomes available from rfsim.yaml (`channel.interferers`), from
# the runtime API (`POST /api/channel`) and from the radio UI without
# touching SpaceLinkChannel:
#
#     from smo_rfsim.channel.space_link import register_impairment
#     register_impairment("iq_imbalance",
#                         lambda spec, cfg: IQImbalance(
#                             gain_db=spec.get("gain_db", 0.5),
#                             phase_deg=spec.get("phase_deg", 2.0)))
#
# The factory receives the raw spec dict from config/API and the owning
# SpaceLinkConfig (for sample_rate etc.). This is the intended landing
# zone for the SatHack impairment set and for the offline chain already in
# recording/iq_recorder.py (I/Q imbalance, DC offset, quantisation,
# power-law phase noise, band-edge filtering, impulsive RFI).
ImpairmentFactory = Callable[[dict, "SpaceLinkConfig"], Any]
_IMPAIRMENT_REGISTRY: dict[str, ImpairmentFactory] = {}


def register_impairment(name: str, factory: ImpairmentFactory) -> None:
    """Register an impairment type usable from config and the runtime API."""
    _IMPAIRMENT_REGISTRY[name.lower()] = factory


def available_impairments() -> list[str]:
    """Names accepted in ``channel.interferers[].type``."""
    return sorted(_IMPAIRMENT_REGISTRY)


def build_impairment(spec: dict, cfg: "SpaceLinkConfig"):
    """Instantiate one impairment from a spec dict. Raises on unknown type."""
    itype = str(spec.get("type", "cw")).lower()
    factory = _IMPAIRMENT_REGISTRY.get(itype)
    if factory is None:
        raise ValueError(
            f"Unknown impairment type '{itype}'. "
            f"Available: {available_impairments()}")
    return factory(spec, cfg)


register_impairment("cw", lambda spec, cfg: CWInterferer(
    freq_offset_hz=float(spec.get("freq_offset_hz", 5000.0)),
    power_dbm=float(spec.get("power_dbm", -90.0)),
    sample_rate=cfg.sample_rate))

register_impairment("wideband", lambda spec, cfg: WidebandInterferer(
    bandwidth_hz=float(spec.get("bandwidth_hz", 50000.0)),
    power_dbm=float(spec.get("power_dbm", -80.0)),
    center_offset_hz=float(spec.get("center_offset_hz", 0.0)),
    sample_rate=cfg.sample_rate))

# SatHack-derived channel impairments (roadmap X-15): Doppler ramp, oscillator
# drift, IQ imbalance, DC offset. Registration runs on import of this module,
# so these types are available anywhere the built-in interferers are.
from .sathack_impairments import register_sathack_impairments  # noqa: E402
register_sathack_impairments(register_impairment)


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

    # Deterministic impairment RNG (None = use the global numpy RNG)
    seed: Optional[int] = None


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
        self._rng = (np.random.default_rng(cfg.seed)
                     if cfg.seed is not None else np.random)

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

        self._awgn = None
        self._phase_noise = None
        self._fading = None
        self._multipath = None
        self._interferers: list = []
        self._rebuild_awgn()
        self._rebuild_phase_noise()
        self._rebuild_fading()
        self._rebuild_multipath()
        self._rebuild_interferers()

    # ── (re)construction of individual impairment stages ──

    def _rebuild_awgn(self):
        cfg = self._config
        self._awgn = AWGNSource(
            eb_n0_db=cfg.eb_n0_db, sps=cfg.sps,
            bits_per_symbol=cfg.bits_per_symbol,
            rng=self._rng) if cfg.awgn_enabled else None

    def _rebuild_phase_noise(self):
        cfg = self._config
        self._phase_noise = PhaseNoiseSource(
            linewidth_hz=cfg.phase_noise_linewidth_hz,
            sample_rate=cfg.sample_rate,
            rng=self._rng) if cfg.phase_noise_enabled else None

    def _rebuild_fading(self):
        cfg = self._config
        self._fading = RicianFading(
            k_factor_db=cfg.fading_k_factor_db,
            max_doppler_hz=cfg.fading_max_doppler_hz,
            sample_rate=cfg.sample_rate,
            seed=cfg.seed if cfg.seed is not None else 42,
        ) if cfg.fading_enabled else None

    def _rebuild_multipath(self):
        cfg = self._config
        self._multipath = MultipathChannel(
            taps=cfg.multipath_taps or None,
            sample_rate=cfg.sample_rate,
        ) if cfg.multipath_enabled else None

    def _rebuild_interferers(self):
        cfg = self._config
        built = []
        for spec in cfg.interferers:
            if not isinstance(spec, dict):
                raise ValueError(f"Interferer spec must be a mapping: {spec!r}")
            built.append(build_impairment(spec, cfg))
        self._interferers = built

    # ── Runtime control ──

    def set_impairments(self, **kwargs) -> dict:
        """Update impairment settings live and rebuild affected stages.

        Accepts any ``SpaceLinkConfig`` field name. Unknown names raise
        ``ValueError`` so a typo in an API call is not silently ignored.
        Returns the resulting ``get_status()`` snapshot.
        """
        from dataclasses import fields as _fields
        known = {f.name for f in _fields(SpaceLinkConfig)}
        touched = set()
        for key, value in kwargs.items():
            if value is None:
                continue
            if key not in known:
                raise ValueError(
                    f"Unknown channel parameter '{key}'. Valid: {sorted(known)}")
            setattr(self._config, key, value)
            touched.add(key)

        if touched & {"awgn_enabled", "sps", "bits_per_symbol"}:
            self._rebuild_awgn()
        if "eb_n0_db" in touched and self._awgn:
            self._awgn.set_eb_n0(self._config.eb_n0_db)
        if touched & {"phase_noise_enabled", "phase_noise_linewidth_hz",
                      "sample_rate"}:
            self._rebuild_phase_noise()
        if touched & {"fading_enabled", "fading_k_factor_db",
                      "fading_max_doppler_hz", "sample_rate"}:
            self._rebuild_fading()
        if touched & {"multipath_enabled", "multipath_taps", "sample_rate"}:
            self._rebuild_multipath()
        if touched & {"interferers", "sample_rate"}:
            self._rebuild_interferers()
        if "doppler_hz" in touched:
            self._doppler_hz = self._config.doppler_hz
        if "sample_rate" in touched:
            self._sample_rate = self._config.sample_rate
        if touched:
            logger.info("SpaceLinkChannel impairments updated: %s",
                        sorted(touched))
        return self.get_status()

    def set_sample_rate(self, sample_rate: float):
        """Retune every rate-dependent impairment (symbol-rate changes)."""
        self.set_impairments(sample_rate=float(sample_rate))

    @property
    def link_budget(self) -> LinkBudget:
        return self._link_budget

    @property
    def config(self) -> SpaceLinkConfig:
        return self._config

    def compute_link_budget(self, range_km: float, data_rate_bps: float,
                            elevation_deg: float = 90.0) -> dict:
        """Full CCSDS 401.0-B link budget for the current geometry."""
        return self._link_budget.compute(range_km, data_rate_bps,
                                         elevation_deg=elevation_deg)

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
            return (self._rng.normal(0, sigma, len(samples))
                    + 1j * self._rng.normal(0, sigma, len(samples))
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
            "interferers": [dict(spec) for spec in self._config.interferers
                            if isinstance(spec, dict)],
            "fading_max_doppler_hz": self._config.fading_max_doppler_hz,
            "frequency_hz": self._config.frequency_hz,
            "sample_rate": self._sample_rate,
            "available_impairments": available_impairments(),
        }
