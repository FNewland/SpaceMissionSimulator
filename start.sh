#!/usr/bin/env bash
# Start the SMO suite: Simulator (8080), MCS (9090), Planner (9091)
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
for arg in "$@"; do
    case "$arg" in
        --rf)       RF_MODE="RF" ;;
        --rf=*)     RF_MODE="${arg#--rf=}" ;;
        --public)   PUBLIC=1 ;;
        *)          echo "Unknown option: $arg"; echo "Usage: $0 [--rf | --rf=FRAME | --rf=RF] [--public]"; exit 1 ;;
    esac
done
# Also honour the environment variable (CLI takes priority)
if [ -z "$RF_MODE" ] && [ -n "${SMO_RF_MODE:-}" ]; then
    RF_MODE="$SMO_RF_MODE"
fi
# --public can also be requested via SMO_PUBLIC=1
if [ -z "$PUBLIC" ] && [ "${SMO_PUBLIC:-}" = "1" ]; then
    PUBLIC=1
fi

# Resolve the delayed-TM dump archive dir and export it so the simulator
# child writes its dumps there (and the viewer reads from the same place).
DUMP_DIR="${SMO_DUMP_DIR:-$SCRIPT_DIR/workspace/dumps}"
export SMO_DUMP_DIR="$DUMP_DIR"

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
if python -c "import smo_common, smo_simulator, smo_mcs, smo_planner, smo_rfsim" 2>/dev/null; then
    echo "==> Packages already installed (offline-ready, no pip needed)."
else
    echo "==> Installing packages (first run)..."
    # Try fully offline first using a local wheels/ dir if present, fall back to online
    if [ -d "$SCRIPT_DIR/wheels" ]; then
        pip install -q --no-index --find-links "$SCRIPT_DIR/wheels" \
            -e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim \
        || pip install -q -e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim
    else
        pip install -q -e packages/smo-common
        pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner -e packages/smo-rfsim
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

# ── RF Bridge (must start before MCS so it's listening) ──
RFSIM_MSG=""
RADIO_MSG=""
if [ -n "$RF_MODE" ]; then
    RFSIM_VENV="$SCRIPT_DIR/.venv-rfsim"
    if [ -d "$RFSIM_VENV" ]; then
        echo "==> Starting RF Bridge in $RF_MODE mode (Python 3.14 + GNU Radio)..."
        "$RFSIM_VENV/bin/smo-rfsim" \
            --config "$SCRIPT_DIR/configs/eosat1/rfsim.yaml" \
            --mode "$RF_MODE" --radio-web &
    else
        echo "==> Starting RF Bridge in $RF_MODE mode (standard venv)..."
        smo-rfsim --config "$SCRIPT_DIR/configs/eosat1/rfsim.yaml" \
                  --mode "$RF_MODE" --radio-web &
    fi
    RFSIM_PID=$!
    sleep 2
    RFSIM_MSG="  RF Bridge:        mode=$RF_MODE  (MCS→TM:8012, TC:8011)"
    RADIO_MSG="  Radio Status:     http://localhost:8094"
fi

# Start MCS — in RF mode, connect through the bridge; otherwise direct to sim
if [ -n "$RF_MODE" ]; then
    echo "==> Starting MCS on port 9090 (via RF bridge: TM:8012, TC:8011)..."
    smo-mcs --config configs/eosat1/ --port 9090 --connect localhost:8012 --tc-port 8011 &
else
    echo "==> Starting MCS on port 9090 (direct: TM:8002, TC:8001)..."
    smo-mcs --config configs/eosat1/ --port 9090 &
fi
MCS_PID=$!

# Start Planner (HTTP:9091)
echo "==> Starting Planner on port 9091..."
smo-planner --config configs/eosat1/ --port 9091 &
PLANNER_PID=$!

# Start Delayed TM Viewer (HTTP:8092) — reads workspace/dumps/ on demand
echo "==> Starting Delayed TM viewer on port 8092..."
python "$SCRIPT_DIR/tools/delayed_tm_viewer.py" --port 8092 --dumps "$DUMP_DIR" ${PUBLIC:+--public} &
DTM_PID=$!

# Start Orbit Tools (HTTP:8093) — TLE/state vector to TC command converter
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
    fi
fi

# Start Documentation Viewer (HTTP:8095)
echo "==> Starting Documentation viewer on port 8095..."
python "$SCRIPT_DIR/tools/doc_viewer.py" --port 8095 --docs "$SCRIPT_DIR/configs/eosat1" &
DOC_PID=$!

echo ""
echo "========================================="
echo "  SMO Suite Running"
echo "========================================="
echo "  Simulator:        http://localhost:8080"
echo "  MCS:              http://localhost:9090"
echo "  Planner:          http://localhost:9091"
echo "  Delayed TM view:  http://localhost:8092"
echo "  Orbit Tools:      http://localhost:8093"
echo "  Documentation:    http://localhost:8095"
if [ -n "$RFSIM_MSG" ]; then
    echo "$RFSIM_MSG"
    echo "$RADIO_MSG"
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

# Wait for any child to exit
wait
