# Defect 65 — After LOS every trend plot kept drawing the last received value, producing a flat line indistinguishable from live telemetry

## Summary

The MCS's eleven Chart.js trend plots did not blank, did not gap and did not
freeze when telemetry stopped. They kept appending the **last received value**
once per second, forever.

The mechanism, end to end:

1. `MCSServer._param_cache` is written on arrival and **never expires** — there
   is no TTL, no eviction and no null-out anywhere in `server.py`.
2. `_state_poll_loop` broadcasts a `{"type":"state"}` message every second
   unconditionally, built from that cache, i.e. re-sending the same values.
3. `updateUI()` runs on every such message and calls the active tab's
   `updateXXX()`, which calls `pushChartData(canvasId, chartTimeCounter++, [...])`.
4. `pushChartData()` appended the point with no freshness check at all.

After `MAX_CHART_POINTS` (120) seconds of LOS the chart is a **100 % flat line
spanning the whole window**, with every genuine pre-LOS sample pushed off the
left edge. There is nothing on the plot to distinguish that from a genuinely
constant parameter.

Four correct staleness signals already existed and **none of them reached
Chart.js**:

| Signal | Produced | Consumed by | Reached the plots? |
|---|---|---|---|
| `state.stale` (age > 60 s) | `server.py` | `index.html` — assigned to `const stale` and **never referenced again** | no |
| `state.last_frame_age_s` | `server.py` | only the `sb-conn` status-bar text | no |
| `state.subsystem_staleness{sub: age}` | `server.py` | only the OVERVIEW tab chips | no |
| `state.params_age{pid: age}` | `server.py` | only `isParamStale()` on numeric cells | no |
| `state.in_contact` | `_compute_orbital_state()` | pass countdown / link displays | no |

The result was an operator looking at a numeric cell greyed out and stamped
`STALE`, two inches from a healthy-looking trend of the same parameter.

## Severity

**Critical (operationally dangerous).** This is the worst class of display
defect: it does not fail visibly, it *lies plausibly*. A flat SoC trace during a
battery anomaly, or a flat FPA temperature during a cooler failure, reads as
"stable" when it actually means "no data since AOS-1".

## Status

**Fixed.** One policy, gated in exactly one place so no call site can forget it.

1. **A single freshness predicate.** `tmFreshness(state, subsystem)` returns
   `'fresh' | 'degraded' | 'stale' | 'none'` from the real signals:
   - `last_frame_age_s == null` → `none` (no TM frame has ever arrived);
   - the server's own `stale` flag forces `stale`;
   - the age used is `max(last_frame_age_s, subsystem_staleness[sub])`, so a
     subsystem whose HK SID is disabled or which is powered down goes stale even
     while the link is up;
   - `in_contact === false` with an age past the fresh window → `stale`,
     because out of contact no newer frame can possibly arrive;
   - thresholds `TM_FRESH_S = 5` and `TM_STALE_S = 15`, both configurable
     constants next to `MAX_CHART_POINTS`.
2. **The gate.** `pushChartData()` calls it first. On `stale`/`none` it
   **stops appending entirely** and calls `blankChart()`.
3. **Blanking is explicit, not silent.** `blankChart()` empties the buffers and
   the datasets and overlays a hatched banner reading
   `NO DATA (LOS) — LAST FRAME 42s AGO` (or
   `NO DATA — NO TELEMETRY RECEIVED` when nothing has ever arrived) across the
   plot.
4. **Degraded is marked, not hidden.** Between 5 s and 15 s the chart still
   plots but carries an amber `LAG 8s` chip in the corner.
5. **Recovery is clean.** Because the buffers are emptied, the first post-AOS
   sample starts a fresh trace — no interpolated segment bridging the gap and no
   pre-LOS values carried across it.
6. **Inactive tabs too.** `blankStaleChartsEverywhere()` runs at the top of
   `updateUI()`, so a chart on a hidden tab cannot retain a frozen buffer and
   reappear intact on tab switch.

## Acceptance criteria

- Fresh telemetry plots; 30 pushes during LOS add **zero** points.
- After blanking, the buffer length is 0 and recovery restarts from the first
  new sample (verified by asserting the first y-value post-recovery).
- Per-subsystem staleness blanks only that subsystem's charts.
- Every canvas id used by a `pushChartData()` call site maps to a staleness
  subsystem (asserted over the call sites found in the page, so a new chart
  cannot quietly bypass the per-subsystem half of the gate).

## Affected areas

- `packages/smo-mcs/src/smo_mcs/static/index.html` — `TM_FRESH_S`/`TM_STALE_S`,
  `CHART_SUBSYSTEM_PREFIX`, `chartSubsystemOf`, `tmFreshness`,
  `staleChartLabel`, `chartOverlayEl`, `blankChart`, `setChartFreshOverlay`,
  the gate in `pushChartData`, `blankStaleChartsEverywhere`, and the
  `.chart-stale-overlay` CSS.
- Tests: `tests/test_mcs/test_mcs_page_behaviour.py::TestChartStalenessGate`.

## Forward work (not in this change)

The chart x-axis is still `chartTimeCounter`, a **shared global counter**
incremented by whichever tab happens to be active, with `x: {display:false}`.
It is not a clock, so the operator cannot tell how much wall-clock time a
segment covers, and two tabs share one counter. Converting all eleven
`ensureChart` definitions and `CHART_OPTS_BASE` to `x: {type:'time'}` fed by the
ground clock is the correct follow-up (audit item 10, effort M). Blanking makes
the present behaviour honest; a real time axis would make gaps *visible*.

## Related

- Defect #63 / #64 — the other two MCS display defects fixed in the same pass.
