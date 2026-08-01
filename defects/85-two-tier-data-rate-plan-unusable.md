# 85 — the two-tier data-rate plan was unusable at both ends

## Summary

Franz, on the shipped rate plan:

> "my low rate seems to be 1 kbps which is very low. I might expect a beacon
> at that rate in bootloader, but in routine operation, I need enough to get
> housekeeping data down. For high rate, this should be for delayed telemetry
> and image dumps."

He is arithmetically correct at both ends, and the repo contains the numbers
that prove it.

`configs/eosat1/subsystems/ttc.yaml` offered exactly two rates —
`tm_rate_lo_bps: 1000` and `tm_rate_hi_bps: 64000` — selected by a boolean
`data_rate_mode` (0/1) over S8 func 65.

### 1. The low rate could not carry housekeeping. At all.

`configs/eosat1/telemetry/hk_structures.yaml` generates, at the configured
intervals:

| SID | Name | Params | Packet (B) | Interval (s) | bps |
|---|---|---|---|---|---|
| 1 | EPS | 51 | 105 | 1.0 | 840.0 |
| 2 | AOCS | 71 | 147 | 4.0 | 294.0 |
| 3 | TCS | 17 | 49 | 60.0 | 6.5 |
| 4 | Platform | 26 | 72 | 8.0 | 72.0 |
| 5 | Payload | 23 | 64 | 8.0 | 64.0 |
| 6 | TTC | 27 | 75 | 8.0 | 75.0 |
| **1-6** | | **215** | **512** | | **1351.5 bps** |

After RS(255,223) (x1.1435) and the 1115/1107 TM transfer frame (x1.0072)
that is **1557 bps of channel**. The low rate was **1000 bps — 0.64x the
generation rate.** The real-time backlog could never drain: `_rt_pending`
grew monotonically until `rt_tm_backlog_max` started discarding packets and
`tm_queue_drops` climbed forever. This is not a preference, it is a
quantified defect.

(For scale, the beacon it *should* have been carrying — SID 11, 28 bytes
every 30 s — is 7.47 bps. 1 kbps carries that with 134x headroom.)

### 2. "High" was not a high rate by any real-world measure.

64 kbps is about two-thirds of what a commodity CubeSat **TT&C** radio does
(GomSpace NanoCom AX2150: 96 kbps; EnduroSat S-band: 125 kbps) and 1/16 of
what a real S-band **payload** transmitter does (ISIS TXS2 up to 4.3 Mbps at
transfer-frame level; GomSpace NanoCom Link-S 1-15 Mbps QPSK; ESA's OPS-SAT
flies 1 Mbps S-band). A 4 MiB image took **10.9 minutes** — 0.78 images per
514 s pass, i.e. *less than one image per pass*, which makes the image-dump
exercise unteachable.

### 3. Two rates cannot express four operational needs.

Bootloader beacon, safe-mode/FDIR lifeline, routine operations and bulk dump
are four distinct regimes with four different requirements. With two rungs,
FDIR's `ttc_reduce_power` action and the bootloader beacon both fell to the
same 1 kbps, so "reduce rate for link stability" and "the spacecraft is in
its bootloader" were indistinguishable, and safe mode was left either
under-served at 1 kbps or over-served at 64 kbps.

### 4. Selecting QPSK doubled the reported bit rate.

`tick()` and `set_modulation` both did
`tm_data_rate = base_rate * bits_per_symbol`. Modulation must change the
**symbol** rate and the occupied bandwidth, not the information rate; as
written, a "64 kbps" link reported 128 kbps the moment QPSK was selected.

### 5. The telecommand uplink inherited the top TM rate.

`_tc_uplink_rate_bps = config.get("tc_uplink_rate_bps", self._tm_rate_hi)`.
Raising the top TM tier would silently have made the *uplink* run at the same
rate. The ladder's top rung is 128 kbps, which is 32x Sentinel-1's 4 kbps TC
rate and 2x ESA's typical 64 kbps maximum, and it would have changed the
uplink lock timing with it.

## Severity

