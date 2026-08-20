"""Self-contained HTML renderer for report specs.

One HTML file, zero network references: CSS inline, plots rendered with
matplotlib (Agg backend) and embedded as base64 PNG ``data:`` URLs. Every
plot carries an accessible caption and a ``<details>`` table with the
underlying numbers; every section with no data in the period states so
explicitly rather than rendering empty (brief 06 §A acceptance).
"""
from __future__ import annotations

import base64
import html as _html
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .analysis import (
    alarm_pairs,
    breakdown_bins,
    commands_view,
    completeness,
    events_view,
    param_stats,
)
from .catalogs import Catalogs
from .dataset import TelemetryDataset
from .periods import Period, cuc_to_utc_str, passes_in
from .plotting import (
    PlottingUnavailable,
    plotting_status,
    require_plotting,
    unavailable_html,
)
from .spec import (
    AlarmsSection,
    AppendixSection,
    BreakdownSection,
    CommandsSection,
    CompletenessSection,
    ConsoleLogSection,
    EventsSection,
    HeaderSection,
    NarrativeSection,
    PassRollupSection,
    PlotSection,
    ReportSpec,
    StatsSection,
    TableSection,
)

__all__ = ["render_report_html"]

_log = logging.getLogger(__name__)

_SEV_NAMES = {1: "INFO", 2: "LOW", 3: "MEDIUM", 4: "HIGH"}

_CSS = """
body { font-family: system-ui, -apple-system, sans-serif; max-width: 1100px;
       margin: 1.5em auto; padding: 0 1em; color: #1a2433; background: #fff; }
h1 { border-bottom: 3px solid #16437e; padding-bottom: 6px; margin-bottom: 2px; }
h2 { margin-top: 1.6em; border-bottom: 1px solid #b9c6d8; padding-bottom: 3px;
     color: #16437e; }
.sub { color: #5a6c82; margin-bottom: 1em; }
.classif { display:inline-block; background:#a33; color:#fff; padding:2px 10px;
           border-radius:3px; font-size:12px; letter-spacing:1px; }
table { border-collapse: collapse; width: 100%; margin: .6em 0; font-size: 12.5px; }
th, td { border: 1px solid #c9d4e0; padding: 3px 7px; text-align: left;
         vertical-align: top; }
th { background: #eef3f8; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
caption { text-align: left; color: #5a6c82; font-size: 11.5px; padding: 2px 0; }
.meta { background: #f4f7fa; border-left: 4px solid #16437e; padding: 8px 14px; }
.warnband { background: #fff7e0; border-left: 4px solid #d99a06;
            padding: 6px 12px; margin: .6em 0; font-size: 12.5px; }
.nodata { color: #8a6d1a; background: #fdf6e3; padding: 6px 12px;
          border-left: 4px solid #d0a020; }
.flag { color: #a11; font-weight: 600; }
.sev1 { color: #667; } .sev2 { color: #1b6e1b; }
.sev3 { color: #b46e00; font-weight: 600; } .sev4 { color: #b00020; font-weight: 700; }
.est { color:#889; } .est::after { content: "~"; }
img.plot { max-width: 100%; border: 1px solid #d5dde6; border-radius: 3px; }
details { margin: 4px 0 12px; } summary { cursor: pointer; color: #46648c;
          font-size: 12px; }
.narr { background:#fbfcfe; border:1px solid #e2e8f0; border-radius:4px;
        padding: 8px 14px; white-space: pre-wrap; }
footer { margin-top: 2.5em; color: #7a8aa0; font-size: 11px;
         border-top: 1px solid #ccd; padding-top: 8px; }
"""


def _esc(v: Any) -> str:
    return _html.escape(str(v))


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "--"
    if isinstance(v, float):
        return f"{v:.{nd}f}".rstrip("0").rstrip(".") if v == v else "nan"
    return str(v)


# ── substitution ─────────────────────────────────────────────────────────

_SUBST_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.|-]+)\s*\}\}")


def _lookup(ctx: dict, dotted: str) -> Any:
    dotted = dotted.split("|")[0].strip()
    node: Any = ctx
    # Greedy segment match so "stats.eps.bat_soc.min" resolves with the
    # parameter's dotted name as one key.
    parts = dotted.split(".")
    i = 0
    while i < len(parts):
        if not isinstance(node, dict):
            return None
        # longest-prefix key match
        for j in range(len(parts), i, -1):
            key = ".".join(parts[i:j])
            if key in node:
                node = node[key]
                i = j
                break
        else:
            return None
    return node


def _substitute(text: str, ctx: dict) -> str:
    def repl(m):
        v = _lookup(ctx, m.group(1))
        return _fmt(v) if v is not None else "—"
    return _SUBST_RE.sub(repl, text)


# ── plotting ─────────────────────────────────────────────────────────────

def _plot_failure_html(sec, exc: BaseException) -> str:
    """A plot that blew up says so, with the interpreter it blew up in."""
    st = plotting_status()
    return (
        f"<h2 id='{_esc(getattr(sec, 'id', 'plot'))}'>"
        f"{_esc(getattr(sec, 'title', None) or getattr(sec, 'id', 'Plot'))}</h2>"
        f"<div class='nodata'>Plot not rendered — plotting failed in this "
        f"server. <code>{_esc(type(exc).__name__)}: {_esc(str(exc))}</code>"
        f"<br>interpreter: <code>{_esc(st.executable)}</code> · "
        f"matplotlib: <code>{_esc(st.version or 'not importable')}</code> · "
        f"backend: <code>{_esc(st.backend or 'unknown')}</code></div>"
    )


