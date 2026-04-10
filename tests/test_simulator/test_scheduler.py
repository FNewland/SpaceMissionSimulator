"""Tests for TC Scheduler and TM Storage."""
import pytest


class TestTCScheduler:
    """Test TCScheduler (S11)."""

    def test_insert_and_list(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        cmd_id = sched.insert(1000, b'\x01\x02')
        assert cmd_id == 1
        cmds = sched.list_commands()
        assert len(cmds) == 1
        assert cmds[0]['exec_time'] == 1000

    def test_tick_executes_due_commands(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        sched.insert(100, b'\xAA')
        sched.insert(200, b'\xBB')
        sched.insert(300, b'\xCC')

        # Tick at time 150 — only first command should fire
        due = sched.tick(150)
        assert len(due) == 1
        assert due[0] == b'\xAA'
        assert len(sched.list_commands()) == 2

    def test_tick_executes_multiple_due(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        sched.insert(100, b'\xAA')
        sched.insert(100, b'\xBB')
        due = sched.tick(100)
        assert len(due) == 2

    def test_tick_disabled(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        sched.insert(100, b'\xAA')
        sched.disable_schedule()
        due = sched.tick(200)
        assert len(due) == 0
        assert len(sched.list_commands()) == 1

    def test_enable_disable(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        assert sched.enabled
        sched.disable_schedule()
        assert not sched.enabled
        sched.enable_schedule()
        assert sched.enabled

    def test_delete_by_id(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        id1 = sched.insert(100, b'\xAA')
        id2 = sched.insert(200, b'\xBB')
        assert sched.delete(id1)
        assert len(sched.list_commands()) == 1
        assert sched.list_commands()[0]['id'] == id2

    def test_delete_nonexistent(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        assert not sched.delete(999)

    def test_delete_all(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        sched.insert(100, b'\xAA')
        sched.insert(200, b'\xBB')
        sched.delete_all()
        assert len(sched.list_commands()) == 0

    def test_get_status(self):
        from smo_simulator.tc_scheduler import TCScheduler
        sched = TCScheduler()
        sched.insert(100, b'\xAA')
        status = sched.get_status()
        assert status['enabled'] is True
        assert status['count'] == 1


class TestOnboardTMStorage:
    """Test OnboardTMStorage (S15)."""

    def test_store_and_dump(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.store_packet(3, b'\x01\x02')  # HK -> store 1
        store.store_packet(3, b'\x03\x04')
        packets = store.start_dump(1)
        assert len(packets) == 2

    def test_store_events(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.store_packet(5, b'\xAA')  # Event -> store 2
        packets = store.start_dump(2)
        assert len(packets) == 1

    def test_store_science(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.store_packet(99, b'\xBB')  # Unknown service -> science store 3
        packets = store.start_dump(3)
        assert len(packets) == 1

    def test_enable_disable(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.disable_store(1)
        store.store_packet(3, b'\x01')
        packets = store.start_dump(1)
        assert len(packets) == 0  # store was disabled

        store.enable_store(1)
        store.store_packet(3, b'\x02')
        packets = store.start_dump(1)
        assert len(packets) == 1

    def test_delete_store(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.store_packet(3, b'\x01')
        store.store_packet(3, b'\x02')
        store.delete_store(1)
        packets = store.start_dump(1)
        assert len(packets) == 0

    def test_get_status(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        store.store_packet(3, b'\x01')
        status = store.get_status()
        assert len(status) == 4  # HK, Event, Science, Alarm stores
        hk_status = status[0]
        assert hk_status['id'] == 1
        assert hk_status['count'] == 1
        assert hk_status['capacity'] == 18000  # sized for 90 min of HK @ ~1.6 pkts/s
        assert hk_status['enabled'] is True

    def test_store_capacity_limit(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage({1: {'name': 'test', 'capacity': 3}})
        for i in range(5):
            store.store_packet(3, bytes([i]))
        packets = store.start_dump(1)
        assert len(packets) == 3  # Stop-when-full: only first 3 stored

    def test_dump_nonexistent_store(self):
        from smo_simulator.tm_storage import OnboardTMStorage
        store = OnboardTMStorage()
        packets = store.start_dump(99)
        assert len(packets) == 0


class TestEPSPowerLines:
    """Test EPS power line switching."""

    def _make_eps(self):
        from smo_simulator.models.eps_basic import EPSBasicModel
        model = EPSBasicModel()
        model.configure({"battery": {"capacity_wh": 120.0}})
        return model

    def test_default_power_lines(self):
        model = self._make_eps()
        lines = model._state.power_lines
        # Non-switchable infrastructure starts ON
        assert lines['obc'] is True
        assert lines['ttc_rx'] is True
        # All switchable lines start OFF (LEOP convention)
        assert lines['payload'] is False
        assert lines['aocs_wheels'] is False
        assert lines['ttc_tx'] is False
        assert lines['htr_bat'] is False

    def test_switch_payload_on(self):
        model = self._make_eps()
        result = model.handle_command({"command": "power_line_on", "line_index": 3})
        assert result['success']
        assert model._state.power_lines['payload'] is True

    def test_switch_payload_off(self):
        model = self._make_eps()
        model._state.power_lines['payload'] = True
        model._state.payload_mode = 2
        result = model.handle_command({"command": "power_line_off", "line_index": 3})
        assert result['success']
        assert model._state.power_lines['payload'] is False
        # Side effect: payload_mode should be set to 0
        assert model._state.payload_mode == 0

    def test_non_switchable_rejected(self):
        model = self._make_eps()
        # OBC (index 0) is non-switchable
        result = model.handle_command({"command": "power_line_off", "line_index": 0})
        assert not result['success']
        assert result['error_code'] == 0x0006

    def test_unknown_line_rejected(self):
        model = self._make_eps()
        result = model.handle_command({"command": "power_line_on", "line_index": 99})
        assert not result['success']

    def test_power_consumption_changes(self):
        """Toggling power lines should affect power consumption."""
        from unittest.mock import MagicMock
        model = self._make_eps()
        orbit = MagicMock()
        orbit.in_eclipse = False
        orbit.solar_beta_deg = 20.0
        params = {}

        # Turn AOCS wheels ON first (default is OFF in LEOP convention)
        model.handle_command({"command": "power_line_on", "line_index": 7})
        model.tick(1.0, orbit, params)
        cons_wheels_on = model._state.power_cons_w

        # Now turn AOCS wheels off
        model.handle_command({"command": "power_line_off", "line_index": 7})
        model.tick(1.0, orbit, params)
        cons_no_wheels = model._state.power_cons_w

        # Power consumption should decrease by ~12W
        assert cons_no_wheels < cons_wheels_on - 5
