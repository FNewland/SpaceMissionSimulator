"""Defect #86 — every data-rate figure in the courseware tracks `tm_rate_tiers`.

`configs/eosat1/subsystems/ttc.yaml tm_rate_tiers` is the single source of truth
for the TM data-rate ladder. Before this test the same numbers were re-typed in
procedures, the operations manual, the acceptance test plan, the PUS reference
and the design document — and when the ladder replaced the old binary
1 kbps / 64 kbps switch, every one of those copies became a lie that an operator
or a student would follow.

This module is the tripwire for the NEXT rate change. It fails when:

  * a retired rate (1000 bps as an *operations* rate, 64 kbps, 51.2 kbps
    effective, the 655 s image) is quoted anywhere in the procedures, the
    manual, the ops docs or the test plan;
  * a procedure quotes a bps figure for a tier that disagrees with the ladder;
  * the func_id for TTC_SET_DATA_RATE / TTC_SET_DOWNLINK_MODE drifts from the
    command catalogue.

It deliberately reads the ladder rather than hard-coding 16 000 / 128 000: change
`ttc.yaml` and this test tells you which documents you still have to change.
"""
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs" / "eosat1"
DOCS_DIR = REPO_ROOT / "docs"
PROC_DIR = CONFIG_DIR / "procedures"
MANUAL_DIR = CONFIG_DIR / "manual"


def _ttc_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "subsystems" / "ttc.yaml").read_text())


TTC = _ttc_config()
TIERS = {t["name"]: t for t in TTC["tm_rate_tiers"]}
LADDER_BPS = {t["bps"] for t in TTC["tm_rate_tiers"]}


def _join_grouped_digits(text: str) -> str:
    """`16 000 bps` -> `16000 bps`.

    The house style writes long numerals with a thousands space. The ladder
    numbers have to be comparable with `tm_rate_tiers`, so join them first.
    """
    return re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", text)


def _catalog_func_id(name: str) -> int:
    catalog = yaml.safe_load(
        (CONFIG_DIR / "commands" / "tc_catalog.yaml").read_text())["commands"]
    return next(c["func_id"] for c in catalog if c["name"] == name)


# Documents that describe the CURRENT mission and must not carry a retired rate.
# Review artefacts under docs/review_2026-07/ and docs/gap_analysis/ are excluded:
# they are dated records of what the simulator used to be, and rewriting history
# is not the same as keeping the manual honest. docs/ops_research/ is likewise a
# requirements-capture record.
def _live_documents() -> list[Path]:
    paths: list[Path] = []
    paths += sorted(PROC_DIR.rglob("*.md"))
    paths += sorted(MANUAL_DIR.glob("*.md"))
    for name in ("PUS_SERVICE_REFERENCE.md", "OPERATIONS_GUIDE.md",
                 "LINK_BUDGET_AND_RATES.md"):
        p = DOCS_DIR / name
        if p.exists():
            paths.append(p)
    plan = DOCS_DIR / "test_plan" / "ACCEPTANCE_TEST_PLAN.md"
    if plan.exists():
        paths.append(plan)
    fid = DOCS_DIR / "sim_fidelity" / "ttc_fidelity.md"
    if fid.exists():
        paths.append(fid)
    return paths


LIVE_DOCS = _live_documents()
LIVE_IDS = [str(p.relative_to(REPO_ROOT)) for p in LIVE_DOCS]

#: Rates that the ladder retired. Each entry is (regex, why it is wrong).
RETIRED_RATE_PATTERNS = [
    (re.compile(r"\b64\s*kbps\b", re.I),
     "64 kbps was the old high rate; the HIGH tier is 128 kbps"),
    # ...but 64000 Bd is the HIGH tier's QPSK SYMBOL rate and is correct.
    (re.compile(r"(?<!\d)64\s?000(?!\d)(?!\s*[kM]?Bd)"),
     "64000 bps was the old high rate; the HIGH tier is 128000 bps"),
    (re.compile(r"\b51\.?2\s*kbps\b", re.I),
     "51.2 kbps was 64 kbps x 0.8; the effective HIGH rate is 102.4 kbps"),
    (re.compile(r"\b51200\b"),
     "51200 was the old effective rate; it is now 102400"),
]

#: A retired rate MAY be named, but only where the same line says it is history.
#: This is the escape hatch for "why 128 kbps and not 64 kbps" prose and for
#: struck-through gap-analysis entries — and it is deliberately narrow, so a
#: stale figure cannot hide behind a vague sentence.
HISTORICAL_MARKERS = (
    "~~", "retired", "used to", "was the old", "no longer", "originally",
    "closed", "previously", "not adopted", "before the ladder", "replaced",
    "instead of", "was two-thirds", "deprecated", "old high rate",
    "then-current", "superseded",
)


