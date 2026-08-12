"""The four model owners' display requirements (Phase 6, issue 4).

Each test names the requirement it covers and the failure it prevents. The
headline items:

* **AOCS D-1** — commanded mode vs actual mode. In the instrumented run the
  spacecraft silently abandoned NOMINAL for six orbits and NOTHING on any
  display said so: the mode field read "COARSE_SUN" and looked healthy.
* **AOCS D-4** — the momentum-envelope percentage. The console showed
  |h_wheel|, which reads ~0 even with every wheel saturated, so an operator saw
  "momentum = 0" while the wheels saturated.
* **TT&C §2.1** — the five-state DOWNLINK row. The TT&C page answered "the
  link is fine", which was true of the link and false of the spacecraft.
* **PAYLOAD D-1** — the FPA-readiness block: the display half of Franz's
  "FPA does not get to YES".
* **EPS R-1/R-3** — "charging" at +50 W on an 88 W spacecraft, and a wing
  status that could sit STOWED at 18.6 W generation with nothing saying why.
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
PAGE = INDEX.read_text()

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


BASE_STATE = """
    state = { last_frame_age_s: 1.0, stale: false, in_contact: true,
              hk_sid_age_s: {'1': 0.4, '2': 2.0, '5': 3.0, '6': 3.0},
              subsystem_staleness: {eps: 0.4, aocs: 1.0, payload: 2.0, ttc: 2.0},
              eps: {}, aocs: {}, payload: {}, ttc: {},
              params: {}, params_age: {} };
