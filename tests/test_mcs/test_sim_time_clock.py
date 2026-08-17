"""The MCS runs on SIM TIME — spec `docs/plan_2026-08c/TIME_ARCHITECTURE.md`, Part D.

What these tests pin down, and why each one exists:

* **D.1** `sim_now()` / `sim_now_ts()` are the clock, and the anchor-and-
  extrapolate mechanism (with speed) still works after the rename.
* **D.2** Staleness ages in **sim** seconds. Two properties matter and they
  are the two the old wall-clock code got backwards: **pausing** the simulator
  must FREEZE every age (it used to make the whole console stale within a
  minute), and **accelerating** it must age telemetry at speed (it used to
  make nothing ever go stale).
* **D.3** Alarm raise/clear timestamps are sim time.
* **D.4** The archive requires an explicit epoch at every writer, stores sim
  time, and stamps a schema version so a pre-migration wall-clock database is
  not silently read as sim-time data.
* **D.5** Command history is sim time and records the on-board CUC an S11
  command was scheduled for.
* **D.6** The `> 1e9` heuristic is gone: `0x0309` is decoded on J2000, so
  `sc_time` is populated by ordinary telemetry instead of only after a GPS
  sync wrote a Unix value into a J2000 field.
* **D.9** The correlation block the console renders.

The browser half (D.7 / D.8) is covered in ``test_mcs_page_behaviour.py``
style, via the Node harness, at the bottom of this file.
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.config.schemas import MCSDisplayConfig  # noqa: E402
from smo_common.protocol.ecss_packet import (  # noqa: E402
    datetime_to_cuc_seconds,
)
from smo_mcs.server import MCSServer  # noqa: E402
from smo_mcs.tm_archive import (  # noqa: E402
    SCHEMA_VERSION, TIME_BASE_SIM, TIME_BASE_WALL, TMArchive,
)

CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).parent / "js" / "mcs_harness.js"

#: A sim epoch deliberately far from the wall clock, so any test that
#: accidentally passes because sim time happens to equal wall time fails here.
SIM_EPOCH = datetime(2030, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ── helpers ─────────────────────────────────────────────────────────────

def _make_server(tmp_path=None, speed=1.0) -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        srv = MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                        connect_port=9999, http_port=0)
    srv._archive.close()
    if tmp_path is not None:
        srv._archive = TMArchive(tmp_path / "tm_archive.db")
        srv._archive.open()
    else:
        srv._archive = MagicMock()
        srv._archive.store_alarm.return_value = 1
    _anchor(srv, SIM_EPOCH, speed)
    return srv


def _anchor(srv: MCSServer, sim_utc: datetime, speed: float,
            wall_ago_s: float = 0.0) -> None:
    """Apply the anchor the sim-state poll loop applies."""
    srv._ground_epoch = sim_utc
    srv._ground_start_wall = _time.time() - wall_ago_s
    srv._sim_speed = float(speed)


def _pkt(service, subtype, data, apid=1, seq=0, cuc_seconds=None):
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.secondary.cuc_seconds = cuc_seconds
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _ws(srv):
    srv._ws_clients = []
    srv._ws_lock = asyncio.Lock()
    srv._param_cache_lock = asyncio.Lock()


# ── D.1 the clock ───────────────────────────────────────────────────────

def test_sim_now_is_the_clock_and_get_ground_utc_is_an_alias():
    srv = _make_server()
    assert abs((srv.sim_now() - SIM_EPOCH).total_seconds()) < 2.0
    # The old spelling still resolves, to exactly the same clock.
    assert abs((srv.get_ground_utc() - srv.sim_now()).total_seconds()) < 0.5
    # Two consecutive reads of a live clock, so allow a millisecond.
    assert abs(srv.sim_now_ts() - srv.sim_now().timestamp()) < 1e-3


def test_sim_now_extrapolates_at_speed():
    srv = _make_server(speed=10.0)
    _anchor(srv, SIM_EPOCH, 10.0, wall_ago_s=3.0)
    # 3 wall seconds x speed 10 = 30 sim seconds.
    assert abs((srv.sim_now() - (SIM_EPOCH + timedelta(seconds=30))
                ).total_seconds()) < 1.0


def test_pause_freezes_the_clock():
    srv = _make_server(speed=0.0)
    _anchor(srv, SIM_EPOCH, 0.0, wall_ago_s=600.0)
    # Ten wall minutes at speed 0 have moved sim time by nothing at all.
    assert abs((srv.sim_now() - SIM_EPOCH).total_seconds()) < 0.01


# ── D.2 staleness ages in sim seconds ───────────────────────────────────

def _receive_hk(srv, sid=1):
    """Drive one HK frame through _process_tm and return its sim stamp."""
    data = MagicMock()
    with patch.object(srv._tm_processor, "_process_hk",
                      return_value={"sid": sid, "params": {0x0101: 50.0}}):
        asyncio.run(srv._process_tm(_pkt(3, 25, b"\x00\x01" + b"\x00" * 40,
                                         cuc_seconds=1.0)))
    return srv._last_tm_frame_ts


def test_tm_receive_timestamp_is_sim_time_not_wall_time():
    srv = _make_server()
    _ws(srv)
    ts = _receive_hk(srv)
    assert abs(ts - SIM_EPOCH.timestamp()) < 2.0, \
        "TM receive timestamp is not on the sim clock"
    assert abs(ts - _time.time()) > 1e8, \
        "TM receive timestamp looks like the host wall clock"


def test_pausing_the_simulator_freezes_every_age():
    """The headline defect: pause used to make all telemetry go stale."""
    srv = _make_server()
    _ws(srv)
    _receive_hk(srv)
    # Simulator paused: 10 real minutes pass, sim time does not move.
    _anchor(srv, SIM_EPOCH, 0.0, wall_ago_s=600.0)
    now = srv.sim_now_ts()
    assert now - srv._last_tm_frame_ts < 1.0, \
        "a paused simulator is aging telemetry on the wall clock"
    assert srv._param_cache[0x0101]["last_update_ts"] == pytest.approx(now, abs=1.0)


def test_accelerating_the_simulator_ages_telemetry_at_speed():
    """The mirror defect: at speed nothing ever went stale."""
    srv = _make_server()
    _ws(srv)
    _receive_hk(srv)
    # 2 wall seconds at x60 = 120 sim seconds since that frame.
    _anchor(srv, SIM_EPOCH, 60.0, wall_ago_s=2.0)
    age = srv.sim_now_ts() - srv._last_tm_frame_ts
    assert 100.0 < age < 140.0, f"age did not scale with speed: {age}"


def test_hk_sid_goes_silent_on_sim_seconds():
    """SILENT is `interval_s * 2.5 + 1`, and interval_s is a SIM cadence."""
    srv = _make_server()
    _ws(srv)
    _receive_hk(srv, sid=1)
    # Paused: two real minutes, no sim seconds. SID 1 must stay PERIODIC.
    _anchor(srv, SIM_EPOCH, 0.0, wall_ago_s=120.0)
    rows = asyncio.run(srv._handle_hk_structures(MagicMock()))
    body = json.loads(rows.body.decode())
    sid1 = next(r for r in body["structures"] if r["sid"] == 1)
    assert sid1["observed"] == "periodic"
    assert body["age_time_base"] == "sim"

    # Now let sim time run: 1 wall second at x600 is 600 sim seconds.
    _anchor(srv, SIM_EPOCH, 600.0, wall_ago_s=1.0)
    body = json.loads(
        asyncio.run(srv._handle_hk_structures(MagicMock())).body.decode())
    sid1 = next(r for r in body["structures"] if r["sid"] == 1)
    assert sid1["observed"] == "silent"


def test_state_poll_ages_are_never_negative_across_a_backwards_jump():
    srv = _make_server()
    _ws(srv)
    _receive_hk(srv)
    # A breakpoint load moves sim time an hour into the past.
    _anchor(srv, SIM_EPOCH - timedelta(hours=1), 1.0)
    now = srv.sim_now_ts()
    assert max(0.0, now - srv._last_tm_frame_ts) == 0.0


# ── D.3 alarms ──────────────────────────────────────────────────────────

def test_alarm_raise_and_clear_are_stamped_with_sim_time():
    srv = _make_server()
    _ws(srv)
    alarm = asyncio.run(srv._raise_alarm({
        "severity": 4, "subsystem": "eps", "parameter": "0x0101",
        "value": "2.0", "source": "LIMIT",
    }))
    assert abs(alarm["timestamp"] - SIM_EPOCH.timestamp()) < 2.0
    assert alarm["time_base"] == "sim"
    # The archive row carries the same stamp, explicitly.
    _, kwargs = srv._archive.store_alarm.call_args
    assert kwargs["utc_epoch"] == alarm["timestamp"]

    # Ten sim minutes later the excursion clears.
    _anchor(srv, SIM_EPOCH + timedelta(minutes=10), 1.0)
    asyncio.run(srv._clear_ground_alarm(0x0101, {"value": 50.0}))
    cleared = next(a for a in srv._alarm_journal if a.get("cleared"))
    assert cleared["cleared_ts"] - cleared["timestamp"] == pytest.approx(600, abs=5)


# ── D.4 the archive ─────────────────────────────────────────────────────

def test_every_archive_writer_requires_an_explicit_epoch(tmp_path):
    """No `time.time()` defaults: forgetting the epoch must be a TypeError."""
    a = TMArchive(tmp_path / "a.db")
    a.open()
    with pytest.raises(TypeError):
        a.store_parameters({"eps.bat_soc": 50.0})
    with pytest.raises(TypeError):
        a.store_event(1, 1, "eps", "x")
    with pytest.raises(TypeError):
        a.store_alarm({"severity": 1})
    with pytest.raises(TypeError):
        a.store_command(seq=1)
    with pytest.raises(TypeError):
        a.prune()
    with pytest.raises(TypeError):
        a.get_playback_data("eps")
    a.close()


def test_new_archive_declares_schema_version_and_sim_time_base(tmp_path):
    a = TMArchive(tmp_path / "a.db")
    a.open()
    assert a.schema_version == SCHEMA_VERSION
    assert a.time_base == TIME_BASE_SIM
    assert a.legacy_time_base is False
    assert a.meta()["sim_time"] is True
    a.close()


def test_a_pre_migration_database_is_flagged_not_silently_reused(tmp_path):
    """A v1 file holds wall-clock epochs; it must not be read as sim time."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE tm_parameters (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " utc_epoch REAL NOT NULL, param_name TEXT NOT NULL, value REAL NOT NULL);")
    conn.execute("INSERT INTO tm_parameters (utc_epoch, param_name, value) "
                 "VALUES (?, ?, ?)", (_time.time(), "eps.bat_soc", 50.0))
    conn.commit()
    conn.close()

    a = TMArchive(path)
    a.open()
    assert a.schema_version == 1
    assert a.time_base == TIME_BASE_WALL
    assert a.legacy_time_base is True
    assert "LEGACY" in a.meta()["note"]
    # The v2 columns are added so the file still opens, but the old rows keep
    # their meaning — which is exactly what `time_base` records.
    a.store_parameters({"eps.bat_soc": 51.0}, utc_epoch=SIM_EPOCH.timestamp())
    assert len(a.query_parameters("eps.bat_soc")) == 2
    a.close()

    # Re-opening reads the recorded verdict back rather than re-guessing.
    b = TMArchive(path)
    b.open()
    assert b.time_base == TIME_BASE_WALL
    b.close()


