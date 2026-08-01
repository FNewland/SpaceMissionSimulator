"""Defect #88 — a failure code an operator can actually see.

Franz:

    "How can I visualize the error codes? They are not shown in the telemetry
    window or the error window or the FDIR window."

Defect #87 put the code and the reason on the verification-log row and the
command-history row. Those are the two surfaces an operator looks at *while
sending a command*. The three surfaces Franz names — the parameter displays,
the event log and the FDIR journal — were not reached:

* the event log rendered ``EVT-779 (RID 0x030B): <sentence>``: no event NAME
  and no meaning for the code buried in the sentence;
* the FDIR journal filed a rejection as one more anonymous ``S5 EVENT`` row;
* no parameter carried a failure code at all — ``_last_error_code`` lived and
  died inside ``ServiceDispatcher``.

These tests drive the real telemetry path (wire bytes in, display state out)
and the browser code that renders it.
"""
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from smo_common.config.schemas import MCSDisplayConfig
from smo_common.protocol.pus_services import (
    COMMAND_REJECTED_EVENT_ID, FAILURE_CODES, MEMORY_FAILURE_CODES,
    format_command_rejection, parse_command_rejection,
)
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")

REASON = "TM rate is forced to the beacon tier in beacon mode (LOW refused)"
AUX = format_command_rejection("S8.1 func 65", "execution", 0x0007, REASON)


async def _event_metadata(srv, pkt) -> dict:
    """The `packet` metadata the server broadcasts over the WebSocket.

    `_process_tm` returns nothing — it builds the dict and pushes it to the
    clients — so the display's real input is the broadcast payload.
    """
    ws = MagicMock()
    ws.send_str = AsyncMock()
    srv._ws_clients = [(ws, None)]
    srv._archive = MagicMock()
    await srv._process_tm(pkt)
    ws.send_str.assert_awaited()
    for call in ws.send_str.await_args_list:
        msg = json.loads(call[0][0])
        if msg.get("type") == "packet" or "packet" in msg:
            return msg["packet"]
    raise AssertionError("no packet metadata broadcast")


def _journal(srv) -> list[dict]:
    return srv._fdir_model.display_data(active_alarms=[])["journal"]


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


def _pkt(service, subtype, data, apid=1, seq=0):
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _s5_event(event_id, severity, text):
    aux = text.encode("utf-8")[:120]
    return (struct.pack('>HB', event_id, severity) + struct.pack('>I', 0)
            + struct.pack('>B', len(aux)) + aux)


# ── one vocabulary, server-side ─────────────────────────────────────

class TestOneVocabulary:

    def test_the_aux_text_codec_round_trips(self):
        got = parse_command_rejection(AUX)
        assert got["identity"] == "S8.1 func 65"
        assert got["stage"] == "execution"
        assert got["error_code"] == 0x0007
        assert got["error_text"] == FAILURE_CODES[0x0007]
        assert got["reason"] == REASON

    def test_a_rejection_with_no_reason_still_parses(self):
        got = parse_command_rejection(
            format_command_rejection("S17.1", "acceptance", 0x0006, ""))
        assert got["error_code"] == 0x0006
        assert got["reason"] == ""
        assert got["error_text"] == FAILURE_CODES[0x0006]

    def test_a_memory_failure_reads_with_the_s6_vocabulary(self):
        got = parse_command_rejection(
            format_command_rejection("S6.2", "execution", 0x0002,
                                     "S6 MEM_LOAD: read-only region"))
        assert got["error_text"] == MEMORY_FAILURE_CODES[0x0002]

    def test_an_ordinary_event_text_is_not_mistaken_for_a_rejection(self):
        assert parse_command_rejection("Battery voltage below threshold") is None
        assert parse_command_rejection("") is None

    @pytest.mark.asyncio
    async def test_the_endpoint_serves_the_shared_map(self):
        srv = _make_server()
        resp = await srv._handle_failure_codes(MagicMock())
        body = json.loads(resp.body.decode())
        assert body["codes"] == {f"0x{c:04X}": t for c, t in FAILURE_CODES.items()}
        assert body["memory_codes"] == {f"0x{c:04X}": t
                                        for c, t in MEMORY_FAILURE_CODES.items()}
        assert body["command_rejected_event_id"] == "0x030B"


# ── the event / error surface ───────────────────────────────────────

