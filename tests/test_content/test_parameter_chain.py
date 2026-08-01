"""The telemetry chain must close (brief 08 WP-8, gap 1).

Every parameter in `telemetry/parameters.yaml` has to be *reachable* by the
ground: it appears in at least one HK SID, or it is `on_demand: true` and the
S20.3 handler can return it, or it is explicitly allowlisted here with a
reason.  Every SID member has to be *produced* by a booted engine.  And every
parameter named in `mcs/limits.yaml` or `monitoring/s12_definitions.yaml` has
to be reachable, otherwise the limit or the monitor can never fire.

This replaces a class of one-off tests and would have caught defects #5, #36
and brief 08 findings 12 and 13.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


def _pid(v) -> int:
    return int(v) if isinstance(v, int) else int(str(v), 0)


def _load(rel: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / rel).read_text()) or {}


PARAMS = _load("telemetry/parameters.yaml")["parameters"]
HK = _load("telemetry/hk_structures.yaml")["structures"]
PARAM_BY_ID = {_pid(p["id"]): p for p in PARAMS}
HK_IDS = {_pid(m["param_id"]) for s in HK for m in (s.get("parameters") or [])}


#: Parameters that are deliberately neither in an HK SID nor on-demand.
#: Every entry needs a reason. Empty is the goal.
DERIVED_ONLY: dict[int, str] = {}


# ── 1. Every catalogued parameter is reachable ──────────────────────────

def test_every_parameter_is_reachable():
    unreachable = []
    for p in PARAMS:
        pid = _pid(p["id"])
        if pid in HK_IDS or p.get("on_demand") or pid in DERIVED_ONLY:
            continue
        unreachable.append(f"0x{pid:04X} {p.get('name')}")
    assert not unreachable, (
        f"{len(unreachable)} parameters are in no HK SID, are not on_demand "
        f"and are not allowlisted: {unreachable}")


def test_derived_only_allowlist_entries_still_exist():
    for pid in DERIVED_ONLY:
        assert pid in PARAM_BY_ID, f"stale DERIVED_ONLY entry 0x{pid:04X}"


def test_parameter_ids_are_unique():
    seen: dict[int, str] = {}
    dupes = []
    for p in PARAMS:
        pid = _pid(p["id"])
        if pid in seen:
            dupes.append(f"0x{pid:04X}: {seen[pid]} vs {p.get('name')}")
        seen[pid] = p.get("name", "")
    assert not dupes, f"duplicate parameter ids: {dupes}"


def test_ground_derived_pseudo_parameters_live_in_derived_yaml():
    """`contact.*` is computed by the ground, not downlinked.

    They sat in the spacecraft parameter catalogue where they could never be
    reachable — no HK SID can carry them and no S20 read can return them.
    """
    derived_path = CONFIG_DIR / "telemetry" / "derived.yaml"
    assert derived_path.exists(), "telemetry/derived.yaml is missing"
    derived = yaml.safe_load(derived_path.read_text())["derived_parameters"]
    names = {d["name"] for d in derived}
    assert {"contact.current_gs", "contact.time_to_aos",
            "contact.in_contact"} <= names
    catalogue_names = {p["name"] for p in PARAMS}
    assert not (names & catalogue_names), \
        "a derived parameter is also declared as spacecraft telemetry"


# ── 2. Every SID member exists and is produced ──────────────────────────

def test_every_sid_member_is_in_the_parameter_catalogue():
    orphans = []
    for s in HK:
        for m in s.get("parameters") or []:
            pid = _pid(m["param_id"])
            if pid not in PARAM_BY_ID:
                orphans.append(f"SID {s['sid']} -> 0x{pid:04X}")
    assert not orphans, f"HK members with no catalogue entry: {orphans}"


@pytest.fixture(scope="module")
def ticked_engine():
    from smo_simulator.engine import SimulationEngine
    eng = SimulationEngine(str(CONFIG_DIR))
    eng._spacecraft_phase = 6
    for _ in range(20):
        eng._tick_once(1.0)
    return eng


#: SID members a booted engine legitimately does not write within the first
#: 20 ticks. Each needs a reason.
UNPRODUCED_OK: dict[int, str] = {}


def test_every_sid_member_is_produced_by_a_booted_engine(ticked_engine):
    missing = []
    for pid in sorted(HK_IDS):
        if pid in ticked_engine.params or pid in UNPRODUCED_OK:
            continue
        missing.append(f"0x{pid:04X} {PARAM_BY_ID.get(pid, {}).get('name', '?')}")
    assert not missing, (
        f"{len(missing)} HK SID members are never written by the engine, so "
        f"they downlink as 0: {missing}")


def test_on_demand_parameters_are_produced(ticked_engine):
    """An on-demand parameter that nothing writes returns 0.0 from S20.3.

    That is worse than an error, because 0.0 is *plausible*: `sat.mode`
    decoded as 0 means SAFE, and it read SAFE throughout every nominal pass
    of every exercise because nothing assigned it (brief 08 finding 12). The
    gate is zero. A parameter the model genuinely does not compute must be
    declared `stub: true` — a visible statement in the catalogue instead of a
    silent one on the wire.
    """
    missing = []
    for p in PARAMS:
        pid = _pid(p["id"])
        if not p.get("on_demand") or p.get("stub"):
            continue
        if pid not in ticked_engine.params:
            missing.append(f"0x{pid:04X} {p.get('name')}")
    assert not missing, (
        f"{len(missing)} on-demand parameters are never written by a booted "
        f"engine, so S20.3 returns a plausible 0.0 for them. Either write "
        f"them or mark them `stub: true`: {sorted(missing)}")


def test_stub_parameters_are_declared_not_discovered(ticked_engine):
    """The reverse ratchet: a `stub: true` parameter the engine now *does*
    write is a stale declaration, and it hides the next real stub."""
    stale = []
    for p in PARAMS:
        pid = _pid(p["id"])
        if p.get("stub") and pid in ticked_engine.params:
            stale.append(f"0x{pid:04X} {p.get('name')}")
    assert not stale, (
        f"parameters marked `stub: true` that the engine now produces — drop "
        f"the flag: {stale}")


def test_the_stub_count_does_not_grow():
    """A stub is an honest placeholder, not a parking space."""
    stubs = [p["name"] for p in PARAMS if p.get("stub")]
    assert len(stubs) <= 20, (
        f"{len(stubs)} parameters are declared stubs. Adding one is allowed; "
        f"doing it silently is not. Current set: {sorted(stubs)}")


# ── 3. Limits and monitors name reachable parameters ────────────────────

def test_every_limited_parameter_is_reachable():
    limits = _load("mcs/limits.yaml")["limits"]
    bad = []
    for l in limits:
        pid = _pid(l["param_id"])
        p = PARAM_BY_ID.get(pid)
        if p is None:
            bad.append(f"0x{pid:04X} (not in the catalogue)")
        elif not (pid in HK_IDS or p.get("on_demand")):
            bad.append(f"0x{pid:04X} {p.get('name')} (unreachable)")
    assert not bad, f"limits.yaml bands unreachable parameters: {bad}"


def test_rate_ladder_parameters_are_in_the_ttc_hk_sid():
    """0x0539-0x053C — active vs commanded tier (brief 08 finding 13).

    They carried limit bands and were S20-poll only, so the routine TTC
    housekeeping could not show a gate-demoted link.
    """
    missing = [f"0x{pid:04X}" for pid in (0x0539, 0x053A, 0x053B, 0x053C)
               if pid not in HK_IDS]
    assert not missing, f"rate ladder parameters not in any HK SID: {missing}"


def test_every_s12_monitored_parameter_is_reachable():
    s12 = _load("monitoring/s12_definitions.yaml").get("monitors") \
        or _load("monitoring/s12_definitions.yaml").get("definitions") or []
    bad = []
    for d in s12:
        raw = d.get("param_id", d.get("parameter_id"))
        if raw is None:
            continue
        pid = _pid(raw)
        p = PARAM_BY_ID.get(pid)
        if p is None:
            bad.append(f"0x{pid:04X} (not in the catalogue)")
        elif not (pid in HK_IDS or p.get("on_demand")):
            bad.append(f"0x{pid:04X} {p.get('name')} (unreachable)")
    assert not bad, f"S12 monitors on unreachable parameters: {bad}"
