# EO-Sat-1 Link Budget and the TM Data-Rate Ladder

**Document ID:** EOSAT1-OPS-LINK-001
**Issue:** 2.0 — 2026-08-12 (issue 1.0, 2026-07-29)

> **What changed at issue 2.0.** EO-Sat-1 has two transmitters, not one.
> §9 is new and describes the UHF bootloader beacon transmitter, its link
> budget and what it does and does not gate. §4 gains a note that the S-band
> EIRP is now derived from the commanded PA power rather than being a
> constant, so `TTC_SET_TX_POWER` moves the margin table. §6.3's recovery
> sequence gains the `TTC_PA_ENABLE` step it had always been missing.

**Applies to:** `configs/eosat1/subsystems/ttc.yaml` (`tm_rate_tiers`), and everything
downstream of it: the simulator, the RF chain, the mission planner, the mission control
console, the flight procedures and the operations manual.

---

## 1. Why a spacecraft has more than one downlink rate

A student meeting a spacecraft link for the first time usually asks the wrong question:
*what is the data rate?* Real missions do not have one. They have a small, fixed,
programmable set of rates, and the interesting engineering is in **why there is more than
one and what decides which one is used**.

Three separate pressures force the set apart.

**Acquisition wants the lowest rate you can bear.** Immediately after separation nobody
knows where the spacecraft is to better than a few kilometres, the antenna may be stowed,
the attitude is uncontrolled and the transmitter may be running on whatever power the
battery has left. Every bit per second you ask for costs you `10·log₁₀(R)` decibels of
link margin, so the rate that gets you your first packet is the slowest rate that can
still carry something worth reading.

**Routine operations want the lowest rate that keeps up.** This is a *rate* requirement,
not a margin requirement, and it is arithmetic rather than judgement: the spacecraft
generates housekeeping at a certain number of bits per second, and if the downlink is
slower than that the backlog grows without bound. A rate below the generation rate is not
"degraded" — it is broken.

**Bulk downlink wants the highest rate you can justify.** Imagery is large and contact
time is scarce. But the top rate costs power, heat, occupied bandwidth and ground-station
configuration, so it is not left switched on: it is *requested*, for a planned activity,
on a pass chosen because its geometry supports it.

EO-Sat-1 therefore flies a three-rung ladder. Each rung answers one of those three
pressures, and nothing on the spacecraft, in the planner or on the console carries its own
copy of the numbers — they all read `configs/eosat1/subsystems/ttc.yaml`.

---

## 2. The ladder as shipped

| # | Tier | Information rate | Modulation | Coding | Coding gain | Symbol rate | Occupied BW | Command code |
|---|------|------------------|------------|--------|-------------|-------------|-------------|--------------|
| 0 | `SAFE` | 1 000 bps | BPSK | conv(7,½) + RS(255,223) | 7.5 dB | 1 kBd | 3.1 kHz | `0xFF` (not commandable) |
| 1 | `LOW` | 16 000 bps | BPSK | conv(7,½) + RS(255,223) | 7.5 dB | 16 kBd | 49.8 kHz | `0x00` |
| 2 | `HIGH` | 128 000 bps | QPSK | RS(255,223) | 6.0 dB | 64 kBd | 99.5 kHz | `0x01` |

The telecommand uplink is a **fixed 4 000 bps** and is deliberately *not* on the ladder
(`tc_uplink_rate_bps`). Raising the downlink never raises the uplink. This matters more
than it looks: the key used to default to the top TM rate, so a change to the downlink
would silently have changed uplink lock timing.

### 2.1 SAFE — 1 kbps, the lifeline (renamed from BEACON, 2026-08c)

`SAFE` (the rung formerly named BEACON — "beacon" now always means the UHF whip
transmitter) exists to answer one question — *is the OBC alive?* — under the worst conditions
the mission will ever see. It carries SID 11: seven parameters, a 29-byte packet, every
30 seconds. That is **7.7 bps of average load**, so 1 kbps carries it with 130× headroom
and a transmit burst of 0.23 s. Its occupied bandwidth, 3.1 kHz, is the narrowest signal
the spacecraft can produce, which is exactly what you want when the ground station is
sweeping ±40 kHz of Doppler uncertainty looking for a carrier.

**This rung is not the beacon transmitter.** It is the S-band chain running slowly, and
it dies with the S-band PA like everything else on that chain. The UHF radio that carries
SID 11 when the S-band chain is dead is a separate box — see §9, and §9.5 for the three
different things this mission calls "beacon".

It is reachable **only** from the bootloader or from beacon mode (`TTC_SET_BEACON_MODE`,
S8 func 70). `TTC_SET_DATA_RATE` will not select it. That restriction is deliberate: an
operator who could command 1 kbps in nominal operations would be commanding a rate the
spacecraft cannot live on (§2.2), and they would find out slowly, through a growing
backlog, rather than immediately, through a rejected command.

### 2.2 LOW — 16 kbps, where the spacecraft actually lives

**This is the routine operations rate.** It is also the safe-mode rate, the FDIR fallback
and the rate that works with the antenna stowed. It is not a degraded mode and it is not
a contingency; a healthy EO-Sat-1 in the middle of a nominal ops day is transmitting at
16 kbps.

16 000 bps is derived, not chosen. The shipped `telemetry/hk_structures.yaml` defines the
six periodic housekeeping structures:

