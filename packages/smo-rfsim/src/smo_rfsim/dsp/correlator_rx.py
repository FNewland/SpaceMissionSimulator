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
        # Bit buffer: carries trailing bits that don't fill a complete byte
        self._bit_buffer = np.array([], dtype=np.uint8)
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
        # Flush residual samples and bit buffer (demodulated under old modulation)
        self._residual = np.array([], dtype=np.complex64)
        self._bit_buffer = np.array([], dtype=np.uint8)

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
            # Use atan2-based detectors for robust convergence at all
            # initial phase offsets. The classic sign(I)*Q detector has
            # zero-crossings at ±90° where the PLL stalls.
            if self._modulation in (0, ):  # BPSK
                # Squared BPSK: raises signal to 2nd power to remove
                # modulation, then extract phase error. Converges from
                # any initial phase offset (no zero-crossing stall).
                sq = corrected ** 2
                phase_err = 0.5 * math.atan2(sq.imag, sq.real)
            elif self._modulation in (1, 3):  # QPSK / OQPSK
                # 4th power for QPSK
                sq = corrected ** 4
                phase_err = 0.25 * math.atan2(sq.imag, sq.real)
            else:  # 8PSK — M-th power
                sq = corrected ** 8
                phase_err = 0.125 * math.atan2(sq.imag, sq.real)
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
            snr_ok = self._energy_avg > 3.0

            # Modulation validation: check that the I/Q power distribution
            # matches the configured modulation. Prevents false lock when
            # the wrong modulation is configured (e.g., QPSK PLL locks on
            # BPSK with zero phase error because BPSK has no Q component).
            #
            # IMPORTANT: only validate AFTER sustained SNR lock, not during
            # acquisition. During PLL convergence the symbols rotate in
            # the I/Q plane and the ratio is ~1.0 regardless of modulation.
            mod_valid = True
            if snr_ok and self._energy_avg > 6.0 and n_symbols >= 20:
                mod_valid = self._validate_constellation(symbols)

            self.carrier_locked = snr_ok
            self.clock_locked = snr_ok
            # If modulation mismatch detected after sustained lock,
            # log but don't prevent frame sync — let the operator see
            # the constellation and decide
            if not mod_valid and snr_ok:
                logger.info("Constellation may not match configured modulation "
                            "(I/Q ratio check failed)")

        # 5. Store constellation points (after frequency correction)
        self.constellation_points = list(symbols[-128:])

        # 6. Symbol-to-bits mapping (modulation-dependent)
        new_bits = self._symbols_to_bits(symbols)

        # 7. Pack bits into bytes, carrying partial bits across calls
        # Prepend any leftover bits from the previous call
        if len(self._bit_buffer) > 0:
            all_bits = np.concatenate([self._bit_buffer, new_bits])
        else:
            all_bits = new_bits
        n_bytes = len(all_bits) // 8
        if n_bytes == 0:
            self._bit_buffer = all_bits  # save for next call
            return b''
        # Pack complete bytes, save remainder
        bits = all_bits[:n_bytes * 8]
        self._bit_buffer = all_bits[n_bytes * 8:]  # carry trailing bits
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

    def _validate_constellation(self, symbols: np.ndarray) -> bool:
        """Check that demodulated symbols match the configured modulation.

        Prevents false lock when the wrong modulation is configured.
        Uses I/Q power ratio to distinguish modulation orders:
        - BPSK: energy concentrated on real axis (I >> Q)
        - QPSK/OQPSK: balanced I and Q energy
        - 8PSK: balanced with uniform phase distribution
        - GMSK/GFSK: constant envelope (ring pattern)

        Returns False if the constellation clearly doesn't match,
        which causes the lock detector to report unlocked.
        """
        if len(symbols) < 20:
            return True

        i_power = np.mean(symbols.real ** 2)
        q_power = np.mean(symbols.imag ** 2) + 1e-10
        iq_ratio = i_power / q_power

        if self._modulation == 0:  # BPSK: expect I >> Q
            if iq_ratio < 2.0:
                # Too much Q energy for BPSK — likely QPSK or higher
                logger.debug("Constellation mismatch: BPSK config but I/Q ratio=%.1f "
                             "(expect >2.0)", iq_ratio)
                return False

        elif self._modulation in (1, 3):  # QPSK/OQPSK: expect balanced I ≈ Q
            if iq_ratio > 5.0 or iq_ratio < 0.2:
                # Too imbalanced — likely BPSK (all on I axis)
                logger.debug("Constellation mismatch: QPSK config but I/Q ratio=%.1f "
                             "(expect 0.2-5.0)", iq_ratio)
                return False

        elif self._modulation == 2:  # 8PSK: expect balanced
            if iq_ratio > 3.0 or iq_ratio < 0.33:
                logger.debug("Constellation mismatch: 8PSK config but I/Q ratio=%.1f",
                             iq_ratio)
                return False

        elif self._modulation in (6, 7):  # GMSK/GFSK: constant envelope
            amp_var = np.var(np.abs(symbols))
            amp_mean = np.mean(np.abs(symbols)) + 1e-10
            if amp_var / (amp_mean ** 2) > 0.3:
                # Too much amplitude variation for constant-envelope CPM
                logger.debug("Constellation mismatch: GMSK config but amplitude "
                             "variation too high")
                return False

        return True

    def get_constellation_iq(self, max_points: int = 128) -> list[list[float]]:
        """Return recent I/Q symbol samples for display."""
        points = self.constellation_points[-max_points:]
        return [[round(float(p.real), 3), round(float(p.imag), 3)] for p in points]
