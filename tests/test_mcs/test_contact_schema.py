"""Defect #47 — the MCS must parse the planner's REAL contact schema.

The planner emits {aos, los (ISO strings), gs_name, max_elevation_deg};
the MCS UI timeline reads aos_utc/los_utc floats + ground_station and the
contact-schedule panel reads aos_time/los_time + max_elevation. Previously
only the synthetic fallback matched, so real passes rendered only when the
planner was DOWN. The synthetic pass fabrication is now removed (DI-03).
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.displays.contact_pass_scheduler import ContactScheduler
from smo_mcs.server import MCSServer


PLANNER_WINDOW = {
    "aos": "2026-03-10T12:00:00+00:00",
    "los": "2026-03-10T12:10:00+00:00",
    "gs_name": "Troll",
    "max_elevation_deg": 42.1,
    "duration_s": 600.0,
}


def _make_server():
    with patch("smo_mcs.server.load_mcs_displays",
               return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        return MCSServer(
            config_dir="/tmp/fake_config",
            connect_host="localhost",
            connect_port=9999,
            http_port=0,
        )


class TestNormalizeContact:
    def test_planner_schema_gains_ui_fields(self):
        out = MCSServer._normalize_contact(PLANNER_WINDOW)
        expected_aos = datetime(2026, 3, 10, 12, 0,
                                tzinfo=timezone.utc).timestamp()
        assert out["aos_utc"] == pytest.approx(expected_aos)
        assert out["los_utc"] == pytest.approx(expected_aos + 600)
        assert out["ground_station"] == "Troll"
        assert out["max_elevation"] == pytest.approx(42.1)
        assert out["aos_time"] == out["aos_utc"]
        assert out["los_time"] == out["los_utc"]
        # Original planner keys preserved
        assert out["aos"] == PLANNER_WINDOW["aos"]
        assert out["gs_name"] == "Troll"

    def test_mcs_schema_passes_through(self):
        c = {"aos_utc": 100.0, "los_utc": 700.0,
             "ground_station": "Iqaluit", "max_elevation_deg": 10.0}
        out = MCSServer._normalize_contact(c)
        assert out["aos_utc"] == 100.0
        assert out["ground_station"] == "Iqaluit"
        assert out["duration_s"] == 600.0

    def test_multi_station_gs_tag_supported(self):
        # The multi-station propagator work may tag windows with `gs`.
        c = dict(PLANNER_WINDOW)
        del c["gs_name"]
        c["gs"] = "Iqaluit"
        out = MCSServer._normalize_contact(c)
        assert out["ground_station"] == "Iqaluit"


class TestContactSchedulerParsesPlannerSchema:
    def test_update_passes_with_planner_format(self):
        sched = ContactScheduler()
        sched.update_passes([PLANNER_WINDOW])
        aos = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
        passes = sched.get_next_passes(10, current_time=aos - 3600)
        assert len(passes) == 1
        p = passes[0]
        assert p["aos_time"] == pytest.approx(aos)
        assert p["los_time"] == pytest.approx(aos + 600)
        assert p["ground_station"] == "Troll"
        assert p["max_elevation"] == pytest.approx(42.1)
        assert p["status"]["status"] == "upcoming"

    def test_update_passes_with_mcs_format_still_works(self):
        sched = ContactScheduler()
        sched.update_passes([{
            "aos_time": 1000.0, "los_time": 1600.0,
            "max_elevation": 30.0, "ground_station": "Iqaluit",
        }])
        passes = sched.get_next_passes(10, current_time=500.0)
        assert passes[0]["ground_station"] == "Iqaluit"
        assert passes[0]["aos_time"] == 1000.0

    def test_entries_without_times_are_dropped_not_faked(self):
        """Previously a pass with missing fields defaulted to aos=now,
        los=now+600 — a fabricated in-progress contact."""
        sched = ContactScheduler()
        sched.update_passes([{"ground_station": "Iqaluit"}])
        assert sched.get_next_passes(10, current_time=0.0) == []

    def test_in_contact_status_from_planner_format(self):
        sched = ContactScheduler()
        sched.update_passes([PLANNER_WINDOW])
        aos = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).timestamp()
        status = sched.get_current_contact_status(current_time=aos + 60)
        assert status["in_contact"] is True
        assert status["ground_station"] == "Troll"


class TestContactsProxy:
    async def _client(self, server) -> TestClient:
        app = web.Application()
        app.router.add_get("/api/contacts", server._handle_contacts_proxy)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_real_planner_contacts_normalized(self):
        # Stand up a fake planner serving the real schema.
        planner_app = web.Application()

        async def contacts(request):
            return web.json_response({"contacts": [PLANNER_WINDOW],
                                      "computed_at": "2026-03-10T11:00:00Z"})
        planner_app.router.add_get("/api/contacts", contacts)
        planner_srv = TestServer(planner_app)
        await planner_srv.start_server()

        server = _make_server()
        server._planner_api_base = str(planner_srv.make_url("")).rstrip("/")
        client = await self._client(server)
        try:
            resp = await client.get("/api/contacts")
            data = await resp.json()
            assert resp.status == 200
            assert "synthetic" not in data
            c = data["contacts"][0]
            assert "aos_utc" in c and "los_utc" in c
            assert c["ground_station"] == "Troll"
        finally:
            await client.close()
            await planner_srv.close()

    @pytest.mark.asyncio
    async def test_planner_down_returns_empty_not_fabricated(self):
        """DI-03: no fabricated passes when the planner is unreachable."""
        server = _make_server()
        server._planner_api_base = "http://127.0.0.1:1"  # guaranteed refuse
        client = await self._client(server)
        try:
            resp = await client.get("/api/contacts")
            data = await resp.json()
            assert data["contacts"] == []
            assert data["planner_unavailable"] is True
        finally:
            await client.close()

    def test_synthetic_contact_builder_removed(self):
        server = _make_server()
        assert not hasattr(server, "_build_synthetic_contacts")


class TestDumpSyntheticTagging:
    @pytest.mark.asyncio
    async def test_dump_data_endpoint_flags_synthetic(self):
        server = _make_server()
        server._tm_dump_data["eps_1"] = server._generate_dump_data("eps", 1)
        server._tm_dump_synthetic.add("eps_1")
        app = web.Application()
        app.router.add_get("/api/tm-dump-data", server._handle_tm_dump_data)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/tm-dump-data?key=eps_1")
            data = await resp.json()
            assert data["synthetic"] is True
            assert all(pt["synthetic"] is True for pt in data["data"])

            resp = await client.get("/api/tm-dump-data?key=missing")
            data = await resp.json()
            assert data["synthetic"] is False
        finally:
            await client.close()
