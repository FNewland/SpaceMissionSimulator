"""Memory profiling tests — verify no unbounded growth under sustained load.

Runs the engine for extended periods and checks that key data structures
stay bounded. Uses tracemalloc for Python-level memory tracking.

Ref: Static analysis findings in commit 4757956
"""

import tracemalloc
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


def _tick(engine, n=1):
    orbit = SimpleNamespace(
        in_contact=True, in_eclipse=False, solar_beta_deg=20.0,
        lat_deg=45.0, lon_deg=10.0, alt_km=450.0,
        vel_x=0.0, vel_y=7.5, vel_z=0.0,
        gs_elevation_deg=30.0, gs_azimuth_deg=180.0, gs_range_km=800.0,
    )
    for _ in range(n):
        engine._drain_instr_queue()
        engine._in_contact = True
        engine.params[0x05FF] = 1
        engine._tick_spacecraft_phase(1.0)
        engine._tick_auto_tx_hold(1.0)
        for name, model in engine.subsystems.items():
            try:
                model.tick(1.0, orbit, engine.params)
            except Exception:
                pass
        engine._tick_s12_monitoring()
        engine._check_subsystem_events()
        engine._emit_hk_packets(1.0)
        engine._tick_dump_emission(1.0)
        engine._drain_tc_queue()
        engine._tick_count += 1
        # Drain TM queue to prevent buildup
        while not engine.tm_queue.empty():
            engine.tm_queue.get_nowait()


class TestMemoryProfile:

    def test_sustained_ticking_no_growth(self):
        """Run engine for 500 ticks, verify memory doesn't grow unbounded."""
        tracemalloc.start()

        engine = SimulationEngine(CONFIG_DIR)
        engine._spacecraft_phase = 6
        engine.params[0x0311] = 1
        engine._override_passes = True
        engine._fdir_enabled = False
        obdh = engine.subsystems.get("obdh")
        if obdh and hasattr(obdh, "_state"):
            obdh._state.sw_image = 1
            obdh._state.watchdog_armed = False
        for sid in engine._hk_enabled:
            engine._hk_enabled[sid] = True

        # Warm up
        _tick(engine, 50)
        snapshot1 = tracemalloc.take_snapshot()
        mem1 = sum(s.size for s in snapshot1.statistics('filename'))

        # Sustained run
        _tick(engine, 500)
        snapshot2 = tracemalloc.take_snapshot()
        mem2 = sum(s.size for s in snapshot2.statistics('filename'))

        growth_mb = (mem2 - mem1) / (1024 * 1024)
        print(f"\n  Memory after 50 ticks:  {mem1 / 1024:.0f} KB")
        print(f"  Memory after 550 ticks: {mem2 / 1024:.0f} KB")
        print(f"  Growth: {growth_mb:.2f} MB")

        tracemalloc.stop()

        # Allow 5 MB growth max for 500 ticks (should be < 1 MB)
        assert growth_mb < 5.0, \
            f"Memory grew {growth_mb:.1f} MB over 500 ticks (leak?)"

    def test_data_structure_bounds(self):
        """Verify key data structures stay bounded after 200 ticks."""
        engine = SimulationEngine(CONFIG_DIR)
        engine._spacecraft_phase = 6
        engine.params[0x0311] = 1
        engine._override_passes = True
        engine._fdir_enabled = False
        obdh = engine.subsystems.get("obdh")
        if obdh and hasattr(obdh, "_state"):
            obdh._state.sw_image = 1
            obdh._state.watchdog_armed = False
        for sid in engine._hk_enabled:
            engine._hk_enabled[sid] = True

        _tick(engine, 200)

        # Check bounds
        disp = engine._dispatcher
        print(f"\n  params dict size: {len(engine.params)}")
        print(f"  S3 custom SIDs: {len(disp._s3_custom_sids)}")
        print(f"  S12 definitions: {len(disp._s12_definitions)}")
        print(f"  S19 definitions: {len(disp._s19_definitions)}")
        print(f"  S13 transfers: {len(disp._s13_transfers)}")
        print(f"  dump pending: {len(engine._dump_pending)}")

        payload = engine.subsystems.get("payload")
        if payload and hasattr(payload, "_state"):
            print(f"  image catalog: {len(payload._state.image_catalog)}")
            assert len(payload._state.image_catalog) <= 1000

        assert len(engine.params) < 500, "Param store grew too large"
        assert len(disp._s13_transfers) == 0, "S13 transfers not cleaned"
        assert len(engine._dump_pending) < 5000, "Dump queue too large"
