"""Tests for S12 on-board monitoring and S19 event-action integration.

Covers:
  - S12 check_monitoring called per tick (via dispatcher)
  - S12 violation generates events for out-of-limits
  - S12 enable/disable monitoring
  - S12 add/delete monitoring definitions
  - S12 limit types (absolute, check low/high)
  - S19 event-action trigger on matching event
  - S19 action execution (stored TC runs)
  - S19 enable/disable per rule
  - S19 multiple rules for same event type
  - S12 report (subtype 12)
  - S19 report (subtype 8)
"""
import struct
import pytest
from unittest.mock import MagicMock

from smo_simulator.service_dispatch import ServiceDispatcher


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
        "aocs": MagicMock(), "eps": MagicMock(), "payload": MagicMock(),
        "tcs": MagicMock(), "obdh": MagicMock(), "ttc": MagicMock(),
    }
    eps_mock = engine.subsystems["eps"]
    eps_mock._state = MagicMock()
    eps_mock._state.power_lines = {}
    eps_mock.handle_command = MagicMock(return_value={"success": True})
    engine._tc_scheduler = MagicMock()
    engine._tm_storage = MagicMock()
    return engine


class TestS12MonitoringDefinitions:
    """Test S12 monitoring definition management."""

    def test_add_monitoring_definition(self):
        """S12.6 should add a monitoring definition."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Format: param_id(2) + check_type(1) + low_limit(4) + high_limit(4)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        assert 0x0101 in d._s12_definitions
        defn = d._s12_definitions[0x0101]
        assert defn['param_id'] == 0x0101
        assert defn['low_limit'] == pytest.approx(20.0)
        assert defn['high_limit'] == pytest.approx(80.0)
        assert defn['enabled'] is True

    def test_delete_monitoring_definition(self):
        """S12.7 should delete a monitoring definition."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Add first
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        assert 0x0101 in d._s12_definitions
        # Delete
        del_data = struct.pack('>H', 0x0101)
        d.dispatch(12, 7, del_data, None)
        assert 0x0101 not in d._s12_definitions

    def test_enable_monitoring(self):
        """S12.1 should enable monitoring globally."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d._s12_enabled = False
        d.dispatch(12, 1, b'', None)
        assert d._s12_enabled is True

    def test_disable_monitoring(self):
        """S12.2 should disable monitoring globally."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d._s12_enabled = True
        d.dispatch(12, 2, b'', None)
        assert d._s12_enabled is False


class TestS12MonitoringChecks:
    """Test S12 check_monitoring() violation detection."""

    def test_no_violations_within_limits(self):
        """No violations when parameter value is within limits."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        engine.params[0x0101] = 50.0  # Within limits
        violations = d.check_monitoring()
        assert len(violations) == 0

    def test_violation_below_low_limit(self):
        """Violation when parameter value is below low limit."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        engine.params[0x0101] = 10.0  # Below low limit
        violations = d.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['param_id'] == 0x0101
        assert violations[0]['value'] == 10.0

    def test_violation_above_high_limit(self):
        """Violation when parameter value is above high limit."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        engine.params[0x0101] = 95.0  # Above high limit
        violations = d.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['value'] == 95.0

    def test_no_violations_when_disabled(self):
        """No violations when monitoring is globally disabled."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        engine.params[0x0101] = 10.0  # Would violate
        d._s12_enabled = False
        violations = d.check_monitoring()
        assert len(violations) == 0

    def test_no_violations_when_param_missing(self):
        """No violations when monitored param is not in params dict."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBff', 0x0101, 0, 20.0, 80.0)
        d.dispatch(12, 6, data, None)
        # Don't set the param -> engine.params[0x0101] doesn't exist
        violations = d.check_monitoring()
        assert len(violations) == 0

    def test_multiple_monitoring_definitions(self):
        """Multiple params can be monitored simultaneously."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Add two definitions
        d.dispatch(12, 6, struct.pack('>HBff', 0x0101, 0, 20.0, 80.0), None)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0302, 0, 0.0, 90.0), None)
        engine.params[0x0101] = 10.0  # Violation
        engine.params[0x0302] = 50.0  # Within limits
        violations = d.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['param_id'] == 0x0101

    def test_both_params_violating(self):
        """Both params violating should produce two violations."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0101, 0, 20.0, 80.0), None)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0302, 0, 0.0, 90.0), None)
        engine.params[0x0101] = 10.0  # Below low
        engine.params[0x0302] = 95.0  # Above high
        violations = d.check_monitoring()
        assert len(violations) == 2

    def test_violation_includes_limit_values(self):
        """Violation dict should include low_limit and high_limit."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0101, 0, 20.0, 80.0), None)
        engine.params[0x0101] = 5.0
        violations = d.check_monitoring()
        assert violations[0]['low_limit'] == pytest.approx(20.0)
        assert violations[0]['high_limit'] == pytest.approx(80.0)


