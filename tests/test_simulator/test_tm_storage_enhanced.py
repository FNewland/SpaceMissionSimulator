"""Tests for enhanced TM storage features: overflow flag, alarm buffer,
timestamps, store_alarm, store_packet_direct, stop-when-full behaviour."""
import pytest

from smo_simulator.tm_storage import OnboardTMStorage


class TestTMStorageEnhanced:
    """Test enhanced OnboardTMStorage features (S15)."""

    def test_alarm_store_exists(self):
        """Default configuration includes 4 stores, with store 4 being Alarm."""
        storage = OnboardTMStorage()
        status = storage.get_status()
        assert len(status) == 4
        alarm_entry = [s for s in status if s['id'] == 4]
        assert len(alarm_entry) == 1
        assert 'Alarm' in alarm_entry[0]['name']

    def test_store_alarm_convenience(self):
        """store_alarm() routes packets to the alarm store (id=4)."""
        storage = OnboardTMStorage()
        result = storage.store_alarm(b'\xAA')
        assert result is True
        status = storage.get_status()
        alarm_status = [s for s in status if s['id'] == 4][0]
        assert alarm_status['count'] == 1

    def test_store_packet_direct(self):
        """store_packet_direct() inserts directly into the specified store."""
        storage = OnboardTMStorage()
        result = storage.store_packet_direct(1, b'\xBB')
        assert result is True
        status = storage.get_status()
        hk_status = [s for s in status if s['id'] == 1][0]
        assert hk_status['count'] == 1

    def test_overflow_flag_set_when_full(self):
        """Overflow flag is set when a store reaches capacity."""
        storage = OnboardTMStorage({1: {'name': 'test', 'capacity': 2}})
        storage.store_packet_direct(1, b'\x01')
        storage.store_packet_direct(1, b'\x02')
        storage.store_packet_direct(1, b'\x03')  # Should be rejected
        status = storage.get_status()
        store_status = [s for s in status if s['id'] == 1][0]
        assert store_status['overflow'] is True
        assert store_status['count'] == 2

    def test_overflow_flag_cleared_on_delete(self):
        """delete_store() clears the overflow flag."""
        storage = OnboardTMStorage({1: {'name': 'test', 'capacity': 2}})
        storage.store_packet_direct(1, b'\x01')
        storage.store_packet_direct(1, b'\x02')
        storage.store_packet_direct(1, b'\x03')  # Triggers overflow
        assert storage.is_overflow(1) is True

        storage.delete_store(1)
        assert storage.is_overflow(1) is False

    def test_store_returns_false_when_full(self):
        """store_packet_direct() returns False for linear stores once full."""
        # Use store 2 (Event_Store) which is linear (stop-when-full)
        storage = OnboardTMStorage({2: {'name': 'test', 'capacity': 2, 'circular': False}})
        assert storage.store_packet_direct(2, b'\x01') is True
        assert storage.store_packet_direct(2, b'\x02') is True
        assert storage.store_packet_direct(2, b'\x03') is False

    def test_circular_buffer_overwrites_oldest(self):
        """Circular store (HK) overwrites oldest packet when full."""
        storage = OnboardTMStorage({1: {'name': 'test', 'capacity': 3, 'circular': True}})
        packets = [bytes([i]) for i in range(1, 6)]  # b'\x01' .. b'\x05'
        for pkt in packets:
            assert storage.store_packet_direct(1, pkt) is True
        dumped = storage.start_dump(1)
        assert len(dumped) == 3
        # Oldest two were overwritten; remaining are 3, 4, 5
        assert dumped[0] == b'\x03'
        assert dumped[1] == b'\x04'
        assert dumped[2] == b'\x05'

    def test_timestamps_tracked(self):
        """Stored packets track oldest and newest timestamps."""
        storage = OnboardTMStorage()
        storage.store_packet_direct(1, b'\x01', timestamp=1000.0)
        status = storage.get_status()
        hk_status = [s for s in status if s['id'] == 1][0]
        assert hk_status['oldest_ts'] is not None
        assert hk_status['newest_ts'] is not None
        assert hk_status['oldest_ts'] == 1000.0
        assert hk_status['newest_ts'] == 1000.0

    def test_stop_when_full_preserves_first_packets(self):
        """Stop-when-full (linear) keeps the earliest packets and rejects later ones."""
        # Use a linear (non-circular) store
        storage = OnboardTMStorage({2: {'name': 'test', 'capacity': 3, 'circular': False}})
        packets = [bytes([i]) for i in range(1, 6)]  # b'\x01' .. b'\x05'
        for pkt in packets:
            storage.store_packet_direct(2, pkt)
        dumped = storage.start_dump(2)
        assert len(dumped) == 3
        assert dumped[0] == b'\x01'
        assert dumped[1] == b'\x02'
        assert dumped[2] == b'\x03'

    def test_alarm_store_capacity(self):
        """Default alarm store capacity is 500."""
        storage = OnboardTMStorage()
        status = storage.get_status()
        alarm_status = [s for s in status if s['id'] == 4][0]
        assert alarm_status['capacity'] == 500
