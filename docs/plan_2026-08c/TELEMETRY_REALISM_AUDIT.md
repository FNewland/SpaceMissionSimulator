# Telemetry realism audit — what a real EO-Sat-1 could actually show

**Raised 2026-08-20:** *"there are a little over 160 parameters [on the AOCS page]. There is
no way all of these can be telemetered from an actual spacecraft ... and why would an actual
spacecraft telemeter a parameter called `aocs.fidelity_mode`? ... Can you go through all the
subsystems and do a sanity check on what is visible in the MCS and radio and planning system
windows — these should ONLY be things that would be visible from a real spacecraft through
telemetry, or through a real rf link, or calculated on the ground through the ground software."*

Scope as asked: **every subsystem except AOCS**, plus the rfsim and planner surfaces. AOCS is
listed at the end only as the pattern, since you had already found it.

**Nothing has been changed.** Most of these are design rulings about your spacecraft, not
defects with an obvious fix, and deleting forty parameters from a telemetry dictionary on my
own judgement would be the wrong move. What follows is the evidence and a proposed
disposition for each; the mechanism to act on it is sketched at the end.

---

## The test applied

A parameter earns its place on an operator display only if it is one of:

| Class | Meaning |
|---|---|
| `REAL_HK` | A real spacecraft would downlink this routinely in a housekeeping structure. |
| `REAL_ON_REQUEST` | Plausible on real hardware, but as a diagnostic read, not routine HK. |
| `GROUND_COMPUTED` | The ground derives it. The spacecraft should not be reporting it. |
| `SIM_ONLY` | A model artefact that could not exist on real hardware. **The defect class.** |

Two failure modes matter more than the others, because both are invisible on the console:

1. **Wrong side of the link** — the spacecraft reporting something only the *ground* can
   measure (its own downlink Eb/N0, its elevation above a station, its slant range).
2. **The cause telemetered as the effect** — a parameter written directly from a
   failure-injection handler. The operator reads the answer instead of diagnosing it, and the
   exercise is destroyed without anyone noticing.

---

## Headline counts

| Subsystem | Params | SIM_ONLY | GROUND_COMPUTED in HK | Notes |
|---|---:|---:|---:|---|
| EPS | 75 | 6 | 9 | Worst duplication; SID 1 is 54 params at **1 Hz** |
| TCS | 40 | 15 | 4 | Six phantom heater zones, two invented radiator sensors |
| TTC | 45 | 7 | 6 | **The worst subsystem** — ground measurements in spacecraft HK |
| PROP | 28 | 5 | 3 | Injected leak rate and thruster degradation telemetered |
| PAYLOAD | 61 | 7 | 4 | Instrument grading its own image quality |
| OBDH | 36 | **0** | 1 | Clean. The clock block is exemplary — see below |
| FDIR | 4 | **0** | 0 | Clean provenance, but all three counters are misnamed |
| spacecraft / sys | 6 | 0 | 0 | One fabricated stub (`sys.mission_flag`) |

OBDH and FDIR came back clean. That is worth saying plainly: the pattern is not everywhere.

---

## Tier 1 — the spacecraft reporting what only the ground can know

These are the clearest defects, and the least defensible: no amount of instrument design would
give a satellite these numbers.

### TTC — six parameters in HK SID 6, every 8 s

`ttc_basic.py` computes the downlink budget *at the ground station* and then telemeters the
result back as if the spacecraft had measured it. Verified directly:

```python
# ttc_basic.py ~1242
gt_eff = self._gs_gt - self._gt_derate_db(s.elevation_deg)      # GROUND STATION G/T
c_n0   = (s.rssi_dbm - 30.0) - losses + gt_eff + 228.6
gs_penalty = (s.gs_lna_degrade_db + s.gs_antenna_mispoint_db
              + s.gs_feed_loss_db + s.gs_rfi_db
              + s.gs_hpa_degrade_db + s.gs_ref_osc_drift_db
              + s.gs_tracking_loss_db)                          # INJECTED GROUND FAULTS
s.eb_n0 = ... - gs_penalty
```

| ID | Name | Why it cannot exist |
|---|---|---|
| **0x0519** | `ttc.eb_n0` | Computed with the ground station's G/T and the instructor's injected *ground-segment* failures. |
| **0x050C** | `ttc.ber` | The ground demodulator's error rate, inverted from the above. |
| **0x0503** | `ttc.link_margin` | Downlink margin against a ground budget. Also on the system overview. |
| **0x0502** | `ttc.rssi` | Named like a legitimate *uplink* RSSI, computed as the *downlink* level at the ground antenna. Doubly misleading: it displaces the honest parameter a real transponder does report. |
| **0x050A** | `ttc.contact_elevation` | Elevation above a ground station. The model's own comment says *"elevation is a ground concept"* — and telemeters it every 8 s. |
| **0x0509** | `ttc.range_km` | Slant range — a ground ranging / OD product. |

