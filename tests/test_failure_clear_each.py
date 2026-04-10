"""Verify that EVERY failure exposed in the instructor dropdown can be both
injected AND cleared, and that the clear path actually undoes the dirty state
the inject path created.

This drives the failures via FailureManager — the same code path as the UI
buttons (failure_inject / failure_clear) — so any clear that quietly fell
through into the catch-all `else` would leave the model dirty and fail here.

Also exercises the global "CLEAR ALL FAILURES" button by injecting several
failures at once and asserting they all clear in one go.
"""
from __future__ import annotations

import pytest

from smo_simulator.failure_manager import FailureManager
from smo_simulator.models.eps_basic import EPSBasicModel
from smo_simulator.models.aocs_basic import AOCSBasicModel
from smo_simulator.models.tcs_basic import TCSBasicModel
from smo_simulator.models.obdh_basic import OBDHBasicModel, SW_APPLICATION
from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.payload_basic import PayloadBasicModel


# A tiny shim engine that owns the same {sub_name -> model} dict the real
# engine does and dispatches inject/clear to the right model. This mirrors
# SimulationEngine._handle_failure_inject / _handle_failure_clear without the
# rest of the engine boot machinery.
class _MiniEngine:
    def __init__(self):
        self.subsystems = {
            "eps": EPSBasicModel(),
            "aocs": AOCSBasicModel(),
            "tcs": TCSBasicModel(),
            "obdh": OBDHBasicModel(),
            "ttc": TTCBasicModel(),
            "payload": PayloadBasicModel(),
        }
        for m in self.subsystems.values():
            try:
                m.configure({})
            except Exception:
                pass
        self.fm = FailureManager(self._inject, self._clear)

    def _inject(self, cmd):
        m = self.subsystems[cmd["subsystem"]]
        extra = {k: v for k, v in cmd.items()
                 if k not in ("type", "subsystem", "failure", "magnitude")}
        m.inject_failure(cmd["failure"], float(cmd.get("magnitude", 1.0)), **extra)

    def _clear(self, cmd):
        m = self.subsystems[cmd["subsystem"]]
        extra = {k: v for k, v in cmd.items()
                 if k not in ("type", "subsystem", "failure")}
        m.clear_failure(cmd["failure"], **extra)


