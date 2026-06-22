"""Edge-triggering / one-shot event audit.

Verifies that subsystem-model telemetry events fire ONCE per state transition
(rising edge), not every tick a triggering condition holds. Each test drives a
PERSISTENT triggering condition for ~20 ticks and asserts the corresponding
event is emitted at most once; where practical it also clears then re-asserts
the condition and checks the event re-emits.

See the suite-wide bug: models computed a level/threshold each tick and appended
an event whenever the condition was TRUE, with no edge guard, so they re-fired
continuously. Correct behaviour is rising-edge / state-change / one-shot.
"""
from unittest.mock import MagicMock

import pytest

from smo_simulator.models.ttc_basic import TTCBasicModel
from smo_simulator.models.payload_basic import PayloadBasicModel
from smo_simulator.models.tcs_basic import TCSBasicModel
from smo_simulator.models.eps_basic import EPSBasicModel
from smo_simulator.models.aocs_basic import AOCSBasicModel, MODE_NOMINAL


class StubQueue:
    """List-backed stand-in for the engine's _model_event_queue.

    Supports both the dict-based put/put_nowait path (TTC, payload, TCS, AOCS,
    OBDH) and the tuple-based put path (EPS), recording everything for later
    inspection.
    """

    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)

    def put_nowait(self, item):
        self.items.append(item)

    def event_ids(self):
        ids = []
        for it in self.items:
            if isinstance(it, dict):
                ids.append(it.get("event_id"))
            elif isinstance(it, (tuple, list)) and it:
                ids.append(it[0])
        return ids

    def count(self, event_id):
        return sum(1 for e in self.event_ids() if e == event_id)


class StubEngine:
    def __init__(self):
        self._model_event_queue = StubQueue()


def make_orbit_state(in_contact=False, range_km=1000.0, elevation=30.0,
                     in_eclipse=False):
    state = MagicMock()
    state.in_eclipse = in_eclipse
    state.solar_beta_deg = 20.0
    state.lat_deg = 45.0
    state.lon_deg = 10.0
    state.alt_km = 500.0
    state.vel_x = 0.0
    state.vel_y = 7.5
    state.vel_z = 0.0
    state.in_contact = in_contact
    state.gs_elevation_deg = elevation
    state.gs_azimuth_deg = 90.0
    state.gs_range_km = range_km
    return state


def _attach_engine(model):
    eng = StubEngine()
    model._engine = eng
    return eng._model_event_queue


# ──────────────────────────────────────────────────────────────────────
# TTC — lock acquisition (one-shot) + PA overheat shutdown (rising-edge)
# ──────────────────────────────────────────────────────────────────────

class TestTTCEdgeTriggering:
    def _model(self):
        m = TTCBasicModel()
        m.configure({})
        m._state.antenna_deployed = True
        m._state.antenna_deployment_sensor = 2
        return m

    def test_lock_acquired_events_fire_at_most_once(self):
        """Held in frame_sync for 20+ ticks → each 'acquired' event ≤ 1."""
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state(in_contact=True, range_km=1000.0)
        params = {}
        for _ in range(25):
            m.tick(1.0, orbit, params)
        assert m._state.frame_sync is True
        assert q.count(0x0500) <= 1, "Carrier lock acquired emitted more than once"
        assert q.count(0x0502) <= 1, "Bit sync acquired emitted more than once"
        assert q.count(0x0504) <= 1, "Frame sync acquired emitted more than once"

    def test_pa_overheat_shutdown_fires_once_then_re_emits(self):
        """PA overheat shutdown held → event 0x0509 emitted exactly once;
        clearing and re-asserting re-emits."""
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state(in_contact=False)
        params = {}
        m._state.pa_overheat_shutdown = True
        for _ in range(20):
            # Force the condition to persist (cool-down hysteresis would clear
            # it otherwise); pa_temp stays high so the model keeps it latched.
            m._state.pa_temp = 75.0
            m._state.pa_overheat_shutdown = True
            m.tick(1.0, orbit, params)
        assert q.count(0x0509) == 1, "PA overheat shutdown re-fired while held"

        # Clear, then re-assert — should emit a second time.
        m._state.pa_overheat_shutdown = False
        m._state.pa_temp = 30.0
        m.tick(1.0, orbit, params)
        m._state.pa_overheat_shutdown = True
        m._state.pa_temp = 75.0
        m.tick(1.0, orbit, params)
        assert q.count(0x0509) == 2, "PA overheat shutdown did not re-emit on new edge"


# ──────────────────────────────────────────────────────────────────────
# Payload — SNR_DEGRADED (rising-edge) + storage/cooler thresholds
# ──────────────────────────────────────────────────────────────────────

