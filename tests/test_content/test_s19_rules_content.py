"""Content tests for the shipped S19 event-action rules.

``configs/eosat1/monitoring/s19_rules.yaml`` maps on-board events to S8
function commands that the spacecraft executes *autonomously*, with no operator
in the loop. Nothing in the runtime validates those mappings: an ``action_func_id``
that no longer matches the S8 function map still loads, still fires, and quietly
commands the wrong subsystem. That is exactly how defect #37 happened (a rule
mapped to func 55 — OBDH ``obc_boot_app`` — silently booted the OBC out of the
bootloader at startup).

These tests are the missing net. For every rule they assert:

* the rule loads with an integer ``event_type`` / ``action_func_id``;
* ``event_type`` resolves to a real event-catalog id or an S12-monitored
  parameter, so the rule can actually fire;
* ``action_func_id`` routes to a real subsystem handler — no silent no-ops;
* no rule uses an OBC-critical func (52/53/55/56), and the autonomous
  dispatcher still refuses those funcs while the OBC is in the bootloader.

**Domain ownership:** the *semantic* correctness of a rule (does "TTC PA
Overheat" really want this S8 func?) is an FDIR-domain decision owned by Flight
Ops, not by this test. Rules whose description names one action but whose func
id routes somewhere else are recorded in ``KNOWN_MISROUTED`` below with the
routing they currently have. That pins today's behaviour (so nobody changes it
by accident) without this test silently "fixing" the YAML.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from smo_simulator.monitoring_loader import (
    TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM,
    load_s19_rules, register_s19_rules,
)
from smo_simulator.service_dispatch import ServiceDispatcher

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
S19_FILE = CONFIG_DIR / "monitoring" / "s19_rules.yaml"

SUBSYSTEMS = ("aocs", "eps", "payload", "tcs", "obdh", "ttc", "prop")

# S8 funcs that boot/reboot/reconfigure the OBC. ServiceDispatcher refuses these
# on the autonomous S19 path while the OBC is in the bootloader; no shipped rule
# should be reaching for them in the first place.
OBC_CRITICAL_FUNCS = {52, 53, 55, 56}

# ── Rule → intended target subsystem, read off each rule's own description ──
# "TCS FPA Overtemp -> Payload Off" is triggered by TCS but *acts on* the
# payload's power, so the value below is the subsystem the action should drive.
RULE_INTENT = {
    1001: "eps",      # -> Payload Off (Load Shed)
    1002: "eps",      # -> FPA Cooler Off
    1003: "eps",      # -> Payload Power Off
    1004: "eps",      # -> Load Shedding
    1005: "eps",      # -> Load Shedding
    2001: "aocs",     # -> Trigger Desaturation
    2002: "aocs",     # -> Switch to ST2
    2003: "aocs",     # -> ST Fallback
    2004: "aocs",     # -> Safe Mode
    2005: "aocs",     # -> Disable Wheel 1
    2006: "aocs",     # -> Disable Wheel 2
    2007: "aocs",     # -> Disable Wheel 3
    2008: "aocs",     # -> Disable Wheel 4
    3001: "eps",      # -> Payload Off (via power line)
    3002: "ttc",      # -> Transponder TX Off
    3003: "obdh",     # -> OBC Safe Mode
    4001: "ttc",      # -> Increase TX Power
    4002: "ttc",      # -> PA Off
    4003: "ttc",      # -> Increase TX Power
    5001: "obdh",     # -> OBC reboot acknowledge
    5002: "obdh",     # -> Switch CAN Bus
    5003: "obdh",     # -> Memory Scrub
    5004: "obdh",     # -> Safe Mode
    6001: "eps",      # -> Stop Imaging (via power line)
    6002: "payload",  # -> Payload Standby
    6003: "payload",  # -> Verify Data
    # Phase D propulsion autonomy (funcs 108-118 route to the prop model)
    8001: "prop",     # -> abort burn on attitude error
    8002: "prop",     # -> abort burn on tank pressure
    8003: "prop",     # -> abort burn on catbed overtemp
    8004: "prop",     # -> isolate on leak
    8005: "prop",     # -> disarm on safe mode
    8006: "prop",     # -> abort burn on SoC critical
}

# Rules whose action_func_id does NOT drive the subsystem their description
# names. Value = (subsystem, command) the func actually reaches today.
#
# These are reported to Flight Ops as an FDIR review item; they are pinned here
# rather than edited because remapping an autonomous response is a domain
# decision. Once a rule is remapped, delete its entry — the paired
# test_rule_action_matches_declared_intent case will then enforce the fix.
KNOWN_MISROUTED = {
    1001: ("aocs", "slew_to_quaternion"),   # func 10 is AOCS_SLEW_TO, not payload off
    1002: ("aocs", "check_momentum"),       # func 11 is AOCS_CHECK_MOMENTUM, not FPA cooler
    1003: ("aocs", "rw_ramp_down"),         # func 14 is AOCS_RW_RAMP_DOWN, not power line off
    1004: ("aocs", "slew_to_quaternion"),   # func 10 again
    1005: ("aocs", "slew_to_quaternion"),   # func 10 again
    3002: ("aocs", "begin_acquisition"),    # func 12 is AOCS_BEGIN_ACQUISITION, not transponder off
    3003: ("tcs", "heater"),                # func 40 is a TCS heater circuit, not OBDH safe mode
    4002: ("obdh", "obc_select_bus"),       # func 54 is OBDH select-bus, not TTC PA off
    5001: ("tcs", "heater"),                # func 42 is a TCS heater circuit, not OBC reboot
    5002: ("tcs", "set_setpoint"),          # func 44 is TCS heater setpoint, not CAN bus switch
    5003: ("tcs", "heater"),                # func 41 is a TCS heater circuit, not memory scrub
    5004: ("tcs", "heater"),                # func 40 again
    6002: ("eps", "reset_oc_flag"),         # func 21 is EPS reset-OC-flag, not payload standby
}

# Rules whose *trigger id* does not mean what the rule's description says.
# Surfaced by the defect-#70 namespace split: with `trigger.kind` now explicit,
# each id resolves to exactly one catalogued thing, and for these four that
# thing contradicts the rule name. Re-pointing a trigger changes when the
# spacecraft acts autonomously, so — like KNOWN_MISROUTED — it is a Flight Ops
# decision and is pinned here rather than edited in the YAML.
# Value = what the declared trigger id actually is.
KNOWN_TRIGGER_DRIFT = {
    1001: "0x0102 is BATTERY_SOC_CRITICAL; description says bus undervoltage "
          "(that would be 0x0103/0x0104)",
    1002: "0x0100 is EPS_MODE_CHANGE; description says SoC critical",
    1003: "0x0100 is EPS_MODE_CHANGE; description says SoC critical",
    2004: "0x0202 is RW_BEARING_DEGRADED; description says attitude error high",
}


# ── Helpers ─────────────────────────────────────────────────────────

def _mock_dispatcher():
    """A ServiceDispatcher over mock subsystems, for routing probes."""
    engine = MagicMock()
    engine.params = {}
    engine.tm_builder = MagicMock()
    engine.tm_builder._pack_tm = MagicMock(return_value=b"\x00" * 20)
    subs = {}
    for name in SUBSYSTEMS:
        m = MagicMock()
        m.handle_command = MagicMock(return_value={"success": True})
        m._state = MagicMock()
        m._state.power_lines = {}
        subs[name] = m
    engine.subsystems = subs
    engine._s19_definitions = {}
    return ServiceDispatcher(engine), subs


def _probe_func(func_id: int) -> list[tuple[str, str]]:
    """Dispatch S8.1 func_id and report every (subsystem, command) it drives.

    Padded with zero bytes so funcs that unpack arguments still reach their
    handler; a func that routes nowhere returns an empty list.
    """
    dispatcher, subs = _mock_dispatcher()
    dispatcher._handle_s8(1, bytes([func_id]) + bytes(32))
    out: list[tuple[str, str]] = []
    for name, model in subs.items():
        for call in model.handle_command.call_args_list:
            cmd = call.args[0] if call.args else call.kwargs.get("cmd", {})
            out.append((name, cmd.get("command", "")))
    return out


def _raw_rules() -> list[dict]:
    return yaml.safe_load(S19_FILE.read_text())["s19_rules"]


def _event_catalog_ids() -> set[int]:
    data = yaml.safe_load((CONFIG_DIR / "events" / "event_catalog.yaml").read_text())
    return {e["id"] for e in data["events"]}


def _s12_monitored_param_ids() -> set[int]:
    data = yaml.safe_load(
        (CONFIG_DIR / "monitoring" / "s12_definitions.yaml").read_text())
    ids = set()
    for d in data["s12_definitions"]:
        pid = d.get("param_id")
        ids.add(int(pid, 0) if isinstance(pid, str) else pid)
    return ids


RULES = load_s19_rules(CONFIG_DIR)
RULE_IDS = sorted(RULES)


# ── Loader-level invariants ─────────────────────────────────────────

def test_all_rules_load():
    """Every rule in the YAML survives the loader (none silently dropped)."""
    raw = _raw_rules()
    assert len(RULES) == len(raw), (
        f"{len(raw)} rules in s19_rules.yaml but {len(RULES)} loaded — "
        f"a rule is missing ea_id/event_type/action_func_id")
    assert RULES, "no S19 rules loaded at all"


def test_ea_ids_are_unique():
    raw = _raw_rules()
    ids = [r["ea_id"] for r in raw]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate ea_id(s) in s19_rules.yaml: {sorted(dupes)}"


def test_rule_intent_table_covers_every_shipped_rule():
    """Keeps this test file honest when new rules are added to the YAML."""
    missing = set(RULES) - set(RULE_INTENT)
    stale = set(RULE_INTENT) - set(RULES)
    assert not missing, (
        f"rules {sorted(missing)} are shipped but not classified in "
        f"RULE_INTENT — add them (and review where they route)")
    assert not stale, f"RULE_INTENT lists rules that no longer exist: {sorted(stale)}"


# ── Per-rule checks ─────────────────────────────────────────────────

@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_fields_are_integers(ea_id):
    rule = RULES[ea_id]
    assert isinstance(rule["event_type"], int), (
        f"rule {ea_id}: event_type did not parse to an int")
    assert isinstance(rule["action_func_id"], int), (
        f"rule {ea_id}: action_func_id did not parse to an int")
    assert 0 <= rule["action_func_id"] <= 255, (
        f"rule {ea_id}: action_func_id {rule['action_func_id']} out of S8 range")


@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_event_type_is_resolvable(ea_id):
    """A rule can only ever fire if its event_type is a real event or a
    parameter the S12 monitor actually watches."""
    event_type = RULES[ea_id]["event_type"]
    known_events = _event_catalog_ids()
    monitored = _s12_monitored_param_ids()
    assert event_type in known_events or event_type in monitored, (
        f"rule {ea_id}: event_type 0x{event_type:04X} is neither an event in "
        f"event_catalog.yaml nor a parameter monitored by s12_definitions.yaml "
        f"— this rule can never fire")


@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_action_routes_to_a_real_handler(ea_id):
    """No rule may dispatch an S8 func that falls through the routing table."""
    func_id = RULES[ea_id]["action_func_id"]
    routed = _probe_func(func_id)
    assert routed, (
        f"rule {ea_id}: S8 func {func_id} routes to no subsystem handler — "
        f"the autonomous response is a silent no-op")


@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_does_not_use_obc_critical_func(ea_id):
    """Defect #37 regression: no rule may auto-boot/reboot/reconfigure the OBC."""
    func_id = RULES[ea_id]["action_func_id"]
    assert func_id not in OBC_CRITICAL_FUNCS, (
        f"rule {ea_id} maps to OBC-critical S8 func {func_id} "
        f"({sorted(OBC_CRITICAL_FUNCS)} are reboot/switch/boot-app/boot-inhibit) "
        f"— an autonomous rule must never command these")


