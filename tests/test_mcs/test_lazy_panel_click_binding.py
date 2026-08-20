"""Field report 2026-08-20: sixteen PUS-panel controls posted nothing.

The modern MCS renders the DIAG/PUS panes LAZILY -- ``renderS3Panel``,
``renderS11Panel``, ``renderMemoryPanel`` (S6), ``renderS9Panel`` and
``renderParamsPanel`` write their markup only when their ``data-diag`` tab is
first clicked.  The button bindings, however, were registered once at init by

    const on = (id, fn) => { const el = $(id); if (el) el.addEventListener(...) };

so at registration time those elements did not exist, ``if (el)`` skipped them
silently, and no listener was ever attached.  ENABLE ALL / DISABLE ALL /
RELOAD DEFINITIONS (S3), the whole S6 memory block, REQUEST TIME REPORT /
SYNC / SET SC TIME (S9), ADD (11,4) and params REFRESH were all inert -- with
no JS error to give it away, which is why it shipped.

These tests pin the fix: ``on()`` must be delegation-based, and every id it
binds that belongs to a lazily-rendered pane must still resolve.  Mutating the
helper back to the ``addEventListener``-at-init form fails ``test_on_helper_
is_delegated``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

MCS_JS = (Path(__file__).resolve().parents[2] / "packages" / "smo-mcs" / "src" /
          "smo_mcs" / "static" / "modern" / "mcs.js")

# Panels whose markup is produced by a render function called on tab click, not
# at boot.  Any id-bound control inside one of these is only reachable through
# delegation.
LAZY_PANEL_IDS = [
    "s3-req-all", "s3-en-all", "s3-dis-all", "s3-reload",
    "s11-now", "s11-add",
    "s6-dump", "s6-crc", "s6-load", "s6-verify", "s6-readback",
    "s9-report", "s9-sync", "s9-set-now", "s9-set-send",
    "params-refresh",
]


@pytest.fixture(scope="module")
def src() -> str:
    return MCS_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(src: str) -> str:
    """`src` with whole-line // comments stripped.

    The fix's own explanatory comment quotes the broken helper verbatim, so a
    naive scan would match the cautionary tale rather than the live code.
    """
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def test_on_helper_is_delegated(code: str) -> None:
    """The id->handler helper must register into a map, not bind at init."""
    m = re.search(r"const on = \(id, fn\) => \{(?P<body>[^}]*)\}", code)
    assert m, "the `on(id, fn)` binding helper is gone or was reshaped"
    body = m.group("body")
    assert "addEventListener" not in body, (
        "on() binds a listener to the element at registration time again -- "
        "controls inside lazily-rendered PUS panes will be inert (field "
        "report 2026-08-20). Register into the delegation map instead."
    )
    assert re.search(r"\.set\(id, fn\)", body), (
        "on() no longer records the handler in a lookup map")
    # ...and there must be a document-level click listener that consults it.
    assert re.search(r'document\.addEventListener\("click"', code)


def test_delegation_walks_ancestors(code: str) -> None:
    """A click on a child node (an icon/span inside a button) must still hit."""
    assert re.search(r"_clickById\.has\(el\.id\)", code)
    assert re.search(r"el = el\.parentElement", code), (
        "the delegated click listener does not walk up from e.target, so "
        "clicks landing on a child element of a bound button are lost")


@pytest.mark.parametrize("elem_id", LAZY_PANEL_IDS)
def test_lazy_panel_control_is_bound_and_rendered(code: str, elem_id: str) -> None:
    """Every dead control from the field report: markup exists AND is bound."""
    assert f'id="{elem_id}"' in code, f"{elem_id} is no longer rendered"
    assert re.search(r'on\(\s*"%s"' % re.escape(elem_id), code), (
        f"{elem_id} is rendered but has no on() binding -- it will do nothing")


def test_refusals_are_visible_not_only_announced(code: str) -> None:
    """A refusal only an accessibility tool can hear reads as a dead button.

    ADD (11,4) legitimately refuses an empty Svc,Sub, but said so only via
    ``announce()`` -- the screen-reader-only live region -- so it looked like
    another inert control in the 2026-08-20 field report. Both refusal paths
    must now write into their panel's own preview line.
    """
    assert re.search(r"function panelResult\(id, msg, bad\)", code), (
        "the panelResult() helper that surfaces refusals in-panel is gone")
    for fn_name in ("s11Add", "s9SetTime"):
        m = re.search(r"async function %s\([^)]*\) \{(.*?)\n\}" % fn_name,
                      code, re.S)
        assert m, f"{fn_name} is gone or was reshaped"
        body = m.group(1)
        assert "panelResult(" in body, (
            f"{fn_name} reports its outcome without panelResult() -- an "
            "operator gets no visible feedback")
        assert not re.search(r"\n\s*announce\(", body), (
            f"{fn_name} still has a bare announce()-only path; route it "
            "through panelResult() so the reason is visible")


def test_every_telecommand_is_recorded_in_history(code: str) -> None:
    """No call site may reach /api/pus-command without recording the send.

    Field report 2026-08-20 (second): the PUS panels commanded the spacecraft
    but nothing appeared in CMD HISTORY. Twenty call sites posted to
    /api/pus-command directly and only two -- the builder and the quick
    commands -- also called noteHistory(). Recording therefore moved into the
    transport, sendPus(). Exactly one place may name the endpoint.
    """
    m = re.search(r"async function sendPus\(body\) \{(?P<body>.*?)\n\}", code, re.S)
    assert m, "sendPus() -- the single recording transport -- is gone"
    # unconditionally, at statement position -- a call parked behind a guard
    # records nothing and is exactly how this would silently come back.
    assert re.search(r"^\s*noteHistory\(\{", m.group("body"), re.M), (
        "sendPus() no longer records the send unconditionally; CMD HISTORY "
        "will go blank for every panel that uses it")

    posts = re.findall(r'postJSON\("/api/pus-command"', code)
    assert len(posts) == 1, (
        f"{len(posts)} call sites post to /api/pus-command directly; only "
        "sendPus() may. A direct post is a telecommand with no ground record.")

    # ...and nothing may record twice, which would double-count the decision log.
    assert code.count("noteHistory({") == 1, (
        "more than one place builds a history entry -- sends will be "
        "double-recorded")


def test_pus_posts_use_data_hex(code: str) -> None:
    """Companion pin: the server reads data_hex, never data (2026-08-19)."""
    for call in re.findall(r'sendPus\(\{[^}]*\}', code):
        assert "data_hex" in call, f"sendPus call without data_hex: {call[:120]}"
