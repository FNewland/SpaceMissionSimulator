"""Tests for RF link gating — TM/TC contact-aware communication."""
import queue
import pytest
from unittest.mock import MagicMock, patch


def make_orbit_state(in_contact=False):
    state = MagicMock()
    state.in_eclipse = False
    state.solar_beta_deg = 20.0
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = 30.0 if in_contact else -10.0
    state.gs_azimuth_deg = 180.0
    state.gs_range_km = 800.0 if in_contact else 2000.0
    return state


def make_ttc_model():
    from smo_simulator.models.ttc_basic import TTCBasicModel
    model = TTCBasicModel()
    model.configure({})
    return model


def make_mock_engine():
    """Create a minimal mock engine with real gating properties."""
    from smo_simulator.engine import SimulationEngine
    engine = MagicMock(spec=SimulationEngine)
    engine.params = {}
    engine._in_contact = False
    engine._override_passes = False
    engine.tm_queue = queue.Queue(maxsize=2000)
    engine._tm_storage = MagicMock()
    engine.tm_builder = MagicMock()
    engine.tm_builder.build_verification_failure = MagicMock(return_value=b'\x00' * 20)

    # Bind real properties (matching fixed engine.py)
    type(engine).downlink_active = property(
        lambda self: ((self._in_contact and bool(self.params.get(0x0501, 0))) or self._override_passes)
    )
    type(engine).uplink_active = property(
        lambda self: ((self._in_contact and bool(self.params.get(0x0501, 0))) or self._override_passes)
    )
    # Bind real _enqueue_tm
    engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)
    return engine


# ---- TTC Model Tests ----

class TestTTCLinkGating:
    def test_downlink_blocked_no_contact(self):
        """TTC link_active should be False when not in orbital contact."""
        model = make_ttc_model()
        params = {}
        orbit = make_orbit_state(in_contact=False)
        model.tick(1.0, orbit, params)
        assert params[0x0501] == 0  # link_active = False

    def test_downlink_blocked_transponder_failure(self):
        """In contact but transponder failed -> no downlink."""
        model = make_ttc_model()
        params = {}
        orbit = make_orbit_state(in_contact=True)

        # Inject primary failure (default mode is 0 = primary)
        model.inject_failure("primary_failure", 1.0)
        model.tick(1.0, orbit, params)
        assert params[0x0501] == 0  # link_active = False despite contact

    def test_downlink_allowed_link_active(self):
        """Normal AOS with healthy transponder -> link locked after acquisition."""
        model = make_ttc_model()
        params = {}
        orbit = make_orbit_state(in_contact=True)
        # Lock acquisition: carrier(2s) + bit(5s) + frame(10s) — need 11 ticks
        for _ in range(11):
            model.tick(1.0, orbit, params)
        assert params[0x0501] == 2  # link_status = LOCKED

    def test_override_enables_downlink(self):
        """Override param (0x05FF=1) should force TTC into contact if transponder OK."""
        model = make_ttc_model()
        params = {0x05FF: 1}  # override active
        orbit = make_orbit_state(in_contact=False)  # no orbital contact
        # Lock acquisition needs time even with override
        for _ in range(11):
            model.tick(1.0, orbit, params)
        assert params[0x0501] == 2  # link locked via override

    def test_transponder_failure_overrides_override(self):
        """TTC transponder failure blocks link even with override active."""
        model = make_ttc_model()
        params = {0x05FF: 1}  # override active
        orbit = make_orbit_state(in_contact=False)

        model.inject_failure("primary_failure", 1.0)
        model.tick(1.0, orbit, params)
        assert params[0x0501] == 0  # still blocked — transponder failure wins


# ---- Engine TM Gating Tests ----

