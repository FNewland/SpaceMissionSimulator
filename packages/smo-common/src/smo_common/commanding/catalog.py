"""Phase E (brief 04 WP-0) — the TC argument-schema catalogue.

``tc_catalog.yaml`` is the single source of truth for every telecommand's
wire form: field types, offsets, endianness, engineering units, raw<->eng
calibration, valid ranges, defaults and enum/bit labels. This module loads
it once and gives every consumer — the simulator's dispatcher tests, the
MCS builder (via /api/tc-catalog), the procedure runner, the OBCP loader,
the planner, the S11 embedder, the XTCE/SMDL generators and the command
reference generator — the same resolved view, so the six consumers cannot
drift again (they have twice; see the divergence table in brief 04).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

__all__ = [
    "Catalog",
    "load_catalog",
    "default_catalog",
    "find_config_dir",
    "FIELD_WIDTHS",
    "field_width",
]

#: Fixed on-the-wire width (bytes) per scalar field type. Must agree with
#: ``smo_mcs.tc_lengths.FIELD_WIDTHS`` and the dispatcher's struct formats.
FIELD_WIDTHS: dict[str, int] = {
    "uint8": 1, "int8": 1, "uint16": 2, "int16": 2,
    "uint32": 4, "int32": 4, "float32": 4, "float64": 8,
}

#: struct format char per type (big-endian '>' applied by the packer).
STRUCT_FMT: dict[str, str] = {
    "uint8": "B", "int8": "b", "uint16": "H", "int16": "h",
    "uint32": "I", "int32": "i", "float32": "f", "float64": "d",
}

VARIABLE_FIELD_TYPES = frozenset({"bytes", "string", "blob"})


def field_width(ftype: str) -> Optional[int]:
    key = str(ftype or "").strip().lower()
    if key in VARIABLE_FIELD_TYPES:
        return None
    return FIELD_WIDTHS.get(key)


def find_config_dir(config_dir: Path | str | None = None) -> Path:
    """Resolve the mission config directory carrying commands/tc_catalog.yaml.

    Order: explicit argument > $SMO_CONFIG_DIR > ./configs/eosat1 relative to
    the current working directory > configs/eosat1 relative to the repository
    root that contains this package (packages/smo-common/src/smo_common).
    """
    if config_dir is not None:
        p = Path(config_dir)
        if (p / "commands" / "tc_catalog.yaml").exists():
            return p
        raise FileNotFoundError(f"no commands/tc_catalog.yaml under {p}")
    env = os.environ.get("SMO_CONFIG_DIR")
    if env and (Path(env) / "commands" / "tc_catalog.yaml").exists():
        return Path(env)
    cwd_guess = Path.cwd() / "configs" / "eosat1"
    if (cwd_guess / "commands" / "tc_catalog.yaml").exists():
        return cwd_guess
    # packages/smo-common/src/smo_common/commanding/catalog.py -> repo root
    repo = Path(__file__).resolve().parents[4]
    for base in (repo, repo.parent):
        guess = base / "configs" / "eosat1"
        if (guess / "commands" / "tc_catalog.yaml").exists():
            return guess
    raise FileNotFoundError("cannot locate configs/<mission>/commands/tc_catalog.yaml")


class Catalog:
    """Resolved TC catalogue with name / alias / (service,subtype,func) indexes."""

    def __init__(self, commands: list[dict[str, Any]],
                 quick_commands: list[dict[str, Any]] | None = None):
        self.commands = commands
        self.quick_commands = quick_commands or []
        self.by_name: dict[str, dict] = {}
        self.by_key: dict[tuple[int, int, int | None], dict] = {}
        for c in commands:
            self.by_name[c["name"]] = c
            for a in c.get("aliases") or []:
                self.by_name.setdefault(a, c)
            key = (int(c["service"]), int(c["subtype"]), c.get("func_id"))
            self.by_key.setdefault(key, c)

    def get(self, name: str) -> dict:
        try:
            return self.by_name[name]
        except KeyError:
            raise KeyError(f"unknown command {name!r} (not in tc_catalog.yaml)") from None

    def by_func(self, service: int, subtype: int, func_id: int | None) -> dict | None:
        return self.by_key.get((int(service), int(subtype), func_id))


_CACHE: dict[str, Catalog] = {}


def load_catalog(config_dir: Path | str | None = None) -> Catalog:
    """Load (and cache) the TC catalogue for a config directory."""
    cdir = find_config_dir(config_dir)
    key = str(cdir.resolve())
    if key not in _CACHE:
        raw = yaml.safe_load((cdir / "commands" / "tc_catalog.yaml").read_text())
        commands = raw.get("commands") if isinstance(raw, dict) else raw
        quick = raw.get("quick_commands") if isinstance(raw, dict) else None
        _CACHE[key] = Catalog(list(commands or []), quick)
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()


def default_catalog() -> Catalog:
    return load_catalog(None)
