# Power & Thermal (eps_tcs) -- Role Analysis

**Position ID:** `eps_tcs`
**Display Name:** Power & Thermal
**Subsystems:** eps, tcs
**Allowed PUS Services:** 1, 3, 5, 8, 17, 20
**Allowed func_ids:** 13, 14, 15, 30, 31, 32, 33, 34, 35
**Visible Tabs:** overview, eps, tcs, commanding, procedures, manual
**Manual Sections:** 01_eps, 03_tcs

## 1. Mission Lifecycle Phases and Applicable Procedures

### LEOP

| Procedure | ID | eps_tcs Role |
|---|---|---|
| Initial Health Check | LEOP-002 | Verify power and thermal status |
| Solar Array Verification | LEOP-004 | Monitor solar array currents and voltages |

### Commissioning

| Procedure | ID | eps_tcs Role |
|---|---|---|
| EPS Checkout | COM-001 | Execute EPS tests, verify power distribution |
| TCS Verification | COM-002 | Verify thermal control loops and heater operation |
| Payload Power On | COM-009 | Monitor power budget impact |
| FPA Cooler Activation | COM-010 | Monitor power consumption and thermal |

### Nominal Operations

| Procedure | ID | eps_tcs Role |
|---|---|---|
| Eclipse Transition | NOM-010 | Monitor power balance during eclipse |

### Contingency

| Procedure | ID | eps_tcs Role |
|---|---|---|
| Under-Voltage Load Shed | CTG-001 | Execute power line disconnections |
| Thermal Exceedance | CTG-004 | Adjust heaters and power |
| EPS Safe Mode | CTG-005 | Execute EPS safe mode recovery |
| Solar Array Degradation | CTG-009 | Assess degradation, adjust power profile |
| Overcurrent Response | CTG-012 | Isolate and reset overcurrent line |
| Battery Cell Failure | CTG-013 | Assess cell failure, adjust charge limits |

### Emergency

| Procedure | ID | eps_tcs Role |
|---|---|---|
| Total Power Failure | EMG-002 | Manage emergency power restoration |
| Thermal Runaway | EMG-006 | Emergency heater shutdown |

## 2. Available Commands and Telemetry

### Commands

#### EPS Commands (S8, func_ids 13-15)

| Command | func_id | Description |
|---|---|---|
| EPS_POWER_ON | 13 | Switch power line ON (line_index 0-7) |
| EPS_POWER_OFF | 14 | Switch power line OFF (line_index 0-7) |
| EPS_RESET_OC_FLAG | 15 | Reset overcurrent trip flag and re-enable line |

#### TCS Commands (S8, func_ids 30-35)

| Command | func_id | Description |
|---|---|---|
| HEATER_BATTERY | 30 | Battery heater on/off |
| HEATER_OBC | 31 | OBC heater on/off |
| HEATER_THRUSTER | 32 | Thruster heater on/off |
| FPA_COOLER | 33 | FPA cooler on/off |
| HEATER_SET_SETPOINT | 34 | Modify heater thermostat setpoints (circuit, on_temp, off_temp) |
| HEATER_AUTO_MODE | 35 | Return heater to autonomous thermostat control |

#### General Services

| Service | Commands | Description |
|---|---|---|
| S1 | (TM only) | Request verification reports |
| S3 | HK_REQUEST, HK_ENABLE, HK_DISABLE, HK_SET_INTERVAL | Housekeeping for SID 1 (EPS) and SID 3 (TCS) |
| S5 | EVENT_ENABLE, EVENT_DISABLE | Event report control |
| S17 | CONNECTION_TEST | Link verification |
| S20 | SET_PARAM, GET_PARAM | Direct parameter read/write for EPS/TCS parameters |

### Telemetry

#### EPS Parameters (SID 1, 1 s interval)

| Parameter | ID | Units | Description |
|---|---|---|---|
| eps.bat_voltage | 0x0100 | V | Battery voltage |
| eps.bat_soc | 0x0101 | % | Battery state of charge |
| eps.bat_temp | 0x0102 | C | Battery temperature |
| eps.sa_a_current | 0x0103 | A | Solar array A current |
| eps.sa_b_current | 0x0104 | A | Solar array B current |
| eps.bus_voltage | 0x0105 | V | Bus voltage |
| eps.power_cons | 0x0106 | W | Power consumption |
| eps.power_gen | 0x0107 | W | Power generation |
| eps.eclipse_flag | 0x0108 | -- | Eclipse indicator |
| eps.bat_current | 0x0109 | A | Battery current |
| eps.oc_trip_flags | 0x010D | -- | Overcurrent trip bitmask |
| eps.uv_flag | 0x010E | -- | Undervoltage flag |
| eps.pl_obc..pl_aocs_wheels | 0x0110-0x0117 | -- | Power line status (8 lines) |
| eps.line_current_0..7 | 0x0118-0x011F | A | Per-line current draw |
| eps.bat_dod | 0x0120 | % | Battery depth of discharge |
| eps.bat_cycles | 0x0121 | -- | Charge/discharge cycle count |
| eps.mppt_efficiency | 0x0122 | -- | MPPT tracker efficiency |
| eps.sa_age_factor | 0x0123 | -- | Solar array aging factor |

