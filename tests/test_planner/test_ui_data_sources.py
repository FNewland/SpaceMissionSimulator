"""Static checks on the planner UI's data sources (defects #57, #58, #60).

The planner front end used to treat ``time_source == "sim"`` as permission to
fabricate data in the browser: a hard-coded ``GROUND_STATIONS_FALLBACK`` list
drove the timeline and the contacts table, ``SAMPLE_ACTIVITIES`` stood in for
the schedule, and every clock was browser wall-clock. These tests pin the
deletion so it cannot come back.

They are deliberately textual: the page has no JS test harness in this repo,
and the properties being asserted (which identifiers exist, which endpoint is
the only station source) are exactly the ones a regression would reintroduce.
"""
import re
from pathlib import Path

import pytest


STATIC = (Path(__file__).resolve().parents[2] / "packages" / "smo-planner" /
          "src" / "smo_planner" / "static")
INDEX = STATIC / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text()


@pytest.fixture(scope="module")
def script(page: str) -> str:
    """Just the <script> body, so HTML comments are not mistaken for code."""
    m = re.search(r"<script>(.*)</script>", page, re.S)
    assert m, "planner index.html has no inline script block"
    return m.group(1)


# ── No fabricated data ─────────────────────────────────────────────

class TestNoBrowserSideFixtures:

    def test_ground_station_fallback_list_is_gone(self, script):
        assert "GROUND_STATIONS_FALLBACK" not in script

    def test_sample_activities_are_gone(self, script):
        assert "SAMPLE_ACTIVITIES" not in script
        assert "generateSimulatedActivities" not in script
        assert "simulatedActivities" not in script

    def test_synthetic_contact_generator_is_gone(self, script):
        assert "generateSimulatedContacts" not in script
        assert "simulatedContacts" not in script

    def test_sim_mode_no_longer_switches_to_fake_data(self, script):
        assert "useSimulatedContacts" not in script
        assert "useSimulatedSchedule" not in script

    def test_no_hard_coded_station_names_in_the_script(self, script):
        for name in ("Svalbard", "Iqaluit", "Troll", "Inuvik", "O'Higgins",
                     "Ottawa"):
            assert name not in script, (
                f"station name '{name}' is hard-coded in the planner UI; the "
                f"station list must come from /api/ground-stations")


class TestSingleStationSource:

    def test_api_ground_stations_is_the_only_station_source(self, script):
        assert script.count("'/api/ground-stations'") == 1
        # the fetch result is what the rest of the page uses
        assert "groundStations = data.ground_stations" in script
        assert "groundStations.length" in script
        assert "groundStations.findIndex(gs => gs.name === c.station)" in script

    def test_failed_station_fetch_reports_instead_of_inventing(self, script):
        assert "groundStationsError" in script
        assert "Ground-station list unavailable" in script

    def test_timeline_rows_come_from_the_fetched_list(self, script):
        assert "const gsCount = Math.max(groundStations.length, 1);" in script
        assert "groundStations.forEach((gs, i) => {" in script

    def test_first_paint_awaits_the_station_list(self, script):
        assert "await loadGroundStations();" in script


# ── Sim time, not wall clock ───────────────────────────────────────

class TestUIUsesServerTime:

    def test_simnow_exists_and_anchors_on_the_api(self, script):
        assert "function simNow()" in script
        assert "simAnchorMs" in script and "simSpeed" in script
        assert "'/api/time-source'" in script

    @pytest.mark.parametrize("fn,marker", [
        ("updateClock", "const now = simNowDate();"),
        ("updateTerminator", "const now = simNowDate();"),
        ("drawGroundTrack", "const nowMs = simNow();"),
        ("drawTimeline", "const now = simNow();"),
        ("updateContactsTable", "const now = simNow();"),
        # openModal prefills the "Start Time (UTC)" field. What this pins is
        # that the instant comes from the PLANNER's clock; the value is now
        # formatted straight to UTC digits rather than being shifted by the
        # browser's offset first, so there is no `const now` to match.
        ("openModal", "simNowDate()"),
    ])
    def test_time_consuming_paths_use_sim_time(self, script, fn, marker):
        start = script.index("function " + fn)
        body = script[start:start + 1600]
        assert marker in body, f"{fn}() is not on the planner clock"

    def test_no_wall_clock_left_in_the_draw_paths(self, script):
        for fn in ("updateClock", "drawTimeline", "updateContactsTable",
                   "openModal", "updateTerminator"):
            start = script.index("function " + fn)
            body = script[start:start + 1600]
            assert "Date.now()" not in body, f"{fn}() still reads Date.now()"
            assert "new Date()" not in body, f"{fn}() still reads new Date()"

    def test_wall_clock_fallback_is_visible(self, script):
        """The silent degradation (sim unreachable -> wall clock) must show."""
        assert "wall_clock_fallback" in script
        assert "clockDegraded" in script
        assert "WALL CLOCK" in script
        assert "clockSourceBadge" in script


# ── Standard activities + data budget ──────────────────────────────

class TestPlanControls:

    def test_load_standard_activities_control_exists(self, page, script):
        assert 'id="templateSelect"' in page
        assert "loadStandardActivities" in script
        assert "'/api/schedule/load-template'" in script
        assert "'/api/schedule/templates'" in script

    def test_clear_schedule_control_exists(self, script):
        assert "async function clearSchedule()" in script
        assert "method: 'DELETE'" in script

    def test_empty_schedule_says_so(self, script):
        assert "No activities planned" in script


class TestDataBudgetPanel:

    def test_falsy_zero_guard_is_fixed(self, script):
        assert "!dataBudgetData.utilization_percent" not in script
        assert "dataBudgetData.utilization_percent === undefined" in script

    def test_panel_shows_the_feasibility_truth(self, script):
        assert "plan_closes" in script
        assert "PLAN DOES NOT CLOSE" in script
        assert "days_to_drain" in script
        assert "first_overflow_utc" in script

    def test_per_pass_downlink_is_rendered(self, script):
        assert "pass_downlink" in script
        assert "Per-pass downlink" in script
