"""Gate tests for the declarative failure-injection parameter specs.

Every failure a subsystem model implements must declare a ``FailureSpec`` next
to its ``inject_failure()`` body (see ``smo_simulator/failure_spec.py``). That
spec is the single source of truth for:

* the instructor UI's generated injection form (``GET /api/failure-catalog``);
* ``FailureManager.inject`` validation — reject with a reason, never clamp;
* the scenario content gate.

These tests keep the spec table and the implementation in bijection, so a
failure can never ship with a parameter the UI cannot reach (which is how
"which reaction wheel?" ended up hard-coded to wheel 0), nor a spec for a
failure the model no longer implements.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from smo_simulator import failure_catalog as fc
from smo_simulator.failure_manager import FailureManager
from smo_simulator.failure_spec import FailureParamError, PARAM_TYPES

MODEL_DIR = (Path(__file__).resolve().parents[2] / "packages" / "smo-simulator"
             / "src" / "smo_simulator" / "models")

MODEL_FILES = {
    "eps": "eps_basic.py",
    "aocs": "aocs_basic.py",
    "tcs": "tcs_basic.py",
    "obdh": "obdh_basic.py",
    "ttc": "ttc_basic.py",
    "payload": "payload_basic.py",
    "prop": "prop_basic.py",
}


def _implemented(subsystem: str) -> set[str]:
    """Failure names a model's inject_failure() branches on, from its source."""
    src = (MODEL_DIR / MODEL_FILES[subsystem]).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "inject_failure":
            body = ast.get_source_segment(src, node)
            names = set(re.findall(r'failure\s*==\s*["\'](\w+)["\']', body))
            # `elif failure in _GS_FAILURE_FIELDS:` — a table-driven branch.
            for table in re.findall(r'failure in (\w+)', body):
                names |= set(_module_table(src, table))
            return names
    raise AssertionError(f"inject_failure() not found in {subsystem}")


def _module_table(src: str, name: str) -> list[str]:
    """Keys of a module-level dict literal, by name."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name
                and isinstance(node.value, ast.Dict)):
            return [k.value for k in node.value.keys
                    if isinstance(k, ast.Constant)]
    return []


ALL_PAIRS = sorted(fc.all_failure_names())
PAIR_IDS = [f"{s}.{f}" for s, f in ALL_PAIRS]


# ── spec table <-> implementation bijection ─────────────────────────

@pytest.mark.parametrize("subsystem", sorted(MODEL_FILES))
def test_every_implemented_failure_has_a_spec(subsystem):
    implemented = _implemented(subsystem)
    declared = set(fc.catalog()[subsystem])
    missing = implemented - declared
    assert not missing, (
        f"{subsystem}: inject_failure() handles {sorted(missing)} but "
        f"FAILURE_SPECS does not declare them — they are unreachable from the "
        f"instructor UI and unvalidated on the API")


@pytest.mark.parametrize("subsystem", sorted(MODEL_FILES))
def test_every_spec_has_an_implementation(subsystem):
    implemented = _implemented(subsystem)
    declared = set(fc.catalog()[subsystem])
    extra = declared - implemented
    assert not extra, (
        f"{subsystem}: FAILURE_SPECS declares {sorted(extra)} but "
        f"inject_failure() does not implement them — injecting one would be "
        f"a silent no-op")


# ── spec well-formedness ────────────────────────────────────────────

@pytest.mark.parametrize("subsystem,failure", ALL_PAIRS, ids=PAIR_IDS)
def test_spec_is_well_formed(subsystem, failure):
    spec = fc.get_spec(subsystem, failure)
    assert spec.label and spec.label != failure, (
        f"{subsystem}.{failure}: needs a human label, not the raw name")
    assert spec.effect, (
        f"{subsystem}.{failure}: needs an `effect` — what it should do, so the "
        f"instructor console can say so and an audit can check it")
    for p in (spec.params or ()):
        assert p.type in PARAM_TYPES, f"{subsystem}.{failure}.{p.name}: bad type"
        assert p.label, f"{subsystem}.{failure}.{p.name}: needs a label"
        if p.type in ("int", "float") and p.name not in ("magnitude",):
            assert p.unit, (
                f"{subsystem}.{failure}.{p.name}: a numeric parameter must "
                f"declare its engineering unit")
        if p.type == "enum":
            assert p.values, f"{subsystem}.{failure}.{p.name}: enum with no values"
    # A boolean magnitude ("failed / not failed") has no engineering unit;
    # anything numeric must declare one.
    if spec.magnitude is not None and spec.magnitude.type in ("int", "float"):
        assert spec.magnitude.unit, (
            f"{subsystem}.{failure}: magnitude must declare its unit — 'a "
            f"number between 0 and 1' is what this work replaced")


@pytest.mark.parametrize("subsystem,failure", ALL_PAIRS, ids=PAIR_IDS)
def test_spec_defaults_validate_against_the_spec(subsystem, failure):
    """A failure's own defaults must be a legal injection."""
    spec = fc.get_spec(subsystem, failure)
    mag, params = spec.validate(spec.default_magnitude(), spec.defaults())
    assert isinstance(mag, float)
    assert isinstance(params, dict)


