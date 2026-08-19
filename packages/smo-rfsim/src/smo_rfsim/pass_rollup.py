"""End-of-pass RF rollups (POSITION_NEEDS G14, server half).

The radio frontend computes lock states, Eb/N0, frame counters and
throughput live and then loses them at LOS — a station produces a pass
report (§4.3: AOS/LOS achieved vs predicted, data volumes, faults) and the
rfsim had nothing for the GOM to review. This module accumulates one
rollup record per pass and appends it to a JSONL store served at
``GET /api/pass-rollups``.

Store choice: **JSONL under workspace/** rather than SQLite. The rfsim has
no existing database (the MCS archive's SQLite house pattern lives in
smo-mcs and holds ground TM, which this is not); its house pattern for
session products is flat files (``recording/``, ``workspace/``). One JSON
object per line, append-only, no locking concerns across the bridge's
single writer.

Timestamps: the bridge feeds sim time when the simulator WS provides it
(``state.sim_time``) and wall UTC otherwise; every record says which via
``time_base`` — the two are never mixed within one record.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Default store location, overridable for deployments/tests.
DEFAULT_STORE = "workspace/pass_rollups.jsonl"


def store_path() -> Path:
    return Path(os.environ.get("SMO_PASS_ROLLUPS", DEFAULT_STORE))


def read_rollups(limit: int = 200,
                 path: Optional[Path] = None) -> list[dict]:
    """Read the newest ``limit`` rollup records, oldest first."""
    p = path or store_path()
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("pass-rollup store unreadable: %s", e)
        return []
    return out[-limit:]


def _lock_name(v: Any) -> str:
    return str(getattr(v, "value", v) or "").upper()


class PassRollupTracker:
    """Accumulates per-pass RF statistics and writes one record at LOS.

    Driven by the bridge: :meth:`set_contact` on every sim WS feedback
    message (geometric contact = predicted AOS/LOS), :meth:`sample` on
    every radio-updater tick (measured lock/Eb-N0/frame counters).
    """

    def __init__(self, frame_length_bytes: int = 1115,
                 path: Optional[Path] = None):
        self._frame_length = int(frame_length_bytes)
        self._path = path or store_path()
        self._pass_count = 0
        self._active: Optional[dict] = None
        self._time_base = "wall"

    # ── bridge hooks ─────────────────────────────────────────────────

    def set_contact(self, in_contact: bool, gs_name: str,
                    now_ts: float, time_base: str = "wall",
                    status=None) -> None:
        """Contact transition from orbital geometry (predicted AOS/LOS)."""
        self._time_base = time_base
        if in_contact and self._active is None:
            self._pass_count += 1
            self._active = {
                "pass_id": self._pass_count,
                "gs_name": str(gs_name or ""),
                "aos_pred_ts": float(now_ts),
                "first_lock_ts": None,
                "eb_n0_sum": 0.0,
                "eb_n0_n": 0,
                "frames_ok_at_aos": None,
                "frames_bad_at_aos": None,
                "frames_ok_last": None,
                "frames_bad_last": None,
                "tier_names": [],
                "time_base": time_base,
            }
            if status is not None:
                self.sample(status, now_ts)
        elif in_contact and self._active is not None and gs_name:
            if not self._active["gs_name"]:
                self._active["gs_name"] = str(gs_name)
        elif (not in_contact) and self._active is not None:
            if status is not None:
                self.sample(status, now_ts)
            self._finalise(float(now_ts))

    def sample(self, status, now_ts: float) -> None:
        """One measured sample from the RadioStatus snapshot."""
        a = self._active
        if a is None:
            return
        if a["frames_ok_at_aos"] is None:
            a["frames_ok_at_aos"] = int(status.good_frames)
            a["frames_bad_at_aos"] = int(status.bad_frames)
        a["frames_ok_last"] = int(status.good_frames)
        a["frames_bad_last"] = int(status.bad_frames)
        if (a["first_lock_ts"] is None
                and _lock_name(status.carrier_lock) == "LOCKED"):
            a["first_lock_ts"] = float(now_ts)
        eb = float(getattr(status, "eb_n0_db", 0.0) or 0.0)
        if eb != 0.0:
            a["eb_n0_sum"] += eb
            a["eb_n0_n"] += 1
        tier = str(getattr(status, "tm_tier_name", "") or "")
        if tier and tier not in a["tier_names"]:
            a["tier_names"].append(tier)

    # ── record shape ─────────────────────────────────────────────────

    def _finalise(self, los_ts: float) -> None:
        a = self._active
        self._active = None
        if a is None:
            return
        ok0 = a["frames_ok_at_aos"]
        okn = a["frames_ok_last"]
        bad0 = a["frames_bad_at_aos"]
        badn = a["frames_bad_last"]
        frames_ok = None if (ok0 is None or okn is None) else max(0, okn - ok0)
        frames_bad = (None if (bad0 is None or badn is None)
                      else max(0, badn - bad0))
        total = ((frames_ok or 0) + (frames_bad or 0))
        record = {
            "pass_id": a["pass_id"],
            "gs_name": a["gs_name"],
            "time_base": a["time_base"],
            # Predicted vs achieved: aos_pred is geometric line-of-sight;
            # first_lock is when the receiver actually locked the carrier.
            "aos_pred_ts": a["aos_pred_ts"],
            "los_ts": los_ts,
            "duration_s": round(max(0.0, los_ts - a["aos_pred_ts"]), 1),
            "first_lock_ts": a["first_lock_ts"],
            "lock_delay_s": (None if a["first_lock_ts"] is None
                             else round(a["first_lock_ts"] - a["aos_pred_ts"], 1)),
            "locked": a["first_lock_ts"] is not None,
            # Honest null when no sample carried an Eb/N0.
            "mean_eb_n0_db": (round(a["eb_n0_sum"] / a["eb_n0_n"], 2)
                              if a["eb_n0_n"] else None),
            "frames_ok": frames_ok,
            "frames_bad": frames_bad,
            "fer": (round((frames_bad or 0) / total, 4) if total else None),
            "data_volume_bytes": (None if frames_ok is None
                                  else frames_ok * self._frame_length),
            "tier_names": a["tier_names"],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            logger.warning("pass-rollup append failed: %s", e)

    def active_summary(self) -> Optional[dict]:
        """The in-progress pass, for the API (never written to the store)."""
        a = self._active
        if a is None:
            return None
        return {
            "pass_id": a["pass_id"],
            "gs_name": a["gs_name"],
            "aos_pred_ts": a["aos_pred_ts"],
            "first_lock_ts": a["first_lock_ts"],
            "mean_eb_n0_db": (round(a["eb_n0_sum"] / a["eb_n0_n"], 2)
                              if a["eb_n0_n"] else None),
            "time_base": a["time_base"],
        }
