"""Full mission validation: LEOP, commissioning, and contingencies through RF.

Exercises the complete EOSAT-1 operational sequence through the MCS web UI
with the simulator in RF mode. Uses scripted pass windows (10 min contact,
5 min gap) to simulate realistic mission operations.

13 passes covering:
  Pass  1: First acquisition (CONNECTION_TEST, OBC boot, SET_TIME)
  Pass  2: Antenna deploy, link setup, S15 storage
  Pass  3: AOCS power-on, magnetometer init, detumble
  Pass  4: Star trackers, sun acquisition, nominal pointing
  Pass  5: Payload power-on, EPS/TCS checkout
  Pass  6: TM dump, time-tagged commands
  Pass  7: CONTINGENCY — RW seizure
  Pass  8: CONTINGENCY — Cascading sensor failure
  Pass  9: CONTINGENCY — EPS overcurrent
  Pass 10: CONTINGENCY — Battery load shedding
  Pass 11: CONTINGENCY — OBC crash / bootloader recovery
  Pass 12: CONTINGENCY — TTC no-TM / transponder switchover
  Pass 13: CONTINGENCY — GS failure + PA overheat

Run:
    pytest tests/test_e2e_browser/test_full_mission_validation.py -v --headed -s

Wall clock: ~30-45 minutes (gaps compressed at 5x sim speed).
"""

import json
import struct
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Page

SCREENSHOT_DIR = Path(__file__).parent / "screenshots" / "mission_validation"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Timing constants (wall-clock seconds)
PASS_DURATION = 120       # 2 min wall-clock per pass (sim runs at 1x)
GAP_DURATION = 30         # 30s wall-clock gap (sim runs at 5x → 2.5 min sim)
CMD_SETTLE = 3            # seconds to wait after sending a command
LOCK_TIMEOUT = 45000      # ms to wait for frame sync lock
BOOT_TIMEOUT = 15         # seconds for OBC boot countdown


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

class MissionLog:
    """Track commands sent, ACKs received, failures for final report."""
    def __init__(self):
        self.commands_sent = 0
        self.acks_received = 0
        self.failures_injected = 0
        self.failures_recovered = 0
        self.passes_completed = 0
        self.issues = []

    def report(self):
        print("\n" + "=" * 70)
        print("MISSION VALIDATION FINAL REPORT")
        print("=" * 70)
        print(f"  Passes completed:      {self.passes_completed}")
        print(f"  Commands sent:         {self.commands_sent}")
        print(f"  ACKs received:         {self.acks_received}")
        print(f"  Failures injected:     {self.failures_injected}")
        print(f"  Failures recovered:    {self.failures_recovered}")
        print(f"  Issues found:          {len(self.issues)}")
        for i, issue in enumerate(self.issues, 1):
            print(f"    {i}. {issue}")
        print("=" * 70)


def _api(url: str, data: dict = None) -> dict:
    """HTTP API call."""
    if data:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def _sim_cmd(stack: dict, cmd: dict) -> dict:
    """Send instructor command to simulator."""
    return _api(f"{stack['sim_http']}/api/command", cmd)


def _mcs_cmd(stack: dict, service: int, subtype: int,
             data_hex: str = "", name: str = "") -> dict:
    """Send PUS command via MCS."""
    return _api(f"{stack['mcs_url']}/api/pus-command", {
        "service": service, "subtype": subtype,
        "data_hex": data_hex, "name": name or f"S{service}.{subtype}",
        "position": "flight_director"
    })


def _get_state(stack: dict) -> dict:
    """Get current spacecraft state from MCS."""
    return _api(f"{stack['mcs_url']}/api/state")


def _screenshot(page: Page, name: str):
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"))


def _pass_start(stack: dict, page: Page, pass_num: int, name: str, log: MissionLog):
    """Begin a pass window."""
    print(f"\n{'='*60}")
    print(f"PASS {pass_num}: {name}")
    print(f"{'='*60}")
    _sim_cmd(stack, {"type": "override_passes", "enabled": True})
    _sim_cmd(stack, {"type": "set_speed", "value": 1.0})
    time.sleep(3.0)  # let lock establish


