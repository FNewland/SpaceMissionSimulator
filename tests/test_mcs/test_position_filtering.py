"""Tests for position-based access control and filtering.

Validates that each operator position has the correct:
  - visible_tabs (which UI tabs they can see)
  - overview_subsystems (which subsystems appear on their overview)
  - manual_sections (which manual chapters they can access)
  - allowed_commands / allowed_services / allowed_func_ids
"""
import pytest
import yaml
from pathlib import Path

from smo_common.config.schemas import PositionConfig


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "configs" / "eosat1"

ALL_TABS = [
    "system_dashboard", "power_monitor", "fdir_panel", "contact_schedule", "procedure_panel",
    "overview", "eps", "aocs", "tcs", "obdh", "ttc",
    "payload", "commanding", "pus", "procedures", "manual",
]

VALID_SUBSYSTEM_NAMES = {"eps", "aocs", "tcs", "obdh", "ttc", "payload", "fdir"}


def _load_positions():
    """Load positions.yaml and return dict of position configs."""
    path = CONFIG_ROOT / "mcs" / "positions.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return data["positions"]


def _position_config(positions, name):
    """Create a PositionConfig from raw YAML data."""
    return PositionConfig(**positions[name])


# ---------------------------------------------------------------------------
# Flight Director — full access
# ---------------------------------------------------------------------------

class TestFlightDirectorPosition:
    """Flight Director should have access to all tabs, subsystems, and commands."""

    def test_visible_tabs_all_16(self):
        positions = _load_positions()
        cfg = _position_config(positions, "flight_director")
        assert set(cfg.visible_tabs) == set(ALL_TABS), (
            f"FD visible_tabs mismatch: {cfg.visible_tabs}"
        )
        assert len(cfg.visible_tabs) == 16

    def test_overview_subsystems_all(self):
        positions = _load_positions()
        cfg = _position_config(positions, "flight_director")
        expected = {"eps", "aocs", "tcs", "obdh", "ttc", "payload"}
        assert set(cfg.overview_subsystems) == expected

    def test_manual_sections_all(self):
        positions = _load_positions()
        cfg = _position_config(positions, "flight_director")
        assert cfg.manual_sections == "all"

    def test_allowed_commands_all(self):
        positions = _load_positions()
        cfg = _position_config(positions, "flight_director")
        assert cfg.allowed_commands == "all"


# ---------------------------------------------------------------------------
# EPS/TCS — Power & Thermal
# ---------------------------------------------------------------------------

class TestEpsTcsPosition:
    """EPS/TCS operator should see overview, eps, tcs, commanding, procedures, manual."""

    def test_visible_tabs(self):
        positions = _load_positions()
        cfg = _position_config(positions, "eps_tcs")
        expected = ["overview", "eps", "tcs", "commanding", "procedures", "manual"]
        assert cfg.visible_tabs == expected

    def test_overview_subsystems(self):
        positions = _load_positions()
        cfg = _position_config(positions, "eps_tcs")
        assert set(cfg.overview_subsystems) == {"eps", "tcs"}

    def test_manual_sections(self):
        positions = _load_positions()
        cfg = _position_config(positions, "eps_tcs")
        assert isinstance(cfg.manual_sections, list)
        assert "01_eps" in cfg.manual_sections
        assert "03_tcs" in cfg.manual_sections

    def test_has_allowed_services(self):
        positions = _load_positions()
        cfg = _position_config(positions, "eps_tcs")
        assert cfg.allowed_commands is None
        assert len(cfg.allowed_services) > 0

    def test_has_allowed_func_ids(self):
        positions = _load_positions()
        cfg = _position_config(positions, "eps_tcs")
        assert len(cfg.allowed_func_ids) > 0


# ---------------------------------------------------------------------------
# AOCS — Flight Dynamics
# ---------------------------------------------------------------------------

class TestAocsPosition:
    """AOCS operator should see overview, aocs, commanding, procedures, manual."""

    def test_visible_tabs(self):
        positions = _load_positions()
        cfg = _position_config(positions, "aocs")
        expected = ["overview", "aocs", "commanding", "procedures", "manual"]
        assert cfg.visible_tabs == expected

    def test_overview_subsystems(self):
        positions = _load_positions()
        cfg = _position_config(positions, "aocs")
        assert set(cfg.overview_subsystems) == {"aocs"}

    def test_manual_sections(self):
        positions = _load_positions()
        cfg = _position_config(positions, "aocs")
        assert isinstance(cfg.manual_sections, list)
        assert "02_aocs" in cfg.manual_sections
        assert "07_orbit_ops" in cfg.manual_sections

    def test_has_allowed_services(self):
        positions = _load_positions()
        cfg = _position_config(positions, "aocs")
        assert cfg.allowed_commands is None
        assert len(cfg.allowed_services) > 0

    def test_has_allowed_func_ids(self):
        positions = _load_positions()
        cfg = _position_config(positions, "aocs")
        assert len(cfg.allowed_func_ids) > 0


# ---------------------------------------------------------------------------
# TTC — Tracking Telemetry & Command
# ---------------------------------------------------------------------------