def test_archived_parameters_land_on_sim_time_with_the_packet_cuc(tmp_path):
    srv = _make_server(tmp_path)
    _ws(srv)
    srv._last_obc_cuc = 826387200.5
    srv._archive_state_snapshot({"eps": {"bat_soc": 50.0}})
    rows = srv._archive.query_parameters("eps.bat_soc")
    assert rows
    assert abs(rows[-1]["utc_epoch"] - SIM_EPOCH.timestamp()) < 2.0
    with sqlite3.connect(tmp_path / "tm_archive.db") as c:
        cuc = c.execute("SELECT obc_cuc FROM tm_parameters "
                        "WHERE param_name='eps.bat_soc'").fetchone()[0]
    assert cuc == pytest.approx(826387200.5)


def test_archive_endpoints_declare_the_time_base(tmp_path):
    srv = _make_server(tmp_path)
    req = MagicMock()
    req.query = {}
    body = json.loads(asyncio.run(srv._handle_archive_events(req)).body.decode())
    assert body["archive"]["time_base"] == TIME_BASE_SIM


# ── D.5 command history ─────────────────────────────────────────────────

def test_command_history_is_sim_time():
    srv = _make_server()
    srv._record_command({"seq": 1, "name": "EPS_LINE_ON", "service": 8,
                         "subtype": 1, "state": "SENT"})
    entry = srv._verification_log[0]
    assert abs(entry["timestamp"] - SIM_EPOCH.timestamp()) < 2.0
    _, kwargs = srv._archive.store_command.call_args
    assert kwargs["utc_epoch"] == entry["timestamp"]
    assert kwargs["sched_cuc"] is None      # immediate TC: no on-board tag


