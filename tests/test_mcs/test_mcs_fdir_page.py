"""Browser-side behaviour for the second MCS pass (defects #76-#79).

`index.html` keeps all of its JavaScript inline, so these tests run the page's
script under Node inside the shared `vm` harness (tests/test_mcs/js/
mcs_harness.js) and exercise the real functions.

What IS covered here:
  * the S11 panel's embedded command builder packs byte-for-byte the same TC as
    the full Commanding-tab builder for an equivalent command, and produces the
    right TC(11,4) time-tag prefix;
  * the schedule table resolves mnemonics and renders one row per entry;
  * the FDIR window's five panels render from an /api/fdir payload, including
    the level colour class, the latch chips, the monitor / rule tables and the
    correlated journal;
  * the memory browser renders a TM(6,6) dump as offset+hex+ASCII and reaches a
    MATCH / MISMATCH verdict from a TM(6,10) CRC;
  * the station cards and contact legend are generated from the API list.

What is NOT covered and still needs manual verification in a real browser:
  * that the FDIR tab is reachable and correctly gated for the fdir_systems
    seat (position filtering runs against a live /api/positions response);
  * layout of the five FDIR panels, the journal scroll region and the new
    memory patch/verify strip;
  * the file-upload path in the memory patch box (FileReader is not stubbed);
  * that the S11 time-tag preview updates live as the operator types.
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
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")


def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run(
        [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


# A miniature /api/tc-catalog answer, shaped like the real endpoint.
CATALOG_JS = """
tcCatalog = [
  {name:'CONNECTION_TEST', service:17, subtype:1, fields:[], expected_data_bytes:0, allowed:true},
  {name:'MON_REPORT_DEFS', service:12, subtype:12, fields:[], expected_data_bytes:0, allowed:true},
  {name:'MON_ADD_DEF', service:12, subtype:6, expected_data_bytes:13, allowed:true,
   fields:[{name:'mon_id',type:'uint16'},{name:'param_id',type:'uint16'},
           {name:'check_type',type:'uint8'},{name:'low_limit',type:'float32'},
           {name:'high_limit',type:'float32'}]},
  {name:'MEM_DUMP', service:6, subtype:5, expected_data_bytes:7, allowed:true,
   fields:[{name:'memory_id',type:'uint8'},{name:'address',type:'uint32'},
           {name:'length',type:'uint16'}]},
  {name:'OBCP_START', service:18, subtype:3, expected_data_bytes:3, allowed:true,
   fields:[{name:'proc_id',type:'uint16'},{name:'param',type:'uint8'}]},
  {name:'PLD_CAPTURE_IMAGE', service:8, subtype:1, func_id:28, expected_data_bytes:1, allowed:true,
   fields:[{name:'func_id',type:'uint8'}]},
];
tcCatalogLoaded = true;
"""


# ── the reusable command builder ────────────────────────────────────

@requires_node
class TestCommandBuilderComponent:
    def test_both_instances_exist(self, tmp_path):
        out = run_js(f"""
            {CATALOG_JS}
            const host = document.getElementById('s11-host');
            renderCommandBuilder(host, {{prefix:'s11cb', onSend: () => {{}}}});
            return {{ prefixes: Object.keys(CMD_BUILDERS).sort() }};
        """, tmp_path)
        # 'cmd' is registered by initCmdBuilder() at page load.
        assert "s11cb" in out["prefixes"]

    def test_s11_builder_packs_the_same_bytes_as_the_full_builder(self, tmp_path):
        """The S11 panel used to be a separate, poorer, hand-written form.

        Same command, both builders, identical packed data field.
        """
        out = run_js(f"""
            {CATALOG_JS}
            renderCommandBuilder(document.getElementById('s11-host'),
                                 {{prefix:'s11cb', onSend: () => {{}}}});
            function pack(prefix) {{
              document.getElementById(prefix + '-service').value = '12';
              document.getElementById(prefix + '-subtype').value = '6';
              document.getElementById(prefix + '-command').value = 'MON_ADD_DEF';
              updateStructuredFields(prefix, 'MON_ADD_DEF');
              document.getElementById(sfId(prefix, 'mon_id')).value = '8000';
              document.getElementById(sfId(prefix, 'param_id')).value = '0101';
              document.getElementById(sfId(prefix, 'check_type')).value = '0';
              document.getElementById(sfId(prefix, 'low_limit')).value = '20';
              document.getElementById(sfId(prefix, 'high_limit')).value = '100';
              return packStructuredFields(prefix);
            }}
            return {{ full: pack('cmd'), s11: pack('s11cb') }};
        """, tmp_path)
        assert out["full"] == out["s11"]
        # mon_id(2) param_id(2) check(1) low(4) high(4) = 13 bytes
        assert out["full"] == "80 00 01 01 00 41 A0 00 00 42 C8 00 00"

    def test_s11_wraps_the_packed_tc_with_a_time_tag(self, tmp_path):
        out = run_js("""
            return {
              a: s11BuildS114Data(0x31FB5EE9, 8, 1, '1C'),
              b: s11BuildS114Data(1000, 17, 1, ''),
              c: s11BuildS114Data(0, 12, 6, '80 00 01 01 00 41 A0 00 00 42 C8 00 00'),
            };
        """, tmp_path)
        assert out["a"] == "31 FB 5E E9 08 01 1C"
        assert out["b"] == "00 00 03 E8 11 01"
        assert out["c"].startswith("00 00 00 00 0C 06 80 00")

    def test_catalogue_drives_the_service_list_including_s18(self, tmp_path):
        """The hardcoded <option> list had no value="18", so the OBCP commands
        could not be selected at all."""
        out = run_js(f"""
            {CATALOG_JS}
            renderCommandBuilder(document.getElementById('s11-host'),
                                 {{prefix:'s11cb', onSend: () => {{}}}});
            return {{ services: catalogServices(),
                      html: document.getElementById('s11cb-service').innerHTML }};
        """, tmp_path)
        assert 18 in out["services"]
        assert 'value="18"' in out["html"]

    def test_command_picker_lists_catalogue_commands_for_the_service(self, tmp_path):
        out = run_js(f"""
            {CATALOG_JS}
            renderCommandBuilder(document.getElementById('s11-host'),
                                 {{prefix:'s11cb', onSend: () => {{}}}});
            document.getElementById('s11cb-service').value = '18';
            populateCommandPicker('s11cb');
            return document.getElementById('s11cb-command').innerHTML;
        """, tmp_path)
        assert "OBCP_START" in out and "S18.3" in out

    def test_picking_a_command_sets_subtype_and_fields(self, tmp_path):
        out = run_js(f"""
            {CATALOG_JS}
            renderCommandBuilder(document.getElementById('s11-host'),
                                 {{prefix:'s11cb', onSend: () => {{}}}});
            document.getElementById('s11cb-service').value = '18';
            populateCommandPicker('s11cb');
            document.getElementById('s11cb-command').value = 'OBCP_START';
            onBuilderCommandPicked('s11cb');
            return {{ subtype: document.getElementById('s11cb-subtype').value,
                      name: document.getElementById('s11cb-name').value,
                      fields: document.getElementById('s11cb-struct-fields').innerHTML }};
        """, tmp_path)
        assert str(out["subtype"]) == "3"
        assert out["name"] == "OBCP_START"
        assert "proc_id" in out["fields"] and "param" in out["fields"]

    def test_catalogue_field_types_map_to_builder_widgets(self, tmp_path):
        out = run_js(f"""
            {CATALOG_JS}
            const d = catalogFieldDefs(6, 5, 'MEM_DUMP');
            return d.fields.map(f => [f.name, f.type, f.size || null]);
        """, tmp_path)
        assert out == [["memory_id", "int", 1],
                       ["address", "hex_int", 4],
                       ["length", "int", 2]]

    def test_valid_subtypes_come_from_the_catalogue_when_loaded(self, tmp_path):
        out = run_js(f"""
            {CATALOG_JS}
            return {{ s18: validSubtypesFor(18), s12: validSubtypesFor(12) }};
        """, tmp_path)
        assert out["s18"] == [3]
        assert out["s12"] == [6, 12]


# ── on-board schedule table ─────────────────────────────────────────

SCHEDULE_JS = """
s11ApplyScheduleReport({
  count: 3, enabled: true, next_id: 12, reported_ts: 1700000000,
  entries: [
    {id:2, exec_time_cuc:838557301, exec_utc:'2026-08-01T10:00:00+00:00', apid:1,
     service:8, subtype:1, data_len:2, data_hex:'1303', mnemonic:'EPS_POWER_LINE_ON'},
    {id:1, exec_time_cuc:838557417, exec_utc:'2026-08-01T10:02:00+00:00', apid:1,
     service:8, subtype:1, data_len:1, data_hex:'1C', mnemonic:'PLD_CAPTURE_IMAGE'},
    {id:3, exec_time_cuc:838557500, exec_utc:'2026-08-01T10:04:00+00:00', apid:1,
     service:17, subtype:1, data_len:0, data_hex:'', mnemonic:'CONNECTION_TEST'},
  ],
});
"""


@requires_node
class TestScheduleRendering:
    def test_empty_state_is_honest(self, tmp_path):
        out = run_js("""
            s11Schedule = null; renderS11Schedule();
            return { body: document.getElementById('s11-tbody').innerHTML,
                     badge: document.getElementById('s11-enabled-badge').textContent };
        """, tmp_path)
        assert "TC(11,17)" in out["body"]
        assert out["badge"] == "UNKNOWN"

    def test_one_row_per_entry_with_mnemonics(self, tmp_path):
        out = run_js(f"""
            {SCHEDULE_JS}
            const html = document.getElementById('s11-tbody').innerHTML;
            return {{ rows: (html.match(/<tr>/g) || []).length, html: html }};
        """, tmp_path)
        assert out["rows"] == 3
        for mnemonic in ("EPS_POWER_LINE_ON", "PLD_CAPTURE_IMAGE", "CONNECTION_TEST"):
            assert mnemonic in out["html"]

    def test_row_shows_utc_cuc_apid_service_and_data(self, tmp_path):
        out = run_js(f"""{SCHEDULE_JS}
            return document.getElementById('s11-tbody').innerHTML;""", tmp_path)
        assert "2026-08-01 10:02:00" in out
        assert "838557417" in out
        assert "S8.1" in out and "S17.1" in out
        assert "13 03" in out, "data bytes must be readable, not a run-on string"

    def test_header_enable_flag_and_next_id_are_shown(self, tmp_path):
        out = run_js(f"""
            {SCHEDULE_JS}
            const a = {{ badge: document.getElementById('s11-enabled-badge').textContent,
                        nextId: document.getElementById('s11-next-id').textContent }};
            s11ApplyScheduleReport({{count:0, enabled:false, next_id:5, entries:[]}});
            return {{ enabled: a,
                      disabled: {{ badge: document.getElementById('s11-enabled-badge').textContent,
                                  body: document.getElementById('s11-tbody').innerHTML }} }};
        """, tmp_path)
        assert out["enabled"]["badge"] == "ENABLED"
        assert out["enabled"]["nextId"] == "12"
        assert out["disabled"]["badge"] == "DISABLED"
        assert "EMPTY" in out["disabled"]["body"]

    def test_every_row_offers_a_delete(self, tmp_path):
        out = run_js(f"""{SCHEDULE_JS}
            return document.getElementById('s11-tbody').innerHTML;""", tmp_path)
        assert out.count("s11Delete(") == 3


# ── FDIR window ─────────────────────────────────────────────────────

FDIR_JS = """
fdirData = {
  state: {
    fdir_level: 'subsystem', fdir_level_index: 2,
    sc_mode: 0, sc_mode_name: 'SAFE', sc_mode_source: 'sim_state',
    safe_latched: true, emergency_latched: false,
    latch_source: 'derived_from_sc_mode',
    load_shed_stage: 2, monitors_out_of_limits: 2,
    monitors_out_of_limits_ids: [0x8000, 0x8001],
    monitoring_enabled: true, last_action_ts: 1700000000,
    last_action: 'S19 rule 1001 (eps_bus_undervoltage_payload_off) -> S8 func 20',
    s12_reported: true, s19_reported: true,
    s12_report_age_s: 4.2, s19_report_age_s: 4.2,
  },
  monitor_summary: { total: 3, enabled: 2, out_of_limits: 2 },
  rule_summary: { total: 2, enabled: 1, fired: 1 },
  monitors: [
    {mon_id:0x8000, name:'EPS Battery SoC Warning', param_id:0x0101,
     param_name:'eps.bat_soc', check_type:0, check_type_name:'absolute',
     low_limit:20, high_limit:100, enabled:true, severity:'WARNING',
     current_value:17.5, status:'OUT_OF_LIMITS', violation_count:3, onboard:true},
    {mon_id:0x8001, name:'AOCS Rate Delta', param_id:0x0207,
     param_name:'aocs.rate_roll', check_type:1, check_type_name:'delta',
     low_limit:-1, high_limit:1, enabled:true, severity:'ALARM',
     current_value:2.5, status:'OUT_OF_LIMITS', violation_count:1, onboard:true},
    {mon_id:0x8002, name:'TTC RSSI', param_id:0x0502, param_name:'ttc.rssi',
     check_type:0, check_type_name:'absolute', low_limit:-110, high_limit:-60,
     enabled:false, severity:'WARNING', current_value:-80,
     status:'IN_LIMITS', violation_count:0, onboard:true},
  ],
  rules: [
    {ea_id:1001, name:'eps_bus_undervoltage_payload_off', trigger_kind:0,
     trigger_kind_name:'event', trigger_id:0x0102,
     trigger_label:'event BATTERY_SOC_CRITICAL (0x0102)', action_func_id:20,
     action_label:'20 - EPS_POWER_LINE_OFF', enabled:true, fire_count:2,
     last_fire_ts:1700000000, fire_inferred:true, onboard:true},
    {ea_id:1002, name:'aocs_rate_high_safe_mode', trigger_kind:1,
     trigger_kind_name:'param', trigger_id:0x0207,
     trigger_label:'param aocs.rate_roll', action_func_id:2,
     action_label:'2 - AOCS_SET_MODE', enabled:false, fire_count:0,
     last_fire_ts:null, fire_inferred:true, onboard:true},
  ],
  journal: [
    {seq:4, ts:1700000003, kind:'rule', ea_id:1001,
     label:'eps_bus_undervoltage_payload_off', action:'20 - EPS_POWER_LINE_OFF',
     trigger_id:0x0102, trigger_kind:0, severity:3, inferred:true},
    {seq:3, ts:1700000002, kind:'event', event_id:0x0102, severity:4,
     description:'Battery SoC below 10%', label:'BATTERY_SOC_CRITICAL',
     subsystem:'eps'},
    {seq:2, ts:1700000001, kind:'s12_in', mon_id:0x8000, param_id:0x0101,
     value:17.5, severity:3, low_limit:20, high_limit:100,
     label:'EPS Battery SoC Warning'},
    {seq:1, ts:1700000000, kind:'mode', label:'SC MODE -> SAFE', severity:3},
  ],
  active_alarms: [{id:1, severity:'HIGH', subsystem:'eps', parameter:'0x0101'}],
};
renderFdirWindow();
"""


@requires_node
class TestFdirWindowRendering:
    def test_state_strip_shows_level_mode_and_latches(self, tmp_path):
        out = run_js(f"""
            {FDIR_JS}
            return {{
              level: document.getElementById('fdir-level').textContent,
              levelClass: document.getElementById('fdir-level-box').className,
              mode: document.getElementById('fdir-sc-mode').textContent,
              modeSrc: document.getElementById('fdir-sc-mode-src').textContent,
              safe: document.getElementById('fdir-latch-safe').className,
              emg: document.getElementById('fdir-latch-emg').className,
              shed: document.getElementById('fdir-load-shed').textContent,
              ool: document.getElementById('fdir-ool-count').textContent,
              action: document.getElementById('fdir-last-action').textContent,
            }};
        """, tmp_path)
        assert out["level"] == "SUBSYSTEM"
        assert "lvl-subsystem" in out["levelClass"]
        assert out["mode"] == "SAFE"
        assert "simulator state" in out["modeSrc"], \
            "sc_mode is not telemetry — the display must say where it came from"
        assert "on" in out["safe"].split()          # SAFE latch lit
        assert "on" not in out["emg"].split()       # EMERGENCY latch dark
        assert out["shed"] == "L2"
        assert str(out["ool"]) == "2"
        assert "S19 rule 1001" in out["action"]

    def test_monitor_table_carries_limits_value_and_status(self, tmp_path):
        out = run_js(f"""{FDIR_JS}
            return document.getElementById('fdir-mon-tbody').innerHTML;""", tmp_path)
        assert out.count("<tr") == 3
        assert "EPS Battery SoC Warning" in out
        assert "eps.bat_soc" in out and "0x0101" in out
        assert "20.00 … 100.00" in out
        assert "OUT OF LIMITS" in out
        assert "absolute" in out and "delta" in out
        assert "fdir-row-ool" in out
        assert "fdir-row-disabled" in out, "a disabled monitor must read as disabled"

    def test_monitor_table_offers_only_real_commands(self, tmp_path):
        out = run_js(f"""{FDIR_JS}
            return document.getElementById('fdir-mon-tbody').innerHTML;""", tmp_path)
        # TC(12,3)/TC(12,4)/TC(12,7) all exist in tc_catalog.yaml.
        assert "s12EnableDef(" in out and "s12DisableDef(" in out
        assert "s12DeleteDef(" in out

    def test_out_of_limits_filter(self, tmp_path):
        out = run_js(f"""
            {FDIR_JS}
            document.getElementById('fdir-mon-ool-only').checked = true;
            renderFdirMonitors();
            const ool = document.getElementById('fdir-mon-tbody').innerHTML;
            document.getElementById('fdir-mon-ool-only').checked = false;
            document.getElementById('fdir-mon-filter').value = 'rssi';
            renderFdirMonitors();
            return {{ oolRows: (ool.match(/<tr/g) || []).length,
                      byName: document.getElementById('fdir-mon-tbody').innerHTML }};
        """, tmp_path)
        assert out["oolRows"] == 2
        assert "TTC RSSI" in out["byName"]
        assert "EPS Battery SoC Warning" not in out["byName"]

    def test_rule_table_shows_trigger_action_and_fire_history(self, tmp_path):
        out = run_js(f"""{FDIR_JS}
            return document.getElementById('fdir-rule-tbody').innerHTML;""", tmp_path)
        assert out.count("<tr") == 2
        assert "eps_bus_undervoltage_payload_off" in out
        assert "BATTERY_SOC_CRITICAL" in out
        assert "EPS_POWER_LINE_OFF" in out
        assert "inferred" in out, "a ground-inferred fire count must say so"
        assert "s19EnableRule(" in out and "s19DisableRule(" in out

    def test_journal_correlates_all_four_kinds(self, tmp_path):
        out = run_js(f"""{FDIR_JS}
            return document.getElementById('fdir-journal').innerHTML;""", tmp_path)
        for kind in ("k-rule", "k-event", "k-s12_in", "k-mode"):
            assert kind in out, kind
        assert "S19 FIRE" in out and "S12 OOL" in out and "SC MODE" in out
        assert "17.50 outside [20.00, 100.00]" in out
        assert "BATTERY_SOC_CRITICAL" in out

    def test_journal_filters_hide_entries(self, tmp_path):
        out = run_js(f"""
            {FDIR_JS}
            // The DOM stub creates checkboxes unchecked; the real markup ships
            // all four filters checked.
            ['fdir-jf-event','fdir-jf-s12','fdir-jf-rule','fdir-jf-mode']
              .forEach(id => document.getElementById(id).checked = true);
            document.getElementById('fdir-jf-event').checked = false;
            renderFdirJournal();
            return document.getElementById('fdir-journal').innerHTML;
        """, tmp_path)
        assert "k-event hidden-je" in out
        assert "k-rule hidden-je" not in out
        assert "k-s12_in hidden-je" not in out

    def test_empty_journal_says_so(self, tmp_path):
        out = run_js("""
            fdirData = { journal: [] }; renderFdirJournal();
            return document.getElementById('fdir-journal').innerHTML;
        """, tmp_path)
        assert "No FDIR activity" in out

    def test_pus_tab_tables_come_from_the_same_payload(self, tmp_path):
        """#s12-tbody and #s19-tbody were never filled by anything."""
        out = run_js(f"""
            {FDIR_JS}
            renderS12Table(); renderS19Table();
            return {{ s12: document.getElementById('s12-tbody').innerHTML,
                      s19: document.getElementById('s19-tbody').innerHTML,
                      s12note: document.getElementById('s12-source-note').textContent,
                      s19note: document.getElementById('s19-source-note').textContent }};
        """, tmp_path)
        assert out["s12"].count("<tr") == 3
        assert out["s19"].count("<tr") == 2
        assert "EPS Battery SoC Warning" in out["s12"]
        assert "eps_bus_undervoltage_payload_off" in out["s19"]
        assert "reported by the spacecraft" in out["s12note"]
        assert "reported by the spacecraft" in out["s19note"]

    def test_ground_database_is_labelled_as_unconfirmed(self, tmp_path):
        out = run_js("""
            fdirData = { state: {s12_reported: false, s19_reported: false},
                         monitors: [], rules: [] };
            renderS12Table(); renderS19Table();
            return { s12: document.getElementById('s12-source-note').textContent,
                     s19: document.getElementById('s19-source-note').textContent };
        """, tmp_path)
        assert "not yet confirmed on board" in out["s12"]
        assert "not yet confirmed on board" in out["s19"]


