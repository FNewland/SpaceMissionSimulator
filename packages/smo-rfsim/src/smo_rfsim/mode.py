"""RF simulation operating modes."""

from enum import Enum


class RFSimMode(str, Enum):
    """Bridge operating mode.

    PACKET — transparent TCP relay (current behavior, no framing)
    FRAME  — CCSDS Transfer Framing with pure-Python BER injection
    RF     — full GNU Radio BPSK modulation/demodulation chain
    """
    PACKET = "PACKET"
    FRAME = "FRAME"
    RF = "RF"
