#!/usr/bin/env bash
# =============================================================================
# SMO Simulator — Standalone Startup Script
# =============================================================================
# Starts only the spacecraft simulator.
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
    echo "  Copy smo-env.conf.example to smo-env.conf and edit it."
    exit 1
fi

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run install.sh first, or create manually:"
    echo "  python3.11 -m venv .venv && source .venv/bin/activate"
    echo "  pip install -e packages/smo-common -e packages/smo-simulator"
    exit 1
fi
source .venv/bin/activate

# Ensure packages are installed
pip install -q -e packages/smo-common -e packages/smo-simulator 2>/dev/null

echo "============================================="
echo "  SMO Simulator"
echo "============================================="
echo "  TC Port:    ${SIMULATOR_TC_PORT:-8001}"
echo "  TM Port:    ${SIMULATOR_TM_PORT:-8002}"
echo "  HTTP Port:  ${SIMULATOR_HTTP_PORT:-8080}"
echo "  Speed:      ${SIMULATOR_SPEED:-1.0}x"
echo "  Config:     ${MISSION_CONFIG:-configs/eosat1/}"
echo "============================================="
echo "  Instructor UI: http://$(hostname -f 2>/dev/null || echo localhost):${SIMULATOR_HTTP_PORT:-8080}"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

exec smo-simulator \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --speed "${SIMULATOR_SPEED:-1.0}"
