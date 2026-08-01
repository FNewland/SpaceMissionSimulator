# RF / Radio Subsystem Audit — EO-Sat-1 Simulator Suite

Date: 2026-07-28 · Scope: `packages/smo-rfsim` (whole package), radio web UI, `smo-simulator` TT&C model +
engine TM path, `smo-mcs` displays, `configs/eosat1/rfsim.yaml`, `configs/eosat1/subsystems/ttc.yaml`, `start.sh`.
Method: read-only source inspection + `pytest tests/test_rfsim/` (178 passed, 5.0 s).

---

## State of the RF subsystem — overall

The RF subsystem is **two-thirds built and one-third wired**. The DSP core is real and good: a threaded
TX → Channel → RX pipeline (`pipeline/`) that genuinely frames CCSDS TM, Reed–Solomon encodes, RRC pulse-shapes,
modulates to complex baseband, adds AWGN and a Doppler rotation, then matched-filters, tracks carrier phase with a
2nd-order M-th-power loop, slices symbols, resyncs on ASM and RS-decodes back to ECSS packets. That path works, is
tested, and drives the constellation display, the three lock LEDs and the good/bad frame counters with genuinely
derived quantities. Alongside it sits a second, *entirely unused*, richer channel model (`channel/space_link.py`
with Rician fading, Wiener phase noise, CW/wideband interferers, an 18-line link budget) and a third, well-developed
offline impairment chain in `recording/iq_recorder.py` (power-law phase noise, S-curve Doppler, I/Q imbalance,
DC offset, galactic noise, impulsive RFI, quantisation). Neither is reachable from a running simulation.

The failures the user reports are all *wiring* failures rather than missing physics. The spectrum analyser and eye
diagram have a complete UI renderer and a complete backend producer — and nothing in between: `RadioFrontend.update_spectrum()`
and `update_eye_diagram()` are never called from anywhere in the repo, so `spectrum_db`/`eye_i` are always empty
lists and both canvases stay blank forever. The same is true of `update_channel_status()` and `update_link_budget()`,
so the "Link Budget" and "Channel Model" panels are permanently `0.0 dB` / `OFF`. High-rate telemetry is broken at
four independent points (a uint8-vs-uint32 command-encoding mismatch, a tick-loop overwrite, a real-time TM path
that ignores data rate entirely, and an RF pipeline whose symbol rate is hard-wired to 32 kbaud because its config
section does not exist). Configurability is close to nil: the radio web UI is strictly read-only (three GET routes,
no POST/WS-in), the pipeline's `symbol_rate`/`sps`/`rolloff`/`modulation` are read from `config.pipeline` which is
not a field of `RFSimConfig`, and `channel.frequency_hz: 2200.0e6` in `rfsim.yaml` is silently discarded because
`ChannelConfig` has no such attribute. Modulation coverage is advertised as eight schemes and is real for three-and-a-half.

Net: the pieces to make the radio interface impressive already exist inside the process. Most of the fixes below are
10–60 line wiring jobs, not new DSP.

---

## Q1. SPECTRUM ANALYSER — **BROKEN** (renderer + producer both exist; never connected)

### Evidence

| Element | Location | State |
|---|---|---|
| UI panel + canvas | `packages/smo-rfsim/src/smo_rfsim/static/radio.html:134-138` (`<canvas id="spectrum-chart">`) | present |
| UI draw function | `radio.html:421-457` (`drawSpectrum()`), axis labels, −40…+10 dB scale | present, complete |
| UI guard | `radio.html:372-375` — `if (d.spectrum_db && d.spectrum_db.length > 4) drawSpectrum(...)` | never true |
| Transport field | `radio/frontend.py:75-76` (`spectrum_db: list[float]`, `spectrum_freq_range_hz`) | always `[]` / `0.0` |
| Backend producer | `radio/frontend.py:196-210` (`update_spectrum()` — Hann window, `np.fft.fftshift(np.fft.fft(...))`, 20 log10, decimate to 128 bins) | **correct, and never called** |
| Caller | none | `grep -rn "update_spectrum" .` → only the definition and this audit |
| WS serialiser | `radio/web_ui.py:40-42` (`asdict(status)` → JSON every 0.5 s) | works; ships an empty list |

**The exact break:** `RadioFrontend.update_spectrum()` has zero call sites. `bridge.py:_radio_updater()`
(`bridge.py:367-422`) is the only per-second updater and it calls `update_rf`, `update_lock`,
`get_constellation`, `update_frame_counts` — nothing else. The UI therefore receives `spectrum_db: []`,
the `length > 4` guard fails silently, and the canvas is never even cleared, so the panel renders as an empty
box with a heading. There is no error, no console message, no "no data" placeholder (unlike the constellation,
which does have one at `radio.html:347-351`).

Secondary break: `spectrum_freq_range_hz` is derived from `self.status.bandwidth_hz` (`frontend.py:210`), which is
only ever set by `update_channel_status()` (`frontend.py:187`) — also never called. So even once fed, the frequency
axis would fall back to the JS default of 256 kHz (`radio.html:374`).

### Where the samples already are

Three live taps, in order of preference:

1. **`pipeline/channel_stage.py:115` / `:123`** — `impaired` is the post-AWGN, post-Doppler complex64 stream
   at `sample_rate = symbol_rate × sps` (32 kBd × 8 = 256 kHz), i.e. exactly what a ground-station SDR sees.
   It is already handed to `_zmq_publish()` (`channel_stage.py:126-132`), so a second consumer is trivial.
   This is the correct tap — it shows the RRC-shaped main lobe, the roll-off shoulders, the noise floor rising
   as Eb/N0 drops, and the whole spectrum sliding sideways under Doppler.
2. **`pipeline/sample_buffer.py`** — `SampleBuffer` (the `ch→rx` buffer, `coordinator.py:52`) already carries the
   same chunks; a `peek()`-style copy avoids touching the channel thread.
3. **`dsp/correlator_rx.py:161`** — `filtered` (post-matched-filter) if you want to show the *receiver's* view
   rather than the antenna's.

### Fix proposal

