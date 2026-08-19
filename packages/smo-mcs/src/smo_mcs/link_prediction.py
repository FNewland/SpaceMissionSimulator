"""Predicted link-budget chain for the TT&C overlay (POSITION_NEEDS G5).

Ports the range → FSPL → expected-Eb/N0 arithmetic of
``tools/link_budget_ladder.py`` / ``TTCBasicModel.link_margin_for`` into the
MCS so ``GET /api/displays/link-prediction`` can serve the predicted curves
the TT&C console overlays under the measured trace (REQ-TTC-MCS-010/011).

The prediction uses GROUND knowledge only — the MCS's own orbit propagator
for geometry and the RF constants in configuration (``subsystems/ttc.yaml``
for the spacecraft chain, ``planning/ground_stations.yaml`` for per-station
G/T) — so it works identically in real mode. The formulas are kept
numerically identical to the simulator's deterministic budget
(``smo_simulator.models.ttc_basic.TTCBasicModel.link_margin_for``); the MCS
deliberately does NOT import the simulator package (ground tools are
spacecraft-agnostic and the mcs package does not depend on smo-simulator).

    FSPL   = 20log10(d_m) + 20log10(f_Hz) - 147.55
    C/N0   = EIRP + G_sc - FSPL - losses(el) + (G/T - derate(el)) + 228.6
    Eb/N0  = C/N0 - 10log10(R_b)
    margin = Eb/N0 - (required + mod_penalty - coding_gain)
"""
from __future__ import annotations

import logging
import math
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

#: Eb/N0 penalty per modulation, mirror of TTCBasicModel.MODULATION_PARAMS.
MODULATION_EBN0_PENALTY = {"BPSK": 0.0, "QPSK": 0.0, "8PSK": 3.6,
                           "OQPSK": 0.0}


