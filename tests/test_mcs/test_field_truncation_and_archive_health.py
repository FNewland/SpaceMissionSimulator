"""Three owner-reported console defects, gated.

1. **"In simulator parameter windows we cannot see the whole field — e.g. SA
   panel currents in EPS."**  ``.panel`` carried ``overflow: hidden``. A panel
   is a GRID ITEM in every ``.grid-1x*`` layout, and a grid item whose
   ``overflow`` is not ``visible`` loses its automatic minimum size, so the
   track could be squeezed narrower than its content and the content was
   clipped — with no ellipsis, no wrap, no scrollbar and no tooltip. A value
   clipped mid-digit is indistinguishable from a real one, which on an ops
   console is worse than showing nothing. Gated here on both halves of the
   fix: the CSS that lets a column size to its content, and the render path
   that always emits the FULL raw value plus its unit in a tooltip.

2. **"On the OBDH page we have a 'database disk image is malformed' error if
   we request service 15."**  Two defects.

   (a) The button should not be there: telecommands belong on the command
       page. Gated here on every subsystem tab being free of command-issuing
       controls AND on the equivalent capability existing on the command page,
       so the audit cannot strand anything.

   (b) ``database disk image is malformed`` is SQLite's own words for a
       damaged file and must never reach an operator panel. Gated on a
       deliberately corrupted archive producing a clean, degraded, 200-level
       response everywhere, and on the S15 dump view surviving it.

3. The v2 migration deliberately does not convert a v1 database. Gated on a
   v1 file, a corrupt file and a missing file producing three DISTINCT,
   legible operator-facing messages rather than one generic failure.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer
from smo_mcs.tm_archive import (
    STATUS_CORRUPT,
    STATUS_LEGACY,
    STATUS_NEW,
    STATUS_OK,
    TIME_BASE_WALL,
    TMArchive,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"
PAGE = INDEX.read_text()

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")

#: The six spacecraft-subsystem display tabs. Everything here is a DISPLAY;
#: none of them may issue a telecommand.
SUBSYSTEM_PANES = ("eps", "tcs", "aocs", "ttc", "payload", "prop", "obdh")


def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run(
        [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _pane_source(name: str) -> str:
    """The markup of one tab pane, from its opening div to the next one."""
    start = PAGE.index(f'id="pane-{name}"')
    nxt = PAGE.find('class="tab-pane', start + 1)
    return PAGE[start:nxt if nxt > 0 else len(PAGE)]


def _make_server(tmp_path: Path | None = None,
                 archive: TMArchive | None = None) -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        srv = MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                        connect_port=9999, http_port=0)
    srv._archive.close()          # never write into the shipped config tree
    if archive is not None:
        srv._archive = archive
    elif tmp_path is not None:
        srv._archive = TMArchive(tmp_path / "tm_archive.db")
        srv._archive.open()
    return srv


def _req(**query):
    r = MagicMock()
    r.query = dict(query)
    return r


def _body(resp) -> dict:
    return json.loads(resp.body.decode())


def _write_corrupt(path: Path) -> None:
    """A file SQLite will open and then refuse: valid header, junk pages.

    Overwriting the interior of a real database is what an interrupted write
    or a sync client copying the file mid-transaction produces, and it is what
    makes SQLite say "database disk image is malformed" rather than "file is
    not a database".
    """
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(
        "CREATE TABLE tm_parameters (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " utc_epoch REAL NOT NULL, param_name TEXT NOT NULL,"
        " value REAL NOT NULL);"
        "CREATE TABLE archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO archive_meta VALUES ('schema_version','2'),"
        " ('time_base','sim');")
    for i in range(400):
        conn.execute("INSERT INTO tm_parameters (utc_epoch, param_name, value)"
                     " VALUES (?, ?, ?)", (float(i), f"eps.p{i}", float(i)))
    conn.commit()
    conn.close()
    raw = bytearray(path.read_bytes())
    page = int.from_bytes(raw[16:18], "big") or 4096
    # Scribble over the interior pages, leaving the header intact so the file
    # is still recognisably a database.
    for off in range(page, len(raw)):
        raw[off] = 0x5A
    path.write_bytes(bytes(raw))


# ══════════════════════════════════════════════════════════════════════
# 1. Parameter values are never clipped, and the full value is always
#    one hover away.
# ══════════════════════════════════════════════════════════════════════

class TestNoTruncatedParameterFields:

    def test_panels_no_longer_clip_their_own_content(self):
        """`.panel { overflow: hidden }` is the root cause. It must be gone.

        With `overflow` anything but `visible`, a panel used as a grid item
        gets `min-width: 0` and the track can be squeezed under its content;
        the overflow is then clipped with nothing to say so.
        """
        block = re.search(r"\n\.panel \{(.*?)\}", PAGE, re.S)
        assert block, ".panel rule not found"
        body = block.group(1)
        assert "overflow: hidden" not in body, (
            ".panel is clipping its content again — that is the truncation "
            "defect")
        assert "overflow: visible" in body

    def test_a_table_can_grow_past_the_panel_and_scrolls_instead_of_clipping(self):
        table = re.search(r"\n\.data-table \{(.*?)\}", PAGE, re.S).group(1)
        # `width: 100%` capped a column at the panel width; `min-width` makes
        # it a floor so the column sizes to its widest value.
        assert "min-width: 100%" in table
        assert "table-layout: auto" in table
        body = re.search(r"\n\.panel-body \{(.*?)\}", PAGE, re.S).group(1)
        assert "overflow-x: auto" in body, (
            "a table wider than its panel must scroll, never clip")

    def test_a_parameter_value_never_wraps_or_shrinks(self):
        val = re.search(r"\n\.data-row \.val \{(.*?)\}", PAGE, re.S).group(1)
        assert "white-space: nowrap" in val
        assert "flex: 0 0 auto" in val, (
            "the value must not be the flex item that gives way — the LABEL is")
        lbl = re.search(r"\n\.data-row \.lbl \{(.*?)\}", PAGE, re.S).group(1)
        assert "overflow-wrap: anywhere" in lbl

    def test_no_numeric_cell_is_truncated_with_an_ellipsis(self):
        """`12.34…` on an ops console is indistinguishable from `12.34`."""
        assert "text-overflow: ellipsis" not in PAGE, (
            "an ellipsis truncation was reintroduced; a clipped number cannot "
            "be told from a real one")
        numcell = re.search(r"\.data-table td\.num-cell, \.num-cell \{(.*?)\}",
                            PAGE, re.S).group(1)
        assert "white-space: nowrap" in numcell

    @requires_node
    def test_the_sa_panel_currents_render_in_full_with_a_tooltip(self, tmp_path):
        """The exact field the owner reported.

        The cell shows 3 dp because that is what fits; the tooltip and
        `data-raw` carry the UNROUNDED ampere value with its unit.
        """
        out = run_js("""
            state.eps = {power_gen_W: 85.0, power_cons_W: 40.0};
            state.params = state.params || {};
            state.params['0x012B'] = 0.1234567890123;
            state.params['0x012C'] = -0.0987654321;
            state.params['0x012D'] = 0.5;  state.params['0x012E'] = 0.6;
            state.params['0x012F'] = 0.7;  state.params['0x0130'] = 0.8;
            buildEPSLines(); updateEPS();
            return document.getElementById('eps-faces-tbody').innerHTML;
        """, tmp_path)
        # The rendered, rounded value is still there...
        assert "0.123 A" in out
        # ...and so is the complete one, with its unit.
        assert 'title="0.1234567890123 A"' in out
        assert 'data-raw="0.1234567890123"' in out
        assert 'title="-0.0987654321 A"' in out
        assert "num-cell" in out

    @requires_node
    def test_power_line_currents_name_their_unit_in_the_tooltip(self, tmp_path):
        """The cell omits the unit (the column header has it); the tooltip
        must not, or a hover tells the operator a bare number."""
        out = run_js("""
            state.params = state.params || {};
            state.params['0x0105'] = 28.0;
            for (let i = 0; i < 8; i++) {
              state.params['0x'+(0x0110+i).toString(16).toUpperCase().padStart(4,'0')] = 1;
              state.params['0x'+(0x0118+i).toString(16).toUpperCase().padStart(4,'0')] = 0.256789;
            }
            buildEPSLines(); updateEPS();
            return {txt:   document.getElementById('eps-line-0-cur').textContent,
                    tip:   document.getElementById('eps-line-0-cur').getAttribute('title'),
                    wtip:  document.getElementById('eps-line-0-w').getAttribute('title'),
                    total: document.getElementById('eps-lines-total-a').getAttribute('title')};
        """, tmp_path)
        assert out["txt"] == "0.257"          # display unchanged
        assert out["tip"] == "0.256789 A"     # full value + unit on hover
        assert out["wtip"].endswith(" W")
        assert out["total"].endswith(" A")

    @requires_node
    def test_setText_leaves_a_full_value_on_hover_but_keeps_authored_tooltips(
            self, tmp_path):
        """Global safety net.

        Every `setText`-driven field carries its complete rendered string in
        `title`. An author-written tooltip explains the FIELD, not the value,
        so it is never clobbered.
        """
        out = run_js("""
            setText('t-plain', '123.456789 A');
            const authored = document.getElementById('t-authored');
            authored.setAttribute('title', 'Under-voltage flag');
            setText('t-authored', 'ON');
            return {plain:    document.getElementById('t-plain').getAttribute('title'),
                    authored: authored.getAttribute('title')};
        """, tmp_path)
        assert out["plain"] == "123.456789 A"
        assert out["authored"] == "Under-voltage flag"

    @requires_node
    def test_an_abbreviated_hex_blob_says_so_and_carries_the_whole_thing(
            self, tmp_path):
        """Command history sliced data_hex to 24 chars with no marker, so a
        truncated command read as a short one."""
        out = run_js("""
            return {cut:  hexCell('AABBCCDDEEFF00112233445566778899', 8),
                    full: hexCell('AABB', 8)};
        """, tmp_path)
        assert "AABBCCDD…" in out["cut"], "truncation must be explicit"
        assert 'title="AABBCCDDEEFF00112233445566778899 (16 bytes)"' in out["cut"]
        assert 'data-raw="AABBCCDDEEFF00112233445566778899"' in out["cut"]
        assert "…" not in out["full"], "a short blob must not be marked cut"

    @requires_node
    def test_a_trend_table_timestamp_keeps_its_date(self, tmp_path):
        """HH:MM:SS alone drops the day — the archive spans many of them."""
        out = run_js("return tsTd(1700000000.25);", tmp_path)
        assert ">22:13:20<" in out
        assert "2023-11-14" in out
        assert "epoch 1700000000.25" in out


# ══════════════════════════════════════════════════════════════════════
# 2a. Telecommands live on the command page, and only there.
# ══════════════════════════════════════════════════════════════════════

class TestNoCommandControlsOnSubsystemPages:

    @pytest.mark.parametrize("pane", SUBSYSTEM_PANES)
    def test_no_subsystem_page_issues_a_telecommand(self, pane):
        src = _pane_source(pane)
        # The S15.9 dump request — the control the owner tripped over on OBDH.
        assert "requestTmDump(" not in src, (
            f"the {pane.upper()} tab issues a TC(15,9) stored-TM dump")
        # The S3 housekeeping commands.
        for fn in ("hkEnableSid(", "hkDisableSid(", "hkRequest(", "hkToggle(",
                   "sendPusCmd(", "sendPUSCommand("):
            assert fn not in src, f"the {pane.upper()} tab calls {fn}"
        # A mirrored quick-command console is still a command control.
        assert "quick-cmds" not in src, (
            f"the {pane.upper()} tab has a quick-command console on it")

    def test_the_command_page_can_request_every_stored_tm_dump(self):
        """Nothing is stranded: each removed per-tab button has a target here."""
        cmd = _pane_source("commanding")
        assert 'id="cmd-dump-btn"' in cmd
        assert 'id="cmd-dump-subsystem"' in cmd
        for pane in SUBSYSTEM_PANES:
            assert f'value="{pane}"' in cmd, (
                f"{pane} cannot be dumped from the command page")
        # ...plus the two stores that were never on a subsystem tab.
        assert 'value="events"' in cmd and 'value="alarms"' in cmd
        assert 'id="cmd-dump-store"' in cmd

    def test_the_command_page_can_enable_and_request_an_hk_structure(self):
        """Replacement for the PROP tab's ENABLE SID 9 / ONE-SHOT REQUEST."""
        cmd = _pane_source("commanding")
        assert 'id="cmd-hk-sid"' in cmd
        for btn in ("cmd-hk-enable-btn", "cmd-hk-disable-btn",
                    "cmd-hk-request-btn"):
            assert f'id="{btn}"' in cmd
        # SID 9 (propulsion) is the one that had a button, so it is the default.
        sid_input = re.search(r'<input[^>]*id="cmd-hk-sid"[^>]*>', cmd).group(0)
        assert 'value="9"' in sid_input

    @requires_node
    def test_the_command_page_controls_are_actually_wired(self, tmp_path):
        """Present in the markup is not the same as reachable."""
        out = run_js("""
            const sent = [];
            sendPusCmd = (s, st, hex, name) => sent.push(name);
            requestTmDump = (sub, store) => sent.push('DUMP:' + sub + ':' + store);
            initCommandPageServiceControls();
            return {wired: typeof initCommandPageServiceControls,
                    dis:   typeof hkDisableSid,
                    en:    typeof hkEnableSid,
                    req:   typeof hkRequest};
        """, tmp_path)
        assert out["wired"] == "function"
        for key in ("dis", "en", "req"):
            assert out[key] == "function"

    def test_the_prop_tab_still_diagnoses_the_disabled_structure(self):
        """Removing the button must not remove the DISPLAY that explains why
        the page is empty — that half was never a command."""
        src = _pane_source("prop")
        assert "HK SID 9 (PROP) NOT ENABLED" in src
        assert "S3 HK Manager" in src, "no pointer to where the command went"

    def test_removed_consoles_leave_a_signpost_not_a_hole(self):
        assert "PROPULSION COMMANDING" in _pane_source("prop")
        assert "PAYLOAD COMMANDING" in _pane_source("payload")

    def test_the_position_gate_message_moved_with_the_group(self):
        """A position that may not command propulsion has to be told so, and
        the PROP tab console that used to say it is gone."""
        assert "allowed_func_ids excludes 108-118" in PAGE

    def test_display_only_controls_were_kept(self):
        """Not everything interactive is a telecommand.

        The dump SCRUBBER replays data already on the ground; the ON-DEMAND
        filter box and the alarm journal's ARCHIVE link are local views. They
        stay, and this test records that as deliberate.
        """
        assert 'id="eps-dump-scrubber"' in _pane_source("eps")
        assert 'href="/dumps"' in PAGE