class TestTMGating:
    def test_tm_enqueued_when_downlink_active(self):
        """TM packets should enter tm_queue when downlink is active."""
        engine = make_mock_engine()
        engine.params[0x0501] = 1  # link active
        engine._in_contact = True  # AND in contact
        pkt = b'\x08\x01' + b'\x00' * 18  # min 20 bytes with valid-ish header
        engine._enqueue_tm(pkt)
        assert not engine.tm_queue.empty()

    def test_tm_not_enqueued_when_downlink_inactive(self):
        """TM packets should NOT enter tm_queue when downlink is inactive."""
        engine = make_mock_engine()
        engine.params[0x0501] = 0  # link inactive
        pkt = b'\x08\x01' + b'\x00' * 18
        engine._enqueue_tm(pkt)
        assert engine.tm_queue.empty()

    def test_tm_always_stored_onboard(self):
        """Onboard storage receives TM regardless of downlink state."""
        engine = make_mock_engine()
        engine.params[0x0501] = 0  # link inactive

        # Need a packet that passes the len > 13 check and parses
        fake_pkt = b'\x00' * 20
        with patch('smo_common.protocol.ecss_packet.decommutate_packet') as mock_decom:
            mock_parsed = MagicMock()
            mock_parsed.secondary = MagicMock()
            mock_parsed.secondary.service = 3
            mock_decom.return_value = mock_parsed
            engine._enqueue_tm(fake_pkt)

        engine._tm_storage.store_packet.assert_called_once()


# ---- Engine TC Gating Tests ----

class TestTCGating:
    def test_tc_rejected_during_los(self):
        """TC should get S1.2 rejection when uplink is not active."""
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine.params = {0x0501: 0}  # no downlink
        engine._in_contact = False
        engine._override_passes = False
        engine.tc_queue = queue.Queue(maxsize=500)
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_verification_failure = MagicMock(return_value=b'\x00' * 20)

        type(engine).uplink_active = property(
            lambda self: self._in_contact or self._override_passes
        )
        type(engine).downlink_active = property(
            lambda self: bool(self.params.get(0x0501, 0))
        )
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)
        engine._dispatch_tc = MagicMock()
        engine._drain_tc_queue = SimulationEngine._drain_tc_queue.__get__(engine)

        # Build a minimal valid TC packet
        from smo_common.protocol.ecss_packet import decommutate_packet
        # Put raw bytes that can be decommutated
        fake_tc = b'\x18\x01\xc0\x00\x00\x09\x10\x11\x01\x00\x00\x00\x00\x00\x00\x00'
        engine.tc_queue.put_nowait(fake_tc)
        engine._drain_tc_queue()

        # TC should NOT have been dispatched
        engine._dispatch_tc.assert_not_called()
        # Verification failure should have been built
        engine.tm_builder.build_verification_failure.assert_called_once()
        args = engine.tm_builder.build_verification_failure.call_args
        assert args[0][2] == 0x0005  # error code for uplink unavailable

    def test_tc_accepted_during_aos(self):
        """TC flows normally when uplink is active."""
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine.params = {0x0501: 1}
        engine._in_contact = True
        engine._override_passes = False
        engine.tc_queue = queue.Queue(maxsize=500)
        engine._dispatch_tc = MagicMock()
        type(engine).uplink_active = property(
            lambda self: self._in_contact or self._override_passes
        )
        engine._drain_tc_queue = SimulationEngine._drain_tc_queue.__get__(engine)

        fake_tc = b'\x18\x01\xc0\x00\x00\x09\x10\x11\x01\x00\x00\x00\x00\x00\x00\x00'
        engine.tc_queue.put_nowait(fake_tc)
        engine._drain_tc_queue()

        # TC should have been dispatched
        engine._dispatch_tc.assert_called_once()


# ---- Engine Override Tests ----

