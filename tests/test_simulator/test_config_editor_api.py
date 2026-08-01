"""Config editor HTTP API on the simulator instructor server (Phase F §B).

The instructor server (8080) is the ONLY host that mounts the write
surface; the MCS/planner mount the same routes readonly. Covered here:

* the instructor app exposes the full CRUD API and the /config page;
* a valid edit round-trips validate → apply → history with SMO-* trailers;
* invalid edits (schema / cross-file) are 422 with structured issues and
  the file on disk is untouched;
* must-not-edit files are 403;
* revert and baselines work over HTTP;
* an active scenario blocks apply (409) unless forced with a reason;
* a readonly mount (what the MCS and planner do) has NO mutating routes —
  they are absent (404/405), not merely disabled.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

REPO = Path(__file__).resolve().parents[2]
for pkg in ("smo-common", "smo-simulator"):
    src = REPO / "packages" / pkg / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from smo_common.confedit import register_config_routes  # noqa: E402
from smo_common.confedit.editor import ConfigEditor  # noqa: E402

CONFIG_DIR = REPO / "configs" / "eosat1"


class _FakeScenarioEngine:
    def __init__(self):
        self.active = False

    def is_active(self):
        return self.active


def _fake_engine(cfg_dir: Path, dump_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config_dir=cfg_dir,
        _dump_dir=dump_dir,
        _scenario_engine=_FakeScenarioEngine(),
        _breakpoints={},
        get_state_summary=lambda: {},
        get_instructor_snapshot=lambda: {},
        submit_instructor_cmd=lambda cmd: (True, ""),
    )


@pytest.fixture()
def cfg_copy(tmp_path) -> Path:
    cfg = tmp_path / "configs" / "eosat1"
    shutil.copytree(CONFIG_DIR, cfg)
    return cfg


@pytest.fixture()
def instructor_app(cfg_copy, tmp_path):
    """The real instructor app factory over an isolated config copy."""
    from smo_simulator.instructor.app import create_instructor_app
    engine = _fake_engine(cfg_copy, tmp_path / "dumps")
    app = create_instructor_app(engine)
    return app, engine, cfg_copy


async def test_instructor_full_crud_round_trip(instructor_app):
    app, _engine, cfg = instructor_app
    async with TestClient(TestServer(app)) as client:
        # inventory + classification
        r = await client.get("/api/config/files")
        assert r.status == 200
        d = await r.json()
        assert d["readonly"] is False
        by_path = {f["path"]: f for f in d["files"]}
        assert by_path["subsystems/memory_map.yaml"]["editable"] is False
        assert by_path["subsystems/prop.yaml"]["editable"] is True
        assert by_path["simulation.yaml"]["restart_scope"] == ["simulator"]

        # read a file
        r = await client.get("/api/config/file",
                             params={"path": "scenarios/nominal_ops.yaml"})
        assert r.status == 200
        f = await r.json()
        orig = f["text"]
        new = orig.replace("duration_s: 1800", "duration_s: 2400")
        assert new != orig

        # validate the proposed edit
        r = await client.post("/api/config/validate", json={
            "path": "scenarios/nominal_ops.yaml", "text": new})
        assert (await r.json())["ok"] is True

        # diff preview
        r = await client.post("/api/config/diff", json={
            "path": "scenarios/nominal_ops.yaml", "text": new})
        dd = await r.json()
        assert "+" in dd["text_diff"]
        assert dd["restart_scope"] == ["simulator"]

        # apply
        r = await client.put("/api/config/file",
                             params={"path": "scenarios/nominal_ops.yaml"},
                             json={"text": new, "message": "longer run",
                                   "reason": "exercise prep",
                                   "actor": "franz"})
        assert r.status == 200, await r.text()
        res = await r.json()
        assert res["revision"]
        assert res["restart_required"] == ["simulator"]
        assert "duration_s: 2400" in (cfg / "scenarios" /
                                      "nominal_ops.yaml").read_text()

        # history carries the trailers
        r = await client.get("/api/config/history",
                             params={"path": "scenarios/nominal_ops.yaml"})
        hist = (await r.json())["revisions"]
        assert hist[0]["trailers"]["Actor"] == "franz"
        assert hist[0]["trailers"]["Reason"] == "exercise prep"

        # revert to the initial baseline
        base = hist[-1]["rev"]
        r = await client.post("/api/config/revert", json={
            "rev": base, "path": "scenarios/nominal_ops.yaml",
            "reason": "undo"})
        assert r.status == 200
        assert (cfg / "scenarios" / "nominal_ops.yaml").read_text() == orig


async def test_invalid_edit_is_422_and_untouched(instructor_app):
    app, _e, cfg = instructor_app
    path = "subsystems/ttc.yaml"
    before = (cfg / path).read_text()
    bad = before.replace("dl_freq_mhz: 2200.0", "dl_freq_mhz: 2300.0")
    async with TestClient(TestServer(app)) as client:
        r = await client.put("/api/config/file", params={"path": path},
                             json={"text": bad, "message": "detune"})
        assert r.status == 422
        d = await r.json()
        assert any(i.get("check") == "rf.frequencies" for i in d["issues"])
    assert (cfg / path).read_text() == before


async def test_must_not_edit_rejected_403(instructor_app):
    app, _e, cfg = instructor_app
    before = (cfg / "subsystems" / "memory_map.yaml").read_text()
    async with TestClient(TestServer(app)) as client:
        r = await client.put("/api/config/file",
                             params={"path": "subsystems/memory_map.yaml"},
                             json={"text": "x: 1", "message": "no"})
        assert r.status == 403
        body = await r.json()
        assert "must not be edited" in body["error"]
    assert (cfg / "subsystems" / "memory_map.yaml").read_text() == before


async def test_active_scenario_blocks_apply(instructor_app):
    app, engine, cfg = instructor_app
    engine._scenario_engine.active = True
    path = "scenarios/eps_anomaly.yaml"
    text = (cfg / path).read_text() + "\n# note\n"
    async with TestClient(TestServer(app)) as client:
        r = await client.put("/api/config/file", params={"path": path},
                             json={"text": text, "message": "m"})
        assert r.status == 409
        # forced, with a reason, it goes through
        r = await client.put("/api/config/file", params={"path": path},
                             json={"text": text, "message": "m",
                                   "reason": "instructor override",
                                   "force": True})
        assert r.status == 200


async def test_baselines_over_http(instructor_app):
    app, _e, cfg = instructor_app
    path = "scenarios/nominal_ops.yaml"
    orig = (cfg / path).read_text()
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/config/baselines",
                              json={"name": "class-start",
                                    "description": "as shipped"})
        assert r.status == 200
        r = await client.put("/api/config/file", params={"path": path},
                             json={"text": orig.replace(
                                 "duration_s: 1800", "duration_s: 999"),
                                 "message": "diverge"})
        assert r.status == 200
        r = await client.post("/api/config/baselines/class-start/apply",
                              json={"reason": "reset"})
        assert r.status == 200
        d = await r.json()
        assert d["restart_required"]
    assert (cfg / path).read_text() == orig


async def test_config_page_served_on_instructor(instructor_app):
    app, _e, _c = instructor_app
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/config")
        assert r.status == 200
        body = await r.text()
        assert "MISSION CONFIGURATION" in body


# ── readonly mounts (what the MCS and planner do) ────────────────────────

@pytest.fixture()
def readonly_app(cfg_copy, tmp_path):
    app = web.Application()
    editor = ConfigEditor(cfg_copy, history_dir=tmp_path / "hist")
    register_config_routes(app, editor, readonly=True, owner="MCS")
    return app, cfg_copy


async def test_readonly_serves_reads(readonly_app):
    app, _cfg = readonly_app
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/config/files")
        assert r.status == 200
        assert (await r.json())["readonly"] is True
        r = await client.get("/api/config/file",
                             params={"path": "mission.yaml"})
        assert r.status == 200
        r = await client.get("/api/config/history")
        assert r.status == 200
        r = await client.get("/api/config/crosschecks")
        assert r.status == 200
        r = await client.get("/config")
        assert r.status == 200
        assert "READ-ONLY" in await r.text()


async def test_readonly_has_no_write_routes(readonly_app):
    """Students are blocked by construction: the mutating routes are not
    registered at all on the MCS/planner mounts."""
    app, cfg = readonly_app
    before = (cfg / "scenarios" / "nominal_ops.yaml").read_text()
    async with TestClient(TestServer(app)) as client:
        r = await client.put("/api/config/file",
                             params={"path": "scenarios/nominal_ops.yaml"},
                             json={"text": "x: 1", "message": "hack"})
        assert r.status in (404, 405)
        r = await client.post("/api/config/revert",
                              json={"rev": "HEAD", "path": "mission.yaml"})
        assert r.status in (404, 405)
        r = await client.post("/api/config/baselines", json={"name": "x"})
        assert r.status in (404, 405)
        r = await client.post("/api/config/baselines/x/apply", json={})
        assert r.status in (404, 405)
    assert (cfg / "scenarios" / "nominal_ops.yaml").read_text() == before
