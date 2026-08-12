# TT&C display requirements and cross-lane requests — 2026-08b Phase 3

**From:** the TT&C owner (Phase 3 — `tx_active` gate, UHF beacon transmitter,
EIRP from PA power)
**To:** the single MCS owner (Phase 6), plus the EPS, rfsim, manual and
planner owners for the items in §4.

Per COHERENCE_REVIEW §2.5, model owners do not edit `smo-mcs/static/index.html`.
Everything the console needs from this phase is written here instead.

---

## 0. Why this list exists at all

Franz's report was *"we can power off the transmitter but still receive
telemetry in the MCS"*. The model half of that is fixed (see §1). The console
half is not, and it is the half an operator actually meets:

**`configs/eosat1/mcs/displays.yaml`'s TT&C pages contain not one of the gate
parameters.** Not `pa_on` (0x0516), not `antenna_deployed` (0x0520), not
`beacon_mode` (0x0521), not `downlink_mode` (0x053C), and of course none of
the parameters this phase adds. The pages show link status, RSSI, margin,
rate and temperatures — everything about the link's *quality* and nothing
about its *permission*. An operator looking at that page while the PA is off
sees a healthy link and no reason for the silence, which is exactly the
experience Franz described.

(`displays.yaml` is dead config today — COHERENCE_REVIEW §5 keeps it that way
until the interface work lands. It is listed here so the gap is recorded
against the file that will eventually drive the UI, not just against the
hard-coded HTML.)

---

## 1. What the model now publishes

New parameters, all written every tick by `TTCBasicModel.tick`:

| ID | Name | Type | Meaning |
|---|---|---|---|
| **0x0525** | `ttc.tx_active` | 0/1 | **S-band prime chain radiating.** The gate on every TM packet. In HK **SID 6** (periodic) |
| 0x052B | `ttc.tx_gate_status` | bitmask | Why. bit0 PA on, bit1 EPS `ttc_tx` line hot, bit2 station in view, bit3 S-band radiating, bit4 UHF beacon commanded on, bit5 UHF beacon radiating, bit6 S-band antenna deployed, bit7 beacon *mode* off. `0xFF` = everything nominal |
| 0x052C | `ttc.eirp_dbw` | dBW | Live EIRP at the commanded PA power |
| 0x0526 | `ttc.beacon_tx_on` | 0/1 | UHF beacon transmitter commanded on |
| 0x0527 | `ttc.beacon_active` | 0/1 | UHF beacon radiating now |
| 0x0528 | `ttc.beacon_link_margin` | dB | Its own budget, ~30 dB at 5° |
| 0x0529 | `ttc.beacon_dl_freq` | MHz | 437.5 |
| 0x052A | `ttc.beacon_tx_power` | W | 1.0 RF, 0.0 when commanded off |

And in `get_state_summary()['ttc']`: `pa_on`, `tx_active`, `tx_gate_status`,
`eirp_dbw`, `antenna_deployed`, `beacon_mode`, `beacon_tx_on`,
`beacon_active`, `beacon_margin_dB`, `beacon_dl_freq_mhz`,
`beacon_tx_power_W`, `ttc_tx_line_on`.

**Note for whoever owns `_demotion_reason` in the MCS:** its antenna branch
was dead code — it tested a snapshot field the engine never published.
`antenna_deployed` is now in the `ttc` block, so that branch can start
working.

---

## 2. Required displays

### 2.1 DOWNLINK status row — the headline request

One row, on the TT&C page and repeated on the summary bar, rendering exactly
one of these five states. Derive from `tx_gate_status` (0x052B) so the reason
comes from the spacecraft rather than being re-derived on the ground:

| Render | Condition (bits of 0x052B) | Colour |
|---|---|---|
| `TRANSMITTING` | bit3 set | green |
| `INHIBITED (PA OFF)` | bit3 clear **and** bit0 clear | red |
| `INHIBITED (ttc_tx LINE OFF)` | bit3 clear, bit0 set, bit1 clear | red |
| `NO CONTACT` | bit3 clear, bits 0 and 1 set, bit2 clear | grey |
| `BEACON ONLY` | bit3 clear **and** bit5 set | amber, and takes precedence over the two INHIBITED rows when both apply |

