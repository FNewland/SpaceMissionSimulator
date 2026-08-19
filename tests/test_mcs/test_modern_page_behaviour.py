"""Behavioural + static tests for the MODERN MCS console (plan §2.1, Agent 2).

Two layers:

* **Node-harness tests** — `tests/test_mcs/js/modern_harness.js` imports
  `static/modern/mcs.js` as a real ES module (shared imports rewritten to the
  smo-common files on disk) under a DOM stub, and drives the module's `__test`
  export. Covered: SA-header population from a state fixture, the alarm
  drawer render + acknowledge path, the P3 parameter triad with limits, rail
  position filtering, TRAINING-chip visibility per time mode, the command
  codec (big-endian SIDs, catalogue encode/decode round-trip, CRC16), and the
  display-wide staleness gate.

* **Static assertions** — every `/api/...` path referenced by mcs.js exists in
  the MCS server route table; the modern page never references the sim-named
  state route nor the sim-clock-jump field (dispositions M10/M12); Date.now()
  appears only inside the TIME section (M3: simNow is the single primitive);
  the classic page is byte-untouched (Agent-1 pinned sha); every data-param
  in schematics.svg resolves against parameters.yaml (plan §5 content gate);
  display_config.yaml parses to the shape /api/display-config serves
  (and no retired guided-mode plumbing remains).

NOT covered (browser/E2E, Agent 5): layout, CSS, focus behaviour, real WS.
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
MODERN = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "modern"
MCS_JS = MODERN / "mcs.js"
SVG = MODERN / "schematics.svg"
INDEX = MODERN / "index.html"
CLASSIC = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
SERVER = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "server.py"
HARNESS = Path(__file__).resolve().parent / "js" / "modern_harness.js"
DISPLAY_CFG = REPO / "configs" / "eosat1" / "mcs" / "display_config.yaml"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(NODE is None, reason="node required")

# Agent-1 baseline (tests/test_common/test_classic_untouched.py).
# Re-pinned 2026-08d (TZ-1): see tests/test_common/
# test_classic_untouched.py for why the classic page changed.
CLASSIC_SHA = "049d00b55de192552e48ea739bcd36f919d4423add347c3e7a91ed7252f6be01"


def run_js(snippet: str, tmp_path: Path):
    f = tmp_path / "snippet.js"
    f.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(REPO), str(f)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


STATE_FIXTURE = """
const state = {
  sim_time: "2026-08-17T12:00:00Z", ground_utc: "2026-08-17T12:00:00Z",
  met_s: 3600, speed: 1.0, in_contact: true, unacked_alarms: 2,
  last_frame_age_s: 1.2,
  sa: { link: "live", last_frame_age_s: 1.2, tm_tier: 2, tm_tier_name: "LOW",
        tm_tier_demoted: false, store_fill_pct: 41.5, store_fill_source: "S15.14",
        unacked_alarms: 2, speed: 1.0 },
  params: { "0x0101": 61.4, "0x0105": 27.9, "0x0510": 1, "0x0511": 1,
            "0x0512": 1, "0x0134": 0, "0x0700": 0 },
  params_age: { "257": 1.0, "261": 1.0, "1296": 1.0, "1297": 1.0,
                "1298": 1.0, "308": 1.0, "1792": 1.0 },
  param_limit_state: { "0x0101": "nominal", "0x0105": "nominal" },
  subsystem_staleness: { eps: 1.0 },
  obdh: { mode: 0 },
};
"""


# ── node-harness behavioural tests ──────────────────────────────────────────

@requires_node
class TestSaHeader:
    def test_populates_from_state_fixture(self, tmp_path):
        out = run_js(STATE_FIXTURE + """
            T.handleState(state);
            return {
              see: __registry["sa-see-v"].textContent,
              seeSub: __registry["sa-see-s"].textContent,
              talk: __registry["sa-talk-v"].textContent,
              well: __registry["sa-well-v"].textContent,
              unack: __registry["alarm-unack-n"].textContent,
            };
        """, tmp_path)
        assert out["see"].startswith("LIVE")
        assert "tier LOW" in out["seeSub"] and "store 42" in out["seeSub"]
        assert out["talk"].startswith("LINK")
        # sc mode is TM-derived (0x0700 = 0 -> NOMINAL), with provenance
        assert out["well"].startswith("NOMINAL")
        assert out["unack"] == "2"

    def test_sc_mode_provenance_prefers_tm(self, tmp_path):
        """2026-08c item 7 (M8 done right): obdh.mode 0x0300 is PRIMARY —
        0x0700 is fdir.level (0=nominal..3=critical), NOT a mode enum, and
        reading it as one kept the label NOMINAL while the OBC sat in SAFE
        (the dark contingency banner)."""
        out = run_js(STATE_FIXTURE + """
            T.handleState(state);
            T.S.state.params["0x0300"] = 1;             // OBC SAFE in TM
            const fromParam = T.scModeInfo();
            delete T.S.state.params["0x0300"];
            const fromGroup = T.scModeInfo();           // state.obdh.mode = 0
            T.S.state.params["0x0700"] = 3;             // FDIR critical
            const fdir = T.fdirLevelInfo();
            return { fromParam, fromGroup, fdir };
        """, tmp_path)
        assert out["fromParam"] == {"label": "SAFE",
                                    "src": "TM obdh.mode 0x0300"}
        assert out["fromGroup"]["label"] == "NOMINAL"
        assert out["fromGroup"]["src"] == "TM obdh.mode"
        assert out["fdir"] == 3

    def test_stale_state_shows_no_tm(self, tmp_path):
        out = run_js("""
            T.handleState({ sa: {}, params: {}, params_age: {} });
            return { see: __registry["sa-see-v"].textContent,
                     verdict: T.tmFreshness({}, null) };
        """, tmp_path)
        assert out["see"] == "NO TM"
        assert out["verdict"] == "stale"


@requires_node
class TestTrainingChip:
    """M1/M11: chip only when the ground clock is simulator time."""

    def test_hidden_in_real_mode_even_at_speed(self, tmp_path):
        out = run_js("""
            T.S.timeMode = "real";
            T.S.state = { speed: 5.0 };
            return T.trainingChipState();
        """, tmp_path)
        assert out["visible"] is False

    def test_visible_in_sim_mode_with_speed(self, tmp_path):
        out = run_js("""
            T.S.timeMode = "sim";
            T.S.state = { speed: 5.0 };
            const a = T.trainingChipState();
            T.S.state = { speed: 0 };
            const b = T.trainingChipState();
            return { a, b };
        """, tmp_path)
        assert out["a"] == {"visible": True, "text": "TRAINING · SIM x5"}
        assert out["b"] == {"visible": True, "text": "TRAINING · PAUSED"}

    def test_unknown_mode_uses_speed_heuristic(self, tmp_path):
        out = run_js("""
            T.S.timeMode = "unknown";
            T.S.state = { speed: 1.0 };
            const one = T.trainingChipState();
            T.S.state = { speed: 2.0 };
            const two = T.trainingChipState();
            return { one, two };
        """, tmp_path)
        assert out["one"]["visible"] is False       # speed 1.0: no sim evidence
        assert out["two"]["visible"] is True


@requires_node
class TestAlarmDrawer:
    ALARMS = """
        T.S.alarms = [
          { id: "a1", timestamp: 1755432000, subsystem: "eps", severity: 3,
            message: "line overcurrent", param_id: 0x011B, value: 2.31,
            acknowledged: false,
            procedure: { path: "eps_overcurrent.md", name: "EPS OVERCURRENT" } },
          { id: "a2", timestamp: 1755432060, subsystem: "aocs", severity: 2,
            message: "gyro temp high", acknowledged: true },
        ];
    """

    def test_renders_rows_with_procedure_and_chain_links(self, tmp_path):
        out = run_js(self.ALARMS + """
            T.renderAlarmDrawer();
            return __registry["alarm-list"].innerHTML;
        """, tmp_path)
        assert "line overcurrent" in out
        assert 'data-act="ack"' in out            # per-row ACK
        assert 'data-act="procedure"' in out      # alarm -> procedure (P7.6)
        assert 'data-chain="a1"' in out           # causal chain link (P9)
        assert "ds-alarm-row" in out and "unacked" in out

    def test_ack_updates_local_state_and_posts(self, tmp_path):
        out = run_js(self.ALARMS + """
            await T.ackAlarm("a1");
            return {
              acked: T.S.alarms[0].acknowledged,
              posts: __fetchLog.filter(f => f.method === "POST").map(f => f.url),
            };
        """, tmp_path)
        assert out["acked"] is True
        assert "/api/alarms/a1/ack" in out["posts"]

    def test_severity_filter_blank_tolerant(self, tmp_path):
        out = run_js(self.ALARMS + """
            byId("alarm-f-sev").value = "3";
            const high = T.filteredAlarms().map(a => a.id);
            byId("alarm-f-sev").value = "0";
            byId("alarm-f-sub").value = "aocs";
            const aocs = T.filteredAlarms().map(a => a.id);
            return { high, aocs };
        """, tmp_path)
        assert out["high"] == ["a1"]
        assert out["aocs"] == ["a2"]

    def test_priority_and_rationalisation_blank_tolerant(self, tmp_path):
        # A row with neither priority nor rationalisation must still render.
        out = run_js("""
            T.S.alarms = [{ id: "x", severity: 3, subsystem: "ttc",
                            message: "bare alarm", acknowledged: false }];
            T.renderAlarmDrawer();
            return __registry["alarm-list"].innerHTML;
        """, tmp_path)
        assert "bare alarm" in out
        assert re.search(r'class="ds-alarm-row p\d', out)


@requires_node
class TestParamTriad:
    def test_renders_with_limits_and_state(self, tmp_path):
        out = run_js("""
            T.S.paramByHex["0x0101"] = { hex: "0x0101", name: "eps.bat_soc", units: "%" };
            T.S.limits["0x0101"] = { yellow_low: 25, yellow_high: 95,
                                     red_low: 15, red_high: 100, units: "%" };
            T.S.state = { params: { "0x0101": 22.0 },
                          params_age: { "257": 1.0 },
                          param_limit_state: { "0x0101": "yellow_low" } };
            return T.paramTriad("0x0101");
        """, tmp_path)
        assert "ds-param" in out and "eps.bat_soc" in out
        assert 'class="ds-param w"' in out          # yellow -> warn class
        assert "ds-devbar" in out                   # deviation bar present

    def test_no_limit_affordance(self, tmp_path):
        out = run_js("""
            T.S.paramByHex["0x0606"] = { hex: "0x0606", name: "payload.scene_id" };
            T.S.state = { params: { "0x0606": 7 }, params_age: { "1542": 0.5 },
                          param_limit_state: {} };
            return T.paramTriad("0x0606");
        """, tmp_path)
        assert "no limit defined" in out


@requires_node
class TestPositionFiltering:
    POSITIONS = """
        T.S.positions = {
          flight_director: { label: "Flight Director", subsystems: ["fdir"],
            visible_tabs: ["overview","eps","aocs","tcs","obdh","ttc","payload",
                           "prop","commanding","pus","procedures","trends","manual"] },
          eps_tcs: { label: "Power & Thermal", subsystems: ["eps","tcs"],
            visible_tabs: ["overview","eps","tcs","commanding","procedures",
                           "trends","manual"] },
        };
        T.buildRail();
    """

    def test_eps_tcs_hides_foreign_pages(self, tmp_path):
        out = run_js(self.POSITIONS + """
            T.S.position = "eps_tcs";
            T.applyPositionFilter();
            const h = id => __registry["rail-" + id].hidden;
            return { eps: h("eps"), tcs: h("tcs"), aocs: h("aocs"),
                     cmd: h("cmd"), diag: h("diag"), fdir: h("fdir"),
                     ovr: h("ovr"), man: h("man") };
        """, tmp_path)
        assert out == {"eps": False, "tcs": False, "aocs": True,
                       "cmd": False, "diag": True, "fdir": True,
                       "ovr": False, "man": False}

    def test_flight_director_sees_all(self, tmp_path):
        out = run_js(self.POSITIONS + """
            T.S.position = "flight_director";
            T.applyPositionFilter();
            const h = id => __registry["rail-" + id].hidden;
            return [h("eps"), h("aocs"), h("fdir"), h("diag"), h("prp")];
        """, tmp_path)
        assert out == [False, False, False, False, False]

    def test_rail_visibility_predicate(self, tmp_path):
        out = run_js("""
            const pos = { subsystems: ["fdir"], visible_tabs: ["overview","pus"] };
            return {
              fdir: T.railVisibleForPosition("fdir", pos, false),
              diag: T.railVisibleForPosition("diag", pos, false),
              eps: T.railVisibleForPosition("eps", pos, false),
              seeAll: T.railVisibleForPosition("eps", pos, true),
            };
        """, tmp_path)
        assert out == {"fdir": True, "diag": True, "eps": False, "seeAll": True}


@requires_node
class TestCommandCodec:
    def test_sid_is_big_endian(self, tmp_path):
        out = run_js("""
            return { s1: T.sidToHex(1), s11: T.sidToHex(11),
                     s256: T.sidToHex(256), wide: T.sidToHex(0x1234) };
        """, tmp_path)
        assert out == {"s1": "00 01", "s11": "00 0B",
                       "s256": "01 00", "wide": "12 34"}

    def test_encode_decode_round_trip(self, tmp_path):
        out = run_js("""
            const cmd = { service: 8, subtype: 1, func_id: 0x24, fields: [
              { name: "line", type: "uint8" },
              { name: "setpoint", type: "float32" },
            ]};
            const hex = T.encodeCatalogCommand(cmd, { line: 3, setpoint: 2.5 });
            const dec = T.decodeCatalogArgs(cmd, hex.replace(/\\s+/g, "").slice(2));
            return { hex, dec };
        """, tmp_path)
        assert out["hex"].startswith("24 03 ")
        assert out["dec"]["ok"] is True
        assert out["dec"]["values"]["line"] == 3
        assert abs(out["dec"]["values"]["setpoint"] - 2.5) < 1e-6

    def test_crc16_ccitt(self, tmp_path):
        # 0xFFFF-seeded CCITT of "123456789" == 0x29B1 (well-known vector)
        out = run_js("""
            const ascii = "123456789".split("").map(c =>
              c.charCodeAt(0).toString(16).padStart(2, "0")).join(" ");
            return T.crc16Ccitt(ascii);
        """, tmp_path)
        assert out == 0x29B1

    def test_authority_says_who_owns_a_denied_command(self, tmp_path):
        out = run_js("""
            T.S.tcCatalog = [{ name: "PROP_ARM", service: 8, subtype: 1,
                               allowed: false, critical: true }];
            T.S.positions = {
              propulsion: { label: "Propulsion", allowed_commands: ["PROP_ARM"] },
              eps_tcs: { label: "Power & Thermal", allowed_commands: [] },
            };
            T.S.position = "eps_tcs";
            return T.commandAuthority("PROP_ARM");
        """, tmp_path)
        assert out["allowed"] is False
        assert out["text"] == "NOT PERMITTED — belongs to Propulsion"


def real_catalog_commands(*names):
    """The named commands from the REAL tc_catalog.yaml, as the /api/tc-catalog
    field shapes the builder consumes (fields[].unit/enum/range/calibration,
    command criticality/func_id). Encoding tests run against the mission's own
    catalogue metadata so they cannot drift from it."""
    cat = yaml.safe_load(
        (REPO / "configs" / "eosat1" / "commands" / "tc_catalog.yaml").read_text())
    by_name = {c["name"]: c for c in cat["commands"]}
    return {n: by_name[n] for n in names}


@requires_node
class TestCommandBuilderEncoding:
    """Defect 2026-08 #2: the modern builder must encode from ENGINEERING
    units through the catalogue schema — enum names, unit conversion in the
    encoder (never the operator), live validation, paste-to-decode."""

    CMDS = ["AOCS_SET_MODE", "PAYLOAD_COOLER_SETPOINT", "RW_SET_SPEED_BIAS",
            "SYS_SET_MISSION_PHASE", "SCHEDULE_TC"]

    def _snippet(self, body):
        cmds = real_catalog_commands(*self.CMDS)
        return f"const CAT = {json.dumps(cmds)};\n" + body

    def test_five_example_commands_encode_from_engineering_units(self, tmp_path):
        out = run_js(self._snippet("""
            return {
              aocs: T.encodeCatalogCommand(CAT.AOCS_SET_MODE, { mode: 4 }),
              aocsDefault: T.encodeCatalogCommand(CAT.AOCS_SET_MODE, {}),
              // operator types -15 (degC); the ENCODER produces centi-kelvin
              cooler: T.encodeCatalogCommand(CAT.PAYLOAD_COOLER_SETPOINT, { setpoint: -15 }),
              rw: T.encodeCatalogCommand(CAT.RW_SET_SPEED_BIAS,
                                         { wheel_idx: 2, bias_rpm: 150.5 }),
              phase: T.encodeCatalogCommand(CAT.SYS_SET_MISSION_PHASE, {}),
              sched: T.encodeCatalogCommand(CAT.SCHEDULE_TC,
                       { exec_time_cuc: 1000, tc_data: "11 22 33" }),
            };
        """), tmp_path)
        assert out["aocs"] == "00 04"                       # func 0 + enum NOMINAL_NADIR
        assert out["aocsDefault"] == "00 04"                # catalogue default = 4
        # -15.00 degC -> 25815 cK = 0x64D7 (tc_catalog.yaml worked example)
        assert out["cooler"] == "24 64 D7"
        assert out["rw"] == "08 02 43 16 80 00"             # func 8, u8, IEEE-754 BE
        assert out["phase"] == "78 06 00"                   # func 120, phase 6, flags 0
        assert out["sched"] == "00 00 03 E8 11 22 33"       # u32 CUC + hex tail

    def test_paste_decode_round_trips_to_engineering_units(self, tmp_path):
        out = run_js(self._snippet("""
            const cooler = T.decodeCatalogArgs(CAT.PAYLOAD_COOLER_SETPOINT, "64 D7");
            const rw = T.decodeCatalogArgs(CAT.RW_SET_SPEED_BIAS, "02 43 16 80 00");
            const sched = T.decodeCatalogArgs(CAT.SCHEDULE_TC, "00 00 03 E8 11 22 33");
            const short = T.decodeCatalogArgs(CAT.RW_SET_SPEED_BIAS, "02");
            return { cooler, rw, sched, short };
        """), tmp_path)
        assert out["cooler"]["ok"] and abs(out["cooler"]["values"]["setpoint"] + 15.0) < 1e-6
        assert out["rw"]["ok"] and out["rw"]["values"]["wheel_idx"] == 2
        assert abs(out["rw"]["values"]["bias_rpm"] - 150.5) < 1e-4
        assert out["sched"]["ok"] and out["sched"]["values"]["exec_time_cuc"] == 1000
        assert out["sched"]["values"]["tc_data"] == "11 22 33"
        assert out["short"]["ok"] is False and "too short" in out["short"]["error"]

    def test_validation_reports_the_rejection_reason(self, tmp_path):
        out = run_js(self._snippet("""
            const f = CAT.PAYLOAD_COOLER_SETPOINT.fields[0];
            const mode = CAT.AOCS_SET_MODE.fields[0];
            return {
              outside: T.catFieldError(f, -25),
              inside: T.catFieldError(f, -15),
              badEnum: T.catFieldError(mode, 42),
              goodEnum: T.catFieldError(mode, 4),
              empty: T.catFieldError(f, ""),
            };
        """), tmp_path)
        assert "outside [-20" in out["outside"] and "REJECT" in out["outside"]
        assert out["inside"] is None
        assert "not an enumerated value" in out["badEnum"]
        assert out["goodEnum"] is None
        assert "no value" in out["empty"]

    def test_widgets_are_schema_driven(self, tmp_path):
        out = run_js(self._snippet("""
            return {
              enumW: T.cmdFieldWidget(CAT.AOCS_SET_MODE.fields[0]),
              engW: T.cmdFieldWidget(CAT.PAYLOAD_COOLER_SETPOINT.fields[0]),
              bytesW: T.cmdFieldWidget(CAT.SCHEDULE_TC.fields[1]),
            };
        """), tmp_path)
        # enum -> dropdown with the value NAMES, catalogue default selected
        assert "<select" in out["enumW"] and "DETUMBLE" in out["enumW"]
        assert 'value="4" selected' in out["enumW"]
        # linear-calibrated -> engineering-unit number input with range + unit
        assert 'type="number"' in out["engW"] and "degC" in out["engW"]
        assert 'min="-20"' in out["engW"] and 'max="0"' in out["engW"]
        assert "[-20 … 0]" in out["engW"]
        # bytes tail -> hex input
        assert "hex bytes" in out["bytesW"]

    def test_criticality_string_gates_arm_execute(self, tmp_path):
        out = run_js(self._snippet("""
            return {
              phase: T.isCritical(CAT.SYS_SET_MISSION_PHASE),   // "critical"
              cooler: T.isCritical(CAT.PAYLOAD_COOLER_SETPOINT), // "caution"
              aocs: T.isCritical(CAT.AOCS_SET_MODE),             // none
              legacyBool: T.isCritical({ critical: true }),
            };
        """), tmp_path)
        assert out == {"phase": True, "cooler": False, "aocs": False,
                       "legacyBool": True}

    def test_provenance_uses_served_keys(self, tmp_path):
        out = run_js(self._snippet("""
            const cmd = Object.assign({}, CAT.PAYLOAD_COOLER_SETPOINT,
                                      { expected_data_bytes: 3 });
            return T.provenanceText(cmd);
        """), tmp_path)
        assert "S8.1 func 36 (0x24)" in out
        assert "3 data bytes" in out
        assert "readback payload.fpa_setpoint (0x0631)" in out
        assert "[object Object]" not in out
        assert "CAUTION" in out


@requires_node
class TestPusPanelsRenderFromFixtures:
    """Defect 2026-08 #1: every PUS panel renders real content from the
    server-shaped payloads (/api/fdir `rules`/`state`, /api/hk-structures
    `param_count`, TM(11,18) `exec_time_cuc`, TM(15,14) store rows)."""

    def test_rail_entry_is_named_pus(self, tmp_path):
        out = run_js("""
            T.buildRail();
            return __registry["nav-rail"].innerHTML;
        """, tmp_path)
        assert ">PUS<" in out           # discoverability: not "DIA"
        assert 'id="rail-diag"' in out  # page id unchanged

    def test_s19_renders_served_rules_key(self, tmp_path):
        # /api/fdir serves `rules` with trigger/action labels — the old code
        # read only ea_rules/s19 and always showed "no rule table".
        out = run_js("""
            T.S.fdir = { rules: [{ ea_id: 1001, name: "eps_bus_undervoltage",
              trigger_kind: 0, trigger_kind_name: "event",
              trigger_id: 258, trigger_label: "event BATTERY_SOC_CRITICAL (0x0102)",
              action_func_id: 10, action_label: "10 - AOCS_SLEW_TO",
              enabled: true, fire_count: 2 }] };
            return T.renderS19Block("diag");
        """, tmp_path)
        assert "eps_bus_undervoltage" in out
        assert "BATTERY_SOC_CRITICAL" in out
        assert "AOCS_SLEW_TO" in out
        assert "no rule table" not in out

    def test_s12_renders_name_value_and_violation_status(self, tmp_path):
        out = run_js("""
            T.S.fdir = { monitors: [{ mon_id: 32768, name: "EPS Battery SoC Warning",
              param_id: 257, param_name: "eps.bat_soc", check_type: 0,
              check_type_name: "absolute", low_limit: 20, high_limit: 100,
              enabled: true, severity: "WARNING", current_value: 12.4,
              status: "VIOLATION", violation_count: 3 }] };
            return T.renderS12Block("diag");
        """, tmp_path)
        assert "EPS Battery SoC Warning" in out
        assert "12.40" in out            # live value column
        assert "absolute" in out         # check-type NAME, not the raw 0
        assert "OOL" in out              # status VIOLATION -> out-of-limits chip

    def test_fdir_strip_reads_nested_state(self, tmp_path):
        out = run_js("""
            T.S.state = { params: {}, params_age: {} };
            T.S.fdir = { state: { fdir_level: "system", safe_latched: true,
              emergency_latched: false, latch_source: "derived_from_sc_mode",
              load_shed_stage: 2, monitors_out_of_limits: 3 },
              monitors: [], journal: [], active_alarms: [] };
            T.renderFdirPage();
            return __registry["fdir-strip"].innerHTML;
        """, tmp_path)
        assert "system" in out
        assert "SAFE" in out
        assert ">2<" in out              # load shed stage
        assert ">3<" in out              # monitors OOL count

    def test_s3_renders_apid_and_param_count(self, tmp_path):
        out = run_js("""
            T.S.hkStructures = { structures: [
              { sid: 1, name: "EPS", apid: 1, interval_s: 1.0,
                param_count: 52, last_seen_age_s: 0.5 } ] };
            T.S.state = { hk_sid_age_s: { "1": 0.5 } };
            T.renderS3Panel();
            return __registry["diag-s3"].innerHTML;
        """, tmp_path)
        assert ">52<" in out             # param_count (was always 0)
        assert "EPS" in out and "PERIODIC" in out

    def test_s11_renders_report_entries(self, tmp_path):
        out = run_js("""
            T.S.state = { time_correlation: { epoch: "2000-01-01T12:00:00Z" } };
            T.S.s11Schedule = { count: 1, enabled: true, next_id: 2, entries: [
              { id: 1, exec_time_cuc: 820497600, apid: 10, service: 8,
                subtype: 1, data_len: 2, data_hex: "0004" } ] };
            T.renderS11Table();
            return __registry["s11-table"].innerHTML;
        """, tmp_path)
        assert "0004" in out             # data_hex column (was blank)
        assert "S8,1" in out
        assert "2026-01-01" in out       # CUC -> UTC conversion
        assert "ENABLED" in out and "next id 2" in out

    def test_s15_renders_store_report_details(self, tmp_path):
        out = run_js("""
            T.S.state = { storage_status: [
              { id: 1, name: "HK Store", count: 120, capacity: 1000,
                enabled: true, fill_pct: 12.0, overflow: false,
                circular: true, wrap_count: 0 } ] };
            T.renderS15Panel();
            return __registry["diag-s15"].innerHTML;
        """, tmp_path)
        assert "12.0 % full" in out
        assert "120 / 1000 packets" in out
        assert "ENABLED" in out

    def test_s9_and_params_panels_render(self, tmp_path):
        out = run_js("""
            T.S.state = { time_correlation: { sc_cuc_coarse: 1000, sc_cuc_fine: 0,
              epoch: "2000-01-01T12:00:00Z", offset_s: 0.2,
              offset_state: "nominal", time_ref_name: "GROUND",
              time_since_sync_s: 12 }, params: {}, params_age: {} };
            T.S.paramCatalog = [{ hex: "0x0101", name: "eps.bat_soc",
              subsystem: "eps", units: "%", in_hk: true, on_demand: false }];
            T.renderS9Panel();
            T.renderParamsPanel();
            return { s9: __registry["diag-s9"].innerHTML,
                     params: __registry["diag-params"].innerHTML };
        """, tmp_path)
        assert "Time correlation (S9)" in out["s9"] and "GROUND" in out["s9"]
        assert "eps.bat_soc" in out["params"] and "1 parameters" in out["params"]


@requires_node
class TestStalenessGate:
    def test_verdicts(self, tmp_path):
        out = run_js("""
            return {
              fresh: T.tmFreshness({ last_frame_age_s: 1.0 }, null),
              degraded: T.tmFreshness({ last_frame_age_s: 8.0 }, null),
              stale: T.tmFreshness({ last_frame_age_s: 60.0 }, null),
              subFresh: T.tmFreshness({ subsystem_staleness: { eps: 2.0 },
                                        last_frame_age_s: 60.0 }, "eps"),
              never: T.tmFreshness({}, null),
            };
        """, tmp_path)
        assert out == {"fresh": "fresh", "degraded": "degraded",
                       "stale": "stale", "subFresh": "fresh", "never": "stale"}


@requires_node
class TestSharedS12S19Renderers:
    def test_one_implementation_two_mounts(self, tmp_path):
        out = run_js("""
            T.S.fdir = { monitors: [{ mon_id: 1, param_id: 0x0101, check_type: 0,
              low_limit: 20, high_limit: 90, enabled: true, out_of_limits: true }],
              ea_rules: [{ ea_id: 7, trigger_kind: 1, trigger_value: 0x0101,
                           action: "EPS_LOAD_SHED", enabled: true }] };
            return { s12d: T.renderS12Block("diag"), s12f: T.renderS12Block("fdir"),
                     s19d: T.renderS19Block("diag"), s19f: T.renderS19Block("fdir") };
        """, tmp_path)
        # same table body from both mounts; only the mount tag differs
        strip = lambda h: h.replace("diag", "M").replace("fdir", "M")
        assert strip(out["s12d"]) == strip(out["s12f"])
        assert strip(out["s19d"]) == strip(out["s19f"])
        assert "0x0101" in out["s12d"] and "OOL" in out["s12d"]
        assert "EPS_LOAD_SHED" in out["s19d"]


@requires_node
class TestDisplayConfigClient:
    def test_table_accessors_tolerate_missing_or_malformed_config(self, tmp_path):
        out = run_js("""
            T.S.displayConfig = {};
            const empty = { g: T.captureGateTable(), b: T.baselineTable() };
            T.S.displayConfig = { capture_gates: "nope", health_baselines: [1] };
            const bad = { g: T.captureGateTable(), b: T.baselineTable() };
            T.S.displayConfig = { capture_gates: [{ label: "x" }],
                                  health_baselines: { "0x0136": { value: 100 } } };
            const good = { g: T.captureGateTable(), b: T.baselineTable() };
            return { empty, bad, good };
        """, tmp_path)
        assert out["empty"] == {"g": [], "b": None}
        assert out["bad"] == {"g": [], "b": None}
        assert out["good"]["g"] == [{"label": "x"}]
        assert out["good"]["b"] == {"0x0136": {"value": 100}}


# ── static assertions ───────────────────────────────────────────────────────

def server_route_paths():
    src = SERVER.read_text()
    return re.findall(
        r'add_(?:get|post|put|delete|static)\(\s*"([^"]+)"', src)


def mcs_js_api_refs():
    src = MCS_JS.read_text()
    return sorted(set(re.findall(r'["`](/api/[a-z0-9\-_/{}$.]*)', src)))


class TestStaticContracts:
    def test_every_api_path_exists_in_route_table(self):
        routes = server_route_paths()
        plain = [r for r in routes if "{" not in r]
        prefixes = [r.split("{")[0] for r in routes if "{" in r]
        missing = []
        for ref in mcs_js_api_refs():
            base = ref.split("?")[0]
            # template literals: /api/alarms/${...}/ack etc. -> check prefix
            base = base.split("${")[0]
            ok = (base in plain
                  or any(base.startswith(p) and p for p in prefixes)
                  or any(r.startswith(base) for r in plain if base.endswith("/")))
            if not ok:
                missing.append(ref)
        assert not missing, f"mcs.js references unknown routes: {missing}"

    def test_never_references_sim_named_route_or_jump_field(self):
        src = MCS_JS.read_text() + INDEX.read_text()
        assert "/api/sim-state" not in src          # M12
        assert "sim_time_jump" not in src           # M10

    def test_date_now_only_in_time_section(self):
        src = MCS_JS.read_text()
        lines = src.splitlines()
        section2 = next(i for i, l in enumerate(lines) if "═══ 2. STATE" in l)
        offenders = [i + 1 for i, l in enumerate(lines)
                     if "Date.now()" in l and i >= section2]
        assert not offenders, (
            f"Date.now() outside the TIME section at lines {offenders}; "
            "use simNow()/simNowMs() (M3)")
        # and the primitive itself exists exactly once
        assert src.count("function _wallNowMs() { return Date.now(); }") == 1

    def test_classic_page_untouched(self):
        actual = hashlib.sha256(CLASSIC.read_bytes()).hexdigest()
        assert actual == CLASSIC_SHA, "classic index.html was modified"

    def test_every_schematic_data_param_resolves(self):
        params_yaml = REPO / "configs" / "eosat1" / "telemetry" / "parameters.yaml"
        data = yaml.safe_load(params_yaml.read_text())
        known = set()
        for p in data.get("parameters", []):
            pid = p["id"] if isinstance(p["id"], int) else int(str(p["id"]), 0)
            known.add(f"0x{pid:04X}")
        used = set(re.findall(r'data-param="(0x[0-9A-Fa-f]{4})"', SVG.read_text()))
        assert used, "schematics.svg carries no data-param bindings"
        unresolved = sorted(used - known)
        assert not unresolved, f"schematic params not in catalog: {unresolved}"

    def test_schematics_have_all_eight_symbols_and_nav(self):
        svg = SVG.read_text()
        for sym in ["sch-ovr", "sch-eps", "sch-tcs", "sch-aocs", "sch-ttc",
                    "sch-pld", "sch-prp", "sch-obd"]:
            assert f'<symbol id="{sym}"' in svg, f"missing {sym}"
        for nav in ["eps", "tcs", "aocs", "ttc", "pld", "prp", "obd"]:
            assert f'data-nav="{nav}"' in svg, f"L1 lacks data-nav={nav}"

    def test_display_config_yaml_matches_served_shape(self):
        data = yaml.safe_load(DISPLAY_CFG.read_text())
        assert isinstance(data, dict) and data, "display_config.yaml must be a mapping"
        gates = data["capture_gates"]
        assert isinstance(gates, list) and gates
        for g in gates:
            assert {"label", "param", "op"} <= set(g)
        baselines = data["health_baselines"]
        assert isinstance(baselines, dict) and baselines
        for hexid, b in baselines.items():
            assert re.fullmatch(r"0x[0-9A-Fa-f]{4}", hexid)
            assert "value" in b

    def test_training_layer_fully_retired(self):
        """The guided/annotation teaching plumbing is gone from the operator
        UI (owner ruling 2026-08-19): no toggle, no callouts, no doc link.
        The TRAINING time-source chip and the causal chain are NOT training
        content and stay."""
        src = INDEX.read_text() + MCS_JS.read_text()
        for needle in ["guided-btn", "data-ann", "data-guided",
                       "why-this-design", "annotationText",
                       "applyAnnotations", "/api/annotations"]:
            assert needle not in src, f"retired training plumbing remains: {needle}"
        assert not (MODERN / "why-this-design.html").exists()

    def test_shell_carries_required_chrome(self):
        html = INDEX.read_text()
        for needle in [
            'href="/static/shared/design-system.css',      # shared tokens
            'src="/static/modern/mcs.js',                  # one module
            'id="training-chip"',                          # M1 chip (hidden default)
            'id="pos-select"',                             # 7-position selector
            'id="alert-live"', 'role="alert"',             # live region
            'class="ds-skip"',                             # skip link
            'id="bottom-drawer"', 'id="alarm-drawer"',
            'id="contingency-banner"',
            'prefers-reduced-motion',
        ]:
            assert needle in html, f"index.html missing {needle}"
        # no raster chrome (B7), no external origins
        assert ".png" not in html and ".jpg" not in html
        assert "http://" not in html and "https://" not in html

    def test_rest_fallback_uses_api_state(self):
        src = MCS_JS.read_text()
        assert 'getJSON("/api/state")' in src
        # WS with position param + house 3 s reconnect via shared wsClient
        assert "/ws?position=" in src
        assert "wsClient(" in src


# ── POSITION_NEEDS gap surfaces (G1-G9, G12, G15) ───────────────────────────

def catalog_param(hex_id: str) -> dict:
    """The REAL parameters.yaml row for a hex id — bitmask-legend tests parse
    the mission's own descriptions so the decoder cannot drift from them."""
    params = yaml.safe_load(
        (REPO / "configs" / "eosat1" / "telemetry" / "parameters.yaml").read_text())
    want = int(hex_id, 16)
    for p in params["parameters"]:
        pid = p["id"] if isinstance(p["id"], int) else int(str(p["id"]), 0)
        if pid == want:
            return p
    raise KeyError(hex_id)


