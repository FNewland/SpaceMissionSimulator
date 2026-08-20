"""The sent-commands report must name the command, not just number it.

Field report 2026-08-20: "In the delayed TM reports for 'sent commands', I only
have the sequence counter and command execution state - it would be helpful to
have the command name and parameters too if possible."

"If possible" was the right hedge. It is NOT possible from the dump: the
section is built from PUS Service 1 verification telemetry, and an S1 report
identifies a telecommand by request-id — by protocol it carries no service, no
subtype and no arguments. The identity lives on the ground, in the MCS's
``tc_log``, and the CCSDS sequence count is the key both sides share.

So this is a join, and these tests pin both halves of it: that the join works
when the log is present, and that the report stays honest when it is not.
"""
from __future__ import annotations

import pytest

from smo_common.reporting.analysis import commands_view
from smo_common.reporting.dataset import TelemetryDataset
from smo_common.reporting.periods import Period


def _verif(seq: int, subtype: int, t: float = 100.0) -> dict:
    return {"request_id": (1 << 14) | seq, "tc_apid": 1, "tc_seq": seq,
            "t": t, "subtype": subtype, "error_code": 0, "reason": ""}


@pytest.fixture
def period() -> Period:
    return Period(start_cuc=0.0, end_cuc=1000.0, kind="manual", label="test")


@pytest.fixture
def dataset() -> TelemetryDataset:
    ds = TelemetryDataset(catalogs=None)
    ds.verifications = [_verif(7, 1), _verif(7, 7), _verif(9, 1), _verif(9, 2)]
    return ds


class TestWithoutTheGroundLog:
    def test_rows_still_render_and_claim_nothing(self, dataset, period):
        rows = commands_view(dataset, period)
        assert len(rows) == 2
        for r in rows:
            assert r["name"] == "", "a report must not invent a command name"
            assert r["service"] is None
            assert r["args"] == ""
        # the verification story is unaffected
        assert {r["seq"]: r["outcome"] for r in rows} == {
            7: "COMPLETED", 9: "REJECTED"}


class TestWithTheGroundLog:
    def test_the_join_names_the_command(self, dataset, period):
        dataset.command_log = {
            7: {"name": "AOCS_SET_MODE", "service": 8, "subtype": 1,
                "data_hex": "37 04"},
            9: {"name": "SYS_SET_MISSION_PHASE", "service": 8, "subtype": 1,
                "data_hex": "01 06"},
        }
        rows = {r["seq"]: r for r in commands_view(dataset, period)}
        assert rows[7]["name"] == "AOCS_SET_MODE"
        assert rows[7]["service"] == 8 and rows[7]["subtype"] == 1
        assert rows[9]["name"] == "SYS_SET_MISSION_PHASE"
        # the outcome column is untouched by the join
        assert rows[9]["outcome"] == "REJECTED"

    def test_a_command_missing_from_the_log_stays_blank(self, dataset, period):
        """A TC sent before the log existed must not borrow another's name."""
        dataset.command_log = {7: {"name": "AOCS_SET_MODE", "service": 8,
                                   "subtype": 1, "data_hex": "37 04"}}
        rows = {r["seq"]: r for r in commands_view(dataset, period)}
        assert rows[7]["name"] == "AOCS_SET_MODE"
        assert rows[9]["name"] == ""

    def test_undecodable_arguments_fall_back_to_raw_hex(self, dataset, period):
        """Raw hex is still information; a wrong decode is not."""
        dataset.command_log = {7: {"name": "UNKNOWN_CMD", "service": 99,
                                   "subtype": 99, "data_hex": "DE AD BE EF"}}
        rows = {r["seq"]: r for r in commands_view(dataset, period)}
        assert "DEADBEEF" in rows[7]["args"].replace(" ", "").upper()

    def test_an_empty_payload_is_not_reported_as_arguments(self, dataset, period):
        dataset.command_log = {7: {"name": "S9_REPORT", "service": 9,
                                   "subtype": 2, "data_hex": ""}}
        rows = {r["seq"]: r for r in commands_view(dataset, period)}
        assert rows[7]["args"] == ""
        assert rows[7]["name"] == "S9_REPORT"


class TestRendering:
    def test_the_table_gains_the_columns_and_states_its_provenance(
            self, dataset, period):
        from smo_common.reporting.render_html import _render_commands
        from smo_common.reporting.spec import CommandsSection

        dataset.command_log = {
            7: {"name": "AOCS_SET_MODE", "service": 8, "subtype": 1,
                "data_hex": "37 04"}}
        sec = CommandsSection(type="commands", id="cmds", title="Commands")
        html = _render_commands(sec, dataset, period)
        assert "<th>Command</th>" in html and "<th>Arguments</th>" in html
        assert "AOCS_SET_MODE" in html
        # 1 of 2 named -- the caption must say so rather than implying all
        assert "1 of 2 named" in html

    def test_no_log_says_why_rather_than_looking_broken(self, dataset, period):
        from smo_common.reporting.render_html import _render_commands
        from smo_common.reporting.spec import CommandsSection

        sec = CommandsSection(type="commands", id="cmds", title="Commands")
        html = _render_commands(sec, dataset, period)
        assert "no ground command log" in html
        assert "request-id" in html
