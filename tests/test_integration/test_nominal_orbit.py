"""Integration tests for nominal orbit operations.

Covers:
  - Full orbit cycle (sunlight -> eclipse -> sunlight)
  - All subsystems ticking together for extended periods
  - HK generation over multiple orbit periods
  - TM storage circular buffer behavior
  - Subsystem cross-coupling (EPS-TCS battery temp)
"""
import pytest
from unittest.mock import MagicMock

from smo_simulator.models.eps_basic import EPSBasicModel
from smo_simulator.models.obdh_basic import OBDHBasicModel
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.aocs_basic import AOCSBasicModel
from smo_simulator.models.tcs_basic import TCSBasicModel
from smo_simulator.models.payload_basic import PayloadBasicModel
from smo_simulator.tm_storage import OnboardTMStorage, CIRCULAR_STORES


def make_orbit_state(in_eclipse=False, in_contact=False, beta=20.0,
                     alt_km=500.0, lat_deg=45.0):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = beta
    state.lat_deg = lat_deg
    state.lon_deg = 10.0
    state.alt_km = alt_km
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = -10.0 if not in_contact else 30.0
    state.gs_azimuth_deg = 90.0
    state.gs_range_km = 2000.0 if not in_contact else 1000.0
    return state


class TestOrbitCycle:
    """Test a full orbit cycle: sunlight -> eclipse -> sunlight."""

    def _make_all_subsystems(self):
        """Create and configure all subsystem models."""
        models = {
            "eps": EPSBasicModel(),
            "obdh": OBDHBasicModel(),
            "ttc": TTCBasicModel(),
            "aocs": AOCSBasicModel(),
            "tcs": TCSBasicModel(),
            "payload": PayloadBasicModel(),
        }
        for model in models.values():
            model.configure({})
        return models

    def test_eclipse_entry_reduces_power_gen(self):
        """Entering eclipse should reduce EPS power generation to near zero."""
        eps = EPSBasicModel()
        eps.configure({})
        params = {}

        # Sunlight tick
        orbit_sun = make_orbit_state(in_eclipse=False)
        eps.tick(1.0, orbit_sun, params)
        gen_sunlight = eps._state.power_gen_w

        # Eclipse tick
        orbit_ecl = make_orbit_state(in_eclipse=True)
        eps.tick(1.0, orbit_ecl, params)
        gen_eclipse = eps._state.power_gen_w

        assert gen_eclipse < gen_sunlight
        assert gen_eclipse == pytest.approx(0.0, abs=0.1)

    def test_eclipse_entry_drains_battery(self):
        """Eclipse should drain battery (SoC decreases)."""
        eps = EPSBasicModel()
        eps.configure({})
        params = {}
        orbit_ecl = make_orbit_state(in_eclipse=True)
        initial_soc = eps._state.bat_soc_pct
        for _ in range(100):
            eps.tick(1.0, orbit_ecl, params)
        assert eps._state.bat_soc_pct < initial_soc

    def test_sunlight_charges_battery(self):
        """Sunlight should charge battery (SoC increases or net power positive)."""
        eps = EPSBasicModel()
        eps.configure({})
        eps._state.bat_soc_pct = 50.0
        params = {}
        orbit_sun = make_orbit_state(in_eclipse=False)
        for _ in range(100):
            eps.tick(1.0, orbit_sun, params)
        assert eps._state.bat_soc_pct > 50.0 or eps._state.power_gen_w > eps._state.power_cons_w

    def test_full_orbit_simulation(self):
        """Simulate a full orbit: 60 min sunlight + 35 min eclipse."""
        eps = EPSBasicModel()
        eps.configure({})
        params = {}

        initial_soc = eps._state.bat_soc_pct

        # 60 min sunlight (3600s)
        orbit_sun = make_orbit_state(in_eclipse=False)
        for _ in range(60):
            eps.tick(60.0, orbit_sun, params)

        soc_after_sun = eps._state.bat_soc_pct

        # 35 min eclipse (2100s)
        orbit_ecl = make_orbit_state(in_eclipse=True)
        for _ in range(35):
            eps.tick(60.0, orbit_ecl, params)

        soc_after_ecl = eps._state.bat_soc_pct

        # SoC should have increased in sunlight and decreased in eclipse
        assert soc_after_sun > initial_soc or soc_after_sun > 70
        assert soc_after_ecl < soc_after_sun


