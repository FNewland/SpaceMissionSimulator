"""Console/mission log (POSITION_NEEDS G8).

Covers the append-only SQLite store (TM archive schema v3), the two
additive endpoints, and the server auto-append hooks (TC sent + S1
verdict, alarm ack, GO/NO-GO poll opened/result).
"""
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from smo_mcs.server import MCSServer
from smo_mcs.tm_archive import TMArchive, SCHEMA_VERSION

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


# ── the store ────────────────────────────────────────────────────────


class TestConsoleLogStore:
    def test_schema_v3_and_roundtrip(self, tmp_path):
        a = TMArchive(tmp_path / "a.db")
        a.open()
        assert SCHEMA_VERSION == 3
        assert a.schema_version == 3
        rid = a.store_console_entry(utc_epoch=100.0, kind="note",
                                    text="hello", position="eps_tcs")
        assert rid > 0
        rows = a.query_console_log()
        assert len(rows) == 1
        assert rows[0]["kind"] == "note"
        assert rows[0]["position"] == "eps_tcs"
        assert rows[0]["utc_epoch"] == 100.0
        a.close()

    def test_invalid_kind_rejected(self, tmp_path):
        a = TMArchive(tmp_path / "a.db")
        a.open()
        with pytest.raises(ValueError):
            a.store_console_entry(utc_epoch=1.0, kind="bogus", text="x")
        a.close()

    def test_since_filter_and_order(self, tmp_path):
        a = TMArchive(tmp_path / "a.db")
        a.open()
        for t in (10.0, 20.0, 30.0):
            a.store_console_entry(utc_epoch=t, kind="mark", text=f"t{t}")
        rows = a.query_console_log(since_epoch=15.0)
        assert [r["utc_epoch"] for r in rows] == [20.0, 30.0]
        a.close()

    def test_v2_file_upgrades_in_place(self, tmp_path):
        """A pre-existing sim-time v2 archive gains the table additively."""
        import sqlite3
        db = tmp_path / "old.db"
        a = TMArchive(db)
        a.open()
        a.close()
        conn = sqlite3.connect(db)
        conn.execute("UPDATE archive_meta SET value='2' "
                     "WHERE key='schema_version'")
        conn.execute("DROP TABLE console_log")
        conn.commit()
        conn.close()
        b = TMArchive(db)
        b.open()
        assert b.schema_version == 3
        assert b.status in ("ok", "new")
        b.store_console_entry(utc_epoch=1.0, kind="note", text="post-upgrade")
        assert len(b.query_console_log()) == 1
        b.close()

    def test_prune_never_deletes_console_log(self, tmp_path):
        a = TMArchive(tmp_path / "a.db")
        a.open()
        a.store_console_entry(utc_epoch=10.0, kind="note", text="ancient")
        a.store_parameters({"eps.bat_soc": 50.0}, utc_epoch=10.0)
        a.prune(now_epoch=10.0 + 10 * 604_800)
        assert a.query_parameters("eps.bat_soc") == []
        assert len(a.query_console_log()) == 1, \
            "console log is append-only — prune must not touch it"
        a.close()


# ── endpoints + hooks ────────────────────────────────────────────────


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
    return MCSServer(config_dir=str(CONFIG_DIR), http_port=0)


async def _client(server):
    app = web.Application()
    app.router.add_get("/api/console-log", server._handle_console_log_get)
    app.router.add_post("/api/console-log", server._handle_console_log_post)
    app.router.add_post("/api/go-nogo/poll", server._handle_go_nogo_poll)
    app.router.add_post("/api/go-nogo/respond",
                        server._handle_go_nogo_respond)
    app.router.add_post("/api/alarms/{alarm_id}/ack",
                        server._handle_alarm_ack)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


