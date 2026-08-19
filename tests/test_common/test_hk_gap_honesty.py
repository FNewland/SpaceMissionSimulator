"""A hole in the delayed-TM record must be reported as a hole.

When a housekeeping SID is disabled — by TC(3,6), or by a boot resetting the
on-board enable set — it simply stops producing packets. The S15 store then
contains data up to that moment and nothing after it, and a report generated
over a period spanning the outage has to say so.

`completeness()` used to compute ``expected`` from the span of the packets it
actually received (``times[-1] - times[0]``), so a SID that stopped half way
through the period reported ~0% missing and zero gaps: the truncated data was
presented as if it covered the whole period. Only INTERIOR holes were ever
detected, which is exactly the shape a disable does NOT have.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.reporting.analysis import completeness  # noqa: E402
from smo_common.reporting.dataset import TelemetryDataset  # noqa: E402
from smo_common.reporting.periods import Period  # noqa: E402

CADENCE = 4.0  # SID 2 in configs/eosat1/telemetry/hk_structures.yaml
SID = 2


class _Catalogs:
    """Only the field `completeness()` reads."""

    def __init__(self):
        self.hk_structs = {SID: {"sid": SID, "name": "AOCS",
                                 "interval_s": CADENCE}}


def _dataset(times: list[float], catalogs) -> TelemetryDataset:
    ds = TelemetryDataset(catalogs=catalogs)
    ds.hk_times[SID] = sorted(times)
    return ds


def _period(start: float, end: float) -> Period:
    return Period(start_cuc=start, end_cuc=end, kind="test", label="test")


@pytest.fixture()
def catalogs():
    return _Catalogs()


def test_sid_that_stops_half_way_is_reported_as_half_missing(catalogs):
    """The classic shape of an enable-set reset: data, then nothing."""
    times = [i * CADENCE for i in range(75)]  # 0 s … 296 s of a 600 s period
    rows = completeness(_dataset(times, catalogs), _period(0.0, 600.0), catalogs, [SID])
    row = rows[0]

    assert row["received"] == 75
    assert row["expected"] == 150, (
        "expected must be counted over the requested period, not over the "
        "span of the packets that happened to arrive")
    assert row["missing"] == 75
    assert row["missing_pct"] == pytest.approx(50.0, abs=0.1)
    assert row["gaps"], (
        "a SID that stopped and never came back has no INTERIOR hole — the "
        "gap is at the end, and it was invisible")
    assert row["longest_gap_s"] > 250


def test_sid_that_starts_late_is_reported_as_a_leading_gap(catalogs):
    """The other shape: the operator re-enabled it part way through."""
    times = [300.0 + i * CADENCE for i in range(75)]
    row = completeness(_dataset(times, catalogs), _period(0.0, 600.0), catalogs, [SID])[0]

    assert row["missing"] == 75
    assert any(g["start_cuc"] == 0.0 for g in row["gaps"]), (
        f"no leading gap reported: {row['gaps']}")


def test_uninterrupted_sid_has_no_gap_and_no_missing(catalogs):
    times = [i * CADENCE for i in range(150)]
    row = completeness(_dataset(times, catalogs), _period(0.0, 600.0), catalogs, [SID])[0]

    assert row["gaps"] == [], f"false gap on a complete stream: {row['gaps']}"
    assert row["missing"] == 0
    assert row["missing_pct"] == 0.0


def test_interior_gap_is_still_found(catalogs):
    """The behaviour that already worked must keep working."""
    times = ([i * CADENCE for i in range(50)]
             + [400.0 + i * CADENCE for i in range(50)])
    row = completeness(_dataset(times, catalogs), _period(0.0, 600.0), catalogs, [SID])[0]

    interior = [g for g in row["gaps"]
                if g["start_cuc"] > 0.0 and g["end_cuc"] < 600.0]
    assert len(interior) == 1
    assert interior[0]["duration_s"] == pytest.approx(204.0, abs=1.0)


def test_a_sid_that_never_arrived_is_100_percent_missing(catalogs):
    row = completeness(_dataset([], catalogs), _period(0.0, 600.0), catalogs, [SID])[0]

    assert row["received"] == 0
    assert row["missing_pct"] == pytest.approx(100.0)
    assert len(row["gaps"]) == 1, "the whole period is one gap"
