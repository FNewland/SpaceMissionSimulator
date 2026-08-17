#!/usr/bin/env bash
# Start the SMO suite: Simulator ($SMO_SIM_HTTP_PORT, default 8080),
# MCS (9090), Planner (9091)
#
# Usage:
#   ./start.sh            # standard mode (no RF)
#   ./start.sh --rf       # with RF bridge in FRAME mode (CCSDS framing + BER)
#   ./start.sh --rf=RF    # with RF bridge in RF mode (BPSK mod/demod)
#   ./start.sh --rf=FRAME # same as --rf
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── Parse arguments ──
RF_MODE=""
PUBLIC=""
REAL=""
for arg in "$@"; do
    case "$arg" in
        --rf)       RF_MODE="RF" ;;
        --rf=*)     RF_MODE="${arg#--rf=}" ;;
        --public)   PUBLIC=1 ;;
        --real)     REAL=1 ;;
        --host=*)   SMO_BIND="${arg#--host=}" ;;
        *)          echo "Unknown option: $arg"; echo "Usage: $0 [--rf | --rf=FRAME | --rf=RF] [--public] [--real] [--host=ADDR]"; exit 1 ;;
    esac
done

# ── Time source (ground UTC / Planner "now") ──
# "sim"  → MCS & Planner closed-loop follow the simulator's sim_time/speed.
# "real" → both use real wall-clock UTC (for a real mission link).
# Children inherit these env vars; we also pass --time-source explicitly.
# Precedence in each service: CLI flag > env > mission-config field > default.
SMO_TIME_SOURCE="${SMO_TIME_SOURCE:-sim}"
if [ -n "$REAL" ]; then
    SMO_TIME_SOURCE="real"
fi
export SMO_TIME_SOURCE
SMO_SIM_STATE_URL="${SMO_SIM_STATE_URL:-}"   # resolved once the bind is known
# ── Simulator HTTP port ──
# The simulator used to hard-code 8080, so a host where 8080 was already
# taken could not run the suite at all. It now takes --port; export the
# same name smo-mcs reads for the sim-state URL so both ends agree.
SMO_SIM_HTTP_PORT="${SMO_SIM_HTTP_PORT:-8080}"
export SMO_SIM_HTTP_PORT
# Also honour the environment variable (CLI takes priority)
if [ -z "$RF_MODE" ] && [ -n "${SMO_RF_MODE:-}" ]; then
    RF_MODE="$SMO_RF_MODE"
fi
# --public can also be requested via SMO_PUBLIC=1
if [ -z "$PUBLIC" ] && [ "${SMO_PUBLIC:-}" = "1" ]; then
    PUBLIC=1
fi

# Resolve the delayed-TM dump archive dir and export it so EVERY child agrees
# on one location: the simulator writes its S15 dumps there, and the MCS,
# the simulator's own dump API and the standalone viewer all read from it.
# Resolution order (see smo_common.dump_store): SMO_DUMP_DIR > mission.yaml
# `dump_dir:` > workspace/dumps. We pin an absolute path here so a child
# started from a different working directory still finds the same store.
DUMP_DIR="${SMO_DUMP_DIR:-$SCRIPT_DIR/workspace/dumps}"
export SMO_DUMP_DIR="$DUMP_DIR"

# Host name/IP to print in the banner. With --public the services bind
# 0.0.0.0, so printing "localhost" would hand students a URL that resolves to
# their OWN machine; print something reachable instead (defect #90).
if [ -n "$PUBLIC" ]; then
    ACCESS_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -z "$ACCESS_HOST" ] && ACCESS_HOST="$(hostname -f 2>/dev/null || hostname)"
    [ -z "$ACCESS_HOST" ] && ACCESS_HOST="localhost"
else
    ACCESS_HOST="localhost"
fi

# Bind address. In LOCAL mode the banner used to claim "services bound to
# loopback where supported" while the simulator, MCS and planner hard-coded
# 0.0.0.0 unconditionally (D-2). They now honour --host/$SMO_BIND, so the
# banner and the sockets agree.
if [ -n "$PUBLIC" ]; then
    SMO_BIND="${SMO_BIND:-0.0.0.0}"
else
    SMO_BIND="${SMO_BIND:-127.0.0.1}"
