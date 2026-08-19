"""/wide wall display — non-interactivity + freshness gates (plan §2.3).

Owned by Agent 3 (planner modern). The wall display is a DISTINCT page with
hard requirements:

  - ZERO interactive elements: no <button>, <form>, <input>, <select>;
    no inline event handlers (onclick= etc.); wide.js registers no user
    event handlers (its only listener is DOMContentLoaded).
  - All six blocks present (clock / passes / timeline / track / budgets /
    link), each with a stable id, arranged as a narrow left rail (time /
    passes / link, stacked) beside a stage that the ground track dominates
    (rework 2026-08-19, owner field report: "the ground track view is too
    small" on a real wall screen).
  - Freshness watchdog in wide.js: per-block stale treatment plus a
    full-page STALE banner after 120 s without a successful poll (risk R7).
  - Wall type floor: nothing below 18 px, primary numerals >= 28 px.
  - Two layout tiers (stacked stage on 16:9 + a full-height map column on
    ultrawide, via aspect-ratio media queries).
  - JS polling only — no meta-refresh.

v2 (2026-08-19, second wall-screen field report) adds:

  - ONE ground-track renderer on this page: the canvas (the vendor SVG map
    path is gone from /wide; it stays on the interactive planner + MCS).
  - The next-orbit boundary is DERIVED from served data (equator crossings
    of the ground track, Kepler-from-altitude fallback), never a literal,
    and the plan block and the map share the one derivation.
  - The plan block labels contacts/activities inside that next orbit only.
  - The map draws the canonical ground stations and the AOS/LOS points of
    the same contacts the NEXT PASSES block lists.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MODERN = REPO / "packages/smo-planner/src/smo_planner/static/modern"
WIDE_HTML = MODERN / "wide.html"
WIDE_JS = MODERN / "wide.js"
WIDE_BASEMAP_JS = MODERN / "wide-basemap.js"

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
    # Both tiers must actually re-shape the stage, not just the rail.
    assert re.search(r"@media\s*\(min-aspect-ratio", css)
    assert css.count("grid-template-areas") >= 2, (
        "the ultrawide tier must re-place the stage blocks, not merely "
        "resize them")


# ── Layout rework 2026-08-19: rail + stage, map as the centrepiece ───────

def test_rail_stacks_time_passes_and_link():
    """The owner asked for time / next passes / contact status stacked in one
    narrow column, with the freed width going to the map and the plan."""
    html = WIDE_HTML.read_text()
    rail = re.search(r'<div class="rail">(.*?)</div>\s*<!-- ══ Stage',
                     html, re.S)
    assert rail, "no .rail container in wide.html"
    body = rail.group(1)
    for block_id in ("block-clock", "block-passes", "block-link"):
        assert f'id="{block_id}"' in body, (
            f"#{block_id} must live in the stacked left rail")
    for block_id in ("block-track", "block-timeline", "block-budgets"):
        assert f'id="{block_id}"' not in body, (
            f"#{block_id} belongs on the stage, not in the narrow rail")
    # The rail stacks: one column, three rows.
    assert re.search(r"\.rail\{[^}]*grid-template-rows", html, re.S), (
        "the rail must stack its three blocks in rows")


def test_stage_holds_track_plan_and_budgets_with_the_map_largest():
    html = WIDE_HTML.read_text()
    stage = re.search(r'<div class="stage">(.*?)\n  </div>', html, re.S)
    assert stage, "no .stage container in wide.html"
    body = stage.group(1)
    for block_id in ("block-track", "block-timeline", "block-budgets"):
        assert f'id="{block_id}"' in body

    # Tier 1 stacks the stage; the map row must be the largest by a clear
    # margin (it is the centrepiece of a wall display).
    rows = re.search(r"\.stage\{[^}]*grid-template-rows:\s*"
                     r"minmax\(0,(\d+)fr\)\s*minmax\(0,(\d+)fr\)\s*"
                     r"minmax\(0,(\d+)fr\)", html, re.S)
    assert rows, "tier-1 stage rows not found"
    track, plan, budgets = (int(g) for g in rows.groups())
    assert track >= 2 * plan, (
        f"ground track row is {track}fr against a {plan}fr plan row — the "
        f"map must dominate the wall")
    assert track > budgets


def test_budgets_is_a_slim_horizontal_strip():
    """Budgets moved out of its own column into a full-width strip under the
    plan; no readout was dropped."""
    html = WIDE_HTML.read_text()
    assert "budstrip" in html, "budgets must render as the horizontal strip"
    assert re.search(r"\.budstrip\{[^}]*grid-template-columns", html, re.S)
    for rid in ("wPowerVal", "wPowerBar", "wPowerSub",
                "wDataVal", "wDataBar", "wDataSub",
                "wPassFit", "wSocExit", "wMissionSub"):
        assert f'id="{rid}"' in html, f"budget readout #{rid} lost in the rework"


def test_ground_track_map_scales_without_distortion():
    """The map must fill its (now much larger) box with ONE scale for both
    axes — the old code stretched lon->[0,w] and lat->[0,h] independently,
    which squashed the world flat in a wide block."""
    js = WIDE_JS.read_text()
    assert "trackProjection" in js, "no explicit map projection in wide.js"
    assert re.search(r"Math\.min\(\s*w\s*/\s*360\s*,\s*h\s*/", js), (
        "the projection scale must be the smaller of the width-filling and "
        "height-filling scales, applied to BOTH axes")
    assert not re.search(r"\(\(lon \+ 180\) / 360\) \* w", js), (
        "aspect-stretching lon->[0,w] / lat->[0,h] projection is back")
    assert "TRACK_LAT_VIS" in js, (
        "the visible latitude band must be explicit so the polar reach of a "
        "sun-synchronous track is never cropped off the wall")


def test_canvas_is_the_only_ground_track_renderer():
    """Renderer decision 2026-08-19: /wide has ONE map renderer, the canvas.

    The page used to try the vendor SVG world map first and fall back to the
    canvas, so the wall looked different depending on whether a deployment
    shipped vendor/ — and only one of the two paths was ever exercised. The
    wall's map now carries a dashed/solid split at the derived orbit
    boundary, haloed labels, AOS/LOS glyphs on the track and station markers;
    maintaining that twice is the worse trade. The canvas path is
    self-contained and offline, so it is the one that is kept, tested and
    screenshotted. (The interactive planner page and the MCS still use the
    vendor map; this test governs /wide only.)
    """
    js = WIDE_JS.read_text()
    html = WIDE_HTML.read_text()
    assert WIDE_BASEMAP_JS.exists(), (
        "wide-basemap.js missing — a bare graticule is not a map")
    assert re.search(r"export const LAND = \[", WIDE_BASEMAP_JS.read_text())
    assert 'from "./wide-basemap.js"' in js, (
        "wide.js must import the offline basemap it draws")
    assert 'id="wTrackCanvas"' in html, "the canvas renderer element is gone"
    # No vendor map is loaded or instantiated (prose in the decision comments
    # is fine; a <script> tag or a constructor call is not).
    assert not re.search(r"<script[^>]*svg-world-map", html), (
        "wide.html loads the vendor world map again — /wide keeps exactly "
        "one ground-track renderer (the canvas)")
    assert 'id="wideMap"' not in html, "the vendor map container is back"
    assert not re.search(r"new\s+SvgWorldMap", js), (
        "wide.js instantiates the vendor world map — /wide keeps exactly "
        "one ground-track renderer (the canvas)")
    assert not re.search(r"\.src\s*=\s*[\"'][^\"']*svg-world-map", js), (
        "wide.js injects the vendor world map script again")


# ── Owner requests 2026-08-19 (wall-screen field report, v2) ─────────────

def test_orbital_period_is_derived_from_served_data_not_a_literal():
    """The one-orbit boundary both blocks draw must be MEASURED.

    Primary derivation: equator crossings of the served ground track (half a
    nodal period apart). Fallback: Kepler from the reported mean altitude.
    Neither may be a baked-in minutes figure — this repo has shipped a stale
    period constant before, and the elements changed again this month.
    """
    js = WIDE_JS.read_text()
    assert "derivedOrbitPeriod" in js, "no derived orbital period in wide.js"
    # Measured from the track's own equator crossings ...
    assert re.search(r"\(a\.lat\s*<\s*0\)\s*===\s*\(b\.lat\s*<\s*0\)", js), (
        "the period must come from equator crossings of the served track")
    # ... with a physics fallback that uses REPORTED altitude, not a literal.
    assert "MU_EARTH_KM3_S2" in js and "EARTH_MEAN_R_KM" in js
    assert re.search(r"alt_km", js), (
        "the Kepler fallback must read the reported altitude")
    # No bare period literal anywhere: nothing that looks like NN.N minutes
    # multiplied into milliseconds.
    assert not re.search(r"9[0-9]\.\d\s*\*\s*60", js), (
        "a hard-coded orbital period literal is back in wide.js")


def test_both_blocks_use_the_same_one_orbit_boundary():
    """The labelled plan lane and the solid/dotted ground track must be the
    same story: one derivation, called by both renderers."""
    js = WIDE_JS.read_text()
    timeline = js[js.index("function renderTimeline"):
                  js.index("function trackPosAt")]
    trackfn = js[js.index("function renderTrackCanvas"):]
    assert "derivedOrbitPeriod()" in timeline, (
        "the PLAN block must label against the derived orbit boundary")
    assert "derivedOrbitPeriod()" in trackfn, (
        "the GROUND TRACK block must split solid/dotted on the SAME derived "
        "orbit boundary")


def test_plan_labels_only_the_next_orbit():
    """Text labels (activity type / station) on the imminent items only —
    labelling all ~15 revolutions is unreadable on a wall."""
    js = WIDE_JS.read_text()
    assert "activityLabel" in js, "no activity-type label in the plan block"
    timeline = js[js.index("function renderTimeline"):
                  js.index("function trackPosAt")]
    # The labelled lane is bounded by dEnd = now + one derived period.
    assert re.search(r"dEnd\s*=\s*orbMs\s*\?\s*now\s*\+\s*orbMs", timeline), (
        "the labelled lane must end at one derived orbital period")
    assert "c.aos < dEnd" in timeline and "a.startMs < dEnd" in timeline, (
        "only contacts/activities inside the next orbit may be labelled")
    # Labels are wall-sized.
    assert re.search(r"Math\.max\(21,", timeline), (
        "next-orbit labels must not fall below the 21 px used at wall "
        "distance (page floor is 18 px)")


def test_ground_track_is_dotted_after_the_next_orbit():
    js = WIDE_JS.read_text()
    trackfn = js[js.index("function renderTrackCanvas"):]
    assert "setLineDash" in trackfn, (
        "beyond the next orbit the track must be dotted, not just faded")
    assert re.search(r"const solid = boundary === null \|\| t <= boundary",
                     trackfn), (
        "the solid/dotted split must be a time test against the one-orbit "
        "boundary")


def test_ground_stations_come_from_the_canonical_endpoint_and_are_drawn():
    js = WIDE_JS.read_text()
    assert '"/api/ground-stations"' in js, (
        "station positions must come from /api/ground-stations (orbit.yaml "
        "ground_stations — the canonical list, defect #42), never a copy")
    trackfn = js[js.index("function renderTrackCanvas"):]
    assert "for (const s of stations)" in trackfn, (
        "the stations must be plotted on the map")
    assert "s.name.toUpperCase()" in trackfn, (
        "station markers must be NAMED — an unlabelled triangle on a wall "
        "tells the room nothing")
    # No station geometry may be hard-coded in the page.
    for name in ("Svalbard", "Iqaluit", "Troll", "Inuvik", "Higgins"):
        assert name not in js, (
            f"wide.js hard-codes station '{name}' — the list is served")


def test_aos_los_marks_share_the_next_passes_dataset():
    """The AOS/LOS marks on the map and the NEXT PASSES list must be two
    views of ONE dataset, filtered identically, so they cannot disagree."""
    js = WIDE_JS.read_text()
    passes = js[js.index("function renderPasses"):
                js.index("function activityLabel")]
    trackfn = js[js.index("function renderTrackCanvas"):]
    assert re.search(r"contacts\.filter\(\(c\) => c\.los > now\)", passes), (
        "NEXT PASSES selection changed — keep the map in step with it")
    assert re.search(r"contacts\.filter\(\(c\) => c\.los > now\)", trackfn), (
        "the map's AOS/LOS marks must use the SAME contact selection as the "
        "NEXT PASSES block")
    assert "trackPosAt" in trackfn, (
        "AOS/LOS must be interpolated ONTO the plotted track, not guessed")
    assert '"AOS ' in trackfn and '"LOS ' in trackfn, (
        "AOS and LOS marks must be labelled as such")


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