DERIVED_FIXTURE = """
    T.S.state = { params: {}, params_age: {}, derived: {
      "eps.soc_at_eclipse_exit_pred": { id: "0x0803", name: "eps.soc_at_eclipse_exit_pred",
        units: "%", value: 61.2, valid: true, reason: "", source: "ground" },
      "eps.time_to_soc_yellow": { id: "0x0804", name: "eps.time_to_soc_yellow",
        units: "s", value: null, valid: false,
        reason: "SoC not trending towards yellow", source: "ground" },
      "ttc.pass_fit_ratio": { id: "0x0809", name: "ttc.pass_fit_ratio",
        units: "ratio", value: 1.8, valid: true, reason: "", source: "ground" },
    }};
"""


@requires_node
class TestDerivedRows:
    """G1/G3: derived rows render the valid value with a ground-provenance
    mark, and the INVALID state with the server's reason — never a number."""

    def test_valid_row_value_and_provenance(self, tmp_path):
        out = run_js(DERIVED_FIXTURE + """
            return T.renderDerivedRow("eps.soc_at_eclipse_exit_pred");
        """, tmp_path)
        assert "61.2 %" in out
        assert "0x0803" in out
        assert ">GND<" in out                       # ground-derived mark
        assert "NOT downlinked" in out              # provenance tooltip

    def test_invalid_row_shows_reason_never_a_number(self, tmp_path):
        out = run_js(DERIVED_FIXTURE + """
            return T.renderDerivedRow("eps.time_to_soc_yellow");
        """, tmp_path)
        assert "INVALID" in out
        assert "SoC not trending towards yellow" in out
        assert ">GND<" in out
        # no fabricated value anywhere in the row
        assert not re.search(r"\d+\s*s\b", out)

    def test_fit_verdict_line_both_ways(self, tmp_path):
        out = run_js(DERIVED_FIXTURE + """
            const wont = T.fitVerdictLine();
            T.S.state.derived["ttc.pass_fit_ratio"].value = 0.6;
            const fits = T.fitVerdictLine();
            T.S.state.derived["ttc.pass_fit_ratio"] =
              { id: "0x0809", units: "ratio", value: null, valid: false,
                reason: "no pass in the next 2 h", source: "ground" };
            const invalid = T.fitVerdictLine();
            return { wont, fits, invalid };
        """, tmp_path)
        assert "PASS FIT 1.80" in out["wont"] and "will NOT fit" in out["wont"]
        assert "PASS FIT 0.60" in out["fits"] and "will NOT" not in out["fits"]
        assert "INVALID" in out["invalid"] and "no pass in the next 2 h" in out["invalid"]

    def test_sa_header_carries_the_two_decision_projections(self, tmp_path):
        out = run_js(DERIVED_FIXTURE + """
            T.S.state.derived["ttc.pass_fit_ratio"].value = 0.62;
            T.S.state.derived["eps.soc_at_eclipse_exit_pred"].value = 58.9;
            T.updateSaHeader();
            const valid = { next: __registry["sa-next-s"].textContent,
                            well: __registry["sa-well-s"].textContent };
            // invalid projections stay OFF the header (page rows carry the reason)
            T.S.state.derived["ttc.pass_fit_ratio"].valid = false;
            T.S.state.derived["eps.soc_at_eclipse_exit_pred"].valid = false;
            T.updateSaHeader();
            const invalid = { next: __registry["sa-next-s"].textContent,
                              well: __registry["sa-well-s"].textContent };
            return { valid, invalid };
        """, tmp_path)
        assert "fit 0.62" in out["valid"]["next"] and "(gnd)" in out["valid"]["next"]
        assert "SoC@ecl-exit 59 %" in out["valid"]["well"]
        assert "fit" not in out["invalid"]["next"]
        assert "SoC@ecl-exit" not in out["invalid"]["well"]

    def test_derived_card_renders_on_owning_page(self, tmp_path):
        out = run_js(DERIVED_FIXTURE + """
            T.renderDerivedCard("eps");
            return __registry["eps-derived"].innerHTML;
        """, tmp_path)
        assert "SoC at eclipse exit" in out       # valid row
        assert "INVALID" in out                    # invalid row, same card


