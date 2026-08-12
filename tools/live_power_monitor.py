#!/usr/bin/env python3
"""Live power monitor: drive a running simulator to a full nominal
configuration (ADCS included) and log generation vs draw over N orbits.

This is the harness behind the "steady-state draw exceeds steady-state
generation" investigation. It talks to the *running* services exactly the way
an operator does — real telecommands over the TC socket, real telemetry from
the HTTP state API — so anything it measures is what a student sees.

    python3 tools/live_power_monitor.py --minutes 200 --speed 60 --out /tmp/pw.csv

It never touches the models directly. If a step is refused, it says so and
carries on, because a refused commissioning step is itself a finding.
"""
from __future__ import annotations

import argparse
import csv
import json
import socket
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "smo-common" / "src"))

from smo_common.commanding.encode import encode  # noqa: E402
from smo_common.commanding.catalog import load_catalog  # noqa: E402
from smo_common.protocol.ecss_packet import build_tc_packet  # noqa: E402
from smo_common.protocol.framing import frame_packet  # noqa: E402

SIM_HTTP = "http://127.0.0.1:8080"
TC_HOST, TC_PORT = "127.0.0.1", 8001
APID = 0x001


class TCLink:
    """One long-lived TC socket, sequence-counted like a real uplink."""

    def __init__(self, host: str = TC_HOST, port: int = TC_PORT) -> None:
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.seq = 0
        self.catalog = load_catalog(REPO / "configs" / "eosat1")

    def send(self, name: str, args: dict | None = None,
             service: int | None = None, subtype: int | None = None,
             raw: bytes | None = None) -> None:
        if raw is None:
            cmd = self.catalog.get(name)
            data = encode(name, args or {}, catalog=self.catalog)
            service = int(cmd["service"])
            subtype = int(cmd["subtype"])
        else:
            data = raw
        pkt = build_tc_packet(APID, service, subtype, data, self.seq)
        self.seq = (self.seq + 1) & 0x3FFF
        # The TC socket is length-prefixed (smo_common.protocol.framing) —
        # a bare packet is silently swallowed by read_framed_packet.
        self.sock.sendall(frame_packet(pkt))

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def state() -> dict:
    with urllib.request.urlopen(f"{SIM_HTTP}/api/state", timeout=5.0) as r:
        return json.loads(r.read().decode())


def instructor(cmd: dict) -> dict:
    req = urllib.request.Request(
        f"{SIM_HTTP}/api/command",
        data=json.dumps(cmd).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5.0) as r:
        return json.loads(r.read().decode())


def step(label: str, fn, settle: float = 1.5) -> None:
    try:
        fn()
        print(f"  [ok]   {label}")
    except Exception as exc:  # a refused step is a finding, not a crash
        print(f"  [FAIL] {label}: {exc}")
    time.sleep(settle)


