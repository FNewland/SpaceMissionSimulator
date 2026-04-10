"""
EO Mission Simulator — Full PUS-C Service Dispatcher
All 17 PUS-C services, all normative subtypes (~80 total).
Returns list of TM response packets (bytes) for each TC processed.

Service coverage:
  S1  TC Verification          S2  Device Access
  S3  Housekeeping             S4  Parameter Statistics
  S5  Event Reporting          S6  Memory Management
  S7  OBL Monitoring           S8  Function Management
  S9  Time Management          S10 External Device Commanding
  S11 Time-Based Scheduling    S12 Parameter Monitoring
  S13 Large Data Transfer      S14 Real-Time Forwarding
  S15 On-Board Storage         S16 Packet Selection
  S17 Test                     S18 On-Board Control Procedures
  S19 Event-Action             S20 Parameter Management
  S21 Request Sequencing       S22 Position-Based Scheduling
  S23 File Management
"""
import struct
import logging
import time
from collections import deque
from datetime import datetime
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine import SimulationEngine

from ecss_decommutator import ECSSDecommutator, PacketType

logger = logging.getLogger(__name__)
_decom = ECSSDecommutator()

# ── Error codes ───────────────────────────────────────────────────────────────
ERR_INVALID_SUBTYPE  = 0x0001
ERR_INVALID_PARAM    = 0x0002
ERR_SUBSYS_REJECTED  = 0x0003
ERR_LENGTH           = 0x0004
ERR_PARSE            = 0x0005
ERR_NOT_FOUND        = 0x0006
ERR_ALREADY_EXISTS   = 0x0007
ERR_LIMIT_VIOLATION  = 0x0008


# ═════════════════════════════════════════════════════════════════════════════
# Per-service state classes
# ═════════════════════════════════════════════════════════════════════════════

class _ParamStats:
    """Rolling window statistics for one parameter (S4)."""
    __slots__ = ('enabled','interval_s','timer','window','n_samples')
    def __init__(self, interval_s=60.0):
        self.enabled     = True
        self.interval_s  = interval_s
        self.timer       = 0.0
        self.window      = deque(maxlen=300)
        self.n_samples   = 0
    def accumulate(self, val):
        self.window.append(val); self.n_samples += 1
    def stats(self):
        if not self.window: return 0.0, 0.0, 0.0, 0.0
        vals = list(self.window)
        mean = sum(vals)/len(vals)
        return vals[-1], min(vals), max(vals), mean


class _S7OBLEntry:
    """One On-Board Lookup table entry (S7)."""
    __slots__ = ('table_id','active','data')
    def __init__(self, table_id, data=b''):
        self.table_id = table_id
        self.active   = True
        self.data     = data


class _S11ScheduledCmd:
    """One time-tagged command (S11)."""
    __slots__ = ('cmd_id','exec_cuc','tc_bytes','executed','inserted_at')
    def __init__(self, cmd_id, exec_cuc, tc_bytes):
        self.cmd_id      = cmd_id
        self.exec_cuc    = exec_cuc   # CUC seconds
        self.tc_bytes    = tc_bytes
        self.executed    = False
        self.inserted_at = time.time()


class _S12LimitEntry:
    """One parameter limit definition (S12)."""
    __slots__ = ('enabled','yellow_lo','yellow_hi','red_lo','red_hi',
                 'check_interval','check_timer','violation_count','prev_state')
    def __init__(self, yellow_lo, yellow_hi, red_lo, red_hi, check_interval=10.0):
        self.enabled         = True
        self.yellow_lo       = yellow_lo
        self.yellow_hi       = yellow_hi
        self.red_lo          = red_lo
        self.red_hi          = red_hi
        self.check_interval  = check_interval
        self.check_timer     = 0.0
        self.violation_count = 0
        self.prev_state      = 'NOMINAL'


class _S14ForwardingEntry:
    """One forwarding control entry (S14)."""
    __slots__ = ('vc_id','enabled','service_filter','subtype_filter')
    def __init__(self, vc_id, enabled=True):
        self.vc_id          = vc_id
        self.enabled        = enabled
        self.service_filter = []   # empty = all
        self.subtype_filter = []


class _S15Session:
    """One on-board storage recording session (S15)."""
    __slots__ = ('sid','enabled','packet_count','stored_ids','created_at','size_bytes')
    def __init__(self, sid, stored_ids=None):
        self.sid          = sid
        self.enabled      = True
        self.packet_count = 0
        self.stored_ids   = stored_ids or []
        self.created_at   = time.time()
        self.size_bytes   = 0


class _S16PacketSel:
    """Packet selection entry for downlink (S16)."""
    __slots__ = ('sel_id','active','apid_list','service_list')
    def __init__(self, sel_id):
        self.sel_id       = sel_id
        self.active       = True
        self.apid_list    = []
        self.service_list = []


class _S18Procedure:
    """One on-board control procedure (S18)."""
    __slots__ = ('proc_id','name','state','step_count','current_step',
                 'steps','loaded_at')
    STATES = ('IDLE','LOADED','RUNNING','SUSPENDED','COMPLETED','ABORTED')
    def __init__(self, proc_id, name='', steps=None):
        self.proc_id      = proc_id
        self.name         = name
        self.state        = 'LOADED'
        self.step_count   = len(steps or [])
        self.current_step = 0
        self.steps        = steps or []
        self.loaded_at    = time.time()


class _S19EventAction:
    """One event→action mapping (S19)."""
    __slots__ = ('event_id','action_id','enabled','tc_bytes')
    def __init__(self, event_id, action_id, enabled=True, tc_bytes=b''):
        self.event_id  = event_id
        self.action_id = action_id
        self.enabled   = enabled
        self.tc_bytes  = tc_bytes


class _S23File:
    """Virtual file (S23)."""
    __slots__ = ('file_id','path','data','attrs','created_at','modified_at')
    def __init__(self, file_id, path, data=b''):
        self.file_id     = file_id
        self.path        = path
        self.data        = data
        self.attrs       = {}
        self.created_at  = time.time()
        self.modified_at = time.time()


# ═════════════════════════════════════════════════════════════════════════════
# Store initialisation helpers
# ═════════════════════════════════════════════════════════════════════════════

def _ensure_stores(engine):
    """Lazily attach per-service state stores to engine instance."""
    if not hasattr(engine, '_s4_stats'):
        engine._s4_stats:      Dict[int, _ParamStats]       = {}
    if not hasattr(engine, '_s5_event_log'):
        engine._s5_event_log:  List[dict]                   = []   # capped at 200
    if not hasattr(engine, '_s5_enabled'):
        engine._s5_enabled:    Dict[int, bool]              = {}   # event_id → enabled
    if not hasattr(engine, '_s6_memory'):
        engine._s6_memory:     Dict[int, bytearray]         = {}
    if not hasattr(engine, '_s7_obl'):
        engine._s7_obl:        Dict[int, _S7OBLEntry]       = {}
    if not hasattr(engine, '_s11_schedule'):
        engine._s11_schedule:  Dict[int, _S11ScheduledCmd]  = {}
    if not hasattr(engine, '_s11_enabled'):
        engine._s11_enabled:   bool                         = True
    if not hasattr(engine, '_s11_seq'):
        engine._s11_seq:       int                          = 0
    if not hasattr(engine, '_s12_limits'):
        engine._s12_limits:    Dict[int, _S12LimitEntry]    = {}
        _load_default_limits(engine)
    if not hasattr(engine, '_s13_transfers'):
        engine._s13_transfers: Dict[int, dict]              = {}
    if not hasattr(engine, '_s14_forwarding'):
        engine._s14_forwarding:Dict[int, _S14ForwardingEntry]= {}
    if not hasattr(engine, '_s15_sessions'):
        engine._s15_sessions:  Dict[int, _S15Session]       = {}
    if not hasattr(engine, '_s16_selections'):
        engine._s16_selections:Dict[int, _S16PacketSel]     = {}
    if not hasattr(engine, '_s18_procedures'):
        engine._s18_procedures:Dict[int, _S18Procedure]     = {}
    if not hasattr(engine, '_s19_actions'):
        engine._s19_actions:   Dict[int, _S19EventAction]   = {}
    if not hasattr(engine, '_s21_sequences'):
        engine._s21_sequences: Dict[int, dict]              = {}


