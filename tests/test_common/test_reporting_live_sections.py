"""Console-log and pass-rollup report sections (POSITION_NEEDS G8 + G14).

These two sections consume live stores (MCS /api/console-log, rfsim
/api/pass-rollups) over HTTP. Contract under test: reachable ⇒ entries in
the period render as a table; unreachable ⇒ an explicit notice — never
fabricated rows. Fetching is monkeypatched at the module boundary
(render_html._fetch_json) so no server is needed.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.protocol.ecss_packet import (  # noqa: E402
    cuc_seconds_to_datetime,
    datetime_to_cuc_seconds,
)
from smo_common.reporting import load_spec  # noqa: E402
from smo_common.reporting import render_html  # noqa: E402
from smo_common.reporting.periods import Period  # noqa: E402
from smo_common.reporting.render_html import (  # noqa: E402
    _render_console_log,
    _render_pass_rollups,
)
from smo_common.reporting.spec import (  # noqa: E402
    ConsoleLogSection,
    PassRollupSection,
)

CONFIG_DIR = REPO / "configs" / "eosat1"

T0 = datetime(2026, 3, 10, 4, 0, 0, tzinfo=timezone.utc)
T0_CUC = datetime_to_cuc_seconds(T0)
T0_EPOCH = T0.timestamp()

PERIOD = Period(T0_CUC, T0_CUC + 600, "absolute", "test pass")


def test_spec_accepts_the_new_section_types():
    spec = load_spec({
        "schema_version": 1,
        "report": {"id": "x", "title": "X"},
        "sections": [
            {"type": "console_log", "id": "log"},
            {"type": "pass_rollup", "id": "rf"},
        ],
    })
    assert isinstance(spec.sections[0], ConsoleLogSection)
    assert isinstance(spec.sections[1], PassRollupSection)


def test_shipped_pass_report_template_carries_both_sections():
    spec = load_spec(CONFIG_DIR / "reports" / "pass_report.yaml")
    kinds = [s.type for s in spec.sections]
    assert "console_log" in kinds
    assert "pass_rollup" in kinds


class TestConsoleLogSection:
    def test_unreachable_store_renders_notice_not_rows(self, monkeypatch):
        monkeypatch.setattr(render_html, "_fetch_json", lambda *a, **k: None)
        html = _render_console_log(
            ConsoleLogSection(type="console_log", id="log"), PERIOD)
        assert "unreachable" in html
        assert "NOT reconstructed" in html
        assert "<tr>" not in html.split("</h2>")[1]

    def test_entries_render_with_position_and_kind(self, monkeypatch):
        doc = {"entries": [
            {"utc_epoch": T0_EPOCH + 10, "position": "eps_tcs",
             "kind": "tc", "text": "TC sent: EPS_POWER_ON (S8.1, seq 3)",
             "ref": "seq:3", "source": "server"},
            {"utc_epoch": T0_EPOCH + 20, "position": "flight_director",
             "kind": "gonogo", "text": "GO/NO-GO result: ALL_GO",
             "ref": "", "source": "server"},
        ], "archive_unavailable": False}
        captured = {}

        def fake(url, timeout_s=3.0):
            captured["url"] = url
            return doc
        monkeypatch.setattr(render_html, "_fetch_json", fake)
        html = _render_console_log(
            ConsoleLogSection(type="console_log", id="log"), PERIOD)
        assert "EPS_POWER_ON" in html
        assert "eps_tcs" in html
        assert "ALL_GO" in html
        # The fetch is period-bounded (sim-epoch since/until from the CUC
        # period), so the store does the filtering.
        assert "since=" in captured["url"]
        assert "until=" in captured["url"]

    def test_kind_filter(self, monkeypatch):
        doc = {"entries": [
            {"utc_epoch": T0_EPOCH, "kind": "tc", "text": "a tc",
             "position": "", "ref": "", "source": "server"},
            {"utc_epoch": T0_EPOCH, "kind": "note", "text": "a note",
             "position": "", "ref": "", "source": "operator"},
        ], "archive_unavailable": False}
        monkeypatch.setattr(render_html, "_fetch_json",
                            lambda *a, **k: doc)
        html = _render_console_log(
            ConsoleLogSection(type="console_log", id="log", kinds=["note"]),
            PERIOD)
        assert "a note" in html
        assert "a tc" not in html


class TestPassRollupSection:
    def test_unreachable_store_renders_notice(self, monkeypatch):
        monkeypatch.setattr(render_html, "_fetch_json", lambda *a, **k: None)
        html = _render_pass_rollups(
            PassRollupSection(type="pass_rollup", id="rf"), PERIOD)
        assert "unreachable" in html

    def test_plan_vs_achieved_line_items(self, monkeypatch):
        doc = {"rollups": [{
            "pass_id": 1, "gs_name": "Iqaluit", "time_base": "sim",
            "aos_pred_ts": T0_EPOCH + 30, "los_ts": T0_EPOCH + 540,
            "duration_s": 510.0, "first_lock_ts": T0_EPOCH + 42,
            "lock_delay_s": 12.0, "locked": True, "mean_eb_n0_db": 11.5,
            "frames_ok": 200, "frames_bad": 4, "fer": 0.0196,
            "data_volume_bytes": 223000, "tier_names": ["LOW"],
        }], "active": None}
        monkeypatch.setattr(render_html, "_fetch_json",
                            lambda *a, **k: doc)
        html = _render_pass_rollups(
            PassRollupSection(type="pass_rollup", id="rf"), PERIOD)
        assert "Iqaluit" in html
        assert "12.0 s" in html      # achieved lock delay vs predicted AOS
        assert "11.5 dB" in html     # mean Eb/N0
        assert "0.0196" in html      # FER

    def test_sim_time_rollups_outside_period_are_filtered(self, monkeypatch):
        doc = {"rollups": [{
            "pass_id": 1, "gs_name": "Troll", "time_base": "sim",
            "aos_pred_ts": T0_EPOCH + 90_000, "los_ts": T0_EPOCH + 90_500,
            "locked": False, "lock_delay_s": None, "mean_eb_n0_db": None,
            "fer": None, "data_volume_bytes": None, "duration_s": 500.0,
            "tier_names": [],
        }], "active": None}
        monkeypatch.setattr(render_html, "_fetch_json",
                            lambda *a, **k: doc)
        html = _render_pass_rollups(
            PassRollupSection(type="pass_rollup", id="rf"), PERIOD)
        assert "No completed passes" in html

    def test_no_lock_pass_shows_no_lock_not_a_number(self, monkeypatch):
        doc = {"rollups": [{
            "pass_id": 1, "gs_name": "Troll", "time_base": "sim",
            "aos_pred_ts": T0_EPOCH + 30, "los_ts": T0_EPOCH + 500,
            "locked": False, "lock_delay_s": None, "mean_eb_n0_db": None,
            "fer": None, "data_volume_bytes": None, "duration_s": 470.0,
            "tier_names": [],
        }], "active": None}
        monkeypatch.setattr(render_html, "_fetch_json",
                            lambda *a, **k: doc)
        html = _render_pass_rollups(
            PassRollupSection(type="pass_rollup", id="rf"), PERIOD)
        assert "no lock" in html
        assert "—" in html  # nulls render as dashes, never invented values


def test_cuc_epoch_conversion_is_consistent():
    """The since/until the section sends must land on the console-log clock."""
    from smo_common.reporting.render_html import _cuc_to_epoch
    e = _cuc_to_epoch(T0_CUC)
    assert e == pytest.approx(T0_EPOCH, abs=1.0)
    dt = cuc_seconds_to_datetime(T0_CUC).replace(tzinfo=timezone.utc)
    assert dt.timestamp() == pytest.approx(e, abs=1.0)
