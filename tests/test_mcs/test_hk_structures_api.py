"""Defect #68 — the HK SID table must come from the configuration.

The MCS HK Manager used to be driven by a hardcoded `hkSids` array in
index.html that was wrong in every column of every row: SIDs 4 and 6 had their
names swapped, SID 7 was invented, SID 11 (the bootloader beacon) was missing,
and every interval and parameter count was wrong.

`GET /api/hk-structures` now serves the definitions the MCS already loads from
`configs/eosat1/telemetry/hk_structures.yaml` — the same file the simulator
packs its HK from. These tests pin the endpoint against that file so the two
can never diverge again, and cover the observed (rather than assumed) enable
state that replaced the old always-green LEDs.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
HK_YAML = CONFIG_DIR / "telemetry" / "hk_structures.yaml"


@pytest.fixture(scope="module")
def hk_config() -> dict[int, dict]:
    data = yaml.safe_load(HK_YAML.read_text())
    return {int(s["sid"]): s for s in data["structures"]}


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        return MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                         connect_port=9999, http_port=0)


def _build_app(server: MCSServer) -> web.Application:
    """Just the routes these tests exercise (MCSServer builds its app in start())."""
    app = web.Application()
    app.router.add_get("/api/hk-structures", server._handle_hk_structures)
    app.router.add_get("/api/tc-catalog", server._handle_tc_catalog)
    return app


@pytest.fixture
async def mcs():
    """Yield (server, client) so tests can poke server state directly."""
    server = _make_server()
    server._running = False
    cli = TestClient(TestServer(_build_app(server)))
    await cli.start_server()
    yield server, cli
    await cli.close()


@pytest.fixture
async def client(mcs) -> TestClient:
    return mcs[1]


# ── the table matches the configuration ─────────────────────────────

class TestHkStructuresEndpoint:

    async def test_endpoint_returns_every_configured_sid_and_nothing_else(
            self, client, hk_config):
        resp = await client.get("/api/hk-structures")
        assert resp.status == 200
        body = await resp.json()
        sids = {s["sid"] for s in body["structures"]}
        assert sids == set(hk_config), (
            "the served SID list must be exactly what hk_structures.yaml defines "
            "— no phantom SID 7, no missing SID 11"
        )

    async def test_names_intervals_and_param_counts_match_the_config(
            self, client, hk_config):
        body = await (await client.get("/api/hk-structures")).json()
        for entry in body["structures"]:
            cfg = hk_config[entry["sid"]]
            assert entry["name"] == cfg["name"], f"SID {entry['sid']} name"
            assert entry["interval_s"] == pytest.approx(float(cfg["interval_s"]))
            assert entry["param_count"] == len(cfg["parameters"])

    async def test_sid_4_is_platform_and_sid_6_is_ttc(self, client):
        """The old UI had these two swapped — 4 was labelled TTC, 6 was OBDH."""
        body = await (await client.get("/api/hk-structures")).json()
        by_sid = {s["sid"]: s for s in body["structures"]}
        assert by_sid[4]["name"] == "Platform"
        assert by_sid[6]["name"] == "TTC"

    async def test_beacon_sid_11_is_present_readonly_and_on_the_bootloader_apid(
            self, client):
        body = await (await client.get("/api/hk-structures")).json()
        by_sid = {s["sid"]: s for s in body["structures"]}
        assert 11 in by_sid, "SID 11 (Beacon) must be listed"
        beacon = by_sid[11]
        assert beacon["name"] == "Beacon"
        assert beacon["apid"] == body["bootloader_apid"] == 2
        # 2026-08b (ruling R2): 7, not 6. SID 11 is now carried by a real
        # separate radio — the UHF beacon transmitter — and gained
        # ttc.pa_on (0x0516) as its seventh parameter, so the one packet that
        # still arrives when the S-band chain is silent says whether the PA is
        # the reason for the silence. This also settles a documentation split:
        # commissioning_sequence_walkthrough.md:44 has said "7 parameters"
        # against the config's 6 since the 2026-07 display audit.
        assert beacon["param_count"] == 7
        assert beacon["interval_s"] == pytest.approx(30.0)
        assert beacon["readonly"] is True
        assert beacon["note"], "the read-only row needs an explanatory state"

    async def test_non_beacon_sids_are_on_the_application_apid_and_commandable(
            self, client):
        body = await (await client.get("/api/hk-structures")).json()
        for entry in body["structures"]:
            if entry["sid"] == 11:
                continue
            assert entry["apid"] == body["spacecraft_apid"] == 1
            assert entry["readonly"] is False


# ── observed, not assumed, enable state ─────────────────────────────

class TestObservedEnableState:

    async def test_never_seen_before_any_telemetry(self, client):
        body = await (await client.get("/api/hk-structures")).json()
        for entry in body["structures"]:
            assert entry["observed"] == "never_seen"
            assert entry["last_seen_age_s"] is None
            # `None` means "the spacecraft never told us" — the old UI simply
            # asserted True for every SID, forever.
            assert entry["enabled"] is None

    async def test_recently_seen_sid_reports_periodic(self, mcs):
        import time as _t
        server, client = mcs
        server._sid_last_seen[1] = _t.time()
        body = await (await client.get("/api/hk-structures")).json()
        by_sid = {s["sid"]: s for s in body["structures"]}
        assert by_sid[1]["observed"] == "periodic"
        assert by_sid[1]["last_seen_age_s"] is not None

    async def test_long_silent_sid_reports_silent(self, mcs):
        import time as _t
        server, client = mcs
        server._sid_last_seen[3] = _t.time() - 10_000.0   # SID 3 interval is 60 s
        body = await (await client.get("/api/hk-structures")).json()
        by_sid = {s["sid"]: s for s in body["structures"]}
        assert by_sid[3]["observed"] == "silent"

    async def test_reported_enable_map_is_used_when_offered(self, mcs):
        server, client = mcs
        server._ingest_reported_hk_enabled({"1": False, 2: True})
        body = await (await client.get("/api/hk-structures")).json()
        by_sid = {s["sid"]: s for s in body["structures"]}
        assert by_sid[1]["enabled"] is False
        assert by_sid[2]["enabled"] is True
        assert by_sid[3]["enabled"] is None        # not reported


class TestIngestReportedHkEnabled:
    """Optional sim-side feed — malformed input must never break the display."""

    def test_accepts_a_mapping(self):
        srv = _make_server()
        srv._ingest_reported_hk_enabled({1: True, "2": 0})
        assert srv._sid_enabled_reported == {1: True, 2: False}

    def test_accepts_a_list_of_dicts(self):
        srv = _make_server()
        srv._ingest_reported_hk_enabled([{"sid": 4, "enabled": True}])
        assert srv._sid_enabled_reported == {4: True}

    @pytest.mark.parametrize("bad", [None, {}, [], "nonsense", 17])
    def test_garbage_is_ignored(self, bad):
        srv = _make_server()
        srv._ingest_reported_hk_enabled(bad)
        assert srv._sid_enabled_reported == {}

    def test_unparseable_sid_is_skipped_without_dropping_the_rest(self):
        srv = _make_server()
        srv._ingest_reported_hk_enabled({"not-a-sid": True, 5: True})
        assert srv._sid_enabled_reported == {5: True}


# ── /api/tc-catalog now carries derived lengths (defect #67) ────────

class TestTcCatalogLengths:

    async def test_catalog_entries_expose_expected_data_bytes(self, client):
        body = await (await client.get("/api/tc-catalog")).json()
        commands = body["commands"]
        assert commands, "tc catalogue is empty"
        for cmd in commands:
            assert "expected_data_bytes" in cmd
            assert "variable_length" in cmd
            assert "min_data_bytes" in cmd
            assert "length_note" in cmd

    async def test_a_representative_command_has_the_right_length(self, client):
        body = await (await client.get("/api/tc-catalog")).json()
        by_name = {c["name"]: c for c in body["commands"]}
        assert by_name["HK_ENABLE"]["expected_data_bytes"] == 2
        assert by_name["S2_EPS_BATTERY_HEATER"]["expected_data_bytes"] == 3

    async def test_bytes_typed_commands_report_variable_not_a_number(self, client):
        """Identified by shape — the catalogue is maintained independently."""
        body = await (await client.get("/api/tc-catalog")).json()
        variable = [c for c in body["commands"]
                    if any(f.get("type") in ("bytes", "string", "blob")
                           for f in (c.get("fields") or []))]
        assert variable, "expected at least one bytes-typed command"
        for cmd in variable:
            assert cmd["expected_data_bytes"] is None, cmd["name"]
            assert cmd["variable_length"] is True, cmd["name"]
            assert cmd["min_data_bytes"] >= 0

    async def test_commands_with_no_fields_key_report_undeclared(self, client):
        """`TCCommandDef` defaults `fields` to [] — the server must not let
        that default masquerade as "this command takes zero data bytes"."""
        raw = yaml.safe_load(
            (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        cmds = raw["commands"] if isinstance(raw, dict) else raw
        undeclared = {c["name"] for c in cmds if "fields" not in c}
        if not undeclared:
            pytest.skip("every catalogue entry now declares `fields`")
        body = await (await client.get("/api/tc-catalog")).json()
        by_name = {c["name"]: c for c in body["commands"]}
        for name in undeclared:
            assert by_name[name]["expected_data_bytes"] is None, name
            assert "no 'fields' key" in by_name[name]["length_note"], name

    async def test_every_s8_function_is_available_to_the_builder(self, client):
        """The hardcoded FUNC_CATALOG only had 79 of the 91 func_ids."""
        raw = yaml.safe_load(
            (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())
        cmds = raw["commands"] if isinstance(raw, dict) else raw
        configured = {c["func_id"] for c in cmds if c.get("func_id") is not None}
        body = await (await client.get("/api/tc-catalog")).json()
        served = {c["func_id"] for c in body["commands"]
                  if c.get("service") == 8 and c.get("subtype") == 1
                  and c.get("func_id") is not None}
        assert served == configured
        assert len(served) >= 91
        # The twelve the operator could not previously select.
        for missing in (42, 81, 82, 84, 100, 101, 102, 103, 104, 105, 106, 107):
            assert missing in served
