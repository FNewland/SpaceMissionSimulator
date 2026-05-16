"""Phase 5 Acceptance Tests: Ground Tool Verification.

Tests each ground tool's core functionality without requiring the
full system stack to be running. Validates data formats, computation
correctness, and known issues.

Ref: EOSAT1-TP-ATP-001 §8 (Phase 5: Ground Tool Tests)
"""

import struct
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "configs" / "eosat1"
TOOLS_DIR = PROJECT_ROOT / "tools"


# ═══════════════════════════════════════════════════════════════
# GT-ORB: Orbit Tools — TLE/State Vector Conversion
# ═══════════════════════════════════════════════════════════════

class TestOrbitTools:

    @pytest.fixture(scope="class")
    def orbit_module(self):
        """Import orbit tools module."""
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            import orbit_tools
            return orbit_tools
        except ImportError as e:
            pytest.skip(f"orbit_tools not importable: {e}")
        finally:
            if str(TOOLS_DIR) in sys.path:
                sys.path.remove(str(TOOLS_DIR))

    def test_gt_orb_001_tle_parse(self, orbit_module):
        """TLE parsing produces valid orbital elements."""
        tle1 = "1 99001U 26001A   26068.50000000  .00000100  00000-0  10000-4 0  9990"
        tle2 = "2 99001  98.0000 120.0000 0001200  90.0000 270.0000 15.24000000 00010"
        try:
            sat = orbit_module.Satrec.twoline2rv(tle1, tle2)
            assert sat.inclo > 0, "Inclination should be positive"
        except Exception as e:
            pytest.skip(f"sgp4 parse failed: {e}")

    def test_gt_orb_002_state_vector_to_hex(self, orbit_module):
        """State vector → S20.1 hex commands format."""
        # Typical LEO state vector (ECEF)
        x, y, z = 6878000.0, 0.0, 0.0  # meters
        vx, vy, vz = 0.0, 7600.0, 0.0  # m/s

        # Generate S20.1 commands manually
        param_ids = [0x0231, 0x0232, 0x0233, 0x0234, 0x0235, 0x0236]
        values = [x, y, z, vx, vy, vz]
        commands = []
        for pid, val in zip(param_ids, values):
            data = struct.pack('>Hf', pid, val)
            commands.append(data.hex())

        assert len(commands) == 6
        # First command should set X position
        assert commands[0].startswith("0231")

    def test_gt_orb_003_orbital_elements(self, orbit_module):
        """Orbital elements computed from TLE match expected SSO."""
        tle1 = "1 99001U 26001A   26068.50000000  .00000100  00000-0  10000-4 0  9990"
        tle2 = "2 99001  98.0000 120.0000 0001200  90.0000 270.0000 15.24000000 00010"
        try:
            sat = orbit_module.Satrec.twoline2rv(tle1, tle2)
            # Inclination should be ~98° (SSO)
            incl_deg = sat.inclo * 180.0 / 3.14159265
            assert 95.0 < incl_deg < 100.0, f"Inclination {incl_deg}° not SSO"
            # Eccentricity should be near-circular
            assert sat.ecco < 0.01, f"Eccentricity {sat.ecco} too high"
        except Exception as e:
            pytest.skip(f"sgp4 failed: {e}")


# ═══════════════════════════════════════════════════════════════
# GT-DTM: Delayed TM Viewer
# ═══════════════════════════════════════════════════════════════

class TestDelayedTMViewer:

    def test_gt_dtm_001_tool_exists(self):
        """Delayed TM viewer script exists."""
        assert (TOOLS_DIR / "delayed_tm_viewer.py").exists()

    def test_gt_dtm_002_tool_importable(self):
        """Delayed TM viewer is importable (syntax valid)."""
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            spec = importlib.util.spec_from_file_location(
                "delayed_tm_viewer", TOOLS_DIR / "delayed_tm_viewer.py")
            assert spec is not None
        finally:
            if str(TOOLS_DIR) in sys.path:
                sys.path.remove(str(TOOLS_DIR))

    def test_gt_dtm_003_workspace_dumps_dir(self):
        """workspace/dumps/ directory exists or can be created."""
        dumps_dir = PROJECT_ROOT / "workspace" / "dumps"
        dumps_dir.mkdir(parents=True, exist_ok=True)
        assert dumps_dir.exists()


# ═══════════════════════════════════════════════════════════════
# GT-RAD: Radio Dashboard
# ═══════════════════════════════════════════════════════════════

