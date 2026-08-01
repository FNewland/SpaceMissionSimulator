"""Live RF impairments (defect #56).

`SpaceLinkChannel` — Rician fading, Wiener phase noise, CW/wideband
interferers, multipath — was fully written and instantiated nowhere; the
pipeline used the two-impairment `BasebandChannel` instead. These tests
assert the impairments are now reachable from the running pipeline, that
they measurably degrade the receiver, and that they can be cleared again.
"""

import time

import numpy as np
import pytest

from smo_rfsim.bridge import RFSimBridge
from smo_rfsim.channel.space_link import (
    SpaceLinkChannel, SpaceLinkConfig, available_impairments,
    build_impairment, register_impairment,
)
from smo_rfsim.config import RFSimConfig
from smo_rfsim.mode import RFSimMode
from smo_rfsim.pipeline.channel_stage import ChannelStage
from smo_rfsim.pipeline.coordinator import PipelineCoordinator


def _tone(n=4096):
    """Unit-amplitude reference signal."""
    return np.ones(n, dtype=np.complex64)


class TestChannelStageUsesSpaceLink:
    def test_channel_stage_instantiates_space_link_channel(self):
        stage = ChannelStage(eb_n0_db=12.0, sps=8)
        assert isinstance(stage.channel, SpaceLinkChannel)

    def test_pipeline_channel_is_space_link(self):
        cfg = RFSimConfig()
        cfg.mode = RFSimMode.RF
        cfg.network.zmq_samples_port = 0
        pc = PipelineCoordinator(cfg)
        assert isinstance(pc._channel.channel, SpaceLinkChannel)

    def test_config_impairments_reach_the_channel(self):
        cfg = RFSimConfig()
        cfg.mode = RFSimMode.RF
        cfg.network.zmq_samples_port = 0
        cfg.channel.phase_noise_enabled = True
        cfg.channel.fading_enabled = True
        cfg.channel.interferers = [
            {"type": "cw", "freq_offset_hz": 12000, "power_dbm": -40}]
        pc = PipelineCoordinator(cfg)
        status = pc.get_channel_status()
        assert status["phase_noise_enabled"] is True
        assert status["fading_enabled"] is True
        assert status["n_interferers"] == 1
        assert status["frequency_hz"] == pytest.approx(cfg.channel.frequency_hz)


class TestImpairmentsAreMeasurable:
    def test_phase_noise_rotates_the_carrier(self):
        cfg = SpaceLinkConfig(awgn_enabled=False, phase_noise_enabled=True,
                              phase_noise_linewidth_hz=2000.0, seed=1)
        ch = SpaceLinkChannel(cfg)
        out = ch.process(_tone())
        phase_spread = float(np.std(np.angle(out)))
        assert phase_spread > 0.05, "phase noise had no effect"
        assert np.allclose(np.abs(out), 1.0, atol=1e-5), (
            "phase noise must not change the envelope")

    def test_fading_modulates_the_envelope(self):
        cfg = SpaceLinkConfig(awgn_enabled=False, fading_enabled=True,
                              fading_k_factor_db=0.0,
                              fading_max_doppler_hz=500.0, seed=1)
        ch = SpaceLinkChannel(cfg)
        out = ch.process(_tone())
        assert float(np.std(np.abs(out))) > 0.05, "fading had no effect"

    def test_cw_interferer_appears_at_the_right_offset(self):
        cfg = SpaceLinkConfig(awgn_enabled=False, sample_rate=256000.0,
                              interferers=[{"type": "cw",
                                            "freq_offset_hz": 20000.0,
                                            "power_dbm": -30.0}])
        ch = SpaceLinkChannel(cfg)
        out = ch.process(np.zeros(8192, dtype=np.complex64))
        spec = np.abs(np.fft.fftshift(np.fft.fft(out)))
        freqs = np.fft.fftshift(np.fft.fftfreq(len(out), 1 / cfg.sample_rate))
        peak_hz = float(freqs[int(np.argmax(spec))])
        assert abs(peak_hz - 20000.0) < 200.0, (
            f"CW interferer at {peak_hz} Hz, expected 20 kHz")

    def test_impairments_are_independently_switchable(self):
        ch = SpaceLinkChannel(SpaceLinkConfig(seed=1))
        assert ch.get_status()["fading_enabled"] is False
        st = ch.set_impairments(fading_enabled=True, fading_k_factor_db=3.0)
        assert st["fading_enabled"] is True
        assert st["fading_k_factor_db"] == 3.0
        st = ch.set_impairments(fading_enabled=False)
        assert st["fading_enabled"] is False

    def test_unknown_parameter_is_rejected(self):
        ch = SpaceLinkChannel(SpaceLinkConfig())
        with pytest.raises(ValueError):
            ch.set_impairments(fadng_enabled=True)   # typo


