"""SMO Common — TM Packet Builder.

Assembles ECSS PUS TM packets from shared parameter stores.
Supports S1 (Verification), S3 (Housekeeping), S5 (Events),
S20 (Parameter Value Reports).

Config-driven: HK structure definitions are loaded from YAML config
rather than being hardcoded.

Time: every packet is stamped with ON-BOARD time from the injected
``time_source`` into the 9-octet PUS secondary header (CUC coarse u32 + fine
u16). There is no wall-clock fallback and no untimestamped path — see
``MissingTimeSourceError`` and ``docs/plan_2026-08c/TIME_ARCHITECTURE.md``.
"""
import struct
import logging
from typing import Callable, Optional, Union

from ..protocol.ecss_packet import CucTime, crc16_ccitt, TM_SEC_HDR_LEN
from ..protocol.pus_services import encode_failure_reason

logger = logging.getLogger(__name__)

#: What a TM time source may return: full-precision seconds since the mission
#: epoch, or a ready-made :class:`CucTime`.
TimeSourceResult = Union[float, int, CucTime]
TimeSource = Callable[[], TimeSourceResult]


class MissingTimeSourceError(RuntimeError):
    """Raised when a TM packet is built with no on-board clock available.

    Every packet the spacecraft emits is timestamped with ON-BOARD time. There
    is no fallback: stamping a packet with the host wall clock would put a
    number on the wire that no on-board clock ever produced, and would do so
    silently. A missing time source is a wiring bug in the caller.
    """


# Maximum length of an S5 event report's auxiliary text, in octets. The field
# is length-prefixed with one octet in both the builder and every parser, so
# this is a free parameter; 120 is enough to carry a rejected command's
# identity together with the on-board software's reason for refusing it
# (defect #87 — 32 octets truncated the reason to uselessness).
EVENT_AUX_TEXT_MAX = 120

#: Largest TM packet the mission is specified around, octets, INCLUDING the
#: 6-octet primary header, the 9-octet PUS secondary header and the 2-octet
#: CRC. Reports longer than this are segmented (see pack_tm_segmented).
MAX_TM_PACKET_OCTETS = 256
PRIMARY_HEADER_LEN = 6
CRC_LEN = 2


def _as_cuc(value: TimeSourceResult) -> CucTime:
    """Coerce a time-source result (seconds or CucTime) to a CucTime."""
    if isinstance(value, CucTime):
        return value
    if isinstance(value, (int, float)):
        return CucTime.from_seconds(value)
    raise TypeError(
        f"TM time source must return seconds (float/int) or CucTime, "
        f"got {type(value).__name__}"
    )


