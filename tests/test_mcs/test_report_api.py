"""Report generator HTTP API — the routes the MCS mounts (Phase F §A).

Mounted on a throwaway app exactly as the MCS does in ``server.start()``
(the ``register_dump_routes`` pattern), over a synthetic dump store with
known content, so the round trip template → generate → list → download is
asserted without booting the full ground segment.
"""
from __future__ import annotations

import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.protocol.ecss_packet import datetime_to_cuc_seconds  # noqa: E402
from smo_common.reporting import register_report_routes  # noqa: E402
from smo_common.telemetry.tm_builder import TMBuilder  # noqa: E402

CONFIG_DIR = REPO / "configs" / "eosat1"
# `datetime_to_cuc` now returns (coarse, fine); this test does arithmetic
# on the value, so it wants the full-precision scalar form.
T0_CUC = datetime_to_cuc_seconds(datetime(2026, 3, 10, 4, 0, 0))
PERIOD = {"kind": "absolute", "start": "2026-03-10T04:00:00Z",
          "end": "2026-03-10T04:02:00Z"}


def _write_synthetic_dump(dump_dir: Path) -> None:
    import yaml
    hk = yaml.safe_load((CONFIG_DIR / "telemetry" / "hk_structures.yaml")
                        .read_text())
    sid1_def = next(s for s in hk["structures"] if s["sid"] == 1)
    sid1 = [(p["param_id"] if isinstance(p["param_id"], int)
             else int(str(p["param_id"]), 0),
             p.get("pack_format", "H"), float(p.get("scale", 1)))
            for p in sid1_def["parameters"]]
    cur = {"t": T0_CUC}
    b = TMBuilder(apid=1, time_source=lambda: cur["t"])
    pkts = []
    for i in range(60):
        cur["t"] = T0_CUC + i
        pkts.append(b.build_hk_packet(
            1, {0x0100: 27.0, 0x0101: 80.0, 0x0105: 28.0}, sid1))
    cur["t"] = T0_CUC + 30
    pkts.append(b.build_event_packet(0x0102, 3, "SAFE MODE ENTRY", {}))
    dump_dir.mkdir(parents=True, exist_ok=True)
    # wall-clock name deliberately unrelated to the CUC content
    with (dump_dir / "dump_sid01_19990101T000000Z.bin").open("wb") as f:
        for p in pkts:
            f.write(struct.pack(">I", len(p)))
            f.write(p)


@pytest.fixture()
def report_env(tmp_path):
    dump_dir = tmp_path / "dumps"
    _write_synthetic_dump(dump_dir)
    reports_dir = tmp_path / "reports"
    app = web.Application()
    register_report_routes(app, lambda: dump_dir, CONFIG_DIR,
                           reports_dir=reports_dir, owner="MCS")
    return app, reports_dir


async def test_templates_lists_the_three_shipped(report_env):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/reports/templates")
        assert r.status == 200
        ids = {t["id"] for t in (await r.json())["templates"]}
        assert {"pass_report", "daily_ops_review",
                "anomaly_investigation"} <= ids


async def test_generate_list_download_round_trip(report_env):
    app, reports_dir = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/reports/generate", json={
            "template": "pass_report", "period": PERIOD})
        assert r.status == 200, await r.text()
        meta = await r.json()
        assert meta["report_id"] == "pass_report"
        assert meta["packets"] == 61
        assert (reports_dir / meta["filename"]).is_file()

        r = await client.get("/api/reports")
        listing = await r.json()
        assert listing["count"] == 1
        assert listing["reports"][0]["filename"] == meta["filename"]

        r = await client.get("/api/reports/file",
                             params={"name": meta["filename"]})
        assert r.status == 200
        html = await r.text()
        assert "EOSAT-1 Pass Report" in html
        assert "SAFE MODE ENTRY" in html
        assert "data:image/png;base64," in html


async def test_generation_is_cuc_based_not_filename(report_env):
    """The dump filename says 1999; a 1999 window must select nothing."""
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/reports/generate", json={
            "template": "pass_report",
            "period": {"kind": "absolute",
                       "start": "1999-01-01T00:00:00Z",
                       "end": "1999-01-01T01:00:00Z"}})
        assert r.status == 200
        meta = await r.json()
        # dumps were decoded (packets counted) but the 1999 period holds none
        assert meta["packets"] == 61
        r2 = await client.get("/api/reports/file",
                              params={"name": meta["filename"]})
        html = await r2.text()
        assert "No housekeeping packets" in html or "no data" in html.lower()


@pytest.mark.parametrize("name", [
    "../../../etc/passwd", "..%2Fetc%2Fpasswd", "/etc/passwd",
    "x/../../y.html", "nul\x00l.html",
])
async def test_download_rejects_path_traversal(report_env, name):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/reports/file", params={"name": name})
        assert r.status == 400


async def test_unknown_template_is_404(report_env):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/reports/generate", json={
            "template": "no_such_template", "period": PERIOD})
        assert r.status == 404


async def test_validate_endpoint_reports_spec_paths(report_env):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.post("/api/reports/validate", json={
            "spec_yaml": "schema_version: 1\n"
                         "report: {id: x, title: X, bogus: 1}\n"})
        d = await r.json()
        assert d["ok"] is False
        assert any("bogus" in e["path"] for e in d["errors"])


async def test_reports_page_served(report_env):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/reports")
        assert r.status == 200
        body = await r.text()
        assert "DELAYED-TM REPORTS" in body
        assert "Generate report" in body


# ── "matplotlib IS installed" — make the mismatch visible from the MCS ────

async def test_report_listing_names_the_interpreter_it_runs_under(report_env):
    """An operator who has installed matplotlib needs to see WHICH python
    this server is, without reading a server log."""
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/reports")
        d = await r.json()
    p = d["plotting"]
    assert p["executable"] == sys.executable
    assert p["environment"]
    assert "-m pip install" in p["install_command"]
    assert sys.executable in p["install_command"]
    if p["available"]:
        assert p["backend"].lower() == "agg"   # never an interactive backend
        assert p["state"] == "ok"
    else:
        assert p["error"]


async def test_reports_page_renders_the_plotting_status(report_env):
    app, _ = report_env
    async with TestClient(TestServer(app)) as client:
        body = await (await client.get("/reports")).text()
    assert 'id="plotting"' in body
    assert "renderPlotting" in body
    assert "install_command" in body


def test_mounting_the_report_api_logs_the_extra_status_once(tmp_path, caplog):
    """Startup check: say at boot whether this process can draw a plot,
    rather than only failing when a report is finally generated."""
    import logging

    from smo_common.reporting import plotting as _plotting

    _plotting._cached = None
    app = web.Application()
    with caplog.at_level(logging.INFO,
                         logger="smo_common.reporting.api"):
        register_report_routes(app, lambda: tmp_path, CONFIG_DIR,
                               reports_dir=tmp_path / "reports", owner="MCS")
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "MCS" in text
    assert "plotting" in text.lower()
    assert sys.executable in text
