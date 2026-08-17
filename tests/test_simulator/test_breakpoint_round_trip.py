"""Plan A-4 — complete state round-trip through breakpoints.

Boot an engine into a rich state (application phase, S11 schedule loaded,
failures injected, S12 enable toggled, dump in progress, edge detectors hot
past an AOS and an eclipse entry), save, perturb, load, and deep-compare a
canonical state dump against the one captured at save time. Also: a fresh
engine restored from the snapshot must NOT emit spurious AOS/eclipse/
mode-change events on its first tick, the S11 schedule must survive, a
failing component save must fail the WHOLE save, and the Stateful registry
must be complete.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from smo_simulator.engine import SimulationEngine
from smo_simulator.breakpoints import BreakpointManager, BreakpointSaveError

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

# A syntactically valid CCSDS TC wrapper is not required by TCScheduler.insert
# (identity decommutation is best-effort) — any bytes round-trip.
FAKE_TC = bytes(range(24))

# Event ids that must NOT fire spuriously on the first tick after a load.
TRANSITION_EVENT_IDS = {
    0x0001,  # AOS
    0x0002,  # LOS
    0x0010,  # eclipse entry
    0x0011,  # sunlight entry
    0x0200,  # AOCS mode change
    0x0300,  # OBC mode change
    0x0600, 0x0601, 0x0602,  # payload/imaging mode changes
    0x0050, 0x0051, 0x0052, 0x0054,  # phase-machine transitions
}


def _drain_events(eng) -> list[dict]:
    out = []
    while True:
        try:
            out.append(eng.event_queue.get_nowait())
        except Exception:
            break
    return out


def _make_engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    return SimulationEngine(CONFIG_DIR, speed=1.0)


def _boot_rich_engine() -> SimulationEngine:
    """Engine in application phase, past first AOS (~720 s) and eclipse entry
    (~1280 s), with S11/S12/S19/failure/dump state all non-default."""
    eng = _make_engine()
    # Boot the OBC application so the phase machine leaves the bootloader.
    eng.subsystems["obdh"]._state.sw_image = 1
    for _ in range(3):
        eng._tick_once(1.0)
    assert eng._spacecraft_phase >= 4

    # Run past the first AOS and into the first eclipse (sub-stepped ticks).
    for _ in range(23):
        eng._tick_once(60.0)
    assert eng.orbit.state.in_eclipse, "expected to be inside first eclipse"
    assert eng._prev_in_eclipse, "eclipse edge detector should be hot"

    # S11: load a time-tagged command far in the future.
    eng._tc_scheduler.insert(eng._get_cuc_time() + 500_000, FAKE_TC)

    # Failures: one gradual, one intermittent (exercises flicker RNG state).
    eng._failure_manager.inject("eps", "sa_degraded", magnitude=0.5,
                                onset="gradual", duration_s=0.0)
    eng._failure_manager.inject("ttc", "tx_intermittent", magnitude=1.0,
                                onset="intermittent")

    # S12: toggle one shipped definition's enable flag.
    disp = eng._dispatcher
    assert disp._s12_definitions, "expected boot-loaded S12 definitions"
    mon_id = sorted(disp._s12_definitions)[0]
    disp._s12_definitions[mon_id]["enabled"] = False

    # S19: disable one shipped event-action rule.
    if disp._s19_enabled_ids:
        disp._s19_enabled_ids.discard(sorted(disp._s19_enabled_ids)[0])

    # Settle a few more seconds so the injected state is exercised.
    for _ in range(10):
        eng._tick_once(1.0)

    # S15 dump in progress + RT backlog. Added AFTER the settle ticks — the
    # dump pacer would (correctly) drain this tiny backlog within a tick.
    eng._dump_pending.append((b"\x11\x22\x33\x44", 1))
    eng._dump_active_stores.add(1)
    eng._dump_byte_budget = 123.5

    # Phase D: propulsion armed MID-BURN — the richest prop state there is
    # (loaded burn, partial dv integrated, valves open, catbed hot). Set
    # directly rather than through the arm gate; the round-trip contract is
    # about state completeness, not interlocks.
    ps = eng.subsystems["prop"]._state
    ps.state = 2                      # FIRING
    ps.burn_status = 2
    ps.fcv_open = True
    ps.burn_loaded = True
    ps.burn_dv_cmd_m_s = 0.85
    ps.burn_time_cmd_s = 31.9
    ps.burn_elapsed_s = 12.5
    ps.burn_dv_mag_m_s = 0.33
    ps.burn_dv_at_m_s = 0.329
    ps.burn_dv_lat_m_s = 0.004
    ps.m_prop_kg = 0.398
    ps.catbed_temp_c = 214.0
    return eng


def _canonical(state: dict) -> dict:
    """Snapshot minus save-time metadata (wall-clock volatile)."""
    s = copy.deepcopy(state)
    s.pop("name", None)
    s.pop("saved_at", None)      # wall-clock session artefact
    s.pop("timestamp", None)     # pre-Part-C name for saved_at
    return s


def test_save_perturb_load_deep_equals():
    eng = _boot_rich_engine()
    snap = BreakpointManager(eng).save(name="rich")
    reference = _canonical(snap)

    # Perturb thoroughly.
    for _ in range(50):
        eng._tick_once(1.0)
    eng._failure_manager.inject("aocs", "gyro_drift", magnitude=1.0,
                                onset="step")
    eng._tc_scheduler.delete_all()
    disp = eng._dispatcher
    mon_id = sorted(disp._s12_definitions)[0]
    disp._s12_definitions[mon_id]["enabled"] = True
    eng._dump_pending.clear()
    eng._dump_active_stores.clear()

    assert _canonical(BreakpointManager(eng).save(name="perturbed")) \
        != reference, "perturbation did not change the canonical state dump"

    # Load and re-dump: must deep-equal the reference.
    assert BreakpointManager(eng).load(state=snap)
    after = _canonical(BreakpointManager(eng).save(name="after"))
    assert after == reference, "state dump after load differs from save time"


def test_s11_schedule_survives_load():
    eng = _boot_rich_engine()
    before = eng._tc_scheduler.list_commands()
    assert len(before) == 1
    snap = BreakpointManager(eng).save(name="s11")

    eng._tc_scheduler.delete_all()  # the pre-A-4 effect of any load
    assert eng._tc_scheduler.list_commands() == []

    assert BreakpointManager(eng).load(state=snap)
    after = eng._tc_scheduler.list_commands()
    assert after == before, "S11 time-tagged schedule did not survive the load"
    # The embedded packet itself must be byte-identical (it is what executes).
    cmd_id = after[0]["id"]
    assert eng._tc_scheduler._commands[cmd_id]["packet"] == FAKE_TC


def test_fresh_engine_restore_no_spurious_events():
    """Restore into a FRESH engine (the restart workflow): the first tick must
    not re-fire AOS/eclipse/mode-change/phase events (brief 08 B-2 — the
    _prev_* edge detectors used to be lost)."""
    rich = _boot_rich_engine()
    snap = BreakpointManager(rich).save(name="xfer")

    fresh = _make_engine()  # boots in bootloader, detectors cold
    assert BreakpointManager(fresh).load(state=snap)

    # The restored engine carries the rich state (checked BEFORE ticking —
    # the first tick legitimately advances it, e.g. drains the tiny dump).
    assert fresh._spacecraft_phase == rich._spacecraft_phase
    assert len(fresh._tc_scheduler.list_commands()) == 1
    assert {f["id"] for f in fresh._failure_manager.active_failures()} == \
           {f["id"] for f in rich._failure_manager.active_failures()}
    assert fresh._dump_active_stores == {1}
    assert fresh._prev_in_eclipse is True

    _drain_events(fresh)  # discard anything from boot

    fresh._tick_once(1.0)
    fired = [ev for ev in _drain_events(fresh)
             if ev.get("event_id") in TRANSITION_EVENT_IDS]
    assert fired == [], (
        f"spurious transition events on first tick after load: "
        f"{[hex(ev['event_id']) for ev in fired]}")


# ── No aliasing between a loaded snapshot and live state (G2-f) ─────

def _deep_diff(a, b, path=""):
    """Human-readable list of differences between two nested structures."""
    if type(a) is not type(b):
        return [f"{path}: type {type(a).__name__} -> {type(b).__name__}"]
    out = []
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b), key=repr):
            if k not in a:
                out.append(f"{path}.{k}: appeared ({b[k]!r})")
            elif k not in b:
                out.append(f"{path}.{k}: disappeared")
            else:
                out += _deep_diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, (list, tuple)):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} -> {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                out += _deep_diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: {a!r} -> {b!r}")
    return out


def test_loaded_snapshot_is_not_mutated_by_later_ticks():
    """A loaded breakpoint dict is inert (G2-f).

    Phase C found AOCS `set_state` assigning the snapshot's own list objects
    into live state, after which the model corrupted the stored snapshot on
    every tick. The same plain `setattr` loop lived in EPS, TCS, TTC, OBDH,
    payload and prop (`power_lines`, `device_states`, `line_currents`,
    `sa_panel_currents`, `image_catalog`, `_temp_history`), in the FDIR
    procedure executor's `steps` and in `BreakpointManager.load`'s
    `_fdir_triggered`.

    This is deliberately not "save, perturb, load, re-save and compare":
    that comparison passes under aliasing, because the snapshot drifts in
    step with the live state it is aliased to. The snapshot is compared with
    a copy of ITSELF taken at load time.
    """
    eng = _boot_rich_engine()
    snap = BreakpointManager(eng).save(name="alias")
    pristine = copy.deepcopy(snap)

    assert BreakpointManager(eng).load(state=snap)
    # Long enough to move every integrator, cross an eclipse boundary and
    # run the FDIR/procedure machinery.
    for _ in range(60):
        eng._tick_once(30.0)

    diff = _deep_diff(pristine, snap)
    assert not diff, (
        "ticks after the load mutated the loaded snapshot — live state is "
        "aliased onto it:\n  " + "\n  ".join(diff[:40]))


def test_two_engines_restored_from_one_snapshot_do_not_share_state():
    """The other half of the aliasing failure mode (G2-f)."""
    eng = _boot_rich_engine()
    snap = BreakpointManager(eng).save(name="shared")

    a, b = _make_engine(), _make_engine()
    assert BreakpointManager(a).load(state=copy.deepcopy(snap))
    assert BreakpointManager(b).load(state=copy.deepcopy(snap))
    # Same snapshot object this time — the containers must still be distinct.
    assert BreakpointManager(a).load(state=snap)
    assert BreakpointManager(b).load(state=snap)

    for name in ("eps", "ttc", "payload", "obdh", "tcs", "aocs"):
        sa, sb = a.subsystems[name]._state, b.subsystems[name]._state
        for field, va in list(vars(sa).items()):
            if not isinstance(va, (dict, list, set)):
                continue
            vb = getattr(sb, field, None)
            assert va is not vb, (
                f"{name}._state.{field} is the SAME object in two engines "
                f"restored from one snapshot")


def test_failed_component_save_fails_whole_save(tmp_path, monkeypatch):
    """No more silently-incomplete breakpoints (brief 08 C-3)."""
    eng = _make_engine()
    for _ in range(3):
        eng._tick_once(1.0)

    def boom():
        raise RuntimeError("component cannot serialise")

    monkeypatch.setattr(eng._scenario_engine, "get_state", boom)
    out = tmp_path / "broken.json"
    with pytest.raises(BreakpointSaveError):
        BreakpointManager(eng).save(name="broken", path=out)
    assert not out.exists(), "an incomplete breakpoint file was written"


def test_register_stateful_rejects_stateless_component():
    eng = _make_engine()

    class NoState:
        def tick(self, dt):
            pass

    with pytest.raises(TypeError):
        eng.register_stateful("bogus", NoState())


# ── Registry completeness ───────────────────────────────────────────

# Engine attributes holding objects that legitimately stay OUTSIDE the
# Stateful registry. Every entry needs a reason.
REGISTRY_ALLOWLIST = {
    "orbit": "SGP4 is stateless given a UTC time; the propagator clock is "
             "captured as the breakpoint's top-level orbit_utc field",
    # `_fault_propagator` and `_procedure_executor` were allowlisted here as
    # "never run in production". Phase G wired both into `_tick_fdir_advanced`,
    # so they now hold cross-tick state and are registered Stateful; the
    # allowlist entries are gone rather than widened.
    "tm_builder": "sequence counters + APID are serialised inside the "
                  "engine component state (tm_apid / tm_seq_count)",
}

# Components that must ALWAYS be registered, by engine attribute name.
REQUIRED_REGISTERED = [
    "_tc_scheduler", "_failure_manager", "_scenario_engine", "_dispatcher",
    "_load_shedding", "_tm_storage", "_obcp_manager", "_memory",
    # Phase G: live FDIR — active faults, recovery escalation levels and
    # running recovery procedures are all cross-tick state.
    "_fault_propagator", "_procedure_executor",
]


def test_registry_completeness():
    eng = _make_engine()
    registered = {id(c) for c in eng.stateful_components().values()}

    # 1. Everything with a tick() method reachable from the engine's attrs
    #    (the things the sim loop advances) is registered or allowlisted.
    for attr, obj in vars(eng).items():
        if obj is None or isinstance(
                obj, (int, float, str, bool, bytes, dict, list, set,
                      frozenset, tuple)):
            continue
        if not callable(getattr(obj, "tick", None)):
            continue
        if id(obj) in registered:
            continue
        assert attr in REGISTRY_ALLOWLIST, (
            f"engine.{attr} ({type(obj).__name__}) has a tick() method but "
            f"is neither registered Stateful nor allowlisted with a reason")

    # 2. Subsystem models are all registered.
    for name, model in eng.subsystems.items():
        assert id(model) in registered, f"model {name} not registered"

    # 3. The known component list is registered.
    for attr in REQUIRED_REGISTERED:
        comp = getattr(eng, attr)
        assert id(comp) in registered, f"engine.{attr} not registered"

    # 4. The engine's own (engine-level) state is registered.
    assert id(eng) in registered

    # 5. Everything registered actually implements the protocol.
    from smo_common.state import Stateful
    for name, comp in eng.stateful_components().items():
        assert isinstance(comp, Stateful), f"{name} violates Stateful"
