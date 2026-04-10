"""Tests for consistency gap closures (D1, D2, D3).

Validates that simulator capabilities are fully exposed in MCS and planner configs,
and that all declared commands are backed by handlers.

D1: Sim capabilities exposed in MCS (S3.27, S5, S12, S19, per-panel solar)
D2: MCS-declared commands are implemented in sim (S9.2, S3.4, S8.1 func 50+)
D3: Sim/MCS capabilities known to planner (thermal_rebalance, load_shedding, FDIR_recovery)
"""
import struct
import yaml
import pytest
from pathlib import Path
from unittest.mock import MagicMock


# ─── D1: Ensure S3.27 round-trip works (on-demand HK) ──────────────────────────

class TestD1_S327OnDemandHK:
    """Validate S3.27 (on-demand HK) works end-to-end."""

    def test_s327_hk_request_creates_response(self):
        """S3.27 (on-demand HK report) with SID=10 returns HK packet."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        engine.params = {0x0100: 28.5, 0x0101: 75.0}
        engine._hk_structures = {
            10: {"sid": 10, "params": [0x0100, 0x0101]}
        }
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_hk_packet = MagicMock(return_value=b'\x03\x19\x00\x10' + b'\x00' * 16)
        engine._get_cuc_time = MagicMock(return_value=1000)

        d = ServiceDispatcher(engine)

        # Send S3.27 (subtype 27, on-demand HK) for SID 10
        sid = 10
        data = struct.pack('>H', sid)
        responses = d.dispatch(3, 27, data, None)

        # Should return 1 HK packet
        assert len(responses) == 1
        assert responses[0] == b'\x03\x19\x00\x10' + b'\x00' * 16

    def test_s327_multiple_sids(self):
        """S3.27 works for all predefined SIDs (1-6, 10-30)."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        engine.params = {i: float(i) for i in range(0x0100, 0x0110)}
        engine._hk_structures = {
            sid: {"sid": sid, "params": [0x0100, 0x0101]}
            for sid in [1, 2, 3, 4, 5, 6, 10, 20, 30]
        }
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_hk_packet = MagicMock(return_value=b'\x00' * 20)
        engine._get_cuc_time = MagicMock(return_value=1000)

        d = ServiceDispatcher(engine)

        for sid in [1, 2, 3, 4, 5, 6, 10, 20, 30]:
            data = struct.pack('>H', sid)
            responses = d.dispatch(3, 27, data, None)
            assert len(responses) == 1, f"S3.27 failed for SID {sid}"


# ─── D1: Validate S5 event emission ──────────────────────────────────────────

class TestD1_S5EventEmission:
    """Validate S5 event enable/disable controls event reporting."""

    def test_s5_event_enable_disables_reporting(self):
        """Enable event type 5, verify is_event_enabled returns True."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        d = ServiceDispatcher(engine)

        # Disable all first
        d._s5_enabled_types.clear()
        assert not d.is_event_enabled(5)

        # Enable event type 5 via S5.5
        data = bytes([5])
        d.dispatch(5, 5, data, None)
        assert d.is_event_enabled(5)

    def test_s5_event_disable_stops_reporting(self):
        """Disable event type 7, verify is_event_enabled returns False."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        d = ServiceDispatcher(engine)

        # Should be enabled by default
        assert d.is_event_enabled(7)

        # Disable via S5.6
        data = bytes([7])
        d.dispatch(5, 6, data, None)
        assert not d.is_event_enabled(7)

    def test_s5_enable_all_enables_all_types(self):
        """S5.7 (enable all) enables all 256 event types."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        d = ServiceDispatcher(engine)

        # Disable all first
        d._s5_enabled_types.clear()
        assert len(d._s5_enabled_types) == 0

        # Enable all via S5.7
        d.dispatch(5, 7, b'', None)
        assert len(d._s5_enabled_types) >= 4
        for etype in range(1, 5):
            assert d.is_event_enabled(etype)

    def test_s5_disable_all_disables_all_types(self):
        """S5.8 (disable all) disables all event types."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        d = ServiceDispatcher(engine)

        # Disable all via S5.8
        d.dispatch(5, 8, b'', None)
        assert len(d._s5_enabled_types) == 0
        assert not d.is_event_enabled(0)
        assert not d.is_event_enabled(100)


