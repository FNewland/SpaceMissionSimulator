"""Defect #87 — the MCS shows WHY a command was refused, not just "1,8".

The spacecraft now appends a failure notice to S1.2 / S1.4 / S1.8 and raises a
COMMAND_REJECTED S5 event. These tests drive the real telemetry path (wire
bytes in, display state out) and check the browser code that renders it.
"""
import re
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smo_common.config.schemas import MCSDisplayConfig
from smo_common.protocol.pus_services import (
    FAILURE_CODES, encode_failure_reason,
)
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX_HTML = (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs"
              / "static" / "index.html")

BEACON_REASON = "TM rate is forced to the beacon tier in beacon mode (LOW refused)"


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


def _s1_failure(subtype, tc_apid, tc_seq, code, reason):
    """Build the data field of an S1.2 / S1.8 failure report."""
    request_id = ((tc_apid & 0x7FF) << 14) | (tc_seq & 0x3FFF)
    return (struct.pack('>IH', request_id, code)
            + encode_failure_reason(reason))


def _s5_event(event_id, severity, text):
    aux = text.encode('ascii', 'ignore')
    return (struct.pack('>HB', event_id, severity) + struct.pack('>I', 0)
            + struct.pack('>B', len(aux)) + aux)


def _sent(srv, seq, name, service=8, subtype=1):
    srv._verification_log.appendleft({
        "seq": seq, "name": name, "service": service, "subtype": subtype,
        "state": "SENT", "timestamp": 0.0, "error_code": 0,
        "position": "spacecraft_controller",
    })


@pytest.mark.asyncio
class TestVerificationLogCarriesTheReason:
    async def _drain(self, srv, pkt):
        srv._archive = MagicMock()
        await srv._process_tm(pkt)

    async def test_s1_8_row_shows_code_meaning_and_reason(self):
        srv = _make_server()
        _sent(srv, 25, "TTC_SET_DATA_RATE")
        await self._drain(srv, _pkt(1, 8, _s1_failure(8, 1, 25, 0x0007,
                                                      BEACON_REASON)))
        row = srv._verification_log[0]
        assert row["state"] == "FAILED"
        assert row["error_code"] == 0x0007
        assert row["reason"] == BEACON_REASON
        assert row["error_text"] == FAILURE_CODES[0x0007]

    async def test_s1_2_row_shows_the_acceptance_reason(self):
        srv = _make_server()
        _sent(srv, 26, "PAYLOAD_CAPTURE")
        await self._drain(srv, _pkt(1, 2, _s1_failure(
            2, 1, 26, 0x0006,
            "S8.1 func 28 is not in the bootloader command subset")))
        row = srv._verification_log[0]
        assert row["state"] == "REJECTED"
        assert "bootloader" in row["reason"]
        assert row["error_text"] == FAILURE_CODES[0x0006]

    async def test_legacy_report_without_a_notice_still_updates_the_row(self):
        """A spacecraft that appends nothing must not regress the display."""
        srv = _make_server()
        _sent(srv, 27, "OLD_SPACECRAFT")
        await self._drain(srv, _pkt(1, 8, struct.pack('>IH', 27, 0x0001)))
        row = srv._verification_log[0]
        assert row["state"] == "FAILED"
        assert row["error_code"] == 0x0001
        assert row["reason"] == ""
        assert row["error_text"] == FAILURE_CODES[0x0001]

    async def test_success_rows_are_untouched(self):
        srv = _make_server()
        _sent(srv, 28, "PING")
        await self._drain(srv, _pkt(1, 1, struct.pack('>I', 28)))
        await self._drain(srv, _pkt(1, 7, struct.pack('>I', 28)))
        assert srv._verification_log[0]["state"] == "COMPLETED"
        assert srv._verification_log[0]["error_code"] == 0

    async def test_packet_metadata_broadcast_carries_reason_and_meaning(self):
        """The raw-packet log line the browser draws gets the same upgrade."""
        import json
        from unittest.mock import AsyncMock

        srv = _make_server()
        srv._archive = MagicMock()
        ws = MagicMock()
        ws.send_str = AsyncMock()
        srv._ws_clients = [(ws, None)]
        await srv._process_tm(
            _pkt(1, 8, _s1_failure(8, 1, 25, 0x0007, BEACON_REASON)))

        ws.send_str.assert_awaited()
        verif = json.loads(ws.send_str.await_args[0][0])["packet"]["verification"]
        assert verif["tc_seq"] == 25
        assert verif["error_code"] == 0x0007
        assert verif["reason"] == BEACON_REASON
        assert verif["error_text"] == FAILURE_CODES[0x0007]

    async def test_api_fills_defaults_for_rows_never_verified(self):
        srv = _make_server()
        _sent(srv, 29, "NEVER_ANSWERED")
        request = MagicMock()
        resp = await srv._handle_verification_log(request)
        import json
        rows = json.loads(resp.body.decode())["log"]
        assert rows[0]["reason"] == ""
        assert rows[0]["error_text"] == ""
        assert rows[0]["error_code"] == 0