| # | Change | Effort |
|---|---|---|
| S1 | In `ChannelStage._run_loop`, keep a rolling `deque` of the last ~2048 impaired samples behind a lock; expose `ChannelStage.get_recent_samples()`. Add `PipelineCoordinator.get_spectrum_samples()`. In `bridge.py:_radio_updater()` (RF branch, after line 387) call `self.radio.update_spectrum(self._pipeline.get_spectrum_samples())`. ~25 lines. | **S** |
| S2 | Also call `self.radio.update_channel_status(...)` and `update_link_budget(...)` in the same block so the frequency axis, bandwidth, and the dead "Link Budget"/"Channel Model" panels come alive. Requires instantiating `SpaceLinkChannel`/`LinkBudget` (Q6) or synthesising the dict from `config.channel` + `ttc.yaml`. | **S–M** |
| S3 | Average 4–8 FFTs (Welch) before display — a single 512-point FFT of a noisy stream is visually very jumpy at 2 Hz update. `frontend.py:196` currently takes one snapshot. | **S** |
| M1 | Add a "no data" placeholder to `radio.html:372` mirroring the constellation's (`:347-351`) so a future breakage is visible rather than silent. | **S** |
| M2 | FRAME and PACKET modes have no sample stream at all, so the panel must be hidden (or show "RF mode only") outside `mode == RF`. `radio.html` never checks `d.mode` for these panels. | **S** |

---

## Q2. EYE DIAGRAM — **BROKEN** (identical failure mode; plus a resolution problem once fixed)

### Evidence

| Element | Location | State |
|---|---|---|
| UI panel + canvas | `radio.html:140-144` (`<canvas id="eye-diagram" width="400" height="300">`) | present |
| UI draw function | `radio.html:459-495` (`drawEyeDiagram()`) — grid, 30 overlaid traces at 30 % alpha, ±1 labels | present, I-channel only |
| UI guard | `radio.html:377-380` — `if (d.eye_i && d.eye_i.length > 4)` | never true |
| Transport fields | `radio/frontend.py:78-79` (`eye_i`, `eye_q`) | always `[]` |
| Backend producer | `radio/frontend.py:212-225` (`update_eye_diagram(symbols, sps=8, traces=20)` — folds at `2 × sps`, emits real and imag) | **correct, and never called** |
| Caller | none | `grep -rn "update_eye_diagram" .` → definition only |

**The exact break:** identical to Q1 — no call site. Additionally `eye_q` is transported but the JS renderer
ignores it entirely (`radio.html:479-488` only plots `eyeI`), so even a correct feed gives a single-rail eye —
fine for BPSK, wrong for QPSK/OQPSK where the Q rail is where OQPSK's half-symbol offset would show.

**A second, subtler break waiting behind the first.** The obvious thing to feed it —
`CorrelatorRX.constellation_points` (`dsp/correlator_rx.py:266`) — is **symbol-rate decimated** (one sample per
symbol, `correlator_rx.py:176-178`). Folding one-sample-per-symbol data at a 16-sample period, as
`frontend.py:219` and `radio.html:476` both assume, produces noise, not an eye. An eye diagram needs the
*oversampled* waveform between decision instants.

### What the DSP chain already has that would feed a real eye

- **`dsp/correlator_rx.py:161`** — `filtered = np.convolve(samples, self._rrc, 'same')`: the post-matched-filter
  complex stream at **sps = 8 samples/symbol**. This is precisely the right signal: fold it at 2 × sps = 16 with
  the symbol-timing offset from `indices` (`correlator_rx.py:176`) and you get a textbook eye whose opening
  narrows as Eb/N0 falls and closes completely when the PLL slips. It is a local variable today — retaining the
  last ~512 samples is a 3-line change.
- **`correlator_rx.py:178`** `raw_symbols` / **`:184-188`** `symbols` — the timing-recovery output (post
  phase-correction decision instants). Useful for the *decision-point scatter* overlay (the classic dots at the
  eye centre) but not for the traces.
- **`dsp/demodulator.py:139`** `constellation_points` — the alternative Costas/M&M receiver, used only for the
  TC uplink (`pipeline/coordinator.py:110`, `:206`); same decimation caveat.

### Fix proposal

| # | Change | Effort |
|---|---|---|
| S1 | Retain a rolling buffer of the last N post-matched-filter samples in `CorrelatorRX.demodulate()` (after `correlator_rx.py:161`), plus the current symbol-timing offset. Expose `get_eye_samples()` → `GroundStationRX` → `PipelineCoordinator`. Call `radio.update_eye_diagram(...)` from `bridge.py:_radio_updater()`. ~30 lines. | **S** |
| S2 | Fix `frontend.py:212-225` to take oversampled samples + `sps` + timing phase rather than "symbols", and to slice exactly `traces × 2 × sps` values aligned to the decision instant. | **S** |
| M1 | Render the Q rail (`eye_q`) as a second colour in `radio.html:459-495`, and draw the decision instant as a vertical marker. This is what makes OQPSK visibly different from QPSK — and is currently the only way a student could *see* that OQPSK is not implemented (Q5). | **S** |
| M2 | Overlay decision-point dots from `symbols` and print a measured eye-opening / EVM number (the RX already computes an EVM-based SNR estimate at `correlator_rx.py:233-240` — surface `_energy_avg` as a dB figure). | **M** |

---

## Q3. HIGH-RATE TELEMETRY — **BROKEN** (four independent breaks; the 64 kbps rate is unreachable from the ground)

> **CLOSED.** All four breaks were fixed by defect #74 / roadmap N-8, and the rate
> plan itself was then replaced: defect #85 retired the 1 kbps / 64 kbps pair for a
> three-rung ladder (BEACON 1 / LOW 16 / HIGH 128 kbps) and defect #86 propagated it
> through the planner, the console, the procedures and the courseware. The analysis
> below is retained as the dated record of why the change was needed; every rate it
> quotes is historical. Current behaviour: `docs/LINK_BUDGET_AND_RATES.md`.

### What "high rate" is in the code

It is a **data-rate setting**, not a separate channel, APID, band or payload downlink. There is exactly one
downlink path. Two rates exist:

- `configs/eosat1/subsystems/ttc.yaml:4-5` — `tm_rate_hi_bps: 64000`, `tm_rate_lo_bps: 1000`.
- `models/ttc_basic.py:74` — `data_rate_mode: int = 1  # 0=low-rate (1 kbps), 1=high-rate (64 kbps)`.
- `models/ttc_basic.py:24` — `tm_data_rate: int = 64000` (the reported value, param `0x0506`).
- Command `TTC_SET_DATA_RATE`, S8 func 65 (`configs/eosat1/commands/tc_catalog.yaml:987-993`).

This matches the textbook exactly: *"the EOSAT-1 simulator only supports the two rates above"*
(`docs/review_2026-07/textbook_inventory.md:46`, book p. 400) — **but the claim is true of the catalogue and false
of the effect**. This corroborates and extends the existing roadmap item **N-8**
(`docs/review_2026-07/UPGRADE_ROADMAP.md:113`, `REVIEW_2026-07.md:414`).

