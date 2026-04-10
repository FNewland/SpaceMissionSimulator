"""End-to-end test: simulator builds an HK packet, MCS decoder unpacks it,
parameters appear in MCSServer._param_cache with correct values.

This is the test that prior fix passes did NOT add. Two earlier "fixes"
to the MCS TM ingest path claimed to populate _param_cache but never
actually decoded HK packets — _param_cache was permanently empty and
/api/state always returned `stale: True`. This test exists so that
regression can never happen silently again.
"""
import asyncio
from pathlib import Path

import pytest

from smo_common.telemetry.tm_builder import TMBuilder
from smo_common.protocol.ecss_packet import decommutate_packet
from smo_common.config.loader import load_hk_structures
from smo_mcs.server import MCSServer


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs" / "eosat1"


def _build_eps_hk_packet(values: dict[int, float]) -> bytes:
    """Build an SID 1 (EPS) HK packet using the same code path the engine uses."""
    hk_defs = load_hk_structures(CONFIG_DIR)
    eps_def = next(h for h in hk_defs if h.sid == 1)
    structure = [(p.param_id, p.pack_format, p.scale) for p in eps_def.parameters]
    builder = TMBuilder(apid=100)
    # Default any missing params to 0 so the pack succeeds
    full_params = {pid: values.get(pid, 0.0) for pid, _, _ in structure}
    pkt = builder.build_hk_packet(sid=1, params=full_params, hk_structure=structure)
    assert pkt is not None
    return pkt


@pytest.mark.asyncio
async def test_hk_packet_round_trip_populates_mcs_cache():
    """The acceptance test for the third false-green fix.

    Build a real EPS HK packet with known values, hand it to MCSServer's
    own _process_tm, then assert _param_cache contains those exact values
    (after scale inversion) and that /api/state would no longer be stale.
    """
    server = MCSServer(config_dir=CONFIG_DIR)

    # Known input values for a handful of EPS params
    inputs = {
        0x0100: 7.42,    # bat_voltage  V    (scale 100, format H)
        0x0101: 83.5,    # bat_soc      %    (scale 100, format H)
        0x0102: 12.3,    # bat_temp     C    (scale 100, format h)
        0x0105: 28.10,   # bus_voltage  V    (scale 100, format H)
        0x0107: 41.0,    # power_gen    W    (scale 10,  format H)
    }
    raw = _build_eps_hk_packet(inputs)
    parsed = decommutate_packet(raw)
    assert parsed is not None and parsed.secondary is not None
    assert parsed.secondary.service == 3 and parsed.secondary.subtype == 25

    # Hand to the MCS exactly the way _tm_receive_loop does
    await server._process_tm(parsed)

    # 1. Cache populated
    assert len(server._param_cache) > 0, (
        "MCS _param_cache is empty after a valid HK packet — the decode "
        "path is not wired up. This is the third false green pattern."
    )

    # 2. Decoded values match inputs (modulo scale rounding to 2 dp)
    for pid, expected in inputs.items():
        assert pid in server._param_cache, f"param 0x{pid:04X} missing from cache"
        got = server._param_cache[pid]["value"]
        assert abs(got - expected) < 0.02, (
            f"param 0x{pid:04X}: expected {expected}, got {got}"
        )

    # 3. Liveness timestamp set
    assert server._last_tm_frame_ts is not None
    assert server._last_tm_frame_ts > 0

    # 4. Subsystem-grouped view (mirrors what /api/state returns)
    grouped: dict = {}
    for pid, entry in server._param_cache.items():
        meta = server._param_meta.get(pid)
        if meta and meta["subsystem"]:
            grouped.setdefault(meta["subsystem"], {})[meta["short_key"]] = entry["value"]
    assert "eps" in grouped, "subsystem grouping for eps missing"
    assert "bat_voltage" in grouped["eps"]
    assert abs(grouped["eps"]["bat_voltage"] - 7.42) < 0.02
    assert abs(grouped["eps"]["bat_soc"] - 83.5) < 0.02


@pytest.mark.asyncio
async def test_unknown_sid_does_not_corrupt_cache():
    """A packet for an unknown SID must not crash and must not poison the cache."""
    server = MCSServer(config_dir=CONFIG_DIR)
    builder = TMBuilder(apid=100)
    # Forge an SID-99 HK packet with an empty payload after the SID header
    import struct
    fake_data = struct.pack(">H", 99)
    pkt = builder._pack_tm(service=3, subtype=25, data=fake_data, cuc=0)
    parsed = decommutate_packet(pkt)
    assert parsed is not None

    await server._process_tm(parsed)
    # Liveness updated, cache untouched
    assert server._last_tm_frame_ts is not None
    assert server._param_cache == {}


@pytest.mark.asyncio
async def test_s20_param_value_report_populates_cache():
    """S20.2 on-demand parameter reports must also land in _param_cache."""
    server = MCSServer(config_dir=CONFIG_DIR)
    builder = TMBuilder(apid=100)
    pkt = builder.build_param_value_report(param_id=0x0143, value=1.234)
    parsed = decommutate_packet(pkt)
    assert parsed is not None

    await server._process_tm(parsed)
    assert 0x0143 in server._param_cache
    assert abs(server._param_cache[0x0143]["value"] - 1.234) < 1e-3
    assert server._param_cache[0x0143]["sid"] == -1
