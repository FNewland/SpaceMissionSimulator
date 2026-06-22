"""Dispatch defects B and C.

B: S8 legacy quick-action funcs 100-107 (EPS solar array / TCS heaters) were
   silently dropped (_handle_s8 fell through to []). Now routed to existing
   EPS/TCS handlers.
C: S8 func 49 (TCS_GET_THERMAL_MAP) acknowledged but returned no telemetry.
   Now packs an S8.2 TM reply carrying the thermal map, mirroring funcs 61/62.
"""
import struct

import pytest

from smo_simulator.engine import SimulationEngine

CONFIG_DIR = "configs/eosat1"


@pytest.fixture
def engine():
    return SimulationEngine(CONFIG_DIR)


# ─── Defect B: legacy quick-action funcs 100-107 ──────────────────────────

def test_legacy_func_100_enables_solar_array_a(engine):
    """Func 100 (EPS_SA_A_ON_LEGACY) must actually enable solar array A."""
    eps = engine.subsystems["eps"]
    eps._state.sa_a_enabled = False
    engine._dispatcher._handle_s8(1, bytes([100]))
    assert eps._state.sa_a_enabled is True


def test_legacy_func_101_disables_solar_array_a(engine):
    eps = engine.subsystems["eps"]
    eps._state.sa_a_enabled = True
    engine._dispatcher._handle_s8(1, bytes([101]))
    assert eps._state.sa_a_enabled is False


def test_legacy_func_102_103_solar_array_b(engine):
    eps = engine.subsystems["eps"]
    eps._state.sa_b_enabled = False
    engine._dispatcher._handle_s8(1, bytes([102]))
    assert eps._state.sa_b_enabled is True
    engine._dispatcher._handle_s8(1, bytes([103]))
    assert eps._state.sa_b_enabled is False


def test_legacy_func_104_107_heaters(engine):
    """Funcs 104-107 toggle the battery and OBC heaters via TCS."""
    tcs = engine.subsystems["tcs"]
    engine._dispatcher._handle_s8(1, bytes([104]))  # battery heater ON
    assert tcs._state.htr_battery is True
    engine._dispatcher._handle_s8(1, bytes([105]))  # battery heater OFF
    assert tcs._state.htr_battery is False
    engine._dispatcher._handle_s8(1, bytes([106]))  # OBC heater ON
    assert tcs._state.htr_obc is True
    engine._dispatcher._handle_s8(1, bytes([107]))  # OBC heater OFF
    assert tcs._state.htr_obc is False


def test_legacy_funcs_in_catalog(engine):
    """The 8 legacy quick-action commands load from the catalog (defect E too)."""
    from pathlib import Path
    from smo_common.config.loader import load_tc_catalog

    names = {c.name for c in load_tc_catalog(Path(CONFIG_DIR))}
    for n in [
        "EPS_SA_A_ON_LEGACY", "EPS_SA_A_OFF_LEGACY",
        "EPS_SA_B_ON_LEGACY", "EPS_SA_B_OFF_LEGACY",
        "TCS_HEATER_BAT_ON_LEGACY", "TCS_HEATER_BAT_OFF_LEGACY",
        "TCS_HEATER_OBC_ON_LEGACY", "TCS_HEATER_OBC_OFF_LEGACY",
    ]:
        assert n in names, f"{n} missing from catalog"


# ─── Defect C: func 49 thermal map returns a TM ───────────────────────────

def _unpack_tm_header(pkt):
    """Return (service, subtype, data) from a _pack_tm packet."""
    # primary(6) + sec_hdr: 0x10, service, subtype, cuc(4) -> data starts at 13
    service = pkt[7]
    subtype = pkt[8]
    data = pkt[13:-2]  # strip trailing CRC16
    return service, subtype, data


def test_func_49_returns_thermal_map_tm(engine):
    resp = engine._dispatcher._handle_s8(1, bytes([49]))
    assert resp, "func 49 returned no TM packet"
    service, subtype, data = _unpack_tm_header(resp[0])
    assert service == 8 and subtype == 2, "expected an S8.2 reply"
    # 10 float32 temps were packed.
    assert len(data) == 10 * 4
    temps = struct.unpack('>' + 'f' * 10, data)
    # The thermal map should carry real (finite, plausible) temperatures.
    tcs = engine.subsystems["tcs"]
    assert temps[6] == pytest.approx(tcs._state.temp_obc, abs=1e-3)
    assert temps[8] == pytest.approx(tcs._state.temp_fpa, abs=1e-3)
