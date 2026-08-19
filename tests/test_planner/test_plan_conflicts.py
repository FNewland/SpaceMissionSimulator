"""Conflict detection, per-resource overlap, and the "Start Time (UTC)" field.

Two owner-reported defects:

1. The Add-activity modal's "Start Time (UTC)" was parsed as *browser-local*
   time (``new Date("2026-03-10T12:00")`` on a zone-less ``datetime-local``
   value), so a typed time landed hours out. Everything on this platform is sim
   UTC end to end (docs/plan_2026-08c/TIME_ARCHITECTURE.md), so the label was
   right and the parsing was wrong.

2. Loading a standard plan from the template dropdown produced a large number
   of "overlap" conflicts on validation. Three things were wrong at once:

   * ``check_conflicts`` reported every declared mutual exclusion in the plan
     regardless of *when* the two activities ran — 8 dumps and 2 images in a
     24 h plan gave 36 "conflicts" between activities hours apart;
   * ``check_time_overlap`` was global rather than per-resource, so a
     ``data_dump`` (ttc + obdh) sharing a clock slot with a ``calibration``
     (payload + aocs) was reported even though nothing is contended;
   * ``_upcoming_passes`` counted contact *windows*, but the five-station
     network produces windows that overlap in time and the spacecraft has one
     transmit chain, so ``downlink_catchup`` booked two dumps on the same few
     minutes; and the flexible daylight anchors were resolved without regard
     for the pass-pinned dumps, dropping an imaging_pass on top of one.
"""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from smo_planner.activity_scheduler import ActivityScheduler, ActivityState
from smo_planner.plan_templates import (
    load_standard_plans, instantiate_template, _upcoming_passes,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
INDEX = (REPO_ROOT / "packages" / "smo-planner" / "src" / "smo_planner" /
         "static" / "index.html")


@pytest.fixture(scope="module")
def activity_types() -> list[dict]:
    return yaml.safe_load(
        (CONFIG_DIR / "planning" / "activity_types.yaml").read_text()
    )["activity_types"]


def _sched(activity_types) -> ActivityScheduler:
    return ActivityScheduler(activity_types)


# ── 1. The UTC start-time field ────────────────────────────────────

@pytest.fixture(scope="module")
def script() -> str:
    m = re.search(r"<script>(.*)</script>", INDEX.read_text(), re.S)
    assert m, "planner index.html has no inline script block"
    return m.group(1)


class TestStartTimeIsUtc:
    """Textual pins on the modal's time handling (no JS harness in this repo)."""

    def test_input_value_is_never_parsed_with_bare_new_date(self, script):
        """`new Date(<zone-less string>)` is defined as BROWSER-LOCAL."""
        assert "new Date(startTime)" not in script

    def test_parser_builds_the_instant_from_utc_fields(self, script):
        assert "function utcInputValueToDate" in script
        m = re.search(r"function utcInputValueToDate[\s\S]{0,600}?\n}", script)
        assert m and "Date.UTC(" in m.group(0)

    def test_prefill_writes_utc_digits_not_local_ones(self, script):
        # The old prefill compensated for the browser offset so the field
        # showed LOCAL digits under a UTC label.
        assert "getTimezoneOffset() * 60000" not in script
        m = re.search(r"function dateToUtcInputValue[\s\S]{0,500}?\n}", script)
        assert m, "no UTC formatter for the datetime-local value"
        assert "getUTCFullYear" in m.group(0)
        assert "getUTCHours" in m.group(0)

    def test_submit_sends_the_utc_parsed_instant(self, script):
        assert "utcInputValueToDate(startTime)" in script
        assert "start_time: startDate.toISOString()" in script

    def test_local_equivalent_hint_exists(self, script):
        assert "function updateStartTimeHint" in script
        assert "actStartTimeHint" in script
        # The hint is the only place the browser's own zone is allowed, and it
        # is display-only.
        assert "toLocaleString()" in script

    def test_hint_is_wired_to_the_input(self):
        page = INDEX.read_text()
        m = re.search(r'<input[^>]*id="actStartTime"[^>]*>', page)
        assert m, "actStartTime input not found"
        assert "updateStartTimeHint()" in m.group(0)

    def test_no_other_datetime_input_in_the_page(self):
        """Sweep: if another datetime input appears it needs the same care."""
        page = INDEX.read_text()
        ids = re.findall(r'<input[^>]*type="(?:datetime-local|date|time)"'
                         r'[^>]*id="(\w+)"', page)
        assert ids == ["actStartTime"], ids

    def test_every_display_formatter_reads_utc_fields(self, script):
        """No `getHours()`/`getMinutes()`/`getDate()` — those are local."""
        assert not re.search(r"\.get(Hours|Minutes|Seconds|Date|Month|"
                             r"FullYear|Day)\(\)", script)

    def test_locale_time_formatting_pins_the_utc_zone(self, script):
        """Any toLocale*Time/Date call must name a zone or be the local hint."""
        for call in re.findall(r"toLocale(?:Time|Date)String\([^)]*\)", script):
            assert "timeZone: 'UTC'" in call, call


# ── 2a. Conflict detection semantics ───────────────────────────────

class TestMutualExclusionIsTimeGated:
    """`conflicts_with` is a statement about SIMULTANEITY."""

    def test_overlapping_exclusive_pair_is_detected(self, activity_types):
        s = _sched(activity_types)
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        # data_dump.conflicts_with includes imaging_pass
        act = s.add_activity("imaging_pass", "2026-03-10T10:02:00+00:00")
        assert any("data_dump" in w for w in act.get("warnings", []))
        issues = s.validate_pass_plan([])
        assert [i for i in issues if i["type"] == "conflict"]

    def test_same_pair_hours_apart_is_not_a_conflict(self, activity_types):
        s = _sched(activity_types)
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        act = s.add_activity("imaging_pass", "2026-03-10T14:00:00+00:00")
        assert "warnings" not in act
        assert s.validate_pass_plan([]) == []

    def test_back_to_back_exclusive_pair_is_not_a_conflict(self, activity_types):
        s = _sched(activity_types)
        # data_dump is 360 s: 10:00:00 -> 10:06:00 (2026-08d PLAN-2 — the
        # 330 s dump window plus the ladder set-up and restoration).
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        s.add_activity("imaging_pass", "2026-03-10T10:06:00+00:00")
        assert s.validate_pass_plan([]) == []

    def test_cancelled_activities_are_ignored(self, activity_types):
        s = _sched(activity_types)
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        s.add_activity("imaging_pass", "2026-03-10T10:02:00+00:00")
        s.update_state(1, ActivityState.CANCELLED)
        assert [i for i in s.validate_pass_plan([])
                if i["type"] == "conflict"] == []


class TestOverlapIsPerResource:
    """Two activities may share a clock slot if they hold different hardware."""

    def test_disjoint_subsystems_may_overlap(self, activity_types):
        s = _sched(activity_types)
        # data_dump  -> ttc + obdh ; calibration -> payload + aocs
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        act = s.add_activity("calibration", "2026-03-10T10:01:00+00:00")
        assert "warnings" not in act
        assert [i for i in s.validate_pass_plan([])
                if i["type"] == "time_overlap"] == []

    def test_shared_subsystem_overlap_is_detected(self, activity_types):
        s = _sched(activity_types)
        # both hold payload + aocs
        s.add_activity("imaging_pass", "2026-03-10T10:00:00+00:00")
        s.add_activity("calibration", "2026-03-10T10:01:00+00:00")
        overlaps = [i for i in s.validate_pass_plan([])
                    if i["type"] == "time_overlap"]
        assert overlaps
        assert any("payload" in i["message"] for i in overlaps)

    def test_two_dumps_on_the_same_slot_are_detected(self, activity_types):
        """One transmit chain: two dumps at once is a real clash."""
        s = _sched(activity_types)
        s.add_activity("data_dump", "2026-03-10T10:00:00+00:00")
        s.add_activity("data_dump", "2026-03-10T10:02:00+00:00")
        assert [i for i in s.validate_pass_plan([])
                if i["type"] == "time_overlap"]

    def test_undeclared_resources_stay_conservative(self):
        """An activity type with no requires_subsystems competes with all."""
        s = ActivityScheduler([
            {"name": "mystery_a", "duration_s": 600, "conflicts_with": []},
            {"name": "mystery_b", "duration_s": 600, "conflicts_with": []},
        ])
        s.add_activity("mystery_a", "2026-03-10T10:00:00+00:00")
        s.add_activity("mystery_b", "2026-03-10T10:01:00+00:00")
        assert [i for i in s.validate_pass_plan([])
                if i["type"] == "time_overlap"]

    def test_offset_spellings_of_the_same_instant_still_overlap(
            self, activity_types):
        """No lexical string comparison anywhere in the overlap test."""
        s = _sched(activity_types)
        s.add_activity("imaging_pass", "2026-03-10T10:00:00Z")
        s.add_activity("calibration", "2026-03-10T11:00:30+01:00")  # = 10:00:30
        assert [i for i in s.validate_pass_plan([])
                if i["type"] == "time_overlap"]


# ── 2b. Contact windows vs downlink opportunities ──────────────────

class TestUpcomingPasses:

    NOW = datetime(2026, 3, 10, 0, 0, 0, tzinfo=timezone.utc)

    def _c(self, gs, aos_min, dur_s, el):
        aos = self.NOW + timedelta(minutes=aos_min)
        return {"gs_name": gs, "aos": aos.isoformat(),
                "los": (aos + timedelta(seconds=dur_s)).isoformat(),
                "duration_s": dur_s, "max_elevation_deg": el}

    def test_overlapping_windows_collapse_to_one_opportunity(self):
        contacts = [
            self._c("Svalbard", 10, 480, 20.0),
            self._c("Troll", 14, 480, 55.0),      # overlaps Svalbard
            self._c("Inuvik", 90, 480, 30.0),
        ]
        out = _upcoming_passes(contacts, self.NOW, None, 10.0)
        assert len(out) == 2
        # the better link wins
        assert out[0]["gs_name"] == "Troll"
        assert out[1]["gs_name"] == "Inuvik"

    def test_separate_windows_are_kept(self):
        contacts = [self._c("Svalbard", 10, 480, 20.0),
                    self._c("Troll", 30, 480, 55.0)]
        assert len(_upcoming_passes(contacts, self.NOW, None, 0.0)) == 2

    def test_elevation_gate_and_station_filter_still_apply(self):
        contacts = [self._c("Svalbard", 10, 480, 5.0),
                    self._c("Troll", 30, 480, 55.0)]
        assert len(_upcoming_passes(contacts, self.NOW, None, 10.0)) == 1
        assert len(_upcoming_passes(contacts, self.NOW, "Svalbard", 0.0)) == 1

    def test_windows_are_ordered_chronologically_not_lexically(self):
        """'+01:00' and 'Z' spellings must not decide the order."""
        a = {"gs_name": "A", "aos": "2026-03-10T11:00:00+01:00",   # 10:00 UTC
             "los": "2026-03-10T11:05:00+01:00", "max_elevation_deg": 20.0}
        b = {"gs_name": "B", "aos": "2026-03-10T10:30:00Z",
             "los": "2026-03-10T10:35:00Z", "max_elevation_deg": 20.0}
        out = _upcoming_passes([b, a], self.NOW, None, 0.0)
        assert [c["gs_name"] for c in out] == ["A", "B"]


# ── 2c. Every shipped standard plan loads clean ────────────────────

def _shipped_templates():
    return load_standard_plans(
        CONFIG_DIR / "planning" / "standard_plans.yaml").templates


@pytest.fixture(scope="module")
def planner_server():
    from smo_planner.server import PlannerServer
    return PlannerServer(CONFIG_DIR, http_port=0, time_source="real")


# Six load epochs spread around the orbit and the day, so the result does not
# depend on where in the eclipse/contact cycle the operator happens to click.
LOAD_EPOCHS = [datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
               + timedelta(minutes=k * 197) for k in range(6)]


@pytest.mark.parametrize("t0", LOAD_EPOCHS, ids=lambda d: d.strftime("%H%M"))
@pytest.mark.parametrize("template_id", [t.id for t in _shipped_templates()])
def test_shipped_plan_loads_with_zero_conflicts(
        planner_server, activity_types, template_id, t0):
    """The owner-reported defect, against REAL propagated geometry."""
    from smo_planner.orbit_planner import OrbitPlanner
    from smo_common.orbit.propagator import OrbitPropagator

    srv = planner_server
    srv._compute_contacts(t0)
    prop = OrbitPropagator(srv._tle1, srv._tle2, ground_stations=srv._gs_list,
                           earth_radius_km=srv._earth_r)
    template = next(t for t in _shipped_templates() if t.id == template_id)
    track = OrbitPlanner(prop).predict_ground_track(
        t0, duration_hours=template.horizon_hours + 2, step_s=60)

    specs, warnings = instantiate_template(
        template, t0, contacts=srv._contacts_cache, ground_track=track,
        activity_types=activity_types)
    assert specs, f"{template_id} produced no activities: {warnings}"

    sched = ActivityScheduler(activity_types)
    for spec in specs:
        sched.add_activity(spec["name"], spec["start_time"], **spec["extras"])

    issues = [i for i in sched.validate_pass_plan(srv._contacts_cache)
              if i["type"] in ("conflict", "time_overlap")]
    assert issues == [], (
        f"{template_id} at {t0.isoformat()} reported {len(issues)} "
        f"conflicts: {issues[:5]}")


@pytest.mark.parametrize("template_id", [t.id for t in _shipped_templates()])
def test_shipped_plan_activities_are_all_in_the_future(
        planner_server, activity_types, template_id):
    """Loading a plan must not book anything the plan can no longer execute."""
    t0 = LOAD_EPOCHS[0]
    srv = planner_server
    srv._compute_contacts(t0)
    template = next(t for t in _shipped_templates() if t.id == template_id)
    specs, _ = instantiate_template(
        template, t0, contacts=srv._contacts_cache,
        ground_track=srv._ground_track_cache, activity_types=activity_types)
    # A pass already in progress at t0 can legitimately anchor a dump slightly
    # behind t0; nothing may be more than one pass-length in the past.
    for s in specs:
        start = datetime.fromisoformat(s["start_time"])
        assert start > t0 - timedelta(minutes=15), s


# ── 2d. Deliberate overlaps are still caught ───────────────────────

class TestDeliberateOverlapStillDetected:

    def test_hand_built_overlapping_pair_in_a_loaded_plan(self, activity_types):
        """Load a real template, then add a clashing activity on top of one."""
        t0 = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        contacts = [{
            "gs_name": "Svalbard", "aos": (t0 + timedelta(minutes=20)).isoformat(),
            "los": (t0 + timedelta(minutes=28)).isoformat(),
            "duration_s": 480, "max_elevation_deg": 40.0}]
        track = [{"utc": (t0 + timedelta(minutes=i)).isoformat(),
                  "lat": 0.0, "lon": 0.0, "in_eclipse": False}
                 for i in range(240)]
        template = next(t for t in _shipped_templates()
                        if t.id == "payload_checkout")
        specs, _ = instantiate_template(
            template, t0, contacts=contacts, ground_track=track,
            activity_types=activity_types)
        sched = ActivityScheduler(activity_types)
        for spec in specs:
            sched.add_activity(spec["name"], spec["start_time"], **spec["extras"])
        assert [i for i in sched.validate_pass_plan(contacts)
                if i["type"] in ("conflict", "time_overlap")] == []

        # Now drop a second imaging_pass squarely on top of the first.
        first_image = next(a for a in sched.get_schedule()
                           if a["name"] == "imaging_pass")
        sched.add_activity("imaging_pass", first_image["start_time"])
        issues = [i for i in sched.validate_pass_plan(contacts)
                  if i["type"] in ("conflict", "time_overlap")]
        assert issues, "a deliberately overlapping pair was not detected"
        assert any(i["type"] == "time_overlap" for i in issues)

    def test_template_slip_is_reported_not_silent(self, activity_types):
        """A flexible anchor that has to move says so."""
        t0 = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        # A pass already in progress, so the pinned dump (midpoint - 2.75 min,
        # 330 s) covers the offset_from_now housekeeping slot at now + 1 min.
        # Both hold obdh, so they cannot both run.
        contacts = [{
            "gs_name": "Svalbard", "aos": (t0 - timedelta(minutes=1)).isoformat(),
            "los": (t0 + timedelta(minutes=7)).isoformat(),
            "duration_s": 480, "max_elevation_deg": 40.0}]
        track = [{"utc": (t0 + timedelta(minutes=i)).isoformat(),
                  "lat": 0.0, "lon": 0.0, "in_eclipse": False}
                 for i in range(240)]
        template = next(t for t in _shipped_templates()
                        if t.id == "payload_checkout")
        specs, warnings = instantiate_template(
            template, t0, contacts=contacts, ground_track=track,
            activity_types=activity_types)
        sched = ActivityScheduler(activity_types)
        for spec in specs:
            sched.add_activity(spec["name"], spec["start_time"], **spec["extras"])
        assert [i for i in sched.validate_pass_plan(contacts)
                if i["type"] in ("conflict", "time_overlap")] == []
        assert any("slipped to" in w for w in warnings), warnings

    def test_resolution_without_a_catalogue_is_unchanged(self, activity_types):
        """Legacy callers that pass no catalogue get the old single-phase pass."""
        t0 = datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)
        track = [{"utc": (t0 + timedelta(minutes=i)).isoformat(),
                  "lat": 0.0, "lon": 0.0, "in_eclipse": False}
                 for i in range(240)]
        template = next(t for t in _shipped_templates()
                        if t.id == "payload_checkout")
        specs, warnings = instantiate_template(
            template, t0, contacts=[], ground_track=track)
        assert specs
        assert not any("slipped to" in w for w in warnings)
