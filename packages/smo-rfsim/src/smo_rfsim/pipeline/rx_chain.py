"""Ground Station RX — continuous sample-to-packet receiver.

Runs in its own thread. Demodulates the continuous sample stream using
real carrier recovery (Costas PLL), clock recovery (Mueller & Muller),
then performs frame synchronization, Viterbi decoding, RS decoding,
and ECSS packet extraction.

The constellation tap, lock indicators, and BER all come from the
actual signal processing — not from formulas or synthetic data.

Includes two automatic recovery mechanisms:

  **Carrier auto-search** — if the PLL reports carrier lock but frame
  sync remains stuck in SEARCH for several seconds, the demodulated
  byte stream is not producing valid ASMs.  The receiver cycles through
  phase rotations (to resolve PSK ambiguity the ASM resolver missed)
  and, if that fails, tries adjacent modulation schemes.  Once frame
  sync acquires, the search stops.

  **Frame-to-carrier feedback** — if frame sync drops from LOCK back
  to SEARCH (flywheel exhausted), the PLL is reset to force a clean
  re-acquisition rather than continuing to free-run on a stale phase/
  frequency estimate.
"""

import logging
import math
import queue
import struct
import threading
import time
from typing import Callable, Optional

import numpy as np

from ..ccsds.asm import ASM_LENGTH
from ..ccsds.frame_sync import FrameSynchronizer, SyncState
from ..ccsds.reed_solomon import decode as rs_decode
from ..ccsds.tm_frame import TMFrameParser
from ..ccsds.viterbi import decode as viterbi_decode
from ..dsp.correlator_rx import CorrelatorRX

from .sample_buffer import SampleBuffer

logger = logging.getLogger(__name__)


