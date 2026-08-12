# Payload display + cross-owner requirements — Phase 4 (2026-08b)

Written by the PAYLOAD (Phase 4) owner. Audiences, in order:

* the **single MCS owner** (coherence review §2.5) — sections 2 and 3;
* the **AOCS owner** — section 4, answering the coordination contract in
  `DISPLAY_REQUESTS_AOCS.md` §3;
* the **EPS owner** — section 5 (requests, not changes: `eps_basic.py` and
  `eps.yaml` are not payload-owned);
* the **TT&C owner** — section 6;
* the **textbook owner** (Phase 7) — section 7;
* whoever owns `smo-common` — section 8.

Nothing in `smo-mcs` has been touched. Phase 4 changed
`payload_basic.py`, `tcs_basic.py`, the payload/TCS/S13 blocks of
`service_dispatch.py`, `tm_storage.py`, and the payload's own config files.

---

## 1. What changed in the model that the ground can now see

### 1.1 New parameters (all in the payload-reserved `0x06xx` range, all in HK SID 5)

| id | name | type | why it exists |
|---|---|---|---|
| `0x0644` | `payload.cal_mode` | 0-2 | which phase set the last/current calibration asked for (DARK / FLAT / BOTH) |
| `0x0645` | `payload.cal_abort_reason` | 0-5 | **why a calibration produced nothing.** 0 = completed, 1 = detector unpowered, 2 = shutter moved, 3 = lamp state changed, 4 = FPA left its regulation band, 5 = operator abort |
| `0x0646` | `payload.cal_valid_mask` | bits 0-3 | bands holding **both** a bias and a gain coefficient; `0x0F` = fully calibrated |
| `0x0647` | `payload.product_mb` | MB | volume held by **catalogued products** — distinct from `mem_used_mb` (`0x060B`), which also carries the store's pre-existing fill |
| `0x0648` | `payload.product_count` | count | number of image products on board |
| `0x0649` | `payload.readout_active` | bool | 1 while a commanded strip is being read out |
| `0x064A` | `payload.cooler_floor_c` | °C | coldest FPA temperature the cooler can hold **at the present sink and load** |
| `0x064B` | `payload.cooler_margin_k` | K | commanded setpoint − achievable floor; **≤ 0 means the setpoint is out of reach** |

`0x064A` / `0x064B` are the payload's mirror of two new TCS-side parameters,
`0x0424 tcs.fpa_cooler_floor_c` and `0x0425 tcs.fpa_cooler_margin_k`. Those two
are written by `tcs_basic` but are **not declared in `parameters.yaml`** —
`0x04xx` is the TCS range and the payload owner was scoped to add `0x06xx`
only. **Request:** whoever owns the TCS parameter block should declare them
(see §5.3); until then, read the payload mirrors, which are declared and in
HK SID 5.

### 1.2 New events (payload-reserved `0x06xx` range)

| event | name | when |
|---|---|---|
| `0x060E` | `SHUTTER_TEST_COMPLETE` | already emitted — **was declared nowhere and downlinked as UNKNOWN** |
| `0x060F` | `FILTER_ROTATION_COMPLETE` | ditto |
| `0x0610` | `TRANSFER_START` | promised by a source comment, previously sent by **nothing** |
| `0x0611` | `TRANSFER_COMPLETE` | already emitted — also UNKNOWN |
| `0x0612` | `CALIBRATION_ABORTED` | a running calibration lost a precondition; reason at `0x0645` |
| `0x0613` | `IMAGE_CAPTURED` | a product was written to the store |
| `0x0614` | `IMAGE_RELEASED` | a product was deleted and its storage freed (S13.5 completion) |

`payload_basic.PAYLOAD_EVENTS` and `events/event_catalog.yaml` are now the same
list, and a test (`test_payload_2026_08b.py::TestEventTable`) fails if they
drift.

### 1.3 New and changed telecommands

