"""Mission scenario integration tests.

Higher-level operational scenarios testing multi-subsystem coordination
using the ServiceDispatcher with a mock engine.
"""
import struct
import pytest
from unittest.mock import MagicMock

from smo_simulator.service_dispatch import ServiceDispatcher


@pytest.fixture
def scenario_engine():
    """Create a mock engine for scenario testing."""
    engine = MagicMock()
    engine.params = {}
    engine._hk_structures = {}
    engine._hk_enabled = {}
    engine._hk_intervals = {}
    engine._get_cuc_time = MagicMock(return_value=5000)
    engine.tm_builder = MagicMock()
    engine.tm_builder._pack_tm = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_hk_packet = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_connection_test_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_param_value_report = MagicMock(return_value=b'\x00' * 20)
    engine.tm_builder.build_time_report = MagicMock(return_value=b'\x00' * 20)
    engine.subsystems = {
        "aocs": MagicMock(), "eps": MagicMock(), "payload": MagicMock(),
        "tcs": MagicMock(), "obdh": MagicMock(), "ttc": MagicMock(),
    }
    engine._tc_scheduler = MagicMock()
    engine._tm_storage = MagicMock()
    engine.event_types_enabled = set(range(256))
    return engine


@pytest.fixture
def dispatcher(scenario_engine):
    return ServiceDispatcher(scenario_engine)


class TestNominalPassScenario:
    """Test a nominal ground pass: connection test → HK request → commanding."""

    def test_connection_test_then_hk_request(self, dispatcher, scenario_engine):
        """S17.1 connection test followed by S3.27 HK one-shot."""
        # Connection test
        resp = dispatcher.dispatch(17, 1, b'')
        assert len(resp) > 0
        scenario_engine.tm_builder.build_connection_test_report.assert_called_once()

        # One-shot HK request
        sid_data = struct.pack('>H', 1)
        resp = dispatcher.dispatch(3, 27, sid_data)
        assert len(resp) > 0

    def test_sequential_commands_across_subsystems(self, dispatcher, scenario_engine):
        """Send commands to multiple subsystems in sequence."""
        # AOCS set mode (func_id=0, mode=4 NOMINAL)
        dispatcher.dispatch(8, 1, bytes([0, 4]))
        scenario_engine.subsystems["aocs"].handle_command.assert_called()

        # EPS power line on (func_id=19, line=0)
        dispatcher.dispatch(8, 1, bytes([19, 0]))
        scenario_engine.subsystems["eps"].handle_command.assert_called()

        # TTC PA on (func_id=66)
        dispatcher.dispatch(8, 1, bytes([66]))
        scenario_engine.subsystems["ttc"].handle_command.assert_called()


class TestMonitoringScenario:
    """Test monitoring configuration and checking flow."""

    def test_enable_add_check_disable(self, dispatcher, scenario_engine):
        """Full monitoring lifecycle."""
        # Enable monitoring
        dispatcher.dispatch(12, 1, b'')
        assert dispatcher._s12_enabled is True

        # Add monitoring definition for param 0x0100 (battery SoC)
        data = struct.pack('>HBff', 0x0100, 0, 20.0, 100.0)
        dispatcher.dispatch(12, 6, data)
        assert 0x0100 in dispatcher._s12_definitions

        # Set parameter value and check
        scenario_engine.params[0x0100] = 50.0
        violations = dispatcher.check_monitoring()
        assert len(violations) == 0  # 50 is within [20, 100]

        # Set parameter out of range
        scenario_engine.params[0x0100] = 15.0
        violations = dispatcher.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['param_id'] == 0x0100

        # Disable monitoring
        dispatcher.dispatch(12, 2, b'')
        violations = dispatcher.check_monitoring()
        assert len(violations) == 0  # disabled, no violations reported

    def test_report_monitoring_definitions(self, dispatcher):
        """Add definitions then report them with S12.12."""
        # Add two definitions
        data1 = struct.pack('>HBff', 0x0100, 0, 10.0, 90.0)
        data2 = struct.pack('>HBff', 0x0200, 1, -5.0, 5.0)
        dispatcher.dispatch(12, 6, data1)
        dispatcher.dispatch(12, 6, data2)

        # Report all
        resp = dispatcher.dispatch(12, 12, b'')
        assert len(resp) > 0

    def test_delete_monitoring_definition(self, dispatcher, scenario_engine):
        """Add and delete a monitoring definition."""
        data = struct.pack('>HBff', 0x0100, 0, 20.0, 80.0)
        dispatcher.dispatch(12, 6, data)
        assert 0x0100 in dispatcher._s12_definitions

        del_data = struct.pack('>H', 0x0100)
        dispatcher.dispatch(12, 7, del_data)
        assert 0x0100 not in dispatcher._s12_definitions


