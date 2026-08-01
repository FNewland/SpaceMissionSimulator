"""Schema-driven form model generation from the pydantic config schemas.

Walks a pydantic model's fields and produces a JSON form specification —
types, defaults, numeric ranges, descriptions, nesting — that the editor UI
renders generically (no per-file hand-written form code). Also provides
``unknown_key_report``: every key in a parsed YAML document that the loader
would silently drop (pydantic default ``extra="ignore"``), so the UI can
show a red "IGNORED BY LOADER" band instead of eating the key.
"""
from __future__ import annotations

import typing
from typing import Any, Optional, Union, get_args, get_origin

from pydantic import BaseModel

from ..config import schemas as _schemas
from ..config.schemas import (
    EventCatalog,
    FDIRConfig,
    MCSDisplayConfig,
    MemoryMapConfig,
    MissionConfig,
    OrbitConfig,
    PUSServicesConfig,
    ProcedureIndex,
    ScenarioConfig,
    SimulationConfig,
)

__all__ = ["schema_model_for", "form_spec_for", "unknown_key_report",
           "SCHEMA_MAP"]


class _ListWrapper:
    """Marker for files whose top level is ``{<key>: [<Model>, ...]}``."""

    def __init__(self, key: str, model: type[BaseModel]):
        self.key = key
        self.model = model


def _subsystem_model(name: str) -> Optional[type[BaseModel]]:
    cls = getattr(_schemas, f"{name.upper()}Config", None) \
        or getattr(_schemas, f"{name.capitalize()}Config", None)
    return cls if isinstance(cls, type) and issubclass(cls, BaseModel) else None


#: configs-relative path (or fnmatch pattern) → schema. Checked in order.
SCHEMA_MAP: list[tuple[str, Any]] = [
    ("mission.yaml", MissionConfig),
    ("orbit.yaml", OrbitConfig),
    ("simulation.yaml", SimulationConfig),
    ("subsystems/fdir.yaml", FDIRConfig),
    ("subsystems/memory_map.yaml", MemoryMapConfig),
    ("memory/memory_map.yaml", MemoryMapConfig),
    ("telemetry/hk_structures.yaml",
     _ListWrapper("structures", _schemas.HKStructureDef)),
    ("telemetry/parameters.yaml",
     _ListWrapper("parameters", _schemas.ParameterDef)),
    ("mcs/limits.yaml", _ListWrapper("limits", _schemas.LimitDef)),
    ("commands/tc_catalog.yaml",
     _ListWrapper("commands", _schemas.TCCommandDef)),
    ("events/event_catalog.yaml", EventCatalog),
    ("mcs/displays.yaml", MCSDisplayConfig),
    ("mcs/positions.yaml", MCSDisplayConfig),
    ("mcs/pus_services.yaml", PUSServicesConfig),
    ("procedures/procedure_index.yaml", ProcedureIndex),
    ("planning/activity_types.yaml", _schemas.ActivityTypesConfig),
    ("scenarios/*.yaml", ScenarioConfig),
]


def schema_model_for(relpath: str) -> Optional[Union[type[BaseModel], _ListWrapper]]:
    """The pydantic schema that loads this file, if the loader has one."""
    import fnmatch
    rp = relpath.replace("\\", "/").lstrip("/")
    for pattern, model in SCHEMA_MAP:
        if fnmatch.fnmatch(rp, pattern):
            return model
    # subsystems/<name>.yaml → <NAME>Config when it exists
    if rp.startswith("subsystems/") and rp.endswith(".yaml"):
        return _subsystem_model(rp.split("/")[-1][:-5])
    return None


# ── form-spec generation ─────────────────────────────────────────────────

_PRIMITIVES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _annotation_spec(ann: Any, depth: int) -> dict[str, Any]:
    """Turn a type annotation into a form field type description."""
    if ann in _PRIMITIVES:
        return {"type": _PRIMITIVES[ann]}
    origin = get_origin(ann)
    if origin is Union or origin is getattr(typing, "UnionType", None):
        args = [a for a in get_args(ann) if a is not type(None)]
        spec = _annotation_spec(args[0], depth) if args else {"type": "any"}
        spec["nullable"] = type(None) in get_args(ann)
        return spec
    if origin in (list, tuple, set):
        args = get_args(ann)
        item = _annotation_spec(args[0], depth + 1) if args else {"type": "any"}
        return {"type": "list", "items": item}
    if origin is dict:
        args = get_args(ann)
        val = _annotation_spec(args[1], depth + 1) if len(args) == 2 else {"type": "any"}
        return {"type": "mapping", "values": val}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        if depth > 6:
            return {"type": "object", "model": ann.__name__}
        return {"type": "object", "model": ann.__name__,
                "fields": _model_fields(ann, depth + 1)}
    if get_origin(ann) is typing.Literal:
        return {"type": "enum", "values": list(get_args(ann))}
    return {"type": "any"}


