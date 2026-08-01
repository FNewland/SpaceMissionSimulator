# CubeSat Nadir-Pointing ADCS Telemetry Simulator — Technical Note

This note explains the models behind `cubesat_adcs_sim.py`, the fault that is
injected, and how to read the telemetry it produces. The simulator generates
realistic, physically self-consistent values for the **sensor inputs**,
**ADCS algorithm internal states**, and **actuator outputs** of a 3U CubeSat
flying a nadir-pointing attitude profile in a Sun-synchronous low-Earth orbit,
for a user-defined epoch and orbit, across three phases: normal operation, a
debris-impact loss of control, and a failed ground recovery attempt.

## 1. What it produces

Running the script writes four files next to it:

- `cubesat_adcs_telemetry.csv` — the full time series (10,801 samples at 1 s, 38 channels).
- `plot_1_sensors.png` — magnetometer and Sun-sensor telemetry plus eclipse / Sun-valid flags.
- `plot_2_estimator.png` — pointing error, estimated body rates, and control mode.
- `plot_3_actuators.png` — reaction-wheel torque and stored momentum, magnetorquer dipole.
- `plot_4_event_zoom.png` — second-by-second zooms of the impact and recovery events.

In every plot the background shading marks the phase: **green** = normal,
**orange** = post-impact slow roll, **red** = recovery attempt and after.

## 2. How to configure a run

All scenario inputs live in the `USER CONFIGURATION BLOCK` at the top of the script.
The main knobs:

- `EPOCH_UTC` — the date/time. The Sun direction and the geomagnetic field are both
  computed from this epoch, so changing it changes the sensor geometry and eclipse timing.
- `ORBIT` — classical orbital elements. Default is a ~500 km, 97.4° Sun-synchronous orbit
  (the J2 nodal-regression check prints `+0.985 deg/day`, confirming Sun-synchronicity).
- `T_TOTAL`, `DT` — duration and time step (default 3 hours at 1 s).
- `T_IMPACT`, `T_RECOVERY` — the times of the two events.
- `IMPACT_DELTA_OMEGA`, `CORRUPTED_AXIS`, `CORRUPTION_FACTOR` — the fault definition.

## 3. Environment and orbit models

The orbit is propagated with a Kepler solver plus secular J2 drift of the right
ascension of the ascending node and argument of perigee, which is what makes the
default orbit genuinely Sun-synchronous. Position and velocity are produced in an
Earth-centred inertial frame.

The Sun direction uses a low-precision analytic ephemeris (Vallado, ~0.01° accuracy),
and eclipse is detected with a cylindrical-shadow test. The geomagnetic field uses a
tilted-dipole (centred-dipole IGRF) model rotated from Earth-fixed to inertial by
Greenwich Mean Sidereal Time; field magnitude at 500 km ranges ~24–51 µT, matching
reality (weaker near the magnetic equator, roughly double near the poles).

The nadir-pointing target is the Local-Vertical-Local-Horizontal (LVLH) frame:
+Z points to nadir, –Y along the orbit-normal, +X completes the set near the velocity
direction. Holding nadir lock therefore requires the body to rotate once per orbit
about the orbit normal (~0.06°/s), which the controller tracks as a feed-forward rate.

## 4. Sensors (telemetry inputs)

- **Three-axis magnetometer** — true body-frame field plus a fixed bias (a few hundred nT)
  and white noise (250 nT, 1σ). Reported in microtesla per axis plus magnitude.
- **Coarse Sun sensor** — unit Sun vector in the body frame with ~0.5° angular noise,
  valid only when the spacecraft is sunlit (NaN and a `sun_valid = 0` flag during eclipse).

There is no star tracker or rate gyro in this sensor suite, which is typical of a
low-cost CubeSat. Body rate is therefore treated as the output of a lightly filtered
rate estimate (see below).

## 5. Attitude determination (algorithm internal state)

The estimator is a **predict-and-correct (complementary) filter**, the practical
work-horse for mag + Sun CubeSats:

