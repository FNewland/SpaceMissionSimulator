# 90 — downlinked delayed telemetry was unreachable from a client machine

**Status:** Fixed

## Trigger

Franz, setting the suite up for a class on a network (instructor/server machine
plus student client machines):

> "Can you also check where breakpoints would be stored and loaded from in a
> server-based system, and where downlinked delayed telemetry would be stored
> and served from? I tried this in a network setting and the client could not
> see the delayed TM files (I did not try breakpointing, but expect a similar
> issue?)"

Two questions. The answers are different: delayed TM was genuinely broken over
the network in three independent ways, **breakpoints were never affected**.

## What the delayed-TM path actually was

1. The operator commands S15.9. `SimulationEngine.queue_dump()` paces the stored
   packets down the link *and* archives the complete raw dump to
   `dump_sid<NN>_<UTC>.bin` — length-prefixed raw packets — under
   `SMO_DUMP_DIR` > mission `dump_dir:` > `workspace/dumps`. The writer is the
   **simulator process**, on the simulator's local disk.
2. The only reader was `tools/delayed_tm_viewer.py`, a standalone aiohttp tool
   started by `start.sh` on port 8092, which `glob`s that directory **on its own
   local filesystem**.

That is the whole architecture. There was no other route to the files.

## Root cause — three of them, any one sufficient

### 1. The dumps had no API and no owner. (structural, the real one)

The `.bin` archives are the ground segment's copy of downlinked data, but
nothing in the ground segment served them. `grep -rn 8092` across the tree finds
the string only in `start.sh`, the viewer itself and documentation — **no UI
anywhere links to it**. The MCS, which is the service every student actually has
open, had `POST /api/tm-dump` (send an S15.9) and `GET /api/tm-dump-data`
(replay points out of the MCS's own SQLite archive, falling back to *synthetic*
data), and no notion whatsoever of the archive files. A student on a client
machine, given the MCS URL, had no path to a dump — not a broken one, none.