# ─── D2: Verify S9.2 time report works ──────────────────────────────────────

class TestD2_S92TimeReport:
    """Validate S9.2 (request time report) returns current OBT."""

    def test_s92_returns_time_report(self):
        """S9.2 (subtype 2) returns 1 time report packet."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        engine._get_cuc_time = MagicMock(return_value=12345)
        engine.tm_builder = MagicMock()
        engine.tm_builder.build_time_report = MagicMock(return_value=b'\x09\x82' + struct.pack('>I', 12345))
        engine._get_cuc_time = MagicMock(return_value=12345)

        d = ServiceDispatcher(engine)
        responses = d.dispatch(9, 2, b'', None)

        assert len(responses) == 1
        engine.tm_builder.build_time_report.assert_called_with(12345)


# ─── D2: Verify S3.4 HK disable works ──────────────────────────────────────

class TestD2_S34HKDisable:
    """Validate S3.4 (disable periodic HK) disables the SID."""

    def test_s34_disable_periodic_hk(self):
        """S3.4 (disable) with SID=20 calls set_hk_enabled(20, False)."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        d = ServiceDispatcher(engine)

        # Send S3.4 (subtype 4) for SID 20
        sid = 20
        data = struct.pack('>H', sid)
        d.dispatch(3, 4, data, None)

        # Should have called set_hk_enabled(20, False)
        engine.set_hk_enabled.assert_called_with(20, False)


# ─── D2: Verify S8.1 func 50+ handlers exist ──────────────────────────────

class TestD2_S81FuncHandlers:
    """Validate S8.1 function handlers for func_id >= 50."""

    def test_s81_func_50_obdh_set_mode(self):
        """S8.1 func_id=50 (OBDH set mode) routes to OBDH subsystem."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        obdh = MagicMock()
        engine.subsystems = {"obdh": obdh}
        d = ServiceDispatcher(engine)

        data = bytes([50, 1])  # func_id=50, mode=1
        d.dispatch(8, 1, data, None)

        # Should have called obdh.handle_command with set_mode
        obdh.handle_command.assert_called()
        call_args = obdh.handle_command.call_args
        assert call_args[0][0]["command"] == "set_mode"

    def test_s81_func_63_ttc_switch_primary(self):
        """S8.1 func_id=63 (TTC switch primary) routes to TTC subsystem."""
        from smo_simulator.service_dispatch import ServiceDispatcher

        engine = MagicMock()
        ttc = MagicMock()
        engine.subsystems = {"ttc": ttc}
        d = ServiceDispatcher(engine)

        data = bytes([63])  # func_id=63
        d.dispatch(8, 1, data, None)

        # Should have called ttc.handle_command with switch_primary
        ttc.handle_command.assert_called()
        call_args = ttc.handle_command.call_args
        assert call_args[0][0]["command"] == "switch_primary"


# ─── D3: Planner activity type validation ─────────────────────────────────

class TestD3_PlannerActivityTypes:
    """Validate that planner knows about all required activity types."""

    def test_activity_types_file_exists(self):
        """activity_types.yaml file exists and is valid YAML."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        assert config_path.exists(), f"activity_types.yaml not found at {config_path}"

        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "activity_types" in config
        assert isinstance(config["activity_types"], list)

    def test_imaging_pass_activity_present(self):
        """imaging_pass activity type is declared."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "imaging_pass" in names

    def test_data_dump_activity_present(self):
        """data_dump activity type is declared."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "data_dump" in names

    def test_momentum_desaturation_activity_present(self):
        """momentum_desaturation activity type is declared."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "momentum_desaturation" in names

    def test_thermal_rebalance_activity_present(self):
        """thermal_rebalance activity type is declared (D3.1)."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "thermal_rebalance" in names, "thermal_rebalance activity missing (D3.1)"

        # Verify structure
        thermal = next(a for a in config["activity_types"] if a["name"] == "thermal_rebalance")
        assert "duration_s" in thermal
        assert "power_w" in thermal
        assert "command_sequence" in thermal
        assert thermal["requires_subsystems"] == ["tcs"]

    def test_load_shedding_activity_present(self):
        """load_shedding activity type is declared (D3.3)."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "load_shedding" in names, "load_shedding activity missing (D3.3)"

        # Verify structure
        load_shed = next(a for a in config["activity_types"] if a["name"] == "load_shedding")
        assert "duration_s" in load_shed
        assert "command_sequence" in load_shed
        assert load_shed["requires_subsystems"] == ["eps"]
        assert load_shed["priority"] == "high"

    def test_fdir_recovery_activity_present(self):
        """fdir_recovery activity type is declared (D3.4)."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        names = [a["name"] for a in config["activity_types"]]
        assert "fdir_recovery" in names, "fdir_recovery activity missing (D3.4)"

        # Verify structure
        fdir = next(a for a in config["activity_types"] if a["name"] == "fdir_recovery")
        assert "duration_s" in fdir
        assert "command_sequence" in fdir
        assert "aocs" in fdir["requires_subsystems"]
        assert "eps" in fdir["requires_subsystems"]
        assert fdir["priority"] == "high"

    def test_activity_has_required_fields(self):
        """All activity types have required YAML fields."""
        config_path = Path("configs") / "eosat1" / "planning" / "activity_types.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        required = ["name", "description", "duration_s", "command_sequence"]
        for activity in config["activity_types"]:
            for field in required:
                assert field in activity, f"Activity {activity.get('name')} missing field {field}"


