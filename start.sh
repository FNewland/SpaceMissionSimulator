#!/usr/bin/env bash
# Start the SMO suite: Simulator (8080), MCS (9090), Planner (9091)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ ! -d ".venv" ]; then
    echo "ERROR: .venv not found. Create it first with: python3.11 -m venv .venv"
    exit 1
fi
source .venv/bin/activate

# Ensure local vendor symlinks are correct so the UI works fully offline
if [ -f "$SCRIPT_DIR/fix_vendor_symlinks.sh" ]; then
    bash "$SCRIPT_DIR/fix_vendor_symlinks.sh" >/dev/null 2>&1 || true
fi

# Skip pip install entirely if everything is already importable (no internet needed)
if python -c "import smo_common, smo_simulator, smo_mcs, smo_planner" 2>/dev/null; then
    echo "==> Packages already installed (offline-ready, no pip needed)."
else
    echo "==> Installing packages (first run)..."
    # Try fully offline first using a local wheels/ dir if present, fall back to online
    if [ -d "$SCRIPT_DIR/wheels" ]; then
        pip install -q --no-index --find-links "$SCRIPT_DIR/wheels" \
            -e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner \
        || pip install -q -e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner
    else
        pip install -q -e packages/smo-common
        pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner
    fi
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

# Start Simulator (TC:8001, TM:8002, HTTP/WS:8080)
echo "==> Starting Simulator on port 8080..."
smo-simulator --config configs/eosat1/ &
SIM_PID=$!
sleep 2

# Start MCS (HTTP:9090, connects to Simulator TM:8002 + TC:8001)
echo "==> Starting MCS on port 9090..."
smo-mcs --config configs/eosat1/ --port 9090 &
MCS_PID=$!

# Start Planner (HTTP:9091)
echo "==> Starting Planner on port 9091..."
smo-planner --config configs/eosat1/ --port 9091 &
PLANNER_PID=$!

# Start Delayed TM Viewer (HTTP:8092) — reads workspace/dumps/ on demand
echo "==> Starting Delayed TM viewer on port 8092..."
python "$SCRIPT_DIR/tools/delayed_tm_viewer.py" --port 8092 --dumps "$SCRIPT_DIR/workspace/dumps" &
DTM_PID=$!

# Start Orbit Tools (HTTP:8093) — TLE/state vector to TC command converter
# Check dependencies first (sgp4, numpy, aiohttp are not in the core packages)
if python -c "import sgp4, numpy, aiohttp" 2>/dev/null; then
    echo "==> Starting Orbit Tools on port 8093..."
    python "$SCRIPT_DIR/tools/orbit_tools.py" --serve --port 8093 &
    ORB_PID=$!
else
    echo "==> Orbit Tools: missing dependencies. Installing sgp4 numpy aiohttp..."
    pip install -q sgp4 numpy aiohttp 2>/dev/null || true
    if python -c "import sgp4, numpy, aiohttp" 2>/dev/null; then
        echo "==> Starting Orbit Tools on port 8093..."
        python "$SCRIPT_DIR/tools/orbit_tools.py" --serve --port 8093 &
        ORB_PID=$!
    else
        echo "==> WARNING: Orbit Tools could not start (pip install sgp4 numpy aiohttp failed)"
        echo "    You can install manually: pip install sgp4 numpy aiohttp"
    fi
fi

echo ""
echo "========================================="
echo "  SMO Suite Running"
echo "========================================="
echo "  Simulator:        http://localhost:8080"
echo "  MCS:              http://localhost:9090"
echo "  Planner:          http://localhost:9091"
echo "  Delayed TM view:  http://localhost:8092"
echo "  Orbit Tools:      http://localhost:8093"
echo "========================================="
echo "  Press Ctrl+C to stop all services"
echo "========================================="
echo ""

# Wait for any child to exit
wait
