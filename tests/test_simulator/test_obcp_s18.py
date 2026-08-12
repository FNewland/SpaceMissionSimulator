"""S18 On-Board Control Procedures (OBCP) — end-to-end engine tests.

Covers defect #48 (on-board scripting absent): load via TC(18,1), activate via
TC(18,3), step execution over engine ticks on sim time, stop mid-run via
TC(18,4), status report TM(18,7), bad-step failure events, breakpoint
save/restore of a running procedure, and validation that the shipped example
procedures in configs/eosat1/obcp/ only use implemented S8 functions.

Drives engine._tick_once directly (fast-test pattern from
tests/test_obc_startup_bootloader.py) — no real-time sleeps.
"""
import json
import queue
import struct

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine
from smo_simulator.obcp import (
    ObcpManager,
    EVENT_OBCP_STARTED, EVENT_OBCP_COMPLETED,
    EVENT_OBCP_ABORTED, EVENT_OBCP_STEP_FAILED,
    OBCP_STATUS_IDLE, OBCP_STATUS_RUNNING,
)

CONFIG_DIR = "configs/eosat1"
APID = 0x1AC

# Every S8 func_id actually routed by ServiceDispatcher._handle_s8 (see
# service_dispatch.py: AOCS 0-15/84, EPS 16-25/81-83, payload 26-39,
# TCS 40-49, OBDH 50-62/80, TTC 63-78, legacy quick-actions 100-107).
IMPLEMENTED_S8_FUNCS = (
    set(range(0, 16)) | set(range(16, 26)) | set(range(26, 40))
    | set(range(40, 50)) | set(range(50, 63)) | set(range(63, 79))
    | {80, 81, 82, 83, 84} | set(range(85, 92)) | set(range(100, 108))
    | set(range(108, 119))   # Phase D propulsion block
)


def make_engine():
    """Engine out of bootloader (application booted) with forced link."""
    eng = SimulationEngine(CONFIG_DIR)
    eng._override_passes = True                    # force uplink/downlink
    eng.subsystems["obdh"]._state.sw_image = 1     # application running
    eng._tick_once(1.0)                            # phase 3 -> 4 (LEOP)
    assert eng._spacecraft_phase >= 4
    return eng


def s8_tc(func_id: int, data: bytes = b"") -> bytes:
    return build_tc_packet(apid=APID, service=8, subtype=1,
                           data=bytes([func_id]) + data)


def s18_tc(subtype: int, data: bytes = b"") -> bytes:
    return build_tc_packet(apid=APID, service=18, subtype=subtype, data=data)


def drain_events(eng) -> list[dict]:
    evs = []
    while True:
        try:
            evs.append(eng.event_queue.get_nowait())
        except queue.Empty:
            break
    return evs


def event_ids(evs) -> list[int]:
    return [e.get('event_id') for e in evs]


# ─── Load / activate / step execution ────────────────────────────────────────