@requires_node
class TestBitmaskDecoders:
    """G2: 0x02C5 / 0x0B15 decoded to labelled chips FROM the served
    parameter descriptions (the dictionary is the single source of truth)."""

    def _snippet(self, body):
        p1 = catalog_param("0x02C5")
        p2 = catalog_param("0x0B15")
        return (f"const FINE = {json.dumps(p1)};\n"
                f"const INHIB = {json.dumps(p2)};\n" + body)

    def test_legends_parse_from_real_descriptions(self, tmp_path):
        out = run_js(self._snippet("""
            return { fine: T.parseBitLegend(FINE.description),
                     inhib: T.parseBitLegend(INHIB.description) };
        """), tmp_path)
        assert [b["bit"] for b in out["fine"]] == list(range(7))
        assert [b["bit"] for b in out["inhib"]] == list(range(11))
        assert out["fine"][4]["label"].startswith("momentum")
        assert out["inhib"][0]["label"] == "catbed"
        # long teaching parenthetical stripped from the chip label
        assert "pyramid" not in out["fine"][1]["label"]

    def test_chips_decode_a_known_mask(self, tmp_path):
        # 0b0010010: bit1 (wheels) + bit4 (momentum) failing, rest clear
        out = run_js(self._snippet("""
            T.S.paramByHex["0x02C5"] = { hex: "0x02C5", name: "aocs.fine_ready",
                                         description: FINE.description };
            T.S.state = { params: { "0x02C5": 0x12 }, params_age: { "709": 1.0 } };
            return T.renderBitmaskChips("0x02C5", { clearLabel: "READY FOR FINE" });
        """), tmp_path)
        assert "aocs.fine_ready = 0x0012" in out
        assert "2 BLOCKING" in out
        # the failing bits are alarm chips, a clear bit is an ok chip
        assert re.search(r'ds-chip alarm[^>]*bit 1 SET', out.replace("\n", " ")) or \
               ('bit 1 SET' in out and 'ds-chip alarm' in out)
        assert 'bit 0 clear' in out

    def test_all_clear_mask_shows_the_ready_verdict(self, tmp_path):
        out = run_js(self._snippet("""
            T.S.paramByHex["0x0B15"] = { hex: "0x0B15", name: "prop.arm_inhibit_mask",
                                         description: INHIB.description };
            T.S.state = { params: { "0x0B15": 0 }, params_age: { "2837": 1.0 } };
            return T.renderBitmaskChips("0x0B15", { clearLabel: "ARM PERMITTED" });
        """), tmp_path)
        assert "ARM PERMITTED" in out
        assert "BLOCKING" not in out

    def test_stale_tm_withholds_the_chips(self, tmp_path):
        out = run_js(self._snippet("""
            T.S.paramByHex["0x02C5"] = { hex: "0x02C5", description: FINE.description };
            T.S.state = { params: {}, params_age: {} };
            return T.renderBitmaskChips("0x02C5");
        """), tmp_path)
        assert "withheld" in out and "ds-chip alarm" not in out