@pytest.mark.asyncio
class TestEventSurface:

    async def test_a_rejection_event_carries_name_code_meaning_and_reason(self):
        srv = _make_server()
        pkt_meta = await _event_metadata(srv, _pkt(5, 1, _s5_event(0x030B, 2, AUX)))
        evt = pkt_meta["event"]
        assert evt["event_id"] == COMMAND_REJECTED_EVENT_ID
        assert evt["name"] == "COMMAND_REJECTED"
        rej = evt["rejection"]
        assert rej["error_code"] == 0x0007
        assert rej["error_text"] == FAILURE_CODES[0x0007]
        assert rej["reason"] == REASON
        assert rej["identity"] == "S8.1 func 65"

    async def test_an_ordinary_event_gets_a_name_but_no_rejection(self):
        srv = _make_server()
        pkt_meta = await _event_metadata(
            srv, _pkt(5, 1, _s5_event(0x0309, 2, "S11 TC scheduling queue full")))
        evt = pkt_meta["event"]
        assert evt["name"] == "TC_QUEUE_OVERFLOW"
        assert "rejection" not in evt

    async def test_an_uncatalogued_event_does_not_break_the_decode(self):
        srv = _make_server()
        pkt_meta = await _event_metadata(srv, _pkt(5, 1, _s5_event(0xEEEE, 1, "mystery")))
        assert pkt_meta["event"]["name"] == ""
        assert pkt_meta["event"]["description"] == "mystery"


# ── the FDIR window's journal ───────────────────────────────────────

@pytest.mark.asyncio
class TestFdirJournal:

    async def test_a_rejection_is_its_own_journal_kind(self):
        srv = _make_server()
        srv._archive = MagicMock()
        await srv._process_tm(_pkt(5, 1, _s5_event(0x030B, 2, AUX)))
        journal = _journal(srv)
        rows = [e for e in journal if e["kind"] == "cmd_rej"]
        assert len(rows) == 1, f"no cmd_rej row in {journal}"
        row = rows[0]
        assert row["label"] == "COMMAND_REJECTED"
        assert row["event_id"] == COMMAND_REJECTED_EVENT_ID
        assert row["error_code"] == 0x0007
        assert row["error_text"] == FAILURE_CODES[0x0007]
        assert row["reason"] == REASON
        assert row["stage"] == "execution"

    async def test_rejections_sit_alongside_s12_transitions(self):
        """The journal is one timeline: S12, S19 and now TC rejections."""
        srv = _make_server()
        srv._archive = MagicMock()
        srv._fdir_model.ingest_s12_transition(
            {"mon_id": 1, "param_id": 0x0100, "entered": True, "value": 1.0,
             "low_limit": 2.0, "high_limit": 9.0, "severity": 3})
        await srv._process_tm(_pkt(5, 1, _s5_event(0x030B, 2, AUX)))
        kinds = {e["kind"] for e in _journal(srv)}
        assert {"cmd_rej", "s12_in"} <= kinds

    async def test_an_ordinary_event_is_still_kind_event(self):
        srv = _make_server()
        srv._archive = MagicMock()
        await srv._process_tm(
            _pkt(5, 1, _s5_event(0x0309, 2, "S11 TC scheduling queue full")))
        assert [e["kind"] for e in _journal(srv)] == ["event"]


# ── the telemetry (parameter) window ────────────────────────────────

class TestLastFailureCodeIsATelemetryParameter:

    def test_the_parameter_is_declared(self):
        srv = _make_server()
        meta = srv._param_meta.get(0x0315)
        assert meta, "0x0315 obdh.last_tc_error_code is not in parameters.yaml"
        assert meta["name"] == "obdh.last_tc_error_code"

    def test_the_parameter_is_in_a_periodic_hk_structure(self):
        """It must arrive without being asked for, like the counter it explains."""
        structures = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "hk_structures.yaml").read_text())
        rows = structures["structures"] if isinstance(structures, dict) else structures
        carrying = [s["sid"] for s in rows
                    if any(int(p["param_id"]) == 0x0315
                           for p in (s.get("parameters") or []))]
        assert carrying, \
            "0x0315 is in no HK structure — it would never be downlinked"
        # It belongs with the TC counters it explains (SID 4, Platform/OBC).
        assert 4 in carrying

    def test_the_obdh_model_publishes_it(self):
        from smo_simulator.models.obdh_basic import OBDHBasicModel
        model = OBDHBasicModel()
        model.record_tc_failure_code(0x0007)
        assert model._state.last_tc_error_code == 0x0007
        params: dict = {}
        model.tick(1.0, None, params)
        assert params[0x0315] == 0x0007

    def test_a_reboot_clears_it(self):
        from smo_simulator.models.obdh_basic import OBDHBasicModel
        model = OBDHBasicModel()
        model.record_tc_failure_code(0x0007)
        model._reboot()
        assert model._state.last_tc_error_code == 0

    def test_a_rejection_latches_the_code_through_the_engine(self, tmp_path):
        """End to end: the engine's one rejection choke point writes telemetry."""
        engine = MagicMock()
        obdh = MagicMock()
        engine.subsystems = {"obdh": obdh}
        from smo_simulator.engine import SimulationEngine
        SimulationEngine._emit_command_rejected(
            engine, 8, 1, b'\x41\x00', 0x0007, REASON, "execution")
        obdh.record_tc_failure_code.assert_called_once_with(0x0007)


# ── the browser ─────────────────────────────────────────────────────

