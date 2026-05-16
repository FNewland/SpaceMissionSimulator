"""Phase 1 Acceptance Tests: S8.1 Command Verification through RF Pipeline.

Every command is sent as a real TC packet, processed by the engine,
and the response (S1.1 ACK, S1.7 completion) flows through the full
RF signal processing chain (TX → Channel → RX) before being verified.

Test ordering matters: destructive commands (emergency_load_shed,
obc_switch_unit) are placed last in their groups and restore state
afterward, so subsequent subsystem tests aren't affected.

Order: AOCS → Payload → TCS → TTC → EPS (load shed last) →
       OBDH (switch_unit last) → Connection Test

Runtime: ~3-5 minutes (real RF processing per command).
Ref: EOSAT1-TP-ATP-001 §4
"""

import struct
import pytest


# ═══════════════════════════════════════════════════════════════
# 1. AOCS COMMANDS (func_id 0–15) — no destructive side effects
# ═══════════════════════════════════════════════════════════════

class TestAOCSCommandsRF:

    def test_set_mode_off(self, harness):
        r = harness.send_s8(0, bytes([0]), "AOCS_MODE_OFF")
        assert r["ack_11"], "No S1.1 for AOCS mode OFF"

    def test_set_mode_detumble(self, harness):
        r = harness.send_s8(0, bytes([2]), "AOCS_MODE_DETUMBLE")
        assert r["ack_11"]

    def test_set_mode_coarse_sun(self, harness):
        r = harness.send_s8(0, bytes([3]), "AOCS_MODE_COARSE_SUN")
        assert r["ack_11"]

    def test_set_mode_nominal(self, harness):
        r = harness.send_s8(0, bytes([4]), "AOCS_MODE_NOMINAL")
        assert r["ack_11"]

    def test_set_mode_fine_point(self, harness):
        r = harness.send_s8(0, bytes([5]), "AOCS_MODE_FINE_POINT")
        assert r["ack_11"]

    def test_set_mode_desat(self, harness):
        r = harness.send_s8(0, bytes([7]), "AOCS_MODE_DESAT")
        assert r["ack_11"]

    def test_desaturate(self, harness):
        r = harness.send_s8(1, name="AOCS_DESATURATE")
        assert r["ack_11"]

    def test_disable_wheel_0(self, harness):
        r = harness.send_s8(2, bytes([0]), "DISABLE_WHEEL_0")
        assert r["ack_11"]

    def test_enable_wheel_0(self, harness):
        r = harness.send_s8(3, bytes([0]), "ENABLE_WHEEL_0")
        assert r["ack_11"]

    def test_st1_power_on(self, harness):
        r = harness.send_s8(4, bytes([1]), "ST1_POWER_ON")
        assert r["ack_11"]

    def test_st2_power_on(self, harness):
        r = harness.send_s8(5, bytes([1]), "ST2_POWER_ON")
        assert r["ack_11"]

    def test_st_select_2(self, harness):
        r = harness.send_s8(6, bytes([2]), "ST_SELECT_2")
        assert r["ack_11"]

    def test_mag_select(self, harness):
        r = harness.send_s8(7, bytes([1]), "MAG_SELECT")
        assert r["ack_11"]

    def test_mtq_enable(self, harness):
        r = harness.send_s8(9, bytes([1]), "MTQ_ENABLE")
        assert r["ack_11"]

    def test_gyro_calibration(self, harness):
        r = harness.send_s8(13, name="GYRO_CALIBRATION")
        assert r["ack_11"]

    def test_set_deadband(self, harness):
        r = harness.send_s8(15, struct.pack('>f', 0.5), "SET_DEADBAND_0.5")
        assert r["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(2)
        assert hk is not None, "No AOCS HK recovered through RF"


# ═══════════════════════════════════════════════════════════════
# 2. PAYLOAD COMMANDS (func_id 26–39) — before EPS load shed
# ═══════════════════════════════════════════════════════════════

class TestPayloadCommandsRF:

    def test_mode_standby(self, harness):
        r = harness.send_s8(26, bytes([1]), "PAYLOAD_STANDBY")
        assert r["ack_11"]

    def test_mode_imaging(self, harness):
        r = harness.send_s8(26, bytes([2]), "PAYLOAD_IMAGING")
        assert r["ack_11"]

    def test_set_band_config(self, harness):
        r = harness.send_s8(33, bytes([0x0F]), "SET_BAND_ALL")
        assert r["ack_11"]

    def test_set_detector_gain(self, harness):
        r = harness.send_s8(35, struct.pack('>f', 2.0), "SET_GAIN_2.0")
        assert r["ack_11"]

    def test_set_cooler_setpoint(self, harness):
        r = harness.send_s8(36, struct.pack('>f', -30.0), "SET_COOLER_-30C")
        assert r["ack_11"]

    def test_start_calibration(self, harness):
        r = harness.send_s8(37, name="START_CALIBRATION")
        assert r["ack_11"]

    def test_stop_calibration(self, harness):
        r = harness.send_s8(38, name="STOP_CALIBRATION")
        assert r["ack_11"]

    def test_set_compression(self, harness):
        r = harness.send_s8(39, struct.pack('>f', 4.0), "SET_COMPRESSION_4")
        assert r["ack_11"]

    def test_capture_image(self, harness):
        r = harness.send_s8(28, struct.pack('>ff', 45.0, 10.0), "CAPTURE_45_10")
        assert r["ack_11"]

    def test_download_image(self, harness):
        r = harness.send_s8(29, struct.pack('>H', 0), "DOWNLOAD_IMG_0")
        assert r["ack_11"]

    def test_delete_image(self, harness):
        r = harness.send_s8(30, struct.pack('>H', 0), "DELETE_IMG_0")
        assert r["ack_11"]

    def test_get_catalog(self, harness):
        r = harness.send_s8(32, name="GET_IMAGE_CATALOG")
        assert r["ack_11"]

    def test_set_integration_time(self, harness):
        r = harness.send_s8(34, struct.pack('>ffff', 0.01, 0.02, 0.015, 0.025),
                            "SET_INT_TIMES")
        assert r["ack_11"]

    def test_mark_bad_segment(self, harness):
        r = harness.send_s8(31, bytes([0]), "MARK_BAD_SEG_0")
        assert r["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(5)
        assert hk is not None, "No Payload HK recovered through RF"


# ═══════════════════════════════════════════════════════════════
# 3. TCS COMMANDS (func_id 40–49) — no power gating issues
# ═══════════════════════════════════════════════════════════════

class TestTCSCommandsRF:

    def test_heater_bat_on(self, harness):
        r = harness.send_s8(40, bytes([1]), "HTR_BAT_ON")
        assert r["ack_11"]

    def test_heater_bat_off(self, harness):
        r = harness.send_s8(40, bytes([0]), "HTR_BAT_OFF")
        assert r["ack_11"]

    def test_heater_obc_on(self, harness):
        r = harness.send_s8(41, bytes([1]), "HTR_OBC_ON")
        assert r["ack_11"]

    def test_fpa_cooler(self, harness):
        r = harness.send_s8(43, bytes([1]), "FPA_COOLER_ON")
        assert r["ack_11"]

    def test_set_setpoint(self, harness):
        data = bytes([1]) + struct.pack('>ff', 5.0, 10.0)
        r = harness.send_s8(44, data, "SET_SETPOINT_BAT")
        assert r["ack_11"]

    def test_auto_mode(self, harness):
        r = harness.send_s8(45, bytes([1]), "AUTO_MODE_BAT")
        assert r["ack_11"]

    def test_duty_limit(self, harness):
        r = harness.send_s8(46, bytes([1, 80]), "DUTY_LIMIT_80")
        assert r["ack_11"]

    def test_decontamination_start(self, harness):
        r = harness.send_s8(47, struct.pack('>f', 50.0), "DECON_START_50C")
        assert r["ack_11"]

    def test_decontamination_stop(self, harness):
        r = harness.send_s8(48, name="DECON_STOP")
        assert r["ack_11"]

    def test_thermal_map(self, harness):
        r = harness.send_s8(49, name="GET_THERMAL_MAP")
        assert r["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(3)
        assert hk is not None, "No TCS HK recovered through RF"


# ═══════════════════════════════════════════════════════════════
# 4. TTC COMMANDS (func_id 63–78) — before OBDH reboot
# ═══════════════════════════════════════════════════════════════

class TestTTCCommandsRF:

    def test_switch_primary(self, harness):
        r = harness.send_s8(63, name="SWITCH_PRIMARY")
        assert r["ack_11"]

    def test_switch_redundant(self, harness):
        r = harness.send_s8(64, name="SWITCH_REDUNDANT")
        assert r["ack_11"]
        # Switch back to primary
        harness.send_s8(63, name="SWITCH_PRIMARY_RESTORE")

    def test_pa_on(self, harness):
        r = harness.send_s8(66, name="PA_ON")
        assert r["ack_11"]

    def test_pa_off(self, harness):
        r = harness.send_s8(67, name="PA_OFF")
        assert r["ack_11"]
        harness.send_s8(66, name="PA_ON_RESTORE")

    def test_deploy_antennas(self, harness):
        r = harness.send_s8(69, name="DEPLOY_ANTENNAS")
        assert r["ack_11"]

    def test_beacon_on(self, harness):
        r = harness.send_s8(70, bytes([1]), "BEACON_ON")
        assert r["ack_11"]

    def test_beacon_off(self, harness):
        r = harness.send_s8(70, bytes([0]), "BEACON_OFF")
        assert r["ack_11"]

    def test_cmd_channel(self, harness):
        r = harness.send_s8(71, name="CMD_CHANNEL_START")
        assert r["ack_11"]

    def test_modulation_bpsk(self, harness):
        r = harness.send_s8(74, bytes([0]), "MOD_BPSK")
        assert r["ack_11"]

    def test_modulation_qpsk(self, harness):
        r = harness.send_s8(74, bytes([1]), "MOD_QPSK")
        assert r["ack_11"]
        harness.send_s8(74, bytes([0]), "MOD_BPSK_RESTORE")

    def test_ranging_start(self, harness):
        r = harness.send_s8(76, name="RANGING_START")
        assert r["ack_11"]

    def test_ranging_stop(self, harness):
        r = harness.send_s8(77, name="RANGING_STOP")
        assert r["ack_11"]

    def test_coherent_mode(self, harness):
        r = harness.send_s8(78, bytes([1]), "COHERENT_ON")
        assert r["ack_11"]

    def test_set_tm_rate(self, harness):
        r = harness.send_s8(65, struct.pack('>I', 64000), "SET_TM_RATE_64k")
        assert r["ack_11"]

    def test_set_tx_power(self, harness):
        r = harness.send_s8(68, struct.pack('>f', 1.5), "SET_TX_PWR_1.5W")
        assert r["ack_11"]

    def test_set_rx_gain(self, harness):
        r = harness.send_s8(75, struct.pack('>f', -80.0), "SET_RX_GAIN_-80dB")
        assert r["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(6)
        assert hk is not None, "No TTC HK recovered through RF"


# ═══════════════════════════════════════════════════════════════
# 5. EPS COMMANDS (func_id 16–25, 81–82)
#    emergency_load_shed LAST, with state restore
# ═══════════════════════════════════════════════════════════════

class TestEPSCommandsRF:

    def test_power_on_aocs(self, harness):
        r = harness.send_s8(19, bytes([7]), "PWR_ON_AOCS_WHEELS")
        assert r["ack_11"]

    def test_power_off_aocs(self, harness):
        r = harness.send_s8(20, bytes([7]), "PWR_OFF_AOCS_WHEELS")
        assert r["ack_11"]
        harness.send_s8(19, bytes([7]), "PWR_ON_AOCS_RESTORE")

    def test_power_on_payload(self, harness):
        r = harness.send_s8(19, bytes([4]), "PWR_ON_PAYLOAD")
        assert r["ack_11"]

    def test_power_on_htr_bat(self, harness):
        r = harness.send_s8(19, bytes([5]), "PWR_ON_HTR_BAT")
        assert r["ack_11"]

    def test_power_on_ttc_tx(self, harness):
        r = harness.send_s8(19, bytes([3]), "PWR_ON_TTC_TX")
        assert r["ack_11"]

    def test_reset_oc_flag(self, harness):
        r = harness.send_s8(21, bytes([3]), "RESET_OC_FLAG_3")
        assert r["ack_11"]

    def test_deploy_wing(self, harness):
        r = harness.send_s8(81, bytes([2]), "DEPLOY_WING_BOTH")
        assert r["ack_11"]

    def test_set_charge_rate(self, harness):
        r = harness.send_s8(23, struct.pack('>f', 2.0), "SET_CHARGE_RATE_2A")
        assert r["ack_11"]

    def test_set_payload_mode(self, harness):
        r = harness.send_s8(16, bytes([0]), "PAYLOAD_MODE_OFF")
        assert r["ack_11"]

    def test_fpa_cooler(self, harness):
        r = harness.send_s8(17, bytes([1]), "FPA_COOLER_ON")
        assert r["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(1)
        assert hk is not None, "No EPS HK recovered through RF"

    def test_zzz_emergency_load_shed_and_restore(self, harness):
        """DESTRUCTIVE: emergency load shed — runs last, restores state."""
        r = harness.send_s8(25, bytes([1]), "EMERGENCY_LOAD_SHED_1")
        assert r["ack_11"]
        # Restore: re-enable payload line (shed by stage 1)
        harness.send_s8(19, bytes([4]), "RESTORE_PAYLOAD_LINE")


# ═══════════════════════════════════════════════════════════════
# 6. OBDH COMMANDS (func_id 50–62, 80)
#    obc_switch_unit LAST, with app re-boot
# ═══════════════════════════════════════════════════════════════

class TestOBDHCommandsRF:

    def test_set_mode(self, harness):
        r = harness.send_s8(50, bytes([1]), "OBC_MODE_NOMINAL")
        assert r["ack_11"]

    def test_memory_scrub(self, harness):
        r = harness.send_s8(51, name="MEM_SCRUB")
        assert r["ack_11"]

    def test_select_bus_a(self, harness):
        r = harness.send_s8(54, bytes([0]), "SELECT_BUS_A")
        assert r["ack_11"]

    def test_boot_inhibit(self, harness):
        r = harness.send_s8(56, bytes([1]), "BOOT_INHIBIT_ON")
        assert r["ack_11"]
        harness.send_s8(56, bytes([0]), "BOOT_INHIBIT_OFF")

    def test_clear_reboot_cnt(self, harness):
        r = harness.send_s8(57, name="CLEAR_REBOOT_CNT")
        assert r["ack_11"]

    def test_diagnostic(self, harness):
        r = harness.send_s8(61, name="OBC_DIAGNOSTIC")
        assert r["ack_11"]

    def test_error_log(self, harness):
        r = harness.send_s8(62, name="OBC_ERROR_LOG")
        assert r["ack_11"]

    def test_gps_time_sync(self, harness):
        r = harness.send_s8(80, name="GPS_TIME_SYNC")
        assert r["ack_11"]

    def test_watchdog_sequence(self, harness):
        """Watchdog: set period → enable → disable (as a unit to avoid reboot)."""
        r1 = harness.send_s8(58, struct.pack('>H', 5000), "SET_WD_5000")
        assert r1["ack_11"]
        r2 = harness.send_s8(59, name="WD_ENABLE")
        assert r2["ack_11"]
        # Immediately disable so the watchdog doesn't fire in subsequent ticks
        r3 = harness.send_s8(60, name="WD_DISABLE")
        assert r3["ack_11"]

    def test_verify_hk(self, harness):
        hk = harness.get_hk(4)
        assert hk is not None, "No Platform HK recovered through RF"

    def test_zzz_switch_unit_and_restore(self, harness):
        """DESTRUCTIVE: OBC switchover triggers reboot to bootloader.
        Runs last, then re-boots application."""
        r = harness.send_s8(53, name="OBC_SWITCH_UNIT")
        assert r["ack_11"]
        # OBC reboots into bootloader — re-boot app (func=55 is bootloader-allowed)
        harness.send_s8(55, name="OBC_BOOT_APP_RESTORE", ticks=15, wait_s=3.0)


# ═══════════════════════════════════════════════════════════════
# 7. CONNECTION TEST + BEACON HK — safe, runs last
# ═══════════════════════════════════════════════════════════════

class TestConnectionTestRF:

    def test_s17_connection_test(self, harness):
        """S17.1 → S1.1 + S17.2 through RF pipeline."""
        r = harness.send_command(17, 1, name="CONNECTION_TEST")
        assert r["ack_11"], "No S1.1 for CONNECTION_TEST"
        s17_2 = any(p.secondary.service == 17 and p.secondary.subtype == 2
                     for p in r["responses"])
        assert s17_2, "No S17.2 connection test report"

    def test_beacon_hk_through_rf(self, harness):
        hk = harness.get_hk(11)
        assert hk is not None, "No Beacon HK recovered through RF"