CAPTURE_FIXTURE = """
    T.S.displayConfig = { capture_gates: [
      { label: "AOCS mode = FINE_POINT", param: "0x020F", op: "eq", value: 5 },
      { label: "fine_ready clear", param: "0x02C5", op: "eq", value: 0 },
      { label: "FPA ready", param: "0x0610", op: "eq", value: 1 },
      { label: "SoC > 50", param: "0x0101", op: "gt", value: 50 },
      { label: "FPA temp band", param: "0x0408", op: "range", min: -40, max: -10 },
    ]};
    T.S.state = { params: { "0x020F": 5, "0x02C5": 0, "0x0610": 1,
                            "0x0101": 62, "0x0408": -15 },
                  params_age: { "527": 1, "709": 1, "1552": 1, "257": 1, "1032": 1 } };
"""


@requires_node
class TestCaptureReadiness:
    """G4: the NOM-002 gate composite, driven by the display_config.yaml table."""

    def test_all_gates_met_is_go(self, tmp_path):
        out = run_js(CAPTURE_FIXTURE + """
            const c = T.computeCaptureReadiness();
            return { composite: c.composite,
                     states: c.rows.map(r => r.state) };
        """, tmp_path)
        assert out["composite"] == "GO"
        assert out["states"] == ["pass"] * 5

    def test_one_failing_gate_is_nogo(self, tmp_path):
        out = run_js(CAPTURE_FIXTURE + """
            T.S.state.params["0x0101"] = 45;      // SoC below the gate
            return T.computeCaptureReadiness().composite;
        """, tmp_path)
        assert out == "NO-GO"

    def test_missing_telemetry_is_unknown_not_go(self, tmp_path):
        out = run_js(CAPTURE_FIXTURE + """
            delete T.S.state.params["0x0408"];    // FPA temp gone
            return T.computeCaptureReadiness().composite;
        """, tmp_path)
        assert out == "UNKNOWN"

    def test_no_gate_table_is_designed_absence(self, tmp_path):
        out = run_js("""
            T.S.displayConfig = {};
            T.S.state = { params: {}, params_age: {} };
            return T.computeCaptureReadiness();
        """, tmp_path)
        assert out == {"available": False}

    def test_real_display_config_yaml_carries_the_gate_table(self):
        data = yaml.safe_load(DISPLAY_CFG.read_text())
        gates = data["capture_gates"]
        assert len(gates) == 5
        params = {g["param"] for g in gates}
        assert params == {"0x020F", "0x02C5", "0x0610", "0x0101", "0x0408"}


