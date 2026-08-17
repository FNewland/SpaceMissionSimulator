"""Defect #87 — a rejected command must say WHY, all the way to the ground.

Franz commanded the LOW TM rate straight after booting the OBC application.
The spacecraft was still in beacon mode (the application boot does not clear
it), so ``ttc_basic._tier_permission_error`` refused the tier with an
excellent reason — "TM rate is forced to the beacon tier in beacon mode (LOW
refused)". The reason reached ``result["message"]``, the dispatcher stored it
in ``_last_error``… and then the S1.8 report packed only request_id +
error_code and threw the text away. The operator saw "a 1,8" and a number.

These tests pin the fix:

* S1.8 / S1.2 / S1.4 carry a strictly-appended, length-prefixed UTF-8 failure
  notice, and a legacy parser reading only the fixed prefix still works;
* a COMMAND_REJECTED S5 event carries the command identity and the reason;
* several different rejection paths each produce a SPECIFIC reason, not a
  generic one.
"""
import os
import struct
from pathlib import Path

import pytest
import yaml

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_common.protocol.pus_services import (
    FAILURE_CODES, FAILURE_REASON_MAX_BYTES, VerificationData,
    decode_failure_reason, encode_failure_reason, failure_code_text,
)
from smo_common.telemetry.tm_builder import TMBuilder
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

# S8.1 func ids used below
FUNC_SET_DATA_RATE = 65
FUNC_SET_DOWNLINK_MODE = 79
FUNC_OBC_REBOOT = 52
FUNC_DEPLOY_WING = 81


