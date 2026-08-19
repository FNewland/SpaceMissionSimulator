"""Authority hygiene contract (POSITION_NEEDS G10).

Every S8 func_id in commands/tc_catalog.yaml must be owned by at least one
NON-flight-director position in mcs/positions.yaml, OR sit on the explicit
FD-only allowlist below with a stated reason. This is the test that keeps
new commands from shipping as orphans nobody but the FD can send (ten did:
83, 85-91, 96, 97/98, 119 — fixed 2026-08-18).
"""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"

#: S8 functions deliberately owned by NO subsystem position. Each entry
#: needs a reason; an empty reason fails the test.
FD_ONLY_ALLOWLIST = {
    120: ("SYS_SET_MISSION_PHASE — the commissioning phase gate is the "
          "Flight Director's own instrument (POSITION_NEEDS §3.1: 'the one "
          "command that is genuinely FD-owned'); granting it to a subsystem "
          "desk would let a single position advance the mission phase."),
}


def _load_positions():
    data = yaml.safe_load((CONFIG / "mcs" / "positions.yaml").read_text())
    return data["positions"]


def _catalogued_s8_funcs():
    data = yaml.safe_load(
        (CONFIG / "commands" / "tc_catalog.yaml").read_text())
    cmds = data.get("commands", data)
    out = {}
    for c in cmds:
        if c.get("service") == 8 and "func_id" in c:
            out[int(c["func_id"])] = str(c.get("name", ""))
    return out


def test_every_s8_func_is_owned_or_explicitly_fd_only():
    positions = _load_positions()
    owned: set[int] = set()
    for pos_id, pos in positions.items():
        if pos_id == "flight_director":
            continue  # allowed_commands: all — FD ownership proves nothing
        for fid in pos.get("allowed_func_ids") or []:
            owned.add(int(fid))

    funcs = _catalogued_s8_funcs()
    orphans = []
    for fid, name in sorted(funcs.items()):
        if fid in owned:
            continue
        if fid in FD_ONLY_ALLOWLIST:
            assert FD_ONLY_ALLOWLIST[fid].strip(), (
                f"func {fid} ({name}) is allowlisted FD-only WITHOUT a "
                "reason — state one")
            continue
        orphans.append(f"{fid} ({name})")
    assert not orphans, (
        "S8 functions owned by NO non-FD position and not on the FD-only "
        f"allowlist (G10 authority hygiene): {orphans}. Either add them to a "
        "position's allowed_func_ids with a justification comment, or add "
        "them to FD_ONLY_ALLOWLIST here with a reason.")


def test_allowlist_carries_no_stale_entries():
    """An allowlisted func that a position DOES own is a stale allowlist row."""
    positions = _load_positions()
    owned: set[int] = set()
    for pos_id, pos in positions.items():
        if pos_id == "flight_director":
            continue
        owned.update(int(f) for f in pos.get("allowed_func_ids") or [])
    stale = sorted(set(FD_ONLY_ALLOWLIST) & owned)
    assert not stale, (
        f"FD_ONLY_ALLOWLIST entries now owned by a position: {stale} — "
        "remove them from the allowlist")


def test_g10_dispositions_are_in_config():
    """The specific §6 orphan fixes, pinned so they cannot regress."""
    positions = _load_positions()
    eps = set(positions["eps_tcs"]["allowed_func_ids"])
    aocs = set(positions["aocs"]["allowed_func_ids"])
    pl = set(positions["payload_ops"]["allowed_func_ids"])
    fdir = set(positions["fdir_systems"]["allowed_func_ids"])
    assert 83 in eps, "EPS_SET_MODE belongs to eps_tcs"
    assert {85, 86, 87, 88, 89, 90, 91, 96} <= aocs, \
        "AOCS pointing/estimator funcs belong to aocs"
    assert 119 in pl, "PAYLOAD_SET_SHUTTER belongs to payload_ops"
    assert {97, 98} <= fdir, \
        "UHF bootloader beacon TX is the OBC recovery path (fdir_systems)"
    # S9 time management: the desk that owns on-board time truth (FS3)
    # can send it; TT&C keeps it too.
    assert 9 in positions["fdir_systems"]["allowed_services"]
    assert 9 in positions["ttc"]["allowed_services"]