| SID | Name | Parameters | Packet | Interval | Rate |
|-----|------|-----------:|-------:|---------:|-----:|
| 1 | EPS | 51 | 105 B | 1.0 s | 840.0 bps |
| 2 | AOCS | 71 | 147 B | 4.0 s | 294.0 bps |
| 3 | TCS | 17 | 49 B | 60.0 s | 6.5 bps |
| 4 | Platform | 26 | 72 B | 8.0 s | 72.0 bps |
| 5 | Payload | 23 | 64 B | 8.0 s | 64.0 bps |
| 6 | TT&C | 27 | 75 B | 8.0 s | 75.0 bps |
| | **Total** | **215** | | | **1 351.5 bps** |

Multiply by RS(255,223) (×255/223) and the 1115/1107-byte TM transfer frame and the
housekeeping stream costs **1 557 bps of channel**.

Two conclusions follow immediately, and they are the whole argument for this rung.

* **1 000 bps is below 1 557 bps.** The mission's previous "low rate" could never deliver
  real-time housekeeping. The backlog grew monotonically and packets were dropped. That
  is not a preference or a matter of taste; it is a quantified defect, and it is why
  1 kbps is now labelled as a beacon rate and locked away behind beacon mode.
* **16 000 bps clears it at 9.7 % duty** and still plays back roughly **0.85 orbits of
  stored housekeeping** in a single 514-second pass. It is the smallest round number at
  which a spacecraft in safe mode neither loses data nor falls behind across an orbit.

16 kbps is also Copernicus Sentinel-1's low S-band TM rate, and it sits between the
GomSpace AX2150's 9.6 and 19.2 kbps steps — so it is a rate a real 6U radio would offer.

### 2.3 HIGH — 128 kbps, earned per dump

`HIGH` carries delayed-telemetry playback and image dumps, and nothing else. It is gated
three ways, and every gate is enforced by **rejection**, never by silent clamping:

| Gate | Config key | Telemetry | Rejected because |
|------|-----------|-----------|------------------|
| Antenna deployed | `requires_antenna: true` | `ttc.antenna_deployed` (0x0520) | A stowed patch has the wrong pattern for a sustained high-rate dump |
| DOWNLINK mode on | `requires_downlink_mode: true` | `ttc.downlink_mode` (0x053C) | Bulk downlink must be a deliberate, planned activity — see §5 |
| Elevation ≥ 10° — **GROUND policy, not on-board** | `min_elevation_deg: 10.0` (read by planner + MCS only) | ground contact geometry (0x050A shown for reference) | Tracking rate, Doppler rate and multipath make a low-elevation 64 kBd QPSK dump a poor bet — enforced by planning and an MCS arm-through hold |

A command that fails a gate returns an S1.8 execution failure with error `0x0007` and a
message naming the gate, e.g. *"HIGH (128000 bps) requires DOWNLINK/dump mode"*.
An unknown tier code returns `0x0003`. Nothing is quietly substituted. A clamp would teach
an operator that the spacecraft ignores them; a rejection with a reason teaches them the
rule.

128 000 bps is Sentinel-1's *nominal* S-band TM rate and the top of the commodity CubeSat
TT&C-radio class (EnduroSat 125 kbps, GomSpace AX2150 96 kbps), so the entire ladder is
reachable with one TT&C radio. No separate high-rate transmitter is implied — which is
the right hardware story for a 6U.

### 2.4 Why the steps are 16× and 8×

1 kbps × 2⁰, 2⁴, 2⁷. The steps are **+12.04 dB** and **+9.03 dB**, which is exactly the
`−10·log₁₀(R)` term in the link budget. A student can read the ladder straight off the
budget: *a tier step costs you 9 to 12 dB of margin, and that is the only thing rate does
to a link.*

---

## 3. Modulation and coding: two independent levers

The modulation and the coding change with the tier, and the reasons are different from the
reason the rate changes. It is worth separating them explicitly, because conflating rate,
modulation and bandwidth is the single most common misunderstanding in a first link-budget
course.

**Rate sets the noise bandwidth.** `Eb/N₀ = C/N₀ − 10·log₁₀(R_b)`. Doubling the
information rate costs 3 dB, full stop, independent of how you modulate it.

**Coding sets how much Eb/N₀ you need.** Uncoded BPSK or QPSK needs 9.6 dB at BER 1e-5.
RS(255,223) buys about 6.0 dB; concatenating it with a rate-½ K=7 convolutional code buys
about 7.5 dB. The two lower rungs use conv+RS because they are the rungs that have to work
when everything else has gone wrong, and because at 1–16 kbps the Viterbi decode is cheap.
The top rung uses RS alone: it does not need the extra 1.5 dB (§4), and the convolutional
code would double its symbol rate and therefore its occupied bandwidth.

**Modulation sets the symbol rate, and therefore the bandwidth — not the bit rate.** This
is the point students most often get backwards. QPSK carries two bits per symbol, so
128 kbps of *information* is 64 kBd of *symbols*. It does not make the link faster; it
makes the same information fit in half the spectrum:

| Tier | Bits/symbol | Symbol rate | Occupied BW (SRRC α=0.35) | % of the 5 MHz mission channel |
|------|------------:|------------:|--------------------------:|-------------------------------:|
| SAFE | 1 (BPSK) | 1 kBd | 3.1 kHz | 0.1 % |
| LOW | 1 (BPSK) | 16 kBd | 49.8 kHz | 1.0 % |
| HIGH | 2 (QPSK) | 64 kBd | 99.5 kHz | 2.0 % |

QPSK at the top rung is a deliberate teaching choice as well as an engineering one. The
link is not power-limited (§4) and 5 MHz of channel is not scarce, so neither lever is
forced; QPSK was chosen because the tier step becomes *visible on the radio* — the
constellation, the eye diagram and the spectrum all change when you command it, and a
student can see for themselves that rate and modulation are independent.

