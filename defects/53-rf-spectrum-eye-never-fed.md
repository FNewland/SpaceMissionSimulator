## Summary

The radio front-end's **spectrum analyser** and **eye diagram** were dead
panels. Both had a complete UI renderer (`static/radio.html:421-457`
`drawSpectrum()`, `:459-495` `drawEyeDiagram()`) and a complete backend
producer (`radio/frontend.py:196-210` `update_spectrum()`, `:212-225`
`update_eye_diagram()`) — and **nothing in between**. Neither producer had
a single call site anywhere in the repository, so `spectrum_db` and
`eye_i` were always `[]`, the UI guards at `radio.html:373` and `:378`
never fired, and both canvases rendered as empty boxes with a heading. No
error, no console message, no "no data" placeholder.

The same was true of `update_channel_status()` and `update_link_budget()`,
so the "Link Budget" and "Channel Model" panels showed `0.0 dB` / `OFF`
permanently, and of `PipelineCoordinator.get_diagnostics()` — an 18-counter
dict that was only ever written to a log line (`bridge.py:305`).

A second, subtler break sat behind the first: the obvious thing to feed the
eye with — `CorrelatorRX.constellation_points` — is **symbol-rate
decimated** (one sample per symbol, `correlator_rx.py:176-178`). Folding
one-sample-per-symbol data at a 16-sample period, as both
`frontend.py:219` and `radio.html:476` assumed, produces noise, not an
eye. An eye diagram needs the oversampled waveform *between* decision
instants. `eye_q` was also transported but never drawn, so even a correct
feed would have given a single-rail eye — exactly the rail on which OQPSK's
half-symbol offset is visible.

## Severity

**Major** — two of the radio UI's headline instruments, plus the link
budget and channel model panels, were permanently blank in a training
simulator whose purpose is to show students what a ground station sees.
The behaviour was silent, and no test anywhere exercised the producers, so
the suite stayed green while four display features were dead in production.

## Status

**Fixed** in `packages/smo-rfsim`:

1. **Live sample taps.** `ChannelStage` now retains a rolling, lock-guarded
   window of the last 8192 post-impairment samples
   (`pipeline/channel_stage.py`, `SPECTRUM_BUFFER_SIZE`,
   `_record_spectrum()`, `get_recent_samples()`) — the true ground-station
   SDR view, taken at the same point already published over ZMQ.
   `CorrelatorRX` retains the last 1024 **post-matched-filter, oversampled**
   samples plus the symbol-timing phase (`dsp/correlator_rx.py`
   `_store_eye()`, `get_eye_samples()`, `eye_timing_offset`).
2. **Coordinator accessors.** `PipelineCoordinator.get_spectrum_samples()`,
   `get_eye_samples()`, `eye_timing_offset`, `sps`, `sample_rate`,
   `evm_db`, `get_channel_status()`, `get_link_budget()`
   (`pipeline/coordinator.py`).
3. **Call sites.** New `RFSimBridge._update_displays()` (`bridge.py`) is
   invoked every RF-mode `_radio_updater()` cycle and feeds
   `update_spectrum`, `update_eye_diagram`, `update_channel_status`,
   `update_link_budget`, `update_diagnostics` and `update_pipeline_status`.
   It is rate limited to `_display_min_period_s = 0.2 s` (≤ 5 Hz) so the
   FFT and eye folding cannot swamp the bridge loop.
4. **Better producers.** `update_spectrum()` is now Welch-averaged (8
   Hann-windowed segments, 50 % overlap) instead of one jumpy FFT, keeps
   the peak of each decimation group so a narrow CW interferer is never
   averaged away, and takes the sample rate so the frequency axis is real
   rather than a JS fallback. `update_eye_diagram()` now takes
   *oversampled* samples + `sps` + `timing_offset` and slices exactly
   `traces × 2 × sps` values aligned so the decision instant lands at trace
   centre. Both return a boolean instead of silently doing nothing.
5. **UI.** `static/radio.html` draws the Q rail in a second colour, marks
   the decision instant, folds at `2 × eye_sps` (not a hard-wired 16),
   renders "no data" placeholders instead of leaving a blank canvas, hides
   both panels outside RF mode with an explanatory note, and adds a
   Pipeline Diagnostics panel plus symbol rate / measured EVM SNR /
   achieved throughput rows.

## Acceptance criteria

- [x] `spectrum_db` is non-empty and passes the UI's `length > 4` guard
      after one `_update_displays()` cycle on a live pipeline.
- [x] The spectrum shows a genuine RRC main lobe (centre > edges + 5 dB).
- [x] Welch averaging is measurably smoother than a single FFT.
- [x] `eye_i` **and** `eye_q` are non-empty and equal length; the eye is fed
      from the oversampled tap, not the symbol-rate one.
- [x] The eye opening narrows when Eb/N0 is dropped (proving it is derived
      from the real signal).
- [x] Channel model, link budget and diagnostics panels are populated.
- [x] Display updates are rate limited to ≤ 5 Hz.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/pipeline/channel_stage.py`
- `packages/smo-rfsim/src/smo_rfsim/dsp/correlator_rx.py`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/coordinator.py`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/rx_chain.py`
- `packages/smo-rfsim/src/smo_rfsim/radio/frontend.py`
- `packages/smo-rfsim/src/smo_rfsim/bridge.py`
- `packages/smo-rfsim/src/smo_rfsim/static/radio.html`
- `tests/test_rfsim/test_display_producers.py` (12 tests)

## Related

- Defect #56 (`SpaceLinkChannel` never instantiated) — the spectrum
  analyser is what makes a CW interferer or a rising noise floor visible,
  so the two fixes are only fully useful together.
- Defect #54 (OQPSK) — the Q-rail eye is the only place a student can *see*
  the half-symbol offset.
- RF audit 2026-07, Q1 and Q2.
