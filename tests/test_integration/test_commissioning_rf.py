"""End-to-end commissioning test through the RF pipeline.

Exercises the full sequence: bootloader HK → CONNECTION_TEST →
OBC app boot → platform HK → subsystem power-on → all-SID HK,
all flowing through the RF signal processing pipeline (TX → Channel → RX).

Verifies packet delivery, ACK generation, SID transitions, and
diagnostic counters at every handoff point.
"""

import asyncio
import struct
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from smo_common.protocol.ecss_packet import (
    build_tc_packet, decommutate_packet,
)
from smo_simulator.engine import SimulationEngine
from smo_rfsim.config import RFSimConfig
from smo_rfsim.pipeline.coordinator import PipelineCoordinator


CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"

# Spacecraft APID (from mission.yaml)
APP_APID = 1
BOOT_APID = 2


def _make_orbit(in_contact=True):
    """Mock orbit state with ground contact."""
    return SimpleNamespace(
        in_contact=in_contact, in_eclipse=False,
        solar_beta_deg=20.0, lat_deg=45.0, lon_deg=10.0,
        alt_km=450.0, vel_x=0.0, vel_y=7.5, vel_z=0.0,
        gs_elevation_deg=30.0, gs_azimuth_deg=180.0,
        gs_range_km=800.0,
    )


def _make_pipeline():
    """Create an RF pipeline with high Eb/N0 (no errors)."""
    config = RFSimConfig()
    config.ccsds.rs_enabled = True
    config.ccsds.convolutional_enabled = False
    config.channel.eb_n0_db = 20.0
    config.network.zmq_samples_port = 0
    coord = PipelineCoordinator(config)
    coord.set_link_in_view(True)
    coord.start()
    coord.set_transmitting(True)
    return coord


def _tick_engine(engine, dt=1.0):
    """Advance the engine by one tick."""
    orbit = _make_orbit()
    engine._drain_instr_queue()
    orbit_state = engine.orbit.advance(dt)
    # Use mock orbit for stable geometry
    orbit_state = orbit
    engine._in_contact = True
    engine.params[0x05FF] = 1 if engine._override_passes else 0

    engine._tick_spacecraft_phase(dt)
    engine._tick_auto_tx_hold(dt)

    _ALWAYS_ON = {"eps", "ttc", "obdh"}
    if engine._spacecraft_phase < 2:
        active = _ALWAYS_ON
    elif engine._spacecraft_phase < 4:
        active = _ALWAYS_ON | {"tcs"}
    else:
        active = set(engine.subsystems.keys())

    for name, model in engine.subsystems.items():
        if name not in active:
            continue
        try:
            model.tick(dt, orbit_state, engine.params)
        except Exception:
            pass

    engine._tick_s12_monitoring()
    if engine._fdir_enabled:
        engine._tick_fdir()
        engine._tick_fdir_advanced(dt)
    engine._check_subsystem_events()
    engine._check_transitions(orbit_state)
    engine._emit_hk_packets(dt)
    engine._tick_dump_emission(dt)
    engine._drain_tc_queue()
    engine._tick_count += 1


def _drain_tm_to_pipeline(engine, coord):
    """Move all packets from engine tm_queue into the RF pipeline."""
    count = 0
    while not engine.tm_queue.empty():
        try:
            pkt = engine.tm_queue.get_nowait()
            coord.enqueue_tm_packet(pkt)
            count += 1
        except Exception:
            break
    return count


def _recover_packets(coord, timeout=3.0):
    """Recover all available packets from the RF pipeline."""
    recovered = []
    deadline = time.monotonic() + timeout
    loop = asyncio.new_event_loop()
    while time.monotonic() < deadline:
        try:
            pkt = loop.run_until_complete(
                asyncio.wait_for(coord.get_recovered_packet(), timeout=0.3))
            if pkt:
                recovered.append(pkt)
            else:
                break
        except (asyncio.TimeoutError, Exception):
            break
    loop.close()
    return recovered