def _model_fields(model: type[BaseModel], depth: int = 0) -> list[dict]:
    fields = []
    for name, fi in model.model_fields.items():
        spec = _annotation_spec(fi.annotation, depth)
        spec["name"] = name
        if fi.description:
            spec["description"] = fi.description
        try:
            if fi.default is not None and not fi.default_factory \
                    and not isinstance(fi.default, BaseModel) \
                    and repr(fi.default) != "PydanticUndefined":
                spec["default"] = fi.default
        except Exception:  # noqa: BLE001 — default rendering is best-effort
            pass
        spec["required"] = fi.is_required()
        # numeric constraints from Field(ge=…, le=…)
        for meta in fi.metadata or []:
            for attr, key in (("ge", "min"), ("le", "max"),
                              ("gt", "exclusive_min"), ("lt", "exclusive_max")):
                v = getattr(meta, attr, None)
                if v is not None:
                    spec[key] = v
        fields.append(spec)
    return fields


def form_spec_for(relpath: str) -> Optional[dict[str, Any]]:
    """JSON form specification for a config file, or ``None`` (no schema —
    the UI falls back to the raw YAML editor for such files)."""
    model = schema_model_for(relpath)
    if model is None:
        return None
    if isinstance(model, _ListWrapper):
        return {
            "kind": "list_file",
            "list_key": model.key,
            "model": model.model.__name__,
            "item_fields": _model_fields(model.model),
        }
    return {
        "kind": "object_file",
        "model": model.__name__,
        "fields": _model_fields(model),
    }


# ── unknown-key report ───────────────────────────────────────────────────

def _walk_unknown(data: Any, model: type[BaseModel], path: str,
                  out: list[dict]) -> None:
    if not isinstance(data, dict):
        return
    fields = model.model_fields
    # alias support
    names: dict[str, Any] = {}
    for fname, fi in fields.items():
        names[fname] = fi
        if fi.alias:
            names[fi.alias] = fi
    for key, value in data.items():
        if key not in names:
            out.append({"path": f"{path}{key}",
                        "msg": f"key {key!r} is not in the "
                               f"{model.__name__} schema — the loader "
                               "silently ignores it"})
            continue
        fi = names[key]
        ann = fi.annotation
        # unwrap Optional
        if get_origin(ann) is Union or get_origin(ann) is getattr(typing, "UnionType", None):
            args = [a for a in get_args(ann) if a is not type(None)]
            ann = args[0] if args else ann
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            _walk_unknown(value, ann, f"{path}{key}.", out)
        elif get_origin(ann) in (list, tuple):
            item = (get_args(ann) or [None])[0]
            if isinstance(item, type) and issubclass(item, BaseModel) \
                    and isinstance(value, list):
                for i, entry in enumerate(value):
                    _walk_unknown(entry, item, f"{path}{key}[{i}].", out)


def unknown_key_report(relpath: str, data: Any) -> list[dict]:
    """Keys in ``data`` the loader for ``relpath`` would silently drop.

    Empty when the file has no known schema (nothing to compare against) —
    the caller distinguishes "no unknown keys" from "no schema" via
    :func:`schema_model_for`.
    """
    model = schema_model_for(relpath)
    if model is None or not isinstance(data, dict):
        return []
    out: list[dict] = []
    if isinstance(model, _ListWrapper):
        for key in data:
            if key != model.key:
                out.append({"path": key,
                            "msg": f"key {key!r} is not read — this file's "
                                   f"loader only consumes {model.key!r}"})
        for i, entry in enumerate(data.get(model.key) or []):
            _walk_unknown(entry, model.model, f"{model.key}[{i}].", out)
        return out
    _walk_unknown(data, model, "", out)
    return out
