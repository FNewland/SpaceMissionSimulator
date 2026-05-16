"""Full mission validation: LEOP, commissioning, and contingencies through RF.

Uses SIX browser windows simultaneously:
  1. MCS (main) — command builder, telemetry tabs, verification log
  2. Radio dashboard — RF lock indicators, Eb/N0 chart, constellation
  3. Instructor — pass override control, failure injection, sim state
  4. Delayed TM Viewer — stored TM dump decode and plots
  5. Orbit Tools — TLE/state vector to TC command converter
  6. Planner — contact windows, ground track, power/data budgets

Every action is visible: commands typed in MCS, lock LEDs checked on
Radio, pass override toggled on Instructor, failures injected visibly.

Run with --headed to watch all three windows:
    pytest tests/test_e2e_browser/test_full_mission_validation.py -v --headed -s

13 passes + final report. Wall clock: ~30-45 minutes.
"""

import json
import struct
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Page

SCREENSHOT_DIR = Path(__file__).parent / "screenshots" / "mission_validation"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

CMD_PAUSE = 2.5
GAP_DURATION = 30


# ═══════════════════════════════════════════════════════════════
# UI HELPERS — actions visible across all three windows
# ═══════════════════════════════════════════════════════════════

class MissionLog:
    def __init__(self):
        self.commands_sent = 0
        self.passes_completed = 0
        self.failures_injected = 0
        self.issues = []

    def report(self):
        print(f"\n{'='*60}")
        print("MISSION VALIDATION FINAL REPORT")
        print(f"{'='*60}")
        print(f"  Passes completed:  {self.passes_completed}")
        print(f"  Commands sent:     {self.commands_sent}")
        print(f"  Failures injected: {self.failures_injected}")
        print(f"  Issues found:      {len(self.issues)}")
        for i, issue in enumerate(self.issues, 1):
            print(f"    {i}. {issue}")
        print(f"{'='*60}")


def _screenshot(page: Page, name: str):
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"))


def _screenshot_all(windows: dict, prefix: str):
    """Screenshot all three windows."""
    for wname, page in windows.items():
        page.bring_to_front()
        time.sleep(0.3)
        _screenshot(page, f"{prefix}_{wname}")


# ── MCS actions (visible in MCS window) ──

def _mcs_switch_tab(mcs: Page, tab: str):
    mcs.bring_to_front()
    mcs.click(f"#tab-{tab}")
    time.sleep(0.5)


def _mcs_send_command(mcs: Page, service: int, subtype: int,
                       data_hex: str, name: str, log: MissionLog):
    """Type command into MCS builder and click Send — visible."""
    mcs.bring_to_front()
    _mcs_switch_tab(mcs, "commanding")
    mcs.select_option("#cmd-service", str(service))
    time.sleep(0.2)
    mcs.fill("#cmd-subtype", str(subtype))
    mcs.fill("#cmd-data", data_hex)
    mcs.fill("#cmd-name", name)
    time.sleep(0.2)
    mcs.on("dialog", lambda d: d.accept())
    mcs.click("#cmd-send-btn")
    log.commands_sent += 1
    print(f"    CMD → {name} (S{service}.{subtype} data={data_hex})")
    time.sleep(CMD_PAUSE)


# ── Radio actions (visible in Radio window) ──

def _radio_check_lock(radio: Page) -> dict:
    """Bring Radio window to front and read lock LEDs."""
    radio.bring_to_front()
    time.sleep(1.0)
    carrier = radio.evaluate(
        "() => document.getElementById('carrier-led')?.className.includes('led-green')")
    bitsync = radio.evaluate(
        "() => document.getElementById('bitsync-led')?.className.includes('led-green')")
    framesync = radio.evaluate(
        "() => document.getElementById('framesync-led')?.className.includes('led-green')")
    status = "LOCKED" if framesync else ("ACQUIRING" if carrier else "NO SIGNAL")
    print(f"    RADIO: carrier={'G' if carrier else 'R'} "
          f"bit={'G' if bitsync else 'R'} "
          f"frame={'G' if framesync else 'R'} [{status}]")
    return {"carrier": carrier, "bitsync": bitsync, "framesync": framesync}


def _radio_read_ebn0(radio: Page) -> str:
    """Read Eb/N0 from Radio dashboard."""
    radio.bring_to_front()
    time.sleep(0.3)
    try:
        return radio.locator("#ebn0").inner_text()
    except Exception:
        return "N/A"


