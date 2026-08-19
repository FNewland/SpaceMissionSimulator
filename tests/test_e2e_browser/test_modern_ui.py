"""E2E browser tests for the modern UI (design_2026-08 plan §5, Agent 5).

Boots the REAL stack (smo-simulator, smo-mcs, smo-planner against
configs/eosat1, ``--ui modern``) and drives the modern pages with Playwright
headless Chromium. Excluded from the default run: marked ``e2e`` and skipped
unless ``SMO_E2E=1`` (see README.md in this directory).

Covers, per the plan:
- the SMO_UI switch on all three servers (``/`` modern, ``/classic`` classic,
  ``?ui=`` override both ways, planner ``/wide`` both branches,
  instructor ``?ui=`` × ``?sub=`` composition);
- MCS: WS connect, SA header population, schematic-as-navigation,
  alarm drawer on ``a``, TC(17,1) round-trip into the verification log,
  position-driven rail filtering;
- planner: activity add/edit/delete round-trip; wide page non-interactive
  with all six blocks;
- instructor: typed failure inject + clear, scenario start/stop, debrief.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("SMO_E2E") != "1",
                       reason="live-stack browser E2E — set SMO_E2E=1 to run"),
]

REPO = Path(__file__).resolve().parents[2]
VENV_BIN = Path(sys.executable).parent
SIM = "http://127.0.0.1:8080"
MCS = "http://127.0.0.1:9090"
PLANNER = "http://127.0.0.1:9091"


def _get(url: str, timeout: float = 5.0) -> tuple[int, str, str]:
    """GET url → (status, final_url, body). Follows redirects."""
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")


def _wait_http(url: str, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited rc={proc.returncode} while "
                               f"waiting for {url}")
        try:
            _get(url, timeout=2.0)
            return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(f"{url} not ready in {timeout}s")


@pytest.fixture(scope="session")
def stack():
    """Simulator + MCS + planner, all --ui modern, on the canonical ports."""
    tmp = tempfile.mkdtemp(prefix="smo-e2e-dumps-")
    env = dict(os.environ)
    env.pop("SMO_UI", None)                       # CLI --ui is the authority
    env["SMO_TIME_SOURCE"] = "sim"
    env["SMO_DUMP_DIR"] = tmp
    env["SMO_SIM_HTTP_PORT"] = "8080"
    env["SMO_SIM_STATE_URL"] = f"{SIM}/api/state"
    procs: list[subprocess.Popen] = []

    def launch(*argv: str) -> subprocess.Popen:
        p = subprocess.Popen(argv, cwd=REPO, env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             start_new_session=True)
        procs.append(p)
        return p

    try:
        sim = launch(str(VENV_BIN / "smo-simulator"), "--config",
                     "configs/eosat1/", "--host", "127.0.0.1",
                     "--port", "8080", "--ui", "modern")
        _wait_http(f"{SIM}/api/state", sim)
        mcs = launch(str(VENV_BIN / "smo-mcs"), "--config", "configs/eosat1/",
                     "--port", "9090", "--host", "127.0.0.1",
                     "--connect", "127.0.0.1:8002",
                     "--time-source", "sim", "--ui", "modern")
        _wait_http(f"{MCS}/api/state", mcs)
        planner = launch(str(VENV_BIN / "smo-planner"), "--config",
                         "configs/eosat1/", "--port", "9091",
                         "--host", "127.0.0.1",
                         "--connect-host", "127.0.0.1",
                         "--time-source", "sim", "--ui", "modern")
        _wait_http(f"{PLANNER}/api/schedule", planner)
        # The simulator gates TM downlink on ground-station contact; between
        # passes the MCS legitimately has NO TM. Force contact the way an
        # instructor would (OVERRIDE PASSES) so the TM-dependent browser
        # tests see a live link, then wait for the first decoded frame.
        req = urllib.request.Request(
            f"{SIM}/api/command", method="POST",
            data=json.dumps({"type": "override_passes"}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        t0 = time.time()
        while time.time() - t0 < 60:
            state = json.loads(_get(f"{MCS}/api/state")[2])
            if (state.get("param_count") or 0) > 0:
                break
            time.sleep(1)
        else:
            raise TimeoutError("MCS never decoded TM after override_passes")
        yield {"sim": SIM, "mcs": MCS, "planner": PLANNER}
    finally:
        for p in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser, stack):
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    pg = ctx.new_page()
    yield pg
    ctx.close()


# ── 1. The SMO_UI switch ─────────────────────────────────────────────────────

class TestSwitch:
    def test_mcs_switch(self, stack):
        assert "Modern Console" in _get(f"{MCS}/")[2]
        assert "SMO Mission Control System" in _get(f"{MCS}/classic")[2]
        assert "Modern Console" in _get(f"{MCS}/modern")[2]
        # ?ui= overrides both ways on a modern-default server
        assert "SMO Mission Control System" in _get(f"{MCS}/?ui=classic")[2]
        assert "Modern Console" in _get(f"{MCS}/?ui=modern")[2]

    def test_planner_switch(self, stack):
        modern = _get(f"{PLANNER}/")[2]
        assert "modern" in modern and "planner.js" in modern
        classic = _get(f"{PLANNER}/classic")[2]
        assert "planner.js" not in classic
        assert "planner.js" not in _get(f"{PLANNER}/?ui=classic")[2]
        assert "planner.js" in _get(f"{PLANNER}/?ui=modern")[2]

    def test_planner_wide_modern_serves_wall_page(self, stack):
        body = _get(f"{PLANNER}/wide")[2]
        assert "Wall Display" in body

    def test_planner_wide_classic_redirect_preserved(self, stack):
        status, final, body = _get(f"{PLANNER}/wide?ui=classic")
        assert status == 200
        assert "layout=wide" in final and "ui=classic" in final
        assert "Wall Display" not in body      # classic page, wide layout mode

    def test_instructor_switch_and_sub_composition(self, stack):
        assert "SMO Instructor — EOSAT-1" in _get(f"{SIM}/")[2]
        assert "SMO Instructor Interface" in _get(f"{SIM}/classic")[2]
        # ?ui= composes with ?sub= (plan R10) — both orders, both generations
        assert "SMO Instructor Interface" in _get(f"{SIM}/?ui=classic&sub=eps")[2]
        assert "SMO Instructor — EOSAT-1" in _get(f"{SIM}/?sub=eps")[2]
        assert "SMO Instructor — EOSAT-1" in _get(f"{SIM}/?sub=eps&ui=modern")[2]


# ── 2. MCS in the browser ────────────────────────────────────────────────────

class TestMcsBrowser:
    def _open(self, page):
        page.goto(f"{MCS}/", wait_until="domcontentloaded")
        page.wait_for_selector("#nav-rail button", timeout=15000)
        return page

    def test_ws_connects_and_sa_header_populates(self, page):
        self._open(page)
        page.wait_for_selector("#ws-dot.on", timeout=15000)
        # SA SEE card leaves its placeholder once TM flows
        page.wait_for_function(
            "() => { const t = document.getElementById('sa-see-v').textContent;"
            " return t && t !== '—' && t !== 'NO TM'; }", timeout=30000)
        assert page.inner_text("#sa-well-v").strip() != ""
        assert page.inner_text("#sa-next-v").strip() != ""

    def test_schematic_node_navigates_to_l2(self, page):
        self._open(page)
        page.wait_for_selector("#ovr-schematic svg", timeout=15000)
        assert "hide" in (page.get_attribute("#page-eps", "class") or "")
        page.click("#ovr-node-eps")
        page.wait_for_function(
            "() => !document.getElementById('page-eps').classList.contains('hide')",
            timeout=5000)
        assert "hide" not in (page.get_attribute("#page-eps", "class") or "")

    def test_alarm_drawer_opens_with_a(self, page):
        self._open(page)
        page.keyboard.press("a")
        page.wait_for_selector("#alarm-drawer.open", timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => !document.getElementById('alarm-drawer').classList.contains('open')",
            timeout=5000)

    def test_tc_17_1_round_trip_shows_in_verification_log(self, page):
        self._open(page)
        page.wait_for_selector("#ws-dot.on", timeout=15000)
        page.click("#rail-cmd")
        page.wait_for_selector("#cmd-send", timeout=5000)
        page.select_option("#cmd-service", "17")
        page.fill("#cmd-subtype", "1")
        page.fill("#cmd-data", "")
        page.click("#cmd-send")
        page.wait_for_function(
            "() => document.getElementById('cmd-verif-log')"
            ".textContent.includes('TC(17,1)')", timeout=20000)

    def test_position_switch_filters_rail(self, page):
        self._open(page)
        page.wait_for_selector("#rail-aocs", timeout=10000)
        assert page.get_attribute("#rail-aocs", "hidden") is None
        page.select_option("#pos-select", "eps_tcs")
        page.wait_for_function(
            "() => document.getElementById('rail-aocs').hidden === true",
            timeout=5000)
        # EPS stays visible for the Power & Thermal position
        assert page.get_attribute("#rail-eps", "hidden") is None
        page.select_option("#pos-select", "flight_director")
        page.wait_for_function(
            "() => document.getElementById('rail-aocs').hidden === false",
            timeout=5000)


# ── 3. Planner in the browser ────────────────────────────────────────────────

class TestPlannerBrowser:
    @staticmethod
    def _schedule():
        d = json.loads(_get(f"{PLANNER}/api/schedule")[2])
        return d.get("schedule", d.get("activities", []))

    def test_activity_add_edit_delete_round_trip(self, page):
        dialogs: list[str] = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(f"{PLANNER}/", wait_until="domcontentloaded")
        page.wait_for_selector("#btnAddActivity", timeout=15000)
        n0 = len(self._schedule())

        page.click("#btnAddActivity")
        page.wait_for_selector("#modalOverlay", state="visible", timeout=5000)
        # activity types load async from /api/activity-types
        page.wait_for_function(
            "() => document.querySelectorAll('#actType option').length > 1",
            timeout=10000)
        page.select_option("#actType", index=1)
        # start in the near SIM-time future (the planner runs on sim time)
        ts = json.loads(_get(f"{PLANNER}/api/time-source")[2])
        sim_now = time.mktime(time.strptime(ts["sim_time"][:16], "%Y-%m-%dT%H:%M"))
        start = time.strftime("%Y-%m-%dT%H:%M", time.gmtime(sim_now + 2 * 3600))
        page.fill("#actStartTime", start)
        page.fill("#actDuration", "10")
        page.click("#btnModalSubmit")
        page.wait_for_function(
            "() => document.querySelectorAll('#activityList [data-act]').length > 0",
            timeout=10000)
        assert not dialogs, f"planner raised: {dialogs}"
        assert len(self._schedule()) == n0 + 1

        # EDIT: change duration through the per-row EDIT button
        page.click("#activityList [data-act='edit']")
        page.wait_for_selector("#modalOverlay", state="visible", timeout=5000)
        page.fill("#actDuration", "20")
        page.click("#btnModalSubmit")
        page.wait_for_selector("#modalOverlay", state="hidden", timeout=10000)
        assert not dialogs, f"planner raised: {dialogs}"

        def _dur(a):
            return (a.get("duration_s") or a.get("duration_minutes", 0) * 60
                    or a.get("duration_min", 0) * 60)
        assert any(_dur(a) == 1200 for a in self._schedule()), self._schedule()

        # DELETE (the confirm dialog is auto-accepted above)
        page.click("#activityList [data-act='delete']")
        deadline = time.time() + 10
        while time.time() < deadline and len(self._schedule()) != n0:
            time.sleep(0.5)
        assert len(self._schedule()) == n0

    def test_wide_page_non_interactive_and_all_six_blocks(self, page):
        page.goto(f"{PLANNER}/wide", wait_until="domcontentloaded")
        page.wait_for_selector("#block-clock", timeout=15000)
        # plan §2.3: zero interactive elements
        assert page.locator("button, form, input, select, textarea").count() == 0
        for block in ("block-clock", "block-passes", "block-timeline",
                      "block-track", "block-budgets", "block-link"):
            assert page.locator(f"#{block}").count() == 1, block
        # the wall page renders content, not placeholders
        page.wait_for_function(
            "() => document.getElementById('wUtc').textContent.trim().length >= 8",
            timeout=15000)


# ── 4. Instructor in the browser ─────────────────────────────────────────────

class TestInstructorBrowser:
    def _open(self, page):
        page.goto(f"{SIM}/", wait_until="domcontentloaded")
        page.wait_for_selector("#btn-inject", timeout=15000)
        return page

    def test_inject_typed_failure_and_clear(self, page):
        self._open(page)
        page.wait_for_function(
            "() => document.querySelectorAll('#fail-subsystem option').length > 0"
            " && document.getElementById('fail-subsystem').value !== ''",
            timeout=15000)
        page.wait_for_function(
            "() => document.querySelectorAll('#fail-type option').length > 0",
            timeout=10000)
        page.click("#btn-inject")
        page.wait_for_function(
            "() => document.getElementById('failures-list')"
            ".textContent.includes('CLEAR')", timeout=15000)
        page.click("#failures-list button")
        page.wait_for_function(
            "() => !document.getElementById('failures-list')"
            ".textContent.includes('CLEAR')", timeout=15000)

    def test_scenario_start_stop_and_debrief(self, page):
        self._open(page)
        page.wait_for_function(
            "() => document.querySelectorAll('#scenario-list .scenario-item,"
            " #scenario-list [data-scenario], #scenario-list li,"
            " #scenario-list button, #scenario-list div').length > 0",
            timeout=15000)
        page.locator("#scenario-list > *").first.click()
        page.click("#btn-start-scenario")
        page.wait_for_function(
            "() => document.getElementById('scenario-chip')"
            ".textContent.trim().length > 0", timeout=15000)
        time.sleep(2)
        page.click("#btn-stop-scenario")
        # debrief: fetch and expect the placeholder to be replaced
        page.wait_for_selector("#btn-fetch-debrief", timeout=5000)
        page.wait_for_function(
            "() => { const el = document.getElementById('btn-fetch-debrief');"
            " el.click();"
            " const t = document.getElementById('debrief-body').textContent;"
            " return !t.includes('No debrief'); }", timeout=20000)
        body = page.inner_text("#debrief-body")
        assert body.strip() != ""
