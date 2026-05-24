#!/usr/bin/env bash
#
# upload_and_file_defects.sh
#
# One-shot script to:
#   1. Initialise a git repo in the current directory (if not already one)
#   2. Commit the current working tree
#   3. Create a PUBLIC GitHub repo named SpaceMissionSoftware under the
#      authenticated gh user (or --org <org>)
#   4. Push main to origin
#   5. File seven defect issues from defects/*.md
#
# Requirements:
#   - gh CLI installed and authenticated (`gh auth status` should succeed)
#   - git configured with user.name and user.email
#   - Run from the SpaceMissionSimulation project root
#
# Usage:
#   ./scripts/upload_and_file_defects.sh                 # use gh user, public
#   ./scripts/upload_and_file_defects.sh --org my-org    # create under an org
#   ./scripts/upload_and_file_defects.sh --dry-run       # print commands only
#
set -euo pipefail

REPO_NAME="SpaceMissionSoftware"
VISIBILITY="--public"
ORG=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --org)
            ORG="$2"
            shift 2
            ;;
        --private)
            VISIBILITY="--private"
            shift
            ;;
        --public)
            VISIBILITY="--public"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "DRY-RUN> $*"
    else
        echo "+ $*"
        eval "$@"
    fi
}

# ---------------------------------------------------------------- preflight
command -v gh  >/dev/null || { echo "gh CLI not found"; exit 1; }
command -v git >/dev/null || { echo "git not found"; exit 1; }

if [[ $DRY_RUN -eq 0 ]]; then
    gh auth status >/dev/null 2>&1 || {
        echo "gh is not authenticated. Run: gh auth login"
        exit 1
    }
fi

# Derive the owner string used in `gh issue create`
if [[ -n "$ORG" ]]; then
    OWNER="$ORG"
else
    OWNER="$(gh api user --jq .login 2>/dev/null || echo YOUR_GH_USERNAME)"
fi
FULL_REPO="${OWNER}/${REPO_NAME}"

echo "Target repo: ${FULL_REPO} (${VISIBILITY#--})"

# ---------------------------------------------------------------- git init
if [[ ! -d .git ]]; then
    run "git init -b main"
else
    echo "Existing git repo detected — skipping init"
fi

# ---------------------------------------------------------------- .gitignore
if [[ ! -f .gitignore ]]; then
    run "cat > .gitignore <<'EOF'
__pycache__/
*.pyc
*.pyo
.venv/
venv/
.env
.DS_Store
.pytest_cache/
.coverage
htmlcov/
node_modules/
dist/
build/
*.egg-info/
EOF"
fi

# ---------------------------------------------------------------- initial commit
run "git add -A"
if [[ $DRY_RUN -eq 1 ]] || ! git diff --cached --quiet; then
    run "git -c commit.gpgsign=false commit -m 'Initial upload: EO-Sat-1 mission simulator + defect register'"
else
    echo "No staged changes — skipping commit"
fi

# ---------------------------------------------------------------- create repo + push
if [[ $DRY_RUN -eq 1 ]]; then
    run "gh repo create ${FULL_REPO} ${VISIBILITY} --source=. --remote=origin --push"
else
    if gh repo view "${FULL_REPO}" >/dev/null 2>&1; then
        echo "Repo ${FULL_REPO} already exists — adding remote + pushing"
        if ! git remote | grep -q '^origin$'; then
            git remote add origin "https://github.com/${FULL_REPO}.git"
        fi
        git push -u origin main
    else
        gh repo create "${FULL_REPO}" ${VISIBILITY} --source=. --remote=origin --push
    fi
fi

# ---------------------------------------------------------------- labels
# Create labels idempotently (ignore failures if they already exist).
create_label() {
    local name="$1" color="$2" desc="$3"
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "DRY-RUN> gh label create '${name}' --color '${color}' --description '${desc}' --force"
    else
        gh label create "${name}" --color "${color}" --description "${desc}" \
            --repo "${FULL_REPO}" --force >/dev/null 2>&1 || true
    fi
}
create_label "severity:critical" "b60205" "Mission cannot operate safely without this"
create_label "severity:major"    "d93f0b" "Significant operational impact"
create_label "severity:minor"    "fbca04" "Cosmetic or convenience"
create_label "status:fixed"      "0e8a16" "Already fixed in working tree"
create_label "area:simulator"    "1d76db" "smo-simulator package"
create_label "area:mcs"          "5319e7" "Mission Control System frontend/backend"
create_label "area:obdh"         "c5def5" "OBDH subsystem model"
create_label "area:aocs"         "c5def5" "AOCS subsystem model"
create_label "area:s15"          "c5def5" "PUS-C Service 15 onboard storage"
create_label "area:hk"           "c5def5" "Housekeeping telemetry"
create_label "area:operability"  "fef2c0" "Ground operability / observability"
create_label "area:planner"      "0052cc" "smo-planner package"
create_label "area:common"       "006b75" "smo-common shared library"
create_label "area:rfsim"        "1d76db" "smo-rfsim RF bridge"
create_label "area:gateway"      "bfdadc" "smo-gateway package"
create_label "area:tcs"          "c5def5" "TCS thermal subsystem model"
create_label "area:tools"        "d4c5f9" "Standalone tools/ services"
create_label "area:ui"           "fef2c0" "Frontend / UI wiring"
create_label "type:dead-code"    "e99695" "Implemented but not connected / no callers"

