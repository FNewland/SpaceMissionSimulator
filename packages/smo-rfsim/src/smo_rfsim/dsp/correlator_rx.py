"""Correlation-based PSK/APSK/CPM receiver.

Instead of a PLL + clock recovery chain (which has phase ambiguity and
convergence issues in pure Python), this receiver uses matched-filter
correlation against the known RRC pulse shape to make symbol decisions.

It works by:
1. Matched RRC filtering (maximize SNR)
2. Downsample at symbol rate (every sps samples; staggered I/Q for OQPSK)
3. Symbol decision (nearest constellation point, or differential for π/4-DQPSK)
4. Pack bits into bytes
5. Track I/Q samples for constellation display and post-matched-filter
   samples for the eye diagram

GMSK/GFSK take a separate continuous-phase (CPM) path using a
differential-phase discriminator followed by a symbol-period integrator.

This is simpler and more robust than a full Costas+M&M chain, and
perfectly adequate for a simulation where we control both sides.
"""

import math
import logging
import numpy as np

logger = logging.getLogger(__name__)


class UnsupportedModulationError(ValueError):
    """Raised when a modulation with no receiver implementation is selected.

    Deliberately loud: silently falling back to a BPSK slicer produced
    plausible-looking garbage for every TX-only scheme (defect #54).
    """


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
    """Correlation-based receiver with frequency correction.

    Supports BPSK (1 bit/sym), QPSK (2), 8PSK (3), OQPSK (2, staggered
    I/Q sampling), 16-APSK (4, nearest-point on the DVB-S2 two-ring map),
    π/4-DQPSK (2, differential detection) and GMSK/GFSK (1, CPM
    discriminator).  Uses matched filtering, decision-directed frequency
    tracking, and symbol-rate downsampling.
    """

    # Bits-per-symbol for each modulation. A modulation absent from this
    # table has no receiver and is rejected at selection time.
    _BITS_PER_SYM = {0: 1, 1: 2, 2: 3, 3: 2, 4: 4, 5: 2, 6: 1, 7: 1}

    #: Modulations this receiver can actually demodulate.
    SUPPORTED_MODULATIONS = frozenset(_BITS_PER_SYM)

    #: Continuous-phase modulations (separate demod path).
    _CPM_MODULATIONS = frozenset({6, 7})

    #: Number of post-matched-filter samples retained for the eye diagram.
    EYE_BUFFER_SIZE = 1024

    def __init__(self, sps: int = 8, rolloff: float = 0.35,
                 sample_rate: float = 256000.0, modulation: int = 0,
                 gray_coding: bool = True, gmsk_bt: float = 0.5,
                 loop_bw: float = 0.08):
        self._check_supported(modulation)
        self.sps = sps
        self.sample_rate = sample_rate
        self.gray_coding = gray_coding
        self.gmsk_bt = gmsk_bt
        self._modulation = modulation
        self._bps = self._BITS_PER_SYM[modulation]
        self._rrc = _rrc_taps(sps, rolloff).astype(np.float32)
        from .modulator import _gaussian_taps
        self._gaussian = _gaussian_taps(sps, gmsk_bt)
        self._residual = np.array([], dtype=np.complex64)
        self.constellation_points: list[complex] = []
        self.carrier_locked = False
        self.clock_locked = False
        self._energy_avg = 0.0
        self._agc_gain = 1.0
        # Coarse Doppler pre-compensation (set externally from sim state)
        self._doppler_hz = 0.0
        self._doppler_phase = 0.0
        # Bit buffer: carries trailing bits that don't fill a complete byte
        self._bit_buffer = np.array([], dtype=np.uint8)
        # π/4-DQPSK differential reference (last symbol of previous chunk)
        self._pi4_prev: complex = 1.0 + 0j
        # Post-matched-filter samples retained for the eye diagram
        self._eye_buffer = np.array([], dtype=np.complex64)
        self._eye_offset = 0
        # Fine PLL for residual phase/frequency after Doppler pre-comp
        self._phase = 0.0
        self._freq = 0.0
        bw = loop_bw  # only needs to track residual, not full Doppler
        damping = 0.707
        denom = 1.0 + 2.0 * damping * bw + bw * bw
        self._alpha = 4.0 * damping * bw / denom
        self._beta = 4.0 * bw * bw / denom

    # ── Modulation selection ──

    @classmethod
    def _check_supported(cls, modulation: int) -> None:
        if modulation not in cls._BITS_PER_SYM:
            from .modulator import MOD_NAMES
            name = MOD_NAMES.get(modulation, str(modulation))
            raise UnsupportedModulationError(
                f"Modulation {modulation} ({name}) has no receiver "
                f"implementation. Supported: "
                f"{sorted(cls.SUPPORTED_MODULATIONS)}")

    @property
    def reference_map(self) -> np.ndarray:
        """Ideal constellation used for slicing and EVM."""
        from .modulator import constellation_map, PSK8_MAP
        if self._modulation == 5:
            # π/4-DQPSK transmits on the 8PSK ring
            return PSK8_MAP
        return constellation_map(self._modulation, self.gray_coding)

    def set_doppler(self, doppler_hz: float):
        """Set the known Doppler frequency for pre-compensation."""
        self._doppler_hz = doppler_hz

    def nudge_phase(self, delta_rad: float):
        """Apply an instantaneous phase offset to the PLL.

        Used by the carrier auto-search: if frame sync can't find ASMs
        despite carrier lock, the PLL may be settled on a phase ambiguity
        that the ASM-based resolver missed. A discrete nudge forces the
        PLL to re-converge from a different starting point.
        """
        self._phase = (self._phase + delta_rad) % (2 * math.pi)
        # Flush byte-level state — symbols demodulated before the nudge
        # are under the old phase and would corrupt frame sync
        self._bit_buffer = np.array([], dtype=np.uint8)
        self._residual = np.array([], dtype=np.complex64)

    def reset_acquisition(self):
        """Full PLL reset — forces carrier re-acquisition from scratch.

        Stronger than nudge_phase: zeroes the frequency integrator and
        the lock detector so the loop has to re-pull-in. Used when frame
        sync drops from LOCK to SEARCH, indicating the demodulated byte
        stream has become unusable despite the PLL reporting 'locked'.
        """
        self._phase = 0.0
        self._freq = 0.0
        self._energy_avg = 0.0
        self._agc_gain = 1.0
        self.carrier_locked = False
        self.clock_locked = False
        self._bit_buffer = np.array([], dtype=np.uint8)
        self._residual = np.array([], dtype=np.complex64)
        self.constellation_points = []
        self._pi4_prev = 1.0 + 0j
        logger.info("PLL: full acquisition reset")

    def set_modulation(self, modulation: int):
        """Change the modulation scheme, resetting acquisition state.

        A modulation change invalidates the PLL phase/frequency estimate
        and the lock detector energy average — the old values were tuned
        to a different constellation and would prevent re-acquisition.

        Raises UnsupportedModulationError for schemes with no receiver.
        """
        self._check_supported(modulation)
        if modulation == self._modulation:
            return
        logger.info("RX modulation %d → %d, resetting PLL", self._modulation, modulation)
        self._modulation = modulation
        self._bps = self._BITS_PER_SYM[modulation]
        # Reset PLL — old phase/freq are for the wrong constellation
        self._phase = 0.0
        self._freq = 0.0
        self._doppler_phase = 0.0
        # Reset lock detector — old energy estimate is invalid
        self._energy_avg = 0.0
        self._agc_gain = 1.0
        self.carrier_locked = False
        self.clock_locked = False
        # Clear stale constellation points
        self.constellation_points = []
        # Flush residual samples and bit buffer (demodulated under old modulation)
        self._residual = np.array([], dtype=np.complex64)
        self._bit_buffer = np.array([], dtype=np.uint8)
        self._pi4_prev = 1.0 + 0j
        self._eye_buffer = np.array([], dtype=np.complex64)

    # ── Eye diagram tap ──

    def _store_eye(self, filtered: np.ndarray, offset: int = 0) -> None:
        """Retain the most recent post-matched-filter (oversampled) samples.

        This — not the symbol-rate decimated constellation tap — is what an
        eye diagram needs: the waveform *between* decision instants.
        """
        if len(filtered) == 0:
            return
        keep = filtered[-self.EYE_BUFFER_SIZE:]
        # Symbol-timing phase of the first retained sample, so the display
        # can align traces to the decision instant. Decision instants sit at
        # index `offset + k*sps` of `filtered`; after dropping the leading
        # `dropped` samples they sit at `offset - dropped + k*sps`.
        dropped = max(0, len(filtered) - self.EYE_BUFFER_SIZE)
        self._eye_offset = int((offset - dropped) % self.sps)
        self._eye_buffer = np.asarray(keep, dtype=np.complex64).copy()

    def get_eye_samples(self, max_samples: int = None) -> np.ndarray:
        """Oversampled post-matched-filter samples for the eye diagram."""
        buf = self._eye_buffer
        if max_samples is not None and len(buf) > max_samples:
            return buf[-max_samples:]
        return buf

    @property
    def eye_timing_offset(self) -> int:
        """Index (within the first symbol period of get_eye_samples()) of
        the symbol decision instant."""
        return self._eye_offset

    @property
    def evm_db(self) -> float:
        """Decision-directed SNR estimate in dB (0 when unlocked)."""
        if self._energy_avg <= 0:
            return 0.0
        return 10.0 * math.log10(self._energy_avg)

    # ── Demodulation ──

    def demodulate(self, samples: np.ndarray) -> bytes:
        """Demodulate complex baseband samples to bytes."""
        if self._modulation in self._CPM_MODULATIONS:
            return self._demodulate_cpm(samples)
        return self._demodulate_psk(samples)

    def _precompensate(self, samples: np.ndarray) -> np.ndarray:
        """Prepend residual and remove the known Doppler offset."""
        if len(self._residual) > 0:
            samples = np.concatenate([self._residual, samples])
        if self._doppler_hz != 0.0:
            n = len(samples)
            t = np.arange(n) / self.sample_rate
            phase = 2 * math.pi * (-self._doppler_hz) * t + self._doppler_phase
            samples = samples * np.exp(1j * phase).astype(np.complex64)
            self._doppler_phase = phase[-1] if n > 0 else self._doppler_phase
            # Wrap to prevent float overflow
            self._doppler_phase %= (2 * math.pi)
        return samples

    def _demodulate_psk(self, samples: np.ndarray) -> bytes:
        """PSK/APSK path: matched filter → decimate → PLL → slice."""
        samples = self._precompensate(samples)

        # 1. Matched RRC filter
        filtered = np.convolve(samples, self._rrc.astype(np.complex64), mode='same')

        # 2. Downsample at symbol rate. OQPSK samples the Q rail half a
        #    symbol later than the I rail — that stagger *is* the modulation.
        staggered = self._modulation == 3 and self.sps >= 2
        half = self.sps // 2 if staggered else 0
        n_symbols = max(0, (len(filtered) - half) // self.sps)
        if n_symbols == 0:
            self._residual = samples
            return b''

        # Save residual for next call
        used = n_symbols * self.sps
        self._residual = samples[used:].copy()
        self._store_eye(filtered[:used + half])

        indices = np.arange(n_symbols) * self.sps
        if staggered:
            # Keep both taps complex: the carrier phase must be removed
            # *before* the I/Q rails are separated, otherwise a residual
            # rotation cross-couples them.
            raw_symbols = filtered[indices]
            raw_q = filtered[indices + half]
        else:
            indices = indices[indices < len(filtered)]
            raw_symbols = filtered[indices]
            raw_q = None
        n_symbols = len(raw_symbols)

        # 3. Frequency correction (removes residual carrier rotation).
        symbols = self._track_carrier(raw_symbols, raw_q)

        # 4. AGC — 16-APSK carries information in the ring radius, so the
        #    slicer needs the amplitude normalised. Harmless for M-PSK.
        if self._modulation == 4 and n_symbols > 0:
            rms = float(np.sqrt(np.mean(np.abs(symbols) ** 2))) + 1e-9
            self._agc_gain = 0.8 * self._agc_gain + 0.2 * (1.0 / rms)
            symbols = symbols * self._agc_gain

        # 5. Lock detection using a decision-directed EVM estimate.
        self._update_lock(symbols)

        # 6. Store constellation points (after frequency correction)
        self.constellation_points = list(symbols[-128:])

        # 7. Symbol-to-bits mapping (modulation-dependent)
        new_bits = self._symbols_to_bits(symbols)

        # 8. Pack bits into bytes, carrying partial bits across calls
        if len(self._bit_buffer) > 0:
            all_bits = np.concatenate([self._bit_buffer, new_bits])
        else:
            all_bits = new_bits
        n_bytes = len(all_bits) // 8
        if n_bytes == 0:
            self._bit_buffer = all_bits  # save for next call
            return b''
        bits = all_bits[:n_bytes * 8]
        self._bit_buffer = all_bits[n_bytes * 8:]  # carry trailing bits
        result = bytes(np.packbits(bits))

        # 9. Phase ambiguity resolution. Differential π/4-DQPSK is immune
        #    (information is in the phase *change*), so it is skipped.
        if self._modulation != 5:
            result = self._resolve_phase_ambiguity(result, symbols)
        return result

    def _track_carrier(self, raw_symbols: np.ndarray,
                       raw_q: np.ndarray = None) -> np.ndarray:
        """2nd-order M-th-power / decision-directed carrier tracking loop.

        ``raw_q`` carries the half-symbol-late OQPSK Q tap: both taps are
        de-rotated by the same loop phase, then the I rail is read from the
        on-time tap and the Q rail from the late tap.
        """
        symbols = np.zeros_like(raw_symbols)
        ref = self.reference_map if self._modulation == 4 else None
        for k in range(len(raw_symbols)):
            rot = np.exp(-1j * self._phase)
            corrected = raw_symbols[k] * rot
            if raw_q is not None:
                corrected = complex(corrected.real, (raw_q[k] * rot).imag)
            symbols[k] = corrected
            # Phase error detector (modulation-dependent). atan2-based
            # detectors converge from any initial phase offset; the classic
            # sign(I)*Q detector stalls at its ±90° zero crossings.
            if self._modulation == 0:  # BPSK — squared loop
                sq = corrected ** 2
                phase_err = 0.5 * math.atan2(sq.imag, sq.real)
            elif self._modulation in (1, 3):  # QPSK / OQPSK — 4th power
                # The QPSK constellation sits at 45°+90k, so its own 4th
                # power is e^{jπ}. Removing that (negating) puts the loop's
                # stable point at zero phase error instead of at 45° —
                # without this the loop settles on the decision boundary.
                sq = -(corrected ** 4)
                phase_err = 0.25 * math.atan2(sq.imag, sq.real)
            elif self._modulation == 4:  # 16-APSK — decision-directed
                # The DVB-S2 two-ring constellation has no clean M-th-power
                # line (the 12-point outer ring smears a 4th-power detector),
                # so the loop is decision-directed against the ideal map.
                # Pull-in range is ±15°, which the Doppler pre-compensation
                # upstream comfortably provides; the residual 90° ambiguity
                # is resolved by the ASM rotation search.
                scaled = corrected * self._agc_gain
                nearest = ref[int(np.argmin(np.abs(ref - scaled)))]
                prod = scaled * np.conj(nearest)
                phase_err = math.atan2(prod.imag, prod.real)
            else:  # 8PSK and π/4-DQPSK (8PSK ring) — 8th power
                sq = corrected ** 8
                phase_err = 0.125 * math.atan2(sq.imag, sq.real)
            # 2nd order loop update
            self._freq += self._beta * phase_err
            self._phase += self._freq + self._alpha * phase_err
            self._phase %= (2 * math.pi)
        return symbols

    def _update_lock(self, symbols: np.ndarray) -> None:
        """Decision-directed EVM lock detector (works for all modulations)."""
        n_symbols = len(symbols)
        if n_symbols == 0:
            return
        if self._modulation == 0:  # BPSK — ideal points at ±1 on the real axis
            decided = np.sign(symbols.real)
            error = symbols - decided
        else:
            ref = self.reference_map
            nearest = np.array([ref[int(np.argmin(np.abs(ref - s)))]
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
        # matches the configured modulation. Prevents false lock when the
        # wrong modulation is configured (e.g. QPSK PLL locks on BPSK with
        # zero phase error because BPSK has no Q component).
        #
        # IMPORTANT: only validate AFTER sustained SNR lock, not during
        # acquisition — while the PLL converges the symbols rotate in the
        # I/Q plane and the ratio is ~1.0 regardless of modulation.
        mod_valid = True
        if snr_ok and self._energy_avg > 6.0 and n_symbols >= 20:
            mod_valid = self._validate_constellation(symbols)

        self.carrier_locked = snr_ok
        self.clock_locked = snr_ok
        if not mod_valid and snr_ok:
            logger.info("Constellation may not match configured modulation "
                        "(I/Q ratio check failed)")

    def _resolve_phase_ambiguity(self, result: bytes,
                                 symbols: np.ndarray) -> bytes:
        """PSK PLLs can lock at rotated phases: BPSK 180°, QPSK/OQPSK/APSK
        90°, 8PSK 45°. If the ASM does not appear, retry the constellation's
        candidate rotations."""
        asm = b'\x1A\xCF\xFC\x1D'
        if len(result) < 8:
            return result
        if any(result[i:i + 4] == asm for i in range(min(len(result) - 4, 200))):
            return result
        if self._modulation == 0:  # BPSK: 180°
            rotations = [math.pi]
        elif self._modulation in (1, 3, 4):  # QPSK/OQPSK/16-APSK: 90°
            rotations = [math.pi / 2, math.pi, 3 * math.pi / 2]
        else:  # 8PSK: 45° increments
            rotations = [k * math.pi / 4 for k in range(1, 8)]
        for rot in rotations:
            rotated_syms = symbols * np.exp(-1j * rot)
            rot_bits = self._symbols_to_bits(rotated_syms, update_state=False)
            n_b = len(rot_bits) // 8
            if n_b == 0:
                continue
            rot_bytes = bytes(np.packbits(rot_bits[:n_b * 8]))
            if any(rot_bytes[i:i + 4] == asm
                   for i in range(min(len(rot_bytes) - 4, 200))):
                self._phase += rot
                return rot_bytes
        return result

    # ── CPM (GMSK / GFSK) receiver ──

    def _demodulate_cpm(self, samples: np.ndarray) -> bytes:
        """Non-coherent CPM receiver: differential-phase discriminator
        followed by a symbol-period integrator.

        GMSK/GFSK carry data in the instantaneous *frequency*, so the
        matched-RRC + slicer path above is meaningless for them. Here the
        sample-to-sample phase advance recovers the frequency deviation;
        integrating it over one symbol period and taking the sign recovers
        the NRZ bit. This is phase-ambiguity free (no rotation search) and
        modulation-index independent, so it serves GMSK (h=0.5) and GFSK
        (configurable h) alike.
        """
        samples = self._precompensate(samples)
        sps = self.sps
        # One extra sample is needed for the first phase difference.
        if len(samples) < 2 * sps + 1:
            self._residual = samples
            return b''

        # Instantaneous frequency: arg(s[n] * conj(s[n-1]))
        freq = np.angle(samples[1:] * np.conj(samples[:-1]))

        # Integrate over one symbol period (boxcar matched to the NRZ pulse)
        window = np.ones(sps) / sps
        integrated = np.convolve(freq, window, mode='same')

        n_symbols = len(integrated) // sps
        if n_symbols == 0:
            self._residual = samples
            return b''
        used = n_symbols * sps
        # Keep the last sample so the next chunk's first difference is valid
        self._residual = samples[used:].copy()

        indices = np.arange(n_symbols) * sps
        decisions = integrated[indices]
        self._store_eye((integrated[:used] * sps).astype(np.complex64))

        # Lock/EVM: constant-envelope CPM — use the normalised decision
        # magnitude as the quality metric.
        mean_mag = float(np.mean(np.abs(decisions))) + 1e-12
        spread = float(np.mean((np.abs(decisions) - mean_mag) ** 2)) + 1e-12
        snr_est = (mean_mag ** 2) / spread
        if snr_est > self._energy_avg:
            self._energy_avg = 0.9 * self._energy_avg + 0.1 * snr_est
        else:
            self._energy_avg = 0.5 * self._energy_avg + 0.5 * snr_est
        self.carrier_locked = self._energy_avg > 3.0
        self.clock_locked = self.carrier_locked
        self.constellation_points = list(
            np.exp(1j * np.cumsum(decisions[-128:])).astype(np.complex64))

        new_bits = (decisions > 0).astype(np.uint8)
        if len(self._bit_buffer) > 0:
            all_bits = np.concatenate([self._bit_buffer, new_bits])
        else:
            all_bits = new_bits
        n_bytes = len(all_bits) // 8
        if n_bytes == 0:
            self._bit_buffer = all_bits
            return b''
        bits = all_bits[:n_bytes * 8]
        self._bit_buffer = all_bits[n_bytes * 8:]
        return bytes(np.packbits(bits))

    # ── Symbol → bit mapping ──

    def _symbols_to_bits(self, symbols: np.ndarray,
                         update_state: bool = True) -> np.ndarray:
        """Map demodulated complex symbols to bit decisions.

        Both sides of the link resolve their constellation through
        ``modulator.constellation_map()``, so the labelling (Gray or
        natural binary) can never disagree.
        """
        from .modulator import constellation_map, pi4_phase_changes

        if self._modulation == 0:  # BPSK: 1 bit per symbol
            return (symbols.real > 0).astype(np.uint8)

        if self._modulation == 5:  # π/4-DQPSK: differential detection
            return self._pi4_to_bits(symbols, pi4_phase_changes(self.gray_coding),
                                     update_state)

        if self._modulation not in self._BITS_PER_SYM:
            raise UnsupportedModulationError(
                f"No bit mapping for modulation {self._modulation}")

        ref = constellation_map(self._modulation, self.gray_coding)
        bps = self._BITS_PER_SYM[self._modulation]
        bits = np.zeros(len(symbols) * bps, dtype=np.uint8)
        for k, sym in enumerate(symbols):
            idx = int(np.argmin(np.abs(ref - sym)))
            for b in range(bps):
                bits[k * bps + b] = (idx >> (bps - 1 - b)) & 1
        return bits

    def _pi4_to_bits(self, symbols: np.ndarray, table: list,
                     update_state: bool) -> np.ndarray:
        """Differential detection for π/4-DQPSK.

        The dibit is recovered from the phase *change* between successive
        symbols, so no absolute carrier phase reference is needed — which
        is exactly why π/4-DQPSK is used on fading links.
        """
        bits = np.zeros(len(symbols) * 2, dtype=np.uint8)
        prev = self._pi4_prev
        table_arr = np.asarray(table)
        for k, sym in enumerate(symbols):
            if abs(sym) < 1e-12 or abs(prev) < 1e-12:
                delta = 0.0
            else:
                prod = sym * np.conj(prev)
                delta = math.atan2(prod.imag, prod.real)
            # Nearest tabulated phase change (wrapped difference)
            diff = np.angle(np.exp(1j * (table_arr - delta)))
            idx = int(np.argmin(np.abs(diff)))
            bits[k * 2] = (idx >> 1) & 1
            bits[k * 2 + 1] = idx & 1
            prev = sym
        if update_state:
            self._pi4_prev = prev
        return bits

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

        elif self._modulation in (2, 4, 5):  # 8PSK/16-APSK/π+4-DQPSK: balanced
            if iq_ratio > 3.0 or iq_ratio < 0.33:
                logger.debug("Constellation mismatch: mod %d but I/Q ratio=%.1f",
                             self._modulation, iq_ratio)
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
