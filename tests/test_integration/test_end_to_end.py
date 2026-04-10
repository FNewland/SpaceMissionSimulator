"""Integration tests — end-to-end command/response flows.

Tests full TC dispatch and TM response generation using a ServiceDispatcher
backed by a mock engine. Each test exercises a complete PUS service round-trip:
build TC data, dispatch, and verify the TM response(s).
"""
import struct
import pytest
from unittest.mock import MagicMock

from smo_simulator.service_dispatch import ServiceDispatcher


# ---------------------------------------------------------------------------
# Helper: create a mock engine with all required attributes
# ---------------------------------------------------------------------------

def make_mock_engine():
    """Create a minimal mock engine suitable for ServiceDispatcher."""
    engine = MagicMock()
    engine.params = {}
    engine._hk_structures = {}
    engine._hk_enabled = {}
    engine._hk_intervals = {}
    engine._get_cuc_time = MagicMock(return_value=1000)
    engine.tm_builder = MagicMock()
    engine.tm_builder._pack_tm = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_hk_packet = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_connection_test_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_param_value_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_time_report = MagicMock(return_value=b'\x00' * 20)
    engine.subsystems = {
        "aocs": MagicMock(),
        "eps": MagicMock(),
        "payload": MagicMock(),
        "tcs": MagicMock(),
        "obdh": MagicMock(),
        "ttc": MagicMock(),
    }
    engine._tc_scheduler = MagicMock()
    engine._tm_storage = MagicMock()
    engine.event_types_enabled = set(range(256))
    return engine


# ===================================================================
# S17 — Connection Test
# ===================================================================

