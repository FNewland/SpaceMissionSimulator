"""Procedure cards, per position, with the position's own steps highlighted.

Field report 2026-08-20: "could the MAN page display the procedure cards per
position with the specific steps to be executed by that position highlighted
somehow (so this changes by position)?"

Nothing in configuration attributes a procedure STEP to a position — only whole
procedures carry ``required_positions`` / ``position_roles``. So the per-step
highlight is derived, by joining each command step's ``func_id`` against the
commanding authority in ``positions.yaml``. These tests pin that the derivation
is sound, that it stays silent where it cannot be, and — the strongest check —
that it independently reproduces the human-authored role text.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


@pytest.fixture
async def client() -> TestClient:
    server = _make_server()
    server._running = False
    app = web.Application()
    app.router.add_get("/api/procedure/cards", server._handle_proc_cards)
    cli = TestClient(TestServer(app))
    await cli.start_server()
    yield cli
    await cli.close()


async def _cards(client, position: str) -> dict:
    resp = await client.get(f"/api/procedure/cards?position={position}")
    assert resp.status == 200
    body = await resp.json()
    return {c["id"]: c for c in body["cards"]}


class TestWholeProcedureAttribution:
    async def test_relevance_follows_required_positions(self, client):
        ttc = await _cards(client, "ttc")
        assert ttc["LEOP-001"]["relevant"] is True      # ttc is required
        relevant = [c for c in ttc.values() if c["relevant"]]
        assert 0 < len(relevant) < len(ttc), (
            "every card relevant to every position is not filtering at all")

    async def test_the_flight_director_sees_every_card(self, client):
        fd = await _cards(client, "flight_director")
        assert all(c["relevant"] for c in fd.values())

    async def test_the_authored_role_is_passed_through_verbatim(self, client):
        fdir = await _cards(client, "fdir_systems")
        assert fdir["LEOP-001"]["my_role"] == (
            "Issue OBC_BOOT_APP, verify sw_image transition")


class TestPerStepAttribution:
    async def test_the_derived_highlight_matches_the_authored_role(self, client):
        """The check that makes the derivation trustworthy.

        LEOP-001's authored role for fdir_systems is "Issue OBC_BOOT_APP...".
        Nothing in the derivation reads that sentence — it joins func_id 55
        against positions.yaml. Arriving at the same answer independently is
        what says the join means something.
        """
        fdir = await _cards(client, "fdir_systems")
        mine = [s["mnemonic"] for s in fdir["LEOP-001"]["steps"] if s["mine"]]
        assert "OBC_BOOT_APP" in mine

        ttc = await _cards(client, "ttc")
        ttc_mine = [s["mnemonic"] for s in ttc["LEOP-001"]["steps"] if s["mine"]]
        assert any(m.startswith("TTC_") for m in ttc_mine), ttc_mine
        # ...and the two positions do NOT highlight the same steps
        assert "OBC_BOOT_APP" not in ttc_mine

    async def test_service_level_authority_is_not_treated_as_ownership(self, client):
        """Every position may send S17 and S3. Owning them attributes nothing.

        Before this rule, CONNECTION_TEST (S17,1) came back owned by all six
        non-FD positions and nearly every step of every card was highlighted.
        A highlight that is always on is not a highlight.
        """
        ttc = await _cards(client, "ttc")
        for card in ttc.values():
            for step in card["steps"]:
                if step["mnemonic"] == "CONNECTION_TEST":
                    assert step["owners"] == []
                    assert step["mine"] is False
                    assert step["attribution"] == "none"

    async def test_non_command_steps_are_never_attributed(self, client):
        ttc = await _cards(client, "ttc")
        for card in ttc.values():
            for step in card["steps"]:
                if step["type"] in ("go_nogo", "tlm_check"):
                    assert step["owners"] == []
                    assert step["mine"] is False

    async def test_the_flight_director_owns_no_step_in_particular(self, client):
        """FD may send anything, so counting FD as an owner would highlight
        every step and tell the Flight Director nothing."""
        fd = await _cards(client, "flight_director")
        assert all(c["my_step_count"] == 0 for c in fd.values())
        assert not any("flight_director" in s["owners"]
                       for c in fd.values() for s in c["steps"])

    async def test_attribution_grade_is_declared(self, client):
        resp = await client.get("/api/procedure/cards?position=ttc")
        body = await resp.json()
        assert "commanding authority" in body["step_attribution"]
        ttc = {c["id"]: c for c in body["cards"]}
        for step in ttc["LEOP-001"]["steps"]:
            assert step["attribution"] in ("func_id", "none")
            assert (step["attribution"] == "func_id") == bool(step["owners"])


class TestItActuallyChangesByPosition:
    async def test_the_same_card_highlights_differently_per_position(self, client):
        seen = {}
        for pos in ("ttc", "fdir_systems", "eps_tcs"):
            cards = await _cards(client, pos)
            seen[pos] = tuple(sorted(
                s["index"] for s in cards["LEOP-001"]["steps"] if s["mine"]))
        assert len(set(seen.values())) == len(seen), (
            f"positions highlight identical steps, so the card does not "
            f"change by position: {seen}")