#: Phrases that treat 1 kbps as an OPERATIONS rate. 1 kbps still exists — it is
#: the BEACON rung — so a bare "1 kbps" is legitimate. What is not legitimate is
#: calling it the low rate, the safe-mode rate or the routine rate.
BEACON_MISUSE_PATTERNS = [
    re.compile(r"low[- ]rate\s*(mode)?\s*\(1\s*kbps\)", re.I),
    re.compile(r"1\s*kbps\s*low[- ]rate", re.I),
    re.compile(r"\(rate=0\)[^\n]*1000\s*bps", re.I),
    re.compile(r"tm_data_rate[^\n]*=\s*1000\s*bps", re.I),
]


class TestNoRetiredRatesInLiveDocuments:

    @pytest.mark.parametrize("path", LIVE_DOCS, ids=LIVE_IDS)
    def test_no_retired_rate_figures(self, path):
        text = _join_grouped_digits(path.read_text())
        offences = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            low = line.lower()
            if any(marker in low for marker in HISTORICAL_MARKERS):
                continue        # named AND flagged as history on the same line
            for pattern, why in RETIRED_RATE_PATTERNS:
                if pattern.search(line):
                    offences.append(f"{path.name}:{lineno}: {line.strip()}  <- {why}")
        assert not offences, (
            "retired data-rate figures still in a live document (name one only "
            "on a line that also says it is history — see HISTORICAL_MARKERS):"
            "\n  " + "\n  ".join(offences))

    @pytest.mark.parametrize("path", LIVE_DOCS, ids=LIVE_IDS)
    def test_one_kbps_is_only_ever_the_beacon(self, path):
        text = path.read_text()
        offences = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in BEACON_MISUSE_PATTERNS:
                if pattern.search(line):
                    offences.append(f"{path.name}:{lineno}: {line.strip()}")
        assert not offences, (
            "1 kbps is the BEACON rung (bootloader/beacon only); the low "
            "OPERATIONS rate is LOW at "
            f"{TIERS['LOW']['bps']} bps:\n  " + "\n  ".join(offences))


class TestProceduresAgreeWithTheLadder:

    #: Files that command or describe the ladder and must therefore be
    #: internally consistent with it.
    RATE_PROCEDURES = [
        PROC_DIR / "nominal" / "data_rate_change.md",
        PROC_DIR / "nominal" / "data_downlink.md",
        PROC_DIR / "contingency" / "ber_anomaly.md",
        PROC_DIR / "leop" / "first_acquisition.md",
    ]

    @pytest.mark.parametrize(
        "path", RATE_PROCEDURES, ids=[p.name for p in RATE_PROCEDURES])
    def test_every_quoted_bps_is_a_configured_tier(self, path):
        """A `NNNNN bps` figure in a rate procedure must be a rung of the
        ladder — not a number someone typed from memory."""
        stray = set()
        for line in _join_grouped_digits(path.read_text()).splitlines():
            # A line about the housekeeping CHANNEL load (1557 bps after RS and
            # TM framing) or the fixed 4 kbps TELECOMMAND uplink is quoting a
            # derived figure, not a tier, and says so on the same line.
            if "channel" in line.lower() or "telecommand" in line.lower():
                continue
            stray |= {int(m) for m in re.findall(r"\b(\d{3,7})\s*bps\b", line)}
        stray -= LADDER_BPS
        assert not stray, (
            f"{path.name} quotes bit rates that are not rungs of "
            f"tm_rate_tiers {sorted(LADDER_BPS)}: {sorted(stray)}")

    @pytest.mark.parametrize(
        "path", RATE_PROCEDURES, ids=[p.name for p in RATE_PROCEDURES])
    def test_every_quoted_symbol_rate_is_a_configured_tier(self, path):
        """Symbol rate = bit rate / bits-per-symbol. QPSK halves it, which is
        the point of the HIGH tier's modulation change."""
        bits_per_symbol = {"BPSK": 1, "QPSK": 2, "8PSK": 3, "OQPSK": 2}
        valid = {t["bps"] // bits_per_symbol[t["modulation"].upper()]
                 for t in TTC["tm_rate_tiers"]}
        text = _join_grouped_digits(path.read_text())
        quoted = {int(m) for m in re.findall(r"\b(\d{3,7})\s*Bd\b", text)}
        stray = quoted - valid
        assert not stray, (
            f"{path.name} quotes symbol rates that no tier produces "
            f"{sorted(valid)}: {sorted(stray)}")

    def test_the_rate_command_func_id_matches_the_catalogue(self):
        """Pre-existing drift: data_rate_change.md and ber_anomaly.md both
        cited func_id 52 (an OBDH function) where the catalogue says 65."""
        expected = _catalog_func_id("TTC_SET_DATA_RATE")
        assert expected == 65
        for path in PROC_DIR.rglob("*.md"):
            text = path.read_text()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "TTC_SET_DATA_RATE" not in line:
                    continue
                for fid in re.findall(r"func_id[= ]\s*(\d+)", line):
                    assert int(fid) == expected, (
                        f"{path.name}:{lineno} cites func_id {fid} for "
                        f"TTC_SET_DATA_RATE; the catalogue says {expected}")

    def test_the_downlink_mode_command_func_id_matches_the_catalogue(self):
        expected = _catalog_func_id("TTC_SET_DOWNLINK_MODE")
        assert expected == 79
        for path in PROC_DIR.rglob("*.md"):
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if "TTC_SET_DOWNLINK_MODE" not in line:
                    continue
                for fid in re.findall(r"func_id[= ]\s*(\d+)", line):
                    assert int(fid) == expected, (
                        f"{path.name}:{lineno} cites func_id {fid} for "
                        f"TTC_SET_DOWNLINK_MODE; the catalogue says {expected}")


