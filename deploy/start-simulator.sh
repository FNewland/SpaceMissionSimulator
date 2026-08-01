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

# Delayed-TM dump store — one location shared by the writer (simulator) and
# the readers (MCS dump API, simulator dump API, tools/delayed_tm_viewer.py).
export SMO_DUMP_DIR="${DUMP_DIR:-$PROJECT_DIR/workspace/dumps}"
mkdir -p "$SMO_DUMP_DIR"

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Run install.sh first, or create manually:"
    echo "  python3.11 -m venv .venv && source .venv/bin/activate"
    echo "  pip install -e packages/smo-common -e packages/smo-simulator"
    exit 1
fi
source .venv/bin/activate

# Packages are installed by install.sh; a start script must not reach the
# network in an offline classroom (D-3).
if ! python -c "import smo_common, smo_simulator" 2>/dev/null; then
    echo "ERROR: smo-common / smo-simulator are not installed in .venv."
    echo "       Run deploy/install.sh once, then start again."
    exit 1
fi

echo "============================================="
echo "  SMO Simulator"
echo "============================================="
echo "  TC Port:    ${SIMULATOR_TC_PORT:-8001}"
echo "  TM Port:    ${SIMULATOR_TM_PORT:-8002}"
echo "  Bind:       ${SIMULATOR_BIND:-0.0.0.0}:${SIMULATOR_HTTP_PORT:-8080}"
echo "  Speed:      ${SIMULATOR_SPEED:-1.0}x"
echo "  Config:     ${MISSION_CONFIG:-configs/eosat1/}"
echo "============================================="
echo "  Instructor UI: http://$(hostname -f 2>/dev/null || echo localhost):${SIMULATOR_HTTP_PORT:-8080}"
echo "  Delayed TM:    http://$(hostname -f 2>/dev/null || echo localhost):${SIMULATOR_HTTP_PORT:-8080}/dumps"
echo "  Dump store:    ${SMO_DUMP_DIR}"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

exec smo-simulator \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --host "${SIMULATOR_BIND:-0.0.0.0}" \
    --speed "${SIMULATOR_SPEED:-1.0}"
