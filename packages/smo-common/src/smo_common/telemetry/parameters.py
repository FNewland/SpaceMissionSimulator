"""SMO Common — Parameter ID Registry.

Provides a mapping from parameter IDs to metadata (name, subsystem,
units, type) loaded from configuration.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParameterInfo:
    """Metadata for a single telemetry parameter."""
    id: int
    name: str
    subsystem: str = ""
    type: str = "float"
    units: str = ""
    description: str = ""


class ParameterRegistry:
    """Registry of parameter definitions, loaded from config."""

    def __init__(self):
        self._by_id: dict[int, ParameterInfo] = {}
        self._by_name: dict[str, ParameterInfo] = {}

    def register(self, param: ParameterInfo) -> None:
        self._by_id[param.id] = param
        self._by_name[param.name] = param

    def register_many(self, params: list[ParameterInfo]) -> None:
        for p in params:
            self.register(p)

    def get_by_id(self, param_id: int) -> Optional[ParameterInfo]:
        return self._by_id.get(param_id)

    def get_by_name(self, name: str) -> Optional[ParameterInfo]:
        return self._by_name.get(name)

    def resolve_name(self, name: str) -> Optional[int]:
        """Resolve a dotted parameter name (e.g. 'eps.battery_soc') to its ID."""
        p = self._by_name.get(name)
        return p.id if p else None

    def all_parameters(self) -> list[ParameterInfo]:
        return list(self._by_id.values())

    def load_from_config(self, param_defs: list[dict]) -> None:
        """Load parameters from config dicts (as returned by YAML loader)."""
        for d in param_defs:
            self.register(ParameterInfo(
                id=d["id"],
                name=d["name"],
                subsystem=d.get("subsystem", ""),
                type=d.get("type", "float"),
                units=d.get("units", ""),
                description=d.get("description", ""),
            ))
