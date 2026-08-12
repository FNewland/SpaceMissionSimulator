"""Phase 4 acceptance tests for the 2026-08b batch (payload / TCS / image path).

Three reported problems, and the fixes the coherence review (§2.4, PL-a..PL-d)
assigned to the payload owner:

  (A) PL-d — "need to switch to imaging on payload to do calibration dark".
      Payload mode 3 CALIBRATION; calibration gated on the OPTICS (shutter,
      lamp, lamp device power, FPA thermal state) instead of on `mode == 2`;
      an absolute PAYLOAD_SET_SHUTTER; the cycle_shutter double toggle; the
      BOTH-mode dark phase that completed in one tick; and the valid mask that
      only a BOTH run could ever set.

  (B) PL-a — "cannot do a capture, FPA does not get to YES". Three of the four
      cooler command paths set the TCS request flag and never energised the
      EPS line; the TCS handler answered "success" regardless; the payload's
      duplicate `_fpa_target` was not persisted; the 0.00 C setpoint was
      rejected by a float epsilon; the planner asked for AOCS mode 1.

  (C) PL-c — "where do the image packets go?" Nowhere: no bytes existed, S13
      fabricated blocks, S13.5 freed nothing, the science store held acks, and
      IMAGING accreted ~9.7 MB/s of un-catalogued phantom data with zero
      capture commands.

Every behavioural claim below was first reproduced against the pre-fix build.
"""
import struct
import sys
import zlib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from smo_simulator.models.eps_basic import EPSBasicModel  # noqa: E402
from smo_simulator.models.payload_basic import (  # noqa: E402
    PAYLOAD_EVENTS, PayloadBasicModel, snap_to_envelope,
)
from smo_simulator.models.tcs_basic import TCSBasicModel  # noqa: E402


class _Orbit:
    in_eclipse = False
    alt_km = 500.0
    lat_deg = 10.0
    lon_deg = 20.0
    solar_beta_deg = 30.0


def _cfg(name):
    return yaml.safe_load((CONFIG_DIR / "subsystems" / f"{name}.yaml").read_text())


def _payload(seed=7):
    m = PayloadBasicModel()
    m.configure(_cfg("payload"))
    m.rng.seed(seed)
    return m


def _cold_standby(m):
    """STANDBY with the detector really at its setpoint and settled."""
    s = m._state
    s.mode = 1
    s.cooler_active = True
    s.fpa_temp = s.cooler_setpoint_c
    s.fpa_ready = True
    s.fpa_ready_timer = 120.0
    return m


def _ready_to_image(m):
    _cold_standby(m)
    m._state.mode = 2
    m._state.shutter_position = 1
    return m


# ══════════════════════════════════════════════════════════════════════
# (A) PL-d — calibration, mode 3, and the shutter
# ══════════════════════════════════════════════════════════════════════