fi
export SMO_BIND
# Address a readiness probe (and the children) should use to reach a service.
# 0.0.0.0 is a bind address, never a destination.
PROBE_HOST="$SMO_BIND"
[ "$PROBE_HOST" = "0.0.0.0" ] && PROBE_HOST="127.0.0.1"

# ── Readiness ──
# `sleep 2` and hope was the previous contract: if smo-simulator died on a bad
# config, MCS and Planner still came up and showed an empty, plausible console
# (D-1). Poll the service's own endpoint instead, and abort loudly.
wait_for_http() {
    local name="$1" url="$2" pid="$3" timeout="${4:-30}"
    local waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo ""
            echo "ERROR: $name exited during start-up. Its output is above."
            return 1
        fi
        if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
            echo "    $name ready after ${waited}s"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo ""
    echo "ERROR: $name did not become ready within ${timeout}s ($url)."
    return 1
}

wait_for_port() {
    local name="$1" host="$2" port="$3" pid="$4" timeout="${5:-30}"
    local waited=0
    while [ "$waited" -lt "$timeout" ]; do
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            echo ""
            echo "ERROR: $name exited during start-up. Its output is above."
            return 1
        fi
        if python -c 'import socket,sys; s=socket.socket(); s.settimeout(1.0); sys.exit(0 if not s.connect_ex((sys.argv[1], int(sys.argv[2]))) else 1)' "$host" "$port" 2>/dev/null; then
            echo "    $name ready after ${waited}s"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo ""
    echo "ERROR: $name did not open $host:$port within ${timeout}s."
    return 1
}

die() {
    echo "==> Aborting start-up: $1"
    echo "    Nothing else was started; no half-live console is left behind."
    exit 1
}

# Activate virtual environment
if [ ! -f ".venv/bin/activate" ]; then
    echo "ERROR: .venv is missing or incomplete (.venv/bin/activate not found)."
    echo "Recreate it with:"
    echo "    rm -rf .venv && python3.11 -m venv .venv && source .venv/bin/activate \\"
    echo "      && pip install -e packages/smo-common -e packages/smo-simulator \\"
    echo "         -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim"
    exit 1
fi
source .venv/bin/activate

# Ensure local vendor symlinks are correct so the UI works fully offline
if [ -f "$SCRIPT_DIR/fix_vendor_symlinks.sh" ]; then
    bash "$SCRIPT_DIR/fix_vendor_symlinks.sh" >/dev/null 2>&1 || true
fi

# Skip pip install entirely if everything is already importable (no internet needed)
#
# D-3: a start script that reaches the network stalls for minutes in an
# offline classroom and then blames the wrong thing. The order is therefore
# (1) already installed → do nothing, (2) vendored wheels/ → install offline,
# (3) otherwise refuse and say exactly what to do. A networked developer who
# genuinely wants the online path opts in with SMO_ALLOW_NETWORK_INSTALL=1.
SMO_PKGS="-e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim"
if python -c "import smo_common, smo_simulator, smo_mcs, smo_planner, smo_rfsim" 2>/dev/null; then
    echo "==> Packages already installed (offline-ready, no pip needed)."
elif [ -d "$SCRIPT_DIR/wheels" ]; then
    echo "==> Installing packages from vendored wheels/ (offline, first run)..."
    pip install -q --no-index --find-links "$SCRIPT_DIR/wheels" $SMO_PKGS \
        || { echo "ERROR: offline install from $SCRIPT_DIR/wheels failed."
             echo "       The wheels/ directory is incomplete for this Python."
             exit 1; }
elif [ "${SMO_ALLOW_NETWORK_INSTALL:-0}" = "1" ]; then
    echo "==> Installing packages from the network (SMO_ALLOW_NETWORK_INSTALL=1)..."
    pip install -q -e packages/smo-common
    pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim
else
    echo "ERROR: the SMO packages are not installed in .venv and there is no"
    echo "       wheels/ directory to install them from offline."
    echo ""
    echo "       This script does not reach the network: in an offline"
    echo "       classroom that stalls and then reports the wrong failure."
    echo ""
    echo "       On a networked machine, run ONCE:"
    echo "           source .venv/bin/activate"
    echo "           pip install $SMO_PKGS"
    echo "       or re-run this script with SMO_ALLOW_NETWORK_INSTALL=1."
    echo ""
    echo "       For an offline lab, build the wheels once elsewhere:"
    echo "           pip wheel -w wheels/ $SMO_PKGS sgp4 numpy aiohttp"
    echo "       and ship wheels/ with the tree."
    exit 1