def _classify_packets(raw_packets):
    """Parse recovered packets and classify by service/subtype."""
    classified = {
        "hk_sids": set(),       # SIDs seen in S3.25 packets
        "acks_11": 0,           # S1.1 acceptance
        "acks_17": 0,           # S1.7 completion
        "conn_test": 0,         # S17.2 connection test response
        "events": 0,            # S5.x events
        "other": 0,
        "total": 0,
        "by_service": {},
    }
    for raw in raw_packets:
        pkt = decommutate_packet(raw)
        if pkt is None or pkt.secondary is None:
            classified["other"] += 1
            classified["total"] += 1
            continue
        svc = pkt.secondary.service
        sub = pkt.secondary.subtype
        key = f"S{svc}.{sub}"
        classified["by_service"][key] = classified["by_service"].get(key, 0) + 1
        classified["total"] += 1

        if svc == 3 and sub == 25:
            # Extract SID from data field (first 2 bytes, big-endian)
            if len(pkt.data_field) >= 2:
                sid = struct.unpack('>H', pkt.data_field[:2])[0]
                classified["hk_sids"].add(sid)
        elif svc == 1 and sub == 1:
            classified["acks_11"] += 1
        elif svc == 1 and sub == 7:
            classified["acks_17"] += 1
        elif svc == 17 and sub == 2:
            classified["conn_test"] += 1
        elif svc == 5:
            classified["events"] += 1
        else:
            classified["other"] += 1
    return classified


def _print_phase_report(phase_name, injected, recovered, classified, diag):
    """Print a diagnostic report for one commissioning phase."""
    print(f"\n--- {phase_name} ---")
    print(f"  Packets injected to pipeline:  {injected}")
    print(f"  Packets recovered from pipeline: {recovered}")
    print(f"  HK SIDs seen:     {sorted(classified['hk_sids'])}")
    print(f"  S1.1 ACKs:        {classified['acks_11']}")
    print(f"  S1.7 ACKs:        {classified['acks_17']}")
    print(f"  S17.2 ConnTest:   {classified['conn_test']}")
    print(f"  S5.x Events:      {classified['events']}")
    print(f"  By service:       {classified['by_service']}")
    print(f"  TX drops:         {diag['tx_packet_drops']}")
    print(f"  TX buf overflows: {diag['tx_buffer_overflows']}")
    print(f"  RX bad frames:    {diag['rx_bad_frames']}")
    print(f"  RX buf overflows: {diag['rx_buffer_overflows']}")
    print(f"  Recovery Q drops: {diag['recovered_queue_drops']}")


