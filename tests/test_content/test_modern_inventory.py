"""API-compat gate for the modern UI (design_2026-08 plan §5, Agent 5).

Four static gates, no server boot:

(a) ROUTE-TABLE SNAPSHOT — the full HTTP route list of each server module
    (and of the shared smo_common route registrars) is pinned below. The
    binding API rule is "additive only": a NEW route is fine (update the
    snapshot), but if any pinned route disappears the test fails — that
    would break one of the two UI generations or a headless client.

(b) MODERN JS -> ROUTE existence — every `/api/...` path referenced by any
    modern JS file must exist in the owning server's route table (with
    `${...}` template tails matched against `{param}` routes).

(c) SCHEMATIC BINDING — every `data-param` in the modern MCS
    schematics.svg must resolve against configs/eosat1/telemetry/
    parameters.yaml (risk R1: schematic drift).

(d) CLASSIC BYTE-FREEZE — the classic pages' sha256, imported from
    test_classic_untouched (one source of truth, re-asserted here so this
    gate is self-contained in a content run).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]

MCS_SERVER = REPO / "packages/smo-mcs/src/smo_mcs/server.py"
PLANNER_SERVER = REPO / "packages/smo-planner/src/smo_planner/server.py"
INSTRUCTOR_APP = (REPO / "packages/smo-simulator/src/smo_simulator"
                  / "instructor/app.py")
DUMP_STORE = REPO / "packages/smo-common/src/smo_common/dump_store.py"
REPORTING_API = REPO / "packages/smo-common/src/smo_common/reporting/api.py"
CONFEDIT_API = REPO / "packages/smo-common/src/smo_common/confedit/api.py"

MCS_MODERN = REPO / "packages/smo-mcs/src/smo_mcs/static/modern"
PLANNER_MODERN = REPO / "packages/smo-planner/src/smo_planner/static/modern"
INSTRUCTOR_MODERN = (REPO / "packages/smo-simulator/src/smo_simulator"
                     / "instructor/static/modern")

_ROUTE_RE = re.compile(
    r"add_(?:get|post|put|delete|patch|head|route)\(\s*"
    r"(?:[A-Za-z_][\w.]*\s*\+\s*)?"          # shared registrars: prefix + "…"
    r"(?:\"([^\"]+)\"|'([^']+)')")


def routes_in(path: Path) -> list[str]:
    """Every route path string registered in a source file, in file order."""
    out = []
    for m in _ROUTE_RE.finditer(path.read_text()):
        out.append(m.group(1) or m.group(2))
    return out


# ── (a) route-table snapshots — full current lists, recorded 2026-08-18 ─────
# A route may be ADDED (append it here); it must never disappear.

MCS_ROUTES = [
    "/", "/classic", "/modern", "/ws",
    "/api/displays", "/api/state", "/api/sim-state", "/api/pus-command",
    "/api/verification-log", "/api/procedures", "/api/procedures/{path:.+}",
    "/api/manual", "/api/manual/{path:.+}", "/api/positions",
    "/api/tc-catalog", "/api/param-catalog", "/api/failure-codes",
    "/api/hk-structures", "/api/fdir", "/api/schedule", "/api/memory-map",
    "/api/ground-stations", "/api/ground-time", "/api/procedure/load",
    "/api/schedule/upload-s11", "/api/procedure/start", "/api/procedure/pause",
    "/api/procedure/resume", "/api/procedure/abort", "/api/procedure/step",
    "/api/procedure/skip", "/api/procedure/status",
    "/api/procedure/override-command", "/api/procedure/activity-types",
    "/api/procedure/index", "/api/procedure/tlm-vocabulary",
    "/api/procedure/save", "/api/procedure/custom", "/api/go-nogo/status",
    "/api/go-nogo/poll", "/api/go-nogo/respond", "/api/handover",
    "/api/handover", "/api/limits", "/api/annotations", "/api/alarms",
    "/api/alarms/{alarm_id}/ack", "/api/client-config", "/api/contacts",
    "/api/tm-dump", "/api/tm-dump-data", "/api/archive/parameters",
    "/api/archive/events", "/api/archive/alarms", "/api/archive/playback",
    "/api/archive/commands", "/api/archive/param-names",
    "/api/displays/contact-schedule", "/api/displays/power-budget",
    "/api/displays/fdir-alarms", "/api/displays/system-overview",
    "/api/displays/alarms/{alarm_id}/ack", "/api/displays/alarm-trends",
    # POSITION_NEEDS additions, 2026-08-18 (additive): G5 link prediction,
    # G6 fault graph, G7 GO/NO-GO matrix, G8 console log (GET+POST).
    "/api/displays/link-prediction", "/api/fault-graph",
    "/api/gonogo-matrix", "/api/console-log", "/api/console-log",
    # Contrast/training rework, 2026-08-19: operational display config
    # (capture gates + health baselines, moved out of the retired
    # annotations.yaml). /api/annotations stays (serves {}, URL stability).
    "/api/display-config",
]

PLANNER_ROUTES = [
    "/", "/classic", "/modern", "/wide",
    "/api/contacts", "/api/schedule", "/api/ground-stations",
    "/api/ground-track", "/api/spacecraft-state", "/api/schedule",
    "/api/schedule", "/api/schedule/templates", "/api/schedule/load-template",
    "/api/config/alignment", "/api/config/planner", "/api/schedule/{id}",
    "/api/schedule/{id}", "/api/schedule/upload", "/api/schedule/validate",
    "/api/activity-types", "/api/time-source", "/api/schedule/pass-activity",
    "/api/budget/power", "/api/budget/data", "/api/imaging/targets",
    "/api/imaging/opportunities", "/api/imaging/schedule",
    "/api/constraints/validate", "/api/constraints/power",
    "/api/constraints/aocs", "/api/constraints/thermal",
    "/api/constraints/data-volume", "/api/constraints/conflicts",
]

INSTRUCTOR_ROUTES = [
    "/", "/classic", "/modern", "/ws",
    "/api/state", "/api/instructor/snapshot", "/api/command",
    "/api/scenarios", "/api/scenario/debrief", "/api/failures",
    "/api/failure-catalog", "/api/breakpoints", "/api/breakpoint/save",
    "/api/breakpoint/load",
]

# Shared one-implementation-many-hosts registrars (mounted under /dumps,
# /reports and /config on the servers above). Sub-app-relative paths.
DUMP_STORE_ROUTES = ["/", "/file", "/summary", "/{name}", "/{name}/summary"]
REPORTING_ROUTES = ["/", "/templates", "/templates/{id}", "/validate",
                    "/generate", "/file"]
CONFEDIT_ROUTES = ["/files", "/file", "/schema", "/validate", "/diff",
                   "/history", "/revisions/{rev}", "/diff/{a}..{b}",
                   "/crosschecks", "/baselines", "/file", "/revert",
                   "/baselines", "/baselines/{name}/apply"]


@pytest.mark.parametrize("src,snapshot", [
    (MCS_SERVER, MCS_ROUTES),
    (PLANNER_SERVER, PLANNER_ROUTES),
    (INSTRUCTOR_APP, INSTRUCTOR_ROUTES),
    (DUMP_STORE, DUMP_STORE_ROUTES),
    (REPORTING_API, REPORTING_ROUTES),
    (CONFEDIT_API, CONFEDIT_ROUTES),
], ids=lambda p: p.name if isinstance(p, Path) else "")
def test_no_pre_existing_route_disappears(src, snapshot):
    current = routes_in(src)
    # multiset containment: every pinned route (with multiplicity, e.g. the
    # planner registers /api/schedule for GET+POST+DELETE) must survive.
    missing = []
    pool = list(current)
    for r in snapshot:
        if r in pool:
            pool.remove(r)
        else:
            missing.append(r)
    assert not missing, (
        f"{src.relative_to(REPO)}: pre-existing routes DISAPPEARED "
        f"(additive-only API rule, plan §1.2): {missing}")


def test_route_snapshot_is_current():
    """New routes must be appended to the snapshot (kept deliberately)."""
    for src, snapshot in [(MCS_SERVER, MCS_ROUTES),
                          (PLANNER_SERVER, PLANNER_ROUTES),
                          (INSTRUCTOR_APP, INSTRUCTOR_ROUTES)]:
        current = routes_in(src)
        extra = [r for r in current if current.count(r) > snapshot.count(r)]
        assert not extra, (
            f"{src.relative_to(REPO)}: routes exist that are not in the "
            f"snapshot — append them to keep the gate honest: {sorted(set(extra))}")


# ── (b) every /api path referenced by modern JS exists on its server ────────

_JS_API_RE = re.compile(r"[\"'`](/api/[A-Za-z0-9/_.\-]*(?:\$\{)?)")


def js_api_refs(js: Path) -> set[str]:
    out = set()
    for m in _JS_API_RE.finditer(js.read_text()):
        p = m.group(1)
        if p.endswith("${"):
            p = p[:-2] + "*"          # template tail  -> wildcard
        elif p.endswith("/"):
            p = p + "*"               # concatenated tail -> wildcard
        out.add(p)
    return out


def route_matches(ref: str, routes: list[str]) -> bool:
    for r in routes:
        base = re.sub(r"\{[^}]*\}", "*", r)
        if ref.endswith("*"):
            stem = ref[:-1]
            if base.startswith(stem) or r.startswith(stem):
                return True
        elif ref == r or ref == base.rstrip("*").rstrip("/"):
            return True
        elif base.endswith("*") and ref.startswith(base[:-1]):
            return True
    return False


@pytest.mark.parametrize("js_dir,routes,foreign", [
    (MCS_MODERN, MCS_ROUTES,
     # documented cross-origin planner call through client-config planner_url
     {"/api/time-source"}),
    (PLANNER_MODERN, PLANNER_ROUTES, set()),
    (INSTRUCTOR_MODERN, INSTRUCTOR_ROUTES, set()),
], ids=["mcs", "planner", "instructor"])
def test_every_modern_js_api_path_has_a_route(js_dir, routes, foreign):
    unmatched = []
    for js in sorted(js_dir.glob("*.js")):
        for ref in sorted(js_api_refs(js)):
            if ref in foreign:
                continue
            if not route_matches(ref, routes):
                unmatched.append(f"{js.name}: {ref}")
    assert not unmatched, (
        f"modern JS references API paths with no route on the owning "
        f"server: {unmatched}")


# ── (c) schematic data-param bindings resolve against the dictionary ────────

def test_every_schematic_data_param_resolves():
    svg = (MCS_MODERN / "schematics.svg").read_text()
    bound = set(re.findall(r'data-param="([^"]+)"', svg))
    assert bound, "schematics.svg carries no data-param bindings?"
    params = yaml.safe_load(
        (REPO / "configs/eosat1/telemetry/parameters.yaml").read_text())
    rows = params["parameters"] if isinstance(params, dict) else params
    known = set()
    for row in rows:
        pid = row.get("id") if isinstance(row, dict) else None
        if pid is None:
            continue
        known.add(int(pid, 16) if isinstance(pid, str) else int(pid))
    unknown = []
    for b in sorted(bound):
        try:
            v = int(b, 16)
        except ValueError:
            continue                      # doc placeholder like 0xHHHH
        if v not in known:
            unknown.append(b)
    assert not unknown, (
        f"schematics.svg binds parameters absent from telemetry/"
        f"parameters.yaml (risk R1): {unknown}")


# ── (d) classic pages byte-frozen (one source of truth, re-asserted) ────────

def test_classic_pages_unchanged():
    from tests.test_common.test_classic_untouched import CLASSIC_PAGES
    for rel, expected in sorted(CLASSIC_PAGES.items()):
        actual = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        assert actual == expected, (
            f"classic page {rel} changed (sha256 {actual[:12]}…) — classic "
            f"files are byte-frozen (plan §1.1)")
