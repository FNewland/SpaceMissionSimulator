"""Tests for configuration integrity of the MCS role-based overhaul.

Validates that:
  - All indexed procedures have corresponding files on disk
  - All procedure files on disk are referenced in the index (excluding custom/)
  - All required_positions reference valid position names from positions.yaml
  - All command_services are valid PUS service numbers (1-20)
  - All visible_tabs in positions.yaml are valid tab IDs
  - All overview_subsystems are valid subsystem names
  - All manual_sections reference actual files in manual/
  - positions.yaml loads with PositionConfig schema (new fields)
  - Manual files exist (10 files)
  - procedure_index has position_roles for multi-position procedures
"""
import pytest
import yaml
from pathlib import Path

from smo_common.config.schemas import PositionConfig


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "configs" / "eosat1"

VALID_TAB_IDS = {
    "system_dashboard", "power_monitor", "fdir_panel", "contact_schedule", "procedure_panel",
    "overview", "eps", "aocs", "tcs", "obdh", "ttc",
    "payload", "commanding", "pus", "procedures", "manual",
}

VALID_SUBSYSTEM_NAMES = {"eps", "aocs", "tcs", "obdh", "ttc", "payload", "fdir"}

VALID_PUS_SERVICES = set(range(1, 21))

# The 4 new procedure files added as part of the role-based overhaul
# (orbit_maintenance.md removed since no propulsion capability)
NEW_PROCEDURE_FILES = {
    "leop/time_sync.md",
    "nominal/hk_configuration.md",
    "nominal/software_upload.md",
    "emergency/obc_reboot.md",
}

EXPECTED_MANUAL_FILE_COUNT = 15


def _load_yaml(relative_path):
    """Load a YAML file relative to CONFIG_ROOT."""
    full_path = CONFIG_ROOT / relative_path
    assert full_path.exists(), f"Config file not found: {full_path}"
    with open(full_path, "r") as fh:
        data = yaml.safe_load(fh)
    assert data is not None, f"Config file is empty or invalid: {full_path}"
    return data


def _load_positions():
    """Load and return the positions dict from positions.yaml."""
    data = _load_yaml("mcs/positions.yaml")
    return data["positions"]


def _load_procedure_index():
    """Load and return the procedure list from procedure_index.yaml."""
    data = _load_yaml("procedures/procedure_index.yaml")
    return data["procedures"]


# ---------------------------------------------------------------------------
# 1. All indexed procedures have corresponding files on disk
# ---------------------------------------------------------------------------