class TestCalibrationMode:

    def test_mode_2_still_means_imaging(self):
        """The AOCS contract (DISPLAY_REQUESTS_AOCS.md §3.3).

        AOCS asserts its automatic desaturation inhibit on
        `payload.mode == 2`. CALIBRATION had to be a NEW enum value; if this
        test ever fails, the AOCS owner has to be told before the change ships.
        """
        m = _payload()
        assert m.handle_command({"command": "set_mode", "mode": 2})["success"]
        params = {}
        m.tick(1.0, _Orbit(), params)
        assert params[0x0600] == 2

    def test_calibration_is_mode_3(self):
        m = _payload()
        assert m.handle_command({"command": "set_mode", "mode": 3})["success"]
        assert m._state.mode == 3

    def test_mode_out_of_range_is_refused(self):
        r = _payload().handle_command({"command": "set_mode", "mode": 4})
        assert r["success"] is False
        assert r["error_code"] == 0x0003

    @pytest.mark.parametrize("cal_mode", [0, 1, 2])
    def test_calibration_is_refused_in_imaging(self, cal_mode):
        """The reported defect. Every phase set, refused from IMAGING."""
        m = _ready_to_image(_payload())
        m._state.cal_lamp_on = True   # so FLAT's own optics gate cannot mask it
        r = m.handle_command({"command": "start_calibration", "mode": cal_mode})
        assert r["success"] is False
        assert r["code"] == "CAL_REFUSED_IMAGING"

    def test_dark_runs_from_standby_with_the_shutter_closed(self):
        """COM-011's route, which used to require PAYLOAD_SET_MODE(2) first."""
        m = _cold_standby(_payload())
        assert m.handle_command({"command": "set_shutter",
                                 "position": 0})["success"]
        r = m.handle_command({"command": "start_calibration", "mode": 0})
        assert r["success"] is True, r
        assert m._state.mode == 3, "STANDBY should be promoted to CALIBRATION"

    def test_dark_is_refused_with_the_shutter_open(self):
        m = _cold_standby(_payload())
        m._state.shutter_position = 1
        r = m.handle_command({"command": "start_calibration", "mode": 0})
        assert r["success"] is False
        assert r["code"] == "CAL_SHUTTER_OPEN"

    def test_dark_is_refused_with_the_lamp_lit(self):
        m = _cold_standby(_payload())
        m._state.shutter_position = 0
        m._state.cal_lamp_on = True
        r = m.handle_command({"command": "start_calibration", "mode": 0})
        assert r["success"] is False
        assert r["code"] == "CAL_LAMP_ON"

    def test_calibration_is_refused_when_the_fpa_is_warm(self):
        """Dark current roughly doubles every 6-8 K — coefficients taken off
        setpoint describe a detector the mission does not have."""
        m = _payload()
        m._state.mode = 1
        m._state.shutter_position = 0
        r = m.handle_command({"command": "start_calibration", "mode": 0})
        assert r["success"] is False
        assert r["code"] == "CAL_FPA_NOT_READY"

    def test_flat_needs_the_shutter_open_and_the_lamp_on(self):
        m = _cold_standby(_payload())
        m._state.shutter_position = 0
        assert m.handle_command({"command": "start_calibration",
                                 "mode": 1})["code"] == "CAL_SHUTTER_CLOSED"
        m._state.shutter_position = 1
        assert m.handle_command({"command": "start_calibration",
                                 "mode": 1})["code"] == "CAL_LAMP_OFF"
        m._state.cal_lamp_on = True
        assert m.handle_command({"command": "start_calibration",
                                 "mode": 1})["success"] is True

    def test_unpowered_lamp_device_blocks_a_flat(self):
        m = _cold_standby(_payload())
        m._state.shutter_position = 1
        m._state.cal_lamp_on = True
        m._state.device_states[0x0602] = False
        r = m.handle_command({"command": "start_calibration", "mode": 1})
        assert r["success"] is False and r["error_code"] == 0x0007

    def test_both_does_not_skip_its_dark_phase(self):
        """Was: `calibration_timer = duration/2` then `<= half_duration`,
        true on the FIRST tick — 15 s of dark integration in one dt."""
        m = _cold_standby(_payload())
        m._state.shutter_position = 0
        m.handle_command({"command": "start_calibration", "mode": 2})
        assert m._state.calibration_state == 1
        m.tick(1.0, _Orbit(), {})
        assert m._state.calibration_state == 1, "dark phase skipped in one tick"

        states = []
        for _ in range(30):
            m.tick(1.0, _Orbit(), {})
            states.append(m._state.calibration_state)
        # DARK -> FLAT at the half-way point, not immediately.
        assert states.index(2) >= 13
        assert m._state.calibration_state == 3
        assert m._state.dark_frame_count == 10
        assert m._state.flat_frame_count == 10

    def test_both_sequencer_drives_the_optics_across_the_phase_boundary(self):
        m = _cold_standby(_payload())
        m._state.shutter_position = 0
        m.handle_command({"command": "start_calibration", "mode": 2})
        for _ in range(16):
            m.tick(1.0, _Orbit(), {})
        assert m._state.calibration_state == 2
        assert m._state.shutter_position == 1, "sequencer must open the shutter"
        assert m._state.cal_lamp_on is True, "sequencer must light the lamp"
        for _ in range(16):
            m.tick(1.0, _Orbit(), {})
        assert m._state.cal_lamp_on is False, "sequencer must put the lamp out"

    def test_dark_then_flat_reaches_a_valid_calibration(self):
        """The route COM-011 recommends. Before, a DARK-only or FLAT-only run
        left `calibration_valid_mask` at 0x00 forever — only a BOTH run ever
        set it — so the recommended route could never reach a valid state."""
        m = _cold_standby(_payload())
        m.handle_command({"command": "set_shutter", "position": 0})
        m.handle_command({"command": "start_calibration", "mode": 0})
        for _ in range(20):
            m.tick(1.0, _Orbit(), {})
        assert m._state.calibration_state == 3
        assert m._state.bias_coeff["blue"] is not None
        assert m._state.calibration_valid_mask == 0x00, "gain half is missing"

        m.handle_command({"command": "set_shutter", "position": 1})
        m.handle_command({"command": "set_cal_lamp", "on": True})
        m.handle_command({"command": "start_calibration", "mode": 1})
        for _ in range(20):
            m.tick(1.0, _Orbit(), {})
        assert m._state.calibration_valid_mask == 0x0F

    @pytest.mark.parametrize("break_it,reason", [
        (lambda s: setattr(s, "shutter_position", 1), 2),
        (lambda s: setattr(s, "cal_lamp_on", True), 3),
        (lambda s: setattr(s, "fpa_temp", 20.0), 4),
    ])
    def test_optics_violation_mid_run_aborts(self, break_it, reason):
        m = _cold_standby(_payload())
        m.handle_command({"command": "set_shutter", "position": 0})
        m.handle_command({"command": "start_calibration", "mode": 0})
        m.tick(1.0, _Orbit(), {})
        break_it(m._state)
        # Two ticks: the optics/thermal monitor runs at the TOP of tick(), so a
        # thermal excursion is seen on the tick after readiness is recomputed.
        m.tick(1.0, _Orbit(), {})
        m.tick(1.0, _Orbit(), {})
        assert m._state.calibration_active is False
        assert m._state.cal_abort_reason == reason
        assert m._state.calibration_valid_mask == 0x00

    def test_leaving_calibration_mode_aborts_the_run(self):
        m = _cold_standby(_payload())
        m.handle_command({"command": "set_shutter", "position": 0})
        m.handle_command({"command": "start_calibration", "mode": 0})
        m.handle_command({"command": "set_mode", "mode": 1})
        assert m._state.calibration_active is False
        assert m._state.cal_abort_reason == 5


