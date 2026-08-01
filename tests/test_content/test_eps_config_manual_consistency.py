"""Phase B (brief 01, test 7 + WP-13): config↔manual consistency and
dead-config lint for the EPS hardware sheet.

* The tabulated wing area, deployed area and battery capacity in
  configs/eosat1/manual/01_eps.md must EQUAL the eps.yaml values (within
  5 %) — finding 23 was a manual still quoting the pre-rebalance numbers.
* eps.yaml must not contain the dead keys the previous model silently
  ignored (`arrays:`, `platform_idle_power_w`), and every top-level key it
  does contain must be one the model actually consumes.
"""
import re
from pathlib import Path

import pytest
import yaml

CONFIG_DIR = Path(__file__).parent.parent.parent / "configs" / "eosat1"


@pytest.fixture(scope="module")
def eps_yaml():
    return yaml.safe_load((CONFIG_DIR / "subsystems" / "eps.yaml").read_text())


@pytest.fixture(scope="module")
def manual():
    return (CONFIG_DIR / "manual" / "01_eps.md").read_text()


def _manual_value(manual: str, row_label: str) -> str:
    m = re.search(rf"^\|\s*{re.escape(row_label)}\s*\|\s*([^|]+)\|",
                  manual, re.MULTILINE)
    assert m, f"manual row '{row_label}' not found"
    return m.group(1).strip()


class TestManualMatchesConfig:
    def test_wing_area(self, eps_yaml, manual):
        cfg_wing = eps_yaml["solar_panels"]["wing_py_area_m2"]
        cell = _manual_value(manual, "Wing area")
        num = float(re.search(r"([\d.]+)\s*m2", cell).group(1))
        assert num == pytest.approx(cfg_wing, rel=0.05), (
            f"manual wing area {num} m2 vs eps.yaml {cfg_wing} m2")

    def test_body_and_deployed_area(self, eps_yaml, manual):
        sp = eps_yaml["solar_panels"]
        body = sum(sp[k] for k in ("px_area_m2", "mx_area_m2", "py_area_m2",
                                   "my_area_m2", "pz_area_m2", "mz_area_m2"))
        deployed = body + sp["wing_py_area_m2"] + sp["wing_my_area_m2"]
        body_doc = float(re.search(
            r"([\d.]+)\s*m2", _manual_value(manual, "Body-mounted area")).group(1))
        depl_doc = float(re.search(
            r"([\d.]+)\s*m2", _manual_value(manual, "Deployed total area")).group(1))
        assert body_doc == pytest.approx(body, rel=0.05)
        assert depl_doc == pytest.approx(deployed, rel=0.05)

    def test_battery_capacity(self, eps_yaml, manual):
        cap = eps_yaml["battery"]["capacity_wh"]
        cell = _manual_value(manual, "Capacity")
        num = float(re.search(r"([\d.]+)\s*Wh", cell).group(1))
        assert num == pytest.approx(cap, rel=0.05), (
            f"manual battery {cell} vs eps.yaml {cap} Wh")

    def test_heater_setpoints_match_tcs_yaml(self, manual):
        """The manual's battery-heater thermostat (ON +5 / OFF +10, 8 W) is
        what tcs.yaml now configures — brief 05 WP-3 (a)."""
        tcs = yaml.safe_load((CONFIG_DIR / "subsystems" / "tcs.yaml").read_text())
        bat = next(h for h in tcs["heaters"] if h["name"] == "battery")
        assert bat["power_w"] == 8.0
        assert bat["on_temp_c"] == 5.0
        assert bat["off_temp_c"] == 10.0
        row = _manual_value(manual, "Battery heater")
        assert "8 W" in row


class TestDeadConfigGone:
    #: Every top-level key in eps.yaml and the model attribute / mechanism
    #: that consumes it — the WP-13 config-lint allowlist.
    CONSUMED_KEYS = {
        "model", "technology",  # loader/model registry
        "battery", "cell_efficiency", "array_losses",
        "obc_power_w", "solar_panels",
        "payload_power_w", "payload_standby_power_w", "fpa_cooler_power_w",
        "transponder_power_w", "transponder_hi_power_w",
        "transponder_rx_power_w",
        "power_lines", "param_ids",
    }

    def test_dead_keys_removed(self, eps_yaml):
        assert "arrays" not in eps_yaml, (
            "legacy `arrays:` block is back — it was dead config that fed a "
            "permanently-false array-degraded alarm (brief 01 F11/WP-13)")
        assert "platform_idle_power_w" not in eps_yaml, (
            "`platform_idle_power_w` is dead config (brief 01 F14/WP-13)")

    def test_every_key_is_consumed(self, eps_yaml):
        unknown = set(eps_yaml.keys()) - self.CONSUMED_KEYS
        assert not unknown, (
            f"eps.yaml keys not consumed by any model: {sorted(unknown)} — "
            "either wire them or delete them (WP-13)")

    def test_dead_code_removed_from_model(self):
        import inspect
        from smo_simulator.models import eps_basic
        src = inspect.getsource(eps_basic)
        assert "POWER_LINE_NOMINAL_W" not in src
        assert "_platform_idle_w" not in src
        assert "self._panel_area " not in src and \
            "self._panel_area=" not in src.replace(" ", "")
