"""Every parameter the engine WRITES must be declared in `parameters.yaml`.

`tests/test_content/test_parameter_chain.py` walks the catalogue outwards: it
proves every declared parameter is reachable and every HK member is produced.
This is the missing direction — a value the models publish into
``engine.params`` that the catalogue never names.  Such a parameter is
*undownlinkable*: no HK structure can carry it, S20.3 refuses the id, the MCS
has no name, unit or limit for it, and nothing fails, so the gap is invisible.

That is exactly how `0x0424` / `0x0425` (the FPA cooler floor and margin, the
honest reason FPA READY does not come up) sat written-but-unreachable.

`KNOWN_UNDECLARED` is a ratchet, not an amnesty: every entry needs a reason,
and the list must only ever shrink.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"


def _pid(v) -> int:
    return int(v) if isinstance(v, int) else int(str(v), 0)


#: Ids written into `engine.params` that are deliberately NOT spacecraft
#: telemetry, or that are known gaps awaiting their owner.  Each needs a
#: reason.  Do not add to this list to make a new parameter pass — declare it.
KNOWN_UNDECLARED: dict[int, str] = {
    0x05FE: "simulator time-acceleration factor — a ground/instructor value "
            "written by the engine, never downlinked",
    0x05FF: "instructor 'override passes' toggle — same: console state, not "
            "spacecraft telemetry",
    # 0x0213/0x0214 dropped 2026-08c item 2: GPS velocity now rides the
    # DECLARED ECEF PVT block 0x0234-0x0236 (aocs.gps_vel_x/y/z, HK SID 12).
    0x031F: "obdh heat dissipation (W), consumed by the TCS coupling only "
            "(OBDH owner)",
    0x0504: "ttc uplink frequency (MHz) readback (TT&C owner)",
    0x0505: "ttc downlink frequency (MHz) readback (TT&C owner)",
    0x050B: "ttc contact azimuth (deg) — the elevation twin IS declared "
            "(TT&C owner)",
    0x0538: "ttc ground-station degradation budget (dB) (TT&C owner)",
    0x0607: "payload line rate (payload owner)",
    0x0608: "payload data rate (Mbps) (payload owner)",
}


@pytest.fixture(scope="module")
def declared_ids() -> set[int]:
    data = yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    return {_pid(p["id"]) for p in data["parameters"]}


@pytest.fixture(scope="module")
def ticked_engine():
    from smo_simulator.engine import SimulationEngine
    eng = SimulationEngine(str(CONFIG_DIR))
    eng._spacecraft_phase = 6
    for _ in range(20):
        eng._tick_once(1.0)
    return eng


def test_every_written_parameter_is_declared(ticked_engine, declared_ids):
    undeclared = sorted(set(ticked_engine.params)
                        - declared_ids - set(KNOWN_UNDECLARED))
    assert not undeclared, (
        "the engine writes parameters that telemetry/parameters.yaml does not "
        "declare, so they can never be downlinked or limit-checked: "
        f"{[f'0x{p:04X}' for p in undeclared]}")


def test_the_cooler_authority_parameters_are_declared(ticked_engine,
                                                      declared_ids):
    """The two that motivated this gate, named explicitly."""
    for pid, name, units in ((0x0424, "tcs.fpa_cooler_floor_c", "C"),
                             (0x0425, "tcs.fpa_cooler_margin_k", "K")):
        assert pid in declared_ids, f"0x{pid:04X} is still undeclared"
        assert pid in ticked_engine.params, f"0x{pid:04X} is no longer written"
    data = yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
    by_id = {_pid(p["id"]): p for p in data["parameters"]}
    assert by_id[0x0424]["units"] == "C"
    assert by_id[0x0425]["units"] == "K"
    for pid in (0x0424, 0x0425):
        assert by_id[pid]["subsystem"] == "tcs"
        # Reachable: neither is in an HK SID, so both must be on-demand or
        # test_parameter_chain's reachability gate would fail.
        assert by_id[pid].get("on_demand") is True


def test_the_known_undeclared_list_does_not_go_stale(ticked_engine):
    """An entry the engine no longer writes is a stale excuse."""
    stale = [f"0x{pid:04X}" for pid in KNOWN_UNDECLARED
             if pid not in ticked_engine.params]
    assert not stale, f"drop these from KNOWN_UNDECLARED: {stale}"


def test_the_known_undeclared_list_does_not_grow():
    assert len(KNOWN_UNDECLARED) <= 11, (
        "this list is a ratchet — declare the parameter instead of "
        "allowlisting it")
