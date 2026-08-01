import random; random.seed(3)
DT=1.0; PERIOD=5670.0; ECL=0.35
TAU={"obc":900.0,"battery":1200.0}; CAP={"obc":800.0,"battery":2000.0}
G=0.5   # tcs.yaml thermal_coupling.zone_conductance.battery_obc
HP={"battery":6.0,"obc":4.0}
ON,OFF=1.0,5.0          # tcs.yaml heaters[battery] on_temp_c / off_temp_c
def run(line_bat_on, line_obc_on, hours=12):
    Tb=20.0; To=20.0; htr_b=False; htr_o=False; n=int(hours*3600/DT)
    trips=0; mn=1e9
    for i in range(n):
        t=i*DT; ecl=(t%PERIOD)<ECL*PERIOD; env=10.0 if ecl else 12.0
        # coupling (tcs_basic._apply_thermal_coupling)
        hf=G*(To-Tb); Tb+=hf/CAP["battery"]*DT; To-=hf/CAP["obc"]*DT
        # battery thermostat (tcs_basic.py:461-470)
        if line_bat_on:
            if Tb<=ON: 
                if not htr_b: trips+=1
                htr_b=True
            elif Tb>=OFF: htr_b=False
        else: htr_b=False
        bp=HP["battery"] if htr_b else 0.0
        Tb+=((env-Tb)/TAU["battery"]+bp/CAP["battery"])*DT+random.gauss(0,0.02)
        # obc heater = EPS line state (tcs_basic.py:477-482)
        htr_o=line_obc_on
        op=HP["obc"] if htr_o else 0.0
        To+=((env-To)/TAU["obc"]+op/CAP["obc"])*DT+random.gauss(0,0.03)
        mn=min(mn,Tb)
    return Tb,To,htr_b,trips,mn
for lb,lo in [(False,False),(True,False),(True,True)]:
    Tb,To,h,tr,mn=run(lb,lo)
    print(f"htr_bat line={lb!s:5} htr_obc line={lo!s:5} -> T_bat={Tb:6.2f}C (min over 12h {mn:6.2f}C) "
          f"T_obc={To:6.2f}C  htr_battery={h}  thermostat ON-trips in 12 h = {tr}")
print("\nBattery thermostat ON setpoint = %.1f C ; minimum achievable T_bat = env_int(eclipse) = 10.0 C" % ON)
print("Margin to trip: %.1f C  -> heater can NEVER autonomously turn on." % (10.0-ON))
