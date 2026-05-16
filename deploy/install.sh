#!/usr/bin/env bash
# =============================================================================
# SMO Space Mission Simulator — Full Installation Script
# =============================================================================
# Supports: Linux (Ubuntu/Debian/Fedora/RHEL/Arch), macOS, WSL
#
# Usage:
#   ./deploy/install.sh              # Full install (all packages)
#   ./deploy/install.sh --sim-only   # Simulator + common only
#   ./deploy/install.sh --mcs-only   # MCS + common only
#   ./deploy/install.sh --plan-only  # Planner + common only
#   ./deploy/install.sh --rfsim      # Include RF simulation layer
#   ./deploy/install.sh --dev        # Include test dependencies
#   ./deploy/install.sh --offline    # Use pre-built wheels (no internet)
#   ./deploy/install.sh --check      # Verify existing installation only
# =============================================================================
set -e

# ── Colour output ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
log_fail() { echo -e "  ${RED}✗${NC} $1"; }
log_warn() { echo -e "  ${YELLOW}!${NC} $1"; }
log_info() { echo -e "  ${CYAN}→${NC} $1"; }

# ── Find project root (script is in deploy/) ──
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Parse arguments ──
INSTALL_SIM=true
INSTALL_MCS=true
INSTALL_PLAN=true
INSTALL_GW=true
INSTALL_RFSIM=false
INSTALL_DEV=false
OFFLINE=false
CHECK_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --sim-only)  INSTALL_MCS=false; INSTALL_PLAN=false; INSTALL_GW=false ;;
        --mcs-only)  INSTALL_SIM=false; INSTALL_PLAN=false; INSTALL_GW=false ;;
        --plan-only) INSTALL_SIM=false; INSTALL_MCS=false;  INSTALL_GW=false ;;
        --rfsim)     INSTALL_RFSIM=true ;;
        --dev)       INSTALL_DEV=true ;;
        --offline)   OFFLINE=true ;;
        --check)     CHECK_ONLY=true ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --sim-only   Install simulator + common only"
            echo "  --mcs-only   Install MCS + common only"
            echo "  --plan-only  Install planner + common only"
            echo "  --rfsim      Also install RF simulation layer"
            echo "  --dev        Also install test dependencies (pytest)"
            echo "  --offline    Use pre-built wheels from wheels/ directory"
            echo "  --check      Verify existing installation without installing"
            echo "  --help       Show this help"
            exit 0
            ;;
        *) echo -e "${RED}Unknown option: $arg${NC}"; echo "Use --help for usage."; exit 1 ;;
    esac
done

echo ""
echo -e "${BOLD}=======================================${NC}"
echo -e "${BOLD}  SMO Suite — Installation${NC}"
echo -e "${BOLD}=======================================${NC}"
echo ""

# ── Detect OS ──
OS=$(uname -s)
ARCH=$(uname -m)
echo -e "  Platform:  ${CYAN}${OS} ${ARCH}${NC}"

# ── Check system prerequisites ──
echo ""
echo -e "${BOLD}[1/6] Checking prerequisites...${NC}"

# Check git
if command -v git &>/dev/null; then
    log_ok "git $(git --version | awk '{print $3}')"
else
    log_warn "git not found (optional — needed only to clone the repo)"
fi

# ── Find Python 3.11+ ──
find_python() {
    for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v $cmd &>/dev/null; then
            ver=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    log_fail "Python 3.10+ not found"
    echo ""
    echo -e "${RED}Python 3.10 or newer is required.${NC}"
    echo ""
    echo "Install Python for your platform:"
    echo ""
    case "$OS" in
        Darwin)
            echo "  macOS (Homebrew):"
            echo "    brew install python@3.12"
            echo ""
            echo "  macOS (installer):"
            echo "    Download from https://www.python.org/downloads/"
            ;;
        Linux)
            echo "  Ubuntu / Debian:"
            echo "    sudo apt update && sudo apt install python3.11 python3.11-venv python3.11-dev"
            echo ""
            echo "  Fedora / RHEL / CentOS:"
            echo "    sudo dnf install python3.11"
            echo ""
            echo "  Arch Linux:"
            echo "    sudo pacman -S python"
            ;;
        *)
            echo "  Download from https://www.python.org/downloads/"
            ;;
    esac
    echo ""
    exit 1
}

PYTHON_VER=$($PYTHON --version 2>&1)
log_ok "Python: $PYTHON ($PYTHON_VER)"

# Check pip
if $PYTHON -m pip --version &>/dev/null; then
    PIP_VER=$($PYTHON -m pip --version 2>&1 | awk '{print $2}')
    log_ok "pip $PIP_VER"
else
    log_fail "pip not found"
    echo ""
    echo "Install pip:"
    echo "  $PYTHON -m ensurepip --upgrade"
    echo "  # or: sudo apt install python3-pip"
    exit 1
fi

# Check venv module
if $PYTHON -c "import venv" 2>/dev/null; then
    log_ok "venv module available"
