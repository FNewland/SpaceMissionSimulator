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
        # Ground-limit state machine.  A parameter sitting outside its band
        # must raise exactly one alarm, not one per HK cycle, so the journal
        # stays readable; the *state* is what the displays colour from.
        self._limit_status: dict[int, str] = {}

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
        """Process an S12 check-transition report, TM(12,9) / TM(12,10).

        This used to unpack only the historical 6-byte prefix (param_id +
        value) and throw away `mon_id`, `check_type`, `severity` and both
        limits — four of the six fields that make a limit violation legible —
        even though the full 18-byte parser sat next door in
        `smo_mcs.pus_reports`. The raised alarm therefore said "0x0101 = 8.2"
        with no band, no severity and no monitor id, so two monitors on the
        same parameter (the WARNING and ALARM tiers on battery SoC, for
        instance) were indistinguishable on the ground.
        """
        from smo_common.reporting.decode import (
            decode_s12_transition, s12_check_type_name, s12_severity_name,
        )
        rec = decode_s12_transition(data, subtype)
        if rec is None:
            return {"service": 12, "subtype": subtype}

        result: dict[str, Any] = {"service": 12, "subtype": subtype, **rec}
        if rec.get("check_type") is not None:
            result["check_type_name"] = s12_check_type_name(rec["check_type"])
        if rec.get("severity") is not None:
            result["severity_name"] = s12_severity_name(rec["severity"])

        # If out-of-limits, add as alarm — now carrying the whole report, so
        # the journal can say which monitor fired and against what band.
        if subtype == 9:
            alarm = {
                "param_id": rec["param_id"],
                "value": rec["value"],
                "status": "monitoring_violation",
            }
            for key in ("mon_id", "check_type", "severity",
                        "low_limit", "high_limit"):
                if rec.get(key) is not None:
                    alarm[key] = rec[key]
            if "check_type_name" in result:
                alarm["check_type_name"] = result["check_type_name"]
            if "severity_name" in result:
                alarm["severity_name"] = result["severity_name"]
            self._alarms.append(alarm)

        return result

    def classify(self, param_id: int, value: float) -> str:
        """Return the ground-limit state of *value* for *param_id*.

        ``"unlimited"`` when the parameter carries no band at all — which is
        a different statement from ``"nominal"`` and the displays render it
        differently (no colour, rather than green).
        """
        limit = self._limits.get(param_id)
        if not limit:
            return "unlimited"
        if limit.red_low is not None and value < limit.red_low:
            return "red_low"
        if limit.red_high is not None and value > limit.red_high:
            return "red_high"
        if limit.yellow_low is not None and value < limit.yellow_low:
            return "yellow_low"
        if limit.yellow_high is not None and value > limit.yellow_high:
            return "yellow_high"
        return "nominal"

    def limit_band(self, param_id: int) -> Optional[dict]:
        limit = self._limits.get(param_id)
        if not limit:
            return None
        return {
            "yellow_low": limit.yellow_low, "yellow_high": limit.yellow_high,
            "red_low": limit.red_low, "red_high": limit.red_high,
        }

    def limit_bands(self) -> dict[int, dict]:
        return {pid: self.limit_band(pid) for pid in self._limits}

    def limit_statuses(self) -> dict[int, str]:
        """Current ground-limit state of every limited parameter seen so far."""
        return dict(self._limit_status)

    def _check_limit(self, param_id: int, value: float) -> None:
        limit = self._limits.get(param_id)
        if not limit:
            return
        status = self.classify(param_id, value)
        prev = self._limit_status.get(param_id, "nominal")
        self._limit_status[param_id] = status
        if status == prev:
            # Steady state — already journalled (or already nominal).
            return
        self._alarms.append({
            "param_id": param_id,
            "value": value,
            "status": status,
            "previous": prev,
            "cleared": status == "nominal",
            "band": self.limit_band(param_id),
        })

    def get_param(self, param_id: int) -> Optional[float]:
        return self._params.get(param_id)

    def get_history(self, param_id: int) -> list[float]:
        return list(self._history.get(param_id, []))

    def pop_alarms(self) -> list[dict]:
        alarms = list(self._alarms)
        self._alarms.clear()
        return alarms
