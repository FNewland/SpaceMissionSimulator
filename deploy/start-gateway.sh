#!/usr/bin/env bash
# =============================================================================
# SMO Gateway — Standalone Startup Script
# =============================================================================
# Starts the TM/TC relay gateway for multi-site MCS deployments.
# The gateway sits between the simulator and multiple MCS instances,
# fanning out TM to all connected clients and aggregating TC upstream.
#
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

# Packages are installed by deploy/install.sh. A start script must not reach
# the network in an offline classroom (D-3).
if ! python -c "import smo_common, smo_gateway" 2>/dev/null; then
    echo "ERROR: smo-common / smo-gateway are not installed in .venv."
    echo "       Run deploy/install.sh once, then start again."
    exit 1
fi

UPSTREAM_HOST="${GATEWAY_UPSTREAM_HOST:-${SIMULATOR_HOST:-localhost}}"
UPSTREAM_PORT="${GATEWAY_UPSTREAM_PORT:-${SIMULATOR_TM_PORT:-8002}}"
LISTEN_PORT="${GATEWAY_LISTEN_PORT:-10025}"
LISTEN_BIND="${GATEWAY_BIND:-0.0.0.0}"

echo "============================================="
echo "  SMO Gateway (TM/TC Relay)"
echo "============================================="
echo "  Upstream:  ${UPSTREAM_HOST}:${UPSTREAM_PORT}"
echo "  Listen:    ${LISTEN_BIND}:${LISTEN_PORT}"
echo "============================================="
echo "  MCS instances should connect to port ${LISTEN_PORT}"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

exec smo-gateway \
    --upstream "${UPSTREAM_HOST}:${UPSTREAM_PORT}" \
    --listen "${LISTEN_BIND}:${LISTEN_PORT}"
