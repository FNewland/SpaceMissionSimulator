#!/usr/bin/env bash
# SMO Space Mission Simulator — Installation Script
# Supports: Linux (Ubuntu/Debian/Fedora/RHEL/Arch), macOS
set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Find project root (script is in deploy/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "======================================="
echo "  SMO Simulator — Installation"
echo "======================================="

# Detect OS
OS=$(uname -s)
echo "Detected OS: $OS"

# Find Python 3.11+
find_python() {
    for cmd in python3.13 python3.12 python3.11 python3; do
        if command -v $cmd &>/dev/null; then
            ver=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            major=$(echo $ver | cut -d. -f1)
            minor=$(echo $ver | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
                echo $cmd
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || { echo -e "${RED}ERROR: Python 3.11+ not found${NC}"; echo "Install: sudo apt install python3.11 (Ubuntu) or brew install python@3.11 (macOS)"; exit 1; }
echo -e "${GREEN}Using Python: $PYTHON ($($PYTHON --version))${NC}"

# Create virtual environment
cd "$PROJECT_ROOT"
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
fi
source .venv/bin/activate

# Install packages
echo "Installing SMO packages..."
if [ -d "$PROJECT_ROOT/wheels" ]; then
    echo "(Using offline wheels)"
    pip install -q --no-index --find-links "$PROJECT_ROOT/wheels" \
        -e packages/smo-common -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner \
    || { echo "Offline install failed, trying online..."; pip install -q -e packages/smo-common && pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner; }
else
    pip install -q -e packages/smo-common
    pip install -q -e packages/smo-simulator -e packages/smo-mcs -e packages/smo-planner
fi

# Install orbit tools dependencies
echo "Installing Orbit Tools dependencies..."
pip install -q sgp4 numpy aiohttp 2>/dev/null || echo -e "${YELLOW}WARNING: Could not install orbit tools deps (sgp4, numpy, aiohttp)${NC}"

# Verify installation
echo ""
echo "Verifying installation..."
PASS=true
for mod in smo_common smo_simulator smo_mcs smo_planner; do
    if python -c "import $mod" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $mod"
    else
        echo -e "  ${RED}✗${NC} $mod"
        PASS=false
    fi
done

# Verify config
if [ -f "$PROJECT_ROOT/configs/eosat1/mission.yaml" ]; then
    echo -e "  ${GREEN}✓${NC} Mission config (eosat1)"
else
    echo -e "  ${RED}✗${NC} Mission config missing"
    PASS=false
fi

echo ""
if $PASS; then
    echo -e "${GREEN}Installation complete!${NC}"
    echo ""
    echo "To start the simulator:"
    echo "  cd $PROJECT_ROOT"
    echo "  bash start.sh"
else
    echo -e "${RED}Installation completed with errors. Check above.${NC}"
    exit 1
fi