@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_action_matches_declared_intent(ea_id):
    """The func a rule fires must drive the subsystem its description names.

    Rules listed in KNOWN_MISROUTED are pinned to the routing they have today
    (see the module docstring — remapping them is a Flight Ops decision).
    """
    func_id = RULES[ea_id]["action_func_id"]
    routed = _probe_func(func_id)
    subsystems = {s for s, _ in routed}

    if ea_id in KNOWN_MISROUTED:
        expected = KNOWN_MISROUTED[ea_id]
        assert expected in routed, (
            f"rule {ea_id} (func {func_id}) no longer routes to {expected}; "
            f"it now drives {routed}. If this was an intentional FDIR fix, "
            f"remove ea_id {ea_id} from KNOWN_MISROUTED.")
        return

    intended = RULE_INTENT[ea_id]
    assert intended in subsystems, (
        f"rule {ea_id}: description targets {intended!r} but S8 func {func_id} "
        f"drives {sorted(subsystems)} ({routed})")


@pytest.mark.xfail(
    strict=True,
    reason="13 shipped S19 rules dispatch an S8 func that does not match their "
           "description (defect #37 class, see KNOWN_MISROUTED). Reported to "
           "Flight Ops for FDIR review; this xfail flips to a pass — and must "
           "then be deleted — once the rules are remapped.",
)
def test_no_rule_is_misrouted():
    """Summary gate on the outstanding FDIR review item."""
    offenders = []
    for ea_id in RULE_IDS:
        func_id = RULES[ea_id]["action_func_id"]
        subsystems = {s for s, _ in _probe_func(func_id)}
        if RULE_INTENT[ea_id] not in subsystems:
            offenders.append((ea_id, func_id, RULE_INTENT[ea_id], sorted(subsystems)))
    assert not offenders, (
        "S19 rules whose action does not match their declared intent:\n  "
        + "\n  ".join(str(o) for o in offenders))


