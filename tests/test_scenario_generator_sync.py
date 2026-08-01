"""Guard against drift between tools/scenario_generator.py and the simulator.

The scenario generator ships a hard-coded ``SUBSYSTEMS`` catalog that mirrors the
``inject_failure()`` handlers in each subsystem model. Because it is a manual copy
(no runtime import of the models), it silently drifts: at one point it was missing
the entire ``payload`` subsystem plus several TTC/OBDH failure modes, so those
failures could never be selected from the generator UI and broken scenarios looked
fine. This test fails if the two ever diverge again.
"""
import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "tools" / "scenario_generator.py"
MODEL_DIR = REPO_ROOT / "packages" / "smo-simulator" / "src" / "smo_simulator" / "models"

# Subsystems the generator is expected to cover, mapped to their model file stem.
SUBSYSTEMS = {
    "eps": "eps_basic.py",
    "aocs": "aocs_basic.py",
    "tcs": "tcs_basic.py",
    "ttc": "ttc_basic.py",
    "obdh": "obdh_basic.py",
    "payload": "payload_basic.py",
}


def _generator_catalog() -> dict[str, set[str]]:
    """Extract {subsystem: {failure names}} from the generator's SUBSYSTEMS dict.

    Parsed with ast.literal_eval so the test has no dependency on aiohttp (which
    importing the module would pull in).
    """
    tree = ast.parse(GENERATOR.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SUBSYSTEMS" for t in node.targets
        ):
            data = ast.literal_eval(node.value)
            return {sub: set(spec["failures"].keys()) for sub, spec in data.items()}
    raise AssertionError("SUBSYSTEMS dict not found in scenario_generator.py")


def _model_failures(model_file: str) -> set[str]:
    """Extract the failure names handled by a model's inject_failure() method."""
    src = (MODEL_DIR / model_file).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "inject_failure":
            body_src = ast.get_source_segment(src, node)
            return set(re.findall(r'failure == "([a-z_]+)"', body_src))
    raise AssertionError(f"inject_failure() not found in {model_file}")


def test_generator_covers_every_subsystem():
    """Every subsystem model must be represented in the generator catalog."""
    catalog = _generator_catalog()
    missing = set(SUBSYSTEMS) - set(catalog)
    assert not missing, f"Generator SUBSYSTEMS missing subsystem(s): {sorted(missing)}"


@pytest.mark.parametrize("subsystem,model_file", sorted(SUBSYSTEMS.items()))
def test_generator_matches_model_failures(subsystem, model_file):
    """The generator's failure list for each subsystem must equal the model's."""
    catalog = _generator_catalog()
    gen = catalog.get(subsystem, set())
    model = _model_failures(model_file)
    missing_from_gen = model - gen
    extra_in_gen = gen - model
    assert not missing_from_gen and not extra_in_gen, (
        f"{subsystem}: generator/model failure lists differ.\n"
        f"  in model but not generator: {sorted(missing_from_gen)}\n"
        f"  in generator but not model: {sorted(extra_in_gen)}"
    )
