# NOM-003: Data Downlink
**Subsystem:** PAYLOAD / TTC
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Downlink stored science imagery and ancillary data from the onboard mass memory
to the ground station during an AOS window. This procedure raises the TM rate
ladder to HIGH, opens an S13 large-data transfer against a CATALOGUED image
product, walks the segments down, closes the transfer, and returns the link to
LOW before LOS.

> **Rewritten 2026-08b.** The previous revision commanded
> `PAYLOAD_SET_MODE(mode=3)` and called it "PLAYBACK". There has never been a
> PLAYBACK mode: `PAYLOAD_SET_MODE`'s range was 0-2 (mode 3 is now CALIBRATION,
> added in Phase 4), so the command was REJECTED and the whole downlink
> depended on a transition that could not happen. The real bulk-data path is
> **Service 13**, and since Phase 4 it is bound to real image bytes with a
> CRC-32 the ground can check.

## Prerequisites
- [ ] NOM-001 Pass Startup completed with all-GO declaration
- [ ] TTC link established: `ttc.link_status` (0x0501) = 2 (LOCKED)
- [ ] Link margin confirmed: `ttc.link_margin` (0x0503) > 3.0 dB
- [ ] Payload in STANDBY mode: `payload.mode` (0x0600) = 1
- [ ] Onboard storage contains PRODUCTS: `payload.product_count` (0x0648) > 0.
      `payload.store_used` (0x0604) is not the test — the store ships about 20 %
      full of non-product fill, so it is > 0 % on an empty spacecraft.
- [ ] Ground station data recorder armed and receiving

## Procedure Steps

### Step 1 --- Confirm Link Quality
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- request TT&C housekeeping
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 5 s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 5 s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB within 5 s
**GO/NO-GO:** If link margin <= 3.0 dB, HOLD. If margin does not improve within
30 s, attempt `TTC_SWITCH_REDUNDANT`. If still insufficient, defer downlink.

### Step 2 --- Record Baseline Storage State
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) --- request Payload housekeeping
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 5 s
**Note:** Record `payload.product_mb` (0x0647) as V0 and `payload.product_count`
(0x0648) as N0. These count CATALOGUED products; `payload.store_used` (0x0604)
also carries the store's baseline fill and moves for reasons unrelated to imagery.
**TC:** `PAYLOAD_GET_IMAGE_CATALOG` (Service 8, Subtype 1, func_id 32) — on-board catalogue
**Note:** Since Phase 4 this answers with a TM(8,2) catalogue report: a header of
shown(u16) held(u16) product_mb_x100(u32) free_mb_x100(u32), then one 20-octet
record per product — scene_id(u16), lat_x1e4(i32), lon_x1e4(i32), size_kb(u32),
lines(u16), quality_pct(u8), status(u8), flags(u8), segment(u8). Pick the
`scene_id` to downlink from it. `status` 0 = OK, 1 = PARTIAL, 2 = CORRUPT;
`flags` bit 0 = the strip was flown on a degraded (3-wheel) FINE set.

### Step 3 --- Raise the Link to HIGH
**TC:** `TTC_SET_DOWNLINK_MODE(on=1)` (Service 8, Subtype 1, func_id 79) --- enter
DOWNLINK/dump mode, the mode gate on the HIGH tier
**TC:** `TTC_SET_DATA_RATE(rate=1)` (Service 8, Subtype 1, func_id 65) --- raise the TM
rate ladder to HIGH (128 kbps QPSK RS(255,223)). The ON-BOARD gates are exactly:
antenna deployed + DOWNLINK mode on + beacon-mode latch clear (each unmet gate
is an S1.8 with error 0x0007 naming it). There is NO on-board elevation gate
(2026-08c): the 10-deg rule on HIGH is GROUND arm-through policy --- the MCS
warns from its own contact geometry when HIGH is commanded below 10 deg and
the operator must explicitly confirm past the warning; the planner schedules
HIGH dumps above it
**Verify:** `ttc.tm_data_rate` (0x0506) = 128000 bps and `ttc.tm_tier` (0x0539) =
`ttc.tm_tier_cmd` (0x053A) = 2 within 10 s
**Note:** Flown order (2026-08c): HK(6) link check -> catalogue -> DOWNLINK
mode + HIGH (with the ground arm-through above) -> S13.1 open (blocks + CRC)
-> segments -> status -> end -> back to LOW. That is this card's Steps 1-6.
**Note:** Do NOT command a payload mode here. There is no PLAYBACK mode and
`PAYLOAD_SET_MODE(mode=3)` is CALIBRATION since Phase 4 — sending it would start
a calibration in the middle of a downlink pass. The payload stays in STANDBY for
the whole session; S13 reads from the store, not from an instrument mode.
**GO/NO-GO:** HIGH tier active, DOWNLINK mode on, payload in STANDBY

### Step 3a --- Open the S13 Transfer
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- TT&C housekeeping
**TC:** `S13_CREATE_TRANSFER(scene_id=N, size_mb=0)` (Service 13, Subtype 1) --- open a
large-data transfer against the scene chosen from the catalogue in Step 2
**Note:** The TM(13,2) reply carries the transfer id followed by
`total_blocks(u32) + crc32(u32)`. Record BOTH: the block count sizes the session
and the CRC-32 is what the ground checks the reassembled product against.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) --- payload housekeeping
**Verify:** `payload.transfer_active == 1` (0x062D, timeout 15 s)
**GO/NO-GO:** Transfer open, block count and CRC-32 recorded