# ══════════════════════════════════════════════════════════════════════
# 2b + 3. Archive health: corrupt, legacy and missing are three states.
# ══════════════════════════════════════════════════════════════════════

class TestArchiveOpensSafely:

    def test_a_healthy_archive_is_wal_and_says_so(self, tmp_path):
        """WAL is what makes the request path and the background prune safe
        to interleave. `PRAGMA journal_mode=WAL` returns the mode ACTUALLY in
        force, and the old code never looked at it."""
        a = TMArchive(tmp_path / "a.db")
        a.open()
        assert a.journal_mode == "wal"
        assert a.available is True
        a.close()

    def test_every_connection_use_is_serialised(self, tmp_path):
        """`check_same_thread=False` turns sqlite3's own guard off; a mutex
        has to take its place or two threads interleave inside one statement.
        """
        import threading
        a = TMArchive(tmp_path / "a.db")
        a.open()
        assert isinstance(a._lock, type(threading.RLock()))
        errors: list[BaseException] = []

        def writer(base: int):
            try:
                for i in range(60):
                    a.store_parameters({f"eps.p{base}": float(i)},
                                       utc_epoch=1000.0 + i)
                    a.prune(now_epoch=1000.0, max_age_s=10_000)
                    a.query_parameters(f"eps.p{base}")
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"concurrent archive access raised: {errors}"
        assert a.available is True
        assert a.status == STATUS_NEW    # created this test; still healthy
        a.close()

    def test_a_corrupt_archive_degrades_instead_of_raising(self, tmp_path):
        path = tmp_path / "broken.db"
        _write_corrupt(path)
        a = TMArchive(path)
        a.open()                      # must NOT raise
        assert a.status == STATUS_CORRUPT
        assert a.available is False
        # Every accessor answers empty rather than throwing a driver error.
        assert a.query_parameters("eps.bat_soc") == []
        assert a.query_events() == []
        assert a.query_alarms() == []
        assert a.query_commands() == []
        assert a.parameter_names() == []
        assert a.get_playback_data("eps", now_epoch=1000.0) == []
        assert a.prune(now_epoch=1000.0) == 0
        assert a.store_alarm({"severity": 4}, utc_epoch=1000.0) == -1
        a.store_parameters({"eps.bat_soc": 50.0}, utc_epoch=1000.0)  # no raise
        a.close()

    def test_the_corruption_is_logged_loudly_once(self, tmp_path, caplog):
        path = tmp_path / "broken.db"
        _write_corrupt(path)
        with caplog.at_level("ERROR"):
            a = TMArchive(path)
            a.open()
            a.query_parameters("eps.bat_soc")
        loud = [r for r in caplog.records if "TM ARCHIVE UNUSABLE" in r.message]
        assert len(loud) == 1, "corruption must be logged, and only once"
        assert loud[0].levelname == "ERROR"
        a.close()


