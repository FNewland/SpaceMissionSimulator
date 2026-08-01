"""Modulation round-trip tests (defect #54).

Covers every scheme the RF chain advertises: each one must modulate *and*
demodulate. Before these fixes only BPSK/QPSK/8PSK had a receiver, OQPSK
was a mislabelled QPSK with no Q offset, and 16-APSK / pi/4-DQPSK / GMSK /
GFSK silently fell back to a BPSK slicer.
"""

import numpy as np
import pytest

from smo_rfsim.dsp.channel import BasebandChannel
from smo_rfsim.dsp.correlator_rx import CorrelatorRX, UnsupportedModulationError
from smo_rfsim.dsp.modulator import (
    Modulator, MOD_NAMES, BITS_PER_SYMBOL, constellation_map,
    QPSK_GRAY_MAP, PSK8_GRAY_MAP, QPSK_MAP, PSK8_MAP,
)

ALL_MODS = list(range(8))
PAYLOAD = bytes(np.random.default_rng(1234).integers(0, 256, 400, dtype=np.uint8))


def _roundtrip(mod: int, eb_n0_db: float = 99.0, gray: bool = True) -> bytes:
    m = Modulator(modulation=mod, sps=8, gray_coding=gray)
    rx = CorrelatorRX(sps=8, modulation=mod, gray_coding=gray)
    samples = m.modulate(PAYLOAD)
    if eb_n0_db < 50:
        ch = BasebandChannel(eb_n0_db=eb_n0_db, sps=8,
                             bits_per_symbol=BITS_PER_SYMBOL[mod])
        samples = ch.process(samples)
    return rx.demodulate(np.asarray(samples, dtype=np.complex64))


def _match_ratio(out: bytes) -> float:
    n = min(len(out), len(PAYLOAD))
    if n == 0:
        return 0.0
    return sum(a == b for a, b in zip(out, PAYLOAD)) / n


class TestEveryModulationRoundTrips:
    @pytest.mark.parametrize("mod", ALL_MODS)
    def test_noiseless_roundtrip(self, mod):
        out = _roundtrip(mod)
        assert len(out) >= len(PAYLOAD) - 1, f"{MOD_NAMES[mod]} produced no bytes"
        assert _match_ratio(out) > 0.99, (
            f"{MOD_NAMES[mod]} round-trip failed: "
            f"{_match_ratio(out):.2%} bytes correct")

    @pytest.mark.parametrize("mod", ALL_MODS)
    def test_natural_binary_still_selectable(self, mod):
        assert _match_ratio(_roundtrip(mod, gray=False)) > 0.99

    @pytest.mark.parametrize("mod", [0, 1, 2, 3, 4, 5])
    def test_roundtrip_with_awgn(self, mod):
        """Linear/coherent schemes survive a 10 dB Eb/N0 channel."""
        assert _match_ratio(_roundtrip(mod, eb_n0_db=10.0)) > 0.95

    @pytest.mark.parametrize("mod", [6, 7])
    def test_cpm_roundtrip_with_awgn(self, mod):
        """CPM discriminator detection is non-coherent, so it needs more
        Eb/N0 than a coherent slicer — but it must still work."""
        assert _match_ratio(_roundtrip(mod, eb_n0_db=18.0)) > 0.8


class TestGrayCoding:
    def test_qpsk_gray_map_is_gray(self):
        # Sort the 4 points by angle; neighbours must differ in one bit
        order = np.argsort(np.angle(QPSK_GRAY_MAP) % (2 * np.pi))
        for i in range(len(order)):
            a, b = int(order[i]), int(order[(i + 1) % len(order)])
            assert bin(a ^ b).count("1") == 1, "QPSK map is not Gray coded"

    def test_8psk_gray_map_is_gray(self):
        order = np.argsort(np.angle(PSK8_GRAY_MAP) % (2 * np.pi))
        for i in range(len(order)):
            a, b = int(order[i]), int(order[(i + 1) % len(order)])
            assert bin(a ^ b).count("1") == 1, "8PSK map is not Gray coded"

    def test_natural_maps_are_not_gray(self):
        """Sanity check: the legacy maps really were natural binary."""
        order = np.argsort(np.angle(QPSK_MAP) % (2 * np.pi))
        gaps = [bin(int(order[i]) ^ int(order[(i + 1) % 4])).count("1")
                for i in range(4)]
        assert max(gaps) == 2

    def test_gray_selected_by_default(self):
        assert np.allclose(constellation_map(1, True), QPSK_GRAY_MAP)
        assert np.allclose(constellation_map(1, False), QPSK_MAP)
        assert np.allclose(constellation_map(2, False), PSK8_MAP)

    @staticmethod
    def _mean_neighbour_hamming(cmap: np.ndarray) -> float:
        """Mean bit errors caused by mistaking a point for its nearest
        neighbour — the quantity Gray coding minimises (to exactly 1)."""
        total = 0
        for idx, point in enumerate(cmap):
            dist = np.abs(cmap - point)
            dist[idx] = np.inf
            nearest = int(np.argmin(dist))
            total += bin(idx ^ nearest).count("1")
        return total / len(cmap)

    @pytest.mark.parametrize("mod", [1, 2, 3])
    def test_gray_lowers_bit_errors_vs_natural(self, mod):
        """A symbol error at high Eb/N0 lands on an adjacent point. With
        Gray coding that costs exactly one bit; with natural binary it
        costs more — which is why measured BER used to sit above the
        textbook curve."""
        gray_cost = self._mean_neighbour_hamming(constellation_map(mod, True))
        natural_cost = self._mean_neighbour_hamming(constellation_map(mod, False))
        assert gray_cost == pytest.approx(1.0), (
            f"{MOD_NAMES[mod]} Gray map costs {gray_cost} bits per "
            f"adjacent symbol error, expected 1.0")
        assert natural_cost > gray_cost