def test_s11_command_records_the_onboard_cuc_it_was_scheduled_for(tmp_path):
    srv = _make_server(tmp_path)
    exec_cuc = 826387200
    srv._record_command({"seq": 9, "name": "S11.4[x]", "service": 11,
                         "subtype": 4, "state": "SENT",
                         "sched_cuc": float(exec_cuc)}, data_hex="00")
    row = next(r for r in srv._archive.query_commands() if r["seq"] == 9)
    assert row["sched_cuc"] == pytest.approx(exec_cuc)
    # The two clocks are separate columns, and they disagree — that is the
    # point: one is ground sim time, one is the spacecraft's clock.
    assert abs(row["utc_epoch"] - row["sched_cuc"]) > 1e8


# ── D.6 the 0x0309 decode ───────────────────────────────────────────────

def test_obc_time_is_decoded_on_j2000_without_the_1e9_heuristic():
    srv = _make_server()
    target = datetime(2030, 6, 1, 12, 0, 5, tzinfo=timezone.utc)
    coarse = int(datetime_to_cuc_seconds(target))
    # ~9.6e8 — the old `> 1e9` guard rejected exactly this, which is why
    # sc_time was permanently None.
    assert coarse < 1e9
    srv._param_cache[0x0309] = {"value": coarse, "last_update_ts": 0, "sid": 4}

    ts = srv._compute_time_state()
    assert ts["sc_time"] is not None, "the 1e9 heuristic is still rejecting it"
    assert ts["sc_time"].startswith("2030-06-01T12:00:05")


