"""Defect #45 — planner activity command sequences must route to the
subsystems they claim to command.

The engine routes S8.1 functions by numeric range
(smo_simulator/service_dispatch.py `_handle_s8`):
  0-15 AOCS · 16-25 EPS · 26-39 payload · 40-49 TCS · 50-62 OBDH ·
  63-79 TTC · 80 OBDH · 81-83 EPS · 84 AOCS.
(The TTC range runs to 79 inclusive since defect #85 added func 79,
TTC_SET_DOWNLINK_MODE — `service_dispatch._handle_s8` routes `range(63, 80)`.)

Previously imaging carried 0x14/0x16 (EPS no-ops), data_dump carried 0x32
(OBDH set-mode), etc. These tests are table-driven over ALL activity types
and also drive the real ServiceDispatcher to prove each func id lands on an
implemented handler of the right subsystem.
"""
import struct
from pathlib import Path

import pytest
import yaml

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


def _load_activity_types() -> list[dict]:
    with open(CONFIG_DIR / "planning" / "activity_types.yaml") as f:
        return yaml.safe_load(f)["activity_types"]


def _route_subsystem(func_id: int) -> str | None:
    """Mirror of service_dispatch._handle_s8 range routing."""
    if 0 <= func_id < 16 or func_id == 84:
        return "aocs"
    if 16 <= func_id < 26 or func_id in (81, 82, 83):
        return "eps"
    if 26 <= func_id < 40:
        return "payload"
    if 40 <= func_id < 50:
        return "tcs"
    if 50 <= func_id < 63 or func_id == 80:
        return "obdh"
    if 63 <= func_id < 80:
        return "ttc"
    if 108 <= func_id < 119:
        return "prop"          # Phase D propulsion block
    return None


def _s8_steps(activity: dict) -> list[dict]:
    return [s for s in activity.get("command_sequence", [])
            if s.get("service") == 8]


def _func_id(step: dict) -> int:
    fid = step["func_id"]
    if isinstance(fid, str):
        fid = int(fid, 16) if fid.startswith("0x") else int(fid)
    return int(fid)


# Expected S8 func ids per activity type — verified against
# service_dispatch.py handlers (set_mode=26, capture=28, gyro cal=13,
# tm rate=65, auto mode=45, load shed=25, aocs set-mode=0, desat=1,
# eps set-mode=83).
EXPECTED_S8_FUNCS = {
    "imaging_pass": [26, 28],
    # The HIGH tier is gated: enter DOWNLINK mode (79), raise the rate (65),
    # dump, then leave DOWNLINK mode so the active tier falls back to LOW.
    "data_dump": [79, 65, 79],
    "calibration": [13],
    "housekeeping_collection": [],
    "software_upload": [],
    "momentum_desaturation": [1],
    "thermal_rebalance": [45],
    "load_shedding": [25],
    "fdir_recovery": [0, 83],
    # Phase D: prop-line on (19), catbed preheat (42), set burn / arm /
    # execute / disarm (110/108/111/109), heater off (42).
    "orbit_manoeuvre": [19, 42, 110, 108, 111, 109, 42],
}


