"""FailureManager onset models: step, gradual ramp, intermittent flicker, expiry.

``failure_manager.py`` is the layer between "the instructor pressed inject" and
the subsystem models. Its three onset models and its timed expiry decide *when*
a fault reaches the spacecraft, which is the whole point of a training scenario
— a bearing that degrades over 90 s trains a different response than one that
fails instantly.

Existing tests covered the clear paths (test_failure_clear_each) and exercised
gradual onset indirectly through scenarios. Nothing pinned the ramp rate, the
flicker timing, or the ``duration_s`` auto-clear. These tests drive
``FailureManager`` directly with recorded inject/clear callbacks and a
deterministic RNG so the timing is asserted rather than assumed.
"""
import random

import pytest

from smo_simulator.failure_manager import (
    DEFAULT_FIRST_FLICKER_S,
    DEFAULT_FLICKER_OFF_S,
    DEFAULT_FLICKER_ON_S,
    DEFAULT_ONSET_DURATION_S,
    ONSET_GRADUAL,
    ONSET_INTERMITTENT,
    ONSET_STEP,
    FailureManager,
)


class Recorder:
    """Captures the inject/clear commands the manager emits."""

    def __init__(self):
        self.injects: list[dict] = []
        self.clears: list[dict] = []

    def inject(self, cmd):
        self.injects.append(dict(cmd))

    def clear(self, cmd):
        self.clears.append(dict(cmd))

    @property
    def magnitudes(self) -> list[float]:
        return [c["magnitude"] for c in self.injects]


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def fm(rec):
    # These tests are about ONSET TIMING and drive the manager with synthetic
    # failure names and a recording stub instead of real models, so the
    # parameter-spec validation (which needs a real subsystem/failure pair)
    # is off here. Validation itself is covered by
    # tests/test_simulator/test_failure_param_specs.py.
    return FailureManager(inject_fn=rec.inject, clear_fn=rec.clear,
                          validate_params=False)


# ── Step onset ──────────────────────────────────────────────────────

class TestStepOnset:

    def test_step_applies_full_magnitude_immediately(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=0.7, onset=ONSET_STEP)
        assert rec.magnitudes == [0.7], (
            "a step failure must reach the model on inject, not on the "
            "next tick")

    def test_step_ignores_onset_duration(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=1.0, onset=ONSET_STEP,
                  onset_duration_s=120.0)
        fid = fm.active_failures()[0]["id"]
        fm.tick(60.0)
        assert rec.magnitudes == [1.0], "step onset must not ramp"
        assert fm.active_failures()[0]["id"] == fid

    def test_step_is_the_default_onset(self, fm, rec):
        fm.inject("tcs", "heater_stuck_off", magnitude=1.0)
        assert rec.magnitudes == [1.0]


# ── Gradual onset ───────────────────────────────────────────────────

class TestGradualOnset:

    def test_gradual_does_not_apply_at_inject(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL,
                  onset_duration_s=100.0)
        assert rec.injects == [], (
            "gradual onset must start at zero magnitude, not full")

    def test_gradual_ramps_linearly_to_target(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL,
                  onset_duration_s=100.0)
        for _ in range(10):
            fm.tick(10.0)
        assert rec.magnitudes == pytest.approx(
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])

    def test_gradual_ramp_rate_scales_with_target_magnitude(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=0.5, onset=ONSET_GRADUAL,
                  onset_duration_s=50.0)
        fm.tick(10.0)
        assert rec.magnitudes[-1] == pytest.approx(0.1), (
            "ramp rate must be target/onset_duration, not a fixed rate")

    def test_gradual_clamps_at_target_and_stops_reapplying(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL,
                  onset_duration_s=10.0)
        fm.tick(10.0)
        assert rec.magnitudes[-1] == pytest.approx(1.0)
        applied = len(rec.injects)
        fm.tick(10.0)
        fm.tick(10.0)
        assert len(rec.injects) == applied, (
            "a saturated gradual failure must stop re-applying every tick")

    def test_zero_onset_duration_jumps_straight_to_target(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=0.8, onset=ONSET_GRADUAL,
                  onset_duration_s=0.0)
        fm.tick(1.0)
        assert rec.magnitudes == pytest.approx([0.8])

    def test_default_onset_duration_is_used_when_unspecified(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL)
        fm.tick(DEFAULT_ONSET_DURATION_S / 2.0)
        assert rec.magnitudes[-1] == pytest.approx(0.5), (
            f"expected the {DEFAULT_ONSET_DURATION_S}s default onset ramp")

    def test_reported_magnitude_tracks_the_ramp(self, fm):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL,
                  onset_duration_s=100.0)
        fm.tick(25.0)
        assert fm.active_failures()[0]["magnitude"] == pytest.approx(0.25)
        assert fm.active_failures()[0]["elapsed_s"] == pytest.approx(25.0)