### Break 1 — the command argument is decoded as the wrong type (new finding)

`tc_catalog.yaml:992` declares `{ name: rate, type: uint8, description: "0=low(1kbps), 1=high(64kbps)" }`.
The dispatcher (`packages/smo-simulator/src/smo_simulator/service_dispatch.py:928-934`) does:

```python
elif func_id == 65:  # set tm rate
    rate = (struct.unpack('>I', data[:4])[0] if len(data) >= 4 else 64000)
    ttc.handle_command({"command": "set_tm_rate", "rate": rate})
```

It expects a **big-endian uint32 raw bps value**. An operator following the documented procedure
(`configs/eosat1/procedures/nominal/data_rate_change.md:63-64`, `mcs/role_analysis/ttc_role.md:57`) sends one byte:

- `rate=1` → `data = b'\x01'` → `len(data) < 4` → falls through to the literal `64000` → *appears* to work, but only
  by accident, and `rate=0` (LOW) does exactly the same thing — **the low-rate command silently sets high rate**.
- If instead the argument is padded to 4 bytes (`00 00 00 01`), `rate = 1`, and `ttc_basic.py:820`
  (`if rate in (self._tm_rate_hi, self._tm_rate_lo)`) rejects it: `{"success": False, "message": "Invalid rate"}`.

Only `struct.pack('>I', 64000)` / `struct.pack('>I', 1000)` work — which is what the acceptance test does
(`tests/test_acceptance/test_commands.py:279-280`), which is why the test suite never caught this.
The MCS S8 UI takes a raw hex-bytes argument (`packages/smo-mcs/src/smo_mcs/static/index.html:3293`), so the operator
must know the undocumented 4-byte form. Also note the procedures quote **func_id 52** for `TTC_SET_DATA_RATE`
(`procedures/nominal/data_rate_change.md:38,63`; `contingency/ber_anomaly.md:41,93`) while the catalogue says **65**.

### Break 2 — even a successful command is overwritten on the next tick (this is roadmap N-8)

`ttc_basic.py:818-823` sets `self._state.tm_data_rate = rate`. But `tick()` unconditionally recomputes it every
second (`ttc_basic.py:347-357`):

```python
elif not s.beacon_mode:
    base_rate = self._tm_rate_hi if s.data_rate_mode == 1 else self._tm_rate_lo
    s.tm_data_rate = base_rate * bits_per_sym
```

`data_rate_mode` is settable by **no telecommand anywhere**. The only writers are
`engine.py:432` and `engine.py:2184`, both setting it to `0` (bootloader/beacon). Once a scenario boots through the
bootloader phase, `data_rate_mode` is 0 forever and the rate is pinned to `tm_rate_lo × bits_per_symbol`.
Confirmed by the code comment in `tests/test_simulator/test_obcp_s18.py:348`.

### Break 3 — real-time telemetry is not rate-limited at all

Grepping `engine.py` for `tm_data_rate` yields exactly one consumer: `_tick_dump_emission()`
(`engine.py:1647-1690`, `rate_bps = float(getattr(ttc._state, "tm_data_rate", 1000))`, byte-budget pacing at
`:1671-1683`). That is **S15 stored-TM dump pacing only**. Real-time HK/event/verification TM goes through
`_enqueue_tm()` (`engine.py:1791-1812`), which gates on `downlink_active` (`engine.py:837-856`) and nothing else —
no rate, no byte budget. So switching to high rate produces **no additional real-time frames**; only a stored dump
would drain 64× faster. This is the single largest reason "it doesn't seem to do anything".

### Break 4 — the RF pipeline symbol rate is hard-wired and never reconfigured

`pipeline/coordinator.py:41-48`:

```python
pc = getattr(config, 'pipeline', None)
symbol_rate = getattr(pc, 'symbol_rate', 32000.0) if pc else 32000.0
sps = getattr(pc, 'sps', 8) if pc else 8
rolloff = ...; modulation = getattr(pc, 'modulation', 0) if pc else 0
```

`RFSimConfig` (`packages/smo-rfsim/src/smo_rfsim/config.py:66-72`) has fields `mode`, `ccsds`, `channel`, `network`
— **there is no `pipeline` field**, and `configs/eosat1/rfsim.yaml` has no `pipeline:` block. So `pc` is always
`None` and the pipeline runs at a fixed **32 kBd / sps 8 / 256 kHz / BPSK**, i.e. 32 kbps — a rate that is neither
1 kbps nor 64 kbps.

The bridge receives the sim's rate over WS (`engine.py:2351` publishes `ttc.data_rate_bps`) and does exactly one
thing with it (`bridge.py:536-538`):

```python
data_rate = ttc.get("data_rate_bps")
if data_rate is not None:
    self.radio.status.data_rate_kbps = float(data_rate) / 1000.0
```

— it updates a **text label**. `PipelineCoordinator.reconfigure(symbol_rate=...)` exists
(`coordinator.py:257-265`, `tx_chain.py:124-135`) and is **never called with a symbol rate**; only
`reconfigure(modulation=...)` is called (`bridge.py:522`). So the modulated signal, the frame cadence
(`tx_chain._calc_frame_interval`, `:80-92`) and the occupied bandwidth are all identical at 1 kbps and 64 kbps.
The RX chain has no rate-change handling at all.

### What the MCS shows

Param `0x0506` is a real HK parameter (`configs/eosat1/telemetry/parameters.yaml:249`,
`hk_structures.yaml:248`, `xtce/eosat1.xml:3327,3531,3625`) and is displayed as
`RF PARAMETERS → Data Rate` (`packages/smo-mcs/src/smo_mcs/static/index.html:1598`, populated at `:4731` via
`server.py:61` key map `tm_data_rate → data_rate_bps`). So **the MCS faithfully displays whatever number the model
reports** — and that number is currently wrong/frozen for the reasons above. There is no MCS panel showing actual
achieved throughput, frames/s, or link occupancy, and no MCS link to the radio UI at `:8094` (only `start.sh:143`
prints it).

### Verdict and fixes

**BROKEN** end-to-end. The 64 kbps rate is not reachable from the ground; when nominally "selected" nothing about
the emitted frames, the modulated signal or the MCS-visible throughput changes.