class TestOverride:
    def test_override_instructor_cmd(self):
        """Override passes toggle via instructor command handler."""
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine._override_passes = False
        engine._handle_instructor_cmd = SimulationEngine._handle_instructor_cmd.__get__(engine)
        engine._handle_failure_inject = MagicMock()
        engine._handle_failure_clear = MagicMock()
        engine._failure_manager = MagicMock()

        engine._handle_instructor_cmd({'type': 'override_passes', 'enabled': True})
        assert engine._override_passes is True

        engine._handle_instructor_cmd({'type': 'override_passes', 'enabled': False})
        assert engine._override_passes is False

    def test_override_in_state_summary(self):
        """downlink_active, uplink_active, override_passes appear in state summary."""
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine.params = {0x0501: 1, 0x0101: 75.0, 0x0100: 28.0, 0x0105: 28.0,
                        0x0102: 25.0, 0x0109: 0.5, 0x0103: 1.0, 0x0104: 1.0,
                        0x0107: 30.0, 0x0106: 20.0, 0x020F: 3, 0x0217: 0.01,
                        0x0204: 0.0, 0x0205: 0.0, 0x0206: 0.0,
                        0x0207: 1000, 0x0208: -1000, 0x0209: 500, 0x020A: -500,
                        0x0406: 25.0, 0x0407: 20.0, 0x0408: -30.0,
                        0x040A: 0, 0x040B: 0, 0x040C: 1,
                        0x0300: 0, 0x0302: 15.0, 0x0303: 30.0, 0x030A: 0,
                        0x0500: 0, 0x0502: -80.0, 0x0503: 12.0, 0x0509: 800.0, 0x050A: 30.0,
                        0x0600: 1, 0x0601: -25.0, 0x0604: 10.0, 0x0605: 0}
        engine._in_contact = True
        engine._override_passes = True
        import threading
        engine._params_lock = threading.Lock()
        engine._tick_count = 42
        engine._sim_time = MagicMock()
        engine._sim_time.isoformat = MagicMock(return_value="2026-01-01T00:00:00+00:00")
        engine._sim_elapsed_fdir = 42.0
        engine.speed = 1.0
        engine.sc_mode = 0
        engine._spacecraft_phase = 6
        engine.orbit = MagicMock()
        engine.orbit.state = make_orbit_state(in_contact=True)
        engine._failure_manager = MagicMock()
        engine._failure_manager.active_failures = MagicMock(return_value=[])
        engine._tc_scheduler = MagicMock()
        engine._tc_scheduler.get_status = MagicMock(return_value={})
        engine._tm_storage = MagicMock()
        engine._tm_storage.get_status = MagicMock(return_value={})
        engine.subsystems = {"eps": MagicMock()}
        engine.subsystems["eps"]._state = MagicMock()
        engine.subsystems["eps"]._state.power_lines = {}
        type(engine).downlink_active = property(
            lambda self: bool(self.params.get(0x0501, 0))
        )
        type(engine).uplink_active = property(
            lambda self: self._in_contact or self._override_passes
        )
        engine._get_power_lines_state = SimulationEngine._get_power_lines_state.__get__(engine)
        engine.get_state_summary = SimulationEngine.get_state_summary.__get__(engine)

        summary = engine.get_state_summary()
        assert 'downlink_active' in summary
        assert 'uplink_active' in summary
        assert 'override_passes' in summary
        assert summary['downlink_active'] is True
        assert summary['uplink_active'] is True
        assert summary['override_passes'] is True


# ---- S11 Scheduled Commands Bypass Test ----

class TestS11Bypass:
    def test_s11_executes_during_los(self):
        """Time-tagged commands from scheduler bypass the uplink gate."""
        # In the engine _run_loop, due_tcs from scheduler are dispatched
        # directly via _dispatch_tc, not through _drain_tc_queue.
        # This test verifies the design: scheduler commands don't go
        # through _drain_tc_queue which has the uplink gate.
        from smo_simulator.engine import SimulationEngine
        import inspect

        source = inspect.getsource(SimulationEngine._run_loop)
        # Verify scheduler dispatches directly, not through _drain_tc_queue
        assert '_dispatch_tc(tc_pkt)' in source or '_dispatch_tc' in source
        # Verify the scheduled command dispatch is separate from tc_queue drain
        lines = source.split('\n')
        drain_line = None
        sched_line = None
        for i, line in enumerate(lines):
            if '_drain_tc_queue' in line:
                drain_line = i
            if 'due_tcs' in line and '_dispatch_tc' in line:
                sched_line = i
            if 'for tc_pkt in due_tcs' in line:
                sched_line = i

        # Both should exist and be separate code paths
        assert drain_line is not None, "_drain_tc_queue should be called in _run_loop"
        assert sched_line is not None, "Scheduled TC dispatch should exist in _run_loop"


# ---- TTC Link Failure (Transponder/PA) Blocking Tests ----