class TestShutter:

    def test_cycle_shutter_toggles_once_per_cycle_not_twice(self):
        """`cycles=1` from OPEN must end CLOSED. The handler used to toggle
        AND the tick used to toggle, so the blade always came back."""
        m = _payload()
        assert m._state.shutter_position == 1
        m.handle_command({"command": "cycle_shutter", "cycles": 1})
        assert m._state.shutter_position == 1, "no movement before the tick"
        params = {}
        for _ in range(3):
            m.tick(1.0, _Orbit(), params)
        assert m._state.shutter_position == 0
        assert params[0x063C] == 0.0, "COM-011 step 2 checks 0x063C == 0"
        assert m._state.shutter_test_active is False

    def test_even_cycle_count_returns_to_the_starting_position(self):
        m = _payload()
        m.handle_command({"command": "cycle_shutter", "cycles": 2})
        for _ in range(4):
            m.tick(1.0, _Orbit(), {})
        assert m._state.shutter_position == 1

    def test_set_shutter_is_absolute_and_idempotent(self):
        m = _payload()
        for _ in range(2):
            assert m.handle_command({"command": "set_shutter",
                                     "position": 0})["success"]
            assert m._state.shutter_position == 0
        assert m.handle_command({"command": "set_shutter",
                                 "position": 1})["success"]
        assert m._state.shutter_position == 1

    def test_set_shutter_rejects_a_bad_position(self):
        r = _payload().handle_command({"command": "set_shutter", "position": 7})
        assert r["success"] is False and r["error_code"] == 0x0003

    def test_set_shutter_needs_the_mechanism_powered(self):
        m = _payload()
        m._state.device_states[0x0603] = False
        r = m.handle_command({"command": "set_shutter", "position": 0})
        assert r["success"] is False and r["error_code"] == 0x0007

    def test_set_shutter_refused_during_a_cycle_test(self):
        m = _payload()
        m.handle_command({"command": "cycle_shutter", "cycles": 5})
        r = m.handle_command({"command": "set_shutter", "position": 0})
        assert r["success"] is False and r["error_code"] == 0x0005

    def test_capture_reads_the_shutter(self):
        """`capture` never looked at `shutter_position` at all, so a
        100 %-quality product of the back of the blade was catalogued."""
        m = _ready_to_image(_payload())
        m._state.shutter_position = 0
        r = m.handle_command({"command": "capture"})
        assert r["success"] is False
        assert r["code"] == "SHUTTER_CLOSED"
        m._state.shutter_position = 1
        assert m.handle_command({"command": "capture"})["success"] is True


