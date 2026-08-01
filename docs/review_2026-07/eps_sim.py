#!/usr/bin/env python3
"""
Standalone reproduction of the EO-Sat-1 simulator EPS arithmetic (review item 9).

Reproduces the SIMULATOR'S equations (eps_basic.py / engine.py as documented in
power_model_extract.md), not textbook ones:

  net_w  = gen_w - cons_w
  dSoC%  = net_w * dt / 4320                    (120 Wh battery; no coulombic eff.)
  ocv    = 21.5 + 7.7 * soc/100
  bat_i  = net_w / ocv                          (+A charging)
  v_load = ocv - bat_i * 0.05                   (NB: sim's sign convention -> voltage
                                                 RISES slightly under discharge)
  bat_v  = max(0, v_load) + N(0, 0.02)
  bus_v  = min(29, max(0, bat_v)) + N(0, 0.01)

Loads are power-line based with the wattages the tick code actually uses
(obc 15, ttc_rx 5 hard-coded, ttc_tx 20, payload 0/8/45, fpa_cooler 15,
htr_bat 6, htr_obc 4 billed at full power, aocs_wheels 6 hard-coded),
cons noise N(0,1) W.

Generation: 6-panel CSS model, wings deployed, beta = 0.
  sun_body = (cos(phi), sin(phi), 0), phi advancing 360 deg / 5700 s (AOCS sweep).
  per-face: 0.295 * 1361 * area * max(0,cos) * 0.97 * (1 + N(0,0.005)); 0 in eclipse.
  Areas: +/-X 0.06, +/-Y 0.27 (deployed), +/-Z 0.06 (dark at beta=0).
Eclipse: binary, fraction 0.38 of the 5700 s orbit, centred at phi = 180 deg
(orbit fraction 0.31..0.69). Alignment sensitivity computed separately.

Protections modelled:
  - In-model bus-voltage shed: bus_v < 26.5 V -> shed first still-ON of
    [payload, fpa_cooler, ttc_tx, aocs_wheels], ONE line per tick, latched
    (no auto-restore).  ttc_tx shed is defeated during passes by the engine's
    auto-TX hold (any accepted TC forces the line ON for 900 s), so TX is
    modelled as active during every contact window regardless of shed state.
  - FDIR (fdir.yaml, WORKING in the real engine): SoC<20 -> payload off;
    SoC<15 -> EPS safe (sheds payload+cooler); bus<26 -> EPS safe;
    bus<24 -> AOCS safe (wheels off).
  - fault_propagation.yaml SoC stages 30/20/10 (DEAD in the real engine,
    engine.py:618 reads eps._state.soc which doesn't exist -> always 100):
    modelled only in the "intended" variants:
      30% -> payload standby->off ; 20% -> AOCS safe (wheels off) ;
      10% -> heater duty cut (assumed 50%: 10 W -> 5 W).

Profiles:
  P1  nominal platform 36 W, Iqaluit passes only (2 of 3 orbits, 10 min pass,
      TX = AOS..LOS+900s auto-hold => 1500 s per pass).
  P2  P1 + imaging: payload line ON (8 W standby), 3 x 120 s captures/orbit at
      45 W over the sunlit (day-side) arc, FPA cooler 15 W for 1300 s around them.
  P3  P2 + Troll passes (same orbits, half an orbit after Iqaluit) => TX ~ every
      half orbit.
  P4  sustained science: payload imaging 45 W + cooler 15 W continuous,
      two-station contacts.
"""
import math, json
import numpy as np

T_ORB = 5700.0          # s, AOCS sun-sweep period used for both sweep and eclipse
ECL_F = 0.38            # eclipse fraction
ECL_LO, ECL_HI = 0.5 - ECL_F / 2, 0.5 + ECL_F / 2   # orbit-fraction window
FLUX = 0.295 * 1361.0 * 0.97      # = 389.45 W/m2 effective (cell_eff*irr*MPPT)
AX, AY = 0.06, 0.27               # +/-X body panel, +/-Y wing-deployed areas
CAP_WH = 120.0
DT = 1.0
DUR_S = 72 * 3600
SHED_V = 26.5