ALARM_CASCADE_FIXTURE = """
    const graph = { available: true,
      edge_semantics: "edges are DERIVED fault-to-fault ancestry",
      nodes: [ { id: "eps_undervoltage", param_id: "0x0105" },
               { id: "aocs_att", param_id: "0x0217" },
               { id: "pld_fpa", param_id: "0x0601" },
               { id: "tcs_temp", param_id: "0x0408" },
               { id: "aocs_rw", param_id: "0x0207" } ],
      edges: [ { parent: "eps_undervoltage", child: "aocs_att" },
               { parent: "eps_undervoltage", child: "pld_fpa" },
               { parent: "eps_undervoltage", child: "tcs_temp" },
               { parent: "eps_undervoltage", child: "aocs_rw" } ] };
    const alarms = [
      { id: "r", param_id: 0x0105, subsystem: "eps", severity: 3, message: "bus undervoltage" },
      { id: "a", param_id: 0x0217, subsystem: "aocs", severity: 2, message: "att err" },
      { id: "b", param_id: 0x0601, subsystem: "payload", severity: 2, message: "fpa warm" },
      { id: "c", param_id: 0x0408, subsystem: "tcs", severity: 2, message: "fpa temp" },
      { id: "d", param_id: 0x0207, subsystem: "aocs", severity: 2, message: "rw stop" },
    ];
"""


