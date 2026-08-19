"""Derived-parameter block v2 (POSITION_NEEDS G1 + G3).

The honesty contract is the load-bearing behaviour under test: every
derived value carries an explicit valid/reason pair, and NO entry is ever
a fabricated number — an empty archive, missing telemetry, or a trend
away from the threshold all yield ``valid: false`` with ``value: null``.
"""
from pathlib import Path

import pytest

from smo_mcs.derived_params import (
    DERIVED_V2_IDS,
    DerivedParamEngine,
    MOMENTUM_THRESHOLD_PCT,
)
from smo_mcs.server import MCSServer
from smo_mcs.tm_archive import TMArchive
from smo_common.config.loader import load_limits

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "eosat1"

ALL_NAMES = list(DERIVED_V2_IDS)


@pytest.fixture
def archive(tmp_path):
    a = TMArchive(tmp_path / "test_archive.db")
    a.open()
    yield a
    a.close()


@pytest.fixture
def engine(archive):
    limits = load_limits(CONFIG_DIR)
    now = {"t": 10_000.0}
    eng = DerivedParamEngine(archive, limits, None, lambda: now["t"])
    eng.set_catbed_threshold(180.0)
    eng._now = now  # test hook to move sim time
    return eng


def _feed(archive, name, points):
    """Write (t, value) history rows."""
    for t, v in points:
        archive.store_parameters({name: v}, utc_epoch=t)


class TestHonestInvalid:
    """No archive history ⇒ invalid state, never a number."""

    def test_all_entries_invalid_with_empty_archive_and_no_tm(self, engine):
        out = engine.compute({})
        assert set(out) == set(ALL_NAMES)
        for name, ent in out.items():
            assert ent["valid"] is False, name
            assert ent["value"] is None, name
            assert ent["reason"], f"{name} must say WHY it is invalid"
            assert ent["source"] == "ground"

    def test_soc_present_but_no_history_is_invalid(self, engine):
        out = engine.compute({"eps": {"bat_soc": 70.0}, "in_eclipse": True,
                              "time_to_eclipse_exit_s": 900.0})
        ent = out["eps.soc_at_eclipse_exit_pred"]
        assert ent["valid"] is False
        assert ent["value"] is None
        assert "insufficient history" in ent["reason"]

    def test_ids_are_the_documented_0x08xx_block(self, engine):
        out = engine.compute({})
        for name, ent in out.items():
            assert ent["id"] == f"0x{DERIVED_V2_IDS[name]:04X}"
        # Allocation is stable and collision-free above the contact trio.
        ids = sorted(DERIVED_V2_IDS.values())
        assert ids[0] >= 0x0803
        assert len(ids) == len(set(ids))

    def test_declared_in_derived_yaml_not_parameters_yaml(self):
        import yaml
        derived = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "derived.yaml").read_text())
        declared = {p["name"]: int(p["id"]) if isinstance(p["id"], int)
                    else int(str(p["id"]), 0)
                    for p in derived["derived_parameters"]}
        for name, pid in DERIVED_V2_IDS.items():
            assert declared.get(name) == pid, (
                f"{name} (0x{pid:04X}) must be documented in "
                f"telemetry/derived.yaml alongside the contact trio")
        params = yaml.safe_load(
            (CONFIG_DIR / "telemetry" / "parameters.yaml").read_text())
        sc_names = {p.get("name") for p in params.get("parameters", [])}
        for name in DERIVED_V2_IDS:
            assert name not in sc_names, (
                f"{name} is GROUND-derived and must not appear in "
                "parameters.yaml as spacecraft TM")