# ── the parameters the UI can reach cover the ones the model reads ──

@pytest.mark.parametrize("subsystem", sorted(MODEL_FILES))
def test_specs_cover_every_kwarg_the_model_reads(subsystem):
    """Every ``kw.get("x")`` in inject_failure() has a matching ParamSpec.

    This is the check that would have caught the original complaint: the model
    read `wheel`, `unit`, `line_index`, `axis`, `segment`... but the interface
    exposed only one 0..1 scalar, so none of them could be set.
    """
    src = (MODEL_DIR / MODEL_FILES[subsystem]).read_text()
    tree = ast.parse(src)
    body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "inject_failure":
            body = ast.get_source_segment(src, node)
    assert body is not None
    read = set(re.findall(r'kw(?:args)?\.get\(\s*["\'](\w+)["\']', body))
    read -= {"magnitude"}
    declared = {p.name for spec in fc.catalog()[subsystem].values()
                for p in (spec.params or ())}
    missing = read - declared
    assert not missing, (
        f"{subsystem}: inject_failure() reads kwargs {sorted(missing)} that no "
        f"FailureSpec declares — no caller (UI, API or scenario) can set them")


# ── validation: reject with a reason, never clamp ────────────────────

class _Recorder:
    def __init__(self):
        self.injects, self.clears = [], []

    def inject(self, cmd):
        self.injects.append(cmd)

    def clear(self, cmd):
        self.clears.append(cmd)


@pytest.fixture
def fm():
    rec = _Recorder()
    manager = FailureManager(rec.inject, rec.clear)
    manager.recorder = rec
    return manager


def test_unknown_failure_is_rejected(fm):
    with pytest.raises(FailureParamError, match="unknown failure"):
        fm.inject("eps", "not_a_real_failure")


def test_unknown_subsystem_is_rejected(fm):
    with pytest.raises(FailureParamError, match="unknown subsystem"):
        fm.inject("warp_core", "breach")


def test_unknown_parameter_is_rejected(fm):
    with pytest.raises(FailureParamError, match="unknown parameter"):
        fm.inject("aocs", "rw_bearing", magnitude=0.5, whele=1)


@pytest.mark.parametrize("kwargs,pattern", [
    ({"wheel": 9}, "must be one of"),
    ({"wheel": "left"}, "must be one of"),
])
def test_out_of_range_enum_is_rejected_not_clamped(fm, kwargs, pattern):
    with pytest.raises(FailureParamError, match=pattern):
        fm.inject("aocs", "rw_bearing", magnitude=0.5, **kwargs)


def test_out_of_range_number_is_rejected_not_clamped(fm):
    with pytest.raises(FailureParamError, match="must be <="):
        fm.inject("aocs", "rw_bearing", magnitude=5.0, wheel=0)
    assert fm.recorder.injects == [], "an invalid injection must not reach a model"


def test_non_finite_magnitude_is_rejected(fm):
    with pytest.raises(FailureParamError, match="finite"):
        fm.inject("tcs", "obc_thermal", heat_w=float("inf"))


def test_int_parameter_rejects_a_fraction(fm):
    with pytest.raises(FailureParamError, match="whole number"):
        fm.inject("payload", "image_corrupt", count=2.5)


def test_string_values_from_a_form_are_coerced(fm):
    """The HTML form sends every field as a string; the spec coerces them."""
    fm.inject("eps", "overcurrent", magnitude="3.0", line_index="8")
    cmd = fm.recorder.injects[-1]
    assert cmd["line_index"] == 8 and cmd["magnitude"] == pytest.approx(3.0)


def test_multi_enum_accepts_a_list_and_a_csv_string(fm):
    fm.inject("aocs", "multi_wheel_failure", wheels=[0, 2])
    assert fm.recorder.injects[-1]["wheels"] == [0, 2]
    fm.inject("aocs", "multi_wheel_failure", wheels="1,3")
    assert fm.recorder.injects[-1]["wheels"] == [1, 3]


def test_unit_selector_is_part_of_the_failure_id(fm):
    """Two units of the same failure must be separately trackable/clearable.

    The id used to be built from `array`/`wheel`/`circuit` only, so injecting
    star tracker 1 and star tracker 2 collided on one id: the second overwrote
    the first, and clearing removed one while the other stayed applied and
    untracked.
    """
    a = fm.inject("aocs", "st_failure", unit=1)
    b = fm.inject("aocs", "st_failure", unit=2)
    assert a != b
    assert len(fm.active_failures()) == 2
    assert fm.clear(a) and len(fm.active_failures()) == 1


def test_unknown_onset_is_rejected(fm):
    with pytest.raises(FailureParamError, match="unknown onset"):
        fm.inject("aocs", "rw_bearing", magnitude=0.5, onset="slowly")


def test_catalog_serialises_for_the_api():
    payload = fc.to_dict()
    assert payload["subsystems"], "catalogue must not be empty"
    import json
    json.loads(json.dumps(payload))  # must be JSON-serialisable
    for sub in payload["subsystems"]:
        assert sub["label"], f"{sub['name']}: needs a display label"
        for f in sub["failures"]:
            assert set(f) >= {"name", "label", "effect", "magnitude", "params"}