Test the order: PA-off *and* beacon-up is the common case after an FDIR
shutdown, and the operator needs to be told both — suggested rendering
`INHIBITED (PA OFF) — BEACON ONLY`.

Rationale: this single row is the answer to Franz's question. Every other
item below is supporting detail.

### 2.2 UHF beacon transmitter indicator

A distinct indicator, visually separated from the S-band block, because it is
a separate radio. Minimum: `beacon_active` as a lamp, with
`beacon_dl_freq_mhz` / `beacon_tx_power_W` / `beacon_margin_dB` beside it.
It must **not** be greyed out when the S-band block goes red — that
independence is the whole teaching point of ruling R2, and a UI that dims the
beacon with the prime chain would teach the opposite of the truth.

### 2.3 `beacon_mode` (0x0521) — displayed nowhere today

`beacon_mode` gates **both** operational tiers: while it is latched, both LOW
and HIGH are refused with error 0x0007, and `TTC_SET_DATA_RATE` returns *"TM
rate is forced to the beacon tier in beacon mode"*. It survives an application
boot (`OBC_BOOT_APP` does not clear it — see LINK_BUDGET_AND_RATES §6.3), so a
spacecraft can sit in it for an entire session. It appears on no display.

Put it on the TT&C page next to the tier readouts, and — importantly — label
it so it cannot be confused with `beacon_tx_on`. Suggested labels:
`TM tier forced (beacon mode)` for 0x0521, `UHF beacon TX` for 0x0526.

### 2.4 The EPS `ttc_tx` line, on the TT&C tab

`eps.pl_ttc_tx` (0x0112) exists and is displayed on the EPS page. It also
determines whether the TT&C subsystem can transmit, and an operator debugging
a dead downlink should not have to change tab to find out. Mirror it on the
TT&C tab (the engine snapshot now carries it as `ttc.ttc_tx_line_on`), with a
note that the auto-TX hold re-energises it for 300 s after every accepted
telecommand.

### 2.5 Supporting rows

* `tx_active` (0x0525) as a lamp, `eirp_dbw` (0x052C) as a value next to
  `tx_fwd_power` (0x050D) — the pair makes `TTC_SET_TX_POWER` visibly real.
* `pa_on` (0x0516) and `antenna_deployed` (0x0520) as lamps. Both existed and
  neither was displayed.
* Two new commands for the command panel: `TTC_BEACON_TX_ON` (S8 func 97) and
  `TTC_BEACON_TX_OFF` (func 98). Quick-command entries are already in
  `tc_catalog.yaml` under group TTC.

---

## 3. Back-channel and archive notes (no display work, but the MCS owner
should know)

**3.1 `/api/state` bypasses the RF chain, and now bypasses a real gate.**
The MCS reads the engine's state summary over HTTP, not through the RF
pipeline. That was always a fidelity shortcut; with the `tx_active` gate live
it becomes a visible inconsistency: with the PA off, the TM packet stream
stops dead while `/api/state` keeps `tm_tier`, `downlink_mode`,
`elevation_deg`, `rssi_dbm` and the rest updating once a second. An operator
watching the tier field will see a live number for a radio that is not
transmitting.

Two acceptable resolutions, MCS owner's call:
* render every `ttc` field except the gate set as **stale/greyed** whenever
  `tx_active` is 0 (cheap, honest, no protocol change); or
* badge the whole panel `BACK-CHANNEL — NOT VIA RF` when `tx_active` is 0.

Not resolvable in this phase: the back-channel is out of the TT&C lane and
closing it properly is the `test_back_channel_closed.py` work.

**3.2 The dump archive bypass is deliberate — leave it.** S13/S15 dumps are
written to `workspace/dumps/` regardless of link state (defect #90, knowingly
accepted so a classroom can inspect dump content without a pass). It is
unaffected by this phase and should stay as it is; noted here only so nobody
"fixes" it while implementing §2.1.

---

## 4. Cross-lane requests

### 4.1 EPS owner — no change needed, and here is the arithmetic

The UHF beacon's power was checked against the re-derived sheet and **needs no
`eps.yaml` change**:

* keying draw 1.8 W DC (EnduroSat UHF Type II at 30 dBm);
* duty = 29-byte SID 11 at 1000 bps = 0.23 s in every 30 s = **0.75 %**;
* orbit-average TX draw = **13 mW**;
* `POWER_SIZING.md` §3 already books `transponder_rx_power_w: 1.5` as
  "EnduroSat idle/Rx 1.2 W + **~0.3 W UHF beacon Rx**", which covers the
  standby (0.2 W) and the keying (13 mW) with two decades of headroom.

