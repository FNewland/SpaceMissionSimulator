# EO-Sat-1 Space Mission Operations Suite

A spacecraft simulator, mission control system and mission planner for teaching
satellite operations. It flies **EO-Sat-1**, a 6U ocean-colour Earth-observation
mission in a 450 km sun-synchronous orbit, and it speaks real ECSS PUS-C
services over real sockets, so what a student learns here transfers.

Nothing is mocked at the interface: telecommands go out over a TCP socket as
CCSDS/PUS packets, telemetry comes back the same way, and the ground software
has to decommutate it. The RF chain, the link budget, the orbit propagation,
the power balance and the attitude dynamics are all modelled rather than
scripted.

---

## Start it

```bash
./start.sh                 # everything, bound to loopback
./start.sh --public        # everything, reachable from student machines
./start.sh --rf            # add the RF chain (CCSDS framing, BER, BPSK)
```

`start.sh` polls each service for readiness before starting the one that
depends on it and aborts with a named error if any fails, so a simulator that
dies on a bad config cannot leave a plausible-but-empty console in front of a
class. It never reaches the network.

| What | Where | Who |
|---|---|---|
| **Mission Control System** | `http://<host>:9090` | **students** |
| Delayed-TM archive | `http://<host>:9090/dumps` | students |
| Reports | `http://<host>:9090/reports` | students |
| Mission Planner | `http://<host>:9091` | students |
| Documentation viewer | `http://<host>:8095` | students |
| Instructor console | `http://<host>:8080` | **instructor only** |
| Configuration editor | `http://<host>:8080/config` | **instructor only** |
| Scenario generator | `http://<host>:8096` | **instructor only** |

Full port map, firewall list and distributed-deployment shapes:
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

---

## What is in the box

| Package | What it is |
|---|---|
| `smo-simulator` | The spacecraft. Orbit, power, thermal, AOCS, propulsion, payload, OBDH, TTC; PUS services; FDIR; the instructor console |
| `smo-mcs` | The control room. Six operator positions, commanding, limit checking, alarms, trends, procedures, the delayed-TM archive |
| `smo-planner` | Pass planning, ground track, imaging opportunities, resource budgets, constraint checking |
| `smo-rfsim` | CCSDS framing, Reed–Solomon, BPSK/QPSK modulation, an RF channel model with impairments |
| `smo-gateway` | TM/TC relay for multi-site deployments |
| `smo-common` | Shared protocol, configuration, orbit, reporting and state-serialisation code |

Everything the spacecraft *is* lives in `configs/eosat1/` — the hardware sheet,
the telecommand catalogue, the telemetry dictionary, the housekeeping
structures, the monitoring rules, the FDIR model, the scenarios, the
operations manual and 67 operations procedures. The Python packages are the
machinery; the configuration is the mission.

---

## Documentation

Start here:

| Document | For |
|---|---|
| [`docs/INSTRUCTOR_GUIDE.md`](docs/INSTRUCTOR_GUIDE.md) | Running a class |
| [`docs/OPERATIONS_GUIDE.md`](docs/OPERATIONS_GUIDE.md) | Operating the console |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Setting it up on a server |
| [`deploy/INSTALLATION_MANUAL.html`](deploy/INSTALLATION_MANUAL.html) | Installing it |

Reference:

| Document | Contents |
|---|---|
| [`docs/PUS_SERVICE_REFERENCE.md`](docs/PUS_SERVICE_REFERENCE.md) | Every PUS service as implemented, including the documented deviations |
| [`docs/PARAMETER_MAP.md`](docs/PARAMETER_MAP.md) | Generated: parameter → SID → limits → S12 |
| [`docs/LINK_BUDGET_AND_RATES.md`](docs/LINK_BUDGET_AND_RATES.md) | The link budget and the data-rate ladder |
| [`docs/PROCEDURE_SCHEMA.md`](docs/PROCEDURE_SCHEMA.md) | How a procedure is written and made runnable |
| [`docs/OBCP_GUIDE.md`](docs/OBCP_GUIDE.md) | On-board control procedures (S18) |
| [`docs/PROPULSION_GUIDE.md`](docs/PROPULSION_GUIDE.md) | The propulsion module and burn operations |
| [`docs/SIMULATION_CONFIG.md`](docs/SIMULATION_CONFIG.md) | Every configuration key |
| `configs/eosat1/manual/` | The spacecraft user manual, per subsystem |

Engineering contracts — read these before changing the simulator:

| Document | Contents |
|---|---|
| [`docs/DETERMINISM.md`](docs/DETERMINISM.md) | What is reproducible, what time acceleration does to the physics, the secular-mode honesty flag |
| [`docs/THREADING_AND_STATE.md`](docs/THREADING_AND_STATE.md) | Who writes what, when the snapshot publishes, what breakpoints capture |
| [`docs/architecture.md`](docs/architecture.md) | System architecture |

Project state:

| Document | Contents |
|---|---|
| [`DEFECTS.md`](DEFECTS.md) | The defect register — the most carefully maintained document here |
| [`docs/STATUS.md`](docs/STATUS.md) | What is still open, and what is deliberately not being done |
| [`docs/CHANGELOG.md`](docs/CHANGELOG.md) | Release history |
| `docs/archive/` | Superseded reports, with an index per folder |

---

## Tests

```bash
.venv/bin/python -m pytest tests -q -n 4 --dist loadfile \
    --ignore=tests/test_e2e_browser
```

About 4 900 tests, six minutes on four workers. `tests/test_e2e_browser`
needs a live stack and is excluded by default.

The suite that matters most is `tests/test_content/`: data-driven tests over
the *contracts between artefacts* — config against code, catalogue against
dispatcher, procedure against catalogue, limit band against model behaviour,
route against UI. Nearly every recurring defect in this project's history was
a contract drifting, not a function misbehaving.

## Developing

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e packages/smo-common -e packages/smo-simulator \
            -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim
```

Several artefacts are **generated** and pinned byte-for-byte in CI. If you
change the configuration they describe, re-run the generator and commit the
result:

```bash
python tools/generate_xtce.py        # configs/eosat1/xtce/
python tools/generate_smdl.py        # configs/eosat1/smdl/
python tools/gen_command_reference.py # configs/eosat1/manual/09_command_reference.md
python tools/gen_parameter_map.py    # docs/PARAMETER_MAP.md
python tools/procedure_audit.py      # workspace/audit/ (also a CI gate)
python tools/power_budget.py         # the power budget, derived not assumed
```

## Licence

See [`LICENSE`](LICENSE).
