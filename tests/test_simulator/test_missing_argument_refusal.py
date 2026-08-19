"""Command safety — a telecommand with its argument byte MISSING is REFUSED.

Franz's report: "in application mode but cannot get beacon mode off to get HK
through". The cause was in the dispatcher's argument extraction, not in the
TT&C model. Every S8.1 route read its argument as

    on = bool(data[0]) if data else True

so a telecommand that arrived with no argument byte was executed with a
SUBSTITUTED value — and for twelve of them the substituted value was the
dangerous direction. ``TTC_SET_BEACON_MODE`` (func 70) with no argument turned
beacon mode ON, which pins the S-band TM rate ladder to the SAFE (1 kbps)
rung, and reported a clean S1.1 / S1.3 / S1.7 while doing it. The operator saw
success while the spacecraft did the opposite of what they meant.

The house rule (defect #87, and TTC_SET_TX_POWER's own refusal) is: reject
with a stated reason, never clamp or default silently. These tests pin it:

* every fixed command, sent with an EMPTY data field, produces a refusal that
  NAMES the missing field — and never an S1.7 "execution complete";
* the owner's case specifically: a bare TTC_SET_BEACON_MODE does not latch
  beacon mode on;
* the correct forms still work (on=0 clears beacon mode, on=1 sets it);
* out-of-range arguments are refused rather than clamped.
"""
import os
import struct
from pathlib import Path

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_common.protocol.pus_services import VerificationData
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture
def engine(tmp_path):
    """A commissioned engine with the link up and every power line hot.

    Everything is powered so the ACCEPTANCE gates cannot pre-empt the
    execution-stage refusal these tests are about.
    """
    os.environ.setdefault("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng._in_contact = True
    eng.params[0x0501] = 2
    eng.subsystems["obdh"]._state.sw_image = 1
    eps = eng.subsystems["eps"]
    for line in list(eps._state.power_lines):
        eps._state.power_lines[line] = True
    ttc = eng.subsystems["ttc"]
    ttc._state.antenna_deployed = True
    ttc._state.beacon_mode = False
    # AOCS and the payload out of MODE_OFF: their acceptance-stage mode gate
    # is a different (and correct) refusal, and it would mask the argument
    # refusal these tests are about.
    eng.subsystems["aocs"]._state.mode = 4          # NOMINAL_NADIR
    eng.subsystems["payload"]._state.mode = 1       # STANDBY
    eng.params[0x0115] = 1                          # battery heater line
    eng.params[0x0116] = 1                          # OBC heater line
    eng.params[0x014A] = 1                          # thruster heater line
    return eng


def _drain(engine):
    out = []
    while not engine.tm_queue.empty():
        parsed = decommutate_packet(engine.tm_queue.get_nowait())
        if parsed and parsed.secondary:
            out.append(parsed)
    return out


def _send(engine, data, service=8, subtype=1):
    _drain(engine)
    engine._dispatch_tc(build_tc_packet(apid=100, service=service,
                                        subtype=subtype, data=data))
    return _drain(engine)


def _of(packets, service, subtype):
    return [p for p in packets
            if p.secondary.service == service and p.secondary.subtype == subtype]


def _failure_reason(packets):
    """The failure notice from whichever S1 failure report was produced."""
    for subtype in (8, 4, 2):
        got = _of(packets, 1, subtype)
        if got:
            return VerificationData.parse(got[0].data_field, subtype).reason
    return None


# ── the commands whose argument byte is now MANDATORY ───────────────────
#
# (func_id, catalogue name, field name that must be named in the refusal,
#  the value the handler USED to substitute when the byte was absent)

MANDATORY_U8 = [
    # ── the twelve boolean sites of the original report ──
    (116, "PROP_INHIBIT_SET", "on", "True (inhibit SET)"),
    (4, "ST1_POWER", "on", "True (ST1 powered ON)"),
    (5, "ST2_POWER", "on", "True (ST2 powered ON)"),
    (9, "MTQ_ENABLE", "enable", "True (magnetorquers ENABLED)"),
    (96, "AOCS_SET_DESAT_INHIBIT", "inhibit", "True (desaturation INHIBITED)"),
    (18, "EPS_TRANSPONDER_TX", "on", "True (transponder TX ON)"),
    (40, "HEATER_BATTERY", "on", "True (battery heater ON)"),
    (41, "HEATER_OBC", "on", "True (OBC heater ON)"),
    (42, "HEATER_THRUSTER", "on", "True (thruster heater ON)"),
    (43, "FPA_COOLER", "on", "True (cryocooler ON)"),
    (56, "OBC_BOOT_INHIBIT", "inhibit", "True (auto-boot INHIBITED)"),
    (70, "TTC_SET_BEACON_MODE", "on", "True (beacon mode ON — the field case)"),
    (78, "TTC_SET_COHERENT_MODE", "on", "True (coherent mode ON)"),
    (79, "TTC_SET_DOWNLINK_MODE", "on", "True (DOWNLINK mode ENTERED)"),
    # ── the wider sweep: same defect class, non-boolean arguments ──
    (0, "AOCS_SET_MODE", "mode", "0 (MODE_OFF)"),
    (2, "AOCS_DISABLE_WHEEL", "wheel_idx", "0 (wheel 0)"),
    (3, "AOCS_ENABLE_WHEEL", "wheel_idx", "0 (wheel 0)"),
    (6, "ST_SELECT", "unit", "1 (ST1)"),
    (14, "AOCS_RW_RAMP_DOWN", "wheel_idx", "255 (ALL wheels)"),
    (84, "AOCS_GPS_SET_START_MODE", "start_mode", "0 (COLD)"),
    (85, "AOCS_SET_POINTING_FRAME", "frame", "1 (LVLH_NADIR)"),
    (90, "AOCS_SET_MOM_MGMT", "enable", "False (management disabled)"),
    (91, "AOCS_EST_RESET", "mode", "0 (COLD reset)"),
    (16, "EPS_PAYLOAD_MODE", "mode", "0 (payload OFF)"),
    (17, "EPS_FPA_COOLER", "on", "False (cooler OFF)"),
    (19, "EPS_POWER_ON", "line_index", "silent no-op"),
    (20, "EPS_POWER_OFF", "line_index", "silent no-op"),
    (21, "EPS_RESET_OC_FLAG", "line_index", "silent no-op"),
    (25, "EPS_EMERGENCY_LOAD_SHED", "stage", "silent no-op"),
    (81, "EPS_DEPLOY_WING", "wing", "'both' (BOTH wings deployed)"),
    (83, "EPS_SET_MODE", "mode", "1 (SAFE)"),
    (26, "PAYLOAD_SET_MODE", "mode", "0 (payload OFF)"),
    (31, "PAYLOAD_MARK_BAD_SEGMENT", "segment_id", "0 (segment 0)"),
    (33, "PAYLOAD_SET_BAND_CONFIG", "mask", "0x0F (all bands)"),
    (37, "PAYLOAD_START_CALIBRATION", "mode", "2 (both)"),
    (92, "PAYLOAD_SELECT_FILTER", "position", "0"),
    (93, "PAYLOAD_CYCLE_SHUTTER", "cycles", "1"),
    (95, "PAYLOAD_CAL_LAMP", "on", "False"),
    (119, "PAYLOAD_SET_SHUTTER", "position", "1 (OPEN)"),
    (45, "HEATER_AUTO_MODE", "circuit", "0 (battery)"),
    (50, "OBC_SET_MODE", "mode", "0 (nominal)"),
    (54, "OBC_SELECT_BUS", "bus", "0 (Bus A)"),
    (74, "TTC_SET_MODULATION", "mode", "0 (BPSK)"),
    (113, "PROP_ISO_OPEN", "branch", "2 (BOTH branches OPENED)"),
    (114, "PROP_ISO_CLOSE", "branch", "2 (both)"),
]

#: Commands whose multi-octet argument used to fall through a bare
#: ``if len(data) >= N:`` with no ``else`` — a short TC did NOTHING and still
#: verified S1.7 "execution complete".
MANDATORY_MULTI = [
    (8, "RW_SET_SPEED_BIAS", "wheel_idx"),
    (10, "AOCS_SLEW_TO", "q_x"),
    (22, "EPS_SWITCH_LOAD", "load_id"),
    (23, "EPS_SET_CHARGE_RATE", "rate_a"),
    (24, "EPS_SOLAR_ARRAY_DRIVE", "angle_deg"),
    (27, "PAYLOAD_SET_SCENE", "scene_id"),
    (29, "PAYLOAD_DOWNLOAD_IMAGE", "scene_id"),
    (30, "PAYLOAD_DELETE_IMAGE", "scene_id"),
    (94, "PAYLOAD_INITIATE_TRANSFER", "scene_id"),
    (44, "HEATER_SET_SETPOINT", "circuit"),
    (46, "TCS_SET_HEATER_DUTY_LIMIT", "circuit"),
    (47, "TCS_DECONTAMINATION_START", "target_temp_c"),
    (58, "OBC_SET_WATCHDOG_PERIOD", "period_ticks"),
    (72, "TTC_SET_UL_FREQ", "freq_mhz"),
    (73, "TTC_SET_DL_FREQ", "freq_mhz"),
    (75, "TTC_SET_RX_GAIN", "agc_db"),
    (115, "PROP_SET_LIMITS", "att_err_max_deg"),
]


class TestMissingArgumentIsRefused:
    @pytest.mark.parametrize(
        "func_id,name,field,old_default",
        MANDATORY_U8,
        ids=[f"{f}-{n}" for f, n, _, _ in MANDATORY_U8])
    def test_empty_data_field_is_refused_by_name(
            self, engine, func_id, name, field, old_default):
        """No argument byte -> a refusal that names the field, not `old_default`."""
        tm = _send(engine, bytes([func_id]))
        reason = _failure_reason(tm)
        assert reason, (
            f"{name} (func {func_id}) with no argument produced NO failure "
            f"report — it used to be executed as {old_default}")
        assert field in reason, (
            f"{name} refusal must name the missing field {field!r}: {reason!r}")
        assert _of(tm, 1, 7) == [], (
            f"{name} was refused AND reported execution complete")

    @pytest.mark.parametrize(
        "func_id,name,field", MANDATORY_MULTI,
        ids=[f"{f}-{n}" for f, n, _ in MANDATORY_MULTI])
    def test_short_data_field_is_refused_by_name(
            self, engine, func_id, name, field):
        """A short multi-octet argument is refused, not silently ignored."""
        tm = _send(engine, bytes([func_id]))
        reason = _failure_reason(tm)
        assert reason, (
            f"{name} (func {func_id}) with no arguments produced no failure "
            f"report — a short TC used to be a silent no-op with S1.7 OK")
        assert field in reason, (
            f"{name} refusal must name {field!r}: {reason!r}")
        assert _of(tm, 1, 7) == []

    def test_every_refusal_uses_the_bad_argument_code(self, engine):
        """Same code convention as the TTC_SET_TX_POWER refusal: 0x0003."""
        for func_id, name, _field, _old in MANDATORY_U8:
            tm = _send(engine, bytes([func_id]))
            reports = _of(tm, 1, 8) or _of(tm, 1, 4)
            assert reports, name
            subtype = reports[0].secondary.subtype
            vd = VerificationData.parse(reports[0].data_field, subtype)
            assert vd.error_code == 0x0003, (name, hex(vd.error_code))


class TestBeaconModeRegression:
    """The owner's case, end to end."""

    def test_bare_beacon_mode_command_does_not_latch_beacon_mode_on(self, engine):
        ttc = engine.subsystems["ttc"]
        assert ttc._state.beacon_mode is False
        tm = _send(engine, bytes([70]))            # func 70, NO argument byte
        assert ttc._state.beacon_mode is False, (
            "a bare TTC_SET_BEACON_MODE latched beacon mode ON — the S-band "
            "ladder is now pinned to the SAFE rung and HK cannot get through")
        reason = _failure_reason(tm)
        assert reason and "TTC_SET_BEACON_MODE" in reason and "on" in reason
        assert "0=off" in reason and "1=on" in reason
        assert _of(tm, 1, 7) == [], "refused command reported execution complete"

    def test_bare_command_does_not_pin_the_rate_ladder(self, engine):
        """The symptom, not just the flag: the SAFE rung stays unselected."""
        engine.subsystems["ttc"]._state.beacon_mode = False
        _send(engine, bytes([70]))
        for _ in range(3):
            engine._tick_once(1.0)
        assert engine.subsystems["ttc"]._state.beacon_mode is False

    def test_explicit_on_still_sets_beacon_mode(self, engine):
        tm = _send(engine, bytes([70, 1]))
        assert engine.subsystems["ttc"]._state.beacon_mode is True
        assert _of(tm, 1, 8) == [], "TTC_SET_BEACON_MODE on=1 was refused"

    def test_explicit_off_still_clears_beacon_mode(self, engine):
        engine.subsystems["ttc"]._state.beacon_mode = True
        tm = _send(engine, bytes([70, 0]))
        assert engine.subsystems["ttc"]._state.beacon_mode is False, (
            "TTC_SET_BEACON_MODE on=0 must clear beacon mode — this is the "
            "command the owner could not get to work")
        assert _of(tm, 1, 8) == []

    def test_off_then_on_round_trip(self, engine):
        _send(engine, bytes([70, 1]))
        assert engine.subsystems["ttc"]._state.beacon_mode is True
        _send(engine, bytes([70, 0]))
        assert engine.subsystems["ttc"]._state.beacon_mode is False


class TestCorrectFormsStillWork:
    """The fix must not cost the well-formed telecommand anything."""

    def test_heater_on_and_off(self, engine):
        tcs = engine.subsystems["tcs"]
        _send(engine, bytes([40, 1]))
        assert tcs._state.htr_battery is True
        _send(engine, bytes([40, 0]))
        assert tcs._state.htr_battery is False

    def test_desat_inhibit_both_directions(self, engine):
        aocs = engine.subsystems["aocs"]
        _send(engine, bytes([96, 1]))
        assert bool(getattr(aocs._state, "desat_inhibit", False)) is True
        _send(engine, bytes([96, 0]))
        assert bool(getattr(aocs._state, "desat_inhibit", True)) is False

    def test_downlink_mode_both_directions(self, engine):
        ttc = engine.subsystems["ttc"]
        _send(engine, bytes([79, 1]))
        assert bool(getattr(ttc._state, "downlink_mode", False)) is True
        _send(engine, bytes([79, 0]))
        assert bool(getattr(ttc._state, "downlink_mode", True)) is False

    def test_multi_octet_command_with_full_arguments_is_accepted(self, engine):
        tm = _send(engine, bytes([58]) + struct.pack('>H', 30))
        assert _of(tm, 1, 8) == [] and _of(tm, 1, 4) == []


class TestOutOfRangeIsRefusedNotClamped:
    """The reverse direction: an argument that IS present but out of range."""

    def test_solar_array_drive_angle_is_refused_not_clamped(self, engine):
        eps = engine.subsystems["eps"]
        before = float(getattr(eps, "_sada_target_angle", 0.0))
        tm = _send(engine, bytes([24]) + struct.pack('>h', 12000))  # +120 deg
        reason = _failure_reason(tm)
        assert reason and "EPS_SOLAR_ARRAY_DRIVE" in reason
        assert float(getattr(eps, "_sada_target_angle", 0.0)) == before, (
            "the drive angle was clamped to the stop instead of refused")

    def test_heater_duty_limit_over_100_is_refused(self, engine):
        tm = _send(engine, bytes([46, 0, 220]))
        reason = _failure_reason(tm)
        assert reason and "max_duty_pct" in reason

    def test_watchdog_period_zero_is_refused_not_promoted(self, engine):
        obdh = engine.subsystems["obdh"]
        before = obdh._state.watchdog_period
        tm = _send(engine, bytes([58]) + struct.pack('>H', 0))
        reason = _failure_reason(tm)
        assert reason and "period_ticks" in reason
        assert obdh._state.watchdog_period == before

    def test_unknown_wing_code_is_refused_not_folded_onto_both(self, engine):
        tm = _send(engine, bytes([81, 7]))
        reason = _failure_reason(tm)
        assert reason and "wing" in reason
