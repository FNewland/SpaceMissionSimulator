"""Tests for failure-injection modes added to back the contingency procedures.

These verify that:
  * each new inject_failure() branch actually mutates persistent model state,
  * the matching clear_failure() branch undoes (or unsticks) it,
  * the contingency-procedure trigger condition becomes true.
"""
from __future__ import annotations

import pytest

from smo_simulator.models.obdh_basic import OBDHBasicModel, SW_BOOTLOADER, SW_APPLICATION
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.eps_basic import EPSBasicModel
from smo_simulator.models.tcs_basic import TCSBasicModel


# ── OBDH ──────────────────────────────────────────────────────────────────────

def test_obdh_memory_segment_fail_marks_segment_unhealthy_and_recoverable():
    obdh = OBDHBasicModel()
    assert all(obdh._state.memory_segments)

    obdh.inject_failure("memory_segment_fail", segment=2)
    assert obdh._state.memory_segments[2] is False
    assert obdh._state.mem_errors >= 1

    obdh.clear_failure("memory_segment_fail", segment=2)
    assert obdh._state.memory_segments[2] is True


def test_obdh_stuck_in_bootloader_drops_to_bootloader_image():
    obdh = OBDHBasicModel()
    obdh._state.sw_image = SW_APPLICATION

    obdh.inject_failure("stuck_in_bootloader")
    assert obdh._state.sw_image == SW_BOOTLOADER
    assert obdh._state.boot_image_corrupt is True
    assert obdh._state.boot_inhibit is True
    # The recovery procedure detects the stuck state via sw_image == 0,
    # which is exactly what we now report.

    obdh.clear_failure("stuck_in_bootloader")
    assert obdh._state.boot_image_corrupt is False
    assert obdh._state.boot_inhibit is False


# ── TT&C ──────────────────────────────────────────────────────────────────────

def test_ttc_antenna_deploy_failed_jams_sensor_and_blocks_deployment():
    ttc = TTCBasicModel()
    ttc._state.antenna_deployed = True
    ttc._state.antenna_deployment_ready = True
    ttc._state.antenna_deployment_sensor = 2

    ttc.inject_failure("antenna_deploy_failed")
    assert ttc._state.antenna_deployed is False
    assert ttc._state.antenna_deployment_ready is False
    assert ttc._state.antenna_deployment_sensor == 3  # jammed/partial


# ── EPS ───────────────────────────────────────────────────────────────────────

def test_eps_solar_array_total_loss_zeroes_both_arrays():
    eps = EPSBasicModel()
    eps.inject_failure("solar_array_total_loss")
    assert eps._state.sa_a_degradation == 0.0
    assert eps._state.sa_b_degradation == 0.0
    assert all(v == 0.0 for v in eps._state.sa_panel_degradation.values())

    eps.clear_failure("solar_array_total_loss")
    assert eps._state.sa_a_degradation == 1.0
    assert eps._state.sa_b_degradation == 1.0
    assert all(v == 1.0 for v in eps._state.sa_panel_degradation.values())


# ── TCS ───────────────────────────────────────────────────────────────────────

def test_tcs_temp_anomaly_pushes_battery_temp_above_red_line():
    tcs = TCSBasicModel()
    base = tcs._state.temp_battery
    tcs.inject_failure("temp_anomaly", zone="battery", offset_c=40.0)
    assert tcs._state.temp_battery >= base + 39.9
    assert tcs._state.sensor_drift.get("battery", 0.0) >= 39.9

    tcs.clear_failure("temp_anomaly", zone="battery")
    assert "battery" not in tcs._state.sensor_drift
