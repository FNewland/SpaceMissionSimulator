"""SatHack-derived channel impairments (roadmap X-15).

Ports of channel effects mined from Franz's SatHack toolkit into the
``SpaceLinkChannel`` impairment registry:

* ``doppler_ramp``     — realistic LEO Doppler S-curve (rfchain/doppler.py + sim.py)
* ``oscillator_drift`` — slow CFO with drift/aging/walk (rfchain/sim.py add_cfo)
* ``iq_imbalance``     — quadrature imbalance image tone (sathack config.yaml tuning)
* ``dc_offset``        — RTL centre birdie (sathack/cf32_source.py remove_dc)

Each test asserts the impairment (a) changes the signal in the physically
expected way, (b) is deterministic under a fixed seed, (c) is a no-op when
neutralised/disabled, and the end-to-end pipeline still decodes with a mild
mix enabled.
"""

import numpy as np
import pytest

from smo_rfsim.channel.space_link import (
    SpaceLinkChannel, SpaceLinkConfig, available_impairments, build_impairment,
)
from smo_rfsim.channel.sathack_impairments import (
    DopplerRampImpairment, OscillatorDriftImpairment,
    IQImbalanceImpairment, DCOffsetImpairment,
)
from smo_rfsim.dsp.modulator import Modulator, BITS_PER_SYMBOL
from smo_rfsim.dsp.correlator_rx import CorrelatorRX


FS = 256000.0
N = 8192


def _tone(f_hz: float, n: int = N, fs: float = FS) -> np.ndarray:
    t = np.arange(n) / fs
    return np.exp(1j * 2 * np.pi * f_hz * t).astype(np.complex64)


def _peak_hz(y: np.ndarray, fs: float = FS) -> float:
    spec = np.abs(np.fft.fftshift(np.fft.fft(y)))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / fs))
    return float(freqs[int(np.argmax(spec))])


def _cfg(**kw) -> SpaceLinkConfig:
    base = dict(sample_rate=FS, frequency_hz=2200.0e6, seed=42,
                awgn_enabled=False)
    base.update(kw)
    return SpaceLinkConfig(**base)


# ── registry ────────────────────────────────────────────────────────────────

class TestRegistration:
    def test_all_four_types_registered(self):
        for name in ("doppler_ramp", "oscillator_drift",
                     "iq_imbalance", "dc_offset"):
            assert name in available_impairments()

    def test_selectable_via_interferers_list(self):
        cfg = _cfg(interferers=[{"type": "dc_offset", "offset_i": 0.1}])
        ch = SpaceLinkChannel(cfg)
        assert ch.get_status()["n_interferers"] == 1


# ── Doppler ramp ─────────────────────────────────────────────────────────────

class TestDopplerRamp:
    def test_shifts_spectral_peak_by_commanded_profile(self):
        # A real LEO Doppler curve is gentle near closest approach and spans
        # the whole pass; place the (short) analysis window a long way before
        # t_ca so the commanded Doppler is large, saturated, and ~constant
        # over the window — then the FFT peak must land on it.
        imp = build_impairment(
            {"type": "doppler_ramp", "max_doppler_hz": 30000.0,
             "t_ca_s": 250.0, "pass_duration_s": 600.0}, _cfg())
        y = imp.apply(_tone(0.0))                     # window at t≈0, dt≈-250 s
        t_mid = (N / 2) / FS
        commanded = float(imp.instantaneous_doppler_hz(t_mid))
        assert abs(commanded) > 20000.0, "profile should be near peak far from t_ca"
        bin_hz = FS / N
        assert abs(_peak_hz(y) - commanded) < 3 * bin_hz, (
            f"peak {_peak_hz(y):.0f} Hz vs commanded {commanded:.0f} Hz")

    def test_doppler_ramps_through_zero_at_closest_approach(self):
        imp = DopplerRampImpairment(carrier_hz=2200e6, max_doppler_hz=25000.0,
                                    t_ca_s=250.0, sample_rate=FS)
        # Approaching before t_ca (positive), ~0 at t_ca, receding after (neg).
        assert imp.instantaneous_doppler_hz(0.0) > 5000.0
        assert abs(imp.instantaneous_doppler_hz(250.0)) < 1.0
        assert imp.instantaneous_doppler_hz(500.0) < -5000.0

    def test_peak_moves_across_the_pass(self):
        # The instantaneous frequency must change sign from before to after
        # closest approach (a ramp, not a fixed offset).
        imp = build_impairment(
            {"type": "doppler_ramp", "max_doppler_hz": 20000.0,
             "t_ca_s": 250.0, "pass_duration_s": 600.0}, _cfg())
        f_early = float(imp.instantaneous_doppler_hz(100.0))
        f_late = float(imp.instantaneous_doppler_hz(400.0))
        assert f_early > 0 > f_late, "Doppler must ramp through zero over a pass"

    def test_deterministic(self):
        spec = {"type": "doppler_ramp", "max_doppler_hz": 12000.0, "t_ca_s": 5.0}
        a = build_impairment(spec, _cfg()).apply(_tone(0.0))
        b = build_impairment(spec, _cfg()).apply(_tone(0.0))
        assert np.array_equal(a, b)

    def test_no_op_when_max_doppler_zero(self):
        imp = build_impairment(
            {"type": "doppler_ramp", "max_doppler_hz": 0.0}, _cfg())
        x = _tone(0.0)
        assert np.array_equal(imp.apply(x), x)


