"""Tests for PUS TC acceptance/rejection via _check_tc_acceptance.

Validates that the SimulationEngine correctly accepts valid telecommands
and rejects invalid ones with the appropriate error codes:
  0x0001 = unknown service
  0x0002 = unknown subtype
  0x0003 = invalid data length
"""
import pytest
from unittest.mock import MagicMock
from smo_simulator.engine import SimulationEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine():
    """Create a mock engine with the real _check_tc_acceptance bound."""
    engine = MagicMock(spec=SimulationEngine)
    engine._check_tc_acceptance = (
        SimulationEngine._check_tc_acceptance.__get__(engine)
    )
    return engine


# Known services that accept TCs (S1 is TM-only, not included)
KNOWN_SERVICES = {3, 5, 6, 8, 9, 11, 12, 15, 17, 19, 20}

VALID_SUBTYPES = {
    3: {1, 2, 3, 4, 5, 6, 27, 31},
    5: {5, 6, 7, 8},
    6: {2, 5, 9},
    8: {1},
    9: {1, 2},
    11: {4, 7, 9, 11, 13, 17},
    12: {1, 2, 6, 7},
    15: {1, 2, 9, 11, 13},
    17: {1},
    19: {1, 2, 4, 5, 8},
    20: {1, 3},
}


# ===================================================================
# Service 3 - Housekeeping
# ===================================================================

class TestS3Acceptance:
    """Service 3 (Housekeeping) acceptance tests."""

    @pytest.mark.parametrize("subtype", sorted(VALID_SUBTYPES[3]))
    def test_s3_all_valid_subtypes_accepted(self, subtype):
        """Each valid S3 subtype should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(3, subtype, b'')
        assert accepted, f"S3.{subtype} should be accepted"
        assert code == 0

    def test_s3_invalid_subtype_rejected(self):
        """An invalid S3 subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(3, 99, b'')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 5 - Event Reporting
# ===================================================================

class TestS5Acceptance:
    """Service 5 (Event Reporting) acceptance tests."""

    @pytest.mark.parametrize("subtype", sorted(VALID_SUBTYPES[5]))
    def test_s5_valid_subtypes_accepted(self, subtype):
        """Each valid S5 subtype should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(5, subtype, b'')
        assert accepted, f"S5.{subtype} should be accepted"
        assert code == 0

    def test_s5_invalid_subtype_rejected(self):
        """An invalid S5 subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(5, 1, b'')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 6 - Memory Management
# ===================================================================

class TestS6Acceptance:
    """Service 6 (Memory Management) acceptance tests."""

    @pytest.mark.parametrize("subtype", sorted(VALID_SUBTYPES[6]))
    def test_s6_valid_subtypes_accepted(self, subtype):
        """Each valid S6 subtype should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(6, subtype, b'')
        assert accepted, f"S6.{subtype} should be accepted"
        assert code == 0

    def test_s6_invalid_subtype_rejected(self):
        """An invalid S6 subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(6, 1, b'')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 8 - Function Management
# ===================================================================

class TestS8Acceptance:
    """Service 8 (Function Management) acceptance tests."""

    def test_s8_with_data_accepted(self):
        """S8.1 with non-empty data should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(8, 1, b'\x00')
        assert accepted
        assert code == 0

    def test_s8_with_multi_byte_data_accepted(self):
        """S8.1 with multi-byte data should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(8, 1, b'\x00\x03\x01')
        assert accepted
        assert code == 0

    def test_s8_missing_data_rejected(self):
        """S8.1 with empty data should be rejected with 0x0003."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(8, 1, b'')
        assert not accepted
        assert code == 0x0003

    def test_s8_invalid_subtype_rejected(self):
        """S8 with an invalid subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(8, 2, b'\x00')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 12 - Monitoring
# ===================================================================

class TestS12Acceptance:
    """Service 12 (Monitoring) acceptance tests."""

    @pytest.mark.parametrize("subtype", sorted(VALID_SUBTYPES[12]))
    def test_s12_valid_subtypes_accepted(self, subtype):
        """Each valid S12 subtype should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(12, subtype, b'')
        assert accepted, f"S12.{subtype} should be accepted"
        assert code == 0

    def test_s12_invalid_subtype_rejected(self):
        """An invalid S12 subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(12, 99, b'')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 19 - Event-Action
# ===================================================================