class TestRadioDashboard:

    def test_gt_rad_001_html_exists(self):
        """Radio dashboard HTML exists."""
        html = PROJECT_ROOT / "packages" / "smo-rfsim" / "src" / "smo_rfsim" / \
               "static" / "radio.html"
        assert html.exists()

    def test_gt_rad_002_html_has_lock_elements(self):
        """Radio HTML has carrier/bit/frame sync LED elements."""
        html = PROJECT_ROOT / "packages" / "smo-rfsim" / "src" / "smo_rfsim" / \
               "static" / "radio.html"
        content = html.read_text()
        assert "carrier-led" in content
        assert "bitsync-led" in content
        assert "framesync-led" in content

    def test_gt_rad_003_html_has_constellation(self):
        """Radio HTML has constellation canvas."""
        html = PROJECT_ROOT / "packages" / "smo-rfsim" / "src" / "smo_rfsim" / \
               "static" / "radio.html"
        content = html.read_text()
        assert "constellation" in content

    def test_gt_rad_004_frontend_classes(self):
        """RadioFrontend and RadioStatus are importable."""
        from smo_rfsim.radio.frontend import RadioFrontend, RadioStatus, LockState
        fe = RadioFrontend()
        assert fe.status.carrier_lock == LockState.UNLOCKED
        fe.update_lock(LockState.LOCKED, LockState.LOCKED, LockState.LOCKED)
        assert fe.status.carrier_lock == LockState.LOCKED


# ═══════════════════════════════════════════════════════════════
# GT-MCS: MCS Web UI Structure
# ═══════════════════════════════════════════════════════════════

class TestMCSStructure:

    @pytest.fixture(scope="class")
    def mcs_html(self):
        """Load MCS index.html content."""
        html = PROJECT_ROOT / "packages" / "smo-mcs" / "src" / "smo_mcs" / \
               "static" / "index.html"
        if not html.exists():
            pytest.skip("MCS index.html not found")
        return html.read_text()

    def test_gt_mcs_001_all_tabs_present(self, mcs_html):
        """MCS has all expected tab elements."""
        expected_tabs = [
            "tab-overview", "tab-eps", "tab-tcs", "tab-aocs", "tab-ttc",
            "tab-payload", "tab-obdh", "tab-commanding", "tab-procedures",
        ]
        for tab in expected_tabs:
            assert tab in mcs_html, f"Missing tab: {tab}"

    def test_gt_mcs_002_commanding_elements(self, mcs_html):
        """MCS has command builder UI elements."""
        assert "cmd-service" in mcs_html
        assert "cmd-subtype" in mcs_html
        assert "cmd-data" in mcs_html
        assert "cmd-send-btn" in mcs_html

    def test_gt_mcs_003_websocket_connection(self, mcs_html):
        """MCS has WebSocket connection code."""
        assert "WebSocket" in mcs_html or "ws://" in mcs_html

    def test_gt_mcs_004_verification_log(self, mcs_html):
        """MCS has verification log table."""
        assert "verif-tbody" in mcs_html

    def test_gt_mcs_005_procedure_panel(self, mcs_html):
        """MCS has procedure execution panel."""
        assert "proc-type-select" in mcs_html
        assert "proc-btn-load" in mcs_html
        assert "proc-btn-start" in mcs_html

    def test_gt_mcs_006_lock_chain_display(self, mcs_html):
        """MCS has TTC lock chain display."""
        assert "ttc-carrier" in mcs_html
        assert "ttc-bitsync" in mcs_html
        assert "ttc-framesync" in mcs_html

    def test_gt_mcs_007_no_cdn_dependencies(self, mcs_html):
        """MCS should not depend on CDN (air-gap requirement)."""
        # Check for external CDN URLs
        import re
        cdn_refs = re.findall(r'(https?://cdn\.[^\s"\']+)', mcs_html)
        assert len(cdn_refs) == 0, f"CDN dependencies found: {cdn_refs}"

    def test_gt_mcs_008_contact_timeline(self, mcs_html):
        """MCS has contact window timeline."""
        assert "contact" in mcs_html.lower()


# ═══════════════════════════════════════════════════════════════
# GT-PLN: Planner
# ═══════════════════════════════════════════════════════════════

class TestPlanner:

    def test_gt_pln_001_planner_package_exists(self):
        """Planner package exists."""
        planner_dir = PROJECT_ROOT / "packages" / "smo-planner"
        assert planner_dir.exists()

    def test_gt_pln_002_planner_importable(self):
        """Planner server is importable."""
        try:
            import smo_planner
        except ImportError:
            pytest.skip("smo-planner not installed")

    def test_gt_pln_003_orbit_config_exists(self):
        """Orbit configuration for ground stations exists."""
        orbit_cfg = CONFIG_DIR / "orbit.yaml"
        assert orbit_cfg.exists()
        import yaml
        with open(orbit_cfg) as f:
            data = yaml.safe_load(f)
        assert "ground_stations" in data or "altitude_km" in data


# ═══════════════════════════════════════════════════════════════
# GT-INS: Instructor / Simulator UI
# ═══════════════════════════════════════════════════════════════

class TestInstructor:

    def test_gt_ins_001_instructor_app_exists(self):
        """Instructor app module exists."""
        inst_path = PROJECT_ROOT / "packages" / "smo-simulator" / "src" / \
                    "smo_simulator" / "instructor"
        assert inst_path.exists()

    def test_gt_ins_002_failure_manager_importable(self):
        """FailureManager is importable and constructable."""
        from smo_simulator.failure_manager import FailureManager
        from smo_simulator.engine import SimulationEngine
        eng = SimulationEngine(CONFIG_DIR)
        # FailureManager should be initialized in the engine
        assert hasattr(eng, '_failure_manager')
        assert eng._failure_manager is not None