#### TCS Parameters (SID 3, 60 s interval)

| Parameter | ID | Units | Description |
|---|---|---|---|
| tcs.temp_panel_px..mz | 0x0400-0x0405 | C | Six panel temperatures |
| tcs.temp_obc | 0x0406 | C | OBC temperature |
| tcs.temp_battery | 0x0407 | C | Battery temperature |
| tcs.temp_fpa | 0x0408 | C | FPA detector temperature |
| tcs.temp_thruster | 0x0409 | C | Thruster temperature |
| tcs.htr_battery | 0x040A | -- | Battery heater status |
| tcs.htr_obc | 0x040B | -- | OBC heater status |
| tcs.cooler_fpa | 0x040C | -- | FPA cooler status |
| tcs.htr_duty_battery | 0x040E | % | Battery heater duty cycle |
| tcs.htr_duty_obc | 0x040F | % | OBC heater duty cycle |
| tcs.htr_duty_thruster | 0x0410 | % | Thruster heater duty cycle |
| tcs.htr_total_power | 0x0411 | W | Total heater power |

#### Limit Monitoring (this position must watch)

| Parameter | Yellow | Red |
|---|---|---|
| eps.bat_voltage | 23.0 -- 29.0 V | 22.0 -- 29.5 V |
| eps.bat_soc | 25 -- 95 % | 15 -- 100 % |
| eps.bat_temp | 2.0 -- 40.0 C | 0.0 -- 45.0 C |
| eps.bus_voltage | 27.0 -- 29.0 V | 26.5 -- 29.5 V |
| eps.bat_dod | 0 -- 60 % | 0 -- 80 % |
| eps.line_current_* | Per-line thresholds | Per-line thresholds |
| tcs.temp_obc | 5.0 -- 60.0 C | 0.0 -- 70.0 C |
| tcs.temp_battery | 2.0 -- 40.0 C | 0.0 -- 45.0 C |
| tcs.temp_fpa | -18.0 -- 8.0 C | -20.0 -- 12.0 C |
| tcs.htr_total_power | 0 -- 15 W | 0 -- 18 W |

### Display Widgets

**EPS Overview:** Battery SoC gauge, bus voltage gauge, solar array values, UV/OV flags.
**Power Lines:** Per-line current draw table, overcurrent trip flags.
**Power Trends:** Power balance chart (gen vs. cons, 10 min), battery SoC trend (3 hr), line currents chart.
**Thermal Overview:** 10-point temperature table, heater status indicators, FPA cooler indicator.
**Temperature Trends:** Component temperatures chart (30 min), panel temperatures chart (30 min).

## 3. Inter-Position Coordination Needs

| Scenario | Coordinating With | Coordination Details |
|---|---|---|
| Initial health check (LEOP-002) | flight_director, fdir_systems | Confirm power/thermal nominal before OBDH checkout proceeds |
| Solar array verification (LEOP-004) | flight_director | Report SA currents; FD authorizes |
| Payload power on (COM-009) | flight_director, payload_ops | Monitor power budget impact when payload draws ~25 W |
| FPA cooler activation (COM-010) | flight_director, payload_ops | Monitor cooler power draw (~8 W) and thermal impact |
| Eclipse transition (NOM-010) | aocs | Coordinate: AOCS monitors attitude; eps_tcs monitors power balance and battery heater activation |
| Thermal exceedance (CTG-004) | flight_director, payload_ops | eps_tcs adjusts heaters/power; payload_ops safes imager if needed |
| Thermal runaway (EMG-006) | flight_director, payload_ops | Emergency heater shutdown; payload_ops powers off imager |
| Under-voltage load shed (CTG-001) | flight_director | FD authorizes load shed priority; eps_tcs executes EPS_POWER_OFF on non-essential lines |

### FDIR Rules Relevant to This Position

| FDIR Rule | Trigger | Action |
|---|---|---|
| eps.bat_soc < 20% | Level 1 | Autonomous payload_poweroff |
| eps.bat_soc < 15% | Level 2 | safe_mode_eps |
| eps.bus_voltage < 26 V | Level 2 | safe_mode_eps |
| tcs.temp_battery > 42 C | Level 1 | heater_off_battery |
| tcs.temp_battery < 1 C | Level 1 | heater_on_battery |

## 4. GO/NO-GO Responsibilities

The Power & Thermal position provides GO/NO-GO input to the Flight Director for:

- **Pass startup:** Confirm battery SoC adequate for pass duration, no UV/OV flags, thermal status nominal.
- **Payload activation (COM-009, COM-010):** Confirm power budget supports payload + cooler draw; thermal margins adequate.
- **Eclipse entry/exit:** Confirm battery charge sufficient for eclipse duration; heater margins adequate.
- **Contingency recovery:** Confirm power system stable before allowing subsystems to be re-enabled after load shed.
- **Any power line switching:** Confirm no overcurrent conditions before EPS_POWER_ON.

**Critical Decision Points:**
- If eps.bat_soc drops below 25% (yellow), recommend payload power-off to Flight Director.
- If any eps.line_current exceeds yellow threshold, recommend isolating the affected line.
- If tcs.temp_battery exits yellow range, recommend heater override via HEATER_SET_SETPOINT or HEATER_AUTO_MODE.

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*
