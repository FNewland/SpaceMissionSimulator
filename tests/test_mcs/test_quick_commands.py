"""Defect #89 — the QUICK COMMANDS panel and the data-field length check.

Franz:

    "When I send a quick command with data bytes, I get a data byte size error
    (it thinks they all have 0 bytes) which I have to accept to send the
    command."

Two things were wrong and both are covered here.

1. The soft pre-send length check (defect #67) resolved the expected byte count
   from the command builder's *ambient DOM state* — for S8.1, from the
   structured builder's function dropdown. A quick command never touches that
   dropdown, so "PA ON" was checked against whatever function happened to be
   selected. The check is now a pure function of ``(service, subtype, data)``:
   for S8.1 the func_id is the first octet of the data field, which is exactly
   how ``service_dispatch._route_*(data[0], data[1:])`` reads it.

2. The EXPECTED/ENTERED chip is refreshed by the ``input`` event, which
   assigning ``.value`` does not fire, so after a quick command it kept saying
   ``ENTERED 0 B``.

The table-driven part below is the important one. It does not hardcode a byte
count for any command: it derives, **from the dispatcher's own source**, how
many argument octets each S8.1 handler actually consumes, and compares that
with what the catalogue declares and with what the quick command sends. That is
the generic form of the defect-#80 class of bug (a command whose declared
length is shorter than its handler's ``if len(data) >= n:`` guard is silently
swallowed while S1 still reports success).
"""
import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from smo_mcs.tc_lengths import annotate_command

REPO = Path(__file__).resolve().parents[2]
INDEX = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"
DISPATCH = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
            / "service_dispatch.py")
CATALOG = REPO / "configs" / "eosat1" / "commands" / "tc_catalog.yaml"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(
    NODE is None, reason="node is required to execute the page's inline JS")


# ── the quick-command table, read from the CATALOGUE (Phase E WP-5) ──
#
# The page no longer carries a single hand-written byte literal: the QUICK
# COMMANDS panel is generated from tc_catalog.yaml's quick_commands list
# (command name + ENGINEERING args) and the hex is computed by the shared
# encoder. This table re-derives each button's wire bytes with the PYTHON
# encoder — the JS mirror is pinned to it by the cross-encoder agreement
# test (tests/test_content/test_command_encoders_agree.py).

from smo_common.commanding import encode as _encode, load_catalog as _load_catalog

_CATALOG = _load_catalog(REPO / "configs" / "eosat1")


def _quick_commands() -> list[dict]:
    out = []
    for q in _CATALOG.quick_commands:
        cmd = _CATALOG.get(q["command"])
        wire = _encode(q["command"], q.get("args") or {}, catalog=_CATALOG)
        out.append({
            "service": int(cmd["service"]), "subtype": int(cmd["subtype"]),
            "data_hex": " ".join(f"{b:02X}" for b in wire),
            "name": q.get("name") or q["command"],
            "command": q["command"],
            "n_bytes": len(wire),
            "func_id": (int(cmd["func_id"])
                        if (cmd["service"], cmd["subtype"]) == (8, 1)
                        and cmd.get("func_id") is not None else None),
        })
    return out


QUICK_COMMANDS = _quick_commands()


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    data = yaml.safe_load(CATALOG.read_text())
    cmds = data["commands"] if isinstance(data, dict) else data
    for c in cmds:
        annotate_command(c)
    return cmds


@pytest.fixture(scope="module")
def by_key(catalog) -> dict:
    """(service, subtype, func_id) -> annotated command, plus (svc, sub, None)."""
    out = {}
    for c in catalog:
        out[(c["service"], c["subtype"], c.get("func_id"))] = c
        out.setdefault((c["service"], c["subtype"], None), c)
    return out


# ── what the dispatcher's handlers actually consume ─────────────────
#
# Derived by reading service_dispatch.py rather than by listing commands, so a
# new S8 function is covered the moment it is written.


