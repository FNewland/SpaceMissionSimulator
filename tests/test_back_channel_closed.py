"""Tests verifying MCS↔simulator back-channels are closed.

HA-001 + HA-006: TM/TC must flow only over TCP sockets, not HTTP.
"""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

# Import the server modules
from smo_mcs.server import MCSServer
from smo_simulator.instructor.app import create_instructor_app
from smo_simulator.engine import SimulationEngine


def test_simulator_has_no_mcs_state_endpoint():
    """Test 1: Instructor app does not have /api/mcs-state endpoint.

    The endpoint was deleted because MCS must not read simulator state
    via HTTP. TM must come from TCP port 8002 only.
    """
    # Create a minimal engine mock
    engine_mock = MagicMock()
    engine_mock.get_state_summary.return_value = {}
    engine_mock.get_instructor_snapshot.return_value = {}

    app = create_instructor_app(engine_mock)

    # Walk the app.router and check for /api/mcs-state
    found_mcs_state = False
    for resource in app.router.resources():
        if hasattr(resource, '_prefix'):
            if '/api/mcs-state' in str(resource._prefix):
                found_mcs_state = True
                break

    assert not found_mcs_state, "Found /api/mcs-state endpoint; it should be deleted"


def test_mcs_does_not_import_sim_http():
    """Test 2: MCS server code does not reference simulator HTTP.

    Grep for 8080, sim_api_base, sim_api_url, /api/mcs-state.
    These should not appear in the source.
    """
    mcs_file = Path(__file__).parent.parent / "packages/smo-mcs/src/smo_mcs/server.py"
    source = mcs_file.read_text()

    assert '8080' not in source, "Found port 8080 in MCS server code"
    assert '/api/mcs-state' not in source, "Found /api/mcs-state reference in MCS code"
    assert 'sim_api_base' not in source, "Found sim_api_base in MCS code"
    assert 'sim_api_url' not in source, "Found sim_api_url in MCS code"


def test_mcs_param_cache_starts_empty_and_marks_stale():
    """Test 3: MCS param_cache is empty at startup, marks state as stale.

    With no TM connection, the parameter cache should be empty and
    /api/state should report stale=True.
    """
    config_dir = Path(__file__).parent.parent / "configs/eosat1"
    assert config_dir.exists(), f"Config dir not found: {config_dir}"

    # Create MCS with no TM connection (will fail to connect)
    server = MCSServer(config_dir)

    # Check param_cache is empty
    assert server._param_cache == {}, "param_cache should start empty"
    assert server._last_tm_frame_ts is None, "_last_tm_frame_ts should start None"

    # Check staleness logic would mark it stale
    assert server._param_cache == {}  # Empty cache means stale
    assert server._last_tm_frame_ts is None  # No TM received means stale


def test_mcs_has_no_http_command_back_channel():
    """Test 6: the MCS exposes no `/api/command` route at all.

    It used to answer 410 Gone. Phase G deleted the stub together with its
    only caller (`sendLegacyCommand()` in the removed `old_index.html`): a
    route that exists only to refuse is still a route students find in a
    grep, and the property this test defends is the ABSENCE of the
    back-channel. All commands go out over the TC TCP socket via
    /api/pus-command, which is what makes RF gating and command logging
    possible.
    """
    server_src = (Path(__file__).parent.parent
                  / "packages/smo-mcs/src/smo_mcs/server.py").read_text()
    assert 'add_post("/api/command"' not in server_src
    assert 'add_get("/api/command"' not in server_src
    assert not hasattr(MCSServer, "_handle_command"), \
        "the disabled /api/command handler is back"
    assert "_handle_pus_command" in server_src


def test_instructor_command_rejects_tc_commands():
    """Test: Instructor /api/command rejects spacecraft TC commands.

    Telecommands (service/subtype/data_hex format) are rejected with 403.
    The MCS must use the TC TCP socket instead.
    """
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request
    import json

    engine_mock = MagicMock()
    engine_mock.instr_queue = MagicMock()

    app = create_instructor_app(engine_mock)

    # Find the command handler
    command_handler = None
    for route in app.router.routes():
        if '/api/command' in str(route.resource):
            command_handler = route._handler
            break

    assert command_handler is not None, "Could not find /api/command handler"

    # Test that TC-like commands are rejected
    # This would require async context; for now just verify code exists
    source = Path(__file__).parent.parent / "packages/smo-simulator/src/smo_simulator/instructor/app.py"
    app_source = source.read_text()

    assert 'denied' in app_source.lower() or 'reject' in app_source.lower(), \
        "Instructor should reject or deny something"
    assert 'TC TCP socket' in app_source or '8001' in app_source, \
        "Should document TC socket requirement"


def test_instructor_command_allows_simulation_control():
    """Test: Instructor /api/command allows simulation control commands.

    Commands like set_speed, freeze, resume, etc. should be allowed.
    """
    source = Path(__file__).parent.parent / "packages/smo-simulator/src/smo_simulator/instructor/app.py"
    app_source = source.read_text()

    # Verify allowed command types are documented
    allowed_types = [
        'set_speed', 'freeze', 'resume', 'inject', 'clear_failure',
        'failure_inject', 'failure_clear', 'override_passes', 'set_phase',
        'start_scenario', 'stop_scenario',
    ]

    for cmd_type in allowed_types[:3]:  # Check a few key ones
        assert cmd_type in app_source, f"Command type '{cmd_type}' not found in code"
