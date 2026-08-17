# EOSAT-1 Command Reference

**Document ID:** EOSAT1-UM-REF-010  
**Issue:** 2.0  
**Source revision:** `tc_catalog.yaml@d817c9a0a3c9`  
**Classification:** UNCLASSIFIED — For Simulation Use Only

> **GENERATED FILE — DO NOT EDIT BY HAND.**  
> Produced by `tools/gen_command_reference.py` from `configs/eosat1/commands/tc_catalog.yaml`, which is the single source of wire truth for every telecommand argument. Every example hex string below is computed by the same encoder the ground segment uses (`smo_common.commanding.encode`), so the manual and the spacecraft cannot disagree. Re-run the tool after any catalogue change.

---

## 1. Reading this document

**Offsets** are octets within the TC **data field**. For S8.1 (Function Management) octet 0 is the `func_id` byte, so the first argument sits at offset 1 — exactly how the on-board dispatcher reads it (`func_id = data[0]`, arguments from `data[1:]`).

**Byte order** is big-endian everywhere, declared per field rather than assumed.

**Engineering vs raw.** Uplink arguments are fixed-point integers with a declared scale and offset; the operator types the engineering value and the ground segment computes the raw field. `eng = raw x 0.01 - 273.15 (cK/LSB)` means the wire carries centi-kelvin: a setpoint of -15.00 degC is raw 25815 = `0x64D7`. **No negative engineering value ever produces a two's-complement raw** on this spacecraft's temperature arguments.

**Rejection, not clamping.** A value outside the declared range is refused with TM(1,8) execution failure (commonly error `0x0003`), never silently clamped. The MCS command builder shows the range inline and flags a bad value before SEND.

## 2. Command summary

| Command | Service | func_id | Bytes | Arguments | Criticality |
|---|---|---|---|---|---|
| `S2_AOCS_GPS_RECEIVER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_GYROSCOPE_0` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_GYROSCOPE_1` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_GYROSCOPE_2` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_MAGNETOMETER_A` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_MAGNETOMETER_B` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_MAGNETORQUER_X` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_MAGNETORQUER_Y` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_MAGNETORQUER_Z` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_REACTION_WHEEL_0` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_REACTION_WHEEL_1` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_REACTION_WHEEL_2` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_REACTION_WHEEL_3` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_STAR_TRACKER_1` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_STAR_TRACKER_2` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_AOCS_SUN_SENSOR_ARRAY` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_BATTERY_HEATER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_CHARGE_REGULATOR` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_PDU_BUS1` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_PDU_BUS2` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_PDU_BUS3` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_EPS_SOLAR_ARRAY_DRIVE` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_OBDH_CAN_BUS` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_OBDH_MASS_MEMORY` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_OBDH_OBC_A` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_OBDH_OBC_B` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_OBDH_WATCHDOG_TIMER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_PAYLOAD_CALIBRATION_LAMP` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_PAYLOAD_COMPRESSION` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_PAYLOAD_COOLER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_PAYLOAD_FPA` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_PAYLOAD_SHUTTER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_DECONTAM_HEATER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_1` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_10` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_2` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_3` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_4` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_5` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_6` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_7` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_8` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_HEATER_ZONE_9` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TCS_THERMISTOR_ARRAY` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TTC_ANTENNA_DRIVE` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TTC_LNA` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TTC_POWER_AMPLIFIER` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TTC_TRANSPONDER_A` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_TTC_TRANSPONDER_B` | S2.1 | — | 3 | device_id, state | nominal |
| `S2_REPORT_STATUS` | S2.6 | — | 2 | device_id | nominal |
| `HK_ENABLE` | S3.5 | — | 2 | sid | nominal |
| `HK_DISABLE` | S3.6 | — | 2 | sid | nominal |
| `HK_REQUEST` | S3.27 | — | 2 | sid | nominal |
| `HK_SET_INTERVAL` | S3.31 | — | 6 | sid, interval_s | nominal |
| `EVENT_ENABLE` | S5.5 | — | 2 | event_id | nominal |
| `EVENT_DISABLE` | S5.6 | — | 2 | event_id | nominal |
| `AOCS_SET_TORQUE_GAINS_ALL` | S6.2 | — | 16 | address, gain_x, gain_y, gain_z | nominal |
| `AOCS_SET_TORQUE_GAIN_X` | S6.2 | — | 8 | address, gain | nominal |
| `AOCS_SET_TORQUE_GAIN_Y` | S6.2 | — | 8 | address, gain | nominal |
| `AOCS_SET_TORQUE_GAIN_Z` | S6.2 | — | 8 | address, gain | nominal |
| `MEM_LOAD` | S6.2 | — | var | memory_id, address, data | nominal |
| `AOCS_DUMP_TORQUE_GAINS` | S6.5 | — | 6 | address, length | nominal |
| `MEM_DUMP` | S6.5 | — | 7 | memory_id, address, length | nominal |
| `MEM_CHECK` | S6.9 | — | 7 | memory_id, address, length | nominal |
| `AOCS_SET_MODE` | S8.1 | 0 (0x00) | 2 | mode | nominal |
| `AOCS_DESATURATE` | S8.1 | 1 (0x01) | 1 | — | nominal |
| `AOCS_DISABLE_WHEEL` | S8.1 | 2 (0x02) | 2 | wheel_idx | nominal |
| `AOCS_ENABLE_WHEEL` | S8.1 | 3 (0x03) | 2 | wheel_idx | nominal |
| `ST1_POWER` | S8.1 | 4 (0x04) | 2 | on | nominal |
| `ST2_POWER` | S8.1 | 5 (0x05) | 2 | on | nominal |
| `ST_SELECT` | S8.1 | 6 (0x06) | 2 | unit | nominal |
| `MAG_SELECT` | S8.1 | 7 (0x07) | 2 | unit | nominal |
| `RW_SET_SPEED_BIAS` | S8.1 | 8 (0x08) | 6 | wheel_idx, bias_rpm | nominal |
| `MTQ_ENABLE` | S8.1 | 9 (0x09) | 2 | enable | nominal |
| `AOCS_SLEW_TO` | S8.1 | 10 (0x0A) | 21 | q_x, q_y, q_z, q_w, rate_dps | nominal |
| `AOCS_CHECK_MOMENTUM` | S8.1 | 11 (0x0B) | 1 | — | nominal |
| `AOCS_BEGIN_ACQUISITION` | S8.1 | 12 (0x0C) | 1 | — | nominal |
| `AOCS_GYRO_CALIBRATION` | S8.1 | 13 (0x0D) | 1 | — | nominal |
| `AOCS_RW_RAMP_DOWN` | S8.1 | 14 (0x0E) | 6 | wheel_idx, target_rpm | nominal |
| `AOCS_SET_DEADBAND` | S8.1 | 15 (0x0F) | 5 | deadband_deg | nominal |
| `EPS_PAYLOAD_MODE` | S8.1 | 16 (0x10) | 2 | mode | nominal |
| `EPS_FPA_COOLER` | S8.1 | 17 (0x11) | 2 | on | nominal |
| `EPS_TRANSPONDER_TX` | S8.1 | 18 (0x12) | 2 | on | nominal |
| `EPS_POWER_ON` | S8.1 | 19 (0x13) | 2 | line_index | nominal |
| `EPS_POWER_OFF` | S8.1 | 20 (0x14) | 2 | line_index | nominal |
| `EPS_RESET_OC_FLAG` | S8.1 | 21 (0x15) | 2 | line_index | nominal |
| `EPS_SWITCH_LOAD` | S8.1 | 22 (0x16) | 3 | load_id, state | nominal |
| `EPS_SET_CHARGE_RATE` | S8.1 | 23 (0x17) | 5 | rate_a | nominal |
| `EPS_SOLAR_ARRAY_DRIVE` | S8.1 | 24 (0x18) | 3 | angle_deg | nominal |
| `EPS_EMERGENCY_LOAD_SHED` | S8.1 | 25 (0x19) | 2 | stage | nominal |
| `PAYLOAD_SET_MODE` | S8.1 | 26 (0x1A) | 2 | mode | nominal |
| `PAYLOAD_SET_SCENE` | S8.1 | 27 (0x1B) | 3 | scene_id | nominal |
| `PAYLOAD_CAPTURE` | S8.1 | 28 (0x1C) | 5 | scene_id, lines | caution |
| `PAYLOAD_DOWNLOAD_IMAGE` | S8.1 | 29 (0x1D) | 3 | scene_id | nominal |
| `PAYLOAD_DELETE_IMAGE` | S8.1 | 30 (0x1E) | 3 | scene_id | caution |
| `PAYLOAD_MARK_BAD_SEGMENT` | S8.1 | 31 (0x1F) | 2 | segment_id | caution |
| `PAYLOAD_GET_IMAGE_CATALOG` | S8.1 | 32 (0x20) | 1 | — | nominal |
| `PAYLOAD_SET_BAND_CONFIG` | S8.1 | 33 (0x21) | 2 | mask | nominal |
| `PAYLOAD_SET_INTEGRATION_TIME` | S8.1 | 34 (0x22) | 9 | blue_ms, green_ms, red_ms, nir_ms | nominal |
| `PAYLOAD_SET_GAIN` | S8.1 | 35 (0x23) | 3 | gain | nominal |
| `PAYLOAD_COOLER_SETPOINT` | S8.1 | 36 (0x24) | 3 | setpoint | caution |
| `PAYLOAD_START_CALIBRATION` | S8.1 | 37 (0x25) | 2 | mode | nominal |
| `PAYLOAD_STOP_CALIBRATION` | S8.1 | 38 (0x26) | 1 | — | nominal |
| `PAYLOAD_SET_COMPRESSION` | S8.1 | 39 (0x27) | 3 | algorithm, ratio | nominal |
| `HEATER_BATTERY` | S8.1 | 40 (0x28) | 2 | on | nominal |
| `HEATER_OBC` | S8.1 | 41 (0x29) | 2 | on | nominal |
| `HEATER_THRUSTER` | S8.1 | 42 (0x2A) | 2 | on | nominal |
| `FPA_COOLER` | S8.1 | 43 (0x2B) | 2 | on | nominal |
| `HEATER_SET_SETPOINT` | S8.1 | 44 (0x2C) | 10 | circuit, on_temp, off_temp | nominal |
| `HEATER_AUTO_MODE` | S8.1 | 45 (0x2D) | 2 | circuit | nominal |
| `TCS_SET_HEATER_DUTY_LIMIT` | S8.1 | 46 (0x2E) | 3 | circuit, max_duty_pct | nominal |
| `TCS_DECONTAMINATION_START` | S8.1 | 47 (0x2F) | 5 | target_temp_c | nominal |
| `TCS_DECONTAMINATION_STOP` | S8.1 | 48 (0x30) | 1 | — | nominal |
| `TCS_GET_THERMAL_MAP` | S8.1 | 49 (0x31) | 1 | — | nominal |
| `OBC_SET_MODE` | S8.1 | 50 (0x32) | 2 | mode | nominal |
| `OBC_MEMORY_SCRUB` | S8.1 | 51 (0x33) | 1 | — | nominal |
| `OBC_REBOOT` | S8.1 | 52 (0x34) | 1 | — | critical |
| `OBC_SWITCH_UNIT` | S8.1 | 53 (0x35) | 1 | — | critical |
| `OBC_SELECT_BUS` | S8.1 | 54 (0x36) | 2 | bus | caution |
| `OBC_BOOT_APP` | S8.1 | 55 (0x37) | 1 | — | caution |
| `OBC_BOOT_INHIBIT` | S8.1 | 56 (0x38) | 2 | inhibit | nominal |
| `OBC_CLEAR_REBOOT_CNT` | S8.1 | 57 (0x39) | 1 | — | nominal |
| `OBC_SET_WATCHDOG_PERIOD` | S8.1 | 58 (0x3A) | 3 | period_ticks | nominal |
| `OBC_WATCHDOG_ENABLE` | S8.1 | 59 (0x3B) | 1 | — | nominal |
| `OBC_WATCHDOG_DISABLE` | S8.1 | 60 (0x3C) | 1 | — | nominal |
| `OBC_DIAGNOSTIC` | S8.1 | 61 (0x3D) | 1 | — | nominal |
| `OBC_ERROR_LOG` | S8.1 | 62 (0x3E) | 1 | — | nominal |
| `TTC_SWITCH_PRIMARY` | S8.1 | 63 (0x3F) | 1 | — | nominal |
| `TTC_SWITCH_REDUNDANT` | S8.1 | 64 (0x40) | 1 | — | nominal |
| `TTC_SET_DATA_RATE` | S8.1 | 65 (0x41) | 2 | rate | nominal |
| `TTC_PA_ON` | S8.1 | 66 (0x42) | 1 | — | nominal |
| `TTC_PA_OFF` | S8.1 | 67 (0x43) | 1 | — | nominal |
| `TTC_SET_TX_POWER` | S8.1 | 68 (0x44) | 2 | level | caution |
| `TTC_DEPLOY_ANTENNA` | S8.1 | 69 (0x45) | 1 | — | nominal |
| `TTC_SET_BEACON_MODE` | S8.1 | 70 (0x46) | 2 | on | nominal |
| `TTC_CMD_CHANNEL_START` | S8.1 | 71 (0x47) | 1 | — | nominal |
| `TTC_SET_UL_FREQ` | S8.1 | 72 (0x48) | 5 | freq_mhz | nominal |
| `TTC_SET_DL_FREQ` | S8.1 | 73 (0x49) | 5 | freq_mhz | nominal |
| `TTC_SET_MODULATION` | S8.1 | 74 (0x4A) | 2 | mode | nominal |
| `TTC_SET_RX_GAIN` | S8.1 | 75 (0x4B) | 5 | agc_db | nominal |
| `TTC_RANGING_START` | S8.1 | 76 (0x4C) | 1 | — | nominal |
| `TTC_RANGING_STOP` | S8.1 | 77 (0x4D) | 1 | — | nominal |
| `TTC_SET_COHERENT_MODE` | S8.1 | 78 (0x4E) | 2 | on | nominal |
| `TTC_SET_DOWNLINK_MODE` | S8.1 | 79 (0x4F) | 2 | on | nominal |
| `OBC_GPS_TIME_SYNC` | S8.1 | 80 (0x50) | 1 | — | caution |
| `EPS_DEPLOY_WING` | S8.1 | 81 (0x51) | 2 | wing | nominal |
| `EPS_WING_STATUS` | S8.1 | 82 (0x52) | 1 | — | nominal |
| `EPS_SET_MODE` | S8.1 | 83 (0x53) | 2 | mode | nominal |
| `AOCS_GPS_SET_START_MODE` | S8.1 | 84 (0x54) | 2 | start_mode | nominal |
| `AOCS_SET_POINTING_FRAME` | S8.1 | 85 (0x55) | 2 | frame | nominal |
| `AOCS_SET_TARGET_QUAT` | S8.1 | 86 (0x56) | 17 | q_x, q_y, q_z, q_w | nominal |
| `AOCS_SET_TARGET_GEO` | S8.1 | 87 (0x57) | 13 | lat, lon, alt_m | nominal |
| `AOCS_SET_CONTROL_BW` | S8.1 | 88 (0x58) | 9 | omega_n, zeta | nominal |
| `AOCS_FINE_READY_CHECK` | S8.1 | 89 (0x59) | 1 | — | nominal |
| `AOCS_SET_MOM_MGMT` | S8.1 | 90 (0x5A) | 4 | enable, entry_pct, exit_pct | nominal |
| `AOCS_EST_RESET` | S8.1 | 91 (0x5B) | 2 | mode | nominal |
| `PAYLOAD_SELECT_FILTER` | S8.1 | 92 (0x5C) | 2 | position | nominal |
| `PAYLOAD_CYCLE_SHUTTER` | S8.1 | 93 (0x5D) | 2 | cycles | nominal |
| `PAYLOAD_INITIATE_TRANSFER` | S8.1 | 94 (0x5E) | 3 | scene_id | nominal |
| `PAYLOAD_CAL_LAMP` | S8.1 | 95 (0x5F) | 2 | on | nominal |
| `AOCS_SET_DESAT_INHIBIT` | S8.1 | 96 (0x60) | 2 | inhibit | nominal |
| `TTC_BEACON_TX_ON` | S8.1 | 97 (0x61) | 1 | — | normal |
| `TTC_BEACON_TX_OFF` | S8.1 | 98 (0x62) | 1 | — | caution |
| `EPS_SA_A_ON_LEGACY` | S8.1 | 100 (0x64) | 1 | — | nominal |
| `EPS_SA_A_OFF_LEGACY` | S8.1 | 101 (0x65) | 1 | — | nominal |
| `EPS_SA_B_ON_LEGACY` | S8.1 | 102 (0x66) | 1 | — | nominal |
| `EPS_SA_B_OFF_LEGACY` | S8.1 | 103 (0x67) | 1 | — | nominal |
| `TCS_HEATER_BAT_ON_LEGACY` | S8.1 | 104 (0x68) | 1 | — | nominal |
| `TCS_HEATER_BAT_OFF_LEGACY` | S8.1 | 105 (0x69) | 1 | — | nominal |
| `TCS_HEATER_OBC_ON_LEGACY` | S8.1 | 106 (0x6A) | 1 | — | nominal |
| `TCS_HEATER_OBC_OFF_LEGACY` | S8.1 | 107 (0x6B) | 1 | — | nominal |
| `PROP_ARM` | S8.1 | 108 (0x6C) | 3 | arm_code | critical |
| `PROP_DISARM` | S8.1 | 109 (0x6D) | 1 | — | nominal |
| `PROP_SET_BURN` | S8.1 | 110 (0x6E) | 11 | dv_m_s, direction, mode, t_start_cuc | critical |
| `PROP_EXECUTE` | S8.1 | 111 (0x6F) | 1 | — | nominal |
| `PROP_ABORT` | S8.1 | 112 (0x70) | 1 | — | nominal |
| `PROP_ISO_OPEN` | S8.1 | 113 (0x71) | 2 | branch | nominal |
| `PROP_ISO_CLOSE` | S8.1 | 114 (0x72) | 2 | branch | nominal |
| `PROP_SET_LIMITS` | S8.1 | 115 (0x73) | 17 | att_err_max_deg, p_min_bar, catbed_max_c, t_burn_max_s | nominal |
| `PROP_INHIBIT_SET` | S8.1 | 116 (0x74) | 2 | on | nominal |
| `PROP_INHIBIT_CLR` | S8.1 | 117 (0x75) | 3 | key | nominal |
| `PROP_GET_STATUS` | S8.1 | 118 (0x76) | 1 | — | nominal |
| `PAYLOAD_SET_SHUTTER` | S8.1 | 119 (0x77) | 2 | position | caution |
| `SET_TIME` | S9.1 | — | 4 | cuc_seconds | nominal |
| `REQUEST_TIME` | S9.2 | — | 0 | — | nominal |
| `SCHEDULE_TC` | S11.4 | — | var | exec_time_cuc, tc_data | nominal |
| `DELETE_SCHEDULED` | S11.7 | — | 2 | cmd_id | nominal |
| `DISABLE_SCHEDULE` | S11.9 | — | 0 | — | nominal |
| `DELETE_ALL_SCHEDULED` | S11.11 | — | 0 | — | caution |
| `ENABLE_SCHEDULE` | S11.13 | — | 0 | — | nominal |
| `LIST_SCHEDULE` | S11.17 | — | 0 | — | nominal |
| `MON_ENABLE` | S12.1 | — | 0 | — | nominal |
| `MON_DISABLE` | S12.2 | — | 0 | — | nominal |
| `MON_ENABLE_DEF` | S12.3 | — | 2 | mon_id | nominal |
| `MON_DISABLE_DEF` | S12.4 | — | 2 | mon_id | nominal |
| `MON_ADD_DEF` | S12.6 | — | 13 | mon_id, param_id, check_type, low_limit, high_limit | nominal |
| `MON_DELETE_DEF` | S12.7 | — | 2 | mon_id | nominal |
| `MON_REPORT_DEFS` | S12.12 | — | 0 | — | nominal |
| `S13_CREATE_TRANSFER` | S13.1 | — | 4 | scene_id, size_mb | nominal |
| `S13_DOWNLOAD_SEGMENT` | S13.3 | — | 4 | transfer_id, segment_num | nominal |
| `S13_END_TRANSFER` | S13.5 | — | 2 | transfer_id | nominal |
| `S13_REPORT_STATUS` | S13.9 | — | 2 | transfer_id | nominal |
| `ENABLE_STORE` | S15.1 | — | 1 | store_id | nominal |
| `DISABLE_STORE` | S15.2 | — | 1 | store_id | nominal |
| `DUMP_STORE` | S15.9 | — | 1 | store_id | nominal |
| `DELETE_STORE` | S15.11 | — | 1 | store_id | caution |
| `STORE_STATUS` | S15.13 | — | 0 | — | nominal |
| `CONNECTION_TEST` | S17.1 | — | 0 | — | nominal |
| `OBCP_LOAD` | S18.1 | — | var | obcp_blob | caution |
| `OBCP_DELETE` | S18.2 | — | 2 | proc_id | nominal |
| `OBCP_START` | S18.3 | — | 3 | proc_id, param | nominal |
| `OBCP_STOP` | S18.4 | — | 2 | proc_id | caution |
| `OBCP_STATUS_REPORT` | S18.6 | — | 0 | — | nominal |
| `EA_ADD` | S19.1 | — | 6 | ea_id, trigger_kind, trigger_id, action_func_id | nominal |
| `EA_DELETE` | S19.2 | — | 2 | ea_id | nominal |
| `EA_ENABLE` | S19.4 | — | 2 | ea_id | nominal |
| `EA_DISABLE` | S19.5 | — | 2 | ea_id | nominal |
| `EA_REPORT_DEFS` | S19.8 | — | 0 | — | nominal |
| `SET_PARAM` | S20.1 | — | 6 | param_id, value | nominal |
| `GET_PARAM` | S20.3 | — | 2 | param_id | nominal |