**Major.** The rate half of the pass-planning lesson was untenable: the low
rate lost telemetry by construction, the high rate could not move a single
image in a pass, and the two-value enum could not express the mission's own
mode policy.

## Status

**Fixed.** The binary switch is replaced by a **config-declared three-rung
ladder** (Franz's ruling, 2026-07-29). Every number is configuration; nothing
about the ladder is a code constant.

### The ladder

| # | Tier | bps | Modulation | Coding | Occupied BW | Code | Available in |
|---|---|---|---|---|---|---|---|
| 0 | `BEACON` | 1 000 | BPSK | conv(7,1/2)+RS | 3.1 kHz | 0xFF | Bootloader / beacon mode ONLY |
| 1 | `LOW` | 16 000 | BPSK | conv(7,1/2)+RS | 50 kHz | 0x00 | **ROUTINE OPERATIONS** and the safe-mode / FDIR fallback. Antenna may be stowed |
| 2 | `HIGH` | 128 000 | QPSK | RS(255,223) | 100 kHz | 0x01 | Delayed telemetry and image dumps: antenna deployed **and** DOWNLINK/dump mode **and** elevation >= 10 deg |

Uplink is decoupled and fixed at `tc_uplink_rate_bps: 4000`.

### Why these numbers

* **1 / 16 / 128 kbps = 1 kbps x 2^0, 2^4, 2^7.** The steps are **+12.04 dB**
  and **+9.03 dB** — exactly the `-10 log10(R)` term in the link budget, so a
  student can read the ladder straight off the budget.
* **BEACON 1000 bps.** SID 11 is 7.47 bps average, so 1 kbps carries it with
  134x headroom in a 0.22 s burst — the right shape for a power-starved,
  stowed-antenna, PA-off bootloader, and inside the real 1200-9600 bps
  CubeSat beacon band (GASPACS 9600 bps 2GFSK; OPS-SAT 9.6 kbit/s GMSK UHF).
  **Franz's constraint is met exactly: 1 kbps is now reachable only from the
  bootloader.**
* **LOW 16 000 bps — the routine operations rate.** Derived, not chosen.
  Real-time HK needs 1557 bps of channel; in a 514 s maximum pass 16 kbps
  clears that at 9.7 % duty **plus 906 kB = 0.85 orbits of stored HK**, i.e.
  the smallest round number at which the spacecraft neither loses data nor
  falls behind across an orbit. It is Sentinel-1's low S-band TM rate and
  sits between the AX2150's 9.6 and 19.2 kbps steps. BPSK keeps the PA in
  constant envelope, which is what a safe-mode / stowed link wants.
* **HIGH 128 000 bps — dumps only.** Sentinel-1's *nominal* S-band TM rate
  and the top of the commodity CubeSat TT&C-radio class (EnduroSat 125 kbps,
  AX2150 96 kbps), so the whole ladder is reachable with **one TT&C radio** —
  no separate high-rate transmitter is implied, which is the right hardware
  story for a 6U. A 4 MiB image takes **262.1 s = 58 % of the mission's
  448 s average pass**, so an image dump costs a real, schedulable fraction
  of a pass and pass planning stays the binding lesson.
* **QPSK at HIGH is a judgement call, stated as one.** The link is not
  power-limited (35.2 dB of margin at Iqaluit / 5 deg) and 5 MHz of channel
  is not scarce (200 kHz at BPSK vs 100 kHz at QPSK), so neither lever is
  forced. QPSK was chosen because it halves the occupied bandwidth for the
  same information rate and makes the tier step *visible* on the radio — the
  constellation, the eye and the spectrum all change — which teaches that
  rate and modulation are independent levers. The counter-argument is real:
  flight TT&C radios at this rate (AX2150, EnduroSat) use constant-envelope
  GMSK/GFSK, and a saturated PA would prefer OQPSK. `modulation: OQPSK` in
  `ttc.yaml` is a one-line change if that fidelity matters more.

The single most important precedent is **Copernicus Sentinel-1**: a real EO
mission whose S-band housekeeping downlink is a programmable ladder
(16 / 128 / 512 kbit/s) over a fixed 4 kbit/s telecommand uplink. The two
operational rungs this mission flies are Sentinel-1's low and nominal rates
exactly.

### Mode / rate policy, enforced by REJECTION

| Spacecraft state | BEACON | LOW | HIGH |
|---|---|---|---|
| Bootloader, antenna stowed, PA off | **forced** | x | x |
| SAFE, antenna stowed | x | **default** | x |
| STANDBY / IDLE / IMAGING (routine ops) | x | **default** | x |
| DOWNLINK (payload dump), elevation >= 10 deg | x | commandable | **commandable** |
| FDIR `ttc_reduce_power` | x | **forced** | x |

A request for a tier that is not permitted is **refused with an S1.8
execution failure** — error `0x0007` for a permission gate, `0x0003` for an
unknown tier code — never silently clamped, and the commanded tier is left
untouched. The *active* tier may be demoted by a gate (HIGH auto-reverts to
LOW at LOS or below 10 deg) while the *commanded* tier is remembered, so the
tier resumes on the next good geometry without being re-commanded. The
commanded tier remaining authoritative across ticks is defect #74's fix and
is pinned by a regression test.

### The data budget — does it still close?

Franz asked directly: *"does that still give a decent data budget
calculation, knowing delayed TM and regular TM also need to come down with
images?"* Using the repo's own configuration (`hk_structures.yaml`,
`payload.yaml` 4 MiB images) and the mission's five-station contact
statistics (**385 contact-min/day across 103 passes per 48 h** = 51.5
passes/day, 23 100 s/day, **average pass 448.5 s = 7.48 min**). All figures
are MiB of *channel* (payload x 1.1507 for RS(255,223) and the 1115/1107
transfer frame), which is how the 1557 bps HK figure is derived, so HK and
imagery are compared in the same units.

