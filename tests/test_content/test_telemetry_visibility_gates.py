"""What a real spacecraft could actually show — enforced, not just documented.

Raised 2026-08-20:

    "If I am going to be able to use all the tools except the simulator for a
    real spacecraft, where all I can do is see what rf signal comes in,
    decommute it and look at the data inside, or calculate things on the
    ground based on best prior knowledge and compare against observation,
    this all needs to be correct."

`telemetry/parameters.yaml` now classifies every parameter:

* ``real`` — genuine spacecraft telemetry: downlinked in an HK structure, or
  readable with TC(20,3).
* ``instructor`` — simulation ground truth. The magnitude of an injected
  fault, a model coefficient, a "true" value the spacecraft has no sensor
  for. The archetype is ``aocs.fidelity_mode`` (0x02C6), a numerical
  integration switch that was being downlinked at 4 s intervals.
* ``ground_derived`` — a real quantity that the GROUND measures or computes.
  A spacecraft cannot report its own downlink Eb/N0, BER, slant range or
  elevation above a station.

These gates hold the line. Without them the classification is a comment.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
PARAMS = CONFIG / "telemetry" / "parameters.yaml"
HK = CONFIG / "telemetry" / "hk_structures.yaml"
S12 = CONFIG / "monitoring" / "s12_definitions.yaml"


def _toi(v):
    return int(v) if isinstance(v, int) else int(str(v), 0)


@pytest.fixture(scope="module")
def params() -> dict[int, dict]:
    out = {}
    for p in yaml.safe_load(PARAMS.read_text())["parameters"]:
        out[_toi(p["id"])] = p
    return out


@pytest.fixture(scope="module")
def visibility(params) -> dict[int, str]:
    return {pid: str(p.get("visibility", "real")) for pid, p in params.items()}


@pytest.fixture(scope="module")
def hk_members() -> dict[int, list[int]]:
    """param_id -> [sid, ...]"""
    out: dict[int, list[int]] = {}
    for s in yaml.safe_load(HK.read_text())["structures"]:
        for p in s.get("parameters") or []:
            out.setdefault(_toi(p["param_id"]), []).append(s["sid"])
    return out


# ── the three gates ─────────────────────────────────────────────────

def test_no_instructor_parameter_is_downlinked(visibility, hk_members, params):
    """Simulation truth must never ride in a housekeeping structure.

    This is the gate that would have caught `aocs.fidelity_mode` in SID 7,
    `aocs.gyro_bias_injected` in SID 2, and `prop.leak_rate` in SID 9 — every
    one of them the *cause* of a fault, handed to the operator who is
    supposed to be diagnosing it.
    """
    offenders = [(f"0x{pid:04X}", params[pid].get("name"), sids)
                 for pid, sids in hk_members.items()
                 if visibility.get(pid) == "instructor"]
    assert not offenders, (
        "instructor-only truth is being downlinked as housekeeping: "
        f"{offenders}")


def test_no_ground_derived_parameter_is_downlinked(visibility, hk_members, params):
    """A spacecraft cannot report what the ground measures about its downlink."""
    offenders = [(f"0x{pid:04X}", params[pid].get("name"), sids)
                 for pid, sids in hk_members.items()
                 if visibility.get(pid) == "ground_derived"]
    assert not offenders, (
        "the spacecraft is reporting quantities only the ground can measure: "
        f"{offenders}. These belong to the ground segment.")


def test_no_onboard_monitor_watches_a_non_real_parameter(visibility, params):
    """On-board FDIR cannot limit-check a value measured on the ground.

    The S12 monitoring service runs ON THE SPACECRAFT. Defining a monitor on
    `ttc.eb_n0` asks the satellite to alarm on a number computed at the
    ground station using the ground station's own G/T.
    """
    doc = yaml.safe_load(S12.read_text()) or {}
    monitors = doc.get("s12_definitions") or []
    assert monitors, (
        "no S12 monitors parsed - this gate would pass vacuously. The key in "
        "s12_definitions.yaml has changed.")
    offenders = []
    for m in monitors:
        raw = m.get("param_id")
        if raw is None:
            continue
        pid = _toi(raw)
        vis = visibility.get(pid, "real")
        if vis != "real":
            offenders.append((f"0x{pid:04X}",
                              params.get(pid, {}).get("name"), vis))
    assert not offenders, (
        "on-board S12 monitors are defined on parameters the spacecraft "
        f"cannot see: {offenders}")


# ── the classification itself has to stay honest ────────────────────

def test_the_known_offenders_stay_classified(visibility, params):
    """Named in the field report and the audit. Regression anchors."""
    expected = {
        0x02C6: "instructor",   # aocs.fidelity_mode - integration switch
        0x0296: "instructor",   # aocs.gyro_bias_injected - the injected fault
        0x0B16: "instructor",   # prop.leak_rate - set only by the injection
        0x0B19: "instructor",   # prop.orbit_epoch_version - a sim object counter
        0x060D: "instructor",   # payload.last_scene_quality - from fault flags
        0x0124: "instructor",   # eps.sa_a_degradation - 1.0 - magnitude
        0x0519: "ground_derived",   # ttc.eb_n0 - measured at the ground
        0x050C: "ground_derived",   # ttc.ber - the ground demodulator's
        0x050A: "ground_derived",   # ttc.contact_elevation - ground geometry
        0x0509: "ground_derived",   # ttc.range_km - ground ranging / OD
    }
    for pid, want in expected.items():
        assert pid in params, f"0x{pid:04X} vanished from the catalogue"
        got = visibility.get(pid)
        assert got == want, (
            f"0x{pid:04X} {params[pid].get('name')} is classified {got!r}, "
            f"expected {want!r} — see docs/plan_2026-08c/"
            "TELEMETRY_REALISM_AUDIT.md")


def test_every_visibility_value_is_one_of_the_three(visibility, params):
    allowed = {"real", "instructor", "ground_derived"}
    bad = {f"0x{pid:04X}": v for pid, v in visibility.items() if v not in allowed}
    assert not bad, f"unknown visibility values: {bad}"


def test_a_real_parameter_is_reachable(visibility, params, hk_members):
    """`real` means the ground can actually get it: HK, or on-demand.

    A parameter that is neither in a structure nor marked `on_demand` is
    unreachable — it would sit in the operator's dictionary forever showing
    a dash, which is how phantom hardware hides.
    """
    unreachable = []
    for pid, p in params.items():
        if visibility.get(pid) != "real":
            continue
        if pid in hk_members or p.get("on_demand"):
            continue
        unreachable.append((f"0x{pid:04X}", p.get("name")))
    assert not unreachable, (
        f"real parameters with no route to the ground: {unreachable}")


def test_the_classification_did_not_quietly_empty_the_dictionary(visibility):
    """Guard against over-correction: most telemetry IS real."""
    real = sum(1 for v in visibility.values() if v == "real")
    assert real / len(visibility) > 0.8, (
        f"only {real}/{len(visibility)} parameters are real telemetry — the "
        "classification has been applied too aggressively")