| TC | change |
|---|---|
| `PAYLOAD_SET_MODE` (func 26) | **mode 3 = CALIBRATION added.** Value 2 is unchanged and still means IMAGING (see §4). |
| `PAYLOAD_SET_SHUTTER` (**func 119, new**) | absolute shutter position, 0 = CLOSED / 1 = OPEN. There was no absolute shutter command anywhere. |
| `PAYLOAD_CYCLE_SHUTTER` (func 93) | `cycles` now counts **blade transitions**, one per tick, and the handler no longer toggles as well. An odd count ends on the opposite position. |
| `PAYLOAD_START_CALIBRATION` (func 37) | gated on the optics, not on the mode word; full precondition list in the catalogue entry. |
| `PAYLOAD_GET_IMAGE_CATALOG` (func 32) | now returns a **TM(8,2) catalogue report**; it used to return nothing at all. |
| `PAYLOAD_DOWNLOAD_IMAGE` (func 29) | now returns a **TM(8,2) product record**. |
| `PAYLOAD_INITIATE_TRANSFER` (func 94) | now returns **TM(8,2): scene_id(2) + bytes_total(4) + blocks(4) + crc32(4)** and raises `0x0610`. |
| `S13_CREATE_TRANSFER` | bound to a real catalogued product; TM(13,2) gains `total_blocks(4) + crc32(4)` **appended** after the transfer id. |
| `S13_END_TRANSFER` | a fully-dumped session now deletes the product and frees its storage. |

Func 119 was taken from the free `119-127` block, as instructed: func 96 is
`AOCS_SET_DESAT_INHIBIT` and 97/98 are the TT&C owner's UHF beacon.

---

## 2. Display requirements, in priority order

### D-1 (critical) — an FPA-readiness block that explains itself

This is the display half of Franz's "cannot do a capture — FPA does not get to
YES". The root causes were wiring (fixed), but the reason the operator could
not diagnose it in the first place is that **`0x0610 payload.fpa_ready` is a
bare 0/1 with nothing beside it**. Four different conditions produce the same
`0`, and the panel showed none of them.

Requirement: wherever `payload.fpa_ready` is shown, show these beside it —

```
FPA READY   NO
  detector    -8.3 C      (0x0601)          ← where it is
  setpoint   -15.0 C      (0x0631)          ← where it was told to go
  floor      -12.8 C      (0x064A)          ← where it CAN go
  margin      -2.2 K      (0x064B)  ← RED   ← the answer
  cooler      ON          (0x040C)
  EPS line 4  ON          (0x0114)
```

`cooler_margin_k` ≤ 0 is the single most valuable new indicator in this batch:
it says "the cooler physically cannot reach the setpoint you commanded at this
sink temperature", which is a state the model can now genuinely enter (§7.2)
and which COM-011's off-nominal section asks the trainee to diagnose. Colour it
red at ≤ 0, amber below 2 K.

`0x0114` (EPS `fpa_cooler` line) belongs in this block even though it is an EPS
parameter — three of the four cooler command paths failed *precisely* because
that line stayed dark, and the operator had no reason to look at an EPS page
while debugging a payload symptom.

### D-2 (critical) — the payload mode label needs a fourth entry

Any table mapping `payload.mode` (`0x0600`) to a label must gain **3 =
CALIBRATION**. There is at least one such table in `smo-mcs/static/index.html`.
A mode the panel renders as `?` during every calibration is worse than useless
on a card that is about calibration.

Suggested labels: `OFF` / `STANDBY` / `IMAGING` / `CALIBRATION`, with
CALIBRATION shown in the same colour family as STANDBY (the cooler is running,
the instrument is not producing products).

### D-3 (high) — a calibration block

`0x062A cal_active` and `0x062B cal_progress` already exist and are already
displayed. Add, in the same block:

* `0x0644 cal_mode` as `DARK` / `FLAT` / `BOTH`;
* `0x0646 cal_valid_mask` as four band lamps (`B G R N`), all four lit = fully
  calibrated;
* `0x0645 cal_abort_reason` — **only when non-zero**, rendered as text:
  `1 detector unpowered`, `2 shutter moved`, `3 lamp state changed`,
  `4 FPA left its band`, `5 operator abort`.

Rationale: a calibration can now stop early, on purpose, and silently
discarding half-made coefficients is the correct behaviour. Without `0x0645`
the operator sees a progress bar that stopped and no reason anywhere.

### D-4 (high) — an image-store block that distinguishes products from fill

The payload panel currently shows `store_used %` (`0x0604`) and
`mem_used_mb` (`0x060B`). Neither answers "what can I downlink?", because the
store starts 20 % full of non-product fill.

Requirement: add `0x0648 product_count` and `0x0647 product_mb` next to them:

```
STORE   4001.4 / 20000 MB   (20.0 %)
  products     3   (4.1 MB)        ← 0x0648 / 0x0647
```

