## Summary

The S19 event-action rules mis-mapped S8 `func_id`s, causing the OBC to boot
into its application autonomously at startup instead of staying in the
bootloader.

`configs/eosat1/monitoring/s19_rules.yaml` was authored against a **stale** S8
`func_id` map. Rules `ea_id 4001` and `4003` used `action_func_id: 55`
believing it meant "set TX power", but func 55 = OBDH `obc_boot_app`
(`service_dispatch.py:851-852`, `_route_obdh_cmd`). The correct "set TX power"
is TTC func 68.

At cold start the TTC BER edge emits event `0x050C` → rule `4003` →
`obc_boot_app` → ~10 s later `OBDHBasicModel.tick` flips `sw_image` to
`SW_APPLICATION` and the phase machine advances 3 → 4. (Note:
`engine._enter_bootloader_mode` correctly starts in bootloader at construction —
`engine.py:265`, `:301` set `sw_image = 0`; persisted breakpoints are **not** the
cause.) An audit found ~17 of 26 rules referenced stale func_ids.

## Severity

**Critical** — the spacecraft autonomously left the bootloader at startup
without a ground command, violating the bootloader → application checkout
sequence and defeating the bootloader command gate.

## Status

**Fixed** in `configs/eosat1/monitoring/s19_rules.yaml` and
`packages/smo-simulator/src/smo_simulator/service_dispatch.py`:

1. Corrected `action_func_id` for every mis-mapped rule to match the
   authoritative routing. The primary fix is `ea_id 4001`
   (`s19_rules.yaml:89-91`) and `4003` (`:104-106`): `55 → 68`
   (TTC `set_tx_power`). The audit also corrected EPS/payload/OBDH/TTC rules
   (each annotated with a `# FIX: was N = ...` comment in the YAML), choosing
   no-harm targets where the original intent was ambiguous (e.g. avoiding a
   reboot-on-reboot-event).
2. **Defense-in-depth:** the S19 autonomous dispatch path now refuses
   OBC-critical funcs (`_OBC_CRITICAL_FUNCS = {52, 53, 55, 56}`,
   `service_dispatch.py:1360`) while the OBC `sw_image == BOOTLOADER`, logging a
   warning (`:1363-1369`). This mirrors the OBDH bootloader command gate so a
   future mis-mapped rule cannot boot the OBC autonomously.

## Acceptance criteria

- [x] After ~15 sim-seconds the OBC stays in bootloader: `sw_image == 0`,
      params[0x0311] == 0, phase == 3.
- [x] The S19 dispatch path blocks OBC-critical funcs (52/53/55/56) while the
      OBC is in bootloader.
- [x] `ea_id 4001`/`4003` route to TTC `set_tx_power` (func 68), not
      `obc_boot_app`.

## Affected areas

- `configs/eosat1/monitoring/s19_rules.yaml` (mis-mapped `action_func_id`s)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_OBC_CRITICAL_FUNCS` bootloader gate on the S19 dispatch path)
- `packages/smo-simulator/src/smo_simulator/engine.py` (a behaviour-preserving
  `_tick_once()` helper extracted from `_run_loop` at `:807` for fast
  testability)
- `tests/test_obc_startup_bootloader.py` — engine ticked ~15 sim-s stays in
  bootloader, plus a test that S19 dispatch blocks OBC-critical funcs in
  bootloader.

## Related

- Defect #5 (`sw_image`/`phase` not in HK) — this bug was hard to spot because
  `sw_image` is not observable in HK; #29 (OBDH bus-failure isolation) — same
  family of routing/gate correctness issues.