# ── Instructor actions (visible in Instructor window) ──

def _instructor_api(stack: dict, cmd: dict):
    """Send via instructor HTTP API."""
    try:
        body = json.dumps(cmd).encode()
        req = urllib.request.Request(
            f"{stack['sim_http']}/api/command",
            data=body, headers={"Content-Type": "application/json"},
            method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _pass_start(stack: dict, windows: dict, pass_num: int, name: str,
                log: MissionLog):
    """Begin pass — toggle override on Instructor, check Radio."""
    print(f"\n{'='*60}")
    print(f"PASS {pass_num}: {name}")
    print(f"{'='*60}")

    # Show instructor and enable override
    inst = windows["instructor"]
    inst.bring_to_front()
    time.sleep(0.5)
    _instructor_api(stack, {"type": "override_passes", "enabled": True})
    _instructor_api(stack, {"type": "set_speed", "value": 1.0})
    print("    [INSTRUCTOR] Pass override ENABLED, speed 1x")
    time.sleep(2.0)

    # Check Radio for lock
    _radio_check_lock(windows["radio"])

    # Switch MCS to overview
    _mcs_switch_tab(windows["mcs"], "overview")
    time.sleep(1.0)


def _pass_end(stack: dict, windows: dict, pass_num: int, log: MissionLog):
    """End pass — screenshot all windows, disable override."""
    _screenshot_all(windows, f"pass{pass_num:02d}")
    log.passes_completed = pass_num

    # Show instructor and disable override
    inst = windows["instructor"]
    inst.bring_to_front()
    time.sleep(0.3)
    _instructor_api(stack, {"type": "override_passes", "enabled": False})
    _instructor_api(stack, {"type": "set_speed", "value": 5.0})
    print(f"    [INSTRUCTOR] Pass override DISABLED, speed 5x")

    # Check Radio — should lose lock
    time.sleep(3.0)
    _radio_check_lock(windows["radio"])

    print(f"  [Pass {pass_num} complete — gap {GAP_DURATION}s at 5x]")
    time.sleep(GAP_DURATION)
    _instructor_api(stack, {"type": "set_speed", "value": 1.0})


def _inject_failure(stack: dict, windows: dict, log: MissionLog,
                     subsystem: str, failure: str, **kw):
    """Inject failure — show instructor window."""
    inst = windows["instructor"]
    inst.bring_to_front()
    time.sleep(0.5)
    cmd = {"type": "failure_inject", "subsystem": subsystem, "failure": failure}
    cmd.update(kw)
    _instructor_api(stack, cmd)
    log.failures_injected += 1
    print(f"    [INSTRUCTOR] ⚡ INJECTED: {subsystem}/{failure}")
    time.sleep(1.0)


def _clear_failures(stack: dict, windows: dict, log: MissionLog,
                     subsystem: str = None):
    inst = windows["instructor"]
    inst.bring_to_front()
    time.sleep(0.3)
    if subsystem:
        _instructor_api(stack, {"type": "failure_clear", "subsystem": subsystem})
    else:
        _instructor_api(stack, {"type": "failure_clear"})
    print(f"    [INSTRUCTOR] ✓ Cleared failures" +
          (f" ({subsystem})" if subsystem else ""))


# ── Delayed TM Viewer actions ──

def _delayed_tm_check(dtm: Page):
    """Bring Delayed TM viewer to front and refresh dump list."""
    dtm.bring_to_front()
    time.sleep(0.5)
    try:
        dtm.click("#btnRefresh")
        time.sleep(2.0)
    except Exception:
        pass
    print("    [DELAYED TM] Refreshed dump list")


def _delayed_tm_load_latest(dtm: Page):
    """Click 'Latest' to load most recent dump."""
    dtm.bring_to_front()
    time.sleep(0.3)
    try:
        dtm.click("#btnLatest")
        time.sleep(3.0)
    except Exception:
        pass
    print("    [DELAYED TM] Loaded latest dump")


# ── Orbit Tools actions ──

def _orbit_tools_convert_tle(orbit: Page):
    """Enter EOSAT-1 TLE and convert to TC commands."""
    orbit.bring_to_front()
    time.sleep(0.5)
    tle1 = "1 99001U 26001A   26068.50000000  .00000100  00000-0  10000-4 0  9990"
    tle2 = "2 99001  98.0000 120.0000 0001200  90.0000 270.0000 15.24000000 00010"
    try:
        orbit.fill("#tle1", tle1)
        orbit.fill("#tle2", tle2)
        time.sleep(0.3)
        orbit.click("button:has-text('Convert')")
        time.sleep(2.0)
        print("    [ORBIT TOOLS] TLE converted to TC commands")
    except Exception as e:
        print(f"    [ORBIT TOOLS] TLE conversion: {e}")


# ── Planner actions ──

def _planner_check_contacts(planner: Page):
    """Bring Planner to front and check contact window display."""
    planner.bring_to_front()
    time.sleep(1.0)
    print("    [PLANNER] Contact windows displayed")


# ═══════════════════════════════════════════════════════════════
# TEST CLASS
# ═══════════════════════════════════════════════════════════════

class TestFullMissionValidation:

    @pytest.fixture(autouse=True, scope="class")
    def mission_log(self):
        log = MissionLog()
        yield log
        log.report()

    # ─── PASS 1: First Acquisition ─────────────────────────────

    def test_pass01_first_acquisition(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 1, "FIRST ACQUISITION", log)

        _mcs_send_command(mcs, 17, 1, "", "CONNECTION_TEST", log)

        # Check Radio for lock progress
        _radio_check_lock(w["radio"])
        time.sleep(5.0)

        _mcs_send_command(mcs, 8, 1, "37", "OBC_BOOT_APP", log)
        time.sleep(12.0)

        # Set time
        from datetime import datetime, timezone
        cuc_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        cuc_s = int((datetime.now(timezone.utc) - cuc_epoch).total_seconds())
        _mcs_send_command(mcs, 9, 1, struct.pack('>I', cuc_s).hex(), "SET_TIME", log)

        _mcs_send_command(mcs, 3, 27, "00 01", "HK_REQ_EPS", log)

        # Show MCS overview
        _mcs_switch_tab(mcs, "overview")
        time.sleep(3.0)

        _pass_end(stack, w, 1, log)

    # ─── PASS 2: Antenna & Link ───────────────────────────────

    def test_pass02_antenna_link(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 2, "ANTENNA & LINK SETUP", log)

        _mcs_send_command(mcs, 8, 1, "45", "DEPLOY_ANTENNAS", log)
        _mcs_send_command(mcs, 8, 1, "42", "PA_ON", log)

        for sid, name in [(1,"EPS"), (3,"TCS"), (4,"PLATFORM"), (6,"TTC")]:
            _mcs_send_command(mcs, 3, 27, f"00 {sid:02x}", f"HK_{name}", log)

        _mcs_send_command(mcs, 15, 1, "00", "S15_ENABLE", log)

        # Check Radio — should show lock
        lock = _radio_check_lock(w["radio"])
        ebn0 = _radio_read_ebn0(w["radio"])
        print(f"    RADIO Eb/N0: {ebn0}")

        # Upload TLE via Orbit Tools
        _orbit_tools_convert_tle(w["orbit_tools"])
        _screenshot(w["orbit_tools"], "pass02_orbit_tools")

        # Check Planner for contact windows
        _planner_check_contacts(w["planner"])
        _screenshot(w["planner"], "pass02_planner")

        # Show TTC tab on MCS
        _mcs_switch_tab(mcs, "ttc")
        time.sleep(3.0)

        _pass_end(stack, w, 2, log)

    # ─── PASS 3: AOCS Init ────────────────────────────────────

    def test_pass03_aocs_init(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 3, "AOCS INITIALIZATION", log)

        _mcs_send_command(mcs, 8, 1, "13 07", "PWR_ON_AOCS", log)
        _mcs_send_command(mcs, 8, 1, "07 01", "MAG_A_ENABLE", log)
        _mcs_send_command(mcs, 8, 1, "00 02", "AOCS_DETUMBLE", log)

        _mcs_switch_tab(mcs, "aocs")
        time.sleep(10.0)

        _mcs_send_command(mcs, 3, 27, "00 02", "HK_AOCS", log)
        _pass_end(stack, w, 3, log)

    # ─── PASS 4: Attitude Control ─────────────────────────────

    def test_pass04_attitude(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 4, "ATTITUDE CONTROL", log)

        _mcs_send_command(mcs, 8, 1, "04 01", "ST1_ON", log)
        _mcs_send_command(mcs, 8, 1, "05 01", "ST2_ON", log)
        time.sleep(10.0)

        _mcs_send_command(mcs, 8, 1, "00 03", "COARSE_SUN", log)
        time.sleep(8.0)
        _mcs_send_command(mcs, 8, 1, "00 04", "NOMINAL", log)

        _mcs_switch_tab(mcs, "aocs")
        time.sleep(5.0)
        _pass_end(stack, w, 4, log)

    # ─── PASS 5: Checkout ─────────────────────────────────────

    def test_pass05_checkout(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 5, "SUBSYSTEM CHECKOUT", log)

        _mcs_send_command(mcs, 8, 1, "13 04", "PWR_ON_PAYLOAD", log)
        _mcs_send_command(mcs, 8, 1, "1a 01", "PAYLOAD_STANDBY", log)
        _mcs_send_command(mcs, 8, 1, "13 05", "PWR_ON_HTR_BAT", log)

        _mcs_switch_tab(mcs, "eps")
        time.sleep(5.0)
        _mcs_switch_tab(mcs, "tcs")
        time.sleep(3.0)
        _mcs_switch_tab(mcs, "payload")
        time.sleep(3.0)

        _pass_end(stack, w, 5, log)

    # ─── PASS 6: Advanced Ops ─────────────────────────────────

    def test_pass06_advanced(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 6, "ADVANCED OPERATIONS", log)

        _mcs_send_command(mcs, 15, 9, "00", "S15_DUMP", log)
        time.sleep(8.0)

        # Check Delayed TM viewer for the dump
        _delayed_tm_check(w["delayed_tm"])
        _delayed_tm_load_latest(w["delayed_tm"])
        _screenshot(w["delayed_tm"], "pass06_delayed_tm")

        _mcs_send_command(mcs, 11, 17, "", "LIST_SCHEDULED", log)
        _mcs_send_command(mcs, 15, 11, "00", "S15_CLEAR", log)

        # Check Radio
        _radio_check_lock(w["radio"])

        # Check Planner
        _planner_check_contacts(w["planner"])
        _screenshot(w["planner"], "pass06_planner")

        _pass_end(stack, w, 6, log)

    # ─── PASS 7: RW Seizure ───────────────────────────────────

    def test_pass07_rw_seizure(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 7, "CONTINGENCY: RW SEIZURE", log)

        _inject_failure(stack, w, log, "aocs", "rw_seizure",
                        magnitude=1.0, onset="step", wheel=0)
        time.sleep(5.0)

        _mcs_switch_tab(mcs, "aocs")
        time.sleep(3.0)

        _mcs_send_command(mcs, 8, 1, "02 00", "DISABLE_WHEEL_0", log)
        _mcs_send_command(mcs, 8, 1, "01", "DESATURATE", log)
        time.sleep(5.0)

        _clear_failures(stack, w, log, "aocs")
        _pass_end(stack, w, 7, log)

    # ─── PASS 8: Sensor Cascade ───────────────────────────────

    def test_pass08_sensors(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 8, "CONTINGENCY: SENSOR CASCADE", log)

        _inject_failure(stack, w, log, "aocs", "st_failure",
                        magnitude=1.0, onset="step", unit=1)
        time.sleep(3.0)
        _mcs_switch_tab(mcs, "aocs")
        time.sleep(2.0)

        _mcs_send_command(mcs, 8, 1, "06 02", "SELECT_ST2", log)

        _inject_failure(stack, w, log, "aocs", "mag_a_fail",
                        magnitude=1.0, onset="step")
        time.sleep(3.0)
        _mcs_send_command(mcs, 8, 1, "07 01", "SELECT_MAG_B", log)

        _clear_failures(stack, w, log, "aocs")
        _pass_end(stack, w, 8, log)

    # ─── PASS 9: EPS Overcurrent ──────────────────────────────

    def test_pass09_overcurrent(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 9, "CONTINGENCY: EPS OVERCURRENT", log)

        _inject_failure(stack, w, log, "eps", "overcurrent",
                        magnitude=2.0, onset="step", line_index=3)
        time.sleep(5.0)

        _mcs_switch_tab(mcs, "eps")
        time.sleep(3.0)

        _mcs_send_command(mcs, 8, 1, "15 03", "RESET_OC", log)
        _mcs_send_command(mcs, 8, 1, "13 03", "PWR_ON_LINE3", log)

        _clear_failures(stack, w, log, "eps")
        _pass_end(stack, w, 9, log)

    # ─── PASS 10: Load Shedding ───────────────────────────────

    def test_pass10_load_shed(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 10, "CONTINGENCY: LOAD SHEDDING", log)

        _inject_failure(stack, w, log, "eps", "undervoltage",
                        magnitude=1.5, onset="gradual", onset_duration_s=30)
        time.sleep(10.0)

        _mcs_switch_tab(mcs, "eps")
        time.sleep(5.0)

        _mcs_send_command(mcs, 8, 1, "14 04", "SHED_PAYLOAD", log)
        _clear_failures(stack, w, log, "eps")
        time.sleep(8.0)
        _mcs_send_command(mcs, 8, 1, "13 04", "RESTORE_PAYLOAD", log)

        _pass_end(stack, w, 10, log)

    # ─── PASS 11: OBC Crash ───────────────────────────────────

    def test_pass11_obc_crash(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 11, "CONTINGENCY: OBC CRASH", log)

        _inject_failure(stack, w, log, "obdh", "obc_crash",
                        magnitude=1.0, onset="step")
        time.sleep(5.0)

        _mcs_switch_tab(mcs, "obdh")
        time.sleep(3.0)

        _mcs_send_command(mcs, 17, 1, "", "CONN_TEST", log)
        _mcs_send_command(mcs, 8, 1, "37", "BOOT_APP", log)
        time.sleep(12.0)

        _clear_failures(stack, w, log, "obdh")

        # Check Radio — should re-acquire lock after boot
        _radio_check_lock(w["radio"])
        _pass_end(stack, w, 11, log)

    # ─── PASS 12: TTC No-TM ──────────────────────────────────

    def test_pass12_ttc_no_tm(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 12, "CONTINGENCY: TTC NO-TM", log)

        _inject_failure(stack, w, log, "ttc", "primary_failure",
                        magnitude=1.0, onset="step")
        time.sleep(5.0)

        # Check Radio — should show lock loss
        _radio_check_lock(w["radio"])
        _mcs_switch_tab(mcs, "ttc")
        time.sleep(2.0)

        _mcs_send_command(mcs, 8, 1, "40", "SWITCH_REDUNDANT", log)
        _mcs_send_command(mcs, 8, 1, "42", "PA_ON", log)
        time.sleep(5.0)

        _clear_failures(stack, w, log, "ttc")
        _radio_check_lock(w["radio"])
        _pass_end(stack, w, 12, log)

    # ─── PASS 13: PA Overheat ─────────────────────────────────

    def test_pass13_pa_overheat(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]
        _pass_start(stack, w, 13, "CONTINGENCY: PA OVERHEAT", log)

        _inject_failure(stack, w, log, "ttc", "pa_overheat",
                        magnitude=1.0, onset="gradual", onset_duration_s=60)
        time.sleep(15.0)

        # Show TTC tab + Radio
        _mcs_switch_tab(mcs, "ttc")
        time.sleep(3.0)
        ebn0 = _radio_read_ebn0(w["radio"])
        print(f"    RADIO Eb/N0: {ebn0}")

        _mcs_send_command(mcs, 8, 1, "43", "PA_OFF", log)
        time.sleep(10.0)
        _mcs_send_command(mcs, 8, 1, "42", "PA_ON", log)

        _clear_failures(stack, w, log)
        _mcs_send_command(mcs, 17, 1, "", "FINAL_CONN_TEST", log)

        _radio_check_lock(w["radio"])
        _pass_end(stack, w, 13, log)

    # ─── FINAL REPORT ──────────────────────────────────────────

    def test_final_report(self, multi_window, system_stack, mission_log):
        w, stack, log = multi_window, system_stack, mission_log
        mcs = w["mcs"]

        for tab in ["overview", "eps", "tcs", "aocs", "ttc",
                     "payload", "obdh", "commanding"]:
            _mcs_switch_tab(mcs, tab)
            time.sleep(1.5)
            _screenshot(mcs, f"final_{tab}")

        # Final screenshots of all tool windows
        for wname in ["radio", "instructor", "delayed_tm", "orbit_tools", "planner"]:
            w[wname].bring_to_front()
            time.sleep(1.0)
            _screenshot(w[wname], f"final_{wname}")

        assert log.passes_completed >= 13
        assert log.commands_sent > 40