"""


# ═══════════════════ AOCS ═══════════════════

@requires_node
class TestAocsCommandedVsActualMode:
    """D-1 (critical). The single highest-value display change in the batch."""

    def test_the_live_run_failure_is_now_visible(self, tmp_path):
        """NOMINAL commanded, COARSE_SUN flown — six orbits, silently."""
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 3};
            state.params['0x0297'] = 4;      // commanded NOMINAL
            state.params['0x0298'] = 1;      // demoted
            updateAOCS();
            return {actual: document.getElementById('aocs-mode-actual').textContent,
                    cmd: document.getElementById('aocs-mode-cmd').textContent,
                    note: document.getElementById('aocs-demote-note').textContent,
                    shown: document.getElementById('aocs-demote-note').style.display};
        """, tmp_path)
        assert out["actual"] == "COARSE_SUN"
        assert "NOMINAL" in out["cmd"]
        assert out["shown"] != "none"
        assert "NOT IN COMMANDED MODE" in out["note"]
        assert "NOMINAL WAS COMMANDED" in out["note"]

    def test_a_transient_demotion_reads_differently_from_a_safing(self, tmp_path):
        """ECLIPSE while NOMINAL is commanded is housekeeping, not a fault."""
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 8};          // ECLIPSE
            state.params['0x0297'] = 4;
            state.params['0x0298'] = 1;
            updateAOCS();
            return document.getElementById('aocs-demote-note').textContent;
        """, tmp_path)
        assert "TRANSIENT" in out
        assert "RETURNING TO NOMINAL" in out

    def test_no_demotion_shows_no_alarm(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x0297'] = 4;
            state.params['0x0298'] = 0;
            updateAOCS();
            return {note: document.getElementById('aocs-demote-note').style.display,
                    actual: document.getElementById('aocs-mode-actual').textContent};
        """, tmp_path)
        assert out["note"] == "none"
        assert out["actual"] == "NOMINAL"

    def test_the_summary_panel_repeats_it(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 3};
            state.params['0x0297'] = 4;
            state.params['0x0298'] = 1;
            updateOverview();
            return document.getElementById('ov-aocs-mode').textContent;
        """, tmp_path)
        assert "COARSE_SUN" in out and "cmd NOMINAL" in out


class TestAocsModeTable:
    """D-3. Mode 9 INERTIAL was missing, so it rendered as `MODE_9`."""

    def test_the_mode_names_match_the_configuration(self):
        codes_path = CONFIG_DIR / "aocs" / "aocs_mode_codes.yaml"
        if not codes_path.exists():
            pytest.skip("aocs_mode_codes.yaml not present")
        codes = yaml.safe_load(codes_path.read_text())["modes"]
        names = {int(code): str(row["name"]).upper() for code, row in codes.items()}
        assert names, "aocs_mode_codes.yaml declares no modes"
        for code, name in names.items():
            assert f"'{name}'" in PAGE, (
                f"AOCS mode {code} ({name}) is not in the console's mode table")

    @requires_node
    def test_mode_nine_is_inertial_not_a_number(self, tmp_path):
        out = run_js("return [aocsModeLabel(9), aocsModeLabel(4), aocsModeLabel(99)];",
                     tmp_path)
        assert out == ["INERTIAL", "NOMINAL", "MODE_99"]


@requires_node
class TestAocsMomentumAndGyros:
    """D-4 / D-5 / D-6, and the 0x02C5 mask."""

    def test_body_momentum_and_envelope_are_shown_beside_wheel_momentum(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x025B'] = 0.0004;   // |h_wheel| — reads as ~zero
            state.params['0x029B'] = 0.0181;   // body momentum
            state.params['0x02BA'] = 88.0;     // envelope % — the wheels ARE saturating
            updateAOCS();
            return {wheel: document.getElementById('aocs-momentum').textContent,
                    body: document.getElementById('aocs-h-body').textContent,
                    env: document.getElementById('aocs-mom-envelope').textContent,
                    fill: document.getElementById('aocs-mom-fill').className};
        """, tmp_path)
        assert out["wheel"] == "0.000 Nms"      # the number that misled operators
        assert out["body"] == "0.0181 Nms"
        assert out["env"] == "88.0 %"
        assert "warn" in out["fill"]            # 70-90 % is amber

    def test_desat_lamps(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x029A'] = 1;   // inhibited (payload imaging)
            state.params['0x029C'] = 1;   // last dump timed out
            updateAOCS();
            return {inh: document.getElementById('aocs-desat-inhibit-lbl').textContent,
                    ineff: document.getElementById('aocs-desat-ineff-lbl').textContent};
        """, tmp_path)
        assert out["inh"] == "INHIBITED"
        assert "INEFFECTIVE" in out["ineff"]

    def test_fine_degraded_is_labelled_as_a_three_wheel_set(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 5};
            state.params['0x0299'] = 1;
            updateAOCS();
            return document.getElementById('aocs-fine-degraded').textContent;
        """, tmp_path)
        assert "3-wheel" in out

    def test_the_fine_ready_mask_is_decoded_to_named_reasons(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x02C5'] = (1 << 0) | (1 << 4);
            updateAOCS();
            return {verdict: document.getElementById('aocs-fine-verdict').textContent,
                    set: document.getElementById('aocs-fine-ready').innerHTML
                         .split('bit-ind set').length - 1};
        """, tmp_path)
        assert "FINE_POINT BLOCKED" in out["verdict"]
        assert "STAR TRACKER" in out["verdict"] and "MOMENTUM" in out["verdict"]
        assert out["set"] == 2

    def test_a_clear_fine_mask_says_fine_is_available(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x02C5'] = 0;
            updateAOCS();
            return document.getElementById('aocs-fine-verdict').textContent;
        """, tmp_path)
        assert out == "FINE_POINT AVAILABLE"

    def test_the_gyro_block_makes_the_device_commands_observable(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.aocs = {mode: 4};
            state.params['0x025D'] = 1;
            state.params['0x0294'] = 2;      // one unit off
            state.params['0x0295'] = 1;      // and a channel saturated
            state.params['0x0296'] = 0.0125;
            updateAOCS();
            return {units: document.getElementById('aocs-gyro-units').textContent,
                    sat: document.getElementById('aocs-gyro-sat').textContent,
                    bias: document.getElementById('aocs-gyro-bias').textContent};
        """, tmp_path)
        assert out["units"] == "2 of 3"
        assert "SATURATED" in out["sat"]
        assert out["bias"].startswith("0.0125")


class TestEventLogCarriesTheReason:
    """D-2: the mode-change reason and the RETURN PATH must not be truncated."""

    def test_long_descriptions_wrap_rather_than_clip(self):
        assert "overflow-wrap: anywhere" in PAGE
        assert ".log-msg { color: var(--text-secondary); flex: 1; min-width: 0;" in PAGE

    def test_instructor_overrides_are_badged(self):
        """EPS R-7: an instructor action must never read as spacecraft behaviour."""
        assert "INSTRUCTOR OVERRIDE" in PAGE
        assert "instructor-badge" in PAGE

    @requires_node
    def test_the_badge_appears_on_an_override_event(self, tmp_path):
        out = run_js("""
            const row = makeLogRow('events', {ts: '12:00:00', sev: 'warn',
              sevLabel: 'EPS',
              msg: 'INSTRUCTOR OVERRIDE: battery SoC set 42 % -> 90 % (not physics)'});
            return {html: row.innerHTML, marked: row.dataset.instructor};
        """, tmp_path)
        assert "instructor-badge" in out["html"]
        assert out["marked"] == "1"

    @requires_node
    def test_an_ordinary_event_is_not_badged(self, tmp_path):
        out = run_js("""
            const row = makeLogRow('events', {ts: '12:00:00', sev: 'info',
              sevLabel: 'AOCS', msg: 'AOCS mode change from ECLIPSE to NOMINAL'});
            return row.innerHTML;
        """, tmp_path)
        assert "instructor-badge" not in out


# ═══════════════════ TT&C ═══════════════════

@requires_node
class TestDownlinkStatusRow:
    """§2.1 — the answer to "why is there no telemetry?"."""

    def test_the_five_states(self, tmp_path):
        out = run_js("""
            const S = {
              transmitting: 0xFF,
              pa_off: 0xFF & ~(1 << 0) & ~(1 << 3) & ~(1 << 5),
              line_off: 0xFF & ~(1 << 1) & ~(1 << 3) & ~(1 << 5),
              no_contact: 0xFF & ~(1 << 2) & ~(1 << 3) & ~(1 << 5),
              beacon_only: (1 << 0) | (1 << 1) | (1 << 4) | (1 << 5) | (1 << 6),
            };
            const r = {};
            for (const k in S) r[k] = downlinkState(S[k]);
            return r;
        """, tmp_path)
        assert out["transmitting"]["label"] == "TRANSMITTING"
        assert out["transmitting"]["cls"] == "ok"
        assert out["pa_off"]["label"] == "INHIBITED (PA OFF)"
        assert out["pa_off"]["cls"] == "bad"
        assert out["line_off"]["label"] == "INHIBITED (ttc_tx LINE OFF)"
        assert out["no_contact"]["label"] == "NO CONTACT"
        assert out["no_contact"]["cls"] == "idle"      # grey: nothing is wrong
        assert out["beacon_only"]["label"] == "BEACON ONLY"
        assert out["beacon_only"]["cls"] == "warn"

    def test_pa_off_with_the_beacon_up_reports_both(self, tmp_path):
        """The common state after an FDIR shutdown — the operator needs both."""
        out = run_js("""
            const gate = (1 << 1) | (1 << 2) | (1 << 4) | (1 << 5) | (1 << 6);
            return downlinkState(gate);
        """, tmp_path)
        assert out["label"] == "INHIBITED (PA OFF) — BEACON ONLY"
        assert "beacon" in out["why"].lower()

    def test_missing_gate_telemetry_is_not_reported_as_transmitting(self, tmp_path):
        out = run_js("return downlinkState(null);", tmp_path)
        assert out["label"] == "UNKNOWN"

    def test_the_row_renders_and_the_gate_bits_are_named(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x052B'] = (1 << 1) | (1 << 2) | (1 << 6);  // PA off
            state.params['0x0525'] = 0;
            updateTTC();
            return {label: document.getElementById('ttc-downlink-state').textContent,
                    why: document.getElementById('ttc-downlink-why').textContent,
                    bits: document.getElementById('ttc-gate-bits').innerHTML,
                    backchannel: document.getElementById('ttc-backchannel-badge').style.display};
        """, tmp_path)
        assert out["label"] == "INHIBITED (PA OFF)"
        assert "power amplifier is off" in out["why"]
        assert "PA ON" in out["bits"] and "S-BAND RADIATING" in out["bits"]
        # §3.1: the RF-quality block is being fed by the back-channel, and says so.
        assert out["backchannel"] == ""