class TestAllSubsystemsTicking:
    """Test all subsystems ticking together."""

    def _make_all_subsystems(self):
        models = {
            "eps": EPSBasicModel(),
            "obdh": OBDHBasicModel(),
            "ttc": TTCBasicModel(),
            "aocs": AOCSBasicModel(),
            "tcs": TCSBasicModel(),
            "payload": PayloadBasicModel(),
        }
        for model in models.values():
            model.configure({})
        return models

    def test_all_subsystems_tick_without_error(self):
        """All subsystems should tick together without exceptions."""
        models = self._make_all_subsystems()
        params = {}
        orbit = make_orbit_state()

        for _ in range(100):
            for name, model in models.items():
                model.tick(1.0, orbit, params)

    def test_all_key_params_present(self):
        """After ticking, all key parameters should be in shared params."""
        models = self._make_all_subsystems()
        params = {}
        orbit = make_orbit_state()

        for _ in range(5):
            for model in models.values():
                model.tick(1.0, orbit, params)

        # EPS
        assert 0x0101 in params  # bat_soc
        assert 0x0100 in params  # bat_voltage
        assert 0x0105 in params  # bus_voltage
        assert 0x0107 in params  # power_gen
        assert 0x0106 in params  # power_cons

        # OBDH
        assert 0x0302 in params  # cpu_load
        assert 0x0300 in params  # obc_mode
        assert 0x030A in params  # reboot_count

        # TTC
        assert 0x0501 in params  # link_status
        assert 0x0502 in params  # rssi

        # AOCS
        assert 0x020F in params  # aocs_mode
        assert 0x0217 in params  # att_error

        # TCS
        assert 0x0407 in params  # temp_battery
        assert 0x0406 in params  # temp_obc
        assert 0x0408 in params  # temp_fpa

        # Payload
        assert 0x0600 in params  # pli_mode
        assert 0x0601 in params  # fpa_temp

    def test_extended_simulation_stability(self):
        """200 ticks should complete without crash or invalid values."""
        models = self._make_all_subsystems()
        params = {}
        orbit = make_orbit_state()

        for _ in range(200):
            for model in models.values():
                model.tick(1.0, orbit, params)

        # SoC should remain in valid range
        soc = params.get(0x0101, 0)
        assert 0 <= soc <= 100

        # CPU load should be in valid range
        cpu = params.get(0x0302, 0)
        assert 0 <= cpu <= 100


class TestEPSTCSCrossCoupling:
    """Test EPS-TCS cross-coupling for battery temperature."""

    def test_battery_temp_coupling(self):
        """EPS should receive battery temp from TCS."""
        eps = EPSBasicModel()
        eps.configure({})
        tcs = TCSBasicModel()
        tcs.configure({})

        params = {}
        orbit = make_orbit_state()

        tcs.tick(1.0, orbit, params)
        bat_temp = tcs.get_battery_temp()
        assert isinstance(bat_temp, float)

        eps.set_bat_ambient_temp(bat_temp)
        # Verify the ambient temp was set
        assert eps._bat_temp_env == bat_temp