### Step 4 --- Walk the Segments and Monitor Progress
**TC:** `S13_DOWNLOAD_SEGMENT(transfer_id=T, segment_num=K)` (Service 13, Subtype 3) --- request
the next block; repeat for K = 0 .. total_blocks-1
**TC:** `S13_REPORT_STATUS(transfer_id=T)` (Service 13, Subtype 9) --- transfer status, every 60 s
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) --- poll every 60 s
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- poll every 60 s
**Verify:** `payload.transfer_progress` (0x062E) increasing over successive polls
**Verify:** `ttc.link_status == 2` (0x0501, timeout 60 s) --- LOCKED maintained
**Verify:** `ttc.link_margin` (0x0503) > 2.0 dB throughout the session
**Verify:** `ttc.rssi` (0x0502) > -105 dBm throughout the session
**Note:** Since Phase 4 the on-board transfer engine drains at the ACTIVE
information rate `ttc.tm_data_rate` (0x0506) and STALLS when `ttc.link_status`
(0x0501) leaves LOCKED, resuming when it returns. A frozen
`payload.transfer_progress` therefore means the downlink is down — most often
the PA, since `ttc.tx_active` (0x0525) is now a real gate. It used to drain a
counter at a hard-coded 10 Mbps regardless of contact, the PA or the ladder, so
a "downlink" could complete in eclipse with the transmitter off.
**Note:** If `ttc.link_margin` drops below 2.0 dB, stop requesting segments and
go to Step 6; the transfer can be resumed at the next AOS from the same
`transfer_id`.

### Step 5a --- Verify the Product Checksum
**Action:** CRC-32 the reassembled product on the ground and compare with the
value TM(13,2) reported in Step 3a.
**Note:** This is a real check, not a formality. An `image_corrupt`-injected
product serves DAMAGED bytes against the CLEAN checksum the instrument computed
at capture, so a corrupted image now genuinely fails verification instead of
being byte-for-byte identical to a good one.
**GO/NO-GO:** CHECKSUM OK --- the product downlinked intact

### Step 5 --- Verify Ground Reception
**Action:** Confirm with ground station operator that frames are being received
and archived without errors.
**Verify:** Ground station frame error rate < 1.0E-6
**Note:** If ground station reports loss of frame sync, pause and re-evaluate
link conditions before continuing.

### Step 6 --- Terminate Downlink Session and Release the HIGH Tier
**TC:** `S13_END_TRANSFER(transfer_id=T)` (Service 13, Subtype 5) --- close the session
**Note:** Closing a FULLY-dumped session DELETES the product and returns its
storage; a partial session leaves the product on board for the next pass.
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- TT&C housekeeping
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) --- payload housekeeping
**Verify:** `payload.transfer_active == 0` (0x062D, timeout 15 s)
**Verify:** `payload.mode == 1` (0x0600, timeout 15 s) --- STANDBY throughout
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) --- back to LOW
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79)
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps within 10 s
**Verify:** `ttc.downlink_mode` (0x053C) = 0
**Note:** HIGH is released as deliberately as it was taken. Leaving the spacecraft
commanded to HIGH with DOWNLINK mode latched is leaving a gate open — routine
operations belong at LOW.
**Note:** Record final `payload.product_mb` (0x0647) as Vf and
`payload.product_count` (0x0648) as Nf.
**Note:** Data volume released = V0 - Vf MB, products released = N0 - Nf.

### Step 7 --- Post-Downlink Assessment
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- final TT&C status
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) --- final Payload status
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY confirmed)
**Log:** Record downlink duration, data volume transferred, average link margin,
and any frame-sync interruptions in the pass log.

## Off-Nominal Handling
- If `S13_CREATE_TRANSFER` is refused: the `scene_id` is not in the catalogue.
  Re-run `PAYLOAD_GET_IMAGE_CATALOG` (func 32) and pick a scene that is.
- If `payload.transfer_progress` (0x062E) is frozen: the downlink is not
  radiating. Check `ttc.tx_active` (0x0525) and, if it is 0,
  `ttc.tx_gate_status` (0x052B) for which permission is missing — bit0 PA,
  bit1 `ttc_tx` line, bit2 station in view, bit6 antenna. The transfer resumes
  on its own once the link returns.
- If the reassembled product fails its CRC-32: the product is corrupt on board,
  not corrupted in transit — the checksum was computed at capture. Record the
  `scene_id`, do NOT `S13_END_TRANSFER` (which would delete it), and raise a
  payload anomaly.
- If `ttc.link_status` drops to 0 (NO_LINK) or 1 (ACQUIRING) during transfer: Attempt
  `TTC_SWITCH_REDUNDANT`. If link re-established, resume from current point.
  If not, wait and retry at next AOS.
- If `ttc.link_margin` < 2.0 dB sustained: Step the ladder back down to LOW via
  `TTC_SET_DATA_RATE(rate=0)` (func_id 65) and leave DOWNLINK mode with
  `TTC_SET_DOWNLINK_MODE(on=0)` (func_id 79). At LOW the remaining product needs
  eight times the contact time, so re-plan the dump onto a higher pass rather than
  grinding it out at 16 kbps.
- If LOS approaching (< 2 min remaining): Terminate downlink gracefully. Remaining
  data will be scheduled for next available pass.

## Post-Conditions
- [ ] Payload returned to STANDBY mode
- [ ] Storage usage reduced (Sf < S0)
- [ ] Ground station confirms data archived without corruption
- [ ] TTC link maintained throughout (or gracefully recovered)
- [ ] Pass log updated with downlink session summary
