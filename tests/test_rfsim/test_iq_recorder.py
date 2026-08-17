"""Tests for the baseband I/Q recorder (smo_rfsim.recording).

Verify that the recorder produces a well-formed interleaved-float32 .cf32 file
with a matching sidecar, and that the requested impairments actually appear in
the output (carrier offset, wideband noise floor, DC spike, quantization).
"""
import json
import math

import numpy as np
import pytest

from smo_rfsim.recording import BasebandRecorder, RecorderConfig, ImpairmentConfig
from smo_rfsim.recording.iq_recorder import fft_resample


def _load_cf32(path):
    inter = np.fromfile(path, dtype="<f4")
    return inter[0::2] + 1j * inter[1::2]


def test_fft_resample_length_and_bandlimit():
    fs_in, fs_out = 1000, 250
    t = np.arange(fs_in) / fs_in
    x = np.exp(2j * np.pi * 50 * t)          # 50 Hz tone, well below fs_out/2
    y = fft_resample(x.astype(np.complex64), fs_out)
    assert len(y) == fs_out
    # tone should survive resampling (peak near 50 Hz)
    f = np.fft.fftshift(np.fft.fftfreq(fs_out, 1 / fs_out))
    Y = np.fft.fftshift(np.abs(np.fft.fft(y)))
    assert abs(f[np.argmax(Y)] - 50) < 5


def test_recorder_writes_cf32_and_sidecar(tmp_path):
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=4800, duration_s=2.0,
                         sample_rate_hz=100000, random_seed=1)
    rec = BasebandRecorder(cfg)
    man = rec.record(str(tmp_path), basename="unit")
    x = _load_cf32(man["cf32"])
    # exact interleaved length == duration * fs
    assert len(x) == pytest.approx(cfg.duration_s * cfg.sample_rate_hz, rel=1e-3)
    assert x.dtype == np.complex64 or np.iscomplexobj(x)
    sc = json.load(open(man["json"]))
    assert sc["sample_rate_hz"] == cfg.sample_rate_hz
    assert sc["iq_format"].startswith("complex64")
    assert "simulation" in sc and "impairments" in sc["simulation"]
    # values are within RTL-SDR range [-1, 1]
    assert np.max(np.abs(x.real)) <= 1.0 and np.max(np.abs(x.imag)) <= 1.0


def test_carrier_offset_places_signal_off_dc(tmp_path):
    off = -30000.0
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=2400, duration_s=2.0,
                         sample_rate_hz=200000, random_seed=2,
                         impairments=ImpairmentConfig(
                             carrier_offset_hz=off, doppler_model="constant",
                             doppler_constant_hz=0.0, envelope_model="flat",
                             snr_db=25, dc_offset=0.0, quantization_bits=0,
                             phase_noise_rad=0.0, iq_gain_imbalance_db=0.0,
                             iq_phase_imbalance_deg=0.0))
    x = _load_cf32(BasebandRecorder(cfg).record(str(tmp_path), "off")["cf32"])
    f = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / cfg.sample_rate_hz))
    P = np.fft.fftshift(np.abs(np.fft.fft(x * np.hanning(len(x)))) ** 2)
    peak_f = f[np.argmax(P)]
    assert abs(peak_f - off) < 3000       # carrier lands near the requested offset


def test_quantization_produces_discrete_levels(tmp_path):
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=2400, duration_s=1.0,
                         sample_rate_hz=100000, random_seed=3,
                         impairments=ImpairmentConfig(quantization_bits=8))
    x = _load_cf32(BasebandRecorder(cfg).record(str(tmp_path), "q")["cf32"])
    # 8-bit → at most 256 distinct I values
    assert len(np.unique(x.real)) <= 256


def test_snr_is_in_the_right_ballpark(tmp_path):
    target = 20.0
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=9600, duration_s=3.0,
                         sample_rate_hz=250000, random_seed=4,
                         impairments=ImpairmentConfig(
                             snr_db=target, carrier_offset_hz=0.0,
                             doppler_model="none", envelope_model="flat",
                             dc_offset=0.0, quantization_bits=0,
                             phase_noise_rad=0.0, iq_gain_imbalance_db=0.0,
                             iq_phase_imbalance_deg=0.0))
    x = _load_cf32(BasebandRecorder(cfg).record(str(tmp_path), "snr")["cf32"])
    # measure in-band (|f| < occ_bw/2) vs out-of-band noise power
    f = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / cfg.sample_rate_hz))
    P = np.fft.fftshift(np.abs(np.fft.fft(x)) ** 2) / len(x)
    occ = 9600 * 1.35
    inb = np.abs(f) < occ / 2
    floor = np.median(P[~inb])
    sig = np.sum(np.clip(P[inb] - floor, 0, None))
    noise = floor * inb.sum()
    snr = 10 * math.log10(sig / noise)
    assert abs(snr - target) < 6.0        # loose: estimator + windowing tolerance


