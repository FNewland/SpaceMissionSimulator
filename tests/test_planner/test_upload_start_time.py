"""Defect #44 — planner "Upload to MCS" must preserve the scheduled
start_time and (by default) route the activity through the MCS S11
time-tagged upload endpoint instead of the immediate procedure loader.
"""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from smo_planner.server import PlannerServer

CONFIG_DIR = "configs/eosat1"
START_TIME = "2026-03-11T10:00:00+00:00"


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
    """Stand up a fake MCS capturing both upload endpoints."""
    app = web.Application()

    async def upload_s11(request):
        captured["path"] = "/api/schedule/upload-s11"
        captured["body"] = await request.json()
        return web.json_response({"status": "scheduled", "count": 2,
                                  "skipped": []})

    async def proc_load(request):
        captured["path"] = "/api/procedure/load"
        captured["body"] = await request.json()
        return web.json_response({"state": "LOADED"})

    app.router.add_post("/api/schedule/upload-s11", upload_s11)
    app.router.add_post("/api/procedure/load", proc_load)
    srv = TestServer(app)
    await srv.start_server()
    return srv


@pytest.mark.asyncio
async def test_default_upload_is_s11_with_start_time(planner):
    captured: dict = {}
    mcs = await _fake_mcs(captured)
    try:
        planner._mcs_url = str(mcs.make_url("")).rstrip("/")
        act = planner._scheduler.add_activity("imaging_pass", START_TIME)

        resp = await planner._handle_upload_schedule(
            _FakeRequest({"activity_id": act["id"]}))
        body = json.loads(resp.body)

        assert resp.status == 200
        assert captured["path"] == "/api/schedule/upload-s11"
        assert captured["body"]["start_time"] == START_TIME
        assert captured["body"]["name"] == "imaging_pass"
        assert captured["body"]["steps"] == act["command_sequence"]
        assert body["mode"] == "s11"
        # Activity marked UPLOADED
        assert planner._scheduler.get_activity(
            act["id"])["state_name"] == "UPLOADED"
    finally:
        await mcs.close()


@pytest.mark.asyncio
async def test_immediate_mode_still_available_and_carries_start_time(planner):
    captured: dict = {}
    mcs = await _fake_mcs(captured)
    try:
        planner._mcs_url = str(mcs.make_url("")).rstrip("/")
        act = planner._scheduler.add_activity("data_dump", START_TIME)

        resp = await planner._handle_upload_schedule(
            _FakeRequest({"activity_id": act["id"], "mode": "immediate",
                          "step_by_step": True}))

        assert resp.status == 200
        assert captured["path"] == "/api/procedure/load"
        assert captured["body"]["start_time"] == START_TIME
        assert captured["body"]["step_by_step"] is True
    finally:
        await mcs.close()


@pytest.mark.asyncio
async def test_invalid_mode_rejected(planner):
    act = planner._scheduler.add_activity("data_dump", START_TIME)
    resp = await planner._handle_upload_schedule(
        _FakeRequest({"activity_id": act["id"], "mode": "bogus"}))
    assert resp.status == 400


def test_procedure_runner_preserves_start_time():
    """MCS-side half of the immediate path: the runner keeps and reports
    the scheduled start time instead of discarding it."""
    from smo_mcs.procedure_runner import ProcedureRunner

    async def _send(*a, **k):
        return {"status": "sent", "seq": 1}

    runner = ProcedureRunner(_send, lambda p: None)
    runner.load("imaging_pass", [{"service": 17, "subtype": 1}],
                start_time=START_TIME)
    assert runner.status()["scheduled_start_time"] == START_TIME