The beacon is billed to `ttc_rx`, the **non-switchable** line, deliberately: a
lifeline an operator can de-energise with a power-line command is not a
lifeline.

**One real request:** `eps._state.transponder_tx` is a dead flag. It is
written at `eps_basic.py:860, 956, 967, 1258, 1413, 1416` and **read nowhere
in the repo**, and the engine's auto-TX hold (`engine.py:1469`) writes
`power_lines["ttc_tx"]` without updating it, so it is not merely unused but
unreliable. The TT&C model now reads the live line state via 0x0112 instead.
Please **delete `transponder_tx`** and the `set_transponder_tx` command that
sets it (`service_dispatch.py:987` routes to it), or make it the single
source of truth — Franz's instruction was explicitly "do not leave a dead
flag", and it is an EPS-lane file.

### 4.2 rfsim owner — the absolute EIRP is 7 dB optimistic

`ttc.yaml`'s `eirp_dbw: 10.0` plus `sc_gain_dbi: 3.0` is 13 dBW of EIRP,
i.e. 20 W radiated. The researched radio (POWER_SIZING §2.3, EnduroSat S-band,
"24 to 33 dBm") produces **2 W RF = 3 dBW**, which with the 3 dBi patch is
**6 dBW**. Every margin in LINK_BUDGET_AND_RATES §4 is therefore ~7 dB
optimistic.

Not corrected in this phase because `tests/test_rfsim/test_rate_ladder_pipeline.py`
asserts `rfsim.yaml link_budget.eirp_dbw == ttc.yaml eirp_dbw`, and
`rfsim.yaml` is another owner's file. It is a **two-file, one-commit change**:
set both to `3.0`. Nothing else needs touching — the TT&C model now derives
the live EIRP as `eirp_dbw + 10·log₁₀(P / pa_ref_power_w)`, so the reference
moving takes the whole ladder with it. Every rung still closes by 26 dB after
the correction.

### 4.3 Manual owner — the SSPA watt table is not a CubeSat radio

`TTC_SET_TX_POWER`'s levels are 1 / 2 / 5 W RF. Five watts RF out of an 11 W
DC line is 45 % PAE; real S-band SSPAs manage 20–35 %, and the EnduroSat part
tops out at 33 dBm = 2 W. The researched ladder is **0.25 / 1.0 / 2.0 W**.

Not changed here because the table is quoted verbatim in
`configs/eosat1/manual/09_command_reference.md`, which is *generated* from
`tc_catalog.yaml` and asserted by
`tests/test_content/test_generated_command_reference.py` ("1.0 W RF",
"5.0 W RF"). Retuning it is: edit `pa_low_power_w` / `pa_nominal_power_w` /
`pa_max_power_w` in `ttc.yaml` (already config, already the source of the
level table), edit the enum labels in `tc_catalog.yaml`, regenerate the
manual, update the two content assertions. The link budget then follows
automatically because the EIRP is derived from those watts.

### 4.4 Planner owner — an unrelated pre-existing failure, for the record

`tests/test_planner/test_rate_ladder_budget.py::
test_housekeeping_is_a_channel_cost_at_the_routine_rung` asserts
`hk_stream_bps == 1567.0 ± 2`. It reads **1605.7** on the current tree. Phases
1–2 grew SID 1 (51→52 params), SID 2 (71→81) and SID 4 (26→27); Phase 3 adds
1 byte to SID 6, worth **+1.15 bps** of the +38.7 bps delta. The assertion
needs re-baselining against whatever the batch settles on, and
LINK_BUDGET_AND_RATES §2.2's HK table (still quoting the pre-batch parameter
counts) needs regenerating with it.

---

## 5. One-paragraph summary for the console

> The TT&C page must be able to answer "why is there no telemetry?" without
> the operator opening another tab or another document. Today it answers
> "the link is fine", which is true of the link and false of the spacecraft.
> One status row driven by `ttc.tx_gate_status`, one beacon indicator that
> stays lit when the S-band block goes red, and `beacon_mode` shown
> somewhere at all, would close it.
