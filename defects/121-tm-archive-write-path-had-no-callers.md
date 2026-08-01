# 121 — the command history was a permanently empty table, and the archive grew without bound

**Status:** Fixed

## Summary

`TMArchive` implements a complete SQLite-backed mission archive. Four of its
methods — `store_command`, `update_command_state`, `acknowledge_alarm` and
`prune` — had **zero callers**. So the command-history table was a schema that
never received a row, alarm acknowledgements were never persisted, and the
database plus its write-ahead log grew for the whole of a teaching term with
nothing ever pruning it and no shutdown path closing it. Four
`/api/archive/*` endpoints served the data none of it produced, to no client.

## Root cause

`packages/smo-mcs/src/smo_mcs/tm_archive.py`:

| Method | Line | Callers |
|---|---|---|
| `acknowledge_alarm` | 205 | none — `server.py:3564` calls the *panel's* same-named method, which is in-memory only |
| `store_command` | 242 | none |
| `update_command_state` | 257 | none |
| `prune` | — | none |

The `acknowledge_alarm` case is the instructive one: `server.py:3564` does call
an `acknowledge_alarm`, but on `self._fdir_alarm_panel`
(`displays/fdir_alarm_panel.py:81`), which mutates a list in memory. The name
collision made the archive method look wired. An operator acknowledged an
alarm, the badge cleared, they reloaded the console and every acknowledgement
was gone.

Registered and unconsumed alongside them (`server.py:962-965`):

```python
app.router.add_get("/api/archive/parameters", self._handle_archive_params)
app.router.add_get("/api/archive/events",     self._handle_archive_events)
app.router.add_get("/api/archive/alarms",     self._handle_archive_alarms)
app.router.add_get("/api/archive/playback",   self._handle_archive_playback)
```

No page fetched any of them. The storage layer, the query layer and the HTTP
layer were all built; the writer and the reader were both missing, from
opposite ends.

Unbounded growth is the operational consequence that bites a classroom
deployment: the archive and its `-wal` file are never truncated and the process
never closes the connection, so a term's worth of sessions accumulate and the
last session of term starts slowest.

## Fix

* **Commands are archived**: `store_command` on send,
  `update_command_state` on each verification transition, so the command
  history is a real record — ACCEPTED → EXECUTING → COMPLETED, or REJECTED
  with the reason from **#87**.
* **Acknowledgements write through** to `TMArchive.acknowledge_alarm` and
  survive a restart.
* **`prune` runs at construction and on a timer**, bounded by a configured
  retention, and the archive is **closed at shutdown**.
* The four `/api/archive/*` endpoints gained their client: the **TRENDS** page,
  a multi-parameter windowed view over the archive with limit bands and
  eclipse/contact shading.

## Tests

`tests/test_mcs/test_ground_limits_and_archive.py`

* `test_sent_command_is_archived_and_transitions_on_verification`;
* `test_rejected_command_is_archived_as_rejected`;
* `test_acknowledgement_persists_across_a_restart` and
  `test_ack_endpoint_writes_through_to_the_archive`;
* `test_archive_is_pruned_on_start_and_on_a_timer`,
  `test_prune_runs_at_construction`, `test_shutdown_closes_the_archive`;
* `test_trend_endpoints_return_the_seeded_series`.

`tests/test_mcs/test_trends_page.py` (11 tests) covers the new client;
`tests/test_content/test_endpoint_reachability.py::test_no_headless_display_or_archive_endpoints`
fails if an archive endpoint loses its consumer again.

## Files

* `packages/smo-mcs/src/smo_mcs/tm_archive.py`, `server.py`
* `packages/smo-mcs/src/smo_mcs/static/index.html` — TRENDS page

## Related

* **#20** — the consolidated MCS dead-code register; this is the largest
  single instance and is filed separately because it has operational
  consequences (lost acknowledgements, unbounded growth), not merely dead code.
* **#120** — the alarms whose acknowledgements had nowhere to persist.
