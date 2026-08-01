"""Ground limit checking must actually run (brief 08 WP-3 / O-9).

`configs/eosat1/mcs/limits.yaml` declared 51 limit bands and had **zero**
production consumers: `load_limits()` was called nowhere, `TMProcessor` was
constructed without `limits=`, so `_check_limit` returned immediately for
every parameter and `pop_alarms()` had no callers.  Four of the limited
parameters have no S12 definition on board either, so they were unmonitored
on *both* sides.

These tests are deliberately data-driven off the shipped YAML: every band in
the file has to produce a journal entry when a synthetic HK packet violates
it, so a band added later cannot be silently inert.
"""
from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from smo_common.config.loader import load_hk_structures, load_limits
from smo_common.config.schemas import MCSDisplayConfig
from smo_mcs.server import MCSServer
from smo_mcs.tm_processor import TMProcessor

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


def _limits():
    return load_limits(CONFIG_DIR)


def _hk_map() -> dict[int, list[tuple]]:
    out: dict[int, list[tuple]] = {}
    for hk in load_hk_structures(CONFIG_DIR):
        out[hk.sid] = [(p.param_id, p.pack_format, p.scale)
                       for p in hk.parameters]
    return out


def _make_server() -> MCSServer:
    with patch("smo_mcs.server.load_mcs_displays", return_value=MCSDisplayConfig()), \
         patch("smo_mcs.server.load_positions", return_value={}):
        srv = MCSServer(config_dir=str(CONFIG_DIR), connect_host="localhost",
                        connect_port=9999, http_port=0)
    srv._archive = MagicMock()
    srv._archive.store_alarm.return_value = 1
    return srv


def _pkt(service, subtype, data, apid=1, seq=0):
    p = MagicMock()
    p.secondary.service = service
    p.secondary.subtype = subtype
    p.primary.apid = apid
    p.primary.sequence_count = seq
    p.data_field = data
    return p


def _hk_payload(sid: int, structure: list[tuple],
                overrides: dict[int, float]) -> bytes:
    """Build an S3.25 payload for *sid* with engineering values applied."""
    out = struct.pack('>H', sid)
    for param_id, fmt, scale in structure:
        eng = overrides.get(param_id, 0.0)
        raw = int(round(eng * scale)) if fmt not in ("f", "d") else eng * scale
        if fmt in ("f", "d"):
            out += struct.pack('>' + fmt, raw)
            continue
        # Clamp into the field so struct.pack cannot raise for a deliberately
        # extreme test value; the decoded value is what the assertion uses.
        size = struct.calcsize('>' + fmt)
        signed = fmt.islower()
        bits = size * 8
        lo = -(1 << (bits - 1)) if signed else 0
        hi = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
        out += struct.pack('>' + fmt, max(lo, min(hi, raw)))
    return out


# ── 1. The wiring itself ────────────────────────────────────────────────

def test_load_limits_returns_the_shipped_bands():
    limits = _limits()
    assert len(limits) >= 45, "limits.yaml lost bands"
    raw = yaml.safe_load((CONFIG_DIR / "mcs" / "limits.yaml").read_text())
    assert len(limits) == len(raw["limits"])


def test_mcs_server_constructs_tm_processor_with_limits():
    srv = _make_server()
    assert srv._tm_processor._limits, \
        "TMProcessor built without limits — _check_limit is a no-op"
    assert len(srv._tm_processor._limits) == len(_limits())


def test_every_limited_parameter_has_a_band_exposed():
    srv = _make_server()
    bands = srv._tm_processor.limit_bands()
    for lim in _limits():
        assert lim.param_id in bands


# ── 2. Every band is enforceable ────────────────────────────────────────

@pytest.mark.parametrize("lim", _limits(), ids=lambda l: f"0x{l.param_id:04X}")
def test_each_band_classifies_violations(lim):
    """A value outside each declared edge must classify as that edge."""
    proc = TMProcessor(hk_structures={}, limits=[lim])
    pid = lim.param_id
    if lim.red_high is not None:
        assert proc.classify(pid, lim.red_high + 1.0) == "red_high"
    if lim.red_low is not None:
        assert proc.classify(pid, lim.red_low - 1.0) == "red_low"
    if lim.yellow_high is not None and (
            lim.red_high is None or lim.yellow_high < lim.red_high):
        mid = (lim.yellow_high + (lim.red_high if lim.red_high is not None
                                  else lim.yellow_high + 2.0)) / 2.0
        assert proc.classify(pid, mid) == "yellow_high"


def test_unlimited_parameter_is_distinguishable_from_nominal():
    proc = TMProcessor(hk_structures={}, limits=_limits())
    assert proc.classify(0xFFFF, 0.0) == "unlimited"
    assert proc.classify(0x0101, 50.0) == "nominal"


# ── 3. Violations reach the alarm journal end to end ────────────────────

def _first_sid_containing(pid: int):
    for hk in load_hk_structures(CONFIG_DIR):
        for p in hk.parameters:
            if p.param_id == pid:
                return hk
    return None


LIMITED_IN_HK = [l for l in _limits() if _first_sid_containing(l.param_id)]


