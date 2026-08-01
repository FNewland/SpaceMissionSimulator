# Defect 63 — The EVENTS and TM PACKETS windows could not be scrolled back, and silently discarded most of what they buffered

## Summary

The MCS bottom panel's two log windows were the **same DOM node**
(`#log-container`), re-created from scratch on every single packet:

```js
// index.html, old renderBottomLog()
let html = '';
const show = entries.slice(0, 80);        // ← only the newest 80 ever rendered
for (const e of show) { html += `<div class="log-entry">…</div>`; }
container.innerHTML = html;               // ← full replace ⇒ scrollTop resets to 0
```

`renderBottomLog()` was called unconditionally at the end of `handleTMPacket()`
for **every** TM packet off the WebSocket, and again from `addEventLog()` for
every locally generated line. The simulator emits SID 1 housekeeping at 1 Hz
(`hk_structures.yaml`) on top of S1/S5/S12 traffic, so the whole list was torn
down and rebuilt several times a second. Three consequences:

1. **Scrolling back was impossible.** Entries are newest-first, so replacing
   `innerHTML` put the browser back at `scrollTop = 0` — the display always
   snapped to latest, within a fraction of a second of the operator letting go
   of the scrollbar.
2. **Most history was unreachable.** The ring buffers held 200 TM rows and 300
   event rows, but `entries.slice(0, 80)` meant 120 TM rows and 220 event rows
   existed in memory and could not be seen by any means.
3. **The two tabs destroyed each other.** Switching from EVENTS to TM PACKETS
   re-filled the one shared container, so neither pane had its own history or
   scroll position.

The per-subsystem EVENTS boxes on the EPS/TCS/AOCS/TTC/PAYLOAD/OBDH tabs had
the same defect: `renderSubsystemEvents()` did an `innerHTML` replace at 1 Hz
from `updateUI()`, capped at `.slice(-30)` out of a 100-entry buffer.

Exactly one place in the whole 6 kLOC page touched scroll at all — the
procedure log's `logEl.scrollTop = logEl.scrollHeight` — so the pattern was
known and simply never applied to the operator log.

## Severity

**Major (operational).** During a busy pass, or immediately after an anomaly,
the operator cannot read the event that just went by: it is pushed up the list
faster than it can be read and there is no way to hold position. The log is one
of the primary anomaly-investigation tools on the console.

## Status

**Fixed.**

1. **Two independent panes.** `#log-container` is now a scroll parent holding
   `#log-list-events` and `#log-list-tm`. Switching tabs toggles visibility;
   neither pane's history is destroyed by the other.
2. **Incremental DOM.** `appendLogRow(kind, entry)` builds one row and
   `insertBefore(..., list.firstChild)` prepends it, trimming the tail past
   `LOG_RENDER_CAP`. The 1 Hz full-page reflow is gone. A full rebuild
   (`renderLogPane`) now happens only on tab switch and on release of the
   scroll lock.
3. **Scroll lock + jump-to-latest.** A `scroll` listener on the container calls
   `logScrollLockShouldEngage(scrollTop)` (true once the operator is more than
   `LOG_SCROLL_LOCK_PX` from the top — the list is newest-first, so "latest" is
   the top). While locked, **nothing touches the DOM at all**: new entries go
   into the ring buffer and a `pending` counter, and a sticky
   "▲ N newer — jump to latest" chip appears in the panel header showing how
   many rows arrived. Clicking it, or scrolling back to the top, rebuilds the
   pane from the buffer and clears the counter.
4. **Bigger buffers, no hidden truncation.** `EVENT_RING = TM_RING =
   LOG_RENDER_CAP = 2000`. The render cap equals the ring size, so nothing is
   buffered-but-unreachable. (The MCS already archives events server-side via
   `_archive.store_event()`, so the client ring is only a working window.)
5. **Filtering no longer rebuilds.** The events severity filter toggles a
   `.log-filtered` class on existing rows from each row's own `data-sev`,
   preserving both history and scroll position.
6. **Per-subsystem panes.** `renderSubsystemEvents()` early-returns when the
   content signature is unchanged (so the 1 Hz `updateUI()` no longer repaints
   a static list), restores `scrollTop` by the height the prepended rows added
   when the operator has scrolled back, and the buffer/cap went 100/30 → 500/500.

## Acceptance criteria

- Rows are appended incrementally, newest-first, without a full re-render.
- With the lock engaged, the pane's DOM row count does not change while packets
  keep arriving, the pending counter climbs, and the ring buffer keeps every row.
- Releasing the lock renders the **whole** buffer, not the newest 80.
- The EVENTS and TM PACKETS panes each keep their own rows.
- The severity filter hides rows without removing them.

## Affected areas

- `packages/smo-mcs/src/smo_mcs/static/index.html`
  — `#log-container` markup, `.log-list` / `.log-jump` / `.log-colhdr` CSS,
  `appendLogRow`, `makeLogRow`, `renderLogPane`, `renderBottomLog`,
  `applyEventFilterToDom`, `updateLogJumpChip`, `initLogScrollLock`,
  `pushEventLog`, `renderSubsystemEvents`.
- Tests: `tests/test_mcs/test_mcs_page_behaviour.py::TestOperatorLogScrolling`,
  driven through `tests/test_mcs/js/mcs_harness.js`.

## Not covered by tests

The *scroll event itself* is a browser concern: the tests drive
`logPanes[kind].locked` directly. That the chip is visible and clickable, and
that `scrollTop` behaves under real layout, needs manual verification in a
browser.

## Related

- Defect #64 (SID/RID columns) — the same rendering path.
- Defect #65 (plots fabricate a flat line after LOS).
