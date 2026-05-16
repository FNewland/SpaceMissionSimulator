"""Thread-safe sample buffer for passing complex64 chunks between stages."""

import logging
import queue
import numpy as np

logger = logging.getLogger(__name__)


class SampleBuffer:
    """Fixed-depth queue of numpy complex64 arrays.

    Used to pass baseband samples between the TX, channel, and RX
    threads. If the buffer overflows (consumer too slow), the oldest
    chunk is dropped — this models real-time behaviour where you
    can't pause the RF link.
    """

    def __init__(self, max_depth: int = 32, name: str = ""):
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_depth)
        self.name = name
        self.total_samples = 0
        self.overflow_count = 0

    def put(self, samples: np.ndarray) -> None:
        """Enqueue a chunk of complex64 samples."""
        try:
            self._q.put_nowait(samples)
            self.total_samples += len(samples)
        except queue.Full:
            # Drop oldest to make room (real-time: can't stall TX)
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(samples)
                self.total_samples += len(samples)
            except queue.Full:
                pass
            self.overflow_count += 1
            if self.overflow_count % 10 == 1:
                logger.warning("SampleBuffer[%s]: overflow #%d (depth=%d)",
                               self.name, self.overflow_count,
                               self._q.maxsize)

    def get(self, timeout: float = 0.1) -> np.ndarray:
        """Dequeue a sample chunk. Raises queue.Empty on timeout."""
        return self._q.get(timeout=timeout)

    def get_nowait(self) -> np.ndarray:
        """Non-blocking get. Raises queue.Empty if empty."""
        return self._q.get_nowait()

    def drain(self) -> np.ndarray:
        """Drain all available chunks and concatenate. Returns empty array if none."""
        chunks = []
        while True:
            try:
                chunks.append(self._q.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return np.array([], dtype=np.complex64)
        return np.concatenate(chunks)

    @property
    def depth(self) -> int:
        return self._q.qsize()

    @property
    def empty(self) -> bool:
        return self._q.empty()