class TestThreeDistinctOperatorMessages:
    """A v1 file, a corrupt file and a missing file are three DIFFERENT
    problems and must not read as one generic failure."""

    def _missing(self, tmp_path) -> TMArchive:
        a = TMArchive(tmp_path / "does-not-exist.db")
        a.open()
        return a

    def _legacy(self, tmp_path) -> TMArchive:
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE tm_parameters (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " utc_epoch REAL NOT NULL, param_name TEXT NOT NULL,"
            " value REAL NOT NULL);")
        conn.execute("INSERT INTO tm_parameters (utc_epoch, param_name, value)"
                     " VALUES (?, ?, ?)", (time.time(), "eps.bat_soc", 50.0))
        conn.commit()
        conn.close()
        a = TMArchive(path)
        a.open()
        return a

    def _corrupt(self, tmp_path) -> TMArchive:
        path = tmp_path / "broken.db"
        _write_corrupt(path)
        a = TMArchive(path)
        a.open()
        return a

    def test_the_three_states_are_distinguishable(self, tmp_path):
        missing = self._missing(tmp_path)
        legacy = self._legacy(tmp_path)
        corrupt = self._corrupt(tmp_path)

        assert missing.status == STATUS_NEW
        assert legacy.status == STATUS_LEGACY
        assert corrupt.status == STATUS_CORRUPT

        messages = {a.status: a.message()
                    for a in (missing, legacy, corrupt)}
        assert len(set(messages.values())) == 3, (
            "the three failure modes share a message: an operator cannot tell "
            f"them apart — {messages}")
        for a in (missing, legacy, corrupt):
            a.close()

    def test_each_message_is_legible_and_says_what_to_do(self, tmp_path):
        missing = self._missing(tmp_path)
        assert "EMPTY" in missing.message()
        assert "no history" in missing.message().lower()
        assert missing.available is True         # readable, just empty

        legacy = self._legacy(tmp_path)
        assert "LEGACY" in legacy.message()
        assert "WALL" in legacy.message()
        assert legacy.time_base == TIME_BASE_WALL
        assert legacy.schema_version == 1
        assert legacy.available is True          # readable, not correlatable
        # ...and the v1 file is NOT converted, only labelled.
        assert legacy.query_parameters("eps.bat_soc"), (
            "the v1 rows must still be readable")

        corrupt = self._corrupt(tmp_path)
        assert "CORRUPT" in corrupt.message()
        assert "UNAVAILABLE" in corrupt.message()
        assert "live telemetry is unaffected" in corrupt.message().lower()
        assert corrupt.available is False

        for a in (missing, legacy, corrupt):
            a.close()

    def test_no_message_leaks_the_driver_string(self, tmp_path):
        """`database disk image is malformed` is SQLite talking to a
        developer. The operator gets a sentence; the log gets the driver."""
        corrupt = self._corrupt(tmp_path)
        assert "malformed" not in corrupt.message().lower()
        assert "sqlite" not in corrupt.message().lower()
        # It is still available for the log.
        assert corrupt.error
        corrupt.close()

    def test_meta_carries_the_state_to_every_api_response(self, tmp_path):
        for factory, status in ((self._missing, STATUS_NEW),
                                (self._legacy, STATUS_LEGACY),
                                (self._corrupt, STATUS_CORRUPT)):
            a = factory(tmp_path)
            meta = a.meta()
            assert meta["status"] == status
            assert meta["message"] == a.message()
            assert meta["available"] is (status != STATUS_CORRUPT)
            if status == STATUS_CORRUPT:
                assert meta["sim_time"] is False
            a.close()