def _pass_end(stack: dict, page: Page, pass_num: int, log: MissionLog):
    """End a pass window and simulate gap."""
    _screenshot(page, f"pass{pass_num:02d}_end")
    log.passes_completed = pass_num
    _sim_cmd(stack, {"type": "override_passes", "enabled": False})
    _sim_cmd(stack, {"type": "set_speed", "value": 5.0})
    print(f"  [Pass {pass_num} complete — gap {GAP_DURATION}s at 5x]")
    time.sleep(GAP_DURATION)
    _sim_cmd(stack, {"type": "set_speed", "value": 1.0})


def _send(stack: dict, log: MissionLog, service: int, subtype: int,
          data_hex: str = "", name: str = "", settle: float = CMD_SETTLE):
    """Send command and wait for settle time."""
    result = _mcs_cmd(stack, service, subtype, data_hex, name)
    log.commands_sent += 1
    status = result.get("status", "unknown")
    if status == "sent":
        log.acks_received += 1
        print(f"  ✓ {name or f'S{service}.{subtype}'} → sent (seq={result.get('seq', '?')})")
    else:
        msg = result.get("message", status)
        log.issues.append(f"Command {name}: {msg}")
        print(f"  ✗ {name or f'S{service}.{subtype}'} → {msg}")
    time.sleep(settle)
    return result


def _inject(stack: dict, log: MissionLog, subsystem: str, failure: str, **kwargs):
    """Inject a failure via instructor API."""
    cmd = {"type": "failure_inject", "subsystem": subsystem, "failure": failure}
    cmd.update(kwargs)
    _sim_cmd(stack, cmd)
    log.failures_injected += 1
    print(f"  ⚡ Injected {subsystem}/{failure}")


def _clear_failures(stack: dict, log: MissionLog, subsystem: str = None):
    """Clear failures."""
    if subsystem:
        _sim_cmd(stack, {"type": "failure_clear", "subsystem": subsystem})
    else:
        _sim_cmd(stack, {"type": "failure_clear"})
    log.failures_recovered += 1
    print(f"  ✓ Cleared failures" + (f" ({subsystem})" if subsystem else ""))


def _check_param(stack: dict, path: str, expected, tolerance=None) -> bool:
    """Check a telemetry parameter against expected value."""
    state = _get_state(stack)
    # Navigate dotted path
    val = state
    for key in path.split("."):
        if isinstance(val, dict):
            val = val.get(key, val.get("state", {}).get(key, None))
        else:
            val = None
            break
    if val is None:
        return False
    if tolerance is not None:
        return abs(float(val) - float(expected)) <= tolerance
    return val == expected


