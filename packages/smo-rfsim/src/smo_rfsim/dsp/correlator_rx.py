"""Correlation-based BPSK receiver.

Instead of a PLL + clock recovery chain (which has phase ambiguity and
convergence issues in pure Python), this receiver uses matched-filter
correlation against the known RRC pulse shape to make symbol decisions.

It works by:
1. Matched RRC filtering (maximize SNR)
2. Downsample at symbol rate (every sps samples)
3. Hard BPSK decision (sign of real part)
4. Pack bits into bytes
5. Track I/Q samples for constellation display

This is simpler and more robust than a full Costas+M&M chain, and
perfectly adequate for a simulation where we control both sides.
"""

import math
import logging
import numpy as np

logger = logging.getLogger(__name__)


def _rrc_taps(sps: int, rolloff: float = 0.35, ntaps: int = 101) -> np.ndarray:
    """Root-raised-cosine matched filter taps."""
    n = np.arange(ntaps) - (ntaps - 1) / 2
    t = n / sps
    taps = np.zeros(ntaps)
    for i, ti in enumerate(t):
        if ti == 0:
            taps[i] = 1.0 - rolloff + 4 * rolloff / math.pi
        elif rolloff > 0 and abs(abs(ti) - 1 / (4 * rolloff)) < 1e-8:
            taps[i] = (rolloff / math.sqrt(2)) * (
                (1 + 2 / math.pi) * math.sin(math.pi / (4 * rolloff)) +
                (1 - 2 / math.pi) * math.cos(math.pi / (4 * rolloff)))
        else:
            num = (math.sin(math.pi * ti * (1 - rolloff)) +
                   4 * rolloff * ti * math.cos(math.pi * ti * (1 + rolloff)))
            den = math.pi * ti * (1 - (4 * rolloff * ti) ** 2)
            taps[i] = num / den if den != 0 else 0
    return taps / np.sqrt(np.sum(taps ** 2))


