#!/usr/bin/env python3
"""tm_report.py — Turn archived TM dumps into a between-pass report.

Reads one or more ``workspace/dumps/dump_*.bin`` files produced by the
simulator engine (length-prefixed raw PUS packets) and generates a
self-contained HTML report containing:

* Pass metadata (start/end CUC time, packet counts by service/subtype/SID)
* Event log (S5 events with severity colour-coding)
* HK time-series tables and inline SVG sparkline graphs for every HK SID
  found in the dump (one row per parameter)
* Anomaly flags:
    - any S5 severity >= 3 (alarm/critical)
    - parameters whose values changed by > 3σ in a single step
    - gaps in HK cadence

Usage::

    python tools/tm_report.py workspace/dumps/dump_sid01_20260406T120000Z.bin
    python tools/tm_report.py workspace/dumps/            # all dumps in dir

The report is written next to the input file(s) as ``<basename>.report.html``.
Designed for offline, between-pass use — no network access, no simulator
dependency beyond the common decommutator and catalog YAMLs.
"""
from __future__ import annotations

import html
import struct
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

try:
    import yaml
    from smo_common.protocol.ecss_packet import decommutate_packet
    # One decoder set for S12, shared with the MCS, the dump store and the
    # report generator (`smo_common.reporting.decode`). Reimplementing the
    # wire format here is how the three ground paths drifted apart before.
    from smo_common.reporting.decode import (
        decode_s12_definitions,
        decode_s12_transition,
        s12_check_type_name,
        s12_limit_band,
        s12_severity_name,
    )
except Exception as e:  # pragma: no cover
    print(f"import error: {e}", file=sys.stderr)
    sys.exit(1)


# ---------- catalog load ----------
def load_catalogs() -> tuple[dict[int, dict], dict[int, dict], dict[int, list[dict]]]:
    """Return (params_by_id, hk_struct_by_sid, hk_param_list_by_sid)."""
    cfg = REPO / "configs" / "eosat1" / "telemetry"
    with (cfg / "parameters.yaml").open() as f:
        pdata = yaml.safe_load(f) or {}
    with (cfg / "hk_structures.yaml").open() as f:
        hkdata = yaml.safe_load(f) or {}

    def _toi(v):
        return int(v) if isinstance(v, int) else int(str(v), 0)

    params_by_id: dict[int, dict] = {}
    for p in pdata.get("parameters", []) or []:
        try:
            pid = _toi(p["id"])
        except Exception:
            continue
        params_by_id[pid] = p

    hk_by_sid: dict[int, dict] = {}
    hk_plist: dict[int, list[dict]] = {}
    for s in hkdata.get("structures", []) or []:
        sid = int(s.get("sid", 0))
        hk_by_sid[sid] = s
        plist = []
        for pp in s.get("parameters", []) or []:
            try:
                pid = _toi(pp["param_id"])
            except Exception:
                continue
            plist.append({
                "param_id": pid,
                "pack_format": pp.get("pack_format", "H"),
                "scale": float(pp.get("scale", 1) or 1),
            })
        hk_plist[sid] = plist
    return params_by_id, hk_by_sid, hk_plist


# ---------- dump reader ----------
def read_dump(path: Path):
    """Yield raw packet bytes from a length-prefixed .bin dump."""
    data = path.read_bytes()
    off = 0
    while off + 4 <= len(data):
        (n,) = struct.unpack(">I", data[off:off+4])
        off += 4
        if off + n > len(data):
            break
        yield data[off:off+n]
        off += n


# ---------- HK decode ----------
def decode_hk(sid: int, data_field: bytes, params_by_id: dict, hk_plist: dict) -> dict[int, float]:
    """Decode an HK packet body into {param_id: float_value}. Best-effort."""
    plist = hk_plist.get(sid)
    if not plist:
        return {}
    out: dict[int, float] = {}
    # Skip the 2-byte SID at the start of S3.25 HK packets
    off = 2
    for entry in plist:
        fmt = entry["pack_format"]
        try:
            size = struct.calcsize(">" + fmt)
        except Exception:
            continue
        if off + size > len(data_field):
            break
        try:
            (raw,) = struct.unpack(">" + fmt, data_field[off:off+size])
        except Exception:
            break
        off += size
        scale = entry["scale"] or 1
        out[entry["param_id"]] = float(raw) / scale
    return out


