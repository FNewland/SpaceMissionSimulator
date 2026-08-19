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

CONCURRENCY AND FILE INTEGRITY — WHY ``database disk image is malformed``
------------------------------------------------------------------------
The MCS writes this file from the request path (an S15 dump request, an alarm
acknowledgement, a command log) *and* from the 1 Hz snapshot/prune path, and
serves aiohttp routes some of which run in a thread executor.  The connection
is a single handle opened ``check_same_thread=False``.  Three things were
missing and each of them can end in a damaged file:

* **No mutex.**  ``check_same_thread=False`` disables sqlite3's own guard
  without putting anything in its place.  Two threads stepping the same
  ``sqlite3.Connection`` interleave inside one C-level statement.
* **No busy timeout.**  A second writer got ``database is locked`` instantly
  and the caller's ``except Exception`` swallowed it mid-write.
* **WAL was requested but never verified.**  ``PRAGMA journal_mode=WAL``
  *returns the mode actually in force*.  On a Dropbox/iCloud/OneDrive folder
  or an NFS/SMB mount — which is exactly where ``configs/data`` tends to live
  — WAL needs a shared-memory ``-shm`` file and working POSIX byte-range
  locks.  Where it cannot get them SQLite silently stays on the rollback
  journal, and the sync client copying a half-written ``.db`` out from under
  the process is a first-class way to produce ``database disk image is
  malformed``.

:meth:`TMArchive.open` now takes a re-entrant lock around every use of the
connection, sets a busy timeout, checks what journal mode it actually got (and
falls back to ``TRUNCATE`` + ``synchronous=FULL``, which is safe on a synced
folder, when it is not WAL), and runs ``PRAGMA quick_check`` before trusting
the file.

DEGRADED MODE
-------------
An unreadable archive must not take out a display.  Any :class:`sqlite3.Error`
at open, or during a query, moves the archive to a **read-unavailable** state:
it is logged loudly once, :attr:`available` goes False, :meth:`status` says
which of the four distinguishable conditions it is, and every accessor returns
an empty result instead of raising.  The API layer turns that into an
explicit, operator-legible banner rather than letting a driver string like
``database disk image is malformed`` reach a panel.

The four states are deliberately distinct — an operator has to be able to tell
"you have no history because this is a fresh file" from "your history is
unreadable":

``ok``           v2 sim-time archive, readable
``new``          no file existed; an empty one was created for this session
``legacy``       v1 wall-clock file: readable, but not correlatable
``corrupt``      SQLite reports the file itself is damaged
``unavailable``  the file could not be opened at all (locking, permissions)

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
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Current archive schema. Bumped from 1 when ``utc_epoch`` moved from host
#: wall clock to sim time (spec Part D.4).
SCHEMA_VERSION = 3

#: What ``utc_epoch`` means in a version-2 archive.
TIME_BASE_SIM = "sim"
#: What it meant in a version-1 archive.
TIME_BASE_WALL = "wall"

#: Archive states an operator has to be able to tell apart. See the module
#: docstring — "no history" and "unreadable history" are not the same thing.
STATUS_OK = "ok"
STATUS_NEW = "new"
STATUS_LEGACY = "legacy"
STATUS_CORRUPT = "corrupt"
STATUS_UNAVAILABLE = "unavailable"

#: States in which reads return data.
_READABLE = (STATUS_OK, STATUS_NEW, STATUS_LEGACY)

#: How long a writer waits for another writer's lock before giving up. Without
#: this SQLite fails instantly with "database is locked" the moment the
#: request path and the background prune overlap.
BUSY_TIMEOUT_S = 10.0

#: Substrings SQLite uses for real file damage, as opposed to a transient
#: lock or a programming error.
_CORRUPTION_MARKERS = (
    "malformed",            # "database disk image is malformed"
    "not a database",       # "file is not a database"
    "encrypted",            # "file is encrypted or is not a database"
    "corrupt",
    "database disk image",
)


