"""The FPA cooler efficiency knobs are real configuration (§8.2, 2026-08b).

`fpa_cooler_power_w`, `fpa_cooler_carnot_fraction` and `fpa_imaging_load_w`
were documented in `subsystems/tcs.yaml` as config-read and consumed by
`tcs_basic._fpa_achievable_lift_k` with `config.get(...)` — but `TCSConfig` is
a `StrictModel` (`extra="forbid"`), so putting any of them in the YAML made the
loader REJECT the file and the model's own literals were the only live values.
Three knobs that looked configurable and were not.

Each test here changes ONE key through the real loader path and asserts the
model's behaviour moves, which is the only claim that matters.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_common.config.schemas import TCSConfig
from smo_simulator.models.tcs_basic import TCSBasicModel

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"

SINK_C = 12.0          # the nominal internal sink
KNOBS = ("fpa_cooler_power_w", "fpa_cooler_carnot_fraction",
         "fpa_imaging_load_w")


def _yaml_cfg() -> dict:
    return yaml.safe_load(
        (CONFIG_DIR / "subsystems" / "tcs.yaml").read_text())


def _model(**overrides) -> TCSBasicModel:
    """A TCS model configured from the shipped YAML, validated by TCSConfig.

    Going through `TCSConfig` is the point: it is the gate that used to reject
    these keys.
    """
    raw = _yaml_cfg()
    raw.update(overrides)
    validated = TCSConfig(**raw).model_dump()
    m = TCSBasicModel()
    m.configure(validated)
    return m


# ── the schema accepts them at all ──────────────────────────────────────

@pytest.mark.parametrize("key", KNOBS)
def test_the_strict_schema_accepts_the_key(key):
    raw = _yaml_cfg()
    raw[key] = 1.0
    TCSConfig(**raw)   # would raise ValidationError before 2026-08b


def test_the_shipped_yaml_still_loads_and_declares_all_three():
    raw = _yaml_cfg()
    for key in KNOBS:
        assert key in raw, f"{key} is no longer in subsystems/tcs.yaml"
    cfg = TCSConfig(**raw)
    assert cfg.fpa_cooler_power_w == 15.0
    assert cfg.fpa_cooler_carnot_fraction == 0.23
    assert cfg.fpa_imaging_load_w == 2.0


def test_the_model_has_no_private_copy_of_the_numbers():
    """The live values must come from the config, not from model literals."""
    m = _model()
    assert m._fpa_cooler_power_w == 15.0
    assert m._fpa_cooler_carnot_fraction == 0.23
    assert m._fpa_imaging_load_w == 2.0


# ── each knob changes behaviour ─────────────────────────────────────────

def test_cooler_power_changes_the_achievable_lift():
    weak = _model(fpa_cooler_power_w=5.0)
    strong = _model(fpa_cooler_power_w=25.0)
    assert (strong._fpa_achievable_lift_k(SINK_C, 0.0)
            > weak._fpa_achievable_lift_k(SINK_C, 0.0) + 1.0)


def test_carnot_fraction_changes_the_achievable_lift():
    """The instructor's 'the cooler is degrading' knob."""
    nominal = _model()
    degraded = _model(fpa_cooler_carnot_fraction=0.10)
    lift_nom = nominal._fpa_achievable_lift_k(SINK_C, 0.0)
    lift_deg = degraded._fpa_achievable_lift_k(SINK_C, 0.0)
    assert lift_deg < lift_nom - 1.0
    # And it is spendable: at 0.10 of Carnot the shipped -15 C setpoint is out
    # of reach, which is the whole teaching point of a finite margin.
    assert SINK_C - lift_deg > -15.0
    assert SINK_C - lift_nom < -15.0


def test_imaging_load_changes_the_achievable_lift():
    light = _model(fpa_imaging_load_w=0.5)
    heavy = _model(fpa_imaging_load_w=8.0)
    assert (heavy._fpa_achievable_lift_k(SINK_C, heavy._fpa_imaging_load_w)
            < light._fpa_achievable_lift_k(SINK_C, light._fpa_imaging_load_w)
            - 1.0)


def test_a_yaml_override_reaches_the_published_floor_and_margin():
    """End to end: config -> 0x0424 / 0x0425, the parameters the ground sees."""
    def floor_and_margin(model):
        model.rng.seed(7)
        shared: dict[int, float] = {0x0114: 1}   # EPS cooler line powered
        model._state.cooler_fpa = True
        orbit = _Orbit()
        for _ in range(120):
            model.tick(10.0, orbit, shared)
        return shared[0x0424], shared[0x0425]

    nom_floor, nom_margin = floor_and_margin(_model())
    deg_floor, deg_margin = floor_and_margin(
        _model(fpa_cooler_carnot_fraction=0.10))
    assert deg_floor > nom_floor + 1.0, \
        "a degraded cooler must publish a WARMER achievable floor"
    assert nom_margin > 0.0 > deg_margin, \
        "and a margin that goes negative — the setpoint is out of reach"


class _Orbit:
    in_eclipse = False
    solar_beta_deg = 30.0