def _func_ids_tested(test: ast.expr) -> list[int]:
    ids: list[int] = []
    for node in ast.walk(test):
        if not (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "func_id"):
            continue
        for op, cmp in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(cmp, ast.Constant):
                ids.append(cmp.value)
            elif isinstance(op, ast.In) and isinstance(cmp, (ast.Tuple, ast.List,
                                                            ast.Set)):
                ids += [e.value for e in cmp.elts if isinstance(e, ast.Constant)]
    return [i for i in ids if isinstance(i, int)]


#: Attribute calls that mean "the handler did something with this command".
# `_require_u8` / `_require_bytes` are the 2026-08 mandatory-argument guards:
# they either yield the argument or record the refusal, so like
# `generate_s1_exec_fail` they are an ACTION — a short telecommand is answered,
# not silently dropped. Their argument appetite is read by `_octets_consumed`.
_ACTION_CALLS = {"_cmd", "handle_command", "generate_s1_exec_fail",
                 "_require_u8", "_require_bytes"}


def _length_guard(test: ast.expr):
    """``len(data) >= k`` / ``len(data) > k`` / ``if data`` -> k, else None."""
    if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Call)
            and isinstance(test.left.func, ast.Name)
            and test.left.func.id == "len" and test.left.args
            and isinstance(test.left.args[0], ast.Name)
            and test.left.args[0].id == "data"):
        for op, cmp in zip(test.ops, test.comparators):
            if isinstance(cmp, ast.Constant) and isinstance(cmp.value, int):
                if isinstance(op, ast.GtE):
                    return cmp.value
                if isinstance(op, ast.Gt):
                    return cmp.value + 1
    if isinstance(test, ast.Name) and test.id == "data":
        return 1
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        guards = [g for g in (_length_guard(v) for v in test.values) if g is not None]
        if guards:
            return max(guards)
    return None


def _has_action(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in _ACTION_CALLS
               for n in ast.walk(node))


def _min_acting_length(stmts: list[ast.stmt], guard: int = 0):
    """Shortest data field for which the branch does ANYTHING.

    This is the measure that matters for the defect-#80 bug: a command shorter
    than this reaches no handler call at all and is silently discarded while
    S1 acceptance and completion still report success. Handlers that accept
    several forms (S8 func 65 takes a 4-byte raw rate OR a 1-byte tier code OR
    nothing) correctly report the shortest of them.
    """
    best = None

    def note(v):
        nonlocal best
        if v is not None:
            best = v if best is None else min(best, v)

    for st in stmts:
        if isinstance(st, ast.If):
            g = _length_guard(st.test)
            note(_min_acting_length(st.body, max(guard, g) if g is not None else guard))
            note(_min_acting_length(st.orelse, guard))
            continue
        if _has_action(st):
            note(guard)
    return best


def _octets_consumed(body: list[ast.stmt]) -> int:
    """Highest octet index of ``data`` the branch reads.

    ``len(data) >= n`` guards, ``data[k]`` indexing and ``data[a:b]`` slicing
    are all evidence of the handler's appetite. Unlike
    ``_min_acting_length`` this is the *most* the handler can use, which is
    what the catalogue should be declaring.
    """
    need = 0
    module = ast.Module(body=body, type_ignores=[])
    for node in ast.walk(module):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Call)
                and isinstance(node.left.func, ast.Name)
                and node.left.func.id == "len" and node.left.args
                and isinstance(node.left.args[0], ast.Name)
                and node.left.args[0].id == "data"):
            for op, cmp in zip(node.ops, node.comparators):
                if isinstance(cmp, ast.Constant) and isinstance(cmp.value, int):
                    if isinstance(op, ast.GtE):
                        need = max(need, cmp.value)
                    elif isinstance(op, ast.Gt):
                        need = max(need, cmp.value + 1)
        # `self._require_u8(data, ...)` eats exactly one octet;
        # `self._require_bytes(data, n, ...)` eats n.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("_require_u8", "_require_bytes")
                and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "data"):
            if node.func.attr == "_require_u8":
                need = max(need, 1)
            elif (len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, int)):
                need = max(need, node.args[1].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "data"):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, int) \
                    and sl.value >= 0:
                need = max(need, sl.value + 1)
            elif isinstance(sl, ast.Slice) and isinstance(sl.upper, ast.Constant) \
                    and isinstance(sl.upper.value, int):
                need = max(need, sl.upper.value)
    return need


