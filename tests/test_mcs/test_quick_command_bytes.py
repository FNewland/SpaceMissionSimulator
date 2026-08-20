"""Every shipped quick command must put the SAME bytes on the wire as the
authoritative Python encoder.

Quick commands are the buttons an operator actually presses under time
pressure (first-pass startup, HK enable, mode changes), and they carry PRESET
arguments the operator never sees -- so a wrong byte is invisible until the
spacecraft does the wrong thing. This suite has already been bitten twice:
`AOCS_DETUMBLE` once sent mode=4 because the preset args were dropped, and in
2026-08 every modern-console command went out with an EMPTY data field
because the payload key was `data` where the server reads `data_hex`
(tests/test_mcs/test_pus_command_payload_contract.py pins that half).

This pins the other half: the browser's `encodeCatalogCommand` against
`smo_common.commanding.encode`, which is what procedures, OBCPs, the planner
expansion and S11 embeds all go through. Both must agree for all 110 entries,
including the S8.1 func_id prefix octet.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
MCS_JS = REPO / "packages/smo-mcs/src/smo_mcs/static/modern/mcs.js"

# The encoder and every helper it reaches. Kept explicit: if the page grows a
# new helper and this list goes stale, the harness fails loudly rather than
# silently testing a stub.
HELPERS = ["catCal", "catEngToRaw", "catFieldDefault", "catFieldError",
           "catFieldWidth", "catFormatEng", "catIsFloat", "catPackRaw",
           "catRawToEng", "encodeCatalogCommand"]

pytestmark = pytest.mark.skipif(
    subprocess.run(["which", "node"], capture_output=True).returncode != 0,
    reason="node not available")


def _catalog():
    return yaml.safe_load(CONFIG.joinpath("commands", "tc_catalog.yaml")
                          .read_text(encoding="utf-8"))


def _python_expected():
    from smo_common.commanding import load_catalog, encode
    cat_obj = load_catalog(str(CONFIG))
    raw = _catalog()
    cmds = {c["name"]: c for c in raw.get("commands", [])}
    rows = []
    for q in raw.get("quick_commands") or []:
        c = cmds.get(q["command"])
        assert c is not None, (
            f"quick command {q.get('name')} names {q['command']!r}, which is "
            f"not in the catalogue — the button would do nothing")
        rows.append({
            "idx": len(rows),
            "label": q.get("label") or "",
            "name": q.get("name") or q["command"],
            "command": q["command"],
            "args": q.get("args") or {},
            "service": c["service"], "subtype": c["subtype"],
            "func_id": c.get("func_id"), "fields": c.get("fields") or [],
            "py_hex": encode(q["command"], q.get("args") or {},
                             catalog=cat_obj).hex().upper(),
        })
    return rows


_HARNESS = r"""
import fs from 'fs';
const src = fs.readFileSync(process.argv[2], 'utf8');
const names = %s;
let h = '';
for (const n of names) {
  const m = src.match(new RegExp(`(?:export\\s+)?function ${n}\\s*\\([\\s\\S]*?\\n\\}`, 'm'));
  if (!m) { console.log(JSON.stringify({fatal: 'missing helper ' + n})); process.exit(0); }
  h += m[0].replace(/^export\s+/, '') + '\n';
}
h += 'globalThis.__enc = encodeCatalogCommand;\n';
eval(h);
const rows = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = [];
for (const r of rows) {
  let hex, err = null;
  try {
    hex = String(globalThis.__enc(
      {service: r.service, subtype: r.subtype, func_id: r.func_id,
       fields: r.fields, name: r.command}, r.args)).replace(/\s+/g, '').toUpperCase();
  } catch (e) { hex = null; err = e.message; }
  out.push({idx: r.idx, name: r.name, js_hex: hex, err});
}
console.log(JSON.stringify(out));
"""


def _js_actual(rows, tmp_path):
    harness = tmp_path / "h.mjs"
    harness.write_text(_HARNESS % json.dumps(HELPERS), encoding="utf-8")
    data = tmp_path / "rows.json"
    data.write_text(json.dumps(rows), encoding="utf-8")
    r = subprocess.run(["node", str(harness), str(MCS_JS), str(data)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"harness failed: {r.stderr[:400]}"
    parsed = json.loads(r.stdout.strip().splitlines()[-1])
    if isinstance(parsed, dict) and parsed.get("fatal"):
        pytest.fail(parsed["fatal"] + " — update HELPERS in this test")
    return {e["idx"]: e for e in parsed}


def test_every_quick_command_encodes_identically(tmp_path):
    rows = _python_expected()
    assert len(rows) > 50, "quick-command list looks truncated"
    js = _js_actual(rows, tmp_path)

    errors, mismatches = [], []
    for r in rows:
        got = js.get(r["idx"])
        assert got is not None, f"{r['name']} missing from the JS run"
        if got["err"]:
            errors.append(f"{r['name']}: {got['err']}")
        elif got["js_hex"] != r["py_hex"]:
            mismatches.append(
                f"[{r['idx']}] {r['label'] or r['name']} ({r['command']} {r['args']}): "
                f"console={got['js_hex']} python={r['py_hex']}")

    assert not errors, "console encoder threw on:\n  " + "\n  ".join(errors)
    assert not mismatches, (
        f"{len(mismatches)} quick command(s) put different bytes on the wire "
        f"from the console than from every other path:\n  "
        + "\n  ".join(mismatches))


def test_s8_quick_commands_carry_their_func_id(tmp_path):
    """S8.1 data must START with the func_id octet — a missing prefix routes
    the command to whatever function that first argument byte happens to name."""
    rows = [r for r in _python_expected()
            if (r["service"], r["subtype"]) == (8, 1) and r["func_id"] is not None]
    assert rows, "no S8.1 quick commands found"
    js = _js_actual(rows, tmp_path)
    bad = [f"{r['name']}: expected {r['func_id']:02X}… got {js[r['idx']]['js_hex']}"
           for r in rows
           if not (js[r["idx"]]["js_hex"] or "").startswith(f"{r['func_id']:02X}")]
    assert not bad, "S8.1 quick commands without their func_id prefix:\n  " + "\n  ".join(bad)


def test_quick_buttons_have_unique_identities():
    """Two buttons must never resolve to the same quick entry.

    FIELD DEFECT 2026-08-20: `data-quick` carried `q.name || q.command` and
    `sendQuick` resolved it with `.find()`, which returns the FIRST match. Ten
    shipped entries have no `name:`, so "DL Mode OFF" and "GPS Rx Off" both
    fired their ON twin's args -- neither could be turned off from the
    console, silently, while the button reported success.

    The page now keys on the array index, which cannot collide. This test pins
    BOTH halves: the render must not go back to a name key, and the config
    should still give every entry a unique identity for history and audit.
    """
    js = MCS_JS.read_text(encoding="utf-8")
    assert 'data-quick="${q._qi}"' in js, (
        "quick buttons no longer key on the collision-proof index — two "
        "entries sharing a command name would fire the same args again")
    assert 'data-quick="${esc(q.name || q.command)}"' not in js

    raw = _catalog()
    qc = raw.get("quick_commands") or []
    seen, collisions = {}, []
    for i, q in enumerate(qc):
        key = q.get("name") or q["command"]
        if key in seen:
            collisions.append(
                f"{key!r}: {seen[key].get('label')!r} vs {q.get('label')!r}")
        seen[key] = q
    assert not collisions, (
        "quick entries share an identity (harmless now the UI keys on index, "
        "but they are indistinguishable in command history and audit — give "
        "each a unique `name:`):\n  " + "\n  ".join(collisions))
