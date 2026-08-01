"""Behavioural regression tests for the MCS operator page (defects #63–#68).

`index.html` is one self-contained page with all of its JavaScript inline, so
there is nothing importable. These tests run the page's inline script under Node
inside a `vm` sandbox with a small DOM stub (`tests/test_mcs/js/mcs_harness.js`)
and then exercise the real functions.

What IS covered here:
  * `sidToHex()` packs SIDs big-endian — the correctness bug that made every
    HK Enable / Disable / Request in the HK Manager address the wrong SID.
  * The chart staleness gate: `pushChartData()` refuses to append while
    telemetry is stale or absent, blanks the buffers, and resumes cleanly.
  * `buildPacketRow()` / `packetRowCells()` put SID and RID in their own
    columns, blank where the column does not apply.
  * The log ring buffers, the scroll-lock predicate and the pending counter:
    while the lock is engaged no rows enter the DOM and the "N newer" count
    climbs; releasing it rebuilds the pane from the full ring buffer.

What is NOT covered and still needs manual / E2E verification in a browser:
  * that the *scroll event* actually engages the lock (the harness drives
    `logPanes[...].locked` directly — real scroll dispatch, layout and
    `scrollTop` behaviour are browser concerns);
  * that the jump-to-latest chip is visible and clickable, and the stale
    overlay is legible over each chart (pure CSS/layout);
  * the appearance of the HK Manager and S15 store cards.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(NODE is None,
                                   reason="node is required to execute the page's inline JS")


def run_js(snippet: str, tmp_path: Path):
    """Evaluate `snippet` against the page's globals; return its JSON result."""
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run(
        [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# ── defect #68: SID endianness ──────────────────────────────────────

@requires_node
class TestSidEndianness:
    """`sidToHex()` used to emit the LOW byte first.

    The spacecraft unpacks '>H' (service_dispatch._handle_s3), so "01 00" was
    read as SID 256: every Enable / Disable / Request in the HK Manager was
    accepted and silently did nothing, because SID 256 does not exist.
    """

    def test_sid_is_big_endian(self, tmp_path):
        out = run_js("""
            return { s1: sidToHex(1), s2: sidToHex(2), s6: sidToHex(6),
                     s11: sidToHex(11), s256: sidToHex(256), wide: sidToHex(0x1234) };
        """, tmp_path)
        assert out["s1"] == "00 01"
        assert out["s2"] == "00 02"
        assert out["s6"] == "00 06"
        assert out["s11"] == "00 0B"      # beacon — was "0B 00" = SID 2816
        assert out["s256"] == "01 00"
        assert out["wide"] == "12 34"

    def test_matches_the_schema_encoded_quick_command_buttons(self, tmp_path):
        """sidToHex must agree with the shared catalogue encoder.

        The quick-command buttons were always right about the byte order
        (`00 01` for SID 1). Since Phase E WP-5 they no longer carry a byte
        literal at all — they name HK_REQUEST with `sid: 1` and the shared
        encoder produces the bytes — so the comparison is now against that
        encoder, which is the stronger form of the same check.
        """
        from pathlib import Path as _P
        from smo_common.commanding import encode, load_catalog
        cat = load_catalog(_P(INDEX).resolve().parents[5] / "configs" / "eosat1")
        assert " ".join(f"{b:02X}" for b in
                        encode("HK_REQUEST", {"sid": 1}, catalog=cat)) == "00 01"
        out = run_js("return sidToHex(1);", tmp_path)
        assert out == "00 01"


# ── defect #65: staleness gate on the plots ─────────────────────────

@requires_node
class TestChartStalenessGate:
    """Charts must not fabricate a flat line out of the last received value.

    The server broadcasts state at 1 Hz forever and its parameter cache never
    expires, so after LOS every chart kept appending the same y-value once a
    second — indistinguishable from genuinely steady telemetry.
    """

    def test_fresh_telemetry_is_plotted(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true };
            pushChartData('eps-chart-soc', 0, [50]);
            pushChartData('eps-chart-soc', 1, [51]);
            return { points: chartBuffers['eps-chart-soc'][0].length,
                     verdict: tmFreshness(state, 'eps') };
        """, tmp_path)
        assert out["verdict"] == "fresh"
        assert out["points"] == 2

    def test_degraded_still_plots(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: 8.0, stale: false, in_contact: true };
            pushChartData('eps-chart-soc', 0, [50]);
            return { points: chartBuffers['eps-chart-soc'][0].length,
                     verdict: tmFreshness(state, 'eps') };
        """, tmp_path)
        assert out["verdict"] == "degraded"
        assert out["points"] == 1

    def test_stale_blocks_appends_and_blanks_the_buffers(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true };
            for (let i = 0; i < 10; i++) pushChartData('eps-chart-soc', i, [50]);
            const live = chartBuffers['eps-chart-soc'][0].length;
            state = { last_frame_age_s: 40.0, stale: false, in_contact: false };
            for (let i = 0; i < 30; i++) pushChartData('eps-chart-soc', 100 + i, [50]);
            return { live: live,
                     after_los: chartBuffers['eps-chart-soc'][0].length,
                     verdict: tmFreshness(state, 'eps') };
        """, tmp_path)
        assert out["live"] == 10
        assert out["verdict"] == "stale"
        # Not one fabricated point, and the pre-LOS trace is cleared rather
        # than being slowly pushed off the left edge by fake samples.
        assert out["after_los"] == 0

    def test_recovery_restarts_from_empty(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true };
            for (let i = 0; i < 5; i++) pushChartData('eps-chart-soc', i, [50]);
            state = { last_frame_age_s: 90.0, stale: true, in_contact: false };
            pushChartData('eps-chart-soc', 9, [50]);
            const blanked = chartBuffers['eps-chart-soc'][0].length;
            state = { last_frame_age_s: 0.5, stale: false, in_contact: true };
            pushChartData('eps-chart-soc', 10, [60]);
            pushChartData('eps-chart-soc', 11, [61]);
            return { blanked: blanked,
                     recovered: chartBuffers['eps-chart-soc'][0].length,
                     first_y: chartBuffers['eps-chart-soc'][0][0].y };
        """, tmp_path)
        assert out["blanked"] == 0
        assert out["recovered"] == 2
        assert out["first_y"] == 60      # honest restart, no pre-LOS carry-over

    def test_no_telemetry_at_all_is_not_plotted(self, tmp_path):
        out = run_js("""
            ensureChart('eps-chart-soc', [{label:'SoC', borderColor:'#0f0'}], '%');
            state = { last_frame_age_s: null };
            pushChartData('eps-chart-soc', 0, [0]);
            return { points: chartBuffers['eps-chart-soc'][0].length,
                     verdict: tmFreshness(state, 'eps') };
        """, tmp_path)
        assert out["verdict"] == "none"
        assert out["points"] == 0

    def test_per_subsystem_staleness_gates_its_own_chart(self, tmp_path):
        """A powered-down subsystem goes stale while the link is still up."""
        out = run_js("""
            ensureChart('pld-chart-fpa', [{label:'FPA', borderColor:'#0f0'}], 'C');
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      subsystem_staleness: { payload: 300.0, eps: 0.5 } };
            pushChartData('pld-chart-fpa', 0, [-40]);
            return { payload: tmFreshness(state, 'payload'),
                     eps: tmFreshness(state, 'eps'),
                     points: chartBuffers['pld-chart-fpa'][0].length };
        """, tmp_path)
        assert out["payload"] == "stale"
        assert out["eps"] == "fresh"
        assert out["points"] == 0

    def test_server_stale_flag_is_honoured(self, tmp_path):
        out = run_js("return tmFreshness({last_frame_age_s: 2, stale: true}, 'eps');",
                     tmp_path)
        assert out == "stale"

    def test_every_chart_canvas_prefix_maps_to_a_subsystem(self, tmp_path):
        """The gate must not silently fall through to "no subsystem" for a chart."""
        page = INDEX.read_text()
        import re
        canvases = set(re.findall(r"pushChartData\('([a-z0-9-]+)'", page))
        assert canvases, "no pushChartData call sites found"
        out = run_js(
            "return %s.map(function(c){ return [c, chartSubsystemOf(c)]; });"
            % json.dumps(sorted(canvases)), tmp_path)
        unmapped = [c for c, sub in out if sub is None]
        assert not unmapped, f"charts with no staleness subsystem: {unmapped}"


