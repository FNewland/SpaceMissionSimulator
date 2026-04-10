"""SMO Simulator — Time-tagged TC Scheduler (S11).

Queues TC packets for future execution at specified CUC times.
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class TCScheduler:
    """S11 Time-tagged command scheduler."""

    def __init__(self):
        self._lock = threading.Lock()
        self._commands: dict[int, dict] = {}
        self._next_id = 1
        self._enabled = True

    def insert(self, execution_time_cuc: int, tc_packet: bytes) -> int:
        """Insert a time-tagged TC. Returns command ID."""
        with self._lock:
            cmd_id = self._next_id
            self._next_id += 1
            self._commands[cmd_id] = {
                'id': cmd_id,
                'exec_time': execution_time_cuc,
                'packet': tc_packet,
            }
            logger.info("Scheduled TC #%d at CUC %d", cmd_id, execution_time_cuc)
            return cmd_id

    def delete(self, cmd_id: int) -> bool:
        """Delete a scheduled command by ID."""
        with self._lock:
            if cmd_id in self._commands:
                del self._commands[cmd_id]
                return True
            return False

    def delete_all(self) -> None:
        """Delete all scheduled commands."""
        with self._lock:
            self._commands.clear()

    def list_commands(self) -> list[dict]:
        """Return list of scheduled commands (without raw packets)."""
        with self._lock:
            return [
                {'id': c['id'], 'exec_time': c['exec_time']}
                for c in sorted(self._commands.values(), key=lambda c: c['exec_time'])
            ]

    def enable_schedule(self) -> None:
        with self._lock:
            self._enabled = True

    def disable_schedule(self) -> None:
        with self._lock:
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def tick(self, current_cuc: int) -> list[bytes]:
        """Check for due commands and return their TC packets.

        Args:
            current_cuc: Current CUC time in seconds.

        Returns:
            List of TC packets that are due for execution.
        """
        with self._lock:
            if not self._enabled:
                return []

            due = []
            expired_ids = []
            for cmd_id, cmd in self._commands.items():
                if cmd['exec_time'] <= current_cuc:
                    due.append(cmd['packet'])
                    expired_ids.append(cmd_id)

            for cmd_id in expired_ids:
                del self._commands[cmd_id]

            if due:
                logger.info("Executing %d time-tagged commands at CUC %d", len(due), current_cuc)

            return due

    def get_status(self) -> dict:
        """Return scheduler status summary."""
        with self._lock:
            return {
                'enabled': self._enabled,
                'count': len(self._commands),
                'next_id': self._next_id,
            }
