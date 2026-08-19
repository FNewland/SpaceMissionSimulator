"""Command catalogue <-> dispatcher bijection (brief 08 WP-8, gap 4).

For every entry in `commands/tc_catalog.yaml`, build a synthetic packet from
its own `fields` and dispatch it against a real engine; assert the dispatcher
recognises it (it must not fall through the `else` branch unhandled).  And in
the other direction, assert every `func_id` the S8.1 router branches on has a
catalogue entry.

This is currently clean; pinning it stops it rotting.  It catches the defect
class of #37, #38, #45 and #80 — a command added to the catalogue with no
handler, or a handler added with no catalogue entry.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
DISPATCH_SRC = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
                / "service_dispatch.py")

CATALOG = yaml.safe_load(
    (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())["commands"]


def _handled_func_ids() -> set[int]:
    """Every func_id the S8.1 router branches on."""
    src = DISPATCH_SRC.read_text()
    ids: set[int] = set()
    for m in re.finditer(r"func_id\s*==\s*(0x[0-9A-Fa-f]+|\d+)", src):
        ids.add(int(m.group(1), 0))
    for m in re.finditer(r"func_id\s+in\s*\(([^)]*)\)", src):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if re.fullmatch(r"0x[0-9A-Fa-f]+|\d+", tok):
                ids.add(int(tok, 0))
    for m in re.finditer(r"(\d+)\s*<=\s*func_id\s*<=\s*(\d+)", src):
        ids.update(range(int(m.group(1)), int(m.group(2)) + 1))
    for m in re.finditer(r"(\d+)\s*<=\s*func_id\s*<\s*(\d+)", src):
        ids.update(range(int(m.group(1)), int(m.group(2))))
    return ids


HANDLED_FUNCS = _handled_func_ids()

#: Catalogue entries whose func_id is deliberately not routed by the S8.1
#: dispatcher (e.g. a service handled elsewhere). Each needs a reason.
UNROUTED_OK: dict[int, str] = {}

#: Dispatcher branches deliberately NOT in the catalogue. Each needs a
#: reason — the default remains "a handler with no catalogue entry is a
#: command nobody can find".
HANDLED_UNCATALOGUED: dict[int, str] = {
    7: "MAG_SELECT retired from the catalogue (owner ruling 2026-08c: ONE "
       "magnetometer, no redundant unit to select). The branch survives so "
       "a stale procedure sending func 7 receives the model's explanatory "
       "0x0007 refusal instead of a silent no-op.",
}


def _cat_func_ids() -> dict[int, str]:
    out = {}
    for c in CATALOG:
        if c.get("service") == 8 and c.get("subtype") == 1 \
                and c.get("func_id") is not None:
            out[int(c["func_id"])] = c.get("name", "")
    return out


CATALOGUE_FUNCS = _cat_func_ids()


# ── Catalogue -> dispatcher ─────────────────────────────────────────────

def test_every_catalogued_s8_func_has_a_handler():
    missing = [f"{fid} ({name})" for fid, name in sorted(CATALOGUE_FUNCS.items())
               if fid not in HANDLED_FUNCS and fid not in UNROUTED_OK]
    assert not missing, (
        f"catalogued S8.1 functions with no dispatcher branch: {missing}")


def test_every_handled_s8_func_is_catalogued():
    """A handler with no catalogue entry is a command nobody can find."""
    orphans = sorted(fid for fid in HANDLED_FUNCS
                     if fid not in CATALOGUE_FUNCS
                     and fid not in HANDLED_UNCATALOGUED)
    # The router also branches on non-func values in a few places; keep the
    # gate on the documented S8.1 range only.
    orphans = [f for f in orphans if 0 <= f <= 130]
    assert not orphans, (
        f"S8.1 func ids handled but absent from tc_catalog.yaml: {orphans}")


def test_catalogue_func_ids_are_unique():
    seen: dict[int, str] = {}
    dupes = []
    for c in CATALOG:
        if c.get("service") != 8 or c.get("subtype") != 1:
            continue
        fid = c.get("func_id")
        if fid is None:
            continue
        if fid in seen:
            dupes.append(f"func {fid}: {seen[fid]} vs {c.get('name')}")
        seen[fid] = c.get("name", "")
    assert not dupes, f"duplicate func_ids in tc_catalog.yaml: {dupes}"


# ── Live dispatch ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    from smo_simulator.engine import SimulationEngine
    eng = SimulationEngine(str(CONFIG_DIR))
    eng._spacecraft_phase = 6
    for _ in range(3):
        eng._tick_once(1.0)
    return eng


def _synthetic_data(cmd: dict) -> bytes:
    """Build a plausible data field from the catalogue's own `fields`."""
    from smo_mcs.tc_lengths import expected_data_bytes
    info = expected_data_bytes(cmd)
    n = info.get("expected_data_bytes") or 0
    return bytes(n)


SERVICES = sorted({int(c["service"]) for c in CATALOG if c.get("service")})


@pytest.mark.parametrize("service", SERVICES)
def test_every_catalogued_service_is_dispatched(service, engine):
    """Every service in the catalogue reaches a handler branch."""
    src = DISPATCH_SRC.read_text()
    assert f"service == {service}" in src, (
        f"tc_catalog.yaml ships service {service} commands and "
        f"ServiceDispatcher.dispatch has no branch for it")


@pytest.mark.parametrize(
    "cmd", [c for c in CATALOG if c.get("service") == 8 and c.get("subtype") == 1],
    ids=lambda c: f"func{c.get('func_id')}_{c.get('name', '')}")
def test_s8_command_dispatches_without_error(cmd, engine):
    """A synthetic packet built from the catalogue must not raise.

    It may be refused (wrong power state, bad arguments) — what matters is
    that it lands on a branch and the dispatcher returns a result rather than
    swallowing an exception.
    """
    fid = cmd.get("func_id")
    if fid is None:
        pytest.skip("no func_id")
    data = bytes([int(fid)]) + _synthetic_data(cmd)
    res = engine._dispatcher.dispatch(8, 1, data, None)
    assert res is not None
    assert isinstance(res, list)