# ── defect #64: SID / RID columns in the TM PACKETS window ──────────

@requires_node
class TestPacketRowSidRid:
    """`pkt.hk_decoded.sid` and `pkt.event.event_id` were already on the wire."""

    def test_hk_packet_carries_its_sid(self, tmp_path):
        out = run_js("""
            const r = buildPacketRow({service:3, subtype:25, apid:1, seq:42,
                                      data_len:104, hk_decoded:{sid:4, param_count:26}}, 'T');
            return { row: r, cells: packetRowCells(r) };
        """, tmp_path)
        assert out["row"]["sid"] == 4
        assert out["cells"]["sid"] == "4"
        assert out["cells"]["rid"] == ""        # blank where not applicable
        assert out["cells"]["pkt"] == "S3.25"

    def test_event_packet_carries_its_rid(self, tmp_path):
        out = run_js("""
            const r = buildPacketRow({service:5, subtype:4, apid:1, seq:43, data_len:35,
                       event:{event_id:0x0102, severity:4, description:'BAT UNDERVOLT'}}, 'T');
            return { row: r, cells: packetRowCells(r) };
        """, tmp_path)
        assert out["row"]["rid"] == 0x0102
        assert out["cells"]["rid"] == "0x0102"
        assert out["cells"]["sid"] == ""        # blank where not applicable
        assert "BAT UNDERVOLT" in out["cells"]["info"]

    def test_unknown_sid_is_surfaced_not_swallowed(self, tmp_path):
        out = run_js("""
            const r = buildPacketRow({service:3, subtype:25, apid:2, seq:9, data_len:2,
                       hk_decoded:{sid:99, param_count:0, unknown_sid:true}}, 'T');
            return { sid: r.sid, unknown: r.unknownSid, info: r.info };
        """, tmp_path)
        assert out["sid"] == 99
        assert out["unknown"] is True
        assert "UNKNOWN" in out["info"]

    def test_rendered_row_contains_sid_and_rid_cells(self, tmp_path):
        out = run_js("""
            bottomTab = 'tm';
            handleTMPacket({service:3, subtype:25, apid:1, seq:7, data_len:104,
                            hk_decoded:{sid:11, param_count:6}});
            handleTMPacket({service:5, subtype:2, apid:1, seq:8, data_len:35,
                            event:{event_id:0x0205, severity:2, description:'RW SPEED HIGH'}});
            const list = document.getElementById('log-list-tm');
            return { count: list.children.length,
                     newest: list.children[0].innerHTML,
                     older: list.children[1].innerHTML };
        """, tmp_path)
        assert out["count"] == 2
        assert 'class="log-rid">0x0205<' in out["newest"]
        assert 'class="log-sid">11<' in out["older"]

    def test_monitoring_and_s20_reports_use_the_rid_column(self, tmp_path):
        out = run_js("""
            const m = packetRowCells(buildPacketRow({service:12, subtype:9, apid:1, seq:1,
                        data_len:7, monitoring:{param_id:0x0101, value:18.5,
                                                transition:'out_of_limits'}}, 'T'));
            const p = packetRowCells(buildPacketRow({service:20, subtype:2, apid:1, seq:2,
                        data_len:7, s20_param:{id:0x0300, value:1}}, 'T'));
            return { m: m, p: p };
        """, tmp_path)
        assert out["m"]["rid"] == "0x0101"
        assert out["p"]["rid"] == "0x0300"


