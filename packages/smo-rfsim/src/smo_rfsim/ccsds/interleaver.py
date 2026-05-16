"""Block and convolutional interleaver/deinterleaver.

Spreads burst errors across multiple codewords so the FEC decoder
(RS or convolutional) can correct them. Essential when the channel
has correlated errors (fading, interference bursts).

Block interleaver: writes column-wise, reads row-wise (depth × width matrix)
Convolutional interleaver: Forney-style with incrementing delay lines

Reference: CCSDS 131.0-B-4 §6 (Interleaving)
"""

import numpy as np


class BlockInterleaver:
    """Fixed-depth block interleaver.

    Writes data into a matrix column-by-column, reads row-by-row.
    This spreads burst errors of up to `depth` symbols across
    `depth` different codewords.

    Parameters:
        depth: interleaving depth (number of rows)
    """

    def __init__(self, depth: int = 5):
        self.depth = max(1, depth)

    def interleave(self, data: bytes) -> bytes:
        """Interleave data bytes."""
        if self.depth <= 1:
            return data
        n = len(data)
        # Pad to fill complete matrix
        width = (n + self.depth - 1) // self.depth
        padded = bytearray(data) + bytearray(width * self.depth - n)
        # Write column-wise, read row-wise
        matrix = np.frombuffer(padded, dtype=np.uint8).reshape(self.depth, width)
        interleaved = matrix.T.flatten()[:n]
        return bytes(interleaved)

    def deinterleave(self, data: bytes) -> bytes:
        """Deinterleave data bytes (inverse operation)."""
        if self.depth <= 1:
            return data
        n = len(data)
        width = (n + self.depth - 1) // self.depth
        padded = bytearray(data) + bytearray(width * self.depth - n)
        # Inverse: write row-wise, read column-wise
        matrix = np.frombuffer(padded, dtype=np.uint8).reshape(width, self.depth)
        deinterleaved = matrix.T.flatten()[:n]
        return bytes(deinterleaved)


class ConvolutionalInterleaver:
    """Forney convolutional interleaver with incrementing delay lines.

    Each branch has delay = branch_index × unit_delay. This provides
    continuous interleaving without the block boundary latency.

    Parameters:
        branches: number of delay branches
        unit_delay: delay increment per branch (in symbols)
    """

    def __init__(self, branches: int = 5, unit_delay: int = 12):
        self.branches = max(1, branches)
        self.unit_delay = max(1, unit_delay)
        # Delay lines (ring buffers)
        self._interleave_lines = [
            [0] * (i * unit_delay) for i in range(branches)
        ]
        self._deinterleave_lines = [
            [0] * ((branches - 1 - i) * unit_delay) for i in range(branches)
        ]
        self._branch_idx = 0

    def interleave(self, data: bytes) -> bytes:
        """Interleave one block of data."""
        out = bytearray()
        for byte in data:
            line = self._interleave_lines[self._branch_idx]
            if len(line) > 0:
                out.append(line.pop(0))
                line.append(byte)
            else:
                out.append(byte)
            self._branch_idx = (self._branch_idx + 1) % self.branches
        return bytes(out)

    def deinterleave(self, data: bytes) -> bytes:
        """Deinterleave one block of data."""
        out = bytearray()
        for byte in data:
            line = self._deinterleave_lines[self._branch_idx]
            if len(line) > 0:
                out.append(line.pop(0))
                line.append(byte)
            else:
                out.append(byte)
            self._branch_idx = (self._branch_idx + 1) % self.branches
        return bytes(out)
