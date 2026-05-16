"""Tests for Radio front-end status engine."""

import time
import pytest
from smo_rfsim.radio.frontend import (
    RadioFrontend, RadioStatus, LockState, LEDColor
)


class TestRadioStatus:
    def test_default_state(self):
        status = RadioStatus()
        assert status.carrier_lock == LockState.UNLOCKED
        assert status.good_frames == 0

    def test_carrier_led_colors(self):
        status = RadioStatus()
        assert status.carrier_led() == LEDColor.RED
        status.carrier_lock = LockState.ACQUIRING
        assert status.carrier_led() == LEDColor.YELLOW
        status.carrier_lock = LockState.LOCKED
        assert status.carrier_led() == LEDColor.GREEN

    def test_vc_led_stale(self):
        status = RadioStatus(timestamp=time.time())
        # No VC activity → RED
        assert status.vc_led(0) == LEDColor.RED
        # Recent activity → GREEN
        status.vc_active[0] = status.timestamp - 1
        assert status.vc_led(0) == LEDColor.GREEN
        # Old activity → YELLOW
        status.vc_active[1] = status.timestamp - 10
        assert status.vc_led(1) == LEDColor.YELLOW


class TestRadioFrontend:
    def test_update_frame_counts(self):
        fe = RadioFrontend()
        fe.update_frame_counts(100, 5)
        assert fe.status.good_frames == 100
        assert fe.status.bad_frames == 5

    def test_update_rf(self):
        fe = RadioFrontend()
        fe.update_rf(10.0, doppler_hz=1500.0, range_km=450.0)
        assert fe.status.eb_n0_db == 10.0
        assert fe.status.doppler_hz == 1500.0
        assert fe.status.ber_log10 < -3  # BER should be low at 10 dB

    def test_update_lock(self):
        fe = RadioFrontend()
        fe.update_lock(LockState.LOCKED, LockState.LOCKED, LockState.ACQUIRING)
        assert fe.status.carrier_lock == LockState.LOCKED
        assert fe.status.frame_sync == LockState.ACQUIRING

    def test_snapshot_has_timestamp(self):
        fe = RadioFrontend()
        snap = fe.snapshot()
        assert snap.timestamp > 0
