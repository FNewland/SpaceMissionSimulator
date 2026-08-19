"""End-of-pass RF rollups (POSITION_NEEDS G14, rfsim half).

The tracker turns live lock/Eb-N0/frame counters into one record per pass
(predicted AOS vs achieved lock, mean Eb/N0, FER, data volume) appended to
a JSONL store; /api/pass-rollups serves it.
"""
import json

import pytest

from smo_rfsim.pass_rollup import PassRollupTracker, read_rollups
from smo_rfsim.radio.frontend import LockState, RadioFrontend


def _status(carrier="UNLOCKED", eb_n0=0.0, good=0, bad=0, tier=""):
    fe = RadioFrontend()
    s = fe.status
    s.carrier_lock = LockState(carrier) if isinstance(carrier, str) \
        else carrier
    s.eb_n0_db = eb_n0
    s.good_frames = good
    s.bad_frames = bad
    s.tm_tier_name = tier
    return s


class TestTracker:
    def test_full_pass_produces_record(self, tmp_path):
        store = tmp_path / "rollups.jsonl"
        tr = PassRollupTracker(frame_length_bytes=1000, path=store)
        # AOS at t=100 (geometric), lock achieved at t=112.
        tr.set_contact(True, "Iqaluit", 100.0, "sim",
                       status=_status(good=50, bad=2))
        tr.sample(_status("ACQUIRING", eb_n0=0.0, good=50, bad=2), 105.0)
        tr.sample(_status("LOCKED", eb_n0=10.0, good=60, bad=3,
                          tier="LOW"), 112.0)
        tr.sample(_status("LOCKED", eb_n0=12.0, good=150, bad=4,
                          tier="HIGH"), 300.0)
        tr.set_contact(False, "Iqaluit", 640.0, "sim",
                       status=_status("LOCKED", eb_n0=11.0, good=250, bad=6))
        records = read_rollups(path=store)
        assert len(records) == 1
        r = records[0]
        assert r["gs_name"] == "Iqaluit"
        assert r["time_base"] == "sim"
        assert r["aos_pred_ts"] == 100.0
        assert r["los_ts"] == 640.0
        assert r["duration_s"] == 540.0
        # Achieved lock vs predicted AOS.
        assert r["locked"] is True
        assert r["first_lock_ts"] == 112.0
        assert r["lock_delay_s"] == 12.0
        # Mean of the non-zero Eb/N0 samples: (10+12+11)/3.
        assert r["mean_eb_n0_db"] == pytest.approx(11.0)
        # Frame deltas across the pass: 250-50 ok, 6-2 bad.
        assert r["frames_ok"] == 200
        assert r["frames_bad"] == 4
        assert r["fer"] == pytest.approx(4 / 204, abs=1e-4)
        assert r["data_volume_bytes"] == 200 * 1000
        assert set(r["tier_names"]) == {"LOW", "HIGH"}

    def test_pass_with_no_lock_is_honest(self, tmp_path):
        store = tmp_path / "rollups.jsonl"
        tr = PassRollupTracker(path=store)
        tr.set_contact(True, "Troll", 0.0, "sim", status=_status())
        tr.sample(_status("ACQUIRING"), 10.0)
        tr.set_contact(False, "Troll", 500.0, "sim", status=_status())
        r = read_rollups(path=store)[0]
        assert r["locked"] is False
        assert r["first_lock_ts"] is None
        assert r["lock_delay_s"] is None
        assert r["mean_eb_n0_db"] is None  # no non-zero sample — no invention

    def test_consecutive_passes_get_distinct_ids(self, tmp_path):
        store = tmp_path / "rollups.jsonl"
        tr = PassRollupTracker(path=store)
        for i in range(3):
            t0 = i * 1000.0
            tr.set_contact(True, "GS", t0, "sim", status=_status())
            tr.set_contact(False, "GS", t0 + 500, "sim", status=_status())
        ids = [r["pass_id"] for r in read_rollups(path=store)]
        assert ids == [1, 2, 3]

    def test_active_summary_only_during_pass(self, tmp_path):
        tr = PassRollupTracker(path=tmp_path / "r.jsonl")
        assert tr.active_summary() is None
        tr.set_contact(True, "GS", 5.0, "wall", status=_status())
        a = tr.active_summary()
        assert a is not None and a["gs_name"] == "GS"
        assert a["time_base"] == "wall"
        tr.set_contact(False, "GS", 50.0, "wall", status=_status())
        assert tr.active_summary() is None

    def test_store_is_append_only_jsonl(self, tmp_path):
        store = tmp_path / "r.jsonl"
        tr = PassRollupTracker(path=store)
        tr.set_contact(True, "GS", 0.0, "sim", status=_status())
        tr.set_contact(False, "GS", 10.0, "sim", status=_status())
        tr2 = PassRollupTracker(path=store)  # a restart appends, not truncates
        tr2.set_contact(True, "GS", 100.0, "sim", status=_status())
        tr2.set_contact(False, "GS", 110.0, "sim", status=_status())
        lines = store.read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            json.loads(line)


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_api_pass_rollups(self, tmp_path, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer
        from smo_rfsim.radio.web_ui import build_app

        store = tmp_path / "rollups.jsonl"
        monkeypatch.setenv("SMO_PASS_ROLLUPS", str(store))
        tr = PassRollupTracker(path=store)
        tr.set_contact(True, "Iqaluit", 0.0, "sim", status=_status())
        tr.sample(_status("LOCKED", eb_n0=9.0, good=10, bad=0), 5.0)
        tr.set_contact(False, "Iqaluit", 600.0, "sim",
                       status=_status("LOCKED", eb_n0=9.0, good=20, bad=0))

        class FakeControl:
            _pass_rollups = tr

        app = build_app(RadioFrontend(), control=FakeControl())
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/pass-rollups")
            assert resp.status == 200
            data = await resp.json()
            assert len(data["rollups"]) == 1
            assert data["rollups"][0]["gs_name"] == "Iqaluit"
            assert data["active"] is None
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_api_without_control_reads_store(self, tmp_path,
                                                   monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer
        from smo_rfsim.radio.web_ui import build_app

        store = tmp_path / "rollups.jsonl"
        monkeypatch.setenv("SMO_PASS_ROLLUPS", str(store))
        app = build_app(RadioFrontend(), control=None)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.get("/api/pass-rollups")
            assert resp.status == 200
            data = await resp.json()
            assert data["rollups"] == []
            assert data["active"] is None
        finally:
            await client.close()
