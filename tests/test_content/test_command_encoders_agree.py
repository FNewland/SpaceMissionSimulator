"""Phase E WP-6 (brief 04 drift gate 3) — cross-encoder agreement.

Encode the SAME engineering-value dict through every ground-side encoder and
assert byte-identical output:

  1. the shared Python encoder (``smo_common.commanding.encode``) — the
     reference;
  2. the MCS procedure runner (``ProcedureRunner._build_data_hex`` with
     named ``args`` — also the S11 embedder's and the planner expander's
     code path, they call the same static method);
  3. the OBCP boot loader (``OBCPManager.load_from_dir`` with ``args:``);
  4. the MCS browser builder's JS encoder (``encodeCatalogCommand``), run
     under node through the page harness.

This is the test that would have caught the 0x32/2.0 W TX-power bug on day
one: a consumer that encodes "the same command" differently fails here
byte-for-byte.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from smo_common.commanding import encode, load_catalog

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = REPO / "tests" / "test_mcs" / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")

CATALOG = load_catalog(CONFIG_DIR)

#: The agreement table: (command name, engineering args). Covers the four
#: brief-04 worked examples, every re-encoded payload command, the TX power
#: levels, and a spread of AOCS / PROP / EPS / S2 / S3 forms.
AGREEMENT_TABLE: list[tuple[str, dict]] = [
    ("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0}),
    ("PAYLOAD_COOLER_SETPOINT", {"setpoint": -10.0}),
    ("PAYLOAD_CAPTURE", {"scene_id": 1042, "lines": 2048}),
    ("PAYLOAD_CAPTURE", {"scene_id": 0, "lines": 512}),
    ("TTC_SET_TX_POWER", {"level": 0}),
    ("TTC_SET_TX_POWER", {"level": 2}),
    ("PAYLOAD_SET_BAND_CONFIG", {"mask": 0x07}),
    ("PAYLOAD_SET_BAND_CONFIG", {"mask": 0x0F}),
    ("PAYLOAD_SET_GAIN", {"gain": 2.0}),
    ("PAYLOAD_SET_GAIN", {"gain": 0.1}),
    ("PAYLOAD_SET_INTEGRATION_TIME",
     {"blue_ms": 8.0, "green_ms": 12.0, "red_ms": 16.0, "nir_ms": 20.0}),
    ("PAYLOAD_SET_COMPRESSION", {"algorithm": 3, "ratio": 0.0}),
    ("PAYLOAD_SET_COMPRESSION", {"algorithm": 1, "ratio": 2.5}),
    ("PAYLOAD_SET_MODE", {"mode": 2}),
    ("PAYLOAD_MARK_BAD_SEGMENT", {"segment_id": 3}),
    ("PAYLOAD_SELECT_FILTER", {"position": 2}),
    ("PAYLOAD_CYCLE_SHUTTER", {"cycles": 5}),
    ("PAYLOAD_INITIATE_TRANSFER", {"scene_id": 1042}),
    ("PAYLOAD_CAL_LAMP", {"on": 1}),
    ("PAYLOAD_START_CALIBRATION", {"mode": 0}),
    ("TTC_SET_DATA_RATE", {"rate": 1}),
    ("AOCS_SET_MODE", {"mode": 5}),
    ("AOCS_SET_MOM_MGMT", {"enable": 1, "entry_pct": 70, "exit_pct": 20}),
    ("AOCS_SET_TARGET_GEO", {"lat": 45.5, "lon": -73.6, "alt_m": 0.0}),
    ("EPS_SOLAR_ARRAY_DRIVE", {"angle_deg": -4500}),
    ("PROP_SET_BURN", {"dv_m_s": 0.85, "direction": 0, "mode": 1,
                       "t_start_cuc": 0}),
    ("PROP_ARM", {"arm_code": 0xA5C3}),
    ("HEATER_SET_SETPOINT", {"circuit": 0, "on_temp": 5.0, "off_temp": 10.0}),
    ("S2_PAYLOAD_COOLER", {"device_id": 0x0601, "state": 1}),
    ("HK_REQUEST", {"sid": 5}),
]

REFERENCE = {  # name+args-json -> hex, computed once from the Python encoder
    (name, json.dumps(args, sort_keys=True)):
        encode(name, args, catalog=CATALOG).hex()
    for name, args in AGREEMENT_TABLE
}


def _ids():
    return [f"{n}:{json.dumps(a, sort_keys=True)}" for n, a in AGREEMENT_TABLE]


class TestPythonEncoderIsSelfConsistent:
    @pytest.mark.parametrize("name,args", AGREEMENT_TABLE, ids=_ids())
    def test_encode_is_deterministic(self, name, args):
        assert encode(name, args, catalog=CATALOG).hex() == \
            REFERENCE[(name, json.dumps(args, sort_keys=True))]


class TestProcedureRunnerAgrees:
    """Path 2 — also the S11 embedder and the planner activity expander."""

    @pytest.mark.parametrize("name,args", AGREEMENT_TABLE, ids=_ids())
    def test_build_data_hex_matches(self, name, args, monkeypatch):
        monkeypatch.setenv("SMO_CONFIG_DIR", str(CONFIG_DIR))
        from smo_mcs.procedure_runner import ProcedureRunner
        cmd = CATALOG.get(name)
        step = {"service": cmd["service"], "subtype": cmd["subtype"],
                "args": dict(args), "description": name}
        if cmd.get("func_id") is not None:
            step["func_id"] = cmd["func_id"]
        else:
            step["command"] = name
        got = ProcedureRunner._build_data_hex(step)
        assert got == REFERENCE[(name, json.dumps(args, sort_keys=True))], \
            f"{name}: procedure runner produced {got}"


class TestObcpLoaderAgrees:
    """Path 3 — the OBCP boot loader's ``args:`` support."""

    def test_obcp_steps_encode_identically(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMO_CONFIG_DIR", str(CONFIG_DIR))
        from smo_simulator.obcp import ObcpManager
        from smo_common.protocol.ecss_packet import decommutate_packet

        # S8.1 commands only — the loader keys on (service, subtype, func_id).
        s8 = [(n, a) for n, a in AGREEMENT_TABLE
              if CATALOG.get(n).get("func_id") is not None]
        steps = []
        for name, args in s8:
            cmd = CATALOG.get(name)
            steps.append({"delay_s": 0, "service": 8, "subtype": 1,
                          "func_id": cmd["func_id"], "args": dict(args)})
        (tmp_path / "99_agreement.yaml").write_text(
            "procedure:\n  id: 99\n  name: agreement\n  steps:\n" +
            "".join(
                f"    - {json.dumps(s)}\n" for s in steps))
        mgr = ObcpManager(engine=None)
        assert mgr.load_from_dir(tmp_path) == 1
        proc = mgr._procedures[99]
        for (name, args), step in zip(s8, proc["steps"]):
            pkt = decommutate_packet(step["tc"])
            assert pkt.data_field.hex() == \
                REFERENCE[(name, json.dumps(args, sort_keys=True))], \
                f"{name}: OBCP loader produced {pkt.data_field.hex()}"

    def test_contradicting_hand_written_data_hex_is_refused(self, tmp_path,
                                                            monkeypatch):
        """Drift gate 4: a hand-written data_hex that disagrees with the
        encoded args must fail the load, not silently win."""
        monkeypatch.setenv("SMO_CONFIG_DIR", str(CONFIG_DIR))
        from smo_simulator.obcp import ObcpManager
        (tmp_path / "98_bad.yaml").write_text(
            "procedure:\n  id: 98\n  name: bad\n  steps:\n"
            "    - {delay_s: 0, service: 8, subtype: 1, func_id: 28,\n"
            "       args: {scene_id: 1042, lines: 2048},\n"
            "       data_hex: \"42360000c2933333\"}\n")
        mgr = ObcpManager(engine=None)
        assert mgr.load_from_dir(tmp_path) == 0


@requires_node
class TestJsBuilderAgrees:
    """Path 4 — the browser's mirrored encoder, run under node."""

    def test_js_encoder_matches_python(self, tmp_path):
        table = [
            {"name": n, "args": a,
             "expect": REFERENCE[(n, json.dumps(a, sort_keys=True))]}
            for n, a in AGREEMENT_TABLE
        ]
        commands = json.dumps(CATALOG.commands)
        snippet = f"""
          tcCatalog = {commands};
          const table = {json.dumps(table)};
          const out = [];
          for (const row of table) {{
            const hex = (encodeByName(row.name, row.args) || '')
                          .replace(/\\s+/g, '').toLowerCase();
            out.push({{ name: row.name, hex, expect: row.expect,
                        ok: hex === row.expect }});
          }}
          return out;
        """
        snippet_file = tmp_path / "snippet.js"
        snippet_file.write_text(snippet)
        proc = subprocess.run(
            [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
        rows = json.loads(proc.stdout)
        bad = [r for r in rows if not r["ok"]]
        assert not bad, "JS encoder disagrees with the Python encoder:\n" + \
            "\n".join(f"  {r['name']}: js={r['hex']} py={r['expect']}"
                      for r in bad)

    def test_js_decoder_round_trips(self, tmp_path):
        commands = json.dumps(CATALOG.commands)
        snippet = f"""
          tcCatalog = {commands};
          const cmd = tcCatalog.find(c => c.name === 'PAYLOAD_COOLER_SETPOINT');
          const res = decodeCatalogArgs(cmd, '64 D7');
          const cap = tcCatalog.find(c => c.name === 'PAYLOAD_CAPTURE');
          const capRes = decodeCatalogArgs(cap, '04 12 08 00');
          return {{ setpoint: res.ok ? res.values.setpoint : null,
                    scene: capRes.ok ? capRes.values.scene_id : null,
                    lines: capRes.ok ? capRes.values.lines : null }};
        """
        snippet_file = tmp_path / "snippet.js"
        snippet_file.write_text(snippet)
        proc = subprocess.run(
            [NODE, str(HARNESS), str(INDEX), str(snippet_file)],
            capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
        out = json.loads(proc.stdout)
        assert out["setpoint"] == pytest.approx(-15.0, abs=1e-9)
        assert out["scene"] == 1042
        assert out["lines"] == 2048