class TestLoadActivateExecute:

    def test_load_activate_and_step_execution_over_ticks(self):
        eng = make_engine()
        lines = eng.subsystems["eps"]._state.power_lines

        # Two-step procedure: t+0 payload line ON, t+3s fpa_cooler line ON.
        blob = ObcpManager.encode_load_blob(10, "test_two_lines", [
            {'delay_s': 0.0, 'tc': s8_tc(19, b'\x03')},   # power_line_on payload
            {'delay_s': 3.0, 'tc': s8_tc(19, b'\x04')},   # power_line_on fpa_cooler
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        assert 10 in eng._obcp_manager._procedures

        assert not lines.get("payload")
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 10)))
        assert 10 in eng._obcp_manager._running
        evs = drain_events(eng)
        assert EVENT_OBCP_STARTED in event_ids(evs)

        # First tick: step 0 (delay 0) fires; step 1 (delay 3) must not yet.
        eng._tick_once(1.0)
        assert lines.get("payload") is True
        assert not lines.get("fpa_cooler")

        # Three more sim seconds: step 1 fires, procedure completes.
        for _ in range(3):
            eng._tick_once(1.0)
        assert lines.get("fpa_cooler") is True
        assert 10 not in eng._obcp_manager._running
        assert EVENT_OBCP_COMPLETED in event_ids(drain_events(eng))

    def test_steps_advance_on_sim_time_respecting_speed(self):
        """A 10x-speed tick (dt_sim=10) consumes 10 sim-seconds of delay."""
        eng = make_engine()
        lines = eng.subsystems["eps"]._state.power_lines
        blob = ObcpManager.encode_load_blob(11, "speedy", [
            {'delay_s': 8.0, 'tc': s8_tc(19, b'\x05')},   # htr_bat line ON
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 11)))
        eng._tick_once(10.0)   # one tick at 10x speed covers the 8 s delay
        assert lines.get("htr_bat") is True
        assert 11 not in eng._obcp_manager._running

    def test_delete_removes_procedure(self):
        eng = make_engine()
        blob = ObcpManager.encode_load_blob(12, "todelete", [
            {'delay_s': 0.0, 'tc': s8_tc(19, b'\x03')},
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        assert 12 in eng._obcp_manager._procedures
        eng._dispatch_tc(s18_tc(2, struct.pack('>H', 12)))
        assert 12 not in eng._obcp_manager._procedures


# ─── Stop mid-run ────────────────────────────────────────────────────────────


class TestStopMidRun:

    def test_stop_aborts_before_remaining_steps(self):
        eng = make_engine()
        lines = eng.subsystems["eps"]._state.power_lines
        blob = ObcpManager.encode_load_blob(20, "stoppable", [
            {'delay_s': 0.0, 'tc': s8_tc(19, b'\x03')},   # payload line ON
            {'delay_s': 5.0, 'tc': s8_tc(19, b'\x04')},   # fpa_cooler line ON
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 20)))
        eng._tick_once(1.0)                    # step 0 executes
        assert lines.get("payload") is True

        eng._dispatch_tc(s18_tc(4, struct.pack('>H', 20)))   # stop mid-run
        assert 20 not in eng._obcp_manager._running
        assert EVENT_OBCP_ABORTED in event_ids(drain_events(eng))

        for _ in range(8):                     # step 1 must never fire
            eng._tick_once(1.0)
        assert not lines.get("fpa_cooler")
        # Procedure stays loaded (only the run was aborted) and can restart.
        assert 20 in eng._obcp_manager._procedures
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 20)))
        assert 20 in eng._obcp_manager._running


# ─── Status report TM(18,7) ─────────────────────────────────────────────────


class TestStatusReport:

    def test_status_report_tm(self):
        eng = make_engine()
        blob = ObcpManager.encode_load_blob(30, "status_proc", [
            {'delay_s': 0.0, 'tc': s8_tc(19, b'\x03')},
            {'delay_s': 30.0, 'tc': s8_tc(19, b'\x04')},
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 30)))
        eng._tick_once(1.0)   # step 0 executed; now waiting at step 1

        responses = eng._dispatcher.dispatch(18, 6, b'', None)
        assert len(responses) == 1
        tm = decommutate_packet(responses[0])
        assert tm is not None and tm.secondary is not None
        assert tm.secondary.service == 18 and tm.secondary.subtype == 7

        count = tm.data_field[0]
        entries = {}
        off = 1
        for _ in range(count):
            pid, status, step, n_steps = struct.unpack(
                '>HBBB', tm.data_field[off:off + 5])
            entries[pid] = (status, step, n_steps)
            off += 5
        # Boot-loaded examples (1..3) are idle; ours is running at step 1.
        assert 30 in entries
        assert entries[30] == (OBCP_STATUS_RUNNING, 1, 2)
        for boot_pid in (1, 2, 3):
            assert boot_pid in entries
            assert entries[boot_pid][0] == OBCP_STATUS_IDLE

    def test_start_of_unknown_procedure_reports_error(self):
        eng = make_engine()
        d = eng._dispatcher
        # WP-7: the engine turns this into the S1.8 exec failure from the
        # returned result, not from a dispatcher attribute.
        res = d.dispatch(18, 3, struct.pack('>H', 999), None)
        assert res.failure_kind == "execution"
        assert res.reason


