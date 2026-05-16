"""Shared utilities for XTCE and SMDL generators.

Loads YAML TM/TC definitions and provides type mapping, UUID generation,
and subsystem grouping used by both generate_xtce.py and generate_smdl.py.
"""

import uuid
import yaml
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "configs" / "eosat1"

# Fixed namespaces for deterministic UUID generation
XTCE_NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:eosat1:xtce")
SMDL_NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:eosat1:smdl")


def xtce_uuid(name: str) -> str:
    return str(uuid.uuid5(XTCE_NS, name))


def smdl_uuid(name: str) -> str:
    return str(uuid.uuid5(SMDL_NS, name))


def _hex_int(v) -> int:
    """Parse a YAML value that may be hex string or int."""
    if isinstance(v, int):
        return v
    return int(str(v), 0)


# ── pack_format → type attributes ──

PACK_FORMAT_MAP = {
    "B": {"xtce_type": "Integer", "bits": 8, "signed": False, "struct": "B"},
    "H": {"xtce_type": "Integer", "bits": 16, "signed": False, "struct": "H"},
    "I": {"xtce_type": "Integer", "bits": 32, "signed": False, "struct": "I"},
    "b": {"xtce_type": "Integer", "bits": 8, "signed": True, "struct": "b"},
    "h": {"xtce_type": "Integer", "bits": 16, "signed": True, "struct": "h"},
    "i": {"xtce_type": "Integer", "bits": 32, "signed": True, "struct": "i"},
    "f": {"xtce_type": "Float", "bits": 32, "signed": True, "struct": "f"},
}

TC_TYPE_MAP = {
    "uint8": {"bits": 8, "signed": False},
    "uint16": {"bits": 16, "signed": False},
    "uint32": {"bits": 32, "signed": False},
    "int16": {"bits": 16, "signed": True},
    "int32": {"bits": 32, "signed": True},
    "float32": {"bits": 32, "signed": True, "float": True},
    "bytes": {"bits": 0, "signed": False, "binary": True},
}

# ── Subsystem name normalization ──

SUBSYSTEM_NAMES = {
    "eps": "EPS", "aocs": "AOCS", "obdh": "OBDH", "tcs": "TCS",
    "ttc": "TTC", "payload": "Payload", "fdir": "FDIR",
    "spacecraft": "Spacecraft", "contact": "Spacecraft",
    "procedure": "Spacecraft", "sat": "Spacecraft",
}

# SID → subsystem mapping
SID_SUBSYSTEM = {
    1: "EPS", 2: "AOCS", 3: "TCS", 4: "OBDH",
    5: "Payload", 6: "TTC", 11: "Beacon",
}

# func_id ranges → subsystem
FUNC_ID_SUBSYSTEM = [
    (range(0, 16), "AOCS"),
    (range(16, 26), "EPS"),
    (range(26, 40), "Payload"),
    (range(40, 50), "TCS"),
    (range(50, 63), "OBDH"),
    (range(63, 79), "TTC"),
    (range(80, 83), "OBDH"),
    (range(100, 108), "EPS"),
]


def func_id_to_subsystem(fid: int) -> str:
    for r, name in FUNC_ID_SUBSYSTEM:
        if fid in r:
            return name
    return "Spacecraft"


# ── YAML loaders ──

def load_parameters() -> list[dict]:
    with open(CONFIG / "telemetry" / "parameters.yaml") as f:
        data = yaml.safe_load(f)
    params = []
    for p in data.get("parameters", []):
        p["id"] = _hex_int(p["id"])
        p["subsystem_name"] = SUBSYSTEM_NAMES.get(p.get("subsystem", ""), "Spacecraft")
        # Sanitize param name for XML (replace dots with underscores)
        p["xml_name"] = p["name"].replace(".", "_")
        params.append(p)
    return params


def load_hk_structures() -> list[dict]:
    with open(CONFIG / "telemetry" / "hk_structures.yaml") as f:
        data = yaml.safe_load(f)
    structures = []
    for s in data.get("structures", []):
        for p in s.get("parameters", []):
            p["param_id"] = _hex_int(p["param_id"])
        structures.append(s)
    return structures


def load_commands() -> list[dict]:
    with open(CONFIG / "commands" / "tc_catalog.yaml") as f:
        data = yaml.safe_load(f)
    cmds = []
    for c in data.get("commands", []):
        if "func_id" in c:
            c["subsystem_name"] = func_id_to_subsystem(c["func_id"])
        else:
            c["subsystem_name"] = "Spacecraft"
        cmds.append(c)
    return cmds


def load_s12_definitions() -> list[dict]:
    path = CONFIG / "monitoring" / "s12_definitions.yaml"
    if not path.exists():
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    rules = []
    for r in data.get("s12_definitions", data.get("definitions", data.get("monitors", data.get("rules", [])))):
        if "param_id" in r:
            r["param_id"] = _hex_int(r["param_id"])
        rules.append(r)
    return rules


def load_mission_config() -> dict:
    with open(CONFIG / "mission.yaml") as f:
        return yaml.safe_load(f)


def build_param_lookup(params: list[dict]) -> dict[int, dict]:
    """Build a dict mapping param_id → param definition."""
    return {p["id"]: p for p in params}


def build_hk_param_formats(hk_structures: list[dict]) -> dict[int, dict]:
    """Build a dict mapping param_id → {pack_format, scale} from HK structures."""
    result = {}
    for sid_struct in hk_structures:
        for p in sid_struct.get("parameters", []):
            result[p["param_id"]] = {
                "pack_format": p.get("pack_format", "f"),
                "scale": p.get("scale", 1),
                "sid": sid_struct["sid"],
            }
    return result


def group_params_by_subsystem(params: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for p in params:
        sub = p["subsystem_name"]
        groups.setdefault(sub, []).append(p)
    return groups


def group_commands_by_subsystem(cmds: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for c in cmds:
        sub = c["subsystem_name"]
        groups.setdefault(sub, []).append(c)
    return groups
