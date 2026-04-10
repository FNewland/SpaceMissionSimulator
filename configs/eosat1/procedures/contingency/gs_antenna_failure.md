# PROC-TTC-OFF-003: Ground Station Antenna Failure Response

**Category:** Contingency
**Position Lead:** TT&C
**Cross-Position:** Flight Director
**Difficulty:** Intermediate

## Objective
Respond to a ground station equipment failure that causes loss of signal during a
pass. This procedure guides the operator through confirming that the anomaly is on
the ground side (not the spacecraft), coordinating with the Flight Director to switch
to an alternate ground station, and managing the operational impact of a lost or
shortened contact window.

## Prerequisites
- [ ] Link was established and nominal before signal loss occurred
- [ ] Signal loss is sudden and complete (not gradual degradation)
- [ ] Spacecraft TTC telemetry was nominal immediately before the drop
- [ ] Flight Director is on console and available for coordination
- [ ] Alternate ground station contact information is available

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| ttc.link_status | 0x0501 | 0 (lost) — was 1 before failure |
| ttc.rssi | 0x0502 | Dropped below receiver threshold |
| ttc.link_margin | 0x0503 | Was > 3 dB before failure |
| ttc.carrier_lock | 0x0510 | 0 (lost) — was 1 before failure |
| ttc.contact_elevation | 0x050A | Record value at time of loss |
| ttc.gs_equipment_status | 0x0524 | GS equipment status bitmask |
| ttc.active_gs | 0x0523 | Current ground station index |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | -- | Request one-shot HK report (blind) |
| CONNECTION_TEST | 17 | 1 | -- | S17 connection test (blind) |

## Procedure Steps

### Step 1: Confirm Signal Loss Is Not Spacecraft-Side
**Action:** Review the last received telemetry frame before signal loss:
- `ttc.rssi` (0x0502) — was nominal (> -90 dBm)?
- `ttc.link_margin` (0x0503) — was healthy (> 3 dB)?
- `ttc.pa_on` (0x0516) — was PA active?
- `ttc.pa_temp` (0x050F) — was PA temperature normal (< 55 C)?
**Action:** Check the signal loss characteristics:
- Sudden drop to noise floor = equipment failure (antenna, LNA, cabling)
- Gradual degradation = atmospheric, pointing, or spacecraft issue
**Action:** Verify ground station antenna tracking status:
- Is the antenna still tracking? Check antenna controller for faults.
- Is the feed/LNA powered? Check ground station equipment status panel.
**Action:** Check ground station receive chain:
- LNA power supply
- Downconverter lock status
- IF/baseband processing
**Note:** A 30 dB signal drop with a healthy spacecraft strongly indicates a ground
station equipment failure (antenna drive failure, LNA failure, or feed fault).
**GO/NO-GO:** Ground station equipment failure confirmed (or strongly suspected) —
proceed to Step 2. If spacecraft-side suspected, follow PROC-TTC-OFF-002 or
PROC-TTC-LINK-LOSS instead.

### Step 2: Attempt Local Ground Station Recovery
**Action:** Check if the antenna can be reset or re-pointed:
- Issue antenna controller reset if available
- Command re-acquisition of satellite track
**Action:** If LNA or receive chain failure is suspected:
- Switch to backup LNA if available
- Check all RF cable connections and power supplies
**Action:** Wait up to 2 minutes for local GS recovery attempt.
**Verify:** Check receiver for signal return.
**Note:** While attempting GS recovery, the spacecraft continues to transmit normally.
No data is being lost onboard — the HK and payload data continue to be stored in
the spacecraft mass memory.
**GO/NO-GO:** If GS recovers, resume pass operations and log incident. If GS does
NOT recover within 2 minutes, proceed to Step 3.

### Step 3: Notify Flight Director and Assess Impact
**Action:** Report to Flight Director:
- "GS [station name] antenna failure confirmed at [time]. No spacecraft anomaly.
  Spacecraft is healthy, transmitting normally. Ground cannot receive."