class TestActivityFuncIdTable:
    """Table-driven checks over every activity type in the config."""

    @pytest.mark.parametrize("activity", _load_activity_types(),
                             ids=lambda a: a["name"])
    def test_s8_funcs_route_to_a_known_subsystem(self, activity):
        for step in _s8_steps(activity):
            sub = _route_subsystem(_func_id(step))
            assert sub is not None, (
                f"{activity['name']}: func {_func_id(step)} falls outside "
                "every implemented S8 routing range")

    @pytest.mark.parametrize("activity", _load_activity_types(),
                             ids=lambda a: a["name"])
    def test_s8_funcs_route_to_a_required_subsystem(self, activity):
        required = set(activity.get("requires_subsystems") or [])
        if not required:
            pytest.skip("activity declares no required subsystems")
        for step in _s8_steps(activity):
            sub = _route_subsystem(_func_id(step))
            assert sub in required, (
                f"{activity['name']}: '{step.get('description')}' "
                f"(func {_func_id(step)}) routes to {sub}, but the activity "
                f"requires {sorted(required)}")

    @pytest.mark.parametrize("activity", _load_activity_types(),
                             ids=lambda a: a["name"])
    def test_expected_func_ids(self, activity):
        name = activity["name"]
        assert name in EXPECTED_S8_FUNCS, f"unknown activity type {name}"
        assert [_func_id(s) for s in _s8_steps(activity)] == \
            EXPECTED_S8_FUNCS[name]

    def test_imaging_wait_for_uses_real_telemetry_parameter(self):
        """payload.imaging_active never existed as telemetry; the wait must
        poll payload.mode (param 0x0600)."""
        for activity in _load_activity_types():
            for step in activity.get("command_sequence", []):
                wf = step.get("wait_for")
                if wf:
                    assert wf["parameter"] != "payload.imaging_active"

    def test_software_upload_s6_load_carries_address_and_data(self):
        act = next(a for a in _load_activity_types()
                   if a["name"] == "software_upload")
        step = next(s for s in act["command_sequence"]
                    if s.get("service") == 6)
        assert step["subtype"] == 2
        assert int(step["address"]) >= 0x00020000, \
            "address must be outside the read-only Boot ROM"
        assert step.get("data"), "S6.2 without data is a silent no-op"


class TestDispatcherRouting:
    """Drive the real ServiceDispatcher: every activity S8 command must hit
    handle_command on the subsystem the activity requires."""

    @pytest.mark.parametrize("activity",
                             [a for a in _load_activity_types()
                              if _s8_steps(a)],
                             ids=lambda a: a["name"])
    def test_dispatch_hits_required_subsystem(self, activity,
                                              service_dispatcher,
                                              mock_engine):
        for step in _s8_steps(activity):
            fid = _func_id(step)
            data = bytearray([fid])
            for p in step.get("params", []):
                data.append(int(p) & 0xFF)
            expected_sub = _route_subsystem(fid)
            target = mock_engine.subsystems[expected_sub]
            target.handle_command.reset_mock()
            service_dispatcher._handle_s8(1, bytes(data))
            assert target.handle_command.called, (
                f"{activity['name']}: func {fid} did not reach the "
                f"{expected_sub} model — unimplemented or misrouted")


class TestImagingPlannerSequence:
    """The imaging planner's generated sequence uses real payload funcs and
    its lat/lon parameters survive into the TC data (defect #45/C)."""

    def _sequence(self):
        from smo_planner.imaging_planner import ImagingPlanner
        planner = ImagingPlanner([{
            "id": "T1", "name": "Test", "priority": "high",
            "region": {"min_lat": 10.0, "max_lat": 15.0,
                       "min_lon": -35.5, "max_lon": -25.0},
        }])
        return planner.generate_capture_sequence(
            "T1", "2026-03-11T10:00:00Z")["command_sequence"]

    def test_generated_funcs_route_to_payload(self):
        seq = self._sequence()
        funcs = [_func_id(s) for s in seq if s.get("service") == 8]
        assert funcs == [26, 28]
        for f in funcs:
            assert _route_subsystem(f) == "payload"

    def test_capture_scene_and_lines_encoded_as_uint16(self):
        """Phase E WP-1: the capture step carries scene_id + lines through
        the shared catalogue encoder (geodesy is stamped on board, not
        commanded)."""
        from smo_mcs.procedure_runner import ProcedureRunner
        seq = self._sequence()
        capture = [s for s in seq if s.get("service") == 8][-1]
        data = bytes.fromhex(ProcedureRunner._build_data_hex(capture))
        assert data[0] == 28
        scene_id, lines = struct.unpack(">HH", data[1:5])
        assert scene_id == capture["args"]["scene_id"] > 0
        assert lines == 2048
        assert len(data) == 5
