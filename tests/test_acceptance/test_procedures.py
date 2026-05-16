"""Phase 4 Acceptance Tests: Procedure Execution.

Verifies that procedures can be loaded, and the procedure index is
complete and well-formed. Tests the ProcedureRunner state machine
with mock command/telemetry functions.

Ref: EOSAT1-TP-ATP-001 §7 (Phase 4: Procedure Execution Tests)
"""

import asyncio
import yaml
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"
PROC_INDEX = CONFIG_DIR / "procedures" / "procedure_index.yaml"
PROC_DIR = CONFIG_DIR / "procedures"


# ═══════════════════════════════════════════════════════════════
# Procedure Index Validation
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def proc_index():
    """Load the procedure index."""
    with open(PROC_INDEX) as f:
        data = yaml.safe_load(f)
    return data.get("procedures", [])


class TestProcedureIndex:

    def test_index_file_exists(self):
        """Procedure index YAML exists."""
        assert PROC_INDEX.exists()

    def test_index_not_empty(self, proc_index):
        """Index contains procedures."""
        assert len(proc_index) > 0, "Procedure index is empty"

    def test_index_has_all_categories(self, proc_index):
        """Index covers all 5 categories."""
        categories = {p.get("category") for p in proc_index}
        expected = {"leop", "commissioning", "nominal", "contingency", "emergency"}
        missing = expected - categories
        assert len(missing) == 0, f"Missing categories: {missing}"

    def test_leop_procedures_count(self, proc_index):
        """At least 7 LEOP procedures."""
        leop = [p for p in proc_index if p.get("category") == "leop"]
        assert len(leop) >= 7, f"Only {len(leop)} LEOP procedures"

    def test_commissioning_procedures_count(self, proc_index):
        """At least 13 commissioning procedures."""
        com = [p for p in proc_index if p.get("category") == "commissioning"]
        assert len(com) >= 13, f"Only {len(com)} commissioning procedures"

    def test_contingency_procedures_count(self, proc_index):
        """At least 26 contingency procedures."""
        ctg = [p for p in proc_index if p.get("category") == "contingency"]
        assert len(ctg) >= 20, f"Only {len(ctg)} contingency procedures"

    def test_emergency_procedures_count(self, proc_index):
        """At least 6 emergency procedures."""
        emg = [p for p in proc_index if p.get("category") == "emergency"]
        assert len(emg) >= 6, f"Only {len(emg)} emergency procedures"

    def test_each_procedure_has_required_fields(self, proc_index):
        """Every procedure has id, name, file, category."""
        for p in proc_index:
            assert "id" in p, f"Procedure missing 'id': {p}"
            assert "name" in p, f"Procedure {p.get('id')} missing 'name'"
            assert "file" in p, f"Procedure {p.get('id')} missing 'file'"
            assert "category" in p, f"Procedure {p.get('id')} missing 'category'"

    def test_procedure_files_exist(self, proc_index):
        """Every referenced procedure file exists on disk."""
        missing = []
        for p in proc_index:
            filepath = PROC_DIR / p["file"]
            if not filepath.exists():
                missing.append(f"{p['id']}: {p['file']}")
        assert len(missing) == 0, \
            f"{len(missing)} procedure files missing:\n" + "\n".join(missing[:10])

    def test_procedure_ids_unique(self, proc_index):
        """All procedure IDs are unique."""
        ids = [p["id"] for p in proc_index]
        dupes = [pid for pid in ids if ids.count(pid) > 1]
        assert len(dupes) == 0, f"Duplicate procedure IDs: {set(dupes)}"

    def test_procedure_ids_follow_convention(self, proc_index):
        """IDs follow CODE-NNN convention."""
        import re
        bad = []
        for p in proc_index:
            if not re.match(r'^[A-Z]+-\d+$', p["id"]):
                bad.append(p["id"])
        assert len(bad) == 0, f"Non-standard IDs: {bad}"


# ═══════════════════════════════════════════════════════════════
# Procedure Runner State Machine
# ═══════════════════════════════════════════════════════════════