This is also the display that makes the PL-b fix legible. Before it, IMAGING
alone accreted ~9.7 MB/s and `store_used` climbed steadily with zero captures;
now `store_used` only moves when `product_count` moves, and an operator who
learned the old behaviour needs to see the new one.

### D-5 (high) — an image catalogue view, fed by func 32

`PAYLOAD_GET_IMAGE_CATALOG` (S8 func 32) now answers with a TM(8,2) report.
Wire format:

```
header : shown(u16) held(u16) product_mb_x100(u32) free_mb_x100(u32)
entry  : scene_id(u16) lat_x1e4(i32) lon_x1e4(i32) size_kb(u32)
         lines(u16) quality_pct(u8) status(u8) flags(u8) segment(u8)
         -- 20 octets, repeated `shown` times (max 32)
```

`status`: 0 = OK, 1 = PARTIAL, 2 = CORRUPT.
`flags` bit 0 = the strip was flown on a **degraded (3-wheel) FINE set**;
bit 1 = a product checksum has been computed.

Suggested rendering: a sortable table (scene, lat/lon, MB, lines, quality,
status). This is the display that answers "where do the image packets go?" —
right now the MCS has no view of on-board imagery at all.

The same 20-octet record is emitted by `PAYLOAD_DOWNLOAD_IMAGE` (func 29) for a
single scene, and is what `PAYLOAD_CAPTURE` writes into the Science_Store, so
one decoder serves all three.

### D-6 (medium) — S13 transfer progress with a checksum verdict

`PAYLOAD_INITIATE_TRANSFER` (func 94) and `S13_CREATE_TRANSFER` both now report
the product's CRC-32. If the MCS accumulates S13.3 blocks it can compute the
CRC of what it received and show `CHECKSUM OK` / `CHECKSUM MISMATCH`.

That is worth doing, because it makes the `image_corrupt` failure injection
observable for the first time: a corrupt product serves damaged bytes but
carries the clean checksum the instrument computed at capture, so a corrupted
image now actually **looks** corrupted on the ground instead of being
byte-for-byte identical to a good one.

### D-7 (medium) — shutter and readout lamps

* `0x063C shutter_pos` already exists; it is now **commandable absolutely**
  (func 119) and is a hard precondition for both capture and dark frames. It
  deserves a lamp on the payload panel, not just an on-demand parameter.
* `0x0649 readout_active` is new and is the honest "the instrument is
  scanning" indicator. `0x0607 line_rate` and `0x0608 data_rate` are now
  non-zero **only** during a commanded strip (a few seconds per capture), so a
  display that renders them as a continuous trend will look dead. Render them
  as instantaneous values gated on `0x0649`.

### D-8 (medium) — add func 119 and the calibration sequence to the payload command panel

If the MCS has quick-action buttons for payload commands, `PAYLOAD_SET_SHUTTER
OPEN` / `CLOSED` should be among them: it is now step 1 of every dark-frame
procedure, and there is no other way to reach a known shutter position.

### D-9 (low) — staleness

HK SID 5 is an 8 s structure and is inside the current hard-coded rules, so it
is not affected by the cadence × 1.5 staleness fix. Noted only so the MCS owner
does not have to check.

---

## 3. Note on packet sizes

HK SID 5 grew by 11 octets (8 new parameters). It was already the second-widest
structure; if the MCS has any hard-coded SID 5 length it will need updating.
The 8 s cadence is unchanged, so the HK budget in `tm_storage.py`'s sizing
comment (≈1.64 pkts/s) still holds.

---

## 4. Answer to the AOCS owner (`DISPLAY_REQUESTS_AOCS.md` §3)

**Your contract holds. `payload.mode == 2` still means IMAGING and nothing
else. Do not change your constant.**

1. CALIBRATION was added as a **new** value, 3, exactly as your §3.3 said would
   be safe. Values 0/1/2 keep their meanings bit-for-bit. There is a test
   (`test_payload_2026_08b.py::TestCalibrationMode::test_mode_2_still_means_imaging`)
   whose only job is to fail if anyone renumbers the enum, and its docstring
   points at your document.

