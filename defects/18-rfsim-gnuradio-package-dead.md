## Summary

The RF bridge advertises "RF mode = full GNU Radio BPSK modulation/demodulation"
(`start.sh:7-8` and docs), but the entire `gnuradio/` package is unreachable —
even with `./start.sh --rf=RF` and GNU Radio installed, no GNU Radio code path
is ever selected.

Evidence: `gnuradio/{gr_bridge,downlink_mod,downlink_demod,uplink_mod,
uplink_demod,channel_sim}.py` provide the factory `create_rf_processor`
(`gr_bridge.py:109`) and the `GnuRadioRFProcessor` / `PurePythonRFProcessor`
classes. Grep for `create_rf_processor` finds callers only in
`tests/test_rfsim/test_gr_bridge.py` — nothing in `packages/` or `tools/`. The
live RF path runs through `bridge._init_rf_mode → PipelineCoordinator`
(`bridge.py:95-98`), which imports only the numpy `dsp/*` modules and never
touches `gnuradio/*`. `pipeline/channel_stage.py:28` does
`from gnuradio import channels as gr_channels`, but `gr_channels` is never used
in the file (it always uses the numpy `BasebandChannel`).

So ~470 lines of signal-processing machinery — the headline differentiator of
RF mode — cannot be exercised through any supported launch path.

## Severity

**Major** — the product claims (in `start.sh` and docs) a GNU Radio RF
capability that no code path can reach. It is dead code plus an inaccurate
capability claim. Severity is Major rather than Critical because the RF bridge
is an optional, advanced feature (off unless `--rf` is passed) and the numpy
pipeline does provide a working RF mode.

## Requirements for the fix

1. Either make the GNU Radio implementation selectable, or remove it and
   correct the `start.sh`/documentation claims so they describe the numpy
   pipeline that actually runs.

## Suggested implementation

- Plumb a `use_gnuradio` flag from config/CLI into `PipelineCoordinator` /
  `ChannelStage` so `create_rf_processor(..., use_gnuradio=True)` is used when
  GNU Radio is present; add a test that runs a frame end-to-end through the GR
  path when `HAS_GNURADIO`.
- Or delete the `gnuradio/` package and amend `start.sh:7-8` and the RF docs to
  say "BPSK via numpy DSP pipeline".

## Acceptance criteria

- Either `--rf=RF` with GNU Radio installed demonstrably runs frames through
  the GR blocks (verified by a test or log), or the package is removed and no
  doc/launcher text claims GNU Radio is used.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/gnuradio/*`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/coordinator.py`, `pipeline/channel_stage.py`
- `packages/smo-rfsim/src/smo_rfsim/bridge.py`
- `start.sh` and RF documentation

## Related

- Defect #19 (radio dashboard placeholder panels) — another case where RF
  capability exists in code but is not connected to what the user sees.
- Defect #22 groups the remaining dead RF/gateway/tools modules.