Two consequences worth separating out:

- **On-board FDIR monitors these.** `monitoring/s12_definitions.yaml` defines S12 limit checks
  on `ttc.rssi`, `ttc.link_margin`, `ttc.eb_n0` and `ttc.ber`. Even if you keep the parameters
  for teaching, on-board autonomy cannot monitor a quantity measured on the ground.
- **`ttc.gs_equipment_status` (0x0524)** is a bitmask of the *ground station's* antenna, LNA,
  HPA and reference-oscillator health, built from injected ground failures. There is also an
  uncatalogued `0x0538` carrying the aggregate injected ground penalty in dB — invisible today
  only because it is undocumented.

**Proposed disposition:** move the whole set to ground-derived parameters computed by the MCS
from the link budget it already has (`/api/displays/link-prediction` exists and does exactly
this arithmetic). The spacecraft keeps `tx_fwd_power`, `pa_temp`, `pa_on`, the uplink lock
ladder, `cmd_rx_count` — all genuine. The operator then does what a real operator does:
compares the *predicted* link against the station's *measured* Eb/N0.

---

## Tier 2 — the cause telemetered as the effect

Each of these is written directly by a failure-injection handler. Verified:

| ID | Name | Evidence |
|---|---|---|
| **0x0B16** | `prop.leak_rate` | `prop_basic.py:823` sets it from the injection; `:562` publishes it verbatim. No spacecraft has a leak-rate sensor. The description itself concedes the real signal is the 0x0B01 pressure trend. |
| **0x0124/5** | `eps.sa_a/b_degradation` | `eps_basic.py:1552` — `1.0 - magnitude`, straight from the injected fault. |
| **0x0B09** | `prop.thrust_est` | Multiplied by the injected `perf_factor`, so it reveals a degradation no real thrust estimate could see. |
| **0x060D** | `payload.last_scene_quality` | Drawn from the injected fault flags (`rng.uniform(10,30)` if corrupt…). An imager cannot grade its own product; that is a Level-1 ground verdict. |
| **0x0616** | `payload.snr` | Built from truth pointing error and the injected FPA-degradation factor. Also has an S12 monitor on it. |

**Proposed disposition:** these belong on the instructor console, next to the injection that
created them — not in the telemetry dictionary. The observable effects (tank pressure trend,
generated power trend, image artefacts) are already modelled and are what the exercise is
actually about.

---

## Tier 3 — model internals wearing a parameter ID

| ID | Name | What it actually is |
|---|---|---|
| **0x0B19** | `prop.orbit_epoch_version` | A counter on the *simulator's* propagator object, described as an "MCS cache key". The exact species as `aocs.fidelity_mode`. |
| **0x0B1A** | `prop.sma_km` | The propagator's truth semi-major axis. SMA is *the* orbit-determination product; propulsion reporting it is backwards. |
| **0x0B17** | `prop.thrust_offset_torque` | Self-described as the value "fed to AOCS" — the model's inter-model coupling channel. |
| **0x0626** | `payload.att_quality_factor` | A hand-tuned dimensionless coefficient whose only role is to scale the model's SNR. |
| **0x0122** | `eps.mppt_efficiency` | A constant config coefficient (0.97) read out of the generation equation — downlinked 86 400 times a day. |
| **0x0123** | `eps.sa_age_factor` | The ageing *law's* output — truth about degradation. A spacecraft infers this from trended generation; it cannot measure it. |
| **0x0420/1** | `tcs.temp_radiator_n/s` | `temp_panel_mz - 4.0` and `- 5.0`. Two "independent" sensors that are one sensor and two literals. |
| **0x0412-7** | `tcs.temp_panel_*_ilm` | Six IDs computed by an expression byte-identical to 0x0400-0x0405. Six channels that can never differ from six others. |
| **0x0320-6** | `tcs.htr_duty_z1..z6` + total | Six heater circuits that **do not exist** — `tcs.yaml` defines three (battery, OBC, thruster). Nothing writes them; S20 will serve them anyway. |
| **0x0130** | `eps.sa_mz_current` | A shunt on a face `eps.yaml` gives zero cell area ("imager aperture + radiator, no cells"). |

