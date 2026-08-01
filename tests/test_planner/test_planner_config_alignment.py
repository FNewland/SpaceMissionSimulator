"""Tests for the planner's own config file and the station alignment check.

Covers (defect #57):
  - configs/eosat1 declares all five ground stations in orbit.yaml
    (Iqaluit, Troll, Svalbard, Inuvik, O'Higgins)
  - planning/planner.yaml loads and validates, and mirrors the same five
  - the alignment check passes for the shipped config
  - the alignment check FAILS LOUDLY when the two configs diverge:
      * station missing from planner.yaml
      * station missing from orbit.yaml
      * geometry mirror outside tolerance
  - alignment.on_divergence=error makes PlannerServer construction fail
  - the CLI prints a report and exits 2 on divergence
  - GET /api/config/alignment serves the report
"""
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from smo_common.config.loader import load_orbit_config
from smo_planner.planner_config import (
    PlannerConfig, PlannerStationConfig,
    load_planner_config, check_station_alignment, format_alignment_report,
    main as alignment_main,
)
from smo_planner.server import PlannerServer


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"

EXPECTED_STATIONS = {"Iqaluit", "Troll", "Svalbard", "Inuvik", "O'Higgins"}


# ── The shipped five-station configuration ─────────────────────────

class TestFiveStationConfig:

    def test_orbit_yaml_has_all_five_stations(self):
        cfg = load_orbit_config(CONFIG_DIR)
        names = {gs.name for gs in cfg.ground_stations}
        assert names == EXPECTED_STATIONS, f"orbit.yaml stations: {names}"

    def test_station_geometry_is_polar_and_masked_at_5deg(self):
        cfg = load_orbit_config(CONFIG_DIR)
        for gs in cfg.ground_stations:
            assert abs(gs.lat_deg) > 60.0, f"{gs.name} is not a polar station"
            assert -180.0 <= gs.lon_deg <= 180.0
            assert gs.min_elevation_deg == 5.0

    def test_planner_yaml_loads_and_validates(self):
        cfg = load_planner_config(CONFIG_DIR)
        assert isinstance(cfg, PlannerConfig)
        assert {s.name for s in cfg.stations} == EXPECTED_STATIONS
        assert cfg.default_plan.start_empty is True
        # budget values are inherited from the spacecraft config, not copied
        assert cfg.budget.storage_capacity_mb is None
        assert cfg.budget.downlink_rate_bps is None

    def test_rf_augmentation_file_mirrors_the_same_five(self):
        data = yaml.safe_load(
            (CONFIG_DIR / "planning" / "ground_stations.yaml").read_text())
        names = {gs["name"] for gs in data["ground_stations"]}
        assert names == EXPECTED_STATIONS

    def test_shipped_config_is_aligned(self):
        planner_cfg = load_planner_config(CONFIG_DIR)
        orbit_cfg = load_orbit_config(CONFIG_DIR)
        report = check_station_alignment(planner_cfg, orbit_cfg.ground_stations)
        assert report["aligned"], report["issues"]
        assert report["orbit_station_count"] == 5
        assert report["planner_station_count"] == 5
        assert all(r["status"] == "ok" for r in report["stations"])


# ── Divergence detection ───────────────────────────────────────────

def _orbit_stations():
    return list(load_orbit_config(CONFIG_DIR).ground_stations)


