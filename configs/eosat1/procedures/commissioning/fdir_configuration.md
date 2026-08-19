# COM-008: FDIR Configuration & Protection Functions
**Subsystem:** OBDH / EPS / All
**Phase:** COMMISSIONING
**Revision:** 2.0 --- rewritten 2026-08c. Revision 1.0 read and wrote "FDIR
threshold" parameters 0x0320-0x0331 that are actually TCS heater-duty
telemetry, and its recovery step commanded `OBC_SET_MODE(mode=1)` believing
1 = NOMINAL --- it is SAFE, so the card ended with the OBC in SAFE mode and
every later card inherited it. The real on-board protection interfaces are
Service 12 (parameter monitoring), Service 19 (event-actions), the FDIR rule
table (`fdir.yaml`, ground-configured) and the commanded EPS load-shed
ladder; this revision exercises those, and ends in the state it started.
**Approved:** Flight Operations Director

**Entry state:** COM-007 exit state --- OBC NOMINAL (`obdh.mode` 0x0300 = 0),
AOCS commanded NOMINAL, downlink LOW, payload/cooler lines OFF, no load-shed
latch (`eps.load_shed_stage` 0x0134 = 0).

## Purpose
Confirm the autonomous protection stack is configured and demonstrably alive
before the payload flies: the S12 monitoring service and its definitions, the
S19 event-action table that binds triggers to S8 recovery actions, the FDIR
summary telemetry, and the commanded load-shed ladder with its 2026-08c latch
semantics (commanded stages LATCH; the shed order from nominal is 1 = payload,
2 = + fpa_cooler, 3 = + ttc_tx; stage 0 clears the latch WITHOUT re-powering
anything; events 0x0106-0x0108 fire per stage).

## Prerequisites
- [ ] COM-001 through COM-007 completed
- [ ] OBC in NOMINAL mode (`obdh.mode` 0x0300 = 0)
- [ ] `eps.bat_soc` (0x0101) > 70%
- [ ] Bidirectional S-band link active with link margin > 6 dB
- [ ] Flight Director has approved protection-function testing

## Procedure Steps

### Step 1 --- Enable and Inventory the S12 Monitoring Service
**TC:** `MON_ENABLE` (Service 12, Subtype 1) --- enable on-board parameter monitoring
**TC:** `MON_REPORT_DEFS` (Service 12, Subtype 12) --- request the definition table
**Verify:** TM(12,13) definition report received within 10s
**Action:** Compare the reported monitor set against the operations database
(`monitoring/s12_defs`): every shipped definition present, none unexpected.
**GO/NO-GO:** Monitoring service enabled with the expected definition table

### Step 2 --- Inventory the S19 Event-Action Table
**TC:** `EA_REPORT_DEFS` (Service 19, Subtype 8) --- request the event-action table
**Verify:** TM(19,128) report received within 10s
**Action:** Confirm the shipped rules are present and ENABLED --- in particular
the momentum-management chain (S12 monitor on `aocs.h_env_frac` 0x02BA firing
ea_id 2001, whose action is S8 func 1, the desaturation function) and the TCS
overtemp safing chain (ea 3003). Record the table in the FDIR Configuration
Report.
**GO/NO-GO:** Event-action table matches the operations database

### Step 3 --- Read the FDIR Summary Telemetry
**TC:** `GET_PARAM(param_id=0x0700)` (Service 20, Subtype 3) --- `fdir.level`
**TC:** `GET_PARAM(param_id=0x0701)` (Service 20, Subtype 3) --- `fdir.active_alarms`
**Verify:** `fdir.level` (0x0700) = 0 within 10s --- no latched contingency
**Verify:** `fdir.active_alarms` (0x0701) = 0 within 10s
**Action:** Any non-zero reading here is an unexplained latched alarm ---
resolve it before testing the shed ladder, or the test result is uninterpretable.
**GO/NO-GO:** FDIR quiescent --- clean baseline for the functional test

### Step 4 --- Functional Test: Commanded Load-Shed Latch
**Action:** Record the entry state of the shed-order lines: `eps.pl_payload`
(0x0113), `eps.pl_fpa_cooler` (0x0114), `eps.pl_ttc_tx` (0x0112). At this
point in commissioning payload and cooler are OFF (their cards have not run),
so stage 1 changes no line --- the test observes the LATCH and its event, which
is exactly the 2026-08c behaviour being commissioned.
**TC:** `EPS_EMERGENCY_LOAD_SHED(stage=1)` (Service 8, Subtype 1, func_id 25) --- command stage 1
**Verify:** `eps.load_shed_stage` (0x0134) = 1 within 5s --- the COMMANDED stage
LATCHES (it used to be overwritten to 0 by the next tick)
**Verify:** Event `LOAD_SHED_STAGE_1` (0x0106) in the event log within 10s
**Verify:** `eps.pl_payload` (0x0113) = 0 --- the payload rung is shed
**Note:** Do NOT command stage 2 or 3 here: stage 3 sheds `ttc_tx` and drops
this very downlink. The staged walk-down belongs to CTG-001/CTG-023 where the
spacecraft is actually in trouble; commissioning proves the latch and the
clear.
**TC:** `EPS_EMERGENCY_LOAD_SHED(stage=0)` (Service 8, Subtype 1, func_id 25) --- clear the latch
**Verify:** `eps.load_shed_stage` (0x0134) = 0 within 5s
**Verify:** Every line flag recorded above is UNCHANGED --- stage 0 clears the
latch and re-powers NOTHING; restoration is always an explicit operator act
**GO/NO-GO:** Latch set, evented, cleared; no line re-powered by stage 0

### Step 5 --- Confirm No Residual Protection State
**TC:** `GET_PARAM(param_id=0x0700)` (Service 20, Subtype 3) --- `fdir.level`
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `fdir.level` (0x0700) = 0 within 10s
**Verify:** `eps.load_shed_stage` (0x0134) = 0 within 10s
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10s --- this card makes NO
OBC-mode excursion and must not end with a SAFE OBC (the rev-1.0 defect)
**GO/NO-GO:** Protection stack armed, spacecraft state exactly as at entry

## Off-Nominal Handling
- If TM(12,13) or TM(19,128) is empty or missing definitions: reload the monitoring/event-action configuration from the operations database (`MON_ADD_DEF` / `EA_ADD` per the database record) and re-request the report.
- If `eps.load_shed_stage` does not latch at Step 4: the 2026-08c shed handler fix has regressed --- raise a simulator/FSW defect; do not proceed to payload commissioning with an unproven shed ladder.
- If stage 0 re-powers any line: same --- that is the specific defect the 2026-08c fix removed. Record and escalate.
- If `fdir.level` != 0 at Step 3 or 5: read the FDIR journal, identify the latched rule, clear the underlying cause. Never mask it by disabling the rule to pass the card.

## Exit state
- IDENTICAL to the entry state: OBC NOMINAL, no shed latch, no line changed,
  S12 monitoring enabled (it was enabled before; Step 1 is idempotent)
- Protection stack inventoried and functionally demonstrated

## Post-Conditions
- [ ] S12 definitions and S19 event-actions verified against the database
- [ ] Load-shed latch semantics demonstrated (latch, event, clear, no re-power)
- [ ] FDIR summary telemetry quiescent
- [ ] OBC confirmed NOMINAL at exit
- [ ] FDIR Configuration Report generated

**NEXT:** COM-009 (Payload Power On)
