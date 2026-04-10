"""Comprehensive validation tests for EOSAT-1 YAML configuration files.

Tests verify that all config files load correctly, contain no duplicate IDs,
and that cross-references between files are consistent and valid.
"""
import pytest
import yaml
from pathlib import Path
from collections import Counter


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "configs" / "eosat1"

VALID_SUBSYSTEMS = {"eps", "aocs", "obdh", "tcs", "ttc", "payload", "orbital", "fdir"}

VALID_EVENT_SEVERITIES = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}

VALID_PACK_FORMATS = {"B", "b", "H", "h", "I", "i", "f", "d"}


def _load_yaml(relative_path):
    """Load a YAML file relative to CONFIG_ROOT and return its contents."""
    full_path = CONFIG_ROOT / relative_path
    assert full_path.exists(), f"Config file not found: {full_path}"
    with open(full_path, "r") as fh:
        data = yaml.safe_load(fh)
    assert data is not None, f"Config file is empty or invalid YAML: {full_path}"
    return data


# ---------------------------------------------------------------------------
# 1. All config files load without errors
# ---------------------------------------------------------------------------

_ALL_CONFIG_FILES = [
    "mission.yaml",
    "orbit.yaml",
    "subsystems/eps.yaml",
    "subsystems/aocs.yaml",
    "subsystems/tcs.yaml",
    "subsystems/obdh.yaml",
    "subsystems/ttc.yaml",
    "subsystems/payload.yaml",
    "subsystems/fdir.yaml",
    "subsystems/memory_map.yaml",
    "telemetry/parameters.yaml",
    "telemetry/hk_structures.yaml",
    "mcs/limits.yaml",
    "mcs/displays.yaml",
    "mcs/positions.yaml",
    "mcs/pus_services.yaml",
    "events/event_catalog.yaml",
    "commands/tc_catalog.yaml",
    "procedures/procedure_index.yaml",
    "planning/activity_types.yaml",
]


@pytest.mark.parametrize("config_file", _ALL_CONFIG_FILES)
def test_config_files_load_successfully(config_file):
    """Every listed config file must parse as valid YAML without errors."""
    data = _load_yaml(config_file)
    assert isinstance(data, dict), (
        f"{config_file} top-level structure must be a mapping, got {type(data).__name__}"
    )


# ---------------------------------------------------------------------------
# 2. Telemetry parameter IDs are unique
# ---------------------------------------------------------------------------

def test_telemetry_parameter_ids_are_unique():
    """No two telemetry parameters may share the same numeric ID."""
    data = _load_yaml("telemetry/parameters.yaml")
    params = data["parameters"]

    ids = [p["id"] for p in params]
    counts = Counter(ids)
    duplicates = {hex(pid): cnt for pid, cnt in counts.items() if cnt > 1}
    assert not duplicates, f"Duplicate parameter IDs found: {duplicates}"


# ---------------------------------------------------------------------------
# 3. Telemetry parameter names are unique
# ---------------------------------------------------------------------------

def test_telemetry_parameter_names_are_unique():
    """No two telemetry parameters may share the same name."""
    data = _load_yaml("telemetry/parameters.yaml")
    params = data["parameters"]

    names = [p["name"] for p in params]
    counts = Counter(names)
    duplicates = {name: cnt for name, cnt in counts.items() if cnt > 1}
    assert not duplicates, f"Duplicate parameter names found: {duplicates}"


# ---------------------------------------------------------------------------
# 4. HK structure param_id references resolve to defined parameters
# ---------------------------------------------------------------------------

