"""The two consoles must not disagree about whether a value is current.

Field report 2026-08-20: "check the stale telemetry mechanism ... both in the
old and new versions of the UI".

They disagreed. Classic implements the cadence-derived rule from
``configs/eosat1/mcs/staleness.yaml`` -- thresholds scaled to how often the
parameter is ACTUALLY downlinked. Modern judged every parameter against a flat
5 s / 15 s threshold inherited from the shared component library, which is the
exact defect that policy file was written to kill: HK SID 3 (TCS) has
``interval_s: 60``, so the thermal displays read STALE for ~45 s out of every
60, and SID 11 (beacon, 30 s) read STALE for 22 s out of every 30 -- during the
one mission phase where the beacon is the only telemetry there is.

Modern now runs the same algorithm. These tests pin the algorithm against the
config, and pin the two implementations against EACH OTHER, so the consoles
cannot drift apart again.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
STALENESS_YAML = CONFIG_DIR / "mcs" / "staleness.yaml"
CLASSIC = REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static" / "index.html"
MODERN_HARNESS = Path(__file__).resolve().parent / "js" / "modern_harness.js"
CLASSIC_HARNESS = Path(__file__).resolve().parent / "js" / "mcs_harness.js"

NODE = shutil.which("node") or shutil.which("nodejs")
requires_node = pytest.mark.skipif(NODE is None, reason="node required")

# (age_s, interval_s) probes chosen to sit on both sides of every boundary the
# policy defines, including the two real cadences that motivated the file.
CASES = [
    (0.0, 1.0), (3.0, 1.0), (11.0, 1.0), (25.0, 1.0),      # EPS 1 Hz (floors bite)
    (3.0, 4.0), (11.0, 4.0), (21.0, 4.0),                   # AOCS 4 s
    (7.0, 8.0), (11.0, 8.0), (21.0, 8.0),                   # 8 s structures
    (22.0, 30.0), (28.0, 30.0), (46.0, 30.0),               # SID 11 beacon 30 s
    (45.0, 60.0), (55.0, 60.0), (95.0, 60.0),               # SID 3 TCS 60 s
    (120.0, None), (400.0, None), (1200.0, None),           # aperiodic / on-demand
]


def _run(harness: Path, snippet: str, tmp_path: Path, name: str):
    # The two harnesses take different first arguments: the modern one loads a
    # real ES module and needs the repo root; the classic one extracts the
    # inline <script> out of a single HTML file and needs that file.
    root = str(CLASSIC) if name == "classic" else str(REPO)
    f = tmp_path / f"{name}.js"
    f.write_text(snippet)
    proc = subprocess.run([NODE, str(harness), root, str(f)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, f"{name} harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def policy() -> dict:
    return yaml.safe_load(STALENESS_YAML.read_text())["staleness"]


def _expected(age: float, interval, pol: dict) -> str:
    """The rule as the policy file states it, computed independently here."""
    if interval is None:
        fresh, stale = pol["aperiodic_fresh_s"], pol["aperiodic_stale_s"]
    else:
        fresh = max(pol["floor_fresh_s"], interval * pol["fresh_factor"])
        stale = max(pol["floor_stale_s"], interval * pol["stale_factor"], fresh)
    if age <= fresh:
        return "fresh"
    return "degraded" if age <= stale else "stale"


@requires_node
def test_modern_matches_the_configured_policy(tmp_path, policy):
    """Modern's verdict is derived from staleness.yaml, not a flat threshold."""
    probes = json.dumps(CASES)
    out = _run(MODERN_HARNESS,
               f"return {probes}.map(([a, i]) => mod.ageVerdict(a, i));",
               tmp_path, "modern")
    got = dict(zip((tuple(c) for c in CASES), out))
    for (age, interval), verdict in got.items():
        assert verdict == _expected(age, interval, policy), (
            f"modern: age {age}s on a {interval}s cadence -> {verdict!r}, "
            f"policy says {_expected(age, interval, policy)!r}")


@requires_node
def test_the_two_consoles_agree(tmp_path):
    """Same age, same cadence, same verdict -- in classic and in modern."""
    probes = json.dumps(CASES)
    modern = _run(MODERN_HARNESS,
                  f"return {probes}.map(([a, i]) => mod.ageVerdict(a, i));",
                  tmp_path, "modern")
    classic = _run(CLASSIC_HARNESS,
                   f"return {probes}.map(function (c) "
                   f"{{ return ageVerdict(c[0], c[1]); }});",
                   tmp_path, "classic")
    disagreements = [
        (case, c, m) for case, c, m in zip(CASES, classic, modern) if c != m]
    assert not disagreements, (
        "the two consoles disagree about staleness: "
        + "; ".join(f"age {a}s / cadence {i}s: classic={c} modern={m}"
                    for (a, i), c, m in disagreements))


