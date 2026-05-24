## Summary

Four panels of the RF bridge's Radio status dashboard (`static/radio.html`,
served on port ~8094 in `--rf` mode) render permanent placeholder data in every
mode: **Link Budget**, **Channel Model**, **Spectrum**, and **Eye diagram**.
The methods that would populate them exist but are never called, and the
channel/link-budget models they were designed to read from are themselves dead
code.

Evidence: `radio.html` draws these panels from `RadioStatus` fields —
`eirp_dbw`/`fspl_db`/`cn0_dbhz`/`coding_gain_db`/`frequency_hz`/`bandwidth_hz`
(link budget), `phase_noise_enabled`/`fading_enabled`/`fading_k_db`/
`n_interferers` (channel), `spectrum_db` (spectrum), `eye_i`/`eye_q` (eye). Those
fields are populated only by `frontend.update_link_budget`,
`update_channel_status`, `update_spectrum`, and `update_eye_diagram`
(`radio/frontend.py:180-225`), and grep shows those four methods have **zero
callers**. The WS feed (`radio/web_ui.py:40-42`) serialises the whole dataclass,
so it always sends defaults: EIRP/FSPL/C-N0 = 0, coding_gain = 6, frequency =
437 MHz (the dataclass default — note `rfsim.yaml` says 2200 MHz, never
plumbed), fading/phase-noise = OFF, and empty spectrum/eye arrays (so those
canvases never draw).

The producers were meant to read from `channel/space_link.py`
(`SpaceLinkChannel`) and `channel/link_budget.py` (`LinkBudget`) — see the
docstrings at `frontend.py:181,190` ("from SpaceLinkChannel.get_status()" /
"from LinkBudget.compute()"). But that whole cluster (`space_link.py` +
`fading.py` + `noise.py` + `link_budget.py`, ~530 LOC of Rician/Rayleigh fading,
phase noise, interferers, and link budget) is instantiated by nothing: the live
channels are `dsp/channel.BasebandChannel` (RF) and `channel/model.ChannelModel`
(FRAME). The intended wiring was never made.

## Severity

**Major** — four dashboard panels look live but never change, which is
misleading for anyone using the radio view to reason about link quality. The
underlying advanced channel-impairment modelling is fully implemented yet
unreachable. (Major rather than Critical because the RF bridge is optional.)

## Requirements for the fix

1. The four panels must show real, changing data, or be removed.
2. The channel/link-budget models intended to feed them must be wired into the
   live pipeline, or removed.

## Suggested implementation

- Wire `SpaceLinkChannel` (with `fading`/`noise`/`link_budget`) into the RF
  `ChannelStage`, and call `frontend.update_channel_status`/`update_link_budget`
  from the bridge with that channel's status; call `update_spectrum` /
  `update_eye_diagram` from the RX chain with real samples.
- Plumb `frequency_hz`/`bandwidth_hz` from `rfsim.yaml` instead of the 437 MHz
  default.
- If this depth of modelling is not wanted, delete the four panels from
  `radio.html` and the `space_link`/`fading`/`noise`/`link_budget` cluster.

## Acceptance criteria

- In `--rf` mode the Link Budget, Channel, Spectrum, and Eye panels show values
  that respond to the configured link and channel settings, or those panels are
  removed.
- The displayed RF frequency matches `rfsim.yaml`.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/static/radio.html`
- `packages/smo-rfsim/src/smo_rfsim/radio/frontend.py`, `radio/web_ui.py`
- `packages/smo-rfsim/src/smo_rfsim/channel/{space_link,fading,noise,link_budget}.py`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/{channel_stage,rx_chain}.py`

## Related

- Defect #18 (GNU Radio package dead) and #22 (RF/gateway/tools dead code).
