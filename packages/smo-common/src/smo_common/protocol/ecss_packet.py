"""SMO Common — ECSS/CCSDS Packet Handling.

Implements CCSDS Space Packet primary header, ECSS PUS-C secondary header,
CRC-16/CCITT-FALSE, and packet parsing/building utilities.
"""
import struct
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Optional, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mission time epoch
# ---------------------------------------------------------------------------
# The single epoch on the wire is J2000.0 = 2000-01-01T12:00:00 UTC.
#
# The authoritative source is ``mission.yaml time_epoch``; this literal is only
# the FALLBACK used before any mission config has been loaded (and by tools that
# never load one).  ``smo_common.config.loader.load_mission_config`` calls
# :func:`set_time_epoch` as its explicit initialisation hook, so this low-level
# protocol module never imports the config layer (no import cycle, and
# ``ecss_packet`` stays usable with no config on disk at all).
#
# ``tests/test_common/test_protocol.py`` gates that the YAML and this literal
# agree, so a divergence is a test failure rather than a silent wire change.
TIME_EPOCH_DEFAULT = datetime(2000, 1, 1, 12, 0, 0)

#: Current mission epoch.  Rebound by :func:`set_time_epoch`.  Prefer
#: :func:`get_time_epoch` over importing this name by value — a
#: ``from ... import TIME_EPOCH`` captures whatever was current at import time.
TIME_EPOCH = TIME_EPOCH_DEFAULT

#: CUC fine-time resolution: sub-second in units of 1/65536 s (~15.26 us).
CUC_FINE_TICKS = 65536

#: The coarse field is a uint32 on the wire.
CUC_COARSE_MAX = 0xFFFFFFFF

#: Wire size of a packed CUC (uint32 coarse + uint16 fine).
CUC_SIZE = 6

#: Wire size of the PUS TM secondary header: 3 header octets + CUC.
TM_SEC_HDR_LEN = 3 + CUC_SIZE  # 9

#: Wire size of the PUS TC secondary header (no time field).
TC_SEC_HDR_LEN = 3


def get_time_epoch() -> datetime:
    """Return the mission epoch currently in force (naive UTC)."""
    return TIME_EPOCH


def set_time_epoch(epoch: Union[datetime, str, None]) -> datetime:
    """Set the mission epoch used by every CUC conversion in this process.

    This is the explicit initialisation hook called by the config loader with
    ``mission.yaml time_epoch``.  ``None`` (or an empty string) restores the
    fallback literal.  Accepts an ISO-8601 string or a ``datetime``; a
    timezone-aware datetime is converted to UTC and stored naive.

    Returns the epoch that is now in force.
    """
    global TIME_EPOCH
    if epoch is None or (isinstance(epoch, str) and not epoch.strip()):
        TIME_EPOCH = TIME_EPOCH_DEFAULT
        return TIME_EPOCH
    if isinstance(epoch, str):
        parsed = datetime.fromisoformat(epoch.strip().replace("Z", "+00:00"))
    elif isinstance(epoch, datetime):
        parsed = epoch
    else:
        raise TypeError(f"time epoch must be a datetime or ISO-8601 string, got {type(epoch)!r}")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    TIME_EPOCH = parsed
    return TIME_EPOCH


def reset_time_epoch() -> datetime:
    """Restore the fallback epoch literal. Mainly for tests."""
    return set_time_epoch(None)


class PacketType(IntEnum):
    TM = 0
    TC = 1


class PUSService(IntEnum):
    VERIFICATION = 1
    DEVICE_ACCESS = 2
    HOUSEKEEPING = 3
    PARAMETER_STATISTICS = 4
    EVENT_REPORTING = 5
    MEMORY_MANAGEMENT = 6
    FUNCTION_MANAGEMENT = 8
    TIME_MANAGEMENT = 9
    ON_BOARD_MONITORING = 12
    LARGE_DATA_TRANSFER = 13
    RT_FORWARDING = 14
    ON_BOARD_STORAGE = 15
    TEST = 17
    EVENT_ACTION = 19
    PARAMETER_MANAGEMENT = 20
    SCHEDULING = 11
    FILE_MANAGEMENT = 23


class EventSeverity(IntEnum):
    INFORMATIONAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4