| # | Change | Effort |
|---|---|---|
| H1 | Add `set_data_rate_mode` to `ttc_basic.handle_command` and route func 65 to it; accept the catalogued **uint8** `0/1` in `service_dispatch.py:928-934` (keep the 4-byte raw-bps form as a legacy fallback). Make `tick()` respect an explicit override. Fixes Breaks 1+2. ~20 lines. Also fix the func-id 52 vs 65 discrepancy in the four procedure docs. | **S** |
| H2 | Pace real-time TM by `tm_data_rate` in `_enqueue_tm`/the tick, using the same byte-budget approach already proven in `_tick_dump_emission` (`engine.py:1647-1690`) — extract it into a shared `_release_by_rate()`. This is what makes the rate *observable*: at 1 kbps HK visibly backs up and arrives late; at 64 kbps it flows. Fixes Break 3. | **M** |
| H3 | Add a `pipeline:` section to `RFSimConfig` + `rfsim.yaml` (`symbol_rate`, `sps`, `rrc_rolloff`, `modulation`, `sample_buffer_depth`, `tc_symbol_rate`, `tc_sps`) — `coordinator.py:41-48` already reads it via `getattr`, so this is pure config plumbing. | **S** |
| H4 | In `bridge._apply_sim_feedback` (`bridge.py:536`), call `self._pipeline.reconfigure(symbol_rate=data_rate/bits_per_symbol)` alongside the label update, and add matching RX-side handling (`GroundStationRX.reconfigure` currently takes modulation only, `rx_chain.py:148-161`). Then a rate change visibly changes the spectrum width (Q1) and the eye timing (Q2). Fixes Break 4. | **M** |
| H5 | Add an "achieved throughput" row to the MCS RF panel (frames/s × frame bits) and to the radio UI, so high-rate selection has a *measured* consequence, not just a commanded label. | **S** |

---

## Q4. RADIO CONFIGURATION — **PARTIAL** (config file partly dead, no runtime API, zero UI controls)

### What exists today

**A. `configs/eosat1/rfsim.yaml` (loaded by `config.py:74-94`, `hasattr`-filtered so unknown keys are silently dropped):**

| Key | Consumed? | Where |
|---|---|---|
| `mode` | ✅ (overridden by `start.sh --rf=`) | `config.py:80-81`, `bridge.py:579-580` |
| `ccsds.tm_frame_length`, `scid`, `fecf_present`, `rs_enabled`, `convolutional_enabled` | ✅ | `coordinator.py:60-96` |
| `ccsds.vc_realtime`, `vc_playback`, `vc_idle` | ⚠️ parsed, VC ids hard-coded downstream (`bridge.py:329` `vcid=0`) | — |
| `channel.eb_n0_db` | ✅ (also overwritten live from sim WS, `bridge.py:461-468`) | `coordinator.py:74-84` |
| `channel.doppler_hz` | ✅ (overwritten live, `bridge.py:470-475`) | — |
| `channel.awgn_enabled` | ❌ **parsed but never read** — `BasebandChannel.process` always adds noise below 50 dB (`dsp/channel.py:58`) | — |
| `channel.frequency_hz: 2200.0e6` | ❌ **silently discarded** — `ChannelConfig` (`config.py:35-44`) has no `frequency_hz`; `hasattr` filter drops it | — |
| `channel.path_loss_db`, `ber_target`, `delay_ms` | ❌ never read in RF mode (`ber_target` used only by the FRAME-mode `ChannelModel`) | — |
| `network.*` | ✅ | `bridge.py`, `coordinator.py:75` |
| `network.zmq_samples_port` | ✅ | `channel_stage.py:66-71` |

**B. `configs/eosat1/subsystems/ttc.yaml` (consumed by `ttc_basic.configure`, `:182-214`):**
`ul_freq_mhz` 449.0, `dl_freq_mhz` 401.5 (UHF — **DI-05**), `tm_rate_hi_bps`, `tm_rate_lo_bps`, `eirp_dbw`,
`gs_g_t_db`, `sc_gain_dbi`, `param_ids`. Undocumented-but-read: `tc_uplink_rate_bps`, `coding_gain_db`,
`pa_max_power_w`, `pa_nominal_power_w`, `pa_shutdown_temp_c`, `pa_thermal_tau_s` (`ttc_basic.py:189-195`).

**C. Runtime — spacecraft telecommands only (S8, `service_dispatch.py:919-976`):**
63/64 transponder select, 65 set TM rate (**broken**, Q3), 66/67 PA on/off, 68 TX power, 69 antenna deploy,
70 beacon mode, 71 command channel, 72/73 UL/DL frequency, **74 set modulation** (works, and is the *only*
command that actually reaches the DSP — `bridge.py:517-522` → `coordinator.reconfigure`), 75 RX gain (AGC target,
cosmetic only), 76/77 ranging, 78 coherent mode.

Three catalogue/dispatcher/model contradictions in this set:
- `TTC_SET_UL_FREQ` catalogue says **2000–2100 MHz** (`tc_catalog.yaml:1036`), model validates **440–460 MHz** (`ttc_basic.py:878`).
- `TTC_SET_DL_FREQ` catalogue says **8400–8500 MHz** (X-band!, `tc_catalog.yaml:1043`), model validates **400–410 MHz** (`ttc_basic.py:886`), `rfsim.yaml:19` says **2200 MHz**. Four different bands across four files.
- `TTC_SET_TX_POWER` catalogue declares `level: uint8` 0/1/2 = 1/5/8 W (`tc_catalog.yaml:1010`); dispatcher unpacks a **float32 watts** (`service_dispatch.py:938-943`); model rejects anything above `pa_max_power_w = 5.0` (`ttc_basic.py:840-844`) — so the catalogued 8 W setting can never be applied.

**D. Runtime — RF bridge:** `bridge.main()` accepts `--config`, `--mode`, `--eb-n0`, `--radio-ui`, `--radio-web`,
`-v` (`bridge.py:560-568`). Once started, **nothing is changeable**. `run_web_ui` registers three routes —
`GET /`, `GET /ws`, `GET /api/status` (`radio/web_ui.py:55-59`). No POST, no inbound WS handling. The docstrings are
explicit that this is intentional: *"Purely observational — does not inject failures"* (`web_ui.py:2-4`,
`frontend.py:114-120`).

**E. UI controls:** none. `radio.html` is 544 lines of read-only widgets; the only interactivity is the WS reconnect
timer (`radio.html:384-388`).

### What an instructor/operator would reasonably want at runtime

Frequency (UL/DL), symbol rate, modulation, FEC selection (RS on/off, convolutional on/off, interleaver depth),
TX power / EIRP, ground G/T and antenna gain, Eb/N0 or C/N0 setpoint, AWGN on/off, Doppler (manual override vs
orbit-driven, plus rate), phase-noise linewidth, Rician K / fading rate, CW and wideband interferers
(offset + power), receiver loop bandwidth, RRC roll-off, sps, ground-station equipment penalties (the seven `gs_*`
failures), and pass gating (link in view / TX on).

