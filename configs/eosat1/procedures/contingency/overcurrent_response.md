# PROC-EPS-OFF-001: Overcurrent Response

**Category:** Contingency
**Position Lead:** Power & Thermal (EPS/TCS)
**Cross-Position:** Affected subsystem position (AOCS, TT&C, Payload, or FDIR/Systems)
**Difficulty:** Intermediate

## Objective
Respond to an overcurrent trip event on one or more EPS power lines. This procedure
identifies which line(s) tripped, assesses per-line current to determine if the fault
is transient or persistent, isolates the fault, resets the overcurrent flag, re-enables
the affected power line, and verifies nominal operation of the downstream subsystem.

## Prerequisites
- [ ] Overcurrent event detected via telemetry or alarm
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] EPS operator on console or Flight Director aware
- [ ] Spacecraft is not in EMERGENCY mode — `obdh.mode` (0x0300) != 2

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| eps.oc_trip_flags | 0x010D | Non-zero bitmask indicates tripped line(s) |
| eps.line_current_0 | 0x0118 | OBC line current |
| eps.line_current_1 | 0x0119 | TTC RX line current |
| eps.line_current_2 | 0x011A | TTC TX line current |
| eps.line_current_3 | 0x011B | Payload line current |
| eps.line_current_4 | 0x011C | FPA cooler line current |
| eps.line_current_5 | 0x011D | Battery heater line current |
| eps.line_current_6 | 0x011E | OBC heater line current |
| eps.line_current_7 | 0x011F | AOCS wheels line current |
| eps.pl_obc | 0x0110 | Power line OBC status |
| eps.pl_ttc_tx | 0x0112 | Power line TTC TX status |
| eps.pl_payload | 0x0113 | Power line Payload status |
| eps.pl_fpa_cooler | 0x0114 | Power line FPA cooler status |
| eps.pl_aocs_wheels | 0x0117 | Power line AOCS wheels status |
| eps.bus_voltage | 0x0105 | > 27.0 V |
| eps.bat_soc | 0x0101 | Record current value |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| EPS_RESET_OC_FLAG | 8 | 1 | 15 | Reset overcurrent trip flag and re-enable line |
| EPS_POWER_ON | 8 | 1 | 13 | Switch power line ON |
| EPS_POWER_OFF | 8 | 1 | 14 | Switch power line OFF |

## Procedure Steps

### Step 1: Identify Tripped Power Line(s)
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.oc_trip_flags` (0x010D) — decode bitmask to identify tripped line(s):
- Bit 0: OBC (line 0) — non-switchable, critical
- Bit 1: TTC RX (line 1) — non-switchable, critical
- Bit 2: TTC TX (line 2)
- Bit 3: Payload (line 3)
- Bit 4: FPA cooler (line 4)
- Bit 5: Battery heater (line 5)
- Bit 6: OBC heater (line 6)
- Bit 7: AOCS wheels (line 7)
**Note:** Record which line(s) tripped and the time of detection.
**GO/NO-GO:** Tripped line(s) identified — proceed to current assessment.

### Step 2: Check Per-Line Current for Fault Assessment
**Action:** Request EPS housekeeping: `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** Check the per-line current for the tripped line (0x0118 through 0x011F):
- If current is near 0 A: Line has been disconnected by the LCL (expected after trip).
- If current is still elevated: Persistent fault — do NOT re-enable. Isolate and
  investigate.
**Verify:** Check power line status registers (0x0110 through 0x0117) to confirm line
is OFF after the trip.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V — bus is stable without the tripped load.
**Action:** Record all line currents for anomaly report.
**GO/NO-GO:** If persistent overcurrent detected on the line, do NOT proceed to reset.
Escalate to engineering team. If current is nominal (near 0), proceed.