# ── Intermittent onset ──────────────────────────────────────────────

class TestIntermittentOnset:

    @staticmethod
    def _fixed_uniform(monkeypatch, fm, value: float):
        """Make every rng.uniform() inside this manager return `value`.

        Since plan A-2 the manager draws from its own ``rng`` instance (an
        engine-seeded ``random.Random``), not the module-global ``random``.
        """
        monkeypatch.setattr(fm.rng, "uniform", lambda a, b: value)

    def test_intermittent_does_not_apply_at_inject(self, fm, rec, monkeypatch):
        self._fixed_uniform(monkeypatch, fm, 5.0)
        fm.inject("ttc", "tx_intermittent", magnitude=1.0,
                  onset=ONSET_INTERMITTENT)
        assert rec.injects == []
        assert rec.clears == []

    def test_first_transition_is_a_clear_then_it_flickers_on(
            self, fm, rec, monkeypatch):
        """Pins the current flicker phasing.

        ``_flicker_on`` starts True and the first transition inverts it, so the
        first thing an intermittent failure does is emit a *clear* (a no-op on a
        model that was never faulted); the fault itself lands one interval
        later. Flagged rather than changed — flipping the initial phase would
        alter every intermittent scenario's timeline.
        """
        self._fixed_uniform(monkeypatch, fm, 5.0)
        fm.inject("ttc", "tx_intermittent", magnitude=1.0,
                  onset=ONSET_INTERMITTENT)

        fm.tick(4.0)  # before the first flicker boundary
        assert rec.clears == [] and rec.injects == []

        fm.tick(2.0)  # crosses t=5s
        assert len(rec.clears) == 1
        assert rec.injects == []

        fm.tick(5.0)  # crosses the off-interval boundary at t=10s
        assert len(rec.injects) == 1
        assert rec.injects[0]["magnitude"] == 1.0

    def test_flicker_alternates_apply_and_clear(self, fm, rec, monkeypatch):
        self._fixed_uniform(monkeypatch, fm, 3.0)
        fm.inject("ttc", "tx_intermittent", magnitude=1.0,
                  onset=ONSET_INTERMITTENT)
        for _ in range(12):
            fm.tick(3.0)
        assert len(rec.injects) >= 2, "flicker never applied the failure"
        assert len(rec.clears) >= 2, "flicker never cleared the failure"
        assert abs(len(rec.injects) - len(rec.clears)) <= 1, (
            f"flicker is unbalanced: {len(rec.injects)} on / "
            f"{len(rec.clears)} off")

    def test_flicker_does_not_deactivate_the_failure(self, fm, rec, monkeypatch):
        """An off-phase must not retire the failure — it comes back."""
        self._fixed_uniform(monkeypatch, fm, 2.0)
        fm.inject("ttc", "tx_intermittent", magnitude=1.0,
                  onset=ONSET_INTERMITTENT)
        for _ in range(6):
            fm.tick(2.0)
        assert fm.active_failures(), (
            "an intermittent failure disappeared from the active list during "
            "an off phase")

    def test_seeded_flicker_is_reproducible(self, rec):
        """Same seed, same flicker timeline — scenarios stay replayable."""
        def run() -> list[tuple[str, float]]:
            recorder = Recorder()
            # Plan A-2: reproducibility comes from the manager's own seeded
            # rng (the engine passes one derived from simulation.yaml `seed`),
            # not from the module-global random.seed().
            manager = FailureManager(recorder.inject, recorder.clear, validate_params=False,
                                     rng=random.Random(1234))
            manager.inject("ttc", "tx_intermittent", magnitude=1.0,
                           onset=ONSET_INTERMITTENT)
            events: list[tuple[str, float]] = []
            t = 0.0
            for _ in range(60):
                before = (len(recorder.injects), len(recorder.clears))
                manager.tick(1.0)
                t += 1.0
                if len(recorder.injects) > before[0]:
                    events.append(("on", t))
                if len(recorder.clears) > before[1]:
                    events.append(("off", t))
            return events

        first, second = run(), run()
        assert first == second, "seeded flicker timeline is not reproducible"
        assert first, "seeded run produced no flicker transitions at all"

    def test_flicker_intervals_come_from_configuration(self, rec, monkeypatch):
        """The configured on/off windows are the ones sampled."""
        sampled: list[tuple[float, float]] = []

        manager = FailureManager(rec.inject, rec.clear, validate_params=False, config={
            "default_onset_duration_s": 30.0,
            "intermittent": {"first_flicker_s": [1.0, 1.0],
                             "on_s": [2.0, 2.0],
                             "off_s": [3.0, 3.0]},
        })
        monkeypatch.setattr(manager.rng, "uniform",
                            lambda a, b: sampled.append((a, b)) or 1.0)
        manager.inject("ttc", "tx_intermittent", magnitude=1.0,
                       onset=ONSET_INTERMITTENT)
        for _ in range(6):
            manager.tick(1.0)

        assert sampled[0] == (1.0, 1.0), "first flicker window not applied"
        assert (3.0, 3.0) in sampled, "configured off-window never sampled"
        assert (2.0, 2.0) in sampled, "configured on-window never sampled"