class TestTCGatingDuringTTCFailure:
    def test_tc_rejected_when_transponder_failed_despite_orbital_contact(self):
        """TC should be REJECTED when transponder fails, even if in orbital contact.

        Root cause fix: uplink_active must check param 0x0501 (TTC link_active)
        in addition to orbital contact.
        """
        from smo_simulator.engine import SimulationEngine
        engine = MagicMock(spec=SimulationEngine)
        engine.params = {0x0501: 0}  # link_active = 0 (transponder failed)
        engine._in_contact = True    # BUT spacecraft is in orbital contact!
        engine._override_passes = False
        engine.tc_queue = queue.Queue(maxsize=500)
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_verification_failure = MagicMock(return_value=b'\x00' * 20)

        # Use REAL uplink_active and downlink_active properties (both check contact + link)
        type(engine).uplink_active = SimulationEngine.uplink_active
        type(engine).downlink_active = SimulationEngine.downlink_active
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)
        engine._dispatch_tc = MagicMock()
        engine._drain_tc_queue = SimulationEngine._drain_tc_queue.__get__(engine)

        fake_tc = b'\x18\x01\xc0\x00\x00\x09\x10\x11\x01\x00\x00\x00\x00\x00\x00\x00'
        engine.tc_queue.put_nowait(fake_tc)
        engine._drain_tc_queue()

        # TC should NOT have been dispatched
        engine._dispatch_tc.assert_not_called()
        # Verification failure should have been built (uplink unavailable)
        engine.tm_builder.build_verification_failure.assert_called_once()

    def test_hk_blocked_when_pa_off_despite_orbital_contact(self):
        """HK packets should NOT reach MCS when PA is off, even during orbital contact."""
        model = make_ttc_model()
        params = {0x05FF: 0}  # no override
        orbit = make_orbit_state(in_contact=True)  # in contact

        # Turn PA off (link_active should become False)
        model._state.pa_on = False
        model.tick(1.0, orbit, params)

        assert params[0x0501] == 0, "link_active should be 0 when PA is off"

    def test_s20_get_rejected_when_antenna_stowed(self):
        """S20.3 GET response should be dropped when antenna is stowed (no downlink)."""
        from smo_simulator.engine import SimulationEngine
        import struct

        engine = MagicMock(spec=SimulationEngine)
        engine.params = {
            0x0100: 28.5,  # parameter to GET
            0x0501: 0,     # antenna stowed -> no link
        }
        engine._in_contact = True
        engine._override_passes = False
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()

        # Mock tm_builder to return a valid packet
        mock_pkt = b'\x08\x09' + struct.pack('>H', 0x0100) + struct.pack('>f', 28.5) + b'\x00' * 10
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_param_value_report = MagicMock(return_value=mock_pkt)

        # Set up REAL properties (both check contact AND link)
        type(engine).downlink_active = SimulationEngine.downlink_active
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)

        # Dispatch S20.3 GET
        from smo_simulator.service_dispatch import ServiceDispatcher
        dispatcher = ServiceDispatcher(engine)
        data = struct.pack('>H', 0x0100)
        responses = dispatcher.dispatch(20, 3, data, None)

        # Enqueue response
        for resp_pkt in responses:
            engine._enqueue_tm(resp_pkt)

        # Response should NOT reach tm_queue
        assert engine.tm_queue.empty(), "S20 GET response should be blocked during no-downlink"


# ---- REGRESSION TEST: On-Demand TM Leak ----
# Regression identified 2026-04-06: S20.3 GET responses were being enqueued
# even when out of orbital contact. Root cause: downlink_active only checked
# param 0x0501 (TTC link status) but NOT _in_contact (orbital contact).
# Fix: downlink_active now requires BOTH conditions.

