"""Baseband I/Q recorder — export simulated downlink as .cf32 + sidecar.

The recorder turns the simulator's modulated baseband into a file that is
byte-for-byte compatible with:

  * GNU Radio File Sink (type ``complex`` = interleaved float32 I,Q), and
  * the RTL-SDR ``.cf32`` captures used by the ground-station recorder
    (``satscanner.py`` / ``analyze.py``), so that a simulated pass can be
    dropped straight into the same analysis/comparison tooling as a real
    spacecraft recording.

Pipeline
--------
    bits ─▶ Modulator (sps_int) ─▶ FFT-resample to fs_out
         ─▶ pass amplitude envelope (AOS/TCA/LOS hump) & optional burst gating
         ─▶ phase noise ─▶ time-varying Doppler + static carrier offset
         ─▶ IQ imbalance ─▶ DC offset ─▶ wideband AWGN floor
         ─▶ scale to [-1,1] (+ optional 8-bit quantization)
         ─▶ interleaved float32  →  NAME_NORAD_UTC_FREQ.cf32  (+ .json / .sigmf-meta)

Design notes
------------
* Numpy-only (no scipy) so it carries no new dependency for the sim package.
* Noise is added as complex circular Gaussian **across the whole output band**,
  exactly like a real SDR: a narrowband signal sits in a wide, flat noise
  floor. ``snr_db`` is defined as the in-band SNR (signal power over the noise
  power within the occupied bandwidth), which is how the real sidecars quote
  SNR — making sim and real directly comparable.
* Every impairment is recorded in the sidecar's ``simulation`` block, so a
  recording is fully reproducible and self-documenting.
"""
from __future__ import annotations

import json
import logging
import math
import os
import struct
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from ..dsp.modulator import Modulator, BITS_PER_SYMBOL, MOD_NAMES
from ..ccsds.tm_frame import TMFrameBuilder
from ..ccsds.asm import attach_asm

logger = logging.getLogger(__name__)

_NAME_TO_MOD = {v.upper(): k for k, v in MOD_NAMES.items()}
_NAME_TO_MOD.update({"PSK": 0, "2PSK": 0, "4PSK": 1, "FSK": 7, "AFSK": 7})


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
@dataclass
class ImpairmentConfig:
    """Real-world SDR / channel impairments applied to the clean baseband."""
    # --- carrier / Doppler ---
    carrier_offset_hz: float = -60000.0     # static offset of signal from DC
    doppler_model: str = "leo_scurve"       # "leo_scurve" | "constant" | "none"
    doppler_peak_hz: float = 9500.0         # ±peak Doppler over the pass (UHF≈±9.5k)
    doppler_tca_frac: float = 0.5           # fractional time of closest approach
    doppler_constant_hz: float = 0.0        # used when doppler_model == "constant"

    # --- amplitude vs time (pass geometry) ---
    envelope_model: str = "elevation_hump"  # "elevation_hump" | "flat"
    envelope_depth_db: float = 12.0         # AOS/LOS depth below TCA peak
    envelope_tca_frac: float = 0.5

    # --- burstiness (e.g. packetised beacons) ---
    burst_enabled: bool = False
    burst_period_s: float = 1.0
    burst_duty: float = 0.3

    # --- oscillator / receiver artefacts ---
    phase_noise_rad: float = 0.02           # per-sample random-walk phase std
    iq_gain_imbalance_db: float = 0.3       # I/Q gain mismatch
    iq_phase_imbalance_deg: float = 1.5     # I/Q quadrature error
    dc_offset: float = 0.01                 # additive DC (fraction of rms)

    # --- residual Doppler (after imperfect ground-station correction) ---
    residual_doppler_hz: float = 0.0        # constant leftover offset
    residual_doppler_jitter_hz: float = 0.0  # AFC/PLL tracking jitter (rms)

    # --- noise / quantization ---
    snr_db: float = 15.0                    # in-band SNR (signal / in-band noise)
    sky_noise_fraction: float = 0.0         # fraction of noise power that is colored (galactic)
    sky_noise_slope: float = 2.5            # colored-noise PSD ∝ f^-slope (VHF galactic ≈ 2.5–2.8)
    impulsive_prob: float = 0.0             # per-sample probability of an impulsive spike
    impulsive_gain: float = 8.0             # impulse amplitude (× noise std)
    quantization_bits: int = 8             # 0 = float (no quantization); 8 = RTL-SDR
    clip_level: float = 0.9                 # peak scaling before quantization

    # --- oscillator phase noise (power-law PSD; alternative to random-walk) ---
    phase_noise_model: str = "randwalk"     # "randwalk" | "powerlaw" | "none"
    phase_noise_level_dbc_1k: float = -80.0  # dBc/Hz at 1 kHz offset (powerlaw)
    phase_noise_floor_dbc: float = -140.0   # far-out phase-noise floor (dBc/Hz)

    # --- multipath / fading (clear-sky LEO ≈ high-K Ricean → AWGN) ---
    multipath_enabled: bool = False
    ricean_k_db: float = 12.0               # LOS/diffuse power ratio (high = near-LOS)
    multipath_delay_us: float = 0.0         # echo delay; 0 = flat fading
    fading_rate_hz: float = 0.0             # slow fading rate (0 = static)

    # --- interference ---
    cw_interferers: tuple = ()              # tuple of (freq_offset_hz, power_db_rel_signal)
    broadband_rfi_db: float = -999.0        # extra wideband noise raise (dB rel signal); -999 = off

    # --- receiver front-end ---
    band_edge_filter: bool = False          # keep only the central 4/5 of the band (RTL-SDR roll-off)
    sample_clock_ppm: float = 0.0           # sample-clock error (time scaling)