# ── Autonomous-dispatch behaviour ───────────────────────────────────

def test_bootloader_gate_blocks_obc_critical_funcs():
    """The S19 path must refuse OBC-critical funcs while the OBC is in bootloader.

    Defense in depth behind the content check above: even a mis-mapped future
    rule must not be able to boot the OBC out of the bootloader.
    """
    for func_id in sorted(OBC_CRITICAL_FUNCS):
        dispatcher, subs = _mock_dispatcher()
        subs["obdh"]._state.sw_image = 0  # SW_BOOTLOADER
        dispatcher._s19_dispatch(ea_id=9999, func_id=func_id)
        assert not subs["obdh"].handle_command.called, (
            f"S19 dispatch of OBC-critical func {func_id} was NOT blocked "
            f"while the OBC was in the bootloader")


def test_bootloader_gate_allows_obc_critical_funcs_in_application():
    """The gate must be conditional — not a blanket refusal."""
    dispatcher, subs = _mock_dispatcher()
    subs["obdh"]._state.sw_image = 1  # SW_APPLICATION
    dispatcher._s19_dispatch(ea_id=9999, func_id=52)
    assert subs["obdh"].handle_command.called, (
        "S19 dispatch of func 52 was blocked even though the OBC was running "
        "the application image")


def test_non_critical_funcs_are_never_gated():
    """A rule's real action must still fire while the OBC is in the bootloader."""
    dispatcher, subs = _mock_dispatcher()
    subs["obdh"]._state.sw_image = 0
    dispatcher._s19_dispatch(ea_id=2001, func_id=1)  # AOCS desaturate
    assert subs["aocs"].handle_command.called, (
        "non-critical S19 action was blocked by the bootloader gate")


