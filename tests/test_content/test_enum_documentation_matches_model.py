"""A documented state code must mean what the spacecraft actually sends.

Field report 2026-08-20. `obdh.last_reboot_cause` (0x0316) documented
``1=watchdog, 2=memory, 3=switchover, 4=commanded`` while the flight model
defines ``REBOOT_COMMAND=1, REBOOT_WATCHDOG=2, REBOOT_MEMORY_ERROR=3,
REBOOT_SWITCHOVER=4``. Every non-zero value decoded to the wrong cause.

The model is authoritative: it matches ``defects/reviews/obdh.md``, which
states "NONE=0, CMD=1, WATCHDOG=2, MEM=3, SWITCHOVER=4". The catalogue was
wrong, and five operational procedures had been written against the wrong
catalogue — so a crew following ``obc_reboot.md`` would have verified
``= 4 (commanded)`` after a commanded reboot that reports 1, and the
switchover procedure expected 3 where the model sends 4. Those procedures
would have failed against the spacecraft they were written for.

This became urgent when the console started rendering engineering values:
a mismatch that used to show a bare ``1`` now confidently says ``watchdog``.
A wrong name is worse than a raw number, because it stops the operator
looking it up.

These tests pin the chain end to end: model constants -> catalogue
description -> every procedure that asserts a value.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
PARAMS = CONFIG / "telemetry" / "parameters.yaml"
PROCEDURES = CONFIG / "procedures"
OBDH_MODEL = (REPO / "packages" / "smo-simulator" / "src" / "smo_simulator"
              / "models" / "obdh_basic.py")


def _catalogue_states(pid: int) -> dict[int, str]:
    """The codes as DOCUMENTED, parsed from the catalogue description."""
    from smo_common.telemetry.eng_values import parse_states_from_description
    for p in yaml.safe_load(PARAMS.read_text())["parameters"]:
        ident = p["id"] if isinstance(p["id"], int) else int(str(p["id"]), 0)
        if ident == pid:
            return parse_states_from_description(p.get("description", ""))
    raise AssertionError(f"0x{pid:04X} missing from parameters.yaml")


def _model_reboot_constants() -> dict[int, str]:
    """The codes as IMPLEMENTED, read from the flight model's constants."""
    src = OBDH_MODEL.read_text()
    out = {}
    for name, value in re.findall(r"^(REBOOT_[A-Z_]+)\s*=\s*(\d+)", src, re.M):
        out[int(value)] = name.replace("REBOOT_", "").lower()
    return out


def test_reboot_cause_catalogue_matches_the_flight_model():
    model = _model_reboot_constants()
    assert model, "no REBOOT_* constants found in the OBDH model"
    documented = _catalogue_states(0x0316)
    # The catalogue's words are looser than the constant names ("memory" for
    # MEMORY_ERROR), so compare the code ordering by keyword, not verbatim.
    expect_keyword = {0: "none", 1: "command", 2: "watchdog",
                      3: "memory", 4: "switchover"}
    for code, keyword in expect_keyword.items():
        assert code in model, f"model lost REBOOT code {code}"
        assert keyword in model[code], (
            f"model code {code} is {model[code]!r}, expected {keyword!r}")
        assert code in documented, (
            f"catalogue no longer documents reboot code {code}")
        assert keyword in documented[code].lower(), (
            f"catalogue says code {code} means {documented[code]!r}, but the "
            f"flight model sends it for {model[code]!r}. An operator reading "
            "this after an unexpected reboot is told the wrong cause.")


@pytest.mark.parametrize("code,keyword", [
    (1, "commanded"), (4, "switchover"),
])
def test_procedures_assert_the_codes_the_spacecraft_actually_sends(code, keyword):
    """Procedures verifying a reboot cause must use the real code."""
    hits = []
    for md in PROCEDURES.rglob("*.md"):
        text = md.read_text()
        for line in text.splitlines():
            if "last_reboot_cause" not in line:
                continue
            m = re.search(r"=\s*(\d)\s*\((\w+)\)", line)
            if m and m.group(2).lower().startswith(keyword[:6]):
                hits.append((md.name, int(m.group(1)), line.strip()[:90]))
    for name, asserted, line in hits:
        assert asserted == code, (
            f"{name} expects last_reboot_cause = {asserted} for "
            f"'{keyword}', but the spacecraft sends {code}. This procedure "
            f"would fail on a real pass.\n    {line}")


def test_no_procedure_documents_the_superseded_ordering():
    """The old ordering, verbatim, must not survive anywhere."""
    stale = "1=watchdog, 2=memory, 3=switchover, 4=commanded"
    offenders = [md.relative_to(REPO) for md in PROCEDURES.rglob("*.md")
                 if stale in md.read_text()]
    assert not offenders, (
        f"the superseded reboot-cause ordering is still documented in: "
        f"{offenders}")