@dataclass
class RecorderConfig:
    """Top-level recorder configuration."""
    # signal
    modulation: object = "BPSK"             # int index or name (BPSK/QPSK/GMSK/GFSK…)
    symbol_rate_hz: float = 9600.0
    rolloff: float = 0.35                    # RRC rolloff (PSK/APSK)
    gmsk_bt: float = 0.5                     # Gaussian BT (GMSK/GFSK)
    sps_internal: int = 8                   # oversampling used by the modulator

    # output
    sample_rate_hz: float = 250000.0        # matches the RTL-SDR dataset
    duration_s: float = 30.0

    # data source: "ccsds" (framed idle/random) | "random"
    data_source: str = "ccsds"
    frame_length: int = 223                 # TM frame length (bytes, pre-ASM)
    scid: int = 1
    random_seed: int = 0

    # provenance (for the sidecar / filename)
    satellite: str = "EOSAT1-SIM"
    norad: int = 0
    nominal_downlink_hz: float = 401_415_000.0
    mode_label: str = ""                    # free-text mode (defaults to modulation)

    impairments: ImpairmentConfig = field(default_factory=ImpairmentConfig)

    def mod_index(self) -> int:
        if isinstance(self.modulation, int):
            return self.modulation
        return _NAME_TO_MOD.get(str(self.modulation).upper(), 0)


# --------------------------------------------------------------------------- #
#  Numpy-only FFT resampler (scipy.signal.resample equivalent)
# --------------------------------------------------------------------------- #
def fft_resample(x: np.ndarray, num_out: int) -> np.ndarray:
    """Band-limited resample of a complex signal to ``num_out`` samples via FFT.

    Equivalent to ``scipy.signal.resample`` for complex input, numpy-only.
    """
    n_in = len(x)
    if num_out == n_in:
        return x.astype(np.complex64)
    X = np.fft.fft(x)
    if num_out > n_in:                       # upsample: zero-pad the spectrum
        Y = np.zeros(num_out, dtype=complex)
        half = n_in // 2
        Y[:half + 1] = X[:half + 1]
        Y[-(n_in - half - 1):] = X[half + 1:]
        if n_in % 2 == 0:                    # split Nyquist energy
            Y[half] *= 0.5
            Y[half + num_out - n_in] = Y[half]
    else:                                    # downsample: truncate the spectrum
        Y = np.zeros(num_out, dtype=complex)
        half = num_out // 2
        Y[:half + 1] = X[:half + 1]
        Y[-(num_out - half - 1):] = X[-(num_out - half - 1):]
    y = np.fft.ifft(Y) * (num_out / n_in)
    return y.astype(np.complex64)


