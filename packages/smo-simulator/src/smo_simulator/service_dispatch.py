"""SMO Simulator — PUS TC Service Dispatcher.

Clean class-based dispatcher. Routes incoming TC packets to appropriate
service handlers. Supports S1, S3, S5, S6, S8, S9, S11, S12, S15, S17,
S18, S19, S20.
"""
import struct
import logging
import threading
from typing import Any

from smo_simulator.monitoring_loader import (
    TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM,
)
from smo_simulator.memory_map import (
    MemoryImage, MemoryMap,
    ERR_ILLEGAL_ADDRESS, ERR_WRITE_PROTECTED, ERR_BAD_ARGUMENT,
    ERR_LENGTH_EXCEEDED,
)

logger = logging.getLogger(__name__)


class DispatchResult(list):
    """The TM packets a dispatch produced, plus how it ended (WP-7 / C-1).

    ``dispatch()`` used to signal failure through ``_last_error`` /
    ``_last_error_code`` / ``_last_exec_fail`` attributes on the dispatcher,
    which the engine reset before the call and read after it.  That mechanism
    is not reentrant, and three sources of nested dispatch are now live — S11
    time-tag release, S19 event-actions and S18 OBCP steps — so an inner
    command overwrote the outer command's flag and the outer command reported
    the inner one's outcome.

    The outcome now travels back with the packets.  This is a ``list``
    subclass so the ~220 existing call sites that iterate the return value
    keep working unchanged; the failure information is on the attributes.

    ``failure_kind``:
      ``""``            executed normally
      ``"execution"``   a subsystem refused it → engine sends S1.8
      ``"exec_start"``  the dispatcher already downlinked its own S1.4
    """

    __slots__ = ("error_code", "reason", "failure_kind")

    def __init__(self, packets=(), error_code: int = 0, reason: str = "",
                 failure_kind: str = ""):
        super().__init__(packets or [])
        self.error_code = int(error_code)
        self.reason = str(reason or "")
        self.failure_kind = failure_kind

    @property
    def failed(self) -> bool:
        return bool(self.failure_kind)

    @property
    def error(self):
        """``None`` when the command executed; otherwise the refusal."""
        if not self.failure_kind:
            return None
        return {"error_code": self.error_code, "reason": self.reason,
                "kind": self.failure_kind}

# Fallback only. The real value is the configured payload image size
# (subsystems/payload.yaml image_size_mb); _default_image_size_mb() reads it
# from the live payload model and only falls back to this when the model is
# unavailable (bare-dispatcher unit tests). 4 MB per defect #82 / roadmap N-11.
DEFAULT_IMAGE_SIZE_MB = 4


