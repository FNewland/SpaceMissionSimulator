"""Tests for new PUS services in ServiceDispatcher.

Covers S5 (Event Reporting), S6 (Memory Management), S12 (On-Board Monitoring),
S19 (Event-Action), S8 (new function routes), and S3 (ECSS subtypes).
"""
import struct
import pytest
from unittest.mock import MagicMock


def make_mock_engine():
    """Create a minimal mock engine for testing dispatch."""
    engine = MagicMock()
    engine.params = {}
    engine._hk_structures = {}
    engine._hk_enabled = {}
    engine._hk_intervals = {}
    engine._get_cuc_time = MagicMock(return_value=1000)
    engine.tm_builder = MagicMock()
    engine.tm_builder.build_hk_packet = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_param_value_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_time_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_connection_test_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder._pack_tm = MagicMock(return_value=b'\x00' * 20)
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
    return engine


# ─── S5 Event Reporting ──────────────────────────────────────────────────────


class TestS5EventReporting:
    """Test S5 Event Reporting — enable/disable event types."""

    def test_enable_event_type(self):
        """Dispatch S5.5 with event type 42, verify is_event_enabled(42) is True."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # First disable type 42 so we can confirm enable works
        d._s5_enabled_types.discard(42)
        assert not d.is_event_enabled(42)

        # Enable event type 42 via S5.5
        data = bytes([42])
        d.dispatch(5, 5, data, None)
        assert d.is_event_enabled(42) is True

    def test_disable_event_type(self):
        """Enable type 42, then dispatch S5.6 to disable it."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Type 42 is enabled by default (all 0-255 are)
        assert d.is_event_enabled(42) is True

        # Disable event type 42 via S5.6
        data = bytes([42])
        d.dispatch(5, 6, data, None)
        assert d.is_event_enabled(42) is False

    def test_all_events_enabled_by_default(self):
        """A new dispatcher has all event types 0-255 enabled."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        assert d.is_event_enabled(0) is True
        assert d.is_event_enabled(100) is True
        assert d.is_event_enabled(255) is True
        # The full set should contain 256 entries
        assert len(d._s5_enabled_types) == 256


# ─── S6 Memory Management ────────────────────────────────────────────────────


class TestS6Memory:
    """Test S6 Memory Management — dump, check, and load."""

    def test_mem_dump_returns_response(self):
        """S6.5 MEM_DUMP with address=0x1000 length=64 returns 1 response."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        address = 0x1000
        length = 64
        data = struct.pack('>IH', address, length)
        responses = d.dispatch(6, 5, data, None)

        assert len(responses) == 1
        engine.tm_builder._pack_tm.assert_called_once()
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 6
        assert call_kwargs[1]['subtype'] == 6

    def test_mem_check_returns_response(self):
        """S6.9 MEM_CHECK with address=0x2000 length=128 returns 1 response."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        address = 0x2000
        length = 128
        data = struct.pack('>IH', address, length)
        responses = d.dispatch(6, 9, data, None)

        assert len(responses) == 1
        engine.tm_builder._pack_tm.assert_called_once()
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 6
        assert call_kwargs[1]['subtype'] == 10

    def test_mem_load_returns_progress(self):
        """S6.2 MEM_LOAD with address+data returns S1.5 progress report."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        address = 0x20001000  # Use a writable SRAM address instead of read-only boot ROM
        payload_data = b'\xDE\xAD\xBE\xEF'
        data = struct.pack('>I', address) + payload_data
        responses = d.dispatch(6, 2, data, None)

        assert len(responses) == 1  # S1.5 progress packet
        # Verify _pack_tm was called with S1.5 (service=1, subtype=5)
        engine.tm_builder._pack_tm.assert_called_once()
        call_kwargs = engine.tm_builder._pack_tm.call_args
        assert call_kwargs[1]['service'] == 1
        assert call_kwargs[1]['subtype'] == 5


# ─── S12 On-Board Monitoring ─────────────────────────────────────────────────