def test_hk_structure_param_ids_reference_valid_parameters():
    """Every param_id in hk_structures.yaml must exist in parameters.yaml."""
    params_data = _load_yaml("telemetry/parameters.yaml")
    valid_param_ids = {p["id"] for p in params_data["parameters"]}

    hk_data = _load_yaml("telemetry/hk_structures.yaml")
    missing = []
    for structure in hk_data["structures"]:
        sid = structure["sid"]
        name = structure["name"]
        for entry in structure["parameters"]:
            pid = entry["param_id"]
            if pid not in valid_param_ids:
                missing.append(f"SID {sid} ({name}): param_id {hex(pid)}")

    assert not missing, (
        f"HK structures reference undefined parameter IDs:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 5. HK structure IDs are unique
# ---------------------------------------------------------------------------

def test_hk_structure_ids_are_unique():
    """No two HK structures may share the same SID."""
    hk_data = _load_yaml("telemetry/hk_structures.yaml")
    sids = [s["sid"] for s in hk_data["structures"]]
    counts = Counter(sids)
    duplicates = {sid: cnt for sid, cnt in counts.items() if cnt > 1}
    assert not duplicates, f"Duplicate HK structure SIDs: {duplicates}"


# ---------------------------------------------------------------------------
# 6. Limits reference valid parameter IDs
# ---------------------------------------------------------------------------

def test_limits_reference_valid_parameter_ids():
    """Every param_id in limits.yaml must exist in parameters.yaml."""
    params_data = _load_yaml("telemetry/parameters.yaml")
    valid_param_ids = {p["id"] for p in params_data["parameters"]}

    limits_data = _load_yaml("mcs/limits.yaml")
    missing = []
    for entry in limits_data["limits"]:
        pid = entry["param_id"]
        if pid not in valid_param_ids:
            missing.append(hex(pid))

    assert not missing, (
        f"Limits reference undefined parameter IDs: {missing}"
    )


# ---------------------------------------------------------------------------
# 7. Limits have consistent threshold ordering
# ---------------------------------------------------------------------------

def test_limits_threshold_ordering():
    """For each limit, red_low <= yellow_low <= yellow_high <= red_high."""
    limits_data = _load_yaml("mcs/limits.yaml")
    violations = []
    for entry in limits_data["limits"]:
        pid = hex(entry["param_id"])
        rl = entry["red_low"]
        yl = entry["yellow_low"]
        yh = entry["yellow_high"]
        rh = entry["red_high"]
        if not (rl <= yl <= yh <= rh):
            violations.append(
                f"{pid}: red_low={rl}, yellow_low={yl}, "
                f"yellow_high={yh}, red_high={rh}"
            )

    assert not violations, (
        f"Limit threshold ordering violations:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# 8. Event catalog IDs are unique
# ---------------------------------------------------------------------------

def test_event_ids_are_unique():
    """No two events may share the same numeric ID."""
    data = _load_yaml("events/event_catalog.yaml")
    events = data["events"]

    ids = [e["id"] for e in events]
    counts = Counter(ids)
    duplicates = {hex(eid): cnt for eid, cnt in counts.items() if cnt > 1}
    assert not duplicates, f"Duplicate event IDs found: {duplicates}"


# ---------------------------------------------------------------------------
# 9. Event catalog uses valid severity levels and subsystems
# ---------------------------------------------------------------------------

def test_event_catalog_fields_are_valid():
    """All events must use recognized severity levels and subsystem names."""
    data = _load_yaml("events/event_catalog.yaml")
    events = data["events"]

    bad_severity = []
    bad_subsystem = []
    for evt in events:
        if evt["severity"] not in VALID_EVENT_SEVERITIES:
            bad_severity.append(f"{evt['name']}: {evt['severity']}")
        if evt["subsystem"] not in VALID_SUBSYSTEMS:
            bad_subsystem.append(f"{evt['name']}: {evt['subsystem']}")

    assert not bad_severity, f"Invalid event severities: {bad_severity}"
    assert not bad_subsystem, f"Invalid event subsystems: {bad_subsystem}"


# ---------------------------------------------------------------------------
# 10. FDIR rules reference valid parameter names
# ---------------------------------------------------------------------------

def test_fdir_rules_reference_valid_parameter_names():
    """Every parameter name in FDIR rules must exist in parameters.yaml."""
    params_data = _load_yaml("telemetry/parameters.yaml")
    valid_names = {p["name"] for p in params_data["parameters"]}

    fdir_data = _load_yaml("subsystems/fdir.yaml")
    rules = fdir_data["rules"]
    missing = []
    for rule in rules:
        param_name = rule["parameter"]
        if param_name not in valid_names:
            missing.append(f"{param_name} (action: {rule['action']})")

    assert not missing, (
        f"FDIR rules reference undefined parameter names:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 11. Procedure index has unique IDs and references valid positions
# ---------------------------------------------------------------------------

def test_procedure_ids_are_unique():
    """No two procedures may share the same ID."""
    data = _load_yaml("procedures/procedure_index.yaml")
    procedures = data["procedures"]

    ids = [p["id"] for p in procedures]
    counts = Counter(ids)
    duplicates = {pid: cnt for pid, cnt in counts.items() if cnt > 1}
    assert not duplicates, f"Duplicate procedure IDs found: {duplicates}"


def test_procedure_positions_reference_valid_positions():
    """Every position in required_positions must exist in positions.yaml."""
    positions_data = _load_yaml("mcs/positions.yaml")
    valid_positions = set(positions_data["positions"].keys())

    proc_data = _load_yaml("procedures/procedure_index.yaml")
    missing = []
    for proc in proc_data["procedures"]:
        for pos in proc.get("required_positions", []):
            if pos not in valid_positions:
                missing.append(f"{proc['id']}: position '{pos}'")

    assert not missing, (
        f"Procedures reference undefined operator positions:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 12. MCS positions reference valid subsystems
# ---------------------------------------------------------------------------

def test_mcs_positions_reference_valid_subsystems():
    """Subsystems listed under each MCS position must be recognized names."""
    data = _load_yaml("mcs/positions.yaml")
    invalid = []
    for pos_key, pos_cfg in data["positions"].items():
        for sub in pos_cfg.get("subsystems", []):
            if sub not in VALID_SUBSYSTEMS:
                invalid.append(f"{pos_key}: subsystem '{sub}'")

    assert not invalid, (
        f"MCS positions reference unrecognized subsystems:\n"
        + "\n".join(f"  - {i}" for i in invalid)
    )


# ---------------------------------------------------------------------------
# 13. Display definitions reference valid parameter names
# ---------------------------------------------------------------------------

def test_display_parameter_references_are_valid():
    """Every parameter name used in display widgets must exist in parameters.yaml."""
    params_data = _load_yaml("telemetry/parameters.yaml")
    valid_names = {p["name"] for p in params_data["parameters"]}

    displays_data = _load_yaml("mcs/displays.yaml")
    missing = []

    for pos_key, pos_cfg in displays_data["positions"].items():
        for page in pos_cfg.get("pages", []):
            for widget in page.get("widgets", []):
                # Single-parameter widgets
                if "parameter" in widget:
                    pname = widget["parameter"]
                    if pname not in valid_names:
                        missing.append(
                            f"{pos_key}/{page['name']}: '{pname}'"
                        )
                # Multi-parameter widgets (value_table, line_chart)
                if "parameters" in widget:
                    for pname in widget["parameters"]:
                        if pname not in valid_names:
                            missing.append(
                                f"{pos_key}/{page['name']}: '{pname}'"
                            )

    assert not missing, (
        f"Display widgets reference undefined parameters:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 14. Activity types reference valid procedures
# ---------------------------------------------------------------------------

def test_activity_types_reference_valid_procedures():
    """Every procedure_ref in activity_types.yaml must map to a procedure ID."""
    proc_data = _load_yaml("procedures/procedure_index.yaml")
    valid_proc_ids = {p["id"] for p in proc_data["procedures"]}

    act_data = _load_yaml("planning/activity_types.yaml")
    missing = []
    for act in act_data["activity_types"]:
        ref = act.get("procedure_ref")
        if ref and ref not in valid_proc_ids:
            missing.append(f"{act['name']}: procedure_ref '{ref}'")

    assert not missing, (
        f"Activity types reference undefined procedure IDs:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# 15. Mission config has required top-level fields
# ---------------------------------------------------------------------------

def test_mission_config_has_required_fields():
    """mission.yaml must contain the essential mission-level fields."""
    data = _load_yaml("mission.yaml")
    required_keys = ["name", "spacecraft_apid", "pus_version", "time_epoch", "network"]
    missing = [k for k in required_keys if k not in data]
    assert not missing, f"mission.yaml missing required keys: {missing}"

    # Validate network sub-keys
    net = data["network"]
    net_required = ["tc_port", "tm_port", "http_port"]
    net_missing = [k for k in net_required if k not in net]
    assert not net_missing, f"mission.yaml network section missing keys: {net_missing}"


# ---------------------------------------------------------------------------
# 16. Orbit config has TLE and ground station data
# ---------------------------------------------------------------------------

def test_orbit_config_structure():
    """orbit.yaml must have TLE lines, altitude, and ground stations."""
    data = _load_yaml("orbit.yaml")
    assert "tle_line1" in data, "orbit.yaml missing tle_line1"
    assert "tle_line2" in data, "orbit.yaml missing tle_line2"
    assert "altitude_km" in data, "orbit.yaml missing altitude_km"

    stations = data.get("ground_stations", [])
    assert len(stations) > 0, "orbit.yaml has no ground stations"
    for gs in stations:
        assert "name" in gs, f"Ground station missing 'name': {gs}"
        assert "lat_deg" in gs, f"Ground station '{gs.get('name')}' missing lat_deg"
        assert "lon_deg" in gs, f"Ground station '{gs.get('name')}' missing lon_deg"


# ---------------------------------------------------------------------------
# 17. HK structure pack_format values are valid struct format characters
# ---------------------------------------------------------------------------

def test_hk_structure_pack_formats_are_valid():
    """Every pack_format in hk_structures must be a recognized struct char."""
    hk_data = _load_yaml("telemetry/hk_structures.yaml")
    invalid = []
    for structure in hk_data["structures"]:
        sid = structure["sid"]
        name = structure["name"]
        for entry in structure["parameters"]:
            fmt = entry["pack_format"]
            if fmt not in VALID_PACK_FORMATS:
                invalid.append(f"SID {sid} ({name}): param_id {hex(entry['param_id'])} format '{fmt}'")

    assert not invalid, (
        f"HK structures contain invalid pack_format values:\n"
        + "\n".join(f"  - {i}" for i in invalid)
    )


# ---------------------------------------------------------------------------
# 18. Command catalog has no duplicate (service, subtype, func_id) tuples
# ---------------------------------------------------------------------------

def test_command_catalog_no_duplicate_identifiers():
    """Each command must have a unique (service, subtype, func_id) or unique name.

    S2 (Device Access) commands share (service=2, subtype=1) but are
    differentiated by device_id in their data field, so we use the
    command name as the uniqueness key for S2 commands.
    """
    data = _load_yaml("commands/tc_catalog.yaml")
    commands = data["commands"]

    keys = []
    for cmd in commands:
        if cmd["service"] == 2:
            # S2 commands are unique by name (device_id is in the data)
            key = (cmd["service"], cmd["subtype"], cmd["name"])
        else:
            key = (cmd["service"], cmd["subtype"], cmd.get("func_id"))
        keys.append((key, cmd["name"]))

    seen = {}
    duplicates = []
    for key, name in keys:
        if key in seen:
            duplicates.append(f"{name} duplicates {seen[key]} at {key}")
        else:
            seen[key] = name

    assert not duplicates, (
        f"Duplicate command identifiers:\n"
        + "\n".join(f"  - {d}" for d in duplicates)
    )
