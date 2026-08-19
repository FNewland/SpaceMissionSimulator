"""Tests for the default-empty plan and the standard-activity templates.

Covers (defect #57, audit Q3):
  - the scheduler/server start with an EMPTY schedule
  - planning/standard_plans.yaml loads, validates, and only names activity
    types that exist in activity_types.yaml (so every instance gets a real
    command sequence with verified func_ids)
  - templates instantiate relative to the planner's CURRENT (sim) time
  - pass_relative anchors snap to REAL contact windows
  - GET /api/schedule/templates, POST /api/schedule/load-template,
    DELETE /api/schedule
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_planner.activity_scheduler import ActivityScheduler
from smo_planner.plan_templates import (
    load_standard_plans, validate_templates, instantiate_template,
    StandardPlansConfig, PlanTemplateConfig, TemplateActivityConfig,
)
from smo_planner.server import PlannerServer


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
SIM_NOW = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)

CONTACTS = [
    {"gs_name": "Svalbard", "aos": "2026-03-10T00:20:00+00:00",
     "los": "2026-03-10T00:28:00+00:00", "duration_s": 480,
     "max_elevation_deg": 40.0},
    {"gs_name": "Troll", "aos": "2026-03-10T01:05:00+00:00",
     "los": "2026-03-10T01:13:00+00:00", "duration_s": 480,
     "max_elevation_deg": 25.0},
    {"gs_name": "Inuvik", "aos": "2026-03-10T01:55:00+00:00",
     "los": "2026-03-10T02:02:00+00:00", "duration_s": 420,
     "max_elevation_deg": 20.0},
    {"gs_name": "Iqaluit", "aos": "2026-03-10T02:40:00+00:00",
     "los": "2026-03-10T02:47:00+00:00", "duration_s": 420,
     "max_elevation_deg": 15.0},
    {"gs_name": "O'Higgins", "aos": "2026-03-10T03:25:00+00:00",
     "los": "2026-03-10T03:31:00+00:00", "duration_s": 360,
     "max_elevation_deg": 12.0},
]

GROUND_TRACK = [
    {"utc": (SIM_NOW + timedelta(minutes=i)).isoformat(),
     "lat": 0.0, "lon": 0.0, "alt_km": 450.0,
     "in_eclipse": (i % 90) < 35}
    for i in range(0, 600)
]


def _activity_type_names() -> set[str]:
    data = yaml.safe_load(
        (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
    return {a["name"] for a in data["activity_types"]}


# ── Default state ──────────────────────────────────────────────────

class TestDefaultEmptyPlan:

    def test_scheduler_starts_empty(self):
        types = yaml.safe_load(
            (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
        sched = ActivityScheduler(types["activity_types"])
        assert sched.get_schedule() == []

    def test_server_starts_empty(self):
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        assert server._scheduler.get_schedule() == []
        assert server.planner_cfg.default_plan.start_empty is True

    def test_clear_schedule_returns_to_empty(self):
        sched = ActivityScheduler([])
        sched.add_activity("housekeeping_collection", SIM_NOW.isoformat())
        sched.add_activity("data_dump", SIM_NOW.isoformat())
        assert sched.clear_schedule() == 2
        assert sched.get_schedule() == []


# ── Template config ────────────────────────────────────────────────

class TestTemplateConfig:

    def test_shipped_templates_load_and_validate(self):
        cfg = load_standard_plans(CONFIG_DIR / "planning" / "standard_plans.yaml")
        assert cfg.templates, "no standard-plan templates configured"
        issues = validate_templates(cfg, _activity_type_names())
        assert issues == [], issues

    def test_default_template_exists(self):
        from smo_planner.planner_config import load_planner_config
        planner_cfg = load_planner_config(CONFIG_DIR)
        cfg = load_standard_plans(CONFIG_DIR / "planning" / "standard_plans.yaml")
        assert cfg.get(planner_cfg.default_plan.default_template) is not None

    def test_unknown_activity_type_is_rejected(self):
        """The failure mode the JS fixtures had: 'Orbit Maintenance' is not a
        defined activity type, so it has no duration, power or commands."""
        cfg = StandardPlansConfig(templates=[PlanTemplateConfig(
            id="bad", activities=[
                TemplateActivityConfig(type="Orbit Maintenance")])])
        issues = validate_templates(cfg, _activity_type_names())
        assert any("Orbit Maintenance" in i for i in issues)

    def test_unknown_anchor_is_rejected(self):
        cfg = StandardPlansConfig(templates=[PlanTemplateConfig(
            id="bad", activities=[
                TemplateActivityConfig(type="data_dump", anchor="whenever")])])
        issues = validate_templates(cfg, _activity_type_names())
        assert any("unknown anchor" in i for i in issues)


# ── Instantiation ──────────────────────────────────────────────────

class TestInstantiation:

    def _nominal(self):
        cfg = load_standard_plans(CONFIG_DIR / "planning" / "standard_plans.yaml")
        return cfg.get("nominal_ops_day")

    def test_instantiates_at_the_given_now(self):
        specs, warnings = instantiate_template(
            self._nominal(), SIM_NOW, CONTACTS, GROUND_TRACK)
        assert specs
        starts = [datetime.fromisoformat(s["start_time"]) for s in specs]
        assert min(starts) >= SIM_NOW
        # everything lands within the template horizon, not months away
        assert max(starts) - SIM_NOW < timedelta(hours=24)

    def test_pass_relative_snaps_to_real_contacts(self):
        specs, _ = instantiate_template(
            self._nominal(), SIM_NOW, CONTACTS, GROUND_TRACK)
        dumps = [s for s in specs if s["name"] == "data_dump"]
        # nominal_ops_day asks for 8 dumps at pass stride 3; only 5 contacts
        # are offered here, so passes 0 and 3 resolve and the rest are warned
        # away rather than invented (defect #82 changed stride 1 x4 -> 3 x8).
        assert len(dumps) == 2
        aos_times = {c["aos"] for c in CONTACTS}
        by_aos = {c["aos"]: c for c in CONTACTS}
        for d in dumps:
            assert d["extras"]["pass_aos"] in aos_times
            assert d["extras"]["station"] in {
                "Svalbard", "Troll", "Inuvik", "Iqaluit", "O'Higgins"}
            contact = by_aos[d["extras"]["pass_aos"]]
            aos = datetime.fromisoformat(contact["aos"])
            los = datetime.fromisoformat(contact["los"])
            start = datetime.fromisoformat(d["start_time"])
            # Dumps are centred on closest approach, not hung off AOS: the
            # 360 s activity has to sit above the 10 deg gate to hold the
            # HIGH tier (defect #86; 2026-08d PLAN-2 grew the activity from
            # 330 s to 360 s, so the centring offset is 3.0 min not 2.75).
            assert start == aos + (los - aos) / 2 - timedelta(minutes=3.0)
            # ...and only gate-clearing passes are offered to the template.
            assert contact["max_elevation_deg"] >= 10.0
            assert d["extras"]["pass_max_elevation_deg"] == \
                contact["max_elevation_deg"]

    def test_daylight_anchor_picks_a_sunlit_sample(self):
        specs, _ = instantiate_template(
            self._nominal(), SIM_NOW, CONTACTS, GROUND_TRACK)
        imaging = [s for s in specs if s["name"] == "imaging_pass"]
        assert imaging
        sunlit = {pt["utc"] for pt in GROUND_TRACK if not pt["in_eclipse"]}
        for act in imaging:
            assert act["start_time"] in sunlit

    def test_missing_passes_warn_rather_than_inventing_them(self):
        specs, warnings = instantiate_template(
            self._nominal(), SIM_NOW, [], GROUND_TRACK)
        assert not any(s["name"] == "data_dump" for s in specs)
        assert any("no contact window" in w for w in warnings)

    def test_types_resolve_to_real_command_sequences(self):
        types = yaml.safe_load(
            (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
        sched = ActivityScheduler(types["activity_types"])
        specs, _ = instantiate_template(
            self._nominal(), SIM_NOW, CONTACTS, GROUND_TRACK)
        for spec in specs:
            act = sched.add_activity(
                spec["name"], spec["start_time"], **spec["extras"])
            assert act["command_sequence"], f"{act['name']} has no commands"
            for step in act["command_sequence"]:
                if step.get("service") == 8:
                    assert step.get("func_id") is not None


# ── Endpoints ──────────────────────────────────────────────────────

def _server_with_contacts():
    server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
    server._contacts_cache = CONTACTS
    server._ground_track_cache = GROUND_TRACK
    server._get_now = lambda: SIM_NOW
    return server


async def _client(server):
    app = web.Application()
    app.router.add_get("/api/schedule", server._handle_schedule)
    app.router.add_delete("/api/schedule", server._handle_clear_schedule)
    app.router.add_get("/api/schedule/templates", server._handle_plan_templates)
    app.router.add_post("/api/schedule/load-template", server._handle_load_template)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_template_endpoints_round_trip():
    server = _server_with_contacts()
    client = await _client(server)
    try:
        # Fresh planner: empty
        data = await (await client.get("/api/schedule")).json()
        assert data["schedule"] == []

        listing = await (await client.get("/api/schedule/templates")).json()
        ids = [t["id"] for t in listing["templates"]]
        assert "nominal_ops_day" in ids
        assert listing["start_empty"] is True

        resp = await client.post("/api/schedule/load-template",
                                 json={"template": "nominal_ops_day"})
        assert resp.status == 201
        loaded = await resp.json()
        assert loaded["added"] > 0
        assert loaded["t0"] == SIM_NOW.isoformat()

        data = await (await client.get("/api/schedule")).json()
        assert len(data["schedule"]) == loaded["added"]
        # instantiated at sim time, on real passes
        for act in data["schedule"]:
            assert act["start_time"] >= SIM_NOW.isoformat()

        cleared = await (await client.delete("/api/schedule")).json()
        assert cleared["removed"] == loaded["added"]
        data = await (await client.get("/api/schedule")).json()
        assert data["schedule"] == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unknown_template_is_404():
    server = _server_with_contacts()
    client = await _client(server)
    try:
        resp = await client.post("/api/schedule/load-template",
                                 json={"template": "does_not_exist"})
        assert resp.status == 404
        body = await resp.json()
        assert "nominal_ops_day" in body["available"]
    finally:
        await client.close()