fi

# Cleanup on exit — kill all background jobs
cleanup() {
    echo ""
    echo "==> Shutting down..."
    kill $(jobs -p) 2>/dev/null
    wait 2>/dev/null
    echo "==> All services stopped."
}
trap cleanup EXIT INT TERM

export SMO_SIM_STATE_URL="${SMO_SIM_STATE_URL:-http://$PROBE_HOST:$SMO_SIM_HTTP_PORT/api/state}"

# Start Simulator (TC:8001, TM:8002, HTTP/WS:$SMO_SIM_HTTP_PORT)
echo "==> Starting Simulator on $SMO_BIND:$SMO_SIM_HTTP_PORT..."
smo-simulator --config configs/eosat1/ --host "$SMO_BIND" --port "$SMO_SIM_HTTP_PORT" &
SIM_PID=$!
# The simulator is the root dependency: MCS and Planner showing an empty but
# plausible console because it died on a bad config is the exact failure mode
# N-1 was meant to eliminate, one layer up (D-1).
wait_for_http "Simulator" "http://$PROBE_HOST:$SMO_SIM_HTTP_PORT/api/state" "$SIM_PID" 40 \
    || die "the simulator never became ready"
wait_for_port "Simulator TM socket" "$PROBE_HOST" 8002 "$SIM_PID" 15 \
    || die "the simulator TM socket never opened"

# ── RF Bridge (must start before MCS so it's listening) ──
RFSIM_MSG=""
RADIO_MSG=""
if [ -n "$RF_MODE" ]; then
    RFSIM_VENV="$SCRIPT_DIR/.venv-rfsim"
    # Look for a *runnable* interpreter inside the dedicated RF venv. This covers
    # both Unix (bin/) and Windows/Git-Bash (Scripts/) layouts, and guards against
    # a .venv-rfsim that was copied from another machine: such a venv has a console
    # script with a hard-coded shebang pointing at an interpreter that doesn't exist
    # here, which fails with "bad interpreter: No such file or directory". We probe
    # the interpreter directly and only use it if it can import smo_rfsim; otherwise
    # we fall back to the main venv's smo-rfsim (installed by the setup above).
    RFSIM_PY=""
    for cand in \
        "$RFSIM_VENV/bin/python3" "$RFSIM_VENV/bin/python" \
        "$RFSIM_VENV/Scripts/python.exe" "$RFSIM_VENV/Scripts/python"; do
        if [ -f "$cand" ] && "$cand" -c "import smo_rfsim" >/dev/null 2>&1; then
            RFSIM_PY="$cand"
            break
        fi
    done

    if [ -n "$RFSIM_PY" ]; then
        echo "==> Starting RF Bridge in $RF_MODE mode (dedicated .venv-rfsim)..."
        # Invoke via the interpreter directly (not the console script) so we never
        # depend on the console script's baked-in shebang path.
        "$RFSIM_PY" -c "import sys; from smo_rfsim.bridge import main; sys.exit(main())" \
            --config "$SCRIPT_DIR/configs/eosat1/rfsim.yaml" \
            --mode "$RF_MODE" --radio-web &
    else
        if [ -d "$RFSIM_VENV" ]; then
            echo "==> Note: .venv-rfsim exists but has no runnable interpreter here"
            echo "         (e.g. copied from another machine) — falling back to main venv."
        fi
        echo "==> Starting RF Bridge in $RF_MODE mode (main venv)..."
        smo-rfsim --config "$SCRIPT_DIR/configs/eosat1/rfsim.yaml" \
                  --mode "$RF_MODE" --radio-web &
    fi
    RFSIM_PID=$!
    wait_for_port "RF Bridge" "$PROBE_HOST" 8012 "$RFSIM_PID" 30 \
        || die "the RF bridge never opened its TM port"
    RFSIM_MSG="  RF Bridge:        mode=$RF_MODE  (MCS→TM:8012, TC:8011)"
    RADIO_MSG="  Radio Status:     http://$ACCESS_HOST:8094"
