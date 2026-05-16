"""Radio front-end status engine.

Purely observational — tracks RF link indicators as reported by the
spacecraft downlink and ground station receiver chain.
"""

import math
import random
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class LockState(str, Enum):
    LOCKED = "LOCKED"
    ACQUIRING = "ACQUIRING"
    UNLOCKED = "UNLOCKED"


class LEDColor(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


@dataclass
class RadioStatus:
    """Current front-end status snapshot."""
    # Lock indicators
    carrier_lock: LockState = LockState.UNLOCKED
    bit_sync: LockState = LockState.UNLOCKED
    frame_sync: LockState = LockState.UNLOCKED
    # RF measurements
    rssi_dbm: float = -120.0
    eb_n0_db: float = 0.0
    ber_log10: float = -1.0
    link_margin_db: float = 0.0
    # Dynamics
    doppler_hz: float = 0.0
    range_km: float = 0.0
    data_rate_kbps: float = 32.0
    # Frame counters
    good_frames: int = 0
    bad_frames: int = 0
    # VC activity (last frame time per VCID)
    vc_active: dict[int, float] = field(default_factory=dict)
    # TC/CLTU status
    cltu_sent: int = 0
    cltu_acked: int = 0
    # Operating mode
    mode: str = "PACKET"
    # Modulation scheme (0=BPSK, 1=QPSK, 2=8PSK, 3=OQPSK)
    modulation: int = 0
    modulation_name: str = "BPSK"
    # Constellation I/Q samples (baseband observation)
    iq_samples: list[list[float]] = field(default_factory=list)
    # Ground segment Eb/N0 penalty from RF chain degradation (dB)
    gs_penalty_db: float = 0.0
    # Timestamp
    timestamp: float = 0.0

    def carrier_led(self) -> LEDColor:
        if self.carrier_lock == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.carrier_lock == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def bit_sync_led(self) -> LEDColor:
        if self.bit_sync == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.bit_sync == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def frame_sync_led(self) -> LEDColor:
        if self.frame_sync == LockState.LOCKED:
            return LEDColor.GREEN
        elif self.frame_sync == LockState.ACQUIRING:
            return LEDColor.YELLOW
        return LEDColor.RED

    def vc_led(self, vcid: int, stale_seconds: float = 5.0) -> LEDColor:
        last = self.vc_active.get(vcid, 0)
        age = self.timestamp - last
        if age < stale_seconds:
            return LEDColor.GREEN
        elif age < stale_seconds * 3:
            return LEDColor.YELLOW
        return LEDColor.RED


class RadioFrontend:
    """Aggregates status from the bridge processing chain.

    This is a read-only observation point. It does not inject failures
    or modify the signal chain. All it does is display what the ground
    station receiver sees.
    """

    IQ_BUFFER_SIZE = 128

    # Ideal constellation reference points per modulation
    _CONSTELLATIONS = {
        0: [(-1.0, 0.0), (1.0, 0.0)],  # BPSK
        1: [(0.707, 0.707), (-0.707, 0.707),
            (-0.707, -0.707), (0.707, -0.707)],  # QPSK
        2: [(1.0, 0.0), (0.707, 0.707), (0.0, 1.0), (-0.707, 0.707),
            (-1.0, 0.0), (-0.707, -0.707), (0.0, -1.0), (0.707, -0.707)],  # 8PSK
        3: [(0.707, 0.707), (-0.707, 0.707),
            (-0.707, -0.707), (0.707, -0.707)],  # OQPSK
    }
    _MOD_NAMES = {0: "BPSK", 1: "QPSK", 2: "8PSK", 3: "OQPSK"}

    def __init__(self):
        self.status = RadioStatus()
        self._rng = random.Random(42)

    def update_frame_counts(self, good: int, bad: int):
        self.status.good_frames = good
        self.status.bad_frames = bad

    def update_rf(self, eb_n0_db: float, doppler_hz: float = 0.0,
                  range_km: float = 0.0, rssi_dbm: float = -80.0):
        self.status.eb_n0_db = eb_n0_db
        self.status.doppler_hz = doppler_hz
        self.status.range_km = range_km
        self.status.rssi_dbm = rssi_dbm
        if eb_n0_db > 0:
            ber = max(1e-12, 0.5 * math.erfc(math.sqrt(10 ** (eb_n0_db / 10.0))))
            self.status.ber_log10 = math.log10(ber) if ber > 0 else -12.0
        else:
            self.status.ber_log10 = -1.0  # no meaningful BER
        self.status.link_margin_db = eb_n0_db - 9.6

    def update_lock(self, carrier: LockState, bit_sync: LockState,
                    frame_sync: LockState):
        self.status.carrier_lock = carrier
        self.status.bit_sync = bit_sync
        self.status.frame_sync = frame_sync

    def update_vc_activity(self, vcid: int):
        self.status.vc_active[vcid] = time.time()

    def update_cltu(self, sent: int, acked: int):
        self.status.cltu_sent = sent
        self.status.cltu_acked = acked

    def update_modulation(self, mod_mode: int):
        self.status.modulation = mod_mode
        self.status.modulation_name = self._MOD_NAMES.get(mod_mode, "BPSK")

    def update_gs_penalty(self, penalty_db: float):
        self.status.gs_penalty_db = penalty_db

    def snapshot(self) -> RadioStatus:
        """Return current status. I/Q samples are set by the bridge from
        the DSP processor — they are real demodulated samples, not synthetic."""
        self.status.timestamp = time.time()
        return self.status