# ── Timed expiry ────────────────────────────────────────────────────

class TestTimedExpiry:

    def test_duration_expiry_clears_and_removes_the_failure(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=1.0, onset=ONSET_STEP,
                  duration_s=30.0)
        assert len(fm.active_failures()) == 1

        fm.tick(29.0)
        assert fm.active_failures(), "failure expired early"
        assert rec.clears == []

        fm.tick(2.0)
        assert fm.active_failures() == [], "failure did not expire"
        assert len(rec.clears) == 1
        assert rec.clears[0]["subsystem"] == "eps"
        assert rec.clears[0]["failure"] == "sa_degraded"

    def test_expiry_is_exact_at_the_boundary(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=1.0, duration_s=10.0)
        fm.tick(10.0)
        assert fm.active_failures() == [], (
            "expiry must fire when elapsed reaches duration_s, not after")

    def test_zero_duration_never_expires(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=1.0, duration_s=0.0)
        for _ in range(100):
            fm.tick(60.0)
        assert len(fm.active_failures()) == 1, (
            "duration_s=0 means indefinite; the failure must persist")
        assert rec.clears == []

    def test_expiry_of_a_gradual_failure_stops_the_ramp(self, fm, rec):
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL,
                  onset_duration_s=100.0, duration_s=30.0)
        for _ in range(6):
            fm.tick(10.0)
        assert fm.active_failures() == []
        applied = len(rec.injects)
        fm.tick(10.0)
        assert len(rec.injects) == applied, "ramp continued after expiry"

    def test_expired_failure_does_not_block_a_reinject(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=1.0, duration_s=5.0)
        fm.tick(5.0)
        assert fm.active_failures() == []
        fm.inject("eps", "sa_degraded", magnitude=1.0)
        assert len(fm.active_failures()) == 1


# ── Identity, addressing and bulk clear ─────────────────────────────