def test_a_unix_timestamp_in_0x0309_no_longer_reads_as_a_sane_time():
    """The old code only 'worked' once a GPS sync wrote a Unix value."""
    srv = _make_server()
    srv._param_cache[0x0309] = {"value": _time.time(), "last_update_ts": 0,
                                "sid": 4}
    ts = srv._compute_time_state()
    # Decoded on J2000, a Unix value is ~30 years in the future — visible as a
    # huge offset rather than silently rendering as "correct".
    assert ts["time_correlation"]["offset_state"] == "alarm"


# ── D.9 the correlation block ───────────────────────────────────────────

def test_correlation_block_carries_all_five_operator_facts():
    srv = _make_server()
    sim = srv.sim_now()
    coarse = int(datetime_to_cuc_seconds(sim)) + 7      # OBC 7 s fast
    for name, value in (("obdh.obc_time_fine", 32768),
                        ("obdh.time_ref", 3),
                        ("obdh.time_since_sync_s", 3600)):
        pid = srv._param_id_by_name(name)
        assert pid is not None, f"{name} missing from parameters.yaml"
        srv._param_cache[pid] = {"value": value, "last_update_ts": 0, "sid": 4}
    srv._param_cache[0x0309] = {"value": coarse, "last_update_ts": 0, "sid": 4}

    tc = srv._compute_time_state()["time_correlation"]
    assert tc["sim_utc"] and tc["sc_utc"]
    assert tc["time_ref"] == 3 and tc["time_ref_name"] == "HOLDOVER"
    assert tc["time_since_sync_s"] == 3600
    assert tc["never_synced"] is False
    assert tc["sc_cuc_fine"] == 32768 and tc["cuc_fine_available"] is True
    # Offset is on-board minus sim, sub-second precision included.
    assert tc["offset_s"] == pytest.approx(7.5, abs=1.5)
    assert tc["offset_state"] == "warning"          # >= 5 s, < 30 s


def test_never_synced_is_reported_from_the_saturated_value():
    srv = _make_server()
    pid = srv._param_id_by_name("obdh.time_since_sync_s")
    srv._param_cache[pid] = {"value": 0xFFFFFFFF, "last_update_ts": 0, "sid": 4}
    tc = srv._compute_time_state()["time_correlation"]
    assert tc["never_synced"] is True
    assert tc["time_since_sync_s"] is None


def test_state_publishes_ground_utc_for_the_browser_time_widgets():
    """`state.ground_utc` was read by the UI but never actually published."""
    srv = _make_server()
    ts = srv._compute_time_state()
    assert ts["ground_utc"] == ts["sim_time"]


# ── D.4/D.5 conversions go through the correlation ──────────────────────

