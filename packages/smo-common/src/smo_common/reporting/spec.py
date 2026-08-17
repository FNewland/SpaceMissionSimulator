"""Report definition schema — pydantic v2, ``extra="forbid"``.

The whole point of the YAML report-definition format is that instructors and
students edit it, so an unknown key anywhere in a spec must fail loudly with
the path to the offending key rather than being silently dropped (the exact
failure mode that made half of the mission config decorative — brief 06 §B
validation design, gate 1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = ["ReportSpec", "load_spec", "validate_spec", "SpecError"]


class SpecError(ValueError):
    """A report definition failed validation. ``errors`` lists path+message."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(f"{e['path']}: {e['msg']}" for e in errors))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── report identity ──────────────────────────────────────────────────────

class ReportIdent(_Strict):
    id: str
    title: str
    subtitle: str = ""
    audience: Literal["operator", "student", "instructor"] = "operator"
    classification: str = "TRAINING — NOT FOR FLIGHT"
    page_size: str = "A4"


# ── sources ──────────────────────────────────────────────────────────────

class DumpSource(_Strict):
    mode: Literal["auto", "files", "none"] = "auto"
    store_ids: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    files: list[str] = Field(default_factory=list)
    api: Optional[str] = None


class GroundArchiveSource(_Strict):
    enabled: bool = False
    base_url: Optional[str] = None
    use_for: list[str] = Field(default_factory=list)


class CatalogSource(_Strict):
    config_dir: str = "configs/eosat1"


class SourceSpec(_Strict):
    dumps: DumpSource = Field(default_factory=DumpSource)
    ground_archive: GroundArchiveSource = Field(default_factory=GroundArchiveSource)
    catalogs: CatalogSource = Field(default_factory=CatalogSource)
    on_missing_source: Literal["warn", "error"] = "warn"


# ── period ───────────────────────────────────────────────────────────────

class PassPeriod(_Strict):
    select: str = "latest"                  # latest | index:<n> | station:<name>
    station: str = "any"
    pad_before_s: float = 300.0
    pad_after_s: float = 300.0
    source: Literal["planner", "propagator", "dump_envelope", "events"] = "events"


class OrbitPeriod(_Strict):
    select: str = "latest"
    count: int = 1
    boundary: str = "ascending_node"


class DayPeriod(_Strict):
    select: str = "latest_utc"
    count: int = 1
    boundary: str = "00:00:00Z"


class BreakpointSpanPeriod(_Strict):
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AbsolutePeriod(_Strict):
    start: str
    end: str


class RelativePeriod(_Strict):
    anchor: str = "last_dump"
    before_s: float = 1800.0
    after_s: float = 600.0


class PeriodSpec(_Strict):
    kind: Literal["pass", "orbit", "day", "breakpoint_span",
                  "dump", "absolute", "relative"] = "dump"
    pass_: Optional[PassPeriod] = Field(default=None, alias="pass")
    orbit: Optional[OrbitPeriod] = None
    day: Optional[DayPeriod] = None
    breakpoint_span: Optional[BreakpointSpanPeriod] = None
    absolute: Optional[AbsolutePeriod] = None
    relative: Optional[RelativePeriod] = None
    #: on-board CUC is the authoritative axis. "wall" exists only so a report
    #: can be scoped by archive-file wall time explicitly; it is never the
    #: default and never mixes with CUC.
    timebase: Literal["cuc", "wall"] = "cuc"
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ── defaults ─────────────────────────────────────────────────────────────

class PlotDefaults(_Strict):
    height_px: int = 240
    grid: bool = True
    show_limits: bool = True
    show_eclipse: bool = True
    show_contacts: bool = False
    marker_events: list[str] = Field(default_factory=list)


class StatsDefaults(_Strict):
    columns: list[str] = Field(default_factory=lambda: [
        "count", "min", "max", "mean", "stddev", "first", "last"])
    round: int = 3


class DefaultsSpec(_Strict):
    plot: PlotDefaults = Field(default_factory=PlotDefaults)
    stats: StatsDefaults = Field(default_factory=StatsDefaults)


# ── sections ─────────────────────────────────────────────────────────────
# ``parameters`` accepts a list of names/ids or the string
# "parameter_sets.<name>" referencing the spec-level parameter_sets block.

ParamRef = Union[str, list[str]]


class HeaderSection(_Strict):
    type: Literal["header"]
    id: str = "cover"
    title: str = ""
    include: list[str] = Field(default_factory=lambda: [
        "period", "dumps_used", "packet_totals", "crc_errors", "generated_at"])


