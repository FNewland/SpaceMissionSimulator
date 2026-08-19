# EO-Sat-1 electrical sizing, re-derived from flown 6U hardware

**Date:** 2026-08-12 · **Batch:** 2026-08b Phase 2a · **Driver:** Franz's ruling R1

> "A cubesat would generally not have a 20W transmitter and would struggle to
> generate 114W peak! Please do research into cubesat power budgets to size
> accordingly."

He is right on both counts, and the ruling reframes the whole finding: the
*documentation* was wrong, not the model. The EPS model's loss stack turns out
to be almost exactly right (see §2); what was fictional was the **area** on
the array and the **wattages** in the manual. This document records the
research, the derivation, and the numbers adopted.

---

## 1. What was wrong

| Item | Was | Problem |
|---|---|---|
| Deployable wing area | 0.180 m²/wing (0.360 m² total) | Peak generation 124 W on an 11 kg 6U. No flown 6U array is rated near this. |
| TX line, HIGH tier | manual 20 W, eps.yaml 10 W | 20 W is not a CubeSat part; and the two files disagreed. |
| TX line, LOW tier | manual 12 W, eps.yaml 5 W | Disagreed. |
| Payload imaging | manual 45 W, eps.yaml 5.0 W | Disagreed by 9x. |
| Payload standby | manual 6 W, eps.yaml 6.0 W | **Standby > imaging.** Physically impossible. |
| FPA cooler | eps.yaml 10 W; payload.yaml, tcs.yaml, textbook, procedure all 15 W | EPS under-billed the cooler by 5 W whenever it ran. |
| Array annual degradation | eps.yaml 0.00275 (0.275 %/yr); code 3.14e-6/h (1.79 %/yr); comments and tests 2.75 %/yr | Three numbers claiming to be one. |

The wattage drift happened because **nothing tested the wattage column** —
`test_eps_config_manual_consistency.py` tested areas and battery capacity, and
those stayed honest for a year. That gap is now closed
(`TestManualWattagesMatchConfig`).

---

## 2. Research: what a real 6U EO CubeSat generates

### 2.1 Deployable arrays

| Product | Published figure | Source |
|---|---|---|
| **MMA Design eHaWK** (6U+) | "starts at 72 W" | NASA *State of the Art of Small Spacecraft Technology*, ch. 3 (Power) |
| MMA Design HaWK (3U–12U) | 36 W peak; family spans "36 W to 330 W" | NASA SoA ch. 3; MMA Space product page |
| **Endurosat 6U Deployable Solar Array** | "19.2 W of power in LEO per side"; panel 209 × 342 × 8.7 mm (0.0715 m²); 655–770 g single, 1065 g double deployable; 30 %+ BOL / 29 %+ EOL TJ cells | Endurosat product page |
| DHV Technology 6U CubeSat panel (DHV-6U) | body-mounted 6U series | DHV / satsearch |
| DHV 3U body panel | 8.48 W | NASA SoA ch. 3 |
| GomSpace CubeSat body panel | 2.3–2.4 W | NASA SoA ch. 3 |

**The key cross-check.** Endurosat's 19.2 W from 0.0715 m² is **268 W/m²
delivered in LEO**. `eps.yaml`'s own loss stack computes

```
K = 1361 × 0.295 (cell) × 0.85 (packing/mismatch/coverglass)
        × [1 − 0.0020 × (65 − 28)] (hot array) × 0.97 (MPPT) × 0.95 (harness)
  = 292 W/m² BOL,  267.2 W/m² at 3-year EOL
```

267.2 vs 268. **The physics was already right; only the area was invented.**
That is what made the sizing error hard to see — every derived quantity was
internally consistent, just applied to an array nobody could build.

### 2.2 What a 6U bus actually delivers to a payload

| Bus | Figure | Source |
|---|---|---|
| **Kongsberg NanoAvionics M6P** (6U) | Available payload power, sun tracking: **17.5 W (Light) / 14.5 W (Mid) / 12.5 W (Max)**; max S/C mass 16 kg | NanoAvionics M6P product page |