## 3. S8.1 Function Management commands

### 3.1 AOCS

#### AOCS_SET_MODE

**S8.1** · func_id **0** (0x00) · 2 data byte(s) · readback aocs.mode (0x020F)

Set AOCS mode

*Aliases:* SET_AOCS_MODE

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | enumerated | — | 4 |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `OFF` |  |
| 1 | `SAFE_BOOT` |  |
| 2 | `DETUMBLE` |  |
| 3 | `COARSE_SUN` |  |
| 4 | `NOMINAL_NADIR` |  |
| 5 | `FINE_POINT` | Refused with TM(1,8) unless aocs.fine_ready (0x02C5) == 0 |
| 6 | `SLEW` |  |
| 7 | `DESATURATION` |  |
| 8 | `ECLIPSE_PROPAGATE` |  |
| 9 | `INERTIAL` |  |

- `mode` — 0=off,1=safe_boot,2=detumble,3=coarse_sun,4=nominal_nadir,5=fine_point,6=slew,7=desaturation,8=eclipse_propagate,9=inertial. Mode 5 is refused with TM(1,8) unless the aocs.fine_ready (0x02C5) precondition bitmask is 0.

Example: `00 04`  (mode=4)

#### AOCS_DESATURATE

**S8.1** · func_id **1** (0x01) · 1 data byte(s)

Start wheel desaturation

*No arguments.*

Example: `01`

#### AOCS_DISABLE_WHEEL

**S8.1** · func_id **2** (0x02) · 2 data byte(s)

Disable specific reaction wheel

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `wheel_idx` | uint8 | 1 | — | raw = eng | — | — |

- `wheel_idx` — Wheel index (0-3)

Example: `02 00`  (wheel_idx=0)

#### AOCS_ENABLE_WHEEL

**S8.1** · func_id **3** (0x03) · 2 data byte(s)

Enable specific reaction wheel

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `wheel_idx` | uint8 | 1 | — | raw = eng | — | — |

- `wheel_idx` — Wheel index (0-3)

Example: `03 00`  (wheel_idx=0)

#### ST1_POWER

**S8.1** · func_id **4** (0x04) · 2 data byte(s)

Star tracker 1 power on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `04 00`  (on=0)

#### ST2_POWER

**S8.1** · func_id **5** (0x05) · 2 data byte(s)

Star tracker 2 power on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `05 00`  (on=0)

#### ST_SELECT

**S8.1** · func_id **6** (0x06) · 2 data byte(s)

Select primary star tracker

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `unit` | uint8 | 1 | — | raw = eng | — | — |

- `unit` — 0=ST1, 1=ST2

Example: `06 00`  (unit=0)

#### MAG_SELECT

**S8.1** · func_id **7** (0x07) · 2 data byte(s)

Select magnetometer source

*Aliases:* SET_MAG_SELECT

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `unit` | uint8 | 1 | — | raw = eng | — | — |

- `unit` — 0=primary, 1=redundant

Example: `07 00`  (unit=0)

#### RW_SET_SPEED_BIAS

**S8.1** · func_id **8** (0x08) · 6 data byte(s)