@requires_node
class TestFirstOutAlarmGrouping:
    """G6: >=4 active alarms sharing fault-graph ancestry collapse under the
    first-out root; the inference is labelled per edge_semantics."""

    def test_cascade_collapses_to_one_root(self, tmp_path):
        out = run_js(ALARM_CASCADE_FIXTURE + """
            const g = T.groupAlarmsByAncestry(alarms, graph);
            return { n: g.groups.length, root: g.groups[0].root.id,
                     cons: g.groups[0].consequents.map(a => a.id).sort(),
                     rest: g.rest.length };
        """, tmp_path)
        assert out["n"] == 1
        assert out["root"] == "r"
        assert out["cons"] == ["a", "b", "c", "d"]
        assert out["rest"] == 0

    def test_below_threshold_stays_flat(self, tmp_path):
        out = run_js(ALARM_CASCADE_FIXTURE + """
            const few = alarms.slice(0, 3);        // root + 2 consequents < 4
            const g = T.groupAlarmsByAncestry(few, graph);
            return { n: g.groups.length, rest: g.rest.length };
        """, tmp_path)
        assert out == {"n": 0, "rest": 3}

    def test_no_graph_degrades_to_flat_list(self, tmp_path):
        out = run_js(ALARM_CASCADE_FIXTURE + """
            const g = T.groupAlarmsByAncestry(alarms, null);
            return { n: g.groups.length, rest: g.rest.length };
        """, tmp_path)
        assert out == {"n": 0, "rest": 5}

    def test_drawer_renders_the_collapsed_group_with_inference_label(self, tmp_path):
        out = run_js(ALARM_CASCADE_FIXTURE + """
            T.S.faultGraph = graph;
            T.S.alarms = alarms;
            T.renderAlarmDrawer();
            const collapsed = __registry["alarm-list"].innerHTML;
            T.S.alarmGroupsOpen["r"] = true;
            T.renderAlarmDrawer();
            const expanded = __registry["alarm-list"].innerHTML;
            return { collapsed, expanded };
        """, tmp_path)
        assert "FIRST-OUT ROOT" in out["collapsed"]
        assert "+ 4 consequent" in out["collapsed"]
        assert "INFERRED" in out["collapsed"]        # edge-semantics label
        assert 'data-group="r"' in out["collapsed"]
        # collapsed: consequents hidden; expanded: they render
        assert "rw stop" not in out["collapsed"]
        assert "rw stop" in out["expanded"]