This is the honest reality check: a 6U with deployables has of order
**12–18 W orbit-average left over for the payload** after the bus is fed. Our
post-fix nominal margin (+15.1 W at worst beta, +17.7 W at max-eclipse beta;
re-measured 2026-08 at the new epoch/513 km TLE-true altitude) sits
right inside that band. A design showing +40 W of payload headroom on a 6U is
not a well-designed spacecraft; it is a mis-specified one.

### 2.3 S-band radio — the 20 W transmitter

| Part | RF out | DC in | Source |
|---|---|---|---|
| **Endurosat S-Band Transceiver** | "Output Power Range: 24 to 33 dBm" (0.25–2.0 W RF) | "Idle (Rx only): 1.2 W @ 12V"; "Maximum transmit: 11 W @12V (33 dBm)" | Endurosat product page |
| Syrlinks EWC27 (X/S) | up to 100 Mbps optional | datasheet not public on the aggregator pages | SatCatalog / satsearch |

**Conclusion:** the realistic figures for a 6U are **0.5–2 W RF output** and
**5–11 W DC while transmitting**. A "20 W transmitter" conflates nothing real:
it is neither an RF output any CubeSat PA produces nor a DC draw any CubeSat
radio takes. Adopted: 5 W DC at the LOW tier (~27 dBm), 11 W DC at the HIGH
tier (33 dBm / 2 W RF), 1.2 W idle Rx.

> **Phase 3 hand-off (TT&C owner):** finding T-b says `TTC_SET_TX_POWER` is
> inert because EIRP is a constant. When you make it live, derive the EIRP
> from **these** numbers (2 W RF max) or the link budget and the power budget
> will describe two different radios.

### 2.4 Payload

| Part | Figure | Source |
|---|---|---|
| **Simera Sense TriScape100** (6U-class EO imager, 98 × 98 × 176 mm, 1.1 kg, 4.75 m GSD at 500 km) | Imaging 1100 mA @ 5.0 V = **5.50 W**; Idle 470 mA = **2.35 W**; Readout 470 mA = 2.35 W | TriScape100 datasheet (SatCatalog mirror) |

This single datasheet fixes the impossible pair: imaging costs **more** than
idling, by construction.

### 2.5 ADCS

| Part | Figure | Source |
|---|---|---|
| **AAC Clyde iADCS400** (integrated: ST200 star tracker + 3 × RW400 wheels + 3 × MTQ400) | Nominal **2.0 W**, peak **5.0 W**; 1150–1700 g | AAC Clyde Space product page |
| Sinclair Interplanetary **RW-0.03** reaction wheel | 0.3 W average; 0.03 Nms; 185 g; 3.4–6.0 V | SatCatalog |

**Not changed in this batch, and deliberately so.** The AOCS suite draw is
published by the AOCS model at 0x02C8 (COARSE 4 / NOMINAL 9 / FINE 15 W) and
the line rating lives in `simulation.yaml`, both owned by the AOCS workstream.
Recorded here as a finding for that owner: **against flight hardware, 9–15 W
for a 6U ADCS suite is 2–4× generous** (iADCS400 is 2 W nominal, 5 W peak,
for the whole suite including the star tracker). Our budget is sized against
the pessimistic value, so correcting it later can only *improve* the margin —
which is the safe direction for a sizing decision to be wrong in.

### 2.6 Battery

| Part | Figure | Source |
|---|---|---|
| **GomSpace NanoPower BPX**, 8S-1P | **100 Wh**, 3 Ah, 28.8 V nominal, 93 × 86 × 41 mm, ~500 g, 18650 Li-ion | GomSpace product page |
| Clyde Space Li-polymer | up to 150 Wh/kg | NASA SoA ch. 3 |

**Held at 100 Wh.** The 40–80 Wh range often quoted for 6U is the *typical*
single-pack; the BPX at 100 Wh in one 0.33 U package at 500 g is a real,
flown, single-pack option. No change needed.

---

## 3. Part class → flight heritage → value adopted

