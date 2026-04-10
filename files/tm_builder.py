"""
EO Mission Simulator — TM Builder
Assembles ECSS PUS TM packets from the shared parameter store.
Supports: S1 verification, S3 housekeeping, S5 event reporting,
          S20 parameter value reports.
"""
import struct
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from config import (
    SPACECRAFT_APID, PUS_VERSION, TIME_EPOCH,
    HK_SID_EPS, HK_SID_AOCS, HK_SID_TCS,
    HK_SID_PLATFORM, HK_SID_PAYLOAD, HK_SID_TTC,
    # EPS params
    P_BAT_VOLTAGE, P_BAT_SOC, P_BAT_TEMP, P_BAT_CURRENT,
    P_SA_A_CURRENT, P_SA_B_CURRENT, P_BUS_VOLTAGE,
    P_POWER_CONS, P_POWER_GEN, P_ECLIPSE_FLAG,
    # AOCS params
    P_ATT_Q1, P_ATT_Q2, P_ATT_Q3, P_ATT_Q4,
    P_RATE_ROLL, P_RATE_PITCH, P_RATE_YAW,
    P_RW1_SPEED, P_RW2_SPEED, P_RW3_SPEED, P_RW4_SPEED,
    P_RW1_TEMP, P_RW2_TEMP, P_RW3_TEMP, P_RW4_TEMP,
    P_MAG_X, P_MAG_Y, P_MAG_Z, P_AOCS_MODE, P_ATT_ERROR,
    P_GPS_LAT, P_GPS_LON, P_GPS_ALT, P_SOLAR_BETA,
    # TCS params
    P_TEMP_PANEL_PX, P_TEMP_PANEL_MX, P_TEMP_PANEL_PY,
    P_TEMP_PANEL_MY, P_TEMP_PANEL_PZ, P_TEMP_PANEL_MZ,
    P_TEMP_OBC, P_TEMP_BATTERY, P_TEMP_FPA, P_TEMP_THRUSTER,
    P_HTR_BATTERY, P_HTR_OBC, P_COOLER_FPA,
    # Platform params
    P_OBC_MODE, P_OBC_CPU_LOAD, P_MMM_USED_PCT,
    P_TC_RX_COUNT, P_TC_ACC_COUNT, P_TC_REJ_COUNT,
    P_TM_PKT_COUNT, P_UPTIME_S, P_REBOOT_COUNT,
    # Payload params
    P_PLI_MODE, P_FPA_TEMP, P_COOLER_PWR, P_IMAGER_TEMP,
    P_STORE_USED_PCT, P_IMAGE_COUNT, P_CHECKSUM_ERRORS,
    # TT&C params
    P_TTC_MODE, P_LINK_STATUS, P_RSSI, P_LINK_MARGIN,
    P_TM_DATA_RATE, P_XPDR_TEMP, P_RANGING_STATUS,
    P_RANGE_KM, P_CONTACT_ELEVATION,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# HK structure definitions: SID → list of (param_id, pack_format, scale)
# pack_format: 'f' = float32, 'i' = int32, 'H' = uint16, 'B' = uint8
# scale: multiplier applied before packing (e.g. 100 for 2-decimal fixed-pt)
# -----------------------------------------------------------------------
_HK_STRUCTURES: Dict[int, List[tuple]] = {
    HK_SID_EPS: [
        (P_BAT_VOLTAGE,   'H', 100),   # V × 100 → uint16
        (P_BAT_SOC,       'H', 100),   # % × 100
        (P_BAT_TEMP,      'h', 100),   # °C × 100 → int16
        (P_BAT_CURRENT,   'h', 100),   # A × 100 → int16
        (P_SA_A_CURRENT,  'H', 100),
        (P_SA_B_CURRENT,  'H', 100),
        (P_BUS_VOLTAGE,   'H', 100),
        (P_POWER_CONS,    'H', 10),    # W × 10
        (P_POWER_GEN,     'H', 10),
        (P_ECLIPSE_FLAG,  'B', 1),
    ],
    HK_SID_AOCS: [
        (P_ATT_Q1,        'h', 10000), # q × 10000 → int16
        (P_ATT_Q2,        'h', 10000),
        (P_ATT_Q3,        'h', 10000),
        (P_ATT_Q4,        'h', 10000),
        (P_RATE_ROLL,     'h', 10000), # deg/s × 10000
        (P_RATE_PITCH,    'h', 10000),
        (P_RATE_YAW,      'h', 10000),
        (P_ATT_ERROR,     'H', 1000),  # deg × 1000
        (P_RW1_SPEED,     'h', 1),     # RPM
        (P_RW2_SPEED,     'h', 1),
        (P_RW3_SPEED,     'h', 1),
        (P_RW4_SPEED,     'h', 1),
        (P_AOCS_MODE,     'B', 1),
    ],
    HK_SID_TCS: [
        (P_TEMP_PANEL_PX, 'h', 100),
        (P_TEMP_PANEL_MX, 'h', 100),
        (P_TEMP_PANEL_PY, 'h', 100),
        (P_TEMP_PANEL_MY, 'h', 100),
        (P_TEMP_PANEL_PZ, 'h', 100),
        (P_TEMP_PANEL_MZ, 'h', 100),
        (P_TEMP_OBC,      'h', 100),
        (P_TEMP_BATTERY,  'h', 100),
        (P_TEMP_FPA,      'h', 100),
        (P_TEMP_THRUSTER, 'h', 100),
        (P_HTR_BATTERY,   'B', 1),
        (P_HTR_OBC,       'B', 1),
        (P_COOLER_FPA,    'B', 1),
    ],
    HK_SID_PLATFORM: [
        (P_OBC_MODE,      'B', 1),
        (P_OBC_CPU_LOAD,  'H', 100),
        (P_MMM_USED_PCT,  'H', 100),
        (P_TC_RX_COUNT,   'I', 1),
        (P_TC_ACC_COUNT,  'I', 1),
        (P_TC_REJ_COUNT,  'H', 1),
        (P_TM_PKT_COUNT,  'I', 1),
        (P_UPTIME_S,      'I', 1),
        (P_REBOOT_COUNT,  'H', 1),
    ],
    HK_SID_PAYLOAD: [
        (P_PLI_MODE,      'B', 1),
        (P_FPA_TEMP,      'h', 100),
        (P_COOLER_PWR,    'H', 10),
        (P_IMAGER_TEMP,   'h', 100),
        (P_STORE_USED_PCT,'H', 100),
        (P_IMAGE_COUNT,   'H', 1),
        (P_CHECKSUM_ERRORS,'H', 1),
    ],
    HK_SID_TTC: [
        (P_TTC_MODE,      'B', 1),
        (P_LINK_STATUS,   'B', 1),
        (P_RSSI,          'h', 10),    # dBm × 10 → int16
        (P_LINK_MARGIN,   'h', 10),
        (P_TM_DATA_RATE,  'I', 1),
        (P_XPDR_TEMP,     'h', 100),
        (P_RANGING_STATUS,'B', 1),
        (P_RANGE_KM,      'I', 1),
        (P_CONTACT_ELEVATION, 'h', 100),
    ],
}


class TMBuilder:
    """
    Assembles ECSS PUS TM packets.
    All packets are big-endian per CCSDS/ECSS.
    """

    def __init__(self, apid: int, obdh=None):
        self._apid      = apid
        self._seq_count = 0
        self._obdh      = obdh   # for CUC time retrieval

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_hk_packet(self, sid: int, params: Dict) -> Optional[bytes]:
        """Build a Service 3 Housekeeping TM packet."""
        struct_def = _HK_STRUCTURES.get(sid)
        if struct_def is None:
            return None
        cuc = self._cuc_now(params)
        data = struct.pack('>H', sid)        # Structure ID (2 bytes)
        for param_id, fmt, scale in struct_def:
            value = params.get(param_id, 0)
            try:
                packed_val = int(round(float(value) * scale))
                data += struct.pack('>' + fmt, packed_val)
            except (struct.error, OverflowError, TypeError):
                data += struct.pack('>' + fmt, 0)
        return self._pack_tm(service=3, subtype=25, data=data, cuc=cuc)

    def build_event_packet(
        self,
        event_id:  int,
        severity:  int,
        aux_text:  str,
        params:    Dict,
    ) -> Optional[bytes]:
        """Build a Service 5 Event Report TM packet."""
        cuc = self._cuc_now(params)
        aux = aux_text.encode('ascii', errors='ignore')[:32]
        data  = struct.pack('>HB', event_id, severity)
        data += struct.pack('>I', cuc)
        data += struct.pack('>B', len(aux)) + aux
        return self._pack_tm(service=5, subtype=severity, data=data, cuc=cuc)

    def build_verification_acceptance(self, tc_apid: int, tc_seq: int) -> bytes:
        """Build Service 1 subtype 1 — Acceptance Success."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>I', request_id)
        return self._pack_tm(service=1, subtype=1, data=data)

    def build_verification_completion(self, tc_apid: int, tc_seq: int) -> bytes:
        """Build Service 1 subtype 7 — Execution Complete."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>I', request_id)
        return self._pack_tm(service=1, subtype=7, data=data)

    def build_verification_failure(self, tc_apid: int, tc_seq: int, error_code: int) -> bytes:
        """Build Service 1 subtype 2 — Acceptance Failure."""
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>IH', request_id, error_code)
        return self._pack_tm(service=1, subtype=2, data=data)

    def build_param_value_report(self, param_id: int, value: float) -> bytes:
        """Build Service 20 Parameter Value Report (subtype 2)."""
        cuc = self._cuc_now({})
        data = struct.pack('>HBf', param_id, 1, value)  # param_id, validity=1, float value
        return self._pack_tm(service=20, subtype=2, data=data, cuc=cuc)

    # ------------------------------------------------------------------
    # Packet assembly
    # ------------------------------------------------------------------

    def _pack_tm(
        self,
        service:  int,
        subtype:  int,
        data:     bytes,
        cuc:      int = 0,
    ) -> bytes:
        """
        Assemble a complete ECSS TM packet.
        Primary header (6) + Secondary header (10) + data + CRC (2).
        Secondary header: spare(1) | pus_ver(4) | time_ref(3) | svc(1) | sub(1) | dest_id(2) | time(4)
        Simplified to 4-byte secondary header: spare_pus | svc | sub | time(4) = 7 bytes
        """
        # Secondary header: [spare_pus=0x10] [svc] [sub] [4-byte CUC time]
        sec_hdr = bytes([0x10, service, subtype]) + struct.pack('>I', cuc)

        payload = sec_hdr + data
        data_length_field = len(payload) - 1  # ECSS: data length = (total - primary header 6) - 1

        # Sequence counter (14-bit, wraps)
        self._seq_count = (self._seq_count + 1) & 0x3FFF

        # Primary header
        # Version=0, Type=TM(0), SecHdr=1, APID
        packet_id  = (0 << 13) | (0 << 12) | (1 << 11) | (self._apid & 0x7FF)
        seq_ctrl   = (0b11 << 14) | self._seq_count   # standalone packet
        primary    = struct.pack('>HHH', packet_id, seq_ctrl, data_length_field)

        packet = primary + payload

        # Packet Error Control (CRC-16/CCITT-FALSE)
        crc = self._crc16(packet)
        return packet + struct.pack('>H', crc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _cuc_now(self, params: Dict) -> int:
        """Return current on-board CUC time (seconds since J2000)."""
        if self._obdh is not None:
            return self._obdh.state.obc_time_cuc
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return int((now - TIME_EPOCH).total_seconds())

    @staticmethod
    def _crc16(data: bytes) -> int:
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
