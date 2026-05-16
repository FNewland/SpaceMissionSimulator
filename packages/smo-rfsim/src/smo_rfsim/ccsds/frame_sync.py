"""TM Frame Synchronizer — ASM correlation state machine.

Locates frame boundaries in a continuous byte stream by correlating
the Attached Sync Marker (ASM) pattern. Implements the standard
three-state acquisition model: SEARCH → VERIFY → LOCK.

Reference: CCSDS 131.0-B-4 (TM Synchronization and Channel Coding)
"""

import logging
from enum import Enum
from typing import Optional

from .asm import ASM_BYTES, ASM_LENGTH, correlate_asm, correlate_asm_with_inversion

logger = logging.getLogger(__name__)


class SyncState(str, Enum):
    SEARCH = "SEARCH"    # no lock, scanning for ASM
    VERIFY = "VERIFY"    # found one ASM, checking next frame boundary
    LOCK = "LOCK"        # locked, expecting ASM at fixed intervals


class FrameSynchronizer:
    """Correlates ASM patterns to extract framed data from a byte stream.

    Parameters:
        frame_length: total frame size including ASM (e.g. 1115 + 4 = 1119)
        max_bit_errors: ASM bit-error tolerance for correlation
        verify_count: consecutive good frames needed to transition VERIFY→LOCK
        flywheel_count: consecutive missed frames before LOCK→SEARCH
    """

    def __init__(self, frame_length: int = 1115, max_bit_errors: int = 3,
                 verify_count: int = 3, flywheel_count: int = 8):
        self.frame_length_with_asm = frame_length + ASM_LENGTH
        self.frame_length = frame_length
        self.max_bit_errors = max_bit_errors
        self.verify_count = verify_count
        self.flywheel_count = flywheel_count

        self.state = SyncState.SEARCH
        self._buffer = bytearray()
        self._verify_hits = 0
        self._flywheel_misses = 0
        self._frame_offset: Optional[int] = None
        self.total_frames = 0
        self._inverted = False  # True if 180° BPSK phase ambiguity detected

    @property
    def flywheel_misses(self) -> int:
        """Current consecutive flywheel miss count (resets on good ASM or lock loss)."""
        return self._flywheel_misses

    def feed(self, data: bytes) -> list[bytes]:
        """Feed raw bytes and return any complete frames extracted (without ASM).

        If the stream is inverted (180° BPSK phase ambiguity), all frame
        data is bit-inverted before returning so upper layers see correct bytes.
        """
        self._buffer.extend(data)
        # Cap buffer to prevent unbounded growth during persistent ASM failure
        max_buf = self.frame_length_with_asm * 10
        if len(self._buffer) > max_buf:
            del self._buffer[:len(self._buffer) - max_buf]
        frames = []
        while True:
            frame = self._try_extract()
            if frame is None:
                break
            if self._inverted:
                frame = bytes(b ^ 0xFF for b in frame)
            frames.append(frame)
        return frames

    def _try_extract(self) -> Optional[bytes]:
        if self.state == SyncState.SEARCH:
            return self._state_search()
        elif self.state == SyncState.VERIFY:
            return self._state_verify()
        else:
            return self._state_lock()

    def _state_search(self) -> Optional[bytes]:
        """Scan for ASM (or inverted ASM) anywhere in the buffer.

        BPSK receivers can lock at 180° phase offset, inverting all bits.
        If the inverted ASM is found, we flag _inverted=True and invert
        all frame data on extraction so the upper layers see correct bytes.
        """
        pos, inverted = correlate_asm_with_inversion(
            bytes(self._buffer), 0, self.max_bit_errors)
        if pos < 0:
            # Keep last ASM_LENGTH-1 bytes in case ASM straddles feed boundary
            if len(self._buffer) > ASM_LENGTH:
                del self._buffer[:len(self._buffer) - ASM_LENGTH + 1]
            return None
        if inverted and not self._inverted:
            logger.info("Frame sync: detected inverted ASM (180° phase ambiguity)")
        self._inverted = inverted
        # Found a candidate ASM — strip everything up to and including ASM
        self._frame_offset = pos
        del self._buffer[:pos + ASM_LENGTH]
        self._pending_first_frame = True
        self.state = SyncState.VERIFY
        self._verify_hits = 0
        return self._state_verify()

    def _state_verify(self, first_frame: bool = False) -> Optional[bytes]:
        """Verify that ASM appears at expected interval.

        When _pending_first_frame is set, we need to extract the first frame
        (data immediately after the ASM we found in SEARCH) before looking
        for a second ASM.
        """
        if getattr(self, '_pending_first_frame', False):
            # Extract the first frame data (after the ASM found in SEARCH)
            if len(self._buffer) < self.frame_length:
                return None
            frame_data = bytes(self._buffer[:self.frame_length])
            del self._buffer[:self.frame_length]
            self._pending_first_frame = False
            self._verify_hits = 1
            self.total_frames += 1
            if self._verify_hits >= self.verify_count:
                self.state = SyncState.LOCK
                self._flywheel_misses = 0
            return frame_data

        if len(self._buffer) < ASM_LENGTH + self.frame_length:
            return None
        # Check for ASM (or inverted ASM) at position 0
        candidate = bytes(self._buffer[:ASM_LENGTH])
        pos, _ = correlate_asm_with_inversion(candidate, 0, self.max_bit_errors)
        if pos == 0:
            # ASM found at expected position
            del self._buffer[:ASM_LENGTH]
            frame_data = bytes(self._buffer[:self.frame_length])
            del self._buffer[:self.frame_length]
            self._verify_hits += 1
            self.total_frames += 1
            if self._verify_hits >= self.verify_count:
                self.state = SyncState.LOCK
                self._flywheel_misses = 0
                logger.info("Frame sync: LOCK acquired after %d frames",
                            self._verify_hits)
            return frame_data
        else:
            # ASM not found — back to search
            logger.debug("Frame sync: VERIFY failed, returning to SEARCH")
            self.state = SyncState.SEARCH
            self._verify_hits = 0
            return None

    def _state_lock(self) -> Optional[bytes]:
        """Extract frames at the locked cadence, with flywheel.

        Checks a small window around position 0 for the ASM to tolerate
        minor byte-alignment drift from the demodulator's variable-length
        output. Without this, even a single-byte timing slip triggers a
        flywheel miss, and 4 consecutive slips lose lock entirely.
        """
        if len(self._buffer) < ASM_LENGTH + self.frame_length:
            return None

        # Check for ASM (or inverted ASM) at position 0 first (fast path)
        candidate = bytes(self._buffer[:ASM_LENGTH])
        pos, _ = correlate_asm_with_inversion(candidate, 0, self.max_bit_errors)
        if pos == 0:
            del self._buffer[:ASM_LENGTH]
            frame_data = bytes(self._buffer[:self.frame_length])
            del self._buffer[:self.frame_length]
            self._flywheel_misses = 0
            self.total_frames += 1
            return frame_data

        # ASM not at expected position — search a small window (±2 bytes)
        # to re-align after demodulator timing drift.
        search_range = min(3, len(self._buffer) - ASM_LENGTH - self.frame_length)
        for offset in range(1, search_range + 1):
            candidate = bytes(self._buffer[offset:offset + ASM_LENGTH])
            pos_w, _ = correlate_asm_with_inversion(candidate, 0, self.max_bit_errors)
            if pos_w == 0:
                # Found ASM at a small offset — re-align
                logger.debug("Frame sync: ASM found at offset +%d, realigning",
                             offset)
                del self._buffer[:offset + ASM_LENGTH]
                frame_data = bytes(self._buffer[:self.frame_length])
                del self._buffer[:self.frame_length]
                self._flywheel_misses = 0
                self.total_frames += 1
                return frame_data

        # No ASM found in window — flywheel
        self._flywheel_misses += 1
        if self._flywheel_misses >= self.flywheel_count:
            logger.warning("Frame sync: LOCK lost after %d misses",
                           self._flywheel_misses)
            self.state = SyncState.SEARCH
            self._flywheel_misses = 0
            return None
        # Skip ASM-sized gap and extract frame anyway (flywheel)
        del self._buffer[:ASM_LENGTH]
        frame_data = bytes(self._buffer[:self.frame_length])
        del self._buffer[:self.frame_length]
        self.total_frames += 1
        return frame_data
