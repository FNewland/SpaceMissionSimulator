import math
TAU={"panel_px":600,"panel_mx":600,"panel_py":500,"panel_my":500,"panel_pz":800,"panel_mz":800,
     "obc":900,"battery":1200,"fpa":120,"thruster":400}
CAP={"panel_px":5000,"panel_mx":5000,"panel_py":4000,"panel_my":4000,"panel_pz":3000,"panel_mz":3000,
     "obc":800,"battery":2000,"fpa":100,"thruster":500}
print("Implied conductance UA = C/tau (the model specifies BOTH tau and C independently):")
for k in TAU: print(f"  {k:10s} tau={TAU[k]:5}s  C={CAP[k]:5} J/K  ->  UA={CAP[k]/TAU[k]:6.3f} W/K")
print("\nHeater authority (steady-state rise = P/UA):")
for k,P in (("battery",6.0),("obc",4.0)):
    print(f"  {k}: {P} W / {CAP[k]/TAU[k]:.3f} W/K = +{P/(CAP[k]/TAU[k]):.2f} K")
print("\nFPA cooler modelled as env offset of (target-20) = -35 K:")
UA=CAP['fpa']/TAU['fpa']; print(f"  implied heat lift = 35 K x {UA:.3f} W/K = {35*UA:.1f} W of cooling")
print(f"  cooler electrical input (payload.yaml fpa_cooler_power_w) = 15.0 W  -> COP = {35*UA/15:.2f} at ~-25 C")
print("\nPanel solar-heat term (simulation.yaml tcs.environment):")
print("  solar_heat_w = I*28*0.3 ;  env = base_env + solar_heat_w*10  (K per W = 10)")
print("  physically correct scaling would be 1/UA_panel:")
for k in ("panel_px","panel_py","panel_pz"):
    print(f"    {k}: 1/UA = {TAU[k]/CAP[k]:.4f} K/W  -> model gain 10 K/W is {10*CAP[k]/TAU[k]:.0f}x too large")
print("\nEquilibrium panel temperature vs measured solar current (env_ext at beta=30 => -10+50*cos30 = +33.3 C):")
env_ext=-10+50*math.cos(math.radians(30))
for face,off,key in (("+X",5,"panel_px"),("+Y",8,"panel_py"),("+Z",-5,"panel_pz")):
    for I in (0.25,0.5,1.0,2.0,4.0):
        heat=I*28*0.3*10.0
        env=env_ext+off+heat
        # add the linear radiation leak: (env-T)/tau = g*(T+270.15)/C  -> solve
        g=0.2; tau=TAU[key]; C=CAP[key]
        T=(env/tau - g*270.15/C)/(1/tau + g/C)
        print(f"   {face} panel  I={I:4.2f} A  env_equiv={env:7.1f} C  ->  T_eq = {T:7.1f} C")
    print()
