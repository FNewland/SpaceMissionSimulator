"""/wide wall display — non-interactivity + freshness gates (plan §2.3).

Owned by Agent 3 (planner modern). The wall display is a DISTINCT page with
hard requirements:

  - ZERO interactive elements: no <button>, <form>, <input>, <select>;
    no inline event handlers (onclick= etc.); wide.js registers no user
    event handlers (its only listener is DOMContentLoaded).
  - All six blocks present (clock / passes / timeline / track / budgets /
    link), each with a stable id.
  - Freshness watchdog in wide.js: per-block stale treatment plus a
    full-page STALE banner after 120 s without a successful poll (risk R7).
  - Wall type floor: nothing below 18 px, primary numerals >= 28 px.
  - Two layout tiers (16:9 grid + ultrawide single row via an
    aspect-ratio media query).
  - JS polling only — no meta-refresh.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODERN = REPO / "packages/smo-planner/src/smo_planner/static/modern"
WIDE_HTML = MODERN / "wide.html"
WIDE_JS = MODERN / "wide.js"

FORBIDDEN_TAGS = ("button", "form", "input", "select", "textarea")
BLOCK_IDS = ("block-clock", "block-passes", "block-timeline",
             "block-track", "block-budgets", "block-link")


class _Auditor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.ids = set()
        self.event_attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            if name == "id" and value:
                self.ids.add(value)
            if name.lower().startswith("on"):
                self.event_attrs.append((tag, name))


def _audit():
    a = _Auditor()
    a.feed(WIDE_HTML.read_text())
    return a


def test_wide_html_parses():
    audit = _audit()
    assert "html" in audit.tags and "body" in audit.tags


@pytest.mark.parametrize("tag", FORBIDDEN_TAGS)
def test_zero_interactive_elements(tag):
    audit = _audit()
    count = audit.tags.count(tag)
    assert count == 0, (
        f"wide.html contains {count} <{tag}> element(s) — the wall display "
        f"is non-interactive by hard requirement (plan §2.3)")


def test_no_inline_event_handlers():
    audit = _audit()
    assert not audit.event_attrs, (
        f"wide.html carries inline event handlers {audit.event_attrs} — "
        f"nothing on the wall is clickable")
    assert "onclick=" not in WIDE_HTML.read_text()


@pytest.mark.parametrize("block_id", BLOCK_IDS)
def test_all_six_blocks_present(block_id):
    audit = _audit()
    assert block_id in audit.ids, (
        f"wall block #{block_id} missing — plan §2.3 requires all six")


def test_wide_js_registers_no_user_event_handlers():
    js = WIDE_JS.read_text()
    listeners = re.findall(r"addEventListener\(\s*[\"']([^\"']+)[\"']", js)
    assert all(ev == "DOMContentLoaded" for ev in listeners), (
        f"wide.js registers user event handlers {listeners} — only the "
        f"DOMContentLoaded bootstrap is allowed on the wall display")
    assert "onclick" not in js


def test_watchdog_present_with_120s_threshold():
    js = WIDE_JS.read_text()
    assert "watchdog" in js, "no freshness watchdog in wide.js (risk R7)"
    assert re.search(r"PAGE_STALE_MS\s*=\s*120000", js), (
        "full-page STALE banner threshold must be 120 s (plan §2.3)")
    html = WIDE_HTML.read_text()
    assert 'id="staleBanner"' in html, "full-page STALE banner element missing"


def test_per_block_stale_treatment():
    js = WIDE_JS.read_text()
    html = WIDE_HTML.read_text()
    assert "isStale" in js, "no per-block staleness predicate in wide.js"
    # The hatch affordance comes from the design-system --hatch token.
    assert "--hatch" in html, "stale blocks must use the hatch affordance"


def test_no_meta_refresh():
    assert "http-equiv" not in WIDE_HTML.read_text().lower(), (
        "the wall refreshes by JS polling, never meta-refresh (plan §2.3)")


def test_training_chip_is_sim_gated():
    js = WIDE_JS.read_text()
    html = WIDE_HTML.read_text()
    assert 'id="trainingChip"' in html
    # Chip only renders when the resolved time source is the simulator.
    assert re.search(r'timeSource\s*===\s*"sim"', js), (
        "TRAINING chip must be gated on time_source == 'sim' (plan §2.3 / M1)")


def test_type_floor_for_wall_readability():
    """Nothing below 18 px in wide.html's page CSS; primary numerals big."""
    css = WIDE_HTML.read_text()
    sizes = [float(s) for s in re.findall(r"font-size:\s*(?:min\([^,]+,\s*)?"
                                          r"(\d+(?:\.\d+)?)px", css)]
    assert sizes, "no font sizes found in wide.html page CSS"
    assert min(sizes) >= 18, (
        f"wide.html declares a {min(sizes)}px font — wall floor is 18 px")
    assert max(sizes) >= 28, "no primary numeral >= 28 px found"


