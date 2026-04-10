# EOSAT-1 Command and Telemetry Reference

**Document ID:** EOSAT1-UM-REF-010
**Issue:** 1.0
**Date:** 2026-03-09
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Purpose

This document provides a consolidated reference of all telecommands (TC) and telemetry
parameters (TM) for the EOSAT-1 spacecraft. It is intended as a quick-reference companion
to the individual subsystem manuals.

## 2. Telecommand Catalogue

### 2.1 AOCS Commands

| Command            | Service | Subservice | Parameters          | Description                     |
|--------------------|---------|------------|---------------------|---------------------------------|
| AOCS_SET_MODE      | S8      | S1         | mode (uint8)        | Set AOCS mode (func_id 0)      |
| AOCS_DESATURATE    | S8      | S1         | —                   | Initiate RW desaturation (func_id 1) |
| AOCS_DISABLE_WHEEL | S8      | S1         | wheel_idx (uint8)   | Disable individual RW (func_id 2) |
| AOCS_ENABLE_WHEEL  | S8      | S1         | wheel_idx (uint8)   | Enable individual RW (func_id 3) |
| ST1_POWER          | S8      | S1         | on (uint8)          | Star tracker 1 on/off (func_id 4) |
| ST2_POWER          | S8      | S1         | on (uint8)          | Star tracker 2 on/off (func_id 5) |
| ST_SELECT          | S8      | S1         | unit (uint8)        | Select primary ST (func_id 6)   |
| MAG_SELECT         | S8      | S1         | on (uint8)          | Select magnetometer (func_id 7) |

**AOCS_SET_MODE — mode values:**

| Value | Mode Name      | Description                                    |
|-------|----------------|------------------------------------------------|
| 0     | OFF            | No control, no attitude determination          |
| 1     | SAFE_BOOT      | Minimal safe state, post-boot                  |
| 2     | DETUMBLE       | Rate damping using magnetorquers (B-dot)       |
| 3     | COARSE_SUN     | Sun-pointing using CSS and magnetorquers       |
| 4     | NOMINAL        | Nadir-pointing, full attitude control (RW)     |
| 5     | FINE_POINT     | Precision pointing using star tracker          |
| 6     | SLEW           | Commanded attitude manoeuvre                   |
| 7     | DESAT          | Reaction wheel desaturation                    |
| 8     | ECLIPSE        | Eclipse-safe propagation mode                  |

### 2.2 Payload Commands

| Command           | Service | Subservice | Parameters          | Description                    |
|-------------------|---------|------------|---------------------|--------------------------------|
| PAYLOAD_SET_MODE  | S8      | S1         | mode (uint8)        | Set payload operating mode     |

**PAYLOAD_SET_MODE — mode values:**

| Value | Mode Name  | Description                                       |
|-------|------------|---------------------------------------------------|
| 0     | OFF        | Payload powered off                               |
| 1     | STANDBY    | Electronics on, FPA cooler active, no imaging     |
| 2     | IMAGING    | Active image acquisition                          |

### 2.3 OBDH Commands

| Command            | Service | Subservice | Parameters          | Description                       |
|--------------------|---------|------------|---------------------|-----------------------------------|
| OBC_SET_MODE       | S8      | S1         | mode (uint8)        | Set OBC operating mode (func_id 50) |
| OBC_MEMORY_SCRUB   | S8      | S1         | —                   | Trigger manual memory scrub (func_id 51) |
| OBC_REBOOT         | S8      | S1         | —                   | Force OBC reboot (func_id 52) [CRITICAL] |
| OBC_SWITCH_UNIT    | S8      | S1         | —                   | Switch to redundant OBC (func_id 53) [CRITICAL] |
| OBC_SELECT_BUS     | S8      | S1         | bus (uint8)         | Select active CAN bus (func_id 54) |
| OBC_BOOT_APP       | S8      | S1         | —                   | Boot application from bootloader (func_id 55) |
| OBC_BOOT_INHIBIT   | S8      | S1         | inhibit (uint8)     | Inhibit/allow auto-boot (func_id 56) |
| OBC_CLEAR_REBOOT_CNT | S8   | S1         | —                   | Reset reboot counter (func_id 57) |
| OBC_GPS_TIME_SYNC  | S8      | S1         | —                   | Sync OBC clock to GPS (func_id 80) [CAUTION] |

**OBC_SET_MODE — mode values:**

| Value | Mode Name    | Description                                      |
|-------|--------------|--------------------------------------------------|
| 0     | NOMINAL      | Full operations                                  |
| 1     | SAFE         | Reduced operations, non-essential loads off       |
| 2     | EMERGENCY    | Minimal boot image, ground intervention required  |