### Proposed configuration surface (consistent with existing architecture)

**1. Config — extend `RFSimConfig` (`config.py`) and `rfsim.yaml`:**

```yaml
pipeline:                 # NEW — read today via getattr, currently always None (coordinator.py:41-48)
  modulation: 0           # 0=BPSK 1=QPSK 2=8PSK 3=OQPSK
  symbol_rate: 32000.0
  sps: 8
  rrc_rolloff: 0.35
  gmsk_bt: 0.5
  sample_buffer_depth: 128
  tc_symbol_rate: 4000.0
  tc_sps: 16
  rx_loop_bw: 0.08
  tc_uplink_eb_n0_db: 25.0   # currently hard-coded, coordinator.py:106

channel:
  frequency_hz: 2200.0e6     # FIX: add to ChannelConfig so it stops being dropped
  awgn_enabled: true         # FIX: actually honour it in BasebandChannel.process
  phase_noise_enabled: false # NEW — SpaceLinkConfig fields already exist (space_link.py:43-59)
  phase_noise_linewidth_hz: 10.0
  fading_enabled: false
  fading_k_factor_db: 10.0
  fading_max_doppler_hz: 50.0
  multipath_enabled: false
  interferers: []            # [{type: cw, freq_offset_hz: 5000, power_dbm: -90}, ...]

link_budget:                 # NEW — mirrors LinkBudget ctor (channel/link_budget.py:20)
  eirp_dbw: 10.0
  gs_gt_dbk: 20.0
  atmospheric_loss_db: 0.5
  implementation_loss_db: 2.0
  polarization_loss_db: 0.3
  coding_gain_db: 6.0
```

Add a strict-key check (fail loudly on unknown keys) — `config.py:82-93`'s `hasattr` filter is the mechanism that
made `frequency_hz` dead config; this is the same class of problem as DI-15/DI-07.

**2. Runtime API — extend `radio/web_ui.py` with a small control plane** (aiohttp is already there; keep it
symmetrical with the existing `GET /api/status`):

| Route | Body | Maps to |
|---|---|---|
| `GET /api/config` | — | current effective pipeline+channel config |
| `POST /api/channel` | `{eb_n0_db, doppler_hz, awgn_enabled, phase_noise_*, fading_*, interferers[]}` | `PipelineCoordinator.set_eb_n0/:220`, `set_doppler/:225`, new `set_impairments()` |
| `POST /api/pipeline` | `{modulation, symbol_rate, rolloff}` | `PipelineCoordinator.reconfigure()/:257` (already exists, extend for rolloff) |
| `POST /api/link` | `{in_view, transmitting}` | `set_link_in_view()/:230`, `set_transmitting()/:244` |
| `GET /api/diagnostics` | — | `PipelineCoordinator.get_diagnostics()/:311` (a rich 18-counter dict that is currently only ever written to the log at `bridge.py:305`) |

Guard with a `--radio-control` flag (default off) so the "purely observational" contract is preserved unless the
instructor opts in. Sim-driven values (Eb/N0, Doppler from `_apply_sim_feedback`) should take precedence unless an
explicit manual-override flag is set, otherwise the WS feedback at `bridge.py:454-545` will stomp operator input
within a second.

**3. UI:** add a collapsible "RADIO CONFIG" panel to `radio.html` (sliders for Eb/N0 and Doppler, dropdown for
modulation, numeric for symbol rate, toggles for AWGN / phase noise / fading, an interferer add/remove list),
posting to the routes above; plus surface `get_diagnostics()` in a "Pipeline Diagnostics" panel. Add a link to
`http://<host>:8094` from the MCS and instructor consoles (neither references it today).

**Effort:** config plumbing **S**; control-plane API **M**; UI panel **M**; interferer/fading wiring depends on Q6 (**M**).

---

## Q5. MODULATIONS — **PARTIAL** (8 advertised, 3 fully working, 1 mislabelled, 4 transmit-only)

### Table: modulation → implemented? → where → gaps

| # | Modulation | TX (modulate) | RX (demodulate) | Sim TTC model | UI | Verdict | Gap |
|---|---|---|---|---|---|---|---|
| 0 | **BPSK** | ✅ `dsp/modulator.py:13` `BPSK_MAP`, RRC shaping `:161-167` | ✅ `dsp/correlator_rx.py:54,193-198` (squared-loop), `:319-320` slicer; alt Costas `dsp/demodulator.py:153-154` | ✅ `ttc_basic.py:150` | ✅ | **WORKS** | No NRZ-M/differential option; no residual-carrier (PCM/PSK/PM) variant |
| 1 | **QPSK** | ✅ `modulator.py:15-17` | ✅ `correlator_rx.py:199-202` (4th-power), `:322-335` | ✅ `ttc_basic.py:151` | ✅ | **WORKS** | Natural-binary, not Gray, symbol mapping (`modulator.py:154-159` vs `correlator_rx.py:329-334`) — self-consistent, but BER vs Eb/N0 is worse than the Gray-coded theory the textbook quotes |
| 2 | **8PSK** | ✅ `modulator.py:19-21` `PSK8_MAP` | ✅ `correlator_rx.py:203-205` (8th-power), `:337-346` | ✅ `ttc_basic.py:152` (+3.6 dB penalty), BER at `:171-175` | ✅ | **WORKS** | Natural binary not Gray; no SCCC/pragmatic-TCM pairing as CCSDS 131.2-B expects |
| 3 | **OQPSK** | ❌ **mislabelled QPSK** — `modulator.py:24` `OQPSK_MAP = QPSK_MAP.copy()`, and `modulate()` (`:161-167`) applies **no half-symbol Q delay** | ❌ handled identically to QPSK — `correlator_rx.py:54` (`3: 2`), `:199` (`in (1, 3)`), `:322` (`in (1, 3)`) | ✅ `ttc_basic.py:153` | ✅ | **BROKEN as labelled** | The defining feature (Q offset by Ts/2, bounded phase transitions, constant-ish envelope) is absent. Selecting OQPSK changes nothing about the waveform. This is the one a student *would* be asked to identify from a constellation/eye |
| 4 | **16-APSK** | ✅ TX only — `modulator.py:26-33` (DVB-S2 4+12 rings, γ=2.7) | ❌ `correlator_rx._BITS_PER_SYM` (`:54`) has no key 4 → `bps` silently falls back to 1 and `_symbols_to_bits` hits the BPSK fallback (`:348-349`) | ❌ not in `MODULATION_PARAMS` (`ttc_basic.py:149-154`) → `set_modulation` rejects it (`:893-896`) | ✅ drawn (`radio.html:404-410`) | **BROKEN** | No demod, no AGC handling for two-ring amplitude, not commandable |
| 5 | **π/4-DQPSK** | ✅ TX only — differential encoding `modulator.py:36,145-153` | ❌ no differential decoder anywhere; `CONSTELLATION_MAPS[5]` is plain QPSK so a coherent slicer would give garbage | ❌ not commandable | ✅ drawn (`radio.html:399`) | **BROKEN** | Differential *decoding* entirely absent |
| 6 | **GMSK** | ✅ TX only — real CPM path, Gaussian pulse + phase integration, h=0.5 (`modulator.py:84-90, 169-197`) | ❌ no CPM/FM discriminator or Viterbi CPM receiver; `_BITS_PER_SYM` has no key 6 → BPSK fallback | ❌ not commandable | ✅ drawn (`radio.html:400`) | **BROKEN** — and this is the CCSDS 401.0-B high-rate near-Earth recommendation and the textbook's UHF worked example (9600 bps GMSK, `textbook_inventory.md:43`) |
| 7 | **GFSK** | ⚠️ TX only, and **identical to GMSK** — `modulator.py:190-191` hard-codes `h = 0.5` despite the docstring promising a configurable index | ❌ same as GMSK | ❌ not commandable | ✅ drawn (`radio.html:401`) | **BROKEN** |