def _noise_floor_slope(x, fs):
    """Fit noise-floor PSD (dB) vs log10|f| over the signal-free bins."""
    f, pxx = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / fs)), None
    from numpy.fft import fft, fftshift
    P = fftshift(np.abs(fft(x)) ** 2) / len(x)
    pdb = 10 * np.log10(P + 1e-20)
    sm = np.convolve(pdb, np.ones(201) / 201, mode="same")
    floor = np.percentile(sm, 30)
    nb = (sm < floor + 6) & (np.abs(f) > 8000)
    lf, pv = np.log10(np.abs(f[nb])), pdb[nb]
    A = np.vstack([lf, np.ones_like(lf)]).T
    return np.linalg.lstsq(A, pv, rcond=None)[0][0]


def test_colored_noise_produces_slope(tmp_path):
    from smo_rfsim.recording import ImpairmentConfig
    common = dict(modulation="BPSK", symbol_rate_hz=9600, duration_s=3.0,
                  sample_rate_hz=250000, random_seed=11)
    base_imp = dict(carrier_offset_hz=-60000.0, doppler_model="none",
                    envelope_model="flat", dc_offset=0.0, quantization_bits=0,
                    phase_noise_model="none", iq_gain_imbalance_db=0.0,
                    iq_phase_imbalance_deg=0.0, snr_db=12)
    white = _load_cf32(BasebandRecorder(RecorderConfig(
        impairments=ImpairmentConfig(sky_noise_fraction=0.0, **base_imp), **common
    )).record(str(tmp_path), "w")["cf32"])
    colored = _load_cf32(BasebandRecorder(RecorderConfig(
        impairments=ImpairmentConfig(sky_noise_fraction=0.9, sky_noise_slope=2.6, **base_imp),
        **common)).record(str(tmp_path), "c")["cf32"])
    s_white = _noise_floor_slope(white, 250000)
    s_colored = _noise_floor_slope(colored, 250000)
    assert abs(s_white) < 2.0                 # white floor ≈ flat
    assert s_colored < -3.0                   # colored floor slopes down with |f|


def test_impulsive_noise_raises_kurtosis(tmp_path):
    # scipy is an optional extra, not a hard dependency of smo-rfsim. Skip
    # rather than fail on an install that does not carry it; the rest of the
    # recorder suite runs without it.
    pytest.importorskip("scipy")
    from scipy.stats import kurtosis
    from smo_rfsim.recording import ImpairmentConfig
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=9600, duration_s=3.0,
                         sample_rate_hz=250000, random_seed=12,
                         impairments=ImpairmentConfig(
                             carrier_offset_hz=-60000.0, doppler_model="none",
                             envelope_model="flat", dc_offset=0.0, quantization_bits=0,
                             phase_noise_model="none", snr_db=12,
                             impulsive_prob=0.002, impulsive_gain=10.0))
    x = _load_cf32(BasebandRecorder(cfg).record(str(tmp_path), "imp")["cf32"])
    assert kurtosis(x.real, fisher=True) > 1.0   # heavy-tailed vs Gaussian ≈ 0


def test_cw_interferer_appears(tmp_path):
    from smo_rfsim.recording import ImpairmentConfig
    cfg = RecorderConfig(modulation="BPSK", symbol_rate_hz=9600, duration_s=2.0,
                         sample_rate_hz=250000, random_seed=13,
                         impairments=ImpairmentConfig(
                             carrier_offset_hz=-60000.0, doppler_model="none",
                             envelope_model="flat", dc_offset=0.0, quantization_bits=0,
                             phase_noise_model="none", snr_db=15,
                             cw_interferers=((40000.0, -6.0),)))
    x = _load_cf32(BasebandRecorder(cfg).record(str(tmp_path), "cw")["cf32"])
    f = np.fft.fftshift(np.fft.fftfreq(len(x), 1 / 250000))
    P = np.fft.fftshift(np.abs(np.fft.fft(x)) ** 2)
    # a sharp line near +40 kHz
    near = np.abs(f - 40000) < 500
    assert P[near].max() > 20 * np.median(P)