class CorrelatorRX:
    """Correlation-based PSK receiver with frequency correction.

    Supports BPSK (1 bit/sym), QPSK (2 bits/sym), 8PSK (3 bits/sym),
    and OQPSK (2 bits/sym). Uses matched filtering, decision-directed
    frequency tracking, and symbol-rate downsampling.
    """

    # Constellation maps and bits-per-symbol for each modulation
    _BITS_PER_SYM = {0: 1, 1: 2, 2: 3, 3: 2}

    def __init__(self, sps: int = 8, rolloff: float = 0.35,
                 sample_rate: float = 256000.0, modulation: int = 0):
        self.sps = sps
        self.sample_rate = sample_rate
        self._modulation = modulation
        self._bps = self._BITS_PER_SYM.get(modulation, 1)
        self._rrc = _rrc_taps(sps, rolloff).astype(np.float32)
        self._residual = np.array([], dtype=np.complex64)
        self.constellation_points: list[complex] = []
        self.carrier_locked = False
        self.clock_locked = False
        self._energy_avg = 0.0
        # Coarse Doppler pre-compensation (set externally from sim state)
        self._doppler_hz = 0.0
        self._doppler_phase = 0.0
        # Fine PLL for residual phase/frequency after Doppler pre-comp
        self._phase = 0.0
        self._freq = 0.0
        bw = 0.08  # only needs to track residual, not full Doppler
        damping = 0.707
        denom = 1.0 + 2.0 * damping * bw + bw * bw
        self._alpha = 4.0 * damping * bw / denom
        self._beta = 4.0 * bw * bw / denom

    def set_doppler(self, doppler_hz: float):
        """Set the known Doppler frequency for pre-compensation."""
        self._doppler_hz = doppler_hz

    def set_modulation(self, modulation: int):
        """Change the modulation scheme, resetting acquisition state.

        A modulation change invalidates the PLL phase/frequency estimate
        and the lock detector energy average — the old values were tuned
        to a different constellation and would prevent re-acquisition.
        """
        if modulation == self._modulation:
            return
        logger.info("RX modulation %d → %d, resetting PLL", self._modulation, modulation)
        self._modulation = modulation
        self._bps = self._BITS_PER_SYM.get(modulation, 1)
        # Reset PLL — old phase/freq are for the wrong constellation
        self._phase = 0.0
        self._freq = 0.0
        self._doppler_phase = 0.0
        # Reset lock detector — old energy estimate is invalid
        self._energy_avg = 0.0
        self.carrier_locked = False
        self.clock_locked = False
        # Clear stale constellation points
        self.constellation_points = []
        # Flush residual samples (demodulated under old modulation)
        self._residual = np.array([], dtype=np.complex64)

    def demodulate(self, samples: np.ndarray) -> bytes:
        """Demodulate BPSK samples to bytes using matched filter + downsample."""
        # Prepend residual from previous call
        if len(self._residual) > 0:
            samples = np.concatenate([self._residual, samples])

        # 0. Coarse Doppler pre-compensation (removes bulk frequency offset)
        if self._doppler_hz != 0.0:
            n = len(samples)
            t = np.arange(n) / self.sample_rate
            phase = 2 * math.pi * (-self._doppler_hz) * t + self._doppler_phase
            samples = samples * np.exp(1j * phase).astype(np.complex64)
            self._doppler_phase = phase[-1] if n > 0 else self._doppler_phase
            # Wrap to prevent float overflow
            self._doppler_phase %= (2 * math.pi)

        # 1. Matched RRC filter
        filtered = np.convolve(samples, self._rrc.astype(np.complex64), mode='same')

        # 2. Downsample at symbol rate (take every sps-th sample)
        # Use peak detection within each symbol period for better timing
        n_symbols = len(filtered) // self.sps
        if n_symbols == 0:
            self._residual = samples
            return b''

        # Save residual for next call
        used = n_symbols * self.sps
        self._residual = samples[used:].copy()

        # Downsample at symbol boundary
        offset = 0
        indices = np.arange(n_symbols) * self.sps + offset
        indices = indices[indices < len(filtered)]
        raw_symbols = filtered[indices]
        n_symbols = len(raw_symbols)

        # 3. Frequency correction (removes Doppler rotation)
        # Apply current phase/freq estimate, then update using
        # BPSK decision-directed phase error detector.
        symbols = np.zeros_like(raw_symbols)
        for k in range(n_symbols):
            # Correct phase
            corrected = raw_symbols[k] * np.exp(-1j * self._phase)
            symbols[k] = corrected
            # Phase error detector (modulation-dependent)
            if self._modulation in (0, ):  # BPSK
                phase_err = corrected.imag * np.sign(corrected.real)
            elif self._modulation in (1, 3):  # QPSK / OQPSK
                phase_err = (np.sign(corrected.real) * corrected.imag
                             - np.sign(corrected.imag) * corrected.real)
            else:  # 8PSK — decision-directed
                angle = np.angle(corrected)
                nearest = round(angle / (math.pi / 4)) * (math.pi / 4)
                phase_err = angle - nearest
            # 2nd order loop update
            self._freq += self._beta * phase_err
            self._phase += self._freq + self._alpha * phase_err
            # Wrap phase
            self._phase %= (2 * math.pi)

        # 4. Lock detection using decision-directed EVM estimate.
        # Measure error between each symbol and its nearest ideal
        # constellation point. Low EVM = locked, high EVM = unlocked.
        # This works for all modulations, unlike Re²/Im² which is
        # BPSK-specific.
        if n_symbols > 0:
            if self._modulation == 0:  # BPSK
                # Ideal points at ±1 on real axis
                decided = np.sign(symbols.real)
                error = symbols - decided
            elif self._modulation in (1, 3):  # QPSK / OQPSK
                from smo_rfsim.dsp.modulator import QPSK_MAP
                nearest = np.array([QPSK_MAP[np.argmin(np.abs(QPSK_MAP - s))]
                                    for s in symbols])
                error = symbols - nearest
            else:  # 8PSK
                from smo_rfsim.dsp.modulator import PSK8_MAP
                nearest = np.array([PSK8_MAP[np.argmin(np.abs(PSK8_MAP - s))]
                                    for s in symbols])
                error = symbols - nearest

            signal_power = np.mean(np.abs(symbols) ** 2) + 1e-10
            error_power = np.mean(np.abs(error) ** 2) + 1e-10
            snr_est = signal_power / error_power
            # Asymmetric filter: fast attack, slow decay
            if snr_est > self._energy_avg:
                self._energy_avg = 0.9 * self._energy_avg + 0.1 * snr_est
            else:
                self._energy_avg = 0.5 * self._energy_avg + 0.5 * snr_est
            # SNR > 3 (~5 dB) indicates real signal, not just noise
            self.carrier_locked = self._energy_avg > 3.0
            self.clock_locked = self.carrier_locked

        # 5. Store constellation points (after frequency correction)
        self.constellation_points = list(symbols[-128:])

        # 6. Symbol-to-bits mapping (modulation-dependent)
        bits = self._symbols_to_bits(symbols)

        # 7. Pack bits into bytes
        n_bytes = len(bits) // 8
        if n_bytes == 0:
            return b''
        bits = bits[:n_bytes * 8]
        byte_array = np.packbits(bits)
        result = bytes(byte_array)

        # 8. Phase ambiguity resolution: PSK PLLs can lock at rotated phases.
        # BPSK: 180° ambiguity. QPSK/OQPSK: 90° ambiguity. 8PSK: 45° ambiguity.
        # Check if the ASM pattern appears. If not, try constellation-appropriate
        # phase rotations by re-mapping symbols with the rotated phase.
        asm = b'\x1A\xCF\xFC\x1D'
        if len(result) >= 8:
            has_asm = any(result[i:i+4] == asm for i in range(min(len(result)-4, 200)))
            if not has_asm:
                # Determine candidate rotations based on modulation
                if self._modulation == 0:  # BPSK: 180°
                    rotations = [math.pi]
                elif self._modulation in (1, 3):  # QPSK/OQPSK: 90°, 180°, 270°
                    rotations = [math.pi/2, math.pi, 3*math.pi/2]
                else:  # 8PSK: 45° increments
                    rotations = [k * math.pi/4 for k in range(1, 8)]
                for rot in rotations:
                    rotated_syms = symbols * np.exp(-1j * rot)
                    rot_bits = self._symbols_to_bits(rotated_syms)
                    n_b = len(rot_bits) // 8
                    if n_b == 0:
                        continue
                    rot_bytes = bytes(np.packbits(rot_bits[:n_b * 8]))
                    if any(rot_bytes[i:i+4] == asm
                           for i in range(min(len(rot_bytes)-4, 200))):
                        result = rot_bytes
                        self._phase += rot
                        break

        return result

    def _symbols_to_bits(self, symbols: np.ndarray) -> np.ndarray:
        """Map demodulated complex symbols to bit decisions."""
        if self._modulation == 0:  # BPSK: 1 bit per symbol
            return (symbols.real > 0).astype(np.uint8)

        elif self._modulation in (1, 3):  # QPSK / OQPSK: 2 bits per symbol
            # Match modulator: idx 0→(+,+), 1→(-,+), 2→(-,-), 3→(+,-)
            # bit0 = NOT(I>0), bit1 = NOT(Q>0)  [inverted because idx 0 = I+,Q+]
            # Actually: map each symbol to nearest constellation point index
            # Index = (I<0)*2 + (Q<0)... no, let's use the modulator's map directly
            from smo_rfsim.dsp.modulator import QPSK_MAP
            bits = np.zeros(len(symbols) * 2, dtype=np.uint8)
            for k, sym in enumerate(symbols):
                # Find nearest QPSK point
                dists = np.abs(QPSK_MAP - sym)
                idx = np.argmin(dists)
                bits[k*2] = (idx >> 1) & 1
                bits[k*2+1] = idx & 1
            return bits

        elif self._modulation == 2:  # 8PSK: 3 bits per symbol
            from smo_rfsim.dsp.modulator import PSK8_MAP
            bits = np.zeros(len(symbols) * 3, dtype=np.uint8)
            for k, sym in enumerate(symbols):
                dists = np.abs(PSK8_MAP - sym)
                idx = np.argmin(dists)
                bits[k*3] = (idx >> 2) & 1
                bits[k*3+1] = (idx >> 1) & 1
                bits[k*3+2] = idx & 1
            return bits

        # Fallback: BPSK
        return (symbols.real > 0).astype(np.uint8)

    def get_constellation_iq(self, max_points: int = 128) -> list[list[float]]:
        """Return recent I/Q symbol samples for display."""
        points = self.constellation_points[-max_points:]
        return [[round(float(p.real), 3), round(float(p.imag), 3)] for p in points]