**1. One 4 MiB image at HIGH.**

```
4 MiB = 33 554 432 bit / 128 000 bps = 262.1 s = 4.37 min = 58.4 % of an average pass
  with the planner's 20 % protocol allowance: 327.7 s = 5.46 min = 73.1 % of a pass
  -> 1.71 images per average pass raw, 1.37 with the allowance
```

An image now costs well over half a pass. That is the answer to "is this
still a meaningful fraction of a pass?" — yes, decisively. (At the rejected
1.024 Mbps it would have been 41 s, 9 % of a pass, and pass planning would
have stopped being a constraint at all.)

**2. Daily capacity, and what HK takes off the top.**

The continuous HK stream is 1557 bps of channel = **134.5 Mbit/day =
16.04 MiB/day**, and it can only be delivered during contact, so it needs
**5 824 bps averaged across the 23 100 s of daily contact**.

| | LOW (16 kbps) | HIGH (128 kbps) |
|---|---|---|
| Capacity over all contact | 44.1 MiB/day (46.2 MB) | 352.5 MiB/day (369.6 MB) |
| Capacity over the >=10 deg share only (79 %) | 35.0 MiB/day | 279.8 MiB/day |
| With the 20 % protocol allowance | 35.2 MiB/day | 282.0 MiB/day |
| **HK share of the link during contact** | **36.4 %** | **4.5 %** |
| (real-time HK alone, 1557 bps) | 9.7 % | 1.2 % |

Planner-consistent figures, using `budget_tracker`'s own elevation-weighted
19 120 s/day and its 20 % protocol factor: **LOW 29.2 MiB/day (30.6 MB/day),
HIGH 233.4 MiB/day (244.7 MB/day)**, versus 116.7 MiB/day at the old 64 kbps.

**3. Images per day the network can sustain, after HK.**

One image on the channel is 4 MiB x 1.1507 = **4.60 MiB**.

| Operating profile | Capacity | minus HK | images/day (raw) | with 20 % proto |
|---|---|---|---|---|
| LOW for the whole day | 44.06 | 28.02 | **6.1** | 4.9 |
| Mixed: HIGH above 10 deg, LOW below | 288.87 | 272.83 | **59.3** | 47.4 |
| HIGH for the whole day | 352.48 | 336.44 | 73.1 | 58.5 |

