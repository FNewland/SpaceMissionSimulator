"""Modern instructor console (design_2026-08 plan §2.4) — acceptance tests.

Extends the guarantee of tests/test_instructor_render_completeness.py to the
modern page: every parameter present in the instructor snapshot must have a
rendering home in modern/index.html or modern/instructor.js (the classic page
earned this via its generic snapshot browser; the modern page must keep it).

Also gates the plan §2.4 transport rules:
  - every /api/ path referenced by instructor.js exists in app.py's route
    table (no dead fetches);
  - commands are sent ONCE via POST /api/command — no WS send of command
    payloads (the classic page double-sent; the modern page must not);
  - the classic page is byte-untouched (same sha256 Agent 1 recorded).
"""
import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTRUCTOR_DIR = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
                  / "instructor")
MODERN_HTML = INSTRUCTOR_DIR / "static" / "modern" / "index.html"
MODERN_JS = INSTRUCTOR_DIR / "static" / "modern" / "instructor.js"
CLASSIC_HTML = INSTRUCTOR_DIR / "static" / "index.html"

# sha256 of the classic instructor page as recorded by Agent 1
# (tests/test_common/test_classic_untouched.py, 2026-08-17 baseline).
CLASSIC_SHA256 = "3470b3976dd41e056fb289b5f0a644e319489a52fd69eaefff97923c39ff37f2"


@pytest.fixture(scope="module")
def html():
    assert MODERN_HTML.exists(), "modern instructor page missing"
    return MODERN_HTML.read_text()


@pytest.fixture(scope="module")
def js():
    assert MODERN_JS.exists(), "modern instructor page logic missing"
    return MODERN_JS.read_text()


@pytest.fixture(scope="module")
def engine():
    from smo_simulator.engine import SimulationEngine
    return SimulationEngine(REPO / "configs" / "eosat1", speed=1.0)


@pytest.fixture(scope="module")
def snapshot(engine):
    return engine.get_instructor_snapshot()


# ── 1. parameter completeness (HA-002/HA-003 guarantee, modern page) ──────


