"""Phase E WP-4 — the MCS command builder's typed argument fields.

The S8.1 escape hatch (`catalogFieldDefs` returning null so every S8.1
function fell back to a free-text hex box) is gone: selecting a function
renders that command's catalogue `fields[]` — engineering-unit number
inputs, enum dropdowns, bitfield checkboxes — with a live RAW hex preview
and reverse decode on paste. Verified by running the page's own inline JS
under node (tests/test_mcs/js/mcs_harness.js).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from smo_common.commanding import load_catalog
from smo_mcs.tc_lengths import annotate_command

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"
CATALOG = load_catalog(REPO / "configs" / "eosat1")

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")

# The page receives /api/tc-catalog commands WITH the server-derived length
# annotations (expected_data_bytes etc.) — mirror that here.
_ANNOTATED = []
for _c in CATALOG.commands:
    _d = dict(_c)
    annotate_command(_d)
    _ANNOTATED.append(_d)
COMMANDS_JSON = json.dumps(_ANNOTATED)


def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(f"tcCatalog = {COMMANDS_JSON};\n" + snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(INDEX), str(snippet_file)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@requires_node
class TestTypedFieldsReplaceTheHexBox:

    def test_s8_1_no_longer_short_circuits_to_null(self, tmp_path):
        out = run_js("""
          const d36 = catalogFieldDefs(8, 1, undefined, 36);
          const d68 = catalogFieldDefs(8, 1, undefined, 68);
          const d33 = catalogFieldDefs(8, 1, undefined, 33);
          return {
            d36_null: d36 === null,
            d36_label: d36 && d36.label,
            d36_types: d36 && d36.fields.map(f => f.type),
            d68_types: d68 && d68.fields.map(f => f.type),
            d33_types: d33 && d33.fields.map(f => f.type),
          };
        """, tmp_path)
        assert out["d36_null"] is False
        assert out["d36_label"] == "PAYLOAD_COOLER_SETPOINT"
        # func picker + an ENGINEERING number input — NOT a hex box
        assert out["d36_types"] == ["func_select", "eng_number"]
        assert out["d68_types"] == ["func_select", "select"]      # enum dropdown
        assert out["d33_types"] == ["func_select", "bitfield"]    # checkboxes

    def test_no_s8_1_command_renders_a_hex_bytes_field(self, tmp_path):
        out = run_js("""
          const offenders = [];
          for (const c of tcCatalog) {
            if (Number(c.service) !== 8 || Number(c.subtype) !== 1) continue;
            const d = catalogFieldDefs(8, 1, undefined, c.func_id);
            for (const f of (d ? d.fields : [])) {
              if (f.type === 'hex_bytes') offenders.push(c.name + '.' + f.name);
            }
          }
          return offenders;
        """, tmp_path)
        assert out == [], f"S8.1 commands still rendering free-text hex: {out}"

    def test_typing_minus_15_shows_raw_24_64_D7(self, tmp_path):
        out = run_js("""
          CMD_BUILDERS['cmd'] = { prefix: 'cmd', _funcId: 36 };
          document.getElementById('cmd-service').value = '8';
          document.getElementById('cmd-subtype').value = '1';
          updateStructuredFields('cmd');
          // the operator types the ENGINEERING value
          document.getElementById('sf-setpoint').value = '-15';
          refreshSchemaPreview('cmd');
          return {
            data: document.getElementById('cmd-data').value,
            preview: document.getElementById('cmd-raw-preview').textContent,
            errors: document.getElementById('cmd-field-errors').textContent,
            provenance: document.getElementById('cmd-struct-label').textContent,
          };
        """, tmp_path)
        assert out["data"] == "24 64 D7"
        assert "RAW" in out["preview"] and "24 64 D7" in out["preview"]
        assert out["errors"] == ""
        # provenance line: func id, hex, byte count, criticality, readback
        assert "S8.1 func 36 (0x24)" in out["provenance"]
        assert "3 data bytes" in out["provenance"]
        assert "CAUTION" in out["provenance"]
        assert "0x0631" in out["provenance"]

    def test_out_of_range_value_is_flagged_but_not_blocked(self, tmp_path):
        out = run_js("""
          CMD_BUILDERS['cmd'] = { prefix: 'cmd', _funcId: 36 };
          document.getElementById('cmd-service').value = '8';
          document.getElementById('cmd-subtype').value = '1';
          updateStructuredFields('cmd');
          document.getElementById('sf-setpoint').value = '-65';
          refreshSchemaPreview('cmd');
          return {
            data: document.getElementById('cmd-data').value,
            errors: document.getElementById('cmd-field-errors').textContent,
            border: document.getElementById('sf-setpoint').style.borderColor,
          };
        """, tmp_path)
        # -65 C still ENCODES (fault-injection training) — 0x514F, positive,
        # no two's complement — but the field is flagged red with the range.
        assert out["data"] == "24 51 4F"
        assert "-20" in out["errors"] and "REJECT" in out["errors"]
        assert out["border"] != ""

    def test_pasting_hex_back_populates_engineering_values(self, tmp_path):
        out = run_js("""
          CMD_BUILDERS['cmd'] = { prefix: 'cmd', _funcId: 26 };  // wrong func on purpose
          document.getElementById('cmd-service').value = '8';
          document.getElementById('cmd-subtype').value = '1';
          updateStructuredFields('cmd');
          // operator pastes a COOLER_SETPOINT wire form
          document.getElementById('cmd-data').value = '24 64 D7';
          reverseDecodeIntoFields('cmd');
          return {
            switched_to: CMD_BUILDERS['cmd']._funcId,
            setpoint: document.getElementById('sf-setpoint').value,
            badge: document.getElementById('cmd-raw-preview').textContent,
          };
        """, tmp_path)
        # the builder auto-switched to the command octet 0 identifies...
        assert out["switched_to"] == 36
        # ...and back-populated the ENGINEERING value
        assert out["setpoint"] == "-15.00"
        assert "DECODED" in out["badge"]

    def test_paste_decode_capture(self, tmp_path):
        out = run_js("""
          CMD_BUILDERS['cmd'] = { prefix: 'cmd', _funcId: 28 };
          document.getElementById('cmd-service').value = '8';
          document.getElementById('cmd-subtype').value = '1';
          updateStructuredFields('cmd');
          document.getElementById('cmd-data').value = '1C 04 12 08 00';
          reverseDecodeIntoFields('cmd');
          return {
            scene: document.getElementById('sf-scene_id').value,
            lines: document.getElementById('sf-lines').value,
          };
        """, tmp_path)
        assert out["scene"] == "1042"
        assert out["lines"] == "2048"

    def test_quick_command_buttons_are_schema_driven(self, tmp_path):
        out = run_js("""
          tcQuickCommands = [
            { group: 'PAYLOAD', label: 'Cooler -15 C',
              command: 'PAYLOAD_COOLER_SETPOINT', name: 'PLD_COOLER_M15',
              args: { setpoint: -15.0 } },
          ];
          renderQuickCmds();
          const body = document.getElementById('quick-cmds-body');
          return { html: body.innerHTML };
        """, tmp_path)
        assert 'data-cmd="PAYLOAD_COOLER_SETPOINT"' in out["html"]
        assert "data-args=" in out["html"]
        assert "data-d=" not in out["html"], \
            "quick commands must not carry hand-written byte literals"


class TestPageCarriesNoByteLiterals:
    def test_no_data_d_literals_anywhere(self):
        assert 'data-d="' not in INDEX.read_text(), \
            "a hand-written quick-command byte literal crept back in"