class ServiceDispatcher:
    """Dispatches PUS TC packets to service-specific handlers."""

    def __init__(self, engine):
        # RLock: S18 OBCP handlers emit S5 events synchronously via
        # engine._emit_event, which calls back into trigger_event_action on
        # this same dispatcher (same thread). A plain Lock would deadlock on
        # that re-entry; RLock keeps all existing semantics otherwise.
        self._lock = threading.RLock()
        self._engine = engine
        # ── Per-dispatch outcome accumulators (WP-7) ────────────────────
        # These are internal to one `dispatch()` call: it saves and restores
        # them around the handler so a nested dispatch (S11 release, S19
        # event-action, S18 OBCP step) cannot overwrite the outer command's
        # outcome. Nothing outside this class reads them — the outcome leaves
        # via the returned DispatchResult.
        self._err_reason = ""
        self._err_code = 0
        #: Set by generate_s1_exec_fail() — the code and reason of an S1.4
        #: execution-START failure the dispatcher downlinked itself.
        #: ``_err_reason`` deliberately stays empty for these (that one drives
        #: the S1.8), but the engine must not then report S1.7 "execution
        #: complete", and the failure must still reach the event log
        #: (defect #88).
        self._exec_fail: dict | None = None
        #: Sequence count of the telecommand being dispatched, so an S1.4
        #: execution-start failure can be attributed to it on the ground.
        #: Every one of the 17 S1.4 sites used to pass a literal 0, and the
        #: MCS correlates verification rows by tc_seq, so those refusals
        #: never turned a row red (O-4).
        self._request_id = 0

        # S1 Request Verification — enhanced subtypes
        self._s1_execution_started = False

        # S3 Housekeeping — dynamic HK definitions (for S3.1/2 create/delete)
        self._s3_custom_sids: dict[int, dict] = {}

        # S5 Event Reporting — enabled event types (set of type IDs)
        self._s5_enabled_types: set[int] = set(range(256))

        # S12 On-Board Monitoring — parameter monitoring definitions
        self._s12_definitions: dict[int, dict] = {}
        self._s12_enabled: bool = True

        # S19 Event-Action — event-action definitions
        self._s19_definitions: dict[int, dict] = {}
        self._s19_enabled_ids: set[int] = set()

        # S13 Large Data Transfer — active transfers
        self._s13_transfers: dict[int, dict] = {}
        self._s13_transfer_counter: int = 1

        # S6 Memory Management — sparse patchable image over the declared map.
        # The engine replaces this with the mission's config-declared map (and
        # a live-model back-reference) right after construction; the built-in
        # default keeps unit tests with mock engines working.
        self._memory = MemoryImage(MemoryMap.default(), engine)

    def generate_s1_reports(self, request_id: int, service: int, subtype: int
                           ) -> list[bytes]:
        """Generate S1 execution start (3/4) and progress (5/6) reports.

        Called by engine around command dispatch to provide full S1 lifecycle:
        S1.1 (accepted) → S1.3 (exec start) → S1.5 (progress) → S1.7 (completed)
        """
        reports = []
        tm = self._engine.tm_builder
        # S1.3 — Execution start success
        req_data = struct.pack('>H', request_id)
        reports.append(tm._pack_tm(service=1, subtype=3, data=req_data))
        return reports

    def generate_s1_progress(self, request_id: int, step: int) -> list[bytes]:
        """Generate S1.5 execution progress report."""
        tm = self._engine.tm_builder
        data = struct.pack('>HB', request_id, step)
        return [tm._pack_tm(service=1, subtype=5, data=data)]

    def generate_s1_exec_fail(self, request_id: int, error_code: int,
                              reason: str = "") -> list[bytes]:
        """Generate S1.4 execution start failure report.

        Wire format:  request_id(u16) error_code(u16) [failure notice]

        The failure notice (``reason_len(u8) + UTF-8``) is appended strictly
        after the historical ``>HH`` prefix, so a legacy parser still decodes
        the request id and the code (defect #87). Without it the operator got
        a bare numeric code and had to guess which of the several gates that
        share code 0x0007 had actually refused the command.

        The failure is also recorded on ``_exec_fail``, which the returned
        ``DispatchResult`` carries out to the engine, so the engine can
        (a) suppress the S1.7 "execution complete" that used to follow an S1.4
        — the two reports contradicted each other and the green row won — and
        (b) raise COMMAND_REJECTED, which is what puts the code in the event
        log and the FDIR journal (defect #88). Note the S1.4 request id is
        Phase G: ``request_id`` now carries the telecommand's real sequence
        count (``self._request_id``, set per dispatch) instead of the literal
        0 every one of the seventeen sites used to pass — the MCS correlates
        verification rows by ``tc_seq``, so with 0 those refusals could never
        turn a row red.
        """
        from smo_common.protocol.pus_services import encode_failure_reason
        tm = self._engine.tm_builder
        data = struct.pack('>HH', request_id, error_code)
        data += encode_failure_reason(reason)
        self._exec_fail = {"error_code": int(error_code),
                           "reason": str(reason or "")}
        return [tm._pack_tm(service=1, subtype=4, data=data)]

    def dispatch(self, service: int, subtype: int, data: bytes,
                 primary_header=None) -> "DispatchResult":
        """Dispatch a TC and return its TM packets *and* its outcome.

        The return value is a ``DispatchResult`` — a list of TM packets that
        also carries ``error_code`` / ``reason`` / ``failure_kind``.  It is
        reentrant: the per-dispatch accumulators are saved on entry and
        restored on exit, so a nested dispatch (S11 release, S19 event-action,
        S18 OBCP step) reports its own outcome and leaves the outer command's
        alone (WP-7 / C-1).
        """
        with self._lock:
            outer = (self._err_reason, self._err_code, self._exec_fail,
                     self._request_id)
            self._err_reason = ""
            self._err_code = 0
            self._exec_fail = None
            self._request_id = int(
                getattr(primary_header, 'sequence_count', 0) or 0)
            try:
                return self._dispatch_inner(service, subtype, data,
                                            primary_header)
            finally:
                (self._err_reason, self._err_code, self._exec_fail,
                 self._request_id) = outer

    def _dispatch_inner(self, service: int, subtype: int, data: bytes,
                        primary_header=None) -> "DispatchResult":
        responses = []
        try:
            if service == 2:
                responses = self._handle_s2(subtype, data, primary_header)
            elif service == 3:
                responses = self._handle_s3(subtype, data)
            elif service == 5:
                responses = self._handle_s5(subtype, data)
            elif service == 6:
                responses = self._handle_s6(subtype, data)
            elif service == 8:
                responses = self._handle_s8(subtype, data)
            elif service == 9:
                responses = self._handle_s9(subtype, data)
            elif service == 11:
                responses = self._handle_s11(subtype, data)
            elif service == 12:
                responses = self._handle_s12(subtype, data)
            elif service == 13:
                responses = self._handle_s13(subtype, data)
            elif service == 15:
                responses = self._handle_s15(subtype, data)
            elif service == 17:
                responses = self._handle_s17(subtype, data)
            elif service == 18:
                responses = self._handle_s18(subtype, data)
            elif service == 19:
                responses = self._handle_s19(subtype, data)
            elif service == 20:
                responses = self._handle_s20(subtype, data)
            else:
                logger.debug("Unhandled service %d subtype %d", service, subtype)
        except Exception as e:
            logger.warning("Service %d dispatch error: %s", service, e)
        if self._err_reason:
            return DispatchResult(responses, self._err_code,
                                  self._err_reason, "execution")
        if self._exec_fail:
            return DispatchResult(responses,
                                  self._exec_fail["error_code"],
                                  self._exec_fail["reason"], "exec_start")
        return DispatchResult(responses)

    # ─── S2 Device Access ───────────────────────────────────────────
    # PUS Service 2 — low-level device access (on/off, register load/dump)
    # Subtypes:
    #   1 = Distribute on/off
    #   2 = Distribute register load
    #   3 = Distribute register dump
    #   5 = Distribute on/off with verification
    #   6 = Report on/off status

    def _handle_s2(self, subtype: int, data: bytes, primary_header=None) -> list[bytes]:
        """S2 Device Access — device-level on/off control and register access."""
        responses = []
        if subtype == 1 and len(data) >= 3:
            # S2.1: Distribute on/off — device_id(2) + state(1)
            device_id = struct.unpack('>H', data[:2])[0]
            state = bool(data[2])
            success = self._set_device_state(device_id, state)
            if success:
                logger.info("S2.1 DEVICE ON/OFF: device_id=0x%04X, state=%d", device_id, state)
                # S1 verification (accept/start/complete) is emitted centrally by
                # the engine's _dispatch_tc, so the handler does not generate its
                # own. (The previous code indexed the PrimaryHeader object as if
                # it were bytes — `len(primary_header)` — raising mid-dispatch.)
            else:
                logger.warning("S2.1 DEVICE ON/OFF failed: device_id=0x%04X not found", device_id)
        elif subtype == 5 and len(data) >= 3:
            # S2.5: Distribute on/off with verification
            device_id = struct.unpack('>H', data[:2])[0]
            state = bool(data[2])
            success = self._set_device_state(device_id, state)
            if success:
                logger.info("S2.5 DEVICE ON/OFF WITH VERIFY: device_id=0x%04X, state=%d", device_id, state)
                # Verify the state was set
                actual_state = self._get_device_state(device_id)
                resp_data = struct.pack('>HB', device_id, int(actual_state))
                responses.append(self._engine.tm_builder._pack_tm(
                    service=2, subtype=6, data=resp_data
                ))
                # S1 verification emitted centrally by _dispatch_tc (see S2.1).
            else:
                logger.warning("S2.5 DEVICE ON/OFF WITH VERIFY failed: device_id=0x%04X", device_id)
        elif subtype == 6 and len(data) >= 2:
            # S2.6: Report on/off status — device_id(2)
            device_id = struct.unpack('>H', data[:2])[0]
            state = self._get_device_state(device_id)
            resp_data = struct.pack('>HB', device_id, int(state))
            responses.append(self._engine.tm_builder._pack_tm(
                service=2, subtype=6, data=resp_data
            ))
            logger.info("S2.6 DEVICE STATUS REPORT: device_id=0x%04X, state=%d", device_id, int(state))
        return responses

    def _set_device_state(self, device_id: int, on_off: bool) -> bool:
        """Set device on/off state. Maps device_id to subsystem and calls its method."""
        subsys_map = {
            (0x0100, 0x010F): "eps",
            (0x0200, 0x021F): "aocs",
            (0x0300, 0x030F): "tcs",
            (0x0400, 0x040F): "ttc",
            (0x0500, 0x050F): "obdh",
            (0x0600, 0x060F): "payload",
        }
        subsystem_name = None
        for (min_id, max_id), name in subsys_map.items():
            if min_id <= device_id <= max_id:
                subsystem_name = name
                break
        if not subsystem_name:
            return False
        subsys = self._engine.subsystems.get(subsystem_name)
        if not subsys:
            return False
        ok = subsys.set_device_state(device_id, on_off)
        # S2_PAYLOAD_COOLER (0x0601) is the fourth command path to the single
        # cryocooler — keep the unified TCS cooler flag in agreement
        # (brief 05 WP-2).
        if ok and device_id == 0x0601:
            self._cmd(self._engine.subsystems.get("tcs"),
                      {"command": "fpa_cooler", "on": bool(on_off)})
        return ok

    def _get_device_state(self, device_id: int) -> bool:
        """Get device on/off state."""
        subsys_map = {
            (0x0100, 0x010F): "eps",
            (0x0200, 0x021F): "aocs",
            (0x0300, 0x030F): "tcs",
            (0x0400, 0x040F): "ttc",
            (0x0500, 0x050F): "obdh",
            (0x0600, 0x060F): "payload",
        }
        subsystem_name = None
        for (min_id, max_id), name in subsys_map.items():
            if min_id <= device_id <= max_id:
                subsystem_name = name
                break
        if not subsystem_name:
            return False
        subsys = self._engine.subsystems.get(subsystem_name)
        if not subsys:
            return False
        return subsys.get_device_state(device_id)

    # ─── S3 Housekeeping ─────────────────────────────────────────────
    # ECSS PUS-C subtype numbering:
    #   5 = enable periodic generation, 6 = disable, 27 = one-shot request
    #   31 = modify interval
    # Legacy subtypes 3/4/5 still accepted for backwards compatibility

    def _is_bootloader_beacon_sid(self, sid: int) -> bool:
        """True if `sid` is the beacon HK and the OBC is in the bootloader."""
        from smo_simulator.engine import BOOTLOADER_BEACON_SID
        if int(sid) != BOOTLOADER_BEACON_SID:
            return False
        obdh = self._engine.subsystems.get("obdh")
        sw_image = getattr(getattr(obdh, "_state", None), "sw_image", None)
        if sw_image is None:
            try:
                sw_image = int(self._engine.params.get(0x0311, 1))
            except Exception:  # noqa: BLE001 — MagicMock engines
                return False
        try:
            return int(sw_image) == 0  # SW_BOOTLOADER
        except (TypeError, ValueError):
            return False

    def _handle_s3(self, subtype: int, data: bytes) -> list[bytes]:
        """S3 Housekeeping requests."""
        if subtype == 1 and len(data) >= 4:
            # Create HK definition: SID(2) + interval_s(float,4) + param_count(1) + param_ids(2 each)
            sid = struct.unpack('>H', data[:2])[0]
            interval_s = struct.unpack('>f', data[2:6])[0] if len(data) >= 6 else 4.0
            param_ids = []
            offset = 7 if len(data) > 6 else 6
            count = data[6] if len(data) > 6 else 0
            for i in range(count):
                if offset + 2 <= len(data):
                    pid = struct.unpack('>H', data[offset:offset+2])[0]
                    param_ids.append(pid)
                    offset += 2
            self._s3_custom_sids[sid] = {
                'sid': sid,
                'interval_s': max(1.0, interval_s),
                'param_ids': param_ids,
            }
            self._engine.set_hk_enabled(sid, True)
            logger.info("S3 CREATE HK SID %d, interval=%.1fs, %d params",
                       sid, interval_s, len(param_ids))
        elif subtype == 2 and len(data) >= 2:
            # Delete HK definition
            sid = struct.unpack('>H', data[:2])[0]
            self._s3_custom_sids.pop(sid, None)
            self._engine.set_hk_enabled(sid, False)
            logger.info("S3 DELETE HK SID %d", sid)
        elif subtype == 27 and len(data) >= 2:
            sid = struct.unpack('>H', data[:2])[0]
            hk_struct = self._engine._hk_structures.get(sid)
            pkt = self._engine.tm_builder.build_hk_packet(
                sid, self._engine.params, hk_structure=hk_struct)
            if pkt:
                return [pkt]
        elif subtype in (3, 5) and len(data) >= 2:
            # Enable periodic HK (5 = ECSS, 3 = legacy)
            sid = struct.unpack('>H', data[:2])[0]
            self._engine.set_hk_enabled(sid, True)
        elif subtype in (4, 6) and len(data) >= 2:
            # Disable periodic HK (6 = ECSS, 4 = legacy)
            sid = struct.unpack('>H', data[:2])[0]
            if self._is_bootloader_beacon_sid(sid):
                # SID 11 on APID 2 is the *only* telemetry the bootloader
                # produces. Disabling it there would make the spacecraft
                # silent with no way to command it back — the OBSW refuses
                # (defect #75). It used to be accepted.
                logger.warning(
                    "S3 HK_DISABLE SID %d refused — beacon HK is mandatory "
                    "while the OBC is in the bootloader", sid)
                return self.generate_s1_exec_fail(
                    self._request_id, 0x0005,
                    f"HK SID {sid} (beacon) is mandatory while the OBC is in "
                    f"the bootloader - disabling it would silence the "
                    f"spacecraft")
            self._engine.set_hk_enabled(sid, False)
        elif subtype == 31 and len(data) >= 6:
            # Modify HK interval (ECSS subtype 31)
            sid = struct.unpack('>H', data[:2])[0]
            interval_s = struct.unpack('>f', data[2:6])[0]
            self._engine.set_hk_interval(sid, max(1.0, interval_s))
        elif subtype == 5 and len(data) >= 6:
            # Legacy: subtype 5 with 6 bytes = modify interval
            sid = struct.unpack('>H', data[:2])[0]
            interval_s = struct.unpack('>f', data[2:6])[0]
            self._engine.set_hk_interval(sid, max(1.0, interval_s))
        return []

    # ─── S5 Event Reporting ──────────────────────────────────────────

    def _handle_s5(self, subtype: int, data: bytes) -> list[bytes]:
        """S5 Event Reporting — enable/disable event types."""
        if subtype == 5 and len(data) >= 1:
            # Enable event type
            event_type = data[0]
            self._s5_enabled_types.add(event_type)
        elif subtype == 6 and len(data) >= 1:
            # Disable event type
            event_type = data[0]
            self._s5_enabled_types.discard(event_type)
        elif subtype == 7:
            # Enable ALL event types
            for etype in range(1, 5):
                self._s5_enabled_types.add(etype)
        elif subtype == 8:
            # Disable ALL event types
            self._s5_enabled_types.clear()
        return []

    def is_event_enabled(self, event_type: int) -> bool:
        """Check if an event type is enabled for reporting."""
        with self._lock:
            return event_type in self._s5_enabled_types

    # ─── S6 Memory Management ────────────────────────────────────────
    #
    # The address space is declared once, in
    # configs/<mission>/memory/memory_map.yaml, and loaded into
    # smo_simulator.memory_map.MemoryMap. `self._memory` is a sparse,
    # page-backed MemoryImage over that map: a MEM_LOAD is *stored*, so
    # MEM_DUMP reads the patch back and MEM_CHECK's CRC changes — which is the
    # entire point of an upload/verify workflow and did not work before
    # (defect #71). Regions may declare a `binding:` onto live model state
    # (the AOCS torque-gain block is the first instance), so a patch to a bound
    # region changes spacecraft behaviour and stays coherent with the sim.
    #
    # Wire formats. Both the catalogued form (with the leading `memory_id`
    # byte, which the shipped bootloader-recovery procedures use) and the
    # historical form (address first) are accepted:
    #   MEM_LOAD  (6,2)  [memory_id(u8)] + address(u32) + data[]
    #   MEM_DUMP  (6,5)  [memory_id(u8)] + address(u32) + length(u16)
    #   MEM_CHECK (6,9)  [memory_id(u8)] + address(u32) + length(u16)
    # Dump/check are disambiguated by length (6 vs 7 bytes). MEM_LOAD is
    # disambiguated by the map: whichever reading lands in a declared region
    # wins, with the catalogued form preferred when both do. This is what fixes
    # a catalog-shaped MEM_LOAD to 0x00100000 landing on 0x00001000.

    def _memory_image(self):
        """The engine's MemoryImage, or this dispatcher's own default one."""
        img = getattr(self._engine, "_memory", None)
        if isinstance(img, MemoryImage):
            return img
        return self._memory

    def _decode_mem_request(self, data: bytes, with_length: bool):
        """Decode a S6 address[/length] request in either wire format.

        Returns (memory_id, address, length, payload) or None if malformed.
        """
        fixed = 6 if with_length else 4
        candidates = []
        # Catalogued form: memory_id(u8) + address(u32) [+ length(u16)]
        if len(data) >= fixed + 1:
            addr = struct.unpack('>I', data[1:5])[0]
            length = struct.unpack('>H', data[5:7])[0] if with_length else 0
            candidates.append((data[0], addr, length, data[7:] if with_length
                               else data[5:]))
        # Historical form: address(u32) [+ length(u16)]
        if len(data) >= fixed:
            addr = struct.unpack('>I', data[:4])[0]
            length = struct.unpack('>H', data[4:6])[0] if with_length else 0
            candidates.append((0, addr, length, data[6:] if with_length
                               else data[4:]))
        if not candidates:
            return None
        mmap = self._memory_image().map
        for cand in candidates:
            probe = cand[2] if with_length else max(1, len(cand[3]))
            if mmap.is_mapped(cand[1], probe):
                return cand
        # Nothing maps — the request will be rejected either way. Report the
        # historical (address-first) reading so the failure names the address
        # the operator actually typed.
        return candidates[-1]

    def _crc16_ccitt(self, data: bytes) -> int:
        """Calculate CRC-16-CCITT checksum."""
        crc = 0xFFFF
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                crc <<= 1
                if crc & 0x10000:
                    crc ^= 0x1021
                crc &= 0xFFFF
        return crc

    def _is_memory_readonly(self, addr: int, length: int = 1) -> bool:
        """True if the declared region covering `addr` is not writable."""
        return self._memory_image().map.is_readonly(addr, length)

    def _generate_memory_content(self, addr: int, length: int) -> bytes:
        """Read `length` bytes at `addr` (patches over declared fill patterns)."""
        return self._memory_image().read(addr, length)

    def _handle_s6(self, subtype: int, data: bytes) -> list[bytes]:
        """S6 Memory Management — load / dump / check with CRC-16."""
        image = self._memory_image()
        mmap = image.map
        responses: list[bytes] = []

        if subtype == 2 and len(data) >= 5:
            # MEM_LOAD
            decoded = self._decode_mem_request(data, with_length=False)
            if decoded is None:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_LOAD: malformed request")
            _mem_id, addr, _len, payload_data = decoded
            if not payload_data:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_LOAD: no data to write")
            if len(payload_data) > mmap.limits.max_load_bytes:
                logger.warning("S6 MEM_LOAD rejected — %d bytes exceeds limit %d",
                               len(payload_data), mmap.limits.max_load_bytes)
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_LENGTH_EXCEEDED,
                    f"S6 MEM_LOAD: {len(payload_data)} bytes exceeds the "
                    f"{mmap.limits.max_load_bytes}-byte load limit")
            region = mmap.region_at(addr, len(payload_data))
            if region is None:
                logger.warning(
                    "S6 MEM_LOAD rejected — 0x%08X+%d is not in any declared region",
                    addr, len(payload_data))
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_ILLEGAL_ADDRESS,
                    f"S6 MEM_LOAD: 0x{addr:08X}+{len(payload_data)} is not in "
                    f"any declared memory region")
            if not region.writable:
                logger.warning(
                    "S6 MEM_LOAD rejected — region '%s' is read-only (0x%08X)",
                    region.name, addr)
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_WRITE_PROTECTED,
                    f"S6 MEM_LOAD: region '{region.name}' at 0x{addr:08X} is "
                    f"read-only")
            image.write(addr, payload_data)
            logger.info("S6 MEM_LOAD at 0x%08X, %d bytes into '%s'",
                        addr, len(payload_data), region.name)
            # S1.5 progress for multi-step memory load
            responses.extend(self.generate_s1_progress(0, 1))

        elif subtype == 5 and len(data) >= 6:
            # MEM_DUMP → TM(6,6)
            decoded = self._decode_mem_request(data, with_length=True)
            if decoded is None:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_DUMP: malformed request")
            _mem_id, addr, length, _rest = decoded
            if length <= 0:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_DUMP: zero-length request")
            if length > mmap.limits.max_dump_bytes:
                logger.warning("S6 MEM_DUMP rejected — %d bytes exceeds limit %d",
                               length, mmap.limits.max_dump_bytes)
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_LENGTH_EXCEEDED,
                    f"S6 MEM_DUMP: {length} bytes exceeds the "
                    f"{mmap.limits.max_dump_bytes}-byte dump limit")
            if not mmap.is_mapped(addr, length):
                logger.warning("S6 MEM_DUMP rejected — 0x%08X+%d unmapped",
                               addr, length)
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_ILLEGAL_ADDRESS,
                    f"S6 MEM_DUMP: 0x{addr:08X}+{length} is not mapped")
            mem_content = image.read(addr, length)
            logger.info("S6 MEM_DUMP at 0x%08X, %d bytes", addr, len(mem_content))
            mem_data = (struct.pack('>I', addr)
                        + struct.pack('>H', len(mem_content)) + mem_content)
            return [self._engine.tm_builder._pack_tm(
                service=6, subtype=6, data=mem_data
            )]

        elif subtype == 9 and len(data) >= 6:
            # MEM_CHECK → TM(6,10)
            decoded = self._decode_mem_request(data, with_length=True)
            if decoded is None:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_CHECK: malformed request")
            _mem_id, addr, length, _rest = decoded
            if length <= 0:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT, "S6 MEM_CHECK: zero-length request")
            if length > mmap.limits.max_check_bytes:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_LENGTH_EXCEEDED,
                    f"S6 MEM_CHECK: {length} bytes exceeds the "
                    f"{mmap.limits.max_check_bytes}-byte check limit")
            if not mmap.is_mapped(addr, length):
                logger.warning("S6 MEM_CHECK rejected — 0x%08X+%d unmapped",
                               addr, length)
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_ILLEGAL_ADDRESS,
                    f"S6 MEM_CHECK: 0x{addr:08X}+{length} is not mapped")
            mem_content = image.read(addr, length)
            crc = self._crc16_ccitt(mem_content)
            logger.info("S6 MEM_CHECK at 0x%08X, %d bytes, CRC=0x%04X",
                        addr, length, crc)
            resp = struct.pack('>IHH', addr, length, crc)
            return [self._engine.tm_builder._pack_tm(
                service=6, subtype=10, data=resp
            )]
        return responses

    # ─── S8 Function Management ──────────────────────────────────────

    def _handle_s8(self, subtype: int, data: bytes) -> list[bytes]:
        """S8 Function Management — subsystem commands."""
        if subtype != 1 or len(data) < 1:
            return []
        func_id = data[0]
        # Route based on function ID ranges
        if func_id in range(0, 16):
            return self._route_aocs_cmd(func_id, data[1:])
        elif func_id in range(16, 26):
            return self._route_eps_cmd(func_id, data[1:])
        elif func_id in range(26, 40):
            return self._route_payload_cmd(func_id, data[1:])
        elif func_id in range(40, 50):
            return self._route_tcs_cmd(func_id, data[1:])
        elif func_id in range(50, 63):
            return self._route_obdh_cmd(func_id, data[1:])
        elif func_id in range(63, 80):
            # 63-78 plus TTC_SET_DOWNLINK_MODE (79), the gate on the HIGH tier
            # of the TM rate ladder (defect #85).
            return self._route_ttc_cmd(func_id, data[1:])
        elif func_id == 80:
            return self._route_obdh_cmd(func_id, data[1:])
        elif func_id in (81, 82):
            return self._route_eps_cmd(func_id, data[1:])
        elif func_id == 83:  # EPS mode (defect #27) — set safe/emergency mode
            return self._route_eps_cmd(func_id, data[1:])
        elif func_id == 84:  # AOCS GPS start-mode select (COLD/WARM/HOT)
            return self._route_aocs_cmd(func_id, data[1:])
        elif func_id in range(85, 92):  # Phase C AOCS pointing/estimator cmds
            return self._route_aocs_cmd(func_id, data[1:])
        elif func_id in range(92, 96):
            # Phase E payload extras (WP-7): 92 SELECT_FILTER,
            # 93 CYCLE_SHUTTER, 94 INITIATE_TRANSFER, 95 CAL_LAMP —
            # allocated from the free block 92-99 (96-99 remain free).
            return self._route_payload_cmd(func_id, data[1:])
        elif func_id in range(108, 119):
            # Phase D propulsion block 108-118. NOTE: brief 03 planned 85-95,
            # but Phase C's AOCS pointing commands took 85-91 first and
            # 100-107 are the legacy quick actions, so 108-118 is the next
            # clean contiguous block (free = 92-99 + 108-127).
            return self._route_prop_cmd(func_id, data[1:])
        elif func_id in range(100, 108):
            # Legacy quick-action funcs 100–107 from tc_catalog.yaml (MCS quick
            # buttons). They have no fields; map each to its intended existing
            # EPS/TCS handler (defect B: these previously fell through to []).
            return self._route_legacy_quick_action(func_id)
        return []

    def _route_prop_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        """Phase D propulsion commands (S8 funcs 108-118).

        108 PROP_ARM(u16 code)   109 PROP_DISARM      110 PROP_SET_BURN
        111 PROP_EXECUTE         112 PROP_ABORT       113 PROP_ISO_OPEN(u8)
        114 PROP_ISO_CLOSE(u8)   115 PROP_SET_LIMITS  116 PROP_INHIBIT_SET(u8)
        117 PROP_INHIBIT_CLR(u16 key)                 118 PROP_GET_STATUS
        """
        prop = self._engine.subsystems.get("prop")
        if not prop:
            return []
        if func_id == 108:  # PROP_ARM: arm_code (u16, 0xA5C3)
            code = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            self._cmd(prop, {"command": "arm", "arm_code": code})
        elif func_id == 109:  # PROP_DISARM
            self._cmd(prop, {"command": "disarm"})
        elif func_id == 110:  # PROP_SET_BURN: dv f32 + direction u8 + mode u8 + t_start u32
            if len(data) >= 10:
                dv, direction, mode, t_start = struct.unpack('>fBBI', data[:10])
                self._cmd(prop, {"command": "set_burn", "dv_m_s": dv,
                                 "direction": direction, "mode": mode,
                                 "t_start_cuc": t_start})
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "PROP_SET_BURN needs dv(f32)+direction(u8)"
                               "+mode(u8)+t_start(u32)"})
        elif func_id == 111:  # PROP_EXECUTE
            self._cmd(prop, {"command": "execute"})
        elif func_id == 112:  # PROP_ABORT
            self._cmd(prop, {"command": "abort"})
        elif func_id == 113:  # PROP_ISO_OPEN: branch u8 (0=A,1=B,2=both)
            branch = data[0] if data else 2
            self._cmd(prop, {"command": "iso_open", "branch": branch})
        elif func_id == 114:  # PROP_ISO_CLOSE: branch u8 (2=both, CTG-028)
            branch = data[0] if data else 2
            self._cmd(prop, {"command": "iso_close", "branch": branch})
        elif func_id == 115:  # PROP_SET_LIMITS: 4x f32
            if len(data) >= 16:
                att, p_min, cb_max, t_max = struct.unpack('>ffff', data[:16])
                self._cmd(prop, {"command": "set_limits",
                                 "att_err_max_deg": att, "p_min_bar": p_min,
                                 "catbed_max_c": cb_max, "t_burn_max_s": t_max})
        elif func_id == 116:  # PROP_INHIBIT_SET: on u8
            on = bool(data[0]) if data else True
            self._cmd(prop, {"command": "inhibit_set", "on": on})
        elif func_id == 117:  # PROP_INHIBIT_CLR: key u16
            key = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            self._cmd(prop, {"command": "inhibit_clr", "key": key})
        elif func_id == 118:  # PROP_GET_STATUS -> TM(8,2) status report
            result = self._cmd(prop, {"command": "get_status"})
            st = result.get("status") or {}
            if st:
                resp = struct.pack(
                    '>BffffHB',
                    int(st.get("state", 0)) & 0xFF,
                    float(st.get("tank_p_bar", 0.0)),
                    float(st.get("propellant_g", 0.0)),
                    float(st.get("catbed_c", 0.0)),
                    float(st.get("dv_remaining_m_s", 0.0)),
                    int(st.get("arm_inhibit_mask", 0)) & 0xFFFF,
                    int(st.get("abort_reason", 0)) & 0xFF,
                )
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=resp)]
        return []

    def _route_legacy_quick_action(self, func_id: int) -> list[bytes]:
        """Route legacy MCS quick-action funcs 100–107 to existing handlers.

        100/101: Solar array A ON/OFF   -> EPS enable_array/disable_array A
        102/103: Solar array B ON/OFF   -> EPS enable_array/disable_array B
        104/105: Battery heater ON/OFF  -> TCS heater circuit=battery
        106/107: OBC heater ON/OFF      -> TCS heater circuit=obc
        """
        eps = self._engine.subsystems.get("eps")
        tcs = self._engine.subsystems.get("tcs")
        if func_id == 100 and eps:
            self._cmd(eps, {"command": "enable_array", "array": "A"})
        elif func_id == 101 and eps:
            self._cmd(eps, {"command": "disable_array", "array": "A"})
        elif func_id == 102 and eps:
            self._cmd(eps, {"command": "enable_array", "array": "B"})
        elif func_id == 103 and eps:
            self._cmd(eps, {"command": "disable_array", "array": "B"})
        elif func_id == 104 and tcs:
            self._cmd(tcs, {"command": "heater", "circuit": "battery", "on": True})
        elif func_id == 105 and tcs:
            self._cmd(tcs, {"command": "heater", "circuit": "battery", "on": False})
        elif func_id == 106 and tcs:
            self._cmd(tcs, {"command": "heater", "circuit": "obc", "on": True})
        elif func_id == 107 and tcs:
            self._cmd(tcs, {"command": "heater", "circuit": "obc", "on": False})
        return []

    def _route_aocs_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        aocs = self._engine.subsystems.get("aocs")
        if not aocs:
            return []
        if func_id == 0:  # set mode (0-9; FINE entry gated on 0x02C5)
            # WP-6: AOCS_SET_MODE(5) with unmet preconditions is REFUSED by
            # the model; _cmd captures the failure and the engine downlinks
            # TM(1,8) with the 0x02C5 bitmask in the failure notice — no more
            # silent accept-then-drop-back.
            mode = data[0] if data else 0
            self._cmd(aocs, {"command": "set_mode", "mode": mode})
        elif func_id == 1:  # desaturate
            self._cmd(aocs, {"command": "desaturate"})
        elif func_id == 2:  # disable wheel
            wheel = data[0] if data else 0
            self._cmd(aocs, {"command": "disable_wheel", "wheel": wheel})
        elif func_id == 3:  # enable wheel
            wheel = data[0] if data else 0
            self._cmd(aocs, {"command": "enable_wheel", "wheel": wheel})
        elif func_id == 4:  # ST1 power
            on = bool(data[0]) if data else True
            self._cmd(aocs, {"command": "st_power", "unit": 1, "on": on})
        elif func_id == 5:  # ST2 power
            on = bool(data[0]) if data else True
            self._cmd(aocs, {"command": "st_power", "unit": 2, "on": on})
        elif func_id == 6:  # ST select
            unit = data[0] if data else 1
            self._cmd(aocs, {"command": "st_select", "unit": unit})
        elif func_id == 7:  # Magnetometer select (defect #26)
            # unit byte: 0 = primary (Mag A), 1 = redundant (Mag B). Send a
            # concrete 'A'/'B' source string so the handler's A/B selection
            # branch runs instead of the legacy bool path (which only set
            # mag_valid and never actually switched the active magnetometer).
            unit = int(data[0]) if data else 0
            self._cmd(aocs, {"command": "mag_select", "source": "B" if unit else "A"})
        elif func_id == 8:  # RW set speed bias
            if len(data) >= 5:
                wheel = data[0]
                bias = struct.unpack('>f', data[1:5])[0]
                self._cmd(aocs, {
                    "command": "rw_set_speed_bias", "wheel": wheel, "bias": bias
                })
        elif func_id == 9:  # MTQ enable/disable
            on = bool(data[0]) if data else True
            cmd = "mtq_enable" if on else "mtq_disable"
            self._cmd(aocs, {"command": cmd})
        elif func_id == 10:  # AOCS_SLEW_TO: quaternion (4 floats) + rate (1 float)
            if len(data) >= 20:
                q = [
                    struct.unpack('>f', data[0:4])[0],
                    struct.unpack('>f', data[4:8])[0],
                    struct.unpack('>f', data[8:12])[0],
                    struct.unpack('>f', data[12:16])[0],
                ]
                rate_dps = struct.unpack('>f', data[16:20])[0]
                self._cmd(aocs, {
                    "command": "slew_to_quaternion",
                    "quaternion": q,
                    "rate_dps": rate_dps
                })
        elif func_id == 11:  # AOCS_CHECK_MOMENTUM
            result = self._cmd(aocs, {"command": "check_momentum"})
            if result.get("success"):
                momentum = result.get("total_momentum_nms", 0.0)
                saturation = result.get("saturation_percent", 0.0)
                resp_data = struct.pack('>ff', momentum, saturation)
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=130, data=resp_data
                )]
        elif func_id == 12:  # AOCS_BEGIN_ACQUISITION
            self._cmd(aocs, {"command": "begin_acquisition"})
        elif func_id == 13:  # AOCS_GYRO_CALIBRATION
            self._cmd(aocs, {"command": "gyro_calibration"})
        elif func_id == 14:  # AOCS_RW_RAMP_DOWN: wheel_idx (1) + target_rpm (4)
            wheel = data[0] if data else 255  # 255 means all wheels
            target_rpm = struct.unpack('>f', data[1:5])[0] if len(data) >= 5 else 0.0
            self._cmd(aocs, {
                "command": "rw_ramp_down",
                "wheel": wheel,
                "target_rpm": target_rpm
            })
        elif func_id == 15:  # AOCS_SET_DEADBAND: deadband_deg (4 bytes float)
            deadband = struct.unpack('>f', data[0:4])[0] if len(data) >= 4 else 0.01
            self._cmd(aocs, {
                "command": "set_deadband",
                "deadband_deg": deadband
            })
        elif func_id == 84:  # AOCS_GPS_SET_START_MODE: start_mode (1 byte)
            # 0=COLD, 1=WARM, 2=HOT. Selects the GPS time-to-first-fix profile
            # consumed by the AOCS GPS model (gps_start_mode). Without this the
            # model field/TTFF logic existed but no command could set it.
            start_mode = int(data[0]) if data else 0
            self._cmd(aocs, {
                "command": "gps_set_start_mode",
                "start_mode": start_mode,
            })
        # ── Phase C pointing/estimator commands (funcs 85-91, WP-5/WP-6) ──
        elif func_id == 85:  # AOCS_SET_POINTING_FRAME: frame (1 byte)
            frame = int(data[0]) if data else 1
            self._cmd(aocs, {"command": "set_pointing_frame", "frame": frame})
        elif func_id == 86:  # AOCS_SET_TARGET_QUAT: qx,qy,qz,qw (4x f32)
            if len(data) >= 16:
                q = [struct.unpack('>f', data[i:i + 4])[0]
                     for i in (0, 4, 8, 12)]
                self._cmd(aocs, {"command": "set_target_quat",
                                 "quaternion": q})
            else:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT,
                    "AOCS_SET_TARGET_QUAT needs 16 data bytes (4x float32)")
        elif func_id == 87:  # AOCS_SET_TARGET_GEO: lat,lon,alt_m (3x f32)
            if len(data) >= 12:
                lat, lon, alt = (struct.unpack('>f', data[i:i + 4])[0]
                                 for i in (0, 4, 8))
                self._cmd(aocs, {"command": "set_target_geo",
                                 "lat": lat, "lon": lon, "alt_m": alt})
            else:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT,
                    "AOCS_SET_TARGET_GEO needs 12 data bytes (3x float32)")
        elif func_id == 88:  # AOCS_SET_CONTROL_BW: omega_n, zeta (2x f32)
            if len(data) >= 8:
                omega_n = struct.unpack('>f', data[0:4])[0]
                zeta = struct.unpack('>f', data[4:8])[0]
                self._cmd(aocs, {"command": "set_control_bw",
                                 "omega_n": omega_n, "zeta": zeta})
            else:
                return self.generate_s1_exec_fail(
                    self._request_id, ERR_BAD_ARGUMENT,
                    "AOCS_SET_CONTROL_BW needs 8 data bytes (2x float32)")
        elif func_id == 89:  # AOCS_FINE_READY_CHECK -> TM(8,130) bitmask
            result = self._cmd(aocs, {"command": "fine_ready_check"})
            mask = int(result.get("fine_ready_mask", 0x7F))
            return [self._engine.tm_builder._pack_tm(
                service=8, subtype=130, data=struct.pack('>B', mask & 0xFF))]
        elif func_id == 90:  # AOCS_SET_MOM_MGMT: enable, entry%, exit% (3x u8)
            enable = bool(data[0]) if data else False
            entry = int(data[1]) if len(data) > 1 else 70
            exit_ = int(data[2]) if len(data) > 2 else 20
            self._cmd(aocs, {"command": "set_mom_mgmt", "enable": enable,
                             "entry_pct": entry, "exit_pct": exit_})
        elif func_id == 91:  # AOCS_EST_RESET: mode (1 byte; 0=cold, 1=warm)
            reset_mode = int(data[0]) if data else 0
            self._cmd(aocs, {"command": "est_reset", "mode": reset_mode})
        return []

    def _route_eps_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        eps = self._engine.subsystems.get("eps")
        if not eps:
            return []
        if func_id == 16:  # payload mode
            mode = data[0] if data else 0
            self._cmd(eps, {"command": "set_payload_mode", "mode": mode})
            # Billing coupling (brief 05 WP-5): the EPS-side mode command must
            # drive the ACTUAL payload, otherwise EPS bills a mode the payload
            # is not in. The payload model itself gates on its power line.
            self._cmd(self._engine.subsystems.get("payload"),
                      {"command": "set_mode", "mode": mode})
            self._cmd(self._engine.subsystems.get("tcs"),
                      {"command": "fpa_cooler", "on": mode >= 1})
        elif func_id == 17:  # fpa cooler
            on = bool(data[0]) if data else False
            self._cmd(eps, {"command": "set_fpa_cooler", "on": on})
            # One cryocooler, one commanded state (brief 05 WP-2): the EPS
            # cooler command also drives the unified TCS cooler flag, so
            # 0x040C / billing / FPA physics all agree on any command path.
            self._cmd(self._engine.subsystems.get("tcs"),
                      {"command": "fpa_cooler", "on": on})
        elif func_id == 18:  # transponder tx
            on = bool(data[0]) if data else True
            self._cmd(eps, {"command": "set_transponder_tx", "on": on})
        elif func_id == 19:  # power line on
            if data:
                line_idx = data[0]
                result = self._cmd(eps, {
                    "command": "power_line_on", "line_index": line_idx
                })
        elif func_id == 20:  # power line off
            if data:
                line_idx = data[0]
                result = self._cmd(eps, {
                    "command": "power_line_off", "line_index": line_idx
                })
        elif func_id == 21:  # reset OC flag
            if data:
                line_idx = data[0]
                self._cmd(eps, {
                    "command": "reset_oc_flag", "line_index": line_idx
                })
        elif func_id == 22:  # switch load
            if len(data) >= 2:
                load_id = data[0]
                state = bool(data[1])
                self._cmd(eps, {
                    "command": "switch_load", "load_id": load_id, "state": state
                })
        elif func_id == 23:  # set charge rate
            if len(data) >= 4:
                rate_a = struct.unpack('>f', data[:4])[0]
                self._cmd(eps, {
                    "command": "set_charge_rate", "rate_a": rate_a
                })
        elif func_id == 24:  # solar array drive
            if len(data) >= 2:
                angle_deg = struct.unpack('>h', data[:2])[0]
                self._cmd(eps, {
                    "command": "set_solar_array_drive", "angle_deg": angle_deg / 100.0
                })
        elif func_id == 25:  # emergency load shed
            if data:
                stage = data[0]
                self._cmd(eps, {
                    "command": "emergency_load_shed", "stage": stage
                })
        elif func_id == 81:  # deploy solar wing
            # data[0]: 0=+Y, 1=-Y, 2=both
            wing_map = {0: "py", 1: "my", 2: "both"}
            wing = wing_map.get(data[0], "both") if data else "both"
            self._cmd(eps, {"command": "deploy_wing", "wing": wing})
        elif func_id == 82:  # get wing status
            self._cmd(eps, {"command": "get_wing_status"})
        elif func_id == 83:  # set EPS mode (defect #27): 0=nominal,1=safe,2=emergency
            mode = int(data[0]) if data else 1
            self._cmd(eps, {"command": "set_eps_mode", "mode": mode})
        return []

    def _make_error_response(self, result: dict) -> list[bytes]:
        """Store error info for the engine to use in S1.8 generation.

        ``_err_reason`` is the human-readable reason the model produced. The
        engine now downlinks it as the S1.8 failure notice (defect #87); it
        used to be stored here and then dropped on the floor.
        """
        self._err_reason = result.get("message", "Unknown error")
        self._err_code = result.get("error_code", 0x0001)
        return []

    def _cmd(self, subsystem, cmd: dict) -> dict:
        """Run a subsystem command and CAPTURE any failure reason it returns.

        Every model's ``handle_command`` answers ``{"success": bool,
        "message": str, "error_code": int}``. Before defect #87 the routers
        called ``handle_command`` directly and threw the result away at all but
        six of ~90 call sites, so a refusal produced a silent S1.7 "execution
        complete" — or, where the code did check, an S1.8 with a bare number.

        Routing every command through this one helper means a handler that
        returns a reason gets it reported to the ground for free, with no
        per-call-site change. Returns the result dict ({} when the model
        returned nothing) so callers that need the payload still work.
        """
        if subsystem is None:
            return {}
        result = subsystem.handle_command(cmd)
        if isinstance(result, dict) and not result.get("success", True):
            self._make_error_response(result)
        return result if isinstance(result, dict) else {}

    def _route_payload_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        payload = self._engine.subsystems.get("payload")
        if not payload:
            return []
        if func_id == 26:  # set mode
            mode = data[0] if data else 0
            result = self._cmd(payload, {"command": "set_mode", "mode": mode})
            # STANDBY/IMAGING imply the FPA cooler; OFF releases it. Route the
            # request to the unified TCS cooler flag (brief 05 WP-2) so the
            # COM-010 path (payload commands only) actually runs the cooler
            # that EPS bills and 0x040C displays.
            if result.get("success", False):
                self._cmd(self._engine.subsystems.get("tcs"),
                          {"command": "fpa_cooler", "on": mode >= 1})
        elif func_id == 27:  # set scene
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            self._cmd(payload, {"command": "set_scene", "scene_id": scene_id})
        elif func_id == 28:  # capture image (Phase E WP-1)
            # Catalogue/textbook form WINS (DECISIONS.md section 9):
            # scene_id uint16 (0 = auto-assign) + lines uint16. Geolocation
            # is NOT an argument — the model stamps lat/lon from orbit_state
            # at shutter open, like a real EO payload. The old handler read
            # 2x float32 lat/lon, so every catalogue-built 4-byte TC failed
            # the length guard and captured at (0, 0) "successfully".
            scene_id = struct.unpack('>H', data[0:2])[0] if len(data) >= 2 else 0
            lines = struct.unpack('>H', data[2:4])[0] if len(data) >= 4 else 0
            self._cmd(payload, {
                "command": "capture", "scene_id": scene_id, "lines": lines
            })
        elif func_id == 29:  # download image
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            self._cmd(payload, {
                "command": "download_image", "scene_id": scene_id
            })
        elif func_id == 30:  # delete image
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            self._cmd(payload, {
                "command": "delete_image", "scene_id": scene_id
            })
        elif func_id == 31:  # mark bad segment
            seg = data[0] if data else 0
            self._cmd(payload, {
                "command": "mark_bad_segment", "segment": seg
            })
        elif func_id == 32:  # get image catalog
            self._cmd(payload, {"command": "get_image_catalog"})
        elif func_id == 33:  # set band configuration
            mask = data[0] if data else 0x0F
            result = self._cmd(payload, {
                "command": "set_band_config", "mask": mask
            })
        elif func_id == 34:  # set integration time (Phase E: 4x uint16, 0.01 ms/LSB)
            # Fixed point replaces 16 octets of float32 with 8: CCD
            # integration times are register counts, not real numbers
            # (brief 04 "no float32 on an uplink argument").
            if len(data) >= 8:
                times = [
                    struct.unpack('>H', data[0:2])[0] * 0.01,
                    struct.unpack('>H', data[2:4])[0] * 0.01,
                    struct.unpack('>H', data[4:6])[0] * 0.01,
                    struct.unpack('>H', data[6:8])[0] * 0.01,
                ]
                self._cmd(payload, {
                    "command": "set_integration_time", "times": times
                })
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "PAYLOAD_SET_INTEGRATION_TIME needs 4x uint16 "
                               "(0.01 ms/LSB, blue/green/red/nir)"})
        elif func_id == 35:  # set detector gain (Phase E: uint16, 0.001x/LSB)
            if len(data) >= 2:
                gain = struct.unpack('>H', data[0:2])[0] * 0.001
                self._cmd(payload, {
                    "command": "set_detector_gain", "gain": gain
                })
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "PAYLOAD_SET_GAIN needs uint16 milli-gain "
                               "(100-10000 = 0.100-10.000)"})
        elif func_id == 36:  # set cooler setpoint (Phase E: uint16 centi-kelvin)
            # raw = round((T_degC + 273.15) * 100): -15.00 degC = 25815 =
            # 0x64D7 on the wire. Unsigned absolute temperature — a negative
            # engineering value never needs a two's-complement raw (the
            # report-(d) fix; see brief 04 FPA worked example).
            if len(data) >= 2:
                raw_ck = struct.unpack('>H', data[0:2])[0]
                setpoint = raw_ck * 0.01 - 273.15
                self._cmd(payload, {
                    "command": "set_cooler_setpoint", "setpoint": setpoint
                })
                # End-to-end (brief 05 deferred item): the payload setpoint
                # drives the TCS-owned FPA node, so the single-node thermal
                # model regulates to the COMMANDED setpoint on this path too.
                if not self._err_reason:
                    self._cmd(self._engine.subsystems.get("tcs"), {
                        "command": "set_fpa_setpoint", "setpoint_c": setpoint
                    })
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "PAYLOAD_COOLER_SETPOINT needs uint16 "
                               "centi-kelvin (25315-27315 = -20..0 degC)"})
        elif func_id == 37:  # start calibration (mode: 0=dark, 1=flat, 2=both)
            mode = data[0] if data else 2
            self._cmd(payload, {"command": "start_calibration", "mode": mode})
        elif func_id == 38:  # stop calibration
            self._cmd(payload, {"command": "stop_calibration"})
        elif func_id == 39:  # set compression (Phase E: algorithm u8 + ratio_deci u8)
            # The model's `algorithm` key was previously unreachable from the
            # wire; ratio is 0.1x/LSB, 0 = AUTO (scene-dependent 1.5-4.0).
            if len(data) >= 2:
                algorithm = data[0]
                ratio = data[1] * 0.1
                self._cmd(payload, {
                    "command": "set_compression",
                    "algorithm": algorithm, "ratio": ratio
                })
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "PAYLOAD_SET_COMPRESSION needs algorithm(u8) "
                               "+ ratio_deci(u8, 0=auto)"})
        # ── Payload extras (Phase E WP-7, funcs 92-95) ──
        elif func_id == 92:  # select filter wheel position
            position = data[0] if data else 0
            self._cmd(payload, {"command": "select_filter",
                                "position": position})
        elif func_id == 93:  # cycle shutter (COM-011 dark-frame prep)
            cycles = data[0] if data else 1
            self._cmd(payload, {"command": "cycle_shutter", "cycles": cycles})
        elif func_id == 94:  # initiate on-board transfer of a stored scene
            scene_id = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            self._cmd(payload, {"command": "initiate_transfer",
                                "scene_id": scene_id})
        elif func_id == 95:  # calibration lamp on/off
            on = bool(data[0]) if data else False
            self._cmd(payload, {"command": "set_cal_lamp", "on": on})
        return []

    def _route_tcs_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        tcs = self._engine.subsystems.get("tcs")
        if not tcs:
            return []
        circuits = {40: "battery", 41: "obc", 42: "thruster"}
        if func_id in circuits:
            on = bool(data[0]) if data else True
            self._cmd(tcs, {
                "command": "heater", "circuit": circuits[func_id], "on": on
            })
        elif func_id == 43:  # FPA cooler
            on = bool(data[0]) if data else True
            self._cmd(tcs, {"command": "fpa_cooler", "on": on})
        elif func_id == 44:  # Set heater setpoint
            if len(data) >= 9:
                circuit = data[0]
                on_temp = struct.unpack('>f', data[1:5])[0]
                off_temp = struct.unpack('>f', data[5:9])[0]
                self._cmd(tcs, {
                    "command": "set_setpoint",
                    "circuit": circuit,
                    "on_temp": on_temp,
                    "off_temp": off_temp,
                })
        elif func_id == 45:  # Auto mode
            circuit = data[0] if data else 0
            self._cmd(tcs, {
                "command": "auto_mode", "circuit": circuit
            })
        elif func_id == 46:  # Set heater duty limit (Phase 5)
            if len(data) >= 2:
                circuit = data[0]
                duty_pct = data[1]
                self._cmd(tcs, {
                    "command": "set_heater_duty_limit",
                    "circuit": circuit,
                    "duty_limit_pct": float(duty_pct),
                })
        elif func_id == 47:  # Decontamination start (Phase 5)
            # Defect #31: default to a positive bake-out target (the handler also
            # defaults to +50C). The previous -50C default would have *cooled*
            # the FPA, the opposite of a decontamination bake-out.
            target_temp = 50.0
            if len(data) >= 4:
                target_temp = struct.unpack('>f', data[:4])[0]
            self._cmd(tcs, {
                "command": "decontamination_start",
                "target_temp_c": target_temp,
            })
        elif func_id == 48:  # Decontamination stop (Phase 5)
            self._cmd(tcs, {"command": "decontamination_stop"})
        elif func_id == 49:  # Get thermal map (Phase 5)
            result = self._cmd(tcs, {"command": "get_thermal_map"})
            # Defect C: pack the thermal map into an S8.2 TM reply (mirroring
            # the query funcs 61/62) instead of silently dropping it.
            if result.get("success"):
                tm = result.get("thermal_map", {})
                # Fixed field order so the ground segment can decode positionally.
                keys = [
                    "temp_panel_px", "temp_panel_mx", "temp_panel_py",
                    "temp_panel_my", "temp_panel_pz", "temp_panel_mz",
                    "temp_obc", "temp_battery", "temp_fpa", "temp_thruster",
                ]
                values = [float(tm.get(k, 0.0)) for k in keys]
                resp_data = struct.pack('>' + 'f' * len(values), *values)
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=resp_data
                )]
        return []

    def _route_obdh_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        obdh = self._engine.subsystems.get("obdh")
        if not obdh:
            return []
        if func_id == 50:  # set mode
            mode = data[0] if data else 0
            self._cmd(obdh, {"command": "set_mode", "mode": mode})
        elif func_id == 51:  # memory scrub
            self._cmd(obdh, {"command": "memory_scrub"})
        elif func_id == 52:  # OBC reboot
            self._cmd(obdh, {"command": "obc_reboot"})
        elif func_id == 53:  # OBC switch unit
            self._cmd(obdh, {"command": "obc_switch_unit"})
        elif func_id == 54:  # OBC select bus
            bus = data[0] if data else 0
            self._cmd(obdh, {"command": "obc_select_bus", "bus": bus})
        elif func_id == 55:  # OBC boot app
            self._cmd(obdh, {"command": "obc_boot_app"})
        elif func_id == 56:  # OBC boot inhibit
            inhibit = bool(data[0]) if data else True
            self._cmd(obdh, {
                "command": "obc_boot_inhibit", "inhibit": inhibit
            })
        elif func_id == 57:  # OBC clear reboot count
            self._cmd(obdh, {"command": "obc_clear_reboot_cnt"})
        elif func_id == 58:  # OBC set watchdog period
            if len(data) >= 2:
                period = struct.unpack('>H', data[:2])[0]
                self._cmd(obdh, {"command": "set_watchdog_period", "period": period})
        elif func_id == 59:  # OBC watchdog enable
            self._cmd(obdh, {"command": "watchdog_enable"})
        elif func_id == 60:  # OBC watchdog disable
            self._cmd(obdh, {"command": "watchdog_disable"})
        elif func_id == 61:  # OBC diagnostic
            result = self._cmd(obdh, {"command": "diagnostic"})
            if result.get("success"):
                health_data = result.get("data", b"")
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=health_data
                )]
        elif func_id == 62:  # OBC error log
            result = self._cmd(obdh, {"command": "error_log"})
            if result.get("success"):
                log_data = result.get("data", b"")
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=log_data
                )]
        elif func_id == 80:  # GPS time sync
            result = self._cmd(obdh, {"command": "gps_time_sync",
                                          "shared_params": self._engine.params,
                                          "sim_time": self._engine._sim_time})
            if result.get("success"):
                # Check if time jump occurred — may disrupt AOCS
                time_jump_s = result.get("time_jump_s", 0.0)
                if abs(time_jump_s) > 5.0:
                    aocs = self._engine.subsystems.get("aocs")
                    if aocs and hasattr(aocs, '_state'):
                        # Force AOCS to SAFE_BOOT on large time jump
                        aocs._state.mode = 1  # MODE_SAFE_BOOT
                        aocs._state.submode = 0
                        aocs._state.time_in_mode = 0.0
                        self._engine._emit_event({
                            'event_id': 0x020F,
                            'severity': 'HIGH',
                            'description': f"AOCS forced to SAFE_BOOT: GPS time sync jump {time_jump_s:.1f}s"
                        })
        return []

    def _route_ttc_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        ttc = self._engine.subsystems.get("ttc")
        if not ttc:
            return []
        if func_id == 63:  # switch primary
            self._cmd(ttc, {"command": "switch_primary"})
        elif func_id == 64:  # switch redundant
            self._cmd(ttc, {"command": "switch_redundant"})
        elif func_id == 65:  # set tm rate (ladder tier)
            # Catalogued form (tc_catalog TTC_SET_DATA_RATE): a single uint8
            # `rate` field carrying a TIER CODE over ttc.yaml's tm_rate_tiers
            # ladder — 0=LOW(16 kbps, routine ops), 1=HIGH(128 kbps, dumps),
            # 0xFF=BEACON(1 kbps, bootloader only). The catalogue declares one
            # uint8 and this handler reads exactly one byte, so the byte count
            # the MCS derives (1 func_id + 1 field = 2) matches the wire
            # (defect #80 was precisely this class of mismatch).
            #
            # 0/1 keep their old meanings verbatim ("the low rate" / "the high
            # rate"), so existing procedures, OBCPs and the FDIR
            # ttc_reduce_power action keep working — they now land on usable
            # operational rates instead of 1 kbps and 64 kbps (defect #85).
            #
            # The 4-byte big-endian raw-bps form is still accepted for older
            # clients; a raw value matching a tier's bps selects that tier.
            if len(data) >= 4:
                rate = struct.unpack('>I', data[:4])[0]
            elif len(data) >= 1:
                rate = data[0]
            else:
                # Bare TC with no argument = the default operational tier
                # (LOW), not the top of the ladder.
                rate = getattr(ttc, "_default_tier", 1)
                rate = ttc.tier(rate).get("command_code", 1) \
                    if hasattr(ttc, "tier") else 1
            self._cmd(ttc, {"command": "set_tm_rate", "rate": rate})
        elif func_id == 66:  # PA on
            self._cmd(ttc, {"command": "pa_on"})
        elif func_id == 67:  # PA off
            self._cmd(ttc, {"command": "pa_off"})
        elif func_id == 68:  # Set TX power (Phase E WP-2: enumerated level)
            # Catalogue/docs/FDIR form WINS: one uint8 level over the
            # corrected watt table 0=LOW 1.0 W, 1=NOMINAL 2.0 W, 2=HIGH
            # 5.0 W (pa_max_power_w; the documented '2 = 8 W' was never
            # executable). The old handler read float32 watts and silently
            # substituted 2.0 W for every 1-byte FDIR/procedure caller —
            # "reduce to 50 %", "minimum power" and "reduced level" all
            # executed as the same 2.0 W while S1.7 reported success.
            # Unknown codes are REJECTED (S1.8 error 0x0003), never clamped.
            if len(data) >= 1:
                self._cmd(ttc, {"command": "set_tx_power",
                                "level": int(data[0])})
            else:
                self._make_error_response({
                    "error_code": 0x0003,
                    "message": "TTC_SET_TX_POWER needs a 1-byte level "
                               "(0=LOW 1 W, 1=NOMINAL 2 W, 2=HIGH 5 W)"})
        elif func_id == 69:  # Deploy antennas
            self._cmd(ttc, {"command": "deploy_antennas"})
        elif func_id == 70:  # Set beacon mode
            self._cmd(ttc, {
                "command": "set_beacon_mode",
                "on": bool(data[0]) if data else True,
            })
        elif func_id == 71:  # Start command channel
            self._cmd(ttc, {"command": "cmd_channel_start"})
        elif func_id == 72:  # Set uplink frequency
            if len(data) >= 4:
                freq_mhz = struct.unpack('>f', data[:4])[0]
                self._cmd(ttc, {"command": "set_ul_freq", "freq_mhz": freq_mhz})
        elif func_id == 73:  # Set downlink frequency
            if len(data) >= 4:
                freq_mhz = struct.unpack('>f', data[:4])[0]
                self._cmd(ttc, {"command": "set_dl_freq", "freq_mhz": freq_mhz})
        elif func_id == 74:  # Set modulation mode
            mode = data[0] if data else 0
            self._cmd(ttc, {"command": "set_modulation", "mode": mode})
        elif func_id == 75:  # Set RX gain (AGC target)
            if len(data) >= 4:
                agc_db = struct.unpack('>f', data[:4])[0]
                self._cmd(ttc, {"command": "set_rx_gain", "agc_db": agc_db})
        elif func_id == 76:  # Start ranging
            self._cmd(ttc, {"command": "ranging_start"})
        elif func_id == 77:  # Stop ranging
            self._cmd(ttc, {"command": "ranging_stop"})
        elif func_id == 78:  # Set coherent transponder mode
            on = bool(data[0]) if data else True
            self._cmd(ttc, {"command": "set_coherent_mode", "on": on})
        elif func_id == 79:  # Set DOWNLINK / payload-dump mode
            # Gate for the HIGH tier of the TM rate ladder (defect #85): bulk
            # downlink has to be entered deliberately, so an S13 image dump or
            # an S15 playback is a planned activity rather than something the
            # spacecraft drifts into at 1 Mbps.
            on = bool(data[0]) if data else True
            self._cmd(ttc, {"command": "set_downlink_mode", "on": on})
        return []

    # ─── S9 Time Management ─────────────────────────────────────────

    def _handle_s9(self, subtype: int, data: bytes) -> list[bytes]:
        """S9 Time Management."""
        if subtype == 1 and len(data) >= 4:
            cuc = struct.unpack('>I', data[:4])[0]
            obdh = self._engine.subsystems.get("obdh")
            if obdh:
                self._cmd(obdh, {"command": "set_time", "cuc": cuc})
        elif subtype == 2:
            # Request time report
            cuc = self._engine._get_cuc_time()
            pkt = self._engine.tm_builder.build_time_report(cuc)
            return [pkt]
        return []

    # ─── S11 Time-tagged Scheduling ──────────────────────────────────

    def _handle_s11(self, subtype: int, data: bytes) -> list[bytes]:
        """S11 Time-tagged Command Scheduling."""
        scheduler = getattr(self._engine, '_tc_scheduler', None)
        if scheduler is None:
            return []
        if subtype == 4 and len(data) >= 4:
            exec_time = struct.unpack('>I', data[:4])[0]
            tc_pkt = data[4:]
            cmd_id = scheduler.insert(exec_time, tc_pkt)
            resp_data = struct.pack('>H', cmd_id)
            return [self._engine.tm_builder._pack_tm(
                service=11, subtype=5, data=resp_data
            )]
        elif subtype == 7 and len(data) >= 2:
            cmd_id = struct.unpack('>H', data[:2])[0]
            scheduler.delete(cmd_id)
        elif subtype == 9:
            scheduler.disable_schedule()
        elif subtype == 11:
            scheduler.delete_all()
        elif subtype == 13:
            scheduler.enable_schedule()
        elif subtype == 17:
            # Detailed on-board schedule report → TM(11,18).
            #
            # Header:  count(u16) + schedule_enabled(u8) + next_id(u16)
            # Record:  id(u16) + exec_time_cuc(u32) + apid(u16) + service(u8)
            #          + subtype(u8) + data_len(u8) + n_bytes(u8) + data[n_bytes]
            #
            # The old report carried only (id, exec_time) — the scheduler
            # deliberately dropped the packet — so the operator could not tell
            # which command was queued (defect #72). The embedded packet stays
            # on board; only its identity is reported.
            cmds = scheduler.list_commands()
            status = scheduler.get_status()
            resp_data = struct.pack('>HBH', len(cmds),
                                    1 if status.get('enabled', True) else 0,
                                    int(status.get('next_id', 0)) & 0xFFFF)
            for c in cmds:
                detail = bytes(c.get('data', b''))[:255]
                resp_data += struct.pack(
                    '>HIHBBBB',
                    c['id'] & 0xFFFF, c['exec_time'] & 0xFFFFFFFF,
                    int(c.get('apid', 0)) & 0xFFFF,
                    int(c.get('service', 0)) & 0xFF,
                    int(c.get('subtype', 0)) & 0xFF,
                    min(int(c.get('data_len', 0)), 255),
                    len(detail),
                ) + detail
            return [self._engine.tm_builder._pack_tm(
                service=11, subtype=18, data=resp_data
            )]
        return []

    # ─── S12 On-Board Monitoring ─────────────────────────────────────

    # S12 check types (defn['check_type']).
    S12_CHECK_ABSOLUTE = 0  # value itself must stay inside [low, high]
    S12_CHECK_DELTA = 1     # change since the previous check must stay inside

    def _handle_s12(self, subtype: int, data: bytes) -> list[bytes]:
        """S12 On-Board Monitoring — parameter limit checking.

        Subtypes:
          1  enable monitoring (global)
          2  disable monitoring (global)
          3  enable ONE definition   — mon_id(2)
          4  disable ONE definition  — mon_id(2)
          6  add definition          — see wire formats below
          7  delete definition       — mon_id(2)
          12 report all definitions  → TM(12,13)

        TC(12,6) accepts two wire formats, distinguished by length:
          * 13 bytes (preferred): mon_id(u16) + param_id(u16) + check_type(u8)
            + low_limit(f32) + high_limit(f32)
          * 11 bytes (legacy MCS form): param_id(u16) + check_type(u8)
            + low_limit(f32) + high_limit(f32); mon_id is taken to be param_id
        """
        if subtype == 1:
            # Enable monitoring
            self._s12_enabled = True
        elif subtype == 2:
            # Disable monitoring
            self._s12_enabled = False
        elif subtype in (3, 4) and len(data) >= 2:
            # Per-definition enable (3) / disable (4)
            mon_id = struct.unpack('>H', data[:2])[0]
            defn = self._s12_definitions.get(mon_id)
            if defn is None:
                return self.generate_s1_exec_fail(
                    self._request_id, 0x0007,
                    f"S12 monitoring definition 0x{mon_id:04X} is not defined")
            defn['enabled'] = (subtype == 3)
            if subtype == 4:
                # A disabled monitor must not carry a stale delta baseline.
                defn['last_value'] = None
            logger.info("S12 definition 0x%04X %s", mon_id,
                        "ENABLED" if subtype == 3 else "DISABLED")
        elif subtype == 6 and len(data) >= 11:
            # Add monitoring definition
            if len(data) >= 13:
                mon_id, param_id = struct.unpack('>HH', data[:4])
                check_type = data[4]
                low, high = struct.unpack('>ff', data[5:13])
            else:
                # Legacy 11-byte form: one monitor per parameter.
                param_id = struct.unpack('>H', data[:2])[0]
                mon_id = param_id
                check_type = data[2]
                low, high = struct.unpack('>ff', data[3:11])
            self._s12_definitions[mon_id] = {
                'mon_id': mon_id,
                'param_id': param_id,
                'check_type': check_type,
                'low_limit': low,
                'high_limit': high,
                'enabled': True,
                'last_value': None,
                'severity': 'WARNING',
                'name': f"ground_mon_0x{mon_id:04X}",
                'description': 'Added from ground via TC(12,6)',
            }
        elif subtype == 7 and len(data) >= 2:
            # Delete monitoring definition
            mon_id = struct.unpack('>H', data[:2])[0]
            self._s12_definitions.pop(mon_id, None)
        elif subtype == 12:
            # Report all monitoring definitions → TM(12,13).
            # Record: mon_id(u16) + param_id(u16) + check_type(u8)
            #         + enabled(u8) + low(f32) + high(f32)   [14 bytes]
            resp_data = struct.pack('>H', len(self._s12_definitions))
            for mon_id, defn in self._s12_definitions.items():
                resp_data += struct.pack(
                    '>HHBBff',
                    mon_id & 0xFFFF, defn['param_id'] & 0xFFFF,
                    int(defn.get('check_type', 0)) & 0xFF,
                    1 if defn.get('enabled', True) else 0,
                    defn['low_limit'], defn['high_limit'])
            return [self._engine.tm_builder._pack_tm(
                service=12, subtype=13, data=resp_data
            )]
        return []

    def build_s12_transition_report(self, violation: dict, entered: bool) -> bytes:
        """Build TM(12,9) (limit entered) / TM(12,10) (returned to nominal).

        Wire format (big-endian), a strict superset of what the MCS already
        parses (``param_id`` + ``value``):

            param_id(u16) value(f32) mon_id(u16) check_type(u8)
            severity(u8) low_limit(f32) high_limit(f32)          [18 bytes]

        12,9 / 12,10 are the subtypes the ground already decodes
        (``tm_processor._process_monitoring``); emitting them turns that dead
        branch live and stops S12 alarms being filed under "unknown" because
        they arrived only as synthetic 0x9xxx S5 events (defect #69).
        """
        return self._engine.tm_builder._pack_tm(
            service=12, subtype=9 if entered else 10,
            data=struct.pack(
                '>HfHBBff',
                int(violation['param_id']) & 0xFFFF,
                float(violation.get('value') or 0.0),
                int(violation.get('mon_id', violation['param_id'])) & 0xFFFF,
                int(violation.get('check_type', 0)) & 0xFF,
                int(violation.get('severity', 2)) & 0xFF,
                float(violation.get('low_limit', 0.0)),
                float(violation.get('high_limit', 0.0)),
            ),
        )

    def _param_source_powered(self, param_id: int) -> bool:
        """Return True if the subsystem owning `param_id` is powered AND in
        an operational mode. Used by S12 monitoring to suppress spurious
        alarms for stale/zero/default values from cold equipment.

        Param ID ranges (EOSAT-1 convention):
          0x0100..0x012F  EPS              — always operational
          0x0200..0x02FF  AOCS             — gated on aocs_wheels line + mode
          0x0300..0x033F  OBDH             — gated on sw_image == APPLICATION
          0x0400..0x04FF  TCS              — always (OBC-resident)
          0x0500..0x05FF  TTC              — gated on ttc_tx for TX params
          0x0600..0x06FF  Payload          — gated on payload line + mode
          0x0700..0x07FF  Platform/misc    — always
        """
        eng = self._engine
        eps = eng.subsystems.get("eps")
        lines = getattr(getattr(eps, "_state", None), "power_lines", {}) or {}

        if 0x0100 <= param_id <= 0x012F:
            return True
        if 0x0200 <= param_id <= 0x02FF:
            if not lines.get("aocs_wheels", False):
                return False
            aocs = eng.subsystems.get("aocs")
            mode = getattr(getattr(aocs, "_state", None), "mode", 0)
            return int(mode) > 0
        if 0x0300 <= param_id <= 0x033F:
            obdh = eng.subsystems.get("obdh")
            sw = getattr(getattr(obdh, "_state", None), "sw_image", 1)
            return int(sw) == 1  # SW_APPLICATION
        if 0x0400 <= param_id <= 0x04FF:
            return True
        if 0x0500 <= param_id <= 0x05FF:
            # TTC RX is always on; TX-related signal/quality params need TX line.
            # Conservative: gate the whole 0x05xx range on ttc_tx because the
            # downlink monitoring rules (RSSI, Eb/N0, link margin, BER) only
            # have meaning when the transmitter is energised.
            return bool(lines.get("ttc_tx", False))
        if 0x0600 <= param_id <= 0x06FF:
            if not lines.get("payload", False):
                return False
            payload = eng.subsystems.get("payload")
            mode = getattr(getattr(payload, "_state", None), "mode", 0)
            return int(mode) > 0
        return True

    def check_monitoring(self) -> list[dict]:
        """Check all monitoring definitions against current parameter values.

        Returns list of transition reports (param violations).
        Called by the engine each tick.
        Each violation includes param_id, value, limits, and event details for S5 generation.
        """
        with self._lock:
            if not self._s12_enabled:
                return []

            violations = []
            for mon_id, defn in self._s12_definitions.items():
                if not defn.get('enabled', True):
                    continue
                param_id = defn['param_id']
                value = self._engine.params.get(param_id)
                if value is None:
                    continue
                # Suppress monitoring for params whose source is cold/dark.
                if not self._param_source_powered(param_id):
                    # Drop the delta baseline too: the next powered sample must
                    # not be differenced against a value from before power-off.
                    defn['last_value'] = None
                    continue

                low = defn['low_limit']
                high = defn['high_limit']
                severity = defn.get('severity', 'WARNING')
                name = defn.get('name', f"param_0x{param_id:04X}")
                description = defn.get('description', '')
                check_type = int(defn.get('check_type', self.S12_CHECK_ABSOLUTE))
                last_value = defn.get('last_value')
                defn['last_value'] = value

                if check_type == self.S12_CHECK_DELTA:
                    # Delta check: the *change* since the previous evaluation
                    # must stay inside [low, high]. The first sample after a
                    # (re)start has no baseline, so it can never violate.
                    if last_value is None:
                        continue
                    checked = value - last_value
                else:
                    checked = value

                if checked < low or checked > high:
                    # Map severity string to numeric level for events
                    severity_map = {'INFO': 1, 'WARNING': 2, 'ALARM': 3, 'HIGH': 4}
                    severity_level = severity_map.get(severity, 2)

                    violations.append({
                        'param_id': param_id,
                        'mon_id': mon_id,
                        'value': checked,
                        'raw_value': value,
                        'low_limit': low,
                        'high_limit': high,
                        'check_type': check_type,
                        'type': 'delta_out_of_limits' if check_type ==
                                self.S12_CHECK_DELTA else 'out_of_limits',
                        'severity': severity_level,
                        'name': name,
                        'description': description,
                    })

            return violations

    # ─── S13 Large Data Transfer ─────────────────────────────────────

    def _default_image_size_mb(self) -> int:
        """Configured payload image size, in whole MB (S13 wire field is u16).

        Read from the live payload model so S13 can never drift from
        ``subsystems/payload.yaml image_size_mb`` (defect #82). Falls back to
        ``DEFAULT_IMAGE_SIZE_MB`` when no real payload model is attached.
        """
        try:
            payload = self._engine.subsystems.get("payload")
            size = getattr(getattr(payload, "_state", None), "image_size_mb", None)
            # isinstance, not float(): a MagicMock payload (unit tests) has a
            # __float__ that answers 1.0, which would silently win here.
            if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
                return max(1, int(round(size)))
        except Exception:
            pass
        return DEFAULT_IMAGE_SIZE_MB

    def _handle_s13(self, subtype: int, data: bytes) -> list[bytes]:
        """S13 Large Data Transfer — create/manage/complete transfer sessions."""
        if subtype == 1 and len(data) >= 4:
            # S13.1: Create download transfer
            # Format: scene_id(2) + initial_size_mb(2) OR variant: scene_id(2) + reserved(2)
            scene_id = struct.unpack('>H', data[:2])[0]
            size_mb = struct.unpack('>H', data[2:4])[0] if len(data) >= 4 else 0
            if size_mb == 0:
                size_mb = self._default_image_size_mb()

            transfer_id = getattr(self, '_s13_transfer_counter', 1)
            self._s13_transfer_counter = transfer_id + 1

            # Initialize transfer dict if not present
            if not hasattr(self, '_s13_transfers'):
                self._s13_transfers = {}

            # Calculate total blocks (1024 bytes per block)
            total_blocks = (size_mb * 1024 * 1024 + 1023) // 1024

            self._s13_transfers[transfer_id] = {
                'transfer_id': transfer_id,
                'scene_id': scene_id,
                'size_mb': size_mb,
                'total_blocks': total_blocks,
                'blocks_sent': 0,
                'status': 'ACTIVE',  # ACTIVE, PAUSED, COMPLETE
            }

            logger.info("S13 create transfer %d for scene %d (%d MB, %d blocks)",
                       transfer_id, scene_id, size_mb, total_blocks)

            # Response: transfer_id(2)
            resp_data = struct.pack('>H', transfer_id)
            return [self._engine.tm_builder._pack_tm(
                service=13, subtype=2, data=resp_data
            )]

        elif subtype == 3 and len(data) >= 4:
            # S13.3: Download segment request
            # Format: transfer_id(2) + segment_number(2)
            transfer_id = struct.unpack('>H', data[:2])[0]
            segment_num = struct.unpack('>H', data[2:4])[0]

            if not hasattr(self, '_s13_transfers'):
                self._s13_transfers = {}

            if transfer_id not in self._s13_transfers:
                # Transfer not found
                return [self._engine.tm_builder._pack_tm(
                    service=13, subtype=4, data=struct.pack('>H', transfer_id)
                )]

            xfer = self._s13_transfers[transfer_id]
            if xfer['status'] != 'ACTIVE':
                return []

            # Generate simulated image data block (1024 bytes)
            # In a real system, this would read from payload memory
            block_data = bytes([(segment_num + i) % 256 for i in range(1024)])

            # Response: transfer_id(2) + segment_num(2) + block_data(1024)
            resp_data = struct.pack('>HH', transfer_id, segment_num) + block_data

            xfer['blocks_sent'] = segment_num + 1

            return [self._engine.tm_builder._pack_tm(
                service=13, subtype=4, data=resp_data
            )]

        elif subtype == 5 and len(data) >= 2:
            # S13.5: End transfer
            # Format: transfer_id(2)
            transfer_id = struct.unpack('>H', data[:2])[0]

            if not hasattr(self, '_s13_transfers'):
                self._s13_transfers = {}

            if transfer_id in self._s13_transfers:
                xfer = self._s13_transfers.pop(transfer_id)
                logger.info("S13 transfer %d complete (scene %d)",
                           transfer_id, xfer['scene_id'])

            return []

        elif subtype == 9 and len(data) >= 2:
            # S13.9: Report transfer status
            # Format: transfer_id(2)
            transfer_id = struct.unpack('>H', data[:2])[0]

            if not hasattr(self, '_s13_transfers'):
                self._s13_transfers = {}

            if transfer_id not in self._s13_transfers:
                return []

            xfer = self._s13_transfers[transfer_id]

            # Calculate progress percentage
            progress = 0
            if xfer['total_blocks'] > 0:
                progress = int(100.0 * xfer['blocks_sent'] / xfer['total_blocks'])

            # Response: transfer_id(2) + blocks_sent(4) + total_blocks(4) + status(1)
            status_code = {'ACTIVE': 1, 'PAUSED': 2, 'COMPLETE': 3}.get(xfer['status'], 0)
            resp_data = struct.pack('>HII B',
                transfer_id, xfer['blocks_sent'], xfer['total_blocks'], status_code)

            return [self._engine.tm_builder._pack_tm(
                service=13, subtype=10, data=resp_data
            )]

        return []

    # ─── S15 Onboard TM Storage ──────────────────────────────────────

    def _handle_s15(self, subtype: int, data: bytes) -> list[bytes]:
        """S15 Onboard TM Storage."""
        storage = getattr(self._engine, '_tm_storage', None)
        if storage is None:
            return []
        if subtype == 1 and len(data) >= 1:
            store_id = data[0]
            storage.enable_store(store_id)
        elif subtype == 2 and len(data) >= 1:
            store_id = data[0]
            storage.disable_store(store_id)
        elif subtype == 9 and len(data) >= 1:
            store_id = data[0]
            # Queue a paced dump: packets are released over many ticks at the
            # TTC TM data rate by the engine's _tick_dump_emission. If the
            # downlink is not active when a packet is released it is lost;
            # override-on forces delivery. Store is auto-cleared on completion.
            count = self._engine.queue_dump(store_id)
            if count > 0:
                return self.generate_s1_progress(0, count)
            return []
        elif subtype == 11 and len(data) >= 1:
            store_id = data[0]
            storage.delete_store(store_id)
        elif subtype == 13:
            # Store status report → TM(15,14).
            #
            # Header:  n_stores(u8)
            # Record:  id(u8) + count(u16) + capacity(u16) + enabled(u8)
            #          + fill_pct_x10(u16) + flags(u8) + wrap_count(u16)
            #          + name_len(u8) + name[name_len]   (ASCII)
            #   flags bit0 = overflow, bit1 = circular
            #
            # get_status() computes name / fill_pct / overflow / circular /
            # wrap_count and the old 'BHHB' record threw all five away, so the
            # ground could not display store status properly (defect #73).
            status = storage.get_status()
            resp_data = struct.pack('>B', len(status))
            for s in status:
                name = str(s.get('name', ''))[:32].encode('ascii', 'replace')
                flags = ((1 if s.get('overflow') else 0)
                         | (2 if s.get('circular') else 0))
                resp_data += struct.pack(
                    '>BHHBHBHB',
                    s['id'] & 0xFF, s['count'] & 0xFFFF, s['capacity'] & 0xFFFF,
                    1 if s['enabled'] else 0,
                    min(int(round(float(s.get('fill_pct', 0.0)) * 10)), 0xFFFF),
                    flags,
                    int(s.get('wrap_count', 0)) & 0xFFFF,
                    len(name),
                ) + name
            return [self._engine.tm_builder._pack_tm(
                service=15, subtype=14, data=resp_data
            )]
        return []

    # ─── S17 Connection Test ─────────────────────────────────────────

    def _handle_s17(self, subtype: int, data: bytes) -> list[bytes]:
        """S17 Connection Test."""
        if subtype == 1:
            pkt = self._engine.tm_builder.build_connection_test_report()
            return [pkt]
        return []

    # ─── S18 On-Board Control Procedures (OBCP) ──────────────────────
    # Subtypes (PUS-C ST[18]-flavoured, simplified):
    #   1 = direct-load procedure (proc_id + name + serialized steps)
    #   2 = delete procedure
    #   3 = activate (start) procedure, optional 1-byte argument
    #   4 = stop (abort) procedure (proc_id 0 = stop all)
    #   6 = report procedure status → TM(18,7)
    # The step interpreter lives in ObcpManager (engine._obcp_manager) and is
    # ticked by the engine main loop; see smo_simulator/obcp.py.

    def _handle_s18(self, subtype: int, data: bytes) -> list[bytes]:
        """S18 On-Board Control Procedures — load/delete/start/stop/report."""
        manager = getattr(self._engine, '_obcp_manager', None)
        if manager is None:
            return []
        if subtype == 1 and len(data) >= 4:
            # Direct-load procedure
            try:
                proc_id, name, steps = manager.parse_load_blob(data)
            except ValueError as e:
                return self._make_error_response(
                    {"message": f"OBCP load parse error: {e}",
                     "error_code": 0x0003})
            if not manager.load(proc_id, name, steps):
                return self._make_error_response(
                    {"message": "OBCP load rejected (store full or bad steps)",
                     "error_code": 0x0007})
            logger.info("S18.1 OBCP LOAD: id=%d '%s' (%d steps)",
                        proc_id, name, len(steps))
        elif subtype == 2 and len(data) >= 2:
            proc_id = struct.unpack('>H', data[:2])[0]
            if not manager.delete(proc_id):
                return self._make_error_response(
                    {"message": f"OBCP {proc_id} not loaded",
                     "error_code": 0x0007})
            logger.info("S18.2 OBCP DELETE: id=%d", proc_id)
        elif subtype == 3 and len(data) >= 2:
            proc_id = struct.unpack('>H', data[:2])[0]
            param = data[2] if len(data) >= 3 else 0
            if not manager.start(proc_id, param):
                return self._make_error_response(
                    {"message": f"OBCP {proc_id} start refused "
                                "(not loaded or already running)",
                     "error_code": 0x0007})
            logger.info("S18.3 OBCP START: id=%d param=%d", proc_id, param)
        elif subtype == 4 and len(data) >= 2:
            proc_id = struct.unpack('>H', data[:2])[0]
            if not manager.stop(proc_id):
                return self._make_error_response(
                    {"message": f"OBCP {proc_id} not running",
                     "error_code": 0x0007})
            logger.info("S18.4 OBCP STOP: id=%d", proc_id)
        elif subtype == 6:
            # Report procedure status → TM(18,7):
            #   count(1) + [proc_id(2) + status(1) + current_step(1) + n_steps(1)]*
            status = manager.get_status()
            resp_data = struct.pack('>B', len(status))
            for s in status:
                resp_data += struct.pack(
                    '>HBBB', s['proc_id'], s['status'],
                    s['current_step'], s['n_steps'])
            return [self._engine.tm_builder._pack_tm(
                service=18, subtype=7, data=resp_data
            )]
        return []

    # ─── S19 Event-Action ────────────────────────────────────────────

    def _handle_s19(self, subtype: int, data: bytes) -> list[bytes]:
        """S19 Event-Action — link events to automatic responses.

        TC(19,1) accepts two wire formats, distinguished by length:
          * 6 bytes (preferred):
            ea_id(u16) + trigger_kind(u8) + trigger_id(u16) + action_func_id(u8)
            where trigger_kind 0 = S5 event id, 1 = monitored parameter id.
          * 4 bytes (legacy): ea_id(u16) + event_type(u8) + action_func_id(u8).
            The 1-byte event type cannot express any real EOSAT-1 event id
            (all are >= 0x0100), which is why ground-added rules never fired
            (defect #70). Retained only so old clients still get a definition
            installed; it is always interpreted in the *event* namespace.
        """
        if subtype == 1 and len(data) >= 6:
            # Add event-action definition (disambiguated form)
            ea_id, trigger_kind, trigger_id = struct.unpack('>HBH', data[:5])
            action_func_id = data[5]
            if trigger_kind not in (TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM):
                return self.generate_s1_exec_fail(
                    self._request_id, 0x0003,
                    f"S19 trigger kind {trigger_kind} is not 0 (event) or "
                    f"1 (parameter)")
            self._s19_definitions[ea_id] = {
                'event_type': trigger_id,
                'trigger_kind': trigger_kind,
                'action_func_id': action_func_id,
            }
            self._s19_enabled_ids.add(ea_id)
        elif subtype == 1 and len(data) >= 4:
            # Add event-action definition (legacy 4-byte form)
            ea_id = struct.unpack('>H', data[:2])[0]
            event_type = data[2]
            action_func_id = data[3]
            self._s19_definitions[ea_id] = {
                'event_type': event_type,
                'trigger_kind': TRIGGER_KIND_EVENT,
                'action_func_id': action_func_id,
            }
            self._s19_enabled_ids.add(ea_id)
        elif subtype == 2 and len(data) >= 2:
            # Delete event-action definition
            ea_id = struct.unpack('>H', data[:2])[0]
            self._s19_definitions.pop(ea_id, None)
            self._s19_enabled_ids.discard(ea_id)
        elif subtype == 4 and len(data) >= 2:
            # Enable event-action
            ea_id = struct.unpack('>H', data[:2])[0]
            if ea_id in self._s19_definitions:
                self._s19_enabled_ids.add(ea_id)
        elif subtype == 5 and len(data) >= 2:
            # Disable event-action
            ea_id = struct.unpack('>H', data[:2])[0]
            self._s19_enabled_ids.discard(ea_id)
        elif subtype == 8:
            # Report all event-action definitions → TM(19,128).
            # Record: ea_id(u16) + trigger_kind(u8) + trigger_id(u16)
            #         + action_func_id(u8) + enabled(u8)          [7 bytes]
            # The old record packed trigger_id as a *uint8*, so every shipped
            # rule (all >= 0x0100) raised struct.error and the whole report
            # came back empty (defect #70).
            resp_data = struct.pack('>H', len(self._s19_definitions))
            for ea_id, defn in self._s19_definitions.items():
                enabled = 1 if ea_id in self._s19_enabled_ids else 0
                resp_data += struct.pack(
                    '>HBHBB',
                    ea_id & 0xFFFF,
                    int(defn.get('trigger_kind', TRIGGER_KIND_EVENT)) & 0xFF,
                    int(defn['event_type']) & 0xFFFF,
                    int(defn['action_func_id']) & 0xFF,
                    enabled)
            return [self._engine.tm_builder._pack_tm(
                service=19, subtype=128, data=resp_data
            )]
        return []

    # S8 funcs that boot/reboot/reconfigure the OBC. While the OBC is in
    # bootloader (sw_image == SW_BOOTLOADER) the autonomous S19 path must NOT
    # execute these — the operator-command path is already gated in
    # obdh_basic.handle_command via _BOOTLOADER_ALLOWED, but trigger_event_action
    # bypasses that gate. Defense-in-depth so a future mis-mapped rule cannot
    # silently boot or reboot the OBC out of the bootloader (Bug 3 hardening).
    _OBC_CRITICAL_FUNCS = frozenset({52, 53, 55, 56})  # reboot, switch_unit, boot_app, boot_inhibit

    def _s19_dispatch(self, ea_id: int, func_id: int,
                      action_data: bytes = b"") -> None:
        """Execute an S19 rule's S8 func, blocking OBC-critical funcs in
        bootloader. ``action_data`` carries the rule's argument bytes —
        previously every autonomy rule dispatched with NO argument bytes, so
        e.g. any wheel's overspeed disabled wheel 0 and rule 2004 commanded
        AOCS OFF instead of a safe mode (brief 02 root cause 8 / WP-9)."""
        if func_id in self._OBC_CRITICAL_FUNCS:
            obdh = self._engine.subsystems.get("obdh")
            sw_image = getattr(getattr(obdh, "_state", None), "sw_image", None)
            if sw_image == 0:  # SW_BOOTLOADER
                logger.warning(
                    "S19 rule %d BLOCKED: OBC-critical S8 func %d refused while OBC in bootloader",
                    ea_id, func_id,
                )
                return
        # An S19 rule can fire from inside another command's dispatch (an S8
        # command emits an event, the event fires a rule). Isolate its outcome
        # so a failing autonomy action cannot become the operator command's
        # verdict — the non-reentrancy half of C-1.
        outer = (self._err_reason, self._err_code, self._exec_fail,
                 self._request_id)
        self._err_reason, self._err_code, self._exec_fail = "", 0, None
        try:
            self._handle_s8(1, bytes([func_id]) + bytes(action_data))
            if self._err_reason:
                logger.warning("S19 rule %d action func %d refused: %s",
                               ea_id, func_id, self._err_reason)
        finally:
            (self._err_reason, self._err_code, self._exec_fail,
             self._request_id) = outer

    def trigger_event_action(self, trigger_id: int,
                             kind: int = TRIGGER_KIND_EVENT) -> None:
        """Called when an event (or an S12 limit violation) occurs.

        Event ids and monitored parameter ids both occupy 0x0000-0x0FFF, so the
        id alone is ambiguous. Matching therefore requires BOTH the id and the
        namespace to agree. Previously a rule matched an exact event id *and*
        any param violation with the same low 12 bits, which dual-triggered 17
        of the 26 shipped rules (defect #70).

        Args:
            trigger_id: event id (kind=TRIGGER_KIND_EVENT) or parameter id
                        (kind=TRIGGER_KIND_PARAM).
            kind: namespace of ``trigger_id``.
        """
        with self._lock:
            for ea_id, defn in self._s19_definitions.items():
                if ea_id not in self._s19_enabled_ids:
                    continue
                if int(defn.get('trigger_kind', TRIGGER_KIND_EVENT)) != kind:
                    continue
                if int(defn['event_type']) != trigger_id:
                    continue

                func_id = defn['action_func_id']
                logger.info(
                    "S19 triggered: rule %d executing S8 func %d for %s 0x%04X",
                    ea_id, func_id,
                    "param violation" if kind == TRIGGER_KIND_PARAM else "event",
                    trigger_id)
                # Execute the S8 function (guarded against OBC-critical funcs
                # while the OBC is in the bootloader).
                self._s19_dispatch(ea_id, func_id,
                                   bytes(defn.get('action_data', b"")))

    # ─── S20 Parameter Management ────────────────────────────────────

    # Parameters that map to engine-internal flags. S20.1 writes must
    # update the flag so the engine doesn't overwrite the value next tick.
    _PARAM_ENGINE_FLAGS = {
        0x05FF: '_override_passes',   # pass override (bool)
    }

    def _handle_s20(self, subtype: int, data: bytes) -> list[bytes]:
        """S20 Parameter Management."""
        if subtype == 1 and len(data) >= 6:
            param_id = struct.unpack('>H', data[:2])[0]
            value = struct.unpack('>f', data[2:6])[0]
            self._engine.params[param_id] = value
            # Sync engine-internal flags that shadow specific params
            flag_attr = self._PARAM_ENGINE_FLAGS.get(param_id)
            if flag_attr is not None:
                setattr(self._engine, flag_attr, bool(value))
        elif subtype == 3 and len(data) >= 2:
            param_id = struct.unpack('>H', data[:2])[0]
            value = float(self._engine.params.get(param_id, 0.0))
            pkt = self._engine.tm_builder.build_param_value_report(
                param_id, value
            )
            return [pkt]
        return []

    # ─── Power state checking ────────────────────────────────────────

    # Map (service, subtype, optional discriminator) → (power_line, label)
    # for the central acceptance-time power gate. A `None` line means
    # "no gate" (e.g. EPS/OBDH/Platform are always-on infrastructure).
    #
    # Discriminators:
    #   • S3.27 (on-demand HK): the SID owner
    #   • S8.1 (function management): the func_id range owner
    #   • S20.1 / S20.3 (parameter set/get): not gated (params are stored
    #     in the OBC, which is always on; downstream subsystems update
    #     them when running)
    _SID_OWNER = {
        1: None,            # EPS — always on
        2: "aocs_wheels",   # AOCS
        3: None,            # TCS — runs on OBC
        4: None,            # Platform — composite, always on
        5: "payload",       # Payload imager
        6: None,            # TTC — receiver always on
        11: None,           # Beacon
    }

    def _func_id_owner(self, func_id: int) -> str | None:
        if 0 <= func_id < 16:
            return "aocs_wheels"
        if 16 <= func_id < 26:
            return None  # EPS
        if 26 <= func_id < 40:
            return "payload"
        if 40 <= func_id < 50:
            return None  # TCS heaters individually power-gated by EPS
        if 50 <= func_id < 63:
            return None  # OBDH
        if 63 <= func_id < 79:
            return "ttc_tx"
        return None

    def check_power_state(
        self, service: int, subtype: int, data: bytes
    ) -> tuple[bool, str]:
        """Centralised TC power gate.

        Decides whether the target of a TC has its EPS power line on. If
        the answer is no, the engine rejects the TC at acceptance with
        S1.2 / error 0x0004 — *no* S1.1, S1.3, dispatch, or S1.7 is
        emitted, so the operator never sees TM coming back from a unit
        that should be cold and dark.

        Returns (allowed, reason). Permissive on unknown TCs.
        """
        eps = self._engine.subsystems.get("eps")
        if (
            not eps
            or not hasattr(eps, "_state")
            or not hasattr(eps._state, "power_lines")
        ):
            return True, ""
        lines = eps._state.power_lines

        line: str | None = None
        label: str = ""
        owner: str | None = None  # subsystem name for mode check
        if service == 3 and subtype == 27 and len(data) >= 2:
            # HK_REQUEST: always allow.  If the subsystem is unpowered its
            # params stay at zero/defaults in the shared param dict, so the
            # HK packet will contain zeros — which is the operationally
            # correct behaviour (the operator sees the SID but knows the
            # values are stale/zero because the equipment is off).
            return True, ""
        elif service == 8 and subtype == 1 and len(data) >= 1:
            func_id = data[0]
            line = self._func_id_owner(func_id)
            owner = self._func_id_subsys(func_id)
            label = f"S8 func_id {func_id}"
            # Boot/wakeup and equipment-management commands must be
            # reachable while the subsystem is in MODE_OFF.  The operator
            # powers the EPS line, then enables individual equipment
            # (wheels, star trackers, MTQs) *before* commanding a mode.
            # Bypass the mode gate for:
            #   AOCS: 0=set_mode, 2=disable_wheel, 3=enable_wheel,
            #         4=ST1 power, 5=ST2 power, 6=ST select,
            #         7=MAG select, 9=MTQ enable/disable
            #   Payload: 26=set_mode
            _EQUIPMENT_MGMT_FUNC_IDS = {0, 2, 3, 4, 5, 6, 7, 9, 26}
            if func_id in _EQUIPMENT_MGMT_FUNC_IDS:
                owner = None
        elif service == 20 and subtype in (1, 3) and len(data) >= 2:
            # S20 parameter set/get: reject if the param's source
            # subsystem is not powered + in an operational mode. Reads
            # of dark subsystems would otherwise return stale / zero
            # values and silently mislead the operator; writes would
            # land in the OBC param store but never be picked up by the
            # downstream model.
            param_id = struct.unpack(">H", data[:2])[0]
            if not self._param_source_powered(param_id):
                return (
                    False,
                    f"S20.{subtype} param 0x{param_id:04X}: "
                    f"source subsystem unpowered or in MODE_OFF",
                )
            return True, ""
        else:
            return True, ""

        # 1) EPS power-line gate
        if line is not None and not lines.get(line, True):
            return False, f"{label}: power line '{line}' is OFF"

        # 2) Subsystem operational-mode gate. The line being energised does
        # not mean the unit is *running* — at startup the AOCS line is hot
        # but s.mode is still OFF, the IMU is unbooted, the GPS has no
        # almanac, the wheels are coasting. Likewise the payload may have
        # its line on in standby with the imager core unpowered.
        # Pass-override is intended to force the comms link only — it must
        # never bypass this state machine.
        if owner:
            sub = self._engine.subsystems.get(owner)
            sub_state = getattr(sub, "_state", None)
            if sub_state is not None:
                mode = getattr(sub_state, "mode", None)
                if mode is not None and int(mode) <= 0:
                    return False, f"{label}: {owner} subsystem in MODE_OFF"

        return True, ""

    # Subsystem-name maps used by the second-stage mode gate above. These
    # mirror _SID_OWNER / _func_id_owner but resolve to the subsystem key in
    # engine.subsystems rather than an EPS line, so the gate can also check
    # the unit's internal mode (e.g. AOCS s.mode == MODE_OFF means the IMU
    # has not been booted, regardless of whether the wheel line is hot).
    _SID_SUBSYS = {
        1: None,        # EPS
        2: "aocs",      # AOCS
        3: None,        # TCS
        4: None,        # Platform
        5: "payload",   # Payload imager
        6: None,        # TTC
        11: None,       # Beacon
    }

    def _func_id_subsys(self, func_id: int) -> str | None:
        if 0 <= func_id < 16:
            return "aocs"
        if 16 <= func_id < 26:
            return None  # EPS commands
        if 26 <= func_id < 40:
            return "payload"
        if 40 <= func_id < 50:
            return None  # TCS
        if 50 <= func_id < 63:
            return None  # OBDH
        if 63 <= func_id < 79:
            return None  # TTC TX (line gate is sufficient)
        return None

    # ─── State save/restore (plan A-4) ───────────────────────────────
    # The dispatcher owns commandable on-board configuration that used to be
    # lost on every breakpoint load (brief 08 B-2): S12 monitoring definitions
    # (including per-definition enable flags and delta baselines), S19
    # event-action enable state, in-flight S13 transfers, S3 ground-defined HK
    # structures and the S5 event-type filter.

    def get_state(self) -> dict[str, Any]:
        # deepcopy, not dict(): a definition can carry nested containers
        # (`s3_custom_sids[sid]["param_ids"]`), and a shallow copy would put
        # a live list into the breakpoint (G2-f).
        import copy
        with self._lock:
            return {
                "s12_enabled": self._s12_enabled,
                "s12_definitions": {str(k): copy.deepcopy(v)
                                    for k, v in self._s12_definitions.items()},
                "s19_definitions": {str(k): copy.deepcopy(v)
                                    for k, v in self._s19_definitions.items()},
                "s19_enabled_ids": sorted(self._s19_enabled_ids),
                "s13_transfers": {str(k): copy.deepcopy(v)
                                  for k, v in self._s13_transfers.items()},
                "s13_transfer_counter": self._s13_transfer_counter,
                "s3_custom_sids": {str(k): copy.deepcopy(v)
                                   for k, v in self._s3_custom_sids.items()},
                "s5_enabled_types": sorted(self._s5_enabled_types),
            }

    def set_state(self, state: dict[str, Any]) -> None:
        import copy
        with self._lock:
            self._s12_enabled = bool(state.get("s12_enabled", True))
            self._s12_definitions = {
                int(k): copy.deepcopy(v)
                for k, v in state.get("s12_definitions", {}).items()}
            self._s19_definitions = {
                int(k): copy.deepcopy(v)
                for k, v in state.get("s19_definitions", {}).items()}
            self._s19_enabled_ids = set(
                int(i) for i in state.get("s19_enabled_ids", []))
            self._s13_transfers = {
                int(k): copy.deepcopy(v)
                for k, v in state.get("s13_transfers", {}).items()}
            self._s13_transfer_counter = int(
                state.get("s13_transfer_counter", 1))
            self._s3_custom_sids = {
                int(k): copy.deepcopy(v)
                for k, v in state.get("s3_custom_sids", {}).items()}
            if "s5_enabled_types" in state:
                self._s5_enabled_types = set(
                    int(i) for i in state["s5_enabled_types"])