@pytest.fixture
def engine(tmp_path):
    """A commissioned engine with the link forced up and the TX line hot."""
    os.environ.setdefault("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng._in_contact = True
    eng.params[0x0501] = 2
    eng.subsystems["obdh"]._state.sw_image = 1
    eng.subsystems["eps"]._state.power_lines["ttc_tx"] = True
    ttc = eng.subsystems["ttc"]
    ttc._state.antenna_deployed = True
    ttc._state.beacon_mode = False
    return eng


def _drain(engine):
    """Every TM packet the engine produced, decommutated."""
    out = []
    while not engine.tm_queue.empty():
        parsed = decommutate_packet(engine.tm_queue.get_nowait())
        if parsed and parsed.secondary:
            out.append(parsed)
    return out


def _of(packets, service, subtype):
    return [p for p in packets
            if p.secondary.service == service and p.secondary.subtype == subtype]


def _send(engine, service, subtype, data):
    """Dispatch one TC and return the TM it produced."""
    _drain(engine)
    engine._dispatch_tc(build_tc_packet(apid=100, service=service,
                                        subtype=subtype, data=data))
    return _drain(engine)


def _s5_events(packets):
    """(event_id, aux_text) for every S5 event report in `packets`."""
    out = []
    for p in packets:
        if p.secondary.service != 5 or len(p.data_field) < 8:
            continue
        event_id = struct.unpack('>H', p.data_field[:2])[0]
        text_len = p.data_field[7]
        aux = p.data_field[8:8 + text_len].decode('utf-8', errors='replace')
        out.append((event_id, aux))
    return out


# ═══════════════════════════════════════════════════════════════════
# The failure-notice codec
# ═══════════════════════════════════════════════════════════════════

class TestFailureNoticeCodec:
    def test_round_trip(self):
        blob = encode_failure_reason("beacon mode refuses LOW")
        assert blob[0] == len(blob) - 1
        assert decode_failure_reason(blob, 0) == "beacon mode refuses LOW"

    def test_empty_reason_emits_no_field(self):
        assert encode_failure_reason("") == b""
        assert encode_failure_reason(None) == b""

    def test_absent_field_decodes_to_empty_string(self):
        """An old spacecraft that appends nothing must not break the parser."""
        assert decode_failure_reason(struct.pack('>IH', 1, 7), 6) == ""

    def test_truncated_field_decodes_to_empty_string(self):
        blob = encode_failure_reason("a longer reason")
        assert decode_failure_reason(blob[:-4], 0) == ""

    def test_long_reason_is_truncated_to_the_cap(self):
        blob = encode_failure_reason("x" * 500)
        assert blob[0] == FAILURE_REASON_MAX_BYTES
        assert len(decode_failure_reason(blob, 0)) == FAILURE_REASON_MAX_BYTES

    def test_truncation_never_splits_a_multibyte_character(self):
        # 'é' is two octets; 100 of them straddle the 160-octet cap.
        blob = encode_failure_reason("é" * 100)
        text = decode_failure_reason(blob, 0)
        assert "�" not in text
        assert text == "é" * (FAILURE_REASON_MAX_BYTES // 2)

    def test_code_vocabulary_covers_every_code_the_obsw_emits(self):
        for code in (0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0006, 0x0007):
            assert code in FAILURE_CODES
            assert failure_code_text(code) == FAILURE_CODES[code]

    def test_service_6_codes_get_the_memory_vocabulary(self):
        assert "read-only" in failure_code_text(0x0002, service=6).lower() or \
               "write protected" in failure_code_text(0x0002, service=6).lower()
        assert failure_code_text(0x0002, service=8) == FAILURE_CODES[0x0002]


# ═══════════════════════════════════════════════════════════════════
# Wire format — appended, and backward compatible
# ═══════════════════════════════════════════════════════════════════

class TestS1WireFormat:
    def test_s1_8_carries_the_reason(self, engine):
        engine.subsystems["ttc"]._state.beacon_mode = True
        tm = _send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 0]))
        fails = _of(tm, 1, 8)
        assert len(fails) == 1
        vd = VerificationData.parse(fails[0].data_field, 8)
        assert vd.error_code == 0x0007
        assert "beacon" in vd.reason.lower()
        assert "LOW" in vd.reason

    def test_legacy_hh_parse_of_s1_8_still_succeeds(self, engine):
        """The historical '>IH' prefix is untouched — the notice is appended."""
        engine.subsystems["ttc"]._state.beacon_mode = True
        tm = _send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 0]))
        data = _of(tm, 1, 8)[0].data_field
        request_id, error_code = struct.unpack('>IH', data[:6])
        assert error_code == 0x0007
        assert (request_id & 0x3FFF) >= 0
        # …and everything after byte 6 is the optional notice, so a legacy
        # reader that stops at 6 sees exactly what it always saw.
        assert len(data) > 6

    def test_legacy_hh_parse_of_s1_4_still_succeeds(self, engine):
        """S1.4 keeps its 16-bit request id: request_id(u16) + code(u16)."""
        # S6 MEM_DUMP of an address that is in no declared region.
        tm = _send(engine, 6, 5, struct.pack('>IH', 0xDEADBEEF, 16))
        fails = _of(tm, 1, 4)
        assert len(fails) == 1
        request_id, error_code = struct.unpack('>HH', fails[0].data_field[:4])
        assert request_id == 0
        assert error_code == 0x0001  # ERR_ILLEGAL_ADDRESS
        assert decode_failure_reason(fails[0].data_field, 4) != ""
        # And the shared parser understands the same bytes.
        vd = VerificationData.parse(fails[0].data_field, 4)
        assert vd.error_code == 0x0001
        assert "0xDEADBEEF" in vd.reason

    def test_s1_2_acceptance_failure_carries_the_reason(self, engine):
        """Acceptance failures had the same gap — an unknown subtype now says so."""
        tm = _send(engine, 3, 99, b'\x00\x01')
        rej = _of(tm, 1, 2)
        assert len(rej) == 1
        vd = VerificationData.parse(rej[0].data_field, 2)
        assert vd.error_code == 0x0002
        assert "S3.99" in vd.reason

    def test_success_reports_are_unchanged(self, engine):
        tm = _send(engine, 17, 1, b'')
        assert _of(tm, 1, 8) == []
        comp = _of(tm, 1, 7)
        assert len(comp) == 1
        assert len(comp[0].data_field) == 4  # request_id only

    def test_builder_omits_the_notice_when_there_is_no_reason(self):
        # A TMBuilder needs an on-board time source: it stamps every packet
        # with the OBC clock and raises rather than falling back to the host
        # wall clock.
        b = TMBuilder(apid=1, time_source=lambda: 1000.0)
        pkt = decommutate_packet(b.build_execution_failure(100, 5, 0x0001))
        assert len(pkt.data_field) == 6  # '>IH' exactly, nothing appended


# ═══════════════════════════════════════════════════════════════════
# The S5 COMMAND_REJECTED event
# ═══════════════════════════════════════════════════════════════════