class NarrativeSection(_Strict):
    type: Literal["narrative"]
    id: str
    title: str = ""
    text: str = ""


class CompletenessSection(_Strict):
    type: Literal["completeness"]
    id: str
    title: str = "Data completeness"
    sids: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    expected_cadence: str = "from_config"
    gap_threshold_multiple: float = 3.0
    breakdown: list[str] = Field(default_factory=lambda: ["per_sid"])
    show: list[str] = Field(default_factory=lambda: [
        "expected", "received", "missing_pct", "longest_gap_s", "gap_list"])
    correlate_with: list[str] = Field(default_factory=list)


class PlotAxis(_Strict):
    label: str = ""
    parameters: ParamRef = Field(default_factory=list)
    range: Optional[list[float]] = None
    side: Literal["left", "right"] = "left"
    log: bool = False


class PlotAnnotations(_Strict):
    eclipse: bool = False
    contacts: bool = False
    events: list[int] = Field(default_factory=list)
    t0_marker: Optional[str] = None


class PlotSection(_Strict):
    type: Literal["plot"]
    id: str
    title: str = ""
    style: Literal["time_series", "multi_axis", "step", "histogram", "xy"] = "time_series"
    axes: list[PlotAxis] = Field(default_factory=list)
    parameters: ParamRef = Field(default_factory=list)
    annotations: PlotAnnotations = Field(default_factory=PlotAnnotations)
    value_labels: str = "raw"
    show_transitions_table: bool = False
    bins: int = 40
    overlay: list[str] = Field(default_factory=list)


class StatsGroup(_Strict):
    label: str
    parameters: ParamRef = Field(default_factory=list)


class StatsSection(_Strict):
    type: Literal["stats"]
    id: str
    title: str = "Parameter statistics"
    groups: list[StatsGroup] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=lambda: [
        "count", "min", "max", "mean", "stddev", "first", "last",
        "ool_yellow_s", "ool_red_s", "ool_pct"])
    limits_from: str = "mcs/limits.yaml"
    flag_rule: str = ""
    split_at: Optional[str] = None


class EventCorrelate(_Strict):
    window_s: float = 60.0
    with_: list[str] = Field(default_factory=list, alias="with")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EventsSection(_Strict):
    type: Literal["events"]
    id: str
    title: str = "Event review"
    severity_min: int = 1
    include_synthetic_s12: bool = True
    resolve_names: str = "event_catalog"
    group_by: Literal["none", "subsystem", "severity", "pass"] = "none"
    show: list[str] = Field(default_factory=lambda: [
        "utc", "cuc", "id", "name", "severity", "subsystem", "aux_text"])
    correlate: Optional[EventCorrelate] = None


class S19Correlation(_Strict):
    enabled: bool = False
    rules_from: str = "monitoring/s19_rules.yaml"
    show: list[str] = Field(default_factory=list)


class AlarmsSection(_Strict):
    type: Literal["alarms"]
    id: str
    title: str = "Limit violations"
    sources: list[str] = Field(default_factory=lambda: ["s12_transitions"])
    severity_min: int = 1
    show_definition: bool = True
    pair_entries_and_clears: bool = True
    unpaired_policy: Literal["report", "ignore"] = "report"
    s19_correlation: Optional[S19Correlation] = None


class CommandsSection(_Strict):
    type: Literal["commands"]
    id: str
    title: str = "Command history"
    source: list[str] = Field(default_factory=lambda: ["s1_verification"])
    show: list[str] = Field(default_factory=lambda: [
        "utc", "seq", "apid", "service", "subtype", "accept", "execute",
        "error_code", "error_text", "reason"])
    outcomes: list[str] = Field(default_factory=lambda: [
        "ACCEPTED", "REJECTED", "COMPLETED", "FAILED", "NO_VERIFICATION"])
    flag_rule: str = ""


class BreakdownSection(_Strict):
    type: Literal["breakdown"]
    id: str
    title: str = ""
    by: Literal["pass", "orbit", "eclipse_cycle", "hour"] = "orbit"
    metrics: list[str] = Field(default_factory=list)


class TableSection(_Strict):
    type: Literal["table"]
    id: str
    title: str = ""
    source: str = "s15_store_status"
    columns: list[str] = Field(default_factory=list)


class AppendixSection(_Strict):
    type: Literal["appendix"]
    id: str
    title: str = "Appendix"
    include: list[str] = Field(default_factory=lambda: [
        "packet_counts_by_service", "apids", "undecodable", "spec_yaml"])
    embed_dataset: bool = False


