"""The S2 GPS-receiver device command (device 0x020F, S2_AOCS_GPS_RECEIVER)
must actually power the GPS receiver on/off.

Previously the GPS power was gated only by AOCS mode, so toggling the device
state had no effect — the command was unwired.
"""
import random
from types import SimpleNamespace

from smo_simulator.models.aocs_basic import AOCSBasicModel, MODE_NOMINAL

GPS_DEVICE = 0x020F


def _nominal_model():
    m = AOCSBasicModel()
    m.configure({})
    m._state.mode = MODE_NOMINAL
    m._state.device_states[GPS_DEVICE] = True
    return m


def _acquire(m, ticks=70):
    orbit = SimpleNamespace()
    for _ in range(ticks):
        m._tick_gyro_and_gps(m._state, 1.0, orbit)


def test_gps_device_off_powers_down_receiver():
    random.seed(0)
    m = _nominal_model()
    _acquire(m)  # past cold TTFF (60 s)
    assert m._state.gps_fix == 3
    assert m._state.gps_num_sats >= 6

    # Operator sends S2_AOCS_GPS_RECEIVER OFF
    assert m.set_device_state(GPS_DEVICE, False) is True
    m._tick_gyro_and_gps(m._state, 1.0, SimpleNamespace())
    assert m._state.gps_fix == 0
    assert m._state.gps_num_sats == 0
    assert m._state.gps_ttff_timer == 0.0  # cold restart armed


def test_gps_device_on_reacquires_cold():
    random.seed(1)
    m = _nominal_model()
    m.set_device_state(GPS_DEVICE, False)
    m._tick_gyro_and_gps(m._state, 1.0, SimpleNamespace())
    assert m._state.gps_fix == 0

    # Switch back ON: receiver re-acquires from a cold start
    assert m.set_device_state(GPS_DEVICE, True) is True
    m._tick_gyro_and_gps(m._state, 1.0, SimpleNamespace())
    assert m._state.gps_fix == 0          # still acquiring just after re-power
    _acquire(m)                            # let TTFF elapse
    assert m._state.gps_fix == 3           # full 3D+velocity fix restored


def test_gps_stays_on_by_default():
    """Default device state is ON, so nominal ops still get a fix (no behaviour
    change for operators who never touch the GPS device command)."""
    random.seed(2)
    m = _nominal_model()
    _acquire(m)
    assert m._state.gps_fix == 3