fi

# Start MCS — in RF mode, connect through the bridge; otherwise direct to sim
if [ -n "$RF_MODE" ]; then
    echo "==> Starting MCS on $SMO_BIND:9090 (via RF bridge: TM:8012, TC:8011)..."
    smo-mcs --config configs/eosat1/ --port 9090 --host "$SMO_BIND" --connect "$PROBE_HOST:8012" --tc-port 8011 --time-source "$SMO_TIME_SOURCE" &
else
    echo "==> Starting MCS on $SMO_BIND:9090 (direct: TM:8002, TC:8001)..."
    smo-mcs --config configs/eosat1/ --port 9090 --host "$SMO_BIND" --connect "$PROBE_HOST:8002" --time-source "$SMO_TIME_SOURCE" &
fi
MCS_PID=$!
wait_for_http "MCS" "http://$PROBE_HOST:9090/api/state" "$MCS_PID" 40 \
    || die "the MCS never became ready"

# Start Planner (HTTP:9091)
echo "==> Starting Planner on $SMO_BIND:9091..."
smo-planner --config configs/eosat1/ --port 9091 --host "$SMO_BIND" --connect-host "$PROBE_HOST" --time-source "$SMO_TIME_SOURCE" &
PLANNER_PID=$!
wait_for_http "Planner" "http://$PROBE_HOST:9091/api/schedule" "$PLANNER_PID" 40 \
    || die "the planner never became ready"

# Start Delayed TM Viewer (HTTP:8092) — reads workspace/dumps/ on demand
echo "==> Starting Delayed TM viewer on port 8092..."
python "$SCRIPT_DIR/tools/delayed_tm_viewer.py" --port 8092 --dumps "$DUMP_DIR" ${PUBLIC:+--public} &
DTM_PID=$!

# Start Orbit Tools (HTTP:8093) — TLE/state vector to TC command converter
if python -c "import sgp4, numpy, aiohttp" 2>/dev/null; then
    echo "==> Starting Orbit Tools on port 8093..."
    python "$SCRIPT_DIR/tools/orbit_tools.py" --serve --port 8093 &
    ORB_PID=$!
elif [ -d "$SCRIPT_DIR/wheels" ]; then
    # Offline classroom (D-3): a start script must never reach the network.
    # If the vendored wheels are present, install from them; otherwise say
    # exactly what is missing and carry on without Orbit Tools.
    echo "==> Orbit Tools: installing sgp4/numpy/aiohttp from vendored wheels/..."
    pip install -q --no-index --find-links "$SCRIPT_DIR/wheels" sgp4 numpy aiohttp 2>/dev/null || true
    if python -c "import sgp4, numpy, aiohttp" 2>/dev/null; then
        echo "==> Starting Orbit Tools on port 8093..."
        python "$SCRIPT_DIR/tools/orbit_tools.py" --serve --port 8093 &
        ORB_PID=$!
    else
        echo "==> WARNING: Orbit Tools not started — sgp4/numpy/aiohttp missing"
        echo "             and not available in $SCRIPT_DIR/wheels."
    fi
else
    echo "==> WARNING: Orbit Tools not started — sgp4/numpy/aiohttp are not"
    echo "             installed and there is no wheels/ directory to install"
    echo "             them from offline. Run:"
    echo "                 pip install sgp4 numpy aiohttp"
    echo "             once on a networked machine, or populate wheels/."
fi

# Start Documentation Viewer (HTTP:8095)
echo "==> Starting Documentation viewer on port 8095..."
python "$SCRIPT_DIR/tools/doc_viewer.py" --port 8095 --docs "$SCRIPT_DIR/configs/eosat1" &
DOC_PID=$!

# Start Scenario Generator (HTTP:8096) — authors configs/eosat1/scenarios/*.yaml
echo "==> Starting Scenario Generator on port 8096..."
python "$SCRIPT_DIR/tools/scenario_generator.py" --port 8096 ${PUBLIC:+--host 0.0.0.0} &
SCENGEN_PID=$!

# Start ADCS Failure-Scenario Viewer (HTTP:8097) — offline CERISE debris-strike
# explorer; physics comes from ADCS_failure_sim/cubesat_adcs_sim.py
echo "==> Starting ADCS Failure-Scenario viewer on port 8097..."
python "$SCRIPT_DIR/tools/adcs_sim_viewer.py" --port 8097 ${PUBLIC:+--public} &
ADCS_PID=$!

