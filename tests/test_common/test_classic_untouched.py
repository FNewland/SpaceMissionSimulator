"""GATE — the three classic consoles are byte-untouched.

The 2026-08 UI reorganisation's hardest constraint (execution plan, §5:
"classic index.html files owned by NO ONE"): the modern pages live in new
`static/modern/` directories and the classic single-file pages must remain
BYTE-IDENTICAL through the whole effort — they are the fallback (`/classic`,
`?ui=classic`) if the modern console regresses, and the reference for the
functional-inventory acceptance checklist.

This test pins the sha256 of each classic page as recorded when Agent 1
(platform) landed. If it fails, someone edited a classic page: revert the
edit — whatever it was trying to do belongs in `static/modern/` or in the
server, not here. If a classic page must ever legitimately change (outside
this reorganisation), update the hash in the same commit with a comment
saying why.
"""
import hashlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# sha256 recorded 2026-08-17 (Agent 1 baseline).
CLASSIC_PAGES = {
    # RE-PINNED 2026-08d (TZ-1), the one legitimate change this gate
    # contemplates. The classic MCS page's S11 scheduler resolved its
    # "Execution UTC (ISO 8601)" box with a bare `new Date(txt)`, which
    # ECMAScript defines as browser-LOCAL for a zone-less string — so a typed
    # time tag was shifted by the host's UTC offset (correct on a UTC machine,
    # 5 h out in EST, 5 h 30 out in IST) and the S11 tags did not line up with
    # the planner's window. This page is the FALLBACK console, so it cannot be
    # left holding a wrong-time-tag defect while only the modern page is fixed
    # (modern/mcs.js already appended the "Z"). Proof:
    # tests/test_mcs/test_s11_time_tag_timezone.py.
    "packages/smo-mcs/src/smo_mcs/static/index.html":
        "049d00b55de192552e48ea739bcd36f919d4423add347c3e7a91ed7252f6be01",
    "packages/smo-planner/src/smo_planner/static/index.html":
        "775196c579e52c4dffa04d082d02d13ad554e1d441b51e2ab3928f8c5fcf3b77",
    # rfsim radio page frozen 2026-08-18 (channel-model split: operator page
    # moved to static/modern/index.html, channel model to modern/sim.html).
    "packages/smo-rfsim/src/smo_rfsim/static/radio.html":
        "193f49f50d30461535e0045189c1638de964632f744accac8ad6bd78e93853f7",
    "packages/smo-simulator/src/smo_simulator/instructor/static/index.html":
        "3470b3976dd41e056fb289b5f0a644e319489a52fd69eaefff97923c39ff37f2",
}


@pytest.mark.parametrize("rel_path,expected", sorted(CLASSIC_PAGES.items()),
                         ids=["mcs", "planner", "rfsim", "instructor"])
def test_classic_page_is_byte_identical(rel_path, expected):
    path = REPO / rel_path
    assert path.exists(), (
        f"classic page {rel_path} is MISSING — it must never be moved or "
        f"renamed; the modern page lives in static/modern/ beside it")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == expected, (
        f"classic page {rel_path} CHANGED (sha256 {actual[:12]}… != "
        f"recorded {expected[:12]}…). The classic consoles are frozen during "
        f"the 2026-08 UI reorganisation: revert this edit — modern-console "
        f"work belongs in static/modern/, server work in server.py.")