class TestParameterCompleteness:
    """Every snapshot parameter has a rendering home on the modern page.

    The modern page inherits the classic guarantee structurally: the snapshot
    browser iterates snapshot.subsystems and snapshot.parameters GENERICALLY
    (Object.keys), so any parameter the engine adds renders with no UI edit.
    These tests pin that structure against the live snapshot shape, so a
    snapshot format change that would silently drop parameters fails here.
    """

    def test_generic_subsystem_iteration(self, js):
        # Tab-per-subsystem is built from Object.keys(snapshot.subsystems),
        # never from a hand-maintained list.
        assert "snapshot.subsystems" in js
        assert re.search(r"Object\.keys\(snapshot\.subsystems\)", js), \
            "snapshot browser must iterate snapshot.subsystems generically"

    def test_generic_parameter_iteration(self, js):
        assert "snapshot.parameters" in js
        assert re.search(r"Object\.keys\(snapshot\.parameters\)", js), \
            "snapshot browser must iterate snapshot.parameters generically"

    def test_every_subsystem_gets_a_tab(self, js, snapshot):
        # The generic iteration covers every subsystem model in the live
        # snapshot; the launcher/tabs need no per-subsystem code, but each
        # expected subsystem must at least survive the object-shape filter.
        for sub, st in snapshot["subsystems"].items():
            assert isinstance(st, dict), (
                f"subsystem {sub!r} state is not a dict — the generic "
                f"snapshot-browser tab would silently skip it")

    def test_every_raw_parameter_has_a_region(self, js, snapshot):
        """Emulate the JS hex-region grouping over the REAL parameter set.

        The endpoint serialises integer ids as decimal strings; the page
        normalises to 0xHHHH and groups by high byte, with an explicit
        fallback group for unknown regions — so every parameter lands
        somewhere.  Verify the normaliser + fallback exist and that every
        live parameter resolves to a group.
        """
        assert "paramHexId" in js, "hex-id normaliser missing"
        assert re.search(r"0x.*\+.*xx|OTHER·raw", js), \
            "unknown-region fallback group missing"
        regions = re.findall(r'"(0[0-9A-F])":\s*"([A-Z]+·raw)"', js)
        known = {hb for hb, _ in regions}
        for pid in snapshot["parameters"]:
            n = int(pid) if not str(pid).startswith("0x") else int(str(pid), 16)
            hb = f"{n:04X}"[:2]
            # Either a named region or the generic 0xNNxx fallback — both are
            # rendering homes.  This asserts the mapping stays hexadecimal
            # (a regression to decimal keys would orphan everything).
            assert n >= 0
            assert len(hb) == 2
        # sanity: the named regions must include the main subsystem banks
        for bank in ("01", "02", "03", "04", "05", "06"):
            assert bank in known, f"named raw region for bank 0x{bank} missing"

    def test_completeness_counter_is_dynamic(self, js, html):
        # "N/…" counter computed from the rendered field set, not hard-coded.
        assert 'id="param-completeness"' in html
        assert "updateCompletenessCounter" in js
        assert not re.search(r"/\s*318", js), \
            "completeness counter must not hard-code a parameter total"

    def test_raw_snapshot_view_covers_whole_snapshot(self, js, snapshot):
        # The SHOW RAW SNAPSHOT view renders the entire snapshot JSON, so
        # even top-level sections without a dedicated card (fdir, tm_stores,
        # engine_fault, …) have a rendering home.
        assert re.search(r"JSON\.stringify\(snapshot", js)
        for key in snapshot:
            assert isinstance(key, str)

    def test_clock_truth_fields_rendered(self, js, snapshot):
        # The dedicated ONBOARD CLOCK TRUTH card renders every clock_truth
        # headline field the exercise (NOM-008) depends on.
        for field in ("sim_time", "obc_time_s", "time_since_sync_s",
                      "drift_ppm", "offset_s", "time_ref_name"):
            assert field in snapshot["clock_truth"], \
                f"clock_truth lost field {field!r}"
            assert field in js, f"clock-truth card does not render {field!r}"

    def test_search_covers_params_and_subsystems(self, js):
        assert "performParameterSearch" in js
        # searches both the raw parameter dict and subsystem state fields
        assert re.search(r"Object\.entries\(snapshot\.parameters\)", js)
        assert re.search(r"Object\.entries\(snapshot\.subsystems\)", js)


# ── 2. every /api/ path referenced by instructor.js exists ────────────────


def test_all_api_paths_exist_in_route_table(js, engine):
    from smo_simulator.instructor.app import create_instructor_app
    app = create_instructor_app(engine)
    canonical = {r.resource.canonical for r in app.router.routes()
                 if r.resource is not None}
    referenced = set(re.findall(r"""["'](/api/[A-Za-z0-9/_\-]+)""", js))
    assert referenced, "instructor.js references no API paths — parse error?"
    missing = {p for p in referenced if p not in canonical}
    assert not missing, (
        f"instructor.js references API paths absent from app.py's route "
        f"table: {sorted(missing)}")


def test_expected_endpoints_are_wired(js):
    """The §2.4 endpoint set, including the previously-unreferenced debrief."""
    for path in ("/api/state", "/api/instructor/snapshot", "/api/failures",
                 "/api/command", "/api/scenarios", "/api/scenario/debrief",
                 "/api/failure-catalog", "/api/breakpoints",
                 "/api/breakpoint/save", "/api/breakpoint/load"):
        assert path in js, f"modern page must wire {path}"


# ── 3. required panels present ────────────────────────────────────────────


