# SMO Simulator — Functionality Audit & Fix Report

**Date:** 2026-06-20
**Scope:** Three reported bugs, a full MCS command↔handler / telemetry↔display audit, and a new ADCS per-axis torque-gain feature.
**Suite status:** 529 targeted tests green (full `smo-simulator` suite 461/461). One pre-existing, unrelated failure documented in §6.

---

## 1. Executive summary

| Item | Type | Status | Defect |
| --- | --- | --- | --- |
| Failure/scenario scripts never stop at end of duration | Bug (reported) | Fixed | #34 |
| Breakpoint orbital position not restored (propagator clock uncaptured) | Bug (reported) | Fixed | #35 |
| Eclipse/sunlight never displayed in MCS (key mismatch) — the *visible* half of the breakpoint bug | Bug (audit) | Fixed | #36 |
| OBC powers up in **application** instead of **bootloader** | Bug (reported) | Fixed | #37 |
| S8 commands 100–107 (legacy EPS/TCS quick-actions) silently dropped | Dead command | Fixed | #38 |
| S8 func 49 `TCS_GET_THERMAL_MAP` returns no telemetry | Partial command | Fixed | #39 |
| `tc_catalog.yaml` `on`/`off` field names abort the catalog load | Dead-on-load | Fixed | #40 |
| ADCS per-axis control-torque gain via S6 memory load/dump | New feature | Delivered | — |

The recurring root cause behind the OBC bug and several others is a **stale S8 function-id map**: configs (S19 event-action rules) and catalog entries were written against an older numbering than `service_dispatch.py` actually routes. That map has now been reconciled.

---

## 2. Reported bugs — root cause & fix

### 2.1 Scenarios don't stop at the end (#34)
`ScenarioEngine.tick()` only *logged* "duration expired" when a scenario's `duration_s` elapsed; it never deactivated the scenario, so it ran indefinitely (the engine loop ticks a scenario only while `is_active()`). Fixed by an idempotent `_finish()` that builds and caches the debrief, **clears the failures the scenario itself injected**, resets event flags so the scenario can be re-run, and sets the scenario inactive. A new `GET /api/scenario/debrief` endpoint exposes the result. *Tests:* `tests/test_scenario_autostop.py` (5).

### 2.2 Breakpoint orbital position + eclipse not restored (#35)
The SGP4 propagator keeps its **own** clock (`_sim_utc`), advanced separately from `engine._sim_time`; spacecraft position and `in_eclipse` are derived solely from it. The breakpoint snapshot never captured that clock, so a loaded breakpoint left the orbit wherever it had drifted (measured: 54 min / ~7,100 km off after a representative run) and the eclipse/sunlight telemetry diverged. Fixed by capturing `orbit_utc` on save and restoring it via `OrbitPropagator.reset()` on load (with a `sim_time` fallback for older snapshots). *Tests:* `tests/test_breakpoint_orbit.py` (2, written failing-first).

### 2.3 Eclipse never *shown* in the MCS (#36) — why 2.2 still "looked" broken
Even with the engine restore correct, the MCS would still show the wrong eclipse state: the simulator emits the key `in_eclipse`, but the MCS power-budget display read `eclipse_active` / `time_to_eclipse_entry_s` / `time_to_eclipse_exit_s`, **none of which were ever produced**. The badge was permanently "sunlit" and the countdown never appeared. Fixed by aligning the display to `in_eclipse` and adding a bounded forward-scan (`OrbitPropagator.next_eclipse_transition`) that now produces real `in_eclipse` + time-to-eclipse fields in the engine state summary and MCS passthrough. *Tests:* `tests/test_mcs/test_eclipse_display.py` (5). **If the breakpoint eclipse still looked wrong to you, this display bug was the visible cause.**

### 2.4 OBC starts in application, not bootloader (#37)
Construction is actually **correct** — `engine._enter_bootloader_mode()` forces `sw_image = 0` at startup. The flip happens ~10 s into the run: at cold start the TTC bit-error-rate edge emits event `0x050C`, which matches S19 event-action rule `ea_id 4003`. That rule used `action_func_id: 55` believing func 55 = "set TX power" — but **func 55 is `obc_boot_app`** (the real set-TX-power is TTC func 68). So the spacecraft autonomously booted its own application image. An audit of the whole `s19_rules.yaml` found ~17 of 26 rules referencing the stale func map; all were reconciled against the authoritative routing in `service_dispatch.py`, choosing no-harm targets where intent was ambiguous (e.g. avoiding reboot-on-reboot loops). Added defense-in-depth: the autonomous S19 dispatch path now refuses OBC-critical funcs (52/53/55/56) while the OBC is in bootloader. *Tests:* `tests/test_obc_startup_bootloader.py`.

---

## 3. New feature — ADCS per-axis torque gain (S6 memory load/dump)

Operators can now set the control-torque gain on each ADCS axis and read the current values back, using the existing PUS Service 6 memory-management path:

| Address | Register | Encoding |
| --- | --- | --- |
| `0x20100000` | `torque_gain_x` | IEEE-754 big-endian float32 |
| `0x20100004` | `torque_gain_y` | IEEE-754 big-endian float32 |
| `0x20100008` | `torque_gain_z` | IEEE-754 big-endian float32 |

