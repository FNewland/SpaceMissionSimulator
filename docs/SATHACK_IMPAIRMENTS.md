# SatHack-derived RF channel impairments (roadmap X-15)

Additional, more physically realistic channel impairments for the EO-Sat-1 RF
chain, mined from Franz's **SatHack** SDR / weak-signal toolkit and ported into
the `SpaceLinkChannel` impairment registry. They exist to be *taught against*:
each one reproduces a real effect a ground-station operator or DSP student meets,
and each is deterministic and switchable so a lesson is reproducible.

## Where they came from — and an honest note

SatHack is an **acquisition and analysis** toolkit (blind CCSDS decode, burst
detection, Doppler prediction, weak-signal integration). It is not a channel
simulator: it spends most of its effort *undoing* channel effects, not modelling
them. The genuinely portable channel physics therefore lives in two places:

* `rfchain/rfchain/sim.py` and `rfchain/rfchain/doppler.py` — the only forward
  (signal-generation / channel) code in the repo: CFO with linear+quadratic
  Doppler, and a TLE/SGP4 range-rate Doppler predictor.
* `sathack/cf32_source.py` and `sathack/config.yaml` — front-end *conditioning*
  (DC-birdie removal, off-centre tuning to dodge the IQ image). We ported the
  **forward models** of the effects this code corrects.

What SatHack does **not** contain, and we did not invent: measured-statistics
fading tables, an ionospheric scintillation model, a polarisation/Faraday model,
or a burst/packet-loss pattern (its burst *detector* is not a loss *model*). Its
real strength is the opposite direction — recovering signals from impaired
captures — which is why only four channel effects were worth porting. See
"Recommendation" at the end for what the rest of SatHack is good for.

## What was ported

| `type` | Class | Models | SatHack source |
|---|---|---|---|
| `doppler_ramp` | `DopplerRampImpairment` | Realistic LEO Doppler S-curve, TLE-free | `rfchain/doppler.py` (`predicted_doppler_hz`), `rfchain/sim.py` (`add_cfo`) |
| `oscillator_drift` | `OscillatorDriftImpairment` | Slow carrier offset: initial offset + linear drift + quadratic aging + seeded random walk | `rfchain/sim.py` (`add_cfo` drift term), `config.yaml` (`sdr.ppm`, `freq_offset_hz`) |
| `iq_imbalance` | `IQImbalanceImpairment` | Quadrature gain/phase imbalance → conjugate image tone | `config.yaml` (`sdr.freq_offset_hz` "IQ-imbalance tuning"); named in the `space_link` registry comment |
| `dc_offset` | `DCOffsetImpairment` | Constant I/Q bias — the RTL "centre birdie" | `sathack/cf32_source.py` (`remove_dc`) |

All live in
`packages/smo-rfsim/src/smo_rfsim/channel/sathack_impairments.py`, registered
from `channel/space_link.py`, and carry attribution comments pointing back at the
SatHack file each came from.

### `doppler_ramp` — realistic LEO Doppler

SatHack computes Doppler from the satellite→station **range-rate** of an
SGP4-propagated TLE. We reproduce the characteristic curve *without* a TLE from a
circular-overpass geometry: for a pass at closest-approach range `r0` and
along-track relative speed `v`,

```
R(t)  = sqrt(r0² + (v·(t − t_ca))²)          # slant range
R'(t) = v²·(t − t_ca) / R(t)                 # range-rate
f_d(t) = − f_c · R'(t) / c                    # Doppler
```

This is the familiar S-curve: **positive** (approaching) before closest
approach, ramping steeply but smoothly through **0 Hz at `t_ca`**, going
**negative** (receding) after. Note the curve is *gentle near t_ca and spans the
whole pass* — the rate at closest approach is `f_peak · v / r0`, only ~tens of
Hz/s, but it saturates to ±`f_peak` (~±48 kHz at S-band) toward the horizon. This
is exactly the "drift-induced coherence loss" SatHack's own analysis
(`Domain_Transform_Signal_Extraction.md`) flags as the station's true enemy.

Config keys: `carrier_hz` (default = `channel.frequency_hz`), `altitude_km`
(550), `elevation_max_deg` (90), `pass_duration_s` (600), `t_ca_s` (default =
half the pass), `max_doppler_hz` (optional direct override of the peak; the
geometry is scaled to hit it).

### `oscillator_drift` — slow CFO with drift, aging, walk

The forward model of `add_cfo`'s `2π(f0·t + ½·drift·t²)` phase, extended with a
quadratic aging term and an optional **seeded** frequency random-walk
(temperature retrace / TCXO wander). This is a slowly-varying *frequency* error,
distinct from the existing `PhaseNoiseSource` (fast Wiener *phase* jitter).

Config keys: `f0_hz` (100), `drift_hz_s` (5), `aging_hz_s2` (0), `walk_hz` (0 =
deterministic drift only). Seeded from `channel.seed`.

### `iq_imbalance` — quadrature imbalance image

