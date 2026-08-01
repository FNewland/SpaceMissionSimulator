"""Defect #88 — every failure code the spacecraft emits must have a meaning.

The console can only show "0x0007 — Not permitted in the current state" if the
code is in the one shared vocabulary
(``smo_common.protocol.pus_services.FAILURE_CODES``, served to the browser by
``GET /api/failure-codes``). A code that the on-board software emits but the
vocabulary does not describe reaches the operator as a bare number — which is
the whole complaint.

These are content tests: they read the shipped configuration and the simulator
source rather than exercising a display, so a new code, a renamed event or a
dropped HK entry fails here rather than silently degrading a console.
"""
import ast
import re
from pathlib import Path

import pytest
import yaml

from smo_common.protocol.pus_services import (
    COMMAND_REJECTED_EVENT_ID, FAILURE_CODES, MEMORY_FAILURE_CODES,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
SIM = REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
DISPATCH = SIM / "service_dispatch.py"
ENGINE = SIM / "engine.py"
MODELS = sorted((SIM / "models").glob("*.py"))


def _emitted_codes(path: Path) -> set[int]:
    """Integer literals assigned to an ``error_code`` key or argument."""
    codes: set[int] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        # {"error_code": 0x0007}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "error_code"
                        and isinstance(v, ast.Constant)
                        and isinstance(v.value, int)):
                    codes.add(v.value)
        # error_code=0x0007
        if isinstance(node, ast.keyword) and node.arg == "error_code" \
                and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, int):
            codes.add(node.value.value)
    return codes


class TestVocabularyCoversWhatIsEmitted:

    def test_every_error_code_in_the_models_has_a_meaning(self):
        unknown = {}
        for path in MODELS:
            for code in _emitted_codes(path):
                if code not in FAILURE_CODES:
                    unknown.setdefault(path.name, set()).add(f"0x{code:04X}")
        assert not unknown, (
            "a subsystem model emits a failure code with no entry in "
            f"FAILURE_CODES, so the console can only show the number: {unknown}")

    def test_every_error_code_in_the_dispatcher_has_a_meaning(self):
        unknown = {f"0x{c:04X}" for c in _emitted_codes(DISPATCH)
                   if c not in FAILURE_CODES}
        assert not unknown, f"dispatcher emits undocumented failure codes: {unknown}"

    def test_the_dispatchers_memory_error_constants_match_the_s6_vocabulary(self):
        """``ERR_*`` in memory_map are the S6 reading of the low codes."""
        from smo_simulator import memory_map
        for name in dir(memory_map):
            if not name.startswith("ERR_"):
                continue
            value = getattr(memory_map, name)
            if not isinstance(value, int) or value == 0:
                continue
            assert value in MEMORY_FAILURE_CODES, (
                f"memory_map.{name} = 0x{value:04X} has no entry in "
                f"MEMORY_FAILURE_CODES, so an S6 refusal shows a bare number")

    def test_no_code_is_described_twice_with_different_words(self):
        """The S6 readings must be a deliberate override, not a duplicate."""
        for code, text in MEMORY_FAILURE_CODES.items():
            assert code in FAILURE_CODES, \
                f"0x{code:04X} has an S6 meaning but no general one"
            assert text != FAILURE_CODES[code], \
                f"0x{code:04X} has the same text in both maps — drop one"


@pytest.fixture(scope="module")
def events():
    data = yaml.safe_load((CONFIG / "events" / "event_catalog.yaml").read_text())
    rows = data["events"] if isinstance(data, dict) else data
    return {int(e["id"]): e for e in rows}


@pytest.fixture(scope="module")
def parameters():
    data = yaml.safe_load((CONFIG / "telemetry" / "parameters.yaml").read_text())
    rows = data["parameters"] if isinstance(data, dict) else data
    return {int(p["id"]): p for p in rows}


@pytest.fixture(scope="module")
def structures():
    data = yaml.safe_load((CONFIG / "telemetry" / "hk_structures.yaml").read_text())
    return data["structures"] if isinstance(data, dict) else data


class TestTheRejectionEventIsCatalogued:

    def test_command_rejected_exists_with_the_shared_id(self, events):
        evt = events.get(COMMAND_REJECTED_EVENT_ID)
        assert evt, "0x030B COMMAND_REJECTED is not in event_catalog.yaml"
        assert evt["name"] == "COMMAND_REJECTED"
        assert evt["subsystem"] == "obdh"

    def test_the_catalogue_entry_says_the_aux_text_carries_the_code(self, events):
        desc = events[COMMAND_REJECTED_EVENT_ID]["description"].lower()
        assert "aux" in desc and "code" in desc, (
            "the entry must tell a reader that the auxiliary text carries the "
            "failure code and the reason — that is the whole point of it")


class TestTheLastFailureCodeIsTelemetry:

    def test_the_parameter_exists(self, parameters):
        p = parameters.get(0x0315)
        assert p, "0x0315 obdh.last_tc_error_code is not declared"
        assert p["name"] == "obdh.last_tc_error_code"
        assert p["subsystem"] == "obdh"
        assert "failure code" in p["description"].lower()

    def test_it_is_downlinked_periodically_with_the_counter_it_explains(
            self, structures):
        sid4 = next(s for s in structures if int(s["sid"]) == 4)
        ids = [int(p["param_id"]) for p in sid4["parameters"]]
        assert 0x0315 in ids, \
            "the last failure code is in no HK packet — it would never arrive"
        assert 0x0306 in ids, "the rejection counter should still be there too"
        packing = next(p for p in sid4["parameters"]
                       if int(p["param_id"]) == 0x0315)
        assert packing["pack_format"] == "H", \
            "a failure code is 16 bits on the wire"
        assert packing.get("scale", 1) == 1, \
            "a code must not be scaled — 0x0007 is not 7/100"

    def test_the_id_is_not_reused(self, parameters):
        names = [p["name"] for p in parameters.values()]
        assert len(names) == len(set(names)), "duplicate parameter name"

    def test_the_obdh_model_publishes_that_exact_id(self):
        src = (SIM / "models" / "obdh_basic.py").read_text()
        assert re.search(r'last_tc_error_code["\']?,\s*0x0315', src), \
            "the model's default param id must match parameters.yaml"


class TestTheRejectionTextFormatIsShared:
    """The spacecraft writes the aux text and the ground segment reads it."""

    def test_the_engine_uses_the_shared_formatter(self):
        src = ENGINE.read_text()
        assert "format_command_rejection(" in src
        assert re.search(r'err 0x\{[^}]*\}', src) is None, (
            "the rejection text must be built by "
            "pus_services.format_command_rejection, not formatted inline")

    def test_the_ground_segment_uses_the_shared_parser(self):
        src = (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs"
               / "server.py").read_text()
        assert "parse_command_rejection(" in src
        assert re.search(r'err 0x\(', src) is None, \
            "the MCS must not carry its own regex for the rejection text"