# ---- schedules (orbit-fraction windows) -------------------------------------
IQ_AOS, IQ_LOS = 900.0, 1500.0            # s into orbit (sunlit, pre-eclipse)
TR_AOS, TR_LOS = 4200.0, 4800.0           # s into orbit (post-eclipse)
TX_HOLD = 900.0                           # engine auto-TX hold after last TC (@LOS)
CAPTURES = [(4275, 4395), (4560, 4680), (4845, 4965)]   # 3 x 120 s per orbit
COOLER_WIN = (3950, 5250)                               # 1300 s per orbit


def gen_area_eff(phi):
    return AX * abs(math.cos(phi)) + AY * abs(math.sin(phi))


def run(profile, variant, seed=42, soc0=75.0, t_offset=0.0,
        ay=AY, tx_w=20.0, tx_hold=TX_HOLD, shed_v=SHED_V, phi_off_deg=0.0):
    """variant: 'asis' | 'intended' | 'novolt' | 'novolt_intended' | 'none'
    ay: +/-Y deployed panel area; phi_off_deg: sweep-vs-eclipse alignment offset
    (0 = eclipse centred on the X-panel lobe [best]; 90 = centred on the Y lobe [worst])."""
    rng = np.random.default_rng(seed)
    n = int(DUR_S / DT)
    g1 = rng.normal(0, 0.005, n)   # lit X-panel noise
    g2 = rng.normal(0, 0.005, n)   # lit Y-panel noise
    cn = rng.normal(0, 1.0, n)     # cons noise
    vn = rng.normal(0, 0.02, n)    # bat_v noise
    bn = rng.normal(0, 0.01, n)    # bus_v noise

    volt_shed_on = variant in ("asis", "intended")
    fdir_soc_on = variant != "none"          # fdir.yaml SoC rules work in the real engine
    fdir_bus_on = variant in ("asis", "intended")
    stages_on = variant in ("intended", "novolt_intended")

    soc = soc0
    # line-enable latches (True = available). Sheds latch them False.
    en = {"payload": True, "cooler": True, "tx": True, "wheels": True}
    htr_duty = 1.0
    shed_ix = 0
    SHED_ORDER = ["payload", "cooler", "tx", "wheels"]
    events = []            # (t, label) first occurrences
    fired = set()
    soc_hist = np.empty(n)
    net_hist = np.empty(n)
    gen_hist = np.empty(n)
    cons_cmd_hist = np.empty(n)    # commanded load (ignoring sheds) for budget chart
    t_flat = None

    two_station = profile in ("P3", "P4")
    imaging = profile in ("P2", "P3")
    sustained = profile == "P4"

    for i in range(n):
        t = i * DT + t_offset
        k = int(t // T_ORB)
        frac_s = t - k * T_ORB
        frac = frac_s / T_ORB
        eclipse = ECL_LO <= frac < ECL_HI
        phi = 2 * math.pi * frac + math.radians(phi_off_deg)

        # ---- generation ----
        if eclipse:
            gen = 0.0
        else:
            px = FLUX * AX * abs(math.cos(phi)) * (1 + g1[i])
            py = FLUX * ay * abs(math.sin(phi)) * (1 + g2[i])
            gen = px + py

        # ---- contact / payload schedules ----
        pass_orbit = (k % 3) != 2                       # ~10 Iqaluit passes/day
        tx_now = pass_orbit and IQ_AOS <= frac_s < IQ_LOS + tx_hold
        if two_station and pass_orbit and TR_AOS <= frac_s < min(TR_LOS + tx_hold, T_ORB):
            tx_now = True
        # auto-TX hold defeats a tx shed during contacts -> billed regardless of en['tx']

        if sustained:
            pl_mode = 2 if en["payload"] else 0
            cooler = en["cooler"]
        elif imaging:
            if en["payload"]:
                pl_mode = 2 if any(a <= frac_s < b for a, b in CAPTURES) else 1
            else:
                pl_mode = 0
            cooler = en["cooler"] and COOLER_WIN[0] <= frac_s < COOLER_WIN[1]
        else:
            pl_mode = 0
            cooler = False

        # ---- loads (line-based, sim wattages) ----
        pl_w = {0: 0.0, 1: 8.0, 2: 45.0}[pl_mode]
        cons = (15.0 + 5.0                       # obc + ttc_rx
                + (tx_w if tx_now else 0.0)
                + pl_w
                + (15.0 if cooler else 0.0)
                + (6.0 + 4.0) * htr_duty         # heater lines billed full power
                + (6.0 if en["wheels"] else 0.0))
        cons += cn[i]
        # commanded load (no sheds) for the budget bar chart
        if sustained:
            c_pl, c_cool = 45.0, 15.0
        elif imaging:
            c_pl = 45.0 if any(a <= frac_s < b for a, b in CAPTURES) else 8.0
            c_cool = 15.0 if COOLER_WIN[0] <= frac_s < COOLER_WIN[1] else 0.0
        else:
            c_pl = c_cool = 0.0
        cons_cmd_hist[i] = 15 + 5 + (tx_w if tx_now else 0) + c_pl + c_cool + 10 + 6

        # ---- battery integration (sim's exact equations) ----
        net = gen - cons
        soc = max(0.0, min(100.0, soc + net * DT / 4320.0))
        ocv = 21.5 + 7.7 * soc / 100.0
        bat_i = net / (ocv + 1e-6)
        v_loaded = ocv - bat_i * 0.05
        bat_v = max(0.0, v_loaded) + vn[i]
        bus_v = min(29.0, max(0.0, bat_v)) + bn[i]

        if soc <= 0.0 and t_flat is None:
            t_flat = t

        # ---- protections ----
        if volt_shed_on and bus_v < shed_v and shed_ix < 4:
            line = SHED_ORDER[shed_ix]
            en[line] = False
            lbl = f"V-shed {line} (bus<26.5V)"
            if lbl not in fired:
                fired.add(lbl); events.append((t, lbl, soc))
            shed_ix += 1
        if fdir_bus_on and bus_v < 26.0:
            for line in ("payload", "cooler"):
                if en[line]:
                    en[line] = False
                    lbl = "FDIR bus<26V EPS-safe (payload+cooler)"
                    if lbl not in fired:
                        fired.add(lbl); events.append((t, lbl, soc))
        if fdir_bus_on and bus_v < 24.0 and en["wheels"]:
            en["wheels"] = False
            lbl = "FDIR bus<24V AOCS-safe (wheels)"
            if lbl not in fired:
                fired.add(lbl); events.append((t, lbl, soc))
        if fdir_soc_on:
            if soc < 20.0 and en["payload"]:
                en["payload"] = False
                lbl = "FDIR SoC<20 payload off"
                if lbl not in fired:
                    fired.add(lbl); events.append((t, lbl, soc))
            if soc < 15.0 and en["cooler"]:
                en["cooler"] = False; en["payload"] = False
                lbl = "FDIR SoC<15 EPS-safe (payload+cooler)"
                if lbl not in fired:
                    fired.add(lbl); events.append((t, lbl, soc))
        if stages_on:
            if soc < 30.0 and en["payload"]:
                en["payload"] = False
                lbl = "Stage-1 SoC<30 payload off (YAML)"
                if lbl not in fired:
                    fired.add(lbl); events.append((t, lbl, soc))
            if soc < 20.0 and en["wheels"]:
                en["wheels"] = False
                lbl = "Stage-2 SoC<20 AOCS safe (YAML)"
                if lbl not in fired:
                    fired.add(lbl); events.append((t, lbl, soc))
            if soc < 10.0 and htr_duty > 0.5:
                htr_duty = 0.5
                lbl = "Stage-3 SoC<10 heater duty cut (YAML)"
                if lbl not in fired:
                    fired.add(lbl); events.append((t, lbl, soc))

        soc_hist[i] = soc
        net_hist[i] = net
        gen_hist[i] = gen

    last24 = slice(n - 24 * 3600, n)
    res = {
        "profile": profile, "variant": variant,
        "min_soc": float(soc_hist.min()),
        "t_min_soc_h": float(soc_hist.argmin() * DT / 3600),
        "final_soc": float(soc_hist[-1]),
        "trend_wh_per_orbit_last24h": float(net_hist[last24].mean() * T_ORB / 3600),
        "clamped100_frac_last24h": float((soc_hist[last24] >= 99.999).mean()),
        "gen_avg_w": float(gen_hist.mean()),
        "load_cmd_avg_w": float(cons_cmd_hist.mean()),
        "t_flat_h": None if t_flat is None else float(t_flat / 3600),
        "events": [(round(t / 3600, 2), lbl, round(s, 1)) for t, lbl, s in events],
    }
    return res, soc_hist


def orbit_avg_gen(ecl_center_deg):
    """Alignment sensitivity: orbit-average generation for a given eclipse centre."""
    tot = 0.0
    N = 20000
    for i in range(N):
        f = i / N
        d = abs((f * 360 - ecl_center_deg + 180) % 360 - 180)
        if d < ECL_F * 180:
            continue
        tot += FLUX * gen_area_eff(2 * math.pi * f)
    return tot / N


if __name__ == "__main__":
    runs = {}
    hists = {}
    for p in ("P1", "P2", "P3", "P4"):
        for v in ("asis", "intended"):
            r, h = run(p, v)
            runs[f"{p}_{v}"] = r
            hists[f"{p}_{v}"] = h
    for p in ("P3", "P4"):
        for v in ("novolt", "novolt_intended", "none"):
            r, h = run(p, v)
            runs[f"{p}_{v}"] = r
            hists[f"{p}_{v}"] = h
    # sensitivity: P1 starting at eclipse entry instead of sunlit start
    r, h = run("P1", "asis", t_offset=ECL_LO * T_ORB)
    runs["P1_asis_eclstart"] = r
    hists["P1_asis_eclstart"] = h
    # P2 with a lower initial SoC (post-LEOP realistic dispersion)
    r, h = run("P2", "asis", soc0=65.0)
    runs["P2_asis_soc65"] = r
    # worst sweep-vs-eclipse alignment (eclipse centred on the Y-wing lobe).
    # In the real sim the AOCS sweep (5700 s) and propagator eclipse (5669 s)
    # drift ~90 deg per 72 h, so both alignments occur within days.
    for p in ("P2", "P3"):
        r, h = run(p, "asis", phi_off_deg=90.0)
        runs[f"{p}_asis_align90"] = r
        hists[f"{p}_asis_align90"] = h
    # ---- recommended config: wings 0.24->0.30 m2 each (+/-Y deployed 0.33),
    #      TX 20->12 W, auto-TX hold 900->300 s, initial SoC 90 %,
    #      LOAD_SHED_VOLTAGE 26.5->25.0 V (=45.5 % SoC), SoC stages fixed (intended)
    REC = dict(ay=0.33, tx_w=12.0, tx_hold=300.0, shed_v=25.0, soc0=90.0)
    for p in ("P1", "P2", "P3", "P4"):
        for off, tag in ((0.0, "rec"), (90.0, "rec90")):
            r, h = run(p, "intended", phi_off_deg=off, **REC)
            runs[f"{p}_{tag}"] = r
            hists[f"{p}_{tag}"] = h

    runs["_align"] = {
        "gen_avg_ecl_at_180deg": orbit_avg_gen(180.0),
        "gen_avg_ecl_at_90deg": orbit_avg_gen(90.0),
        "gen_avg_ecl_at_0deg": orbit_avg_gen(0.0),
    }
    with open("/home/claude/work/power_sim/results.json", "w") as f:
        json.dump(runs, f, indent=1)
    np.savez_compressed("/home/claude/work/power_sim/soc_hist.npz",
                        **{k: v[::10].astype(np.float32) for k, v in hists.items()})
    for k, r in runs.items():
        if k.startswith("_"):
            print(k, {kk: round(vv, 1) for kk, vv in r.items()})
            continue
        print(f"{k:22s} min={r['min_soc']:5.1f}% @{r['t_min_soc_h']:5.1f}h "
              f"final={r['final_soc']:5.1f}% trend={r['trend_wh_per_orbit_last24h']:+6.2f} Wh/orb "
              f"gen={r['gen_avg_w']:5.1f}W load={r['load_cmd_avg_w']:5.1f}W "
              f"flat={r['t_flat_h']} ev={len(r['events'])}")
        for ev in r["events"]:
            print(f"    {ev[0]:6.2f} h  SoC {ev[2]:5.1f}%  {ev[1]}")