class TestProjections:
    def test_time_to_soc_yellow_from_falling_slope(self, engine, archive):
        # 1 %-per-100 s fall from 70 %: yellow_low for 0x0101 is 25.0.
        _feed(archive, "eps.bat_soc",
              [(9_000 + i * 100, 80.0 - i) for i in range(11)])  # ends 70@10k
        out = engine.compute({"eps": {"bat_soc": 70.0}})
        ent = out["eps.time_to_soc_yellow"]
        assert ent["valid"] is True
        # (70 - 25) / (0.01 %/s) = 4500 s
        assert ent["value"] == pytest.approx(4500.0, rel=0.05)

    def test_time_to_soc_yellow_invalid_when_charging(self, engine, archive):
        _feed(archive, "eps.bat_soc",
              [(9_000 + i * 100, 60.0 + i) for i in range(11)])
        out = engine.compute({"eps": {"bat_soc": 70.0}})
        ent = out["eps.time_to_soc_yellow"]
        assert ent["valid"] is False
        assert "not trending" in ent["reason"]

    def test_soc_at_eclipse_exit_projects_slope(self, engine, archive):
        _feed(archive, "eps.bat_soc",
              [(9_000 + i * 100, 80.0 - i) for i in range(11)])
        out = engine.compute({"eps": {"bat_soc": 70.0}, "in_eclipse": True,
                              "time_to_eclipse_exit_s": 1000.0})
        ent = out["eps.soc_at_eclipse_exit_pred"]
        assert ent["valid"] is True
        # 70 % - 0.01 %/s * 1000 s = 60 %
        assert ent["value"] == pytest.approx(60.0, abs=1.0)

    def test_soc_at_eclipse_exit_invalid_outside_eclipse(self, engine,
                                                         archive):
        _feed(archive, "eps.bat_soc",
              [(9_000 + i * 100, 80.0 - i) for i in range(11)])
        out = engine.compute({"eps": {"bat_soc": 70.0}, "in_eclipse": False})
        assert out["eps.soc_at_eclipse_exit_pred"]["valid"] is False

    def test_momentum_eta_to_70pct(self, engine, archive):
        _feed(archive, "aocs.momentum_saturation_pct",
              [(9_000 + i * 100, 40.0 + i) for i in range(11)])  # 50@10k
        out = engine.compute(
            {"aocs": {"momentum_saturation_pct": 50.0}})
        ent = out["aocs.time_to_mom_70pct"]
        assert ent["valid"] is True
        # (70 - 50) / 0.01 = 2000 s
        assert ent["value"] == pytest.approx(
            (MOMENTUM_THRESHOLD_PCT - 50.0) / 0.01, rel=0.05)

    def test_buffer_fill_worst_case(self, engine, archive):
        _feed(archive, "obdh.hktm_buf_fill",
              [(9_000 + i * 100, 40.0 + i) for i in range(11)])  # -> 100 in 5000s
        _feed(archive, "obdh.event_buf_fill",
              [(9_000 + i * 100, 80.0 + i) for i in range(11)])  # -> 100 in 1000s
        out = engine.compute({"obdh": {"hktm_buf_fill": 50.0,
                                       "event_buf_fill": 90.0}})
        ent = out["obdh.time_to_buf_full"]
        assert ent["valid"] is True
        assert ent["value"] == pytest.approx(1000.0, rel=0.05)

    def test_catbed_eta(self, engine, archive):
        _feed(archive, "prop.catbed_temp",
              [(9_000 + i * 100, 20.0 + i * 10) for i in range(11)])  # 120@10k
        out = engine.compute({"prop": {"catbed_temp": 120.0}})
        ent = out["prop.catbed_ready_eta"]
        assert ent["valid"] is True
        # (180-120) / 0.1 C/s = 600 s
        assert ent["value"] == pytest.approx(600.0, rel=0.05)

    def test_catbed_already_ready_is_zero(self, engine, archive):
        _feed(archive, "prop.catbed_temp",
              [(9_000 + i * 100, 150.0 + i * 10) for i in range(11)])
        out = engine.compute({"prop": {"catbed_temp": 250.0}})
        assert out["prop.catbed_ready_eta"]["value"] == 0.0
        assert out["prop.catbed_ready_eta"]["valid"] is True

    def test_time_offset_ms_from_correlation(self, engine):
        out = engine.compute({"time_correlation": {"offset_s": -1.25}})
        ent = out["obdh.time_offset_ms"]
        assert ent["valid"] is True
        assert ent["value"] == pytest.approx(-1250.0)


class TestPassCapacityAndFit:
    def _engine_with_orbit(self, archive):
        class FakeProp:
            def contact_windows(self, duration_s=7200.0, step_s=30.0):
                from datetime import datetime, timezone, timedelta
                now = datetime.fromtimestamp(10_000.0, tz=timezone.utc)
                return [{"aos": (now + timedelta(seconds=300)).isoformat(),
                         "los": (now + timedelta(seconds=900)).isoformat(),
                         "gs_name": "Iqaluit", "max_elevation_deg": 45.0,
                         "duration_s": 600.0}]
        limits = load_limits(CONFIG_DIR)
        return DerivedParamEngine(archive, limits, FakeProp(),
                                  lambda: 10_000.0)

    def test_capacity_next_pass(self, archive):
        eng = self._engine_with_orbit(archive)
        state = {"ttc": {"tm_tier": 1},
                 "tm_rate_ladder": [{"bps": 1000}, {"bps": 16000}],
                 "in_contact": False}
        ent = eng.compute(state)["ttc.pass_data_capacity_bytes"]
        assert ent["valid"] is True
        # 16 kbps x 600 s / 8 = 1.2 MB
        assert ent["value"] == pytest.approx(1_200_000.0, rel=0.01)

    def test_fit_ratio_uses_backlog(self, archive):
        eng = self._engine_with_orbit(archive)
        state = {"ttc": {"tm_tier": 1},
                 "tm_rate_ladder": [{"bps": 1000}, {"bps": 16000}],
                 "in_contact": False,
                 "payload": {"mem_used_mb": 2.4}}  # 2.4 MB vs 1.2 MB
        ent = eng.compute(state)["ttc.pass_fit_ratio"]
        assert ent["valid"] is True
        assert ent["value"] == pytest.approx(2.0, rel=0.01)

    def test_fit_ratio_invalid_without_backlog_telemetry(self, archive):
        eng = self._engine_with_orbit(archive)
        state = {"ttc": {"tm_tier": 1},
                 "tm_rate_ladder": [{"bps": 1000}, {"bps": 16000}],
                 "in_contact": False}
        ent = eng.compute(state)["ttc.pass_fit_ratio"]
        assert ent["valid"] is False
        assert "backlog" in ent["reason"]

    def test_capacity_invalid_without_tier(self, archive):
        eng = self._engine_with_orbit(archive)
        ent = eng.compute({})["ttc.pass_data_capacity_bytes"]
        assert ent["valid"] is False


class TestStatePayloadIntegration:
    """The modern UI polls /api/state — the block must be in the payload."""

    def test_state_poll_publishes_derived_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SMO_TM_ARCHIVE", str(tmp_path / "a.db"))
        server = MCSServer(config_dir=str(CONFIG_DIR), http_port=0)
        state = {"eps": {"bat_soc": 70.0}}
        derived = server._derived_engine.compute(state)
        assert set(derived) == set(ALL_NAMES)
        # Snapshot archiving stores only VALID values (no fabricated history).
        data = {"derived": derived}
        server._archive_state_snapshot(data)
        for name in ALL_NAMES:
            rows = server._archive.query_parameters(name)
            assert rows == [], f"invalid {name} must not be archived"