**OBC_GPS_TIME_SYNC notes:** Requires GPS 3D fix. A time jump > 5s will force AOCS to SAFE_BOOT mode (event 0x020F). AOCS must be re-commissioned through mode progression after a large time jump.

### 2.4 TTC Commands

| Command               | Service | Subservice | Parameters | Description                     |
|-----------------------|---------|------------|------------|---------------------------------|
| TTC_SWITCH_PRIMARY    | S8      | S1         | —          | Activate primary transponder    |
| TTC_SWITCH_REDUNDANT  | S8      | S1         | —          | Activate redundant transponder  |

### 2.5 TCS Commands

| Command          | Service | Subservice | Parameters            | Description                    |
|------------------|---------|------------|-----------------------|--------------------------------|
| HEATER_CONTROL   | S8      | S1         | circuit (str), on (bool) | Control heater circuit      |

**HEATER_CONTROL — circuit values:**

| Circuit   | Heater Location    | Nominal Power |
|-----------|--------------------|---------------|
| battery   | Battery pack       | 8 W           |
| obc       | OBC module         | 5 W           |
| thruster  | Propulsion valve   | 3 W           |

### 2.6 Housekeeping Commands

| Command      | Service | Subservice | Parameters     | Description                       |
|--------------|---------|------------|----------------|-----------------------------------|
| HK_REQUEST   | S3      | S27        | sid (uint8)    | Request housekeeping report       |

**HK_REQUEST — SID values:**

| SID | Subsystem | Content                               |
|-----|-----------|---------------------------------------|
| 1   | EPS       | All EPS parameters (0x0100–0x010A)    |
| 2   | AOCS      | All AOCS parameters (0x0200–0x021B)   |
| 3   | OBDH      | All OBDH parameters (0x0300–0x030A)   |
| 4   | TCS       | All TCS parameters (0x0400–0x040C)    |
| 5   | TTC       | All TTC parameters (0x0500–0x050A)    |
| 6   | Payload   | All PLD parameters (0x0600–0x0609)    |

### 2.7 Time Management Commands

| Command    | Service | Subservice | Parameters           | Description                    |
|------------|---------|------------|----------------------|--------------------------------|
| SET_TIME   | S9      | S1         | cuc_seconds (uint32) | Set on-board time (CUC)       |

### 2.8 Parameter Management Commands

| Command    | Service | Subservice | Parameters              | Description                  |
|------------|---------|------------|-------------------------|------------------------------|
| GET_PARAM  | S20     | S3         | param_id (uint16)       | Read parameter by ID         |
| SET_PARAM  | S20     | S1         | param_id (uint16), value| Write parameter by ID        |

---

## 3. Telemetry Parameter Reference

### 3.1 EPS Parameters (0x01xx)

| Param ID | Mnemonic        | Unit   | Type    | Description                    | Yellow Low | Yellow High | Red Low | Red High |
|----------|-----------------|--------|---------|--------------------------------|------------|-------------|---------|----------|
| 0x0100   | bat_voltage     | V      | float32 | Battery terminal voltage       | 23         | 29          | 22      | 29.5     |
| 0x0101   | bat_soc         | %      | float32 | Battery state of charge        | 25         | 95          | 15      | 100      |
| 0x0102   | bat_temp        | deg C  | float32 | Battery temperature            | 2          | 40          | 0       | 45       |
| 0x0103   | sa_a_current    | A      | float32 | Solar array A current          | —          | —           | —       | —        |
| 0x0104   | sa_b_current    | A      | float32 | Solar array B current          | —          | —           | —       | —        |
| 0x0105   | bus_voltage     | V      | float32 | Regulated bus voltage          | 27         | 29          | 26.5    | 29.5     |
| 0x0106   | power_cons      | W      | float32 | Total power consumption        | —          | —           | —       | —        |
| 0x0107   | power_gen       | W      | float32 | Total power generation         | —          | —           | —       | —        |
| 0x0108   | eclipse_flag    | bool   | uint8   | Eclipse indicator              | —          | —           | —       | —        |
| 0x0109   | bat_current     | A      | float32 | Battery current (+chg/-dischg) | —          | —           | —       | —        |
| 0x010A   | bat_capacity    | Ah     | float32 | Remaining battery capacity     | —          | —           | —       | —        |

### 3.2 AOCS Parameters (0x02xx)