The counter-argument is real and worth telling students: flight TT&C radios at this rate
(AX2150, EnduroSat) use constant-envelope GMSK or GFSK, and a saturated PA would prefer
OQPSK. If that fidelity matters more than the visible constellation change, set
`modulation: OQPSK` on the HIGH tier in `ttc.yaml` — nothing else has to change.

---

## 4. The margin table

Generated by `python tools/link_budget_ladder.py`, which drives
`TTCBasicModel.link_margin_for` — the same model object the simulator flies — against the
shipped configuration. The tool and the flight model cannot drift apart.

**Assumptions** (all from `configs/eosat1/subsystems/ttc.yaml`): 450 km orbit, 2200 MHz
downlink, EIRP 10 dBW **at the NOMINAL 2 W PA setting** + 3 dBi spacecraft patch = 13 dBW
(see §4.3 — the EIRP is no longer a constant), atmospheric 0.5 dB at zenith
scaled by cosec(ε) below 30°, polarisation 0.3 dB, implementation 2.0 dB, G/T de-rated
−1.5 dB below 10° and −0.5 dB below 30°, required Eb/N₀ 9.6 dB uncoded less the tier's
coding gain.

**Geometry.** Slant range `d(ε) = R⊕·[√((r/R⊕)² − cos²ε) − sin ε]`:

| Elevation | Slant range | FSPL at 2200 MHz |
|-----------|------------:|-----------------:|
| 5° | 1 944.5 km | 165.07 dB |
| 10° | 1 570.0 km | 163.22 dB |
| 30° | 825.0 km | 157.63 dB |
| 90° | 450.0 km | 152.36 dB |

**Link margin (dB) at 5° elevation — the worst geometry any station sees:**

| Station | G/T (dB/K) | SAFE | LOW | HIGH |
|---------|-----------:|-------:|----:|-----:|
| Inuvik | 22.0 | 59.8 | 47.7 | 37.2 |
| Svalbard | 21.5 | 59.3 | 47.2 | 36.7 |
| Iqaluit | 20.0 | 57.8 | 45.7 | 35.2 |
| O'Higgins | 20.0 | 57.8 | 45.7 | 35.2 |
| Troll | 18.5 | 56.3 | 44.2 | **33.7** |

**Link margin (dB) at 10° elevation — where the HIGH tier's gate opens:**

| Station | SAFE | LOW | HIGH |
|---------|-------:|----:|-----:|
| Inuvik | 64.0 | 52.0 | 41.5 |
| Svalbard | 63.5 | 51.5 | 41.0 |
| Iqaluit | 62.0 | 50.0 | 39.5 |
| O'Higgins | 62.0 | 50.0 | 39.5 |
| Troll | 60.5 | 48.5 | 38.0 |

**Link margin (dB) at zenith:**

| Station | SAFE | LOW | HIGH |
|---------|-------:|----:|-----:|
| Inuvik | 76.3 | 64.3 | 53.8 |
| Svalbard | 75.8 | 63.8 | 53.3 |
| Iqaluit | 74.3 | 62.3 | 51.8 |
| O'Higgins | 74.3 | 62.3 | 51.8 |
| Troll | 72.8 | 60.8 | 50.3 |

### 4.1 Reading the table

**Every rung closes everywhere, with tens of decibels to spare.** The worst case in the
whole matrix is HIGH at Troll at 5°, and it still has 33.7 dB. At 450 km to a
professional polar dish, a 2 W S-band link is not power-limited. Anyone who has been
taught that a data rate is chosen by finding where the margin runs out should stop and
look at this table, because it is the more common real-world situation and it teaches a
harder lesson: **the rate is chosen by policy, bandwidth, power, thermal cost and pass
planning, not by a power cliff.**

That is why the HIGH tier is gated on mode on board, with the 10° elevation rule
enforced on the GROUND (planner + MCS hold) rather than by a margin threshold.
Modelling a fake cliff at 10° would have been easier to explain and less honest.

**The rungs are separated by exactly what the budget predicts.** Read any row:

| Step | Rate ratio | Rate term | Coding change | Predicted | Measured (Iqaluit @5°) |
|------|-----------:|----------:|---------------|----------:|----------------------:|
| SAFE → LOW | 16× | −12.04 dB | none | −12.04 dB | 57.8 − 45.7 = **−12.1 dB** |
| LOW → HIGH | 8× | −9.03 dB | conv+RS 7.5 → RS 6.0 dB | −10.53 dB | 45.7 − 35.2 = **−10.5 dB** |

If a measured set does not show those two steps, the fault is in the model, not in the
link. That check is what COM-006 Step 2a exists to perform.

**Elevation is worth more than you might guess.** Going from 5° to 10° buys about 4.3 dB
— 1.85 dB of free-space path loss, 1.43 dB of atmosphere and 1.0 dB of the G/T de-rate
coming off. Going to zenith buys 16.6 dB. This is why an operator waits for a pass rather
than waiting for a better rate.

### 4.2 The margin table is drawn at one PA setting

Every number in §4 is the **NOMINAL** operating point, 2.0 W RF. Since 2026-08b the
EIRP is derived from the commanded PA power rather than being a fixed config constant:

```
EIRP(P) = eirp_dbw + 10·log₁₀(P / pa_ref_power_w)      pa_ref_power_w = 2.0 W
```