def _scan_dispatcher() -> tuple[dict[int, int], dict[int, int]]:
    tree = ast.parse(DISPATCH.read_text())
    acting: dict[int, int] = {}
    consumed: dict[int, int] = {}

    def visit(stmts):
        for st in stmts:
            if not isinstance(st, ast.If):
                continue
            ids = _func_ids_tested(st.test)
            if ids:
                a = _min_acting_length(st.body) or 0
                c = _octets_consumed(st.body)
                for i in ids:
                    acting[i] = max(acting.get(i, 0), a)
                    consumed[i] = max(consumed.get(i, 0), c)
            else:
                visit(st.body)
            visit(st.orelse)

    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name.startswith("_route_")
                and node.name.endswith("_cmd")):
            visit(node.body)
    assert len(acting) > 60, \
        f"only {len(acting)} S8 handlers found — the extraction has drifted"
    return acting, consumed


@pytest.fixture(scope="module")
def handler_minima() -> dict[int, int]:
    """func_id -> shortest argument field the handler will act on at all."""
    return _scan_dispatcher()[0]


@pytest.fixture(scope="module")
def handler_maxima() -> dict[int, int]:
    """func_id -> most argument octets the handler can use."""
    return _scan_dispatcher()[1]


# Two catalogue/handler divergences that predate this defect and are NOT in
# this lane. They are recorded here so the sweep below still guards every other
# command and fails the moment a NEW one appears.
#
#   65 TTC_SET_DATA_RATE — DELIBERATE, not a defect. The catalogued form is the
#     1-byte tier code; the 4-byte raw-bps form is kept only for older clients
#     (defect #85, recorded as `deprecated_aliases` in the catalogue). Both
#     forms act, so nothing is discarded.
#
# Phase E WP-1/WP-2 RESOLVED the two open hand-offs that used to sit here:
#   28 PAYLOAD_CAPTURE now reads `scene_id uint16 + lines uint16` exactly as
#     the catalogue declares (DECISIONS.md section 9), and
#   68 TTC_SET_TX_POWER now reads the catalogued 1-byte level with the
#     corrected watt table (0=1 W, 1=2 W, 2=5 W).
# Their removal from this set IS the acceptance test for those work packages;
# a new entry means a new divergence and has to be looked at by a human.
KNOWN_CATALOGUE_HANDLER_DIVERGENCES = {65}


def _declared_arg_bytes(cmd: dict):
    """Argument octets the catalogue declares for an S8.1 command, or None."""
    if (cmd.get("service"), cmd.get("subtype")) != (8, 1):
        return None
    expected = cmd.get("expected_data_bytes")
    if expected is None:              # variable / undeclared: nothing to check
        return None
    return expected - 1               # the func_id byte is not an argument


