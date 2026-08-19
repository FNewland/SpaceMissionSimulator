# 134 — commissioned HK SIDs silently switched off by a mission-phase declaration

**Status:** Fixed

## Summary

Field report: *"when housekeeping SIDs are enabled, they seem to disable… but
this would affect delayed TM."*

They did. An operator who brought the non-default housekeeping structures on
line with `HK_ENABLE` (TC(3,5)) — SIDs 2, 3, 4, 5, 6, 9, 12 — and then sent
`SYS_SET_MISSION_PHASE` (S8.1 func 120), which is the **last step of the last
LEOP card and the last step of the last commissioning card**, had every one of
those SIDs switched back off. No telecommand rejection, no event, nothing on
any display. The on-board software had not rebooted; only the phase label
changed.

Because periodic generation is what feeds the S15 HK store, the consequence was
delayed: the store simply stopped receiving those SIDs, and the first evidence
the operator got was a hole in the next dump — hours later, in a pass they
could not repeat.

## Root cause

`packages/smo-simulator/src/smo_simulator/engine.py`, `apply_phase_change()`:

```python
if old_phase <= 3:
    self._exit_bootloader_mode()
else:
    self._apply_default_hk_enable()      # <-- application -> application
```

`_apply_default_hk_enable()` re-applies `simulation.yaml
housekeeping.default_enabled_sids` (`[1, 11]`). Doing that on a **boot** is
correct and documented (defect #75). Doing it on an application-to-application
phase step is not: a mission phase is a *declaration about where the mission
is*, not a reset of the on-board software. `apply_phase_change()` is reached
from the ground telecommand (S8.1 func 120), from the instructor console
`set_phase`, and from scenarios, so all three wiped the enable set.

Reproduced headlessly: enable SIDs 2, 3, 4, 5, 6, 9, 12 in COMMISSIONING, run
600 s (all seven land in the S15 HK store), send
`SYS_SET_MISSION_PHASE(6)`, run another 600 s — the store receives **zero**
further packets for any of the seven, and only SID 1 continues.

## Fix

1. **The defect.** `apply_phase_change()` no longer touches the HK enable set
   on an application-to-application transition. Only `_exit_bootloader_mode()`
   (i.e. a real boot) re-applies the configured defaults.

2. **The visibility.** A boot or a bootloader entry *does* legitimately reset
   the enable set, and that was equally invisible. Every wholesale reset now
   goes through `Engine._set_hk_enable_set()`, which raises **S5 event 0x0056
   `HK_ENABLE_SET_RESET`** naming the SIDs it switched off and why, and records
   the same explanation as `hk_enable_reset` on `/api/state` and the instructor
   snapshot. The MCS absorbs it (`MCSServer._ingest_hk_enable_reset`), serves it
   on `/api/hk-structures`, and the modern console's HK Manager now shows the
   reported enable set per SID plus a banner explaining a reset and pointing at
   the re-enable command.

3. **The delayed-TM honesty.** A gap must read as a gap:
   * `tools/delayed_tm_viewer.py` detects per-SID gaps from the configured
     cadence (`find_sid_gaps`, same 3 × cadence convention the report
     generator uses), reports them per SID and per parameter, breaks the
     plotted trace across them (`spanGaps: false` plus an explicit
     "NO DATA" sample) and lists them in the table. Previously the chart drew
     a smoothed line straight across a 400 s outage on an evenly-spaced
     category axis — fabricated continuity.
   * `smo_common.reporting.analysis.completeness()` counted `expected` over the
     span of the packets it received, so a SID that stopped part-way through a
     period reported ~0 % missing and no gaps at all. It now counts over the
     requested period and treats leading/trailing holes as gaps — which is the
     exact shape a disable makes.

## Tests

* `tests/test_simulator/test_hk_enable_persistence.py`
* `tests/test_common/test_hk_gap_honesty.py`
* `tests/test_mcs/test_hk_enable_reset_visibility.py`

## Procedure cards affected (not edited — see report)

`leop/initial_orbit_determination.md` (declares phase 5),
`commissioning/*` final card (declares phase 6),
`emergency/obc_reboot.md`, `contingency/obdh_watchdog_recovery.md`,
`contingency/obc_bootloader_recovery.md`, `contingency/obc_redundancy_switchover.md`
and `nominal/hk_configuration.md` need a step telling the operator that any
boot resets the HK enable set, listing the SIDs to re-enable (including 9 PROP
and 12 GPS_NAV, which no reboot card mentions today), and warning that the S15
stores hold nothing for a disabled SID.
