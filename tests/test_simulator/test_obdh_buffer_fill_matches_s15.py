"""The OBDH buffer-fill telemetry IS the S15 on-board store fill.

Owner report: "the buffer fill status in the OBDH window occasionally gets to
a very high value even though the buffer status in the PUS services window
reports buffers that are not close to full."

There is exactly one set of on-board stores, so there must be exactly one
number per store. ``obdh.hktm_buf_fill`` / ``event_buf_fill`` /
``alarm_buf_fill`` (0x0312 / 0x0313 / 0x0314) are the fills of S15 stores 1, 2
and 4 carried in routine HK; ``OnboardTMStorage.get_status()`` is what
TM(15,14) — and therefore the console's S15 Storage page — reports.

Before the fix 0x0312 was a free-running stub: a random percentage accumulated
against a fictitious 1000-slot capacity, drained only while the TTC carrier was
locked. Measured against the shipped config it saturated at 100 % about 25
minutes into every gap between passes while the real 18 000-slot HK_Store sat
at 15–35 %, and it read 0 % during a pass while the real store kept growing.
0x0313 / 0x0314 were raw packet counts declared with units "%" that nothing
ever incremented.

These tests pin the relationship in both directions so the two pages cannot
silently diverge again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from smo_simulator.models.obdh_basic import (
    BUFFER_STORE_IDS, OBDHBasicModel, SW_APPLICATION,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

#: telemetry parameter id ← BUFFER_STORE_IDS key
PARAM_FOR_KEY = {"hktm": 0x0312, "event": 0x0313, "alarm": 0x0314}

#: The OBDH model samples the stores during its own tick; packets emitted later
#: in the same tick land after it, so a live engine comparison can be one
#: tick's worth of packets stale. That is at most a couple of packets in the
#: smallest (500-slot) store. The exact formula is pinned separately in
#: ``test_fill_is_exactly_count_over_capacity``.
TOL = 0.5


def _store_fills(eng) -> dict[int, dict]:
    return {int(s["id"]): s for s in eng._tm_storage.get_status()}


def _assert_agreement(eng, where: str) -> None:
    status = _store_fills(eng)
    for key, store_id in BUFFER_STORE_IDS.items():
        param_id = PARAM_FOR_KEY[key]
        reported = float(eng.params.get(param_id, -1.0))
        st = status[store_id]
        expected = st["count"] / st["capacity"] * 100.0
        assert reported == pytest.approx(expected, abs=TOL), (
            f"{where}: OBDH 0x{param_id:04X} reads {reported:.2f} % but S15 "
            f"store {store_id} ({st['name']}) holds {st['count']}/"
            f"{st['capacity']} = {expected:.2f} %"
        )
        assert 0.0 <= reported <= 100.0, (
            f"{where}: 0x{param_id:04X} = {reported} is not a percentage"
        )
        # And the value TM(15,14) carries is the same number again.
        assert reported == pytest.approx(float(st["fill_pct"]), abs=TOL)


@pytest.mark.slow
def test_obdh_buffer_fill_tracks_s15_stores_over_time():
    """Fill the stores, dump them, and check both quantities all the way."""
    if not CONFIG_DIR.exists():
        pytest.skip("EOSAT-1 config dir not present")

    from smo_simulator.engine import SimulationEngine

    eng = SimulationEngine(CONFIG_DIR, speed=1.0)
    d = eng._dispatcher

    def run(seconds: float) -> None:
        for _ in range(int(seconds)):
            eng._tick_once(1.0)

    # Boot the application so HK is produced and the stores fill.
    d.dispatch(8, 1, bytes([55]), None)
    run(20)
    _assert_agreement(eng, "after boot")

    peak = 0.0
    for _ in range(12):
        run(120)
        _assert_agreement(eng, "while filling")
        peak = max(peak, float(eng.params[0x0312]))

    assert eng._tm_storage.get_status()[0]["count"] > 100, (
        "the HK store should have taken real packets in ~25 min of run time"
    )
    # The stub used to be at 100 % by this point in the pass gap.
    assert peak < 50.0, (
        f"HK store fill reached {peak:.1f} % in ~25 min of simulated time — "
        "that is the free-running stub behaviour, not an 18 000-slot store"
    )

    # A dump (TC(15,9)) must be reported to the operator, not raise, and the
    # OBDH parameter must follow the store down when the store is cleared.
    res = d.dispatch(15, 9, bytes([1]), None)
    assert res.error is None, res.error
    run(60)
    d.dispatch(15, 11, bytes([1]), None)
    run(2)
    assert float(eng.params[0x0312]) == pytest.approx(0.0, abs=TOL), (
        "after TC(15,11) DELETE_STORE the OBDH HK buffer fill must read empty"
    )
    _assert_agreement(eng, "after dump and delete")

    # Refill: the parameter must climb again from zero, i.e. it is not latched.
    run(300)
    assert float(eng.params[0x0312]) > 0.0
    _assert_agreement(eng, "after refill")


def test_fill_is_exactly_count_over_capacity():
    """Exact agreement, pinned against the real OnboardTMStorage.

    No engine, no timing: the OBDH parameters must be byte-for-byte the same
    arithmetic ``get_status()`` publishes in TM(15,14).
    """
    from smo_simulator.tm_storage import OnboardTMStorage

    class _Engine:
        pass

    storage = OnboardTMStorage()
    eng = _Engine()
    eng._tm_storage = storage

    model = OBDHBasicModel()
    model.configure({})
    model._engine = eng
    model._state.sw_image = SW_APPLICATION

    for store_id, n in ((1, 1234), (2, 77), (4, 13)):
        for i in range(n):
            assert storage.store_packet_direct(store_id, bytes([i & 0xFF]))

    params: dict[int, float] = {}
    model.tick(1.0, None, params)

    status = {int(s["id"]): s for s in storage.get_status()}
    for key, store_id in BUFFER_STORE_IDS.items():
        st = status[store_id]
        assert params[PARAM_FOR_KEY[key]] == (
            st["count"] / st["capacity"] * 100.0), (
            f"store {store_id}: OBDH parameter and TM(15,14) must be the same "
            "arithmetic on the same store"
        )
        assert round(params[PARAM_FOR_KEY[key]], 1) == st["fill_pct"]

    # Deleting the store empties the OBDH reading too — the old stub kept its
    # own number and would have carried on regardless.
    storage.delete_store(1)
    model.tick(1.0, None, params)
    assert params[0x0312] == 0.0


def test_buffer_fill_is_percent_not_packet_count():
    """A standalone model reports percentages, never raw counts.

    The event store holds hundreds of packets and the console renders these
    parameters with a '%' suffix; a count in a percent field is what produced
    the "very high value" the owner saw.
    """
    model = OBDHBasicModel()
    model.configure({"buffers": {"hktm_capacity": 1000,
                                 "event_capacity": 500,
                                 "alarm_capacity": 200}})
    model._state.sw_image = SW_APPLICATION

    for _ in range(250):
        assert model.record_event() is True
    for _ in range(100):
        assert model.record_alarm() is True

    params: dict[int, float] = {}
    model.tick(1.0, None, params)

    assert params[0x0313] == pytest.approx(50.0)   # 250 / 500
    assert params[0x0314] == pytest.approx(50.0)   # 100 / 200
    for param_id in PARAM_FOR_KEY.values():
        assert 0.0 <= params[param_id] <= 100.0


def test_buffer_fill_never_free_runs_without_a_store():
    """With no S15 storage the HK fill holds at zero; it is never invented."""
    model = OBDHBasicModel()
    model.configure({})
    model._state.sw_image = SW_APPLICATION
    params: dict[int, float] = {0x0510: 0}  # no carrier lock
    for _ in range(600):
        model.tick(1.0, None, params)
    assert params[0x0312] == 0.0, (
        "hktm_buf_fill must come from the S15 HK store, not from a random walk"
    )