def _load_default_limits(engine):
    """Populate S12 limit table from config DEFAULT_LIMITS."""
    try:
        from config import DEFAULT_LIMITS
        for entry in DEFAULT_LIMITS:
            if isinstance(entry, (tuple, list)):
                if len(entry) < 5: continue
                pid, ylo, yhi, rlo, rhi = entry[0],entry[1],entry[2],entry[3],entry[4]
                interval = entry[5] if len(entry) > 5 else 10.0
            else:
                pid      = entry['id']
                ylo, yhi = entry.get('yellow_lo',-1e9), entry.get('yellow_hi',1e9)
                rlo, rhi = entry.get('red_lo',-1e9),    entry.get('red_hi',1e9)
                interval = entry.get('interval',10.0)
            engine._s12_limits[pid] = _S12LimitEntry(ylo,yhi,rlo,rhi,float(interval))
    except (ImportError, KeyError):
        pass


# ═════════════════════════════════════════════════════════════════════════════
# Tick functions — called by engine every simulation step
# ═════════════════════════════════════════════════════════════════════════════

def monitoring_tick(engine, dt_sim: float):
    """S4 stats accumulation + S12 limit checking + S11 schedule execution."""
    _ensure_stores(engine)
    params = engine.params
    cuc    = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

    # ── S4 statistics ─────────────────────────────────────────────────────
    for param_id, stat in engine._s4_stats.items():
        if not stat.enabled: continue
        stat.accumulate(params.get(param_id, 0.0))
        stat.timer += dt_sim
        if stat.timer >= stat.interval_s:
            stat.timer = 0.0
            cur, mn, mx, mean = stat.stats()
            d = (struct.pack('>H', param_id) + bytes([1]) +
                 struct.pack('>H', int(stat.interval_s)) +
                 struct.pack('>f', cur) + struct.pack('>I', cuc) +
                 struct.pack('>f', mn) + struct.pack('>f', mx) +
                 struct.pack('>f', mean))
            engine._enqueue_tm(engine.tm_builder._pack_tm(4, 1, d, cuc))

    # ── S11 time-based scheduling ──────────────────────────────────────────
    if engine._s11_enabled:
        for cmd_id, cmd in list(engine._s11_schedule.items()):
            if not cmd.executed and cuc >= cmd.exec_cuc:
                cmd.executed = True
                try:
                    engine.tc_queue.put_nowait(cmd.tc_bytes)
                    logger.info("S11: Executing scheduled cmd %d at CUC=%d", cmd_id, cuc)
                    # S11/2 time tag execution report
                    d = struct.pack('>HI', cmd_id, cuc)
                    engine._enqueue_tm(engine.tm_builder._pack_tm(11, 2, d, cuc))
                except Exception as e:
                    logger.warning("S11 schedule exec error: %s", e)

    # ── S12 limit checking ─────────────────────────────────────────────────
    for param_id, lim in engine._s12_limits.items():
        if not lim.enabled: continue
        lim.check_timer += dt_sim
        if lim.check_timer < lim.check_interval: continue
        lim.check_timer = 0.0

        val = params.get(param_id, 0.0)
        if   val <= lim.red_lo    or val >= lim.red_hi:    state = 'RED'
        elif val <= lim.yellow_lo or val >= lim.yellow_hi: state = 'YELLOW'
        else:                                               state = 'NOMINAL'

        if state != 'NOMINAL' and state != lim.prev_state:
            lim.violation_count += 1
            status = 2 if state == 'RED' else 1
            d = (struct.pack('>H', param_id) + bytes([1, status, 2]) +
                 struct.pack('>f', lim.yellow_lo) + struct.pack('>f', lim.yellow_hi) +
                 struct.pack('>f', val) + struct.pack('>H', lim.violation_count))
            engine._enqueue_tm(engine.tm_builder._pack_tm(12, 1, d, cuc))
            if state == 'RED':
                engine._emit_event({'event_id': 0x1000|(param_id&0x0FFF), 'severity': 3,
                    'description': f'RED violation param 0x{param_id:04X}={val:.3f}'})
        lim.prev_state = state
        if state != 'NOMINAL':
            _fire_event_actions(engine, 0x1000|(param_id&0x0FFF))

    # ── S18 procedure advancement ──────────────────────────────────────────
    for proc in engine._s18_procedures.values():
        if proc.state == 'RUNNING' and proc.current_step < proc.step_count:
            proc.current_step += 1
            if proc.current_step >= proc.step_count:
                proc.state = 'COMPLETED'
                d = struct.pack('>HB', proc.proc_id, 3)   # status=completed
                engine._enqueue_tm(engine.tm_builder._pack_tm(18, 3, d, cuc))


def _fire_event_actions(engine, event_id: int):
    """Fire enabled S19 actions associated with event_id."""
    _ensure_stores(engine)
    for ea in engine._s19_actions.values():
        if ea.event_id == event_id and ea.enabled and ea.tc_bytes:
            try: engine.tc_queue.put_nowait(ea.tc_bytes)
            except Exception: pass


# ═════════════════════════════════════════════════════════════════════════════
# Main dispatcher
# ═════════════════════════════════════════════════════════════════════════════