class TMBuilder:
    """Assembles ECSS PUS TM packets. All packets are big-endian per CCSDS/ECSS."""

    def __init__(self, apid: int, time_source: Optional[TimeSource] = None):
        """
        Args:
            apid: source APID for emitted packets.
            time_source: **the on-board clock**. A zero-argument callable
                returning either

                * a ``float`` (or ``int``) of full-precision seconds since the
                  mission epoch — fractional seconds are carried into the CUC
                  fine field, they are not discarded; or
                * a :class:`~smo_common.protocol.ecss_packet.CucTime`.

                It must return ON-BOARD time (the OBDH clock), never sim time
                and never wall clock. It is effectively mandatory: any attempt
                to build a packet without it raises
                :class:`MissingTimeSourceError`. It stays optional in the
                signature only so a builder can be constructed before the
                clock is wired and completed with :meth:`set_time_source`.
        """
        self._apid = apid
        self._seq_count = 0
        self._time_source: Optional[TimeSource] = time_source

    def set_time_source(self, time_source: TimeSource) -> None:
        """Attach (or replace) the on-board clock. See :meth:`__init__`."""
        self._time_source = time_source

    @property
    def apid(self) -> int:
        """Current APID used for all emitted TM packets."""
        return self._apid

    def set_apid(self, apid: int) -> None:
        """Switch the source APID used for subsequent TM packets.

        Used by the engine to distinguish bootloader-sourced TM (beacon/SID 11)
        from application-software-sourced TM. Sequence counter is reset on switch
        so each APID maintains its own counter per ECSS/CCSDS convention.
        """
        new_apid = int(apid) & 0x7FF
        if new_apid != self._apid:
            self._apid = new_apid
            self._seq_count = 0

    def build_hk_packet(self, sid: int, params: dict,
                        hk_structure: list[tuple] | None = None) -> Optional[bytes]:
        """Build a Service 3 Housekeeping TM packet.

        Args:
            sid: Structure ID.
            params: Shared parameter store {param_id: value}.
            hk_structure: List of (param_id, pack_format, scale) tuples.
        """
        if hk_structure is None:
            return None
        cuc = self._get_cuc(params)
        data = struct.pack('>H', sid)
        for param_id, fmt, scale in hk_structure:
            value = params.get(param_id, 0)
            try:
                packed_val = int(round(float(value) * scale))
                data += struct.pack('>' + fmt, packed_val)
            except (struct.error, OverflowError, TypeError):
                data += struct.pack('>' + fmt, 0)
        return self._pack_tm(service=3, subtype=25, data=data, cuc=cuc)

    def build_event_packet(self, event_id: int, severity: int,
                           aux_text: str, params: dict) -> Optional[bytes]:
        """Build a Service 5 Event Report TM packet."""
        cuc = self._get_cuc(params)
        aux = aux_text.encode('ascii', errors='ignore')[:EVENT_AUX_TEXT_MAX]
        data = struct.pack('>HB', event_id, severity)
        # The event body repeats the timestamp as WHOLE SECONDS only (uint32).
        # Deliberately unchanged by the CUC migration: it is a legacy field
        # that every parser reads as '>I' at a fixed offset, and the secondary
        # header now carries the authoritative full-precision time.
        data += struct.pack('>I', cuc.coarse)
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

    def build_verification_failure(self, tc_apid: int, tc_seq: int,
                                   error_code: int, reason: str = "") -> bytes:
        """Build Service 1 subtype 2 — Acceptance Failure.

        Wire format:  request_id(u32) error_code(u16) [failure notice]

        The optional failure notice is ``reason_len(u8) + UTF-8 reason`` and is
        appended STRICTLY AFTER the historical fields, so a parser that reads
        only ``>IH`` is unaffected (defect #87).
        """
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>IH', request_id, error_code)
        data += encode_failure_reason(reason)
        return self._pack_tm(service=1, subtype=2, data=data)

    def build_execution_failure(self, tc_apid: int, tc_seq: int,
                                error_code: int, reason: str = "") -> bytes:
        """Build Service 1 subtype 8 — Execution Failure.

        Wire format:  request_id(u32) error_code(u16) [failure notice]

        Same strictly-appended failure notice as S1.2 (defect #87): this is
        what carries "TM rate is forced to the beacon tier in beacon mode"
        to the ground instead of a bare ``1,8 ERR=7``.
        """
        request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
        data = struct.pack('>IH', request_id, error_code)
        data += encode_failure_reason(reason)
        return self._pack_tm(service=1, subtype=8, data=data)

    def build_time_report(self, cuc: TimeSourceResult | None = None) -> bytes:
        """Build Service 9 subtype 2 — Time Report.

        Body is the CUC in the same 6-octet wire form as the secondary header:
        ``coarse(u32) fine(u16)``. Pass seconds (float/int) or a ``CucTime``;
        omit it to report the on-board clock.
        """
        cuc_t = self._get_cuc() if cuc is None else _as_cuc(cuc)
        return self._pack_tm(service=9, subtype=2, data=cuc_t.pack(), cuc=cuc_t)

    def build_connection_test_report(self) -> bytes:
        """Build Service 17 subtype 2 — Connection Test Report."""
        cuc = self._get_cuc({})
        return self._pack_tm(service=17, subtype=2, data=b'', cuc=cuc)

    def build_param_value_report(self, param_id: int, value: float) -> bytes:
        """Build Service 20 Parameter Value Report (subtype 2)."""
        cuc = self._get_cuc({})
        data = struct.pack('>HBf', param_id, 1, value)
        return self._pack_tm(service=20, subtype=2, data=data, cuc=cuc)

    def _pack_tm(self, service: int, subtype: int, data: bytes,
                 cuc: TimeSourceResult | None = None,
                 seq_flags: int = 0b11) -> bytes:
        """Assemble a TM packet with the 9-octet PUS secondary header.

        ``cuc`` may be seconds (float/int) or a ``CucTime``. Omitting it reads
        the on-board clock — it does NOT mean "timestamp zero". Every packet
        the spacecraft emits carries on-board time (TIME_ARCHITECTURE.md rule 1),
        so there is no untimestamped path out of this builder.
        """
        cuc_t = self._get_cuc() if cuc is None else _as_cuc(cuc)
        sec_hdr = bytes([0x10, service & 0xFF, subtype & 0xFF]) + cuc_t.pack()
        payload = sec_hdr + data
        data_length_field = len(payload) + 1  # CCSDS: (octets after primary including CRC) - 1
        self._seq_count = (self._seq_count + 1) & 0x3FFF
        packet_id = (0 << 13) | (0 << 12) | (1 << 11) | (self._apid & 0x7FF)
        seq_ctrl = ((seq_flags & 0b11) << 14) | self._seq_count
        primary = struct.pack('>HHH', packet_id, seq_ctrl, data_length_field)
        packet = primary + payload
        crc = crc16_ccitt(packet)
        return packet + struct.pack('>H', crc)

    def pack_tm_segmented(self, service: int, subtype: int, *,
                          prefix, records: list[bytes],
                          cuc: TimeSourceResult | None = None) -> list[bytes]:
        """Emit a report as one packet, or several if it will not fit.

        A "report all" service — TM(12,13) monitoring definitions, TM(19,128)
        event-action rules, TM(11,18) the on-board schedule — has an unbounded
        record count, and building it as a single packet silently produces
        something no real link would carry. With the 41 shipped monitors,
        TM(12,13) came to **593 octets**, well over the 256-octet packet the
        mission is specified around (raised 2026-08-21).

        Segmentation uses the CCSDS grouping flags in the primary header, which
        is what they are for: 01 first, 00 continuation, 10 last, 11 standalone.
        The count field at the head of each packet is the number of records IN
        THAT PACKET, so a receiver that ignores the flags still parses every
        segment correctly — it just sees several short reports instead of one
        long one.

        Records must be fixed-size or self-delimiting; they are never split
        across a packet boundary.
        """
        # `prefix(n)` builds the report's own leading fields for a packet
        # carrying n records. It is a callable so each service keeps the field
        # ORDER it already has on the wire — TM(11,18) puts the count first,
        # TM(12,13) and TM(19,128) have nothing before it — and no existing
        # ground parser has to change.
        budget = (MAX_TM_PACKET_OCTETS - PRIMARY_HEADER_LEN - TM_SEC_HDR_LEN
                  - CRC_LEN - len(prefix(0)))
        chunks: list[list[bytes]] = [[]]
        used = 0
        for rec in records:
            if chunks[-1] and used + len(rec) > budget:
                chunks.append([]); used = 0
            chunks[-1].append(rec); used += len(rec)
        out = []
        for i, chunk in enumerate(chunks):
            if len(chunks) == 1:
                flags = 0b11                      # standalone
            elif i == 0:
                flags = 0b01                      # first
            elif i == len(chunks) - 1:
                flags = 0b10                      # last
            else:
                flags = 0b00                      # continuation
            body = prefix(len(chunk)) + b"".join(chunk)
            out.append(self._pack_tm(service, subtype, body, cuc=cuc,
                                     seq_flags=flags))
        return out

    def _get_cuc(self, params: Optional[dict] = None) -> CucTime:
        """Read the on-board clock. Raises if none is wired.

        There is **no wall-clock fallback**: see :class:`MissingTimeSourceError`.
        """
        if self._time_source is None:
            raise MissingTimeSourceError(
                "TMBuilder has no time_source: cannot timestamp a TM packet. "
                "Wire the OBDH clock in via TMBuilder(time_source=...) or "
                "set_time_source(). The host wall clock is not a substitute."
            )
        return _as_cuc(self._time_source())
