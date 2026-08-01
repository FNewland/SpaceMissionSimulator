"""Defects #76-#78 — decoders for the PUS report telemetry the MCS displays.

Every report below used to fall through ``MCSServer._process_tm`` untouched and
land in the raw packet log as an opaque ``S12.13 … (NB)`` line, so no display
could show it. These tests build the wire bytes exactly as
``smo_simulator.service_dispatch`` packs them and assert the parsers invert
them, with particular attention to the two VARIABLE-stride reports — TM(11,18)
and TM(15,14) — where the previous "divide the payload by the record count"
stride guess silently desynchronises as soon as two records differ in length.
"""
import struct

import pytest

from smo_mcs import pus_reports as P


# ── TM(12,9) / TM(12,10) — check transition reports ─────────────────

def _s12_transition_bytes(param_id, value, mon_id, check_type, severity,
                          low, high):
    return struct.pack('>HfHBBff', param_id, value, mon_id, check_type,
                       severity, low, high)


class TestS12Transition:
    def test_full_18_byte_form_round_trips(self):
        raw = _s12_transition_bytes(0x0101, 17.5, 0x8000, 0, 3, 20.0, 100.0)
        assert len(raw) == 18
        out = P.parse_s12_transition(raw, 9)
        assert out["param_id"] == 0x0101
        assert out["value"] == pytest.approx(17.5)
        assert out["mon_id"] == 0x8000
        assert out["check_type"] == 0
        assert out["severity"] == 3
        assert out["low_limit"] == pytest.approx(20.0)
        assert out["high_limit"] == pytest.approx(100.0)
        assert out["transition"] == "out_of_limits"
        assert out["entered"] is True

    def test_subtype_10_is_back_to_nominal(self):
        raw = _s12_transition_bytes(0x0101, 45.0, 0x8000, 0, 1, 20.0, 100.0)
        out = P.parse_s12_transition(raw, 10)
        assert out["transition"] == "back_to_nominal"
        assert out["entered"] is False

    def test_delta_check_type_survives(self):
        raw = _s12_transition_bytes(0x0207, 4.0, 0x8010, 1, 2, -1.0, 1.0)
        out = P.parse_s12_transition(raw, 9)
        assert out["check_type"] == 1

    def test_legacy_6_byte_form_still_parses_without_inventing_fields(self):
        """An older build sends only param_id + value; do not fake the rest."""
        raw = struct.pack('>Hf', 0x0101, 12.25)
        out = P.parse_s12_transition(raw, 9)
        assert out["param_id"] == 0x0101
        assert out["value"] == pytest.approx(12.25)
        for absent in ("mon_id", "check_type", "severity", "low_limit", "high_limit"):
            assert absent not in out

    def test_short_payload_is_rejected(self):
        assert P.parse_s12_transition(b"\x01\x02", 9) is None


# ── TM(12,13) — monitoring definition table ─────────────────────────

def _s12_defs_bytes(records):
    out = struct.pack('>H', len(records))
    for mon_id, param_id, check, enabled, low, high in records:
        out += struct.pack('>HHBBff', mon_id, param_id, check, enabled, low, high)
    return out


class TestS12Definitions:
    RECORDS = [
        (0x8000, 0x0101, 0, 1, 20.0, 100.0),
        (0x8001, 0x0101, 0, 0, 10.0, 100.0),      # second tier on same param
        (0x8002, 0x0207, 1, 1, -0.5, 0.5),        # delta check
    ]

    def test_record_is_14_bytes(self):
        assert len(_s12_defs_bytes(self.RECORDS)) == 2 + 3 * 14

    def test_all_records_decode(self):
        out = P.parse_s12_definitions(_s12_defs_bytes(self.RECORDS))
        assert len(out) == 3
        assert [d["mon_id"] for d in out] == [0x8000, 0x8001, 0x8002]

    def test_two_monitors_on_one_parameter_are_both_kept(self):
        out = P.parse_s12_definitions(_s12_defs_bytes(self.RECORDS))
        on_0101 = [d for d in out if d["param_id"] == 0x0101]
        assert len(on_0101) == 2
        assert {d["low_limit"] for d in on_0101} == {20.0, 10.0}

    def test_enabled_flag_and_limits(self):
        out = P.parse_s12_definitions(_s12_defs_bytes(self.RECORDS))
        assert out[0]["enabled"] is True
        assert out[1]["enabled"] is False
        assert out[2]["check_type"] == 1
        assert out[2]["high_limit"] == pytest.approx(0.5)

    def test_empty_table(self):
        assert P.parse_s12_definitions(struct.pack('>H', 0)) == []

    def test_truncated_payload_stops_cleanly(self):
        raw = _s12_defs_bytes(self.RECORDS)[:-5]
        out = P.parse_s12_definitions(raw)
        assert len(out) == 2       # the complete records, not a garbage third


