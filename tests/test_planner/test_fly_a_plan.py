"""2026-08d PLAN-* — FLY THE PLANNER'S OWN EXPANSION AGAINST A REAL SPACECRAFT.

The planner's activity templates are command sequences, and until this test
nothing ever executed one. They were checked for func-id ROUTING (does func 26
land on the payload?) but never for whether the resulting sequence is a thing
the spacecraft will actually do. It was not:

  * ``imaging_pass`` opened with S8 func **1**, captioned
    "AOCS_SET_MODE_FINE_POINT". Func 1 is ``AOCS_DESATURATE``; set-mode is func
    0. So every planner-scheduled imaging pass began with a 20-50 minute wheel
    desaturation, never reached FINE_POINT, timed out on its own
    ``wait_for aocs.mode == 5``, and the capture came back S1.8 / **0x0021**
    "Capture refused: AOCS not in FINE_POINT". The owner's "an imaging pass in
    planner does a desat but does not leave enough time nor transition to fine
    point before the imaging", exactly.
  * ``data_dump`` left DOWNLINK mode ~1 s after starting the dump, so the
    engine's dump pacer (which reads the ACTIVE TM rate every tick) ran the
    rest of a 330 s dump at LOW — an eighth of the booked rate.
  * ``orbit_manoeuvre`` never opened a latch valve (PROP_ARM refused 0x0B09),
    never commanded a burn-capable attitude mode (0x0B04), compared a
    continuously-varying catbed temperature for EQUALITY, and booked 1500 s for
    a sequence that needs >1900 s.
  * ``software_upload`` loaded 0x40000000, which is not in the memory map;
    the dispatcher's catalogued-form decode read it as boot ROM and refused.

Everything below runs real ECSS packets through ``engine._dispatch_tc`` — every
acceptance gate, power gate, on-board precondition and S1 verification — and
asserts on the parameter pool the spacecraft actually publishes.

WHAT "THE PLANNER'S EXPANSION" MEANS HERE. The sequences are read through
``ActivityScheduler.add_activity`` / ``get_command_sequence`` and through
``ImagingPlanner.generate_capture_sequence`` — the same objects
``PlannerServer._handle_upload_schedule`` hands to the MCS — and each step is
encoded with ``ProcedureRunner._build_data_hex``, the shared ground-side
encoder. Nothing here re-implements the plan.
"""
from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_mcs.procedure_runner import ProcedureRunner
from smo_planner.activity_scheduler import ActivityScheduler
from smo_planner.imaging_planner import ImagingPlanner
from smo_simulator.engine import SimulationEngine

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"

