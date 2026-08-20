"""Engineering values for discrete telemetry — derived from config, never invented.

Field report 2026-08-20: "check in the new version that parameters are
displayed in engineering values, e.g. for AOCS mode - but this may be a generic
issue - at the minute only the raw values seem to display".

It was generic: ``telemetry/parameters.yaml`` has no structured field for state
codes, so every catalogue-driven display printed a raw number. Where the codes
exist at all they are written in prose inside ``description``. These tests pin
that the derivation reads that existing truth correctly, that the one canonical
codes file wins where it exists, and — most importantly — that nothing is
fabricated for parameters whose codes are NOT documented.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smo_common.telemetry.eng_values import (
    build_state_tables,
    format_value,
    load_canonical_state_files,
    parse_states_from_description,
    states_for,
)

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "configs" / "eosat1"
AOCS_CODES = CONFIG_DIR / "aocs" / "aocs_mode_codes.yaml"


@pytest.fixture(scope="module")
def parameters() -> list[dict]:
    return yaml.safe_load(
        (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())["parameters"]


@pytest.fixture(scope="module")
def tables(parameters) -> dict[int, dict[int, str]]:
    return build_state_tables(parameters, CONFIG_DIR)


class TestParsingProse:
    def test_reads_a_documented_state_list(self):
        got = parse_states_from_description(
            "Gyro bias calibration state: 0=IDLE, 1=RUNNING, 2=COMPLETE, 3=ABORTED")
        assert got == {0: "IDLE", 1: "RUNNING", 2: "COMPLETE", 3: "ABORTED"}

    def test_a_single_mention_is_not_a_state_table(self):
        """The guard that keeps prose from becoming fabricated engineering units.

        This is a real description (payload.cal_valid_mask): it mentions one
        value in passing. Treating it as an enum would make the console assert
        a state name the spacecraft never defines.
        """
        assert parse_states_from_description(
            "Bands with BOTH a bias and a gain coefficient "
            "(bits 0-3 blue/green/red/nir; 0x0F = fully calibrated)") == {}

    def test_no_description_yields_nothing(self):
        assert parse_states_from_description("") == {}
        assert parse_states_from_description(None) == {}

    def test_a_bare_range_is_not_a_state_table(self):
        assert parse_states_from_description("Duty cycle 0-100 percent") == {}

    def test_explicit_states_beat_prose(self):
        p = {"id": 0x9999, "description": "0=A, 1=B", "states": {0: "REAL", 1: "TRUTH"}}
        assert states_for(p) == {0: "REAL", 1: "TRUTH"}


class TestCanonicalSource:
    def test_aocs_mode_comes_from_the_canonical_codes_file(self, tables):
        """0x020F carries no description at all; its codes live in their own
        file, whose header declares it canonical. The console must use that
        rather than a fourth hardcoded copy."""
        canonical = yaml.safe_load(AOCS_CODES.read_text())["modes"]
        expected = {int(k): str(v["name"]) for k, v in canonical.items()}
        assert tables[0x020F] == expected
        assert expected[4] == "NOMINAL"       # the operator's reported example

    def test_the_registry_is_explicit(self):
        """A file is canonical because a human said so, not because of its name."""
        reg = load_canonical_state_files(CONFIG_DIR)
        assert set(reg) == {0x020F}

    def test_a_missing_codes_file_is_not_fatal(self, tmp_path):
        assert load_canonical_state_files(tmp_path) == {}


class TestAgainstTheRealCatalogue:
    def test_a_useful_number_of_parameters_are_covered(self, tables):
        # Not a target to game: just proof the derivation found the real,
        # already-authored state lists rather than a handful of accidents.
        assert len(tables) >= 50

    def test_non_discrete_parameters_get_no_states(self, tables):
        """A voltage has no state names. Inventing some would be worse than
        showing the number."""
        for pid in (0x0100,      # eps.bat_voltage
                    0x0406):     # tcs.temp_obc
            assert pid not in tables

    def test_every_derived_name_is_non_empty_and_codes_are_ints(self, tables):
        for pid, table in tables.items():
            assert table, f"0x{pid:04X} has an empty state table"
            for code, name in table.items():
                assert isinstance(code, int)
                assert isinstance(name, str) and name.strip()

    def test_states_do_not_swallow_trailing_prose(self, tables):
        """Names stop at the clause break; a name is a name, not a sentence."""
        for pid, table in tables.items():
            for name in table.values():
                assert len(name) <= 30, f"0x{pid:04X} state name looks like prose: {name!r}"


class TestFormatting:
    def test_a_discrete_value_reads_as_a_name_keeping_the_code(self, tables):
        # The raw code stays visible: procedures, dumps and S12 limits all
        # quote the number, so hiding it would break the operator's crosscheck.
        assert format_value(4, tables[0x020F]) == "NOMINAL (4)"

    def test_an_undefined_code_says_so_rather_than_guessing(self, tables):
        assert format_value(99, tables[0x020F]) == "UNKNOWN (99)"

    def test_a_plain_number_keeps_its_units(self):
        assert format_value(28.4, None, "V") == "28.4 V"

    def test_missing_value_is_a_dash_not_a_zero(self, tables):
        assert format_value(None, tables[0x020F]) == "—"