# --------------------------------------------------------------------------- #
#  Recorder
# --------------------------------------------------------------------------- #
class BasebandRecorder:
    """Builds a realistic simulated baseband recording and writes it to disk."""

    def __init__(self, config: RecorderConfig):
        self.cfg = config
        self.imp = config.impairments
        self._rng = np.random.default_rng(config.random_seed)
        self._diag: dict = {}

    # ---- data ------------------------------------------------------------- #
    def _make_bytes(self, n_bits_needed: int) -> bytes:
        n_bytes = n_bits_needed // 8 + 16
        if self.cfg.data_source == "random":
            return self._rng.integers(0, 256, n_bytes, dtype=np.uint8).tobytes()
        # CCSDS: stream of ASM + TM frames with random data zones
        builder = TMFrameBuilder(scid=self.cfg.scid,
                                 frame_length=self.cfg.frame_length,
                                 fecf_present=True)
        out = bytearray()
        while len(out) < n_bytes:
            payload = self._rng.integers(0, 256, self.cfg.frame_length, dtype=np.uint8).tobytes()
            for frame in builder.add_packet(payload, vcid=0):
                raw = frame.header.pack() + frame.data
                if frame.fecf is not None:
                    raw += struct.pack(">H", frame.fecf)
                out += attach_asm(raw)
            if not out:                       # frame_length larger than one packet
                idle = builder.build_idle_frame()
                raw = idle.header.pack() + idle.data
                out += attach_asm(raw)
        return bytes(out)

    # ---- clean modulated baseband ---------------------------------------- #
    def _modulate(self) -> np.ndarray:
        cfg = self.cfg
        mod = Modulator(modulation=cfg.mod_index(), sps=cfg.sps_internal,
                        rolloff=cfg.rolloff, gmsk_bt=cfg.gmsk_bt)
        bps = BITS_PER_SYMBOL.get(cfg.mod_index(), 1)
        n_symbols = int(math.ceil(cfg.duration_s * cfg.symbol_rate_hz))
        n_bits = n_symbols * bps
        data = self._make_bytes(n_bits)
        base = mod.modulate(data)             # at symbol_rate * sps_internal
        # trim/pad to exact duration at the internal rate
        n_int = int(round(cfg.duration_s * cfg.symbol_rate_hz * cfg.sps_internal))
        if len(base) >= n_int:
            base = base[:n_int]
        else:
            base = np.concatenate([base, np.zeros(n_int - len(base), np.complex64)])
        # resample internal_rate -> output sample_rate
        n_out = int(round(cfg.duration_s * cfg.sample_rate_hz))
        y = fft_resample(base, n_out)
        return _normalize_power(y)

    # ---- impairment stages ----------------------------------------------- #
    def _apply_envelope_and_bursts(self, y: np.ndarray, t: np.ndarray) -> np.ndarray:
        imp = self.imp
        env = np.ones(len(y))
        if imp.envelope_model == "elevation_hump":
            # slant-range hump: parabolic-in-dB, peak at TCA
            frac = t / t[-1]
            shape = 1.0 - ((frac - imp.envelope_tca_frac) /
                           max(imp.envelope_tca_frac, 1 - imp.envelope_tca_frac)) ** 2
            shape = np.clip(shape, 0, 1)
            env *= 10 ** ((imp.envelope_depth_db * (shape - 1)) / 20.0)
        if imp.burst_enabled:
            phase = (t % imp.burst_period_s) / imp.burst_period_s
            env *= (phase < imp.burst_duty).astype(float)
        self._diag["envelope_span_db"] = float(
            20 * np.log10((env.max() + 1e-12) / (env[env > 0].min() + 1e-12))
            if np.any(env > 0) else 0.0)
        return y * env.astype(np.complex64)

    def _apply_phase_noise(self, y: np.ndarray) -> np.ndarray:
        if self.imp.phase_noise_rad <= 0:
            return y
        walk = np.cumsum(self._rng.normal(0, self.imp.phase_noise_rad, len(y)))
        return (y * np.exp(1j * walk)).astype(np.complex64)

    def _apply_doppler(self, y: np.ndarray, t: np.ndarray) -> np.ndarray:
        imp = self.imp
        f = np.full(len(y), imp.carrier_offset_hz, dtype=float)
        if imp.doppler_model == "leo_scurve":
            # classic S-curve: +peak at AOS, 0 at TCA, -peak at LOS (arctan shape)
            frac = (t / t[-1] - imp.doppler_tca_frac)
            k = 8.0
            f += imp.doppler_peak_hz * (-np.tanh(k * frac))
            self._diag["doppler_span_hz"] = float(2 * imp.doppler_peak_hz)
        elif imp.doppler_model == "constant":
            f += imp.doppler_constant_hz
            self._diag["doppler_span_hz"] = 0.0
        # residual Doppler after imperfect ground-station correction
        if imp.residual_doppler_hz:
            f += imp.residual_doppler_hz
        if imp.residual_doppler_jitter_hz:
            f += self._rng.normal(0, imp.residual_doppler_jitter_hz, len(y))
        phase = 2 * np.pi * np.cumsum(f) / self.cfg.sample_rate_hz
        return (y * np.exp(1j * phase)).astype(np.complex64)

    # ---- power-law phase noise (shaped, not random-walk) ------------------ #
    def _apply_powerlaw_phase_noise(self, y: np.ndarray) -> np.ndarray:
        """Phase noise with a realistic multi-slope PSD.

        Synthesised by shaping white noise in the frequency domain to an
        f^-1 (flicker) skirt anchored at ``phase_noise_level_dbc_1k`` plus a
        flat far-out floor, rather than the pure random-walk (f^-4) integrator.
        """
        imp = self.imp
        fs = self.cfg.sample_rate_hz
        n = len(y)
        f = np.fft.rfftfreq(n, 1 / fs)
        fmin = fs / n
        f[0] = fmin
        # single-sideband phase-noise PSD (rad^2/Hz): flicker (1/f) skirt + floor
        L_1k = 10 ** (imp.phase_noise_level_dbc_1k / 10.0)      # linear at 1 kHz
        L_floor = 10 ** (imp.phase_noise_floor_dbc / 10.0)
        Sphi = L_1k * (1000.0 / f) + L_floor                    # rad^2/Hz
        # shape unit-variance white noise to the PSD, then rescale to the
        # analytically-integrated total phase variance (fixes absolute level)
        white = (self._rng.normal(size=len(f)) + 1j * self._rng.normal(size=len(f)))
        spectrum = white * np.sqrt(Sphi)
        spectrum[0] = 0.0
        phi = np.fft.irfft(spectrum, n=n)
        fmax = fs / 2.0
        target_var = L_1k * 1000.0 * math.log(fmax / fmin) + L_floor * fmax
        cur = np.std(phi)
        if cur > 0:
            phi = phi * (math.sqrt(target_var) / cur)
        phi = phi - np.mean(phi)
        self._diag["phase_noise_rms_rad"] = float(np.std(phi))
        return (y * np.exp(1j * phi)).astype(np.complex64)

    # ---- multipath / Ricean fading --------------------------------------- #
    def _apply_multipath(self, y: np.ndarray) -> np.ndarray:
        imp = self.imp
        if not imp.multipath_enabled:
            return y
        fs = self.cfg.sample_rate_hz
        k = 10 ** (imp.ricean_k_db / 10.0)
        los = math.sqrt(k / (k + 1))
        dif = math.sqrt(1 / (k + 1))
        # diffuse component: slowly-varying complex gain (flat fading)
        n = len(y)
        if imp.fading_rate_hz > 0:
            t = np.arange(n) / fs
            # sum of a few random sinusoids → band-limited diffuse gain
            g = np.zeros(n, dtype=complex)
            for _ in range(6):
                fd = imp.fading_rate_hz * self._rng.uniform(-1, 1)
                ph = self._rng.uniform(0, 2 * np.pi)
                g += np.exp(1j * (2 * np.pi * fd * t + ph))
            g /= math.sqrt(6)
        else:
            g = np.full(n, self._rng.normal() + 1j * self._rng.normal(), complex) / math.sqrt(2)
        faded = los * y + dif * (g * y)
        # optional discrete echo (frequency-selective) if a delay is given
        if imp.multipath_delay_us > 0:
            d = int(round(imp.multipath_delay_us * 1e-6 * fs))
            if d > 0:
                echo = np.zeros_like(faded)
                echo[d:] = dif * faded[:-d]
                faded = los * faded + echo
        self._diag["ricean_k_db"] = float(imp.ricean_k_db)
        return _normalize_power(faded).astype(np.complex64)

    # ---- interference (CW tones, broadband RFI) -------------------------- #
    def _add_interference(self, y: np.ndarray) -> np.ndarray:
        imp = self.imp
        fs = self.cfg.sample_rate_hz
        n = len(y)
        sig_power = float(np.mean(np.abs(y) ** 2))
        out = y.copy()
        for foff, pdb in imp.cw_interferers:
            amp = math.sqrt(sig_power * 10 ** (pdb / 10.0)) * math.sqrt(2)
            ph = self._rng.uniform(0, 2 * np.pi)
            t = np.arange(n) / fs
            out = out + (amp * np.exp(1j * (2 * np.pi * foff * t + ph))).astype(np.complex64)
        if imp.broadband_rfi_db > -900:
            rfi_var = sig_power * 10 ** (imp.broadband_rfi_db / 10.0)
            std = math.sqrt(rfi_var / 2)
            out = out + (self._rng.normal(0, std, n) +
                         1j * self._rng.normal(0, std, n)).astype(np.complex64)
            self._diag["broadband_rfi_db"] = float(imp.broadband_rfi_db)
        if imp.cw_interferers:
            self._diag["cw_interferers"] = list(imp.cw_interferers)
        return out

    # ---- receiver front-end: band-edge roll-off + clock ppm -------------- #
    def _apply_frontend(self, y: np.ndarray) -> np.ndarray:
        imp = self.imp
        if imp.band_edge_filter:
            Y = np.fft.fft(y)
            n = len(Y)
            keep = int(0.4 * n)                 # central ~4/5 of the band
            Y[keep:n - keep] = 0
            y = np.fft.ifft(Y).astype(np.complex64)
        if imp.sample_clock_ppm:
            factor = 1.0 + imp.sample_clock_ppm * 1e-6
            n_new = int(round(len(y) * factor))
            y = fft_resample(y, n_new)[:len(y)] if n_new >= len(y) else \
                np.concatenate([fft_resample(y, n_new),
                                np.zeros(len(y) - n_new, np.complex64)])
        return y

    def _apply_iq_imbalance(self, y: np.ndarray) -> np.ndarray:
        imp = self.imp
        if imp.iq_gain_imbalance_db == 0 and imp.iq_phase_imbalance_deg == 0:
            return y
        g = 10 ** (imp.iq_gain_imbalance_db / 20.0)
        ph = math.radians(imp.iq_phase_imbalance_deg)
        i = y.real
        q = y.imag
        # apply gain to I, gain+phase skew to Q
        i2 = i
        q2 = g * (q * math.cos(ph) + i * math.sin(ph))
        return (i2 + 1j * q2).astype(np.complex64)

    def _add_noise(self, y: np.ndarray) -> np.ndarray:
        """Add complex circular AWGN across the whole band for a target in-band SNR."""
        imp = self.imp
        cfg = self.cfg
        sig_power = float(np.mean(np.abs(y) ** 2))
        occ_bw = cfg.symbol_rate_hz * (1 + cfg.rolloff)
        occ_bw = min(occ_bw, cfg.sample_rate_hz)
        snr_lin = 10 ** (imp.snr_db / 10.0)
        # in-band signal power / (noise psd * occ_bw) = snr  →  noise psd
        noise_psd = sig_power / (snr_lin * occ_bw)
        noise_var = noise_psd * cfg.sample_rate_hz       # total noise power over band
        n = len(y)
        # split noise power between flat (receiver thermal) and colored (galactic sky)
        frac = float(np.clip(imp.sky_noise_fraction, 0, 1))
        white_var = noise_var * (1 - frac)
        colored_var = noise_var * frac
        std = math.sqrt(white_var / 2.0)
        noise = (self._rng.normal(0, std, n) +
                 1j * self._rng.normal(0, std, n)).astype(np.complex64)
        if colored_var > 0:
            noise = noise + _colored_noise(n, cfg.sample_rate_hz, imp.sky_noise_slope,
                                           colored_var, self._rng)
            self._diag["sky_noise_fraction"] = frac
            self._diag["sky_noise_slope"] = float(imp.sky_noise_slope)
        # impulsive (man-made) noise: sparse heavy-tailed spikes
        if imp.impulsive_prob > 0:
            mask = self._rng.random(n) < imp.impulsive_prob
            spikes = (self._rng.normal(0, std, n) + 1j * self._rng.normal(0, std, n))
            noise = noise + (mask * imp.impulsive_gain * spikes).astype(np.complex64)
            self._diag["impulsive_prob"] = float(imp.impulsive_prob)
        self._diag["wideband_snr_db"] = float(
            10 * np.log10(sig_power / (noise_var + 1e-20)))
        self._diag["inband_snr_db_target"] = float(imp.snr_db)
        return y + noise

    def _quantize_and_scale(self, y: np.ndarray) -> np.ndarray:
        imp = self.imp
        peak = np.max(np.abs(np.concatenate([y.real, y.imag]))) + 1e-12
        y = y * (imp.clip_level / peak)
        if imp.dc_offset:
            rms = math.sqrt(np.mean(np.abs(y) ** 2))
            y = y + imp.dc_offset * rms * (1 + 1j)
        y = np.clip(y.real, -1, 1) + 1j * np.clip(y.imag, -1, 1)
        if imp.quantization_bits and imp.quantization_bits > 0:
            levels = 2 ** imp.quantization_bits
            step = 2.0 / levels
            y = np.round(y.real / step) * step + 1j * np.round(y.imag / step) * step
        return y.astype(np.complex64)

    # ---- top-level -------------------------------------------------------- #
    def generate(self) -> np.ndarray:
        """Return the full impaired baseband as complex64 (interleaved on write)."""
        y = self._modulate()
        t = np.arange(len(y)) / self.cfg.sample_rate_hz
        y = self._apply_envelope_and_bursts(y, t)
        y = self._apply_multipath(y)
        # phase noise: power-law PSD (realistic) or legacy random-walk
        if self.imp.phase_noise_model == "powerlaw":
            y = self._apply_powerlaw_phase_noise(y)
        elif self.imp.phase_noise_model == "randwalk":
            y = self._apply_phase_noise(y)
        y = self._apply_doppler(y, t)
        y = self._apply_iq_imbalance(y)
        y = self._add_interference(y)
        y = self._add_noise(y)
        y = self._apply_frontend(y)
        y = self._quantize_and_scale(y)
        return y

    def sidecar(self) -> dict:
        """Build the JSON sidecar (satscanner-compatible + simulation block)."""
        cfg = self.cfg
        now = datetime.now(timezone.utc).isoformat()
        sdr_center = cfg.nominal_downlink_hz - cfg.impairments.carrier_offset_hz
        mode = cfg.mode_label or MOD_NAMES.get(cfg.mod_index(), "BPSK")
        return {
            "satellite": cfg.satellite,
            "norad": cfg.norad,
            "mode": mode,
            "transmitter": f"SIMULATED {mode} {cfg.symbol_rate_hz:.0f} Bd",
            "nominal_downlink_hz": cfg.nominal_downlink_hz,
            "sdr_center_freq_hz": sdr_center,
            "sample_rate_hz": cfg.sample_rate_hz,
            "iq_format": "complex64 (interleaved float32 I,Q), little-endian",
            "recorded_seconds": cfg.duration_s,
            "signal_confirmed": True,
            "recorded_at_utc": now,
            "source": "SIMULATED (smo-rfsim BasebandRecorder)",
            "simulation": {
                "modulation": MOD_NAMES.get(cfg.mod_index(), "BPSK"),
                "modulation_index": cfg.mod_index(),
                "symbol_rate_hz": cfg.symbol_rate_hz,
                "sps_internal": cfg.sps_internal,
                "rolloff": cfg.rolloff,
                "gmsk_bt": cfg.gmsk_bt,
                "data_source": cfg.data_source,
                "frame_length": cfg.frame_length,
                "random_seed": cfg.random_seed,
                "impairments": asdict(cfg.impairments),
                "diagnostics": self._diag,
            },
        }

    def record(self, out_dir: str, basename: Optional[str] = None) -> dict:
        """Generate + write .cf32 and .json. Returns a manifest dict."""
        os.makedirs(out_dir, exist_ok=True)
        y = self.generate()
        cfg = self.cfg
        if basename is None:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            freq_mhz = cfg.nominal_downlink_hz / 1e6
            safe = str(cfg.satellite).replace(" ", "_")
            basename = f"{safe}_{cfg.norad}_{ts}_{freq_mhz:.4f}MHz"
        cf32_path = os.path.join(out_dir, basename + ".cf32")
        json_path = os.path.join(out_dir, basename + ".json")
        sig_path = os.path.join(out_dir, basename + ".sigmf-meta")
        write_cf32(cf32_path, y)
        sc = self.sidecar()
        with open(json_path, "w") as f:
            json.dump(sc, f, indent=2)
        with open(sig_path, "w") as f:
            json.dump(_sigmf_meta(cfg), f, indent=2)
        logger.info("Wrote %s (%d samples, %.1f s)", cf32_path, len(y), cfg.duration_s)
        return {"cf32": cf32_path, "json": json_path, "sigmf": sig_path,
                "n_samples": len(y), "sidecar": sc}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def _normalize_power(x: np.ndarray) -> np.ndarray:
    p = np.mean(np.abs(x) ** 2)
    return (x / math.sqrt(p)).astype(np.complex64) if p > 0 else x.astype(np.complex64)