Against a mission that generates **3-5 images/day** (25.9-33.9 MiB of
payload, 29.9-39.1 MiB of channel including HK).

**4. Does the nominal ops day close?**

| Profile | Capacity | 3 img/day | 5 img/day |
|---|---|---|---|
| LOW only, raw | 44.06 MiB | closes, +32 % | closes, +11 % |
| LOW only, with 20 % proto | 35.2 MiB | closes, +15 % | **FAILS, -11 %** |
| LOW only, planner-consistent | 29.2 MiB | **FAILS** | **FAILS** |
| Mixed (HIGH >=10 deg) | 288.9 MiB | closes, +90 % | closes, +87 % |

And the shipped `nominal_ops_day` plan (8.6 MB generated against 27.8 MB of
contact capacity at the old 64 kbps): **at LOW alone it is 6.95 MB of
capacity and the plan FAILS by 24 %; at HIGH it is 55.6 MB and closes with
85 % margin.**

**This is the right answer, not a problem.** The ladder makes the budget a
*plannable* quantity instead of a foregone conclusion:

* LOW carries **all** housekeeping — real-time and stored playback — with
  64 % of the link to spare, so routine operations never lose telemetry.
  That was Franz's first requirement and it is met with 10.3x headroom over
  the 1557 bps HK channel rate.
* LOW alone does **not** carry the imaging plan. Imagery requires HIGH-tier
  dump passes, which is Franz's second requirement and is now a scheduling
  decision the student has to make.
* The cost of that decision is concrete and small: one average pass at HIGH
  moves **1.49 images** (1.19 with the protocol allowance), so 3 images/day
  needs **~2.5 dump passes** and 5 images/day needs **~4.2** — i.e. **5-8 %
  of the 51.5 passes/day** must be booked as DOWNLINK-mode dumps above
  10 deg. Everything else stays at LOW.
* Delayed telemetry and imagery genuinely compete: on a dump pass HK
  playback still takes 4.5 % of the link, and on a LOW pass it takes 36 %.
  The trade is visible in the numbers rather than hidden by a 55x margin.

### What changed, concretely

* `configs/eosat1/subsystems/ttc.yaml` — `tm_rate_tiers` (name, bps,
  `command_code`, modulation, coding, per-tier coding gain, permission
  gates), `default_tier: LOW` / `beacon_tier: BEACON` / `safe_tier: LOW`,
  explicit `tc_uplink_rate_bps: 4000`, and the link-budget loss terms from
  defect #84. `tm_rate_hi_bps` / `tm_rate_lo_bps` are retained as
  **deprecated aliases** (now 128000 / 16000) so out-of-lane readers — the
  planner's `budget_tracker` and the MCS — keep resolving until they migrate;
  when absent from the YAML `smo_common`'s `TTCConfig` derives them from the
  ladder.
* `smo_common/config/schemas.py` — `TMRateTier` model with validation
  (ascending bps, unique names, known modulation/coding, tier names resolve),
  plus `TTCConfig.tier()` / `tier_index()` helpers.
* `models/ttc_basic.py` — `data_rate_mode` is a tier index over the ladder,
  not a boolean; `tm_tier` carries the *active* tier; `_effective_tier()`
  applies the gates; `_tier_permission_error()` produces the rejection
  reasons; `link_margin_for()` exposes the closed-form budget;
  `tier_symbol_rate()` and `tier_occupied_bw_hz()` derive the RF consequences.
  **`tm_data_rate` is now the tier's information rate exactly** — the
  `* bits_per_symbol` bug is gone from both `tick` and `set_modulation`, and
  modulation moves the symbol rate instead. The uplink no longer defaults to
  the top TM rate, and the uplink-lock pacing thresholds are expressed
  against the TC rate (`TC_SLOW_LOCK_RATE_BPS` / `TC_FAST_LOCK_RATE_BPS`).