class TestEventActionScenario:
    """Test event-action rule configuration and triggering."""

    def test_add_enable_trigger_action(self, dispatcher, scenario_engine):
        """Add an event-action rule and trigger it."""
        # Add rule: ea_id=1, event_type=3 (MEDIUM), action=66 (TTC PA On)
        data = struct.pack('>HBB', 1, 3, 66)
        dispatcher.dispatch(19, 1, data)
        assert 1 in dispatcher._s19_definitions
        assert 1 in dispatcher._s19_enabled_ids

        # Trigger event type 3
        dispatcher.trigger_event_action(3)
        # Should have called _handle_s8 internally
        scenario_engine.subsystems["ttc"].handle_command.assert_called()

    def test_disable_prevents_trigger(self, dispatcher, scenario_engine):
        """Disabled rules should not trigger."""
        # Add and immediately disable
        data = struct.pack('>HBB', 2, 4, 50)
        dispatcher.dispatch(19, 1, data)

        dis_data = struct.pack('>H', 2)
        dispatcher.dispatch(19, 5, dis_data)
        assert 2 not in dispatcher._s19_enabled_ids

        # Trigger should do nothing
        scenario_engine.subsystems["ttc"].handle_command.reset_mock()
        dispatcher.trigger_event_action(4)
        scenario_engine.subsystems["ttc"].handle_command.assert_not_called()

    def test_report_all_definitions(self, dispatcher):
        """S19.8 report all event-action definitions."""
        # Add two rules
        dispatcher.dispatch(19, 1, struct.pack('>HBB', 10, 1, 0))
        dispatcher.dispatch(19, 1, struct.pack('>HBB', 20, 2, 1))

        resp = dispatcher.dispatch(19, 8, b'')
        assert len(resp) > 0


class TestEventReportingScenario:
    """Test S5 event type enable/disable."""

    def test_disable_all_then_enable_all(self, dispatcher):
        """S5.8 disables all, S5.7 enables all."""
        # Disable all
        dispatcher.dispatch(5, 8, b'')
        assert len(dispatcher._s5_enabled_types) == 0
        assert not dispatcher.is_event_enabled(1)

        # Enable all
        dispatcher.dispatch(5, 7, b'')
        for etype in range(1, 5):
            assert dispatcher.is_event_enabled(etype)

    def test_selective_enable_disable(self, dispatcher):
        """Enable/disable individual event types."""
        # Disable all first
        dispatcher.dispatch(5, 8, b'')

        # Enable type 2
        dispatcher.dispatch(5, 5, bytes([2]))
        assert dispatcher.is_event_enabled(2)
        assert not dispatcher.is_event_enabled(1)

        # Disable type 2
        dispatcher.dispatch(5, 6, bytes([2]))
        assert not dispatcher.is_event_enabled(2)


class TestHKManagementScenario:
    """Test S3 HK definition lifecycle."""

    def test_create_enable_disable_delete(self, dispatcher, scenario_engine):
        """Full HK definition lifecycle."""
        # Create HK SID 10 with interval 5s and 2 params
        data = struct.pack('>Hf', 10, 5.0) + bytes([2]) + struct.pack('>HH', 0x0100, 0x0101)
        dispatcher.dispatch(3, 1, data)
        assert 10 in dispatcher._s3_custom_sids
        scenario_engine.set_hk_enabled.assert_called_with(10, True)

        # Disable periodic HK
        dispatcher.dispatch(3, 6, struct.pack('>H', 10))
        scenario_engine.set_hk_enabled.assert_called_with(10, False)

        # Re-enable
        dispatcher.dispatch(3, 5, struct.pack('>H', 10))
        scenario_engine.set_hk_enabled.assert_called_with(10, True)

        # Delete
        dispatcher.dispatch(3, 2, struct.pack('>H', 10))
        assert 10 not in dispatcher._s3_custom_sids

    def test_modify_hk_interval(self, dispatcher, scenario_engine):
        """S3.31 modify HK interval."""
        data = struct.pack('>Hf', 1, 20.0)
        dispatcher.dispatch(3, 31, data)
        scenario_engine.set_hk_interval.assert_called_with(1, 20.0)


class TestMemoryOperationsScenario:
    """Test S6 memory operations."""

    def test_memory_dump_returns_response(self, dispatcher):
        """S6.5 dump should return S6.6 response."""
        data = struct.pack('>IH', 0x00001000, 64)
        resp = dispatcher.dispatch(6, 5, data)
        assert len(resp) > 0

    def test_memory_check_returns_checksum(self, dispatcher):
        """S6.9 check should return S6.10 with checksum."""
        data = struct.pack('>IH', 0x00002000, 128)
        resp = dispatcher.dispatch(6, 9, data)
        assert len(resp) > 0

    def test_memory_load(self, dispatcher):
        """S6.2 load should not crash."""
        data = struct.pack('>I', 0x00003000) + b'\xDE\xAD\xBE\xEF'
        resp = dispatcher.dispatch(6, 2, data)
        # S6.2 doesn't return a response, just logs
        assert isinstance(resp, list)