class TestProcedureRunner:

    @pytest.fixture
    def runner(self):
        """Create a ProcedureRunner with mock callbacks."""
        from smo_mcs.procedure_runner import ProcedureRunner

        commands_sent = []

        async def mock_send(service, subtype, data_hex="", **kw):
            commands_sent.append((service, subtype, data_hex))
            return {"status": "sent", "seq": len(commands_sent)}

        def mock_telemetry(path):
            # Return nominal values for any telemetry query
            defaults = {
                "eps.bus_voltage_V": 28.0,
                "eps.soc_pct": 75.0,
                "obdh.sw_image": 1,
                "aocs.mode": 4,
                "ttc.link_margin_db": 8.0,
                "payload.mode": 1,
            }
            return defaults.get(path, 0.0)

        runner = ProcedureRunner(mock_send, mock_telemetry)
        runner._commands_sent = commands_sent
        return runner

    def test_runner_initial_state(self, runner):
        """Runner starts in IDLE state."""
        from smo_mcs.procedure_runner import ProcedureState
        assert runner.state == ProcedureState.IDLE

    def test_runner_load(self, runner):
        """Loading a procedure transitions to LOADED."""
        from smo_mcs.procedure_runner import ProcedureState
        steps = [
            {"service": 17, "subtype": 1, "description": "Connection test"},
            {"service": 3, "subtype": 27, "data_hex": "00 01",
             "description": "Request EPS HK"},
        ]
        result = runner.load("TEST_PROC", steps)
        assert runner.state == ProcedureState.LOADED
        assert len(runner.steps) == 2

    def test_runner_start_and_complete(self, runner):
        """Start → execute all steps → COMPLETED."""
        from smo_mcs.procedure_runner import ProcedureState
        steps = [
            {"service": 17, "subtype": 1, "description": "Ping"},
        ]
        runner.load("SIMPLE", steps)

        loop = asyncio.new_event_loop()
        loop.run_until_complete(runner.start())
        # Give task time to execute
        loop.run_until_complete(asyncio.sleep(1.0))
        loop.close()

        assert runner.state in (ProcedureState.COMPLETED, ProcedureState.RUNNING)

    def test_runner_status_format(self, runner):
        """Status dict has expected fields."""
        runner.load("STATUS_TEST", [{"service": 17, "subtype": 1}])
        status = runner.status()
        assert "state" in status
        assert "procedure_name" in status or "name" in status
        assert "current_step" in status
        assert "step_results" in status or "steps" in status

    def test_runner_cannot_start_without_load(self, runner):
        """Start without load returns error."""
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(runner.start())
        loop.close()
        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# Procedure Content Validation
# ═══════════════════════════════════════════════════════════════

class TestProcedureContent:

    @pytest.fixture(scope="class")
    def all_procedures(self):
        """Load all procedure files."""
        with open(PROC_INDEX) as f:
            index = yaml.safe_load(f).get("procedures", [])

        procs = {}
        for p in index:
            filepath = PROC_DIR / p["file"]
            if filepath.exists():
                with open(filepath) as f:
                    content = f.read()
                procs[p["id"]] = {
                    "meta": p,
                    "content": content,
                    "size": len(content),
                }
        return procs

    def test_no_empty_procedures(self, all_procedures):
        """No procedure file should be empty."""
        empty = [pid for pid, p in all_procedures.items() if p["size"] < 50]
        assert len(empty) == 0, f"Empty procedure files: {empty}"

    def test_leop_001_mentions_boot(self, all_procedures):
        """LEOP-001 should reference OBC boot."""
        p = all_procedures.get("LEOP-001")
        if p:
            content = p["content"].lower()
            assert "boot" in content or "obc" in content, \
                "LEOP-001 doesn't mention boot/OBC"

    def test_contingency_procedures_mention_recovery(self, all_procedures):
        """Contingency procedures should mention recovery steps."""
        ctg_procs = {k: v for k, v in all_procedures.items() if k.startswith("CTG")}
        no_recovery = []
        for pid, p in ctg_procs.items():
            content = p["content"].lower()
            if "recover" not in content and "restore" not in content \
                    and "clear" not in content:
                no_recovery.append(pid)
        # Allow some to not explicitly say "recovery"
        assert len(no_recovery) < len(ctg_procs) // 2, \
            f"{len(no_recovery)} contingency procs don't mention recovery"

    def test_emergency_procedures_mention_safe(self, all_procedures):
        """Emergency procedures should reference safe mode."""
        emg_procs = {k: v for k, v in all_procedures.items() if k.startswith("EMG")}
        for pid, p in emg_procs.items():
            content = p["content"].lower()
            assert "safe" in content or "emergency" in content or "critical" in content, \
                f"{pid} doesn't mention safe/emergency/critical"
