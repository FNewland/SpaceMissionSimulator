# EOSAT-1 SMO — Networked Deployment Guide

**Audience:** the instructor setting the suite up before a class, on a server
that student machines connect to over the network.

**Scope:** what runs where, what binds to what, which ports must be reachable,
where every persistent artefact lives, and how a student with nothing but a
browser reaches it. Read this *before* the first class; the classroom recipe in
[§7](#7-classroom-recipe-end-to-end) is the short version.

Companion documents:

- `deploy/INSTALLATION_MANUAL.html` — installing Python, the venv and the
  packages on each machine.
- `docs/OPERATIONS_GUIDE.md` — running the sim once it is up.
- `configs/eosat1/manual/installation_guide.md` — mission-specific install notes.

> **Every port and bind address in this document was read off `start.sh`,
> `deploy/*.sh`, `deploy/smo-env.conf` and the service sources.** Where a
> comment elsewhere in the tree disagrees, this file is the one that was
> checked against the code.

---

## 1. Deployment shapes

There are three shapes, in increasing order of pain:

| Shape | What it is | When to use it |
|---|---|---|
| **A — Single machine** | Everything on one laptop; the browser is on the same box. | Development, solo practice, marking. |
| **B — Server + client browsers** | One server runs every service; students run *only a browser*. | **The classroom. Use this one.** |
| **C — Distributed services** | Simulator on one host, MCS on another, planner on a third. | Multi-room exercises, a "remote ground station" demo. |

Shape B needs no shared filesystem, no student-side install and no per-student
configuration. Shape C does need care about where files land — see [§5.4](#54-shape-c-simulator-and-mcs-on-different-hosts).

---

## 2. Services, ports and bind addresses

### 2.1 Started by `./start.sh` (the normal single-command launch)

| Service | Process | Port(s) | Binds to | Purpose |
|---|---|---|---|---|
| Simulator — TC uplink | `smo-simulator` | **8001** TCP | `$SMO_BIND` | Telecommand socket. MCS connects here. |
| Simulator — TM downlink | `smo-simulator` | **8002** TCP | `$SMO_BIND` | Telemetry broadcast. MCS connects here. |
| Simulator — instructor UI + API | `smo-simulator` | **8080** HTTP/WS | `$SMO_BIND` | Instructor console, breakpoints, failures, scenarios, `/dumps`, **`/config` — the configuration editor, read-write**, **`/reports` — the report generator**. |
| MCS — operator UI + API | `smo-mcs` | **9090** HTTP/WS | `$SMO_BIND` | **The students' door.** Displays, commanding, TRENDS, alarms, `/dumps`, **`/reports`**, **`/config` (read-only browser)**. |
| Planner | `smo-planner` | **9091** HTTP | `$SMO_BIND` | Pass planning, ground track. `/wide` for ultra-wide screens; `/config` read-only. |
| Delayed TM viewer | `tools/delayed_tm_viewer.py` | **8092** HTTP | `127.0.0.1`, **`0.0.0.0` only with `--public`** | Rich decode/plots of dump files, and the standalone report generator. Optional. |
| Orbit tools | `tools/orbit_tools.py` | **8093** HTTP | `0.0.0.0` (always) | TLE / state-vector → TC converter. Started only if `sgp4`, `numpy` and `aiohttp` import; the banner says `NOT RUNNING` otherwise. |
| Documentation viewer | `tools/doc_viewer.py` | **8095** HTTP | `0.0.0.0` (always) | Serves `configs/eosat1/` manuals. |
| Scenario generator | `tools/scenario_generator.py` | **8096** HTTP | `localhost`, **`0.0.0.0` only with `--public`** | Authors `configs/eosat1/scenarios/*.yaml`. Instructor tool. |
| ADCS failure viewer | `tools/adcs_sim_viewer.py` | **8097** HTTP | `127.0.0.1`, **`0.0.0.0` only with `--public`** | Offline CERISE debris-strike explorer + CSV download. |

`$SMO_BIND` is **`127.0.0.1` by default** and **`0.0.0.0` with `--public`**;
`--host=ADDR` (or exporting `SMO_BIND`) overrides both. Until Phase G the
simulator, MCS and planner hard-coded `0.0.0.0` regardless of the flag while
the banner claimed loopback — the flag now genuinely controls the socket
(brief 08 D-2), so **a classroom server must be started with `--public`** or
students will not reach it.

**Start-up is ordered and checked (D-1).** `start.sh` polls the simulator's
`/api/state` and its TM socket, then the MCS `/api/state`, then the planner
`/api/schedule`, starting each dependant only when the one below it answers,
and **aborts the whole launch with a named error** if any fails. A simulator
that dies on a bad config no longer leaves an empty but plausible MCS console
in front of a class. The auxiliary viewers are probed too, but only warn. Once
running, the script exits as soon as the **first** core service exits; the
previous bare `wait` waited for all of them, so a dead simulator left a frozen
console up.

**`start.sh` never reaches the network.** If the packages are not importable it
installs from a vendored `wheels/` directory, and if there is none it stops and
prints the exact command to run — instead of stalling on `pip` in an offline
classroom and blaming the wrong component (D-3). `SMO_ALLOW_NETWORK_INSTALL=1`
opts back into the online path on a developer machine.

### 2.2 Only when RF is enabled (`./start.sh --rf` or `--rf=RF`)

| Service | Port(s) | Binds to | Purpose |
|---|---|---|---|
| RF bridge — TC to spacecraft | **8011** TCP | `mcs_bind` in `configs/eosat1/rfsim.yaml` (default `0.0.0.0`) | MCS sends TC here instead of 8001. |
| RF bridge — TM to ground | **8012** TCP | as above | MCS receives TM here instead of 8002. |
| Radio status web UI | **8094** HTTP | `0.0.0.0` | Modulation, BER, spectrum, eye. |
| ZMQ baseband PUB | **5555** TCP | per `rfsim.yaml` | Raw sample stream for external SDR tooling. Off unless used. |

With `--rf`, `start.sh` starts the MCS with `--connect localhost:8012
--tc-port 8011`; ports 8001/8002 stay open but the MCS no longer uses them.

### 2.3 Only in the `deploy/` scripts

| Service | Port | Binds to | Purpose |
|---|---|---|---|
| Gateway (`smo-gateway`) | **10025** TCP | `$GATEWAY_BIND` (default `0.0.0.0`) | TM/TC relay fan-out for several MCS instances. `USE_GATEWAY="yes"` in `deploy/smo-env.conf`. |

`deploy/start-all.sh` starts the simulator, MCS, planner, (optionally) the
gateway **and, since Phase G, the same auxiliary viewers on 8092–8097 that
`start.sh` starts**. Set `START_TOOLS="no"` in `deploy/smo-env.conf` for the
old behaviour, `TOOLS_BIND="127.0.0.1"` to keep them on the server console, and
`DELAYED_TM_PORT` / `ORBIT_TOOLS_PORT` / `DOC_VIEWER_PORT` /
`SCENARIO_GEN_PORT` / `ADCS_VIEWER_PORT` to move them.

`start-all.sh` uses the same readiness/abort contract as `start.sh`: each
dependant waits for the one below it to answer, a failure aborts, and the
supervisor exits when the first core service exits. Every bind address
(`SIMULATOR_BIND`, `MCS_BIND`, `PLANNER_BIND`, `GATEWAY_BIND`) in
`deploy/smo-env.conf` is now passed to the process that owns it; before Phase G
`MCS_BIND` and `PLANNER_BIND` were declared in that file and read by nobody.
None of the `deploy/*.sh` scripts install anything — `deploy/install.sh` does
that, once, and a start script that finds a package missing says so and stops.

### 2.4 Firewall — what must be reachable from a student machine

For **shape B**, open inbound TCP on the server:

```
9090     MCS            ← the only port a student strictly needs
8080     Simulator      ← instructor console; also serves /dumps
9091     Planner        ← if students do pass planning
8092-8097               ← only if you run the auxiliary tools for students
8094                    ← only with --rf, for the radio dashboard
```

For **shape C**, additionally open, on the simulator host, **8001** and **8002**
(or **8011**/**8012** with RF, or **10025** for the gateway) to the MCS host
only. These are raw TM/TC sockets with no authentication — do not expose them
to students or to an untrusted network.

There is **no authentication anywhere in the suite.** Run it on a lab network
or behind a firewall, never on the open internet.

---

## 3. Binding publicly: `--public`

`./start.sh --public` (or `SMO_PUBLIC=1 ./start.sh`) sets `SMO_BIND=0.0.0.0`
for **every** service — the simulator (8001/8002/8080), the MCS (9090), the
planner (9091) and the three tools that default to loopback: delayed TM viewer
(8092), scenario generator (8096) and ADCS viewer (8097).

Without it, `SMO_BIND=127.0.0.1` and **nothing is reachable from another
machine.** This changed in Phase G: the three core services used to bind
`0.0.0.0` unconditionally while the banner claimed otherwise (D-2), so a
"local" run was in fact exposed on every interface. If you want a specific
interface rather than all of them, use `./start.sh --host=192.168.1.10`.

`start.sh` prints the service URLs when it finishes starting. Without
`--public` it prints `localhost`; **with `--public` it prints this machine's IP
address**, because handing students a `localhost:` URL sends their browser to
their own laptop, where nothing is listening. If the address it prints is not
the one students should use (multi-homed server, VPN, NAT), give them the right
one — the services do not care which interface the request arrives on.

---

## 4. Delayed telemetry: the dump store

This is the artefact that most often catches people out, so it gets its own
section.

### 4.1 What it is

When an operator commands **S15.9 "start dump"**, the spacecraft plays the
selected on-board store back down the link. Two things happen:

1. The packets are paced down the RF link at the current TM rate and, if the
   link is up, arrive at the MCS as live telemetry.
2. The **complete** raw dump — every packet, including whatever the link
   dropped — is archived to a file:
   `dump_sid<NN>_<YYYYMMDDThhmmssZ>.bin`, holding length-prefixed raw CCSDS/PUS
   packets (4-byte big-endian length, then that many bytes, repeated).

Those `.bin` files are the ground segment's tape of the pass. An on-board
`S15.11 clear` deliberately does *not* delete them.

### 4.2 Where the files live — one resolution order

Every writer and every reader resolves the store the same way (implemented once
in `smo_common.dump_store.resolve_dump_dir`):

```
explicit CLI argument            e.g. delayed_tm_viewer.py --dumps /srv/dumps
  > SMO_DUMP_DIR environment variable
    > mission config  dump_dir:  in configs/<mission>/mission.yaml
      > workspace/dumps  (relative to the service's working directory)
```

- `start.sh` pins `SMO_DUMP_DIR` to an **absolute** path
  (`<repo>/workspace/dumps` unless you set it) and exports it to every child,
  so all services agree even if one is started from a different directory.
- `deploy/*.sh` do the same from the `DUMP_DIR` setting in
  `deploy/smo-env.conf` — **set it there, not in an ad-hoc environment
  variable**, so the setting is captured with the rest of the deployment
  configuration.
- For a permanent per-mission location, set `dump_dir:` in
  `configs/eosat1/mission.yaml` instead; the environment variable then only
  needs to appear when you want to override it for one run.

### 4.3 How a student gets at them

The store is served over HTTP by the process that owns it. **The MCS is the
primary owner**: the dumps are the ground segment's copy of downlinked data,
the MCS is the service every operator already has open, it is on a port that is
already reachable and already firewalled-open, and serving from the MCS origin
means no second port, no CORS configuration and no shared filesystem. The
simulator mounts the *same* routes because it is the process that physically
writes the files — which matters in shape C.

| URL | What it gives you |
|---|---|
| `http://<server>:9090/dumps` | **Browser page** — table of every dump with store id, UTC, packet count, size; download and summary buttons. This is the link students want. |
| `http://<server>:9090/api/dumps` | JSON listing with metadata (`filename`, `store_id`, `timestamp`, `size_bytes`, `mtime`, `packet_count`) plus the store path and totals. |
| `http://<server>:9090/api/dumps/file?name=<filename>` | The raw `.bin`, as a download. |
| `http://<server>:9090/api/dumps/summary?name=<filename>` | Decoded summary: packet counts per PUS service/subtype, APIDs seen, CUC time span, CRC error count. |
| `http://<server>:9090/api/dumps/<filename>` | Alias for `/file`. |
| `http://<server>:8080/dumps` and `/api/dumps…` | The identical API on the **simulator**. |

The MCS UI carries a **DELAYED TM** link in the header (top right) pointing at
`/dumps`. The href is relative, so it resolves against whatever host the
student loaded the MCS from.

The fetch endpoints accept a bare filename only. Anything containing a path
separator, `..`, a drive letter, a NUL, or a name that is not
`<safe characters>.bin` is refused with **400**, and a symlink inside the store
that points outside it is refused too. These endpoints are network-exposed;
treat that check as load-bearing.

### 4.4 The standalone viewer (port 8092)

`tools/delayed_tm_viewer.py` still exists and still does the deep decode the
API summary does not: per-SID housekeeping time series in engineering units,
S5 event extraction, sparklines, anomaly flags, Chart.js plots. It runs in two
modes:

```bash
# Local mode (default, unchanged) — reads the store off this machine's disk
python tools/delayed_tm_viewer.py --port 8092 --dumps "$SMO_DUMP_DIR" --public

# API mode — reads the store over HTTP from whichever service owns it
python tools/delayed_tm_viewer.py --port 8092 --api http://server:9090 --public
```

API mode (`--api`, or `SMO_DUMP_API`) makes the viewer a **client** of the dump
API rather than a filesystem reader, so it can run on the instructor's laptop
against a store on the server with no NFS mount. Students do not need it: the
MCS page covers list/download/summary.

### 4.5 Sanity check before a class

```bash
curl -s http://<server>:9090/api/dumps | head -c 400
```

Expect JSON with `"store": "<absolute path>"` and a `dumps` array — empty is
fine before the first dump of the run and the page still loads. Then command an
S15 dump from the MCS and re-run: a new entry should appear within a second.
If `store` is not the path you expect, something is starting with a different
`SMO_DUMP_DIR` or working directory; fix that rather than copying files around.

---

## 5. Where every persistent artefact lives

| Artefact | Written by | Location | How a client retrieves it |
|---|---|---|---|
| **Delayed-TM dumps** (`dump_sid*.bin`) | Simulator, at S15 dump time | `$SMO_DUMP_DIR` → `workspace/dumps` | **HTTP:** MCS `/dumps`, `/api/dumps…`; simulator `/dumps`. Also the 8092 viewer. |
| **Breakpoints** (`<name>.json`) | Simulator | `workspace/breakpoints/` on the simulator host | **HTTP, already:** instructor UI on 8080 — `GET /api/breakpoints` lists, `POST /api/command {type:"save_breakpoint"}` saves, `POST /api/command {type:"load_breakpoint", name}` loads. Save and load are entirely server-side; nothing resolves a client-local path. See [§6](#6-breakpoints-not-affected). |
| **TM archive** (`tm_archive.db`, SQLite) | MCS | `configs/../data/tm_archive.db` on the MCS host | **HTTP:** MCS `/api/archive/parameters`, `/api/archive/events`, `/api/archive/alarms`, `/api/archive/playback`. Not offered as a file download. |
| **Generated reports** (`.html`) | Report generator (simulator 8080, MCS 9090, viewer 8092) | `workspace/reports/` on the host that generated it | **HTTP:** `/api/reports` lists, `/api/reports/file?name=…` downloads, `/reports` is the browser page. Self-contained HTML — hand the file to a student directly. |
| **Configuration history** | Config editor (simulator 8080 `/config`) | `workspace/config-history/` — a shadow git repo whose work-tree is `configs/` | **HTTP:** `/api/config/history`, `/api/config/revisions/<rev>`, `/api/config/diff/<a>..<b>`, `/api/config/baselines`. The MCS and planner mount the same routes **read-only**; the write surface exists only on 8080. |
| **Custom procedures** (`.yaml`) | MCS, on "save procedure" | `configs/eosat1/procedures/custom/` on the MCS host | **HTTP:** `/api/procedure/custom` lists, `/api/procedures/<path>` fetches. Reachable from a client. |
| **Scenario definitions** (`.yaml`) | Scenario generator (8096) | `configs/eosat1/scenarios/` on the host running the tool | Instructor tool; the simulator reads scenarios from its own config dir. If the generator runs on a different host from the simulator, copy the YAML across. |
| **Scenario debriefs** | Scenario engine | **In memory only** | **HTTP:** simulator `GET /api/scenario/debrief`. Nothing on disk to retrieve. |
| **Imaging products** | Payload model | **No files.** Products exist as on-board data volume and S15/HK telemetry. | Nothing to retrieve; there is no image file to fetch. |
| **ADCS telemetry CSV** | ADCS viewer (8097) | **In memory**, per run | **HTTP:** the viewer's own `GET /download?id=<run>` — already a browser download. Needs the viewer started with `--public` to be reachable. |
| **Procedure audit report** | `tools/procedure_audit.py` (offline) | `workspace/audit/` | **Server-only.** Developer/instructor tool, not a student artefact. |
| **Recorded IQ** (`.cf32` + `.json` + `.sigmf-meta`) | `smo-rfsim-record` CLI | The `--out` directory on whichever host you ran it on | **Server-only by design.** These are multi-megabyte SDR captures for offline analysis in GNU Radio / Inspectrum, produced by a CLI you run deliberately, not something a pass generates. Copy them with `scp`. Not exposed over HTTP. |
| **Service logs** | all services | **stdout only** — no log files are written | Read the terminal, or redirect: `./start.sh > smo.log 2>&1`. |

### 5.4 Shape C: simulator and MCS on different hosts

The dump files are written by the **simulator**. If the MCS runs elsewhere, the
MCS's `/api/dumps` will list *its own* (probably empty) store. Two options:

1. **Point students at the simulator's copy of the API**:
   `http://<simulator-host>:8080/dumps`. Nothing to configure; the routes are
   already mounted there. This is the simpler option.
2. **Share the store**: mount one directory on both hosts and set `DUMP_DIR` in
   `deploy/smo-env.conf` (or `dump_dir:` in `mission.yaml`) to it on both. Then
   the MCS URL works too.

The same reasoning applies to the MCS-owned artefacts (TM archive, custom
procedures): they live wherever the MCS runs.

---

## 6. Breakpoints — not affected

Breakpoint save and load are **already fully server-side** and work unchanged
from a client machine. There is no client-local path anywhere in the flow:

- **Save** — the instructor UI posts `{"type": "save_breakpoint", "name": …}` to
  `POST /api/command` on the simulator (8080). The engine captures the snapshot,
  keeps it in `engine._breakpoints`, and writes
  `workspace/breakpoints/<sanitised-name>.json` **on the simulator host**.
- **List** — `GET /api/breakpoints` on the simulator returns what the engine
  holds in memory, so the LOAD buttons survive a browser reload and appear on
  any client.
- **Load** — the UI posts `{"type": "load_breakpoint", "name": …}`; the engine
  looks the snapshot up **by name in its own store** and restores it. The
  browser never sends or reads a file.
- **Restart** — on start-up the engine reloads every `workspace/breakpoints/*.json`
  into `engine._breakpoints`, so breakpoints saved in a previous run are listed
  and loadable.

The only requirement is that the instructor's browser can reach port 8080. On a
classroom server started with `--public` it can; on a default (loopback) start
only a browser on the server itself can. Breakpointing is an instructor function
anyway, normally driven from the server console.

If you want the `.json` files themselves (to copy a starting state to another
installation), take them off `workspace/breakpoints/` on the simulator host with
`scp`. There is no HTTP download for the raw snapshot files.

---

## 7. Classroom recipe, end to end

**On the server, once:**

1. Install per `deploy/INSTALLATION_MANUAL.html` (Python 3.11, venv, `pip install -e` the five packages).
2. Decide where dumps live. Default (`<repo>/workspace/dumps`) is fine. To pin
   it elsewhere, set `DUMP_DIR` in `deploy/smo-env.conf`, or `dump_dir:` in
   `configs/eosat1/mission.yaml`.
3. Open the firewall: inbound TCP **9090**, **8080**, **9091** (add 8092–8097 if
   you will run the auxiliary tools for students; add 8094 if using `--rf`).
4. Note the server's IP address on the lab network.

**On the server, before each class:**

```bash
cd /path/to/SpaceMissionSimulation
./start.sh --public              # add --rf for the RF-chain variant
```

Check the banner: it prints the server's own address, not `localhost`. Then:

```bash
curl -s http://localhost:9090/api/dumps      # store path + (possibly empty) listing
curl -s http://localhost:8080/api/breakpoints
```

**What to give the students** (substitute the server's address):

| For | URL |
|---|---|
| Mission control (main workstation) | `http://192.168.1.10:9090` |
| Delayed TM archive | `http://192.168.1.10:9090/dumps` — or the **DELAYED TM** button in the MCS header |
| Mission planner | `http://192.168.1.10:9091` (`/wide` on ultra-wide displays) |
| Mission documentation | `http://192.168.1.10:8095` |
| Pass / daily-ops reports | `http://192.168.1.10:9090/reports` |
| Configuration browser (read-only) | `http://192.168.1.10:9090/config` |
| Deep delayed-TM analysis (optional) | `http://192.168.1.10:8092` |

**What the instructor keeps:** `http://<server>:8080` — the instructor console
(failure injection, scenarios, time control, breakpoints), **`http://<server>:8080/config`
— the read-write configuration editor**, and `http://<server>:8096` — the
scenario generator. Do not hand these out. The MCS and planner `/config` pages
are the safe, read-only view of the same configuration and its history: the
mutating routes do not exist on those hosts, they are not merely disabled.

**During a pass**, to make delayed telemetry appear:

1. Wait for AOS (or force the link from the instructor console).
2. From the MCS, command an S15 dump for the store you want (the "Request S15
   TM Dump" buttons on each subsystem tab, or a raw S15.9).
3. The paced packets arrive live; the complete archive file appears in the store
   immediately, and in `/dumps` on the next refresh (the page polls every 30 s).
4. Students click **download** for the raw `.bin`, or **summary** for the decode.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Nothing at all is reachable from a student machine | Since Phase G the core services honour the bind flag, and the default is loopback. | Start the server with `./start.sh --public` (or `--host=<lab IP>`). |
| `start.sh` stops with "the simulator never became ready" | The simulator failed to boot — almost always a config error; its traceback is immediately above the message. | Fix the config. This is deliberate: the launch aborts rather than showing an empty MCS. |
| `start.sh` stops with "the SMO packages are not installed" | First run on a machine with no `wheels/` directory. | `pip install -e` the five packages once, or ship a `wheels/` directory, or re-run with `SMO_ALLOW_NETWORK_INSTALL=1`. Start scripts do not reach the network by design. |
| Student browser shows nothing at `http://<server>:8092` | The delayed TM viewer binds loopback without `--public`. | Start with `./start.sh --public`, or just use `http://<server>:9090/dumps`, which does not depend on that tool at all. |
| Student typed `localhost:9090` and got nothing | `localhost` is *their* machine. | Give them the server's IP. `start.sh --public` now prints it. |
| `/dumps` lists nothing | No S15 dump has been commanded yet in this run, or the service is looking at a different store. | Check the `store:` path shown on the page against `echo $SMO_DUMP_DIR` on the server. |
| `/dumps` on the MCS is empty but the files exist | Shape C: the simulator writes them on another host. | Use `http://<simulator-host>:8080/dumps`, or share the directory. |
| Dumps appear but contain fewer packets than expected | Nothing wrong — the archive holds the *complete* dump; it is the live downlink that drops packets when the link is down. Compare `packet_count` in the listing with what arrived live. | — |
| MCS shows no telemetry at all | MCS cannot reach the simulator's TM socket. | Check `MCS_TM_HOST`/`MCS_TM_PORT` (or `--connect`), and that 8002 (or 8012/10025) is open between the hosts. |
| Planner panels empty in the MCS | `SMO_PLANNER_URL` points at `localhost` from the MCS's point of view. | Set `PLANNER_REACHABLE_HOST` in `deploy/smo-env.conf`, or export `SMO_PLANNER_URL` before starting the MCS. |
| A breakpoint saved last week is missing | `workspace/breakpoints/` was cleared, or the simulator is running from a different working directory. | Start the simulator from the repository root, as all the start scripts do. |