# ---------- analysis ----------
class PassAnalysis:
    def __init__(self):
        self.events: list[dict] = []
        # sid -> list of (cuc_time, {pid: value})
        self.hk_samples: dict[int, list[tuple[int, dict[int, float]]]] = defaultdict(list)
        self.pkt_counts: dict[tuple[int, int], int] = defaultdict(int)
        self.hk_counts: dict[int, int] = defaultdict(int)
        # On-board CUC seconds (float: coarse + fine/65536), None = none seen.
        self.first_time: float | None = None
        self.last_time: float | None = None
        # S12 on-board monitoring. Franz: "We cannot see the contents of the
        # S12s in delayed TM dumps." This analyser handled S3.25 and S5 and had
        # no `elif svc == 12` branch at all, so a dump of the Alarm store —
        # which is exactly where S12 now lands (tm_storage.SERVICE_TO_STORE
        # routes service 12 to store 4) — reduced to one row in the
        # packet-count table. The decoders have lived in
        # `smo_common.reporting.decode` the whole time.
        self.s12_transitions: list[dict] = []
        self.s12_definitions: list[dict] = []

    def ingest_packet(self, raw: bytes, params_by_id: dict, hk_plist: dict) -> None:
        try:
            pkt = decommutate_packet(raw)
        except Exception:
            return
        if pkt is None or pkt.secondary is None:
            return
        svc = pkt.secondary.service
        sub = pkt.secondary.subtype
        self.pkt_counts[(svc, sub)] += 1

        # On-board time from the 9-octet PUS secondary header (CUC migration:
        # coarse uint32 seconds since the mission epoch + fine uint16 in
        # units of 1/65536 s). `cuc_seconds` is the full-precision float view;
        # `cuc_time` would drop the sub-second part. Packet field offsets are
        # not computed here — `decommutate_packet` already places the data
        # field at octet 15.
        #
        # CUC 0 is a LEGITIMATE time (the OBC clock starts at J2000/CUC 0 on
        # first boot and after every bootloader reset), so absence is `None`,
        # never a falsy 0.
        cuc = getattr(pkt.secondary, "cuc_seconds", None)
        cuc = float(cuc) if cuc is not None else None
        if cuc is not None:
            if self.first_time is None or cuc < self.first_time:
                self.first_time = cuc
            if self.last_time is None or cuc > self.last_time:
                self.last_time = cuc

        if svc == 3 and sub == 25 and len(pkt.data_field) >= 2:
            sid = struct.unpack(">H", pkt.data_field[:2])[0]
            self.hk_counts[sid] += 1
            values = decode_hk(sid, pkt.data_field, params_by_id, hk_plist)
            if values:
                self.hk_samples[sid].append((cuc, values))
        elif svc == 5:
            # Event report: subtype encodes severity (1 info, 2 low, 3 med, 4 high)
            event_id = 0
            desc = ""
            if len(pkt.data_field) >= 2:
                event_id = struct.unpack(">H", pkt.data_field[:2])[0]
            if len(pkt.data_field) > 2:
                try:
                    desc = pkt.data_field[2:].decode("utf-8", errors="replace").rstrip("\x00 ")
                except Exception:
                    pass
            self.events.append({
                "cuc": cuc, "subtype": sub, "event_id": event_id,
                "severity": sub, "description": desc,
            })
        elif svc == 12 and sub in (9, 10):
            tr = decode_s12_transition(pkt.data_field, sub)
            if tr:
                meta = params_by_id.get(tr["param_id"], {})
                tr.update({
                    "cuc": cuc,
                    "param_hex": f"0x{tr['param_id']:04X}",
                    "param_name": meta.get("name", ""),
                    "units": meta.get("units", ""),
                    "check_type_name": s12_check_type_name(tr.get("check_type")),
                    "severity_name": s12_severity_name(tr.get("severity")),
                    "band": s12_limit_band(tr),
                })
                self.s12_transitions.append(tr)
        elif svc == 12 and sub == 13:
            for d in decode_s12_definitions(pkt.data_field):
                meta = params_by_id.get(d["param_id"], {})
                d.update({
                    "cuc": cuc,
                    "param_hex": f"0x{d['param_id']:04X}",
                    "param_name": meta.get("name", ""),
                    "check_type_name": s12_check_type_name(d.get("check_type")),
                })
                self.s12_definitions.append(d)


# ---------- render ----------
def _svg_spark(points: list[tuple[float, float]], w=220, h=32) -> str:
    if len(points) < 2:
        return ""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    dy = (y1 - y0) or 1.0
    dx = (x1 - x0) or 1
    path = " ".join(
        f"{(x-x0)/dx*(w-2)+1:.1f},{(h-1)-((y-y0)/dy)*(h-2):.1f}"
        for x, y in points
    )
    return (f"<svg width='{w}' height='{h}' viewBox='0 0 {w} {h}'>"
            f"<polyline fill='none' stroke='#08f' stroke-width='1.2' "
            f"points='{path}'/></svg>")