# ── S12 / S19 command packing from the panels ───────────────────────

@requires_node
class TestFdirCommandPacking:
    def test_s12_add_sends_the_13_byte_form_with_a_monitor_id(self, tmp_path):
        """The old panel sent the 11-byte legacy form with no mon_id, so a
        parameter could carry only one monitor."""
        out = run_js("""
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({s, st, d, n});
            document.getElementById('mon-id').value = '0x8000';
            document.getElementById('mon-pid').value = '0x0101';
            document.getElementById('mon-check').value = '0';
            document.getElementById('mon-low').value = '20';
            document.getElementById('mon-high').value = '100';
            s12Add();
            return sent[0];
        """, tmp_path)
        assert out["s"] == 12 and out["st"] == 6
        assert out["d"] == "80 00 01 01 00 41 A0 00 00 42 C8 00 00"
        assert len(out["d"].split()) == 13

    def test_s19_add_sends_the_6_byte_form_with_a_16_bit_trigger(self, tmp_path):
        """The 4-byte legacy form had a ONE-byte trigger id; every EOSAT-1 event
        id is >= 0x0100, so no rule added from this console could ever fire."""
        out = run_js("""
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({s, st, d, n});
            document.getElementById('ea-id').value = '2001';
            document.getElementById('ea-kind').value = '1';
            document.getElementById('ea-trigger').value = '0x0101';
            document.getElementById('ea-action').value = '20';
            s19Add();
            return sent[0];
        """, tmp_path)
        assert out["s"] == 19 and out["st"] == 1
        assert out["d"] == "07 D1 01 01 01 14"
        assert len(out["d"].split()) == 6

    def test_per_definition_enable_disable_use_the_right_subtypes(self, tmp_path):
        out = run_js("""
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({s, st, d});
            s12EnableDef(0x8000); s12DisableDef(0x8000);
            s19EnableRule(1001); s19DisableRule(1001);
            return sent;
        """, tmp_path)
        assert [(e["s"], e["st"]) for e in out] == [(12, 3), (12, 4), (19, 4), (19, 5)]
        assert out[0]["d"] == "80 00"
        assert out[2]["d"] == "03 E9"

    def test_report_requests_are_the_catalogued_ones(self, tmp_path):
        out = run_js("""
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({s, st, n});
            fdirReportMonitors(); fdirReportRules();
            return sent;
        """, tmp_path)
        assert out[0]["s"] == 12 and out[0]["st"] == 12
        assert out[0]["n"] == "MON_REPORT_DEFS"
        assert out[1]["s"] == 19 and out[1]["st"] == 8
        assert out[1]["n"] == "EA_REPORT_DEFS"