| Part class | Flight heritage cited | Value adopted (`eps.yaml`) |
|---|---|---|
| Deployable wing, cell area | Endurosat 6U Deployable, 2 panels of 209 × 342 mm/wing, ~84 % packing | `wing_py/my_area_m2: 0.120` each |
| Deployable wing, rating cross-check | MMA eHaWK 6U+ "starts at 72 W" | 2 × 0.120 × 292 = **70.1 W BOL at β = 0** |
| Deployable wing, mass | Endurosat double-deployable 1065 g | ~1.07 kg/wing (documented in the manual) |
| Body panels | DHV/GomSpace body panel class; 6U CDS envelope 100 × 226 × 366 | unchanged: 0.198 m² over 5 faces |
| Plane-of-array constant | Endurosat "19.2 W in LEO" ⇒ 268 W/m² | unchanged loss stack: K = 292 BOL / 267.2 EOL |
| Array degradation | TJ GaAs LEO, 2.75 %/yr | `annual_degradation: 0.0275` (was 0.00275); model now derives its per-sunlit-hour rate from it |
| Battery | GomSpace NanoPower BPX 8S-1P | `capacity_wh: 100.0` (unchanged) |
| S-band TX, LOW tier | Endurosat S-band, ~27 dBm | `transponder_power_w: 5.0` (unchanged) |
| S-band TX, HIGH tier | Endurosat S-band, 33 dBm / 2 W RF, 11 W DC | `transponder_hi_power_w: 11.0` (was 10.0) |
| S-band RX | Endurosat idle/Rx 1.2 W + ~0.3 W UHF beacon Rx | `transponder_rx_power_w: 1.5` (unchanged) |
| EO imager, imaging | Simera Sense TriScape100, 1100 mA @ 5 V | `payload_power_w: 5.5` (was 5.0) |
| EO imager, idle | Simera Sense TriScape100, 470 mA @ 5 V | `payload_standby_power_w: 2.4` (was 6.0 — **above** imaging) |
| FPA cooler | Peltier stack; agrees with payload.yaml/tcs.yaml/textbook | `fpa_cooler_power_w: 15.0` (was 10.0) |
| OBC + PDU | 6U OBC class | `obc_power_w: 6.0` (unchanged; split 3 W OBDH / 3 W EPS for reporting only) |
| ADCS suite | AAC Clyde iADCS400 2 W nom / 5 W peak; Sinclair RW-0.03 0.3 W | **not changed** — AOCS owner's file. Flagged as 2–4× generous. |

---

## 4. Array sanity check — area, mass, specific power

| Quantity | Value | Sanity |
|---|---|---|
| Cell area per wing | 0.120 m² | 2 × (209 × 342 mm) = 0.143 m² geometric at 84 % packing — a buildable Endurosat panel pair |
| Total deployed cell area | 0.438 m² (0.198 body + 0.240 wing) | Fits the 6U envelope stowed: each wing folds to 2 panels on the 226 × 366 face |
| Wing assembly mass | ~1.07 kg/wing, 2.14 kg total | 19 % of an 11 kg 6U. High, but deployable arrays with hinges and HDRM genuinely are (Endurosat quote 1065 g for the double-deployable) |
| Wing specific power | 70.1 W / 2.14 kg = **33 W/kg** | Conservative against the HaWK family's marketing (~100 W/kg for the array alone, before hinges and HDRM) |
| Peak whole-spacecraft generation | **88.6 W** (was 123.8) | Wings 70 W (= eHaWK 6U rating) + up to ~18 W of body panels, which eHaWK's rating excludes |

**Explicitly not repeated:** the 2026-07 session's 0.90 m² array on an 11 kg
6U. `test_peak_generation_is_a_cubesat_number` now fails if the deployed cell
area exceeds 0.60 m² or the wings are rated above 80 W BOL.

---

## 5. The commanding duty assumption (P-e)

`engine.py:1458-1491` forces `ttc_tx` ON for `auto_tx_hold_s` = 300 s after
**every accepted telecommand**, re-armed per command. The budget tool derived
its TX duty from pass geometry alone (33 % including the hold), which
describes an *unattended* spacecraft. In a live classroom session the
instructor and students command continuously, each accepted TC re-arms the
hold, and the TX line is effectively always on.

