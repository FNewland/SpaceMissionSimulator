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


def _build_eps_hk_packet(values: dict[int, float], t: float = 1000.0) -> bytes:
    hk_defs = load_hk_structures(CONFIG_DIR)
    eps_def = next(h for h in hk_defs if h.sid == 1)
    structure = [(p.param_id, p.pack_format, p.scale) for p in eps_def.parameters]
    builder = TMBuilder(apid=100, time_source=lambda: t)
    full = {pid: values.get(pid, 0.0) for pid, _, _ in structure}
    return builder.build_hk_packet(sid=1, params=full, hk_structure=structure)


def _build_event_packet(event_id: int, severity: int, desc: str) -> bytes:
    builder = TMBuilder(apid=100, time_source=lambda: 1000.0)
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


# ── G11: ground limits on played-back dumps ─────────────────────────────
#
# mcs/limits.yaml bands used below (see configs/eosat1/mcs/limits.yaml):
#   0x0100 bat_voltage  yellow 23..29,  red 22..29.5
#   0x0101 bat_soc      yellow 25..95,  red 15..100
#   0x0102 bat_temp     yellow  2..40,  red  0..45
#
# The fixture keeps every OTHER limited SID-1 parameter in band so the
# per-dump summary counts exactly the two planted exceedances.

_IN_BAND_SID1 = {
    0x0100: 25.0, 0x0101: 83.5, 0x0102: 20.0, 0x0105: 28.0,
    0x0118: 0.1, 0x0119: 0.1, 0x011A: 0.1, 0x011B: 0.1,
    0x011C: 0.1, 0x011D: 0.1, 0x011E: 0.1, 0x011F: 0.1, 0x0120: 10.0,
}


@pytest.fixture
def limits_dump_dir(tmp_path: Path) -> Path:
    """One dump, two samples 8 s apart: 0x0100 red, 0x0102 yellow, rest OK."""
    when = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    dump_dir = tmp_path / "dumps"
    dump_dir.mkdir()
    values = dict(_IN_BAND_SID1)
    values[0x0100] = 20.0      # below red_low 22 -> RED
    values[0x0102] = 42.0      # above yellow_high 40, below red_high 45 -> YELLOW
    _write_dump(dump_dir, 1, when, [
        _build_eps_hk_packet(values, t=1000.0),
        _build_eps_hk_packet(values, t=1008.0),
    ])
    return dump_dir


class TestLimitsOnPlayedBackDumps:
    def _analyse(self, dump_dir: Path) -> dict:
        params_by_id, _, hk_plist = dtv.load_catalogs()
        limits = dtv.load_limits_by_pid(REPO / "configs" / "eosat1")
        assert limits, "mcs/limits.yaml did not load"
        return dtv.analyse_dumps(sorted(dump_dir.glob("*.bin")),
                                 params_by_id, hk_plist,
                                 limits_by_pid=limits)

    def test_red_yellow_and_in_band_classified(self, limits_dump_dir: Path):
        result = self._analyse(limits_dump_dir)
        sid1 = next(h for h in result["hk"] if h["sid"] == 1)
        by_pid = {p["pid"]: p for p in sid1["params"]}

        red = by_pid[0x0100]["limit"]
        assert red is not None and red["state"] == "red"
        assert red["samples_red"] == 2 and red["samples_yellow"] == 0
        # 2 samples 8 s apart: first owns 8 s, last owns the mean spacing (8 s)
        assert red["time_in_red_s"] == pytest.approx(16.0, abs=0.5)
        assert red["time_in_yellow_s"] == 0.0
        assert red["band"]["red_low"] == pytest.approx(22.0)

        yellow = by_pid[0x0102]["limit"]
        assert yellow is not None and yellow["state"] == "yellow"
        assert yellow["samples_yellow"] == 2 and yellow["samples_red"] == 0
        assert yellow["time_in_yellow_s"] == pytest.approx(16.0, abs=0.5)
        assert yellow["time_in_red_s"] == 0.0

        ok = by_pid[0x0101]["limit"]
        assert ok is not None and ok["state"] == "ok"
        assert ok["time_in_yellow_s"] == 0.0 and ok["time_in_red_s"] == 0.0

    def test_per_dump_summary_line_counts_exceedances(self, limits_dump_dir: Path):
        result = self._analyse(limits_dump_dir)
        lim = result["limits"]
        assert lim["checked"] >= 3
        assert len(lim["red"]) == 1 and len(lim["yellow"]) == 1
        assert lim["summary"] == "1 parameter(s) exceeded yellow, 1 red"
        # names, not ids, so the summary is readable on the page
        assert any("bat" in n or "0x" in n for n in lim["red"] + lim["yellow"])

    def test_all_in_band_summary(self, tmp_path: Path):
        dump_dir = tmp_path / "dumps"
        dump_dir.mkdir()
        _write_dump(dump_dir, 1,
                    datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
                    [_build_eps_hk_packet(dict(_IN_BAND_SID1), t=1000.0),
                     _build_eps_hk_packet(dict(_IN_BAND_SID1), t=1008.0)])
        result = self._analyse(dump_dir)
        assert result["limits"]["yellow"] == []
        assert result["limits"]["red"] == []
        assert "within limits" in result["limits"]["summary"]

    def test_no_limits_loaded_is_stated_not_invented(self, limits_dump_dir: Path):
        params_by_id, _, hk_plist = dtv.load_catalogs()
        result = dtv.analyse_dumps(sorted(limits_dump_dir.glob("*.bin")),
                                   params_by_id, hk_plist)
        assert result["limits"]["summary"] == "no ground limits loaded"
        sid1 = next(h for h in result["hk"] if h["sid"] == 1)
        assert all(p["limit"] is None for p in sid1["params"])


@pytest.mark.asyncio
async def test_http_decode_carries_limit_verdicts(limits_dump_dir: Path):
    """The served /api/decode result carries the G11 limit block + badges data."""
    from aiohttp.test_utils import TestClient, TestServer

    app = dtv.make_app(limits_dump_dir)
    async with TestClient(TestServer(app)) as client:
        r = await client.get("/api/decode", params={"all": "1"})
        assert r.status == 200
        j = await r.json()
        assert j["limits"]["checked"] >= 3
        assert "exceeded yellow" in j["limits"]["summary"]
        sid1 = next(h for h in j["hk"] if h["sid"] == 1)
        red = next(p for p in sid1["params"] if p["pid"] == 0x0100)
        assert red["limit"]["state"] == "red"
        assert red["limit"]["band"]["yellow_high"] is not None

        # The page ships the badge renderer and the band-shading plugin.
        page = await (await client.get("/")).text()
        assert "limitCell" in page and "limitBands" in page
        assert "Ground limits" in page


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