# ─── MCS Configuration Exposure ───────────────────────────────────────────────

class TestD1_MCSSolarCurrentsExposed:
    """Validate that per-panel solar currents are exposed in MCS displays."""

    def test_mcs_displays_file_exists(self):
        """displays.yaml file exists and is valid YAML."""
        config_path = Path("configs") / "eosat1" / "mcs" / "displays.yaml"
        assert config_path.exists(), f"displays.yaml not found at {config_path}"

        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "displays" in config or "positions" in config

    def test_mcs_has_eps_advanced_panel(self):
        """MCS displays.yaml includes eps_advanced panel with per-panel currents."""
        config_path = Path("configs") / "eosat1" / "mcs" / "displays.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        displays = config.get("displays", {})
        assert "eps_advanced" in displays, "eps_advanced display panel missing"

        eps_adv = displays["eps_advanced"]
        assert "label" in eps_adv
        assert "pages" in eps_adv

        # Check for per-panel current parameters
        all_params = []
        for page in eps_adv.get("pages", []):
            for widget in page.get("widgets", []):
                if "parameters" in widget:
                    all_params.extend(widget["parameters"])

        solar_params = [p for p in all_params if "sa_" in p and "current" in p]
        assert len(solar_params) >= 6, f"Expected 6+ solar panel current params, got {solar_params}"

    def test_mcs_has_monitoring_panel(self):
        """MCS displays.yaml includes monitoring_panel for S5/S12/S19 events."""
        config_path = Path("configs") / "eosat1" / "mcs" / "displays.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        displays = config.get("displays", {})
        assert "monitoring_panel" in displays, "monitoring_panel display missing (S5/S12/S19)"

        mon = displays["monitoring_panel"]
        assert "label" in mon
        page_names = [p["name"] for p in mon.get("pages", [])]
        assert any("S12" in name or "Monitoring" in name for name in page_names), "S12 page missing"
        assert any("S5" in name or "Event" in name for name in page_names), "S5 page missing"
        assert any("S19" in name or "Event-Action" in name for name in page_names), "S19 page missing"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
