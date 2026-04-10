# FDIR / Systems (fdir_systems) -- Role Analysis

**Position ID:** `fdir_systems`
**Display Name:** FDIR / Systems
**Subsystems:** obdh, fdir
**Allowed PUS Services:** 1, 3, 5, 6, 8, 12, 17, 19, 20
**Allowed func_ids:** 40, 41, 42, 43, 44, 45, 46, 47
**Visible Tabs:** overview, obdh, commanding, pus, procedures, manual
**Manual Sections:** 04_obdh, 08_fdir

## 1. Mission Lifecycle Phases and Applicable Procedures

### LEOP

| Procedure | ID | fdir_systems Role |
|---|---|---|
| Initial Health Check | LEOP-002 | Verify OBDH and HK configuration |

### Commissioning

| Procedure | ID | fdir_systems Role |
|---|---|---|
| OBDH Checkout | COM-007 | Verify OBC, CAN bus, memory subsystems |
| FDIR Configuration | COM-008 | Configure monitoring limits and event-actions |

### Nominal Operations

| Procedure | ID | fdir_systems Role |
|---|---|---|
| HK Configuration | NOM-005 | Adjust HK reporting rates and SID configuration |
| Software Upload | NOM-006 | Execute memory load and verify |
| Clock Synchronisation | NOM-008 | Execute time update command |

### Contingency

| Procedure | ID | fdir_systems Role |
|---|---|---|
| OBDH Watchdog Recovery | CTG-010 | Assess watchdog event, verify OBC state |
| OBC Redundancy Switchover | CTG-011 | Execute switchover, verify new OBC |
| Memory Segment Failure | CTG-016 | Isolate bad segment, remap storage |
| Bus Failure Switchover | CTG-017 | Switch CAN bus, verify communication |
| Boot Loader Recovery | CTG-018 | Execute boot loader recovery sequence |

### Emergency

| Procedure | ID | fdir_systems Role |
|---|---|---|
| OBC Reboot | EMG-003 | Execute reboot, verify recovery |

## 2. Available Commands and Telemetry

### Commands

#### OBDH Function Commands (S8, func_ids 40-47)

| Command | func_id | Criticality | Description | Fields |
|---|---|---|---|---|
| OBC_SET_MODE | 40 | -- | Set OBC mode | mode: 0=nominal, 1=safe, 2=emergency |
| OBC_MEMORY_SCRUB | 41 | -- | Trigger manual memory scrub | (none) |
| OBC_REBOOT | 42 | **critical** | Force OBC reboot (drops to boot loader) | (none) |
| OBC_SWITCH_UNIT | 43 | **critical** | Switch to redundant OBC (A<->B) | (none) |
| OBC_SELECT_BUS | 44 | caution | Select active CAN bus | bus: 0=Bus A, 1=Bus B |
| OBC_BOOT_APP | 45 | caution | Boot application software from boot loader | (none) |
| OBC_BOOT_INHIBIT | 46 | -- | Inhibit auto-boot (stay in boot loader) | inhibit: 0=allow, 1=inhibit |
| OBC_CLEAR_REBOOT_CNT | 47 | -- | Reset reboot counter to zero | (none) |

#### Memory Management (S6)

| Command | Subtype | Description | Fields |
|---|---|---|---|
| MEM_LOAD | 2 | Load data to onboard memory | memory_id (0=SRAM, 1=EEPROM), address, data |
| MEM_DUMP | 5 | Dump onboard memory contents | memory_id, address, length |
| MEM_CHECK | 9 | Check onboard memory CRC | memory_id, address, length |

#### Onboard Monitoring (S12)

| Command | Subtype | Description | Fields |
|---|---|---|---|
| MON_ENABLE | 1 | Enable parameter monitoring | (none) |
| MON_DISABLE | 2 | Disable parameter monitoring | (none) |
| MON_ADD_DEF | 6 | Add monitoring definition | param_id, low_limit, high_limit |
| MON_DELETE_DEF | 7 | Delete monitoring definition | param_id |

#### Event-Action (S19)

| Command | Subtype | Description | Fields |
|---|---|---|---|
| EA_ADD | 1 | Add event-action rule | event_id, action_tc (embedded TC) |
| EA_DELETE | 2 | Delete event-action rule | ea_id |
| EA_ENABLE | 4 | Enable event-action rule | ea_id |
| EA_DISABLE | 5 | Disable event-action rule | ea_id |

#### General Services

