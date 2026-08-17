"""SMO Common — PUS Service Parsers and Builders.

Per-service data extraction for S1 (Verification), S3 (Housekeeping),
S5 (Events), S20 (Parameter Management), and builders for common TCs.
"""
import re
import struct
from dataclasses import dataclass
from typing import Optional

from .ecss_packet import PUSService, EventSeverity

# ─── S1 failure notice (defect #87) ──────────────────────────────────────
#
# ECSS-E-ST-70-41 lets an S1 failure report carry a "failure notice" data
# field after the failure code. EOSAT-1 uses that slot for the human-readable
# reason the on-board software produced when it refused the command, so the
# operator sees *why* a 1,2 / 1,4 / 1,8 came back instead of a bare number.
#
#   S1.2 / S1.8 :  request_id(u32) error_code(u16) [ reason_len(u8) reason… ]
#   S1.4        :  request_id(u16) error_code(u16) [ reason_len(u8) reason… ]
#
# The reason is UTF-8, length-prefixed with one octet, and is capped at
# FAILURE_REASON_MAX_BYTES. It is STRICTLY APPENDED: a legacy parser that
# reads only the fixed prefix ('>IH' / '>HH') is unaffected, and the field is
# absent entirely when there is no reason to report.
FAILURE_REASON_MAX_BYTES = 160

# Failure-code vocabulary shared by the on-board dispatcher and the ground
# segment. The codes are the ones the simulator actually emits — see
# SimulationEngine._check_tc_acceptance / _dispatch_tc and
# ServiceDispatcher.generate_s1_exec_fail.
FAILURE_CODES: dict[int, str] = {
    0x0000: "No error",
    0x0001: "Generic failure / unknown service",
    0x0002: "Unknown subtype",
    0x0003: "Invalid parameter or data length",
    0x0004: "Subsystem unpowered or unreachable",
    0x0005: "Invalid in the current mode",
    0x0006: "Not permitted in bootloader",
    0x0007: "Not permitted in the current state",
    # Phase C (brief 02 WP-6) — payload capture pointing gate
    0x0021: "Capture refused: AOCS not in FINE_POINT",
    0x0022: "Capture refused: total pointing error above 0.05 deg",
    0x0023: "Capture refused: rate stability above 0.005 deg/s",
    # 2026-08b (DISPLAY_REQUESTS_PAYLOAD.md §8.1) — payload calibration and
    # shutter refusals. They used to report the generic 0x0005 / 0x0007, so the
    # console printed "Invalid in the current mode" for four physically
    # distinct refusals and the reason survived only in the free text.
    0x0024: "Calibration refused: not permitted in IMAGING",
    0x0025: "Calibration refused: FPA not thermally ready",
    0x0026: "Calibration refused: shutter in the wrong position",
    0x0027: "Calibration refused: calibration lamp in the wrong state",
    0x0028: "Capture refused: shutter closed",
    # Phase D (brief 03 WP-2a) — propulsion arm interlocks, one code each,
    # mirroring the arm_inhibit_mask (0x0B15) bits
    0x0B01: "Arm refused: catalyst bed below minimum temperature",
    0x0B02: "Arm refused: tank pressure outside the operating band",
    0x0B03: "Arm refused: insufficient propellant remaining",
    0x0B04: "Arm refused: AOCS not in a burn-capable mode (NOMINAL/FINE/INERTIAL)",
    0x0B05: "Arm refused: attitude error above the arm limit",
    0x0B06: "Arm refused: battery SoC below the arm limit",
    0x0B07: "Arm refused: spacecraft not in NOMINAL phase",
    0x0B08: "Arm refused: burn not permitted in eclipse",
    0x0B09: "Arm refused: no latch-valve branch open (PROP_ISO_OPEN first)",
    0x0B0A: "Arm refused: propulsion inhibit latched (PROP_INHIBIT_CLR required)",
    0x0B0B: "Arm refused: no burn loaded (PROP_SET_BURN within the arm window)",
    # Phase D — other propulsion command refusals
    0x0B0C: "Burn refused: delta-V outside (0, 20] m/s",
    0x0B0D: "Burn refused: delta-V exceeds the remaining envelope",
    0x0B0E: "Burn refused: burn in progress",
    0x0B0F: "Arm refused: bad arm code",
    0x0B10: "Arm refused: not in SAFED state",
    0x0B11: "Execute refused: not ARMED (PROP_SET_BURN + PROP_ARM first)",
    0x0B12: "Valve command refused: latch valve(s) stuck",
    0x0B13: "Inhibit-clear refused: bad key",
}

# Service 6 reuses the low codes with memory-specific meanings
# (smo_simulator.memory_map ERR_*). Only meaningful on an S1.4 raised by an
# S6 memory request.
MEMORY_FAILURE_CODES: dict[int, str] = {
    0x0001: "Illegal address (outside every declared region)",
    0x0002: "Write protected (read-only region)",
    0x0003: "Bad argument (malformed memory request)",
    0x0004: "Length exceeds the configured memory transfer limit",
}


