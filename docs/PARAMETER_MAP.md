# Parameter map — parameter → SID → limits → S12

**Generated** by `tools/gen_parameter_map.py` from `configs/eosat1/`. Do not edit; edit the configuration and re-run.
Byte-stability is pinned in CI, so this file cannot drift from the configuration it describes.

Columns:

| Column | Meaning |
|---|---|
| **HK SID** | Housekeeping structures that carry this parameter in routine telemetry. Blank means it is never downlinked routinely. |
| **S20** | `on_demand: true` — readable with `GET_PARAM` (S20.3 in this suite; see `docs/PUS_SERVICE_REFERENCE.md`). |
| **Ground limits** | Yellow/red bands from `mcs/limits.yaml`, checked by the MCS on every sample. |
| **S12** | On-board monitors from `monitoring/s12_definitions.yaml`. These fire autonomously and can drive S19 event-actions. |
| **Stub** | The catalogue declares the parameter but no model computes it, so an S20 read returns 0.0. Declared, not discovered. |

**Reachability rule.** Every parameter must be in at least one HK SID, or be `on_demand`, or be listed in the `DERIVED_ONLY` allowlist in `tests/test_content/test_parameter_chain.py` with a reason. A parameter that is limited or S12-monitored but unreachable is a monitor that can never fire; that is a test failure, not a note.

## Summary

| | Count |
|---|---:|
| Catalogued parameters | 446 |
| In at least one HK SID | 321 |
| On-demand (S20-readable) | 146 |
| Declared stubs (model does not compute) | 20 |
| Ground limit bands | 52 |
| S12 on-board monitors | 46 |
| Ground-derived pseudo-parameters (`derived.yaml`) | 3 |

## Housekeeping structures

| SID | Name | Members |
|---:|---|---:|
| 1 | EPS | 52 |
| 2 | AOCS | 81 |
| 3 | TCS | 17 |
| 4 | Platform | 30 |
| 5 | Payload | 31 |
| 6 | TTC | 32 |
| 7 | AOCS_DETERMINATION | 41 |
| 8 | EPS Power Management | 13 |
| 9 | PROP | 26 |
| 11 | Beacon | 7 |