2. **A calibration does not raise your inhibit, and should not.** In mode 3 the
   shutter is closed (dark) or looking at an internal lamp (flat); there is no
   pointing requirement and no strip to truncate. A momentum dump during a
   calibration is harmless. If you would rather fence it off anyway, key on
   `payload.mode in (2, 3)` — but the payload owner's recommendation is to
   leave it at `== 2`.

3. **Degraded FINE is permitted to capture.** You asked us to decide. A 3-wheel
   FINE set (`0x0299 aocs.fine_degraded`) captures normally. Reasoning: the
   payload's capture gate already enforces the numbers that actually determine
   image quality — total pointing error ≤ 0.05° and rate stability
   ≤ 0.005 °/s — and a wheel count is not a pointing specification. Refusing on
   wheel count would refuse strips that meet spec, which is the wrong failure.

   What we do instead is **record it**: every catalogue entry carries a
   `fine_degraded` field, surfaced as bit 0 of the `flags` octet in the func 32
   report (§D-5). A reprocessing campaign can then find every strip flown on
   three wheels without re-deriving it from AOCS history, and the relaxed
   jitter spec is product provenance rather than a hidden difference.

   If you later decide the relaxed jitter spec should tighten the payload's own
   thresholds when `fine_degraded` is set, say so and we will read `0x0299` in
   the gate rather than only in the stamp.

4. **We now command FINE ourselves.** `planning/activity_types.yaml`
   `imaging_pass` gained `AOCS_SET_MODE(5)` plus a `wait_for aocs.mode == 5`
   at the front of its command sequence, and its precondition changed from
   `aocs.mode == 1` (SAFE_BOOT — which no capture could ever satisfy) to
   `aocs.mode == 5`. The `calibration` activity's precondition changed from
   `aocs.mode == 1` to `aocs.mode == 4` (NOMINAL), matching its own
   `requires_attitude: nominal`; it runs AOCS_GYRO_CALIBRATION and does not
   need fine pointing. Please sanity-check both against your mode machine.

5. Your §3.5 (DESAT returns to the pre-desat mode and re-promotes to FINE)
   is what makes the payload's "exactly mode 5" gate satisfiable after a dump.
   Noted and relied upon; the gate is unchanged.