# ── defect #63: log scrolling, buffering and the scroll lock ────────

@requires_node
class TestOperatorLogScrolling:
    """The log used to be rebuilt with innerHTML on every packet.

    That reset `scrollTop` several times a second, and only the newest 80 of
    the 200/300 buffered rows were ever rendered.
    """

    def test_ring_buffers_are_large(self, tmp_path):
        out = run_js("return [EVENT_RING, TM_RING, LOG_RENDER_CAP];", tmp_path)
        assert out == [2000, 2000, 2000]

    def test_render_cap_matches_the_ring_so_nothing_is_unreachable(self, tmp_path):
        out = run_js("return LOG_RENDER_CAP >= Math.max(EVENT_RING, TM_RING);", tmp_path)
        assert out is True

    def test_scroll_lock_predicate(self, tmp_path):
        out = run_js("""
            return [logScrollLockShouldEngage(0), logScrollLockShouldEngage(4),
                    logScrollLockShouldEngage(5), logScrollLockShouldEngage(400)];
        """, tmp_path)
        assert out == [False, False, True, True]

    def test_rows_are_appended_incrementally_newest_first(self, tmp_path):
        out = run_js("""
            bottomTab = 'tm';
            for (let i = 0; i < 5; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            const list = document.getElementById('log-list-tm');
            return { rows: list.children.length,
                     newest_seq: list.children[0].innerHTML,
                     pending: logPanes.tm.pending };
        """, tmp_path)
        assert out["rows"] == 5
        assert out["pending"] == 0
        assert ">4<" in out["newest_seq"]     # newest packet is at the top

    def test_locked_pane_stops_touching_the_dom_and_counts_new_rows(self, tmp_path):
        out = run_js("""
            bottomTab = 'tm';
            for (let i = 0; i < 5; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            const list = document.getElementById('log-list-tm');
            const before = list.children.length;
            logPanes.tm.locked = true;                  // operator scrolled back
            for (let i = 0; i < 7; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:100+i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            return { before: before, after: list.children.length,
                     pending: logPanes.tm.pending, buffered: tmLog.length,
                     chip: document.getElementById('log-jump-latest')
                             .classList.contains('visible'),
                     chip_count: document.getElementById('log-jump-count').textContent };
        """, tmp_path)
        assert out["before"] == 5
        assert out["after"] == 5            # DOM untouched -> scroll never moves
        assert out["pending"] == 7
        assert out["buffered"] == 12        # nothing is lost
        assert out["chip"] is True
        assert out["chip_count"] == "7"

    def test_jump_to_latest_rebuilds_the_whole_buffer(self, tmp_path):
        out = run_js("""
            bottomTab = 'tm';
            for (let i = 0; i < 5; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            logPanes.tm.locked = true;
            for (let i = 0; i < 7; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:100+i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            logPanes.tm.locked = false; logPanes.tm.pending = 0;
            renderLogPane('tm');
            updateLogJumpChip();
            const list = document.getElementById('log-list-tm');
            return { rows: list.children.length,
                     chip: document.getElementById('log-jump-latest')
                             .classList.contains('visible') };
        """, tmp_path)
        assert out["rows"] == 12            # all 12, not the old slice(0, 80)
        assert out["chip"] is False

    def test_more_than_eighty_rows_are_rendered(self, tmp_path):
        """The old renderer hard-capped the DOM at `entries.slice(0, 80)`."""
        out = run_js("""
            bottomTab = 'tm';
            for (let i = 0; i < 250; i++)
              handleTMPacket({service:3, subtype:25, apid:1, seq:i, data_len:10,
                              hk_decoded:{sid:1, param_count:51}});
            return document.getElementById('log-list-tm').children.length;
        """, tmp_path)
        assert out == 250

    def test_events_and_tm_panes_are_independent(self, tmp_path):
        """They used to be the same DOM node, so one destroyed the other."""
        out = run_js("""
            bottomTab = 'events';
            addEventLog('warn', 'TEST', 'hello');
            handleTMPacket({service:3, subtype:25, apid:1, seq:1, data_len:10,
                            hk_decoded:{sid:1, param_count:51}});
            return { events: document.getElementById('log-list-events').children.length,
                     tm: document.getElementById('log-list-tm').children.length };
        """, tmp_path)
        assert out["events"] == 1
        assert out["tm"] == 1

    def test_severity_filter_predicate(self, tmp_path):
        out = run_js("""
            return { all_info: logSeverityMatches('info', ''),
                     medplus_warn: logSeverityMatches('warn', 'medplus'),
                     medplus_alarm: logSeverityMatches('alarm', 'medplus'),
                     medplus_info: logSeverityMatches('info', 'medplus'),
                     exact: logSeverityMatches('nom', 'nom'),
                     mismatch: logSeverityMatches('nom', 'alarm') };
        """, tmp_path)
        assert out == {"all_info": True, "medplus_warn": True, "medplus_alarm": True,
                       "medplus_info": False, "exact": True, "mismatch": False}

    def test_filter_hides_rows_without_rebuilding(self, tmp_path):
        out = run_js("""
            bottomTab = 'events';
            addEventLog('info', 'A', 'one');
            addEventLog('alarm', 'B', 'two');
            eventSevFilter = 'alarm';
            applyEventFilterToDom();
            const list = document.getElementById('log-list-events');
            return { rows: list.children.length,
                     hidden: [list.children[0].classList.contains('log-filtered'),
                              list.children[1].classList.contains('log-filtered')] };
        """, tmp_path)
        assert out["rows"] == 2                 # history intact
        assert out["hidden"] == [False, True]   # newest (alarm) shown, info hidden