def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(INDEX), str(snippet_file)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@requires_node
class TestBrowserRendering:

    def test_the_event_log_line_names_the_event_and_decodes_the_code(self, tmp_path):
        out = run_js("""
          FAILURE_CODE_TEXT = {'0x0007': 'Not permitted in the current state'};
          const ev = { event_id: 0x030B, severity: 2, name: 'COMMAND_REJECTED',
            description: 'raw aux text',
            rejection: { identity: 'S8.1 func 65', stage: 'execution',
                         error_code: 7,
                         error_text: 'Not permitted in the current state',
                         reason: 'TM rate is forced to the beacon tier' } };
          return { line: eventLogText(ev), plain: eventLogText(
            { event_id: 0x0309, severity: 2, name: 'TC_QUEUE_OVERFLOW',
              description: 'S11 TC scheduling queue full' }) };
        """, tmp_path)
        line = out["line"]
        assert "0x030B" in line and "COMMAND_REJECTED" in line
        assert "0x0007" in line                       # the code
        assert "Not permitted in the current state" in line   # its meaning
        assert "TM rate is forced to the beacon tier" in line  # the reason
        assert "S8.1 func 65" in line                 # which command
        assert out["plain"] == "0x0309 TC_QUEUE_OVERFLOW: S11 TC scheduling queue full"

    def test_the_fdir_journal_row_shows_code_meaning_and_reason(self, tmp_path):
        out = run_js("""
          FAILURE_CODE_TEXT = {};
          return fdirJournalText({
            kind: 'cmd_rej', label: 'COMMAND_REJECTED', event_id: 0x030B,
            identity: 'S8.1 func 65', stage: 'execution', error_code: 7,
            error_text: 'Not permitted in the current state',
            reason: 'beacon mode latched' });
        """, tmp_path)
        assert "COMMAND_REJECTED" in out and "0x030B" in out
        assert "0x0007 — Not permitted in the current state" in out
        assert "beacon mode latched" in out
        assert "S8.1 func 65" in out

    def test_the_journal_kind_has_a_filter_and_a_label(self, tmp_path):
        out = run_js("return FDIR_JOURNAL_KINDS.cmd_rej;", tmp_path)
        assert out == ["TC REJECT", "fdir-jf-cmdrej"]
        assert 'id="fdir-jf-cmdrej"' in INDEX.read_text()

    def test_the_vocabulary_is_fetched_not_hardcoded(self, tmp_path):
        out = run_js("""
          fetch = async () => ({ json: async () => ({
            codes: { '0x0004': 'Subsystem unpowered or unreachable' } }) });
          return loadFailureCodes().then(() => ({
            decoded: failureCodeLabel(4),
            unknown: failureCodeLabel(0x00AB),
            none: failureCodeLabel(null)
          }));
        """, tmp_path)
        assert out["decoded"] == "0x0004 — Subsystem unpowered or unreachable"
        assert out["unknown"] == "0x00AB"     # never invents a meaning
        assert out["none"] == ""

    def test_the_browser_holds_no_second_copy_of_the_vocabulary(self):
        html = INDEX.read_text()
        # A JS object literal mapping a numeric failure code to text would be a
        # second source of truth. FAILURE_CODE_TEXT must start empty.
        assert re.search(r'\{[^{}]*0x0007\s*:\s*[\'"]', html) is None
        assert "let FAILURE_CODE_TEXT = {};" in html

    def test_the_obdh_pane_renders_the_last_failure_code(self, tmp_path):
        out = run_js("""
          FAILURE_CODE_TEXT = {'0x0007': 'Not permitted in the current state'};
          updateOBDHSVG = () => {};   // the DOM stub has no SVG className
          state.obdh = {};
          state.params = { '0x0304': 12, '0x0305': 11, '0x0306': 1, '0x0315': 7 };
          updateOBDH();
          const el = document.getElementById('obdh-last-tc-err');
          return { err: el.textContent, cls: el.className,
                   rx: document.getElementById('obdh-tc-rx').textContent,
                   rej: document.getElementById('obdh-tc-rej').textContent };
        """, tmp_path)
        assert out["err"] == "0x0007 — Not permitted in the current state"
        assert "val-warn" in out["cls"]
        assert out["rx"] == "12" and out["rej"] == "1"

    def test_the_obdh_pane_says_none_rather_than_showing_a_bare_zero(self, tmp_path):
        out = run_js("""
          updateOBDHSVG = () => {};   // the DOM stub has no SVG className
          state.obdh = {};
          state.params = { '0x0315': 0 };
          updateOBDH();
          const el = document.getElementById('obdh-last-tc-err');
          return { err: el.textContent, cls: el.className };
        """, tmp_path)
        assert out["err"] == "none since boot"
        assert "val-nom" in out["cls"]

    def test_subsystem_event_routing_survived_the_new_log_format(self, tmp_path):
        """The old router parsed 'EVT-nnn:' out of the rendered text."""
        out = run_js("""
          subsysEvents.obdh = [];
          routeEventToSubsystem('0x030B COMMAND_REJECTED: refused', 0x030B);
          return subsysEvents.obdh.length;
        """, tmp_path)
        assert out == 1
