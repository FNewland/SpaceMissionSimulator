#!/usr/bin/env bash
#
# sync_remediation.sh — commit + push the 2026-05-24 defect-remediation work.
#
# Stages ONLY the remediation files (source fixes, configs, defect register,
# remediation plan, new defect bodies, and the regression test) — deliberately
# NOT the tracked .venv/ churn or any unrelated in-flight edits in your tree.
#
# Run from anywhere; it resolves the repo root from its own location.
#
# Usage:
#   ./scripts/sync_remediation.sh             # stage, commit, push to origin/main
#   ./scripts/sync_remediation.sh --no-push   # stage + commit only
#   ./scripts/sync_remediation.sh --issues    # also file defect issues via gh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DO_PUSH=1
DO_ISSUES=0
for arg in "$@"; do
    case "$arg" in
        --no-push) DO_PUSH=0 ;;
        --issues)  DO_ISSUES=1 ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# Clear any stale index lock (harmless if absent).
rm -f .git/index.lock 2>/dev/null || true

FILES=(
  packages/smo-simulator/src/smo_simulator/engine.py
  packages/smo-simulator/src/smo_simulator/service_dispatch.py
  packages/smo-simulator/src/smo_simulator/instructor/app.py
  packages/smo-simulator/src/smo_simulator/instructor/static/index.html
  packages/smo-simulator/src/smo_simulator/models/aocs_basic.py
  packages/smo-simulator/src/smo_simulator/models/eps_basic.py
  packages/smo-simulator/src/smo_simulator/models/obdh_basic.py
  packages/smo-simulator/src/smo_simulator/models/payload_basic.py
  packages/smo-simulator/src/smo_simulator/models/tcs_basic.py
  packages/smo-simulator/src/smo_simulator/models/ttc_basic.py
  packages/smo-mcs/src/smo_mcs/server.py
  packages/smo-mcs/src/smo_mcs/procedure_runner.py
  packages/smo-planner/src/smo_planner/server.py
  configs/eosat1/telemetry/hk_structures.yaml
  configs/eosat1/scenarios/aocs_wheel_failure.yaml
  DEFECTS.md
  REMEDIATION_PLAN.md
  scripts/upload_and_file_defects.sh
  scripts/sync_remediation.sh
  tests/test_remediation_phase01.py
  .gitignore
)

# New defect bodies 09..33 (08 intentionally has no file).
shopt -s nullglob
FILES+=( defects/09-*.md defects/1[0-9]-*.md defects/2[0-9]-*.md defects/3[0-3]-*.md )

echo "==> Staging ${#FILES[@]} path patterns ..."
git add -- "${FILES[@]}"

echo "==> Staged files:"
git diff --cached --name-only

if git diff --cached --quiet; then
    echo "==> Nothing staged (already committed?). Aborting commit."
else
    git commit -m "Remediate defects 09-33: simulator fixes + MCS/planner, audit, plan, tests"
fi

if [[ $DO_PUSH -eq 1 ]]; then
    echo "==> Pushing to origin/main ..."
    git push origin main
else
    echo "==> --no-push: skipping push."
fi

if [[ $DO_ISSUES -eq 1 ]]; then
    echo "==> Filing defect issues via gh ..."
    echo "    (NOTE: files issues 01-33; trim ISSUES[] in upload_and_file_defects.sh"
    echo "     if 01-07 were already filed, to avoid duplicates.)"
    ./scripts/upload_and_file_defects.sh
else
    echo ""
    echo "To file the GitHub defect issues (needs gh authenticated):"
    echo "    ./scripts/upload_and_file_defects.sh --dry-run   # preview"
    echo "    ./scripts/upload_and_file_defects.sh             # file for real"
fi

echo "==> Done."
