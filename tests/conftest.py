"""Shared test fixtures."""
import sys
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add package source directories to path for testing
for pkg in ["smo-common", "smo-simulator", "smo-gateway", "smo-mcs", "smo-planner"]:
    src = Path(__file__).parent.parent / "packages" / pkg / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


CONFIG_DIR = str(Path(__file__).parent.parent / "configs" / "eosat1")


@pytest.fixture
def config_dir():
    """Return path to eosat1 config directory."""
    return CONFIG_DIR


@pytest.fixture
def mock_engine():
    """Create a mock engine suitable for ServiceDispatcher testing."""
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
    engine._tc_scheduler = MagicMock()
    engine._tm_storage = MagicMock()
    engine.event_types_enabled = set(range(256))

    # Setup eps subsystem mock for power gating tests
    eps_mock = engine.subsystems["eps"]
    eps_mock._state = MagicMock()
    eps_mock._state.power_lines = {}
    eps_mock.handle_command = MagicMock(return_value={"success": True})

    return engine


@pytest.fixture
def service_dispatcher(mock_engine):
    """Create a ServiceDispatcher with a mock engine."""
    from smo_simulator.service_dispatch import ServiceDispatcher
    return ServiceDispatcher(mock_engine)