class TestSchedulingScenario:
    """Test S11 time-tagged scheduling operations."""

    def test_schedule_insert_and_list(self, dispatcher, scenario_engine):
        """Insert a scheduled command then list."""
        # Configure mock scheduler
        scenario_engine._tc_scheduler.insert.return_value = 42
        scenario_engine._tc_scheduler.list_commands.return_value = [
            {'id': 42, 'exec_time': 6000}
        ]

        # Insert: exec_time=6000, then some TC data
        data = struct.pack('>I', 6000) + b'\x00\x01\x02'
        resp = dispatcher.dispatch(11, 4, data)
        assert len(resp) > 0
        scenario_engine._tc_scheduler.insert.assert_called_once()

        # List
        resp = dispatcher.dispatch(11, 17, b'')
        assert len(resp) > 0

    def test_schedule_enable_disable(self, dispatcher, scenario_engine):
        """Enable and disable scheduler."""
        dispatcher.dispatch(11, 9, b'')  # disable
        scenario_engine._tc_scheduler.disable_schedule.assert_called_once()

        dispatcher.dispatch(11, 13, b'')  # enable
        scenario_engine._tc_scheduler.enable_schedule.assert_called_once()


class TestParameterManagementScenario:
    """Test S20 parameter set/get flow."""

    def test_set_then_read_parameter(self, dispatcher, scenario_engine):
        """Set a parameter with S20.1 then read it with S20.3."""
        # Set param 0x0100 to 42.5
        data = struct.pack('>Hf', 0x0100, 42.5)
        dispatcher.dispatch(20, 1, data)
        assert scenario_engine.params[0x0100] == pytest.approx(42.5, abs=0.1)

        # Read param 0x0100
        read_data = struct.pack('>H', 0x0100)
        resp = dispatcher.dispatch(20, 3, read_data)
        assert len(resp) > 0
        scenario_engine.tm_builder.build_param_value_report.assert_called()


class TestStorageScenario:
    """Test S15 onboard storage operations."""

    def test_store_enable_dump_delete_status(self, dispatcher, scenario_engine):
        """Full store lifecycle: enable → dump → delete → status."""
        scenario_engine._tm_storage.start_dump.return_value = [b'\x00' * 10]
        scenario_engine._tm_storage.get_status.return_value = [
            {'id': 1, 'count': 5, 'capacity': 100, 'enabled': True}
        ]
        # queue_dump is the new paced-dump entry point; return >0 so S1.5
        # progress is emitted.
        scenario_engine.queue_dump.return_value = 1

        # Enable store 1
        dispatcher.dispatch(15, 1, bytes([1]))
        scenario_engine._tm_storage.enable_store.assert_called_with(1)

        # Dump store 1
        resp = dispatcher.dispatch(15, 9, bytes([1]))
        assert len(resp) > 0
        scenario_engine.queue_dump.assert_called_with(1)

        # Delete store 1
        dispatcher.dispatch(15, 11, bytes([1]))
        scenario_engine._tm_storage.delete_store.assert_called_with(1)

        # Report status
        resp = dispatcher.dispatch(15, 13, b'')
        assert len(resp) > 0


class TestMultiSubsystemCoordination:
    """Test operations that span multiple subsystems."""

    def test_imaging_sequence(self, dispatcher, scenario_engine):
        """AOCS fine-point → payload capture → store dump."""
        # AOCS fine point mode (func_id=0, mode=5)
        dispatcher.dispatch(8, 1, bytes([0, 5]))
        scenario_engine.subsystems["aocs"].handle_command.assert_called_with(
            {"command": "set_mode", "mode": 5}
        )

        # Payload capture (func_id=28, lat=0, lon=0)
        capture_data = bytes([28]) + struct.pack('>ff', 45.0, -73.0)
        dispatcher.dispatch(8, 1, capture_data)
        scenario_engine.subsystems["payload"].handle_command.assert_called()

        # Store dump (now goes through engine.queue_dump for paced release)
        scenario_engine.queue_dump.return_value = 1
        dispatcher.dispatch(15, 9, bytes([3]))  # Science store
        scenario_engine.queue_dump.assert_called_with(3)

    def test_thermal_heater_control(self, dispatcher, scenario_engine):
        """Control heaters across TCS subsystem."""
        # Battery heater on (func_id=40, on=1)
        dispatcher.dispatch(8, 1, bytes([40, 1]))
        scenario_engine.subsystems["tcs"].handle_command.assert_called_with(
            {"command": "heater", "circuit": "battery", "on": True}
        )

        # OBC heater off (func_id=41, on=0)
        dispatcher.dispatch(8, 1, bytes([41, 0]))
        scenario_engine.subsystems["tcs"].handle_command.assert_called_with(
            {"command": "heater", "circuit": "obc", "on": False}
        )
