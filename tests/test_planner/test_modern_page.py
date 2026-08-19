"""Modern planner interactive page — static acceptance gates (plan §2.2).

Owned by Agent 3 (planner modern). These are file-level gates that hold
without a running server:

  1. API-reference gate: every /api/ path referenced by the modern page
     modules (planner.js, wide.js) resolves against the planner's registered
     route table (parsed from server.py — the additive-only API rule means
     the table is the contract).
  2. The explicit SoC input exists (disposition P5 — no hidden battery_soc
     literal; blank means server default).
  3. The per-row EDIT control exists and wires PUT /api/schedule/{id}
     (previously a headless endpoint — Report 1 §1B).
  4. The per-domain constraint breakdown accordion exists (wires
     GET /api/constraints/power|aocs|thermal|data-volume|conflicts).
  5. No hard-coded mission literals: 94.7 (orbit period), 705 (altitude),
     80.0 (battery SoC) must not appear in any modern planner file
     (dispositions P4 + P5).
  6. The classic page is byte-untouched (Agent 1 baseline sha256).
"""
import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "packages/smo-planner/src/smo_planner/static"
MODERN = STATIC / "modern"
SERVER_PY = REPO / "packages/smo-planner/src/smo_planner/server.py"

MODERN_FILES = ["index.html", "planner.js", "wide.html", "wide.js"]


# ── helpers ──────────────────────────────────────────────────────────────

def _registered_routes():
    """Parse the planner route table from server.py (the API contract)."""
    src = SERVER_PY.read_text()
    routes = re.findall(
        r"add_(?:get|post|put|delete|static)\(\s*[\"']([^\"']+)[\"']", src)
    assert routes, "could not parse any routes from server.py"
    return routes


def _api_refs(js_text):
    """Every /api/... string literal referenced by a page module.

    Handles the page's URL styles: full literals ('/api/contacts'),
    querystring literals ('/api/ground-track?duration_hours=' — the query
    part is stripped), template/concatenation prefixes ('/api/schedule/' +
    id — kept with the trailing slash and matched as a prefix of a
    parameterised route).
    """
    refs = set()
    for m in re.finditer(r"[\"'`](/api/[^\"'`]*)", js_text):
        path = m.group(1).split("?", 1)[0]
        refs.add(path)
    return refs


def _resolves(ref, routes):
    for route in routes:
        if ref == route:
            return True
        # '/api/schedule/' + id  ->  '/api/schedule/{id}'
        # '/api/constraints/' + domain -> '/api/constraints/power' etc.
        if ref.endswith("/") and route.startswith(ref) and len(route) > len(ref):
            return True
    return False


# ── 0. files exist ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name", MODERN_FILES)
def test_modern_file_exists(name):
    assert (MODERN / name).exists(), f"static/modern/{name} is missing"


# ── 1. API-reference gate ────────────────────────────────────────────────

@pytest.mark.parametrize("js_name", ["planner.js", "wide.js"])
def test_every_api_reference_is_a_registered_route(js_name):
    routes = _registered_routes()
    refs = _api_refs((MODERN / js_name).read_text())
    assert refs, f"{js_name} references no /api/ endpoints — parsing broke?"
    missing = sorted(r for r in refs if not _resolves(r, routes))
    assert not missing, (
        f"{js_name} references endpoints absent from the planner route "
        f"table: {missing}. Both UIs must hit the same (additive-only) API "
        f"— plan §1.2.")


def test_new_ui_clients_for_previously_headless_endpoints():
    """§2.2 requires the modern page to wire the endpoints the classic page
    left headless (Report 1 §1B)."""
    js = (MODERN / "planner.js").read_text()
    assert "/api/imaging/targets" in js, "targets list (GET /api/imaging/targets) not wired"
    assert "/api/config/planner" in js, "orbit config (GET /api/config/planner) not wired"
    for domain in ("power", "aocs", "thermal", "data-volume", "conflicts"):
        assert f"cx-{domain}" in (MODERN / "index.html").read_text(), (
            f"per-domain constraint accordion for '{domain}' missing")
    assert '"/api/constraints/" + domain' in js or "/api/constraints/${" in js, (
        "per-domain breakdown does not fetch /api/constraints/<domain>")


# ── 2. explicit SoC input (P5) ───────────────────────────────────────────

def test_soc_input_present_and_labelled():
    html = (MODERN / "index.html").read_text()
    assert 'id="socInput"' in html, "explicit SoC input (P5) missing"
    assert 'for="socInput"' in html, "SoC input has no <label for=> (must be labelled)"


def test_blank_soc_sends_no_battery_soc_parameter():
    js = (MODERN / "planner.js").read_text()
    # The blank branch must return the bare endpoint (server default),
    # and no battery_soc literal may be pre-baked anywhere.
    assert 'return "/api/constraints/validate"' in js, (
        "blank SoC must fall back to the bare /api/constraints/validate "
        "(server default), not a client-side literal")
    assert not re.search(r"battery_soc=\d", js), (
        "found a hard-coded battery_soc value — disposition P5 forbids it")


# ── 3. per-row EDIT wiring PUT /api/schedule/{id} ────────────────────────

