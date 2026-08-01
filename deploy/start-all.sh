#!/usr/bin/env bash
# =============================================================================
# SMO Suite — Start All Components on This Machine
# =============================================================================
# Starts all components (Simulator, MCS, Planner, the auxiliary tool viewers
# and optionally the Gateway) on a single machine. For distributed deployment,
# use the individual start-*.sh scripts on each machine instead.
#
# Configure addresses and ports in smo-env.conf before running.
#
# Phase G hardening (brief 08 D-1…D-4), matching ../start.sh:
#   * every dependant waits on a *readiness probe*, not `sleep`; a service that
#     dies on a bad config aborts the launch instead of leaving a plausible,
#     empty console up;
#   * no network access — packages are installed by deploy/install.sh;
#   * bind addresses come from smo-env.conf and are actually passed through;
#   * the supervisor exits when the FIRST core service exits, so a dead
#     simulator stops the suite rather than freezing it.
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Load environment configuration
if [ -f "$SCRIPT_DIR/smo-env.conf" ]; then
    source "$SCRIPT_DIR/smo-env.conf"
else
    echo "ERROR: smo-env.conf not found in $SCRIPT_DIR"
    exit 1
fi

# Delayed-TM dump store — one location shared by the writer (simulator) and
# the readers (MCS dump API, simulator dump API, tools/delayed_tm_viewer.py).
export SMO_DUMP_DIR="${DUMP_DIR:-$PROJECT_DIR/workspace/dumps}"
mkdir -p "$SMO_DUMP_DIR"

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run install.sh first."
    exit 1
fi
source .venv/bin/activate

# Packages are installed by deploy/install.sh. A start script must never reach
# the network: in an offline classroom `pip install` stalls for minutes and
# then blames the wrong thing (D-3).
if ! python -c "import smo_common, smo_simulator, smo_mcs, smo_planner" 2>/dev/null; then
    echo "ERROR: the SMO packages are not installed in .venv."
    echo "       Run deploy/install.sh once on this machine, then start again."
    echo "       (This script deliberately does not install anything — an"
    echo "        offline classroom must not depend on the network.)"
    exit 1
fi
if [ "${USE_GATEWAY:-no}" = "yes" ] && ! python -c "import smo_gateway" 2>/dev/null; then
    echo "ERROR: USE_GATEWAY=yes but smo-gateway is not installed in .venv."
    echo "       Run deploy/install.sh, or set USE_GATEWAY=\"no\"."
    exit 1
fi

# ── Bind and probe addresses ──
# A bind address is not a destination: 0.0.0.0 means "every interface", so a
# readiness probe must dial 127.0.0.1 instead.
SIM_BIND="${SIMULATOR_BIND:-0.0.0.0}"
MCS_BIND_ADDR="${MCS_BIND:-0.0.0.0}"
PLANNER_BIND_ADDR="${PLANNER_BIND:-0.0.0.0}"
GW_BIND="${GATEWAY_BIND:-0.0.0.0}"
probe_addr() { if [ "$1" = "0.0.0.0" ]; then echo "127.0.0.1"; else echo "$1"; fi; }
SIM_PROBE="$(probe_addr "$SIM_BIND")"
MCS_PROBE="$(probe_addr "$MCS_BIND_ADDR")"
PLANNER_PROBE="$(probe_addr "$PLANNER_BIND_ADDR")"
GW_PROBE="$(probe_addr "$GW_BIND")"

# Track PIDs for cleanup
PIDS=()

cleanup() {
    echo ""
    echo "==> Shutting down all services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null
    echo "==> All services stopped."
}
trap cleanup EXIT INT TERM

# ── Readiness helpers ──
# `sleep` and hope was the previous contract. Poll the service's own endpoint,
# notice when the process has already died, and abort loudly (D-1).
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