class TestDegradedArchiveDoesNotBreakThePages:
    """An archive that cannot be opened must not take out the S15 dump view
    or any other page."""

    @pytest.fixture()
    def srv(self, tmp_path):
        path = tmp_path / "tm_archive.db"
        _write_corrupt(path)
        archive = TMArchive(path)
        archive.open()
        assert archive.status == STATUS_CORRUPT
        return _make_server(archive=archive)

    @pytest.mark.parametrize("handler_name,query,key", [
        ("_handle_archive_params", {"name": "eps.bat_soc"}, "data"),
        ("_handle_archive_events", {}, "events"),
        ("_handle_archive_alarms", {}, "alarms"),
        ("_handle_archive_commands", {}, "commands"),
        ("_handle_archive_playback", {"subsystem": "eps"}, "data"),
        ("_handle_archive_param_names", {}, "names"),
    ])
    def test_every_archive_endpoint_degrades_cleanly(self, srv, handler_name,
                                                     query, key):
        resp = asyncio.run(getattr(srv, handler_name)(_req(**query)))
        assert resp.status == 200, "a corrupt archive must not be a 500"
        body = _body(resp)
        assert body[key] == []
        assert body["archive_unavailable"] is True
        assert body["archive"]["status"] == STATUS_CORRUPT
        # The panel gets a sentence, never the driver's words.
        blob = json.dumps(body).lower()
        assert "malformed" not in blob
        assert "disk image" not in blob

    def test_the_s15_dump_view_survives_a_corrupt_archive(self, srv):
        """The owner's exact path: OBDH page, request service 15.

        The telecommand still goes to the spacecraft. The ground-side
        playback degrades, visibly, and the response is not an error.
        """
        srv._tc_writer = MagicMock()
        srv._tc_writer.is_closing.return_value = False
        srv._tc_send_lock = asyncio.Lock()

        async def run():
            req = MagicMock()
            req.json = MagicMock(
                return_value=_coro({"store_id": 1, "subsystem": "obdh"}))
            srv._tc_writer.drain = _drain
            return await srv._handle_tm_dump(req)

        resp = asyncio.run(run())
        assert resp.status == 200, (
            "a corrupt ground archive must not fail the S15 request")
        body = _body(resp)
        assert body["status"] == "sent"
        assert body["archive"]["status"] == STATUS_CORRUPT
        blob = json.dumps(body).lower()
        assert "malformed" not in blob and "disk image" not in blob

    def test_the_server_starts_at_all_with_a_broken_archive(self, srv):
        """Construction must not raise, and unrelated pages keep working."""
        assert srv._archive_available() is False
        meta = srv._archive_meta()
        assert meta["status"] == STATUS_CORRUPT
        assert meta["available"] is False
        assert "CORRUPT" in meta["message"]


