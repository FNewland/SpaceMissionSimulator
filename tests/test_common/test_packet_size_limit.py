"""No telemetry packet may exceed the mission's 256-octet limit.

Raised 2026-08-21:

    "Bear in mind that a standard ECSS packet is 256 bytes, so I am concerned
    you have SIDs that have too many parameters in them..."

The housekeeping structures turned out to be fine — the largest, AOCS SID 2,
is 152 octets on the wire (174 before the visibility reclassification), well
inside the limit. But the *unbounded* reports were not:

* **TM(12,13)**, report all monitoring definitions, built one packet of
  ``2 + 14 x n`` octets. With the 41 shipped monitors that is **593 octets** —
  2.3x the limit, emitted on every request.
* **TM(11,18)**, the on-board schedule report, is ``12 + detail`` octets per
  entry and overflowed at roughly a dozen time-tagged commands — i.e. on any
  realistic pass upload.
* **TM(19,128)**, report all event-action rules, came to 243 octets with the
  32 shipped rules. It fit, with three octets to spare; the 33rd rule would
  have overflowed it silently.

Reports are now segmented across packets using the CCSDS grouping flags, and
the ground reassembles them. These tests pin both halves — the wire limit and
the reassembly — because a segmented report that the ground cannot rebuild is
worse than an oversized one: it looks healthy and shows a fraction of the table.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
import yaml

from smo_common.telemetry.tm_builder import (
    CRC_LEN,
    MAX_TM_PACKET_OCTETS,
    PRIMARY_HEADER_LEN,
    TMBuilder,
)
from smo_common.protocol.ecss_packet import TM_SEC_HDR_LEN

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"

#: primary + PUS secondary + CRC
PACKET_OVERHEAD = PRIMARY_HEADER_LEN + TM_SEC_HDR_LEN + CRC_LEN
_FMT = {"B": 1, "b": 1, "H": 2, "h": 2, "I": 4, "i": 4, "f": 4, "d": 8}


@pytest.fixture
def builder() -> TMBuilder:
    return TMBuilder(apid=1, time_source=lambda: 1000.0)


# ── housekeeping structures ─────────────────────────────────────────

def _hk_structures():
    return yaml.safe_load(
        (CONFIG / "telemetry" / "hk_structures.yaml").read_text())["structures"]


@pytest.mark.parametrize("sid_def", _hk_structures(),
                         ids=lambda s: f"SID{s['sid']}-{s['name']}")
def test_hk_structure_fits_one_packet(sid_def):
    """The operator's question, answered per structure."""
    params = sid_def.get("parameters") or []
    payload = sum(_FMT.get(str(p.get("pack_format", "H")), 2) for p in params)
    total = PACKET_OVERHEAD + 2 + payload      # +2 for the SID field
    assert total <= MAX_TM_PACKET_OCTETS, (
        f"HK SID {sid_def['sid']} ({sid_def['name']}) packs {len(params)} "
        f"parameters into {total} octets, over the {MAX_TM_PACKET_OCTETS}-octet "
        "limit. Split the structure into two SIDs at different cadences.")


def test_hk_headroom_is_reported_not_assumed():
    """Fail loudly if any structure creeps past 80% of the budget."""
    tight = []
    for s in _hk_structures():
        params = s.get("parameters") or []
        payload = sum(_FMT.get(str(p.get("pack_format", "H")), 2) for p in params)
        total = PACKET_OVERHEAD + 2 + payload
        if total > 0.8 * MAX_TM_PACKET_OCTETS:
            tight.append((s["sid"], s["name"], total))
    assert not tight, (
        f"HK structures within 20% of the packet limit: {tight} — split them "
        "before adding more parameters.")


# ── segmented reports ───────────────────────────────────────────────

def _s12_records(n: int) -> list[bytes]:
    return [struct.pack(">HHBBff", i, 0x0100 + i, 0, 1, 0.0, 100.0)
            for i in range(n)]


def test_the_shipped_monitor_table_is_segmented(builder):
    """41 monitors x 14 octets = 593 in one packet. It must not be one packet."""
    n = len(yaml.safe_load(
        (CONFIG / "monitoring" / "s12_definitions.yaml").read_text())["s12_definitions"])
    pkts = builder.pack_tm_segmented(
        12, 13, prefix=lambda k: struct.pack(">H", k), records=_s12_records(n))
    assert len(pkts) > 1, (
        f"{n} monitoring definitions still fit in one packet — check the "
        "record size, or the limit has been raised")
    for p in pkts:
        assert len(p) <= MAX_TM_PACKET_OCTETS, f"segment is {len(p)} octets"


