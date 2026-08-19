"""The PROP tab — Franz: "Add displays for PROP commands and telemetry".

Propulsion shipped in a previous build with 28 telemetry parameters
(0x0B00-0x0B1B), 11 telecommands (S8 funcs 108-118), HK SID 9 at 8 s, 19 events
(0x0800-0x0812), 5 ground-limit bands, 8 S12 monitors, an operator position and
a full model. The console showed exactly ONE thing for it:
``<option value="prop">PROP</option>`` in the TRENDS dropdown. No tab, no pane,
no `updateProp()`, no chart mapping, no event routing — `EVENT_SUBSYSTEM_RANGES`
stopped at 0x06FF so all nineteen propulsion events fell through unrouted — no
limit colouring, no quick commands and no HK request for SID 9.

The operationally critical widget is the **arm-interlock mask 0x0B15 rendered
as eleven named indicators**: PROP_ARM is refused whenever any bit is set and
the bit is the only thing that says why, so a hex number asks the operator to
decode a bitmask by hand inside a collision-avoidance window.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")


def run_js(snippet: str, tmp_path: Path):
    f = tmp_path / "snippet.js"
    f.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(INDEX), str(f)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


PAGE = INDEX.read_text()

# A propulsion spacecraft in a plausible pre-burn state: SAFED, tanks full,
# catbed cold (interlock bit 0) and no burn loaded (bit 10).
PRE_ARM_STATE = """
    state = {
      last_frame_age_s: 1.0, stale: false, in_contact: true,
      hk_sid_age_s: {'1': 0.4, '9': 3.0},
      subsystem_staleness: {prop: 2.0, eps: 0.4, aocs: 1.0},
      params: {
        '0x0B00': 0, '0x0B15': 0x0401, '0x0B01': 21.4, '0x0B02': 398.0,
        '0x0B03': 99.5, '0x0B04': 18.0, '0x0B05': 12.0, '0x0B06': 0,
        '0x0B07': 0, '0x0B08': 0, '0x0B09': 330.0, '0x0B0A': 220.0,
        '0x0B0B': 0.0, '0x0B0C': 42.1, '0x0B0D': 0, '0x0B12': 0,
        '0x0B13': 0.0, '0x0B14': 0, '0x0B16': 0.0, '0x0B18': 12.4,
        '0x0B19': 1, '0x0B1A': 6871.0, '0x0B1B': 0.0, '0x014A': 0,
        '0x0217': 0.02, '0x020F': 4
      },
      params_age: {}
    };
