## Summary

The RF chain advertised eight modulation schemes and could correctly
transmit *and* receive three and a half.

**OQPSK was a mislabelled QPSK.** `dsp/modulator.py:24` defined
`OQPSK_MAP = QPSK_MAP.copy()` and `Modulator.modulate()` applied **no
half-symbol Q delay** — the single feature that defines offset QPSK.
The receiver treated it identically (`correlator_rx.py:54` `3: 2`, `:199`
`in (1, 3)`, `:322` `in (1, 3)`). Selecting OQPSK changed nothing about
the emitted waveform, the constellation or the eye. This is precisely the
scheme a student would be asked to identify from a constellation/eye
display.

**Four schemes were transmit-only.** `CorrelatorRX._BITS_PER_SYM`
(`:54`) had no keys 4–7, so 16-APSK, π/4-DQPSK, GMSK and GFSK silently
fell back to `bps = 1` and the BPSK slicer at `:348-349` — plausible-looking
garbage rather than a failure. GFSK was additionally byte-identical to
GMSK because `_modulate_cpm` hard-coded `h = 0.5` despite a docstring
promising a configurable index.

**QPSK/8PSK used natural-binary symbol maps**, not Gray. The two sides
agreed with each other, so the link worked — but a symbol error at high
Eb/N0 costs 2 bits instead of 1, putting measured BER above the Gray-coded
theory curves the textbook quotes.

**The QPSK/OQPSK carrier loop settled on the decision boundary.** The
4th-power detector drove `phase_err → 0` at `φ = 45° + 90k` because the
QPSK constellation sits at 45°+90k and its own 4th power is `e^{jπ}`. The
ASM rotation search only tried 90° multiples, so it could not recover the
45° error. A direct QPSK modulate→demodulate round trip returned ~6 %
correct bytes even with no noise.

## Severity

**Major** — a mislabelled waveform in a teaching simulator actively
teaches the wrong thing, four advertised schemes produced garbage without
saying so, and the QPSK carrier-loop bias meant one of the three
"working" schemes did not actually close a link outside the pipeline's
auto-search machinery.

## Status

**Fixed** in `packages/smo-rfsim/src/smo_rfsim/dsp/`:

1. **Real OQPSK.** `Modulator._shape_psk()` (`modulator.py`) upsamples I
   and Q separately and delays the Q rail by `sps/2` samples *before* RRC
   shaping, carrying the half-symbol tail across `modulate()` calls in
   `_oqpsk_q_tail` so the offset is continuous at frame boundaries. The
   receiver samples the two rails at staggered instants (`correlator_rx.py`
   `_demodulate_psk`, `staggered`/`half`), de-rotating both taps by the
   same loop phase *before* separating them so a residual carrier offset
   cannot cross-couple the rails.
2. **Gray coding.** `_gray_psk_map()` builds `QPSK_GRAY_MAP` /
   `PSK8_GRAY_MAP` (`pts[p ^ (p >> 1)] = e^{jθ_p}`) and a Gray π/4-DQPSK
   phase-change table. Both sides resolve their labelling through the new
   `constellation_map(modulation, gray_coding)` / `pi4_phase_changes()`
   helpers, so TX and RX can never disagree. **Gray is the default**;
   natural binary stays selectable via `pipeline.gray_coding: false` and
   the legacy `QPSK_MAP` / `PSK8_MAP` / `CONSTELLATION_MAPS` names are
   unchanged.
3. **Carrier-loop bias.** The QPSK/OQPSK (and 16-APSK acquisition)
   detector now uses `sq = -(corrected ** 4)`, removing the
   constellation's own 4th-power phase so the loop's stable point is zero
   phase error instead of 45°.
4. **Receivers for the TX-only schemes.**
   - **16-APSK**: nearest-point slicing on the DVB-S2 4+12 map with an AGC
     (the two-ring constellation carries information in the radius) and a
     decision-directed carrier loop; 90° ambiguity resolved by the ASM
     search.
   - **π/4-DQPSK**: true differential detection from the phase change
     between successive symbols, with the reference symbol carried across
     chunks. Being differential it is immune to phase ambiguity, so the
     rotation search is skipped for it.
   - **GMSK / GFSK**: a separate CPM path (`_demodulate_cpm`) — a
     differential-phase discriminator followed by a symbol-period
     integrator. Non-coherent, phase-ambiguity free and modulation-index
     independent, so it serves GMSK (h = 0.5) and GFSK (configurable
     `pipeline.gfsk_mod_index`) alike.
5. **Loud failure for anything else.** `UnsupportedModulationError` is
   raised by `CorrelatorRX.__init__` and `set_modulation()` for a
   modulation with no receiver, instead of silently falling back to the
   BPSK slicer. `bridge._apply_sim_feedback` logs it as an error rather
   than swallowing it.

Measured noiseless round-trip after the fix: 8/8 schemes recover the
payload. With AWGN, the six linear/coherent schemes are error-free at
10 dB Eb/N0; the two CPM schemes need ~18 dB, the expected penalty for
non-coherent discriminator detection.

## Acceptance criteria

- [x] Every modulation in `MOD_NAMES` has a receiver in
      `CorrelatorRX.SUPPORTED_MODULATIONS`.
- [x] All eight round-trip modulate → demodulate, Gray *and* natural binary.
- [x] The OQPSK Q rail is provably delayed by exactly `sps/2` relative to
      QPSK; the I rail is unchanged; the envelope minimum is >10× QPSK's
      (no zero crossings); the offset is continuous across chunk boundaries.
- [x] The Gray maps cost exactly 1 bit per adjacent-symbol error; the
      natural-binary maps cost more.
- [x] An unknown modulation raises at selection time.
- [x] GFSK responds to its modulation index; GMSK stays at h = 0.5.

## Affected areas

- `packages/smo-rfsim/src/smo_rfsim/dsp/modulator.py`
- `packages/smo-rfsim/src/smo_rfsim/dsp/correlator_rx.py`
- `packages/smo-rfsim/src/smo_rfsim/pipeline/rx_chain.py`,
  `pipeline/coordinator.py` (gray/BT/loop-bandwidth plumbing)
- `packages/smo-rfsim/src/smo_rfsim/bridge.py` (loud rejection)
- `tests/test_rfsim/test_modulation_roundtrip.py` (42 tests)

## Related

- Defect #53 — the eye diagram's Q rail is where the OQPSK offset becomes
  visible.
- Defect #55 — `pipeline.modulation` / `pipeline.gray_coding` are the new
  config keys that select these.
- Not addressed here (out of scope, still open): NRZ-M differential
  encoding, PCM/PSK/PM residual carrier + ranging tones, LDPC/turbo coding.
- RF audit 2026-07, Q5.