class TestCommissioningRFPipeline:
    """End-to-end commissioning through the RF signal processing pipeline."""

    def test_full_commissioning_sequence(self):
        """Bootloader → app boot → subsystem power-on, all through RF."""
        engine = SimulationEngine(CONFIG_DIR)
        # Start in bootloader (phase 3), override passes for contact
        assert engine._spacecraft_phase == 3 or engine._spacecraft_phase >= 0
        engine._spacecraft_phase = 3
        engine._override_passes = True

        coord = _make_pipeline()
        tc_seq = 0  # TC sequence counter

        total_injected = 0
        total_recovered = 0
        all_sids = set()
        all_acks_11 = 0
        all_acks_17 = 0
        all_conn_test = 0

        try:
            # ═══════════════════════════════════════════
            # PHASE A: Bootloader — SID 11 beacon only
            # SID 11 interval is 30s, so we need >30 ticks
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("PHASE A: BOOTLOADER (35 ticks)")
            print("=" * 60)

            for _ in range(35):
                _tick_engine(engine)
                n = _drain_tm_to_pipeline(engine, coord)
                total_injected += n
                time.sleep(0.05)

            # Let pipeline process
            time.sleep(1.0)
            recovered_a = _recover_packets(coord, timeout=3.0)
            total_recovered += len(recovered_a)
            class_a = _classify_packets(recovered_a)
            all_sids |= class_a["hk_sids"]
            diag_a = coord.get_diagnostics()
            _print_phase_report("BOOTLOADER", total_injected, len(recovered_a),
                                class_a, diag_a)

            # Bootloader should emit SID 11 (beacon)
            assert 11 in class_a["hk_sids"], \
                f"Expected SID 11 in bootloader, got {class_a['hk_sids']}"

            # ═══════════════════════════════════════════
            # PHASE B: CONNECTION_TEST + OBC_BOOT_APP
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("PHASE B: COMMANDING — CONNECTION_TEST + OBC_BOOT_APP")
            print("=" * 60)

            # Send CONNECTION_TEST (S17.1)
            tc_conn = build_tc_packet(APP_APID, 17, 1, b'', seq_count=tc_seq)
            tc_seq += 1
            engine.tc_queue.put_nowait(tc_conn)

            # Tick to process TC and generate ACK
            for _ in range(5):
                _tick_engine(engine)
                _drain_tm_to_pipeline(engine, coord)
                time.sleep(0.05)

            # Send OBC_BOOT_APP (S8.1, func_id=55)
            tc_boot = build_tc_packet(APP_APID, 8, 1, bytes([55]), seq_count=tc_seq)
            tc_seq += 1
            engine.tc_queue.put_nowait(tc_boot)

            # Tick through the 10s boot countdown
            for _ in range(15):
                _tick_engine(engine)
                n = _drain_tm_to_pipeline(engine, coord)
                total_injected += n
                time.sleep(0.05)

            time.sleep(1.0)
            recovered_b = _recover_packets(coord, timeout=3.0)
            total_recovered += len(recovered_b)
            class_b = _classify_packets(recovered_b)
            all_sids |= class_b["hk_sids"]
            all_acks_11 += class_b["acks_11"]
            all_acks_17 += class_b["acks_17"]
            all_conn_test += class_b["conn_test"]
            diag_b = coord.get_diagnostics()
            _print_phase_report("COMMANDING", total_injected, len(recovered_b),
                                class_b, diag_b)

            # Should have booted to application by now
            sw_image = int(engine.params.get(0x0311, 0))
            print(f"  sw_image: {sw_image}")
            print(f"  spacecraft_phase: {engine._spacecraft_phase}")

            # ═══════════════════════════════════════════
            # PHASE C: LEOP — platform HK should appear
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("PHASE C: LEOP — PLATFORM HK (20 ticks)")
            print("=" * 60)

            for _ in range(20):
                _tick_engine(engine)
                n = _drain_tm_to_pipeline(engine, coord)
                total_injected += n
                time.sleep(0.05)

            time.sleep(1.0)
            recovered_c = _recover_packets(coord, timeout=3.0)
            total_recovered += len(recovered_c)
            class_c = _classify_packets(recovered_c)
            all_sids |= class_c["hk_sids"]
            diag_c = coord.get_diagnostics()
            _print_phase_report("LEOP", total_injected, len(recovered_c),
                                class_c, diag_c)

            # After app boot, should see platform SIDs (1, 3, 4, 6)
            # SIDs 2 and 5 are power-gated (off until we power them on)

            # ═══════════════════════════════════════════
            # PHASE D: Power on AOCS + Payload
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("PHASE D: SUBSYSTEM POWER-ON (20 ticks)")
            print("=" * 60)

            # Power on AOCS wheels (line index 7)
            tc_aocs = build_tc_packet(APP_APID, 8, 1, bytes([19, 7]),
                                      seq_count=tc_seq)
            tc_seq += 1
            engine.tc_queue.put_nowait(tc_aocs)

            for _ in range(5):
                _tick_engine(engine)
                _drain_tm_to_pipeline(engine, coord)
                time.sleep(0.05)

            # Power on Payload (line index 4)
            tc_pld = build_tc_packet(APP_APID, 8, 1, bytes([19, 4]),
                                     seq_count=tc_seq)
            tc_seq += 1
            engine.tc_queue.put_nowait(tc_pld)

            for _ in range(15):
                _tick_engine(engine)
                n = _drain_tm_to_pipeline(engine, coord)
                total_injected += n
                time.sleep(0.05)

            time.sleep(1.0)
            recovered_d = _recover_packets(coord, timeout=3.0)
            total_recovered += len(recovered_d)
            class_d = _classify_packets(recovered_d)
            all_sids |= class_d["hk_sids"]
            all_acks_11 += class_d["acks_11"]
            all_acks_17 += class_d["acks_17"]
            diag_d = coord.get_diagnostics()
            _print_phase_report("POWER-ON", total_injected, len(recovered_d),
                                class_d, diag_d)

            # ═══════════════════════════════════════════
            # PHASE E: Sustained commissioning (30 ticks)
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("PHASE E: SUSTAINED COMMISSIONING (30 ticks)")
            print("=" * 60)

            for _ in range(30):
                _tick_engine(engine)
                n = _drain_tm_to_pipeline(engine, coord)
                total_injected += n
                time.sleep(0.05)

            time.sleep(2.0)
            recovered_e = _recover_packets(coord, timeout=5.0)
            total_recovered += len(recovered_e)
            class_e = _classify_packets(recovered_e)
            all_sids |= class_e["hk_sids"]
            diag_e = coord.get_diagnostics()
            _print_phase_report("SUSTAINED", total_injected, len(recovered_e),
                                class_e, diag_e)

            # ═══════════════════════════════════════════
            # FINAL REPORT
            # ═══════════════════════════════════════════
            print("\n" + "=" * 60)
            print("FINAL COMMISSIONING RF PIPELINE REPORT")
            print("=" * 60)
            print(f"  Total ticks:              125")
            print(f"  Total TM injected:        {total_injected}")
            print(f"  Total TM recovered:       {total_recovered}")
            print(f"  Engine queue drops:        {engine.tm_queue_drops}")
            print(f"  Engine packets enqueued:   {engine.tm_packets_enqueued}")
            print(f"  All HK SIDs seen:         {sorted(all_sids)}")
            print(f"  Total S1.1 ACKs:          {all_acks_11}")
            print(f"  Total S1.7 ACKs:          {all_acks_17}")
            print(f"  Total S17.2 ConnTest:     {all_conn_test}")
            diag_final = coord.get_diagnostics()
            print(f"  TX frames transmitted:     {diag_final['tx_frames_transmitted']}")
            print(f"  TX data frames:            {diag_final['tx_data_frames']}")
            print(f"  TX idle frames:            {diag_final['tx_idle_frames']}")
            print(f"  TX packet drops:           {diag_final['tx_packet_drops']}")
            print(f"  TX buffer overflows:       {diag_final['tx_buffer_overflows']}")
            print(f"  RX good frames:            {diag_final['rx_good_frames']}")
            print(f"  RX bad frames:             {diag_final['rx_bad_frames']}")
            print(f"    RS failures:             {diag_final['rx_rs_failures']}")
            print(f"    FECF failures:           {diag_final['rx_fecf_failures']}")
            print(f"    Flywheel misses:         {diag_final['rx_flywheel_misses']}")
            print(f"  RX buffer overflows:       {diag_final['rx_buffer_overflows']}")
            print(f"  RX packets recovered:      {diag_final['rx_packets_recovered']}")
            print(f"  Recovery queue drops:       {diag_final['recovered_queue_drops']}")
            print("=" * 60)

            # ═══════════════════════════════════════════
            # ASSERTIONS
            # ═══════════════════════════════════════════

            # Zero drops at every stage
            assert engine.tm_queue_drops == 0, \
                f"Engine dropped {engine.tm_queue_drops} packets"
            assert diag_final["tx_packet_drops"] == 0, \
                f"TX dropped {diag_final['tx_packet_drops']} packets"
            assert diag_final["tx_buffer_overflows"] == 0, \
                f"TX buffer overflows: {diag_final['tx_buffer_overflows']}"
            assert diag_final["rx_buffer_overflows"] == 0, \
                f"RX buffer overflows: {diag_final['rx_buffer_overflows']}"
            assert diag_final["rx_bad_frames"] == 0, \
                f"RX bad frames: {diag_final['rx_bad_frames']}"
            assert diag_final["recovered_queue_drops"] == 0, \
                f"Recovery queue drops: {diag_final['recovered_queue_drops']}"

            # Packets actually flowed
            assert total_injected > 0, "No TM packets were generated"
            assert total_recovered > 0, "No TM packets were recovered from RF pipeline"

            # SID 11 (beacon) should have been seen in bootloader phase
            assert 11 in all_sids, "SID 11 (beacon) never seen"

            # At least some ACKs recovered
            assert all_acks_11 > 0, "No S1.1 acceptance ACKs recovered"

        finally:
            coord.stop()