def _plot_png(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _cuc_to_dt(t: float) -> datetime:
    from ..protocol.ecss_packet import cuc_seconds_to_datetime
    return cuc_seconds_to_datetime(float(t))


def _shade_eclipse(ax, dataset: TelemetryDataset, catalogs: Catalogs,
                   period: Period) -> None:
    pid = catalogs.param_id_for("eps.eclipse_flag")
    if pid is None:
        return
    pts = dataset.series_for(pid, period.start_cuc, period.end_cuc)
    start = None
    for t, v in pts:
        if v >= 0.5 and start is None:
            start = t
        elif v < 0.5 and start is not None:
            ax.axvspan(_cuc_to_dt(start), _cuc_to_dt(t), alpha=0.12,
                       color="#334", lw=0)
            start = None
    if start is not None and pts:
        ax.axvspan(_cuc_to_dt(start), _cuc_to_dt(pts[-1][0]), alpha=0.12,
                   color="#334", lw=0)


def _shade_contacts(ax, dataset: TelemetryDataset, catalogs: Catalogs,
                    period: Period) -> None:
    for p in passes_in(dataset, catalogs):
        if p["los"] < period.start_cuc or p["aos"] > period.end_cuc:
            continue
        ax.axvspan(_cuc_to_dt(max(p["aos"], period.start_cuc)),
                   _cuc_to_dt(min(p["los"], period.end_cuc)),
                   alpha=0.10, color="#2a7", lw=0)


def _limit_bands(ax, catalogs: Catalogs, pid: int) -> None:
    lim = catalogs.limits_by_id.get(pid)
    if not lim:
        return
    for key, color in (("yellow_low", "#d0a020"), ("yellow_high", "#d0a020"),
                       ("red_low", "#c03030"), ("red_high", "#c03030")):
        v = lim.get(key)
        if v is not None:
            ax.axhline(v, color=color, lw=0.8, ls="--", alpha=0.7)


def _render_plot_section(sec: PlotSection, spec: ReportSpec,
                         dataset: TelemetryDataset, period: Period,
                         catalogs: Catalogs) -> str:
    # Optional dependency (smo-common[reports]). Degrade to a stated notice
    # rather than failing the whole report — the statistics, event, alarm and
    # command sections do not need it.
    #
    # The notice must be ACTIONABLE. The old one said "matplotlib is not
    # installed, install it with pip install smo-common[reports]" for every
    # failure, including the two that dominate in the field: the server running
    # under a different interpreter than the one the operator installed into,
    # and a matplotlib that imports but whose backend cannot draw on a headless
    # machine. `plotting` names the interpreter and quotes the real error; see
    # its module docstring.
    try:
        plt, mdates = require_plotting()
    except PlottingUnavailable as e:
        return (
            '<section class="plot missing-dep"><h3>%s</h3>%s</section>'
            % (_esc(getattr(sec, "title", "Plot")), unavailable_html(e.status))
        )

    # Collect axis definitions
    axes_defs = list(sec.axes)
    if not axes_defs and sec.parameters:
        from .spec import PlotAxis
        axes_defs = [PlotAxis(parameters=sec.parameters)]

    series_by_axis: list[list[tuple[int, list[tuple[float, float]]]]] = []
    any_data = False
    for ax_def in axes_defs:
        rows = []
        for name in spec.resolve_params(ax_def.parameters):
            pid = catalogs.param_id_for(name)
            if pid is None:
                continue
            pts = dataset.series_for(pid, period.start_cuc, period.end_cuc)
            if pts:
                any_data = True
            rows.append((pid, pts))
        series_by_axis.append(rows)

    title = sec.title or sec.id
    if not any_data:
        return (f"<h2 id='{_esc(sec.id)}'>{_esc(title)}</h2>"
                f"<div class='nodata'>No data for this plot in the "
                f"reporting period.</div>")

    h_px = spec.defaults.plot.height_px
    fig_h = max(h_px / 100.0, 2.2)

    if sec.style == "histogram":
        fig, ax = plt.subplots(figsize=(10.0, fig_h))
        for pid, pts in series_by_axis[0]:
            vals = [v for _, v in pts]
            if vals:
                ax.hist(vals, bins=sec.bins, alpha=0.6,
                        label=catalogs.param_name(pid))
        if any(o.startswith("p") for o in sec.overlay):
            vals_all = sorted(v for rows in series_by_axis
                              for _, pts in rows for _, v in pts)
            for o in sec.overlay:
                if o.startswith("p") and o[1:].isdigit() and vals_all:
                    q = int(o[1:]) / 100.0
                    idx = min(int(q * len(vals_all)), len(vals_all) - 1)
                    ax.axvline(vals_all[idx], ls=":", color="#555", lw=1)
                    ax.annotate(o, (vals_all[idx], ax.get_ylim()[1] * 0.95),
                                fontsize=8, color="#555")
        ax.legend(fontsize=8)
        ax.grid(spec.defaults.plot.grid, alpha=0.3)
    else:
        fig, ax_l = plt.subplots(figsize=(10.0, fig_h))
        axes_mpl = [ax_l]
        drawstyle = "steps-post" if sec.style == "step" else "default"
        colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
        ci = 0
        for ax_def, rows in zip(axes_defs, series_by_axis):
            mpl_ax = ax_l
            if ax_def.side == "right":
                mpl_ax = ax_l.twinx()
                axes_mpl.append(mpl_ax)
            for pid, pts in rows:
                if not pts:
                    continue
                xs = [_cuc_to_dt(t) for t, _ in pts]
                ys = [v for _, v in pts]
                mpl_ax.plot(xs, ys, lw=1.1, drawstyle=drawstyle,
                            label=catalogs.param_name(pid),
                            color=colors[ci % len(colors)])
                ci += 1
                if (spec.defaults.plot.show_limits and len(axes_defs) == 1
                        and len(rows) <= 2):
                    _limit_bands(mpl_ax, catalogs, pid)
            if ax_def.label:
                mpl_ax.set_ylabel(ax_def.label, fontsize=9)
            if ax_def.range and len(ax_def.range) == 2:
                mpl_ax.set_ylim(*ax_def.range)
            if ax_def.log:
                mpl_ax.set_yscale("log")
        ann = sec.annotations
        if ann.eclipse or spec.defaults.plot.show_eclipse:
            _shade_eclipse(ax_l, dataset, catalogs, period)
        if ann.contacts or spec.defaults.plot.show_contacts:
            _shade_contacts(ax_l, dataset, catalogs, period)
        if ann.t0_marker:
            try:
                from .periods import _iso_to_cuc
                ax_l.axvline(_cuc_to_dt(_iso_to_cuc(ann.t0_marker)),
                             color="#b00", lw=1.4, ls="-")
            except (ValueError, TypeError):
                pass
        for e in dataset.events_in(period.start_cuc, period.end_cuc, 1):
            if e["event_id"] in (ann.events or []):
                ax_l.axvline(_cuc_to_dt(e["t"]), color="#888", lw=0.7, ls=":")
        ax_l.grid(spec.defaults.plot.grid, alpha=0.3)
        ax_l.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        handles, labels = [], []
        for a in axes_mpl:
            h, l = a.get_legend_handles_labels()
            handles += h
            labels += l
        if handles:
            ax_l.legend(handles, labels, fontsize=8, loc="best")
        fig.autofmt_xdate()

    png = _plot_png(fig)
    # Data table (capped) for accessibility / post-processing.
    rows_html = []
    for rows in series_by_axis:
        for pid, pts in rows:
            name = catalogs.param_name(pid)
            for t, v in pts[:400]:
                rows_html.append(f"<tr><td>{_esc(name)}</td>"
                                 f"<td>{_esc(cuc_to_utc_str(t))}</td>"
                                 f"<td class='num'>{_fmt(v)}</td></tr>")
    table = ("<details><summary>show data "
             f"({sum(len(p) for r in series_by_axis for _, p in r)} samples, "
             "first 400/parameter shown)</summary>"
             "<table><tr><th>Parameter</th><th>UTC</th><th>Value</th></tr>"
             + "".join(rows_html) + "</table></details>")

    out = [f"<h2 id='{_esc(sec.id)}'>{_esc(title)}</h2>",
           f"<img class='plot' alt='{_esc(title)} plot' "
           f"src='data:image/png;base64,{png}'>", table]

    if sec.show_transitions_table:
        trs = []
        for rows in series_by_axis:
            for pid, pts in rows:
                prev = None
                for t, v in pts:
                    if prev is not None and v != prev:
                        trs.append((t, catalogs.param_name(pid), prev, v))
                    prev = v
        trs.sort()
        if trs:
            out.append("<table><caption>State transitions</caption>"
                       "<tr><th>UTC</th><th>Parameter</th><th>From</th>"
                       "<th>To</th></tr>" +
                       "".join(f"<tr><td>{_esc(cuc_to_utc_str(t))}</td>"
                               f"<td>{_esc(n)}</td><td class='num'>{_fmt(a)}"
                               f"</td><td class='num'>{_fmt(b)}</td></tr>"
                               for t, n, a, b in trs[:200]) + "</table>")
        else:
            out.append("<div class='nodata'>No state transitions in the "
                       "period.</div>")
    return "".join(out)


# ── section renderers ────────────────────────────────────────────────────

def _render_header(sec: HeaderSection, spec: ReportSpec,
                   dataset: TelemetryDataset, period: Period) -> str:
    rows = []
    if "period" in sec.include:
        rows.append(("Period", f"{period.label} — "
                     f"{cuc_to_utc_str(period.start_cuc)} → "
                     f"{cuc_to_utc_str(period.end_cuc)} "
                     f"({period.duration_s / 60.0:.1f} min, CUC timebase)"))
    if "dumps_used" in sec.include:
        rows.append(("Dumps used", ", ".join(
            f"{f['filename']} ({f['packets']} pkts)"
            for f in dataset.files) or "none"))
    if "packet_totals" in sec.include:
        rows.append(("Packets", f"{dataset.total_packets} total, "
                     f"{dataset.undecodable} undecodable"))
    if "crc_errors" in sec.include:
        rows.append(("CRC errors", str(dataset.crc_errors)))
    if "generated_at" in sec.include:
        rows.append(("Generated",
                     datetime.now(timezone.utc).isoformat(timespec="seconds")))
    body = "".join(f"<tr><th style='width:160px'>{_esc(k)}</th>"
                   f"<td>{_esc(v)}</td></tr>" for k, v in rows)
    return f"<div class='meta'><table>{body}</table></div>"


def _render_narrative(sec: NarrativeSection, ctx: dict) -> str:
    text = _substitute(sec.text, ctx)
    body = _esc(text)
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    body = re.sub(r"\*(.+?)\*", r"<i>\1</i>", body)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>" if sec.title else ""
    return f"{title}<div class='narr'>{body}</div>"


def _render_completeness(sec: CompletenessSection, dataset, period,
                         catalogs) -> str:
    rows = completeness(dataset, period, catalogs, sec.sids,
                        sec.gap_threshold_multiple)
    have_data = any(r["received"] for r in rows)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    if not have_data:
        return title + ("<div class='nodata'>No housekeeping packets in the "
                        "reporting period for the selected SIDs.</div>")
    body = "".join(
        f"<tr><td>SID {r['sid']} — {_esc(r['name'])}</td>"
        f"<td class='num'>{r['cadence_s']:g} s</td>"
        f"<td class='num'>{r['expected']}</td>"
        f"<td class='num'>{r['received']}</td>"
        f"<td class='num'>{'<span class=flag>' if r['missing_pct'] > 5 else ''}"
        f"{r['missing_pct']:.2f} %{'</span>' if r['missing_pct'] > 5 else ''}</td>"
        f"<td class='num'>{r['longest_gap_s']:g}</td>"
        f"<td class='num'>{len(r['gaps'])}</td></tr>" for r in rows)
    gap_rows = "".join(
        f"<tr><td>SID {r['sid']}</td><td>{_esc(g['start_utc'])}</td>"
        f"<td>{_esc(g['end_utc'])}</td><td class='num'>{g['duration_s']:g}</td></tr>"
        for r in rows for g in r["gaps"])
    gaps_tbl = ""
    if gap_rows:
        gaps_tbl = ("<details open><summary>gap list</summary><table>"
                    "<tr><th>SID</th><th>Gap start (UTC)</th>"
                    "<th>Gap end (UTC)</th><th>Duration s</th></tr>"
                    + gap_rows + "</table></details>")
    return (title +
            "<table><caption>Received vs expected per SID (expected from "
            "hk_structures.yaml cadence; gap = Δt &gt; "
            f"{sec.gap_threshold_multiple:g} × cadence)</caption>"
            "<tr><th>Structure</th><th>Cadence</th><th>Expected</th>"
            "<th>Received</th><th>Missing</th><th>Longest gap s</th>"
            "<th>Gaps</th></tr>" + body + "</table>" + gaps_tbl)


def _render_stats(sec: StatsSection, spec, dataset, period, catalogs) -> str:
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    nd = spec.defaults.stats.round
    blocks = []
    for grp in sec.groups:
        rows = []
        for name in spec.resolve_params(grp.parameters):
            pid = catalogs.param_id_for(name)
            st = param_stats(dataset, pid, period, catalogs) if pid is not None else None
            if st is None:
                rows.append(f"<tr><td>{_esc(name)}</td>"
                            f"<td colspan='{len(sec.columns)}'>"
                            "no data in period</td></tr>")
                continue
            flagged = st["ool_red_s"] > 0 or st["ool_pct"] > 5
            cells = "".join(
                f"<td class='num'>{_fmt(st.get(c), nd)}</td>"
                for c in sec.columns)
            nm = _esc(st["name"]) + (f" [{_esc(st['units'])}]" if st["units"] else "")
            if flagged:
                nm = f"<span class='flag'>{nm}</span>"
            rows.append(f"<tr><td>{nm}</td>{cells}</tr>")
        head = "".join(f"<th>{_esc(c)}</th>" for c in sec.columns)
        blocks.append(f"<table><caption>{_esc(grp.label)}</caption>"
                      f"<tr><th>Parameter</th>{head}</tr>"
                      + "".join(rows) + "</table>")
    if not blocks:
        blocks = ["<div class='nodata'>No parameter groups defined.</div>"]
    return title + "".join(blocks)


def _render_events(sec: EventsSection, dataset, period, catalogs) -> str:
    rows = events_view(dataset, period, catalogs, sec.severity_min,
                       sec.include_synthetic_s12)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    if not rows:
        return title + (f"<div class='nodata'>No events of severity ≥ "
                        f"{sec.severity_min} in the reporting period.</div>")
    body = "".join(
        f"<tr class='sev{r['severity']}'>"
        f"<td>{_esc(r['utc'])}</td>"
        f"<td>{_esc(r['id_hex'])}</td><td>{_esc(r['name'])}</td>"
        f"<td>{_SEV_NAMES.get(r['severity'], r['severity'])}</td>"
        f"<td>{_esc(r['subsystem'])}</td><td>{_esc(r['aux_text'])}</td></tr>"
        for r in rows)
    return (title + f"<table><caption>{len(rows)} events "
            f"(severity ≥ {sec.severity_min})</caption>"
            "<tr><th>UTC</th><th>ID</th><th>Name</th><th>Sev</th>"
            "<th>Subsystem</th><th>Text</th></tr>" + body + "</table>")


def _render_alarms(sec: AlarmsSection, dataset, period, catalogs) -> str:
    res = alarm_pairs(dataset, period, catalogs, sec.severity_min)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    if not res["total"]:
        return title + ("<div class='nodata'>No S12 limit transitions in "
                        "the reporting period.</div>")
    parts = [title]
    if res["paired"]:
        body = "".join(
            f"<tr><td>{_esc(p['param_name'])}</td>"
            f"<td class='num'>{p.get('mon_id', '--')}</td>"
            f"<td class='num'>{p.get('severity', '--')}</td>"
            f"<td class='num'>{_fmt(p.get('value'))}</td>"
            f"<td class='num'>{_fmt(p.get('low_limit'))} / "
            f"{_fmt(p.get('high_limit'))}</td>"
            f"<td>{_esc(p['entered_utc'])}</td>"
            f"<td>{_esc(p['cleared_utc'])}</td>"
            f"<td class='num'>{p['duration_s']:g}</td></tr>"
            for p in res["paired"])
        parts.append(
            "<table><caption>Paired limit entries and clears</caption>"
            "<tr><th>Parameter</th><th>Mon</th><th>Sev</th><th>Value</th>"
            "<th>Limits</th><th>Entered</th><th>Cleared</th>"
            "<th>In limit s</th></tr>" + body + "</table>")
    if res["unpaired"] and sec.unpaired_policy == "report":
        body = "".join(
            f"<tr><td class='flag'>{_esc(p['param_name'])}</td>"
            f"<td class='num'>{_fmt(p.get('value'))}</td>"
            f"<td>{_esc(p['entered_utc'])}</td></tr>"
            for p in res["unpaired"])
        parts.append(
            "<table><caption class='flag'>STILL OUT OF LIMITS at end of "
            "period (no clear seen)</caption>"
            "<tr><th>Parameter</th><th>Value</th><th>Entered</th></tr>"
            + body + "</table>")
    return "".join(parts)


def _render_commands(sec: CommandsSection, dataset, period) -> str:
    rows = commands_view(dataset, period)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    if not rows:
        return title + ("<div class='nodata'>No command verification "
                        "reports (S1) in the reporting period.</div>")
    def _row(r: dict) -> str:
        bad = r["outcome"] in ("REJECTED", "FAILED")
        err = "0x%04X" % r["error_code"] if r["error_code"] else ""
        outcome = (f"<span class='flag'>{r['outcome']}</span>" if bad
                   else r["outcome"])
        # Name/args come from the ground command log joined on `seq`; an
        # em-dash means "no log for this period", never "no arguments".
        svc = ("" if r.get("service") is None
               else f"S{r['service']},{r['subtype']}")
        return (f"<tr><td>{_esc(r['utc'])}</td>"
                f"<td class='num'>{r['apid']}</td>"
                f"<td class='num'>{r['seq']}</td>"
                f"<td>{_esc(r.get('name') or '—')}</td>"
                f"<td>{_esc(svc)}</td>"
                f"<td>{_esc(r.get('args') or '')}</td>"
                f"<td>{r['accept']}</td><td>{r['execute']}</td>"
                f"<td>{outcome}</td><td>{err}</td>"
                f"<td>{_esc(r['error_text'] or r['reason'])}</td></tr>")

    body = "".join(_row(r) for r in rows)
    n_bad = sum(1 for r in rows if r["outcome"] in ("REJECTED", "FAILED"))
    n_named = sum(1 for r in rows if r.get("name"))
    # Say plainly where the identity came from, and how much of it we have.
    # An S1 report cannot carry a command name (the protocol identifies a TC
    # by request-id alone), so the name is a ground-side join on `seq`.
    if n_named == len(rows):
        prov = ("names and arguments joined from the ground command log "
                "on sequence count")
    elif n_named:
        prov = (f"{n_named} of {len(rows)} named from the ground command log; "
                "the rest were sent outside it")
    else:
        prov = ("no ground command log for this period — S1 verification "
                "reports carry a request-id, never a command name")
    return (title + f"<table><caption>{len(rows)} commands from S1 "
            f"verification reports, {n_bad} rejected/failed; {prov}</caption>"
            "<tr><th>UTC</th><th>APID</th><th>Seq</th><th>Command</th>"
            "<th>Svc</th><th>Arguments</th><th>Accept</th>"
            "<th>Execute</th><th>Outcome</th><th>Err</th><th>Reason</th></tr>"
            + body + "</table>")


def _render_breakdown(sec: BreakdownSection, dataset, period, catalogs) -> str:
    rows = breakdown_bins(dataset, period, catalogs, sec.by)
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title or f'Per-{sec.by}')}</h2>"
    if not rows:
        return title + (f"<div class='nodata'>No {sec.by} bins resolve "
                        "within the reporting period.</div>")
    cols = ["label", "start_utc", "duration_s", "packets", "min_soc",
            "max_bat_temp", "max_att_error", "events_sev3plus",
            "commands_sent", "commands_rejected"]
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = "".join("<tr>" + "".join(
        f"<td class='num'>{_fmt(r.get(c))}</td>" if c != "label"
        else f"<td>{_esc(r.get(c))}</td>" for c in cols) + "</tr>"
        for r in rows)
    return title + f"<table><tr>{head}</tr>{body}</table>"


