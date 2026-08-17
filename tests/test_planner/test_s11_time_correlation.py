"""TIME_ARCHITECTURE Part E.4 — plan upload to S11 goes through the time
correlation, and the offset is NOT silently corrected.

Two halves, both asserted here:

1. **Planner side.** The planner uploads the activity's ``start_time`` as the
   absolute ISO-8601 **sim-time** instant the operator planned. It does not
   convert to CUC, does not send an ``exec_time``, and does not adjust the
   time for the state of the on-board clock. There is exactly one sim-time ->
   on-board-CUC conversion in the chain and it is not in the planner.

2. **The conversion the MCS then performs.** ``MCSServer._utc_to_obc_cuc``
   anchors on the OBC time telemetry (param 0x0309) plus the ground/sim-clock
   delta. So if the OBC clock reads 40 s ahead of sim time, the time tag the
   spacecraft stores is 40 s larger, and the spacecraft reaches it 40 s EARLY
   in sim time. That is the intended, teachable behaviour: the plan is right,
   the clock is wrong, and the operator fixes the clock (NOM-008 / LEOP-006),
   not the plan.
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_common.protocol.ecss_packet import (
    datetime_to_cuc_coarse, get_time_epoch,
)
from smo_planner.server import PlannerServer

CONFIG_DIR = "configs/eosat1"

SIM_NOW = datetime(2026, 3, 11, 9, 0, 0, tzinfo=timezone.utc)
START_TIME = "2026-03-11T10:00:00+00:00"
START_DT = datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)
OBC_ERROR_S = 40  # the OBC clock is 40 s FAST


class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def planner():
    # time_source="real" avoids any simulator polling during the test.
    return PlannerServer(CONFIG_DIR, time_source="real")


async def _fake_mcs(captured: dict) -> TestServer:
    app = web.Application()

    async def upload_s11(request):
        captured["body"] = await request.json()
        return web.json_response({"status": "scheduled", "count": 1,
                                  "skipped": []})

    app.router.add_post("/api/schedule/upload-s11", upload_s11)
    srv = TestServer(app)
    await srv.start_server()
    return srv


def _make_mcs():
    with patch("smo_mcs.server.load_mcs_displays",
               return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        from smo_mcs.server import MCSServer
        return MCSServer(config_dir="/tmp/fake_config",
                         connect_host="localhost", connect_port=9999,
                         http_port=0)


def _pin_mcs_sim_clock(mcs, when: datetime) -> None:
    """Freeze the MCS's notion of sim/ground UTC.

    Part D renames ``get_ground_utc``; pin every name that exists so this test
    does not care which one has landed.
    """
    for name in ("get_ground_utc", "get_sim_utc", "sim_now"):
        if hasattr(mcs, name):
            setattr(mcs, name, lambda _w=when: _w)


# ── 1. The planner uploads sim time, unmodified ─────────────────────

@pytest.mark.asyncio
async def test_planner_uploads_sim_time_and_never_converts_to_cuc(planner):
    captured: dict = {}
    mcs = await _fake_mcs(captured)
    try:
        planner._mcs_url = str(mcs.make_url("")).rstrip("/")
        act = planner._scheduler.add_activity("imaging_pass", START_TIME)
        resp = await planner._handle_upload_schedule(
            _FakeRequest({"activity_id": act["id"]}))
        assert resp.status == 200
    finally:
        await mcs.close()

    body = captured["body"]
    # The planned sim-time instant, byte for byte.
    assert body["start_time"] == START_TIME
    # No CUC, no exec_time, no offset correction anywhere in the payload.
    for key in ("cuc", "exec_time", "exec_time_cuc", "start_cuc",
                "obc_time", "clock_offset_s"):
        assert key not in body, f"planner must not send {key}"
    assert json.dumps(body).count(str(datetime_to_cuc_coarse(START_DT))) == 0


@pytest.mark.asyncio
async def test_planner_does_not_shift_start_time_for_a_wrong_obc_clock(planner):
    """Same activity, same upload payload, regardless of the OBC clock.

    The planner has no view of the on-board clock and must not acquire one:
    pre-compensating here would make the schedule fire at the planned sim
    time with a wrong clock, hiding the very error the operator is trained
    to find.
    """
    captured: dict = {}
    mcs = await _fake_mcs(captured)
    try:
        planner._mcs_url = str(mcs.make_url("")).rstrip("/")
        act = planner._scheduler.add_activity("imaging_pass", START_TIME)
        await planner._handle_upload_schedule(
            _FakeRequest({"activity_id": act["id"]}))
        first = captured["body"]["start_time"]
        # Nothing about a drifting clock can reach the planner, but assert the
        # upload is a pure function of the plan by repeating it.
        await planner._handle_upload_schedule(
            _FakeRequest({"activity_id": act["id"]}))
        assert captured["body"]["start_time"] == first == START_TIME
    finally:
        await mcs.close()


# ── 2. The MCS conversion the planner relies on ─────────────────────

def _fires_at_sim_time(exec_cuc: int, obc_error_s: float) -> datetime:
    """Sim time at which an OBC whose clock is ``obc_error_s`` FAST reaches
    the on-board time tag ``exec_cuc``.

    The OBC reads ``cuc(T) + obc_error_s`` at sim time T, so it reaches the
    tag at ``epoch + exec_cuc - obc_error_s``.
    """
    return (get_time_epoch().replace(tzinfo=timezone.utc)
            + timedelta(seconds=exec_cuc - obc_error_s))


def test_conversion_goes_through_the_correlation_not_the_bare_epoch():
    """With OBC time telemetry in hand, the tag is expressed on the OBC's
    clock: a clock reading 40 s ahead gets a tag 40 s larger.

    This is the ``_utc_to_obc_cuc`` path the planner relies on. A naive
    ``datetime_to_cuc`` would emit ``naive_cuc`` regardless of the state of
    the on-board clock — and, when the clock has never been set (still near
    J2000 after a bootloader reset), a tag the spacecraft will not reach for
    decades, i.e. a plan that never fires.
    """
    mcs = _make_mcs()
    _pin_mcs_sim_clock(mcs, SIM_NOW)
    mcs._param_cache[0x0309] = {
        "value": datetime_to_cuc_coarse(SIM_NOW) + OBC_ERROR_S}

    exec_cuc = mcs._utc_to_obc_cuc(START_DT)
    assert exec_cuc == datetime_to_cuc_coarse(START_DT) + OBC_ERROR_S
    assert exec_cuc != datetime_to_cuc_coarse(START_DT)


def test_an_uncorrelated_obc_clock_error_fires_the_schedule_early():
    """The teaching point (spec E.4): a 40 s clock error the ground has not
    measured makes the schedule fire 40 s EARLY in sim time.

    Here the ground has no OBC time telemetry (nothing has been downlinked
    since the clock was last set/rebooted), so the conversion falls back to
    the mission epoch — equivalently, the clock drifted 40 s after the sample
    the correlation was built from. Either way the plan is uploaded exactly
    as planned and the spacecraft executes it early. Nothing anywhere in the
    chain hides that: the cure is a clock sync (NOM-008 / LEOP-006), not a
    planner-side fudge.
    """
    mcs = _make_mcs()
    _pin_mcs_sim_clock(mcs, SIM_NOW)
    mcs._param_cache.pop(0x0309, None)

    exec_cuc = mcs._utc_to_obc_cuc(START_DT)
    assert exec_cuc == datetime_to_cuc_coarse(START_DT)   # epoch fallback

    fires_at = _fires_at_sim_time(exec_cuc, OBC_ERROR_S)
    assert (START_DT - fires_at).total_seconds() == OBC_ERROR_S


def test_a_synchronised_clock_fires_at_the_planned_sim_time():
    """After a clock sync the same plan, unchanged, fires on time."""
    mcs = _make_mcs()
    _pin_mcs_sim_clock(mcs, SIM_NOW)
    mcs._param_cache[0x0309] = {"value": datetime_to_cuc_coarse(SIM_NOW)}

    exec_cuc = mcs._utc_to_obc_cuc(START_DT)
    assert exec_cuc == datetime_to_cuc_coarse(START_DT)
    assert _fires_at_sim_time(exec_cuc, 0.0) == START_DT
