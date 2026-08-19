"""2026-08d TZ-1 — an S11 time tag must not depend on the host's time zone.

THE FIELD REPORT. "S11 time tags do not align with the planner window... the
planner is running on a machine set to UTC rather than EST, which explains the
time tag issue — the system needs to recognise what time the planner is using
and not do any offset if it is in UTC."

THE ROOT CAUSE. The MCS operator page's S11 scheduler resolved its "Execution
UTC (ISO 8601)" box with a bare ``new Date(txt)``. ECMAScript defines that,
for a date-time string carrying NO offset, as **browser-local** — so
``2026-03-10T12:34:56`` (exactly what a field labelled UTC invites an operator
to type) became 12:34:56 EST = 17:34:56 UTC on an EST host, and 12:34:56 IST =
07:04:56 UTC on a half-hour-offset host. Measured before the fix:

    typed 2026-03-10T12:34:56   UTC             -> CUC 826418096
                                America/New_York-> CUC 826432496  (+5 h)
                                Asia/Kolkata    -> CUC 826398296  (-5 h 30)

"No offset if it is in UTC" is precisely the shape of the bug: a UTC host has
a zero offset, so it alone produced the right tag and the defect was invisible
on the machine that mattered. The "NOW" button prefills with ``toISOString()``,
which carries a Z, so prefilled values always survived — only a TYPED time was
wrong. This is the same defect class as the planner's Add-Activity field
(fixed earlier, see the block above ``openModal`` in the planner's index.html);
the S11 widget was the surviving instance of it.

THE RULE. Everything on the wire and in the API is UTC
(``docs/plan_2026-08c/TIME_ARCHITECTURE.md``). A zone-less string is UTC. The
only place the browser's zone may appear is a clearly-labelled display
convenience (the planner's "= <local time>" hint under the start-time field),
and it never feeds back into a planned time.

WHAT IS ASSERTED HERE, in three zones — UTC, EST (whole-hour) and IST
(half-hour, which catches an implementation that assumes integer-hour offsets):

  1. the browser widget resolves the same typed time to the same CUC;
  2. an explicit offset is still honoured (the operator said what they meant);
  3. the whole Python path — planner activity ``start_time`` -> planner S11
     upload -> MCS ``_utc_to_obc_cuc`` -> the TC(11,4) time tag — yields an
     identical tag.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).parent / "js" / "mcs_harness.js"
CONFIG_DIR = str(REPO / "configs" / "eosat1")

#: UTC, a whole-hour western offset, and a HALF-hour eastern one. The last is
#: the one that catches "subtract the hours" implementations.
ZONES = ("UTC", "America/New_York", "Asia/Kolkata")

SIM_NOW = datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)
TYPED_UTC = "2026-03-10T12:34:56"          # NO zone suffix — the defect case
START_TIME_ISO = "2026-03-10T12:34:56+00:00"


# ── 1/2. the browser widget ─────────────────────────────────────────────

def _js(snippet: str, tz: str):
    if not HARNESS.exists():
        pytest.skip("node harness not present")
    if shutil.which("node") is None:
        pytest.skip("node not available")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(snippet)
        path = f.name
    env = dict(os.environ, TZ=tz)
    try:
        out = subprocess.run(["node", str(HARNESS), str(INDEX), path],
                             capture_output=True, text=True, timeout=60,
                             env=env)
    finally:
        os.unlink(path)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


_RESOLVE = """
  adoptServerClock({sim_time: '%s', speed: 0});
  document.getElementById('s11-tt-mode').value = 'utc';
  document.getElementById('s11-tt-utc').value = %s;
  const t = s11ResolveTimeTag();
  return { cuc: t.cuc, utc: t.utc, error: t.error || null,
           zoneOffsetMin: -new Date().getTimezoneOffset() };