class LinkPredictor:
    """Deterministic link prediction from config + the orbit propagator."""

    def __init__(self, config_dir: str | Path, orbit_prop):
        self._orbit_prop = orbit_prop
        self._ok = False
        self._why = ""
        cfg_dir = Path(config_dir)
        try:
            ttc = yaml.safe_load(
                (cfg_dir / "subsystems" / "ttc.yaml").read_text()) or {}
        except Exception as e:  # noqa: BLE001
            ttc = {}
            self._why = f"subsystems/ttc.yaml unreadable: {e}"
        self._eirp_dbw = float(ttc.get("eirp_dbw", 10.0))
        self._sc_gain_dbi = float(ttc.get("sc_gain_dbi", 3.0))
        self._dl_freq_hz = float(ttc.get("dl_freq_mhz", 2200.0)) * 1e6
        self._atm_loss = float(ttc.get("atmospheric_loss_db", 0.5))
        self._pol_loss = float(ttc.get("polarization_loss_db", 0.3))
        self._impl_loss = float(ttc.get("implementation_loss_db", 2.0))
        self._required_eb_n0 = float(ttc.get("required_eb_n0_db", 9.6))
        self._gt_derate_10 = float(ttc.get("gt_derate_below_10deg_db", 1.5))
        self._gt_derate_30 = float(ttc.get("gt_derate_below_30deg_db", 0.5))
        self._default_gt = float(ttc.get("gs_g_t_db", 20.0))
        self._tiers = []
        for idx, t in enumerate(ttc.get("tm_rate_tiers") or []):
            self._tiers.append({
                "index": idx,
                "name": str(t.get("name", f"TIER{idx}")),
                "bps": int(t.get("bps", 0)),
                "modulation": str(t.get("modulation", "BPSK")).upper(),
                "coding": str(t.get("coding", "RS")).upper(),
                "coding_gain_db": float(t.get("coding_gain_db", 3.0)),
            })
        # Per-station G/T from planning/ground_stations.yaml.
        self._station_gt: dict[str, float] = {}
        try:
            gs = yaml.safe_load(
                (cfg_dir / "planning" / "ground_stations.yaml").read_text()) or {}
            for s in gs.get("ground_stations") or gs.get("stations") or []:
                self._station_gt[str(s.get("name", ""))] = float(
                    s.get("g_t_db", self._default_gt))
        except Exception as e:  # noqa: BLE001
            logger.debug("ground_stations.yaml unreadable: %s", e)
        if self._tiers and not self._why:
            self._ok = True
        elif not self._tiers and not self._why:
            self._why = "subsystems/ttc.yaml has no tm_rate_tiers"

    # ── the budget chain ─────────────────────────────────────────────

    def _atm_loss_at(self, el_deg: float) -> float:
        el = max(float(el_deg), 1.0)
        if el >= 30.0:
            return self._atm_loss
        return self._atm_loss * math.sin(math.radians(30.0)) / \
            math.sin(math.radians(el))

    def _gt_derate(self, el_deg: float) -> float:
        el = float(el_deg)
        if el < 10.0:
            return self._gt_derate_10
        if el < 30.0:
            return self._gt_derate_30
        return 0.0

    def fspl_db(self, range_km: float) -> float:
        return (20 * math.log10(max(range_km, 1e-3) * 1000.0)
                + 20 * math.log10(self._dl_freq_hz) - 147.55)

    def point(self, range_km: float, el_deg: float,
              gs_gt_db: Optional[float] = None) -> dict:
        """FSPL / C/N0 / per-tier Eb/N0 + margin at one geometry."""
        gt = self._default_gt if gs_gt_db is None else float(gs_gt_db)
        fspl = self.fspl_db(range_km)
        losses = (self._atm_loss_at(el_deg) + self._pol_loss
                  + self._impl_loss)
        gt_eff = gt - self._gt_derate(el_deg)
        c_n0 = (self._eirp_dbw + self._sc_gain_dbi - fspl - losses
                + gt_eff + 228.6)
        tiers = {}
        for t in self._tiers:
            eb = c_n0 - 10 * math.log10(max(float(t["bps"]), 1.0))
            required = (self._required_eb_n0
                        + MODULATION_EBN0_PENALTY.get(t["modulation"], 0.0)
                        - t["coding_gain_db"])
            tiers[t["name"]] = {
                "eb_n0_db": round(eb, 2),
                "required_eb_n0_db": round(required, 2),
                "margin_db": round(eb - required, 2),
            }
        return {"range_km": round(float(range_km), 1),
                "elevation_deg": round(float(el_deg), 2),
                "fspl_db": round(fspl, 2),
                "c_n0_dbhz": round(c_n0, 2),
                "tiers": tiers}

    # ── the endpoint payload ─────────────────────────────────────────

    def predict(self, sim_now, step_s: float = 15.0) -> dict:
        """Time series over the current-or-next pass, plus 'now'.

        ``sim_now`` is the current SIM datetime; the caller must already
        have reset the propagator to it (the state poll does every second).
        """
        if not self._ok:
            return {"available": False, "reason": self._why, "tiers": []}
        if self._orbit_prop is None:
            return {"available": False,
                    "reason": "orbit propagator unavailable (no TLE)",
                    "tiers": self._tiers}
        try:
            self._orbit_prop.reset(sim_now)
            windows = self._orbit_prop.contact_windows(duration_s=7200.0,
                                                       step_s=30.0)
        except Exception as e:  # noqa: BLE001
            return {"available": False,
                    "reason": f"orbit propagation failed: {e}",
                    "tiers": self._tiers}
        if not windows:
            return {"available": False,
                    "reason": "no pass in the next 2 h",
                    "tiers": self._tiers}

        from datetime import datetime, timezone

        def _parse(v):
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        win = windows[0]
        gs_name = str(win.get("gs_name", ""))
        aos = _parse(win["aos"])
        los = _parse(win["los"])
        if sim_now.tzinfo is None:
            sim_now = sim_now.replace(tzinfo=aos.tzinfo)
        in_current = aos <= sim_now
        gt = self._station_gt.get(gs_name, self._default_gt)

        series_t: list[float] = []
        series_range: list[list[float]] = []
        series_fspl: list[list[float]] = []
        series_el: list[list[float]] = []
        series_ebn0: dict[str, list[list[float]]] = {
            t["name"]: [] for t in self._tiers}
        t = aos
        while t <= los:
            geo = self._orbit_prop.station_geometry_at(t, gs_name)
            if geo is not None:
                ts = t.timestamp()
                p = self.point(geo["range_km"], geo["elevation_deg"], gt)
                series_t.append(ts)
                series_range.append([ts, p["range_km"]])
                series_fspl.append([ts, p["fspl_db"]])
                series_el.append([ts, p["elevation_deg"]])
                for tname, tv in p["tiers"].items():
                    series_ebn0[tname].append([ts, tv["eb_n0_db"]])
            t += timedelta(seconds=step_s)

        now_point = None
        geo_now = self._orbit_prop.station_geometry_at(sim_now, gs_name)
        if geo_now is not None and geo_now["elevation_deg"] > 0.0:
            now_point = {"t": sim_now.timestamp(),
                         **self.point(geo_now["range_km"],
                                      geo_now["elevation_deg"], gt)}

        return {
            "available": True,
            "source": ("ground prediction: orbit propagator + "
                       "subsystems/ttc.yaml + planning/ground_stations.yaml"),
            "pass": {
                "gs_name": gs_name,
                "gs_g_t_db": gt,
                "aos": aos.isoformat(),
                "los": los.isoformat(),
                "max_elevation_deg": win.get("max_elevation_deg"),
                "which": "current" if in_current else "next",
            },
            "tiers": [
                {**t, "required_eb_n0_db": round(
                    self._required_eb_n0
                    + MODULATION_EBN0_PENALTY.get(t["modulation"], 0.0)
                    - t["coding_gain_db"], 2)}
                for t in self._tiers
            ],
            "series": {
                "range_km": series_range,
                "fspl_db": series_fspl,
                "elevation_deg": series_el,
                "eb_n0_db": series_ebn0,
            },
            "now": now_point,
        }