@requires_node
class TestBlindTcMode:
    """G12: armed enable, BLIND tagging in the history, TM-readback clear."""

    def test_enable_requires_two_presses(self, tmp_path):
        out = run_js("""
            T.toggleBlindTc();
            const armed = T.blindTcState();
            T.toggleBlindTc();
            const enabled = T.blindTcState();
            return { armed, enabled,
                     posts: __fetchLog.filter(f => f.method === "POST").map(f => f.url) };
        """, tmp_path)
        assert out["armed"] == {"enabled": False, "armPending": True}
        assert out["enabled"]["enabled"] is True
        # enabling is logged to the console log (G8 transport)
        assert "/api/console-log" in out["posts"]

    def test_history_tagged_blind_until_tm_readback(self, tmp_path):
        out = run_js("""
            T.toggleBlindTc(); T.toggleBlindTc();       // armed enable
            T.noteHistory({ ts: "12:00:00", name: "TTC_TX_ON", data: "", verdict: "sent" });
            const pending = T.S.cmdHistory[0].blind;
            const htmlPending = __registry["cmd-history"].innerHTML;
            T.noteVerification({ subtype: 7, tc_service: 8, tc_subtype: 1 });
            const confirmed = T.S.cmdHistory[0].blind;
            const htmlConfirmed = __registry["cmd-history"].innerHTML;
            return { pending, confirmed, htmlPending, htmlConfirmed };
        """, tmp_path)
        assert out["pending"] == "pending"
        assert out["confirmed"] == "confirmed"
        assert "blind-chip" in out["htmlPending"] and ">BLIND<" in out["htmlPending"]
        assert "BLIND ✓ TM" in out["htmlConfirmed"]

    def test_normal_mode_leaves_history_untagged(self, tmp_path):
        out = run_js("""
            T.noteHistory({ ts: "12:00:00", name: "EPS_POWER_ON", data: "", verdict: "sent" });
            return { blind: T.S.cmdHistory[0].blind ?? null,
                     html: __registry["cmd-history"].innerHTML };
        """, tmp_path)
        assert out["blind"] is None
        assert "BLIND" not in out["html"]


@requires_node
class TestBaselineHealth:
    """G15: baseline-vs-now deltas from the display_config.yaml commissioning
    snapshot, drift computed client-side, >5 % flagged (NOM-011)."""

    FIXTURE = """
        T.S.displayConfig = { health_baselines: {
          "0x0136": { label: "Battery health", value: 100, units: "%" },
          "0x0124": { label: "SA-A degradation", value: 1.0, units: "" },
          "0x0B16": { label: "Leak rate", value: 0, units: "mg/s", abs_flag: 0.05 },
        }};
        T.S.state = { params: { "0x0136": 93.0, "0x0124": 0.97, "0x0B16": 0.0 },
                      params_age: { "310": 1, "292": 1, "2838": 1 } };
    """

    def test_drift_computed_and_flagged_over_5pct(self, tmp_path):
        out = run_js(self.FIXTURE + """
            const c = T.computeBaselineDeltas();
            return c.rows.map(r => ({ id: r.hexId, drift: r.drift, flag: r.flag }));
        """, tmp_path)
        by = {r["id"]: r for r in out}
        assert abs(by["0x0136"]["drift"] + 7.0) < 1e-6 and by["0x0136"]["flag"] is True
        assert abs(by["0x0124"]["drift"] + 3.0) < 1e-6 and by["0x0124"]["flag"] is False
        # zero baseline: no percent drift, absolute-threshold flagging
        assert by["0x0B16"]["drift"] is None and by["0x0B16"]["flag"] is False

    def test_zero_baseline_flags_on_absolute_delta(self, tmp_path):
        out = run_js(self.FIXTURE + """
            T.S.state.params["0x0B16"] = 0.2;      // leaking
            const r = T.computeBaselineDeltas().rows.find(x => x.hexId === "0x0B16");
            return { flag: r.flag, delta: r.delta };
        """, tmp_path)
        assert out["flag"] is True and abs(out["delta"] - 0.2) < 1e-9

    def test_missing_tm_is_no_tm_not_zero_drift(self, tmp_path):
        out = run_js(self.FIXTURE + """
            delete T.S.state.params["0x0124"];
            const r = T.computeBaselineDeltas().rows.find(x => x.hexId === "0x0124");
            T.renderBaselineCard();
            return { state: r.state, html: __registry["trnd-baseline"].innerHTML };
        """, tmp_path)
        assert out["state"] == "no-tm"
        assert "NO TM" in out["html"]

    def test_no_baseline_config_is_designed_absence(self, tmp_path):
        out = run_js("""
            T.S.displayConfig = {};
            T.renderBaselineCard();
            return __registry["trnd-baseline"].innerHTML;
        """, tmp_path)
        assert "no commissioning baseline" in out

    def test_real_display_config_yaml_carries_the_baselines(self):
        data = yaml.safe_load(DISPLAY_CFG.read_text())
        b = data["health_baselines"]
        assert set(b) == {"0x0120", "0x0121", "0x0136",
                          "0x0124", "0x0125", "0x0B16"}
        assert b["0x0B16"]["abs_flag"] > 0        # zero-baseline threshold


