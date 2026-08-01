"""SMO Simulator — PUS Service 18 On-Board Control Procedures (OBCP).

Minimal but real ECSS-flavoured on-board scripting. An OBCP is an ordered
list of steps; each step is a relative delay (seconds of *sim time*) plus a
complete PUS TC packet that the spacecraft executes on itself when the delay
expires. Self-generated TCs are routed through the engine's normal internal
dispatch path (``engine._dispatch_tc``) so all acceptance rules — bootloader
gate, power gate, subtype validation, S1 verification — apply exactly as for
ground-uplinked TCs. Executions are tagged ``source=OBCP`` in the log.

TC interface (handled by ServiceDispatcher._handle_s18):
  TC(18,1)  direct-load procedure   proc_id(2) + name_len(1) + name +
                                    n_steps(1) + [delay_s(f32) + tc_len(2) + tc]*
  TC(18,2)  delete procedure        proc_id(2)
  TC(18,3)  activate (start)        proc_id(2) [+ param(1)]
  TC(18,4)  stop (abort)            proc_id(2)   (0 = stop all)
  TC(18,6)  report status           →  TM(18,7)  count(1) +
                                       [proc_id(2)+status(1)+step(1)+n_steps(1)]*

Events (via engine._emit_event → S5 TM + S19 triggering):
  0x0700 OBCP_STARTED   0x0701 OBCP_COMPLETED
  0x0702 OBCP_ABORTED   0x0703 OBCP_STEP_FAILED

Boot-time procedures are pre-loaded from ``configs/<mission>/obcp/*.yaml``
(see docs/OBCP_GUIDE.md for the on-disk format), mirroring the S19-rules
config-driven store, so students can activate procedures immediately.

State survives breakpoints: ``get_state``/``set_state`` are wired into
BreakpointManager exactly like OnboardTMStorage.
"""
import binascii
import logging
import struct
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# OBCP execution status codes (TM(18,7) status field)
OBCP_STATUS_IDLE = 0      # loaded, not executing
OBCP_STATUS_RUNNING = 1   # activated, stepping on sim time

# S5 event IDs (0x0700-0x070F reserved for OBCP — see event_catalog.yaml)
EVENT_OBCP_STARTED = 0x0700
EVENT_OBCP_COMPLETED = 0x0701
EVENT_OBCP_ABORTED = 0x0702
EVENT_OBCP_STEP_FAILED = 0x0703

MAX_PROCEDURES = 16
MAX_STEPS = 32


