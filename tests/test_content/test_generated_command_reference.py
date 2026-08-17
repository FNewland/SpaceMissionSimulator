"""Phase E WP-12 (brief 04 drift gate 4) — generated artefacts stay in step.

``configs/eosat1/manual/09_command_reference.md`` and the "Commandable
settings" table in ``06_payload.md`` are generated from
``tc_catalog.yaml``; the XTCE/SMDL exports likewise. Regenerating must be a
no-op, so the manual, the exports and the spacecraft cannot disagree — the
failure mode this whole phase exists to close (the shipped reference listed
one of fourteen payload commands and two of seventeen TT&C commands).
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from smo_common.commanding import encode, load_catalog

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "configs" / "eosat1"
REFERENCE = CONFIG / "manual" / "09_command_reference.md"
PAYLOAD_MANUAL = CONFIG / "manual" / "06_payload.md"
CATALOG = load_catalog(CONFIG)


def _run(*args):
    return subprocess.run([sys.executable, *args], cwd=REPO,
                          capture_output=True, text=True, timeout=300)


class TestReferenceIsGeneratedAndCurrent:

    def test_reference_exists_and_is_marked_generated(self):
        text = REFERENCE.read_text()
        assert "GENERATED FILE — DO NOT EDIT BY HAND" in text
        assert "tools/gen_command_reference.py" in text

    def test_regeneration_is_a_no_op(self):
        """The CI diff gate: `--check` must pass against the committed file."""
        proc = _run(str(REPO / "tools" / "gen_command_reference.py"), "--check")
        assert proc.returncode == 0, \
            ("09_command_reference.md is stale — re-run "
             f"tools/gen_command_reference.py\n{proc.stdout}{proc.stderr}")

    def test_every_catalogue_command_appears(self):
        text = REFERENCE.read_text()
        missing = [c["name"] for c in CATALOG.commands
                   if f"#### {c['name']}" not in text]
        assert not missing, f"commands absent from the reference: {missing}"

    def test_payload_commands_are_all_documented(self):
        """The specific gap report (b) named: 1 of 14 payload commands."""
        text = REFERENCE.read_text()
        payload = [c for c in CATALOG.commands
                   if (c["service"], c["subtype"]) == (8, 1)
                   and int(c.get("func_id", -1)) in
                   list(range(26, 40)) + list(range(92, 96))]
        assert len(payload) >= 18
        for c in payload:
            assert f"#### {c['name']}" in text, c["name"]

    def test_example_hex_matches_the_shared_encoder(self):
        """Worked examples are computed, not typed."""
        text = REFERENCE.read_text()
        for name, args, expected in [
            ("PAYLOAD_COOLER_SETPOINT", {"setpoint": -15.0}, "24 64 D7"),
            ("PAYLOAD_CAPTURE", {"scene_id": 0, "lines": 2048}, "1C 00 00 08 00"),
            ("TTC_SET_TX_POWER", {"level": 1}, "44 01"),
        ]:
            wire = " ".join(f"{b:02X}" for b in
                            encode(name, args, catalog=CATALOG))
            assert wire == expected
            assert f"`{expected}`" in text, f"{name} example {expected} absent"

    def test_units_ranges_and_enums_are_rendered(self):
        text = REFERENCE.read_text()
        # engineering unit + calibration + range for the FPA setpoint
        assert "eng = raw x 0.01 - 273.15 (cK/LSB)" in text
        assert "-20.0 … 0.0" in text
        # enum table for the TX power levels, with the CORRECTED watt table
        assert "`LOW`" in text and "1.0 W RF" in text
        assert "5.0 W RF" in text
        # bit table for the band mask
        assert "`BLUE`" in text and "443 nm" in text
        assert "reserved — must be 0" in text


class TestPayloadManualSettingsTable:

    def test_generated_block_present(self):
        text = PAYLOAD_MANUAL.read_text()
        assert "BEGIN GENERATED: payload commandable settings" in text
        assert "END GENERATED: payload commandable settings" in text

    def test_every_payload_command_is_in_the_table(self):
        text = PAYLOAD_MANUAL.read_text()
        block = text.split("BEGIN GENERATED")[1].split("END GENERATED")[0]
        for c in CATALOG.commands:
            if (c["service"], c["subtype"]) != (8, 1):
                continue
            fid = int(c.get("func_id", -1))
            if fid in list(range(26, 40)) + list(range(92, 96)):
                assert f"`{c['name']}`" in block, c["name"]


class TestGeneratedExportsAreCurrent:

    @pytest.mark.parametrize("tool,target", [
        ("tools/generate_xtce.py", "configs/eosat1/xtce/eosat1.xml"),
        ("tools/generate_smdl.py", "configs/eosat1/smdl/payload.xml"),
        # brief 08 documentation gap: the parameter/SID/limit/S12 map is
        # generated from config in CI, so it cannot describe a configuration
        # that no longer exists. Findings 12 and 13 are one glance at it.
        ("tools/gen_parameter_map.py", "docs/PARAMETER_MAP.md"),
    ])
    def test_regeneration_is_byte_stable(self, tool, target, tmp_path):
        before = (REPO / target).read_bytes()
        proc = _run(str(REPO / tool))
        assert proc.returncode == 0, proc.stderr
        after = (REPO / target).read_bytes()
        assert before == after, (
            f"{target} is stale — re-run {tool} and commit the result")

    GENERATORS = [
        "tools/gen_command_reference.py",
        "tools/generate_xtce.py",
        "tools/generate_smdl.py",
        "tools/gen_parameter_map.py",
        "tools/xtce_smdl_common.py",
    ]

    ARTEFACTS = [
        "configs/eosat1/manual/09_command_reference.md",
        "configs/eosat1/xtce/eosat1.xml",
        "configs/eosat1/smdl/payload.xml",
        "configs/eosat1/smdl/eosat1_catalogue.xml",
        "docs/PARAMETER_MAP.md",
    ]

    @pytest.mark.parametrize("source", GENERATORS)
    def test_generators_do_not_read_the_wall_clock(self, source):
        """A generated file must be a pure function of its inputs.

        A ``**Date:** <today>`` stamp made every byte-diff gate above fail on
        every calendar day but the one the reference was generated on.
        Provenance is carried by ``tools/source_revision.py`` instead, which
        digests the input files.
        """
        text = (REPO / source).read_text()
        for clock in ("date.today(", "datetime.now(", "utcnow(", "time.time("):
            assert clock not in text, (
                f"{source} stamps a wall-clock value into a generated "
                f"artefact ({clock}); use source_revision() instead")

    @pytest.mark.parametrize("target", ARTEFACTS)
    def test_artefacts_carry_no_wall_clock_date(self, target):
        """The byte-diff gates must pass on any calendar day."""
        text = (REPO / target).read_text()
        stamps = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        assert not stamps, f"{target} embeds a date stamp: {sorted(set(stamps))}"

    def test_xtce_command_metadata_is_conformant(self):
        xml = (REPO / "configs" / "eosat1" / "xtce" / "eosat1.xml").read_text()
        # Phase E WP-11: byte layout, calibrators, ranges and enums.
        assert "CommandContainer" in xml
        assert 'FixedValueEntry name="func_id" binaryValue="24"' in xml
        assert "PAYLOAD_COOLER_SETPOINT_setpoint_ArgType" in xml
        assert "EnumeratedArgumentType" in xml
        assert 'coefficient="-273.15"' in xml
        assert 'minInclusive="-20.0"' in xml
        # the YAML `on:` -> boolean True bug must stay dead
        assert 'name="True"' not in xml