def _looks_corrupt(exc: BaseException) -> bool:
    """True when *exc* says the FILE is damaged, not that we misused it."""
    if isinstance(exc, sqlite3.DatabaseError):
        text = str(exc).lower()
        return any(m in text for m in _CORRUPTION_MARKERS)
    return False

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

-- Console / mission log (schema v3, POSITION_NEEDS G8). APPEND-ONLY: rows
-- are inserted and read, never updated or deleted (prune() does not touch
-- this table — a decision record must outlive the telemetry that produced
-- it). utc_epoch is SIM TIME like every other table in this file; the
-- archive_meta time_base declaration covers it too.
CREATE TABLE IF NOT EXISTS console_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    utc_epoch   REAL    NOT NULL,   -- SIM TIME (schema v2 rules)
    position    TEXT    DEFAULT '', -- console position the entry belongs to
    kind        TEXT    NOT NULL,   -- tc|ack|shelve|procedure|gonogo|mark|note
    text        TEXT    NOT NULL,
    ref         TEXT    DEFAULT '', -- machine ref, e.g. "seq:42", "alarm:7"
    source      TEXT    DEFAULT 'operator'  -- operator | server
);

CREATE INDEX IF NOT EXISTS idx_console_log_utc ON console_log (utc_epoch);
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
        # Every touch of `_conn` is taken under this lock. The MCS drives the
        # archive from the aiohttp request path, the 1 Hz snapshot/prune path
        # and (for the report routes) a thread executor; `check_same_thread=
        # False` turns sqlite3's own guard off without replacing it.
        self._lock = threading.RLock()
        self._status: str = STATUS_OK
        self._error: str = ""
        self._journal_mode: str = ""
        self._degraded_logged = False

    # ── Lifecycle ─────────────────────────────────────────────────

    @property
    def schema_version(self) -> int:
        """Schema version of the OPEN file (1 = legacy wall-clock rows)."""
        return self._schema_version

    @property
    def status(self) -> str:
        """One of ``ok`` / ``new`` / ``legacy`` / ``corrupt`` / ``unavailable``."""
        return self._status

    @property
    def available(self) -> bool:
        """True when reads return real rows rather than a degraded empty set."""
        return self._status in _READABLE and self._conn is not None

    @property
    def error(self) -> str:
        """The driver's own text, for the log — never for a display."""
        return self._error

    @property
    def journal_mode(self) -> str:
        """Journal mode actually in force (``wal`` unless the FS refused it)."""
        return self._journal_mode

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

    # ── Connection setup ──────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Open the handle with the pragmas this deployment actually needs.

        ``PRAGMA journal_mode=WAL`` RETURNS the mode in force. On a synced
        folder (Dropbox/iCloud/OneDrive) or a network mount, WAL cannot get
        the shared-memory file and byte-range locks it needs and SQLite quietly
        stays on the rollback journal — the configuration most likely to end
        in "database disk image is malformed" when the sync client copies the
        file mid-write. If we did not get WAL we say so loudly and switch to
        TRUNCATE + synchronous=FULL, which is the durable choice there.
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False,
                               timeout=BUSY_TIMEOUT_S)
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
        got = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(got[0]).lower() if got else ""
        if mode != "wal":
            logger.error(
                "TM archive %s could NOT be put into WAL mode (SQLite gave "
                "'%s'). That normally means the file lives on a synced folder "
                "or a network mount whose locking SQLite cannot rely on — the "
                "classic route to a corrupt archive. Falling back to "
                "journal_mode=TRUNCATE / synchronous=FULL. Move the archive to "
                "local, non-synced storage.", self._db_path, mode or "?")
            conn.execute("PRAGMA journal_mode=TRUNCATE")
            conn.execute("PRAGMA synchronous=FULL")
            got = conn.execute("PRAGMA journal_mode").fetchone()
            mode = str(got[0]).lower() if got else ""
        else:
            conn.execute("PRAGMA synchronous=NORMAL")
        self._journal_mode = mode
        return conn

    def _integrity_ok(self, conn: sqlite3.Connection) -> tuple[bool, str]:
        """``PRAGMA quick_check`` — cheap, and catches a torn page.

        Run before we trust an existing file, so corruption surfaces at open
        (where we can degrade cleanly and log it) rather than in the middle of
        an operator's S15 dump request.
        """
        row = conn.execute("PRAGMA quick_check(1)").fetchone()
        verdict = str(row[0]) if row else "no result"
        return verdict.lower() == "ok", verdict

    def _degrade(self, status: str, exc: BaseException | None,
                 detail: str = "") -> None:
        """Enter read-unavailable mode. Loud in the log, quiet at the API."""
        self._status = status
        self._error = detail or (str(exc) if exc else "")
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001 — already failing
            pass
        self._conn = None
        if not self._degraded_logged:
            self._degraded_logged = True
            logger.error(
                "TM ARCHIVE UNUSABLE (%s): %s — path=%s. The archive is now in "
                "READ-UNAVAILABLE mode: stored-TM playback, trends and archived "
                "command history will report no data for this session. Every "
                "other display keeps working. Move or delete the file and "
                "restart the MCS to start a fresh archive.",
                status, self._error, self._db_path)

    def open(self) -> None:
        """Open (or create) the database and ensure the schema is present.

        Resolves three things, and never raises:

        * the file's **time base** — a database that already has archive
          tables but no ``archive_meta`` row was written before the sim-time
          migration, so its ``utc_epoch`` values are host wall clock. It is
          stamped version 1 and reported as such rather than quietly read as
          sim time;
        * whether the file is **intact** (``PRAGMA quick_check``);
        * whether it could be opened at all.

        A corrupt or unopenable file puts the archive into read-unavailable
        mode instead of propagating a ``sqlite3`` exception up into whatever
        panel happened to ask first.
        """
        self._status = STATUS_OK
        self._error = ""
        self._degraded_logged = False
        first_open = not Path(self._db_path).exists()
        try:
            self._conn = self._connect()
        except sqlite3.Error as e:
            self._degrade(STATUS_CORRUPT if _looks_corrupt(e)
                          else STATUS_UNAVAILABLE, e)
            return
        except OSError as e:
            self._degrade(STATUS_UNAVAILABLE, e)
            return

        try:
            with self._lock:
                if not first_open:
                    ok, verdict = self._integrity_ok(self._conn)
                    if not ok:
                        self._degrade(STATUS_CORRUPT, None,
                                      f"integrity check failed: {verdict}")
                        return
                self._open_schema(first_open)
        except sqlite3.Error as e:
            self._degrade(STATUS_CORRUPT if _looks_corrupt(e)
                          else STATUS_UNAVAILABLE, e)
            return

        logger.info("TM archive opened: %s (schema v%d, time_base=%s, "
                    "journal_mode=%s, status=%s)",
                    self._db_path, self._schema_version, self._time_base,
                    self._journal_mode, self._status)

    def _open_schema(self, first_open: bool) -> None:
        """Create/patch the schema and settle schema_version + time_base."""
        assert self._conn is not None
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
                self._status = STATUS_LEGACY
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
                self._status = STATUS_NEW if first_open else STATUS_OK
                if first_open:
                    logger.info(
                        "No TM archive existed at %s — created a new, EMPTY "
                        "one. There is no telemetry history before this "
                        "session; trends and S15 playback will fill up as the "
                        "session runs.", self._db_path)
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
            # v2 → v3 is purely ADDITIVE (the console_log table, created by
            # the executescript above), so an existing sim-time v2 file is
            # upgraded in place: same rows, same time base, one new empty
            # table. A v1 wall-clock file is NOT upgraded — its problem is
            # the time base, not the table set.
            if (self._schema_version == 2
                    and self._time_base == TIME_BASE_SIM):
                self._schema_version = SCHEMA_VERSION
                self._conn.execute(
                    "INSERT OR REPLACE INTO archive_meta (key, value) "
                    "VALUES ('schema_version', ?)",
                    (str(self._schema_version),))
                logger.info(
                    "TM archive %s upgraded schema v2 -> v3 (additive: "
                    "console_log table)", self._db_path)
            if self.legacy_time_base:
                self._status = STATUS_LEGACY
                logger.warning(
                    "TM archive %s is schema_version=%d / time_base=%s — its "
                    "timestamps are NOT sim time.",
                    self._db_path, self._schema_version, self._time_base)
            else:
                self._status = STATUS_OK
        self._conn.commit()

    #: One sentence per state, written for an operator, not for a developer.
    #: These are what a panel shows; the driver's own text never leaves the log.
    _MESSAGES = {
        STATUS_OK: ("Telemetry archive online (schema v{v}, sim time). "
                    "utc_epoch is SIM TIME (absolute UTC of the simulated "
                    "world) as a Unix epoch float."),
        STATUS_NEW: ("Telemetry archive was EMPTY at startup — no archive file "
                     "existed, so a new one was created. There is no history "
                     "from before this session; it fills as the session runs."),
        STATUS_LEGACY: ("LEGACY ARCHIVE (schema v{v}): utc_epoch is host WALL "
                        "CLOCK and cannot be correlated with on-board CUC. Its "
                        "rows will not line up with this session's sim-time "
                        "rows. Start a fresh archive file for a sim-time "
                        "session."),
        STATUS_CORRUPT: ("TELEMETRY ARCHIVE CORRUPT — the archive file is "
                         "damaged and cannot be read. Stored-TM playback, "
                         "trends and archived command history are UNAVAILABLE "
                         "for this session; live telemetry is unaffected. Move "
                         "or delete the archive file and restart the MCS."),
        STATUS_UNAVAILABLE: ("TELEMETRY ARCHIVE UNAVAILABLE — the archive file "
                             "could not be opened (permissions, or a "
                             "filesystem whose locking SQLite cannot use). "
                             "Stored-TM playback, trends and archived command "
                             "history are UNAVAILABLE for this session; live "
                             "telemetry is unaffected."),
    }

    def message(self) -> str:
        """The one sentence a panel is allowed to show about the archive."""
        return self._MESSAGES.get(
            self._status, self._MESSAGES[STATUS_UNAVAILABLE]
        ).format(v=self._schema_version)

    def meta(self) -> dict[str, Any]:
        """Provenance an API response can hand to the operator verbatim."""
        return {
            "schema_version": self._schema_version,
            "time_base": self._time_base,
            "sim_time": (self._time_base == TIME_BASE_SIM
                         and self._status in _READABLE),
            "status": self._status,
            "available": self.available,
            "readable": self.available,
            "journal_mode": self._journal_mode,
            "message": self.message(),
            # `note` predates `message` and several callers read it; keep the
            # LEGACY/SIM wording it has always carried, and let a degraded
            # archive say so here too.
            "note": (self.message() if self._status not in _READABLE else
                     ("utc_epoch is SIM TIME (absolute UTC of the simulated "
                      "world) as a Unix epoch float"
                      if self._time_base == TIME_BASE_SIM else
                      "LEGACY ARCHIVE: utc_epoch is host WALL CLOCK and cannot "
                      "be correlated with on-board CUC")),
        }

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except sqlite3.Error as e:  # noqa: BLE001
                    logger.debug("Archive close failed: %s", e)
                self._conn = None

    # ── Guarded connection access ─────────────────────────────────

    def _fail(self, op: str, exc: sqlite3.Error) -> None:
        """A statement blew up mid-session: classify it and degrade."""
        if _looks_corrupt(exc):
            self._degrade(STATUS_CORRUPT, exc)
        else:
            # Not file damage (a locked database, a disk-full). Do not condemn
            # the file, but do not let the driver text out either.
            logger.warning("TM archive %s failed: %s", op, exc)

    def _query(self, op: str, sql: str, args: Any = ()) -> list[tuple]:
        """Run a SELECT. Returns [] rather than raising when degraded."""
        with self._lock:
            if not self.available:
                return []
            try:
                return self._conn.execute(sql, args).fetchall()
            except sqlite3.Error as e:
                self._fail(op, e)
                return []

    def _execute(self, op: str, sql: str, args: Any = ()) -> sqlite3.Cursor | None:
        """Run one statement and commit. Returns None rather than raising."""
        with self._lock:
            if not self.available:
                return None
            try:
                cur = self._conn.execute(sql, args)
                self._conn.commit()
                return cur
            except sqlite3.Error as e:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                self._fail(op, e)
                return None

    def _execute_many(self, op: str, sql: str, rows: list) -> None:
        with self._lock:
            if not self.available:
                return
            try:
                self._conn.executemany(sql, rows)
                self._conn.commit()
            except sqlite3.Error as e:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                self._fail(op, e)

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
        if not params:
            return
        t = float(utc_epoch)
        rows = [(t, name, val, obc_cuc) for name, val in params.items()]
        self._execute_many(
            "store_parameters",
            "INSERT INTO tm_parameters (utc_epoch, param_name, value, obc_cuc) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )

    def query_parameters(
        self,
        param_name: str,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 3600,
    ) -> list[dict]:
        """Retrieve time-series for a single parameter."""
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
        rows = self._query("query_parameters", sql, args)
        return [{"utc_epoch": r[0], "value": r[1]} for r in reversed(rows)]

    # ── Event storage ─────────────────────────────────────────────

    def store_event(self, event_id: int, severity: int,
                    subsystem: str = "", description: str = "",
                    *, utc_epoch: float, obc_cuc: float | None = None) -> None:
        """Archive one S5 event. ``utc_epoch`` is required sim time."""
        self._execute(
            "store_event",
            "INSERT INTO tm_events "
            "(utc_epoch, event_id, severity, subsystem, description, obc_cuc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (float(utc_epoch), event_id, severity, subsystem, description,
             obc_cuc),
        )

    def query_events(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        severity_min: int = 0,
        limit: int = 500,
    ) -> list[dict]:
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
        rows = self._query("query_events", sql, args)
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
        cur = self._execute(
            "store_alarm",
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
        return (cur.lastrowid or -1) if cur is not None else -1

    def acknowledge_alarm(self, alarm_db_id: int) -> None:
        self._execute(
            "acknowledge_alarm",
            "UPDATE tm_alarms SET acknowledged = 1 WHERE id = ?",
            (alarm_db_id,),
        )

    def query_alarms(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
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
        rows = self._query("query_alarms", sql, args)
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
        self._execute(
            "store_command",
            "INSERT INTO tc_log "
            "(utc_epoch, seq, name, service, subtype, data_hex, position, "
            " state, sched_cuc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (float(utc_epoch), seq, name, service, subtype, data_hex,
             position, state, sched_cuc),
        )

    def update_command_state(self, seq: int, state: str) -> None:
        self._execute(
            "update_command_state",
            "UPDATE tc_log SET state = ? WHERE seq = ? "
            "AND id = (SELECT MAX(id) FROM tc_log WHERE seq = ?)",
            (state, seq, seq),
        )

    def query_commands(
        self,
        start_epoch: float | None = None,
        end_epoch: float | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Command history, newest last (same ordering as query_alarms)."""
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
        rows = self._query("query_commands", sql, args)
        return [
            {"id": r[0], "utc_epoch": r[1], "seq": r[2], "name": r[3],
             "service": r[4], "subtype": r[5], "data_hex": r[6],
             "position": r[7], "state": r[8], "sched_cuc": r[9]}
            for r in reversed(rows)
        ]

    # ── Console / mission log (schema v3, POSITION_NEEDS G8) ──────

    #: The closed entry-kind vocabulary. `tc` = telecommand sent / verdict,
    #: `ack`/`shelve` = alarm handling, `procedure` = procedure lifecycle,
    #: `gonogo` = GO/NO-GO poll opened or resolved, `mark` = the book's
    #: "Mark" callout (a timestamped pointer), `note` = free text.
    CONSOLE_LOG_KINDS = ("tc", "ack", "shelve", "procedure", "gonogo",
                        "mark", "note")

    def store_console_entry(self, *, utc_epoch: float, kind: str,
                            text: str, position: str = "",
                            ref: str = "", source: str = "operator") -> int:
        """Append one console-log entry. Returns the row id (0 if degraded).

        ``utc_epoch`` is **sim time**, same clock as every other table.
        ``kind`` must be one of :data:`CONSOLE_LOG_KINDS`.
        """
        if kind not in self.CONSOLE_LOG_KINDS:
            raise ValueError(f"console-log kind {kind!r} not in "
                             f"{self.CONSOLE_LOG_KINDS}")
        cur = self._execute(
            "store_console_entry",
            "INSERT INTO console_log (utc_epoch, position, kind, text, ref, "
            "source) VALUES (?, ?, ?, ?, ?, ?)",
            (float(utc_epoch), str(position), str(kind), str(text),
             str(ref), str(source)),
        )
        return int(cur.lastrowid) if cur is not None else 0

    def query_console_log(self, since_epoch: float | None = None,
                          until_epoch: float | None = None,
                          limit: int = 1000) -> list[dict]:
        """Console-log entries, oldest first (append order)."""
        sql = ("SELECT id, utc_epoch, position, kind, text, ref, source "
               "FROM console_log WHERE 1=1")
        args: list[Any] = []
        if since_epoch is not None:
            sql += " AND utc_epoch >= ?"
            args.append(float(since_epoch))
        if until_epoch is not None:
            sql += " AND utc_epoch <= ?"
            args.append(float(until_epoch))
        sql += " ORDER BY utc_epoch DESC, id DESC LIMIT ?"
        args.append(limit)
        rows = self._query("query_console_log", sql, args)
        return [
            {"id": r[0], "utc_epoch": r[1], "position": r[2], "kind": r[3],
             "text": r[4], "ref": r[5], "source": r[6]}
            for r in reversed(rows)
        ]

    def parameter_names(self, limit: int = 2000) -> list[str]:
        """Distinct parameter names present in the archive.

        The trend page needs the list of what can actually be plotted; the
        archive stores whatever `_archive_state_snapshot` saw, which is a
        superset-by-alias of `parameters.yaml` (the UI key map renames some).
        """
        rows = self._query(
            "parameter_names",
            "SELECT DISTINCT param_name FROM tm_parameters "
            "ORDER BY param_name LIMIT ?", (limit,))
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
        prefix = f"{subsystem}.%"
        t_end = end_epoch if end_epoch is not None else float(now_epoch)
        t_start = start_epoch if start_epoch is not None else (t_end - 3600)
        rows = self._query(
            "get_playback_data",
            "SELECT utc_epoch, param_name, value FROM tm_parameters "
            "WHERE param_name LIKE ? AND utc_epoch BETWEEN ? AND ? "
            "ORDER BY utc_epoch LIMIT ?",
            (prefix, t_start, t_end, limit),
        )

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

        The prune is the archive's other WRITER, and it runs from the 1 Hz
        loop while the request path may be inserting. It takes the same lock
        as every other statement and runs its four DELETEs as ONE transaction,
        so a failure half way through rolls back instead of leaving the file
        partially pruned.
        """
        cutoff = float(now_epoch) - max_age_s
        with self._lock:
            if not self.available:
                return 0
            n = 0
            try:
                # sqlite3's default isolation_level opens the transaction on
                # the first DELETE, so the four DELETEs and the commit below
                # are one unit: a failure part way through rolls back.
                for table in ("tm_parameters", "tm_events",
                              "tm_alarms", "tc_log"):
                    cur = self._conn.execute(
                        f"DELETE FROM {table} WHERE utc_epoch < ?", (cutoff,)
                    )
                    n += cur.rowcount
                self._conn.commit()
            except sqlite3.Error as e:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                self._fail("prune", e)
                return 0
        if n:
            logger.info("Pruned %d archive rows older than %.0f s", n, max_age_s)
        return n