---

## Tier 4 — ground arithmetic in spacecraft housekeeping

Not impossible, just misplaced — and they cost link budget. The clearest are values computed
from *other parameters in the same packet*:

- `eps.power_margin_w` (0x0135) = `power_gen - power_cons`, both in the same packet — **and
  duplicated byte-for-byte at 0x013B**.
- `eps.bat_dod` (0x0120) = `100 - bat_soc`; `eps.battery_health_pct` (0x0136) = `100 - dod`.
  Three copies of state of charge, one of them mislabelled "health".
- `eps.per_switch_state` (0x0131) — a bitmask rebuilt from the eight booleans beside it.
- `eps.eclipse_active` (0x013A) — the same boolean as 0x0108 on a second SID.
- `eps.subsys_power_*` (0x013C-41) — re-bucketing of line currents already downlinked. A shunt
  measures a *line*, not a subsystem; `subsys_power_eps` is a plug residual forced non-negative
  so it cannot even show a mis-closure.
- `tcs.thermal_gradient` (0x0419) — max−min over temperatures in the same packet.
- `payload.swath_width_km` / `gsd_m` (0x0619/0x0628) — fixed optics × ephemeris.

**The HK budget, computed from the pack formats:** all 11 SIDs ≈ **1712 bps**, of which EPS
SID 1 alone is **800 bps** — 54 parameters at 1 Hz. Against the LOW rung (16 kbps) that is
fine for a real-time pass. The binding constraint is *stored* HK: 148 Mbit/day, of which EPS is
69 Mbit — 47 % of the entire housekeeping budget for one subsystem, needing ~9 250 s of contact
per day at LOW to play back. On the 90-byte SID 1 payload, six of the parameters above account
for 11 bytes (12 % of every packet, ~8.5 Mbit/day) and every one of them is recomputable on the
ground. There is also a priority inversion worth a thought: EPS, with 600–2700 s thermal time
constants, samples at 1 Hz, while AOCS — the fast dynamics — samples at 4 s.

---

## The two tools

### Planner — clean

No sim-truth defect on the modern interactive page or `/wide`. Position, altitude, eclipse
arcs, beta angle, contact windows, elevation and range are all propagated on the ground from
the TLE; SoC is a labelled planning *assumption* with an editable field; `/wide` reads
MCS-derived parameters and honours their `valid`/`reason` flags. It reads the simulator only
for the clock, MET and post-burn elements, and the last is hard-guarded to sim mode.

Two things:
- **`/classic` still runs the hard-coded eclipse model** — `35 * 60 * 1000` at
  `static/index.html:1641`, plus `ORBIT_PERIOD_MIN = 94.7` / `ALTITUDE_KM = 705` and a
  hard-coded `battery_soc=80.0`. Fabricated eclipse bars sit on the same timeline as real
  propagated contact windows. It is still routed and is the fallback for an unrecognised
  `SMO_UI` value.
- The data-volume figure is labelled `onboard` but assumes an empty store (the page never
  passes `current_onboard_mb`). SoC has an "assumed" affordance; this does not.

### Radio — the split is presentation-only

The operator page is *supposed* to be observational, with the channel model at `/sim`. In
practice:

- **"Eb/N0 (measured)" is the instructor's knob echoed back.** `bridge.py:394` —
  `eb_n0 = self.config.channel.eb_n0_db`. **BER** is then `0.5·erfc(√(10^(Eb/N0/10)))` of that
  same number — no bit errors are counted, so it can never show a non-AWGN impairment, and it
  cannot disagree with the knob. **"Doppler (measured)"** is `config.channel.doppler_hz`.
  Set Eb/N0 to 4 dB and the operator reads exactly 4.0.
- **Spacecraft transmitter internals on the operator page** — `tx_packet_drops`,
  `tx_buffer_overflows`, `tx_idle_frames` are rendered verbatim in the diagnostics panel. That
  is the cause, named, where the exercise wants the operator to infer it from lock, FER and
  spectrum.
- **`/ws` ships the entire channel-model config to every operator browser**, including
  `fading_enabled`, `n_interferers`, `phase_noise_enabled`, `gs_penalty_db` and the full
  `config` dict. Unrendered today, one line of JS from being rendered, visible in devtools now.
- **The operator footer links to `/classic`**, which displays Fading / K-factor / Interferers /
  Phase noise / GS penalty with no instructor strip — only the POST controls are gated.