# ── memory browser ──────────────────────────────────────────────────

MEMMAP_JS = """
memoryMap = {
  regions: [
    {name:'boot_rom', start:0, size:0x10000, end:0x10000, access:'ro',
     writable:false, fill_kind:'constant', description:'Boot ROM', binding:null},
    {name:'app_a', start:0x100000, size:0x80000, end:0x180000, access:'rw',
     writable:true, fill_kind:'address_high', description:'Image A', binding:null},
    {name:'aocs_gains', start:0x20100000, size:12, end:0x2010000C, access:'rw',
     writable:true, fill_kind:'zero', description:'Gains',
     binding:{model:'aocs', setter:'set_torque_gain'}},
  ],
  limits: {max_load_bytes:512, max_dump_bytes:1024, max_check_bytes:0x100000},
};
memoryMapLoaded = true;
renderMemoryMap();
"""


@requires_node
class TestMemoryBrowser:
    def test_map_table_renders_every_region_with_access(self, tmp_path):
        out = run_js(f"""{MEMMAP_JS}
            return document.getElementById('mem-map-tbody').innerHTML;""", tmp_path)
        assert out.count("<tr") == 3
        assert "boot_rom" in out and "app_a" in out and "aocs_gains" in out
        assert "0x00000000" in out and "0x20100000" in out
        assert ">RO<" in out
        assert "BOUND" in out, "a live-bound region must be flagged"

    def test_missing_map_is_reported_honestly(self, tmp_path):
        out = run_js("""
            memoryMap = {regions: [], limits: {}}; memoryMapLoaded = false;
            renderMemoryMap();
            return { body: document.getElementById('mem-map-tbody').innerHTML,
                     src: document.getElementById('mem-map-source').textContent };
        """, tmp_path)
        assert "undeclared" in out["body"]
        assert "UNAVAILABLE" in out["src"]

    def test_dump_uses_the_catalogued_memory_id_form(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({{s, st, d}});
            document.getElementById('mem-addr').value = '0x00100000';
            document.getElementById('mem-len').value = '256';
            document.getElementById('mem-id').value = '0';
            memDump();
            return sent[0];
        """, tmp_path)
        assert out["s"] == 6 and out["st"] == 5
        # memory_id(1) + address(4) + length(2) = 7 bytes
        assert out["d"] == "00 00 10 00 00 01 00"

    def test_patch_into_a_readonly_region_is_refused_before_uplink(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({{s, st, d}});
            document.getElementById('mem-addr').value = '0x00000000';
            document.getElementById('mem-patch-data').value = 'DE AD BE EF';
            memPatchLoad();
            return {{ sent: sent.length,
                      status: document.getElementById('mem-patch-status').innerHTML }};
        """, tmp_path)
        assert out["sent"] == 0
        assert "0x0002" in out["status"] and "write protected" in out["status"]

    def test_patch_outside_any_region_is_refused(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            const sent = [];
            sendPusCmd = () => sent.push(1);
            document.getElementById('mem-addr').value = '0xDEADBEEF';
            document.getElementById('mem-patch-data').value = 'DEAD';
            memPatchLoad();
            return {{ sent: sent.length,
                      status: document.getElementById('mem-patch-status').innerHTML }};
        """, tmp_path)
        assert out["sent"] == 0
        assert "0x0001" in out["status"]

    def test_load_then_verify_produces_a_match_verdict(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            const sent = [];
            sendPusCmd = (s, st, d, n) => sent.push({{s, st, d}});
            document.getElementById('mem-addr').value = '0x00100000';
            document.getElementById('mem-patch-data').value = 'DE AD BE EF';
            memPatchLoad();
            memPatchVerify();
            renderMemoryCheck({{address: 0x00100000, length: 4, crc: 0x4097,
                               region: 'app_a'}});
            return {{ sent: sent, expected: memLastPatch.crc,
                      table: document.getElementById('mem-check-tbody').innerHTML }};
        """, tmp_path)
        assert out["sent"][0]["s"] == 6 and out["sent"][0]["st"] == 2
        assert out["sent"][0]["d"] == "00 00 10 00 00 DE AD BE EF"
        assert out["sent"][1]["s"] == 6 and out["sent"][1]["st"] == 9
        assert out["expected"] == 0x4097, "ground CRC-16-CCITT must match the OBC's"
        assert "MATCH" in out["table"] and "MISMATCH" not in out["table"]

    def test_a_wrong_crc_is_a_mismatch(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            sendPusCmd = () => {{}};
            document.getElementById('mem-addr').value = '0x00100000';
            document.getElementById('mem-patch-data').value = 'DE AD BE EF';
            memPatchLoad();
            renderMemoryCheck({{address: 0x00100000, length: 4, crc: 0x1234}});
            return document.getElementById('mem-check-tbody').innerHTML;
        """, tmp_path)
        assert "MISMATCH" in out

    def test_load_over_the_limit_is_refused(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            const sent = [];
            sendPusCmd = () => sent.push(1);
            document.getElementById('mem-addr').value = '0x00100000';
            document.getElementById('mem-patch-data').value = 'AA'.repeat(600);
            memPatchLoad();
            return {{ sent: sent.length,
                      status: document.getElementById('mem-patch-status').innerHTML }};
        """, tmp_path)
        assert out["sent"] == 0
        assert "512" in out["status"]

    def test_dump_is_rendered_as_offset_hex_and_ascii(self, tmp_path):
        out = run_js(f"""
            {MEMMAP_JS}
            renderMemoryDump({{address: 0x00100000, length: 18,
                              data_hex: '48656C6C6F2C20776F726C6421000102030405',
                              region: 'app_a'}});
            return document.getElementById('mem-dump-result').innerHTML;
        """, tmp_path)
        assert "00100000" in out and "00100010" in out, "offsets per 16-byte line"
        assert "48 65 6C 6C 6F" in out
        assert "Hello, world!" in out, "printable bytes must show as ASCII"
        assert "app_a" in out