else
    log_fail "venv module not available"
    echo ""
    echo "Install the venv module:"
    case "$OS" in
        Linux)
            echo "  Ubuntu/Debian: sudo apt install python3.11-venv"
            echo "  Fedora/RHEL:   sudo dnf install python3.11-devel"
            ;;
        *)
            echo "  Re-install Python with the venv module included."
            ;;
    esac
    exit 1
fi

# ── Check-only mode ──
if $CHECK_ONLY; then
    echo ""
    echo -e "${BOLD}[CHECK] Verifying existing installation...${NC}"
    cd "$PROJECT_ROOT"
    PASS=true

    if [ -d ".venv" ]; then
        log_ok "Virtual environment exists"
        source .venv/bin/activate
        for mod in smo_common smo_simulator smo_mcs smo_planner smo_gateway; do
            if python -c "import $mod" 2>/dev/null; then
                log_ok "$mod"
            else
                log_warn "$mod not installed"
            fi
        done
        if python -c "import smo_rfsim" 2>/dev/null; then
            log_ok "smo_rfsim (RF simulation)"
        else
            log_info "smo_rfsim not installed (optional — use --rfsim)"
        fi
        # Check key dependencies
        for dep in pydantic yaml sgp4 numpy aiohttp; do
            display_name="$dep"
            [ "$dep" = "yaml" ] && display_name="pyyaml"
            if python -c "import $dep" 2>/dev/null; then
                ver=$(python -c "import $dep; print(getattr($dep, '__version__', 'ok'))" 2>/dev/null)
                log_ok "$display_name $ver"
            else
                log_fail "$display_name missing"
                PASS=false
            fi
        done
    else
        log_fail "Virtual environment not found"
        PASS=false
    fi

    if [ -f "$PROJECT_ROOT/configs/eosat1/mission.yaml" ]; then
        log_ok "Mission config (eosat1)"
    else
        log_fail "Mission config missing"
        PASS=false
    fi

    echo ""
    if $PASS; then
        echo -e "${GREEN}Installation is healthy.${NC}"
    else
        echo -e "${RED}Issues found. Run install.sh without --check to fix.${NC}"
    fi
    exit 0
fi

# ── Step 2: Create virtual environment ──
echo ""
echo -e "${BOLD}[2/6] Setting up virtual environment...${NC}"
cd "$PROJECT_ROOT"

if [ -d ".venv" ]; then
    log_info "Virtual environment already exists (.venv)"
    source .venv/bin/activate
    log_ok "Activated existing .venv"
else
    log_info "Creating virtual environment..."
    $PYTHON -m venv .venv
    source .venv/bin/activate
    log_ok "Created and activated .venv"
fi

# Upgrade pip to avoid build issues
log_info "Upgrading pip..."
python -m pip install --upgrade pip setuptools wheel -q 2>/dev/null
PIP_VER=$(python -m pip --version | awk '{print $2}')
log_ok "pip $PIP_VER"

# ── Step 3: Install packages ──
echo ""
echo -e "${BOLD}[3/6] Installing SMO packages...${NC}"

install_pkg() {
    local pkg_path="$1"
    local pkg_name="$(basename "$pkg_path")"
    if [ -d "$PROJECT_ROOT/$pkg_path" ]; then
        if $OFFLINE && [ -d "$PROJECT_ROOT/wheels" ]; then
            pip install -q --no-index --find-links "$PROJECT_ROOT/wheels" -e "$PROJECT_ROOT/$pkg_path" 2>/dev/null \
                || pip install -q -e "$PROJECT_ROOT/$pkg_path"
        else
            pip install -q -e "$PROJECT_ROOT/$pkg_path"
        fi
        log_ok "$pkg_name"
    else
        log_fail "$pkg_name — directory not found: $pkg_path"
        return 1
    fi
}

# Always install common first (all others depend on it)
install_pkg "packages/smo-common"

INSTALL_FAIL=false

if $INSTALL_SIM; then
    install_pkg "packages/smo-simulator" || INSTALL_FAIL=true
fi

if $INSTALL_MCS; then
    install_pkg "packages/smo-mcs" || INSTALL_FAIL=true
fi

if $INSTALL_PLAN; then
    install_pkg "packages/smo-planner" || INSTALL_FAIL=true
fi

if $INSTALL_GW; then
    install_pkg "packages/smo-gateway" || INSTALL_FAIL=true
fi

if $INSTALL_RFSIM; then
    install_pkg "packages/smo-rfsim" || INSTALL_FAIL=true
fi

# ── Step 4: Install additional dependencies ──
echo ""
echo -e "${BOLD}[4/6] Installing additional dependencies...${NC}"

# Tools dependencies (orbit_tools, delayed_tm_viewer, doc_viewer)
pip install -q sgp4 numpy aiohttp 2>/dev/null && log_ok "Orbit tools deps (sgp4, numpy, aiohttp)" \
    || log_warn "Could not install orbit tools deps"

