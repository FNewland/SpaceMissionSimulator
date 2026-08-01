"""Tests for the planner's /api/constraints/* routes.

Six routes back the planner's "can this plan actually fly?" panel:

  GET /api/constraints/validate      — aggregate across every checker
  GET /api/constraints/power         — SoC timeline + bus-capacity violations
  GET /api/constraints/aocs          — slew feasibility + momentum budget
  GET /api/constraints/thermal       — payload duty cycle + FPA cooldown
  GET /api/constraints/data-volume   — onboard storage capacity/margin
  GET /api/constraints/conflicts     — exclusive-resource overlaps

They had no pytest coverage at all — only a demo script outside ``testpaths``.
Each route is exercised twice: once against a clean plan (no violations, stable
response shape) and once against a plan seeded with a deliberate breach, so a
checker that silently stops reporting is caught.

Follows the mocked-server pattern in test_planner_server.py: no TLE
propagation, no network, just the real PlannerServer handlers over a real
ActivityScheduler.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.imaging_planner import ImagingPlanner
from smo_planner.server import PlannerServer

T0 = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)

ACTIVITY_TYPES = [
    {"name": "imaging_pass", "duration_s": 120, "power_w": 75,
     "data_volume_mb": 800},
    {"name": "data_dump", "duration_s": 600, "power_w": 50,
     "data_volume_mb": 0},
    {"name": "housekeeping_collection", "duration_s": 300, "power_w": 5,
     "data_volume_mb": 1},
    {"name": "momentum_desaturation", "duration_s": 300, "power_w": 30,
     "data_volume_mb": 0},
]


def _iso(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _make_server() -> PlannerServer:
    """PlannerServer with only the pieces the constraint routes touch."""
    server = PlannerServer.__new__(PlannerServer)
    server.config_dir = Path("/tmp/fake_config")
    server.http_port = 0
    server._gs_list = []
    server._earth_r = 6371.0
    server._orbit_planner = MagicMock()
    server._live_prop = MagicMock()
    server._activity_types = ACTIVITY_TYPES
    server._scheduler = ActivityScheduler(ACTIVITY_TYPES)
    server._mcs_url = "http://localhost:9090"
    server._contacts_cache = [{
        "gs": "Ottawa", "aos": _iso(0), "los": _iso(10),
        "max_elevation_deg": 45.0, "duration_s": 600,
    }]
    server._contacts_computed_at = T0
    server._ground_track_cache = [
        {"utc": _iso(m), "lat": 45.0, "lon": -75.0, "alt_km": 550.0,
         "in_eclipse": False}
        for m in range(0, 120, 1)
    ]
    server._ground_track_computed_at = T0
    server._imaging_planner = ImagingPlanner()
    return server


def _build_app(server: PlannerServer) -> web.Application:
    app = web.Application()
    app.router.add_get("/api/constraints/validate",
                       server._handle_validate_constraints)
    app.router.add_get("/api/constraints/power", server._handle_check_power)
    app.router.add_get("/api/constraints/aocs", server._handle_check_aocs)
    app.router.add_get("/api/constraints/thermal", server._handle_check_thermal)
    app.router.add_get("/api/constraints/data-volume",
                       server._handle_check_data_volume)
    app.router.add_get("/api/constraints/conflicts",
                       server._handle_check_conflicts)
    return app


@pytest.fixture
async def client():
    """TestClient over a PlannerServer with an empty schedule."""
    server = _make_server()
    c = TestClient(TestServer(_build_app(server)))
    await c.start_server()
    c.planner = server  # expose for schedule seeding
    try:
        yield c
    finally:
        await c.close()


# ── Seeders: each puts one specific breach into the schedule ────────

def _seed_power_breach(server: PlannerServer) -> None:
    """A long, high-power activity that drains the battery below 20% SoC.

    Scheduled outside the cached ground track on purpose, so the checker falls
    back to its 35 % eclipse estimate: 4 h at 250 W nets roughly -760 Wh against
    a 1120 Wh battery starting at 80 %, landing near 12 %.
    """
    server._scheduler.add_activity(
        "imaging_pass", _iso(2000), duration_s=14400, power_w=250,
        data_volume_mb=0,
    )


def _seed_bus_capacity_breach(server: PlannerServer) -> None:
    """Single activity drawing more than the 300 W bus can supply."""
    server._scheduler.add_activity(
        "imaging_pass", _iso(0), duration_s=120, power_w=500,
    )


def _seed_slew_breach(server: PlannerServer) -> None:
    """Two imaging passes on opposite sides of the globe, back to back."""
    server._scheduler.add_activity(
        "imaging_pass", _iso(0), duration_s=120,
        capture_lat=45.0, capture_lon=-75.0,
    )
    server._scheduler.add_activity(
        "imaging_pass", _iso(2.5), duration_s=120,
        capture_lat=-45.0, capture_lon=105.0,
    )


def _seed_thermal_breach(server: PlannerServer) -> None:
    """40 minutes of imaging inside one orbit, with no FPA cooldown gap."""
    for i in range(10):
        server._scheduler.add_activity(
            "imaging_pass", _iso(i * 4), duration_s=240,
        )


def _seed_storage_breach(server: PlannerServer) -> None:
    """Enough imaging to overflow the 20 GB mass memory with no dump."""
    for i in range(30):
        server._scheduler.add_activity(
            "imaging_pass", _iso(i * 10), duration_s=120,
            data_volume_mb=1000,
        )


def _seed_conflict_breach(server: PlannerServer) -> None:
    """A dump and a housekeeping collection fighting over the antenna."""
    server._scheduler.add_activity("data_dump", _iso(0), duration_s=600)
    server._scheduler.add_activity(
        "housekeeping_collection", _iso(2), duration_s=300)


# ── /api/constraints/validate ───────────────────────────────────────

class TestValidateConstraints:

    async def test_clean_plan_is_valid(self, client):
        client.planner._scheduler.add_activity(
            "housekeeping_collection", _iso(0))
        resp = await client.get("/api/constraints/validate")
        assert resp.status == 200
        data = await resp.json()
        assert data["valid"] is True
        assert data["violation_count"] == 0
        assert data["error_count"] == 0
        assert "power" in data["checker_results"]

    async def test_response_shape(self, client):
        resp = await client.get("/api/constraints/validate")
        data = await resp.json()
        for key in ("valid", "violation_count", "violations", "error_count",
                    "warning_count", "checker_results"):
            assert key in data, f"missing key {key!r} in validate response"
        assert isinstance(data["violations"], list)

    async def test_power_breach_makes_plan_invalid(self, client):
        _seed_power_breach(client.planner)
        resp = await client.get("/api/constraints/validate")
        data = await resp.json()
        assert data["valid"] is False
        assert data["error_count"] >= 1
        assert any(v["checker"] == "PowerConstraintChecker"
                   for v in data["violations"])

    async def test_violations_carry_actionable_fields(self, client):
        _seed_power_breach(client.planner)
        resp = await client.get("/api/constraints/validate")
        data = await resp.json()
        v = data["violations"][0]
        for key in ("checker", "severity", "message", "suggested_fix"):
            assert key in v, f"violation is missing {key!r}"
        assert v["severity"] in ("error", "warning")
        assert v["message"], "violation message is empty"

    async def test_battery_soc_query_param_is_honoured(self, client):
        """Starting SoC changes the outcome — the query param must reach the checker."""
        client.planner._scheduler.add_activity(
            "imaging_pass", _iso(0), duration_s=3600, power_w=200)
        high = await (await client.get(
            "/api/constraints/validate?battery_soc=100")).json()
        low = await (await client.get(
            "/api/constraints/validate?battery_soc=21")).json()
        assert low["error_count"] >= high["error_count"], (
            "starting at 21% SoC should be no better than starting at 100%")
        assert low["valid"] is False

    async def test_cancelled_activities_are_excluded(self, client):
        _seed_power_breach(client.planner)
        act_id = client.planner._scheduler.get_schedule()[0]["id"]
        client.planner._scheduler.update_state(act_id, ActivityState.CANCELLED)
        data = await (await client.get("/api/constraints/validate")).json()
        assert not any(v["checker"] == "PowerConstraintChecker"
                       for v in data["violations"]), (
            "a CANCELLED activity must not raise power violations")


# ── /api/constraints/power ──────────────────────────────────────────

class TestPowerConstraints:

    async def test_clean_plan_returns_timeline(self, client):
        client.planner._scheduler.add_activity("imaging_pass", _iso(0))
        resp = await client.get("/api/constraints/power")
        assert resp.status == 200
        data = await resp.json()
        assert data["violations"] == []
        assert len(data["soc_timeline"]) == 1
        entry = data["soc_timeline"][0]
        assert {"activity_id", "activity_name", "soc_before",
                "soc_after"} <= set(entry)
        assert 0.0 <= data["final_soc"] <= 100.0

    async def test_soc_breach_is_reported_as_json(self, client):
        """Regression: violations must be JSON-serialisable.

        ``check_plan_power`` hands back ConstraintViolation objects; the route
        used to pass them straight to json_response, so this endpoint returned
        500 for exactly the plans it exists to flag.
        """
        _seed_power_breach(client.planner)
        resp = await client.get("/api/constraints/power")
        assert resp.status == 200, (
            f"power route failed on a plan with violations: {await resp.text()}")
        data = await resp.json()
        assert data["violations"], "SoC breach was not reported"
        v = data["violations"][0]
        assert isinstance(v, dict)
        assert v["checker"] == "PowerConstraintChecker"
        assert v["severity"] == "error"

    async def test_bus_capacity_is_reflected_in_final_soc(self, client):
        _seed_bus_capacity_breach(client.planner)
        data = await (await client.get("/api/constraints/power")).json()
        assert data["final_soc"] <= 100.0
        assert isinstance(data["soc_timeline"], list)

    async def test_empty_schedule_is_a_clean_result(self, client):
        data = await (await client.get("/api/constraints/power")).json()
        assert data["violations"] == []
        assert data["soc_timeline"] == []


# ── /api/constraints/aocs ───────────────────────────────────────────

class TestAOCSConstraints:

    async def test_clean_plan_has_no_violations(self, client):
        client.planner._scheduler.add_activity(
            "imaging_pass", _iso(0), capture_lat=45.0, capture_lon=-75.0)
        client.planner._scheduler.add_activity(
            "imaging_pass", _iso(30), capture_lat=46.0, capture_lon=-74.0)
        resp = await client.get("/api/constraints/aocs")
        assert resp.status == 200
        data = await resp.json()
        assert data["slew_violations"] == []
        assert data["momentum_violations"] == []

    async def test_infeasible_slew_is_flagged(self, client):
        _seed_slew_breach(client.planner)
        data = await (await client.get("/api/constraints/aocs")).json()
        assert data["slew_violations"], (
            "back-to-back imaging of antipodal targets should be infeasible")
        v = data["slew_violations"][0]
        assert v["checker"] == "AOCSConstraintChecker"
        assert "slew" in v["message"].lower()
        assert v["suggested_fix"]

    async def test_momentum_accumulation_is_flagged(self, client):
        """Enough imaging with no desaturation must breach the momentum budget.

        The checker accumulates 0.15 h·km²/s per pass against a 100 h·km²/s
        wheel capacity and warns above 80 %, so it takes ~534 passes to trip —
        the plan below is deliberately sized past that.
        """
        for i in range(600):
            client.planner._scheduler.add_activity(
                "imaging_pass", _iso(i * 30), duration_s=120)
        data = await (await client.get("/api/constraints/aocs")).json()
        assert data["momentum_violations"], (
            "600 imaging passes with no desaturation should breach the "
            "momentum budget")
        assert all(v["severity"] == "warning"
                   for v in data["momentum_violations"])

    async def test_desaturation_resets_the_momentum_budget(self, client):
        """A desaturation activity between passes clears accumulated momentum."""
        for i in range(600):
            client.planner._scheduler.add_activity(
                "imaging_pass", _iso(i * 30), duration_s=120)
            if i % 100 == 99:
                client.planner._scheduler.add_activity(
                    "momentum_desaturation", _iso(i * 30 + 15), duration_s=300)
        data = await (await client.get("/api/constraints/aocs")).json()
        assert data["momentum_violations"] == [], (
            "desaturating every 100 passes should keep momentum within budget")

    async def test_response_shape_on_empty_schedule(self, client):
        data = await (await client.get("/api/constraints/aocs")).json()
        assert set(data) == {"slew_violations", "momentum_violations"}


# ── /api/constraints/thermal ────────────────────────────────────────

class TestThermalConstraints:

    async def test_clean_plan_has_no_violations(self, client):
        client.planner._scheduler.add_activity("imaging_pass", _iso(0))
        resp = await client.get("/api/constraints/thermal")
        assert resp.status == 200
        data = await resp.json()
        assert data["duty_cycle_violations"] == []
        assert data["cooldown_violations"] == []

    async def test_duty_cycle_and_cooldown_breaches_are_flagged(self, client):
        _seed_thermal_breach(client.planner)
        data = await (await client.get("/api/constraints/thermal")).json()
        assert data["duty_cycle_violations"], (
            "40 min of imaging in one orbit should breach the duty cycle")
        assert data["cooldown_violations"], (
            "imaging every 4 min should breach the 300 s FPA cooldown")
        assert all(v["severity"] == "warning"
                   for v in data["duty_cycle_violations"])

    async def test_non_imaging_activities_are_ignored(self, client):
        for i in range(10):
            client.planner._scheduler.add_activity(
                "housekeeping_collection", _iso(i * 4), duration_s=240)
        data = await (await client.get("/api/constraints/thermal")).json()
        assert data["duty_cycle_violations"] == []
        assert data["cooldown_violations"] == []


# ── /api/constraints/data-volume ────────────────────────────────────

class TestDataVolumeConstraints:

    async def test_clean_plan_has_no_violations(self, client):
        client.planner._scheduler.add_activity(
            "imaging_pass", _iso(0), data_volume_mb=100)
        resp = await client.get("/api/constraints/data-volume")
        assert resp.status == 200
        data = await resp.json()
        assert data["storage_violations"] == []

    async def test_storage_overflow_is_flagged(self, client):
        _seed_storage_breach(client.planner)
        data = await (await client.get("/api/constraints/data-volume")).json()
        errors = [v for v in data["storage_violations"]
                  if v["severity"] == "error"]
        assert errors, "30 GB of imaging must overflow the 20 GB mass memory"
        assert "capacity" in errors[0]["message"].lower()

    async def test_current_onboard_mb_query_param_is_honoured(self, client):
        client.planner._scheduler.add_activity(
            "imaging_pass", _iso(0), data_volume_mb=1000)
        clean = await (await client.get(
            "/api/constraints/data-volume?current_onboard_mb=0")).json()
        loaded = await (await client.get(
            "/api/constraints/data-volume?current_onboard_mb=19500")).json()
        assert clean["storage_violations"] == []
        assert loaded["storage_violations"], (
            "starting with 19.5 GB already onboard must breach capacity")


# ── /api/constraints/conflicts ──────────────────────────────────────

class TestConflictConstraints:

    async def test_clean_plan_has_no_conflicts(self, client):
        client.planner._scheduler.add_activity("data_dump", _iso(0))
        client.planner._scheduler.add_activity(
            "housekeeping_collection", _iso(60))
        resp = await client.get("/api/constraints/conflicts")
        assert resp.status == 200
        data = await resp.json()
        assert data["conflict_violations"] == []

    async def test_overlapping_antenna_users_conflict(self, client):
        _seed_conflict_breach(client.planner)
        data = await (await client.get("/api/constraints/conflicts")).json()
        assert data["conflict_violations"], (
            "an overlapping dump and HK collection both need the antenna")
        v = data["conflict_violations"][0]
        assert v["checker"] == "ConflictResolutionChecker"
        assert v["severity"] == "error"
        assert "overlap" in v["message"].lower()

    async def test_lower_priority_activity_is_the_one_flagged(self, client):
        """Conflict resolution nominates the lower-priority activity to move."""
        _seed_conflict_breach(client.planner)
        data = await (await client.get("/api/constraints/conflicts")).json()
        assert data["conflict_violations"][0]["activity_name"] == (
            "housekeeping_collection")

    async def test_cancelled_activity_does_not_conflict(self, client):
        _seed_conflict_breach(client.planner)
        hk = [a for a in client.planner._scheduler.get_schedule()
              if a["name"] == "housekeeping_collection"][0]
        client.planner._scheduler.update_state(
            hk["id"], ActivityState.CANCELLED)
        data = await (await client.get("/api/constraints/conflicts")).json()
        assert data["conflict_violations"] == []


# ── Cross-route consistency ─────────────────────────────────────────

async def test_validate_aggregates_the_aocs_checker(client):
    """A slew breach reported by /aocs must also appear in /validate."""
    _seed_slew_breach(client.planner)

    aocs = await (await client.get("/api/constraints/aocs")).json()
    combined = await (await client.get("/api/constraints/validate")).json()

    assert aocs["slew_violations"], "seeding produced no slew violation"
    names = [v["checker"] for v in combined["violations"]]
    assert names.count("AOCSConstraintChecker") == (
        len(aocs["slew_violations"]) + len(aocs["momentum_violations"]))
    assert combined["valid"] is False


async def test_validate_aggregates_the_conflict_checker(client):
    """A resource conflict reported by /conflicts must appear in /validate."""
    _seed_conflict_breach(client.planner)

    conflicts = await (await client.get("/api/constraints/conflicts")).json()
    combined = await (await client.get("/api/constraints/validate")).json()

    assert conflicts["conflict_violations"], "seeding produced no conflict"
    names = [v["checker"] for v in combined["violations"]]
    assert names.count("ConflictResolutionChecker") == len(
        conflicts["conflict_violations"])
    assert combined["valid"] is False


async def test_slew_check_scope_differs_between_routes(client):
    """/aocs screens slew to imaging activities; /validate checks the whole plan.

    Pins a real behavioural difference: ``check_aocs_constraints`` filters the
    schedule to activities whose name contains "imaging" before running the
    slew check, while ``validate_plan`` runs it over every activity. Mixing
    non-imaging activities between two imaging passes therefore hides the slew
    violation from /validate. Worth knowing before trusting one route as a
    proxy for the other.
    """
    _seed_slew_breach(client.planner)
    _seed_conflict_breach(client.planner)  # non-imaging activities in between

    aocs = await (await client.get("/api/constraints/aocs")).json()
    combined = await (await client.get("/api/constraints/validate")).json()

    assert aocs["slew_violations"], "/aocs should still see the slew breach"
    names = [v["checker"] for v in combined["violations"]]
    assert names.count("AOCSConstraintChecker") == 0, (
        "if /validate now also filters to imaging activities, this pinned "
        "difference is resolved — update the test and the docstring")


@pytest.mark.parametrize("route", [
    "/api/constraints/validate",
    "/api/constraints/power",
    "/api/constraints/aocs",
    "/api/constraints/thermal",
    "/api/constraints/data-volume",
    "/api/constraints/conflicts",
])
async def test_every_route_returns_json_for_a_breaching_plan(client, route):
    """No constraint route may 500 on a plan that violates everything.

    The routes wrap their checker in a bare ``except`` that turns any error
    into a 500 with an ``error`` key, so a serialisation regression shows up
    here rather than as a silently empty panel.
    """
    _seed_power_breach(client.planner)
    _seed_slew_breach(client.planner)
    _seed_thermal_breach(client.planner)
    _seed_storage_breach(client.planner)
    _seed_conflict_breach(client.planner)

    resp = await client.get(route)
    assert resp.status == 200, f"{route} -> {resp.status}: {await resp.text()}"
    data = await resp.json()
    assert "error" not in data, f"{route} reported an internal error: {data}"
