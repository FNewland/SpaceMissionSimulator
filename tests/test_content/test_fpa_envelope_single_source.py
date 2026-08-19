"""The FPA thermal envelope has exactly one source (DECISIONS §3, G3-d).

Five files carried five different answers for the same detector, and the sixth
was the textbook. This test recomputes every derived band from
`subsystems/tcs.yaml: fpa_envelope` and fails if any file has drifted — which
is what "single-sourced" has to mean in a tree where the consumers are YAML
and cannot import anything.

The specific failure it exists to prevent is the one Phase E created: the
setpoint became commandable over -20…0 C while the detector's S12 low limit
stayed at -16.5 C, so a *legal, catalogued* command produced a standing red
alarm. Any future widening of the command envelope now either propagates or
turns this test red.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_common.config.fpa_envelope import FPAEnvelope

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"


def _load(rel: str):
    return yaml.safe_load((CONFIG / rel).read_text())


TCS = _load("subsystems/tcs.yaml")
ENV = FPAEnvelope.from_config(TCS)

LIMITS = {int(str(l["param_id"]), 0) if not isinstance(l["param_id"], int)
          else l["param_id"]: l for l in _load("mcs/limits.yaml")["limits"]}
S12 = _load("monitoring/s12_definitions.yaml")
S12_DEFS = (S12.get("s12_definitions") or S12.get("monitors")
            or S12.get("definitions") or [])
CATALOG = _load("commands/tc_catalog.yaml")

NODE = 0x0408      # tcs.temp_fpa — the TCS-owned control node
FINGER = 0x0601    # payload.fpa_temp — cooler cold finger
DIE = 0x0617       # payload.detector_temp — detector die

TOL = 1e-6


def _s12(param_id: int, name_contains: str):
    for d in S12_DEFS:
        raw = d.get("param_id", d.get("parameter_id"))
        pid = raw if isinstance(raw, int) else int(str(raw), 0)
        if pid == param_id and name_contains.lower() in d["name"].lower():
            return d
    raise AssertionError(
        f"no S12 monitor on 0x{param_id:04X} named like {name_contains!r}")


# ── 0. The envelope is actually declared, not defaulted ────────────────

def test_the_envelope_is_declared_in_tcs_yaml():
    """TCS owns the FPA node, so the envelope lives with TCS. If this block
    goes missing the dataclass defaults would silently take over and every
    assertion below would still pass against nothing."""
    assert "fpa_envelope" in TCS, \
        "subsystems/tcs.yaml has no fpa_envelope block — the single source"
    for field in ("setpoint_min_c", "setpoint_max_c", "uncooled_max_c",
                  "detector_offset_c", "imaging_max_c", "control_3sigma_k"):
        assert field in TCS["fpa_envelope"], f"fpa_envelope lacks {field}"


def test_the_envelope_is_self_consistent():
    assert ENV.setpoint_min_c < ENV.setpoint_default_c < ENV.setpoint_max_c
    assert ENV.imaging_max_c < ENV.setpoint_max_c, (
        "imaging_max_c must be inside the command envelope, otherwise every "
        "commandable setpoint would produce an image")
    assert ENV.uncooled_max_c > ENV.setpoint_max_c, (
        "the uncooled equilibrium must be warmer than the warmest setpoint, "
        "or the cooler is doing nothing")
    assert ENV.node_warning().low < ENV.setpoint_min_c, (
        "the warning band must clear the coldest commandable setpoint — the "
        "old -20.0 low sat exactly ON it and chattered")
    w, a = ENV.node_warning(), ENV.node_alarm()
    assert a.low < w.low and a.high > w.high, "alarm must bracket warning"


# ── 1. The command envelope and the config agree ───────────────────────

def test_the_setpoint_command_range_matches_the_envelope():
    """`PAYLOAD_COOLER_SETPOINT` (S8 func 36) is what makes the envelope
    reachable; if the two drift, the limits are derived from a range the
    operator cannot command, or vice versa."""
    cmd = next(c for c in CATALOG["commands"]
               if c["name"] == "PAYLOAD_COOLER_SETPOINT")
    field = next(f for f in cmd["fields"] if f["name"] == "setpoint")
    assert field["range"]["min"] == pytest.approx(ENV.setpoint_min_c)
    assert field["range"]["max"] == pytest.approx(ENV.setpoint_max_c)
    assert field["default"] == pytest.approx(ENV.setpoint_default_c)


def test_the_tcs_cooler_target_is_the_envelope_default():
    assert TCS["fpa_cooler_target_c"] == pytest.approx(ENV.setpoint_default_c)


def test_the_payload_cooler_target_agrees():
    pl = _load("subsystems/payload.yaml")
    assert pl["fpa_cooler_target_c"] == pytest.approx(ENV.setpoint_default_c)
    assert pl["detector_offset_c"] == pytest.approx(ENV.detector_offset_c)


# ── 2. Every derived band matches ──────────────────────────────────────

@pytest.mark.parametrize("param_id,band_fn,alarm_fn,label", [
    (NODE, "node_warning", "node_alarm", "tcs.temp_fpa"),
    (FINGER, "detector_warning", "detector_alarm", "payload.fpa_temp"),
    (DIE, "die_warning", "die_alarm", "payload.detector_temp"),
])
def test_mcs_limit_bands_are_derived(param_id, band_fn, alarm_fn, label):
    lim = LIMITS.get(param_id)
    assert lim is not None, f"mcs/limits.yaml has no band for {label}"
    warn = getattr(ENV, band_fn)()
    alarm = getattr(ENV, alarm_fn)()
    assert lim["yellow_low"] == pytest.approx(warn.low, abs=TOL), label
    assert lim["yellow_high"] == pytest.approx(warn.high, abs=TOL), label
    assert lim["red_low"] == pytest.approx(alarm.low, abs=TOL), label
    assert lim["red_high"] == pytest.approx(alarm.high, abs=TOL), label


def test_s12_node_monitors_are_derived():
    warn, alarm = ENV.node_warning(), ENV.node_alarm()
    w = _s12(NODE, "warning")
    assert w["low_limit"] == pytest.approx(warn.low, abs=TOL)
    assert w["high_limit"] == pytest.approx(warn.high, abs=TOL)
    a = _s12(NODE, "alarm")
    assert a["low_limit"] == pytest.approx(alarm.low, abs=TOL)
    assert a["high_limit"] == pytest.approx(alarm.high, abs=TOL)


def test_s12_detector_monitor_is_derived():
    warn = ENV.detector_warning()
    d = _s12(FINGER, "fpa temperature")
    assert d["low_limit"] == pytest.approx(warn.low, abs=TOL)
    assert d["high_limit"] == pytest.approx(warn.high, abs=TOL)


def test_tcs_event_thresholds_are_derived():
    """These used to be [-50, -5] / [-60, 0] — an imaging-readiness band worn
    as a health band, which is the conflation that produced the whole mess."""
    evt = TCS["event_thresholds"]
    warn, alarm = ENV.node_warning(), ENV.node_alarm()
    assert evt["temperature_warning"]["fpa"] == [warn.low, warn.high]
    assert evt["temperature_alarm"]["fpa"] == [alarm.low, alarm.high]


def test_planner_imaging_constraints_are_derived():
    acts = _load("planning/activity_types.yaml")["activity_types"]
    imaging = next(a for a in acts if a["name"] == "imaging_pass")
    tc = imaging["thermal_constraints"]
    band = ENV.imaging_band()
    assert tc["fpa_temp_min_c"] == pytest.approx(band.low, abs=TOL)
    assert tc["fpa_temp_max_c"] == pytest.approx(band.high, abs=TOL)


def test_planner_preconditions_use_the_imaging_bound():
    """2026-08d PLAN-8: the parameter is `payload.fpa_temp` (0x0601).
    `payload.fpa_temp_c` is not a name in telemetry/parameters.yaml, so the
    planner's pre-condition checker reported it as "not available" and never
    evaluated the bound this test exists to single-source."""
    acts = _load("planning/activity_types.yaml")["activity_types"]
    bound = f"payload.fpa_temp < {ENV.imaging_max_c}"
    for act in acts:
        for cond in act.get("pre_conditions") or []:
            if "fpa_temp" in cond:
                assert cond == bound, (
                    f"{act['name']} pre-condition {cond!r} does not use the "
                    f"single-sourced imaging bound {bound!r}")


# ── 3. The models use the envelope, not literals ───────────────────────

SIM = REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"


@pytest.mark.parametrize("rel", ["models/tcs_basic.py", "models/payload_basic.py"])
def test_the_models_import_the_envelope(rel):
    src = (SIM / rel).read_text()
    assert "FPAEnvelope" in src, f"{rel} does not use the shared envelope"
    assert "_fpa_env" in src


def test_readiness_is_the_same_rule_in_both_subsystems():
    """TCS raises FPA_THERMAL_READY (0x040A) and the payload sets fpa_ready
    (0x0610). Two rules meant a detector that was ready on one tab and not on
    the other."""
    tcs = (SIM / "models" / "tcs_basic.py").read_text()
    pld = (SIM / "models" / "payload_basic.py").read_text()
    assert "_fpa_env.is_ready(" in tcs
    assert "_fpa_env.is_ready(" in pld


def test_over_and_under_temp_events_are_derived():
    pld = (SIM / "models" / "payload_basic.py").read_text()
    assert "_fpa_env.overtemp_c()" in pld
    assert "_fpa_env.undertemp_c()" in pld


# ── 4. The envelope produces a coherent operating picture ──────────────

def test_a_legal_setpoint_never_violates_a_limit():
    """The whole point. Sweep the commandable envelope and assert that the
    regulated node, the cold finger and the die all sit inside their yellow
    bands, with 3 sigma of headroom."""
    sigma3 = ENV.control_3sigma_k
    for i in range(0, 21):
        sp = ENV.setpoint_min_c + i * (ENV.setpoint_max_c - ENV.setpoint_min_c) / 20
        for temp, band, label in (
            (sp, ENV.node_warning(), "node"),
            (sp + ENV.detector_offset_c, ENV.detector_warning(), "finger"),
            (sp + ENV.detector_offset_c + ENV.die_offset_c, ENV.die_warning(), "die"),
        ):
            assert band.low + sigma3 <= temp <= band.high - sigma3, (
                f"setpoint {sp:.1f} C puts the {label} at {temp:.2f} C, "
                f"within 3 sigma of its yellow band {band}")


def test_the_uncooled_state_never_violates_a_limit():
    """Cooler off is a normal state — the payload is off between passes."""
    for temp, band, label in (
        (ENV.uncooled_max_c, ENV.node_warning(), "node"),
        (ENV.uncooled_max_c + ENV.detector_offset_c,
         ENV.detector_warning(), "finger"),
        (ENV.uncooled_max_c + ENV.detector_offset_c + ENV.die_offset_c,
         ENV.die_warning(), "die"),
    ):
        assert temp <= band.high, (
            f"the uncooled {label} at {temp:.2f} C breaches its yellow high "
            f"{band.high} — a spacecraft with the payload off would alarm")


def test_readiness_requires_both_regulation_and_cold():
    assert ENV.is_ready(-15.0, -15.0)
    assert not ENV.is_ready(-8.0, -15.0), "not settled"
    assert not ENV.is_ready(0.0, 0.0), (
        "a perfectly regulated 0 C detector is far too warm to image with")
    assert not ENV.is_ready(-2.0, -3.0), (
        "settled about a -3 C setpoint but at -2 C, above imaging_max_c")
    assert ENV.is_ready(-20.0, -20.0), "the coldest legal setpoint must work"