# ── ground stations ─────────────────────────────────────────────────

STATIONS_JS = """
groundStations = [
  {name:'Iqaluit', id:'iqaluit', lat_deg:63.747, lon_deg:-68.518, alt_km:0.034,
   min_elevation_deg:5.0, color:'#3b82f6', antenna_diameter_m:9.0, band:'S-band', g_t_db:20.0},
  {name:'Troll', id:'troll', lat_deg:-72.012, lon_deg:2.535, alt_km:1.275,
   min_elevation_deg:5.0, color:'#22c55e'},
  {name:'Svalbard', id:'svalbard', lat_deg:78.2297, lon_deg:15.3975, alt_km:0.458,
   min_elevation_deg:5.0, color:'#f59e0b'},
  {name:'Inuvik', id:'inuvik', lat_deg:68.3195, lon_deg:-133.5495, alt_km:0.102,
   min_elevation_deg:5.0, color:'#a78bfa'},
  {name:"O'Higgins", id:'o-higgins', lat_deg:-63.3211, lon_deg:-57.9008, alt_km:0.032,
   min_elevation_deg:5.0, color:'#ec4899'},
];
renderGsPanels(); renderContactLegend();
"""


@requires_node
class TestGroundStationPanels:
    def test_a_card_is_built_for_every_station(self, tmp_path):
        out = run_js(f"""{STATIONS_JS}
            return document.getElementById('gs-panels').innerHTML;""", tmp_path)
        for sid in ("iqaluit", "troll", "svalbard", "inuvik", "o-higgins"):
            assert f'id="gs-{sid}-panel"' in out, sid
            assert f'id="gs-{sid}-el"' in out, sid
        assert "O&#x27;HIGGINS" in out or "O&#39;HIGGINS" in out or "O'HIGGINS" in out

    def test_selector_has_one_button_per_station(self, tmp_path):
        out = run_js(f"""{STATIONS_JS}
            return document.getElementById('gs-selector').innerHTML;""", tmp_path)
        assert out.count("gs-selector-btn") == 5

    def test_rf_augmentation_is_shown_when_present(self, tmp_path):
        out = run_js(f"""{STATIONS_JS}
            return document.getElementById('gs-panels').innerHTML;""", tmp_path)
        assert "9 m dish" in out and "S-band" in out and "G/T 20 dB/K" in out

    def test_contact_legend_lists_all_five(self, tmp_path):
        out = run_js(f"""{STATIONS_JS}
            return document.getElementById('contact-legend-stations').innerHTML;""",
                     tmp_path)
        assert out.count("contact-legend-dot") == 5
        assert "#f59e0b" in out and "#ec4899" in out

    def test_colours_come_from_the_list_not_a_two_entry_map(self, tmp_path):
        out = run_js(f"""{STATIONS_JS}
            return {{ svalbard: gsColorFor('Svalbard'),
                      inuvik: gsColorFor('Inuvik'),
                      unknown: gsColorFor('Nowhere') }};""", tmp_path)
        assert out["svalbard"] == "#f59e0b"
        assert out["inuvik"] == "#a78bfa"
        assert out["unknown"] == "#6BB3FF"

    def test_tracking_state_follows_the_active_station(self, tmp_path):
        out = run_js(f"""
            {STATIONS_JS}
            state = {{ in_contact: true, ground_station_name: 'Svalbard',
                      gs_elevation_deg: 42.0, gs_range_km: 900.0, ttc: {{}} }};
            updateGsDisplay();
            return {{ sval: document.getElementById('gs-svalbard-status-text').textContent,
                      iqa: document.getElementById('gs-iqaluit-status-text').textContent,
                      el: document.getElementById('gs-svalbard-el').textContent }};
        """, tmp_path)
        assert out["sval"] == "TRACKING"
        assert out["iqa"] == "IDLE"
        assert out["el"].startswith("42.0")

    def test_no_station_tracks_out_of_contact(self, tmp_path):
        out = run_js(f"""
            {STATIONS_JS}
            state = {{ in_contact: false, ground_station_name: '', ttc: {{}} }};
            updateGsDisplay();
            return groundStations.map(g =>
              document.getElementById('gs-' + g.id + '-status-text').textContent);
        """, tmp_path)
        assert set(out) == {"IDLE"}

    def test_empty_list_is_reported_honestly(self, tmp_path):
        out = run_js("""
            groundStations = []; renderGsPanels();
            return document.getElementById('gs-panels').innerHTML;
        """, tmp_path)
        assert "unavailable" in out