@requires_node
class TestBeaconAndTierGates:

    def test_the_beacon_block_is_independent_of_the_s_band_block(self, tmp_path):
        """R2's teaching point: a UI that dimmed the beacon with the prime
        chain would teach the opposite of the truth."""
        out = run_js(BASE_STATE + """
            state.params['0x052B'] = (1 << 1) | (1 << 4) | (1 << 5) | (1 << 6);
            state.params['0x0526'] = 1; state.params['0x0527'] = 1;
            state.params['0x0528'] = 30.4; state.params['0x0529'] = 437.5;
            state.params['0x052A'] = 1.0; state.params['0x0525'] = 0;
            updateTTC();
            const panel = document.getElementById('ttc-beacon-panel');
            return {active: document.getElementById('ttc-beacon-active-lbl').textContent,
                    freq: document.getElementById('ttc-beacon-freq').textContent,
                    pwr: document.getElementById('ttc-beacon-pwr').textContent,
                    margin: document.getElementById('ttc-beacon-margin').textContent,
                    dimmed: panel.style.opacity || ''};
        """, tmp_path)
        assert out["active"] == "RADIATING"
        assert out["freq"] == "437.5 MHz"
        assert out["pwr"] == "1.00 W"
        assert out["margin"] == "30.4 dB"
        assert out["dimmed"] == "", "the beacon panel must never be dimmed"

    def test_beacon_mode_is_displayed_and_labelled_distinctly(self, tmp_path):
        """0x0521 gates BOTH operational tiers and appeared on no display."""
        out = run_js(BASE_STATE + """
            state.params['0x0521'] = 1;
            state.params['0x0526'] = 0;
            updateTTC();
            return {mode: document.getElementById('ttc-beacon-mode').textContent,
                    tx: document.getElementById('ttc-beacon-tx').textContent};
        """, tmp_path)
        assert "LATCHED" in out["mode"]
        assert out["tx"] == "OFF"          # the two must not be confusable
        assert "TM tier forced (beacon mode, 0x0521)" in PAGE
        assert "UHF beacon TX (0x0526)" in PAGE

    def test_the_eps_ttc_tx_line_is_mirrored_on_the_ttc_tab(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0112'] = 0;
            updateTTC();
            return document.getElementById('ttc-txline-lbl').textContent;
        """, tmp_path)
        assert out == "OFF"
        assert "auto-TX hold" in PAGE

    def test_tx_active_and_eirp_make_set_tx_power_visibly_real(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0525'] = 1; state.params['0x052C'] = 6.0;
            updateTTC();
            return {act: document.getElementById('ttc-txactive-lbl').textContent,
                    eirp: document.getElementById('ttc-eirp').textContent};
        """, tmp_path)
        assert out["act"] == "YES"
        assert out["eirp"] == "6.0 dBW"