@dataclass
class CucTime:
    """ECSS CUC time: ``coarse`` seconds + ``fine`` 1/65536 s ticks.

    Wire format (6 octets, big-endian)::

        coarse : uint32   seconds since the mission epoch (J2000)
        fine   : uint16   sub-second in units of 1/65536 s (~15.26 us)

    ``fine`` is always normalised into ``[0, CUC_FINE_TICKS)`` by carrying whole
    seconds into ``coarse``, so ``CucTime(5, 65536) == CucTime(6, 0)``.
    ``coarse`` is allowed to be negative in memory (times before the epoch) so
    that arithmetic and :meth:`to_seconds` stay exact; :meth:`pack` is what
    enforces the uint32 wire range.
    """

    coarse: int = 0
    fine: int = 0

    def __post_init__(self) -> None:
        coarse = int(self.coarse)
        fine = int(self.fine)
        carry, fine = divmod(fine, CUC_FINE_TICKS)  # floor division: fine >= 0
        self.coarse = coarse + carry
        self.fine = fine

    # ── conversions ─────────────────────────────────────────────────

    def to_seconds(self) -> float:
        """Full-precision seconds since the mission epoch."""
        return self.coarse + self.fine / CUC_FINE_TICKS

    @classmethod
    def from_seconds(cls, seconds: float) -> "CucTime":
        """Build from full-precision seconds since the mission epoch.

        Uses floor semantics, so ``fine`` is never negative and
        ``from_seconds(x).to_seconds()`` round-trips to within one tick.
        """
        seconds = float(seconds)
        coarse = int(seconds // 1)
        fine = int(round((seconds - coarse) * CUC_FINE_TICKS))
        return cls(coarse, fine)  # __post_init__ carries fine == 65536

    def to_datetime(self) -> datetime:
        """Naive UTC datetime for this CUC, on the current mission epoch."""
        return get_time_epoch() + timedelta(seconds=self.to_seconds())

    @classmethod
    def from_datetime(cls, dt: datetime) -> "CucTime":
        """Build from a UTC datetime (aware or naive) on the current epoch."""
        return cls.from_seconds(_datetime_to_epoch_seconds(dt))

    # ── wire ────────────────────────────────────────────────────────

    def pack(self) -> bytes:
        """Serialise to the 6-octet big-endian wire form."""
        if not 0 <= self.coarse <= CUC_COARSE_MAX:
            raise ValueError(
                f"CUC coarse {self.coarse} outside the uint32 wire range "
                f"[0, {CUC_COARSE_MAX}] (times before the mission epoch, or "
                f"beyond it by ~136 years, are not representable on the wire)"
            )
        return struct.pack('>IH', self.coarse, self.fine)

    @classmethod
    def unpack(cls, data: bytes) -> "CucTime":
        """Parse the 6-octet big-endian wire form."""
        if len(data) < CUC_SIZE:
            raise ValueError(f"CUC requires {CUC_SIZE} bytes, got {len(data)}")
        coarse, fine = struct.unpack('>IH', data[:CUC_SIZE])
        return cls(coarse, fine)

    def as_tuple(self) -> tuple[int, int]:
        return (self.coarse, self.fine)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.coarse}.{self.fine:05d}"


@dataclass
class PrimaryHeader:
    """CCSDS Space Packet Primary Header (6 bytes)."""
    version: int = 0
    packet_type: int = 0  # 0=TM, 1=TC
    sec_hdr_flag: int = 1
    apid: int = 1
    sequence_flags: int = 3  # 3 = standalone
    sequence_count: int = 0
    data_length: int = 0

    def pack(self) -> bytes:
        packet_id = (self.version << 13) | (self.packet_type << 12) | (self.sec_hdr_flag << 11) | (self.apid & 0x7FF)
        seq_ctrl = (self.sequence_flags << 14) | (self.sequence_count & 0x3FFF)
        return struct.pack('>HHH', packet_id, seq_ctrl, self.data_length)

    @classmethod
    def unpack(cls, data: bytes) -> 'PrimaryHeader':
        if len(data) < 6:
            raise ValueError("Primary header requires 6 bytes")
        word0, word1, data_length = struct.unpack('>HHH', data[:6])
        return cls(
            version=(word0 >> 13) & 0x07,
            packet_type=(word0 >> 12) & 0x01,
            sec_hdr_flag=(word0 >> 11) & 0x01,
            apid=word0 & 0x7FF,
            sequence_flags=(word1 >> 14) & 0x03,
            sequence_count=word1 & 0x3FFF,
            data_length=data_length,
        )