# ══════════════════════════════════════════════════════════════════════
# (B) PL-a — the cooler chain
# ══════════════════════════════════════════════════════════════════════

class TestCoolerSetpointPersistenceAndEpsilon:

    def test_setpoint_survives_a_breakpoint_round_trip(self):
        """Defect #132's payload twin. `_fpa_target` lived outside the
        dataclass so `get_state()` never captured it: after a load, TCS
        regulated to the restored setpoint while the payload's readiness
        window was still centred on the constructor's -15 C, and FPA READY
        read NO forever with 0x0631 showing the commanded value."""
        m = _payload()
        assert m.handle_command({"command": "set_cooler_setpoint",
                                 "setpoint": -19.5})["success"]
        snapshot = m.get_state()
        assert snapshot["cooler_setpoint_c"] == pytest.approx(-19.5)

        restored = _payload()
        restored.set_state(snapshot)
        assert restored._state.cooler_setpoint_c == pytest.approx(-19.5)
        # The readiness window must follow the RESTORED setpoint.
        restored._state.mode = 1
        restored._state.fpa_temp = -19.5
        restored._state.cooler_active = True
        params = {}
        for _ in range(70):
            restored.tick(1.0, _Orbit(), params)
        assert restored._state.fpa_ready is True
        assert params[0x0631] == pytest.approx(-19.5, abs=0.1)
        assert params[0x0610] == 1

    def test_no_second_copy_of_the_setpoint_exists(self):
        assert not hasattr(_payload(), "_fpa_target")

    def test_zero_degrees_is_accepted_on_the_wire_value(self):
        """raw 27315 cK decodes to +5.684341886080802e-14, which is > 0.0, so
        the documented top of the envelope was refused with a message saying
        the value was legal."""
        decoded = 27315 * 0.01 - 273.15
        assert decoded > 0.0, "the epsilon this test exists for has vanished"
        for m in (_payload(), None):
            if m is None:
                break
            assert m.handle_command({"command": "set_cooler_setpoint",
                                     "setpoint": decoded})["success"] is True
            assert m._state.cooler_setpoint_c == 0.0

    def test_tcs_applies_the_same_epsilon(self):
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        decoded = 27315 * 0.01 - 273.15
        assert tcs.handle_command({"command": "set_fpa_setpoint",
                                   "setpoint_c": decoded})["success"] is True
        assert tcs._fpa_cooler_target == 0.0

    def test_out_of_envelope_is_still_refused(self):
        r = _payload().handle_command({"command": "set_cooler_setpoint",
                                       "setpoint": 0.5})
        assert r["success"] is False and r["error_code"] == 0x0003

    def test_snap_helper_only_snaps_inside_the_tolerance(self):
        assert snap_to_envelope(5.68e-14, -20.0, 0.0) == 0.0
        assert snap_to_envelope(-20.0 - 1e-9, -20.0, 0.0) == -20.0
        assert snap_to_envelope(0.001, -20.0, 0.0) == 0.001


class TestCoolerLoudFailure:

    def test_tcs_cooler_refuses_when_its_eps_line_is_dark(self):
        """`return {"success": True}` was unconditional, so COM-009's only
        cooler path reported "execution complete" while 0x040C stayed 0."""
        class _Eng:
            params = {0x0114: 0}
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        tcs._engine = _Eng()
        r = tcs.handle_command({"command": "fpa_cooler", "on": True})
        assert r["success"] is False
        assert r["error_code"] == 0x0004
        assert "power line 4" in r["message"]
        assert tcs._state.cooler_fpa is False

    def test_tcs_cooler_accepts_when_the_line_is_live(self):
        class _Eng:
            params = {0x0114: 1}
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        tcs._engine = _Eng()
        assert tcs.handle_command({"command": "fpa_cooler",
                                   "on": True})["success"] is True
        assert tcs._state.cooler_fpa is True

    def test_a_failed_cooler_reports_a_reason(self):
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        tcs._state.cooler_failed = True
        r = tcs.handle_command({"command": "fpa_cooler", "on": True})
        assert r["success"] is False and "message" in r