* `engine.py` — the bootloader/beacon path selects the configured
  `beacon_tier`; leaving the bootloader lands on `safe_tier` (= LOW = the
  routine rate), never on the top tier; `_current_tm_rate_bps()` /
  `_current_tm_tier()` / `_current_tm_coding()` are the single rate source
  for the real-time pacer (defect #74, `simulation.yaml ttc.rt_tm_pacing`)
  and the S15 dump pacer, so both honour the tier; the WS snapshot publishes
  `tm_tier`, `tm_tier_name`, `tm_coding` and `symbol_rate_bd`.
* `service_dispatch.py` + `tc_catalog.yaml` — S8 func 65 keeps its single
  `uint8` field, so the byte count the MCS derives (1 func_id + 1 field = 2)
  still matches the handler exactly (defect #80 was this class of bug). The
  value is a tier `command_code`: **0 = LOW, 1 = HIGH, 0xFF = BEACON**. Codes
  0 and 1 preserve the legacy binary encoding *verbatim* — 0 has always meant
  "the low rate" and 1 "the high rate", and they still do; they simply land
  on 16 kbps and 128 kbps instead of 1 kbps and 64 kbps. That keeps the FDIR
  `ttc_reduce_power` action (`data: [0x00]`) correct without editing it, and
  keeps every procedure that raised the rate for a dump correct too. Tier
  names and raw bps values are also accepted. A bare TC with no argument
  selects LOW, not the top tier. New func 79 `TTC_SET_DOWNLINK_MODE` is the
  HIGH gate. `obcp/01_pass_preparation.yaml` sent the raw uint32 `0000fa00`
  (64000), which is no longer a configured rate and would be refused; it now
  sends the catalogued single byte `00` (LOW), which is the correct rate for
  pass preparation — the dump procedure raises to HIGH when it enters
  DOWNLINK mode.
* New telemetry: `0x0539` active tier, `0x053A` commanded tier, `0x053B`
  symbol rate (Bd), `0x053C` downlink mode. `0x0506` continues to carry the
  active rate in bps.
* `smo-rfsim` — `pipeline.max_symbol_rate` was an arbitrary 200 kBd and is
  now **derived from the regulation**: NASA's S-band table caps a mission at
  5.00 MHz occupied in 2200-2290 MHz and occupied BW is
  `(1 + rrc_rolloff) x symbol rate`, so at alpha = 0.35 the legal ceiling is
  5.00e6 / 1.35 = **3.70 MBd**. Anything the mission may lawfully transmit is
  accepted; anything refused is refused because it would breach the channel
  allocation. `pipeline.symbol_rate` defaults to the LOW tier (16 kBd), which
  is where routine operations sit. The bridge applies the tier's modulation
  and bit rate together and forces a retune when the modulation changes at a
  fixed bit rate, so the constellation, occupied bandwidth and eye all move
  with the tier; the radio status carries `tm_tier_name`, `tm_coding` and
  `occupied_bw_hz`. `ccsds.convolutional_follow_tier` (opt-in, default off)
  switches the Viterbi decoder on for the conv+RS tiers — off by default only
  because the pure-Python decoder is ~5 s/frame; the coding is reported
  either way. `rfsim.yaml`'s `link_budget` and `ttc.yaml` are now checked
  against each other by test.

### Bandwidth, regulation and margin

Occupied bandwidth is `(1 + 0.35) x symbol rate` after RS(255,223), the
1115/1107 frame and the r=1/2 convolutional expansion: **3.1 kHz / 50 kHz /
100 kHz**. NASA's S-band table caps a mission at **5.00 MHz** in
2200-2290 MHz, so the top tier uses **2.0 %** of the allocation. With the
defect #84 budget fixed, margins (`tools/link_budget_ladder.py`) are:

| Tier | Iqaluit 5 deg | Troll 5 deg | Iqaluit 10 deg | Troll 10 deg | Iqaluit zenith |
|---|---|---|---|---|---|
| BEACON | 57.8 | 56.3 | 62.0 | 60.5 | 74.3 |
| LOW | 45.7 | 44.2 | 50.0 | 48.5 | 62.3 |
| HIGH | 35.2 | 33.7 | 39.5 | 38.0 | 51.8 |

Worst permitted case is **HIGH at Troll, 10 deg = 38.0 dB** (HIGH is gated to
>= 10 deg). The ladder is bandwidth- and policy-limited, not power-limited,
which is the honest engineering story and a better lesson than a fake power
cliff. No X-band is needed for this mission.

### Verification

* `tests/test_simulator/test_tm_rate_ladder.py` — 57 tests: ladder comes from
  config and is tunable; tier selection and every rejection rule, including
  that the FDIR `[0x00]` byte still selects LOW and that LOW works with the
  antenna stowed; commanded tier survives ticks and HIGH auto-reverts to LOW
  and returns; telemetry reports the active and commanded tiers and the
  symbol rate; both pacers honour the tier; **the HK backlog drains at LOW
  and at HIGH and grows without bound at BEACON**; catalogue/handler byte
  agreement; modulation follows the tier and moves the symbol rate not the
  bit rate; per-tier occupied bandwidth against the 5 MHz channel; per-tier
  and per-station link margins at 5 deg and zenith; and that one image costs
  50-70 % of an average pass.
* `tests/test_rfsim/test_rate_ladder_pipeline.py` — the regulatory ceiling is
  what `max_symbol_rate` is, the pipeline retunes per tier end to end (TX,
  channel, RX), the bridge follows tier + modulation + coding, `rfsim.yaml`
  and `ttc.yaml` agree on the link budget and the downlink frequency.
* `tools/link_budget_ladder.py` — prints the whole tier x station x elevation
  margin table and the occupied-bandwidth check; exits non-zero on any
  failure.

### Sources

* [eoPortal — Copernicus Sentinel-1](https://www.eoportal.org/satellite-missions/copernicus-sentinel-1)
  (S-band 16 / 128 / 512 kbit/s programmable TM; 4 kbit/s TC)
* [eoPortal — ESA OPS-SAT-1](https://www.eoportal.org/satellite-missions/ops-sat)
  (1 Mbit/s S-band downlink; 256 kbit/s S-band uplink "vs. typical ESA
  maximum of 64 kbit/s"; 9.6 kbit/s GMSK UHF beacon)
* [GomSpace NanoCom AX2150 datasheet](https://gomspace.com/UserFiles/Subsystems/datasheet/gs-ds-nanocom-ax2150-1028153-21.pdf)
  (S-band TT&C 9.6 / 19.2 / 38.4 / 96 kbps, RS(255,223))
* [EnduroSat S-band transceiver](https://www.endurosat.com/products/s-band-transceiver/)
  (0.1-125 kbps configurable)
* [GomSpace NanoCom Link-S datasheet](https://gomspace.com/wp-content/uploads/2025/09/gs-ds-NanoCom-Link-1046020-10-2.pdf)
  (0.5-7.5 MBd; 1-15 Mbit/s QPSK; conv r=1/2 + RS(255,223))
* [ISIS TXS2 S-band transmitter datasheet](https://www.cubesatshop.com/wp-content/uploads/2016/06/ISIS-TXS2-DSH-0001-TXS2_Datasheet-02_01.pdf)
  (0.625-5 Msym/s; 4.3 Mbit/s at transfer-frame level; worked case of
  2.1 Mbit/s OQPSK from 5 deg to a 1.9 m station at 3.0 dB margin)
* [NASA Explorers — S-Band Overview, 2-8 GHz](https://explorers.larc.nasa.gov/2023ESE/pdf_files/S-Band-Overview_2-8GHz.pdf)
  (2200-2290 MHz allocation; **maximum bandwidth 5.00 MHz** per mission)
* [NASA NTRS 20210022422 — S-band Network Analysis for LEO Multi-CubeSat Science Missions](https://ntrs.nasa.gov/api/citations/20210022422/downloads/S-band%20Network%20Analysis%20and%20Strategies%20for%20LEO%20Multi-CubeSat%20Science%20Missions_15Oct21c_final.pdf)
  (NEN station G/T 17.0-29.6 dB/K, which brackets this repo's 18.5-22.0)
* [NASA State of the Art of Small Spacecraft Technology — 9.0 Communications](https://www.nasa.gov/smallsat-institute/sst-soa/soa-communications/)
* [CCSDS 413.0-G-3 — Bandwidth-Efficient Modulations](https://ccsds.org/Pubs/413x0g3e1.pdf)
  (Category A recommended modulations; SFCG 21-2R4 spectral mask)
* [GASPACS CubeSat communications](https://smallsatgasteam.github.io/GASPACS-Comms-Info/)
  (9600 bps 2GFSK AX.25 beacon every 120 s)
* [arXiv:1908.09501 — CubeSat Communications: Recent Advances and Future Challenges](https://arxiv.org/pdf/1908.09501)

### Known follow-up (other lanes)

* `smo-planner` — `budget_tracker.DOWNLINK_RATE_BPS = 64000` and
  `params["downlink_rate_bps"] = ttc.tm_rate_hi_bps` must become a per-
  activity tier selection (**HIGH 128 kbps for `data_dump`, LOW 16 kbps for
  routine/HK**), and `constraint_checkers` / `server.py` carry three more
  `64000.0` defaults. The deprecated aliases keep them running in the
  meantime and now resolve to 128000 / 16000, so
  `tests/test_planner/test_data_budget_config.py`'s 116.7 MB/day assertions
  need re-deriving to **233.4 MiB/day at HIGH and 29.2 MiB/day at LOW**
  (planner-consistent, its own 19 120 s/day and 20 % protocol factor).
  `min_dump_elevation_deg` should express the 10 deg HIGH gate, and
  `_elevation_efficiency()` becomes the tier-gating rule rather than a
  triangular proxy. `activity_types.yaml`'s `data_dump duration_s: 600` was
  sized for 64 kbps; at HIGH one 4 MiB image is 262 s raw / 328 s with the
  protocol allowance, so 600 s carries ~1.8 images — close to right, but it
  should be re-derived rather than left coincidental.
* MCS: `server.py`'s key map is fine, but the WS `ttc` block now carries
  `tm_tier`, `tm_tier_name`, `tm_coding`, `symbol_rate_bd` and
  `downlink_mode` — display `LOW (16 kbps, BPSK, conv+RS)` rather than a bare
  number. New params 0x0539-0x053C want entries in
  `telemetry/parameters.yaml` and `mcs/limits.yaml` (an expected-value check
  per mode would catch a spacecraft left at HIGH). `TTC_SET_DOWNLINK_MODE`
  (S8 func 79, one uint8) needs a UI control.
* Docs / procedures / courseware still quote 1 kbps / 64 kbps throughout:
  `manual/05_ttc.md` (also still shows the retired UHF frequencies),
  `manual/09_leop.md`, `manual/07_orbit_ops.md`, `docs/design_document.html`,
  `docs/generate_design_pdf.py`, `docs/sim_fidelity/ttc_fidelity.md`,
  `docs/test_plan/ACCEPTANCE_TEST_PLAN.md:147` (CMD-TTC-014 expects
  `41 0000FA00`), `docs/EOSAT1_Commissioning_Pass_Plan.html` (COM-006
  "Confirm 1 kbps housekeeping rate" — now a BEACON-tier check),
  `configs/eosat1/mcs/role_analysis/ttc_role.md`. The textbook's p. 400 claim
  that "the EOSAT-1 simulator only supports the two rates above" is now
  false, and its COM-006 margin baselines must be re-measured against the
  corrected budget (`tools/link_budget_ladder.py` prints the table).
* `procedures/nominal/data_rate_change.md` and
  `procedures/contingency/ber_anomaly.md` still quote func_id 52 where the
  catalogue says 65 — pre-existing, and worth fixing in the same pass. The
  rate-reduction step in `ber_anomaly.md` now targets LOW.
* `workspace/breakpoints/*.json` all carry `data_rate_mode: 0` with beacon
  mode set, which still restores correctly as BEACON. No migration needed.