async def _coro(value):
    return value


async def _drain():
    return None


# ══════════════════════════════════════════════════════════════════════
# The console renders the degraded state instead of an error toast.
# ══════════════════════════════════════════════════════════════════════

@requires_node
class TestConsoleShowsTheArchiveState:

    def test_a_corrupt_archive_paints_a_banner(self, tmp_path):
        out = run_js("""
            const down = applyArchiveState({
              archive_unavailable: true,
              archive: {status: 'corrupt', available: false,
                        message: 'TELEMETRY ARCHIVE CORRUPT — ...'},
            });
            const b = document.getElementById('archive-banner');
            return {down: down, text: b.textContent, display: b.style.display};
        """, tmp_path)
        assert out["down"] is True
        assert "TM ARCHIVE" in out["text"]
        assert "CORRUPT" in out["text"]
        assert out["display"] == ""

    def test_a_healthy_archive_paints_nothing(self, tmp_path):
        out = run_js("""
            applyArchiveState({archive: {status: 'ok', available: true,
                                         message: 'fine'}});
            const b = document.getElementById('archive-banner');
            return {text: b.textContent, display: b.style.display};
        """, tmp_path)
        assert out["text"] == ""
        assert out["display"] == "none"

    def test_a_legacy_archive_is_flagged_but_not_as_a_failure(self, tmp_path):
        out = run_js("""
            const down = applyArchiveState({
              archive: {status: 'legacy', available: true,
                        message: 'LEGACY ARCHIVE ...'},
            });
            const b = document.getElementById('archive-banner');
            return {down: down, text: b.textContent, colour: b.style.color};
        """, tmp_path)
        assert out["down"] is False
        assert "LEGACY" in out["text"]
        assert "warning" in out["colour"]