class TestCoolerAuthority:
    """The flat 40 K lift, and why it is now a curve."""

    def _tcs(self):
        tcs = TCSBasicModel()
        tcs.configure(_cfg("tcs"))
        return tcs

    def test_authority_falls_as_the_sink_warms_in_absolute_terms(self):
        tcs = self._tcs()
        cold_floor = 10.0 - tcs._fpa_achievable_lift_k(10.0, 0.0)
        warm_floor = 20.0 - tcs._fpa_achievable_lift_k(20.0, 0.0)
        assert warm_floor > cold_floor + 8.0, (
            "the achievable floor must track the sink — a flat lift was the "
            "defect")

    def test_imaging_load_costs_authority(self):
        tcs = self._tcs()
        idle = tcs._fpa_achievable_lift_k(12.0, 0.0)
        loaded = tcs._fpa_achievable_lift_k(12.0, tcs._fpa_imaging_load_w)
        assert loaded < idle, "cooling authority must degrade under load"
        assert idle - loaded == pytest.approx(1.1, abs=0.3)

    def test_the_nominal_setpoint_still_closes_with_margin(self):
        """The shipped mission must not move: -15 C from the +12 C sink."""
        tcs = self._tcs()
        floor = 12.0 - tcs._fpa_achievable_lift_k(12.0, 0.0)
        assert floor < -15.0, f"floor {floor:.2f} C cannot hold -15 C"
        assert -15.0 - floor == pytest.approx(5.35, abs=0.5)

    def test_a_warm_sink_takes_the_nominal_setpoint_away(self):
        """The teachable failure: at a +20 C sink the cooler cannot hold -15 C
        and FPA READY will not come up — with 0x0425 saying why."""
        tcs = self._tcs()
        floor = 20.0 - tcs._fpa_achievable_lift_k(20.0, 0.0)
        assert floor > -15.0

    def test_authority_never_exceeds_the_configured_ceiling(self):
        tcs = self._tcs()
        assert tcs._fpa_achievable_lift_k(200.0, 0.0) \
            <= tcs._fpa_cooler_max_lift_k

    def test_floor_and_margin_are_published(self):
        tcs = self._tcs()
        eps = EPSBasicModel()
        eps.configure(_cfg("eps"))
        eps._state.power_lines["fpa_cooler"] = True
        params = {}
        tcs._state.cooler_fpa = True
        orbit = _Orbit()
        for _ in range(60):
            eps.tick(10.0, orbit, params)
            tcs.tick(10.0, orbit, params)
        assert params[0x0424] < -15.0
        assert params[0x0425] == pytest.approx(
            tcs._fpa_cooler_target - params[0x0424], abs=1e-6)


class TestFineDegradedCapture:
    """A 3-wheel FINE set may capture; the product records that it did."""

    def _fine(self, m, degraded):
        params = {
            0x020F: 5, 0x02AA: 0.01, 0x02C3: 0.001, 0x0292: 0.0,
            0x0299: 1.0 if degraded else 0.0, 0x0408: -15.0, 0x040C: 1,
        }
        m.tick(1.0, _Orbit(), params)
        return params

    def test_degraded_fine_is_permitted_to_capture(self):
        m = _ready_to_image(_payload())
        self._fine(m, degraded=True)
        m._state.fpa_ready = True     # the tick's own thermal model is not under test
        r = m.handle_command({"command": "capture", "scene_id": 42})
        assert r["success"] is True, r

    def test_the_product_records_a_degraded_set(self):
        m = _ready_to_image(_payload())
        self._fine(m, degraded=True)
        m._state.fpa_ready = True
        m.handle_command({"command": "capture", "scene_id": 43})
        assert m.find_image(43)["fine_degraded"] is True

        m2 = _ready_to_image(_payload())
        self._fine(m2, degraded=False)
        m2._state.fpa_ready = True
        m2.handle_command({"command": "capture", "scene_id": 44})
        assert m2.find_image(44)["fine_degraded"] is False


# ══════════════════════════════════════════════════════════════════════
# (C) PL-b / PL-c — the image data path
# ══════════════════════════════════════════════════════════════════════

