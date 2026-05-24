## Summary

A consolidated register of parameters that subsystem models compute (and in some
cases monitor/limit-check) but that are in **no periodic HK SID**, so they are
invisible in routine telemetry. Some are readable on-demand via S20.3, but that
path is itself gated in one case (see TTC). All confirmed by grepping
`configs/eosat1/telemetry/hk_structures.yaml` for the param ID.

**AOCS:**

1. Raw body-frame sun vector `0x0220`–`0x0222` and raw dual-magnetometer A/B
   channels + select `0x0223`–`0x0229` are written each tick
   (`models/aocs_basic.py:1029-1040`) but not in SID 2. The composite CSS sun
   vector and composite mag ARE in SID 2, so diagnostics are largely covered —
   but the absent raw A/B mag channels weaken Mag-A-vs-B fault isolation in the
   sensor-cascade scenario (see defect #26).

**EPS:**

2. `sa_a_degradation` (0x0124), `sa_b_degradation` (0x0125),
   `sa_lifetime_hours` (0x0126), `wing_status` (0x0144), `wing_deploy_timer`
   (0x0145) are catalogued in `parameters.yaml` and written in tick
   (`eps_basic.py:613-614` for the wing pair) but in no SID — so during/after the
   func-81 wing deployment the operator gets no periodic wing telemetry.
3. Param-ID collision: `0x013A` is written as PDM-unswitched status
   (`eps_basic.py:586`) but `parameters.yaml:330` defines `0x013A =
   eps.eclipse_active`. An S20.3 read returns the PDM bitmask under the label
   "eclipse_active" (eclipse is actually 0x0108). Neither interpretation is in
   any SID.

**TTC:**

4. `antenna_deployment_ready` (0x0535), `antenna_deployment_sensor` (0x0536),
   `modulation_mode` (0x0537) are written (`ttc_basic.py:494-502`) but not in SID
   6. The `antenna_deploy_failed` failure (`:848-852`, sets sensor=3 "jammed")
   surfaces only as a one-shot S5 event; there's no SID parameter to poll. Worse,
   S20.3 reads of any 0x05xx param are power-gated on `ttc_tx` being ON
   (`service_dispatch.py:945-950`), so the antenna sensor can't even be read
   on-demand in the cold state where an operator most needs it.

**TCS:**

5. Heater setpoint readback `0x0330` (battery) / `0x0331` (OBC) added by a prior
   fix (`tcs_basic.py:477-478`) are not in SID 3 — readable only via S20, never in
   periodic TCS HK, partly re-opening the gap that fix claimed to close.

**OBDH:**

6. OBC heat-dissipation `0x031F`, added by a prior fix
   (`obdh_basic.py:397`, per `defects/reviews/obdh_fixed.md`), is not in SID 4
   (SID 4 carries 0x0319–0x031E but not 0x031F).

(The already-filed defect #5 — `sw_image` 0x0311 / `phase` 0x0129 not in any HK
SID — is the canonical example of this class and is not repeated here.)

## Severity

**Minor**, with one Major edge: the TTC antenna-deployment sensor (item 4) is the
only evidence of a stuck-antenna failure and is both absent from periodic HK and
blocked from on-demand read while TX is off — making that failure genuinely hard
to diagnose. The rest are diagnostic/observability gaps with partial S20
workarounds.

## Requirements for the fix

1. Parameters that scenarios/FDIR/limits expect operators to watch must be
   present in a periodic HK SID (or deliberately marked on-demand with the S20
   read path actually reachable).
2. Resolve the 0x013A definition/implementation collision.

## Suggested implementation

- Add to the relevant SIDs in `hk_structures.yaml`: TTC 0x0535–0x0537 → SID 6;
  EPS 0x0124–0x0126, 0x0144–0x0145 → SID 1; TCS 0x0330/0x0331 → SID 3; OBDH
  0x031F → SID 4; AOCS raw dual-mag A/B → SID 2 (at least the A/B channels needed
  for fault isolation).
- Reconcile `parameters.yaml` 0x013A: give PDM status its own catalogued ID
  matching the code and fix/remove the `eclipse_active` alias.
- Reconsider gating RX/mechanism params (e.g. antenna sensor) on `ttc_tx` in the
  S20 power-gate (`service_dispatch.py:945-950`).

## Acceptance criteria

- Each listed parameter appears in periodic HK (or has a working, reachable S20
  on-demand path), verifiable from the MCS.
- The 0x013A read returns the correct, correctly-labelled value.
- A test asserts the antenna-deployment sensor is observable while TX is off.

## Affected areas

- `configs/eosat1/telemetry/hk_structures.yaml` (multiple SIDs)
- `configs/eosat1/.../parameters.yaml` (0x013A reconciliation)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py` (S20 power-gate, item 4)

## Related

- Defect #5 (sw_image/phase not in HK) — same class, already filed.
- Defect #6 (no MCS parameter-watch widget) — even on-demand-readable params are
  hard to see without it.
- Defect #32 (dead/inert commands & state) — the command-side twin of this register.