class TestTtcPosition:
    """TTC operator should see overview, ttc, commanding, procedures, manual."""

    def test_visible_tabs(self):
        positions = _load_positions()
        cfg = _position_config(positions, "ttc")
        expected = ["overview", "ttc", "commanding", "procedures", "manual"]
        assert cfg.visible_tabs == expected

    def test_overview_subsystems(self):
        positions = _load_positions()
        cfg = _position_config(positions, "ttc")
        assert set(cfg.overview_subsystems) == {"ttc"}

    def test_manual_sections(self):
        positions = _load_positions()
        cfg = _position_config(positions, "ttc")
        assert isinstance(cfg.manual_sections, list)
        assert "05_ttc" in cfg.manual_sections

    def test_has_allowed_services(self):
        positions = _load_positions()
        cfg = _position_config(positions, "ttc")
        assert cfg.allowed_commands is None
        assert len(cfg.allowed_services) > 0


# ---------------------------------------------------------------------------
# Payload Operations
# ---------------------------------------------------------------------------

class TestPayloadOpsPosition:
    """Payload operator should see overview, payload, commanding, procedures, manual."""

    def test_visible_tabs(self):
        positions = _load_positions()
        cfg = _position_config(positions, "payload_ops")
        expected = ["overview", "payload", "commanding", "procedures", "manual"]
        assert cfg.visible_tabs == expected

    def test_overview_subsystems(self):
        positions = _load_positions()
        cfg = _position_config(positions, "payload_ops")
        assert set(cfg.overview_subsystems) == {"payload"}

    def test_manual_sections(self):
        positions = _load_positions()
        cfg = _position_config(positions, "payload_ops")
        assert isinstance(cfg.manual_sections, list)
        assert "06_payload" in cfg.manual_sections

    def test_has_allowed_services(self):
        positions = _load_positions()
        cfg = _position_config(positions, "payload_ops")
        assert cfg.allowed_commands is None
        assert len(cfg.allowed_services) > 0


# ---------------------------------------------------------------------------
# FDIR / Systems
# ---------------------------------------------------------------------------

class TestFdirSystemsPosition:
    """FDIR/Systems operator should see overview, obdh, commanding, pus, procedures, manual."""

    def test_visible_tabs(self):
        positions = _load_positions()
        cfg = _position_config(positions, "fdir_systems")
        expected = ["overview", "obdh", "commanding", "pus", "procedures", "manual"]
        assert cfg.visible_tabs == expected

    def test_overview_subsystems(self):
        positions = _load_positions()
        cfg = _position_config(positions, "fdir_systems")
        assert set(cfg.overview_subsystems) == {"obdh"}

    def test_manual_sections(self):
        positions = _load_positions()
        cfg = _position_config(positions, "fdir_systems")
        assert isinstance(cfg.manual_sections, list)
        assert "04_obdh" in cfg.manual_sections
        assert "08_fdir" in cfg.manual_sections

    def test_has_allowed_services(self):
        positions = _load_positions()
        cfg = _position_config(positions, "fdir_systems")
        assert cfg.allowed_commands is None
        assert len(cfg.allowed_services) > 0


# ---------------------------------------------------------------------------
# Cross-position checks
# ---------------------------------------------------------------------------

class TestCrossPositionConsistency:
    """Cross-cutting checks that apply to all positions."""

    def test_overview_subsystems_is_subset_of_subsystems(self):
        """Each position's overview_subsystems must be a subset of their subsystems."""
        positions = _load_positions()
        violations = []
        for pos_key, pos_data in positions.items():
            subsystems = set(pos_data.get("subsystems", []))
            overview = set(pos_data.get("overview_subsystems", []))
            if not overview.issubset(subsystems):
                extra = overview - subsystems
                violations.append(
                    f"{pos_key}: overview has {extra} not in subsystems"
                )
        assert not violations, (
            f"overview_subsystems not a subset of subsystems:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_all_positions_have_overview_tab(self):
        """Every position should have the 'overview' tab."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            tabs = pos_data.get("visible_tabs", [])
            assert "overview" in tabs, (
                f"{pos_key} does not have 'overview' in visible_tabs"
            )

    def test_all_positions_have_procedures_and_manual_tabs(self):
        """Every position should have procedures and manual tabs."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            tabs = pos_data.get("visible_tabs", [])
            assert "procedures" in tabs, (
                f"{pos_key} does not have 'procedures' tab"
            )
            assert "manual" in tabs, (
                f"{pos_key} does not have 'manual' tab"
            )

    def test_non_fd_positions_have_restricted_commands(self):
        """Non-FD positions must NOT have allowed_commands='all'."""
        positions = _load_positions()
        for pos_key, pos_data in positions.items():
            if pos_key == "flight_director":
                continue
            cfg = PositionConfig(**pos_data)
            assert cfg.allowed_commands != "all", (
                f"{pos_key} should not have allowed_commands='all'"
            )
            assert len(cfg.allowed_services) > 0, (
                f"{pos_key} has no allowed_services"
            )
            assert len(cfg.allowed_func_ids) > 0, (
                f"{pos_key} has no allowed_func_ids"
            )

    def test_six_positions_defined(self):
        """There should be exactly 6 operator positions."""
        positions = _load_positions()
        expected = {
            "flight_director", "eps_tcs", "aocs",
            "ttc", "payload_ops", "fdir_systems",
        }
        assert set(positions.keys()) == expected