- **MEM_LOAD (S6.2)** to these addresses writes the gains into the AOCS model; **MEM_DUMP (S6.5→S6.6)** returns the *live* gains.
- The attitude control law multiplies each axis's commanded correction by its gain, so a higher gain makes that axis respond more strongly and a lower gain more weakly. Default **1.0 (unity)** keeps existing behaviour unchanged.
- Gains are part of AOCS `get_state`/`set_state`, so **breakpoints persist them**.
- MCS commands added to `tc_manager.py` + `tc_catalog.yaml`: `AOCS_SET_TORQUE_GAIN_X/Y/Z`, `AOCS_SET_TORQUE_GAINS_ALL`, `AOCS_DUMP_TORQUE_GAINS`.

*Tests:* `tests/test_adcs_torque_gain.py` (11) — per-axis scaling, axis independence, dispatcher load→dump round-trip, breakpoint persistence.

---

## 4. Command map (audit)

PUS services are issued by the MCS from `configs/eosat1/commands/tc_catalog.yaml` and routed by `ServiceDispatcher.dispatch` → `_handle_sN` → model `handle_command()`.

| Service | Status | Notes |
| --- | --- | --- |
| S2 Device Access | WIRED | on/off + status, all 6 models |
| S3 Housekeeping | WIRED | enable/disable/report |
| S5 Event Reporting | WIRED | |
| S6 Memory | WIRED | load/dump/check; now also the ADCS gain registers (§3) |
| **S8 Function Mgmt** | WIRED (after fixes) | func 0–83 routed; **100–107 were DEAD → now routed (#38)**; **func 49 was PARTIAL → now returns TM (#39)** |
| S9 Time | WIRED | |
| S11 Scheduling | WIRED | time-tagged TCs bypass the uplink gate by design |
| S12 Monitoring | WIRED | |
| S13 Large Data | WIRED | |
| S15 Onboard Storage | WIRED | |
| S17 Connection Test | WIRED | |
| **S19 Event-Action** | WIRED but **mis-mapped → fixed (#37)** | rules now match the real func map; bootloader gate added |
| S20 Parameter | WIRED | |

**S8 function routing** (0–15 AOCS, 16–25/81–83 EPS, 26–39 payload, 40–49 TCS, 50–62/80 OBDH, 63–78 TTC) was confirmed reachable end-to-end; the only gaps were funcs 49 and 100–107, both fixed.

**Instructor-UI commands:** every `type` the instructor UI POSTs to `/api/command` now has a matching engine branch, and unknown types are logged (the old silent-drop class is closed). Engine branches reachable only by direct API (no shipped UI button) — `inject`, `clear_failure`, `set_phase`, `pause_scenario`, `start_separation` — are noted as a possible UI follow-up, not defects.

---

## 5. Telemetry map (audit)

- **Eclipse / sunlight:** producer key `in_eclipse` vs consumer key `eclipse_active` mismatch, plus two consumer fields with no producer at all — fixed under #36 (see §2.3).
- **All HK SID parameters** (0x01xx EPS, 0x02xx AOCS, 0x03xx OBDH, 0x04xx TCS, 0x05xx TTC, 0x06xx payload) are written by their owning model and packaged by `_emit_hk_packets`; no other display was found reading a parameter the simulator never produces.
- **Pre-existing observability gap (not re-opened here):** `sw_image` (0x0311) and `phase` (0x0129) are still not in any HK SID — tracked as the earlier defect #5.

---

## 6. Verification

Run with the `smo-*` packages editable-installed plus `pytest`, excluding the Playwright browser e2e tests (need a real browser) and the RF-bridge integration test (needs the GNU Radio / rfsim hardware path).

```
tests/test_simulator ............................ 461 passed
tests/test_mcs + test_common + breakpoints ...... 322 passed
new/affected fixes (7 files) .................... 70 passed
```

**One pre-existing, unrelated failure:** `tests/test_commissioning_sequence.py::TestPhase1::test_ttc_lock_acquisition_during_contact` asserts the TTC link reaches LOCKED within 18 s and gets SYNC(1). It fails **identically with every touched file reverted to git HEAD**, so it is not caused by this work — it is a pre-existing TTC lock-acquisition timing issue (sibling to the previously-noted TTC pre-existing failure). Flagged for a separate look.

---

## 7. Operational caveats (please read)

1. **These fixes are uncommitted working-tree edits.** Several (including the breakpoint orbit fix) were already present in the working tree but **not in git HEAD**. They will not reach a clean checkout until committed. Use `scripts/sync_remediation.sh` on your Mac to stage/commit/push.
2. **Restart the sim to pick up changes.** The running simulator serves its UI and config in-process; restart `start.sh` after pulling these edits.
3. **Old breakpoint files** (saved before #35) lack `orbit_utc` and fall back to `sim_time` on load (negligible position error). New saves are exact.
4. **GitHub issue filing:** `scripts/upload_and_file_defects.sh` needs its `ISSUES` array extended for the new defect files #34–#40.
5. **S19 rule re-mapping is broad (#37).** ~17 rules were corrected to no-harm targets; since these are autonomous FDIR responses, a quick domain review of the new `action_func_id` choices against your intended onboard autonomy is worthwhile.

---

*Defect bodies: `defects/34-*.md` … `defects/40-*.md`. Register: `DEFECTS.md` rows 34–40.*
