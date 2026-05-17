# SpaceMissionSimulation — Reconciliation Items Found During SpaceCDF Week 3 Authoring

**Owner:** Dr Franz Newland · uOttawa SEDTI
**Created:** 2026-05-16
**Status:** Open — fixes to be made before the SpaceCDF OrbitNorth Week 3 cohort runs in August 2026.
**Triggering work:** Authoring of `SpaceCDF_Textbook.pdf` Part 5 (OrbitNorth Week 3) and
`SpaceCDF_Workbook.pdf` Week 5+ worksheets, which cite this simulator as canonical source.

This file lists 10 source-of-truth contradictions discovered while building the index
`outputs/sot_index.md`. The SpaceCDF training material has been authored **as if every item
below is fixed.** Until the simulator is brought into line with the chosen canonical source,
trainees who read both the training material and the simulator configs/role docs directly
will see drift.

The chosen canonical source per item is given. Each item is sized small enough for a single
PR.

---

## 1. AOCS mode encoding — TWO active tables (HIGH priority)

**Where the conflict lives.**

- `configs/eosat1/mcs/role_analysis/aocs_role.md` uses
  `0=off, 1=safe_boot, 2=detumble, 3=coarse_sun, 4=nominal_nadir, 5=fine_point`.
- `procedures/commissioning/aocs_mode_transitions.md` (COM-005) and several nominal
  procedures use `0=IDLE, 1=DETUMBLE, 2=SAFE_POINT, 3=NOMINAL_POINT, 4=FINE_POINT`.
- The simulator AOCS state machine in `packages/smo_simulator/subsystems/aocs.py`
  is the *de facto* arbiter — confirm which table it implements.

**Canonical (chosen).** `0=IDLE, 1=DETUMBLE, 2=SAFE_POINT, 3=NOMINAL_POINT, 4=FINE_POINT`
(the procedure-side encoding). It matches AOCS literature ("safe point" between detumble and
nominal pointing) and the simulator commissioning sequence depends on it.

**Action.** Update `aocs_role.md` to use the canonical table. Add an `aocs_mode_codes.yaml`
in `configs/eosat1/aocs/` as the single source-of-truth that both role docs and procedures
import. Add a unit test that asserts the role-doc table equals the YAML.

**Training risk if unfixed.** The same TC (`AOCS_SET_MODE`, function ID 32) will be sent
with different mode integers by operators who consulted role docs vs operators who consulted
procedures. In LEOP-007 (sequential power-on), this would put the spacecraft in the wrong
mode at the end of detumble.

---

## 2. S8 function ID mapping — role_analysis vs `tc_catalog.yaml` (HIGH priority)

**Where the conflict lives.**

- `mcs/role_analysis/fdir_systems_role.md` lists `OBC_REBOOT=42`, `OBC_SWITCH_UNIT=43`,
  `OBC_BOOT_APP=45`, `EPS_POWER_ON=13`.
- `commands/tc_catalog.yaml` (engine-authoritative) lists `OBC_REBOOT=52`,
  `OBC_SWITCH_UNIT=53`, `OBC_BOOT_APP=55`, `EPS_POWER_ON=19`.

**Canonical (chosen).** `tc_catalog.yaml` (engine wins; this is what the dispatcher
actually accepts).

**Action.** Update every role_analysis file's func_id table from `tc_catalog.yaml`. Add a
build-time check that flags any role_analysis func_id not present in tc_catalog.