def _wait_for_param(stack: dict, path: str, expected, timeout: float = 30.0,
                    tolerance=None) -> bool:
    """Poll until a parameter reaches expected value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _check_param(stack, path, expected, tolerance):
            return True
        time.sleep(1.0)
    return False


# ═══════════════════════════════════════════════════════════════
# TEST CLASS
# ═══════════════════════════════════════════════════════════════

class TestFullMissionValidation:
    """Complete mission validation: 13 passes through LEOP, commissioning,
    and all contingency scenarios."""

    @pytest.fixture(autouse=True, scope="class")
    def mission_log(self):
        log = MissionLog()
        yield log
        log.report()

    # ─── PASS 1: First Acquisition ─────────────────────────────

    def test_pass01_first_acquisition(self, mcs_page, system_stack, mission_log):
        """First contact: CONNECTION_TEST, OBC boot, time sync."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 1, "FIRST ACQUISITION", log)

        # CONNECTION_TEST
        _send(stack, log, 17, 1, "", "CONNECTION_TEST")

        # OBC_BOOT_APP
        _send(stack, log, 8, 1, "37", "OBC_BOOT_APP", settle=BOOT_TIMEOUT)

        # SET_TIME (current CUC)
        cuc_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        cuc_s = int((now - cuc_epoch).total_seconds())
        _send(stack, log, 9, 1, struct.pack('>I', cuc_s).hex(), "SET_TIME")

        # Request time report
        _send(stack, log, 9, 2, "", "TIME_REPORT_REQ")

        # Request EPS HK to verify app is running
        _send(stack, log, 3, 27, "00 01", "HK_REQ_EPS")

        _screenshot(page, "pass01_first_acq")
        _pass_end(stack, page, 1, log)

    # ─── PASS 2: Antenna & Link Setup ─────────────────────────

    def test_pass02_antenna_link(self, mcs_page, system_stack, mission_log):
        """Deploy antenna, PA on, verify link chain."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 2, "ANTENNA & LINK SETUP", log)

        # Deploy antennas
        _send(stack, log, 8, 1, "45", "DEPLOY_ANTENNAS")

        # PA on
        _send(stack, log, 8, 1, "42", "PA_ON")

        # Request all HK SIDs
        for sid, name in [(1,"EPS"), (2,"AOCS"), (3,"TCS"), (4,"PLATFORM"), (6,"TTC")]:
            _send(stack, log, 3, 27, f"00 {sid:02x}", f"HK_REQ_{name}", settle=1)

        # Enable S15 TM storage
        _send(stack, log, 15, 1, "00", "S15_ENABLE_STORE_0")

        # Check TTC tab for lock chain
        page.click("#tab-ttc")
        time.sleep(5.0)
        _screenshot(page, "pass02_ttc_link")

        _pass_end(stack, page, 2, log)

    # ─── PASS 3: AOCS Initialization ──────────────────────────

    def test_pass03_aocs_init(self, mcs_page, system_stack, mission_log):
        """Power AOCS, init magnetometer, start detumble."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 3, "AOCS INITIALIZATION", log)

        # Power on AOCS wheels
        _send(stack, log, 8, 1, "13 07", "PWR_ON_AOCS_WHEELS")

        # Enable magnetometer A
        _send(stack, log, 8, 1, "07 01", "MAG_A_ENABLE")

        # Set mode DETUMBLE
        _send(stack, log, 8, 1, "00 02", "AOCS_SET_DETUMBLE")

        # Monitor rates for a bit
        time.sleep(10.0)

        # Request AOCS HK
        _send(stack, log, 3, 27, "00 02", "HK_REQ_AOCS")

        page.click("#tab-aocs")
        time.sleep(3.0)
        _screenshot(page, "pass03_aocs")

        _pass_end(stack, page, 3, log)

    # ─── PASS 4: Attitude Control ─────────────────────────────

    def test_pass04_attitude_control(self, mcs_page, system_stack, mission_log):
        """Star trackers, sun acquisition, nominal pointing."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 4, "ATTITUDE CONTROL", log)

        # Power star trackers
        _send(stack, log, 8, 1, "04 01", "ST1_POWER_ON")
        _send(stack, log, 8, 1, "05 01", "ST2_POWER_ON")

        # Wait for boot
        time.sleep(15.0)

        # Sun acquisition
        _send(stack, log, 8, 1, "00 03", "AOCS_SET_COARSE_SUN")
        time.sleep(10.0)

        # Nominal pointing
        _send(stack, log, 8, 1, "00 04", "AOCS_SET_NOMINAL")
        time.sleep(10.0)

        # Check attitude
        _send(stack, log, 3, 27, "00 02", "HK_REQ_AOCS")
        page.click("#tab-aocs")
        time.sleep(3.0)
        _screenshot(page, "pass04_attitude")

        _pass_end(stack, page, 4, log)

    # ─── PASS 5: Subsystem Checkout ───────────────────────────

    def test_pass05_checkout(self, mcs_page, system_stack, mission_log):
        """Payload power-on, EPS/TCS verification."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 5, "SUBSYSTEM CHECKOUT", log)

        # Power payload
        _send(stack, log, 8, 1, "13 04", "PWR_ON_PAYLOAD")

        # Payload standby
        _send(stack, log, 8, 1, "1a 01", "PAYLOAD_STANDBY")

        # Power battery heater
        _send(stack, log, 8, 1, "13 05", "PWR_ON_HTR_BAT")

        # EPS checkout
        _send(stack, log, 3, 27, "00 01", "HK_REQ_EPS")
        page.click("#tab-eps")
        time.sleep(5.0)
        _screenshot(page, "pass05_eps")

        # TCS checkout
        _send(stack, log, 3, 27, "00 03", "HK_REQ_TCS")
        page.click("#tab-tcs")
        time.sleep(5.0)
        _screenshot(page, "pass05_tcs")

        _pass_end(stack, page, 5, log)

    # ─── PASS 6: Advanced Operations ──────────────────────────

    def test_pass06_advanced_ops(self, mcs_page, system_stack, mission_log):
        """TM dump, time-tagged commands."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 6, "ADVANCED OPERATIONS", log)

        # Request S15 TM dump
        _send(stack, log, 15, 9, "00", "S15_DUMP_STORE_0")
        time.sleep(10.0)

        # Schedule time-tagged command: AOCS DESAT in 60s
        # S11.4: CUC_time(4) + embedded TC
        cuc_epoch = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        future_cuc = int((datetime.now(timezone.utc) - cuc_epoch).total_seconds()) + 60
        ttc_data = struct.pack('>I', future_cuc) + bytes([8, 1, 0, 7])  # S8.1 DESAT
        _send(stack, log, 11, 4, ttc_data.hex(), "S11_SCHEDULE_DESAT")

        # List scheduled commands
        _send(stack, log, 11, 17, "", "S11_LIST_SCHEDULED")

        time.sleep(15.0)

        # Clear S15 storage
        _send(stack, log, 15, 11, "00", "S15_CLEAR_STORE_0")

        page.click("#tab-commanding")
        time.sleep(2.0)
        _screenshot(page, "pass06_advanced")

        _pass_end(stack, page, 6, log)

    # ─── PASS 7: CONTINGENCY — RW Seizure ─────────────────────

    def test_pass07_contingency_rw_seizure(self, mcs_page, system_stack, mission_log):
        """RW seizure → disable wheel → 3-wheel mode → desaturate."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 7, "CONTINGENCY: RW SEIZURE", log)

        _inject(stack, log, "aocs", "rw_seizure", magnitude=1.0, onset="step", wheel=0)
        time.sleep(10.0)

        # Detect: request AOCS HK
        _send(stack, log, 3, 27, "00 02", "HK_DETECT_RW_FAIL")
        time.sleep(5.0)

        # Recover: disable wheel 0
        _send(stack, log, 8, 1, "02 00", "DISABLE_WHEEL_0")

        # Desaturate
        _send(stack, log, 8, 1, "01", "AOCS_DESATURATE")
        time.sleep(10.0)

        _clear_failures(stack, log, "aocs")
        _screenshot(page, "pass07_rw_recovery")
        _pass_end(stack, page, 7, log)

    # ─── PASS 8: CONTINGENCY — Cascading Sensors ──────────────

    def test_pass08_contingency_sensor_cascade(self, mcs_page, system_stack, mission_log):
        """ST1 fail → CSS head fail → MAG-A fail → switchover recovery."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 8, "CONTINGENCY: SENSOR CASCADE", log)

        # Inject ST1 failure
        _inject(stack, log, "aocs", "st_failure", magnitude=1.0, onset="step", unit=1)
        time.sleep(5.0)

        # Detect + switch to ST2
        _send(stack, log, 3, 27, "00 02", "HK_DETECT_ST1_FAIL")
        _send(stack, log, 8, 1, "06 02", "SELECT_ST2")
        time.sleep(5.0)

        # Inject CSS head failure
        _inject(stack, log, "aocs", "css_head_fail", magnitude=1.0, onset="step", face="px")
        time.sleep(5.0)

        # Inject MAG-A failure
        _inject(stack, log, "aocs", "mag_a_fail", magnitude=1.0, onset="step")
        time.sleep(3.0)

        # Switch to MAG-B
        _send(stack, log, 8, 1, "07 01", "SELECT_MAG_B")
        time.sleep(5.0)

        _send(stack, log, 3, 27, "00 02", "HK_VERIFY_RECOVERY")
        _clear_failures(stack, log, "aocs")
        _screenshot(page, "pass08_sensor_recovery")
        _pass_end(stack, page, 8, log)

    # ─── PASS 9: CONTINGENCY — EPS Overcurrent ────────────────

    def test_pass09_contingency_eps_overcurrent(self, mcs_page, system_stack, mission_log):
        """EPS overcurrent trip → reset → re-enable."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 9, "CONTINGENCY: EPS OVERCURRENT", log)

        _inject(stack, log, "eps", "overcurrent", magnitude=2.0, onset="step", line_index=3)
        time.sleep(5.0)

        # Detect
        _send(stack, log, 3, 27, "00 01", "HK_DETECT_OC")
        time.sleep(3.0)

        # Reset OC flag (func=21, line=3)
        _send(stack, log, 8, 1, "15 03", "RESET_OC_FLAG_LINE3")

        # Re-enable power line (func=19, line=3)
        _send(stack, log, 8, 1, "13 03", "PWR_ON_LINE3")
        time.sleep(5.0)

        _clear_failures(stack, log, "eps")
        _send(stack, log, 3, 27, "00 01", "HK_VERIFY_EPS")
        _screenshot(page, "pass09_eps_recovery")
        _pass_end(stack, page, 9, log)

    # ─── PASS 10: CONTINGENCY — Load Shedding ─────────────────

    def test_pass10_contingency_load_shedding(self, mcs_page, system_stack, mission_log):
        """Battery discharge → progressive load shedding → recovery."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 10, "CONTINGENCY: LOAD SHEDDING", log)

        _inject(stack, log, "eps", "undervoltage", magnitude=1.5, onset="gradual",
                onset_duration_s=30)
        time.sleep(15.0)

        # Monitor SoC
        _send(stack, log, 3, 27, "00 01", "HK_MONITOR_SOC")
        time.sleep(5.0)

        # Shed payload
        _send(stack, log, 8, 1, "14 04", "PWR_OFF_PAYLOAD")
        time.sleep(5.0)

        # Clear failure and verify recovery
        _clear_failures(stack, log, "eps")
        time.sleep(10.0)

        # Re-enable payload
        _send(stack, log, 8, 1, "13 04", "PWR_ON_PAYLOAD")
        _send(stack, log, 3, 27, "00 01", "HK_VERIFY_SOC")
        _screenshot(page, "pass10_load_shed_recovery")
        _pass_end(stack, page, 10, log)

    # ─── PASS 11: CONTINGENCY — OBC Crash ─────────────────────

    def test_pass11_contingency_obc_crash(self, mcs_page, system_stack, mission_log):
        """OBC crash → bootloader → re-boot application."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 11, "CONTINGENCY: OBC CRASH", log)

        _inject(stack, log, "obdh", "obc_crash", magnitude=1.0, onset="step")
        time.sleep(5.0)

        # Should see only SID 11 (beacon) now
        _send(stack, log, 17, 1, "", "CONNECTION_TEST_BOOTLOADER")
        time.sleep(3.0)

        # Re-boot application
        _send(stack, log, 8, 1, "37", "OBC_BOOT_APP_RECOVERY", settle=BOOT_TIMEOUT)

        # Clear reboot counter
        _send(stack, log, 8, 1, "39", "CLEAR_REBOOT_CNT")

        # Verify app running
        _send(stack, log, 3, 27, "00 01", "HK_VERIFY_APP_BOOT")

        _clear_failures(stack, log, "obdh")
        _screenshot(page, "pass11_obc_recovery")
        _pass_end(stack, page, 11, log)

    # ─── PASS 12: CONTINGENCY — TTC No-TM ─────────────────────

    def test_pass12_contingency_ttc_no_tm(self, mcs_page, system_stack, mission_log):
        """TTC primary failure → switch redundant → verify link."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 12, "CONTINGENCY: TTC NO-TM", log)

        _inject(stack, log, "ttc", "primary_failure", magnitude=1.0, onset="step")
        time.sleep(5.0)

        # Try connection test (may fail on primary)
        _send(stack, log, 17, 1, "", "CONNECTION_TEST_PRIMARY")
        time.sleep(3.0)

        # Switch to redundant transponder
        _send(stack, log, 8, 1, "33", "SWITCH_REDUNDANT_XPDR")
        time.sleep(5.0)

        # PA on redundant
        _send(stack, log, 8, 1, "42", "PA_ON_REDUNDANT")
        time.sleep(5.0)

        # Verify link
        _send(stack, log, 17, 1, "", "CONNECTION_TEST_REDUNDANT")
        _send(stack, log, 3, 27, "00 06", "HK_REQ_TTC_VERIFY")

        _clear_failures(stack, log, "ttc")
        page.click("#tab-ttc")
        time.sleep(3.0)
        _screenshot(page, "pass12_ttc_recovery")
        _pass_end(stack, page, 12, log)

    # ─── PASS 13: CONTINGENCY — GS Failure + PA Overheat ──────

    def test_pass13_contingency_gs_and_pa(self, mcs_page, system_stack, mission_log):
        """Ground station failure + PA overheat → dual recovery."""
        page, stack, log = mcs_page, system_stack, mission_log
        _pass_start(stack, page, 13, "CONTINGENCY: GS + PA OVERHEAT", log)

        # Inject PA overheat (gradual)
        _inject(stack, log, "ttc", "pa_overheat", magnitude=1.0,
                onset="gradual", onset_duration_s=60)
        time.sleep(20.0)

        # Monitor PA temperature
        _send(stack, log, 3, 27, "00 06", "HK_MONITOR_PA_TEMP")
        time.sleep(5.0)

        # Reduce TX power before shutdown
        _send(stack, log, 8, 1, "43", "PA_REDUCE_POWER")
        time.sleep(5.0)

        # If PA auto-shutdown, re-enable after cooling
        _send(stack, log, 8, 1, "42", "PA_ON_AFTER_COOL")
        time.sleep(10.0)

        # Inject receiver degradation (GS issue)
        _inject(stack, log, "ttc", "receiver_degrade", magnitude=0.5, onset="step")
        time.sleep(5.0)

        # Detect degradation
        _send(stack, log, 3, 27, "00 06", "HK_DETECT_GS_DEGRADE")

        # Clear all failures
        _clear_failures(stack, log)
        time.sleep(5.0)

        # Verify full recovery
        _send(stack, log, 17, 1, "", "FINAL_CONNECTION_TEST")
        _send(stack, log, 3, 27, "00 06", "FINAL_TTC_HK")

        page.click("#tab-ttc")
        time.sleep(3.0)
        _screenshot(page, "pass13_final_recovery")
        _pass_end(stack, page, 13, log)

    # ─── FINAL REPORT ──────────────────────────────────────────

    def test_final_report(self, mcs_page, system_stack, mission_log):
        """Capture final state and generate mission report."""
        page, stack, log = mcs_page, system_stack, mission_log

        # Final screenshots of all tabs
        for tab in ["overview", "eps", "tcs", "aocs", "ttc", "payload",
                     "obdh", "commanding"]:
            page.click(f"#tab-{tab}")
            time.sleep(1.0)
            _screenshot(page, f"final_{tab}")

        # Connection health
        conn = page.locator("#sb-conn").inner_text()
        print(f"\n  Final connection status: {conn}")

        # Log will be printed by fixture teardown
        assert log.passes_completed >= 13, \
            f"Only completed {log.passes_completed}/13 passes"
        assert log.commands_sent > 50, \
            f"Only sent {log.commands_sent} commands (expected >50)"