@dataclass(init=False)
class SecondaryHeader:
    """ECSS PUS TM Secondary Header (9 octets).

    Layout::

        byte 0   : 0x10 | pus_version
        byte 1   : service
        byte 2   : subtype
        bytes 3-6: CUC coarse (uint32 BE)  seconds since the mission epoch
        bytes 7-8: CUC fine   (uint16 BE)  1/65536 s

    ``cuc_time`` remains available as a read/write property over ``cuc_coarse``
    for the many call sites that only care about whole seconds; use
    ``cuc_seconds`` (float) or ``cuc`` (:class:`CucTime`) for full precision.
    """

    pus_version: int = 2
    service: int = 0
    subtype: int = 0
    cuc_coarse: int = 0
    cuc_fine: int = 0

    def __init__(self, pus_version: int = 2, service: int = 0, subtype: int = 0,
                 cuc_coarse: int = 0, cuc_fine: int = 0,
                 cuc_time: Optional[int] = None):
        self.pus_version = pus_version
        self.service = service
        self.subtype = subtype
        # ``cuc_time=`` is the legacy whole-seconds spelling of ``cuc_coarse=``.
        self.cuc_coarse = int(cuc_time) if cuc_time is not None else int(cuc_coarse)
        self.cuc_fine = int(cuc_fine)

    # ── time views ──────────────────────────────────────────────────

    @property
    def cuc_time(self) -> int:
        """Whole CUC seconds (the coarse field). Legacy accessor."""
        return self.cuc_coarse

    @cuc_time.setter
    def cuc_time(self, value: int) -> None:
        """Set whole CUC seconds. Does NOT touch ``cuc_fine``."""
        self.cuc_coarse = int(value)

    @property
    def cuc_seconds(self) -> float:
        """Full-precision seconds since the mission epoch."""
        return self.cuc_coarse + self.cuc_fine / CUC_FINE_TICKS

    @cuc_seconds.setter
    def cuc_seconds(self, value: float) -> None:
        cuc = CucTime.from_seconds(value)
        self.cuc_coarse, self.cuc_fine = cuc.coarse, cuc.fine

    @property
    def cuc(self) -> CucTime:
        """The timestamp as a :class:`CucTime`."""
        return CucTime(self.cuc_coarse, self.cuc_fine)

    @cuc.setter
    def cuc(self, value: CucTime) -> None:
        self.cuc_coarse, self.cuc_fine = value.coarse, value.fine

    # ── wire ────────────────────────────────────────────────────────

    def pack(self) -> bytes:
        spare_pus = 0x10 | (self.pus_version & 0x0F)
        return (bytes([spare_pus, self.service & 0xFF, self.subtype & 0xFF])
                + CucTime(self.cuc_coarse, self.cuc_fine).pack())

    @classmethod
    def unpack(cls, data: bytes) -> 'SecondaryHeader':
        if len(data) < TM_SEC_HDR_LEN:
            raise ValueError(
                f"Secondary header requires at least {TM_SEC_HDR_LEN} bytes")
        spare_pus = data[0]
        cuc = CucTime.unpack(data[3:TM_SEC_HDR_LEN])
        return cls(pus_version=spare_pus & 0x0F, service=data[1], subtype=data[2],
                   cuc_coarse=cuc.coarse, cuc_fine=cuc.fine)