## aocs

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0200` | aocs.att_q1 |  | 2 |  |  |  |  |
| `0x0201` | aocs.att_q2 |  | 2 |  |  |  |  |
| `0x0202` | aocs.att_q3 |  | 2 |  |  |  |  |
| `0x0203` | aocs.att_q4 |  | 2 |  |  |  |  |
| `0x0204` | aocs.rate_roll | deg/s | 2 |  | -0.5…0.5 / -2…2 |  |  |
| `0x0205` | aocs.rate_pitch | deg/s | 2 |  | -0.5…0.5 / -2…2 |  |  |
| `0x0206` | aocs.rate_yaw | deg/s | 2 |  | -0.5…0.5 / -2…2 |  |  |
| `0x0207` | aocs.rw1_speed | RPM | 2 |  | -5000…5000 / -5500…5500 | AOCS Reaction Wheel 1 Overspeed (-5000.0…5000.0, WARNING) |  |
| `0x0208` | aocs.rw2_speed | RPM | 2 |  |  | AOCS Reaction Wheel 2 Overspeed (-5000.0…5000.0, WARNING) |  |
| `0x0209` | aocs.rw3_speed | RPM | 2 |  |  | AOCS Reaction Wheel 3 Overspeed (-5000.0…5000.0, WARNING) |  |
| `0x020A` | aocs.rw4_speed | RPM | 2 |  |  | AOCS Reaction Wheel 4 Overspeed (-5000.0…5000.0, WARNING) |  |
| `0x020B` | aocs.mag_x | nT |  | yes |  |  |  |
| `0x020C` | aocs.mag_y | nT |  | yes |  |  |  |
| `0x020D` | aocs.mag_z | nT |  | yes |  |  |  |
| `0x020F` | aocs.mode |  | 2 |  |  |  |  |
| `0x0210` | aocs.gps_lat | deg |  | yes |  |  |  |
| `0x0211` | aocs.gps_lon | deg |  | yes |  |  |  |
| `0x0212` | aocs.gps_alt | km |  | yes |  |  |  |
| `0x0215` | aocs.css_sun_confidence |  |  | yes |  |  |  |
| `0x0216` | aocs.solar_beta | deg |  | yes |  |  |  |
| `0x0217` | aocs.att_error | deg | 2 |  | -1…1 / -2…2 | AOCS Attitude Error High (0.0…1.0, WARNING) |  |
| `0x0218` | aocs.rw1_temp | C | 2 | yes | 0…60 / -5…70 |  |  |
| `0x0219` | aocs.rw2_temp | C | 2 | yes |  |  |  |
| `0x021A` | aocs.rw3_temp | C | 2 | yes |  |  |  |
| `0x021B` | aocs.rw4_temp | C | 2 | yes |  |  |  |
| `0x0220` | aocs.sun_body_x |  |  | yes |  |  |  |
| `0x0221` | aocs.sun_body_y |  |  | yes |  |  |  |
| `0x0222` | aocs.sun_body_z |  |  | yes |  |  |  |
| `0x0223` | aocs.mag_a_x | nT |  | yes |  |  |  |
| `0x0224` | aocs.mag_a_y | nT |  | yes |  |  |  |
| `0x0225` | aocs.mag_a_z | nT |  | yes |  |  |  |
| `0x0226` | aocs.mag_b_x | nT |  | yes |  |  |  |
| `0x0227` | aocs.mag_b_y | nT |  | yes |  |  |  |
| `0x0228` | aocs.mag_b_z | nT |  | yes |  |  |  |
| `0x0229` | aocs.mag_select |  |  | yes |  |  |  |
| `0x0230` | aocs.wheel_torque_cmd | Nm |  | yes |  |  | yes |
| `0x0231` | aocs.gps_ecef_x | m |  | yes |  |  | yes |
| `0x0232` | aocs.gps_ecef_y | m |  | yes |  |  | yes |
| `0x0233` | aocs.gps_ecef_z | m |  | yes |  |  | yes |
| `0x0234` | aocs.gps_vel_x | m/s |  | yes |  |  | yes |
| `0x0235` | aocs.gps_vel_y | m/s |  | yes |  |  | yes |
| `0x0236` | aocs.gps_vel_z | m/s |  | yes |  |  | yes |
| `0x0240` | aocs.st1_status |  | 2 |  |  | AOCS Star Tracker 1 Blind (0.0…2.5, ALARM) |  |
| `0x0241` | aocs.st1_num_stars |  | 2 |  |  |  |  |
| `0x0242` | aocs.st2_num_stars |  | 2 |  |  |  |  |
| `0x0243` | aocs.st2_status |  | 2 |  |  | AOCS Star Tracker 2 Blind (0.0…2.5, ALARM) |  |
| `0x0244` | aocs.st_selected |  | 2 |  |  |  |  |
| `0x0245` | aocs.css_sun_x |  | 2 |  |  |  |  |
| `0x0246` | aocs.css_sun_y |  | 2 |  |  |  |  |
| `0x0247` | aocs.css_sun_z |  | 2 |  |  |  |  |
| `0x0248` | aocs.css_valid |  | 2 |  |  |  |  |
| `0x0250` | aocs.rw1_current | A | 2 |  |  |  |  |
| `0x0251` | aocs.rw2_current | A | 2 |  |  |  |  |
| `0x0252` | aocs.rw3_current | A | 2 |  |  |  |  |
| `0x0253` | aocs.rw4_current | A | 2 |  |  |  |  |
| `0x0254` | aocs.rw1_enabled |  | 2 |  |  |  |  |
| `0x0255` | aocs.rw2_enabled |  | 2 |  |  |  |  |
| `0x0256` | aocs.rw3_enabled |  | 2 |  |  |  |  |
| `0x0257` | aocs.rw4_enabled |  | 2 |  |  |  |  |
| `0x0258` | aocs.mtq_x_duty | % | 2 |  |  |  |  |
| `0x0259` | aocs.mtq_y_duty | % | 2 |  |  |  |  |
| `0x025A` | aocs.mtq_z_duty | % | 2 |  |  |  |  |
| `0x025B` | aocs.total_momentum | Nms | 2 |  | 0…0.018 / 0…0.03 |  |  |
| `0x025C` | aocs.mag_enabled |  | 2 |  |  |  |  |
| `0x025D` | aocs.gyro_enabled |  | 2 |  |  |  |  |
| `0x025E` | aocs.mtq_enabled |  | 2 |  |  |  |  |
| `0x0260` | aocs.wheels_enabled |  |  | yes |  |  |  |
| `0x0261` | aocs.mtq_dipole_total | Am^2 |  | yes |  |  | yes |
| `0x0262` | aocs.submode |  | 2 |  |  |  |  |
| `0x0264` | aocs.time_in_mode | s | 2 |  |  |  |  |
| `0x0270` | aocs.gyro_bias_x | deg/s | 2 |  |  | AOCS Gyro X-Axis Bias High (-0.5…0.5, WARNING) |  |
| `0x0271` | aocs.gyro_bias_y | deg/s | 2 |  |  | AOCS Gyro Y-Axis Bias High (-0.5…0.5, WARNING) |  |
| `0x0272` | aocs.gyro_bias_z | deg/s | 2 |  |  | AOCS Gyro Z-Axis Bias High (-0.5…0.5, WARNING) |  |
| `0x0273` | aocs.gyro_temp | C | 2 |  | 0…50 / -10…60 |  |  |
| `0x0274` | aocs.gps_fix |  | 2 |  |  |  |  |
| `0x0275` | aocs.gps_pdop |  | 2 |  | 0…4 / 0…6 |  |  |
| `0x0276` | aocs.gps_num_sats |  | 2 |  |  |  |  |
| `0x0277` | aocs.mag_field_total | nT | 2 |  |  |  |  |
| `0x027A` | aocs.css_px |  | 2 | yes |  |  |  |
| `0x027B` | aocs.css_mx |  | 2 | yes |  |  |  |
| `0x027C` | aocs.css_py |  | 2 | yes |  |  |  |
| `0x027D` | aocs.css_my |  | 2 | yes |  |  |  |
| `0x027E` | aocs.css_pz |  | 2 | yes |  |  |  |
| `0x027F` | aocs.css_mz |  | 2 | yes |  |  |  |
| `0x0280` | aocs.slew_time_remaining | s | 2 |  |  |  |  |
| `0x0281` | aocs.slew_progress | % | 2 |  |  |  |  |
| `0x0282` | aocs.momentum_saturation_pct | % | 2 |  |  |  |  |
| `0x0283` | aocs.attitude_source |  | 2 |  |  |  |  |
| `0x0284` | aocs.rw1_bearing_health | % | 2 |  |  |  |  |
| `0x0285` | aocs.rw2_bearing_health | % | 2 |  |  |  |  |
| `0x0286` | aocs.rw3_bearing_health | % | 2 |  |  |  |  |
| `0x0287` | aocs.rw4_bearing_health | % | 2 |  |  |  |  |
| `0x0288` | aocs.css_head_health_px | % | 2 |  |  |  |  |
| `0x0289` | aocs.css_head_health_mx | % | 2 |  |  |  |  |
| `0x028A` | aocs.css_head_health_py | % | 2 |  |  |  |  |
| `0x028B` | aocs.css_head_health_my | % | 2 |  |  |  |  |
| `0x028C` | aocs.css_head_health_pz | % | 2 |  |  |  |  |
| `0x028D` | aocs.css_head_health_mz | % | 2 |  |  |  |  |
| `0x028E` | aocs.rw_cmd_sum | Nm |  | yes |  |  |  |
| `0x0292` | aocs.slew_state |  |  | yes |  |  |  |
| `0x0294` | aocs.gyro_units_on |  | 2 |  |  |  |  |
| `0x0295` | aocs.gyro_saturated |  | 2 |  |  |  |  |
| `0x0296` | aocs.gyro_bias_injected | deg/s | 2 |  |  |  |  |
| `0x0297` | aocs.commanded_mode |  | 2 |  |  |  |  |
| `0x0298` | aocs.mode_demoted |  | 2 |  |  |  |  |
| `0x0299` | aocs.fine_degraded |  | 2 |  |  |  |  |
| `0x029A` | aocs.desat_inhibit |  | 2 |  |  |  |  |
| `0x029B` | aocs.h_body_nms | Nms | 2 |  |  |  |  |
| `0x029C` | aocs.desat_ineffective |  | 2 |  |  |  |  |
| `0x029D` | aocs.wheels_commissioned |  | 2 |  |  |  |  |
| `0x02A0` | aocs.q_est_1 |  | 7 |  |  |  |  |
| `0x02A1` | aocs.q_est_2 |  | 7 |  |  |  |  |
| `0x02A2` | aocs.q_est_3 |  | 7 |  |  |  |  |
| `0x02A3` | aocs.q_est_4 |  | 7 |  |  |  |  |
| `0x02A4` | aocs.rate_est_x | deg/s | 7 |  |  |  |  |
| `0x02A5` | aocs.rate_est_y | deg/s | 7 |  |  |  |  |
| `0x02A6` | aocs.rate_est_z | deg/s | 7 |  |  |  |  |
| `0x02A7` | aocs.knowledge_err_3sig | arcsec | 7 |  |  |  |  |
| `0x02A8` | aocs.control_err | deg | 7 |  |  |  |  |
| `0x02A9` | aocs.control_err_arcsec | arcsec | 7 |  |  |  |  |
| `0x02AA` | aocs.total_pointing_err_3sig | deg | 7 |  |  |  |  |
| `0x02AB` | aocs.q_ref_x |  | 7 |  |  |  |  |
| `0x02AC` | aocs.q_ref_y |  | 7 |  |  |  |  |
| `0x02AD` | aocs.q_ref_z |  | 7 |  |  |  |  |
| `0x02AE` | aocs.q_ref_w |  | 7 |  |  |  |  |
| `0x02AF` | aocs.pointing_frame |  | 7 |  |  |  |  |
| `0x02B0` | aocs.nadir_body_x |  | 7 |  |  |  |  |
| `0x02B1` | aocs.nadir_body_y |  | 7 |  |  |  |  |
| `0x02B2` | aocs.nadir_body_z |  | 7 |  |  |  |  |
| `0x02B3` | aocs.sun_body_true_x |  | 7 |  |  |  |  |
| `0x02B4` | aocs.sun_body_true_y |  | 7 |  |  |  |  |
| `0x02B5` | aocs.sun_body_true_z |  | 7 |  |  |  |  |
| `0x02B6` | aocs.h_body_x | Nms | 7 |  |  |  |  |
| `0x02B7` | aocs.h_body_y | Nms | 7 |  |  |  |  |
| `0x02B8` | aocs.h_body_z | Nms | 7 |  |  |  |  |
| `0x02B9` | aocs.h_body_mag | Nms | 7 |  |  |  |  |
| `0x02BA` | aocs.h_env_frac | % | 7 |  | 0…70 / 0…90 | AOCS Momentum Envelope High (-1.0…70.0, WARNING) |  |
| `0x02BB` | aocs.mtq_dipole_x | Am2 | 7 |  |  |  |  |
| `0x02BC` | aocs.mtq_dipole_y | Am2 | 7 |  |  |  |  |
| `0x02BD` | aocs.mtq_dipole_z | Am2 | 7 |  |  |  |  |
| `0x02BE` | aocs.tau_dist_x | uNm | 7 |  |  |  |  |
| `0x02BF` | aocs.tau_dist_y | uNm | 7 |  |  |  |  |
| `0x02C0` | aocs.tau_dist_z | uNm | 7 |  |  |  |  |
| `0x02C1` | aocs.est_source |  | 7 |  |  |  |  |
| `0x02C2` | aocs.est_innov_rej_count |  | 7 |  |  |  |  |
| `0x02C3` | aocs.rate_stability_3sig | deg/s | 7 |  |  |  |  |
| `0x02C4` | aocs.settle_time_s | s | 7 |  |  |  |  |
| `0x02C5` | aocs.fine_ready |  | 7 |  |  |  |  |
| `0x02C6` | aocs.fidelity_mode |  | 7 |  |  |  |  |
| `0x02C7` | aocs.geoloc_err_m | m | 7 |  |  |  |  |
| `0x02C8` | aocs.power_w | W | 7 |  |  |  |  |

## eps

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0100` | eps.bat_voltage | V | 1, 11 |  | 23…29 / 22…29.5 |  |  |
| `0x0101` | eps.bat_soc | % | 1, 11 |  | 25…95 / 15…100 | EPS Battery SoC Warning (20.0…100.0, WARNING)<br>EPS Battery SoC Critical (10.0…100.0, ALARM) |  |
| `0x0102` | eps.bat_temp | C | 1 |  | 2…40 / 0…45 | EPS Battery Overtemp (-5.0…45.0, WARNING)<br>EPS Battery Undertemp (-5.0…50.0, WARNING) |  |
| `0x0103` | eps.sa_a_current | A | 1 |  |  | EPS Solar Array A Low Current (0.1…50.0, WARNING) |  |
| `0x0104` | eps.sa_b_current | A | 1 |  |  | EPS Solar Array B Low Current (0.1…50.0, WARNING) |  |
| `0x0105` | eps.bus_voltage | V | 1 |  | 27…29 / 26.5…29.5 | EPS Bus Voltage Low (27.0…35.0, WARNING)<br>EPS Bus Voltage Critical (25.0…35.0, ALARM) |  |
| `0x0106` | eps.power_cons | W | 1 |  |  |  |  |
| `0x0107` | eps.power_gen | W | 1 |  |  |  |  |
| `0x0108` | eps.eclipse_flag |  | 1 |  |  |  |  |
| `0x0109` | eps.bat_current | A | 1 |  |  | EPS Battery Overcurrent (-15.0…15.0, WARNING) |  |
| `0x010A` | eps.bat_capacity_wh | Wh |  | yes |  |  |  |
| `0x010B` | eps.sa_a_voltage | V | 1 |  |  |  |  |
| `0x010C` | eps.sa_b_voltage | V | 1 |  |  |  |  |
| `0x010D` | eps.oc_trip_flags |  | 1 |  |  |  |  |
| `0x010E` | eps.uv_flag |  | 1 |  |  |  |  |
| `0x010F` | eps.ov_flag |  | 1 |  |  |  |  |
| `0x0110` | eps.pl_obc |  | 1 |  |  |  |  |
| `0x0111` | eps.pl_ttc_rx |  | 1 |  |  |  |  |
| `0x0112` | eps.pl_ttc_tx |  | 1 |  |  |  |  |
| `0x0113` | eps.pl_payload |  | 1 |  |  |  |  |
| `0x0114` | eps.pl_fpa_cooler |  | 1 |  |  |  |  |
| `0x0115` | eps.pl_htr_bat |  | 1 |  |  |  |  |
| `0x0116` | eps.pl_htr_obc |  | 1 |  |  |  |  |
| `0x0117` | eps.pl_aocs_wheels |  | 1 |  |  |  |  |
| `0x0118` | eps.line_current_0 | A | 1 |  | 0…1.8 / 0…2 |  |  |
| `0x0119` | eps.line_current_1 | A | 1 |  | 0…0.25 / 0…0.3 |  |  |
| `0x011A` | eps.line_current_2 | A | 1 |  | 0…0.9 / 0…1 |  |  |
| `0x011B` | eps.line_current_3 | A | 1 |  | 0…2.2 / 0…2.5 |  |  |
| `0x011C` | eps.line_current_4 | A | 1 |  | 0…0.8 / 0…1 |  |  |
| `0x011D` | eps.line_current_5 | A | 1 |  | 0…0.4 / 0…0.5 |  |  |
| `0x011E` | eps.line_current_6 | A | 1 |  | 0…0.25 / 0…0.3 |  |  |
| `0x011F` | eps.line_current_7 | A | 1 |  | 0…0.7 / 0…0.8 |  |  |
| `0x0120` | eps.bat_dod | % | 1 |  | 0…60 / 0…80 |  |  |
| `0x0121` | eps.bat_cycles |  | 1 |  |  |  |  |
| `0x0122` | eps.mppt_efficiency |  | 1 |  |  |  |  |
| `0x0123` | eps.sa_age_factor |  | 1 |  |  |  |  |
| `0x0124` | eps.sa_a_degradation |  |  | yes |  |  |  |
| `0x0125` | eps.sa_b_degradation |  |  | yes |  |  |  |
| `0x0126` | eps.sa_lifetime_hours | h |  | yes |  |  |  |
| `0x0127` | eps.sep_timer_active |  |  | yes |  |  |  |
| `0x0128` | eps.sep_timer_remaining | s |  | yes |  |  |  |
| `0x0129` | eps.spacecraft_phase |  | 4 |  |  |  |  |
| `0x012A` | eps.pdm_unsw_status |  |  | yes |  |  |  |
| `0x012B` | eps.sa_px_current | A | 1 | yes |  |  |  |
| `0x012C` | eps.sa_mx_current | A | 1 | yes |  |  |  |
| `0x012D` | eps.sa_py_current | A | 1 | yes |  |  |  |
| `0x012E` | eps.sa_my_current | A | 1 | yes |  |  |  |
| `0x012F` | eps.sa_pz_current | A | 1 | yes |  |  |  |
| `0x0130` | eps.sa_mz_current | A | 1 | yes |  |  |  |
| `0x0131` | eps.per_switch_state |  | 1 |  |  |  |  |
| `0x0132` | eps.charge_rate_a | A | 1 |  |  |  |  |
| `0x0133` | eps.solar_array_drive_angle | deg | 1, 8 |  |  |  |  |
| `0x0134` | eps.load_shed_stage |  | 1 |  |  |  |  |
| `0x0135` | eps.power_margin_w | W | 1 |  |  |  |  |
| `0x0136` | eps.battery_health_pct | % | 1 |  |  |  |  |
| `0x0137` | eps.eps_mode |  | 1 |  |  |  |  |
| `0x0138` | eps.battery_heater_on |  | 1 |  |  |  |  |
| `0x0139` | eps.battery_heater_setpoint | C | 1 |  |  |  |  |
| `0x013A` | eps.eclipse_active |  | 8 |  |  |  |  |
| `0x013B` | eps.power_margin | W | 8 |  |  |  |  |
| `0x013C` | eps.subsys_power_eps | W | 8 |  |  |  |  |
| `0x013D` | eps.subsys_power_aocs | W | 8 |  |  |  |  |
| `0x013E` | eps.subsys_power_tcs | W | 8 |  |  |  |  |
| `0x013F` | eps.subsys_power_ttc | W | 8 |  |  |  |  |
| `0x0140` | eps.subsys_power_payload | W | 8 |  |  |  |  |
| `0x0141` | eps.subsys_power_obdh | W | 8 |  |  |  |  |
| `0x0143` | eps.actual_charge_current_a | A | 1 |  |  |  |  |
| `0x0144` | eps.wing_status |  | 1, 8 |  |  |  |  |
| `0x0145` | eps.wing_deploy_timer | s |  | yes |  |  |  |
| `0x0146` | eps.sa_wing_py_current | A | 8 |  |  |  |  |
| `0x0147` | eps.sa_wing_my_current | A | 8 |  |  |  |  |
| `0x0148` | eps.bus_load_current | A | 8 |  |  |  |  |
| `0x0149` | eps.fpa_cooler_cmd |  |  | yes |  |  |  |
| `0x014A` | eps.pl_prop |  |  | yes |  |  |  |
| `0x014B` | eps.line_current_8 | A |  | yes |  |  |  |

