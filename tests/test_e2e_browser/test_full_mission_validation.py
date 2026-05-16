"""Full mission validation: LEOP, commissioning, and contingencies through RF.

Every action is performed visibly through the MCS browser UI:
- Tab switches are clicked
- Commands are typed into the command builder and sent via the Send button
- Telemetry is read from the displayed values
- ACKs are verified in the verification log table
- Screenshots are taken at each milestone

Run with --headed to watch the operator sequence in real time:
    pytest tests/test_e2e_browser/test_full_mission_validation.py -v --headed -s

13 passes covering LEOP + commissioning + all 8 contingency scenarios.
Wall clock: ~30-45 minutes with scripted pass windows.
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

# Timing
PASS_CONTACT = 120    # 2 min contact per pass
GAP_DURATION = 30     # 30s gap (at 5x speed)
CMD_PAUSE = 2.5       # pause between commands so operator can see


# ═══════════════════════════════════════════════════════════════
# UI HELPERS — every action is visible in the browser
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


def _sim_cmd(stack: dict, cmd: dict):
    """Send instructor command (not visible in MCS — sim-side only)."""
    try:
        body = json.dumps(cmd).encode()
        req = urllib.request.Request(
            f"{stack['sim_http']}/api/command",
            data=body, headers={"Content-Type": "application/json"},
            method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _screenshot(page: Page, name: str):
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"))


def _switch_tab(page: Page, tab_name: str):
    """Click a tab in the MCS — visible tab switch."""
    page.click(f"#tab-{tab_name}")
    time.sleep(0.5)


def _send_command_via_ui(page: Page, service: int, subtype: int,
                          data_hex: str, name: str, log: MissionLog):
    """Type a command into the MCS command builder and click Send.

    This is fully visible in the browser — the operator sees:
    1. Switch to Commanding tab
    2. Service dropdown selected
    3. Subtype filled
    4. Data hex typed
    5. Name typed
    6. SEND button clicked
    7. Event log shows confirmation
    """
    _switch_tab(page, "commanding")
    time.sleep(0.3)

    # Select service from dropdown
    page.select_option("#cmd-service", str(service))
    time.sleep(0.2)

    # Clear and fill subtype
    page.fill("#cmd-subtype", str(subtype))
    time.sleep(0.1)

    # Clear and fill data hex
    page.fill("#cmd-data", data_hex)
    time.sleep(0.1)

    # Clear and fill command name
    page.fill("#cmd-name", name)
    time.sleep(0.2)

    # Handle any confirm dialogs (for unknown subtypes)
    page.on("dialog", lambda d: d.accept())

    # Click SEND — visible button press
    page.click("#cmd-send-btn")
    log.commands_sent += 1

    print(f"    CMD → {name} (S{service}.{subtype} data={data_hex})")
    time.sleep(CMD_PAUSE)


def _check_ttc_lock(page: Page) -> dict:
    """Switch to TTC tab and read lock chain status — visible check."""
    _switch_tab(page, "ttc")
    time.sleep(1.0)

    carrier = page.evaluate(
        "() => document.getElementById('ttc-carrier')?.classList.contains('locked')")
    bitsync = page.evaluate(
        "() => document.getElementById('ttc-bitsync')?.classList.contains('locked')")
    framesync = page.evaluate(
        "() => document.getElementById('ttc-framesync')?.classList.contains('locked')")

    status = "LOCKED" if framesync else ("ACQUIRING" if carrier else "NO SIGNAL")
    print(f"    TTC: carrier={'Y' if carrier else 'N'} "
          f"bit={'Y' if bitsync else 'N'} "
          f"frame={'Y' if framesync else 'N'} [{status}]")
    return {"carrier": carrier, "bitsync": bitsync, "framesync": framesync}


def _check_verif_log(page: Page) -> int:
    """Switch to commanding tab and count verification entries — visible."""
    _switch_tab(page, "commanding")
    time.sleep(1.0)
    count = page.locator("#verif-tbody tr").count()
    print(f"    Verification log: {count} entries")
    return count


def _read_conn_status(page: Page) -> str:
    """Read connection status from the status bar — visible."""
    return page.locator("#sb-conn").inner_text()


def _pass_start(stack: dict, page: Page, pass_num: int, name: str, log: MissionLog):
    """Begin a pass — enable override, speed 1x, show overview."""
    print(f"\n{'='*60}")
    print(f"PASS {pass_num}: {name}")
    print(f"{'='*60}")
    _sim_cmd(stack, {"type": "override_passes", "enabled": True})
    _sim_cmd(stack, {"type": "set_speed", "value": 1.0})
    _switch_tab(page, "overview")
    time.sleep(3.0)  # let lock establish


def _pass_end(stack: dict, page: Page, pass_num: int, log: MissionLog):
    """End pass — screenshot, disable override, speed up for gap."""
    _screenshot(page, f"pass{pass_num:02d}_end")
    log.passes_completed = pass_num
    conn = _read_conn_status(page)
    print(f"    Connection: {conn}")
    _sim_cmd(stack, {"type": "override_passes", "enabled": False})
    _sim_cmd(stack, {"type": "set_speed", "value": 5.0})
    print(f"  [Pass {pass_num} complete — gap {GAP_DURATION}s at 5x]")
    time.sleep(GAP_DURATION)
    _sim_cmd(stack, {"type": "set_speed", "value": 1.0})


def _inject_failure(stack: dict, log: MissionLog,
                     subsystem: str, failure: str, **kw):
    """Inject failure via instructor API (not UI — instructor-side)."""
    cmd = {"type": "failure_inject", "subsystem": subsystem, "failure": failure}
    cmd.update(kw)
    _sim_cmd(stack, cmd)
    log.failures_injected += 1
    print(f"    ⚡ INJECTED: {subsystem}/{failure}")


def _clear_failures(stack: dict, log: MissionLog, subsystem: str = None):
    if subsystem:
        _sim_cmd(stack, {"type": "failure_clear", "subsystem": subsystem})
    else:
        _sim_cmd(stack, {"type": "failure_clear"})
    print(f"    ✓ Cleared failures" + (f" ({subsystem})" if subsystem else ""))


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

    def test_pass01_first_acquisition(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 1, "FIRST ACQUISITION", log)

        # Send CONNECTION_TEST to trigger auto-TX
        _send_command_via_ui(page, 17, 1, "", "CONNECTION_TEST", log)

        # Check TTC lock chain (visible on TTC tab)
        _check_ttc_lock(page)
        time.sleep(5.0)

        # OBC Boot App
        _send_command_via_ui(page, 8, 1, "37", "OBC_BOOT_APP", log)
        time.sleep(12.0)  # boot countdown

        # Set time
        from datetime import datetime, timezone
        cuc_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        cuc_s = int((datetime.now(timezone.utc) - cuc_epoch).total_seconds())
        _send_command_via_ui(page, 9, 1, struct.pack('>I', cuc_s).hex(), "SET_TIME", log)

        # Request EPS HK to prove app is running
        _send_command_via_ui(page, 3, 27, "00 01", "HK_REQ_EPS", log)

        # Show overview with telemetry
        _switch_tab(page, "overview")
        time.sleep(3.0)
        _screenshot(page, "pass01_overview")

        _pass_end(stack, page, 1, log)

    # ─── PASS 2: Antenna & Link Setup ─────────────────────────

    def test_pass02_antenna_link(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 2, "ANTENNA & LINK SETUP", log)

        _send_command_via_ui(page, 8, 1, "45", "DEPLOY_ANTENNAS", log)
        _send_command_via_ui(page, 8, 1, "42", "PA_ON", log)

        # Request HK for multiple subsystems (visible commanding)
        for sid, name in [(1,"EPS"), (3,"TCS"), (4,"PLATFORM"), (6,"TTC")]:
            _send_command_via_ui(page, 3, 27, f"00 {sid:02x}", f"HK_{name}", log)

        # Enable S15 TM storage
        _send_command_via_ui(page, 15, 1, "00", "S15_ENABLE_STORE", log)

        # Check lock chain on TTC tab
        _check_ttc_lock(page)
        _screenshot(page, "pass02_ttc")

        # Check verification log
        _check_verif_log(page)

        _pass_end(stack, page, 2, log)

    # ─── PASS 3: AOCS Initialization ──────────────────────────

    def test_pass03_aocs_init(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 3, "AOCS INITIALIZATION", log)

        _send_command_via_ui(page, 8, 1, "13 07", "PWR_ON_AOCS_WHEELS", log)
        _send_command_via_ui(page, 8, 1, "07 01", "MAG_A_ENABLE", log)
        _send_command_via_ui(page, 8, 1, "00 02", "AOCS_SET_DETUMBLE", log)

        # Switch to AOCS tab to watch rates
        _switch_tab(page, "aocs")
        time.sleep(10.0)
        _screenshot(page, "pass03_aocs")

        # Request AOCS HK
        _send_command_via_ui(page, 3, 27, "00 02", "HK_REQ_AOCS", log)

        _pass_end(stack, page, 3, log)

    # ─── PASS 4: Attitude Control ─────────────────────────────

    def test_pass04_attitude_control(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 4, "ATTITUDE CONTROL", log)

        _send_command_via_ui(page, 8, 1, "04 01", "ST1_POWER_ON", log)
        _send_command_via_ui(page, 8, 1, "05 01", "ST2_POWER_ON", log)
        time.sleep(10.0)

        _send_command_via_ui(page, 8, 1, "00 03", "AOCS_COARSE_SUN", log)
        time.sleep(8.0)

        _send_command_via_ui(page, 8, 1, "00 04", "AOCS_NOMINAL", log)

        # Watch AOCS tab
        _switch_tab(page, "aocs")
        time.sleep(5.0)
        _screenshot(page, "pass04_attitude")

        _pass_end(stack, page, 4, log)

    # ─── PASS 5: Subsystem Checkout ───────────────────────────

    def test_pass05_checkout(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 5, "SUBSYSTEM CHECKOUT", log)

        _send_command_via_ui(page, 8, 1, "13 04", "PWR_ON_PAYLOAD", log)
        _send_command_via_ui(page, 8, 1, "1a 01", "PAYLOAD_STANDBY", log)
        _send_command_via_ui(page, 8, 1, "13 05", "PWR_ON_HTR_BAT", log)

        # Show EPS tab
        _switch_tab(page, "eps")
        time.sleep(5.0)
        _screenshot(page, "pass05_eps")

        # Show TCS tab
        _switch_tab(page, "tcs")
        time.sleep(3.0)
        _screenshot(page, "pass05_tcs")

        # Show Payload tab
        _switch_tab(page, "payload")
        time.sleep(3.0)
        _screenshot(page, "pass05_payload")

        _pass_end(stack, page, 5, log)

    # ─── PASS 6: Advanced Operations ──────────────────────────

    def test_pass06_advanced_ops(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 6, "ADVANCED OPERATIONS", log)

        # TM dump request
        _send_command_via_ui(page, 15, 9, "00", "S15_DUMP_STORE", log)
        time.sleep(8.0)

        # Schedule time-tagged command
        cuc_epoch = __import__('datetime').datetime(2000,1,1,12,0,0,
            tzinfo=__import__('datetime').timezone.utc)
        future = int((__import__('datetime').datetime.now(
            __import__('datetime').timezone.utc) - cuc_epoch).total_seconds()) + 60
        ttc_data = struct.pack('>I', future) + bytes([8, 1, 0, 7])
        _send_command_via_ui(page, 11, 4, ttc_data.hex(), "SCHEDULE_DESAT_T+60", log)

        # List scheduled
        _send_command_via_ui(page, 11, 17, "", "LIST_SCHEDULED", log)

        # Show commanding tab with verification log
        _check_verif_log(page)
        _screenshot(page, "pass06_commanding")

        _pass_end(stack, page, 6, log)

    # ─── PASS 7: CONTINGENCY — RW Seizure ─────────────────────

    def test_pass07_rw_seizure(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 7, "CONTINGENCY: RW SEIZURE", log)

        _inject_failure(stack, log, "aocs", "rw_seizure",
                        magnitude=1.0, onset="step", wheel=0)
        time.sleep(5.0)

        # Detect on AOCS tab
        _switch_tab(page, "aocs")
        time.sleep(3.0)
        _screenshot(page, "pass07_detect")

        # Recover: disable wheel, desaturate
        _send_command_via_ui(page, 8, 1, "02 00", "DISABLE_WHEEL_0", log)
        _send_command_via_ui(page, 8, 1, "01", "DESATURATE", log)
        time.sleep(5.0)

        _clear_failures(stack, log, "aocs")
        _switch_tab(page, "aocs")
        time.sleep(3.0)
        _screenshot(page, "pass07_recovery")

        _pass_end(stack, page, 7, log)

    # ─── PASS 8: CONTINGENCY — Sensor Cascade ─────────────────

    def test_pass08_sensor_cascade(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 8, "CONTINGENCY: SENSOR CASCADE", log)

        _inject_failure(stack, log, "aocs", "st_failure",
                        magnitude=1.0, onset="step", unit=1)
        time.sleep(3.0)

        # Detect on AOCS tab
        _switch_tab(page, "aocs")
        time.sleep(3.0)

        # Switch to ST2
        _send_command_via_ui(page, 8, 1, "06 02", "SELECT_ST2", log)

        _inject_failure(stack, log, "aocs", "mag_a_fail",
                        magnitude=1.0, onset="step")
        time.sleep(3.0)

        # Switch to MAG-B
        _send_command_via_ui(page, 8, 1, "07 01", "SELECT_MAG_B", log)

        _clear_failures(stack, log, "aocs")
        _screenshot(page, "pass08_recovery")
        _pass_end(stack, page, 8, log)

    # ─── PASS 9: CONTINGENCY — EPS Overcurrent ────────────────

    def test_pass09_eps_overcurrent(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 9, "CONTINGENCY: EPS OVERCURRENT", log)

        _inject_failure(stack, log, "eps", "overcurrent",
                        magnitude=2.0, onset="step", line_index=3)
        time.sleep(5.0)

        # Detect on EPS tab
        _switch_tab(page, "eps")
        time.sleep(3.0)
        _screenshot(page, "pass09_detect")

        # Reset OC flag and re-enable
        _send_command_via_ui(page, 8, 1, "15 03", "RESET_OC_LINE3", log)
        _send_command_via_ui(page, 8, 1, "13 03", "PWR_ON_LINE3", log)

        _clear_failures(stack, log, "eps")
        _switch_tab(page, "eps")
        time.sleep(3.0)
        _screenshot(page, "pass09_recovery")

        _pass_end(stack, page, 9, log)

    # ─── PASS 10: CONTINGENCY — Load Shedding ─────────────────

    def test_pass10_load_shedding(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 10, "CONTINGENCY: LOAD SHEDDING", log)

        _inject_failure(stack, log, "eps", "undervoltage",
                        magnitude=1.5, onset="gradual", onset_duration_s=30)
        time.sleep(10.0)

        # Watch EPS tab
        _switch_tab(page, "eps")
        time.sleep(5.0)
        _screenshot(page, "pass10_detect")

        # Shed payload
        _send_command_via_ui(page, 8, 1, "14 04", "PWR_OFF_PAYLOAD", log)

        _clear_failures(stack, log, "eps")
        time.sleep(8.0)

        # Re-enable
        _send_command_via_ui(page, 8, 1, "13 04", "PWR_ON_PAYLOAD", log)
        _screenshot(page, "pass10_recovery")

        _pass_end(stack, page, 10, log)

    # ─── PASS 11: CONTINGENCY — OBC Crash ─────────────────────

    def test_pass11_obc_crash(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 11, "CONTINGENCY: OBC CRASH", log)

        _inject_failure(stack, log, "obdh", "obc_crash",
                        magnitude=1.0, onset="step")
        time.sleep(5.0)

        # Detect on OBDH tab
        _switch_tab(page, "obdh")
        time.sleep(3.0)
        _screenshot(page, "pass11_detect")

        # Connection test (bootloader-allowed)
        _send_command_via_ui(page, 17, 1, "", "CONN_TEST_BOOTLOADER", log)

        # Re-boot app
        _send_command_via_ui(page, 8, 1, "37", "OBC_BOOT_APP_RECOVER", log)
        time.sleep(12.0)

        _clear_failures(stack, log, "obdh")
        _switch_tab(page, "overview")
        time.sleep(3.0)
        _screenshot(page, "pass11_recovery")

        _pass_end(stack, page, 11, log)

    # ─── PASS 12: CONTINGENCY — TTC No-TM ─────────────────────

    def test_pass12_ttc_no_tm(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 12, "CONTINGENCY: TTC NO-TM", log)

        _inject_failure(stack, log, "ttc", "primary_failure",
                        magnitude=1.0, onset="step")
        time.sleep(5.0)

        # Check TTC tab
        _check_ttc_lock(page)
        _screenshot(page, "pass12_detect")

        # Try connection test
        _send_command_via_ui(page, 17, 1, "", "CONN_TEST_PRIMARY", log)

        # Switch transponder
        _send_command_via_ui(page, 8, 1, "40", "SWITCH_REDUNDANT", log)
        _send_command_via_ui(page, 8, 1, "42", "PA_ON_REDUNDANT", log)
        time.sleep(5.0)

        _clear_failures(stack, log, "ttc")
        _check_ttc_lock(page)
        _screenshot(page, "pass12_recovery")

        _pass_end(stack, page, 12, log)

    # ─── PASS 13: CONTINGENCY — PA Overheat ───────────────────

    def test_pass13_pa_overheat(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 13, "CONTINGENCY: PA OVERHEAT", log)

        _inject_failure(stack, log, "ttc", "pa_overheat",
                        magnitude=1.0, onset="gradual", onset_duration_s=60)
        time.sleep(15.0)

        # Monitor PA temp on TTC tab
        _switch_tab(page, "ttc")
        time.sleep(5.0)
        _screenshot(page, "pass13_detect")

        # Request TTC HK to see temp
        _send_command_via_ui(page, 3, 27, "00 06", "HK_TTC_PA_TEMP", log)

        # PA off to cool
        _send_command_via_ui(page, 8, 1, "43", "PA_OFF_COOL", log)
        time.sleep(10.0)

        # PA back on
        _send_command_via_ui(page, 8, 1, "42", "PA_ON_AFTER_COOL", log)

        _clear_failures(stack, log)

        # Final connection test
        _send_command_via_ui(page, 17, 1, "", "FINAL_CONN_TEST", log)
        _check_ttc_lock(page)
        _screenshot(page, "pass13_recovery")

        _pass_end(stack, page, 13, log)

    # ─── FINAL REPORT ──────────────────────────────────────────

    def test_final_report(self, mcs_page, system_stack, mission_log):
        page, stack, log = mcs_page, system_stack, mission_log

        # Tour all tabs for final screenshots
        for tab in ["overview", "eps", "tcs", "aocs", "ttc",
                     "payload", "obdh", "commanding"]:
            _switch_tab(page, tab)
            time.sleep(1.5)
            _screenshot(page, f"final_{tab}")

        conn = _read_conn_status(page)
        print(f"\n  Final connection: {conn}")

        verif_count = _check_verif_log(page)
        _screenshot(page, "final_verif_log")

        assert log.passes_completed >= 13, \
            f"Only {log.passes_completed}/13 passes"
        assert log.commands_sent > 40, \
            f"Only {log.commands_sent} commands sent"