| Param ID | Mnemonic        | Unit   | Type    | Description                    | Yellow Low | Yellow High | Red Low | Red High |
|----------|-----------------|--------|---------|--------------------------------|------------|-------------|---------|----------|
| 0x0200   | quat_q1         | —      | float64 | Quaternion q1                  | —          | —           | —       | —        |
| 0x0201   | quat_q2         | —      | float64 | Quaternion q2                  | —          | —           | —       | —        |
| 0x0202   | quat_q3         | —      | float64 | Quaternion q3                  | —          | —           | —       | —        |
| 0x0203   | quat_q4         | —      | float64 | Quaternion q4 (scalar)         | —          | —           | —       | —        |
| 0x0204   | rate_roll       | deg/s  | float32 | Body roll rate                 | -0.5       | 0.5         | -2      | 2        |
| 0x0205   | rate_pitch      | deg/s  | float32 | Body pitch rate                | -0.5       | 0.5         | -2      | 2        |
| 0x0206   | rate_yaw        | deg/s  | float32 | Body yaw rate                  | -0.5       | 0.5         | -2      | 2        |
| 0x0207   | rw1_speed       | RPM    | float32 | Reaction wheel 1 speed         | -5000      | 5000        | -5500   | 5500     |
| 0x0208   | rw2_speed       | RPM    | float32 | Reaction wheel 2 speed         | -5000      | 5000        | -5500   | 5500     |
| 0x0209   | rw3_speed       | RPM    | float32 | Reaction wheel 3 speed         | -5000      | 5000        | -5500   | 5500     |
| 0x020A   | rw4_speed       | RPM    | float32 | Reaction wheel 4 speed         | -5000      | 5000        | -5500   | 5500     |
| 0x020B   | mag_x           | uT     | float32 | Magnetometer X                 | —          | —           | —       | —        |
| 0x020C   | mag_y           | uT     | float32 | Magnetometer Y                 | —          | —           | —       | —        |
| 0x020D   | mag_z           | uT     | float32 | Magnetometer Z                 | —          | —           | —       | —        |
| 0x020F   | aocs_mode       | enum   | uint8   | AOCS mode                      | —          | —           | —       | —        |
| 0x0210   | sc_lat          | deg    | float32 | Sub-satellite latitude         | —          | —           | —       | —        |
| 0x0211   | sc_lon          | deg    | float32 | Sub-satellite longitude        | —          | —           | —       | —        |
| 0x0212   | sc_alt          | km     | float32 | Orbital altitude               | —          | —           | —       | —        |
| 0x0217   | att_error       | deg    | float32 | Total attitude error           | -1         | 1           | -2      | 2        |
| 0x0218   | rw1_temp        | deg C  | float32 | Reaction wheel 1 temperature   | 0          | 60          | -5      | 70       |
| 0x0219   | rw2_temp        | deg C  | float32 | Reaction wheel 2 temperature   | 0          | 60          | -5      | 70       |
| 0x021A   | rw3_temp        | deg C  | float32 | Reaction wheel 3 temperature   | 0          | 60          | -5      | 70       |
| 0x021B   | rw4_temp        | deg C  | float32 | Reaction wheel 4 temperature   | 0          | 60          | -5      | 70       |

### 3.3 OBDH Parameters (0x03xx)

| Param ID | Mnemonic         | Unit   | Type    | Description                    | Yellow Low | Yellow High | Red Low | Red High |
|----------|------------------|--------|---------|--------------------------------|------------|-------------|---------|----------|
| 0x0300   | obc_mode         | enum   | uint8   | OBC mode (0/1/2)               | —          | —           | —       | —        |
| 0x0301   | obc_temp         | deg C  | float32 | OBC board temperature          | —          | —           | —       | —        |
| 0x0302   | cpu_load         | %      | float32 | CPU utilisation                | 0          | 85          | 0       | 98       |
| 0x0303   | mmm_used         | %      | float32 | Mass memory utilisation        | —          | —           | —       | —        |
| 0x0304   | tc_recv_count    | count  | uint32  | Total TCs received             | —          | —           | —       | —        |
| 0x0305   | tc_exec_count    | count  | uint32  | TCs executed                   | —          | —           | —       | —        |
| 0x0306   | tc_reject_count  | count  | uint32  | TCs rejected                   | —          | —           | —       | —        |
| 0x0307   | tm_pkt_count     | count  | uint32  | TM packets generated           | —          | —           | —       | —        |
| 0x0308   | uptime           | s      | uint32  | Seconds since boot             | —          | —           | —       | —        |
| 0x030A   | reboot_count     | count  | uint16  | Total OBC reboots              | —          | —           | —       | —        |

### 3.4 TCS Parameters (0x04xx)