Also present / absent:

- **Pulse shaping:** RRC (`modulator.py:64-81`, roll-off default 0.35, matched at RX `correlator_rx.py:25-42`) ✅;
  Gaussian BT (`modulator.py:84-90`, default 0.5) ✅ — but `gmsk_bt` is not reachable from any config.
- **Differential encoding / NRZ-M:** ❌ absent except inside π/4-DQPSK. CCSDS 401.0-B mandates NRZ-M for
  suppressed-carrier BPSK/QPSK to resolve the phase ambiguity. The sim instead resolves ambiguity by brute-force
  ASM correlation over candidate rotations (`correlator_rx.py:287-315`) plus a timed auto-search
  (`rx_chain.py:256-376`) — clever, but it teaches the wrong mechanism.
- **PCM/PSK/PM residual carrier + subcarrier:** ❌ absent. This is *the* standard CCSDS near-Earth low-rate TT&C
  mode and the reason ranging works; `ranging_start`/`ranging_stop` (`ttc_basic.py:916-924`) set a boolean with no
  signal-level counterpart.
- **SP-L / Manchester:** ❌ absent.
- **Coding:** RS(255,223) ✅ (`ccsds/reed_solomon.py`), interleaver ✅ (`ccsds/interleaver.py`),
  convolutional K=7 r=1/2 + Viterbi ✅ but **disabled** (`rfsim.yaml:15` — "pure-Python Viterbi too slow (~5 s/frame)"),
  BCH(63,56) for CLTU ✅ (`ccsds/bch.py`), randomiser ✅ (`ccsds/scrambler.py`).
  **Turbo and LDPC: absent** — the textbook quotes coding gains for both (`textbook_inventory.md:45`) and names
  QPSK+LDPC r=3/4 as the *recommended* scheme, and the book's own EOSAT-1 3U baseline is "S-band 2 W **QPSK+LDPC**"
  (`textbook_inventory.md:142`).

### Expectation vs reality

| Expected for a CCSDS near-Earth S-band link | In the sim |
|---|---|
| BPSK/QPSK/OQPSK with NRZ-M + SRRC (401.0-B 2.4.17A/2.4.18) | BPSK/QPSK real; OQPSK fake; no NRZ-M |
| GMSK with precoding for high symbol rates (401.0-B 2.4.17B) | TX only, no receiver |
| PCM/PSK/PM residual carrier for low-rate TT&C + ranging | absent |
| 8PSK / 16-APSK with SCCC (131.2-B) | 8PSK real (natural binary); 16-APSK TX only |
| RS(255,223) + conv K=7, or LDPC (131.0-B) / turbo | RS ✅, conv present-but-off, LDPC/turbo absent |
| Textbook claim (`textbook_inventory.md:46`, book p. 400): "modulation BPSK/QPSK/8PSK/OQPSK (func 74)" | true of the *catalogue* and the TT&C model; OQPSK is not a distinct waveform in the DSP |

### Fix proposal

| # | Change | Effort |
|---|---|---|
| M1 | **Implement OQPSK properly** — delay the Q rail by `sps/2` in `Modulator.modulate` when `modulation == 3`, and advance it back in `CorrelatorRX` before slicing. ~15 lines each side, and it makes the eye diagram (Q2) genuinely instructive. | **S** |
| M2 | **Gray-code** the QPSK/8PSK symbol maps on both sides so measured BER matches the textbook curves. | **S** |
| M3 | Either **remove** 16-APSK / π/4-DQPSK / GFSK from `MOD_NAMES`, `_CONSTELLATIONS` and `radio.html:393-411`, or gate them behind an "unimplemented" label — advertising eight schemes when four cannot be received is worse than offering four. | **S** |
| M4 | **GMSK receiver** (differential-phase discriminator + matched Gaussian filter is sufficient at these SNRs; full CPM-Viterbi optional). Unlocks the textbook's UHF 9600 bps GMSK worked example and the CCSDS high-rate recommendation. | **M** |
| M5 | **NRZ-M differential encoding** as a config option on BPSK/QPSK, with the ASM-rotation brute force kept as fallback. Teaches the real ambiguity-resolution mechanism. | **M** |
| M6 | **LDPC (CCSDS 131.0-B r=1/2, 7/8)** — the book's recommended scheme and the largest single gap vs the taught material. Pure-Python min-sum on 1115-byte frames is feasible offline but likely too slow in the live loop; consider frame-rate-limited or numpy-vectorised. | **L** |
| M7 | **PCM/PSK/PM residual carrier + ranging tone** so `ranging_start` has a signal-level meaning. | **L** |

---

## Q6. RF NOISE INJECTION — **PARTIAL** (a good impairment library exists; almost none of it is reachable at runtime)

### What can actually be injected today, and how

**A. In the live RF pipeline (`mode: RF`) — two impairments only:**

