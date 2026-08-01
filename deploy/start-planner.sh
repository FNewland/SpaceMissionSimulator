#!/usr/bin/env bash
# =============================================================================
# SMO Mission Planner — Standalone Startup Script
# =============================================================================
# Starts the planning tool web server.
# Configure addresses in smo-env.conf before running.
#
# The planner has TWO display modes:
#   Standard:    http://<host>:<port>/        (normal screens)
#   Wide-screen: http://<host>:<port>/wide    (5760x1080 ultra-wide)
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

# Packages are installed by install.sh; a start script must not reach the
# network in an offline classroom (D-3).
if ! python -c "import smo_common, smo_planner" 2>/dev/null; then
    echo "ERROR: smo-common / smo-planner are not installed in .venv."
    echo "       Run deploy/install.sh once, then start again."
    exit 1
fi

HTTP_PORT="${PLANNER_HTTP_PORT:-9091}"
HOSTNAME_DISPLAY="$(hostname -f 2>/dev/null || echo localhost)"

echo "============================================="
echo "  SMO Mission Planner"
echo "============================================="
echo "  Bind:       ${PLANNER_BIND:-0.0.0.0}:${HTTP_PORT}"
echo "  Config:     ${MISSION_CONFIG:-configs/eosat1/}"
echo "============================================="
echo "  Standard:   http://${HOSTNAME_DISPLAY}:${HTTP_PORT}"
echo "  Wide-screen: http://${HOSTNAME_DISPLAY}:${HTTP_PORT}/wide"
echo "============================================="
echo "  Press Ctrl+C to stop"
echo "============================================="
echo ""

exec smo-planner \
    --config "${MISSION_CONFIG:-configs/eosat1/}" \
    --host "${PLANNER_BIND:-0.0.0.0}" \
    --port "${HTTP_PORT}"
