"""Tests for the config-driven, honest data budget (defect #60).

Covers:
  - budget parameters are resolved from the MISSION CONFIG
    (payload.yaml total_storage_mb = 20000, image_size_mb = 4,
     ttc.yaml tm_rate_hi_bps = 64000, eps.yaml battery capacity), not from
     module constants (which were 20x off), and report their source
  - the two planner modules that disagreed about the downlink rate
    (BudgetTracker 51.2 kbps vs DataVolumeConstraintChecker 0.5 Mbps) now use
    the same number from ttc.yaml
  - the budget is integrated chronologically: overflow is detected with a time,
    not hidden by netting totals
  - an infeasible plan is flagged honestly (plan_closes False, backlog,
    days_to_drain) rather than silently fudged
  - the real EOSAT-1 numbers after defect #82: a 4 MB image costs ~655 s of
    effective contact (about one good pass), so a day's imaging closes with
    margin, while an 800 MB-era product still cannot
  - an empty plan renders (utilization_percent present and 0.0), which is what
    the UI's falsy-zero guard used to swallow
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from smo_planner.budget_tracker import BudgetTracker, budget_params_from_config
from smo_planner.constraint_checkers import DataVolumeConstraintChecker
from smo_planner.planner_config import load_planner_config


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
T0 = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)


def _contacts(n=10, first_offset_min=20, spacing_min=45,
              duration_s=480, max_el=30.0):
    out = []
    for i in range(n):
        aos = T0 + timedelta(minutes=first_offset_min + i * spacing_min)
        out.append({
            "gs_name": ["Svalbard", "Troll", "Inuvik", "Iqaluit", "O'Higgins"][i % 5],
            "aos": aos.isoformat(),
            "los": (aos + timedelta(seconds=duration_s)).isoformat(),
            "duration_s": duration_s,
            "max_elevation_deg": max_el,
        })
    return out


def _act(name, start, duration_s, data_mb=0.0):
    return {"id": abs(hash((name, start))) % 10000, "name": name,
            "start_time": start.isoformat(), "duration_s": duration_s,
            "power_w": 50, "data_volume_mb": data_mb,
            "state": 0, "state_name": "PLANNED"}


# ── Config resolution ──────────────────────────────────────────────

class TestBudgetParamsFromConfig:

    def test_values_come_from_the_spacecraft_config(self):
        params = budget_params_from_config(CONFIG_DIR)
        payload = yaml.safe_load(
            (CONFIG_DIR / "subsystems" / "payload.yaml").read_text())
        ttc = yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())
        eps = yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())

        assert params["storage_capacity_mb"] == payload["total_storage_mb"] == 20000.0
        assert params["image_size_mb"] == payload["image_size_mb"] == 4.0
        assert params["battery_capacity_wh"] == eps["battery"]["capacity_wh"]

        # The downlink is a LADDER (defect #86). The legacy single-rate key is
        # the DUMP rung — the top of ttc.yaml's tm_rate_tiers, 128 kbps — and
        # the routine rung is the configured default_tier, 16 kbps.
        tiers = {t["name"]: t for t in ttc["tm_rate_tiers"]}
        assert params["downlink_rate_bps"] == ttc["tm_rate_hi_bps"] == 128000
        assert params["dump_rate_bps"] == tiers["HIGH"]["bps"] == 128000
        assert params["routine_rate_bps"] == tiers[ttc["default_tier"]]["bps"] == 16000
        assert params["dump_tier_name"] == "HIGH"
        assert params["routine_tier_name"] == "LOW"
        # ...and the HIGH tier's elevation gate is a planning constraint, not
        # a comment: it comes from the tier itself.
        assert params["default_min_dump_elevation_deg"] == \
            tiers["HIGH"]["min_elevation_deg"] == 10.0

    def test_sources_are_reported(self):
        params = budget_params_from_config(CONFIG_DIR)
        assert "payload.yaml" in params["sources"]["storage_capacity_mb"]
        assert "ttc.yaml" in params["sources"]["downlink_rate_bps"]

    def test_planner_yaml_can_override(self):
        cfg = load_planner_config(CONFIG_DIR)
        cfg.budget.storage_capacity_mb = 4096.0
        params = budget_params_from_config(CONFIG_DIR, cfg)
        assert params["storage_capacity_mb"] == 4096.0
        assert "planner.yaml" in params["sources"]["storage_capacity_mb"]

    def test_tracker_from_config_uses_them(self):
        tracker = BudgetTracker.from_config(CONFIG_DIR, contacts=_contacts(2))
        budget = tracker.compute_data_budget()
        assert budget["storage_capacity_mb"] == 20000.0
        assert budget["downlink_rate_bps"] == 128000
        assert budget["effective_downlink_rate_bps"] == pytest.approx(102400.0)
        assert budget["routine_rate_bps"] == 16000
        assert budget["dump_rate_bps"] == 128000
        assert budget["min_dump_elevation_deg"] == 10.0
        assert budget["image_size_mb"] == 4.0

    def test_the_two_modules_now_agree_on_the_downlink_rate(self):
        """BudgetTracker said 51.2 kbps, DataVolumeConstraintChecker 0.5 Mbps."""
        params = budget_params_from_config(CONFIG_DIR)
        checker = DataVolumeConstraintChecker.from_params(params)
        tracker = BudgetTracker.from_config(CONFIG_DIR, contacts=_contacts(1))
        assert checker.downlink_rate_bps == params["downlink_rate_bps"]
        assert checker.effective_rate_bps == pytest.approx(
            tracker.compute_data_budget()["effective_downlink_rate_bps"])

    def test_checker_default_capacity_matches_payload_config(self):
        checker = DataVolumeConstraintChecker.from_params(
            budget_params_from_config(CONFIG_DIR))
        assert checker.storage_capacity_mb == 20000.0


# ── Empty plan renders ─────────────────────────────────────────────

class TestEmptyPlanRenders:

    def test_empty_plan_reports_zero_not_nothing(self):
        tracker = BudgetTracker.from_config(CONFIG_DIR, contacts=_contacts(5))
        budget = tracker.compute_data_budget()
        assert budget["utilization_percent"] == 0.0
        assert budget["onboard_data_mb"] == 0.0
        assert budget["storage_capacity_mb"] == 20000.0
        assert budget["plan_closes"] is True
        # per-pass capacity is real and non-zero even with an empty plan
        assert len(budget["pass_downlink"]) == 5
        assert all(p["capacity_mb"] > 0 for p in budget["pass_downlink"])


# ── The truth about the plan ───────────────────────────────────────

class TestPlanFeasibility:

    def test_the_configured_image_closes_in_a_day(self):
        """Defect #82: with the textbook-aligned 4 MB image, a day that
        acquires two images and dumps on every pass closes with margin."""
        contacts = _contacts(10)
        image_mb = budget_params_from_config(CONFIG_DIR)["image_size_mb"]
        assert image_mb == 4.0
        schedule = [
            _act("imaging_pass", T0 + timedelta(minutes=5), 120, image_mb),
            _act("imaging_pass", T0 + timedelta(hours=3), 120, image_mb),
        ]
        for c in contacts:
            aos = datetime.fromisoformat(c["aos"])
            schedule.append(_act("data_dump", aos, c["duration_s"]))
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()

        assert budget["planned_generation_mb"] == pytest.approx(2 * image_mb)
        assert budget["plan_closes"] is True
        assert budget["backlog_growth_mb"] <= 0.001
        assert budget["onboard_data_mb"] == pytest.approx(0.0, abs=0.05)
        assert not any("DOWNLINK_INFEASIBLE" in w for w in budget["warnings"])

    def test_one_image_costs_about_one_pass_of_contact(self):
        """The arithmetic the pass-planning lesson rests on: 4 MiB at the HIGH
        tier (128 kbps) x 0.8 protocol efficiency is 327.7 s of effective
        contact — most of a good pass, and the reason `data_dump` is 330 s
        long. Not days of the whole network, and not a rounding error either:
        booking a dump still costs a real, schedulable fraction of a pass."""
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=_contacts(10)).compute_data_budget()
        eff_bps = budget["effective_downlink_rate_bps"]
        seconds = budget["image_size_mb"] * 1024 * 1024 * 8 / eff_bps
        assert seconds == pytest.approx(327.68, abs=0.1)
        assert budget["image_dump_seconds"] == pytest.approx(327.7, abs=0.1)
        # Over half of an average pass in the shipped contact statistics.
        assert 50.0 < budget["image_pass_fraction_percent"] < 90.0
        # At the routine rung the same image would take eight times as long —
        # more than a whole pass, which is why dumps must be booked at HIGH.
        routine_seconds = (budget["image_size_mb"] * 1024 * 1024 * 8
                           / (budget["routine_rate_bps"] * 0.8))
        assert routine_seconds > budget["mean_pass_seconds"] * 4
        # ...and a day of the real network shifts many images, not a fraction
        assert (budget["daily_downlink_capacity_mb"]
                / budget["image_size_mb"]) > 10.0

    def test_the_legacy_800_mb_product_still_does_not_close(self):
        """The size that motivated defect #82: an 800 MB product needs days of
        contact time, so a single-image plan cannot close. Kept as the
        counter-example that proves the check is not vacuous."""
        contacts = _contacts(10)
        schedule = [_act("imaging_pass", T0 + timedelta(minutes=5), 120, 800.0)]
        # a dump on every pass
        for c in contacts:
            aos = datetime.fromisoformat(c["aos"])
            schedule.append(_act("data_dump", aos, c["duration_s"]))
        tracker = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule)
        budget = tracker.compute_data_budget()

        assert budget["planned_generation_mb"] == 800.0
        assert budget["plan_closes"] is False
        assert budget["backlog_growth_mb"] > 0
        assert budget["days_to_drain"] > 1.0
        assert any("DOWNLINK_INFEASIBLE" in w for w in budget["warnings"])
        # ...and the shortfall is a real number, not a fudge
        assert budget["planned_downlink_mb"] < budget["planned_generation_mb"]

    def test_a_plan_that_fits_closes(self):
        """A small enough product does close — the check is not just 'always
        complain'."""
        contacts = _contacts(6)
        cap = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts).compute_data_budget()
        per_pass = cap["pass_downlink"][0]["capacity_mb"]
        schedule = [_act("housekeeping_collection", T0 + timedelta(minutes=1),
                         60, per_pass * 0.5)]
        for c in contacts:
            aos = datetime.fromisoformat(c["aos"])
            schedule.append(_act("data_dump", aos, c["duration_s"]))
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()
        assert budget["plan_closes"] is True
        assert budget["backlog_growth_mb"] <= 0.001
        assert budget["planned_downlink_mb"] > 0

    def test_overflow_is_detected_with_a_time(self):
        """Ordering matters: the old netting model could report a comfortable
        margin for a plan that overflows part-way through."""
        contacts = _contacts(4)
        schedule = [
            _act("imaging_pass", T0 + timedelta(hours=i), 120, 800.0)
            for i in range(6)
        ]
        tracker = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule)
        tracker._storage_mb = 1600.0     # small store, to overflow on image 3
        budget = tracker.compute_data_budget()
        assert budget["overflow_mb"] > 0
        assert budget["first_overflow_utc"] is not None
        first = datetime.fromisoformat(budget["first_overflow_utc"])
        assert T0 < first < T0 + timedelta(hours=6)
        assert any("STORAGE_OVERFLOW" in w for w in budget["warnings"])
        assert budget["plan_closes"] is False

    def test_dump_only_counts_inside_a_contact_window(self):
        """A dump scheduled outside any pass moves no data."""
        contacts = _contacts(3)
        outside = T0 + timedelta(minutes=1)     # first AOS is at +20 min
        schedule = [
            _act("imaging_pass", T0, 120, 100.0),
            _act("data_dump", outside, 600),
        ]
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()
        assert budget["planned_downlink_mb"] == 0.0
        assert budget["onboard_data_mb"] == pytest.approx(100.0, abs=0.1)

    def test_unused_passes_are_reported(self):
        contacts = _contacts(5)
        schedule = [_act("imaging_pass", T0, 120, 50.0)]
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()
        assert any("PASS_WITHOUT_DUMP" in w for w in budget["warnings"])

    def test_five_station_daily_capacity_is_reported(self):
        """The physical ceiling the operator needs to see."""
        contacts = _contacts(20, spacing_min=33)
        budget = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts).compute_data_budget()
        assert budget["daily_downlink_capacity_mb"] > 0
        # ...still far below one 800 MB image (the reason for defect #82) ...
        assert budget["daily_downlink_capacity_mb"] < 800.0
        # ... but comfortably above a day's worth of 4 MB products
        assert budget["daily_downlink_capacity_mb"] > 10 * budget["image_size_mb"]