wait_for_http() {
    local name="$1" url="$2" pid="$3" timeout="${4:-40}"
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

die() {
    echo "==> Aborting start-up: $1"
    echo "    Nothing further was started; no half-live console is left behind."
    exit 1
}

# --- Start Simulator ---
echo "==> Starting Simulator (TC:${SIMULATOR_TC_PORT:-8001} TM:${SIMULATOR_TM_PORT:-8002} HTTP:${SIMULATOR_HTTP_PORT:-8080}) on ${SIM_BIND}..."
smo-simulator \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --host "${SIM_BIND}" \
    --speed "${SIMULATOR_SPEED:-1.0}" &
SIM_PID=$!
PIDS+=("$SIM_PID")
wait_for_http "Simulator" "http://${SIM_PROBE}:${SIMULATOR_HTTP_PORT:-8080}/api/state" "$SIM_PID" 40 \
    || die "the simulator never became ready"
wait_for_port "Simulator TM socket" "$SIM_PROBE" "${SIMULATOR_TM_PORT:-8002}" "$SIM_PID" 15 \
    || die "the simulator TM socket never opened"

# --- Optionally start Gateway ---
if [ "${USE_GATEWAY:-no}" = "yes" ]; then
    echo "==> Starting Gateway (listen:${GATEWAY_LISTEN_PORT:-10025}) on ${GW_BIND}..."
    smo-gateway \
        --upstream "${GATEWAY_UPSTREAM_HOST:-localhost}:${GATEWAY_UPSTREAM_PORT:-${SIMULATOR_TM_PORT:-8002}}" \
        --listen "${GW_BIND}:${GATEWAY_LISTEN_PORT:-10025}" &
    GW_PID=$!
    PIDS+=("$GW_PID")
    wait_for_port "Gateway" "$GW_PROBE" "${GATEWAY_LISTEN_PORT:-10025}" "$GW_PID" 30 \
        || die "the gateway never opened its listen port"

    TM_CONNECT="${GATEWAY_HOST:-localhost}:${GATEWAY_LISTEN_PORT:-10025}"
else
    TM_CONNECT="${SIMULATOR_HOST:-localhost}:${SIMULATOR_TM_PORT:-8002}"
fi

# --- Start MCS ---
PLANNER_HOST="${PLANNER_REACHABLE_HOST:-localhost}"
PLANNER_PORT="${PLANNER_HTTP_PORT:-9091}"
export SMO_PLANNER_URL="http://${PLANNER_HOST}:${PLANNER_PORT}"

echo "==> Starting MCS (HTTP:${MCS_HTTP_PORT:-9090}) on ${MCS_BIND_ADDR}..."
smo-mcs \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --connect "${TM_CONNECT}" \
    --host "${MCS_BIND_ADDR}" \
    --port "${MCS_HTTP_PORT:-9090}" &
MCS_PID=$!
PIDS+=("$MCS_PID")
wait_for_http "MCS" "http://${MCS_PROBE}:${MCS_HTTP_PORT:-9090}/api/state" "$MCS_PID" 40 \
    || die "the MCS never became ready"

# --- Start Planner ---
echo "==> Starting Planner (HTTP:${PLANNER_HTTP_PORT:-9091}) on ${PLANNER_BIND_ADDR}..."
smo-planner \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --host "${PLANNER_BIND_ADDR}" \
    --port "${PLANNER_HTTP_PORT:-9091}" &
PLANNER_PID=$!
PIDS+=("$PLANNER_PID")
wait_for_http "Planner" "http://${PLANNER_PROBE}:${PLANNER_HTTP_PORT:-9091}/api/schedule" "$PLANNER_PID" 40 \
    || die "the planner never became ready"

# --- Auxiliary tool viewers ---
# These used to be started only by ../start.sh, so a classroom launched from
# deploy/ silently lost the delayed-TM decoder, the orbit converter, the
# manuals viewer, the scenario authoring tool and the ADCS failure explorer.
# They are non-critical: a failure warns, it does not abort the class.
AUX_STARTED=""
if [ "${START_TOOLS:-yes}" = "yes" ]; then
    TOOL_PUBLIC=""
    if [ "${TOOLS_BIND:-0.0.0.0}" = "0.0.0.0" ]; then TOOL_PUBLIC="--public"; fi

    echo "==> Starting Delayed TM viewer (HTTP:${DELAYED_TM_PORT:-8092})..."
    python "$PROJECT_DIR/tools/delayed_tm_viewer.py" --port "${DELAYED_TM_PORT:-8092}" \
        --dumps "$SMO_DUMP_DIR" $TOOL_PUBLIC &
    PIDS+=($!)

    if python -c "import sgp4, numpy, aiohttp" 2>/dev/null; then
        echo "==> Starting Orbit Tools (HTTP:${ORBIT_TOOLS_PORT:-8093})..."
        python "$PROJECT_DIR/tools/orbit_tools.py" --serve --port "${ORBIT_TOOLS_PORT:-8093}" &
        PIDS+=($!)
    else
        echo "==> WARNING: Orbit Tools not started — sgp4/numpy/aiohttp missing."
        echo "             Re-run deploy/install.sh on a networked machine, or"
        echo "             populate wheels/ and install from it offline."
    fi

    echo "==> Starting Documentation viewer (HTTP:${DOC_VIEWER_PORT:-8095})..."
    python "$PROJECT_DIR/tools/doc_viewer.py" --port "${DOC_VIEWER_PORT:-8095}" \
        --docs "$PROJECT_DIR/${MISSION_CONFIG:-configs/eosat1/}" &
    PIDS+=($!)

    echo "==> Starting Scenario Generator (HTTP:${SCENARIO_GEN_PORT:-8096})..."
    python "$PROJECT_DIR/tools/scenario_generator.py" --port "${SCENARIO_GEN_PORT:-8096}" \
        --host "${TOOLS_BIND:-0.0.0.0}" &
    PIDS+=($!)

    echo "==> Starting ADCS Failure-Scenario viewer (HTTP:${ADCS_VIEWER_PORT:-8097})..."
    python "$PROJECT_DIR/tools/adcs_sim_viewer.py" --port "${ADCS_VIEWER_PORT:-8097}" $TOOL_PUBLIC &
    PIDS+=($!)
    AUX_STARTED="yes"
fi

# --- Display URLs ---
HOSTNAME_DISPLAY="$(hostname -f 2>/dev/null || echo localhost)"

echo ""
echo "============================================="
echo "  SMO Suite — All Services Running"
echo "============================================="
echo ""
echo "  Simulator Instructor UI:"
echo "    http://${HOSTNAME_DISPLAY}:${SIMULATOR_HTTP_PORT:-8080}"
echo "  Configuration editor (instructor, read-write):"
echo "    http://${HOSTNAME_DISPLAY}:${SIMULATOR_HTTP_PORT:-8080}/config"
echo "  Report generator:"
echo "    http://${HOSTNAME_DISPLAY}:${SIMULATOR_HTTP_PORT:-8080}/reports"
echo ""
echo "  Mission Control System:"
echo "    http://${HOSTNAME_DISPLAY}:${MCS_HTTP_PORT:-9090}"
echo "  MCS report generator / config browser (read-only):"
echo "    http://${HOSTNAME_DISPLAY}:${MCS_HTTP_PORT:-9090}/reports"
echo "    http://${HOSTNAME_DISPLAY}:${MCS_HTTP_PORT:-9090}/config"
echo ""
echo "  Delayed TM archive (downlinked S15 dumps, students use this):"
echo "    http://${HOSTNAME_DISPLAY}:${MCS_HTTP_PORT:-9090}/dumps"
echo "    store: ${SMO_DUMP_DIR}"
echo ""
echo "  Mission Planner:"
echo "    http://${HOSTNAME_DISPLAY}:${PLANNER_HTTP_PORT:-9091}"
echo "  Mission Planner (wide-screen 5760x1080 layout):"
echo "    http://${HOSTNAME_DISPLAY}:${PLANNER_HTTP_PORT:-9091}/wide"
echo ""
if [ -n "$AUX_STARTED" ]; then
echo "  Delayed TM viewer:   http://${HOSTNAME_DISPLAY}:${DELAYED_TM_PORT:-8092}"
echo "  Orbit Tools:         http://${HOSTNAME_DISPLAY}:${ORBIT_TOOLS_PORT:-8093}"
echo "  Documentation:       http://${HOSTNAME_DISPLAY}:${DOC_VIEWER_PORT:-8095}"
echo "  Scenario Generator:  http://${HOSTNAME_DISPLAY}:${SCENARIO_GEN_PORT:-8096}"
echo "  ADCS Failure Sim:    http://${HOSTNAME_DISPLAY}:${ADCS_VIEWER_PORT:-8097}"
echo ""
fi
if [ "${USE_GATEWAY:-no}" = "yes" ]; then
echo "  Gateway Relay:"
echo "    ${GW_BIND}:${GATEWAY_LISTEN_PORT:-10025}"
echo ""
fi
echo "============================================="
echo "  Remote Access: replace '${HOSTNAME_DISPLAY}' with"
echo "  this machine's IP address on your network."
echo "============================================="
echo "  Press Ctrl+C to stop all services"
echo "============================================="
echo ""

# Wait for the FIRST core service to exit, then stop everything (D-1). Bare
# `wait` waited for ALL children, so a simulator that died mid-exercise left
# the MCS and planner up, serving a frozen but plausible console.
while :; do
    for entry in "Simulator:$SIM_PID" "MCS:$MCS_PID" "Planner:$PLANNER_PID"; do
        name="${entry%%:*}"; pid="${entry##*:}"
        if ! kill -0 "$pid" 2>/dev/null; then
            echo ""
            echo "==> ${name} exited — stopping the suite."
            exit 1
        fi
    done
    sleep 2
done