- Freshness is measured on the wrong hop: the FEED chip ages the WebSocket message, but the
  bridge emits one every second regardless, so on simulator loss the page reads FRESH while
  Eb/N0, RSSI, Doppler and range sit frozen. In PACKET mode it shows three green locks and
  99.0 dB.

The one genuinely measured quality figure on the page is **RX SNR (EVM)** — and it is already
displayed, right next to the truth values.

---

## Defects that are not realism, but will bite

Found along the way; all verified against the models.

- **`obdh.last_reboot_cause` (0x0316) — the enum is wrong.** The catalogue says
  `1=watchdog, 2=memory, 3=switchover, 4=commanded`. The model defines `REBOOT_COMMAND=1,
  REBOOT_WATCHDOG=2, REBOOT_MEMORY_ERROR=3, REBOOT_SWITCHOVER=4`. **Every non-zero value
  decodes to the wrong cause** — a commanded reboot reads as "watchdog". This corrupts the
  first question an operator asks after an unexpected reboot.
  **This one is now worse because of the engineering-values work I just delivered:** the
  console used to show `1` and make you look it up; it now shows `watchdog (1)` with
  confidence. Which side is authoritative — the model or the catalogue — is your call, and I
  have deliberately not guessed.
- **The three `fdir.*` counters do not count alarms.** `active_alarms` counts tripped
  fault-cascade rules, `critical_alarms` counts *failed recovery procedures*, `high_alarms`
  counts *running* ones. The spacecraft has a real S12 monitoring service whose out-of-limits
  are what an operator would mean by "alarms".
- **`obdh.mmm_used` (0x0303) is frozen** at 20.0 and moved only by a memory-failure injection.
  Imaging and downlink never touch it, while `payload.mem_used_mb` tells a different story.
- **`obdh.obc_b_status` (0x030D)** emits an undocumented value 2 after a switchover;
  **`obdh.can_bus_status` (0x0A11)** can never emit its documented "degraded".
- **`tcs.htr_total_power` (0x0411)** is a nameplate sum of *commanded* states, so it reads full
  power through an injected open-circuit heater failure — masking the fault being taught.
- **`tcs.temp_thruster` (0x0409)** and `htr_thruster` (0x041B) are the propulsion arm
  interlock's inputs, and are on-demand only. Safety interlock inputs belong in routine HK.
- **`sat.mode` (0x0001)** — the top-level spacecraft mode word — is on-demand only and in no HK
  structure.

## What OBDH got right

Worth copying rather than just noting. The OBC's true oscillator error (`clock_drift_ppm`) is
written to **no parameter ID at all**; the model's own guard says *"Only the GPS-discipline
path may use this ... Nothing else in the model reads sim time."* `time_since_sync_s` is
measured on the drifting clock, so it is itself slightly wrong — which is exactly right. And
the one parameter that does expose the true offset lives in `derived.yaml` with
`source: ground`, in no HK SID, because the *ground* can measure that offset and the OBC
cannot.

That is the pattern the rest of the dictionary needs.

---

## Proposed mechanism

Rather than deleting parameters, add an explicit `visibility` classification to
`telemetry/parameters.yaml`:

```yaml
- id: 0x0B16
  name: prop.leak_rate
  visibility: instructor      # real | instructor | ground_derived
```

Then:

- `real` (default) — unchanged.
- `instructor` — served only to the instructor console; **refused** by `/api/param-catalog`
  and absent from the operator dictionary, so it cannot appear on a subsystem page, in the
  PARAMS list, or in an S20 read.
- `ground_derived` — moved to `derived.yaml`, computed by the MCS, and rendered with the
  ground provenance sub-label the console already supports.

With three gate tests: no `instructor` parameter in any HK structure; no S12 monitor defined on
a `ground_derived` or `instructor` parameter; and every parameter in an HK SID has a modelled
writer. That last one would have caught the six phantom heater zones on its own.

**What I need from you** is the ruling on each disposition — particularly the TTC set, since
moving `eb_n0`/`ber`/`link_margin` to the ground changes what the TT&C page and the S12
definitions look like. Tell me which tiers to action and I will implement them with the gates.

---

## AOCS — for completeness

Out of scope as agreed, but the same three species are present, and two of them say so:
`aocs.fidelity_mode` (0x02C6, the integration switch you found), `aocs.gyro_bias_injected`
(0x0296, *"instructor visibility"*), `aocs.tau_dist_x` (0x02BE, *"instructor insight"*),
`aocs.gyro_bias_resid` (0x02D2). The AOCS descriptions are honest about it; the TTC, PROP and
PAYLOAD ones are not, which makes them harder to spot and, on a console, worse.