class TestFailureIdentity:

    def test_per_wheel_failures_are_addressed_independently(self, fm, rec):
        a = fm.inject("aocs", "rw_bearing", magnitude=1.0, wheel=0)
        b = fm.inject("aocs", "rw_bearing", magnitude=1.0, wheel=2)
        assert a != b, "per-wheel failures collapsed onto one id"
        assert len(fm.active_failures()) == 2

        assert fm.clear(a) is True
        remaining = fm.active_failures()
        assert len(remaining) == 1 and remaining[0]["id"] == b

    def test_extra_params_reach_the_model(self, fm, rec):
        fm.inject("eps", "sa_degraded", magnitude=0.5, array="A")
        assert rec.injects[-1]["array"] == "A"

    def test_reinjecting_the_same_target_replaces_the_failure(self, fm):
        first = fm.inject("aocs", "rw_bearing", magnitude=0.2, wheel=1)
        second = fm.inject("aocs", "rw_bearing", magnitude=0.9, wheel=1)
        assert first == second
        assert len(fm.active_failures()) == 1
        assert fm.active_failures()[0]["magnitude"] == pytest.approx(0.9)

    def test_clear_of_unknown_id_is_false(self, fm):
        assert fm.clear("nope.nothing.0") is False

    def test_clear_all_clears_every_onset_type(self, fm, rec, monkeypatch):
        import smo_simulator.failure_manager as mod
        monkeypatch.setattr(mod.random, "uniform", lambda a, b: 5.0)
        fm.inject("eps", "sa_degraded", magnitude=1.0, onset=ONSET_STEP)
        fm.inject("aocs", "rw_bearing", magnitude=1.0, onset=ONSET_GRADUAL)
        fm.inject("ttc", "tx_intermittent", magnitude=1.0,
                  onset=ONSET_INTERMITTENT)
        assert len(fm.active_failures()) == 3

        fm.clear_all()
        assert fm.active_failures() == []
        cleared = {(c["subsystem"], c["failure"]) for c in rec.clears}
        assert cleared == {("eps", "sa_degraded"), ("aocs", "rw_bearing"),
                           ("ttc", "tx_intermittent")}

    def test_model_exceptions_do_not_stop_the_manager(self, rec):
        """A model that raises on inject must not break the tick loop."""
        def boom(cmd):
            raise RuntimeError("model exploded")

        manager = FailureManager(inject_fn=boom, clear_fn=rec.clear,
                                 validate_params=False)
        manager.inject("aocs", "rw_bearing", magnitude=1.0,
                       onset=ONSET_GRADUAL, onset_duration_s=10.0)
        manager.tick(5.0)  # must not raise
        assert len(manager.active_failures()) == 1


# ── Configuration ───────────────────────────────────────────────────

class TestConfiguration:

    def test_defaults_match_the_module_constants(self, fm):
        assert fm._default_onset_s == DEFAULT_ONSET_DURATION_S
        assert fm._first_flicker_s == tuple(DEFAULT_FIRST_FLICKER_S)
        assert fm._flicker_on_s == tuple(DEFAULT_FLICKER_ON_S)
        assert fm._flicker_off_s == tuple(DEFAULT_FLICKER_OFF_S)

    def test_config_dict_overrides_onset_duration(self, rec):
        manager = FailureManager(rec.inject, rec.clear, validate_params=False, config={
            "default_onset_duration_s": 20.0,
        })
        manager.inject("aocs", "rw_bearing", magnitude=1.0,
                       onset=ONSET_GRADUAL)
        manager.tick(10.0)
        assert rec.magnitudes[-1] == pytest.approx(0.5), (
            "simulation.yaml default_onset_duration_s was not applied")

    def test_config_object_is_accepted_as_well_as_a_dict(self, rec):
        class Cfg:
            default_onset_duration_s = 40.0
            intermittent = None

        manager = FailureManager(rec.inject, rec.clear, validate_params=False, config=Cfg())
        assert manager._default_onset_s == 40.0

    def test_partial_config_keeps_the_remaining_defaults(self, rec):
        manager = FailureManager(rec.inject, rec.clear, validate_params=False, config={
            "intermittent": {"on_s": [7.0, 7.0]},
        })
        assert manager._flicker_on_s == (7.0, 7.0)
        assert manager._flicker_off_s == tuple(DEFAULT_FLICKER_OFF_S)
        assert manager._default_onset_s == DEFAULT_ONSET_DURATION_S