#: Dotted telemetry name -> parameter id, straight off the mission's own
#: parameter list, so a `wait_for` in the plan is resolved the way the MCS
#: resolves it rather than against a table this test invents.
PARAM_ID = {p["name"]: int(p["id"]) for p in yaml.safe_load(
    (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())["parameters"]}

#: S1 subtypes that mean "the spacecraft said no": 1.2 acceptance failure,
#: 1.4 execution-start failure, 1.6 progress failure, 1.8 execution failure.
NEGATIVE_ACKS = (2, 4, 6, 8)


def _activity_types() -> list[dict]:
    return yaml.safe_load(
        (CONFIG_DIR / "planning" / "activity_types.yaml").read_text()
    )["activity_types"]


# ═══════════════════════════════════════════════════════════════════
# Flight harness
# ═══════════════════════════════════════════════════════════════════

class PlanFlight:
    """A commissioned EOSAT-1 plus an executor for planner command steps."""

    def __init__(self, engine: SimulationEngine):
        self.eng = engine
        self.t = 0.0
        self._seq = 0
        self.refusals: list[str] = []
        self.sent: list[str] = []

    # ── telemetry ──
    def drain(self) -> list:
        out = []
        q = self.eng.tm_queue
        while not q.empty():
            p = decommutate_packet(q.get_nowait())
            if p and p.secondary:
                out.append(p)
        return out

    def p(self, name: str):
        return self.eng.params.get(PARAM_ID[name])

    def run(self, seconds: float) -> None:
        for _ in range(int(seconds)):
            self.eng._tick_once(1.0)
            self.t += 1.0
            if self.eng.tm_queue.qsize() > 1200:
                self.drain()

    # ── telecommand ──
    def send(self, service: int, subtype: int, data: bytes = b"",
             label: str = "") -> list:
        self.drain()
        self.eng._dispatch_tc(build_tc_packet(
            apid=100, service=service, subtype=subtype, data=data,
            seq_count=self._seq & 0x3FFF))
        self._seq += 1
        pk = self.drain()
        self.sent.append(label or f"S{service}.{subtype}")
        for r in pk:
            if r.secondary.service != 1 or r.secondary.subtype not in NEGATIVE_ACKS:
                continue
            code = (struct.unpack('>H', r.data_field[4:6])[0]
                    if len(r.data_field) >= 6 else 0)
            self.refusals.append(
                f"{label or f'S{service}.{subtype}'}: "
                f"S1.{r.secondary.subtype} error 0x{code:04X}")
        return pk

    def s8(self, func: int, *args: int, label: str = "") -> list:
        return self.send(8, 1, bytes([func, *args]), label=label or f"func {func}")

    # ── the planner's own steps ──
    def step(self, step: dict) -> None:
        """Execute one planner command-sequence step.

        `wait_s` advances sim time (this is the dwell the S11 on-board
        schedule also books). `wait_for` polls the parameter pool the way the
        MCS ProcedureRunner does — and, like the on-board schedule, creates no
        time of its own beyond waiting for something that must already be on
        its way.
        """
        if "wait_s" in step:
            self.run(float(step["wait_s"]))
            return
        if "wait_for" in step:
            w = step["wait_for"]
            name = w["parameter"]
            assert name in PARAM_ID, (
                f"plan waits on '{name}', which is not a telemetry parameter")
            deadline = self.t + float(w.get("timeout_s", 30))
            op = str(w.get("operator", "==") or "==")
            while self.t < deadline:
                self.run(1)
                cur = self.p(name)
                if cur is not None and ProcedureRunner._compare(
                        float(cur), op, float(w["value"])):
                    return
            pytest.fail(
                f"plan step timed out: {name} {op} {w['value']} after "
                f"{w.get('timeout_s')} s (last value {self.p(name)!r})"
                + (f"; refusals so far: {self.refusals}"
                   if self.refusals else ""))
            return
        assert "service" in step, f"unrecognised plan step: {step!r}"
        data_hex = ProcedureRunner._build_data_hex(step)
        self.send(int(step["service"]), int(step["subtype"]),
                  bytes.fromhex(data_hex) if data_hex else b"",
                  label=step.get("description",
                                 f"S{step['service']}.{step['subtype']}"))

    def fly(self, sequence: list[dict]) -> None:
        for st in sequence:
            self.step(st)
        self.run(3)          # let the last commands reach the HK snapshot

    # ── helpers ──
    def assert_all_accepted(self, what: str) -> None:
        assert not self.refusals, (
            f"{what}: the spacecraft refused "
            f"{len(self.refusals)} telecommand(s):\n  "
            + "\n  ".join(self.refusals))

    def clear_refusals(self) -> None:
        self.refusals = []

    def advance_to_sunlight(self, need_s: float) -> None:
        """Park at the start of a sunlit arc with at least `need_s` of it left.

        Placement is part of the plan: the propulsion ARM interlock refuses in
        eclipse (INH_ECLIPSE) and the AOCS demotes to ECLIPSE_PROPAGATE, so an
        activity whose BURN falls in umbra cannot fly however correct its
        commanding is. See orbit_manoeuvre's
        `timing.daylight_required_at_offset_s`.
        """
        for _ in range(60):
            nxt = self.eng.orbit.next_eclipse_transition()
            if not nxt.get("in_eclipse"):
                remaining = float(nxt.get("time_to_eclipse_entry_s") or 0.0)
                if remaining >= need_s:
                    return
                # Too late in this arc — sit out the rest of it.
                self._coast(remaining + 60.0)
                continue
            self._coast(float(nxt.get("time_to_eclipse_exit_s") or 300.0) + 30.0)
        pytest.fail("no sunlit arc long enough for the manoeuvre")

    def _coast(self, seconds: float) -> None:
        """Run forward in chunks, keeping the battery off the load-shed floor
        (see `top_up_battery`: this test is about commanding, not endurance)."""
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            chunk = min(300.0, remaining)
            self.run(chunk)
            remaining -= chunk
            self.top_up_battery()

    def _sunlight_remaining_s(self) -> float:
        """Seconds of sunlight left, from the propagator's own forward scan."""
        try:
            nxt = self.eng.orbit.next_eclipse_transition()
        except Exception:                                  # pragma: no cover
            return 0.0
        if not nxt or nxt.get("in_eclipse"):
            return 0.0
        secs = nxt.get("time_to_eclipse_entry_s")
        return float(secs) if secs else 0.0

    def telemetry(self) -> dict:
        """The parameter pool as the dotted name -> value dict the planner's
        pre-condition checker consumes."""
        return {name: float(self.eng.params[pid])
                for name, pid in PARAM_ID.items()
                if pid in self.eng.params}

    def await_pre_conditions(self, scheduler, activity: dict,
                             timeout_s: float = 4000.0) -> None:
        """Run forward until the activity's declared pre-conditions hold."""
        deadline = self.t + timeout_s
        unmet: list[str] = ["(not evaluated)"]
        while self.t < deadline:
            unmet = scheduler.check_pre_conditions(activity, self.telemetry())
            if not unmet:
                return
            self._coast(120.0)
        pytest.fail(f"{activity['name']}: pre-conditions never held — {unmet}")

    def top_up_battery(self, soc_pct: float = 88.0) -> None:
        """INSTRUCTOR action, not a plan step.

        EOSAT-1's routine-ops power balance is slightly negative with the FPA
        cooler running and the transmitter up, so a multi-hour flight-through
        discharges the battery into an EPS load shed and the AOCS line goes
        dark. That is correct spacecraft behaviour and it has its own tests
        (the planner's power budget, tests/test_planner/test_budget_*). This
        test is about COMMANDING, so the instructor edits the world between
        activities — exactly the `set_battery_soc` override that exists for
        "allow us to continue running a sim where the battery is getting
        low" — rather than letting a power question decide a commanding
        question.
        """
        self.eng._handle_instructor_cmd(
            {"type": "set_battery_soc", "soc_pct": float(soc_pct)})
        self.run(2)


@pytest.fixture(scope="module")
def flight(tmp_path_factory):
    """A commissioned spacecraft in ROUTINE OPS, which is the state every
    activity in this plan declares as its entry condition.

    Reached by command, not by reaching into model state: star trackers
    powered (the FINE_POINT precondition bitmask 0x02C5 needs a head holding
    >= 5 stars and a converged MEKF), the payload in STANDBY so the FPA cooler
    is running, and enough settled time for both.
    """
    import os
    os.environ.setdefault("SMO_DUMP_DIR",
                          str(tmp_path_factory.mktemp("dumps")))
    if not CONFIG_DIR.exists():                            # pragma: no cover
        pytest.skip("EOSAT-1 config dir not present")

    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    # Commissioned: application image, NOMINAL mission phase, a permanently
    # in-view station (the test stand-in for scheduled passes).
    eng._spacecraft_phase = 6
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng.params[0x05FF] = 1
    eng._in_contact = True
    eng.subsystems["obdh"]._state.sw_image = 1
    eng.subsystems["obdh"]._state.watchdog_armed = False
    for line in eng.subsystems["eps"]._state.power_lines:
        eng.subsystems["eps"]._state.power_lines[line] = True
    ttc = eng.subsystems["ttc"]._state
    ttc.frame_sync = ttc.carrier_lock = ttc.bit_sync = ttc.pa_on = True
    ttc.antenna_deployed = True
    ttc.beacon_mode = False
    ttc._lock_timer = 60.0
    aocs = eng.subsystems["aocs"]._state
    aocs.mode = aocs.commanded_mode = 4
    aocs.time_in_mode = 120.0
    aocs.mtq_enabled = True
    for i in range(4):
        aocs.active_wheels[i] = True
    for sid in eng._hk_enabled:
        eng._hk_enabled[sid] = True

    f = PlanFlight(eng)
    f.s8(4, 1, label="ST1_POWER on")
    f.s8(5, 1, label="ST2_POWER on")
    f.s8(26, 1, label="PAYLOAD_SET_MODE STANDBY")
    f.run(700)
    f.assert_all_accepted("entry state")
    assert int(f.p("aocs.mode")) == 4
    assert int(f.p("aocs.fine_ready")) == 0, (
        "entry state: FINE_POINT preconditions (0x02C5) not satisfied — "
        f"mask 0x{int(f.p('aocs.fine_ready')):02X}")
    assert int(f.p("payload.fpa_ready")) == 1
    f.clear_refusals()
    return f


@pytest.fixture(scope="module")
def plan():
    """The realistic day, expanded by the planner's own scheduler.

    One imaging pass, one downlink, one momentum dump and one orbit
    manoeuvre — the four activities the owner named — instantiated through
    ``ActivityScheduler.add_activity`` so every duration, resource list and
    command sequence comes from the catalogue exactly as an uploaded plan's
    would.
    """
    types = _activity_types()
    sched = ActivityScheduler(types)
    t0 = datetime(2026, 3, 10, 6, 0, 0, tzinfo=timezone.utc)
    order = ["imaging_pass", "data_dump", "momentum_desaturation",
             "orbit_manoeuvre"]
    by_name = {a["name"]: a for a in types}
    when = t0
    for name in order:
        sched.add_activity(name, when.isoformat())
        gap = float((by_name[name].get("timing") or {}).get(
            "min_gap_after_s", 0.0) or 0.0)
        when += timedelta(seconds=by_name[name]["duration_s"] + gap + 60)
    return sched


# ═══════════════════════════════════════════════════════════════════
# The plan is feasible ON PAPER before it is flown
# ═══════════════════════════════════════════════════════════════════

class TestThePlanValidates:

    def test_no_activity_books_less_time_than_its_sequence_needs(self, plan):
        """2026-08d PLAN-7. `duration_s` is a booking; `command_sequence` is
        what the spacecraft does. Nothing compared them, so imaging_pass
        booked 120 s for 197 s of mandatory dwell and orbit_manoeuvre booked
        1500 s for >1900 s."""
        for activity in plan.get_schedule():
            need = ActivityScheduler.sequence_min_duration_s(activity)
            assert activity["duration_s"] >= need, (
                f"{activity['name']} books {activity['duration_s']} s for a "
                f"{need} s sequence")
            assert plan.check_sequence_timing(activity) == []

    def test_every_catalogue_activity_declares_its_timing(self):
        for a in _activity_types():
            assert "timing" in a, f"{a['name']} has no timing: block"
            need = ActivityScheduler.sequence_min_duration_s(a)
            assert a["duration_s"] >= need, a["name"]
            assert (a.get("max_duration_s") or a["duration_s"]) >= need

    def test_every_wait_for_and_pre_condition_names_real_telemetry(self):
        """2026-08d PLAN-8. A `wait_for` or a `pre_condition` naming telemetry
        that does not exist is not a check — it is a 30 s timeout mid-pass, or
        (in the planner's checker) an "unavailable" that reads like a pass.
        Three shipped pre-conditions named parameters the mission does not
        have: `payload.fpa_temp_c` (it is `payload.fpa_temp`, 0x0601),
        `ttc.in_contact` (there is none; 0x0501 `ttc.link_status` is the real
        reading) and `eps.battery_soc` (it is `eps.bat_soc`, 0x0101)."""
        import re
        for activity in _activity_types():
            for step in activity.get("command_sequence") or []:
                wf = step.get("wait_for")
                if wf:
                    assert wf["parameter"] in PARAM_ID, (
                        f"{activity['name']}: wait_for names "
                        f"'{wf['parameter']}', which is not in "
                        f"telemetry/parameters.yaml")
            for cond in activity.get("pre_conditions") or []:
                m = re.match(r"^\s*([\w.]+)\s*([><=!]+)\s*([\d.eE+-]+)\s*$",
                             str(cond))
                assert m, f"{activity['name']}: unparseable pre-condition {cond!r}"
                assert m.group(1) in PARAM_ID, (
                    f"{activity['name']}: pre-condition names "
                    f"'{m.group(1)}', which is not in telemetry/parameters.yaml")

    def test_the_whole_plan_validates_clean(self, plan):
        issues = plan.validate_pass_plan([], telemetry=None)
        assert issues == [], issues

    def test_the_checker_catches_a_desat_immediately_before_an_image(self):
        """The infeasible plan the owner described, refused on the ground.

        A desaturation runs 20-50 minutes, holds the attitude at DESAT entry,
        and the FINE_POINT preconditions (0x02C5) need ~10 minutes to become
        satisfiable again afterwards. `timing.min_gap_after_s` says so, and
        the checker now enforces it."""
        types = _activity_types()
        sched = ActivityScheduler(types)
        desat = next(a for a in types if a["name"] == "momentum_desaturation")
        t0 = datetime(2026, 3, 10, 6, 0, 0, tzinfo=timezone.utc)
        sched.add_activity("momentum_desaturation", t0.isoformat())
        image = sched.add_activity(
            "imaging_pass",
            (t0 + timedelta(seconds=desat["duration_s"] + 30)).isoformat())
        assert image.get("warnings"), \
            "an imaging pass 30 s after a momentum dump must be refused"
        assert any("recovery gap" in w for w in image["warnings"])
        assert any(i["type"] == "separation"
                   for i in sched.validate_pass_plan([], telemetry=None))

    def test_the_checker_catches_an_under_booked_activity(self):
        sched = ActivityScheduler(_activity_types())
        act = sched.add_activity("imaging_pass",
                                 "2026-03-10T06:00:00+00:00",
                                 duration_s=120)   # the shipped-defect value
        assert any("Under-booked" in w for w in act.get("warnings", []))

    def test_lead_time_survives_the_s11_upload(self, plan):
        """The S11 on-board schedule SKIPS `wait_for` steps — a telemetry
        check cannot run on board — and advances the execution offset only on
        `wait_s`. So every real settle time has to be an explicit `wait_s`, or
        the uploaded schedule fires the capture back-to-back with the mode
        command. This is what "does not leave enough time... before the
        imaging" looks like once the plan reaches the spacecraft."""
        for activity in plan.get_schedule():
            seq = activity["command_sequence"]
            on_board_span = sum(float(s.get("wait_s", 0) or 0.0)
                                for s in seq if "wait_s" in s)
            declared = float((activity.get("timing") or {}).get(
                "sequence_min_duration_s", 0.0))
            assert on_board_span >= declared, (
                f"{activity['name']}: only {on_board_span} s of the declared "
                f"{declared} s survives an S11 upload — the rest is in "
                f"wait_for steps the on-board schedule drops")

        imaging = next(a for a in plan.get_schedule()
                       if a["name"] == "imaging_pass")
        seq = imaging["command_sequence"]
        fine_at = next(i for i, s in enumerate(seq)
                       if s.get("description") == "AOCS_SET_MODE_FINE_POINT")
        cap_at = next(i for i, s in enumerate(seq)
                      if s.get("description") == "PAYLOAD_CAPTURE_IMAGE")
        lead = sum(float(s.get("wait_s", 0) or 0.0)
                   for s in seq[fine_at:cap_at] if "wait_s" in s)
        assert lead >= 90.0, (
            "the FINE_POINT transition must be scheduled with real settle "
            "time before the shutter, not assumed instantaneous")


# ═══════════════════════════════════════════════════════════════════
# Fly it
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestFlyTheDay:
    """Ordered: each activity leaves the state the next one entered from."""

    # ── 1. imaging ──
    def test_imaging_pass_produces_a_product(self, flight, plan):
        seq = plan.get_command_sequence(
            next(a["id"] for a in plan.get_schedule()
                 if a["name"] == "imaging_pass"))
        before = int(flight.p("payload.image_count") or 0)

        # The defect, pinned: func 1 is DESATURATE, func 0 is SET_MODE.
        aocs_funcs = [int(str(s["func_id"]), 0) if isinstance(s["func_id"], str)
                      else int(s["func_id"])
                      for s in seq if s.get("service") == 8]
        assert 1 not in aocs_funcs, \
            "S8 func 1 is AOCS_DESATURATE — a 20-50 min dump inside an image"

        flight.top_up_battery()
        flight.clear_refusals()
        flight.fly(seq)
        flight.assert_all_accepted("imaging_pass")

        assert int(flight.p("payload.image_count") or 0) == before + 1, \
            "the capture did not produce a product"
        catalog = flight.eng.subsystems["payload"]._state.image_catalog
        assert catalog, "no catalogue entry"
        assert catalog[-1].get("lines", 0) == 2048
        assert catalog[-1].get("size_mb", 0) > 0

        # Restoration: not left in IMAGING (which latches desat_inhibit_auto)
        # nor in FINE_POINT (from which the next dump would be refused).
        assert int(flight.p("payload.mode")) == 1, "left in IMAGING"
        assert int(flight.p("aocs.mode")) == 4, "left in FINE_POINT"

    def test_the_target_driven_capture_flies_too(self, flight):
        """The planner's OTHER way of ordering a capture
        (POST /api/imaging/schedule -> ImagingPlanner). It used to command the
        payload only and hit the same 0x0021 refusal by a different route."""
        planner = ImagingPlanner([{
            "id": "T1", "name": "Test", "priority": "high",
            "region": {"min_lat": 10.0, "max_lat": 15.0,
                       "min_lon": -35.5, "max_lon": -25.0},
        }])
        capture = planner.generate_capture_sequence(
            "T1", "2026-03-10T06:00:00Z")
        assert capture["duration_s"] >= \
            ActivityScheduler.sequence_min_duration_s(capture)
        before = int(flight.p("payload.image_count") or 0)
        flight.top_up_battery()
        flight.clear_refusals()
        flight.fly(capture["command_sequence"])
        flight.assert_all_accepted("ImagingPlanner capture")
        assert int(flight.p("payload.image_count") or 0) == before + 1
        assert int(flight.p("aocs.mode")) == 4
        assert int(flight.p("payload.mode")) == 1

    # ── 2. downlink ──
    def test_data_dump_runs_at_high_and_hands_the_ladder_back(self, flight,
                                                              plan):
        seq = plan.get_command_sequence(
            next(a["id"] for a in plan.get_schedule()
                 if a["name"] == "data_dump"))
        flight.top_up_battery()
        flight.clear_refusals()

        # Sample the ACTIVE TM rate every simulated second across the whole
        # activity: the engine's S15 dump pacer reads exactly this
        # (engine._tick_dump_emission -> _current_tm_rate_bps), so it is the
        # rate the dump really moves data at.
        rates: list[float] = []
        real_run = flight.run

        def sampling_run(seconds: float) -> None:
            for _ in range(int(seconds)):
                real_run(1)
                rates.append(float(flight.eng._current_tm_rate_bps()))

        flight.run = sampling_run          # type: ignore[method-assign]
        try:
            flight.fly(seq)
        finally:
            flight.run = real_run          # type: ignore[method-assign]
        flight.assert_all_accepted("data_dump")

        dwell_s = float(next(a for a in _activity_types()
                             if a["name"] == "data_dump")["timing"]["dump_dwell_s"])
        at_high = sum(1 for r in rates if r >= 128000)
        assert at_high >= dwell_s, (
            f"the dump held HIGH for only {at_high} s of the {dwell_s:.0f} s "
            f"dump window — the ladder was dropped while the store was still "
            f"emptying")

        # Handed back: LOW, out of DOWNLINK mode. An activity that walks away
        # in HIGH/DOWNLINK is the defect class already hit on the procedure
        # cards.
        assert int(flight.p("ttc.tm_data_rate")) == 16000
        assert int(flight.p("ttc.downlink_mode")) == 0

    def test_the_dump_command_order_matches_the_on_board_gates(self, plan):
        """beacon off -> antenna deployed -> DOWNLINK mode -> HIGH. The first
        two are entry conditions; the third is the one the plan owns, and
        `ttc_basic._tier_permission_error` refuses HIGH (S1.8 0x0007) without
        it."""
        seq = plan.get_command_sequence(
            next(a["id"] for a in plan.get_schedule()
                 if a["name"] == "data_dump"))
        s8 = [s for s in seq if s.get("service") == 8]
        dl_on = next(i for i, s in enumerate(s8)
                     if int(s["func_id"]) == 0x4F and s["params"] == [1])
        rate_high = next(i for i, s in enumerate(s8)
                         if int(s["func_id"]) == 0x41 and s["params"] == [1])
        rate_low = next(i for i, s in enumerate(s8)
                        if int(s["func_id"]) == 0x41 and s["params"] == [0])
        dl_off = next(i for i, s in enumerate(s8)
                      if int(s["func_id"]) == 0x4F and s["params"] == [0])
        assert dl_on < rate_high, "HIGH commanded before DOWNLINK mode"
        assert rate_high < rate_low < dl_off, (
            "the ladder must be stepped DOWN before the gate is dropped, or "
            "HIGH stays latched as the COMMANDED tier")

    # ── 3. momentum management ──
    def test_momentum_desaturation_runs_and_returns_to_nominal(self, flight,
                                                               plan):
        # Load the wheels so the dump has something to do — by COMMAND
        # (RW_SET_SPEED_BIAS, S8 func 8), so the body dynamics stay consistent
        # with the wheel states. Writing `h_wheel` directly instead leaves the
        # attitude solution inconsistent and the spacecraft safes itself,
        # which would be an artefact of the test rather than of the plan.
        for wheel in range(4):
            flight.send(8, 1,
                        bytes([8, wheel]) + struct.pack(">f", 3000.0),
                        label=f"RW_SET_SPEED_BIAS {wheel}")
        flight.top_up_battery()
        flight.run(5)
        h0 = float(flight.p("aocs.total_momentum"))
        assert h0 > 0.01, h0

        seq = plan.get_command_sequence(
            next(a["id"] for a in plan.get_schedule()
                 if a["name"] == "momentum_desaturation"))
        flight.clear_refusals()
        flight.fly(seq)
        flight.assert_all_accepted("momentum_desaturation")

        assert float(flight.p("aocs.total_momentum")) < h0 / 2.0, \
            "the dump did not remove momentum"
        # The dump is over: DESAT (mode 7) has handed control back. It hands
        # back to the pre-desat COMMANDED mode, possibly via the automatic
        # ECLIPSE_PROPAGATE hand-over if it finishes near umbra entry.
        assert int(flight.p("aocs.mode")) in (4, 8), \
            "DESAT must return to the pre-desat mode"
        assert not flight.eng.subsystems["aocs"]._state.desat_ineffective, \
            "the dump hit its 3600 s on-board timeout"

    def test_the_desat_is_commanded_from_a_mode_that_permits_it(self, plan):
        """`aocs_basic` refuses AOCS_DESATURATE in FINE_POINT
        (DESAT_REFUSED_FINE) and in OFF/SAFE_BOOT/DETUMBLE. The activity
        leaves FINE deliberately instead of discovering it in the pass."""
        seq = plan.get_command_sequence(
            next(a["id"] for a in plan.get_schedule()
                 if a["name"] == "momentum_desaturation"))
        s8 = [s for s in seq if s.get("service") == 8]
        assert int(s8[0]["func_id"]) == 0x00 and s8[0]["params"] == [4]
        assert int(s8[1]["func_id"]) == 0x01

    # ── 4. the day ends somewhere sane ──
    def test_the_spacecraft_is_left_in_routine_operations(self, flight):
        """After imaging, downlink and momentum management the spacecraft is
        back in the configuration the next activity's pre-conditions assume.

        This is the defect class the owner already hit on the procedure
        cards: an activity that walks away in HIGH/DOWNLINK, or leaves the
        payload in IMAGING (which latches `aocs.desat_inhibit_auto` and makes
        the next momentum dump refuse), or leaves the AOCS in FINE_POINT
        (from which a dump is refused outright)."""
        flight.top_up_battery()
        flight.run(30)
        assert int(flight.p("aocs.mode")) in (4, 8), \
            "AOCS not in NOMINAL (or its automatic eclipse hand-over)"
        assert int(flight.p("payload.mode")) == 1, "payload not in STANDBY"
        assert int(flight.p("ttc.tm_data_rate")) == 16000, "left at HIGH"
        assert int(flight.p("ttc.downlink_mode")) == 0, "left in DOWNLINK mode"
        assert not flight.eng.subsystems["aocs"]._state.desat_inhibit_auto, \
            "the payload was left imaging — the next dump would be refused"
        assert not flight.refusals, flight.refusals


# ═══════════════════════════════════════════════════════════════════
# The remaining catalogue activities, flown one at a time
# ═══════════════════════════════════════════════════════════════════

@pytest.mark.slow
@pytest.mark.parametrize("name,await_preconds", [
    # `await_preconds` is False for activities whose pre-conditions are
    # CONTINGENCY triggers rather than entry conditions: thermal_rebalance
    # fires on `tcs.temp_regulation_margin < 2.0`, which by construction never
    # holds on a healthy spacecraft. The commanding still has to be flyable.
    ("calibration", True),
    ("housekeeping_collection", True),
    ("software_upload", True),
    ("thermal_rebalance", False),
])
def test_other_activities_are_accepted_end_to_end(flight, name,
                                                  await_preconds):
    """Every remaining activity the planner can expand, except the two whose
    whole purpose is to safe the spacecraft (load_shedding, fdir_recovery) and
    which are therefore flown nowhere near a nominal-ops fixture.

    Each is flown only once its own declared ``pre_conditions`` hold, which is
    also the point: they are now EVALUABLE. Three of them used to name
    telemetry that does not exist (``payload.fpa_temp_c``, ``ttc.in_contact``,
    ``eps.battery_soc``) or compare against ``true``, so the checker reported
    them as unavailable rather than checking anything — including
    ``calibration``, whose real blocker is the star-tracker attitude reference
    the AOCS demands (CAL_ERR_NO_REFERENCE 0x0A03).
    """
    sched = ActivityScheduler(_activity_types())
    activity = sched.add_activity(name, "2026-03-10T06:00:00+00:00")
    flight.top_up_battery()
    # Some activities need a whole sunlit arc, not merely to START in one:
    # the gyro-calibration arc aborts the instant the star-tracker attitude
    # reference goes away, which is what umbra entry does to the estimator.
    need_daylight_s = float((activity.get("timing") or {}).get(
        "daylight_required_at_offset_s", 0.0) or 0.0)
    if need_daylight_s:
        flight.advance_to_sunlight(need_daylight_s + 300.0)
    if await_preconds:
        flight.await_pre_conditions(sched, activity)
    flight.clear_refusals()
    flight.fly(activity["command_sequence"])
    flight.assert_all_accepted(name)


# ═══════════════════════════════════════════════════════════════════
# The orbit manoeuvre, flown LAST
# ═══════════════════════════════════════════════════════════════════
#
# It goes last on purpose. The burn does not run to completion in this model:
# the thrust-misalignment torque (prop.yaml `thrust_offset_mm: 5.0` ->
# 1.65 mN.m, against a 2 mN.m per-wheel authority and a 0.020 rad/s NOMINAL
# control bandwidth) drives `aocs.att_error` past 1.0 deg within ~4 s, and S19
# rule 8001 aborts the burn autonomously at ~0.08 of the commanded 0.85 m/s.
# NOM-013 step 5 already calls that out ("an off-axis burn abort is an AOCS
# anomaly first: GO CTG-002"), and the FDIR safing that follows leaves the
# spacecraft where a contingency procedure, not a plan, has to pick it up.
#
# That is spacecraft physics, not a plan defect and not a refused telecommand,
# and it is reported to the owner rather than papered over by trimming the
# plan's delta-V until the abort stops happening. What the PLAN owns — and
# what is asserted here — is that every telecommand is accepted, every ARM
# interlock is satisfied, thrust is actually delivered, and the propulsion
# system is handed back safed with its valves shut and its heater off.

@pytest.mark.slow
class TestFlyTheOrbitManoeuvre:

    def test_orbit_manoeuvre_arms_and_burns(self, flight, plan):
        activity = next(a for a in plan.get_schedule()
                        if a["name"] == "orbit_manoeuvre")
        seq = plan.get_command_sequence(activity["id"])

        # Placement is part of the plan: the burn, not the activity start,
        # has to be in daylight (timing.daylight_required_at_offset_s).
        need = float(activity["timing"]["daylight_required_at_offset_s"]) + 300
        flight.advance_to_sunlight(need)
        flight.top_up_battery()

        dv_before = float(flight.p("prop.dv_accum") or 0.0)
        flight.clear_refusals()
        flight.fly(seq)
        flight.assert_all_accepted("orbit_manoeuvre")

        # The latch valve was opened (its absence made PROP_ARM refuse
        # 0x0B09), the bed was hot, the burn was armed and executed, and
        # thrust was delivered.
        assert float(flight.p("prop.catbed_temp")) >= 180.0
        assert float(flight.p("prop.dv_accum") or 0.0) > dv_before, \
            "PROP_EXECUTE never delivered any impulse"
        # NOTE: the burn does not run to completion in this model — the
        # thrust-misalignment torque (prop.yaml thrust_offset_mm 5.0 ->
        # 1.65 mN.m, against a 2 mN.m wheel) drives aocs.att_error past 1 deg
        # within ~4 s and S19 rule 8001 aborts autonomously, at ~0.08 of the
        # commanded 0.85 m/s. That is spacecraft behaviour, not a refused
        # telecommand, and is reported to the owner separately; what the plan
        # owns is that every command was accepted and the interlocks were
        # satisfied.
        assert int(flight.p("prop.burn_status")) in (3, 4), \
            "the burn neither completed nor aborted — it never started"

        # Restoration: safed, valves shut, catbed heater off.
        assert int(flight.p("prop.state")) == 0, "left ARMED or FIRING"
        assert int(flight.p("prop.latch_a_state")) == 0
        assert int(flight.p("prop.latch_b_state")) == 0
