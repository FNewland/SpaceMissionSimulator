"""SMO MCS — SQLite Telemetry Archive.

Provides persistent storage for telemetry parameters, events, alarms,
and command history.  Uses a single SQLite database file so that TM
data survives MCS restarts and can be replayed for S15 playback.

Tables
------
tm_parameters : time-series of parameter values (param_name, value, utc)
tm_events     : S5 event log (event_id, severity, description, utc)
tm_alarms     : alarm journal (severity, subsystem, parameter, value, acked)
tc_log        : command history (seq, service, subtype, position, state, utc)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_parameters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,
    param_name  TEXT    NOT NULL,
    value       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tm_param_name_utc
    ON tm_parameters (param_name, utc_epoch);

CREATE TABLE IF NOT EXISTS tm_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,
    event_id    INTEGER NOT NULL,
    severity    INTEGER NOT NULL,
    subsystem   TEXT    DEFAULT '',
    description TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tm_events_utc ON tm_events (utc_epoch);

CREATE TABLE IF NOT EXISTS tm_alarms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,
    severity    INTEGER NOT NULL,
    subsystem   TEXT    DEFAULT '',
    parameter   TEXT    DEFAULT '',
    value       TEXT    DEFAULT '',
    source      TEXT    DEFAULT '',
    acknowledged INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tm_alarms_utc ON tm_alarms (utc_epoch);

CREATE TABLE IF NOT EXISTS tc_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,
    seq         INTEGER NOT NULL,
    name        TEXT    DEFAULT '',
    service     INTEGER DEFAULT 0,
    subtype     INTEGER DEFAULT 0,
    data_hex    TEXT    DEFAULT '',
    position    TEXT    DEFAULT '',
    state       TEXT    DEFAULT 'SENT'
);

CREATE INDEX IF NOT EXISTS idx_tc_log_seq ON tc_log (seq);
"""

# Maximum parameter rows before automatic pruning (per parameter).
# At 1 Hz, 86 400 rows = 24 hours of data.  We keep up to 7 days.
_MAX_ROWS_PER_PARAM = 604_800


