"""S12/S19 Monitoring Configuration Loader.

Loads S12 on-board monitoring definitions and S19 event-action rules
from YAML config files and registers them with the ServiceDispatcher.

Two wire-format invariants live here (defects #69 and #70):

* **S12 definitions are keyed by *definition id* (``mon_id``), not by
  ``param_id``.** A parameter may carry several monitors — typically a
  WARNING tier and an ALARM tier on the same value. Keying by ``param_id``
  silently collapsed each pair into whichever rule the loader saw last, so
  the whole WARNING tier disappeared from the spacecraft.
* **S19 triggers carry an explicit namespace discriminator.** On-board event
  ids and monitored parameter ids both live in 0x0000-0x0FFF, so a bare
  ``event_type`` cannot say which one it means and the dispatcher used to
  match *both* — one rule firing on two unrelated conditions.
"""
import logging
from pathlib import Path
from typing import Any, Optional
import yaml

logger = logging.getLogger(__name__)

# S19 trigger namespaces. Wire values; also used as the `kind` byte in
# TC(19,1) / TM(19,128).
TRIGGER_KIND_EVENT = 0   # id is an S5 event id (configs/.../event_catalog.yaml)
TRIGGER_KIND_PARAM = 1   # id is a monitored parameter id (S12 limit violation)

_TRIGGER_KIND_NAMES = {
    "event": TRIGGER_KIND_EVENT,
    "evt": TRIGGER_KIND_EVENT,
    "s5": TRIGGER_KIND_EVENT,
    "param": TRIGGER_KIND_PARAM,
    "parameter": TRIGGER_KIND_PARAM,
    "s12": TRIGGER_KIND_PARAM,
}

# S12 definition ids auto-assigned to config-declared rules that do not carry
# an explicit `mon_id:`. Kept above the ground-added range used by the MCS
# (which addresses monitors by param id in the legacy TC(12,6) form) so the
# two cannot collide.
S12_CONFIG_MON_ID_BASE = 0x8000


def _as_int(value, default=None):
    """Parse an int that may be written as a hex string in YAML."""
    if value is None:
        return default
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def parse_trigger_kind(value, default: int = TRIGGER_KIND_EVENT) -> int:
    """Map a YAML ``kind:`` value ('event' / 'param' / 0 / 1) to a wire value."""
    if value is None:
        return default
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in _TRIGGER_KIND_NAMES:
            raise ValueError(f"unknown S19 trigger kind {value!r}")
        return _TRIGGER_KIND_NAMES[key]
    kind = int(value)
    if kind not in (TRIGGER_KIND_EVENT, TRIGGER_KIND_PARAM):
        raise ValueError(f"unknown S19 trigger kind {value!r}")
    return kind


def load_s12_definitions(config_path: Path) -> dict[int, dict]:
    """Load S12 monitoring definitions from YAML.

    Expected file: configs/eosat1/monitoring/s12_definitions.yaml

    Returns:
        Dictionary mapping **mon_id** (definition id) -> monitoring definition.
        Several definitions may share the same ``param_id`` (WARNING + ALARM
        tiers on one parameter); each keeps its own key.
    """
    s12_file = config_path / "monitoring" / "s12_definitions.yaml"
    definitions = {}

    if not s12_file.exists():
        logger.warning("S12 definitions file not found: %s", s12_file)
        return definitions

    try:
        with open(s12_file, 'r') as f:
            config = yaml.safe_load(f)

        if not config or 's12_definitions' not in config:
            logger.warning("No 's12_definitions' key in %s", s12_file)
            return definitions

        # Parse each monitoring definition
        for idx, rule in enumerate(config['s12_definitions']):
            param_id = rule.get('param_id')
            if param_id is None:
                logger.warning("S12 rule %d missing param_id", idx)
                continue

            # Convert param_id to int if it's a hex string (e.g., "0x0101")
            param_id = _as_int(param_id)

            rule_name = rule.get('name', f"rule_{idx}")

            # Definition id: explicit `mon_id:` if the config declares one,
            # otherwise auto-assigned by file order. It is the *key*, so two
            # rules on the same parameter no longer overwrite each other
            # (defect #69: 4 of 38 rules — every WARNING tier — were lost).
            mon_id = _as_int(rule.get('mon_id'), S12_CONFIG_MON_ID_BASE + idx)
            if mon_id in definitions:
                logger.warning(
                    "S12 rule %d ('%s'): duplicate mon_id 0x%04X — skipped",
                    idx, rule_name, mon_id)
                continue

            low_limit = float(rule.get('low_limit', 0.0))
            high_limit = float(rule.get('high_limit', 0.0))
            # Phase B (brief 05 finding 2 / WP-6): an inverted band
            # (high < low) has an EMPTY admissible set — the monitor is
            # violated by every possible value and fires at t=0 forever.
            # That is a configuration fault, not a monitorable condition:
            # refuse to load it.
            if high_limit < low_limit:
                raise ValueError(
                    f"S12 rule {idx} ('{rule_name}', param 0x{param_id:04X}): "
                    f"inverted limits high_limit={high_limit} < "
                    f"low_limit={low_limit} — empty admissible set")

            definitions[mon_id] = {
                'mon_id': mon_id,
                'param_id': param_id,
                'check_type': int(rule.get('check_type', 0)),
                'low_limit': low_limit,
                'high_limit': high_limit,
                'severity': rule.get('severity', 'WARNING'),
                'enabled': bool(rule.get('enabled', True)),
                'last_value': None,
                'name': rule_name,
                'description': rule.get('description', ''),
            }

        logger.info("Loaded %d S12 monitoring definitions from %s", len(definitions), s12_file)
        return definitions

    except ValueError:
        # Inverted-limit configuration faults must surface, not be swallowed.
        raise
    except Exception as e:
        logger.error("Failed to load S12 definitions from %s: %s", s12_file, e)
        return definitions