# The tool viewers are auxiliary: a failure warns rather than aborting the
# class, but it must be *visible* — a printed URL for a port nothing listens
# on is the same "plausible but empty" failure as D-1, one tool down.
check_optional() {
    wait_for_port "$1" "$PROBE_HOST" "$2" "$3" 15 >/dev/null 2>&1 \
        || echo "==> WARNING: $1 did not come up on port $2 — its URL below will not work."
}
check_optional "Delayed TM viewer"   8092 "$DTM_PID"
if [ -n "${ORB_PID:-}" ]; then check_optional "Orbit Tools" 8093 "$ORB_PID"; fi
check_optional "Documentation viewer" 8095 "$DOC_PID"
check_optional "Scenario Generator"   8096 "$SCENGEN_PID"
check_optional "ADCS Failure viewer"  8097 "$ADCS_PID"

echo ""
echo "========================================="
echo "  SMO Suite Running"
echo "========================================="
echo "  Simulator:        http://$ACCESS_HOST:$SMO_SIM_HTTP_PORT"
echo "  Config editor:    http://$ACCESS_HOST:$SMO_SIM_HTTP_PORT/config    (instructor, read-write)"
echo "  Report generator: http://$ACCESS_HOST:$SMO_SIM_HTTP_PORT/reports"
echo "  MCS:              http://$ACCESS_HOST:9090"
echo "  MCS reports:      http://$ACCESS_HOST:9090/reports"
echo "  MCS config view:  http://$ACCESS_HOST:9090/config    (read-only)"
echo "  Delayed TM:       http://$ACCESS_HOST:9090/dumps    <- students use this"
echo "  Planner:          http://$ACCESS_HOST:9091"
echo "  Planner (wide):   http://$ACCESS_HOST:9091/wide"
echo "  Delayed TM view:  http://$ACCESS_HOST:8092          (rich decode/plots)"
if [ -n "${ORB_PID:-}" ]; then
echo "  Orbit Tools:      http://$ACCESS_HOST:8093"
else
echo "  Orbit Tools:      NOT RUNNING (sgp4/numpy/aiohttp unavailable)"
fi
echo "  Documentation:    http://$ACCESS_HOST:8095"
echo "  Scenario Gen:     http://$ACCESS_HOST:8096"
echo "  ADCS Failure Sim: http://$ACCESS_HOST:8097"
echo "  Dump store:       $DUMP_DIR"
echo "  Time source:      $SMO_TIME_SOURCE  (sim_state_url=$SMO_SIM_STATE_URL)"
if [ -n "$RFSIM_MSG" ]; then
    echo "$RFSIM_MSG"
    echo "$RADIO_MSG"
fi
echo "========================================="
if [ -n "$PUBLIC" ]; then
    echo "  PUBLIC mode: services bound to $SMO_BIND — reachable from client"
    echo "  machines. Give students the URLs above (see docs/DEPLOYMENT.md)."
else
    echo "  LOCAL mode: simulator, MCS and planner bound to $SMO_BIND."
    echo "  Use --public (or --host=ADDR) for a classroom/server deployment."
fi
echo "========================================="
if [ -n "$RF_MODE" ]; then
    echo "  RF mode: $RF_MODE"
else
    echo "  RF mode: off  (use --rf to enable)"
fi
echo "  Press Ctrl+C to stop all services"
echo "========================================="
echo ""

# Wait for the FIRST child to exit, then stop everything (D-1). Bare `wait`
# waited for ALL children, so a simulator that died mid-exercise left the MCS
# and planner up, serving a frozen but plausible console.
while :; do
    if ! kill -0 "$SIM_PID" 2>/dev/null; then
        echo ""
        echo "==> Simulator exited — stopping the suite."
        exit 1
    fi
    if ! kill -0 "$MCS_PID" 2>/dev/null; then
        echo ""
        echo "==> MCS exited — stopping the suite."
        exit 1
    fi
    if ! kill -0 "$PLANNER_PID" 2>/dev/null; then
        echo ""
        echo "==> Planner exited — stopping the suite."
        exit 1
    fi
    sleep 2
done