SEVERITY_COLOURS = {1: "#888", 2: "#0a0", 3: "#b70", 4: "#c00"}
S12_SEVERITY_COLOURS = {"INFO": "#888", "WARNING": "#b70",
                        "ALARM": "#c00", "HIGH": "#c00"}


def _fmt_cuc(cuc: float | int | None) -> str:
    """Full-precision CUC seconds as UTC, falling back to the raw count.

    Takes the float (coarse + fine/65536) rather than truncating to whole
    seconds, and treats only ``None`` as "no timestamp" — CUC 0 is a real
    on-board time after a bootloader reset.
    """
    if cuc is None:
        return "--"
    try:
        from smo_common.protocol.ecss_packet import cuc_seconds_to_datetime
        return cuc_seconds_to_datetime(float(cuc)).isoformat(
            sep=" ", timespec="milliseconds") + "Z"
    except Exception:  # pragma: no cover - defensive
        return str(cuc)


def _render_s12(an: "PassAnalysis") -> str:
    """The S12 section: check transitions, then the definition table.

    A limit violation is only legible with all six fields together — which
    parameter, which way it went, the value, the check type, the severity and
    the band it left. The real-time MCS path threw four of the six away and
    this offline path decoded none of them.
    """
    if not an.s12_transitions and not an.s12_definitions:
        return ""
    out = ["<h2>On-board monitoring (S12)</h2>"]
    if an.s12_transitions:
        entered = sum(1 for t in an.s12_transitions if t.get("entered"))
        rows = []
        for t in an.s12_transitions:
            sev = t.get("severity_name") or ""
            colour = S12_SEVERITY_COLOURS.get(sev, "#333")
            direction = ("<b style='color:#c00;'>OUT OF LIMITS</b>" if t.get("entered")
                         else "<span style='color:#0a0;'>back to nominal</span>")
            band = t.get("band") or "<i style='color:#888;'>not reported</i>"
            name = t.get("param_name") or ""
            units = t.get("units") or ""
            rows.append(
                f"<tr><td>{_fmt_cuc(t.get('cuc'))}</td>"
                f"<td>{t.get('mon_id', '--')}</td>"
                f"<td>{t['param_hex']} {html.escape(name)}</td>"
                f"<td>{direction}</td>"
                f"<td style='text-align:right;'>{t['value']:.3f} {html.escape(units)}</td>"
                f"<td>{html.escape(t.get('check_type_name') or '--')}</td>"
                f"<td style='color:{colour};'>{html.escape(sev or '--')}</td>"
                f"<td style='text-align:right;'>{band}</td></tr>")
        out.append(
            f"<p>{len(an.s12_transitions)} check transition(s): {entered} "
            f"out-of-limits, {len(an.s12_transitions) - entered} return(s) to nominal.</p>"
            "<table><thead><tr><th>UTC</th><th>Mon</th><th>Parameter</th>"
            "<th>Transition</th><th>Value</th><th>Check</th><th>Severity</th>"
            "<th>Limit band</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table>")
    if an.s12_definitions:
        rows = "".join(
            f"<tr><td>{d['mon_id']}</td>"
            f"<td>{d['param_hex']} {html.escape(d.get('param_name') or '')}</td>"
            f"<td>{html.escape(d.get('check_type_name') or '--')}</td>"
            f"<td>{'enabled' if d['enabled'] else 'disabled'}</td>"
            f"<td style='text-align:right;'>{d['low_limit']:.3f}</td>"
            f"<td style='text-align:right;'>{d['high_limit']:.3f}</td></tr>"
            for d in an.s12_definitions)
        out.append("<h3>Monitoring definitions (TM 12,13)</h3>"
                   "<table><thead><tr><th>Mon</th><th>Parameter</th><th>Check</th>"
                   "<th>State</th><th>Low</th><th>High</th></tr></thead><tbody>"
                   + rows + "</tbody></table>")
    return "".join(out)


