"""SMO MCS — TM Processor.

Decommutation, parameter extraction, limit checking, and history buffer.
Handles all SIDs including SID 10 (boot loader HK), S5 events, and S12
monitoring transition reports.
"""
import logging
import struct
from collections import deque
from typing import Any, Optional

from smo_common.protocol.ecss_packet import decommutate_packet
from smo_common.config.schemas import LimitDef

logger = logging.getLogger(__name__)


class TMProcessor:
    """Processes incoming TM packets: decommutation, limits, history."""

    def __init__(self, hk_structures: dict[int, list[tuple]] | None = None,
                 limits: list[LimitDef] | None = None):
        self._hk_structures = hk_structures or {}
        self._limits = {l.param_id: l for l in (limits or [])}
        self._params: dict[int, float] = {}
        self._history: dict[int, deque] = {}
        self._alarms: list[dict] = []
        self._max_history = 600  # 10 minutes at 1 Hz

    def process_packet(self, raw: bytes) -> Optional[dict]:
        pkt = decommutate_packet(raw)
        if pkt is None or pkt.secondary is None:
            return None
        svc = pkt.secondary.service
        sub = pkt.secondary.subtype
        if svc == 3 and sub == 25:
            return self._process_hk(pkt.data_field)
        elif svc == 5:
            return self._process_event(pkt.data_field, sub)
        elif svc == 12 and sub in (9, 10):
            return self._process_monitoring(pkt.data_field, sub)
        return {"service": svc, "subtype": sub}

    def _process_hk(self, data: bytes) -> dict:
        if len(data) < 2:
            return {}
        sid = struct.unpack('>H', data[:2])[0]
        structure = self._hk_structures.get(sid)
        if not structure:
            return {"sid": sid, "raw": True}
        offset = 2
        params = {}
        for param_id, fmt, scale in structure:
            size = struct.calcsize('>' + fmt)
            if offset + size > len(data):
                break
            raw_val = struct.unpack('>' + fmt, data[offset:offset + size])[0]
            value = raw_val / scale if scale != 0 else raw_val
            params[param_id] = value
            self._params[param_id] = value
            # History
            if param_id not in self._history:
                self._history[param_id] = deque(maxlen=self._max_history)
            self._history[param_id].append(value)
            # Limit check
            self._check_limit(param_id, value)
            offset += size
        return {"sid": sid, "params": params}

    def _process_event(self, data: bytes, severity: int) -> dict:
        if len(data) < 3:
            return {}
        event_id = struct.unpack('>H', data[:2])[0]
        result: dict[str, Any] = {
            "event_id": event_id,
            "severity": severity,
        }
        # Parse auxiliary data if present
        if len(data) >= 7:
            cuc_time = struct.unpack('>I', data[3:7])[0]
            result["cuc_time"] = cuc_time
            if len(data) > 7:
                text_len = data[7]
                if len(data) >= 8 + text_len:
                    result["description"] = data[8:8+text_len].decode(
                        'ascii', errors='ignore'
                    )
        return result

    def _process_monitoring(self, data: bytes, subtype: int) -> dict:
        """Process S12 monitoring transition report."""
        if len(data) < 6:
            return {"service": 12, "subtype": subtype}
        param_id = struct.unpack('>H', data[:2])[0]
        value = struct.unpack('>f', data[2:6])[0]

        result = {
            "service": 12,
            "subtype": subtype,
            "param_id": param_id,
            "value": value,
            "transition": (
                "out_of_limits" if subtype == 9 else "back_to_nominal"
            ),
        }

        # If out-of-limits, add as alarm
        if subtype == 9:
            self._alarms.append({
                "param_id": param_id,
                "value": value,
                "status": "monitoring_violation",
            })

        return result

    def _check_limit(self, param_id: int, value: float) -> None:
        limit = self._limits.get(param_id)
        if not limit:
            return
        status = "nominal"
        if limit.red_low is not None and value < limit.red_low:
            status = "red_low"
        elif limit.red_high is not None and value > limit.red_high:
            status = "red_high"
        elif limit.yellow_low is not None and value < limit.yellow_low:
            status = "yellow_low"
        elif limit.yellow_high is not None and value > limit.yellow_high:
            status = "yellow_high"
        if status != "nominal":
            self._alarms.append({
                "param_id": param_id,
                "value": value,
                "status": status,
            })

    def get_param(self, param_id: int) -> Optional[float]:
        return self._params.get(param_id)

    def get_history(self, param_id: int) -> list[float]:
        return list(self._history.get(param_id, []))

    def pop_alarms(self) -> list[dict]:
        alarms = list(self._alarms)
        self._alarms.clear()
        return alarms
