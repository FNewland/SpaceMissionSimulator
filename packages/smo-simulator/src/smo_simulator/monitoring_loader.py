"""S12/S19 Monitoring Configuration Loader.

Loads S12 on-board monitoring definitions and S19 event-action rules
from YAML config files and registers them with the ServiceDispatcher.
"""
import logging
from pathlib import Path
from typing import Any, Optional
import yaml

logger = logging.getLogger(__name__)


def load_s12_definitions(config_path: Path) -> dict[int, dict]:
    """Load S12 monitoring definitions from YAML.

    Expected file: configs/eosat1/monitoring/s12_definitions.yaml

    Returns:
        Dictionary mapping param_id -> monitoring definition dict.
        Format: {param_id: {'param_id': X, 'check_type': 0, 'low_limit': Y, 'high_limit': Z, ...}}
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
            if isinstance(param_id, str):
                param_id = int(param_id, 0)

            # Create unique key: param_id + rule name hash (to allow multiple rules per param)
            rule_name = rule.get('name', f"rule_{idx}")
            rule_key = f"{param_id}_{rule_name}"

            definitions[rule_key] = {
                'param_id': param_id,
                'check_type': rule.get('check_type', 0),
                'low_limit': float(rule.get('low_limit', 0.0)),
                'high_limit': float(rule.get('high_limit', 0.0)),
                'severity': rule.get('severity', 'WARNING'),
                'enabled': True,
                'last_value': None,
                'name': rule_name,
                'description': rule.get('description', ''),
            }

        logger.info("Loaded %d S12 monitoring definitions from %s", len(definitions), s12_file)
        return definitions

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

            event_type = rule.get('event_type')
            action_func_id = rule.get('action_func_id')
            enabled = rule.get('enabled', True)

            if event_type is None:
                logger.warning("S19 rule %d missing event_type", idx)
                continue
            if action_func_id is None:
                logger.warning("S19 rule %d missing action_func_id", idx)
                continue

            # Convert event_type to int if it's a hex string
            if isinstance(event_type, str):
                event_type = int(event_type, 0)

            rules[ea_id] = {
                'event_type': event_type,
                'action_func_id': action_func_id,
                'description': rule.get('description', ''),
                'enabled': enabled,
            }

        logger.info("Loaded %d S19 event-action rules from %s", len(rules), s19_file)
        return rules

    except Exception as e:
        logger.error("Failed to load S19 rules from %s: %s", s19_file, e)
        return rules


def register_s12_definitions(dispatcher, definitions: dict[int, dict]) -> None:
    """Register S12 definitions with the ServiceDispatcher.

    Args:
        dispatcher: ServiceDispatcher instance
        definitions: Dictionary of monitoring definitions (as returned by load_s12_definitions)
    """
    for rule_key, defn in definitions.items():
        param_id = defn['param_id']
        dispatcher._s12_definitions[param_id] = {
            'param_id': param_id,
            'check_type': defn['check_type'],
            'low_limit': defn['low_limit'],
            'high_limit': defn['high_limit'],
            'enabled': True,
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
            'action_func_id': rule['action_func_id'],
            'description': rule.get('description', ''),
        }
        if rule.get('enabled', True):
            dispatcher._s19_enabled_ids.add(ea_id)

    logger.info("Registered %d S19 rules with dispatcher", len(rules))
