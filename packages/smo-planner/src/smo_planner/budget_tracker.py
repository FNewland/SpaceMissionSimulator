"""SMO Planner — Power/Data Budget Tracker.

Computes energy balance over a 24-hour plan period, predicts battery
state-of-charge at pass boundaries, and tracks onboard data volume
against storage capacity.

Model parameters (EOSAT-1):
  - Eclipse base drain: 95 W
  - Sunlight base drain: 95 W (loads same, but solar panels charge)
  - Solar array output: ~280 W at beta=0 deg, varies with cos(beta)
  - Battery capacity: 40 Ah at 28 V = 1120 Wh
  - Onboard storage: 1024 MB
  - Downlink data rate: 64 kbps (protocol overhead 20% -> effective 51.2 kbps)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from smo_planner.utils import parse_iso as _parse_iso


# EOSAT-1 power/data constants
ECLIPSE_BASE_POWER_W = 95.0
SUNLIGHT_BASE_POWER_W = 95.0
SOLAR_ARRAY_MAX_W = 280.0
BATTERY_CAPACITY_WH = 1120.0
INITIAL_SOC_PERCENT = 80.0
MIN_SOC_PERCENT = 25.0
ONBOARD_STORAGE_MB = 1024.0
DOWNLINK_RATE_BPS = 64000
PROTOCOL_OVERHEAD = 0.20  # 20% overhead
# Link budget constants for elevation-dependent throughput
TX_EIRP_DBW = 0.0        # 0 dBW (1 W) UHF transmitter
FREQ_GHZ = 0.4015        # UHF downlink frequency (401.5 MHz)
SYSTEM_NOISE_TEMP_K = 290.0  # Ground station system noise temperature
BOLTZMANN_DBW = -228.6    # Boltzmann constant in dBW/K/Hz
REQUIRED_EBNО_DB = 10.0   # Required Eb/N0 for BPSK BER=1e-6
IMPLEMENTATION_LOSS_DB = 2.0  # Modulation/coding implementation loss


class BudgetTracker:
    """Track power and data budgets across a 24-hour mission plan."""

    def __init__(
        self,
        contacts: list[dict] | None = None,
        schedule: list[dict] | None = None,
        ground_track: list[dict] | None = None,
        initial_soc: float = INITIAL_SOC_PERCENT,
        battery_capacity_wh: float = BATTERY_CAPACITY_WH,
        storage_capacity_mb: float = ONBOARD_STORAGE_MB,
    ):
        self._contacts = sorted(contacts or [], key=lambda c: c.get("aos", ""))
        self._schedule = sorted(schedule or [], key=lambda a: a.get("start_time", ""))
        self._ground_track = ground_track or []
        self._initial_soc = initial_soc
        self._battery_wh = battery_capacity_wh
        self._storage_mb = storage_capacity_mb

    def compute_power_budget(self) -> dict:
        """Compute 24-hour power budget with SoC predictions at pass boundaries.

        Returns a dict with:
          - initial_soc: starting battery SoC %
          - pass_predictions: list of {pass_id, gs_name, aos, los, soc_at_aos, soc_at_los}
          - final_soc: predicted SoC at end of 24h period
          - warnings: list of constraint violation messages
          - total_charge_wh: total energy charged
          - total_drain_wh: total energy consumed
        """
        warnings: list[str] = []
        pass_predictions: list[dict] = []

        if not self._contacts:
            return {
                "initial_soc": self._initial_soc,
                "pass_predictions": [],
                "final_soc": self._initial_soc,
                "warnings": ["No contact windows available for budget computation"],
                "total_charge_wh": 0.0,
                "total_drain_wh": 0.0,
            }

        # Build timeline events from contacts
        soc_wh = self._battery_wh * (self._initial_soc / 100.0)
        total_charge = 0.0
        total_drain = 0.0

        # Process inter-pass and pass intervals
        prev_time_str = self._contacts[0].get("aos", "")
        prev_time = _parse_iso(prev_time_str) if prev_time_str else None

        for idx, contact in enumerate(self._contacts):
            aos_str = contact.get("aos", "")
            los_str = contact.get("los", "")
            gs_name = contact.get("gs_name", "unknown")
            pass_duration_s = contact.get("duration_s", 0)

            aos = _parse_iso(aos_str) if aos_str else None
            los = _parse_iso(los_str) if los_str else None

            if aos is None or los is None:
                continue

            # --- Inter-pass interval (before this pass) ---
            if prev_time and aos > prev_time:
                gap_s = (aos - prev_time).total_seconds()
                eclipse_fraction = self._estimate_eclipse_fraction(prev_time, aos)
                drain_wh, charge_wh = self._energy_balance(
                    gap_s, eclipse_fraction, prev_time, aos
                )
                soc_wh = soc_wh + charge_wh - drain_wh
                total_charge += charge_wh
                total_drain += drain_wh

            soc_at_aos = max(0.0, min(100.0, (soc_wh / self._battery_wh) * 100.0))

            # --- Pass interval ---
            if pass_duration_s > 0:
                eclipse_fraction = self._estimate_eclipse_fraction(aos, los)
                pass_drain, pass_charge = self._energy_balance(
                    pass_duration_s, eclipse_fraction, aos, los
                )
                # Add activity consumption during pass
                activity_drain = self._activity_power_during(aos, los)
                pass_drain += activity_drain
                soc_wh = soc_wh + pass_charge - pass_drain
                total_charge += pass_charge
                total_drain += pass_drain

            soc_wh = max(0.0, min(self._battery_wh, soc_wh))
            soc_at_los = max(0.0, min(100.0, (soc_wh / self._battery_wh) * 100.0))

            pass_pred = {
                "pass_id": idx + 1,
                "gs_name": gs_name,
                "aos": aos_str,
                "los": los_str,
                "soc_at_aos": round(soc_at_aos, 1),
                "soc_at_los": round(soc_at_los, 1),
            }
            pass_predictions.append(pass_pred)

            if soc_at_aos < MIN_SOC_PERCENT:
                warnings.append(
                    f"Pass {idx + 1} ({gs_name}): SoC at AOS = {soc_at_aos:.1f}% "
                    f"(below {MIN_SOC_PERCENT}% threshold)"
                )
            if soc_at_los < MIN_SOC_PERCENT:
                warnings.append(
                    f"Pass {idx + 1} ({gs_name}): SoC at LOS = {soc_at_los:.1f}% "
                    f"(below {MIN_SOC_PERCENT}% threshold)"
                )

            prev_time = los

        final_soc = max(0.0, min(100.0, (soc_wh / self._battery_wh) * 100.0))

        # Check if any scheduled activity would cause SoC to drop below threshold
        for activity in self._schedule:
            act_warnings = self._check_activity_power(activity, pass_predictions)
            warnings.extend(act_warnings)

        return {
            "initial_soc": self._initial_soc,
            "pass_predictions": pass_predictions,
            "final_soc": round(final_soc, 1),
            "warnings": warnings,
            "total_charge_wh": round(total_charge, 1),
            "total_drain_wh": round(total_drain, 1),
        }

    def compute_data_budget(self) -> dict:
        """Compute data volume budget over the planning period.

        Returns a dict with:
          - onboard_data_mb: current estimated onboard data volume
          - storage_capacity_mb: total storage capacity
          - utilization_percent: storage utilization
          - pass_downlink: list of per-pass downlink capacity
          - planned_generation_mb: total data from imaging activities
          - planned_downlink_mb: total planned downlink
          - warnings: list of constraint violation messages
        """
        warnings: list[str] = []

        # Calculate data generation from imaging activities
        planned_gen_mb = 0.0
        for activity in self._schedule:
            state = activity.get("state", 0)
            state_name = activity.get("state_name", "")
            if state_name in ("CANCELLED", "FAILED"):
                continue
            planned_gen_mb += activity.get("data_volume_mb", 0)

        # Calculate per-pass downlink capacity
        pass_downlink: list[dict] = []
        total_downlink_mb = 0.0
        effective_rate_bps = DOWNLINK_RATE_BPS * (1.0 - PROTOCOL_OVERHEAD)

        for idx, contact in enumerate(self._contacts):
            duration_s = contact.get("duration_s", 0)
            gs_name = contact.get("gs_name", "unknown")
            max_el_deg = contact.get("max_elevation_deg", 15.0)
            # Elevation-dependent effective throughput:
            # Higher passes have shorter range -> lower FSPL -> higher margin
            # -> can sustain the nominal data rate for longer.
            # Model the effective duty cycle as the fraction of the pass above
            # a usable elevation (~10 deg).  Approximation: for a pass with
            # max elevation E, the fraction of time above 10 deg scales
            # roughly as 1 - (10/E) for E > 10 (triangular pass profile).
            if max_el_deg <= 5.0:
                el_efficiency = 0.3  # marginal pass — barely usable
            elif max_el_deg <= 10.0:
                el_efficiency = 0.5
            elif max_el_deg <= 30.0:
                el_efficiency = 0.6 + 0.3 * ((max_el_deg - 10.0) / 20.0)
            else:
                el_efficiency = 0.9 + 0.1 * min((max_el_deg - 30.0) / 60.0, 1.0)
            # Effective downlink capacity in MB (scaled by elevation efficiency)
            capacity_mb = (effective_rate_bps * duration_s * el_efficiency) / (8.0 * 1024 * 1024)

            # Check if a data_dump activity is scheduled during this pass
            dump_scheduled = self._is_dump_scheduled_during(contact)
            actual_mb = capacity_mb if dump_scheduled else 0.0
            total_downlink_mb += actual_mb

            pass_downlink.append({
                "pass_id": idx + 1,
                "gs_name": gs_name,
                "aos": contact.get("aos", ""),
                "los": contact.get("los", ""),
                "duration_s": duration_s,
                "max_elevation_deg": max_el_deg,
                "elevation_efficiency": round(el_efficiency, 3),
                "capacity_mb": round(capacity_mb, 1),
                "planned_dump_mb": round(actual_mb, 1),
                "dump_scheduled": dump_scheduled,
            })

        # Net onboard data
        onboard_mb = planned_gen_mb - total_downlink_mb
        utilization = (onboard_mb / self._storage_mb) * 100.0 if self._storage_mb > 0 else 0.0

        if onboard_mb > self._storage_mb:
            warnings.append(
                f"Onboard data volume ({onboard_mb:.1f} MB) exceeds storage "
                f"capacity ({self._storage_mb:.0f} MB)"
            )

        if planned_gen_mb > 0 and total_downlink_mb == 0:
            warnings.append(
                "Data generation planned but no downlink passes scheduled"
            )

        return {
            "onboard_data_mb": round(max(0.0, onboard_mb), 1),
            "storage_capacity_mb": self._storage_mb,
            "utilization_percent": round(max(0.0, min(100.0, utilization)), 1),
            "pass_downlink": pass_downlink,
            "planned_generation_mb": round(planned_gen_mb, 1),
            "planned_downlink_mb": round(total_downlink_mb, 1),
            "warnings": warnings,
        }

    def _energy_balance(
        self,
        duration_s: float,
        eclipse_fraction: float,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[float, float]:
        """Compute (drain_wh, charge_wh) over a time interval.

        Args:
            duration_s: interval duration in seconds
            eclipse_fraction: fraction of interval spent in eclipse (0.0-1.0)
            start, end: time bounds (used for activity power lookup)

        Returns:
            (total_drain_wh, total_charge_wh)
        """
        hours = duration_s / 3600.0
        eclipse_hours = hours * eclipse_fraction
        sunlight_hours = hours * (1.0 - eclipse_fraction)

        # Base system drain (always running)
        drain_wh = ECLIPSE_BASE_POWER_W * eclipse_hours + SUNLIGHT_BASE_POWER_W * sunlight_hours

        # Solar charge (only in sunlight) — use actual beta angle from propagator
        midpoint = None
        if start is not None and end is not None:
            midpoint = start + (end - start) / 2
        beta_deg = self._get_beta_angle(at_time=midpoint)
        charge_w = SOLAR_ARRAY_MAX_W * math.cos(math.radians(min(abs(beta_deg), 89.0)))
        charge_wh = charge_w * sunlight_hours

        return drain_wh, charge_wh

    def _activity_power_during(self, start: datetime, end: datetime) -> float:
        """Sum activity power consumption (Wh) for activities overlapping [start, end]."""
        total_wh = 0.0
        for activity in self._schedule:
            state_name = activity.get("state_name", "")
            if state_name in ("CANCELLED", "FAILED"):
                continue
            act_start_str = activity.get("start_time", "")
            act_duration_s = activity.get("duration_s", 0)
            act_power_w = activity.get("power_w", 0)
            if not act_start_str or act_power_w == 0:
                continue
            act_start = _parse_iso(act_start_str)
            if act_start is None:
                continue
            from datetime import timedelta
            act_end = act_start + timedelta(seconds=act_duration_s)

            # Compute overlap
            overlap_start = max(start, act_start)
            overlap_end = min(end, act_end)
            if overlap_start < overlap_end:
                overlap_s = (overlap_end - overlap_start).total_seconds()
                total_wh += act_power_w * (overlap_s / 3600.0)

        return total_wh

    def _estimate_eclipse_fraction(self, start: datetime, end: datetime) -> float:
        """Estimate fraction of time in eclipse between start and end.

        Uses ground track data if available, otherwise uses a default
        ~35% eclipse fraction for a 450km SSO orbit.
        """
        if not self._ground_track:
            return 0.35  # default SSO eclipse fraction

        eclipse_count = 0
        total_count = 0
        start_iso = start.isoformat()
        end_iso = end.isoformat()

        for point in self._ground_track:
            utc = point.get("utc", "")
            if start_iso <= utc <= end_iso:
                total_count += 1
                if point.get("in_eclipse", False):
                    eclipse_count += 1

        if total_count == 0:
            return 0.35
        return eclipse_count / total_count

    def _get_beta_angle(self, at_time: datetime | None = None) -> float:
        """Get solar beta angle from ground track data.

        Searches the ground track for the nearest point to *at_time* and
        returns the propagator-computed beta angle.  Falls back to a
        moderate SSO default (30 deg) only if no ground track is loaded.
        """
        if not self._ground_track:
            return 30.0

        # If no specific time requested, return the median beta from the track
        betas = [
            pt.get("solar_beta_deg", 30.0)
            for pt in self._ground_track
            if "solar_beta_deg" in pt
        ]
        if not betas:
            return 30.0

        if at_time is None:
            # Use the median value across the planning window
            betas_sorted = sorted(betas)
            return betas_sorted[len(betas_sorted) // 2]

        # Find the closest ground-track point to the requested time
        target_iso = at_time.isoformat()
        best_beta = betas[0]
        best_diff = float("inf")
        for pt in self._ground_track:
            utc_str = pt.get("utc", "")
            if not utc_str:
                continue
            pt_time = _parse_iso(utc_str)
            if pt_time is None:
                continue
            diff = abs((pt_time - at_time).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best_beta = pt.get("solar_beta_deg", 30.0)
        return best_beta

    def _check_activity_power(
        self, activity: dict, pass_predictions: list[dict]
    ) -> list[str]:
        """Check if an activity would push SoC below threshold.

        Uses the predicted SoC at the activity's start time (interpolated
        from pass predictions) rather than the initial SoC.
        """
        warnings = []
        power_w = activity.get("power_w", 0)
        duration_s = activity.get("duration_s", 0)
        name = activity.get("name", "unknown")

        if power_w <= 0 or duration_s <= 0:
            return warnings

        energy_wh = power_w * (duration_s / 3600.0)
        soc_impact = (energy_wh / self._battery_wh) * 100.0

        # Find the predicted SoC closest to the activity's start time
        act_start_str = activity.get("start_time", "")
        soc_at_activity = self._initial_soc  # fallback
        if act_start_str and pass_predictions:
            act_start = _parse_iso(act_start_str)
            if act_start is not None:
                # Find the pass boundary closest to (and before) the activity
                for pred in pass_predictions:
                    pred_los = _parse_iso(pred.get("los", ""))
                    if pred_los is not None and pred_los <= act_start:
                        soc_at_activity = pred.get("soc_at_los", self._initial_soc)
                    pred_aos = _parse_iso(pred.get("aos", ""))
                    if pred_aos is not None and pred_aos <= act_start:
                        soc_at_activity = pred.get("soc_at_aos", soc_at_activity)

        if soc_impact > (soc_at_activity - MIN_SOC_PERCENT):
            warnings.append(
                f"Activity '{name}' consumes {energy_wh:.1f} Wh "
                f"({soc_impact:.1f}% SoC) — predicted SoC at execution "
                f"is {soc_at_activity:.1f}%, may breach {MIN_SOC_PERCENT}% floor"
            )

        return warnings

    def _is_dump_scheduled_during(self, contact: dict) -> bool:
        """Check if a data_dump activity is scheduled during the given pass."""
        aos_str = contact.get("aos", "")
        los_str = contact.get("los", "")
        if not aos_str or not los_str:
            return False

        aos = _parse_iso(aos_str)
        los = _parse_iso(los_str)
        if aos is None or los is None:
            return False

        for activity in self._schedule:
            state_name = activity.get("state_name", "")
            if state_name in ("CANCELLED", "FAILED"):
                continue
            name = activity.get("name", "")
            if "dump" not in name.lower() and "downlink" not in name.lower():
                continue
            act_start_str = activity.get("start_time", "")
            if not act_start_str:
                continue
            act_start = _parse_iso(act_start_str)
            if act_start is None:
                continue
            from datetime import timedelta
            act_end = act_start + timedelta(seconds=activity.get("duration_s", 0))
            # Activity overlaps with pass if it starts before LOS and ends after AOS
            if act_start < los and act_end > aos:
                return True
        return False



# _parse_iso imported from smo_planner.utils