| `TTC_SET_TX_POWER` level | RF out | ΔEIRP | LOW rung at Iqaluit, 5° |
|---|---:|---:|---:|
| 0 `LOW` | 1.0 W | −3.01 dB | 42.7 dB |
| 1 `NOMINAL` | 2.0 W | 0.00 dB | **45.7 dB** |
| 2 `HIGH` | 5.0 W | +3.98 dB | 49.7 dB |

Subtract 3.01 dB from any figure in §4 to read it at the LOW PA setting, or add
3.98 dB to read it at HIGH.

**Why this is worth a paragraph.** Until this change `_eirp` was a constant that nothing
derived from the PA power, so `TTC_SET_TX_POWER` moved telemetry `ttc.tx_fwd_power`
(0x050D) and nothing else — the margin, the Eb/N₀, the BER and the whole RF pipeline
were bit-identical at 1 W and 5 W. FDIR's `ttc_reduce_power` level-1 recovery, whose
entire purpose is to trade rate and power for link stability, was therefore a no-op on
the link. `ttc.eirp_dbw` (0x052C) now reports the live figure so the effect is visible
from the ground.

Two absolute-level caveats are recorded in `ttc.yaml` rather than silently carried:
10 dBW at 2 W RF implies 7 dB of gain that a 6U does not have (the honest figure is
6 dBW total EIRP, i.e. every margin here is ~7 dB optimistic), and a 5 W RF "HIGH" step
out of an 11 W DC line is 45 % PAE, which no S-band SSPA reaches. Both are two-file
changes that touch `rfsim.yaml` and the generated command reference, and both are
raised in `docs/plan_2026-08b/DISPLAY_REQUESTS_TTC.md`. Neither changes any conclusion
below: the worst cell in the matrix would still close by 26 dB.

### 4.3 The ceiling is regulation, not physics

The 2200–2290 MHz allocation caps a mission at **5.00 MHz occupied bandwidth**. With
SRRC α = 0.35 that is 3.70 MBd, which through RS(255,223) and the transfer frame is about
**3.2 Mbps** at QPSK with convolutional coding, or 6.4 Mbps with RS alone. The link itself
would carry several Mbps at 3 dB margin even with a realistic patch pattern. So the
regulation bites first, at every geometry — and **no X-band is needed for this mission**.
X-band would only be justified if the payload volume grew by roughly two orders of
magnitude.

---

## 5. What the ladder does to mission planning

This is where the ladder stops being an RF topic and becomes an operations topic. The
mission planner (`packages/smo-planner`) reads two rungs from `tm_rate_tiers` — the
`default_tier` (LOW) for routine traffic and the top rung (HIGH) for `data_dump`
activities — and it never uses a single flat rate. Three consequences follow.

### 5.1 One image is most of a pass

A 4 MiB raw frame at HIGH:

| | At HIGH (128 kbps) | At LOW (16 kbps) |
|---|---|---|
| Raw channel time | **262.1 s** | 2 097 s |
| With the planner's 20 % protocol allowance | **327.7 s** | 2 621 s |
| As a fraction of the 445 s average pass | **74 %** | 589 % |

So the `data_dump` activity in `planning/activity_types.yaml` is **330 seconds long** —
one image, rounded up, and nothing more. `n` images need `n` dumps, and the arithmetic of
a plan is legible on its face. The old 600 s duration was sized for a retired flat rate;
at 128 kbps it would have booked 1.8 images of capacity with only one image to put in it.

At LOW the same image needs more than a whole pass. **That is the trade the ladder makes
visible: imagery is not merely slower at LOW, it is impossible.**

### 5.2 Housekeeping alone is a third of the low-rate network

The 1 557 bps housekeeping stream is 16.0 MiB per day. The five-station network provides
about 23 300 seconds of contact per day. Carrying a day of housekeeping therefore
consumes:

| Tier | Contact time needed per day | Share of the network |
|------|----------------------------:|---------------------:|
| LOW (16 kbps) | 8 410 s | **36 %** |
| HIGH (128 kbps) | 1 051 s | **4.5 %** |

Thirty-six percent of the entire ground network, before a single image. The budget reports
this as `hk_contact_fraction_percent`, per tier, computed from `hk_structures.yaml` rather
than assumed. It is the number that makes the case for LOW being 16 kbps and not 8, and
the number that makes a student stop thinking of housekeeping as free.

### 5.3 A pass spent dumping is a pass not spent otherwise

The planner's data budget now reports per pass:

* `high_fraction` — what proportion of the window sits above the 10° gate, derived from
  spherical geometry (`fraction_above_elevation`), not from a lookup table. A 30° pass
  spends **76 %** of its window above 10°; a 12° pass only **47 %**; a 7° pass **none**,
  and can never dump at HIGH at all.
* `dump_capable` / `dump_tier` — whether the pass can hold HIGH, and which tier a dump
  booked on it would actually run at.
* `capacity_mb` — the blended capacity: HIGH for the arc above the gate, LOW for the
  wings, minus the station's setup and teardown time.
* `dump_contact_fraction_percent` — the share of the day's usable contact seconds the plan
  has already committed to dumps.

Booking a dump on a pass that never clears 10° raises a `DUMP_BELOW_ELEVATION_GATE`
warning, because the spacecraft will refuse the tier and the pass will move an eighth of
the planned volume. The shipped `nominal_ops_day` template avoids this structurally: its
`data_dump` entries carry `min_pass_elevation_deg: 10.0`, so the template only offers
passes the spacecraft will let run at HIGH — 43 of the 52 windows in a typical day — and
anchors each 330 s dump on closest approach, where the whole activity sits above the gate.

