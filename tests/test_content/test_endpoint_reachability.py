"""Every registered HTTP route must have a consumer (brief 08 WP-8, gap 7).

Enumerate the routes each server registers and assert each one is referenced
by a UI asset, another package, a tool, a test, or is listed in
`INTENTIONALLY_API_ONLY` with a reason.  This is the test that would have
caught #13, #16, O-12, O-13 and O-14 the day they were introduced: five
`/api/displays/*` endpoints and four `/api/archive/*` endpoints shipped with
working implementations and no way to reach them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"
TOOLS = REPO / "tools"
TESTS = REPO / "tests"

SERVERS = {
    "mcs": PACKAGES / "smo-mcs" / "src" / "smo_mcs" / "server.py",
    "planner": PACKAGES / "smo-planner" / "src" / "smo_planner" / "server.py",
    "simulator": (PACKAGES / "smo-simulator" / "src" / "smo_simulator"
                  / "instructor" / "app.py"),
}

_ROUTE_RE = re.compile(
    r"""router\.add_(?:get|post|put|delete|route)\(\s*["']([^"']+)["']""")


#: Routes with no UI consumer that are deliberately API-only.
#: Key: "<server> <path>". Value: the reason.
INTENTIONALLY_API_ONLY: dict[str, str] = {
    # `mcs /catalog` was here. Phase G deleted the route: its only consumers
    # were the `files/*.html` pages (deleted with the `files/` tree) and the
    # April `docs/gap_analysis/` write-ups that describe them. Nothing current
    # cites it, so a shim "for printed handouts" was a guess, not a reason.
    "mcs /api/sim-state":
        "same payload as /api/state under the pre-rename path; kept for "
        "student scripts and the acceptance suite",
    "planner /wide":
        "documented classroom URL (deploy/INSTALLATION_MANUAL.html, "
        "docs/DEPLOYMENT.md); redirects to /?layout=wide",
    "mcs /api/ground-time":
        "instructor time-offset control; reached from scripted exercise "
        "setup and the acceptance suite, not from the student console",
    "simulator /api/breakpoint/save":
        "REST entry point for scripted exercise setup; the instructor "
        "console uses /api/command {type: save_breakpoint}, which lands on "
        "the same tick-thread handler",
    "simulator /api/breakpoint/load":
        "REST entry point for scripted exercise setup; the instructor "
        "console uses /api/command {type: load_breakpoint}",
    "simulator /api/scenario/debrief":
        "scenario debrief export consumed by the instructor's own tooling "
        "and by the acceptance suite, not by a page in this repo",
    # The four /api/archive/* endpoints and the /api/displays/* set were
    # allowlisted here as "implemented, no UI yet". Phase G lane 1 built the
    # TRENDS tab (WP-4) and surfaced system-overview / contact-schedule /
    # power-budget / alarm-trends in the console, and deleted
    # /api/displays/procedure-status together with its backing class — it
    # carried no data that /api/procedure/status and the FDIR window's
    # recovery panel do not already show. No entry is needed for any of them
    # any more; the debt is closed rather than described.
}


def _routes(path: Path) -> set[str]:
    return set(_ROUTE_RE.findall(path.read_text()))


def _corpus() -> str:
    """Everything that could reference a route: UI assets, code, tools, tests."""
    chunks = []
    for root, patterns in ((PACKAGES, ("**/*.html", "**/*.js", "**/*.py")),
                           (TOOLS, ("**/*.py", "**/*.html")),
                           (TESTS, ("**/*.py",))):
        if not root.exists():
            continue
        for pattern in patterns:
            for f in root.glob(pattern):
                if "__pycache__" in str(f):
                    continue
                try:
                    chunks.append(f.read_text(errors="ignore"))
                except OSError:
                    continue
    return "\n".join(chunks)


CORPUS = _corpus()


def _referenced(route: str, server_src: Path) -> bool:
    """Is *route* named anywhere other than its own registration line?

    Templated segments (`{alarm_id}`, `{path:.+}`) are never written literally
    by a caller — a browser builds `/api/alarms/${id}/ack` — so match on the
    literal prefix up to the first placeholder.
    """
    literal = route.split("{", 1)[0]
    if not literal or literal == "/":
        return True
    hits = CORPUS.count(literal)
    # Subtract the occurrences inside the server module itself (the
    # registration line and the handler docstring).
    own = server_src.read_text().count(literal)
    return hits > own


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_every_route_has_a_consumer_or_a_reason(server):
    src = SERVERS[server]
    if not src.exists():
        pytest.skip(f"{src} not present")
    orphans = []
    for route in sorted(_routes(src)):
        key = f"{server} {route}"
        if key in INTENTIONALLY_API_ONLY:
            continue
        if not _referenced(route, src):
            orphans.append(route)
    assert not orphans, (
        f"{server}: routes registered with no consumer and no "
        f"INTENTIONALLY_API_ONLY reason: {orphans}")


def test_allowlist_entries_are_still_registered():
    stale = []
    for key in INTENTIONALLY_API_ONLY:
        server, route = key.split(" ", 1)
        src = SERVERS.get(server)
        if src is None or not src.exists():
            stale.append(key)
            continue
        if route not in _routes(src):
            stale.append(key)
    assert not stale, f"stale INTENTIONALLY_API_ONLY entries: {stale}"


def test_allowlist_entries_carry_a_reason():
    for key, reason in INTENTIONALLY_API_ONLY.items():
        assert reason and len(reason) > 20, f"{key} has no real reason"


def test_no_headless_display_or_archive_endpoints():
    """The set brief 08 called out as headless (#13 / O-12 / O-13) is empty.

    Nine endpoints — the four `/api/archive/*` and the five `/api/displays/*`
    — shipped with working implementations, live backing classes and no way
    to reach them; #13 recorded that as "Partial" for three months. Phase G
    lane 1 built the TRENDS tab and the overview/EPS panels that call them and
    deleted the one that carried no unique data. The bound is now zero so the
    debt cannot quietly reappear.
    """
    src = SERVERS["mcs"]
    headless = sorted(
        route for route in _routes(src)
        if (route.startswith("/api/displays") or route.startswith("/api/archive"))
        and not _referenced(route, src))
    assert headless == [], f"headless endpoints reappeared: {headless}"
