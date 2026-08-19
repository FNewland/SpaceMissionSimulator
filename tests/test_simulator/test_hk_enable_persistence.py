"""HK enable state must only change when something has the right to change it.

Field report: "when housekeeping SIDs are enabled, they seem to disable…
but this would affect delayed TM".

Reproduced: `Engine.apply_phase_change()` re-applied
`simulation.yaml housekeeping.default_enabled_sids` on EVERY application-mode
phase transition, not just on a boot. So the last card of LEOP
(`SYS_SET_MISSION_PHASE(5)`) and the last card of commissioning
(`SYS_SET_MISSION_PHASE(6)`) each silently switched off every SID the operator
had just commissioned — no event, no display, and therefore no periodic HK and
nothing entering the S15 HK store for those SIDs. The first evidence an
operator got was a hole in the next dump.

Two separate things are pinned here:

* a mission-phase DECLARATION is not a boot and must not touch the HK enable
  set (the defect);
* a boot / bootloader entry legitimately DOES reset it — and must say so, with
  S5 event 0x0056 and a machine-readable record on the state endpoint, so the
  gap it creates in the delayed-TM store is explained rather than mysterious.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
sys.path.insert(0, str(REPO / "tools"))

from smo_common.protocol.ecss_packet import (  # noqa: E402
    build_tc_packet, decommutate_packet,
)
from smo_simulator.engine import SimulationEngine  # noqa: E402

#: Non-default SIDs an operator brings up during commissioning.
COMMISSIONED = [2, 3, 4, 5, 6, 9, 12]

EV_HK_ENABLE_SET_RESET = 0x0056


def _nominal_engine(phase: int = 5) -> SimulationEngine:
    """An engine with the application running, in `phase`, links up."""
    eng = SimulationEngine(str(CONFIG_DIR))
    eng._spacecraft_phase = phase
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng._fdir_enabled = False
    obdh = eng.subsystems["obdh"]._state
    obdh.sw_image = 1
    obdh.watchdog_armed = False
    eps = eng.subsystems["eps"]._state
    for line in eps.power_lines:
        eps.power_lines[line] = True
    ttc = eng.subsystems["ttc"]._state
    ttc.frame_sync = ttc.carrier_lock = ttc.bit_sync = True
    ttc.pa_on = ttc.antenna_deployed = True
    ttc.beacon_mode = False
    for sid in eng._tm_storage._stores:
        eng._tm_storage.enable_store(sid)
    # The harness jumps straight to an application phase, so put the HK enable
    # set where a real boot would have left it (the configured defaults) and
    # forget the cold-boot reset — the operator has not commanded anything yet.
    eng._apply_default_hk_enable()
    eng._hk_enable_reset = None
    return eng


class _Uplink:
    """Send real TC packets through the engine's own dispatch path."""

    def __init__(self, eng: SimulationEngine):
        self.eng = eng
        self.seq = 0

    def send(self, service: int, subtype: int, data: bytes = b"") -> None:
        self.seq += 1
        self.eng._dispatch_tc(build_tc_packet(
            apid=1, service=service, subtype=subtype, data=data,
            seq_count=self.seq))

    def hk_enable(self, sid: int) -> None:
        self.send(3, 5, struct.pack(">H", sid))

    def hk_disable(self, sid: int) -> None:
        self.send(3, 6, struct.pack(">H", sid))

    def set_phase(self, phase: int, flags: int = 0) -> None:
        self.send(8, 1, bytes([120, phase, flags]))

    def run(self, seconds: int) -> None:
        for _ in range(seconds):
            self.eng._tick_once(1.0)


def _enabled(eng: SimulationEngine) -> list[int]:
    return sorted(sid for sid in COMMISSIONED if eng._hk_enabled.get(sid))


def _stored_sid_counts(eng: SimulationEngine) -> dict[int, int]:
    """{sid: packets} for the S3.25 HK packets sitting in the S15 HK store."""
    counts: dict[int, int] = {}
    for pkt in eng._tm_storage._stores.get(1, []):
        parsed = decommutate_packet(pkt)
        sec = getattr(parsed, "secondary", None)
        if not sec or sec.service != 3 or sec.subtype != 25:
            continue
        if len(parsed.data_field) < 2:
            continue
        sid = struct.unpack(">H", parsed.data_field[:2])[0]
        counts[sid] = counts.get(sid, 0) + 1
    return counts


# ── the defect: a phase declaration is not a boot ────────────────────────

