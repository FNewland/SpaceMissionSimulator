"""CUC time — the 9-octet PUS secondary header, epoch wiring, and TM stamping.

Gates TIME_ARCHITECTURE.md Part A:

* ``coarse: uint32`` seconds since J2000 + ``fine: uint16`` in 1/65536 s;
* the secondary header is 9 octets, not 7;
* one epoch, defined in ``mission.yaml`` and only *defaulted* in Python;
* a TM builder with no on-board clock raises rather than stamping wall clock.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from smo_common.protocol import ecss_packet as ep
from smo_common.protocol.ecss_packet import (
    CUC_COARSE_MAX,
    CUC_FINE_TICKS,
    CUC_SIZE,
    TM_SEC_HDR_LEN,
    CucTime,
    SecondaryHeader,
    build_tm_packet,
    cuc_seconds_to_datetime,
    datetime_to_cuc,
    datetime_to_cuc_coarse,
    datetime_to_cuc_seconds,
    decommutate_packet,
    get_time_epoch,
    parse_cuc_time,
    reset_time_epoch,
    set_time_epoch,
)
from smo_common.telemetry.tm_builder import MissingTimeSourceError, TMBuilder

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture(autouse=True)
def _default_epoch():
    """Every test starts and ends on the fallback epoch."""
    reset_time_epoch()
    yield
    reset_time_epoch()


# ── constants ───────────────────────────────────────────────────────────


def test_fine_tick_constant():
    assert CUC_FINE_TICKS == 65536
    assert CUC_SIZE == 6
    assert TM_SEC_HDR_LEN == 9
    assert CUC_COARSE_MAX == 0xFFFFFFFF


# ── CucTime ─────────────────────────────────────────────────────────────


def test_cuc_time_round_trip_seconds():
    cuc = CucTime.from_seconds(1234.5)
    assert cuc.coarse == 1234
    assert cuc.fine == CUC_FINE_TICKS // 2
    assert cuc.to_seconds() == 1234.5


def test_cuc_time_sub_tick_precision():
    """One tick is ~15.26 us and survives a pack/unpack round trip."""
    one_tick = 1.0 / CUC_FINE_TICKS
    cuc = CucTime.from_seconds(10 + one_tick)
    assert cuc == CucTime(10, 1)
    assert CucTime.unpack(cuc.pack()).to_seconds() == pytest.approx(
        10 + one_tick, abs=one_tick / 2)


def test_cuc_time_pack_is_six_bytes_big_endian():
    assert CucTime(1, 2).pack() == b"\x00\x00\x00\x01\x00\x02"
    assert CucTime(CUC_COARSE_MAX, CUC_FINE_TICKS - 1).pack() == b"\xff" * 6


def test_cuc_time_fine_wraps_at_65536():
    """fine == CUC_FINE_TICKS is one whole second, not an overflow."""
    assert CucTime(5, CUC_FINE_TICKS) == CucTime(6, 0)
    assert CucTime(5, CUC_FINE_TICKS + 3) == CucTime(6, 3)
    assert CucTime(0, 3 * CUC_FINE_TICKS) == CucTime(3, 0)
    # and from_seconds must never emit an out-of-range fine after rounding
    almost = 7.0 - (0.4 / CUC_FINE_TICKS)
    assert CucTime.from_seconds(almost) == CucTime(7, 0)


def test_cuc_time_negative_fine_carries_down():
    assert CucTime(5, -1) == CucTime(4, CUC_FINE_TICKS - 1)


def test_cuc_time_before_j2000_is_representable_in_memory():
    """Negative times work in memory; the uint32 wire field is what refuses."""
    cuc = CucTime.from_seconds(-1.5)
    assert cuc.coarse == -2
    assert cuc.fine == CUC_FINE_TICKS // 2
    assert cuc.to_seconds() == -1.5
    assert cuc.to_datetime() == get_time_epoch() - timedelta(seconds=1.5)
    with pytest.raises(ValueError):
        cuc.pack()


def test_cuc_time_beyond_uint32_refuses_to_pack():
    with pytest.raises(ValueError):
        CucTime(CUC_COARSE_MAX + 1, 0).pack()
    # the last representable second still packs
    assert len(CucTime(CUC_COARSE_MAX, 0).pack()) == CUC_SIZE


def test_cuc_time_uint32_coarse_range_round_trips():
    for coarse in (0, 1, 2 ** 31 - 1, 2 ** 31, CUC_COARSE_MAX):
        cuc = CucTime(coarse, 12345)
        assert CucTime.unpack(cuc.pack()) == cuc


def test_cuc_time_datetime_round_trip():
    dt = datetime(2026, 3, 10, 4, 5, 6, 500000)
    cuc = CucTime.from_datetime(dt)
    assert abs((cuc.to_datetime() - dt).total_seconds()) < 1.0 / CUC_FINE_TICKS


def test_cuc_time_from_aware_datetime_matches_naive_utc():
    aware = datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 3, 10, 4, 0, 0)
    assert CucTime.from_datetime(aware) == CucTime.from_datetime(naive)


def test_cuc_time_unpack_short_buffer_raises():
    with pytest.raises(ValueError):
        CucTime.unpack(b"\x00" * (CUC_SIZE - 1))


# ── legacy helper compatibility ─────────────────────────────────────────


def test_parse_cuc_time_single_argument_unchanged():
    assert parse_cuc_time(0) == get_time_epoch()
    assert parse_cuc_time(86400) == get_time_epoch() + timedelta(days=1)


def test_parse_cuc_time_with_fine():
    half = parse_cuc_time(10, CUC_FINE_TICKS // 2)
    assert half == get_time_epoch() + timedelta(seconds=10.5)


def test_datetime_to_cuc_returns_coarse_fine_pair():
    dt = get_time_epoch() + timedelta(seconds=42.25)
    assert datetime_to_cuc(dt) == (42, CUC_FINE_TICKS // 4)


def test_legacy_whole_second_wrapper_matches_old_behaviour():
    """``datetime_to_cuc_coarse`` == the pre-migration ``datetime_to_cuc``."""
    for offset in (0.0, 1.0, 42.25, 86399.999, 1e6):
        dt = get_time_epoch() + timedelta(seconds=offset)
        legacy = int((dt - get_time_epoch()).total_seconds())
        assert datetime_to_cuc_coarse(dt) == legacy


def test_datetime_to_cuc_seconds_is_full_precision():
    dt = get_time_epoch() + timedelta(seconds=42.25)
    assert datetime_to_cuc_seconds(dt) == pytest.approx(42.25)
    assert cuc_seconds_to_datetime(42.25) == dt


# ── epoch wiring ────────────────────────────────────────────────────────


def test_mission_yaml_epoch_matches_python_fallback():
    """Gate: mission.yaml is the source, the literal is only the fallback."""
    raw = yaml.safe_load((CONFIG_DIR / "mission.yaml").read_text())
    yaml_epoch = datetime.fromisoformat(str(raw["time_epoch"]).replace("Z", "+00:00"))
    if yaml_epoch.tzinfo is not None:
        yaml_epoch = yaml_epoch.astimezone(timezone.utc).replace(tzinfo=None)
    assert yaml_epoch == ep.TIME_EPOCH_DEFAULT, (
        "configs/eosat1/mission.yaml time_epoch and "
        "ecss_packet.TIME_EPOCH_DEFAULT disagree — the wire epoch would "
        "depend on whether a mission config had been loaded"
    )


def test_load_mission_config_publishes_the_epoch():
    from smo_common.config.loader import load_mission_config

    set_time_epoch(datetime(1999, 1, 1))
    assert get_time_epoch() == datetime(1999, 1, 1)
    mission = load_mission_config(CONFIG_DIR)
    assert get_time_epoch() == datetime.fromisoformat(mission.time_epoch)
    assert ep.TIME_EPOCH == get_time_epoch()


def test_conversions_follow_the_configured_epoch():
    set_time_epoch("2010-01-01T00:00:00")
    assert parse_cuc_time(0) == datetime(2010, 1, 1)
    assert datetime_to_cuc(datetime(2010, 1, 2)) == (86400, 0)
    reset_time_epoch()
    assert get_time_epoch() == ep.TIME_EPOCH_DEFAULT


def test_set_time_epoch_accepts_aware_iso_and_datetime():
    assert set_time_epoch("2000-01-01T12:00:00Z") == ep.TIME_EPOCH_DEFAULT
    assert set_time_epoch(datetime(2001, 2, 3, 4, 5, 6)) == datetime(2001, 2, 3, 4, 5, 6)
    with pytest.raises(TypeError):
        set_time_epoch(12345)


# ── secondary header ────────────────────────────────────────────────────


def test_secondary_header_is_nine_bytes():
    hdr = SecondaryHeader(service=3, subtype=25, cuc_coarse=0x01020304,
                          cuc_fine=0x0506)
    packed = hdr.pack()
    assert len(packed) == 9
    assert packed == bytes([0x12, 3, 25, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
    back = SecondaryHeader.unpack(packed)
    assert (back.service, back.subtype) == (3, 25)
    assert back.cuc_coarse == 0x01020304
    assert back.cuc_fine == 0x0506


def test_secondary_header_cuc_time_property_is_whole_seconds():
    hdr = SecondaryHeader(cuc_coarse=100, cuc_fine=CUC_FINE_TICKS // 2)
    assert hdr.cuc_time == 100          # legacy call sites keep working
    assert hdr.cuc_seconds == 100.5     # full precision
    assert hdr.cuc == CucTime(100, CUC_FINE_TICKS // 2)
    hdr.cuc_time = 200
    assert hdr.cuc_coarse == 200
    hdr.cuc_seconds = 300.25
    assert (hdr.cuc_coarse, hdr.cuc_fine) == (300, CUC_FINE_TICKS // 4)


def test_secondary_header_legacy_cuc_time_kwarg():
    hdr = SecondaryHeader(service=1, subtype=1, cuc_time=77)
    assert hdr.cuc_coarse == 77
    assert hdr.cuc_fine == 0


def test_secondary_header_short_buffer_raises():
    with pytest.raises(ValueError):
        SecondaryHeader.unpack(b"\x12\x03\x19\x00\x00\x00\x01")  # old 7 bytes


# ── whole packet ────────────────────────────────────────────────────────


def test_tm_packet_round_trip_with_fine_precision():
    pkt = build_tm_packet(apid=1, service=3, subtype=25, data=b"\xaa\xbb",
                          seq_count=7, cuc_time=1000, cuc_fine=1234)
    parsed = decommutate_packet(pkt)
    assert parsed is not None and parsed.crc_valid
    assert parsed.secondary.cuc_coarse == 1000
    assert parsed.secondary.cuc_fine == 1234
    assert parsed.secondary.cuc_seconds == 1000 + 1234 / CUC_FINE_TICKS
    assert parsed.data_field == b"\xaa\xbb"


def test_tm_packet_accepts_a_cuc_time_object():
    pkt = build_tm_packet(apid=1, service=5, subtype=2, data=b"",
                          cuc_time=CucTime(9, 9))
    parsed = decommutate_packet(pkt)
    assert parsed.secondary.cuc == CucTime(9, 9)
    assert parsed.data_field == b""


def test_tm_data_field_offset_moved_by_two():
    """The data field now starts at octet 15, not 13."""
    pkt = build_tm_packet(apid=1, service=17, subtype=2, data=b"\x01\x02\x03")
    assert pkt[6 + TM_SEC_HDR_LEN:6 + TM_SEC_HDR_LEN + 3] == b"\x01\x02\x03"
    assert decommutate_packet(pkt).data_field == b"\x01\x02\x03"


# ── TM builder time-source contract ─────────────────────────────────────


def test_builder_without_time_source_raises():
    b = TMBuilder(apid=1)
    with pytest.raises(MissingTimeSourceError):
        b.build_hk_packet(1, {}, [(0x0101, "H", 1)])
    with pytest.raises(MissingTimeSourceError):
        b.build_verification_acceptance(1, 1)
    with pytest.raises(MissingTimeSourceError):
        b.build_event_packet(1, 1, "x", {})
    with pytest.raises(MissingTimeSourceError):
        b.build_connection_test_report()


def test_builder_never_falls_back_to_wall_clock(monkeypatch):
    """A missing clock is a bug: nothing may stamp the host time instead."""
    b = TMBuilder(apid=1)
    try:
        b.build_connection_test_report()
    except MissingTimeSourceError:
        pass
    else:  # pragma: no cover - the point of the test
        pytest.fail("builder silently invented a timestamp")


def test_builder_time_source_float_keeps_sub_second():
    b = TMBuilder(apid=1, time_source=lambda: 1000.5)
    parsed = decommutate_packet(b.build_connection_test_report())
    assert parsed.secondary.cuc_coarse == 1000
    assert parsed.secondary.cuc_fine == CUC_FINE_TICKS // 2
    assert parsed.secondary.cuc_seconds == 1000.5


def test_builder_time_source_may_return_cuc_time():
    b = TMBuilder(apid=1, time_source=lambda: CucTime(42, 7))
    parsed = decommutate_packet(b.build_connection_test_report())
    assert parsed.secondary.cuc == CucTime(42, 7)


def test_builder_time_source_bad_type_raises():
    b = TMBuilder(apid=1, time_source=lambda: "now")
    with pytest.raises(TypeError):
        b.build_connection_test_report()


def test_set_time_source_completes_a_bare_builder():
    b = TMBuilder(apid=1)
    b.set_time_source(lambda: 5.0)
    assert decommutate_packet(b.build_connection_test_report()).secondary.cuc_coarse == 5


def test_verification_packets_carry_onboard_time():
    """TIME_ARCHITECTURE Part C.4: S1 must not go out with cuc == 0."""
    b = TMBuilder(apid=1, time_source=lambda: 4242.25)
    for pkt in (b.build_verification_acceptance(1, 2),
                b.build_verification_completion(1, 2),
                b.build_verification_failure(1, 2, 7, "no"),
                b.build_execution_failure(1, 2, 7, "no")):
        sec = decommutate_packet(pkt).secondary
        assert sec.service == 1
        assert sec.cuc_seconds == 4242.25


def test_pack_tm_without_cuc_reads_the_clock():
    """The default is "ask the clock", not "stamp zero"."""
    b = TMBuilder(apid=1, time_source=lambda: 111.0)
    sec = decommutate_packet(b._pack_tm(service=6, subtype=6, data=b"")).secondary
    assert sec.cuc_coarse == 111


def test_event_packet_body_and_header_agree():
    b = TMBuilder(apid=1, time_source=lambda: 900.75)
    parsed = decommutate_packet(b.build_event_packet(0x0102, 2, "hi", {}))
    from smo_common.protocol.pus_services import EventData
    ev = EventData.parse(parsed.data_field)
    assert ev.event_id == 0x0102
    assert ev.aux_text == "hi"
    assert ev.cuc_time == 900                       # body: whole seconds
    assert parsed.secondary.cuc_seconds == 900.75   # header: full precision


def test_time_report_body_is_coarse_plus_fine():
    b = TMBuilder(apid=1, time_source=lambda: 0.0)
    parsed = decommutate_packet(b.build_time_report(CucTime(1234, 567)))
    assert parsed.secondary.service == 9 and parsed.secondary.subtype == 2
    assert len(parsed.data_field) == CUC_SIZE
    assert CucTime.unpack(parsed.data_field) == CucTime(1234, 567)
    assert parsed.secondary.cuc == CucTime(1234, 567)
