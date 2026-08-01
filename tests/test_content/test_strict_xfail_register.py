"""A strict-xfail must not become permanent furniture (brief 08 gap 10).

`@pytest.mark.xfail(strict=True)` is the right instrument for a *visible,
non-red* gate on a domain decision that only the customer can make: the suite
stays green, the item stays reported, and the day the decision lands the xfail
flips to XPASS and the build breaks until someone deletes it.

It stops being the right instrument the moment it survives a release. Three
strict-xfails on this project have now been open across two sessions, which is
how a gate turns into wallpaper.

The rule enforced here is deliberately **not** wall-clock based — a test that
starts failing on a calendar date fails in a classroom, not in CI. Instead:
every strict-xfail must be declared in the register below with the release it
was opened in and the release by which it must be resolved, and it fails the
build when `CURRENT_RELEASE` reaches that deadline. Bumping the release is a
deliberate act, so the gate trips at a moment someone is looking.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"

#: The release this tree is building. Bump it when a phase ships; that bump is
#: what makes an over-age strict-xfail fail the build.
CURRENT_RELEASE = "2026-08"

#: (file, test name) -> (opened_in, resolve_by, why it is not simply fixed).
#: `resolve_by` is a release string compared lexically — the format is
#: `YYYY-MM`, so lexical order is chronological order.
REGISTER: dict[tuple[str, str], tuple[str, str, str]] = {
    ("tests/test_content/test_s19_rules_content.py",
     "test_no_rule_is_misrouted"): (
        "2026-06", "2026-10",
        "13 shipped S19 rules dispatch an S8 func contradicting their "
        "description. Blanket-remapping S19 is explicitly forbidden (the "
        "2026-06 session established that lesson); each rule needs a "
        "per-rule ruling from Flight Ops. DECISIONS.md 'Also open'."),
    ("tests/test_content/test_s19_rules_content.py",
     "test_no_rule_trigger_contradicts_its_description"): (
        "2026-07", "2026-10",
        "4 S19 rules point at a trigger id whose catalogued meaning "
        "contradicts the rule's own name. Same ruling, same owner."),
    ("tests/test_content/test_procedures_content.py",
     "test_obdh_procedures_command_the_obdh"): (
        "2026-07", "2026-10",
        "obc_reboot.md and software_upload.md quote S8 funcs for OBDH "
        "operations that route to AOCS/EPS/payload/TCS handlers. Either the "
        "procedures or the S8 map is wrong; which one is a Flight-Ops call."),
}


def _strict_xfails() -> list[tuple[str, str, int]]:
    """Every `@pytest.mark.xfail(strict=True)` in the suite."""
    found: list[tuple[str, str, int]] = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:  # pragma: no cover - would fail collection anyway
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                target = dec.func
                name = ""
                while isinstance(target, ast.Attribute):
                    name = target.attr + ("." + name if name else "")
                    target = target.value
                if not name.startswith("mark.xfail") and name != "xfail":
                    continue
                strict = any(
                    kw.arg == "strict" and getattr(kw.value, "value", None) is True
                    for kw in dec.keywords)
                if strict:
                    found.append((rel, node.name, node.lineno))
    return found


def test_every_strict_xfail_is_registered():
    """An unregistered strict-xfail is an invisible open decision."""
    unregistered = [f"{f}::{n} (line {ln})" for f, n, ln in _strict_xfails()
                    if (f, n) not in REGISTER]
    assert not unregistered, (
        "strict-xfail with no entry in REGISTER — add one naming the release "
        "it was opened in, the release it must be resolved by, and who owns "
        f"the decision: {unregistered}")


def test_register_has_no_stale_entries():
    """A register entry whose xfail has been deleted hides the next one."""
    live = {(f, n) for f, n, _ in _strict_xfails()}
    stale = [f"{f}::{n}" for (f, n) in REGISTER if (f, n) not in live]
    assert not stale, (
        f"REGISTER names strict-xfails that no longer exist — delete them: "
        f"{stale}")


def test_no_strict_xfail_is_over_age():
    """The gate that stops a gate becoming furniture."""
    over = []
    for (f, n), (opened, resolve_by, why) in REGISTER.items():
        if CURRENT_RELEASE >= resolve_by:
            over.append(
                f"{f}::{n} — opened {opened}, due {resolve_by}, release is "
                f"{CURRENT_RELEASE}. {why}")
    assert not over, (
        "a strict-xfail has outlived its deadline. Either the decision has "
        "landed (delete the xfail and fix the content) or it has not (extend "
        "`resolve_by` deliberately, in a commit that says why):\n  "
        + "\n  ".join(over))


def test_deadlines_are_after_the_release_they_were_opened_in():
    bad = [f"{f}::{n}" for (f, n), (opened, due, _) in REGISTER.items()
           if due <= opened]
    assert not bad, f"REGISTER entries with a deadline at or before opening: {bad}"


def test_the_number_of_open_domain_decisions_does_not_grow():
    """A ratchet. Adding a strict-xfail is allowed; doing it silently is not."""
    assert len(REGISTER) <= 3, (
        f"{len(REGISTER)} strict-xfails are open. Each one is a question the "
        f"customer has not answered; raise the ceiling deliberately.")
