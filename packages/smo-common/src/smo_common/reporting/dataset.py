"""TelemetryDataset — one decode pass over N dump files.

Produces per-parameter time series, event/alarm/verification lists, store
status snapshots, a packet census and error counters, all on the **CUC (sim
time)** axis. The dump filename timestamp is wall clock and is never used to
place data in time (brief 06: under acceleration the two axes are
unrelated) — it is carried only as provenance metadata.

Zero-CUC packets (S1/S12/S6/S11/S15 — ``tm_builder._pack_tm`` defaults
``cuc=0``) are placed according to ``zero_cuc_policy``:

* ``interpolate`` (default) — strictly between their bracketing CUC-bearing
  neighbours, by file order (marked ``time_estimated=True``);
* ``exclude`` — dropped from timed views (still counted in the census);
* ``pin_to_dump_start`` — assigned the file's first non-zero CUC.
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
    zero_cuc_placed: int = 0
    zero_cuc_excluded: int = 0
    files: list[dict] = field(default_factory=list)
    first_cuc: Optional[float] = None
    last_cuc: Optional[float] = None

    # ── construction ────────────────────────────────────────────────

    @classmethod
    def from_dumps(cls, paths: Iterable[Path | str], catalogs: Catalogs,
                   zero_cuc_policy: str = "interpolate") -> "TelemetryDataset":
        ds = cls(catalogs=catalogs)
        for path in paths:
            ds._ingest_file(Path(path), zero_cuc_policy)
        # Sort every timed view by CUC (dumps of different stores overlap).
        for pid in ds.series:
            ds.series[pid].sort(key=lambda p: p[0])
        for sid in ds.hk_times:
            ds.hk_times[sid].sort()
        ds.events.sort(key=lambda e: e.get("t", 0))
        ds.s12_transitions.sort(key=lambda e: e.get("t", 0))
        ds.verifications.sort(key=lambda e: e.get("t", 0))
        return ds

    def _ingest_file(self, path: Path, zero_cuc_policy: str) -> None:
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
            cuc = int(pkt.secondary.cuc_time or 0)
            rec: dict[str, Any] = {"svc": svc, "sub": sub, "cuc": cuc,
                                   "data": pkt.data_field, "pkt": pkt}
            # S5 events carry a second CUC inside the body — authoritative
            # even if the secondary header one were missing.
            if svc == 5:
                body = decode_event(pkt.data_field)
                rec["body"] = body
                if not cuc and body["cuc"]:
                    rec["cuc"] = body["cuc"]
            records.append(rec)

        self.files.append({
            "filename": path.name, "store_id": store_id,
            "wall_timestamp": wall_ts.isoformat() if wall_ts else None,
            "packets": n, "size_bytes": len(data),
        })

        # Place zero-CUC packets.
        self._assign_times(records, zero_cuc_policy)

        # Second pass: route into aggregates.
        for rec in records:
            t = rec.get("t")
            if t is None:
                continue
            svc, sub = rec["svc"], rec["sub"]
            est = rec.get("time_estimated", False)
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
                    "t": t, "time_estimated": est, "subtype": sub, **body})
            elif svc == 12 and sub in (9, 10):
                tr = decode_s12_transition(rec["data"], sub)
                if tr:
                    self.s12_transitions.append(
                        {"t": t, "time_estimated": est, **tr})
            elif svc == 1:
                self.verifications.append({
                    "t": t, "time_estimated": est,
                    **decode_verification(rec["data"], sub)})
            elif svc == 15 and sub == 14:
                self.store_status.append({
                    "t": t, "time_estimated": est,
                    "stores": decode_s15_store_status(rec["data"])})

    def _assign_times(self, records: list[dict], policy: str) -> None:
        """Give every record a ``t`` (float CUC) per the zero-CUC policy."""
        nonzero = [(i, r["cuc"]) for i, r in enumerate(records) if r["cuc"]]
        first_nz = nonzero[0][1] if nonzero else None
        for idx, rec in enumerate(records):
            if rec["cuc"]:
                rec["t"] = float(rec["cuc"])
                continue
            if policy == "exclude":
                rec["t"] = None
                self.zero_cuc_excluded += 1
                continue
            if policy == "pin_to_dump_start" or not nonzero:
                rec["t"] = float(first_nz) if first_nz is not None else None
                if rec["t"] is not None:
                    rec["time_estimated"] = True
                    self.zero_cuc_placed += 1
                continue
            # interpolate: strictly between bracketing non-zero neighbours
            prev = next((c for i, c in reversed(nonzero) if i < idx), None)
            nxt = next((c for i, c in nonzero if i > idx), None)
            if prev is not None and nxt is not None:
                # position within the bracket by index distance
                lo = max(i for i, c in nonzero if i < idx)
                hi = min(i for i, c in nonzero if i > idx)
                frac = (idx - lo) / max(hi - lo, 1)
                rec["t"] = prev + (nxt - prev) * frac
                # keep strictly between when the bracket is a single second
                if rec["t"] <= prev:
                    rec["t"] = prev + 1e-3
                if rec["t"] >= nxt and nxt > prev:
                    rec["t"] = nxt - 1e-3
            elif prev is not None:
                rec["t"] = prev + 1e-3
            elif nxt is not None:
                rec["t"] = nxt - 1e-3
            else:
                rec["t"] = None
            if rec["t"] is not None:
                rec["time_estimated"] = True
                self.zero_cuc_placed += 1

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
