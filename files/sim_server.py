#!/usr/bin/env python3
"""
EO Mission Simulator — Multi-Position MCS Server

Ports:
  8001  TC  uplink        raw TCP, 2-byte big-endian length prefix + ECSS packet
  8002  TM  downlink      same framing
  8003  Instructor        raw TCP, JSON newline-delimited
  8080  MCS Web            HTTP GET / → nav hub
                           HTTP GET /{pos} → position HTML
                           HTTP GET /ws → WebSocket JSON bridge
                           HTTP GET /catalog → JSON failure+TC catalog
"""
import asyncio, json, logging, os, signal, struct as _struct, sys
from pathlib import Path

import aiohttp
from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ecss_decommutator import ECSSDecommutator
from engine import SimulationEngine
from config import TC_PORT, TM_PORT, INSTR_PORT, TM_MAX_CLIENTS

MCS_PORT = 8080
PAGE_DIR = Path(__file__).parent

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s — %(message)s')
logger = logging.getLogger('sim_server')

# ═══════════════════════════════════════════════════════════════════════════
# Position page map
# ═══════════════════════════════════════════════════════════════════════════
POSITIONS = {
    'fd':      ('fd.html',      'Flight Dynamics'),
    'eps':     ('eps.html',     'Power & Thermal'),
    'ttc':     ('ttc.html',     'TT&C / Comms'),
    'payload': ('payload.html', 'Payload Ops'),
    'sys':     ('sys.html',     'FDIR / Systems'),
    'fdir':    ('fdir.html',    'Flight Director'),
    'instr':   ('instr.html',   'Instructor / SIM CTRL'),
}

# ═══════════════════════════════════════════════════════════════════════════
# Failure catalog
# ═══════════════════════════════════════════════════════════════════════════
FAILURE_CATALOG = {
    'eps':     ['solar_array_partial','bat_cell','bus_short','sa_a_failed','sa_b_failed','over_discharge'],
    'aocs':    ['rw_bearing','rw_seizure','gyro_bias','st_blind','rw_all_failed','large_att_error'],
    'tcs':     ['heater_failure','cooler_failure','obc_thermal','fpa_overtemp','bat_overtemp'],
    'obdh':    ['watchdog_reset','memory_errors','cpu_overload','reboot_loop'],
    'ttc':     ['primary_failure','redundant_failure','ranging_failure','link_dropout'],
    'payload': ['cooler_failure','fpa_degraded','store_full','checksum_errors'],
    'fdir':    ['safe_mode_false_trigger','recovery_loop'],
}

# ═══════════════════════════════════════════════════════════════════════════
# Full TC catalog — all 17 PUS services, key subtypes
# ═══════════════════════════════════════════════════════════════════════════
def _f(n, t, d, label): return dict(n=n, t=t, d=d, label=label)
U8, U16, U32, F32, STR = 'uint8','uint16','uint32','float','str'