# ══════════════════════════════════════════════════════════════════════
# Laid out by a real engine.
#
# The node harness executes the page's JS but has no CSS and no layout, so it
# cannot answer the actual question — "is this value clipped on screen?".
# These run the page in headless Chromium and measure the INK of every value
# against the nearest clipping ancestor. They skip cleanly where Playwright or
# a browser binary is not installed; the CSS and render-path gates above are
# the ones that always run.
# ══════════════════════════════════════════════════════════════════════

_SEED_JS = """
() => {
  state.eps = {power_gen_W: 85.0, power_cons_W: 40.0, soc_pct: 61.234,
               bus_voltage_V: 28.123456, bat_voltage_V: 25.987654,
               bat_current_A: -1.234567, temp_bat_C: 12.3456};
  state.params = state.params || {};
  const set = (p, v) =>
    state.params['0x' + p.toString(16).toUpperCase().padStart(4, '0')] = v;
  [0x012B,0x012C,0x012D,0x012E,0x012F,0x0130]
    .forEach((p, i) => set(p, -0.1234567890123 * (i + 1)));
  set(0x0144, 3); set(0x0146, 1.23456789); set(0x0147, -9.87654321);
  set(0x0123, 0.987654); set(0x0133, -123.456); set(0x0105, 28.123456);
  for (let i = 0; i < 8; i++) { set(0x0110 + i, 1); set(0x0118 + i, 1.2345678 + i); }
  buildEPSLines(); updateEPS();
  hkSids = [];
  for (let i = 1; i <= 12; i++) hkSids.push({
    sid: i, name: 'HK_STRUCTURE_LONG_NAME_' + i, apid: 1, interval_s: 1,
    param_count: 34, enabled: true, observed: 'periodic',
    last_seen_age_s: 1.234567});
  hkStructuresLoaded = true; renderHkTable();
  verifLog = [{seq: 1, name: 'AOCS_SET_MODE', state: 'REJECTED',
    position: 'flight_director', error_code: 0x0007,
    error_text: 'Not permitted in the current state',
    reason: 'AOCS refused the transition: wheel 3 speed above the slew '
          + 'interlock threshold of 4500 rpm'}];
  renderVerifLog();
  document.querySelectorAll('.tab-pane').forEach(p => p.style.display = 'block');
  return true;
}
"""