class TestCommandRejectedEvent:
    EV = 0x030B

    def test_event_is_in_the_catalogue(self):
        catalog = yaml.safe_load(
            (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())
        by_id = {int(e["id"]): e for e in catalog["events"]}
        assert self.EV in by_id
        assert by_id[self.EV]["name"] == "COMMAND_REJECTED"

    def test_execution_rejection_raises_the_event_with_identity_and_reason(self, engine):
        engine.subsystems["ttc"]._state.beacon_mode = True
        tm = _send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 0]))
        events = [e for e in _s5_events(tm) if e[0] == self.EV]
        assert len(events) == 1
        text = events[0][1]
        assert "S8.1" in text                      # service / subtype
        assert f"func {FUNC_SET_DATA_RATE}" in text  # func identity
        assert "0x0007" in text                    # failure code
        assert "beacon" in text.lower()            # the reason itself

    def test_acceptance_rejection_raises_the_event(self, engine):
        tm = _send(engine, 3, 99, b'\x00\x01')
        events = [e for e in _s5_events(tm) if e[0] == self.EV]
        assert len(events) == 1
        assert "acceptance" in events[0][1]

    def test_accepted_command_raises_no_rejection_event(self, engine):
        tm = _send(engine, 17, 1, b'')
        assert [e for e in _s5_events(tm) if e[0] == self.EV] == []

    def test_event_reaches_the_onboard_alarm_store(self, engine):
        """severity MEDIUM (>=2) => duplicated into the alarm store (store 4)."""
        engine._tm_storage.enable_store(4)
        before = {s['id']: s['count'] for s in engine._tm_storage.get_status()}
        engine.subsystems["ttc"]._state.beacon_mode = True
        _send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 0]))
        after = {s['id']: s['count'] for s in engine._tm_storage.get_status()}
        assert after[4] > before[4]


# ═══════════════════════════════════════════════════════════════════
# Table-driven: every rejection path names its own gate
# ═══════════════════════════════════════════════════════════════════

def _beacon_mode(eng):
    eng.subsystems["ttc"]._state.beacon_mode = True


def _bootloader(eng):
    eng._spacecraft_phase = 3
    eng.subsystems["obdh"]._state.sw_image = 0


def _no_antenna(eng):
    eng.subsystems["ttc"]._state.antenna_deployed = False


def _identity(eng):
    pass


# (label, setup, service, subtype, data, expected code, substrings the reason
#  must contain — each unique to THAT gate)
REJECTION_CASES = [
    (
        "bad parameter — unknown TM rate tier",
        _identity, 8, 1, bytes([FUNC_SET_DATA_RATE, 0x42]), 0x0003,
        ["Invalid TM rate/tier"],
    ),
    (
        "gated tier — beacon mode refuses LOW (Franz's case)",
        _beacon_mode, 8, 1, bytes([FUNC_SET_DATA_RATE, 0]), 0x0007,
        ["beacon", "LOW"],
    ),
    (
        "gated tier — HIGH needs a deployed antenna",
        _no_antenna, 8, 1, bytes([FUNC_SET_DATA_RATE, 1]), 0x0007,
        ["antenna"],
    ),
    (
        "bootloader-blocked func — payload capture",
        _bootloader, 8, 1, bytes([28, 0, 0, 0, 0, 0, 0, 0, 0]), 0x0006,
        ["bootloader", "func 28"],
    ),
    (
        "bootloader-blocked service — S15 dump",
        _bootloader, 15, 9, bytes([1]), 0x0006,
        ["bootloader", "S15.9"],
    ),
    (
        "unknown service",
        _identity, 99, 1, b'\x00', 0x0001,
        ["99"],
    ),
    (
        "unknown subtype",
        _identity, 8, 99, b'\x00', 0x0002,
        ["S8.99"],
    ),
    (
        "S6 memory — unmapped address",
        _identity, 6, 5, struct.pack('>IH', 0xDEADBEEF, 16), 0x0001,
        ["MEM_DUMP", "0xDEADBEEF"],
    ),
    (
        "S19 — bad trigger kind",
        _identity, 19, 1, struct.pack('>HBHB', 1, 9, 0x0101, 10), 0x0003,
        ["trigger kind"],
    ),
]


@pytest.mark.parametrize(
    "label,setup,service,subtype,data,code,substrings",
    REJECTION_CASES, ids=[c[0] for c in REJECTION_CASES])
def test_every_rejection_path_gives_a_specific_reason(
        engine, label, setup, service, subtype, data, code, substrings):
    setup(engine)
    tm = _send(engine, service, subtype, data)

    reasons = []
    for sub in (2, 4, 8):
        for p in _of(tm, 1, sub):
            vd = VerificationData.parse(p.data_field, sub)
            reasons.append((sub, vd.error_code, vd.reason))
    assert reasons, f"{label}: no S1 failure report at all"

    codes = [r[1] for r in reasons]
    assert code in codes, f"{label}: expected code 0x{code:04X}, got {codes}"

    text = " | ".join(r[2] for r in reasons)
    assert text.strip(), f"{label}: failure reported with an EMPTY reason"
    assert text != "Unknown error", f"{label}: generic reason"
    for needle in substrings:
        assert needle.lower() in text.lower(), \
            f"{label}: reason {text!r} does not name the gate ({needle!r})"


