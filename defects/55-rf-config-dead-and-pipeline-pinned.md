## Summary

Most of the RF simulator's configuration was dead, and the RF pipeline
could not be reconfigured at all — at load time or at runtime.

**Dead config.** `RFSimConfig.from_yaml` (`config.py:82-93`) copied YAML
keys onto its dataclasses behind a `hasattr` filter, so any key the
dataclass did not declare was **silently discarded**.
`channel.frequency_hz: 2200.0e6` in `rfsim.yaml` was dropped on every
load — `ChannelConfig` had no such field — so the radio UI fell back to a
hard-coded 437 MHz. `channel.awgn_enabled` was parsed and never read
(`BasebandChannel.process` always added noise below 50 dB), and
`channel.path_loss_db` / `delay_ms` were never read in RF mode.

**Pipeline pinned.** `PipelineCoordinator` read `symbol_rate`, `sps`,
`rrc_rolloff`, `modulation`, `sample_buffer_depth`, `tc_symbol_rate` and
`tc_sps` from `config.pipeline` via `getattr` (`coordinator.py:41-48`) —
but **`RFSimConfig` had no `pipeline` field** and `rfsim.yaml` had no
`pipeline:` block. `pc` was therefore always `None` and the pipeline ran
permanently at 32 kBd / sps 8 / 256 kHz / BPSK: a rate that is neither the
mission's 1 kbps nor its 64 kbps.

**Rate changes were cosmetic.** The bridge received the spacecraft's
commanded TM rate over the sim WebSocket and did exactly one thing with it
(`bridge.py:536-538`): update a text label.
`PipelineCoordinator.reconfigure(symbol_rate=…)` existed and was never
called with a symbol rate, and the RX chain had no rate handling at all.
The modulated signal, the frame cadence and the occupied bandwidth were
identical at 1 kbps and at 64 kbps.

**No runtime control.** `radio/web_ui.py` registered three GET routes and
nothing else. Once the bridge was started, nothing was changeable —
`radio.html` was 544 lines of read-only widgets.

Also: `configs/eosat1/rfsim.yaml` writes floats as `2200.0e6`, which
PyYAML (YAML 1.1) parses as a **string**, not a number. Because the key
was being dropped anyway this had never surfaced.

## Severity

**Major** — a documented, shipped config file whose values had no effect;
a signal-processing pipeline that ignored the commanded data rate, making
the mission's high-rate/low-rate distinction unobservable; and no way for
an instructor to change anything at runtime.

## Status

**Fixed**:

1. **Strict, validated config** (`config.py`). New `ConfigError`; unknown
   top-level or section keys now **raise** instead of being dropped
   (`strict=True` by default, `strict=False` downgrades to a warning).
   Every section has an explicit `validate()` with range checks naming the
   offending key. `_coerce()` converts YAML scalars to each field's
   declared type, so `2200.0e6` becomes a float instead of a string.
2. **New `PipelineConfig` section** — `modulation`, `symbol_rate`, `sps`,
   `rrc_rolloff`, `gmsk_bt`, `gfsk_mod_index`, `gray_coding`,
   `sample_buffer_depth`, `tc_symbol_rate`, `tc_sps`, `rx_loop_bw`,
   `tc_uplink_eb_n0_db`, `track_data_rate`, `min/max_symbol_rate` — read
   directly (no `getattr` fallbacks) by `PipelineCoordinator` and
   plumbed through to TX, channel and RX.
3. **`ChannelConfig` extended** with `frequency_hz`, `uplink_frequency_hz`
   and the impairment fields (see defect #56); `awgn_enabled` now reaches
   the channel.
4. **New `LinkBudgetConfig` section** mirroring `LinkBudget.__init__`,
   plus `ground_stations_file` / `default_ground_station` so per-station
   G/T is read from `configs/eosat1/planning/ground_stations.yaml` and the
   station in contact selects the right value at runtime.
5. **Symbol rate follows the commanded bit rate.**
   `PipelineCoordinator.set_data_rate_bps()` converts bit rate to symbol
   rate via bits/symbol and `reconfigure(symbol_rate=…)` now propagates to
   **all three** stages: TX frame cadence, channel sample rate (so Eb/N0
   and Doppler stay correctly scaled) and RX Doppler pre-compensation
   (`GroundStationRX.reconfigure` gained a `symbol_rate` argument).
   `bridge._apply_sim_feedback` calls it. A rate change now visibly changes
   the occupied bandwidth on the spectrum analyser and the eye timing.
6. **Runtime control plane** (`radio/web_ui.py`, `bridge.py`):
   `GET /api/config`, `GET /api/diagnostics`, `POST /api/channel`,
   `POST /api/pipeline`, `POST /api/link`. Gated behind
   `network.radio_control_enabled` / the new `--radio-control` flag
   (default **off**, preserving the "purely observational" contract);
   disabled routes return 403. Values set through the API are recorded as
   manual overrides so the 1 Hz sim WebSocket feedback cannot stomp the
   operator a second later; `{"release": "all"}` hands control back.
7. **UI.** `radio.html` gained a "Radio Config" panel (Eb/N0 and Doppler
   sliders, modulation dropdown, symbol rate, AWGN / phase-noise / fading
   toggles with parameters, an interferer add/clear list, Apply and
   "Release to sim") shown only when the control plane is enabled, plus a
   Pipeline Diagnostics panel surfacing the 18 counters that were
   previously log-only.

## Acceptance criteria

- [x] `channel.frequency_hz` survives a YAML load as a float and reaches
      the channel model and the UI.
- [x] `channel.awgn_enabled: false` actually disables the noise source.
- [x] A `pipeline:` section exists, validates, and drives symbol rate,
      sps, modulation and rolloff.
- [x] An unknown or out-of-range key raises `ConfigError` naming the key.
- [x] `set_data_rate_bps(64000)` retunes TX, channel *and* RX; QPSK halves
      the symbol rate for the same bit rate; rates are clamped to the
      configured limits and tracking can be disabled.
- [x] Sim WS `data_rate_bps` feedback retunes the pipeline, not just a label.
- [x] Channel and pipeline settings round-trip through
      `POST /api/channel|pipeline` → `GET /api/config`.
- [x] Control routes 403 when the control plane is disabled; malformed
      bodies 400.
- [x] Manual overrides survive sim feedback until released.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/config.py`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/coordinator.py`,
  `pipeline/rx_chain.py`, `pipeline/channel_stage.py`
- `packages/smo-rfsim/src/smo_rfsim/bridge.py`
- `packages/smo-rfsim/src/smo_rfsim/radio/web_ui.py`, `radio/frontend.py`
- `packages/smo-rfsim/src/smo_rfsim/static/radio.html`
- `configs/eosat1/rfsim.yaml` (new `pipeline:` and `link_budget:` sections)
- `tests/test_rfsim/test_radio_control.py` (32 tests)

## Related

- Defect #56 — the impairment fields added to `ChannelConfig` are what the
  new API surface controls.
- RF audit 2026-07, Q4 and Q3 break (4). Breaks (1)–(3) of Q3 (the func-65
  uint8/uint32 decode mismatch, the `tick()` overwrite of
  `data_rate_mode`, and real-time TM not being rate-paced) are
  simulator-side and remain open.
