"""SMO Common — Subsystem Model Registry.

Discovers SubsystemModel implementations via Python entry points
(group: 'smo.subsystem_models'), with fallback to direct imports
of the built-in models from smo_simulator.models.
"""
from __future__ import annotations
import logging
from importlib.metadata import entry_points
from typing import Type

from .subsystem import SubsystemModel

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "smo.subsystem_models"

_registry: dict[str, Type[SubsystemModel]] = {}


def _register_builtins() -> None:
    """Directly import and register the 6 built-in subsystem models.

    This is used as a fallback when entry points are not available
    (e.g. running with PYTHONPATH instead of pip install -e).
    """
    builtins = {
        "eps_basic": "smo_simulator.models.eps_basic.EPSBasicModel",
        "aocs_basic": "smo_simulator.models.aocs_basic.AOCSBasicModel",
        "tcs_basic": "smo_simulator.models.tcs_basic.TCSBasicModel",
        "obdh_basic": "smo_simulator.models.obdh_basic.OBDHBasicModel",
        "ttc_basic": "smo_simulator.models.ttc_basic.TTCBasicModel",
        "payload_basic": "smo_simulator.models.payload_basic.PayloadBasicModel",
    }
    for name, qualname in builtins.items():
        module_path, class_name = qualname.rsplit(".", 1)
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            if isinstance(cls, type) and issubclass(cls, SubsystemModel):
                _registry[name] = cls
                logger.debug("Registered builtin model: %s -> %s", name, class_name)
        except Exception as e:
            logger.warning("Failed to import builtin model %s: %s", name, e)


def discover_models() -> dict[str, Type[SubsystemModel]]:
    """Scan entry points and populate the registry.

    Falls back to direct imports of built-in models when no entry
    points are found (i.e. packages not installed via pip install -e).
    """
    global _registry
    eps = entry_points()
    group = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, 'select') else eps.get(ENTRY_POINT_GROUP, [])
    for ep in group:
        try:
            cls = ep.load()
            if isinstance(cls, type) and issubclass(cls, SubsystemModel):
                _registry[ep.name] = cls
                logger.debug("Registered subsystem model: %s -> %s", ep.name, cls.__name__)
            else:
                logger.warning("Entry point %s is not a SubsystemModel subclass", ep.name)
        except Exception as e:
            logger.warning("Failed to load entry point %s: %s", ep.name, e)
    if not _registry:
        logger.info("No entry points found for %s, loading built-in models", ENTRY_POINT_GROUP)
        _register_builtins()
    return dict(_registry)


def get_model_class(name: str) -> Type[SubsystemModel] | None:
    """Look up a model class by entry point name."""
    if not _registry:
        discover_models()
    return _registry.get(name)


def create_model(name: str, config: dict | None = None) -> SubsystemModel:
    """Create and configure a subsystem model instance by entry point name."""
    cls = get_model_class(name)
    if cls is None:
        raise ValueError(f"Unknown subsystem model: {name!r}. "
                         f"Available: {list(_registry.keys())}")
    instance = cls()
    if config:
        instance.configure(config)
    return instance


def register_model(name: str, cls: Type[SubsystemModel]) -> None:
    """Manually register a model class (useful for testing)."""
    _registry[name] = cls


def list_models() -> list[str]:
    """Return names of all registered models."""
    if not _registry:
        discover_models()
    return list(_registry.keys())