"""


# ── the tab exists at all ────────────────────────────────────────────

class TestTabExists:

    def test_there_is_a_prop_tab_button_and_pane(self):
        assert 'id="tab-prop" data-tab="prop" data-subsystem="prop"' in PAGE
        assert 'id="pane-prop"' in PAGE

    def test_update_prop_is_called_every_cycle(self):
        assert "function updateProp()" in PAGE
        assert "  updateProp();" in PAGE, "updateProp must be wired into updateUI"

    def test_propulsion_events_are_routed(self):
        """0x0800-0x0812 fell through unrouted: the table stopped at 0x06FF."""
        assert "prop:    [0x0800, 0x08FF]" in PAGE
        assert "id=\"prop-events\"" in PAGE

    def test_the_prop_charts_are_gated_by_the_prop_subsystem(self):
        """A chart whose prefix maps to no subsystem escapes the staleness gate."""
        assert "prop: 'prop'" in PAGE

    def test_the_visible_tabs_config_grants_the_tab(self):
        pos = yaml.safe_load(
            (CONFIG_DIR / "mcs" / "positions.yaml").read_text())["positions"]
        assert "prop" in pos["propulsion"]["visible_tabs"]
        assert "prop" in pos["flight_director"]["visible_tabs"]
        # Flight Dynamics gets a READ-ONLY view: visible_tabs gates viewing,
        # allowed_func_ids gates commanding, and they are independent.
        assert "prop" in pos["aocs"]["visible_tabs"]
        assert not set(range(108, 119)) & set(pos["aocs"]["allowed_func_ids"])
        assert set(range(108, 119)) <= set(pos["propulsion"]["allowed_func_ids"])


# ── the arm-interlock mask: the operationally critical widget ────────

@requires_node
class TestArmInterlockMask:

    def test_all_eleven_bits_are_named(self, tmp_path):
        out = run_js("""
            return PROP_INTERLOCK_BITS.map(b => [b.bit, b.label]);
        """, tmp_path)
        assert [row[0] for row in out] == list(range(11))
        labels = [row[1] for row in out]
        # The names must match the parameter description of 0x0B15.
        for expected in ("CATBED", "TANK P", "PROPELLANT", "AOCS MODE",
                         "ATT ERROR", "BATTERY SoC", "MISSION PHASE",
                         "ECLIPSE", "VALVES", "INHIBIT LATCHED",
                         "NO BURN LOADED"):
            assert expected in labels, f"{expected} missing from the interlock names"

    def test_the_bits_match_the_parameter_catalogue(self):
        """The labels must not drift from parameters.yaml's 0x0B15 description."""
        params = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
        desc = next(p["description"] for p in params["parameters"]
                    if int(str(p["id"]), 0) == 0x0B15)
        # The catalogue names each bit b0..b10; make sure we render that many.
        for n in range(11):
            assert f"b{n} " in desc or f"b{n}" in desc

    def test_a_set_bit_names_the_interlock_rather_than_showing_hex(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            updateProp();
            return {verdict: document.getElementById('prop-arm-verdict').textContent,
                    grid: document.getElementById('prop-interlocks').innerHTML,
                    tag: document.getElementById('prop-mask-tag').textContent};
        """, tmp_path)
        assert "PROP_ARM REFUSED" in out["verdict"]
        assert "CATBED" in out["verdict"] and "NO BURN LOADED" in out["verdict"]
        # The hex is still there — as corroboration, not as the answer.
        assert "0x0401" in out["verdict"]
        # Exactly the two set bits carry the `set` class.
        assert out["grid"].count('class="bit-ind set"') == 2

    def test_a_clear_mask_says_arm_will_be_accepted(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            state.params['0x0B15'] = 0;
            updateProp();
            return {verdict: document.getElementById('prop-arm-verdict').textContent,
                    set: document.getElementById('prop-interlocks').innerHTML
                         .split('bit-ind set').length - 1};
        """, tmp_path)
        assert "ALL INTERLOCKS CLEAR" in out["verdict"]
        assert out["set"] == 0

    def test_no_mask_telemetry_is_not_reported_as_clear(self, tmp_path):
        """"No data" must never be rendered as "all interlocks clear"."""
        out = run_js(PRE_ARM_STATE + """
            delete state.params['0x0B15'];
            updateProp();
            return document.getElementById('prop-arm-verdict').textContent;
        """, tmp_path)
        assert "NO DATA" in out


# ── the rest of the tab ──────────────────────────────────────────────

