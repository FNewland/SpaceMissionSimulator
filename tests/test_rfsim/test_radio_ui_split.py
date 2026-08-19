"""Radio UI split (design_2026-08): channel model off the operator page.

Standing rule — ground tools are spacecraft-agnostic. The operator radio
page may show what real ground equipment shows (locks, spectrum,
constellation, eye diagram, measured levels and Doppler); the CHANNEL
MODEL (injected impairments, Eb/N0/Doppler knobs, fading/phase-noise/
interferer controls, GS penalty injection) is simulation truth and lives
only on the instructor-badged /sim view.

Gates here:
  1. grep-gate — the served modern operator HTML contains no channel-model
     vocabulary and never links to /sim;
  2. the sim view carries the INSTRUCTOR accent strip and drives the SAME
     endpoints as before (impairment workflow unchanged);
  3. /classic serves the frozen radio.html byte-for-byte, with the house
     SMO_UI / --ui / ?ui= precedence;
  4. route snapshot — the API surface is unchanged (no route removed,
     renamed or re-methoded).
"""
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smo_rfsim.radio.frontend import RadioFrontend
from smo_rfsim.radio.web_ui import build_app, resolve_ui_mode

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "packages/smo-rfsim/src/smo_rfsim/static"
CLASSIC = STATIC / "radio.html"
MODERN = STATIC / "modern/index.html"
SIM_VIEW = STATIC / "modern/sim.html"


# ══════════════ 1. Grep-gate: operator page is channel-model-free ════════

# Vocabulary that reveals or controls simulation truth. Case-insensitive.
CHANNEL_MODEL_TOKENS = [
    "channel model", "channel-model", "channel_model",
    "phase noise", "phase_noise", "phasenoise",
    "fading", "interferer", "awgn", "impair",
    "doppler ramp", "oscillator", "iq imbalance", "tracking-loss",
    "gs_penalty", "gs-penalty", "penalty",
    "/api/channel", "/api/pipeline", "/api/link",
    "cfg-ebn0", "cfg-dopp", "release to sim",
]


class TestOperatorPageIsChannelModelFree:
    def test_modern_operator_page_exists(self):
        assert MODERN.exists()

    @pytest.mark.parametrize("token", CHANNEL_MODEL_TOKENS)
    def test_no_channel_model_vocabulary(self, token):
        html = MODERN.read_text().lower()
        assert token not in html, (
            f"operator radio page contains channel-model vocabulary "
            f"{token!r} — simulation truth belongs on /sim (instructor) only")

    def test_no_link_to_sim_view(self):
        html = MODERN.read_text()
        assert not re.search(r"""(href|fetch|open)\(?['"=]+/sim\b""", html)
        assert "/sim" not in html, (
            "operator page must not reference the instructor sim view")

    def test_no_post_requests_at_all(self):
        # The operator radio console is purely observational.
        assert "POST" not in MODERN.read_text().upper().replace(
            "POSITION", "")  # css word 'position' is fine

    def test_operator_keeps_the_legitimate_set(self):
        """What a real ground-station rack shows stays."""
        html = MODERN.read_text().lower()
        for legit in ["carrier", "bit sync", "frame sync", "rssi", "eb/n0",
                      "spectrum", "constellation", "eye diagram",
                      "doppler", "link budget", "virtual channel", "cltu"]:
            assert legit in html, f"operator page lost {legit!r}"

    def test_uses_shared_design_system(self):
        html = MODERN.read_text()
        assert "/static/shared/design-system.css" in html
        assert "ds-card" in html
        assert "ds-instructor-strip" not in html


# ══════════════ 2. Sim view: INSTRUCTOR badge + same endpoints ═══════════

class TestSimView:
    def test_sim_view_exists_and_is_instructor_badged(self):
        html = SIM_VIEW.read_text()
        assert "ds-instructor-strip" in html, (
            "sim view must carry the INSTRUCTOR accent strip (house "
            "convention, plan §2.4)")
        assert "instructor" in html.lower()
        assert "/static/shared/design-system.css" in html

    def test_sim_view_drives_the_same_endpoints(self):
        """MOVE-I, not rewrite: the impairment workflow is unchanged."""
        html = SIM_VIEW.read_text()
        assert "/api/channel" in html
        assert "/api/pipeline" in html
        # Same payload fields the bridge accepts (_CHANNEL_KEYS)
        for key in ["eb_n0_db", "doppler_hz", "phase_noise_enabled",
                    "fading_enabled", "interferers", "awgn_enabled"]:
            assert key in html, f"sim view lost impairment field {key!r}"
        # Release-to-sim escape hatch preserved
        assert '{ release: "all" }' in html or "'release': 'all'" in html

    def test_sim_view_shows_gs_penalty(self):
        # Injected ground-equipment degradation is sim truth: lives here,
        # not on the operator page.
        assert "gs_penalty_db" in SIM_VIEW.read_text()


# ══════════════ 3. UI switch: house precedence + /classic frozen ═════════

