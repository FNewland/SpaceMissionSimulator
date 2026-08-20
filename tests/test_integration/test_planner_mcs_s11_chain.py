"""End-to-end commanding chain: planner activity → MCS S11 upload →
simulator on-board schedule → time-tagged execution (defects #44-#46).

Drives the REAL components of every hop except the TCP transport:
  1. The imaging activity's command_sequence from the real
     ``activity_types.yaml`` / ``ImagingPlanner``.
  2. The real MCS ``/api/schedule/upload-s11`` handler builds and "uplinks"
     TC(11,4) packets (captured from the framed byte stream).
  3. The real SimulationEngine accepts them, stores the embedded TCs in its
     S11 scheduler, and executes them once the OBC clock passes the tags —
     with real S1 verification.
"""
import struct
from datetime import timedelta, timezone
from unittest.mock import patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_common.protocol.ecss_packet import TIME_EPOCH, decommutate_packet
from smo_common.protocol.framing import FRAME_HEADER_SIZE
from smo_mcs.server import MCSServer
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = "configs/eosat1"


class _FakeWriter:
    def __init__(self):
        self.buffer = b""

    def is_closing(self):
        return False

    def write(self, data):
        self.buffer += data

    async def drain(self):
        pass

    def frames(self):
        out, buf = [], self.buffer
        while len(buf) >= FRAME_HEADER_SIZE:
            n = struct.unpack(">H", buf[:FRAME_HEADER_SIZE])[0]
            out.append(buf[FRAME_HEADER_SIZE:FRAME_HEADER_SIZE + n])
            buf = buf[FRAME_HEADER_SIZE + n:]
        return out