def test_shipped_rules_register_into_the_dispatcher():
    """register_s19_rules() installs every enabled rule as an S19 definition."""
    dispatcher, _ = _mock_dispatcher()
    dispatcher._s19_definitions = {}
    dispatcher._s19_enabled_ids = set()
    register_s19_rules(dispatcher, RULES)
    assert set(dispatcher._s19_definitions) == set(RULES)
    enabled = {ea for ea, r in RULES.items() if r.get("enabled", True)}
    assert dispatcher._s19_enabled_ids == enabled


def test_registered_rule_fires_its_action_on_matching_event():
    """End-to-end: a shipped rule's trigger reaches its S8 func."""
    dispatcher, subs = _mock_dispatcher()
    dispatcher._s19_definitions = {}
    dispatcher._s19_enabled_ids = set()
    subs["obdh"]._state.sw_image = 1
    register_s19_rules(dispatcher, RULES)

    # 2001: momentum saturation (an S12 *parameter* trigger) -> AOCS desaturate
    # (a correctly-mapped rule).
    dispatcher.trigger_event_action(RULES[2001]["event_type"],
                                    kind=RULES[2001]["trigger_kind"])
    commands = [c.args[0].get("command")
                for c in subs["aocs"].handle_command.call_args_list]
    assert "desaturate" in commands, (
        f"rule 2001 did not reach the AOCS desaturate handler; got {commands}")


# ── Trigger namespace (defect #70) ──────────────────────────────────

@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_declares_a_trigger_namespace(ea_id):
    """Every shipped rule must say which namespace its trigger id lives in."""
    raw = {r["ea_id"]: r for r in _raw_rules()}[ea_id]
    assert isinstance(raw.get("trigger"), dict), (
        f"rule {ea_id} still uses the ambiguous bare `event_type:` field — "
        f"migrate it to `trigger: {{kind: event|param, id: ...}}`")
    assert RULES[ea_id]["trigger_kind"] in (TRIGGER_KIND_EVENT,
                                            TRIGGER_KIND_PARAM)


