"""Owner-reported budget defects: data-budget dumps, and the 0 %/100 % SoC.

Two reports, both about the planner's budget arithmetic.

**"The data budget takes into account imaging passes but not planned data
dumps, so it always shows the data budget does not close."** The dumps were
counted, but at the wrong rate. A contact window was collapsed to one averaged
number — the ladder blend ``f_high*HIGH + (1-f_high)*LOW`` smeared evenly over
the whole window, with the ground station's setup/teardown taken off as a flat
percentage. A ``data_dump`` anchored on closest approach (which is exactly what
``standard_plans.yaml`` books, ``at: midpoint``) sits wholly inside the
HIGH-tier arc and wholly clear of the edge overhead, so both approximations
under-credited it and they compounded: ~69 % of the achievable volume on a good
pass. Enough images and the plan stopped closing even though the dumps were
correctly placed. :class:`TestDumpsAreCreditedTheRateTheyGet` pins the
arithmetic.

**"The power budget on PC always shows the final SoC as 0; on the Mac it shows
100."** Neither was a computation. The energy balance needs eclipse and
solar-beta series from the ground track; when the track was missing or covered
a different window, the tracker substituted ``0.35`` and ``30 deg`` and
integrated on them anyway. Those constants land on the OTHER SIDE of zero from
the real orbit-average balance, and the per-pass clamp
``max(0, min(capacity, soc))`` then drove the result to whichever rail matched:
100 % with no track, 0 % with a stale or short one. The same clamp turned a
single NaN into an exact rail too. :class:`TestPowerBudgetRefusesToGuess` pins
the refusal; :class:`TestPowerBudgetHandCalculation` pins the arithmetic when
the inputs really are there.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from smo_planner.budget_tracker import (
    ECLIPSE_BASE_POWER_W,
    SOLAR_ARRAY_MAX_W,
    SUNLIGHT_BASE_POWER_W,
    BudgetTracker,
    budget_params_from_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
T0 = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)
MB = 1024.0 * 1024.0


def _contact(gs, offset_min, duration_s, max_el):
    aos = T0 + timedelta(minutes=offset_min)
    return {
        "gs_name": gs,
        "aos": aos.isoformat(),
        "los": (aos + timedelta(seconds=duration_s)).isoformat(),
        "duration_s": duration_s,
        "max_elevation_deg": max_el,
    }


def _act(name, start, duration_s, data_mb=0.0, power_w=0.0):
    return {"id": abs(hash((name, start.isoformat()))) % 100000, "name": name,
            "start_time": start.isoformat(), "duration_s": duration_s,
            "power_w": power_w, "data_volume_mb": data_mb,
            "state": 0, "state_name": "PLANNED"}


def _dump_centred_on(contact, duration_s=330):
    """A dump anchored on closest approach, the way the templates book it."""
    aos = datetime.fromisoformat(contact["aos"])
    los = datetime.fromisoformat(contact["los"])
    mid = aos + (los - aos) / 2
    return _act("data_dump", mid - timedelta(seconds=duration_s / 2),
                duration_s)


def _illumination(start, hours, step_s=60.0, eclipse_fraction=0.0,
                  beta_deg=0.0, period_s=5580.0):
    """Synthetic eclipse/beta series with EXACTLY known content."""
    n = int(hours * 3600 / step_s)
    out = []
    for i in range(n + 1):
        phase = ((i * step_s) % period_s) / period_s
        out.append({
            "utc": (start + timedelta(seconds=i * step_s)).isoformat(),
            "lat": 0.0, "lon": 0.0, "alt_km": 450.0,
            "in_eclipse": phase < eclipse_fraction,
            "solar_beta_deg": beta_deg,
        })
    return out


# ── 1. Data budget: dumps are credited the rate they actually get ──

class TestDumpsAreCreditedTheRateTheyGet:

    def test_a_dump_on_closest_approach_moves_a_full_image(self):
        """The arithmetic the whole plan rests on.

        ``activity_types.yaml`` sizes ``data_dump`` at 330 s precisely because
        that is one 4 MiB image at the HIGH tier with the 20 % protocol
        allowance: 4 x 1048576 x 8 / (128000 x 0.8) = 327.7 s. So a 330 s dump
        placed on the high-elevation core of a good pass must be credited a
        full image — no more (there is only one image onboard) and, critically,
        no less. The smeared model credited 2.8 MB of it.
        """
        params = budget_params_from_config(CONFIG_DIR)
        image_mb = params["image_size_mb"]
        assert image_mb == 4.0

        contact = _contact("Svalbard", 30, 550, 62.0)
        schedule = [
            _act("imaging_pass", T0 + timedelta(minutes=5), 120, image_mb),
            _dump_centred_on(contact),
        ]
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=[contact], schedule=schedule
        ).compute_data_budget()

        # The dump is entirely inside both the usable window and the HIGH arc,
        # so the only thing limiting it is what is onboard: one image.
        assert b["planned_downlink_mb"] == pytest.approx(image_mb, abs=0.05)
        assert b["pass_downlink"][0]["planned_dump_mb"] == pytest.approx(
            image_mb, abs=0.05)
        assert b["plan_closes"] is True
        assert b["onboard_data_mb"] == pytest.approx(0.0, abs=0.05)

    def test_the_dump_is_not_charged_the_pass_average_rate(self):
        """A 330 s dump on the core of the pass is not 330 s of pass-average.

        On a 550 s / 62 deg Svalbard pass the ladder blend is ~104.6 kbps and
        the usable window is 460 of 550 s, so the old model credited
        330 s x 104.6 kbps x 0.8 x (460/550) = 2.77 MB. The real rate over
        those 330 s is the full 128 kbps: 4.03 MB. Pin the ratio so a
        regression to smearing is unambiguous.
        """
        contact = _contact("Svalbard", 30, 550, 62.0)
        # 40 MB onboard so the dump is rate-limited, not content-limited.
        schedule = [
            _act("imaging_pass", T0 + timedelta(minutes=5), 120, 40.0),
            _dump_centred_on(contact),
        ]
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=[contact], schedule=schedule
        ).compute_data_budget()

        achievable_mb = 128000 * 0.8 * 330 / (8.0 * MB)
        assert achievable_mb == pytest.approx(4.03, abs=0.02)
        assert b["planned_downlink_mb"] == pytest.approx(achievable_mb, abs=0.05)
        # ...and decisively above what the smeared model would have credited
        smeared_mb = 104567 * 0.8 * 330 / (8.0 * MB) * (460.0 / 550.0)
        assert b["planned_downlink_mb"] > 1.4 * smeared_mb

    def test_a_dump_below_the_elevation_gate_gets_the_routine_rung_only(self):
        """The counter-example: the credit is the REAL rate, not an optimistic
        one. A pass that never clears 10 deg cannot hold HIGH at all."""
        contact = _contact("Troll", 30, 420, 6.0)
        schedule = [
            _act("imaging_pass", T0 + timedelta(minutes=5), 120, 40.0),
            _dump_centred_on(contact),
        ]
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=[contact], schedule=schedule
        ).compute_data_budget()
        assert b["pass_downlink"][0]["dump_capable"] is False
        # 330 s at LOW (16 kbps x 0.8) is barely half a megabyte
        assert b["planned_downlink_mb"] == pytest.approx(
            16000 * 0.8 * 330 / (8.0 * MB), abs=0.05)
        assert b["plan_closes"] is False
        assert any("DUMP_BELOW_ELEVATION_GATE" in w for w in b["warnings"])

    def test_setup_and_teardown_eat_the_ENDS_of_the_pass(self):
        """Ground overhead is 60 s after AOS and 30 s before LOS — the two
        lowest-elevation slices. Modelling it as a uniform derating of the
        whole window is what stole a fifth of every correctly-placed dump."""
        contact = _contact("Svalbard", 30, 550, 62.0)
        b = BudgetTracker.from_config(
            CONFIG_DIR, contacts=[contact]).compute_data_budget()
        p = b["pass_downlink"][0]
        assert p["usable_s"] == pytest.approx(550 - 90, abs=0.1)
        # Trimming the ends removes LOW seconds, so the achievable mean rate
        # over the usable window is ABOVE the whole-window ladder blend.
        blend = (p["high_fraction"] * b["dump_rate_bps"]
                 + (1 - p["high_fraction"]) * b["routine_rate_bps"])
        assert p["mean_rate_bps"] > blend
        assert p["high_seconds"] <= p["usable_s"]

    def test_a_known_plan_closes_and_an_over_planned_day_does_not(self):
        """Same passes, same dumps, more images: closure has to flip.

        Eight gate-clearing passes, one 330 s dump centred on each = eight
        images of booked downlink. Six images closes; twelve cannot, and the
        planner must say so rather than either fudging a margin or (as before)
        failing to close a plan that fits.
        """
        contacts = [_contact(["Svalbard", "Inuvik", "Troll", "Iqaluit"][i % 4],
                             90 + i * 95, 550, 45.0)
                    for i in range(8)]
        dumps = [_dump_centred_on(c) for c in contacts]

        def _budget(n_images):
            images = [_act("imaging_pass", T0 + timedelta(minutes=5 + 3 * i),
                           120, 4.0) for i in range(n_images)]
            return BudgetTracker.from_config(
                CONFIG_DIR, contacts=contacts, schedule=images + dumps
            ).compute_data_budget()

        fits = _budget(6)
        assert fits["planned_generation_mb"] == pytest.approx(24.0)
        assert fits["plan_closes"] is True
        assert fits["backlog_growth_mb"] <= 0.001
        assert fits["planned_downlink_mb"] == pytest.approx(24.0, abs=0.1)
        assert not any("DOWNLINK_INFEASIBLE" in w for w in fits["warnings"])

        over = _budget(12)
        assert over["planned_generation_mb"] == pytest.approx(48.0)
        assert over["plan_closes"] is False
        # Eight dumps x ~4.03 MB is ~32 MB of booked downlink; the rest stays up.
        assert over["planned_downlink_mb"] == pytest.approx(8 * 4.03, abs=0.3)
        assert over["backlog_growth_mb"] == pytest.approx(48.0 - 8 * 4.03,
                                                          abs=0.3)
        assert any("DOWNLINK_INFEASIBLE" in w for w in over["warnings"])

    def test_overlapping_windows_are_one_downlink_opportunity(self):
        """Two dishes see the spacecraft at once, but there is one transmit
        chain. The dump must be credited the BEST rung available, not whichever
        station happens to sort first."""
        poor = _contact("Troll", 30, 550, 6.0)      # never clears the gate
        good = _contact("Svalbard", 30, 550, 62.0)  # same minutes, high
        schedule = [_act("imaging_pass", T0 + timedelta(minutes=5), 120, 40.0),
                    _dump_centred_on(good)]
        for order in ([poor, good], [good, poor]):
            b = BudgetTracker.from_config(
                CONFIG_DIR, contacts=order, schedule=schedule
            ).compute_data_budget()
            assert b["planned_downlink_mb"] == pytest.approx(
                128000 * 0.8 * 330 / (8.0 * MB), abs=0.05), (
                    f"station order {[c['gs_name'] for c in order]} changed "
                    f"the credited volume")


# ── 2. Power budget: degenerate input is a refusal, not a rail ──────

class TestPowerBudgetRefusesToGuess:

    CONTACTS = [_contact("Svalbard", 60, 600, 40.0),
                _contact("Inuvik", 180, 600, 30.0)]

    def _track(self, **kw):
        return _illumination(T0 + timedelta(minutes=55), hours=3.0, **kw)

    def test_no_ground_track_is_reported_not_rendered_as_100(self):
        b = BudgetTracker(contacts=self.CONTACTS, ground_track=[]
                          ).compute_power_budget()
        assert b["computable"] is False
        assert b["final_soc"] is None
        assert b["total_charge_wh"] is None
        assert "illumination" in b["reason"].lower()
        # The exact failure the owner saw: a rail dressed as a result.
        assert b["final_soc"] not in (0.0, 100.0, 0, 100)

    def test_a_track_that_does_not_cover_the_window_is_reported(self):
        """The planner's cached map track is 3 h; the contact horizon is 24 h.
        Integrating the 21 h shortfall on constants is what produced 0 %."""
        contacts = self.CONTACTS + [_contact("Troll", 20 * 60, 600, 30.0)]
        b = BudgetTracker(contacts=contacts, ground_track=self._track()
                          ).compute_power_budget()
        assert b["computable"] is False
        assert b["final_soc"] is None
        assert "no eclipse/beta data" in b["reason"]
        assert b["ground_track_end_utc"] < b["window_end_utc"]

    def test_a_non_finite_beta_is_reported_not_clamped_to_a_rail(self):
        """``max(0.0, min(100.0, nan))`` is 100.0 in Python — and the
        mirror-image argument order gives 0.0. That is how one bad sample
        became a confident headline number on one machine and its opposite on
        another."""
        track = [dict(pt, solar_beta_deg=float("nan")) for pt in self._track()]
        b = BudgetTracker(contacts=self.CONTACTS, ground_track=track
                          ).compute_power_budget()
        assert b["computable"] is False
        assert b["final_soc"] is None
        assert "Non-finite" in b["reason"]

    def test_no_contacts_is_reported(self):
        b = BudgetTracker(contacts=[]).compute_power_budget()
        assert b["computable"] is False
        assert b["final_soc"] is None

    def test_a_zero_capacity_battery_is_reported(self):
        b = BudgetTracker(contacts=self.CONTACTS, ground_track=self._track(),
                          battery_capacity_wh=0.0).compute_power_budget()
        assert b["computable"] is False
        assert b["final_soc"] is None

    def test_the_clamp_no_longer_launders_nan(self):
        from smo_planner.budget_tracker import _clamp_finite
        import math
        assert max(0.0, min(100.0, float("nan"))) == 100.0   # the old bug
        assert max(0.0, min(float("nan"), 100.0)) == 0.0     # ...and its twin
        assert math.isnan(_clamp_finite(float("nan"), 0.0, 100.0))
        assert _clamp_finite(150.0, 0.0, 100.0) == 100.0
        assert _clamp_finite(-1.0, 0.0, 100.0) == 0.0


class TestPowerBudgetHandCalculation:
    """With real inputs the integration must match arithmetic done by hand."""

    def test_final_soc_matches_an_independent_hand_calculation(self):
        # One 3600 s pass, fully sunlit, beta 0. Nothing else in the plan.
        contact = _contact("Svalbard", 60, 3600, 40.0)
        start = datetime.fromisoformat(contact["aos"])
        track = _illumination(start - timedelta(minutes=5), hours=1.5,
                              eclipse_fraction=0.0, beta_deg=0.0)
        b = BudgetTracker(contacts=[contact], ground_track=track,
                          initial_soc=50.0, battery_capacity_wh=100.0
                          ).compute_power_budget()
        assert b["computable"] is True

        # By hand: the window is exactly the pass (first AOS .. last LOS), so
        # there is no inter-pass interval. 3600 s = 1.0 h, fully sunlit:
        #   drain  = SUNLIGHT_BASE_POWER_W * 1 h
        #   charge = SOLAR_ARRAY_MAX_W * cos(0) * 1 h
        hours = 1.0
        drain_wh = SUNLIGHT_BASE_POWER_W * hours
        charge_wh = SOLAR_ARRAY_MAX_W * hours
        assert b["total_drain_wh"] == pytest.approx(drain_wh, abs=0.05)
        assert b["total_charge_wh"] == pytest.approx(charge_wh, abs=0.05)

        # 50 Wh + 64 Wh - 32 Wh = 82 Wh of a 100 Wh battery = 82.0 %
        assert b["final_soc"] == pytest.approx(82.0, abs=0.1)
        assert b["soc_saturated"] is False

    def test_eclipse_and_beta_enter_the_arithmetic(self):
        """Half the interval in eclipse at beta 60 deg: charge is
        SOLAR_ARRAY_MAX_W * cos(60) * 0.5 h, drain is the base over the whole
        hour. Pins that BOTH illumination inputs are actually used."""
        import math
        contact = _contact("Svalbard", 60, 3600, 40.0)
        start = datetime.fromisoformat(contact["aos"])
        track = _illumination(start - timedelta(minutes=5), hours=1.5,
                              eclipse_fraction=0.5, beta_deg=60.0,
                              period_s=3600.0)
        b = BudgetTracker(contacts=[contact], ground_track=track,
                          initial_soc=80.0, battery_capacity_wh=100.0
                          ).compute_power_budget()
        assert b["computable"] is True
        assert b["total_drain_wh"] == pytest.approx(
            ECLIPSE_BASE_POWER_W * 0.5 + SUNLIGHT_BASE_POWER_W * 0.5, abs=0.5)
        assert b["total_charge_wh"] == pytest.approx(
            SOLAR_ARRAY_MAX_W * math.cos(math.radians(60.0)) * 0.5, abs=0.5)

    def test_a_saturated_result_is_labelled_as_a_limit(self):
        """A plan whose balance is negative still gets a number — a battery
        really does stop at 0 % — but the answer must carry the fact that it
        is a floor, so the operator cannot read it as a prediction."""
        contacts = [_contact("Svalbard", 60 + i * 90, 600, 40.0)
                    for i in range(12)]
        start = datetime.fromisoformat(contacts[0]["aos"])
        # Permanent eclipse: no generation at all, so the battery must floor.
        track = _illumination(start - timedelta(minutes=5), hours=20.0,
                              eclipse_fraction=1.0, beta_deg=0.0)
        b = BudgetTracker(contacts=contacts, ground_track=track,
                          initial_soc=20.0, battery_capacity_wh=100.0
                          ).compute_power_budget()
        assert b["computable"] is True
        assert b["final_soc"] == 0.0
        assert b["soc_saturated"] is True
        assert b["soc_floor_hits"] > 0
        assert b["first_soc_floor_utc"] is not None
        assert any("SOC_FLOOR_REACHED" in w for w in b["warnings"])


# ── 3. The endpoint keeps the refusal intact ────────────────────────

class TestPowerBudgetEndpoint:

    @pytest.mark.asyncio
    async def test_endpoint_serves_a_computable_budget_for_the_real_plan(self):
        """End to end: the server must hand the tracker an illumination series
        covering the CONTACT horizon, not the 3 h map track. Otherwise the fix
        above would simply turn a wrong number into a permanent 'unavailable'.
        """
        from smo_planner.server import PlannerServer
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        server._compute_contacts(T0)
        server._compute_ground_track(T0)
        tracker = server._make_budget_tracker(
            ground_track=server._illumination_track())
        budget = tracker.compute_power_budget()
        assert budget["computable"] is True, budget.get("reason")
        assert budget["final_soc"] is not None
        assert 0.0 <= budget["final_soc"] <= 100.0
        assert len(budget["pass_predictions"]) > 10

    @pytest.mark.asyncio
    async def test_the_map_track_alone_would_not_have_been_enough(self):
        """The counter-example that proves the previous test is not vacuous:
        the cached 3 h map track really does fail to cover the horizon."""
        from smo_planner.server import PlannerServer
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        server._compute_contacts(T0)
        server._compute_ground_track(T0)
        budget = server._make_budget_tracker(
            ground_track=server._ground_track_cache).compute_power_budget()
        assert budget["computable"] is False
        assert budget["final_soc"] is None