### Step 3: Isolate Fault — Coordinate with Affected Subsystem
**Action:** Notify the position operator responsible for the affected subsystem:
- Line 2 (TTC TX): Notify TT&C operator
- Line 3 (Payload): Notify Payload operator
- Line 4 (FPA cooler): Notify Payload/EPS-TCS operator
- Line 5 (Battery heater): EPS/TCS operator handles directly
- Line 6 (OBC heater): EPS/TCS operator handles directly
- Line 7 (AOCS wheels): Notify AOCS operator
**Action:** Affected subsystem operator confirms the subsystem was in a known state
before the trip and there are no indications of hardware damage.
**Verify:** If the trip occurred during a mode transition or power-on event, assess
whether inrush current may have caused a transient trip.
**GO/NO-GO:** Fault assessed as likely transient — proceed to reset. If hardware
damage suspected, HOLD and escalate.

### Step 4: Reset Overcurrent Flag
**Action:** Reset the overcurrent trip flag for the affected line:
`EPS_RESET_OC_FLAG(line_index=N)` (func_id 15) where N is the tripped line index (0-7).
**Verify:** `eps.oc_trip_flags` (0x010D) — confirm the bit for the affected line has cleared.
**Verify:** Wait 5 s and re-check `eps.oc_trip_flags` to confirm flag remains cleared.
**GO/NO-GO:** OC flag cleared — proceed to re-enable.

### Step 5: Re-Enable Power Line
**Action:** Re-enable the power line: `EPS_POWER_ON(line_index=N)` (func_id 13)
**Verify:** Power line status register (0x0110 + N) = 1 (ON) within 5 s
**Verify:** Per-line current (0x0118 + N) returns to expected nominal value
**Verify:** `eps.oc_trip_flags` (0x010D) remains clear (no re-trip)
**Action:** Wait 30 s to confirm line is stable.
**GO/NO-GO:** Line re-enabled and stable — proceed to verification.

### Step 6: Verify Downstream Subsystem Recovery
**Action:** Request housekeeping for the affected subsystem:
- AOCS wheels: `HK_REQUEST(sid=2)` — verify `aocs.mode` (0x020F), wheel speeds
- Payload: `HK_REQUEST(sid=6)` — verify `payload.mode` (0x0600)
- TTC TX: `HK_REQUEST(sid=5)` — verify `ttc.link_status` (0x0501)
- Heaters: `HK_REQUEST(sid=3)` — verify heater status and temperatures
**Verify:** Downstream subsystem is functioning nominally.
**Verify:** `eps.power_cons` (0x0106) reflects the re-added load.
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V with load restored.
**GO/NO-GO:** Subsystem nominal and power budget stable — procedure complete.

## Verification Criteria
- [ ] `eps.oc_trip_flags` (0x010D) = 0 (all flags cleared)
- [ ] Affected power line status = ON
- [ ] Per-line current within expected nominal range
- [ ] Downstream subsystem operating nominally
- [ ] Bus voltage stable > 27.0 V
- [ ] No re-trip within 5 minutes of re-enable

## Contingency
- If overcurrent flag re-trips immediately after reset: Do NOT attempt a third reset.
  The fault is persistent. Command `EPS_POWER_OFF(line_index=N)` to isolate.
  Escalate to engineering team for hardware assessment.
- If re-trip occurs within 5 minutes of re-enable: Possible intermittent fault. Command
  `EPS_POWER_OFF(line_index=N)`. Wait for next contact to retry. If trip recurs,
  treat as persistent fault.
- If the tripped line is OBC (line 0) or TTC RX (line 1): These are non-switchable
  critical lines. An OC trip on these lines indicates a severe anomaly. Escalate
  immediately to Flight Director and engineering team.
- If bus voltage drops after re-enabling the line: The load may be drawing excessive
  current. Immediately command `EPS_POWER_OFF(line_index=N)` and investigate.
- If multiple lines tripped simultaneously: Suspect a bus-level fault rather than a
  single-line issue. Check `eps.bus_voltage`, `eps.bat_voltage`, and `eps.bat_current`.
  Escalate to Flight Director.

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).