# ── defect #67: builder byte counts / catalogue-driven func list ────

@requires_node
class TestCommandBuilderByteCounts:

    def test_hardcoded_func_catalog_is_gone(self):
        page = INDEX.read_text()
        assert "const FUNC_CATALOG = [" not in page, \
            "FUNC_CATALOG must be built from /api/tc-catalog, not hardcoded"
        assert "/api/tc-catalog" in page, "the client must actually call the endpoint"

    def test_entered_byte_counting(self, tmp_path):
        out = run_js("""
            return { empty: enteredDataBytes(''), spaced: enteredDataBytes('00 01'),
                     packed: enteredDataBytes('0001'), three: enteredDataBytes('08 00 7A'),
                     comma: enteredDataBytes('08,00,7A'),
                     messy: enteredDataBytes('  00   01  ') };
        """, tmp_path)
        assert out == {"empty": 0, "spaced": 2, "packed": 2, "three": 3,
                       "comma": 3, "messy": 2}

    def test_expected_bytes_text(self, tmp_path):
        out = run_js("""
            return {
              fixed: expectedBytesText({expected: 3, min: 3, variable: false, note: ''}),
              one: expectedBytesText({expected: 1, min: 1, variable: false, note: ''}),
              variable: expectedBytesText({expected: null, min: 6, variable: true,
                                           note: 'variable'}),
              unknown: expectedBytesText({expected: null, min: 1, variable: false,
                                          note: "no 'fields' key"})
            };
        """, tmp_path)
        assert out["fixed"] == "expects 3 data bytes"
        assert out["one"] == "expects 1 data byte"
        assert "variable" in out["variable"] and "6 fixed" in out["variable"]
        assert "not declared" in out["unknown"]

    def test_catalogue_response_drives_func_list_and_lengths(self, tmp_path):
        """Simulate the /api/tc-catalog payload the server now returns."""
        out = run_js("""
            const payload = { commands: [
              { name:'AOCS_SET_MODE', service:8, subtype:1, func_id:0,
                expected_data_bytes:2, min_data_bytes:2, variable_length:false,
                length_note:'', allowed:true },
              { name:'EPS_DEPLOY_WING', service:8, subtype:1, func_id:81,
                expected_data_bytes:1, min_data_bytes:1, variable_length:false,
                length_note:'', allowed:true },
              { name:'TTC_SWITCH_PRIMARY', service:8, subtype:1, func_id:63,
                expected_data_bytes:null, min_data_bytes:1, variable_length:false,
                length_note:"catalogue entry has no 'fields' key", allowed:true },
              { name:'MEM_LOAD', service:6, subtype:2,
                expected_data_bytes:null, min_data_bytes:6, variable_length:true,
                length_note:'variable', allowed:true },
              { name:'HK_ENABLE', service:3, subtype:5,
                expected_data_bytes:2, min_data_bytes:2, variable_length:false,
                length_note:'', allowed:true }
            ] };
            fetch = async () => ({ json: async () => payload });
            return loadTcCatalog().then(() => ({
              funcs: FUNC_CATALOG.map(f => [f.id, f.expected, f.variable]),
              hk: CMD_LENGTH_BY_KEY['3.5'],
              mem: CMD_LENGTH_BY_KEY['6.2'],
              switch_primary: CMD_LENGTH_BY_KEY['8.1.63']
            }));
        """, tmp_path)
        assert out["funcs"] == [[0, 2, False], [63, None, False], [81, 1, False]]
        assert out["hk"]["expected"] == 2
        assert out["mem"]["variable"] is True and out["mem"]["expected"] is None
        assert out["switch_primary"]["expected"] is None
        assert out["switch_primary"]["variable"] is False

    def test_variable_and_undeclared_commands_never_show_a_number(self, tmp_path):
        out = run_js("""
            const payload = { commands: [
              { name:'MEM_LOAD', service:6, subtype:2, expected_data_bytes:null,
                min_data_bytes:6, variable_length:true, length_note:'variable' },
              { name:'TTC_SWITCH_PRIMARY', service:8, subtype:1, func_id:63,
                expected_data_bytes:null, min_data_bytes:1, variable_length:false,
                length_note:"no 'fields' key" }
            ] };
            fetch = async () => ({ json: async () => payload });
            return loadTcCatalog().then(() => {
              const chip = document.getElementById('cmd-bytes-chip');
              document.getElementById('cmd-service').value = '6';
              document.getElementById('cmd-subtype').value = '2';
              document.getElementById('cmd-data').value = 'DE AD BE EF';
              updateCmdBytesChip();
              const variable = chip.textContent;
              document.getElementById('cmd-service').value = '8';
              document.getElementById('cmd-subtype').value = '1';
              document.getElementById('cmd-data').value = '3F';
              updateCmdBytesChip();
              return { variable: variable, undeclared: chip.textContent };
            });
        """, tmp_path)
        assert "VARIABLE" in out["variable"]
        assert "6 fixed" in out["variable"]
        assert "NOT DECLARED" in out["undeclared"]
        for text in out.values():
            assert "EXPECTED" not in text     # never a fabricated number


