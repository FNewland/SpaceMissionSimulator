"""TC Communications Link Transmission Unit (CLTU) encoder/decoder.

A CLTU wraps a TC Transfer Frame for uplink transmission:
  [Start Sequence (2 bytes: 0xEB90)]
  [BCH Code Block 1 (8 bytes)]
  [BCH Code Block 2 (8 bytes)]
  ...
  [BCH Code Block N (8 bytes)]
  [Tail Sequence (8 bytes: 0xC5C5C5C5C5C5C579)]

Reference: CCSDS 232.0-B-4 (TC Space Data Link Protocol)
"""

import logging
from typing import Optional

from .bch import encode_code_block, check_code_block, correct_code_block

logger = logging.getLogger(__name__)

START_SEQUENCE = b'\xEB\x90'
TAIL_SEQUENCE = b'\xC5\xC5\xC5\xC5\xC5\xC5\xC5\x79'
CODE_BLOCK_DATA_SIZE = 7
CODE_BLOCK_SIZE = 8


def encode_cltu(tc_frame: bytes) -> bytes:
    """Encode a TC Transfer Frame into a CLTU.

    Pads the frame to a multiple of 7 bytes, encodes each 7-byte
    block with BCH(64,56), then wraps with start/tail sequences.
    """
    # Pad to multiple of 7 bytes with 0x55 fill
    padded = tc_frame
    remainder = len(padded) % CODE_BLOCK_DATA_SIZE
    if remainder:
        padded = padded + b'\x55' * (CODE_BLOCK_DATA_SIZE - remainder)

    cltu = bytearray(START_SEQUENCE)
    for i in range(0, len(padded), CODE_BLOCK_DATA_SIZE):
        block_data = padded[i:i + CODE_BLOCK_DATA_SIZE]
        cltu.extend(encode_code_block(block_data))
    cltu.extend(TAIL_SEQUENCE)
    return bytes(cltu)


def decode_cltu(cltu: bytes, correct_errors: bool = True) -> Optional[bytes]:
    """Decode a CLTU back to the TC Transfer Frame.

    Validates start/tail sequences, decodes BCH code blocks,
    and optionally corrects single-bit errors.

    Returns the TC frame bytes or None if decoding fails.
    """
    if len(cltu) < len(START_SEQUENCE) + CODE_BLOCK_SIZE + len(TAIL_SEQUENCE):
        logger.warning("CLTU too short: %d bytes", len(cltu))
        return None

    if cltu[:2] != START_SEQUENCE:
        logger.warning("CLTU start sequence mismatch")
        return None

    if cltu[-8:] != TAIL_SEQUENCE:
        logger.warning("CLTU tail sequence mismatch")
        return None

    # Extract code blocks (between start and tail)
    payload = cltu[2:-8]
    if len(payload) % CODE_BLOCK_SIZE != 0:
        logger.warning("CLTU payload not a multiple of %d bytes", CODE_BLOCK_SIZE)
        return None

    tc_frame = bytearray()
    total_corrections = 0
    for i in range(0, len(payload), CODE_BLOCK_SIZE):
        block = payload[i:i + CODE_BLOCK_SIZE]
        if correct_errors:
            data, valid, corrections = correct_code_block(block)
            total_corrections += corrections
        else:
            data, valid = check_code_block(block)
        if not valid:
            logger.warning("CLTU code block %d failed BCH check", i // CODE_BLOCK_SIZE)
            return None
        tc_frame.extend(data)

    if total_corrections > 0:
        logger.info("CLTU decoded with %d single-bit corrections", total_corrections)

    return bytes(tc_frame)