# ── oscillator drift ─────────────────────────────────────────────────────────

class TestOscillatorDrift:
    def test_constant_offset_lands_at_f0(self):
        imp = build_impairment(
            {"type": "oscillator_drift", "f0_hz": 8000.0,
             "drift_hz_s": 0.0, "walk_hz": 0.0}, _cfg())
        y = imp.apply(_tone(0.0))
        assert abs(_peak_hz(y) - 8000.0) < 2 * (FS / N)

    def test_drift_moves_the_frequency_over_time(self):
        imp = OscillatorDriftImpairment(f0_hz=0.0, drift_hz_s=200000.0,
                                        walk_hz=0.0, sample_rate=FS)
        first = _peak_hz(imp.apply(_tone(0.0)))      # near t=0 → ~0 Hz
        # advance several buffers, drift has accrued
        for _ in range(5):
            imp.apply(_tone(0.0))
        later = _peak_hz(imp.apply(_tone(0.0)))
        assert later > first + 500.0, (
            f"drift did not move the carrier ({first:.0f}→{later:.0f} Hz)")

    def test_random_walk_is_deterministic_under_seed(self):
        spec = {"type": "oscillator_drift", "f0_hz": 100.0,
                "drift_hz_s": 10.0, "walk_hz": 20.0}
        a = build_impairment(spec, _cfg(seed=99)).apply(_tone(0.0))
        b = build_impairment(spec, _cfg(seed=99)).apply(_tone(0.0))
        c = build_impairment(spec, _cfg(seed=1234)).apply(_tone(0.0))
        assert np.array_equal(a, b)
        assert not np.array_equal(a, c), "different seed must differ"

    def test_no_op_when_all_zero(self):
        imp = build_impairment(
            {"type": "oscillator_drift", "f0_hz": 0.0, "drift_hz_s": 0.0,
             "aging_hz_s2": 0.0, "walk_hz": 0.0}, _cfg())
        x = _tone(0.0)
        assert np.allclose(imp.apply(x), x, atol=1e-6)


# ── IQ imbalance ─────────────────────────────────────────────────────────────

class TestIQImbalance:
    def test_creates_image_tone_at_expected_level(self):
        f0 = 20000.0
        imp = build_impairment(
            {"type": "iq_imbalance", "gain_db": 1.0, "phase_deg": 5.0}, _cfg())
        y = imp.apply(_tone(f0))
        spec = np.abs(np.fft.fftshift(np.fft.fft(y)))
        freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / FS))
        main = spec.max()
        image = spec[int(np.argmin(np.abs(freqs + f0)))]   # look at -f0
        measured_db = 20 * np.log10(image / main)
        expected_db = -imp.image_rejection_db()
        assert abs(measured_db - expected_db) < 1.0, (
            f"image at {measured_db:.1f} dB, expected {expected_db:.1f} dB")

    def test_stronger_imbalance_raises_a_stronger_image(self):
        f0 = 15000.0
        weak = IQImbalanceImpairment(gain_db=0.2, phase_deg=1.0)
        strong = IQImbalanceImpairment(gain_db=2.0, phase_deg=8.0)
        assert strong.image_rejection_db() < weak.image_rejection_db()

    def test_deterministic(self):
        spec = {"type": "iq_imbalance", "gain_db": 0.8, "phase_deg": 3.0}
        x = _tone(10000.0)
        a = build_impairment(spec, _cfg()).apply(x)
        b = build_impairment(spec, _cfg()).apply(x)
        assert np.array_equal(a, b)

    def test_no_op_when_balanced(self):
        imp = build_impairment(
            {"type": "iq_imbalance", "gain_db": 0.0, "phase_deg": 0.0}, _cfg())
        x = _tone(12000.0)
        assert np.allclose(imp.apply(x), x, atol=1e-6)
        assert imp.image_rejection_db() == float("inf")


