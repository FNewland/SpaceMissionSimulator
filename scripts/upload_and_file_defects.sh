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
create_label "area:eps"          "c5def5" "EPS power subsystem model"
create_label "area:ttc"          "c5def5" "TT&C subsystem model"
create_label "area:prop"         "c5def5" "Propulsion subsystem model"
create_label "area:fdir"         "d93f0b" "FDIR / S12 / S19 autonomy"
create_label "area:pus"          "006b75" "PUS-C services and command dispatch"
create_label "area:config"       "bfdadc" "configs/ content and schemas"
create_label "area:procedures"   "bfdadc" "Flight procedure library"
create_label "area:breakpoints"  "0052cc" "Breakpoint save/load and state round-trip"
create_label "area:determinism"  "0052cc" "Reproducibility, seeding and time acceleration"
create_label "area:deploy"       "d4c5f9" "Deployment and launch scripts"
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
    "33|severity:minor|Subsystem computed-but-unobservable telemetry (consolidated cleanup register|area:simulator,area:hk,type:dead-code"
    "34|severity:major|Failure-script scenarios never stop at end of duration|area:simulator,area:scenario"
    "35|severity:major|Breakpoint save/load does not restore orbital position or eclipse state|area:simulator,area:breakpoints"
    "36|severity:major|Eclipse / sunlight state never displayed — MCS reads keys sim never produces|area:mcs,area:simulator"
    "37|severity:critical|S19 rules mis-map S8 func_ids — autonomous OBC application boot at startup|area:simulator,area:fdir"
    "38|severity:major|S8 function-management commands 100-107 dropped by dispatcher|area:simulator,area:pus"
    "39|severity:minor|S8 func 49 TCS_GET_THERMAL_MAP acknowledges but returns no telemetry|area:simulator,area:tcs"
    "40|severity:major|tc_catalog.yaml on/off field names parse as booleans and abort the catalog load|area:mcs,area:config"
    "41|severity:critical|Contact gating uses first ground station only — all Troll passes dead air|area:simulator,area:orbit"
    "42|severity:major|Ground-station geometry duplicated across orbit.yaml and planning config|area:config"
    "43|severity:major|TM frame flush leaves stale packet offsets and wrong FHP — silent frame loss|area:rfsim,area:ccsds"
    "44|severity:major|Planner upload to MCS discards the scheduled activity start time|area:planner,area:mcs"
    "45|severity:critical|Planner activity func_ids misroute — imaging hits EPS no-ops|area:planner,area:pus"
    "46|severity:critical|MCS S11 builder embeds bare svc+subtype — time-tagged TCs die malformed|area:mcs,area:pus"
    "47|severity:major|MCS contact display schema mismatch — real passes render only when planner down|area:mcs,area:planner"
    "48|severity:major|On-board scripting absent — no PUS S18 / OBCP capability|area:simulator,area:pus"
    "49|severity:major|SoC-staged load shedding dead — engine reads a non-existent soc attribute|area:simulator,area:eps"
    "50|severity:major|Power budget does not close in standard operations|area:simulator,area:eps"
    "51|severity:minor|Simulation/scenario parameters hard-coded instead of configuration|area:simulator,area:config"
    "52|severity:major|Scenario loader silently ignores unknown actions and failures|area:simulator,area:scenario"
    "53|severity:major|RF spectrum analyser and eye diagram producers have zero call sites|area:rfsim,area:ui"
    "54|severity:major|OQPSK mislabelled - no half-symbol offset; four modulations TX-only|area:rfsim,area:dsp"
    "55|severity:major|Radio channel config dead; DSP pipeline symbol rate pinned|area:rfsim,area:config"
    "56|severity:major|SpaceLinkChannel impairments instantiated nowhere|area:rfsim"
    "57|severity:critical|Planner UI shows hard-coded stations and sample activities|area:planner,area:ui"
    "58|severity:major|Planner UI runs on wall clock while backend resolves sim time|area:planner,area:ui"
    "59|severity:major|Planner caches ignore breakpoint sim-time jumps|area:planner"
    "60|severity:major|Data budget never renders and uses wrong constants|area:planner"
    "61|severity:major|Imaging opportunities aim at region centre; constraints unused|area:planner"
    "62|severity:minor|ADCS failure-scenario viewer only in fork with stale physics|area:tools"
    "63|severity:major|MCS log windows reset scroll and truncate rendering|area:mcs,area:ui"
    "64|severity:minor|TM packet rows carry no SID/RID columns|area:mcs,area:ui"
    "65|severity:critical|Live plots fabricate a flat line after LOS|area:mcs,area:ui"
    "66|severity:major|Delayed-TM viewer charts dead; CUC always zero|area:tools,area:mcs"
    "67|severity:minor|Command builder shows no expected data-byte count|area:mcs,area:ui"
    "68|severity:critical|HK SID table fiction and little-endian SID packing|area:mcs,area:hk"
    "69|severity:critical|S12 loses definitions; no check-transition telemetry|area:simulator,area:fdir"
    "70|severity:critical|S19 report broken; rules dual-triggered by ID namespace clash|area:simulator,area:fdir"
    "71|severity:major|S6 memory map hard-coded; patches discarded|area:simulator,area:pus"
    "72|severity:major|S11 schedule report discards command identity|area:simulator,area:pus"
    "73|severity:minor|S15 store status report discards status fields|area:simulator,area:pus"
    "74|severity:major|High-rate telemetry not commandable or rate-paced|area:simulator,area:ttc"
    "75|severity:major|All HK SIDs enabled at boot contrary to the operations manual|area:simulator,area:hk"
    "76|severity:major|No FDIR window in the MCS|area:mcs,area:fdir"
    "77|severity:major|Time-tagged builder cut down; onboard schedule never displayed|area:mcs,area:pus"
    "78|severity:minor|MCS memory regions hard-coded in JS|area:mcs"
    "79|severity:major|MCS station list hard-coded; no breakpoint-jump detection|area:mcs"
    "80|severity:minor|tc_catalog metadata gaps|area:config,area:mcs"
    "82|severity:major|Image size makes any plan infeasible at S-band rates|area:config,area:planner"
    "83|severity:major|Four divergent procedure representations; builder output not runnable|area:mcs,area:procedures"
    "84|severity:critical|TTC link budget subtracts ground-station G/T instead of adding it|area:simulator,area:ttc"
    "85|severity:major|Two-tier 1 kbps / 64 kbps data-rate plan unusable for routine operations|area:simulator,area:ttc,area:config"
    "86|severity:major|Ground segment assumes a single downlink rate|area:planner,area:mcs,area:docs"
    "87|severity:major|Command rejection reason never reaches the ground|area:simulator,area:pus"
    "88|severity:major|Error codes invisible in operator displays|area:mcs,area:ui"
    "89|severity:major|Quick commands warn zero data bytes expected|area:mcs,area:ui"
    "90|severity:major|Delayed telemetry dumps unreachable from a networked client|area:mcs,area:deploy"
    "91|severity:major|No RNG seeding anywhere in packages/ — no run was reproducible|area:simulator,area:determinism"
    "92|severity:critical|Time acceleration changed the physics; process noise scaled in dt not sqrt(dt)|area:simulator,area:determinism"
    "93|severity:critical|Breakpoint save/load covered ~12 of 93 engine attributes|area:simulator,area:breakpoints"
    "94|severity:major|set_state aliases and consumes the stored breakpoint snapshot|area:simulator,area:breakpoints"
    "95|severity:major|Solar generation constant was BOL-only (389.5 vs 267.9 W/m2)|area:simulator,area:eps"
    "96|severity:major|Solar array drive is a dead lever; wings sit on the swept axis|area:simulator,area:eps"
    "97|severity:major|AOCS sun sweep hard-coded to 5700 s against a 5669.3 s orbit|area:simulator,area:aocs,area:eps"
    "98|severity:major|Battery terminal-voltage sign inverted; no coulombic efficiency|area:simulator,area:eps"
    "99|severity:critical|Protection ladder inverted (FDIR rule inside the nominal band); restore hysteresis absent|area:simulator,area:eps,area:fdir"
    "100|severity:major|Solar array degraded event fires on the first sunlit tick of every run|area:simulator,area:eps"
    "101|severity:critical|FPA setpoint used as an environment offset; two unlinked FPA integrators|area:simulator,area:tcs"
    "102|severity:major|Battery heater thermostat unreachable; heater command reverted silently|area:simulator,area:tcs"
    "103|severity:major|EPS bills line-switch position, not actuator state|area:simulator,area:eps"
    "104|severity:major|Panel solar heat gain ~80x too large; beta dependence inverted|area:simulator,area:tcs"
    "105|severity:critical|S12 monitor bands never validated — one fires at t=0, one is dead|area:simulator,area:fdir,area:config"
    "106|severity:major|Parameter-id collisions, crossed ids and undeclared writes|area:config,area:hk,area:operability"
    "107|severity:major|sat.mode and two TTC parameters never written — read SAFE / all-broken|area:simulator,area:hk,area:operability"
    "108|severity:critical|No attitude dynamics at all — no inertia tensor, no estimator, no INERTIAL mode|area:simulator,area:aocs"
    "109|severity:major|ST1 zenith boresight inverted against the manual|area:simulator,area:aocs"
    "110|severity:critical|Wheels and magnetorquers disabled at separation and never re-enabled|area:simulator,area:aocs"
    "111|severity:major|S19 rules dispatch with no action_data — cannot name a wheel or a tracker|area:simulator,area:fdir"
    "112|severity:major|FDIR and S12 have no persistence concept|area:simulator,area:fdir"
    "113|severity:major|TLE text quantisation breaks a manoeuvred-orbit breakpoint|area:common,area:breakpoints,area:prop"
    "114|severity:critical|PAYLOAD_CAPTURE argument divergence — every planned image at 0N 0E|area:simulator,area:planner,area:pus"
    "115|severity:major|TTC_SET_TX_POWER reads float watts — three FDIR procedures ignored|area:simulator,area:ttc,area:pus"
    "116|severity:major|19 procedure files carry a wholesale stale func_id scheme|area:procedures,area:config"
    "117|severity:minor|FUNC_ID_SUBSYSTEM stale — 20 commands exported as Spacecraft|area:tools,area:config"
    "118|severity:major|Systemic S20 subtype inversion — 79 annotations across 10 files|area:procedures,area:pus"
    "119|severity:major|Commands clamp out-of-range arguments instead of rejecting them|area:simulator,area:pus"
    "120|severity:critical|mcs/limits.yaml had zero consumers — the ground did no limit checking|area:mcs,area:operability,type:dead-code"
    "121|severity:major|TMArchive write path unused — empty command history, unbounded growth|area:mcs,type:dead-code"
    "122|severity:major|Contact status serialises Infinity; completed passes fill the panel|area:mcs,area:ui"
    "123|severity:major|MCS displays fabricate nominal values; subsystem health permanently GREEN|area:mcs,area:ui"
    "124|severity:critical|FDIR never ran — detect_fault and start_procedure had zero call sites|area:simulator,area:fdir,type:dead-code"
    "125|severity:major|dispatch() error side-channel not re-entrant — nested commands clobber|area:simulator,area:pus"
    "126|severity:major|All 17 S1.4 failure sites pass request_id = 0|area:simulator,area:pus,area:mcs"
    "127|severity:critical|Instructor snapshot reads live state off-thread and by reference|area:simulator,area:operability"
    "128|severity:major|Config schemas not strict — a typo'd key was silently dropped|area:common,area:config"
    "129|severity:major|Wing-deploy events reuse CHARGE_COMPLETE / PDU_OVERCURRENT ids|area:simulator,area:eps"
    "130|severity:major|Launch scripts: no readiness probes, false bind claim, network pip installs|area:deploy,area:tools"
    "131|severity:minor|Procedure audit cannot resolve event citations — 9 false findings|area:tools,area:procedures"
    "132|severity:major|Commanded FPA setpoint lost on breakpoint load|area:simulator,area:tcs,area:breakpoints"
    "133|severity:major|FPA thermal envelope disagreed across five files|area:config,area:tcs,area:planner"
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
