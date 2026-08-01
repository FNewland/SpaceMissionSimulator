"""On-board PUS service fixes — defects #69-#75 and roadmap N-1/N-2/N-5/N-9.

Each class here pins one previously-broken on-board behaviour:

* #69 S12 — definitions keyed by definition id (no silently-lost WARNING tier),
  TM(12,9)/(12,10) check transitions, per-definition enable/disable, delta checks
* #70 S19 — TC(19,8) report actually returns records; ground-added rules can
  reference a real event; triggers carry a namespace so nothing dual-fires
* #71 S6  — config-declared memory map, sparse patch store, RO/unmapped
  rejection, dump/check consistency, bound regions tracking the live model
* #72 S11 — TM(11,18) carries the identity of the deferred command
* #73 S15 — TM(15,14) carries name / fill% / overflow / wrap
* #74     — the commanded TM rate is authoritative and paces real-time TM
* #75     — HK SID default enable state is config-driven; the bootloader
  beacon HK cannot be disabled
* N-1/N-2/N-5/N-9 — tick-thread guard, snapshot threading contract,
  reproducible epoch, and the retired 245 W AOCS-off fallback array
"""
import os
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from smo_common.protocol.ecss_packet import build_tc_packet, decommutate_packet
from smo_simulator.engine import SimulationEngine, BOOTLOADER_BEACON_SID
from smo_simulator.memory_map import (
    MemoryImage, MemoryMap, ERR_ILLEGAL_ADDRESS, ERR_WRITE_PROTECTED,
)
from smo_simulator.monitoring_loader import (
    TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM, load_s12_definitions,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"


@pytest.fixture
def engine(tmp_path):
    """A nominal, commissioned engine with the link forced up."""
    os.environ.setdefault("SMO_DUMP_DIR", str(tmp_path / "dumps"))
    eng = SimulationEngine(CONFIG_DIR)
    eng._spacecraft_phase = 6
    eng.params[0x0311] = 1
    eng._override_passes = True
    eng._in_contact = True
    eng.params[0x0501] = 2
    obdh = eng.subsystems.get("obdh")
    if obdh is not None:
        obdh._state.sw_image = 1
    return eng


def _tm_of(engine, service, subtype):
    """Drain tm_queue and return the decommutated packets of one TM type."""
    out = []
    while not engine.tm_queue.empty():
        pkt = engine.tm_queue.get_nowait()
        parsed = decommutate_packet(pkt)
        if (parsed and parsed.secondary
                and parsed.secondary.service == service
                and parsed.secondary.subtype == subtype):
            out.append(parsed)
    return out


def _fail_codes(responses):
    """S1.4 failure codes carried by a list of raw TM packets."""
    codes = []
    for pkt in responses:
        parsed = decommutate_packet(pkt)
        if parsed and parsed.secondary and parsed.secondary.service == 1 \
                and parsed.secondary.subtype == 4:
            codes.append(struct.unpack('>HH', parsed.data_field[:4])[1])
    return codes


# ═══════════════════════════════════════════════════════════════════
# Defect #69 — S12 on-board monitoring
# ═══════════════════════════════════════════════════════════════════

class TestS12Definitions:

    def test_every_config_definition_is_registered(self, engine):
        """All 38 shipped rules survive registration.

        register_s12_definitions() used to re-key by param_id, so each
        parameter carrying both a WARNING and an ALARM tier kept only the
        ALARM one — 4 rules lost, and no early-warning tier anywhere.
        """
        loaded = load_s12_definitions(CONFIG_DIR)
        registered = engine._dispatcher._s12_definitions
        assert len(registered) == len(loaded) == 46, (
            f"{len(loaded)} rules loaded but {len(registered)} registered")

    def test_both_tiers_survive_on_a_shared_parameter(self, engine):
        """Battery SoC keeps its 20 % WARNING *and* its 10 % ALARM."""
        defs = [d for d in engine._dispatcher._s12_definitions.values()
                if d['param_id'] == 0x0101]
        lows = sorted(d['low_limit'] for d in defs)
        assert lows == [10.0, 20.0], (
            f"battery SoC should carry both tiers, got limits {lows}")
        severities = {d['severity'] for d in defs}
        assert severities == {"WARNING", "ALARM"}

    @pytest.mark.parametrize("param_id,expected_lows", [
        (0x0101, [10.0, 20.0]),      # SoC 20 % warning / 10 % alarm
        (0x0105, [25.0, 27.0]),      # bus 27 V warning / 25 V alarm
    ])
    def test_named_lost_tiers_are_back(self, engine, param_id, expected_lows):
        defs = [d for d in engine._dispatcher._s12_definitions.values()
                if d['param_id'] == param_id]
        assert sorted(d['low_limit'] for d in defs) == expected_lows

    def test_battery_temperature_keeps_both_limits(self, engine):
        """0x0102 carried an overtemp (45 C) and an undertemp rule."""
        defs = [d for d in engine._dispatcher._s12_definitions.values()
                if d['param_id'] == 0x0102]
        assert len(defs) == 2
        assert 45.0 in {d['high_limit'] for d in defs}


class TestS12TransitionReports:

    def _violating_monitor(self, engine, low=95.0, high=100.0):
        """Install a monitor on battery SoC that the current value violates."""
        engine._dispatcher._s12_definitions.clear()
        engine._dispatcher.dispatch(
            12, 6, struct.pack('>HHBff', 0x9001, 0x0101, 0, low, high))
        engine.params[0x0101] = 50.0

    def test_entering_a_limit_emits_tm_12_9(self, engine):
        """A check transition must reach the ground as real S12 telemetry.

        Previously S12 only ever produced a synthetic 0x9xxx S5 event, which
        the MCS alarm journal files under "unknown" — the ground's S12.9/12.10
        parser was dead code.
        """
        self._violating_monitor(engine)
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()

        engine._tick_s12_monitoring()
        reports = _tm_of(engine, 12, 9)
        assert reports, "no TM(12,9) emitted on entering a limit"

        data = reports[0].data_field
        param_id, value, mon_id, check_type, severity, low, high = \
            struct.unpack('>HfHBBff', data[:18])
        assert param_id == 0x0101
        assert mon_id == 0x9001
        assert value == pytest.approx(50.0)
        assert (low, high) == pytest.approx((95.0, 100.0))
        assert check_type == 0

    def test_returning_to_nominal_emits_tm_12_10(self, engine):
        self._violating_monitor(engine)
        engine._tick_s12_monitoring()
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()

        engine.params[0x0101] = 97.0        # back inside the band
        engine._tick_s12_monitoring()
        reports = _tm_of(engine, 12, 10)
        assert reports, "no TM(12,10) emitted on returning to nominal"
        param_id = struct.unpack('>H', reports[0].data_field[:2])[0]
        assert param_id == 0x0101

    def test_transition_is_edge_triggered(self, engine):
        self._violating_monitor(engine)
        engine._tick_s12_monitoring()
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
        engine._tick_s12_monitoring()
        assert not _tm_of(engine, 12, 9), (
            "TM(12,9) re-emitted while the parameter stayed in violation")


class TestS12PerDefinitionControl:

    def test_disable_and_enable_one_definition(self, engine):
        d = engine._dispatcher
        d._s12_definitions.clear()
        d.dispatch(12, 6, struct.pack('>HHBff', 0x9001, 0x0101, 0, 95.0, 100.0))
        d.dispatch(12, 6, struct.pack('>HHBff', 0x9002, 0x0101, 0, 90.0, 100.0))
        engine.params[0x0101] = 50.0
        assert len(d.check_monitoring()) == 2

        d.dispatch(12, 4, struct.pack('>H', 0x9001))     # disable one
        violated = {v['mon_id'] for v in d.check_monitoring()}
        assert violated == {0x9002}
        assert d._s12_definitions[0x9001]['enabled'] is False

        d.dispatch(12, 3, struct.pack('>H', 0x9001))     # enable it again
        assert {v['mon_id'] for v in d.check_monitoring()} == {0x9001, 0x9002}

    def test_enable_disable_of_unknown_definition_is_rejected(self, engine):
        responses = engine._dispatcher.dispatch(12, 4, struct.pack('>H', 0xBEEF))
        assert _fail_codes(responses) == [0x0007]

    def test_global_disable_still_wins(self, engine):
        d = engine._dispatcher
        d._s12_definitions.clear()
        d.dispatch(12, 6, struct.pack('>HHBff', 0x9001, 0x0101, 0, 95.0, 100.0))
        engine.params[0x0101] = 50.0
        d.dispatch(12, 2, b'')
        assert d.check_monitoring() == []


class TestS12DeltaCheck:

    def _delta_monitor(self, engine, low=-5.0, high=5.0):
        d = engine._dispatcher
        d._s12_definitions.clear()
        # check_type 1 = delta
        d.dispatch(12, 6, struct.pack('>HHBff', 0x9010, 0x0101, 1, low, high))
        return d

    def test_first_sample_has_no_baseline(self, engine):
        d = self._delta_monitor(engine)
        engine.params[0x0101] = 50.0
        assert d.check_monitoring() == [], (
            "a delta check fired on its very first sample, with no baseline")

    def test_small_change_is_nominal(self, engine):
        d = self._delta_monitor(engine)
        engine.params[0x0101] = 50.0
        d.check_monitoring()
        engine.params[0x0101] = 52.0
        assert d.check_monitoring() == []

    def test_large_change_violates(self, engine):
        d = self._delta_monitor(engine)
        engine.params[0x0101] = 50.0
        d.check_monitoring()
        engine.params[0x0101] = 80.0          # +30 in one check interval
        violations = d.check_monitoring()
        assert len(violations) == 1
        assert violations[0]['type'] == 'delta_out_of_limits'
        assert violations[0]['value'] == pytest.approx(30.0)


class TestS12DefinitionReport:

    def test_report_carries_mon_id_param_id_and_enable_state(self, engine):
        d = engine._dispatcher
        responses = d.dispatch(12, 12, b'')
        assert len(responses) == 1
        data = decommutate_packet(responses[0]).data_field
        count = struct.unpack('>H', data[:2])[0]
        assert count == len(d._s12_definitions) == 46
        # 14-byte records: mon_id, param_id, check_type, enabled, low, high
        assert len(data) == 2 + 14 * count
        mon_id, param_id, check_type, enabled, low, high = \
            struct.unpack('>HHBBff', data[2:16])
        assert mon_id in d._s12_definitions
        assert d._s12_definitions[mon_id]['param_id'] == param_id
        assert enabled == 1


# ═══════════════════════════════════════════════════════════════════
# Defect #70 — S19 event-action
# ═══════════════════════════════════════════════════════════════════

class TestS19Report:

    def test_report_returns_real_content(self, engine):
        """TC(19,8) used to return nothing at all with the shipped rules.

        The record packed event_type as a uint8; every shipped rule has a
        >= 0x0100 trigger, so struct.error was raised and swallowed.
        """
        responses = engine._dispatcher.dispatch(19, 8, b'')
        assert len(responses) == 1, "TC(19,8) returned no report"

        data = decommutate_packet(responses[0]).data_field
        count = struct.unpack('>H', data[:2])[0]
        assert count == 32
        assert len(data) == 2 + 7 * count, "TM(19,128) record is not 7 bytes"

        rules = {}
        for i in range(count):
            off = 2 + 7 * i
            ea_id, kind, trig, func, enabled = struct.unpack(
                '>HBHBB', data[off:off + 7])
            rules[ea_id] = (kind, trig, func, enabled)

        # A rule whose trigger id cannot fit in a byte must survive the report.
        # (Phase C WP-9: rule 2001 now triggers on 0x02BA aocs.h_env_frac —
        # the old 0x025B monitor could never trip.)
        assert rules[2001][1] == 0x02BA
        assert rules[2001][0] == TRIGGER_KIND_PARAM
        assert rules[1001][0] == TRIGGER_KIND_EVENT
        # All shipped rules are enabled except 2004 (att-error safing) —
        # disabled by default since Phase C because real slew/acquisition
        # transients legitimately cross its 1.0° S12 trigger; sustained
        # excursions are handled by fdir.yaml (persist_s 420).
        for ea_id, (_, _, _, e) in rules.items():
            assert e == (0 if ea_id == 2004 else 1), (ea_id, e)

    def test_report_tracks_disable(self, engine):
        d = engine._dispatcher
        d.dispatch(19, 5, struct.pack('>H', 2001))
        data = decommutate_packet(d.dispatch(19, 8, b'')[0]).data_field
        count = struct.unpack('>H', data[:2])[0]
        state = {}
        for i in range(count):
            off = 2 + 7 * i
            ea_id, _k, _t, _f, enabled = struct.unpack('>HBHBB', data[off:off + 7])
            state[ea_id] = enabled
        assert state[2001] == 0
        assert state[2002] == 1


class TestS19GroundAddedRules:

    def test_ground_rule_can_reference_a_real_event(self, engine):
        """TC(19,1) used to truncate event_type to one byte.

        Every EOSAT-1 event id is >= 0x0100, so no ground-added rule could
        ever match anything.
        """
        d = engine._dispatcher
        d._s19_definitions.clear()
        d._s19_enabled_ids.clear()
        # ea_id, kind=event, trigger 0x0301 (OBC_REBOOT), func 66 (TTC PA ON)
        d.dispatch(19, 1, struct.pack('>HBHB', 7001, TRIGGER_KIND_EVENT,
                                      0x0301, 66))
        assert d._s19_definitions[7001]['event_type'] == 0x0301

        ttc = engine.subsystems["ttc"]
        ttc._state.pa_on = False
        d.trigger_event_action(0x0301, kind=TRIGGER_KIND_EVENT)
        assert ttc._state.pa_on is True, (
            "a ground-added rule on a real event id did not fire")

    def test_ground_rule_on_a_parameter_fires_only_on_the_parameter(self, engine):
        d = engine._dispatcher
        d._s19_definitions.clear()
        d._s19_enabled_ids.clear()
        d.dispatch(19, 1, struct.pack('>HBHB', 7002, TRIGGER_KIND_PARAM,
                                      0x0301, 66))

        ttc = engine.subsystems["ttc"]
        ttc._state.pa_on = False
        d.trigger_event_action(0x0301, kind=TRIGGER_KIND_EVENT)
        assert ttc._state.pa_on is False, (
            "a param-keyed rule fired on an event with the same id")

        d.trigger_event_action(0x0301, kind=TRIGGER_KIND_PARAM)
        assert ttc._state.pa_on is True

    def test_unknown_trigger_kind_is_rejected(self, engine):
        responses = engine._dispatcher.dispatch(
            19, 1, struct.pack('>HBHB', 7003, 9, 0x0301, 66))
        assert _fail_codes(responses) == [0x0003]

    def test_legacy_four_byte_form_still_installs_a_rule(self, engine):
        d = engine._dispatcher
        d.dispatch(19, 1, struct.pack('>HBB', 7004, 0x42, 66))
        assert d._s19_definitions[7004]['event_type'] == 0x42
        assert d._s19_definitions[7004]['trigger_kind'] == TRIGGER_KIND_EVENT


class TestS19NoDualTrigger:

    def test_s12_param_violation_does_not_fire_an_event_keyed_rule(self, engine):
        """The live reproduction of the defect, through the engine.

        Rule 1001 is keyed on *event* 0x0102. 0x0102 is also the monitored
        battery-temperature parameter, and an S12 violation of it used to fire
        rule 1001 (which dispatches AOCS_SLEW_TO) while the rule claimed to be
        about bus undervoltage.
        """
        fired = []
        engine._dispatcher._s19_dispatch = \
            lambda ea_id, func_id, action_data=b"": fired.append(ea_id)

        engine._emit_event({'event_id': 0x9000 | 0x0102, 'severity': 3,
                            'description': 'battery temperature violation'})
        assert 1001 not in fired, (
            "an S12 battery-temperature violation fired event-keyed rule 1001")

        engine._emit_event({'event_id': 0x0102, 'severity': 3,
                            'description': 'BATTERY_SOC_CRITICAL'})
        assert 1001 in fired, "rule 1001 no longer fires on its declared event"

    def test_param_keyed_rule_fires_from_a_real_s12_violation(self, engine):
        """Rule 4002 is keyed on parameter 0x050F (PA temperature)."""
        fired = []
        engine._dispatcher._s19_dispatch = \
            lambda ea_id, func_id, action_data=b"": fired.append(ea_id)
        engine._emit_event({'event_id': 0x9000 | 0x050F, 'severity': 3,
                            'description': 'PA overtemp'})
        assert 4002 in fired


# ═══════════════════════════════════════════════════════════════════
# Defect #71 — S6 memory map and patches
# ═══════════════════════════════════════════════════════════════════

class TestMemoryMapConfig:

    def test_mission_map_is_config_declared(self, engine):
        names = {r.name for r in engine._memory_map.regions}
        assert {"boot_rom", "app_a", "app_b", "config",
                "scratch_ram", "aocs_gains"} <= names
        assert (CONFIG_DIR / "memory" / "memory_map.yaml").exists()

    def test_regions_do_not_overlap(self, engine):
        ordered = sorted(engine._memory_map.regions, key=lambda r: r.start)
        for a, b in zip(ordered, ordered[1:]):
            assert b.start >= a.end, f"{b.name} overlaps {a.name}"

    def test_missing_map_falls_back_to_the_builtin(self, tmp_path):
        mmap = MemoryMap.load(tmp_path)
        assert mmap.region_at(0x00100000) is not None


class TestS6Patching:

    A = 0x00100000       # app_a, rw

    def _crc(self, engine, addr, length):
        resp = engine._dispatcher.dispatch(6, 9, struct.pack('>IH', addr, length))
        return struct.unpack('>IHH', decommutate_packet(resp[0]).data_field)[2]

    def _dump(self, engine, addr, length):
        resp = engine._dispatcher.dispatch(6, 5, struct.pack('>IH', addr, length))
        data = decommutate_packet(resp[0]).data_field
        return data[6:]

    def test_patch_round_trips_through_dump(self, engine):
        payload = b"PATCHED!"
        engine._dispatcher.dispatch(6, 2, struct.pack('>I', self.A) + payload)
        assert self._dump(engine, self.A, len(payload)) == payload

    def test_patch_changes_the_check_crc(self, engine):
        """MEM_CHECK used to CRC synthetic content, so a patch was invisible."""
        before = self._crc(engine, self.A, 64)
        engine._dispatcher.dispatch(6, 2, struct.pack('>I', self.A) + b"PATCHED!")
        after = self._crc(engine, self.A, 64)
        assert before != after, "MEM_CHECK CRC unchanged after a patch"

    def test_dump_and_check_agree(self, engine):
        """The CRC must be reconstructible from what dump returns."""
        length = 512
        engine._dispatcher.dispatch(6, 2, struct.pack('>I', self.A) + b"XYZ")
        dumped = self._dump(engine, self.A, length)
        assert len(dumped) == length, (
            "dump was silently truncated while check was not")
        assert engine._dispatcher._crc16_ccitt(dumped) == \
            self._crc(engine, self.A, length)

    def test_multi_packet_upload_then_verify(self, engine):
        """A patch split across several MEM_LOADs verifies as one block."""
        blocks = [bytes([i]) * 64 for i in range(1, 5)]
        for i, block in enumerate(blocks):
            engine._dispatcher.dispatch(
                6, 2, struct.pack('>I', self.A + i * 64) + block)
        expected = b"".join(blocks)
        assert self._dump(engine, self.A, len(expected)) == expected
        assert self._crc(engine, self.A, len(expected)) == \
            engine._dispatcher._crc16_ccitt(expected)

    def test_readonly_region_is_rejected(self, engine):
        responses = engine._dispatcher.dispatch(
            6, 2, struct.pack('>I', 0x00000000) + b"\x00" * 8)
        assert _fail_codes(responses) == [ERR_WRITE_PROTECTED]

    def test_boot_backup_is_rejected_too(self, engine):
        """The UI called 0x00010000 read-only; only 0x0-0x1FFFF was enforced."""
        responses = engine._dispatcher.dispatch(
            6, 2, struct.pack('>I', 0x00010000) + b"\x00" * 8)
        assert _fail_codes(responses) == [ERR_WRITE_PROTECTED]

    def test_unmapped_address_is_rejected(self, engine):
        for subtype, data in (
            (2, struct.pack('>I', 0xDEADBEEF) + b"\x00" * 4),
            (5, struct.pack('>IH', 0xDEADBEEF, 16)),
            (9, struct.pack('>IH', 0xDEADBEEF, 16)),
        ):
            responses = engine._dispatcher.dispatch(6, subtype, data)
            assert _fail_codes(responses) == [ERR_ILLEGAL_ADDRESS], (
                f"S6.{subtype} accepted an unmapped address")

    def test_catalog_shaped_load_lands_on_the_declared_address(self, engine):
        """MEM_LOAD(memory_id=1, address=0x00100000) used to hit 0x00001000."""
        engine._dispatcher.dispatch(
            6, 2, bytes([1]) + struct.pack('>I', self.A) + b"CATALOG!")
        assert self._dump(engine, self.A, 8) == b"CATALOG!"

    def test_dump_length_limit_is_enforced(self, engine):
        over = engine._memory_map.limits.max_dump_bytes + 1
        responses = engine._dispatcher.dispatch(
            6, 5, struct.pack('>IH', self.A, over))
        assert _fail_codes(responses), "over-long dump was not rejected"


class TestS6BoundRegions:

    GAINS = 0x20100000

    def test_dump_reflects_the_live_model(self, engine):
        engine.subsystems["aocs"]._state.torque_gain_y = 7.5
        resp = engine._dispatcher.dispatch(6, 5, struct.pack('>IH', self.GAINS, 12))
        gains = struct.unpack('>fff', decommutate_packet(resp[0]).data_field[6:18])
        assert gains[1] == pytest.approx(7.5)

    def test_patch_changes_the_model(self, engine):
        payload = struct.pack('>fff', 2.5, 3.5, 4.5)
        engine._dispatcher.dispatch(6, 2, struct.pack('>I', self.GAINS) + payload)
        state = engine.subsystems["aocs"]._state
        assert (state.torque_gain_x, state.torque_gain_y, state.torque_gain_z) \
            == pytest.approx((2.5, 3.5, 4.5))

    def test_partial_patch_touches_only_its_axis(self, engine):
        state = engine.subsystems["aocs"]._state
        state.torque_gain_x = 1.0
        state.torque_gain_z = 1.0
        engine._dispatcher.dispatch(
            6, 2, struct.pack('>I', self.GAINS + 4) + struct.pack('>f', 9.0))
        assert state.torque_gain_y == pytest.approx(9.0)
        assert state.torque_gain_x == pytest.approx(1.0)
        assert state.torque_gain_z == pytest.approx(1.0)

    def test_bound_region_bytes_are_not_stale(self, engine):
        """A bound region is a window, not a copy: the model wins."""
        engine._dispatcher.dispatch(
            6, 2, struct.pack('>I', self.GAINS) + struct.pack('>f', 2.0))
        engine.subsystems["aocs"]._state.torque_gain_x = 6.0
        resp = engine._dispatcher.dispatch(6, 5, struct.pack('>IH', self.GAINS, 4))
        value = struct.unpack('>f', decommutate_packet(resp[0]).data_field[6:10])[0]
        assert value == pytest.approx(6.0)


class TestMemoryImageStore:

    def test_pages_are_sparse(self):
        image = MemoryImage(MemoryMap.default())
        assert image.patched_pages == 0
        image.read(0x00100000, 4096)
        assert image.patched_pages == 0, "a read materialised pages"
        image.write(0x00100000, b"x")
        assert image.patched_pages == 1

    def test_state_round_trips(self):
        image = MemoryImage(MemoryMap.default())
        image.write(0x20000000, b"restore-me")
        clone = MemoryImage(MemoryMap.default())
        clone.set_state(image.get_state())
        assert clone.read(0x20000000, 10) == b"restore-me"


# ═══════════════════════════════════════════════════════════════════
# Defect #72 — S11 schedule report
# ═══════════════════════════════════════════════════════════════════

class TestS11ScheduleReport:

    def _schedule(self, engine, exec_time, func_id):
        inner = build_tc_packet(1, 8, 1, bytes([func_id, 0x02]))
        engine._dispatcher.dispatch(11, 4, struct.pack('>I', exec_time) + inner)

    def test_report_carries_command_identity(self, engine):
        self._schedule(engine, 900000, 0x1A)
        responses = engine._dispatcher.dispatch(11, 17, b'')
        data = decommutate_packet(responses[0]).data_field

        count, enabled, next_id = struct.unpack('>HBH', data[:5])
        assert count == 1
        assert enabled == 1
        assert next_id == 2

        (cmd_id, exec_time, apid, service, subtype,
         data_len, n_bytes) = struct.unpack('>HIHBBBB', data[5:17])
        assert cmd_id == 1
        assert exec_time == 900000
        assert (apid, service, subtype) == (1, 8, 1), (
            "the report still cannot say which command is queued")
        assert data_len == 2
        assert data[17:17 + n_bytes] == bytes([0x1A, 0x02])

    def test_report_is_ordered_by_execution_time(self, engine):
        self._schedule(engine, 900200, 0x1C)
        self._schedule(engine, 900100, 0x1A)
        data = decommutate_packet(
            engine._dispatcher.dispatch(11, 17, b'')[0]).data_field
        off, times = 5, []
        for _ in range(struct.unpack('>H', data[:2])[0]):
            _id, t, _apid, _s, _st, _dl, n = struct.unpack('>HIHBBBB',
                                                           data[off:off + 12])
            times.append(t)
            off += 12 + n
        assert times == sorted(times)

    def test_scheduler_still_executes_the_real_packet(self, engine):
        """Caching the identity must not disturb execution."""
        self._schedule(engine, 100, 0x1A)
        due = engine._tc_scheduler.tick(200)
        assert len(due) == 1
        parsed = decommutate_packet(due[0])
        assert (parsed.secondary.service, parsed.secondary.subtype) == (8, 1)

    def test_report_survives_a_malformed_embedded_packet(self, engine):
        engine._dispatcher.dispatch(11, 4, struct.pack('>I', 500) + b'\xAA\xBB')
        responses = engine._dispatcher.dispatch(11, 17, b'')
        assert len(responses) == 1


# ═══════════════════════════════════════════════════════════════════
# Defect #73 — S15 store status
# ═══════════════════════════════════════════════════════════════════

class TestS15StoreStatus:

    @staticmethod
    def _parse(data):
        stores, off = [], 1
        for _ in range(data[0]):
            (sid, count, cap, enabled, fill_x10,
             flags, wrap, name_len) = struct.unpack('>BHHBHBHB', data[off:off + 12])
            off += 12
            name = data[off:off + name_len].decode('ascii')
            off += name_len
            stores.append({
                'id': sid, 'count': count, 'capacity': cap,
                'enabled': bool(enabled), 'fill_pct': fill_x10 / 10.0,
                'overflow': bool(flags & 1), 'circular': bool(flags & 2),
                'wrap_count': wrap, 'name': name,
            })
        return stores

    def test_status_report_carries_every_field(self, engine):
        responses = engine._dispatcher.dispatch(15, 13, b'')
        assert len(responses) == 1
        stores = self._parse(decommutate_packet(responses[0]).data_field)

        assert {s['id'] for s in stores} == {1, 2, 3, 4}
        by_id = {s['id']: s for s in stores}
        assert by_id[1]['name'] == 'HK_Store'
        assert by_id[1]['circular'] is True, "the circular flag was discarded"
        assert by_id[4]['name'] == 'Alarm_Store'
        for s in stores:
            assert 0.0 <= s['fill_pct'] <= 100.0

    def test_fill_percentage_tracks_content(self, engine):
        storage = engine._tm_storage
        storage.enable_store(2)
        for _ in range(100):
            storage.store_packet(5, b'\x00' * 20)
        stores = self._parse(
            decommutate_packet(engine._dispatcher.dispatch(15, 13, b'')[0]).data_field)
        event_store = next(s for s in stores if s['id'] == 2)
        assert event_store['count'] == 100
        assert event_store['fill_pct'] == pytest.approx(5.0, abs=0.2)

    def test_disk_archive_is_written_at_queue_time_and_survives_clear(self, engine,
                                                                     tmp_path):
        """Ground-copy semantics are deliberately unchanged (defect #73).

        The `.bin` under workspace/dumps is the ground's copy of a dump that
        was already downlinked; an on-board clear must not delete it.
        """
        engine._dump_dir = tmp_path / "dumps"
        storage = engine._tm_storage
        storage.enable_store(2)
        for _ in range(5):
            storage.store_packet(5, b'\x00' * 20)
        engine.queue_dump(2)
        archives = list(engine._dump_dir.glob("dump_sid02_*.bin"))
        assert archives, "no dump archive written at queue time"

        engine._dispatcher.dispatch(15, 11, bytes([2]))     # clear on board
        assert archives[0].exists(), "an on-board clear deleted the ground copy"


# ═══════════════════════════════════════════════════════════════════
# Defect #74 — commandable, rate-paced telemetry
# ═══════════════════════════════════════════════════════════════════

def _orbit_state():
    return SimpleNamespace(
        in_contact=True, in_eclipse=False, solar_beta_deg=20.0,
        lat_deg=45.0, lon_deg=10.0, alt_km=450.0,
        vel_x=0.0, vel_y=7.5, vel_z=0.0,
        gs_elevation_deg=30.0, gs_azimuth_deg=180.0, gs_range_km=800.0)


class TestCommandedDataRate:

    @staticmethod
    def _ready(engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.antenna_deployed = True
        ttc._state.beacon_mode = False
        # The HIGH tier of the ladder is gated on DOWNLINK/dump mode and
        # elevation >= 10 deg (defect #85); put the spacecraft in a state
        # where every tier is legal so these tests exercise the decode.
        ttc._state.downlink_mode = True
        ttc.tick(1.0, _orbit_state(), engine.params)
        return ttc

    def test_catalogued_uint8_low_actually_selects_low(self, engine):
        """rate=0 used to fall through to a hard-coded 64000.

        Under the ladder (defect #85) the legacy code 0 still means "the low
        rate" — it now lands on LOW (16 kbps), not the 1 kbps beacon.
        """
        ttc = self._ready(engine)
        engine._dispatcher._handle_s8(1, bytes([65, 0]))
        assert ttc.tier_name(ttc._state.data_rate_mode) == "LOW"
        assert ttc._state.tm_data_rate == 16000

    def test_catalogued_uint8_high_selects_high(self, engine):
        """Legacy code 1 still means "the high rate" — now HIGH at 128 kbps."""
        ttc = self._ready(engine)
        engine._dispatcher._handle_s8(1, bytes([65, 1]))
        assert ttc.tier_name(ttc._state.data_rate_mode) == "HIGH"
        assert ttc._state.tm_data_rate == 128000

    def test_commanded_rate_survives_a_tick(self, engine):
        """tick() recomputed the rate from data_rate_mode, which no TC could set."""
        ttc = self._ready(engine)
        engine._dispatcher._handle_s8(1, bytes([65, 1]))
        for _ in range(5):
            ttc.tick(1.0, _orbit_state(), engine.params)
        assert ttc._state.tm_data_rate == 128000, (
            "the commanded HIGH tier was overwritten by the tick")

        engine._dispatcher._handle_s8(1, bytes([65, 0]))
        for _ in range(5):
            ttc.tick(1.0, _orbit_state(), engine.params)
        assert ttc._state.tm_data_rate == 16000

    def test_legacy_raw_bps_form_still_works(self, engine):
        """A 4-byte raw-bps argument equal to a configured tier still works."""
        ttc = self._ready(engine)
        engine._dispatcher._handle_s8(1, bytes([65]) + struct.pack('>I', 128000))
        assert ttc._state.tm_data_rate == 128000

    def test_high_rate_needs_a_deployed_antenna(self, engine):
        ttc = engine.subsystems["ttc"]
        ttc._state.antenna_deployed = False
        result = ttc.handle_command({"command": "set_tm_rate", "rate": 1})
        assert result["success"] is False

    def test_invalid_rate_is_refused(self, engine):
        ttc = self._ready(engine)
        assert ttc.handle_command(
            {"command": "set_tm_rate", "rate": 12345})["success"] is False


class TestRealTimeTMPacing:

    @staticmethod
    def _paced_engine(engine):
        engine._force_rt_pacing = True
        engine._rt_pending.clear()
        engine._rt_byte_budget = 0.0
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
        return engine

    def _fill(self, engine, n=40, size=64):
        for _ in range(n):
            engine._enqueue_tm(b'\x08\x01' + b'\x00' * (size - 2))

    def _released(self, engine):
        count = 0
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
            count += 1
        return count

    def test_low_rate_backs_telemetry_up(self, engine):
        self._paced_engine(engine)
        engine.subsystems["ttc"]._state.tm_data_rate = 1000     # BEACON, 125 B/s
        self._fill(engine)
        assert self._released(engine) == 0, "TM bypassed the pacer entirely"
        engine._tick_rt_emission(1.0)
        assert self._released(engine) <= 2
        assert engine._rt_pending, "the whole backlog drained at BEACON rate"

    def test_high_rate_flows(self, engine):
        self._paced_engine(engine)
        engine.subsystems["ttc"]._state.tm_data_rate = 128000   # NOMINAL, 16 kB/s
        self._fill(engine)
        engine._tick_rt_emission(1.0)
        assert not engine._rt_pending, "NOMINAL failed to drain 2560 B in 1 s"
        assert self._released(engine) == 40

    def test_a_rate_change_changes_what_the_radio_sees(self, engine):
        self._paced_engine(engine)
        engine.subsystems["ttc"]._state.tm_data_rate = 1000
        self._fill(engine, n=60)
        engine._tick_rt_emission(1.0)
        slow = self._released(engine)

        self._paced_engine(engine)
        engine.subsystems["ttc"]._state.tm_data_rate = 128000
        self._fill(engine, n=60)
        engine._tick_rt_emission(1.0)
        fast = self._released(engine)

        assert fast > slow, (
            f"the commanded rate had no observable effect ({slow} vs {fast})")

    def test_backlog_is_bounded(self, engine):
        self._paced_engine(engine)
        engine._rt_backlog_max = 5
        engine.subsystems["ttc"]._state.tm_data_rate = 1000
        self._fill(engine, n=50)
        assert len(engine._rt_pending) == 5
        assert engine.tm_queue_drops >= 45

    def test_pacing_is_off_for_untickled_engines(self, engine):
        """`auto` must not disturb callers that never tick the engine."""
        engine._force_rt_pacing = False
        engine.running = False
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
        engine._enqueue_tm(b'\x08\x01' + b'\x00' * 30)
        assert not engine.tm_queue.empty()


class TestSBandFrequencyPlan:

    def test_uplink_band(self, engine):
        ttc = engine.subsystems["ttc"]
        assert ttc.handle_command(
            {"command": "set_ul_freq", "freq_mhz": 2025.0})["success"] is True
        assert ttc.handle_command(
            {"command": "set_ul_freq", "freq_mhz": 449.0})["success"] is False

    def test_downlink_band(self, engine):
        ttc = engine.subsystems["ttc"]
        assert ttc.handle_command(
            {"command": "set_dl_freq", "freq_mhz": 2200.0})["success"] is True
        assert ttc.handle_command(
            {"command": "set_dl_freq", "freq_mhz": 8400.0})["success"] is False

    def test_configured_plan_is_inside_the_validated_bands(self, engine):
        ttc = engine.subsystems["ttc"]
        assert ttc.UL_BAND_MHZ[0] <= ttc._ul_freq_mhz <= ttc.UL_BAND_MHZ[1]
        dl = ttc._dl_freq_hz / 1e6
        assert ttc.DL_BAND_MHZ[0] <= dl <= ttc.DL_BAND_MHZ[1]


# ═══════════════════════════════════════════════════════════════════
# Defect #75 — HK SID default enable state
# ═══════════════════════════════════════════════════════════════════

class TestHKDefaultEnableState:

    def test_default_matches_config(self, engine):
        configured = set(engine._sim_cfg.housekeeping.default_enabled_sids)
        configured.add(BOOTLOADER_BEACON_SID)
        engine._apply_default_hk_enable()
        enabled = {sid for sid, on in engine._hk_enabled.items() if on}
        assert enabled == configured & set(engine._hk_enabled)

    def test_non_essential_sids_are_off_until_commanded(self, engine):
        engine._apply_default_hk_enable()
        assert engine._hk_enabled[5] is False, (
            "payload HK was enabled at boot without a command")

        engine._dispatcher.dispatch(3, 5, struct.pack('>H', 5))
        assert engine._hk_enabled[5] is True, "HK_ENABLE is still a no-op"

        engine._dispatcher.dispatch(3, 6, struct.pack('>H', 5))
        assert engine._hk_enabled[5] is False

    def test_application_boot_does_not_enable_everything(self, engine):
        engine._exit_bootloader_mode()
        assert engine._hk_enabled[5] is False
        assert engine._hk_enabled[BOOTLOADER_BEACON_SID] is True

    def test_only_enabled_sids_emit(self, engine):
        engine._apply_default_hk_enable()
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()
        engine._force_rt_pacing = False
        for _ in range(20):
            engine._emit_hk_packets(1.0)
        sids = set()
        while not engine.tm_queue.empty():
            parsed = decommutate_packet(engine.tm_queue.get_nowait())
            if parsed and parsed.secondary and parsed.secondary.service == 3:
                sids.add(parsed.data_field[0] if parsed.data_field else None)
        assert 5 not in sids


class TestBeaconHKCannotBeDisabledInBootloader:

    def test_disable_is_rejected_in_bootloader(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 0        # bootloader
        engine.params[0x0311] = 0
        engine._hk_enabled[BOOTLOADER_BEACON_SID] = True

        responses = engine._dispatcher.dispatch(
            3, 6, struct.pack('>H', BOOTLOADER_BEACON_SID))
        assert _fail_codes(responses) == [0x0005], (
            "HK_DISABLE(sid=11) was accepted while the OBC was in the bootloader")
        assert engine._hk_enabled[BOOTLOADER_BEACON_SID] is True, (
            "the beacon HK was silenced in the bootloader")

    def test_legacy_subtype_4_is_rejected_too(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 0
        responses = engine._dispatcher.dispatch(
            3, 4, struct.pack('>H', BOOTLOADER_BEACON_SID))
        assert _fail_codes(responses) == [0x0005]

    def test_disable_is_allowed_in_the_application(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 1
        engine.params[0x0311] = 1
        engine._hk_enabled[BOOTLOADER_BEACON_SID] = True
        engine._dispatcher.dispatch(3, 6, struct.pack('>H', BOOTLOADER_BEACON_SID))
        assert engine._hk_enabled[BOOTLOADER_BEACON_SID] is False

    def test_other_sids_are_never_gated(self, engine):
        engine.subsystems["obdh"]._state.sw_image = 0
        engine._hk_enabled[3] = True
        engine._dispatcher.dispatch(3, 6, struct.pack('>H', 3))
        assert engine._hk_enabled[3] is False


# ═══════════════════════════════════════════════════════════════════
# Roadmap N-1 / N-2 / N-5 / N-9
# ═══════════════════════════════════════════════════════════════════

class TestTickThreadGuard:

    def test_malformed_set_speed_is_rejected_at_the_door(self, engine):
        ok, reason = engine.submit_instructor_cmd(
            {"type": "set_speed", "value": "fast"})
        assert ok is False
        assert "number" in reason
        assert engine.instr_queue.empty()

    def test_unknown_command_type_is_rejected(self, engine):
        ok, _ = engine.submit_instructor_cmd({"type": "drop_tables"})
        assert ok is False

    def test_non_dict_payload_is_rejected(self, engine):
        assert engine.submit_instructor_cmd("set_speed 5")[0] is False
        assert engine.submit_instructor_cmd(None)[0] is False

    def test_out_of_range_values_are_rejected(self, engine):
        assert engine.submit_instructor_cmd(
            {"type": "set_speed", "value": -1})[0] is False
        assert engine.submit_instructor_cmd(
            {"type": "set_phase", "phase": 99})[0] is False
        assert engine.submit_instructor_cmd(
            {"type": "set_speed", "value": float("nan")})[0] is False

    def test_valid_command_is_accepted(self, engine):
        assert engine.submit_instructor_cmd(
            {"type": "set_speed", "value": 4})[0] is True
        engine._drain_instr_queue()
        assert engine.speed == 4.0

    def test_tick_survives_a_malformed_command_that_bypassed_validation(self, engine):
        """A payload put straight on the queue must not kill the tick thread.

        DI-01: `{"type":"set_speed","value":"fast"}` over the instructor
        WebSocket used to raise ValueError inside _drain_instr_queue, which
        killed the daemon sim thread while every server kept serving the last
        frozen state.
        """
        engine.instr_queue.put_nowait({"type": "set_speed", "value": "fast"})
        before = engine._tick_count
        engine._tick_once(1.0)
        assert engine._tick_count == before + 1, "the tick did not complete"
        assert engine.instr_cmd_rejects >= 1

    def test_run_loop_records_a_fault_and_continues(self, engine):
        boom = {"n": 0}

        def exploding_tick(dt):
            boom["n"] += 1
            if boom["n"] == 1:
                raise RuntimeError("synthetic tick failure")

        engine._tick_once = exploding_tick
        engine._sim_cfg.tick_hz = 50.0
        engine.start()
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and boom["n"] < 3:
                time.sleep(0.02)
        finally:
            engine.stop()
            time.sleep(0.1)
        assert boom["n"] >= 3, "the tick loop stopped after one exception"
        assert engine.engine_fault_count == 1
        assert "synthetic tick failure" in (engine.engine_fault or "")

    def test_engine_fault_is_visible_in_state(self, engine):
        engine._record_engine_fault(ValueError("kaboom"))
        summary = engine._build_state_summary()
        assert summary["engine_fault_count"] == 1
        assert "kaboom" in summary["engine_fault"]


class TestSnapshotThreadingContract:

    def test_readers_get_the_published_snapshot(self, engine):
        engine._tick_once(1.0)
        first = engine.get_state_summary()
        assert first is engine.get_state_summary(), (
            "each reader rebuilds state instead of taking the snapshot")
        engine._tick_once(1.0)
        assert engine.get_state_summary() is not first

    def test_snapshot_is_internally_consistent_under_concurrent_ticks(self, engine):
        """A reader must never see two fields from different ticks.

        The tick writes a matched (tick, marker) pair; a torn read shows a
        marker that does not belong to the snapshot's tick.
        """
        engine._tick_once(1.0)
        stop = threading.Event()
        torn: list[tuple] = []

        def writer():
            while not stop.is_set():
                engine.params[0x0101] = float(engine._tick_count % 90 + 5)
                engine._tick_once(0.01)

        def reader():
            while not stop.is_set():
                snap = engine.get_state_summary()
                params = snap["params"]
                if round(params.get(0x0101, 0.0), 1) != snap["eps"]["soc_pct"]:
                    torn.append((snap["tick"], params.get(0x0101),
                                 snap["eps"]["soc_pct"]))

        threads = [threading.Thread(target=writer),
                   threading.Thread(target=reader),
                   threading.Thread(target=reader)]
        for t in threads:
            t.start()
        time.sleep(0.6)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        assert not torn, f"torn reads observed: {torn[:5]}"

    def test_instructor_snapshot_uses_the_same_published_params(self, engine):
        engine._tick_once(1.0)
        summary = engine.get_state_summary()
        instructor = engine.get_instructor_snapshot()
        assert instructor["parameters"] == summary["params"]

    def test_reader_never_raises_while_the_tick_mutates_params(self, engine):
        """`dict(self.params)` on live state could raise mid-iteration."""
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer():
            i = 0
            while not stop.is_set():
                engine.params[0x7000 + (i % 500)] = float(i)
                engine._tick_once(0.01)
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    engine.get_state_summary()
                    engine.get_instructor_snapshot()
                except BaseException as e:  # noqa: BLE001
                    errors.append(e)
                    return

        threads = [threading.Thread(target=writer)] + \
                  [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        time.sleep(0.5)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        assert not errors, f"reader raised: {errors[:3]}"


class TestReproducibleEpoch:

    def test_engine_starts_at_t0_epoch(self, engine):
        assert engine._t0_epoch is not None
        assert engine._t0_epoch.year == 2026
        assert engine.orbit.utc.replace(microsecond=0) == \
            engine._t0_epoch.replace(microsecond=0)

    def test_two_runs_see_the_same_geometry(self):
        first = SimulationEngine(CONFIG_DIR)
        second = SimulationEngine(CONFIG_DIR)
        for _ in range(30):
            a = first.orbit.advance(60.0)
            b = second.orbit.advance(60.0)
            assert (a.lat_deg, a.lon_deg, a.in_eclipse, a.in_contact) == \
                (b.lat_deg, b.lon_deg, b.in_eclipse, b.in_contact)

    def test_wall_clock_mode_is_still_available(self, engine):
        engine._sim_cfg.epoch = "wall"
        assert engine._resolve_t0_epoch() is None


class TestAOCSOffArrayFallback:

    def _eps(self, engine):
        eps = engine.subsystems["eps"]
        eps._state.in_eclipse = False
        # No valid AOCS sun vector: zero both the CSS composite AND the
        # Phase C true-sun channel (0x02B3-B5) the EPS now prefers.
        for pid in (0x0245, 0x0246, 0x0247, 0x02B3, 0x02B4, 0x02B5):
            engine.params[pid] = 0.0
        return eps

    def test_uncontrolled_generation_is_physical(self, engine):
        """The old fallback produced ~245 W against ~82 W deployed (N-9)."""
        eps = self._eps(engine)
        orbit = _orbit_state()
        orbit.in_eclipse = False
        for _ in range(3):
            eps.tick(1.0, orbit, engine.params)
        gen = eps._state.power_gen_w
        assert gen < 60.0, (
            f"AOCS-off fallback still over-generates: {gen:.1f} W")
        assert gen >= 0.0

    def test_wing_deployment_increases_generation(self, engine):
        """Deploy-before-you-switch-things-on becomes a real constraint."""
        eps = self._eps(engine)
        orbit = _orbit_state()
        eps._state.wing_py_deployed = False
        eps._state.wing_my_deployed = False
        eps._panel_areas = dict(eps._panel_areas_body)
        eps.tick(1.0, orbit, engine.params)
        stowed = eps._state.power_gen_w

        eps._state.wing_py_deployed = True
        eps._state.wing_my_deployed = True
        eps.tick(1.0, orbit, engine.params)
        deployed = eps._state.power_gen_w
        assert deployed > stowed * 1.5, (
            f"wing deployment barely mattered: {stowed:.1f} -> {deployed:.1f} W")

    def test_disabled_array_reduces_generation(self, engine):
        eps = self._eps(engine)
        orbit = _orbit_state()
        eps.tick(1.0, orbit, engine.params)
        both = eps._state.power_gen_w
        eps._state.sa_b_enabled = False
        eps.tick(1.0, orbit, engine.params)
        assert eps._state.power_gen_w < both