# ── defect #68 / #66: the HK Manager table ──────────────────────────

@requires_node
class TestHkManagerTable:

    def test_hardcoded_sid_table_is_gone(self):
        page = INDEX.read_text()
        assert "const hkSids = [" not in page, \
            "the SID table must come from /api/hk-structures"
        assert "EPS Power Lines" not in page, "phantom SID 7 is back"
        assert "/api/hk-structures" in page

    def test_table_is_rendered_from_the_api_payload(self, tmp_path):
        out = run_js("""
            const payload = { structures: [
              { sid:1, name:'EPS', interval_s:1.0, param_count:51, apid:1,
                readonly:false, note:'', last_seen_age_s:0.4, observed:'periodic',
                enabled:null },
              { sid:4, name:'Platform', interval_s:8.0, param_count:26, apid:1,
                readonly:false, note:'', last_seen_age_s:60.0, observed:'silent',
                enabled:null },
              { sid:11, name:'Beacon', interval_s:10.0, param_count:6, apid:2,
                readonly:true, note:'Bootloader beacon on APID 0x002',
                last_seen_age_s:null, observed:'never_seen', enabled:null }
            ] };
            fetch = async () => ({ json: async () => payload });
            return loadHkStructures().then(() => {
              const rows = document.getElementById('hk-sid-tbody').children;
              return { n: rows.length,
                       html: Array.prototype.map.call(rows, r => r.innerHTML) };
            });
        """, tmp_path)
        assert out["n"] == 3
        eps, platform, beacon = out["html"]
        # SID 4 is "Platform" — the old table called it "TTC" (4 and 6 were swapped)
        assert "Platform" in platform
        assert "PERIODIC" in eps
        assert "SILENT" in platform
        # SID 11 is present, on APID 2, and read-only
        assert "Beacon" in beacon
        assert "0x002" in beacon
        assert "read-only" in beacon
        assert "disabled" in beacon and "inhibited" in beacon

    def test_beacon_row_keeps_request_but_not_enable_disable(self, tmp_path):
        out = run_js("""
            const payload = { structures: [
              { sid:11, name:'Beacon', interval_s:10.0, param_count:6, apid:2,
                readonly:true, note:'beacon', last_seen_age_s:1.0,
                observed:'periodic', enabled:null } ] };
            fetch = async () => ({ json: async () => payload });
            return loadHkStructures().then(() => {
              const html = document.getElementById('hk-sid-tbody').children[0].innerHTML;
              return { request: html.indexOf('hkRequest(11)') >= 0,
                       toggle: html.indexOf('hkToggle(11)') >= 0 };
            });
        """, tmp_path)
        assert out["request"] is True
        assert out["toggle"] is False

    def test_state_is_observed_not_assumed(self, tmp_path):
        """The old table hardcoded `enabled: true` for every row, forever."""
        out = run_js("""
            return {
              never: hkSidState({sid:3, interval_s:60, observed:'never_seen', enabled:null}),
              silent: hkSidState({sid:3, interval_s:60, observed:'silent', enabled:null}),
              periodic: hkSidState({sid:3, interval_s:60, observed:'periodic', enabled:null}),
              reported_off: hkSidState({sid:3, observed:'silent', enabled:false}),
              reported_on_but_silent: hkSidState({sid:3, observed:'silent', enabled:true})
            };
        """, tmp_path)
        assert out["never"]["label"] == "NEVER SEEN"
        assert out["silent"]["label"] == "SILENT"
        assert out["periodic"]["label"] == "PERIODIC"
        assert out["reported_off"]["label"] == "DISABLED"
        # A SID the spacecraft says is enabled but which is not arriving is a
        # discrepancy the operator must see, not a green LED.
        assert out["reported_on_but_silent"]["label"] == "ENABLED · NOT ARRIVING"

    def test_toggle_does_not_fake_local_state(self, tmp_path):
        """`hkToggle` used to flip a client-side flag, making the LED decorative."""
        out = run_js("""
            const payload = { structures: [
              { sid:1, name:'EPS', interval_s:1.0, param_count:51, apid:1,
                readonly:false, note:'', last_seen_age_s:0.4, observed:'periodic',
                enabled:null } ] };
            fetch = async () => ({ json: async () => payload });
            const sent = [];
            sendPusCmd = (svc, sub, data, name) => sent.push([svc, sub, data, name]);
            return loadHkStructures().then(() => {
              hkToggle(1);
              return { sent: sent, observed: hkSids[0].observed,
                       enabled: hkSids[0].enabled };
            });
        """, tmp_path)
        assert out["sent"] == [[3, 6, "00 01", "HK_DISABLE_SID1"]]
        assert out["observed"] == "periodic"   # unchanged until telemetry says so
        assert out["enabled"] is None

    def test_observed_state_updates_from_the_state_broadcast(self, tmp_path):
        out = run_js("""
            const payload = { structures: [
              { sid:1, name:'EPS', interval_s:1.0, param_count:51, apid:1,
                readonly:false, note:'', last_seen_age_s:0.4, observed:'periodic',
                enabled:null } ] };
            fetch = async () => ({ json: async () => payload });
            return loadHkStructures().then(() => {
              updateHkObservedState({ hk_sid_age_s: { '1': 45.0 } });
              const silent = hkSids[0].observed;
              updateHkObservedState({ hk_sid_age_s: { '1': 0.8 },
                                      hk_enabled: { '1': true } });
              return { silent: silent, back: hkSids[0].observed,
                       enabled: hkSids[0].enabled };
            });
        """, tmp_path)
        assert out["silent"] == "silent"      # 45 s > 1 s interval * 2.5 + 1
        assert out["back"] == "periodic"
        assert out["enabled"] is True