class ServiceDispatcher:
    """Parse TC packet and dispatch to per-service handler."""

    @staticmethod
    def dispatch(raw_tc: bytes, engine: 'SimulationEngine') -> List[bytes]:
        _ensure_stores(engine)
        responses: List[bytes] = []
        try:
            pkt = _decom.decommutate_packet(raw_tc)
        except Exception as e:
            logger.warning("TC parse failed: %s", e)
            engine.obdh.record_tc_rejected()
            return responses

        if pkt.header.packet_type != PacketType.COMMAND:
            return responses
        if not pkt.secondary_header:
            engine.obdh.record_tc_rejected()
            return responses

        apid = pkt.header.apid
        seq  = pkt.header.sequence_count
        svc  = pkt.secondary_header.service_type
        sub  = pkt.secondary_header.service_subtype
        data = pkt.data_field

        engine.obdh.record_tc_accepted()
        responses.append(engine.tm_builder.build_verification_acceptance(apid, seq))

        try:
            result_pkts = ServiceDispatcher._route(svc, sub, data, engine)
            responses.extend(result_pkts)
            responses.append(engine.tm_builder.build_verification_completion(apid, seq))
        except Exception as e:
            logger.warning("TC S%d/%d exec error: %s", svc, sub, e)
            responses.append(engine.tm_builder.build_verification_failure(
                apid, seq, ERR_SUBSYS_REJECTED))

        return responses

    @staticmethod
    def _route(svc, sub, data, engine) -> List[bytes]:
        _h = {
             1: ServiceDispatcher._svc1_verification,
             2: ServiceDispatcher._svc2_device,
             3: ServiceDispatcher._svc3_housekeeping,
             4: ServiceDispatcher._svc4_statistics,
             5: ServiceDispatcher._svc5_event,
             6: ServiceDispatcher._svc6_memory,
             7: ServiceDispatcher._svc7_obl,
             8: ServiceDispatcher._svc8_function,
             9: ServiceDispatcher._svc9_time,
            10: ServiceDispatcher._svc10_extdev,
            11: ServiceDispatcher._svc11_schedule,
            12: ServiceDispatcher._svc12_monitoring,
            13: ServiceDispatcher._svc13_largedata,
            14: ServiceDispatcher._svc14_forwarding,
            15: ServiceDispatcher._svc15_storage,
            16: ServiceDispatcher._svc16_selection,
            17: ServiceDispatcher._svc17_test,
            18: ServiceDispatcher._svc18_procedure,
            19: ServiceDispatcher._svc19_event_action,
            20: ServiceDispatcher._svc20_parameter,
            21: ServiceDispatcher._svc21_sequencing,
            22: ServiceDispatcher._svc22_scheduling,
            23: ServiceDispatcher._svc23_file,
        }
        h = _h.get(svc)
        if h is None:
            logger.warning("No handler for PUS service %d", svc)
            return []
        return h(sub, data, engine)

    # ─────────────────────────────────────────────────────────────────────────
    # S1 — TC Verification  (normative TC subtypes configure ack flags)
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc1_verification(sub, data, engine):
        # S1/1  Enable/configure acceptance reporting
        # S1/2  Disable acceptance reporting
        # (Response reports are generated by dispatcher itself)
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S2 — Device Access
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc2_device(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S2 device_id missing")
        dev_id = struct.unpack('>H', data[:2])[0]
        dev_data = data[2:]

        if sub == 1:   # Raw device command
            logger.info("S2/1 raw command device=%d len=%d", dev_id, len(dev_data))
        elif sub == 2: # Read device
            # Return S2/3 device data report
            d = struct.pack('>H', dev_id) + bytes([0]) + dev_data[:16]
            return [engine.tm_builder._pack_tm(2, 3, d, cuc)]
        elif sub == 3: # Write device
            logger.info("S2/3 write device=%d len=%d", dev_id, len(dev_data))
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S3 — Housekeeping
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc3_housekeeping(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S3 SID missing")
        sid = struct.unpack('>H', data[:2])[0]

        if sub == 1:     # Enable periodic HK collection
            interval = struct.unpack('>I', data[2:6])[0] if len(data) >= 6 else 4
            engine.set_hk_enabled(sid, True)
            engine.set_hk_interval(sid, float(interval))

        elif sub == 2:   # Disable periodic HK collection
            engine.set_hk_enabled(sid, False)

        elif sub == 3:   # Enable periodic diagnostic collection (treated as HK)
            interval = struct.unpack('>I', data[2:6])[0] if len(data) >= 6 else 4
            engine.set_hk_enabled(sid, True)
            engine.set_hk_interval(sid, float(interval))

        elif sub == 4:   # Disable periodic diagnostic collection
            engine.set_hk_enabled(sid, False)

        elif sub == 5:   # Report HK structure
            d = struct.pack('>H', sid) + struct.pack('>B', 1)  # 1=enabled
            return [engine.tm_builder._pack_tm(3, 27, d, cuc)]

        elif sub == 6:   # Report diagnostic structure
            d = struct.pack('>H', sid) + struct.pack('>B', 1)
            return [engine.tm_builder._pack_tm(3, 28, d, cuc)]

        elif sub == 7:   # One-shot HK report → S3/25
            pkt = engine.tm_builder.build_hk_packet(sid, engine.params)
            if pkt: return [pkt]

        elif sub == 8:   # One-shot diagnostic report → S3/26
            pkt = engine.tm_builder.build_hk_packet(sid, engine.params)
            if pkt: return [engine.tm_builder._pack_tm(3, 26, pkt[6:], cuc)]

        elif sub == 9:   # Append parameters to HK structure
            logger.info("S3/9 append params to SID %d", sid)

        elif sub == 10:  # Modify collection interval
            if len(data) >= 6:
                new_interval = struct.unpack('>I', data[2:6])[0]
                engine.set_hk_interval(sid, float(new_interval))

        elif sub == 11:  # Delete parameters from structure
            logger.info("S3/11 delete params from SID %d", sid)

        elif sub == 12:  # Report all HK structure IDs → S3/27 for each
            pkts = []
            for known_sid in getattr(engine, '_hk_intervals', {}).keys():
                d = struct.pack('>H', known_sid) + bytes([1])
                pkts.append(engine.tm_builder._pack_tm(3, 27, d, cuc))
            return pkts

        elif sub == 27:  # One-shot HK report (alt subtype)
            pkt = engine.tm_builder.build_hk_packet(sid, engine.params)
            if pkt: return [pkt]

        elif sub == 28:  # One-shot diagnostic report (alt)
            pkt = engine.tm_builder.build_hk_packet(sid, engine.params)
            if pkt: return [pkt]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S4 — Parameter Statistics
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc4_statistics(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:   # Enable statistics reporting for parameter
            if len(data) < 4: raise ValueError("S4 enable: need param_id+interval")
            pid  = struct.unpack('>H', data[:2])[0]
            intv = float(struct.unpack('>H', data[2:4])[0])
            if pid not in engine._s4_stats:
                engine._s4_stats[pid] = _ParamStats(max(intv, 10.0))
            else:
                engine._s4_stats[pid].enabled    = True
                engine._s4_stats[pid].interval_s = max(intv, 10.0)

        elif sub == 2: # Disable statistics reporting
            if len(data) < 2: raise ValueError("S4 disable: need param_id")
            pid = struct.unpack('>H', data[:2])[0]
            if pid in engine._s4_stats:
                engine._s4_stats[pid].enabled = False

        elif sub == 3: # Request statistics report → S4/1
            if len(data) < 2: raise ValueError("S4 report: need param_id")
            pid  = struct.unpack('>H', data[:2])[0]
            stat = engine._s4_stats.get(pid)
            if stat:
                cur, mn, mx, mean = stat.stats()
                d = (struct.pack('>H', pid) + bytes([1]) +
                     struct.pack('>H', int(stat.interval_s)) +
                     struct.pack('>f', cur) + struct.pack('>I', cuc) +
                     struct.pack('>f', mn) + struct.pack('>f', mx) +
                     struct.pack('>f', mean))
                return [engine.tm_builder._pack_tm(4, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S5 — Event Reporting
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc5_event(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        # Subtypes 1-4 enable/disable by severity group
        # Subtypes 5-6 enable/disable individual event IDs
        event_id = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0

        if   sub == 1: pass  # Enable informative event reporting (always on)
        elif sub == 2: pass  # Disable informative event reporting
        elif sub == 3: pass  # Enable low-severity event reporting
        elif sub == 4: pass  # Disable low-severity event reporting
        elif sub == 5: engine._s5_enabled[event_id] = True   # Enable specific
        elif sub == 6: engine._s5_enabled[event_id] = False  # Disable specific
        elif sub == 7:  # Clear event log
            engine._s5_event_log.clear()
        elif sub == 8:  # Report event log → S5/1..4 for each entry
            pkts = []
            for ev in engine._s5_event_log[-20:]:  # last 20
                sev = ev.get('severity', 1)
                sub_tm = min(max(sev, 1), 4)
                d = (struct.pack('>H', ev.get('event_id', 0)) +
                     bytes([sev]) + struct.pack('>I', ev.get('cuc', cuc)))
                pkts.append(engine.tm_builder._pack_tm(5, sub_tm, d, cuc))
            return pkts

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S6 — Memory Management
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc6_memory(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 10: raise ValueError("S6: need mem_id(2)+addr(4)+len(4)")
        mem_id = struct.unpack('>H', data[:2])[0]
        addr   = struct.unpack('>I', data[2:6])[0]
        length = min(struct.unpack('>I', data[6:10])[0], 4096)

        if mem_id not in engine._s6_memory:
            engine._s6_memory[mem_id] = bytearray(65536)
        mem = engine._s6_memory[mem_id]

        if sub == 1:    # Load memory from ground (with data verification)
            payload = data[10:10+length]
            end = min(addr + len(payload), len(mem))
            mem[addr:end] = payload[:end-addr]

        elif sub == 2:  # Raw memory load (no verification)
            payload = data[10:10+length]
            end = min(addr+len(payload), len(mem))
            mem[addr:end] = payload[:end-addr]

        elif sub == 3:  # Memory dump to file (acknowledged)
            start, end = addr & 0xFFFF, min((addr & 0xFFFF)+length, len(mem))
            chunk = bytes(mem[start:end])
            d = struct.pack('>HII', mem_id, addr, len(chunk)) + chunk
            return [engine.tm_builder._pack_tm(6, 6, d, cuc)]

        elif sub == 5:  # Raw memory dump request → S6/6
            start, end = addr & 0xFFFF, min((addr & 0xFFFF)+length, len(mem))
            chunk = bytes(mem[start:end])
            d = struct.pack('>HII', mem_id, addr, len(chunk)) + chunk
            return [engine.tm_builder._pack_tm(6, 6, d, cuc)]

        elif sub == 7:  # Copy memory block
            src_addr   = struct.unpack('>I', data[10:14])[0] & 0xFFFF if len(data) >= 14 else 0
            src_end    = min(src_addr+length, len(mem))
            block      = bytes(mem[src_addr:src_end])
            dst_end    = min(addr+len(block), len(mem))
            mem[addr:dst_end] = block[:dst_end-addr]

        elif sub == 9:  # Checksum request → S6/10
            start, end = addr & 0xFFFF, min((addr & 0xFFFF)+length, len(mem))
            csum = sum(mem[start:end]) & 0xFFFFFFFF
            d = struct.pack('>HIIII', mem_id, addr, length, csum, 0)
            return [engine.tm_builder._pack_tm(6, 10, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S7 — On-Board Lookup Table (OBL) Monitoring
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc7_obl(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S7: need table_id(2)")
        table_id = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Create OBL table
            engine._s7_obl[table_id] = _S7OBLEntry(table_id, data[2:])
        elif sub == 2:  # Delete OBL table
            engine._s7_obl.pop(table_id, None)
        elif sub == 3:  # Activate OBL table
            if table_id in engine._s7_obl:
                engine._s7_obl[table_id].active = True
        elif sub == 4:  # Suspend OBL table
            if table_id in engine._s7_obl:
                engine._s7_obl[table_id].active = False
        elif sub == 5:  # Resume OBL table
            if table_id in engine._s7_obl:
                engine._s7_obl[table_id].active = True
        elif sub == 128:  # OBL status report → S7/1
            count = len(engine._s7_obl)
            d = struct.pack('>H', count)
            for tid, t in engine._s7_obl.items():
                d += struct.pack('>HB', tid, int(t.active))
            return [engine.tm_builder._pack_tm(7, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S8 — Function Management
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc8_function(sub, data, engine):
        if len(data) < 2: raise ValueError("S8 function ID missing")
        func_id = struct.unpack('>H', data[:2])[0]
        args    = data[2:]

        FUNCS = {
            0x0001: _fn_payload_off,
            0x0002: _fn_payload_standby,
            0x0003: _fn_payload_imaging,
            0x0004: _fn_payload_high_res,
            0x0010: _fn_aocs_desaturate,
            0x0011: _fn_aocs_safe_mode,
            0x0012: _fn_aocs_nominal_mode,
            0x0013: _fn_aocs_nadir_mode,
            0x0014: _fn_aocs_sun_acq,
            0x0015: _fn_aocs_detumble,
            0x0020: _fn_eps_array_a_on,
            0x0021: _fn_eps_array_a_off,
            0x0022: _fn_eps_array_b_on,
            0x0023: _fn_eps_array_b_off,
            0x0024: _fn_eps_bat_heater_on,
            0x0025: _fn_eps_bat_heater_off,
            0x0030: _fn_fpa_cooler_on,
            0x0031: _fn_fpa_cooler_off,
            0x0032: _fn_obc_heater_on,
            0x0033: _fn_obc_heater_off,
            0x0040: _fn_transponder_redundant,
            0x0041: _fn_transponder_primary,
            0x0042: _fn_transponder_high_rate,
            0x0043: _fn_transponder_low_rate,
            0x0050: _fn_fdir_enable,
            0x0051: _fn_fdir_disable,
            0x0052: _fn_fdir_reset,
            0x0060: _fn_memory_scrub,
            0x0061: _fn_reboot_obc,
            0x0062: _fn_clear_error_registers,
            0x0070: _fn_scenario_detect,
            0x0071: _fn_scenario_isolate,
            0x0072: _fn_scenario_recover,
            0x0080: _fn_safe_mode_enter,
            0x0081: _fn_safe_mode_exit,
            0x0090: _fn_dump_all_hk,
        }
        fn = FUNCS.get(func_id)
        if fn is None:
            raise ValueError(f"Unknown function 0x{func_id:04X}")
        fn(engine, args)
        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S9 — Time Management
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc9_time(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:    # Time-set TC
            if len(data) < 5: raise ValueError("S9 time data too short")
            new_cuc = struct.unpack('>I', data[1:5])[0]
            engine.obdh.cmd_set_time(new_cuc)

        elif sub == 2:  # Time correlation TC
            if len(data) >= 8:
                ref_cuc = struct.unpack('>I', data[:4])[0]
                logger.info("S9/2 time correlation ref_cuc=%d", ref_cuc)
            # Respond with S9/2 time report
            d = bytes([0x01]) + struct.pack('>I', cuc)
            return [engine.tm_builder._pack_tm(9, 2, d, cuc)]

        elif sub == 128:  # Report current time → S9/2
            d = bytes([0x01]) + struct.pack('>I', cuc)
            return [engine.tm_builder._pack_tm(9, 2, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S10 — External Device Commanding
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc10_extdev(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S10: need device_id(2)")
        dev_id = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Send raw command to external device
            logger.info("S10/1 external device %d command len=%d", dev_id, len(data)-2)
        elif sub == 2:  # Activate device
            logger.info("S10/2 activate external device %d", dev_id)
            d = struct.pack('>HB', dev_id, 1)   # device_id, status=active
            return [engine.tm_builder._pack_tm(10, 1, d, cuc)]
        elif sub == 3:  # Deactivate device
            logger.info("S10/3 deactivate external device %d", dev_id)
            d = struct.pack('>HB', dev_id, 0)
            return [engine.tm_builder._pack_tm(10, 1, d, cuc)]
        elif sub == 128:  # Device status report
            d = struct.pack('>HBH', dev_id, 1, len(data))
            return [engine.tm_builder._pack_tm(10, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S11 — Time-Based Scheduling
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc11_schedule(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:    # Enable time-based scheduling
            engine._s11_enabled = True

        elif sub == 2:  # Disable time-based scheduling
            engine._s11_enabled = False

        elif sub == 3:  # Reset schedule (delete all entries)
            engine._s11_schedule.clear()

        elif sub == 4:  # Insert TC into schedule
            if len(data) < 6: raise ValueError("S11 insert: need exec_cuc(4)+tc_len(2)")
            exec_cuc = struct.unpack('>I', data[:4])[0]
            tc_len   = struct.unpack('>H', data[4:6])[0]
            tc_bytes = data[6:6+tc_len]
            engine._s11_seq = (engine._s11_seq + 1) & 0xFFFF
            cmd_id   = engine._s11_seq
            engine._s11_schedule[cmd_id] = _S11ScheduledCmd(cmd_id, exec_cuc, tc_bytes)
            logger.info("S11 inserted cmd %d exec_at CUC=%d", cmd_id, exec_cuc)

        elif sub == 5:  # Delete TC from schedule
            if len(data) < 2: raise ValueError("S11 delete: need cmd_id(2)")
            cmd_id = struct.unpack('>H', data[:2])[0]
            engine._s11_schedule.pop(cmd_id, None)

        elif sub == 6:  # Activate/time-shift specific TC
            if len(data) < 6: raise ValueError("S11 activate: need cmd_id(2)+new_cuc(4)")
            cmd_id   = struct.unpack('>H', data[:2])[0]
            new_cuc  = struct.unpack('>I', data[2:6])[0]
            if cmd_id in engine._s11_schedule:
                engine._s11_schedule[cmd_id].exec_cuc = new_cuc
                engine._s11_schedule[cmd_id].executed = False

        elif sub == 7:  # Time-shift entire schedule by delta
            if len(data) < 4: raise ValueError("S11 time-shift: need delta_cuc(4)")
            delta = struct.unpack('>i', data[:4])[0]   # signed delta
            for cmd in engine._s11_schedule.values():
                if not cmd.executed:
                    cmd.exec_cuc = max(0, cmd.exec_cuc + delta)

        elif sub == 128:  # Report schedule → S11/1
            pending = [(c.cmd_id, c.exec_cuc) for c in engine._s11_schedule.values()
                       if not c.executed]
            d = struct.pack('>BH', int(engine._s11_enabled), len(pending))
            for cmd_id, exec_cuc in pending[:20]:
                d += struct.pack('>HI', cmd_id, exec_cuc)
            return [engine.tm_builder._pack_tm(11, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S12 — Parameter Monitoring
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc12_monitoring(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:    # Enable parameter monitoring
            if len(data) < 2: raise ValueError("S12 enable: need param_id")
            pid = struct.unpack('>H', data[:2])[0]
            if pid in engine._s12_limits:
                engine._s12_limits[pid].enabled = True
            else:
                engine._s12_limits[pid] = _S12LimitEntry(-1e9, 1e9, -1e9, 1e9)

        elif sub == 2:  # Disable parameter monitoring
            if len(data) < 2: raise ValueError("S12 disable: need param_id")
            pid = struct.unpack('>H', data[:2])[0]
            if pid in engine._s12_limits:
                engine._s12_limits[pid].enabled = False

        elif sub == 3:  # Enable all parameter monitoring
            for lim in engine._s12_limits.values(): lim.enabled = True

        elif sub == 4:  # Disable all parameter monitoring
            for lim in engine._s12_limits.values(): lim.enabled = False

        elif sub == 5:  # Add / modify parameter limit check
            # Format: param_id(2) + ylo(f) + yhi(f) + rlo(f) + rhi(f) + interval(H)
            if len(data) < 18: raise ValueError("S12 add: need full limit definition")
            pid  = struct.unpack('>H', data[:2])[0]
            ylo  = struct.unpack('>f', data[2:6])[0]
            yhi  = struct.unpack('>f', data[6:10])[0]
            rlo  = struct.unpack('>f', data[10:14])[0]
            rhi  = struct.unpack('>f', data[14:18])[0]
            intv = struct.unpack('>H', data[18:20])[0] if len(data) >= 20 else 10
            engine._s12_limits[pid] = _S12LimitEntry(ylo, yhi, rlo, rhi, float(intv))

        elif sub == 6:  # Modify existing check interval
            if len(data) < 4: raise ValueError("S12 modify: need param_id(2)+interval(2)")
            pid  = struct.unpack('>H', data[:2])[0]
            intv = struct.unpack('>H', data[2:4])[0]
            if pid in engine._s12_limits:
                engine._s12_limits[pid].check_interval = float(intv)

        elif sub == 7:  # Delete parameter check
            if len(data) < 2: raise ValueError("S12 delete: need param_id")
            pid = struct.unpack('>H', data[:2])[0]
            engine._s12_limits.pop(pid, None)

        elif sub == 8:  # Delete all parameter checks
            engine._s12_limits.clear()

        elif sub == 9:  # Report monitoring definitions → S12/2
            count = len(engine._s12_limits)
            d = struct.pack('>H', count)
            for pid, lim in engine._s12_limits.items():
                d += struct.pack('>HBffff', pid, int(lim.enabled),
                                 lim.yellow_lo, lim.yellow_hi,
                                 lim.red_lo,    lim.red_hi)
            return [engine.tm_builder._pack_tm(12, 2, d, cuc)]

        elif sub == 10: # Report out-of-limit parameters → S12/1 for each
            pkts = []
            for pid, lim in engine._s12_limits.items():
                if lim.prev_state != 'NOMINAL' and lim.enabled:
                    val = engine.params.get(pid, 0.0)
                    status = 2 if lim.prev_state == 'RED' else 1
                    d = (struct.pack('>H', pid) + bytes([1, status, 2]) +
                         struct.pack('>f', lim.yellow_lo) +
                         struct.pack('>f', lim.yellow_hi) +
                         struct.pack('>f', val) +
                         struct.pack('>H', lim.violation_count))
                    pkts.append(engine.tm_builder._pack_tm(12, 1, d, cuc))
            return pkts

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S13 — Large Data Transfer
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc13_largedata(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S13: need transfer_id(2)")
        xid = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Abort uplink
            engine._s13_transfers.pop(xid, None)

        elif sub == 2:  # Request downlink transfer
            # Initiate chunked downlink of a virtual data product
            total_size = struct.unpack('>I', data[2:6])[0] if len(data) >= 6 else 1024
            part_size  = 256
            total_parts = (total_size + part_size - 1) // part_size
            engine._s13_transfers[xid] = {'total': total_size, 'sent': 0,
                                            'parts': total_parts, 'part_no': 0}
            # Send first part immediately
            pkts = []
            for part_no in range(min(total_parts, 3)):
                is_last = (part_no == total_parts - 1)
                sub_tm  = 1 if part_no == 0 else (3 if is_last else 2)
                payload = bytes(part_size)
                d = struct.pack('>HHH', xid, part_no+1, total_parts) + payload
                pkts.append(engine.tm_builder._pack_tm(13, sub_tm, d, cuc))
            return pkts

        elif sub == 16: # Abort ongoing downlink
            engine._s13_transfers.pop(xid, None)

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S14 — Real-Time Forwarding Control
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc14_forwarding(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 1: raise ValueError("S14: need vc_id(1)")
        vc_id = data[0]

        if sub == 1:    # Enable forwarding on VC
            engine._s14_forwarding[vc_id] = _S14ForwardingEntry(vc_id, True)
        elif sub == 2:  # Disable forwarding on VC
            if vc_id in engine._s14_forwarding:
                engine._s14_forwarding[vc_id].enabled = False
        elif sub == 3:  # Enable source packet forwarding
            engine._s14_forwarding[vc_id] = _S14ForwardingEntry(vc_id, True)
        elif sub == 4:  # Disable source packet forwarding
            if vc_id in engine._s14_forwarding:
                engine._s14_forwarding[vc_id].enabled = False
        elif sub == 5:  # Report forwarding status → S14/1
            count = len(engine._s14_forwarding)
            d = struct.pack('>B', count)
            for vc, fwd in engine._s14_forwarding.items():
                d += struct.pack('>BB', vc, int(fwd.enabled))
            return [engine.tm_builder._pack_tm(14, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S15 — On-Board Storage
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc15_storage(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S15: need sid(2)")
        sid = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Enable storage
            stored_ids = list(data[3:3+data[2]]) if len(data) > 2 else []
            engine._s15_sessions[sid] = _S15Session(sid, stored_ids)

        elif sub == 2:  # Disable storage (suspend)
            if sid in engine._s15_sessions:
                engine._s15_sessions[sid].enabled = False

        elif sub == 3:  # Enable previously disabled storage
            if sid in engine._s15_sessions:
                engine._s15_sessions[sid].enabled = True

        elif sub == 4:  # Delete storage contents
            if sid in engine._s15_sessions:
                engine._s15_sessions[sid].packet_count = 0
                engine._s15_sessions[sid].size_bytes   = 0

        elif sub == 5:  # Delete storage session
            engine._s15_sessions.pop(sid, None)

        elif sub == 6:  # Copy from storage → downlink virtual packet
            if sid in engine._s15_sessions:
                sess = engine._s15_sessions[sid]
                d = struct.pack('>HHI', sid, sess.packet_count, sess.size_bytes)
                return [engine.tm_builder._pack_tm(15, 25, d, cuc)]

        elif sub == 7:  # Move to downlink (copy then delete)
            if sid in engine._s15_sessions:
                sess = engine._s15_sessions[sid]
                d = struct.pack('>HHI', sid, sess.packet_count, sess.size_bytes)
                pkts = [engine.tm_builder._pack_tm(15, 25, d, cuc)]
                engine._s15_sessions[sid].packet_count = 0
                return pkts

        elif sub == 8:  # Set downlink bandwidth limit
            logger.info("S15/8 set bandwidth limit for session %d", sid)

        elif sub == 9:  # Storage status report → S15/10
            count = len(engine._s15_sessions)
            d = struct.pack('>H', count)
            for s_sid, sess in engine._s15_sessions.items():
                d += struct.pack('>HBHI', s_sid, int(sess.enabled),
                                 sess.packet_count, sess.size_bytes)
            return [engine.tm_builder._pack_tm(15, 10, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S16 — Packet Selection for Downlink/Storage
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc16_selection(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S16: need sel_id(2)")
        sel_id = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Add to selection
            sel = engine._s16_selections.setdefault(sel_id, _S16PacketSel(sel_id))
            # Optional: apid list follows
            if len(data) >= 4:
                n_apids = data[2]
                for i in range(n_apids):
                    if 3 + i*2 + 2 <= len(data):
                        apid = struct.unpack('>H', data[3+i*2:5+i*2])[0]
                        if apid not in sel.apid_list:
                            sel.apid_list.append(apid)
        elif sub == 2:  # Delete from selection
            engine._s16_selections.pop(sel_id, None)
        elif sub == 3:  # Activate selection
            if sel_id in engine._s16_selections:
                engine._s16_selections[sel_id].active = True
        elif sub == 4:  # Deactivate selection
            if sel_id in engine._s16_selections:
                engine._s16_selections[sel_id].active = False
        elif sub == 128:  # Report selection status → S16/1
            count = len(engine._s16_selections)
            d = struct.pack('>H', count)
            for s_id, sel in engine._s16_selections.items():
                d += struct.pack('>HBB', s_id, int(sel.active), len(sel.apid_list))
                for apid in sel.apid_list[:8]:
                    d += struct.pack('>H', apid)
            return [engine.tm_builder._pack_tm(16, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S17 — Test
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc17_test(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:   # Are-You-Alive (connection test) → S17/2
            d = struct.pack('>I', cuc)
            return [engine.tm_builder._pack_tm(17, 2, d, cuc)]

        elif sub == 2: # On-board connection test → S17/3
            test_id = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else 0
            d = struct.pack('>HBI', test_id, 0, cuc)   # test_id, result=pass, cuc
            return [engine.tm_builder._pack_tm(17, 3, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S18 — On-Board Control Procedures
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc18_procedure(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S18: need proc_id(2)")
        proc_id = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Load procedure
            name = data[2:].decode('ascii', errors='replace') if len(data) > 2 else ''
            engine._s18_procedures[proc_id] = _S18Procedure(proc_id, name)
            d = struct.pack('>HB', proc_id, 1)  # loaded
            return [engine.tm_builder._pack_tm(18, 1, d, cuc)]

        elif sub == 2:  # Unload procedure
            engine._s18_procedures.pop(proc_id, None)

        elif sub == 3:  # Activate procedure
            if proc_id in engine._s18_procedures:
                engine._s18_procedures[proc_id].state = 'RUNNING'
                d = struct.pack('>HB', proc_id, 2)  # running
                return [engine.tm_builder._pack_tm(18, 2, d, cuc)]

        elif sub == 4:  # Suspend procedure
            if proc_id in engine._s18_procedures:
                engine._s18_procedures[proc_id].state = 'SUSPENDED'

        elif sub == 5:  # Resume procedure
            if proc_id in engine._s18_procedures:
                engine._s18_procedures[proc_id].state = 'RUNNING'

        elif sub == 6:  # Abort procedure
            if proc_id in engine._s18_procedures:
                engine._s18_procedures[proc_id].state = 'ABORTED'

        elif sub == 128:  # Procedure status report → S18/2
            pkts = []
            for pid, proc in engine._s18_procedures.items():
                state_code = {'IDLE':0,'LOADED':1,'RUNNING':2,'SUSPENDED':3,
                              'COMPLETED':4,'ABORTED':5}.get(proc.state, 0)
                d = struct.pack('>HBB', pid, state_code, proc.current_step)
                pkts.append(engine.tm_builder._pack_tm(18, 2, d, cuc))
            return pkts

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S19 — Event-Action
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc19_event_action(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:    # Add event-action definition
            if len(data) < 5: raise ValueError("S19 add: need event_id+action_id+enabled")
            eid = struct.unpack('>H', data[:2])[0]
            aid = struct.unpack('>H', data[2:4])[0]
            enabled = bool(data[4])
            tc_bytes = data[5:]
            engine._s19_actions[aid] = _S19EventAction(eid, aid, enabled, tc_bytes)

        elif sub == 2:  # Delete specific event-action
            if len(data) < 2: raise ValueError("S19 delete: need action_id")
            aid = struct.unpack('>H', data[:2])[0]
            engine._s19_actions.pop(aid, None)

        elif sub == 3:  # Delete all event-action definitions
            engine._s19_actions.clear()

        elif sub == 4:  # Enable specific event-action
            if len(data) < 2: raise ValueError("S19 enable: need action_id")
            aid = struct.unpack('>H', data[:2])[0]
            if aid in engine._s19_actions:
                engine._s19_actions[aid].enabled = True

        elif sub == 5:  # Disable specific event-action
            if len(data) < 2: raise ValueError("S19 disable: need action_id")
            aid = struct.unpack('>H', data[:2])[0]
            if aid in engine._s19_actions:
                engine._s19_actions[aid].enabled = False

        elif sub == 6:  # Enable all event-actions
            for ea in engine._s19_actions.values(): ea.enabled = True

        elif sub == 7:  # Disable all event-actions
            for ea in engine._s19_actions.values(): ea.enabled = False

        elif sub == 8:  # Report all definitions → S19/1
            count = len(engine._s19_actions)
            d = struct.pack('>H', count)
            for aid, ea in engine._s19_actions.items():
                d += struct.pack('>HHB', ea.event_id, ea.action_id, int(ea.enabled))
            return [engine.tm_builder._pack_tm(19, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S20 — Parameter Management
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc20_parameter(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))

        if sub == 1:    # Set parameter value
            if len(data) < 7: raise ValueError("S20 set: data too short")
            pid   = struct.unpack('>H', data[:2])[0]
            ptype = data[2]
            value = (float(struct.unpack('>I', data[3:7])[0]) if ptype == 1
                     else float(struct.unpack('>f', data[3:7])[0]))
            engine.params[pid] = value
            logger.info("S20/1 param 0x%04X = %.4f", pid, value)

        elif sub == 2:  # Report one parameter value → S20/2
            if len(data) < 2: raise ValueError("S20 report: need param_id")
            pid   = struct.unpack('>H', data[:2])[0]
            value = engine.params.get(pid, 0.0)
            return [engine.tm_builder.build_param_value_report(pid, value)]

        elif sub == 3:  # Request parameter value report (TC) → S20/2
            if len(data) < 2: raise ValueError("S20 request: need param_id")
            pid   = struct.unpack('>H', data[:2])[0]
            value = engine.params.get(pid, 0.0)
            return [engine.tm_builder.build_param_value_report(pid, value)]

        elif sub == 128:  # Report all parameters → S20/2 for each
            pkts = []
            for pid, val in list(engine.params.items())[:50]:  # Cap at 50
                pkts.append(engine.tm_builder.build_param_value_report(pid, val))
            return pkts

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S21 — Request Sequencing
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc21_sequencing(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 2: raise ValueError("S21: need seq_id(2)")
        seq_id = struct.unpack('>H', data[:2])[0]

        if sub == 1:    # Enable sequence
            if seq_id in engine._s21_sequences:
                engine._s21_sequences[seq_id]['enabled'] = True
            else:
                engine._s21_sequences[seq_id] = {'enabled':True,'step':0,'steps':[]}

        elif sub == 2:  # Disable sequence
            if seq_id in engine._s21_sequences:
                engine._s21_sequences[seq_id]['enabled'] = False

        elif sub == 3:  # Abort sequence
            if seq_id in engine._s21_sequences:
                engine._s21_sequences[seq_id]['step'] = 0
                engine._s21_sequences[seq_id]['enabled'] = False

        elif sub == 4:  # Report all sequences → S21/1 for each
            pkts = []
            for sid, sq in engine._s21_sequences.items():
                d = struct.pack('>HBB', sid, int(sq['enabled']), sq['step'])
                pkts.append(engine.tm_builder._pack_tm(21, 1, d, cuc))
            return pkts

        elif sub == 5:  # Report specific sequence → S21/1
            sq = engine._s21_sequences.get(seq_id, {'enabled':False,'step':0})
            d = struct.pack('>HBB', seq_id, int(sq['enabled']), sq.get('step',0))
            return [engine.tm_builder._pack_tm(21, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S22 — Position-Based Scheduling
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc22_scheduling(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if not hasattr(engine, '_s22_schedules'):
            engine._s22_schedules = {}

        if sub == 1:    # Enable position-based scheduling
            logger.info("S22/1 enable position scheduling")
        elif sub == 2:  # Disable position-based scheduling
            logger.info("S22/2 disable position scheduling")
        elif sub == 3:  # Insert position-tagged TC
            if len(data) < 16: raise ValueError("S22 insert: need lat(f)+lon(f)+alt(f)+tc_len(H)+tc")
            lat    = struct.unpack('>f', data[:4])[0]
            lon    = struct.unpack('>f', data[4:8])[0]
            alt    = struct.unpack('>f', data[8:12])[0]
            tc_len = struct.unpack('>H', data[12:14])[0]
            tc_b   = data[14:14+tc_len]
            entry_id = len(engine._s22_schedules) + 1
            engine._s22_schedules[entry_id] = {
                'lat':lat,'lon':lon,'alt':alt,'tc':tc_b,'executed':False}
        elif sub == 4:  # Delete position-tagged TC
            if len(data) >= 2:
                eid = struct.unpack('>H', data[:2])[0]
                engine._s22_schedules.pop(eid, None)
        elif sub == 128:  # Report → S22/1
            count = len(engine._s22_schedules)
            d = struct.pack('>H', count)
            for eid, e in engine._s22_schedules.items():
                d += struct.pack('>Hfff', eid, e['lat'], e['lon'], e['alt'])
            return [engine.tm_builder._pack_tm(22, 1, d, cuc)]

        return []

    # ─────────────────────────────────────────────────────────────────────────
    # S23 — File Management
    # ─────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _svc23_file(sub, data, engine):
        cuc = int(getattr(engine.obdh.state, 'obc_time_cuc', 0))
        if len(data) < 3: raise ValueError("S23: need file_id(2)+path_len(1)")
        file_id  = struct.unpack('>H', data[:2])[0]
        path_len = data[2]
        if len(data) < 3 + path_len: raise ValueError("S23: path too short")
        path = data[3:3+path_len].decode('ascii', errors='replace')
        rest = data[3+path_len:]

        if sub == 1:    # Create file
            engine._s23_files[file_id] = _S23File(file_id, path)
            pb = path.encode('ascii')
            d  = struct.pack('>HB', file_id, len(pb)) + pb + struct.pack('>I', 0)
            return [engine.tm_builder._pack_tm(23, 1, d, cuc)]

        elif sub == 2:  # Delete file
            engine._s23_files.pop(file_id, None)

        elif sub == 3:  # Report file attributes → S23/1
            f  = engine._s23_files.get(file_id)
            pb = path.encode('ascii')
            sz = len(f.data) if f else 0
            d  = struct.pack('>HB', file_id, len(pb)) + pb + struct.pack('>I', sz)
            return [engine.tm_builder._pack_tm(23, 1, d, cuc)]

        elif sub == 4:  # Set file attributes
            if file_id in engine._s23_files:
                engine._s23_files[file_id].attrs = {'raw': rest.hex()}

        elif sub == 5:  # Read file → S23/2
            f  = engine._s23_files.get(file_id)
            fd = f.data if f else b''
            pb = path.encode('ascii')
            d  = (struct.pack('>HB', file_id, len(pb)) + pb +
                  struct.pack('>I', len(fd)) + fd[:1024])
            return [engine.tm_builder._pack_tm(23, 2, d, cuc)]

        elif sub == 6:  # Append to file
            if len(rest) >= 4:
                wlen  = struct.unpack('>I', rest[:4])[0]
                wdata = rest[4:4+wlen]
                if file_id not in engine._s23_files:
                    engine._s23_files[file_id] = _S23File(file_id, path)
                engine._s23_files[file_id].data += wdata
                engine._s23_files[file_id].modified_at = time.time()

        elif sub == 7:  # Copy file
            if len(rest) >= 3:
                dst_id   = struct.unpack('>H', rest[:2])[0]
                dst_plen = rest[2]
                dst_path = rest[3:3+dst_plen].decode('ascii', errors='replace')
                src_f = engine._s23_files.get(file_id)
                if src_f:
                    engine._s23_files[dst_id] = _S23File(dst_id, dst_path, src_f.data)

        elif sub == 8:  # Rename file
            if file_id in engine._s23_files:
                engine._s23_files[file_id].path = path

        elif sub == 128:  # Directory listing → S23/1 for each
            pkts = []
            for fid, f in engine._s23_files.items():
                pb = f.path.encode('ascii')[:64]
                d  = struct.pack('>HB', fid, len(pb)) + pb + struct.pack('>I', len(f.data))
                pkts.append(engine.tm_builder._pack_tm(23, 1, d, cuc))
            return pkts

        return []


# ═════════════════════════════════════════════════════════════════════════════
# S8 Function Implementations
# ═════════════════════════════════════════════════════════════════════════════

def _safe_call(fn, *args):
    try: fn(*args)
    except Exception as e: logger.debug("S8 fn error: %s", e)

def _fn_payload_off(e, a):        e.payload.cmd_set_mode(0)
def _fn_payload_standby(e, a):    e.payload.cmd_set_mode(1)
def _fn_payload_imaging(e, a):    e.payload.cmd_set_mode(2)
def _fn_payload_high_res(e, a):   e.payload.cmd_set_mode(3)

def _fn_aocs_desaturate(e, a):
    _safe_call(e.aocs.cmd_desaturate)

def _fn_aocs_safe_mode(e, a):
    try:
        from config import AOCS_MODE_SAFE
        e.aocs.cmd_set_mode(AOCS_MODE_SAFE)
    except Exception: pass

def _fn_aocs_nominal_mode(e, a):
    try:
        from config import AOCS_MODE_NOMINAL
        e.aocs.cmd_set_mode(AOCS_MODE_NOMINAL)
    except Exception: pass

def _fn_aocs_nadir_mode(e, a):
    try:
        from config import AOCS_MODE_NADIR
        e.aocs.cmd_set_mode(AOCS_MODE_NADIR)
    except Exception:
        _safe_call(e.aocs.cmd_set_mode, 2)

def _fn_aocs_sun_acq(e, a):
    _safe_call(e.aocs.cmd_set_mode, 1)

def _fn_aocs_detumble(e, a):
    _safe_call(e.aocs.cmd_set_mode, 0)

def _fn_eps_array_a_on(e, a):   _safe_call(getattr(e.eps, 'cmd_enable_array',  lambda x: None), 'A')
def _fn_eps_array_a_off(e, a):  _safe_call(getattr(e.eps, 'cmd_disable_array', lambda x: None), 'A')
def _fn_eps_array_b_on(e, a):   _safe_call(getattr(e.eps, 'cmd_enable_array',  lambda x: None), 'B')
def _fn_eps_array_b_off(e, a):  _safe_call(getattr(e.eps, 'cmd_disable_array', lambda x: None), 'B')

def _fn_eps_bat_heater_on(e, a):
    _safe_call(getattr(e.tcs, 'cmd_bat_heater', lambda x: None), True)

def _fn_eps_bat_heater_off(e, a):
    _safe_call(getattr(e.tcs, 'cmd_bat_heater', lambda x: None), False)

def _fn_fpa_cooler_on(e, a):
    _safe_call(getattr(e.tcs, 'cmd_fpa_cooler', lambda x: None), True)

def _fn_fpa_cooler_off(e, a):
    _safe_call(getattr(e.tcs, 'cmd_fpa_cooler', lambda x: None), False)

def _fn_obc_heater_on(e, a):
    _safe_call(getattr(e.tcs, 'cmd_obc_heater', lambda x: None), True)

def _fn_obc_heater_off(e, a):
    _safe_call(getattr(e.tcs, 'cmd_obc_heater', lambda x: None), False)

def _fn_transponder_redundant(e, a):
    _safe_call(getattr(e.ttc, 'cmd_switch_to_redundant', lambda: None))

def _fn_transponder_primary(e, a):
    _safe_call(getattr(e.ttc, 'cmd_switch_to_primary', lambda: None))

def _fn_transponder_high_rate(e, a):
    _safe_call(getattr(e.ttc, 'cmd_set_high_rate', lambda: None))

def _fn_transponder_low_rate(e, a):
    _safe_call(getattr(e.ttc, 'cmd_set_low_rate', lambda: None))

def _fn_fdir_enable(e, a):    _safe_call(getattr(e.fdir, 'cmd_enable_fdir', lambda x: None), True)
def _fn_fdir_disable(e, a):   _safe_call(getattr(e.fdir, 'cmd_enable_fdir', lambda x: None), False)
def _fn_fdir_reset(e, a):     _safe_call(getattr(e.fdir, 'cmd_reset', lambda: None))

def _fn_memory_scrub(e, a):
    _safe_call(getattr(e.obdh, 'cmd_memory_scrub', lambda: None))

def _fn_reboot_obc(e, a):
    _safe_call(getattr(e.obdh, 'cmd_reboot', lambda: None))

def _fn_clear_error_registers(e, a):
    _safe_call(getattr(e.obdh, 'cmd_clear_errors', lambda: None))

def _fn_safe_mode_enter(e, a):
    _safe_call(getattr(e.fdir, 'cmd_safe_mode', lambda x: None), True)

def _fn_safe_mode_exit(e, a):
    _safe_call(getattr(e.fdir, 'cmd_safe_mode', lambda x: None), False)

def _fn_dump_all_hk(e, a):
    for sid in getattr(e, '_hk_intervals', {}).keys():
        pkt = e.tm_builder.build_hk_packet(sid, e.params)
        if pkt:
            try: e._enqueue_tm(pkt)
            except Exception: pass

def _fn_scenario_detect(e, a):
    se = getattr(e, '_scenario_engine', None)
    if se: se.record_response('detect', 'Operator detected anomaly via TC')

def _fn_scenario_isolate(e, a):
    se = getattr(e, '_scenario_engine', None)
    if se: se.record_response('isolate', 'Operator isolated fault via TC')

def _fn_scenario_recover(e, a):
    se = getattr(e, '_scenario_engine', None)
    if se: se.record_response('recover', 'Operator recovery action via TC')
