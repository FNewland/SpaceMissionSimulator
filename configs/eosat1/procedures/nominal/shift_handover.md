# PROC-FD-001: Flight Director Shift Handover

**Category:** Nominal
**Position Lead:** Flight Director
**Cross-Position:** All positions (EPS/TCS, AOCS, TT&C, Payload, FDIR/Systems)
**Difficulty:** Beginner

## Objective
Execute a structured shift handover between outgoing and incoming Flight Directors. This
procedure ensures all subsystem states are reviewed, pending alarms are acknowledged or
dispositioned, upcoming ground station contacts and scheduled commands are verified, and
operational awareness is fully transferred to the incoming shift team.

## Prerequisites
- [ ] Incoming Flight Director and shift team present at consoles
- [ ] All position operators have prepared their subsystem status summaries
- [ ] Pass log from current shift is up to date
- [ ] No active emergency procedures in progress (if emergency is active, handover
  must explicitly include emergency status and ongoing actions)

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| obdh.mode | 0x0300 | 0 (NOMINAL) or current mode |
| eps.bat_soc | 0x0101 | Current value (record) |
| eps.bus_voltage | 0x0105 | > 28.0 V nominal |
| eps.power_gen | 0x0107 | Current value |
| eps.power_cons | 0x0106 | Current value |
| eps.eclipse_flag | 0x0108 | Current sun/eclipse state |
| aocs.mode | 0x020F | Current AOCS mode |
| aocs.att_error | 0x0217 | < 0.1 deg nominal |
| tcs.temp_battery | 0x0407 | Within 0 to 40 C |
| tcs.temp_fpa | 0x0408 | Current value |
| ttc.link_status | 0x0501 | Current link state |
| ttc.tm_data_rate | 0x0506 | Current data rate |
| payload.mode | 0x0600 | Current payload mode |
| payload.store_used | 0x0604 | Current storage level |
| obdh.reboot_count | 0x030A | Current value |
| obdh.tc_rej_count | 0x0306 | Current value |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| LIST_SCHEDULE | 11 | 17 | — | Request schedule summary |

## Procedure Steps

### Step 1: Review All Subsystem States
**Action:** Request full housekeeping sweep across all subsystems:
- `HK_REQUEST(sid=1)` — EPS housekeeping
- `HK_REQUEST(sid=2)` — AOCS housekeeping
- `HK_REQUEST(sid=3)` — TCS housekeeping
- `HK_REQUEST(sid=4)` — OBDH housekeeping
- `HK_REQUEST(sid=5)` — TTC housekeeping
- `HK_REQUEST(sid=6)` — Payload housekeeping
**Verify:** `obdh.mode` (0x0300) — record and announce current OBC mode
**Verify:** `eps.bat_soc` (0x0101), `eps.bus_voltage` (0x0105), `eps.power_gen` (0x0107),
`eps.power_cons` (0x0106) — record power budget summary
**Verify:** `aocs.mode` (0x020F), `aocs.att_error` (0x0217) — record attitude status
**Verify:** `tcs.temp_battery` (0x0407), `tcs.temp_obc` (0x0406) — record thermal status
**Verify:** `ttc.link_status` (0x0501) — record link status
**Verify:** `payload.mode` (0x0600), `payload.store_used` (0x0604) — record payload status
**Action:** Each position operator provides verbal status summary to incoming team:
- EPS/TCS: Power budget, battery trend, thermal status, heater activity
- AOCS: Attitude mode, pointing accuracy, wheel speeds, momentum status
- TT&C: Link status, next contact window, data rate configuration
- Payload: Imaging status, storage capacity, pending downlinks
- FDIR/Systems: OBC health, reboot count, memory usage, active FDIR rules
**GO/NO-GO:** All subsystem states reviewed and understood by incoming team — proceed.

### Step 2: Check Pending Alarms and Anomalies
**Action:** Review alarm buffer via OBDH HK: `HK_REQUEST(sid=4)` (Service 3, Subtype 27),
or review alarm display on MCS console.
**Verify:** `obdh.alarm_buf_fill` (0x0314) — record level
**Verify:** `obdh.tc_rej_count` (0x0306) — record value, compare with shift start baseline
**Action:** Outgoing FD briefs incoming FD on:
- Any active limit violations or warnings
- Any alarms that were acknowledged but not yet resolved
- Any parameters being monitored due to trend concerns
- Any rejected telecommands and their disposition
- Any FDIR actions that fired during the shift
**Action:** Incoming FD acknowledges understanding of all pending items.
**GO/NO-GO:** All alarms reviewed and dispositioned — proceed.

### Step 3: Review Upcoming Ground Station Contacts
**Action:** Review ground station contact schedule for the next 12 hours.
**Verify:** Next contact window: station, AOS/LOS times, maximum elevation
**Verify:** Planned activities for each contact (HK dump, data downlink, command uploads)
**Verify:** Link configuration required (data rate, PA power level)
**Action:** If any contact requires special configuration (e.g., high-rate downlink,
ranging session), brief incoming TT&C operator on required actions.
**GO/NO-GO:** Contact schedule reviewed and understood — proceed.

### Step 4: Verify Stored Commands and Schedule
**Action:** Request onboard schedule summary: `LIST_SCHEDULE` (Service 11, Subtype 17)
**Verify:** Review all time-tagged commands in the schedule:
- Confirm all scheduled commands are still valid and intended
- Verify execution times against updated orbit prediction
- Identify any commands that need to be deleted or modified
**Verify:** `obdh.hktm_buf_fill` (0x0312) — record HK/TM buffer status
**Verify:** `obdh.event_buf_fill` (0x0313) — record event buffer status
**Action:** If any scheduled commands need modification, incoming FD coordinates with
relevant position operator to update.
**GO/NO-GO:** Schedule reviewed and confirmed — proceed.

### Step 5: Formal Handover Declaration
**Action:** Outgoing FD verbally declares:
- Summary of shift highlights (anomalies, procedures executed, notable events)
- Open action items and their owners
- Any constraints or concerns for the upcoming shift
- Any deferred activities and their rationale
**Action:** Incoming FD verbally acknowledges:
- Understanding of current spacecraft state
- Acceptance of all open action items
- Awareness of upcoming planned activities
**Action:** Both FDs log handover time and signatures in the pass log.
**Verify:** Incoming FD confirms they have authority and responsibility.
**GO/NO-GO:** Handover complete — incoming FD has the console.

## Verification Criteria
- [ ] All six subsystem HK reports reviewed and recorded
- [ ] All pending alarms reviewed and dispositioned
- [ ] Upcoming contact schedule reviewed for next 12 hours
- [ ] Onboard command schedule verified
- [ ] Formal handover declaration logged with timestamps
- [ ] Pass log updated and signed by both Flight Directors

## Contingency
- If an anomaly is detected during the handover HK sweep: Pause handover. Outgoing FD
  retains authority and leads the anomaly response. Handover resumes once the anomaly is
  dispositioned or stable.
- If a critical alarm triggers during handover: Outgoing FD retains authority until
  the alarm is resolved or the situation is stable enough to transfer. Both FDs may
  coordinate during this period.
- If telemetry is unavailable (no link): Handover proceeds based on last known
  telemetry state. Incoming FD is briefed on the gap and expected next contact. All
  last-known values are clearly identified as stale.
