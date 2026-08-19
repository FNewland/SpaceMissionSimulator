"""Guard against drift between tools/scenario_generator.py and the simulator.

The generator used to ship a hard-coded ``SUBSYSTEMS`` catalog that mirrored the
``inject_failure()`` handlers in each subsystem model. Because it was a manual
copy it silently drifted: at one point it was missing the entire ``payload``
subsystem plus several TTC/OBDH failure modes, so those failures could never be
selected from the generator UI and broken scenarios looked fine.

It now builds ``SUBSYSTEMS`` from ``smo_simulator.failure_catalog`` — the same
declarative FAILURE_SPECS the instructor console and the FailureManager use — so
the drift is structurally impossible. These tests hold that property: the
generator must expose exactly the catalogue's failures, per subsystem, with the
typed parameter specs attached.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "tools" / "scenario_generator.py"

# Subsystems the generator must cover. `prop` is here because propulsion
# (S8 funcs 108-118, HK SID 9) landed after the generator's hand-copied table
# was written and never made it in.
SUBSYSTEMS = ("eps", "aocs", "tcs", "ttc", "obdh", "payload", "prop")


def _generator_catalog() -> dict[str, dict]:
    """The generator's live SUBSYSTEMS catalogue.

    Imported (not ast-parsed) now that it is derived rather than literal.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import scenario_generator  # noqa: PLC0415

    return scenario_generator.SUBSYSTEMS


def _catalog_failures() -> dict[str, set[str]]:
    from smo_simulator.failure_catalog import catalog

    return {sub: set(specs) for sub, specs in catalog().items()}


def test_generator_covers_every_subsystem():
    """Every subsystem model must be represented in the generator catalog."""
    catalog = _generator_catalog()
    missing = set(SUBSYSTEMS) - set(catalog)
    assert not missing, f"Generator SUBSYSTEMS missing subsystem(s): {sorted(missing)}"


@pytest.mark.parametrize("subsystem", sorted(SUBSYSTEMS))
def test_generator_matches_model_failures(subsystem):
    """The generator's failure list for each subsystem equals the catalogue's."""
    gen = set(_generator_catalog().get(subsystem, {}).get("failures", {}))
    model = _catalog_failures().get(subsystem, set())
    assert gen == model, (
        f"{subsystem}: generator/catalogue failure lists differ.\n"
        f"  in catalogue but not generator: {sorted(model - gen)}\n"
        f"  in generator but not catalogue: {sorted(gen - model)}"
    )


def test_generator_covers_the_propulsion_subsystem():
    """Propulsion landed after the generator's hand-copied table was written."""
    assert "prop" in _generator_catalog(), (
        "the scenario generator cannot author a propulsion failure")


@pytest.mark.parametrize("subsystem", sorted(SUBSYSTEMS))
def test_generator_carries_the_typed_parameter_specs(subsystem):
    """Each generator failure entry carries its ParamSpec list.

    The generator's form is rendered from these, so an entry without them
    would fall back to the model defaults — which is exactly the "no way to
    choose which wheel" complaint this work fixes.
    """
    from smo_simulator.failure_catalog import get_spec

    for name, entry in _generator_catalog()[subsystem]["failures"].items():
        assert "params" in entry and "magnitude" in entry, (
            f"{subsystem}.{name}: generator entry has no parameter spec")
        spec = get_spec(subsystem, name)
        assert [p["name"] for p in entry["params"]] == [p.name for p in spec.params], (
            f"{subsystem}.{name}: generator parameter list differs from the spec")
