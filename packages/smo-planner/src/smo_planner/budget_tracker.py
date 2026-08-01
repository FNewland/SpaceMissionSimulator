"""SMO Planner — Power/Data Budget Tracker.

Computes energy balance over a 24-hour plan period, predicts battery
state-of-charge at pass boundaries, and integrates onboard data volume against
storage capacity.

DATA BUDGET (defect #60)
------------------------
The data model is driven by the MISSION CONFIG, not by module constants:

  * onboard storage capacity  <- ``subsystems/payload.yaml total_storage_mb``
  * image size per capture    <- ``subsystems/payload.yaml image_size_mb``
  * downlink rates            <- ``subsystems/ttc.yaml tm_rate_tiers``
  * protocol overhead / HK background / per-station rates
                              <- ``planning/planner.yaml budget:`` + ``stations:``

Use :func:`budget_params_from_config` (or ``BudgetTracker.from_config``) to
resolve them; the module-level constants below are only the fallbacks used when
no config directory is available (unit tests, standalone use).

``compute_data_budget`` integrates the onboard store chronologically —
generation spread over each activity's duration, downlink only while a dump
overlaps a real contact window — so it reports *when* the store overflows and
whether the plan closes, instead of netting totals and hiding the ordering.

THE RATE LADDER (defect #86)
----------------------------
There is no longer ONE downlink rate. ``subsystems/ttc.yaml tm_rate_tiers``
ships a three-rung ladder and the planner has to respect it, because the two
rungs it plans against differ by a factor of eight:

  * **routine rate** — the ``default_tier`` (LOW, 16 000 bps). Real-time
    housekeeping, event reports, S6 memory dumps: everything that is not a
    bulk playback. Ungated: no antenna deployment, no mode, no elevation.
  * **dump rate** — the top rung (HIGH, 128 000 bps). Reached ONLY by a
    ``data_dump`` activity, and only when the pass clears the tier's
    ``min_elevation_deg`` gate (10 deg), because HIGH additionally requires a
    deployed antenna and DOWNLINK mode which the dump activity commands.

A contact window therefore does not have "a capacity": it has a capacity that
depends on how much of the window sits above the dump-elevation gate. That
fraction is derived from spherical geometry (:func:`fraction_above_elevation`),
not guessed — a 30 deg pass spends 76 % of its window above 10 deg, a 12 deg
pass only 45 %, and a 7 deg pass none at all and can never dump at HIGH.

The budget reports the two consequences a mission planner has to live with:

  * ``hk_contact_fraction_percent`` — the continuous housekeeping stream
    (derived from ``telemetry/hk_structures.yaml``, ~1557 bps of channel after
    RS and TM framing) eats ~36 % of the day's whole contact time at LOW and
    ~4.5 % at HIGH. Housekeeping alone is a third of the low-rate network.
  * ``dump_contact_fraction_percent`` — the share of the day's contact time
    the plan has committed to bulk dumps. A pass spent dumping is a pass not
    spent on anything else, so this number is the real cost of imagery.

Model parameters (EOSAT-1):
  - Eclipse base drain: 32 W (OBC 15 W + RX 5 W + wheels 12 W)
  - Sunlight base drain: 32 W (loads same, but solar panels charge)
  - Solar array output: ~64 W peak, varies with cos(beta)
  - Battery capacity: 120 Wh (eps.yaml battery.capacity_wh)
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from smo_planner.utils import parse_iso as _parse_iso

logger = logging.getLogger(__name__)


# EOSAT-1 power/data constants (OBC 15W + RX 5W + wheels 12W = 32W base)
ECLIPSE_BASE_POWER_W = 32.0
SUNLIGHT_BASE_POWER_W = 32.0
SOLAR_ARRAY_MAX_W = 64.0   # Deployed wings: ~64W peak sunlit generation
BATTERY_CAPACITY_WH = 120.0
INITIAL_SOC_PERCENT = 80.0
MIN_SOC_PERCENT = 25.0
# Fallback only — the real value comes from payload.yaml total_storage_mb
# (20000 MB). Kept so BudgetTracker() still works without a config dir.
ONBOARD_STORAGE_MB = 20000.0
# Fallbacks for the two rungs of ttc.yaml's tm_rate_tiers the planner uses.
# DUMP = the top tier (HIGH, 128 kbps, dumps only, gated on elevation);
# ROUTINE = the default_tier (LOW, 16 kbps, everything else). DOWNLINK_RATE_BPS
# is the legacy single-rate name and is an alias of the DUMP rate — a
# `data_dump` is the only activity that ever saw the old "high rate".
DUMP_RATE_BPS = 128000
ROUTINE_RATE_BPS = 16000
DOWNLINK_RATE_BPS = DUMP_RATE_BPS
#: Elevation below which the HIGH tier is refused by the spacecraft
#: (ttc.yaml tm_rate_tiers HIGH min_elevation_deg). Planning must respect it or
#: it books capacity the spacecraft will not deliver.
MIN_DUMP_ELEVATION_DEG = 10.0
#: Station elevation mask — the horizon a contact window is computed against
#: (orbit.yaml ground_stations[].min_elevation_deg).
ELEVATION_MASK_DEG = 5.0
#: Orbit altitude used for the pass-geometry arithmetic (orbit.yaml altitude_km).
ORBIT_ALTITUDE_KM = 450.0
EARTH_RADIUS_KM = 6378.137
#: Channel expansion of the housekeeping stream: RS(255,223) (x255/223) and the
#: 1115/1107-byte TM transfer frame. Matches the research report's 1351.5 bps
#: of PUS packets -> 1557 bps of channel.
HK_CHANNEL_EXPANSION = (255.0 / 223.0) * (1115.0 / 1107.0)
PROTOCOL_OVERHEAD = 0.20  # 20% overhead
MB = 1024.0 * 1024.0      # bytes per MB used throughout the data budget
# Link-budget constants — DEAD: nothing in this module reads them. The
# elevation-dependent throughput is modelled by _elevation_efficiency() and the
# rate comes from ttc.yaml. They are kept only so an external importer does not
# break, and they are STALE (UHF; the mission flies S-band per ttc.yaml /
# planning/ground_stations.yaml — DI-05). Do not add a caller: derive a real
# link budget from the RF config instead.
TX_EIRP_DBW = 0.0        # unused (stale: UHF 1 W)
FREQ_GHZ = 0.4015        # unused (stale: UHF 401.5 MHz)
SYSTEM_NOISE_TEMP_K = 290.0  # Ground station system noise temperature
BOLTZMANN_DBW = -228.6    # Boltzmann constant in dBW/K/Hz
REQUIRED_EBNО_DB = 10.0   # Required Eb/N0 for BPSK BER=1e-6
IMPLEMENTATION_LOSS_DB = 2.0  # Modulation/coding implementation loss


def _central_angle_deg(elevation_deg: float, altitude_km: float) -> float:
    """Earth-central angle between the sub-satellite point and a station that
    sees the spacecraft at *elevation_deg*.

        gamma(eps) = arccos( (Re/r) * cos eps ) - eps

    At 450 km this is 16.481 deg at the 5 deg mask, 13.088 deg at 10 deg and
    0 deg at zenith — the geometry table of the data-rate research report.
    """
    r = EARTH_RADIUS_KM + max(altitude_km, 1.0)
    eps = math.radians(max(-90.0, min(90.0, elevation_deg)))
    arg = max(-1.0, min(1.0, (EARTH_RADIUS_KM / r) * math.cos(eps)))
    return math.degrees(math.acos(arg) - eps)


def fraction_above_elevation(
    max_elevation_deg: float,
    gate_deg: float,
    mask_deg: float = ELEVATION_MASK_DEG,
    altitude_km: float = ORBIT_ALTITUDE_KM,
) -> float:
    """Fraction of a contact window spent above *gate_deg* elevation.

    A circular-orbit pass whose highest elevation is ``max_elevation_deg``
    passes the station at a closest-approach central angle
    ``gamma_min = gamma(max_elevation_deg)``. The along-track half-angle over
    which the station sees the spacecraft above elevation ``eps`` is

        Delta(eps) = arccos( cos gamma(eps) / cos gamma_min )

    so the fraction of the window above the gate is
    ``Delta(gate) / Delta(mask)``. This is the *derived* answer to "how much of
    this pass can actually run at HIGH", replacing the triangular
    "elevation efficiency" guess the budget used when there was only one rate.

    Returns 0.0 for a pass that never clears the gate and 1.0 for a gate at or
    below the mask.
    """
    if gate_deg <= mask_deg:
        return 1.0
    if max_elevation_deg <= gate_deg:
        return 0.0
    g_min = _central_angle_deg(max_elevation_deg, altitude_km)
    cos_min = math.cos(math.radians(g_min))
    if cos_min <= 0.0:
        return 0.0

    def _half_angle(eps: float) -> float:
        ratio = math.cos(math.radians(_central_angle_deg(eps, altitude_km))) / cos_min
        return math.degrees(math.acos(max(-1.0, min(1.0, ratio))))

    span_mask = _half_angle(mask_deg)
    if span_mask <= 1e-9:
        return 0.0
    return max(0.0, min(1.0, _half_angle(gate_deg) / span_mask))


def hk_stream_bps_from_config(config_dir: str | Path) -> float:
    """Channel bit rate of the CONTINUOUS housekeeping stream, from config.

    Sums ``telemetry/hk_structures.yaml`` SIDs 1-6 (the periodic bus HK; SID 11
    is the beacon and is not part of routine traffic) using the shipped packet
    model — 6 B CCSDS primary + 10 B PUS-C secondary + 1 B SID + data field +
    2 B CRC — and expands the result for RS(255,223) and TM framing.

    The shipped structures give 1351.5 bps of packets = **1557 bps of channel**.
    That is 9.7 % duty at LOW and the reason the old 1 kbps "low rate" could
    never carry routine operations: it was *below* the generation rate.
    """
    import struct

    try:
        from smo_common.config.loader import load_yaml
        data = load_yaml(Path(config_dir) / "telemetry" / "hk_structures.yaml") or {}
    except Exception as e:                           # pragma: no cover - defensive
        logger.warning("hk_structures.yaml unreadable (%s) — HK stream unknown", e)
        return 0.0

    total_bps = 0.0
    for struct_def in data.get("structures", []) or []:
        sid = int(struct_def.get("sid", 0))
        interval_s = float(struct_def.get("interval_s", 0.0) or 0.0)
        if sid < 1 or sid > 6 or interval_s <= 0.0:
            continue
        payload_b = 0
        for p in struct_def.get("parameters", []) or []:
            try:
                payload_b += struct.calcsize(str(p.get("pack_format", "B")))
            except Exception:                        # pragma: no cover - defensive
                payload_b += 1
        packet_b = 6 + 10 + 1 + payload_b + 2
        total_bps += packet_b * 8.0 / interval_s
    return total_bps * HK_CHANNEL_EXPANSION


def budget_params_from_config(
    config_dir: str | Path,
    planner_cfg: Any | None = None,
) -> dict:
    """Resolve budget parameters from the mission + planner configs.

    Every number the data/power budget needs is taken from the spacecraft
    config (payload.yaml / ttc.yaml / eps.yaml) unless ``planner.yaml``
    explicitly overrides it. Missing files fall back to the module constants,
    so this never raises.
    """
    from smo_planner.planner_config import load_planner_config

    config_dir = Path(config_dir)
    if planner_cfg is None:
        try:
            planner_cfg = load_planner_config(config_dir)
        except Exception as e:                       # pragma: no cover - defensive
            logger.warning("planner.yaml unreadable (%s) — budget defaults used", e)
            planner_cfg = None

    b = getattr(planner_cfg, "budget", None)
    params: dict[str, Any] = {
        "initial_soc": getattr(b, "initial_soc_percent", INITIAL_SOC_PERCENT),
        "battery_capacity_wh": BATTERY_CAPACITY_WH,
        "storage_capacity_mb": ONBOARD_STORAGE_MB,
        "image_size_mb": None,
        # Legacy single-rate key == the DUMP rate (see module docstring).
        "downlink_rate_bps": DUMP_RATE_BPS,
        "dump_rate_bps": DUMP_RATE_BPS,
        "routine_rate_bps": ROUTINE_RATE_BPS,
        "dump_tier_name": "HIGH",
        "routine_tier_name": "LOW",
        "default_min_dump_elevation_deg": MIN_DUMP_ELEVATION_DEG,
        "elevation_mask_deg": ELEVATION_MASK_DEG,
        "orbit_altitude_km": ORBIT_ALTITUDE_KM,
        "hk_stream_bps": 0.0,
        "protocol_overhead": getattr(b, "protocol_overhead", PROTOCOL_OVERHEAD),
        "hk_background_mb_per_hour": getattr(b, "hk_background_mb_per_hour", 0.0),
        "storage_margin_percent": getattr(b, "storage_margin_percent", 10.0),
        "station_downlink_rate_bps": {},
        "min_dump_elevation_deg": {},
        "station_setup_s": {},
        "sources": {},
    }

    # Spacecraft config is canonical for storage / image size / rate.
    try:
        from smo_common.config.loader import load_all_subsystem_configs
        subs = load_all_subsystem_configs(config_dir)
        payload = subs.get("payload")
        ttc = subs.get("ttc")
        eps = subs.get("eps")
        if payload is not None:
            params["storage_capacity_mb"] = float(payload.total_storage_mb)
            params["image_size_mb"] = float(payload.image_size_mb)
            params["sources"]["storage_capacity_mb"] = "subsystems/payload.yaml total_storage_mb"
            params["sources"]["image_size_mb"] = "subsystems/payload.yaml image_size_mb"
        if ttc is not None:
            # ── The rate ladder, not a single rate (defect #86) ──
            # ROUTINE = ttc.yaml default_tier (LOW). DUMP = the top rung
            # (HIGH), which is the only tier a data_dump can reach and the
            # only one carrying an elevation gate.
            routine = ttc.tier(ttc.default_tier)
            dump = ttc.tm_rate_tiers[-1]
            params["routine_rate_bps"] = float(routine.bps)
            params["routine_tier_name"] = routine.name
            params["dump_rate_bps"] = float(dump.bps)
            params["dump_tier_name"] = dump.name
            params["downlink_rate_bps"] = float(dump.bps)
            if dump.min_elevation_deg > 0.0:
                params["default_min_dump_elevation_deg"] = float(dump.min_elevation_deg)
            src = ("subsystems/ttc.yaml tm_rate_tiers "
                   f"({routine.name} {routine.bps} bps routine / "
                   f"{dump.name} {dump.bps} bps dump)")
            params["sources"]["downlink_rate_bps"] = src
            params["sources"]["routine_rate_bps"] = src
            params["sources"]["dump_rate_bps"] = src
            params["sources"]["default_min_dump_elevation_deg"] = (
                f"subsystems/ttc.yaml tm_rate_tiers[{dump.name}].min_elevation_deg")
        if eps is not None:
            params["battery_capacity_wh"] = float(eps.battery.capacity_wh)
            params["sources"]["battery_capacity_wh"] = "subsystems/eps.yaml battery.capacity_wh"
    except Exception as e:                           # pragma: no cover - defensive
        logger.warning("Subsystem configs unreadable (%s) — budget defaults used", e)

    # planner.yaml overrides (null in YAML = "inherit the spacecraft value").
    for planner_field, key in (
        ("battery_capacity_wh", "battery_capacity_wh"),
        ("storage_capacity_mb", "storage_capacity_mb"),
        ("image_size_mb", "image_size_mb"),
        ("downlink_rate_bps", "downlink_rate_bps"),
    ):
        val = getattr(b, planner_field, None) if b is not None else None
        if val is not None:
            params[key] = float(val)
            params["sources"][key] = "planning/planner.yaml budget"
    # An explicit planner.yaml downlink_rate_bps overrides the DUMP rung only;
    # the routine rung stays what the spacecraft says it is.
    if params["sources"].get("downlink_rate_bps") == "planning/planner.yaml budget":
        params["dump_rate_bps"] = params["downlink_rate_bps"]

    for st in getattr(planner_cfg, "stations", []) or []:
        if st.downlink_rate_bps is not None:
            params["station_downlink_rate_bps"][st.name] = float(st.downlink_rate_bps)
        params["min_dump_elevation_deg"][st.name] = float(st.min_dump_elevation_deg)
        params["station_setup_s"][st.name] = float(st.setup_time_s) + float(
            st.teardown_time_s)
        if st.min_elevation_deg is not None:
            params["elevation_mask_deg"] = float(st.min_elevation_deg)

    # Orbit altitude drives the pass geometry the elevation gate is derived from.
    try:
        from smo_common.config.loader import load_yaml
        orbit = load_yaml(config_dir / "orbit.yaml") or {}
        if orbit.get("altitude_km"):
            params["orbit_altitude_km"] = float(orbit["altitude_km"])
            params["sources"]["orbit_altitude_km"] = "orbit.yaml altitude_km"
    except Exception as e:                           # pragma: no cover - defensive
        logger.warning("orbit.yaml unreadable (%s) — 450 km assumed", e)

    params["hk_stream_bps"] = hk_stream_bps_from_config(config_dir)
    if params["hk_stream_bps"] > 0:
        params["sources"]["hk_stream_bps"] = (
            "telemetry/hk_structures.yaml SIDs 1-6, +RS(255,223) and TM framing")

    return params


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
        downlink_rate_bps: float = DOWNLINK_RATE_BPS,
        protocol_overhead: float = PROTOCOL_OVERHEAD,
        image_size_mb: float | None = None,
        hk_background_mb_per_hour: float = 0.0,
        storage_margin_percent: float = 10.0,
        station_downlink_rate_bps: dict[str, float] | None = None,
        min_dump_elevation_deg: dict[str, float] | None = None,
        initial_onboard_mb: float = 0.0,
        sources: dict[str, str] | None = None,
        routine_rate_bps: float | None = None,
        dump_rate_bps: float | None = None,
        routine_tier_name: str = "LOW",
        dump_tier_name: str = "HIGH",
        default_min_dump_elevation_deg: float = MIN_DUMP_ELEVATION_DEG,
        elevation_mask_deg: float = ELEVATION_MASK_DEG,
        orbit_altitude_km: float = ORBIT_ALTITUDE_KM,
        hk_stream_bps: float = 0.0,
        station_setup_s: dict[str, float] | None = None,
    ):
        self._contacts = sorted(contacts or [], key=lambda c: c.get("aos", ""))
        self._schedule = sorted(schedule or [], key=lambda a: a.get("start_time", ""))
        self._ground_track = ground_track or []
        self._initial_soc = initial_soc
        self._battery_wh = battery_capacity_wh
        self._storage_mb = storage_capacity_mb
        self._downlink_rate_bps = float(downlink_rate_bps)
        self._overhead = float(protocol_overhead)
        self._image_size_mb = image_size_mb
        self._hk_mb_per_hour = float(hk_background_mb_per_hour)
        self._storage_margin_percent = float(storage_margin_percent)
        self._station_rates = dict(station_downlink_rate_bps or {})
        self._min_dump_el = dict(min_dump_elevation_deg or {})
        self._initial_onboard_mb = float(initial_onboard_mb)
        self._sources = dict(sources or {})
        # ── Rate ladder (defect #86) ──
        # `downlink_rate_bps` remains the legacy name for the DUMP rung, so a
        # caller that knows nothing about the ladder still gets the rate a
        # data_dump uses. `routine_rate_bps` defaults to it too, which makes a
        # bare BudgetTracker() behave exactly as the single-rate version did.
        self._dump_rate_bps = float(
            dump_rate_bps if dump_rate_bps is not None else downlink_rate_bps)
        self._routine_rate_bps = float(
            routine_rate_bps if routine_rate_bps is not None else downlink_rate_bps)
        self._routine_tier = str(routine_tier_name)
        self._dump_tier = str(dump_tier_name)
        self._default_min_dump_el = float(default_min_dump_elevation_deg)
        self._mask_deg = float(elevation_mask_deg)
        self._altitude_km = float(orbit_altitude_km)
        self._hk_stream_bps = float(hk_stream_bps)
        self._station_setup_s = dict(station_setup_s or {})

    @classmethod
    def from_config(
        cls,
        config_dir: str | Path,
        planner_cfg: Any | None = None,
        contacts: list[dict] | None = None,
        schedule: list[dict] | None = None,
        ground_track: list[dict] | None = None,
        initial_onboard_mb: float = 0.0,
    ) -> "BudgetTracker":
        """Build a tracker whose parameters come from the mission config."""
        p = budget_params_from_config(config_dir, planner_cfg)
        return cls(
            contacts=contacts, schedule=schedule, ground_track=ground_track,
            initial_soc=p["initial_soc"],
            battery_capacity_wh=p["battery_capacity_wh"],
            storage_capacity_mb=p["storage_capacity_mb"],
            downlink_rate_bps=p["downlink_rate_bps"],
            protocol_overhead=p["protocol_overhead"],
            image_size_mb=p["image_size_mb"],
            hk_background_mb_per_hour=p["hk_background_mb_per_hour"],
            storage_margin_percent=p["storage_margin_percent"],
            station_downlink_rate_bps=p["station_downlink_rate_bps"],
            min_dump_elevation_deg=p["min_dump_elevation_deg"],
            initial_onboard_mb=initial_onboard_mb,
            sources=p["sources"],
            routine_rate_bps=p["routine_rate_bps"],
            dump_rate_bps=p["dump_rate_bps"],
            routine_tier_name=p["routine_tier_name"],
            dump_tier_name=p["dump_tier_name"],
            default_min_dump_elevation_deg=p["default_min_dump_elevation_deg"],
            elevation_mask_deg=p["elevation_mask_deg"],
            orbit_altitude_km=p["orbit_altitude_km"],
            hk_stream_bps=p["hk_stream_bps"],
            station_setup_s=p["station_setup_s"],
        )

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

    # ── Data budget ──────────────────────────────────────────────

    def _dump_rate_for(self, gs_name: str) -> float:
        """Raw DUMP-tier (HIGH) bit rate for a station."""
        return float(self._station_rates.get(gs_name, self._dump_rate_bps))

    def _effective_rate_bps(self, gs_name: str) -> float:
        """Effective (post-overhead) DUMP-tier bit rate for a station.

        Kept as the legacy single-rate accessor; the per-pass model below
        blends this with the routine rate according to the elevation gate.
        """
        return self._dump_rate_for(gs_name) * (1.0 - self._overhead)

    def _dump_gate_deg(self, gs_name: str) -> float:
        """Elevation the DUMP tier needs at this station (deg)."""
        return float(self._min_dump_el.get(gs_name, self._default_min_dump_el))

    def _usable_seconds(self, contact: dict) -> float:
        """Contact seconds left after ground/onboard setup and teardown."""
        duration_s = float(contact.get("duration_s", 0) or 0.0)
        overhead_s = float(self._station_setup_s.get(contact.get("gs_name", ""), 0.0))
        return max(0.0, duration_s - overhead_s)

    def _pass_profile(self, contact: dict) -> dict:
        """Rate profile of one contact window under the ladder.

        A pass is not one rate. Above the station's ``min_dump_elevation_deg``
        the spacecraft will accept the DUMP tier (HIGH); below it the tier is
        refused and the same seconds carry only the routine tier (LOW). The
        split point is geometry, so the fraction is derived
        (:func:`fraction_above_elevation`) rather than assumed.
        """
        gs_name = contact.get("gs_name", "")
        max_el_deg = float(contact.get("max_elevation_deg", 15.0) or 0.0)
        gate = self._dump_gate_deg(gs_name)
        usable_s = self._usable_seconds(contact)

        f_high = fraction_above_elevation(
            max_el_deg, gate, self._mask_deg, self._altitude_km)
        dump_bps = self._dump_rate_for(gs_name)
        routine_bps = self._routine_rate_bps
        mean_bps = f_high * dump_bps + (1.0 - f_high) * routine_bps
        capacity_mb = mean_bps * (1.0 - self._overhead) * usable_s / (8.0 * MB)
        return {
            "capacity_mb": capacity_mb,
            "usable_s": usable_s,
            "high_fraction": f_high,
            "high_seconds": f_high * usable_s,
            "dump_tier": self._dump_tier if f_high > 0.0 else self._routine_tier,
            "dump_capable": f_high > 0.0,
            "gate_deg": gate,
            "mean_rate_bps": mean_bps,
        }

    def _pass_capacity_mb(self, contact: dict) -> tuple[float, float]:
        """(capacity_mb, high_fraction) for one contact window.

        The second element is reported as ``elevation_efficiency`` for UI
        back-compatibility; under the ladder it is no longer a fudge factor but
        the fraction of the window above the dump-elevation gate.
        """
        prof = self._pass_profile(contact)
        return prof["capacity_mb"], prof["high_fraction"]

    def compute_data_budget(self) -> dict:
        """Integrate the onboard data store over the planning horizon.

        Chronological model (defect #60 — the previous version summed totals and
        so could report a comfortable margin for a plan that overflows at hour 3):

          * every non-cancelled activity generates ``data_volume_mb`` spread
            evenly over its ``duration_s``;
          * housekeeping accrues continuously at ``hk_background_mb_per_hour``;
          * downlink flows only while a dump activity OVERLAPS a real contact
            window, at that pass's blended ladder rate (DUMP tier above the
            elevation gate, routine tier below it), and never more than what is
            actually onboard;
          * the store is clamped at ``storage_capacity_mb``; anything above is
            recorded as overflow (data lost).

        Returns the legacy keys (onboard_data_mb, storage_capacity_mb,
        utilization_percent, pass_downlink, planned_generation_mb,
        planned_downlink_mb, warnings) plus the honest ones: peak fill and when,
        first overflow, end-of-horizon backlog, days_to_drain, plan_closes, and
        the ladder view (per-tier daily capacity, the housekeeping stream's
        share of contact time, the share committed to dumps).
        """
        warnings: list[str] = []
        eps_mb = 1e-9

        active = [
            a for a in self._schedule
            if a.get("state_name", "") not in ("CANCELLED", "FAILED")
        ]

        # ── Per-pass capacity (independent of the plan) ──────────
        pass_downlink: list[dict] = []
        total_capacity_mb = 0.0
        total_contact_s = 0.0
        total_usable_s = 0.0
        total_high_s = 0.0
        dump_committed_s = 0.0
        gated_dumps: list[str] = []
        for idx, contact in enumerate(self._contacts):
            prof = self._pass_profile(contact)
            capacity_mb = prof["capacity_mb"]
            total_capacity_mb += capacity_mb
            total_contact_s += float(contact.get("duration_s", 0) or 0.0)
            total_usable_s += prof["usable_s"]
            total_high_s += prof["high_seconds"]
            dump_scheduled = self._is_dump_scheduled_during(contact)
            if dump_scheduled:
                dump_committed_s += prof["usable_s"]
                if not prof["dump_capable"]:
                    gated_dumps.append(
                        f"pass {idx + 1} ({contact.get('gs_name', '?')}, max el "
                        f"{float(contact.get('max_elevation_deg', 0) or 0):.1f} deg)")
            pass_downlink.append({
                "pass_id": idx + 1,
                "gs_name": contact.get("gs_name", "unknown"),
                "aos": contact.get("aos", ""),
                "los": contact.get("los", ""),
                "duration_s": contact.get("duration_s", 0),
                "max_elevation_deg": contact.get("max_elevation_deg", 15.0),
                # Under the ladder this is the fraction of the window above the
                # dump-elevation gate, derived from pass geometry.
                "elevation_efficiency": round(prof["high_fraction"], 3),
                "high_fraction": round(prof["high_fraction"], 3),
                "usable_s": round(prof["usable_s"], 1),
                "dump_tier": prof["dump_tier"],
                "dump_capable": prof["dump_capable"],
                "min_dump_elevation_deg": prof["gate_deg"],
                "mean_rate_bps": round(prof["mean_rate_bps"], 1),
                "capacity_mb": round(capacity_mb, 1),
                "planned_dump_mb": 0.0,
                "dump_scheduled": dump_scheduled,
            })

        planned_gen_mb = sum(float(a.get("data_volume_mb", 0) or 0.0) for a in active)

        # ── Build the integration timeline ───────────────────────
        gen_intervals: list[tuple[datetime, datetime, float]] = []  # (t0, t1, mb/s)
        for a in active:
            t0 = _parse_iso(a.get("start_time", ""))
            dur = float(a.get("duration_s", 0) or 0.0)
            vol = float(a.get("data_volume_mb", 0) or 0.0)
            if t0 is None or dur <= 0 or vol <= 0:
                continue
            gen_intervals.append((t0, t0 + timedelta(seconds=dur), vol / dur))

        dump_intervals: list[tuple[datetime, datetime]] = []
        for a in active:
            name = str(a.get("name", "")).lower()
            if "dump" not in name and "downlink" not in name:
                continue
            t0 = _parse_iso(a.get("start_time", ""))
            dur = float(a.get("duration_s", 0) or 0.0)
            if t0 is None or dur <= 0:
                continue
            dump_intervals.append((t0, t0 + timedelta(seconds=dur)))

        pass_intervals: list[tuple[datetime, datetime, int, float]] = []
        for idx, contact in enumerate(self._contacts):
            aos = _parse_iso(contact.get("aos", ""))
            los = _parse_iso(contact.get("los", ""))
            if aos is None or los is None or los <= aos:
                continue
            capacity_mb, _eff = self._pass_capacity_mb(contact)
            span_s = (los - aos).total_seconds()
            pass_intervals.append((aos, los, idx, capacity_mb / span_s))

        boundaries: set[datetime] = set()
        for t0, t1, _ in gen_intervals:
            boundaries.update((t0, t1))
        for t0, t1 in dump_intervals:
            boundaries.update((t0, t1))
        for t0, t1, _i, _r in pass_intervals:
            boundaries.update((t0, t1))

        horizon_start = min(boundaries) if boundaries else None
        horizon_end = max(boundaries) if boundaries else None

        onboard_mb = self._initial_onboard_mb
        peak_mb = onboard_mb
        peak_time: datetime | None = horizon_start
        overflow_mb = 0.0
        first_overflow: datetime | None = None
        downlinked_mb = 0.0
        hk_mb = 0.0
        per_pass_dumped: dict[int, float] = {}

        if horizon_start is not None:
            times = sorted(boundaries)
            for t0, t1 in zip(times, times[1:]):
                dt_s = (t1 - t0).total_seconds()
                if dt_s <= 0:
                    continue
                gen_rate = sum(r for (a0, a1, r) in gen_intervals if a0 <= t0 and a1 >= t1)
                gen_rate += self._hk_mb_per_hour / 3600.0
                hk_mb += self._hk_mb_per_hour / 3600.0 * dt_s

                dl_rate = 0.0
                pass_idx: int | None = None
                dumping = any(d0 <= t0 and d1 >= t1 for (d0, d1) in dump_intervals)
                if dumping:
                    for (p0, p1, idx, rate) in pass_intervals:
                        if p0 <= t0 and p1 >= t1:
                            dl_rate += rate
                            pass_idx = idx
                            break

                onboard_mb += gen_rate * dt_s
                if onboard_mb > self._storage_mb:
                    lost = onboard_mb - self._storage_mb
                    overflow_mb += lost
                    onboard_mb = self._storage_mb
                    if first_overflow is None:
                        first_overflow = t0

                dumped = min(onboard_mb, dl_rate * dt_s)
                onboard_mb -= dumped
                downlinked_mb += dumped
                if pass_idx is not None and dumped > 0:
                    per_pass_dumped[pass_idx] = per_pass_dumped.get(pass_idx, 0.0) + dumped

                if onboard_mb > peak_mb:
                    peak_mb = onboard_mb
                    peak_time = t1

        for idx, mb in per_pass_dumped.items():
            pass_downlink[idx]["planned_dump_mb"] = round(mb, 1)

        utilization = (onboard_mb / self._storage_mb) * 100.0 if self._storage_mb > 0 else 0.0
        peak_utilization = (peak_mb / self._storage_mb) * 100.0 if self._storage_mb > 0 else 0.0

        # Daily downlink capacity across the horizon (all passes, if every one
        # were used) — the physical ceiling on what the plan can shift.
        horizon_h = 24.0
        if horizon_start and horizon_end and horizon_end > horizon_start:
            horizon_h = (horizon_end - horizon_start).total_seconds() / 3600.0
        contacts_span_h = self._contacts_span_hours() or horizon_h
        daily_capacity_mb = (total_capacity_mb / contacts_span_h * 24.0
                             if contacts_span_h > 0 else 0.0)
        days_to_drain = (onboard_mb / daily_capacity_mb
                         if daily_capacity_mb > eps_mb and onboard_mb > eps_mb else 0.0)

        backlog_growth_mb = onboard_mb - self._initial_onboard_mb
        plan_closes = (
            overflow_mb <= eps_mb
            and backlog_growth_mb <= eps_mb
        )

        # ── The ladder view (defect #86) ─────────────────────────
        # What a day of contact costs at each rung, so the operator can see
        # the trade instead of a single MB/day figure that hides it.
        contact_s_per_day = (total_contact_s / contacts_span_h * 24.0
                             if contacts_span_h > 0 else 0.0)
        usable_s_per_day = (total_usable_s / contacts_span_h * 24.0
                            if contacts_span_h > 0 else 0.0)
        high_s_per_day = (total_high_s / contacts_span_h * 24.0
                          if contacts_span_h > 0 else 0.0)
        eff = 1.0 - self._overhead
        tier_capacity_mb = {
            self._routine_tier: round(
                self._routine_rate_bps * eff * usable_s_per_day / (8.0 * MB), 1),
            self._dump_tier: round(
                self._dump_rate_bps * eff * usable_s_per_day / (8.0 * MB), 1),
        }

        # Housekeeping is a CHANNEL cost even though the circular S15 store
        # means it is not a STORAGE cost: a day of HK has to come down through
        # the same contact seconds the imagery wants.
        hk_stream_mb_per_day = self._hk_stream_bps * 86400.0 / (8.0 * MB)
        hk_bits_per_day = self._hk_stream_bps * 86400.0
        hk_contact_fraction = {}
        for tier_name, rate in ((self._routine_tier, self._routine_rate_bps),
                                (self._dump_tier, self._dump_rate_bps)):
            if rate > 0 and contact_s_per_day > 0:
                hk_contact_fraction[tier_name] = round(
                    100.0 * (hk_bits_per_day / rate) / contact_s_per_day, 1)
            else:
                hk_contact_fraction[tier_name] = 0.0

        # One image, in the currency the operator plans in: seconds of pass.
        image_dump_s = 0.0
        image_pass_fraction = 0.0
        mean_pass_s = (total_contact_s / len(self._contacts)
                       if self._contacts else 0.0)
        if self._image_size_mb and self._dump_rate_bps > 0:
            image_dump_s = (float(self._image_size_mb) * 8.0 * MB
                            / (self._dump_rate_bps * eff))
            if mean_pass_s > 0:
                image_pass_fraction = 100.0 * image_dump_s / mean_pass_s

        dump_contact_fraction = (100.0 * dump_committed_s / total_usable_s
                                 if total_usable_s > 0 else 0.0)

        # ── Warnings (honest, not reassuring) ────────────────────
        if gated_dumps:
            warnings.append(
                f"DUMP_BELOW_ELEVATION_GATE: {len(gated_dumps)} dump(s) are "
                f"booked on passes that never reach "
                f"{self._default_min_dump_el:.0f} deg, so the spacecraft will "
                f"refuse the {self._dump_tier} tier and they run at "
                f"{self._routine_tier} "
                f"({self._routine_rate_bps / 1000.0:.0f} kbps): "
                + "; ".join(gated_dumps[:5])
                + (" ..." if len(gated_dumps) > 5 else "")
            )
        if overflow_mb > eps_mb:
            warnings.append(
                f"STORAGE_OVERFLOW: onboard data exceeds storage capacity "
                f"({self._storage_mb:.0f} MB) from "
                f"{first_overflow.isoformat() if first_overflow else 'unknown'}; "
                f"{overflow_mb:.1f} MB lost over the horizon"
            )
        elif peak_mb > self._storage_mb * (1.0 - self._storage_margin_percent / 100.0):
            warnings.append(
                f"Peak onboard data {peak_mb:.1f} MB is inside the "
                f"{self._storage_margin_percent:.0f}% storage margin "
                f"({self._storage_mb:.0f} MB capacity)"
            )
        if planned_gen_mb > 0 and downlinked_mb <= eps_mb:
            warnings.append(
                "Data generation planned but no downlink passes scheduled"
            )
        if backlog_growth_mb > eps_mb:
            warnings.append(
                f"DOWNLINK_INFEASIBLE: plan does not close — backlog grows by "
                f"{backlog_growth_mb:.1f} MB over {horizon_h:.1f} h "
                f"(generated {planned_gen_mb + hk_mb:.1f} MB, downlinked "
                f"{downlinked_mb:.1f} MB). Total pass capacity is "
                f"{daily_capacity_mb:.1f} MB/day on the ladder "
                f"({self._dump_tier} {self._dump_rate_bps / 1000.0:.0f} kbps "
                f"above {self._default_min_dump_el:.0f} deg, "
                f"{self._routine_tier} {self._routine_rate_bps / 1000.0:.0f} kbps "
                f"below it)"
                + (f"; {days_to_drain:.1f} days of contact time needed to drain"
                   if days_to_drain else "")
            )
        unused = [p for p in pass_downlink if not p["dump_scheduled"]]
        if planned_gen_mb > 0 and unused:
            warnings.append(
                f"PASS_WITHOUT_DUMP: {len(unused)} of {len(pass_downlink)} "
                f"contact windows carry no dump "
                f"({sum(p['capacity_mb'] for p in unused):.1f} MB of unused capacity)"
            )

        return {
            # legacy keys (UI + existing tests)
            "onboard_data_mb": round(max(0.0, onboard_mb), 1),
            "storage_capacity_mb": self._storage_mb,
            "utilization_percent": round(max(0.0, min(100.0, utilization)), 1),
            "pass_downlink": pass_downlink,
            "planned_generation_mb": round(planned_gen_mb, 1),
            "planned_downlink_mb": round(downlinked_mb, 1),
            "warnings": warnings,
            # honest model output
            "initial_onboard_mb": round(self._initial_onboard_mb, 1),
            "hk_generation_mb": round(hk_mb, 1),
            "peak_onboard_mb": round(peak_mb, 1),
            "peak_utilization_percent": round(max(0.0, min(100.0, peak_utilization)), 1),
            "peak_utc": peak_time.isoformat() if peak_time else None,
            "first_overflow_utc": first_overflow.isoformat() if first_overflow else None,
            "overflow_mb": round(overflow_mb, 1),
            "backlog_growth_mb": round(backlog_growth_mb, 1),
            "total_pass_capacity_mb": round(total_capacity_mb, 1),
            "daily_downlink_capacity_mb": round(daily_capacity_mb, 1),
            "days_to_drain": round(days_to_drain, 2),
            # ── The rate ladder (defect #86) ──
            "routine_rate_bps": self._routine_rate_bps,
            "routine_tier_name": self._routine_tier,
            "dump_rate_bps": self._dump_rate_bps,
            "dump_tier_name": self._dump_tier,
            "min_dump_elevation_deg": self._default_min_dump_el,
            "daily_capacity_by_tier_mb": tier_capacity_mb,
            "contact_seconds_per_day": round(contact_s_per_day, 1),
            "usable_contact_seconds_per_day": round(usable_s_per_day, 1),
            "dump_capable_seconds_per_day": round(high_s_per_day, 1),
            "passes_dump_capable": sum(
                1 for p in pass_downlink if p["dump_capable"]),
            "passes_total": len(pass_downlink),
            "hk_stream_bps": round(self._hk_stream_bps, 1),
            "hk_stream_mb_per_day": round(hk_stream_mb_per_day, 2),
            "hk_contact_fraction_percent": hk_contact_fraction,
            "image_dump_seconds": round(image_dump_s, 1),
            "image_pass_fraction_percent": round(image_pass_fraction, 1),
            "mean_pass_seconds": round(mean_pass_s, 1),
            "dump_contact_fraction_percent": round(dump_contact_fraction, 1),
            "plan_closes": plan_closes,
            "horizon_hours": round(horizon_h, 2),
            "downlink_rate_bps": self._downlink_rate_bps,
            "effective_downlink_rate_bps": round(
                self._downlink_rate_bps * (1.0 - self._overhead), 1),
            "image_size_mb": self._image_size_mb,
            "config_sources": self._sources,
        }

    def _contacts_span_hours(self) -> float:
        """Wall span covered by the contact list, in hours (0 if unknown)."""
        aos_times = [_parse_iso(c.get("aos", "")) for c in self._contacts]
        los_times = [_parse_iso(c.get("los", "")) for c in self._contacts]
        aos_times = [t for t in aos_times if t is not None]
        los_times = [t for t in los_times if t is not None]
        if not aos_times or not los_times:
            return 0.0
        span = (max(los_times) - min(aos_times)).total_seconds() / 3600.0
        return max(span, 0.0)

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