class TestNoPhantomImagingData:

    def test_imaging_alone_stores_nothing(self):
        """Measured on the pre-fix build: 9.735 MB/s with ZERO capture
        commands, filling the 20 GB store in ~33 minutes, against 1.14 MB for
        one real product — a ~9000:1 ratio of phantom to real."""
        m = _ready_to_image(_payload())
        before = m._state.mem_used_mb
        params = {}
        for _ in range(300):
            m.tick(1.0, _Orbit(), params)
        assert m._state.mem_used_mb == pytest.approx(before, abs=1e-9)
        assert m._state.image_catalog == []
        assert params[0x0649] == 0.0, "readout_active must be 0 with no strip"

    def test_readout_runs_only_during_a_commanded_strip(self):
        m = _ready_to_image(_payload())
        params = {}
        m.tick(1.0, _Orbit(), params)
        assert params[0x0607] == 0.0, "line rate before any capture"
        m._state.fpa_ready = True
        assert m.handle_command({"command": "capture", "lines": 2048})["success"]
        m.tick(1.0, _Orbit(), params)
        assert params[0x0607] > 0.0, "readout must run during the strip"
        assert params[0x0649] == 1.0
        # 2048 lines at 500 Hz is 4.1 s.
        for _ in range(6):
            m.tick(1.0, _Orbit(), params)
        assert params[0x0607] == 0.0, "readout must stop when the strip ends"

    def test_capture_books_exactly_its_own_volume(self):
        m = _ready_to_image(_payload())
        before = m._state.mem_used_mb
        r = m.handle_command({"command": "capture", "lines": 2048})
        img = m.find_image(r["scene_id"])
        assert m._state.mem_used_mb - before == pytest.approx(img["size_mb"])
        assert m._state.product_bytes == m.image_size_bytes(img)


class TestImageBytes:

    def _one(self, scene_id=900):
        m = _ready_to_image(_payload())
        assert m.handle_command({"command": "capture",
                                 "scene_id": scene_id})["success"]
        return m

    def test_a_product_has_a_seed_and_a_block_count(self):
        m = self._one()
        img = m.find_image(900)
        assert img["data_seed"] > 0
        assert m.image_block_count(img) > 100

    def test_blocks_are_deterministic_and_seekable(self):
        m = self._one()
        assert m.image_block(900, 17) == m.image_block(900, 17)
        assert m.image_block(900, 17) != m.image_block(900, 18)
        assert len(m.image_block(900, 17)) == PayloadBasicModel.BLOCK_BYTES

    def test_blocks_differ_between_products(self):
        m = self._one(900)
        m.handle_command({"command": "capture", "scene_id": 901})
        assert m.image_block(900, 3) != m.image_block(901, 3)

    def test_out_of_range_and_unknown_scenes_return_none(self):
        m = self._one()
        assert m.image_block(900, 10 ** 9) is None
        assert m.image_block(4242, 0) is None

    def test_the_whole_product_checksums_to_the_catalogue_value(self):
        m = self._one()
        img = m.find_image(900)
        crc = 0
        for i in range(m.image_block_count(img)):
            crc = zlib.crc32(m.image_block(900, i), crc)
        assert crc == m.image_checksum(900)

    def test_a_corrupt_product_fails_its_own_checksum(self):
        """The point of the corrupt-image failure injection: the received
        bytes must disagree with the checksum the instrument recorded."""
        m = _ready_to_image(_payload())
        m.inject_failure("image_corrupt", 1, count=1)
        m.handle_command({"command": "capture", "scene_id": 950})
        img = m.find_image(950)
        assert img["status"] == 2
        recorded = m.image_checksum(950)
        crc = 0
        for i in range(m.image_block_count(img)):
            crc = zlib.crc32(m.image_block(950, i), crc)
        assert crc != recorded

    def test_release_frees_the_storage(self):
        m = self._one()
        used = m._state.mem_used_mb
        size = m.find_image(900)["size_mb"]
        r = m.release_image(900)
        assert r["success"] is True
        assert m.find_image(900) is None
        assert m._state.mem_used_mb == pytest.approx(used - size)
        assert m._state.product_bytes == 0

    def test_release_of_an_unknown_scene_is_refused(self):
        assert self._one().release_image(4242)["success"] is False