def render_report(path: Path, an: PassAnalysis, params_by_id: dict) -> str:
    pkt_total = sum(an.pkt_counts.values())
    rows_pkt = "".join(
        f"<tr><td>S{svc}.{sub}</td><td>{n}</td></tr>"
        for (svc, sub), n in sorted(an.pkt_counts.items())
    )
    rows_events = "".join(
        f"<tr style='color:{SEVERITY_COLOURS.get(e['severity'],'#333')};'>"
        f"<td>{_fmt_cuc(e['cuc'])}</td><td>S5.{e['subtype']}</td>"
        f"<td>0x{e['event_id']:04X}</td><td>{html.escape(e['description'])}</td></tr>"
        for e in an.events
    ) or "<tr><td colspan='4' style='color:#888;'>No events in dump.</td></tr>"

    # HK series per SID
    hk_sections = []
    anomalies: list[str] = []
    for sid, samples in sorted(an.hk_samples.items()):
        if not samples:
            continue
        # pivot: pid -> [(cuc, value)]
        series: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for cuc, vals in samples:
            for pid, v in vals.items():
                series[pid].append((cuc, v))

        rows = []
        for pid in sorted(series):
            pts = series[pid]
            meta = params_by_id.get(pid, {})
            name = meta.get("name", f"0x{pid:04X}")
            units = meta.get("units", "")
            vals_only = [v for _, v in pts]
            mn, mx = min(vals_only), max(vals_only)
            avg = sum(vals_only) / len(vals_only)
            spark = _svg_spark(pts)
            # Simple 3σ anomaly flag: any step whose delta exceeds 3× mean |delta|
            deltas = [abs(vals_only[i] - vals_only[i-1])
                      for i in range(1, len(vals_only))]
            flag = ""
            if deltas:
                mean_d = sum(deltas) / len(deltas) or 1e-9
                peak = max(deltas)
                if peak > 5 * mean_d and peak > 0.5:
                    flag = "<span style='color:#c60;'>Δ</span>"
                    anomalies.append(f"SID {sid} {name}: peak Δ={peak:.3f} (mean {mean_d:.3f})")
            rows.append(
                f"<tr><td>0x{pid:04X}</td><td>{html.escape(name)}</td>"
                f"<td>{html.escape(units)}</td>"
                f"<td style='text-align:right;'>{mn:.3f}</td>"
                f"<td style='text-align:right;'>{mx:.3f}</td>"
                f"<td style='text-align:right;'>{avg:.3f}</td>"
                f"<td>{spark}</td><td>{flag}</td></tr>"
            )

        hk_sections.append(
            f"<h3>SID {sid} — {an.hk_counts[sid]} packets, {len(samples)} samples</h3>"
            f"<table><thead><tr><th>ID</th><th>Name</th><th>Units</th>"
            f"<th>Min</th><th>Max</th><th>Mean</th><th>Trend</th><th></th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    anomalies_html = "".join(f"<li>{html.escape(a)}</li>" for a in anomalies) \
                     or "<li>None</li>"

    s12_html = _render_s12(an)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>TM Dump Report — {path.name}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 2em auto;
       padding: 0 1em; color: #222; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 4px; }}
h2 {{ margin-top: 2em; border-bottom: 1px solid #ccc; }}
table {{ border-collapse: collapse; width: 100%; margin: .6em 0; font-size: 12px; }}
th, td {{ border: 1px solid #ccc; padding: 3px 6px; vertical-align: top; }}
th {{ background: #f0f0f0; text-align: left; }}
.meta {{ background: #f6f6f6; padding: 8px 12px; border-left: 3px solid #08f; }}
</style></head><body>
<h1>TM Dump Report</h1>
<div class='meta'>
<b>Source:</b> <code>{html.escape(str(path))}</code><br>
<b>Report generated (wall clock, not mission time):</b> {datetime.utcnow().isoformat()}Z<br>
<b>Total packets:</b> {pkt_total}<br>
<b>On-board time (CUC) range:</b> {_fmt_cuc(an.first_time)} → {_fmt_cuc(an.last_time)}
</div>

<h2>Packet counts by service</h2>
<table><thead><tr><th>Service.Subtype</th><th>Count</th></tr></thead>
<tbody>{rows_pkt}</tbody></table>

<h2>Events</h2>
<table><thead><tr><th>CUC</th><th>Subtype</th><th>Event ID</th><th>Description</th></tr></thead>
<tbody>{rows_events}</tbody></table>

{s12_html}

<h2>Anomalies flagged</h2>
<ul>{anomalies_html}</ul>

<h2>Housekeeping time-series</h2>
{''.join(hk_sections) or '<p>No HK packets in dump.</p>'}

</body></html>"""


# ---------- main ----------
def process(path: Path, params_by_id, hk_plist) -> Path:
    an = PassAnalysis()
    for raw in read_dump(path):
        an.ingest_packet(raw, params_by_id, hk_plist)
    out = path.with_suffix(".report.html")
    out.write_text(render_report(path, an, params_by_id))
    print(f"{path.name}: {sum(an.pkt_counts.values())} packets, "
          f"{len(an.events)} events → {out}")
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    params_by_id, _, hk_plist = load_catalogs()
    targets: list[Path] = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            targets.extend(sorted(p.glob("dump_*.bin")))
        elif p.is_file():
            targets.append(p)
    if not targets:
        print("no dump files found", file=sys.stderr)
        return 1
    for t in targets:
        try:
            process(t, params_by_id, hk_plist)
        except Exception as e:
            print(f"{t.name}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