## fdir

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0700` | fdir.level |  |  | yes |  |  |  |
| `0x0701` | fdir.active_alarms |  |  | yes |  |  |  |
| `0x0702` | fdir.critical_alarms |  |  | yes |  |  |  |
| `0x0703` | fdir.high_alarms |  |  | yes |  |  |  |

## obdh

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0300` | obdh.mode |  | 4, 11 |  |  |  |  |
| `0x0301` | obdh.temp | C |  | yes |  |  |  |
| `0x0302` | obdh.cpu_load | % | 4 |  | 0…85 / 0…98 | OBDH CPU Load High (0.0…90.0, WARNING) |  |
| `0x0303` | obdh.mmm_used | % | 4 |  |  | OBDH Memory Usage High (0.0…90.0, WARNING) |  |
| `0x0304` | obdh.tc_rx_count |  | 4 |  |  |  |  |
| `0x0305` | obdh.tc_acc_count |  | 4 |  |  |  |  |
| `0x0306` | obdh.tc_rej_count |  | 4 |  |  |  |  |
| `0x0307` | obdh.tm_pkt_count |  | 4 |  |  |  |  |
| `0x0308` | obdh.uptime | s | 4 |  |  |  |  |
| `0x0309` | obdh.obc_time | s | 4 |  |  |  |  |
| `0x030A` | obdh.reboot_count |  | 4, 11 |  | 0…2 / 0…5 | OBDH Excessive Reboots (0.0…3.0, ALARM) |  |
| `0x030B` | obdh.sw_version |  |  | yes |  |  |  |
| `0x030C` | obdh.active_obc |  | 4, 11 |  |  |  |  |
| `0x030D` | obdh.obc_b_status |  | 4 |  |  |  |  |
| `0x030E` | obdh.active_bus |  | 4 |  |  |  |  |
| `0x030F` | obdh.bus_a_status |  | 4 |  |  |  |  |
| `0x0310` | obdh.bus_b_status |  | 4 |  |  |  |  |
| `0x0311` | obdh.sw_image |  | 4, 11 |  |  |  |  |
| `0x0312` | obdh.hktm_buf_fill | % | 4 |  | 0…80 / 0…95 |  |  |
| `0x0313` | obdh.event_buf_fill | % | 4 |  | 0…80 / 0…95 |  |  |
| `0x0314` | obdh.alarm_buf_fill | % | 4 |  | 0…80 / 0…95 |  |  |
| `0x0315` | obdh.last_tc_error_code |  | 4 |  |  |  |  |
| `0x0316` | obdh.last_reboot_cause |  |  | yes |  |  |  |
| `0x0317` | obdh.boot_count_a |  |  | yes |  |  |  |
| `0x0318` | obdh.boot_count_b |  |  | yes |  |  |  |
| `0x0319` | obdh.seu_count |  | 4 |  | 0…10 / 0…50 |  |  |
| `0x031A` | obdh.scrub_progress | % | 4 |  |  |  |  |
| `0x031B` | obdh.task_count |  | 4 |  |  |  |  |
| `0x031C` | obdh.stack_usage | % | 4 |  | 0…80 / 0…95 |  |  |
| `0x031D` | obdh.heap_usage | % | 4 |  | 0…80 / 0…95 |  |  |
| `0x031E` | obdh.mem_errors |  | 4 |  |  | OBDH Memory Errors (0.0…5.0, WARNING) |  |
| `0x0A10` | obdh.storage_fill | % |  | yes |  |  |  |
| `0x0A11` | obdh.can_bus_status |  |  | yes |  |  |  |
| `0x0A12` | obdh.obc_time_fine |  | 4 |  |  |  |  |
| `0x0A13` | obdh.time_ref |  | 4 |  |  |  |  |
| `0x0A14` | obdh.time_since_sync_s | s | 4 |  | 0…43200 / 0…86400 |  |  |