@requires_node
class TestGonogoMatrixContext:
    """G7: an open poll renders its call-matrix row beside the buttons."""

    FIXTURE = """
        T.S.gonogoMatrix = { available: true, matrix: [
          { situation: "OBC switchover", who_calls: "fdir_systems",
            who_responds: ["ALL"], fd_decision: "FD authorises (CRITICAL)",
            critical: true },
          { situation: "Blind command send", who_calls: "ttc",
            who_responds: ["flight_director"],
            fd_decision: "FD authorises each blind TC", critical: false } ] };
    """

    def test_match_and_render(self, tmp_path):
        out = run_js(self.FIXTURE + """
            const m = T.matchGonogoRow("OBC SWITCHOVER GO/NO-GO");
            T.S.goNoGo = { type: "go_nogo_poll", label: "OBC switchover",
                           active: true, responses: { ttc: "GO" } };
            T.renderGoNoGo();
            return { m: m && m.situation,
                     html: __registry["gonogo-status"].innerHTML };
        """, tmp_path)
        assert out["m"] == "OBC switchover"
        assert "must respond" in out["html"]
        assert "FDIR / SYSTEMS" in out["html"]      # who_calls, titled
        assert "CRITICAL" in out["html"]
        assert "gonogo_matrix.yaml" in out["html"]

    def test_unmatched_label_says_freeform(self, tmp_path):
        out = run_js(self.FIXTURE + """
            T.S.goNoGo = { type: "go_nogo_poll", label: "zzz nothing",
                           active: true, responses: {} };
            T.renderGoNoGo();
            return __registry["gonogo-status"].innerHTML;
        """, tmp_path)
        assert "no matching call-matrix row" in out


@requires_node
class TestConsoleLogSurface:
    """G8: the LOG drawer tab — merged stream render, kind filter, AUTO mark."""

    FIXTURE = """
        T.S.consoleLog = [
          { id: 1, utc_epoch: 1755432000, kind: "tc", position: "aocs",
            text: "TC(8,1) AOCS_SET_MODE sent", source: "server" },
          { id: 2, utc_epoch: 1755432060, kind: "mark", position: "eps_tcs",
            text: "MARK — eclipse entry", source: "operator" } ];
    """

    def test_renders_merged_stream_with_auto_badge(self, tmp_path):
        out = run_js(self.FIXTURE + """
            T.renderConsoleLog();
            return __registry["conlog-list"].innerHTML;
        """, tmp_path)
        assert "AOCS_SET_MODE" in out and "[TC]" in out and "[aocs]" in out
        assert "AUTO" in out                       # server entries labelled
        assert "MARK — eclipse entry" in out

    def test_kind_filter(self, tmp_path):
        out = run_js(self.FIXTURE + """
            T.S.conlogKind = "mark";
            T.renderConsoleLog();
            return __registry["conlog-list"].innerHTML;
        """, tmp_path)
        assert "MARK — eclipse entry" in out
        assert "AOCS_SET_MODE" not in out

    def test_shell_carries_the_log_surface(self):
        html = INDEX.read_text()
        assert 'data-bd="conlog"' in html          # drawer tab
        assert 'id="conlog-input"' in html         # free-text note box
        assert 'data-conlog="mark"' in html        # MARK button
        assert 'data-conlog="note"' in html
        assert 'id="conlog-kind"' in html          # kind filter


@requires_node
class TestBurnPlanCard:
    """G9: loaded-vs-planned burn comparison helpers."""

    def test_mismatch_thresholds(self, tmp_path):
        out = run_js("""
            return {
              match: T.burnMismatch(2.00, 2.02),
              mismatch: T.burnMismatch(2.0, 3.5),
              noPlan: T.burnMismatch(2.0, NaN),
              dv: T.plannedBurnDv({ dv_mps: 1.5 }),
              dvAlt: T.plannedBurnDv({ delta_v: 0.8 }),
            };
        """, tmp_path)
        assert out["match"] is False
        assert out["mismatch"] is True
        assert out["noPlan"] is None               # not comparable, not false
        assert out["dv"] == 1.5 and out["dvAlt"] == 0.8

    def test_card_states(self, tmp_path):
        out = run_js("""
            T.S.paramByHex["0x0B0E"] = { hex: "0x0B0E" };
            T.S.state = { params: { "0x0B0E": 2.0, "0x0B10": 45 },
                          params_age: { "2830": 1, "2832": 1 } };
            T.S.burnPlan = { available: true, count: 1, activity: {
              id: 7, name: "orbit_manoeuvre", start_time: "2026-08-18T12:00:00Z",
              state_name: "PLANNED", procedure_ref: "NOM-004", dv_mps: 3.5 } };
            T.renderBurnCard();
            const mismatch = __registry["prp-burnplan"].innerHTML;
            T.S.burnPlan.activity.dv_mps = 2.0;
            T.renderBurnCard();
            const match = __registry["prp-burnplan"].innerHTML;
            T.S.burnPlan = { available: false, reason: "planner unreachable (offline or cross-origin) — confirm the burn plan by voice with FDS" };
            T.renderBurnCard();
            const absent = __registry["prp-burnplan"].innerHTML;
            return { mismatch, match, absent };
        """, tmp_path)
        assert "MISMATCH" in out["mismatch"] and "Do NOT arm" in out["mismatch"]
        assert "LOADED = PLANNED" in out["match"]
        assert "planner unreachable" in out["absent"]   # designed absence
        assert "MISMATCH" not in out["absent"]


@requires_node
class TestLinkPredictionOverlay:
    """G5: predicted-vs-measured now readout + graceful degradation."""

    def test_now_readout_margin(self, tmp_path):
        out = run_js("""
            T.S.state = { params: { "0x0539": 1, "0x0519": 6.2 },
                          params_age: { "1337": 1, "1305": 1 } };
            T.S.linkPred = { available: true,
              pass: { gs_name: "Iqaluit", which: "current" },
              tiers: [ { name: "BEACON" }, { name: "LOW" }, { name: "HIGH" } ],
              series: { eb_n0_db: { LOW: [[1, 12.0]] } },
              now: { tiers: { LOW: { eb_n0_db: 12.4 } } } };
            const r = T.linkNowReadout();
            T.renderLinkOverlay();
            return { r, html: __registry["ttc-linkpred-now"].innerHTML };
        """, tmp_path)
        assert out["r"]["tierName"] == "LOW"
        assert abs(out["r"]["delta"] + 6.2) < 1e-6      # 6 dB below prediction
        assert "predicted-now 12.4 dB" in out["html"]
        assert "measured-now 6.2 dB" in out["html"]
        assert "Δ -6.2 dB" in out["html"]

    def test_unavailable_is_designed_absence(self, tmp_path):
        out = run_js("""
            T.S.state = { params: {}, params_age: {} };
            T.S.linkPred = { available: false, reason: "no pass in the next 2 h" };
            T.renderLinkOverlay();
            return __registry["ttc-linkpred-now"].innerHTML;
        """, tmp_path)
        assert "no pass in the next 2 h" in out
        assert "predicted-now" not in out