# ── TM(19,128) — event-action rule table ────────────────────────────

def _s19_defs_bytes(records):
    out = struct.pack('>H', len(records))
    for ea_id, kind, trigger_id, func, enabled in records:
        out += struct.pack('>HBHBB', ea_id, kind, trigger_id, func, enabled)
    return out


class TestS19Definitions:
    RECORDS = [
        (1001, 0, 0x0102, 10, 1),     # event trigger
        (1002, 1, 0x0101, 20, 0),     # parameter trigger, disabled
    ]

    def test_record_is_7_bytes(self):
        assert len(_s19_defs_bytes(self.RECORDS)) == 2 + 2 * 7

    def test_16_bit_trigger_id_survives(self):
        """The old record packed trigger_id as uint8; every real id is >= 0x0100."""
        out = P.parse_s19_definitions(_s19_defs_bytes(self.RECORDS))
        assert out[0]["trigger_id"] == 0x0102
        assert out[1]["trigger_id"] == 0x0101

    def test_trigger_kind_namespaces(self):
        out = P.parse_s19_definitions(_s19_defs_bytes(self.RECORDS))
        assert out[0]["trigger_kind"] == P.TRIGGER_KIND_EVENT
        assert out[0]["trigger_kind_name"] == "event"
        assert out[1]["trigger_kind"] == P.TRIGGER_KIND_PARAM
        assert out[1]["trigger_kind_name"] == "param"

    def test_enabled_and_action(self):
        out = P.parse_s19_definitions(_s19_defs_bytes(self.RECORDS))
        assert out[0]["enabled"] is True and out[0]["action_func_id"] == 10
        assert out[1]["enabled"] is False and out[1]["action_func_id"] == 20

    def test_empty_and_truncated(self):
        assert P.parse_s19_definitions(struct.pack('>H', 0)) == []
        assert len(P.parse_s19_definitions(_s19_defs_bytes(self.RECORDS)[:-3])) == 1


# ── TM(11,18) — detailed on-board schedule report (VARIABLE stride) ──

def _s11_schedule_bytes(entries, enabled=True, next_id=99, count=None):
    header = struct.pack('>HBH', len(entries) if count is None else count,
                         1 if enabled else 0, next_id)
    body = b""
    for cid, exec_t, apid, svc, st, data in entries:
        body += struct.pack('>HIHBBBB', cid, exec_t, apid, svc, st,
                            len(data), len(data)) + data
    return header + body


class TestS11Schedule:
    ENTRIES = [
        (1, 838557417, 1, 8, 1, bytes([0x1C])),              # 13 B record
        (2, 838557301, 1, 8, 1, bytes([0x13, 0x03])),        # 14 B record
        (3, 838557500, 1, 6, 2, bytes(range(16))),           # 28 B record
        (4, 838557600, 1, 17, 1, b""),                       # 12 B record
    ]

    def test_variable_stride_is_walked_per_record(self):
        """Records here are 13/14/28/12 bytes — a derived stride cannot work."""
        raw = _s11_schedule_bytes(self.ENTRIES)
        rep = P.parse_s11_schedule(raw)
        assert rep["count"] == 4
        assert len(rep["entries"]) == 4
        assert {e["id"] for e in rep["entries"]} == {1, 2, 3, 4}

    def test_a_fixed_stride_would_have_been_wrong(self):
        raw = _s11_schedule_bytes(self.ENTRIES)
        derived = (len(raw) - 5) // 4
        assert derived != 12 + len(self.ENTRIES[0][5])   # no single stride fits

    def test_entries_are_sorted_by_execution_time(self):
        rep = P.parse_s11_schedule(_s11_schedule_bytes(self.ENTRIES))
        times = [e["exec_time_cuc"] for e in rep["entries"]]
        assert times == sorted(times)
        assert rep["entries"][0]["id"] == 2          # earliest exec time

    def test_header_carries_enable_flag_and_next_id(self):
        rep = P.parse_s11_schedule(_s11_schedule_bytes(self.ENTRIES, enabled=False,
                                                       next_id=42))
        assert rep["enabled"] is False
        assert rep["next_id"] == 42

    def test_command_identity_is_preserved(self):
        rep = P.parse_s11_schedule(_s11_schedule_bytes(self.ENTRIES))
        by_id = {e["id"]: e for e in rep["entries"]}
        assert by_id[1]["service"] == 8 and by_id[1]["subtype"] == 1
        assert by_id[1]["data_hex"] == "1C"
        assert by_id[3]["service"] == 6 and by_id[3]["subtype"] == 2
        assert by_id[3]["data_hex"] == bytes(range(16)).hex().upper()
        assert by_id[4]["data_hex"] == ""

    def test_truncated_data_is_flagged_not_silently_dropped(self):
        """data_len > n means the spacecraft reported only the first n bytes."""
        raw = struct.pack('>HBH', 1, 1, 7)
        raw += struct.pack('>HIHBBBB', 5, 1000, 1, 6, 2, 200, 4) + b"\xDE\xAD\xBE\xEF"
        rep = P.parse_s11_schedule(raw)
        e = rep["entries"][0]
        assert e["data_len"] == 200 and e["truncated"] is True
        assert e["data_hex"] == "DEADBEEF"

    def test_empty_schedule(self):
        rep = P.parse_s11_schedule(struct.pack('>HBH', 0, 1, 1))
        assert rep["count"] == 0 and rep["entries"] == []

    def test_short_payload_is_rejected(self):
        assert P.parse_s11_schedule(b"\x00\x01") is None

    def test_count_larger_than_payload_stops_cleanly(self):
        raw = _s11_schedule_bytes(self.ENTRIES[:2], count=9)
        rep = P.parse_s11_schedule(raw)
        assert rep["count"] == 9 and len(rep["entries"]) == 2


