#!/bin/bash
# Fix broken vendor symlinks that use absolute paths.
# Run from the SpaceMissionSimulation root directory.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

fix_symlink() {
    local link="$1"
    local target="$2"
    if [ -L "$link" ]; then
        current="$(readlink "$link")"
        if [ "$current" != "$target" ]; then
            echo "Fixing $link"
            echo "  was:  $current"
            echo "  now:  $target"
            rm "$link"
            ln -s "$target" "$link"
        else
            echo "OK: $link"
        fi
    else
        echo "Creating $link -> $target"
        ln -s "$target" "$link"
    fi
}

fix_symlink \
    "$ROOT/packages/smo-planner/src/smo_planner/static/vendor" \
    "../../../../../vendor"

fix_symlink \
    "$ROOT/packages/smo-mcs/src/smo_mcs/static/vendor" \
    "../../../../../vendor"

fix_symlink \
    "$ROOT/packages/smo-simulator/src/smo_simulator/instructor/static/vendor" \
    "../../../../../../vendor"

echo "Done. Vendor symlinks now use relative paths."
