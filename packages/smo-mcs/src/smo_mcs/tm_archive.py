"""SMO MCS — SQLite Telemetry Archive.

Provides persistent storage for telemetry parameters, events, alarms,
and command history.  Uses a single SQLite database file so that TM
data survives MCS restarts and can be replayed for S15 playback.

TIME BASE — READ THIS BEFORE INTERPRETING A ROW
-----------------------------------------------
Every ``utc_epoch`` column in a **schema version 2** archive is **SIM TIME**
expressed as a Unix epoch float: the absolute UTC of the simulated world
(``MCSServer.sim_now()``), *not* the host wall clock.  That is what makes an
archived parameter correlatable with the on-board CUC carried by the packet
that produced it, and it is what makes an age computed from these rows freeze
when the simulator is paused and run at speed when it is accelerated.

Schema version **1** archives (anything written before the 2026-08c time
architecture change) carry host wall-clock epochs instead.  The two are not
comparable — a sim epoch is usually years away from the wall clock — so
``open()`` refuses to *silently* mix them: a pre-existing database with no
``archive_meta`` table is stamped ``schema_version = 1`` /
``time_base = 'wall'``, a loud warning is logged, and :attr:`time_base` /
:attr:`schema_version` report what the file actually holds so every API
response can say so.  There is deliberately no auto-conversion: the sim epoch
of the old run is not recorded anywhere and cannot be reconstructed.

Because of that, **no method defaults its timestamp**.  Every writer must pass
an explicit ``utc_epoch``; the old ``time.time()`` defaults are exactly how
wall-clock rows got in.

Tables
------
archive_meta  : key/value; ``schema_version`` and ``time_base``
tm_parameters : time-series of parameter values (param_name, value, utc, obc_cuc)
tm_events     : S5 event log (event_id, severity, description, utc, obc_cuc)
tm_alarms     : alarm journal (severity, subsystem, parameter, value, acked)
tc_log        : command history (seq, service, subtype, position, state, utc,
                sched_cuc = the on-board CUC the command was scheduled for,
                NULL for an immediate telecommand)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Current archive schema. Bumped from 1 when ``utc_epoch`` moved from host
#: wall clock to sim time (spec Part D.4).
SCHEMA_VERSION = 2

#: What ``utc_epoch`` means in a version-2 archive.
TIME_BASE_SIM = "sim"
#: What it meant in a version-1 archive.
TIME_BASE_WALL = "wall"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tm_parameters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- SIM TIME as a Unix epoch float (schema v2). See the module docstring.
    utc_epoch   REAL    NOT NULL,
    param_name  TEXT    NOT NULL,
    value       REAL    NOT NULL,
    -- Full-precision on-board CUC seconds of the newest packet that fed this
    -- batch, NULL when no packet has been received yet. This is the column
    -- that lets a row be correlated with the spacecraft's own clock.
    obc_cuc     REAL
);

CREATE INDEX IF NOT EXISTS idx_tm_param_name_utc
    ON tm_parameters (param_name, utc_epoch);

CREATE TABLE IF NOT EXISTS tm_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,   -- SIM TIME (schema v2)
    event_id    INTEGER NOT NULL,
    severity    INTEGER NOT NULL,
    subsystem   TEXT    DEFAULT '',
    description TEXT    DEFAULT '',
    obc_cuc     REAL               -- on-board CUC seconds of the S5 packet
);

CREATE INDEX IF NOT EXISTS idx_tm_events_utc ON tm_events (utc_epoch);

CREATE TABLE IF NOT EXISTS tm_alarms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,   -- SIM TIME (schema v2)
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
    utc_epoch   REAL    NOT NULL,   -- SIM TIME the TC was uplinked (schema v2)
    seq         INTEGER NOT NULL,
    name        TEXT    DEFAULT '',
    service     INTEGER DEFAULT 0,
    subtype     INTEGER DEFAULT 0,
    data_hex    TEXT    DEFAULT '',
    position    TEXT    DEFAULT '',
    state       TEXT    DEFAULT 'SENT',
    -- On-board CUC seconds this command was SCHEDULED for (S11 time tag).
    -- NULL for an immediate telecommand. Deliberately separate from
    -- utc_epoch: one is ground sim time, the other is the spacecraft's clock,
    -- and the gap between them is the whole point of the time-correlation
    -- exercise.
    sched_cuc   REAL
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
        self._schema_version: int = SCHEMA_VERSION
        self._time_base: str = TIME_BASE_SIM

    # ── Lifecycle ─────────────────────────────────────────────────

    @property
    def schema_version(self) -> int:
        """Schema version of the OPEN file (1 = legacy wall-clock rows)."""
        return self._schema_version

    @property
    def time_base(self) -> str:
        """``'sim'`` or ``'wall'`` — what ``utc_epoch`` means in this file."""
        return self._time_base

    @property
    def legacy_time_base(self) -> bool:
        """True when the open file predates the sim-time migration."""
        return self._time_base != TIME_BASE_SIM

    def _preexisting(self) -> bool:
        """True if this file already carried archive tables before we opened."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('tm_parameters','tm_events','tm_alarms','tc_log')"
        ).fetchone()
        return bool(row and row[0])

    def _add_missing_columns(self) -> None:
        """Bring a v1 file's tables up to the v2 column set, in place.

        The added columns are all nullable, so widening an old file is safe;
        it does NOT change the meaning of the rows already in it — that is
        what ``archive_meta.time_base`` records.
        """
        assert self._conn is not None
        wanted = {
            "tm_parameters": [("obc_cuc", "REAL")],
            "tm_events": [("obc_cuc", "REAL")],
            "tc_log": [("sched_cuc", "REAL")],
        }
        for table, cols in wanted.items():
            have = {r[1] for r in
                    self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, decl in cols:
                if name not in have:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def open(self) -> None:
        """Open (or create) the database and ensure schema is present.

        Also resolves the file's **time base**. A database that already has
        archive tables but no ``archive_meta`` row was written before the
        sim-time migration, so its ``utc_epoch`` values are host wall clock.
        It is stamped as version 1 and reported as such rather than being
        quietly read as sim time.
        """
        first_open = not Path(self._db_path).exists()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        had_tables = (not first_open) and self._preexisting()
        had_meta = bool(self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='archive_meta'").fetchone()[0])
        self._conn.executescript(_SCHEMA)
        self._add_missing_columns()

        row = self._conn.execute(
            "SELECT value FROM archive_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            if had_tables and not had_meta:
                # Pre-migration file: its rows sit on the host wall clock.
                self._schema_version = 1
                self._time_base = TIME_BASE_WALL
                logger.warning(
                    "TM archive %s predates the sim-time migration: its "
                    "utc_epoch column holds HOST WALL CLOCK, not sim time. "
                    "Marking it schema_version=1 / time_base=wall. Old rows "
                    "cannot be correlated with on-board CUC and will not line "
                    "up with new sim-time rows — start a fresh archive file "
                    "for a sim-time session.", self._db_path)
            else:
                self._schema_version = SCHEMA_VERSION
                self._time_base = TIME_BASE_SIM
            self._conn.executemany(
                "INSERT OR REPLACE INTO archive_meta (key, value) VALUES (?, ?)",
                [("schema_version", str(self._schema_version)),
                 ("time_base", self._time_base)])
        else:
            self._schema_version = int(row[0])
            base = self._conn.execute(
                "SELECT value FROM archive_meta WHERE key = 'time_base'"
            ).fetchone()
            self._time_base = str(base[0]) if base else TIME_BASE_WALL
            if self.legacy_time_base:
                logger.warning(
                    "TM archive %s is schema_version=%d / time_base=%s — its "
                    "timestamps are NOT sim time.",
                    self._db_path, self._schema_version, self._time_base)
        self._conn.commit()
        logger.info("TM archive opened: %s (schema v%d, time_base=%s)",
                    self._db_path, self._schema_version, self._time_base)

    def meta(self) -> dict[str, Any]:
        """Provenance an API response can hand to the operator verbatim."""
        return {
            "schema_version": self._schema_version,
            "time_base": self._time_base,
            "sim_time": self._time_base == TIME_BASE_SIM,
            "note": ("utc_epoch is SIM TIME (absolute UTC of the simulated "
                     "world) as a Unix epoch float"
                     if self._time_base == TIME_BASE_SIM else
                     "LEGACY ARCHIVE: utc_epoch is host WALL CLOCK and cannot "
                     "be correlated with on-board CUC"),
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Parameter storage ─────────────────────────────────────────

    def store_parameters(self, params: dict[str, float], *,
                         utc_epoch: float,
                         obc_cuc: float | None = None) -> None:
        """Store a batch of parameter values.

        ``utc_epoch`` is **required and must be sim time** — see the module
        docstring. ``obc_cuc`` is the full-precision on-board CUC of the
        newest packet behind this batch, so the row can be correlated with
        the spacecraft's own clock.
        """
        if not self._conn or not params:
            return
        t = float(utc_epoch)
        rows = [(t, name, val, obc_cuc) for name, val in params.items()]
        self._conn.executemany(
            "INSERT INTO tm_parameters (utc_epoch, param_name, value, obc_cuc) "
            "VALUES (?, ?, ?, ?)",
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
                    *, utc_epoch: float, obc_cuc: float | None = None) -> None:
        """Archive one S5 event. ``utc_epoch`` is required sim time."""
        if not self._conn:
            return
        self._conn.execute(
            "INSERT INTO tm_events "
            "(utc_epoch, event_id, severity, subsystem, description, obc_cuc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (float(utc_epoch), event_id, severity, subsystem, description,
             obc_cuc),
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
        sql = ("SELECT utc_epoch, event_id, severity, subsystem, description, "
               "obc_cuc FROM tm_events WHERE severity >= ?")
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
             "subsystem": r[3], "description": r[4], "obc_cuc": r[5]}
            for r in reversed(rows)
        ]

    # ── Alarm storage ─────────────────────────────────────────────

    def store_alarm(self, alarm: dict, *, utc_epoch: float) -> int:
        """Store an alarm and return the row id.

        ``utc_epoch`` is required sim time — the alarm's RAISE time. It is
        passed explicitly rather than read out of ``alarm['timestamp']`` so a
        caller cannot archive a wall-clock stamp by forgetting to set one.
        """
        if not self._conn:
            return -1
        cur = self._conn.execute(
            "INSERT INTO tm_alarms "
            "(utc_epoch, severity, subsystem, parameter, value, source, acknowledged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                float(utc_epoch),
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
                      *, utc_epoch: float,
                      sched_cuc: float | None = None) -> None:
        """Archive one uplinked telecommand.

        ``utc_epoch`` is required sim time (when the ground sent it).
        ``sched_cuc`` is the on-board CUC the command was time-tagged for,
        where one exists (S11.4 inserts and the commands embedded in them);
        ``None`` for an immediate telecommand.
        """
        if not self._conn:
            return
        self._conn.execute(
            "INSERT INTO tc_log "
            "(utc_epoch, seq, name, service, subtype, data_hex, position, "
            " state, sched_cuc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (float(utc_epoch), seq, name, service, subtype, data_hex,
             position, state, sched_cuc),
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

    def query_commands(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Command history, newest last (same ordering as query_alarms)."""
        if not self._conn:
            return []
        sql = ("SELECT id, utc_epoch, seq, name, service, subtype, data_hex, "
               "position, state, sched_cuc FROM tc_log WHERE 1=1")
        args: list[Any] = []
        if start_epoch is not None:
            sql += " AND utc_epoch >= ?"
            args.append(start_epoch)
        if end_epoch is not None:
            sql += " AND utc_epoch <= ?"
            args.append(end_epoch)
        sql += " ORDER BY utc_epoch DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = self._conn.execute(sql, args).fetchall()
        return [
            {"id": r[0], "utc_epoch": r[1], "seq": r[2], "name": r[3],
             "service": r[4], "subtype": r[5], "data_hex": r[6],
             "position": r[7], "state": r[8], "sched_cuc": r[9]}
            for r in reversed(rows)
        ]

    def parameter_names(self, limit: int = 2000) -> list[str]:
        """Distinct parameter names present in the archive.

        The trend page needs the list of what can actually be plotted; the
        archive stores whatever `_archive_state_snapshot` saw, which is a
        superset-by-alias of `parameters.yaml` (the UI key map renames some).
        """
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT DISTINCT param_name FROM tm_parameters "
            "ORDER BY param_name LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    # ── Playback support ──────────────────────────────────────────

    def get_playback_data(
        self,
        subsystem: str,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 3600,
        *,
        now_epoch: float,
    ) -> list[dict]:
        """Return archived parameter data for a subsystem (for S15 playback).

        Queries all parameters whose name starts with ``subsystem.`` prefix.
        ``now_epoch`` (required sim time) is the "end of the window" default —
        it must come from the same clock the rows were written on.
        """
        if not self._conn:
            return []
        prefix = f"{subsystem}.%"
        t_end = end_epoch if end_epoch is not None else float(now_epoch)
        t_start = start_epoch if start_epoch is not None else (t_end - 3600)
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

    def prune(self, *, now_epoch: float, max_age_s: float = 604_800) -> int:
        """Delete records older than *max_age_s* seconds (default 7 days).

        ``now_epoch`` is required and must be on the SAME clock the rows were
        written on (sim time). Pruning a sim-time archive against the wall
        clock would delete the entire file on the first call whenever the sim
        epoch is in the past, and never delete anything when it is in the
        future — which is exactly what the old ``time.time()`` default did.
        """
        if not self._conn:
            return 0
        cutoff = float(now_epoch) - max_age_s
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