On the shipped contact statistics that plan generates 8.6 MB and books eight dumps whose
passes carry 44 MB, closing with about **80 % margin** — and it closes *because* it books
HIGH. At LOW the same eight passes would carry 5.5 MB against 8.6 MB generated and the
plan would not close.

Daily network capacity, for orientation:

| Scenario | Capacity |
|----------|---------:|
| Every usable contact second at HIGH | ~227 MiB/day |
| Every usable contact second at LOW | ~28 MiB/day |
| Realistic blend (HIGH above the gate, LOW below it) | **~162 MiB/day** |

Against roughly 8–34 MB/day of generation, the mission has margin — but it has that margin
only because dumps are booked onto the right passes.

---

## 6. Operating the ladder

### 6.1 Commands

| Command | Service | Subtype | Func | Argument |
|---------|--------:|--------:|-----:|----------|
| `TTC_SET_DATA_RATE` | 8 | 1 | **65** | one `uint8` tier code: `0x00` = LOW, `0x01` = HIGH, `0xFF` = SAFE (not accepted in nominal software) |
| `TTC_SET_DOWNLINK_MODE` | 8 | 1 | **79** | one `uint8`: 1 = enter DOWNLINK/dump mode, 0 = leave |
| `TTC_SET_BEACON_MODE` | 8 | 1 | 70 | one `uint8`: 1 = force the SAFE (1 kbps) rung |

**The legacy encoding is preserved verbatim.** `rate=0` has always meant "the low rate"
and `rate=1` "the high rate". Every printed procedure, every OBCP and the FDIR
`ttc_reduce_power` action therefore keep working unchanged — they simply land on usable
operational rates now.

A dump is always the same four steps: enter DOWNLINK mode (79), raise the rate (65,
rate=1), dump, then release both. `planning/activity_types.yaml`'s `data_dump` command
sequence does exactly that, and NOM-011 / NOM-003 walk an operator through it.

### 6.2 Telemetry, and the active-vs-commanded distinction

| Parameter | ID | Meaning |
|-----------|----|---------|
| `ttc.tx_active` | 0x0525 | **Is the S-band chain radiating at all?** The gate every TM packet passes. 0 with the rate ladder still reporting a tier means the tier is what you *would* transmit at if you were transmitting |
| `ttc.tx_gate_status` | 0x052B | Why 0x0525 is what it is, bit by bit |
| `ttc.eirp_dbw` | 0x052C | Live EIRP at the commanded PA power (§4.2) |
| `ttc.beacon_active` | 0x0527 | The **UHF beacon transmitter**, which is a different radio (§9) |
| `ttc.tm_data_rate` | 0x0506 | Active information rate (1000 / 16000 / 128000) |
| `ttc.tm_tier` | 0x0539 | **ACTIVE** tier index — what is on air now |
| `ttc.tm_tier_cmd` | 0x053A | **COMMANDED** tier index — what the ground asked for |
| `ttc.tm_symbol_rate` | 0x053B | Symbol rate on air (16 kBd at LOW, 64 kBd at HIGH) |
| `ttc.downlink_mode` | 0x053C | DOWNLINK/dump mode, the gate on HIGH |

The gates act on the **active** tier and never overwrite the **commanded** one. So as a
pass descends through 10°, `tm_tier` drops from HIGH to LOW while `tm_tier_cmd` stays at
HIGH, and on the next pass above 10° the link comes back to HIGH without anyone
re-commanding it.

The console shows both, side by side, with the reason when they differ:

```
TM Rate (active)      LOW (16 kbps, BPSK, conv+RS)
TM Rate (commanded)   HIGH (128 kbps, QPSK, RS(255,223))
Tier gate             HIGH needs elevation >= 10 deg (now 6.4 deg)
```

That difference is a diagnosis, not noise. A console that showed only the active rate
would turn "the gate refused the tier you asked for" into "why is the dump slow?".

### 6.3 How to change rate, and why a change can be refused

A tier whose gates are not satisfied is **refused, never silently clamped**: the
spacecraft answers `TTC_SET_DATA_RATE` with an S1.8 execution failure. Since defect #87
that report carries the on-board software's own words in its failure notice, and the
same text appears as S5 event `0x030B COMMAND_REJECTED` in the event log and the FDIR
journal. If you get a `1,8`, **read the reason** — the numeric code is not enough, as
four different gates all report `0x0007`.

**Every gate on `TTC_SET_DATA_RATE` (S8.1 func 65):**

| # | Gate | Applies to | Code | What the failure notice says |
|---|------|-----------|-----:|------------------------------|
| 1 | Tier code must be a real tier | any | `0x0003` | `Invalid TM rate/tier <x>. Valid: 0=LOW(16000 bps), 1=HIGH(128000 bps), …` |
| 2 | **Beacon mode forces the beacon tier** | LOW, HIGH | `0x0007` | `TM rate is forced to the beacon tier in beacon mode (LOW refused)` |
| 3 | SAFE is bootloader-only | SAFE (`0xFF`) | `0x0007` | `SAFE is a bootloader-only tier (1000 bps); not selectable in nominal operations` |
| 4 | Antenna must be deployed | LOW, HIGH | `0x0007` | `HIGH (128000 bps) requires a deployed antenna` |
| 5 | DOWNLINK/dump mode must be on | HIGH | `0x0007` | `HIGH (128000 bps) requires DOWNLINK/dump mode (TTC_SET_DOWNLINK_MODE)` |
| — | TT&C TX power line must be on | any | `0x0004` | rejected earlier, at *acceptance*: `S8 func_id 65: power line 'ttc_tx' is OFF` |

