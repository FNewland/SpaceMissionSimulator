"""SatHack-derived RF channel impairments (roadmap X-15).

These impairments are ported from Franz's SatHack SDR/weak-signal toolkit.
SatHack is an *acquisition/analysis* toolkit — it spends most of its effort
undoing channel effects (centring off the DC birdie, de-rotating Doppler,
tracking oscillator chirp). The genuinely portable *channel* physics lives in
its signal-generation and conditioning code; each class below is the forward
(impairing) model of an effect SatHack demonstrably characterises or removes.

Ported effects and their SatHack provenance:

* ``DopplerRampImpairment`` — realistic LEO Doppler S-curve, TLE-free.
  Physics from ``rfchain/rfchain/doppler.py::predicted_doppler_hz`` (range-rate
  → Doppler) and the linear+quadratic carrier of
  ``rfchain/rfchain/sim.py::add_cfo``. The S-curve is generated from a
  circular-orbit overpass geometry so no TLE/SGP4 is needed.

* ``OscillatorDriftImpairment`` — slow carrier frequency offset with linear
  drift, quadratic aging, and a seeded random-walk (temperature/retrace).
  From ``rfchain/rfchain/sim.py::add_cfo`` (``drift_hz_s`` term) and the TCXO
  ``ppm`` / ``freq_offset_hz`` notes in SatHack ``config.yaml``.

* ``IQImbalanceImpairment`` — gain/phase quadrature imbalance that raises a
  conjugate image tone. This is the effect SatHack's recorder deliberately
  tunes *around* — ``config.yaml`` ``sdr.freq_offset_hz`` ("offset /
  IQ-imbalance tuning") pushes the signal off the DC image — and the effect
  the ``space_link`` extension-point comment lists as an intended port.

* ``DCOffsetImpairment`` — constant I/Q bias (the RTL-SDR "centre birdie").
  The forward model of ``sathack/cf32_source.py::remove_dc``, which strips
  exactly this before demodulation.

Each impairment exposes ``apply(samples: np.ndarray) -> np.ndarray`` and is
registered via ``register_impairment`` so it is selectable from
``rfsim.yaml`` (``channel.interferers[].type``), the runtime API, and the
radio UI, exactly like the built-in ``cw`` / ``wideband`` interferers.

Determinism: effects with a random component take their seed from the owning
``SpaceLinkConfig.seed`` (offset per type so streams do not correlate), matching
the numpy ``default_rng`` convention used elsewhere in this package.
"""

from __future__ import annotations

import math
import numpy as np


# Mean Earth radius and geocentric gravitational constant (WGS-84 / EGM),
# used only to synthesise a physically-plausible TLE-free LEO Doppler curve.
_EARTH_RADIUS_M = 6_378_137.0
_MU_EARTH = 3.986_004_418e14      # m^3 / s^2
_C = 299_792_458.0                # m/s


