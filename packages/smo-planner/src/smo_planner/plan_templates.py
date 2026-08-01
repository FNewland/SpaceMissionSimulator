"""SMO Planner — standard activity templates ("Load standard activities").

The planner starts with an EMPTY schedule (``planner.yaml
default_plan.start_empty``). Standard activity sets are *config-defined*
templates in ``configs/<mission>/planning/standard_plans.yaml`` that the
operator instantiates explicitly, relative to the current (sim) time and the
real contact windows — replacing the hard-coded JavaScript ``SAMPLE_ACTIVITIES``
fixtures that used to be drawn instead of the real schedule (defect #57).

A template entry names an activity **type** from
``planning/activity_types.yaml``; the instance is created via
``ActivityScheduler.add_activity``, which fills duration/power/data volume and
the *command sequence with its verified func_ids* from that catalogue. A
template can therefore never invent an activity type or a command routing.

Anchors
-------
``absolute``          — explicit ``start_time`` (ISO 8601).
``offset_from_now``   — ``offset_min`` after the planner's current time.
``pass_relative``     — snap to a real contact window: ``pass_index`` (0 = next
                        upcoming pass), ``at`` (``aos``|``los``|``midpoint``),
                        ``offset_min``. Optional ``station`` restricts the
                        match to that ground station and
                        ``min_pass_elevation_deg`` restricts it to passes that
                        clear an elevation gate — which is how a template books
                        its dumps on passes that can hold the HIGH tier.
``daylight_relative`` — the next sunlit ground-track sample at least
                        ``min_offset_min`` ahead (imaging/calibration).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from smo_planner.utils import parse_iso as _parse_iso

logger = logging.getLogger(__name__)

ANCHORS = ("absolute", "offset_from_now", "pass_relative", "daylight_relative")


class TemplateActivityConfig(BaseModel):
    """One activity in a standard-plan template."""
    type: str = Field(description="Activity type name from activity_types.yaml")
    anchor: str = "offset_from_now"
    offset_min: float = 0.0
    start_time: str | None = None          # anchor=absolute
    pass_index: int = 0                    # anchor=pass_relative
    at: str = "aos"                        # aos | los | midpoint
    station: str | None = None             # restrict pass_relative to a station
    min_pass_elevation_deg: float = Field(
        default=0.0,
        description="anchor=pass_relative: only consider contact windows whose "
                    "max elevation reaches this. Set it to the DUMP tier's "
                    "min_elevation_deg (ttc.yaml tm_rate_tiers) so a template "
                    "books its dumps on passes the spacecraft will actually "
                    "let run at the high rate (defect #86).")
    min_offset_min: float = 0.0            # anchor=daylight_relative
    repeat: int = Field(default=1, description="Instantiate N times ...")
    repeat_every_min: float = 0.0          # ... spaced by this many minutes
    repeat_pass_stride: int = 1            # ... or by this many passes
    priority: str | None = None
    duration_s: float | None = None
    note: str = ""


class PlanTemplateConfig(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    horizon_hours: float = 24.0
    activities: list[TemplateActivityConfig] = Field(default_factory=list)


class StandardPlansConfig(BaseModel):
    templates: list[PlanTemplateConfig] = Field(default_factory=list)

    def get(self, template_id: str) -> PlanTemplateConfig | None:
        for t in self.templates:
            if t.id == template_id:
                return t
        return None


def load_standard_plans(path: str | Path) -> StandardPlansConfig:
    """Load and validate planning/standard_plans.yaml (empty if absent)."""
    p = Path(path)
    if not p.exists():
        logger.info("No standard-plan templates at %s", p)
        return StandardPlansConfig()
    from smo_common.config.loader import load_yaml
    cfg = StandardPlansConfig.model_validate(load_yaml(p) or {})
    logger.info("Loaded %d standard-plan template(s) from %s",
                len(cfg.templates), p)
    return cfg


def validate_templates(
    cfg: StandardPlansConfig, activity_type_names: set[str] | list[str]
) -> list[str]:
    """Return a list of problems (empty = valid).

    Catches the failure mode the JS fixtures had: naming an activity that does
    not exist in the catalogue ("Orbit Maintenance") and therefore has no
    duration, no power and no command sequence.
    """
    known = set(activity_type_names)
    issues: list[str] = []
    seen: set[str] = set()
    for t in cfg.templates:
        if t.id in seen:
            issues.append(f"duplicate template id '{t.id}'")
        seen.add(t.id)
        if not t.activities:
            issues.append(f"template '{t.id}' has no activities")
        for a in t.activities:
            if known and a.type not in known:
                issues.append(
                    f"template '{t.id}': activity type '{a.type}' is not "
                    f"defined in activity_types.yaml")
            if a.anchor not in ANCHORS:
                issues.append(
                    f"template '{t.id}': unknown anchor '{a.anchor}' "
                    f"(expected one of {', '.join(ANCHORS)})")
            if a.anchor == "absolute" and not a.start_time:
                issues.append(
                    f"template '{t.id}': anchor=absolute requires start_time")
            if a.at not in ("aos", "los", "midpoint"):
                issues.append(f"template '{t.id}': unknown at='{a.at}'")
            if a.repeat < 1:
                issues.append(f"template '{t.id}': repeat must be >= 1")
    return issues


# ── Anchor resolution ───────────────────────────────────────────────

def _upcoming_passes(contacts: list[dict], now: datetime,
                     station: str | None,
                     min_max_elevation_deg: float = 0.0) -> list[dict]:
    out = []
    for c in contacts or []:
        los = _parse_iso(c.get("los", ""))
        if los is None or los <= now:
            continue
        if station and c.get("gs_name") != station:
            continue
        if min_max_elevation_deg > 0.0 and float(
                c.get("max_elevation_deg", 0.0) or 0.0) < min_max_elevation_deg:
            continue
        out.append(c)
    out.sort(key=lambda c: c.get("aos", ""))
    return out


def _pass_time(contact: dict, at: str) -> datetime | None:
    aos = _parse_iso(contact.get("aos", ""))
    los = _parse_iso(contact.get("los", ""))
    if aos is None:
        return None
    if at == "los":
        return los or aos
    if at == "midpoint" and los is not None:
        return aos + (los - aos) / 2
    return aos


def _next_daylight(ground_track: list[dict], after: datetime) -> datetime | None:
    for pt in ground_track or []:
        if pt.get("in_eclipse"):
            continue
        t = _parse_iso(pt.get("utc", ""))
        if t is not None and t >= after:
            return t
    return None


def instantiate_template(
    template: PlanTemplateConfig,
    now: datetime,
    contacts: list[dict] | None = None,
    ground_track: list[dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """Resolve a template into concrete activity specs at *now*.

    Returns ``(specs, warnings)`` where each spec is
    ``{"name": <activity type>, "start_time": iso, "extras": {...}}`` ready for
    ``ActivityScheduler.add_activity(name, start_time, **extras)``.
    """
    specs: list[dict] = []
    warnings: list[str] = []
    contacts = contacts or []
    ground_track = ground_track or []

    for entry in template.activities:
        for rep in range(max(1, entry.repeat)):
            start: datetime | None = None
            extras: dict[str, Any] = {}

            if entry.anchor == "absolute":
                start = _parse_iso(entry.start_time or "")
                if start is not None and rep:
                    start = start + timedelta(minutes=entry.repeat_every_min * rep)
            elif entry.anchor == "offset_from_now":
                start = now + timedelta(
                    minutes=entry.offset_min + entry.repeat_every_min * rep)
            elif entry.anchor == "pass_relative":
                upcoming = _upcoming_passes(
                    contacts, now, entry.station, entry.min_pass_elevation_deg)
                idx = entry.pass_index + rep * max(1, entry.repeat_pass_stride)
                if idx >= len(upcoming):
                    warnings.append(
                        f"{entry.type}: no contact window at pass index {idx}"
                        + (f" for station {entry.station}" if entry.station else "")
                        + (f" above {entry.min_pass_elevation_deg:.0f} deg"
                           if entry.min_pass_elevation_deg > 0 else "")
                        + " — activity skipped")
                    continue
                contact = upcoming[idx]
                base = _pass_time(contact, entry.at)
                if base is None:
                    warnings.append(f"{entry.type}: unusable contact window — skipped")
                    continue
                start = base + timedelta(minutes=entry.offset_min)
                extras["station"] = contact.get("gs_name")
                extras["pass_aos"] = contact.get("aos")
                extras["pass_los"] = contact.get("los")
                # Carried so the data-volume checker can cost the dump at the
                # rung this pass can actually hold (defect #86).
                extras["pass_max_elevation_deg"] = contact.get("max_elevation_deg")
            elif entry.anchor == "daylight_relative":
                after = now + timedelta(
                    minutes=entry.min_offset_min + entry.repeat_every_min * rep)
                start = _next_daylight(ground_track, after)
                if start is None:
                    warnings.append(
                        f"{entry.type}: no sunlit ground-track sample after "
                        f"{after.isoformat()} — falling back to offset anchor")
                    start = after
                start = start + timedelta(minutes=entry.offset_min)
            else:
                warnings.append(f"{entry.type}: unknown anchor '{entry.anchor}' — skipped")
                continue

            if start is None:
                warnings.append(f"{entry.type}: could not resolve a start time — skipped")
                continue

            if entry.priority:
                extras["priority"] = entry.priority
            if entry.duration_s is not None:
                extras["duration_s"] = entry.duration_s
            if entry.note:
                extras["note"] = entry.note
            extras["template_id"] = template.id

            specs.append({
                "name": entry.type,
                "start_time": start.isoformat(),
                "extras": extras,
            })

    specs.sort(key=lambda s: s["start_time"])
    return specs, warnings