def _render_table(sec: TableSection, dataset, period) -> str:
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title or sec.source)}</h2>"
    if sec.source != "s15_store_status":
        return title + (f"<div class='nodata'>Unknown table source "
                        f"{_esc(sec.source)}.</div>")
    snaps = [s for s in dataset.store_status
             if period.start_cuc <= s["t"] <= period.end_cuc] or dataset.store_status
    if not snaps:
        return title + ("<div class='nodata'>No TM(15,14) store-status "
                        "report in the dumps.</div>")
    last = snaps[-1]
    body = "".join(
        f"<tr><td class='num'>{s['id']}</td><td>{_esc(s['name'])}</td>"
        f"<td class='num'>{s['count']}</td><td class='num'>{s['capacity']}</td>"
        f"<td class='num'>{s['fill_pct']}</td>"
        f"<td>{'YES' if s['overflow'] else ''}</td>"
        f"<td>{'circ' if s['circular'] else 'lin'}</td>"
        f"<td class='num'>{s['wrap_count']}</td></tr>"
        for s in last["stores"])
    return (title + "<table><caption>On-board store status at "
            f"{_esc(cuc_to_utc_str(last['t']))}</caption>"
            "<tr><th>ID</th><th>Name</th><th>Count</th><th>Capacity</th>"
            "<th>Fill %</th><th>Overflow</th><th>Type</th><th>Wraps</th></tr>"
            + body + "</table>")


