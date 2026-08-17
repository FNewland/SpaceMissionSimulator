"""TelemetryDataset — one decode pass over N dump files.

Produces per-parameter time series, event/alarm/verification lists, store
status snapshots, a packet census and error counters, all on the **CUC (sim
time)** axis. The dump filename timestamp is wall clock and is never used to
place data in time (brief 06: under acceleration the two axes are
unrelated) — it is carried only as provenance metadata.

Every packet carries an on-board timestamp. ``TMBuilder._pack_tm`` no longer
defaults ``cuc`` to 0 — it reads the on-board clock, and raises if none is
wired (``MissingTimeSourceError``) — so the "untimestamped packet" case this
module used to paper over with ``zero_cuc_policy`` can no longer arise. The
policy is gone: after TIME_ARCHITECTURE.md Part B a CUC of 0 is a *legitimate*
time (the OBC clock starts at J2000/CUC 0 out of a bootloader reset), so
treating 0 as "unknown" would now actively misplace post-reboot telemetry.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from ..dump_store import iter_packets, parse_dump_filename
from .catalogs import Catalogs
from .decode import (
    decode_event,
    decode_packet,
    decode_s12_transition,
    decode_s15_store_status,
    decode_verification,
)

__all__ = ["TelemetryDataset"]


@dataclass
class TelemetryDataset:
    catalogs: Catalogs
    #: pid -> [(cuc_float, value)], file/decode order, CUC-sorted at the end
    series: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    #: sid -> [cuc_float, ...] HK packet times
    hk_times: dict[int, list[float]] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    s12_transitions: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    store_status: list[dict] = field(default_factory=list)
    census: dict[tuple[int, int], int] = field(default_factory=dict)
    apids: set[int] = field(default_factory=set)
    crc_errors: int = 0
    undecodable: int = 0
    total_packets: int = 0
    files: list[dict] = field(default_factory=list)
    first_cuc: Optional[float] = None
    last_cuc: Optional[float] = None

    # ── construction ────────────────────────────────────────────────

    @classmethod
    def from_dumps(cls, paths: Iterable[Path | str],
                   catalogs: Catalogs) -> "TelemetryDataset":
        ds = cls(catalogs=catalogs)
        for path in paths:
            ds._ingest_file(Path(path))
        # Sort every timed view by CUC (dumps of different stores overlap).
        for pid in ds.series:
            ds.series[pid].sort(key=lambda p: p[0])
        for sid in ds.hk_times:
            ds.hk_times[sid].sort()
        ds.events.sort(key=lambda e: e.get("t", 0))
        ds.s12_transitions.sort(key=lambda e: e.get("t", 0))
        ds.verifications.sort(key=lambda e: e.get("t", 0))
        return ds

    def _ingest_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError as e:
            self.files.append({"filename": path.name, "error": str(e)})
            return
        store_id, wall_ts = parse_dump_filename(path.name)

        # First pass: decode with file position, keep raw CUCs.
        records: list[dict] = []
        n = 0
        for raw in iter_packets(data):
            n += 1
            self.total_packets += 1
            pkt = decode_packet(raw)
            if pkt is None or pkt.secondary is None:
                self.undecodable += 1
                continue
            if not pkt.crc_valid:
                self.crc_errors += 1
            self.apids.add(pkt.primary.apid)
            svc, sub = pkt.secondary.service, pkt.secondary.subtype
            self.census[(svc, sub)] = self.census.get((svc, sub), 0) + 1
            # Full precision: coarse seconds + fine (1/65536 s) ticks.
            cuc = float(pkt.secondary.cuc_seconds)
            rec: dict[str, Any] = {"svc": svc, "sub": sub, "t": cuc,
                                   "data": pkt.data_field, "pkt": pkt}
            if svc == 5:
                rec["body"] = decode_event(pkt.data_field)
            records.append(rec)

        self.files.append({
            "filename": path.name, "store_id": store_id,
            "wall_timestamp": wall_ts.isoformat() if wall_ts else None,
            "packets": n, "size_bytes": len(data),
        })

        # Second pass: route into aggregates.
        for rec in records:
            t = rec.get("t")
            if t is None:
                continue
            svc, sub = rec["svc"], rec["sub"]
            if self.first_cuc is None or t < self.first_cuc:
                self.first_cuc = t
            if self.last_cuc is None or t > self.last_cuc:
                self.last_cuc = t
            if svc == 3 and sub == 25 and len(rec["data"]) >= 2:
                sid = struct.unpack(">H", rec["data"][:2])[0]
                self.hk_times.setdefault(sid, []).append(t)
                hk_struct = self.catalogs.hk_structs.get(sid)
                if hk_struct:
                    from .decode import decode_hk_body
                    for pid, val in decode_hk_body(rec["data"][2:], hk_struct).items():
                        self.series.setdefault(pid, []).append((t, val))
            elif svc == 5:
                body = rec.get("body") or decode_event(rec["data"])
                self.events.append({
                    "t": t, "subtype": sub, **body})
            elif svc == 12 and sub in (9, 10):
                tr = decode_s12_transition(rec["data"], sub)
                if tr:
                    self.s12_transitions.append(
                        {"t": t, **tr})
            elif svc == 1:
                self.verifications.append({
                    "t": t, **decode_verification(rec["data"], sub)})
            elif svc == 15 and sub == 14:
                self.store_status.append({
                    "t": t, "stores": decode_s15_store_status(rec["data"])})

    # ── views ───────────────────────────────────────────────────────

    def series_for(self, pid: int, start: float | None = None,
                   end: float | None = None) -> list[tuple[float, float]]:
        pts = self.series.get(pid, [])
        if start is None and end is None:
            return list(pts)
        return [(t, v) for t, v in pts
                if (start is None or t >= start) and (end is None or t <= end)]

    def events_in(self, start: float | None = None, end: float | None = None,
                  severity_min: int = 1,
                  include_synthetic_s12: bool = True) -> list[dict]:
        out = []
        for e in self.events:
            t = e["t"]
            if start is not None and t < start:
                continue
            if end is not None and t > end:
                continue
            if e["severity"] < severity_min:
                continue
            if not include_synthetic_s12 and e.get("s12_param_id") is not None:
                continue
            out.append(e)
        return out

    def window(self) -> tuple[Optional[float], Optional[float]]:
        return self.first_cuc, self.last_cuc