class TestAlignmentDetectsDivergence:

    def test_station_missing_from_planner_is_reported(self):
        cfg = load_planner_config(CONFIG_DIR)
        cfg.stations = [s for s in cfg.stations if s.name != "Svalbard"]
        report = check_station_alignment(cfg, _orbit_stations())
        assert not report["aligned"]
        assert any("Svalbard" in i and "missing from planner.yaml" in i
                   for i in report["issues"])
        row = next(r for r in report["stations"] if r["name"] == "Svalbard")
        assert row["status"] == "missing_in_planner"

    def test_station_missing_from_orbit_is_reported(self):
        cfg = load_planner_config(CONFIG_DIR)
        cfg.stations.append(PlannerStationConfig(name="Kiruna", priority=10))
        report = check_station_alignment(cfg, _orbit_stations())
        assert not report["aligned"]
        assert any("Kiruna" in i and "absent from orbit.yaml" in i
                   for i in report["issues"])
        row = next(r for r in report["stations"] if r["name"] == "Kiruna")
        assert row["status"] == "missing_in_orbit"

    def test_geometry_mismatch_is_reported_with_the_delta(self):
        cfg = load_planner_config(CONFIG_DIR)
        troll = cfg.station("Troll")
        troll.lat_deg = troll.lat_deg + 0.5     # 55 km away
        report = check_station_alignment(cfg, _orbit_stations())
        assert not report["aligned"]
        assert any("Troll" in i and "lat_deg differs" in i
                   for i in report["issues"])
        row = next(r for r in report["stations"] if r["name"] == "Troll")
        assert row["status"] == "geometry_mismatch"
        assert row["deltas"]["lat_deg"] == pytest.approx(0.5, abs=1e-6)

    def test_tiny_difference_inside_tolerance_is_not_a_divergence(self):
        cfg = load_planner_config(CONFIG_DIR)
        iqaluit = cfg.station("Iqaluit")
        iqaluit.lat_deg = iqaluit.lat_deg + 0.0005   # ~55 m, inside tolerance
        report = check_station_alignment(cfg, _orbit_stations())
        assert report["aligned"], report["issues"]

    def test_omitted_geometry_inherits_and_is_not_a_divergence(self):
        cfg = load_planner_config(CONFIG_DIR)
        for st in cfg.stations:
            st.lat_deg = st.lon_deg = st.alt_km = st.min_elevation_deg = None
        report = check_station_alignment(cfg, _orbit_stations())
        assert report["aligned"], report["issues"]

    def test_report_text_is_renderable(self):
        cfg = load_planner_config(CONFIG_DIR)
        text = format_alignment_report(
            check_station_alignment(cfg, _orbit_stations()))
        for name in EXPECTED_STATIONS:
            assert name in text
        assert "ALIGNED" in text


# ── Load-time validation policy ────────────────────────────────────

class TestLoadTimeValidation:

    def test_server_warns_on_divergence(self, tmp_path, caplog):
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        server._planner_cfg.stations.append(
            PlannerStationConfig(name="Kiruna"))
        with caplog.at_level("WARNING"):
            report = server._check_station_alignment()
        assert not report["aligned"]
        assert any("GROUND-STATION CONFIG DIVERGENCE" in r.message
                   for r in caplog.records)

    def test_on_divergence_error_raises(self):
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        server._planner_cfg.alignment.on_divergence = "error"
        server._planner_cfg.stations.append(PlannerStationConfig(name="Kiruna"))
        with pytest.raises(ValueError, match="diverge from orbit.yaml"):
            server._check_station_alignment()

    def test_shipped_config_constructs_cleanly_and_reports_aligned(self):
        server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
        assert server._alignment_report["aligned"]
        assert len(server._gs_list) == 5


# ── CLI ────────────────────────────────────────────────────────────

class TestAlignmentCLI:

    def test_cli_exit_zero_when_aligned(self, capsys):
        rc = alignment_main(["--config", str(CONFIG_DIR)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "ALIGNED" in out
        assert "Svalbard" in out

    def test_cli_exit_two_when_divergent(self, tmp_path, capsys):
        # Copy the mission config and delete a station from orbit.yaml only.
        import shutil
        cfg_dir = tmp_path / "eosat1"
        shutil.copytree(CONFIG_DIR, cfg_dir)
        orbit = yaml.safe_load((cfg_dir / "orbit.yaml").read_text())
        orbit["ground_stations"] = [
            gs for gs in orbit["ground_stations"] if gs["name"] != "Inuvik"]
        (cfg_dir / "orbit.yaml").write_text(yaml.safe_dump(orbit))

        rc = alignment_main(["--config", str(cfg_dir)])
        out = capsys.readouterr().out
        assert rc == 2
        assert "DIVERGENT" in out
        assert "Inuvik" in out

    def test_cli_json_mode(self, capsys):
        import json
        rc = alignment_main(["--config", str(CONFIG_DIR), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["aligned"] is True
        assert payload["orbit_station_count"] == 5


# ── Endpoint ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alignment_endpoint_serves_the_report():
    server = PlannerServer(CONFIG_DIR, http_port=0, time_source="real")
    app = web.Application()
    app.router.add_get("/api/config/alignment", server._handle_alignment)
    app.router.add_get("/api/config/planner", server._handle_planner_config)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.get("/api/config/alignment")
        assert resp.status == 200
        data = await resp.json()
        assert data["aligned"] is True
        assert {r["name"] for r in data["stations"]} == EXPECTED_STATIONS
        assert "ALIGNED" in data["text"]

        resp = await client.get("/api/config/planner")
        data = await resp.json()
        assert len(data["planner_config"]["stations"]) == 5
        # Budget values must come from the spacecraft config, and say so.
        assert data["budget_params"]["storage_capacity_mb"] == 20000.0
        assert "payload.yaml" in data["budget_sources"]["storage_capacity_mb"]
    finally:
        await client.close()