1. **Predict** — propagate the attitude quaternion forward using the estimated body rate.
2. **Correct** — when the Sun is visible **and** the Sun and field vectors are not
   near-collinear, solve attitude with the **TRIAD** algorithm from the two vector pairs,
   then blend it into the prediction (30% weight). Two guards make this robust:
   a *geometry check* (TRIAD is ill-conditioned when the two reference vectors are nearly
   parallel) and an *innovation gate* (a TRIAD fix that disagrees with the prediction by
   more than 25° is rejected). In eclipse or bad geometry the filter coasts on the prediction.

Reported states include the estimated quaternion, the estimated body rates, the
attitude error versus the nadir target, the control mode, and — as a simulation-only
diagnostic — `est_err_deg`, the true error of the estimate against ground truth
(a real spacecraft cannot measure this). In normal operation the estimate tracks truth
to ~1.2° on average, which is representative of mag + Sun determination.

## 6. Control law and actuators (algorithm outputs)

Control is **PD on the quaternion error**, expressed in the body frame:

> τ = K_p · δq_vec − K_d · (ω_est − ω_orbit)

- **Reaction wheels** realise the commanded torque (rated 1 mN·m, momentum capacity
  30 mN·m·s). Stored wheel momentum is tracked and saturates at the hardware limit.
- **Magnetorquers** run a cross-product momentum-dump law, m ∝ h_wheel × B, to bleed
  stored wheel momentum back out against the Earth field (rated 0.2 A·m²). In normal
  operation they sit at a few hundredths of an A·m², as expected.

In normal operation the spacecraft holds nadir to a mean pointing error of ~0.3°
(95th percentile < 0.8°) with body rates near the orbital rate — a healthy, quiescent
telemetry signature.

## 7. The fault scenario

**Debris impact (`T_IMPACT`, default 4000 s).** An instantaneous angular-momentum kick
(`IMPACT_DELTA_OMEGA`) is applied to the body, the ADCS trips to **SAFE** (actuators off),
and the spacecraft enters a slow tumble. The impact also plants a *latent* control-gain
corruption on one axis (`CORRUPTED_AXIS`, default X) — a 10× gain error — which does
nothing while control is off. The resulting roll is slow (~2°/s) and grows gradually under
gravity-gradient torque on the elongated 3U body.

**Recovery attempt (`T_RECOVERY`, default 8000 s).** The ground re-enables control. The
corrupted axis now commands roughly ten times the intended torque; its faulted wheel driver
delivers up to 10× the rated torque (±10 mN·m, clearly visible in the actuator telemetry).
The over-gained loop is violently unstable, thrashing the spacecraft and pumping angular
momentum into the wheels until a wheel saturates, at which point the ADCS **auto-safes**
again and the spacecraft is left tumbling with saturated wheels. This is exactly the
requested behaviour: the corrupted axis applies 10× torque until the system switches itself
off. With the defaults the recovery window lasts ~200 s.

## 8. How to read the key signatures

| Telemetry | Normal | Post-impact | Recovery |
|---|---|---|---|
| Pointing error | < ~1° | swings 0–180° (tumbling) | brief violent thrash |
| Body rate | ~orbital (0.06°/s) | slow roll ~2–3°/s | spikes to tens of °/s |
| Wheel torque | ~0, small corrections | zero (control off) | corrupted axis railed at ±10 mN·m |
| Wheel momentum | near zero (dumped) | frozen | ramps to ±30 mN·m·s saturation |
| Magnetorquer | small, ~0.03 A·m² | zero | saturated bursts |
| Control mode | NORMAL | SAFE | RECOVERY → SAFE |

## 9. Assumptions and limitations

The models are engineering-grade but simplified: rigid-body dynamics; a centred-dipole
field rather than full IGRF; an analytic Sun and a cylindrical (no penumbra) shadow;
sensor errors modelled as bias + Gaussian noise; and a body-rate estimate that stands in
for a MEMS rate sensor / attitude-rate filter rather than a from-scratch rate observer.
The recovery instability is a faithful but discrete-time depiction of a grossly over-gained
loop. These choices keep the simulation fast and transparent while producing telemetry whose
magnitudes and behaviour are representative of a real nadir-pointing CubeSat ADCS. For
higher fidelity the natural extensions are a full IGRF model, an EKF/MEKF with an explicit
gyro-bias state, and aerodynamic/SRP disturbance models tied to the spacecraft geometry.