# ═══════════════════ PAYLOAD ═══════════════════

@requires_node
class TestFpaReadinessBlock:
    """D-1 — the display half of "FPA does not get to YES"."""

    def test_the_block_shows_the_four_numbers_that_explain_a_no(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0610'] = 0;      // not ready
            state.params['0x0601'] = -8.3;   // detector
            state.params['0x0631'] = -15.0;  // setpoint
            state.params['0x064A'] = -12.8;  // achievable floor
            state.params['0x064B'] = -2.2;   // margin -> the answer
            state.params['0x040C'] = 1;
            state.params['0x0114'] = 1;
            updatePayload();
            const m = document.getElementById('pld-fpa-margin');
            return {ready: document.getElementById('pld-fpa-ready2-lbl').textContent,
                    det: document.getElementById('pld-fpa-det').textContent,
                    set: document.getElementById('pld-fpa-setpoint').textContent,
                    floor: document.getElementById('pld-fpa-floor').textContent,
                    margin: m.textContent, colour: m.style.color,
                    why: document.getElementById('pld-fpa-ready-why').textContent};
        """, tmp_path)
        assert out["ready"] == "NO"
        assert out["det"] == "-8.3 C" and out["set"] == "-15.0 C"
        assert out["floor"] == "-12.8 C" and out["margin"] == "-2.2 K"
        assert "alarm" in out["colour"]          # margin <= 0 is red
        assert "cannot get there" in out["why"]

    def test_a_dark_eps_line_is_named_as_the_first_thing_to_check(self, tmp_path):
        """Three of the four cooler command paths failed precisely here."""
        out = run_js(BASE_STATE + """
            state.params['0x0610'] = 0; state.params['0x0114'] = 0;
            state.params['0x040C'] = 0;
            updatePayload();
            return {why: document.getElementById('pld-fpa-ready-why').textContent,
                    line: document.getElementById('pld-cooler-line').textContent};
        """, tmp_path)
        assert "line 4" in out["why"].lower()
        assert "cannot run" in out["line"]

    def test_ready_shows_no_excuse(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0610'] = 1; state.params['0x064B'] = 5.4;
            state.params['0x0114'] = 1; state.params['0x040C'] = 1;
            updatePayload();
            return {lbl: document.getElementById('pld-fpa-ready2-lbl').textContent,
                    why: document.getElementById('pld-fpa-ready-why').textContent};
        """, tmp_path)
        assert out["lbl"] == "YES"
        assert out["why"] == ""