class TMArchive:
    """SQLite-backed telemetry archive."""

    def __init__(self, db_path: str | Path = "tm_archive.db"):
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # ── Lifecycle ─────────────────────────────────────────────────

    def open(self) -> None:
        """Open (or create) the database and ensure schema is present."""
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.info("TM archive opened: %s", self._db_path)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Parameter storage ─────────────────────────────────────────

    def store_parameters(self, params: dict[str, float],
                         utc_epoch: float | None = None) -> None:
        """Store a batch of parameter values at the given time."""
        if not self._conn or not params:
            return
        t = utc_epoch or time.time()
        rows = [(t, name, val) for name, val in params.items()]
        self._conn.executemany(
            "INSERT INTO tm_parameters (utc_epoch, param_name, value) "
            "VALUES (?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def query_parameters(
        self,
        param_name: str,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 3600,
    ) -> list[dict]:
        """Retrieve time-series for a single parameter."""
        if not self._conn:
            return []
        sql = "SELECT utc_epoch, value FROM tm_parameters WHERE param_name = ?"
        args: list[Any] = [param_name]
        if start_epoch is not None:
            sql += " AND utc_epoch >= ?"
            args.append(start_epoch)
        if end_epoch is not None:
            sql += " AND utc_epoch <= ?"
            args.append(end_epoch)
        sql += " ORDER BY utc_epoch DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [{"utc_epoch": r[0], "value": r[1]} for r in reversed(rows)]

    # ── Event storage ─────────────────────────────────────────────

    def store_event(self, event_id: int, severity: int,
                    subsystem: str = "", description: str = "",
                    utc_epoch: float | None = None) -> None:
        if not self._conn:
            return
        t = utc_epoch or time.time()
        self._conn.execute(
            "INSERT INTO tm_events (utc_epoch, event_id, severity, subsystem, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (t, event_id, severity, subsystem, description),
        )
        self._conn.commit()

    def query_events(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        severity_min: int = 0,
        limit: int = 500,
    ) -> list[dict]:
        if not self._conn:
            return []
        sql = "SELECT utc_epoch, event_id, severity, subsystem, description FROM tm_events WHERE severity >= ?"
        args: list[Any] = [severity_min]
        if start_epoch is not None:
            sql += " AND utc_epoch >= ?"
            args.append(start_epoch)
        if end_epoch is not None:
            sql += " AND utc_epoch <= ?"
            args.append(end_epoch)
        sql += " ORDER BY utc_epoch DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [
            {"utc_epoch": r[0], "event_id": r[1], "severity": r[2],
             "subsystem": r[3], "description": r[4]}
            for r in reversed(rows)
        ]

    # ── Alarm storage ─────────────────────────────────────────────

    def store_alarm(self, alarm: dict) -> int:
        """Store an alarm and return the row id."""
        if not self._conn:
            return -1
        cur = self._conn.execute(
            "INSERT INTO tm_alarms "
            "(utc_epoch, severity, subsystem, parameter, value, source, acknowledged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                alarm.get("timestamp", time.time()),
                alarm.get("severity", 0),
                alarm.get("subsystem", ""),
                alarm.get("parameter", ""),
                str(alarm.get("value", "")),
                alarm.get("source", ""),
                1 if alarm.get("acknowledged") else 0,
            ),
        )
        self._conn.commit()
        return cur.lastrowid or -1

    def acknowledge_alarm(self, alarm_db_id: int) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "UPDATE tm_alarms SET acknowledged = 1 WHERE id = ?",
            (alarm_db_id,),
        )
        self._conn.commit()

    def query_alarms(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
        if not self._conn:
            return []
        sql = "SELECT id, utc_epoch, severity, subsystem, parameter, value, source, acknowledged FROM tm_alarms WHERE 1=1"
        args: list[Any] = []
        if start_epoch is not None:
            sql += " AND utc_epoch >= ?"
            args.append(start_epoch)
        if end_epoch is not None:
            sql += " AND utc_epoch <= ?"
            args.append(end_epoch)
        sql += " ORDER BY utc_epoch DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [
            {"id": r[0], "utc_epoch": r[1], "severity": r[2],
             "subsystem": r[3], "parameter": r[4], "value": r[5],
             "source": r[6], "acknowledged": bool(r[7])}
            for r in reversed(rows)
        ]

    # ── Command log ───────────────────────────────────────────────

    def store_command(self, seq: int, name: str = "", service: int = 0,
                      subtype: int = 0, data_hex: str = "",
                      position: str = "", state: str = "SENT",
                      utc_epoch: float | None = None) -> None:
        if not self._conn:
            return
        t = utc_epoch or time.time()
        self._conn.execute(
            "INSERT INTO tc_log "
            "(utc_epoch, seq, name, service, subtype, data_hex, position, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (t, seq, name, service, subtype, data_hex, position, state),
        )
        self._conn.commit()

    def update_command_state(self, seq: int, state: str) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "UPDATE tc_log SET state = ? WHERE seq = ? "
            "AND id = (SELECT MAX(id) FROM tc_log WHERE seq = ?)",
            (state, seq, seq),
        )
        self._conn.commit()

    # ── Playback support ──────────────────────────────────────────

    def get_playback_data(
        self,
        subsystem: str,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 3600,
    ) -> list[dict]:
        """Return archived parameter data for a subsystem (for S15 playback).

        Queries all parameters whose name starts with ``subsystem.`` prefix.
        """
        if not self._conn:
            return []
        prefix = f"{subsystem}.%"
        t_end = end_epoch or time.time()
        t_start = start_epoch or (t_end - 3600)  # default 1 hour
        rows = self._conn.execute(
            "SELECT utc_epoch, param_name, value FROM tm_parameters "
            "WHERE param_name LIKE ? AND utc_epoch BETWEEN ? AND ? "
            "ORDER BY utc_epoch LIMIT ?",
            (prefix, t_start, t_end, limit),
        ).fetchall()

        # Group by timestamp
        from collections import defaultdict
        by_time: dict[float, dict[str, float]] = defaultdict(dict)
        for t, name, val in rows:
            short = name.split(".", 1)[-1] if "." in name else name
            by_time[t][short] = val

        return [
            {"timestamp": t, **params}
            for t, params in sorted(by_time.items())
        ]

    # ── Maintenance ───────────────────────────────────────────────

    def prune(self, max_age_s: float = 604_800) -> int:
        """Delete records older than *max_age_s* seconds (default 7 days)."""
        if not self._conn:
            return 0
        cutoff = time.time() - max_age_s
        n = 0
        for table in ("tm_parameters", "tm_events", "tm_alarms", "tc_log"):
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE utc_epoch < ?", (cutoff,)
            )
            n += cur.rowcount
        self._conn.commit()
        if n:
            logger.info("Pruned %d archive rows older than %.0f s", n, max_age_s)
        return n