def failure_code_text(code: int, service: Optional[int] = None) -> str:
    """Human-readable meaning of an S1 failure code.

    ``service`` is the service of the *rejected telecommand* when known; it
    selects the S6 memory-specific vocabulary for memory requests.
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        return "Unknown failure code"
    if service == 6 and code in MEMORY_FAILURE_CODES:
        return MEMORY_FAILURE_CODES[code]
    return FAILURE_CODES.get(code, f"Unknown failure code 0x{code:04X}")


def encode_failure_reason(reason: Optional[str]) -> bytes:
    """Encode a failure reason as ``len(u8) + UTF-8``; b'' when there is none.

    Truncates to FAILURE_REASON_MAX_BYTES without splitting a multi-byte
    character, so the field always decodes cleanly.
    """
    if not reason:
        return b""
    raw = str(reason).encode("utf-8", errors="replace")
    if len(raw) > FAILURE_REASON_MAX_BYTES:
        raw = raw[:FAILURE_REASON_MAX_BYTES]
        # Drop a trailing PARTIAL UTF-8 sequence only. Testing by decoding is
        # exact; hand-rolled continuation-octet arithmetic would also eat a
        # complete multi-byte character that happens to end on the cap.
        while raw:
            try:
                raw.decode("utf-8")
                break
            except UnicodeDecodeError:
                raw = raw[:-1]
    if not raw:
        return b""
    return struct.pack('>B', len(raw)) + raw


def decode_failure_reason(data: bytes, offset: int) -> str:
    """Decode the optional failure notice starting at ``offset``.

    Returns "" when the field is absent or truncated, so an old spacecraft
    that does not append one is handled identically to one that does.
    """
    if data is None or len(data) <= offset:
        return ""
    length = data[offset]
    if length == 0 or len(data) < offset + 1 + length:
        return ""
    return data[offset + 1:offset + 1 + length].decode("utf-8", errors="replace")


# ─── COMMAND_REJECTED (0x030B) auxiliary text (defect #88) ───────────────
#
# The S5 event that accompanies every refusal carries the whole story in its
# auxiliary text, because an S5 event has no structured room for it. That text
# is *written* by the spacecraft (engine._emit_command_rejected) and *read* by
# the ground segment (smo_mcs.server), so the format lives here, once, next to
# the failure-code vocabulary it quotes. A ground segment that decodes the code
# out of the text can then show its plain-English meaning from FAILURE_CODES
# without the browser holding a second copy of either.
#
#   "S8.1 func 65 rejected at execution, err 0x0007: <reason>"
COMMAND_REJECTED_EVENT_ID = 0x030B

_REJECTION_AUX_RE = re.compile(
    r"^(?P<identity>.*?) rejected at (?P<stage>\w+), "
    r"err 0x(?P<code>[0-9A-Fa-f]{1,4})(?::\s*(?P<reason>.*))?$"
)


def format_command_rejection(identity: str, stage: str, error_code: int,
                             reason: str) -> str:
    """Render the COMMAND_REJECTED auxiliary text.

    ``identity`` names the refused telecommand ("S8.1 func 65"), ``stage`` is
    "acceptance" or "execution".
    """
    text = f"{identity} rejected at {stage}, err 0x{int(error_code):04X}"
    if reason:
        text += f": {reason}"
    return text


def parse_command_rejection(text: str) -> Optional[dict]:
    """Recover ``{identity, stage, error_code, error_text, reason}`` from it.

    Returns ``None`` when ``text`` is not a rejection notice, so a caller can
    hand it every S5 auxiliary text it sees.
    """
    if not text:
        return None
    m = _REJECTION_AUX_RE.match(str(text).strip())
    if not m:
        return None
    code = int(m.group("code"), 16)
    identity = m.group("identity") or ""
    service = None
    svc_match = re.match(r"^S(\d+)\.(\d+)", identity)
    if svc_match:
        service = int(svc_match.group(1))
    return {
        "identity": identity,
        "stage": m.group("stage"),
        "error_code": code,
        "error_text": failure_code_text(code, service=service),
        "reason": (m.group("reason") or "").strip(),
    }


@dataclass
class VerificationData:
    """S1 Verification report data."""
    request_id: int = 0
    tc_apid: int = 0
    tc_seq: int = 0
    error_code: int = 0
    reason: str = ""

    @classmethod
    def parse(cls, data: bytes, subtype: int) -> 'VerificationData':
        if subtype == 4:
            # S1.4 execution-start failure is generated by the dispatcher with
            # a 16-bit request id: request_id(u16) + error_code(u16) [+ notice]
            if len(data) < 4:
                return cls()
            request_id = struct.unpack('>H', data[:2])[0]
            error_code = struct.unpack('>H', data[2:4])[0]
            return cls(request_id=request_id, tc_apid=0,
                       tc_seq=request_id & 0x3FFF, error_code=error_code,
                       reason=decode_failure_reason(data, 4))
        if len(data) < 4:
            return cls()
        request_id = struct.unpack('>I', data[:4])[0]
        tc_apid = (request_id >> 14) & 0x7FF
        tc_seq = request_id & 0x3FFF
        error_code = 0
        reason = ""
        if subtype in (2, 8) and len(data) >= 6:  # acceptance / execution failure
            error_code = struct.unpack('>H', data[4:6])[0]
            reason = decode_failure_reason(data, 6)
        return cls(request_id=request_id, tc_apid=tc_apid, tc_seq=tc_seq,
                   error_code=error_code, reason=reason)


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
    """S5 Event report data.

    ``cuc_time`` is the timestamp repeated inside the event *body* as a bare
    uint32 of whole seconds. It is deliberately NOT extended to coarse+fine by
    the CUC migration: the authoritative, full-precision timestamp is the one
    in the packet's 9-octet secondary header.
    """
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