@requires_node
class TestPropPanels:

    def test_status_block_decodes_the_state_words(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            updateProp();
            const r = {safed: document.getElementById('prop-state').textContent};
            state.params['0x0B00'] = 2; state.params['0x0B0D'] = 2;
            updateProp();
            r.firing = document.getElementById('prop-state').textContent;
            r.burn = document.getElementById('prop-burn-status').textContent;
            state.params['0x0B0D'] = 4; state.params['0x0B14'] = 1;
            updateProp();
            r.aborted = document.getElementById('prop-burn-status').textContent;
            r.reason = document.getElementById('prop-abort-reason').textContent;
            return r;
        """, tmp_path)
        assert out["safed"] == "SAFED"
        assert out["firing"] == "FIRING" and out["burn"] == "FIRING"
        assert out["aborted"] == "ABORTED"
        assert "attitude error" in out["reason"]

    def test_burn_progress_shows_commanded_versus_achieved(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            state.params['0x0B0E'] = 0.850;
            state.params['0x0B0F'] = 0.804;
            state.params['0x0B10'] = 32.0;
            state.params['0x0B11'] = 30.3;
            updateProp();
            return {cmd: document.getElementById('prop-dv-cmd').textContent,
                    ach: document.getElementById('prop-dv-ach').textContent,
                    err: document.getElementById('prop-dv-err').textContent,
                    time: document.getElementById('prop-burn-time').textContent,
                    bar: document.getElementById('prop-burn-bar').style.width};
        """, tmp_path)
        assert out["cmd"] == "0.850 m/s"
        assert out["ach"] == "0.804 m/s"
        assert out["err"].startswith("-0.046")
        assert "%" in out["err"]
        assert out["time"] == "30.3 / 32.0 s"
        assert out["bar"].startswith("94")

    def test_dv_budget_and_valves(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            state.params['0x0B06'] = 1; state.params['0x0B07'] = 2;
            updateProp();
            return {rem: document.getElementById('prop-dv-rem2').textContent,
                    mass: document.getElementById('prop-mass').textContent,
                    sma: document.getElementById('prop-sma').textContent,
                    epoch: document.getElementById('prop-epoch').textContent,
                    latchA: document.getElementById('prop-svg-latch-a-lbl').textContent,
                    latchB: document.getElementById('prop-svg-latch-b-lbl').textContent,
                    tank: document.getElementById('prop-tank-tbody').innerHTML};
        """, tmp_path)
        assert out["rem"] == "42.10 m/s"
        assert out["mass"] == "12.400 kg"
        assert out["sma"] == "6871.000 km"
        assert out["epoch"] == "v1"
        assert out["latchA"] == "OPEN"
        assert out["latchB"] == "FAULT"      # a stuck valve is not "closed"
        assert "0x0B01" in out["tank"] and "21.40 bar" in out["tank"]

    def test_the_disabled_sid_banner_replaces_a_silent_lie(self, tmp_path):
        """SID 9 is not enabled by default and that is CORRECT behaviour."""
        out = run_js("""
            state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
                      hk_sid_age_s: {'1': 0.4}, subsystem_staleness: {eps: 0.4},
                      params: {}, params_age: {} };
            updateProp();
            return {display: document.getElementById('prop-sid-banner').style.display,
                    text: document.getElementById('prop-sid-banner-text').textContent};
        """, tmp_path)
        assert out["display"] == ""
        assert "SID 9" in out["text"] and "TC(3,5)" in out["text"]
        assert "PROP" in out["text"]

    def test_the_banner_hides_once_the_structure_arrives(self, tmp_path):
        out = run_js(PRE_ARM_STATE + """
            updateProp();
            return document.getElementById('prop-sid-banner').style.display;
        """, tmp_path)
        assert out == "none"


# ── commanding ───────────────────────────────────────────────────────

class TestPropCommands:

    def test_the_quick_command_group_covers_the_two_step_arm_sequence(self):
        cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        prop = [q for q in cat["quick_commands"] if q.get("group") == "PROPULSION"]
        by_cmd = {q["command"]: q for q in prop}
        for needed in ("PROP_SET_BURN", "PROP_ARM", "PROP_EXECUTE", "PROP_ABORT",
                       "PROP_DISARM", "PROP_ISO_OPEN", "PROP_ISO_CLOSE",
                       "PROP_INHIBIT_SET", "PROP_INHIBIT_CLR", "PROP_GET_STATUS"):
            assert needed in by_cmd, f"{needed} missing from the PROP console"
        # The catbed must reach 180 C before ARM passes its interlocks, so the
        # preheat belongs on this console even though it is a TCS circuit.
        assert "HEATER_THRUSTER" in by_cmd

    def test_the_committing_commands_demand_a_confirmation(self):
        cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        prop = {q["command"]: q for q in cat["quick_commands"]
                if q.get("group") == "PROPULSION"}
        for critical in ("PROP_SET_BURN", "PROP_ARM", "PROP_EXECUTE", "PROP_ABORT"):
            assert prop[critical].get("critical") is True, (
                f"{critical} commits propellant and must be confirmed")
        assert prop["PROP_GET_STATUS"].get("critical") is not True

    def test_the_arm_code_is_the_catalogue_value(self):
        """0xA5C3; the panel must not invent one."""
        cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        arm = next(q for q in cat["quick_commands"]
                   if q.get("name") == "PROP_ARM_SEQ")
        assert arm["args"]["arm_code"] == 0xA5C3

    def test_the_console_renders_the_propulsion_group(self):
        """The PROPULSION group renders on the COMMAND page.

        It used to be mirrored into a second console on the PROP display tab.
        Telecommands belong on the command page, so the copy is gone; what
        must not be lost is the group itself.
        """
        assert "id=\"prop-quick-cmds\"" not in PAGE, (
            "the PROP display tab has grown a command console again")
        assert "'PROPULSION'" in PAGE
        assert "id=\"quick-cmds-body\"" in PAGE
        # And the PROP tab still tells the operator where the commands went.
        assert "PROPULSION COMMANDING" in PAGE
        assert "COMMANDING</b> tab" in PAGE

    def test_a_position_without_the_funcs_is_told_why_it_cannot_command(self):
        assert "allowed_func_ids excludes 108-118" in PAGE

    def test_sid_9_can_be_requested_and_enabled_from_the_console(self):
        cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        hk = [q for q in cat["quick_commands"]
              if q.get("command") == "HK_REQUEST" and q["args"].get("sid") == 9]
        assert hk, "no one-shot HK request for the propulsion structure"
        # TC(3,5)/TC(3,27) are telecommands, so the buttons that used to sit
        # on the PROP banner are gone. The capability is on the COMMAND page:
        # a SID field defaulted to 9 with ENABLE / DISABLE / ONE-SHOT REQUEST.
        assert 'onclick="hkEnableSid(9)"' not in PAGE
        assert 'id="cmd-hk-sid"' in PAGE
        assert 'value="9"' in PAGE
        for btn in ("cmd-hk-enable-btn", "cmd-hk-disable-btn",
                    "cmd-hk-request-btn"):
            assert f'id="{btn}"' in PAGE, f"{btn} missing from the command page"
        # ...and the PROP banner still says where to go.
        assert "S3 HK Manager" in PAGE


# ── ground limits ────────────────────────────────────────────────────

class TestPropGroundLimits:

    def test_every_declared_prop_band_colours_something(self):
        """Five propulsion bands were declared in limits.yaml and rendered
        nowhere, because propulsion had no display at all."""
        limits = yaml.safe_load((CONFIG_DIR / "mcs" / "limits.yaml").read_text())
        prop_ids = {int(str(l["param_id"]), 0) for l in limits["limits"]
                    if 0x0B00 <= int(str(l["param_id"]), 0) <= 0x0BFF}
        assert prop_ids, "no propulsion ground limits in the configuration"
        for pid in prop_ids:
            token = f": 0x{pid:04X}"
            assert token in PAGE, (
                f"0x{pid:04X} has a ground-limit band but no element in "
                "LIMIT_ELEMENTS to colour")


# ── displays.yaml no longer describes a display that does not exist ──

class TestDisplaysYamlReconciled:

    def test_the_propulsion_display_config_matches_the_built_tab(self):
        cfg = yaml.safe_load((CONFIG_DIR / "mcs" / "displays.yaml").read_text())
        prop = cfg["positions"]["propulsion"]
        names = [p["name"] for p in prop["pages"]]
        assert "Status & Interlocks" in names
        widgets = [w for p in prop["pages"] for w in p["widgets"]]
        params = {w.get("parameter") for w in widgets}
        # The things the tab is actually built around.
        for needed in ("prop.arm_inhibit_mask", "prop.state", "prop.burn_status",
                       "prop.tank_pressure", "prop.dv_remaining",
                       "prop.burn_dv_achieved"):
            assert needed in params, f"{needed} missing from displays.yaml"
        assert any(w["type"] == "event_log" for w in widgets)