# ─── Bad step → failure event ────────────────────────────────────────────────


class TestBadStepFailure:

    def test_unknown_service_step_emits_failure_and_aborts(self):
        eng = make_engine()
        bad_tc = build_tc_packet(apid=APID, service=99, subtype=1, data=b'')
        blob = ObcpManager.encode_load_blob(40, "bad_proc", [
            {'delay_s': 0.0, 'tc': bad_tc},
            {'delay_s': 5.0, 'tc': s8_tc(19, b'\x03')},
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 40)))
        eng._tick_once(1.0)

        ids = event_ids(drain_events(eng))
        assert EVENT_OBCP_STEP_FAILED in ids
        assert EVENT_OBCP_ABORTED in ids
        assert 40 not in eng._obcp_manager._running
        # Remaining step never ran.
        assert not eng.subsystems["eps"]._state.power_lines.get("payload")

    def test_malformed_step_bytes_emit_failure(self):
        eng = make_engine()
        blob = ObcpManager.encode_load_blob(41, "garbage_proc", [
            {'delay_s': 0.0, 'tc': b'\x01\x02\x03'},   # not a CCSDS packet
        ])
        eng._dispatch_tc(s18_tc(1, blob))
        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 41)))
        eng._tick_once(1.0)
        ids = event_ids(drain_events(eng))
        assert EVENT_OBCP_STEP_FAILED in ids
        assert 41 not in eng._obcp_manager._running

    def test_truncated_load_blob_reports_error(self):
        eng = make_engine()
        d = eng._dispatcher
        res = d.dispatch(18, 1, struct.pack('>H', 50) + b'\xff\xff', None)
        assert res.failure_kind == "execution"
        assert 50 not in eng._obcp_manager._procedures


# ─── Breakpoint save/restore of a running procedure ─────────────────────────


class TestBreakpointSaveRestore:

    def test_running_procedure_survives_breakpoint(self):
        from smo_simulator.breakpoints import BreakpointManager

        eng1 = make_engine()
        blob = ObcpManager.encode_load_blob(60, "bp_proc", [
            {'delay_s': 0.0, 'tc': s8_tc(19, b'\x03')},   # payload line ON
            {'delay_s': 4.0, 'tc': s8_tc(19, b'\x04')},   # fpa_cooler line ON
        ])
        eng1._dispatch_tc(s18_tc(1, blob))
        eng1._dispatch_tc(s18_tc(3, struct.pack('>H', 60)))
        eng1._tick_once(1.0)   # step 0 executed; waiting on step 1

        state = BreakpointManager(eng1).save("obcp_bp")
        # Prove the snapshot is JSON-serializable like on-disk breakpoints.
        state = json.loads(json.dumps(state, default=str))
        assert "obcp" in state
        assert "60" in state["obcp"]["running"]

        # Restore into a FRESH engine and finish the run there.
        eng2 = make_engine()
        assert BreakpointManager(eng2).load(state=state)
        mgr2 = eng2._obcp_manager
        assert 60 in mgr2._procedures
        assert 60 in mgr2._running
        assert mgr2._running[60]['step_index'] == 1

        lines2 = eng2.subsystems["eps"]._state.power_lines
        for _ in range(6):
            eng2._tick_once(1.0)
        assert lines2.get("fpa_cooler") is True
        assert 60 not in mgr2._running

    def test_idle_store_roundtrip(self):
        mgr = ObcpManager(engine=None)   # engine unused for state I/O
        mgr.load(5, "roundtrip", [{'delay_s': 1.5, 'tc': s8_tc(19, b'\x03')}])
        snap = json.loads(json.dumps(mgr.get_state()))
        mgr2 = ObcpManager(engine=None)
        mgr2.set_state(snap)
        assert mgr2._procedures[5]['name'] == "roundtrip"
        assert mgr2._procedures[5]['steps'][0]['delay_s'] == 1.5
        assert mgr2._procedures[5]['steps'][0]['tc'] == s8_tc(19, b'\x03')