| Impairment | Implementation | How it is driven |
|---|---|---|
| **AWGN at a commanded Eb/N0** | `dsp/channel.py:56-65` (Es/N0 = Eb/N0 + 10log10(bps); σ² = 1/(2·Es/N0·sps)) | `bridge.py:461-468` from sim WS `ttc.eb_n0_db` → `coordinator.set_eb_n0/:220` → `channel_stage.set_eb_n0/:73`. Also `--eb-n0` at launch (`bridge.py:564,581-582`). |
| **Carrier frequency offset (Doppler)** | `dsp/channel.py:48-53` | `bridge.py:470-475` from sim WS `ttc.doppler_hz` (computed in `ttc_basic.py:485-497` from range rate) → `coordinator.set_doppler/:225`, plus RX pre-compensation `correlator_rx.py:82-84,150-158`. |
| **Total signal loss** | `channel_stage.py:106-122` — when the link is inactive the signal is *replaced* by σ=0.3 complex noise | `set_link_in_view()/:230`, `set_transmitting()/:244` from contact state |

That is the complete list. No phase noise, no fading, no multipath, no interferers, no jamming, no I/Q imbalance,
no DC offset, no quantisation, no group delay, no filtering are applied to the live stream.

**B. In FRAME mode — byte-level bit-flip only:** `channel/model.py` `ChannelModel.impair()` and
`channel/ber_injector.py` (44 lines), driven by `channel.eb_n0_db`/`ber_target`. Used at `bridge.py:202,336`.

**C. Written but dead — `channel/space_link.py` (224 lines) and its dependencies.** `SpaceLinkChannel` composes
Doppler → multipath → Rician/Rayleigh fading → phase noise → AWGN → interferers (`space_link.py:166-207`), with
config dataclass fields for every one (`space_link.py:26-65`) and a `get_status()` (`:209-224`) whose keys exactly
match what `RadioFrontend.update_channel_status()` expects (`frontend.py:180-187`). Supporting classes:
`AWGNSource`, `PhaseNoiseSource` (Wiener, `noise.py:52-85`), `CWInterferer` (`:88-112`), `WidebandInterferer`
(`:115-149`), `RicianFading`/`RayleighFading`/`MultipathChannel` (`fading.py`), `LinkBudget` (`link_budget.py`).
**`SpaceLinkChannel` is instantiated nowhere** — `grep -rn "SpaceLinkChannel" .` returns only its own definition and
a docstring reference in `frontend.py:181`. The RF pipeline uses the much simpler `BasebandChannel` instead
(`coordinator.py:80-84`). Consequently the radio UI's "Channel Model" panel (`radio.html:157-164`) is hard-wired to
show `OFF / OFF / -- dB / 0` forever.

**D. Written but offline-only — `recording/iq_recorder.py` (590 lines).** A genuinely comprehensive impairment
chain, all configurable via `ImpairmentConfig` (`iq_recorder.py:80-115`): static carrier offset, LEO S-curve
Doppler, elevation-hump amplitude envelope, burst gating, random-walk **and** power-law (dBc/Hz) phase noise, I/Q
gain/phase imbalance, DC offset, residual Doppler + AFC jitter, in-band SNR, coloured galactic sky noise,
impulsive spikes, quantisation to 8-bit, Rician multipath with configurable K and fading rate, CW interferer
tuples, broadband RFI, band-edge filtering, sample-clock ppm error. It writes `.cf32` + SigMF sidecars via
`recording/cli.py`. None of it is reachable from a running sim — it is a file generator.

**E. Ground-station / spacecraft RF failure modes (`models/ttc_basic.py:938-1024`)** — these are *link-budget dB
penalties*, applied at `ttc_basic.py:444-456`, exported as param `0x0538` (`:596-599`) and forwarded to the bridge
as `gs_penalty_db` (`engine.py:2348` → `bridge.py:524-527` → `radio.html:352-356`):

| Failure | Effect | Max (magnitude 1.0) |
|---|---|---|
| `high_ber` | `ber_inject_offset` — direct Eb/N0 reduction | 10 dB |
| `receiver_degrade` | `receiver_nf_degrade` | 5 dB |
| `pa_overheat` | `pa_heat_inject` W → thermal shutdown at 70 °C | 20 W |
| `uplink_loss` | holds carrier/bit/frame lock False | — |
| `primary_failure` / `redundant_failure` | transponder RX/TX dead | — |
| `antenna_deploy_failed` | stowed antenna, −6 dB margin (`:459-461`) | — |
| `gs_lna_degradation` | ground LNA NF | 6 dB |
| `gs_antenna_mispoint` | pointing loss | 10 dB |
| `gs_feed_loss` | feed/waveguide | 4 dB |
| `gs_rfi_interference` | noise rise | 8 dB |
| `gs_hpa_degradation` | uplink HPA | 5 dB |
| `gs_ref_osc_drift` | reference oscillator | 3 dB |
| `gs_tracking_loss` | tracking failure | 15 dB |

All seven `gs_*` are tested (`tests/test_rfsim/test_gs_failures.py:100-118`, 7/7 injectable + clearable, penalty
stacking verified at `:68-76`, Eb/N0 effect at `:36-66`). **Importantly, these penalties do reach the DSP**: they
reduce `s.eb_n0`, which the bridge picks up over WS and applies to the live channel — so injecting
`gs_antenna_mispoint` genuinely widens the constellation and raises the frame error rate. That is the one part of
the impairment story that works end to end.

**Delivery mechanisms:** `FailureManager` (`packages/smo-simulator/src/smo_simulator/failure_manager.py`) supports
`step` / `gradual` (ramped over `onset_duration_s`, `:143-151`) / `intermittent` (random flicker, `:153-161`) onsets
and auto-expiry — a good instructor mechanism. Reachable from scenario YAML
(`configs/eosat1/scenarios/gs_rf_degradation.yaml:22`, `gs_antenna_failure.yaml:19`, `ttc_pa_overheat.yaml:14`,
`gs_tracking_loss.yaml:30`), from `tools/scenario_generator.py:81-87`, and from the instructor console — **but the
instructor UI failure dropdown lists only 7 of the 13 TT&C failures** (`packages/smo-simulator/src/smo_simulator/instructor/static/index.html:1375-1378`:
`primary_failure, redundant_failure, high_ber, pa_overheat, uplink_loss, receiver_degrade, antenna_deploy_failed`) —
**all seven `gs_*` ground-segment failures are missing from the UI** and can only be injected via scenario file or
raw API.

### What is missing for realistic RF-impairment training

1. **Phase noise** — implemented twice (`noise.py:52-85`, `iq_recorder.py:288-310`), reachable zero times. This is
   the impairment that visibly rotates/smears the constellation and is the classic "why won't the PLL lock" lesson.