## payload

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0600` | payload.mode |  | 5 |  |  |  |  |
| `0x0601` | payload.fpa_temp | C | 5 |  | -22.5…13.5 / -26…17 | Payload FPA Temperature (-22.5…13.5, WARNING) |  |
| `0x0602` | payload.cooler_pwr | W | 5 |  |  |  |  |
| `0x0603` | payload.imager_temp | C | 5 |  |  |  |  |
| `0x0604` | payload.store_used | % | 5 |  |  | Payload Storage Full (0.0…95.0, WARNING) |  |
| `0x0605` | payload.image_count |  | 5 |  |  |  |  |
| `0x0606` | payload.scene_id |  |  | yes |  |  |  |
| `0x0609` | payload.checksum_errors |  | 5 |  |  | Payload Checksum Errors (0.0…0.5, WARNING) |  |
| `0x060A` | payload.mem_total_mb | MB | 5 |  |  |  |  |
| `0x060B` | payload.mem_used_mb | MB | 5 |  |  |  |  |
| `0x060C` | payload.last_scene_id |  | 5 |  |  |  |  |
| `0x060D` | payload.last_scene_quality | % | 5 |  |  |  |  |
| `0x0610` | payload.fpa_ready |  | 5 |  |  |  |  |
| `0x0611` | payload.imager_status |  |  | yes |  |  | yes |
| `0x0612` | payload.mem_segments_bad |  | 5 |  |  |  |  |
| `0x0613` | payload.duty_cycle_pct | % |  | yes |  |  |  |
| `0x0614` | payload.compression_ratio |  | 5 |  |  |  |  |
| `0x0615` | payload.cal_lamp_on |  | 5 |  |  |  |  |
| `0x0616` | payload.snr | dB | 5 |  | 30…55 / 20…60 | Payload SNR Low (25.0…100.0, WARNING) |  |
| `0x0617` | payload.detector_temp | C | 5 |  | -22…14 / -25.5…17.5 |  |  |
| `0x0618` | payload.integration_time | ms |  | yes |  |  |  |
| `0x0619` | payload.swath_width_km | km |  | yes |  |  |  |
| `0x0620` | payload.band_blue_snr | dB |  | yes |  |  |  |
| `0x0621` | payload.band_green_snr | dB |  | yes |  |  |  |
| `0x0622` | payload.band_red_snr | dB |  | yes |  |  |  |
| `0x0623` | payload.band_nir_snr | dB |  | yes |  |  |  |
| `0x0624` | payload.active_bands |  |  | yes |  |  |  |
| `0x0625` | payload.band_enable_mask |  |  | yes |  |  |  |
| `0x0626` | payload.att_quality_factor |  |  | yes |  |  |  |
| `0x0627` | payload.att_error_deg | deg |  | yes |  |  |  |
| `0x0628` | payload.gsd_m | m |  | yes |  |  |  |
| `0x0629` | payload.scene_entropy |  |  | yes |  |  |  |
| `0x062A` | payload.calibration_active |  | 5 |  |  |  |  |
| `0x062B` | payload.calibration_progress | % | 5 |  |  |  |  |
| `0x062C` | payload.last_calibration_time | s | 5 |  |  |  |  |
| `0x062D` | payload.transfer_active |  | 5 |  |  |  |  |
| `0x062E` | payload.transfer_progress | % | 5 |  |  |  |  |
| `0x062F` | payload.integration_time_ms | ms | 5 |  |  |  |  |
| `0x0631` | payload.fpa_setpoint | C |  | yes |  |  |  |
| `0x0632` | payload.fpa_stability | C |  | yes |  |  | yes |
| `0x0633` | payload.cooler_duty | % |  | yes |  |  | yes |
| `0x0635` | payload.integration_ms | ms |  | yes |  |  |  |
| `0x0636` | payload.gain_code | x |  | yes |  |  |  |
| `0x0638` | payload.int_time_blue | ms |  | yes |  |  |  |
| `0x0639` | payload.int_time_green | ms |  | yes |  |  |  |
| `0x063A` | payload.int_time_red | ms |  | yes |  |  |  |
| `0x063B` | payload.int_time_nir | ms |  | yes |  |  |  |
| `0x063C` | payload.shutter_pos |  |  | yes |  |  |  |
| `0x063D` | payload.filter_pos |  |  | yes |  |  |  |
| `0x063E` | payload.compression_algo |  |  | yes |  |  |  |
| `0x0640` | payload.cal_flag |  |  | yes |  |  |  |
| `0x0642` | payload.dark_frame_cnt |  |  | yes |  |  |  |
| `0x0643` | payload.bias_frame_cnt |  |  | yes |  |  |  |
| `0x0644` | payload.cal_mode |  | 5 |  |  |  |  |
| `0x0645` | payload.cal_abort_reason |  | 5 |  |  |  |  |
| `0x0646` | payload.cal_valid_mask |  | 5 |  |  |  |  |
| `0x0647` | payload.product_mb | MB | 5 |  |  |  |  |
| `0x0648` | payload.product_count |  | 5 |  |  |  |  |
| `0x0649` | payload.readout_active |  | 5 |  |  |  |  |
| `0x064A` | payload.cooler_floor_c | C | 5 |  |  |  |  |
| `0x064B` | payload.cooler_margin_k | K | 5 |  |  |  |  |

## prop

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0B00` | prop.state |  | 9 |  |  |  |  |
| `0x0B01` | prop.tank_pressure | bar | 9 |  | 6…24 / 4…26 | PROP Tank Pressure Band (6.0…24.0, WARNING)<br>PROP Tank Pressure Out-of-Band (4.0…26.0, ALARM)<br>PROP Tank Pressure Rate (-0.2…0.2, ALARM) |  |
| `0x0B02` | prop.propellant_mass | g | 9 |  | 20…401 / 5…401 | PROP Propellant Low (20.0…401.0, WARNING) |  |
| `0x0B03` | prop.propellant_pct | % | 9 |  |  |  |  |
| `0x0B04` | prop.tank_temp | C | 9 |  |  |  |  |
| `0x0B05` | prop.catbed_temp | C | 9 |  | -20…300 / -30…350 | PROP Catbed Temperature Band (-20.0…300.0, WARNING)<br>PROP Catbed Overtemp (-30.0…350.0, ALARM) |  |
| `0x0B06` | prop.latch_a_state |  | 9 |  |  |  |  |
| `0x0B07` | prop.latch_b_state |  | 9 |  |  |  |  |
| `0x0B08` | prop.fcv_state |  | 9 |  |  |  |  |
| `0x0B09` | prop.thrust_est | mN | 9 |  | 100…400 / 50…450 | PROP Low Thrust (100.0…400.0, WARNING) |  |
| `0x0B0A` | prop.isp_est | s | 9 |  |  |  |  |
| `0x0B0B` | prop.dv_accum | m/s | 9 |  |  |  |  |
| `0x0B0C` | prop.dv_remaining | m/s | 9 |  | 5…100 / 1…100 | PROP dV Envelope Low (5.0…100.0, WARNING) |  |
| `0x0B0D` | prop.burn_status |  | 9 |  |  |  |  |
| `0x0B0E` | prop.burn_dv_cmd | m/s | 9 |  |  |  |  |
| `0x0B0F` | prop.burn_dv_achieved | m/s | 9 |  |  |  |  |
| `0x0B10` | prop.burn_time_cmd | s | 9 |  |  |  |  |
| `0x0B11` | prop.burn_time_elapsed | s | 9 |  |  |  |  |
| `0x0B12` | prop.burn_count |  | 9 |  |  |  |  |
| `0x0B13` | prop.total_on_time | s | 9 |  |  |  |  |
| `0x0B14` | prop.abort_reason |  | 9 |  |  |  |  |
| `0x0B15` | prop.arm_inhibit_mask |  | 9 |  |  |  |  |
| `0x0B16` | prop.leak_rate | mg/s | 9 |  |  |  |  |
| `0x0B17` | prop.thrust_offset_torque | uNm |  | yes |  |  |  |
| `0x0B18` | prop.sc_mass | kg | 9 |  |  |  |  |
| `0x0B19` | prop.orbit_epoch_version |  | 9 |  |  |  |  |
| `0x0B1A` | prop.sma_km | km | 9 |  |  |  |  |
| `0x0B1B` | prop.power_draw | W |  | yes |  |  |  |

