"""Proof tests for the 2026-08c field-report fixes (second batch).

Each class pins one item from the owner's LEOP/commissioning fly-through:

  * item 9  — S13 was refused at ACCEPTANCE (0x0001 "unknown service") on the
              real uplink path even though the dispatcher implements it, so
              NOM-003's whole downlink chain was dead on the wire;
  * item 8  — a COMMANDED load-shed stage was overwritten to 0 by the next
              tick (no 0x0134 movement, no stage event) and the old handler
              POWERED ON every line beyond the commanded stage;
  * item 11 — gyro-bias calibration demanded mode >= NOMINAL, which the
              commissioning order cannot deliver at COM-003 (wheels are only
              enabled at COM-004). The honest precondition is ST tracking +
              ST feeding the estimator + low rates, which COARSE_SUN reaches;
  * items 1/2/3 — magnetometer TM, GPS PVT TM and the EPS prop line ride
              routine telemetry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    # Boot the application so the bootloader TC allowlist is out of the way.
    eng._dispatcher.dispatch(8, 1, bytes([55]), None)
    for _ in range(20):
        eng._tick_once(1.0)
    assert eng._spacecraft_phase == 4
    return eng


def _drain(engine):
    out = []
    while not engine.tm_queue.empty():
        parsed = decommutate_packet(engine.tm_queue.get_nowait())
        if parsed and parsed.secondary:
            out.append(parsed)
    return out


def _send_raw(engine, service, subtype, data=b""):
    """One TC through the REAL packet path (acceptance gates included)."""
    _drain(engine)
    engine._dispatch_tc(build_tc_packet(apid=100, service=service,
                                        subtype=subtype, data=data))
    return _drain(engine)


def _of(packets, service, subtype):
    return [p for p in packets
            if p.secondary.service == service and p.secondary.subtype == subtype]


# ═══════════════════════════════════════════════════════════════════
# Item 9 — S13 passes acceptance on the real TC path
# ═══════════════════════════════════════════════════════════════════

class TestS13Acceptance:
    def test_s13_create_is_not_refused_at_acceptance(self, engine):
        """TC(13,1) must reach the handler: no S1.2, and the handler answers
        (TM(13,2) session or TM(13,4) NAK for an uncatalogued scene)."""
        pkts = _send_raw(engine, 13, 1, bytes([0x00, 0x07, 0x00, 0x00]))
        assert not _of(pkts, 1, 2), "S13.1 was refused at acceptance again"
        assert _of(pkts, 13, 2) or _of(pkts, 13, 4), \
            "the S13 handler produced no reply at all"

    def test_s13_subtypes_are_known(self, engine):
        for sub in (1, 3, 5, 9):
            ok, code = engine._check_tc_acceptance(13, sub, b"\x00\x00\x00\x00")
            assert ok, f"S13.{sub} refused at acceptance (0x{code:04X})"

    def test_s13_tm_subtypes_are_not_commandable(self, engine):
        ok, code = engine._check_tc_acceptance(13, 2, b"")
        assert not ok and code == 0x0002


# ═══════════════════════════════════════════════════════════════════
# Item 8 — commanded load shedding is real, observable and evented
# ═══════════════════════════════════════════════════════════════════

class TestCommandedLoadShed:
    LINES = {1: "payload", 2: "fpa_cooler", 3: "ttc_tx"}

    def _lines(self, engine):
        return engine.subsystems["eps"]._state.power_lines

    def _all_on(self, engine):
        for idx in (2, 3, 4, 5, 6, 7, 8):
            engine._dispatcher.dispatch(8, 1, bytes([19, idx]), None)
        for _ in range(3):
            engine._tick_once(1.0)

    def test_each_stage_has_observable_effect_and_tm(self, engine):
        self._all_on(engine)
        shed_order = ["payload", "fpa_cooler", "ttc_tx"]
        for stage in (1, 2, 3):
            r = engine._dispatcher.dispatch(8, 1, bytes([25, stage]), None)
            assert r.error is None, r.error
            for _ in range(2):
                engine._tick_once(1.0)
            lines = self._lines(engine)
            for name in shed_order[:stage]:
                assert lines[name] is False, f"stage {stage}: {name} still on"
            # 0x0134 must publish the commanded stage (used to snap to 0).
            assert int(engine.params[0x0134]) == stage
            # Re-arm the shed line for the next iteration's delta check? No —
            # stages are cumulative; nothing to re-arm.

    def test_shed_never_powers_lines_on(self, engine):
        """Commanding a LOWER stage must not re-energise anything — the old
        handler set power_lines[load] = not should_shed."""
        self._all_on(engine)
        engine._dispatcher.dispatch(8, 1, bytes([25, 3]), None)
        engine._tick_once(1.0)
        before = dict(self._lines(engine))
        engine._dispatcher.dispatch(8, 1, bytes([25, 1]), None)
        engine._tick_once(1.0)
        after = self._lines(engine)
        for name, was_on in before.items():
            if not was_on:
                assert after[name] is False, \
                    f"shed stage 1 POWERED ON {name}"

    def test_stage_events_fire_per_stage(self, engine):
        """A direct 0->3 shed logs LOAD_SHED_STAGE_1, _2 and _3."""
        self._all_on(engine)
        # The TM queue is downlink-gated (no contact at phase 4), so assert
        # on engine.event_queue, which _emit_event feeds unconditionally.
        while not engine.event_queue.empty():
            engine.event_queue.get_nowait()
        engine._dispatcher.dispatch(8, 1, bytes([25, 3]), None)
        for _ in range(3):
            engine._tick_once(1.0)
        ids = set()
        while not engine.event_queue.empty():
            ids.add(engine.event_queue.get_nowait().get("event_id"))
        for eid in (0x0106, 0x0107, 0x0108):
            assert eid in ids, f"event 0x{eid:04X} did not fire ({sorted(map(hex, ids))})"

    def test_stage_zero_clears_latch_without_repowering(self, engine):
        self._all_on(engine)
        engine._dispatcher.dispatch(8, 1, bytes([25, 2]), None)
        engine._tick_once(1.0)
        assert int(engine.params[0x0134]) == 2
        r = engine._dispatcher.dispatch(8, 1, bytes([25, 0]), None)
        assert r.error is None
        for _ in range(2):
            engine._tick_once(1.0)
        assert int(engine.params[0x0134]) == 0
        lines = self._lines(engine)
        assert lines["payload"] is False and lines["fpa_cooler"] is False


# ═══════════════════════════════════════════════════════════════════
# Item 11 — gyro calibration reachable at COM-003 (before wheels)
# ═══════════════════════════════════════════════════════════════════

class TestGyroCalPrecondition:
    def _to_coarse_sun_with_st(self, engine):
        d = engine._dispatcher
        # Power AOCS line, star trackers on, coarse sun — NO wheels.
        d.dispatch(8, 1, bytes([19, 7]), None)
        d.dispatch(8, 1, bytes([4, 1]), None)   # ST1 power
        d.dispatch(8, 1, bytes([0, 3]), None)   # AOCS_SET_MODE COARSE_SUN
        for _ in range(240):
            engine._tick_once(1.0)

    def test_cal_starts_in_coarse_sun_with_st_tracking(self, engine):
        """The COM-003 state: COARSE_SUN, ST tracking, NO wheels.

        COARSE_SUN auto-promotes to NOMINAL after ~60 s of sun lock + ST
        (it is a transient hold, not a destination), so the calibration is
        attempted at the FIRST tick where the tracker feeds the estimator
        while the mode is still COARSE_SUN — which is exactly the window
        COM-003 flies in. Wheels stay off throughout (COM-004 enables
        them), which is the whole point of item 11."""
        d = engine._dispatcher
        d.dispatch(8, 1, bytes([19, 7]), None)   # AOCS power line
        d.dispatch(8, 1, bytes([4, 1]), None)    # ST1 power
        d.dispatch(8, 1, bytes([0, 3]), None)    # AOCS_SET_MODE COARSE_SUN
        aocs = engine.subsystems["aocs"]
        s = aocs._state
        started = False
        # 200 -> 900 ticks, retrying on the rate gate (2026-08): at the new
        # epoch (t0 2026-08-20T12:00:00Z, TLE-truth ruling) COARSE_SUN
        # acquires the sun immediately (t0 is in sunlight) and the residual
        # body rate is still just above the 0.10 deg/s calibration gate when
        # the tracker first aids the estimator; it damps below the gate a few
        # hundred seconds later, still in COARSE_SUN with the wheels off —
        # the same COM-003 window, entered a little later.
        for _ in range(900):
            engine._tick_once(1.0)
            if (s.mode == 3 and (int(s.est_source) & 0x01)
                    and s.st1_num_stars >= 5):
                assert int(engine.params.get(0x0260, 0)) == 0, \
                    "wheels must be OFF (COM-004 enables them)"
                r = aocs.handle_command({"command": "gyro_calibration",
                                         "action": 0, "duration_s": 60})
                if not r["success"] and "body rate" in r.get("message", ""):
                    continue          # still damping — stay in the window
                assert r["success"], r
                assert s.gyro_cal_state == 1  # RUNNING
                started = True
                break
        assert started, (
            "the ST never aided the estimator in COARSE_SUN "
            f"(mode={s.mode}, est_source={s.est_source:#x}, "
            f"st_valid={s.st_valid}, stars={s.st1_num_stars})")

    def test_cal_still_refused_without_st(self, engine):
        d = engine._dispatcher
        d.dispatch(8, 1, bytes([19, 7]), None)
        d.dispatch(8, 1, bytes([0, 3]), None)   # COARSE_SUN, no ST power
        for _ in range(60):
            engine._tick_once(1.0)
        aocs = engine.subsystems["aocs"]
        r = aocs.handle_command(
            {"command": "gyro_calibration", "action": 0, "duration_s": 60})
        assert r["success"] is False
        assert r["error_code"] == 0x0A03


# ═══════════════════════════════════════════════════════════════════
# Items 1/2/3 — new TM is real: mag field, GPS PVT, prop line
# ═══════════════════════════════════════════════════════════════════

class TestNewTelemetry:
    def test_mag_field_published_in_detumble(self, engine):
        d = engine._dispatcher
        d.dispatch(8, 1, bytes([19, 7]), None)
        for _ in range(2):
            engine._tick_once(1.0)  # let 0x0117 reflect the line state
        d.dispatch(8, 1, bytes([9, 1]), None)      # MTQ_ENABLE — B-dot actuator
        r = d.dispatch(8, 1, bytes([0, 2]), None)  # DETUMBLE — B-dot needs mag
        assert r.error is None, r.error
        for _ in range(10):
            engine._tick_once(1.0)
        bx, by, bz = (engine.params[p] for p in (0x020B, 0x020C, 0x020D))
        mag = (bx * bx + by * by + bz * bz) ** 0.5
        assert 5_000.0 < mag < 70_000.0, "measured B implausible"
        assert int(engine.params[0x025C]) == 1
        assert engine.params[0x0277] == pytest.approx(mag, rel=0.05)

    def test_gps_pvt_gated_on_fix(self, engine):
        # GPS receiver device on (527) — TTFF then a 3D fix publishes PVT.
        d = engine._dispatcher
        d.dispatch(8, 1, bytes([19, 7]), None)
        d.dispatch(2, 1, bytes([0x02, 0x0F, 0x01]), None)  # device 527 on
        for _ in range(120):
            engine._tick_once(1.0)
        fix = int(engine.params.get(0x0274, 0))
        lat = engine.params.get(0x0210, 0.0)
        alt = engine.params.get(0x0212, 0.0)
        if fix >= 2:
            assert -90.0 <= lat <= 90.0
            assert 400.0 < alt < 500.0, f"gps_alt {alt} km implausible"
            assert abs(engine.params.get(0x0231, 0.0)) > 1e5  # ECEF X in m
        else:
            # Honest zeros while the receiver has no fix.
            assert lat == 0.0 and alt == 0.0

    def test_s13_session_drives_payload_transfer_tm(self, engine):
        """Item 10: S13.1 against a catalogued product raises
        payload.transfer_active (0x062D) and 0x062E drains at the active TM
        rate; S13.5 clears the flag — the NOM-003 verifies."""
        import struct
        pl = engine.subsystems["payload"]
        # Plant a catalogued product directly (the capture chain is exercised
        # elsewhere); what is under test is the S13 <-> payload binding.
        r = pl.handle_command({"command": "set_mode", "mode": 1})
        img = {"scene_id": 42, "lat": 0.0, "lon": 0.0, "size_mb": 1.0,
               "lines": 512, "quality": 90, "status": 0, "flags": 0,
               "segment": 0, "data_seed": 1234}
        pl._state.image_catalog.append(img)
        engine._override_passes = True
        engine.params[0x05FF] = 1
        d = engine._dispatcher
        d.dispatch(8, 1, bytes([19, 2]), None)   # ttc_tx line
        d.dispatch(8, 1, bytes([19, 3]), None)   # payload line
        d.dispatch(8, 1, bytes([66]), None)      # PA on
        d.dispatch(8, 1, bytes([70, 0]), None)   # beacon mode off
        for _ in range(5):
            engine._tick_once(1.0)
        res = d.dispatch(13, 1, struct.pack(">HH", 42, 0), None)
        assert res.error is None, res.error
        engine._tick_once(1.0)
        assert int(engine.params[0x062D]) == 1, "0x062D did not raise"
        p0 = engine.params[0x062E]
        for _ in range(10):
            engine._tick_once(1.0)
        assert engine.params[0x062E] > p0, "0x062E is not draining"
        d.dispatch(13, 5, struct.pack(">H", 1), None)
        engine._tick_once(1.0)
        assert int(engine.params[0x062D]) == 0, "S13.5 did not clear 0x062D"

    def test_prop_line_status_and_current_follow_the_switch(self, engine):
        d = engine._dispatcher
        assert int(engine.params.get(0x014A, 0)) == 0
        d.dispatch(8, 1, bytes([19, 8]), None)
        for _ in range(3):
            engine._tick_once(1.0)
        assert int(engine.params[0x014A]) == 1
        assert 0x014B in engine.params
        d.dispatch(8, 1, bytes([20, 8]), None)
        for _ in range(3):
            engine._tick_once(1.0)
        assert int(engine.params[0x014A]) == 0
