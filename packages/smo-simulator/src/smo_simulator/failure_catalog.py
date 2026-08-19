"""SMO Simulator — the failure catalogue.

Aggregates every subsystem model's ``FAILURE_SPECS`` table (declared next to
its ``inject_failure()`` body) into one catalogue, keyed by the subsystem name
the engine uses in ``engine.subsystems``.

Consumers:

* ``FailureManager.inject`` — validates magnitude/parameters before a failure
  reaches a model, and rejects with a reason instead of clamping;
* ``GET /api/failure-catalog`` — the instructor UI *generates* its injection
  form from this, so a new failure needs no UI change;
* the scenario content gate — every shipped scenario file is checked against
  it, so a scenario that names a failure or a parameter that no longer exists
  fails the build instead of silently doing nothing at runtime.
"""
from __future__ import annotations

from typing import Any

from smo_simulator.failure_spec import FailureSpec, FailureParamError

# Subsystem name (as used by engine.subsystems) -> model class.
_MODEL_IMPORTS = {
    "eps": ("smo_simulator.models.eps_basic", "EPSBasicModel"),
    "aocs": ("smo_simulator.models.aocs_basic", "AOCSBasicModel"),
    "tcs": ("smo_simulator.models.tcs_basic", "TCSBasicModel"),
    "obdh": ("smo_simulator.models.obdh_basic", "OBDHBasicModel"),
    "ttc": ("smo_simulator.models.ttc_basic", "TTCBasicModel"),
    "payload": ("smo_simulator.models.payload_basic", "PayloadBasicModel"),
    "prop": ("smo_simulator.models.prop_basic", "PropBasicModel"),
}

# UI/scenario aliases -> engine subsystem name. The instructor console and the
# older scenario files call the AOCS "adcs"; the engine keys it "aocs".
SUBSYSTEM_ALIASES = {"adcs": "aocs", "acs": "aocs"}

# Human labels for the subsystem picker.
SUBSYSTEM_LABELS = {
    "eps": "EPS — Electrical Power",
    "aocs": "ADCS — Attitude Determination & Control",
    "tcs": "TCS — Thermal Control",
    "obdh": "OBDH — On-Board Data Handling",
    "ttc": "TT&C — Telemetry, Tracking & Command",
    "payload": "Payload — Multispectral Imager",
    "prop": "PROP — Propulsion",
}

_CATALOG: dict[str, dict[str, FailureSpec]] | None = None


def canonical_subsystem(name: str) -> str:
    """Map a UI/scenario subsystem alias onto the engine's own name."""
    key = (name or "").strip().lower()
    return SUBSYSTEM_ALIASES.get(key, key)


def _build() -> dict[str, dict[str, FailureSpec]]:
    from importlib import import_module

    catalog: dict[str, dict[str, FailureSpec]] = {}
    for sub, (module_name, class_name) in _MODEL_IMPORTS.items():
        model_cls = getattr(import_module(module_name), class_name)
        specs = getattr(model_cls, "FAILURE_SPECS", {})
        catalog[sub] = {
            name: FailureSpec(
                name=spec.name, label=spec.label, effect=spec.effect,
                subsystem=sub, magnitude=spec.magnitude, params=spec.params,
                reversible=spec.reversible, notes=spec.notes,
            )
            for name, spec in specs.items()
        }
    return catalog


def catalog() -> dict[str, dict[str, FailureSpec]]:
    """The whole catalogue: ``{subsystem: {failure_name: FailureSpec}}``."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build()
    return _CATALOG


def get_spec(subsystem: str, failure: str) -> FailureSpec | None:
    """Spec for one injection, or None if the pair is unknown."""
    return catalog().get(canonical_subsystem(subsystem), {}).get(failure)


def require_spec(subsystem: str, failure: str) -> FailureSpec:
    sub = canonical_subsystem(subsystem)
    subs = catalog()
    if sub not in subs:
        raise FailureParamError(
            f"unknown subsystem {subsystem!r} — known: {sorted(subs)}")
    spec = subs[sub].get(failure)
    if spec is None:
        raise FailureParamError(
            f"{sub}: unknown failure {failure!r} — known: "
            f"{sorted(subs[sub])}")
    return spec


def validate(subsystem: str, failure: str, magnitude: Any = None,
             params: dict[str, Any] | None = None
             ) -> tuple[str, float, dict[str, Any]]:
    """Validate an injection request.

    Returns ``(canonical_subsystem, magnitude, params)``.
    Raises :class:`FailureParamError` with an operator-readable reason.
    """
    spec = require_spec(subsystem, failure)
    mag, out = spec.validate(magnitude, params)
    return canonical_subsystem(subsystem), mag, out


def to_dict() -> dict[str, Any]:
    """JSON-serialisable catalogue for the instructor API."""
    subs = catalog()
    return {
        "aliases": dict(SUBSYSTEM_ALIASES),
        "subsystems": [
            {
                "name": sub,
                "label": SUBSYSTEM_LABELS.get(sub, sub.upper()),
                "failures": [subs[sub][f].to_dict() for f in sorted(subs[sub])],
            }
            for sub in sorted(subs) if subs[sub]
        ],
    }


def all_failure_names() -> set[tuple[str, str]]:
    """Every ``(subsystem, failure)`` pair in the catalogue."""
    return {(sub, name) for sub, specs in catalog().items() for name in specs}