Section = Union[
    HeaderSection, NarrativeSection, CompletenessSection, PlotSection,
    StatsSection, EventsSection, AlarmsSection, CommandsSection,
    BreakdownSection, TableSection, AppendixSection,
]


class OutputSpec(_Strict):
    formats: list[str] = Field(default_factory=lambda: ["html"])
    self_contained: bool = True
    filename: Optional[str] = None
    save_to: str = "workspace/reports"


class ReportSpec(_Strict):
    schema_version: int = 1
    report: ReportIdent
    vars: dict[str, Any] = Field(default_factory=dict)
    source: SourceSpec = Field(default_factory=SourceSpec)
    period: PeriodSpec = Field(default_factory=PeriodSpec)
    parameter_sets: dict[str, list[str]] = Field(default_factory=dict)
    defaults: DefaultsSpec = Field(default_factory=DefaultsSpec)
    sections: list[Section] = Field(default_factory=list)
    output: OutputSpec = Field(default_factory=OutputSpec)

    # ── parameter reference resolution ──────────────────────────────
    def resolve_params(self, ref: ParamRef) -> list[str]:
        """Expand a ``parameters:`` value into a flat list of names/ids."""
        if isinstance(ref, str):
            if ref.startswith("parameter_sets."):
                key = ref.split(".", 1)[1]
                if key not in self.parameter_sets:
                    raise SpecError([{"path": f"parameter_sets.{key}",
                                      "msg": "referenced parameter set not defined"}])
                return list(self.parameter_sets[key])
            return [ref]
        out: list[str] = []
        for item in ref:
            out.extend(self.resolve_params(item))
        return out

    def all_parameter_refs(self) -> list[tuple[str, str]]:
        """Every parameter name referenced anywhere, with its spec path."""
        refs: list[tuple[str, str]] = []
        for set_name, names in self.parameter_sets.items():
            for n in names:
                refs.append((f"parameter_sets.{set_name}", n))
        for i, sec in enumerate(self.sections):
            base = f"sections[{i}]({getattr(sec, 'id', '?')})"
            if isinstance(sec, PlotSection):
                for j, ax in enumerate(sec.axes):
                    for n in self.resolve_params(ax.parameters):
                        refs.append((f"{base}.axes[{j}]", n))
                for n in self.resolve_params(sec.parameters):
                    refs.append((base, n))
            elif isinstance(sec, StatsSection):
                for g in sec.groups:
                    for n in self.resolve_params(g.parameters):
                        refs.append((f"{base}.{g.label}", n))
        return refs


def _pyd_errors(exc: ValidationError) -> list[dict]:
    out = []
    for e in exc.errors():
        path = ".".join(str(p) for p in e["loc"]) or "<root>"
        out.append({"path": path, "msg": e["msg"]})
    return out


def load_spec(source: Union[str, Path, dict]) -> ReportSpec:
    """Load and schema-validate a report definition.

    ``source`` may be a YAML file path, YAML text, or an already-parsed dict.
    Raises :class:`SpecError` with per-key paths on any violation, including
    unknown keys anywhere in the document.
    """
    if isinstance(source, dict):
        data = source
    else:
        p = Path(str(source))
        try:
            is_file = p.is_file()
        except OSError:
            is_file = False
        text = p.read_text() if is_file else str(source)
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise SpecError([{"path": "<yaml>", "msg": str(e)}]) from e
    if not isinstance(data, dict):
        raise SpecError([{"path": "<root>", "msg": "report spec must be a mapping"}])
    try:
        return ReportSpec.model_validate(data)
    except ValidationError as e:
        raise SpecError(_pyd_errors(e)) from e


def validate_spec(spec: ReportSpec, catalogs=None) -> list[dict]:
    """Semantic validation beyond the schema. Returns [{path, msg}, ...].

    With ``catalogs`` supplied, every referenced parameter must exist in
    parameters.yaml (by name, or as a 0x-prefixed / integer id) — a name not
    in the catalogue is an ERROR naming the parameter, never a silent drop.
    """
    errors: list[dict] = []
    seen_ids: set[str] = set()
    for sec in spec.sections:
        sid = getattr(sec, "id", None)
        if sid in seen_ids:
            errors.append({"path": f"sections.{sid}", "msg": "duplicate section id"})
        if sid:
            seen_ids.add(sid)
    try:
        refs = spec.all_parameter_refs()
    except SpecError as e:
        return errors + e.errors
    if catalogs is not None:
        for path, name in refs:
            if catalogs.param_id_for(name) is None:
                errors.append({
                    "path": path,
                    "msg": f"parameter {name!r} is not in parameters.yaml"})
    return errors