class DopplerRampImpairment:
    """Realistic LEO Doppler shift that ramps across an overpass (TLE-free).

    Ported from ``rfchain/rfchain/doppler.py`` (which computes Doppler from the
    satellite→station range-rate of an SGP4-propagated TLE) and the
    linear+quadratic carrier of ``rfchain/rfchain/sim.py::add_cfo``.

    Instead of a TLE, the characteristic S-curve is generated from a circular
    overpass geometry: the slant range of a satellite passing at closest
    approach distance ``r0`` with along-track relative speed ``v`` is

        R(t) = sqrt(r0^2 + (v (t - t_ca))^2)

    whose range-rate R'(t) = v^2 (t - t_ca) / R(t) gives the familiar Doppler
    curve — a steep near-linear ramp through zero at closest approach (t_ca),
    saturating to ±f_max towards the horizon. This reproduces the real
    "drift-induced coherence loss" SatHack's docs flag as the station's true
    enemy (``Domain_Transform_Signal_Extraction.md``).

    Config keys (``type: doppler_ramp``):
        carrier_hz:        RF carrier (defaults to the channel frequency).
        altitude_km:       orbit altitude for the geometry (default 550).
        elevation_max_deg: peak elevation of the pass; sets curve steepness and
                           peak Doppler (default 90 = overhead).
        pass_duration_s:   horizon-to-horizon duration used to place t_ca
                           (default 600).
        t_ca_s:            time of closest approach, in stream seconds
                           (default pass_duration_s / 2).
        max_doppler_hz:    optional direct override of the peak Doppler; when
                           given, the orbit geometry is scaled to hit it.
    """

    def __init__(self, carrier_hz: float = 2200.0e6, altitude_km: float = 550.0,
                 elevation_max_deg: float = 90.0, pass_duration_s: float = 600.0,
                 t_ca_s: float | None = None, max_doppler_hz: float | None = None,
                 sample_rate: float = 256000.0):
        self._fs = float(sample_rate)
        self._carrier = float(carrier_hz)
        self._t_ca = float(t_ca_s) if t_ca_s is not None else 0.5 * pass_duration_s
        self._phase = 0.0
        self._sample_counter = 0

        # Circular-orbit overpass geometry → (r0, v) for R(t).
        r_orbit = _EARTH_RADIUS_M + altitude_km * 1000.0
        v_sat = math.sqrt(_MU_EARTH / r_orbit)                    # orbital speed
        el = math.radians(max(0.0, min(89.999, elevation_max_deg)))
        # Slant range at closest approach for the given peak elevation (law of
        # sines on the Earth-centre / station / satellite triangle).
        sin_beta = (_EARTH_RADIUS_M / r_orbit) * math.cos(el)
        beta = math.asin(max(-1.0, min(1.0, sin_beta)))
        gamma = (math.pi / 2.0) - el - beta                       # geocentric arc
        self._r0 = math.sqrt(_EARTH_RADIUS_M**2 + r_orbit**2
                             - 2 * _EARTH_RADIUS_M * r_orbit * math.cos(gamma))
        # Relative along-track speed component projected at the ground track;
        # the sub-satellite point moves at v_sat * Re / r_orbit.
        self._v = v_sat * (_EARTH_RADIUS_M / r_orbit)
        # Peak Doppler magnitude (approached far from t_ca): f = fc * v / c.
        f_peak_geom = self._carrier * self._v / _C
        if max_doppler_hz is not None and f_peak_geom > 0:
            # Scale the effective velocity so the peak matches the request.
            self._v *= float(max_doppler_hz) / f_peak_geom
        self._max_doppler_hz = (float(max_doppler_hz)
                                if max_doppler_hz is not None else f_peak_geom)

    def instantaneous_doppler_hz(self, t: np.ndarray | float):
        """Commanded Doppler (Hz) at stream time ``t`` — the profile itself.

        Positive Doppler = satellite approaching (before closest approach).
        """
        t = np.asarray(t, dtype=np.float64)
        dt = t - self._t_ca
        rng = np.sqrt(self._r0**2 + (self._v * dt) ** 2)
        range_rate = (self._v**2) * dt / rng                     # m/s
        # approaching (dt < 0, range shrinking) → positive Doppler
        return -self._carrier * range_rate / _C

    def apply(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        if n == 0 or self._max_doppler_hz == 0.0:
            return samples
        idx = np.arange(n) + self._sample_counter
        t = idx / self._fs
        dop = self.instantaneous_doppler_hz(t)
        # Integrate instantaneous frequency to phase, carrying phase across
        # successive buffers so a streamed pass stays continuous.
        phase = self._phase + 2.0 * math.pi * np.cumsum(dop) / self._fs
        self._phase = float(phase[-1]) % (2.0 * math.pi)
        self._sample_counter += n
        return samples * np.exp(1j * phase).astype(samples.dtype)


class OscillatorDriftImpairment:
    """Slow oscillator frequency error: offset + linear drift + aging + walk.

    Ported from ``rfchain/rfchain/sim.py::add_cfo`` — whose phase is
    ``2π(f0 t + ½ drift t²)`` — extended with a quadratic aging term and a
    seeded frequency random-walk (temperature retrace / TCXO wander). The
    ``ppm`` and ``freq_offset_hz`` fields in SatHack ``config.yaml`` are the
    real-hardware analogue: an RTL dongle's TCXO sits at a ppm-level offset
    that drifts with temperature over a pass.

    This is distinct from :class:`PhaseNoiseSource` (fast Wiener *phase*
    jitter): here the error is a slowly-varying *frequency*.

    Config keys (``type: oscillator_drift``):
        f0_hz:        initial frequency offset (default 100).
        drift_hz_s:   linear drift rate (default 5).
        aging_hz_s2:  quadratic aging term (default 0).
        walk_hz:      std-dev of the per-second frequency random walk
                      (default 0 = deterministic drift only).
    """

    _SEED_OFFSET = 4101

    def __init__(self, f0_hz: float = 100.0, drift_hz_s: float = 5.0,
                 aging_hz_s2: float = 0.0, walk_hz: float = 0.0,
                 sample_rate: float = 256000.0, seed: int | None = None):
        self._fs = float(sample_rate)
        self._f0 = float(f0_hz)
        self._drift = float(drift_hz_s)
        self._aging = float(aging_hz_s2)
        self._walk = float(walk_hz)
        self._phase = 0.0
        self._walk_freq = 0.0            # accumulated random-walk offset (Hz)
        self._t0 = 0.0                   # elapsed stream time (s)
        self._rng = np.random.default_rng(
            (0 if seed is None else int(seed)) + self._SEED_OFFSET)

    def apply(self, samples: np.ndarray) -> np.ndarray:
        n = len(samples)
        if n == 0:
            return samples
        t = self._t0 + np.arange(n) / self._fs
        # Deterministic frequency programme.
        freq = self._f0 + self._drift * t + self._aging * t * t
        # Optional seeded random walk on the frequency (per-sample increments
        # scaled so the std grows as sqrt(seconds) at rate ``walk_hz``/√s).
        if self._walk > 0.0:
            sigma = self._walk * math.sqrt(1.0 / self._fs)
            steps = self._rng.normal(0.0, sigma, n)
            walk = self._walk_freq + np.cumsum(steps)
            self._walk_freq = float(walk[-1])
            freq = freq + walk
        phase = self._phase + 2.0 * math.pi * np.cumsum(freq) / self._fs
        self._phase = float(phase[-1]) % (2.0 * math.pi)
        self._t0 = float(t[-1] + 1.0 / self._fs)
        return samples * np.exp(1j * phase).astype(samples.dtype)


class IQImbalanceImpairment:
    """Quadrature gain/phase imbalance that raises a conjugate image tone.

    Motivated by SatHack's front-end handling: ``config.yaml``
    ``sdr.freq_offset_hz`` tunes the signal deliberately *off* DC ("offset /
    IQ-imbalance tuning") precisely so the mirror-image self-interference an
    imbalanced I/Q demodulator produces does not sit on the signal, and the
    ``space_link`` registry comment names ``iq_imbalance`` as an intended port.

    Model (standard widely-used form): for gain imbalance ``g`` (linear, from
    ``gain_db``) and phase error ``φ`` (from ``phase_deg``),

        y = μ·x + ν·conj(x),   μ = ½(1 + g e^{-jφ}),  ν = ½(1 - g e^{jφ})

    A pure tone at +f therefore gains an image at −f whose level relative to
    the wanted tone is |ν/μ|. With ``gain_db = 0`` and ``phase_deg = 0`` the
    imbalance vanishes (μ = 1, ν = 0) — an exact no-op.

    Config keys (``type: iq_imbalance``):
        gain_db:   I-vs-Q gain imbalance in dB (default 0.5).
        phase_deg: quadrature phase error in degrees (default 2.0).
    """

    def __init__(self, gain_db: float = 0.5, phase_deg: float = 2.0):
        g = 10.0 ** (float(gain_db) / 20.0)
        phi = math.radians(float(phase_deg))
        self._mu = 0.5 * (1.0 + g * np.exp(-1j * phi))
        self._nu = 0.5 * (1.0 - g * np.exp(1j * phi))

    def image_rejection_db(self) -> float:
        """Image-rejection ratio |μ/ν| in dB (∞ when perfectly balanced)."""
        if self._nu == 0:
            return float("inf")
        return 20.0 * math.log10(abs(self._mu) / abs(self._nu))

    def apply(self, samples: np.ndarray) -> np.ndarray:
        if self._nu == 0:
            return samples
        out = self._mu * samples + self._nu * np.conj(samples)
        return out.astype(samples.dtype)


class DCOffsetImpairment:
    """Constant I/Q bias — the RTL-SDR "centre birdie".

    Forward model of ``sathack/cf32_source.py::remove_dc`` (which subtracts the
    sample mean before demodulation because "RTL front ends always sit on a
    centre birdie"). Adds a fixed complex offset, i.e. a spectral spike at 0 Hz.

    Config keys (``type: dc_offset``):
        offset_i: constant added to the in-phase channel (default 0.05).
        offset_q: constant added to the quadrature channel (default 0.0).
    """

    def __init__(self, offset_i: float = 0.05, offset_q: float = 0.0):
        self._dc = complex(float(offset_i), float(offset_q))

    def apply(self, samples: np.ndarray) -> np.ndarray:
        if self._dc == 0:
            return samples
        return (samples + self._dc).astype(samples.dtype)


# ── Registration ───────────────────────────────────────────────────────────
#
# Imported for its side effects by ``space_link`` so these types are available
# wherever the built-in impairments are. Factories mirror the cw/wideband
# registrations: they receive the raw spec dict and the owning SpaceLinkConfig.

def register_sathack_impairments(register) -> None:
    """Register all SatHack-derived impairments with ``register`` (the
    ``register_impairment`` callable from ``space_link``)."""

    register("doppler_ramp", lambda spec, cfg: DopplerRampImpairment(
        carrier_hz=float(spec.get("carrier_hz", cfg.frequency_hz)),
        altitude_km=float(spec.get("altitude_km", 550.0)),
        elevation_max_deg=float(spec.get("elevation_max_deg", 90.0)),
        pass_duration_s=float(spec.get("pass_duration_s", 600.0)),
        t_ca_s=(None if spec.get("t_ca_s") is None else float(spec["t_ca_s"])),
        max_doppler_hz=(None if spec.get("max_doppler_hz") is None
                        else float(spec["max_doppler_hz"])),
        sample_rate=cfg.sample_rate))

    register("oscillator_drift", lambda spec, cfg: OscillatorDriftImpairment(
        f0_hz=float(spec.get("f0_hz", 100.0)),
        drift_hz_s=float(spec.get("drift_hz_s", 5.0)),
        aging_hz_s2=float(spec.get("aging_hz_s2", 0.0)),
        walk_hz=float(spec.get("walk_hz", 0.0)),
        sample_rate=cfg.sample_rate,
        seed=cfg.seed))

    register("iq_imbalance", lambda spec, cfg: IQImbalanceImpairment(
        gain_db=float(spec.get("gain_db", 0.5)),
        phase_deg=float(spec.get("phase_deg", 2.0))))

    register("dc_offset", lambda spec, cfg: DCOffsetImpairment(
        offset_i=float(spec.get("offset_i", 0.05)),
        offset_q=float(spec.get("offset_q", 0.0))))