class TestOQPSKOffset:
    """The defining feature of OQPSK: a half-symbol delay on the Q rail."""

    SPS = 8
    DATA = bytes(np.random.default_rng(7).integers(0, 256, 200, dtype=np.uint8))

    def _pair(self):
        q = Modulator(modulation=1, sps=self.SPS).modulate(self.DATA)
        o = Modulator(modulation=3, sps=self.SPS).modulate(self.DATA)
        return q, o

    def test_oqpsk_differs_from_qpsk(self):
        q, o = self._pair()
        assert not np.allclose(q, o), "OQPSK waveform is identical to QPSK"

    def test_i_rail_unchanged(self):
        q, o = self._pair()
        assert np.allclose(q.real, o.real, atol=1e-5)

    def test_q_rail_delayed_by_half_symbol(self):
        q, o = self._pair()
        half = self.SPS // 2
        assert np.allclose(o.imag[half:], q.imag[:-half], atol=1e-5), (
            "OQPSK Q rail is not delayed by sps/2")

    def test_envelope_never_crosses_zero(self):
        """No simultaneous I/Q transitions → no 180° phase jumps → the
        envelope does not collapse the way QPSK's does."""
        q, o = self._pair()
        mid = slice(200, -200)
        assert np.min(np.abs(o[mid])) > 10 * np.min(np.abs(q[mid]))

    def test_q_offset_is_continuous_across_chunks(self):
        """The half-symbol tail must carry over between modulate() calls,
        otherwise the offset resets at every frame boundary."""
        m_single = Modulator(modulation=3, sps=self.SPS)
        m_split = Modulator(modulation=3, sps=self.SPS)
        whole = m_single.modulate(self.DATA)
        a = m_split.modulate(self.DATA[:100])
        b = m_split.modulate(self.DATA[100:])
        joined = np.concatenate([a, b])
        # Compare well inside each half to avoid the convolution edges
        assert np.allclose(joined[100:700], whole[100:700], atol=1e-3)

    def test_staggered_receiver_recovers_data(self):
        assert _match_ratio(_roundtrip(3)) > 0.99


class TestUnsupportedModulationFailsLoudly:
    def test_constructor_rejects_unknown_mode(self):
        with pytest.raises(UnsupportedModulationError):
            CorrelatorRX(sps=8, modulation=99)

    def test_set_modulation_rejects_unknown_mode(self):
        rx = CorrelatorRX(sps=8, modulation=0)
        with pytest.raises(UnsupportedModulationError):
            rx.set_modulation(42)

    def test_all_advertised_modes_are_supported(self):
        """Every entry in MOD_NAMES must have a receiver — advertising a
        scheme that cannot be received is the bug this guards."""
        for mod in MOD_NAMES:
            assert mod in CorrelatorRX.SUPPORTED_MODULATIONS, (
                f"{MOD_NAMES[mod]} is advertised but has no receiver")


class TestGFSKModulationIndex:
    def test_gfsk_index_is_configurable(self):
        """GFSK used to be byte-identical to GMSK (h hard-wired to 0.5)."""
        a = Modulator(modulation=7, sps=8, gfsk_mod_index=0.5).modulate(b"\xA5" * 32)
        b = Modulator(modulation=7, sps=8, gfsk_mod_index=0.8).modulate(b"\xA5" * 32)
        assert not np.allclose(a, b)

    def test_gmsk_index_stays_at_half(self):
        a = Modulator(modulation=6, sps=8, gfsk_mod_index=0.9).modulate(b"\xA5" * 32)
        b = Modulator(modulation=6, sps=8, gfsk_mod_index=0.5).modulate(b"\xA5" * 32)
        assert np.allclose(a, b)