# ── TM(15,14) — store status report (VARIABLE stride) ────────────────

def _s15_status_bytes(records):
    out = struct.pack('>B', len(records))
    for sid, count, cap, enabled, fill_x10, flags, wrap, name in records:
        nm = name.encode("ascii")
        out += struct.pack('>BHHBHBHB', sid, count, cap, enabled, fill_x10,
                           flags, wrap, len(nm)) + nm
    return out


class TestS15StoreStatus:
    RECORDS = [
        (1, 120, 1000, 1, 120, 0b00, 0, "HK Store"),
        (2, 500, 500, 1, 1000, 0b11, 3, "Event Store"),      # overflow+circular
        (3, 0, 2000, 0, 0, 0b00, 0, "Science"),
        (4, 7, 100, 1, 70, 0b10, 0, "Alarm Store"),
    ]

    def test_variable_name_lengths_do_not_desynchronise_the_walk(self):
        """Names are 8/11/7/11 chars — a single derived stride cannot work."""
        out = P.parse_s15_store_status(_s15_status_bytes(self.RECORDS))
        assert [s["id"] for s in out] == [1, 2, 3, 4]
        assert [s["name"] for s in out] == [r[7] for r in self.RECORDS]

    def test_round_trip_of_every_field(self):
        out = P.parse_s15_store_status(_s15_status_bytes(self.RECORDS))
        s2 = out[1]
        assert s2["count"] == 500 and s2["capacity"] == 500
        assert s2["enabled"] is True
        assert s2["fill_pct"] == pytest.approx(100.0)
        assert s2["overflow"] is True and s2["circular"] is True
        assert s2["wrap_count"] == 3
        s3 = out[2]
        assert s3["enabled"] is False and s3["fill_pct"] == pytest.approx(0.0)
        assert s3["overflow"] is False and s3["circular"] is False
        assert out[3]["circular"] is True and out[3]["overflow"] is False

    def test_empty_and_truncated(self):
        assert P.parse_s15_store_status(b"\x00") == []
        raw = _s15_status_bytes(self.RECORDS)[:-4]
        assert len(P.parse_s15_store_status(raw)) == 3


# ── TM(6,6) / TM(6,10) — memory dump and CRC reports ─────────────────

class TestS6Reports:
    def test_dump_round_trip(self):
        payload = bytes(range(32))
        raw = struct.pack('>IH', 0x00100000, len(payload)) + payload
        out = P.parse_s6_dump(raw)
        assert out["address"] == 0x00100000
        assert out["length"] == 32
        assert out["data_hex"] == payload.hex().upper()
        assert out["truncated"] is False

    def test_dump_truncation_is_reported(self):
        raw = struct.pack('>IH', 0x20000000, 64) + b"\x01\x02"
        out = P.parse_s6_dump(raw)
        assert out["received"] == 2 and out["truncated"] is True

    def test_check_round_trip(self):
        raw = struct.pack('>IHH', 0x00200000, 512, 0x4097)
        out = P.parse_s6_check(raw)
        assert out == {"address": 0x00200000, "length": 512, "crc": 0x4097}

    def test_short_payloads_rejected(self):
        assert P.parse_s6_dump(b"\x00\x00") is None
        assert P.parse_s6_check(b"\x00\x00\x00\x00") is None


# ── synthetic S12 event ids ─────────────────────────────────────────

class TestS12EventMapping:
    def test_limit_entered_event_maps_to_its_parameter(self):
        assert P.s12_event_to_param_id(0x9000 | 0x0101) == 0x0101

    def test_return_to_nominal_event_maps_to_its_monitor(self):
        assert P.s12_event_to_param_id(0xA000 | 0x0207) == 0x0207

    def test_a_catalogued_event_is_not_an_s12_mirror(self):
        assert P.s12_event_to_param_id(0x0102) is None
        assert P.s12_event_to_param_id(0x0501) is None