if $INSTALL_RFSIM; then
    pip install -q reedsolo 2>/dev/null && log_ok "reedsolo (Reed-Solomon codec)" \
        || log_warn "Could not install reedsolo"
fi

if $INSTALL_DEV; then
    pip install -q pytest pytest-asyncio 2>/dev/null && log_ok "Test deps (pytest, pytest-asyncio)" \
        || log_warn "Could not install test deps"
    pip install -q playwright pytest-playwright 2>/dev/null && {
        log_ok "Browser test deps (playwright, pytest-playwright)"
        playwright install chromium 2>/dev/null && log_ok "Chromium browser installed" \
            || log_warn "Chromium install failed (run: playwright install chromium)"
    } || log_warn "Could not install browser test deps"
fi

# ── Step 5: Fix vendor symlinks ──
echo ""
echo -e "${BOLD}[5/6] Configuring vendor assets...${NC}"

if [ -f "$PROJECT_ROOT/fix_vendor_symlinks.sh" ]; then
    bash "$PROJECT_ROOT/fix_vendor_symlinks.sh" >/dev/null 2>&1 && log_ok "Vendor symlinks configured" \
        || log_warn "Vendor symlink fix had warnings (UI may still work)"
else
    log_info "No vendor symlinks to fix"
fi

# Ensure deploy scripts are executable
chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null
chmod +x "$PROJECT_ROOT/start.sh" 2>/dev/null
chmod +x "$PROJECT_ROOT/fix_vendor_symlinks.sh" 2>/dev/null
log_ok "Scripts marked executable"

# ── Step 6: Verify installation ──
echo ""
echo -e "${BOLD}[6/6] Verifying installation...${NC}"

PASS=true

# Check Python packages
MODULES_TO_CHECK="smo_common"
$INSTALL_SIM  && MODULES_TO_CHECK="$MODULES_TO_CHECK smo_simulator"
$INSTALL_MCS  && MODULES_TO_CHECK="$MODULES_TO_CHECK smo_mcs"
$INSTALL_PLAN && MODULES_TO_CHECK="$MODULES_TO_CHECK smo_planner"
$INSTALL_GW   && MODULES_TO_CHECK="$MODULES_TO_CHECK smo_gateway"
$INSTALL_RFSIM && MODULES_TO_CHECK="$MODULES_TO_CHECK smo_rfsim"

for mod in $MODULES_TO_CHECK; do
    if python -c "import $mod" 2>/dev/null; then
        log_ok "$mod"
    else
        log_fail "$mod — import failed"
        PASS=false
    fi
done

# Check key dependencies
for dep in pydantic yaml sgp4 numpy aiohttp; do
    pkg_name="$dep"
    [ "$dep" = "yaml" ] && pkg_name="pyyaml"
    if python -c "import $dep" 2>/dev/null; then
        log_ok "$pkg_name"
    else
        log_fail "$pkg_name — import failed"
        PASS=false
    fi
done

# Check mission config
if [ -f "$PROJECT_ROOT/configs/eosat1/mission.yaml" ]; then
    log_ok "Mission config (eosat1)"
else
    log_fail "Mission config missing (configs/eosat1/mission.yaml)"
    PASS=false
fi

# Check tools
for tool in delayed_tm_viewer.py orbit_tools.py doc_viewer.py; do
    if [ -f "$PROJECT_ROOT/tools/$tool" ]; then
        log_ok "Tool: $tool"
    else
        log_warn "Tool not found: $tool (non-critical)"
    fi
done

# ── Summary ──
echo ""
echo -e "${BOLD}=======================================${NC}"
if $PASS && ! $INSTALL_FAIL; then
    echo -e "${GREEN}${BOLD}  Installation complete!${NC}"
    echo -e "${BOLD}=======================================${NC}"
    echo ""
    echo "  Quick start (all services on this machine):"
    echo "    cd $PROJECT_ROOT"
    echo "    ./start.sh"
    echo ""
    echo "  Or use the deploy scripts:"
    echo "    ./deploy/start-all.sh"
    echo ""
    echo "  Web UIs:"
    echo "    Instructor:  http://localhost:8080"
    echo "    MCS:         http://localhost:9090"
    echo "    Planner:     http://localhost:9091"
    if $INSTALL_RFSIM; then
        echo "    Radio RF:    http://localhost:8094  (when RF bridge active)"
    fi
    echo ""
    echo "  Documentation:"
    echo "    open deploy/INSTALLATION_MANUAL.html"
    echo ""
    if $INSTALL_DEV; then
        echo "  Run tests:"
        echo "    source .venv/bin/activate"
        echo "    pytest tests/ -v"
        echo ""
    fi
else
    echo -e "${RED}${BOLD}  Installation completed with errors.${NC}"
    echo -e "${BOLD}=======================================${NC}"
    echo ""
    echo "  Check the errors above and re-run:"
    echo "    ./deploy/install.sh"
    echo ""
    exit 1
fi