"""


def _resolve(value: str, tz: str) -> dict:
    return _js(_RESOLVE % (SIM_NOW.isoformat(), json.dumps(value)), tz)


def test_the_three_hosts_really_are_in_different_zones():
    """Guard the guard: if the harness ignored TZ every assertion below would
    pass vacuously."""
    offsets = {tz: _resolve(TYPED_UTC, tz)["zoneOffsetMin"] for tz in ZONES}
    assert offsets["UTC"] == 0
    assert offsets["America/New_York"] in (-300, -240)   # EST / EDT
    assert offsets["Asia/Kolkata"] == 330                # +05:30, half-hour


def test_typed_zoneless_time_gives_the_same_cuc_in_every_zone():
    results = {tz: _resolve(TYPED_UTC, tz) for tz in ZONES}
    for tz, r in results.items():
        assert r["error"] is None, (tz, r)
    cucs = {tz: r["cuc"] for tz, r in results.items()}
    assert len(set(cucs.values())) == 1, cucs
    # And it is the UTC reading of what was typed, not a local one.
    utcs = {r["utc"] for r in results.values()}
    assert utcs == {"2026-03-10T12:34:56.000Z"}


def test_an_explicit_offset_is_still_honoured():
    """The operator who writes an offset means it — only ZONE-LESS input is
    forced to UTC."""
    for tz in ZONES:
        z = _resolve("2026-03-10T12:34:56Z", tz)
        east = _resolve("2026-03-10T07:34:56-05:00", tz)   # = 12:34:56 UTC
        assert z["error"] is None and east["error"] is None
        assert z["cuc"] == east["cuc"], tz
        assert z["cuc"] == _resolve(TYPED_UTC, tz)["cuc"], tz


def test_relative_and_prefilled_modes_are_unaffected():
    """`rel` runs off the SIM clock and `NOW` prefills an ISO string with a Z;
    both were always zone-independent, and the fix must not change them."""
    snippet = """
      adoptServerClock({sim_time: '%s', speed: 0});
      document.getElementById('s11-tt-mode').value = 'rel';
      document.getElementById('s11-tt-rel').value = '10';
      const rel = s11ResolveTimeTag();
      document.getElementById('s11-tt-mode').value = 'utc';
      s11FillNow();
      const filled = document.getElementById('s11-tt-utc').value;
      return { cuc: rel.cuc, filled: filled,
               refilled: s11ResolveTimeTag().cuc };
    """ % SIM_NOW.isoformat()
    results = {tz: _js(snippet, tz) for tz in ZONES}
    assert len({r["cuc"] for r in results.values()}) == 1, results
    assert len({r["filled"] for r in results.values()}) == 1, results
    assert len({r["refilled"] for r in results.values()}) == 1, results


# ── 3. the Python path: planner activity -> MCS -> TC(11,4) ─────────────

class _FakeRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


def _make_mcs():
    from smo_common.config.schemas import MCSDisplayConfig
    with patch("smo_mcs.server.load_mcs_displays",
               return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}), \
         patch("smo_mcs.server.load_tc_catalog", return_value=[]):
        from smo_mcs.server import MCSServer
        return MCSServer(config_dir="/tmp/fake_config",
                         connect_host="localhost", connect_port=9999,
                         http_port=0)


def _end_to_end_cuc() -> tuple[str, int]:
    """Run planner-upload -> MCS conversion in-process, return (start, cuc)."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer
    from smo_common.protocol.ecss_packet import datetime_to_cuc_coarse
    from smo_planner.server import PlannerServer

    captured: dict = {}

    async def _run():
        app = web.Application()

        async def upload_s11(request):
            captured["body"] = await request.json()
            return web.json_response({"status": "scheduled"})

        app.router.add_post("/api/schedule/upload-s11", upload_s11)
        srv = TestServer(app)
        await srv.start_server()
        try:
            planner = PlannerServer(CONFIG_DIR, time_source="real")
            planner._mcs_url = str(srv.make_url("")).rstrip("/")
            act = planner._scheduler.add_activity("imaging_pass",
                                                  START_TIME_ISO)
            resp = await planner._handle_upload_schedule(
                _FakeRequest({"activity_id": act["id"]}))
            assert resp.status == 200
        finally:
            await srv.close()

    asyncio.run(_run())

    start_time = captured["body"]["start_time"]
    mcs = _make_mcs()
    # Pin the MCS sim clock and give it an on-board clock reading, so the
    # conversion goes through the real time correlation rather than the epoch
    # fallback — that is the path a live upload takes.
    for name in ("get_ground_utc", "get_sim_utc", "sim_now"):
        if hasattr(mcs, name):
            setattr(mcs, name, lambda _w=SIM_NOW: _w)
    mcs._param_cache[0x0309] = {"value": datetime_to_cuc_coarse(SIM_NOW) + 40}

    start_dt = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    return start_time, mcs._utc_to_obc_cuc(start_dt)


def _end_to_end_cuc_in_zone(tz: str) -> dict:
    """Re-run the path in a child process whose TZ is ``tz``."""
    code = (
        "import sys, json;"
        f"sys.path.insert(0, {str(REPO / 'tests')!r});"
        "from test_mcs.test_s11_time_tag_timezone import _end_to_end_cuc;"
        "import time;"
        "s, c = _end_to_end_cuc();"
        "print(json.dumps({'start': s, 'cuc': c, 'tz': time.strftime('%z')}))"
    )
    env = dict(os.environ, TZ=tz, PYTHONPATH=os.pathsep.join(
        [str(REPO / "packages" / p / "src") for p in
         ("smo-common", "smo-simulator", "smo-gateway", "smo-mcs",
          "smo-planner")] + [str(REPO)]))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=180, env=env, cwd=str(REPO))
    assert out.returncode == 0, out.stderr[-4000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_planner_to_s11_time_tag_is_identical_in_every_host_zone():
    """The whole chain, end to end: same plan, three hosts, one time tag."""
    results = {tz: _end_to_end_cuc_in_zone(tz) for tz in ZONES}
    # The hosts really are in different zones.
    assert len({r["tz"] for r in results.values()}) == 3, results
    # The planner ships the planned SIM-time instant unmodified...
    assert {r["start"] for r in results.values()} == {START_TIME_ISO}
    # ...and the MCS turns it into one and only one on-board time tag.
    cucs = {tz: r["cuc"] for tz, r in results.items()}
    assert len(set(cucs.values())) == 1, cucs


def test_the_tag_is_the_correlated_utc_value_not_a_local_one():
    """Sanity: the single value the three hosts agree on is the RIGHT one —
    the UTC instant on the OBC's clock (which here reads 40 s fast)."""
    from smo_common.protocol.ecss_packet import datetime_to_cuc_coarse
    _start, cuc = _end_to_end_cuc()
    expected = datetime_to_cuc_coarse(
        datetime.fromisoformat(START_TIME_ISO)) + 40
    assert cuc == expected
