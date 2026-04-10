#!/usr/bin/env python3
"""
EO Mission Simulator — Test Client
Connects to the simulator, receives and decodes TM packets,
sends a sample set of telecommands.

Usage:
    python test_client.py
    python test_client.py --host 127.0.0.1 --tm-only
    python test_client.py --send-tcs
"""
import argparse
import socket
import struct
import threading
import json
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ecss_decommutator import ECSSDecommutator, PUSService

# -----------------------------------------------------------------------
HOST    = '127.0.0.1'
TC_PORT = 8001
TM_PORT = 8002
INSTR_PORT = 8003

_decom  = ECSSDecommutator()
_LOCK   = threading.Lock()


def _print(msg: str) -> None:
    with _LOCK:
        print(msg)


# -----------------------------------------------------------------------
# TM Receiver thread
# -----------------------------------------------------------------------

class TMReceiver(threading.Thread):
    def __init__(self, host: str, port: int, max_packets: int = 0):
        super().__init__(daemon=True, name="tm-rx")
        self.host        = host
        self.port        = port
        self.max_packets = max_packets
        self.count       = 0
        self._sock       = None

    def run(self) -> None:
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=5)
            _print(f"[TM] Connected to {self.host}:{self.port}")
        except Exception as e:
            _print(f"[TM] Connect failed: {e}")
            return

        buf = b''
        while True:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 2:
                    pkt_len = struct.unpack('>H', buf[:2])[0]
                    if len(buf) < 2 + pkt_len:
                        break
                    pkt = buf[2:2 + pkt_len]
                    buf = buf[2 + pkt_len:]
                    self._decode(pkt)
                    self.count += 1
                    if self.max_packets and self.count >= self.max_packets:
                        return
            except socket.timeout:
                continue
            except Exception as e:
                _print(f"[TM] Receive error: {e}")
                break

    def _decode(self, pkt: bytes) -> None:
        try:
            d = _decom.decommutate_packet(pkt)
            h = d.header
            if d.secondary_header:
                svc  = d.secondary_header.service_type
                sub  = d.secondary_header.service_subtype
                try:
                    svc_name = PUSService(svc).name
                except ValueError:
                    svc_name = f"SVC{svc}"
                if svc == 1:
                    _print(f"[TM] S1/{sub}  APID={h.apid} seq={h.sequence_count} "
                           f"({'ACCEPT' if sub == 1 else 'COMPLETE' if sub == 7 else 'FAIL'})")
                elif svc == 3:
                    sid = struct.unpack('>H', d.data_field[:2])[0] if len(d.data_field) >= 2 else '?'
                    _print(f"[TM] S3/HK  SID={sid}  APID={h.apid}  len={len(pkt)} bytes")
                elif svc == 5:
                    if len(d.data_field) >= 3:
                        ev_id  = struct.unpack('>H', d.data_field[:2])[0]
                        sev    = d.data_field[2]
                        desc   = d.data_field[7:].decode('ascii', errors='ignore') if len(d.data_field) > 7 else ''
                        _print(f"[TM] S5/EVENT  id=0x{ev_id:04X}  sev={sev}  '{desc[:60]}'")
                elif svc == 20:
                    _print(f"[TM] S20/{sub}  param value report  len={len(d.data_field)}")
                else:
                    _print(f"[TM] {svc_name}/{sub}  APID={h.apid}  seq={h.sequence_count}")
            else:
                _print(f"[TM] Raw  APID={h.apid}  len={len(pkt)}")
        except Exception as e:
            _print(f"[TM] Decode error ({len(pkt)} bytes): {e}")


# -----------------------------------------------------------------------
# TC Sender
# -----------------------------------------------------------------------

class TCSender:
    def __init__(self, host: str, port: int):
        self._sock = socket.create_connection((host, port), timeout=5)
        _print(f"[TC] Connected to {host}:{port}")

    def send(self, pkt: bytes) -> None:
        frame = struct.pack('>H', len(pkt)) + pkt
        self._sock.sendall(frame)

    def close(self) -> None:
        self._sock.close()


def _build_command(apid: int, svc: int, sub: int, data: bytes, seq: int) -> bytes:
    """Build a minimal PUS TC packet."""
    sec_hdr = bytes([0x10, svc, sub])
    payload = sec_hdr + data
    data_len = len(payload) - 1
    packet_id = (0 << 13) | (1 << 12) | (1 << 11) | (apid & 0x7FF)
    seq_ctrl  = (0b01 << 14) | (seq & 0x3FFF)
    primary   = struct.pack('>HHH', packet_id, seq_ctrl, data_len)
    pkt = primary + payload
    crc = _crc16(pkt)
    return pkt + struct.pack('>H', crc)


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        crc &= 0xFFFF
    return crc


