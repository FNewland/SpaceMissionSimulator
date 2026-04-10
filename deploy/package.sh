#!/usr/bin/env bash
# =============================================================================
# SMO Suite — Package for Distribution
# =============================================================================
# Creates a clean distributable archive (.tar.gz) of the SMO suite.
# The archive contains everything needed to run on a fresh machine:
#   - All source packages
#   - Mission configuration
#   - Deployment scripts
#   - Installation manual
#
# Usage:
#   ./deploy/package.sh                    # Full package
#   ./deploy/package.sh --name my-build    # Custom archive name
# =============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Parse args
ARCHIVE_NAME="smo-suite"
while [ $# -gt 0 ]; do
    case "$1" in
        --name) ARCHIVE_NAME="$2"; shift 2 ;;
        *) shift ;;
    esac
done

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BUILD_DIR="/tmp/${ARCHIVE_NAME}-${TIMESTAMP}"
ARCHIVE="${PROJECT_DIR}/${ARCHIVE_NAME}-${TIMESTAMP}.tar.gz"

echo "============================================="
echo "  SMO Suite — Packaging"
echo "============================================="
echo "  Source:  $PROJECT_DIR"
echo "  Output:  $ARCHIVE"
echo "============================================="
echo ""

# Create clean build directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "==> Copying source packages..."
mkdir -p "$BUILD_DIR/packages"
for pkg in smo-common smo-simulator smo-mcs smo-planner smo-gateway; do
    cp -r "packages/$pkg" "$BUILD_DIR/packages/$pkg"
    # Remove __pycache__ and .egg-info
    find "$BUILD_DIR/packages/$pkg" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$BUILD_DIR/packages/$pkg" -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
done

echo "==> Copying mission configuration..."
cp -r configs "$BUILD_DIR/configs"

echo "==> Copying deployment scripts..."
cp -r deploy "$BUILD_DIR/deploy"
# Remove any local data
rm -f "$BUILD_DIR/deploy/.DS_Store"

echo "==> Copying root project files..."
cp pyproject.toml "$BUILD_DIR/"
[ -f README.md ] && cp README.md "$BUILD_DIR/"

echo "==> Copying documentation..."
[ -d docs ] && cp -r docs "$BUILD_DIR/docs"

echo "==> Copying tests..."
cp -r tests "$BUILD_DIR/tests"
find "$BUILD_DIR/tests" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Remove any data/archive files
rm -rf "$BUILD_DIR/data" 2>/dev/null || true
find "$BUILD_DIR" -name "*.db" -delete 2>/dev/null || true
find "$BUILD_DIR" -name ".DS_Store" -delete 2>/dev/null || true

echo "==> Creating archive..."
cd /tmp
tar -czf "$ARCHIVE" "$(basename "$BUILD_DIR")"
rm -rf "$BUILD_DIR"

SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo ""
echo "============================================="
echo "  Package created: $ARCHIVE"
echo "  Size: $SIZE"
echo "============================================="
echo ""
echo "  To install on another machine:"
echo "    1. Copy the archive to the target machine"
echo "    2. tar xzf $(basename "$ARCHIVE")"
echo "    3. cd $(basename "$BUILD_DIR")"
echo "    4. ./deploy/install.sh"
echo "    5. Edit deploy/smo-env.conf"
echo "    6. ./deploy/start-all.sh"
echo "============================================="