Gates 1–5 are `ttc_basic._tier_permission_error`; the power-line gate is the engine's
central acceptance-time check. Gates 4–5 apply to the ACTIVE tier every tick as well, so
HIGH auto-reverts and resumes without re-commanding (§6.2) — but the *commanded* change
above is refused outright.

**Gate 6 (elevation ≥ 10°) no longer exists ON BOARD** (2026-08c): a spacecraft
cannot know its elevation as seen from a station, so the 10° HIGH rule moved to the
GROUND — `subsystems/ttc.yaml tm_rate_tiers[HIGH].min_elevation_deg` is read by the
mission planner (which schedules HIGH dumps above it) and by the MCS commanding page
(which raises an arm-through hold when HIGH is commanded below it, from the ground's
own contact geometry). The refusal text
`HIGH ... requires elevation >= 10 deg` can therefore no longer be produced by the
spacecraft; the physics still degrades low-elevation links honestly through the
cosec atmospheric loss and the G/T de-rate.

**The exact command sequence.**

*Select LOW (routine operations) after a boot or a recovery:*

```
1. S8.1 func 19  EPS_POWER_LINE_ON, line 'ttc_tx'   (if not already on)
2. S8.1 func 66  TTC_PA_ENABLE                      ← the step that was missing
3. S8.1 func 70  TTC_SET_BEACON_MODE, on = 0        ← clears gate 2
4. S8.1 func 69  TTC_DEPLOY_ANTENNA                 (once, LEOP; gate 4)
5. S8.1 func 65  TTC_SET_DATA_RATE, rate = 0x00     → LOW, 16 kbps
```

**Step 2 is new, and its absence used to be invisible.** The bootloader leaves the
spacecraft with `pa_on = False` (`engine.py:631`) and nothing performed the reverse, so
*every* recovery ran with the power amplifier commanded off. That produced no symptom at
all while nothing in the TM chain read the PA state — telemetry flowed anyway. Two
changes close it:

* **Booting the application lifts the S-band inhibit.** The TT&C model watches
  `obdh.sw_image` (0x0311) and re-enables the PA on the bootloader → application edge,
  which is the missing half of `_enter_bootloader_mode`'s inhibit. A spacecraft that
  reboots into its application therefore comes back with a transmitter, which is the
  only survivable default: the alternative is a watchdog reset that requires a blind
  command to recover from.
* **Step 2 is still written down, and still worth sending.** It is idempotent after a
  clean boot, and it is the step that recovers the link after an operator or an FDIR
  card has switched the PA off — `ttc_emergency_shutdown` does exactly that.

If the PA *is* off, the symptom is now unmistakable: no S-band telemetry, only the UHF
beacon's SID 11 every 30 s, with `ttc.pa_on` inside that beacon reading 0.

Leaving beacon mode (step 3) also resets the **commanded** tier to LOW. Before 2026-08b
it reset only the flag, so the console reported commanded = SAFE / active = LOW
indefinitely and the tier-demotion display could never clear.

*Select HIGH for a dump, then give it back:*

```
1. …LOW selectable as above (elevation ≥ 10 deg is a GROUND policy hold in the
   MCS, not an on-board gate)
2. S8.1 func 79  TTC_SET_DOWNLINK_MODE, on = 1        ← clears gate 5
3. S8.1 func 65  TTC_SET_DATA_RATE, rate = 0x01       → HIGH, 128 kbps
4. …dump (S15.9 / S13)…
5. S8.1 func 65  TTC_SET_DATA_RATE, rate = 0x00       → back to LOW
6. S8.1 func 79  TTC_SET_DOWNLINK_MODE, on = 0
```

**The one that catches people.** Booting the OBC application (`S8.1 func 55`,
`OBC_BOOT_APP`) does **not** clear beacon mode. A spacecraft that came up in the
bootloader, or that was put into beacon mode by a contingency, is still in beacon mode
after the application is running — and gate 2 refuses *both* LOW and HIGH until
`TTC_SET_BEACON_MODE off` is sent. Whether the application boot should clear beacon mode
automatically is an open design question; today it does not, deliberately, so step 2
above is mandatory. LEOP-002 and CON-BOOT walk through it.

---

## 7. Changing the numbers

Everything above is configuration. `configs/eosat1/subsystems/ttc.yaml` is the only place
a rate is defined; the simulator, the RF pipeline, the planner, the console, the
procedures test and the link-budget tool all read it.

**To change a rate**, edit the rung's `bps`:

```yaml
tm_rate_tiers:
  - name: HIGH
    bps: 256000            # was 128000
    command_code: 0x01
    modulation: QPSK
    coding: RS
    coding_gain_db: 6.0
    requires_antenna: true
    requires_downlink_mode: true
    min_elevation_deg: 10.0
```

Then run, in order:

1. `python tools/link_budget_ladder.py` — check every rung still closes and the steps are
   what the budget predicts. It exits non-zero if a rung fails to close or overruns the
   5 MHz channel.
2. `python -m pytest tests/test_content/test_data_rate_content.py` — this reads the ladder
   and tells you **which documents you still have to change**. It is the tripwire for
   exactly the kind of drift this change was made to clean up.
3. `python -m pytest tests/test_planner tests/test_mcs tests/test_simulator` — the planner
   re-derives the `data_dump` duration check, the daily capacity and the elevation-gate
   arithmetic from config, so a rate change that breaks the shipped plan fails here.

