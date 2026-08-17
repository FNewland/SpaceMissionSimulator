"""Period resolution — turn a spec ``period:`` block into a CUC window.

All selection is **CUC-based** (on-board sim time). The dump filename's
wall-clock timestamp is never consulted: under time acceleration the two
axes are unrelated (brief 06).

Kinds:

* ``dump``      — the envelope of the decoded dataset;
* ``absolute``  — ISO-8601 UTC start/end converted through the CUC epoch;
* ``relative``  — around an anchor (ISO time, or ``last_dump``);
* ``day``       — UTC-day boundaries containing the latest data;
* ``orbit``     — orbital-period bins anchored at ``orbit.yaml t0_epoch``,
  period from the TLE mean motion;
* ``pass``      — AOS/LOS event pairs found **in the telemetry stream**
  (event ids 0x0001/0x0002) when present; otherwise contact windows
  computed from the configured ground stations via the orbit propagator;
  otherwise the dump envelope (each fallback is recorded in the label).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..protocol.ecss_packet import (
    cuc_seconds_to_datetime, datetime_to_cuc_seconds)
from .catalogs import Catalogs
from .dataset import TelemetryDataset
from .spec import PeriodSpec

logger = logging.getLogger(__name__)

__all__ = ["Period", "resolve_period", "cuc_to_utc_str", "orbit_period_s"]

EVENT_AOS = 0x0001
EVENT_LOS = 0x0002


@dataclass
class Period:
    start_cuc: float
    end_cuc: float
    kind: str
    label: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return max(self.end_cuc - self.start_cuc, 0.0)

    def context(self) -> dict[str, Any]:
        """Substitution values for narrative templates (``{{period.*}}``)."""
        return {
            "kind": self.kind,
            "label": self.label,
            "start_utc": cuc_to_utc_str(self.start_cuc),
            "end_utc": cuc_to_utc_str(self.end_cuc),
            "start_cuc": int(self.start_cuc),
            "end_cuc": int(self.end_cuc),
            "duration_s": round(self.duration_s, 1),
            "duration_min": round(self.duration_s / 60.0, 1),
            **self.meta,
        }


def cuc_to_utc_str(cuc: Optional[float]) -> str:
    if cuc is None:
        return "--"
    try:
        # Full precision: CUC carries sub-second (1/65536 s) resolution.
        return (cuc_seconds_to_datetime(float(cuc))
                .replace(tzinfo=timezone.utc).isoformat())
    except (ValueError, OverflowError):
        return "--"


def _iso_to_cuc(s: str) -> float:
    dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return float(datetime_to_cuc_seconds(dt))


def orbit_period_s(catalogs: Catalogs) -> float:
    """Orbital period from the TLE mean motion (rev/day, line 2 cols 53-63)."""
    line2 = str(catalogs.orbit.get("tle_line2", "") or "")
    m = re.match(r"^2\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\d+\.\d+)", line2)
    mean_motion = None
    if m:
        try:
            mean_motion = float(m.group(1))
        except ValueError:
            mean_motion = None
    if not mean_motion and len(line2) >= 63:
        try:
            mean_motion = float(line2[52:63])
        except ValueError:
            mean_motion = None
    if not mean_motion:
        return 5670.0  # ~94.5 min LEO default
    return 86400.0 / mean_motion


def _dump_envelope(dataset: TelemetryDataset) -> tuple[float, float]:
    a, b = dataset.window()
    if a is None or b is None:
        return 0.0, 0.0
    return float(a), float(b)


def _passes_from_events(dataset: TelemetryDataset) -> list[tuple[float, float, str]]:
    """(aos_cuc, los_cuc, station_label) pairs from AOS/LOS events."""
    passes = []
    open_aos: Optional[tuple[float, str]] = None
    for e in dataset.events:
        if e["event_id"] == EVENT_AOS:
            open_aos = (e["t"], e.get("aux_text", "") or "")
        elif e["event_id"] == EVENT_LOS and open_aos is not None:
            passes.append((open_aos[0], e["t"], open_aos[1]))
            open_aos = None
    return passes


def _passes_from_propagator(catalogs: Catalogs, start: float,
                            end: float) -> list[tuple[float, float, str]]:
    try:
        from ..orbit.propagator import GroundStation, OrbitPropagator
    except Exception:  # noqa: BLE001 — sgp4 may be absent
        return []
    try:
        stations = [
            GroundStation(
                name=str(gs.get("name", "?")),
                lat_deg=float(gs["lat_deg"]), lon_deg=float(gs["lon_deg"]),
                alt_km=float(gs.get("alt_km", 0.0)),
                min_elevation_deg=float(gs.get("min_elevation_deg", 5.0)))
            for gs in catalogs.ground_stations()
        ]
        if not stations or end <= start:
            return []
        prop = OrbitPropagator(
            catalogs.orbit.get("tle_line1", ""),
            catalogs.orbit.get("tle_line2", ""),
            ground_stations=stations)
        t0 = cuc_seconds_to_datetime(float(start)).replace(tzinfo=timezone.utc)
        prop.reset(t0)
        out = []
        # Pad by 20 min so a pass straddling the envelope edge is still found.
        for w in prop.contact_windows(duration_s=(end - start) + 1200.0):
            try:
                aos = _iso_to_cuc(w["aos"])
                los = _iso_to_cuc(w["los"])
            except (KeyError, ValueError):
                continue
            out.append((aos, los, str(w.get("gs_name", ""))))
        out.sort()
        return out
    except Exception as e:  # noqa: BLE001
        logger.debug("propagator pass resolution failed: %s", e)
        return []


def passes_in(dataset: TelemetryDataset, catalogs: Catalogs) -> list[dict]:
    """All passes overlapping the dataset, events first, propagator fallback."""
    a, b = _dump_envelope(dataset)
    src = "events"
    passes = _passes_from_events(dataset)
    if not passes:
        src = "propagator"
        passes = _passes_from_propagator(catalogs, a, b)
    return [{"aos": p[0], "los": p[1], "station": p[2] or "?", "source": src}
            for p in passes if p[1] >= a and p[0] <= b]


def resolve_period(period: PeriodSpec, dataset: TelemetryDataset,
                   catalogs: Catalogs,
                   vars: dict[str, Any] | None = None) -> Period:
    """Resolve the spec's period block against the decoded dataset."""
    vars = vars or {}
    kind = period.kind
    env_a, env_b = _dump_envelope(dataset)

    if kind == "dump":
        return Period(env_a, env_b, "dump", "all selected dumps",
                      {"source": "dump_envelope"})

    if kind == "absolute":
        ab = period.absolute
        if ab is None:
            return Period(env_a, env_b, "dump", "dump envelope (absolute block missing)")
        return Period(_iso_to_cuc(_subst(ab.start, vars)),
                      _iso_to_cuc(_subst(ab.end, vars)),
                      "absolute", f"{ab.start} → {ab.end}")

    if kind == "relative":
        rel = period.relative
        if rel is None:
            return Period(env_a, env_b, "dump", "dump envelope (relative block missing)")
        anchor_str = _subst(rel.anchor, vars)
        if anchor_str == "last_dump" or not anchor_str:
            anchor = env_b
        else:
            anchor = _iso_to_cuc(anchor_str)
        return Period(anchor - rel.before_s, anchor + rel.after_s, "relative",
                      f"{rel.before_s:.0f} s before → {rel.after_s:.0f} s after "
                      f"{cuc_to_utc_str(anchor)}",
                      {"anchor_utc": cuc_to_utc_str(anchor), "anchor_cuc": anchor})

    if kind == "day":
        d = period.day
        count = d.count if d else 1
        ref = cuc_seconds_to_datetime(float(env_b or 0)).replace(tzinfo=timezone.utc)
        day_start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day_start - timedelta(days=max(count - 1, 0))
        end = day_start + timedelta(days=1)
        return Period(datetime_to_cuc_seconds(start), datetime_to_cuc_seconds(end),
                      "day", f"UTC day(s) {start.date()} → {ref.date()}",
                      {"met_day": (ref.date() - _t0_date(catalogs, ref)).days
                       if _t0_date(catalogs, ref) else "?"})

    if kind == "orbit":
        o = period.orbit
        count = o.count if o else 1
        per = orbit_period_s(catalogs)
        t0s = catalogs.t0_epoch()
        anchor = _iso_to_cuc(t0s) if t0s else env_a
        ref = env_b if env_b else anchor
        n = int((ref - anchor) // per) if per > 0 else 0
        end = anchor + (n + 1) * per
        start = end - count * per
        return Period(start, end, "orbit",
                      f"orbit(s) {n - count + 2}–{n + 1} from t0 "
                      f"(period {per / 60.0:.1f} min)",
                      {"orbit_period_s": per})

    if kind == "pass":
        p = period.pass_
        pads = (p.pad_before_s, p.pad_after_s) if p else (300.0, 300.0)
        station = (p.station if p else "any") or "any"
        select = (p.select if p else "latest") or "latest"
        cands = passes_in(dataset, catalogs)
        if station != "any":
            cands = [c for c in cands
                     if station.lower() in str(c["station"]).lower()] or cands
        chosen = None
        if cands:
            if select.startswith("index:"):
                try:
                    chosen = cands[int(select.split(":", 1)[1])]
                except (ValueError, IndexError):
                    chosen = cands[-1]
            else:
                chosen = cands[-1]
        if chosen:
            return Period(chosen["aos"] - pads[0], chosen["los"] + pads[1],
                          "pass",
                          f"pass {chosen['station']} "
                          f"{cuc_to_utc_str(chosen['aos'])} → "
                          f"{cuc_to_utc_str(chosen['los'])} "
                          f"(±pad, boundaries from {chosen['source']})",
                          {"station": chosen["station"],
                           "aos_utc": cuc_to_utc_str(chosen["aos"]),
                           "los_utc": cuc_to_utc_str(chosen["los"]),
                           "pass_source": chosen["source"]})
        return Period(env_a, env_b, "pass",
                      "dump envelope (no AOS/LOS events in stream and no "
                      "propagator contact found)", {"station": "?"})

    if kind == "breakpoint_span":
        # Breakpoint files carry sim_time; resolution is best-effort.
        bp = period.breakpoint_span
        meta = {"from": getattr(bp, "from_", None), "to": getattr(bp, "to", None)}
        span = _breakpoint_span(meta["from"], meta["to"])
        if span:
            return Period(span[0], span[1], "breakpoint_span",
                          f"breakpoints {meta['from']} → {meta['to']}", meta)
        return Period(env_a, env_b, "breakpoint_span",
                      "dump envelope (breakpoints not resolvable)", meta)

    return Period(env_a, env_b, "dump", "dump envelope")


def _breakpoint_span(name_from: str | None,
                     name_to: str | None) -> Optional[tuple[float, float]]:
    import json
    from pathlib import Path
    bp_dir = Path("workspace/breakpoints")
    if not bp_dir.is_dir() or not (name_from and name_to):
        return None

    def _time_of(name: str) -> Optional[float]:
        for p in bp_dir.glob("*.json"):
            if name in p.stem:
                try:
                    data = json.loads(p.read_text())
                except (OSError, ValueError):
                    continue
                t = data.get("sim_time") or data.get("cuc_time")
                if isinstance(t, (int, float)):
                    return float(t)
        return None

    a, b = _time_of(name_from), _time_of(name_to)
    if a is None or b is None:
        return None
    return (min(a, b), max(a, b))


def _t0_date(catalogs: Catalogs, ref):
    t0s = catalogs.t0_epoch()
    if not t0s:
        return None
    try:
        return datetime.fromisoformat(t0s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _subst(text: str, vars: dict[str, Any]) -> str:
    """Substitute ``{{vars.x}}`` in period anchors supplied via templates."""
    out = str(text)
    for k, v in vars.items():
        out = out.replace("{{vars.%s}}" % k, str(v))
    return out