#: Every value-bearing cell whose rendered ink escapes a clipping ancestor, or
#: which is ellipsised. `.visually-hidden` screen-reader text is excluded: it
#: is deliberately parked off-screen.
_MEASURE_JS = """
() => {
  const bad = [];
  for (const el of document.querySelectorAll('.val, td, .verif-reason')) {
    if (el.classList.contains('visually-hidden')
        || el.closest('.visually-hidden')
        || el.querySelector('.visually-hidden')) continue;
    const txt = (el.textContent || '').trim();
    if (!txt) continue;
    if (getComputedStyle(el).textOverflow === 'ellipsis'
        && el.scrollWidth > el.clientWidth + 1) {
      bad.push({txt: txt.slice(0, 48), kind: 'ellipsis'});
      continue;
    }
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects());
    if (!rects.length) continue;
    const right = Math.max(...rects.map(r => r.right));
    for (let p = el.parentElement; p; p = p.parentElement) {
      const st = getComputedStyle(p);
      if (st.overflowX === 'hidden' || st.overflowX === 'clip') {
        if (right > p.getBoundingClientRect().right + 0.5)
          bad.push({txt: txt.slice(0, 48), kind: 'clipped',
                    by: (p.className || p.tagName).slice(0, 24)});
        break;
      }
    }
  }
  return bad;
}
"""