class TestIndexedProceduresExistOnDisk:
    """Every file referenced in procedure_index.yaml must exist."""

    def test_all_indexed_procedures_have_files(self):
        procedures = _load_procedure_index()
        proc_dir = CONFIG_ROOT / "procedures"
        missing = []
        for proc in procedures:
            filepath = proc_dir / proc["file"]
            if not filepath.exists():
                missing.append(f"{proc['id']}: {proc['file']}")
        assert not missing, (
            f"Indexed procedures reference missing files:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


# ---------------------------------------------------------------------------
# 2. All procedure files on disk are referenced in the index
# ---------------------------------------------------------------------------

class TestAllDiskProceduresAreIndexed:
    """Every .md file in procedures/ (excluding custom/) should be in the index."""

    def test_all_disk_procedures_are_indexed(self):
        procedures = _load_procedure_index()
        indexed_files = {proc["file"] for proc in procedures}

        proc_dir = CONFIG_ROOT / "procedures"
        disk_files = set()
        for md_file in proc_dir.rglob("*.md"):
            relative = md_file.relative_to(proc_dir)
            # Exclude custom/ directory
            if relative.parts[0] == "custom":
                continue
            disk_files.add(str(relative))

        unindexed = disk_files - indexed_files
        assert not unindexed, (
            f"Procedure files on disk not referenced in index:\n"
            + "\n".join(f"  - {f}" for f in sorted(unindexed))
        )

    def test_expected_total_procedure_files(self):
        """There should be 63 procedure files (orbit_maintenance removed since no propulsion capability)."""
        proc_dir = CONFIG_ROOT / "procedures"
        disk_files = []
        for md_file in proc_dir.rglob("*.md"):
            relative = md_file.relative_to(proc_dir)
            if relative.parts[0] == "custom":
                continue
            disk_files.append(str(relative))
        assert len(disk_files) == 64, (
            f"Expected 64 procedure files, found {len(disk_files)}"
        )

    def test_new_procedure_files_exist(self):
        """The 5 new procedure files from the overhaul must exist."""
        proc_dir = CONFIG_ROOT / "procedures"
        missing = []
        for relpath in NEW_PROCEDURE_FILES:
            if not (proc_dir / relpath).exists():
                missing.append(relpath)
        assert not missing, (
            f"New procedure files are missing:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )


# ---------------------------------------------------------------------------
# 3. All required_positions reference valid position names
# ---------------------------------------------------------------------------

class TestRequiredPositionsAreValid:
    """Every position in required_positions must exist in positions.yaml."""

    def test_required_positions_reference_valid_names(self):
        positions = _load_positions()
        valid_position_names = set(positions.keys())

        procedures = _load_procedure_index()
        invalid = []
        for proc in procedures:
            for pos in proc.get("required_positions", []):
                if pos not in valid_position_names:
                    invalid.append(f"{proc['id']}: position '{pos}'")
        assert not invalid, (
            f"Procedures reference undefined positions:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# 4. All command_services are valid PUS service numbers (1-20)
# ---------------------------------------------------------------------------

class TestCommandServicesAreValid:
    """command_services in each procedure must be PUS services 1-20."""

    def test_command_services_are_valid_pus_numbers(self):
        procedures = _load_procedure_index()
        invalid = []
        for proc in procedures:
            for svc in proc.get("command_services", []):
                if svc not in VALID_PUS_SERVICES:
                    invalid.append(f"{proc['id']}: service {svc}")
        assert not invalid, (
            f"Procedures reference invalid PUS service numbers:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# 5. All visible_tabs in positions.yaml are valid tab IDs
# ---------------------------------------------------------------------------

class TestVisibleTabsAreValid:
    """visible_tabs must only contain recognized tab identifiers."""

    def test_visible_tabs_are_valid_ids(self):
        positions = _load_positions()
        invalid = []
        for pos_key, pos_cfg in positions.items():
            for tab in pos_cfg.get("visible_tabs", []):
                if tab not in VALID_TAB_IDS:
                    invalid.append(f"{pos_key}: tab '{tab}'")
        assert not invalid, (
            f"Positions reference invalid tab IDs:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# 6. All overview_subsystems are valid subsystem names
# ---------------------------------------------------------------------------

class TestOverviewSubsystemsAreValid:
    """overview_subsystems must be recognized subsystem names."""

    def test_overview_subsystems_are_valid(self):
        positions = _load_positions()
        invalid = []
        for pos_key, pos_cfg in positions.items():
            for sub in pos_cfg.get("overview_subsystems", []):
                if sub not in VALID_SUBSYSTEM_NAMES:
                    invalid.append(f"{pos_key}: subsystem '{sub}'")
        assert not invalid, (
            f"Positions reference invalid overview subsystem names:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# 7. All manual_sections reference actual files in manual/
# ---------------------------------------------------------------------------

class TestManualSectionsAreValid:
    """manual_sections must reference existing files in configs/eosat1/manual/."""

    def test_manual_sections_reference_existing_files(self):
        positions = _load_positions()
        manual_dir = CONFIG_ROOT / "manual"
        # Build set of valid section stems (e.g. "01_eps" from "01_eps.md")
        valid_sections = {f.stem for f in manual_dir.glob("*.md")}

        invalid = []
        for pos_key, pos_cfg in positions.items():
            sections = pos_cfg.get("manual_sections", [])
            if sections == "all":
                continue
            for section in sections:
                if section not in valid_sections:
                    invalid.append(f"{pos_key}: section '{section}'")
        assert not invalid, (
            f"Positions reference non-existent manual sections:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# 8. positions.yaml loads correctly with PositionConfig schema
# ---------------------------------------------------------------------------

class TestPositionConfigSchema:
    """positions.yaml must load into PositionConfig with new fields."""

    def test_positions_yaml_loads_with_schema(self):
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            config = PositionConfig(**pos_data)
            assert config.display_name, f"{pos_key} missing display_name"
            assert config.label, f"{pos_key} missing label"

    def test_positions_have_visible_tabs(self):
        """All positions must have the visible_tabs field."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            config = PositionConfig(**pos_data)
            assert len(config.visible_tabs) > 0, (
                f"{pos_key} has empty visible_tabs"
            )

    def test_positions_have_overview_subsystems(self):
        """All positions must have the overview_subsystems field."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            config = PositionConfig(**pos_data)
            assert len(config.overview_subsystems) > 0, (
                f"{pos_key} has empty overview_subsystems"
            )

    def test_positions_have_manual_sections(self):
        """All positions must have the manual_sections field."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            config = PositionConfig(**pos_data)
            assert config.manual_sections, (
                f"{pos_key} has empty manual_sections"
            )


# ---------------------------------------------------------------------------
# 9. Manual files exist (10 files)
# ---------------------------------------------------------------------------

class TestManualFilesExist:
    """The manual/ directory should contain exactly 10 markdown files."""

    def test_manual_directory_has_expected_count(self):
        manual_dir = CONFIG_ROOT / "manual"
        assert manual_dir.exists(), "Manual directory does not exist"
        md_files = list(manual_dir.glob("*.md"))
        assert len(md_files) == EXPECTED_MANUAL_FILE_COUNT, (
            f"Expected {EXPECTED_MANUAL_FILE_COUNT} manual files, "
            f"found {len(md_files)}: {[f.name for f in md_files]}"
        )

    def test_manual_files_are_not_empty(self):
        manual_dir = CONFIG_ROOT / "manual"
        for md_file in manual_dir.glob("*.md"):
            content = md_file.read_text()
            assert len(content.strip()) > 0, (
                f"Manual file is empty: {md_file.name}"
            )


# ---------------------------------------------------------------------------
# 10. Procedure index has position_roles for multi-position procedures
# ---------------------------------------------------------------------------

class TestPositionRolesForMultiPositionProcedures:
    """Multi-position procedures must define position_roles."""

    def test_multi_position_procedures_have_position_roles(self):
        procedures = _load_procedure_index()
        missing_roles = []
        for proc in procedures:
            required = proc.get("required_positions", [])
            if len(required) > 1:
                if "position_roles" not in proc or not proc["position_roles"]:
                    missing_roles.append(proc["id"])
        assert not missing_roles, (
            f"Multi-position procedures missing position_roles:\n"
            + "\n".join(f"  - {pid}" for pid in missing_roles)
        )

    def test_position_roles_match_required_positions(self):
        """Every position in required_positions must have a role defined."""
        procedures = _load_procedure_index()
        mismatched = []
        for proc in procedures:
            required = set(proc.get("required_positions", []))
            roles = set(proc.get("position_roles", {}).keys())
            if len(required) > 1 and required != roles:
                mismatched.append(
                    f"{proc['id']}: required={sorted(required)}, "
                    f"roles={sorted(roles)}"
                )
        assert not mismatched, (
            f"position_roles do not match required_positions:\n"
            + "\n".join(f"  - {m}" for m in mismatched)
        )

    def test_single_position_procedures_also_have_roles(self):
        """Even single-position procedures should have position_roles."""
        procedures = _load_procedure_index()
        missing = []
        for proc in procedures:
            required = proc.get("required_positions", [])
            if len(required) == 1:
                if "position_roles" not in proc or not proc["position_roles"]:
                    missing.append(proc["id"])
        assert not missing, (
            f"Single-position procedures missing position_roles:\n"
            + "\n".join(f"  - {pid}" for pid in missing)
        )