class GroundStationRX(threading.Thread):
    """Continuous ground station receiver with real signal processing."""

    def __init__(self, frame_length: int = 1115, fecf_present: bool = True,
                 modulation: int = 0, sps: int = 8, rolloff: float = 0.35,
                 rs_enabled: bool = True, conv_enabled: bool = True,
                 input_buffer: SampleBuffer = None,
                 packet_callback: Callable[[bytes], None] = None,
                 loop_bw: float = 0.01):
        super().__init__(daemon=True, name="GroundStationRX")
        self._running = False

        # Demodulator (matched-filter correlator receiver)
        self._demod = CorrelatorRX(sps=sps, rolloff=rolloff, modulation=modulation)

        # Coding config
        self._rs_enabled = rs_enabled
        self._conv_enabled = conv_enabled
        self._frame_length = frame_length  # uncoded frame length

        # Compute coded frame length (what the frame sync sees between ASMs)
        coded_len = frame_length
        if rs_enabled:
            n_blocks = (frame_length + 222) // 223
            coded_len = n_blocks * 255
        if conv_enabled:
            coded_len = coded_len * 2 + 2  # rate 1/2 + flush

        # Frame synchronizer searches for ASM at coded frame intervals
        self._frame_sync = FrameSynchronizer(frame_length=coded_len)
        self._coded_frame_length = coded_len

        # Frame parser (operates on decoded frames)
        self._frame_parser = TMFrameParser(
            frame_length=frame_length, fecf_present=fecf_present)

        # I/O
        self._input = input_buffer or SampleBuffer(name="rx_in")
        self._packet_callback = packet_callback

        # Byte reassembly buffer (demod may produce partial bytes at boundaries)
        self._byte_buffer = bytearray()

        # Stats
        self.good_frames = 0
        self.bad_frames = 0
        self.packets_recovered = 0
        self.rs_failures = 0
        self.fecf_failures = 0
        self._lock = threading.Lock()

        # ── Carrier auto-search state ──
        # Phase nudge rotations to try for each modulation order
        self._modulation = modulation
        self._sps = sps
        self._rolloff = rolloff
        self._search_active = False
        self._search_deadline = 0.0        # monotonic time to next nudge
        self._search_phase_idx = 0         # index into phase rotation list
        self._search_mod_idx = 0           # index into modulation candidate list
        self._carrier_lock_since = 0.0     # when carrier first locked
        self._demod_active_since = 0.0     # when demod first produced bytes
        self._last_frame_sync_state = SyncState.SEARCH
        self.phase_nudges = 0              # count of nudges applied
        self.mod_searches = 0              # count of modulation trials
        self.pll_resets = 0                # count of frame→carrier resets

    @property
    def carrier_locked(self) -> bool:
        return self._demod.carrier_locked

    @property
    def clock_locked(self) -> bool:
        return self._demod.clock_locked

    @property
    def frame_locked(self) -> bool:
        return self._frame_sync.state == SyncState.LOCK

    @property
    def frame_sync_state(self) -> SyncState:
        return self._frame_sync.state

    @property
    def flywheel_misses(self) -> int:
        return self._frame_sync.flywheel_misses

    def get_constellation(self, max_points: int = 128) -> list[list[float]]:
        """Return recent demodulated I/Q points from the Costas loop output."""
        return self._demod.get_constellation_iq(max_points)

    @property
    def measured_ber(self) -> float:
        """Frame error rate as a proxy for BER."""
        with self._lock:
            total = self.good_frames + self.bad_frames
        if total == 0:
            return 0.0
        return self.bad_frames / total

    def reconfigure(self, modulation: int):
        """Change modulation scheme (resets PLL/clock and frame sync state).

        Only resets if the modulation actually changed — repeated calls
        with the same value are no-ops so we don't destroy an active
        frame sync lock.
        """
        if modulation == self._demod._modulation:
            return
        self._demod.set_modulation(modulation)
        # Reset frame sync — its buffer contains bytes demodulated
        # under the old modulation which would prevent lock.
        self._frame_sync = FrameSynchronizer(frame_length=self._coded_frame_length)

    def run(self):
        self._running = True
        logger.info("GroundStationRX started")
        try:
            self._run_loop()
        except Exception as e:
            logger.error("GroundStationRX crashed: %s", e, exc_info=True)
        finally:
            self._running = False

    # ── Auto-search configuration ──
    # Seconds with carrier lock but no frame sync before trying nudges
    _SEARCH_PATIENCE_S = 3.0
    # Seconds with no lock at all (despite signal) before trying mods
    _BLIND_PATIENCE_S = 5.0
    # Seconds to dwell on each phase rotation before trying the next
    _NUDGE_DWELL_S = 1.5
    # Seconds to dwell on each modulation candidate
    _MOD_DWELL_S = 2.5
    # Adjacent modulations to try (ordered by commonality)
    _MOD_CANDIDATES = [0, 1, 3, 2]  # BPSK, QPSK, OQPSK, 8PSK

    def _phase_rotations(self) -> list[float]:
        """Return candidate phase rotations for the current modulation."""
        mod = self._demod._modulation
        if mod == 0:       # BPSK: 180°
            return [math.pi]
        elif mod in (1, 3): # QPSK/OQPSK: 90°, 180°, 270°
            return [math.pi / 2, math.pi, 3 * math.pi / 2]
        elif mod == 2:      # 8PSK: 45° increments
            return [k * math.pi / 4 for k in range(1, 8)]
        return [math.pi]    # default: try inversion

    def _run_loop(self):
        self._demod_byte_count = 0
        self._carrier_lock_since = 0.0
        prev_frame_state = SyncState.SEARCH

        while self._running:
            try:
                samples = self._input.get(timeout=0.1)
            except queue.Empty:
                # Even when idle, run the auto-search timer checks
                self._check_auto_search()
                continue

            # 1. Demodulate: AGC → matched filter → Costas PLL → M&M → bit decisions
            recovered_bytes = self._demod.demodulate(samples)
            if not recovered_bytes:
                continue

            # Track that the demod is producing bytes (signal present)
            if self._demod_active_since == 0.0:
                self._demod_active_since = time.monotonic()

            # Log demod output periodically for diagnostics
            self._demod_byte_count += len(recovered_bytes)
            if self._demod_byte_count % 10000 < len(recovered_bytes):
                logger.info("RX demod: %d total bytes, carrier=%s, frame_sync=%s",
                            self._demod_byte_count,
                            self._demod.carrier_locked,
                            self._frame_sync.state.value)

            # 2. Feed into frame synchronizer (ASM correlation on recovered bytes)
            raw_frames = self._frame_sync.feed(recovered_bytes)

            for raw_frame in raw_frames:
                try:
                    self._process_frame(raw_frame)
                except Exception as e:
                    logger.warning("RX frame processing error: %s", e)
                    with self._lock:
                        self.bad_frames += 1

            # ── Frame-to-carrier feedback ──
            # If frame sync just dropped from LOCK to SEARCH, the
            # demodulated stream has become unusable.  Reset the PLL
            # to force a clean re-acquisition instead of letting it
            # free-run on a stale estimate.
            cur_state = self._frame_sync.state
            if prev_frame_state == SyncState.LOCK and cur_state == SyncState.SEARCH:
                logger.warning("Frame sync lost LOCK → SEARCH, resetting PLL "
                               "for re-acquisition")
                self._demod.reset_acquisition()
                self._frame_sync = FrameSynchronizer(
                    frame_length=self._coded_frame_length)
                self.pll_resets += 1
                self._search_active = False
                self._carrier_lock_since = 0.0
            prev_frame_state = cur_state

            # ── Carrier auto-search ──
            self._check_auto_search()

    def _check_auto_search(self):
        """Carrier auto-search: nudge phase or try adjacent modulations.

        Called every iteration of the run loop.  Two trigger paths:

        **Path A — carrier locked, no frame sync** (phase ambiguity):
          After _SEARCH_PATIENCE_S seconds, cycle through phase rotations
          for the current modulation, then try adjacent modulations.

        **Path B — no lock at all despite signal** (wrong modulation):
          The PLL can't lock because the modulation order is wrong (e.g.
          QPSK configured but BPSK transmitted). After _BLIND_PATIENCE_S
          seconds of demod producing bytes with neither carrier nor frame
          lock, skip phase nudges and go straight to modulation search.

        In both paths, if a change produces frame sync (VERIFY or LOCK),
        the search stops.
        """
        now = time.monotonic()
        fs = self._frame_sync.state

        # ── Happy path: frame sync is working ──
        if fs in (SyncState.LOCK, SyncState.VERIFY):
            if self._search_active:
                logger.info("Auto-search: frame sync acquired (%s), "
                            "search cancelled after %d nudges, %d mod trials",
                            fs.value, self.phase_nudges, self.mod_searches)
                self._search_active = False
            self._carrier_lock_since = 0.0
            self._demod_active_since = 0.0
            return

        # ── Determine which trigger path applies ──
        has_carrier = self._demod.carrier_locked
        has_signal = self._demod_active_since > 0.0

        if has_carrier:
            # Path A: carrier locked but no frame sync
            if self._carrier_lock_since == 0.0:
                self._carrier_lock_since = now
            patience = self._SEARCH_PATIENCE_S
            trigger_time = self._carrier_lock_since
            skip_phase_nudges = False
        elif has_signal:
            # Path B: signal present but no carrier lock at all
            patience = self._BLIND_PATIENCE_S
            trigger_time = self._demod_active_since
            skip_phase_nudges = True   # phase nudges are pointless without carrier
        else:
            # No signal — nothing to search
            self._carrier_lock_since = 0.0
            self._search_active = False
            return

        waiting_for = now - trigger_time
        if waiting_for < patience:
            return  # give the normal path more time

        # ── Start or continue auto-search ──
        if not self._search_active:
            if skip_phase_nudges:
                logger.info("Auto-search: signal present for %.1fs but no "
                            "carrier lock — trying modulation search",
                            waiting_for)
                # Jump past phase nudges
                self._search_phase_idx = 999
            else:
                logger.info("Auto-search: carrier locked for %.1fs but frame "
                            "sync stuck in %s — starting phase search",
                            waiting_for, fs.value)
                self._search_phase_idx = 0
            self._search_active = True
            self._search_mod_idx = 0
            self._search_deadline = now  # try first action immediately

        if now < self._search_deadline:
            return  # dwell period not elapsed yet

        # ── Phase A: try phase rotations on current modulation ──
        rotations = self._phase_rotations()
        if self._search_phase_idx < len(rotations):
            rot = rotations[self._search_phase_idx]
            logger.info("Auto-search: phase nudge %.0f° (attempt %d/%d)",
                        math.degrees(rot), self._search_phase_idx + 1,
                        len(rotations))
            self._demod.nudge_phase(rot)
            # Clear frame sync buffer — old bytes are from old phase
            self._frame_sync = FrameSynchronizer(
                frame_length=self._coded_frame_length)
            self._search_phase_idx += 1
            self._search_deadline = now + self._NUDGE_DWELL_S
            self.phase_nudges += 1
            return

        # ── Phase B: try adjacent modulation schemes ──
        candidates = [m for m in self._MOD_CANDIDATES
                      if m != self._demod._modulation]
        if self._search_mod_idx < len(candidates):
            new_mod = candidates[self._search_mod_idx]
            from ..dsp.modulator import MOD_NAMES
            logger.info("Auto-search: trying modulation %s (attempt %d/%d)",
                        MOD_NAMES.get(new_mod, str(new_mod)),
                        self._search_mod_idx + 1, len(candidates))
            self._demod.set_modulation(new_mod)
            self._frame_sync = FrameSynchronizer(
                frame_length=self._coded_frame_length)
            self._search_mod_idx += 1
            self._search_deadline = now + self._MOD_DWELL_S
            self.mod_searches += 1
            return

        # ── All candidates exhausted — reset PLL and start over ──
        logger.warning("Auto-search: all phase rotations and modulation "
                       "candidates exhausted — resetting PLL and restarting")
        self._demod.reset_acquisition()
        self._frame_sync = FrameSynchronizer(
            frame_length=self._coded_frame_length)
        self._search_active = False
        self._carrier_lock_since = 0.0
        self._demod_active_since = 0.0
        self.pll_resets += 1

    def _process_frame(self, raw_frame: bytes) -> None:
        """Process a synchronized frame through FEC decoding and packet extraction."""
        frame_data = raw_frame

        # 3. Viterbi decode (if convolutional coding was applied on TX)
        if self._conv_enabled:
            # Original frame length (with RS parity if enabled) determines output size.
            orig_len = self._frame_length
            if self._rs_enabled:
                # RS adds 32 parity bytes per 223-byte block
                n_blocks = (self._frame_length + 222) // 223
                orig_len = self._frame_length + n_blocks * 32
            frame_data = viterbi_decode(frame_data, original_length=orig_len)

        # 4. RS decode in 255-byte blocks (matching TX encoding)
        if self._rs_enabled:
            rs_out = bytearray()
            ok = True
            for i in range(0, len(frame_data), 255):
                block = frame_data[i:i + 255]
                if len(block) < 33:  # too short for RS
                    rs_out.extend(block)
                    continue
                decoded = rs_decode(block)
                if decoded is None:
                    ok = False
                    break
                rs_out.extend(decoded)
            if not ok:
                with self._lock:
                    self.bad_frames += 1
                    self.rs_failures += 1
                return
            frame_data = bytes(rs_out)

        # 5. Parse TM frame (validate FECF)
        parsed = self._frame_parser.parse_frame(frame_data)
        if parsed is None:
            with self._lock:
                self.bad_frames += 1
                self.fecf_failures += 1
            return

        with self._lock:
            self.good_frames += 1

        # 6. Extract ECSS packets
        if not parsed.is_idle:
            packets = self._frame_parser.extract_packets(parsed)
            for pkt in packets:
                self.packets_recovered += 1
                if self._packet_callback:
                    self._packet_callback(pkt)

    def stop(self):
        self._running = False
