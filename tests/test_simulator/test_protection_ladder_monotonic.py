"""Phase B (brief 01, test 5): the protection ladder must be monotonic.

This is the test that would have caught finding 9: the FDIR bus-voltage rule
at 26.0 V sat at 58.4 % SoC — INSIDE the nominal eclipse band — so the
harshest protection (EPS safe mode) fired first, during correct operations.

Every voltage threshold in fdir.yaml, simulation.yaml and
fdir/fault_propagation.yaml is converted to SoC through the configured OCV
curve, merged with the SoC-based stages, and the resulting ladder must be
strictly monotonically decreasing in SoC with severity increasing as SoC
falls:

    shed 45.5 % > EPS-safe 39 % > stage-1 30 % > AOCS-safe 22 %
    > stage-2 20 % > cascade 19.5 % > stage-3 10 %
"""
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


def _load(name):
    return yaml.safe_load((CONFIG_DIR / name).read_text())


def _ocv_to_soc(v, bat):
    return (v - bat["soc_0_v"]) / (bat["soc_100_v"] - bat["soc_0_v"]) * 100.0


def _ladder():
    """Return [(soc_pct, severity_rank, label)] sorted as configured."""
    eps = _load("subsystems/eps.yaml")
    bat = eps["battery"]
    fdir = _load("subsystems/fdir.yaml")
    sim = _load("simulation.yaml")
    prop = _load("fdir/fault_propagation.yaml")

    rungs = []

    # in-model bus-voltage shed (mildest: sheds one line per tick, restores)
    shed_v = sim["eps"]["load_shed"]["bus_voltage_v"]
    rungs.append((_ocv_to_soc(shed_v, bat), 1, f"in-model shed {shed_v} V"))

    # fdir.yaml bus-voltage rules
    sev_by_action = {"safe_mode_eps": 2, "safe_mode_aocs": 4}
    for rule in fdir["rules"]:
        if rule["parameter"] == "eps.bus_voltage" and "<" in rule["condition"]:
            soc = _ocv_to_soc(float(rule["threshold"]), bat)
            sev = sev_by_action.get(rule["action"], 3)
            rungs.append((soc, sev, f"fdir {rule['action']} {rule['threshold']} V"))

    # FDIR SoC shed stages
    stages = sim["eps"]["load_shed"]["soc_stage_thresholds_pct"]
    assert stages == sorted(stages, reverse=True), "stages must descend"
    for i, soc in enumerate(stages):
        rungs.append((soc, 3 + 2 * i, f"SoC shed stage {i + 1}"))

    # fault-propagation undervoltage cascade (payload off + AOCS safe +
    # heater standby — near the bottom of the ladder)
    for cascade in prop["fault_cascades"]:
        if cascade.get("primary_param") == "eps.bus_voltage" \
                and "threshold" in cascade:
            soc = _ocv_to_soc(float(cascade["threshold"]), bat)
            rungs.append((soc, 6, f"cascade {cascade['fault_id']}"))

    return rungs


def test_thresholds_strictly_monotonic_in_soc():
    rungs = sorted(_ladder(), key=lambda r: -r[0])
    socs = [r[0] for r in rungs]
    for a, b in zip(socs, socs[1:]):
        assert a > b, (
            "protection ladder has coincident or inverted rungs:\n  "
            + "\n  ".join(f"{soc:5.1f}% {label}" for soc, _, label in rungs))


def test_severity_increases_as_soc_falls():
    """The mildest action must have the highest threshold; severity rank must
    be non-decreasing while SoC decreases."""
    rungs = sorted(_ladder(), key=lambda r: -r[0])
    sevs = [r[1] for r in rungs]
    assert sevs[0] == min(sevs), (
        f"mildest action is not first: {[(round(s, 1), v, l) for s, v, l in rungs]}")
    for a, b in zip(sevs, sevs[1:]):
        assert b >= a, (
            "severity not monotone down the ladder:\n  "
            + "\n  ".join(f"{soc:5.1f}% sev{sev} {label}"
                          for soc, sev, label in rungs))


def test_no_rung_inside_nominal_band():
    """Nothing in the ladder may trip above 50 % SoC — the nominal eclipse
    band. Finding 9's 26.0 V rule sat at 58.4 %."""
    for soc, _, label in _ladder():
        assert soc < 50.0, f"'{label}' trips at {soc:.1f}% SoC — nominal band"


def test_expected_ladder_shape():
    """Pin the intended rungs (brief 01 WP-6) so silent edits are visible."""
    eps = _load("subsystems/eps.yaml")
    bat = eps["battery"]
    fdir = _load("subsystems/fdir.yaml")
    bus_rules = {r["action"]: float(r["threshold"]) for r in fdir["rules"]
                 if r["parameter"] == "eps.bus_voltage"}
    assert bus_rules["safe_mode_eps"] == 24.5
    assert bus_rules["safe_mode_aocs"] == 23.2
    assert abs(_ocv_to_soc(24.5, bat) - 39.0) < 1.0
    assert abs(_ocv_to_soc(23.2, bat) - 22.1) < 1.0