@requires_node
class TestPayloadModeAndCalibration:

    def test_mode_three_is_calibration_not_a_question_mark(self, tmp_path):
        """D-2. A '?' during every calibration, on a card about calibration."""
        out = run_js(BASE_STATE + """
            const r = {};
            [0, 1, 2, 3].forEach(m => {
              state.params['0x0600'] = m;
              updatePayload();
              r[m] = document.getElementById('pld-mode').textContent;
            });
            return r;
        """, tmp_path)
        assert out == {"0": "OFF", "1": "STANDBY", "2": "IMAGING", "3": "CALIBRATION"}

    def test_mode_two_still_means_imaging(self, tmp_path):
        """The AOCS desat inhibit keys on payload.mode == 2; do not renumber."""
        out = run_js("return PAYLOAD_MODES[2];", tmp_path)
        assert out == "IMAGING"

    def test_the_calibration_block_explains_an_early_stop(self, tmp_path):
        """D-3. A progress bar that stopped, with no reason anywhere."""
        out = run_js(BASE_STATE + """
            state.params['0x0600'] = 3; state.params['0x062A'] = 1;
            state.params['0x062B'] = 42; state.params['0x0644'] = 2;
            state.params['0x0646'] = 0x03; state.params['0x0645'] = 4;
            updatePayload();
            return {mode: document.getElementById('pld-cal-mode').textContent,
                    bands: document.getElementById('pld-cal-bands').textContent,
                    abortShown: document.getElementById('pld-cal-abort-row').style.display,
                    abort: document.getElementById('pld-cal-abort').textContent};
        """, tmp_path)
        assert out["mode"] == "BOTH"
        assert out["bands"].startswith("B G")     # two of four bands calibrated
        assert out["abortShown"] != "none"
        assert "FPA left its regulation band" in out["abort"]

    def test_the_abort_row_is_hidden_when_nothing_went_wrong(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0645'] = 0;
            updatePayload();
            return document.getElementById('pld-cal-abort-row').style.display;
        """, tmp_path)
        assert out == "none"

    def test_all_four_bands_lit_reads_as_fully_calibrated(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x0646'] = 0x0F;
            updatePayload();
            return document.getElementById('pld-cal-bands').textContent;
        """, tmp_path)
        assert "fully calibrated" in out