For gain imbalance `g` (from `gain_db`) and phase error `φ` (from `phase_deg`):

```
y = μ·x + ν·conj(x),   μ = ½(1 + g·e^{−jφ}),   ν = ½(1 − g·e^{jφ})
```

A tone at +f gains a mirror image at −f at level `|ν/μ|` (the image-rejection
ratio, `image_rejection_db()`). `gain_db = 0, phase_deg = 0` ⇒ exact no-op. This
is the self-interference SatHack's recorder deliberately tunes *around* by
offset-tuning off DC.

Config keys: `gain_db` (0.5), `phase_deg` (2.0).

### `dc_offset` — RTL centre birdie

Adds a constant complex bias `offset_i + j·offset_q`, i.e. a spectral spike at
0 Hz — precisely what `cf32_source.remove_dc` subtracts before demodulation.

Config keys: `offset_i` (0.05), `offset_q` (0.0).

## Config reference

Impairments are injected as entries in `channel.interferers` in
`configs/eosat1/rfsim.yaml` — the existing convention for any registered
`apply(samples)` object. **Disabled by default** (the list is empty); worked
examples are in the file's comments. Example:

```yaml
channel:
  seed: 42                 # makes every impairment below reproducible
  interferers:
    - {type: doppler_ramp, altitude_km: 550, elevation_max_deg: 90,
       pass_duration_s: 600, t_ca_s: 300, max_doppler_hz: 48000}
    - {type: oscillator_drift, f0_hz: 100, drift_hz_s: 5, walk_hz: 0}
    - {type: iq_imbalance, gain_db: 0.5, phase_deg: 2.0}
    - {type: dc_offset, offset_i: 0.05, offset_q: 0.0}
```

They are equally reachable live at runtime via `POST /api/channel`
(`{"interferers": [...]}`) and from the radio UI, like `cw`/`wideband`.

## Classroom suggestions — which impairment demonstrates what

* **`doppler_ramp` — carrier tracking & why a static offset is the easy case.**
  Enable with a large `max_doppler_hz` and watch the constellation spin and the
  spectral peak slide across a pass, crossing 0 Hz at `t_ca`. Lesson: open-loop
  vs closed-loop Doppler correction, and why coherent integration time is capped
  by the drift rate (`f_peak·v/r0`), not the noise floor.

* **`oscillator_drift` — TCXO/ppm error and receiver AFC.** Set a `f0_hz` offset
  and small `drift_hz_s`; show the residual carrier the AFC/Costas loop must
  chase and how `ppm` on a cheap dongle maps to kHz at S-band. Contrast with
  `phase_noise_enabled` (fast phase jitter) to teach the difference between
  frequency drift and phase noise.

* **`iq_imbalance` — image-reject receivers and why you tune off-DC.** Enable it
  and point out the mirror-image tone; compute `image_rejection_db()` and relate
  `gain_db`/`phase_deg` to the image level. Lesson: quadrature demod
  imperfections and the reason SatHack's recorder offsets its tuning.

* **`dc_offset` — the LO leakage / centre birdie.** Enable it and show the 0-Hz
  spike; then explain why blind receivers guard the DC bins and mean-subtract.
  Pairs naturally with `iq_imbalance` as "front-end artefacts, not the channel".

A good end-to-end demo: enable a **mild** mix of all four at ~15 dB Eb/N0 — the
link still decodes (verified in the tests), so students see realistic artefacts
without a broken link, then push each one until the receiver fails.

## Tests

`tests/test_rfsim/test_sathack_impairments.py` — 21 tests: each impairment
changes the signal in the physically expected way (Doppler peak follows the
commanded profile and ramps through zero; the IQ image tone appears at the
expected level; the DC spike sits at 0 Hz at amplitude `|offset|·N`; oscillator
drift moves the carrier), is deterministic under a fixed seed, is a no-op when
neutralised, and the end-to-end BPSK pipeline still decodes (>90 %) with a mild
mix enabled.

## Recommendation — what the rest of SatHack is good for

The bulk of SatHack is **receiver / analysis** material, not channel models, and
that is where its teaching value lies:

* **`rfchain/doppler.py` (`residual_cfo_track`), `estimate.py`, `integrate.py`** —
  blind Doppler/chirp-rate tracking and coherent beacon integration: ideal for a
  *receiver-side* lab (estimate and remove the very Doppler `doppler_ramp`
  injects).
* **`rfchain/fec.py`, `rs_ccsds.py`, `demod.py`, `scripts/ber_demo.py`** —
  soft-decision Viterbi/RS coding-gain curves: a strong FEC lesson, complementary
  to the sim's own CCSDS chain.
* **`Domain_Transform_Signal_Extraction.md`** — an excellent reading on FrFT /
  synchrosqueezing / subspace weak-signal methods for a graduate DSP module.

In short: SatHack is best used as the **ground-segment / receiver** counterpart
to this simulator's **space-segment / channel** side — inject impairments here,
recover from them there.