class TestS19Acceptance:
    """Service 19 (Event-Action) acceptance tests."""

    @pytest.mark.parametrize("subtype", sorted(VALID_SUBTYPES[19]))
    def test_s19_valid_subtypes_accepted(self, subtype):
        """Each valid S19 subtype should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(19, subtype, b'')
        assert accepted, f"S19.{subtype} should be accepted"
        assert code == 0

    def test_s19_invalid_subtype_rejected(self):
        """An invalid S19 subtype should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(19, 3, b'')
        assert not accepted
        assert code == 0x0002


# ===================================================================
# Service 1 - TM-only (not accepted as TC)
# ===================================================================

class TestS1NotAccepted:
    """Service 1 (Verification) is TM-only and must not be accepted."""

    def test_s1_not_accepted(self):
        """S1 is TM-only and should be rejected with unknown-service error."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(1, 1, b'')
        assert not accepted
        assert code == 0x0001

    def test_s1_subtype_2_not_accepted(self):
        """S1.2 should also be rejected -- S1 is entirely TM-only."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(1, 2, b'')
        assert not accepted
        assert code == 0x0001


# ===================================================================
# Invalid / unknown services
# ===================================================================

class TestInvalidServiceRejected:
    """Unknown services must be rejected with error code 0x0001."""

    @pytest.mark.parametrize("service", [0, 2, 4, 7, 10, 13, 14, 16, 18, 99, 255])
    def test_invalid_service_rejected(self, service):
        """Services outside the known set should be rejected."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(service, 1, b'')
        assert not accepted, f"Service {service} should be rejected"
        assert code == 0x0001


# ===================================================================
# Invalid subtypes across multiple services
# ===================================================================

class TestInvalidSubtypeRejected:
    """Invalid subtypes should be rejected with error code 0x0002."""

    @pytest.mark.parametrize("service,subtype", [
        (3, 10),
        (5, 1),
        (6, 1),
        (9, 99),
        (11, 1),
        (12, 3),
        (15, 3),
        (17, 2),
        (19, 3),
        (20, 2),
    ])
    def test_invalid_subtype_rejected(self, service, subtype):
        """Bad subtypes should be rejected with 0x0002."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(service, subtype, b'')
        assert not accepted, f"S{service}.{subtype} should be rejected"
        assert code == 0x0002


# ===================================================================
# Cross-service: verify each new service is accepted
# ===================================================================

class TestEachNewServiceAccepted:
    """Verify that each service in the known set is accepted with a valid subtype."""

    @pytest.mark.parametrize("service", sorted(KNOWN_SERVICES))
    def test_each_known_service_accepted(self, service):
        """Every known service should be accepted when given a valid subtype."""
        engine = _make_engine()
        # Pick the first valid subtype for the service
        subtype = min(VALID_SUBTYPES[service])
        # S8.1 needs data
        data = b'\x00' if service == 8 else b''
        accepted, code = engine._check_tc_acceptance(service, subtype, data)
        assert accepted, f"Service {service} with subtype {subtype} should be accepted"
        assert code == 0


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    """Edge-case tests for _check_tc_acceptance."""

    def test_s9_subtype_1_accepted(self):
        """S9.1 (time update) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(9, 1, b'')
        assert accepted
        assert code == 0

    def test_s9_subtype_2_accepted(self):
        """S9.2 (time report request) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(9, 2, b'')
        assert accepted
        assert code == 0

    def test_s11_valid_subtype_accepted(self):
        """S11.4 (schedule insert) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(11, 4, b'')
        assert accepted
        assert code == 0

    def test_s15_valid_subtype_accepted(self):
        """S15.1 (storage enable) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(15, 1, b'')
        assert accepted
        assert code == 0

    def test_s17_connection_test_accepted(self):
        """S17.1 (connection test) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(17, 1, b'')
        assert accepted
        assert code == 0

    def test_s20_set_param_accepted(self):
        """S20.1 (set parameter) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(20, 1, b'')
        assert accepted
        assert code == 0

    def test_s20_get_param_accepted(self):
        """S20.3 (get parameter) should be accepted."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(20, 3, b'')
        assert accepted
        assert code == 0

    def test_accepted_returns_zero_error_code(self):
        """When a TC is accepted, the error code must be 0."""
        engine = _make_engine()
        accepted, code = engine._check_tc_acceptance(17, 1, b'')
        assert accepted
        assert code == 0, "Accepted TCs must return error code 0"

    def test_return_type_is_tuple(self):
        """_check_tc_acceptance must return a 2-tuple."""
        engine = _make_engine()
        result = engine._check_tc_acceptance(17, 1, b'')
        assert isinstance(result, tuple)
        assert len(result) == 2