Set reaction wheel speed bias (added to the wheel's current speed)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `wheel_idx` | uint8 | 1 | — | raw = eng | — | — |
| `bias_rpm` | float32 | 2 | — | raw = eng | — | — |

- `wheel_idx` — Wheel index 0-3
- `bias_rpm` — Speed bias in RPM, IEEE-754 big-endian

Example: `08 00 00 00 00 00`  (wheel_idx=0, bias_rpm=0)

#### MTQ_ENABLE

**S8.1** · func_id **9** (0x09) · 2 data byte(s)

Enable/disable magnetorquers

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `enable` | uint8 | 1 | — | raw = eng | — | — |

- `enable` — 0=disable, 1=enable

Example: `09 00`  (enable=0)

#### AOCS_SLEW_TO

**S8.1** · func_id **10** (0x0A) · 21 data byte(s)

Execute slew maneuver to target quaternion

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `q_x` | float32 | 1 | — | raw = eng | — | — |
| `q_y` | float32 | 5 | — | raw = eng | — | — |
| `q_z` | float32 | 9 | — | raw = eng | — | — |
| `q_w` | float32 | 13 | — | raw = eng | — | — |
| `rate_dps` | float32 | 17 | — | raw = eng | — | — |

- `q_x` — Target quaternion X component
- `q_y` — Target quaternion Y component
- `q_z` — Target quaternion Z component
- `q_w` — Target quaternion W component (scalar)
- `rate_dps` — Slew rate in deg/s (0.1-10.0)

Example: `0A 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00`  (q_x=0, q_y=0, q_z=0, q_w=0, rate_dps=0)

#### AOCS_CHECK_MOMENTUM

**S8.1** · func_id **11** (0x0B) · 1 data byte(s)

Query reaction wheel momentum status

*No arguments.*

Example: `0B`

#### AOCS_BEGIN_ACQUISITION

**S8.1** · func_id **12** (0x0C) · 1 data byte(s)

Start automated attitude acquisition sequence

*No arguments.*

Example: `0C`

#### AOCS_GYRO_CALIBRATION

**S8.1** · func_id **13** (0x0D) · 1 data byte(s)

Reset gyro bias estimate

*No arguments.*

Example: `0D`

#### AOCS_RW_RAMP_DOWN

**S8.1** · func_id **14** (0x0E) · 6 data byte(s)

Gracefully reduce reaction wheel speed

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `wheel_idx` | uint8 | 1 | — | raw = eng | — | — |
| `target_rpm` | float32 | 2 | — | raw = eng | — | — |

- `wheel_idx` — Wheel index (0-3) or 255 for all
- `target_rpm` — Target wheel speed (RPM)

Example: `0E 00 00 00 00 00`  (wheel_idx=0, target_rpm=0)

#### AOCS_SET_DEADBAND

**S8.1** · func_id **15** (0x0F) · 5 data byte(s)

Set the FINE-point control deadband (a REAL control deadband since Phase C; also writes the S5 ATTITUDE_ERROR_HIGH event threshold, preserving the historical wiring)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `deadband_deg` | float32 | 1 | — | raw = eng | — | — |

- `deadband_deg` — Control deadband / event threshold (0-1 deg)

Example: `0F 00 00 00 00`  (deadband_deg=0)

#### AOCS_GPS_SET_START_MODE

**S8.1** · func_id **84** (0x54) · 2 data byte(s) · position `aocs`

Select GPS receiver start mode (cold/warm/hot). Sets the time-to-first-fix profile used after the GPS receiver is powered on.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `start_mode` | uint8 | 1 | — | enumerated | — | 0 |

*`start_mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `COLD` | 60 s TTFF |
| 1 | `WARM` | 30 s TTFF |
| 2 | `HOT` | 5 s TTFF |

- `start_mode` — 0=COLD (60s TTFF), 1=WARM (30s), 2=HOT (5s)

Example: `54 00`  (start_mode=0)

#### AOCS_SET_POINTING_FRAME

**S8.1** · func_id **85** (0x55) · 2 data byte(s) · position `aocs`

Select the AOCS reference frame for NOMINAL/FINE pointing

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `frame` | uint8 | 1 | — | enumerated | — | 1 |

*`frame` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `INERTIAL` | Hold the AOCS_SET_TARGET_QUAT attitude |
| 1 | `LVLH_NADIR` | Local-vertical nadir pointing (default) |
| 2 | `TARGET_TRACK` | Track the AOCS_SET_TARGET_GEO ground target |
| 3 | `SUN` | Sun pointing |

- `frame` — 0=INERTIAL, 1=LVLH_NADIR, 2=TARGET_TRACK, 3=SUN

Example: `55 01`  (frame=1)

#### AOCS_SET_TARGET_QUAT

**S8.1** · func_id **86** (0x56) · 17 data byte(s) · position `aocs`

Set the inertial-hold target quaternion (used by INERTIAL mode 9 and frame 0)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `q_x` | float32 | 1 | — | raw = eng | -1.0 … 1.0 | 0.0 |
| `q_y` | float32 | 5 | — | raw = eng | -1.0 … 1.0 | 0.0 |
| `q_z` | float32 | 9 | — | raw = eng | -1.0 … 1.0 | 0.0 |
| `q_w` | float32 | 13 | — | raw = eng | -1.0 … 1.0 | 1.0 |

- `q_x` — Target quaternion X
- `q_y` — Target quaternion Y
- `q_z` — Target quaternion Z
- `q_w` — Target quaternion W (scalar)

Example: `56 00 00 00 00 00 00 00 00 00 00 00 00 3F 80 00 00`  (q_x=0.0, q_y=0.0, q_z=0.0, q_w=1.0)

#### AOCS_SET_TARGET_GEO

**S8.1** · func_id **87** (0x57) · 13 data byte(s) · position `aocs`

Set a ground target for TARGET_TRACK pointing (off-nadir imaging)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `lat` | float32 | 1 | deg | raw = eng | -90.0 … 90.0 | 0.0 |
| `lon` | float32 | 5 | degE | raw = eng | -180.0 … 180.0 | 0.0 |
| `alt_m` | float32 | 9 | m | raw = eng | -500.0 … 9000.0 | 0.0 |

- `lat` — Target latitude, deg
- `lon` — Target longitude, deg east
- `alt_m` — Target altitude above the reference sphere, m

Example: `57 00 00 00 00 00 00 00 00 00 00 00 00`  (lat=0.0, lon=0.0, alt_m=0.0)

#### AOCS_SET_CONTROL_BW

**S8.1** · func_id **88** (0x58) · 9 data byte(s) · position `aocs`

Override the controller bandwidth/damping (commissioning gain tuning; 0 restores per-mode defaults)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `omega_n` | float32 | 1 | rad/s | raw = eng | 0.0 … 0.2 | 0.0 |
| `zeta` | float32 | 5 | — | raw = eng | 0.0 … 2.0 | 0.0 |

- `omega_n` — Natural frequency, rad/s (0.001-0.2; 0 = per-mode default)
- `zeta` — Damping ratio (0.1-2.0; 0 = per-mode default)

Example: `58 00 00 00 00 00 00 00 00`  (omega_n=0.0, zeta=0.0)

#### AOCS_FINE_READY_CHECK

**S8.1** · func_id **89** (0x59) · 1 data byte(s) · position `aocs` · readback aocs.fine_ready (0x02C5)

Report the FINE-entry precondition bitmask (0x02C5) as TM(8,130); 0 = ready

*No arguments.*

Example: `59`

#### AOCS_SET_MOM_MGMT

**S8.1** · func_id **90** (0x5A) · 4 data byte(s) · position `aocs`

Configure autonomous momentum management (auto-DESAT thresholds)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `enable` | uint8 | 1 | — | enumerated | — | 1 |
| `entry_pct` | uint8 | 2 | % | raw = eng | 10 … 95 | 70 |
| `exit_pct` | uint8 | 3 | % | raw = eng | 5 … 90 | 20 |

*`enable` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `DISABLE` |  |
| 1 | `ENABLE` |  |

- `enable` — 0=disable, 1=enable
- `entry_pct` — DESAT entry at this % of the momentum envelope (default 70)
- `exit_pct` — DESAT completes when the momentum envelope falls back below this % (default 20)

Example: `5A 01 46 14`  (enable=1, entry_pct=70, exit_pct=20)

#### AOCS_EST_RESET

**S8.1** · func_id **91** (0x5B) · 2 data byte(s) · position `aocs`

Reinitialise the attitude estimator (after a time jump — see gps_time_sync_recovery)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | enumerated | — | 1 |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `COLD` | TRIAD/ST bootstrap |
| 1 | `WARM` | Keep attitude, re-inflate covariance |

- `mode` — 0=cold (TRIAD/ST bootstrap), 1=warm (keep attitude, re-inflate covariance)

Example: `5B 01`  (mode=1)

### 3.2 EPS

#### EPS_PAYLOAD_MODE

**S8.1** · func_id **16** (0x10) · 2 data byte(s)

Set payload power mode

*Aliases:* SET_PAYLOAD_MODE

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | raw = eng | — | — |

- `mode` — 0=off, 1=standby, 2=imaging

Example: `10 00`  (mode=0)

#### EPS_FPA_COOLER

**S8.1** · func_id **17** (0x11) · 2 data byte(s)

Enable/disable FPA cooler

*Aliases:* SET_FPA_COOLER

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `11 00`  (on=0)

#### EPS_TRANSPONDER_TX

**S8.1** · func_id **18** (0x12) · 2 data byte(s)

Enable/disable transponder TX

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `12 00`  (on=0)

#### EPS_POWER_ON

**S8.1** · func_id **19** (0x13) · 2 data byte(s)

Switch power line ON

*Aliases:* POWER_LINE_ON

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `line_index` | uint8 | 1 | — | raw = eng | — | — |

- `line_index` — Power line index (0-8; 8 = prop)

Example: `13 00`  (line_index=0)

#### EPS_POWER_OFF

**S8.1** · func_id **20** (0x14) · 2 data byte(s)

Switch power line OFF

*Aliases:* POWER_LINE_OFF

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `line_index` | uint8 | 1 | — | raw = eng | — | — |

- `line_index` — Power line index (0-8; 8 = prop)

Example: `14 00`  (line_index=0)

#### EPS_RESET_OC_FLAG

**S8.1** · func_id **21** (0x15) · 2 data byte(s)

Reset overcurrent trip flag and re-enable line

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `line_index` | uint8 | 1 | — | raw = eng | — | — |

- `line_index` — Power line index (0-8; 8 = prop)

Example: `15 00`  (line_index=0)

#### EPS_SWITCH_LOAD

**S8.1** · func_id **22** (0x16) · 3 data byte(s)

Switch individual power load on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `load_id` | uint8 | 1 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `load_id` — Load ID (0-7)
- `state` — 0=off, 1=on

Example: `16 00 00`  (load_id=0, state=0)

#### EPS_SET_CHARGE_RATE

**S8.1** · func_id **23** (0x17) · 5 data byte(s)

Override battery charge rate

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `rate_a` | float32 | 1 | — | raw = eng | — | — |

- `rate_a` — Charge rate in Amps (0=auto)

Example: `17 00 00 00 00`  (rate_a=0)

#### EPS_SOLAR_ARRAY_DRIVE

**S8.1** · func_id **24** (0x18) · 3 data byte(s)

Set the frozen solar array drive angle. Phase B: load-bearing — with SADA tracking active the drive follows the sun automatically and this sets the park angle used if tracking stops; with tracking off the wings generate per the commanded angle (0 = legacy +Y orientation).

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `angle_deg` | int16 | 1 | — | raw = eng | — | — |

- `angle_deg` — Drive angle in CENTI-degrees; the handler divides by 100 (-9000..+9000 = -90..+90 deg)

Example: `18 00 00`  (angle_deg=0)

#### EPS_EMERGENCY_LOAD_SHED

**S8.1** · func_id **25** (0x19) · 2 data byte(s)

Immediate load shedding to specified stage

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `stage` | uint8 | 1 | — | raw = eng | — | — |

- `stage` — Shed stage (0-3)

Example: `19 00`  (stage=0)

#### EPS_SET_MODE

**S8.1** · func_id **83** (0x53) · 2 data byte(s)

Set EPS operating mode. SAFE sheds the two least-essential loads, EMERGENCY sheds all of them; loads are restored by the operator, not automatically.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | raw = eng | — | — |

- `mode` — 0=nominal, 1=safe, 2=emergency

Example: `53 00`  (mode=0)

### 3.3 Payload

#### PAYLOAD_SET_MODE

**S8.1** · func_id **26** (0x1A) · 2 data byte(s) · readback payload.mode (0x0600)

Set payload mode. Modes 1 (STANDBY), 2 (IMAGING) and 3 (CALIBRATION) all request the FPA cooler; mode 0 releases it. The request now drives BOTH the TCS cooler flag and the EPS `fpa_cooler` power line (2026-08b PL-a) — previously this path set a flag against a dark line and the detector never cooled, so FPA READY (0x0610) stayed NO forever with no error.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | enumerated | 0 … 3 | 0 |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `OFF` | Imager off, cooler released |
| 1 | `STANDBY` | FPA cooler on, detector idle |
| 2 | `IMAGING` | Detector integrating; capture enabled |
| 3 | `CALIBRATION` | Radiometric calibration; capture disabled, shutter and lamp gated by the cal sequencer |

- `mode` — 0=off, 1=standby, 2=imaging, 3=calibration

Example: `1A 00`  (mode=0)

#### PAYLOAD_SET_SCENE

**S8.1** · func_id **27** (0x1B) · 3 data byte(s) · readback payload.scene_id (0x0606)

DEPRECATED — pre-set the next scene id. Superseded by PAYLOAD_CAPTURE's own scene_id argument, which overrides this value at capture time; retained for backward compatibility only.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 1 | — | raw = eng | 0 … 65535 | 0 |


Example: `1B 00 00`  (scene_id=0)

#### PAYLOAD_CAPTURE

**S8.1** · func_id **28** (0x1C) · 5 data byte(s) · CAUTION · readback payload.last_scene_id (0x060C)

Trigger one image acquisition. The WHERE is set by the WHEN: schedule this TC with S11.4 at the target overpass. Geolocation is stamped on board from the propagated sub-satellite point at shutter open and returned in the image catalogue — it is not an argument.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 1 | — | raw = eng | 0 … 65535 | 0 |
| `lines` | uint16 | 3 | lines | raw = eng | 256 … 20000 | 2048 |

- `scene_id` — Ground-assigned product identifier, later used by PAYLOAD_DOWNLOAD_IMAGE (29) and PAYLOAD_DELETE_IMAGE (30). 0x0000 = let the payload auto-assign the next free id.
- `lines` — Along-track detector lines. Acquisition time = lines / line_rate_hz (500 Hz, subsystems/payload.yaml). Raw frame volume scales as (lines/2048) x (active_bands/4) x image_size_mb; 2048 lines x 4 bands = 4.0 MB = subsystems/payload.yaml image_size_mb.

Example: `1C 00 00 08 00`  (scene_id=0, lines=2048)

#### PAYLOAD_DOWNLOAD_IMAGE

**S8.1** · func_id **29** (0x1D) · 3 data byte(s)

Report one stored image product. 2026-08b PL-c: the handler called the model and threw the result away, so no image metadata ever reached the ground on this func. It now answers with a TM(8,2) 20-octet product summary (same layout as PAYLOAD_GET_IMAGE_CATALOG's entries), or an S1.8 refusal if the scene is not in the catalogue.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 1 | — | raw = eng | 0 … 65535 | 0 |

- `scene_id` — Scene ID to download

Example: `1D 00 00`  (scene_id=0)

#### PAYLOAD_DELETE_IMAGE

**S8.1** · func_id **30** (0x1E) · 3 data byte(s) · CAUTION

Delete specific image from payload memory

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 1 | — | raw = eng | 0 … 65535 | 0 |

- `scene_id` — Scene ID to delete

Example: `1E 00 00`  (scene_id=0)

#### PAYLOAD_MARK_BAD_SEGMENT

**S8.1** · func_id **31** (0x1F) · 2 data byte(s) · CAUTION · readback payload.mem_segments_bad (0x0612)

Mark memory segment as unusable

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `segment_id` | uint8 | 1 | — | raw = eng | 0 … 7 | 0 |

- `segment_id` — Segment index 0-7 (subsystems/payload.yaml num_memory_segments: 8; the model rejects >= 8. The old '0-79' documentation was never executable).

Example: `1F 00`  (segment_id=0)

#### PAYLOAD_GET_IMAGE_CATALOG

**S8.1** · func_id **32** (0x20) · 1 data byte(s)

Request the stored image catalogue. 2026-08b PL-c: this now RETURNS something. The handler called the model and discarded the result, so the one command whose entire purpose is to tell the ground what is on board produced no telemetry at all. TM(8,2) reply, built like PROP_GET_STATUS (func 118): shown(2) + held(2) + product_mb x100 (4) + free_mb x100 (4), followed by `shown` 20-octet product summaries of scene_id(2) lat(4, 1e-4 deg) lon(4, 1e-4 deg) size_kb(4) lines(2) quality(1, %) status(1, 0=OK/1=PARTIAL/2=CORRUPT) flags(1) segment(1). flags bit 0 = the strip was flown on a 3-wheel (degraded) FINE set, bit 1 = a product checksum has been computed. At most 32 summaries per report; `held` is the true count.

*No arguments.*

Example: `20`

#### PAYLOAD_SET_BAND_CONFIG

**S8.1** · func_id **33** (0x21) · 2 data byte(s) · readback payload.band_enable_mask (0x0625)

Set spectral band enable mask

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mask` | uint8 | 1 | — | bitfield | 1 … 15 | 15 |

*`mask` bits:*

| Bit | Name | Meaning |
|---|---|---|
| 0 | `BLUE` | 443 nm, 20 nm BW, SNR 40 dB - chlorophyll-a |
| 1 | `GREEN` | 560 nm, 20 nm BW, SNR 50 dB - turbidity |
| 2 | `RED` | 665 nm, 20 nm BW, SNR 48 dB - atmos. correction |
| 3 | `NIR` | 865 nm, 40 nm BW, SNR 42 dB - atmos. correction |
| 4 | `RESERVED` | reserved — must be 0 |
| 5 | `RESERVED` | reserved — must be 0 |
| 6 | `RESERVED` | reserved — must be 0 |
| 7 | `RESERVED` | reserved — must be 0 |

- `mask` — Each enabled band multiplies raw volume by lines x 5000 px x 2 B. Bit order matches SPECTRAL_BANDS[].bit in payload_basic.py. mask = 0x00 is REJECTED (at least one band required).

Example: `21 0F`  (mask=15)

#### PAYLOAD_SET_INTEGRATION_TIME

**S8.1** · func_id **34** (0x22) · 9 data byte(s) · readback payload.integration_ms (average) (0x0635)

Set per-band detector integration times. Fixed point, 0.01 ms/LSB (was 4x float32 = 16 argument octets; now 8). Longer integration raises band SNR ~3 dB per doubling.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `blue_ms` | uint16 | 1 | ms (raw cs(0.01ms)) | eng = raw x 0.01 (cs(0.01ms)/LSB) | 0.1 … 655.35 | 2.0 |
| `green_ms` | uint16 | 3 | ms (raw cs(0.01ms)) | eng = raw x 0.01 (cs(0.01ms)/LSB) | 0.1 … 655.35 | 2.0 |
| `red_ms` | uint16 | 5 | ms (raw cs(0.01ms)) | eng = raw x 0.01 (cs(0.01ms)/LSB) | 0.1 … 655.35 | 2.0 |
| `nir_ms` | uint16 | 7 | ms (raw cs(0.01ms)) | eng = raw x 0.01 (cs(0.01ms)/LSB) | 0.1 … 655.35 | 2.0 |

- `blue_ms` — Blue band integration time (ms)
- `green_ms` — Green band integration time (ms)
- `red_ms` — Red band integration time (ms)
- `nir_ms` — NIR band integration time (ms)

Example: `22 00 C8 00 C8 00 C8 00 C8`  (blue_ms=2.0, green_ms=2.0, red_ms=2.0, nir_ms=2.0)

#### PAYLOAD_SET_GAIN

**S8.1** · func_id **35** (0x23) · 3 data byte(s) · readback payload.gain_code (0x0636)

Set detector gain. Fixed point, 0.001x/LSB (was float32). Gain raises reported SNR ~1.5 dB per doubling until saturation.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `gain` | uint16 | 1 | x (raw milli-x) | eng = raw x 0.001 (milli-x/LSB) | 0.1 … 10.0 | 1.0 |

- `gain` — Detector gain 0.100-10.000 (raw = gain x 1000)

Example: `23 03 E8`  (gain=1.0)

#### PAYLOAD_COOLER_SETPOINT

**S8.1** · func_id **36** (0x24) · 3 data byte(s) · CAUTION · readback payload.fpa_setpoint (0x0631)

Set the FPA thermo-electric cooler regulation setpoint (drives the TCS-owned FPA node). Encoded as unsigned CENTI-KELVIN so a negative Celsius value never requires a two's-complement raw: raw = round((T_degC + 273.15) x 100). -15.00 degC -> 25815 = 0x64D7.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `setpoint` | uint16 | 1 | degC (raw cK) | eng = raw x 0.01 - 273.15 (cK/LSB) | -20.0 … 0.0 | -15.0 |

- `setpoint` — FPA regulation setpoint in degC (Peltier-class cooler envelope -20..0 degC, subsystems/payload.yaml). Outside the envelope the TC is REJECTED with S1.8 error 0x0003.

Example: `24 64 D7`  (setpoint=-15.0)

#### PAYLOAD_START_CALIBRATION

**S8.1** · func_id **37** (0x25) · 2 data byte(s) · readback payload.cal_active (0x062A)

Begin a radiometric calibration sequence (dark frame / flat field / both). PRECONDITIONS (2026-08b PL-d — the gate is on the OPTICS, not on a mode word; the old rule was "must be in IMAGING mode", which required the shutter to be open onto the scene before a dark frame could be asked for). Refused with S1.8 unless:    * payload mode is 1 STANDBY or 3 CALIBRATION — never 0 OFF and never     2 IMAGING (error 0x0024);   * the FPA is thermally ready, 0x0610 == 1 (0x0025) — dark current     roughly doubles every 6-8 K, so coefficients taken off setpoint     describe a different detector;   * DARK and BOTH: shutter CLOSED, 0x063C == 0 (0x0026) and the     calibration lamp OFF, 0x0615 == 0 (0x0027);   * FLAT: shutter OPEN and lamp ON;   * the devices involved are powered — FPA 0x0600, shutter 0x0603, and     the lamp 0x0602 for FLAT/BOTH (0x0007).  A BOTH run starts in the dark configuration and the on-board sequencer opens the shutter and lights the lamp itself at the DARK -> FLAT transition; no ground step can land between two phases 15 s apart. Changing the optics under a running sequence ABORTS it (event 0x0612, reason at 0x0645) rather than completing over invalid frames. Completion sets the per-band coefficients; 0x0646 cal_valid_mask marks the bands that now have BOTH a bias and a gain, so a DARK run completes an earlier FLAT run and vice versa.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | enumerated | — | 2 |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `DARK` | Dark frames only — shutter CLOSED, lamp OFF (produces the bias half) |
| 1 | `FLAT` | Flat field only — shutter OPEN, lamp ON (produces the gain half) |
| 2 | `BOTH` | Dark then flat; the sequencer drives the optics across the phase boundary |

- `mode` — 0=dark only, 1=flat only, 2=both (COM-011 step 2 uses DARK)

Example: `25 02`  (mode=2)

#### PAYLOAD_STOP_CALIBRATION

**S8.1** · func_id **38** (0x26) · 1 data byte(s)

Abort calibration sequence

*No arguments.*

Example: `26`

#### PAYLOAD_SET_COMPRESSION

**S8.1** · func_id **39** (0x27) · 3 data byte(s) · readback payload.compression_ratio (0x0614)

Select compression algorithm and ratio override (was a single float32 ratio; the algorithm octet was previously unreachable over the wire).

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `algorithm` | uint8 | 1 | — | enumerated | — | 3 |
| `ratio` | uint8 | 2 | x (raw deci-x) | eng = raw x 0.1 (deci-x/LSB) | 0.0 … 4.0 | 0.0 |

*`algorithm` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `NONE` | No compression (ratio 1.0) |
| 1 | `CCSDS-121` | Lossless Rice |
| 2 | `CCSDS-122` | Image DWT |
| 3 | `CCSDS-123` | Multispectral predictor (default) |

- `algorithm` — Compression algorithm
- `ratio` — Ratio override x10 (0 = AUTO scene-dependent 1.5-4.0; 10-40 = 1.0-4.0)

Example: `27 03 00`  (algorithm=3, ratio=0.0)

#### PAYLOAD_SELECT_FILTER

**S8.1** · func_id **92** (0x5C) · 2 data byte(s) · position `payload` · readback payload.filter_pos (0x063D)

Rotate the filter wheel to a position (2 s per 90 deg step)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `position` | uint8 | 1 | — | enumerated | — | 0 |

*`position` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `CLEAR` | Open position (imaging) |
| 1 | `ND2` | Neutral density 2 (solar cal) |
| 2 | `DIFFUSER` | Flat-field diffuser |
| 3 | `DARK` | Opaque (dark reference) |

- `position` — Filter wheel position 0-3

Example: `5C 00`  (position=0)

#### PAYLOAD_CYCLE_SHUTTER

**S8.1** · func_id **93** (0x5D) · 2 data byte(s) · position `payload` · readback payload.shutter_pos (0x063C)

Run a shutter mechanism EXERCISE. This is a mechanism health test, not a way to reach a position — use PAYLOAD_SET_SHUTTER (func 119) for that. 2026-08b PL-d: `cycles` counts TRANSITIONS of the blade, one per model tick, and the mechanism is driven entirely by the model. Previously the command handler toggled once AND the tick toggled once per remaining count, so cycles=1 moved the blade twice, the shutter always ended where it started, and the shutter could never be commanded closed at all.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `cycles` | uint8 | 1 | transitions | raw = eng | 1 … 100 | 1 |

- `cycles` — Number of blade transitions (1-100). An ODD count ends on the opposite position from the start; an EVEN count returns to it.

Example: `5D 01`  (cycles=1)

#### PAYLOAD_INITIATE_TRANSFER

**S8.1** · func_id **94** (0x5E) · 3 data byte(s) · position `payload` · readback payload.transfer_active (0x062D)

Start the on-board image transfer engine for a stored scene — the bridge to the S13_CREATE_TRANSFER ground workflow (textbook section 6.7). 2026-08b PL-c: raises event 0x0610 TRANSFER_START (promised by a source comment and previously emitted by nothing) and answers with a TM(8,2) report of scene_id(2) + bytes_total(4) + blocks(4) + crc32(4) — the three numbers the ground needs to plan and verify a dump, all of which used to be computed on board and then dropped. The transfer drains at the ACTIVE TM information rate (0x0506) and only while the downlink is up (0x0501), not at the old hard-coded 10 Mbps: it stalls at loss of signal and resumes on the next pass.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 1 | — | raw = eng | 1 … 65535 | 1 |

- `scene_id` — Stored scene to transfer

Example: `5E 00 01`  (scene_id=1)

#### PAYLOAD_CAL_LAMP

**S8.1** · func_id **95** (0x5F) · 2 data byte(s) · position `payload` · readback payload.cal_lamp_on (0x0615)

Calibration lamp on/off (also reachable as S2 device 0x0602)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | enumerated | — | 0 |

*`on` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `OFF` |  |
| 1 | `ON` |  |

- `on` — 0=off, 1=on

Example: `5F 00`  (on=0)

### 3.4 TCS

#### HEATER_BATTERY

**S8.1** · func_id **40** (0x28) · 2 data byte(s)

Battery heater on/off

*Aliases:* HEATER_CONTROL

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `28 00`  (on=0)

#### HEATER_OBC

**S8.1** · func_id **41** (0x29) · 2 data byte(s)

OBC heater on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `29 00`  (on=0)

#### HEATER_THRUSTER

**S8.1** · func_id **42** (0x2A) · 2 data byte(s)

Thruster heater on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `2A 00`  (on=0)

#### FPA_COOLER

**S8.1** · func_id **43** (0x2B) · 2 data byte(s)

FPA cooler on/off

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `2B 00`  (on=0)

#### HEATER_SET_SETPOINT

**S8.1** · func_id **44** (0x2C) · 10 data byte(s)

Modify heater thermostat setpoints

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `circuit` | uint8 | 1 | — | raw = eng | — | — |
| `on_temp` | float32 | 2 | — | raw = eng | — | — |
| `off_temp` | float32 | 6 | — | raw = eng | — | — |

- `circuit` — 0=battery, 1=obc, 2=thruster
- `on_temp` — Thermostat ON temperature (C)
- `off_temp` — Thermostat OFF temperature (C)

Example: `2C 00 00 00 00 00 00 00 00 00`  (circuit=0, on_temp=0, off_temp=0)

#### HEATER_AUTO_MODE

**S8.1** · func_id **45** (0x2D) · 2 data byte(s)

Return heater to autonomous thermostat control

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `circuit` | uint8 | 1 | — | raw = eng | — | — |

- `circuit` — 0=battery, 1=obc, 2=thruster

Example: `2D 00`  (circuit=0)

#### TCS_SET_HEATER_DUTY_LIMIT

**S8.1** · func_id **46** (0x2E) · 3 data byte(s)

Limit heater duty cycle to prevent excessive thermal cycling

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `circuit` | uint8 | 1 | — | raw = eng | — | — |
| `max_duty_pct` | uint8 | 2 | — | raw = eng | — | — |

- `circuit` — 0=battery, 1=obc, 2=thruster
- `max_duty_pct` — Maximum duty cycle percentage (0-100)

Example: `2E 00 00`  (circuit=0, max_duty_pct=0)

#### TCS_DECONTAMINATION_START

**S8.1** · func_id **47** (0x2F) · 5 data byte(s)

Begin decontamination heating sequence for payload

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `target_temp_c` | float32 | 1 | — | raw = eng | — | — |

- `target_temp_c` — Target FPA temperature for decontamination (typically +50C)

Example: `2F 00 00 00 00`  (target_temp_c=0)

#### TCS_DECONTAMINATION_STOP

**S8.1** · func_id **48** (0x30) · 1 data byte(s)

Abort decontamination heating sequence

*No arguments.*

Example: `30`

#### TCS_GET_THERMAL_MAP

**S8.1** · func_id **49** (0x31) · 1 data byte(s)

Request current thermal status of all zones

*No arguments.*

Example: `31`

### 3.5 OBDH

#### OBC_SET_MODE

**S8.1** · func_id **50** (0x32) · 2 data byte(s)

Set OBC mode

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | raw = eng | — | — |

- `mode` — 0=nominal, 1=safe, 2=emergency

Example: `32 00`  (mode=0)

#### OBC_MEMORY_SCRUB

**S8.1** · func_id **51** (0x33) · 1 data byte(s)

Trigger manual memory scrub

*No arguments.*

Example: `33`

#### OBC_REBOOT

**S8.1** · func_id **52** (0x34) · 1 data byte(s) · CRITICAL

Force OBC reboot (drops to boot loader)

*No arguments.*

Example: `34`

#### OBC_SWITCH_UNIT

**S8.1** · func_id **53** (0x35) · 1 data byte(s) · CRITICAL

Switch to redundant OBC (A->B or B->A)

*No arguments.*

Example: `35`

#### OBC_SELECT_BUS

**S8.1** · func_id **54** (0x36) · 2 data byte(s) · CAUTION

Select active CAN bus

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `bus` | uint8 | 1 | — | raw = eng | — | — |

- `bus` — 0=Bus A, 1=Bus B

Example: `36 00`  (bus=0)

#### OBC_BOOT_APP

**S8.1** · func_id **55** (0x37) · 1 data byte(s) · CAUTION

Boot application software from boot loader

*No arguments.*

Example: `37`

#### OBC_BOOT_INHIBIT

**S8.1** · func_id **56** (0x38) · 2 data byte(s)

Inhibit auto-boot (stay in boot loader)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `inhibit` | uint8 | 1 | — | raw = eng | — | — |

- `inhibit` — 0=allow auto-boot, 1=inhibit

Example: `38 00`  (inhibit=0)

#### OBC_CLEAR_REBOOT_CNT

**S8.1** · func_id **57** (0x39) · 1 data byte(s)

Reset reboot counter to zero

*No arguments.*

Example: `39`

#### OBC_SET_WATCHDOG_PERIOD

**S8.1** · func_id **58** (0x3A) · 3 data byte(s)

Configure watchdog timeout period

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `period_ticks` | uint16 | 1 | — | raw = eng | — | — |

- `period_ticks` — Watchdog period in ticks (1-1000)

Example: `3A 00 00`  (period_ticks=0)

#### OBC_WATCHDOG_ENABLE

**S8.1** · func_id **59** (0x3B) · 1 data byte(s)

Enable watchdog monitoring

*No arguments.*

Example: `3B`

#### OBC_WATCHDOG_DISABLE

**S8.1** · func_id **60** (0x3C) · 1 data byte(s)

Disable watchdog monitoring

*No arguments.*

Example: `3C`

#### OBC_DIAGNOSTIC

**S8.1** · func_id **61** (0x3D) · 1 data byte(s)

Request OBC health diagnostic report

*No arguments.*

Example: `3D`

#### OBC_ERROR_LOG

**S8.1** · func_id **62** (0x3E) · 1 data byte(s)

Request recent error log entries

*No arguments.*

Example: `3E`

#### OBC_GPS_TIME_SYNC

**S8.1** · func_id **80** (0x50) · 1 data byte(s) · CAUTION

Synchronise onboard clock with GPS time. Requires GPS 3D fix. WARNING: a time jump may invalidate AOCS attitude solution — AOCS may transition to SAFE_BOOT.

*No arguments.*

Example: `50`

#### EPS_DEPLOY_WING

**S8.1** · func_id **81** (0x51) · 2 data byte(s)

Deploy solar wing (+Y, -Y, or both) via burn wire

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `wing` | uint8 | 1 | — | raw = eng | — | — |

- `wing` — 0=+Y, 1=-Y, 2=both

Example: `51 00`  (wing=0)

#### EPS_WING_STATUS

**S8.1** · func_id **82** (0x52) · 1 data byte(s)

Query solar wing deployment status

*No arguments.*

Example: `52`

### 3.6 TT&C

#### TTC_SWITCH_PRIMARY

**S8.1** · func_id **63** (0x3F) · 1 data byte(s)

Switch to primary transponder

*No arguments.*

Example: `3F`

#### TTC_SWITCH_REDUNDANT

**S8.1** · func_id **64** (0x40) · 1 data byte(s)

Switch to redundant transponder

*No arguments.*

Example: `40`

#### TTC_SET_DATA_RATE

**S8.1** · func_id **65** (0x41) · 2 data byte(s) · readback ttc.tm_data_rate (0x0510)

Select a rung of the TM data-rate ladder defined in subsystems/ttc.yaml tm_rate_tiers. Sets data_rate_mode (the COMMANDED tier), which the TTC tick resolves the active tier from, so the commanded tier survives the tick and is authoritative. Three tiers: LOW 16 kbps BPSK conv(7,1/2)+RS — ROUTINE OPERATIONS, and the safe-mode / FDIR fallback; HIGH 128 kbps QPSK RS(255,223) — delayed-telemetry playback and image dumps only; BEACON 1 kbps BPSK conv(7,1/2)+RS — bootloader/beacon lifeline, entered with TTC_SET_BEACON_MODE, not selectable here. LEGACY ENCODING PRESERVED VERBATIM: rate=0 still means "the low rate" and rate=1 still means "the high rate" — they now select LOW (16 kbps) and HIGH (128 kbps) instead of 1 kbps and 64 kbps, so existing procedures, OBCPs and the FDIR ttc_reduce_power action keep their meaning. Permission rules are enforced by REJECTION with an S1.8 execution failure (error 0x0007), never by silent clamping: HIGH requires a deployed antenna, DOWNLINK/dump mode (TTC_SET_DOWNLINK_MODE) and elevation >= 10 deg, and every tier except BEACON is refused while beacon mode is latched. An unknown tier code gives error 0x0003.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `rate` | uint8 | 1 | — | enumerated | — | 0 |

*`rate` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `LOW` | 16 kbps BPSK conv(7,1/2)+RS - routine operations |
| 1 | `HIGH` | 128 kbps QPSK RS(255,223) - dumps only (gated) |
| 255 | `BEACON` | 1 kbps bootloader beacon - not selectable here |

- `rate` — Tier code: 0=LOW(16 kbps, routine operations), 1=HIGH(128 kbps, dumps only), 255=BEACON(1 kbps, bootloader only). Codes come from tm_rate_tiers[].command_code in subsystems/ttc.yaml.

Example: `41 00`  (rate=0)

#### TTC_PA_ON

**S8.1** · func_id **66** (0x42) · 1 data byte(s)

Enable power amplifier

*No arguments.*

Example: `42`

#### TTC_PA_OFF

**S8.1** · func_id **67** (0x43) · 1 data byte(s)

Disable power amplifier

*No arguments.*

Example: `43`

#### TTC_SET_TX_POWER

**S8.1** · func_id **68** (0x44) · 2 data byte(s) · CAUTION · readback ttc.tx_fwd_power (0x050D)

Select an SSPA operating point. Discrete steps only: the RF output of a solid-state PA is a function of the commanded gain state, baseplate temperature and VSWR, and is not a continuous ground-settable quantity. Unknown codes are REJECTED with S1.8 error 0x0003, never clamped. (The old '2 = 8 W' documentation was never executable: set_tx_power rejects anything above pa_max_power_w = 5.0 W.)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `level` | uint8 | 1 | — | enumerated | — | 1 |

*`level` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `LOW` | 1.0 W RF - link-margin-limited passes, FDIR power reduction |
| 1 | `NOMINAL` | 2.0 W RF - routine operations (ttc.yaml pa_nominal_power_w) |
| 2 | `HIGH` | 5.0 W RF - ceiling (ttc.yaml pa_max_power_w); ~17 W DC at 30% PAE |

- `level` — 0=LOW (1.0 W), 1=NOMINAL (2.0 W), 2=HIGH (5.0 W)

Example: `44 01`  (level=1)

#### TTC_DEPLOY_ANTENNA

**S8.1** · func_id **69** (0x45) · 1 data byte(s)

Deploy spacecraft antenna (burn-wire command)

*No arguments.*

Example: `45`

#### TTC_SET_BEACON_MODE

**S8.1** · func_id **70** (0x46) · 2 data byte(s)

Enable/disable beacon mode (low-rate telemetry)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off, 1=on

Example: `46 00`  (on=0)

#### TTC_CMD_CHANNEL_START

**S8.1** · func_id **71** (0x47) · 1 data byte(s)

Start dedicated command channel (15-min timer)

*No arguments.*

Example: `47`

#### TTC_SET_UL_FREQ

**S8.1** · func_id **72** (0x48) · 5 data byte(s)

Set uplink frequency

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `freq_mhz` | float32 | 1 | — | raw = eng | — | — |

- `freq_mhz` — Uplink frequency in MHz (S-band 2025-2110, CCSDS 401.0-B)

Example: `48 00 00 00 00`  (freq_mhz=0)

#### TTC_SET_DL_FREQ

**S8.1** · func_id **73** (0x49) · 5 data byte(s)

Set downlink frequency

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `freq_mhz` | float32 | 1 | — | raw = eng | — | — |

- `freq_mhz` — Downlink frequency in MHz (S-band 2200-2290, CCSDS 401.0-B)

Example: `49 00 00 00 00`  (freq_mhz=0)

#### TTC_SET_MODULATION

**S8.1** · func_id **74** (0x4A) · 2 data byte(s)

Set modulation scheme. Changes symbol mapping and effective data rate. BPSK is the default safe mode. Higher-order schemes increase throughput but require better Eb/N0.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mode` | uint8 | 1 | — | enumerated | — | 0 |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `BPSK` | 1 bit/sym, most robust |
| 1 | `QPSK` | 2 bit/sym |
| 2 | `8PSK` | 3 bit/sym, needs +3.6 dB |
| 3 | `OQPSK` | 2 bit/sym, offset timing |

- `mode` — 0=BPSK (1 bit/sym, most robust), 1=QPSK (2 bit/sym), 2=8PSK (3 bit/sym, needs +3.6 dB), 3=OQPSK (2 bit/sym, offset timing)

Example: `4A 00`  (mode=0)

#### TTC_SET_RX_GAIN

**S8.1** · func_id **75** (0x4B) · 5 data byte(s)

Set receiver AGC target level

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `agc_db` | float32 | 1 | — | raw = eng | — | — |

- `agc_db` — AGC target in dB (-100 to 0)

Example: `4B 00 00 00 00`  (agc_db=0)

#### TTC_RANGING_START

**S8.1** · func_id **76** (0x4C) · 1 data byte(s)

Begin ranging sequence (requires frame sync)

*No arguments.*

Example: `4C`

#### TTC_RANGING_STOP

**S8.1** · func_id **77** (0x4D) · 1 data byte(s)

Stop ranging sequence

*No arguments.*

Example: `4D`

#### TTC_SET_COHERENT_MODE

**S8.1** · func_id **78** (0x4E) · 2 data byte(s)

Enable/disable coherent transponder mode

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=off (non-coherent), 1=on (coherent)

Example: `4E 00`  (on=0)

#### TTC_SET_DOWNLINK_MODE

**S8.1** · func_id **79** (0x4F) · 2 data byte(s)

Enter or leave DOWNLINK / payload-dump mode. This is the gate on the HIGH tier of the TM data-rate ladder (TTC_SET_DATA_RATE): bulk downlink has to be entered deliberately, so an S13 image dump or an S15 delayed- telemetry playback is a planned pass activity rather than something the spacecraft drifts into at 1 Mbps. Leaving DOWNLINK mode does NOT clear the commanded tier — the active tier simply falls back to LOW until DOWNLINK mode is re-entered.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | raw = eng | — | — |

- `on` — 0=leave DOWNLINK mode, 1=enter DOWNLINK/dump mode

Example: `4F 00`  (on=0)

### 3.7 Other

#### AOCS_SET_DESAT_INHIBIT

**S8.1** · func_id **96** (0x60) · 2 data byte(s) · position `aocs`

Inhibit/allow momentum desaturation. A dump drops fine pointing, so it must not pre-empt an imaging strip; the inhibit is also asserted AUTOMATICALLY while payload.mode is IMAGING

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `inhibit` | uint8 | 1 | — | enumerated | — | 1 |

*`inhibit` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `ALLOW` |  |
| 1 | `INHIBIT` |  |

- `inhibit` — 0=allow desaturation, 1=inhibit

Example: `60 01`  (inhibit=1)

#### TTC_BEACON_TX_ON

**S8.1** · func_id **97** (0x61) · 1 data byte(s) · NORMAL · readback ttc.beacon_tx_on (0x0526)

Switch the UHF bootloader beacon TRANSMITTER on. This is the second radio, not a mode of the S-band transponder: separate band (437.5 MHz, in the 435-438 MHz amateur-satellite segment), separate PA (1 W RF), separate deployable dipole and separate link budget. It carries HK SID 11 only, and it keeps carrying it when the S-band prime chain is dead - PA off, ttc_tx line pulled, transponder failed or patch stowed. That independence is the entire reason a spacecraft carries one.

*No arguments.*

Example: `61`

#### TTC_BEACON_TX_OFF

**S8.1** · func_id **98** (0x62) · 1 data byte(s) · CAUTION · readback ttc.beacon_tx_on (0x0526)

Switch the UHF bootloader beacon transmitter off. CAUTION: this removes the mission's last independent downlink. With the beacon off, an S-band failure leaves the spacecraft unable to tell the ground that the OBC is alive, and recovery depends entirely on the uplink still working. Use only for RFI de-confliction or a deliberate radio-silence exercise.

*No arguments.*

Example: `62`

#### PAYLOAD_SET_SHUTTER

**S8.1** · func_id **119** (0x77) · 2 data byte(s) · CAUTION · position `payload` · readback payload.shutter_pos (0x063C)

Drive the shutter to an ABSOLUTE position. 2026-08b PL-d: there was no absolute shutter command anywhere in this catalogue — PAYLOAD_CYCLE_SHUTTER was the only shutter TC, and a relative toggle cannot reach a KNOWN state. That is what COM-011 step 2 ("cycle to CLOSED, verify 0x063C == 0 within 10 s") needs, what a dark frame needs, and what PAYLOAD_CAPTURE now checks before acquiring. Idempotent: commanding CLOSED twice leaves it CLOSED. Refused (0x0007) when the shutter mechanism device 0x0603 is unpowered, and (0x0005) while a PAYLOAD_CYCLE_SHUTTER exercise is running.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `position` | uint8 | 1 | — | enumerated | — | 1 |

*`position` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `CLOSED` | Blade across the aperture — the dark-frame configuration |
| 1 | `OPEN` | Light path open — required for capture and for flat fields |

- `position` — 0=closed, 1=open

Example: `77 01`  (position=1)

### 3.8 EPS (legacy quick actions)

#### EPS_SA_A_ON_LEGACY

**S8.1** · func_id **100** (0x64) · 1 data byte(s) · position `eps`

Solar array A ON (legacy quick-action)

*No arguments.*

Example: `64`

#### EPS_SA_A_OFF_LEGACY

**S8.1** · func_id **101** (0x65) · 1 data byte(s) · position `eps`

Solar array A OFF (legacy quick-action)

*No arguments.*

Example: `65`

#### EPS_SA_B_ON_LEGACY

**S8.1** · func_id **102** (0x66) · 1 data byte(s) · position `eps`

Solar array B ON (legacy quick-action)

*No arguments.*

Example: `66`

#### EPS_SA_B_OFF_LEGACY

**S8.1** · func_id **103** (0x67) · 1 data byte(s) · position `eps`

Solar array B OFF (legacy quick-action)

*No arguments.*

Example: `67`

#### TCS_HEATER_BAT_ON_LEGACY

**S8.1** · func_id **104** (0x68) · 1 data byte(s) · position `eps`

Battery heater ON (legacy quick-action)

*No arguments.*

Example: `68`

#### TCS_HEATER_BAT_OFF_LEGACY

**S8.1** · func_id **105** (0x69) · 1 data byte(s) · position `eps`

Battery heater OFF (legacy quick-action)

*No arguments.*

Example: `69`

#### TCS_HEATER_OBC_ON_LEGACY

**S8.1** · func_id **106** (0x6A) · 1 data byte(s) · position `eps`

OBC heater ON (legacy quick-action)

*No arguments.*

Example: `6A`

#### TCS_HEATER_OBC_OFF_LEGACY

**S8.1** · func_id **107** (0x6B) · 1 data byte(s) · position `eps`

OBC heater OFF (legacy quick-action)

*No arguments.*

Example: `6B`

### 3.9 Propulsion

#### PROP_ARM

**S8.1** · func_id **108** (0x6C) · 3 data byte(s) · CRITICAL · position `propulsion` · readback prop.state (0x0B00)

Arm the propulsion system (two-step: requires PROP_SET_BURN within 300 s and the arm code). Runs all interlocks — catbed >= 180 C, tank pressure band, propellant, AOCS mode/att error, SoC, phase, eclipse, valves; each failure returns a distinct S1(8) code and its bit in 0x0B15.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `arm_code` | uint16 | 1 | — | raw = eng | — | 42435 |

- `arm_code` — Fixed arm code 0xA5C3 (= 42435); any other value is rejected

Example: `6C A5 C3`  (arm_code=42435)

#### PROP_DISARM

**S8.1** · func_id **109** (0x6D) · 1 data byte(s) · position `propulsion`

ARMED -> SAFED; closes the flow-control valve, leaves latch valves

*No arguments.*

Example: `6D`

#### PROP_SET_BURN

**S8.1** · func_id **110** (0x6E) · 11 data byte(s) · CRITICAL · position `propulsion` · readback prop.burn_dv_cmd (0x0B0E)

Load a manoeuvre: delta-V magnitude, direction and cut-off mode. The sim publishes the predicted burn duration (0x0B10) for FDS cross-check before arming. Must precede PROP_ARM by <= 300 s.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `dv_m_s` | float32 | 1 | m/s | raw = eng | 0.01 … 20.0 | 0.85 |
| `direction` | uint8 | 5 | — | enumerated | — | 0 |
| `mode` | uint8 | 6 | — | enumerated | — | 1 |
| `t_start_cuc` | uint32 | 7 | s(CUC) | raw = eng | — | 0 |

*`direction` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `PROGRADE` | +X body, nominal nadir attitude |
| 1 | `RETROGRADE` | Requires 180-deg yaw / INERTIAL hold |

*`mode` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `OPEN_LOOP` | Timed burn |
| 1 | `DV_CUTOFF` | Closed-loop dv cut-off (accelerometer-integrated within this burn only) |

- `dv_m_s` — Delta-V magnitude, m/s (0 < dv <= 20; checked against 0x0B0C remaining)
- `direction` — 0=prograde (+X body, nominal nadir attitude), 1=retrograde (requires 180-deg yaw / INERTIAL hold)
- `mode` — 0=open-loop timed, 1=closed-loop dv cut-off (accelerometer-integrated within this burn only)
- `t_start_cuc` — Burn start CUC (0 = fire on PROP_EXECUTE; normally S11 time-tagged per NOM-004)

Example: `6E 3F 59 99 9A 00 01 00 00 00 00`  (dv_m_s=0.85, direction=0, mode=1, t_start_cuc=0)

#### PROP_EXECUTE

**S8.1** · func_id **111** (0x6F) · 1 data byte(s) · position `propulsion`

ARMED -> FIRING. Rejected (S1.8) when not ARMED. Normally delivered by the S11 time-tagged schedule (NOM-004 step 3).

*No arguments.*

Example: `6F`

#### PROP_ABORT

**S8.1** · func_id **112** (0x70) · 1 data byte(s) · position `propulsion`

FIRING -> SAFED immediately; closes the FCV and latches abort_reason (0x0B14). Partial delta-V already delivered still perturbs the orbit.

*No arguments.*

Example: `70`

#### PROP_ISO_OPEN

**S8.1** · func_id **113** (0x71) · 2 data byte(s) · position `propulsion` · readback prop.latch_a_state (0x0B06)

Open latch-valve branch(es)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `branch` | uint8 | 1 | — | enumerated | — | 2 |

*`branch` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `A` |  |
| 1 | `B` |  |
| 2 | `BOTH` |  |

- `branch` — 0=A, 1=B, 2=both

Example: `71 02`  (branch=2)

#### PROP_ISO_CLOSE

**S8.1** · func_id **114** (0x72) · 2 data byte(s) · position `propulsion` · readback prop.latch_a_state (0x0B06)

Close latch-valve branch(es) — CTG-028 step 2 isolates 'both branches' (branch=2)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `branch` | uint8 | 1 | — | enumerated | — | 2 |

*`branch` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `A` |  |
| 1 | `B` |  |
| 2 | `BOTH` |  |

- `branch` — 0=A, 1=B, 2=both

Example: `72 02`  (branch=2)

#### PROP_SET_LIMITS

**S8.1** · func_id **115** (0x73) · 17 data byte(s) · position `propulsion`

Reconfigure the on-board burn-abort thresholds (also the FDIR configuration hook)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `att_err_max_deg` | float32 | 1 | deg | raw = eng | 0.1 … 10.0 | 2.0 |
| `p_min_bar` | float32 | 5 | bar | raw = eng | 1.0 … 22.0 | 4.0 |
| `catbed_max_c` | float32 | 9 | degC | raw = eng | 200.0 … 500.0 | 350.0 |
| `t_burn_max_s` | float32 | 13 | s | raw = eng | 10.0 … 600.0 | 300.0 |

- `att_err_max_deg` — Abort when 0x0217 exceeds this during a burn (default 2.0)
- `p_min_bar` — Abort below this feed pressure (default 4.0)
- `catbed_max_c` — Abort above this catbed temperature (default 350)
- `t_burn_max_s` — Burn watchdog timeout (default 300)

Example: `73 40 00 00 00 40 80 00 00 43 AF 00 00 43 96 00 00`  (att_err_max_deg=2.0, p_min_bar=4.0, catbed_max_c=350.0, t_burn_max_s=300.0)

#### PROP_INHIBIT_SET

**S8.1** · func_id **116** (0x74) · 2 data byte(s) · position `propulsion` · readback prop.arm_inhibit_mask (0x0B15)

Ground-latched propulsion inhibit (the deployer 'independent inhibits' lesson)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `on` | uint8 | 1 | — | enumerated | — | 1 |

*`on` values:*

| Value | Label | Meaning |
|---|---|---|
| 0 | `CLEAR` | Clear ground inhibit |
| 1 | `SET` | Set ground inhibit |

- `on` — 0=clear ground inhibit, 1=set

Example: `74 01`  (on=1)

#### PROP_INHIBIT_CLR

**S8.1** · func_id **117** (0x75) · 3 data byte(s) · position `propulsion` · readback prop.state (0x0B00)

Clear both the ground inhibit and an FDIR/double-abort latched inhibit (INHIBIT(3) -> SAFED)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `key` | uint16 | 1 | — | raw = eng | — | 42435 |

- `key` — Clear key 0xA5C3 (= 42435)

Example: `75 A5 C3`  (key=42435)

#### PROP_GET_STATUS

**S8.1** · func_id **118** (0x76) · 1 data byte(s) · position `propulsion`

On-demand propulsion status report -> TM(8,2): state, tank pressure, propellant, catbed, dv remaining, inhibit mask, abort reason

*No arguments.*

Example: `76`

## 4. Other PUS services

### 4.1 Service 2 — Device Access

#### S2_AOCS_GPS_RECEIVER

**S2.1** · 3 data byte(s) · position `aocs`

GPS receiver ON/OFF (AOCS device 0x020F)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020F)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_GYROSCOPE_0

**S2.1** · 3 data byte(s)

Control gyroscope 0 (device 0x0206)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0206)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_GYROSCOPE_1

**S2.1** · 3 data byte(s)

Control gyroscope 1 (device 0x0207)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0207)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_GYROSCOPE_2

**S2.1** · 3 data byte(s)

Control gyroscope 2 (device 0x0208)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0208)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_MAGNETOMETER_A

**S2.1** · 3 data byte(s)

Control magnetometer A (device 0x0209)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0209)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_MAGNETOMETER_B

**S2.1** · 3 data byte(s)

Control magnetometer B (device 0x020A)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020A)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_MAGNETORQUER_X

**S2.1** · 3 data byte(s)

Control magnetorquer X (device 0x020B)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020B)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_MAGNETORQUER_Y

**S2.1** · 3 data byte(s)

Control magnetorquer Y (device 0x020C)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020C)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_MAGNETORQUER_Z

**S2.1** · 3 data byte(s)

Control magnetorquer Z (device 0x020D)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020D)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_REACTION_WHEEL_0

**S2.1** · 3 data byte(s)

Control reaction wheel 0 (device 0x0200)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0200)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_REACTION_WHEEL_1

**S2.1** · 3 data byte(s)

Control reaction wheel 1 (device 0x0201)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0201)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_REACTION_WHEEL_2

**S2.1** · 3 data byte(s)

Control reaction wheel 2 (device 0x0202)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0202)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_REACTION_WHEEL_3

**S2.1** · 3 data byte(s)

Control reaction wheel 3 (device 0x0203)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0203)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_STAR_TRACKER_1

**S2.1** · 3 data byte(s)

Control star tracker 1 (device 0x0204)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0204)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_STAR_TRACKER_2

**S2.1** · 3 data byte(s)

Control star tracker 2 (device 0x0205)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0205)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_AOCS_SUN_SENSOR_ARRAY

**S2.1** · 3 data byte(s)

Control sun sensor array (device 0x020E)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x020E)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_BATTERY_HEATER

**S2.1** · 3 data byte(s)

Control battery heater (device 0x0100)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0100)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_CHARGE_REGULATOR

**S2.1** · 3 data byte(s)

Control battery charge regulator (device 0x010F)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x010F)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_PDU_BUS1

**S2.1** · 3 data byte(s)

Control PDU power distribution bus 1 (device 0x0102)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0102)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_PDU_BUS2

**S2.1** · 3 data byte(s)

Control PDU power distribution bus 2 (device 0x0103)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0103)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_PDU_BUS3

**S2.1** · 3 data byte(s)

Control PDU power distribution bus 3 (device 0x0104)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0104)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_EPS_SOLAR_ARRAY_DRIVE

**S2.1** · 3 data byte(s)

Control solar array drive (device 0x0101)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0101)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_OBDH_CAN_BUS

**S2.1** · 3 data byte(s)

Control CAN bus interface (device 0x0504)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0504)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_OBDH_MASS_MEMORY

**S2.1** · 3 data byte(s)

Control mass memory unit (device 0x0502)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0502)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_OBDH_OBC_A

**S2.1** · 3 data byte(s)

Control OBC-A (device 0x0500)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0500)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_OBDH_OBC_B

**S2.1** · 3 data byte(s)

Control OBC-B (device 0x0501)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0501)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_OBDH_WATCHDOG_TIMER

**S2.1** · 3 data byte(s)

Control watchdog timer (device 0x0503)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0503)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_PAYLOAD_CALIBRATION_LAMP

**S2.1** · 3 data byte(s)

Control calibration lamp (device 0x0602)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0602)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_PAYLOAD_COMPRESSION

**S2.1** · 3 data byte(s)

Control compression unit (device 0x0604)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0604)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_PAYLOAD_COOLER

**S2.1** · 3 data byte(s)

Control FPA cooler (device 0x0601)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0601)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_PAYLOAD_FPA

**S2.1** · 3 data byte(s)

Control focal plane array (device 0x0600)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0600)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_PAYLOAD_SHUTTER

**S2.1** · 3 data byte(s)

Control shutter mechanism (device 0x0603)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0603)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_DECONTAM_HEATER

**S2.1** · 3 data byte(s)

Control decontamination heater (device 0x030B)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x030B)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_1

**S2.1** · 3 data byte(s)

Control heater zone 1 (device 0x0300)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0300)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_10

**S2.1** · 3 data byte(s)

Control heater zone 10 (device 0x0309)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0309)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_2

**S2.1** · 3 data byte(s)

Control heater zone 2 (device 0x0301)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0301)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_3

**S2.1** · 3 data byte(s)

Control heater zone 3 (device 0x0302)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0302)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_4

**S2.1** · 3 data byte(s)

Control heater zone 4 (device 0x0303)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0303)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_5

**S2.1** · 3 data byte(s)

Control heater zone 5 (device 0x0304)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0304)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_6

**S2.1** · 3 data byte(s)

Control heater zone 6 (device 0x0305)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0305)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_7

**S2.1** · 3 data byte(s)

Control heater zone 7 (device 0x0306)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0306)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_8

**S2.1** · 3 data byte(s)

Control heater zone 8 (device 0x0307)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0307)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_HEATER_ZONE_9

**S2.1** · 3 data byte(s)

Control heater zone 9 (device 0x0308)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0308)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TCS_THERMISTOR_ARRAY

**S2.1** · 3 data byte(s)

Control thermistor array (device 0x030A)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x030A)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TTC_ANTENNA_DRIVE

**S2.1** · 3 data byte(s)

Control antenna drive (device 0x0404)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0404)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TTC_LNA

**S2.1** · 3 data byte(s)

Control low-noise amplifier (device 0x0403)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0403)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TTC_POWER_AMPLIFIER

**S2.1** · 3 data byte(s)

Control power amplifier (device 0x0402)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0402)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TTC_TRANSPONDER_A

**S2.1** · 3 data byte(s)

Control transponder A (device 0x0400)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0400)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_TTC_TRANSPONDER_B

**S2.1** · 3 data byte(s)

Control transponder B (device 0x0401)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |
| `state` | uint8 | 2 | — | raw = eng | — | — |

- `device_id` — Device ID (0x0401)
- `state` — 0=OFF, 1=ON

Example: `00 00 00`  (device_id=0, state=0)

#### S2_REPORT_STATUS

**S2.6** · 2 data byte(s)

Report device on/off status

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `device_id` | uint16 | 0 | — | raw = eng | — | — |

- `device_id` — Device ID to query

Example: `00 00`  (device_id=0)

### 4.2 Service 3 — Housekeeping

#### HK_ENABLE

**S3.5** · 2 data byte(s)

Enable periodic HK generation

*Aliases:* HK_REPORT_CONFIG

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `sid` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (sid=0)

#### HK_DISABLE

**S3.6** · 2 data byte(s)

Disable periodic HK generation

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `sid` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (sid=0)

#### HK_REQUEST

**S3.27** · 2 data byte(s)

Request one-shot HK report

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `sid` | uint16 | 0 | — | raw = eng | — | — |

- `sid` — Structure ID (1-6, 10)

Example: `00 00`  (sid=0)

#### HK_SET_INTERVAL

**S3.31** · 6 data byte(s)

Modify HK reporting interval

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `sid` | uint16 | 0 | — | raw = eng | — | — |
| `interval_s` | float32 | 2 | — | raw = eng | — | — |


Example: `00 00 00 00 00 00`  (sid=0, interval_s=0)

### 4.3 Service 5 — Event Reporting

#### EVENT_ENABLE

**S5.5** · 2 data byte(s)

Enable event report generation for specified event IDs

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `event_id` | uint16 | 0 | — | raw = eng | — | — |

- `event_id` — Event definition ID

Example: `00 00`  (event_id=0)

#### EVENT_DISABLE

**S5.6** · 2 data byte(s)

Disable event report generation for specified event IDs

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `event_id` | uint16 | 0 | — | raw = eng | — | — |

- `event_id` — Event definition ID

Example: `00 00`  (event_id=0)

### 4.4 Service 6 — Memory Management

#### AOCS_SET_TORQUE_GAINS_ALL

**S6.2** · 16 data byte(s)

Set all three AOCS torque control gains

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `address` | uint32 | 0 | — | raw = eng | — | — |
| `gain_x` | float32 | 4 | — | raw = eng | — | — |
| `gain_y` | float32 | 8 | — | raw = eng | — | — |
| `gain_z` | float32 | 12 | — | raw = eng | — | — |

- `address` — Gain base address (0x20100000)
- `gain_x` — X-axis torque gain
- `gain_y` — Y-axis torque gain
- `gain_z` — Z-axis torque gain

Example: `00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00`  (address=0, gain_x=0, gain_y=0, gain_z=0)

#### AOCS_SET_TORQUE_GAIN_X

**S6.2** · 8 data byte(s)

Set AOCS X-axis torque control gain

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `address` | uint32 | 0 | — | raw = eng | — | — |
| `gain` | float32 | 4 | — | raw = eng | — | — |

- `address` — Gain X address (0x20100000)
- `gain` — X-axis torque gain

Example: `00 00 00 00 00 00 00 00`  (address=0, gain=0)

#### AOCS_SET_TORQUE_GAIN_Y

**S6.2** · 8 data byte(s)

Set AOCS Y-axis torque control gain

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `address` | uint32 | 0 | — | raw = eng | — | — |
| `gain` | float32 | 4 | — | raw = eng | — | — |

- `address` — Gain Y address (0x20100004)
- `gain` — Y-axis torque gain

Example: `00 00 00 00 00 00 00 00`  (address=0, gain=0)

#### AOCS_SET_TORQUE_GAIN_Z

**S6.2** · 8 data byte(s)

Set AOCS Z-axis torque control gain

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `address` | uint32 | 0 | — | raw = eng | — | — |
| `gain` | float32 | 4 | — | raw = eng | — | — |

- `address` — Gain Z address (0x20100008)
- `gain` — Z-axis torque gain

Example: `00 00 00 00 00 00 00 00`  (address=0, gain=0)

#### MEM_LOAD

**S6.2** · variable length

Load data to on-board memory. Address must lie in an rw region of configs/eosat1/memory/memory_map.yaml; unmapped -> S1.8 code 0x0001, read-only -> 0x0002.

*Aliases:* MEM_LOAD_START, MEM_LOAD_DATA, MEM_LOAD_ABORT

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `memory_id` | uint8 | 0 | — | raw = eng | — | — |
| `address` | uint32 | 1 | — | raw = eng | — | — |
| `data` | bytes | 5 | — | raw = eng | — | — |

- `memory_id` — 0=SRAM, 1=EEPROM (informational; the address selects the region)
- `address` — Start address
- `data` — Data to load

Example: _variable length — see the field table_

#### AOCS_DUMP_TORQUE_GAINS

**S6.5** · 6 data byte(s)

Dump all three AOCS torque control gains

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `address` | uint32 | 0 | — | raw = eng | — | — |
| `length` | uint16 | 4 | — | raw = eng | — | — |

- `address` — Gain base address (0x20100000)
- `length` — Dump length (12 bytes = 3 floats)

Example: `00 00 00 00 00 00`  (address=0, length=0)

#### MEM_DUMP

**S6.5** · 7 data byte(s)

Dump on-board memory contents

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `memory_id` | uint8 | 0 | — | raw = eng | — | — |
| `address` | uint32 | 1 | — | raw = eng | — | — |
| `length` | uint16 | 5 | — | raw = eng | — | — |


Example: `00 00 00 00 00 00 00`  (memory_id=0, address=0, length=0)

#### MEM_CHECK

**S6.9** · 7 data byte(s)

Check on-board memory CRC

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `memory_id` | uint8 | 0 | — | raw = eng | — | — |
| `address` | uint32 | 1 | — | raw = eng | — | — |
| `length` | uint16 | 5 | — | raw = eng | — | — |


Example: `00 00 00 00 00 00 00`  (memory_id=0, address=0, length=0)

### 4.5 Service 9 — Time Management

#### SET_TIME

**S9.1** · 4 data byte(s)

Set spacecraft time

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `cuc_seconds` | uint32 | 0 | — | raw = eng | — | — |


Example: `00 00 00 00`  (cuc_seconds=0)

#### REQUEST_TIME

**S9.2** · 0 data byte(s)

Request time report

*Aliases:* TIME_REPORT_REQUEST

*No arguments.*

Example: ``

### 4.6 Service 11 — Time-tagged Scheduling

#### SCHEDULE_TC

**S11.4** · variable length

Insert time-tagged TC into schedule

*Aliases:* TIME_SCHEDULE

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `exec_time_cuc` | uint32 | 0 | — | raw = eng | — | — |
| `tc_data` | bytes | 4 | — | raw = eng | — | — |

- `exec_time_cuc` — Execution time (CUC seconds)
- `tc_data` — Embedded TC packet

Example: _variable length — see the field table_

#### DELETE_SCHEDULED

**S11.7** · 2 data byte(s)

Delete scheduled command by ID

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `cmd_id` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (cmd_id=0)

#### DISABLE_SCHEDULE

**S11.9** · 0 data byte(s)

Disable TC schedule execution

*No arguments.*

Example: ``

#### DELETE_ALL_SCHEDULED

**S11.11** · 0 data byte(s) · CAUTION

Delete all scheduled commands

*No arguments.*

Example: ``

#### ENABLE_SCHEDULE

**S11.13** · 0 data byte(s)

Enable TC schedule execution

*No arguments.*

Example: ``

#### LIST_SCHEDULE

**S11.17** · 0 data byte(s)

Request schedule summary report

*No arguments.*

Example: ``

### 4.7 Service 12 — On-board Monitoring

#### MON_ENABLE

**S12.1** · 0 data byte(s)

Enable on-board parameter monitoring

*No arguments.*

Example: ``

#### MON_DISABLE

**S12.2** · 0 data byte(s)

Disable on-board parameter monitoring

*No arguments.*

Example: ``

#### MON_ENABLE_DEF

**S12.3** · 2 data byte(s)

Enable ONE monitoring definition (12,1 enables the whole service)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mon_id` | uint16 | 0 | — | raw = eng | — | — |

- `mon_id` — Definition ID to enable

Example: `00 00`  (mon_id=0)

#### MON_DISABLE_DEF

**S12.4** · 2 data byte(s)

Disable ONE monitoring definition (12,2 disables the whole service)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mon_id` | uint16 | 0 | — | raw = eng | — | — |

- `mon_id` — Definition ID to disable

Example: `00 00`  (mon_id=0)

#### MON_ADD_DEF

**S12.6** · 13 data byte(s)

Add parameter monitoring definition (mon_id addresses the definition, not the parameter)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mon_id` | uint16 | 0 | — | raw = eng | — | — |
| `param_id` | uint16 | 2 | — | raw = eng | — | — |
| `check_type` | uint8 | 4 | — | raw = eng | — | — |
| `low_limit` | float32 | 5 | — | raw = eng | — | — |
| `high_limit` | float32 | 9 | — | raw = eng | — | — |

- `mon_id` — Definition ID (unique per monitor)
- `param_id` — Parameter to monitor
- `check_type` — 0=absolute (value in band), 1=delta (change since last check in band)
- `low_limit` — Low limit threshold
- `high_limit` — High limit threshold

Example: `00 00 00 00 00 00 00 00 00 00 00 00 00`  (mon_id=0, param_id=0, check_type=0, low_limit=0, high_limit=0)

#### MON_DELETE_DEF

**S12.7** · 2 data byte(s)

Delete parameter monitoring definition

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `mon_id` | uint16 | 0 | — | raw = eng | — | — |

- `mon_id` — Definition ID to delete

Example: `00 00`  (mon_id=0)

#### MON_REPORT_DEFS

**S12.12** · 0 data byte(s)

Report all monitoring definitions -> TM(12,13)

*No arguments.*

Example: ``

### 4.8 Service 13 — Large Data Transfer

#### S13_CREATE_TRANSFER

**S13.1** · 4 data byte(s)

Create a download transfer session for a STORED image product. 2026-08b PL-c: the session is now bound to a real catalogue entry. A scene that is not in the payload's image catalogue is REFUSED (S1.8 error 0x0003 plus a TM(13,4) NAK) — previously a transfer for scene 9999 against an EMPTY catalogue returned transfer_id 1, 4 MB and ACTIVE, so the ground could open, run and complete the download of an image that had never been taken. Use PAYLOAD_GET_IMAGE_CATALOG (S8 func 32) to see what is actually on board. The session is sized from the product's own COMPRESSED size, so `size_mb` is now advisory: it is ignored whenever the scene is catalogued. TM(13,2) response: transfer_id(2) + total_blocks(4) + crc32(4). The last two fields are appended, so a decoder that reads only the transfer id is unaffected; `crc32` is the checksum the instrument computed over the product at capture, and the ground should recompute it over the received blocks — a CORRUPT product (catalogue status 2) will not match, which is how a corrupted image is now diagnosable.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `scene_id` | uint16 | 0 | — | raw = eng | — | — |
| `size_mb` | uint16 | 2 | — | raw = eng | — | — |

- `scene_id` — Scene ID to download (must be in the payload image catalogue)
- `size_mb` — Advisory only; ignored for a catalogued scene. 0 = configured payload image size (4 MB)

Example: `00 00 00 00`  (scene_id=0, size_mb=0)

#### S13_DOWNLOAD_SEGMENT

**S13.3** · 4 data byte(s)

Request one 1024-byte segment of image data. The bytes are DERIVED from the product (a keyed stream over the catalogue entry's data_seed), so the same segment requested twice is identical and the whole product checksums to the crc32 that S13_CREATE_TRANSFER reported. They used to be a `(segment_num + i) % 256` ramp identical for every scene.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `transfer_id` | uint16 | 0 | — | raw = eng | — | — |
| `segment_num` | uint16 | 2 | — | raw = eng | — | — |

- `transfer_id` — Transfer session ID
- `segment_num` — Segment block number

Example: `00 00 00 00`  (transfer_id=0, segment_num=0)

#### S13_END_TRANSFER

**S13.5** · 2 data byte(s)

Complete transfer session and free resources. 2026-08b PL-c: it now really does free them — a session whose blocks were ALL sent deletes the product and returns its storage (event 0x0614, 0x0647/0x0648 fall). Previously nothing was freed, so a spacecraft could downlink its entire store and still report it full. A partial or aborted session leaves the product on board, which is the "the pass dropped, re-dump next orbit" case this service exists for.

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `transfer_id` | uint16 | 0 | — | raw = eng | — | — |

- `transfer_id` — Transfer session ID

Example: `00 00`  (transfer_id=0)

#### S13_REPORT_STATUS

**S13.9** · 2 data byte(s)

Get transfer progress status

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `transfer_id` | uint16 | 0 | — | raw = eng | — | — |

- `transfer_id` — Transfer session ID

Example: `00 00`  (transfer_id=0)

### 4.9 Service 15 — On-board Storage

#### ENABLE_STORE

**S15.1** · 1 data byte(s)

Enable onboard TM store

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `store_id` | uint8 | 0 | — | raw = eng | — | — |

- `store_id` — 1=HK, 2=Event, 3=Science, 4=Alarm

Example: `00`  (store_id=0)

#### DISABLE_STORE

**S15.2** · 1 data byte(s)

Disable onboard TM store

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `store_id` | uint8 | 0 | — | raw = eng | — | — |


Example: `00`  (store_id=0)

#### DUMP_STORE

**S15.9** · 1 data byte(s)

Dump contents of onboard TM store

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `store_id` | uint8 | 0 | — | raw = eng | — | — |


Example: `00`  (store_id=0)

#### DELETE_STORE

**S15.11** · 1 data byte(s) · CAUTION

Delete contents of onboard TM store

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `store_id` | uint8 | 0 | — | raw = eng | — | — |


Example: `00`  (store_id=0)

#### STORE_STATUS

**S15.13** · 0 data byte(s)

Request onboard TM store status

*No arguments.*

Example: ``

### 4.10 Service 17 — Connection Test

#### CONNECTION_TEST

**S17.1** · 0 data byte(s)

Send connection test, expect S17.2 response

*No arguments.*

Example: ``

### 4.11 Service 18 — On-board Control Procedures

#### OBCP_LOAD

**S18.1** · variable length · CAUTION

Direct-load an OBCP (raw serialized blob: proc_id + name + steps; see docs/OBCP_GUIDE.md)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `obcp_blob` | bytes | 0 | — | raw = eng | — | — |

- `obcp_blob` — Serialized OBCP load blob (hex)

Example: _variable length — see the field table_

#### OBCP_DELETE

**S18.2** · 2 data byte(s)

Delete a loaded on-board control procedure (aborts it if running)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `proc_id` | uint16 | 0 | — | raw = eng | — | — |

- `proc_id` — Procedure ID (boot: 1=pass_prep, 2=safe_mode, 3=payload_checkout)

Example: `00 00`  (proc_id=0)

#### OBCP_START

**S18.3** · 3 data byte(s)

Activate (start) a loaded on-board control procedure

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `proc_id` | uint16 | 0 | — | raw = eng | — | — |
| `param` | uint8 | 2 | — | raw = eng | — | — |

- `proc_id` — Procedure ID (boot: 1=pass_prep, 2=safe_mode, 3=payload_checkout)
- `param` — Optional activation argument (0 if unused)

Example: `00 00 00`  (proc_id=0, param=0)

#### OBCP_STOP

**S18.4** · 2 data byte(s) · CAUTION

Stop (abort) a running on-board control procedure

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `proc_id` | uint16 | 0 | — | raw = eng | — | — |

- `proc_id` — Procedure ID (0 = stop ALL running procedures)

Example: `00 00`  (proc_id=0)

#### OBCP_STATUS_REPORT

**S18.6** · 0 data byte(s)

Request OBCP status report — TM(18,7) lists every loaded procedure with run state and current step

*No arguments.*

Example: ``

### 4.12 Service 19 — Event-Action

#### EA_ADD

**S19.1** · 6 data byte(s)

Add event-action definition (trigger_kind 0=S5 event id, 1=S12 monitored parameter id)

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `ea_id` | uint16 | 0 | — | raw = eng | — | — |
| `trigger_kind` | uint8 | 2 | — | raw = eng | — | — |
| `trigger_id` | uint16 | 3 | — | raw = eng | — | — |
| `action_func_id` | uint8 | 5 | — | raw = eng | — | — |

- `ea_id` — Event-action definition ID
- `trigger_kind` — 0=event id (event_catalog.yaml), 1=parameter id (S12 limit violation)
- `trigger_id` — Triggering event ID or parameter ID, per trigger_kind
- `action_func_id` — S8 function dispatched when the rule fires

Example: `00 00 00 00 00 00`  (ea_id=0, trigger_kind=0, trigger_id=0, action_func_id=0)

#### EA_DELETE

**S19.2** · 2 data byte(s)

Delete event-action definition

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `ea_id` | uint16 | 0 | — | raw = eng | — | — |

- `ea_id` — Event-action definition ID

Example: `00 00`  (ea_id=0)

#### EA_ENABLE

**S19.4** · 2 data byte(s)

Enable event-action definition

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `ea_id` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (ea_id=0)

#### EA_DISABLE

**S19.5** · 2 data byte(s)

Disable event-action definition

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `ea_id` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (ea_id=0)

#### EA_REPORT_DEFS

**S19.8** · 0 data byte(s)

Report all event-action definitions -> TM(19,128)

*No arguments.*

Example: ``

### 4.13 Service 20 — Parameter Management

#### SET_PARAM

**S20.1** · 6 data byte(s)

Set parameter value

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `param_id` | uint16 | 0 | — | raw = eng | — | — |
| `value` | float32 | 2 | — | raw = eng | — | — |


Example: `00 00 00 00 00 00`  (param_id=0, value=0)

#### GET_PARAM

**S20.3** · 2 data byte(s)

Request parameter value

| Argument | Type | Offset | Unit | Raw ↔ engineering | Range | Default |
|---|---|---|---|---|---|---|
| `param_id` | uint16 | 0 | — | raw = eng | — | — |


Example: `00 00`  (param_id=0)

## 5. MCS quick commands

The console's QUICK COMMANDS panel is generated from the same catalogue: each button names a command and its **engineering** argument values, and the hex below is what the shared encoder produces at click time.

| Group | Button | Command | Arguments | Wire bytes |
|---|---|---|---|---|
| AOCS MODES | Detumble | `AOCS_SET_MODE` | mode=2 | `00 02` |
| AOCS MODES | Coarse Sun | `AOCS_SET_MODE` | mode=3 | `00 03` |
| AOCS MODES | Nominal | `AOCS_SET_MODE` | mode=4 | `00 04` |
| AOCS MODES | Fine Point | `AOCS_SET_MODE` | mode=5 | `00 05` |
| AOCS MODES | Desaturate | `AOCS_DESATURATE` | — | `01` |
| AOCS EQUIPMENT | RW0 On | `AOCS_ENABLE_WHEEL` | wheel_idx=0 | `03 00` |
| AOCS EQUIPMENT | RW1 On | `AOCS_ENABLE_WHEEL` | wheel_idx=1 | `03 01` |
| AOCS EQUIPMENT | RW2 On | `AOCS_ENABLE_WHEEL` | wheel_idx=2 | `03 02` |
| AOCS EQUIPMENT | RW3 On | `AOCS_ENABLE_WHEEL` | wheel_idx=3 | `03 03` |
| AOCS EQUIPMENT | RW0 Off | `AOCS_DISABLE_WHEEL` | wheel_idx=0 | `02 00` |
| AOCS EQUIPMENT | RW1 Off | `AOCS_DISABLE_WHEEL` | wheel_idx=1 | `02 01` |
| AOCS EQUIPMENT | RW2 Off | `AOCS_DISABLE_WHEEL` | wheel_idx=2 | `02 02` |
| AOCS EQUIPMENT | RW3 Off | `AOCS_DISABLE_WHEEL` | wheel_idx=3 | `02 03` |
| AOCS EQUIPMENT | MTQ Enable | `MTQ_ENABLE` | enable=1 | `09 01` |
| AOCS EQUIPMENT | MTQ Disable | `MTQ_ENABLE` | enable=0 | `09 00` |
| AOCS EQUIPMENT | ST1 On | `ST1_POWER` | on=1 | `04 01` |
| AOCS EQUIPMENT | ST1 Off | `ST1_POWER` | on=0 | `04 00` |
| AOCS EQUIPMENT | ST2 On | `ST2_POWER` | on=1 | `05 01` |
| AOCS EQUIPMENT | ST2 Off | `ST2_POWER` | on=0 | `05 00` |
| AOCS EQUIPMENT | GPS Rx On | `S2_AOCS_GPS_RECEIVER` | device_id=527, state=1 | `02 0F 01` |
| AOCS EQUIPMENT | GPS Rx Off | `S2_AOCS_GPS_RECEIVER` | device_id=527, state=0 | `02 0F 00` |
| AOCS EQUIPMENT | GPS Cold | `AOCS_GPS_SET_START_MODE` | start_mode=0 | `54 00` |
| AOCS EQUIPMENT | GPS Warm | `AOCS_GPS_SET_START_MODE` | start_mode=1 | `54 01` |
| AOCS EQUIPMENT | GPS Hot | `AOCS_GPS_SET_START_MODE` | start_mode=2 | `54 02` |
| EPS POWER LINES | TTC TX On | `EPS_POWER_ON` | line_index=2 | `13 02` |
| EPS POWER LINES | TTC TX Off | `EPS_POWER_OFF` | line_index=2 | `14 02` |
| EPS POWER LINES | Payload On | `EPS_POWER_ON` | line_index=3 | `13 03` |
| EPS POWER LINES | Payload Off | `EPS_POWER_OFF` | line_index=3 | `14 03` |
| EPS POWER LINES | FPA Cool On | `EPS_POWER_ON` | line_index=4 | `13 04` |
| EPS POWER LINES | FPA Cool Off | `EPS_POWER_OFF` | line_index=4 | `14 04` |
| EPS POWER LINES | Bat Htr On | `EPS_POWER_ON` | line_index=5 | `13 05` |
| EPS POWER LINES | Bat Htr Off | `EPS_POWER_OFF` | line_index=5 | `14 05` |
| EPS POWER LINES | OBC Htr On | `EPS_POWER_ON` | line_index=6 | `13 06` |
| EPS POWER LINES | OBC Htr Off | `EPS_POWER_OFF` | line_index=6 | `14 06` |
| EPS POWER LINES | AOCS Whl On | `EPS_POWER_ON` | line_index=7 | `13 07` |
| EPS POWER LINES | AOCS Whl Off | `EPS_POWER_OFF` | line_index=7 | `14 07` |
| EPS POWER LINES | Reset OC 0 | `EPS_RESET_OC_FLAG` | line_index=0 | `15 00` |
| TCS | Bat Htr ON | `HEATER_BATTERY` | on=1 | `28 01` |
| TCS | Bat Htr OFF | `HEATER_BATTERY` | on=0 | `28 00` |
| TCS | FPA Cool ON | `FPA_COOLER` | on=1 | `2B 01` |
| TCS | FPA Cool OFF | `FPA_COOLER` | on=0 | `2B 00` |
| TTC | PA ON | `TTC_PA_ON` | — | `42` |
| TTC | PA OFF | `TTC_PA_OFF` | — | `43` |
| TTC | DL Mode ON | `TTC_SET_DOWNLINK_MODE` | on=1 | `4F 01` |
| TTC | DL Mode OFF | `TTC_SET_DOWNLINK_MODE` | on=0 | `4F 00` |
| TTC | Rate HIGH (128k) | `TTC_SET_DATA_RATE` | rate=1 | `41 01` |
| TTC | Rate LOW (16k) | `TTC_SET_DATA_RATE` | rate=0 | `41 00` |
| TTC | TX Pwr LOW (1W) | `TTC_SET_TX_POWER` | level=0 | `44 00` |
| TTC | TX Pwr NOM (2W) | `TTC_SET_TX_POWER` | level=1 | `44 01` |
| TTC | TX Pwr HIGH (5W) | `TTC_SET_TX_POWER` | level=2 | `44 02` |
| TTC | Beacon TX ON | `TTC_BEACON_TX_ON` | — | `61` |
| TTC | Beacon TX OFF | `TTC_BEACON_TX_OFF` | — | `62` |
| PAYLOAD | Standby | `PAYLOAD_SET_MODE` | mode=1 | `1A 01` |
| PAYLOAD | Imaging | `PAYLOAD_SET_MODE` | mode=2 | `1A 02` |
| PAYLOAD | Off | `PAYLOAD_SET_MODE` | mode=0 | `1A 00` |
| PAYLOAD | Capture 2048 ln | `PAYLOAD_CAPTURE` | scene_id=0, lines=2048 | `1C 00 00 08 00` |
| PAYLOAD | Cooler -15 C | `PAYLOAD_COOLER_SETPOINT` | setpoint=-15.0 | `24 64 D7` |
| PAYLOAD | Cooler -10 C | `PAYLOAD_COOLER_SETPOINT` | setpoint=-10.0 | `24 66 CB` |
| PAYLOAD | Bands ALL | `PAYLOAD_SET_BAND_CONFIG` | mask=15 | `21 0F` |
| PAYLOAD | Bands VIS | `PAYLOAD_SET_BAND_CONFIG` | mask=7 | `21 07` |
| PAYLOAD | Gain 1.0x | `PAYLOAD_SET_GAIN` | gain=1.0 | `23 03 E8` |
| PAYLOAD | Int 8/12/16/20 ms | `PAYLOAD_SET_INTEGRATION_TIME` | blue_ms=8.0, green_ms=12.0, red_ms=16.0, nir_ms=20.0 | `22 03 20 04 B0 06 40 07 D0` |
| PAYLOAD | Cal DARK | `PAYLOAD_START_CALIBRATION` | mode=0 | `25 00` |
| PAYLOAD | Cal BOTH | `PAYLOAD_START_CALIBRATION` | mode=2 | `25 02` |
| PAYLOAD | Cal STOP | `PAYLOAD_STOP_CALIBRATION` | — | `26` |
| PAYLOAD | Cal Lamp On | `PAYLOAD_CAL_LAMP` | on=1 | `5F 01` |
| PAYLOAD | Cal Lamp Off | `PAYLOAD_CAL_LAMP` | on=0 | `5F 00` |
| PAYLOAD | Shutter x5 | `PAYLOAD_CYCLE_SHUTTER` | cycles=5 | `5D 05` |
| PAYLOAD | Shutter CLOSED | `PAYLOAD_SET_SHUTTER` | position=0 | `77 00` |
| PAYLOAD | Shutter OPEN | `PAYLOAD_SET_SHUTTER` | position=1 | `77 01` |
| PAYLOAD | Calibration mode | `PAYLOAD_SET_MODE` | mode=3 | `1A 03` |
| PAYLOAD | Cal FLAT | `PAYLOAD_START_CALIBRATION` | mode=1 | `25 01` |
| PAYLOAD | Filter CLEAR | `PAYLOAD_SELECT_FILTER` | position=0 | `5C 00` |
| PAYLOAD | Catalog Req | `PAYLOAD_GET_IMAGE_CATALOG` | — | `20` |
| PROPULSION | Catbed Preheat ON | `HEATER_THRUSTER` | on=1 | `2A 01` |
| PROPULSION | Catbed Preheat OFF | `HEATER_THRUSTER` | on=0 | `2A 00` |
| PROPULSION | Iso Valves OPEN | `PROP_ISO_OPEN` | branch=2 | `71 02` |
| PROPULSION | Iso Valves CLOSE | `PROP_ISO_CLOSE` | branch=2 | `72 02` |
| PROPULSION | Load Burn 0.85 m/s pro | `PROP_SET_BURN` | dv_m_s=0.85, direction=0, mode=1, t_start_cuc=0 | `6E 3F 59 99 9A 00 01 00 00 00 00` |
| PROPULSION | ARM (step 2 of 2) | `PROP_ARM` | arm_code=42435 | `6C A5 C3` |
| PROPULSION | DISARM | `PROP_DISARM` | — | `6D` |
| PROPULSION | EXECUTE BURN | `PROP_EXECUTE` | — | `6F` |
| PROPULSION | ABORT BURN | `PROP_ABORT` | — | `70` |
| PROPULSION | Inhibit SET | `PROP_INHIBIT_SET` | on=1 | `74 01` |
| PROPULSION | Inhibit CLEAR | `PROP_INHIBIT_CLR` | key=42435 | `75 A5 C3` |
| PROPULSION | Status Report | `PROP_GET_STATUS` | — | `76` |
| OBDH | OBC Reboot | `OBC_REBOOT` | — | `34` |
| OBDH | OBC Switch | `OBC_SWITCH_UNIT` | — | `35` |
| OBDH | Boot App | `OBC_BOOT_APP` | — | `37` |
| OBDH | Mem Scrub | `OBC_MEMORY_SCRUB` | — | `33` |
| OBDH | GPS Time Sync | `OBC_GPS_TIME_SYNC` | — | `50` |
| GENERAL | Conn Test | `CONNECTION_TEST` | — | `` |
| GENERAL | Deploy Ant | `TTC_DEPLOY_ANTENNA` | — | `45` |
| GENERAL | WD Enable | `OBC_WATCHDOG_ENABLE` | — | `3B` |
| HK REQUESTS (S3.27) | EPS (SID1) | `HK_REQUEST` | sid=1 | `00 01` |
| HK REQUESTS (S3.27) | AOCS (SID2) | `HK_REQUEST` | sid=2 | `00 02` |
| HK REQUESTS (S3.27) | TCS (SID3) | `HK_REQUEST` | sid=3 | `00 03` |
| HK REQUESTS (S3.27) | OBDH (SID4) | `HK_REQUEST` | sid=4 | `00 04` |
| HK REQUESTS (S3.27) | Payload (SID5) | `HK_REQUEST` | sid=5 | `00 05` |
| HK REQUESTS (S3.27) | TTC (SID6) | `HK_REQUEST` | sid=6 | `00 06` |
| HK REQUESTS (S3.27) | PROP (SID9) | `HK_REQUEST` | sid=9 | `00 09` |
| HK REQUESTS (S3.27) | EPS PWR (SID8) | `HK_REQUEST` | sid=8 | `00 08` |

---

*Generated from `commands/tc_catalog.yaml` — 220 commands, 270 arguments.*