def commission(tc: TCLink, *, deploy_wings: bool = True) -> None:
    """Bring the spacecraft from bootloader to a full nominal configuration."""
    print("\n== Phase 1: boot the application ==")
    step("OBC_BOOT_APP", lambda: tc.send("OBC_BOOT_APP"), settle=2.0)
    # Wing deployment and most of S8 are refused while the bootloader owns the
    # bus (phase 3). Wait for the LEOP transition rather than guessing a delay.
    for _ in range(60):
        if int(state().get("sc_phase", state().get("phase", 0)) or 0) >= 4:
            break
        try:
            if state()["obdh"].get("mode", 0) != 0:
                break
        except Exception:
            pass
        time.sleep(1.0)
    print(f"  phase after boot: obdh.mode={state()['obdh'].get('mode')}")

    print("\n== Phase 2: enable housekeeping we need to see ==")
    for sid in (1, 2, 3, 8, 9):
        step(f"HK enable SID {sid}",
             lambda s=sid: tc.send("HK_ENABLE", {"sid": s}), settle=0.4)

    print("\n== Phase 3: EPS — power lines and the solar wings ==")
    if deploy_wings:
        for wing in (0, 1):
            step(f"EPS_DEPLOY_WING {wing}",
                 lambda w=wing: tc.send("EPS_DEPLOY_WING", {"wing": w}), settle=3.0)
    for line in ("ttc_tx", "aocs_wheels", "payload", "htr_bat", "htr_obc"):
        step(f"EPS_POWER_ON {line}",
             lambda ln=line: tc.send("EPS_POWER_ON", {"line_index": LINE_IDX[ln]}),
             settle=0.6)

    print("\n== Phase 4: TT&C ==")
    step("TTC_PA_ON", lambda: tc.send("TTC_PA_ON"))
    step("TTC_SET_BEACON_MODE off", lambda: tc.send("TTC_SET_BEACON_MODE", {"on": 0}))
    step("TTC_DEPLOY_ANTENNA", lambda: tc.send("TTC_DEPLOY_ANTENNA"), settle=3.0)
    step("TTC_SET_DATA_RATE LOW", lambda: tc.send("TTC_SET_DATA_RATE", {"rate": 0}))

    print("\n== Phase 5: AOCS — sensors, wheels, nominal pointing ==")
    # The S2 device commands carry no default device_id in the catalogue, so
    # they must be supplied explicitly or the dispatcher sees 0x0000.
    for dev, did in (("S2_AOCS_STAR_TRACKER_1", 0x0204),
                     ("S2_AOCS_GYROSCOPE_0", 0x0206),
                     ("S2_AOCS_GYROSCOPE_1", 0x0207),
                     ("S2_AOCS_GYROSCOPE_2", 0x0208),
                     ("S2_AOCS_REACTION_WHEEL_0", 0x0200),
                     ("S2_AOCS_REACTION_WHEEL_1", 0x0201),
                     ("S2_AOCS_REACTION_WHEEL_2", 0x0202),
                     ("S2_AOCS_REACTION_WHEEL_3", 0x0203)):
        step(f"{dev} on",
             lambda d=dev, i=did: tc.send(d, {"device_id": i, "state": 1}),
             settle=0.4)
    for w in range(4):
        step(f"AOCS_ENABLE_WHEEL {w}",
             lambda i=w: tc.send("AOCS_ENABLE_WHEEL", {"wheel_idx": i}), settle=0.4)
    step("MTQ_ENABLE", lambda: tc.send("MTQ_ENABLE", {"enable": 1}))
    step("AOCS_SET_MODE NOMINAL(4)", lambda: tc.send("AOCS_SET_MODE", {"mode": 4}),
         settle=8.0)

    print("\n== Phase 6: payload to standby (cooler on) ==")
    step("EPS_FPA_COOLER on", lambda: tc.send("EPS_FPA_COOLER", {"on": 1}))
    step("PAYLOAD_SET_MODE STANDBY(1)",
         lambda: tc.send("PAYLOAD_SET_MODE", {"mode": 1}), settle=2.0)


LINE_IDX = {
    "obc": 0, "ttc_rx": 1, "ttc_tx": 2, "payload": 3, "fpa_cooler": 4,
    "htr_bat": 5, "htr_obc": 6, "aocs_wheels": 7, "prop": 8,
}


def monitor(minutes: float, period_s: float, out: Path) -> list[dict]:
    """Poll the live state API and log the power chain."""
    rows: list[dict] = []
    t_end = time.time() + minutes * 60.0
    last_print = 0.0
    while time.time() < t_end:
        try:
            s = state()
        except Exception as exc:
            print(f"  state poll failed: {exc}")
            time.sleep(period_s)
            continue
        eps = s.get("eps", {})
        aocs = s.get("aocs", {})
        row = {
            "met_s": s.get("met_s"),
            "sim_time": s.get("sim_time"),
            "eclipse": int(bool(s.get("in_eclipse"))),
            "sc_mode": s.get("sc_mode"),
            "aocs_mode": aocs.get("mode"),
            "gen_w": eps.get("power_gen_W"),
            "cons_w": eps.get("power_cons_W"),
            "net_w": round((eps.get("power_gen_W") or 0) - (eps.get("power_cons_W") or 0), 3),
            "soc_pct": eps.get("soc_pct"),
            "bus_v": eps.get("bus_voltage_V"),
            "bat_a": eps.get("bat_current_A"),
            "sa_a": eps.get("sa_a_A"),
            "sa_b": eps.get("sa_b_A"),
            "att_err_deg": aocs.get("att_error_deg"),
        }
        rows.append(row)
        if time.time() - last_print > 20.0:
            last_print = time.time()
            print(f"  t={row['met_s']:>8.0f}s ecl={row['eclipse']} "
                  f"gen={row['gen_w']:>6.2f} cons={row['cons_w']:>6.2f} "
                  f"net={row['net_w']:>+7.2f} soc={row['soc_pct']:>5.1f}% "
                  f"aocs={row['aocs_mode']}")
        time.sleep(period_s)

    if rows:
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return rows