def _colored_noise(n: int, fs: float, slope: float, power: float,
                   rng: np.random.Generator) -> np.ndarray:
    """Complex noise with a gently colored (non-white) PSD, scaled to ``power``.

    Physical note: the galactic/sky f^-2.5 color spans *octaves of absolute RF*;
    across a single ~250 kHz baseband it is essentially flat and only sets the
    noise *level*. What is actually observable within one capture is the
    receiver/front-end **band shape**. This function therefore models a bounded
    band color: a PSD ∝ |f|^-slope but clipped near DC (no DC delta) so it
    produces a measurable few-dB tilt/dome across the band rather than a spike.
    """
    f = np.fft.fftfreq(n, 1 / fs)
    fa = np.abs(f)
    f_ref = fs * 0.05                              # clip the rise below ~5% of band
    fa = np.clip(fa, f_ref, None)
    shape = fa ** (-slope / 2.0)                   # amplitude shaping (PSD ∝ shape^2)
    shape = shape / shape.max()
    white = rng.normal(size=n) + 1j * rng.normal(size=n)
    z = np.fft.ifft(np.fft.fft(white) * shape)
    z = z / math.sqrt(np.mean(np.abs(z) ** 2))     # unit power
    return (z * math.sqrt(power)).astype(np.complex64)