class TestCatalogueMatchesTheHandlers:
    """The defect-#80 class, checked mechanically over every S8.1 command."""

    def test_no_catalogued_command_is_silently_discarded(self, catalog,
                                                         handler_minima):
        """A command built to the catalogue must reach its handler.

        This is the exact shape of defect #80: `RW_SET_SPEED_BIAS` declared 3
        argument bytes, the handler's only branch was `if len(data) >= 5:`, so
        a correctly-built command did nothing at all while S1.1/S1.7 reported
        success. Derived from the dispatcher's source, so a new S8 function is
        covered the moment it is written.
        """
        short = []
        for cmd in catalog:
            declared = _declared_arg_bytes(cmd)
            fid = cmd.get("func_id")
            if declared is None or fid not in handler_minima:
                continue
            if declared < handler_minima[fid]:
                short.append(
                    f"{cmd['name']} (func {fid}) declares {declared} argument "
                    f"byte(s); the handler acts only from {handler_minima[fid]}")
        assert not short, (
            "a command SHORTER than its handler's guard is silently discarded "
            "while S1 still reports success:\n  " + "\n  ".join(short))

    def test_the_set_of_handlers_that_want_more_than_is_declared_is_pinned(
            self, catalog, handler_maxima):
        """The softer half: the handler can use more than is declared.

        A command in this state is not discarded — it runs with the handler's
        internal defaults for the octets the operator was never told to send,
        which is the *silently wrong* rather than the *silently ignored*
        failure. One of the three is deliberate (a legacy longer form) and two
        are open hand-offs; see the comment on the set above. The assertion
        exists so a fourth cannot appear unnoticed.
        """
        found = set()
        detail = []
        for cmd in catalog:
            declared = _declared_arg_bytes(cmd)
            fid = cmd.get("func_id")
            if declared is None or fid not in handler_maxima:
                continue
            if declared < handler_maxima[fid]:
                found.add(fid)
                detail.append(f"{cmd['name']} (func {fid}) declares {declared}, "
                              f"handler can use {handler_maxima[fid]}")
        new = found - KNOWN_CATALOGUE_HANDLER_DIVERGENCES
        assert not new, (
            "new catalogue/handler length divergence:\n  "
            + "\n  ".join(d for d in detail
                          if int(d.split("func ")[1].split(")")[0]) in new))

    def test_declared_length_EQUALS_the_handler_appetite(self, catalog,
                                                         handler_maxima):
        """Phase E WP-6 drift gate 1 — tightened to EQUALITY.

        For every fixed-length S8.1 command, the argument octets the
        catalogue declares must equal the most the handler can consume.
        Fewer means the operator is never told about octets the handler
        substitutes defaults for (the silent-wrong failure); more means the
        handler ignores octets the catalogue promises are load-bearing.
        Func 65's deliberate legacy 4-byte form is the single pinned
        exception (`deprecated_aliases` in the catalogue).
        """
        bad = []
        for cmd in catalog:
            declared = _declared_arg_bytes(cmd)
            fid = cmd.get("func_id")
            if declared is None or fid not in handler_maxima:
                continue
            if fid in KNOWN_CATALOGUE_HANDLER_DIVERGENCES:
                continue
            if declared != handler_maxima[fid]:
                bad.append(f"{cmd['name']} (func {fid}) declares {declared} "
                           f"argument byte(s); the handler consumes up to "
                           f"{handler_maxima[fid]}")
        assert not bad, "\n  ".join(["catalogue/handler length mismatch:"] + bad)


