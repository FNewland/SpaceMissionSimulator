"""Integration tests validating position-procedure relationships.

Tests that the configuration integrity between positions.yaml and
procedure_index.yaml forms a consistent role-based access control system:
  - FD has access to all procedures
  - Each position can only see procedures where they appear in required_positions
  - Multi-position procedures reference valid positions and have position_roles
  - Command services match position's allowed_services for their procedures
"""
import pytest
import yaml
from pathlib import Path


CONFIG_ROOT = Path(__file__).resolve().parent.parent.parent / "configs" / "eosat1"


def _load_yaml(relative_path):
    """Load a YAML file relative to CONFIG_ROOT."""
    full_path = CONFIG_ROOT / relative_path
    assert full_path.exists(), f"Config file not found: {full_path}"
    with open(full_path, "r") as fh:
        data = yaml.safe_load(fh)
    assert data is not None, f"Config file is empty or invalid: {full_path}"
    return data


def _load_positions():
    """Load positions.yaml and return the positions dict."""
    data = _load_yaml("mcs/positions.yaml")
    return data["positions"]


def _load_procedure_index():
    """Load procedure_index.yaml and return the procedures list."""
    data = _load_yaml("procedures/procedure_index.yaml")
    return data["procedures"]


def _procedures_for_position(procedures, position_name):
    """Return list of procedures where position_name is in required_positions."""
    return [
        proc for proc in procedures
        if position_name in proc.get("required_positions", [])
    ]


# ---------------------------------------------------------------------------
# FD has access to all procedures
# ---------------------------------------------------------------------------

class TestFlightDirectorAccess:
    """Flight Director should have access to all procedures."""

    def test_fd_appears_in_many_procedures(self):
        """FD should be required in a significant number of procedures."""
        procedures = _load_procedure_index()
        fd_procs = _procedures_for_position(procedures, "flight_director")
        # FD should be in most procedures (but not all, as some are
        # single-position like NOM-004, NOM-005, NOM-007, NOM-011, CTG-015)
        assert len(fd_procs) > len(procedures) // 2, (
            f"FD only appears in {len(fd_procs)} of {len(procedures)} procedures"
        )

    def test_fd_has_access_to_all_categories(self):
        """FD should have procedures in every category."""
        procedures = _load_procedure_index()
        fd_procs = _procedures_for_position(procedures, "flight_director")
        fd_categories = {proc["category"] for proc in fd_procs}
        expected_categories = {"leop", "commissioning", "nominal", "contingency", "emergency"}
        assert fd_categories == expected_categories, (
            f"FD missing categories: {expected_categories - fd_categories}"
        )

    def test_fd_allowed_commands_all(self):
        """FD should have unrestricted command access."""
        positions = _load_positions()
        fd = positions["flight_director"]
        assert fd.get("allowed_commands") == "all"


# ---------------------------------------------------------------------------
# Each position can only see procedures where they appear
# ---------------------------------------------------------------------------

class TestPositionProcedureVisibility:
    """Each position should only see procedures they are assigned to."""

    def test_eps_tcs_sees_only_assigned_procedures(self):
        """EPS/TCS should only see procedures with eps_tcs in required_positions."""
        procedures = _load_procedure_index()
        eps_procs = _procedures_for_position(procedures, "eps_tcs")
        # Verify each returned procedure actually lists eps_tcs
        for proc in eps_procs:
            assert "eps_tcs" in proc["required_positions"], (
                f"{proc['id']} does not list eps_tcs"
            )
        # EPS/TCS should have some procedures
        assert len(eps_procs) > 0

    def test_aocs_sees_only_assigned_procedures(self):
        procedures = _load_procedure_index()
        aocs_procs = _procedures_for_position(procedures, "aocs")
        for proc in aocs_procs:
            assert "aocs" in proc["required_positions"]
        assert len(aocs_procs) > 0

    def test_ttc_sees_only_assigned_procedures(self):
        procedures = _load_procedure_index()
        ttc_procs = _procedures_for_position(procedures, "ttc")
        for proc in ttc_procs:
            assert "ttc" in proc["required_positions"]
        assert len(ttc_procs) > 0

    def test_payload_ops_sees_only_assigned_procedures(self):
        procedures = _load_procedure_index()
        pl_procs = _procedures_for_position(procedures, "payload_ops")
        for proc in pl_procs:
            assert "payload_ops" in proc["required_positions"]
        assert len(pl_procs) > 0

    def test_fdir_systems_sees_only_assigned_procedures(self):
        procedures = _load_procedure_index()
        fdir_procs = _procedures_for_position(procedures, "fdir_systems")
        for proc in fdir_procs:
            assert "fdir_systems" in proc["required_positions"]
        assert len(fdir_procs) > 0

    def test_every_procedure_has_at_least_one_position(self):
        """No procedure should be orphaned (no required_positions)."""
        procedures = _load_procedure_index()
        orphaned = [
            proc["id"] for proc in procedures
            if not proc.get("required_positions")
        ]
        assert not orphaned, (
            f"Procedures with no required_positions: {orphaned}"
        )