def _make_mcs():
    with patch("smo_mcs.server.load_mcs_displays",
               return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        return MCSServer(config_dir="/tmp/fake_config",
                         connect_host="localhost", connect_port=9999,
                         http_port=0)


def _make_engine():
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6
    eng._override_passes = True
    eps = eng.subsystems["eps"]
    if hasattr(eps, "_state") and hasattr(eps._state, "power_lines"):
        eps._state.power_lines["payload"] = True
    return eng


def _cuc_to_iso(cuc: int) -> str:
    return (TIME_EPOCH + timedelta(seconds=cuc)).replace(
        tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_imaging_activity_executes_on_board_at_planned_time():
    engine = _make_engine()
    payload = engine.subsystems["payload"]
    payload._state.fpa_ready = True
    # Phase C WP-6: capture is gated on AOCS FINE_POINT with pointing error
    # inside the gate. These tests exercise the planner->MCS->S11 chain, not
    # the pointing chain — pin the AOCS telemetry to a compliant FINE state.
    engine.subsystems["aocs"].tick = lambda dt, o, p: p.update(
        {0x020F: 5, 0x02AA: 0.01, 0x02C3: 0.001, 0x0292: 0, 0x02C8: 15.0})
    payload._state.aocs_seen = True
    payload._state.aocs_mode_seen = 5
    payload._state.aocs_total_err_seen = 0.01
    payload._state.aocs_rate_stab_seen = 0.001
    payload._state.aocs_settled_seen = True
    payload._state.mode = 0
    images_before = payload._state.image_count

    # 1. Planner side: real activity definition.
    with open(f"{CONFIG_DIR}/planning/activity_types.yaml") as f:
        activity = next(a for a in yaml.safe_load(f)["activity_types"]
                        if a["name"] == "imaging_pass")
    start_cuc = engine._get_cuc_time() + 120
    start_iso = _cuc_to_iso(start_cuc)

    # 2. MCS side: real S11 upload handler.
    mcs = _make_mcs()
    writer = _FakeWriter()
    mcs._tc_writer = writer
    app = web.Application()
    app.router.add_post("/api/schedule/upload-s11",
                        mcs._handle_s11_schedule_upload)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post("/api/schedule/upload-s11", json={
            "name": activity["name"],
            "start_time": start_iso,
            "steps": activity["command_sequence"],
            "procedure_ref": activity.get("procedure_ref", ""),
        })
        body = await resp.json()
        assert resp.status == 200
        # 2026-08b PL-a: three commandable steps now, not two. The sequence
        # gained an AOCS_SET_MODE(5 = FINE_POINT) at the front — without it
        # the payload's own capture gate refused every planner-scheduled
        # image ("Capture refused: AOCS not in FINE_POINT"), and
        # activity_types.yaml's precondition asked for mode 1 (SAFE_BOOT).
        # 2026-08-19: the imaging expansion is now the PHYSICALLY COMPLETE
        # sequence, not the three-command minimum. The owner flew it and found
        # the old form unflyable: it desaturated immediately before the capture
        # (20-50 min, and it INHIBITS imaging) and left no settle time to reach
        # FINE_POINT. It now walks NOMINAL -> payload STANDBY -> FINE_POINT ->
        # settle -> IMAGING -> shutter OPEN -> capture -> readout, then
        # restores. Assert the SHAPE, not a magic number: a bare count pin
        # taught us nothing when it broke.
        n_cmds = body["count"]
        assert n_cmds >= 3, "expansion collapsed"

        # 3. Simulator side: uplink each frame into the real engine.
        frames = writer.frames()
        assert len(frames) == n_cmds
        for raw in frames:
            engine._dispatch_tc(raw)
        assert engine._tc_scheduler.get_status()["count"] == n_cmds

        # Nothing fires before the planned time.
        due = engine._tc_scheduler.tick(start_cuc - 10)
        assert due == []
        assert payload._state.mode == 0

        # The sequence runs in ORDER, spread over its real settle time. Walk
        # the on-board clock forward rather than pinning commands to guessed
        # offsets (the old +1/+30 pins encoded the unflyable 3-command form).
        # The payload must reach IMAGING before any capture fires.
        saw_imaging_before_capture = False
        for t in range(1, 601):
            for pkt in engine._tc_scheduler.tick(start_cuc + t):
                engine._dispatch_tc(pkt)
            if payload._state.mode == 2 and \
                    payload._state.image_count == images_before:
                saw_imaging_before_capture = True
            if engine._tc_scheduler.get_status()["count"] == 0:
                break

        assert saw_imaging_before_capture, \
            "capture fired without the payload first reaching IMAGING"
        assert payload._state.image_count == images_before + 1
        assert engine._tc_scheduler.get_status()["count"] == 0

        # S1.7 execution-complete verification reached the (open) downlink.
        completions = 0
        while not engine.tm_queue.empty():
            p = decommutate_packet(engine.tm_queue.get_nowait())
            if p and p.secondary and p.secondary.service == 1 \
                    and p.secondary.subtype == 7:
                completions += 1
        assert completions >= n_cmds  # every embedded command completed
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_imaging_planner_capture_receives_the_commanded_scene():
    """ImagingPlanner-generated sequence: the planner-ASSIGNED scene_id and
    lines must survive the whole chain into the payload's image catalogue
    (Phase E WP-1 — the send-no-params defect made every planned image an
    auto-numbered (0,0)-stamped capture)."""
    from smo_planner.imaging_planner import ImagingPlanner

    engine = _make_engine()
    payload = engine.subsystems["payload"]
    payload._state.fpa_ready = True
    # Phase C WP-6: capture is gated on AOCS FINE_POINT with pointing error
    # inside the gate. These tests exercise the planner->MCS->S11 chain, not
    # the pointing chain — pin the AOCS telemetry to a compliant FINE state.
    engine.subsystems["aocs"].tick = lambda dt, o, p: p.update(
        {0x020F: 5, 0x02AA: 0.01, 0x02C3: 0.001, 0x0292: 0, 0x02C8: 15.0})
    payload._state.aocs_seen = True
    payload._state.aocs_mode_seen = 5
    payload._state.aocs_total_err_seen = 0.01
    payload._state.aocs_rate_stab_seen = 0.001
    payload._state.aocs_settled_seen = True
    payload._state.mode = 2

    planner = ImagingPlanner([{
        "id": "T1", "name": "Test", "priority": "high",
        "region": {"min_lat": 40.0, "max_lat": 51.0,
                   "min_lon": -76.0, "max_lon": -74.5},
    }])
    capture = planner.generate_capture_sequence(
        "T1", _cuc_to_iso(engine._get_cuc_time() + 60))

    mcs = _make_mcs()
    writer = _FakeWriter()
    mcs._tc_writer = writer
    app = web.Application()
    app.router.add_post("/api/schedule/upload-s11",
                        mcs._handle_s11_schedule_upload)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post("/api/schedule/upload-s11", json={
            "name": capture["name"],
            "start_time": capture["start_time"],
            "steps": capture["command_sequence"],
        })
        assert resp.status == 200

        for raw in writer.frames():
            engine._dispatch_tc(raw)
        for pkt in engine._tc_scheduler.tick(engine._get_cuc_time() + 3600):
            engine._dispatch_tc(pkt)

        # The capture executed and the catalogue entry carries the
        # PLANNER-ASSIGNED product id and strip length...
        assert payload._state.image_count >= 1
        planned_scene = capture["scene_id"]
        assert planned_scene > 0
        entry = next(img for img in payload._state.image_catalog
                     if img["scene_id"] == planned_scene)
        assert entry["lines"] == capture["lines"] == 2048
        assert payload._state.last_scene_id == planned_scene
        # ...and geolocation was stamped ON BOARD from the ephemeris at
        # shutter open (a real number from orbit_state, not a TC argument;
        # the engine had not ticked the orbit here, so the stamp equals the
        # payload's last-sampled sub-satellite point).
        assert entry["lat"] == pytest.approx(
            payload._state.orbit_lat_deg, abs=1e-3)
        assert entry["lon"] == pytest.approx(
            payload._state.orbit_lon_deg, abs=1e-3)
    finally:
        await client.close()