@dataclass
class DecommutatedPacket:
    """A fully parsed ECSS packet."""
    primary: PrimaryHeader
    secondary: Optional[SecondaryHeader] = None
    data_field: bytes = b''
    crc: int = 0
    crc_valid: bool = True
    raw: bytes = b''


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (polynomial 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
        crc &= 0xFFFF
    return crc


def _datetime_to_epoch_seconds(dt: datetime) -> float:
    """Full-precision seconds from the mission epoch to ``dt``."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt - get_time_epoch()).total_seconds()


def parse_cuc_time(coarse: int, fine: int = 0) -> datetime:
    """Convert a CUC (coarse seconds [+ fine ticks]) to a naive UTC datetime.

    ``fine`` defaults to 0, so the legacy single-argument call is unchanged in
    both signature and meaning.
    """
    return get_time_epoch() + timedelta(
        seconds=coarse + (fine or 0) / CUC_FINE_TICKS)


def cuc_seconds_to_datetime(seconds: float) -> datetime:
    """Convert full-precision CUC seconds to a naive UTC datetime."""
    return get_time_epoch() + timedelta(seconds=float(seconds))


def datetime_to_cuc(dt: datetime) -> tuple[int, int]:
    """Convert a UTC datetime to ``(coarse, fine)`` CUC.

    **Breaking change:** this used to return a bare ``int`` of whole seconds.
    The whole-seconds form now has its own name,
    :func:`datetime_to_cuc_coarse`, so no call site can silently keep compiling
    against a changed meaning — a caller that was not updated gets a tuple and
    fails loudly.
    """
    return CucTime.from_datetime(dt).as_tuple()


def datetime_to_cuc_coarse(dt: datetime) -> int:
    """Whole CUC seconds for ``dt`` — the legacy ``datetime_to_cuc`` behaviour.

    Thin wrapper over :func:`datetime_to_cuc`.  Identical to the old
    implementation for every time at or after the epoch (the only range the
    uint32 coarse field can carry); before the epoch it floors where the old
    ``int()`` truncated toward zero, which is the correct CUC definition.
    """
    return datetime_to_cuc(dt)[0]


def datetime_to_cuc_seconds(dt: datetime) -> float:
    """Full-precision seconds since the mission epoch for ``dt``."""
    return _datetime_to_epoch_seconds(dt)


def decommutate_packet(raw: bytes) -> Optional[DecommutatedPacket]:
    """Parse a raw ECSS packet into its components."""
    if len(raw) < 8:  # minimum: 6 primary + 2 CRC
        return None
    primary = PrimaryHeader.unpack(raw[:6])

    # Total packet length = primary(6) + data_length + 1
    total_len = 6 + primary.data_length + 1
    if len(raw) < total_len:
        return None

    # CRC is last 2 bytes
    packet_body = raw[:total_len - 2]
    crc_received = struct.unpack('>H', raw[total_len - 2:total_len])[0]
    crc_computed = crc16_ccitt(packet_body)

    secondary = None
    data_field = b''
    if primary.sec_hdr_flag:
        # TC secondary header is 3 bytes (no CUC time); TM is 9 since the CUC
        # migration (3 + coarse u32 + fine u16) — see TM_SEC_HDR_LEN.
        if primary.packet_type == PacketType.TC:
            sec_hdr_len = TC_SEC_HDR_LEN  # [spare/PUS, service, subtype]
            if len(raw) >= 6 + sec_hdr_len:
                sec_data = raw[6:6 + sec_hdr_len]
                secondary = SecondaryHeader(
                    pus_version=sec_data[0] & 0x0F,
                    service=sec_data[1],
                    subtype=sec_data[2],
                )
                data_field = raw[6 + sec_hdr_len:total_len - 2]
            else:
                data_field = raw[6:total_len - 2]
        else:
            # [spare/PUS, service, subtype, CUC coarse(4), CUC fine(2)]
            sec_hdr_len = TM_SEC_HDR_LEN
            if len(raw) >= 6 + sec_hdr_len:
                secondary = SecondaryHeader.unpack(raw[6:6 + sec_hdr_len])
                data_field = raw[6 + sec_hdr_len:total_len - 2]
            else:
                data_field = raw[6:total_len - 2]
    else:
        data_field = raw[6:total_len - 2]

    return DecommutatedPacket(
        primary=primary,
        secondary=secondary,
        data_field=data_field,
        crc=crc_received,
        crc_valid=(crc_received == crc_computed),
        raw=raw[:total_len],
    )


def build_tm_packet(
    apid: int,
    service: int,
    subtype: int,
    data: bytes,
    seq_count: int = 0,
    cuc_time: int = 0,
    cuc_fine: int = 0,
) -> bytes:
    """Build a complete ECSS TM packet with CRC.

    ``cuc_time`` is the coarse (whole-seconds) field; pass ``cuc_fine`` for
    sub-second resolution, or a :class:`CucTime` as ``cuc_time`` for both.
    """
    if isinstance(cuc_time, CucTime):
        cuc_time, cuc_fine = cuc_time.coarse, cuc_time.fine
    sec_hdr = SecondaryHeader(service=service, subtype=subtype,
                              cuc_coarse=cuc_time, cuc_fine=cuc_fine)
    payload = sec_hdr.pack() + data
    data_length = len(payload) + 1  # CCSDS: (octets after primary including CRC) - 1

    primary = PrimaryHeader(
        packet_type=PacketType.TM,
        sec_hdr_flag=1,
        apid=apid,
        sequence_count=seq_count & 0x3FFF,
        data_length=data_length,
    )
    packet = primary.pack() + payload
    crc = crc16_ccitt(packet)
    return packet + struct.pack('>H', crc)


def build_tc_packet(
    apid: int,
    service: int,
    subtype: int,
    data: bytes = b'',
    seq_count: int = 0,
) -> bytes:
    """Build a complete ECSS TC packet with CRC."""
    sec_hdr = bytes([0x20, service, subtype])  # PUS version 2
    payload = sec_hdr + data
    data_length = len(payload) + 1  # CCSDS: (octets after primary including CRC) - 1

    primary = PrimaryHeader(
        packet_type=PacketType.TC,
        sec_hdr_flag=1,
        apid=apid,
        sequence_count=seq_count & 0x3FFF,
        data_length=data_length,
    )
    packet = primary.pack() + payload
    crc = crc16_ccitt(packet)
    return packet + struct.pack('>H', crc)
