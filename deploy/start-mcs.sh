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

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run install.sh first."
    exit 1
fi
source .venv/bin/activate

# Ensure packages are installed
pip install -q -e packages/smo-common -e packages/smo-mcs 2>/dev/null

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
echo "  HTTP Port:     ${HTTP_PORT}"
echo "  TM Source:     ${TM_SOURCE} (${TM_HOST}:${TM_PORT})"
echo "  Planner API:   http://${PLANNER_HOST}:${PLANNER_PORT}"
echo "  Config:        ${MISSION_CONFIG:-configs/eosat1/}"
echo "============================================="
echo "  MCS UI: http://$(hostname -f 2>/dev/null || echo localhost):${HTTP_PORT}"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

# Set environment variable for planner URL so the MCS server can inject it
export SMO_PLANNER_URL="http://${PLANNER_HOST}:${PLANNER_PORT}"

exec smo-mcs \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --connect "${TM_HOST}:${TM_PORT}" \
    --port "${HTTP_PORT}"