def load_s19_rules(config_path: Path) -> dict[int, dict]:
    """Load S19 event-action rules from YAML.

    Expected file: configs/eosat1/monitoring/s19_rules.yaml

    Returns:
        Dictionary mapping ea_id -> event-action rule dict.
        Format: {ea_id: {'event_type': X, 'action_func_id': Y, ...}}
    """
    s19_file = config_path / "monitoring" / "s19_rules.yaml"
    rules = {}

    if not s19_file.exists():
        logger.warning("S19 rules file not found: %s", s19_file)
        return rules

    try:
        with open(s19_file, 'r') as f:
            config = yaml.safe_load(f)

        if not config or 's19_rules' not in config:
            logger.warning("No 's19_rules' key in %s", s19_file)
            return rules

        # Parse each event-action rule
        for idx, rule in enumerate(config['s19_rules']):
            ea_id = rule.get('ea_id')
            if ea_id is None:
                logger.warning("S19 rule %d missing ea_id", idx)
                continue

            # Convert ea_id to int if needed
            if isinstance(ea_id, str):
                ea_id = int(ea_id, 0)

            action_func_id = rule.get('action_func_id')
            enabled = rule.get('enabled', True)

            # Trigger: preferred disambiguated form is
            #     trigger: {kind: event|param, id: 0x0102}
            # The legacy bare `event_type:` is still accepted and defaults to
            # the *event* namespace — never the param namespace — so a legacy
            # rule can no longer double-fire (defect #70).
            trigger = rule.get('trigger')
            if isinstance(trigger, dict):
                trigger_id = trigger.get('id', trigger.get('event_type'))
                try:
                    trigger_kind = parse_trigger_kind(trigger.get('kind'))
                except ValueError as e:
                    logger.warning("S19 rule %d: %s", idx, e)
                    continue
            else:
                trigger_id = rule.get('event_type')
                trigger_kind = TRIGGER_KIND_EVENT
                if trigger_id is not None:
                    logger.info(
                        "S19 rule %s uses the legacy bare event_type field; "
                        "assuming trigger kind 'event'", ea_id)

            if trigger_id is None:
                logger.warning("S19 rule %d missing trigger id / event_type", idx)
                continue
            if action_func_id is None:
                logger.warning("S19 rule %d missing action_func_id", idx)
                continue

            trigger_id = _as_int(trigger_id)

            # Optional argument bytes for the action func (Phase C WP-9):
            # without them every AOCS autonomy rule dispatched with an empty
            # payload — wheel rules all disabled wheel 0, and rule 2004
            # commanded AOCS OFF (mode byte defaulted to 0).
            action_data = rule.get('action_data', [])
            if isinstance(action_data, (int, str)):
                action_data = [action_data]
            action_bytes = [_as_int(b) & 0xFF for b in action_data]

            rules[ea_id] = {
                # `event_type` is retained as the trigger id for wire/back
                # compatibility; `trigger_kind` says which namespace it is in.
                'event_type': trigger_id,
                'trigger_kind': trigger_kind,
                'action_func_id': int(action_func_id),
                'action_data': action_bytes,
                'description': rule.get('description', ''),
                'name': rule.get('name', ''),
                'enabled': enabled,
            }

        logger.info("Loaded %d S19 event-action rules from %s", len(rules), s19_file)
        return rules

    except Exception as e:
        logger.error("Failed to load S19 rules from %s: %s", s19_file, e)
        return rules


def register_s12_definitions(dispatcher, definitions: dict[int, dict]) -> None:
    """Register S12 definitions with the ServiceDispatcher.

    Keyed by ``mon_id`` (definition id) so a parameter can carry more than one
    monitor — e.g. the WARNING and ALARM tiers on battery SoC. Re-keying by
    ``param_id`` here is what silently discarded 4 of the 38 shipped rules
    (defect #69).

    Args:
        dispatcher: ServiceDispatcher instance
        definitions: Dictionary of monitoring definitions (as returned by load_s12_definitions)
    """
    for mon_id, defn in definitions.items():
        dispatcher._s12_definitions[mon_id] = {
            'mon_id': mon_id,
            'param_id': defn['param_id'],
            'check_type': defn['check_type'],
            'low_limit': defn['low_limit'],
            'high_limit': defn['high_limit'],
            'enabled': bool(defn.get('enabled', True)),
            'last_value': None,
            'severity': defn.get('severity', 'WARNING'),
            'name': defn.get('name', ''),
            'description': defn.get('description', ''),
        }

    logger.info("Registered %d S12 definitions with dispatcher", len(definitions))


def register_s19_rules(dispatcher, rules: dict[int, dict]) -> None:
    """Register S19 rules with the ServiceDispatcher.

    Args:
        dispatcher: ServiceDispatcher instance
        rules: Dictionary of event-action rules (as returned by load_s19_rules)
    """
    for ea_id, rule in rules.items():
        dispatcher._s19_definitions[ea_id] = {
            'event_type': rule['event_type'],
            'trigger_kind': rule.get('trigger_kind', TRIGGER_KIND_EVENT),
            'action_func_id': rule['action_func_id'],
            'action_data': list(rule.get('action_data', [])),
            'description': rule.get('description', ''),
        }
        if rule.get('enabled', True):
            dispatcher._s19_enabled_ids.add(ea_id)

    logger.info("Registered %d S19 rules with dispatcher", len(rules))