@requires_node
class TestPayloadStoreAndInstrument:

    def test_products_are_distinguished_from_store_fill(self, tmp_path):
        """D-4. The store starts 20 % full of NON-product fill."""
        out = run_js(BASE_STATE + """
            state.params['0x0604'] = 20.0; state.params['0x060B'] = 4001.4;
            state.params['0x060A'] = 20000; state.params['0x0648'] = 3;
            state.params['0x0647'] = 4.1;
            updatePayload();
            return {store: document.getElementById('pld-store-used').textContent,
                    products: document.getElementById('pld-products').textContent};
        """, tmp_path)
        assert "4001.4 / 20000 MB" in out["store"] and "20.0 %" in out["store"]
        assert out["products"] == "3  (4.1 MB)"

    def test_line_and_data_rate_are_gated_on_readout(self, tmp_path):
        """D-7. They are now non-zero ONLY during a commanded strip, so a
        continuous trend would look dead."""
        out = run_js(BASE_STATE + """
            state.params['0x0649'] = 0; state.params['0x0607'] = 0;
            updatePayload();
            const idle = document.getElementById('pld-rates').textContent;
            state.params['0x0649'] = 1; state.params['0x0607'] = 500;
            state.params['0x0608'] = 9.7;
            updatePayload();
            return {idle: idle, scanning: document.getElementById('pld-rates').textContent,
                    lamp: document.getElementById('pld-readout').textContent};
        """, tmp_path)
        assert "only during a commanded strip" in out["idle"]
        assert "500 lines/s" in out["scanning"]
        assert out["lamp"] == "SCANNING"

    def test_the_shutter_has_a_lamp(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x063C'] = 0;
            updatePayload();
            return document.getElementById('pld-shutter').textContent;
        """, tmp_path)
        assert out == "CLOSED"

    def test_the_momentum_planning_warning_is_shown_not_enforced(self, tmp_path):
        """The payload owner explicitly does NOT gate captures on 0x02C5 b4."""
        out = run_js(BASE_STATE + """
            state.params['0x02C5'] = (1 << 4);
            updatePayload();
            return document.getElementById('pld-desat-warn').textContent;
        """, tmp_path)
        assert "desat is coming" in out

    def test_the_absolute_shutter_command_is_on_the_console(self):
        """D-8. Step 1 of every dark-frame procedure, and the only way to
        reach a KNOWN shutter position."""
        cat = yaml.safe_load((CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        pld = {q.get("name"): q for q in cat["quick_commands"]
               if q.get("group") == "PAYLOAD"}
        assert pld["PLD_SHUTTER_CLOSE"]["args"]["position"] == 0
        assert pld["PLD_SHUTTER_OPEN"]["args"]["position"] == 1
        assert pld["PLD_SHUTTER_CLOSE"]["command"] == "PAYLOAD_SET_SHUTTER"
        assert pld["PLD_CALIBRATION"]["args"]["mode"] == 3


# ═══════════════════ EPS ═══════════════════

class TestSocTrendThresholds:
    """R-1. ">50 W = charging" on a spacecraft that generates 88 W peak."""

    def _budget(self, gen, cons):
        from smo_mcs.displays.power_budget import PowerBudget
        return PowerBudget(power_gen_w=gen, power_cons_w=cons,
                           battery_soc_percent=70.0, battery_temp_c=15.0,
                           load_shedding_stage=0, eclipse_active=False,
                           time_to_eclipse_entry_s=None, time_to_eclipse_exit_s=None,
                           per_subsystem_power={})

    def test_normal_nominal_operations_read_as_charging(self):
        """+9 to +16 W is a real, healthy, battery-charging state."""
        assert self._budget(50.0, 41.0).soc_trend == "charging"
        assert self._budget(85.0, 69.0).soc_trend == "charging"

    def test_eclipse_reads_as_discharging(self):
        assert self._budget(0.0, 38.0).soc_trend == "discharging"

    def test_the_dead_band_survives_the_measurement_noise(self):
        """`power_cons` carries a 1 W-sigma noise term."""
        assert self._budget(40.0, 40.0).soc_trend == "stable"
        assert self._budget(41.5, 40.0).soc_trend == "stable"
        assert self._budget(38.5, 40.0).soc_trend == "stable"

    def test_the_breakdown_states_its_sid_8_dependency(self):
        """R-2. The panel showed nothing and did not say why."""
        from smo_mcs.displays.power_budget import PowerBudgetMonitor
        mon = PowerBudgetMonitor()
        mon.update_from_telemetry({"eps": {"power_gen_W": 85.0, "power_cons_W": 40.0}})
        data = mon.get_display_data()
        assert data["breakdown_available"] is False
        assert "SID 8" in data["breakdown_note"]
        assert data["breakdown_requires"]["sid"] == 8
        assert data["breakdown_residual_w"] is None

    def test_the_breakdown_reconciles_against_power_cons(self):
        from smo_mcs.displays.power_budget import PowerBudgetMonitor
        mon = PowerBudgetMonitor()
        mon.update_from_telemetry({"eps": {
            "power_gen_W": 85.0, "power_cons_W": 40.0,
            "subsys_power_eps": 2.0, "subsys_power_aocs": 9.0,
            "subsys_power_tcs": 8.0, "subsys_power_ttc": 6.5,
            "subsys_power_payload": 8.5, "subsys_power_obdh": 6.0,
        }})
        data = mon.get_display_data()
        assert data["breakdown_available"] is True
        assert data["total_subsystem_power"] == 40.0
        assert data["breakdown_residual_w"] == 0.0


@requires_node
class TestWingStatusAndGeneration:
    """R-3 — the single biggest contributor to "generation drops off too fast"."""

    def test_stowed_wings_are_announced_in_red(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 18.6, power_cons_W: 40.0, soc_pct: 60};
            state.params['0x0144'] = 0x00;
            state.params['0x012B'] = 0.4;      // body faces producing
            state.params['0x0146'] = 0.0; state.params['0x0147'] = 0.0;
            state.params['0x0108'] = 0;
            buildEPSLines(); updateEPS();
            const b = document.getElementById('eps-wing-banner');
            return {text: b.textContent, colour: b.style.color,
                    diag: document.getElementById('eps-gen-diagnosis').textContent};
        """, tmp_path)
        assert "STOWED" in out["text"]
        assert "alarm" in out["colour"]
        assert "WINGS STOWED" in out["diag"]
        assert "not a failure" in out["diag"]

    def test_deployed_wings_read_green(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 85.0, power_cons_W: 40.0, soc_pct: 80};
            state.params['0x0144'] = 0x03;
            state.params['0x0146'] = 1.2; state.params['0x0147'] = 1.2;
            buildEPSLines(); updateEPS();
            const b = document.getElementById('eps-wing-banner');
            return {text: b.textContent, colour: b.style.color,
                    diag: document.getElementById('eps-gen-diagnosis').textContent};
        """, tmp_path)
        assert "DEPLOYED" in out["text"]
        assert "nominal" in out["colour"]
        assert out["diag"] == ""

    def test_a_dead_solar_array_drive_is_distinguishable_from_stowed_wings(self, tmp_path):
        """R-5 case 3: wings deployed, wing currents zero, body faces alive."""
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 17.0, power_cons_W: 40.0, soc_pct: 60};
            state.params['0x0144'] = 0x03;
            state.params['0x0146'] = 0.0; state.params['0x0147'] = 0.0;
            state.params['0x012B'] = 0.5; state.params['0x0108'] = 0;
            buildEPSLines(); updateEPS();
            return document.getElementById('eps-gen-diagnosis').textContent;
        """, tmp_path)
        assert "solar-array drive" in out

    def test_eclipse_is_not_reported_as_a_fault(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 0.0, power_cons_W: 38.0, soc_pct: 70};
            state.params['0x0144'] = 0x03; state.params['0x0108'] = 1;
            buildEPSLines(); updateEPS();
            return document.getElementById('eps-gen-diagnosis').textContent;
        """, tmp_path)
        assert "ECLIPSE" in out and "expected" in out

    def test_per_face_currents_are_rendered(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 85.0, power_cons_W: 40.0};
            [0x012B,0x012C,0x012D,0x012E,0x012F,0x0130].forEach((p,i) => {
              state.params['0x' + p.toString(16).toUpperCase().padStart(4,'0')] = 0.1 * (i+1);
            });
            buildEPSLines(); updateEPS();
            return document.getElementById('eps-faces-tbody').innerHTML;
        """, tmp_path)
        for face in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            assert face in out
        assert "0.600 A" in out