def test_edit_control_and_put_wiring():
    js = (MODERN / "planner.js").read_text()
    assert 'data-act=\\"edit\\"' in js or 'data-act="edit"' in js, (
        "per-row EDIT control missing from the activity list renderer")
    assert '"PUT"' in js or "'PUT'" in js, "no PUT request found in planner.js"
    assert '"/api/schedule/" + editingId' in js or "/api/schedule/${" in js, (
        "EDIT does not target /api/schedule/{id}")


# ── 5. no hard-coded mission literals (P4/P5) ────────────────────────────

@pytest.mark.parametrize("name", MODERN_FILES)
@pytest.mark.parametrize("literal", ["94.7", "705", "80.0"])
def test_no_hardcoded_orbit_or_soc_literals(name, literal):
    text = (MODERN / name).read_text()
    assert literal not in text, (
        f"static/modern/{name} contains the hard-coded literal '{literal}' "
        f"— orbit/SoC values must come from /api/config/planner, "
        f"/api/spacecraft-state or the ground track (dispositions P4/P5)")


# ── 6. classic page byte-untouched ───────────────────────────────────────

CLASSIC_SHA256 = "775196c579e52c4dffa04d082d02d13ad554e1d441b51e2ab3928f8c5fcf3b77"


def test_classic_planner_page_untouched():
    classic = STATIC / "index.html"
    assert classic.exists(), "classic planner page is missing"
    actual = hashlib.sha256(classic.read_bytes()).hexdigest()
    assert actual == CLASSIC_SHA256, (
        "the classic planner page CHANGED — it is frozen during the 2026-08 "
        "UI reorganisation (Agent 1 baseline); modern work belongs in "
        "static/modern/")


# ── page structure sanity ────────────────────────────────────────────────

def test_index_uses_shared_design_system_and_module_script():
    html = (MODERN / "index.html").read_text()
    assert "/static/shared/design-system.css" in html
    assert 'type="module"' in html and "/static/modern/planner.js" in html


def test_index_data_badges_default_to_em_dash():
    """P3: no static SIMULATED/LIVE text before /api/time-source resolves."""
    html = (MODERN / "index.html").read_text()
    for bid in ("mapDataBadge", "contactDataBadge", "activityDataBadge",
                "powerDataBadge", "dataDataBadge", "imagingDataBadge"):
        m = re.search(rf'id="{bid}"[^>]*>([^<]*)<', html)
        assert m, f"data badge {bid} missing"
        assert m.group(1).strip() == "—", (
            f"data badge {bid} defaults to '{m.group(1).strip()}' — must be "
            f"an em-dash until /api/time-source resolves (disposition P3)")


def test_vendor_missing_state_is_designed():
    html = (MODERN / "index.html").read_text()
    assert 'id="mapFallback"' in html, (
        "no designed vendor-missing state for the ground-track map (risk R3)")


def test_power_budget_cannot_compute_state_rendered():
    js = (MODERN / "planner.js").read_text()
    assert "computable" in js and "CANNOT BE COMPUTED" in js, (
        "the power budget's computable:false answer must render as a "
        "designed state, never a 0%/100% bar (plan §2.2)")


# ── G3 planner half: fit verdicts from the planner's own budget ──────────

def test_fit_verdicts_computed_from_planner_budget_only():
    js = (MODERN / "planner.js").read_text()
    assert "computeFitVerdicts" in js, "G3 fit-verdict join missing"
    assert "WILL NOT FIT" in js, "no will-not-fit highlight (G3)"
    # Per-activity achievable vs backlog + per-pass verdict are rendered.
    assert "DUMP FIT" in js and "ACHIEVABLE" in js
    # The join uses served budget-tracker fields, never the MCS.
    assert "pass_downlink" in js and "capacity_mb" in js
    assert "onboard_data_mb" in js


# ── G14 display half: last-pass rollup, config-gated cross-origin ────────

def test_pass_rollup_panel_present_and_config_gated():
    html = (MODERN / "index.html").read_text()
    js = (MODERN / "planner.js").read_text()
    assert 'id="passRollupPanel"' in html, "last-pass rollup panel missing"
    # Read from the CONFIGURED services.rfsim_url only; unset is a DESIGNED
    # absent state, and no host is ever guessed client-side.
    assert "services.rfsim_url" in js
    assert "No rfsim URL configured" in js
    assert "localhost" not in js, (
        "planner.js must not hard-code a service host — the rfsim base "
        "comes from planning/planner.yaml services.rfsim_url")
    # The cross-origin path is joined onto the configured base; it must NOT
    # be a leading-slash literal (those are reserved for planner routes and
    # gated by test_every_api_reference_is_a_registered_route).
    assert '"api/pass-rollups' in js


def test_services_config_is_additive_and_default_unset():
    """planner.yaml `services` block: additive, default None (absent state)."""
    from smo_planner.planner_config import PlannerConfig, load_planner_config
    cfg = PlannerConfig()
    assert cfg.services.rfsim_url is None
    assert cfg.services.mcs_url is None
    assert "services" in cfg.model_dump(mode="json")
    # The shipped mission config (no services key) still validates.
    shipped = load_planner_config(REPO / "configs" / "eosat1")
    assert shipped.services.rfsim_url is None