@pytest.mark.parametrize("ea_id", RULE_IDS)
def test_rule_trigger_resolves_in_its_declared_namespace(ea_id):
    """The trigger id must exist in the namespace the rule declares.

    Stricter than the old "event OR param" check, which could not see that a
    rule resolved only in the namespace it did not mean.
    """
    rule = RULES[ea_id]
    if rule["trigger_kind"] == TRIGGER_KIND_EVENT:
        assert rule["event_type"] in _event_catalog_ids(), (
            f"rule {ea_id}: trigger kind 'event' but 0x{rule['event_type']:04X} "
            f"is not in event_catalog.yaml — this rule can never fire")
    else:
        assert rule["event_type"] in _s12_monitored_param_ids(), (
            f"rule {ea_id}: trigger kind 'param' but 0x{rule['event_type']:04X} "
            f"is not monitored by s12_definitions.yaml — this rule can never fire")


def test_no_rule_is_dual_triggered():
    """A rule must respond to exactly one condition.

    Event ids and monitored parameter ids share 0x0000-0x0FFF. With the old
    bare `event_type` the dispatcher matched both namespaces, so 17 of the 26
    shipped rules fired on two unrelated conditions — most visibly rule 1001,
    which fired on a battery-*temperature* limit violation as well as on the
    SoC-critical event, while claiming to be about bus undervoltage.
    """
    dispatcher, subs = _mock_dispatcher()
    dispatcher._s19_definitions = {}
    dispatcher._s19_enabled_ids = set()
    subs["obdh"]._state.sw_image = 1
    register_s19_rules(dispatcher, RULES)

    fired: list[int] = []
    dispatcher._s19_dispatch = lambda ea_id, func_id, action_data=b"": fired.append(ea_id)

    offenders = []
    for ea_id, rule in RULES.items():
        other = (TRIGGER_KIND_PARAM if rule["trigger_kind"] == TRIGGER_KIND_EVENT
                 else TRIGGER_KIND_EVENT)
        fired.clear()
        dispatcher.trigger_event_action(rule["event_type"], kind=other)
        if ea_id in fired:
            offenders.append((ea_id, hex(rule["event_type"]), sorted(set(fired))))
    assert not offenders, (
        "rules that also fire in the namespace they did NOT declare:\n  "
        + "\n  ".join(str(o) for o in offenders))


def test_s12_param_violation_does_not_fire_an_event_keyed_rule():
    """The exact defect-#70 reproduction, pinned.

    Rule 1001 declares an *event* trigger on 0x0102 (BATTERY_SOC_CRITICAL).
    0x0102 is also a monitored parameter (battery temperature), and an S12
    violation of it used to fire this rule.
    """
    dispatcher, subs = _mock_dispatcher()
    dispatcher._s19_definitions = {}
    dispatcher._s19_enabled_ids = set()
    subs["obdh"]._state.sw_image = 1
    register_s19_rules(dispatcher, RULES)
    assert RULES[1001]["trigger_kind"] == TRIGGER_KIND_EVENT

    fired: list[int] = []
    dispatcher._s19_dispatch = lambda ea_id, func_id, action_data=b"": fired.append(ea_id)

    dispatcher.trigger_event_action(0x0102, kind=TRIGGER_KIND_PARAM)
    assert 1001 not in fired, (
        "an S12 battery-temperature violation still fires event-keyed rule 1001")

    dispatcher.trigger_event_action(0x0102, kind=TRIGGER_KIND_EVENT)
    assert 1001 in fired, "rule 1001 no longer fires on its declared event trigger"


@pytest.mark.xfail(
    strict=True,
    reason="4 shipped S19 rules point at a trigger id whose catalogued meaning "
           "contradicts the rule's own description (see KNOWN_TRIGGER_DRIFT). "
           "Disambiguating the namespaces (defect #70) made the drift visible "
           "but deliberately did not re-target any rule — that is an "
           "FDIR-domain decision for Flight Ops. This xfail flips to a pass — "
           "and must then be deleted — once the ids are reviewed.",
)
def test_no_rule_trigger_contradicts_its_description():
    """Summary gate on the outstanding trigger-drift review item."""
    offenders = []
    catalog = {e["id"]: e["name"]
               for e in yaml.safe_load(
                   (CONFIG_DIR / "events" / "event_catalog.yaml").read_text())["events"]}
    for ea_id in RULE_IDS:
        if ea_id in KNOWN_TRIGGER_DRIFT:
            offenders.append((ea_id, hex(RULES[ea_id]["event_type"]),
                              catalog.get(RULES[ea_id]["event_type"], "?"),
                              KNOWN_TRIGGER_DRIFT[ea_id]))
    assert not offenders, (
        "S19 rules whose trigger id does not mean what their description says:\n  "
        + "\n  ".join(str(o) for o in offenders))