@requires_node
class TestPerLinePowerTable:
    """R-4 — one row per line, with a TOTAL that must reconcile."""

    def test_lines_are_named_and_carry_derived_watts(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_cons_W: 40.0};
            state.params['0x0105'] = 28.0;             // bus voltage
            for (let i = 0; i < 8; i++) {
              state.params['0x' + (0x0110 + i).toString(16).toUpperCase().padStart(4,'0')] = 1;
              state.params['0x' + (0x0118 + i).toString(16).toUpperCase().padStart(4,'0')] = 0.25;
            }
            buildEPSLines(); updateEPS();
            return {body: document.getElementById('eps-lines-tbody').innerHTML,
                    a: document.getElementById('eps-lines-total-a').textContent,
                    w: document.getElementById('eps-lines-total-w').textContent,
                    pcons: document.getElementById('eps-lines-pcons').textContent,
                    l4: document.getElementById('eps-line-4-w').textContent};
        """, tmp_path)
        for name in ("ttc_tx", "payload", "fpa_cooler", "htr_bat", "htr_obc",
                     "aocs_wheels", "obc", "ttc_rx"):
            assert name in out["body"], f"line {name} is not named"
        assert out["l4"] == "7.0"            # 0.25 A x 28 V
        assert out["a"] == "2.000"
        assert out["w"] == "56.0"
        assert out["pcons"] == "40.0 W"

    def test_the_propulsion_line_is_present_and_marked_on_demand(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.params['0x014A'] = 1; state.params['0x014B'] = 0.5;
            state.params['0x0105'] = 28.0;
            buildEPSLines(); updateEPS();
            return {body: document.getElementById('eps-lines-tbody').innerHTML,
                    st: document.getElementById('eps-line-8-status').textContent,
                    w: document.getElementById('eps-line-8-w').textContent};
        """, tmp_path)
        assert "prop" in out["body"] and "on-demand" in out["body"]
        assert out["st"] == "ON" and out["w"] == "14.0"