## spacecraft

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0001` | sat.mode |  |  | yes |  |  |  |
| `0x0900` | proc.progress | % |  | yes |  |  |  |
| `0x0901` | proc.current_step |  |  | yes |  |  |  |
| `0x0902` | proc.total_steps |  |  | yes |  |  |  |
| `0x0903` | proc.state |  |  | yes |  |  |  |

## sys

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0002` | sys.mission_flag |  |  | yes |  |  | yes |

## tcs

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x0320` | tcs.htr_duty_z1 | % |  | yes |  |  | yes |
| `0x0321` | tcs.htr_duty_z2 | % |  | yes |  |  | yes |
| `0x0322` | tcs.htr_duty_z3 | % |  | yes |  |  | yes |
| `0x0323` | tcs.htr_duty_z4 | % |  | yes |  |  | yes |
| `0x0324` | tcs.htr_duty_z5 | % |  | yes |  |  | yes |
| `0x0325` | tcs.htr_duty_z6 | % |  | yes |  |  | yes |
| `0x0326` | tcs.htr_duty_total | % |  | yes |  |  | yes |
| `0x0330` | tcs.setpoint_bat | C |  | yes |  |  |  |
| `0x0331` | tcs.setpoint_obc | C |  | yes |  |  |  |
| `0x0400` | tcs.temp_panel_px | C | 3 |  |  |  |  |
| `0x0401` | tcs.temp_panel_mx | C | 3 |  |  |  |  |
| `0x0402` | tcs.temp_panel_py | C | 3 |  |  |  |  |
| `0x0403` | tcs.temp_panel_my | C | 3 |  |  |  |  |
| `0x0404` | tcs.temp_panel_pz | C | 3 |  |  |  |  |
| `0x0405` | tcs.temp_panel_mz | C | 3 |  |  |  |  |
| `0x0406` | tcs.temp_obc | C | 3 |  | 5…60 / 0…70 | TCS OBC Overtemp (0.0…60.0, ALARM) |  |
| `0x0407` | tcs.temp_battery | C | 3 |  | 2…40 / 0…45 | TCS Battery Overtemp (-10.0…45.0, WARNING) |  |
| `0x0408` | tcs.temp_fpa | C | 3 |  | -22…14 / -25.5…17.5 | TCS FPA Temperature Warning (-22.0…14.0, WARNING)<br>TCS FPA Temperature Alarm (-25.5…17.5, ALARM) |  |
| `0x0409` | tcs.temp_thruster | C |  | yes |  |  |  |
| `0x040A` | tcs.htr_battery |  | 3 |  |  |  |  |
| `0x040B` | tcs.htr_obc |  | 3 |  |  |  |  |
| `0x040C` | tcs.cooler_fpa |  | 3 |  |  |  |  |
| `0x040E` | tcs.htr_duty_battery | % | 3 |  |  |  |  |
| `0x040F` | tcs.htr_duty_obc | % | 3 |  |  |  |  |
| `0x0410` | tcs.htr_duty_thruster | % |  | yes |  |  |  |
| `0x0411` | tcs.htr_total_power | W | 3 |  | 0…12.5 / 0…14 | TCS Heater Power Limit (0.0…24.5, WARNING) |  |
| `0x0412` | tcs.temp_panel_px_ilm | C |  | yes |  |  |  |
| `0x0413` | tcs.temp_panel_mx_ilm | C |  | yes |  |  |  |
| `0x0414` | tcs.temp_panel_py_ilm | C |  | yes |  |  |  |
| `0x0415` | tcs.temp_panel_my_ilm | C |  | yes |  |  |  |
| `0x0416` | tcs.temp_panel_pz_ilm | C |  | yes |  |  |  |
| `0x0417` | tcs.temp_panel_mz_ilm | C |  | yes |  |  |  |
| `0x0418` | tcs.decontamination_active |  | 3 |  |  |  |  |
| `0x0419` | tcs.thermal_gradient | C | 3 |  |  |  |  |
| `0x041A` | tcs.temp_regulation_margin | C |  | yes |  |  |  |
| `0x041B` | tcs.htr_thruster |  |  | yes |  |  |  |
| `0x0420` | tcs.temp_radiator_n | C |  | yes |  |  |  |
| `0x0421` | tcs.temp_radiator_s | C |  | yes |  |  |  |
| `0x0424` | tcs.fpa_cooler_floor_c | C |  | yes |  |  |  |
| `0x0425` | tcs.fpa_cooler_margin_k | K |  | yes |  |  |  |

## ttc

| ID | Name | Unit | HK SID | S20 | Ground limits (Y / R) | S12 | Stub |
|---|---|---|---|:-:|---|---|:-:|
| `0x040D` | ttc.pa_backup_temp | C |  | yes |  |  | yes |
| `0x0500` | ttc.mode |  | 6 |  |  |  |  |
| `0x0501` | ttc.link_status |  | 6 |  |  |  |  |
| `0x0502` | ttc.rssi | dBm | 6 |  |  | TTC RSSI Low (-95.0…0.0, WARNING) |  |
| `0x0503` | ttc.link_margin | dB | 6 |  |  | TTC Link Margin Critical (3.0…100.0, ALARM) |  |
| `0x0506` | ttc.tm_data_rate | bps | 6 |  | 16000…128000 / 900…130000 |  |  |
| `0x0507` | ttc.xpdr_temp | C | 6 |  |  |  |  |
| `0x0508` | ttc.ranging_status |  | 6 |  |  |  |  |
| `0x0509` | ttc.range_km | km | 6 |  |  |  |  |
| `0x050A` | ttc.contact_elevation | deg | 6 |  |  |  |  |
| `0x050C` | ttc.ber |  | 6 |  | -7…-5 / -8…-4 | TTC Bit Error Rate High (-5.0…0.0, WARNING) |  |
| `0x050D` | ttc.tx_fwd_power | dBm | 6 |  |  |  |  |
| `0x050F` | ttc.pa_temp | C | 6 |  | 0…55 / -10…65 | TTC PA Overtemp (-10.0…60.0, ALARM) |  |
| `0x0510` | ttc.carrier_lock |  | 6 |  |  |  |  |
| `0x0511` | ttc.bit_sync |  | 6 |  |  |  |  |
| `0x0512` | ttc.frame_sync |  | 6 |  |  |  |  |
| `0x0513` | ttc.cmd_rx_count |  | 6 |  |  |  |  |
| `0x0516` | ttc.pa_on |  | 6, 11 |  |  |  |  |
| `0x0519` | ttc.eb_n0 | dB | 6 |  |  | TTC Eb/N0 Low (4.0…100.0, WARNING) |  |
| `0x051A` | ttc.agc_level | dB | 6 |  | -80…-30 / -100…-20 |  |  |
| `0x051B` | ttc.doppler_hz | Hz | 6 |  |  |  |  |
| `0x051C` | ttc.range_rate | m/s | 6 |  |  |  |  |
| `0x051D` | ttc.cmd_auth_status |  | 6 |  |  |  |  |
| `0x051E` | ttc.total_bytes_tx | B | 6 |  |  |  |  |
| `0x051F` | ttc.total_bytes_rx | B | 6 |  |  |  |  |
| `0x0520` | ttc.antenna_deployed |  | 6 | yes |  |  |  |
| `0x0521` | ttc.beacon_mode |  | 6 |  |  |  |  |
| `0x0522` | ttc.cmd_decode_timer | s | 6 |  |  |  |  |
| `0x0523` | ttc.active_gs |  |  | yes |  |  |  |
| `0x0524` | ttc.gs_equipment_status |  |  | yes |  |  |  |
| `0x0525` | ttc.tx_active |  | 6 |  |  |  |  |
| `0x0526` | ttc.beacon_tx_on |  |  | yes |  |  |  |
| `0x0527` | ttc.beacon_active |  |  | yes |  |  |  |
| `0x0528` | ttc.beacon_link_margin | dB |  | yes |  |  |  |
| `0x0529` | ttc.beacon_dl_freq | MHz |  | yes |  |  |  |
| `0x052A` | ttc.beacon_tx_power | W |  | yes |  |  |  |
| `0x052B` | ttc.tx_gate_status |  |  | yes |  |  |  |
| `0x052C` | ttc.eirp_dbw | dBW |  | yes |  |  |  |
| `0x0535` | ttc.antenna_deployment_ready |  |  | yes |  |  |  |
| `0x0536` | ttc.antenna_deployment_sensor |  |  | yes |  |  |  |
| `0x0537` | ttc.modulation_mode |  |  | yes |  |  |  |
| `0x0539` | ttc.tm_tier |  | 6 | yes | 1…2 / -1…2.5 |  |  |
| `0x053A` | ttc.tm_tier_cmd |  | 6 | yes | 1…2 / -1…2.5 |  |  |
| `0x053B` | ttc.tm_symbol_rate | Bd | 6 | yes | 16000…64000 / 900…65000 |  |  |
| `0x053C` | ttc.downlink_mode |  | 6 | yes | 0…1 / 0…1 |  |  |

## Ground-derived pseudo-parameters (`telemetry/derived.yaml`)

Computed by the ground segment from the ephemeris and the station list. They are **not** spacecraft telemetry: no HK SID can carry them and no S20 read can return them. They sat in `parameters.yaml` — where they were permanently unreachable — until the 2026-08 build.

| Name | Unit | Description |
|---|---|---|
| contact.current_gs |  | Current ground station index (ground: orbit propagation vs ground_stations.yaml) |
| contact.time_to_aos | s | Time to acquisition of signal (ground: next contact window) |
| contact.in_contact |  | Currently in contact (ground: elevation above the station mask) |