class TestS17ConnectionTestFlow:
    """Dispatch S17.1 and verify a connection-test TM packet is returned."""

    def test_s17_connection_test_flow(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        responses = dispatcher.dispatch(17, 1, b'', None)

        assert len(responses) == 1, "S17.1 should return exactly one TM packet"
        engine.tm_builder.build_connection_test_report.assert_called_once()
        # The returned packet should be the bytes from build_connection_test_report
        assert responses[0] == engine.tm_builder.build_connection_test_report.return_value


# ===================================================================
# S3 — Housekeeping One-Shot Request
# ===================================================================

class TestS3HKRequestFlow:
    """Dispatch S3.27 with a SID and verify a HK packet is returned."""

    def test_s3_hk_request_flow(self):
        engine = make_mock_engine()
        sid = 1
        hk_struct = [(0x0100, '>f', 1.0), (0x0101, '>f', 1.0)]
        engine._hk_structures = {sid: hk_struct}
        dispatcher = ServiceDispatcher(engine)

        data = struct.pack('>H', sid)
        responses = dispatcher.dispatch(3, 27, data, None)

        assert len(responses) == 1, "S3.27 should return exactly one HK packet"
        engine.tm_builder.build_hk_packet.assert_called_once_with(
            sid, engine.params, hk_structure=hk_struct
        )


# ===================================================================
# S9 — Time Report
# ===================================================================

class TestS9TimeReportFlow:
    """Dispatch S9.2 and verify a time report TM packet is returned."""

    def test_s9_time_report(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        responses = dispatcher.dispatch(9, 2, b'', None)

        assert len(responses) == 1, "S9.2 should return exactly one time-report packet"
        engine._get_cuc_time.assert_called_once()
        engine.tm_builder.build_time_report.assert_called_once_with(1000)


# ===================================================================
# S6 — Memory Dump
# ===================================================================

class TestS6MemoryDumpFlow:
    """Dispatch S6.5 (MEM_DUMP) and verify S6.6 response packet."""

    def test_s6_memory_dump(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        address = 0x0000_8000
        length = 128
        data = struct.pack('>IH', address, length)

        responses = dispatcher.dispatch(6, 5, data, None)

        assert len(responses) == 1, "S6.5 should return one S6.6 dump-data packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 6
        assert call_kwargs[1]['subtype'] == 6
        # Verify the response data starts with the requested address
        resp_data = call_kwargs[1]['data']
        resp_addr = struct.unpack('>I', resp_data[:4])[0]
        assert resp_addr == address


# ===================================================================
# S6 — Memory Check
# ===================================================================

class TestS6MemoryCheckFlow:
    """Dispatch S6.9 (MEM_CHECK) and verify checksum response."""

    def test_s6_memory_check(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        address = 0x0000_2000
        length = 64
        data = struct.pack('>IH', address, length)

        responses = dispatcher.dispatch(6, 9, data, None)

        assert len(responses) == 1, "S6.9 should return one S6.10 checksum packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 6
        assert call_kwargs[1]['subtype'] == 10
        # Verify the response data contains the address, length, and a checksum
        resp_data = call_kwargs[1]['data']
        r_addr, r_len, r_csum = struct.unpack('>IHH', resp_data)
        assert r_addr == address
        assert r_len == length
        # CRC-16-CCITT of Boot ROM region (0x55 pattern repeated 64 times) = 0x3BBF
        assert r_csum == 0x3BBF  # computed CRC-16-CCITT checksum


# ===================================================================
# S20 — Parameter Set then Read
# ===================================================================

class TestS20ParamSetReadFlow:
    """Dispatch S20.1 to set a parameter, then S20.3 to read it back."""

    def test_s20_param_set_read(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        param_id = 0x0100
        value = 28.75

        # Step 1 — set parameter via S20.1
        set_data = struct.pack('>Hf', param_id, value)
        set_responses = dispatcher.dispatch(20, 1, set_data, None)
        assert set_responses == [], "S20.1 SET should return no TM packets"
        assert engine.params[param_id] == pytest.approx(value, abs=0.01)

        # Step 2 — read parameter via S20.3
        read_data = struct.pack('>H', param_id)
        read_responses = dispatcher.dispatch(20, 3, read_data, None)
        assert len(read_responses) == 1, "S20.3 GET should return one TM packet"
        engine.tm_builder.build_param_value_report.assert_called_once_with(
            param_id, pytest.approx(value, abs=0.01)
        )


# ===================================================================
# S5 — Enable / Disable Events
# ===================================================================

class TestS5EnableDisableEventsFlow:
    """S5.5 enable and S5.6 disable individual event types."""

    def test_s5_enable_disable_events(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        event_type = 42

        # All types are enabled by default — disable type 42
        disable_data = bytes([event_type])
        dispatcher.dispatch(5, 6, disable_data, None)
        assert not dispatcher.is_event_enabled(event_type), \
            "Event type 42 should be disabled after S5.6"

        # Re-enable type 42
        enable_data = bytes([event_type])
        dispatcher.dispatch(5, 5, enable_data, None)
        assert dispatcher.is_event_enabled(event_type), \
            "Event type 42 should be enabled after S5.5"


# ===================================================================
# S5 — Enable All / Disable All
# ===================================================================

class TestS5EnableAllFlow:
    """S5.8 disables all events, S5.7 enables all."""

    def test_s5_enable_all(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        # Step 1 — disable all event types via S5.8
        dispatcher.dispatch(5, 8, b'', None)
        assert len(dispatcher._s5_enabled_types) == 0, \
            "All event types should be disabled after S5.8"
        assert not dispatcher.is_event_enabled(1)
        assert not dispatcher.is_event_enabled(100)

        # Step 2 — enable all event types via S5.7
        dispatcher.dispatch(5, 7, b'', None)
        # S5.7 adds types 1-4 back (per implementation)
        for etype in range(1, 5):
            assert dispatcher.is_event_enabled(etype), \
                f"Event type {etype} should be enabled after S5.7"


# ===================================================================
# S12 — Monitoring Lifecycle
# ===================================================================

class TestS12MonitoringLifecycle:
    """Add a monitoring definition, check for violations, then delete."""

    def test_s12_monitoring_lifecycle(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        param_id = 0x0101
        low_limit = 10.0
        high_limit = 90.0

        # Step 1 — add monitoring definition via S12.6
        add_data = struct.pack('>HBff', param_id, 0, low_limit, high_limit)
        dispatcher.dispatch(12, 6, add_data, None)
        assert param_id in dispatcher._s12_definitions

        # Step 2 — set param within limits: no violations
        engine.params[param_id] = 50.0
        violations = dispatcher.check_monitoring()
        assert violations == []

        # Step 3 — set param out-of-limits: expect violation
        engine.params[param_id] = 95.0
        violations = dispatcher.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['param_id'] == param_id
        assert violations[0]['type'] == 'out_of_limits'

        # Step 4 — delete the definition via S12.7
        del_data = struct.pack('>H', param_id)
        dispatcher.dispatch(12, 7, del_data, None)
        assert param_id not in dispatcher._s12_definitions

        # After deletion no violations should be reported
        violations = dispatcher.check_monitoring()
        assert violations == []


# ===================================================================
# S19 — Event-Action Lifecycle
# ===================================================================

class TestS19EventActionLifecycle:
    """Add a rule, enable it, trigger the event, verify the action fires."""

    def test_s19_event_action_lifecycle(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        ea_id = 1
        event_type = 10
        action_func_id = 1  # AOCS desaturate

        # Step 1 — add event-action via S19.1
        add_data = struct.pack('>HBB', ea_id, event_type, action_func_id)
        dispatcher.dispatch(19, 1, add_data, None)
        assert ea_id in dispatcher._s19_definitions
        assert ea_id in dispatcher._s19_enabled_ids

        # Step 2 — trigger the event
        dispatcher.trigger_event_action(event_type)
        engine.subsystems["aocs"].handle_command.assert_called_once_with(
            {"command": "desaturate"}
        )

        # Step 3 — disable the rule via S19.5, trigger again, no new call
        engine.subsystems["aocs"].handle_command.reset_mock()
        disable_data = struct.pack('>H', ea_id)
        dispatcher.dispatch(19, 5, disable_data, None)
        dispatcher.trigger_event_action(event_type)
        engine.subsystems["aocs"].handle_command.assert_not_called()

        # Step 4 — re-enable via S19.4, trigger, action fires again
        enable_data = struct.pack('>H', ea_id)
        dispatcher.dispatch(19, 4, enable_data, None)
        dispatcher.trigger_event_action(event_type)
        engine.subsystems["aocs"].handle_command.assert_called_once_with(
            {"command": "desaturate"}
        )


# ===================================================================
# S19 — Report Definitions
# ===================================================================

class TestS19ReportDefinitions:
    """Add rules, dispatch S19.8, verify report TM packet."""

    def test_s19_report_definitions(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        # Add two event-action rules
        for ea_id, evt, act in [(1, 10, 1), (2, 20, 42)]:
            data = struct.pack('>HBB', ea_id, evt, act)
            dispatcher.dispatch(19, 1, data, None)

        # Request report via S19.8
        responses = dispatcher.dispatch(19, 8, b'', None)

        assert len(responses) == 1, "S19.8 should return one report packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 19
        assert call_kwargs[1]['subtype'] == 128

        # Verify report data structure: count(2) + entries
        resp_data = call_kwargs[1]['data']
        count = struct.unpack('>H', resp_data[:2])[0]
        assert count == 2


# ===================================================================
# S12 — Report Definitions
# ===================================================================

class TestS12ReportDefinitions:
    """Add monitoring defs, dispatch S12.12, verify report TM packet."""

    def test_s12_report_definitions(self):
        engine = make_mock_engine()
        dispatcher = ServiceDispatcher(engine)

        # Add two monitoring definitions
        for param_id, low, high in [(0x0101, 10.0, 90.0), (0x0102, 0.0, 50.0)]:
            data = struct.pack('>HBff', param_id, 0, low, high)
            dispatcher.dispatch(12, 6, data, None)

        # Request report via S12.12
        responses = dispatcher.dispatch(12, 12, b'', None)

        assert len(responses) == 1, "S12.12 should return one report packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 12
        assert call_kwargs[1]['subtype'] == 13

        # Verify report data structure: count(2) + entries
        resp_data = call_kwargs[1]['data']
        count = struct.unpack('>H', resp_data[:2])[0]
        assert count == 2


# ===================================================================
# S15 — Store Status
# ===================================================================

class TestS15StoreStatus:
    """Dispatch S15.13 and verify a status response is returned."""

    def test_s15_store_status(self):
        engine = make_mock_engine()
        engine._tm_storage.get_status.return_value = [
            {'id': 1, 'count': 25, 'capacity': 5000, 'enabled': True},
            {'id': 2, 'count': 0, 'capacity': 2000, 'enabled': False},
        ]
        dispatcher = ServiceDispatcher(engine)

        responses = dispatcher.dispatch(15, 13, b'', None)

        assert len(responses) == 1, "S15.13 should return one status packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 15
        assert call_kwargs[1]['subtype'] == 14

        # Verify the packed status data
        resp_data = call_kwargs[1]['data']
        store_count = struct.unpack('>B', resp_data[:1])[0]
        assert store_count == 2


# ===================================================================
# S11 — Schedule List
# ===================================================================

class TestS11ScheduleList:
    """Insert a time-tagged command, then dispatch S11.17 to list."""

    def test_s11_schedule_list(self):
        engine = make_mock_engine()
        engine._tc_scheduler.insert.return_value = 1
        engine._tc_scheduler.list_commands.return_value = [
            {'id': 1, 'exec_time': 5000},
        ]
        dispatcher = ServiceDispatcher(engine)

        # Step 1 — insert a time-tagged command via S11.4
        exec_time = 5000
        inner_tc = b'\xAA\xBB\xCC'
        insert_data = struct.pack('>I', exec_time) + inner_tc
        insert_responses = dispatcher.dispatch(11, 4, insert_data, None)

        assert len(insert_responses) == 1, "S11.4 should return an ack packet"
        engine._tc_scheduler.insert.assert_called_once_with(exec_time, inner_tc)

        # Step 2 — list scheduled commands via S11.17
        list_responses = dispatcher.dispatch(11, 17, b'', None)

        assert len(list_responses) == 1, "S11.17 should return one list packet"
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 11
        assert call_kwargs[1]['subtype'] == 18

        # Verify list data: count(2) + [id(2) + exec_time(4)] per entry
        resp_data = call_kwargs[1]['data']
        count = struct.unpack('>H', resp_data[:2])[0]
        assert count == 1
        cmd_id, cmd_time = struct.unpack('>HI', resp_data[2:8])
        assert cmd_id == 1
        assert cmd_time == 5000


# ===================================================================
# Acceptance — Reject Unknown Service
# ===================================================================

class TestAcceptanceRejectsBadService:
    """Verify _check_tc_acceptance rejects unknown services."""

    def test_acceptance_rejects_bad_service(self):
        from smo_simulator.engine import SimulationEngine

        # Bind the real method to a mock engine to test acceptance logic
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        # Unknown service 99
        accepted, error_code = engine._check_tc_acceptance(99, 1, b'')
        assert not accepted, "Unknown service should be rejected"
        assert error_code == 0x0001, "Error code should be 0x0001 (unknown service)"

        # Unknown service 255
        accepted, error_code = engine._check_tc_acceptance(255, 1, b'')
        assert not accepted
        assert error_code == 0x0001

        # Valid service 17, valid subtype 1
        accepted, error_code = engine._check_tc_acceptance(17, 1, b'')
        assert accepted, "Known service/subtype should be accepted"
        assert error_code == 0

        # Valid service 3, invalid subtype 99
        accepted, error_code = engine._check_tc_acceptance(3, 99, b'')
        assert not accepted, "Invalid subtype should be rejected"
        assert error_code == 0x0002

        # S8 with empty data should be rejected
        accepted, error_code = engine._check_tc_acceptance(8, 1, b'')
        assert not accepted, "S8.1 with no data should be rejected"
        assert error_code == 0x0003