| Param ID | Mnemonic         | Unit   | Type    | Description                    | Yellow Low | Yellow High | Red Low | Red High |
|----------|------------------|--------|---------|--------------------------------|------------|-------------|---------|----------|
| 0x0400   | temp_panel_px    | deg C  | float32 | +X panel temperature           | —          | —           | —       | —        |
| 0x0401   | temp_panel_mx    | deg C  | float32 | -X panel temperature           | —          | —           | —       | —        |
| 0x0402   | temp_panel_py    | deg C  | float32 | +Y panel temperature           | —          | —           | —       | —        |
| 0x0403   | temp_panel_my    | deg C  | float32 | -Y panel temperature           | —          | —           | —       | —        |
| 0x0404   | temp_panel_pz    | deg C  | float32 | +Z panel temperature           | —          | —           | —       | —        |
| 0x0405   | temp_panel_mz    | deg C  | float32 | -Z panel temperature           | —          | —           | —       | —        |
| 0x0406   | temp_obc         | deg C  | float32 | OBC temperature                | 5          | 60          | 0       | 70       |
| 0x0407   | temp_battery     | deg C  | float32 | Battery temperature            | 2          | 40          | 0       | 45       |
| 0x0408   | temp_fpa         | deg C  | float32 | FPA detector temperature       | -18        | 8           | -20     | 12       |
| 0x0409   | temp_thruster    | deg C  | float32 | Thruster valve temperature     | —          | —           | —       | —        |
| 0x040A   | htr_battery      | bool   | uint8   | Battery heater state           | —          | —           | —       | —        |
| 0x040B   | htr_obc          | bool   | uint8   | OBC heater state               | —          | —           | —       | —        |
| 0x040C   | cooler_fpa       | bool   | uint8   | FPA cooler state               | —          | —           | —       | —        |

### 3.5 TTC Parameters (0x05xx)

| Param ID | Mnemonic           | Unit   | Type    | Description                    |
|----------|--------------------|--------|---------|--------------------------------|
| 0x0500   | ttc_mode           | enum   | uint8   | Active transponder             |
| 0x0501   | link_status        | enum   | uint8   | Link state (0=none, 1=locked)  |
| 0x0502   | rssi               | dBm    | float32 | Received signal strength       |
| 0x0503   | link_margin        | dB     | float32 | Current link margin            |
| 0x0506   | tm_data_rate       | kbps   | float32 | Downlink data rate             |
| 0x0507   | xpdr_temp          | deg C  | float32 | Transponder temperature        |
| 0x0509   | range_km           | km     | float32 | Slant range to ground station  |
| 0x050A   | contact_elevation  | deg    | float32 | Ground station elevation       |

### 3.6 Payload Parameters (0x06xx)

| Param ID | Mnemonic         | Unit   | Type    | Description                    | Yellow Low | Yellow High | Red Low | Red High |
|----------|------------------|--------|---------|--------------------------------|------------|-------------|---------|----------|
| 0x0600   | pld_mode         | enum   | uint8   | Payload mode (0/1/2)           | —          | —           | —       | —        |
| 0x0601   | fpa_temp         | deg C  | float32 | FPA detector temperature       | -18        | 8           | -20     | 12       |
| 0x0602   | cooler_pwr       | W      | float32 | FPA cooler power               | —          | —           | —       | —        |
| 0x0604   | store_used       | %      | float32 | Data storage utilisation       | —          | —           | —       | —        |
| 0x0605   | image_count      | count  | uint16  | Stored image count             | —          | —           | —       | —        |
| 0x0609   | checksum_errors  | count  | uint16  | Data integrity errors          | —          | —           | —       | —        |

---

## 4. Service Type Summary

| Service | Subservice | Name              | Direction | Description                    |
|---------|------------|-------------------|-----------|--------------------------------|
| S3      | S27        | HK_REQUEST        | TC        | Request housekeeping report    |
| S8      | S1         | FUNCTION_MGMT     | TC        | Mode changes, actuator control |
| S9      | S1         | SET_TIME          | TC        | Time synchronisation           |
| S20     | S1         | SET_PARAM         | TC        | Write parameter                |
| S20     | S3         | GET_PARAM         | TC        | Read parameter                 |

## 5. Parameter ID Allocation Map

| Range           | Subsystem | Count |
|-----------------|-----------|-------|
| 0x0100 – 0x01FF | EPS       | 11    |
| 0x0200 – 0x02FF | AOCS      | 20    |
| 0x0300 – 0x03FF | OBDH      | 10    |
| 0x0400 – 0x04FF | TCS       | 13    |
| 0x0500 – 0x05FF | TTC       | 8     |
| 0x0600 – 0x06FF | Payload   | 6     |

**Total telemetry parameters:** 68

---

*End of Document — EOSAT1-UM-REF-010*
