"""TTC uplink-lock timing is decoupled from the downlink TM data rate.

Regression for the modelling fix: the carrier->bit->frame receive-lock
sequence is an *uplink* acquisition, so it must be paced by the uplink command
rate, not the downlink TM rate. Previously entering beacon / low-rate downlink
tripled the uplink lock time (~30 s instead of the nominal ~17 s).
"""
from types import SimpleNamespace

from smo_simulator.models.ttc_basic import TTCBasicModel


def _orbit():
    # orbit not in real contact; the test drives contact via pass-override
    # (param 0x05FF), which gives a fixed 500 km nominal geometry.
    return SimpleNamespace(in_contact=False, gs_range_km=500.0,
                           gs_elevation_deg=45.0, gs_azimuth_deg=180.0)


def _new_model(beacon: bool) -> TTCBasicModel:
    m = TTCBasicModel()
    m.configure({})
    s = m._state
    s.pa_on = True
    s.antenna_deployed = True
    s.beacon_mode = beacon
    s.data_rate_mode = 0 if beacon else 1
    s.uplink_lost = False
    return m


def _ticks_to_frame_sync(beacon: bool) -> int:
    m = _new_model(beacon)
    params = {0x05FF: 1}  # pass override on
    for t in range(1, 60):
        m.tick(1.0, _orbit(), params)
        if m._state.frame_sync:
            return t
    return -1


def test_uplink_lock_same_in_beacon_and_highrate():
    """Frame sync must take the same (~11 s) time whether or not the downlink
    is in beacon/low-rate mode — the downlink rate must not affect uplink lock."""
    t_beacon = _ticks_to_frame_sync(beacon=True)
    t_highrate = _ticks_to_frame_sync(beacon=False)
    assert t_beacon == t_highrate, (
        f"uplink lock should not depend on downlink rate: "
        f"beacon={t_beacon}s vs high-rate={t_highrate}s"
    )
    # Nominal sequence is carrier 2s + bit 5s + frame 10s; frame_sync becomes
    # true once lock_timer >= 10 (the tick after 10 cumulative seconds).
    assert 10 <= t_beacon <= 12, f"frame sync should be ~11 s, got {t_beacon}s"


def test_low_uplink_rate_still_slows_lock():
    """The capability is preserved: a genuinely low UPLINK rate slows lock."""
    m = _new_model(beacon=False)
    m._tc_uplink_rate_bps = m._tm_rate_lo  # force low uplink command rate
    params = {0x05FF: 1}
    t = -1
    for i in range(1, 80):
        m.tick(1.0, _orbit(), params)
        if m._state.frame_sync:
            t = i
            break
    assert t >= 28, f"low uplink rate should give ~30 s lock, got {t}s"
