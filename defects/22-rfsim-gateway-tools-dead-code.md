## Summary

A consolidated register of smaller dead / unwired items in `smo-rfsim`,
`smo-gateway`, and `tools/`. Each was confirmed by grepping for callers and by
reading `start.sh` / `configs/eosat1/rfsim.yaml` to establish what the launcher
can actually reach. (The two headline RF gaps — the dead GNU Radio package and
the placeholder radio panels — are filed separately as defects #18 and #19.)

**smo-rfsim:**

1. **`ccsds/scrambler.py` and `ccsds/interleaver.py` are fully dead** — no
   callers anywhere, not even tests. The TX/RX chains never apply CCSDS
   pseudo-randomisation (scrambler) or block/convolutional interleaving, even
   though scrambling is a standard step of the CCSDS TM chain.
2. **`dsp/processor.DSPProcessor`** (`dsp/processor.py:19`) — a complete
   mod→channel→demod class — is imported only by tests; the live RF pipeline
   uses `tx_chain`/`channel_stage`/`rx_chain` with `CorrelatorRX`. Its
   `get_noise_only_constellation` (`:117`) is dead even within the class.
3. **`radio/terminal_ui.py`** (a full rich/plain-text dashboard) is reachable
   only via `--radio-ui` (`bridge.py:588-590`), but `start.sh:82,86` always
   passes `--radio-web` and never `--radio-ui`, so a user can't start it through
   the supported launcher.
4. **`PipelineCoordinator.get_recovered_packet()`** (`coordinator.py:149`) has no
   callers — the bridge uses `drain_recovered_packets` instead (and the code
   comments say the old `run_in_executor` method was deliberately abandoned for
   stalling the relay loop).
5. **`config.pipeline` is never present.** `coordinator.py:41` reads
   `getattr(config, 'pipeline', None)`, but `RFSimConfig` has no `pipeline` field
   and the YAML has no `pipeline:` section, so symbol_rate/sps/modulation/buffer
   depth are permanently hardcoded — the apparent configurability is illusory.
6. **`convolutional_enabled` default mismatch.** `CCSDSConfig.convolutional_enabled`
   defaults to `True` (`config.py:32`) while `rfsim.yaml` sets it `false` (the
   pure-Python Viterbi is ~5 s/frame). Running with a bare `RFSimConfig()` (no
   YAML override) silently engages the very slow Viterbi path.

**smo-gateway:**

7. **Orphaned from the main launcher.** Top-level `start.sh` never starts the
   gateway (only `deploy/*.sh` do). Within the package, `Gateway` (`gateway.py`)
   reimplements upstream connect and downstream fan-out inline, while
   `DownstreamManager` (`downstream.py:11`) and `UpstreamConnection`
   (`upstream.py:11`) — which look like the gateway's connection layer — are
   imported only by tests. A maintainer would reasonably assume `Gateway` is
   built on those classes; it isn't.

**tools/:**

8. **`delayed_tm_viewer.py` Plot buttons throw a ReferenceError.** Lines 604 and
   702 pass `plugins: [ChartDataLabels || {}]` to Chart.js, but the page loads
   only `chart.umd.min.js` and `chartjs-plugin-zoom` (`:255-256`) —
   `ChartDataLabels` is never defined, so the per-parameter "Plot" (`:461`) and
   "Plot All" (`:449`) buttons error out at chart-creation time.

(For reference, `tools/orbit_tools.py` and `tools/doc_viewer.py` are correctly
wired with real backends; `tm_report.py`, `procedure_audit.py`,
`generate_smdl.py`, `generate_xtce.py` are CLI utilities not launched by
start.sh and are out of the "UI with no backend" class.)

## Severity

**Minor** — the RF bridge and gateway are optional/advanced, and the live RF and
TM-viewer paths work. But item 8 is a user-visible broken control in a tool
start.sh does launch, and items 1, 5, 6, 7 are real correctness / configurability
/ maintainability issues.

## Requirements for the fix

Triage each: wire it up (1 scrambler is a standard CCSDS step worth adding; 3
terminal UI needs a launcher flag), fix the bug (8 ChartDataLabels; 6 default;
5 config plumbing), or delete the dead code (2 DSPProcessor, 4 get_recovered_packet,
and the unused gateway managers in 7 — or refactor `Gateway` onto them).

## Suggested implementation

- Add the CCSDS scrambler to `tx_chain._encode_frame` / `rx_chain._process_frame`
  (and interleaver if the link design calls for it), or remove both modules.
- Fix `delayed_tm_viewer.py` to guard `typeof ChartDataLabels !== 'undefined'` or
  load the datalabels plugin.
- Align `convolutional_enabled` default with the YAML; add a real `pipeline:`
  config section (or remove the `getattr` that implies one).
- Add a `start.sh` flag for the terminal radio UI, or document it as manual-only.
- Refactor `Gateway` to use `UpstreamConnection`/`DownstreamManager`, or delete
  them; document that the gateway is deploy-only.
- Delete `DSPProcessor` and `get_recovered_packet`.

## Acceptance criteria

- The delayed-TM-viewer Plot buttons render charts without console errors.
- No rfsim/gateway module is dead without an explicit decision recorded.
- `RFSimConfig()` defaults match the shipped YAML; pipeline parameters are either
  configurable or the dead config hook is removed.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/ccsds/{scrambler,interleaver}.py`, `dsp/processor.py`, `radio/terminal_ui.py`, `pipeline/coordinator.py`, `config.py`
- `packages/smo-gateway/src/smo_gateway/{gateway,downstream,upstream}.py`
- `tools/delayed_tm_viewer.py`
- `start.sh`, `configs/eosat1/rfsim.yaml`

## Related

- Defect #18 (GNU Radio package dead) and #19 (radio dashboard placeholder
  panels) — the two Major RF gaps.