TC_CATALOG = [
  # ── S1 TC Verification ──────────────────────────────────────────────────
  dict(service=1,  subtype=1,  label='S1/1  Enable Acceptance Reporting',    position='sys',  fields=[_f('apid_filter',U16,0,'APID Filter (0=all)')]),
  dict(service=1,  subtype=2,  label='S1/2  Disable Acceptance Reporting',   position='sys',  fields=[_f('apid_filter',U16,0,'APID Filter (0=all)')]),
  # ── S2 Device Access ────────────────────────────────────────────────────
  dict(service=2,  subtype=1,  label='S2/1  Raw Device Command',             position='sys',  fields=[_f('device_id',U16,1,'Device ID'), _f('raw_hex',STR,'','Hex Data')]),
  dict(service=2,  subtype=2,  label='S2/2  Read Device Register',           position='sys',  fields=[_f('device_id',U16,1,'Device ID'), _f('raw_hex',STR,'00','Reg Addr Hex')]),
  dict(service=2,  subtype=3,  label='S2/3  Write Device Register',          position='sys',  fields=[_f('device_id',U16,1,'Device ID'), _f('raw_hex',STR,'','Hex Data')]),
  # ── S3 Housekeeping ─────────────────────────────────────────────────────
  dict(service=3,  subtype=1,  label='S3/1  Enable HK Collection',           position='sys',  fields=[_f('structure_id',U16,1,'Structure ID'), _f('interval_s',U32,30,'Interval (s)')]),
  dict(service=3,  subtype=2,  label='S3/2  Disable HK Collection',          position='sys',  fields=[_f('structure_id',U16,1,'Structure ID')]),
  dict(service=3,  subtype=3,  label='S3/3  Enable Diagnostic Collection',   position='sys',  fields=[_f('structure_id',U16,2,'Structure ID'), _f('interval_s',U32,10,'Interval (s)')]),
  dict(service=3,  subtype=4,  label='S3/4  Disable Diagnostic Collection',  position='sys',  fields=[_f('structure_id',U16,2,'Structure ID')]),
  dict(service=3,  subtype=7,  label='S3/7  One-Shot HK Report',             position='fdir', fields=[_f('structure_id',U16,1,'Structure ID')]),
  dict(service=3,  subtype=10, label='S3/10 Modify HK Interval',             position='sys',  fields=[_f('structure_id',U16,1,'Structure ID'), _f('interval_s',U32,60,'New Interval (s)')]),
  dict(service=3,  subtype=12, label='S3/12 Report All HK Structures',       position='sys',  fields=[_f('structure_id',U16,0,'(unused)')]),
  # ── S4 Parameter Statistics ─────────────────────────────────────────────
  dict(service=4,  subtype=1,  label='S4/1  Enable Statistics Reporting',    position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID'), _f('interval_s',U16,60,'Interval (s)')]),
  dict(service=4,  subtype=2,  label='S4/2  Disable Statistics Reporting',   position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  dict(service=4,  subtype=3,  label='S4/3  Request Statistics Report',      position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  # ── S5 Event Reporting ──────────────────────────────────────────────────
  dict(service=5,  subtype=5,  label='S5/5  Enable Event Reporting',         position='sys',  fields=[_f('event_id',U16,0,'Event ID (0=all)')]),
  dict(service=5,  subtype=6,  label='S5/6  Disable Event Reporting',        position='sys',  fields=[_f('event_id',U16,0,'Event ID (0=all)')]),
  dict(service=5,  subtype=7,  label='S5/7  Clear Event Log',                position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=5,  subtype=8,  label='S5/8  Report Event Log',               position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S6 Memory Management ────────────────────────────────────────────────
  dict(service=6,  subtype=2,  label='S6/2  Memory Load',                    position='sys',  fields=[_f('memory_id',U16,1,'Memory ID'), _f('start_address',U32,0x10000000,'Address'), _f('length',U32,16,'Length'), _f('raw_hex',STR,'','Data Hex')]),
  dict(service=6,  subtype=5,  label='S6/5  Memory Dump Request',            position='sys',  fields=[_f('memory_id',U16,1,'Memory ID'), _f('start_address',U32,0x10000000,'Address'), _f('length',U32,256,'Length (bytes)')]),
  dict(service=6,  subtype=9,  label='S6/9  Memory Checksum',                position='sys',  fields=[_f('memory_id',U16,1,'Memory ID'), _f('start_address',U32,0x10000000,'Address'), _f('length',U32,1024,'Length')]),
  # ── S7 OBL Monitoring ───────────────────────────────────────────────────
  dict(service=7,  subtype=1,  label='S7/1  Create OBL Table',               position='sys',  fields=[_f('table_id',U16,1,'Table ID')]),
  dict(service=7,  subtype=2,  label='S7/2  Delete OBL Table',               position='sys',  fields=[_f('table_id',U16,1,'Table ID')]),
  dict(service=7,  subtype=3,  label='S7/3  Activate OBL Table',             position='sys',  fields=[_f('table_id',U16,1,'Table ID')]),
  dict(service=7,  subtype=4,  label='S7/4  Suspend OBL Table',              position='sys',  fields=[_f('table_id',U16,1,'Table ID')]),
  dict(service=7,  subtype=128,label='S7/128 OBL Status Report',             position='sys',  fields=[_f('table_id',U16,0,'(unused)')]),
  # ── S8 Function Management ──────────────────────────────────────────────
  dict(service=8,  subtype=1,  label='S8/1  Payload OFF  [0x0001]',          position='payload', fields=[_f('function_id',U16,0x0001,'Fn ID (0x0001)')]),
  dict(service=8,  subtype=1,  label='S8/1  Payload STANDBY [0x0002]',       position='payload', fields=[_f('function_id',U16,0x0002,'Fn ID (0x0002)')]),
  dict(service=8,  subtype=1,  label='S8/1  Payload IMAGING [0x0003]',       position='payload', fields=[_f('function_id',U16,0x0003,'Fn ID (0x0003)')]),
  dict(service=8,  subtype=1,  label='S8/1  Payload HIGH-RES [0x0004]',      position='payload', fields=[_f('function_id',U16,0x0004,'Fn ID (0x0004)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Desaturate  [0x0010]',      position='fd',      fields=[_f('function_id',U16,0x0010,'Fn ID (0x0010)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Safe Mode   [0x0011]',      position='fd',      fields=[_f('function_id',U16,0x0011,'Fn ID (0x0011)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Nominal     [0x0012]',      position='fd',      fields=[_f('function_id',U16,0x0012,'Fn ID (0x0012)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Nadir Pt    [0x0013]',      position='fd',      fields=[_f('function_id',U16,0x0013,'Fn ID (0x0013)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Sun Acq     [0x0014]',      position='fd',      fields=[_f('function_id',U16,0x0014,'Fn ID (0x0014)')]),
  dict(service=8,  subtype=1,  label='S8/1  AOCS Detumble    [0x0015]',      position='fd',      fields=[_f('function_id',U16,0x0015,'Fn ID (0x0015)')]),
  dict(service=8,  subtype=1,  label='S8/1  SA-A ON          [0x0020]',      position='eps',     fields=[_f('function_id',U16,0x0020,'Fn ID (0x0020)')]),
  dict(service=8,  subtype=1,  label='S8/1  SA-A OFF         [0x0021]',      position='eps',     fields=[_f('function_id',U16,0x0021,'Fn ID (0x0021)')]),
  dict(service=8,  subtype=1,  label='S8/1  SA-B ON          [0x0022]',      position='eps',     fields=[_f('function_id',U16,0x0022,'Fn ID (0x0022)')]),
  dict(service=8,  subtype=1,  label='S8/1  SA-B OFF         [0x0023]',      position='eps',     fields=[_f('function_id',U16,0x0023,'Fn ID (0x0023)')]),
  dict(service=8,  subtype=1,  label='S8/1  Bat Heater ON    [0x0024]',      position='eps',     fields=[_f('function_id',U16,0x0024,'Fn ID (0x0024)')]),
  dict(service=8,  subtype=1,  label='S8/1  Bat Heater OFF   [0x0025]',      position='eps',     fields=[_f('function_id',U16,0x0025,'Fn ID (0x0025)')]),
  dict(service=8,  subtype=1,  label='S8/1  FPA Cooler ON    [0x0030]',      position='payload', fields=[_f('function_id',U16,0x0030,'Fn ID (0x0030)')]),
  dict(service=8,  subtype=1,  label='S8/1  FPA Cooler OFF   [0x0031]',      position='payload', fields=[_f('function_id',U16,0x0031,'Fn ID (0x0031)')]),
  dict(service=8,  subtype=1,  label='S8/1  OBC Heater ON    [0x0032]',      position='eps',     fields=[_f('function_id',U16,0x0032,'Fn ID (0x0032)')]),
  dict(service=8,  subtype=1,  label='S8/1  OBC Heater OFF   [0x0033]',      position='eps',     fields=[_f('function_id',U16,0x0033,'Fn ID (0x0033)')]),
  dict(service=8,  subtype=1,  label='S8/1  TXer → Redundant [0x0040]',      position='ttc',     fields=[_f('function_id',U16,0x0040,'Fn ID (0x0040)')]),
  dict(service=8,  subtype=1,  label='S8/1  TXer → Primary   [0x0041]',      position='ttc',     fields=[_f('function_id',U16,0x0041,'Fn ID (0x0041)')]),
  dict(service=8,  subtype=1,  label='S8/1  TXer High Rate   [0x0042]',      position='ttc',     fields=[_f('function_id',U16,0x0042,'Fn ID (0x0042)')]),
  dict(service=8,  subtype=1,  label='S8/1  TXer Low Rate    [0x0043]',      position='ttc',     fields=[_f('function_id',U16,0x0043,'Fn ID (0x0043)')]),
  dict(service=8,  subtype=1,  label='S8/1  FDIR Enable      [0x0050]',      position='sys',     fields=[_f('function_id',U16,0x0050,'Fn ID (0x0050)')]),
  dict(service=8,  subtype=1,  label='S8/1  FDIR Disable     [0x0051]',      position='sys',     fields=[_f('function_id',U16,0x0051,'Fn ID (0x0051)')]),
  dict(service=8,  subtype=1,  label='S8/1  FDIR Reset       [0x0052]',      position='sys',     fields=[_f('function_id',U16,0x0052,'Fn ID (0x0052)')]),
  dict(service=8,  subtype=1,  label='S8/1  Memory Scrub     [0x0060]',      position='sys',     fields=[_f('function_id',U16,0x0060,'Fn ID (0x0060)')]),
  dict(service=8,  subtype=1,  label='S8/1  Reboot OBC       [0x0061]',      position='sys',     fields=[_f('function_id',U16,0x0061,'Fn ID (0x0061)')]),
  dict(service=8,  subtype=1,  label='S8/1  Clear Errors     [0x0062]',      position='sys',     fields=[_f('function_id',U16,0x0062,'Fn ID (0x0062)')]),
  dict(service=8,  subtype=1,  label='S8/1  Safe Mode ENTER  [0x0080]',      position='fdir',    fields=[_f('function_id',U16,0x0080,'Fn ID (0x0080)')]),
  dict(service=8,  subtype=1,  label='S8/1  Safe Mode EXIT   [0x0081]',      position='fdir',    fields=[_f('function_id',U16,0x0081,'Fn ID (0x0081)')]),
  dict(service=8,  subtype=1,  label='S8/1  Dump All HK      [0x0090]',      position='instr',   fields=[_f('function_id',U16,0x0090,'Fn ID (0x0090)')]),
  dict(service=8,  subtype=1,  label='S8/1  Scenario Detect  [0x0070]',      position='instr',   fields=[_f('function_id',U16,0x0070,'Fn ID (0x0070)')]),
  dict(service=8,  subtype=1,  label='S8/1  Scenario Isolate [0x0071]',      position='instr',   fields=[_f('function_id',U16,0x0071,'Fn ID (0x0071)')]),
  dict(service=8,  subtype=1,  label='S8/1  Scenario Recover [0x0072]',      position='instr',   fields=[_f('function_id',U16,0x0072,'Fn ID (0x0072)')]),
  # ── S9 Time Management ──────────────────────────────────────────────────
  dict(service=9,  subtype=1,  label='S9/1  Set Spacecraft Time',            position='sys',  fields=[_f('time_seconds',U32,0,'Seconds since J2000')]),
  dict(service=9,  subtype=2,  label='S9/2  Request Time Correlation',       position='sys',  fields=[_f('ref_cuc',U32,0,'Reference CUC (0=now)')]),
  dict(service=9,  subtype=128,label='S9/128 Report Current Time',           position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S10 External Device ─────────────────────────────────────────────────
  dict(service=10, subtype=2,  label='S10/2 Activate External Device',       position='sys',  fields=[_f('device_id',U16,1,'Device ID')]),
  dict(service=10, subtype=3,  label='S10/3 Deactivate External Device',     position='sys',  fields=[_f('device_id',U16,1,'Device ID')]),
  dict(service=10, subtype=128,label='S10/128 Device Status Report',         position='sys',  fields=[_f('device_id',U16,1,'Device ID')]),
  # ── S11 Time-Based Scheduling ───────────────────────────────────────────
  dict(service=11, subtype=1,  label='S11/1 Enable Scheduling',              position='instr', fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=11, subtype=2,  label='S11/2 Disable Scheduling',             position='instr', fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=11, subtype=3,  label='S11/3 Reset Schedule',                 position='instr', fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=11, subtype=4,  label='S11/4 Insert Time-Tagged TC',          position='instr', fields=[_f('exec_cuc',U32,0,'Exec CUC Time'), _f('tc_service',U8,17,'TC Service'), _f('tc_subtype',U8,1,'TC Subtype')]),
  dict(service=11, subtype=5,  label='S11/5 Delete Time-Tagged TC',          position='instr', fields=[_f('cmd_id',U16,1,'Command ID')]),
  dict(service=11, subtype=7,  label='S11/7 Time-Shift Schedule',            position='instr', fields=[_f('delta_cuc',U32,0,'Delta CUC (signed)')]),
  dict(service=11, subtype=128,label='S11/128 Report Schedule',              position='instr', fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S12 Parameter Monitoring ─────────────────────────────────────────────
  dict(service=12, subtype=1,  label='S12/1 Enable Parameter Monitoring',    position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  dict(service=12, subtype=2,  label='S12/2 Disable Parameter Monitoring',   position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  dict(service=12, subtype=3,  label='S12/3 Enable All Monitoring',          position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=12, subtype=4,  label='S12/4 Disable All Monitoring',         position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=12, subtype=5,  label='S12/5 Set Limit Check',                position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID'), _f('yellow_lo',F32,-40.0,'Yellow Lo'), _f('yellow_hi',F32,85.0,'Yellow Hi'), _f('red_lo',F32,-50.0,'Red Lo'), _f('red_hi',F32,95.0,'Red Hi'), _f('interval',U16,10,'Check Interval (s)')]),
  dict(service=12, subtype=7,  label='S12/7 Delete Limit Check',             position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  dict(service=12, subtype=9,  label='S12/9 Report Monitoring Definitions',  position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=12, subtype=10, label='S12/10 Report Out-of-Limit Params',    position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S13 Large Data Transfer ──────────────────────────────────────────────
  dict(service=13, subtype=2,  label='S13/2 Request Data Downlink',          position='payload', fields=[_f('transfer_id',U16,1,'Transfer ID'), _f('total_size',U32,4096,'Total Size (bytes)')]),
  dict(service=13, subtype=16, label='S13/16 Abort Data Transfer',           position='payload', fields=[_f('transfer_id',U16,1,'Transfer ID')]),
  # ── S14 Real-Time Forwarding ─────────────────────────────────────────────
  dict(service=14, subtype=1,  label='S14/1 Enable VC Forwarding',           position='ttc',  fields=[_f('vc_id',U8,0,'VC ID')]),
  dict(service=14, subtype=2,  label='S14/2 Disable VC Forwarding',          position='ttc',  fields=[_f('vc_id',U8,0,'VC ID')]),
  dict(service=14, subtype=5,  label='S14/5 Report Forwarding Status',       position='ttc',  fields=[_f('vc_id',U8,0,'VC ID (0=all)')]),
  # ── S15 On-Board Storage ─────────────────────────────────────────────────
  dict(service=15, subtype=1,  label='S15/1 Create Storage Session',         position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=2,  label='S15/2 Suspend Storage',                position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=3,  label='S15/3 Resume Storage',                 position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=4,  label='S15/4 Delete Storage Contents',        position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=5,  label='S15/5 Delete Storage Session',         position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=6,  label='S15/6 Copy to Downlink',               position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  dict(service=15, subtype=9,  label='S15/9 Storage Status Report',          position='payload', fields=[_f('session_id',U16,1,'Session ID')]),
  # ── S16 Packet Selection ─────────────────────────────────────────────────
  dict(service=16, subtype=1,  label='S16/1 Add to Downlink Selection',      position='ttc',  fields=[_f('sel_id',U16,1,'Selection ID'), _f('n_apids',U8,1,'Num APIDs'), _f('apid_0',U16,1,'APID 0')]),
  dict(service=16, subtype=2,  label='S16/2 Delete Selection',               position='ttc',  fields=[_f('sel_id',U16,1,'Selection ID')]),
  dict(service=16, subtype=3,  label='S16/3 Activate Selection',             position='ttc',  fields=[_f('sel_id',U16,1,'Selection ID')]),
  dict(service=16, subtype=4,  label='S16/4 Deactivate Selection',           position='ttc',  fields=[_f('sel_id',U16,1,'Selection ID')]),
  dict(service=16, subtype=128,label='S16/128 Report Selection Status',      position='ttc',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S17 Test ─────────────────────────────────────────────────────────────
  dict(service=17, subtype=1,  label='S17/1 Connection Test (AYA)',          position='fdir', fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=17, subtype=2,  label='S17/2 On-Board Connection Test',       position='fdir', fields=[_f('test_id',U16,1,'Test ID')]),
  # ── S18 On-Board Control Procedures ─────────────────────────────────────
  dict(service=18, subtype=1,  label='S18/1 Load Procedure',                 position='instr', fields=[_f('proc_id',U16,1,'Proc ID'), _f('proc_name',STR,'SAFE_MODE','Name')]),
  dict(service=18, subtype=2,  label='S18/2 Unload Procedure',               position='instr', fields=[_f('proc_id',U16,1,'Proc ID')]),
  dict(service=18, subtype=3,  label='S18/3 Activate Procedure',             position='instr', fields=[_f('proc_id',U16,1,'Proc ID')]),
  dict(service=18, subtype=4,  label='S18/4 Suspend Procedure',              position='instr', fields=[_f('proc_id',U16,1,'Proc ID')]),
  dict(service=18, subtype=5,  label='S18/5 Resume Procedure',               position='instr', fields=[_f('proc_id',U16,1,'Proc ID')]),
  dict(service=18, subtype=6,  label='S18/6 Abort Procedure',                position='instr', fields=[_f('proc_id',U16,1,'Proc ID')]),
  dict(service=18, subtype=128,label='S18/128 Procedure Status Report',      position='instr', fields=[_f('proc_id',U16,0,'(unused)')]),
  # ── S19 Event-Action ─────────────────────────────────────────────────────
  dict(service=19, subtype=1,  label='S19/1 Add Event-Action',               position='sys',  fields=[_f('event_id',U16,0x1001,'Event ID'), _f('action_id',U16,1,'Action ID'), _f('enabled',U8,1,'Enabled (0/1)')]),
  dict(service=19, subtype=2,  label='S19/2 Delete Event-Action',            position='sys',  fields=[_f('action_id',U16,1,'Action ID')]),
  dict(service=19, subtype=4,  label='S19/4 Enable Event-Action',            position='sys',  fields=[_f('action_id',U16,1,'Action ID')]),
  dict(service=19, subtype=5,  label='S19/5 Disable Event-Action',           position='sys',  fields=[_f('action_id',U16,1,'Action ID')]),
  dict(service=19, subtype=6,  label='S19/6 Enable All Event-Actions',       position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=19, subtype=7,  label='S19/7 Disable All Event-Actions',      position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=19, subtype=8,  label='S19/8 Report All Event-Actions',       position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S20 Parameter Management ─────────────────────────────────────────────
  dict(service=20, subtype=1,  label='S20/1 Set Parameter Value',            position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID'), _f('param_type',U8,2,'Type (1=int 2=float)'), _f('parameter_value',F32,0.0,'Value')]),
  dict(service=20, subtype=2,  label='S20/2 Report Parameter Value',         position='sys',  fields=[_f('parameter_id',U16,0x0101,'Param ID')]),
  dict(service=20, subtype=128,label='S20/128 Report All Parameters',        position='sys',  fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S21 Request Sequencing ───────────────────────────────────────────────
  dict(service=21, subtype=1,  label='S21/1 Enable Sequence',                position='instr', fields=[_f('seq_id',U16,1,'Sequence ID')]),
  dict(service=21, subtype=2,  label='S21/2 Disable Sequence',               position='instr', fields=[_f('seq_id',U16,1,'Sequence ID')]),
  dict(service=21, subtype=3,  label='S21/3 Abort Sequence',                 position='instr', fields=[_f('seq_id',U16,1,'Sequence ID')]),
  dict(service=21, subtype=4,  label='S21/4 Report All Sequences',           position='instr', fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S22 Position-Based Scheduling ───────────────────────────────────────
  dict(service=22, subtype=1,  label='S22/1 Enable Position Scheduling',     position='fd',   fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=22, subtype=2,  label='S22/2 Disable Position Scheduling',    position='fd',   fields=[_f('dummy',U16,0,'(no fields)')]),
  dict(service=22, subtype=3,  label='S22/3 Insert Position-Tagged TC',      position='fd',   fields=[_f('latitude',F32,0.0,'Target Lat (deg)'), _f('longitude',F32,0.0,'Target Lon (deg)'), _f('altitude',F32,500.0,'Target Alt (km)'), _f('tc_service',U8,8,'TC Service'), _f('tc_subtype',U8,1,'TC Subtype')]),
  dict(service=22, subtype=4,  label='S22/4 Delete Position-Tagged TC',      position='fd',   fields=[_f('entry_id',U16,1,'Entry ID')]),
  dict(service=22, subtype=128,label='S22/128 Report Position Schedule',     position='fd',   fields=[_f('dummy',U16,0,'(no fields)')]),
  # ── S23 File Management ──────────────────────────────────────────────────
  dict(service=23, subtype=1,  label='S23/1 Create File',                    position='payload', fields=[_f('file_id',U16,1,'File ID'), _f('file_path',STR,'/data/img001.raw','Path')]),
  dict(service=23, subtype=2,  label='S23/2 Delete File',                    position='payload', fields=[_f('file_id',U16,1,'File ID'), _f('file_path',STR,'/data/img001.raw','Path')]),
  dict(service=23, subtype=3,  label='S23/3 Report File Attributes',         position='payload', fields=[_f('file_id',U16,1,'File ID'), _f('file_path',STR,'/data/img001.raw','Path')]),
  dict(service=23, subtype=5,  label='S23/5 Read File',                      position='payload', fields=[_f('file_id',U16,1,'File ID'), _f('file_path',STR,'/data/img001.raw','Path')]),
  dict(service=23, subtype=7,  label='S23/7 Copy File',                      position='payload', fields=[_f('file_id',U16,1,'Src File ID'), _f('file_path',STR,'/data/src.raw','Src Path'), _f('dst_id',U16,2,'Dst File ID'), _f('dst_path',STR,'/data/dst.raw','Dst Path')]),
  dict(service=23, subtype=8,  label='S23/8 Rename File',                    position='payload', fields=[_f('file_id',U16,1,'File ID'), _f('file_path',STR,'/data/new_name.raw','New Path')]),
  dict(service=23, subtype=128,label='S23/128 Directory Listing',            position='payload', fields=[_f('file_id',U16,0,'(unused)'), _f('file_path',STR,'/data/','Path')]),
]


# ═══════════════════════════════════════════════════════════════════════════
# TC packet builder + encoder
# ═══════════════════════════════════════════════════════════════════════════

def build_tc_packet(apid, service, subtype, data, seq_count=0):
    sec     = bytes([0x10, service, subtype])
    pkt_id  = (1 << 12) | (1 << 11) | (apid & 0x7FF)
    seq_ctl = (0b01 << 14) | (seq_count & 0x3FFF)
    pri     = _struct.pack('>HHH', pkt_id, seq_ctl, len(sec)+len(data)-1)
    raw     = pri + sec + data
    return raw + _struct.pack('>H', sum(raw) & 0xFFFF)


def encode_tc_data(service, subtype, params):
    s, sub, p = service, subtype, params
    d = b''

    def u8(k,dv=0):  return _struct.pack('>B', int(p.get(k,dv)))
    def u16(k,dv=0): return _struct.pack('>H', int(p.get(k,dv)))
    def u32(k,dv=0): return _struct.pack('>I', int(p.get(k,dv)))
    def f32(k,dv=0): return _struct.pack('>f', float(p.get(k,dv)))
    def raw(k='raw_hex'):
        v = p.get(k,'')
        return bytes.fromhex(v.replace(' ','')) if v else b''

    if s == 1:   d = u16('apid_filter')
    elif s == 2: d = u16('device_id') + raw()
    elif s == 3:
        d = u16('structure_id')
        if sub in (1,3): d += u32('interval_s',30)
        elif sub == 10:  d += u32('interval_s',60)
    elif s == 4: d = u16('parameter_id') + (u16('interval_s',60) if sub==1 else b'')
    elif s == 5: d = u16('event_id') if sub in (5,6) else b''
    elif s == 6:
        d = u16('memory_id') + u32('start_address',0x10000000) + u32('length',256)
        if sub == 2: d += raw()
    elif s == 7:   d = u16('table_id')
    elif s == 8:
        d = u16('function_id')
        for k in ('arg0','arg1','arg2'):
            if p.get(k) not in (None,''): d += u32(k)
    elif s == 9:
        if sub == 1:   d = _struct.pack('>BI', 1, int(p.get('time_seconds',0)))
        elif sub == 2: d = u32('ref_cuc')
    elif s == 10:  d = u16('device_id')
    elif s == 11:
        if sub == 4:
            d  = u32('exec_cuc')
            # Build a minimal TC for the embedded command
            inner_svc = int(p.get('tc_service',17))
            inner_sub = int(p.get('tc_subtype',1))
            inner_pkt = build_tc_packet(1, inner_svc, inner_sub, b'\x00\x00')
            d += _struct.pack('>H', len(inner_pkt)) + inner_pkt
        elif sub == 5: d = u16('cmd_id')
        elif sub == 7: d = _struct.pack('>i', int(p.get('delta_cuc',0)))
        else:          d = b''
    elif s == 12:
        if sub in (1,2,7): d = u16('parameter_id')
        elif sub == 5:
            d = u16('parameter_id') + f32('yellow_lo') + f32('yellow_hi') + \
                f32('red_lo') + f32('red_hi') + u16('interval',10)
    elif s == 13:
        d = u16('transfer_id')
        if sub == 2: d += u32('total_size',4096)
    elif s == 14:  d = u8('vc_id')
    elif s == 15:  d = u16('session_id')
    elif s == 16:
        d = u16('sel_id')
        if sub == 1:
            n = int(p.get('n_apids',1))
            d += _struct.pack('>B', n)
            for i in range(n):
                d += u16(f'apid_{i}')
    elif s == 17:
        if sub == 2: d = u16('test_id')
    elif s == 18:
        d = u16('proc_id')
        if sub == 1:
            name = str(p.get('proc_name','PROC')).encode('ascii')[:32]
            d += name
    elif s == 19:
        if sub == 1:
            d = u16('event_id') + u16('action_id') + u8('enabled',1)
        elif sub in (2,4,5): d = u16('action_id')
    elif s == 20:
        if sub == 1:
            ptype = int(p.get('param_type',2))
            val   = float(p.get('parameter_value',0))
            d = u16('parameter_id') + _struct.pack('>B',ptype)
            d += _struct.pack('>I',int(val)) if ptype==1 else _struct.pack('>f',val)
        elif sub in (2,3): d = u16('parameter_id')
    elif s == 21:  d = u16('seq_id') if sub != 4 else b''
    elif s == 22:
        if sub == 3:
            inner_svc = int(p.get('tc_service',8))
            inner_sub = int(p.get('tc_subtype',1))
            inner_pkt = build_tc_packet(1,inner_svc,inner_sub,b'\x00\x00')
            d = f32('latitude') + f32('longitude') + f32('altitude') + \
                _struct.pack('>H',len(inner_pkt)) + inner_pkt
        elif sub == 4: d = u16('entry_id')
    elif s == 23:
        fid      = int(p.get('file_id',1))
        path     = str(p.get('file_path','/data/')).encode('ascii')[:128]
        d = _struct.pack('>HB', fid, len(path)) + path
        if sub == 7:   # copy — add destination
            dst_id   = int(p.get('dst_id',2))
            dst_path = str(p.get('dst_path','/data/dst.raw')).encode('ascii')[:128]
            d += _struct.pack('>HB', dst_id, len(dst_path)) + dst_path

    return d


# ═══════════════════════════════════════════════════════════════════════════
# SimServer
# ═══════════════════════════════════════════════════════════════════════════

class SimServer:
    def __init__(self, engine):
        self.engine          = engine
        self._decom          = ECSSDecommutator()
        self._tm_clients:    set = set()
        self._instr_clients: set = set()
        self._ws_clients:    set = set()
        self._tc_seq:        int = 0

    # ── WS helpers ────────────────────────────────────────────────────────
    async def _ws_send(self, ws, msg):
        try: await ws.send_str(msg); return True
        except Exception: return False

    async def _ws_broadcast(self, msg):
        dead = set()
        for ws in list(self._ws_clients):
            if not await self._ws_send(ws, msg): dead.add(ws)
        self._ws_clients -= dead

    # ── HTTP handlers ─────────────────────────────────────────────────────
    async def handle_nav(self, req):
        """Serve a position navigation hub page."""
        links = ''.join(
            f'<a href="/{k}" class="card"><div class="pos">{v[1]}</div>'
            f'<div class="url">/{k}</div></a>'
            for k,v in POSITIONS.items()
        )
        html = f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>EOSAT-1 MCS</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{background:#060b10;color:#7cf;font-family:monospace;display:flex;
        flex-direction:column;align-items:center;justify-content:center;min-height:100vh}}
  h1{{font-size:2em;letter-spacing:.2em;margin-bottom:.3em;color:#0ff;text-transform:uppercase}}
  p{{color:#456;margin-bottom:2em;font-size:.9em}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1em;max-width:900px;width:100%;padding:0 1em}}
  .card{{display:block;background:#0d1a24;border:1px solid #1a3a4a;border-radius:6px;
          padding:1.5em 1em;text-decoration:none;transition:all .2s;text-align:center}}
  .card:hover{{border-color:#0ff;background:#0d2535;transform:translateY(-2px)}}
  .pos{{font-size:1em;color:#0ff;font-weight:bold;margin-bottom:.4em;text-transform:uppercase}}
  .url{{font-size:.75em;color:#456}}
</style></head><body>
<h1>EOSAT-1 MCS</h1>
<p>Select operator position</p>
<div class="grid">{links}</div>
</body></html>"""
        return web.Response(text=html, content_type='text/html')

    async def handle_position(self, req):
        pos  = req.match_info['pos']
        info = POSITIONS.get(pos)
        if not info:
            return web.Response(status=404, text='Position not found')
        fname = PAGE_DIR / info[0]
        if fname.exists():
            return web.FileResponse(fname)
        return web.Response(status=503,
            text=f'<h2>{info[1]} display not yet built — {info[0]}</h2>',
            content_type='text/html')

    async def handle_catalog(self, req):
        pos = req.query.get('pos')    # optional filter by position
        tc  = TC_CATALOG if not pos else [t for t in TC_CATALOG if t.get('position','') == pos]
        return web.json_response({'failures': FAILURE_CATALOG, 'tc': tc})

    async def handle_ws(self, req):
        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(req)
        self._ws_clients.add(ws)
        logger.info('WS client: %s', req.remote)
        try:
            state = self.engine.get_state_summary()
            await ws.send_str(json.dumps({'type':'state','data':state}))
        except Exception: pass
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_msg(ws, msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.discard(ws)
        return ws

    async def _handle_ws_msg(self, ws, raw):
        try: msg = json.loads(raw)
        except Exception: return
        t = msg.get('type','')
        if t == 'instr':
            try: self.engine.instr_queue.put_nowait(msg.get('cmd',{}))
            except Exception: pass
        elif t == 'tc':
            try:
                svc  = int(msg.get('service',17))
                sub  = int(msg.get('subtype',1))
                apid = int(msg.get('apid',1))
                data = encode_tc_data(svc, sub, msg.get('params',{}))
                self._tc_seq = (self._tc_seq + 1) & 0x3FFF
                pkt  = build_tc_packet(apid, svc, sub, data, self._tc_seq)
                self.engine.tc_queue.put_nowait(pkt)
                await self._ws_send(ws, json.dumps({
                    'type':'tc_ack','seq':self._tc_seq,
                    'service':svc,'subtype':sub,'apid':apid,
                    'len':len(pkt),'hex':pkt.hex()}))
            except Exception as e:
                await self._ws_send(ws, json.dumps({'type':'tc_err','error':str(e)}))
        elif t == 'ping':
            await self._ws_send(ws, json.dumps({'type':'pong'}))

    # ── Periodic tasks ────────────────────────────────────────────────────
    async def _broadcast_state(self):
        while True:
            await asyncio.sleep(1.0)
            try:
                state   = self.engine.get_state_summary()
                ws_msg  = json.dumps({'type':'state','data':state})
                tcp_msg = (json.dumps(state)+'\n').encode()
                if self._ws_clients:
                    await self._ws_broadcast(ws_msg)
                dead = set()
                for w in list(self._instr_clients):
                    try:    w.write(tcp_msg); await w.drain()
                    except: dead.add(w)
                self._instr_clients -= dead
            except Exception as e:
                logger.debug('State broadcast: %s', e)

    async def _broadcast_tm(self):
        while True:
            await asyncio.sleep(0.01)
            while not self.engine.tm_queue.empty():
                try:    pkt = self.engine.tm_queue.get_nowait()
                except: break
                if self._tm_clients:
                    frame = _struct.pack('>H',len(pkt)) + pkt
                    dead  = set()
                    for w in list(self._tm_clients):
                        try: w.write(frame); await w.drain()
                        except: dead.add(w)
                    for w in dead:
                        self._tm_clients.discard(w)
                        try: w.close()
                        except: pass
                if self._ws_clients:
                    try:
                        dp  = self._decom.decommutate_packet(pkt)
                        svc = sub = 0
                        if dp.secondary_header:
                            svc = dp.secondary_header.service_type
                            sub = dp.secondary_header.service_subtype
                        await self._ws_broadcast(json.dumps({
                            'type':'tm_packet','apid':dp.header.apid,
                            'seq':dp.header.sequence_count,
                            'service':svc,'subtype':sub,
                            'len':len(pkt),'hex':pkt[:16].hex()}))
                    except: pass

    # ── Raw TCP handlers ─────────────────────────────────────────────────
    async def handle_tc(self, r, w):
        addr = w.get_extra_info('peername')
        logger.info('TC client: %s', addr)
        try:
            while True:
                lb = await asyncio.wait_for(r.readexactly(2), timeout=60.0)
                n  = _struct.unpack('>H', lb)[0]
                if n < 6 or n > 4096: break
                tc = await asyncio.wait_for(r.readexactly(n), timeout=10.0)
                self.engine.tc_queue.put_nowait(tc)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError): pass
        finally:
            logger.info('TC gone: %s', addr)
            try: w.close()
            except: pass

    async def handle_tm(self, r, w):
        addr = w.get_extra_info('peername')
        if len(self._tm_clients) >= TM_MAX_CLIENTS: w.close(); return
        self._tm_clients.add(w)
        logger.info('TM client: %s', addr)
        try:
            while True:
                await asyncio.sleep(5.0)
                if w.is_closing(): break
        except (ConnectionResetError, BrokenPipeError): pass
        finally:
            self._tm_clients.discard(w)
            logger.info('TM gone: %s', addr)
            try: w.close()
            except: pass

    async def handle_instructor(self, r, w):
        addr = w.get_extra_info('peername')
        self._instr_clients.add(w)
        logger.info('Instructor TCP: %s', addr)
        try:
            while True:
                line = await asyncio.wait_for(r.readline(), timeout=120.0)
                if not line: break
                s = line.decode('utf-8', errors='ignore').strip()
                if s.startswith('{'):
                    try: self.engine.instr_queue.put_nowait(json.loads(s))
                    except: pass
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError): pass
        finally:
            self._instr_clients.discard(w)
            try: w.close()
            except: pass

    # ── Run ───────────────────────────────────────────────────────────────
    async def run(self):
        self.engine.start()
        asyncio.create_task(self._broadcast_tm())
        asyncio.create_task(self._broadcast_state())

        tc_s  = await asyncio.start_server(self.handle_tc,         '0.0.0.0', TC_PORT)
        tm_s  = await asyncio.start_server(self.handle_tm,         '0.0.0.0', TM_PORT)
        in_s  = await asyncio.start_server(self.handle_instructor, '0.0.0.0', INSTR_PORT)

        app = web.Application()
        app.router.add_get('/',          self.handle_nav)
        app.router.add_get('/ws',        self.handle_ws)
        app.router.add_get('/catalog',   self.handle_catalog)
        app.router.add_get('/{pos}',     self.handle_position)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', MCS_PORT).start()

        logger.info('═' * 58)
        logger.info('  EOSAT-1 Mission Control Server')
        logger.info('  TC  uplink  → :%d  (raw TCP)', TC_PORT)
        logger.info('  TM  dnlink  → :%d  (raw TCP)', TM_PORT)
        logger.info('  Instructor  → :%d  (JSON TCP)', INSTR_PORT)
        logger.info('  MCS Hub     → http://0.0.0.0:%d/', MCS_PORT)
        for k,v in POSITIONS.items():
            logger.info('  %-10s  → http://0.0.0.0:%d/%s', v[1], MCS_PORT, k)
        logger.info('  TC catalog  → %d commands', len(TC_CATALOG))
        logger.info('═' * 58)

        async with tc_s, tm_s, in_s:
            await asyncio.gather(tc_s.serve_forever(), tm_s.serve_forever(), in_s.serve_forever())


def main():
    engine = SimulationEngine(speed=1.0)
    server = SimServer(engine)
    loop   = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    def _stop(s,f): engine.stop(); loop.stop()
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:    loop.run_until_complete(server.run())
    except RuntimeError: pass
    finally: engine.stop(); logger.info('Shutdown complete.')

if __name__ == '__main__':
    main()