| Service | Commands | Description |
|---|---|---|
| S1 | (TM only) | Request verification reports |
| S3 | HK_REQUEST, HK_ENABLE, HK_DISABLE, HK_SET_INTERVAL | Housekeeping for SID 4 (Platform/OBDH), SID 10 (BootLoader) |
| S5 | EVENT_ENABLE, EVENT_DISABLE | Event report control |
| S17 | CONNECTION_TEST | Link verification |
| S20 | SET_PARAM, GET_PARAM | Direct parameter read/write for OBDH parameters |

### Telemetry

#### OBDH Parameters (SID 4, 8 s interval)

**OBC Health:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| obdh.mode | 0x0300 | -- | OBC mode (nominal/safe/emergency) |
| obdh.temp | 0x0301 | C | OBC temperature |
| obdh.cpu_load | 0x0302 | % | CPU utilisation |
| obdh.mmm_used | 0x0303 | % | Memory management usage |
| obdh.uptime | 0x0308 | s | Uptime since last reboot |
| obdh.reboot_count | 0x030A | -- | Reboot counter |
| obdh.sw_version | 0x030B | -- | Software version identifier |
| obdh.last_reboot_cause | 0x0316 | -- | Cause: none/watchdog/memory/switchover/commanded |

**Dual-OBC and Redundancy:**

| Parameter | ID | Description |
|---|---|---|
| obdh.active_obc | 0x030C | Active unit (A or B) |
| obdh.obc_b_status | 0x030D | Backup OBC status (OFF/STANDBY) |
| obdh.active_bus | 0x030E | Active CAN bus (A or B) |
| obdh.bus_a_status | 0x030F | Bus A status (OK/ERROR) |
| obdh.bus_b_status | 0x0310 | Bus B status (OK/ERROR) |
| obdh.sw_image | 0x0311 | Running image (bootloader/application) |
| obdh.boot_count_a | 0x0317 | Total boot count OBC-A |
| obdh.boot_count_b | 0x0318 | Total boot count OBC-B |

**TC/TM Counters:**

| Parameter | ID | Description |
|---|---|---|
| obdh.tc_rx_count | 0x0304 | Total received TC count |
| obdh.tc_acc_count | 0x0305 | Accepted TC count |
| obdh.tc_rej_count | 0x0306 | Rejected TC count |
| obdh.tm_pkt_count | 0x0307 | TM packet count |

**Buffers and Storage:**

| Parameter | ID | Units | Description |
|---|---|---|---|
| obdh.hktm_buf_fill | 0x0312 | % | HK/TM buffer fill level |
| obdh.event_buf_fill | 0x0313 | % | Event buffer fill level |
| obdh.alarm_buf_fill | 0x0314 | % | Alarm buffer fill level |

**Flight Hardware Realism (Phase 4):**

| Parameter | ID | Units | Description |
|---|---|---|---|
| obdh.seu_count | 0x0319 | -- | Single-event upset counter |
| obdh.scrub_progress | 0x031A | % | Memory scrub progress |
| obdh.task_count | 0x031B | -- | Active OS task count |
| obdh.stack_usage | 0x031C | % | Stack memory usage |
| obdh.heap_usage | 0x031D | % | Heap memory usage |
| obdh.mem_errors | 0x031E | -- | Memory error count |

#### BootLoader Telemetry (SID 10, 16 s interval)

Available when OBC is in boot loader (sw_image=0). Minimal parameter set:
- obdh.active_obc, obdh.active_bus, eps.bus_voltage, obdh.temp, obdh.uptime, obdh.reboot_count, obdh.sw_image, obdh.last_reboot_cause

#### Limit Monitoring

| Parameter | Yellow | Red |
|---|---|---|
| obdh.cpu_load | 0 -- 85 % | 0 -- 98 % |
| obdh.reboot_count | 0 -- 2 | 0 -- 5 |
| obdh.hktm_buf_fill | 0 -- 80 % | 0 -- 95 % |
| obdh.event_buf_fill | 0 -- 80 % | 0 -- 95 % |
| obdh.alarm_buf_fill | 0 -- 80 % | 0 -- 95 % |
| obdh.seu_count | 0 -- 10 | 0 -- 50 |
| obdh.stack_usage | 0 -- 80 % | 0 -- 95 % |
| obdh.heap_usage | 0 -- 80 % | 0 -- 95 % |

### Display Widgets

**OBC Status page:** CPU load gauge (0-100%); value table of mode, uptime, reboot_count, tc_rx/acc/rej counts; active OBC indicator; SW image indicator.
**Bus & Buffers page:** Active bus, Bus A/B status indicators; HK TM, Event, Alarm buffer gauges; value table of obc_b_status, last_reboot_cause, boot_count_a, boot_count_b.
**OBC Trends page:** CPU load chart (10 min); buffer fill chart (10 min).

## 3. Inter-Position Coordination Needs