def summarise(rows: list[dict]) -> None:
    if not rows:
        print("no samples")
        return
    gen = [r["gen_w"] for r in rows if r["gen_w"] is not None]
    con = [r["cons_w"] for r in rows if r["cons_w"] is not None]
    sun = [r for r in rows if not r["eclipse"]]
    ecl = [r for r in rows if r["eclipse"]]
    soc = [r["soc_pct"] for r in rows if r["soc_pct"] is not None]

    print("\n" + "=" * 68)
    print("POWER SUMMARY  (live simulator, full nominal configuration)")
    print("=" * 68)
    print(f"samples                 : {len(rows)}  "
          f"({rows[0]['met_s']:.0f}s -> {rows[-1]['met_s']:.0f}s MET)")
    print(f"orbit-average generation: {statistics.fmean(gen):8.2f} W")
    print(f"orbit-average load      : {statistics.fmean(con):8.2f} W")
    print(f"orbit-average margin    : {statistics.fmean(gen) - statistics.fmean(con):+8.2f} W")
    print(f"generation/load ratio   : {statistics.fmean(gen) / max(statistics.fmean(con), 1e-9):8.2f}")
    if sun:
        print(f"sunlit generation       : {statistics.fmean([r['gen_w'] for r in sun]):8.2f} W "
              f"(min {min(r['gen_w'] for r in sun):.2f}, max {max(r['gen_w'] for r in sun):.2f})")
    if ecl:
        print(f"eclipse fraction        : {len(ecl) / len(rows):8.3f}")
    print(f"SoC                     : start {soc[0]:.1f}%  end {soc[-1]:.1f}%  "
          f"min {min(soc):.1f}%  max {max(soc):.1f}%")
    trend = soc[-1] - soc[0]
    print(f"SoC trend over the run  : {trend:+.2f} %   "
          f"{'CLOSING' if trend >= -0.5 else 'NOT CLOSING — draw exceeds generation'}")
    print("=" * 68)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15.0,
                    help="wall-clock minutes to monitor")
    ap.add_argument("--period", type=float, default=2.0, help="poll period, s")
    ap.add_argument("--speed", type=float, default=60.0,
                    help="simulation speed multiplier to request")
    ap.add_argument("--out", default="/tmp/power_live.csv")
    ap.add_argument("--no-commission", action="store_true")
    ap.add_argument("--no-wings", action="store_true",
                    help="skip wing deployment (reproduces the stowed-wing case)")
    args = ap.parse_args()

    if not args.no_commission:
        tc = TCLink()
        try:
            commission(tc, deploy_wings=not args.no_wings)
        finally:
            tc.close()

    s = state()
    print("\nconfiguration after commissioning:")
    print(f"  sc_mode={s['sc_mode']}  aocs.mode={s['aocs']['mode']}  "
          f"lines={ {k: v for k, v in s['eps']['power_lines'].items() if v} }")

    if args.speed and args.speed != 1.0:
        try:
            # engine._INSTRUCTOR_NUMERIC_FIELDS keys set_speed on 'value',
            # not 'speed' — send both so either schema is honoured.
            instructor({"type": "set_speed", "value": args.speed,
                        "speed": args.speed})
            print(f"  speed set to {args.speed}x")
        except Exception as exc:
            print(f"  could not set speed: {exc}")
        time.sleep(2.0)

    print(f"\nmonitoring for {args.minutes} wall-clock minutes...")
    rows = monitor(args.minutes, args.period, Path(args.out))
    summarise(rows)
    print(f"\ncsv: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