@requires_node
def test_the_two_real_cadences_that_motivated_the_policy(tmp_path):
    """The regression that started it: TCS at 60 s and the 30 s beacon.

    Under the flat 5/15 s rule these read STALE for most of every cycle. If
    this test fails, someone has reintroduced a fixed threshold.
    """
    out = _run(MODERN_HARNESS,
               "return {tcs45: mod.ageVerdict(45, 60), "
               "beacon22: mod.ageVerdict(22, 30), "
               "tcs_really_late: mod.ageVerdict(95, 60)};",
               tmp_path, "modern")
    assert out["tcs45"] == "fresh", (
        "a 60 s TCS parameter 45 s after its last sample is not stale -- the "
        "next one is not even due yet")
    assert out["beacon22"] == "fresh", (
        "the 30 s beacon 22 s after its last sample is not stale")
    assert out["tcs_really_late"] == "stale", (
        "a 60 s parameter 95 s old HAS missed a downlink and must read stale")


# ── engineering values (field report 2026-08-20, same batch) ────────

@requires_node
def test_modern_renders_engineering_values_not_raw_codes(tmp_path):
    """aocs.mode must read NOMINAL, not 4 -- and generically, not per widget.

    The catalogue entry is injected here rather than fetched, so this pins the
    RENDERING contract (states map -> name + code) independently of whether a
    server happens to be running.
    """
    out = _run(MODERN_HARNESS, """
        const S = mod.__test.S;
        S.paramByHex["0x020F"] = { hex: "0x020F", name: "aocs.mode",
          states: { "0": "OFF", "4": "NOMINAL" } };
        S.paramByHex["0x0100"] = { hex: "0x0100", name: "eps.bat_voltage", units: "V" };
        return {
          named:    mod.engValue("0x020F", 4),
          zero:     mod.engValue("0x020F", 0),
          undef:    mod.engValue("0x020F", 7),
          missing:  mod.engValue("0x020F", null),
          numeric:  mod.engValue("0x0100", 28.4),
          unknown_param: mod.engValue("0x9999", 3)
        };
    """, tmp_path, "modern")
    assert out["named"] == "NOMINAL (4)", (
        "a discrete parameter must render its state NAME; the raw code stays "
        "in parentheses because procedures and dumps quote the number")
    assert out["zero"] == "OFF (0)", "code 0 is a state, not a missing value"
    assert out["undef"] == "UNKNOWN (7)", (
        "an undefined code must say so rather than silently show a number")
    assert out["missing"] == "—", "no value is a dash, not a state"
    assert out["numeric"] is None, (
        "a non-discrete parameter must fall through to numeric formatting, "
        "not acquire invented state names")
    assert out["unknown_param"] is None


# ── position filtering actually hides (field report 2026-08-20) ─────

MODERN_INDEX = (REPO / "packages" / "smo-mcs" / "src" / "smo_mcs" / "static"
                / "modern" / "index.html")


def test_hidden_rail_buttons_are_actually_hidden():
    """The rail sets `hidden`; the CSS has to let that mean something.

    applyPositionFilter() correctly set `hidden` on every rail button a
    position does not own -- and all fourteen tabs stayed on screen anyway,
    because `.rail button{display:grid}` is an AUTHOR rule and beats the user
    agent's `[hidden]{display:none}`. The logic was right and invisible. A
    console that shows a TT&C operator the propulsion tab is offering a page
    with no telemetry behind it.
    """
    css = MODERN_INDEX.read_text()
    assert ".rail button[hidden]" in css, (
        "`.rail button[hidden]{display:none}` is gone -- position filtering "
        "will silently stop hiding anything, because the author display rule "
        "overrides the UA [hidden] rule")
    # the guard must come after the display declaration it defends against
    assert css.index(".rail button{") < css.index(".rail button[hidden]")


@requires_node
def test_each_position_sees_only_the_tabs_it_owns(tmp_path):
    positions = yaml.safe_load(
        (CONFIG_DIR / "mcs" / "positions.yaml").read_text())
    positions = positions.get("positions", positions)
    ttc = positions["ttc"]
    out = _run(MODERN_HARNESS, f"""
        const pos = {json.dumps(ttc)};
        const ids = ["ovr","eps","tcs","aocs","ttc","pld","prp","obd",
                     "cmd","fdir","proc","trnd","diag","man"];
        return ids.filter((id) => mod.railVisibleForPosition(id, pos, false));
    """, tmp_path, "modern")
    assert "ttc" in out and "ovr" in out and "cmd" in out
    for not_owned in ("eps", "tcs", "aocs", "pld", "prp", "obd", "fdir"):
        assert not_owned not in out, (
            f"the TT&C position is offered the {not_owned} tab, which carries "
            "no telemetry it owns")