class TestTMStorageCircularBuffer:
    """Test TM storage circular buffer behavior."""

    def test_store_1_is_circular(self):
        """Store 1 (HK) should use circular buffer mode."""
        assert 1 in CIRCULAR_STORES

    def test_store_2_is_linear(self):
        """Store 2 (Events) should use stop-when-full mode."""
        assert 2 not in CIRCULAR_STORES

    def test_circular_buffer_overwrites_oldest(self):
        """Circular buffer should overwrite oldest when full."""
        storage = OnboardTMStorage({
            1: {'name': 'Test', 'capacity': 3, 'circular': True},
        })
        # Store 4 packets (exceeds capacity of 3)
        for i in range(4):
            result = storage.store_packet_direct(1, bytes([i]))
            assert result is True  # Circular always succeeds
        # Should have exactly 3 packets
        packets = storage.start_dump(1)
        assert len(packets) == 3
        # Oldest (0) should be gone, remaining should be 1, 2, 3
        assert packets[0] == bytes([1])
        assert packets[2] == bytes([3])

    def test_linear_buffer_stops_when_full(self):
        """Linear buffer should reject packets when full."""
        storage = OnboardTMStorage({
            2: {'name': 'Test', 'capacity': 3, 'circular': False},
        })
        for i in range(3):
            result = storage.store_packet_direct(2, bytes([i]))
            assert result is True
        # 4th packet should be rejected
        result = storage.store_packet_direct(2, bytes([99]))
        assert result is False
        # Should still have exactly 3 packets
        packets = storage.start_dump(2)
        assert len(packets) == 3

    def test_overflow_flag_set(self):
        """Overflow flag should be set when buffer is full."""
        storage = OnboardTMStorage({
            1: {'name': 'Test', 'capacity': 2, 'circular': True},
        })
        storage.store_packet_direct(1, b'\x01')
        storage.store_packet_direct(1, b'\x02')
        assert storage.is_overflow(1) is False
        storage.store_packet_direct(1, b'\x03')
        assert storage.is_overflow(1) is True

    def test_delete_store_clears_all(self):
        """delete_store should clear all packets and reset overflow."""
        storage = OnboardTMStorage()
        for i in range(10):
            storage.store_packet_direct(1, bytes([i]))
        storage.delete_store(1)
        assert len(storage.start_dump(1)) == 0
        assert storage.is_overflow(1) is False

    def test_enable_disable_store(self):
        """Disabled store should reject packets."""
        storage = OnboardTMStorage()
        storage.disable_store(1)
        result = storage.store_packet_direct(1, b'\x01')
        assert result is False
        storage.enable_store(1)
        result = storage.store_packet_direct(1, b'\x01')
        assert result is True


class TestHKOverMultipleOrbits:
    """Test HK generation patterns over multiple orbits."""

    def test_obdh_uptime_increments(self):
        """OBDH uptime should increment with each tick."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        params = {}
        orbit = make_orbit_state()
        obdh.tick(1.0, orbit, params)
        uptime_1 = obdh._state.uptime_s
        obdh.tick(1.0, orbit, params)
        uptime_2 = obdh._state.uptime_s
        assert uptime_2 > uptime_1

    def test_obdh_time_cuc_advances(self):
        """OBDH CUC time should advance with ticks."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        params = {}
        orbit = make_orbit_state()
        cuc_start = obdh._state.obc_time_cuc
        for _ in range(10):
            obdh.tick(1.0, orbit, params)
        assert obdh._state.obc_time_cuc > cuc_start

    def test_tm_packet_counter_increments(self):
        """TM packet counter should increment when record_tm_packet called."""
        obdh = OBDHBasicModel()
        obdh.configure({})
        initial = obdh._state.tm_pkt_count
        obdh.record_tm_packet()
        obdh.record_tm_packet()
        assert obdh._state.tm_pkt_count == initial + 2

    def test_storage_status_report(self):
        """TM storage status report should include all stores."""
        storage = OnboardTMStorage()
        status = storage.get_status()
        assert len(status) == 4  # HK, Event, Science, Alarm stores
        for s in status:
            assert "id" in s
            assert "count" in s
            assert "capacity" in s
            assert "enabled" in s
            assert "circular" in s
            assert "fill_pct" in s