@requires_node
class TestEnergyIntegral:
    """R-6 — the instantaneous margin cannot answer the operator's question."""

    def test_it_accumulates_and_reports_wh_per_orbit(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 85.0, power_cons_W: 40.0, soc_pct: 70};
            buildEPSLines();
            // Two samples 120 s apart: +45 W for 120 s = 1.5 Wh, which scales
            // to a full orbit.
            let t = 1000000;
            const realNow = Date.now;
            Date.now = () => t * 1000;
            updateEPS();
            t += 120;
            Date.now = () => t * 1000;
            updateEPS();
            Date.now = realNow;
            return {wh: document.getElementById('eps-wh-orbit').textContent,
                    window: document.getElementById('eps-wh-window').textContent,
                    margin: document.getElementById('eps-margin-now').textContent,
                    socmin: document.getElementById('eps-soc-min').textContent};
        """, tmp_path)
        # Positive and scaled to an orbit; the exact figure depends on the
        # orbit period constant, so assert the sign and the units.
        assert out["wh"].startswith("+") and "Wh/orbit" in out["wh"]
        assert "scaled" in out["window"]
        assert out["margin"] == "45.0 W"
        assert out["socmin"] == "70.0 %"

    def test_a_short_window_says_it_is_still_accumulating(self, tmp_path):
        out = run_js(BASE_STATE + """
            state.eps = {power_gen_W: 85.0, power_cons_W: 40.0, soc_pct: 70};
            buildEPSLines(); updateEPS();
            return document.getElementById('eps-wh-orbit').textContent;
        """, tmp_path)
        assert out == "accumulating…"