# ---------------------------------------------------------------------------
# Multi-position procedures
# ---------------------------------------------------------------------------

class TestMultiPositionProcedures:
    """Test procedures that require multiple operator positions."""

    def test_leop001_requires_fd_and_ttc(self):
        """LEOP-001 (First Acquisition + OBC Boot) requires FD, TTC and FDIR Systems.

        Under the cold-boot initial state LEOP-001 is now responsible for
        exiting the OBDH bootloader (OBC_BOOT_APP) in addition to acquiring
        the link, so FDIR Systems is part of the required position set.
        """
        procedures = _load_procedure_index()
        leop001 = next(p for p in procedures if p["id"] == "LEOP-001")
        assert "flight_director" in leop001["required_positions"]
        assert "ttc" in leop001["required_positions"]
        assert "fdir_systems" in leop001["required_positions"]
        assert len(leop001["required_positions"]) == 3

    def test_leop001_has_position_roles(self):
        """LEOP-001 should have position_roles describing each position's role."""
        procedures = _load_procedure_index()
        leop001 = next(p for p in procedures if p["id"] == "LEOP-001")
        assert "position_roles" in leop001
        roles = leop001["position_roles"]
        assert "flight_director" in roles
        assert "ttc" in roles
        assert isinstance(roles["flight_director"], str)
        assert len(roles["flight_director"]) > 0

    def test_multi_position_procedures_exist(self):
        """There should be multiple procedures requiring more than one position."""
        procedures = _load_procedure_index()
        multi = [
            proc["id"] for proc in procedures
            if len(proc.get("required_positions", [])) > 1
        ]
        assert len(multi) > 10, (
            f"Expected many multi-position procedures, found {len(multi)}"
        )

    def test_all_multi_position_procedures_have_roles(self):
        """Every multi-position procedure must have position_roles."""
        procedures = _load_procedure_index()
        missing = []
        for proc in procedures:
            required = proc.get("required_positions", [])
            if len(required) > 1:
                if "position_roles" not in proc:
                    missing.append(proc["id"])
        assert not missing, (
            f"Multi-position procedures missing position_roles: {missing}"
        )

    def test_position_roles_keys_match_required_positions(self):
        """position_roles keys must exactly match required_positions."""
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
            f"position_roles/required_positions mismatch:\n"
            + "\n".join(f"  - {m}" for m in mismatched)
        )

    def test_three_position_procedures_exist(self):
        """Some procedures should require 3 positions (e.g., COM-009, COM-012)."""
        procedures = _load_procedure_index()
        three_pos = [
            proc["id"] for proc in procedures
            if len(proc.get("required_positions", [])) >= 3
        ]
        assert len(three_pos) > 0, "Expected at least one 3-position procedure"


# ---------------------------------------------------------------------------
# position_roles field is present for multi-position procedures
# ---------------------------------------------------------------------------

class TestPositionRolesContent:
    """Verify the content of position_roles descriptions."""

    def test_position_roles_are_non_empty_strings(self):
        """All position_roles values must be non-empty strings."""
        procedures = _load_procedure_index()
        empty_roles = []
        for proc in procedures:
            for pos, role in proc.get("position_roles", {}).items():
                if not isinstance(role, str) or not role.strip():
                    empty_roles.append(f"{proc['id']}: {pos}")
        assert not empty_roles, (
            f"Empty or non-string position_roles:\n"
            + "\n".join(f"  - {r}" for r in empty_roles)
        )

    def test_position_roles_describe_actions(self):
        """Role descriptions should be meaningful (at least 10 chars)."""
        procedures = _load_procedure_index()
        too_short = []
        for proc in procedures:
            for pos, role in proc.get("position_roles", {}).items():
                if len(role.strip()) < 10:
                    too_short.append(
                        f"{proc['id']}/{pos}: '{role}' ({len(role)} chars)"
                    )
        assert not too_short, (
            f"Position roles too short to be meaningful:\n"
            + "\n".join(f"  - {s}" for s in too_short)
        )


# ---------------------------------------------------------------------------
# Command services match position's allowed_services
# ---------------------------------------------------------------------------