| Scenario | Coordinating With | Coordination Details |
|---|---|---|
| Initial health check (LEOP-002) | flight_director, eps_tcs | Verify OBDH nominal after eps_tcs confirms power/thermal |
| OBDH checkout (COM-007) | flight_director | FD authorizes; fdir_systems tests OBC, bus, memory |
| FDIR configuration (COM-008) | flight_director | FD approves FDIR rules; fdir_systems configures S12 monitoring and S19 event-actions |
| Software upload (NOM-006) | flight_director | FD GO/NO-GO at each stage; fdir_systems executes MEM_LOAD, MEM_CHECK, OBC_BOOT_APP |
| Clock sync (NOM-008) | flight_director | FD verifies time delta; fdir_systems executes SET_TIME (via S9 is not in fdir_systems service list -- typically coordinated through FD) |
| Watchdog recovery (CTG-010) | flight_director | FD authorizes; fdir_systems verifies OBC recovered, checks reboot_count and last_reboot_cause |
| OBC switchover (CTG-011) | flight_director | FD authorizes OBC_SWITCH_UNIT; fdir_systems executes and verifies new OBC online |
| Bus failure (CTG-017) | flight_director | FD authorizes OBC_SELECT_BUS; fdir_systems switches bus and verifies all nodes responding |
| Boot loader recovery (CTG-018) | flight_director | FD authorizes; fdir_systems manages boot sequence (OBC_BOOT_INHIBIT, MEM_CHECK, OBC_BOOT_APP) |
| OBC reboot (EMG-003) | flight_director | FD authorizes OBC_REBOOT; fdir_systems executes and monitors recovery via SID 10 |

### FDIR Rules Managed by This Position

The fdir_systems position configures and maintains all onboard FDIR rules through S12 (Monitoring) and S19 (Event-Action):

| FDIR Rule | Parameter | Condition | Level | Action |
|---|---|---|---|---|
| EPS low SoC | eps.bat_soc | < 20% | 1 | payload_poweroff |
| EPS critical SoC | eps.bat_soc | < 15% | 2 | safe_mode_eps |
| EPS undervoltage | eps.bus_voltage | < 26 V | 2 | safe_mode_eps |
| TCS battery hot | tcs.temp_battery | > 42 C | 1 | heater_off_battery |
| TCS battery cold | tcs.temp_battery | < 1 C | 1 | heater_on_battery |
| AOCS attitude loss | aocs.att_error | > 5 deg | 2 | safe_mode_aocs |
| OBDH overtemp | obdh.temp_obc* | > 65 C | 2 | safe_mode_obc |
| Excessive reboots | obdh.reboot_count | > 4 | 3 | spacecraft_emergency |
| RW overtemp (x4) | aocs.rw1-4_temp | > 65 C | 1 | disable_rw1-4 |

*Note: fdir.yaml references `obdh.temp_obc` but the actual parameter is `obdh.temp` (known config issue, xfail in test suite).*

## 4. GO/NO-GO Responsibilities

The FDIR / Systems position provides GO/NO-GO input to the Flight Director for:

- **OBC health:** Confirm cpu_load nominal, reboot_count acceptable, no memory errors, sw_image=application, no watchdog events.
- **Software upload readiness:** Confirm OBC stable (uptime sufficient, no recent reboots), memory CRC verified, bus healthy.
- **FDIR configuration:** Confirm all monitoring definitions active (S12 enabled), event-action rules enabled (S19), correct thresholds loaded.
- **Bus health:** Confirm both CAN buses available (bus_a_status=OK, bus_b_status=OK), active bus communicating normally.
- **Post-anomaly clearance:** After watchdog, switchover, or reboot, confirm: correct sw_image running, HK flowing normally, reboot_count stable, all buffers draining.

**Critical Decision Points:**
- If reboot_count exceeds 2 (yellow), recommend investigating root cause before further commanding.
- If reboot_count exceeds 4 (red), FDIR will trigger spacecraft_emergency autonomously; fdir_systems must coordinate with FD immediately.
- If cpu_load exceeds 85% (yellow), investigate task_count and recommend deferring non-essential commanding.
- If seu_count is increasing, recommend triggering OBC_MEMORY_SCRUB.
- If stack_usage or heap_usage exceeds 80% (yellow), alert FD to potential OBC instability.
- If bus_a_status or bus_b_status shows ERROR, recommend bus switchover (CTG-017) before the healthy bus also fails.
- During boot loader recovery: confirm sw_image=0 (boot loader), use SID 10 for minimal telemetry, execute careful MEM_CHECK before OBC_BOOT_APP.
- OBC_REBOOT and OBC_SWITCH_UNIT are criticality=critical commands; always require explicit FD authorization.

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*
