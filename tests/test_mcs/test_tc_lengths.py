"""Defect #67 — the command builder must know how many data bytes a TC needs.

`tc_catalog.yaml` has always carried enough metadata to derive the expected
data-field length of 182 of the 188 commands the audit measured, but the MCS
builder never read it:
`PUS_FIELD_DEFS` and `FUNC_CATALOG` were hardcoded in index.html and
`/api/tc-catalog` — which returns `fields` — had never been called by any
client. The derivation now lives in `smo_mcs.tc_lengths` and is surfaced by
`/api/tc-catalog` as `expected_data_bytes` / `variable_length` /
`min_data_bytes` / `length_note`.

These tests pin the arithmetic against the real catalogue. The commands whose
length is genuinely NOT derivable are identified by shape (a `bytes`-typed
field, or no `fields` key at all) rather than by name, because the catalogue is
maintained independently of this code — those must report "variable" /
"unknown", never a wrong number.
"""
from pathlib import Path

import pytest
import yaml

from smo_mcs.tc_lengths import (
    FIELD_WIDTHS,
    expected_data_bytes,
    field_width,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"
CATALOG = CONFIG_DIR / "commands" / "tc_catalog.yaml"

# The catalogue is edited independently of this code, so the two families of
# non-derivable command are identified BY THEIR SHAPE rather than by a frozen
# list of names: a `bytes`-typed field means unbounded length, and a missing
# `fields` key means the catalogue simply does not say.
VARIABLE_FIELD_TYPES = {"bytes", "string", "blob"}


def _is_variable(cmd: dict) -> bool:
    return any(f.get("type") in VARIABLE_FIELD_TYPES for f in (cmd.get("fields") or []))


def _is_undeclared(cmd: dict) -> bool:
    return "fields" not in cmd


@pytest.fixture(scope="module")
def commands() -> list[dict]:
    data = yaml.safe_load(CATALOG.read_text())
    cmds = data["commands"] if isinstance(data, dict) else data
    assert cmds, "tc_catalog.yaml has no commands"
    return cmds


@pytest.fixture(scope="module")
def by_name(commands) -> dict[str, dict]:
    return {c["name"]: c for c in commands}


# ── the width table ─────────────────────────────────────────────────

class TestFieldWidths:

    def test_every_field_type_in_the_catalogue_is_known(self, commands):
        """A new field type must not silently produce a wrong byte count."""
        seen = set()
        for c in commands:
            for f in c.get("fields") or []:
                seen.add(f["type"])
        unknown = {t for t in seen
                   if field_width(t) is None and t not in ("bytes", "string", "blob")}
        assert not unknown, f"unhandled catalogue field types: {unknown}"

    @pytest.mark.parametrize("ftype,width", [
        ("uint8", 1), ("int16", 2), ("uint16", 2),
        ("uint32", 4), ("float32", 4),
    ])
    def test_scalar_widths(self, ftype, width):
        assert FIELD_WIDTHS[ftype] == width
        assert field_width(ftype) == width

    def test_bytes_is_variable(self):
        assert field_width("bytes") is None


# ── derivation over the real catalogue ──────────────────────────────

class TestDerivationOverCatalogue:

    def test_the_overwhelming_majority_of_commands_are_derivable(self, commands):
        """The audit measured 182 of 188; the ratio is what matters."""
        derivable = [c for c in commands
                     if expected_data_bytes(c)["expected_data_bytes"] is not None]
        assert len(derivable) / len(commands) > 0.95

    def test_only_variable_or_undeclared_commands_lack_a_number(self, commands):
        """Nothing else may fall through to "unknown"."""
        for c in commands:
            info = expected_data_bytes(c)
            if info["expected_data_bytes"] is None:
                assert _is_variable(c) or _is_undeclared(c), (
                    f"{c['name']} has a fully typed field list but no derived length"
                )
            else:
                assert not _is_variable(c) and not _is_undeclared(c), c["name"]

    def test_variable_length_commands_report_variable_not_a_number(self, commands):
        variable = [c for c in commands if _is_variable(c)]
        assert variable, "expected at least one bytes-typed command"
        for c in variable:
            info = expected_data_bytes(c)
            assert info["expected_data_bytes"] is None, c["name"]
            assert info["variable_length"] is True, c["name"]
            assert info["min_data_bytes"] >= 0
            assert "variable" in info["length_note"]

    def test_commands_without_a_fields_key_are_unknown_not_zero(self, commands):
        undeclared = [c for c in commands if _is_undeclared(c)]
        if not undeclared:
            # The shipped catalogue is now fully declared: TTC_SWITCH_PRIMARY /
            # TTC_SWITCH_REDUNDANT gained an explicit `fields: []` (defect #80),
            # so no entry is "undeclared" any more.  The unknown-vs-zero
            # behaviour of the derivation itself is still asserted by
            # TestRobustness.test_missing_fields_key_on_a_non_s8_command_is_unknown.
            pytest.skip("catalogue has no undeclared commands (defect #80)")
        for c in undeclared:
            info = expected_data_bytes(c)
            assert info["expected_data_bytes"] is None, c["name"]
            assert info["variable_length"] is False, c["name"]
            # S8.1 entries still know about the leading func_id byte.
            expected_min = 1 if (c.get("service"), c.get("subtype")) == (8, 1) else 0
            assert info["min_data_bytes"] == expected_min
            assert "no \'fields\' key" in info["length_note"]


class TestRepresentativeSample:
    """Hand-checked byte counts across the service range."""

    @pytest.mark.parametrize("name,expected", [
        # S2.1 device command: device_id uint16 + state uint8
        ("S2_EPS_BATTERY_HEATER", 3),
        # S3.5 / S3.6 / S3.27: sid uint16
        ("HK_ENABLE", 2),
        ("HK_DISABLE", 2),
        # S3.31: sid uint16 + interval float32
        ("HK_SET_INTERVAL", 6),
        # S9.1: cuc uint32
        ("SET_TIME", 4),
        # S15: store_id uint8
        ("ENABLE_STORE", 1),
        ("DELETE_STORE", 1),
        # S15.13 takes no arguments
        ("STORE_STATUS", 0),
        # S17.1 connection test
        ("CONNECTION_TEST", 0),
    ])
    def test_named_command_lengths(self, by_name, name, expected):
        if name not in by_name:
            pytest.skip(f"{name} not present in this catalogue revision")
        assert expected_data_bytes(by_name[name])["expected_data_bytes"] == expected

    def test_s8_1_includes_the_leading_func_id_byte(self, commands):
        """`func_id` is a sibling key, not a field — the dispatcher strips it."""
        for c in commands:
            if c.get("service") == 8 and c.get("subtype") == 1 and "fields" in c:
                declared = sum(field_width(f["type"]) or 0 for f in (c["fields"] or []))
                info = expected_data_bytes(c)
                if info["expected_data_bytes"] is not None:
                    assert info["expected_data_bytes"] == declared + 1, c["name"]

    def test_no_arg_s8_functions_are_exactly_one_byte(self, commands):
        no_arg = [c for c in commands
                  if c.get("service") == 8 and c.get("subtype") == 1
                  and c.get("fields") == []]
        assert no_arg, "expected some parameter-less S8.1 functions"
        for c in no_arg:
            assert expected_data_bytes(c)["expected_data_bytes"] == 1, c["name"]

    def test_non_s8_commands_get_no_func_id_byte(self, by_name):
        # STORE_STATUS has fields: [] and is S15.13 -> zero, not one.
        assert expected_data_bytes(by_name["STORE_STATUS"])["expected_data_bytes"] == 0


class TestRobustness:

    def test_missing_fields_key_on_a_non_s8_command_is_unknown(self):
        info = expected_data_bytes({"service": 3, "subtype": 5})
        assert info["expected_data_bytes"] is None
        assert info["min_data_bytes"] == 0

    def test_unrecognised_field_type_does_not_produce_a_number(self):
        info = expected_data_bytes({
            "service": 2, "subtype": 1,
            "fields": [{"name": "x", "type": "quaternion"}],
        })
        assert info["expected_data_bytes"] is None
        assert info["variable_length"] is False
        assert "quaternion" in info["length_note"]

    def test_empty_command_is_zero_bytes(self):
        info = expected_data_bytes({"service": 17, "subtype": 1, "fields": []})
        assert info["expected_data_bytes"] == 0
