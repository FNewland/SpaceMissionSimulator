"""SMO Simulator — PUS TC Service Dispatcher.

Clean class-based dispatcher. Routes incoming TC packets to appropriate
service handlers. Supports S1, S3, S5, S6, S8, S9, S11, S12, S15, S17,
S19, S20.
"""
import struct
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class ServiceDispatcher:
    """Dispatches PUS TC packets to service-specific handlers."""

    def __init__(self, engine):
        self._lock = threading.Lock()
        self._engine = engine
        self._last_error = ""
        self._last_error_code = 0

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

    def generate_s1_exec_fail(self, request_id: int, error_code: int) -> list[bytes]:
        """Generate S1.4 execution start failure report."""
        tm = self._engine.tm_builder
        data = struct.pack('>HH', request_id, error_code)
        return [tm._pack_tm(service=1, subtype=4, data=data)]

    def dispatch(self, service: int, subtype: int, data: bytes,
                 primary_header=None) -> list[bytes]:
        """Dispatch a TC and return list of TM response packets."""
        with self._lock:
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
                elif service == 19:
                    responses = self._handle_s19(subtype, data)
                elif service == 20:
                    responses = self._handle_s20(subtype, data)
                else:
                    logger.debug("Unhandled service %d subtype %d", service, subtype)
            except Exception as e:
                logger.warning("Service %d dispatch error: %s", service, e)
            return responses

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
                # Generate S1.3 execution start success
                req_id = struct.unpack('>H', primary_header[0:2])[0] if primary_header and len(primary_header) >= 2 else 0
                responses.extend(self.generate_s1_reports(req_id, 2, subtype))
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
                req_id = struct.unpack('>H', primary_header[0:2])[0] if primary_header and len(primary_header) >= 2 else 0
                responses.extend(self.generate_s1_reports(req_id, 2, subtype))
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
        return subsys.set_device_state(device_id, on_off)

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

    def _is_memory_readonly(self, addr: int) -> bool:
        """Check if address is in a read-only memory region (Boot ROM)."""
        # Boot ROM regions: 0x00000000-0x0001FFFF (64KB)
        return 0x00000000 <= addr < 0x00020000

    def _generate_memory_content(self, addr: int, length: int) -> bytes:
        """Generate simulated memory content based on address region."""
        content = bytearray()
        for i in range(length):
            cur_addr = addr + i
            # Return different patterns for different regions
            if 0x00000000 <= cur_addr < 0x00020000:
                # Boot ROM: pattern 0x55
                content.append(0x55)
            elif 0x00100000 <= cur_addr < 0x00200000:
                # Application A: pattern with address bits
                content.append((cur_addr >> 8) & 0xFF)
            elif 0x00200000 <= cur_addr < 0x00220000:
                # Configuration: pattern 0xAA
                content.append(0xAA)
            elif 0x20000000 <= cur_addr < 0x20040000:
                # Scratchpad RAM: incrementing pattern
                content.append((cur_addr & 0xFF) ^ ((cur_addr >> 8) & 0xFF))
            else:
                # Default: fill with zeros
                content.append(0x00)
        return bytes(content)

    def _handle_s6(self, subtype: int, data: bytes) -> list[bytes]:
        """S6 Memory Management — load/dump/check with CRC-16."""
        responses: list[bytes] = []
        if subtype == 2 and len(data) >= 4:
            # MEM_LOAD: address(4) + data
            addr = struct.unpack('>I', data[:4])[0]
            payload_data = data[4:]
            # Check if trying to write to read-only region
            if self._is_memory_readonly(addr):
                logger.warning("S6 MEM_LOAD rejected — write to read-only region 0x%08X", addr)
                return self.generate_s1_exec_fail(0, 0x0002)  # error code 2: write protected
            logger.info("S6 MEM_LOAD at 0x%08X, %d bytes", addr, len(payload_data))
            # S1.5 progress for multi-step memory load
            progress = self.generate_s1_progress(0, 1)  # step 1
            responses.extend(progress)
        elif subtype == 5 and len(data) >= 6:
            # MEM_DUMP: address(4) + length(2)
            addr = struct.unpack('>I', data[:4])[0]
            length = struct.unpack('>H', data[4:6])[0]
            logger.info("S6 MEM_DUMP at 0x%08X, %d bytes", addr, length)
            # Generate simulated memory content
            mem_content = self._generate_memory_content(addr, min(length, 256))
            mem_data = struct.pack('>I', addr) + struct.pack('>H', len(mem_content)) + mem_content
            return [self._engine.tm_builder._pack_tm(
                service=6, subtype=6, data=mem_data
            )]
        elif subtype == 9 and len(data) >= 6:
            # MEM_CHECK: address(4) + length(2)
            addr = struct.unpack('>I', data[:4])[0]
            length = struct.unpack('>H', data[4:6])[0]
            # Generate memory content and compute CRC-16
            mem_content = self._generate_memory_content(addr, length)
            crc = self._crc16_ccitt(mem_content)
            logger.info("S6 MEM_CHECK at 0x%08X, %d bytes, CRC=0x%04X", addr, length, crc)
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
        elif func_id in range(63, 79):
            return self._route_ttc_cmd(func_id, data[1:])
        elif func_id == 80:
            return self._route_obdh_cmd(func_id, data[1:])
        return []

    def _route_aocs_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        aocs = self._engine.subsystems.get("aocs")
        if not aocs:
            return []
        if func_id == 0:  # set mode
            mode = data[0] if data else 0
            aocs.handle_command({"command": "set_mode", "mode": mode})
        elif func_id == 1:  # desaturate
            aocs.handle_command({"command": "desaturate"})
        elif func_id == 2:  # disable wheel
            wheel = data[0] if data else 0
            aocs.handle_command({"command": "disable_wheel", "wheel": wheel})
        elif func_id == 3:  # enable wheel
            wheel = data[0] if data else 0
            aocs.handle_command({"command": "enable_wheel", "wheel": wheel})
        elif func_id == 4:  # ST1 power
            on = bool(data[0]) if data else True
            aocs.handle_command({"command": "st_power", "unit": 1, "on": on})
        elif func_id == 5:  # ST2 power
            on = bool(data[0]) if data else True
            aocs.handle_command({"command": "st_power", "unit": 2, "on": on})
        elif func_id == 6:  # ST select
            unit = data[0] if data else 1
            aocs.handle_command({"command": "st_select", "unit": unit})
        elif func_id == 7:  # Magnetometer select
            on = bool(data[0]) if data else True
            aocs.handle_command({"command": "mag_select", "on": on})
        elif func_id == 8:  # RW set speed bias
            if len(data) >= 5:
                wheel = data[0]
                bias = struct.unpack('>f', data[1:5])[0]
                aocs.handle_command({
                    "command": "rw_set_speed_bias", "wheel": wheel, "bias": bias
                })
        elif func_id == 9:  # MTQ enable/disable
            on = bool(data[0]) if data else True
            cmd = "mtq_enable" if on else "mtq_disable"
            aocs.handle_command({"command": cmd})
        elif func_id == 10:  # AOCS_SLEW_TO: quaternion (4 floats) + rate (1 float)
            if len(data) >= 20:
                q = [
                    struct.unpack('>f', data[0:4])[0],
                    struct.unpack('>f', data[4:8])[0],
                    struct.unpack('>f', data[8:12])[0],
                    struct.unpack('>f', data[12:16])[0],
                ]
                rate_dps = struct.unpack('>f', data[16:20])[0]
                aocs.handle_command({
                    "command": "slew_to_quaternion",
                    "quaternion": q,
                    "rate_dps": rate_dps
                })
        elif func_id == 11:  # AOCS_CHECK_MOMENTUM
            result = aocs.handle_command({"command": "check_momentum"})
            if result.get("success"):
                momentum = result.get("total_momentum_nms", 0.0)
                saturation = result.get("saturation_percent", 0.0)
                resp_data = struct.pack('>ff', momentum, saturation)
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=130, data=resp_data
                )]
        elif func_id == 12:  # AOCS_BEGIN_ACQUISITION
            aocs.handle_command({"command": "begin_acquisition"})
        elif func_id == 13:  # AOCS_GYRO_CALIBRATION
            aocs.handle_command({"command": "gyro_calibration"})
        elif func_id == 14:  # AOCS_RW_RAMP_DOWN: wheel_idx (1) + target_rpm (4)
            wheel = data[0] if data else 255  # 255 means all wheels
            target_rpm = struct.unpack('>f', data[1:5])[0] if len(data) >= 5 else 0.0
            aocs.handle_command({
                "command": "rw_ramp_down",
                "wheel": wheel,
                "target_rpm": target_rpm
            })
        elif func_id == 15:  # AOCS_SET_DEADBAND: deadband_deg (4 bytes float)
            deadband = struct.unpack('>f', data[0:4])[0] if len(data) >= 4 else 0.01
            aocs.handle_command({
                "command": "set_deadband",
                "deadband_deg": deadband
            })
        return []

    def _route_eps_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        eps = self._engine.subsystems.get("eps")
        if not eps:
            return []
        if func_id == 16:  # payload mode
            mode = data[0] if data else 0
            eps.handle_command({"command": "set_payload_mode", "mode": mode})
        elif func_id == 17:  # fpa cooler
            on = bool(data[0]) if data else False
            eps.handle_command({"command": "set_fpa_cooler", "on": on})
        elif func_id == 18:  # transponder tx
            on = bool(data[0]) if data else True
            eps.handle_command({"command": "set_transponder_tx", "on": on})
        elif func_id == 19:  # power line on
            if data:
                line_idx = data[0]
                result = eps.handle_command({
                    "command": "power_line_on", "line_index": line_idx
                })
                if not result.get("success"):
                    return self._make_error_response(result)
        elif func_id == 20:  # power line off
            if data:
                line_idx = data[0]
                result = eps.handle_command({
                    "command": "power_line_off", "line_index": line_idx
                })
                if not result.get("success"):
                    return self._make_error_response(result)
        elif func_id == 21:  # reset OC flag
            if data:
                line_idx = data[0]
                eps.handle_command({
                    "command": "reset_oc_flag", "line_index": line_idx
                })
        elif func_id == 22:  # switch load
            if len(data) >= 2:
                load_id = data[0]
                state = bool(data[1])
                eps.handle_command({
                    "command": "switch_load", "load_id": load_id, "state": state
                })
        elif func_id == 23:  # set charge rate
            if len(data) >= 4:
                rate_a = struct.unpack('>f', data[:4])[0]
                eps.handle_command({
                    "command": "set_charge_rate", "rate_a": rate_a
                })
        elif func_id == 24:  # solar array drive
            if len(data) >= 2:
                angle_deg = struct.unpack('>h', data[:2])[0]
                eps.handle_command({
                    "command": "set_solar_array_drive", "angle_deg": angle_deg / 100.0
                })
        elif func_id == 25:  # emergency load shed
            if data:
                stage = data[0]
                eps.handle_command({
                    "command": "emergency_load_shed", "stage": stage
                })
        return []

    def _make_error_response(self, result: dict) -> list[bytes]:
        """Store error info for the engine to use in S1.8 generation."""
        self._last_error = result.get("message", "Unknown error")
        self._last_error_code = result.get("error_code", 0x0001)
        return []

    def _route_payload_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        payload = self._engine.subsystems.get("payload")
        if not payload:
            return []
        if func_id == 26:  # set mode
            mode = data[0] if data else 0
            payload.handle_command({"command": "set_mode", "mode": mode})
        elif func_id == 27:  # set scene
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            payload.handle_command({"command": "set_scene", "scene_id": scene_id})
        elif func_id == 28:  # capture image
            lat = 0.0
            lon = 0.0
            if len(data) >= 8:
                lat = struct.unpack('>f', data[:4])[0]
                lon = struct.unpack('>f', data[4:8])[0]
            payload.handle_command({
                "command": "capture", "lat": lat, "lon": lon
            })
        elif func_id == 29:  # download image
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            payload.handle_command({
                "command": "download_image", "scene_id": scene_id
            })
        elif func_id == 30:  # delete image
            scene_id = (
                struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            )
            payload.handle_command({
                "command": "delete_image", "scene_id": scene_id
            })
        elif func_id == 31:  # mark bad segment
            seg = data[0] if data else 0
            payload.handle_command({
                "command": "mark_bad_segment", "segment": seg
            })
        elif func_id == 32:  # get image catalog
            payload.handle_command({"command": "get_image_catalog"})
        elif func_id == 33:  # set band configuration
            mask = data[0] if data else 0x0F
            result = payload.handle_command({
                "command": "set_band_config", "mask": mask
            })
        elif func_id == 34:  # set integration time (4 floats per band)
            if len(data) >= 16:
                times = [
                    struct.unpack('>f', data[0:4])[0],
                    struct.unpack('>f', data[4:8])[0],
                    struct.unpack('>f', data[8:12])[0],
                    struct.unpack('>f', data[12:16])[0],
                ]
                payload.handle_command({
                    "command": "set_integration_time", "times": times
                })
        elif func_id == 35:  # set detector gain
            if len(data) >= 4:
                gain = struct.unpack('>f', data[0:4])[0]
                payload.handle_command({
                    "command": "set_detector_gain", "gain": gain
                })
        elif func_id == 36:  # set cooler setpoint
            if len(data) >= 4:
                setpoint = struct.unpack('>f', data[0:4])[0]
                payload.handle_command({
                    "command": "set_cooler_setpoint", "setpoint": setpoint
                })
        elif func_id == 37:  # start calibration
            payload.handle_command({"command": "start_calibration"})
        elif func_id == 38:  # stop calibration
            payload.handle_command({"command": "stop_calibration"})
        elif func_id == 39:  # set compression override
            if len(data) >= 4:
                ratio = struct.unpack('>f', data[0:4])[0]
                payload.handle_command({
                    "command": "set_compression", "ratio": ratio
                })
        return []

    def _route_tcs_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        tcs = self._engine.subsystems.get("tcs")
        if not tcs:
            return []
        circuits = {40: "battery", 41: "obc", 42: "thruster"}
        if func_id in circuits:
            on = bool(data[0]) if data else True
            tcs.handle_command({
                "command": "heater", "circuit": circuits[func_id], "on": on
            })
        elif func_id == 43:  # FPA cooler
            on = bool(data[0]) if data else True
            tcs.handle_command({"command": "fpa_cooler", "on": on})
        elif func_id == 44:  # Set heater setpoint
            if len(data) >= 9:
                circuit = data[0]
                on_temp = struct.unpack('>f', data[1:5])[0]
                off_temp = struct.unpack('>f', data[5:9])[0]
                tcs.handle_command({
                    "command": "set_setpoint",
                    "circuit": circuit,
                    "on_temp": on_temp,
                    "off_temp": off_temp,
                })
        elif func_id == 45:  # Auto mode
            circuit = data[0] if data else 0
            tcs.handle_command({
                "command": "auto_mode", "circuit": circuit
            })
        elif func_id == 46:  # Set heater duty limit (Phase 5)
            if len(data) >= 2:
                circuit = data[0]
                duty_pct = data[1]
                tcs.handle_command({
                    "command": "set_heater_duty_limit",
                    "circuit": circuit,
                    "duty_limit_pct": float(duty_pct),
                })
        elif func_id == 47:  # Decontamination start (Phase 5)
            target_temp = -50.0
            if len(data) >= 4:
                target_temp = struct.unpack('>f', data[:4])[0]
            tcs.handle_command({
                "command": "decontamination_start",
                "target_temp_c": target_temp,
            })
        elif func_id == 48:  # Decontamination stop (Phase 5)
            tcs.handle_command({"command": "decontamination_stop"})
        elif func_id == 49:  # Get thermal map (Phase 5)
            result = tcs.handle_command({"command": "get_thermal_map"})
            # Optionally return thermal map data as TM
            # For now, just acknowledge
        return []

    def _route_obdh_cmd(self, func_id: int, data: bytes) -> list[bytes]:
        obdh = self._engine.subsystems.get("obdh")
        if not obdh:
            return []
        if func_id == 50:  # set mode
            mode = data[0] if data else 0
            obdh.handle_command({"command": "set_mode", "mode": mode})
        elif func_id == 51:  # memory scrub
            obdh.handle_command({"command": "memory_scrub"})
        elif func_id == 52:  # OBC reboot
            obdh.handle_command({"command": "obc_reboot"})
        elif func_id == 53:  # OBC switch unit
            obdh.handle_command({"command": "obc_switch_unit"})
        elif func_id == 54:  # OBC select bus
            bus = data[0] if data else 0
            obdh.handle_command({"command": "obc_select_bus", "bus": bus})
        elif func_id == 55:  # OBC boot app
            obdh.handle_command({"command": "obc_boot_app"})
        elif func_id == 56:  # OBC boot inhibit
            inhibit = bool(data[0]) if data else True
            obdh.handle_command({
                "command": "obc_boot_inhibit", "inhibit": inhibit
            })
        elif func_id == 57:  # OBC clear reboot count
            obdh.handle_command({"command": "obc_clear_reboot_cnt"})
        elif func_id == 58:  # OBC set watchdog period
            if len(data) >= 2:
                period = struct.unpack('>H', data[:2])[0]
                obdh.handle_command({"command": "set_watchdog_period", "period": period})
        elif func_id == 59:  # OBC watchdog enable
            obdh.handle_command({"command": "watchdog_enable"})
        elif func_id == 60:  # OBC watchdog disable
            obdh.handle_command({"command": "watchdog_disable"})
        elif func_id == 61:  # OBC diagnostic
            result = obdh.handle_command({"command": "diagnostic"})
            if result.get("success"):
                health_data = result.get("data", b"")
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=health_data
                )]
        elif func_id == 62:  # OBC error log
            result = obdh.handle_command({"command": "error_log"})
            if result.get("success"):
                log_data = result.get("data", b"")
                return [self._engine.tm_builder._pack_tm(
                    service=8, subtype=2, data=log_data
                )]
        elif func_id == 80:  # GPS time sync
            result = obdh.handle_command({"command": "gps_time_sync",
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
                        self._engine._event_queue.append({
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
            ttc.handle_command({"command": "switch_primary"})
        elif func_id == 64:  # switch redundant
            ttc.handle_command({"command": "switch_redundant"})
        elif func_id == 65:  # set tm rate
            rate = (
                struct.unpack('>I', data[:4])[0]
                if len(data) >= 4
                else 64000
            )
            ttc.handle_command({"command": "set_tm_rate", "rate": rate})
        elif func_id == 66:  # PA on
            ttc.handle_command({"command": "pa_on"})
        elif func_id == 67:  # PA off
            ttc.handle_command({"command": "pa_off"})
        elif func_id == 68:  # Set TX power
            power = (
                struct.unpack('>f', data[:4])[0]
                if len(data) >= 4
                else 2.0
            )
            ttc.handle_command({"command": "set_tx_power", "power_w": power})
        elif func_id == 69:  # Deploy antennas
            ttc.handle_command({"command": "deploy_antennas"})
        elif func_id == 70:  # Set beacon mode
            ttc.handle_command({
                "command": "set_beacon_mode",
                "on": bool(data[0]) if data else True,
            })
        elif func_id == 71:  # Start command channel
            ttc.handle_command({"command": "cmd_channel_start"})
        elif func_id == 72:  # Set uplink frequency
            if len(data) >= 4:
                freq_mhz = struct.unpack('>f', data[:4])[0]
                ttc.handle_command({"command": "set_ul_freq", "freq_mhz": freq_mhz})
        elif func_id == 73:  # Set downlink frequency
            if len(data) >= 4:
                freq_mhz = struct.unpack('>f', data[:4])[0]
                ttc.handle_command({"command": "set_dl_freq", "freq_mhz": freq_mhz})
        elif func_id == 74:  # Set modulation mode
            mode = data[0] if data else 0
            ttc.handle_command({"command": "set_modulation", "mode": mode})
        elif func_id == 75:  # Set RX gain (AGC target)
            if len(data) >= 4:
                agc_db = struct.unpack('>f', data[:4])[0]
                ttc.handle_command({"command": "set_rx_gain", "agc_db": agc_db})
        elif func_id == 76:  # Start ranging
            ttc.handle_command({"command": "ranging_start"})
        elif func_id == 77:  # Stop ranging
            ttc.handle_command({"command": "ranging_stop"})
        elif func_id == 78:  # Set coherent transponder mode
            on = bool(data[0]) if data else True
            ttc.handle_command({"command": "set_coherent_mode", "on": on})
        return []

    # ─── S9 Time Management ─────────────────────────────────────────

    def _handle_s9(self, subtype: int, data: bytes) -> list[bytes]:
        """S9 Time Management."""
        if subtype == 1 and len(data) >= 4:
            cuc = struct.unpack('>I', data[:4])[0]
            obdh = self._engine.subsystems.get("obdh")
            if obdh:
                obdh.handle_command({"command": "set_time", "cuc": cuc})
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
            cmds = scheduler.list_commands()
            resp_data = struct.pack('>H', len(cmds))
            for c in cmds:
                resp_data += struct.pack('>HI', c['id'], c['exec_time'])
            return [self._engine.tm_builder._pack_tm(
                service=11, subtype=18, data=resp_data
            )]
        return []

    # ─── S12 On-Board Monitoring ─────────────────────────────────────

    def _handle_s12(self, subtype: int, data: bytes) -> list[bytes]:
        """S12 On-Board Monitoring — parameter limit checking."""
        if subtype == 1:
            # Enable monitoring
            self._s12_enabled = True
        elif subtype == 2:
            # Disable monitoring
            self._s12_enabled = False
        elif subtype == 6 and len(data) >= 10:
            # Add monitoring definition
            # Format: param_id(2) + check_type(1) + low_limit(4) + high_limit(4)
            #   check_type: 0=absolute, 1=delta
            mon_id = struct.unpack('>H', data[:2])[0]
            check_type = data[2] if len(data) > 2 else 0
            low = struct.unpack('>f', data[3:7])[0] if len(data) >= 7 else 0.0
            high = struct.unpack('>f', data[7:11])[0] if len(data) >= 11 else 0.0
            self._s12_definitions[mon_id] = {
                'param_id': mon_id,
                'check_type': check_type,
                'low_limit': low,
                'high_limit': high,
                'enabled': True,
                'last_value': None,
            }
        elif subtype == 7 and len(data) >= 2:
            # Delete monitoring definition
            mon_id = struct.unpack('>H', data[:2])[0]
            self._s12_definitions.pop(mon_id, None)
        elif subtype == 12:
            # Report all monitoring definitions
            resp_data = struct.pack('>H', len(self._s12_definitions))
            for mon_id, defn in self._s12_definitions.items():
                resp_data += struct.pack('>HBff',
                    defn['param_id'], defn.get('check_type', 0),
                    defn['low_limit'], defn['high_limit'])
            return [self._engine.tm_builder._pack_tm(
                service=12, subtype=13, data=resp_data
            )]
        return []

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
                    continue

                low = defn['low_limit']
                high = defn['high_limit']
                severity = defn.get('severity', 'WARNING')
                name = defn.get('name', f"param_0x{param_id:04X}")
                description = defn.get('description', '')

                if value < low or value > high:
                    # Map severity string to numeric level for events
                    severity_map = {'INFO': 1, 'WARNING': 2, 'ALARM': 3, 'HIGH': 4}
                    severity_level = severity_map.get(severity, 2)

                    violations.append({
                        'param_id': param_id,
                        'mon_id': mon_id,
                        'value': value,
                        'low_limit': low,
                        'high_limit': high,
                        'type': 'out_of_limits',
                        'severity': severity_level,
                        'name': name,
                        'description': description,
                    })

            return violations

    # ─── S13 Large Data Transfer ─────────────────────────────────────

    def _handle_s13(self, subtype: int, data: bytes) -> list[bytes]:
        """S13 Large Data Transfer — create/manage/complete transfer sessions."""
        if subtype == 1 and len(data) >= 4:
            # S13.1: Create download transfer
            # Format: scene_id(2) + initial_size_mb(2) OR variant: scene_id(2) + reserved(2)
            scene_id = struct.unpack('>H', data[:2])[0]
            size_mb = struct.unpack('>H', data[2:4])[0] if len(data) >= 4 else 800
            if size_mb == 0:
                size_mb = 800  # Default image size

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
                xfer = self._s13_transfers[transfer_id]
                xfer['status'] = 'COMPLETE'
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
            status = storage.get_status()
            resp_data = struct.pack('>B', len(status))
            for s in status:
                resp_data += struct.pack(
                    '>BHHB',
                    s['id'], s['count'], s['capacity'],
                    1 if s['enabled'] else 0,
                )
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

    # ─── S19 Event-Action ────────────────────────────────────────────

    def _handle_s19(self, subtype: int, data: bytes) -> list[bytes]:
        """S19 Event-Action — link events to automatic responses."""
        if subtype == 1 and len(data) >= 4:
            # Add event-action definition
            # Format: ea_id(2) + event_type(1) + action_func_id(1)
            ea_id = struct.unpack('>H', data[:2])[0]
            event_type = data[2]
            action_func_id = data[3]
            self._s19_definitions[ea_id] = {
                'event_type': event_type,
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
            # Report all event-action definitions
            resp_data = struct.pack('>H', len(self._s19_definitions))
            for ea_id, defn in self._s19_definitions.items():
                enabled = 1 if ea_id in self._s19_enabled_ids else 0
                resp_data += struct.pack('>HBBB',
                    ea_id, defn['event_type'], defn['action_func_id'], enabled)
            return [self._engine.tm_builder._pack_tm(
                service=19, subtype=128, data=resp_data
            )]
        return []

    def trigger_event_action(self, event_id: int) -> None:
        """Called when an event occurs — check for matching event-actions.

        Args:
            event_id: Event ID that triggered (can be severity for legacy calls,
                      or specific event ID like 0x0100, 0x9000|param_id, etc.)
        """
        with self._lock:
            for ea_id, defn in self._s19_definitions.items():
                if ea_id not in self._s19_enabled_ids:
                    continue

                # Match event-action rule to the event
                rule_event_type = defn['event_type']

                # Try direct match first (exact event ID)
                if rule_event_type == event_id:
                    func_id = defn['action_func_id']
                    logger.info("S19 triggered: rule %d executing S8 func %d for event 0x%04X",
                               ea_id, func_id, event_id)
                    # Execute the S8 function
                    self._handle_s8(1, bytes([func_id]))
                    continue

                # Also match by param_id if this is a param limit violation (0x9000 range)
                if (event_id & 0xF000) == 0x9000 and (rule_event_type & 0xF000) == 0x0000:
                    # Rule specifies a parameter ID directly (e.g., 0x0101 for battery SoC)
                    # and event is a param violation (0x9000 | param_id)
                    event_param_id = event_id & 0x0FFF
                    rule_param_id = rule_event_type & 0x0FFF
                    if event_param_id == rule_param_id:
                        func_id = defn['action_func_id']
                        logger.info("S19 triggered: rule %d executing S8 func %d for param 0x%04X violation",
                                   ea_id, func_id, event_param_id)
                        # Execute the S8 function
                        self._handle_s8(1, bytes([func_id]))

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