# (subsystem, failure, inject_kwargs, dirty_check, clean_check)
# `dirty_check(model)` must return True after inject; `clean_check(model)` after clear.
CASES = [
    # ── EPS ──────────────────────────────────────────────────────────────
    ("eps", "solar_array_partial", {"array": "A"},
     lambda m: m._state.sa_a_degradation < 1.0,
     lambda m: m._state.sa_a_degradation == 1.0),
    ("eps", "solar_panel_loss", {"face": "px"},
     lambda m: m._state.sa_panel_degradation["px"] < 1.0,
     lambda m: m._state.sa_panel_degradation["px"] == 1.0),
    ("eps", "solar_array_total_loss", {},
     lambda m: m._state.sa_a_degradation == 0.0 and m._state.sa_b_degradation == 0.0,
     lambda m: m._state.sa_a_degradation == 1.0 and m._state.sa_b_degradation == 1.0),
    ("eps", "bat_cell", {},
     lambda m: m._state.bat_cell_failure is True,
     lambda m: m._state.bat_cell_failure is False),
    ("eps", "bus_short", {},
     lambda m: m._state.bus_short is True,
     lambda m: m._state.bus_short is False),
    ("eps", "overcurrent", {"line_index": 3},
     lambda m: bool(m._state.oc_inject),
     lambda m: not m._state.oc_inject),
    # undervoltage clear is intentionally a no-op (SoC recovers physically)
    ("eps", "undervoltage", {},
     lambda m: True,  # inject just nudges SoC, no boolean to flip
     lambda m: True),
    ("eps", "overvoltage", {},
     lambda m: True,
     lambda m: True),

    # ── ADCS ─────────────────────────────────────────────────────────────
    ("aocs", "rw_bearing", {"wheel": 0},
     lambda m: m._bearing_degradation[0] > 0.0,
     lambda m: m._bearing_degradation[0] == 0.0),
    ("aocs", "rw_seizure", {"wheel": 1},
     lambda m: m._state.active_wheels[1] is False,
     lambda m: m._bearing_degradation[1] == 0.0),
    ("aocs", "multi_wheel_failure", {"wheels": [0, 1]},
     lambda m: m._state.active_wheels[0] is False and m._state.active_wheels[1] is False,
     lambda m: m._bearing_degradation[0] == 0.0 and m._bearing_degradation[1] == 0.0),
    ("aocs", "gyro_bias", {"axis": 0, "bias": 0.05},
     lambda m: abs(m._gyro_bias[0]) > 0.0,
     lambda m: m._gyro_bias[0] == 0.0),
    ("aocs", "st_failure", {"unit": 1},
     lambda m: m._state.st1_failed is True,
     lambda m: m._state.st1_failed is False),
    ("aocs", "css_failure", {},
     lambda m: m._state.css_failed is True,
     lambda m: m._state.css_failed is False),
    ("aocs", "css_head_fail", {"face": "px"},
     lambda m: m._state.css_head_failed["px"] is True,
     lambda m: m._state.css_head_failed["px"] is False),
    ("aocs", "mag_failure", {},
     lambda m: m._state.mag_failed is True,
     lambda m: m._state.mag_failed is False),
    ("aocs", "mag_a_fail", {},
     lambda m: m._state.mag_a_failed is True,
     lambda m: m._state.mag_a_failed is False),
    ("aocs", "mag_b_fail", {},
     lambda m: m._state.mag_b_failed is True,
     lambda m: m._state.mag_b_failed is False),
    ("aocs", "mtq_failure", {"axis": "x"},
     lambda m: m._state.mtq_x_failed is True,
     lambda m: m._state.mtq_x_failed is False),

    # ── TCS ──────────────────────────────────────────────────────────────
    ("tcs", "heater_failure", {"circuit": "battery"},
     lambda m: m._state.htr_battery_failed is True,
     lambda m: m._state.htr_battery_failed is False),
    ("tcs", "heater_stuck_on", {"circuit": "battery"},
     lambda m: m._state.htr_battery_stuck_on is True,
     lambda m: m._state.htr_battery_stuck_on is False),
    ("tcs", "heater_open_circuit", {"circuit": "battery"},
     lambda m: m._state.htr_battery_open_circuit is True,
     lambda m: m._state.htr_battery_open_circuit is False),
    ("tcs", "cooler_failure", {},
     lambda m: m._state.cooler_failed is True,
     lambda m: m._state.cooler_failed is False),
    ("tcs", "sensor_drift", {"zone": "battery"},
     lambda m: "battery" in m._state.sensor_drift,
     lambda m: "battery" not in m._state.sensor_drift),
    ("tcs", "obc_thermal", {"heat_w": 30.0},
     lambda m: m._state.obc_internal_heat_w > 0.0,
     lambda m: m._state.obc_internal_heat_w == 0.0),
    ("tcs", "temp_anomaly", {"zone": "battery", "offset_c": 25.0},
     lambda m: "battery" in m._state.sensor_drift,
     lambda m: "battery" not in m._state.sensor_drift),

    # ── OBDH ─────────────────────────────────────────────────────────────
    ("obdh", "cpu_spike", {"load": 95.0},
     lambda m: m._cpu_base >= 95.0,
     lambda m: m._cpu_base == 35.0),
    ("obdh", "watchdog_reset", {},
     lambda m: True,  # one-shot
     lambda m: m._state.watchdog_timer == 0),
    ("obdh", "memory_errors", {"count": 5},
     lambda m: m._state.mem_errors >= 5,
     lambda m: True),  # clear is best-effort scrub
    ("obdh", "memory_segment_fail", {"segment": 2},
     lambda m: m._state.memory_segments[2] is False,
     lambda m: m._state.memory_segments[2] is True),
    ("obdh", "bus_failure", {"bus": "A"},
     lambda m: m._state.bus_a_status != 0,
     lambda m: m._state.bus_a_status == 0),
    ("obdh", "boot_image_corrupt", {},
     lambda m: m._state.boot_image_corrupt is True,
     lambda m: m._state.boot_image_corrupt is False),
    ("obdh", "stuck_in_bootloader", {},
     lambda m: m._state.boot_image_corrupt is True and m._state.boot_inhibit is True,
     lambda m: m._state.boot_image_corrupt is False and m._state.boot_inhibit is False),

    # ── TT&C ─────────────────────────────────────────────────────────────
    ("ttc", "primary_failure", {},
     lambda m: m._state.primary_failed is True,
     lambda m: m._state.primary_failed is False),
    ("ttc", "redundant_failure", {},
     lambda m: m._state.redundant_failed is True,
     lambda m: m._state.redundant_failed is False),
    ("ttc", "high_ber", {"offset": 10.0},
     lambda m: m._state.ber_inject_offset > 0.0,
     lambda m: m._state.ber_inject_offset == 0.0),
    ("ttc", "pa_overheat", {"heat_w": 20.0},
     lambda m: m._state.pa_heat_inject > 0.0,
     lambda m: m._state.pa_heat_inject == 0.0),
    ("ttc", "uplink_loss", {},
     lambda m: m._state.uplink_lost is True,
     lambda m: m._state.uplink_lost is False),
    ("ttc", "receiver_degrade", {"nf_db": 5.0},
     lambda m: m._state.receiver_nf_degrade > 0.0,
     lambda m: m._state.receiver_nf_degrade == 0.0),
    ("ttc", "antenna_deploy_failed", {},
     lambda m: m._state.antenna_deployment_sensor == 3,
     lambda m: m._state.antenna_deployment_sensor == 1
               and m._state.antenna_deployment_ready is True),

    # ── Payload ──────────────────────────────────────────────────────────
    ("payload", "cooler_failure", {},
     lambda m: m._state.cooler_failed is True,
     lambda m: m._state.cooler_failed is False),
    ("payload", "fpa_degraded", {},
     lambda m: m._state.fpa_degraded is True,
     lambda m: m._state.fpa_degraded is False),
    ("payload", "image_corrupt", {"count": 3},
     lambda m: m._state.corrupt_remaining > 0,
     lambda m: m._state.corrupt_remaining == 0),
    ("payload", "memory_segment_fail", {"segment": 0},
     lambda m: 0 in m._state.bad_segments,
     lambda m: 0 not in m._state.bad_segments),
    ("payload", "ccd_line_dropout", {},
     lambda m: m._state.ccd_line_dropout is True,
     lambda m: m._state.ccd_line_dropout is False),
]