def test_every_record_survives_segmentation(builder):
    recs = _s12_records(41)
    pkts = builder.pack_tm_segmented(
        12, 13, prefix=lambda k: struct.pack(">H", k), records=recs)
    recovered = sum(struct.unpack(">H", p[PRIMARY_HEADER_LEN + TM_SEC_HDR_LEN:
                                          PRIMARY_HEADER_LEN + TM_SEC_HDR_LEN + 2])[0]
                    for p in pkts)
    assert recovered == len(recs), (
        f"{recovered} of {len(recs)} records reported across the segments")


def test_grouping_flags_follow_ccsds(builder):
    """01 first, 00 continuation, 10 last — and 11 when it fits in one."""
    def flags(p):
        return struct.unpack(">H", p[2:4])[0] >> 14

    many = builder.pack_tm_segmented(
        12, 13, prefix=lambda k: struct.pack(">H", k), records=_s12_records(41))
    assert [flags(p) for p in many][0] == 0b01
    assert [flags(p) for p in many][-1] == 0b10
    assert all(flags(p) == 0b00 for p in many[1:-1])

    one = builder.pack_tm_segmented(
        12, 13, prefix=lambda k: struct.pack(">H", k), records=_s12_records(3))
    assert len(one) == 1 and flags(one[0]) == 0b11, (
        "a report that fits must stay a standalone packet — segmenting it "
        "would change the wire format for every existing receiver")


def test_a_report_that_fits_is_byte_identical_to_the_unsegmented_form(builder):
    """The common case must not change on the wire."""
    recs = _s12_records(3)
    seg = builder.pack_tm_segmented(
        12, 13, prefix=lambda k: struct.pack(">H", k), records=recs)[0]
    body = struct.pack(">H", len(recs)) + b"".join(recs)
    direct = builder._pack_tm(service=12, subtype=13, data=body)
    # The two packets carry different sequence counts (two were built), and
    # the CRC covers that, so compare the packet id and the payload between
    # the primary header and the CRC -- the part that is the report itself.
    assert seg[:2] == direct[:2]
    assert seg[6:-2] == direct[6:-2]


def test_the_schedule_report_keeps_its_field_order(builder):
    """TM(11,18) puts the count FIRST; segmentation must not reorder it."""
    entries = [struct.pack(">HIHBBBB", i, 1000 + i, 1, 8, 1, 0, 0)
               for i in range(30)]
    pkts = builder.pack_tm_segmented(
        11, 18,
        prefix=lambda k: struct.pack(">HBH", k, 1, 42),
        records=entries)
    assert len(pkts) > 1
    off = PRIMARY_HEADER_LEN + TM_SEC_HDR_LEN
    for p in pkts:
        count, enabled, next_id = struct.unpack(">HBH", p[off:off + 5])
        assert enabled == 1 and next_id == 42, (
            "the scheduler header must ride on every segment so each one is "
            "independently meaningful")
        assert count > 0
        assert len(p) <= MAX_TM_PACKET_OCTETS


# ── the call sites, not just the helper ─────────────────────────────
#
# The tests above prove pack_tm_segmented() is correct. They do NOT prove the
# services USE it: reverting a call site to a single _pack_tm() leaves them all
# green. These drive the real dispatcher and measure what it actually emits.

@pytest.fixture(scope="module")
def dispatcher():
    from smo_simulator.engine import SimulationEngine
    return SimulationEngine(config_dir=str(CONFIG))._dispatcher


#: (service, REQUEST subtype, report subtype) — the request subtype is what a
#: ground station actually sends; the report is what comes back.
REPORT_REQUESTS = [(12, 12, 13), (19, 8, 128), (11, 17, 18)]


@pytest.mark.parametrize("service,req,rep", REPORT_REQUESTS,
                         ids=lambda v: str(v))
def test_the_service_emits_only_conformant_packets(dispatcher, service, req, rep):
    """Ask the spacecraft for the whole table and measure what comes back."""
    handler = {12: dispatcher._handle_s12, 19: dispatcher._handle_s19,
               11: dispatcher._handle_s11}[service]
    packets = handler(req, b"") or []
    assert packets, (
        f"TC({service},{req}) returned nothing — the probe is not exercising "
        "the report path, so this test would pass vacuously")
    oversize = [len(p) for p in packets if len(p) > MAX_TM_PACKET_OCTETS]
    assert not oversize, (
        f"TM({service},{rep}) emitted {len(packets)} packet(s), "
        f"{len(oversize)} of them over the {MAX_TM_PACKET_OCTETS}-octet "
        f"limit: {oversize}. The report is not being segmented.")


def test_the_shipped_monitor_report_really_does_segment(dispatcher):
    """The specific defect: 41 monitors must not come back as one packet."""
    packets = dispatcher._handle_s12(12, b"") or []
    assert len(packets) > 1, (
        "TM(12,13) came back as a single packet. With the shipped monitor "
        "table that is ~593 octets — over twice the limit.")