async def _client(ui_mode=None, control=None) -> TestClient:
    app = build_app(RadioFrontend(), control=control, ui_mode=ui_mode)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestUISwitch:
    def test_precedence(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        assert resolve_ui_mode() == "modern"          # shipped default
        monkeypatch.setenv("SMO_UI", "classic")
        assert resolve_ui_mode() == "classic"          # env beats default
        assert resolve_ui_mode("modern") == "modern"   # CLI beats env
        assert resolve_ui_mode("nonsense") == "classic"

    async def test_classic_route_serves_frozen_page(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _client()
        try:
            resp = await client.get("/classic")
            assert resp.status == 200
            assert resp.headers.get("Cache-Control") == "no-cache"
            assert await resp.read() == CLASSIC.read_bytes(), (
                "/classic must serve radio.html byte-untouched")
        finally:
            await client.close()

    async def test_index_default_modern_and_query_override(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _client()
        try:
            resp = await client.get("/")
            assert resp.headers.get("Cache-Control") == "no-cache"
            assert await resp.text() == MODERN.read_text()
            resp = await client.get("/?ui=classic")
            assert await resp.text() == CLASSIC.read_text()
            resp = await client.get("/modern")
            assert await resp.text() == MODERN.read_text()
        finally:
            await client.close()

    async def test_env_classic_and_query_modern(self, monkeypatch):
        monkeypatch.setenv("SMO_UI", "classic")
        client = await _client()
        try:
            resp = await client.get("/")
            assert await resp.text() == CLASSIC.read_text()
            resp = await client.get("/?ui=modern")
            assert await resp.text() == MODERN.read_text()
        finally:
            await client.close()

    async def test_sim_route_serves_instructor_view(self, monkeypatch):
        monkeypatch.delenv("SMO_UI", raising=False)
        client = await _client()
        try:
            resp = await client.get("/sim")
            assert resp.status == 200
            assert resp.headers.get("Cache-Control") == "no-cache"
            text = await resp.text()
            assert "ds-instructor-strip" in text
        finally:
            await client.close()


# ══════════════ 4. Route snapshot: API surface unchanged ═════════════════

# BINDING (plan §1.2): both UIs hit the same endpoints; no endpoint may be
# removed, renamed or re-methoded. Page routes are additive.
EXPECTED_ROUTES = {
    ("GET", "/"),
    ("GET", "/classic"),
    ("GET", "/modern"),
    ("GET", "/sim"),
    ("GET", "/api/status"),
    ("GET", "/api/config"),
    ("GET", "/api/diagnostics"),
    ("GET", "/api/pass-rollups"),   # G14 end-of-pass RF rollups (additive)
    ("POST", "/api/channel"),
    ("POST", "/api/pipeline"),
    ("POST", "/api/link"),
    ("GET", "/static"),          # package static dir
    ("GET", "/static/shared"),   # shared design system (smo-common)
}


class TestRouteSnapshot:
    def test_route_snapshot(self):
        app = build_app(RadioFrontend())
        actual = {(r.method, r.resource.canonical.rstrip("/") or "/")
                  for r in app.router.routes()
                  if r.method in ("GET", "POST")}
        assert actual == EXPECTED_ROUTES

    async def test_control_endpoints_still_work(self):
        """The relocated instructor controls drive the same control plane."""
        class StubControl:
            def __init__(self):
                self.channel_payload = None

            def apply_channel(self, payload):
                self.channel_payload = payload
                return {"channel": payload}

            def apply_pipeline(self, payload):
                return {"pipeline": payload}

            def apply_link(self, payload):
                return {"link": payload}

        stub = StubControl()
        client = await _client(control=stub)
        try:
            resp = await client.post("/api/channel",
                                     json={"eb_n0_db": 3.0,
                                           "fading_enabled": True})
            assert resp.status == 200
            assert (await resp.json())["ok"] is True
            assert stub.channel_payload == {"eb_n0_db": 3.0,
                                            "fading_enabled": True}
        finally:
            await client.close()

    async def test_control_unavailable_is_503_as_before(self):
        client = await _client(control=None)
        try:
            resp = await client.post("/api/channel", json={"eb_n0_db": 3.0})
            assert resp.status == 503
        finally:
            await client.close()


# ══════════════ 5. Effects without cause ═════════════════════════════════

class TestEffectsWithoutCause:
    async def test_injected_impairment_effects_reach_operator_feed(self):
        """A degraded link shows its EFFECTS (low Eb/N0, high BER) in the
        status feed the operator page renders — while the operator page
        (gate above) never names the CAUSE."""
        fe = RadioFrontend()
        fe.update_rf(1.0)          # heavily impaired link
        client = TestClient(TestServer(build_app(fe)))
        await client.start_server()
        try:
            d = await (await client.get("/api/status")).json()
            assert d["eb_n0_db"] == 1.0
            assert d["ber_log10"] > -3          # visibly noisy
            # The payload still carries the truth fields for /sim and
            # /classic (API rule: nothing removed) …
            assert "phase_noise_enabled" in d
            # … but the operator page never renders them (grep-gate above).
        finally:
            await client.close()
