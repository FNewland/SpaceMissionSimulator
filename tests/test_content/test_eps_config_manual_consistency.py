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

    def test_wing_assembly_mass_is_stated(self, manual):
        """R1 asked for the array to be sized against real hardware. A mass
        the reader can sanity-check against the area is part of that."""
        assert "Wing assembly mass" in manual

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


class TestManualWattagesMatchConfig:
    """2026-08b: the WATTAGE column was untested, and it drifted — badly.

    Before this batch the manual's §3.1 table said 45 W imaging payload, 6 W
    standby, 12/20 W transmitter and 15 W cooler, while eps.yaml billed 5.0,
    6.0, 5.0/10.0 and 10.0. Areas and battery capacity were tested and stayed
    honest; wattages were not tested and did not. Franz's ruling R1 landed
    squarely on those fictional numbers ("a CubeSat would generally not have a
    20 W transmitter"), so the drift was not cosmetic — it drove a decision.

    Every row of the §3.1 breakdown is now pinned to the key eps.yaml bills.
    """

    #: manual row label -> (eps.yaml key path, index of the number in the cell)
    WATT_ROWS = {
        "OBC (primary)":      (("obc_power_w",), 0),
        "TTC receiver (RX)":  (("transponder_rx_power_w",), 0),
        "Payload (imaging)":  (("payload_power_w",), 0),
        "Payload (standby)":  (("payload_standby_power_w",), 0),
        "FPA cooler":         (("fpa_cooler_power_w",), 0),
    }

    @staticmethod
    def _first_watts(cell: str) -> float:
        m = re.search(r"([\d.]+)\s*W", cell)
        assert m, f"no wattage in manual cell {cell!r}"
        return float(m.group(1))

    @pytest.mark.parametrize("row", sorted(WATT_ROWS))
    def test_row_matches_eps_yaml(self, eps_yaml, manual, row):
        keys, _ = self.WATT_ROWS[row]
        value = eps_yaml
        for k in keys:
            value = value[k]
        doc = self._first_watts(_manual_value(manual, row))
        assert doc == pytest.approx(float(value), rel=0.02), (
            f"manual '{row}' says {doc} W, eps.yaml says {value} W")

    def test_transmitter_row_carries_both_tiers(self, eps_yaml, manual):
        """The TX row is two numbers (LOW / HIGH tier), and BOTH must match.
        This is the row R1 was about."""
        cell = _manual_value(manual, "TTC transmitter+PA")
        nums = [float(x) for x in re.findall(r"([\d.]+)\s*W", cell)]
        assert eps_yaml["transponder_power_w"] in nums, (
            f"LOW-tier TX {eps_yaml['transponder_power_w']} W absent from "
            f"manual row: {cell!r}")
        assert eps_yaml["transponder_hi_power_w"] in nums, (
            f"HIGH-tier TX {eps_yaml['transponder_hi_power_w']} W absent from "
            f"manual row: {cell!r}")

    def test_no_20_w_transmitter_anywhere(self, eps_yaml):
        """R1, stated as a test: a 6U CubeSat does not fly a 20 W transmitter.
        The Endurosat S-band transceiver — a real flown part — draws 11 W DC
        at its maximum 33 dBm / 2 W RF setting."""
        assert eps_yaml["transponder_hi_power_w"] <= 15.0
        assert eps_yaml["transponder_power_w"] <= 15.0

    def test_standby_is_below_imaging(self, eps_yaml):
        """The physically impossible pair: standby drawing MORE than imaging.
        eps.yaml had payload_standby_power_w 6.0 > payload_power_w 5.0."""
        assert eps_yaml["payload_standby_power_w"] < eps_yaml["payload_power_w"], (
            "payload standby draws more than imaging — the imager cannot cost "
            "less to run than to idle")

    def test_fpa_cooler_agrees_with_payload_yaml(self, eps_yaml):
        """eps.yaml had drifted to 10 W while payload.yaml, tcs.yaml, the
        textbook and the commissioning procedure all said 15 W, so the EPS
        under-billed the cooler by 5 W whenever it ran."""
        payload = yaml.safe_load(
            (CONFIG_DIR / "subsystems" / "payload.yaml").read_text())
        assert eps_yaml["fpa_cooler_power_w"] == pytest.approx(
            payload["fpa_cooler_power_w"])

    def test_peak_generation_is_a_cubesat_number(self, eps_yaml):
        """R1: "would struggle to generate 114 W peak". Bound the *design*
        rather than the model output, so the check is cheap and unambiguous:
        wings at normal incidence with the BOL loss stack must not exceed the
        MMA eHaWK 6U class rating with any headroom to spare."""
        sp = eps_yaml["solar_panels"]
        al = eps_yaml["array_losses"]
        temp_f = 1.0 + al["temp_coeff_per_c"] * (
            al["array_temp_sunlit_c"] - al["temp_ref_c"])
        k_bol = (1361.0 * eps_yaml["cell_efficiency"]
                 * al["inherent_degradation"] * temp_f
                 * al["harness_efficiency"] * 0.97)
        wing_w = (sp["wing_py_area_m2"] + sp["wing_my_area_m2"]) * k_bol
        assert wing_w < 80.0, (
            f"deployable wings rated {wing_w:.0f} W BOL — above the MMA eHaWK "
            f"6U class (72 W). Check docs/plan_2026-08b/POWER_SIZING.md before "
            f"growing the array again.")
        body = sum(sp[k] for k in ("px_area_m2", "mx_area_m2", "py_area_m2",
                                   "my_area_m2", "pz_area_m2", "mz_area_m2"))
        assert body + sp["wing_py_area_m2"] + sp["wing_my_area_m2"] < 0.60, (
            "deployed cell area is not buildable on a 6U — the 2026-07 "
            "session's 0.90 m2 mistake")


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
