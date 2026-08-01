## Summary

Four metadata defects in `configs/eosat1/commands/tc_catalog.yaml` — the file
that the MCS command builder, the byte-count derivation in
`smo_mcs/tc_lengths.py`, the procedure content tests and every operator-facing
command dialog all read as the description of the command set.

1. **`RW_SET_SPEED_BIAS` declared a field width the handler cannot accept.**
   The catalogue said:

   ```yaml
   - { name: wheel_idx, type: uint8 }
   - { name: bias_rpm, type: int16, description: "Speed bias in RPM" }
   ```

   giving a 3-byte S8.1 data field (1 func_id + 1 + 2). The dispatcher
   (`service_dispatch.py:597-603`) unpacks a **float32**:

   ```python
   elif func_id == 8:  # RW set speed bias
       if len(data) >= 5:
           wheel = data[0]
           bias = struct.unpack('>f', data[1:5])[0]
   ```

   and requires 5 argument bytes. A command built to the catalogue's declared
   layout is 2 bytes short, `len(data) >= 5` is false, and the whole command is
   **silently discarded** — no handler call, no error, and (because the engine
   emits S1 verification centrally) a perfectly normal-looking S1.1/S1.7 pair.
   The operator sees the command succeed and the wheel does not move.

   The handler is right and the catalogue is wrong: the AOCS model takes
   `bias = float(cmd.get("bias", 0.0))` and adds it to `rw_speed[wheel]`
   (`aocs_basic.py:1645-1651`), so a float is the correct type.

2. **S8 func 83 (`EPS_SET_MODE`) had no catalogue entry at all.** It is
   implemented (`service_dispatch.py:527-528` routes it, `:729-733` handles it,
   `eps_basic.py:867-872` sets `eps_mode`) and it is load-bearing — mode 1
   (safe) sheds the two least-essential loads and mode 2 (emergency) sheds all
   of them (`eps_basic.py:689-693`). With no catalogue entry it did not appear
   in the MCS command builder, in `/api/tc-catalog`, or in any content test, so
   the only way to send it was a hand-assembled raw packet.

3. **`TTC_SWITCH_PRIMARY` and `TTC_SWITCH_REDUNDANT` had no `fields` key.**
   `tc_lengths.expected_data_bytes()` deliberately distinguishes *"declares no
   arguments"* (`fields: []` → derivable, 1 byte for the func_id) from
   *"the catalogue does not say"* (key absent → `expected_data_bytes: None` plus
   a "length unknown" note). Both transponder-switch commands fell in the second
   bucket, so the command builder could not tell an operator how many bytes to
   send for two commands that in fact take none. They were the last two
   undeclared entries in the file.

4. **`EPS_SOLAR_ARRAY_DRIVE` documented the wrong unit.** The field was
   described as "Drive angle in degrees (-90 to +90)" while the handler divides
   by 100 (`service_dispatch.py:708-713`, `angle_deg / 100.0`), i.e. the wire
   value is **centi-degrees**. An operator sending `45` commands 0.45°.

## Severity

**Major** for item 1 (a command that reports success and does nothing is the
worst failure mode in a training console) and item 2 (an implemented,
safety-relevant EPS command that is unreachable from the UI). **Minor** for
items 3 and 4 (an unknown byte count and a wrong unit — both mislead, neither
silently discards).

## Status

**Fixed.**

1. `bias_rpm` is now `float32` with an explicit "IEEE-754 big-endian" note, and
   `wheel_idx` gained a "Wheel index 0-3" description. Derived length is now
   6 bytes (func_id + uint8 + float32), which is exactly what the handler
   requires.
2. Added `EPS_SET_MODE`, service 8 subtype 1 func_id 83, one `uint8 mode` field
   documenting `0=nominal, 1=safe, 2=emergency`, with the load-shed consequence
   in the description. The catalogue now carries 193 commands, all with unique
   names.
3. `TTC_SWITCH_PRIMARY` and `TTC_SWITCH_REDUNDANT` gained an explicit
   `fields: []`. Every entry in the catalogue is now length-derivable except
   the three genuinely variable-length `bytes`-typed commands.
4. `EPS_SOLAR_ARRAY_DRIVE.angle_deg` now says "Drive angle in CENTI-degrees;
   the handler divides by 100 (-9000..+9000 = -90..+90 deg)".

`tests/test_mcs/test_tc_lengths.py::test_commands_without_a_fields_key_are_unknown_not_zero`
asserted that the shipped catalogue contains at least one undeclared command,
which fix 3 makes false. It now skips when the catalogue is fully declared; the
unknown-vs-zero behaviour of `expected_data_bytes()` itself is still asserted by
`TestRobustness::test_missing_fields_key_on_a_non_s8_command_is_unknown`, which
passes a synthetic command rather than depending on catalogue content.

## Files

- `configs/eosat1/commands/tc_catalog.yaml` — `RW_SET_SPEED_BIAS`,
  `EPS_SOLAR_ARRAY_DRIVE`, `TTC_SWITCH_PRIMARY`, `TTC_SWITCH_REDUNDANT`, new
  `EPS_SET_MODE`
- `tests/test_mcs/test_tc_lengths.py` — one test made content-independent

## Related

- Hand-offs from the MCS-display audit (defects #63–#68), which found items 1–3
  while auditing the command builder's byte-count derivation (defect #67).
- `EA_ADD` (S19.1) had the same class of defect — a declared `action_tc bytes`
  field the dispatcher never parses — and was already corrected under defect
  #70.
- **Still open, outside this lane:** the module docstring of
  `packages/smo-mcs/src/smo_mcs/tc_lengths.py` (lines 19-27) still says four
  commands carry a `bytes` field (there are three) and that two commands have no
  `fields` key (there are now none). Cosmetic, but it is the file's own
  specification.
- **Still open, cosmetic:** `packages/smo-simulator/src/smo_simulator/tm_storage.py`
  lines 18-25 label SID 4 as "TTC" and SID 6 as "OBDH" in the store-sizing
  comment; `hk_structures.yaml` has SID 4 = Platform and SID 6 = TTC. The
  arithmetic is unaffected (both are 8 s).
- The wider fix is roadmap **X-2** (one machine-readable PUS routing table) and
  the round-trip test proposed in `pus_services_audit.md` §cross-cutting 3:
  "for every catalogue entry, build the command from its declared fields,
  dispatch it, assert the handler saw the declared values" — which would have
  caught all four of these mechanically.
