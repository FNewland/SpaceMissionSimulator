"""Client-side half of the 2026-08 command-safety defect.

The spacecraft now REFUSES a telecommand whose mandatory argument byte is
absent (tests/test_simulator/test_missing_argument_refusal.py). The console
should not have let the operator build one in the first place: a bare
``TTC_SET_BEACON_MODE`` reaching the uplink at all is a ground-segment miss.

What the audit found:

* the SCHEMA-DRIVEN builder cannot emit a short packet — ``encodeCatalogCommand``
  walks ``cmd.fields`` and encodes one value per declared field, substituting
  ``catFieldDefault`` for a blank box, so the wire always carries the full
  length. Same for the Python ground encoder (``smo_common.commanding.encode``)
  that procedures, OBCPs, the planner and S11 all use;
* the "Data (hex)" box is the hole. It is free text, it is sent verbatim, and
  ``sendCommand`` had NO length gate — only the advisory "0 / 1 B" chip, which
  warns and lets the packet go.

``catLengthError`` closes it, and ``sendCommand`` refuses before the POST.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HARNESS = Path(__file__).resolve().parent / "js" / "modern_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(NODE is None, reason="node required")


def run_js(snippet: str, tmp_path: Path):
    f = tmp_path / "snippet.js"
    f.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(REPO), str(f)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


BEACON = """
const beacon = {
  name: "TTC_SET_BEACON_MODE", service: 8, subtype: 1, func_id: 70,
  allowed: true, expected_data_bytes: 2,
  fields: [{ name: "on", type: "uint8", description: "0=off, 1=on" }],
};
const setpoint = {
  name: "HEATER_SET_SETPOINT", service: 8, subtype: 1, func_id: 44,
  allowed: true, expected_data_bytes: 10,
  fields: [{ name: "circuit", type: "uint8" },
           { name: "on_temp", type: "float32" },
           { name: "off_temp", type: "float32" }],
};
"""


@requires_node
class TestLengthGate:
    def test_bare_func_id_is_a_length_error_naming_the_field(self, tmp_path):
        out = run_js(BEACON + """
            return { bare: T.catLengthError(beacon, "46"),
                     full: T.catLengthError(beacon, "46 00"),
                     empty: T.catLengthError(beacon, "") };
        """, tmp_path)
        assert out["bare"] and "`on`" in out["bare"]
        assert "0=off, 1=on" in out["bare"]
        assert out["empty"], "an empty data field must not be sendable either"
        assert out["full"] is None, "the correct 2-byte form must be sendable"

    def test_multi_field_command_names_the_first_missing_field(self, tmp_path):
        out = run_js(BEACON + """
            return { short: T.catLengthError(setpoint, "2C 00"),
                     full: T.catLengthError(setpoint,
                       "2C 00 00 00 00 00 00 00 00 00") };
        """, tmp_path)
        assert out["short"] and "on_temp" in out["short"]
        assert out["full"] is None

    def test_the_builder_itself_never_produces_a_short_packet(self, tmp_path):
        """A blank field box encodes the schema default, not zero bytes."""
        out = run_js(BEACON + """
            return { blank: T.encodeCatalogCommand(beacon, { on: "" }),
                     unset: T.encodeCatalogCommand(beacon, {}),
                     off: T.encodeCatalogCommand(beacon, { on: 0 }),
                     on: T.encodeCatalogCommand(beacon, { on: 1 }) };
        """, tmp_path)
        # 2 octets in every case: func_id + the `on` byte.
        assert out["blank"] == "46 00" and out["unset"] == "46 00"
        assert out["off"] == "46 00" and out["on"] == "46 01"

    def test_send_command_refuses_and_posts_nothing(self, tmp_path):
        out = run_js(BEACON + """
            T.S.tcCatalog = [beacon];
            T.S.position = "flight_director";
            byId("cmd-catalog").value = "TTC_SET_BEACON_MODE";
            byId("cmd-service").value = "8";
            byId("cmd-subtype").value = "1";
            byId("cmd-name").value = "TTC_SET_BEACON_MODE";
            byId("cmd-data").value = "46";      // argument byte missing
            await T.sendCommand();
            return { posts: __fetchLog.filter((f) => String(f.url || f)
                       .indexOf("pus-command") >= 0).length,
                     result: byId("cmd-result").innerHTML };
        """, tmp_path)
        assert out["posts"] == 0, "a short command was uplinked anyway"
        assert "not sent" in out["result"]
        assert "`on`" in out["result"]

    def test_send_command_still_sends_the_correct_form(self, tmp_path):
        out = run_js(BEACON + """
            T.S.tcCatalog = [beacon];
            T.S.position = "flight_director";
            byId("cmd-catalog").value = "TTC_SET_BEACON_MODE";
            byId("cmd-service").value = "8";
            byId("cmd-subtype").value = "1";
            byId("cmd-name").value = "TTC_SET_BEACON_MODE";
            byId("cmd-data").value = "46 00";   // beacon mode OFF
            await T.sendCommand();
            return { posts: __fetchLog.filter((f) => String(f.url || f)
                       .indexOf("pus-command") >= 0).length };
        """, tmp_path)
        assert out["posts"] == 1