6. We are **not** reading `0x02C5` bit 4 (momentum envelope ≥ 70 %) in the
   capture gate. It is good advice for a planner but it is not a property of
   the image, and refusing a capture because a dump is *coming* would refuse
   perfectly good strips. Recommend the MCS shows it on the imaging panel
   (§D-1's neighbourhood) as a planning warning instead.

---

## 5. Requests for the EPS owner

These are payload-driven needs in EPS-owned files. None has been made.

### 5.1 Billing table: payload mode 3

`eps_basic.set_payload_mode` and any per-mode wattage table in `eps.yaml` need
an entry for **mode 3 = CALIBRATION**. Suggested load: the same as mode 1
(STANDBY) plus the calibration lamp when lit. The detector is powered and the
cooler is running, but nothing is being read out or compressed, so it is
materially cheaper than IMAGING.

Today an unknown mode 3 will most likely fall through to a default; please make
it explicit rather than implicit. `EPS_PAYLOAD_MODE` (S8 func 16) forwards
whatever it is given straight to the payload, so mode 3 is reachable from the
EPS path too.

### 5.2 The `fpa_cooler` line is now driven from four more places

`ServiceDispatcher._request_fpa_cooler` energises EPS line 4 on **every**
cooler request path (funcs 16, 17, 26, 43 and S2 device 0x0601), where before
only func 17 did. Nothing in EPS changed, but the line will now be switched
considerably more often, including by `PAYLOAD_SET_MODE`. If EPS has any
switching-cycle counter, overcurrent debounce or inrush model on that line,
it will see traffic it did not see before.

The helper also writes `params[0x0114]` directly at command time so that the
TCS model's new "the line is dark" refusal does not fire on a line the same
call has just switched on. EPS republishes the parameter on its next tick, so
the value is not stolen — but if EPS ever gains a reason to *refuse*
`set_fpa_cooler`, that pre-write must move behind the refusal check.

### 5.3 Please declare the two new TCS parameters

`tcs_basic` now publishes `0x0424` (achievable FPA floor, °C) and `0x0425`
(margin, K). They are in the TCS `0x04xx` range and were not declared in
`telemetry/parameters.yaml` because the payload owner was scoped to `0x06xx`.
Suggested entries:

```yaml
  - { id: 0x0424, name: tcs.fpa_cooler_floor_c, subsystem: tcs, units: C, description: "Coldest FPA temperature the cooler can hold at the present sink and load" }
  - { id: 0x0425, name: tcs.fpa_cooler_margin_k, subsystem: tcs, units: K, description: "Commanded FPA setpoint minus achievable floor; <= 0 means out of reach" }
```

Adding them to HK SID 3 (TCS, 60 s) would be a bonus; the payload mirrors are
in SID 5 at 8 s, so this is not urgent.

---

## 6. Note for the TT&C owner

The payload's on-board transfer engine now reads two TT&C parameters every
tick:

* `0x0506 ttc.tm_data_rate` — bytes move at the **active information rate**;
* `0x0501` link status — the transfer **stalls** when the downlink is down and
  resumes when it comes back.

It previously drained a byte counter at a hard-coded 10 Mbps — 78× the
spacecraft's own top tier — with no reference to contact, the PA or the rate
ladder, which is why a "downlink" could complete in eclipse, out of contact,
with the transmitter off.

**This means your `tx_active` gate (T-a) now has a payload consequence.** Once
TM is genuinely gated on the PA, a transfer started with the PA off will sit at
0 % and the operator will see `0x062E transfer_progress` frozen. That is
correct behaviour and is the intended teaching point, but it is a new
interaction between two changes landing in the same batch, so it is worth one
integration check between Phase 3 and Phase 4.

If your beacon design ends up publishing a *beacon* rate into `0x0506` while
the S-band PA is off, tell us — the payload would then drain a bulk image
transfer at 1 kbps rather than stalling, which is not what either of us wants.
The payload reads `0x0501`, not the PA state, so the gate has to be visible
there.

---

## 7. Notes for the textbook owner (Phase 7)

### 7.1 The payload mode table gains a fourth row

Every place the book lists payload modes needs **3 = CALIBRATION**, and the
narrative around COM-011 needs rewriting: the old text has the operator command
IMAGING before requesting a dark frame, which is the defect this phase removed.
The correct dark-frame sequence is now:

```
PAYLOAD_SET_MODE      1   (STANDBY — cooler runs; wait for 0x0610 == 1)
PAYLOAD_SET_SHUTTER   0   (CLOSED — func 119, NEW; verify 0x063C == 0)
PAYLOAD_CAL_LAMP      0   (OFF)
PAYLOAD_START_CALIBRATION 0  (DARK)      -> payload.mode goes to 3
                                          -> 0x0645 says why if it stops
```

and the flat half is `SET_SHUTTER 1`, `CAL_LAMP 1`,
`START_CALIBRATION 1`. Either half completes the other: `0x0646
cal_valid_mask` reaches `0x0F` when every band has both a bias and a gain
coefficient, so DARK-then-FLAT and FLAT-then-DARK both work, and a BOTH run
does both in one command with the on-board sequencer driving the optics across
the phase boundary.

### 7.2 The FPA cooler is **not** a Peltier — and the arithmetic says so

The envelope docstring in `smo_common/config/fpa_envelope.py`, the refusal
messages in both models and (probably) the book all describe a "Peltier
envelope". That description is not consistent with the mission's own numbers,
and this is a genuinely good worked example for the thermal chapter:

* Holding −15 °C against the +12 °C internal sink is a **27 K lift**.
* The FPA node's conductance is `UA = C/τ = 100/120 = 0.833 W/K`, so the
  parasitic heat that must be pumped is `0.833 × 27 = 22.5 W`.
* From `fpa_cooler_power_w = 15 W`, that is a required **COP of 1.50**.
* A single-stage Bi₂Te₃ thermoelectric cannot do it. At its optimum-COP
  current, Goldsmid's standard result gives

  `COP_max = (T_c/ΔT) · (γ − T_h/T_c) / (γ + 1)`, `γ = √(1 + Z·T_m)`

  With `T_c = 258.15 K`, `T_h = 285.15 K`, `T_m = 271.65 K` and the
  `Z·T_m ≈ 0.73` of commercial bismuth telluride at this mean temperature,
  `γ = 1.317` and **`COP_max = 0.88`**. The mission needs 1.7× that. At the
  40 K lift the old model claimed, it is worse still: a single-stage module's
  ΔT_max is only ≈ 70 K *at zero heat load*, where the COP is zero by
  definition.
* But 1.50 is **15.7 % of the Carnot COP** at this lift
  (`T_c/ΔT = 258.15/27 = 9.56`). Miniature Stirling and free-piston space
  coolers reach **10-25 % of Carnot** at near-ambient lifts, and cooled
  small-satellite EO detectors fly exactly that class of machine — the CubeSat
  cryocooler literature (Lockheed Martin Microcryocooler, AIM SX030/SF-100 in
  the 18th International Cryocooler Conference miniature-cryocooler work) is
  Stirling and pulse-tube throughout, not thermoelectric.

**So the hardware is reclassified as a miniature Stirling-class cooler running
at 23 % of Carnot**, and the word "Peltier" is what was wrong, not the mission.
Please correct it wherever the book uses it. The refusal messages in the two
models still say "Peltier envelope" and should be corrected too — they were
left alone in this phase only because changing them would churn a string that
`tests/test_content` may assert on; flag it and the payload owner will change
it in a follow-up.

Sources for the citations above:

* Ferrotec, *Thermoelectric Reference Guide — Mathematical Modeling of
  Modules* (the optimum-current COP expression and the Z·T figure of merit):
  <https://thermal.ferrotec.com/technology/thermoelectric-reference-guide/thermalref11/>
* Ferrotec, *Thermoelectric Reference Guide — Selecting a Module* (ΔT_max and
  the Q_c-vs-ΔT performance curve of a single-stage module):
  <https://thermal.ferrotec.com/technology/thermoelectric-reference-guide/thermalref09/>
* Meerstetter Engineering, *Peltier Element Efficiency* (COP falls steeply with
  ΔT; operating-current guidance):
  <https://www.meerstetter.ch/customer-center/compendium/71-peltier-element-efficiency>
* *Development of a Miniature Cryocooler System for CubeSats*, 18th
  International Cryocooler Conference (AIM SX030 ≈ 0.6 W at 140 K, AIM SF-100
  and the Lockheed Martin Microcryocooler; ~25 W nominal drive, < 200 g):
  <https://cryocooler.org/resources/Documents/C18/044.pdf>
* *Preliminary Design of a Solid-State Thermoelectric Cooling System for Small
  Satellite Sensors* (why thermoelectric is chosen only for modest lifts on
  small satellites):
  <https://www.researchgate.net/publication/315116855_Preliminary_Design_of_a_Solid-State_Thermoelectric_Cooling_System_for_Small_Satellite_Sensors>

### 7.3 Cooling authority is now a curve, and it is teachable

`fpa_cooler_max_lift_k` was a flat 40 K with no coupling whatever to
`fpa_cooler_power_w`, so the cooler had exactly as much authority at a 39 K
lift as at a 1 K lift and "the cooler is struggling" was not a state the
simulator could enter. It is now the *ceiling*; the achievable lift is solved
from the heat balance every tick:

```
Q_c(ΔT) = η · P · T_c/ΔT           (η = fraction of Carnot, 0.23)
Q_c(ΔT) = UA·ΔT + Q_load           (steady state, T_c = T_h − ΔT)
=>  UA·ΔT² + (Q_load + η·P)·ΔT − η·P·T_h = 0
```

Measured on the fixed model:

| sink | load | achievable lift | achievable floor | −15 °C? | −20 °C? |
|---|---|---|---|---|---|
| +12 °C | idle | 32.35 K | −20.35 °C | yes, 5.4 K spare | yes, 0.35 K spare |
| +12 °C | imaging (2 W) | 31.24 K | −19.24 °C | yes | **no** |
| +20 °C | idle | 32.83 K | −12.83 °C | **no** | no |
| +10 °C | idle | 32.23 K | −22.23 °C | yes | yes |

The nominal −15 °C setpoint keeps ~5 K of margin, so **the shipped mission is
unchanged** — the previously-measured "−15.01 °C, σ 0.138 K" steady state still
holds and every derived limit band in `fpa_envelope.py` is untouched. What
changed is that the margin is finite, spendable, and visible at
`0x064A`/`0x064B`.

Two lessons this buys, both of which the book can now set as exercises:

* a **cold-setpoint** campaign at −20 °C holds while idle and loses regulation
  the moment imaging starts, and `0x0425` predicts it before it happens;
* a **warm-sink** case takes the nominal −15 °C setpoint away entirely and FPA
  READY will not come up, with `cooler_margin_k` giving the reason rather than
  the operator guessing.

The old tcs.yaml claim "cooler ON setpoint −20 C node −19.99 C" is still true
*at idle*, but is no longer true under imaging load. The measurement block in
`subsystems/tcs.yaml` has been updated accordingly.

### 7.4 Images do not appear on their own — and now that is actually true

NOM-002's teaching note said so; the model contradicted it. IMAGING mode
accreted `data_rate_mbps` worth of volume every tick with no capture, no
catalogue entry and no product: **measured 9.735 MB/s, filling the 20 GB store
in ~33 minutes from empty with zero `PAYLOAD_CAPTURE` commands**, against
1.14-1.37 MB for one real product — a ratio of about 9000:1 of phantom to real
data, which destroyed the pass-planning arithmetic the whole link-rate ladder
was built on.

Readout now runs only while a commanded strip is being scanned
(`lines / line_rate_hz`; 2048 lines at 500 Hz = 4.1 s), the volume is booked
once by `capture` itself, and `0x0649 readout_active` shows it. Any book text
that describes a continuous imaging data rate needs rewriting: `0x0607` and
`0x0608` are now zero except during those few seconds.

### 7.5 Image bytes exist now

Section 6.7's S13 workflow can be taught for real. Each product carries a seed;
S13.3 serves bytes derived from it (a keyed BLAKE2b stream), the whole product
CRC-32s to the value S13.1 reports, and a `image_corrupt`-injected product
serves damaged bytes against a clean recorded checksum — so "verify the
downlink" is a real exercise rather than a description. S13.5 on a completed
session deletes the product and returns its storage.

---

## 8. Requests for the `smo-common` owner

### 8.1 Four failure codes

`payload_basic` now refuses calibration for four distinct physical reasons and
`capture` for a fifth. They are currently reported with the generic
`0x0005` / `0x0007` because `0x0021`-`0x0023` in
`smo_common.protocol.pus_services.FAILURE_CODES` already mean the three capture
pointing refusals, and reusing them would make the console print the wrong
sentence. The specific reason travels in the `code` string and the message, but
a dedicated code would render properly in the failure vocabulary:

```python
    0x0024: "Calibration refused: not permitted in IMAGING",
    0x0025: "Calibration refused: FPA not thermally ready",
    0x0026: "Calibration refused: shutter in the wrong position",
    0x0027: "Calibration refused: calibration lamp in the wrong state",
    0x0028: "Capture refused: shutter closed",
```

`tests/test_content/test_failure_code_vocabulary.py` enforces that every code a
model emits has an entry, so these must land together with the model change
that starts using them.

### 8.2 Three TCS schema fields

`TCSConfig` is a `StrictModel`, so the cooler-efficiency parameters cannot be
put in `subsystems/tcs.yaml` — the loader would reject the file. The model
reads them with `config.get(...)` and falls back to its defaults, which are the
live values. Please declare:

```python
    fpa_cooler_power_w: float = 15.0          # matches subsystems/payload.yaml
    fpa_cooler_carnot_fraction: float = 0.23  # miniature Stirling class
    fpa_imaging_load_w: float = 2.0           # detector/ROIC dissipation
```

Until then the derivation lives in `tcs_basic._fpa_achievable_lift_k`'s
docstring and in a comment block in `subsystems/tcs.yaml`, and the numbers are
not tunable from configuration — which is a shame, because the Carnot fraction
is exactly the knob an instructor would want for a "the cooler is degrading"
scenario.

---

## 9. Things this phase deliberately did NOT do

* **Did not touch `smo-mcs`.** Everything above is a requirement, not a change.
* **Did not renumber the payload mode enum.** See §4.
* **Did not change the FPA envelope** (`fpa_envelope.py`, the derived limit
  bands, `imaging_max_c`, the readiness rule). The cooler-authority work was
  sized so the shipped −15 °C behaviour is unchanged, precisely so that
  `test_fpa_envelope_single_source.py` and every band derived from it stay
  valid.
* **Did not correct the procedure cards.** COM-009 (cooler activation via
  func 43 — now a path that works), COM-011 (the dark-frame sequence, §7.1) and
  NOM-002 (the "images do not appear on their own" note, now true) all need
  editing, and procedure text is Phase 5 by the coherence review's ordering.
* **Did not rewrite the "Peltier envelope" strings** in the two models' refusal
  messages. See §7.2.
