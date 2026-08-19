# CONTRAST REWORK — 2026-08-19

Owner ruling: (a) the modern UI "does not give enough contrast and the colours
were good in classic"; (b) research the best UI for spacecraft operators;
(c) delete the training content — the interface must be intuitive for actual
operators without a training mode.

This document records the research, the resulting palette decision, and the
arithmetic validation. Companion files: `RESEARCH.md` (the 2026-08-12 survey,
still valid), `tools/check_contrast.py` (the validator that produced the
tables below), `screenshots/contrast_rework/` (before/after).

---

## 1. Research findings

### 1.1 ISA-101 survives — its over-greyed *execution* does not

The high-performance-HMI principles remain sound and are kept: colour is
reserved for state, never decoration; alarm colours are never diluted; stale
must never look like steady; no gradients/glow/blink outside the alarm
contract (RESEARCH.md §1.3). But the industry critique of over-greyed
implementations is real: practitioners warn that the "sea of gray" reading of
ISA-101 is a misreading — the standard asks for *judicious* colour against a
quiet ground, not for dim text. Control Engineering / Automation World
coverage of the "going gray" debate stresses judicious colour use and a
**3:1 minimum luminance contrast for graphics** — a floor, not a target — and
notes that overuse, not use, is what destroys colour's alerting value
([Automation World, "The Great Graphics Debate: Is Going Gray Good for
HMIs?"](https://www.automationworld.com/products/control/article/13316377/the-great-graphics-debate-is-going-gray-good-for-hmis);
[Control.com, "Going Gray"](https://control.com/technical-articles/going-gray/);
[RealPars, HMI colors](https://www.realpars.com/blog/hmi-colors)).
The 2026-08-17 token set fell into exactly this trap: `--tx3` at 6.1:1 and
`--ok` merged into tertiary-label grey made the resting console read as a
grey wasteland — labels, values and nominal states all converged on the same
dim band.

### 1.2 Real space-operations consoles are dark grounds with bright data

NASA MCC, JPL and ESA/ESOC consoles (SCOS-2000 alphanumeric displays,
described in RESEARCH.md §2.1) are dark surfaces carrying **near-white
telemetry values** with saturated per-state colours: green for
limit-OK/affirmative verdicts, yellow/amber for soft violations, red for hard
violations, blue/cyan for commanded state ([NASA display standards, Appendix
F](https://www.nasa.gov/reference/appendix-f-vol-2/)). The value/label
brightness split is the signature property: the *data* is the brightest thing
on the screen, chrome and labels one step down. Classic SMO had this
(`--text-primary #eaeaf2` values on `#10101e` panels ≈ 15:1) and it is what
the owner is asking for back.

### 1.3 ATC colour research: fixed palettes, hierarchy of salience

FAA/EUROCONTROL display research recommends **fixed, machine-validated
palettes** rather than operator brightness knobs ("large individual
differences" in preference create safety risk), a small colour count, and a
hierarchy of salience matched to urgency — de-saturate the unimportant so
alerts gain salience ([Ahlstrom & Arend, "Color Usability on Air Traffic
Control Displays," FAA
2005](https://hf.tc.faa.gov/publications/2005-color-usability-on-air-traffic-control-displays/full_text.pdf);
[FAA DOT/FAA/AR-99/52, Guidelines for the Use of Color in ATC
Displays](https://apps.dtic.mil/sti/tr/pdf/ADA367984.pdf)). This validates
the token discipline (one palette, no per-page colour authoring) and the
quiet-ground/bright-state split — it does *not* validate dim resting text.

### 1.4 Dark-console arithmetic (WCAG on dark grounds)

WCAG 2.2 AA (4.5:1) is a floor for office documents read at ~50 cm. A
console read at arm's-length-plus, often over someone's shoulder, should meet
**AAA (7:1) for all text**, and dark grounds make this cheap: on `#0B0D10`
even a mid-grey `#9BA7B4` clears 7.9:1. The dark-mode cautions from
RESEARCH.md §6 still bind: avoid pure white on pure black (halation), avoid
fully-saturated pure red/blue (chromatic aberration) — classic's slightly
softened saturations (`#FF6B6B`, not `#FF0000`) are precisely the
halation-safe choice, which is part of why classic read well.

### 1.5 Why classic's palette worked (extracted values)

`packages/smo-mcs/src/smo_mcs/static/index.html` (sha-gated, untouched):

| Classic token | Hex | On its panel (#10101e) |
|---|---|---|
| `--text-primary` | `#eaeaf2` | 14.9:1 — values near-white |
| `--status-nominal` | `#00C896` | 7.6:1 — affirmative green, visible |
| `--status-warning` | `#F0B429` | 8.8:1 |
| `--status-alarm` | `#FF6B6B` | 5.9:1 |
| `--status-commanding` | `#6BB3FF` | 7.5:1 |

Three properties: (1) value text near-white against dark panels; (2)
saturated-but-not-pure per-state colours, each in a distinct lightness band
(CVD-robust); (3) real surface separation between page and panels. Classic's
*faults* (decorative gradients, glow on nominal values, colour-as-identity,
9 px labels — AUDIT §2) are not reinstated.

### 1.6 Decision

Keep the ISA-101 skeleton, restore classic's blood:

- **Text raised to AAA.** All dark-theme text tokens ≥ 7:1 on `--bg`/`--s1`
  (their primary surfaces); a new `--val` token makes telemetry numerals
  visibly brighter than labels (`--val` > `--tx` > `--tx2` > `--tx3`).
- **Classic hues verbatim where they test ≥ 7:1** on the new surfaces:
  `--warn #F0B429`, `--info #6BB3FF`, `--ok #00C896`. `--alarm` is classic's
  `#FF6B6B` lightened one step to `#FF7B72` (6.47:1 → 7.12:1 on `--s1`) —
  brightened, never diluted.
- **Green returns, scoped.** `--ok` is green again but ONLY for explicit
  affirmative state verdicts (GO, PASS, ALL CLEAR, in-family, lock
  acquired). Resting telemetry stays neutral (`--val`); a nominal numeral is
  never coloured. This is the honest middle between HPHMI grey-purism and
  actual NASA/ESOC console practice.
- **More surface separation**: `--bg #0B0D10` deepened, `--s3 #262C36`
  lifted — panels visibly stand off the page without gradients or shadows.
- Stale/graphics tokens stay graphics-only (≥ 3:1); greyscale acceptance
  toggle stays (it is an accessibility check, not training content).

---

## 2. Palette: old → new, with contrast ratios

Ratios computed by `tools/check_contrast.py` (WCAG relative-luminance
arithmetic). "on bg / on s1" = the token's primary surfaces.

### Dark theme (text floor 7:1 AAA; graphics floor 3:1)

| Token | Old (2026-08-17) | on bg/s1 | New (2026-08-19) | on bg | on s1 | on s2 | on s3 |
|---|---|---|---|---|---|---|---|
| `--bg` | `#0E1013` | — | `#0B0D10` | — | — | — | — |
| `--s1` | `#14171C` | — | `#14171C` | — | — | — | — |
| `--s2` | `#1A1E24` | — | `#1C2129` | — | — | — | — |
| `--s3` | `#22272F` | — | `#262C36` | — | — | — | — |
| `--val` | *(did not exist)* | — | `#F5F7FA` | 18.13 | 16.74 | 15.06 | 13.08 |
| `--tx` | `#E6E9ED` | 15.6 / 14.8 | `#E9EDF2` | 16.55 | 15.28 | 13.75 | 11.94 |
| `--tx2` | `#A3ACB9` | 8.3 / 7.8 | `#C0C9D4` | 11.63 | 10.73 | 9.66 | 8.39 |
| `--tx3` | `#8A93A0` | 6.1 / 5.8 | `#9BA7B4` | 7.95 | 7.34 | 6.61 | 5.74 |
| `--ok` | `#8A93A0` (grey) | 6.1 / 5.8 | `#00C896` (classic) | 8.99 | 8.30 | 7.47 | 6.49 |
| `--warn` | `#E8A317` | 8.8 / 8.3 | `#F0B429` (classic) | 10.44 | 9.64 | 8.67 | 7.53 |
| `--alarm` | `#FF5C4D` | 6.3 / 5.9 | `#FF7B72` (classic+1) | 7.72 | 7.12 | 6.41 | 5.57 |
| `--info` | `#5AA9FF` | 7.8 / 7.3 | `#6BB3FF` (classic) | 8.82 | 8.14 | 7.33 | 6.36 |
| `--shelf` | `#8B7CF6` | 5.7 / 5.4 | `#A79BF8` | 8.05 | 7.43 | 6.69 | 5.81 |
| `--stale` (graphics) | `#6E6E80` | 3.8 / 3.6 | `#7E8697` | 5.32 | 4.91 | 4.42 | 3.84 |

All text tokens ≥ 7:1 on `--bg` and `--s1`. (`--tx3`/`--alarm` dip to
5.6–5.7 on `--s3`, the *hover/inset* surface — still comfortably above AA;
no primary reading surface is below 7:1.)

### Light theme (AA floor 4.5:1 — unchanged except the two new roles)

| Token | Value | on bg | on s1 | on s2 | on s3 |
|---|---|---|---|---|---|
| `--val` *(new)* | `#0B0D10` | 11.13 | 12.71 | 13.51 | 14.59 |
| `--ok` *(now green)* | `#0B5A42` | 4.70 | 5.37 | 5.71 | 6.16 |
| `--tx/--tx2/--tx3/--warn/--alarm/--info/--shelf/--stale` | unchanged | all ≥ 4.5 (stale ≥ 3:1 graphics) | | | |

Validator output is reproducible: `python tools/check_contrast.py` exits 0
only if every floor holds.

---

## 3. What changed beyond the tokens

Pure-CSS quick wins applied (research-backed, no page restructuring):

1. `.ds-param .vl` 18 → 20 px, colour `--val` — primary telemetry readouts
   larger and brighter than labels (ISA-101 14 px is a floor; console
   viewing distance argues for more).
2. `.ds-and td.eng` 14 → 15 px, colour `--val`; `.ds-node .vl` 15 → 16 px,
   fill `--val` — the AND table and schematic values join the value band.
3. Alarm visual weight: P1 rows get the alarm wash background + heavier
   message text; `.ds-banner.alarm` gets weight 700 + a 4 px left key-line.
   Status washes raised from 7–8 % to 9–10 % alpha (more separation, still
   quiet).
4. `:focus-visible` gains a soft halo ring on top of the 2 px outline.
5. Guided-mode CSS hooks (`data-guided` devbar rules, `.guided-note`)
   removed with the training layer.

Deliberately left for a future pass: embedded sparklines/deviation bars on
more surfaces, alarm-rate meter (EEMUA), Level-1 exception page density work,
any layout/typography restructuring, classic console (sha-gated).

## 4. Training-layer deletion (owner ruling c)

Deleted from the operator UI: the GUIDED toggle, all annotation callouts
(`.guided-note` / `data-ann` / `annotationText` / `applyAnnotations`),
`why-this-design.html` and its `?`-dialog link, and
`configs/eosat1/mcs/annotations.yaml`. `/api/annotations` still routes
(additive-only API rule) but always serves `{}`.

The two OPERATIONAL tables that lived in annotations.yaml moved to
`configs/eosat1/mcs/display_config.yaml`, served by the new
`/api/display-config`: `capture_gates` (NOM-002 capture-readiness gate
table, G4) and `health_baselines` (commissioning baseline snapshot, G15).

Kept, per ruling scope: the causal-chain screen (operational diagnostic),
the TRAINING time-source chip (provenance honesty), the `?` keyboard-help
dialog, the greyscale toggle (accessibility check), and every instructor-
console teaching affordance (sim team's tool).