def write_cf32(path: str, x: np.ndarray) -> None:
    """Write complex samples as interleaved little-endian float32 (I,Q,I,Q…)."""
    x = np.asarray(x, dtype=np.complex64)
    inter = np.empty(2 * len(x), dtype="<f4")
    inter[0::2] = x.real
    inter[1::2] = x.imag
    inter.tofile(path)


def _sigmf_meta(cfg: RecorderConfig) -> dict:
    sdr_center = cfg.nominal_downlink_hz - cfg.impairments.carrier_offset_hz
    return {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": cfg.sample_rate_hz,
            "core:version": "1.0.0",
            "core:description": f"Simulated {MOD_NAMES.get(cfg.mod_index(),'BPSK')} "
                                f"downlink ({cfg.satellite})",
            "core:recorder": "smo-rfsim BasebandRecorder",
        },
        "captures": [{
            "core:sample_start": 0,
            "core:frequency": sdr_center,
        }],
        "annotations": [{
            "core:sample_start": 0,
            "core:sample_count": int(cfg.duration_s * cfg.sample_rate_hz),
            "core:freq_lower_edge": cfg.nominal_downlink_hz
            - cfg.symbol_rate_hz * (1 + cfg.rolloff) / 2,
            "core:freq_upper_edge": cfg.nominal_downlink_hz
            + cfg.symbol_rate_hz * (1 + cfg.rolloff) / 2,
            "core:label": MOD_NAMES.get(cfg.mod_index(), "BPSK"),
        }],
    }