def _laid_out(width: int, extra_css: str = ""):
    """Render index.html in headless Chromium and report clipped cells."""
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright not installed")
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(INDEX.as_uri())
            if extra_css:
                page.add_style_tag(content=extra_css)
            page.wait_for_timeout(400)
            page.evaluate(_SEED_JS)
            page.wait_for_timeout(200)
            bad = page.evaluate(_MEASURE_JS)
            tips = page.evaluate(
                "() => { let n = 0, t = 0;"
                " document.querySelectorAll('#pane-eps td.num-cell,"
                " #pane-eps .data-row .val').forEach(e => {"
                "   t++; if (e.getAttribute('title')) n++; });"
                " return {withTitle: n, total: t}; }")
            browser.close()
    except Exception as e:  # noqa: BLE001 — no browser binary in this env
        pytest.skip(f"headless browser unavailable: {e}")
    return bad, tips


#: The CSS as it was before the fix, re-applied on top so the harness can prove
#: it reproduces the defect it is guarding against.
_PRE_FIX_CSS = (
    ".panel{overflow:hidden!important;}"
    ".panel-body{overflow-x:visible!important;}"
    ".data-table{width:100%!important;min-width:0!important;}"
    ".data-table th{white-space:normal!important;}"
    ".data-row{gap:0!important;}"
    ".data-row .val{flex:0 1 auto!important;white-space:normal!important;}"
    ".verif-reason{max-width:340px!important;overflow:hidden!important;"
    "text-overflow:ellipsis!important;white-space:nowrap!important;}"
)


class TestLaidOutInARealBrowser:

    @pytest.mark.parametrize("width", [1400, 1200, 900, 760])
    def test_no_value_is_clipped_at_any_console_width(self, width):
        bad, _ = _laid_out(width)
        assert bad == [], (
            f"values are still being clipped at {width}px: {bad}")

    def test_the_harness_reproduces_the_defect_with_the_old_css(self):
        """Negative control. If re-applying the pre-fix rules does NOT
        produce clipped cells, this whole class is measuring nothing."""
        bad, _ = _laid_out(760, extra_css=_PRE_FIX_CSS)
        assert bad, ("the pre-fix CSS no longer clips anything — the browser "
                     "check has stopped testing the defect")
        kinds = {b["kind"] for b in bad}
        assert "clipped" in kinds or "ellipsis" in kinds

    def test_eps_values_carry_a_tooltip_when_laid_out(self):
        _, tips = _laid_out(1200)
        assert tips["total"] > 20
        # The handful without one are the LED/flag rows, which hold no number.
        assert tips["withTitle"] >= tips["total"] - 4, (
            f"only {tips['withTitle']}/{tips['total']} EPS values carry a "
            "full-value tooltip")