class TestQuickCommandTable:
    """Every button in the QUICK COMMANDS panel, one case each."""

    def test_the_panel_is_not_empty(self):
        assert len(QUICK_COMMANDS) >= 60, \
            f"only {len(QUICK_COMMANDS)} quick commands parsed from index.html"

    @pytest.mark.parametrize("qc", QUICK_COMMANDS,
                             ids=[f"{q['name']}:{q['data_hex'] or 'empty'}"
                                  for q in QUICK_COMMANDS])
    def test_quick_command_resolves_to_a_catalogue_entry(self, qc, by_key):
        key = (qc["service"], qc["subtype"], qc["func_id"])
        assert key in by_key or (qc["service"], qc["subtype"], None) in by_key, \
            f"{qc['name']} ({qc['data_hex']}) is not in tc_catalog.yaml"

    @pytest.mark.parametrize("qc", QUICK_COMMANDS,
                             ids=[f"{q['name']}:{q['data_hex'] or 'empty'}"
                                  for q in QUICK_COMMANDS])
    def test_quick_command_sends_the_catalogued_number_of_bytes(self, qc, by_key):
        cmd = by_key.get((qc["service"], qc["subtype"], qc["func_id"])) \
            or by_key[(qc["service"], qc["subtype"], None)]
        expected = cmd["expected_data_bytes"]
        if expected is None:
            pytest.skip(f"{cmd['name']} has no declared length "
                        f"({cmd['length_note']})")
        assert qc["n_bytes"] == expected, (
            f"quick command {qc['name']} sends {qc['n_bytes']} byte(s) but "
            f"{cmd['name']} expects {expected} — the console would warn on a "
            f"command an operator cannot fix")

    @pytest.mark.parametrize("qc", [q for q in QUICK_COMMANDS
                                    if q["func_id"] is not None],
                             ids=[f"{q['name']}:{q['data_hex']}"
                                  for q in QUICK_COMMANDS
                                  if q["func_id"] is not None])
    def test_quick_command_satisfies_the_handler(self, qc, handler_minima):
        """What is sent must be at least what the handler reads.

        This is the check that would have caught defect #80 from the console
        side: the bytes on the wire, against the dispatcher's own guard.
        """
        fid = qc["func_id"]
        if fid not in handler_minima:
            pytest.skip(f"func {fid} has no length-bearing handler branch")
        sent_args = qc["n_bytes"] - 1        # minus the func_id byte
        assert sent_args >= handler_minima[fid], (
            f"{qc['name']} sends {sent_args} argument byte(s); the handler for "
            f"func {fid} reads {handler_minima[fid]} and would silently "
            f"discard the command")


# ── the browser behaviour ───────────────────────────────────────────

