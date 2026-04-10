"""Tests for TC dispatch and verification flow."""
import struct
import pytest
from unittest.mock import MagicMock, patch


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


class TestCheckTcAcceptance:
    """Test _check_tc_acceptance validation."""

    def setup_method(self):
        from smo_simulator.engine import SimulationEngine
        # We can't fully instantiate an engine without configs,
        # so we'll test the logic directly
        pass

    def test_unknown_service_rejected(self):
        """Services not in the known set should be rejected."""
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        accepted, code = engine._check_tc_acceptance(99, 1, b'')
        assert not accepted
        assert code == 0x0001

    def test_known_service_accepted(self):
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        accepted, code = engine._check_tc_acceptance(17, 1, b'')
        assert accepted
        assert code == 0

    def test_invalid_subtype_rejected(self):
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        accepted, code = engine._check_tc_acceptance(3, 99, b'')
        assert not accepted
        assert code == 0x0002

    def test_s8_missing_data_rejected(self):
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        accepted, code = engine._check_tc_acceptance(8, 1, b'')
        assert not accepted
        assert code == 0x0003

    def test_s8_with_data_accepted(self):
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._check_tc_acceptance = SimulationEngine._check_tc_acceptance.__get__(engine)

        accepted, code = engine._check_tc_acceptance(8, 1, b'\x00')
        assert accepted


class TestServiceDispatcher:
    """Test service dispatcher routing."""

    def test_s17_connection_test(self):
        """S17.1 should return a connection test report."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        responses = d.dispatch(17, 1, b'', None)
        assert len(responses) == 1
        engine.tm_builder.build_connection_test_report.assert_called_once()

    def test_s9_time_report(self):
        """S9.2 should return a time report."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        responses = d.dispatch(9, 2, b'', None)
        assert len(responses) == 1
        engine.tm_builder.build_time_report.assert_called_once()

    def test_s20_set_param(self):
        """S20.1 should set a parameter value."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        param_id = 0x0100
        value = 42.0
        data = struct.pack('>Hf', param_id, value)
        d = ServiceDispatcher(engine)
        d.dispatch(20, 1, data, None)
        assert engine.params[param_id] == pytest.approx(value, abs=0.01)

    def test_s20_get_param(self):
        """S20.3 should request a parameter value."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        engine.params[0x0100] = 28.5
        data = struct.pack('>H', 0x0100)
        d = ServiceDispatcher(engine)
        responses = d.dispatch(20, 3, data, None)
        assert len(responses) == 1

    def test_s3_enable_hk(self):
        """S3.3 should enable periodic HK."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        data = struct.pack('>H', 1)
        d = ServiceDispatcher(engine)
        d.dispatch(3, 3, data, None)
        engine.set_hk_enabled.assert_called_with(1, True)

    def test_s3_disable_hk(self):
        """S3.4 should disable periodic HK."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        data = struct.pack('>H', 2)
        d = ServiceDispatcher(engine)
        d.dispatch(3, 4, data, None)
        engine.set_hk_enabled.assert_called_with(2, False)

    def test_s8_aocs_command(self):
        """S8.1 func_id=0 should route to AOCS set_mode."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        data = bytes([0, 3])  # func_id=0 (AOCS set_mode), mode=3
        d = ServiceDispatcher(engine)
        d.dispatch(8, 1, data, None)
        engine.subsystems["aocs"].handle_command.assert_called_once()

    def test_unknown_service(self):
        """Unknown services should return empty response list."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        d = ServiceDispatcher(engine)
        responses = d.dispatch(99, 1, b'', None)
        assert responses == []

    def test_power_state_check_no_eps(self):
        """Power check without EPS should allow all commands."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        del engine.subsystems["eps"]
        d = ServiceDispatcher(engine)
        # New signature: (service, subtype, data) — bytes[0] is func_id
        allowed, reason = d.check_power_state(8, 1, bytes([20]))
        assert allowed

    def test_s11_schedule_insert(self):
        """S11.4 should insert a time-tagged command."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        engine._tc_scheduler.insert.return_value = 1
        exec_time = 5000
        inner_tc = b'\x01\x02\x03'
        data = struct.pack('>I', exec_time) + inner_tc
        d = ServiceDispatcher(engine)
        responses = d.dispatch(11, 4, data, None)
        engine._tc_scheduler.insert.assert_called_once_with(exec_time, inner_tc)
        assert len(responses) == 1

    def test_s15_store_status(self):
        """S15.13 should request store status."""
        from smo_simulator.service_dispatch import ServiceDispatcher
        engine = make_mock_engine()
        engine._tm_storage.get_status.return_value = [
            {'id': 1, 'count': 10, 'capacity': 5000, 'enabled': True}
        ]
        d = ServiceDispatcher(engine)
        responses = d.dispatch(15, 13, b'', None)
        assert len(responses) == 1