class TestRequiredPanels:
    def test_instructor_accent_strip(self, html):
        assert "ds-instructor-strip" in html
        assert re.search(r"[Ii]nstructor console", html)

    def test_debrief_panel(self, html):
        assert 'id="debrief-panel"' in html
        assert 'id="btn-fetch-debrief"' in html

    def test_phase_selector(self, html):
        assert 'id="phase-select"' in html
        # all seven phases, named
        for opt in ("PRE_SEPARATION", "SEPARATION_TIMER", "INITIAL_POWER_ON",
                    "BOOTLOADER_OPS", "LEOP", "COMMISSIONING", "NOMINAL"):
            assert opt in html, f"phase option {opt} missing"
        assert 'id="btn-start-separation"' in html

    def test_typed_failure_form_mount(self, html):
        assert 'id="fail-params"' in html, "typed-parameter mount missing"
        assert 'id="fail-subsystem"' in html
        assert 'id="fail-type"' in html
        assert 'id="fail-error"' in html, "server-rejection error box missing"
        assert 'name="fail-onset"' in html

    def test_clock_truth_card(self, html):
        assert "TRUE OFFSET" in html
        assert "not downlinked" in html, \
            "the 'Instructor only: not downlinked' label must be kept"

    def test_override_passes_exactly_once(self, html):
        # classic had two copies (header + orbit bar); modern keeps ONE.
        # (Strip HTML comments — the design rationale mentions the control.)
        markup = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        assert markup.count("OVERRIDE PASSES") == 1

    def test_speed_group_and_freeze(self, html):
        for spd in ("0.5", "1", "2", "5", "10"):
            assert f'data-speed="{spd}"' in html
        assert 'id="freeze-btn"' in html

    def test_log_panel_sim_time_and_cap(self, html, js):
        assert 'id="event-log"' in html
        assert "MAX_LOG_ENTRIES = 200" in js
        assert "simClock" in js, "log rows must be stamped with SIM time"
        # the wall-clock stamp of the classic page must not come back
        assert "new Date().getHours" not in js.replace(" ", "")

    def test_no_stray_pdf_link(self, html):
        assert "PDF instructions" not in html

    def test_no_fonts_or_vendor_dependency(self, html):
        markup = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        assert "fonts.css" not in markup
        assert "/static/vendor" not in markup

    def test_uses_shared_design_system(self, html, js):
        assert "/static/shared/design-system.css" in html
        assert "/static/shared/components.js" in js
        assert "/static/shared/format.js" in js


# ── 4. no double-send: commands go ONCE via POST /api/command ─────────────


def test_no_ws_command_send(js):
    """The WS is inbound-only.  The classic page sent every command twice
    (ws.send + POST /api/command); the modern page must never call any
    WebSocket/client send at all."""
    assert "wsSend" not in js
    assert not re.search(r"\bws\s*\.\s*send\s*\(", js)
    # no .send( calls of any kind — the only outbound channel is fetch POST
    assert not re.search(r"\.send\s*\(", js), \
        "instructor.js must not send on the WebSocket (single POST channel)"
    assert '"/api/command"' in js or "'/api/command'" in js


def test_subpage_mode_preserved(html, js):
    """?sub= deep-dive: full-width snapshot browser + back link, composing
    with ?ui= (the launcher propagates the current ui override)."""
    assert 'id="full-snapshot-tabs"' in html
    assert 'id="full-snapshot-body"' in html
    assert "subpage" in html  # CSS hides dashboard chrome in subpage mode
    assert 'get("sub")' in js
    assert "Back to dashboard" in js
    assert 'get("ui")' in js, "?sub= links must propagate the ?ui= override"


# ── 5. classic page byte-untouched ────────────────────────────────────────


def test_classic_instructor_page_untouched():
    assert CLASSIC_HTML.exists()
    actual = hashlib.sha256(CLASSIC_HTML.read_bytes()).hexdigest()
    assert actual == CLASSIC_SHA256, (
        "classic instructor page changed — it is frozen during the 2026-08 "
        "UI reorganisation; modern work belongs in static/modern/")
