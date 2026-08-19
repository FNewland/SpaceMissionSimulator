"""Every configuration key must have a consumer (brief 08 WP-8, gap 3).

Walk the YAML under `configs/eosat1/`, collect the leaf key names, and assert
each is referenced in `packages/`, `tools/` or `scripts/` source, or is listed
in `KNOWN_UNUSED` with a reason.

This is the direction that catches "we shipped a config block nobody reads" —
`mission.yaml: ground_segment`, `platform_idle_power_w`, `frequency_hz`, the
`MCS_BIND` / `PLANNER_BIND` deploy variables.  The *other* direction (a
consumer reading a key no config defines) is caught by
`model_config = ConfigDict(extra="forbid")` on the `smo_common` schemas, which
`test_schemas_reject_unknown_keys` below pins.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
SOURCE_ROOTS = [REPO / "packages", REPO / "tools", REPO / "scripts",
                REPO / "deploy"]


#: Keys that are deliberately declarative-only (data, not configuration a
#: consumer names): they are read positionally, iterated over, or are the
#: content of a catalogue rather than a switch. Regexes are matched against
#: the dotted path `<file>:<a.b.c>`.
DECLARATIVE_FILES = (
    "telemetry/parameters.yaml",
    "telemetry/derived.yaml",
    "telemetry/hk_structures.yaml",
    "commands/tc_catalog.yaml",
    "events/event_catalog.yaml",
    "monitoring/s12_definitions.yaml",
    "monitoring/s19_rules.yaml",
    "mcs/limits.yaml",
    "procedures/procedure_index.yaml",
    "mcs/displays.yaml",
    "mcs/positions.yaml",
)

#: Dotted-path prefixes whose CHILD keys are consumed by iteration rather than
#: by name (`for k, v in block.items()`), so a name-grep cannot see them.
#: The parent key itself still has to have a consumer.
DYNAMICALLY_KEYED = (
    "param_ids.",                                   # subsystem id maps
    "recovery_escalation.escalation_delays.",       # level_1/2/3
    "recovery_escalation.recovery_actions.",        # group -> level -> action
    "load_shedding_stages.",                        # stage_0..stage_3
    "parameter_sets.",                              # report template sets
    "vars.",                                        # report template variables
    "positions.",                                   # operator position table
)

#: Leaf keys with no consumer, kept deliberately. Each needs a reason.
KNOWN_UNUSED: dict[str, str] = {
    "mission.yaml:payload.target":
        "mission narrative shown in the instructor briefing, not a model input",
    "mission.yaml:payload.bands":
        "documentation of the band set; the model's band table is in "
        "subsystems/payload.yaml",
    "mcs/pus_services.yaml:services.s18.max_procedures":
        "the whole file has no production consumer yet; it is the candidate "
        "single PUS routing table (brief 08 finding 10 / X-2, WP-16) and is "
        "kept for promotion rather than deleted",
    "mcs/pus_services.yaml:services.s18.max_steps_per_procedure":
        "same as max_procedures: pus_services.yaml awaits promotion to THE "
        "routing table (X-2 / WP-16)",
    "subsystems/tcs.yaml:thermal_coupling.zone_conductance.battery_obc":
        "declared zone conductances; the single-node thermal model does not "
        "solve a coupled network yet (brief 08 finding 19, thermal workstream)",
    "subsystems/tcs.yaml:thermal_coupling.zone_conductance.payload_structure":
        "as battery_obc — declared coupling the current model does not use",
    "subsystems/tcs.yaml:thermal_coupling.zone_conductance.structure_electronics":
        "as battery_obc — declared coupling the current model does not use",
    "subsystems/eps.yaml:solar_panels.sada_max_rate_deg_s":
        "SADA slew-rate limit; the drive is commanded to an angle and settles "
        "within a tick, so the rate limit is not yet integrated (Phase B SADA)",
    "planning/activity_types.yaml:activity_types.requires_subsystems":
        "declared for the scheduler's subsystem-availability check, which is "
        "not implemented (planner constraint work, O-14/#16)",
    "planning/activity_types.yaml:activity_types.min_duration_s":
        "duration bounds for a future variable-length scheduler; the planner "
        "schedules the nominal duration_s today",
    "planning/activity_types.yaml:activity_types.max_duration_s":
        "as min_duration_s",
    "planning/activity_types.yaml:activity_types.aocs_constraints.resets_momentum":
        "the planner reads the aocs_constraints block but not this field yet "
        "(O-14: the constraint endpoints have no UI and a partial engine)",
    "planning/activity_types.yaml:activity_types.aocs_constraints.target_momentum":
        "as resets_momentum",
    "planning/activity_types.yaml:activity_types.aocs_constraints.required_gap_s_after":
        "as resets_momentum",
    "planning/activity_types.yaml:activity_types.thermal_constraints.fpa_temp_max_c":
        "the planner reads the thermal_constraints block but not this field",
    "planning/activity_types.yaml:activity_types.thermal_constraints.fpa_temp_min_c":
        "as fpa_temp_max_c",
    "planning/activity_types.yaml:activity_types.thermal_constraints.max_continuous_minutes":
        "as fpa_temp_max_c",
    "planning/activity_types.yaml:activity_types.thermal_constraints.max_per_orbit_minutes":
        "as fpa_temp_max_c",
    "planning/activity_types.yaml:activity_types.thermal_constraints.required_cooldown_s":
        "as fpa_temp_max_c",
}

_IGNORE_KEYS = {
    # Generic structural words that appear everywhere and prove nothing.
    "name", "description", "id", "type", "value", "enabled", "units",
    "comment", "notes", "title", "label", "category", "severity", "steps",
    "params", "data", "mode", "state", "level", "action", "target", "source",
    "priority", "step_id", "command", "service", "subtype", "func_id",
}


def _yaml_files() -> list[Path]:
    return sorted(p for p in CONFIG_DIR.rglob("*.yaml")
                  if "__pycache__" not in str(p))


def _leaf_keys(node, prefix: str = "") -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                continue
            path = f"{prefix}.{k}" if prefix else k
            out.add(path)
            out |= _leaf_keys(v, path)
    elif isinstance(node, list):
        for item in node:
            out |= _leaf_keys(item, prefix)
    return out


def _source_corpus() -> str:
    chunks = []
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        # *.js included: the modern UI pages (static/modern/*.js) are
        # first-class config consumers (e.g. mcs/display_config.yaml keys
        # capture_gates/health_baselines are read by mcs.js). Agent 5 integration.
        for pattern in ("**/*.py", "**/*.html", "**/*.js", "**/*.sh",
                        "**/*.conf"):
            for f in root.glob(pattern):
                if "__pycache__" in str(f):
                    continue
                try:
                    chunks.append(f.read_text(errors="ignore"))
                except OSError:
                    continue
    return "\n".join(chunks)


CORPUS = _source_corpus()


def _rel(path: Path) -> str:
    return str(path.relative_to(CONFIG_DIR))


CONFIG_FILES = [p for p in _yaml_files()
                if _rel(p) not in DECLARATIVE_FILES
                and not _rel(p).startswith(("scenarios/", "procedures/",
                                            "fdir/procedures/", "obcp/"))]


@pytest.mark.parametrize("path", CONFIG_FILES, ids=_rel)
def test_every_config_key_has_a_consumer(path):
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        pytest.fail(f"{_rel(path)} does not parse: {e}")
    if data is None:
        return
    orphans = []
    for dotted in sorted(_leaf_keys(data)):
        leaf = dotted.rsplit(".", 1)[-1]
        if leaf in _IGNORE_KEYS or leaf.startswith("_"):
            continue
        key = f"{_rel(path)}:{dotted}"
        if key in KNOWN_UNUSED:
            continue
        if any(pref in dotted + "." for pref in DYNAMICALLY_KEYED):
            continue
        if leaf in CORPUS:
            continue
        orphans.append(dotted)
    assert not orphans, (
        f"{_rel(path)}: keys named by no consumer and not in KNOWN_UNUSED: "
        f"{orphans}")


def test_known_unused_entries_still_exist():
    stale = []
    for key in KNOWN_UNUSED:
        rel, dotted = key.split(":", 1)
        path = CONFIG_DIR / rel
        if not path.exists():
            stale.append(key)
            continue
        data = yaml.safe_load(path.read_text()) or {}
        if dotted not in _leaf_keys(data):
            stale.append(key)
    assert not stale, f"stale KNOWN_UNUSED entries: {stale}"


def test_mission_yaml_no_longer_declares_a_second_station_list():
    """`ground_segment` duplicated orbit.yaml's stations with nothing enforcing
    agreement (brief 08 finding 19 / X-11)."""
    data = yaml.safe_load((CONFIG_DIR / "mission.yaml").read_text())
    assert "ground_segment" not in data, (
        "mission.yaml carries a second, unenforced ground-station list")


def test_deploy_bind_variables_are_consumed():
    """MCS_BIND / PLANNER_BIND were declared in smo-env.conf and read by no
    start script, while all three servers hard-coded 0.0.0.0 (D-2)."""
    conf = (REPO / "deploy" / "smo-env.conf").read_text()
    scripts = "\n".join(
        (REPO / "deploy" / n).read_text()
        for n in ("start-mcs.sh", "start-planner.sh", "start-simulator.sh"))
    for var in ("MCS_BIND", "PLANNER_BIND", "SIMULATOR_BIND"):
        assert var in conf, f"{var} missing from smo-env.conf"
        assert var in scripts, f"{var} declared but passed to no server"


# ── The other direction: schemas reject unknown keys ────────────────────

def _schema_models():
    """Every pydantic model declared in smo_common.config.schemas."""
    import inspect
    from pydantic import BaseModel
    from smo_common.config import schemas as S
    return [(n, m) for n, m in vars(S).items()
            if inspect.isclass(m) and issubclass(m, BaseModel)
            and m.__module__ == S.__name__ and n != "StrictModel"]


def test_schemas_reject_unknown_keys():
    """`extra="forbid"` makes a typo'd config key fail at load.

    With pydantic's default `extra="ignore"` a mistyped key was silently
    dropped — which is how `ground_segment` sat in mission.yaml for months
    doing nothing, and how `therhold:` for `threshold:` would produce a
    working-but-wrong spacecraft with no diagnostic anywhere. EVERY schema is
    strict, not a hand-maintained subset: one model added without it reopens
    the hole (G2-g).
    """
    from smo_common.config import schemas as S
    import pydantic

    models = _schema_models()
    assert len(models) > 50, "schema discovery found suspiciously few models"
    lax = [f"{n} (extra={(m.model_config or {}).get('extra')!r})"
           for n, m in models
           if (m.model_config or {}).get("extra") != "forbid"]
    assert not lax, f"schemas that still silently swallow unknown keys: {lax}"

    with pytest.raises(pydantic.ValidationError):
        S.NetworkConfig(tc_port=8001, definitely_not_a_field=1)


def test_every_schema_inherits_the_strict_base():
    """Strictness is inherited, so it cannot be forgotten on a new model."""
    from smo_common.config.schemas import StrictModel
    loose = [n for n, m in _schema_models() if not issubclass(m, StrictModel)]
    assert not loose, f"schemas not derived from StrictModel: {loose}"


def test_every_shipped_config_validates_against_its_schema():
    """The other half of `extra="forbid"`: the shipped configs must load.

    A strict schema the mission's own YAML fails is not strictness, it is a
    broken build — and the failure mode it replaces (silent key dropping) is
    exactly what this asserts is gone.
    """
    from smo_common.confedit.formgen import schema_model_for, _ListWrapper

    failures = []
    for path in _yaml_files():
        rel = _rel(path)
        model = schema_model_for(rel)
        if model is None:
            continue
        data = yaml.safe_load(path.read_text()) or {}
        try:
            if isinstance(model, _ListWrapper):
                for entry in data.get(model.key) or []:
                    model.model.model_validate(entry)
            else:
                model.model_validate(data)
        except Exception as e:  # noqa: BLE001 — pydantic ValidationError
            errs = getattr(e, "errors", None)
            locs = ({".".join(str(x) for x in er["loc"]) for er in errs()}
                    if callable(errs) else {str(e)})
            failures.append(f"{rel}: {sorted(locs)[:6]}")
    assert not failures, "configs rejected by their own schema:\n  " + \
        "\n  ".join(failures)