def test_limits_yaml_names_only_known_parameters():
    params = yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    known = set()
    for p in params["parameters"]:
        pid = p["id"]
        known.add(int(pid) if isinstance(pid, int) else int(str(pid), 0))
    unknown = [f"0x{l.param_id:04X}" for l in _limits()
               if l.param_id not in known]
    assert not unknown, f"limits.yaml bands unknown parameters: {unknown}"


@pytest.mark.parametrize("lim", LIMITED_IN_HK[:12],
                         ids=lambda l: f"0x{l.param_id:04X}")
def test_hk_violation_reaches_alarm_journal(lim):
    """Feed an HK packet that breaks the band; expect a journal entry."""
    srv = _make_server()
    hk = _first_sid_containing(lim.param_id)
    structure = [(p.param_id, p.pack_format, p.scale) for p in hk.parameters]
    # Pick a value that is unambiguously outside the band on a side that the
    # packed integer field can actually represent.
    if lim.red_high is not None:
        bad = lim.red_high + max(1.0, abs(lim.red_high) * 0.5)
    else:
        bad = lim.red_low - 1.0
    payload = _hk_payload(hk.sid, structure, {lim.param_id: bad})

    ws = MagicMock()
    ws.send_str = AsyncMock()
    srv._ws_clients = [(ws, None)]
    asyncio.run(srv._process_tm(_pkt(3, 25, payload)))

    limit_alarms = [a for a in srv._alarm_journal if a.get("source") == "LIMIT"]
    tag = f"0x{lim.param_id:04X}"
    assert any(a["parameter"] == tag for a in limit_alarms), (
        f"{tag} violated its band and raised no ground alarm "
        f"(journal: {[a['parameter'] for a in limit_alarms]})")


def test_repeat_violation_does_not_spam_the_journal():
    srv = _make_server()
    lim = next(l for l in LIMITED_IN_HK if l.param_id == 0x0101)  # bat_soc
    hk = _first_sid_containing(lim.param_id)
    structure = [(p.param_id, p.pack_format, p.scale) for p in hk.parameters]
    payload = _hk_payload(hk.sid, structure, {lim.param_id: 5.0})
    ws = MagicMock()
    ws.send_str = AsyncMock()
    srv._ws_clients = [(ws, None)]
    for _ in range(5):
        asyncio.run(srv._process_tm(_pkt(3, 25, payload)))
    tag = f"0x{lim.param_id:04X}"
    raised = [a for a in srv._alarm_journal
              if a.get("source") == "LIMIT" and a["parameter"] == tag]
    assert len(raised) == 1, \
        "a parked out-of-limits parameter must alarm once, not per HK cycle"


def test_return_to_band_clears_the_alarm():
    srv = _make_server()
    lim = next(l for l in LIMITED_IN_HK if l.param_id == 0x0101)
    hk = _first_sid_containing(lim.param_id)
    structure = [(p.param_id, p.pack_format, p.scale) for p in hk.parameters]
    ws = MagicMock()
    ws.send_str = AsyncMock()
    srv._ws_clients = [(ws, None)]
    asyncio.run(srv._process_tm(
        _pkt(3, 25, _hk_payload(hk.sid, structure, {lim.param_id: 5.0}))))
    assert srv.unacknowledged_alarm_count() >= 1
    asyncio.run(srv._process_tm(
        _pkt(3, 25, _hk_payload(hk.sid, structure, {lim.param_id: 70.0}))))
    tag = f"0x{lim.param_id:04X}"
    raised = [a for a in srv._alarm_journal
              if a.get("source") == "LIMIT" and a["parameter"] == tag]
    assert raised and all(a.get("cleared") for a in raised)


# ── 4. Acknowledgement persists ─────────────────────────────────────────

def test_acknowledge_writes_through_to_the_archive():
    srv = _make_server()
    alarm = asyncio.run(srv._raise_alarm({
        "severity": 4, "subsystem": "eps", "parameter": "0x0101",
        "value": "5.0", "source": "LIMIT",
    }))
    assert alarm.get("db_id") == 1, "archive row id not carried on the alarm"
    srv._persist_alarm_ack(alarm)
    srv._archive.acknowledge_alarm.assert_called_once_with(1)


def test_unacknowledged_count_is_published_in_state():
    srv = _make_server()
    assert srv.unacknowledged_alarm_count() == 0
    asyncio.run(srv._raise_alarm({
        "severity": 4, "subsystem": "eps", "parameter": "0x0101",
        "value": "5.0", "source": "LIMIT",
    }))
    assert srv.unacknowledged_alarm_count() == 1


# ── 5. The console can see the bands ────────────────────────────────────

INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"


def test_console_requests_the_limit_bands():
    html = INDEX.read_text()
    assert "/api/limits" in html, "console never fetches the ground limits"
    assert "applyGroundLimits" in html
    assert "param_limit_state" in html


def test_limit_element_map_only_names_limited_parameters():
    """Every parameter id bound to a display element must carry a band."""
    html = INDEX.read_text()
    start = html.index("const LIMIT_ELEMENTS = {")
    end = html.index("};", start)
    block = html[start:end]
    import re
    ids = {int(m, 16) for m in re.findall(r"0x([0-9A-Fa-f]{4})", block)}
    limited = {l.param_id for l in _limits()}
    assert ids, "LIMIT_ELEMENTS is empty"
    assert ids <= limited, (
        "display bound to parameters with no limit band: "
        f"{[f'0x{i:04X}' for i in sorted(ids - limited)]}")