def run_test_tcs(sender: TCSender) -> None:
    seq = 1

    _print("\n[TC] ─── Sending test telecommands ───")

    # 1. Request EPS housekeeping immediately (S3/27)
    _print("[TC] S3/27 — Request EPS HK (SID=1)")
    sender.send(_build_command(1, 3, 27, struct.pack('>H', 1), seq)); seq += 1
    time.sleep(0.5)

    # 2. Request AOCS housekeeping (S3/27 SID=2)
    _print("[TC] S3/27 — Request AOCS HK (SID=2)")
    sender.send(_build_command(1, 3, 27, struct.pack('>H', 2), seq)); seq += 1
    time.sleep(0.5)

    # 3. Request parameter value: battery SoC (S20/3)
    _print("[TC] S20/3 — Request battery SoC (0x0101)")
    sender.send(_build_command(1, 20, 3, struct.pack('>H', 0x0101), seq)); seq += 1
    time.sleep(0.5)

    # 4. Set OBC mode to SAFE via S20 set (0x0300 = OBC_MODE → 1)
    _print("[TC] S20/1 — Set OBC mode to SAFE (0x0300 = 1)")
    sender.send(_build_command(1, 20, 1,
        struct.pack('>HBI', 0x0300, 1, 1), seq)); seq += 1
    time.sleep(0.5)

    # 5. Execute function: AOCS desaturation (S8/1, func=0x0010)
    _print("[TC] S8/1  — Execute function: AOCS desaturate (0x0010)")
    sender.send(_build_command(1, 8, 1, struct.pack('>H', 0x0010), seq)); seq += 1
    time.sleep(0.5)

    # 6. Execute function: Payload standby (S8/1, func=0x0002)
    _print("[TC] S8/1  — Execute function: Payload STANDBY (0x0002)")
    sender.send(_build_command(1, 8, 1, struct.pack('>H', 0x0002), seq)); seq += 1
    time.sleep(0.5)

    # 7. Set time (S9/1)
    import datetime
    epoch = datetime.datetime(2000, 1, 1, 12, 0, 0)
    cuc   = int((datetime.datetime.utcnow() - epoch).total_seconds())
    _print(f"[TC] S9/1  — Set spacecraft time (CUC={cuc})")
    sender.send(_build_command(1, 9, 1, bytes([0x01]) + struct.pack('>I', cuc), seq)); seq += 1
    time.sleep(0.5)

    # 8. Request platform HK (S3/27 SID=4)
    _print("[TC] S3/27 — Request Platform HK (SID=4)")
    sender.send(_build_command(1, 3, 27, struct.pack('>H', 4), seq)); seq += 1
    time.sleep(0.5)

    _print("[TC] ─── All test TCs sent ───\n")


# -----------------------------------------------------------------------
# Instructor client
# -----------------------------------------------------------------------

def inject_failure(host: str, subsystem: str, failure: str, magnitude: float = 1.0, **kwargs) -> None:
    cmd = {'type': 'inject', 'subsystem': subsystem, 'failure': failure, 'magnitude': magnitude}
    cmd.update(kwargs)
    try:
        s = socket.create_connection((host, INSTR_PORT), timeout=3)
        s.sendall((json.dumps(cmd) + '\n').encode('utf-8'))
        s.close()
        _print(f"[INSTR] Injected: {subsystem}/{failure} mag={magnitude}")
    except Exception as e:
        _print(f"[INSTR] Error: {e}")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="EO Mission Simulator Test Client")
    parser.add_argument('--host',       default=HOST)
    parser.add_argument('--tm-only',    action='store_true')
    parser.add_argument('--send-tcs',   action='store_true')
    parser.add_argument('--inject',     default=None, help="subsystem:failure e.g. eps:solar_array_partial")
    parser.add_argument('--packets',    type=int, default=0, help="Stop after N TM packets (0=forever)")
    args = parser.parse_args()

    _print(f"\n{'═'*60}")
    _print(f"  EOSAT-1 Ground Client  —  host={args.host}")
    _print(f"{'═'*60}\n")

    # Optional failure injection
    if args.inject:
        parts = args.inject.split(':')
        if len(parts) >= 2:
            inject_failure(args.host, parts[0], parts[1])
            time.sleep(1)

    # Start TM receiver
    rx = TMReceiver(args.host, TM_PORT, max_packets=args.packets)
    rx.start()

    # Send TCs if requested
    if args.send_tcs and not args.tm_only:
        time.sleep(1.0)  # Wait for TM connection to settle
        try:
            tx = TCSender(args.host, TC_PORT)
            run_test_tcs(tx)
            tx.close()
        except Exception as e:
            _print(f"[TC] Failed: {e}")

    # Wait for TM thread
    try:
        if args.packets:
            rx.join(timeout=60)
        else:
            while rx.is_alive():
                time.sleep(1)
    except KeyboardInterrupt:
        _print("\n[Client] Interrupted — exiting.")


if __name__ == '__main__':
    main()