# ── The shipped standard plan ──────────────────────────────────────

class TestShippedNominalOpsDay:
    """The plan an operator actually loads must close (defect #82).

    Uses the REAL propagated contact windows for the configured ground-station
    network, the REAL template from planning/standard_plans.yaml and the REAL
    activity catalogue — so a regression in any of image size, activity data
    volumes or the template's dump cadence trips this test.
    """

    @staticmethod
    def _budget():
        from smo_planner.plan_templates import (
            load_standard_plans, instantiate_template,
        )
        from smo_planner.server import PlannerServer

        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        server._compute_contacts(T0)
        server._compute_ground_track(T0)
        contacts = server._contacts_cache
        plans = load_standard_plans(
            CONFIG_DIR / "planning" / "standard_plans.yaml")
        template = next(t for t in plans.templates if t.id == "nominal_ops_day")
        specs, _warn = instantiate_template(
            template, T0, contacts, server._ground_track_cache)
        for spec in specs:
            server._scheduler.add_activity(
                spec["name"], spec["start_time"], **spec.get("extras", {}))
        schedule = server._scheduler.get_schedule()
        tracker = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule,
            ground_track=server._ground_track_cache)
        return tracker.compute_data_budget(), schedule, contacts

    def test_nominal_ops_day_closes_with_margin(self):
        budget, schedule, _contacts_ = self._budget()
        assert budget["plan_closes"] is True, budget["warnings"]
        assert budget["overflow_mb"] == 0.0
        assert budget["backlog_growth_mb"] <= 0.001
        assert budget["onboard_data_mb"] == pytest.approx(0.0, abs=0.05)
        # Both figures are reported rounded to 0.1 MB, so comparing them can
        # only ever be good to one quantum each; 0.05 was tighter than the
        # numbers can express and tripped as soon as the dump cadence changed
        # by a few minutes. What matters is that essentially everything the
        # plan generates also goes down, which the overflow / backlog / final
        # onboard assertions above already pin.
        assert budget["planned_downlink_mb"] == pytest.approx(
            budget["planned_generation_mb"], abs=0.15)

        # Real margin, not a knife edge: the passes the plan actually dumps on
        # carry at least twice the data the plan generates.
        scheduled_capacity = sum(
            p["capacity_mb"] for p in budget["pass_downlink"]
            if p["dump_scheduled"])
        assert scheduled_capacity > 2.0 * budget["planned_generation_mb"]

    def test_nominal_ops_day_generation_is_two_configured_images(self):
        budget, schedule, _contacts_ = self._budget()
        images = [a for a in schedule if a["name"] == "imaging_pass"]
        assert len(images) == 2
        assert all(a["data_volume_mb"] == budget["image_size_mb"]
                   for a in images)
        # imagery dominates the day's generation now that the calibration and
        # HK volumes are physical (they used to be 50 MB and 10 MB)
        assert budget["planned_generation_mb"] < 3.0 * budget["image_size_mb"]

    def test_dumps_are_spread_across_the_shift(self):
        """The old stride-1 x4 cadence put every dump before the second
        acquisition, so the plan could not close at any image size."""
        _budget, schedule, _contacts_ = self._budget()
        dumps = sorted(a["start_time"] for a in schedule
                       if a["name"] == "data_dump")
        assert len(dumps) >= 6
        last_image = max(a["start_time"] for a in schedule
                         if a["name"] in ("imaging_pass", "calibration"))
        assert any(d > last_image for d in dumps), (
            "no downlink opportunity after the last data-generating activity")
