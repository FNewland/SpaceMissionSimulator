"""Defect #86 — the planner budget respects the TM data-rate LADDER.

Before this change the planner had one number, ``DOWNLINK_RATE_BPS = 64000``,
and every pass, every activity and every constraint checker used it. The
spacecraft now ships a three-rung ladder in
``configs/eosat1/subsystems/ttc.yaml tm_rate_tiers``:

    BEACON  1 000 bps   bootloader / beacon only
    LOW    16 000 bps   ROUTINE OPERATIONS — ungated
    HIGH  128 000 bps   dumps only — antenna + DOWNLINK mode + elevation >= 10 deg

so a plan that books capacity at a flat rate books capacity the spacecraft will
refuse to deliver. These tests pin the three things that must now be true:

  1. the two rungs are read from config and kept apart (per-activity split);
  2. the 10 deg elevation gate is a real planning constraint — a pass that
     never clears it is costed at LOW, and the shipped templates do not book
     dumps on it;
  3. the shipped ``nominal_ops_day`` closes *because* its dumps are booked on
     gate-clearing passes at HIGH, not because a flat rate flattered it.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from smo_planner.budget_tracker import (
    BudgetTracker,
    budget_params_from_config,
    fraction_above_elevation,
    hk_stream_bps_from_config,
)
from smo_planner.constraint_checkers import DataVolumeConstraintChecker
from smo_planner.plan_templates import instantiate_template, load_standard_plans


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
T0 = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)


def _ttc() -> dict:
    return yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())


def _tiers() -> dict:
    return {t["name"]: t for t in _ttc()["tm_rate_tiers"]}


def _contacts(specs):
    """specs: list of (gs_name, offset_min, duration_s, max_el)."""
    out = []
    for gs, off, dur, el in specs:
        aos = T0 + timedelta(minutes=off)
        out.append({
            "gs_name": gs,
            "aos": aos.isoformat(),
            "los": (aos + timedelta(seconds=dur)).isoformat(),
            "duration_s": dur,
            "max_elevation_deg": el,
        })
    return out


def _act(name, start, duration_s, data_mb=0.0, **extra):
    act = {"id": abs(hash((name, start))) % 10000, "name": name,
           "start_time": start.isoformat(), "duration_s": duration_s,
           "power_w": 50, "data_volume_mb": data_mb,
           "state": 0, "state_name": "PLANNED"}
    act.update(extra)
    return act


# ── 1. Per-activity rate split ─────────────────────────────────────

class TestPerActivityRateSplit:

    def test_the_two_rungs_come_from_the_ladder_and_stay_apart(self):
        p = budget_params_from_config(CONFIG_DIR)
        tiers = _tiers()
        assert p["routine_rate_bps"] == tiers[_ttc()["default_tier"]]["bps"]
        assert p["dump_rate_bps"] == tiers["HIGH"]["bps"]
        assert p["dump_rate_bps"] == 8 * p["routine_rate_bps"], \
            "the ladder's HIGH:LOW step is the whole point — 8x, +9.03 dB"
        assert "tm_rate_tiers" in p["sources"]["routine_rate_bps"]

    def test_routine_traffic_is_not_costed_at_the_dump_rate(self):
        """A pass carries HIGH only where the gate is satisfied. The rest of
        the window — and every pass below the gate — is routine-rate seconds,
        so per-pass capacity must land strictly between the two rungs."""
        contacts = _contacts([("Svalbard", 20, 480, 30.0)])
        b = BudgetTracker.from_config(CONFIG_DIR, contacts=contacts
                                      ).compute_data_budget()
        pass1 = b["pass_downlink"][0]
        usable_s = pass1["usable_s"]
        eff = 0.8
        low_only = b["routine_rate_bps"] * eff * usable_s / (8 * 1024 * 1024)
        high_only = b["dump_rate_bps"] * eff * usable_s / (8 * 1024 * 1024)
        assert low_only < pass1["capacity_mb"] < high_only
        assert pass1["mean_rate_bps"] == pytest.approx(
            pass1["high_fraction"] * b["dump_rate_bps"]
            + (1 - pass1["high_fraction"]) * b["routine_rate_bps"], abs=200.0)

    def test_data_dump_reaches_high_and_the_checker_agrees(self):
        checker = DataVolumeConstraintChecker.from_params(
            budget_params_from_config(CONFIG_DIR))
        assert checker.dump_rate_bps == 128000
        assert checker.routine_rate_bps == 16000
        # A dump on a 30 deg pass gets HIGH; on a 7 deg pass it gets LOW.
        assert checker.dump_rate_for_pass(30.0) == 128000
        assert checker.dump_rate_for_pass(7.0) == 16000
        assert checker.dump_rate_for_pass(None) == 128000

    def test_the_dump_activity_is_one_image_long_at_high(self):
        """activity_types.yaml data_dump duration is derived, not chosen: it is
        exactly what one configured image costs at the HIGH tier."""
        types = yaml.safe_load(
            (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
        dump = next(a for a in types["activity_types"] if a["name"] == "data_dump")
        p = budget_params_from_config(CONFIG_DIR)
        one_image_s = (p["image_size_mb"] * 1024 * 1024 * 8
                       / (p["dump_rate_bps"] * (1 - p["protocol_overhead"])))
        assert one_image_s == pytest.approx(327.68, abs=0.1)
        assert one_image_s <= dump["duration_s"] <= one_image_s * 1.1
        # ...and it commands the gate before it commands the rate.
        s8 = [s for s in dump["command_sequence"] if s.get("service") == 8]
        assert [int(str(s["func_id"]), 0) if isinstance(s["func_id"], str)
                else int(s["func_id"]) for s in s8] == [0x4F, 0x41, 0x4F]

    def test_housekeeping_is_a_channel_cost_at_the_routine_rung(self):
        """HK is 36 % of the day's contact time at LOW and 4.5 % at HIGH —
        the number that makes 1 kbps indefensible and 16 kbps the floor."""
        hk_bps = hk_stream_bps_from_config(CONFIG_DIR)
        # Phase B (brief 01 WP-11) added SID 8 (EPS power management, 8 s):
        # ~+2 bps of channel on top of the 1557 bps baseline.
        # Phase G added the four rate-ladder parameters 0x0539-0x053C to the
        # TTC HK SID (brief 08 finding 13) — 7 bytes every 8 s = +8 bps of
        # channel, which is the price of the console being able to show
        # ACTIVE vs COMMANDED tier from routine housekeeping.
        assert hk_bps == pytest.approx(1567.0, abs=2.0)
        contacts = _contacts([("Svalbard", 20 + 45 * i, 445, 25.0)
                              for i in range(24)])
        b = BudgetTracker.from_config(CONFIG_DIR, contacts=contacts
                                      ).compute_data_budget()
        assert b["hk_stream_mb_per_day"] == pytest.approx(16.1, abs=0.3)
        frac = b["hk_contact_fraction_percent"]
        assert frac["LOW"] == pytest.approx(8 * frac["HIGH"], rel=0.02)
        assert frac["LOW"] > 8 * frac["HIGH"] / 1.01     # LOW is the expensive one
        assert 20.0 < frac["LOW"] < 60.0
        assert frac["HIGH"] < 10.0


# ── 2. The elevation gate ──────────────────────────────────────────

class TestElevationGate:

    def test_gate_value_comes_from_the_tier_not_a_literal(self):
        p = budget_params_from_config(CONFIG_DIR)
        assert p["default_min_dump_elevation_deg"] == \
            _tiers()["HIGH"]["min_elevation_deg"] == 10.0

    def test_pass_geometry_decides_how_much_of_a_window_can_dump(self):
        """Derived from spherical geometry, not a lookup table: a high pass
        spends most of its window above 10 deg, a grazing one none of it."""
        # Even a zenith pass spends its wings below 10 deg: the ceiling is
        # gamma(10)/gamma(5) = 13.088/16.481 = 0.794, not 1.0.
        assert fraction_above_elevation(90.0, 10.0) == pytest.approx(0.794, abs=0.01)
        assert fraction_above_elevation(30.0, 10.0) == pytest.approx(0.758, abs=0.01)
        assert fraction_above_elevation(12.0, 10.0) == pytest.approx(0.466, abs=0.01)
        assert fraction_above_elevation(10.0, 10.0) == 0.0
        assert fraction_above_elevation(7.0, 10.0) == 0.0
        # monotone in max elevation
        vals = [fraction_above_elevation(e, 10.0) for e in (11, 15, 25, 45, 80)]
        assert vals == sorted(vals)

    def test_a_pass_below_the_gate_cannot_dump_at_high(self):
        contacts = _contacts([("Troll", 20, 420, 7.0),
                              ("Troll", 90, 420, 30.0)])
        b = BudgetTracker.from_config(CONFIG_DIR, contacts=contacts
                                      ).compute_data_budget()
        low, high = b["pass_downlink"]
        assert low["dump_capable"] is False
        assert low["dump_tier"] == "LOW"
        assert high["dump_capable"] is True
        assert high["dump_tier"] == "HIGH"
        # ...and the capacity difference is the ladder, not a fudge factor
        assert high["capacity_mb"] > 5 * low["capacity_mb"]
        assert b["passes_dump_capable"] == 1
        assert b["passes_total"] == 2

    def test_booking_a_dump_below_the_gate_is_warned_about(self):
        contacts = _contacts([("Troll", 20, 420, 7.0)])
        aos = datetime.fromisoformat(contacts[0]["aos"])
        schedule = [_act("imaging_pass", T0, 120, 4.0),
                    _act("data_dump", aos, 330)]
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()
        assert any("DUMP_BELOW_ELEVATION_GATE" in w for w in b["warnings"])
        # the plan does NOT close: at LOW that pass moves well under one image
        assert b["plan_closes"] is False

    def test_the_same_dump_above_the_gate_does_close(self):
        contacts = _contacts([("Svalbard", 20, 600, 40.0)])
        aos = datetime.fromisoformat(contacts[0]["aos"])
        schedule = [_act("imaging_pass", T0, 120, 4.0),
                    _act("data_dump", aos, 540)]
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=contacts, schedule=schedule
        ).compute_data_budget()
        assert not any("DUMP_BELOW_ELEVATION_GATE" in w for w in b["warnings"])
        assert b["plan_closes"] is True

    def test_templates_only_book_dumps_on_gate_clearing_passes(self):
        plans = load_standard_plans(CONFIG_DIR / "planning" / "standard_plans.yaml")
        gate = _tiers()["HIGH"]["min_elevation_deg"]
        for template in plans.templates:
            for entry in template.activities:
                if entry.type != "data_dump":
                    continue
                assert entry.min_pass_elevation_deg == gate, (
                    f"template '{template.id}' books dumps without the "
                    f"{gate:.0f} deg HIGH-tier gate")

    def test_a_template_skips_the_passes_it_cannot_use(self):
        plans = load_standard_plans(CONFIG_DIR / "planning" / "standard_plans.yaml")
        template = plans.get("downlink_catchup")
        contacts = _contacts([("Troll", 20, 420, 6.0),
                              ("Svalbard", 70, 480, 35.0),
                              ("Troll", 120, 420, 8.0),
                              ("Inuvik", 170, 450, 22.0)])
        specs, warnings = instantiate_template(template, T0, contacts, [])
        booked = {s["extras"]["station"] for s in specs}
        assert booked == {"Svalbard", "Inuvik"}
        assert any("above 10 deg" in w for w in warnings)


# ── 3. The shipped plan closes because it books HIGH ────────────────

class TestNominalOpsDayClosesOnTheLadder:

    @staticmethod
    def _budget():
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
        return tracker.compute_data_budget(), schedule

    def test_it_closes_and_every_dump_pass_can_hold_high(self):
        budget, schedule = self._budget()
        assert budget["plan_closes"] is True, budget["warnings"]
        assert not any("DUMP_BELOW_ELEVATION_GATE" in w
                       for w in budget["warnings"])
        dump_passes = [p for p in budget["pass_downlink"] if p["dump_scheduled"]]
        assert len(dump_passes) >= 6
        assert all(p["dump_capable"] for p in dump_passes)
        assert all(p["max_elevation_deg"] >= 10.0 for p in dump_passes)

    def test_the_margin_is_real_and_comes_from_the_high_tier(self):
        budget, _schedule = self._budget()
        scheduled_capacity = sum(p["capacity_mb"] for p in budget["pass_downlink"]
                                 if p["dump_scheduled"])
        generation = budget["planned_generation_mb"]
        margin_pct = 100.0 * (1.0 - generation / scheduled_capacity)
        assert margin_pct > 70.0, f"only {margin_pct:.0f} % margin"
        # At the routine rung the SAME passes could not carry the day: the
        # plan closes because it books HIGH, not because the day is small.
        ratio = budget["routine_rate_bps"] / budget["dump_rate_bps"]
        assert scheduled_capacity * ratio < generation * 1.5

    def test_a_pass_spent_dumping_is_a_pass_not_spent_otherwise(self):
        budget, _schedule = self._budget()
        assert 0.0 < budget["dump_contact_fraction_percent"] < 100.0
        assert any("PASS_WITHOUT_DUMP" in w for w in budget["warnings"])
        # one image is a schedulable fraction of a pass, not a rounding error
        assert budget["image_pass_fraction_percent"] > 25.0