Modelled honestly rather than ignored: `tools/power_budget.COMMANDING_DUTY`
adds a TX-on fraction outside contact windows —

| profile | commanding duty | rationale |
|---|---|---|
| quiescent | 0 % | nobody at the console |
| nominal / nominal_imaging | 25 % | a normally-attended session |
| manoeuvre | 35 % | manoeuvre preparation is command-heavy |
| worst | 60 % | the abuse case |

Cost at the nominal profile: roughly +1.3 W orbit-average on the TX line. Not
large, but it was previously invisible, and it is a real difference between
"what the propagator says" and "what a classroom does".

---

## 6. Result — does the criterion close?

Acceptance criterion (Franz, carried forward): **orbit-average generation must
exceed orbit-average load with all nominal systems on, including ADCS**,
judged against the NOMINAL/nadir case, not the sun-pointing case.

`tools/power_budget.py --orbits 4`, EOL, worst beta (−30.6°, day 0).
(Re-measured 2026-08 after the epoch move to 2026-08-20T12:00:00Z and the
TLE-truth altitude ruling, ~513 km mean: the |beta| range over the year is now
2.8–30.6°, eclipse fraction 0.356–0.383, so the worst generation case is
milder than at the old March epoch — the margins below IMPROVED.)

| profile | gen W | load W | margin W | ratio |
|---|---|---|---|---|
| quiescent | 43.8 | 25.5 | +18.3 | 1.72 |
| **nominal** | **43.8** | **28.7** | **+15.1** | **1.53** |
| nominal_imaging | 43.8 | 32.6 | +11.2 | 1.34 |
| manoeuvre | 43.8 | 32.0 | +11.8 | 1.37 |
| worst (abuse) | 43.8 | 50.0 | −6.2 | 0.88 |

At max-eclipse beta (−7.7°, day 290, eclipse fraction 0.383) generation is
46.4 W and nominal closes at 1.61×. The tool's closure gate
(`--min-margin-ratio 1.10`) exits 0.

**The `worst` profile is deliberately negative.** It holds FINE pointing for a
whole orbit, runs the FPA cooler continuously, images through eclipse, dumps
at the HIGH tier on every pass and commands 60 % of the time. A 6U that could
sustain that would be a 6U with an impossible array. Closing the imaging case
by **duty-cycling imaging** — three 90 s captures per orbit with ~20 min of
cooler around them, which is what `nominal_imaging` models and what the
manual now documents — is the correct engineering answer, and it is the
lesson the exercise is supposed to teach.

---

## 7. Sources

- NASA, *State of the Art of Small Spacecraft Technology*, ch. 3 "Power" — https://sst-soa.arc.nasa.gov/03-power
- Endurosat, *6U CubeSat Deployable Solar Array* — https://www.endurosat.com/products/6u-deployable-solar-array/
- Endurosat, *S-Band Transceiver* — https://www.endurosat.com/products/s-band-transceiver/
- MMA Space, *Space Solar Arrays* — https://mmadesignllc.com/products/solar-arrays/
- Kongsberg NanoAvionics, *6U CubeSat Bus M6P* — https://nanoavionics.com/small-satellite-buses/6u-nanosatellite-bus-m6p/
- Simera Sense, *TriScape100* datasheet (SatCatalog mirror) — https://satcatalog.s3.amazonaws.com/components/770/SatCatalog_-_Simera_Sense_-_TriScape100_-_Datasheet.pdf
- AAC Clyde Space, *iADCS400* — https://www.aac-clyde.space/what-we-do/space-products-components/adcs/iadcs400
- SatCatalog, *Sinclair Interplanetary RW-0.03* — https://www.satcatalog.com/component/rw-003/
- GomSpace, *NanoPower BPX 100 Wh* — https://gomspace.com/product/nanopower-bpx/
- DHV Technology, *Solar panels for CubeSats* — https://dhvtechnology.com/products/solar-panels-cubesats/