def run_js(snippet: str, tmp_path: Path):
    snippet_file = tmp_path / "snippet.js"
    snippet_file.write_text(snippet)
    proc = subprocess.run([NODE, str(HARNESS), str(INDEX), str(snippet_file)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


CATALOG_STUB = """
const payload = { commands: [
  { name:'AOCS_SET_MODE', service:8, subtype:1, func_id:0,
    expected_data_bytes:2, min_data_bytes:2, variable_length:false,
    length_note:'', allowed:true },
  { name:'TTC_PA_ON', service:8, subtype:1, func_id:66,
    expected_data_bytes:1, min_data_bytes:1, variable_length:false,
    length_note:'', allowed:true },
  { name:'TTC_SWITCH_PRIMARY', service:8, subtype:1, func_id:63,
    expected_data_bytes:null, min_data_bytes:1, variable_length:false,
    length_note:"catalogue entry has no 'fields' key", allowed:true },
  { name:'MEM_LOAD', service:6, subtype:2, expected_data_bytes:null,
    min_data_bytes:6, variable_length:true, length_note:'variable',
    allowed:true },
  { name:'AOCS_SET_TORQUE_GAINS_ALL', service:6, subtype:2,
    expected_data_bytes:16, min_data_bytes:16, variable_length:false,
    length_note:'', allowed:true },
  { name:'HK_REQUEST', service:3, subtype:27, expected_data_bytes:2,
    min_data_bytes:2, variable_length:false, length_note:'', allowed:true }
] };
fetch = async () => ({ json: async () => payload });
"""


@requires_node
class TestLengthCheckIsAPureFunctionOfTheWire:

    def test_func_id_comes_from_the_data_field_not_the_dropdown(self, tmp_path):
        out = run_js(CATALOG_STUB + """
          return loadTcCatalog().then(() => {
            // The builder's function dropdown says AOCS_SET_MODE (func 0),
            // exactly as it does on a freshly loaded page. The quick command
            // being sent is TTC_PA_ON.
            document.getElementById('sf-func_id').value = '0';
            return {
              pa_on: lengthInfoForWire(8, 1, '42', 'TTC_PA_ON').name,
              packed: lengthInfoForWire(8, 1, '4200', 'x') &&
                      lengthInfoForWire(8, 1, '4200', 'x').name,
              set_mode: lengthInfoForWire(8, 1, '00 02', '').name,
              wire_func: wireFuncId(8, 1, '42'),
              not_s8: wireFuncId(3, 27, '00 01')
            };
          });
        """, tmp_path)
        assert out["pa_on"] == "TTC_PA_ON"
        assert out["packed"] == "TTC_PA_ON"      # "4200" style, no spaces
        assert out["set_mode"] == "AOCS_SET_MODE"
        assert out["wire_func"] == 0x42
        assert out["not_s8"] is None

    def test_a_correct_quick_command_raises_no_warning(self, tmp_path):
        """The regression Franz reported: no confirm() on a valid command."""
        out = run_js(CATALOG_STUB + """
          const asked = [];
          confirm = (m) => { asked.push(m); return true; };
          fetch = async (url) => ({
            json: async () => (String(url).indexOf('tc-catalog') >= 0
                               ? payload : { status: 'sent', seq: 1 })
          });
          return loadTcCatalog().then(async () => {
            document.getElementById('sf-func_id').value = '0';   // stale, unrelated
            await sendPUSCommand(8, 1, '42', 'TTC_PA_ON');
            await sendPUSCommand(8, 1, '00 02', 'AOCS_DETUMBLE');
            await sendPUSCommand(3, 27, '00 01', 'HK_REQ_EPS');
            await sendPUSCommand(8, 1, '63', 'TTC_SWITCH_PRIMARY');  // undeclared
            await sendPUSCommand(6, 2, '01 02 03 04 05 06 07', 'MEM_LOAD'); // variable
            return asked;
          });
        """, tmp_path)
        assert out == [], f"a correct quick command warned: {out}"

    def test_a_genuinely_wrong_length_still_warns(self, tmp_path):
        out = run_js(CATALOG_STUB + """
          const asked = [];
          confirm = (m) => { asked.push(m); return false; };
          return loadTcCatalog().then(async () => {
            await sendPUSCommand(8, 1, '00', 'AOCS_SET_MODE');          // 1 of 2
            await sendPUSCommand(3, 27, '00 01 02', 'HK_REQ_EPS');      // 3 of 2
            return asked;
          });
        """, tmp_path)
        assert len(out) == 2, out
        assert "expects 2 data bytes" in out[0] and "you entered 1" in out[0]
        assert "you entered 3" in out[1]

    def test_variable_and_undeclared_never_report_zero(self, tmp_path):
        out = run_js(CATALOG_STUB + """
          return loadTcCatalog().then(() => ({
            variable: expectedBytesText(lengthInfoForWire(6, 2, '', 'MEM_LOAD')),
            undeclared: expectedBytesText(lengthInfoForWire(8, 1, '3F', ''))
          }));
        """, tmp_path)
        assert "variable" in out["variable"]
        assert "0 data bytes" not in out["variable"]
        assert "not declared" in out["undeclared"]
        assert "0 data bytes" not in out["undeclared"]

    def test_quick_command_click_refreshes_the_bytes_chip(self):
        """`.value = ...` does not fire `input`, so the chip must be told."""
        src = INDEX.read_text()
        body = re.search(r'function initQuickCmds\(\)\s*\{(.*?)\n\}', src, re.S)
        assert body, "initQuickCmds missing"
        assert "updateCmdBytesChip('cmd')" in body.group(1), \
            "the EXPECTED/ENTERED chip would keep saying 'ENTERED 0 B'"
        assert "sendPUSCommand(Number(cmd.service)" in body.group(1), \
            "the quick command must name itself to the send path"
        assert "encodeCatalogCommand(cmd, args)" in body.group(1), \
            "the hex must be COMPUTED by the shared encoder, never read from a literal"

    def test_send_path_does_not_read_the_builder_dom(self):
        src = INDEX.read_text()
        body = re.search(r'async function sendPUSCommand\((.*?)\n\}\n', src, re.S)
        assert body
        assert "lengthInfoForWire(service, subtype, dataHex, name)" in body.group(1)
        assert "currentCmdLengthInfo()" not in body.group(1), \
            "the length check must not depend on the builder's ambient state"