# ---------------------------------------------------------------- file issues
# Format: idx | severity | title | label-csv
ISSUES=(
    "01|severity:major|OBDH \"Buffer Fill – HK TM\" parameter exceeds 100%% (observed 353%%)|area:simulator,area:obdh,area:mcs"
    "02|severity:major|HK_Store sized for <1 orbit of housekeeping (5000 → 18000)|area:simulator,area:s15,area:hk,status:fixed"
    "03|severity:major|TM packets routed to S15 stores during bootloader|area:simulator,area:s15,status:fixed"
    "04|severity:major|AOCS mode defaults to NOMINAL(4) at construction — DETUMBLE at start|area:simulator,area:aocs"
    "05|severity:critical|sw_image (0x0311) and phase (0x0129) not in any HK SID — unobservable|area:simulator,area:hk,area:operability"
    "06|severity:critical|MCS has no generic parameter-watch widget / no S20 client|area:mcs,area:operability"
    "07|severity:minor|Fill %% > 100 impossible for circular store — UI contract bug|area:mcs,area:s15"
    "09|severity:critical|Scenario subsystem non-functional — ScenarioEngine never instantiated|area:simulator,type:dead-code"
    "10|severity:critical|Breakpoint save/load not wired to UI — SAVE no-ops, no LOAD control|area:simulator,area:ui,type:dead-code"
    "11|severity:major|Heater control broken — OBC heater command no-ops; no heater UI affordance|area:simulator,area:tcs,area:ui"
    "12|severity:major|CLEAR ALL FAILURES button does nothing (HTTP 403 + unhandled WS)|area:simulator,area:ui"
    "13|severity:major|MCS advanced displays unreachable (displays.js never loaded) + 500 crash|area:mcs,area:ui,type:dead-code"
    "14|severity:major|MCS Procedure Builder produces unrunnable procedures; steps skipped open|area:mcs,area:ui"
    "15|severity:major|Planner full validation unreachable — only weak name-conflict check wired|area:planner,type:dead-code"
    "16|severity:major|Planner backend endpoints (constraints, pass-activity, PUT, targets) have no UI|area:planner,area:ui,type:dead-code"
    "17|severity:major|smo-common orphaned PUS service parser — duplicated/diverged in MCS|area:common,area:mcs,type:dead-code"
    "18|severity:major|RF via GNU Radio never invoked — entire gnuradio/ package is dead|area:rfsim,type:dead-code"
    "19|severity:major|Radio dashboard panels (link budget/channel/spectrum/eye) show placeholders|area:rfsim,area:ui,type:dead-code"
    "20|severity:minor|MCS dead/unwired code & data-shape bugs (consolidated cleanup register)|area:mcs,type:dead-code"
    "21|severity:minor|Planner + smo-common dead/orphaned code (consolidated cleanup register)|area:planner,area:common,type:dead-code"
    "22|severity:minor|RFsim + gateway + tools dead/unwired code (consolidated cleanup register)|area:rfsim,area:gateway,area:tools,type:dead-code"
    "23|severity:major|Subsystem-generated events never reach operator (engine wiring + undrained queue)|area:simulator,type:dead-code"
    "24|severity:major|AOCS reaction-wheel thermal signature broken (temps not in HK; no rise on seizure)|area:simulator,area:aocs,area:hk"
    "25|severity:major|AOCS aocs_wheel_failure scenario injects unhandled wheel_failure (no-op)|area:simulator,area:aocs"
    "26|severity:major|AOCS MAG_SELECT cannot select the redundant magnetometer (Mag B)|area:simulator,area:aocs"
    "27|severity:major|FDIR/load-shed callbacks invoke commands models don't handle (EPS safing, TTC power)|area:simulator"
    "28|severity:major|TTC uplink_loss failure does not suppress the downlink|area:simulator"
    "29|severity:major|OBDH bus-failure isolation is cosmetic (reachability never consulted)|area:simulator,area:obdh"
    "30|severity:major|OBDH watchdog inverted; injected watchdog reset cannot fire in nominal mode|area:simulator,area:obdh"
    "31|severity:major|TCS advanced thermal commands inert (decontamination, duty-limit, setpoints)|area:simulator,area:tcs"
    "32|severity:minor|Subsystem dead/inert commands & state (consolidated cleanup register)|area:simulator,type:dead-code"
    "33|severity:minor|Subsystem computed-but-unobservable telemetry (consolidated cleanup register)|area:simulator,area:hk,type:dead-code"
)

for entry in "${ISSUES[@]}"; do
    IFS='|' read -r idx severity title labels <<<"$entry"
    body_file=$(ls defects/${idx}-*.md 2>/dev/null | head -1)
    if [[ -z "$body_file" ]]; then
        echo "WARNING: no body file for defect $idx — skipping"
        continue
    fi
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "DRY-RUN> gh issue create --repo ${FULL_REPO} --title \"${title}\" --body-file ${body_file} --label ${severity},${labels}"
    else
        gh issue create \
            --repo "${FULL_REPO}" \
            --title "${title}" \
            --body-file "${body_file}" \
            --label "${severity},${labels}" \
            || echo "WARNING: failed to create issue for defect ${idx}"
    fi
done

echo
echo "Done. Repo: https://github.com/${FULL_REPO}"
echo "Issues: https://github.com/${FULL_REPO}/issues"
