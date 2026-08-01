#!/usr/bin/env bash
# =============================================================================
# SMO Mission Control System — Standalone Startup Script
# =============================================================================
# Starts the MCS web server. It connects to the simulator (or gateway) for
# TM/TC data and serves the operator UI to browsers on the network.
# Configure addresses in smo-env.conf before running.
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

# Packages are installed by install.sh. A start script must not reach the
# network in an offline classroom (D-3).
if ! python -c "import smo_common, smo_mcs" 2>/dev/null; then
    echo "ERROR: smo-common / smo-mcs are not installed in .venv."
    echo "       Run deploy/install.sh once, then start again."
    exit 1
fi

# Determine TM source: gateway or direct to simulator
if [ "${USE_GATEWAY:-no}" = "yes" ]; then
    TM_HOST="${GATEWAY_HOST:-localhost}"
    TM_PORT="${GATEWAY_LISTEN_PORT:-10025}"
    TM_SOURCE="Gateway"
else
    TM_HOST="${MCS_TM_HOST:-${SIMULATOR_HOST:-localhost}}"
    TM_PORT="${MCS_TM_PORT:-${SIMULATOR_TM_PORT:-8002}}"
    TM_SOURCE="Simulator"
fi

HTTP_PORT="${MCS_HTTP_PORT:-9090}"
PLANNER_HOST="${PLANNER_REACHABLE_HOST:-localhost}"
PLANNER_PORT="${PLANNER_HTTP_PORT:-9091}"

echo "============================================="
echo "  SMO Mission Control System"
echo "============================================="
echo "  Bind:          ${MCS_BIND:-0.0.0.0}:${HTTP_PORT}"
echo "  TM Source:     ${TM_SOURCE} (${TM_HOST}:${TM_PORT})"
echo "  Planner API:   http://${PLANNER_HOST}:${PLANNER_PORT}"
echo "  Config:        ${MISSION_CONFIG:-configs/eosat1/}"
echo "============================================="
echo "  MCS UI:        http://$(hostname -f 2>/dev/null || echo localhost):${HTTP_PORT}"
echo "  Delayed TM:    http://$(hostname -f 2>/dev/null || echo localhost):${HTTP_PORT}/dumps"
echo "  Dump store:    ${SMO_DUMP_DIR}"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

# Set environment variable for planner URL so the MCS server can inject it
export SMO_PLANNER_URL="http://${PLANNER_HOST}:${PLANNER_PORT}"

exec smo-mcs \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --connect "${TM_HOST}:${TM_PORT}" \
    --host "${MCS_BIND:-0.0.0.0}" \
    --port "${HTTP_PORT}"