class TestImpairmentRegistry:
    """Extension point for further impairment types (e.g. a SatHack port)."""

    def test_builtin_types_registered(self):
        assert "cw" in available_impairments()
        assert "wideband" in available_impairments()

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            build_impairment({"type": "tachyon_beam"}, SpaceLinkConfig())

    def test_third_party_impairment_can_be_registered(self):
        class DCOffset:
            def __init__(self, offset):
                self.offset = offset

            def apply(self, samples):
                return samples + self.offset

        register_impairment("test_dc_offset",
                            lambda spec, cfg: DCOffset(spec.get("offset", 0.5)))
        try:
            assert "test_dc_offset" in available_impairments()
            cfg = SpaceLinkConfig(
                awgn_enabled=False,
                interferers=[{"type": "test_dc_offset", "offset": 0.25}])
            ch = SpaceLinkChannel(cfg)
            out = ch.process(np.zeros(64, dtype=np.complex64))
            assert np.allclose(out.real, 0.25)
        finally:
            from smo_rfsim.channel import space_link
            space_link._IMPAIRMENT_REGISTRY.pop("test_dc_offset", None)


class TestImpairmentsDegradeAndClear:
    """End-to-end: injected impairments must measurably hurt the live
    receiver, and clearing them must restore it."""

    SETTLE_S = 4.0

    def test_evm_degrades_then_recovers(self):
        cfg = RFSimConfig()
        cfg.mode = RFSimMode.RF
        cfg.network.zmq_samples_port = 0
        cfg.network.radio_control_enabled = True
        cfg.ccsds.convolutional_enabled = False
        bridge = RFSimBridge(cfg)
        pc = bridge._pipeline
        pc.start()
        pc.set_link_in_view(True)
        pc.set_transmitting(True)
        try:
            for _ in range(15):
                pc.enqueue_tm_packet(bytes(range(64)))
            time.sleep(self.SETTLE_S)
            clean_evm = pc.evm_db
            clean_good = pc.good_frames
            assert clean_evm > 8.0, f"link not healthy to start with ({clean_evm})"

            bridge.apply_channel({
                "phase_noise_enabled": True,
                "phase_noise_linewidth_hz": 3000.0,
                "fading_enabled": True,
                "fading_k_factor_db": 0.0,
                "fading_max_doppler_hz": 200.0,
                "interferers": [{"type": "cw", "freq_offset_hz": 20000,
                                 "power_dbm": -32}],
            })
            for _ in range(15):
                pc.enqueue_tm_packet(bytes(range(64)))
            time.sleep(self.SETTLE_S)
            bad_evm = pc.evm_db
            assert bad_evm < clean_evm - 3.0, (
                f"impairments did not degrade the receiver "
                f"({clean_evm:.1f} dB → {bad_evm:.1f} dB)")

            bridge.apply_channel({
                "phase_noise_enabled": False,
                "fading_enabled": False,
                "interferers": [],
            })
            for _ in range(15):
                pc.enqueue_tm_packet(bytes(range(64)))
            time.sleep(self.SETTLE_S + 2.0)
            recovered_evm = pc.evm_db
            assert recovered_evm > bad_evm + 3.0, (
                f"receiver did not recover after clearing impairments "
                f"({bad_evm:.1f} dB → {recovered_evm:.1f} dB)")
            assert pc.get_channel_status()["n_interferers"] == 0
        finally:
            pc.stop()
