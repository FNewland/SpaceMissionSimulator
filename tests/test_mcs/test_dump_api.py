"""Delayed-TM dump store served over HTTP (defect #90).

Franz's classroom failure: the S15 dump archive was a directory on the
server's disk, readable only by a standalone tool that read the LOCAL
filesystem. A student browser on a client machine had no route to it. The
fix serves the store from the process that owns it — the MCS (the ground
segment's copy, and the port every operator already has open) and, for a
split deployment, the simulator that writes the files.

Covered here:

* ``GET /api/dumps`` on an EMPTY store — 200 with an empty list, not a 404
  or a 500 (a store with no dumps yet is the normal state at the start of a
  run, and the page must still load).
* A dump written by the SIMULATOR ENGINE (``queue_dump``) shows up in the
  MCS listing with correct metadata.
* ``GET /api/dumps/file?name=...`` returns the exact archive bytes.
* Path-traversal rejection on the fetch and summary endpoints. This is a
  network-exposed endpoint, so a filename that escapes the store must be
  refused rather than served.
* ``GET /api/dumps/summary?name=...`` decodes the archive.
* ``GET /dumps`` serves the browser page.
* ``tools/delayed_tm_viewer.py`` working as a CLIENT of the API rather than
  a filesystem reader.
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

from smo_common.dump_store import (  # noqa: E402
    DumpPathError,
    list_dumps,
    register_dump_routes,
    resolve_dump_dir,
    resolve_dump_file,
    summarise_dump,
)

CONFIG_DIR = REPO / "configs" / "eosat1"


# ── helpers ──────────────────────────────────────────────────────────

def _app_for(dump_dir: Path, owner: str = "MCS") -> web.Application:
    """The exact routes the MCS/simulator mount, over a throwaway store."""
    app = web.Application()
    register_dump_routes(app, lambda: dump_dir, owner=owner)
    return app


def _write_raw_dump(dump_dir: Path, name: str, packets: list[bytes]) -> Path:
    dump_dir.mkdir(parents=True, exist_ok=True)
    path = dump_dir / name
    with path.open("wb") as f:
        for pkt in packets:
            f.write(struct.pack(">I", len(pkt)))
            f.write(pkt)
    return path


@pytest.fixture
def engine_dump(tmp_path, monkeypatch):
    """Run a real S15 dump through the engine and return (store_dir, path)."""
    if not CONFIG_DIR.exists():
        pytest.skip(f"EOSAT-1 config dir not present at {CONFIG_DIR}")
    from smo_simulator.engine import SimulationEngine
    from smo_simulator.tm_storage import OnboardTMStorage

    store = tmp_path / "server-dumps"
    monkeypatch.setenv("SMO_DUMP_DIR", str(store))
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    assert eng._dump_dir == store, "engine must honour the shared resolution order"

    eng._tm_storage = OnboardTMStorage()
    packets = [b"\xAA\xBB\xCC\xDD", b"\x11" * 12]
    for pkt in packets:
        assert eng._tm_storage.store_packet_direct(1, pkt) is True
    assert eng.queue_dump(1) == len(packets)

    written = sorted(store.glob("dump_sid01_*.bin"))
    assert written, "engine wrote no archive"
    return store, written[0]


# ── listing ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_store_lists_cleanly(tmp_path):
    """An empty (or not-yet-created) store is a 200 with no dumps."""
    store = tmp_path / "empty"
    async with TestClient(TestServer(_app_for(store))) as client:
        r = await client.get("/api/dumps")
        assert r.status == 200
        body = await r.json()
        assert body["dumps"] == []
        assert body["count"] == 0
        assert body["total_bytes"] == 0
        assert body["exists"] is False
        assert body["store"] == str(store)
        assert body["owner"] == "MCS"

    store.mkdir()
    async with TestClient(TestServer(_app_for(store))) as client:
        body = await (await client.get("/api/dumps")).json()
        assert body["dumps"] == [] and body["exists"] is True


@pytest.mark.asyncio
async def test_engine_written_dump_appears_in_listing(engine_dump):
    store, path = engine_dump
    async with TestClient(TestServer(_app_for(store))) as client:
        body = await (await client.get("/api/dumps")).json()

    assert body["count"] == 1
    entry = body["dumps"][0]
    assert entry["filename"] == path.name
    assert entry["store_id"] == 1
    assert entry["packet_count"] == 2
    assert entry["size_bytes"] == path.stat().st_size
    assert entry["timestamp"] and entry["timestamp"].endswith("+00:00")
    # A server-side absolute path must never leak to a network client.
    assert "path" not in entry


# ── retrieval ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_returns_exact_archive_bytes(engine_dump):
    store, path = engine_dump
    expected = path.read_bytes()
    async with TestClient(TestServer(_app_for(store))) as client:
        r = await client.get("/api/dumps/file", params={"name": path.name})
        assert r.status == 200
        assert await r.read() == expected
        assert path.name in r.headers.get("Content-Disposition", "")

        # The /api/dumps/<name> alias serves the same bytes.
        r = await client.get(f"/api/dumps/{path.name}")
        assert r.status == 200
        assert await r.read() == expected


@pytest.mark.asyncio
async def test_fetch_unknown_dump_is_404(tmp_path):
    store = tmp_path / "dumps"
    store.mkdir()
    async with TestClient(TestServer(_app_for(store))) as client:
        r = await client.get("/api/dumps/file",
                             params={"name": "dump_sid09_20260101T000000Z.bin"})
        assert r.status == 404


# ── path traversal ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "../secret.bin",
    "..",
    ".",
    "/etc/passwd",
    "subdir/dump_sid01_20260101T000000Z.bin",
    "dump_sid01_20260101T000000Z.bin/../../../etc/passwd",
    "C:\\windows\\system32\\config\\sam",
    "..\\..\\secret.bin",
    "secret.txt",
])
@pytest.mark.asyncio
async def test_fetch_rejects_path_traversal(tmp_path, name):
    """A filename that escapes the store must be refused, never served."""
    store = tmp_path / "dumps"
    store.mkdir()
    _write_raw_dump(store, "dump_sid01_20260101T000000Z.bin", [b"\x01\x02"])
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"CLASSIFIED")
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "passwd").write_bytes(b"root:x:0:0")

    async with TestClient(TestServer(_app_for(store))) as client:
        for path in ("/api/dumps/file", "/api/dumps/summary"):
            r = await client.get(path, params={"name": name})
            assert r.status in (400, 404), (
                f"{path}?name={name!r} returned {r.status}")
            payload = await r.read()
            assert b"CLASSIFIED" not in payload
            assert b"root:x:0:0" not in payload


@pytest.mark.asyncio
async def test_fetch_without_a_name_is_rejected(tmp_path):
    """No name at all is a bad request on /file (on /summary it means
    'describe the whole store', which is harmless)."""
    store = tmp_path / "dumps"
    store.mkdir()
    async with TestClient(TestServer(_app_for(store))) as client:
        assert (await client.get("/api/dumps/file")).status == 400
        assert (await client.get("/api/dumps/summary")).status == 200


def test_resolve_dump_file_rejects_escapes_and_symlinks(tmp_path):
    """The shared resolver is the security boundary — test it directly too."""
    store = tmp_path / "dumps"
    store.mkdir()
    good = _write_raw_dump(store, "dump_sid01_20260101T000000Z.bin", [b"\x01"])
    outside = tmp_path / "dump_sid99_20260101T000000Z.bin"
    outside.write_bytes(b"OUTSIDE")

    assert resolve_dump_file(store, good.name) == good

    for bad in ("../dump_sid99_20260101T000000Z.bin", "/etc/passwd", "..",
                "a/b.bin", "notadump.txt", "", "\x00.bin"):
        with pytest.raises(DumpPathError):
            resolve_dump_file(store, bad)

    # A symlink planted inside the store must not be a way out.
    link = store / "dump_sid98_20260101T000000Z.bin"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks unavailable")
    with pytest.raises(DumpPathError):
        resolve_dump_file(store, link.name)


# ── summary ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_decodes_a_real_hk_dump(tmp_path):
    from smo_common.config.loader import load_hk_structures
    from smo_common.telemetry.tm_builder import TMBuilder

    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")

    hk_defs = load_hk_structures(CONFIG_DIR)
    eps = next(h for h in hk_defs if h.sid == 1)
    structure = [(p.param_id, p.pack_format, p.scale) for p in eps.parameters]
    builder = TMBuilder(apid=100)
    pkts = [builder.build_hk_packet(sid=1, params={pid: 1.0 for pid, _, _ in structure},
                                    hk_structure=structure)
            for _ in range(3)]
    store = tmp_path / "dumps"
    path = _write_raw_dump(store, "dump_sid01_20260406T120000Z.bin", pkts)

    direct = summarise_dump(path)
    assert direct["packet_count"] == 3
    assert direct["crc_errors"] == 0
    assert 100 in direct["apids"]
    assert {"service": 3, "subtype": 25, "count": 3} in direct["packet_counts"]

    async with TestClient(TestServer(_app_for(store))) as client:
        body = await (await client.get("/api/dumps/summary",
                                       params={"name": path.name})).json()
        assert body == direct

        # No name → cheap metadata for the whole store.
        body = await (await client.get("/api/dumps/summary")).json()
        assert len(body["dumps"]) == 1


@pytest.mark.asyncio
async def test_dumps_page_is_served(tmp_path):
    store = tmp_path / "dumps"
    async with TestClient(TestServer(_app_for(store))) as client:
        r = await client.get("/dumps")
        assert r.status == 200
        text = await r.text()
        assert "DELAYED TM ARCHIVE" in text
        # Self-contained: no CDN, and the API is same-origin (relative).
        assert "http://" not in text.split("<script>")[1]


# ── location resolution ──────────────────────────────────────────────

def test_resolution_order_is_single_and_shared(tmp_path, monkeypatch):
    class Cfg:
        dump_dir = str(tmp_path / "from-config")

    monkeypatch.delenv("SMO_DUMP_DIR", raising=False)
    assert resolve_dump_dir(None) == Path("workspace/dumps")
    assert resolve_dump_dir(Cfg()) == Path(tmp_path / "from-config")

    monkeypatch.setenv("SMO_DUMP_DIR", str(tmp_path / "from-env"))
    assert resolve_dump_dir(Cfg()) == Path(tmp_path / "from-env")
    # An explicit CLI argument still wins over the environment.
    assert resolve_dump_dir(Cfg(), explicit=str(tmp_path / "from-cli")) \
        == Path(tmp_path / "from-cli")


def test_mcs_resolves_the_same_store_as_the_engine(tmp_path, monkeypatch):
    """The MCS and the simulator must agree on where the store is."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    from unittest.mock import patch
    from smo_common.config.schemas import MCSDisplayConfig
    from smo_mcs.server import MCSServer
    from smo_simulator.engine import SimulationEngine

    store = tmp_path / "shared-dumps"
    monkeypatch.setenv("SMO_DUMP_DIR", str(store))
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        mcs = MCSServer(config_dir=CONFIG_DIR, http_port=0)
    assert mcs._dump_dir == eng._dump_dir == store


