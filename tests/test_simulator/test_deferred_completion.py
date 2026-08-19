"""S1.7 means "the effect happened", not "the command was accepted".

THE DEFECT (owner-reported, 2026-08). `OBC_BOOT_APP` (S8.1 func 55) answered
S1.7 "execution complete" the instant it was accepted, while the OBDH model
only flips `sw_image` to APPLICATION after a 10 s CRC verification window.
The operator was told the boot had finished before it had started, sent their
next command inside that window, and got S1.2 "not in the bootloader command
subset" from a spacecraft that had just claimed to be running the
application — with no telemetry anywhere that said a boot was in flight.

OWNER RULING: S1.7 waits for completion for ANY command whose effect takes
more than a second. This file covers the mechanism and every command deferred
under it.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine
from smo_simulator.models.obdh_basic import (
    BOOT_STATE_APPLICATION, BOOT_STATE_BOOTLOADER, BOOT_STATE_FAILED,
    BOOT_STATE_VERIFYING, COMPLETION_BOOT_APP, SW_APPLICATION, SW_BOOTLOADER,
)
from smo_simulator.service_dispatch import DispatchResult

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = str(REPO / "configs" / "eosat1")

APID = 100
FUNC_BOOT_APP = 55
FUNC_SET_BEACON_MODE = 70
FUNC_MEMORY_SCRUB = 60
FUNC_GYRO_CAL = 13
FUNC_DEPLOY_WING = 81  # EPS_DEPLOY_WING: 0=+Y, 1=-Y, 2=both


# ── helpers ─────────────────────────────────────────────────────────────

@pytest.fixture
def eng():
    engine = SimulationEngine(CONFIG_DIR)
    # Keep a station permanently in view so the verification reports reach
    # the TM queue instead of being held by the downlink gate — the same
    # instructor override the LEOP fly-through uses.
    engine._override_passes = True
    engine.params[0x05FF] = 1
    engine._in_contact = True
    return engine


def drain(eng) -> list:
    """Decommutate everything currently on the TM queue."""
    out = []
    while not eng.tm_queue.empty():
        raw = eng.tm_queue.get_nowait()
        pkt = decommutate_packet(raw)
        if pkt is not None and pkt.secondary is not None:
            out.append(pkt)
    return out


def send(eng, service: int, subtype: int, data: bytes, seq: int) -> list:
    eng._dispatch_tc(build_tc_packet(apid=APID, service=service,
                                     subtype=subtype, data=data,
                                     seq_count=seq))
    return drain(eng)


def s1(pkts, subtype: int) -> list:
    return [p for p in pkts
            if p.secondary.service == 1 and p.secondary.subtype == subtype]


def s1_seqs(pkts, subtype: int) -> list[int]:
    """TC sequence counts carried by the S1.<subtype> reports in `pkts`.

    S1.1/1.2/1.7/1.8 carry a u32 request id (apid << 14 | seq); S1.3 carries
    a bare u16 sequence count (`generate_s1_reports`).
    """
    out = []
    for p in s1(pkts, subtype):
        if subtype == 3:
            out.append(struct.unpack('>H', p.data_field[:2])[0])
        else:
            out.append(struct.unpack('>I', p.data_field[:4])[0] & 0x3FFF)
    return out


def run(eng, seconds: float, dt: float = 1.0) -> list:
    pkts = []
    for _ in range(int(round(seconds / dt))):
        eng._tick_once(dt)
        pkts.extend(drain(eng))
    return pkts


def in_application(eng, power_all: bool = False) -> None:
    """Boot the OBC into the application image without going through S8.1."""
    eng.apply_phase_change(6)
    obdh = eng.subsystems["obdh"]
    obdh._state.sw_image = SW_APPLICATION
    obdh._state.boot_state = BOOT_STATE_APPLICATION
    obdh._state.boot_app_pending = False
    if power_all:
        # The acceptance-time power gate refuses commands to unpowered units.
        eps = eng.subsystems["eps"]
        for line in eps._state.power_lines:
            eps._state.power_lines[line] = True
        eng._tick_once(1.0)
        drain(eng)


def failure_reason(pkt) -> str:
    """The failure notice appended after the >IH prefix of an S1.2 / S1.8."""
    body = pkt.data_field[6:]
    if not body:
        return ""
    return body[1:1 + body[0]].decode("utf-8", "replace")


# ── The contract object ─────────────────────────────────────────────────

def test_dispatch_result_carries_the_deferral_beside_the_failure():
    plain = DispatchResult()
    assert not plain.deferred and plain.deferred_key == ""

    d = DispatchResult(deferred_key="x.y", deferred_timeout_s=30.0)
    assert d.deferred and d.deferred_key == "x.y"
    assert d.deferred_timeout_s == 30.0

    # Mutually exclusive with a failure: a refused command started nothing.
    both = DispatchResult(deferred_key="x.y", failure_kind="execution")
    assert not both.deferred


def test_boot_app_reports_deferred_not_complete(eng):
    res = eng._dispatcher.dispatch(8, 1, bytes([FUNC_BOOT_APP]), None)
    assert res.failure_kind == ""
    assert res.deferred
    assert res.deferred_key == COMPLETION_BOOT_APP


# ── OBC_BOOT_APP — the reported defect ──────────────────────────────────

def test_boot_app_s17_waits_for_the_crc_window(eng):
    obdh = eng.subsystems["obdh"]
    assert obdh._state.sw_image == SW_BOOTLOADER

    at_dispatch = send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=11)
    assert s1_seqs(at_dispatch, 1) == [11], "S1.1 acceptance still immediate"
    assert s1_seqs(at_dispatch, 3) == [11], "S1.3 execution start still immediate"
    assert not s1(at_dispatch, 7), \
        "S1.7 claimed the boot was complete before the CRC window even opened"

    # Five seconds in: still nothing, and the model is still in the window.
    mid = run(eng, 5.0)
    assert not s1(mid, 7)
    assert obdh._state.sw_image == SW_BOOTLOADER
    assert obdh._state.boot_state == BOOT_STATE_VERIFYING

    late = run(eng, 8.0)
    assert obdh._state.sw_image == SW_APPLICATION
    assert s1_seqs(late, 7) == [11], \
        "S1.7 must arrive on completion, against the ORIGINAL sequence count"
    assert not s1(late, 8)


def test_boot_crc_failure_produces_s18_not_silence(eng):
    obdh = eng.subsystems["obdh"]
    obdh._state.boot_image_corrupt = True

    send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=12)
    pkts = run(eng, 13.0)

    assert not s1(pkts, 7), "a failed boot must never report execution complete"
    fails = s1(pkts, 8)
    assert fails, "BOOT_FAILURE used to be silent on the verification service"
    assert s1_seqs(pkts, 8) == [12]
    assert "CRC" in failure_reason(fails[0])
    assert obdh._state.sw_image == SW_BOOTLOADER
    assert obdh._state.boot_state == BOOT_STATE_FAILED


def test_boot_completion_raises_an_s5_event(eng):
    send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=13)
    pkts = run(eng, 13.0)
    events = [p for p in pkts if p.secondary.service == 5]
    ids = [struct.unpack('>H', p.data_field[:2])[0] for p in events]
    assert 0x0310 in ids, "BOOT_COMPLETE (0x0310) must reach the event log"


def test_boot_progress_telemetry_exists_while_the_window_is_open(eng):
    send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=14)
    eng._tick_once(1.0)
    assert eng.params[0x0A15] == BOOT_STATE_VERIFYING
    assert 0.0 < eng.params[0x0A16] <= 10.0
    run(eng, 12.0)
    assert eng.params[0x0A15] == BOOT_STATE_APPLICATION
    assert eng.params[0x0A16] == 0.0


def test_boot_state_is_in_hk_sid_4():
    import yaml
    hk = yaml.safe_load(
        (REPO / "configs" / "eosat1" / "telemetry"
         / "hk_structures.yaml").read_text())
    sid4 = next(s for s in hk["structures"] if int(s["sid"]) == 4)
    ids = {int(p["param_id"]) for p in sid4["parameters"]}
    assert 0x0A15 in ids and 0x0A16 in ids


# ── The owner's exact sequence ──────────────────────────────────────────

def test_owner_sequence_boot_then_beacon_mode_is_explained(eng):
    """boot-app → immediately TTC_SET_BEACON_MODE(off): the S1.2 must say why."""
    send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=20)
    run(eng, 1.0)   # one tick, i.e. the operator's next keystroke
    pkts = send(eng, 8, 1, bytes([FUNC_SET_BEACON_MODE, 0]), seq=21)

    rej = s1(pkts, 2)
    assert rej, "func 70 is outside the bootloader subset — still refused"
    reason = failure_reason(rej[0])
    assert "boot IS in progress" in reason, \
        f"the refusal must explain the boot in flight, got: {reason!r}"
    assert "0x0A15" in reason, "point the operator at the boot-state parameter"
    assert "S1.7" in reason, "tell the operator what to wait for"

    # And the telemetry backing that sentence is live on the downlink.
    assert eng.params[0x0A15] == BOOT_STATE_VERIFYING

    # Once the boot completes the same command is accepted.
    in_flight = run(eng, 11.0)
    assert s1_seqs(in_flight, 7) == [20]
    in_application(eng)
    ok = send(eng, 8, 1, bytes([FUNC_SET_BEACON_MODE, 0]), seq=22)
    assert not s1(ok, 2) and s1_seqs(ok, 7) == [22]


def test_bootloader_refusal_is_unchanged_when_no_boot_is_running(eng):
    pkts = send(eng, 8, 1, bytes([FUNC_SET_BEACON_MODE, 0]), seq=23)
    reason = failure_reason(s1(pkts, 2)[0])
    assert "boot the application (S8.1 func 55) first" in reason


# ── The other deferred commands ─────────────────────────────────────────

def test_wing_deployment_defers_its_completion(eng):
    in_application(eng, power_all=True)
    drain(eng)
    at_dispatch = send(eng, 8, 1, bytes([FUNC_DEPLOY_WING, 0]), seq=30)
    assert not s1(at_dispatch, 7), \
        "the panel is still stowed when the handler returns"
    eps = eng.subsystems["eps"]
    assert eps._state.wing_py_deploying

    pkts = run(eng, 40.0)
    assert eps._state.wing_py_deployed
    assert s1_seqs(pkts, 7) == [30]


def test_gyro_calibration_defers_its_completion(eng, monkeypatch):
    in_application(eng)
    aocs = eng.subsystems["aocs"]
    # The arc's own preconditions (powered, tracker in the loop, low rates)
    # are exercised by test_aocs_gyro_calibration; hold them satisfied here so
    # this test is about the VERIFICATION contract and nothing else.
    monkeypatch.setattr(aocs, "_gyro_cal_blocker", lambda s: 0)

    res = eng._dispatcher.dispatch(
        8, 1, bytes([FUNC_GYRO_CAL, 0]) + struct.pack('>H', 30), None)
    assert not res.failed
    assert res.deferred and res.deferred_key == aocs.COMPLETION_GYRO_CAL, \
        "a 300 s arc cannot report execution complete on acceptance"
    assert res.deferred_timeout_s > 30.0


def test_gyro_calibration_s17_arrives_when_the_arc_ends(eng, monkeypatch):
    in_application(eng, power_all=True)
    aocs = eng.subsystems["aocs"]
    monkeypatch.setattr(aocs, "_gyro_cal_blocker", lambda s: 0)
    drain(eng)

    at_dispatch = send(eng, 8, 1,
                       bytes([FUNC_GYRO_CAL, 0]) + struct.pack('>H', 30),
                       seq=35)
    assert not s1(at_dispatch, 7)

    mid = run(eng, 20.0)
    assert not s1(mid, 7), "the arc is still averaging"
    late = run(eng, 20.0)
    assert s1_seqs(late, 7) == [35]
    assert aocs._state.gyro_cal_state == aocs.GYRO_CAL_COMPLETE


def test_gyro_calibration_abort_fails_the_held_report(eng):
    aocs = eng.subsystems["aocs"]
    eng._register_pending_completion(aocs.COMPLETION_GYRO_CAL, APID, 40, 8, 1,
                                     600.0, "AOCS_GYRO_CALIBRATION")
    aocs._abort_gyro_calibration(0x0A06, "preconditions lost")
    drain(eng)
    pkts = run(eng, 1.0)
    assert not s1(pkts, 7)
    assert s1_seqs(pkts, 8) == [40]


def test_payload_calibration_defers_its_completion(eng):
    in_application(eng, power_all=True)
    payload = eng.subsystems["payload"]
    res = payload.handle_command({"command": "start_calibration", "mode": 0})
    if not res.get("success"):
        # Preconditions (shutter, lamp, FPA temperature) are COM-011's
        # business and are covered by the payload tests; force the run.
        payload._state.calibration_active = True
        payload._state.calibration_state = 1
        payload._state.calibration_timer = 5.0
    else:
        assert res["deferred"] == payload.COMPLETION_CALIBRATION

    # An abort discards the half-made products and must FAIL the telecommand.
    eng._register_pending_completion(payload.COMPLETION_CALIBRATION, APID, 45,
                                     8, 1, 600.0, "PAYLOAD_START_CALIBRATION")
    drain(eng)
    payload._abort_calibration(5)
    pkts = run(eng, 1.0)
    assert not s1(pkts, 7)
    assert s1_seqs(pkts, 8) == [45]


def test_memory_scrub_defers_its_completion(eng):
    in_application(eng)
    obdh = eng.subsystems["obdh"]
    res = obdh.handle_command({"command": "memory_scrub"})
    assert res["success"] and res["deferred"] == "obdh.memory_scrub"


# ── Bounds: timeout, cap, supersede ─────────────────────────────────────

def test_a_lost_completion_times_out_into_s18(eng):
    eng._register_pending_completion("test.never", APID, 50, 8, 1, 3.0, "TEST")
    drain(eng)
    pkts = run(eng, 5.0)
    assert not s1(pkts, 7)
    fails = s1(pkts, 8)
    assert s1_seqs(pkts, 8) == [50]
    assert "no completion report" in failure_reason(fails[0])
    assert eng._pending_completions == [], "a timed-out entry must not leak"


def test_the_pending_table_is_capped(eng):
    for i in range(eng.MAX_PENDING_COMPLETIONS + 3):
        eng._register_pending_completion(f"test.k{i}", APID, 60 + i, 8, 1,
                                         600.0, "TEST")
    assert len(eng._pending_completions) == eng.MAX_PENDING_COMPLETIONS
    # The evicted entries were CLOSED with an S1.8, not dropped on the floor.
    assert sorted(s1_seqs(drain(eng), 8)) == [60, 61, 62]


def test_reissuing_a_command_supersedes_its_own_pending_entry(eng):
    eng._register_pending_completion("test.k", APID, 70, 8, 1, 600.0, "TEST")
    eng._register_pending_completion("test.k", APID, 71, 8, 1, 600.0, "TEST")
    assert len(eng._pending_completions) == 1
    assert s1_seqs(drain(eng), 8) == [70]
    # One completion report resolves exactly the surviving telecommand.
    assert eng.report_completion("test.k", True) == 1
    assert s1_seqs(drain(eng), 7) == [71]


def test_report_completion_with_nothing_pending_is_harmless(eng):
    assert eng.report_completion("test.nothing", True) == 0


# ── Breakpoint round-trip ───────────────────────────────────────────────

def test_pending_completions_survive_a_breakpoint(eng):
    send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=80)
    eng._tick_once(1.0)
    drain(eng)
    assert len(eng._pending_completions) == 1

    saved = eng.get_state()
    assert "pending_completions" in saved

    # Restore into a FRESH engine, mid-window, exactly as a breakpoint load
    # would. Without the round-trip the new engine owes the operator nothing
    # and the S1.7 is lost — state that silently heals on restore.
    other = SimulationEngine(CONFIG_DIR)
    other.set_state(saved)
    assert [e["seq"] for e in other._pending_completions] == [80]
    assert other._pending_completions[0]["key"] == COMPLETION_BOOT_APP

    other.subsystems["obdh"].set_state(eng.subsystems["obdh"].get_state())
    drain(other)
    pkts = run(other, 12.0)
    assert s1_seqs(pkts, 7) == [80], \
        "the restored engine must still owe — and pay — the held S1.7"


def test_restored_pending_set_is_clamped_to_the_cap(eng):
    state = eng.get_state()
    state["pending_completions"] = [
        {"key": f"k{i}", "apid": APID, "seq": i, "service": 8, "subtype": 1,
         "label": "", "remaining_s": 60.0}
        for i in range(eng.MAX_PENDING_COMPLETIONS + 5)]
    eng.set_state(state)
    assert len(eng._pending_completions) == eng.MAX_PENDING_COMPLETIONS


def test_an_old_snapshot_without_the_key_still_loads(eng):
    state = eng.get_state()
    state.pop("pending_completions")
    eng.set_state(state)
    assert eng._pending_completions == []


# ── Nothing else regressed ──────────────────────────────────────────────

def test_a_fast_command_still_completes_immediately(eng):
    in_application(eng)
    drain(eng)
    pkts = send(eng, 17, 1, b"", seq=90)
    assert s1_seqs(pkts, 7) == [90]
    assert eng._pending_completions == []


def test_a_refused_long_running_command_is_not_deferred(eng):
    """Already in the application: OBC_BOOT_APP is refused, not deferred."""
    in_application(eng)
    drain(eng)
    pkts = send(eng, 8, 1, bytes([FUNC_BOOT_APP]), seq=91)
    assert s1(pkts, 8), "the model refuses a boot when already in application"
    assert not s1(pkts, 7)
    assert eng._pending_completions == [], \
        "a refusal started nothing, so there is nothing to wait for"
    assert eng.subsystems["obdh"]._state.boot_state != BOOT_STATE_BOOTLOADER
