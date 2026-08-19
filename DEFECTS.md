# Defect Register — SpaceMissionSoftware

Defects #1–8 were identified during the 2026-04-06 simulator review session.
Defects #9–22 were identified during the 2026-05-24 "dead / unwired code" audit,
which traced every backend capability to a UI/interface and every UI control to a
real backend, across the live suite (`packages/smo-*`) and `tools/`.
Defects #91–133 were identified during the 2026-08 evolution build (phases A–G),
which rebuilt the foundation (determinism, time acceleration, state round-trip),
the power/thermal models, the AOCS, propulsion, the command argument schema, the
ground segment and the simulator core. Each entry
below (except the fileless fixed item #8) has a corresponding body file in
`defects/` which is used verbatim by `scripts/upload_and_file_defects.sh` when
creating GitHub issues.

| #   | Severity | Status     | Title                                                                       |
| --- | -------- | ---------- | --------------------------------------------------------------------------- |
| 1   | Major    | Open       | OBDH "Buffer Fill – HK TM" parameter exceeds 100% (observed 353%)            |
| 2   | Major    | Fixed      | HK_Store sized for <1 orbit of housekeeping (5000 → 18000)                   |
| 3   | Major    | Fixed      | TM packets routed to S15 stores during bootloader (sw_image == 0)            |
| 4   | Major    | Open       | AOCS mode defaults to NOMINAL(4) at construction — "DETUMBLE at start"       |
| 5   | Critical | Open       | `sw_image` (0x0311) and `phase` (0x0129) not in any HK SID — unobservable    |
| 6   | Major    | Open       | MCS has no generic parameter-watch widget / no S20 client                    |
| 7   | Minor    | Open       | Fill % > 100 should be impossible for a circular store — UI contract bug    |
| 8   | Major    | Fixed      | Instructor display shows only ~30 params; no subsystem internals visible     |
| 9   | Critical | Fixed      | Scenario subsystem non-functional — `ScenarioEngine` never instantiated     |
| 10  | Critical | Fixed      | Breakpoint save/load not wired to UI — SAVE no-ops, no LOAD control          |
| 11  | Major    | Fixed      | Heater control broken — OBC heater cmd no-ops; no heater UI affordance       |
| 12  | Major    | Fixed      | "CLEAR ALL FAILURES" button does nothing (HTTP 403 + unhandled WS)           |
| 13  | Major    | Partial    | MCS advanced displays unreachable (`displays.js` never loaded) + 500 crash   |
| 14  | Major    | Partial    | MCS Procedure Builder produces unrunnable procedures; steps skipped open     |
| 15  | Major    | Fixed      | Planner full validation unreachable — only weak name-conflict check wired    |
| 16  | Major    | Open       | Planner backend endpoints (constraints, pass-activity, PUT, targets) no UI   |
| 17  | Major    | Open       | smo-common orphaned PUS service parser — duplicated/diverged in MCS          |
| 18  | Major    | Open       | RF "via GNU Radio" never invoked — entire `gnuradio/` package is dead        |
| 19  | Major    | Open       | Radio dashboard panels (link budget/channel/spectrum/eye) show placeholders  |
| 20  | Minor    | Open       | MCS dead/unwired code & data-shape bugs (consolidated cleanup register)      |
| 21  | Minor    | Open       | Planner + smo-common dead/orphaned code (consolidated cleanup register)      |
| 22  | Minor    | Open       | RFsim + gateway + tools dead/unwired code (consolidated cleanup register)    |
| 23  | Major    | Fixed      | Subsystem-generated events never reach operator (`_engine` unset, queue undrained) |
| 24  | Major    | Fixed      | AOCS reaction-wheel thermal signature broken (temps not in HK; no rise on seizure) |
| 25  | Major    | Fixed      | AOCS `aocs_wheel_failure` scenario injects unhandled `wheel_failure` (no-op)  |
| 26  | Major    | Fixed      | AOCS MAG_SELECT cannot select redundant magnetometer (Mag B)                 |
| 27  | Major    | Fixed      | FDIR/load-shed callbacks invoke commands models don't handle (EPS safing, TTC power) |
| 28  | Major    | Fixed      | TTC `uplink_loss` failure does not suppress the downlink                      |
| 29  | Major    | Fixed      | OBDH bus-failure isolation is cosmetic (`is_subsystem_reachable` never consulted) |
| 30  | Major    | Fixed      | OBDH watchdog inverted; injected watchdog reset can't fire in nominal mode    |
| 31  | Major    | Fixed      | TCS advanced thermal commands inert (decontamination, duty-limit, setpoints)  |
| 32  | Minor    | Open       | Subsystem dead/inert commands & state (consolidated cleanup register)         |
| 33  | Minor    | Open       | Subsystem computed-but-unobservable telemetry (consolidated cleanup register) |
| 34  | Major    | Fixed      | Failure-script scenarios never stop at end of duration                       |
| 35  | Major    | Fixed      | Breakpoint save/load does not restore orbital position or eclipse state       |
| 36  | Major    | Fixed      | Eclipse / sunlight state never displayed — MCS reads keys sim never produces  |
| 37  | Critical | Fixed      | S19 rules mis-map S8 func_ids — autonomous OBC application boot at startup     |
| 38  | Major    | Fixed      | S8 function-management commands 100–107 (legacy EPS/TCS quick-actions) dropped |
| 39  | Minor    | Fixed      | S8 func 49 TCS_GET_THERMAL_MAP acknowledges but returns no telemetry           |
| 40  | Major    | Fixed      | tc_catalog.yaml on/off field names parse as booleans and abort the catalog load |
| 41  | Critical | Fixed      | Contact gating uses first ground station only — all Troll passes dead air      |
| 42  | Major    | Fixed      | Ground-station geometry duplicated across orbit.yaml and planning config       |
| 43  | Major    | Fixed      | TM frame flush leaves stale packet offsets + wrong FHP — silent frame loss     |
| 44  | Major    | Fixed      | Planner "Upload to MCS" discards the scheduled activity start time             |
| 45  | Critical | Fixed      | Planner activity func_ids misroute — imaging hits EPS no-ops, dump hits OBDH   |
| 46  | Critical | Fixed      | MCS S11 builder embeds bare svc+subtype — every time-tagged TC dies malformed  |
| 47  | Major    | Fixed      | MCS contact display schema mismatch; real passes render only when planner down |
| 48  | Major    | Fixed      | On-board scripting absent — no PUS S18 / OBCP capability in the platform       |
| 49  | Major    | Fixed      | SoC-staged load shedding dead — engine reads a non-existent `soc` attribute    |
| 50  | Major    | Fixed      | Power budget does not close in standard ops — shed cascade at t≈86 min         |
| 51  | Minor    | Fixed      | Simulation/scenario parameters hard-coded in Python instead of configuration   |
| 52  | Major    | Fixed      | Scenario loader silently ignores unknown actions and unimplemented failures    |
| 53  | Major    | Fixed      | RF spectrum analyser and eye diagram producers have zero call sites          |
| 54  | Major    | Fixed      | OQPSK mislabelled — no half-symbol offset; four modulations TX-only          |
| 55  | Major    | Fixed      | Radio channel config dead; DSP pipeline symbol rate pinned at 32 kBd         |
| 56  | Major    | Fixed      | SpaceLinkChannel (fading, phase noise, interferers) instantiated nowhere     |
| 57  | Critical | Fixed      | Planner UI shows hard-coded JS stations and sample activities, not config    |
| 58  | Major    | Fixed      | Planner UI runs on wall clock while the backend resolves sim time            |
| 59  | Major    | Fixed      | Planner contact/ground-track caches ignore breakpoint sim-time jumps         |
| 60  | Major    | Fixed      | Data budget never renders (falsy-zero guard) and uses wrong constants        |
| 61  | Major    | Fixed      | Imaging opportunities aim at region centre; solar/revisit limits unused      |
| 62  | Minor    | Fixed      | ADCS failure-scenario viewer existed only in the fork, with stale physics    |
| 63  | Major    | Fixed      | MCS log windows reset scroll every packet and render 80 of 200 rows          |
| 64  | Minor    | Fixed      | TM packet rows carry no SID/RID columns though the data is already sent      |
| 65  | Critical | Fixed      | Live plots fabricate a flat line after LOS — stale telemetry looks live      |
| 66  | Major    | Fixed      | Delayed-TM viewer charts dead (undeclared ChartDataLabels; CUC always 0)     |
| 67  | Minor    | Fixed      | Command builder shows no expected data-byte count; func catalogue hardcoded  |
| 68  | Critical | Fixed      | HK SID table is fiction and sidToHex is little-endian — commands hit wrong SID |
| 69  | Critical | Fixed      | S12 loses 4 of 38 definitions; no check-transition telemetry emitted         |
| 70  | Critical | Fixed      | S19 report returns nothing; 17 of 26 rules dual-triggered by ID namespace clash |
| 71  | Major    | Fixed      | S6 memory map hard-coded in 3 places; patches outside the gain block discarded |
| 72  | Major    | Fixed      | S11 schedule report discards command identity — operator sees an empty table |
| 73  | Minor    | Fixed      | S15 store status report discards name, fill %, overflow and wrap             |
| 74  | Major    | Fixed      | High-rate telemetry not commandable and real-time TM never rate-paced        |
| 75  | Major    | Fixed      | All HK SIDs enabled at boot contrary to the operations manual                |
| 76  | Major    | Fixed      | No FDIR window in the MCS; the FDIR renderer was never loaded                |
| 77  | Major    | Fixed      | Time-tagged builder is a cut-down panel; onboard schedule never displayed    |
| 78  | Minor    | Fixed      | MCS memory regions hard-coded in JS rather than served from the memory map   |
| 79  | Major    | Fixed      | MCS station list hard-coded to 2 stations; no breakpoint-jump detection      |
| 80  | Minor    | Fixed      | tc_catalog metadata gaps — wrong type, missing func 83, missing fields keys  |
| 82  | Major    | Fixed      | 800 MB image size makes any plan infeasible at S-band downlink rates         |
| 83  | Major    | Fixed      | Four divergent procedure representations; builder output was not runnable    |
| 84  | Critical | Fixed      | TT&C link budget subtracts ground-station G/T instead of adding it (40 dB)   |
| 85  | Major    | Fixed      | Two-tier 1 kbps / 64 kbps data-rate plan unusable for routine operations     |
| 86  | Major    | Fixed      | Ground segment assumes a single downlink rate for planning and display      |
| 87  | Major    | Fixed      | Command rejection reason computed on board but never reaches the ground     |
| 88  | Major    | Fixed      | Error codes invisible in the telemetry, event and FDIR displays            |
| 89  | Major    | Fixed      | Quick commands warn "0 data bytes expected" for correct payloads           |
| 90  | Major    | Fixed      | Delayed telemetry dumps unreachable from a networked client                |
| 91  | Major    | Fixed      | No RNG seeding anywhere in `packages/` — no run was reproducible            |
| 92  | Critical | Fixed      | Time acceleration changed the physics; noise scaled in dt, not √dt          |
| 93  | Critical | Fixed      | Breakpoint save/load covered ~12 of 93 engine attributes                    |
| 94  | Major    | Fixed      | `set_state` aliased and consumed the stored breakpoint snapshot             |
| 95  | Major    | Fixed      | Solar generation constant was BOL-only — 389.5 vs 267.9 W/m², 1.45× high    |
| 96  | Major    | Fixed      | Solar array drive a dead lever; wings sat on the swept axis                 |
| 97  | Major    | Fixed      | AOCS sun sweep hard-coded to 5700 s against a 5669.3 s orbit (±8 W beat)    |
| 98  | Major    | Fixed      | Battery terminal-voltage sign inverted; no coulombic efficiency             |
| 99  | Critical | Fixed      | Protection ladder inverted (FDIR at 58 % SoC); restore hysteresis absent    |
| 100 | Major    | Fixed      | "Solar array degraded" fired on the first sunlit tick of every run          |
| 101 | Critical | Fixed      | FPA setpoint used as an environment offset; two unlinked FPA integrators    |
| 102 | Major    | Fixed      | Battery heater thermostat unreachable; command reverted silently            |
| 103 | Major    | Fixed      | EPS billed line-switch position, not actuator state (6 W phantom, 60 W free) |
| 104 | Major    | Fixed      | Panel solar heat gain ~80× too large (+362 °C); beta dependence inverted    |
| 105 | Critical | Fixed      | S12 bands never validated — one fires at t=0 and sheds payload, one is dead |
| 106 | Major    | Fixed      | Parameter-id collisions, crossed ids and undeclared writes                  |
| 107 | Major    | Fixed      | `sat.mode` and two TTC parameters never written — read SAFE / all-broken    |
| 108 | Critical | Fixed      | No attitude dynamics at all — no inertia tensor, no estimator, no INERTIAL  |
| 109 | Major    | Fixed      | ST1 zenith boresight inverted against the manual (+Z instead of −Z)         |
| 110 | Critical | Fixed      | Wheels/MTQ disabled at separation, never re-enabled; `wheels_enabled` unset |
| 111 | Major    | Fixed      | S19 rules dispatch with no `action_data` — cannot name a wheel or a tracker |
| 112 | Major    | Fixed      | FDIR/S12 had no persistence concept — real transients became faults         |
| 113 | Major    | Fixed      | TLE text quantisation (~1.3 m) breaks a manoeuvred-orbit breakpoint         |
| 114 | Critical | Fixed      | `PAYLOAD_CAPTURE` three-way divergence — every planned image at 0°N 0°E     |
| 115 | Major    | Fixed      | `TTC_SET_TX_POWER` reads float watts — three FDIR procedures ignored        |
| 116 | Major    | Fixed      | 19 procedure files carry a wholesale stale func_id scheme                   |
| 117 | Minor    | Fixed      | `FUNC_ID_SUBSYSTEM` stale — 20 commands exported as "Spacecraft"            |
| 118 | Major    | Fixed      | Systemic S20 subtype inversion — 79 annotations across 10 files             |
| 119 | Major    | Fixed      | Commands clamp out-of-range arguments instead of rejecting them             |
| 120 | Critical | Fixed      | `mcs/limits.yaml` had zero consumers — the ground did no limit checking     |
| 121 | Major    | Fixed      | TMArchive write path unused — empty command history, unbounded growth       |
| 122 | Major    | Fixed      | Contact status serialises `Infinity`; completed passes fill the panel       |
| 123 | Major    | Fixed      | MCS displays fabricate nominal values; health permanently GREEN             |
| 124 | Critical | Fixed      | FDIR never ran — `detect_fault`/`start_procedure` had zero call sites       |
| 125 | Major    | Fixed      | `dispatch()` error side-channel not re-entrant — nested commands clobber    |
| 126 | Major    | Fixed      | All 17 S1.4 failure sites pass `request_id = 0` — rejections unattributable |
| 127 | Critical | Fixed      | Instructor snapshot read live state off-thread and by reference             |
| 128 | Major    | Fixed      | Config schemas not strict — a typo'd key was silently dropped               |
| 129 | Major    | Fixed      | Wing-deploy events reuse `CHARGE_COMPLETE` / `PDU_OVERCURRENT` ids          |
| 130 | Major    | Fixed      | Launch scripts: no readiness probes, false bind claim, network pip installs |
| 131 | Minor    | Fixed      | Procedure audit cannot resolve event citations — 9 false findings           |
| 132 | Major    | Fixed      | Commanded FPA setpoint lost on breakpoint load                              |
| 133 | Major    | Fixed      | FPA thermal envelope disagreed across five files; legal command read red    |
| 134 | Critical | Fixed      | Commissioned HK SIDs silently switched off by a mission-phase declaration   |

Severity key:
- **Critical** — mission cannot operate safely without this
- **Major** — significant operational impact, workaround exists
- **Minor** — cosmetic or convenience, no operational impact

Fixed items are retained in the register so the associated regression tests
and rationale are captured in the issue tracker for audit.

Status note (2026-05-24 remediation pass): the **entire smo-simulator package is
remediated** — defects #9, #10, #11, #12, #23, #24, #25, #26, #27, #28, #29, #30,
#31 are all **Fixed**, covered by 26 regression tests in
`tests/test_remediation_phase01.py` (full simulator + acceptance suites green:
585 passed). Additional **Fixed**: #15 (planner validation now runs `validate_pass_plan`).
**Partial**: #13 (procedure-status 500 crash fixed via `status()`; wiring the
display *panels* into the live page + feeding real data remains), #14 (runner now
executes builder `wait`/`tlm_check`/`go_nogo`/`command` steps with comparison
operators and fails closed on unknown steps; bridging *saved custom* procedures
into the runner's load path remains). Still **Open**: #16 (planner endpoint UI
controls), #17 (unify PUS parser), #18, #19 (RF bridge — needs GNU Radio to test),
#20, #21, #22, #32, #33 (cleanup registers). Remediation follows
`REMEDIATION_PLAN.md`. Note: running the test suite needs `pytest-asyncio`
installed (some MCS tests are async).