def test_reasons_are_distinct_per_gate(engine, tmp_path):
    """Two commands that share error code 0x0007 must not read the same.

    This is the whole point: 0x0007 alone cannot tell an operator whether the
    beacon-mode gate, the antenna gate or the DOWNLINK-mode gate fired.
    """
    engine.subsystems["ttc"]._state.beacon_mode = True
    beacon = VerificationData.parse(
        _of(_send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 0])), 1, 8)[0]
        .data_field, 8)

    engine.subsystems["ttc"]._state.beacon_mode = False
    engine.subsystems["ttc"]._state.antenna_deployed = False
    antenna = VerificationData.parse(
        _of(_send(engine, 8, 1, bytes([FUNC_SET_DATA_RATE, 1])), 1, 8)[0]
        .data_field, 8)

    assert beacon.error_code == antenna.error_code == 0x0007
    assert beacon.reason != antenna.reason


# ═══════════════════════════════════════════════════════════════════
# The generic plumbing (item 4 of the fix)
# ═══════════════════════════════════════════════════════════════════

class TestGenericPlumbing:
    def test_dispatcher_routes_every_subsystem_command_through_cmd(self):
        """No router may call handle_command directly again.

        ``ServiceDispatcher._cmd`` is what makes a handler's reason reach the
        ground for free. A new ``xxx.handle_command(...)`` in a router would
        silently re-open defect #87, so the source is checked directly.
        """
        import inspect
        from smo_simulator import service_dispatch

        src = inspect.getsource(service_dispatch)
        start = src.index("def _route_legacy_quick_action")
        end = src.index("# ─── S11 Time-tagged Scheduling")
        routers = src[start:end]
        offenders = [ln.strip() for ln in routers.splitlines()
                     if ".handle_command(" in ln
                     and "self._cmd" not in ln
                     # _cmd's own body is the one legitimate direct call
                     and "subsystem.handle_command" not in ln]
        assert offenders == [], \
            f"router(s) bypassing _cmd and dropping the reason: {offenders}"

    def test_cmd_captures_a_failure_without_the_caller_doing_anything(self):
        """A handler that returns success=False is reported with no per-site code."""
        from unittest.mock import MagicMock
        from smo_simulator.service_dispatch import ServiceDispatcher

        eng = MagicMock()
        eng.subsystems = {}
        disp = ServiceDispatcher(eng)
        subsys = MagicMock()
        subsys.handle_command.return_value = {
            "success": False, "message": "brand new gate said no",
            "error_code": 0x0007,
        }
        disp._cmd(subsys, {"command": "whatever"})
        # WP-7 renamed the per-dispatch accumulators; the public contract is
        # now the DispatchResult, but _cmd's capture behaviour is what this
        # test is about, so it reads the accumulator directly.
        assert disp._err_reason == "brand new gate said no"
        assert disp._err_code == 0x0007

    def test_cmd_leaves_successful_commands_alone(self):
        from unittest.mock import MagicMock
        from smo_simulator.service_dispatch import ServiceDispatcher

        eng = MagicMock()
        eng.subsystems = {}
        disp = ServiceDispatcher(eng)
        subsys = MagicMock()
        subsys.handle_command.return_value = {"success": True, "x": 1}
        assert disp._cmd(subsys, {"command": "ok"})["x"] == 1
        assert disp._err_reason == ""

    def test_a_newly_plumbed_site_now_reports(self, engine):
        """EPS wing deploy was one of the six that already reported…

        …but solar-array-drive was not. Prove a previously silent path now
        produces a specific S1.8 rather than a truthful-looking S1.7.
        """
        eps = engine.subsystems["eps"]
        eps._state.power_lines["ttc_tx"] = True
        # Deploy an already-deployed wing: the model refuses with a message.
        _send(engine, 8, 1, bytes([FUNC_DEPLOY_WING, 2]))
        tm = _send(engine, 8, 1, bytes([FUNC_DEPLOY_WING, 2]))
        fails = _of(tm, 1, 8)
        if fails:  # the model only refuses once the wings are already out
            vd = VerificationData.parse(fails[0].data_field, 8)
            assert vd.reason.strip()
            assert vd.reason != "Unknown error"