# ─── Example configs (configs/eosat1/obcp/) ──────────────────────────────────


class TestExampleProcedures:

    def test_examples_preloaded_at_boot(self):
        eng = SimulationEngine(CONFIG_DIR)
        status = {s['proc_id']: s for s in eng._obcp_manager.get_status()}
        assert {1, 2, 3} <= set(status)
        assert status[1]['name'] == "pass_preparation"
        assert status[2]['name'] == "safe_mode_entry"
        assert status[3]['name'] == "payload_checkout"
        for s in status.values():
            assert s['status'] == OBCP_STATUS_IDLE
            assert s['n_steps'] >= 3

    def test_example_steps_use_only_implemented_funcs(self):
        """Every step of every shipped OBCP must decommutate to a TC the
        dispatcher actually implements (guards against fabricated func_ids —
        the disease documented in integration-trace Section 2.3)."""
        eng = SimulationEngine(CONFIG_DIR)
        for pid, proc in eng._obcp_manager._procedures.items():
            for i, step in enumerate(proc['steps']):
                pkt = decommutate_packet(step['tc'])
                assert pkt is not None and pkt.secondary is not None, \
                    f"OBCP {pid} step {i}: unparseable TC"
                svc, sub = pkt.secondary.service, pkt.secondary.subtype
                ok, err = eng._check_tc_acceptance(svc, sub, pkt.data_field)
                assert ok, f"OBCP {pid} step {i}: S{svc}.{sub} rejected 0x{err:04X}"
                if svc == 8 and sub == 1:
                    func_id = pkt.data_field[0]
                    assert func_id in IMPLEMENTED_S8_FUNCS, \
                        f"OBCP {pid} step {i}: S8 func {func_id} not implemented"

    def test_pass_preparation_runs_end_to_end(self):
        eng = make_engine()
        ttc = eng.subsystems["ttc"]._state
        eps = eng.subsystems["eps"]._state
        # 2026-08b (finding 3): bootloader entry still forces the PA off, but
        # `make_engine` boots the application (sw_image 0 -> 1) and the TT&C
        # model now lifts the S-band inhibit on that edge — the missing
        # reverse of `_enter_bootloader_mode`. Without it every commissioning
        # sequence in the repo boots into silence, because `downlink_active`
        # gates on the transmitter now. OBCP 1's own TTC_PA_ON step is still
        # meaningful and still asserted below: it is idempotent here, and it
        # is what recovers the link after an operator or FDIR PA-off.
        assert ttc.pa_on is True    # inhibit lifted by the application boot
        ttc.pa_on = False           # ...so put it back to make the OBCP earn it

        eng._dispatch_tc(s18_tc(3, struct.pack('>H', 1)))   # start OBCP 1
        for _ in range(15):
            eng._tick_once(1.0)

        assert eps.power_lines.get("ttc_tx") is True
        assert ttc.pa_on is True
        assert ttc.antenna_deployed is True
        assert ttc.beacon_mode is False
        assert eps.transponder_tx is True
        # NOTE: the set-TM-rate step (S8 func 65) does reach the TTC model, but
        # the TTC tick recomputes tm_data_rate from `data_rate_mode`, which no
        # TC can set (ttc_basic.py "Data rate selection"). That is a
        # pre-existing TTC limitation, independent of OBCP — see
        # docs/OBCP_GUIDE.md "Known limitations". The step is kept in the
        # procedure because it is the catalogued high-rate command.
        assert 1 not in eng._obcp_manager._running
        assert EVENT_OBCP_COMPLETED in event_ids(drain_events(eng))
