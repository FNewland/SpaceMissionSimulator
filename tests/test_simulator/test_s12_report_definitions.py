"""TC(12,12) — report the on-board monitoring definitions, TM(12,13).

``ServiceDispatcher._handle_s12`` has implemented 12,3 / 12,4 / 12,12 for some
time and ``tc_catalog.yaml`` catalogues all three (MON_ENABLE_DEF,
MON_DISABLE_DEF, MON_REPORT_DEFS), but ``Engine._check_tc_acceptance`` listed
only ``{1, 2, 6, 7}`` for service 12. Every one of the three was therefore
refused at acceptance with S1.2 "S12.12 is not an implemented subtype", and the
MCS FDIR panel's "Refresh from Spacecraft" button — which sends exactly
TC(12,12) — could never populate the monitoring table.

The report format is the one the ground already decodes,
``smo_common.reporting.decode.decode_s12_definitions``: ``count(u16)`` then
``count`` x ``mon_id(u16) param_id(u16) check_type(u8) enabled(u8) low(f32)
high(f32)``. That is a faithful rendering of the ECSS PUS-C 12,12/12,13
"report/monitoring-definition report" pair — the identifier, the monitored
parameter, the check type, the enable state and the limits — so no hybrid was
invented for the MCS.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
import yaml

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_common.reporting.decode import decode_s12_definitions
from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture()
def engine() -> SimulationEngine:
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")
    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    eng.apply_phase_change(6)
    return eng


def _tm(packets):
    """Decode the (12,13) reply out of a dispatch result."""
    for raw in packets:
        pkt = decommutate_packet(raw)
        if pkt and pkt.secondary and (pkt.secondary.service,
                                      pkt.secondary.subtype) == (12, 13):
            return pkt.data_field
    return None


# ── acceptance ──────────────────────────────────────────────────────

@pytest.mark.parametrize("subtype", [3, 4, 12])
def test_implemented_s12_subtypes_are_accepted(engine, subtype):
    accepted, code = engine._check_tc_acceptance(12, subtype, b'\x00\x00')
    assert accepted, f"S12.{subtype} refused at acceptance (code 0x{code:04X})"


def test_s12_12_is_no_longer_rejected_end_to_end(engine):
    import queue
    raw = build_tc_packet(apid=0x01, service=12, subtype=12, data=b'',
                          seq_count=7)
    engine._dispatch_tc(raw)
    rejections = []
    while True:
        try:
            ev = engine.event_queue.get_nowait()
        except queue.Empty:
            break
        if ev.get("event_id") == SimulationEngine.EV_COMMAND_REJECTED:
            rejections.append(ev)
    assert not rejections, (
        "TC(12,12) was rejected: "
        + "; ".join(r.get("description", "") for r in rejections))


def test_a_genuinely_unimplemented_s12_subtype_is_still_refused(engine):
    accepted, code = engine._check_tc_acceptance(12, 99, b'')
    assert not accepted and code == 0x0002


# ── the report ──────────────────────────────────────────────────────

def test_report_returns_a_12_13_packet(engine):
    data = _tm(engine._dispatcher.dispatch(12, 12, b'', None))
    assert data is not None, "TC(12,12) produced no TM(12,13)"


def test_report_carries_every_configured_definition(engine):
    configured = yaml.safe_load(
        (CONFIG_DIR / "monitoring" / "s12_definitions.yaml").read_text()
    )["s12_definitions"]
    data = _tm(engine._dispatcher.dispatch(12, 12, b'', None))
    count = struct.unpack(">H", data[:2])[0]
    assert count == len(engine._dispatcher._s12_definitions)
    assert count == len(configured), (
        "the report dropped definitions between the config and the wire")


def test_report_decodes_with_the_shared_ground_decoder(engine):
    """No hybrid format: the MCS decoder reads it unmodified."""
    data = _tm(engine._dispatcher.dispatch(12, 12, b'', None))
    records = decode_s12_definitions(data)
    assert len(records) == len(engine._dispatcher._s12_definitions)
    for rec in records:
        for key in ("mon_id", "param_id", "check_type", "enabled",
                    "low_limit", "high_limit"):
            assert key in rec, f"decoded record missing {key}"


def test_report_matches_the_on_board_table(engine):
    d = engine._dispatcher
    records = {r["mon_id"]: r
               for r in decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None)))}
    for mon_id, defn in d._s12_definitions.items():
        rec = records[mon_id]
        assert rec["param_id"] == defn["param_id"]
        assert rec["check_type"] == int(defn["check_type"])
        assert bool(rec["enabled"]) is bool(defn["enabled"])
        assert rec["low_limit"] == pytest.approx(defn["low_limit"], rel=1e-6)
        assert rec["high_limit"] == pytest.approx(defn["high_limit"], rel=1e-6)


def test_report_tracks_an_added_definition(engine):
    d = engine._dispatcher
    before = len(decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None))))
    # TC(12,6) 13-byte form: mon_id, param_id, check_type, low, high
    d.dispatch(12, 6, struct.pack(">HHBff", 0x7001, 0x0101, 0, 10.0, 90.0),
               None)
    records = {r["mon_id"]: r
               for r in decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None)))}
    assert len(records) == before + 1
    added = records[0x7001]
    assert added["param_id"] == 0x0101
    assert added["low_limit"] == pytest.approx(10.0)
    assert added["high_limit"] == pytest.approx(90.0)
    assert added["enabled"] == 1


def test_report_reflects_a_per_definition_disable(engine):
    """12,4 disables one monitor; 12,12 must show it — both were refused."""
    d = engine._dispatcher
    mon_id = sorted(d._s12_definitions)[0]
    assert d.dispatch(12, 4, struct.pack(">H", mon_id), None).error is None
    records = {r["mon_id"]: r
               for r in decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None)))}
    assert records[mon_id]["enabled"] == 0

    assert d.dispatch(12, 3, struct.pack(">H", mon_id), None).error is None
    records = {r["mon_id"]: r
               for r in decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None)))}
    assert records[mon_id]["enabled"] == 1


def test_report_drops_a_deleted_definition(engine):
    d = engine._dispatcher
    mon_id = sorted(d._s12_definitions)[0]
    d.dispatch(12, 7, struct.pack(">H", mon_id), None)
    records = {r["mon_id"]: r
               for r in decode_s12_definitions(_tm(d.dispatch(12, 12, b'', None)))}
    assert mon_id not in records
