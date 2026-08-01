## Summary

The S19 event-action engine itself worked — a rule installed from the ground
fired on a genuine engine-emitted event and changed spacecraft state, and
enable / disable / delete all took effect. Three faults sat around it.

1. **TC(19,8) "report all definitions" returned *nothing* with the shipped
   rules.** The record was packed `struct.pack('>HBBB', ea_id, event_type, func,
   enabled)` — a **1-byte** `event_type`. Every shipped rule has a 2-byte
   trigger (0x0102, 0x025B, ...), so `struct.error` was raised, swallowed by the
   blanket `except Exception` in `dispatch()` (`service_dispatch.py:111-113`),
   and the handler returned `[]`.

   ```
   TC(19,8) with shipped rules                 -> responses: 0   <-- report unusable
   TC(19,8) with only 1-byte event_type rules  -> responses: 1  subtype 128
   ```

   Rule status was therefore not reportable at all in the as-shipped
   configuration.

2. **Ground-added rules could not reference a real event.** TC(19,1) read
   `event_type = data[2]`, one byte. Adding a rule for event 0x0102 stored
   `event_type = 0x02`. Every event id and every monitored parameter id in this
   mission is >= 0x0100, so **no ground-added rule could ever match anything.**
   The MCS UI faithfully sent a 1-byte field, so the UI was "correct" for a
   broken wire format.

3. **17 of the 26 shipped rules were dual-triggered.** Event ids and monitored
   parameter ids both live in 0x0000-0x0FFF, and `trigger_event_action()`
   matched *both* namespaces: an exact event-id match, **and** a param-violation
   match on the low 12 bits. A cross-check of all 26 rules against
   `event_catalog.yaml` and `s12_definitions.yaml` found 17 trigger values that
   resolve to an event-catalog id *and* an S12-monitored parameter. Proved live:

   ```
   S19 dispatch after event 0x9102 (S12 violation of bat_temp):
        S19 triggered: rule 1001 executing S8 func 10 for param 0x0102 violation
   S19 dispatch after event 0x0102 (catalog BATTERY_SOC_CRITICAL):
        S19 triggered: rule 1001 executing S8 func 10 for event 0x0102
   ```

   Rule 1001 fired on a **battery-temperature** limit violation *and* on the
   SoC-critical event, while its own description claims bus undervoltage. The
   existing content test only asserted "resolves to a catalog id **or** an S12
   param", so it could not see this.

## Severity

**Major** — an autonomous FDIR response firing on a condition nobody intended is
the worst class of fault in this service, and the operator had no way to read
back what the spacecraft would do, because the report was empty.

## Status

**Fixed.**

1. **Report record widened, TM(19,128) retained.** The subtype is unchanged
   deliberately — it is what the (forthcoming) ground parser will be written
   against, and moving it would have been churn on top of a format change.
   TC(19,8) now returns a report for the shipped configuration:

   ```
   TM(19,128)   big-endian
     header  count            uint16
     record  ea_id            uint16   (7 bytes, repeated `count` times)
             trigger_kind     uint8    0 = S5 event id, 1 = S12 parameter id
             trigger_id       uint16
             action_func_id   uint8
             enabled          uint8
   ```

2. **TC(19,1) widened and given a namespace discriminator:**

   ```
   TC(19,1)  ea_id(u16) + trigger_kind(u8) + trigger_id(u16) + action_func_id(u8)
   ```

   `trigger_kind` outside {0,1} is rejected with S1.8 code 0x0003. The legacy
   4-byte form is still accepted (interpreted in the *event* namespace) so old
   clients still install a definition, but it can no longer double-fire.

