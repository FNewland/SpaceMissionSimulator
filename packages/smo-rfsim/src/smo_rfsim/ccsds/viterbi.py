"""Viterbi decoder for rate-1/2, constraint length K=7 convolutional code.

Pure-Python implementation using the standard add-compare-select (ACS)
trellis decoding algorithm. Matches the encoder in convolutional.py.

Generator polynomials: G1 = 0x79, G2 = 0x5B (octal 171, 133).

Reference: CCSDS 131.0-B-4 (TM Synchronization and Channel Coding)
"""

from .convolutional import G1, G2, K, _parity

NUM_STATES = 1 << (K - 1)  # 64 states
_INF = float('inf')

# Precompute expected outputs for each (state, input_bit) transition
# output[state][input_bit] = (g1_bit, g2_bit)
_EXPECTED_OUTPUT: list[list[tuple[int, int]]] = []
for state in range(NUM_STATES):
    row = []
    for input_bit in range(2):
        reg = ((state << 1) | input_bit) & 0x7F
        g1 = _parity(reg & G1)
        g2 = _parity(reg & G2)
        row.append((g1, g2))
    _EXPECTED_OUTPUT.append(row)

# Precompute next-state table
_NEXT_STATE: list[list[int]] = []
for state in range(NUM_STATES):
    row = []
    for input_bit in range(2):
        row.append(((state << 1) | input_bit) & (NUM_STATES - 1))
    _NEXT_STATE.append(row)


def _branch_metric(received: tuple[int, int],
                    expected: tuple[int, int]) -> int:
    """Hamming distance between received and expected symbol pair."""
    return (received[0] ^ expected[0]) + (received[1] ^ expected[1])


def decode(encoded: bytes, original_length: int | None = None) -> bytes:
    """Viterbi decode a convolutionally encoded byte sequence.

    Args:
        encoded: The encoded bytes (rate 1/2: 2 output bits per input bit).
        original_length: If known, the number of original data bytes.
            Used to strip the K-1 flush bits from the output.

    Returns:
        The decoded data bytes.
    """
    # Unpack encoded bytes into bit pairs (symbols)
    symbols: list[tuple[int, int]] = []
    bits = []
    for byte in encoded:
        for bit_pos in range(7, -1, -1):
            bits.append((byte >> bit_pos) & 1)
    # Group into pairs
    for i in range(0, len(bits) - 1, 2):
        symbols.append((bits[i], bits[i + 1]))

    n_symbols = len(symbols)
    if n_symbols == 0:
        return b''

    # Path metrics: cost to reach each state
    path_metric = [_INF] * NUM_STATES
    path_metric[0] = 0  # start in state 0

    # Survivor paths: for each (time, state), store the input bit taken
    # Use a flat list for efficiency
    survivors: list[list[int]] = [[] for _ in range(NUM_STATES)]

    for t in range(n_symbols):
        new_metric = [_INF] * NUM_STATES
        new_survivors: list[list[int]] = [[] for _ in range(NUM_STATES)]

        for state in range(NUM_STATES):
            if path_metric[state] == _INF:
                continue
            for input_bit in range(2):
                next_state = _NEXT_STATE[state][input_bit]
                expected = _EXPECTED_OUTPUT[state][input_bit]
                bm = _branch_metric(symbols[t], expected)
                candidate = path_metric[state] + bm
                if candidate < new_metric[next_state]:
                    new_metric[next_state] = candidate
                    new_survivors[next_state] = survivors[state] + [input_bit]

        path_metric = new_metric
        survivors = new_survivors

    # Find best final state (should be state 0 after flush bits)
    best_state = 0
    best_metric = path_metric[0]
    for state in range(1, NUM_STATES):
        if path_metric[state] < best_metric:
            best_metric = path_metric[state]
            best_state = state

    decoded_bits = survivors[best_state]

    # Strip K-1 flush bits
    if original_length is not None:
        data_bits = original_length * 8
        decoded_bits = decoded_bits[:data_bits]
    elif len(decoded_bits) > K - 1:
        decoded_bits = decoded_bits[:-(K - 1)]

    # Pack bits into bytes
    out = bytearray()
    for i in range(0, len(decoded_bits), 8):
        byte_val = 0
        for j in range(8):
            if i + j < len(decoded_bits):
                byte_val = (byte_val << 1) | decoded_bits[i + j]
            else:
                byte_val <<= 1
        out.append(byte_val)

    return bytes(out)