# ── FDIR tab reachability ───────────────────────────────────────────

@requires_node
class TestFdirTabWiring:
    def test_the_tab_and_pane_exist(self):
        page = INDEX.read_text()
        assert 'data-tab="fdir"' in page
        assert 'id="pane-fdir"' in page

    def test_flight_director_and_fdir_systems_both_see_it(self, tmp_path):
        out = run_js("""
            const fd = {subsystems: [], visible_tabs: []};
            const fdirSeat = {subsystems: ['obdh','fdir'],
                              visible_tabs: ['overview','obdh','commanding','pus']};
            const eps = {subsystems: ['eps'], visible_tabs: ['overview','eps']};
            return {
              fd:      tabVisibleForPosition('fdir', fd, true),
              fdir:    tabVisibleForPosition('fdir', fdirSeat, false),
              eps:     tabVisibleForPosition('fdir', eps, false),
              epsOwn:  tabVisibleForPosition('eps', eps, false),
              epsPus:  tabVisibleForPosition('pus', eps, false),
            };
        """, tmp_path)
        assert out["fd"] is True
        assert out["fdir"] is True, "the FDIR seat must be able to open the window"
        assert out["eps"] is False
        # the ordinary visible_tabs path is unchanged
        assert out["epsOwn"] is True and out["epsPus"] is False

    def test_the_fdir_seat_is_configured_to_own_the_discipline(self):
        """The derivation above relies on positions.yaml, which is read-only here."""
        import yaml
        positions = yaml.safe_load(
            (REPO / "configs/eosat1/mcs/positions.yaml").read_text())
        seat = positions["positions"]["fdir_systems"]
        assert "fdir" in seat["subsystems"]

    def test_the_dead_display_stack_is_gone(self):
        static = INDEX.parent
        assert not (static / "displays.js").exists()
        assert not (static / "displays.css").exists()
