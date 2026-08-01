"""SMO Simulator — Time-tagged TC Scheduler (S11).

Queues TC packets for future execution at specified CUC times.

Each entry caches the *identity* of the deferred command (APID, service,
subtype, the leading application-data bytes) at insert time, decommutated once
from the embedded packet. Without it the on-board schedule report TM(11,18)
carried only ``(id, exec_time)`` and an operator had no way to find out which
command was queued on board (defect #72).
"""
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# How many bytes of the embedded command's application data are cached and
# reported. Enough to resolve an S8 func_id (byte 0) plus its arguments, which
# is what the ground needs to render a mnemonic.
DETAIL_DATA_BYTES = 8


def _decommutate_identity(tc_packet: bytes) -> dict:
    """Extract (apid, service, subtype, data) from an embedded CCSDS TC.

    Returns zeroed fields if the packet cannot be parsed — a malformed embedded
    packet must not stop the command being scheduled, it just cannot be
    described to the operator.
    """
    identity = {'apid': 0, 'service': 0, 'subtype': 0,
                'data_len': 0, 'data': b''}
    try:
        from smo_common.protocol.ecss_packet import decommutate_packet
        parsed = decommutate_packet(tc_packet)
        if parsed is None:
            return identity
        primary = getattr(parsed, 'primary', None)
        secondary = getattr(parsed, 'secondary', None)
        if primary is not None:
            identity['apid'] = int(getattr(primary, 'apid', 0) or 0)
        if secondary is not None:
            identity['service'] = int(getattr(secondary, 'service', 0) or 0)
            identity['subtype'] = int(getattr(secondary, 'subtype', 0) or 0)
        data = getattr(parsed, 'data_field', b'') or b''
        identity['data_len'] = len(data)
        identity['data'] = bytes(data[:DETAIL_DATA_BYTES])
    except Exception as e:  # noqa: BLE001 — identity is best-effort metadata
        logger.debug("S11: could not decommutate embedded TC (%s)", e)
    return identity


class TCScheduler:
    """S11 Time-tagged command scheduler."""

    def __init__(self):
        self._lock = threading.Lock()
        self._commands: dict[int, dict] = {}
        self._next_id = 1
        self._enabled = True

    def insert(self, execution_time_cuc: int, tc_packet: bytes) -> int:
        """Insert a time-tagged TC. Returns command ID."""
        identity = _decommutate_identity(tc_packet)
        with self._lock:
            cmd_id = self._next_id
            self._next_id += 1
            self._commands[cmd_id] = {
                'id': cmd_id,
                'exec_time': execution_time_cuc,
                'packet': tc_packet,
                **identity,
            }
            logger.info("Scheduled TC #%d at CUC %d (S%d.%d apid=%d)",
                        cmd_id, execution_time_cuc,
                        identity['service'], identity['subtype'],
                        identity['apid'])
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
        """Return the scheduled commands, each with its command identity.

        The raw packet stays on board (it is what actually executes); what is
        returned is everything the ground needs to render a real command list:
        ``id``, ``exec_time`` (CUC), ``apid``, ``service``, ``subtype``,
        ``data_len`` and the first ``DETAIL_DATA_BYTES`` of application data
        (which carries the S8 ``func_id`` and its arguments).
        """
        with self._lock:
            return [
                {
                    'id': c['id'],
                    'exec_time': c['exec_time'],
                    'apid': c.get('apid', 0),
                    'service': c.get('service', 0),
                    'subtype': c.get('subtype', 0),
                    'data_len': c.get('data_len', 0),
                    'data': c.get('data', b''),
                }
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

    # ─── State save/restore (plan A-4) ───────────────────────────────
    # A breakpoint load used to destroy the whole on-board S11 schedule
    # (brief 08 B-2) — the scheduler had no state methods at all.

    def get_state(self) -> dict:
        """Serialise the complete S11 schedule for breakpoint save."""
        with self._lock:
            return {
                'enabled': self._enabled,
                'next_id': self._next_id,
                'commands': [
                    {
                        'id': c['id'],
                        'exec_time': c['exec_time'],
                        'packet': bytes(c['packet']).hex(),
                        'apid': c.get('apid', 0),
                        'service': c.get('service', 0),
                        'subtype': c.get('subtype', 0),
                        'data_len': c.get('data_len', 0),
                        'data': bytes(c.get('data', b'')).hex(),
                    }
                    for c in self._commands.values()
                ],
            }

    def set_state(self, state: dict) -> None:
        """Restore the S11 schedule from a breakpoint."""
        with self._lock:
            self._enabled = bool(state.get('enabled', True))
            self._next_id = int(state.get('next_id', 1))
            self._commands = {}
            for c in state.get('commands', []):
                cmd_id = int(c['id'])
                self._commands[cmd_id] = {
                    'id': cmd_id,
                    'exec_time': int(c['exec_time']),
                    'packet': bytes.fromhex(c['packet']),
                    'apid': int(c.get('apid', 0)),
                    'service': int(c.get('service', 0)),
                    'subtype': int(c.get('subtype', 0)),
                    'data_len': int(c.get('data_len', 0)),
                    'data': bytes.fromhex(c.get('data', '')),
                }