2. **Fading / multipath** — `RicianFading`, `RayleighFading`, `MultipathChannel` all written, none reachable. The
   textbook's UHF example names Faraday fade 10–20 dB as the design driver (`textbook_inventory.md:43`).
3. **Interference / jamming** — `CWInterferer`, `WidebandInterferer` written, unreachable. `gs_rfi_interference`
   exists only as a scalar dB noise-rise, so RFI cannot be *seen* in a spectrum (which, per Q1, does not display
   anyway). A CW interferer at a settable offset is the single most valuable addition for a spectrum-analyser
   lesson.
4. **Receiver front-end artefacts** — I/Q imbalance, DC offset, AGC saturation, quantisation, band-edge roll-off:
   all in `ImpairmentConfig` (`iq_recorder.py:100-103,115`), none live. AGC saturation *is* an event
   (`ttc_basic.py:759-767`) with no signal-level cause.
5. **Doppler rate / dynamic stress** — Doppler is applied as a static offset per chunk (`dsp/channel.py:48-53`) and
   is pre-compensated at the RX with the *known* value (`correlator_rx.py:150-158`), which removes exactly the
   challenge Doppler is supposed to create. `iq_recorder.py:84-88` has a proper LEO S-curve model; the live path
   does not.
6. **Runtime control** — everything above is currently config-time or scenario-time. There is no "turn the noise up
   and watch the constellation open" affordance (see Q4).
7. **Frequency-selective / adjacent-channel effects, group delay, non-linear PA (AM/AM, AM/PM)** — absent entirely;
   the PA is modelled thermally but not as an amplifier.

### Fix proposal

| # | Change | Effort |
|---|---|---|
| N1 | Add the seven `gs_*` failures to the instructor UI dropdown (`instructor/static/index.html:1375-1378`) — they are fully implemented, tested, and currently invisible to the instructor. One-line change. | **S** |
| N2 | **Swap `BasebandChannel` for `SpaceLinkChannel` in `ChannelStage`** (`channel_stage.py:53-56`). The interfaces already match (`set_eb_n0`, `set_doppler`/`set_freq_offset`, `set_link_active`, `set_bits_per_symbol`, `process`) — this is close to a drop-in and immediately unlocks phase noise, Rician/Rayleigh fading, multipath and CW/wideband interferers, plus `get_status()` for the dead "Channel Model" panel. Verify per-chunk continuity (`RicianFading`/`MultipathChannel` state across calls) and CPU cost at 256 kHz. | **M** |
| N3 | Wire `SpaceLinkConfig` into `rfsim.yaml` (see Q4) and `LinkBudget.compute()` → `RadioFrontend.update_link_budget()`, killing the permanently-zero Link Budget panel. | **S–M** |
| N4 | Expose impairments on the control API + UI (Q4): Eb/N0 slider, phase-noise linewidth, fading K + rate, "add CW interferer at ±X kHz, Y dB" — the last one is what makes the fixed spectrum analyser worth looking at. | **M** |
| N5 | Add a `doppler_rate_hz_s` / free-running mode so the RX has to *track* rather than being handed the answer (`correlator_rx.py:150-158`); optionally add an instructor "Doppler compensation OFF" toggle. | **M** |
| N6 | Lift the receiver-artefact block from `iq_recorder.py` (I/Q imbalance `:100-102`, DC offset `:103`, quantisation `:115`, band-edge filter `:117`) into a reusable `dsp/frontend_artefacts.py` shared by the recorder and the live channel — the code exists, it just lives in the wrong module. | **M** |
| N7 | Non-linear PA model (Saleh AM/AM + AM/PM) driven by `tx_fwd_power` so backing off TX power has a spectral consequence (spectral regrowth visible on the fixed spectrum analyser). | **L** |

---

## Consolidated fix backlog, ranked by effort

**Small (each ≲ 1 hour, high visible payoff)**

1. Q1-S1 — wire `update_spectrum()` from a rolling buffer in `ChannelStage`. *Fixes the spectrum analyser.*
2. Q2-S1/S2 — retain post-matched-filter samples in `CorrelatorRX`, wire `update_eye_diagram()`. *Fixes the eye diagram.*
3. Q3-H1 — accept uint8 `0/1` on func 65 and route it to `data_rate_mode`. *Makes high/low rate commandable.*
4. Q3-H3 / Q4 — add the `pipeline:` config section (`coordinator.py` already reads it) and add `frequency_hz` to `ChannelConfig`.
5. Q6-N1 — add the seven `gs_*` failures to the instructor dropdown.
6. Q5-M1/M2/M3 — real OQPSK Q-rail offset; Gray coding; stop advertising the four unreceivable schemes.
7. Q1-S2 — call `update_channel_status()` / `update_link_budget()` so two dead panels come alive.

**Medium**

8. Q3-H2 — rate-pace real-time TM using the existing `_tick_dump_emission` byte-budget pattern. *This is what makes high rate mean something.*
9. Q3-H4 — reconfigure pipeline symbol rate from the commanded TM rate (TX **and** RX).
10. Q6-N2 — replace `BasebandChannel` with `SpaceLinkChannel` in `ChannelStage`. *Unlocks phase noise, fading, interferers in one change.*
11. Q4 — `--radio-control` API + config panel in `radio.html`; surface `get_diagnostics()`.
12. Q5-M4/M5 — GMSK receiver; NRZ-M differential coding.
13. Q6-N5/N6 — Doppler tracking stress; shared receiver-artefact module.
14. Reconcile the frequency plan (DI-05) across `ttc.yaml`, `tc_catalog.yaml` (UL 2000–2100 / DL 8400–8500!), `ttc_basic.py` validators (440–460 / 400–410) and `rfsim.yaml` (2200) — currently four different answers.

**Large**

15. Q5-M6 — LDPC (CCSDS 131.0-B), the textbook's recommended scheme for EOSAT-1.
16. Q5-M7 — PCM/PSK/PM residual carrier + ranging tones.
17. Q6-N7 — non-linear PA with spectral regrowth.

## Test-coverage note

`tests/test_rfsim/` (178 tests, all passing) covers ASM, BCH, CLTU, channel model, convolutional/Viterbi, RS,
frame sync, TM frame, VC, DSP, modulation, constellation and GS failures. **There is no test anywhere that
exercises `update_spectrum`, `update_eye_diagram`, `update_channel_status`, `update_link_budget`, `radio/web_ui.py`
or `radio/terminal_ui.py`** — which is precisely why four display features can be dead in production while the
suite is green (consistent with `docs/review_2026-07/test_strategy.md:143`). Any fix above should land with a test
that asserts the field is non-empty after a `_radio_updater` cycle.