**Action:** Assess the impact of the lost contact:
- How much pass time remains at current station?
- Were there critical commands that needed to be sent this pass?
- Is there stored data that urgently needs downlink?
- Are there time-critical operations pending (orbit manoeuvre, payload task)?
**Action:** If critical commands were pending:
- Can they wait until the next scheduled pass?
- Is there an alternate station with overlapping or near-term visibility?
**GO/NO-GO:** Impact assessed — if critical operations are pending, proceed to
Step 4 immediately. If no urgent need, proceed to Step 5 (replanning).

### Step 4: Coordinate Switch to Alternate Ground Station
**Action:** Request Flight Director to authorize contact via alternate ground station.
**Action:** Check next available pass windows at alternate stations:
- Troll (Antarctica) — check visibility window
- Other network stations — check availability and scheduling
**Action:** Contact alternate station operations:
- Request emergency or priority booking
- Provide spacecraft orbital elements and frequency plan
- Confirm antenna capabilities (S/X-band, data rate support)
**Action:** If an alternate station has a pass window within 30 minutes:
- Prepare for handover: provide pass predictions, frequencies, pointing data
- Confirm uplink/downlink configuration matches spacecraft settings
**Action:** If no near-term alternate pass is available:
- Confirm spacecraft autonomous operations will maintain safe state
- Verify onboard schedule has no critical commands requiring ground confirmation
**Note:** The spacecraft does not know it has lost ground contact. It continues
normal operations including any pre-loaded onboard schedule.
**GO/NO-GO:** Alternate station coordinated — or — no alternate available, spacecraft
in safe autonomous state.

### Step 5: Replan Contact Schedule
**Action:** Work with Flight Director to replan the contact schedule:
- Remove the failed station from near-term schedule until repair is confirmed
- Add replacement passes at alternate stations
- Assess cumulative contact time impact over next 24-48 hours
**Action:** Determine if any onboard commands need to be updated:
- Scheduled payload imaging tasks that assume downlink availability
- Battery management during extended no-contact period
- Onboard data storage capacity (risk of overwriting if not downlinked)
**Action:** Notify engineering team of GS failure for repair:
- Provide failure description and time of occurrence
- Request estimated repair time
- Request confirmation before returning station to operational schedule
**Action:** Log the incident:
- Time of GS failure
- Station name and equipment affected
- Duration of lost contact
- Impact on operations (commands not sent, data not downlinked)
- Alternate station used (if any)
**GO/NO-GO:** Schedule replanned, incident logged — procedure complete.

## Verification Criteria
- [ ] Ground station failure confirmed as root cause (not spacecraft)
- [ ] Flight Director notified and impact assessed
- [ ] Alternate station coordinated (if critical operations were pending)
- [ ] Contact schedule replanned around the failed station
- [ ] GS engineering team notified for repair
- [ ] Incident report filed with all relevant details

## Contingency
- If the failure is ambiguous (cannot confirm ground vs spacecraft): Send blind
  commands via the degraded station: `CONNECTION_TEST` (S17.1) and `HK_REQUEST(sid=5)`.
  If no response to multiple blind commands and an alternate station also shows no
  signal, the problem may be spacecraft-side. Follow PROC-TTC-LINK-LOSS.
- If alternate station is not available for > 6 hours: Verify the spacecraft
  autonomous safe mode timer. If the 24-hour no-contact timer will expire before
  next contact, the spacecraft will enter autonomous safe mode. Plan recovery
  accordingly.
- If multiple ground stations are affected simultaneously: Suspect network-level
  issue (NOC failure, frequency interference, solar event). Escalate to network
  operations and Flight Director.
- If the failed station partially recovers (intermittent signal): Attempt to send
  the highest-priority commands during signal windows. Use low data rate for more
  robust uplink: prepare `TTC_SET_DATA_RATE(rate=0)` (func_id 52) for uplink at
  next solid contact.
- If critical time-tagged commands were on the onboard schedule and need
  cancellation: These cannot be cancelled without uplink. Assess the consequences
  of the scheduled commands executing autonomously.