@pytest.mark.asyncio
class TestRejectionEventReachesTheFdirJournal:
    async def _drain(self, srv, pkt):
        srv._archive = MagicMock()
        await srv._process_tm(pkt)

    async def test_command_rejected_event_is_journalled_with_its_name(self):
        srv = _make_server()
        text = f"S8.1 func 65 rejected at execution, err 0x0007: {BEACON_REASON}"
        await self._drain(srv, _pkt(5, 2, _s5_event(0x030B, 2, text)))
        journal = srv._fdir_model.journal()
        rows = [e for e in journal if e.get("event_id") == 0x030B]
        assert len(rows) == 1
        assert rows[0]["label"] == "COMMAND_REJECTED"
        assert rows[0]["subsystem"] == "obdh"
        assert "func 65" in rows[0]["description"]
        assert "beacon" in rows[0]["description"].lower()

    async def test_the_catalogue_gives_the_journal_a_real_label(self):
        """Without the catalogue entry the FDIR window shows EVT_0x030B."""
        srv = _make_server()
        await self._drain(srv, _pkt(5, 2, _s5_event(0x030B, 2, "x")))
        row = srv._fdir_model.journal()[0]
        assert not row["label"].startswith("EVT_0x")


@pytest.fixture(scope="module")
def html():
    return INDEX_HTML.read_text()


class TestBrowserRendering:
    """The verification table is the row the operator actually reads."""

    def test_verification_table_has_code_and_reason_columns(self, html):
        header = re.search(
            r'<thead><tr>(<th>Seq</th>.*?)</tr></thead>\s*'
            r'<tbody id="verif-tbody">', html, re.S)
        assert header, "verification log table header not found"
        cols = re.findall(r'<th>(.*?)</th>', header.group(1))
        assert cols == ["Seq", "Name", "Status", "Code", "Reason", "Pos"]

    def test_render_verif_log_emits_both_new_cells(self, html):
        body = re.search(r'function renderVerifLog\(\)\s*\{(.*?)\n\}', html, re.S)
        assert body
        src = body.group(1)
        assert "verifCodeText(v)" in src
        assert "v.reason" in src
        assert "verif-code" in src and "verif-reason" in src

    def test_code_helper_renders_hex_and_meaning(self, html):
        """The verification row's Code cell is "0x0007 — <meaning>".

        `verifCodeText` delegates the formatting to `failureCodeLabel`, which
        defect #88 made the single renderer for a failure code (the FDIR
        journal, the event log and the OBDH pane use the same one). Assert the
        pair of helpers between them still produce hex + meaning.
        """
        body = re.search(r'function verifCodeText\(v\)\s*\{(.*?)\n\}', html, re.S)
        assert body, "verifCodeText helper missing"
        assert "failureCodeLabel(v.error_code, v.error_text)" in body.group(1)
        label = re.search(r'function failureCodeLabel\(code, textOverride\)\s*\{(.*?)\n\}',
                          html, re.S)
        assert label, "failureCodeLabel helper missing"
        src = label.group(1)
        assert "textOverride" in src      # the meaning, as served
        assert "toString(16)" in src      # the code, as hex

    def test_event_log_line_carries_the_reason(self, html):
        assert "v.reason ? ' — ' + v.reason : ''" in html

    def test_command_history_row_was_upgraded_not_duplicated(self, html):
        body = re.search(r'async function renderCmdHistory\(\)\s*\{(.*?)\n\}',
                         html, re.S)
        assert body
        src = body.group(1)
        assert "verifCodeText(e)" in src
        assert "e.reason" in src
        assert ">Failure</th>" in src

    def test_no_second_hardcoded_copy_of_the_code_vocabulary(self, html):
        """The meanings come from the server (`error_text`), so they cannot drift.

        A JS object literal mapping numeric failure codes to text would be a
        second source of truth; there must not be one.
        """
        code_map = re.search(
            r'\{[^{}]*0x0007\s*:\s*[\'"]', html)
        assert code_map is None, \
            f"failure-code vocabulary duplicated in the browser: {code_map.group(0)!r}"