class ObcpManager:
    """On-board control procedure store + per-tick interpreter.

    Ticked by the engine main loop with sim-time dt (like TCScheduler), so
    running procedures automatically respect the instructor's sim speed.
    """

    def __init__(self, engine):
        # RLock: the S18 handler runs inside ServiceDispatcher's lock; step
        # execution happens outside our lock (see tick) to avoid lock-order
        # inversion between manager and dispatcher.
        self._lock = threading.RLock()
        self._engine = engine
        # proc_id -> {'name': str, 'steps': [{'delay_s': float, 'tc': bytes}]}
        self._procedures: dict[int, dict] = {}
        # proc_id -> {'step_index': int, 'wait_remaining': float, 'param': int}
        self._running: dict[int, dict] = {}

    # ── store management ──────────────────────────────────────────────

    def load(self, proc_id: int, name: str, steps: list[dict]) -> bool:
        """Load (or replace) a procedure. Steps: [{'delay_s', 'tc'(bytes)}]."""
        if not steps or len(steps) > MAX_STEPS:
            logger.warning("OBCP %d load rejected: %d steps (1..%d allowed)",
                           proc_id, len(steps) if steps else 0, MAX_STEPS)
            return False
        with self._lock:
            if proc_id not in self._procedures and \
                    len(self._procedures) >= MAX_PROCEDURES:
                logger.warning("OBCP %d load rejected: store full (%d)",
                               proc_id, MAX_PROCEDURES)
                return False
            # Replacing a running procedure aborts it first.
            if proc_id in self._running:
                self._abort(proc_id, "replaced by new load")
            self._procedures[proc_id] = {
                'name': str(name),
                'steps': [
                    {'delay_s': float(s['delay_s']), 'tc': bytes(s['tc'])}
                    for s in steps
                ],
            }
        logger.info("OBCP %d '%s' loaded (%d steps)", proc_id, name, len(steps))
        return True

    def delete(self, proc_id: int) -> bool:
        with self._lock:
            if proc_id in self._running:
                self._abort(proc_id, "deleted while running")
            if proc_id in self._procedures:
                del self._procedures[proc_id]
                logger.info("OBCP %d deleted", proc_id)
                return True
            return False

    def start(self, proc_id: int, param: int = 0) -> bool:
        """Activate a loaded procedure. The first step executes once its own
        delay_s has elapsed (delay 0 → next tick)."""
        with self._lock:
            proc = self._procedures.get(proc_id)
            if proc is None:
                logger.warning("OBCP %d start rejected: not loaded", proc_id)
                return False
            if proc_id in self._running:
                logger.warning("OBCP %d start rejected: already running", proc_id)
                return False
            self._running[proc_id] = {
                'step_index': 0,
                'wait_remaining': float(proc['steps'][0]['delay_s']),
                'param': int(param) & 0xFF,
            }
        self._emit(EVENT_OBCP_STARTED, 1,
                   f"OBCP {proc_id} '{proc['name']}' started "
                   f"({len(proc['steps'])} steps, param={param})")
        return True

    def stop(self, proc_id: int) -> bool:
        """Abort a running procedure. proc_id 0 aborts all."""
        with self._lock:
            if proc_id == 0:
                ids = list(self._running.keys())
                for pid in ids:
                    self._abort(pid, "stopped by TC(18,4)")
                return bool(ids)
            if proc_id in self._running:
                self._abort(proc_id, "stopped by TC(18,4)")
                return True
            return False

    def _abort(self, proc_id: int, reason: str) -> None:
        """Internal: remove from running set and emit ABORTED (lock held OK)."""
        self._running.pop(proc_id, None)
        name = self._procedures.get(proc_id, {}).get('name', '?')
        self._emit(EVENT_OBCP_ABORTED, 2,
                   f"OBCP {proc_id} '{name}' aborted: {reason}")

    def get_status(self) -> list[dict]:
        """Status of every loaded procedure (for TM(18,7) and the API)."""
        with self._lock:
            out = []
            for pid in sorted(self._procedures):
                run = self._running.get(pid)
                out.append({
                    'proc_id': pid,
                    'name': self._procedures[pid]['name'],
                    'n_steps': len(self._procedures[pid]['steps']),
                    'status': OBCP_STATUS_RUNNING if run else OBCP_STATUS_IDLE,
                    'current_step': run['step_index'] if run else 0,
                })
            return out

    # ── uplink blob (TC 18,1) serialization ──────────────────────────

    @staticmethod
    def parse_load_blob(data: bytes) -> tuple[int, str, list[dict]]:
        """Parse the TC(18,1) application data. Raises ValueError on error."""
        if len(data) < 4:
            raise ValueError("load blob too short")
        proc_id = struct.unpack('>H', data[:2])[0]
        name_len = data[2]
        off = 3 + name_len
        if len(data) < off + 1:
            raise ValueError("load blob truncated in name")
        name = data[3:off].decode('utf-8', errors='replace')
        n_steps = data[off]
        off += 1
        steps = []
        for _ in range(n_steps):
            if len(data) < off + 6:
                raise ValueError("load blob truncated in step header")
            delay_s = struct.unpack('>f', data[off:off + 4])[0]
            tc_len = struct.unpack('>H', data[off + 4:off + 6])[0]
            off += 6
            if len(data) < off + tc_len:
                raise ValueError("load blob truncated in step TC")
            steps.append({'delay_s': delay_s, 'tc': data[off:off + tc_len]})
            off += tc_len
        if not steps:
            raise ValueError("load blob has no steps")
        return proc_id, name, steps

    @staticmethod
    def encode_load_blob(proc_id: int, name: str, steps: list[dict]) -> bytes:
        """Build the TC(18,1) application data (ground-side helper / tests)."""
        name_b = name.encode('utf-8')[:255]
        out = struct.pack('>H', proc_id) + bytes([len(name_b)]) + name_b
        out += bytes([len(steps)])
        for s in steps:
            tc = bytes(s['tc'])
            out += struct.pack('>f', float(s['delay_s']))
            out += struct.pack('>H', len(tc)) + tc
        return out

    # ── boot-time config loading (configs/<mission>/obcp/*.yaml) ─────

    def load_from_dir(self, path: Path) -> int:
        """Pre-load procedures from YAML files at boot. Returns count loaded.

        File format (one procedure per file):
          procedure:
            id: 1
            name: pass_preparation
            steps:
              - {delay_s: 0, service: 8, subtype: 1, func_id: 19, data_hex: "02"}
        ``func_id`` (optional) is prepended to the data field for S8 calls;
        ``data_hex`` (optional) is appended as raw hex bytes.
        """
        import yaml
        from smo_common.protocol.ecss_packet import build_tc_packet

        path = Path(path)
        if not path.is_dir():
            logger.info("No OBCP config dir at %s — none pre-loaded", path)
            return 0
        count = 0
        for f in sorted(path.glob("*.yaml")):
            try:
                with open(f, 'r') as fh:
                    doc = yaml.safe_load(fh) or {}
                proc = doc.get('procedure')
                if not proc:
                    logger.warning("OBCP file %s has no 'procedure' key", f)
                    continue
                proc_id = int(proc['id'])
                name = str(proc.get('name', f.stem))
                steps = []
                for s in proc.get('steps', []):
                    data = b''
                    if isinstance(s.get('args'), dict):
                        # Phase E WP-1/WP-6: named ENGINEERING-unit args,
                        # packed by the shared catalogue encoder — the same
                        # code path as the procedure runner, planner and S11
                        # embedder, so the OBCP files cannot drift. Includes
                        # the func_id octet for S8.1.
                        from smo_common.commanding import encode
                        data = encode(
                            (int(s.get('service', 8)),
                             int(s.get('subtype', 1)),
                             s.get('func_id')),
                            s['args'])
                        if s.get('data_hex'):
                            # A hand-written data_hex alongside args must
                            # MATCH the encoder (drift gate 4, brief 04).
                            expected = binascii.unhexlify(
                                str(s['data_hex']).replace(' ', ''))
                            if expected != data:
                                raise ValueError(
                                    f"step data_hex {s['data_hex']!r} "
                                    f"contradicts encoded args "
                                    f"{data.hex()!r}")
                    else:
                        if 'func_id' in s:
                            data += bytes([int(s['func_id']) & 0xFF])
                        if s.get('data_hex'):
                            data += binascii.unhexlify(
                                str(s['data_hex']).replace(' ', ''))
                    tc = build_tc_packet(
                        apid=0x1AC,  # self-generated TC APID (on-board source)
                        service=int(s.get('service', 8)),
                        subtype=int(s.get('subtype', 1)),
                        data=data,
                    )
                    steps.append({'delay_s': float(s.get('delay_s', 0.0)),
                                  'tc': tc})
                if self.load(proc_id, name, steps):
                    count += 1
            except Exception as e:  # noqa: BLE001 — one bad file must not block boot
                logger.warning("OBCP config %s load failed: %s", f, e)
        if count:
            logger.info("Pre-loaded %d OBCP(s) from %s", count, path)
        return count

    # ── engine integration ───────────────────────────────────────────

    def tick(self, dt_sim: float) -> None:
        """Advance running procedures by dt_sim (called once per engine tick).

        Steps whose relative delay has elapsed are executed via the engine's
        internal dispatch path. Steps are gathered under the lock but
        dispatched outside it, so a step's own S18 TC (e.g. a procedure that
        starts another procedure) cannot deadlock the manager.
        """
        due: list[tuple[int, int, bytes]] = []      # (proc_id, step_idx, tc)
        completed: list[int] = []
        with self._lock:
            for pid in list(self._running.keys()):
                run = self._running[pid]
                proc = self._procedures.get(pid)
                if proc is None:  # deleted underneath us — drop silently
                    del self._running[pid]
                    continue
                run['wait_remaining'] -= dt_sim
                steps = proc['steps']
                # Several steps may become due in one tick (short delays or
                # high sim speed): keep consuming while the budget allows.
                while run['wait_remaining'] <= 0.0:
                    idx = run['step_index']
                    due.append((pid, idx, steps[idx]['tc']))
                    idx += 1
                    if idx >= len(steps):
                        completed.append(pid)
                        del self._running[pid]
                        break
                    run['step_index'] = idx
                    run['wait_remaining'] += float(steps[idx]['delay_s'])

        for pid, idx, tc in due:
            self._execute_step(pid, idx, tc)
        for pid in completed:
            name = self._procedures.get(pid, {}).get('name', '?')
            self._emit(EVENT_OBCP_COMPLETED, 1,
                       f"OBCP {pid} '{name}' completed")

    def _execute_step(self, proc_id: int, step_idx: int, tc: bytes) -> None:
        """Execute one step TC through the engine's normal dispatch path.

        The TC is pre-validated (decommutation + the engine's acceptance
        rules) so a bad step raises OBCP_STEP_FAILED and aborts the
        procedure instead of vanishing into the malformed-packet log.
        """
        from smo_common.protocol.ecss_packet import decommutate_packet
        eng = self._engine

        pkt = decommutate_packet(tc)
        if pkt is None or pkt.secondary is None:
            self._fail_step(proc_id, step_idx, "malformed step TC")
            return
        svc, sub = pkt.secondary.service, pkt.secondary.subtype
        try:
            accepted, err = eng._check_tc_acceptance(svc, sub, pkt.data_field)
        except Exception:  # pragma: no cover — acceptance must not kill tick
            accepted, err = True, 0
        if not accepted:
            self._fail_step(
                proc_id, step_idx,
                f"step TC S{svc}.{sub} rejected at acceptance "
                f"(error 0x{err:04X})")
            return

        logger.info("OBCP %d step %d: dispatching S%d.%d (source=OBCP)",
                    proc_id, step_idx, svc, sub)
        try:
            eng._dispatch_tc(tc)
        except Exception as e:  # noqa: BLE001
            self._fail_step(proc_id, step_idx, f"dispatch error: {e}")

    def _fail_step(self, proc_id: int, step_idx: int, reason: str) -> None:
        name = self._procedures.get(proc_id, {}).get('name', '?')
        logger.warning("OBCP %d '%s' step %d FAILED: %s (source=OBCP)",
                       proc_id, name, step_idx, reason)
        self._emit(EVENT_OBCP_STEP_FAILED, 2,
                   f"OBCP {proc_id} '{name}' step {step_idx} failed: {reason}")
        with self._lock:
            if proc_id in self._running:
                self._abort(proc_id, f"step {step_idx} failed")

    def _emit(self, event_id: int, severity: int, description: str) -> None:
        try:
            self._engine._emit_event({
                'event_id': event_id,
                'severity': severity,
                'description': description,
            })
        except Exception:  # pragma: no cover — event path must not kill OBCP
            logger.exception("OBCP event emission failed")

    # ── breakpoint support (mirrors OnboardTMStorage) ────────────────

    def get_state(self) -> dict:
        """JSON-safe snapshot: procedure store + running interpreter state."""
        with self._lock:
            return {
                'procedures': {
                    str(pid): {
                        'name': p['name'],
                        'steps': [
                            {'delay_s': s['delay_s'],
                             'tc_hex': s['tc'].hex()}
                            for s in p['steps']
                        ],
                    }
                    for pid, p in self._procedures.items()
                },
                'running': {
                    str(pid): dict(run)
                    for pid, run in self._running.items()
                },
            }

    def set_state(self, state: dict) -> None:
        """Restore a snapshot captured by get_state() (breakpoint load)."""
        if not isinstance(state, dict):
            return
        with self._lock:
            self._procedures = {}
            for pid_s, p in (state.get('procedures') or {}).items():
                try:
                    self._procedures[int(pid_s)] = {
                        'name': p.get('name', ''),
                        'steps': [
                            {'delay_s': float(s['delay_s']),
                             'tc': bytes.fromhex(s['tc_hex'])}
                            for s in p.get('steps', [])
                        ],
                    }
                except Exception as e:  # noqa: BLE001
                    logger.warning("OBCP restore: bad procedure %s: %s", pid_s, e)
            self._running = {}
            for pid_s, run in (state.get('running') or {}).items():
                try:
                    pid = int(pid_s)
                    if pid in self._procedures:
                        self._running[pid] = {
                            'step_index': int(run.get('step_index', 0)),
                            'wait_remaining': float(run.get('wait_remaining', 0.0)),
                            'param': int(run.get('param', 0)),
                        }
                except Exception as e:  # noqa: BLE001
                    logger.warning("OBCP restore: bad running entry %s: %s", pid_s, e)
        logger.info("OBCP state restored: %d procedure(s), %d running",
                    len(self._procedures), len(self._running))
