import math, random
random.seed(7)
DT=1.0
PERIOD=5670.0          # 450 km SSO, 15.24 rev/day
ECL_FRAC=0.35
ECL_INT=10.0; SUN_INT=12.0
TCS_TAU=120.0          # tcs_basic._TAU["fpa"]
TCS_TARGET=-15.0       # tcs.yaml fpa_cooler_target_c
PL_TARGET=-15.0; PL_AMB=5.0; PL_TAU_C=100.0; PL_TAU_W=120.0

def run(tcs_cooler, pl_cooler, T=4*PERIOD):
    t=0.0; Tt=-5.0; Tp=5.0
    rows=[]
    while t<T:
        ecl = (t % PERIOD) < ECL_FRAC*PERIOD
        env_int = ECL_INT if ecl else SUN_INT
        # --- TCS (tcs_basic.py:492-493) ---
        cool = (TCS_TARGET-20.0) if tcs_cooler else 0.0
        Tt += (env_int+cool-Tt)/TCS_TAU*DT + random.gauss(0,0.02)
        # --- Payload (payload_basic.py:379-387) ---
        if pl_cooler: tgt,tau = PL_TARGET, PL_TAU_C
        else:         tgt,tau = PL_AMB,   PL_TAU_W
        Tp += (tgt-Tp)/tau*DT + random.gauss(0,0.02)
        rows.append((t,ecl,Tt,Tp)); t+=DT
    return rows

for name,(tc,pc) in {
    "A cooler OFF everywhere (default state)":(False,False),
    "B payload STANDBY only (PL_SET_MODE 1) - TCS cooler never commanded":(False,True),
    "C both commanded ON (PL_SET_MODE 1 + TCS FPA_COOLER 1)":(True,True),
    "D TCS cooler ON, payload OFF":(True,False),
}.items():
    r=run(tc,pc)
    last=r[-int(PERIOD):]                      # final orbit
    tt=[x[2] for x in last]; tp=[x[3] for x in last]
    print(f"{name}")
    print(f"   tcs.temp_fpa 0x0408 : mean {sum(tt)/len(tt):+7.2f} C   min {min(tt):+7.2f} max {max(tt):+7.2f}")
    print(f"   payload.fpa_temp 0x0601: mean {sum(tp)/len(tp):+7.2f} C   min {min(tp):+7.2f} max {max(tp):+7.2f}")
    print(f"   DIVERGENCE (tcs - payload) mean = {sum(tt)/len(tt)-sum(tp)/len(tp):+7.2f} C\n")