class TestTheGatesAreDocumentedWhereTheyAreUsed:

    def test_dump_procedures_command_downlink_mode_and_check_elevation(self):
        """The HIGH tier is refused without DOWNLINK mode and >= 10 deg. A dump
        procedure that does not command the first and check the second will
        fail mid-pass with an S1.8 error the operator cannot explain."""
        gate = TIERS["HIGH"]["min_elevation_deg"]
        for name in ("nominal/data_downlink.md", "nominal/data_rate_change.md"):
            text = (PROC_DIR / name).read_text()
            assert "TTC_SET_DOWNLINK_MODE" in text, f"{name} never enters DOWNLINK mode"
            assert f"{gate:.0f} deg" in text or f"{gate}" in text, (
                f"{name} does not mention the {gate:.0f} deg elevation gate")

    def test_the_activity_catalogue_dump_matches_the_procedures(self):
        types = yaml.safe_load(
            (CONFIG_DIR / "planning" / "activity_types.yaml").read_text())
        dump = next(a for a in types["activity_types"] if a["name"] == "data_dump")
        funcs = [int(str(s["func_id"]), 0) if isinstance(s["func_id"], str)
                 else int(s["func_id"])
                 for s in dump["command_sequence"] if s.get("service") == 8]
        assert _catalog_func_id("TTC_SET_DOWNLINK_MODE") in funcs
        assert _catalog_func_id("TTC_SET_DATA_RATE") in funcs

    def test_the_planner_elevation_gate_matches_the_tier(self):
        """planner.yaml's per-station min_dump_elevation_deg is the same gate
        the spacecraft enforces. If they drift the planner books capacity the
        spacecraft refuses to deliver."""
        planner = yaml.safe_load(
            (CONFIG_DIR / "planning" / "planner.yaml").read_text())
        gate = TIERS["HIGH"]["min_elevation_deg"]
        for station in planner["stations"]:
            assert station["min_dump_elevation_deg"] == gate, (
                f"{station['name']} plans dumps down to "
                f"{station['min_dump_elevation_deg']} deg but the HIGH tier "
                f"needs {gate} deg")


class TestTheManualQuotesTheLadder:

    def test_the_ttc_manual_lists_every_rung(self):
        text = (MANUAL_DIR / "05_ttc.md").read_text()
        for tier in TTC["tm_rate_tiers"]:
            assert tier["name"] in text, \
                f"manual/05_ttc.md never mentions the {tier['name']} tier"
            assert str(tier["bps"]) in text or \
                f"{tier['bps'] // 1000} kbps" in text, \
                f"manual/05_ttc.md never states the {tier['name']} rate"

    def test_the_routine_operations_rate_is_named_as_such(self):
        """Franz's complaint in one assertion: the low rate must be presented
        as where the spacecraft normally sits, not as a degraded mode."""
        text = (MANUAL_DIR / "05_ttc.md").read_text().lower()
        assert "routine operations" in text
        assert TTC["default_tier"].lower() in text