def test_s11_time_tag_uses_the_obc_clock_including_its_error():
    srv = _make_server()
    sim = srv.sim_now()
    coarse = int(datetime_to_cuc_seconds(sim)) + 40      # OBC 40 s fast
    srv._param_cache[0x0309] = {"value": coarse, "last_update_ts": 0, "sid": 4}
    target = sim + timedelta(minutes=10)
    cuc = srv._utc_to_obc_cuc(target)
    # 600 s ahead ON THE OBC CLOCK, i.e. 40 s of error carried through: the
    # command will fire 40 s early in sim time, and that must NOT be corrected.
    assert cuc == pytest.approx(coarse + 600, abs=2)
    # And the inverse round-trips back to the sim time it will actually fire.
    back = datetime.fromisoformat(srv._obc_cuc_to_utc(cuc))
    assert abs((back - target).total_seconds()) < 3.0


# ── D.7 / D.8 the browser ───────────────────────────────────────────────

def _js(snippet: str):
    if not HARNESS.exists():
        pytest.skip("node harness not present")
    import shutil
    if shutil.which("node") is None:
        pytest.skip("node not available")
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(snippet)
        path = f.name
    out = subprocess.run(["node", str(HARNESS), str(INDEX), path],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_browser_encodes_cuc_on_j2000_not_unix():
    """The live bug: an S11 tag from the browser was ~9.5e8 s in the future."""
    res = _js("""
      const ms = Date.UTC(2026, 2, 10, 4, 0, 0);
      return { coarse: utcToCuc(ms).coarse,
               unix: Math.floor(ms / 1000),
               epoch: new Date(CUC_EPOCH_UNIX_MS).toISOString() };
    """)
    expected = int(datetime_to_cuc_seconds(datetime(2026, 3, 10, 4, 0, 0)))
    assert res["coarse"] == expected
    assert res["epoch"] == "2000-01-01T12:00:00.000Z"
    # The old behaviour, for the record: it emitted the Unix value.
    assert res["unix"] - res["coarse"] == 946728000


def test_browser_cuc_helper_emits_coarse_and_fine():
    res = _js("""
      const c = utcToCuc(CUC_EPOCH_UNIX_MS + 1500);
      return { coarse: c.coarse, fine: c.fine, hex4: c.hex4, hex6: c.hex6,
               back: cucToUtcMs(c.coarse, c.fine) - CUC_EPOCH_UNIX_MS };
    """)
    assert res["coarse"] == 1 and res["fine"] == 32768
    assert res["hex4"] == "00000001"
    assert res["hex6"] == "0000000180 00".replace(" ", "")
    assert res["back"] == pytest.approx(1500)


def test_browser_sim_clock_tracks_server_time_and_speed():
    res = _js("""
      adoptServerClock({sim_time: '2030-06-01T12:00:00+00:00', speed: 0});
      const paused = simNow();
      const wall = Date.now();
      // Speed 0: sim time does not advance no matter what the wall does.
      _simAnchorWallMs -= 60000;
      return { paused, frozen: simNow(), locked: simClockLocked(),
               farFromWall: Math.abs(paused - wall) > 1e11 };
    """)
    assert res["frozen"] == res["paused"], "paused sim clock is still advancing"
    assert res["locked"] is True
    assert res["farFromWall"] is True


def test_browser_s11_tag_is_correlated_to_the_onboard_clock():
    res = _js("""
      adoptServerClock({sim_time: '2030-06-01T12:00:00+00:00', speed: 1});
      state = { time_correlation: { sc_cuc_seconds: 100000.0 } };
      const c = simUtcToObcCuc(simNow() + 600000);
      return { coarse: c.coarse,
               roundTrip: obcCucToSimMs(c.coarse) - simNow() };
    """)
    # 10 minutes ahead of the SPACECRAFT's clock, not of J2000-of-sim-time.
    assert res["coarse"] == pytest.approx(100600, abs=2)
    assert res["roundTrip"] == pytest.approx(600000, abs=3000)


def test_browser_pass_timer_and_ages_no_longer_use_wall_clock():
    """Guard against a `Date.now()` creeping back into a mission display."""
    src = INDEX.read_text()
    body = src.split("MISSION CLOCK", 1)[1]
    # Everything after the clock block: the only legitimate Date.now() uses
    # are inside simNow()/adoptServerClock themselves.
    after = body.split("function utcToCuc", 1)[1]
    assert "Date.now()" not in after, \
        "a mission display is back on the browser wall clock"