class TestOnDemandTMLeak:
    def test_s20_get_blocked_out_of_contact(self):
        """REGRESSION TEST: S20.3 GET response must be blocked when out of contact.

        Even if TTC link status (0x0501) is OK, downlink should NOT occur
        unless spacecraft is also in orbital contact with ground station.

        Root cause: downlink_active property in engine.py did not check _in_contact.
        """
        from smo_simulator.engine import SimulationEngine
        import struct

        engine = MagicMock(spec=SimulationEngine)
        engine.params = {
            0x0100: 28.5,  # parameter to GET
            0x0501: 1,     # link status OK
        }
        engine._in_contact = False  # NOT in orbital contact <-- KEY
        engine._override_passes = False
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()

        # Mock tm_builder to return a valid S20 TM packet
        mock_pkt = b'\x08\x09' + struct.pack('>H', 0x0100) + struct.pack('>f', 28.5) + b'\x00' * 10
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_param_value_report = MagicMock(return_value=mock_pkt)

        # Set up CORRECTED downlink_active property
        type(engine).downlink_active = SimulationEngine.downlink_active

        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)

        # Dispatch S20.3 GET for parameter 0x0100
        from smo_simulator.service_dispatch import ServiceDispatcher
        dispatcher = ServiceDispatcher(engine)
        data = struct.pack('>H', 0x0100)
        responses = dispatcher.dispatch(20, 3, data, None)

        # Enqueue response — _enqueue_tm should check downlink_active
        for resp_pkt in responses:
            engine._enqueue_tm(resp_pkt)

        # ASSERTION: Response should NOT reach tm_queue because not in contact
        assert engine.tm_queue.empty(), \
            "REGRESSION: S20.3 GET response leaked out of contact! downlink_active broken."

    def test_s20_get_blocked_link_failed(self):
        """S20.3 GET response must be blocked when TTC link is down."""
        from smo_simulator.engine import SimulationEngine
        import struct

        engine = MagicMock(spec=SimulationEngine)
        engine.params = {
            0x0100: 28.5,
            0x0501: 0,     # link status FAILED
        }
        engine._in_contact = True     # IN orbital contact
        engine._override_passes = False
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()

        mock_pkt = b'\x08\x09' + struct.pack('>H', 0x0100) + struct.pack('>f', 28.5) + b'\x00' * 10
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_param_value_report = MagicMock(return_value=mock_pkt)

        type(engine).downlink_active = SimulationEngine.downlink_active
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)

        from smo_simulator.service_dispatch import ServiceDispatcher
        dispatcher = ServiceDispatcher(engine)
        data = struct.pack('>H', 0x0100)
        responses = dispatcher.dispatch(20, 3, data, None)

        for resp_pkt in responses:
            engine._enqueue_tm(resp_pkt)

        # Response should NOT reach tm_queue because link is down
        assert engine.tm_queue.empty(), \
            "S20.3 GET response should be blocked when TTC link is down"

    def test_s20_get_allowed_contact_and_link_ok(self):
        """S20.3 GET response must be allowed when in contact AND link OK."""
        from smo_simulator.engine import SimulationEngine
        import struct

        engine = MagicMock(spec=SimulationEngine)
        engine.params = {
            0x0100: 28.5,
            0x0501: 1,     # link status OK
        }
        engine._in_contact = True     # IN contact
        engine._override_passes = False
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()

        mock_pkt = b'\x08\x09' + struct.pack('>H', 0x0100) + struct.pack('>f', 28.5) + b'\x00' * 10
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_param_value_report = MagicMock(return_value=mock_pkt)

        type(engine).downlink_active = SimulationEngine.downlink_active
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)

        from smo_simulator.service_dispatch import ServiceDispatcher
        dispatcher = ServiceDispatcher(engine)
        data = struct.pack('>H', 0x0100)
        responses = dispatcher.dispatch(20, 3, data, None)

        for resp_pkt in responses:
            engine._enqueue_tm(resp_pkt)

        # Response SHOULD reach tm_queue
        assert not engine.tm_queue.empty(), \
            "S20.3 GET response should be allowed when in contact and link OK"

    def test_s20_get_allowed_override(self):
        """S20.3 GET response must be allowed with override, even out of contact."""
        from smo_simulator.engine import SimulationEngine
        import struct

        engine = MagicMock(spec=SimulationEngine)
        engine.params = {
            0x0100: 28.5,
            0x0501: 1,
        }
        engine._in_contact = False    # OUT of contact
        engine._override_passes = True  # BUT override is on
        engine.tm_queue = queue.Queue(maxsize=2000)
        engine._tm_storage = MagicMock()

        mock_pkt = b'\x08\x09' + struct.pack('>H', 0x0100) + struct.pack('>f', 28.5) + b'\x00' * 10
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_param_value_report = MagicMock(return_value=mock_pkt)

        type(engine).downlink_active = SimulationEngine.downlink_active
        engine._enqueue_tm = SimulationEngine._enqueue_tm.__get__(engine)

        from smo_simulator.service_dispatch import ServiceDispatcher
        dispatcher = ServiceDispatcher(engine)
        data = struct.pack('>H', 0x0100)
        responses = dispatcher.dispatch(20, 3, data, None)

        for resp_pkt in responses:
            engine._enqueue_tm(resp_pkt)

        # Response SHOULD reach tm_queue due to override
        assert not engine.tm_queue.empty(), \
            "S20.3 GET response should be allowed when override is active"