# ── S15 clear button ────────────────────────────────────────────────

@requires_node
class TestS15ClearButton:

    def test_clear_button_exists_for_every_store(self):
        page = INDEX.read_text()
        for store_id in (1, 2, 3, 4):
            assert f"s15Clear({store_id})" in page

    def test_clear_sends_tc_15_11_after_confirmation(self, tmp_path):
        out = run_js("""
            const sent = [];
            sendPusCmd = (svc, sub, data, name) => sent.push([svc, sub, data, name]);
            state = { storage_status: [{ id: 2, count: 118, capacity: 500,
                                         enabled: true, name: 'Event Store' }] };
            confirm = () => true;
            s15Clear(2);
            const accepted = sent.slice();
            confirm = () => false;          // operator cancels
            s15Clear(2);
            return { accepted: accepted, after_cancel: sent.length };
        """, tmp_path)
        assert out["accepted"] == [[15, 11, "02", "S15_CLEAR_2"]]
        assert out["after_cancel"] == 1     # cancelling sends nothing

    def test_store_card_works_without_the_new_telemetry_fields(self, tmp_path):
        """Defensive: name / fill_pct / overflow / wrap are not sent yet."""
        out = run_js("""
            state = { storage_status: [{ id: 1, count: 250, capacity: 1000,
                                         enabled: true }] };
            updateStorageStatus();
            return { count: document.getElementById('s15-count-1').textContent,
                     cap: document.getElementById('s15-cap-1').textContent,
                     width: document.getElementById('s15-fill-1').style.width,
                     state: document.getElementById('s15-state-1').textContent };
        """, tmp_path)
        assert out["count"] == "250"
        assert out["cap"] == " / 1000"
        assert out["width"] == "25%"
        assert "ENABLED" in out["state"] and "25% full" in out["state"]

    def test_store_card_uses_the_new_fields_when_they_arrive(self, tmp_path):
        out = run_js("""
            state = { storage_status: [{ id: 1, count: 980, capacity: 1000,
                                         enabled: true, name: 'Housekeeping Store',
                                         fill_pct: 98.0, overflow: true, wrap: true }] };
            updateStorageStatus();
            return { name: document.getElementById('s15-name-1').textContent,
                     width: document.getElementById('s15-fill-1').style.width,
                     cls: document.getElementById('s15-fill-1').className,
                     state: document.getElementById('s15-state-1').textContent };
        """, tmp_path)
        assert out["name"] == "Housekeeping Store"
        assert out["width"] == "98%"
        assert "crit" in out["cls"]
        assert "OVERFLOW" in out["state"]
        assert "WRAP" in out["state"]


