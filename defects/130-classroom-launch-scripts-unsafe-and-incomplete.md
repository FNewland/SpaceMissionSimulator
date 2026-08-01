# 130 — the launch scripts started services blind, bound wider than they claimed, and needed the internet

**Status:** Fixed

## Summary

The scripts an instructor runs to start a class had three independent problems:
`deploy/start-all.sh` started dependants without waiting for anything to be
ready and silently omitted **five** of the tool viewers, the `--public` banner
told students the servers were on loopback while three of them hard-coded
`0.0.0.0` regardless, and `start.sh` reached out to the network for pip
installs on a machine that may have no internet.

## Root cause

**1. No readiness, no supervision.** `deploy/start-all.sh` (154 lines at
baseline) backgrounded each service and moved on. A dependant that came up
before the service it needs — the MCS before the simulator's TC port, the
planner before the simulator's state API — connected to nothing and stayed
in a degraded state for the session. A service that failed to start at all
produced no abort: the script printed its banner of URLs and exited 0.

**2. Five tool viewers never started.** The delayed-TM viewer, the report
viewer and three other `tools/` services are documented in the installation
manual with their ports, and `deploy/start-all.sh` started none of them, so a
student following the manual got connection-refused on a documented URL.

**3. The bind address was a fiction.** `start.sh:58-66` computes an
`ACCESS_HOST` and prints `localhost` unless `--public` is given, with a comment
explaining that "with `--public` the services bind 0.0.0.0". They bind
`0.0.0.0` either way:

```
packages/smo-mcs/src/smo_mcs/server.py:1001        web.TCPSite(runner, "0.0.0.0", …)
packages/smo-planner/src/smo_planner/server.py:437 web.TCPSite(runner, "0.0.0.0", …)
packages/smo-simulator/src/smo_simulator/server.py:43,44,54  "0.0.0.0"
```

None of the three accepted a bind address at all. So the flag that exists to
*restrict* exposure did nothing, and an instructor who deliberately omitted
`--public` on a shared network was exposed anyway — while being told they were
not.

**4. Network access at launch.** `start.sh:92-97` and `:188` run
`pip install`. There is an offline path (`--no-index --find-links wheels`) but
the fallbacks reach the index, and `:188` unconditionally attempts
`pip install -q sgp4 numpy aiohttp`. A teaching lab without internet — the
common case — got a slow, confusing start or a failure at a point where
nothing had told the instructor that installation was even being attempted.

## Fix

* **`--host` / `$SMO_BIND` honoured by the simulator, MCS and planner**;
  local mode binds loopback and `--public` binds `0.0.0.0`, so the banner and
  the socket agree.
* **Readiness polling before every dependant**, a **named abort** if any
  service fails to come up, and **first-child-exit supervision** so a crash
  stops the suite rather than leaving a half-running classroom.
* `deploy/start-all.sh` brought to the same contract as `start.sh` and taught
  to start the five tool viewers; every started port is configurable from
  `deploy/smo-env.conf` and documented.
* **No start script installs from the network unasked**; the offline path is
  explained in the script's own help and in `docs/DEPLOYMENT.md`.

## Tests

`tests/test_content/test_deployment_consistency.py` (16 tests)

* `test_core_servers_accept_a_bind_address` (per server),
  `test_local_mode_binds_loopback`,
  `test_start_sh_passes_the_bind_to_every_core_service`,
  `test_every_printed_url_uses_the_access_host`;
* `test_no_start_script_installs_from_the_network_unasked`,
  `test_start_sh_explains_the_offline_path`;
* `test_dependants_wait_on_a_readiness_probe` and
  `test_a_dead_service_stops_the_suite` (per script);
* `test_start_sh_starts_every_tool_viewer` /
  `test_start_all_starts_every_tool_viewer` (per port), and
  `test_every_started_port_is_in_the_deployment_guide`.

## Files

* `start.sh`, `deploy/start-all.sh`, `deploy/start-gateway.sh`,
  `deploy/smo-env.conf`, `deploy/INSTALLATION_MANUAL.html`
* `packages/smo-simulator/src/smo_simulator/server.py`,
  `packages/smo-mcs/src/smo_mcs/server.py`,
  `packages/smo-planner/src/smo_planner/server.py`
* `docs/DEPLOYMENT.md`

## Related

* **#90** — the previous deployment defect: a documented port that nothing
  listened on, found the same way.
