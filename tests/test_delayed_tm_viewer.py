"""Smoke + functional tests for the delayed TM viewer.

Builds a real EPS HK packet (and a real S5 event packet) using the same
TMBuilder the simulator uses, writes them out as a length-prefixed dump
file in the canonical ``dump_sidNN_<UTC>.bin`` format, then exercises:

* ``list_dumps`` (filename parsing, metadata)
* ``select_dumps`` (latest, all, filename, time-window)
* ``analyse_dumps`` (HK decode, S5 event capture, packet counts)
* The aiohttp app's ``/api/dumps`` and ``/api/decode`` routes

If any of these regress in the future, this test will fail loudly.
"""
from __future__ import annotations

import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from smo_common.telemetry.tm_builder import TMBuilder
from smo_common.config.loader import load_hk_structures

import delayed_tm_viewer as dtv  # noqa: E402


CONFIG_DIR = REPO / "configs" / "eosat1"


def _build_eps_hk_packet(values: dict[int, float]) -> bytes:
    hk_defs = load_hk_structures(CONFIG_DIR)
    eps_def = next(h for h in hk_defs if h.sid == 1)
    structure = [(p.param_id, p.pack_format, p.scale) for p in eps_def.parameters]
    builder = TMBuilder(apid=100)
    full = {pid: values.get(pid, 0.0) for pid, _, _ in structure}
    return builder.build_hk_packet(sid=1, params=full, hk_structure=structure)


def _build_event_packet(event_id: int, severity: int, desc: str) -> bytes:
    builder = TMBuilder(apid=100)
    return builder.build_event_packet(
        event_id=event_id, severity=severity, aux_text=desc, params={},
    )


def _write_dump(dump_dir: Path, sid: int, when: datetime, packets: list[bytes]) -> Path:
    name = f"dump_sid{sid:02d}_{when.strftime('%Y%m%dT%H%M%SZ')}.bin"
    path = dump_dir / name
    with path.open("wb") as f:
        for pkt in packets:
            f.write(struct.pack(">I", len(pkt)))
            f.write(pkt)
    return path


@pytest.fixture
def populated_dump_dir(tmp_path: Path) -> Path:
    """Create three dump files at known timestamps with known contents."""
    base = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    dump_dir = tmp_path / "dumps"
    dump_dir.mkdir()

    # Pass A — 12:00 UTC, EPS HK + a couple of events
    _write_dump(dump_dir, 1, base, [
        _build_eps_hk_packet({0x0100: 7.42, 0x0101: 83.5}),
        _build_eps_hk_packet({0x0100: 7.40, 0x0101: 83.0}),
        _build_event_packet(0x1234, 2, "EPS_PASS_NORMAL"),
    ])
    # Pass B — 13:00 UTC, EPS HK only
    _write_dump(dump_dir, 1, base + timedelta(hours=1), [
        _build_eps_hk_packet({0x0100: 7.55, 0x0101: 84.1}),
    ])
    # Pass C — 14:00 UTC, with a higher-severity event
    _write_dump(dump_dir, 1, base + timedelta(hours=2), [
        _build_eps_hk_packet({0x0100: 7.30, 0x0101: 79.0}),
        _build_event_packet(0xDEAD, 4, "BAT_LOW_VOLTAGE"),
    ])
    return dump_dir


def test_list_dumps_parses_filenames(populated_dump_dir: Path):
    dumps = dtv.list_dumps(populated_dump_dir)
    assert len(dumps) == 3
    # Sorted oldest → newest
    assert dumps[0]["timestamp"] < dumps[1]["timestamp"] < dumps[2]["timestamp"]
    for d in dumps:
        assert d["sid"] == 1
        assert d["filename"].startswith("dump_sid01_")
        assert d["size_bytes"] > 0


def test_select_dumps_latest_and_all(populated_dump_dir: Path):
    latest = dtv.select_dumps(populated_dump_dir, latest=True)
    assert len(latest) == 1
    assert "T140000Z" in latest[0].name
    every = dtv.select_dumps(populated_dump_dir, all_=True)
    assert len(every) == 3


def test_select_dumps_by_filename_and_time_window(populated_dump_dir: Path):
    files = sorted(p.name for p in populated_dump_dir.glob("*.bin"))
    picked = dtv.select_dumps(populated_dump_dir, files=[files[1]])
    assert len(picked) == 1 and picked[0].name == files[1]

    window = dtv.select_dumps(
        populated_dump_dir,
        since="2026-04-06T12:30:00Z",
        until="2026-04-06T13:30:00Z",
    )
    assert len(window) == 1
    assert "T130000Z" in window[0].name


def test_analyse_dumps_decodes_hk_and_events(populated_dump_dir: Path):
    params_by_id, _, hk_plist = dtv.load_catalogs()
    result = dtv.analyse_dumps(
        sorted(populated_dump_dir.glob("*.bin")),
        params_by_id,
        hk_plist,
    )
    # Total packets = 3 + 1 + 2 = 6
    assert result["total_packets"] == 6
    # 2 events (severity 2 and 4)
    assert len(result["events"]) == 2
    sevs = sorted(e["severity"] for e in result["events"])
    assert sevs == [2, 4]
    # HK decoded for SID 1, with bat_voltage and bat_soc among the params
    sids = {h["sid"] for h in result["hk"]}
    assert 1 in sids
    sid1 = next(h for h in result["hk"] if h["sid"] == 1)
    pids = {p["pid"] for p in sid1["params"]}
    assert 0x0100 in pids and 0x0101 in pids
    # Voltage values were 7.42 / 7.40 / 7.55 / 7.30 → check min/max are sane
    bat_v = next(p for p in sid1["params"] if p["pid"] == 0x0100)
    assert 7.25 < bat_v["min"] < 7.35
    assert 7.50 < bat_v["max"] < 7.60


@pytest.mark.asyncio
async def test_http_endpoints_return_real_data(populated_dump_dir: Path):
    from aiohttp.test_utils import TestClient, TestServer

    app = dtv.make_app(populated_dump_dir)
    async with TestClient(TestServer(app)) as client:
        # /api/dumps lists everything
        r = await client.get("/api/dumps")
        assert r.status == 200
        body = await r.json()
        assert len(body["dumps"]) == 3

        # /api/decode?all=1 returns combined analysis
        r = await client.get("/api/decode", params={"all": "1"})
        assert r.status == 200
        j = await r.json()
        assert j["total_packets"] == 6
        assert len(j["events"]) == 2
        assert any(h["sid"] == 1 for h in j["hk"])

        # /api/decode?latest=1 returns just the newest pass (2 packets)
        r = await client.get("/api/decode", params={"latest": "1"})
        j = await r.json()
        assert j["total_packets"] == 2

        # /api/decode with no match → graceful empty response, not 500
        r = await client.get("/api/decode", params={
            "since": "2030-01-01T00:00:00Z",
            "until": "2030-01-02T00:00:00Z",
        })
        assert r.status == 200
        j = await r.json()
        assert j["total_packets"] == 0
        assert "error" in j

        # / serves the HTML page
        r = await client.get("/")
        assert r.status == 200
        assert "DELAYED TM VIEWER" in (await r.text())
