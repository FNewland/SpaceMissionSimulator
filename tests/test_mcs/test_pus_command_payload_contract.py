"""The modern console's PUS payload key must match what the server reads.

FIELD DEFECT 2026-08-20 (owner: "a number of commands do not seem to work,
whereas they work first time in classic"). ``_handle_pus_command`` reads
``body["data_hex"]``; every one of the 20 ``/api/pus-command`` call sites in
the modern page sent ``body["data"]``. The key silently defaulted to ``""``,
so EVERY PUS command from the modern console went out with an EMPTY data
field: HK ENABLE with no SID, S11 schedule inserts with no time tag,
S20 parameter sets with no value.

It failed silently in both directions -- the POST returned 200 because the
packet was well-formed, and the spacecraft either ignored it or, worse,
applied an argument-less default (this is where the beacon-mode command that
latched the ladder to SAFE came from: func 70 with no argument byte defaulted
to ON).

A shape mismatch between a client and its own server is exactly the class the
manual page hit the same week (``pages`` vs ``sections``), so pin it: assert
the key the server reads and the key every client sends are the same string.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MCS_JS = REPO / "packages/smo-mcs/src/smo_mcs/static/modern/mcs.js"
SERVER = REPO / "packages/smo-mcs/src/smo_mcs/server.py"


def _pus_payloads(js: str):
    """Yield (line_no, object-literal) for each postJSON to /api/pus-command."""
    for m in re.finditer(r'postJSON\("/api/pus-command",\s*\{', js):
        start = m.end() - 1
        depth, j = 0, start
        while j < len(js):
            if js[j] == "{":
                depth += 1
            elif js[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        yield js[:start].count("\n") + 1, js[start:j + 1]


def test_server_still_reads_data_hex():
    """If the server ever renames the key, this test names the new contract."""
    assert 'body.get("data_hex"' in SERVER.read_text(encoding="utf-8"), (
        "the PUS handler no longer reads 'data_hex' — update every modern "
        "call site AND this test together, or the console goes silent again")


def test_every_modern_pus_payload_uses_data_hex():
    js = MCS_JS.read_text(encoding="utf-8")
    payloads = list(_pus_payloads(js))
    assert payloads, "no /api/pus-command call sites found — did the path change?"

    offenders = []
    for line, obj in payloads:
        if "data_hex" in obj:
            continue
        # a bare `data:` or shorthand `data,` means an empty field on the wire
        if re.search(r"(?<![\w.])data\s*[:,]", obj):
            offenders.append(line)
    assert not offenders, (
        f"{len(offenders)} /api/pus-command payload(s) send 'data' instead of "
        f"'data_hex' (lines {offenders}) — the server reads 'data_hex', so "
        f"these commands go out with an EMPTY data field")


def test_the_argument_carrying_commands_are_covered():
    """Guard the specific ones the owner found broken."""
    js = MCS_JS.read_text(encoding="utf-8")
    for needle in ("data_hex: sidToHex(sid)",):          # HK enable/disable/oneshot
        assert needle in js, f"expected {needle!r} — HK SID commands need their SID"