**To change a gate**, edit the tier's `requires_antenna`, `requires_downlink_mode` or
`min_elevation_deg`. The planner reads `min_elevation_deg` as its
`min_dump_elevation_deg` default, and `tests/test_content/test_data_rate_content.py`
asserts that every station in `planning/planner.yaml` uses the same value — so the ground
segment cannot plan against a gate the spacecraft does not enforce.

**To change which rung is the default**, edit `default_tier` (routine operations),
`safe_tier` (safe mode and FDIR fallback) or `beacon_tier` (bootloader). These are tier
*names*; a name that is not in `tm_rate_tiers` fails config validation at load.

**To add a rung**, append it to `tm_rate_tiers`. The list must be strictly ascending in
`bps` and its names must be unique — both are validated. Give it a `command_code` that no
other rung uses. Nothing else needs editing: the console builds its labels from the list,
the planner takes the top rung as the dump rate and `default_tier` as the routine rate,
and `tools/link_budget_ladder.py` prints a column for it.

**What you should not do** is add a second copy of a rate anywhere. The deprecated
`tm_rate_lo_bps` / `tm_rate_hi_bps` keys still exist in `ttc.yaml` for out-of-lane readers
and are derived from the ladder when omitted; they are aliases for the safe tier and the
top tier, and no new code should read them.

---

## 8. Further reading

* `configs/eosat1/subsystems/ttc.yaml` — the ladder itself, with the reasoning in comments
* `configs/eosat1/manual/05_ttc.md` §2.4 — the operator's view
* `configs/eosat1/procedures/nominal/data_rate_change.md` (NOM-011) — changing tier during
  a pass
* `configs/eosat1/procedures/commissioning/ttc_link_verification.md` (COM-006) — measuring
  the baselines
* `docs/PUS_SERVICE_REFERENCE.md` — func 65 and func 79 in full
* `docs/sim_fidelity/ttc_fidelity.md` §6 — the rate state machine as implemented
* `tools/link_budget_ladder.py` — regenerate every number in §4

---

## 9. The other transmitter — the UHF bootloader beacon

Everything above §8 describes **one** radio: the S-band prime chain. EO-Sat-1 carries a
second one, and it is the more interesting of the two to a student, because it closes a
completely different budget with completely different hardware to do a job the prime
chain structurally cannot.

### 9.1 Why a second radio at all

Read §2 and §5 again and notice what they have in common: every rung of the ladder, every
gate, every dump and every housekeeping packet goes through the same S-band PA, the same
`ttc_tx` power line, the same transponder and the same deployable patch. Those are four
single points of failure, in series, on the only thing that can tell you the spacecraft
is alive.

A beacon transmitter shares none of them. It has its own PA, its own band, its own
antenna and its own power feed, and it is the box that answers *is the OBC alive?* when
the answer to *is the S-band chain alive?* is no. That is why the textbook has specified
"S-band prime (body-fixed patch) + UHF whip bootloader beacon" since the first issue, and
why `UPGRADE_ROADMAP.md` scope (d) asked for the UHF numbers to be kept as the
bootloader-beacon link.

Until 2026-08b it was not implemented. The rung now named `SAFE` was rung 0 of the S-band ladder — the
same PA, the same antenna, 1 kbps instead of 16 — so switching the S-band PA off silenced
the lifeline along with everything else. Franz's ruling R2: build the second radio.

### 9.2 What it is

| | S-band prime chain | UHF beacon transmitter |
|---|---|---|
| Downlink | 2200 MHz | **437.5 MHz** (435–438 MHz amateur-satellite segment) |
| RF output | 1 / 2 / 5 W, commandable | **1.0 W** (30 dBm), fixed |
| Spacecraft antenna | 3.0 dBi deployable patch | **0 dBi** deployable tape-spring dipole |
| Ground station | 9 m polar dish, G/T +20.0 dB/K | **~16 dBic crossed Yagi, G/T −8.0 dB/K** |
| Rates | 1 / 16 / 128 kbps ladder | **1 kbps**, one rate |
| Carries | everything | **HK SID 11, and nothing else** |
| Power | `ttc_tx`, switchable, 5–11 W DC | `ttc_rx`, **non-switchable**, 1.8 W DC while keying |
| Switched by | `TTC_PA_ON` / `TTC_PA_OFF` (66/67) | `TTC_BEACON_TX_ON` / `_OFF` (**97/98**) |
| Telemetry | `ttc.tx_active` 0x0525 | `ttc.beacon_active` 0x0527 |

Hardware the numbers come from: **EnduroSat UHF Type II Transceiver** (435–438 MHz,
output up to 33 dBm, typical 30 dBm), **GomSpace NanoCom AX100** (430–440 MHz, up to
30 dBm), **EnduroSat UHF Antenna Type II** (deployable tape-spring dipole, ~0 dBi,
linear), and NASA's *State of the Art of Small Spacecraft Technology* ch. 9, which
records UHF as the standard CubeSat beacon band. 437.5 MHz is where IARU-coordinated
CubeSat beacons actually sit.

### 9.3 The budget, and the contrast that is the whole lesson

450 km orbit, slant range from the same geometry table as §4.

| Elevation | Slant range | UHF FSPL @437.5 MHz | Eb/N₀ | **Beacon margin** | S-band LOW margin | S-band SAFE rung |
|---|---:|---:|---:|---:|---:|---:|
| 5° | 1 943.7 km | 151.04 dB | 31.9 dB | **29.8 dB** | 45.7 dB | 57.8 dB |
| 10° | 1 569.6 km | 149.19 dB | 35.3 dB | **33.2 dB** | 50.0 dB | 62.1 dB |
| 30° | 825.0 km | 143.60 dB | 41.8 dB | **39.7 dB** | 57.0 dB | 69.1 dB |
| 90° | 450.0 km | 138.33 dB | 47.1 dB | **45.0 dB** | 62.3 dB | 74.3 dB |