# ── defect #83: the procedure builder emits the canonical schema ────

@requires_node
class TestProcedureBuilderCanonicalSteps:
    """The builder used to emit steps the operator saw as raw dicts, and its
    `command` step was four bare boxes with a hand-typed hex blob.
    """

    def test_add_step_emits_canonical_tagged_steps(self, tmp_path):
        out = run_js("""
            pbSteps = [];
            document.getElementById('pb-step-type').value = 'wait';
            pbAddStep();
            document.getElementById('pb-step-type').value = 'tlm_check';
            pbAddStep();
            document.getElementById('pb-step-type').value = 'go_nogo';
            pbAddStep();
            return { types: pbSteps.map(s => s.type),
                     seconds: pbSteps[0].seconds,
                     timeout: pbSteps[1].timeout_s };
        """, tmp_path)
        assert out["types"] == ["wait", "tlm_check", "go_nogo"]
        assert out["seconds"] == 5
        assert out["timeout"] == 30

    def test_command_step_comes_from_the_shared_command_builder(self, tmp_path):
        """`pbAddCommandStep` is the shared `renderCommandBuilder()` onSend
        callback — the builder never packs a command itself."""
        out = run_js("""
            pbSteps = [];
            pbAddCommandStep(8, 1, '1a 02', 'PLD IMAGING');
            return pbSteps[0];
        """, tmp_path)
        assert out["type"] == "command"
        assert (out["service"], out["subtype"]) == (8, 1)
        assert out["data_hex"] == "1a02"
        assert out["description"] == "PLD IMAGING"

    def test_rendered_steps_contain_no_raw_dicts(self, tmp_path):
        out = run_js("""
            pbSteps = [];
            document.getElementById('pb-step-type').value = 'wait';
            pbAddStep();
            pbAddCommandStep(8, 1, '1c', 'CAPTURE');
            pbRender();
            return { html: document.getElementById('pb-steps').innerHTML,
                     desc: pbSteps.map(pbDescribeStep) };
        """, tmp_path)
        assert "{'type'" not in out["html"] and "[object Object]" not in out["html"]
        assert out["desc"] == ["Wait 5s", "CAPTURE [S8.1]"]