class TestS12Report:
    """Test S12 monitoring report generation."""

    def test_s12_report_subtype_12(self):
        """S12.12 should report all monitoring definitions."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0101, 0, 20.0, 80.0), None)
        d.dispatch(12, 6, struct.pack('>HBff', 0x0302, 0, 10.0, 90.0), None)
        responses = d.dispatch(12, 12, b'', None)
        assert len(responses) == 1
        engine.tm_builder._pack_tm.assert_called()


class TestS19EventActionDefinitions:
    """Test S19 event-action definition management."""

    def test_add_event_action(self):
        """S19.1 should add an event-action definition."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Format: ea_id(2) + event_type(1) + action_func_id(1)
        data = struct.pack('>HBB', 1, 3, 42)  # ea_id=1, severity=3, func=42
        d.dispatch(19, 1, data, None)
        assert 1 in d._s19_definitions
        assert d._s19_definitions[1]['event_type'] == 3
        assert d._s19_definitions[1]['action_func_id'] == 42
        assert 1 in d._s19_enabled_ids

    def test_delete_event_action(self):
        """S19.2 should delete an event-action definition."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBB', 1, 3, 42)
        d.dispatch(19, 1, data, None)
        del_data = struct.pack('>H', 1)
        d.dispatch(19, 2, del_data, None)
        assert 1 not in d._s19_definitions
        assert 1 not in d._s19_enabled_ids

    def test_enable_event_action(self):
        """S19.4 should enable an event-action."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBB', 1, 3, 42)
        d.dispatch(19, 1, data, None)
        # Disable first
        d._s19_enabled_ids.discard(1)
        assert 1 not in d._s19_enabled_ids
        # Enable
        d.dispatch(19, 4, struct.pack('>H', 1), None)
        assert 1 in d._s19_enabled_ids

    def test_disable_event_action(self):
        """S19.5 should disable an event-action."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBB', 1, 3, 42)
        d.dispatch(19, 1, data, None)
        assert 1 in d._s19_enabled_ids
        d.dispatch(19, 5, struct.pack('>H', 1), None)
        assert 1 not in d._s19_enabled_ids


class TestS19EventActionTrigger:
    """Test S19 event-action triggering."""

    def test_trigger_matching_event(self):
        """trigger_event_action should invoke the S8 handler for matching events."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Add rule: severity 3 -> call S8 func_id 52 (OBC reboot)
        data = struct.pack('>HBB', 1, 3, 52)
        d.dispatch(19, 1, data, None)
        # Trigger event with severity 3
        d.trigger_event_action(3)
        # The _handle_s8 should have been called — verify OBDH got called
        engine.subsystems["obdh"].handle_command.assert_called()

    def test_no_trigger_for_wrong_severity(self):
        """Events with wrong severity should not trigger the action."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBB', 1, 3, 52)
        d.dispatch(19, 1, data, None)
        engine.subsystems["obdh"].handle_command.reset_mock()
        # Trigger event with severity 1 (not 3)
        d.trigger_event_action(1)
        # OBDH should not have been called
        engine.subsystems["obdh"].handle_command.assert_not_called()

    def test_disabled_rule_not_triggered(self):
        """Disabled event-action rules should not trigger."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        data = struct.pack('>HBB', 1, 3, 52)
        d.dispatch(19, 1, data, None)
        # Disable the rule
        d.dispatch(19, 5, struct.pack('>H', 1), None)
        engine.subsystems["obdh"].handle_command.reset_mock()
        d.trigger_event_action(3)
        engine.subsystems["obdh"].handle_command.assert_not_called()

    def test_multiple_rules_same_severity(self):
        """Multiple rules for the same severity should all trigger."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Rule 1: severity 3 -> func 52 (OBDH reboot)
        d.dispatch(19, 1, struct.pack('>HBB', 1, 3, 52), None)
        # Rule 2: severity 3 -> func 0 (AOCS set mode)
        d.dispatch(19, 1, struct.pack('>HBB', 2, 3, 0), None)
        engine.subsystems["obdh"].handle_command.reset_mock()
        engine.subsystems["aocs"].handle_command.reset_mock()
        d.trigger_event_action(3)
        # Both subsystems should have been called
        engine.subsystems["obdh"].handle_command.assert_called()
        engine.subsystems["aocs"].handle_command.assert_called()

    def test_trigger_eps_action(self):
        """Event-action targeting EPS func_id range (16-25)."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        # Rule: severity 4 -> func_id 16 (EPS set payload mode)
        d.dispatch(19, 1, struct.pack('>HBB', 1, 4, 16), None)
        d.trigger_event_action(4)
        engine.subsystems["eps"].handle_command.assert_called()


class TestS19Report:
    """Test S19 event-action report generation."""

    def test_s19_report_subtype_8(self):
        """S19.8 should report all event-action definitions."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        d.dispatch(19, 1, struct.pack('>HBB', 1, 3, 42), None)
        d.dispatch(19, 1, struct.pack('>HBB', 2, 4, 10), None)
        responses = d.dispatch(19, 8, b'', None)
        assert len(responses) == 1
        engine.tm_builder._pack_tm.assert_called()

    def test_s19_report_empty(self):
        """S19.8 with no definitions should still return a report."""
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        responses = d.dispatch(19, 8, b'', None)
        assert len(responses) == 1