Worked, at the worst geometry (5°):

```
EIRP           1.0 W = 0.00 dBW  +  0.0 dBi dipole      =    0.00 dBW
FSPL           20log₁₀(1943.7 km) + 20log₁₀(437.5 MHz) − 147.55 = 151.04 dB
losses         atmosphere 1.15 (0.2 dB zenith × cosec 5°)
               + polarisation 3.0 + implementation 2.0  =    6.15 dB
G/T            −8.0 dB/K, de-rated 1.5 dB below 10°     =   −9.50 dB/K
C/N₀           0.00 − 151.04 − 6.15 − 9.50 + 228.6      =   61.91 dBHz
Eb/N₀          61.91 − 10log₁₀(1000)                    =   31.91 dB
required       9.6 uncoded − 7.5 conv(7,½)+RS           =    2.10 dB
MARGIN                                                       29.81 dB
```

Now put that next to the S-band `SAFE` rung at the same geometry and the same
information rate — 57.8 dB — and account for the 28.0 dB difference. It is **not** one
effect:

| Term | UHF vs S-band | Why |
|---|---:|---|
| Free-space path loss | **+14.03 dB to UHF** | 20·log₁₀(2200/437.5); five times the wavelength |
| Atmosphere | +0.69 dB to UHF | 0.2 dB vs 0.5 dB at zenith; UHF is far below the water line |
| Spacecraft antenna | −3.0 dB to UHF | 0 dBi omni dipole vs a 3 dBi patch |
| Polarisation | −2.7 dB to UHF | a linear dipole on a possibly-tumbling spacecraft against a circular ground antenna is a 3 dB nominal mismatch, not 0.3 dB |
| Ground-station G/T | **−28.0 dB to UHF** | a 9 m professional dish is 28 dB better than a university Yagi |
| **Net** | **−28.0 dB** | |

Two lessons a student should take from that table. First, **the frequency choice is not
the dominant term** — UHF wins 14 dB of path loss and then hands back twice that in
ground-station gain, because the aperture you can build scales with wavelength on both
ends and the ground end is where the money is. Second, **the beacon closes anyway, by
30 dB**, because it asks for 1 kbps instead of 16, and it asks a station you can build in
a university car park. It is a deliberately modest link doing a deliberately modest job,
and that is what makes it survivable.

### 9.4 What it gates, and what it does not

**Only HK SID 11.** When the S-band chain is transmitting (`ttc.tx_active` = 1) the OBC's
own housekeeping scheduler sends SID 11 along with everything else and the beacon stays
quiet — no duplication. When the S-band chain is *not* transmitting, the beacon emits
SID 11 on its own 30 s cadence and nothing else follows it down. Real-time HK, playback,
image dumps, S1 verification and on-demand reads all stay on the S-band chain and all die
with the PA.

SID 11 carries seven parameters: battery voltage and SoC, OBC mode, reboot count, active
software image, active OBC — and, since 2026-08b, **`ttc.pa_on`**. That last byte is the
point of the whole exercise. When Franz's original symptom happens in reverse — no
telemetry at a pass — the one packet you still get tells you whether the S-band PA is off,
which turns a silent spacecraft into a one-command fix (`TTC_PA_ENABLE`, §6.3 step 2).

At 1 kbps a 29-byte SID 11 packet takes 0.23 s, so the beacon's transmit duty cycle is
**0.75 %** and its orbit-average draw is **13 mW**. `eps.yaml` already books it:
`transponder_rx_power_w` = 1.5 W is documented in `POWER_SIZING.md` §3 as "EnduroSat
idle/Rx 1.2 W + ~0.3 W UHF beacon Rx", which covers the beacon's standby and its keying
with two decades to spare. **No EPS change is required** — the arithmetic is in
`DISPLAY_REQUESTS_TTC.md`.

### 9.5 Three things called "beacon", and how to keep them apart

This is the trap, and it is the reason the beacon transmitter went unbuilt for a year.

| Name | What it is | Telemetry | Changed by |
|---|---|---|---|
| **`SAFE` tier** (ex-BEACON) | rung 0 of the **S-band** TM rate ladder — 1 kbps, BPSK, conv+RS | `ttc.tm_tier` 0x0539 = 0 | entering beacon mode or the bootloader |
| **`beacon_mode`** | a latched flag that pins the S-band ladder to that rung | `ttc.beacon_mode` 0x0521 | `TTC_SET_BEACON_MODE`, S8 func **70** |
| **beacon transmitter** | a **second radio** at 437.5 MHz carrying SID 11 | `ttc.beacon_tx_on` 0x0526, `ttc.beacon_active` 0x0527 | `TTC_BEACON_TX_ON`/`_OFF`, S8 funcs **97/98** |

The first two are one concept (a rate) wearing two names; the third is hardware. None
implies another: you can be in beacon mode with the UHF transmitter off, or at the HIGH
tier with it on, and the normal state of the spacecraft is HIGH-capable S-band *plus* a
beacon quietly available underneath.

**In the bootloader all three line up, which is exactly why they got conflated.** The
bootloader sets `pa_on = False`, `antenna_deployed = False` and `beacon_mode = True`, so
the S-band ladder sits on its SAFE (ex-BEACON) rung *and* the S-band chain is inhibited *and* the
UHF beacon is the only thing on the air. The three states coincide there and only there.
