## Summary

`channel/space_link.py` implements a full space-link channel model —
Doppler → multipath → Rician/Rayleigh fading → Wiener phase noise → AWGN →
CW/wideband interferers — with a config dataclass covering every effect and
a `get_status()` whose keys exactly match what
`RadioFrontend.update_channel_status()` expects. Its supporting classes
(`AWGNSource`, `PhaseNoiseSource`, `CWInterferer`, `WidebandInterferer`,
`RicianFading`, `RayleighFading`, `MultipathChannel`, `LinkBudget`) are all
implemented too.

**`SpaceLinkChannel` was instantiated nowhere.** `grep -rn
"SpaceLinkChannel"` returned only its own definition and a docstring
reference. The live RF pipeline used the much simpler `BasebandChannel`
(`pipeline/channel_stage.py:53-56`), whose entire impairment repertoire is
a frequency offset and AWGN.

The consequence: in a running simulation there was **no phase noise, no
fading, no multipath, no interference and no jamming** on the sample
stream. The radio UI's "Channel Model" panel was hard-wired to show
`OFF / OFF / -- dB / 0` forever, and `gs_rfi_interference` existed only as
a scalar dB noise-rise with nothing visible in a spectrum. Phase noise —
the classic "why won't the PLL lock" lesson — was implemented twice in the
repository and reachable zero times.

Additionally, all seven ground-segment `gs_*` RF failure modes were fully
implemented and tested in `ttc_basic.py` but missing from the instructor
console's failure dropdown, so they could only be injected from a scenario
file or the raw API.

## Severity

**Major** — the impairment library that makes RF training realistic was
entirely unreachable from a running simulation, and a whole UI panel was
permanently dead as a direct result.

## Status

**Fixed**:

1. **`ChannelStage` now builds a `SpaceLinkChannel`**
   (`pipeline/channel_stage.py`), configured from the new
   `channel:` impairment keys via `PipelineCoordinator._build_link_config()`.
   The interfaces already matched (`set_eb_n0`, `set_freq_offset`,
   `set_link_active`, `set_bits_per_symbol`, `process`); the stage keeps its
   own link-inactive handling and gains `set_sample_rate()`,
   `set_impairments()` and `get_channel_status()`.
2. **Runtime control.** `SpaceLinkChannel.set_impairments(**kwargs)`
   updates any `SpaceLinkConfig` field and rebuilds only the affected
   stages; an unknown parameter name raises `ValueError` rather than being
   silently ignored. Exposed through
   `PipelineCoordinator.set_impairments()`, `RFSimBridge.apply_channel()`,
   `POST /api/channel` and the new radio UI config panel (defect #55).
3. **Impairment plug-in registry** — `register_impairment(name, factory)`
   / `available_impairments()` / `build_impairment(spec, cfg)` in
   `channel/space_link.py`. An impairment is any object with
   `apply(samples) -> samples`. `cw` and `wideband` are registered as
   built-ins; registering a new type makes it immediately usable from
   `rfsim.yaml`, from `POST /api/channel` and from the UI **without
   touching `SpaceLinkChannel`**. This is the intended landing zone for the
   SatHack impairment set and for the offline chain already sitting in
   `recording/iq_recorder.py` (I/Q imbalance, DC offset, quantisation,
   power-law phase noise, band-edge filtering, impulsive RFI). Unknown
   types raise.
4. **Determinism.** `AWGNSource`, `PhaseNoiseSource` and
   `WidebandInterferer` accept an injected RNG; `SpaceLinkConfig.seed`
   (default 42 via `channel.seed`) makes impairments reproducible for
   lessons and tests.
5. **Dead panels alive.** `get_status()` now also reports the interferer
   list, the fading Doppler and the registry contents;
   `bridge._update_displays()` feeds it to `update_channel_status()`, and
   `LinkBudget.compute()` to `update_link_budget()` (see defect #53).
6. **Instructor console.** The seven `gs_*` ground-segment failures
   (`gs_lna_degradation`, `gs_antenna_mispoint`, `gs_feed_loss`,
   `gs_rfi_interference`, `gs_hpa_degradation`, `gs_ref_osc_drift`,
   `gs_tracking_loss`) were added to the TT&C failure dropdown in
   `packages/smo-simulator/src/smo_simulator/instructor/static/index.html`.

Measured end to end on the live pipeline: enabling phase noise
(3 kHz linewidth) + Rayleigh-like fading (K = 0 dB, 200 Hz) + a −32 dBm CW
interferer at +20 kHz drops the receiver's decision-directed SNR estimate
from ~17.8 dB to ~1.0 dB and starts producing RS decode failures; clearing
them restores ~17.7 dB.

## Acceptance criteria

- [x] `ChannelStage.channel` and `PipelineCoordinator._channel.channel` are
      `SpaceLinkChannel` instances.
- [x] `rfsim.yaml` impairment keys reach the running channel.
- [x] Phase noise rotates the carrier without changing the envelope.
- [x] Fading modulates the envelope.
- [x] A CW interferer appears at the commanded frequency offset (verified
      by FFT peak location).
- [x] Impairments measurably degrade the live receiver (EVM SNR drops by
      >3 dB) and can be cleared, with the receiver recovering.
- [x] Impairments are independently switchable; an unknown parameter or
      impairment type raises.
- [x] A third-party impairment can be registered and takes effect without
      modifying `SpaceLinkChannel`.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/channel/space_link.py`
- `packages/smo-rfsim/src/smo_rfsim/channel/noise.py` (seedable RNGs)
- `packages/smo-rfsim/src/smo_rfsim/pipeline/channel_stage.py`,
  `pipeline/coordinator.py`
- `packages/smo-rfsim/src/smo_rfsim/bridge.py`,
  `radio/web_ui.py`, `static/radio.html`
- `configs/eosat1/rfsim.yaml`
- `packages/smo-simulator/src/smo_simulator/instructor/static/index.html`
  (the seven `gs_*` entries only)
- `tests/test_rfsim/test_rf_impairments.py` (12 tests)

## Related

- Defect #53 — a CW interferer is only worth injecting once the spectrum
  analyser works.
- Defect #55 — the config sections and the API surface that drive these.
- Still open (out of scope): Doppler *rate* / free-running Doppler so the
  RX has to track rather than being handed the answer; lifting the
  receiver-artefact block (I/Q imbalance, DC offset, quantisation,
  band-edge filter) out of `recording/iq_recorder.py` into a shared
  module; non-linear PA with spectral regrowth. The registry above is the
  extension point for all three.
- RF audit 2026-07, Q6.
