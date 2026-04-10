"""SMO Common — ECSS/CCSDS Packet Handling.

Implements CCSDS Space Packet primary header, ECSS PUS-C secondary header,
CRC-16/CCITT-FALSE, and packet parsing/building utilities.
"""
import struct
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)

# Time epoch: J2000.0 = 2000-01-01T12:00:00 UTC
TIME_EPOCH = datetime(2000, 1, 1, 12, 0, 0)


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


@dataclass
class SecondaryHeader:
    """ECSS PUS Secondary Header."""
    pus_version: int = 2
    service: int = 0
    subtype: int = 0
    cuc_time: int = 0

    def pack(self) -> bytes:
        spare_pus = 0x10 | (self.pus_version & 0x0F)
        return bytes([spare_pus, self.service, self.subtype]) + struct.pack('>I', self.cuc_time)

    @classmethod
    def unpack(cls, data: bytes) -> 'SecondaryHeader':
        if len(data) < 7:
            raise ValueError("Secondary header requires at least 7 bytes")
        spare_pus = data[0]
        pus_version = spare_pus & 0x0F
        service = data[1]
        subtype = data[2]
        cuc_time = struct.unpack('>I', data[3:7])[0]
        return cls(pus_version=pus_version, service=service, subtype=subtype, cuc_time=cuc_time)


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


def parse_cuc_time(cuc_seconds: int) -> datetime:
    """Convert CUC seconds since J2000 to UTC datetime."""
    return TIME_EPOCH + timedelta(seconds=cuc_seconds)


def datetime_to_cuc(dt: datetime) -> int:
    """Convert UTC datetime to CUC seconds since J2000."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return int((dt - TIME_EPOCH).total_seconds())


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
        # TC secondary header is 3 bytes (no CUC time), TM is 7 bytes
        if primary.packet_type == PacketType.TC:
            sec_hdr_len = 3  # [spare/PUS, service, subtype]
            if len(raw) >= 6 + sec_hdr_len:
                sec_data = raw[6:6 + sec_hdr_len]
                secondary = SecondaryHeader(
                    pus_version=sec_data[0] & 0x0F,
                    service=sec_data[1],
                    subtype=sec_data[2],
                    cuc_time=0,
                )
                data_field = raw[6 + sec_hdr_len:total_len - 2]
            else:
                data_field = raw[6:total_len - 2]
        else:
            sec_hdr_len = 7  # [spare/PUS, service, subtype, CUC(4)]
            if len(raw) > 6 + sec_hdr_len:
                secondary = SecondaryHeader.unpack(raw[6:13])
                data_field = raw[13:total_len - 2]
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
) -> bytes:
    """Build a complete ECSS TM packet with CRC."""
    sec_hdr = SecondaryHeader(service=service, subtype=subtype, cuc_time=cuc_time)
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