# ── the standalone viewer as an API client ───────────────────────────

@pytest.mark.asyncio
async def test_viewer_works_against_the_api(engine_dump):
    """tools/delayed_tm_viewer.py in --api mode reads a REMOTE store."""
    import delayed_tm_viewer as dtv  # noqa: E402

    store, path = engine_dump
    async with TestClient(TestServer(_app_for(store))) as api:
        base = f"http://{api.host}:{api.port}"
        remote = dtv.RemoteDumpStore(base, cache_dir=Path(store).parent / "cache")
        assert remote.api_base == base + "/api/dumps"

        listing = await remote.list()
        assert [d["filename"] for d in listing] == [path.name]

        fetched = await remote.fetch(path.name)
        assert fetched.read_bytes() == path.read_bytes()
        assert fetched.parent == remote.cache_dir  # cached, not written elsewhere

        picked = await remote.select(all_=True)
        assert [p.name for p in picked] == [path.name]

        # The client refuses an escaping name before it ever touches the disk.
        with pytest.raises(ValueError):
            await remote.fetch("../../etc/passwd")

        # And the viewer's own HTTP surface serves the remote store.
        viewer = dtv.make_app(None, api_base=base)
        async with TestClient(TestServer(viewer)) as vc:
            body = await (await vc.get("/api/dumps")).json()
            assert [d["filename"] for d in body["dumps"]] == [path.name]
            decoded = await (await vc.get("/api/decode", params={"all": "1"})).json()
            # The engine fixture stores opaque test payloads rather than real
            # PUS packets, so the decoder finds no services — but the file was
            # fetched over HTTP and read end to end, which is the point here.
            assert [f["filename"] for f in decoded["files"]] == [path.name]
            assert decoded["files"][0]["packets"] == 2


@pytest.mark.asyncio
async def test_viewer_api_mode_survives_an_unreachable_server():
    """A dead API must show an error, not crash the viewer."""
    import delayed_tm_viewer as dtv  # noqa: E402

    viewer = dtv.make_app(None, api_base="http://127.0.0.1:1")
    async with TestClient(TestServer(viewer)) as vc:
        r = await vc.get("/api/dumps")
        assert r.status == 200
        assert "error" in await r.json()

        r = await vc.get("/api/decode", params={"all": "1"})
        assert r.status == 200
        assert (await r.json())["total_packets"] == 0


def test_list_dumps_ignores_foreign_files(tmp_path):
    store = tmp_path / "dumps"
    _write_raw_dump(store, "dump_sid03_20260101T000000Z.bin", [b"\x01\x02\x03"])
    (store / "notes.txt").write_text("not a dump")
    (store / "subdir").mkdir()

    entries = list_dumps(store)
    assert [e["filename"] for e in entries] == ["dump_sid03_20260101T000000Z.bin"]
    assert entries[0]["store_id"] == 3
    assert entries[0]["packet_count"] == 1