# ── live-store sections (G8 console log, G14 pass rollups) ──────────────
#
# These two sections consume ANOTHER SERVER's store over HTTP, so unlike
# every other section they can be unreachable. The contract is honest
# degradation: unreachable ⇒ a stated notice, never fabricated rows.

def _fetch_json(url: str, timeout_s: float = 3.0) -> Optional[dict]:
    """GET a JSON document; None on any failure (module-level so tests can
    monkeypatch it)."""
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — unreachable is a normal state
        _log.debug("report fetch %s failed: %s", url, e)
        return None


def _cuc_to_epoch(cuc: float) -> Optional[float]:
    """On-board CUC seconds -> sim-time Unix epoch (the console-log clock)."""
    try:
        from ..protocol.ecss_packet import cuc_seconds_to_datetime
        return (cuc_seconds_to_datetime(float(cuc))
                .replace(tzinfo=timezone.utc).timestamp())
    except (ValueError, OverflowError):
        return None


def _render_console_log(sec: ConsoleLogSection, period: Period) -> str:
    import os
    base = sec.source_url or (os.environ.get("SMO_MCS_URL",
                                             "http://localhost:9090")
                              + "/api/console-log")
    start = _cuc_to_epoch(period.start_cuc)
    end = _cuc_to_epoch(period.end_cuc)
    url = base
    sep = "&" if "?" in base else "?"
    if start is not None:
        url += f"{sep}since={start:.0f}"
        sep = "&"
    if end is not None:
        url += f"{sep}until={end:.0f}"
        sep = "&"
    url += f"{sep}limit={int(sec.limit)}"
    doc = _fetch_json(url)
    head = f"<h2>{_esc(sec.title)}</h2>"
    # The self-contained-output contract bans http(s):// strings in the
    # HTML, so sources are named scheme-less.
    src_label = base.split("://", 1)[-1]
    if doc is None:
        return (head + "<div class='nodata'>Console-log store unreachable "
                f"({_esc(src_label)}) — no decision record available for "
                "this report. Entries are NOT reconstructed.</div>")
    entries = doc.get("entries") or []
    if sec.kinds:
        want = {k.lower() for k in sec.kinds}
        entries = [e for e in entries
                   if str(e.get("kind", "")).lower() in want]
    if doc.get("archive_unavailable"):
        return (head + "<div class='nodata'>Console-log store reports its "
                "archive UNAVAILABLE — entries for this period cannot be "
                "read.</div>")
    if not entries:
        return (head + "<div class='nodata'>No console-log entries in this "
                "period.</div>")
    rows = []
    for e in entries:
        try:
            when = datetime.fromtimestamp(
                float(e.get("utc_epoch", 0)),
                tz=timezone.utc).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError):
            when = "--"
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td></tr>" % (
                _esc(when), _esc(e.get("position", "")),
                _esc(e.get("kind", "")), _esc(e.get("text", "")),
                _esc(e.get("ref", "")), _esc(e.get("source", ""))))
    return (head
            + "<table><caption>Append-only console/mission log "
              "(sim-time stamps rendered as UTC); source: "
            + _esc(src_label) + "</caption>"
              "<tr><th>UTC (sim)</th><th>Position</th><th>Kind</th>"
              "<th>Entry</th><th>Ref</th><th>Source</th></tr>"
            + "".join(rows) + "</table>")