class TestTransferEngine:

    def _armed(self):
        m = _ready_to_image(_payload())
        m.handle_command({"command": "capture", "scene_id": 700})
        return m

    def test_transfer_is_sized_from_the_product_and_reports_a_checksum(self):
        m = self._armed()
        r = m.handle_command({"command": "initiate_transfer", "scene_id": 700})
        assert r["success"] is True
        assert r["bytes_total"] == m.image_size_bytes(m.find_image(700))
        assert r["blocks"] == m.image_block_count(m.find_image(700))
        assert r["checksum"] == m.image_checksum(700)

    def test_transfer_of_an_unknown_scene_is_refused(self):
        r = self._armed().handle_command({"command": "initiate_transfer",
                                          "scene_id": 4242})
        assert r["success"] is False and r["error_code"] == 0x0003

    def test_a_second_transfer_is_refused_while_one_runs(self):
        m = self._armed()
        m.handle_command({"command": "capture", "scene_id": 701})
        assert m.handle_command({"command": "initiate_transfer",
                                 "scene_id": 700})["success"]
        r = m.handle_command({"command": "initiate_transfer", "scene_id": 701})
        assert r["success"] is False and r["error_code"] == 0x0005

    def test_transfer_drains_at_the_active_tm_rate_not_10_mbps(self):
        """Was a hard-coded 10 Mbps with no reference to contact, the PA or
        the rate ladder — 78x the spacecraft's own top tier."""
        m = self._armed()
        m.handle_command({"command": "initiate_transfer", "scene_id": 700})
        params = {0x0506: 128000.0, 0x0501: 1.0}
        m.tick(10.0, _Orbit(), params)
        assert m._state.transfer_bytes_sent == pytest.approx(160000.0)
        assert m._state.transfer_rate_mbps == pytest.approx(0.128)

    def test_transfer_stalls_when_the_link_drops(self):
        m = self._armed()
        m.handle_command({"command": "initiate_transfer", "scene_id": 700})
        params = {0x0506: 128000.0, 0x0501: 0.0}
        m.tick(60.0, _Orbit(), params)
        assert m._state.transfer_bytes_sent == 0.0
        assert m._state.transfer_active is True


class TestEventTable:

    def test_every_emitted_event_has_a_name(self):
        """0x060E, 0x060F and 0x0611 were emitted by the model and declared
        nowhere, so all three downlinked as "UNKNOWN"."""
        for evt in (0x060E, 0x060F, 0x0610, 0x0611):
            assert PAYLOAD_EVENTS[evt][0] != "UNKNOWN"

    def test_the_model_table_matches_the_event_catalogue(self):
        cat = yaml.safe_load(
            (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())
        rows = cat.get("events", cat)
        declared = {int(e["id"]): (e["name"], e["severity"])
                    for e in rows
                    if 0x0600 <= int(e["id"]) <= 0x06FF}
        assert declared == PAYLOAD_EVENTS, (
            "payload_basic.PAYLOAD_EVENTS and event_catalog.yaml have drifted")

    def test_transfer_start_is_actually_emitted(self):
        """A source comment promised "will be emitted in next tick"; nothing
        anywhere sent it."""
        class _Q:
            def __init__(self):
                self.items = []

            def put_nowait(self, item):
                self.items.append(item)

        class _Eng:
            pass

        m = _ready_to_image(_payload())
        eng = _Eng()
        eng._model_event_queue = _Q()
        m._engine = eng
        m.handle_command({"command": "capture", "scene_id": 800})
        m.handle_command({"command": "initiate_transfer", "scene_id": 800})
        ids = [e["event_id"] for e in eng._model_event_queue.items]
        assert 0x0613 in ids, "IMAGE_CAPTURED"
        assert 0x0610 in ids, "TRANSFER_START"


class TestNewTelemetryIsDeclared:

    def test_the_new_payload_params_are_in_the_catalogue(self):
        declared = {
            int(p["id"])
            for p in yaml.safe_load(
                (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text()
            )["parameters"]
        }
        for pid in (0x0644, 0x0645, 0x0646, 0x0647, 0x0648, 0x0649,
                    0x064A, 0x064B):
            assert pid in declared, f"0x{pid:04X} not in parameters.yaml"

    def test_hk_sid_5_carries_them(self):
        sids = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "hk_structures.yaml").read_text())
        rows = sids.get("structures", sids)
        sid5 = next(s for s in rows if s["sid"] == 5)
        carried = {int(p["param_id"]) for p in sid5["parameters"]}
        for pid in (0x0644, 0x0645, 0x0646, 0x0647, 0x0648, 0x0649,
                    0x064A, 0x064B):
            assert pid in carried, f"0x{pid:04X} not in HK SID 5"

    def test_the_model_publishes_them(self):
        m = _ready_to_image(_payload())
        params = {}
        m.tick(1.0, _Orbit(), params)
        for pid in (0x0644, 0x0645, 0x0646, 0x0647, 0x0648, 0x0649,
                    0x064A, 0x064B):
            assert pid in params, f"0x{pid:04X} never written"