def test_ground_phase_declaration_keeps_the_commanded_hk_sids():
    """SYS_SET_MISSION_PHASE(6) must not touch the HK enable set."""
    eng = _nominal_engine(phase=5)
    up = _Uplink(eng)
    for sid in COMMISSIONED:
        up.hk_enable(sid)
    assert _enabled(eng) == COMMISSIONED

    up.set_phase(6)
    up.run(3)

    assert eng._spacecraft_phase == 6, "the phase declaration was refused"
    assert _enabled(eng) == COMMISSIONED, (
        "SYS_SET_MISSION_PHASE reverted the operator's HK enable set to the "
        "power-up defaults — the OBSW never rebooted")


def test_every_application_phase_step_keeps_the_enable_set():
    """LEOP -> COMMISSIONING -> NOMINAL, and a FORCEd step back."""
    eng = _nominal_engine(phase=4)
    for sid in COMMISSIONED:
        eng.set_hk_enabled(sid, True)
    for target in (5, 6):
        eng.apply_phase_change(target)
        assert _enabled(eng) == COMMISSIONED, f"phase {target} cleared the set"
    eng.apply_phase_change(5)  # forced backwards
    assert _enabled(eng) == COMMISSIONED


def test_operator_disable_still_works():
    """The fix must not make TC(3,6) HK_DISABLE a no-op."""
    eng = _nominal_engine(phase=6)
    up = _Uplink(eng)
    up.hk_enable(5)
    assert eng._hk_enabled[5] is True
    up.hk_disable(5)
    assert eng._hk_enabled[5] is False


# ── the delayed-TM consequence ───────────────────────────────────────────

def test_s15_store_keeps_receiving_across_a_phase_declaration():
    """The S15 HK store must not go quiet because the phase label changed."""
    eng = _nominal_engine(phase=5)
    up = _Uplink(eng)
    for sid in COMMISSIONED:
        up.hk_enable(sid)
    up.run(300)
    before = _stored_sid_counts(eng)
    assert all(before.get(sid, 0) > 0 for sid in COMMISSIONED), before

    up.set_phase(6)
    up.run(300)
    after = _stored_sid_counts(eng)

    for sid in COMMISSIONED:
        assert after.get(sid, 0) > before.get(sid, 0), (
            f"SID {sid} stopped feeding the S15 HK store after the phase "
            f"declaration ({before.get(sid, 0)} -> {after.get(sid, 0)})")


def test_disabling_a_sid_stops_its_delayed_tm_and_the_gap_is_visible():
    """A disabled SID stores nothing, and the store's bookkeeping is honest.

    This is the CORRECT half: no periodic generation means nothing to store.
    What must never happen is the store pretending otherwise.
    """
    eng = _nominal_engine(phase=6)
    up = _Uplink(eng)
    up.hk_enable(2)
    up.run(120)
    mid = _stored_sid_counts(eng)
    status_mid = next(s for s in eng._tm_storage.get_status() if s["id"] == 1)

    up.hk_disable(2)
    up.run(120)
    end = _stored_sid_counts(eng)
    status_end = next(s for s in eng._tm_storage.get_status() if s["id"] == 1)

    assert end.get(2, 0) == mid.get(2, 0), (
        "a disabled SID must contribute nothing to the S15 store")
    # The store keeps counting the SIDs that are still on, and its own
    # oldest/newest bracket really do bracket the packets it holds.
    assert status_end["count"] > status_mid["count"]
    assert status_end["newest_ts"] > status_mid["newest_ts"]
    assert status_end["oldest_ts"] <= status_end["newest_ts"]
    assert status_end["wrap_count"] == 0, "no wrap should be claimed"


def test_dump_across_a_sid_disable_window_shows_a_gap_not_continuity(tmp_path):
    """A dump spanning a SID-disable window must read as a HOLE.

    The delayed-TM viewer plots a decoded series as a line. Before this, the
    disabled stretch was simply absent from the sample list, so the chart
    joined the last sample before the outage to the first sample after it —
    fabricated continuity across telemetry the spacecraft never produced.
    `analyse_dumps` now reports the gap explicitly and the plot breaks there.
    """
    import delayed_tm_viewer as dtv
    from tm_report import load_catalogs

    eng = _nominal_engine(phase=6)
    up = _Uplink(eng)
    up.hk_enable(2)
    up.run(120)          # SID 2 arriving
    up.hk_disable(2)
    up.run(400)          # …silent (much more than 3 x its 4 s cadence)
    up.hk_enable(2)
    up.run(120)          # …and back

    dump = tmp_path / "dump_sid01_20260101T000000Z.bin"
    with dump.open("wb") as fh:
        for pkt in eng._tm_storage._stores[1]:
            fh.write(struct.pack(">I", len(pkt)))
            fh.write(pkt)

    params_by_id, hk_by_sid, hk_plist = load_catalogs()
    result = dtv.analyse_dumps([dump], params_by_id, hk_plist,
                               hk_structs=hk_by_sid)

    sid2 = next(h for h in result["hk"] if h["sid"] == 2)
    assert sid2["gaps"], (
        "the dump spans a 400 s SID-2 outage and the analysis reports no "
        "gap — the viewer would draw it as continuous data")
    longest = max(g["duration_s"] for g in sid2["gaps"])
    assert longest > 300, f"gap understated: {sid2['gaps']}"
    # Every parameter of the SID carries the same gap list, because that is
    # what each plotted series has to break across.
    assert all(p["gaps"] == sid2["gaps"] for p in sid2["params"])
    # No sample is invented inside the hole.
    for param in sid2["params"]:
        for gap in sid2["gaps"]:
            inside = [c for c, _ in param["series"]
                      if gap["start_cuc"] < c < gap["end_cuc"]]
            assert not inside, (
                f"{len(inside)} fabricated sample(s) inside the outage")
    assert "SID 2" in result["gaps"]["summary"]

    # …and a SID that never stopped is NOT accused of having a gap.
    sid1 = next(h for h in result["hk"] if h["sid"] == 1)
    assert not sid1["gaps"], f"false gap on an uninterrupted SID: {sid1['gaps']}"


