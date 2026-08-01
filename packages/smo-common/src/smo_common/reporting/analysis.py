"""Statistics, out-of-limit time, completeness/gap and command analysis.

All time accounting is CUC-based and **sample-count driven**: CUC has 1 s
resolution and SID 1 samples at 1 Hz, so adjacent samples routinely share a
timestamp. A sample is charged its SID's nominal cadence
(``hk_structures.yaml interval_s``) of dwell time, which is exact for a
complete stream and degrades honestly with gaps.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from .catalogs import Catalogs
from .dataset import TelemetryDataset
from .periods import Period, cuc_to_utc_str, orbit_period_s, passes_in

__all__ = [
    "param_stats", "completeness", "commands_view", "alarm_pairs",
    "events_view", "breakdown_bins",
]


def param_stats(dataset: TelemetryDataset, pid: int, period: Period,
                catalogs: Catalogs) -> Optional[dict[str, Any]]:
    """Stats row for one parameter over the period; None when no samples."""
    pts = dataset.series_for(pid, period.start_cuc, period.end_cuc)
    if not pts:
        return None
    vals = [v for _, v in pts]
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    mn, mx = min(vals), max(vals)
    t_min = next(t for t, v in pts if v == mn)
    t_max = next(t for t, v in pts if v == mx)
    cadence = catalogs.cadence_for_pid(pid) or 1.0

    lim = catalogs.limits_by_id.get(pid)
    ool_yellow_s = ool_red_s = 0.0
    for _, v in pts:
        if not lim:
            break
        red = ((lim.get("red_low") is not None and v < lim["red_low"]) or
               (lim.get("red_high") is not None and v > lim["red_high"]))
        yellow = ((lim.get("yellow_low") is not None and v < lim["yellow_low"]) or
                  (lim.get("yellow_high") is not None and v > lim["yellow_high"]))
        if red:
            ool_red_s += cadence
        if red or yellow:
            ool_yellow_s += cadence
    covered_s = n * cadence
    expected = max(int(period.duration_s / cadence), 1)

    return {
        "pid": pid,
        "name": catalogs.param_name(pid),
        "units": catalogs.param_units(pid),
        "count": n,
        "min": mn, "min_at": cuc_to_utc_str(t_min),
        "max": mx, "max_at": cuc_to_utc_str(t_max),
        "mean": mean,
        "stddev": math.sqrt(var),
        "first": vals[0], "last": vals[-1],
        "ool_yellow_s": round(ool_yellow_s, 1),
        "ool_red_s": round(ool_red_s, 1),
        "ool_pct": round(100.0 * ool_yellow_s / covered_s, 2) if covered_s else 0.0,
        "samples_expected": expected,
        "samples_missing": max(expected - n, 0),
        "has_limits": bool(lim),
        "limits": lim or {},
    }


def completeness(dataset: TelemetryDataset, period: Period, catalogs: Catalogs,
                 sids: list[int], gap_threshold_multiple: float = 3.0) -> list[dict]:
    """Per-SID expected/received/missing/gap analysis over the period."""
    out = []
    for sid in sids:
        struct_def = catalogs.hk_structs.get(sid)
        cadence = struct_def["interval_s"] if struct_def else 4.0
        times = [t for t in dataset.hk_times.get(sid, [])
                 if period.start_cuc <= t <= period.end_cuc]
        times.sort()
        received = len(times)
        if times:
            span = max(times[-1] - times[0], 0.0)
            expected = int(span / cadence) + 1
        else:
            expected = int(period.duration_s / cadence) if period.duration_s else 0
        gaps = []
        threshold = gap_threshold_multiple * cadence
        for a, b in zip(times, times[1:]):
            if (b - a) > threshold:
                gaps.append({"start_utc": cuc_to_utc_str(a),
                             "end_utc": cuc_to_utc_str(b),
                             "start_cuc": a, "end_cuc": b,
                             "duration_s": round(b - a, 1)})
        longest = max((g["duration_s"] for g in gaps), default=0.0)
        missing = max(expected - received, 0)
        out.append({
            "sid": sid,
            "name": struct_def["name"] if struct_def else f"SID{sid}",
            "cadence_s": cadence,
            "expected": expected,
            "received": received,
            "missing": missing,
            "missing_pct": round(100.0 * missing / expected, 2) if expected else 0.0,
            "longest_gap_s": longest,
            "gaps": gaps,
        })
    return out


_S1_ACCEPT = {1: "OK", 2: "FAIL"}
_S1_EXEC_OK = {3, 5, 7}
_S1_EXEC_FAIL = {4, 8}


def commands_view(dataset: TelemetryDataset, period: Period) -> list[dict]:
    """Command history reconstructed from S1 verification reports.

    The archive carries only the verification side (never the TCs), so
    identity is the request id — ``(apid << 14) | seq``. Outcomes:
    COMPLETED (S1.7 seen), FAILED (S1.4/S1.8), REJECTED (S1.2),
    ACCEPTED (S1.1 only), NO_VERIFICATION shouldn't occur here by
    construction (no S1 at all means no row).
    """
    by_req: dict[int, dict] = {}
    for v in dataset.verifications:
        if not (period.start_cuc <= v["t"] <= period.end_cuc):
            continue
        rid = v["request_id"]
        row = by_req.setdefault(rid, {
            "request_id": rid, "apid": v["tc_apid"], "seq": v["tc_seq"],
            "t": v["t"], "utc": cuc_to_utc_str(v["t"]),
            "time_estimated": v.get("time_estimated", False),
            "accept": "--", "execute": "--",
            "error_code": 0, "reason": "", "subtypes": [],
        })
        row["t"] = min(row["t"], v["t"])
        row["utc"] = cuc_to_utc_str(row["t"])
        row["subtypes"].append(v["subtype"])
        st = v["subtype"]
        if st in (1, 2):
            row["accept"] = _S1_ACCEPT[st]
        if st in _S1_EXEC_OK:
            row["execute"] = "OK" if st == 7 else row["execute"] or "RUNNING"
        if st in _S1_EXEC_FAIL:
            row["execute"] = "FAIL"
        if v["error_code"]:
            row["error_code"] = v["error_code"]
        if v["reason"]:
            row["reason"] = v["reason"]

    out = []
    for row in by_req.values():
        sts = set(row["subtypes"])
        if 2 in sts:
            outcome = "REJECTED"
        elif sts & _S1_EXEC_FAIL:
            outcome = "FAILED"
        elif 7 in sts:
            outcome = "COMPLETED"
        elif 1 in sts:
            outcome = "ACCEPTED"
        else:
            outcome = "NO_VERIFICATION"
        row["outcome"] = outcome
        try:
            from ..protocol.pus_services import failure_code_text
            row["error_text"] = (failure_code_text(row["error_code"])
                                 if row["error_code"] else "")
        except Exception:  # noqa: BLE001
            row["error_text"] = ""
        out.append(row)
    out.sort(key=lambda r: r["t"])
    return out


def alarm_pairs(dataset: TelemetryDataset, period: Period, catalogs: Catalogs,
                severity_min: int = 1) -> dict[str, Any]:
    """Pair S12 limit entries with their clears; call out the unpaired."""
    trans = [t for t in dataset.s12_transitions
             if period.start_cuc <= t["t"] <= period.end_cuc]
    open_by_key: dict[int, dict] = {}
    paired: list[dict] = []
    unpaired: list[dict] = []
    for tr in trans:
        key = tr.get("mon_id", tr["param_id"])
        if tr["entered"]:
            if tr.get("severity", 2) < severity_min:
                continue
            open_by_key[key] = tr
        else:
            entry = open_by_key.pop(key, None)
            if entry:
                paired.append({
                    "param_id": entry["param_id"],
                    "param_name": catalogs.param_name(entry["param_id"]),
                    "mon_id": entry.get("mon_id"),
                    "severity": entry.get("severity"),
                    "value": entry.get("value"),
                    "low_limit": entry.get("low_limit"),
                    "high_limit": entry.get("high_limit"),
                    "entered_utc": cuc_to_utc_str(entry["t"]),
                    "cleared_utc": cuc_to_utc_str(tr["t"]),
                    "duration_s": round(tr["t"] - entry["t"], 1),
                    "time_estimated": entry.get("time_estimated", False)
                    or tr.get("time_estimated", False),
                })
    for key, entry in open_by_key.items():
        unpaired.append({
            "param_id": entry["param_id"],
            "param_name": catalogs.param_name(entry["param_id"]),
            "mon_id": entry.get("mon_id"),
            "severity": entry.get("severity"),
            "value": entry.get("value"),
            "low_limit": entry.get("low_limit"),
            "high_limit": entry.get("high_limit"),
            "entered_utc": cuc_to_utc_str(entry["t"]),
            "time_estimated": entry.get("time_estimated", False),
        })
    return {"paired": paired, "unpaired": unpaired, "total": len(trans)}


def events_view(dataset: TelemetryDataset, period: Period, catalogs: Catalogs,
                severity_min: int = 1,
                include_synthetic_s12: bool = True) -> list[dict]:
    rows = []
    for e in dataset.events_in(period.start_cuc, period.end_cuc,
                               severity_min, include_synthetic_s12):
        eid = e["event_id"]
        synth_pid = e.get("s12_param_id")
        if synth_pid is not None:
            name = f"S12 {'LIMIT' if eid & 0xF000 == 0x9000 else 'CLEAR'} " \
                   f"{catalogs.param_name(synth_pid)}"
            subsystem = catalogs.params_by_id.get(synth_pid, {}).get("subsystem", "")
        else:
            name = catalogs.event_name(eid)
            subsystem = catalogs.event_subsystem(eid)
        rows.append({
            "t": e["t"], "utc": cuc_to_utc_str(e["t"]),
            "cuc": int(e["t"]),
            "time_estimated": e.get("time_estimated", False),
            "id": eid, "id_hex": f"0x{eid:04X}",
            "name": name, "severity": e["severity"],
            "subsystem": subsystem, "aux_text": e.get("aux_text", ""),
        })
    return rows


def breakdown_bins(dataset: TelemetryDataset, period: Period,
                   catalogs: Catalogs, by: str) -> list[dict]:
    """Per-orbit / per-pass / per-hour metric rows over the period."""
    bins: list[dict] = []
    if by == "orbit":
        per = orbit_period_s(catalogs)
        t0s = catalogs.t0_epoch()
        from .periods import _iso_to_cuc
        anchor = _iso_to_cuc(t0s) if t0s else period.start_cuc
        n0 = int((period.start_cuc - anchor) // per)
        t = anchor + n0 * per
        i = n0
        while t < period.end_cuc:
            bins.append({"label": f"orbit {i + 1}",
                         "start": max(t, period.start_cuc),
                         "end": min(t + per, period.end_cuc)})
            t += per
            i += 1
    elif by == "pass":
        for p in passes_in(dataset, catalogs):
            if p["los"] < period.start_cuc or p["aos"] > period.end_cuc:
                continue
            bins.append({"label": f"{p['station']}",
                         "start": p["aos"], "end": p["los"],
                         "station": p["station"]})
    else:  # hour
        t = period.start_cuc
        while t < period.end_cuc:
            bins.append({"label": cuc_to_utc_str(t)[11:16],
                         "start": t, "end": min(t + 3600, period.end_cuc)})
            t += 3600

    soc_pid = catalogs.param_id_for("eps.bat_soc")
    bat_t_pid = catalogs.param_id_for("eps.bat_temp")
    att_pid = catalogs.param_id_for("aocs.att_error")
    rows = []
    for b in bins:
        sub = Period(b["start"], b["end"], "bin", b["label"])
        pkts = sum(1 for ts in dataset.hk_times.values()
                   for t in ts if b["start"] <= t <= b["end"])
        evs = dataset.events_in(b["start"], b["end"], severity_min=3)
        cmds = commands_view(dataset, sub)
        row = {
            "label": b["label"],
            "station": b.get("station", ""),
            "start_utc": cuc_to_utc_str(b["start"]),
            "end_utc": cuc_to_utc_str(b["end"]),
            "duration_s": round(b["end"] - b["start"], 1),
            "packets": pkts,
            "events_sev3plus": len(evs),
            "commands_sent": len(cmds),
            "commands_rejected": sum(1 for c in cmds
                                     if c["outcome"] in ("REJECTED", "FAILED")),
        }
        for key, pid, fn in (("min_soc", soc_pid, min),
                             ("max_bat_temp", bat_t_pid, max),
                             ("max_att_error", att_pid, max)):
            if pid is not None:
                pts = dataset.series_for(pid, b["start"], b["end"])
                row[key] = round(fn(v for _, v in pts), 2) if pts else None
            else:
                row[key] = None
        rows.append(row)
    return rows