# ── DC offset ────────────────────────────────────────────────────────────────

class TestDCOffset:
    def test_adds_constant_bias(self):
        imp = build_impairment(
            {"type": "dc_offset", "offset_i": 0.1, "offset_q": -0.05}, _cfg())
        y = imp.apply(np.zeros(64, dtype=np.complex64))
        assert np.allclose(y.real, 0.1, atol=1e-5)
        assert np.allclose(y.imag, -0.05, atol=1e-5)

    def test_produces_a_dc_spike(self):
        # A constant I/Q bias is a spectral line at exactly 0 Hz of amplitude
        # |offset|·N; it must appear where there was no DC before.
        offset = 0.2
        x = _tone(20000.0)
        imp = build_impairment({"type": "dc_offset", "offset_i": offset}, _cfg())
        y = imp.apply(x)
        freqs = np.fft.fftshift(np.fft.fftfreq(len(y), 1.0 / FS))
        dc_bin = int(np.argmin(np.abs(freqs)))
        before = np.abs(np.fft.fftshift(np.fft.fft(x)))[dc_bin]
        after = np.abs(np.fft.fftshift(np.fft.fft(y)))[dc_bin]
        assert after > before + 0.5 * offset * len(y), "no DC spike appeared"
        assert after == pytest.approx(offset * len(y), rel=0.05)

    def test_deterministic(self):
        spec = {"type": "dc_offset", "offset_i": 0.07, "offset_q": 0.03}
        x = _tone(5000.0)
        a = build_impairment(spec, _cfg()).apply(x)
        b = build_impairment(spec, _cfg()).apply(x)
        assert np.array_equal(a, b)

    def test_no_op_when_zero(self):
        imp = build_impairment(
            {"type": "dc_offset", "offset_i": 0.0, "offset_q": 0.0}, _cfg())
        x = _tone(3000.0)
        assert np.array_equal(imp.apply(x), x)


# ── disabled = identity, and end-to-end decode ───────────────────────────────

class TestDisabledAndEndToEnd:
    def test_channel_is_identity_when_no_impairments_listed(self):
        # No interferers, no other impairments → the SatHack ports are absent.
        cfg = _cfg(interferers=[])
        ch = SpaceLinkChannel(cfg)
        x = _tone(4000.0)
        assert np.allclose(ch.process(x), x, atol=1e-6)

    def test_pipeline_still_decodes_with_mild_impairments(self):
        payload = bytes(np.random.default_rng(1234)
                        .integers(0, 256, 400, dtype=np.uint8))
        mod = 0  # BPSK
        m = Modulator(modulation=mod, sps=8, gray_coding=True)
        rx = CorrelatorRX(sps=8, modulation=mod, gray_coding=True)
        samples = m.modulate(payload)

        cfg = SpaceLinkConfig(
            awgn_enabled=True, eb_n0_db=15.0, sps=8,
            bits_per_symbol=BITS_PER_SYMBOL[mod],
            sample_rate=8 * 16000.0, frequency_hz=2200e6, seed=7,
            interferers=[
                {"type": "iq_imbalance", "gain_db": 0.4, "phase_deg": 1.5},
                {"type": "dc_offset", "offset_i": 0.02, "offset_q": 0.0},
                {"type": "doppler_ramp", "max_doppler_hz": 30.0,
                 "t_ca_s": 300.0, "pass_duration_s": 600.0},
                {"type": "oscillator_drift", "f0_hz": 10.0,
                 "drift_hz_s": 0.5, "walk_hz": 0.0},
            ])
        ch = SpaceLinkChannel(cfg)
        impaired = ch.process(np.asarray(samples, dtype=np.complex64))
        out = rx.demodulate(np.asarray(impaired, dtype=np.complex64))
        n = min(len(out), len(payload))
        ratio = sum(a == b for a, b in zip(out, payload)) / max(n, 1)
        assert ratio > 0.9, f"mild impairments broke decode: {ratio:.2%}"