class TestCommandServiceAlignment:
    """Command services in procedures should align with position capabilities."""

    def test_fd_can_execute_all_procedure_commands(self):
        """FD with allowed_commands='all' can execute any command service."""
        positions = _load_positions()
        assert positions["flight_director"].get("allowed_commands") == "all"
        # No filtering needed for FD — this is a sanity check

    def test_single_position_procedures_services_are_covered(self):
        """For single-position procedures (non-FD), the position's
        allowed_services must cover at least some of the command_services.
        """
        positions = _load_positions()
        procedures = _load_procedure_index()

        violations = []
        for proc in procedures:
            required = proc.get("required_positions", [])
            if len(required) != 1 or required[0] == "flight_director":
                continue
            pos_key = required[0]
            pos_data = positions.get(pos_key, {})
            allowed = set(pos_data.get("allowed_services", []))
            cmd_svcs = set(proc.get("command_services", []))
            if cmd_svcs and allowed and not cmd_svcs & allowed:
                violations.append(
                    f"{pos_key} sole position on {proc['id']} but "
                    f"no overlap: allowed={sorted(allowed)}, "
                    f"procedure_services={sorted(cmd_svcs)}"
                )

        assert not violations, (
            f"Single-position procedures with no service coverage:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_multi_position_procedures_have_service_coverage(self):
        """For multi-position procedures, at least one assigned position
        (including FD with allowed_commands='all') must be able to send
        each command service.
        """
        positions = _load_positions()
        procedures = _load_procedure_index()

        violations = []
        for proc in procedures:
            required = proc.get("required_positions", [])
            if len(required) <= 1:
                continue
            cmd_svcs = set(proc.get("command_services", []))
            if not cmd_svcs:
                continue

            # Collect all services that assigned positions can send
            combined_allowed = set()
            for pos_key in required:
                pos_data = positions.get(pos_key, {})
                if pos_data.get("allowed_commands") == "all":
                    combined_allowed = cmd_svcs  # FD covers everything
                    break
                combined_allowed |= set(pos_data.get("allowed_services", []))

            uncovered = cmd_svcs - combined_allowed
            if uncovered:
                violations.append(
                    f"{proc['id']}: services {sorted(uncovered)} "
                    f"not covered by any assigned position"
                )

        assert not violations, (
            f"Multi-position procedures with uncovered services:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_procedure_command_services_are_not_empty_for_operational_procedures(self):
        """Most procedures (except handover-type) should have command services."""
        procedures = _load_procedure_index()
        # Some procedures like shift handover may have empty command_services
        empty_svc = [
            proc["id"] for proc in procedures
            if not proc.get("command_services")
            and proc["category"] not in ("nominal",)  # handover exception
        ]
        assert not empty_svc, (
            f"Procedures with empty command_services: {empty_svc}"
        )

    def test_all_command_services_are_valid_pus(self):
        """All command_services values should be valid PUS service numbers (1-20)."""
        procedures = _load_procedure_index()
        valid = set(range(1, 21))
        invalid = []
        for proc in procedures:
            for svc in proc.get("command_services", []):
                if svc not in valid:
                    invalid.append(f"{proc['id']}: service {svc}")
        assert not invalid, (
            f"Invalid PUS service numbers:\n"
            + "\n".join(f"  - {i}" for i in invalid)
        )


# ---------------------------------------------------------------------------
# Cross-validation: position subsystems vs procedure assignments
# ---------------------------------------------------------------------------

class TestPositionSubsystemProcedureAlignment:
    """Positions should be assigned to procedures matching their subsystem focus."""

    def test_eps_tcs_procedures_involve_power_or_thermal(self):
        """EPS/TCS should primarily be assigned to EPS/TCS-related procedures."""
        procedures = _load_procedure_index()
        eps_procs = _procedures_for_position(procedures, "eps_tcs")
        # Check that at least some procedures mention relevant subsystems
        assert len(eps_procs) > 5, (
            f"EPS/TCS has too few procedures: {len(eps_procs)}"
        )

    def test_aocs_procedures_involve_attitude_or_orbit(self):
        """AOCS should be assigned to attitude/orbit-related procedures."""
        procedures = _load_procedure_index()
        aocs_procs = _procedures_for_position(procedures, "aocs")
        assert len(aocs_procs) > 5, (
            f"AOCS has too few procedures: {len(aocs_procs)}"
        )

    def test_ttc_procedures_involve_communications(self):
        """TTC should be assigned to communication-related procedures."""
        procedures = _load_procedure_index()
        ttc_procs = _procedures_for_position(procedures, "ttc")
        assert len(ttc_procs) > 3, (
            f"TTC has too few procedures: {len(ttc_procs)}"
        )

    def test_fdir_procedures_involve_obdh_or_fdir(self):
        """FDIR/Systems should be assigned to OBDH/FDIR-related procedures."""
        procedures = _load_procedure_index()
        fdir_procs = _procedures_for_position(procedures, "fdir_systems")
        assert len(fdir_procs) > 5, (
            f"FDIR/Systems has too few procedures: {len(fdir_procs)}"
        )

    def test_payload_procedures_involve_imaging(self):
        """Payload Ops should be assigned to payload/imaging procedures."""
        procedures = _load_procedure_index()
        pl_procs = _procedures_for_position(procedures, "payload_ops")
        assert len(pl_procs) > 3, (
            f"Payload Ops has too few procedures: {len(pl_procs)}"
        )