def _fmt_or_dash(v: Any, suffix: str = "", nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return _esc(v)


def _render_pass_rollups(sec: PassRollupSection, period: Period) -> str:
    import os
    base = sec.source_url or (os.environ.get("SMO_RFSIM_URL",
                                             "http://localhost:8094")
                              + "/api/pass-rollups")
    sep = "&" if "?" in base else "?"
    doc = _fetch_json(f"{base}{sep}limit={int(sec.limit)}")
    head = f"<h2>{_esc(sec.title)}</h2>"
    src_label = base.split("://", 1)[-1]
    if doc is None:
        return (head + "<div class='nodata'>rfsim pass-rollup store "
                f"unreachable ({_esc(src_label)}) — plan-vs-achieved RF "
                "line items unavailable for this report.</div>")
    rollups = doc.get("rollups") or []
    # Keep the report period honest: only passes whose LOS falls inside it
    # (when both clocks are sim time; wall-clock records are shown with a
    # flag rather than silently filtered against a different clock).
    start = _cuc_to_epoch(period.start_cuc)
    end = _cuc_to_epoch(period.end_cuc)
    in_period = []
    for r in rollups:
        los = r.get("los_ts")
        if (r.get("time_base") == "sim" and los is not None
                and start is not None and end is not None):
            if start <= float(los) <= end:
                in_period.append(r)
        else:
            in_period.append({**r, "_clock_flag": True})
    if not in_period:
        return (head + "<div class='nodata'>No completed passes recorded in "
                "this period.</div>")
    rows = []
    for r in in_period:
        try:
            aos = datetime.fromtimestamp(
                float(r.get("aos_pred_ts", 0)),
                tz=timezone.utc).isoformat(timespec="seconds")
        except (ValueError, OverflowError, OSError):
            aos = "--"
        lock = ("no lock" if not r.get("locked")
                else _fmt_or_dash(r.get("lock_delay_s"), " s"))
        vol = r.get("data_volume_bytes")
        vol_s = "—" if vol is None else f"{float(vol)/1024.0:.1f} kB"
        flag = " <span class='flag'>(wall clock)</span>" \
            if r.get("_clock_flag") else ""
        rows.append(
            "<tr><td>%s</td><td>%s%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num'>%s</td></tr>" % (
                _esc(r.get("gs_name", "")), _esc(aos), flag,
                _fmt_or_dash(r.get("duration_s"), " s", 0), _esc(lock),
                _fmt_or_dash(r.get("mean_eb_n0_db"), " dB"),
                _fmt_or_dash(r.get("fer"), "", 4), vol_s))
    return (head
            + "<table><caption>Per-pass RF rollups (predicted AOS vs "
              "achieved lock, mean Eb/N0, frame-error ratio, data volume); "
              "source: " + _esc(src_label) + "</caption>"
              "<tr><th>Station</th><th>AOS (pred)</th>"
              "<th>Duration</th><th>Lock delay</th><th>Mean Eb/N0</th>"
              "<th>FER</th><th>Volume</th></tr>"
            + "".join(rows) + "</table>")


def _render_appendix(sec: AppendixSection, spec, dataset,
                     spec_yaml: str) -> str:
    title = f"<h2 id='{_esc(sec.id)}'>{_esc(sec.title)}</h2>"
    parts = [title]
    if "packet_counts_by_service" in sec.include:
        body = "".join(
            f"<tr><td>S{s}.{st}</td><td class='num'>{n}</td></tr>"
            for (s, st), n in sorted(dataset.census.items()))
        parts.append("<table><caption>Packet census</caption>"
                     "<tr><th>Service.Subtype</th><th>Count</th></tr>"
                     + body + "</table>")
    if "apids" in sec.include:
        parts.append("<p>APIDs seen: "
                     + ", ".join(str(a) for a in sorted(dataset.apids))
                     + "</p>")
    if "undecodable" in sec.include:
        parts.append(f"<p>Undecodable packets: {dataset.undecodable}; "
                     f"CRC errors: {dataset.crc_errors}.</p>")
    if "spec_yaml" in sec.include and spec_yaml:
        parts.append("<details><summary>report definition (YAML)</summary>"
                     f"<pre>{_esc(spec_yaml)}</pre></details>")
    if sec.embed_dataset or "dataset_json" in sec.include:
        compact = {
            "series": {f"0x{pid:04X}": pts[:2000]
                       for pid, pts in dataset.series.items()},
            "events": [{k: v for k, v in e.items() if k != "pkt"}
                       for e in dataset.events[:2000]],
        }
        parts.append("<details><summary>embedded dataset (JSON)</summary>"
                     "<pre id='dataset-json'>"
                     + _esc(json.dumps(compact)) + "</pre></details>")
    return "".join(parts)


# ── context assembly ─────────────────────────────────────────────────────

def _build_context(spec: ReportSpec, dataset: TelemetryDataset,
                   period: Period, catalogs: Catalogs,
                   vars: dict[str, Any]) -> dict:
    ctx: dict[str, Any] = {
        "period": period.context(),
        "vars": {**spec.vars, **vars},
        "report": {"id": spec.report.id, "title": spec.report.title},
    }
    # stats.<param dotted name>.<col>
    stats_ctx: dict[str, Any] = {}
    for _path, name in spec.all_parameter_refs():
        pid = catalogs.param_id_for(name)
        if pid is None or name in stats_ctx:
            continue
        st = param_stats(dataset, pid, period, catalogs)
        if st:
            stats_ctx[name] = st
    ctx["stats"] = stats_ctx
    evs = events_view(dataset, period, catalogs, 1, True)
    cmds = commands_view(dataset, period)
    comp = completeness(dataset, period, catalogs,
                        list(catalogs.hk_structs.keys()) or [1])
    exp = sum(r["expected"] for r in comp)
    rec = sum(r["received"] for r in comp)
    pss = [p for p in passes_in(dataset, catalogs)
           if p["los"] >= period.start_cuc and p["aos"] <= period.end_cuc]
    ctx["counts"] = {
        "events": {
            "total": len(evs),
            **{f"severity_{s}": sum(1 for e in evs if e["severity"] == s)
               for s in (1, 2, 3, 4)},
            "severity_3plus": sum(1 for e in evs if e["severity"] >= 3),
        },
        "commands": {
            "total": len(cmds),
            "rejected": sum(1 for c in cmds
                            if c["outcome"] in ("REJECTED", "FAILED")),
        },
        "passes": len(pss),
    }
    ctx["gaps"] = {
        "received_pct": round(100.0 * rec / exp, 1) if exp else 0.0,
        "missing_pct": round(100.0 * (exp - rec) / exp, 1) if exp else 0.0,
    }
    later = [p for p in passes_in(dataset, catalogs) if p["aos"] > period.end_cuc]
    ctx["next_pass"] = ({"station": later[0]["station"],
                         "aos_utc": cuc_to_utc_str(later[0]["aos"])}
                        if later else {"station": "—", "aos_utc": "—"})
    return ctx


# ── top level ────────────────────────────────────────────────────────────

def render_report_html(spec: ReportSpec, dataset: TelemetryDataset,
                       period: Period, catalogs: Catalogs,
                       vars: Optional[dict[str, Any]] = None,
                       spec_yaml: str = "") -> str:
    vars = vars or {}
    ctx = _build_context(spec, dataset, period, catalogs, vars)
    parts: list[str] = []
    for sec in spec.sections:
        try:
            if isinstance(sec, HeaderSection):
                parts.append(_render_header(sec, spec, dataset, period))
            elif isinstance(sec, NarrativeSection):
                parts.append(_render_narrative(sec, ctx))
            elif isinstance(sec, CompletenessSection):
                parts.append(_render_completeness(sec, dataset, period, catalogs))
            elif isinstance(sec, PlotSection):
                # A backend that imports but cannot draw (no display, an
                # unwritable MPLCONFIGDIR, a font-cache failure) used to take
                # the whole report out with a 500 from somewhere inside
                # matplotlib. One bad plot degrades to one stated notice.
                try:
                    parts.append(_render_plot_section(sec, spec, dataset,
                                                      period, catalogs))
                except Exception as e:  # noqa: BLE001
                    _log.exception("plot section %r failed to render", sec.id)
                    parts.append(_plot_failure_html(sec, e))
            elif isinstance(sec, StatsSection):
                parts.append(_render_stats(sec, spec, dataset, period, catalogs))
            elif isinstance(sec, EventsSection):
                parts.append(_render_events(sec, dataset, period, catalogs))
            elif isinstance(sec, AlarmsSection):
                parts.append(_render_alarms(sec, dataset, period, catalogs))
            elif isinstance(sec, CommandsSection):
                parts.append(_render_commands(sec, dataset, period))
            elif isinstance(sec, BreakdownSection):
                parts.append(_render_breakdown(sec, dataset, period, catalogs))
            elif isinstance(sec, TableSection):
                parts.append(_render_table(sec, dataset, period))
            elif isinstance(sec, ConsoleLogSection):
                parts.append(_render_console_log(sec, period))
            elif isinstance(sec, PassRollupSection):
                parts.append(_render_pass_rollups(sec, period))
            elif isinstance(sec, AppendixSection):
                parts.append(_render_appendix(sec, spec, dataset, spec_yaml))
        except Exception as e:  # noqa: BLE001 — a bad section must not kill the report
            parts.append(f"<h2>{_esc(getattr(sec, 'id', '?'))}</h2>"
                         f"<div class='nodata'>Section failed to render: "
                         f"{_esc(e)}</div>")

    subtitle = _substitute(spec.report.subtitle, ctx)
    title = _substitute(spec.report.title, ctx)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_CSS}</style></head><body>
<h1>{_esc(title)}</h1>
<div class="sub">{_esc(subtitle)}
 <span class="classif">{_esc(spec.report.classification)}</span></div>
{''.join(parts)}
<footer>Generated by smo_common.reporting — report id {_esc(spec.report.id)},
audience {_esc(spec.report.audience)}. All timestamps on-board CUC (sim
time) rendered as UTC; the dump filename wall-clock timestamp is provenance
only and never used for period selection.</footer>
</body></html>"""
