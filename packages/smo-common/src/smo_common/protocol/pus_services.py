"""SMO Common — PUS Service Parsers and Builders.

Per-service data extraction for S1 (Verification), S3 (Housekeeping),
S5 (Events), S20 (Parameter Management), and builders for common TCs.
"""
import struct
from dataclasses import dataclass
from typing import Optional

from .ecss_packet import PUSService, EventSeverity


@dataclass
class VerificationData:
    """S1 Verification report data."""
    request_id: int = 0
    tc_apid: int = 0
    tc_seq: int = 0
    error_code: int = 0

    @classmethod
    def parse(cls, data: bytes, subtype: int) -> 'VerificationData':
        if len(data) < 4:
            return cls()
        request_id = struct.unpack('>I', data[:4])[0]
        tc_apid = (request_id >> 14) & 0x7FF
        tc_seq = request_id & 0x3FFF
        error_code = 0
        if subtype == 2 and len(data) >= 6:  # acceptance failure
            error_code = struct.unpack('>H', data[4:6])[0]
        return cls(request_id=request_id, tc_apid=tc_apid, tc_seq=tc_seq, error_code=error_code)


@dataclass
class HousekeepingData:
    """S3 Housekeeping report data."""
    sid: int = 0
    raw_data: bytes = b''

    @classmethod
    def parse(cls, data: bytes) -> 'HousekeepingData':
        if len(data) < 2:
            return cls()
        sid = struct.unpack('>H', data[:2])[0]
        return cls(sid=sid, raw_data=data[2:])


@dataclass
class EventData:
    """S5 Event report data."""
    event_id: int = 0
    severity: int = 1
    cuc_time: int = 0
    aux_text: str = ""

    @classmethod
    def parse(cls, data: bytes) -> 'EventData':
        if len(data) < 3:
            return cls()
        event_id, severity = struct.unpack('>HB', data[:3])
        cuc_time = 0
        aux_text = ""
        if len(data) >= 7:
            cuc_time = struct.unpack('>I', data[3:7])[0]
        if len(data) >= 8:
            aux_len = data[7]
            if len(data) >= 8 + aux_len:
                aux_text = data[8:8 + aux_len].decode('ascii', errors='replace')
        return cls(event_id=event_id, severity=severity, cuc_time=cuc_time, aux_text=aux_text)


@dataclass
class ParameterValueData:
    """S20 Parameter Value report data."""
    param_id: int = 0
    validity: int = 0
    value: float = 0.0

    @classmethod
    def parse(cls, data: bytes) -> 'ParameterValueData':
        if len(data) < 7:
            return cls()
        param_id, validity, value = struct.unpack('>HBf', data[:7])
        return cls(param_id=param_id, validity=validity, value=value)


def parse_service_data(service: int, subtype: int, data: bytes):
    """Parse service-specific data field."""
    if service == PUSService.VERIFICATION:
        return VerificationData.parse(data, subtype)
    elif service == PUSService.HOUSEKEEPING and subtype == 25:
        return HousekeepingData.parse(data)
    elif service == PUSService.EVENT_REPORTING:
        return EventData.parse(data)
    elif service == PUSService.PARAMETER_MANAGEMENT and subtype == 2:
        return ParameterValueData.parse(data)
    return None