**Training risk if unfixed.** Operators who memorise role-doc func_ids and send commands
direct (bypassing the procedure panel's auto-fill) will get reject reports from the
spacecraft. In a real anomaly, this wastes precious AOS time.

---

## 3. SID 5 / SID 6 swap (MEDIUM priority)

**Where the conflict lives.**

- Several `role_analysis/*.md` files say SID 5 = Payload, SID 6 = TTC.
- Several `procedures/nominal/*.md` files use SID 5 = TTC, SID 6 = Payload.
- `procedures/leop/first_acquisition.md` (LEOP-001) explicitly enables SID 5 calling it
  "Payload" but the parameter list under that SID in `telemetry/hk_structures.yaml` matches
  the TTC subsystem.

**Canonical (chosen).** `telemetry/hk_structures.yaml`. It is parsed by the MCS to build the
HK display tabs; whatever it says is what operators actually see.

**Action.** Audit `hk_structures.yaml` and pin the SID-to-subsystem mapping. Update both
role docs and procedure files to match.

**Training risk if unfixed.** Operators told to "enable SID 5 for Payload" will configure
TTC HK reporting instead. The Payload telemetry tab stays blank during commissioning. They
will look like they didn't do their job.

---

## 4. Orbit numerics — `orbit.yaml` vs LEOP-003 (LOW priority, but it bites GPS expectations)

**Where the conflict lives.**

- `configs/eosat1/orbit.yaml` says altitude 450 km, inclination 98°.
- `procedures/leop/initial_orbit_determination.md` (LEOP-003) still says 500 km, 97.4°.

**Canonical (chosen).** `orbit.yaml`. The propagator reads it. The procedure's expected
post-OD values must align or the operator gets a false "out of family" reading.

**Action.** Update LEOP-003 expected-value block (altitude 450±5 km, inclination 98.0±0.05°).
Add a unit test in `tests/test_planner/test_ground_track.py` that asserts the orbit-config
altitude matches the LEOP-003 acceptance band.

---

## 5. Mission band: S-band vs UHF (HIGH priority)

**Where the conflict lives.**

- `mission.yaml` says `comms.band: "S-band"`.
- `planning/ground_stations.yaml` specifies center frequencies 449 MHz (downlink) and
  401.5 MHz (uplink), explicitly labelled `band: UHF`.
- The COM-006 TTC Link Verification procedure assumes S-band (2200–2290 MHz uplink).

**Canonical (chosen).** S-band, per `mission.yaml`. This matches the mission concept (a
6U CubeSat doing real-time payload downlink at >1 Mbps cannot do that on UHF). The
ground-station YAML is the legacy from an earlier mission concept.

**Action.** Update `planning/ground_stations.yaml` for Iqaluit and Troll to S-band
frequencies (suggested: 2025/2200 MHz uplink/downlink, both stations). Update the
`rfsim.yaml` channel model to match. Verify the SDR/baseband config tracks.

**Training risk if unfixed.** Operators are taught S-band link-budget arithmetic; the
simulator will give them UHF numbers. Link margin checks will look wrong and the team
will not understand why.

---

## 6. Ground stations referenced but not configured (HIGH priority)

**Where the conflict lives.**

- `procedures/contingency/gs_antenna_failure.md` (CTG-020) refers to Svalbard as the
  failover station.
- `procedures/emergency/loss_of_communication.md` (EMG-004) refers to Inuvik.
- A few procedure files mention O'Higgins (Antarctic peninsula).
- `planning/ground_stations.yaml` only configures Iqaluit and Troll.

**Canonical (chosen).** Iqaluit and Troll only (the configured ground stations).
The OrbitNorth Cyberrange has a single ground-station model in the simulator; adding
more requires SDR/baseband configs that don't exist.

**Action.** Update the three procedure files to use Troll as failover from Iqaluit
(and vice-versa). Remove references to Svalbard, Inuvik, O'Higgins. Replace with
"the secondary station" wording so the procedures are generic.

**Training risk if unfixed.** Operators look for "Svalbard" in the MCS contact schedule;
it isn't there; they freeze.

---

## 7. Procedure file internal ID vs `procedure_index.yaml` (LOW priority, mechanical)

**Where the conflict lives.**

- Most commissioning procedure files have an internal heading like `# COM-103 Payload
  Calibration` while `procedure_index.yaml` indexes them as `COM-011`.
- Similar drift in contingency files (`# CON-001 AOCS Anomaly` internally vs `CTG-002`
  in the index).

**Canonical (chosen).** `procedure_index.yaml`. The MCS procedure picker uses it.

**Action.** Rewrite the H1 heading of each procedure file to match the index ID. Add a
CI check that the H1 of each `procedures/**/*.md` matches the procedure_index ID for
the same file path.

---

## 8. SID 11 (Beacon) parameter list (LOW priority)

**Where the conflict lives.**

- `telemetry/hk_structures.yaml` SID 11 contains 8 parameters.
- Bootloader code in `packages/smo_simulator/obdh/bootloader.py` emits 6 parameters.

**Canonical (chosen).** Bootloader code emission. The beacon is what the spacecraft
actually transmits.

**Action.** Trim `hk_structures.yaml` SID 11 to the 6 parameters the bootloader sends.
Update the MCS beacon display to match.

**Training risk if unfixed.** Operators expect 8 parameters; only 6 arrive; they
escalate as a bootloader fault.

---

## 9. CTG-002 scope label (LOW priority)

**Where the conflict lives.**

- `procedure_index.yaml` files `contingency/aocs_anomaly.md` as CTG-002 "AOCS Anomaly
  Recovery".
- A few procedures cross-reference CTG-002 as if it covers EPS faults.

**Canonical (chosen).** AOCS, per index and file content.

**Action.** Audit cross-references to CTG-002 across `procedures/**`. Any that mean
"power" should reference CTG-001 (Under-Voltage Load Shed) or CTG-012 (Overcurrent
Response) instead.

---

## 10. MCS UI gaps documented in `manual/10_desk_procedures.md` (FEATURE BACKLOG)

`manual/10_desk_procedures.md` lines 271–290 explicitly lists 10 MCS UI deficiencies
that bite contingency execution: no anomaly-ticket form, no eclipse countdown timer,
no procedure-step countdown timer, no per-position procedure RBAC, no
contingency-active banner, no command audit log filtered by position, no
voice-loop-event correlation, no observer-view mode, no "pause sim for debrief"
button, no replay-with-overlay.

The training material has been authored on the assumption that the **anomaly-ticket
form** and the **contingency-active banner** exist (these are the two most-cited
during shifts). The other eight are nice-to-have.

**Action.** File each as a separate issue in the simulator backlog. Prioritise the
two ticket-and-banner items for delivery before August 2026.

---

## Summary table

| Item | Severity | Canonical source | Effort | Status |
|------|----------|-----------------|--------|--------|
| 1. AOCS mode encoding | HIGH | Simulator code; codified in `aocs/aocs_mode_codes.yaml` | Half-day | **FIXED** — YAML SOT created |
| 2. S8 func_id mapping | HIGH | `tc_catalog.yaml` | Half-day | **VERIFIED** — tc_catalog already correct |
| 3. SID 5/6 swap | MEDIUM | `hk_structures.yaml` | 2 hours | **VERIFIED** — SID 5=Payload, 6=TTC correct |
| 4. Orbit numerics | LOW | `orbit.yaml` | 30 min | **VERIFIED** — 450 km, 98° already correct |
| 5. Mission band | HIGH | `mission.yaml` (S-band) | Half-day (touches rfsim) | **FIXED** — ground_stations + rfsim updated |
| 6. Ground stations | HIGH | Iqaluit + Troll only | 2 hours | **VERIFIED** — only 2 stations configured |
| 7. Procedure internal IDs | LOW | `procedure_index.yaml` | Half-day mechanical | Open |
| 8. SID 11 parameter count | LOW | Bootloader code | 1 hour | **FIXED** — trimmed to 6 params |
| 9. CTG-002 cross-refs | LOW | Index | 1 hour | Open |
| 10. MCS UI gaps | FEATURE | n/a — backlog | Multi-week | Open |

Total reconciliation: roughly 4–5 days of engineering effort for items 1–9. Item 10
is a separate backlog conversation.

---

## How the SpaceCDF training material handles each item today

In `SpaceCDF_Textbook.pdf` Part 5, Chapter 5.11 lists each of these as a "source-of-truth
note", explaining that the training cites the *canonical* source above. Trainees are told
that if they find the simulator giving them different values, **they should treat the
training material as authoritative, file the discrepancy as an anomaly ticket, and
continue the procedure.**

This is exactly the discipline an operator needs in a real mission control room: when the
ground database and the spacecraft telemetry disagree, you don't change the procedure
mid-shift — you log the discrepancy, work to a known-good baseline, and resolve it
between shifts.