class TestConsoleLogEndpoints:
    @pytest.mark.asyncio
    async def test_post_then_get_roundtrip(self, server):
        client = await _client(server)
        try:
            resp = await client.post("/api/console-log", json={
                "position": "ttc", "kind": "mark",
                "text": "AOS Iqaluit", "ref": "pass:5"})
            assert resp.status == 200
            body = await resp.json()
            assert body["status"] == "logged"
            assert body["entry"]["source"] == "operator"

            resp = await client.get("/api/console-log")
            data = await resp.json()
            assert data["archive_unavailable"] is False
            kinds = [e["kind"] for e in data["entries"]]
            assert "mark" in kinds
            e = next(x for x in data["entries"] if x["kind"] == "mark")
            assert e["text"] == "AOS Iqaluit"
            assert e["ref"] == "pass:5"
            assert data["time_base"] == "sim"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_bad_kind_is_400(self, server):
        client = await _client(server)
        try:
            resp = await client.post("/api/console-log", json={
                "kind": "banana", "text": "x"})
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_missing_text_is_400(self, server):
        client = await _client(server)
        try:
            resp = await client.post("/api/console-log", json={
                "kind": "note"})
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_since_query_filters(self, server):
        client = await _client(server)
        try:
            await client.post("/api/console-log", json={
                "kind": "note", "text": "old", "ts": 100.0})
            await client.post("/api/console-log", json={
                "kind": "note", "text": "new", "ts": 200.0})
            resp = await client.get("/api/console-log?since=150")
            data = await resp.json()
            assert [e["text"] for e in data["entries"]] == ["new"]
        finally:
            await client.close()


class TestServerAutoAppend:
    def test_tc_send_appends_entry(self, server):
        server._record_command({"seq": 42, "name": "EPS_POWER_ON",
                                "service": 8, "subtype": 1,
                                "state": "SENT", "position": "eps_tcs"})
        rows = server._archive.query_console_log()
        tc = [r for r in rows if r["kind"] == "tc"]
        assert len(tc) == 1
        assert "EPS_POWER_ON" in tc[0]["text"]
        assert tc[0]["position"] == "eps_tcs"
        assert tc[0]["ref"] == "seq:42"
        assert tc[0]["source"] == "server"

    @pytest.mark.asyncio
    async def test_gonogo_poll_and_result_append(self, server):
        client = await _client(server)
        try:
            await client.post("/api/go-nogo/poll", json={
                "position": "flight_director", "label": "Pass startup"})
            rows = server._archive.query_console_log()
            gon = [r for r in rows if r["kind"] == "gonogo"]
            assert any("opened" in r["text"] for r in gon)
            # All positions respond GO -> result entry.
            for pos in server._positions:
                await client.post("/api/go-nogo/respond", json={
                    "position": pos, "response": "GO"})
            rows = server._archive.query_console_log()
            gon = [r for r in rows if r["kind"] == "gonogo"]
            assert any("ALL_GO" in r["text"] for r in gon)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_alarm_ack_appends_entry(self, server):
        server._alarm_journal.append({
            "id": 7, "severity": 3, "subsystem": "eps",
            "parameter": "eps.bat_soc", "acknowledged": False})
        client = await _client(server)
        try:
            resp = await client.post("/api/alarms/7/ack")
            assert resp.status == 200
            rows = server._archive.query_console_log()
            acks = [r for r in rows if r["kind"] == "ack"]
            assert len(acks) == 1
            assert "eps.bat_soc" in acks[0]["text"]
            assert acks[0]["ref"] == "alarm:7"
        finally:
            await client.close()

    def test_s1_verdict_appends_terminal_entry(self, server):
        """The verdict entry references the same seq as the send entry."""
        server._record_command({"seq": 9, "name": "X", "service": 8,
                                "subtype": 1, "state": "SENT",
                                "position": "aocs"})
        server._console_log("tc", "TC seq 9 S1 verdict: COMPLETED",
                            ref="seq:9")
        rows = server._archive.query_console_log()
        refs = [r["ref"] for r in rows if r["kind"] == "tc"]
        assert refs.count("seq:9") == 2
