"""SMO Common — YAML Configuration Loader."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any, TypeVar, Type

import yaml
from pydantic import BaseModel

from .schemas import (
    MissionConfig, NetworkConfig, OrbitConfig, GroundStationConfig,
    EPSConfig, AOCSConfig, TCSConfig, OBDHConfig, TTCConfig, PayloadConfig,
    FDIRConfig, HKStructureDef, ParameterDef, LimitDef, TCCommandDef,
    ScenarioConfig, MCSDisplayConfig, PositionConfig,
    EventDefinition, EventCatalog,
    MemoryMapConfig, PUSServicesConfig, ProcedureIndex,
    ActivityTypesConfig, ActivityTypeConfig,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a single YAML file and return its contents as a dict."""
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data if data is not None else {}


def load_model(path: Path, model_class: Type[T]) -> T:
    """Load a YAML file and validate it against a Pydantic model."""
    data = load_yaml(path)
    return model_class.model_validate(data)


def load_mission_config(config_dir: Path) -> MissionConfig:
    """Load mission.yaml from the config directory."""
    return load_model(config_dir / "mission.yaml", MissionConfig)


def load_orbit_config(config_dir: Path) -> OrbitConfig:
    """Load orbit.yaml from the config directory."""
    return load_model(config_dir / "orbit.yaml", OrbitConfig)


def load_subsystem_config(config_dir: Path, subsystem: str, model_class: Type[T]) -> T:
    """Load a subsystem YAML config (e.g. eps.yaml) from subsystems/ directory."""
    path = config_dir / "subsystems" / f"{subsystem}.yaml"
    return load_model(path, model_class)


def load_all_subsystem_configs(config_dir: Path) -> dict[str, BaseModel]:
    """Load all subsystem configs from the config directory."""
    mapping: dict[str, type[BaseModel]] = {
        "eps": EPSConfig,
        "aocs": AOCSConfig,
        "tcs": TCSConfig,
        "obdh": OBDHConfig,
        "ttc": TTCConfig,
        "payload": PayloadConfig,
    }
    configs = {}
    for name, cls in mapping.items():
        path = config_dir / "subsystems" / f"{name}.yaml"
        if path.exists():
            configs[name] = load_model(path, cls)
            logger.info("Loaded subsystem config: %s", name)
        else:
            logger.warning("Subsystem config not found: %s", path)
            configs[name] = cls()
    return configs


def load_fdir_config(config_dir: Path) -> FDIRConfig:
    """Load FDIR configuration."""
    path = config_dir / "subsystems" / "fdir.yaml"
    if path.exists():
        return load_model(path, FDIRConfig)
    return FDIRConfig()


def load_hk_structures(config_dir: Path) -> list[HKStructureDef]:
    """Load HK structure definitions from telemetry/hk_structures.yaml."""
    path = config_dir / "telemetry" / "hk_structures.yaml"
    if not path.exists():
        return []
    data = load_yaml(path)
    structures = data.get("structures", [])
    return [HKStructureDef.model_validate(s) for s in structures]


def load_parameters(config_dir: Path) -> list[ParameterDef]:
    """Load parameter definitions from telemetry/parameters.yaml."""
    path = config_dir / "telemetry" / "parameters.yaml"
    if not path.exists():
        return []
    data = load_yaml(path)
    params = data.get("parameters", [])
    return [ParameterDef.model_validate(p) for p in params]


def load_limits(config_dir: Path) -> list[LimitDef]:
    """Load limit definitions from mcs/limits.yaml."""
    path = config_dir / "mcs" / "limits.yaml"
    if not path.exists():
        return []
    data = load_yaml(path)
    limits = data.get("limits", [])
    return [LimitDef.model_validate(l) for l in limits]


def load_tc_catalog(config_dir: Path) -> list[TCCommandDef]:
    """Load TC catalog from commands/tc_catalog.yaml."""
    path = config_dir / "commands" / "tc_catalog.yaml"
    if not path.exists():
        return []
    data = load_yaml(path)
    commands = data.get("commands", [])
    return [TCCommandDef.model_validate(c) for c in commands]


def load_scenarios(config_dir: Path) -> list[ScenarioConfig]:
    """Load all scenario YAML files from scenarios/ directory."""
    scenario_dir = config_dir / "scenarios"
    if not scenario_dir.exists():
        return []
    scenarios = []
    for path in sorted(scenario_dir.glob("*.yaml")):
        try:
            scenarios.append(load_model(path, ScenarioConfig))
        except Exception as e:
            logger.warning("Failed to load scenario %s: %s", path.name, e)
    return scenarios


def load_event_catalog(config_dir: Path) -> EventCatalog:
    """Load event catalog from events/event_catalog.yaml."""
    path = config_dir / "events" / "event_catalog.yaml"
    if path.exists():
        return load_model(path, EventCatalog)
    return EventCatalog()


def load_mcs_displays(config_dir: Path) -> MCSDisplayConfig:
    """Load MCS display configuration from mcs/displays.yaml."""
    path = config_dir / "mcs" / "displays.yaml"
    if path.exists():
        return load_model(path, MCSDisplayConfig)
    return MCSDisplayConfig()


def load_positions(config_dir: Path) -> dict[str, PositionConfig]:
    """Load position access control from mcs/positions.yaml."""
    path = config_dir / "mcs" / "positions.yaml"
    if not path.exists():
        return {}
    data = load_yaml(path)
    positions = {}
    for name, pos_data in data.get("positions", {}).items():
        positions[name] = PositionConfig.model_validate(pos_data)
    return positions


# ===== Phase 3: New Loaders =====

def load_memory_map(config_dir: Path) -> MemoryMapConfig:
    """Load onboard memory map from subsystems/memory_map.yaml."""
    path = config_dir / "subsystems" / "memory_map.yaml"
    if path.exists():
        return load_model(path, MemoryMapConfig)
    return MemoryMapConfig()


def load_pus_service_config(config_dir: Path) -> PUSServicesConfig:
    """Load PUS service configuration from mcs/pus_services.yaml."""
    path = config_dir / "mcs" / "pus_services.yaml"
    if path.exists():
        return load_model(path, PUSServicesConfig)
    return PUSServicesConfig()


def load_procedure_index(config_dir: Path) -> ProcedureIndex:
    """Load procedure master index from procedures/procedure_index.yaml."""
    path = config_dir / "procedures" / "procedure_index.yaml"
    if path.exists():
        return load_model(path, ProcedureIndex)
    return ProcedureIndex()


def load_activity_types(config_dir: Path) -> ActivityTypesConfig:
    """Load activity type definitions from planning/activity_types.yaml."""
    path = config_dir / "planning" / "activity_types.yaml"
    if path.exists():
        return load_model(path, ActivityTypesConfig)
    return ActivityTypesConfig()