# ── the visibility half: a boot legitimately resets, and says so ─────────

def test_obc_reboot_resets_the_enable_set_and_raises_an_event():
    eng = _nominal_engine(phase=6)
    for sid in COMMISSIONED:
        eng.set_hk_enabled(sid, True)

    events: list[dict] = []
    original = eng._emit_event
    eng._emit_event = lambda ev: (events.append(dict(ev)), original(ev))[1]

    eng.subsystems["obdh"].handle_command({"command": "obc_reboot"})
    for _ in range(3):
        eng._tick_once(1.0)

    assert _enabled(eng) == [], (
        "the bootloader must run beacon-only HK — that part is correct")
    resets = [e for e in events
              if e.get("event_id") == EV_HK_ENABLE_SET_RESET]
    assert resets, (
        "the HK enable set was wiped by a reboot with no S5 event — the "
        "operator has no way to connect the coming dump gap to the reboot")
    desc = resets[0]["description"]
    for sid in COMMISSIONED:
        assert str(sid) in desc, f"event does not name SID {sid}: {desc}"
    assert resets[0]["severity"] >= 3


def test_reset_reason_is_published_on_the_state_endpoint():
    eng = _nominal_engine(phase=6)
    for sid in COMMISSIONED:
        eng.set_hk_enabled(sid, True)
    assert eng._build_state_summary().get("hk_enable_reset") in (None, {})

    eng.subsystems["obdh"].handle_command({"command": "obc_reboot"})
    for _ in range(3):
        eng._tick_once(1.0)

    record = eng._build_state_summary()["hk_enable_reset"]
    assert record and record["reason"]
    assert set(COMMISSIONED).issubset(record["sids_disabled"])
    assert record["enabled_now"], "the beacon SID must still be on"
    # The instructor snapshot carries it too.
    assert eng.get_instructor_snapshot()["hk_enable_reset"] == record


def test_an_operator_hk_disable_is_not_reported_as_a_reset():
    """TC(3,6) is the operator's own doing — it must not raise 0x0056."""
    eng = _nominal_engine(phase=6)
    up = _Uplink(eng)
    up.hk_enable(2)
    events: list[dict] = []
    original = eng._emit_event
    eng._emit_event = lambda ev: (events.append(dict(ev)), original(ev))[1]
    up.hk_disable(2)
    up.run(2)
    assert not [e for e in events
                if e.get("event_id") == EV_HK_ENABLE_SET_RESET]
    assert eng._build_state_summary().get("hk_enable_reset") in (None, {})


def test_reset_event_is_catalogued():
    catalog = yaml.safe_load(
        (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())["events"]
    ids = {e["id"] if isinstance(e["id"], int) else int(str(e["id"]), 0)
           for e in catalog}
    assert EV_HK_ENABLE_SET_RESET in ids, (
        "S5 0x0056 is emitted but not catalogued — the console would show a "
        "bare event id")


def test_breakpoint_round_trip_preserves_the_enable_set(tmp_path):
    """Regression guard: a breakpoint restore must not lose commissioning."""
    from smo_simulator.breakpoints import BreakpointManager

    eng = _nominal_engine(phase=6)
    for sid in COMMISSIONED:
        eng.set_hk_enabled(sid, True)
    path = tmp_path / "bp.json"
    BreakpointManager(eng).save(name="t", path=path)

    for sid in COMMISSIONED:
        eng.set_hk_enabled(sid, False)
    assert _enabled(eng) == []

    BreakpointManager(eng).load(path=path)
    assert _enabled(eng) == COMMISSIONED