class TestPayloadEdgeTriggering:
    def _model(self):
        m = PayloadBasicModel()
        m.configure({})
        return m

    @staticmethod
    def _imaging_tick(m, orbit, *, att_error):
        """Drive one IMAGING tick with the FPA forced ready.

        The aggregate SNR is computed from the per-band model and driven low by
        a large attitude error (att_quality coupling): att_error ~3 deg yields
        an aggregate SNR well under the 25 dB degraded threshold; ~0 deg keeps
        it nominal (~40+ dB).
        """
        s = m._state
        s.mode = 2            # IMAGING
        s.fpa_temp = -15.0    # in the cooler operational band
        s.fpa_ready_timer = 100.0  # past the readiness hysteresis
        s.cooler_failed = False
        m.tick(1.0, orbit, {0x0217: att_error})

    def test_snr_degraded_fires_at_most_once(self):
        """Held in SNR-degraded imaging condition for 20 ticks → 0x0608 ≤ 1."""
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        for _ in range(20):
            self._imaging_tick(m, orbit, att_error=3.0)
        assert m._state.fpa_ready is True
        assert m._state.snr < 25.0, "test precondition: SNR should be degraded"
        assert q.count(0x0608) <= 1, "SNR_DEGRADED emitted every tick (not edge-triggered)"

    def test_snr_degraded_re_emits_after_recovery(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()

        for _ in range(3):
            self._imaging_tick(m, orbit, att_error=3.0)   # degraded
        first = q.count(0x0608)
        assert first >= 1, "SNR_DEGRADED should fire on the first degraded edge"
        for _ in range(3):
            self._imaging_tick(m, orbit, att_error=0.0)   # recover (nominal SNR)
        for _ in range(3):
            self._imaging_tick(m, orbit, att_error=3.0)   # re-assert
        assert q.count(0x0608) == first + 1, "SNR_DEGRADED did not re-emit on new edge"

    def test_cooler_failure_fires_once(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {}
        m._state.cooler_failed = True
        for _ in range(20):
            m._state.cooler_failed = True
            m.tick(1.0, orbit, params)
        assert q.count(0x0606) <= 1, "COOLER_FAILURE re-fired while held"


# ──────────────────────────────────────────────────────────────────────
# TCS — over-temp warning (rising-edge) + thermal runaway
# ──────────────────────────────────────────────────────────────────────

class TestTCSEdgeTriggering:
    def _model(self):
        m = TCSBasicModel()
        m.configure({})
        return m

    def test_battery_overtemp_warning_fires_at_most_once(self):
        """Battery held above its over-temp warning limit for 20 ticks → ≤ 1."""
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {}
        # Warning limit for battery high is 40.0C; alarm high is 50.0C.
        # Hold at 45C: over the warning, under the alarm.
        for _ in range(20):
            m._state.temp_battery = 45.0
            m.tick(1.0, orbit, params)
        assert q.count(0x0400) <= 1, "TCS battery over-temp warning re-fired while held"

    def test_overtemp_warning_re_emits_after_clear(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {}
        for _ in range(3):
            m._state.temp_battery = 45.0
            m.tick(1.0, orbit, params)
        first = q.count(0x0400)
        for _ in range(3):
            m._state.temp_battery = 20.0   # back in range, clears edge state
            m.tick(1.0, orbit, params)
        for _ in range(3):
            m._state.temp_battery = 45.0   # re-assert
            m.tick(1.0, orbit, params)
        assert q.count(0x0400) == first + 1, "Over-temp warning did not re-emit on new edge"


# ──────────────────────────────────────────────────────────────────────
# EPS — battery overtemp / undertemp / solar-array-degraded (rising-edge)
# ──────────────────────────────────────────────────────────────────────

class TestEPSEdgeTriggering:
    def _model(self):
        m = EPSBasicModel()
        m.configure({})
        return m

    def test_battery_overtemp_fires_at_most_once(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {}
        for _ in range(20):
            m._state.bat_temp = 60.0   # > 45C overtemp, held
            m.tick(1.0, orbit, params)
        assert q.count(0x0109) <= 1, "EPS battery overtemp re-fired while held"

    def test_solar_array_degraded_fires_at_most_once(self):
        m = self._model()
        q = _attach_engine(m)
        # Sunlit so expected SA power is high; degrade the array so actual << expected.
        orbit = make_orbit_state(in_eclipse=False)
        params = {0x0245: 0.0, 0x0246: 0.0, 0x0247: 0.0}  # no CSS sun vector → beta fallback
        m.inject_failure("solar_array_total_loss")
        for _ in range(20):
            m.tick(1.0, orbit, params)
        assert q.count(0x010B) <= 1, "Solar array degraded re-fired while held"


# ──────────────────────────────────────────────────────────────────────
# AOCS — attitude-error-high / gyro-bias-high / RW bearing (rising-edge)
# ──────────────────────────────────────────────────────────────────────

class TestAOCSEdgeTriggering:
    def _model(self):
        m = AOCSBasicModel()
        m.configure({})
        return m

    def test_attitude_error_high_fires_at_most_once(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {0x0117: 1}   # AOCS power line on
        s = m._state
        s.mode = MODE_NOMINAL
        # Force a persistent large attitude error every tick.
        for _ in range(20):
            s.mode = MODE_NOMINAL
            s.att_error = 30.0
            m.tick(1.0, orbit, params)
            s.att_error = 30.0  # in case the mode tick reduced it
        # Re-run generation with the error pinned high to confirm no re-fire:
        # count must remain at most 1 across the whole run.
        assert q.count(0x0206) <= 1, "ATTITUDE_ERROR_HIGH re-fired while held"

    def test_rw_bearing_degraded_fires_once_per_wheel(self):
        m = self._model()
        q = _attach_engine(m)
        orbit = make_orbit_state()
        params = {0x0117: 1}
        m._bearing_degradation[0] = 0.8   # health 20% < 50%
        for _ in range(20):
            m._bearing_degradation[0] = 0.8
            m.tick(1.0, orbit, params)
        assert q.count(0x0202) <= 1, "RW bearing degraded re-fired while held"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