3. **Namespaces separated end to end.**
   - Rule schema: `trigger: {kind: event|param, id: 0x0102}`. The bare
     `event_type:` field is still loaded and always means kind `event` — never
     `param` — so even an un-migrated rule cannot dual-fire.
   - `trigger_event_action(trigger_id, kind=...)` requires **both** the id and
     the namespace to match; the old "also match the low 12 bits of a 0x9xxx
     event" branch is gone.
   - `engine._emit_event` routes `0x9000|param` S12 violations to the *param*
     namespace and everything else to the *event* namespace, so the 0x9xxx
     encoding stays an implementation detail of the S12 -> S5 bridge.
   - `configs/eosat1/monitoring/s19_rules.yaml` migrated: all 26 rules now
     declare `trigger.kind`. **No trigger id was changed.** The kind for each
     rule was read off the rule's own comment (`(from S12)` -> `param`,
     an uppercase catalogue event name -> `event`), and every rule was verified
     to resolve in the namespace it now declares.

4. **Content tests extended** (`tests/test_content/test_s19_rules_content.py`):
   every rule must declare a namespace; its trigger must resolve *in that
   namespace* (stricter than the old "event OR param"); no rule may fire in the
   namespace it did not declare; and the exact defect reproduction — an S12
   battery-temperature violation must not fire event-keyed rule 1001 — is
   pinned.

## Deliberately NOT changed — two Flight Ops review items

Both are FDIR-domain content decisions, so they are **pinned by strict-xfail
gates** rather than edited:

- **`test_no_rule_is_misrouted`** (pre-existing, unchanged): 13 rules dispatch
  an `action_func_id` that contradicts their description — ea 1001 "payload off"
  -> func 10 AOCS_SLEW_TO; ea 4002 "PA off" -> func 54 OBDH select-bus; all four
  OBDH rules (5001-5004) land on TCS heater circuits. The namespace fix does not
  touch any `action_func_id`, so this gate is unaffected and still meaningful.
- **`test_no_rule_trigger_contradicts_its_description`** (new): four rules point
  at a trigger id whose catalogued meaning contradicts the rule name —
  1001 (0x0102 is BATTERY_SOC_CRITICAL, description says bus undervoltage, which
  would be 0x0103/0x0104), 1002 and 1003 (0x0100 is EPS_MODE_CHANGE, description
  says SoC critical), 2004 (0x0202 is RW_BEARING_DEGRADED, description says
  attitude error high). Disambiguating the namespaces made this drift visible;
  re-pointing a trigger changes *when the spacecraft acts autonomously*, which
  is not a change to make silently. Recorded in `KNOWN_TRIGGER_DRIFT`.

Also unchanged, and still open: ea 2005-2008 ("disable wheel 1/2/3/4") all send
bare func 2 with no index byte, so all four disable wheel 0 — a consequence of
the parameter-less action model, not of the namespace split.

## Files

- `packages/smo-simulator/src/smo_simulator/monitoring_loader.py`
  (`TRIGGER_KIND_EVENT/PARAM`, `parse_trigger_kind`, `load_s19_rules`,
  `register_s19_rules`)
- `packages/smo-simulator/src/smo_simulator/service_dispatch.py`
  (`_handle_s19`, `trigger_event_action`)
- `packages/smo-simulator/src/smo_simulator/engine.py` (`_emit_event`)
- `configs/eosat1/monitoring/s19_rules.yaml` (all 26 rules migrated)
- `configs/eosat1/commands/tc_catalog.yaml` (`EA_ADD`, new `EA_REPORT_DEFS`)
- Tests: `tests/test_content/test_s19_rules_content.py`;
  `tests/test_simulator/test_pus_service_fixes.py`
  (`TestS19Report`, `TestS19GroundAddedRules`, `TestS19NoDualTrigger`)

## Related

- Defect #69 — S12 now delivers violations to S19 in the parameter namespace.
- Defect #37 — the earlier mis-mapped-func incident that `KNOWN_MISROUTED`
  exists to prevent recurring.
- Hand-off to the MCS: `TMProcessor` has no S19 branch and `#s19-tbody` is never
  populated; TM(19,128) is now parseable and carries everything that table needs.