class TestS12Monitoring:
    """Test S12 On-Board Monitoring — parameter limit checking."""

    def test_enable_disable_monitoring(self):
        """S12.1 enables monitoring, S12.2 disables it."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Monitoring is enabled by default
        assert d._s12_enabled is True

        # Disable via S12.2
        d.dispatch(12, 2, b'', None)
        assert d._s12_enabled is False

        # Re-enable via S12.1
        d.dispatch(12, 1, b'', None)
        assert d._s12_enabled is True

    def test_add_monitoring_definition(self):
        """S12.6 adds a monitoring definition with param_id, check_type, limits."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        param_id = 0x0101
        check_type = 0  # absolute
        low = 20.0
        high = 80.0
        data = struct.pack('>HBff', param_id, check_type, low, high)
        d.dispatch(12, 6, data, None)

        assert param_id in d._s12_definitions
        defn = d._s12_definitions[param_id]
        assert defn['param_id'] == param_id
        assert defn['check_type'] == check_type
        assert defn['low_limit'] == pytest.approx(low)
        assert defn['high_limit'] == pytest.approx(high)
        assert defn['enabled'] is True

    def test_delete_monitoring_definition(self):
        """Add a definition, then S12.7 deletes it."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add first
        param_id = 0x0101
        data_add = struct.pack('>HBff', param_id, 0, 20.0, 80.0)
        d.dispatch(12, 6, data_add, None)
        assert param_id in d._s12_definitions

        # Delete via S12.7
        data_del = struct.pack('>H', param_id)
        d.dispatch(12, 7, data_del, None)
        assert param_id not in d._s12_definitions

    def test_check_monitoring_detects_violation(self):
        """Param above high limit triggers an out_of_limits violation."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add definition: param 0x0101, limits [20, 80]
        param_id = 0x0101
        data = struct.pack('>HBff', param_id, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)

        # Set param value above high limit
        engine.params[param_id] = 90.0

        violations = d.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['param_id'] == param_id
        assert violations[0]['value'] == 90.0
        assert violations[0]['type'] == 'out_of_limits'

    def test_check_monitoring_nominal(self):
        """Param within limits produces no violations."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        param_id = 0x0101
        data = struct.pack('>HBff', param_id, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)

        # Set param value within limits
        engine.params[param_id] = 50.0

        violations = d.check_monitoring()
        assert violations == []

    def test_check_monitoring_disabled(self):
        """When monitoring is disabled, no violations are returned even if out of limits."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Disable monitoring
        d.dispatch(12, 2, b'', None)

        # Add definition and set param out of limits
        param_id = 0x0101
        data = struct.pack('>HBff', param_id, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        engine.params[param_id] = 90.0

        violations = d.check_monitoring()
        assert violations == []


# ─── S19 Event-Action ────────────────────────────────────────────────────────


class TestS19EventAction:
    """Test S19 Event-Action — linking events to automatic responses."""

    def test_add_event_action(self):
        """S19.1 adds an event-action definition and enables it."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        ea_id = 1
        event_type = 5
        action_func_id = 42
        data = struct.pack('>HBB', ea_id, event_type, action_func_id)
        d.dispatch(19, 1, data, None)

        assert ea_id in d._s19_definitions
        defn = d._s19_definitions[ea_id]
        assert defn['event_type'] == event_type
        assert defn['action_func_id'] == action_func_id
        assert ea_id in d._s19_enabled_ids

    def test_delete_event_action(self):
        """Add an event-action, then S19.2 deletes it."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add
        ea_id = 1
        data_add = struct.pack('>HBB', ea_id, 5, 42)
        d.dispatch(19, 1, data_add, None)
        assert ea_id in d._s19_definitions

        # Delete via S19.2
        data_del = struct.pack('>H', ea_id)
        d.dispatch(19, 2, data_del, None)
        assert ea_id not in d._s19_definitions
        assert ea_id not in d._s19_enabled_ids

    def test_enable_disable_event_action(self):
        """Disable via S19.5, then re-enable via S19.4."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add an event-action (auto-enabled)
        ea_id = 1
        data_add = struct.pack('>HBB', ea_id, 5, 42)
        d.dispatch(19, 1, data_add, None)
        assert ea_id in d._s19_enabled_ids

        # Disable via S19.5
        data_disable = struct.pack('>H', ea_id)
        d.dispatch(19, 5, data_disable, None)
        assert ea_id not in d._s19_enabled_ids

        # Re-enable via S19.4
        data_enable = struct.pack('>H', ea_id)
        d.dispatch(19, 4, data_enable, None)
        assert ea_id in d._s19_enabled_ids

    def test_trigger_event_action(self):
        """Trigger event_type=10 with action_func_id=1 (AOCS desaturate)."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add event-action: event_type=10, action_func_id=1 (desaturate)
        ea_id = 1
        event_type = 10
        action_func_id = 1  # AOCS desaturate
        data = struct.pack('>HBB', ea_id, event_type, action_func_id)
        d.dispatch(19, 1, data, None)

        # Trigger the event
        d.trigger_event_action(event_type)

        # func_id=1 routes to _route_aocs_cmd -> desaturate
        engine.subsystems["aocs"].handle_command.assert_called_once_with(
            {"command": "desaturate"}
        )

    def test_trigger_disabled_event_action_does_nothing(self):
        """Disabled event-action should not fire any subsystem command."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # Add and then disable
        ea_id = 1
        event_type = 10
        action_func_id = 1
        data_add = struct.pack('>HBB', ea_id, event_type, action_func_id)
        d.dispatch(19, 1, data_add, None)
        data_disable = struct.pack('>H', ea_id)
        d.dispatch(19, 5, data_disable, None)

        # Trigger the event — should do nothing
        d.trigger_event_action(event_type)

        engine.subsystems["aocs"].handle_command.assert_not_called()


# ─── S8 New Function Routes ──────────────────────────────────────────────────


class TestS8NewRoutes:
    """Test S8 Function Management — new subsystem command routes."""

    def test_s8_st_power(self):
        """S8.1 func_id=4 routes to AOCS st_power command."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # func_id=4 (ST1 power), on=1
        data = bytes([4, 1])
        d.dispatch(8, 1, data, None)

        engine.subsystems["aocs"].handle_command.assert_called_once_with(
            {"command": "st_power", "unit": 1, "on": True}
        )

    def test_s8_obc_reboot(self):
        """S8.1 func_id=52 routes to OBDH obc_reboot command."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # func_id=52 (OBC reboot), no extra data needed
        data = bytes([52])
        d.dispatch(8, 1, data, None)

        engine.subsystems["obdh"].handle_command.assert_called_once_with(
            {"command": "obc_reboot"}
        )

    def test_s8_pa_on(self):
        """S8.1 func_id=66 routes to TTC pa_on command."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        data = bytes([66])
        d.dispatch(8, 1, data, None)

        engine.subsystems["ttc"].handle_command.assert_called_once_with(
            {"command": "pa_on"}
        )

    def test_s8_payload_capture(self):
        """S8.1 func_id=28 routes to payload capture with lat/lon."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        lat = 45.5
        lon = -73.6
        # func_id=28 is first byte, followed by lat(float) + lon(float)
        data = bytes([28]) + struct.pack('>ff', lat, lon)
        d.dispatch(8, 1, data, None)

        engine.subsystems["payload"].handle_command.assert_called_once()
        call_args = engine.subsystems["payload"].handle_command.call_args[0][0]
        assert call_args["command"] == "capture"
        assert call_args["lat"] == pytest.approx(lat, abs=0.1)
        assert call_args["lon"] == pytest.approx(lon, abs=0.1)

    def test_s8_heater_set_setpoint(self):
        """S8.1 func_id=44 routes to TCS set_setpoint with circuit and temps."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        circuit = 2
        on_temp = 15.0
        off_temp = 25.0
        # func_id=44 first byte, then circuit(1) + on_temp(float) + off_temp(float)
        data = bytes([44]) + struct.pack('>Bff', circuit, on_temp, off_temp)
        d.dispatch(8, 1, data, None)

        engine.subsystems["tcs"].handle_command.assert_called_once()
        call_args = engine.subsystems["tcs"].handle_command.call_args[0][0]
        assert call_args["command"] == "set_setpoint"
        assert call_args["circuit"] == circuit
        assert call_args["on_temp"] == pytest.approx(on_temp)
        assert call_args["off_temp"] == pytest.approx(off_temp)

    def test_s8_reset_oc_flag(self):
        """S8.1 func_id=21 with line=3 routes to EPS reset_oc_flag."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        # func_id=21 (reset OC flag), line_index=3
        data = bytes([21, 3])
        d.dispatch(8, 1, data, None)

        engine.subsystems["eps"].handle_command.assert_called_once_with(
            {"command": "reset_oc_flag", "line_index": 3}
        )


# ─── S3 Enhanced ECSS Subtypes ───────────────────────────────────────────────


class TestS3EnhancedSubtypes:
    """Test S3 Housekeeping — ECSS PUS-C subtypes."""

    def test_s3_ecss_enable(self):
        """S3.5 (ECSS enable periodic HK) with sid=1 calls set_hk_enabled(1, True)."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        sid = 1
        data = struct.pack('>H', sid)
        d.dispatch(3, 5, data, None)

        engine.set_hk_enabled.assert_called_with(sid, True)

    def test_s3_ecss_disable(self):
        """S3.6 (ECSS disable periodic HK) with sid=1 calls set_hk_enabled(1, False)."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        sid = 1
        data = struct.pack('>H', sid)
        d.dispatch(3, 6, data, None)

        engine.set_hk_enabled.assert_called_with(sid, False)

    def test_s3_ecss_modify_interval(self):
        """S3.31 modifies HK interval via set_hk_interval."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)

        sid = 1
        interval_s = 5.0
        data = struct.pack('>Hf', sid, interval_s)
        d.dispatch(3, 31, data, None)

        engine.set_hk_interval.assert_called_once_with(sid, interval_s)