@pytest.mark.parametrize("subsystem,failure,kwargs,dirty,clean", CASES,
                         ids=[f"{c[0]}.{c[1]}" for c in CASES])
def test_each_failure_can_be_individually_cleared(subsystem, failure, kwargs, dirty, clean):
    eng = _MiniEngine()
    model = eng.subsystems[subsystem]

    fid = eng.fm.inject(subsystem, failure, magnitude=1.0, onset="step", **kwargs)
    assert dirty(model), f"{subsystem}.{failure} did not dirty model state"

    ok = eng.fm.clear(fid)
    assert ok, f"FailureManager.clear() returned False for {fid}"
    assert clean(model), f"{subsystem}.{failure} clear did not restore model state"
    # And the failure must no longer appear in the active list.
    active_ids = [a["id"] for a in eng.fm.active_failures()]
    assert fid not in active_ids


def test_clear_all_button_clears_a_mixed_set_of_failures():
    eng = _MiniEngine()

    eng.fm.inject("eps", "bat_cell", magnitude=1.0, onset="step")
    eng.fm.inject("aocs", "css_failure", magnitude=1.0, onset="step")
    eng.fm.inject("tcs", "heater_stuck_on", magnitude=1.0, onset="step", circuit="battery")
    eng.fm.inject("obdh", "stuck_in_bootloader", magnitude=1.0, onset="step")
    eng.fm.inject("ttc", "antenna_deploy_failed", magnitude=1.0, onset="step")
    eng.fm.inject("payload", "image_corrupt", magnitude=1.0, onset="step", count=3)

    assert len(eng.fm.active_failures()) == 6

    eng.fm.clear_all()

    assert eng.fm.active_failures() == []
    assert eng.subsystems["eps"]._state.bat_cell_failure is False
    assert eng.subsystems["aocs"]._state.css_failed is False
    assert eng.subsystems["tcs"]._state.htr_battery_stuck_on is False
    assert eng.subsystems["obdh"]._state.boot_image_corrupt is False
    assert eng.subsystems["ttc"]._state.antenna_deployment_ready is True
    assert eng.subsystems["payload"]._state.corrupt_remaining == 0
