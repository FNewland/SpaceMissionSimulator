"""Franz: "We cannot see the contents of the S12s in delayed TM dumps."

He was right, on all three ground-side paths at once:

* ``dump_store.summarise_dump`` (which feeds ``/api/dumps/summary`` and the
  ``/dumps`` page) produced ``packet_counts`` only, so four limit violations
  rendered as ``{"service": 12, "subtype": 9, "count": 4}`` in a ``<pre>``;
* ``tools/tm_report.py`` — which ``tools/delayed_tm_viewer.py`` delegates to —
  handled S3.25 and S5 and had no ``elif svc == 12`` branch at all;
* the real-time path, ``tm_processor._process_monitoring``, unpacked only the
  historical 6-byte prefix and discarded ``mon_id``, ``check_type``,
  ``severity`` and both limits.

Nothing needed writing from scratch: correct decoders already existed in
``smo_common.reporting.decode`` (and ``smo_mcs.pus_reports``), and
``reporting.dataset`` already ingested S12 out of these very files — so
``/api/reports`` could show a dump's limit violations while ``/dumps`` could
not. These tests close that inconsistency and keep it closed.

S12 is now routed to the on-board **Alarm store** (4) by
``tm_storage.SERVICE_TO_STORE``, so an alarm dump is mostly S12.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from smo_common.dump_store import register_dump_routes, summarise_dump  # noqa: E402
from smo_common.protocol.ecss_packet import build_tm_packet  # noqa: E402

CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"


def _s12_transition(param_id: int, value: float, *, entered: bool, mon_id: int,
                    check_type: int = 0, severity: int = 3,
                    low: float = 20.0, high: float = 100.0,
                    cuc: int = 800_000_000) -> bytes:
    """One TM(12,9)/(12,10), packed exactly as `service_dispatch` packs it."""
    data = struct.pack(">HfHBBff", param_id, value, mon_id, check_type,
                       severity, low, high)
    return build_tm_packet(apid=1, service=12, subtype=9 if entered else 10,
                        data=data, cuc_time=cuc)


def _s12_definitions(rows: list[tuple], cuc: int = 800_000_000) -> bytes:
    """One TM(12,13) definition table."""
    body = struct.pack(">H", len(rows))
    for mon_id, param_id, check_type, enabled, low, high in rows:
        body += struct.pack(">HHBBff", mon_id, param_id, check_type,
                            1 if enabled else 0, low, high)
    return build_tm_packet(apid=1, service=12, subtype=13, data=body, cuc_time=cuc)


def _write_dump(dump_dir: Path, name: str, packets: list[bytes]) -> Path:
    dump_dir.mkdir(parents=True, exist_ok=True)
    path = dump_dir / name
    with path.open("wb") as f:
        for pkt in packets:
            f.write(struct.pack(">I", len(pkt)))
            f.write(pkt)
    return path


@pytest.fixture
def alarm_dump(tmp_path) -> Path:
    """An Alarm-store dump: SoC out of limits, then back, plus the table."""
    return _write_dump(tmp_path / "dumps", "dump_sid04_20260812T101500Z.bin", [
        _s12_transition(0x0101, 18.4, entered=True, mon_id=1, severity=2,
                        low=20.0, high=100.0, cuc=800_000_100),
        _s12_transition(0x0105, 26.1, entered=True, mon_id=3, severity=3,
                        low=27.0, high=35.0, cuc=800_000_150),
        _s12_transition(0x0101, 21.9, entered=False, mon_id=1, severity=2,
                        low=20.0, high=100.0, cuc=800_000_400),
        _s12_definitions([(1, 0x0101, 0, True, 20.0, 100.0),
                          (3, 0x0105, 0, True, 27.0, 35.0),
                          (9, 0x0408, 1, False, -25.0, 20.0)]),
    ])


# ── summarise_dump decodes S12 ──────────────────────────────────────

class TestSummariseDumpDecodesS12:

    def test_transitions_carry_every_field_from_the_wire(self, alarm_dump):
        s = summarise_dump(alarm_dump, config_dir=CONFIG_DIR)
        tr = s["s12_transitions"]
        assert len(tr) == 3
        first = tr[0]
        # The six fields that make a limit violation legible, none of which
        # the dump view had before.
        assert first["param_id"] == 0x0101
        assert first["param_hex"] == "0x0101"
        assert first["param_name"] == "eps.bat_soc"     # from the catalogue
        assert first["mon_id"] == 1
        assert first["entered"] is True
        assert first["transition"] == "out_of_limits"
        assert first["value"] == pytest.approx(18.4, abs=1e-4)
        assert first["check_type_name"] == "absolute"
        assert first["severity_name"] == "WARNING"
        assert (first["low_limit"], first["high_limit"]) == (20.0, 100.0)
        assert first["utc"].startswith("2025-") or first["utc"].endswith("+00:00")

    def test_two_monitors_on_one_parameter_stay_distinguishable(self, alarm_dump):
        """The battery carries a WARNING and an ALARM monitor.

        With `mon_id` discarded — as the real-time path used to do — the two
        tiers were indistinguishable on the ground.
        """
        s = summarise_dump(alarm_dump, config_dir=CONFIG_DIR)
        assert {t["mon_id"] for t in s["s12_transitions"]} == {1, 3}

    def test_the_return_to_nominal_is_marked_as_such(self, alarm_dump):
        s = summarise_dump(alarm_dump, config_dir=CONFIG_DIR)
        back = [t for t in s["s12_transitions"] if not t["entered"]]
        assert len(back) == 1
        assert back[0]["transition"] == "back_to_nominal"
        assert s["s12_summary"] == {
            "transitions": 3, "entered": 2, "returned": 1,
            "definitions": 3, "undecodable": 0, "named": True,
        }

    def test_the_definition_table_is_decoded(self, alarm_dump):
        s = summarise_dump(alarm_dump, config_dir=CONFIG_DIR)
        defs = {d["mon_id"]: d for d in s["s12_definitions"]}
        assert set(defs) == {1, 3, 9}
        assert defs[9]["enabled"] is False
        assert defs[9]["check_type_name"] == "delta"
        assert defs[9]["param_name"] == "tcs.temp_fpa"
        assert defs[3]["low_limit"] == pytest.approx(27.0)

    def test_names_are_optional_and_never_fatal(self, alarm_dump):
        """A host with no mission catalogue still gets the numbers."""
        s = summarise_dump(alarm_dump)                    # no config_dir
        assert s["s12_summary"]["named"] is False
        assert s["s12_transitions"][0]["param_name"] == ""
        assert s["s12_transitions"][0]["param_hex"] == "0x0101"

    def test_a_dump_with_no_s12_is_unaffected(self, tmp_path):
        path = _write_dump(tmp_path / "d", "dump_sid01_20260812T101500Z.bin", [
            build_tm_packet(apid=1, service=3, subtype=25,
                         data=struct.pack(">H", 1) + b"\x00" * 8,
                         cuc_time=800_000_000),
        ])
        s = summarise_dump(path, config_dir=CONFIG_DIR)
        assert s["s12_transitions"] == [] and s["s12_definitions"] == []
        assert s["packet_count"] == 1


# ── served over HTTP, and rendered ──────────────────────────────────

class TestDumpApiAndPage:

    @pytest.mark.asyncio
    async def test_summary_endpoint_serves_the_decoded_s12(self, alarm_dump):
        app = web.Application()
        register_dump_routes(app, lambda: alarm_dump.parent, owner="MCS",
                             config_dir=CONFIG_DIR)
        async with TestClient(TestServer(app)) as client:
            body = await (await client.get(
                f"/api/dumps/summary?name={alarm_dump.name}")).json()
        assert len(body["s12_transitions"]) == 3
        assert body["s12_transitions"][0]["param_name"] == "eps.bat_soc"
        assert len(body["s12_definitions"]) == 3

    @pytest.mark.asyncio
    async def test_the_page_renders_a_table_not_raw_json(self, alarm_dump):
        app = web.Application()
        register_dump_routes(app, lambda: alarm_dump.parent, owner="MCS",
                             config_dir=CONFIG_DIR)
        async with TestClient(TestServer(app)) as client:
            page = await (await client.get("/dumps")).text()
        assert "renderS12" in page
        for column in ("TRANSITION", "SEVERITY", "LIMIT BAND", "MON"):
            assert column in page, f"the S12 table must show {column}"
        assert "s12_transitions" in page and "s12_definitions" in page


# ── the offline tools ───────────────────────────────────────────────

class TestOfflineTools:

    def test_tm_report_decodes_and_renders_s12(self, alarm_dump):
        from tm_report import PassAnalysis, load_catalogs, read_dump, render_report
        params_by_id, _, hk_plist = load_catalogs()
        an = PassAnalysis()
        for raw in read_dump(alarm_dump):
            an.ingest_packet(raw, params_by_id, hk_plist)
        assert len(an.s12_transitions) == 3
        assert len(an.s12_definitions) == 3
        html = render_report(alarm_dump, an, params_by_id)
        assert "On-board monitoring (S12)" in html
        assert "eps.bat_soc" in html
        assert "OUT OF LIMITS" in html
        assert "back to nominal" in html
        assert "Monitoring definitions" in html

    def test_the_viewer_exposes_s12_in_its_json_and_page(self, alarm_dump):
        import delayed_tm_viewer as viewer
        from tm_report import load_catalogs
        params_by_id, _, hk_plist = load_catalogs()
        out = viewer.analyse_dumps([alarm_dump], params_by_id, hk_plist)
        assert len(out["s12_transitions"]) == 3
        assert out["s12_transitions"][0]["param_name"] == "eps.bat_soc"
        assert len(out["s12_definitions"]) == 3
        assert 'id="s12"' in viewer.INDEX_HTML


# ── the real-time path keeps every field too ────────────────────────

class TestRealTimeMonitoringPath:

    def test_process_monitoring_keeps_the_whole_report(self):
        from smo_mcs.tm_processor import TMProcessor
        proc = TMProcessor()
        raw = _s12_transition(0x0105, 26.1, entered=True, mon_id=3,
                              severity=3, low=27.0, high=35.0)
        out = proc.process_packet(raw)
        assert out["mon_id"] == 3
        assert out["severity"] == 3 and out["severity_name"] == "ALARM"
        assert out["check_type"] == 0 and out["check_type_name"] == "absolute"
        assert out["low_limit"] == pytest.approx(27.0)
        assert out["high_limit"] == pytest.approx(35.0)
        alarm = proc._alarms[-1]
        assert alarm["mon_id"] == 3
        assert alarm["severity_name"] == "ALARM"
        assert alarm["low_limit"] == pytest.approx(27.0)

    def test_the_historical_six_byte_form_still_parses(self):
        """Older builds emit only param_id + value; do not fake the rest."""
        from smo_mcs.tm_processor import TMProcessor
        short = build_tm_packet(apid=1, service=12, subtype=9,
                             data=struct.pack(">Hf", 0x0101, 18.4),
                             cuc_time=800_000_000)
        out = TMProcessor().process_packet(short)
        assert out["param_id"] == 0x0101
        assert "mon_id" not in out and "severity" not in out


# ── the console offers the right store ──────────────────────────────

class TestStoreSelection:

    def test_the_dump_button_map_includes_the_alarm_store(self):
        """S12 now routes to store 4; the map used to stop at Science (3)."""
        page = INDEX.read_text()
        assert "DUMP_STORE_MAP" in page
        assert "alarms: 4" in page
        assert "Alarm_Store" in page

    def test_the_map_matches_the_simulator_store_table(self):
        from smo_simulator.tm_storage import DEFAULT_STORES, SERVICE_TO_STORE
        assert SERVICE_TO_STORE[12] == 4, (
            "the payload owner routed S12 to the Alarm store; the console map "
            "must follow")
        assert DEFAULT_STORES[4]["name"] == "Alarm_Store"
        page = INDEX.read_text()
        for sid, info in DEFAULT_STORES.items():
            assert f"{sid}: {{name: '{info['name']}'" in page, (
                f"store {sid} ({info['name']}) missing from the console table")
