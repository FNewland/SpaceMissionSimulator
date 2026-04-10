#!/usr/bin/env bash
# =============================================================================
# SMO Suite — Start All Components on This Machine
# =============================================================================
# Starts all components (Simulator, MCS, Planner, and optionally Gateway)
# on a single machine. For distributed deployment, use the individual
# start-*.sh scripts on each machine instead.
#
# Configure addresses and ports in smo-env.conf before running.
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

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run install.sh first."
    exit 1
fi
source .venv/bin/activate

# Install all packages
echo "==> Checking packages..."
pip install -q -e packages/smo-common \
    -e packages/smo-simulator \
    -e packages/smo-mcs \
    -e packages/smo-planner \
    -e packages/smo-gateway 2>/dev/null

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

# Helper: wait for a TCP port to become reachable (up to 15 seconds)
wait_for_port() {
    local host="$1" port="$2" label="$3"
    local attempts=0
    while [ $attempts -lt 30 ]; do
        if (echo >/dev/tcp/"$host"/"$port") 2>/dev/null; then
            echo "    $label is ready on port $port"
            return 0
        fi
        sleep 0.5
        attempts=$((attempts + 1))
    done
    echo "    WARNING: $label did not become ready on port $port within 15s"
    return 0  # continue anyway
}

# --- Start Simulator ---
echo "==> Starting Simulator (TC:${SIMULATOR_TC_PORT:-8001} TM:${SIMULATOR_TM_PORT:-8002} HTTP:${SIMULATOR_HTTP_PORT:-8080})..."
smo-simulator \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --speed "${SIMULATOR_SPEED:-1.0}" &
PIDS+=($!)
wait_for_port "localhost" "${SIMULATOR_TM_PORT:-8002}" "Simulator"

# --- Optionally start Gateway ---
if [ "${USE_GATEWAY:-no}" = "yes" ]; then
    echo "==> Starting Gateway (listen:${GATEWAY_LISTEN_PORT:-10025})..."
    smo-gateway \
        --upstream "${GATEWAY_UPSTREAM_HOST:-localhost}:${GATEWAY_UPSTREAM_PORT:-${SIMULATOR_TM_PORT:-8002}}" \
        --listen "0.0.0.0:${GATEWAY_LISTEN_PORT:-10025}" &
    PIDS+=($!)
    wait_for_port "localhost" "${GATEWAY_LISTEN_PORT:-10025}" "Gateway"

    TM_CONNECT="${GATEWAY_HOST:-localhost}:${GATEWAY_LISTEN_PORT:-10025}"
else
    TM_CONNECT="${SIMULATOR_HOST:-localhost}:${SIMULATOR_TM_PORT:-8002}"
fi

# --- Start MCS ---
PLANNER_HOST="${PLANNER_REACHABLE_HOST:-localhost}"
PLANNER_PORT="${PLANNER_HTTP_PORT:-9091}"
export SMO_PLANNER_URL="http://${PLANNER_HOST}:${PLANNER_PORT}"

echo "==> Starting MCS (HTTP:${MCS_HTTP_PORT:-9090})..."
smo-mcs \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --connect "${TM_CONNECT}" \
    --port "${MCS_HTTP_PORT:-9090}" &
PIDS+=($!)

# --- Start Planner ---
echo "==> Starting Planner (HTTP:${PLANNER_HTTP_PORT:-9091})..."
smo-planner \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --port "${PLANNER_HTTP_PORT:-9091}" &
PIDS+=($!)

# --- Display URLs ---
HOSTNAME_DISPLAY="$(hostname -f 2>/dev/null || echo localhost)"

echo ""
echo "============================================="
echo "  SMO Suite — All Services Running"
echo "============================================="
echo ""
echo "  Simulator Instructor UI:"
echo "    http://${HOSTNAME_DISPLAY}:${SIMULATOR_HTTP_PORT:-8080}"
echo ""
echo "  Mission Control System:"
echo "    http://${HOSTNAME_DISPLAY}:${MCS_HTTP_PORT:-9090}"
echo ""
echo "  Mission Planner (Standard):"
echo "    http://${HOSTNAME_DISPLAY}:${PLANNER_HTTP_PORT:-9091}"
echo ""
echo "  Mission Planner (Wide-Screen 5760x1080):"
echo "    http://${HOSTNAME_DISPLAY}:${PLANNER_HTTP_PORT:-9091}/wide"
echo ""
if [ "${USE_GATEWAY:-no}" = "yes" ]; then
echo "  Gateway Relay:"
echo "    Port ${GATEWAY_LISTEN_PORT:-10025}"
echo ""
fi
echo "============================================="
echo "  Remote Access: replace 'localhost' with"
echo "  this machine's IP address on your network."
echo "============================================="
echo "  Press Ctrl+C to stop all services"
echo "============================================="
echo ""

# Wait for any child to exit
wait
