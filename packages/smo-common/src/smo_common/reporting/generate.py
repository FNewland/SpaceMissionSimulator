"""Report generation orchestration.

``generate_report()`` is the one entry point shared by the MCS API, the
simulator instructor server, the standalone delayed-TM viewer's
``--report`` mode and any script: template → dumps → dataset → period →
sections → self-contained HTML on disk.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from ..dump_store import list_dumps, resolve_dump_file
from .catalogs import Catalogs
from .dataset import TelemetryDataset
from .periods import Period, cuc_to_utc_str, resolve_period
from .render_html import render_report_html
from .spec import PeriodSpec, ReportSpec, SpecError, load_spec, validate_spec

logger = logging.getLogger(__name__)

__all__ = ["generate_report", "list_templates", "templates_dir_for",
           "DEFAULT_REPORTS_DIR"]

DEFAULT_REPORTS_DIR = Path("workspace/reports")

_SAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_-]+")


def templates_dir_for(config_dir: str | Path) -> Path:
    """Shipped report definitions live with the mission config."""
    return Path(config_dir) / "reports"


def list_templates(config_dir: str | Path) -> list[dict[str, Any]]:
    """Enumerate report templates under ``configs/<mission>/reports/``."""
    out = []
    tdir = templates_dir_for(config_dir)
    if not tdir.is_dir():
        return out
    for path in sorted(tdir.glob("*.yaml")):
        try:
            spec = load_spec(path)
        except SpecError as e:
            out.append({"id": path.stem, "file": path.name,
                        "error": str(e)[:300]})
            continue
        out.append({
            "id": spec.report.id,
            "file": path.name,
            "title": spec.report.title,
            "audience": spec.report.audience,
            "period_kind": spec.period.kind,
            "vars": {k: v for k, v in spec.vars.items()},
            "sections": [getattr(s, "id", s.type) for s in spec.sections],
        })
    return out


def _find_template(config_dir: str | Path, template_id: str) -> Path:
    tdir = templates_dir_for(config_dir)
    cand = tdir / f"{template_id}.yaml"
    if cand.is_file():
        return cand
    for path in tdir.glob("*.yaml"):
        try:
            spec = load_spec(path)
        except SpecError:
            continue
        if spec.report.id == template_id:
            return path
    raise FileNotFoundError(f"no report template {template_id!r} in {tdir}")


def _select_dump_files(spec: ReportSpec, dump_dir: Optional[Path],
                       dump_files: Optional[list[str]]) -> list[Path]:
    """Choose dump files. Explicit file list wins; else the store filtered
    by the spec's ``store_ids``. The filename timestamp is used only as the
    listing order — period selection stays CUC-based on the decoded data.
    """
    if dump_dir is None:
        return []
    dump_dir = Path(dump_dir)
    if dump_files:
        return [resolve_dump_file(dump_dir, name) for name in dump_files]
    if spec.source.dumps.mode == "none":
        return []
    if spec.source.dumps.mode == "files" and spec.source.dumps.files:
        return [resolve_dump_file(dump_dir, name)
                for name in spec.source.dumps.files]
    wanted = set(spec.source.dumps.store_ids or [])
    out = []
    for meta in list_dumps(dump_dir):
        sid = meta.get("store_id")
        if wanted and sid is not None and sid not in wanted:
            continue
        out.append(Path(meta["path"]))
    return out


def _period_override_spec(override: dict[str, Any]) -> PeriodSpec:
    """Build a PeriodSpec from an API-style override dict."""
    kind = override.get("kind", "dump")
    data: dict[str, Any] = {"kind": kind}
    if kind == "absolute":
        data["absolute"] = {"start": override["start"], "end": override["end"]}
    elif kind == "relative":
        data["relative"] = {
            "anchor": override.get("anchor", "last_dump"),
            "before_s": float(override.get("before_s", 1800)),
            "after_s": float(override.get("after_s", 600))}
    elif kind == "pass":
        data["pass"] = {"select": override.get("select", "latest"),
                        "station": override.get("station", "any")}
    elif kind == "orbit":
        data["orbit"] = {"count": int(override.get("count", 1))}
    elif kind == "day":
        data["day"] = {"count": int(override.get("count", 1))}
    return PeriodSpec.model_validate(data)


def generate_report(
    template: Union[str, Path, dict, ReportSpec],
    *,
    config_dir: str | Path,
    dump_dir: str | Path | None = None,
    dump_files: Optional[list[str]] = None,
    vars: Optional[dict[str, Any]] = None,
    period_override: Optional[dict[str, Any]] = None,
    out_dir: str | Path | None = None,
    out_path: str | Path | None = None,
    command_log: Optional[dict[int, dict]] = None,
) -> dict[str, Any]:
    """Generate one report. Returns metadata including the written path.

    ``template`` — a template id (looked up under ``config_dir/reports``),
    a YAML path, YAML text, a parsed dict, or a ready ``ReportSpec``.
    ``period_override`` — API-style dict (kind + fields) that replaces the
    template's period block. Raises :class:`SpecError` on any spec problem.
    """
    spec_yaml = ""
    if isinstance(template, ReportSpec):
        spec = template
    else:
        if isinstance(template, str) and re.fullmatch(r"[A-Za-z0-9_\-]+", template):
            template = _find_template(config_dir, template)
        if isinstance(template, (str, Path)) and Path(str(template)).is_file():
            spec_yaml = Path(str(template)).read_text()
            spec = load_spec(spec_yaml)
        else:
            spec = load_spec(template)  # dict or YAML text
            if isinstance(template, str):
                spec_yaml = template
            else:
                spec_yaml = yaml.safe_dump(template) if isinstance(template, dict) else ""

    catalogs = Catalogs.load(config_dir)
    errors = validate_spec(spec, catalogs)
    if errors:
        raise SpecError(errors)

    files = _select_dump_files(spec, Path(dump_dir) if dump_dir else None,
                               dump_files)
    dataset = TelemetryDataset.from_dumps(
        files, catalogs)
    # Ground-side command identity, keyed by CCSDS sequence count. The dump
    # cannot supply it (S1 carries a request-id, never a name), so the caller
    # injects it; without it the report renders honestly with empty names.
    if command_log:
        dataset.command_log = dict(command_log)

    all_vars = {**spec.vars, **(vars or {})}
    period_spec = (_period_override_spec(period_override)
                   if period_override else spec.period)
    period = resolve_period(period_spec, dataset, catalogs, all_vars)

    html = render_report_html(spec, dataset, period, catalogs, all_vars,
                              spec_yaml=spec_yaml)

    if out_path is None:
        stamp = cuc_to_utc_str(period.start_cuc)
        stamp = _SAFE_STEM_RE.sub("", stamp.replace(":", "").replace("-", ""))[:15] \
            or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        base = _SAFE_STEM_RE.sub("_", spec.report.id) or "report"
        out_dir = Path(out_dir or DEFAULT_REPORTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{base}_{stamp}.html"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    meta = {
        "report_id": spec.report.id,
        "title": spec.report.title,
        "path": str(out_path),
        "filename": out_path.name,
        "size_bytes": out_path.stat().st_size,
        "period": {"kind": period.kind, "label": period.label,
                   "start_utc": cuc_to_utc_str(period.start_cuc),
                   "end_utc": cuc_to_utc_str(period.end_cuc)},
        "dumps_used": [f["filename"] for f in dataset.files],
        "packets": dataset.total_packets,
        "crc_errors": dataset.crc_errors,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # Sidecar metadata so listings can show titles/periods without parsing HTML.
    try:
        out_path.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    except OSError:  # noqa: PERF203 — metadata is best-effort
        pass
    return meta