So the previously-recorded advice for a server deployment ("point both at the
same shared/NFS path and run the viewer with `--host 0.0.0.0`") was a workaround
for a missing API, and it requires exactly the shared mount a classroom does not
have.

### 2. `deploy/` never started the viewer at all.

`deploy/start-all.sh` and the individual `deploy/start-*.sh` scripts — the
documented path for a server deployment — start the simulator, the MCS, the
planner and optionally the gateway. Nothing else. Port 8092 was never listening,
while `deploy/INSTALLATION_MANUAL.html` documents it in four places as if it
were ("Delayed TM Viewer … 8092 … Browse stored TM dump files") and tells the
reader to `ufw allow 8092:8095/tcp`. They also never exported `SMO_DUMP_DIR`, so
the store's location was whatever each service's working directory implied.

### 3. Even under `start.sh`, the URL handed out was the student's own machine.

`tools/delayed_tm_viewer.py` binds `127.0.0.1` unless given `--public`, so
without `./start.sh --public` the port is closed to the network. And with
`--public` the banner still printed

```
  Delayed TM view:  http://localhost:8092
```

for every service. A student typing that reaches their own laptop, where nothing
is listening. Same for the ADCS viewer (8097) and the scenario generator (8096).

### Not the cause

Not CORS: the viewer served its own page from its own origin, so its `fetch`
calls were same-origin. Not a UI path bug: the page builds `/api/dumps` and
`/api/decode` relative to wherever it was loaded from, which is correct. The
problem was upstream of both — nothing was serving, and nothing linked to what
was.

## Breakpoints — checked, and NOT affected

Franz expected "a similar issue". For breakpoints that expectation is wrong, and
worth saying plainly so nobody spends time on it:

- **Save**: instructor UI → `POST /api/command {type:"save_breakpoint", name}` on
  the simulator (8080) → `SimulationEngine._handle_save_breakpoint` captures the
  snapshot, stores it in `engine._breakpoints` and writes
  `workspace/breakpoints/<sanitised>.json` **on the simulator host**.
- **List**: `GET /api/breakpoints` reads `engine._breakpoints` — server memory.
- **Load**: `POST /api/command {type:"load_breakpoint", name}` → the engine looks
  the snapshot up **by name in its own store** and restores it.
- **Restart**: `_load_persisted_breakpoints()` reloads
  `workspace/breakpoints/*.json` at boot, so previous runs' breakpoints are
  listed and loadable.

Every step is server-side, keyed by name, over HTTP. The browser never sends,
receives or resolves a path, and the simulator's HTTP server binds `0.0.0.0`
unconditionally — so a browser on any client machine that can reach port 8080
behaves identically to one on the server. Nothing changed here.

(One latent inconsistency was noted and left alone: the REST endpoints
`POST /api/breakpoint/save` and `/load` in `instructor/app.py` call
`BreakpointManager` directly, so save does not persist to disk or register in
`engine._breakpoints`, and load only accepts an inline `state` dict, not a
`name`. The instructor UI does not use either endpoint — it goes through
`/api/command` — so this is dead weight rather than a live fault. Out of scope
here; it should be folded into the command path or removed.)

## Fix

### One store definition: `smo_common/dump_store.py` (new)

Single source of truth for the store — location, on-disk format, enumeration,
safe fetch, summary, and the HTTP routes.

- `resolve_dump_dir(mission_cfg, explicit)` — the one resolution order,
  now honoured by every writer and reader:
  **explicit CLI argument > `SMO_DUMP_DIR` > mission `dump_dir:` >
  `workspace/dumps`**. The engine and the viewer both call it; the MCS calls it
  with its own mission config, so the MCS and the simulator provably agree
  (there is a test for exactly that).
- `list_dumps()` — metadata including `store_id`, UTC timestamp, size, mtime and
  **packet count**; server-side paths are stripped before serving.
- `resolve_dump_file()` — the security boundary. Refuses path separators, `..`,
  drive letters, NULs, non-`.bin` names, and any symlink inside the store
  resolving outside it.
- `summarise_dump()` — decodes far enough to describe a file without any mission
  catalogue: packet counts per PUS service/subtype, APIDs, CUC span, CRC errors.
- `register_dump_routes(app, get_dump_dir, owner=…)` — mounts the API and a
  dependency-free browser page.

### The MCS owns the store

Mounted in `MCSServer.start()`:

| Route | |
|---|---|
| `GET /dumps` | Self-contained page: table of dumps with download and summary buttons. |
| `GET /api/dumps` | JSON listing + store path + totals. |
| `GET /api/dumps/file?name=` | Raw `.bin` download. |
| `GET /api/dumps/summary?name=` | Decoded summary (no `name` → whole-store metadata). |
| `GET /api/dumps/<name>` | Alias for `/file`. |

**Why the MCS and not the standalone tool:** the archives are the ground
segment's tape of a pass, and the ground segment is the MCS. Practically, it is
the port students already have open and that every deployment doc already tells
you to firewall open; serving from the MCS origin means no second port, no CORS,
no extra process for `deploy/` to forget to start, and no shared mount.

The **same routes are also mounted on the simulator's instructor app (8080)**,
because that process physically writes the files. In a split deployment where
the MCS runs on another host, `http://<simulator>:8080/dumps` works with no
configuration at all. The MCS UI gains a **DELAYED TM** link in the header, with
a relative `href` so it resolves against whichever host the student loaded the
MCS from.

### The standalone viewer becomes a client, and keeps its local mode

`tools/delayed_tm_viewer.py` still does the deep decode the API summary does not
(per-SID HK series in engineering units, S5 events, sparklines, anomaly flags,
Chart.js plots). Unchanged by default. New:

- `--api http://server:9090` (or `SMO_DUMP_API`) reads the store **over HTTP**
  from whichever service owns it, caching fetched archives in a temp dir so the
  existing decode path is reused verbatim. No NFS mount needed.
- `--dumps` now goes through `resolve_dump_dir`, so it obeys the same order as
  everything else instead of its own `os.environ.get(...) or "workspace/dumps"`.
- `list_dumps` / `parse_dump_filename` delegate to `smo_common.dump_store`;
  selection logic was factored into `select_meta()` so local and remote stores
  share it.
- An unreachable API surfaces an error in the page instead of a 500.

### Deployment plumbing

- `deploy/smo-env.conf` gains **`DUMP_DIR`** — the per-deployment setting lives
  in configuration, not only in an ad-hoc environment variable — with the
  single-machine / server+clients / split-host implications written next to it.
- `deploy/start-all.sh`, `start-simulator.sh`, `start-mcs.sh` export
  `SMO_DUMP_DIR` from it, create the directory, and print the `/dumps` URL and
  the store path in their banners.
- `start.sh` pins an absolute `SMO_DUMP_DIR` for every child (as before) and now
  prints **the machine's own address instead of `localhost` when `--public` is
  given**, plus the store path and a line saying which bind mode is in effect.

### Documentation

`docs/DEPLOYMENT.md` (new, linked from `docs/README.md`): deployment shapes,
every service with its port and *actual* bind address verified against
`start.sh` and the sources, what must be reachable from a student machine, where
every persistent artefact lives and how a client gets it, the dump store rules,
the breakpoint finding above, an end-to-end classroom recipe and a
troubleshooting table.

## Other artefacts — audited, and what was done

| Artefact | Verdict |
|---|---|
| Delayed-TM dumps | **Was server-only. Fixed** (above). |
| Breakpoints | Already fully server-side over HTTP. **Unchanged.** |
| MCS TM archive (SQLite) | Already served via `/api/archive/*`. **Unchanged.** |
| Custom procedures | Written by the MCS, already served via `/api/procedure/custom` and `/api/procedures/<path>`. **Unchanged.** |
| Scenario debriefs | In memory, served by `GET /api/scenario/debrief`. Nothing on disk. **Unchanged.** |
| Imaging products | No files are written; products exist only as on-board data volume and telemetry. Nothing to retrieve. **Unchanged.** |
| ADCS viewer CSV | Held in memory and already downloaded over HTTP (`GET /download?id=`). Reachable as long as the viewer runs with `--public`; that is now documented, and the banner no longer misdirects. **Code unchanged.** |
| Recorded IQ (`smo_rfsim/recording/`) | **Server-only, deliberately left so.** Multi-megabyte `.cf32` captures produced by a CLI (`smo-rfsim-record --out …`) that someone runs on purpose for offline GNU Radio / Inspectrum work — not something a pass generates and not a student deliverable. Serving them would put an unbounded file server on a class network for no teaching benefit. Documented as `scp`-only. |
| Procedure audit report (`workspace/audit/`) | **Server-only, left so.** Developer/instructor tool output. |
| Service logs | stdout only; no log files exist. Documented. |
| Scenario generator output | Writes YAML into the config tree of whichever host runs it; instructor tool. Documented for the split-host case. |

## Tests

`tests/test_mcs/test_dump_api.py` (new, 24 tests):

- `GET /api/dumps` on an **empty** store — and on a store directory that does
  not exist yet — is a 200 with an empty list, correct `store` path and
  `exists` flag, not a 404 or a 500.
- A dump written by the **engine** (`SimulationEngine.queue_dump`, with
  `SMO_DUMP_DIR` pointed at a tmp dir) appears in the listing with the right
  `store_id`, `packet_count`, `size_bytes` and timestamp — and the server-side
  `path` key is **not** leaked to the client.
- Fetch returns the exact archive bytes, via both `/file?name=` and the
  `/api/dumps/<name>` alias, with a download disposition; unknown name → 404.
- **Path-traversal rejection**, parametrised over 11 escape forms (`../../etc/passwd`,
  its URL-encoded form, backslashes, a drive letter, a subdirectory, a trailing
  `../..`, a non-`.bin` name, `.`/`..`) against both `/file` and `/summary`,
  asserting the status is 400/404 *and* that planted secret bytes never appear
  in the body. `resolve_dump_file` is also tested directly, including a symlink
  planted inside the store pointing out of it.
- Summary decodes a real HK dump built with the same `TMBuilder` the simulator
  uses (S3.25 ×3, APID 100, zero CRC errors) and the endpoint agrees with the
  direct call.
- `GET /dumps` serves the page and the page is genuinely self-contained.
- Resolution order (all four levels) and **MCS and engine resolving to the same
  store**.
- The viewer as an **API client**: `RemoteDumpStore` lists, fetches into its
  cache, selects, and refuses an escaping filename locally; `make_app(None,
  api_base=…)` serves `/api/dumps` and `/api/decode` off the remote store; and
  an unreachable API degrades to an error payload rather than a crash.

Full suite: 3922 passed, 7 skipped, 3 xfailed, 0 failed.

## Files

* `packages/smo-common/src/smo_common/dump_store.py` (new)
* `packages/smo-mcs/src/smo_mcs/server.py` — `_dump_dir` resolution, route mount
* `packages/smo-mcs/src/smo_mcs/static/index.html` — DELAYED TM header link
* `packages/smo-simulator/src/smo_simulator/engine.py` — uses `resolve_dump_dir`
* `packages/smo-simulator/src/smo_simulator/instructor/app.py` — route mount
* `tools/delayed_tm_viewer.py` — API mode, shared store module, shared resolution
* `start.sh` — reachable host in the banner, store path, bind-mode note
* `deploy/smo-env.conf`, `deploy/start-all.sh`, `deploy/start-mcs.sh`,
  `deploy/start-simulator.sh` — `DUMP_DIR` / `SMO_DUMP_DIR`, banners
* `docs/DEPLOYMENT.md` (new), `docs/README.md`
* `tests/test_mcs/test_dump_api.py` (new)

## Related

* **#66** fixed the viewer's dead charts — it made the tool work, on the machine
  it runs on. This defect is that the tool was the *only* way in.
* **#73** established the ground-copy semantics these files rely on (an on-board
  S15.11 clear must not delete the archive).
* **#10 / #35** are the breakpoint save/load/restore defects; the flow they
  produced is the one audited above and found sound over the network.