Status note (2026-06-20 session): three bugs reported by Franz are now **Fixed** —
scenario scripts never auto-stopping (#34), breakpoint save/load not restoring the
orbital position / eclipse state (#35) and the matching MCS eclipse-display
key mismatch (#36), and the OBC autonomously booting into its application at
startup via mis-mapped S19 rules (#37). A dispatch/catalog audit during the same
session found and fixed three dead command paths: dropped S8 legacy quick-actions
100–107 (#38), the S8 func-49 thermal map that acknowledged but never downlinked
(#39), and the `tc_catalog.yaml` `on`/`off` field names that parsed as YAML
booleans and aborted the **whole** catalog load (#40, now 182 commands loading).
A new **ADCS per-axis control-torque-gain feature** (S6 memory load/dump:
`AOCS_SET_TORQUE_GAIN_X/Y/Z`, `AOCS_SET_TORQUE_GAINS_ALL`,
`AOCS_DUMP_TORQUE_GAINS`) was also added — this is a feature, **not** a defect.
Operational caveat (repeated): all of these fixes are **uncommitted working-tree
edits** relative to git HEAD, so a running simulator must be **restarted** to pick
them up, and breakpoint files saved before #35 lack the `orbit_utc` field. The
GitHub-issue filing script `scripts/upload_and_file_defects.sh` would need its
`ISSUES` array extended for 34–40 before these can be filed as issues.

Status note (2026-07-28 platform review): a ten-part review of the suite
(design audit, test strategy, planner/MCS integration, ground-station network,
planner commanding, on-board scripting, configuration consolidation, power
budget, and a textbook-vs-simulator reconciliation) produced defects **#41–52**,
all **Fixed**. Headlines: contact gating now evaluates every configured ground
station, so planner-scheduled Troll passes are live and the contact-gap median
falls from 87 min (max 668 min) to 84 min (max 148 min) with the bimodal
half-orbit/full-orbit structure expected of a two-station network (#41, #42);
the mission-planning commanding chain works end-to-end for the first time —
activity func_ids corrected, start times preserved, and the MCS S11 builder now
emits complete CCSDS packets that the on-board scheduler accepts and fires
(#44–#47); PUS Service 18 (OBCP) on-board scripting has been implemented with
load/delete/activate/stop/report, breakpoint persistence, three example
procedures and MCS catalogue entries (#48, see `docs/OBCP_GUIDE.md`); the
power budget now closes under standard operations — the dead SoC-shed branch is
repaired and the array/TX/initial-SoC/shed-threshold values moved to
configuration, giving ~27 points of margin above the shed band while leaving
the ladder reachable for failure training (#49, #50); and 48 previously
hard-coded simulation parameters now live in `configs/eosat1/simulation.yaml`
with a documented reference table, alongside a scenario loader that rejects
unknown actions and unimplemented failure modes at load time instead of
ignoring them (#51, #52). Defect #43 (RF transfer-frame FHP desync) was found
while root-causing what had been recorded in earlier sessions as an
order-dependent test flake — that annotation is withdrawn; it was a real
silent-telemetry-loss defect. Full suite after this pass: **2664 passed,
3 skipped, 2 xfailed, 0 failed** (the two xfails are deliberate review gates on
S19 rule and procedure-annotation content that require Franz's FDIR-domain
decisions — see `docs/`-adjacent review report). As always these are
working-tree edits: commit them and restart the simulator to pick them up.

Status note (2026-07-29 follow-up review): Franz raised a further ~25 observations
spanning the mission planner, the radio/RF interface, the MCS operator displays, the
on-board PUS services and the merge of a colleague's fork. Five read-only audits were
run first (planner, RF, MCS displays, PUS services, fork diff — filed under
`docs/review_2026-07/`), then the findings were fixed, producing defects **#53-83**
(no #81 — the suspected fault proved already fixed). All status Fixed.

Themes. The **planner** was displaying fiction: hard-coded JavaScript station and
activity fixtures meant the familiar Svalbard/Inuvik/O'Higgins list came from the
browser, not the configuration, and the UI ran on wall clock while the backend
correctly resolved simulator time (#57, #58). It now has its own configuration file
(`configs/eosat1/planning/planner.yaml`) with an explicit alignment check against the
canonical `orbit.yaml`, all five ground stations, a template-driven "load standard
activities" control on an otherwise empty plan, breakpoint-jump detection, and imaging
opportunities that aim at the real access point and honour solar-elevation and revisit
constraints (#59, #61). The **radio** had a spectrum analyser and eye diagram whose
producers were complete but never called, an OQPSK mode with no offset, four modulations
that could transmit but not receive, dead channel configuration and a channel model
carrying fading, phase noise and interferers that was instantiated nowhere (#53-56);
all are now live, with a runtime configuration surface and a registration point for
further impairment types. The **MCS** was the most operationally serious: live plots
kept appending the last value after loss of signal, drawing a flat line indistinguishable
from real telemetry (#65), and every housekeeping enable/disable command was addressing
the wrong structure because the SID was packed little-endian (#68). The console now has
the **FDIR window** the course textbook describes (#76), a time-tagged builder that uses
the full command builder, a readable on-board schedule, a memory patch/verify workflow
and a config-driven memory map (#77, #78). On board, **S12** was silently discarding
four of its thirty-eight monitoring definitions — every warning tier overwritten by its
alarm twin — and emitted no transition telemetry at all (#69), while **S19** could not
report its rules and fired seventeen of twenty-six on both an event and an unrelated
parameter because the two ID spaces overlapped (#70).

Two things were deliberately NOT decided here. The thirteen S19 rules whose action
function contradicts their description remain flagged by strict-xfail review gates for
Franz's flight-operations judgement — the ID-namespace repair changed no action
function. And the payload image size was reduced 800 MB → 4 MB on Franz's instruction,
aligning the simulator with the textbook and making the nominal operations day close
with 69 % margin; the 20 GB on-board store was left as-is so that downlink, not storage,
remains the binding constraint students must reason about (#82).

Suite after this pass: **3326 passed, 5 skipped, 3 xfailed, 0 failed** (from 2664).
The only piece of the fork worth taking was its ADCS failure-scenario viewer, ported to
use this repository's newer physics module (#62); merging the fork wholesale would have
reverted defects 34-52 and deleted 35 test files. As always these are working-tree
edits — commit them and restart the services to pick them up.

Status note (2026-07-29, data-rate plan): Franz observed that the low rate was
1 kbps — "very low … I might expect a beacon at that rate in bootloader, but in
routine operation, I need enough to get housekeeping data down. For high rate,
this should be for delayed telemetry and image dumps." Research against published
smallsat link designs, CCSDS practice and ground-network specifications, together
with a link budget recomputed for this spacecraft and its five stations, produced
a three-tier ladder now declared in `configs/eosat1/subsystems/ttc.yaml`
(`tm_rate_tiers`): **BEACON 1 kbps** BPSK conv(7,½)+RS, bootloader and beacon only;
**LOW 16 kbps** BPSK conv(7,½)+RS, the routine-operations default and the safe-mode
fallback; **HIGH 128 kbps** QPSK RS(255,223), for delayed telemetry and image dumps,
permitted only with the antenna deployed, in DOWNLINK mode (new S8 func 79) and
above 10° elevation. A request for a tier whose gate is not open is rejected, never
silently clamped, and the active tier is reported separately from the commanded one
so an operator can see a demotion and its cause (#85).

The complaint was quantitatively justified: the shipped housekeeping structures
generate 1557 bps of channel, so at 1 kbps the backlog could never clear. At LOW
that stream is 36 % of the network's contact capacity; at HIGH it is 4.5 %. One
4 MiB image occupies 74 % of an average pass at HIGH, so imagery genuinely competes
with telemetry for pass time — the nominal operations day fails by 24 % if flown at
LOW alone and closes with 80 % margin once dump passes are booked at HIGH. The
planner now resolves the rate per activity rather than assuming one flat number,
with the elevation gate expressed as real geometry (a 12° pass is dump-capable for
47 % of its duration, a 30° pass for 76 %), and 43 of 52 daily passes qualify (#86).

Underneath all of it, the link-budget calculation had been **subtracting** the
ground-station G/T where it must add it — a 40 dB error that ranked Inuvik, the
best-equipped station, as the worst performer (#84). Nothing about a rate plan
could mean anything until that was corrected; station ordering now tracks G/T
directly and `tools/link_budget_ladder.py` prints the tier × station × elevation
margin table, exiting non-zero if any permitted tier falls below 3 dB or exceeds
the 5 MHz per-mission channel allocation.

One further finding deserves recording because of its history. A failure in
`tests/test_acceptance/test_commands.py` looked like the "order-dependent flake"
that had previously been dismissed for several sessions and turned out to be
defect #43. It was investigated on that assumption and proved this time to be a
genuine test-timing defect rather than an RF fault: the RF harness's acquisition
budget was a fixed 0.70 s — about 2.2 frame air-times — and derived from nothing,
so it could not survive a slower tier or a loaded machine. Timing is now derived
from the live pipeline's frame air time, and verification acknowledgements are
matched on the PUS request ID rather than accepting any stray S1.1 in the window,
which is how the harness had been passing on stale acknowledgements. Two
integration tests carried the same latent assumption and were fixed with it. No
defect file was raised: the RF path itself was sound, with no lost frames, RS
failures or PLL resets across ~600 instrumented command windows.

Suite after this pass: **3625 passed, 5 skipped, 3 xfailed, 0 failed**, twice
consecutively. `docs/LINK_BUDGET_AND_RATES.md` is the reference for the ladder and
is written to be given to students.

Status note (2026-07-29, operator-reported follow-ups): Franz commanded the LOW
data rate after booting the OBC application and received a bare "1,8". The
rejection was correct — beacon mode is latched in the transponder state and the
application boot does not clear it, so every non-beacon tier is refused until
`TTC_SET_BEACON_MODE(off)` (S8 func 70) is sent. He decided the gate should stay
explicit, because flight software booting genuinely does not reconfigure a radio,
so the fix was to make the refusal legible rather than to remove it.

That exposed something larger (#87). `_tier_permission_error` composes an
excellent diagnostic — "TM rate is forced to the beacon tier in beacon mode
(LOW refused)" — and `generate_s1_exec_fail` then packed only the request ID and
the error code, discarding the text one line before downlink. An audit found
**86 of 92** dispatcher call sites discarding a handler's failure result
entirely, so a refusal produced a truthful-looking S1.7 success; 16 acceptance
sites and 5 engine gates carried no reason at all. The S1.8 report now appends a
length-prefixed reason after the existing fields (so a legacy `>IH` parse is
byte-identical), a shared `FAILURE_CODES` vocabulary lives in
`smo_common/protocol/pus_services.py` so the two ends cannot drift, rejections
raise event `0x030B COMMAND_REJECTED`, and the plumbing is now generic — a test
fails if a router ever calls `handle_command` directly again.

Franz then reported that error codes appeared in none of the displays he would
naturally look at (#88). The telemetry window now carries a new parameter
`0x0315 obdh.last_tc_error_code` in HK SID 4 and a telecommand-acceptance panel
showing the rx/accepted/rejected counters — which had been downlinked since the
beginning and never displayed — with the last failure decoded to
`0x0007 — Not permitted in the current state`. The event log renders the
rejection with meaning and reason instead of a bare RID, and the FDIR journal
gained a command-rejection kind alongside S12 transitions and S19 firings. The
same sweep found that all 16 execution-failure sites had been followed by an
S1.7 "execution complete", so every S6 memory refusal read as a success and the
memory failure codes were unreachable.

Separately, quick commands were warning that they expected zero data bytes (#89).
The catalogue was innocent: `sendPUSCommand()` resolved the expected length from
ambient builder DOM — the S8.1 function dropdown — which the quick-command path
never touches, so 10 of 64 warned on a fresh page and 45 of 64 once the dropdown
had moved. The length is now derived from the wire bytes exactly as the
dispatcher derives it; all 64 quick commands were exercised in a browser against
a running simulator with zero warnings, and genuinely wrong lengths still warn.

Finally, a classroom deployment question: students on client machines could not
see downlinked delayed telemetry (#90). Three independent causes, any one
sufficient — the S15 archive had no API and no owner (the only reader was a
standalone tool globbing its own filesystem, linked from no UI), the documented
`deploy/` scripts never started that tool nor exported its directory while the
installation manual documented its port as if it were listening, and the tool
bound to localhost while the launcher printed `localhost` URLs to every student.
The dump store is now served over HTTP by the MCS — the ground segment properly
owns the ground copy — with one documented resolution order, a traversal-safe
fetch, and a link in the MCS header. Breakpoints were **not** affected: save,
list and load already go through the simulator's API keyed by name, so a
networked client was always fine.

Suite: **3922 passed, 7 skipped, 3 xfailed, 0 failed**. `docs/DEPLOYMENT.md`
describes the server-plus-clients arrangement with the ports verified against
`start.sh` rather than against its comments.

Status note (2026-08 evolution build, defects #91–133): eight phases landed in
dependency order and every defect below is **Fixed** and pinned by test. The
shape of the register changes here. Everything up to #90 was found by using the
product — a control that did nothing, a display that showed the wrong thing.
#91–133 were found by *building on* it: nothing downstream of Phase A was
verifiable until runs were reproducible (#91), speed-independent (#92) and able
to survive a breakpoint (#93, #94), and each later phase exposed the latent
defects in the layer beneath it. That is why a third of these entries are
pre-existing bugs in code that predates the build — an inverted battery-voltage
sign (#98), an FDIR ladder that safed the spacecraft at 58 % SoC (#99), an
autonomous payload shutdown at t = 0 of every session (#105), a ground segment
that had never once evaluated a limit (#120), an FDIR subsystem with no call
sites (#124). They are recorded here because that is what the register is for.

Three groups are worth reading together. **#95–#104** are the power and thermal
model: no single one of them dominated the budget, and the corrected budget only
became meaningful once all of them were right. **#106, #107, #117, #129** are
one failure mode in four namespaces — an identifier space maintained by hand
with no closure check — now covered by `tests/test_content/test_parameter_chain.py`,
`test_event_catalogue_emissible.py` and the catalogue↔dispatch bijection.
**#114–#119** are the command argument contract, which had four independent
authorities (catalogue, handler, planner, procedures) and now has one.

Two things were deliberately **not** filed as defects. The `±Y` deployable-wing
layout is a hardware design decision and was kept — #96 is filed against the
generation model's failure to read the drive angle, not against the geometry.
Declaring a **SECULAR** AOCS fidelity mode above ×60, with an S5 event and a
telemetry parameter announcing the bypass, is a stated approximation with an
observable contract, not a defect; #92 covers the integrator error it replaced.

Suite: **4 900+ passed, 0 failed**, including 9 200 lines of new test code.
New contract gates: parameter-chain closure, limits-are-live, config-keys-have-
consumers, catalogue↔dispatch bijection, event-catalogue emissibility, endpoint
reachability, deployment consistency, FPA-envelope single source, the procedure
audit, and a strict-xfail register that fails the build when an open domain
decision outlives its deadline.
