"""Fault-graph endpoint (POSITION_NEEDS G6).

GET /api/fault-graph serves the parsed fdir/fault_propagation.yaml so the
alarm drawer can group first-out roots client-side.
"""
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_mcs.fault_graph import load_fault_graph
from smo_mcs.server import MCSServer

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


class TestLoadFaultGraph:
    def test_nodes_match_config(self):
        g = load_fault_graph(CONFIG_DIR)
        assert g["available"] is True
        raw = yaml.safe_load(
            (CONFIG_DIR / "fdir" / "fault_propagation.yaml").read_text())
        cfg_ids = {f["fault_id"] for f in raw["fault_cascades"]}
        assert {n["id"] for n in g["nodes"]} == cfg_ids
        # Every node maps to the param its detection rides on.
        for n in g["nodes"]:
            assert n["param"], n["id"]
            assert n["subsystem"] == n["param"].split(".")[0]

    def test_param_ids_resolved_from_catalogue(self):
        params = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
        ids = {p["name"]: (p["id"] if isinstance(p["id"], int)
                           else int(str(p["id"]), 0))
               for p in params["parameters"]}
        g = load_fault_graph(CONFIG_DIR, ids)
        node = next(n for n in g["nodes"] if n["id"] == "eps_undervoltage")
        assert node["param"] == "eps.bus_voltage"
        assert node["param_id"] == f"0x{ids['eps.bus_voltage']:04X}"

    def test_cascades_are_raw_config(self):
        g = load_fault_graph(CONFIG_DIR)
        uv = [c for c in g["cascades"] if c["from"] == "eps_undervoltage"]
        assert {c["to_subsystem"] for c in uv} == {"aocs", "payload", "tcs"}
        for c in uv:
            assert c["action"]

    def test_derived_edges_are_parent_child_pairs(self):
        g = load_fault_graph(CONFIG_DIR)
        assert g["edges"], "the cascade config must yield ancestry edges"
        node_ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            assert e["parent"] in node_ids
            assert e["child"] in node_ids
            assert e["parent"] != e["child"]
        # eps_undervoltage cascades into aocs, so every aocs-parameter fault
        # is a potential child (first-out grouping: eps root, aocs consequent).
        children = {e["child"] for e in g["edges"]
                    if e["parent"] == "eps_undervoltage"}
        aocs_faults = {n["id"] for n in g["nodes"]
                       if n["subsystem"] == "aocs"}
        assert children & aocs_faults
        # And the semantics are declared, not implied.
        assert "DERIVED" in g["edge_semantics"]

    def test_missing_config_is_honest(self, tmp_path):
        g = load_fault_graph(tmp_path)
        assert g["available"] is False
        assert g["nodes"] == []


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_endpoint_serves_graph(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
        server = MCSServer(config_dir=str(CONFIG_DIR), http_port=0)
        app = web.Application()
        app.router.add_get("/api/fault-graph", server._handle_fault_graph)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/fault-graph")
            assert resp.status == 200
            data = await resp.json()
            assert data["available"] is True
            assert data["source"] == "fdir/fault_propagation.yaml"
            assert data["nodes"] and data["edges"] and data["cascades"]
            # param ids resolved through the server's own catalogue.
            n = next(x for x in data["nodes"]
                     if x["id"] == "eps_undervoltage")
            assert n["param_id"] == "0x0105"
        finally:
            await client.close()
