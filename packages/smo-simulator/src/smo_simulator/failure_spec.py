"""SMO Simulator — declarative failure-injection parameter specs.

Every failure a subsystem model implements declares, next to its
``inject_failure()`` body, a :class:`FailureSpec`: what the failure does, what
its magnitude means (in a real engineering unit, not a normalised 0..1
severity), and which *typed* parameters select the affected unit — which
wheel, which panel, which star tracker, which power line, which gyro axis.

The spec is the single source of truth. It is:

* consumed by :meth:`FailureManager.inject` to validate an injection before it
  reaches a model (rejecting with a reason, never silently clamping);
* served to the instructor UI over ``GET /api/failure-catalog`` so the
  injection form is *generated* from the spec rather than hand-written;
* used by the scenario content gate to check every shipped scenario file.

Historically the only knob was a single 0..1 slider, so "which reaction wheel"
and "how many ppm" had nowhere to live and scenario files carried undeclared
kwargs that models silently ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Parameter types the UI knows how to render.
T_ENUM = "enum"
T_INT = "int"
T_FLOAT = "float"
T_BOOL = "bool"
T_STRING = "string"

PARAM_TYPES = frozenset({T_ENUM, T_INT, T_FLOAT, T_BOOL, T_STRING})


class FailureParamError(ValueError):
    """A failure injection was rejected because its parameters are invalid.

    Deliberately a ``ValueError``: the engine's instructor-command drain
    catches it, counts a reject and reports ``str(e)`` back to the caller, so
    the operator sees the reason instead of a silently clamped injection.
    """


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass(frozen=True)
class ParamSpec:
    """One typed parameter of a failure injection."""

    name: str
    type: str
    label: str
    default: Any = None
    unit: str = ""
    min: float | None = None
    max: float | None = None
    values: tuple[Any, ...] | None = None       # enum: allowed values
    value_labels: tuple[str, ...] | None = None  # enum: human label per value
    step: float | None = None
    required: bool = False
    description: str = ""
    multi: bool = False   # enum only: accept a LIST of allowed values

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"ParamSpec {self.name!r}: unknown type {self.type!r}")
        if self.multi and self.type != T_ENUM:
            raise ValueError(f"ParamSpec {self.name!r}: multi requires an enum")
        if self.type == T_ENUM and not self.values:
            raise ValueError(f"ParamSpec {self.name!r}: enum needs `values`")
        if (self.value_labels is not None and self.values is not None
                and len(self.value_labels) != len(self.values)):
            raise ValueError(
                f"ParamSpec {self.name!r}: value_labels/values length mismatch")

    # ── serialisation ────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name, "type": self.type, "label": self.label,
            "default": self.default, "unit": self.unit,
            "required": self.required, "multi": self.multi,
        }
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.step is not None:
            d["step"] = self.step
        if self.values is not None:
            d["values"] = list(self.values)
            d["value_labels"] = list(self.value_labels or
                                     [str(v) for v in self.values])
        if self.description:
            d["description"] = self.description
        return d

    # ── validation ───────────────────────────────────────────────────
    def coerce(self, value: Any, *, where: str = "") -> Any:
        """Coerce+validate `value`. Raises FailureParamError with a reason.

        Never clamps: an out-of-range value is a rejection, because a silently
        clamped injection is indistinguishable from one that worked.
        """
        ctx = f"{where}.{self.name}" if where else self.name

        if self.type == T_BOOL:
            if isinstance(value, bool):
                return value
            if _is_number(value):
                return bool(value)
            if isinstance(value, str) and value.lower() in (
                    "true", "false", "1", "0", "yes", "no", "on", "off"):
                return value.lower() in ("true", "1", "yes", "on")
            raise FailureParamError(f"{ctx} must be a boolean, got {value!r}")

        if self.type == T_ENUM and self.multi:
            if isinstance(value, str):
                items = [v.strip() for v in value.split(",") if v.strip()]
            elif isinstance(value, (list, tuple, set)):
                items = list(value)
            else:
                items = [value]
            if not items:
                raise FailureParamError(f"{ctx} must name at least one unit")
            allowed = list(self.values or ())
            out = []
            for item in items:
                match = next((a for a in allowed if a == item or str(a) == str(item)),
                             None)
                if match is None:
                    raise FailureParamError(
                        f"{ctx} entries must each be one of {allowed}, "
                        f"got {item!r}")
                if match not in out:
                    out.append(match)
            return out

        if self.type == T_ENUM:
            allowed = list(self.values or ())
            if value in allowed:
                return value
            # A form/JSON round trip turns 2 into "2" and vice versa.
            for a in allowed:
                if str(a) == str(value):
                    return a
            raise FailureParamError(
                f"{ctx} must be one of {allowed}, got {value!r}")

        if self.type in (T_INT, T_FLOAT):
            if isinstance(value, bool) or not (
                    _is_number(value) or isinstance(value, str)):
                raise FailureParamError(f"{ctx} must be a number, got {value!r}")
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise FailureParamError(
                    f"{ctx} must be a number, got {value!r}") from None
            if num != num or num in (float("inf"), float("-inf")):
                raise FailureParamError(f"{ctx} must be finite, got {value!r}")
            if self.type == T_INT:
                if abs(num - round(num)) > 1e-9:
                    raise FailureParamError(
                        f"{ctx} must be a whole number, got {value!r}")
                num = int(round(num))
            if self.min is not None and num < self.min:
                raise FailureParamError(
                    f"{ctx} must be >= {self.min}{self._u()}, got {num}")
            if self.max is not None and num > self.max:
                raise FailureParamError(
                    f"{ctx} must be <= {self.max}{self._u()}, got {num}")
            return num

        # T_STRING
        if not isinstance(value, str):
            raise FailureParamError(f"{ctx} must be a string, got {value!r}")
        if self.values and value not in self.values:
            raise FailureParamError(
                f"{ctx} must be one of {list(self.values)}, got {value!r}")
        return value

    def _u(self) -> str:
        return f" {self.unit}" if self.unit else ""


@dataclass(frozen=True)
class FailureSpec:
    """Declarative description of one injectable failure."""

    name: str
    label: str
    effect: str = ""                       # what it should do, in words
    subsystem: str = ""                    # filled in by the catalog
    magnitude: ParamSpec | None = None     # None == `magnitude` is unused
    params: tuple[ParamSpec, ...] = ()
    reversible: bool = True                # clear_failure fully reverses it
    notes: str = ""

    def param(self, name: str) -> ParamSpec | None:
        for p in self.params:
            if p.name == name:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "subsystem": self.subsystem,
            "label": self.label,
            "effect": self.effect,
            "reversible": self.reversible,
            "notes": self.notes,
            "magnitude": self.magnitude.to_dict() if self.magnitude else None,
            "params": [p.to_dict() for p in self.params],
        }

    def defaults(self) -> dict[str, Any]:
        """Default kwargs for this failure (params carrying a default)."""
        return {p.name: p.default for p in self.params if p.default is not None}

    def default_magnitude(self) -> float:
        if self.magnitude is None or self.magnitude.default is None:
            return 1.0
        return float(self.magnitude.default)

    # ── validation ───────────────────────────────────────────────────
    def validate(self, magnitude: Any = None,
                 params: dict[str, Any] | None = None,
                 *, strict_unknown: bool = True) -> tuple[float, dict[str, Any]]:
        """Validate an injection. Returns coerced ``(magnitude, params)``.

        Raises :class:`FailureParamError` with an operator-readable reason.
        """
        where = f"{self.subsystem}.{self.name}" if self.subsystem else self.name
        params = dict(params or {})

        if self.magnitude is None:
            mag = 1.0 if magnitude is None else float(magnitude)
        elif magnitude is None:
            mag = self.default_magnitude()
        else:
            mag = float(self.magnitude.coerce(magnitude, where=where))

        out: dict[str, Any] = {}
        for spec in self.params:
            if spec.name in params:
                out[spec.name] = spec.coerce(params.pop(spec.name), where=where)
            elif spec.required:
                raise FailureParamError(
                    f"{where}: required parameter {spec.name!r} "
                    f"({spec.label}) is missing")
            elif spec.default is not None:
                out[spec.name] = spec.default

        if params and strict_unknown:
            unknown = sorted(params)
            known = sorted(p.name for p in self.params)
            raise FailureParamError(
                f"{where}: unknown parameter(s) {unknown} — "
                f"this failure accepts {known or '(none)'}")
        out.update(params)
        return mag, out


# ── list helpers used by the model spec tables ───────────────────────

def enum(name: str, label: str, values, *, labels=None, default=None,
         required: bool = False, description: str = "",
         multi: bool = False) -> ParamSpec:
    return ParamSpec(name=name, type=T_ENUM, label=label,
                     values=tuple(values),
                     value_labels=tuple(labels) if labels else None,
                     default=default, required=required,
                     description=description, multi=multi)


def number(name: str, label: str, *, unit: str = "", lo: float | None = None,
           hi: float | None = None, default=None, step: float | None = None,
           integer: bool = False, required: bool = False,
           description: str = "") -> ParamSpec:
    return ParamSpec(name=name, type=T_INT if integer else T_FLOAT,
                     label=label, unit=unit, min=lo, max=hi, default=default,
                     step=step, required=required, description=description)


def boolean(name: str, label: str, *, default=None,
            description: str = "") -> ParamSpec:
    return ParamSpec(name=name, type=T_BOOL, label=label, default=default,
                     description=description)