def test_two_layout_tiers():
    css = WIDE_HTML.read_text()
    assert "min-aspect-ratio" in css, (
        "ultrawide tier must switch via an aspect-ratio media query")


# ── G1 on the wall: mission-level derived readouts ───────────────────────

def test_mission_readouts_present_and_default_to_em_dash():
    """PASS FIT + SoC @ ECLIPSE EXIT readouts on the budgets block, and the
    LAST PASS rollup line on the link block — all defaulting to an em-dash
    (invalid / unconfigured / unreachable must NEVER show a number)."""
    html = WIDE_HTML.read_text()
    for rid in ("wPassFit", "wSocExit", "wLastPass"):
        m = re.search(rf'id="{rid}"[^>]*>([^<]*)<', html)
        assert m, f"wall readout #{rid} missing"
        assert m.group(1).strip() == "—", (
            f"wall readout #{rid} defaults to '{m.group(1).strip()}' — must "
            f"be an em-dash until real, valid data arrives")
    # The absent state carries the hatch affordance.
    assert "absent" in html and "--hatch" in html


def test_mission_readouts_render_invalid_as_dash_never_a_number():
    js = WIDE_JS.read_text()
    # MCS derived entries are {value, valid, reason}: only a VALID value may
    # become a numeral; the invalid branch surfaces the reason.
    assert "ttc.pass_fit_ratio" in js
    assert "eps.soc_at_eclipse_exit_pred" in js
    assert re.search(r"\.valid\b", js), (
        "wide.js must gate derived readouts on the derived entry's valid flag")
    assert "reason" in js


def test_wide_cross_origin_bases_are_config_gated():
    """rfsim/MCS reads come only from planner.yaml services.*; no guessed
    hosts, and the path literals are not planner-route lookalikes."""
    js = WIDE_JS.read_text()
    assert "services.rfsim_url" in js and "services.mcs_url" in js
    assert "localhost" not in js, (
        "wide.js must not hard-code a service host — bases come from "
        "planning/planner.yaml services.*")
    assert '"api/pass-rollups' in js and '"api/state' in js, (
        "cross-origin paths must be joined onto the configured base "
        "(no leading slash — those are planner routes)")


def test_wide_js_uses_only_registered_planner_endpoints():
    """Belt-and-braces double of the gate in test_modern_page: the wall
    display must poll real planner routes only."""
    server_src = (REPO / "packages/smo-planner/src/smo_planner/server.py").read_text()
    routes = re.findall(
        r"add_(?:get|post|put|delete)\(\s*[\"']([^\"']+)[\"']", server_src)
    refs = {m.group(1).split("?", 1)[0]
            for m in re.finditer(r"[\"'`](/api/[^\"'`]*)", WIDE_JS.read_text())}
    assert refs, "wide.js references no endpoints — parsing broke?"
    for ref in refs:
        ok = any(ref == r or (ref.endswith("/") and r.startswith(ref))
                 for r in routes)
        assert ok, f"wide.js references unknown endpoint {ref}"
